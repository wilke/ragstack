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
ragstack-ctl tenant set-ui-mode dev external --ui-port 8090 --yes-destructive dev
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
* apptainer keeps a **per-account, per-`APPTAINER_CONFIGDIR` instance
  registry**. `apptainer instance list` as svcbvbrc does not show
  `qdrant-hackathon` at all, let alone stop it — and neither does a list made
  as wilke with the control plane's `APPTAINER_CONFIGDIR` exported. The release
  and the abandon act in the releasing account's DEFAULT registry
  (`$HOME/.apptainer`); every other phase acts in the ctl's.

So the owner **releases** and the service account **takes**:

| Phase | Account | Command | What it leaves behind |
|---|---|---|---|
| release | `wilke`, `--direct` | `tenant handover <t> --release` | the tenant DOWN, `state: handover`, a token printed |
| take | `svcbvbrc` | `tenant handover <t> --take --token <T>` | the tenant UP under `supervisor: instance`, `owner: svcbvbrc`, `handover.phase: taken` |
| soak | — | watch it | — |
| commit | `svcbvbrc` | `tenant handover <t> --commit --yes-destructive <t>` | `desired_boot: enabled`, the handover block cleared |
| abandon | `svcbvbrc` then `wilke` | `tenant stop <t>`, then `tenant handover <t> --abandon`, then `restore.sh --tenant <t>` | the tenant back the way it was |
| abandon, after a release that never got taken | `wilke` | `tenant handover <t> --abandon` | the row back to `active` / `manual`; your own processes are left running and nothing needs restarting |

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
  row has carried since `adopt --commit`. (After an abandon, in that order:
  the abandon refuses while a port is still held, so the restore comes last.)

A handover moves **no data**. The same directories serve the same processes
under another account, through the ACLs PR-D2 installed. The risk is not data
loss, it is a tenant neither account can start — which is what the census, the
port proofs and the commit's own liveness check are all for.

#### Before the first release: install the binary

**Do this once, before any tenant is released.** The handover adds a field to
the registry (`handover`), and a registry the NEW binary has written is one an
older `ragstack-ctl` refuses to read — not the one tenant, the whole file:
`registry.Load` decodes strictly, and an unknown key fails the document.

The field is `omitempty`, so a fleet with no handover in flight serialises
nothing and an older binary keeps working — but the moment a release records a
block, every reader has to be new enough to know the name.

```bash
# as the owner, from the checkout
make install-ctl                       # /rag/bin/ragstack-ctl -> ragstack-ctl-<version>
ragstack-ctl version                   # the version you expect

# as the service account: the daemon runs the OLD binary until it restarts
/rag/bin/ctl-as-svc.sh version
/rag/bin/ctl-daemon.sh restart
/rag/bin/ctl-as-svc.sh fleet status    # reads the registry through the new binary
```

`ctl-daemon.sh restart` is safe at any time: it stops no tenant, only the
control plane. Check `fleet status` afterwards — if it answers, every reader on
this host can read what a release is about to write.

## The two conventions

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

That pair is now ENFORCED, not a convention you remember: **any** `--direct`
run, not only a release, refuses before it opens the job store when
`CTL_STATE_DIR` — or the `jobs.db` in it — belongs to another uid, and names the
two variables above in the refusal. A wilke `--direct` run against
`/rag/data/ctl` used to get far enough to create wilke-owned `jobs.db-wal` and
`jobs.db-shm` sidecars beside svcbvbrc's database, after which the daemon logged
`job engine unavailable; the mutation surface will refuse` … `attempt to write a
readonly database (8)` and stayed that way for the rest of its life.

The LOCKS are shared even though the state directories are not. The registry,
manifest and tenant locks live in `/rag/data/tenants/.ctl-locks`, which both
accounts can write through the ACL `ragstack-ctl fleet grant` installs — so
wilke's `--abandon` and svcbvbrc's `tenant restart` really do serialise against
each other, and whichever loses is told the job id, pid and start time of the
one holding it. (Locks derived from each account's own state directory would
be two disjoint sets of files, which serialise nothing.)

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
4. **the instances this release will stop are findable and are the right ones**
   — the last thing that can refuse, and the reason it comes before the row is
   written: each store leg's instance must be in **your own apptainer instance
   registry** (`$HOME/.apptainer` — `apptainer instance list` with no
   `APPTAINER_CONFIGDIR` exported), and the process holding the row's port must
   be that instance or one of its children. A leg whose instance is absent and
   whose port is free is already released and is skipped; a leg whose instance
   is absent while its port is HELD is refused, with the whole tenant still up;
