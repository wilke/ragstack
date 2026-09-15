# Runbook — deploying `ragstack-ctl` on coconut (PR-B)

Short form: [`ctl-quickstart.md`](ctl-quickstart.md).

What this covers: getting the control-plane binary onto coconut, adopting the
four live tenants into the registry, publishing the first **gateway
generation**, and running the daemon — plus how to undo each step.

Nothing here starts, stops or touches a tenant. Gateway publication changes
one symlink and sends one `SIGHUP`; every other step writes only to
`/rag/data/ctl`, `/rag/config/ctl` and `/rag/data/tenants/registry.json`.

**Accounts.** Build and install as `wilke`. Everything that writes control-plane
state runs as `svcbvbrc` through `/rag/bin/ctl-as-svc.sh` (sudo lives in
that one wrapper; the ctl has no sudo in any code path). Until the `svcbvbrc`
hand-over (PR-E) the nginx master is owned by whoever started the proxy — see
step 6, which refuses rather than guesses.

---

## Where production code lives

Plan decision (2026-09-15): **nothing in production may reference a home
directory.** Every path below is under `/rag`; a developer's
`~/Development/ragstack` is where the code is *written*, never where it
*runs from* or is *built for a deploy*.

| What | Lives at | How it gets there |
|---|---|---|
| `ragstack-ctl` binary | `/rag/bin/ragstack-ctl-<ver>` (+ `ragstack-ctl` symlink) | `make install-ctl`, built from `/rag/repos/ragstack` |
| daemon/wrapper scripts | `/rag/bin/{ctl-daemon.sh,ctl-as-svc.sh,restore.sh,pre-reboot.sh,snapshot.sh,verify.sh}` | `make install-ops` (source: `ops/coconut/` in the repo) |
| the bare mirror | `/rag/repos/ragstack.git` | `git clone --bare` once; `git fetch` after every push — never worked in |
| tenant worktrees | `/rag/repos/tenants/<name>`, checked out **from the mirror** | `tenant create` / `tenant rebase-worktree` (§ below) |
| operator clone | `/rag/repos/ragstack` | `git clone /rag/repos/ragstack.git /rag/repos/ragstack` — a plain, disposable checkout; builds and `make` targets run here, never in a developer's home |
| prepared artifacts | `/rag/data/ctl/artifacts/<tag>-<sha>/worktree` | `fleet artifact prepare --tag <ref>` |
| Python env(s) tenants run under | `/rag/envs/ragstack` (or a per-artifact env under `/rag/data/ctl/artifacts/…`) | `make install-python` / the artifact's own env |
| node (for `npm ci` / frontend builds) | `/rag/tools/node/<ver>`, `/rag/tools/node/current` symlink | `make install-node NODE_VERSION=vX.Y.Z`; `ctl.env`: `CTL_NODE_BIN=/rag/tools/node/current/bin/node`, `CTL_NPM_BIN=/rag/tools/node/current/bin/npm` (already the ctl's compiled-in defaults — `drivers/real.go`'s `defaultNodeBin`/`defaultNpmBin`) |

**Deploy sequence**, once code is reviewed and tagged:

```bash
git push origin <tag>                                    # from wherever the tag was cut
git -C /rag/repos/ragstack.git fetch --all --tags         # the mirror picks it up
cd /rag/repos/ragstack && git fetch --tags && git checkout <tag>
make install-ctl install-ops                              # binary + daemon/wrapper scripts onto /rag/bin
/rag/bin/ctl-daemon.sh stop && /rag/bin/ctl-daemon.sh start   # restart onto the new binary
```

`make check-ops` is install-ops's own gate (`bash -n` on the six scripts, plus
a grep that fails the build if any of them references `/home/` or `~/` outside
a comment) — `install-ops` runs it first, so a script that regressed into
referencing a home directory never reaches `/rag/bin`.

