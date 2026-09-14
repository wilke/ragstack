# Quick start — `ragstack-ctl` on coconut (PR-B state)

The short version of [`ctl-deploy.md`](ctl-deploy.md). Eight steps, in order,
each with the one line that proves it worked and the one line that undoes it.
When something does not match the **Expect** line, stop and read the matching
section of the full runbook — it explains the failure modes.

**What this does:** deploys the proxy change, installs the control-plane
binary, adopts the four live tenants into the registry, publishes the first
gateway generation, starts the daemon.
**What it never does:** start, stop or touch a tenant. The only live change
is one symlink switch plus one `SIGHUP` to nginx (step 7).

**Accounts.** Everything as `wilke` on coconut in a real terminal (sudo has
`requiretty`). Anything that writes control-plane state runs as `svcbvbrc`
through `ops/coconut/ctl-as-svc.sh`; that wrapper is the only sudo path.

Both repos are on `main` (ragstack 919df6b, coconut-proxy 14baa1b).

---

## 0. Baseline and sanity

```bash
cd ~/Development/ragstack && git checkout main && git pull --ff-only
cd ~/Development/coconut-proxy && git checkout main && git pull --ff-only
cd ~/Development/ragstack

S=$HOME/snapshots/pre-ctl-$(date +%F); ops/coconut/snapshot.sh "$S"   # read-only baseline
ops/coconut/ctl-as-svc.sh version        # proves sudo -> svcbvbrc works (fails until step 2 installs the binary; that is fine)
```

**Expect:** `snapshot.json` + `INVENTORY.md` under `$S`. Keep `$S` — step 7 diffs against it.

## 1. Deploy the proxy change

```bash
cd ~/Development/coconut-proxy
./deploy.sh --dry-run            # what would be copied; any hand-edit drift in /rag/config/proxy
./deploy.sh                      # first run ever needs --force to record the drift manifest
./proxy.sh status
curl -s localhost:9000/ragstack/tenants
```

**Expect:** the same four tenants as before; same nginx master pid;
`ls -l /rag/config/proxy/conf.d/05-tenants.generated.conf` is a **regular file**
(the bootstrap copy — the ctl replaces it with a symlink in step 7).
**Undo:** `git checkout <previous main> && ./deploy.sh && ./proxy.sh reload`.

## 2. Build, test, install the binary

```bash
cd ~/Development/ragstack
make golang-sif                        # once: golang:1.23.12 → /rag/apptainer/images/golang.sif
make go-mode                           # -> GO_MODE=container
make build-ctl test-ctl install-ctl    # built and tested inside the image; caches under /rag/cache/go
/rag/bin/ragstack-ctl version
ops/coconut/ctl-as-svc.sh version
```

**Expect:** `go-mode` says `container`; both `version` calls print the same version and
40-hex commit; `test-ctl` green. (`GO=~/sdk/go1.23.12/bin/go` still selects a host toolchain.)
**Undo:** `ln -sfn ragstack-ctl-<previous> /rag/bin/ragstack-ctl` (old binaries are kept).

## 3. State directories and golden bodies (as svcbvbrc)

```bash
CTL_BIN=/bin/bash ops/coconut/ctl-as-svc.sh -c '
    mkdir -p /rag/data/ctl/{home,locks,gateway,goldens,tmp,artifacts,ui/dist,apptainer/{cache,config}} \
             /rag/config/ctl/{units,templates}
    chmod 2770 /rag/data/ctl /rag/config/ctl
    chmod 0700 /rag/data/ctl/tmp
'
G=~/Development/ragstack/go/internal/ctl/testdata/live-2026-09-10/gateway
CTL_BIN=/bin/bash ops/coconut/ctl-as-svc.sh -c "
    install -m 0644 -D -t /rag/data/ctl/goldens $G/root.json $G/tenants.json $G/api-unknown-404.json $G/catchall-404.json
    ls -l /rag/data/ctl/goldens
"
```

**Expect:** four `*.json` files listed. If `install` cannot read `$G`
(`~/Development` not readable by svcbvbrc), copy the four files to `/tmp` first.
**Undo:** `rmdir` the empty directories.

## 4. `ctl.env` and `ctl-secrets.env`

Files are created **by svcbvbrc**, content carried as base64 over the
wrapper's stdin. Never `install`/`cp` a wilke-owned temp file (svcbvbrc cannot
read it) and never pass secrets as arguments.

