# 0012. A document and chunk registry is the system of record; the stores are projections of it

Status: Proposed (2026-11-05; owner direction 2026-11-05)

## Context

There is no record of a document anywhere except in the indexes built from it.

- **Elasticsearch is the de facto document list.** `GET /v1/documents` is an
  aggregation of the text index by `doc_id` (`python/ragstack/documents.py`),
  and the duplicate-paper guard asks the text index whether a DOI, PMCID or
  PMID is already present (`ingestion/work_identity.py`). That lookup fails
  open: with Elasticsearch down, duplicates are admitted.
- **The copy of record is per collection, and it lives in a store we do not
  control.** `ragstack-archive/1` (`ingestion/archive.py`) writes each
  collection version to the owner's BV-BRC Workspace under `versions/<n>/`.
  The Workspace is per person, outside this project's control, and not sized
  for the open-access and asm collections, let alone the planned growth.
- **Variants cannot sit side by side.** A second chunking needs its own Qdrant
  collection and Elasticsearch index (ADR-0002). A new glossary version can be
  backfilled in place (`scripts/backfill_symbol_glossary.py`), but it then
  replaces the previous one, so comparing two glossaries on the same chunks
  needs a scratch copy of the index.
- **Each chunk carries a full copy of its document's metadata**
  (`chunkers._make_chunk`: `metadata=dict(doc.metadata)`), and overlapping
  windows store the overlap twice: 12.5% of each window, about 1.14× the text,
  at the 512/64 token default.

The code already treats a chunk as a range of text. `_make_chunk` is the only
place a chunk is built; it sets `content = doc.content[start:end]`, and the
chunk id is `uuid5(doc_id:start:end)` over those character offsets.
`ingestion/reassembly.py` rebuilds a document from its chunks' offsets (0 gaps
on 289 dev documents).

## Decision

1. **A document and chunk registry is the system of record.** Qdrant,
   Elasticsearch and Neo4j are projections: they can be dropped and rebuilt
   from the registry, as the archive format already promises for one
   collection version. A rebuild must reproduce the live chunk ids and
   offsets exactly.
2. **Layout, per document:**
   - **original**: the uploaded or fetched file, by sha256;
   - **extracted text**: derived from the original, tagged with the extractor
     (loader and text render) and its version. A new extractor version is a
     new text node; nothing is rewritten in place;
   - **chunk sets**: one per chunk descriptor (the `chunk` component of the
     ADR-0002 build spec, e.g. `fixed_token/512/64`, not the full spec hash,
     which also covers the embedding model) over one extracted text;
   - **layers on a chunk set**: embeddings (per model), symbol glossary (per
     extraction version), structure stamps, boilerplate flags, graph triples.
     A layer is versioned and can be added without re-chunking.
3. **Each chunk stores both offset kinds.** Character offsets (`start_char`,
   `end_char`, Python code points) remain the chunk's identity and the API
   surface; byte offsets into the UTF-8 text are what the registry uses for
   subset reads. The two differ on any non-ASCII text. Both are written at
   chunk time, and the write checks that the byte range decodes to the chunk
   text.
4. **Document-level metadata is stored once,** on the document, not copied to
   every chunk. Projections still denormalize it onto each indexed chunk.
5. **A document catalog lives in Postgres,** as a table next to the ADR-0002
   collection registry: one row per document per tenant with its collection
   memberships (referencing collection registry rows), work identity (DOI,
   PMCID, PMID), registry node ids, and a status that marks documents whose
   projections are missing or failed. Access checks (ADR-0003, ADR-0004) and
   the duplicate guard become joins. A unique constraint on (tenant, work key)
   lets the guard fail closed and closes the race between concurrent jobs that
   the text-index lookup has today.
6. **Write order keeps the existing rule that nothing is destroyed before its
   replacement exists.** The registry write commits first; delete-prior and
   the projection writes follow; a failed projection is retried from the
   registry, never from the source file, and the catalog status records it.
   A committed registry write never deletes anything by itself.
7. **The registry replaces the Workspace as the archive target** for
   collections the Workspace cannot hold. Small personal collections may keep
   writing `ragstack-archive/1` to the Workspace.
8. **Implementation: Shock, which this team maintains,** for the original,
   extracted text, subsets and layer files. Whether Shock's metadata store
   moves from MongoDB to Postgres is a separate, later decision.

## Options considered

- **Keep Elasticsearch as the document list.** No new service, but the record
  stays tied to one store's mapping and availability, and variants still
  cannot sit side by side.
- **Postgres and a filesystem, built here.** Fewer moving parts, but it
  rebuilds what Shock already does (versioned nodes, attributes, subset reads)
  and the team would maintain two blob stores.
- **The BV-BRC Workspace.** Already used for archives, but per person, not
  ours to operate, and not suitable at this scale.

## Consequences

- A chunking or glossary experiment becomes a new chunk set or layer on
  existing text, projected into a scratch index, instead of a full re-ingest
  or an in-place backfill.
- Ingest gains a write before the projections (decision 6).
- Deletes need tombstones in the registry and a cascade to every projection.
- **The archive's readers need a new unit.** `restore.py` and
  `POST /v1/collections/{id}/graph` key on an archive version
  (`versions/<n>/`), which also carries the load receipt, the manifest's
  provenance (ADR-0010 decision 8, ADR-0011), the triples delta and
  tombstones. For a registry-backed collection, the equivalent of a version
  and the home of its receipt and provenance must be defined before those
  readers can switch.
- Two sources of truth exist during migration. Until a collection is migrated,
  its archive or its indexes remain authoritative.

## Migration (outline)

1. Build the registry for one dev collection from its existing indexes, using
   `reassembly.py` to recover the extracted text. Collections where
   reassembly reports `length_mismatches` or gaps are re-extracted from the
   originals instead.
2. Rebuild that collection's projections from the registry and compare them
   with the live indexes chunk by chunk: same chunk ids, same character
   offsets, same text.
3. Move ingest to write the registry first, then the duplicate guard and
   `GET /v1/documents` to the catalog.
4. Migrate the large collections (open-access, asm) and stop archiving them to
   the Workspace.

## Open questions

- How offsets map back to the original document (PDF page and position). Only
  JATS keeps spans into its source today. The Markdown adapter computes spans
  into the raw `.md`, but the text render replaces that content before
  chunking, so Markdown chunk offsets already do not point into the uploaded
  file. The extractor would have to emit the map.
- Access to originals: Shock ACLs versus the per-person ownership the
  Workspace gives today. The duplicate pre-check never names a document the
  caller cannot read (`api/routers/documents.py`); the catalog join must keep
  that property.
- Tenancy of shared corpora: one registry copy referenced by several tenants,
  or one per tenant.
- Retention: how many extractor versions and chunk sets are kept.
- Expected document counts and sizes for the planned growth, to size Shock.
