# ADR 0013 — A collection owns its data: chunk visibility follows the collection, the per-chunk stamp is provenance

- **Status:** Proposed
- **Date:** 2026-11-13
- **Deciders:** @wilke
- **Supersedes:** [ADR-0003](0003-access-control.md) decision 1, in part: its clause "stays
  enforced in filters for at least one release as defence in depth". It also replaces
  decision 2b's shared-surface write exemption.
- **Related:** [#558](https://github.com/wilke/ragstack/issues/558) (a transfer hands over
  a collection the new owner cannot read), [ADR-0004](0004-users-groups-shares.md),
  [ADR-0005](0005-tenant-anatomy.md), [ADR-0014](0014-backups-and-eviction.md)

## Context

ADR-0003 decided that access is checked once, at the collection. It also demoted the
per-chunk `tenant_id` to provenance. The demotion never shipped:

- Every read still filters chunks to `{caller, public}` (`tenancy.py:45`). The value
  stamped at ingest is the caller's subject (`documents.py`, `tenant_id=principal.tenant`),
  and for a BV-BRC user that is `bvbrc:<user>` (`security.py:839`, `tenant=subject`).
  "Tenant" here means a user. GLOSSARY and ADR-0005 define a tenant as a deployment.
- `api/scope.py` `shared_scope` patches one case: a grantee also gets the *current owner's*
  subject in the filter. It does nothing for the owner, and nothing for chunks written by
  anyone else.

**What breaks:** whenever the writer is not the person reading, the chunks disappear from
results without any error:
- after an ownership transfer (#558): the new owner sees zero chunks;
- after an admin, or a grantee with `write`, ingests into someone's collection: the owner
  cannot see those chunks.

The legacy shared corpus (`is_shared_surface`) is the one place where the filter is still
the isolation. Any reader may ingest there (ADR-0003 2b), and each writer's chunks are
visible only to that writer.

## Decision

**1. A collection's data belongs to the collection.** Anyone the collection's access check
admits sees every chunk in it, whoever wrote it. That covers the owner, grantees (including
`public`) and admins. The access check is the only authorization (ADR-0003 decision 2,
ADR-0004).

**2. The per-chunk stamp is provenance only.** It records the subject that wrote the chunk.
- It is kept, for audit and for "who added this".
- It is no longer a read filter on owned collections.
- `shared_scope` and `shared_scope_many` are deleted; nothing is left for them to widen.

**3. Who may write is unchanged.** The owner, a grantee with `write`, or an admin may
ingest. Whoever writes, the data belongs to the collection and therefore to its current
owner. That includes backups ([ADR-0014](0014-backups-and-eviction.md)).

**4. A transfer moves the owner row and nothing else.** The new owner sees everything at
once. No chunk is relabelled.

**5. The filter is dropped only where a physical store holds one collection.** The stores
filter by the stamp alone, with no `collection_id` predicate. Dropping it on a store that
two registry ids share would expose one id's chunks through the other. This is the same
guard as today's `_widening_eligible`. A co-resident store keeps the filter until it is
split or adopted (ADR-0005 decision 7).

**6. The legacy shared corpus gets a service-account owner and stops taking other people's
ingests.**
- An ADR-0004 decision-7 service account becomes its owner.
- A `read` grant no longer suffices to ingest. The `"read" if is_shared_surface else "write"`
  exemption is removed, and writes need `write` like everywhere else.
- **Its filter stays** until the corpus is retired. The per-writer chunks already in it were
  written as private to their writer. Dropping the filter would publish them.
- It is deprecated. New `oa` and `asm` collections replace it. Once they serve its readers it
  is deleted, and decision 5 then leaves no filtered store behind.

**7. Code says what it means.**
- `Principal.tenant` is renamed `subject`, since it holds a user.
- The stamp field is renamed from `tenant_id` to `writer_id`. ADR-0003 proposed `owner_id`,
  but owners change and writers do not.
- Both renames are durable-format changes, and they come after decisions 1–6. Readers
  accept both field names during the migration.

## Order of work

1. Add a read-after-transfer test and a non-owner-ingest test. Both are red today.
2. Decision 6, first part: a service-account owner, and the shared corpus no longer takes
   other people's ingests.
3. Decisions 1, 2 and 5: drop the filter on sole-resident owned collections, and delete
   `shared_scope`. This fixes #558.
4. Decision 7: the renames, with dual reads.

## Consequences

**Gained:**
- Transfer, admin ingest and grantee ingest all work.
- Authorization lives in one place, as ADR-0003 intended.
- The class of bug where a filter mistake is a security hole (#197) leaves the read path of
  owned collections.

**Accepted:**
- **No second layer of defence on owned collections.** A bug that resolves the wrong
  collection is no longer caught by the filter. The physical store is the boundary
  (ADR-0003 decision 1), so such a bug would have to name the wrong physical store.
- **The Neo4j graph** keys its triples on `(name, tenant_id, collection)`. Graph reads must
  scope by collection, and the stamp becomes provenance there too. Purge is already
  collection-wide (GLOSSARY, "Known gap").
- **Users lose the ability to ingest into the shared corpus** until `oa` and `asm` exist.

## Alternatives considered

- **Relabel every chunk on transfer.** Correct, but a bulk write that cannot be atomic with
  the owner change, and it still leaves admin and grantee ingest broken.
- **Widen `shared_scope` to the owner, using recorded writers.** No migration, but an
  owner's view then depends on who wrote what, which is the coupling ADR-0003 set out to
  remove.
- **Refuse transfers that would orphan chunks.** Safe, but it removes the quota remedy that
  transfer exists for.