```bash
D=$(mktemp -d /tmp/ctl-secrets.XXXXXX); chmod 0700 "$D"; umask 077
openssl rand -hex 32     # operator key  — paste below, then clear scrollback
openssl rand -hex 32     # viewer key

cat > "$D/ctl.env" <<'EOF'
CTL_LISTEN=127.0.0.1:23990
CTL_RAG_ROOT=/rag
CTL_STATE_DIR=/rag/data/ctl
CTL_CONFIG_DIR=/rag/config/ctl
CTL_REGISTRY=/rag/data/tenants/registry.json
CTL_ADMIN_SUBJECTS='["bvbrc:<you>@patricbrc.org"]'
CTL_LOG_LEVEL=info
CTL_LOG_FORMAT=json
CTL_EXTERNAL_STORE_PORTS=6333,6343,9200
APPTAINER_CACHEDIR=/rag/data/ctl/apptainer/cache
APPTAINER_CONFIGDIR=/rag/data/ctl/apptainer/config
EOF
cat > "$D/ctl-secrets.env" <<'EOF'
CTL_API_KEYS='["<operator-key>","<viewer-key>"]'
CTL_API_KEY_ROLES='{"<operator-key>":"operator","<viewer-key>":"viewer"}'
EOF

for f in ctl.env:0640 ctl-secrets.env:0600; do n=${f%%:*}; m=${f##*:}
  base64 -w76 <"$D/$n" | CTL_BIN=/bin/bash ops/coconut/ctl-as-svc.sh -c "
    umask 077; base64 -d > /rag/config/ctl/$n.tmp && mv /rag/config/ctl/$n.tmp /rag/config/ctl/$n && chmod $m /rag/config/ctl/$n" >/dev/null
done   # >/dev/null is load-bearing: the pty echoes stdin back out, i.e. your secrets, base64-encoded
       # (it also swallows every base64 -d/mv/chmod error, so a failure here is a bare
       # non-zero exit with no reason; to diagnose, re-run without >/dev/null using a
       # dummy payload, never the real secrets)

CTL_BIN=/bin/bash ops/coconut/ctl-as-svc.sh -c '
  set -e; stat -c "%U:%G %a %n" /rag/config/ctl/ctl.env /rag/config/ctl/ctl-secrets.env
  [ "$(stat -c %a /rag/config/ctl/ctl.env)" = 640 ] && [ "$(stat -c %a /rag/config/ctl/ctl-secrets.env)" = 600 ]
  [ "$(grep -c "^CTL_" /rag/config/ctl/ctl.env)" -ge 5 ] && [ "$(grep -c "^CTL_" /rag/config/ctl/ctl-secrets.env)" -ge 2 ]
' && shred -u "$D"/*.env && rmdir "$D"
```

**Expect:** `svcbvbrc:cels 640 …/ctl.env` and `svcbvbrc:cels 600 …/ctl-secrets.env`;
the `shred` runs only after that check passed. Never `cat` the secrets file afterwards.
**Undo:** delete both files (the daemon then answers every authenticated call 401).

## 5. Adopt the four live tenants

Adoption runs **as wilke**, not through the wrapper: it reads every tenant's
`tenant.env` (0600, wilke-owned) to classify keys and fingerprint secrets, which
svcbvbrc cannot do until the handover (PR-E). The registry it writes is
group-readable (`0660 wilke:cels`), which is all the daemon needs.

```bash
/rag/bin/ragstack-ctl adopt-all --preview --registry /tmp/preview.json     # writes nothing
/rag/bin/ragstack-ctl adopt-all --commit  --registry /tmp/preview.json     # scratch registry + /tmp/manifest.tsv
diff <(cut -f1-8 /tmp/manifest.tsv) /rag/data/tenants/manifest.tsv         # must be empty

/rag/bin/ragstack-ctl adopt-all --commit                                    # the real registry
/rag/bin/ragstack-ctl doctor
```

**Expect:** empty `diff`; doctor reports **0 red** (yellow `worktree_gitdir_unreadable`
is expected until the handover). On coconut doctor is red for one pre-existing
item — demo's dormant `bin/up.sh` would start empty store instances; the ctl
never edits that file — which does not block any step here.
**Point of no return for `new-tenant.sh`:** once `registry.json` exists it refuses new port
blocks; the ctl allocates from here on.
**Undo:** `rm /rag/data/tenants/registry.json{,.lock,.generation}` — `manifest.tsv` is untouched.

## 6. Prove the gateway generation is a no-op

```bash
ops/coconut/ctl-as-svc.sh gateway diff                # -> semantic_noop: true
ops/coconut/ctl-as-svc.sh gateway apply --dry-run     # real nginx -t on a staged copy; writes nothing live
```

**Expect:** `semantic_noop: true`; dry run passes `nginx -t`. If the dry run warns
about an **incomplete** previous publication, run `gateway repair` first — a dry run
never repairs on its own.

