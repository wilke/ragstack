# ADR 0014 — Collection backups belong to the owner; eviction needs a current backup, deletion does not

- **Status:** Proposed
- **Date:** 2026-11-13
- **Deciders:** @wilke
- **Amends:** [ADR-0005](0005-tenant-anatomy.md) decision 5, in two places:
  - "Whose archive is current" becomes "whose backup is current", as defined below.
  - "A dormant collection restores on first access" becomes "on first access, using a
    credential that can read its backup". For a system backup any authorized reader triggers
    the reload; for an owner-only backup, only the owner does.
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

**1. A user backup belongs to the collection's owner,** meaning the active owner row
(ADR-0004). Writes by others are included in the owner's next backup, never in one under
the writer's identity. System backups belong to the operator. Both kinds carry the
collection's ownership.

**2. Two triggers.**
- A **user backup** is one the owner starts. For BV-BRC users it goes to their Workspace,
  driven by the BV-BRC service layer above the API.
- A **system backup** is one an admin or maintenance starts, on operator storage (for
  example the ADR-0012 registry). It stays a reload source across a transfer.

**3. Live access rows win.** A backup carries the collection's record and access rows.
- A reload after eviction keeps the live owner and grants, so a revoked grant never comes
  back.
- The backup's access rows are applied only when an admin replays into an empty collection
  or tenant. A replay creates a new instance (ADR-0013 decision 2).

**4. Current means same instance, no write since the backup.**
- Every committed write advances the collection's write counter. That covers every writer
  (API, workflows, scripts), and deletes and backfills too. The counter never decreases.
- A backup records the collection's instance key (ADR-0013), its backup epoch, and the
  counter value read when it started. It is **current** while all three still match. A
  recovery starts a new epoch (ADR-0013 decision 2).
- A reload restores from the backup that was current at eviction, and leaves the counter
  unchanged.
- This replaces `archive_pending` as the eviction test.

**5. Backups are optional. Eviction needs a current backup; deletion does not.**
- A collection without a current backup stays resident, and at the cap it counts toward the
  507.
- A **system** backup makes any collection evictable.
- A backup held **only by the owner** makes a collection evictable only while it is
  **private**, with no grantees and no `public`. It reloads when the owner, or the BV-BRC
  layer acting with the owner's token, touches it.
- An owner-only backup is reloaded only with the owner's credential, and a system backup
  with the server's. A non-owner's credential is never used, so a non-owner can never push a
  collection to `lost`.
- Deleting a collection needs no backup.

**6. Sharing or transferring a dormant collection requires reloading it first** (409
otherwise).
- On transfer, the previous owner's user backups become **snapshots**. They stay with that
  owner, and they are never a reload or replay source for the collection.
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
- **Writing to or reading from the owner's backup target needs a credential that can reach
  it,** not the caller's. How the server gets one is an implementation choice.
- **Rollout:**
  - Existing archives in the current owner's Workspace can reload collections that are
    already dormant when this lands. They never make a resident collection evictable; a
    resident collection needs a new backup first.
  - Versions held only in other writers' Workspaces are not reloaded. Such a reload reports
    what it skipped and flags the collection as incomplete (#633).
  - A collection that is dormant and shared when this lands is reloaded by an operator, with
    a credential that can read the owner's archive, or given a system backup, before the
    private-only rule applies.
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
