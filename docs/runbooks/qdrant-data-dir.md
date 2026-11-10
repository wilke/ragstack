# Moving a qdrant's data to another filesystem (`/qdrant`)

How to move a tenant's own qdrant, and then the shared qdrant (:6333) and
qdrant2 (:6343), off `/rag` onto the store volume mounted at `/qdrant`, with
a rollback at every step. The ctl side is `stores.qdrant.data_dir` (PR1),
`CTL_STORE_DATA_ROOTS` (PR2) and `ragstack-ctl tenant set-store-dir` (PR3);
the field, the roots and the doctor codes are described in
[ctl-deploy.md § Where a tenant's qdrant keeps its data](ctl-deploy.md#where-a-tenants-qdrant-keeps-its-data-storesqdrantdata_dir).

Two rules hold throughout:

- **The source tree is never modified.** Nothing below writes into, deletes
  from or moves `<data_dir>/qdrant` (or `/rag/data/qdrant`, `/rag/data/qdrant2`)
  until the very last step, which renames it — and that rename waits for a
  fenced backup of the moved store and an owner decision. Never `rsync
  --delete` against a source, never `mv` a store directory.
- **`mountpoint -q /qdrant || exit`** before every step that copies to,
  starts from or repoints at `/qdrant`. If the volume is not mounted,
  `/qdrant/...` is a directory on the root filesystem: an rsync fills `/`, and
  a qdrant started there comes up **empty and healthy-looking** (see
  [the hazard](#the-empty-store-hazard)).

## What has NOT been verified

Read this before the first real move.

- No part of this procedure has been run against a real second filesystem.
  The ctl code is covered by unit tests over fake drivers and temp directories
  on one filesystem; `/qdrant` on coconut has not been used by any of it.
- The backup's EXDEV fallback (qdrant snapshot moved across filesystems by
  copy + remove, PR1) is unit-tested only. The first fenced backup of a moved
  tenant is its first real run.
- The instance supervisor's mount check (deepest mount holding the dir is not
  `/`) reads `/proc/self/mountinfo`; it has not been exercised with `/qdrant`
  unmounted on coconut.
- How the copy runs as **svcbvbrc** is not settled: `ops/coconut/ctl-as-svc.sh`
  runs `ragstack-ctl` only (its `CTL_BIN`), not `rsync`, and whether sudoers
  admits `sudo -n -u svcbvbrc … rsync` on coconut has not been checked. The
  owner decides how a svcbvbrc-owned store is copied (see step 4).
- `restore --as` from a bundle of a moved tenant, and a reboot with a moved
  tenant, have not been run.
- `tenant decommission` (and so `purge`) **refuses** a row that sets
  `stores.qdrant.data_dir` in this release. A migrated tenant is decommissioned
  only after it is moved back (`set-store-dir --clear`).

## Preconditions

All of them, before the first tenant:

1. A ctl release containing PR1 + PR2 + PR3 is deployed as **every**
   `ragstack-ctl` on the host: the daemon, `/rag/bin/ragstack-ctl`, anything
   cron runs. The first row that sets `stores.qdrant.data_dir` makes every
   older binary refuse the whole registry (it decodes with unknown fields
   disallowed — [ctl-deploy.md](ctl-deploy.md)). `set-store-dir` prints a
   one-line reminder after it succeeds.
2. `CTL_STORE_DATA_ROOTS=/qdrant` is in `/rag/config/ctl/ctl.env` and the
   daemon has been restarted (`sudo -n -u svcbvbrc /rag/bin/ctl-daemon.sh
   restart`, inside `script -qec "…" /dev/null` for the tty). A `--direct` run
   reads the same line out of `ctl.env`.
3. `ops/coconut/ctl-as-svc.sh selftest` is green on that build.
4. `mountpoint -q /qdrant` succeeds.
5. `/qdrant` is owned by svcbvbrc, group `cels`, mode `2775`, and svcbvbrc
   has the ACL grant (`ragstack-ctl fleet grant --user svcbvbrc --roots
   /qdrant`, run by the directory's owner).
6. The layout exists, empty:
   - `/qdrant/tenants/<manifest>/` (the copy creates `storage/` and
     `snapshots/` inside it), one per tenant to move — `<manifest>` is the
     row's `manifest_name`;
   - `/qdrant/shared/qdrant/` and `/qdrant/shared/qdrant2/`.
7. Free space on `/qdrant` is at least **1.2 ×** the apparent size of what is
   about to be copied (`du -s --apparent-size <source>`), checked per move.

## The verification primitive: `qdrant-census.sh`

```bash
ops/coconut/qdrant-census.sh <port-or-url>... > census-<label>.json
```

Read-only (GET `/collections`, `/aliases`, `/collections/{c}`; POST
`/collections/{c}/points/count {"exact":true}`). Per collection it prints
name, status, the **exact** `points_count`, `segments_count`, the vector
config and the aliases, sorted and key-sorted, so two censuses of the same
store are byte-identical. Exit 1 on any HTTP error. A census is taken before
the store stops and after it is started from the new directory, and the two
are diffed: **`diff` printing nothing is the pass condition.** On a very large
store raise the per-request timeout with `CENSUS_TIMEOUT=<seconds>` (default
60).

## Per-tenant procedure

For one tenant `<t>` with manifest `<m>`, data dir `<data_dir>` (from
`ragstack-ctl tenant show <t>`) and qdrant HTTP port `<qport>`. Commands for a
svcbvbrc-owned row go through `ops/coconut/ctl-as-svc.sh`; for a row owned by
wilke run `ragstack-ctl … --direct` as wilke. Stamp: `stamp=$(date -u
+%Y%m%dT%H%M%SZ)`.

| # | Step | Rollback from here |
|---|---|---|
| 1 | **Fenced backup.** `ctl-as-svc.sh tenant backup <t> --fence` | nothing changed |
| 2 | **Census before.** `qdrant-census.sh <qport> > census-<t>-before.json` | nothing changed |
| 3 | **Stop the tenant.** `ctl-as-svc.sh tenant stop <t> --yes-destructive <t>` | `tenant start <t>` |
| 4 | **Copy.** `mountpoint -q /qdrant \|\| exit`, then `rsync -aHAX --numeric-ids <data_dir>/qdrant/ /qdrant/tenants/<m>/` **as the account that will own the files** (svcbvbrc for a svcbvbrc row — see *not verified* above; wilke for a wilke row). | `tenant start <t>`; the copy under `/qdrant/tenants/<m>` is inert and may be removed |
| 5 | **Verify the copy.** `rsync -aHAX --numeric-ids --checksum --dry-run --itemize-changes <data_dir>/qdrant/ /qdrant/tenants/<m>/` prints **nothing** (keep `--itemize-changes`: without it or `-v` a dry run prints nothing whatever it finds); `du -s --apparent-size` of both trees is equal; `find <tree> \| wc -l` of both trees is equal. Anything else: do not continue. | as step 4 |
| 6 | **Repoint the row.** `ctl-as-svc.sh tenant set-store-dir <t> --store qdrant --dir /qdrant/tenants/<m> --dry-run`, read the plan, then run it without `--dry-run`. | `tenant set-store-dir <t> --store qdrant --clear`, then `tenant start <t>` |
| 7 | **Start.** `ctl-as-svc.sh tenant start <t>` (a `--direct` run waits; the qdrant step succeeds only once the store answers, and the instance supervisor first re-checks the moved dir and its mount). | `set-store-dir --clear`, `tenant start <t>`, census (below) |
| 8 | **Census after.** `qdrant-census.sh <qport> > census-<t>-after.json; diff census-<t>-before.json census-<t>-after.json` prints nothing. | stop, `set-store-dir --clear`, start, census again — must match *before* |
| 9 | **Doctor.** `ctl-as-svc.sh doctor <t>` shows no `qdrant_data_dir_*` finding and nothing new. | as step 8 |
| 10 | **Soak.** Leave it serving (owner sets the duration); watch the API log and `doctor`. | as step 8 |
| 11 | **Retire the old tree.** `mv <data_dir>/qdrant <data_dir>/qdrant.migrated-$stamp` — a rename on the same filesystem, never a copy-and-delete. | `mv` it back; this must come first before any `--clear` (below) |
| 12 | **Delete the old tree** only after the **next fenced backup** of the moved tenant has succeeded **and** the owner has decided to. | none — this is the one irreversible step |

What `set-store-dir --dir` checks (it refuses, and writes nothing, otherwise):
the row's qdrant is `exclusive`; the qdrant leg is **stopped** (its instance
is in neither the ctl's nor the account's instance table, and nothing listens
on its HTTP or gRPC port); the path is absolute and clean, strictly under a
`CTL_STORE_DATA_ROOTS` entry or under the row's own `data_dir`, and not the
default `<data_dir>/qdrant`; the registry with the new value passes the
per-row contract and the cross-row rule (not another row's qdrant dir, not
inside another row's `data_dir` or qdrant dir); `<dir>`, `<dir>/storage` and
`<dir>/snapshots` are real directories (not symlinks), and outside `/rag` the
deepest mount holding `<dir>` is not `/`. It then writes
`stores.qdrant.data_dir` and `restart_pending` through the normal registry
save (lock, generation check, both validations) and nothing else — no
`last_ops` entry. The job record is the audit trail.

**Rollback** is always the same three commands, and it never touches the
source tree:

```bash
ctl-as-svc.sh tenant stop <t> --yes-destructive <t>     # if it is running
ctl-as-svc.sh tenant set-store-dir <t> --store qdrant --clear
ctl-as-svc.sh tenant start <t>
qdrant-census.sh <qport> | diff census-<t>-before.json -
```

`--clear` refuses unless the qdrant is stopped and
`<data_dir>/qdrant/storage` is a real directory, so after step 11 the rename
has to be undone first. After `--clear` the row no longer carries the field
and `set-store-dir` recorded no `last_ops` key, so an older ctl binary can
read the registry again (provided no other row sets the field).

Two CLI facts that matter here:

- `set-store-dir` is CLI-only and `--direct` only (implied; `--server` is
  refused, and the daemon has no route for it).
- The idempotency key is derived from the request: re-running the **same**
  command line returns the earlier job instead of running again — after a
  `--clear`, a second `--dir` with the same path needs `--new-key`. Setting
  the value the row already has is a no-op success that writes nothing.

## The shared qdrant and qdrant2

The shared stores are not registry rows; `ops/coconut/restore.sh` starts them
from `SHARED_QDRANT_DIR` / `SHARED_QDRANT2_DIR` (environment, else
`/rag/config/stores.env`, else `/rag/data/qdrant` and `/rag/data/qdrant2`).
`stores.env` is parsed, never sourced, and must be a regular file owned by the
account running `restore.sh` (wilke) or root and not group- or
world-writable; `restore.sh` refuses to run otherwise.

**qdrant2 (:6343) first, as the rehearsal**, then qdrant (:6333, ≈1.6 TB).
Each is a two-pass copy so the outage covers only the delta:

1. Preconditions above; `mountpoint -q /qdrant || exit`; free space ≥ 1.2 ×
   `du -s --apparent-size /rag/data/qdrant2`.
2. **Live pass** (store still serving):
   `rsync -aHAX --numeric-ids /rag/data/qdrant2/ /qdrant/shared/qdrant2/`.
   This copy is inconsistent by design; only the delta pass makes it usable.
3. **Announce** the outage to the tenants that use the store (qdrant2:
   lucid / lucid-next; qdrant: asm-next, demo, hackathon's routed collections).
4. `qdrant-census.sh 6343 > census-qdrant2-before.json`.
5. **Stop the tenants that use it, then the store:** each tenant's API
   (`ctl-as-svc.sh tenant stop <t> --only api --yes-destructive <t>`, or the
   tenant's own procedure for a `manual` row), then, as the instance's owner,
   `apptainer instance stop qdrant2`. Confirm nothing listens on :6343.
6. **Delta pass:** `mountpoint -q /qdrant || exit`, then the same rsync again;
   then the step-5 verification of the per-tenant procedure (checksum dry run
   prints nothing; apparent sizes and file counts equal).
7. Add `SHARED_QDRANT2_DIR=/qdrant/shared/qdrant2` to
   `/rag/config/stores.env` (create it if absent: owner wilke or root, mode
   0644 or tighter, not group/world-writable).
8. `mountpoint -q /qdrant || exit`, then `/rag/bin/restore.sh --only stores
   --dry-run` — the qdrant2 binds must name `/qdrant/shared/qdrant2/storage`
   and `/snapshots` — then `/rag/bin/restore.sh --only stores`. It skips the
   stores that already run.
9. `qdrant-census.sh 6343 > census-qdrant2-after.json`; `diff` prints nothing.
10. Restart the tenants' APIs (`ctl-as-svc.sh tenant start <t>`, or their own
    procedure).

**Rollback** (any step): stop the store, **remove the line** from
`stores.env`, `restore.sh --only stores`, census — must match *before*. The
source `/rag/data/qdrant2` was only ever read.

Repeat for qdrant (:6333) with `/rag/data/qdrant`, `/qdrant/shared/qdrant`,
`SHARED_QDRANT_DIR` and port 6333. The live pass of 1.6 TB takes hours; the
delta pass is the outage. Retire the old trees exactly as step 11/12 above:
rename, then delete only after an owner decision.

## The empty-store hazard

`/qdrant` is a mount point. When the volume is not mounted, `/qdrant/...` still
exists as a plain directory on the root filesystem — empty, or holding
whatever an earlier unmounted run wrote there. apptainer binds it without
complaint and qdrant starts on it, **empty, with status green**. Every API
in front of it then answers with zero hits rather than failing.

What guards against it:

- `set-store-dir --dir` and the instance supervisor's start refuse a moved
  dir outside `/rag` whose deepest mount is `/`, and require `storage/` and
  `snapshots/` to exist as real directories; `doctor` reports
  `qdrant_data_dir_root_not_mounted` (error).
- `restore.sh` does **not** check the shared stores' mount: it binds whatever
  `SHARED_QDRANT_DIR` names. Hence the rule, before every copy, start and
  `restore.sh` that touches `/qdrant`: **`mountpoint -q /qdrant || exit`**.
- The census diff after every start is what catches it if everything else
  was skipped: an empty store's census is not the *before* census.
