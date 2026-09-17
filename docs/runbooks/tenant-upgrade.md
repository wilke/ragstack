# Upgrading a tenant to a new tagged release

**This procedure is manual and operator-run.** There is no
`ragstack-ctl tenant update` verb — do not go looking for one. The control
plane's verb list is printed by `ragstack-ctl` with no arguments — the `usage`
block at `go/cmd/ragstack-ctl/main.go:71-232`. It lists
`tenant create|list|show|logs|start|restart|stop|backup|restore|decommission`
(`main.go:121-131`) and **no** `update`, `upgrade` or `deploy` verb. A grep of
the repo for `tenant update` returns nothing.

> **The usage block is not the complete verb list.** `tenant rebase-worktree`
> exists and is dispatched (`main.go:1054`, implemented in
> `go/cmd/ragstack-ctl/rebase.go`) with its own usage line at `main.go:1037`,
> but it is **absent from the top-level `usage()` block** — so `ragstack-ctl`
> with no arguments does not mention it. That is a gap in `usage()`, not a
> missing command. Read the block as the printed summary, not as the complete
> verb list.

So moving a tenant to a new tag is five steps you run yourself, and one `adopt
--readopt` at the end so the registry stops describing the code that *used* to
be there. The recipe below is what was actually performed on 2026-09-15, twice:
first for the **v1.6.0** upgrade of `hackathon` and `dev`
(`/rag/backups/tenants/*/20260915T11*-pre-v1.6.0/`), then for the **v1.6.1**
upgrade of the same two (`/rag/backups/tenants/*/20260915T12*-pre-v1.6.1/`).
Those backup trees are the worked example; **`v1.6.1` is where the fleet is
now**, so that is the tag the commands below use.

> **Tracked as a future control-plane feature.** The brief this runbook was
> written from names a "PR-F / v1.1" for the `tenant update` verb.
> **Unverified:** no `PR-F` appears anywhere in this repository. What *is* in
> the repo is PR-E — the **handover**, after which `svcbvbrc` owns the tenant
> units (`docs/runbooks/ctl-deploy.md:1135`, `ctl-quickstart.md:143`). Treat
> the automated upgrade as "not scheduled in a document I can cite".

---

### How to read a citation

Code references are `file:line` against the tree at the tag named above.

- A path starting `python/`, `go/`, `ops/coconut/`, `frontend/` or `contracts/`
  is relative to the **repository root** — `python/ragstack/tenancy.py:36`. This
  form is always unambiguous and is the one to prefer.
- Any other path is relative to **`python/ragstack/`** — `api/security.py:990`,
  `ops/evict.py:158` (which is `python/ragstack/ops/evict.py`, not the repo's
  `ops/`).
- A **bare filename** — `collections.py:1569` — means the file the surrounding
  section is about.
- **Documentation is cited too**, and differently: a bare `ctl-quickstart.md:143`
  is a sibling in `docs/runbooks/`, while anything else is repo-root
  (`CLAUDE.md:102`, `docs/runbooks/ctl-deploy.md:1135`). Prose renumbers far
  faster than code, so read a doc line number as a hint and the surrounding
  heading as the real anchor.

That last form is the one to watch. Several of these basenames exist more than
once in the tree (`collections.py` and `documents.py` both do), so a bare
citation resolves from its section, not from a search. **Anything checking these
mechanically needs that rule, or it will resolve to the wrong file and report
correct citations as broken** — which is worse than not checking, because the
next person edits good citations to satisfy a bad resolver. If such a check is
ever written, qualify the citations first or give the checker the convention.

One exception is called out where it appears: `/rag/config/proxy/snippets/…` is a
**live-host path**, not a repo file. Nothing here can pin it, and it drifts with
the gateway.

## Two rules before anything starts

### Never stop a service by process-name pattern

Verbatim from this repo's `CLAUDE.md:102`:

> `pkill -f "uvicorn …"` once took down every API on the host — production runs
> the same command line as every scratch server (#402). Stop by the pid
> recorded at launch […], or resolve by port and verify `/proc/<pid>/cwd`
> first. […] `pgrep` returning nothing is a fleet-wide alarm, not proof your
> cleanup worked.

This is not a style preference. Every tenant API on coconut runs the *identical*
argv — `/rag/envs/ragstack/bin/python -m uvicorn ragstack.api.main:app --host
0.0.0.0 --port <port>` — differing only in `--port` and in `cwd`. A pattern that
matches one matches all five, plus any scratch server an agent left running.
`ops/coconut/restore.sh` states the same prohibition in its own header
(`ops/coconut/restore.sh:38`, "kill by process-name pattern, ever (MEMORY: #402)")
and is built around `record_pid_by_port` (`ops/coconut/restore.sh:129-134`) for
exactly this reason.

**Stop a tenant API like this:**

```bash
T=hackathon; D=/rag/data/tenants/$T
PID=$(cat "$D/api-$T.pid")
ls -l /proc/$PID/cwd          # MUST be /rag/repos/tenants/$T/python — stop if it is not
tr '\0' ' ' < /proc/$PID/cmdline; echo    # MUST name this tenant's port
kill "$PID"
```

If the pid file is stale, resolve by port instead and verify `cwd` the same way
before killing anything:

```bash
ss -ltnpH | awk '$4 ~ /:24080$/' | grep -o 'pid=[0-9]*'
```

### Write the plan first

`CLAUDE.md:101`: *"any operation that touches live infrastructure gets a Fable
plan before it starts."* A tenant upgrade stops a live API, replaces the code
under it and rewrites a registry row — it is squarely in scope. Write the plan,
including which tenant, which tag, the rollback tag, and who is using the tenant
right now. Do not start from this runbook alone.

---

## 0. Establish the tenant's UI mode — this changes the procedure

A tenant is a **static-UI** tenant if nginx serves a built bundle off disk, and
a **dev-UI** tenant if nginx proxies to a running Vite dev server. The test is
whether the tenant has a row in the `$tenant_ui` map:

```bash
sed -n '/map \$tenant \$tenant_ui/,/^}/p' /rag/config/proxy/conf.d/05-tenants.generated.conf
# NOTE: that path is a symlink into the ctl-generated gateway tree
#   conf.d/05-tenants.generated.conf -> /rag/data/ctl/gateway/current/conf.d/...
# It is generated from the registry. Read it; never hand-edit it.
```

**Present in the map → dev UI.** **Absent → static UI**, and it will instead
have an `alias <data_dir>/ui/dist/` block in
`/rag/config/proxy/snippets/tenants-ui-static.generated.conf`. In the generated
map in use today — rendered from registry generation 198, which is what its own
header records — `hackathon` is the only absent one; it is static. `dev`,
`demo`, `lucid-next` and `asm-next` are all in the map and are dev-UI. The
registry says the same thing in `tenants.<name>.ui.mode`
(`static|dev|external`, written by `adopt --ui-mode`,
`go/cmd/ragstack-ctl/main.go:89-95`).

**Why it matters:** a static-UI tenant serves a *frozen build*. Checking the
worktree out at a new tag changes nothing a browser can see until you rebuild
and rsync (step 3). A dev-UI tenant's Vite server reads the worktree live, so
it picks the new code up on restart and needs no build step — but its dev
server is a separate process with its own pid, and restarting the API does not
restart it.

Both generated files are **owned by `ragstack-ctl` and must never be
hand-edited** — they say so in their own headers. Nothing in this procedure
edits them; `adopt --readopt` in step 5 changes the registry, and the next
`gateway render`/`apply` regenerates them.

---

## 1. Back up `state/` and `tenant.env` — first, always

The convention already on disk, which this runbook adopts:

```
/rag/backups/tenants/<tenant>/<UTC>-pre-<tag>/
  ├── config/          # the whole config dir: tenant.env, secrets.env, provision.env, prompt-templates.yaml
  ├── state/           # the sqlite state: ragstack_collections.db, ragstack_jobs.db, ragstack_users.db
  ├── ui-dist/         # static-UI tenants only: the bundle you are about to replace
  └── worktree-sha     # `git -C <worktree> rev-parse HEAD` BEFORE the checkout — this is your rollback target
```

```bash
T=hackathon; D=/rag/data/tenants/$T; W=/rag/repos/tenants/$T
TAG=v1.6.1
B=/rag/backups/tenants/$T/$(date -u +%Y%m%dT%H%M%SZ)-pre-$TAG
mkdir -p "$B"
cp -a "$D/config" "$B/config"
cp -a "$D/state"  "$B/state"
git -C "$W" rev-parse HEAD > "$B/worktree-sha"
# static-UI tenants only:
cp -a "$D/ui/dist" "$B/ui-dist"
```

**Expect:** `worktree-sha` holds a 40-hex sha and `state/` holds three `.db`
files. **`worktree-sha` is the single most important file here** — it is what
step "Rollback" checks back out, and it is recorded *before* the checkout
because afterwards the old sha is only recoverable from the reflog.

A tenant whose `COLLECTION_STORE_BACKEND` / `USER_STORE_BACKEND` /
`JOB_STORE_BACKEND` are `postgres` rather than `sqlite` keeps that state in its
Postgres instance, not in `state/` — `hackathon` is such a tenant
(`registry.json` → `tenants.hackathon.settings`), and its `state/*.db` files are
leftovers from provisioning, last written 2026-09-14. **Copying `state/` is
still correct and still cheap, but for a Postgres-backed tenant it is not the
backup of record.** Use `ragstack-ctl tenant backup <t> --fence` for that where
the tenant is eligible — see the caveat in §"What the ctl verbs do and do not
cover".

## 2. Move the worktree to the tag

```bash
git -C "$W" fetch --tags
git -C "$W" checkout --detach "$TAG"
git -C "$W" describe --tags     # Expect: exactly $TAG
git -C "$W" status --short      # Expect: empty. A dirty tenant worktree is a stop-and-investigate.
```

Tenant worktrees are **always detached** — `git status -sb` reads
`## HEAD (no branch)` on all five. That is the intended state; a tenant sitting
on a branch would silently move under you on someone else's `git pull`.

## 3. Rebuild the UI — static-UI tenants only

Skip this whole step for a dev-UI tenant.

```bash
cd "$W/frontend"
npm ci
npx vite build --base "/ragstack/$T/ui/"
rsync -a --delete --chmod=D770,F660 dist/ "$D/ui/dist/"
```

Three things that are easy to get wrong:

- **`--base` is mandatory and is not in the config.** `frontend/vite.config.ts`
  sets no `base`, so a build without the flag emits asset URLs rooted at `/` —
  which 404 at the gateway. `/rag/config/proxy/snippets/routes.conf:441-446`
  spells the failure out (it is written about the Vite dev server's `--base`,
  but the broken-asset-URL consequence is identical for a static build). Verify afterwards that `dist/index.html` references
  `/ragstack/<t>/ui/assets/…` and not `/assets/…`.
- **`npx vite build`, not `npm run build`.** The package script is
  `tsc --noEmit && vite build` (`frontend/package.json`), which gives you no
  clean way to pass `--base` and additionally gates the deploy on a full
  typecheck. Use the direct `npx` form; run `npm run typecheck` separately if
  you want the check.
- **Use the shared toolchain at `/rag/tools/node/current/bin`** (node v26.7.0,
  installed by `make install-node`, added in PR-D2 / #554). That is what the ctl
  and the v1.6.x tenant upgrades used. Put it on `PATH` rather than relying on a
  login shell picking up an nvm install under `$HOME`.
- `ops/coconut/restore.sh:158-159` still resolves `npx` from `$HOME` (nvm /
  `~/.local`) in its preflight and tells you to re-run from a login shell, so
  that check — not the shared toolchain — is the thing that wants one.

- **`--chmod=D770,F660`.** A plain `rsync -a` preserves the build's 644/755
  modes; the tree's convention is 2770 dirs and 660 files. The directory ACLs
  make nginx work either way, so this is about matching the tree, not about
  whether the UI serves.

The destination is the `alias` path nginx already serves:
`alias /rag/data/tenants/hackathon/ui/dist/;` in
`snippets/tenants-ui-static.generated.conf`. No nginx reload is needed for a
content-only swap.

## 4. Check whether the release wants new `tenant.env` keys

A tagged release can add a setting whose absence is silent. **v1.6.0 did**: the
`hackathon` upgrade added `PROMPT_TEMPLATES_FILE` plus a new
`config/prompt-templates.yaml` (ADR-0008 named prompt templates). Diffing the
key *names* against the backup is the cheap check:

```bash
diff <(sed 's/=.*//' "$B/config/tenant.env" | sort) \
     <(sed 's/=.*//' "$D/config/tenant.env" | sort)
```

Read the release's `CHANGELOG`/release notes for required keys. The API also
fails fast on some classes of stale config rather than starting wrong — see
`docs/runbooks/upgrade-407-remove-gowe-store-urls.md` for a release that
*refuses to boot* until an inert key is removed.

## 5. Restart the API by the recipe in `ops/coconut/restore.sh`

The canonical launch is `restore.sh`'s `apis` group. Since **`eb9803f`**
(PR #559) that group covers every registry tenant including `hackathon`, and
exports `RAGSTACK_GIT_TAG` / `RAGSTACK_GIT_SHA` from each tenant's own worktree
on every launch — so a script-started API and a hand-started one report the same
thing at `/v1/version`.

> Before `eb9803f` neither was true: the `apis` loop was a literal list of four
> with `hackathon` absent, and nothing under `ops/` set the version variables.
> If you are working on a checkout older than that commit, both caveats apply and
> you must launch `hackathon` by hand.

When you do launch by hand, still export the two variables. Omitting them makes
`/v1/version` fall back to `git describe` / `git rev-parse` in the worktree
(`python/ragstack/version.py:186-196`) — usually the right answer, but no longer
the supervisor's authoritative word about *what was launched*.

Stop the old process by its recorded pid (see "Never stop a service by
process-name pattern" above), then:

```bash
T=hackathon; D=/rag/data/tenants/$T; PORT=24080
CODE=/rag/repos/tenants/$T/python
( set -a
  . "$D/config/tenant.env"
  [ -f "$D/config/secrets.env" ] && . "$D/config/secrets.env"
  set +a
  export HF_HOME=/rag/cache PYTHONPATH=$CODE
  export RAGSTACK_GIT_TAG=$(git -C "/rag/repos/tenants/$T" describe --tags)
  export RAGSTACK_GIT_SHA=$(git -C "/rag/repos/tenants/$T" rev-parse HEAD)
  cd "$CODE" && exec setsid nohup /rag/envs/ragstack/bin/python -m uvicorn \
      ragstack.api.main:app --host 0.0.0.0 --port "$PORT" \
      >> "$D/logs/api-$T.log" 2>&1 < /dev/null ) &
```

Then **record the pid of the process that owns the port, not of the launcher
subshell** — `$!` here is the wrapper, which is `restore.sh`'s own documented
bug-avoidance note (`ops/coconut/restore.sh:114-115`, above `launch()`; the
recording itself is `record_pid_by_port`, `:129-134`):

```bash
sleep 10
ss -ltnpH | awk -v p="$PORT" '$4 ~ "[:.]"p"$"' | grep -o 'pid=[0-9]*' | cut -d= -f2 \
  > "$D/api-$T.pid"
cat "$D/api-$T.pid"; ls -l /proc/$(cat "$D/api-$T.pid")/cwd
```

**Expect:** `cwd` is `/rag/repos/tenants/<t>/python` and
`curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/health` is `200`.
The pidfile path the registry expects is
`tenants.<name>.api.pidfile` — for `hackathon`,
`/rag/data/tenants/hackathon/api-hackathon.pid`.

**Dev-UI tenants:** the Vite dev server is a separate process and is *not*
restarted by any of the above. It reads the worktree live, so after step 2 it
will hot-reload or want a restart of its own; restart it with
`/rag/config/proxy/ui-dev.sh <tenant> <port> <worktree>/frontend`, the same
launcher `ops/coconut/restore.sh:344` uses.

## 6. Re-adopt so the registry records the new code

```bash
/rag/bin/ragstack-ctl adopt hackathon \
  --data-dir /rag/data/tenants/hackathon \
  --worktree /rag/repos/tenants/hackathon \
  --ui-mode static \
  --readopt --preview          # look at the findings FIRST
```

Then swap `--preview` for `--commit`. `adopt` takes exactly one of
`--preview` / `--commit` (`go/cmd/ragstack-ctl/main.go:741-744`) — there is no
"dry run then apply" flag pair.

Flags that matter (`go/cmd/ragstack-ctl/main.go:714-750`):

| flag | meaning |
|---|---|
| `--data-dir D` | required — the tenant's data tree |
| `--worktree W` | required — the checkout its API runs from |
| `--manifest-name M` | the `manifest.tsv` row / data-dir basename, when it differs from the name (`asm-next` → `asm`, `lucid-next` → `lucid`) |
| `--ui-mode static\|dev\|external` | defaults to `dev` when `--ui-port` is given, `external` when it is not. `static` takes **no** `--ui-port` — the pair is a usage error, and the preview raises `ui_dist_missing` if `<data-dir>/ui/dist` is not there |
| `--ui-port P` | the Vite dev server's port — dev-UI tenants only |
| `--readopt` | **required for an upgrade.** Replaces the row of a tenant already in the registry, keeping `adopted_at`, `desired_boot`, the rollback descriptor and the last ops/backup — and, on a **ctl-supervised** row (`supervisor` not `manual`), its `owner`, `supervisor`, `state`, `handover` block, `api.pidfile` and confirmed store capabilities as well, whoever runs the command. Without it, adopting an already-adopted tenant is an error |
| `--force` | commit despite error-level findings — read every finding before reaching for this. It does **not** get past the refusal for a tenant the control plane already runs (`this tenant is already run by the control plane; use --readopt`) |

Build the UI **before** you re-adopt a static tenant: the preview checks that
the dist exists.

---

## Verify the upgrade landed

Four independent checks. Do all four — each can pass while another fails.

**1. `/v1/version` reports the tag and sha you deployed.** This endpoint was
added post-`v1.5.3` (commit `eb41858`, PR-A / #531) and **first ships in
`v1.6.0`** — `git tag --contains eb41858` returns `v1.6.0` and `v1.6.1`, and no
earlier tag. It needs *a* credential but not an admin one
(`python/ragstack/api/routers/version.py:35-36`,
mounted with `Depends(resolve_tenant)` at `python/ragstack/api/main.py:209`):
unauthenticated it answers `401 {"detail":"missing or invalid API key"}` on a
v1.6.x tenant, and `404 {"detail":"Not Found"}` on a v1.5.3 one. **That
difference is itself the check** — a 404 after an upgrade to v1.6.x means the
process is still running the old code.

```bash
curl -s -H "X-API-Key: $ADMIN_KEY" http://127.0.0.1:24080/v1/version
```

Precedence: the supervisor's `RAGSTACK_GIT_TAG` / `RAGSTACK_GIT_SHA` win over
the worktree's own git state (`python/ragstack/version.py:9`, `:186-196`), and
an env var present-but-empty is treated as absent. So this endpoint reports
*what was launched*, which is exactly the question an upgrade raises.

**2. The process is running the new code.** Belt and braces, because #1 can be
answered by env vars:

```bash
PID=$(cat /rag/data/tenants/$T/api-$T.pid)
ls -l /proc/$PID/cwd                       # the tenant's own python dir
git -C /rag/repos/tenants/$T describe --tags   # the tag
```

**3. The registry's `code.tag`.**

```bash
python3 -c 'import json;print(json.load(open("/rag/data/tenants/registry.json"))["tenants"]["'"$T"'"]["code"])'
/rag/bin/ragstack-ctl tenant show "$T"
```

**Expect:** `code.tag` is the new tag. Note that `code.sha` is `null` for every
tenant in the current registry — do not read a null there as a failure.

**4. Health, through the API and through the gateway.**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:24080/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9000/ragstack/$T/api/v1/collections?counts=false
curl -sI http://127.0.0.1:9000/ragstack/$T/ui/ | head -1   # static-UI tenants
```

For a static-UI tenant also confirm the bundle is the new one:
`grep -o '/ragstack/[^"]*\.js' /rag/data/tenants/$T/ui/dist/index.html` — the
hashed filename must have changed from the copy in `$B/ui-dist`.

For anything deeper than "is it up", `GET /v1/health/deep` and
`GET /v1/stats/stores` are the per-leg views — see
[`tenant-admin.md`](tenant-admin.md).

## Rollback

Rollback is the same five steps run backwards against `worktree-sha`. There is
no ctl rollback verb for an adopted tenant.

```bash
OLD=$(cat "$B/worktree-sha")
git -C "$W" checkout --detach "$OLD"
# static UI: rebuild and re-rsync — the dist does NOT roll back with the worktree
cd "$W/frontend" && npm ci && npx vite build --base "/ragstack/$T/ui/" \
  && rsync -a --delete --chmod=D770,F660 dist/ "$D/ui/dist/"
#   (or restore the saved bundle directly: rsync -a --delete "$B/ui-dist/" "$D/ui/dist/")
# restart by recorded pid, with RAGSTACK_GIT_TAG/SHA re-derived from $OLD
# re-adopt: ragstack-ctl adopt <t> … --readopt --commit
```

**If the release changed `tenant.env`, put the old file back too** — a v1.6.0
`tenant.env` pointed at v1.5.3 code is the failure mode
`upgrade-407-remove-gowe-store-urls.md` describes from the other direction:
config that is inert while an operator believes it is live.

**State does not roll back.** Nothing in this procedure migrates the tenant's
collections/users/jobs, so a rollback is only clean if the release did not
change their schema. `registry.json` marks prepared artifacts with
`schema_compatible` — both entries currently read `false`. **Unverified:**
I did not trace what consumes that flag, so do not read `false` as a
prohibition on rolling back; verify it against the release notes.

---

## Current fleet state

Verified 2026-09-15 against `/rag/data/tenants/registry.json` (generation 205),
the five tenant worktrees, `05-tenants.generated.conf` (generation 198), and a
live `/health` + `/v1/version` probe of each API. **Re-verify before you rely on
it — this table drifts with every upgrade.**

| Tenant | Registry `code.tag` | Worktree HEAD | API port | UI | Data dir |
|---|---|---|---|---|---|
| `hackathon` | `v1.6.1` | `4ea2e38` (= `v1.6.1`) | 24080 | **static** (`<data_dir>/ui/dist`) | `/rag/data/tenants/hackathon` |
| `dev` | `v1.6.1` | `4ea2e38` (= `v1.6.1`) | 24040 | dev, Vite `:8090` | `/rag/data/tenants/dev` |
| `asm-next` | `v1.6.1` | `4ea2e38` (= `v1.6.1`) | 24020 | dev, Vite `:5212` | `/rag/data/tenants/asm` |
| `demo` | `v1.5.3` | `652be18` (= `v1.5.3`) | 24060 | dev, Vite `:5210` | `/rag/data/tenants/demo` |
| `lucid-next` | `v1.5.3` | `652be18` (= `v1.5.3`) | 24000 | dev, Vite `:5211` | `/rag/data/tenants/lucid` |

All five answered `/health` `200` at the time of writing. `/v1/version` answered
`401` (i.e. the route exists) on `hackathon`, `dev` and `asm-next`, and `404`
(the route does not exist) on `demo` and `lucid-next` — which is exactly the
v1.6.x / v1.5.3 split.

**Note the data-dir names.** `asm-next` → `/rag/data/tenants/asm` and
`lucid-next` → `/rag/data/tenants/lucid`: the data dirs **drop the `-next`
suffix**, which is what `--manifest-name` exists for. Getting this wrong points
an upgrade at the wrong tenant's state.

### About `asm-next`

`asm-next` ran on `2f0bafc` — **`v1.6.0`'s immediate parent**, not a separate line
of development — until it was moved to **`v1.6.1` (`4ea2e38`)** and re-adopted on
2026-09-15. All three of `dev`, `hackathon` and `asm-next` are now on `v1.6.1`
with the v2 templates; `demo` and `lucid-next` remain on `v1.5.3`.

The detail is worth keeping because it is the shape of every "is this tenant
current?" question:

- While it sat on `2f0bafc` the registry recorded `code.tag` as
  `v1.5.3-60-g2f0bafc` (a `git describe`), **not** the literal string `main` —
  a registry tag only becomes a release tag when the worktree is checked out at
  that tag and re-adopted.
- **The API code was already identical.** `git diff --stat 2f0bafc f779d0c --
  python/` is empty; the whole v1.6.0 delta was the ctl control plane
  (`go/internal/ctl/**`), `contracts/ctl/**`, `ops/`, `docs/`, `conformance/`,
  `Makefile`, and **one** frontend file — `frontend/src/admin/api/ctlSchema.d.ts`,
  generated typings for the ctl admin UI that no tenant UI imports.
- So "functionally current" and "reads as current in the registry" are different
  claims. Check `code.tag`, not just behaviour.

---

## Reboot recovery

The host reboot scripts cover every registry tenant, including `hackathon`, as of
**`eb9803f`** (PR #559, installed to `/rag/bin` with `make install-ops`).

Before that commit they did not, and a reboot would have returned every tenant
*except* the one the hackathon runs on — with nothing to indicate why. What the fix
put in place:

- `restore.sh` starts hackathon's three stores through the tenant's generated
  `bin/up.sh`, and waits on `:24081`, `:24083` and `pg_isready :24085`. The Postgres
  wait is **fatal**: that instance holds the tenant's users, ACL rows and collection
  registry, so an API without it has no authorization store.
- `hackathon` is in both API loops, and deliberately **not** in the UI loops — its UI
  is a static build nginx serves from `/rag/data/tenants/hackathon/ui/dist`, so there
  is no dev server to start.
- Every tenant API launch now exports `RAGSTACK_GIT_TAG` / `RAGSTACK_GIT_SHA` from its
  own worktree, so `/v1/version` stays truthful after a `restore.sh` restart. It did
  not before, which is why a hand-started API and a script-started one could report
  differently.
- `pre-reboot.sh` stops the hackathon API (pidfile plus cwd check) and its three stores
  ahead of the shared ones, so its "no apptainer instances left" check is no longer
  reporting success while they are still up.
- `snapshot.sh` lists the two hackathon HTTP stores and the API health row.
  `verify.sh` needed no change.

Check the loops include what you expect:

```bash
/rag/bin/restore.sh --dry-run --only apis     # the hackathon row is listed
```

A regression test fails if a registry tenant is missing from those loops:
`python/tests/unit/test_coconut_reboot_tenants.py`.

> These lists are **interim**. The ctl registry is the source of truth; the scripts
> stop carrying hand-maintained tenant lists at PR-E.

## What the ctl verbs do and do not cover

`ragstack-ctl` has real lifecycle verbs — `tenant create --artifact ID`,
`tenant backup <t> [--fence] [--tar]`, `tenant restore <t> --from <bundle-id>
--as <fresh-tenant>`, `tenant decommission <t>`
(`go/cmd/ragstack-ctl/main.go:121-131`). **They apply to tenants the ctl
created.** Every tenant on this host today was *adopted* — read into a registry
row from a hand-started deployment — and `registry.json` records
`supervisor: "manual"` for all five. Adopted tenants get handover in **PR-E**
(`docs/runbooks/ctl-deploy.md:1135`, `ctl-quickstart.md:143`); until then their
units are not the ctl's to drive, which is why this runbook restarts the API by
hand rather than with `tenant restart`.

Two consequences worth stating plainly:

- `tenant backup --fence` is the verified backup — `backup verify` re-hashes
  against `SHA256SUMS`, and the deep check is a `tenant restore --as` into a
  fresh tenant (`go/cmd/ragstack-ctl/main.go:182-183`). The `cp -a` in step 1 is
  an *unfenced* copy of a live tree. It is the right tool for a five-minute
  code-only upgrade and the wrong one for anything that touches data.
- `adopt` / `adopt-all` / `doctor` / `fleet status` / `tenant list|show|logs`
  are read-or-registry-only and are safe against an adopted tenant
  (`go/cmd/ragstack-ctl/main.go:650`).

For adopt and deploy detail — installing the binary, the `svcbvbrc` wrapper,
gateway generations, and what each `adopt` finding means — read
[`ctl-quickstart.md`](ctl-quickstart.md) (the eight-step short form) and
[`ctl-deploy.md`](ctl-deploy.md) (the full runbook). This runbook deliberately
does not restate them.

## Related

- [`tenant-admin.md`](tenant-admin.md) — running the tenant after the upgrade:
  roles, shares, quotas, diagnosing a user report.
- [`upgrade-407-remove-gowe-store-urls.md`](upgrade-407-remove-gowe-store-urls.md)
  — a worked example of a release that needs a `tenant.env` edit in the same
  deploy, and refuses to boot without it.
- [`tracing-a-503.md`](tracing-a-503.md) — when the post-upgrade smoke test
  fails with a 503 and you need to say which leg.
- `ops/coconut/restore.sh` — the launch recipes, and the post-reboot procedure
  this upgrade's restart step borrows from.
