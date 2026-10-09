# Upgrading a tenant

There are two procedures, and which one applies is a fact about the tenant's
registry row, not a choice:

| the row says | the tenant is | upgrade with |
|---|---|---|
| `supervisor: instance` (ctl-run) | started and stopped by the control plane | **Part 1** — `ragstack-ctl tenant update-code`, one job |
| `supervisor: manual` (hand-run) | started by an operator (`ops/coconut/restore.sh`) | **Part 2** — the manual procedure; or hand it over first ([`ctl-handover.md`](ctl-handover.md)) and use Part 1 |
| `supervisor: systemd` | units the ctl renders | not supported by `update-code` (a server image runs as an apptainer instance, and there are no units for one): `ragstack-ctl tenant set-supervisor <t> instance` first, or Part 2 |

`ragstack-ctl --json tenant show <t>` prints the row; `fleet status` shows
the API mode (`worktree` or `image`) and the server image per tenant.

---

## Part 1 — `tenant update-code` (ctl-run tenants)

`update-code` moves a ctl-run tenant's API onto a **prepared server image**
in one job (PR-F F5, brief `plan-tenant-control-plane-2026-09-10.pr-f.md`
§1.3). A tenant whose API still runs from its worktree (`api_mode:
worktree`) is **migrated** by the same op: afterwards its API is the
apptainer instance `api-<manifest>` of the image. A tenant already in image
mode is moved from one image to another.

### D6 — the deploy order (binding)

The new fields an image-mode registry carries (`fleet.server_images`,
`tenant.server_image`, `code.previous_image`) cannot be read by an older
ctl binary: `registry.Load` refuses unknown fields. The **first** `fleet
image prepare` is therefore a one-way door. In order:

1. Build, install and restart the new ctl (`make install-ctl`, the daemon
   restart in [`ctl-deploy.md`](ctl-deploy.md)), then run **both**
   selftests as the ctl account, on coconut, and see them green:
   ```bash
   ragstack-ctl selftest --supervisor instance
   ragstack-ctl selftest --supervisor instance --update-code <image-b1> --update-code <image-b2>
   ```
   The second one creates a worktree sandbox, migrates it onto `<image-b1>`
   and moves it on to `<image-b2>` (each leg proved: the row, the
   `api-<sandbox>` instance holding the port, `/v1/version`), then runs the
   whole backup/stop/restore/decommission/purge sequence on the image-mode
   sandbox. It needs the two images prepared — which is why it comes after
   step 2 the first time. Until step 2, run the first selftest only.
2. Only then build the server image ([`server-image.md`](server-image.md))
   and admit it: `ragstack-ctl --direct fleet image prepare --sif <path>`.
3. Upgrade **dev** first, then hackathon after a soak (plan decision D4).

Rolling the ctl back after step 2 means removing the new fields from
`registry.json` by hand. Do not plan on it.

### Before you start

```bash
T=dev
IMAGE=ragstack-server-v1.6.6-b1.sif
ragstack-ctl fleet image list                 # the image is prepared (name, version, commit)
ragstack-ctl fleet artifact list              # static UI: an artifact AT THE IMAGE'S COMMIT
ragstack-ctl doctor $T --op update-code       # must not be red
```

