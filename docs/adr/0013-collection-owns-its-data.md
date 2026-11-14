# ADR 0013 — A collection owns its data: chunk visibility follows the collection, the per-chunk stamp is provenance

- **Status:** Proposed
- **Date:** 2026-11-13
- **Deciders:** @wilke
- **Amends:**
  - [ADR-0003](0003-access-control.md) decision 1: it finishes the demotion of the per-chunk
    stamp, and replaces the planned rename to `owner_id` (decision 4 below).
  - ADR-0003 decision 2b and [ADR-0002](0002-collection-identity.md) decision 5: it ends the
    shared corpus's read-to-write exemption.
- **Related:** [#558](https://github.com/wilke/ragstack/issues/558),
  [ADR-0004](0004-users-groups-shares.md), [ADR-0014](0014-backups-and-eviction.md)

## Context

ADR-0003 decided that access is checked once, at the collection, and that the per-chunk
`tenant_id` is provenance, not authorization. The second half never shipped:

- Every read still filters chunks to `{caller, public}` (`tenancy.py:45`).
- The stamp is the caller's subject (`tenant_id=principal.tenant` at ingest; `security.py`
  sets `tenant=subject`). For a BV-BRC user it is `bvbrc:<user>`.

`api/scope.py` `shared_scope` patches one case: it adds the current owner's subject for a
grantee. Every other mismatch between writer and reader loses data without any error:
- after a transfer, the new owner sees nothing (#558);
- chunks an admin or a `write` grantee ingested are invisible to the owner.

## Decision

**1. A collection's data belongs to the collection.** Whoever passes the collection's access
check sees all of its chunks, whoever wrote them. That covers the owner, grantees including
`public`, and admins (ADR-0003 decisions 2 and 5, ADR-0004). Owned collections get no
per-chunk read filter, and `shared_scope` goes away.

This relies on ADR-0002 decision 5: a physical store has exactly one registry entry, so the
collection boundary is a store boundary.

**2. Who may write is unchanged.** The owner, a `write` grantee, or an admin may ingest.
Whoever writes, the data belongs to the collection's current owner. A transfer moves only
the owner row.

**3. The legacy shared corpus gets a service-account owner and stops taking other people's
ingests.**
- An ADR-0004 decision-7 service account becomes its owner.
- Writing there needs `write`, as on every other collection.
- **Its read filter stays** until it is retired. Chunks users ingested there were written as
  private to their writer, and dropping the filter would publish them.
- It is deprecated in favour of new `oa` and `asm` collections, and deleted once they serve
  its readers.

**4. The stamp keeps its name and means "writer".** ADR-0003 planned to rename it to
`owner_id`. That would be wrong, because owners change and writers do not, and renaming a
payload field across every store buys nothing. The field stays `tenant_id`, documented as
the writing subject. In code, `Principal.tenant` becomes `subject`, since a tenant is a
deployment (ADR-0005).

## Consequences

**Gained:**
- Transfer, admin ingest and grantee ingest all work.
- Authorization lives in one place, as ADR-0003 intended.

**Accepted:**
- **No second layer of defence on owned collections.** That was ADR-0003's call; the store
  is the boundary.
- **Neo4j graph reads** must scope by collection, not by the stamp. Purge already works
  this way.
- **Users cannot ingest into the shared corpus** until `oa` and `asm` exist.

## Alternatives considered

- **Relabel every chunk on transfer.** A bulk write that cannot be atomic with the owner
  change, and it leaves admin and grantee ingest broken.
- **Widen `shared_scope` to every recorded writer.** No migration, but an owner's view then
  depends on who wrote what, which is the coupling ADR-0003 removed.
- **Refuse transfers that would orphan chunks.** Safe, but it removes the quota remedy that
  transfer exists for.
