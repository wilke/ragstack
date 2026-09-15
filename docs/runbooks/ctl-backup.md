# Backups and restore — `ragstack-ctl tenant backup|restore`, `backup list|verify|prune`

What a bundle is, how to take one an operator can rely on, what is in it, what
is deliberately NOT in it, how to check it, and how to rebuild a tenant from one
(§6).

**The one-sentence version:** `tenant backup <name> --fence` writes a
self-describing directory under `/rag/backups/tenants/<name>/<ts>-backup/` that
a `restore --as` can rebuild the tenant from; without `--fence` you get a copy
of a moving target that can never be restored from; and unless an age recipient
is configured, the tenant's credentials are NOT in it.

---

## 1. Taking one

```bash
ragstack-ctl tenant backup dev --fence --dry-run     # read the plan first, always
ragstack-ctl tenant backup dev --fence --wait        # ~one step per store, then the manifest
ragstack-ctl tenant backup dev --fence --tar --wait  # …and a .tar of the finished directory beside it
```

**Fenced vs best-effort — the only distinction that matters.**

| | `--fence` | without |
|---|---|---|
| the API | stopped for the duration, restarted after | left running |
| the gateway | untouched — the route answers **502** while the API is down | untouched |
| the manifest | `fenced: true`, `consistent: true` when every count agrees | `fenced: false`, `best_effort: true`, `consistent: false` |
| eligible for `restore --as` | yes | **no, ever** |
| satisfies the prerequisite of `handover` / `migrate-local` / `decommission` | once a restore has verified it | no |

A fenced backup is an OUTAGE for the tenant for as long as it runs. That is the
point: nothing else stops a write landing in qdrant after the snapshot was
taken and before elasticsearch's, which is a bundle that restores into a tenant
whose two stores disagree. The fence is verified — after the API unit stops, a
step checks that nothing is listening on the API port and refuses if something
still is.

**The fence stops the API; it does not degrade the tenant to reads.** There is
no read-only mode in v1: the registry carries no read-only flag, so the gateway
has nothing to render one from, and while the tenant is down its route answers
502. The plan used to carry two "publish a generation serving the tenant
read-only" steps either side of the fence; they published the generation that
was already live and fenced nothing, so they are gone — along with the gateway
lock the backup took for them. Read-only serving is v1.x. Take a fenced backup
in a window where the tenant can be down.

The first step of either kind is a free-space check on `/rag/backups`. It
refuses under a 5 GiB reserve. It does NOT check the plan's "1.2× the size of
what is being copied": the registry records no store sizes and walking four
trees with `du` inside a job holding the tenant lock is exactly the unbounded
work a step must not do. Watch the disk yourself before a large tenant.

## 2. What a bundle contains

```
/rag/backups/tenants/<tenant>/<ts>-backup/
├── manifest.json             the machine-readable description (contracts/ctl/schemas/bundle_manifest.json)
├── SHA256SUMS                <sha256>  <relative path>, one line per file
├── MIGRATE.md                the same thing for a human, with the restore command in it
├── registry-row.json         the registry row as it was
├── config/tenant.env         ONLY for an env_layout: managed tenant (a legacy tenant.env still holds secrets)
├── config/provision.env
├── manifests/*.json          the per-collection manifests
├── units/*                   the systemd units this tenant would run under
├── qdrant/<collection>/<snapshot>
├── elasticsearch/snapshots/<ts>-backup/…
├── state/*.db                VACUUM INTO copies, integrity-checked
├── postgres/<tenant>.dump    only for a stores.postgres.kind: local tenant
├── secrets.age               only when a recipient is configured (§3)
└── parts/*.json              one record per leg: the evidence behind each line of the manifest
```

Two properties to know:

- **It is relocatable.** Every path inside the manifest is relative to
  `RAG_ROOT`; the only absolute paths are inside `registry_row`, which records
  where the tenant *was*. Copy the directory anywhere.
- **`<id>.partial` means unfinished.** The bundle is written under that name and
  renamed as the last step. A directory still called `.partial` belongs to a
  backup that was interrupted: it has no manifest and cannot be restored from.

The manifest also lists what is **not** in the bundle (`external[]`): shared
stores (`:6333`, `:9200`, `qdrant2`), `neo4j`, an external postgres. A tenant on
a shared store cannot be fully recovered from its bundle, and the bundle says so
rather than implying otherwise.

## 3. Secrets — the recipients file

The ctl never writes a credential into a bundle in the clear. The tenant's
`secrets.env`, every historical `secrets.env.bak-*`, and (for a legacy tenant) a
secret-bearing `tenant.env` go into a tar that is **age-encrypted** to the
recipients in:

```
/rag/config/ctl/backup-recipients.txt     # one age public key per line; # comments allowed
```

**With no recipient configured the bundle is still written, without the
secrets**, `secrets.included: false`, and the plan warns before anything runs.
A restore from such a bundle mints fresh credentials; the tenant's current keys
are not recoverable from it. That is the deliberate trade: no bundle ever
carries a plaintext credential, and losing a backup is worse than losing a key
that can be re-minted.

