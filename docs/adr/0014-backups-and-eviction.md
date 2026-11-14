# ADR 0014 — Backups belong to the owner; eviction needs a current backup, deletion does not

- **Status:** Proposed
- **Date:** 2026-11-13
- **Deciders:** @wilke
- **Amends:** [ADR-0005](0005-tenant-anatomy.md) decision 5. "Whose archive is current"
  becomes "whose backup is current", defined below.
- **Related:** [#633](https://github.com/wilke/ragstack/issues/633) (ingest archives under
  the caller, restore reads the owner), [#632](https://github.com/wilke/ragstack/issues/632)
  (`archive_pending`), [ADR-0012](0012-document-chunk-registry.md) (the registry as archive
  target), [ADR-0013](0013-collection-owns-its-data.md)

## Context

- **Ingest and restore look in different places.** Ingest writes each version to the
  **caller's** BV-BRC Workspace (`documents.py`, `_gowe_caller(principal)`). Restore reads
  from the **owner's** (`restore.py`, `workspace_subject(rec.spec.owner)`). Versions written
  by an admin or a grantee are skipped on restore. If the owner's folder is empty, the
  collection goes `lost`.
- **Only BV-BRC callers trigger a restore.** Restore on first access runs only for a BV-BRC
  bearer (`api/lifecycle.py`). API-key and keyless callers get 503, and the row stays
  `dormant`.
- **There is one kind of archive.** It is written automatically per ingest. Nothing
  distinguishes a backup a user asked for from one the system needs for eviction.

Eviction exists to free space in a tenant. It is safe only if the collection can be
reloaded on demand.

## Decision

**1. There are two kinds of backup.**
- A **user backup** is started by the owner and written to the owner's chosen target. For a
  BV-BRC user that is their Workspace. The BV-BRC service layer above the RAGStack API
  drives Workspace ingest and Workspace backup; the core API does not assume a Workspace.
- A **system backup** is started by an admin or by maintenance, for one collection, a whole
  tenant, or every tenant. Its target is operator-controlled, such as the ADR-0012 registry
  or a filesystem.

**2. A backup belongs to the collection's owner, whoever wrote the data.**
- Writes by a grantee or an admin go into the owner's backup, never into a backup under the
  writer's identity. This fixes #633.
- Writing to the owner's target therefore needs a credential that can reach it: a service
  identity (ADR-0004 decision 7) or a share from the owner. The caller's token is not
  enough.

**3. A backup preserves ownership.** It carries the collection's registry record and its
access rows (owner, grants, `public`) next to the data. Replaying one restores the recorded
owner and grants exactly. The caller's identity is not substituted, and no chunk is
relabelled (ADR-0013 decision 4).

**4. Backups are recommended, not required.** Short-lived tenants and collections may run
without them.

**5. Eviction needs a current backup; deletion does not.**
- A backup is **current** when no write of any kind has landed since it completed: ingest,
  delete, or a backfill by any writer.
- `archive_pending` generalizes to "written since the last backup", and any writer sets it.
- A collection without a current backup is **never evicted**. At the cap it counts toward
  the 507 "nothing evictable" answer.
- Deleting a collection needs no backup.

**6. A reload on demand uses the newest current backup the server can read.**
- A system backup is always readable by the server.
- A user backup is readable only with a credential for its target, i.e. the owner's, or
  the BV-BRC layer's service identity. Without one, the reload answers 503, as today, and
  the row stays `dormant`.
- A collection whose only backup is a user backup is therefore evictable only if the
  deployment can read that target. Otherwise it stays resident.

**7. A transfer turns the old backups into snapshots.**
- The previous owner's backups become read-only **snapshots** and stay with them. They are
  not a restore source for the collection under its new owner.
- The collection has no current backup until the new owner's first one. Until then it is
  not evictable, unless a current system backup covers it.
- The new owner's first use with no backup available starts one automatically.

## Consequences

**Gained:**
- #633 closes: backups follow the owner, not the writer.
- Admin replay of a whole tenant keeps ownership.
- Eviction can no longer drop data that exists nowhere else.

**Accepted:**
- **Reaching the owner's backup target is a new credential path:** a service identity, or
  a share from the owner.
- **Collections without backups never free space.** At the cap, the tenant answers 507
  sooner.
- **The per-ingest version deltas** (`ragstack-archive/1`) become an implementation of a
  system or user backup, not a kind of their own. Whether they keep running on every
  ingest is an implementation choice under decision 5.

## Open questions

- Should a system backup also be written to a user's Workspace, so the user can see it?
  As drafted, no: system backups stay on operator storage.
- Should a snapshot be deleted when its owner leaves, or kept for audit?
