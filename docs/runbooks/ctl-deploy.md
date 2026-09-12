# Runbook — deploying `ragstack-ctl` on coconut (PR-B)

What this covers: getting the control-plane binary onto coconut, adopting the
four live tenants into the registry, publishing the first **gateway
generation**, and running the daemon — plus how to undo each step.

Nothing here starts, stops or touches a tenant. Gateway publication changes
one symlink and sends one `SIGHUP`; every other step writes only to
`/rag/data/ctl`, `/rag/config/ctl` and `/rag/data/tenants/registry.json`.

**Accounts.** Build and install as `wilke`. Everything that writes control-plane
state runs as `svcbvbrc` through `ops/coconut/ctl-as-svc.sh` (sudo lives in
that one wrapper; the ctl has no sudo in any code path). Until the `svcbvbrc`
hand-over (PR-E) the nginx master is owned by whoever started the proxy — see
step 6, which refuses rather than guesses.

---

## 0. Prerequisites

| Thing | Check | If missing |
|---|---|---|
| Go toolchain (off-host) | `~/sdk/go1.23.12/bin/go version` | Build elsewhere and copy the binary; coconut has no Go on `PATH`. |
| `/rag/config/ctl`, `/rag/data/ctl` | `ls -ld /rag/config/ctl /rag/data/ctl` | Step 3. |
| `coconut-proxy` change deployed | `ls -l /rag/config/proxy/conf.d/05-tenants.generated.conf` | Step 1 — do it first (see the ordering note there). |
| sudo to `svcbvbrc` from a tty | `ops/coconut/ctl-as-svc.sh version` | Ask the admin for the `(svcbvbrc) NOPASSWD: ALL` rule (it exists today). |

The root items of the ansible `coconut-host` role (linger, the
`user@<uid>` drop-in, the proxy unit, the sysctl) are **not done yet**. That is
why step 7 runs the daemon with `ops/coconut/ctl-daemon.sh` instead of
`systemctl --user`. Nothing else in this runbook depends on them.

---

## 1. Deploy the coconut-proxy change FIRST

The ctl publishes into two include paths:

```
/rag/config/proxy/conf.d/05-tenants.generated.conf
/rag/config/proxy/snippets/tenants-ui-static.generated.conf
```

Those paths have to exist in the proxy configuration: `routes.conf` includes
the static snippet and reads `$tenants_names_json` / `$tenants_json` in its
four literal lists, and `00-maps.conf` drops the three `map $tenant …` tables.
That change is the `coconut-proxy` repo's, on branch `ctl-generated-includes`:

```bash
cd ~/Development/coconut-proxy
git checkout ctl-generated-includes
./deploy.sh --dry-run          # shows what would be copied + any deployed-tree drift
./deploy.sh                    # rsync into /rag/config/proxy, then reload
./proxy.sh status              # same master pid, still listening on 9000/9443
curl -s localhost:9000/ragstack/tenants
```

`deploy.sh` ships a committed **bootstrap copy** of each generated include, so
the proxy loads before the ctl has ever published. Those two files arrive as
regular files; `gateway apply` takes them over and replaces each with the
symlink into `/rag/data/ctl/gateway/current/` (it recognises them by content
and says so in its warnings). A regular file at either path that is *not* a ctl
render is refused as a hand edit — move it aside first.

**Order.** Deploy first, publish second — but the two are not a trap if you get
them the wrong way round. Measured on this host (2026-09-12, `nginx.sif`):
nginx does **not** reject a second `map` for a variable that already has one.
It accepts the configuration and the map loaded LAST wins, and
`conf.d/*.conf` is loaded in glob order, so `05-tenants.generated.conf` wins
over `00-maps.conf`. Publishing before the deploy therefore routes through the
generated maps (whose rows are identical today — `gateway diff` proves it) and
leaves the old tables as dead weight until the deploy removes them. What
publishing first does NOT give you is the static snippet include or the
`$tenants_json` lists in `routes.conf`: those come with the deploy.

Verified both ways with the real `nginx -t` inside `nginx.sif`, on a staged
copy of each tree:

```bash
ragstack-ctl gateway apply --dry-run                                        # deployed tree: passes
ragstack-ctl gateway apply --dry-run --proxy-dir ~/Development/coconut-proxy # branch checkout: passes
```

