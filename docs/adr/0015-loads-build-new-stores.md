# ADR 0015 — Loads build new stores, then switch

- **Status:** Proposed
- **Date:** 2026-11-16
- **Deciders:** @wilke
- **Depends on:** [ADR-0013](0013-collection-owns-its-data.md) (schemes, instance keys,
  graph scopes)
- **Related:** [ADR-0002](0002-collection-identity.md) (#285 leftovers),
  [ADR-0005](0005-tenant-anatomy.md) decision 5 (cap and eviction),
  [ADR-0007](0007-tenant-control-plane.md) (tenant restore),
  [ADR-0010](0010-tool-image-binding.md) (release order),
  [ADR-0014](0014-backups-and-eviction.md) (write counter, backup epochs)

## Context

A collection's data is loaded in bulk in six cases: a reload after eviction, a recovery
from damaged stores, a rollback to an earlier backup, a clone, an admin replay, and a move
from the `stamped` to the `instance` scheme (ADR-0013). Loading into the stores that are
serving the collection mixes old and new data and loses both if the load fails. Qdrant
and Elasticsearch could swap through aliases, but Neo4j cannot, and the registry is
already the system of record.

Stores are created implicitly today: startup and create call `ensure_collection` and
`ensure_index`, and Elasticsearch creates a missing index on a bulk write. Several scripts
and workflow inputs address stores by name, with no registry in between.

## Decision

This record applies to collections in the `instance` scheme. A `stamped` collection
reloads, recovers and rolls back into its recorded names, as today (ADR-0013 decision 4).
Ordinary ingest is not a load.

**1. Every load builds a new generation.** A generation is one Qdrant collection and one
ES index. The registry record names the current generation and is the only pointer to
it. Generation stores are created only inside a claimed load: startup, writes and the
store servers never create one. A routed collection whose generation has no route is an
error, never a fallback to the default server.

**2. One load at a time, under a leased claim.** A load first claims the collection's
single slot on the record, recording its generation and any new key before anything is
built. The builder confirms its claim before each batch and stops when the claim is gone.
An expired claim's stores are dropped before its slot is released, so a name is never
forgotten before its store is gone. A claimed collection counts as in flight for eviction.

**3. Writes are admitted and fenced.**
- A write is admitted only while no claim is held. It registers as in flight and names
  the generation it resolved.
- It commits by advancing the write counter (ADR-0014 decision 4), and only while that
  generation is still current. A write that fails the fence is reported as failed;
  retries resolve the generation again.
- A claim waits until admitted writes have finished, or their leases expired, before it
  builds or copies. A clone from live stores holds the same gate on its source.
- Every writer goes through admission, including workflows, scripts and graph
  extraction. Tools that write to stores by name either do or are retired.

**4. Check, then switch atomically.** The build is checked against its source (the backup
manifest, or the source generation) before the switch. The switch succeeds only if the
claim is still held and nothing has evicted, purged, transferred, written to or switched
the collection since the claim. The old generation becomes **retiring**. A failed check or
switch drops the build and leaves the current generation serving.

**5. Drop only what nothing can reach.** A retiring generation is dropped when no
admitted write or claim names it. Readers are not tracked: a reader that finds its
generation gone re-reads the record and retries once. Eviction follows the same rule: the
current generation becomes retiring, and the record stays dormant with no current
generation. A generation change never touches the graph.

**6. Graph during a load.** Graph data belongs to the instance key's scopes (ADR-0013
decision 3), not to a generation.
- A load that keeps the key keeps its triples. After the switch, triples are reconciled to
  the documents and versions the new generation holds. Archived graph legs are loaded only
  where the triples are missing.
- A clone or replay from a backup loads the archived graph legs under the new key;
  versions without one contribute no graph.
- A clone from live stores copies the source's triples under the new key and never changes
  the source's.

**7. Backup epochs** (ADR-0014 decision 4). A new epoch starts on a recovery, a rollback,
a reload that reports skipped versions, and any tenant restore. Graph extraction advances
the write counter, so it makes an earlier backup non-current.

**8. Restores.** A tenant restore-as (ADR-0007) starts a new epoch for every collection.
Keys are re-minted, and stores copied under new names, only when the target shares a
store or graph server with the source. An in-place restore, when ADR-0007 adds one,
refuses to serve a record whose generation is missing and reports stores no record names.

**9. Purge leaves a tombstone.** Purge removes the access rows first, then replaces the
record with a tombstone, keyed by instance key, that names every generation. The id is
free at once. The tombstone is deleted only when every drop is confirmed and no claim is
held. A switch against a tombstone drops its own build.

**10. Capacity.** Building, retiring and tombstoned generations count toward the store
server's physical budget, which counts stores, not rows. A load that finds no room evicts
as ADR-0005 decision 5 does, and is refused if that fails. Requests that trigger a reload
of the same collection join the one claimed load.

**11. Two-release rollout** (ADR-0010). The first release adds the scheme field, keys,
counter fence and claims while every collection stays `stamped`. `instance` collections
are created only from the second release. Once one exists, no deployment downgrades past
the first release.

## Consequences

**Gained:** a failed load leaves the serving stores untouched; old and new data never
share a store; a failed purge leaves a record of what is left; a late write fails visibly
instead of vanishing.

**Accepted:**
- Loading a resident collection needs room for two copies and refuses writes while it
  runs.
- Writers that address stores by name must move behind admission or be retired.
- Store servers must be configured not to create indices on write.
- Readers may see one retry when a switch drops their generation.

## Alternatives considered

- **Load into the serving stores.** Mixes old and new data; a bad backup destroys both.
- **Delete the current stores, then reload.** Loses the data if the backup turns out to be
  bad.
- **Swap through Qdrant and ES aliases.** Neo4j has no aliases, so the graph could not
  follow.
- **Time-based drain** (drop after a fixed delay plus the longest job). No job has a known
  upper bound.
- **Fence at commit only, without admission.** A write that started before a claim can be
  half copied into the new generation.