The doctor gate for `update-code` (`doctor/preconditions.go`) is red on:
`env_not_systemd_parsable`, `port_not_listening` (a tenant whose API is DOWN
is repaired by `tenant start`, never by an upgrade), `worktree_outside_mirror`,
`worktree_gitdir_unreadable`, and — for an image-mode tenant —
`server_image_missing` / `server_image_mismatch` of its CURRENT image (that
image is what a failed upgrade's rollback starts again).

The plan refuses, from the registry alone: a hand-started tenant or one with a
handover in flight; a tenant that is not `active`; an image that is not
prepared; a static UI with no `--artifact`, or an artifact whose commit is not
the image's; `--artifact` with `--no-rebuild-ui`; and the tenant's own
image again without a UI rebuild (that is `tenant restart <t> --only api`).
An env file that defines `PYTHONPATH`, `PATH`, `LD_*`, `APPTAINER*`,
`PREPEND_PATH` or `APPEND_PATH` is refused for an image row (it would hijack
the container) — remove the key first.

### Run it

```bash
# a static-UI tenant: the UI is rebuilt from the artifact, at the image's commit
ragstack-ctl tenant update-code $T --image $IMAGE --artifact <artifact-id> --dry-run
ragstack-ctl tenant update-code $T --image $IMAGE --artifact <artifact-id> --yes-destructive $T --wait

# an API-only patch (or an external/dev UI): the served UI is left alone
ragstack-ctl tenant update-code $T --image $IMAGE --no-rebuild-ui --yes-destructive $T --wait
```

Read the dry run: it is the approval document. Over HTTP it is
`POST /v1/tenants/<t>/ops/update-code` with `args: {image, rebuild_ui?,
artifact_id?}` (`x-ctl-op-args.update-code`); `rebuild_ui` absent means
"rebuild when `ui.mode` is static". The op never runs `npm ci` — the UI is
built from the artifact's own `node_modules`, as `create` does — which is
why it can be an HTTP verb.

### What the job does, in order

| # | step | kind | rollback |
|---|---|---|---|
| 1 | a LIGHT bundle, kind `pre-update` (config + sealed secrets + SQLite state, no store snapshots, no fence; the backup planner's own steps) → `last_backup` | backup steps | the backup's own (the bundle goes back to `<id>.partial`, `last_backup` is restored) |
| 2 | prove the image: file sha256 == the prepared record; `apptainer inspect --labels` == the receipt (version, commit, build, role server); the commit resolves in the mirror to itself | probe | none (changes nothing) |
| 3 | (`rebuild_ui`) `vite build` from the artifact's worktree into `ui/dist.building` — the slow part, while the API still serves; nothing that is served changes | build | the staged build is left in `dist.building` |
| 4 | refuse over a running ingest job; stop the API with the CURRENT launch (worktree: pidfile, TERM, port free; image: `apptainer instance stop api-<m>`, port free) | probe, proc/instance | starts that same launch again and waits for it — on the tree it started from, which steps 5–6 have already put back |
| 5 | check the worktree out at the image's commit — always, so `gowe render`, `env`, drift and the next static build read the code the API runs; the previous HEAD is checkpointed; a dirty tree is refused | git | checks the previous HEAD out again |
| 6 | (`rebuild_ui`) one rename pair: `dist` → `dist.prev-<ts>`, `dist.building` → `dist` (the steps `set-ui-mode static` uses) | fs | swaps back: the new build returns to `dist.building`, the previous one to `dist` |
| 7 | registry: `server_image` ← the record, `code.tag/sha` ← its version/commit, `code.previous_image` ← the old image (if any), `artifact_id` ← `--artifact` (old → `code.previous_artifact_id`). **Moves the registry generation.** | registry | restores the four fields from the checkpoint |
| 8 | start the API instance `api-<m>` from the image (labels and sha256 proved again, binds derived from tenant.env, environment as `APPTAINERENV_*` only) | instance | stops it |
| 9 | post-checks: the instance holds the port; `/health` 200; `/v1/health/deep` 200 (the tenant's own admin key, read at run time); `/v1/version` `git_sha` == the image's full commit and `version` == pep440 of its version (`v1.6.6` → `1.6.6`); then, with `rebuild_ui` and when the live gateway routes the tenant, `GET /ragstack/<t>/ui/` = 200 | probe | none — a failure here unwinds 8…1 |
| 10 | registry: `last_ops.update-code`, `restart_pending = false` | registry | — |

**Nothing the running API reads changes on disk while it runs.** A worktree
API imports modules lazily (graph extraction, restore, the tool-image check
load on first use), so the checkout (5) and the dist swap (6) come only after
the stop (4); the slow UI build (3) comes before it, so the downtime is the
checkout, a rename, and the new instance's start — seconds to a minute,
mostly the image's import time.

### When it fails

**Inside the job** (a step returns an error, the daemon is alive): the engine
rolls back in reverse. You end with the old UI served, the old image recorded
(or, for a migration, a worktree-mode row again), the worktree at its
previous HEAD, and **the OLD API running** — the worktree uvicorn for a
migrating tenant, the old `api-<m>` instance otherwise. The order makes the
last point safe: steps 6 and 5 roll back (UI, then worktree) before step 4's
rollback restarts the old API, so it starts on exactly the tree it was
stopped on. The job is `rolled_back`; `ragstack-ctl job show <id>` says which
step failed and why. The pre-update bundle is left as `<id>.partial` (its
finalize step rolls back with the rest); it is still readable.

**Interrupted** (the daemon died mid-job): the engine resumes a job only when
its re-plan has the same hash, and the plan hash includes
`registry_generation` (`jobs/hash.go`, refused at `jobs/engine.go`'s
rebuild). Step 1's `last_backup` record already moves the generation, so in
practice an interrupted upgrade is **not resumed** — it is recovered by hand,
and where it stopped decides how (`ragstack-ctl job show <id>`):

| stopped in | the row names | what is running | recover with |
|---|---|---|---|
| steps 1–3 | the old code | the old API, untouched | nothing the API reads moved: run `update-code` again |
| step 4 | the old code | the old API, or nothing | `tenant start $T` (the tree is still the old one), or `update-code` again |
| steps 5–6 | the old code | **nothing** (the API was stopped in step 4) | an image row: `tenant start $T` (the old image; the worktree does not feed it). A **migrating** worktree row: its checkout may already be at the new commit — put it back first (`git -C <worktree> checkout <sha>`, the sha is step 5's `worktree-prev:` checkpoint in `job show`), and the UI if step 6 swapped it (`dist` ↔ the `dist.prev-<ts>` step 6 checkpointed), then `tenant start $T`; or simply run `update-code` again |
| **after step 7** | the NEW image | nothing (step 8 had not run) | `tenant start $T` — it starts what the row says, the new image — or `update-code` again to finish (UI probe, post-checks, `last_ops`) |

```bash
ragstack-ctl job show <id>                    # which step it stopped in, and its checkpoints
ragstack-ctl tenant start $T                  # after step 7: starts the NEW image the row names
ragstack-ctl tenant update-code $T --image $IMAGE [--artifact …] --yes-destructive $T   # or finish it
```

To go back to the previous image after an interrupted job, upgrade to it:
`update-code --image <code.previous_image>`. For a migration interrupted after
step 7 there is no worktree launch to return to by op; `tenant start` (the
image) is the recovery.

### Verify

```bash
ragstack-ctl --json tenant show $T | jq '.registry | {server_image, code, last_ops: .last_ops["update-code"]}'
ragstack-ctl fleet status                     # API mode image, server image, health
curl -s -H "X-API-Key: $KEY" http://127.0.0.1:<api>/v1/version   # git_sha == the image's commit
ragstack-ctl gowe render $T                   # reads the worktree, now at the image's commit (note line)
ls -l <data_dir>/logs/api-$T.log              # the runscript appends here
```

`gowe render` on an image-mode row checks the **worktree**, which step 5
keeps at the image's commit, so `<worktree>/cwl` and the image's
`/opt/ragstack/cwl` are the same tree; it prints a note line saying so.

### Old builds and the previous UI

`dist.prev-<ts>` directories accumulate, one per UI swap; remove old ones by
hand once the tenant has soaked. Old images stay in the store
(`/rag/data/ctl/images/server/`) while any row or `code.previous_image`
names them.

---

## Part 2 — the manual procedure (hand-run tenants)

This is the procedure that moved `hackathon`, `dev` and `asm-next` to v1.6.0
and v1.6.1 on 2026-09-15, before the control plane ran them. It applies to a
tenant whose row says `supervisor: manual` — one the ctl does not start or
stop. A ctl-run tenant never takes it: use Part 1. Line citations are against
the tree at that date; read a doc line number as a hint and the heading as the
anchor.

### Two rules before anything starts

#### Never stop a service by process-name pattern

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

#### Write the plan first

`CLAUDE.md:101`: *"any operation that touches live infrastructure gets a Fable
plan before it starts."* A tenant upgrade stops a live API, replaces the code
under it and rewrites a registry row — it is squarely in scope. Write the plan,
including which tenant, which tag, the rollback tag, and who is using the tenant
right now. Do not start from this runbook alone.

---

### 0. Establish the tenant's UI mode — this changes the procedure

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

### 1. Back up `state/` and `tenant.env` — first, always

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

### 2. Move the worktree to the tag

```bash
git -C "$W" fetch --tags
git -C "$W" checkout --detach "$TAG"
git -C "$W" describe --tags     # Expect: exactly $TAG
git -C "$W" status --short      # Expect: empty. A dirty tenant worktree is a stop-and-investigate.
```

Tenant worktrees are **always detached** — `git status -sb` reads
`## HEAD (no branch)` on all five. That is the intended state; a tenant sitting
on a branch would silently move under you on someone else's `git pull`.

### 3. Rebuild the UI — static-UI tenants only

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

### 4. Check whether the release wants new `tenant.env` keys

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

### 4a. A release whose CWL is stamped: the tools image must be where the workers look

> **Never exercised as of 2026-10-06.** No tag carries a stamped CWL yet. The
> newest tag, `v1.6.4`, names the bare `ragstack-worker.sif` in all 23
> `dockerPull` sites, and no tag contains the boot check (#672, `cde401b`;
> its refusal of unset `GOWE_IMAGE_DIRS` is #678, `cdcf737`) or the
> `GOWE_TOOL_IMAGE` refusal (#664, `0e9bbb0`). This section is written from
> the code. The first stamped release will validate it. How a release gets
> stamped is in [`cut-a-release.md`](cut-a-release.md).

A **stamped** release names one versioned tools image in every
`DockerRequirement` (`dockerPull: ragstack-tools-<version>-b<N>.sif`) and
commits that image's receipt as `cwl/tool-image.receipt.json`
([ADR-0010](../adr/0010-tool-image-binding.md) decisions 4 and 6). A GoWe
worker resolves that name as `<its --image-dir>/<name>`, and **nothing in the
engine checks the file**. So before the API boots on such a tag, the image
has to be present, and present as the exact build the release stamped. Run
this section **before step 5's restart**. You can also run it before step 1:
everything up to the `render` reads the tag, not the worktree.

**Does it apply?** Ask the tag, not the worktree:

```bash
git -C "$W" grep -h "dockerPull:" "$TAG" -- 'cwl/*.cwl' | sed 's/^ *//' | sort | uniq -c
#   23 dockerPull: ragstack-worker.sif                 → UNSTAMPED: do item 1 below (GOWE_TOOL_IMAGE), then skip to step 5 (see the end of this section)
#   23 dockerPull: ragstack-tools-v1.6.5-b1.sif        → STAMPED with that name: continue
git -C "$W" show "$TAG":cwl/tool-image.receipt.json  # stamped tags only: the build the release named
```

A mix of the two names is a release that should never have been tagged, since
`stamp_tool_image.py --check` and the pin test both refuse one. Stop and report
it.

**1. `GOWE_TOOL_IMAGE` must not be in `tenant.env`.** The setting is retired
(ADR-0010 decision 5). Every tag that contains #664 **refuses to boot** while
it is set, whatever `INGEST_BACKEND` is:
`GOWE_TOOL_IMAGE='…' is set, and the setting is retired (ADR-0010 decision 5,
#655) … Remove GOWE_TOOL_IMAGE from tenant.env and restart.`
(`python/ragstack/api/deps.py`, `_refuse_retired_tool_image_override`).
`ragstack-ctl adopt` warns `retired_env_key` on it, and `ragstack-ctl env set`
will not set it.

```bash
grep -n '^GOWE_TOOL_IMAGE=' "$D/config/tenant.env" && echo "REMOVE IT before the restart"
```

**2. Know which dir this tenant's workers resolve.** The tenant's group is
`GOWE_WORKER_GROUP` in its `tenant.env`. The group's `--image-dir` is on the
running workers' command lines:

```bash
grep '^GOWE_WORKER_GROUP=' "$D/config/tenant.env"
ps -eo args | grep '[g]owe-worker' | grep -o -- '--group [^ ]* .*--image-dir [^ ]*' | sort -u
```

As of 2026-10-06: `dev` uses group `ragstack-dev` → `/scout/containers/ragstack-dev`,
`hackathon` uses `ragstack-hackathon` → `/scout/containers/ragstack-hackathon`,
and the shared `ragstack` group resolves `/scout/containers`. **None of them
resolves the shared release store `/scout/containers/ragstack/`.** A bare name
does not reach into a subdirectory. [`cut-a-release.md` § 7](cut-a-release.md#7-worker-groups-and---image-dir-during-the-migration-never-exercised)
gives the two ways to fix that, per group. One of them has to be in place
before you go on.

**3. Confirm the named image and its receipt are in that dir.** "In" means both
files, under the stamped name, in the group's `--image-dir`, either directly or
as symlinks into the store. The check opens the receipt beside the path it
finds. `GOWE_IMAGE_DIRS` must name that same dir, because it is what the boot
check searches (first hit wins). The key is executable-surface, so
`ragstack-ctl env set` refuses it. Edit `tenant.env` directly, as the
management session that owns `/rag`
([`verifying-tools-image.md`](verifying-tools-image.md)):

```bash
grep '^GOWE_IMAGE_DIRS=' "$D/config/tenant.env"     # expect the group's --image-dir
N=ragstack-tools-v1.6.5-b1.sif; G=/scout/containers/ragstack-hackathon
ls -lL "$G/$N" "$G/$N.receipt.json"
```

If `GOWE_IMAGE_DIRS` is unset on a stamped tag, the API **refuses to boot**
(`not verified: GOWE_IMAGE_DIRS unset`, #678; below). Set it as part of this
upgrade, before step 5.

**4. Run the check: `ragstack-ctl gowe render <tenant>`.** This is read-only.
It reads the registry row and `tenant.env`, then runs the tenant's own
interpreter against its own checkout:
`python -m ragstack.tool_image verify --json --dirs $GOWE_IMAGE_DIRS --cwl …`
for the three CWLs the API registers. That is the same function the boot
calls, so the two cannot disagree.

```bash
ragstack-ctl gowe render "$T"                                    # after step 2's checkout
ragstack-ctl gowe render "$T" --image-dirs "$G"                  # before tenant.env carries GOWE_IMAGE_DIRS
# before step 2: --worktree redirects only the two defaulted CWL keys and a RELATIVE
# GOWE_WORKFLOW_CWL. dev and hackathon set it ABSOLUTE (/rag/repos/tenants/$T/cwl/…), so
# the ingest row would still render the live, unstamped checkout. Check the tag's file directly:
# <scratch> = a clean checkout of $TAG, e.g.: git -C "$W" worktree add --detach /tmp/$TAG-cwl $TAG
PYTHONPATH=<scratch>/python /rag/envs/ragstack/bin/python -m ragstack.tool_image verify --dirs "$G" \
    --cwl <scratch>/cwl/pdf-ingest-scatter.cwl --cwl <scratch>/cwl/graph-extract.cwl --cwl <scratch>/cwl/restore-collection.cwl
```

**Expect:** for each workflow, a `text sha256 (GoWe would content-hash this)`,
`dockerPull: ragstack-tools-…-b1.sif -> ok at <dir>/<name>`, and exit **0**.
Exit **3** is a refusal, the same decision the boot will make: fix the
store, not the receipt. Every message is listed in
[`verifying-tools-image.md` § When it fails](verifying-tools-image.md#when-it-fails).
Exit 1 means the check could not run. Exit 2 is a usage error.

> **`unchecked` is a refusal.** If neither `GOWE_IMAGE_DIRS` nor
> `--image-dirs` names a dir, every stamped workflow comes back `unchecked`
> and `render` exits **3**, as the boot refuses (#678; a ctl built between
> `cde401b` and `cdcf737` still exits 0 there, so do not install one).
> `python -m ragstack.tool_image verify` exits **4** for the same case.
> **The installed `/rag/bin/ragstack-ctl` predates this verb**
> (`v1.6.2-10-g5a05168` → `unknown command "gowe"`, as of 2026-10-06). Until
> it is reinstalled from a tagged checkout at or after `cdcf737`
> ([`ctl-deploy.md`](ctl-deploy.md)), run the same check from the tenant's
> checkout:
> `PYTHONPATH="$W/python" /rag/envs/ragstack/bin/python -m ragstack.tool_image verify --dirs "$G" --cwl "$W/cwl/pdf-ingest-scatter.cwl" --cwl "$W/cwl/graph-extract.cwl" --cwl "$W/cwl/restore-collection.cwl"`
> (the last two are the defaults; GOWE_WORKFLOW_CWL has no default. Use the tenant's `GOWE_WORKFLOW_CWL` /
> `GRAPH_EXTRACT_CWL` / `COLLECTION_RESTORE_CWL` if it sets them).

**5. Know what the boot does now.** With `INGEST_BACKEND=gowe` the API runs
that check at startup for the three registered CWLs
(`_verify_tool_images_at_boot`):

| The tag's CWL, and the tenant's settings | At boot |
|---|---|
| stamped, `GOWE_IMAGE_DIRS` set, all checks pass | boots. One info line per image: `verified at <path>: sha256 ok, labels ok` |
| stamped, `GOWE_IMAGE_DIRS` set, **any** problem (not found, no receipt, sha256 or label mismatch, committed receipt ≠ the store's) | **refuses to boot**: `tool image identity check FAILED (ADR-0010 decision 7, #655)`, naming the image, the path tried and each problem |
| a document naming two images | **refuses** |
| stamped, `GOWE_IMAGE_DIRS` **unset** | **refuses to boot** (#678): `tool image identity check NOT RUN (ADR-0010 decision 7, #655): not verified: GOWE_IMAGE_DIRS unset`, naming each setting and image, and `Set GOWE_IMAGE_DIRS=<the dir the tenant's workers resolve --image-dir against>` |
| stamped, no `apptainer` on the API's `PATH` | label check is a **warning**. The sha256 still has to match. |
| unstamped (`ragstack-worker.sif`), with or without `GOWE_IMAGE_DIRS` | boots. Info: `unstamped tree, identity check skipped` |
| `INGEST_BACKEND` not `gowe` | no check (the `GOWE_TOOL_IMAGE` refusal still applies) |

A refusal shows up in the API log and the API never binds its port. For a
`manual` tenant, step 5's `/health` check fails. For a ctl-supervised one
(`dev`, `hackathon`: `supervisor: instance`, run as `svcbvbrc`), it is
`/rag/bin/ctl-as-svc.sh tenant restart <t> --yes --wait --direct` that fails.
The boot check runs as the API's account, so for those two tenants
`svcbvbrc` must be able to read the image and its receipt. The rollback is
the usual one: check out `worktree-sha` again.

**A tenant still on an unstamped tag: nothing changes.** Its CWL names
`ragstack-worker.sif`. The worker resolves that through the group dir's
`ragstack-worker.sif` symlink exactly as before. The boot check logs
`unstamped` and skips, and the API seeds no provenance inputs, so collection
manifests record only the image's own `RELEASE` (if it has one). The only new boot-time rule it can hit is the `GOWE_TOOL_IMAGE` refusal (item 1 above), on any tag that contains #664 — which every tag after `v1.6.4` will, stamped or not. Keep the group dir's `ragstack-worker.sif` symlink in place
while any tenant on that group is unstamped.

### 5. Restart the API by the recipe in `ops/coconut/restore.sh`

**First check who supervises the tenant** —
`ragstack-ctl --json fleet status`. A `supervisor: instance` tenant is restarted
through the control plane only (`ctl-handover.md`); stopping it by pid means
fighting its supervisor. Everything below is the `manual` path.

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

### 6. Re-adopt so the registry records the new code

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

### Verify the upgrade landed

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

### Rollback

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

## Related

- [`server-image.md`](server-image.md) — building the server image (and the
  tools image) the upgrade runs.
- [`cut-a-release.md`](cut-a-release.md) — how a release gets its tools image
  and stamped CWL (tag `vT` → build → store → stamp → tag `vS`), then the
  server image built and prepared from `vS` (§ 9); Part 2 § 4a above is the
  hand-run tenant's side of it.
- [`verifying-tools-image.md`](verifying-tools-image.md) — the boot identity
  check and `ragstack-ctl gowe render`, every failure message.
- [`tenant-admin.md`](tenant-admin.md) — running the tenant after the upgrade:
  roles, shares, quotas, diagnosing a user report.
- [`upgrade-407-remove-gowe-store-urls.md`](upgrade-407-remove-gowe-store-urls.md)
  — a worked example of a release that needs a `tenant.env` edit in the same
  deploy, and refuses to boot without it.
- [`tracing-a-503.md`](tracing-a-503.md) — when the post-upgrade smoke test
  fails with a 503 and you need to say which leg.
- `ops/coconut/restore.sh` — the launch recipes, and the post-reboot procedure
  this upgrade's restart step borrows from.
