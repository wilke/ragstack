# Backups — `ragstack-ctl tenant backup`, `backup list|verify|prune`

What a bundle is, how to take one an operator can rely on, what is in it, what
is deliberately NOT in it, and how to check it.

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
| the gateway | publishes a generation serving the tenant read-only | untouched |
| the API | stopped for the duration, restarted after | left running |
| the manifest | `fenced: true`, `consistent: true` when every count agrees | `fenced: false`, `best_effort: true`, `consistent: false` |
| eligible for `restore --as` | yes | **no, ever** |
| satisfies the prerequisite of `handover` / `migrate-local` / `decommission` | once a restore has verified it | no |

A fenced backup is an OUTAGE for the tenant for as long as it runs. That is the
point: nothing else stops a write landing in qdrant after the snapshot was
taken and before elasticsearch's, which is a bundle that restores into a tenant
whose two stores disagree. The fence is verified — after the API unit stops, a
step checks that nothing is listening on the API port and refuses if something
still is.

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

## 6. When it goes wrong

| symptom | what it means |
|---|---|
| refused: `… under the 5 GiB recovery reserve` | free space on `/rag/backups` before retrying |
| refused: `something is still listening on <port>` | the API did not stop; the fence would have been a lie, so the job stopped instead |
| failed: `PRAGMA integrity_check … answered …, not ok` | a state database is damaged. The bundle was NOT written claiming otherwise; investigate the tenant |
| failed: `the repository at … does not hold the snapshot …` | elasticsearch reported success and the directory disagrees. Do not trust the cluster's snapshot state; look at the ES log |
| a `<id>.partial` directory | an interrupted job. Re-run the backup; `prune --dry-run` lists stale ones after 24 h |
| `secrets.included: false` and you expected otherwise | no recipient in `/rag/config/ctl/backup-recipients.txt` when the bundle was taken (§3) |

Restoring is [`restore --as`](ctl-quickstart.md) — a fresh tenant beside the old
one, never in place. There is no in-place restore in v1.