The five tenant worktrees checked out under `~/Development/ragstack/.git/
worktrees/<t>` before this plan (`doctor`'s `worktree_outside_mirror`) are
moved onto the mirror with `ragstack-ctl tenant rebase-worktree <name>` — see
that command's own `--help` for what it does and does not touch. It is a
**local, direct-only** action (no daemon route): run it as the worktree's
OWNER (today, wilke), not through `ctl-as-svc.sh`.

`doctor`'s `home_path_in_production` finding (warn, never blocks an op) is
the standing check that this table stays true: it flags a registry
`data_dir`/`worktree`/`python_env`, a prepared artifact's worktree, a
`ctl.env` host-tool value, or a tenant's live API process cwd/argv[0], if any
of them is still under `/home` or starts with `~`.

---

## 0. Prerequisites

| Thing | Check | If missing |
|---|---|---|
| Go toolchain | `make go-mode` → `GO_MODE=container` (or `host`) | `make golang-sif` pulls `golang:1.23.12` (the `toolchain` line of `go/go.mod`) into `/rag/apptainer/images/golang.sif`; coconut has no Go on `PATH`, so the ctl targets build inside that image. A host toolchain still works: `GO=~/sdk/go1.23.12/bin/go`. |
| `/rag/config/ctl`, `/rag/data/ctl` | `ls -ld /rag/config/ctl /rag/data/ctl` | Step 3. |
| `coconut-proxy` change deployed | `ls -l /rag/config/proxy/conf.d/05-tenants.generated.conf` | Step 1 — do it first (see the ordering note there). |
| sudo to `svcbvbrc` from a tty | `/rag/bin/ctl-as-svc.sh version` | Ask the admin for the `(svcbvbrc) NOPASSWD: ALL` rule (it exists today). |

The root items of the ansible `coconut-host` role (linger, the
`user@<uid>` drop-in, the proxy unit, the sysctl) are **not done yet**. That is
why step 7 runs the daemon with `/rag/bin/ctl-daemon.sh` instead of
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
That change is on the `coconut-proxy` repo's `main` (PR #1, merged 2026-09-12 as
14baa1b):

```bash
cd ~/Development/coconut-proxy
git checkout main && git pull --ff-only
./deploy.sh --dry-run          # shows what would be copied + any deployed-tree drift
./deploy.sh                    # rsync into /rag/config/proxy, then reload (first run ever: --force)
./proxy.sh status              # same master pid, still listening on 9000/9443
curl -s localhost:9000/ragstack/tenants
```