**Do this publish BEFORE adding a tenant.** The first `apply` adopts the proxy's
bootstrap copies only when they are byte-identical to the generation being
published. If the registry already holds a fifth tenant, the switch refuses
("neither the generation being published nor the one currently published") and
you have to move both bootstrap copies aside by hand
(`mv …generated.conf …generated.conf.bootstrap.bak-<date>`) first. Publishing the
no-op generation first, then adopting and publishing the new tenant as
generation 2, avoids that entirely.

## 7. Publish (the one live change)

```bash
ops/coconut/ctl-as-svc.sh gateway apply --expect-bodies /rag/data/ctl/goldens
ops/coconut/ctl-as-svc.sh gateway status
ls -l /rag/config/proxy/conf.d/05-tenants.generated.conf /rag/config/proxy/snippets/tenants-ui-static.generated.conf
ops/coconut/verify.sh "$S"
```

**Expect:** result `verified`; same master pid; `confirm reload` ok (new worker set,
no `[emerg]`); four golden bodies byte-identical; both include paths are now
**symlinks** into `/rag/data/ctl/gateway/current/`; `verify.sh` says ALL GOOD.
**All four golden bodies embed the tenant list** (`/`, `/ragstack/tenants`, the
unknown-tenant 404 and the catch-all 404). After the fleet changes, regenerate
all four — `root.json`, `api-unknown-404.json` and `catchall-404.json` carry a
bare `tenants` name array to append the new name to; `tenants.json` carries a
`tenants` array of `{name,api,ui}` objects, so add a matching object instead —
and install them as svcbvbrc into `/rag/data/ctl/goldens`; a stale golden fails
the probe and the publish reverts — harmlessly, but it costs a generation
number each time.
If refused with *"nginx master is owned by uid N"* the proxy is running as wilke:
run the apply as wilke (`CTL_USER=wilke ops/coconut/ctl-as-svc.sh gateway apply …`)
or restart the proxy under svcbvbrc first. Nothing was written in that case.
**Undo:** `ops/coconut/ctl-as-svc.sh gateway rollback` (stages, tests, HUPs,
confirms, probes the target). First publish with nothing to roll back to:
remove the two symlinks, then `cd ~/Development/coconut-proxy && ./deploy.sh && ./proxy.sh reload`
— symlinks first, deploy second, never the other way round (full runbook, step 6).

## 8. Start the daemon

```bash
ops/coconut/ctl-daemon.sh start
ops/coconut/ctl-daemon.sh status                        # pid, launched-from vs installed binary, GET /health
curl -s -H "X-API-Key: <operator-key>" localhost:23990/v1/fleet | head -c 400
```

**Expect:** `running (pid …)` and `{"status":"ok",…}`; the fleet call lists four tenants.
The daemon binds loopback only; the public `/ragstack/admin/` mount stays off until
the registry sets `ctl.gateway_enabled: true`.
**Undo:** `ops/coconut/ctl-daemon.sh stop`. It signals only the process whose recorded
identity (`ctl.pid.meta`) matches; a mismatch is refused, not deleted. After a later
`make install-ctl`, `status` says "installed binary changed since launch; restart".

---

## Fresh host (interim, before PR-D)

Steps 0, 1, 5 and 6 above are coconut's migration. At the PR-B stage the ctl
**cannot create tenants** (that is PR-D: `fleet artifact prepare`, `tenant
create/start/stop`, units, boot persistence); it can only inventory tenants
that already exist and publish the gateway for them. So a fresh host is:

```bash
# host prep: apptainer + store SIFs + conda env + sysctl + ctl dirs (root, then the operator)
cd ops/ansible && ansible-playbook -i inventory/<site>.yml tenant-host.yml --check --diff -K && \
                  ansible-playbook -i inventory/<site>.yml tenant-host.yml -K

# provision EVERY tenant with the legacy script BEFORE the first adopt --commit:
# once registry.json exists, new-tenant.sh refuses to allocate a new port block.
apptainer/new-tenant.sh <name> --dry-run          # plan: dirs, ports, files
apptainer/new-tenant.sh <name> [--start]          # repeat per tenant

# then steps 2, 3 (dirs only; no goldens), 4, and:
cat > /tmp/tenants.json <<'JSON'
[{"name":"<name>","data_dir":"/rag/data/tenants/<name>","worktree":"/rag/repos/tenants/<name>","ui_port":5210}]
JSON
/rag/bin/ragstack-ctl adopt-all --preview --spec /tmp/tenants.json
/rag/bin/ragstack-ctl adopt-all --commit  --spec /tmp/tenants.json      # or: adopt <name> --data-dir … --worktree …
/rag/bin/ragstack-ctl doctor

# gateway: the proxy tree must carry the two generated include paths (coconut-proxy is
# coconut-specific in its hand-written parts; the generated includes are host-neutral)
ops/coconut/ctl-as-svc.sh gateway apply --dry-run
ops/coconut/ctl-as-svc.sh gateway apply           # no --expect-bodies: the goldens are coconut's responses
# then step 8
```

