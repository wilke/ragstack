# ADR 0014 — Collection backups belong to the owner; eviction needs a current backup, deletion does not

- **Status:** Proposed
- **Date:** 2026-11-13
- **Deciders:** @wilke
- **Amends:** [ADR-0005](0005-tenant-anatomy.md) decision 5. "Whose archive is current"
  becomes "whose backup is current", as defined below.
- **Related:** [#633](https://github.com/wilke/ragstack/issues/633),
  [#632](https://github.com/wilke/ragstack/issues/632) (`archive_pending`),
  [ADR-0007](0007-tenant-control-plane.md) (whole-tenant backup and restore),
  [ADR-0012](0012-document-chunk-registry.md) (Proposed: the registry as archive target),
  [ADR-0013](0013-collection-owns-its-data.md)

## Context

**Scope:** this record covers **per-collection** backups. A whole tenant is backed up and
restored by `ragstack-ctl` (ADR-0007), whose bundles hold the ACL and registry database and
so keep ownership by construction.

What is wrong today:
- **Ingest and restore look in different places.** Ingest archives each version to the
  **caller's** Workspace with the caller's token (`documents.py`, `_gowe_caller`). Restore
  reads from `spec.owner`'s (`restore.py`, `workspace_subject(rec.spec.owner)`). That field
  is the creator and a transfer never updates it. Versions written by anyone else are
  skipped on restore (#633).
- **Nothing says when a backup is current.** `archive_pending` is set only when a GoWe write
  fails. A document delete never sets it, and nothing clears it. Deleting a document, then
  evicting and restoring, brings the document back.

Eviction frees space in a tenant. It is safe only if the collection can be reloaded.

## Decision

**1. A backup belongs to the collection's owner,** meaning the active owner row
(ADR-0004), whoever wrote the data. Writes by others land in the owner's backup, never in
one under the writer's identity.

**2. Two triggers.**
- A **user backup** is one the owner starts. For BV-BRC users it goes to their Workspace,
  driven by the BV-BRC service layer above the API.
- A **system backup** is one an admin or maintenance starts, on operator storage.

**3. Live access rows win.** A backup carries the collection's record and access rows.
- A **reload** after eviction keeps the live owner and grants, and ignores those in the
  backup, so a revoked grant never comes back.
- The backup's access rows are applied only when an admin replays into an empty collection
  or tenant, i.e. disaster recovery.

**4. Current means no write since the backup.**
- Every write to a collection, deletes and backfills included, advances its write counter.
- A backup records the counter value it covers, and it is **current** while the counter
  still has that value.
- This replaces `archive_pending` as the eviction test.

**5. Backups are optional. Eviction needs a current backup; deletion does not.**
- A collection without a current backup stays resident, and at the cap it counts toward the
  507. Deleting a collection needs no backup.
- A collection backed up only by its owner may be evicted. It reloads when the owner, or
  the BV-BRC layer acting with the owner's token, touches it. Other readers get 503 "not
  loaded" until then.

**6. Transfer.**
- A **dormant** collection cannot be transferred (409) until it is reloaded.
- On transfer of a loaded collection, the previous owner's backups become **snapshots**.
  They stay with that owner and are never a reload source for the collection.
- The collection is not current again until a new backup exists. The new owner's first use
  starts one.

## Consequences

**Gained:**
- #633 closes.
- A deleted document can no longer come back through evict and restore.
- A revoked grant can no longer come back through a reload.

**Accepted:**
- **An evicted personal collection is unavailable to its other readers** until its owner
  returns.
- **Writing to the owner's backup target needs a credential that can reach it,** not the
  caller's. How the server gets one is an implementation choice.
- **Existing archives in writers' Workspaces stay where they are.** They are not used for
  reload. A collection that depends on one needs a fresh backup before it can be evicted.
- **Today's per-ingest version deltas** (`ragstack-archive/1`) become one way of producing a
  backup, not a separate mechanism.

## Alternatives considered

- **Archive under the caller, record each version's subject, and restore from each.**
  Restore would need read access to many people's Workspaces, and would break when one of
  them leaves.
- **Require a server-readable backup for eviction.** Never strands a collection, but
  personal collections could never free space.
- **Let the previous owner's backup serve one reload after a transfer.** That depends on
  the previous owner's storage staying readable. Refusing the transfer while the collection
  is dormant is simpler.