`deploy.sh` ships a committed **bootstrap copy** of each generated include, so
the proxy loads before the ctl has ever published. Those two files arrive as
regular files; `gateway apply` takes them over and replaces each with the
symlink into `/rag/data/ctl/gateway/current/` — but only when their content is
**byte-identical to the generation being published**, i.e. the first publish is
the semantic no-op of step 6. A regular file at either path with any other
content is refused as a hand edit ("neither the generation being published nor
the one currently published"). So publish the no-op generation *before* adopting
a new tenant; if you already adopted one, move both copies aside
(`mv …generated.conf …generated.conf.bootstrap.bak-<date>`) and publish — that
is what the coconut migration of 2026-09-14 had to do.

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

Build from **`/rag/repos/ragstack`**, the operator clone of the bare mirror
(see "Where production code lives" below) — not a developer's
`~/Development/ragstack`, which is where the code is EDITED, not where a
deploy is BUILT from. Push the tag, let the mirror pick it up, then check it
out where the build runs:

```bash
# on the machine that pushes (a dev checkout, or CI): tag and push as usual
git -C ~/Development/ragstack push origin <tag>

# on coconut, as wilke: the mirror already has every ref a push updates
# (it is a clone of the same remote, fetched — never worked in — see below)
git -C /rag/repos/ragstack.git fetch --all --tags

cd /rag/repos/ragstack
git fetch --tags && git checkout <tag>
make golang-sif        # once per toolchain bump: pulls golang:1.23.12 → /rag/apptainer/images/golang.sif (~290 MB)
make go-mode           # -> GO_MODE=container  (host `go` on PATH would win: GO_MODE=host)
make build-ctl         # go/bin/ragstack-ctl, static, -trimpath, built inside the image
make test-ctl          # race detector; must be green before installing
make install-ctl       # /rag/bin/ragstack-ctl-<ver> + symlink
make install-ops       # /rag/bin/{ctl-daemon.sh,ctl-as-svc.sh,restore.sh,pre-reboot.sh,snapshot.sh,verify.sh}, 0755
/rag/bin/ragstack-ctl version
```

The image is the exact `toolchain go1.23.12` that `go/go.mod` pins and that CI's
`actions/setup-go` reads from the same file, so a coconut build and a CI build
use the same compiler. Module and build caches live under `/rag/cache/go`, not
in the NFS home. `GO_MODE=host GO=~/sdk/go1.23.12/bin/go` builds with a host
toolchain instead; `GO_MODE=container` forces the image even when a host `go`
exists. Only *building and testing* happen in the container: the ctl binary
itself always runs on the host, because a rootless Apptainer user namespace
cannot read `/proc/<pid>/{cwd,exe,fd}` of processes outside it, and adopt,
doctor and the gateway preflight all need those.

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
/rag/bin/ctl-as-svc.sh version              # proves the sudo path works

CTL_BIN=/bin/bash /rag/bin/ctl-as-svc.sh -c '
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
CTL_BIN=/bin/bash /rag/bin/ctl-as-svc.sh -c "
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
# `>/dev/null` on the wrapper is LOAD-BEARING: the pty ECHOES everything it
# reads on stdin back out through script's stdout, so without it the base64 of
# your secrets is printed to the terminal — and into any scrollback, log or
# session transcript. Verified on this host 2026-09-14 (it happened). The exit
# status still comes through; verification is the separate command below.
# Incomplete on its own, though: the redirect also discards stderr from the
# base64 -d/mv/chmod chain (the child's stderr lands on the same pty stdout),
# so a failure here is a bare non-zero exit with no reason. To diagnose,
# re-run the same command WITHOUT `>/dev/null` using a dummy payload —
# never the real secrets.
base64 -w76 <"$D/ctl.env" | CTL_BIN=/bin/bash /rag/bin/ctl-as-svc.sh -c '
    umask 077
    base64 -d > /rag/config/ctl/ctl.env.tmp &&
    mv /rag/config/ctl/ctl.env.tmp /rag/config/ctl/ctl.env &&
    chmod 0640 /rag/config/ctl/ctl.env
' >/dev/null
base64 -w76 <"$D/ctl-secrets.env" | CTL_BIN=/bin/bash /rag/bin/ctl-as-svc.sh -c '
    umask 077
    base64 -d > /rag/config/ctl/ctl-secrets.env.tmp &&
    mv /rag/config/ctl/ctl-secrets.env.tmp /rag/config/ctl/ctl-secrets.env &&
    chmod 0600 /rag/config/ctl/ctl-secrets.env
' >/dev/null

# Verify WITHOUT ever printing content: ownership/mode, ACL if this host has
# one, and a line-count / key-prefix sanity check. Asserts, so a bad install
# fails this command rather than silently passing.
CTL_BIN=/bin/bash /rag/bin/ctl-as-svc.sh -c '
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

**Optional `ctl.env` rows: where the host programs are.** The real drivers run
programs by absolute path and never search `PATH`. Each variable below is
optional; unset takes the default. Add a row only when this host differs —
coconut's node does, which is the reason these exist.

| Variable | Default | What it is |
|---|---|---|
| `CTL_SYSTEMCTL_BIN` | `/usr/bin/systemctl` | the `systemctl --user` the unit verbs run. |
| `CTL_GIT_BIN` | `/usr/bin/git` | the `git` that resolves refs and manages artifact worktrees. |
| `CTL_NODE_BIN` | `/rag/tools/node/current/bin/node` | the node that runs `vite build` for a tenant UI. |
| `CTL_NPM_BIN` | `/rag/tools/node/current/bin/npm` | the npm `fleet artifact prepare` installs an artifact's frontend with. |
| `CTL_APPTAINER_BIN` | `/usr/bin/apptainer` | the apptainer the gateway and the store drivers exec. |
| `CTL_MIRROR` | `<rag-root>/repos/ragstack.git` | the BARE mirror artifacts are prepared from. The ctl never creates it — see the root items. |
| `CTL_NPM_CACHE` | `<rag-root>/cache/npm` | the npm cache an artifact install writes through (never `~/.npm`). |

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
looks up and is unusable. Stop it with `/rag/bin/ctl-daemon.sh stop` rather
than relying on it to fall over.

---

## 5. Adopt the four live tenants

Adoption runs **as wilke**, not through the wrapper: it reads every tenant's
`tenant.env` (0600, wilke-owned) to classify keys and fingerprint secrets, which
svcbvbrc cannot do until the handover (PR-E). The registry it writes is
`0660 wilke:cels`, which is all the daemon needs to read it. `adopt-all
--commit` also rewrites `manifest.tsv` itself, at `0664`, every time it runs.

Rehearse against a scratch registry first — `--preview` writes nothing at all:

```bash
/rag/bin/ragstack-ctl adopt-all --preview --registry /tmp/preview.json
/rag/bin/ragstack-ctl adopt-all --commit  --registry /tmp/preview.json
# --commit writes the registry file named above AND its sibling projection,
# which is always called manifest.tsv — so /tmp/manifest.tsv, not
# /tmp/preview.tsv.
diff <(cut -f1-8 /tmp/manifest.tsv) /rag/data/tenants/manifest.tsv    # projection identical

/rag/bin/ragstack-ctl adopt-all --commit           # the real registry
/rag/bin/ragstack-ctl doctor
```

**This commit is the point of no return for `new-tenant.sh`:** from here the
registry is the allocator, and `apptainer/new-tenant.sh` refuses to allocate a
port block (PR-A added that `die`). Allocation happens through the ctl.

**Rollback:** `rm /rag/data/tenants/registry.json` (and `registry.json.lock`,
`.generation`). `manifest.tsv` is untouched by a rollback — it was reconciled,
not rewritten — and `new-tenant.sh` allocates again as soon as the registry is
gone.

### The relational store in the registry (`stores.postgres`)

Every tenant row carries `stores.postgres`, the ACL / job / collection store,
in one of the three shapes `new-tenant.sh` provisions and records in
`config/provision.env` as `TENANT_STORE_KIND`:

| `kind` | provisioned by | row |
|---|---|---|
| `sqlite` | the default (no `--postgres`) | `ownership: exclusive`; `url`/`port`/`instance`/`sif`/`data_dir` all null — the state is files under `<data_dir>/state`. |
| `local` | `--postgres local` | a dedicated apptainer instance `postgres-<name>` from `postgres.sif`, bound to 127.0.0.1 on the block's **+5** port (`ports.pg`), data at rest under `<data_dir>/postgres`. `ownership: exclusive`. |
| `external` | `--postgres <admin-dsn>` | a database and role in a server somebody else runs. `ownership: external`, `url` names the server, and nothing else is claimed. |

`url` is **host and port only** (`postgresql://<host>:<port>`) — the schema's
pattern refuses a userinfo, a database name and a query string. The three DSNs
the tenant actually connects with (`USER_STORE_DSN`, `JOB_STORE_DSN`,
`COLLECTION_STORE_DSN`) carry a password and stay `secret_refs` entries, name
and file only, like every other secret-class key.

Only `kind: local` binds the +5 port, so only there does `doctor` treat a
listener on it as the tenant's own; a listener on the +5 port of a `sqlite` or
`external` tenant is still an `unexpected_listener`. A `local` tenant that is
`active` with nothing on that port raises `postgres_not_listening` (warn) —
its user, job and collection stores are all in that server.

**A registry written before this field existed** (anything committed before
2026-09-14) has no `postgres` member. It still loads: the missing row is
filled with the `sqlite` default rather than refusing the file, because
refusing it would take the whole control plane down on a binary upgrade. That
default is wrong for a `--postgres local` tenant, and the symptom is exactly
the `unexpected_listener` warning on its +5 port that `doctor` was already
raising. The fix is to re-adopt — `adopt-all --preview` shows the `postgres`
line per tenant, `--commit` records it — after which the warning clears.

---

## 6. Publish the first gateway generation

```bash
# a) the diff must be a SEMANTIC NO-OP: the generated maps route exactly what
#    the hand-written ones route today.
/rag/bin/ctl-as-svc.sh gateway diff
#    -> semantic_noop: true

# b) the dry run stages a temp copy of /rag/config/proxy with the generation's
#    two files in place and runs the REAL nginx -t inside nginx.sif.
#    It switches nothing and signals nothing, and writes nothing outside the
#    throwaway staging dir (not even a generation dir or txn.json).
#    Run as an account that cannot read tls/proxy.key it reports substituting a
#    throwaway self-signed pair IN THE STAGED COPY — otherwise nginx -t would
#    fail on file ownership rather than on the change under test.
/rag/bin/ctl-as-svc.sh gateway apply --dry-run

# c) publish. The four golden bodies are compared byte-for-byte: this is
#    PR-B's go/no-go. The directory is the one installed in step 3a.
/rag/bin/ctl-as-svc.sh gateway apply --expect-bodies /rag/data/ctl/goldens

/rag/bin/ctl-as-svc.sh gateway status
ls -l /rag/config/proxy/conf.d/05-tenants.generated.conf   # -> …/gateway/current/…
/rag/bin/verify.sh <snapshot>                           # ALL GOOD
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
the apply as that account (`CTL_USER=wilke /rag/bin/ctl-as-svc.sh …`, or
just `/rag/bin/ragstack-ctl gateway apply` as `wilke`) or restart the proxy
under `svcbvbrc` first. The ctl will not signal a process it does not own.

**Rollback (in order of preference):**

1. `/rag/bin/ctl-as-svc.sh gateway rollback` — stages the target generation
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
   CTL_BIN=/bin/rm /rag/bin/ctl-as-svc.sh \
       /rag/config/proxy/conf.d/05-tenants.generated.conf \
       /rag/config/proxy/snippets/tenants-ui-static.generated.conf
   cd ~/Development/coconut-proxy && ./deploy.sh && ./proxy.sh reload
   ```
3. Whole gateway change: back to the hand-written maps.
   **Remove the two symlinks FIRST, before the deploy** — the order matters and
   is not obvious:
   ```bash
   CTL_BIN=/bin/rm /rag/bin/ctl-as-svc.sh \
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
/rag/bin/ctl-as-svc.sh gateway repair    # moves `current`, acknowledges the txn
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
/rag/bin/ctl-daemon.sh start
/rag/bin/ctl-daemon.sh status          # pid + GET /health
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
CTL_BIN=/usr/bin/install /rag/bin/ctl-as-svc.sh -m 0644 \
    ops/systemd/ragstack-ctl.service /rag/config/ctl/units/
/rag/bin/ctl-daemon.sh stop
CTL_BIN=/bin/systemctl /rag/bin/ctl-as-svc.sh --user daemon-reload
CTL_BIN=/bin/systemctl /rag/bin/ctl-as-svc.sh --user enable --now ragstack-ctl.service
```

(The `systemctl --user` calls are exactly why `ctl-as-svc.sh` exports
`XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS`: a plain `sudo -u` session has
neither, and systemd answers "Failed to connect to bus", which reads like a
broken systemd rather than a missing variable.)

**Rollback:** `/rag/bin/ctl-daemon.sh stop` — it signals only a process whose
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

Expected: everything passes except the unlisted-bearer case, which needs the
fixture key server that only `--fake-drivers` provides, and — until the job
engine is wired — the engine-gated half of `conformance/ctl/test_ops.py`. That
half skips with a reason naming the probe it made: on a daemon whose engine
did not build, every mutation answers `409 refused` with "not wired" in the
detail, and the suite says so rather than asserting against a surface that
performs nothing. The authorization, envelope-validation and viewer-reduction
tests in that module run either way. Verified 2026-09-12 on coconut over a
scratch registry adopted from the four live tenants: 105 passed, 2 skipped.

## PR-D: root items and the boot rehearsal

PR-D is the release in which the control plane stops planning and starts
running: `tenant create`, `backup --fence`, `restore --as`, `decommission` and
`selftest` all touch the host. Four things have to be true on coconut before
they can, and three of them are **root** items this runbook cannot do for you.
The fourth — the mirror — is a `wilke` item, and it is already done.

Nothing below changes a tenant. Every step is either a root action on the
`svcbvbrc` account or a read.

### 1. Linger for `svcbvbrc` (root)

```bash
sudo loginctl enable-linger svcbvbrc
ls -l /var/lib/systemd/linger/svcbvbrc     # the file IS the state
```

Without it there is no `user@10078.service` unless somebody is logged in as
`svcbvbrc`, so `systemctl --user` fails with "Failed to connect to bus" and
**nothing comes back after a reboot**. This is why PR-D's acceptance runs as
`wilke` through `--direct`: same engine, same flock files, a user manager that
exists. The daemon path needs this.

The ansible `coconut-host` role does it (`--tags root -K`, section 2).

### 2. The `user@10078.service.d` drop-in (root)

```ini
# /etc/systemd/system/user@10078.service.d/ragstack.conf
[Unit]
RequiresMountsFor=/rag
After=remote-fs.target network-online.target
[Service]
Environment=SYSTEMD_UNIT_PATH=/rag/config/ctl/units:
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart user@10078.service     # ONLY PID 1 re-reads this file
sudo -u svcbvbrc XDG_RUNTIME_DIR=/run/user/10078 systemctl --user show -p UnitPath --value
```

Two facts, both load-bearing:

* **`RequiresMountsFor=/rag`** — every rendered unit carries
  `ConditionPathIsMountPoint=/rag`. A manager that starts before `/rag` is
  mounted finds the condition false, *skips* each unit, and exits 0. Nothing
  fails; the tenants are simply not there.
* **`SYSTEMD_UNIT_PATH=/rag/config/ctl/units:`** (the trailing colon keeps the
  default search path) — the ctl writes units into its own config tree, which
  the manager does not search. Until the drop-in is in effect the ctl works
  around it by `systemctl --user link`-ing each rendered unit, which is
  idempotent and harmless but leaves the manager's view depending on a link
  somebody could remove.

`SYSTEMD_UNIT_PATH` is a *process* environment variable of the manager, so a
`daemon-reload` does not pick it up — only a restart of `user@10078.service`
does. The ansible role asserts the variable actually reached the manager
rather than trusting that the file was written.

The ansible `coconut-host` role writes it (`--tags root -K`, section 3).

### 3. The ops group — the outstanding decision (root)

There is no `ragops` group on coconut. Everything is group **`cels`, with 1869
members**. And:

```
drwxr-xr-x  wilke  cels   /rag/data/tenants
drwxr-xr-x  wilke  cels   /rag/repos/tenants
-rw-rw----  wilke  cels   /rag/data/tenants/registry.json
```

Both tenant roots are **`wilke` 755**, so the `svcbvbrc` daemon **cannot create
a tenant**: `tenant create` makes `<data_dir>` and `<worktree>` under them, and
svcbvbrc may not write either. That is the whole of the gap — the ctl itself is
ready.

The ctl will not work around it. It never `chmod`s a parent it did not create
(the `Files.MkdirAll` contract says so in as many words), because "fixing" the
mode of a directory shared with 1869 accounts is not a repair, it is a change
nobody asked for. New tenant trees the ctl creates are 2770 with the setgid bit
so the group is inherited; the parents are somebody's to decide.

**The decision, stated plainly:** a small group has to own
`/rag/data/tenants` and `/rag/repos/tenants` — say `ragops`, containing `wilke`
and `svcbvbrc` — and those two directories have to become `2775` (or `2770`)
group `ragops`. `cels` is not that group: giving 1869 accounts write access to
every tenant's data directory is a larger change than the one being avoided.
Until it is made, run PR-D's verbs as `wilke` through `--direct` (which is what
the acceptance does) and treat the daemon's create path as undeployed.