Tenants are still started by hand (or `ops/coconut/restore.sh`) until PR-D;
adoption is read-only inventory. A real greenfield guide replaces this section
once `tenant create` exists.

**Adding a tenant to a migrated host:**
**Not yet supported once the registry exists.** `new-tenant.sh` refuses to
allocate a port block after `adopt-all --commit`; the ctl's own `tenant
create` lands in PR-D. On 2026-09-14 the hackathon tenant was provisioned
BEFORE the first registry commit, which is the only sequence this recipe has
been proven for. (Escape hatch for an already-migrated host: see the note
below once verified.)

The sequence that worked pre-registry: `new-tenant.sh <name> --postgres local
--es-heap 1g` (dedicated Postgres on the block's +5 port; the sqlite default
and the shared-server `--postgres <dsn>` mode still exist — all three are
recorded as `stores.postgres.kind` = `local`/`sqlite`/`external`, see the
deploy runbook's "The relational store in the registry"), edit `tenant.env`
(identity, admins, GoWe ingest, limits — no inline comments), `git -C
~/Development/ragstack worktree add --detach /rag/repos/tenants/<name>
<tag>`, `npm ci` + `.env` in its `frontend/`, then either a Vite dev server or
a static bundle (`npx vite build --base /ragstack/<name>/ui/` copied to
`<data_dir>/ui/dist`). For a static UI nginx (svcbvbrc) must traverse the
tenant dir: `chmod 710 <data_dir>` and `chmod 700` its data subdirs (qdrant,
elasticsearch, postgres, state, ingest, manifests) — no `setfacl` on coconut.
Then `bin/up.sh`, start the API, `adopt <name> --data-dir … --worktree …
--ui-mode static` (or `--ui-port N`) as wilke, refresh the four goldens,
`gateway apply --expect-bodies /rag/data/ctl/goldens`.

**Escape hatch on an already-migrated host (verified in a scratch root 2026-09-14).**
`new-tenant.sh` reuses a manifest row that already exists, so hand-append the row
the registry would allocate (next index, `24000 + 20*index`), provision, then adopt
with `--repair-projection` (the hand edit makes the projection stale until then):

```bash
cp -a /rag/data/tenants/{registry.json,registry.json.generation,manifest.tsv} ~/ctl-undo/   # undo copies first
printf '<name>\t<index>\t<base>\n' >> /rag/data/tenants/manifest.tsv
apptainer/new-tenant.sh <name> --postgres local --es-heap 1g          # "[manifest] reusing index N, base P"
# … tenant.env, worktree, UI build, bin/up.sh, start the API (as below) …
/rag/bin/ragstack-ctl adopt <name> --data-dir /rag/data/tenants/<name> --worktree /rag/repos/tenants/<name> \
    --ui-mode static --commit --repair-projection
```

Registry generation advances by one; the daemon needs no restart (it re-reads the
registry per request). Do **not** use `adopt-all --commit --repair-projection` for
this: it repairs the projection first and then refuses ("already in registry.json"),
which deletes the row you just appended. `ragstack-ctl tenant create` (PR-D) replaces
this whole dance. Undo: restore the three copies from `~/ctl-undo/`, `bin/down.sh`,
kill the API listener, `git worktree remove /rag/repos/tenants/<name>`, remove the data dir.

**Undo:** `bin/down.sh`; kill the API listener; remove the worktree
(`git worktree remove`). There is no removal for the registry/manifest row
alone — dropping it means deleting `registry.json` and re-running `adopt-all
--commit` for the whole fleet.

---

## After

| Check | Command | Expect |
|---|---|---|
| tenants routed | `curl -s localhost:9000/ragstack/tenants` | unchanged four |
| registry | `ops/coconut/ctl-as-svc.sh doctor` | 0 red |
| gateway | `ops/coconut/ctl-as-svc.sh gateway status` | `txn_state: complete`, generation 1 |
| daemon | `ops/coconut/ctl-daemon.sh status` | running + health ok |
| fleet | `ops/coconut/verify.sh "$S"` | ALL GOOD |

Exit codes everywhere: `0` ok · `1` error · `2` usage · `3` refused. The wrapper
passes them through; its output comes off a pty, so `tr -d '\r'` before any
line-exact comparison.

Optional: a conformance run against the deployed daemon needs a second daemon
on another port with the rate limiter off — see the full runbook, "Running the
conformance suite against the deployed daemon".

Still waiting on root (not blocking any step above): linger + `user@10078`
drop-in, then the daemon moves from `ctl-daemon.sh` to `systemctl --user`
(full runbook, step 7, second half).
