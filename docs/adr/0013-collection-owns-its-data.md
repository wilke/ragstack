# ADR 0013 — A collection owns its data: each collection instance has its own stores

- **Status:** Proposed
- **Date:** 2026-11-14 (revised; first draft 2026-11-13 stamped chunks in shared stores)
- **Deciders:** @wilke
- **Amends:**
  - [ADR-0003](0003-access-control.md) decisions 1 and 2. On new collections the per-chunk
    stamp is no longer a read filter. The planned `owner_id` rename is dropped.
  - ADR-0003 decision 2b and [ADR-0002](0002-collection-identity.md) decision 5: the shared
    corpus's read-to-ingest exemption ends.
  - ADR-0002 decision 1: for new collections the derived name is the base of the store
    name, not the whole of it (decision 2 below).
- **Related:** [#558](https://github.com/wilke/ragstack/issues/558),
  [ADR-0004](0004-users-groups-shares.md), [ADR-0014](0014-backups-and-eviction.md)

## Context

ADR-0003 put access control at the collection and demoted the per-chunk `tenant_id` to
provenance. The demotion never shipped. The stamp, which holds the writer's subject, still
does five jobs:

1. **Read filter:** a read sees `{caller, public}`. `api/scope.py` `shared_scope` adds the
   owner's stamp for a grantee (`tenancy.py` `scope_filters`).
2. **Chunk identity:** `stores/qdrant.py` `_point_id` = uuid5(`{stamp}:{chunk_id}`), and
   `stores/elasticsearch.py` `_es_id` follows the same pattern.
3. **Replace, delete and duplicate scope:** these act only on the writer's own chunks
   (`ingestion/pipeline.py`; `DELETE /v1/documents` in `api/routers/documents.py`).
4. **Separation on shared stores:** a routed store may hold more than one deployment's data
   (`collections.py` `_routed_store_legs`). A second registry entry on one store can still
   appear after startup (`ops/evict.py`).
5. **Hiding leftovers:** a purge removes the access and registry rows before the stores. If
   it fails part-way, a recreated id gets the same deterministic store name (ADR-0002).

Because the stamp is the writer, data disappears without any error whenever the writer is
not the reader:
- after a transfer, the new owner sees nothing (#558);
- chunks written by an admin or a `write` grantee are invisible to the owner, and the owner
  cannot delete or replace them.

Jobs 2–5 exist only because different data can end up in one physical store. Graph
triples are different: Neo4j holds every collection's triples in one database, tagged
with the collection's store name (`stores/neo4j.py`), and eviction keeps them in place
because backups do not hold triples yet (#350).

## Decision

**1. A collection's data belongs to the collection.** On new collections (decision 5
covers the rest), whoever passes the collection's access check sees all of its chunks: the
owner, grantees including `public`, and admins (ADR-0003 decisions 2 and 5, ADR-0004).

**2. Each collection instance has its own stores.**
- **Instance key:** minted when the collection is created, unique to that creation, and
  recorded on the registry record. A clone (such as an ADR-0007 restore-as), an admin replay
  (ADR-0014 decision 3) and a move (decision 5) create a new instance with a new key. A
  reload after eviction and a recovery keep the key; a recovery starts a new backup epoch,
  so no backup taken before it counts as current (ADR-0014).
- **Vector and text stores:** the Qdrant collection and the ES index are named from the
  ADR-0002 derived name plus the instance and a store generation. Every load (decision 3)
  creates a new generation. The registry record holds the current names and is the only
  pointer to them. A recreated id therefore never meets an earlier instance's leftovers.
- **Graph:** triples carry the instance key as their collection, so they survive a reload
  into new stores, and a recreated id never matches old triples.
- **No read filter:** on these collections chunks are not filtered by stamp. The writer is
  recorded as a separate provenance field. Replace, delete and the duplicate check act on
  the whole collection.
- **Where the key comes from:** always the **target** registry record, never a file header,
  an archive or a source payload.

**3. Every load builds new stores, then switches.** This covers a reload after eviction, a
recovery from damaged stores, a rollback to an earlier backup, a clone, an admin replay
and a move (decision 5).
- Data is never loaded into stores that are serving the collection.
- The order is: build the new generation, check it, switch the registry record to it in one
  write, then drop the old generation once no request can still be using it.
- If the check fails, the new generation is dropped and the old one stays.
- While a resident collection is being reloaded or moved, writes to it are refused. The
  build starts only when no write is open.
- A reload keeps the graph, since the key does not change. A clone, replay or move copies
  the triples under the new key before the switch, and the old key's triples are dropped
  with the old generation.

**4. Who may write is unchanged:** the owner, a `write` grantee, or an admin. Whoever
writes, the data belongs to the collection and to its current owner, i.e. the active owner
row (ADR-0004), not `spec.owner`, which records the creator. A transfer moves only that row.

**5. Existing collections stay on the writer stamp until moved.**
- They keep their filter, and `shared_scope` stays for them. #558 persists there until they
  move.
- A move is a load (decision 3) from the collection's own stores into a new instance,
  reusing the stored vectors.
- Re-ingesting single documents never moves a collection.
- There is no bulk rewrite. `shared_scope` goes when the last writer-stamped collection
  does.

**6. The legacy shared corpus gets a service-account owner and stops taking other people's
ingests.**
- An ADR-0004 decision-7 service account becomes its owner. Ingest needs `write` and is
  stamped `public`.
- It keeps its writer stamps and filter until it is retired, because chunks users ingested
  there were written as private to them.
- Users can still delete their own chunks there. The owner's and admins' deletes span all
  stamps.
- It is deprecated in favour of new `oa` and `asm` collections. At retirement, its remaining
  user chunks are deleted with it, after notice.

## Consequences

**Gained:** on new and moved collections:
- transfer, admin ingest and grantee ingest work;
- there is one copy per document;
- the owner can delete and replace anything;
- old and new data never share a store, so no per-chunk filter is needed to keep them
  apart;
- a switch can be undone until the old generation is dropped.

**Accepted:**
- **Two schemes run side by side.** Multi-collection search and the graph must handle
  writer-stamped collections next to new ones.
- **Moving exposes hidden chunks.** Moving an existing collection makes the chunks that
  grantees and admins wrote visible to all of its readers.
- **A move or clone is a new instance,** so earlier backups stop being current. The
  collection stays resident until it is backed up again.
- **Loading a resident collection needs room for two copies,** and refuses writes while it
  runs.
- **Store names can no longer be computed from the build spec alone.** Tools that do so
  (inventory, protected stores, leftover sweeps, copy) read the registry record instead. A
  generation left behind by a failed drop is recognisable by its name.
- **A `write` grant on a public collection publishes at once.**
- **Some row-level separations end for new collections.**
  - Two deployments routed to one store server cannot read each other's new collections.
    That is consistent with "no cross-tenant sharing".
  - `TENANT_COLLECTIONS` still limits which collections a key may reach, but it no longer
    separates rows inside a new collection. Orgs that need separation use separate
    collections or tenants.
- **API contract changes.** The graph endpoints gain a collection scope, and
  `/v1/stats/tenants` changes meaning for new collections. Python and Go change in step.
- **Users cannot ingest into the shared corpus** until `oa` and `asm` exist.

## Alternatives considered

- **Stamp every chunk with the instance key and keep instances in shared stores.** The
  first draft of this record. It keeps the read filter, mixes old and new copies in one
  store during a rebuild, and needs a stamp-by-stamp purge.
- **Swap through Qdrant and ES aliases.** Neo4j has no aliases, so the graph could not
  follow, and the registry is already the system of record.
- **Delete the current stores, then reload.** Loses the data if the backup turns out to be
  bad.
- **Drop the per-chunk filter but keep one deterministic store per collection id.**
  ADR-0003's plan. Leftovers of a failed purge would reach a recreated id, and loads would
  still mix old and new data in one store.
- **Relabel every chunk on transfer.** A bulk write that cannot be atomic with the owner
  change, and admin and grantee ingest stay broken.
- **Widen `shared_scope` to every recorded writer.** An owner's view would depend on who
  wrote what, and replace and delete stay split.
- **Rewrite all existing data now.** One code path, but a long, risky bulk job on production
  stores.