The ansible `coconut-host` role creates the group and enrols both accounts; the
`ragstack-ctl` role's "group pass" then chgrps the trees. Neither has run.

### 4. The bare mirror (wilke — done)

```bash
git clone --mirror https://github.com/wilke/ragstack.git /rag/repos/ragstack.git
git -C /rag/repos/ragstack.git config core.sharedRepository group
```

`fleet artifact prepare` REQUIRES it: an artifact is a worktree checked out of
the mirror at a reviewed sha, and every tenant runs its own checkout at a
pinned sha (MEMORY: "tenant code isolation"). The ctl never creates the
mirror — cloning it is a deploy-time act, and a control plane that could
create its own code source would be a control plane that decides what code it
runs.

It exists on coconut since 2026-09-14. Before it, tenant worktrees hung off
`~/Development/ragstack` and `doctor` reported `worktree_outside_mirror`.

Keep it current: `git -C /rag/repos/ragstack.git remote update --prune`. The
`ragstack-ctl` ansible role clones it if it is absent (section 3, check-mode
safe: it stats first and the clone carries `creates:`).

### 5. node — `CTL_NODE_BIN` / `CTL_NPM_BIN`

`fleet artifact prepare` runs `npm ci` and `tenant create` runs `vite build`,
both by absolute path. The defaults are `/rag/tools/node/current/bin/{node,npm}`
— where the ansible role unpacks the pinned tarball. **That tree does not exist
on coconut yet.** The only node here is the operator's own:

```bash
ls -l ~wilke/.local/bin/node      # v26.7 today
```

So until the role's node block has run, set both explicitly in
`/rag/config/ctl/ctl.env` (or `host_vars`, which templates them):

```
CTL_NODE_BIN=/home/wilke/.local/bin/node
CTL_NPM_BIN=/home/wilke/.local/bin/npm
```

`CTL_MIRROR` and `CTL_NPM_CACHE` are templated alongside them and default to
`/rag/repos/ragstack.git` and `/rag/cache/npm`.

Note that `frontend/package.json` pins no `engines` range, so "which node" is
currently an operator decision rather than a checked one. Pinning it is the
follow-up; `node_version` in `group_vars/all.yml` is a placeholder until then.

### 6. The selftest, as `wilke`

`ragstack-ctl selftest` is the acceptance of everything above. It creates a
**sandbox** tenant — ports 26000–26099, name `ctltest-<stamp>` — ingests
`/rag/documents/test_api.md` into it, takes a fenced backup, stops it, restores
the bundle into a second sandbox, quarantines both and then proves the host is
clean. It touches no other tenant: every op it submits names a `ctltest-` name,
`registry.Allocate` never hands out a sandbox block, and `Allocate` ignores
sandbox rows and tombstones so the production index does not move.

It runs the engine **in this process**. There is no `--server`, deliberately: a
run pointed at a daemon would create tenants on that daemon's host and then
look for the evidence on this one.

