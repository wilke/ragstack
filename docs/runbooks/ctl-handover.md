# Preparing a tenant for handover — A1…A6

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
`worktree_gitdir_unreadable`, `writable_by_others`, `port_not_listening`,
`stores_unconfirmed`, `boot_cron_missing`, `ctl_account_no_access`. The three
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

1. `npm ci` in `<worktree>/frontend` — **only** when `node_modules` is absent.
   It is the one step in the whole control plane that reaches the network.
2. `vite build --base /ragstack/<t>/ui/` into `<data_dir>/ui/dist.building`.
3. stops that tenant's Vite dev server, found by the port its row records AND
   by identity (cwd under `<worktree>/frontend`, `vite` in the argv). Never by
   process name.
4. one rename: `dist` → `dist.prev-<ts>`, `dist.building` → `dist`. nginx
   serves the old directory until that instant and the new one after it.
5. registry `ui.mode: static`, port cleared.
6. a gateway generation in which the static alias replaces this tenant's
   `$tenant_ui` row.
7. `GET /ragstack/<t>/ui/` through the live gateway, which must answer **200**.

`external --ui-port P` writes the registry and publishes a generation, and
does nothing else at all: no build, nothing started, nothing stopped. It is
for a dev server somebody else runs and goes on running (plan decision D2 —
`dev` keeps its Vite server through and after its handover).

**Clears:** the "instance mode refuses a dev-mode UI" refusal.

**Undo:** the job's rollback restores `dist.prev-<ts>`, puts the mode back and
republishes. By hand afterwards:
`mv <data_dir>/ui/dist{,.bad} && mv <data_dir>/ui/dist.prev-<ts> <data_dir>/ui/dist`,
then `ragstack-ctl tenant set-ui-mode <t> external --ui-port <P>`.

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

Re-verifies each named **exclusive** leg from `/proc` and the listen table
before it writes anything:

* exactly one process serves the leg's port (two pids is two servers; a pid
  this account cannot read is somebody else's);
* that process's argv binds a path under this tenant's own data dir — the
  apptainer `--bind`, because a URL says what the tenant was configured to
  dial and the argv says where the files actually are;
* no other registry row names the same port.

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

The handover itself — `tenant handover --release` as wilke, `--take` and
`--commit` as svcbvbrc — is PR-E2. Until it lands, a prepared tenant goes on
being started by `restore.sh` exactly as before; that is the point of doing
the preparation days ahead.

## If something goes wrong

| Symptom | What it means | What to do |
|---|---|---|
| `set-ui-mode` fails at the probe with 404 | the generation is published but the alias serves nothing | check `<data_dir>/ui/dist/index.html` exists; the job's rollback has already put the previous build back |
| `set-ui-mode` refuses: "the live gateway does not route" | the tenant has no published route yet | `ragstack-ctl gateway apply`, then run it again |
| `set-ui-mode` refuses: "cannot attribute" on the UI port | the dev server belongs to another account | run it as that account, or stop the server by hand first |
| `--confirm-stores` refuses: "not exclusive" | the leg is a shared store | leave it; the tenant is handed over without that capability |
| `--confirm-stores` refuses: "2 different processes" | two servers on one port, or a restart in flight | look at the port, then run it again |
| `backup --scope` refuses: "outage for nothing" | `--fence` with a light scope | drop `--fence`; a light bundle does not need one |
| `doctor --op handover` red on `boot_cron_missing` | nothing would start the tenant after a reboot | `ragstack-ctl fleet enable-boot --cron` as the service account |
| `doctor --op handover` red on `ctl_account_no_access` | svcbvbrc cannot write a managed root | `ragstack-ctl fleet grant --user svcbvbrc` as the path owner |
