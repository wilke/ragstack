# Handing a tenant over

Two halves, in the order an operator runs them:

1. **[Preparing a tenant](#preparing-a-tenant-for-handover--a1a6)** — the A1…A6
   ops, run days ahead, after which nothing has changed for a user.
2. **[The handover itself](#handing-a-tenant-over--release-take-soak-commit)** —
   release (owner), take (service account), soak, commit; and the rollback
   drill that is the reason it is safe to try.

---

## Preparing a tenant for handover — A1…A6

The six ops an operator runs on a hand-started tenant, days before its
handover, to move it into a shape the control plane can operate. They are the
"A" row of the PR-E plan
(`/rag/documents/plans/plan-tenant-control-plane-2026-09-10.pr-e.md`).

**Nothing here hands anything over.** After A1–A6 the tenant is still started
by `wilke`, still serves on the same ports, still answers the same URLs. What
changes is that its registry row now describes something the ctl could
supervise — which is the prerequisite for the two-account handover itself
(PR-E2: `tenant handover --release` / `--take` / `--commit`).

Every one of the six is idempotent and reversible, and every one takes
`--dry-run`. Run them one at a time, read the plan, then run it again with
`--yes` (or `--yes-destructive <tenant>`).

**Account.** All six run as the account that OWNS the tenant's paths — today
`wilke`, in a real terminal on coconut. Four of them are CLI-only jobs
(`--direct` is implied and `--server` is refused): the daemon runs as
`svcbvbrc`, which cannot read this tenant's `/proc`, cannot rename directories
in its data dir, and must not install packages on request.

**Order.** A1 before anything that edits the env files; A2 before A3 on a
tenant whose UI is a dev server (the rebase takes `node_modules` with it); A5
before A6 only so the backup records confirmed capabilities. Otherwise the
order is yours.

---

## Where a tenant stands

```bash
ragstack-ctl tenant show <t>
ragstack-ctl doctor <t> --op handover
```

`doctor --op handover` is the checklist: it raises to **error** exactly the
findings that stop a handover onto the `instance` supervisor —
`env_not_systemd_parsable`, `port_owner_mismatch`, `worktree_outside_mirror`,
`worktree_gitdir_unreadable`, `writable_by_others`, `stores_unconfirmed`, `boot_cron_missing`, `ctl_account_no_access`.
(`port_not_listening` is deliberately NOT among them: the handover's second
phase acts on a tenant its first phase has already stopped, so raising that
finding would refuse every take there will ever be. "Is there a running tenant
to hand over" is the release's own check, asked of the row's `state`.) The three
systemd facts (`linger_missing`, `user_dropin_missing`, `runtime_dir_missing`)
are NOT in that list: PR-D2 postponed systemd until the machine is dedicated,
and this deployment hands tenants over to the instance supervisor, whose boot
hook is the service account's `@reboot` crontab line.

Each of A1–A6 below clears one or two of those rows.

---

## A1 — `env normalize`: one grammar, secrets in their own file

```bash
ragstack-ctl env normalize <t> --dry-run
ragstack-ctl env normalize <t> --yes
```

Moves inline comments onto their own line (systemd's `EnvironmentFile=` keeps
them as part of the value) and moves every secret-class key out of
`tenant.env` into `secrets.env`. The previous content of each file is kept
beside it as `.bak-env-normalize-<ts>`.

**Clears:** `env_not_systemd_parsable`. Also makes `env_layout: managed`,
which is what lets a backup copy `tenant.env` as a public file instead of only
inside the encrypted payload.

**Effect on the running tenant:** none until its next restart. The API read
its environment at start-up and goes on serving from what it read.

---

## A2 — `tenant set-ui-mode`: a build nginx serves, or a server somebody runs

```bash
# the SOP: a static build
ragstack-ctl tenant set-ui-mode <t> static --dry-run
ragstack-ctl tenant set-ui-mode <t> static --yes-destructive <t>

# the documented exception (dev keeps its Vite server)
ragstack-ctl tenant set-ui-mode dev external --ui-port 8090 --dry-run
ragstack-ctl tenant set-ui-mode dev external --ui-port 8090 --yes
```

`static` does seven things, in this order, and rolls every one of them back:

1. `npm ci` in `<worktree>/frontend` — **only** when
   `node_modules/.bin/vite` is absent. It is the one step in the whole control
   plane that reaches the network. The check is the binary rather than the
   directory on purpose: an interrupted install leaves a directory with some of
   a thousand packages in it, and that has to be repaired, not skipped.
2. `vite build --base /ragstack/<t>/ui/` into `<data_dir>/ui/dist.building`.
3. stops that tenant's Vite dev server, found by the port its row records AND
   by identity (cwd under `<worktree>/frontend`, `vite` in the argv). Never by
   process name.
4. two renames on the same filesystem: `dist` → `dist.prev-<ts>`, then
   `dist.building` → `dist`. nginx serves the old directory until the first and
   the new one after the second; between them — microseconds — the alias has no
   directory and answers 404. The dev server is stopped in step 3, BEFORE this,
   so the gateway is still routing the UI to its port throughout the window and
   nobody is served a half-swapped build.
5. registry `ui.mode: static`, port cleared.
6. a gateway generation in which the static alias replaces this tenant's
   `$tenant_ui` row.
7. `GET /ragstack/<t>/ui/` through the live gateway, which must answer **200**.

`external --ui-port P` writes the registry and publishes a generation, and
does nothing else at all: no build, nothing started, nothing stopped. It is
for a dev server somebody else runs and goes on running (plan decision D2 —
`dev` keeps its Vite server through and after its handover).

**Clears:** the "instance mode refuses a dev-mode UI" refusal.

**Undo.** The job's rollback runs last-step-first and leaves you with: the row
back as it was, a gateway generation republished FROM that restored row, and
`dist.prev-<ts>` back in place as `dist`. The staged build is kept at
`<data_dir>/ui/dist.building` for inspection; the next run's
`vite build --emptyOutDir` clears it.

**One thing a rollback does not put back: the Vite dev server.** The ctl
stopped it by pid and does not know the command line to start it with — that
was yours. After a rollback the row says `dev`/`external` again and the gateway
routes to the port again, so the repair is to start the server the way you
started it before. The plan warns about this before you approve it.

By hand, if you are undoing a job that already finished:
`mv <data_dir>/ui/dist{,.bad} && mv <data_dir>/ui/dist.prev-<ts> <data_dir>/ui/dist`,
then `ragstack-ctl tenant set-ui-mode <t> external --ui-port <P>`, then start
the dev server.

**Watch for:** a tenant the live gateway does not route yet — the publish step
refuses rather than advancing a generation for a document nobody reads. Run
`ragstack-ctl gateway apply` first.

---

## A3 — `tenant rebase-worktree`: code out of the mirror, not out of a home dir

```bash
ragstack-ctl tenant rebase-worktree <t> --dry-run
ragstack-ctl tenant rebase-worktree <t>
# a dev-mode UI needs its node_modules back afterwards:
ragstack-ctl tenant rebase-worktree dev --include-dev-ui
(cd <worktree>/frontend && npm ci)
```

Re-checks the worktree out of the bare mirror at the sha its current HEAD
already resolves to — no code change — and leaves the old checkout beside it
as `<worktree>.home-<ts>`. Safe against a running API: it keeps the inode of
the code it loaded.

**Clears:** `worktree_outside_mirror`, `home_path_in_production`.

---

## A4 — `tenant set-bind`: loopback

```bash
ragstack-ctl tenant set-bind <t> 127.0.0.1 --dry-run
ragstack-ctl tenant set-bind <t> 127.0.0.1 --yes
```

Writes the registry's `api.bind` and **nothing else**. That field is where
both launch paths read the bind from — `render.APIArgv`'s `--host` for a
ctl-supervised start, and `ops/coconut/restore.sh`'s for a hand start — so
this one write covers both. There is deliberately no `tenant.env` key: the
tenant API has never read one, the value reaches uvicorn only as a
command-line argument, and a key written for symmetry would be configuration
nothing reads.

Effective at the tenant's **next restart**; the row is marked
`restart_pending`. Anything that reaches `coconut:<api port>` directly from
another host stops working then — everything through the gateway is unaffected
(it proxies over loopback). That is plan decision D1.

**Clears:** the renderer's non-loopback refusal.

---

## A5 — `adopt --confirm-stores`: the ctl may stop this store

```bash
ragstack-ctl adopt <t> --data-dir <D> --worktree <W> --readopt \
    --confirm-stores qdrant,elasticsearch[,postgres] --preview
ragstack-ctl adopt <t> --data-dir <D> --worktree <W> --readopt \
    --confirm-stores qdrant,elasticsearch[,postgres] --commit
```

**A re-adoption does not undo A2 or A4.** `--readopt` re-reads the host, and
the host has not caught up with either decision: a fresh preview of a `static`
UI infers `external` (the tenant has no dev-server port), and the running API
still binds `0.0.0.0` until its next restart. Those three fields — `ui.mode` /
`ui.port`, `api.bind` and the store capabilities — are carried over from the
existing row unless you name them: `--ui-mode`/`--ui-port` for the UI,
`--api-bind` for the bind, `--confirm-stores` for the capabilities. A bind that
differs from the live process is recorded as an `api_bind_drift` row, so the
disagreement is written down rather than resolved behind your back.

Re-verifies each named **exclusive** leg from `/proc` and the listen table
before it writes anything:

* exactly one process serves the leg's port (two pids is two servers; a pid
  this account cannot read is somebody else's);
* that process has a directory under this tenant's own data dir MOUNTED where a
  store of its kind keeps its data — `<data_dir>/qdrant/storage` at
  `/qdrant/storage`, the ES data dir, the postgres PGDATA. The evidence is
  `/proc/<pid>/mountinfo`, not the command line: the process holding the port is
  the one inside the container (`./qdrant`, the elasticsearch JVM, `postgres`)
  and carries no `--bind` at all, because the `apptainer instance run` that set
  the mounts up exited long ago. Only that process's own account may read its
  mountinfo — which is the account you are running this as;
* no other registry row names the same port (resolved the same way for both
  sides: the store URL when there is one, the row's port block when there is
  not).

Then it sets `capabilities {stop, snapshot, restore}` on every named leg.
`purge` is never set. A **shared** leg is refused outright ("not exclusive"):
confirming `stop` on it would be confirming that the ctl may take down a store
other tenants are using. The run is all-or-nothing.

**Clears:** `stores_unconfirmed` (and, once every leg is confirmed,
`capabilities_unconfirmed`).

**Which legs does a tenant have?** `ragstack-ctl tenant show <t>` prints the
ownership of each. On coconut today: `dev` has qdrant + elasticsearch,
`hackathon` has qdrant + elasticsearch + postgres, `lucid-next` has
elasticsearch only, and `demo` and `asm-next` run entirely on shared stores
(there is nothing to confirm for those two, and nothing to hand over either).

---

## A6 — `tenant backup --scope config,state`: the safety net

```bash
ragstack-ctl tenant backup <t> --scope config,state --dry-run
ragstack-ctl tenant backup <t> --scope config,state --yes
ragstack-ctl backup list <t>
```

Seconds, with the API still serving. The bundle carries the config allowlist,
the sealed secret files, the SQLite state (`VACUUM INTO`, exactly as a full
bundle does), the rendered units, `registry-row.json` and
`rollback-descriptor.json` — the last of which is what
`ops/coconut/restore.sh --tenant <t>` reads to bring this tenant back without
the control plane. It carries **no store snapshots** and takes **no fence**:
`--fence` with a light scope is refused, because a fence that stops the API to
hold still a set of files the bundle is not going to read is an outage bought
for nothing.

A light bundle is `fenced: false`, `consistent: false` and is never a restore,
handover or decommission prerequisite — the manifest and `last_backup.scope`
both say so. It is the pre-handover safety net, not a recovery point: no data
moves during a handover (the same directories serve the same processes under
another account), so what has to be recoverable is the tenant's identity and
configuration, and that is exactly what this holds.

For a real recovery point, take a full fenced bundle at a maintenance window:
`ragstack-ctl tenant backup <t> --fence --yes-destructive <t>`.

---

## When A1–A6 are done

```bash
ragstack-ctl doctor <t> --op handover
ragstack-ctl tenant show <t>
ragstack-ctl units render <t>          # the renderer must accept the row as it stands
```

The row is **handover-ready** when `doctor --op handover` is green apart from
the root items, and nothing has changed for a user except that the UI is now a
static build.

One more preparation step applies to a tenant with its own postgres —
hackathon today, and any tenant `new-tenant.sh` provisioned with
`--postgres local`:

```bash
ragstack-ctl env pg-password <t> --dry-run
ragstack-ctl env pg-password <t> --yes
```

The instance supervisor starts `postgres-<manifest>` with the role password in
`APPTAINERENV_POSTGRES_PASSWORD`, read out of `secrets.env` at run time — and a
`new-tenant.sh` tenant carries that password under no such name: the literal is
inside the generated `bin/up.sh` and inside the `POSTGRES_DSN` /
`USER_STORE_DSN` / `COLLECTION_STORE_DSN` connection strings, while
`provision.env` has the host and the port but not the password. Without this
step the handover stops everything and then cannot start the tenant's own
postgres. The op derives the value from those connection strings (refusing when
the three disagree), keeps the previous `secrets.env` as
`.bak-pg-password-<ts>`, never prints it, and does nothing at all when the key
is already there. The release checks its result BEFORE it stops anything.

Then the handover itself, below.

### If something goes wrong (the preparation)

| Symptom | What it means | What to do |
|---|---|---|
| `set-ui-mode` fails at the probe with 404 | the generation is published but the alias serves nothing | check `<data_dir>/ui/dist/index.html` exists; the job's rollback has already put the previous build back |
| `set-ui-mode` refuses: "the live gateway does not route" | the tenant has no published route yet | `ragstack-ctl gateway apply`, then run it again |
| `set-ui-mode` refuses: "cannot attribute" on the UI port | the dev server belongs to another account | run it as that account, or stop the server by hand first |
| `--confirm-stores` refuses: "not exclusive" | the leg is a shared store | leave it; the tenant is handed over without that capability |
| `--confirm-stores` refuses: "2 different processes" | two servers on one port, or a restart in flight | look at the port, then run it again |
| `--confirm-stores` refuses: "mounts nothing under this tenant's data dir" | the process on that port keeps its files somewhere else | it is not this tenant's store; check `stores.*.url` against what is really running |
| `--confirm-stores` refuses: "its storage is somewhere else" | something of the tenant's is mounted, but not its data directory | look at the instance's binds before confirming anything |
| `--confirm-stores` refuses: "reading the mounts of pid N" | the store belongs to another account | run the confirmation as the account that started it |
| after a rollback the UI route 502s | the dev server this op stopped is not restarted by a rollback | start it again the way you started it before |
| `backup --scope` refuses: "outage for nothing" | `--fence` with a light scope | drop `--fence`; a light bundle does not need one |
| `doctor --op handover` red on `boot_cron_missing` | nothing would start the tenant after a reboot | `ragstack-ctl fleet enable-boot --cron` as the service account |
| `doctor --op handover` red on `ctl_account_no_access` | svcbvbrc cannot write a managed root | `ragstack-ctl fleet grant --user svcbvbrc` as the path owner |

---

## The handover itself — release, take, soak, commit

The handover is a **two-account protocol**, and that is a fact about this host
rather than a design preference:

* the daemon runs as `svcbvbrc` and the tenant's processes are `wilke`'s. One
  account cannot signal another's here — reading `/proc/<pid>/cwd` across
  accounts is denied, so the identity check every signal in this control plane
  makes cannot even be attempted;
* apptainer keeps a **per-account instance registry**. `apptainer instance
  list` as svcbvbrc does not show `qdrant-hackathon` at all, let alone stop it.

So the owner **releases** and the service account **takes**:

| Phase | Account | Command | What it leaves behind |
|---|---|---|---|
| release | `wilke`, `--direct` | `tenant handover <t> --release` | the tenant DOWN, `state: handover`, a token printed |
| take | `svcbvbrc` | `tenant handover <t> --take --token <T>` | the tenant UP under `supervisor: instance`, `owner: svcbvbrc`, `handover.phase: taken` |
| soak | — | watch it | — |
| commit | `svcbvbrc` | `tenant handover <t> --commit` | `desired_boot: enabled`, the handover block cleared |
| abandon | `svcbvbrc` then `wilke` | `tenant stop <t>`, then `tenant handover <t> --abandon`, then `restore.sh --tenant <t>` | the tenant back the way it was |

**Four ordinary jobs, and not one of them parks.** An earlier draft had the take
stop at a cutover and the commit continue it — which would have held this
tenant's locks for the length of the soak, and one of those is the REGISTRY
lock, which is the whole fleet's: a 48-hour drill on `dev` would have blocked
every backup of every other tenant behind one operator's coffee. So each phase
finishes, and the ROW carries the state between them: `handover.phase`, and the
token, until a commit or an abandon clears them.

**`owner` moves at the TAKE, `desired_boot` at the commit.** From the moment
the take spawns the API, the pid on the tenant's port is the service account's,
and a row that went on naming the previous owner through a soak would make
doctor's `port_owner_mismatch` fire against the truth — and would make
`tenant stop <t>`, the first half of the way back, refuse for the wrong reason.
What the commit adds is the BOOT commitment: until it runs, `desired_boot`
stays as it was and `fleet start --all` does not adopt the tenant.

Two things hold at every point between them:

* **the row says what is true.** `state: handover` is written BEFORE the first
  process is stopped, so a job that dies halfway leaves a row that tells the
  next operator where they are. Nothing in this protocol leaves the registry
  claiming `active` over a tenant that is not.
* **the tenant is restorable.** Before the release: `restore.sh --tenant <t>`.
  After the take: `ragstack-ctl fleet start --all`. In between, and after an
  abandon: `restore.sh --tenant <t>` again, from the rollback descriptor the
  row has carried since `adopt --commit`.

A handover moves **no data**. The same directories serve the same processes
under another account, through the ACLs PR-D2 installed. The risk is not data
loss, it is a tenant neither account can start — which is what the census, the
port proofs and the commit's own liveness check are all for.

### The two conventions

**Accounts.** `--release` and `--abandon` are the OWNER's and imply `--direct`
(`--server` is refused). `--take` and `--commit` are the SERVICE ACCOUNT's —
either through the daemon or as `ops/coconut/ctl-as-svc.sh`. `--abandon` is
gated on `handover.released_by` rather than on the row's `owner`, because after
a take the owner IS the service account: gating on it would be that account
handing the tenant back to itself.

**State directory.** A wilke `--direct` job cannot write the daemon's
`/rag/data/ctl/jobs.db`: that file belongs to `svcbvbrc`. The owner-side phases
therefore run in a state directory wilke owns, against the same real registry:

```bash
export CTL_STATE_DIR=/rag/data/ctl-selftest
export CTL_CONFIG_DIR=/rag/config/ctl-selftest
```

`tenant handover --release` **refuses before it starts** when the state
directory it would use is not writable by this account, and prints exactly
those two lines. Everything else — the registry, the gateway, the tenant's
files — is the real deployment's.

### Release (wilke)

```bash
export CTL_STATE_DIR=/rag/data/ctl-selftest CTL_CONFIG_DIR=/rag/config/ctl-selftest

ragstack-ctl tenant handover hackathon --release --dry-run
ragstack-ctl tenant handover hackathon --release --yes-destructive hackathon
```

The plan, in the order it runs — and the order IS the operation:

1. **can the take read the postgres password** out of `secrets.env` (skipped
   for a tenant with no postgres server of its own). Asked first, because
   discovering it afterwards means discovering it with the tenant down;
2. **no ingest job is still running** — the last moment anybody can decide to
   wait;
3. **census**: every collection in the tenant's own qdrant with its exact point
   count, every index in its own elasticsearch with its document count, and the
   API's own `/v1/collections?counts=true`. Taken while the tenant is UP;
4. **the registry**: `state: handover`, and a `handover` block holding the
   census, the descriptor reference and a one-shot token;
5. **stop the API** by pidfile, after checking cwd and cmdline (never `pkill`),
   TERM then KILL, and **prove the port free**;
6. **stop each of the tenant's own apptainer instances** in reverse start order
   — postgres, elasticsearch, qdrant — each followed by a port proof.

It prints the token. Keep it: the take needs it, and `ragstack-ctl job show
<id>` has it in the job's result if the terminal is gone.

From here the tenant is down and the gateway answers 502 for it.

**If the release fails partway** it rolls back what it can: the registry step
puts `state: active` back and clears the block, and each instance stop tries to
start its instance again (it runs as the account that owns them, so it can).
The API is the one thing it cannot restart — its command line is yours, not the
registry's — and the rollback says so in as many words. The answer is always
the same: `ops/coconut/restore.sh --tenant <t>`.

### Take (svcbvbrc)

```bash
sudo -u svcbvbrc ragstack-ctl tenant handover hackathon --take \
  --token <the token the release printed> --dry-run
sudo -u svcbvbrc ragstack-ctl tenant handover hackathon --take \
  --token <token> --yes-destructive hackathon
```

(or through the daemon: `POST /v1/tenants/hackathon/ops/handover` with
`{"phase":"take","token":"…"}`.)

It checks the token against the row, proves every port free, records
`supervisor: instance`, then starts the tenant's own stores, waits for any
**shared** store the tenant uses but does not own (bounded, 180 s — the two
accounts' boot orders are not coordinated, so this wait is what makes the order
irrelevant), and spawns the API with a pidfile. Then it proves it:

* `GET /health` on the tenant's own port;
* `GET /v1/health/deep` with the tenant's own admin key, read from its
  `secrets.env` at run time;
* **the census, checked back**. Fewer rows than the release recorded is a
  refusal: the store is up, but not on the data it was on. More is reported and
  allowed (something wrote to the tenant between the two readings);
* `GET /ragstack/<t>/api/health` through the live gateway — the route users
  use.

Then it records `state: active`, `owner: svcbvbrc` and `handover.phase: taken`
— and **finishes**. `desired_boot` is untouched, so nothing brings this tenant
back at a reboot until the commit; the block and its token stay in the row,
because that is what the commit and the abandon are gated on.

### Soak

The row says `state: active`, `supervisor: instance`, `owner: svcbvbrc` and
`handover.phase: taken`, with `desired_boot` still where it was. That
combination is exactly "running under the control plane, not yet the control
plane's tenant", and it is what says the way back is still open.

Nothing holds this tenant's locks during the soak, so ordinary reads and ops
work — but `set-supervisor` refuses while a handover is in flight, and a second
`--release` or `--take` refuses too.

```bash
ragstack-ctl tenant show hackathon
ragstack-ctl fleet status
sudo -u svcbvbrc ragstack-ctl tenant logs hackathon --file api --lines 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9000/ragstack/hackathon/api/health
```

The acceptance checks, per tenant: the API pid is `svcbvbrc`'s in `/proc`; the
tenant's own instances are listed by the svcbvbrc daemon and by nobody else;
the collection counts match the census; `restore.sh --dry-run` shows the tenant
as mid-handover.

48 hours for `dev` (the drill); a day for the others.

### Commit (svcbvbrc)

```bash
sudo -u svcbvbrc ragstack-ctl tenant handover hackathon --commit
```

An ordinary job, gated on the row's `handover.phase: taken` and on this being
the account that took it. Before it promises anything about boot it **checks
the tenant is still up** — something is listening on the API port, that process
is attributable to this account, and `/health` answers — because a commit over
a tenant that is down would enable a boot for something that is not running,
and the soak it concludes would have concluded nothing.

Then it writes `desired_boot: enabled`, `restart_pending: false`, and clears
the `handover` block. The `rollback_descriptor` is deliberately left untouched:
it is the immutable record of how the tenant ran before the ctl had it, and
`restore.sh --tenant` still reads it.

After the commit `ops/coconut/restore.sh` skips the tenant ("handed over to the
control plane") and `ragstack-ctl fleet start --all` — the `@reboot` crontab
line — is what brings it back.

### The rollback drill

Run it once, on `dev`, before trusting it on anything else. It is the whole
reason the protocol has an abandon.

```bash
# 1. release, take (as above), then decide against it.

# 2. the service account stops what it started. Idempotent.
sudo -u svcbvbrc ragstack-ctl tenant stop dev --yes-destructive dev

# 3. the owner puts the row back.
CTL_STATE_DIR=/rag/data/ctl-selftest CTL_CONFIG_DIR=/rag/config/ctl-selftest \
  ragstack-ctl tenant handover dev --abandon --yes-destructive dev

# 4. and starts the tenant the way it was started before.
ops/coconut/restore.sh --tenant dev
```

`--abandon` refuses while **any** of the tenant's ports is still held — the API
and each store it owns exclusively — so step 2 is not optional. A port this
account cannot attribute is the take's, still running as the other account; one
it can is something it started itself; and a row recording `supervisor: manual`
over either would leave those processes with nothing that ever stops them.

It writes the REGISTRY only: `supervisor: manual`, `state: active`,
`owner` back to the account that released it, the handover block cleared.
Nothing is running until step 4.

A tenant in `state: handover` is skipped by a fleet-wide `restore.sh` (it would
race the take) and started by an explicit `--tenant <n>` (which is this drill).
The script says which of the two it is doing.

### Exact commands, per tenant

**hackathon** — the mechanics: already on the mirror, static UI, clean env,
dedicated qdrant + elasticsearch + postgres.

```bash
# preparation (once)
ragstack-ctl env pg-password hackathon --yes
ragstack-ctl tenant backup hackathon --scope config,state --yes
ragstack-ctl doctor hackathon --op handover          # yellow on the systemd trio only

# the handover
export CTL_STATE_DIR=/rag/data/ctl-selftest CTL_CONFIG_DIR=/rag/config/ctl-selftest
ragstack-ctl tenant handover hackathon --release --dry-run
ragstack-ctl tenant handover hackathon --release --yes-destructive hackathon
sudo -u svcbvbrc ragstack-ctl tenant handover hackathon --take --token <T> --yes-destructive hackathon
# soak, then
sudo -u svcbvbrc ragstack-ctl tenant handover hackathon --commit
```

**dev** — the full drill, and the one tenant that keeps its Vite dev server
(plan decision D2: `ui.mode: external` on 8090, run by wilke, untouched by the
ctl). Its stores are its own; its relational state is SQLite, so there is no
`env pg-password` step.

```bash
ragstack-ctl tenant backup dev --scope config,state --yes
export CTL_STATE_DIR=/rag/data/ctl-selftest CTL_CONFIG_DIR=/rag/config/ctl-selftest
ragstack-ctl tenant handover dev --release --yes-destructive dev
sudo -u svcbvbrc ragstack-ctl tenant handover dev --take --token <T> --yes-destructive dev
# → the rollback drill above, in full →
# then release and take again, soak 48 h, and
sudo -u svcbvbrc ragstack-ctl tenant handover dev --commit
```

demo, lucid-next and asm-next follow a soak day apart. Their shared stores stay
wilke-run: only their APIs move (and lucid-next's own elasticsearch). The take's
shared-store wait is what makes their boot order irrelevant.

### Correcting a row by hand

```bash
ragstack-ctl tenant set-supervisor <t> manual|instance
```

Writes the registry's `supervisor` and nothing else — no process is started,
stopped or signalled. It is the repair for a row that disagrees with the host:
a take that wrote `instance` and then failed, an abandon nobody got to run.

It refuses the two ways of making such a disagreement permanent: moving a row
to `instance` while the API port is held by a process this account cannot
attribute (the ctl would be claiming it supervises an API it can neither signal
nor restart), and moving a row to `manual` while this account's own apptainer
instances are running for the tenant (nothing would ever stop them again). It
also refuses while a handover is in flight — that protocol is moving the same
field.

### Credentials, after the handover

```bash
sudo -u svcbvbrc ragstack-ctl key mint <t> gowe --role user --restart --prove
sudo -u svcbvbrc ragstack-ctl key revoke <t> gowe --restart --prove
```

`--restart` restarts the tenant API through its supervisor, so the new ledger
is live; on a `manual` row it is **refused** rather than quietly skipped.
`--prove` then dials the tenant and records the verdict on the job: a surviving
admin key answers 200 (so the API is up and authenticating), a minted key
answers 200, a revoked key answers 401. The credentials are read from the
tenant's own `secrets.env`; the job records fingerprints and status codes,
never a value. `--prove` without `--restart` is refused: an unrestarted API is
still serving the previous ledger, so the proof would say the opposite of what
it claims.

Both ops now write `tenant.keys[]` as well as the file, so a key the ctl minted
is a key the ctl can revoke — and a revoked row is KEPT with `revoked_at` set,
because "who held a credential last month" is the question a ledger exists for.

### If something goes wrong (the handover)

| Symptom | What it means | What to do |
|---|---|---|
| `--release` refuses: "cannot write its job database" | the state dir is the daemon's | export the two `CTL_*` variables above |
| `--release` refuses: "belong to wilke … running as svcbvbrc" | wrong account | run it as the tenant's owner, with `--direct` |
| `--release` refuses: "`ragstack-ctl env pg-password`" | the take could not read the role password | run that op first; it derives it from the DSNs already in `secrets.env` |
| `--release` refuses: "ingest job(s) are still running" | an ingest is mid-write | wait for it, or cancel it through the tenant |
| `--release` refuses: "no backup" | no recovery point | `tenant backup <t> --scope config,state` (seconds, no fence), or `--accept-no-backup` |
| a release step fails after the registry write | the row says `handover` and the tenant may be partly down | read the job's rollback lines; `restore.sh --tenant <t>` starts it, `tenant handover <t> --abandon` clears the row |
| `--take` refuses: "the token does not match" | a stale token, or a newer release | `ragstack-ctl job show <release job id>`; the refusal never echoes the expected value |
| `--take` refuses: "still listening on N" | the release did not free a port | look at the port as the owner; the take must never start a second copy |
| `--take` fails: "came back with FEWER rows" | a store is up but not on its own data | stop it (`tenant stop <t>`), abandon, `restore.sh --tenant <t>`, and look at the instance's binds |
| `--take` fails on the shared-store wait | a store this tenant does not own is not up | start it as its owner (`restore.sh` brings the shared stores up), then take again |
| `--commit` refuses: "nothing to commit" / "only a TAKEN handover" | the take has not run, or it has already been committed | `ragstack-ctl tenant show <t>`: no block means it is already the ctl's |
| `--commit` refuses: "nothing is listening" | the tenant the take started is down | start it (`tenant start <t>`) and commit, or abandon the handover |
| `--commit` refuses: "owned by X … running as Y" | the commit is the account that TOOK it | run it as that account |
| `--abandon` refuses: "released by X … running as Y" | it is gated on `handover.released_by`, not on `owner` | run it as the account that released it |
| `--abandon` refuses: "still running as the other account" | the take's processes are up | `sudo -u svcbvbrc ragstack-ctl tenant stop <t>` first |
| every op demands `--force-with-doctor-diff` | a warning this op actually depends on | read it: the refusal names the code. Warnings the op does NOT depend on are recorded on the job and never block |