```bash
cd ~/Development/ragstack                       # or wherever the checkout is
ragstack-ctl --direct fleet artifact prepare --tag $(git rev-parse HEAD)
ragstack-ctl fleet artifact list                # note the id

ragstack-ctl selftest                           # sqlite state, no gateway
ragstack-ctl selftest --postgres local          # the tenant's own postgres on +5
ragstack-ctl selftest --with-gateway            # publishes and drops a ctltest-* route
/rag/bin/verify.sh                           # must still say ALL GOOD
```

Run the first form **three times**; the plan's acceptance is three green runs
in a row, which is what catches a leftover the previous run did not clean up.

Exit codes: `0` everything green · `3` refused (no prepared artifact, a **red
doctor**, a `--boot` checklist with a FAIL) · `4` a job failed or a check FAILED.

What happens to the sandboxes at the end depends on WHICH of the two kinds of
exit-4 it was:

* **a JOB failed** — the run stops where it failed and leaves the sandbox in
  place for inspection (it may still be running, or half-decommissioned). It
  says so, and you remove it afterwards with `ragstack-ctl selftest --sweep`.
* **every job succeeded and a CHECK failed** — both sandboxes were
  decommissioned and quarantined before the check ran, so there is nothing live
  to look at, and they are **swept**. The FAIL is in the report and the exit
  code is still 4. (Leaving them behind used to exhaust the five sandbox blocks
  in three runs — which is exactly the acceptance below.)
