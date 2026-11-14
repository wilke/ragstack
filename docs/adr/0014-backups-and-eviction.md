# ADR 0014 — Collection backups belong to the owner; eviction needs a current backup, deletion does not

- **Status:** Proposed
- **Date:** 2026-11-13
- **Deciders:** @wilke
- **Amends:** [ADR-0005](0005-tenant-anatomy.md) decision 5. "Whose archive is current"
  becomes "whose backup is current and readable by the server".
- **Related:** [#633](https://github.com/wilke/ragstack/issues/633),
  [#632](https://github.com/wilke/ragstack/issues/632) (`archive_pending`),
  [ADR-0007](0007-tenant-control-plane.md) (whole-tenant backup and restore),
  [ADR-0012](0012-document-chunk-registry.md) (the registry as archive target),
  [ADR-0013](0013-collection-owns-its-data.md)

## Context

**Scope:** this record covers **per-collection** backups. Backing up and restoring a whole
tenant belongs to `ragstack-ctl` (ADR-0007). Its bundles contain the tenant's ACL and
registry database, so they keep ownership by construction.

**The bug:** ingest archives each version to the **caller's** BV-BRC Workspace
(`documents.py`, `_gowe_caller`), but restore reads from the **owner's** (`restore.py`,
`workspace_subject(rec.spec.owner)`). Versions written by an admin or a grantee are skipped
on restore, and an empty owner folder makes the collection `lost` (#633).

**Why it matters:** eviction frees space in a tenant, and it is safe only if the collection
can be reloaded on demand.

## Decision

**1. A backup belongs to the collection's owner, whoever wrote the data.**
- It carries the collection's registry record and access rows, so a replay restores the
  recorded owner and grants. No chunk is relabelled.
- Writes by others land in the owner's backup, never in one under the writer's identity.

**2. Two triggers, one format.**
- A **user backup** is one the owner starts. For BV-BRC users it goes to their Workspace,
  driven by the BV-BRC service layer above the API.
- A **system backup** is one an admin or maintenance starts, on operator storage. That is
  the ADR-0012 registry, where it applies.

**3. Backups are optional. Eviction needs one; deletion does not.**
- A collection may be evicted only if it has a **current** backup **that the server can read
  without the owner's help**. Current means no write of any kind since the backup completed;
  `archive_pending` is that flag, and every writer sets it.
- A collection without such a backup stays resident. At the cap it counts toward the 507.
- Deleting a collection needs no backup.

**4. On transfer, the old backups become snapshots.** They stay with the previous owner and
are not a restore source for the collection under its new owner. The collection is not
evictable until a new current backup exists. The new owner's first use starts one when none
is available.

## Consequences

**Gained:**
- #633 closes.
- Eviction can no longer drop data that exists only in someone's personal storage.

**Accepted:**
- **Writing to the owner's target needs a credential that can reach it**, not the caller's
  token. How the server reaches it is an implementation choice.
- **Collections with only a user backup stay resident** unless the server can read that
  target.
- **Today's per-ingest version deltas** (`ragstack-archive/1`) become one way of producing a
  backup, not a separate mechanism.
