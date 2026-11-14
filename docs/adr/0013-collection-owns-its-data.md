# ADR 0013 — A collection owns its data: chunks are stamped with their collection, not their writer

- **Status:** Proposed
- **Date:** 2026-11-13
- **Deciders:** @wilke
- **Amends:**
  - [ADR-0003](0003-access-control.md) decisions 1 and 2. The per-chunk stamp stays as a
    store-side filter but names the collection, not a user. The planned `owner_id` rename
    is dropped.
  - ADR-0003 decision 2b and [ADR-0002](0002-collection-identity.md) decision 5: the shared
    corpus's read-to-ingest exemption ends.
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

## Decision

**1. A collection's data belongs to the collection.** On collection-stamped collections
(decision 4 covers the rest), whoever passes the collection's access check sees all of its
chunks: the owner, grantees including `public`, and admins
(ADR-0003 decisions 2 and 5, ADR-0004).

**2. New collections stamp every chunk with the collection's instance key.**
- **What the key is:** the collection id plus a value unique to that creation, recorded on
  the registry record. It sits in a reserved namespace that no subject and not `public` can
  occupy, so a recreated id never matches old chunks.
- **What it does:** reads filter on it, chunk ids derive from it, and replace, delete and
  the duplicate check are scoped by it.
- **Graph:** Neo4j triples carry the key too, and graph reads scope by collection.
- **Where it comes from:** the key always comes from the **target** registry record, never
  from a file header, an archive or a source payload. Every path that loads, copies or
  rebuilds chunks stamps from the target record.
- **Recovery vs new instance:** a record whose stores survive (seed, an in-place tenant
  restore) keeps its key and starts a new backup epoch, so no backup taken before the
  recovery can count as current (ADR-0014). An admin replay (ADR-0014 decision 3), or a
  clone such as an ADR-0007 restore-as, creates a new instance with a new key.
- **The writer** is recorded as a separate provenance field, never filtered on.

**3. Who may write is unchanged:** the owner, a `write` grantee, or an admin. Whoever
writes, the data belongs to the collection and to its current owner, i.e. the active owner
row (ADR-0004), not `spec.owner`, which records the creator. A transfer moves only that row.

**4. Existing collections stay on the writer stamp until rebuilt.**
- They keep their filter, and `shared_scope` stays for them. #558 persists there until they
  move.
- A collection moves only by building a **new instance** to completion with the collection
  stamp, then switching to it in one registry write, then purging the old chunks.
- The switch commits only if the old instance's write counter (ADR-0014) has not moved since
  the build started. Otherwise the build is redone, so a delete made during the build is
  never undone. Once the switch commits, writes to the old instance are refused.
- Re-ingesting single documents never switches the scheme.
- There is no bulk rewrite. `shared_scope` goes when the last writer-stamped collection
  does.

**5. The legacy shared corpus gets a service-account owner and stops taking other people's
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

**Gained:** on collection-stamped collections:
- transfer, admin ingest and grantee ingest work;
- there is one copy per document;
- the owner can delete and replace anything;
- isolation on a shared store does not depend on the registry being perfect.

**Accepted:**
- **Two stamp schemes run side by side.** Multi-collection search and the graph must handle
  both.
- **Rebuilding exposes hidden chunks.** Rebuilding an existing collection makes the chunks
  that grantees and admins wrote visible to all of its readers.
- **A rebuild is a new instance,** so earlier backups stop being current. The collection
  stays resident until it is backed up again.
- **A `write` grant on a public collection publishes at once.**
- **Some row-level separations end for new collections.**
  - Two deployments routed to one store cannot read each other's new collections. That is
    consistent with "no cross-tenant sharing".
  - `TENANT_COLLECTIONS` still limits which collections a key may reach, but it no longer
    separates rows inside a new collection. Orgs that need separation use separate
    collections or tenants.
- **API contract changes.** The graph endpoints gain a collection scope, and
  `/v1/stats/tenants` changes meaning for new collections. Python and Go change in step.
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
