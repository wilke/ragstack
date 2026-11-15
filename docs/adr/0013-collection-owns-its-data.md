# ADR 0013 — A collection owns its data: each collection instance has its own stores

- **Status:** Proposed
- **Date:** 2026-11-14 (revised; first draft 2026-11-13 stamped chunks in shared stores)
- **Deciders:** @wilke
- **Amends:**
  - [ADR-0003](0003-access-control.md) decisions 1 and 2. On new and moved collections the
    per-chunk stamp is no longer a read filter. The planned `owner_id` rename is dropped.
  - ADR-0003 decision 2b and [ADR-0002](0002-collection-identity.md) decision 5: the shared
    corpus's read-to-ingest exemption ends.
  - ADR-0002 decision 1: for new collections the derived name is the base of the store
    name, not the whole of it (decision 2 below).
- **Related:** [#558](https://github.com/wilke/ragstack/issues/558),
  [#350](https://github.com/wilke/ragstack/issues/350),
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
triples are different: Neo4j Community holds every collection's triples in one database,
tagged with the collection's store name and merged on (name, writer, collection)
(`stores/neo4j.py`). Eviction keeps them in place because backups do not hold triples yet
(#350). Graph extraction is switched off on every deployed tenant today.

## Decision

**1. A collection's data belongs to the collection.** On new and moved collections
(decision 5 covers the rest), whoever passes the collection's access check sees all of its
chunks and triples: the owner, grantees including `public`, and admins (ADR-0003 decisions
2 and 5, ADR-0004).

**2. Each collection instance has its own stores.**
- **Instance key:** minted when the collection is created and recorded on the registry
  record. It is globally unique, and never equal to a store name, a collection id or a
  subject. A clone (such as an ADR-0007 restore-as), an admin replay (ADR-0014 decision 3)
  and a move (decision 5) create a new instance with a new key. A reload after eviction, a
  recovery from damaged stores and a rollback keep the key. A recovery or rollback, and a
  reload that reports skipped versions, start a new backup epoch, so no earlier backup
  counts as current (ADR-0014).
- **Vector and text stores:** the Qdrant collection and the ES index belong to one
  generation of the instance. Every load (decision 3) creates a new generation with new
  store names. The registry record holds the current names and is the only pointer to
  them; anything that routes or caches a store keys it by collection id, not by store
  name. A recreated id therefore never meets an earlier instance's leftovers.
- **Graph:** triples carry the instance key as their collection, so they survive a reload
  into new stores and a recreated id never matches old triples. The writer is not part of a
  triple's identity, is not filtered on and does not scope deletes; it is provenance.
  Every graph read and delete names its instance keys. Until another backend exists, all
  keys share the one Neo4j database (decision 6).
- **No read filter:** chunks are not filtered by stamp. The writer is a provenance field.
  Chunk ids do not include the writer, so there is one copy per document. Replace, delete
  and the duplicate check act on the whole collection.
- **Where the key comes from:** always the **target** registry record, never a file header,
  an archive or a source payload.
- **Fixed-name stores stay as they are:** the settings-default collection, stores pinned
  by deployment settings, and the legacy shared corpus keep their names and are never
  rebuilt into generations.

**3. Every load builds new stores, then switches.** This covers a reload after eviction, a
recovery, a rollback, a clone, an admin replay and a move (decision 5).
- Data is never loaded into stores that are serving the collection.
- The new generation is recorded on the registry record as **pending** before anything is
  built, so a crash leaves a named leftover, never an unknown one.
- After the build it is checked. If the check fails it is dropped and the old one stays.
- **Switch:** one registry write that makes the pending generation current and the old one
  **retiring**. It succeeds only if the current generation and the write counter
  (ADR-0014 decision 4) are still what they were when the build started; otherwise the
  build is discarded.
- While a load into a resident collection runs, writes to it are refused. Graph extraction
  counts as a write.
- Every process re-reads a collection's store names from the record within a fixed time
  limit. A retiring generation is dropped only after that limit plus the longest running
  request or job, and only after the current generation is confirmed to exist.
- A building generation does not count as a second collection toward the cap; the
  collection counts once.
- **Graph during loads:**
  - a reload, recovery or rollback keeps the triples, since the key does not change; they
    may be stale until re-extracted;
  - a clone from live stores copies the source's triples under the new key and never
    changes the source's;
  - a move copies them under the new key before the switch, and drops the old key's
    triples with the old generation;
  - a replay, or a clone from a backup, has no graph until backups hold triples (#350).

**4. Who may write is unchanged:** the owner, a `write` grantee, or an admin. Whoever
writes, the data belongs to the collection and to its current owner, i.e. the active owner
row (ADR-0004), not `spec.owner`, which records the creator. A transfer moves only that row.

**5. Existing collections are upgraded by moving them, one at a time, after a risk
check.**
- A move is a load (decision 3) from the collection's own stores into a new instance,
  reusing the stored vectors. It drops the stamp filter for that collection.
- Until moved, a collection keeps its writer stamps and filter, and `shared_scope` stays
  for it. #558 persists there until it moves.
- **Risk check:** a move needs no decision when every chunk is already visible to every
  current reader (every stamp is the owner's or `public`) and its store holds no other
  deployment's rows. Otherwise the owner decides first: accept that the hidden chunks
  become visible, delete them, or leave the collection unmoved.
- A tenant may instead stay on its current release, pinned to its API version, and never
  be upgraded.
- Upgrades are operations on deployed tenants and are run by the deployment owner, not
  by this code change. There is no bulk rewrite; `shared_scope` goes when the last
  writer-stamped collection does.

**6. The graph stays behind an interface keyed by instance key.** Option C (one shared
Neo4j, triples tagged by key) is what ships. A later backend may give a key, or a set of
keys, its own database or graph, so small graphs (for example one per OpenChia episode) can
be created, exported and dropped on their own. Export and drop by key work the same way on
either backend, so the choice of backend is not decided here.

**7. The legacy shared corpus is frozen.** It takes no new chunks; at most its metadata is
updated. It keeps its writer stamps and filter, because chunks users ingested there were
written as private to them. It is replaced by new `oa` and `asm` collections. At
retirement, its remaining user chunks are deleted with it, after notice.

## Consequences

**Gained:** on new and moved collections:
- transfer, admin ingest and grantee ingest work;
- there is one copy per document;
- the owner can delete and replace anything;
- old and new data never share a store, so no per-chunk filter is needed to keep them
  apart;
- a switch can be undone until the old generation is dropped;
- a collection's graph can be exported or dropped by its key.

**Accepted:**
- **Two schemes run side by side** until the last collection is moved or its tenant is
  retired. Multi-collection search and the graph must handle writer-stamped collections
  next to new ones.
- **Moving exposes hidden chunks,** gated by the risk check (decision 5).
- **A move or clone is a new instance,** so earlier backups stop being current. The
  collection stays resident until it is backed up again.
- **Loading a resident collection needs room for two copies,** and refuses writes while it
  runs.
- **Store names can no longer be computed from the build spec alone.** Tools that do so
  read the registry record instead: inventory, protected stores, leftover sweeps, copy,
  the eviction graph drop, and the guard that matches a content-addressed create to an
  existing store.
- **A `write` grant on a public collection publishes at once.**
- **Some row-level separations end for new collections.**
  - Two deployments routed to one store server cannot read each other's new collections.
    That is consistent with "no cross-tenant sharing".
  - `TENANT_COLLECTIONS` still limits which collections a key may reach, but it no longer
    separates rows inside a new collection. Orgs that need separation use separate
    collections or tenants.
- **API contract changes.** The graph endpoints gain a collection scope, and
  `/v1/stats/tenants` changes meaning for new collections. Python and Go change in step.
- **Users cannot ingest into the shared corpus;** they use `oa` and `asm` once those exist.

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
- **One graph database per collection now.** Neo4j Community has one user database;
  Enterprise, Apache AGE or another graph store would add a service before it is needed.
  Decision 6 keeps that path open.
- **One common graph, filtered only at retrieval.** Triples extracted from private chunks
  would still be readable through graph endpoints.
- **Relabel every chunk on transfer.** A bulk write that cannot be atomic with the owner
  change, and admin and grantee ingest stay broken.
- **Widen `shared_scope` to every recorded writer.** An owner's view would depend on who
  wrote what, and replace and delete stay split.
- **Rewrite all existing data now.** One code path, but a long, risky bulk job on production
  stores, with no per-collection exposure check.