**Rollback:** `git checkout main && ./deploy.sh` in `~/Development/coconut-proxy`,
then `./proxy.sh reload`. Do that *after* `gateway rollback`/removing the
symlinks if a generation is already published (step 6 rollback).

---

## 2. Build off-host and install

```bash
cd ~/Development/ragstack
make build-ctl GO=$HOME/sdk/go1.23.12/bin/go      # go/bin/ragstack-ctl, static, -trimpath
make test-ctl  GO=$HOME/sdk/go1.23.12/bin/go      # must be green before installing
make install-ctl GO=$HOME/sdk/go1.23.12/bin/go    # /rag/bin/ragstack-ctl-<ver> + symlink
/rag/bin/ragstack-ctl version
```

**Rollback:** `ln -sfn ragstack-ctl-<previous> /rag/bin/ragstack-ctl`. The
versioned binaries are kept, so this is instant and needs no rebuild.

---

## 3. State and config directories (as `svcbvbrc`)

Everything that writes control-plane state goes through `ctl-as-svc.sh`, this
runbook included. A bare `sudo -u svcbvbrc …` is not equivalent: sudo on coconut
has `requiretty`, so it fails outright from a pipeline or a non-pty terminal,
and it does not set `XDG_RUNTIME_DIR` / `DBUS_SESSION_BUS_ADDRESS` (needed the
moment a step touches `systemctl --user`). The wrapper is also where the exit
status is preserved — see the note at the end of this section.

The wrapper runs `$CTL_BIN` with the arguments it is given, so pointing
`CTL_BIN` at a shell is how a one-off command runs as the service account
through the same single sudo path:

```bash
ops/coconut/ctl-as-svc.sh version              # proves the sudo path works

CTL_BIN=/bin/bash ops/coconut/ctl-as-svc.sh -c '
    mkdir -p /rag/data/ctl/{home,locks,gateway,goldens,tmp,artifacts,ui/dist,apptainer/{cache,config}} \
             /rag/config/ctl/{units,templates}
    chmod 2770 /rag/data/ctl /rag/config/ctl
    chmod 0700 /rag/data/ctl/tmp
'
```

Two of those directories are new and are not optional:

| Directory | Why |
|---|---|
| `/rag/data/ctl/tmp` (0700) | where `gateway apply` stages its throwaway copy of the proxy tree. That copy contains `tls/`, so on an account that CAN read `proxy.key` it is a readable private key; it is deliberately not in `/tmp`. |
| `/rag/data/ctl/goldens` | the four golden gateway bodies — see step 3a. |

### 3a. Install the golden bodies

`gateway apply --expect-bodies DIR` compares four responses byte-for-byte and is
PR-B's go/no-go. `DIR` must be a directory that exists **on coconut and is
readable by `svcbvbrc`**. `/rag/repos/ragstack` is a stale checkout (see
MEMORY: "live API runs from dev checkout") and must not be used for this.

Copy the set out of the checkout you built from, once:

```bash
G=~/Development/ragstack/go/internal/ctl/testdata/live-2026-09-10/gateway
CTL_BIN=/bin/bash ops/coconut/ctl-as-svc.sh -c "
    install -m 0644 -D -t /rag/data/ctl/goldens $G/root.json $G/tenants.json \
        $G/api-unknown-404.json $G/catchall-404.json
    ls -l /rag/data/ctl/goldens
"
# -> root.json tenants.json api-unknown-404.json catchall-404.json
```

(`$HOME/Development` must be readable by `svcbvbrc` for that to work; if it is
not, `cp` the four files to `/tmp` first and install them from there.)

From here every `gateway apply` in this runbook passes
`--expect-bodies /rag/data/ctl/goldens`. Running without it is allowed and the
CLI prints a loud `--expect-bodies was NOT passed` banner in the plan, before the
confirmation prompt — that banner means the publish will not check the one thing
it exists to prove.

**Rollback:** the directories are empty at this point; `rmdir` them.

---

## 4. `ctl.env` and `ctl-secrets.env`

`ctl.env` is public (0640), `ctl-secrets.env` holds the API keys (0600). Both
follow the envfile grammar: `KEY=value`, no `export`, no `$`, no trailing
comments. The authoritative key list is `go/internal/ctl/api/env.go`; the
ansible templates (`ops/ansible/roles/ragstack-ctl/templates/`) render exactly
that set and are reconciled by a test.

