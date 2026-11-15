# ADR 0013 — A collection owns its data: each collection instance has its own stores

- **Status:** Proposed
- **Date:** 2026-11-14 (revised; first draft 2026-11-13 stamped chunks in shared stores)
- **Deciders:** @wilke
- **Amends:**
  - [ADR-0003](0003-access-control.md) decisions 1 and 2. On collections in the instance
    scheme (decision 2) the per-chunk stamp is no longer a read filter. The planned
    `owner_id` rename is dropped.
  - ADR-0003 decision 2b and [ADR-0002](0002-collection-identity.md) decision 5: the shared
    corpus's read-to-ingest exemption ends (decision 8).
  - ADR-0002 decision 1: in the instance scheme the derived name is the base of the store
    name, not the whole of it.
- **Related:** [#558](https://github.com/wilke/ragstack/issues/558),
  [#350](https://github.com/wilke/ragstack/issues/350),
  [#285](https://github.com/wilke/ragstack/issues/285),
  [ADR-0004](0004-users-groups-shares.md), [ADR-0007](0007-tenant-control-plane.md),
  [ADR-0014](0014-backups-and-eviction.md)

## Context

ADR-0003 put access control at the collection and demoted the per-chunk `tenant_id` to
provenance. The demotion never shipped. The stamp, which holds the writer's subject, still
does five jobs:

1. **Read filter:** a read sees `{caller, public}`. `api/scope.py` `shared_scope` adds the
   owner's stamp for a grantee (`tenancy.py` `scope_filters`).
2. **Chunk identity:** `stores/qdrant.py` `_point_id` = uuid5(`{stamp}:{chunk_id}`), and
   `stores/elasticsearch.py` `_es_id` follows the same pattern.
3. **Replace, delete and duplicate scope:** replace and `DELETE /v1/documents/{doc_id}` act
   on the writer's own chunks; the duplicate check on the writer's and `public` ones
   (`ingestion/pipeline.py`, `api/routers/documents.py`).
4. **Separation on shared stores:** one physical store may be reached by more than one
   registry entry or deployment, through a route or a shared server (`collections.py`
   `_routed_store_legs`, `_shared_store_users`; `ops/evict.py`).
5. **Hiding leftovers:** a purge removes the access and registry rows before the stores. If
   it fails part-way, a recreated id gets the same deterministic store name (ADR-0002).

Because the stamp is the writer, data disappears without any error whenever the writer is
not the reader:
- after a transfer, the new owner sees nothing (#558);
- chunks written by an admin or a `write` grantee are invisible to the owner, and the owner
  cannot delete or replace them.

Jobs 2–5 exist only because different data can end up in one physical store. Graph
triples live in one Neo4j database, tagged with the collection's Qdrant store name and
unique on (name, writer, collection) (`stores/neo4j.py`). Backups hold triples only for
versions that were graph-extracted, so eviction keeps triples in place. As of 2026-11-14,
graph extraction is switched off on every deployed tenant, and some production stores are
reached by two deployments (a shared server pair, and a route from one tenant into
another's store).

## Decision

**1. A collection's data belongs to the collection.** In the instance scheme, whoever
passes the collection's access check sees all of its chunks and triples: the owner,
grantees including `public`, and admins (ADR-0003 decisions 2 and 5, ADR-0004). Writing
into someone else's collection contributes the data to that collection.

**2. Each collection records its scheme and its instance key.**
- **Scheme:** every registry record says `stamped` or `instance`. Every read, write, delete
  and graph path chooses its behaviour from that field; a missing or unknown value is
  treated as `stamped`, so a fault keeps the filter on. New collections are `instance`;
  existing ones are `stamped` until moved (decision 5); fixed-name stores (decision 6) are
  always `stamped`.
- **Instance key:** minted when a record is created, and minted for every existing record
  when this ships, so ADR-0014 backup currency works for both schemes. It comes from a
  namespace that collection ids, store names and subjects cannot enter (enforced at id
  validation), and is unique across every deployment that shares a store or graph server.
  It always comes from the **target** registry record, never from a file header, an archive
  or a source payload.
- **New key:** a clone, an admin replay (ADR-0014 decision 3), a move (decision 5) and an
  ADR-0007 restore-as create a new instance. A restore-as re-mints every key and renames
  every generation before it serves.
- **Same key, new backup epoch** (ADR-0014 decision 4): a recovery, a rollback, a reload
  that reports skipped versions, and an in-place tenant restore.

**3. In the instance scheme each instance has its own stores.**
- **Vector and text stores** belong to one *generation* of the instance. Each load
  (decision 4) creates a new generation with new store names. The registry record names the
  current generation and is the only pointer to it. The store server a collection uses is
  recorded on the record or resolved by collection id; a generation that cannot be resolved
  is an error, never a fallback to the default server.
- **Graph:** triples carry the instance key and are kept apart from stamped triples, with
  their own uniqueness on (name, key). The writer is provenance: not part of identity, not
  filtered on, not a delete scope. A generation change never touches the graph; a key's
  triples are deleted only when the key is retired (purge, or the source of a move).
- **Chunks** are not filtered by stamp. Chunk ids do not include the writer, so there is
  one copy per document. Replace, delete and the duplicate check act on the whole
  collection. The writer is shown only to the owner and admins.

**4. In the instance scheme every load builds new stores, then switches.** A *load* is a
reload after eviction, a recovery, a rollback, a clone, an admin replay or a move; ordinary
ingest is not a load.
- **One load at a time:** a load first claims the collection's single pending slot on the
  record, naming its job, its generation, any new key, and a lease. The claim fails while
  another live claim exists. A claim whose lease has expired, and everything it built, may
  be dropped by any process.
- **Write fence:** every write names the generation it read at its start, and commits only
  by advancing the write counter (ADR-0014 decision 4) while that generation is current and
  no load holds the slot. This covers every writer, including workflows and scripts, and
  each graph-extraction batch. A write that fails the fence fails visibly and may be
  retried.
- **Check:** the build is checked before the switch. If the check fails, it is dropped and
  the current generation keeps serving.
- **Switch:** atomic. It succeeds only if the lifecycle state, the current generation, the
  write counter and the slot's claim are all unchanged since the claim; otherwise the build
  is dropped. The old generation becomes **retiring**.
- **Drop:** a retiring generation is dropped only when no reader or writer can still reach
  it. Eviction follows the same rule: the current generation becomes retiring and the
  record stays dormant with no current generation. A collection with a claimed slot counts
  as in flight for eviction.
- **Clone from live stores:** reads the source's generation and counter at the start and
  fails if either changed by the end.
- **Graph during a load:**
  - a load that keeps the key keeps its triples, deletes those whose document is not in the
    new generation, and does not load archived graph legs on top of the kept ones;
  - a clone or replay from a backup loads the archived graph legs under the new key;
    versions without one contribute no graph;
  - a clone from live stores copies the source's triples under the new key and never
    changes the source's;
  - a move copies them under the new key before the switch (decision 5).
- **Capacity:** building and retiring generations count toward the store server's physical
  budget; a load is refused when there is no room, and concurrent loads per tenant are
  limited. Requests that trigger a reload of the same collection join the one claimed load.

**5. A `stamped` collection becomes `instance` only by a move, after a risk check.**
- **Movable:** only a collection whose stores this registry entry alone reaches. Not
  movable: fixed-name stores (decision 6), stores another registry entry on the host
  claims, and any store reached through a route, in either direction. Eligibility is decided
  from the host-wide registry union, not from stamps.
- A move is a load from the collection's own resident stores into a new instance. A dormant
  collection is reloaded first. Clones and replays from a `stamped` source are moves for the
  purpose of this decision.
- **Risk check:** run inside the move's write-fenced window, over chunk and triple writers.
  The move needs no decision when every stamp is the owner's or `public`. Otherwise the
  owner is shown counts per writer and per document, not content, and decides: let the
  hidden data become visible, delete it, or leave the collection `stamped`. Where two
  writers hold copies of one document, the owner's copy is kept, otherwise the newest.
  A change of owner aborts a pending move. A move also makes every `write` grant a grant
  over the whole collection, and the owner is told which grants exist.
- **Before the move, unchanged:** a `stamped` collection reloads, recovers and rolls back
  into its existing deterministic names, with its filter, `shared_scope` and its current
  graph tagging. #558 persists there.
- **Who runs it:** moves on deployed tenants are operations run by the deployment owner.
  There is no bulk rewrite.

**6. Fixed-name stores stay `stamped`.** The settings-default collection, stores pinned by
deployment settings, the shared corpus (decision 8), specs that name a physical store, and
stores that fail the movability test in decision 5. They are never rebuilt into
generations and keep today's eviction protection. Their way out is a re-ingest into a new
`instance` collection, or a move the operators of every deployment that reaches the store
coordinate. `shared_scope` goes when the last `stamped` collection is moved or retired.

**7. The graph is reached only through instance keys.** Every graph read and delete names
its keys, and there are no unscoped graph reads. A multi-collection graph read means the
union of per-key results. One shared Neo4j database with key-tagged triples is what ships.
A later backend may give a key, or a set of keys, its own database or graph, so small
graphs (for example one per OpenChia episode) can be created, exported and dropped on
their own; that choice is not made here. `stamped` triples stay on the legacy path.

**8. The legacy shared corpus is frozen.** It takes no new chunks and no chunk-payload
backfills; only its registry record may change. It keeps its writer stamps and filter,
because chunks users ingested there were written as private to them and it never had an
owner to contribute to. Users can delete their own chunks there until it is retired. It is
replaced by new `instance` collections; at retirement, its remaining user chunks are
deleted with it, after notice.

**9. Purge leaves a tombstone until every store is gone.** Purge removes the access rows
first, then turns the record into a tombstone naming the key and every generation (current,
pending, retiring). The tombstone is deleted only after each drop is confirmed, and
inventory reports tombstones. A switch against a tombstone drops its own build.

**10. Who may write is unchanged,** except for the shared corpus (decision 8): the owner, a
`write` grantee, or an admin. Whoever writes, the data belongs to the collection and to its
current owner, i.e. the active owner row (ADR-0004), not `spec.owner`, which records the
creator. A transfer moves only that row.

## Consequences

**Gained:** in the instance scheme:
- transfer, admin ingest and grantee ingest work;
- there is one copy per document;
- the owner can delete and replace anything;
- old and new data never share a store, so no per-chunk filter keeps them apart;
- a failed load leaves the serving stores untouched;
- a collection's graph can be exported or dropped by its key;
- a failed purge leaves a record that names what is left.

**Accepted:**
- **Two schemes run side by side** until the last `stamped` collection is moved or retired.
  The flagship pinned corpora and every store reached by two deployments stay `stamped`,
  with #558, until re-ingested or coordinated.
- **"Stay on the current release" is real only for tenants with their own store servers
  and no routes in or out.** Where stores are shared, every deployment that reaches them is
  bound by decisions 6 and 8.
- **Moving exposes hidden data,** gated by the risk check (decision 5).
- **A move, clone or restore-as is a new instance,** so earlier backups stop being current.
- **Loading a resident collection needs room for two copies** and fences writes while it
  runs.
- **Store names can no longer be computed from the build spec alone.** Tools that do so
  read the registry record instead: inventory, protected stores, leftover sweeps, copy,
  purge and eviction drops, graph reads and writes, and the guard that matches a
  content-addressed create to an existing store. Store route tables are re-keyed by
  collection id or move onto the record, which changes deployment configuration.
- **A `write` grant on a public collection publishes at once.**
- **`TENANT_COLLECTIONS`** still limits which collections a key may reach, but it no longer
  separates rows inside an `instance` collection. Orgs that need separation use separate
  collections or tenants.
- **API contract changes.** The graph endpoints gain a collection scope, the writer field
  is hidden from readers other than the owner and admins, and `/v1/stats/tenants` changes
  meaning in the instance scheme. Python and Go change in step.

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
- **Drop the filter in place on every existing collection.** Fast, but it exposes data on
  stores two deployments reach and gives no owner a decision.
- **One graph database per collection now** (Neo4j Enterprise, Apache AGE, another graph
  store). Adds a service before it is needed; decision 7 keeps the path open.
- **One common graph, filtered only at retrieval.** Triples extracted from private chunks
  would be readable through graph endpoints.
- **Time-based drain** (drop after a fixed delay plus the longest job). No job has a known
  upper bound, so late writes would be acknowledged and lost.
- **Relabel every chunk on transfer.** A bulk write that cannot be atomic with the owner
  change, and admin and grantee ingest stay broken.
- **Widen `shared_scope` to every recorded writer.** An owner's view would depend on who
  wrote what, and replace and delete stay split.