5. **the registry**: `state: handover`, and a `handover` block holding the
   census, the descriptor reference and a one-shot token;
6. **stop the API** by pidfile, after checking cwd and cmdline (never `pkill`),
   TERM then KILL, and **prove the port free**;
7. **stop each of the tenant's own apptainer instances** in reverse start order
   — postgres, elasticsearch, qdrant — in *your* registry, each one waited on
   (up to 120 s) until it has left the instance table AND every port of that
   leg is free, and each followed by its own port proof.

**Step 1 has a companion on a tenant with its own postgres.** The release also
stats that tenant's postgres data directory and prints `postgres data owned by
<uid/account>: the take will copy N MB, and needs 2× that free` — the take has
to copy the tree to own it (see [Postgres-backed
tenants](#postgres-backed-tenants-what-the-take-does-to-the-data-directory)
below) — and it REFUSES when the space is not there. Same reason as step 1:
discovering it afterwards means discovering it with the tenant already down.

It prints the token. Keep it: the take needs it, and `ragstack-ctl job show
<id>` has it in the job's result if the terminal is gone.

From here the tenant is down and the gateway answers 502 for it.

#### The two namespaces, and why step 4 exists

Every apptainer call the control plane makes is namespaced:
`APPTAINER_CONFIGDIR=<CTL_STATE_DIR>/apptainer/config`, so that the daemon's
instances are findable and stoppable from any session. **A release is the one
phase that must not use it.** The tenant it is releasing was started by hand,
so its instances are in *your* registry, and a `stop` aimed at the ctl's looks
in a table that does not contain them — and "not running" is the one answer
`apptainer instance stop` treats as success.

That is exactly what happened on the first real release (2026-09-17): step 9
reported `stopped postgres-hackathon` while the instance (pid 630746) and its
postgres (pid 631059, on 24085) went on running, step 10's port check failed,
and the job rolled back with the tenant's API already down. Compare the two for
yourself:

```bash
apptainer instance list                        # your registry: the tenant's stores
APPTAINER_CONFIGDIR=$CTL_STATE_DIR/apptainer/config apptainer instance list   # the ctl's
```

If step 4 refuses with "the instance … is NOT in the releasing account's own
apptainer instance registry", run those two commands: the store is in whichever
one lists it, and a release must not be run with `APPTAINER_CONFIGDIR` exported
in your shell.

**If the release fails partway** it rolls back what it can, and that now
includes the API: the API stop's rollback **starts the tenant's API again**, as
your account, from the row (the same launch `supervisor: instance` uses —
worktree, `tenant.env` + `secrets.env`, `api.bind`, pidfile). Each instance
stop likewise starts its instance again, in your registry. The registry step
then asks the host: with the API back up it puts `state: active` back and
clears the block, and with the API still down it leaves the honest row
(`handover` / `released`, token intact) and tells you so.

If the restart cannot be made — a row that does not render, a spawn that fails
— the rollback says `ops/coconut/restore.sh --tenant <t>` and does not fail the
job. From a row left at `handover` / `released` you now have two usable verbs:

* **re-run the release.** `--release` over a row still at phase `released` is
  re-entrant: it takes the census again, re-verifies the instances, and keeps
  the token the first release minted (so a `--take` you already copied stays
  valid — the job says so in its warnings and in `result.reentrant`).
* **abandon it.** `--abandon` over such a row is allowed while the ports are
  held by *your own* processes — they are the originals, there is nothing to
  stop — and puts the row back to `state: active`, `supervisor: manual`,
  `owner: you`, block cleared. It still refuses over a port held by a process
  you cannot attribute: that is the take's, and `tenant stop <t>` as the
  service account comes first.

### Take (svcbvbrc)

```bash
/rag/bin/ctl-as-svc.sh tenant handover hackathon --take \
  --token <the token the release printed> --dry-run
/rag/bin/ctl-as-svc.sh tenant handover hackathon --take \
  --token <token> --yes-destructive hackathon
```

(or through the daemon: `POST /v1/tenants/hackathon/ops/handover` with
`{"phase":"take","token":"…"}`.)

It checks the token against the row, proves every port free, records
`supervisor: instance`, **takes ownership of the postgres data directory** when
the tenant has its own and the directory is not already this account's (below —
before any store is started, because postgres will not start on a directory
another account owns), then starts the tenant's own stores, waits for any
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
/rag/bin/ctl-as-svc.sh tenant logs hackathon --file api --lines 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9000/ragstack/hackathon/api/health
```

