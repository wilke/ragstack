# 0012. A document and chunk registry is the system of record; the stores are projections of it

Status: Proposed (2026-11-05; owner direction 2026-11-05)

## Context

There is no record of a document anywhere except in the indexes built from it.

- **Elasticsearch is the de facto registry.** `GET /v1/documents` is an
  aggregation of the text index by `doc_id` (`python/ragstack/documents.py`),
  and the duplicate-paper guard asks the text index whether a DOI or PMCID is
  already present (`ingestion/work_identity.py`). That lookup fails open: with
  Elasticsearch down, duplicates are admitted.
- **The copy of record is per collection, and it lives in a store we do not
  control.** `ragstack-archive/1` (`ingestion/archive.py`) writes each
  collection version to the owner's BV-BRC Workspace under `versions/<n>/`.
  The Workspace is per person, outside this project's control, and not sized
  for the open-access and asm collections, let alone the planned growth.
- **Every variant is a whole collection.** A second chunking or a second
  glossary version needs its own Qdrant collection and Elasticsearch index
  (ADR-0002). The chunking arms on dev are separate collections, and a
  glossary comparison that needs the same chunk under two glossaries cannot be
  run against a tenant at all.
- **Each chunk carries a full copy of its document's metadata**
  (`chunkers._make_chunk`: `metadata=dict(doc.metadata)`), and overlapping
  windows store the overlap twice (about 12% of the text at 512/64).

The code already treats a chunk as a range of text. `_make_chunk` is the only
place a chunk is built; it sets `content = doc.content[start:end]`, and the
chunk id is `uuid5(doc_id:start:end)`. `ingestion/reassembly.py` rebuilds a
document from its chunks' offsets (0 gaps on 289 dev documents).

## Decision

1. **A document and chunk registry is the system of record.** Qdrant,
   Elasticsearch and Neo4j are projections: they can be dropped and rebuilt
   from the registry, as the archive format already promises for one
   collection version.
2. **Layout, per document:**
   - **original**: the uploaded or fetched file, by sha256;
   - **extracted text**: derived from the original, tagged with the extractor
     (loader and text render) and its version. A new extractor version is a
     new text node; nothing is rewritten in place;
   - **chunk sets**: one per chunking spec (the ADR-0002 spec hash) over one
     extracted text, each chunk a **byte range** into that text, read as a
     subset rather than stored again;
   - **layers on a chunk set**: embeddings (per model), symbol glossary (per
     extraction version), structure stamps, boilerplate flags, graph triples.
     A layer is versioned and can be added without re-chunking.
3. **Offsets are bytes in the registry, characters in the API.** `start_char`
   and `end_char` are Python string indices (code points), which differ from
   UTF-8 byte offsets on any non-ASCII text. The registry stores byte ranges;
   the conversion is done once, at write, and checked by reading the range
   back and comparing it to the chunk text.
4. **Document-level metadata is stored once,** on the document, not copied to
   every chunk. Projections still denormalize it onto each indexed chunk.
5. **A thin catalog lives in Postgres:** one row per document per tenant with
   its collection memberships, work identity (DOI, PMCID, PMID), registry node
   ids and status. Access checks (ADR-0003, ADR-0004) and the duplicate guard
   become joins, and the guard can fail closed.
6. **The registry replaces the Workspace as the archive target** for
   collections the Workspace cannot hold. Small personal collections may keep
   writing `ragstack-archive/1` to the Workspace.
7. **Implementation: Shock, which this team maintains,** for the original,
   extracted text, subsets and layer files. Whether Shock's metadata store
   moves from MongoDB to Postgres is a separate, later decision.

## Options considered

- **Keep Elasticsearch as the registry.** No new service, but the record stays
  tied to one store's mapping and availability, and variants stay collections.
- **Postgres and a filesystem, built here.** Fewer moving parts, but it
  rebuilds what Shock already does (versioned nodes, attributes, subset reads)
  and the team would maintain two blob stores.
- **The BV-BRC Workspace.** Already used for archives, but per person, not
  ours to operate, and not suitable at this scale.

## Consequences

- A chunking or glossary experiment becomes a new chunk set or layer on
  existing text, projected into a scratch index, instead of a full re-ingest.
- Ingest gains a write before the projections. Ordering must stay safe: the
  registry write commits first, projections follow, and a failed projection is
  retried from the registry, never from the source file.
- Deletes need tombstones in the registry and a cascade to every projection.
- Two sources of truth exist during migration. Until a collection is migrated,
  its archive or its indexes remain authoritative.

## Migration (outline)

1. Build the registry for one dev collection from its existing indexes, using
   `reassembly.py` to recover the extracted text. Collections where
   reassembly reports `length_mismatches` or gaps are re-extracted from the
   originals instead.
2. Rebuild that collection's projections from the registry and compare them
   with the live indexes chunk by chunk.
3. Move ingest to write the registry first, then the duplicate guard and
   `GET /v1/documents` to the catalog.
4. Migrate the large collections (open-access, asm) and stop archiving them to
   the Workspace.

## Open questions

- How offsets map back to the original document (PDF page and position). Only
  JATS has spans today; the extractor would have to emit the map.
- Tenancy of shared corpora: one registry copy referenced by several tenants,
  or one per tenant.
- Retention: how many extractor versions and chunk sets are kept.
- Expected document counts and sizes for the planned growth, to size Shock.
