# ADR 0015 — Loads build new stores, then switch

- **Status:** Proposed
- **Date:** 2026-11-16
- **Deciders:** @wilke
- **Depends on:** [ADR-0013](0013-collection-owns-its-data.md) (schemes, instance keys,
  graph scopes)
- **Amends:** [ADR-0005](0005-tenant-anatomy.md) decision 5: the cap counts stores, not
  rows (decision 9).
- **Related:** [ADR-0002](0002-collection-identity.md) (#285 leftovers),
  [ADR-0007](0007-tenant-control-plane.md), [ADR-0010](0010-tool-image-binding.md),
  [ADR-0014](0014-backups-and-eviction.md) (write counter, backup epochs)

## Context

A collection's data is loaded in bulk on a reload after eviction, a recovery from damaged
stores, a rollback to an earlier backup, an admin replay, a move to the `instance` scheme
(ADR-0013), and a clone once one exists. Loading into the stores that are serving the
collection mixes old and new data and loses both if the load fails. Qdrant and
Elasticsearch could swap through aliases; Neo4j cannot.

Stores are created implicitly today (startup, create, and Elasticsearch on a bulk write),
and some scripts and workflow inputs address stores by name.

## Decision

**Scope.** Decisions 2, 3, 7, 8 and 9 apply to every collection; a `stamped` collection's
recorded stores are its only generation, replaced only by a move. Decisions 1 and 4–6 apply
when the load's target is `instance`. Ordinary ingest is not a load.

**1. Every load builds a new generation.** A generation is one Qdrant collection and one
ES index, under a reserved name that embeds the instance key. The registry record names
the current generation and is the only pointer to it. Generation stores are created only
inside a claimed load, never by startup, a write or the store server. A routed collection
is served only from its routed servers.

**2. One load at a time, under a leased claim.** A load claims the collection's single
slot, recording its generation and any new key before building. A builder whose claim is
gone stops. When a claim expires, its stores move to the retiring list in the same step
that frees the slot. A claimed collection counts as in flight for eviction.

**3. Writes are admitted and fenced.**
- A write is admitted only while no claim is held, registers as in flight, and names the
  generation it resolved. It commits by advancing the write counter (ADR-0014 decision 4)
  while that generation is still current; otherwise it is reported failed.
- A claim waits until admitted writes have finished or their leases expired. A write whose
  lease expires advances the counter and is reported failed.
- Every writer goes through admission, including workflows, scripts and graph extraction.

**4. Check, then switch atomically.** The build is checked against its source (the backup
manifest, or the source generation). The switch succeeds only if the claim is still held
and nothing evicted, purged, transferred, wrote to or switched the collection since the
claim finished waiting, and, for a move, the collection is still movable (ADR-0013). The
old generation becomes **retiring**. A failed check or switch drops the build.

**5. Drop only what nothing can reach.** A retiring generation is dropped when no admitted
write or claim names it. A reader that finds its generation gone re-reads the record.
Eviction follows the same rule and leaves the record dormant with no current generation.
A clone from live stores fails if its source's generation or counter changed, and counts
as a claim on the source generation until it ends.

**6. Graph during a load.** Graph data belongs to the key's scopes, not to a generation,
so a generation change never touches it.
- A load that keeps the key keeps its triples, reconciled after the switch to the
  documents and chunks the new generation holds. Archived graph legs are loaded only where
  triples are missing.
- A replay or a clone from a backup loads the archived graph legs under the new key.
- A clone from live stores copies the source's triples under the new key, only those the
  cloner can read, and never changes the source's.

**7. Backup epochs** (ADR-0014 decision 4). A new epoch starts on a recovery, a rollback,
a reload that reports skipped versions, and any tenant restore. A tenant restore-as
(ADR-0007) re-mints keys and copies stores only when the target shares a store or graph
server with the source. Graph extraction advances the write counter.

**8. Purge leaves a tombstone.** Purge removes the access rows, then replaces the record
with a tombstone, keyed by instance key, naming every generation and the key's graph
scopes. Purge drops them all. The id is free at once. The tombstone is deleted when every
drop, graph included, is confirmed and no claim is held. A switch against a tombstone
drops its own build.

**9. Capacity.** Building, retiring and tombstoned generations count toward the store
server's budget, which counts stores, not rows. A load that finds no room evicts as
ADR-0005 decision 5 does, and is refused if that fails.

**10. Two-release rollout** (ADR-0010). The first release adds schemes, keys, the counter
fence and claims while every collection stays `stamped`; `instance` collections are created
only from the second. Once one exists, no deployment downgrades past the first release.

## Consequences

**Gained:** a failed load leaves the serving stores untouched; old and new data never
share a store; a failed purge leaves a record of what is left; a late write fails visibly
instead of vanishing.

**Accepted:**
- Loading a resident collection needs room for two copies and refuses writes while it
  runs.
- Writers that address stores by name move behind admission or are retired.
- Readers may retry once when a switch drops their generation.

## Alternatives considered

- **Load into the serving stores, or delete them first.** A bad backup destroys the data.
- **Swap through Qdrant and ES aliases.** Neo4j has no aliases, so the graph could not
  follow.
- **Time-based drain.** No job has a known upper bound.
- **Fence at commit only, without admission.** A write that started before a claim can be
  half copied into the new generation.
