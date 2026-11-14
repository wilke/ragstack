# ADR 0014 — Collection backups belong to the owner; eviction needs a current backup, deletion does not

- **Status:** Proposed
- **Date:** 2026-11-13
- **Deciders:** @wilke
- **Amends:** [ADR-0005](0005-tenant-anatomy.md) decision 5, in two places:
  - "Whose archive is current" becomes "whose backup is current", as defined below.
  - "A dormant collection restores on first access" becomes "on first access by someone who
    can read its backup".
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
  and graph extraction read from `spec.owner`'s (`restore.py`, `graph_extract.py`). That
  field is the creator, and a transfer never updates it. Versions written by anyone else
  are skipped on restore (#633).
- **Restore runs with the caller's token.** A non-owner who cannot read the owner's folder
  can push the collection to `lost` for everyone (`api/lifecycle.py`, `restore.py`).
- **Nothing says when a backup is current.** `archive_pending` is set only when a GoWe write
  fails, a document delete never sets it, and nothing clears it. Deleting a document, then
  evicting and restoring, brings it back.

Eviction frees space in a tenant. It is safe only if the collection can be reloaded.

## Decision

**1. A backup belongs to the collection's owner,** meaning the active owner row
(ADR-0004). Writes by others are included in the owner's next backup, never in one under
the writer's identity.

**2. Two triggers.**
- A **user backup** is one the owner starts. For BV-BRC users it goes to their Workspace,
  driven by the BV-BRC service layer above the API.
- A **system backup** is one an admin or maintenance starts, on operator storage. It stays
  a reload source across a transfer.

**3. Live access rows win.** A backup carries the collection's record and access rows.
- A reload after eviction keeps the live owner and grants, so a revoked grant never comes
  back.
- The backup's access rows are applied only when an admin replays into an empty collection
  or tenant.

**4. Current means no write since the backup.**
- Every committed write advances the collection's write counter. That covers every writer
  (API, workflows, scripts), and deletes and backfills too.
- A backup is current while the counter equals the value read when the backup started.
- A reload sets the counter to the backup's value.
- This replaces `archive_pending` as the eviction test.

**5. Backups are optional. Eviction needs a current backup; deletion does not.**
- A collection without a current backup stays resident, and at the cap it counts toward the
  507.
- A **system** backup makes any collection evictable.
- A backup held **only by the owner** makes a collection evictable only while it is
  **private**, with no grantees and no `public`. It reloads when the owner, or the BV-BRC
  layer acting with the owner's token, touches it.
- A reload is never attempted with a non-owner's credential, so a non-owner can never push a
  collection to `lost`.
- Deleting a collection needs no backup.

**6. Sharing or transferring a dormant collection requires reloading it first** (409
otherwise).
- On transfer, the previous owner's user backups become **snapshots**. They stay with that
  owner and are never a reload source for the collection.
- The collection is current again once the new owner backs it up (the BV-BRC layer may do
  this on first use), or once a system backup exists.

## Consequences

**Gained:**
- #633 closes.
- A deleted document can no longer come back through evict and restore.
- A revoked grant can no longer come back through a reload.
- Shared data can never be stranded by its owner's absence.

**Accepted:**
- **A private collection whose owner never returns can only be deleted,** unless a system
  backup exists.
- **Writing to the owner's backup target needs a credential that can reach it,** not the
  caller's. How the server gets one is an implementation choice.
- **Existing archives in writers' Workspaces stay where they are.** They are not used for
  reload, and a collection that depends on one needs a fresh backup before it can be
  evicted.
- **Retention is not decided here.** User backups and snapshots follow the owner's own
  storage. System backups follow operator policy.
- **Today's per-ingest version deltas** (`ragstack-archive/1`) become one way of producing a
  backup, not a separate mechanism.

## Alternatives considered

- **Archive under the caller, record each version's subject, and restore from each.**
  Restore would need read access to many people's Workspaces, and would break when one of
  them leaves.
- **Require a system backup for every eviction.** Never strands anything, but personal
  collections could never free space.
- **Let owner-only backups make shared collections evictable too.** Grantees could lose
  access for good if the owner leaves.
- **Let the previous owner's backup serve one reload after a transfer.** That depends on
  the previous owner's storage staying readable. Requiring a reload before transfer is
  simpler.