**Generating an identity — the private half never touches coconut:**

```bash
# on YOUR workstation, not on the host
age-keygen -o ~/.config/ragstack-backup.key     # prints: Public key: age1…
chmod 600 ~/.config/ragstack-backup.key
```

Put the **public** key (`age1…`) in `/rag/config/ctl/backup-recipients.txt` on
coconut; keep the file with `AGE-SECRET-KEY-…` in it off the host and in
whatever your team uses for key custody. Two or three recipients is right — one
key whose holder is on leave is a bundle nobody can open.

The daemon holds no identity at all: it can seal a bundle and cannot read one
back. Decrypting is a person, on their own machine:

```bash
age --decrypt -i ~/.config/ragstack-backup.key secrets.age | tar -tv
```

## 4. Listing, verifying, pruning

```bash
ragstack-ctl backup list                       # every tenant
ragstack-ctl backup list dev --json
ragstack-ctl backup verify dev 20260914T093000Z-backup
ragstack-ctl backup prune dev --dry-run
```

`list` reads the manifests on disk — no daemon needed, which matters on the host
you are most likely to be reading backups from. `.partial` directories are shown
as `partial`, and a bundle whose manifest will not parse is shown with the
problem rather than dropped.

`verify` is the **shallow** check, and exits 1 on any of:

- a file that no longer hashes to its `SHA256SUMS` line;
- a file in the bundle that no checksum line covers;
- a manifest missing any member the contract requires, or whose `bundle_id`
  does not match the directory it is in;
- a `SHA256SUMS` whose digest is not the one the manifest records.

It does **not** prove the bundle restores. The deep verify is
`ragstack-ctl tenant restore <tenant> --from <id> --as <fresh-name>`, which
rebuilds a tenant from the bundle and compares the counts against the manifest —
and is the only thing that ever sets `verified: true`.

`prune` **removes nothing in v1**; `--dry-run` is required and the command
refuses without it (exit 3). It prints what a future retention pass would
remove, and why:

- verified `backup` bundles past `keep_last` 7, and verified `pre-update`
  bundles past 2 — oldest first;
- `.partial` directories untouched for more than 24 h;
- the newest verified bundle is never a candidate;
- an **unverified** bundle is never a candidate either: nothing has proved it,
  and it may be the only copy that works.

Deleting is then your own `rm -rf` after reading that list.

## 5. What the registry records

After a successful backup the tenant's row carries:

```json
"last_backup": {"bundle": "/rag/backups/tenants/dev/20260914T093000Z-backup",
                "at": "…", "kind": "backup", "fenced": true, "verified": false},
"last_ops": {"backup": {"job_id": "…", "at": "…", "outcome": "succeeded"}}
```

`bundle` is the absolute directory (registry.json types it as a path); every
operator-facing surface — `backup list`, `restore --from`, the manifest's own
`bundle_id` — speaks the **id**, which is that path's basename. `verified` stays
false until a restore proves it, which is why `handover`, `migrate-local` and
`decommission` refuse on a fresh bundle: their prerequisite is fenced **and**
verified.

## 6. Restore — `ragstack-ctl tenant restore <source> --from <id> --as <new>`

Restore is the **deep verify**. It rebuilds a whole tenant from a bundle, checks
what came back against the manifest, and is the only operation in the control
plane that sets `verified: true` on a backup.

```bash
ragstack-ctl tenant restore dev \
  --from 20260914T093000Z-backup \
  --as dev-restore \
  --yes-destructive dev            # the confirm value is the SOURCE tenant
```

`--wait` is implied: the restored tenant's credentials are minted by the job and
printed **once**, when it succeeds.

### What it does, in order

1. **Verifies the bundle.** Parses `manifest.json`, refuses anything that is not
   `fenced` and `consistent`, re-hashes every file against `SHA256SUMS` in both
   directions, and checks `SHA256SUMS` itself against the digest the manifest
   records. A `.partial` where the bundle should be is named as such.
2. **Builds the fresh tenant** with the same step builder `tenant create` uses —
   allocation, directories, credentials, env files, worktree, UI, units — but
   **not started and not routed**.
3. **Copies the data in**: qdrant snapshots into `<new>/qdrant/snapshots/<c>/`,
   the elasticsearch repository into `<new>/elasticsearch/snapshots/<bundle-id>`,
   the SQLite state files into `<new>/state/`. Streamed, never read into memory.
4. **Starts the stores only**, waits for them, then `Qdrant.Recover` per
   collection, `_restore` of every index from the copied repository, and
   `pg_restore` of the dump (read straight out of the bundle) for a
   postgres-local tenant.
5. **Starts the API**, waits for it, and **verifies**: every collection's point
   count equals the manifest's `points_after`, every index's document count
   equals `docs_after`, and `GET /v1/collections` on the restored tenant equals
   the manifest's inventory. Any disagreement fails the job.
