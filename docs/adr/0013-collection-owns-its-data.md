# ADR 0013 — A collection owns its data: each collection instance has its own stores

- **Status:** Proposed
- **Date:** 2026-11-16 (revised; first draft 2026-11-13 stamped chunks in shared stores)
- **Deciders:** @wilke
- **Amends:**
  - [ADR-0003](0003-access-control.md) decisions 1 and 2: in the `instance` scheme the
    per-chunk stamp is no longer a read filter. The planned `owner_id` rename is dropped.
  - ADR-0003 decision 2b and [ADR-0002](0002-collection-identity.md) decision 5: the shared
    corpus's read-to-ingest exemption ends.
  - ADR-0002 decision 1: in the `instance` scheme the derived name is the base of the store
    name, not the whole of it.
- **Related:** [#558](https://github.com/wilke/ragstack/issues/558),
  [ADR-0004](0004-users-groups-shares.md), [ADR-0014](0014-backups-and-eviction.md),
  [ADR-0015](0015-loads-build-new-stores.md) (how loads build, switch and drop)

## Context

ADR-0003 demoted the per-chunk `tenant_id`, which holds the writer's subject, to
provenance. The demotion never shipped. The stamp still does five jobs: the read filter,
chunk identity, the scope of replace/delete/duplicate checks, separation between registry
entries or deployments that reach one store, and hiding leftovers of a failed purge.
Because the stamp is the writer, data disappears without error whenever the writer is not
the reader: after a transfer the new owner sees nothing (#558), and the owner cannot see,
delete or replace what an admin or a `write` grantee wrote.

The last four jobs exist only because different data can end up in one store. Some
production stores are reached by several deployments, through a shared server or a route.
Graph triples share one Neo4j database, tagged with the Qdrant store name and unique on
(name, writer, collection). As of 2026-11-14 graph extraction is off on every deployed
tenant.

## Decision

**1. A collection's data belongs to the collection.** In the `instance` scheme, whoever
passes the collection's access check sees all of its chunks and graph data. Writing into
someone else's collection contributes the data to it. Who may write is unchanged (owner,
`write` grantee, admin), except that reading a shared corpus no longer lets a user ingest
into it (decision 6). The owner is the active owner row (ADR-0004), not `spec.owner`.

**2. Every record has a scheme and an instance key.**
- **Scheme:** `stamped` or `instance`, set on every record when this ships. A record whose
  scheme is missing, unknown, or disagrees with its stores is refused, never defaulted:
  `instance` behaviour applies only to stores minted for that record's key.
- **Key:** minted at create, and for every existing record when this ships. It comes from
  a namespace collection ids, store names and subjects cannot enter, is unique across
  deployments that share a store or graph server, and is read only from the **target**
  record, never from a file header, archive or payload.
- A clone, an admin replay and a move create a new instance with a new key.

**3. In the `instance` scheme an instance has its own stores and no read filter.**
- Vector and text stores are generations of the instance (ADR-0015). Old and new data,
  and different collections, never share a store.
- Chunk ids do not include the writer: one copy per document. Replace, delete and the
  duplicate check act on the whole collection.
- Graph data belongs to a **graph scope**: the instance key plus an optional partition
  label (for example one workflow episode). Anyone who may write creates partitions; they
  are archived, copied and deleted with their key's data. Triples are unique within their
  scope and kept apart from `stamped` triples. Every graph read, export and delete names
  its scopes. All scopes share one Neo4j database for now; a later backend may give a
  scope its own database without changing this interface.
- The writer is provenance only: never an identity, filter or delete scope, and shown only
  to the owner and admins.

**4. A `stamped` collection becomes `instance` only by a move, after a risk check.**
- **Until moved** it keeps its filter, `shared_scope`, recorded names and graph tagging, and
  reloads, recovers and rolls back as today. #558 persists there. A replay of a `stamped`
  backup restores a `stamped` collection into the target record's own names, refused if
  another entry claims them. A clone of a `stamped` source copies the whole collection and
  follows the move rules below (movability, risk check).
- **Movable** only when its Qdrant, ES and graph servers are exclusive to this deployment
  in the operators' inventory (ADR-0007), compared by resolved address, with no legacy
  deployment and no inbound route reaching them, and it is not fixed (decision 5).
- **Risk check:** access stays per collection; what changes is that the writer filter
  goes. Before the move, the owner sees counts of chunks and triples stamped by writers
  other than the owner or `public`, per writer and source document, and the `write` grants
  that will cover the whole collection. The owner confirms, deletes that data, or leaves
  the collection `stamped`. Where
  two writers hold copies of one document, the owner's is kept, otherwise the owner picks.
  The move aborts if the write counter, the grants or the owner changed since the decision.
- **Running it:** a dormant collection is reloaded first. A move is an operator command; it
  checks movability at the claim and again before the switch. The source's stores and
  triples retire after the switch like any old generation. There is no bulk rewrite.

**5. Fixed stores stay `stamped` and are never evicted.** The settings-derived entry,
pinned names, stores whose name is not derived from their id and build spec, and any store
another entry or deployment reaches. No single deployment evicts, purges, recovers or
rolls back a store another entry or deployment reaches. A fixed or shared
store becomes movable once its pin, route or other claimant is removed; otherwise its way
out is a re-ingest into a new collection. `shared_scope` goes when the last `stamped`
collection is moved or retired.

**6. Shared corpora stay `stamped`; writing needs `write`.** The settings-derived entry
(today the OA, ASM and Lucid corpora among others) is persisted as a record, unless a spec
already claims its stores, and is owned by the operator, who grants `write`. Users keep
deleting their own chunks on every `stamped` shared entry.

## Consequences

**Gained** in the `instance` scheme: transfer, admin ingest and grantee ingest work; one
copy per document; the owner can delete and replace anything; a graph can be exported or
dropped by scope.

**Accepted:**
- **Two schemes run side by side** until the last `stamped` collection is moved or
  retired. The pinned corpora and every store reached by more than one deployment stay
  `stamped`, with #558, until re-ingested or unshared.
- **"Stay on the current release" is real only for tenants with their own store servers
  and no routes in or out.**
- **Moving shows the owner's readers data the writer filter hid,** gated by the owner's
  decision. The risk falls on the
  writers, whose data it was contributed as.
- **A move, clone or replay is a new instance,** so earlier backups stop being current.
- **A `write` grant on a public collection publishes at once.**
- **A document's source path can still name its writer.**
- **`TENANT_COLLECTIONS`** limits which collections a key may reach but no longer
  separates rows inside an `instance` collection.
- **Store names can no longer be computed from the build spec.** Every tool that derives
  them reads the record instead; store route tables are re-keyed by collection id; the
  eviction protection gains the route and pin tests.
- **API contract changes:** graph endpoints take scopes, the writer field and the
  `tenant_id` filter are limited to owners and admins, and `/v1/stats/tenants` changes
  meaning. Python and Go change in step.

## Alternatives considered

- **Stamp every chunk with the instance key in shared stores.** The first draft. Keeps the
  read filter and mixes old and new copies during a rebuild.
- **One deterministic store per collection id, no filter.** ADR-0003's plan. Leftovers of
  a failed purge reach a recreated id.
- **Drop the filter in place on every existing collection.** Exposes data on stores
  several deployments reach, with no owner decision.
- **Freeze the shared corpora.** Rejected as more machinery than the risk warrants; ending
  read-to-ingest is enough.
- **Spin other writers' data off into collections owned by them.** Can be a later tool.
- **One graph database per collection now.** Adds a service before it is needed.
- **One common graph, filtered only at retrieval.** Triples from private chunks would be
  readable through graph endpoints.
- **Relabel every chunk on transfer, or widen `shared_scope` to every writer.** Neither is
  atomic with the owner change, and replace and delete stay split.