Both files are **created by `svcbvbrc` itself**, with their content carried
through `ctl-as-svc.sh`'s stdin — never through a `wilke`-owned temp file that
gets `install`ed or `cp`'d into place, and never as a command-line argument
(arguments are visible to any local user via `ps`, and land in the sudo log).
A file written as `wilke` under `umask 077` is 0600 and owned by `wilke`;
`svcbvbrc` cannot read it, so installing it AS `svcbvbrc` fails outright — the
values never reach `/rag/config/ctl` at all, and this is not hypothetical: it
is exactly what the previous version of this step did, and it failed this way.

**Transport: base64 over stdin.** `ctl-as-svc.sh` runs its command on a pty
(`script -qec` — see the wrapper's own header comment for why), and stdin IS
piped through to the far side unchanged; verified on this host:
`printf 'A=1\nB=x\n' | script -qec 'cat > f' /dev/null` reproduces the input
byte-for-byte in `f`, with no CR added. (The pty's line discipline affects the
child's *output* — the typescript, which this runbook already discards to
`/dev/null` — not what the child reads from stdin.) What raw envfile content
cannot safely cross a pty as-is is: a control character (an API key can
contain one), and canonical mode's 4095-byte line cap, which silently drops
anything longer. `base64 -w76` sidesteps both — 76 fixed printable-ASCII
columns per line — and round-trips the exact original bytes back out the
other side.

```bash
# wilke-only staging dir — never /tmp/ctl.env, which any local user can see.
D=$(mktemp -d /tmp/ctl-secrets.XXXXXX)
chmod 0700 "$D"

# Mint two keys. Print them ONCE, paste them into the file, clear the scrollback.
openssl rand -hex 32     # operator key
openssl rand -hex 32     # viewer key

umask 077
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

# Install both AS svcbvbrc: svcbvbrc decodes its own stdin, writes atomically
# (tmp + mv, so a killed transfer never leaves a half-written file at the real
# path) and sets its own mode. wilke never gets write access to the installed
# path and never needs read access to svcbvbrc's copy.
base64 -w76 <"$D/ctl.env" | CTL_BIN=/bin/bash ops/coconut/ctl-as-svc.sh -c '
    umask 077
    base64 -d > /rag/config/ctl/ctl.env.tmp &&
    mv /rag/config/ctl/ctl.env.tmp /rag/config/ctl/ctl.env &&
    chmod 0640 /rag/config/ctl/ctl.env
'
base64 -w76 <"$D/ctl-secrets.env" | CTL_BIN=/bin/bash ops/coconut/ctl-as-svc.sh -c '
    umask 077
    base64 -d > /rag/config/ctl/ctl-secrets.env.tmp &&
    mv /rag/config/ctl/ctl-secrets.env.tmp /rag/config/ctl/ctl-secrets.env &&
    chmod 0600 /rag/config/ctl/ctl-secrets.env
'

# Verify WITHOUT ever printing content: ownership/mode, ACL if this host has
# one, and a line-count / key-prefix sanity check. Asserts, so a bad install
# fails this command rather than silently passing.
CTL_BIN=/bin/bash ops/coconut/ctl-as-svc.sh -c '
    set -e
    stat -c "%U:%G %a %n" /rag/config/ctl/ctl.env /rag/config/ctl/ctl-secrets.env
    if command -v getfacl >/dev/null 2>&1; then
        getfacl -p /rag/config/ctl/ctl.env /rag/config/ctl/ctl-secrets.env
    fi
    [ "$(stat -c %a /rag/config/ctl/ctl.env)" = 640 ]
    [ "$(stat -c %a /rag/config/ctl/ctl-secrets.env)" = 600 ]
    wc -l /rag/config/ctl/ctl.env /rag/config/ctl/ctl-secrets.env
    [ "$(grep -c "^CTL_" /rag/config/ctl/ctl.env)" -ge 5 ]
    [ "$(grep -c "^CTL_" /rag/config/ctl/ctl-secrets.env)" -ge 2 ]
' &&
# Remove the wilke-side copies ONLY once the verification above exited 0 —
# chained with `&&` on purpose: an install that failed partway must leave the
# only prepared copies on disk, not have them shredded out from under it.
shred -u "$D/ctl.env" "$D/ctl-secrets.env" && rmdir "$D"
```

Never `cat`, `echo` or `grep` `ctl-secrets.env` into a terminal afterwards.
`ctl-daemon.sh` PARSES both files (`KEY=VALUE`, one pair of surrounding quotes
stripped, nothing expanded — the same rule systemd's `EnvironmentFile` applies)
and prints nothing from them; a line that does not match is reported by line
NUMBER, never by content.

**Rollback:** delete both files — but know what that actually does. The daemon
does **not** refuse to start without `ctl.env`: `serve` takes its settings from
the environment, so with the file gone it binds the default
`127.0.0.1:23990`, serves the anonymous `/health`, and answers every
authenticated operation `401 auth_required` because no key is configured. It
looks up and is unusable. Stop it with `ops/coconut/ctl-daemon.sh stop` rather
than relying on it to fall over.

---

## 5. Adopt the four live tenants

Rehearse against a scratch registry first — `--preview` writes nothing at all:

```bash
ops/coconut/ctl-as-svc.sh adopt-all --preview --registry /tmp/preview.json
ops/coconut/ctl-as-svc.sh adopt-all --commit  --registry /tmp/preview.json
# --commit writes the registry file named above AND its sibling projection,
# which is always called manifest.tsv — so /tmp/manifest.tsv, not
# /tmp/preview.tsv.
diff <(cut -f1-8 /tmp/manifest.tsv) /rag/data/tenants/manifest.tsv    # projection identical

ops/coconut/ctl-as-svc.sh adopt-all --commit      # the real registry
ops/coconut/ctl-as-svc.sh doctor
```

**This commit is the point of no return for `new-tenant.sh`:** from here the
registry is the allocator, and `apptainer/new-tenant.sh` refuses to allocate a
port block (PR-A added that `die`). Allocation happens through the ctl.

**Rollback:** `rm /rag/data/tenants/registry.json` (and `registry.json.lock`,
`.generation`). `manifest.tsv` is untouched by a rollback — it was reconciled,
not rewritten — and `new-tenant.sh` allocates again as soon as the registry is
gone.

---

## 6. Publish the first gateway generation

```bash
# a) the diff must be a SEMANTIC NO-OP: the generated maps route exactly what
#    the hand-written ones route today.
ops/coconut/ctl-as-svc.sh gateway diff
#    -> semantic_noop: true

# b) the dry run stages a temp copy of /rag/config/proxy with the generation's
#    two files in place and runs the REAL nginx -t inside nginx.sif.
#    It switches nothing and signals nothing, and writes nothing outside the
#    throwaway staging dir (not even a generation dir or txn.json).
#    Run as an account that cannot read tls/proxy.key it reports substituting a
#    throwaway self-signed pair IN THE STAGED COPY — otherwise nginx -t would
#    fail on file ownership rather than on the change under test.
ops/coconut/ctl-as-svc.sh gateway apply --dry-run

# c) publish. The four golden bodies are compared byte-for-byte: this is
#    PR-B's go/no-go. The directory is the one installed in step 3a.
ops/coconut/ctl-as-svc.sh gateway apply --expect-bodies /rag/data/ctl/goldens

ops/coconut/ctl-as-svc.sh gateway status
ls -l /rag/config/proxy/conf.d/05-tenants.generated.conf   # -> …/gateway/current/…
ops/coconut/verify.sh <snapshot>                           # ALL GOOD
```

`apply` runs, in this order:

1. render the next generation;
2. **verify the nginx master's `/proc` identity** — it exists, it is nginx, it
   is owned by this account. This happens *before* anything is written, so a
   proxy owned by someone else is refused (exit 3) with nothing on disk touched;
3. write `gen-<N>`;
4. stage a throwaway copy of the proxy tree under `/rag/data/ctl/tmp` and run
   the real `nginx -t` against it;
5. record the current state of both include paths in `txn.json`, then switch the
   `current` symlink;
6. `SIGHUP`, then **confirm the master took it**: the worker set must change and
   `run/logs/error.log` must carry no `[emerg]`/`[alert]` written after the
   signal. nginx answers a configuration it cannot use by logging `[emerg]` and
   keeping the old one — the signal itself always "succeeds";
7. probe from the desired state (tenant list, per-tenant health, the four
   bodies);
8. `txn.json: verified`, record the generation as verified, prune to the last
   five.

A failure at 1–4 leaves `current` untouched (`txn.json: failed`). A failure at
5–7 **reverts**: `current` goes back to the previous generation, both include
paths are restored to the content they had before the switch, and the master is
HUPed again (`txn.json: reverted`).

What a revert never does is leave an include *missing*. `routes.conf` includes
the static snippet and `10-gateway.conf` reads `$tenant_api`, so a missing
include or a symlink into a deleted `current` is an nginx that will not start at
its next reload. Concretely, on a failed **first** publish:

* a path that was a coconut-proxy bootstrap copy gets those exact bytes written
  back, as a regular file;
* a path that did not exist at all gets the just-rendered generation's bytes
  written there as a **regular file**, and the result says so in its warnings.
  A configuration nobody verified but that loads beats a gateway that cannot
  start; remove it by hand or re-run `gateway apply` once the cause is fixed.

Only one `apply`, `rollback` or `repair` can run at a time — they take an
exclusive `flock` on `/rag/data/ctl/gateway/.lock` for their whole duration, and
a second one is refused immediately (exit 3) rather than queued.

If the reload is refused with *"nginx master is owned by uid N; run as that
account"*, the proxy is running as someone else (today: `wilke`). Either run
the apply as that account (`CTL_USER=wilke ops/coconut/ctl-as-svc.sh …`, or
just `/rag/bin/ragstack-ctl gateway apply` as `wilke`) or restart the proxy
under `svcbvbrc` first. The ctl will not signal a process it does not own.

**Rollback (in order of preference):**

1. `ops/coconut/ctl-as-svc.sh gateway rollback` — stages the target generation
   and runs `nginx -t` on it, repoints `current`, HUPs, confirms the reload, and
   probes the gateway against **the target generation's own** tenant list (not
   today's registry: an old generation routes what it routed). On any failure
   after its switch it goes back to the generation it started from.
   Refused (exit 3) when there is no previous generation, when the target is not
   on disk, or when the target never reached `verified` — a generation that was
   published and reverted is on disk and was never a state this host served.
2. First publish, nothing to roll back to: remove the two symlinks, restore the
   bootstrap copies from the proxy repo, reload.
   ```bash
   CTL_BIN=/bin/rm ops/coconut/ctl-as-svc.sh \
       /rag/config/proxy/conf.d/05-tenants.generated.conf \
       /rag/config/proxy/snippets/tenants-ui-static.generated.conf
   cd ~/Development/coconut-proxy && ./deploy.sh && ./proxy.sh reload
   ```
3. Whole gateway change: back to the hand-written maps.
   **Remove the two symlinks FIRST, before the deploy** — the order matters and
   is not obvious:
   ```bash
   CTL_BIN=/bin/rm ops/coconut/ctl-as-svc.sh \
       /rag/config/proxy/conf.d/05-tenants.generated.conf \
       /rag/config/proxy/snippets/tenants-ui-static.generated.conf
   cd ~/Development/coconut-proxy && git checkout main && ./deploy.sh && ./proxy.sh reload
   ```
   `deploy.sh` rsyncs the tracked files in; it does not delete a file that is no
   longer tracked. So a `main` that has no `05-tenants.generated.conf` leaves the
   ctl's symlink exactly where it is — and `conf.d/*.conf` loads in glob order,
   so `05-…` still wins over the `00-maps.conf` the deploy just restored. You
   would have "rolled back" to a tree still routed by the generated maps, with
   nothing on screen saying so. Removing the symlinks first makes the deploy the
   only source of routing again.

If a publish is interrupted (a killed shell, a reboot), `gateway status` reports
`txn_state: incomplete`. It does **not** fix anything: `status` is a read — it
is a viewer operation, and it used to move `current` from a dashboard poll, with
no lock, possibly while somebody's `apply` was mid-flight. Putting the pointer
back on the last verified generation is `gateway repair`, which takes the lock
and which an operator runs:

```bash
ops/coconut/ctl-as-svc.sh gateway repair    # moves `current`, acknowledges the txn
/rag/config/proxy/proxy.sh reload           # repair does NOT signal; this is what makes it live
```

`repair` deliberately sends no signal — it only makes the pointer honest — and
prints the reload command to run after it. The running workers keep serving the
old configuration until that reload.

---

## 7. Run the daemon

Root has not installed the linger + `user@<uid>` drop-in yet, so the user unit
cannot start at boot. Until then:

```bash
ops/coconut/ctl-daemon.sh start
ops/coconut/ctl-daemon.sh status          # pid + GET /health
curl -s -H "X-API-Key: <operator-key>" localhost:23990/v1/gateway | head -c 400
tail -f /rag/data/ctl/ctl.log
```

`start` sets the same `umask 0002`, `HOME=/rag/data/ctl/home` and working
directory the user unit does, and the daemon writes its own pidfile
(`serve --pidfile`) once it is listening — so the pidfile's existence means
"up", and `stop` only signals a process that is this binary running `serve`. An
in-flight `ragstack-ctl gateway apply` is never mistaken for the daemon.

The daemon binds loopback only. It is reachable from outside exclusively
through the gateway's `/ragstack/admin/api/` mount, which is added by the
generated static snippet **only when the registry says
`ctl.gateway_enabled: true`** — it is `false` today, so PR-B publishes no admin
mount at all.

Once root has done its part, switch to the supervised form:

```bash
CTL_BIN=/usr/bin/install ops/coconut/ctl-as-svc.sh -m 0644 \
    ops/systemd/ragstack-ctl.service /rag/config/ctl/units/
ops/coconut/ctl-daemon.sh stop
CTL_BIN=/bin/systemctl ops/coconut/ctl-as-svc.sh --user daemon-reload
CTL_BIN=/bin/systemctl ops/coconut/ctl-as-svc.sh --user enable --now ragstack-ctl.service
```

(The `systemctl --user` calls are exactly why `ctl-as-svc.sh` exports
`XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS`: a plain `sudo -u` session has
neither, and systemd answers "Failed to connect to bus", which reads like a
broken systemd rather than a missing variable.)

**Rollback:** `ops/coconut/ctl-daemon.sh stop` — it signals only a process whose
executable is this binary and whose `argv[1]` is `serve`; never `pkill`, and
never a pid that merely has "ragstack-ctl" somewhere in its command line.
Nothing else depends on the daemon: every CLI verb works without it.

---

### Running the conformance suite against the deployed daemon

The daemon's production rate limiter counts credential-less failures in one
shared `anon` bucket (20 per minute, then 429). The black-box suite
`conformance/ctl` deliberately sends more anonymous failures than that, so a
run against a real-driver daemon answers 429 where the contract says 401 and
nine tests fail. That is the limiter working, not a defect. For a verification
run, start a **second** daemon on another loopback port with the limiter
disabled and point the suite at it — never relax the limiter on the daemon
the gateway routes to:

```bash
CTL_RATE_LIMIT_PER_CREDENTIAL=-1 /rag/bin/ragstack-ctl serve --listen 127.0.0.1:23998 &
RAGSTACK_CTL_URL=http://127.0.0.1:23998 RAGSTACK_CTL_API_KEY=<operator> \
RAGSTACK_CTL_API_KEY_VIEWER=<viewer> pytest -q conformance/ctl
```

Expected: everything passes except two skips (the unlisted-bearer case needs
the fixture key server, which only `--fake-drivers` provides, and the mutation
module lands in PR-C). Verified 2026-09-12 on coconut over a scratch registry
adopted from the four live tenants: 105 passed, 2 skipped.

## Verification summary

| Step | Command | Expected |
|---|---|---|
| 1 | `curl -s localhost:9000/ragstack/tenants` | the four tenants, unchanged |
| 2 | `/rag/bin/ragstack-ctl version` | the version just built |
| 5 | `ragstack-ctl doctor` | 0 red |
| 5 | `diff` of the manifest projection | identical |
| 6 | `gateway diff` | `semantic_noop: true` |
| 6 | `gateway apply --dry-run` | `nginx -t` passes on the staged tree |
| 3a | `ls /rag/data/ctl/goldens` | the four `*.json` bodies |
| 6 | `gateway apply --expect-bodies /rag/data/ctl/goldens` | `verified`, same master pid, four bodies identical, `confirm reload` ok |
| 6 | `ls -l /rag/config/proxy/{conf.d/05-tenants,snippets/tenants-ui-static}.generated.conf` | both symlinks into `…/gateway/current/` |
| 6 | `ops/coconut/verify.sh` | ALL GOOD |
| 7 | `ctl-daemon.sh status` | `running (pid …)` + `{"status":"ok",…}` |

**Exit codes.** Every step above is checkable in a script: `0` ok, `1` error,
`2` usage, `3` refused. `ctl-as-svc.sh` passes the ctl's status through
(`script -qec`); it did not before, so a failed step used to read as success.
Its output comes off a pty, so pipe it through `tr -d '\r'` before any
line-exact comparison.