6. **Records it**: `verified: true` in the bundle's own manifest, `verified:
   true` on the source row's `last_backup` (when that record still names this
   bundle), the fresh tenant `active` with `last_ops.restore`, and a gateway
   generation that routes it.

### What it needs before it will plan

| prerequisite | why |
|---|---|
| the `as` name is free | v1 restores side by side; there is no in-place restore |
| the SOURCE row has an `artifact_id`, and that artifact is **prepared on this host** | the fresh tenant is checked out and built from it. `ragstack-ctl fleet artifact prepare --tag <tag>` first |
| the source's relational store is `sqlite` or `local`, not `external` | a bundle holds no dump of a server somebody else runs |

A copy of a **selftest sandbox** (ports in the selftest range) is itself given a
sandbox block, not the next production one: a selftest that restored its own
tenant would otherwise spend a production index on every run, and the copy —
living outside the sandbox range — would need a verified bundle of its own before
`decommission` would clean it up.

Because the BLOCK follows the source and the NAME comes from the request, the
two must agree, and `restore --as` refuses them apart in both directions:
restoring a sandbox as `dev-r` would put a production-named tenant on a selftest
block (which `selftest --sweep` deletes by its block rule), and restoring a
production tenant as `ctltest-…` would put a `ctltest-` name on a production
block — the one combination the sweep refuses and reports. Name a sandbox's copy
`ctltest-<stamp>-r`.

The artifact and the store kind are decided from the **source tenant's registry
row**, because a plan may not read the host. The first step then refuses when the
bundle's manifest names a different artifact or a different store kind — a tenant
restored onto code its data never ran on is not a copy of anything. The store
**images** are checked the same way: a bundle taken from a different pinned
qdrant or elasticsearch digest is refused, and an unpinned digest on either side
skips the check with a line in the job log saying so.

### If it fails

A failed restore rolls back completely: the fresh tenant's registry row is
deleted, its units are stopped and their files removed, and its credential file,
env files and worktree are removed. **Nothing is left running, there is no
registry row and there are no units.**

What REMAINS is the tenant tree, **renamed aside**:

```
/rag/data/tenants/<new>.failed-<ts>/
```

It holds whatever the stores wrote before the failure — the ctl has no recursive
delete, and a rollback is the last place to give it one, so the rule is a rename
and never a deletion. Nothing runs from it and nothing will ever read it again;
remove it by hand once you have looked at it (`rm -rf` is yours to type, not the
control plane's). A sandbox's is swept by `ragstack-ctl selftest --sweep`.

Restoring under the same name works once it is gone, and is **refused while it
is there**: the create half checks that `<data_dir>` is absent or empty before
it makes a directory, so a second attempt can never lay a fresh tenant down on
top of the first one's store files.

The source tenant is never touched except by the last two steps, which only set
the flags saying the bundle has been proved.

| symptom | what it means |
|---|---|
| refused: `bundle … is best_effort (unfenced)` | take a fenced backup; nothing stopped the tenant writing while that one was taken |
| refused: `… SHA256SUMS hashes to … and its manifest says …` | the checksum list was edited after the manifest was written |
| refused: `bundle … was taken from artifact …` | prepare and record that artifact on the source, or restore an older bundle |
| refused: `collection … came back with N point(s) and bundle … recorded M` | the recovery is incomplete; the job rolled back, so nothing is half-restored |
| refused: `the restored tenant reports collections … and bundle … recorded …` | the stores came back but the tenant's own collection registry did not — look at the `state/` copies or the postgres dump |

### What it does NOT reproduce

- **Service accounts.** They are registered through the tenant API, and the
  restored tenant is not started until its data is in. `ragstack-ctl sa create`.
- **Admin subjects.** The registry records their *count*, not the subjects.
  `ragstack-ctl admin add`.
- **The source's key values.** The restored tenant's keys carry the source's
  labels and roles with **fresh** values, delivered once through the job's
  envelope. The source's own values are in the bundle only if it was sealed to
  an age recipient (§3), and a restore never reads them.

## 7. When it goes wrong

| symptom | what it means |
|---|---|
| refused: `… under the 5 GiB recovery reserve` | free space on `/rag/backups` before retrying |
| refused: `something is still listening on <port>` | the API did not stop; the fence would have been a lie, so the job stopped instead |
| failed: `PRAGMA integrity_check … answered …, not ok` | a state database is damaged. The bundle was NOT written claiming otherwise; investigate the tenant |
| failed: `the repository at … does not hold the snapshot …` | elasticsearch reported success and the directory disagrees. Do not trust the cluster's snapshot state; look at the ES log |
| a `<id>.partial` directory | an interrupted job. Re-run the backup; `prune --dry-run` lists stale ones after 24 h |
| a `<tenant>.failed-<ts>` tree under `/rag/data/tenants` | a create or restore that rolled back. Nothing runs from it and no row names it; look at it, then remove it by hand |
| refused: `… already exists and is not empty … remove or rename it first` | a previous create or restore of that name left a tree behind (the row above). Deal with that tree, then retry |
| `secrets.included: false` and you expected otherwise | no recipient in `/rag/config/ctl/backup-recipients.txt` when the bundle was taken (§3) |

Restoring is §6 — a fresh tenant beside the old one, never in place. There is no
in-place restore in v1.