The acceptance checks, per tenant: the API pid is `svcbvbrc`'s in `/proc`; the
tenant's own instances are listed by the svcbvbrc daemon and by nobody else;
the collection counts match the census; `restore.sh --dry-run` shows the tenant
as mid-handover.

48 hours for `dev` (the drill); a day for the others.

### Commit (svcbvbrc)

```bash
/rag/bin/ctl-as-svc.sh tenant handover hackathon --commit --yes-destructive hackathon
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

What it does NOT clear is `data.pre-handover-<ts>`, if the take made one: the
copy of the postgres data directory the tenant used to run on stays on disk
until you delete it, and `doctor` raises the INFO finding
`pre_handover_copy_present`, naming the path, for as long as it is there.

After the commit `ops/coconut/restore.sh` skips the tenant ("handed over to the
control plane") and `ragstack-ctl fleet start --all` — the `@reboot` crontab
line — is what brings it back.

## Postgres-backed tenants: what the take does to the data directory

Postgres refuses to start on a data directory it does not OWN. It compares that
directory's `st_uid` against its own `geteuid()` and exits; permissions are not
consulted at all, so the ACL `fleet grant` installs — which is what makes every
other part of this handover work — buys nothing here. It just says `data
directory has wrong ownership` and stops.

Qdrant and Elasticsearch do not check, which is why the second failed live
handover (2026-09-17, hackathon) brought both of those up and then sat for the
full three minutes on `postgres did not become ready within 3m0s` with the
tenant down. Nobody on coconut can `chown` to another account — there is no root
— so the only way the service account gets a directory it owns is to write one
itself.

**So the take copies it.** A step after the ports-free proofs and before any
store is started, on a tenant whose row says `stores.postgres.kind: local`:

1. stat the bind source `<data_dir>/postgres/data` and its `pgdata` child — the
   directory postgres actually checks. Both already owned by the taking account:
   it says so and does nothing. That is what the second and every later take of
   the same tenant sees;
2. otherwise it refuses unless the filesystem has **2× the tree's size** free.
   The copy and the original both have to fit, and they both go on existing —
   the original is not removed at the end of the handover;
3. copy the tree to `<data_dir>/postgres/data.<account>-<ts>`, as the taking
   account, so every file in the copy is that account's. Modes are preserved;
   every file and every directory is fsynced, because what is being written is a
   database's data directory and a crash before the metadata lands is a corrupt
   one;
4. two renames, neither of which may clobber: `data` → `data.pre-handover-<ts>`,
   then `data.<account>-<ts>` → `data`.

Both names are checkpointed as external ids BEFORE the copy starts, so a crash
between the two renames is recoverable rather than a puzzle: a re-run finishes
the second rename, and a state it cannot decide is reported as `stuck` naming
both paths rather than guessed at. The pre-handover path is recorded in the row,
at `handover.postgres_data.pre_handover`, beside `copy` and `migrated_at`.

**The original is never written to** — it is renamed, not opened — so the take's
rollback is exact: it swaps the two names back and the tenant restarts on the
bytes it always had.

`--dry-run` shows the step with its real numbers, and so does the release's
precondition one phase earlier — which is the one you want, because it runs
while the tenant is still up. `hackathon` today (postgres on 24085, svcbvbrc
taking):

```bash
du -sh /rag/data/tenants/hackathon/postgres/data          # 47M → needs 94M free
df -h  /rag/data/tenants/hackathon/postgres
stat -c '%U %A %n' /rag/data/tenants/hackathon/postgres/data{,/pgdata}
# wilke drwx------ /rag/data/tenants/hackathon/postgres/data
# wilke drwx------ /rag/data/tenants/hackathon/postgres/data/pgdata
```

47 MB is seconds. A postgres grown to tens of gigabytes is a different
conversation — read the release's number before you release, not after.

### The pre-handover directory

`--commit` does **not** delete `data.pre-handover-<ts>`. A commit is the moment
the tenant is the control plane's, not the moment you are sure of it, so the
copy stays and `doctor` raises an INFO finding, `pre_handover_copy_present`,
naming the path for as long as it is there. It does not measure it — `du` over a
postgres data directory is IO doctor would pay on every poll — so run `du -sh`
yourself before you decide.

Delete it by hand, as the account that owns it — `wilke`; svcbvbrc cannot —
once the tenant has soaked and been committed:

```bash
rm -rf /rag/data/tenants/hackathon/postgres/data.pre-handover-<ts>
```

Only after the commit. Until then that directory IS the way back: `--abandon`
swaps the two names again, when the take's copy is in place and the original is
still there, so an abandoned tenant restarts on the directory it always had,
owned by the account that is about to start it.

### The readiness wait fails fast

When the postgres instance is gone from the ctl's instance registry — it exited
— the readiness wait no longer burns three minutes on a process that is not
there. It reads the instance's own `.err` log and fails at once, quoting
postgres's reason:

```bash
# apptainer writes it under $APPTAINER_CONFIGDIR; for the daemon account that is
ls /rag/data/ctl/apptainer/config/instances/logs/$(hostname)/svcbvbrc/postgres-hackathon.err
```

So the ownership failure that cost five minutes of downtime now costs the second
postgres takes to say `data directory has wrong ownership`.

### The rollback drill

Run it once, on `dev`, before trusting it on anything else. It is the whole
reason the protocol has an abandon.

```bash
# 1. release, take (as above), then decide against it.

