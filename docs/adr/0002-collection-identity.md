# ADR 0002 — Collection identity: content-addressed stores + a durable registry

- **Status:** Accepted
- **Date:** 2026-08-04
- **Supersedes:** no prior ADR. This is the first record of a naming scheme that had only
  ever been described in passing — `ARCHITECTURE.md` §2.4, "`(model, dim)`-scoped
  collections" — and never decided in the open.

## Context

A *collection* binds an embedding model, a vector dimension, and a chunking strategy to a
physical pair of stores (a Qdrant collection + an Elasticsearch index of the same name).
Originally the physical name was derived from `(model, dim)` alone, on the reasoning that
those two are what make vectors physically incompatible.

That reasoning was incomplete, and it failed in production in two distinct ways:

1. **Chunking is part of the build spec, not a view over it.** Two corpora built with the
   same model but `fixed_token/256/32` and `fixed_token/512/64` resolved to the *same*
   physical store. The second ingest silently interleaved with the first. Retrieval still
   returned results, so nothing surfaced as an error — only a slow degradation in answer
   quality.

2. **Content addressing is wrong for a user-named set.** Once users could create their own
   collections by name, content addressing became actively harmful: two users who happened
   to pick the same model and chunker landed in one store. Uploading to `open-access`
   made documents appear in `andy`, and deleting from either hit both. This was observed
   live — three names resolving to one 674-point store (#228).

There was also no record of *how* an existing collection was built. Appending to a
collection required the operator to remember or re-derive its model and chunker; getting it
wrong wrote incoherent vectors that could not be detected after the fact.

## Decision

**1. Two naming modes, chosen by intent.**

- *Corpus* (`name` absent) — content-addressed over the **full build spec**:
  `(base, model, dim, chunk_descriptor)`, with a short hash covering `model|dim|chunk`.
  Re-ingesting the same spec is idempotent; a different chunker gets a different store.
- *Named* (`name` present) — the name is part of the identity: a `lib` marker, a slug of
  the name, and a hash over `name|model|dim|chunk`. Distinct names always yield distinct
  stores, including names that slugify identically (`open access` vs `open-access`) or
  slugify to nothing. The value folded in is the caller-supplied collection **`id`**, not
  its display `label` — the id is the stable handle, and a label must stay editable
  without moving data.

Both are deterministic, lowercase `[a-z0-9_]`, and bounded to ≲110 chars — under
Elasticsearch's 255-byte index-name limit. `chunk=None` reproduces the legacy name
byte-for-byte, so existing stores keep resolving.

**2. A durable collection registry** (`ragstack/collection_store.py`) persists each
collection's `CollectionSpec` — model, dim, chunker and its params — keyed by collection id,
with a denormalised `spec_hash` on the record for the guard below to compare against. Four
backends (`memory`, `json`, `sqlite`, `postgres`) satisfy one `CollectionStore` protocol.
The JSON backend takes an advisory `flock` on write: without one, an ad-hoc concurrency
probe (six processes × eight appends) landed 14 of 48 entries; with it, 48 of 48.

Provenance is deliberately *not* in the registry. It stays in a per-collection manifest
written by `provenance.py`, because a manifest records what a specific build observed
while the registry records what a collection currently is.

**3. The build spec is immutable.** Ingesting into an existing collection with a
different model, dim, or chunker is rejected with **409**, not silently accepted. Changing
any of them means a new collection and a full re-ingest.

**4. Deletion is explicit about data.** `DELETE /v1/collections/{id}` unregisters the
binding only; `?purge=true` also destroys the underlying Qdrant collection and ES index.
The default is the non-destructive one.

## Consequences

**Accepted:**

- **Names are not human-friendly.** A physical store is `ragstack_lib_open_access_…_cd24acfc`,
  not `open-access`. Operators must go through the registry or the API to map display
  name → physical store; reading Qdrant directly is no longer self-explanatory.
- **Migration is manual for pre-existing stores.** Collections built before this record
  carry no spec. They are registered with `source: config` provenance — *declared*, not
  *verified* — and are trusted rather than proven. Only stores built after this point get
  `source: ingest`.
- **The registry is now a stateful dependency** on a system that was otherwise derivable
  from its stores. It must be backed up with them; a lost registry means orphaned physical
  stores that nothing references.
- **A new class of orphan.** Unregistering without purging leaves data on disk. This is
  deliberate — the reverse default would make an accidental delete unrecoverable — but it
  means disk usage can exceed what the collection list implies.

**Gained:** re-ingest is genuinely idempotent; two users cannot collide; a mis-specified
append fails loudly at 409 instead of quietly poisoning a corpus; and every collection can
answer *how was this built* from its own record.

## Alternatives considered

- **Aliases over one physical store, partitioned by a payload filter.** Fewer stores, and
  the natural fit for the *library* concept. Rejected as the near-term answer because
  filtered HNSW evaluates `full_scan_threshold` **per segment**, so recall under a
  selective filter had to be proven, not assumed. It was subsequently measured at 1.000
  over 1,080 trials — so this remains the intended end state for libraries-within-a-
  collection (see `libraries-spec.md`, #230). This ADR governs the *physical* layer that
  approach will sit on, and is not superseded by it.
- **A name column in the registry, physical store still content-addressed.** Keeps names
  pretty but does not fix the collision — two named libraries would still share a store.
  Rejected: the isolation has to exist at the physical layer or delete and upload remain
  cross-contaminating.
- **Rejecting duplicate names at creation time.** Cheaper, but only closes the race for
  one API server and does nothing for stores created by the bulk CLI or a workflow.
