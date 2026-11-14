# ADR 0013 — A collection owns its data: chunks are stamped with their collection, not their writer

- **Status:** Proposed
- **Date:** 2026-11-13
- **Deciders:** @wilke
- **Amends:**
  - [ADR-0003](0003-access-control.md) decision 1. The per-chunk stamp stops naming a user;
    the planned `owner_id` rename is dropped.
  - ADR-0003 decision 2b and [ADR-0002](0002-collection-identity.md) decision 5: the shared
    corpus's read-to-write exemption ends.
- **Related:** [#558](https://github.com/wilke/ragstack/issues/558),
  [ADR-0004](0004-users-groups-shares.md), [ADR-0014](0014-backups-and-eviction.md)

## Context

ADR-0003 put access control at the collection and demoted the per-chunk `tenant_id` to
provenance. The demotion never shipped, and the stamp, which holds the writer's subject,
still does five jobs:

1. **Read filter:** reads see `{caller, public}` only (`tenancy.py` `scope_filters`).
2. **Chunk identity:** `stores/qdrant.py` `_point_id` = uuid5(`{stamp}:{chunk_id}`), and
   `stores/elasticsearch.py` `_es_id` follows the same pattern.
3. **Replace, delete and duplicate scope:** these act only on the writer's own chunks
   (`ingestion/pipeline.py`; `DELETE /v1/documents` in `api/routers/documents.py`).
4. **Separation on shared stores:** a routed store may be read by several tenants
   (`collections.py` `_routed_store_legs`). A second registry entry on one store can still
   appear after startup (`ops/evict.py`; the purge guard in `collections.py`).
5. **Hiding leftovers:** a purge removes the access and registry rows before the stores. If
   it fails part-way, a recreated id gets the same deterministic store name (ADR-0002).

Because the stamp is the writer, data disappears without any error whenever the writer is
not the reader:
- after a transfer, the new owner sees nothing (#558);
- chunks written by an admin or a `write` grantee are invisible to the owner, and the owner
  cannot delete or replace them.

## Decision

**1. A collection's data belongs to the collection.** Whoever passes the collection's access
check sees all of its chunks: the owner, grantees including `public`, and admins
(ADR-0003 decisions 2 and 5, ADR-0004). `shared_scope` goes away.

**2. Every chunk is stamped with its collection's instance key, not its writer.**
- The key is the collection id plus a value unique to that creation, recorded on the
  registry record. A recreated id therefore never matches old chunks.
- The key does all five jobs above: reads filter on it, chunk ids derive from it, and
  replace, delete and the duplicate check are scoped by it.
- This keeps a store-side second layer of defence. It also keeps collections apart on
  shared or routed stores, and hides leftovers.

**3. The writer is recorded as provenance.** It is a separate field on the chunk, never
filtered on.

**4. Who may write is unchanged:** the owner, a `write` grantee, or an admin. Whoever
writes, the data belongs to the collection and to its current owner, i.e. the active owner
row (ADR-0004), not `spec.owner`, which records the creator. A transfer moves only that row.

**5. The legacy shared corpus gets a service-account owner and stops taking other people's
ingests.**
- An ADR-0004 decision-7 service account becomes its owner.
- Ingest needs `write`.
- It keeps its writer-stamp filter until it is retired, because chunks users ingested
  there were written as private to them. Until then, users can still delete their own
  chunks there.
- It is deprecated in favour of new `oa` and `asm` collections. At retirement its remaining
  user chunks are deleted with it, after notice.

**6. Migration: new collections only.**
- Collections created or re-ingested after this lands use the collection stamp.
- Existing collections keep the writer stamp and its filter until they are re-ingested,
  rebuilt from a backup, or retired. Their records say which scheme they use.
- There is no bulk rewrite.

## Consequences

**Gained:**
- Transfer, admin ingest and grantee ingest work on new collections.
- One copy per document per collection.
- The owner can delete and replace anything in their collection.
- Isolation on shared stores does not depend on the registry being perfect.

**Accepted:**
- **Two stamp schemes run side by side** until the existing collections are migrated.
- **A `write` grant on a public collection publishes at once.** Whatever the grantee
  ingests is visible to everyone.
- **The graph needs a collection scope.** Neo4j entity identity and graph reads key on the
  writer stamp (`stores/neo4j.py`), and the graph endpoints take no collection. That is an
  API contract change.
- **`/v1/stats/tenants` reports per-stamp counts.** For new collections the stamp is a
  collection key, which changes what that endpoint means. Python and Go must change in
  step.
- **Users cannot ingest into the shared corpus** until `oa` and `asm` exist.

## Alternatives considered

- **Drop the per-chunk filter and rely on the collection check alone.** ADR-0003's plan.
  It leaves jobs 2–5 to separate fixes and removes the second layer of defence.
- **Relabel every chunk on transfer.** A bulk write that cannot be atomic with the owner
  change, and admin and grantee ingest stay broken.
- **Widen `shared_scope` to every recorded writer.** An owner's view would depend on who
  wrote what, and replace and delete stay split.
- **Rewrite all existing data now.** One code path, but a long, risky bulk job on production
  stores.