# 2. the service account stops what it started. Idempotent.
/rag/bin/ctl-as-svc.sh tenant stop dev --yes-destructive dev

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

One exception, on a tenant with its own postgres: the abandon also swaps the
data directory back — `data` → the take's copy, `data.pre-handover-<ts>` →
`data` — so step 4 starts the tenant on the directory its own account has owned
all along. It does that only when the take's copy is in place and the original
is still there; otherwise it touches neither and says which one it found.

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
/rag/bin/ctl-as-svc.sh tenant handover hackathon --take --token <T> --yes-destructive hackathon
# soak, then
/rag/bin/ctl-as-svc.sh tenant handover hackathon --commit --yes-destructive hackathon
```

**dev** — the full drill, and the one tenant that keeps its Vite dev server
(plan decision D2: `ui.mode: external` on 8090, run by wilke, untouched by the
ctl). Its stores are its own; its relational state is SQLite, so there is no
`env pg-password` step.

```bash
ragstack-ctl tenant backup dev --scope config,state --yes
export CTL_STATE_DIR=/rag/data/ctl-selftest CTL_CONFIG_DIR=/rag/config/ctl-selftest
ragstack-ctl tenant handover dev --release --yes-destructive dev
/rag/bin/ctl-as-svc.sh tenant handover dev --take --token <T> --yes-destructive dev
# → the rollback drill above, in full →
# then release and take again, soak 48 h, and
/rag/bin/ctl-as-svc.sh tenant handover dev --commit --yes-destructive dev
```

demo, lucid-next and asm-next follow a soak day apart. Their shared stores stay
wilke-run: only their APIs move (and lucid-next's own elasticsearch). The take's
shared-store wait is what makes their boot order irrelevant.

### Reading a handover off a fleet

```bash
ragstack-ctl fleet status
ragstack-ctl tenant show <t>
```

A released tenant reads `state: handover`; a taken one reads `state: active`
with `handover_phase: taken` — it is up, supervised by the ctl, and still owes
a commit or an abandon. The `handover_phase` column is the only place that
distinction shows, and the handover BLOCK is deliberately not on any read
surface: it carries the hand-off token, and no read surface of this control
plane carries a token. The token lives in the result of the release job that
minted it (`ragstack-ctl job show <id>`).

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
/rag/bin/ctl-as-svc.sh key mint <t> gowe --role user --restart --prove --yes
/rag/bin/ctl-as-svc.sh key revoke <t> gowe --restart --prove --yes-destructive <t>
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
| `--release` refuses: "is NOT in the releasing account's own apptainer instance registry" | this job is looking in a different apptainer instance registry from the one the tenant's stores are in — almost always `APPTAINER_CONFIGDIR` exported in your shell | compare `apptainer instance list` with `APPTAINER_CONFIGDIR=$CTL_STATE_DIR/apptainer/config apptainer instance list`, `unset APPTAINER_CONFIGDIR`, run it again. **Nothing was stopped**: the check runs before the row is written |
| `--release` refuses: "does not descend from the instance" | the port the row names is held by a process outside that instance's family — a reused instance name | look at `apptainer instance list` and the row's `stores.*` ports before going on; do not stop anything by hand |
| `--release` fails: "still in … after its stop" / "still held after" | the instance stop returned but the instance or a port outlived it (an elasticsearch flushing a translog, a wedged container) | wait, look at the port, then **re-run the release** — it is re-entrant |
| a release step fails after the registry write | the row keeps `handover` and the tenant is down | read the job's rollback lines — the API stop's rollback starts the API again and each instance stop restarts its instance, so the tenant is often back up already. Then either **re-run `--release`** (re-entrant, same token) or: `tenant stop <t>` as the service account if the take had already run, `tenant handover <t> --abandon` here, then `restore.sh --tenant <t>` |
| the row says `handover` / `released` and the tenant is RUNNING | a release that failed, whose processes are back (by its own rollback or by `restore.sh`) | `tenant handover <t> --release` to try again with the same token, or `tenant handover <t> --abandon` to put the row back — both are allowed over your own running processes now |
| `--take` refuses: "the token does not match" | a stale token, or a newer release | `ragstack-ctl job show <release job id>`; the refusal never echoes the expected value |
| `--take` refuses: "still listening on N" | the release did not free a port | look at the port as the owner; the take must never start a second copy |
| `--take` fails: "came back with FEWER rows" | a store is up but not on its own data | `tenant stop <t>` as the service account, then `--abandon`, then `restore.sh --tenant <t>` — and look at the instance's binds before trying again |
| `--take` fails on the shared-store wait | a store this tenant does not own is not up | start it as its owner (`restore.sh` brings the shared stores up), then take again |
| `--take` fails: "postgres did not become ready", quoting `data directory has wrong ownership` | postgres compares the data directory's `st_uid` against its own uid, not permissions — the ACL is irrelevant to it | the migration step did not run or did not finish: read `handover.postgres_data` in the row and the `.err` path the failure names. The rollback has already swapped the directory names back, so `tenant stop <t>`, `--abandon`, `restore.sh --tenant <t>`, then fix the ownership before taking again |
| `--take` refuses: "needs 2× … free" | the copy of the postgres data directory and the original both have to fit | free space on that filesystem, or `rm -rf` a `data.pre-handover-<ts>` left by an earlier, committed handover. The release prints the same number before anything is stopped — read it there |
| the job reports `stuck`, naming `data.pre-handover-<ts>` and `data.<account>-<ts>` | a crash between the two renames; the step will not guess which half happened | look at those two directories and at `data`, then **re-run the take**: the external ids checkpointed before the copy let the job's reconcile finish the second rename. Do not rename them by hand |
| `--commit` refuses: "nothing to commit" / "only a TAKEN handover" | the take has not run, or it has already been committed | `ragstack-ctl tenant show <t>`: no block means it is already the ctl's |
| `--commit` refuses: "nothing is listening" | the tenant the take started is down | start it (`tenant start <t>`) and commit, or abandon the handover |
| `--commit` refuses: "owned by X … running as Y" | the commit is the account that TOOK it | run it as that account |
| `--abandon` refuses: "released by X … running as Y" | it is gated on `handover.released_by`, not on `owner` | run it as the account that released it |
| `--abandon` refuses: "still running as the other account" | the take's processes are up | `/rag/bin/ctl-as-svc.sh tenant stop <t>` first |
| `--abandon` refuses: "is not in the releasing account's own apptainer instance registry" | a store's port is held by your account but the instance is not one this account can stop by name | `apptainer instance list`; an abandon hands the tenant back as a hand-started one, so every leg has to be one |
| any `--direct` run refuses: "state directory … owned by" another account | `CTL_STATE_DIR` is the daemon's — and a wilke run there leaves sidecar files that break the daemon's job store | `export CTL_STATE_DIR=/rag/data/ctl-selftest CTL_CONFIG_DIR=/rag/config/ctl-selftest` and run it again |
| `doctor` red on `job_engine_unavailable`; the daemon refuses every mutation | its job store could not be opened — usually foreign-owned `jobs.db-wal` / `jobs.db-shm` beside `/rag/data/ctl/jobs.db` | `curl -s localhost:<port>/health \| jq .engine`; fix the sidecars' modes (or remove them with the daemon stopped). The daemon retries the open in the background — each attempt logged — and serves mutations again as soon as it succeeds; no restart needed |
| every op demands `--force-with-doctor-diff` | a warning this op actually depends on | read it: the refusal names the code. Warnings the op does NOT depend on are recorded on the job and never block |