* **`--keep`** — nothing is swept, whatever the outcome, and the run says how
  to remove what it kept.

```bash
ragstack-ctl selftest --sweep
```

`--sweep` is the only deletion the control plane performs, and it is guarded on
every axis at once. A directory is removed only if its name matches one of two
patterns **and** its resolved path (after `EvalSymlinks`) sits **directly**
under one of three roots:

| pattern | what it is |
|---|---|
| `^ctltest-[0-9a-z-]+\.quarantined-[0-9A-Za-z-]+$` | what `decommission` renames a sandbox's tree to |
| `^ctltest-[0-9a-z-]+$` — **only when no registry row of that name exists** | an orphan: a rolled-back create left the tree and no row. With a row it is a tenant, and the sweep refuses it |
| `^ctltest-[0-9a-z-]+\.failed-[0-9A-Za-z-]+$` | the tree a **rolled-back create or restore renames aside**, holding whatever the stores wrote. No row can ever name it (a registry name has no dot) |

The three roots are `/rag/data/tenants` (the data trees), `/rag/repos/tenants`
(the worktrees) and `/rag/backups/tenants` (a sandbox's own bundle directories).
Registry rows are removed only when the row's **port block** is in the sandbox
range AND its state is `quarantined`: a row named `ctltest-*` on a production
block is refused and reported rather than deleted, and a sandbox that is not
quarantined is refused with "decommission it first" — the sweep once deleted the
row of a live sandbox and orphaned its units.

A `.failed-` or orphan tree belonging to a PRODUCTION tenant is not sweepable by
any of these rules and never will be: removing one is an operator's own `rm -rf`
after looking at it.

Two things the selftest reports as named checks rather than as job failures,
because the job succeeds either way and the difference only shows up later:

* **`es stopped gracefully`** — the tail of
  `/rag/data/tenants/<t>/logs/es-<t>.log` has to end with a `stopped`/`closed`
  line. A JVM that was killed instead of stopping may not have flushed its
  translog, and a bundle taken next would be a bundle of that.
* **`es journal has no SIGKILL`** — `journalctl --user -u ragstack-<t>-es.service
  --since <run start>` must not contain `SIGKILL`. Systemd reports a unit
  "stopped" whether it exited or was killed after `TimeoutStopSec`.

Either check reports `n/a` when the evidence is not there to read (no ES log,
no journalctl), which is not the same as FAIL and does not fail the run.

The op-scoped doctor gates every job. coconut is permanently **yellow** (drift
on an adopted tenant, and so on), so the selftest quotes the doctor hash back —
exactly as an operator would with `--force-with-doctor-diff` — and records in
its report that it did. A **red** doctor is never forced: the run refuses with
exit 3 and names the hash, and that is a finding to act on before anything else
in PR-D is trusted.

### 7. The boot rehearsal

```bash
ragstack-ctl selftest --boot
```

Checklist only — it creates nothing. Exit `3` on any FAIL, because a host that
will not bring its tenants back is a host this command declines to certify.

| Check | What it reads | FAIL means |
|---|---|---|
| `linger` | `/var/lib/systemd/linger/<current user>` | item 1 above has not been done for this account |
| `user@ drop-in` | `/etc/systemd/system/user@<uid>.service.d/*.conf` | item 2: no `RequiresMountsFor=/rag`, or no `SYSTEMD_UNIT_PATH` |
| `<target>: is-enabled` | `systemctl --user is-enabled` per target | the registry says `desired_boot: enabled` and systemd says disabled — that tenant will not come back |
| `default.target pulls in every enabled tenant` | `systemctl --user list-dependencies default.target` | the symlink exists but the manager does not agree it is in the boot graph |

The drop-in row reports `n/a` for any account other than `svcbvbrc`: it is a
root item on the **daemon** account, and a FAIL against `wilke`'s own manager
would be a finding nobody can act on. Run it as `wilke` for the linger and
target rows; run it as `svcbvbrc` (`/rag/bin/ctl-as-svc.sh selftest --boot`)
for the drop-in row, where today it correctly reports linger and drop-in FAIL
until root has run the `coconut-host` role.

`--boot` reads the **live fleet**: it checks every tenant whose registry row
says `desired_boot: enabled`. A sandbox is never one of those for long —
`decommission` sets `desired_boot: disabled` before the run ends, so even
`selftest --keep && selftest --boot` shows no sandbox target, and the pairing
proves nothing. There is nothing to arrange here.

On a host whose adopted tenants are **hand-started** (the state coconut is in
today), no row says `desired_boot: enabled`, so the checklist is the first two
rows plus one that reads:

```
desired_boot targets   n/a   no tenant's row says desired_boot enabled, so there is nothing that should come back
```

That `n/a` is **not a gap and not a FAIL** — it exits 0 — it is the registry
saying that no tenant is claimed to come back by itself, which is true until the
tenants are handed over. The rows that matter today are `linger` and the `user@`
drop-in; the `is-enabled` and `default.target` rows start reporting the moment a
handed-over tenant's row says `desired_boot: enabled`.

### PR-D verification summary

| Item | Command | Expected |
|---|---|---|
| linger | `ls -l /var/lib/systemd/linger/svcbvbrc` | the file exists |
| drop-in | `sudo -u svcbvbrc XDG_RUNTIME_DIR=/run/user/10078 systemctl --user show -p UnitPath --value` | contains `/rag/config/ctl/units` |
| group | `stat -c '%U %G %a' /rag/data/tenants /rag/repos/tenants` | a small ops group, mode 277x — **open decision** |
| mirror | `git -C /rag/repos/ragstack.git rev-parse --verify HEAD^{commit}` | a 40-hex sha |
| node | `$CTL_NODE_BIN --version` | a version, from an absolute path |
| artifact | `ragstack-ctl fleet artifact list` | at least one prepared id |
| selftest | `ragstack-ctl selftest` ×3 | exit 0, every step `succeeded`, every check PASS or n/a |
| selftest | `ragstack-ctl selftest --postgres local` | exit 0 |
| selftest | `ragstack-ctl selftest --with-gateway` then `/rag/bin/verify.sh` | exit 0, then ALL GOOD |
| boot | `ragstack-ctl selftest --boot` | exit 0 once items 1–2 are done |
| no collateral | `ragstack-ctl doctor` · `jq .generation /rag/data/tenants/registry.json` | the five adopted tenants unchanged; the generation advanced only by the selftest's own writes; no new tombstones |

---

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
| 6 | `/rag/bin/verify.sh` | ALL GOOD |
| 7 | `ctl-daemon.sh status` | `running (pid …)` + `{"status":"ok",…}` |

**Exit codes.** Every step above is checkable in a script: `0` ok, `1` error,
`2` usage, `3` refused. `ctl-as-svc.sh` passes the ctl's status through
(`script -qec`); it did not before, so a failed step used to read as success.
Its output comes off a pty, so pipe it through `tr -d '\r'` before any
line-exact comparison.
