I read about 20 files. None of them ingest-tested anything. The short answer: **only `cwl/ingest-bulk.cwl` can run all six methods, and it drops almost all JATS metadata.** Tags: [F] = from files, [I] = my inference.

**(a) Paths**
- `ingest-bulk.cwl` accepts all six methods, including `semantic` and `semantic_pooled` (ingest-bulk.cwl:92). It does not extract JATS: it only takes JSONL shards that already exist (:61-63). [F]
- `jats-ingest.cwl` runs extract → embed → load, but only accepts `fixed`, `fixed_token`, `sentence`, `words` (:85). `embed_shard.py` refuses semantic itself (embed_shard.py:91-98). [F]
- The API path through GoWe (`pdf-ingest-scatter.cwl`) rejects `.jsonl` batches with `JSONL_UNSUPPORTED_ERROR`, and other suffixes (so `.xml`) with "no loader" (ingest-paths.md:139). So JATS cannot go through the API at all. [F]

**(b) `ingest_shard.py`**
- It reads `{text,path,metadata}` through `JsonlLoader` (loaders.py:349), so it does accept `jats_extract` output. [F]
- Every chunk setting is checked against the registry entry, field by field (ingest_target.py:126-130). The workflow defaults are 256/32, so every run must send size and overlap matching the entry exactly. [F]
- `semantic` and `semantic_pooled` build their own breakpoint embedding bridge, using the same endpoints as the vectors (ingest_shard.py:326-344). [F]
- The semantic tunables, the size cap and the sentence token unit come **only** from the entry's `chunk_params` (ingest_shard.py:95-179). [F]
- **fixed_token 1024/0:** overlap 0 is valid (collections.py:432-444). A size above the model window minus 16 is refused (CHUNKING.md:52-54). With SFR's window it should fit [I].

**(c) Registration**
- Each of the six collections must already be in the registry before its first ingest. An unknown id is refused (ingest_target.py:675-680). [F]
- Setting the chunk spec when creating a collection is admin-only (collections.py:765-770). [F]
- The per-owner quota is 5, admins exempt (collections.py:739-744), so six collections need an admin principal. [F]

**(d) New v1.6.22 inputs on `ingest-bulk.cwl`**
- `max_chunks` defaults to 0 = no cap (:121-129). [F] The doc says a registry override "still applies", but `ingest_shard.py` only passes `args.max_chunks` (:364), unlike `load_embeddings.py:319`. [F] So there is no cap on this path. [I]
- `tool_image` and `tool_image_digest` default to null. They are provenance only, recorded and never checked (:130-143). [F]
- `glossary_llm` defaults to `"off"`, which means rules only, no network (:151-156). Every other `glossary_llm_*` input defaults to 0 or null and is ignored when off. [F] **No LLM calls happen with the defaults.**

**(e) What gets lost**
- `ingest_shard.py` builds a bare `JsonlLoader()` with no passthrough list, so "a JATS shard ingested through it loses pmcid/pmid/journal/…" (ingest_shard.py:398-400). `content_type`, `section_title` and `licence` are lost too. [F]
- The enriched fields survive: doi, title, authors, year, journal? [I — `index_metadata` keeps the `EnrichedDoc` fields]
- `abstract` and `citations` are dropped from chunks by design (loaders.py:292). [F]
- The clean tree strips `<back>`, so the reference list never reaches the text. [F per GOAL]
- `jats_extract.py` emits no `structure` key; I found no match for "structure" in it. `ingest-bulk.cwl` has no `structure_metadata` input. So there is no section metadata on chunks. [F]

| ID | What | Evidence | Sev | Who |
|---|---|---|---|---|
| B1 | No path does JATS → semantic in one workflow | jats-ingest.cwl:85 `symbols: [fixed, fixed_token, sentence, words]` | High | Claude/owner: two-stage run (extract, then ingest-bulk) |
| B2 | ingest-bulk drops JATS metadata | ingest_shard.py:399 "loses pmcid/pmid/journal/… on every record" | High for eval (pmcid is the join key) | ragstack team: add `--metadata-passthrough` |
| B3 | Six collections must be registered first, by an admin | ingest_target.py:680 "may not mint a store"; collections.py:768 "admin-only" | Blocker | owner |
| B4 | Size/overlap must match the entry; CWL defaults are 256/32 | ingest_target.py:132 "refusing to ingest"; ingest-bulk.cwl:94-95 | Blocker if omitted | Claude (inputs) |
| B5 | `sentence` token unit and the semantic cap only come from `chunk_params` | ingest_shard.py:141 "the one source" | High | owner, at create |
| B6 | Worker must be able to find the dev registry | ingest-bulk.cwl:75-87 "a name the worker has nothing configured for is REFUSED" | Blocker (unverified) | owner |
| B7 | `gowe_batch_ingest.py` only drives jats-ingest | gowe_batch_ingest.py:492 requires `registry_db` | Medium | Claude: submit with gowe directly |
| B8 | Workflow text (v1.6.22) runs a v1.6.20 image | ingest-bulk.cwl:334; receipt commit 4a0571b | Medium: an unknown flag fails with exit 2 | owner: check it |
| B9 | No section structure for JATS | jats_extract.py (no "structure"); no CWL input | Low (payload only) | ragstack team |
| B10 | GoWe reports `chunk_ids=[]` | GOAL.md:5 | Low | verify via receipt `n_chunks` and Qdrant counts |
| B11 | `tenant` defaults to "public", and it feeds the point ids | jats-ingest.cwl:80 "changing this later means a full re-ingest" | Medium | owner: choose the tenant value now |
| B12 | Semantic allowed on dev only per a doc snapshot | ingest-paths.md:60 "empty (semantic allowed)", read 2026-10-06 | Low on path C [I] | owner: confirm |

**(f) Shortest path to a first working ingest: 5-article dry run**
1. **Owner registers six collections** as admin with `POST /v1/collections`, authenticating with the token in `the owner's BV-BRC token file`. Suggested bodies [I]:
   - `{"id":"oa-ft512-64","chunk":{"method":"fixed_token","size":512,"overlap":64}}`
   - `oa-ft512-0`: same, overlap 0
   - `oa-ft1024-0`: size 1024, overlap 0
   - `oa-sent512`: `{"method":"sentence","size":512,"overlap":0,"params":{"size_unit":"tokens"}}`
   - `oa-sempool512`: `{"method":"semantic_pooled","size":512,"overlap":0}`
   - `oa-sem512`: `{"method":"semantic","size":512,"overlap":0}`

   For the two semantic collections the API records `size_cap: "tokens"` automatically (collections.py:604-610) [F]. Then read each entry back with `GET` to get the recorded overlap.
2. **Claude extracts the shard.** Write `s5.jsonl` with 5 lines `{"pmcid":…,"xml_path":"clean/…"}` taken from the sample manifest. Then run `apptainer exec ragstack-tools-v1.6.20-b1.sif python /opt/ragstack/scripts/jats_extract.py --shard s5.jsonl --corpus <corpus root> --out oa5.jsonl --report oa5.skips.json` [F flags, jats_extract.py:323-335]. Use a distinct shard basename for each collection's run.
3. **Owner submits one `ingest-bulk.cwl` run per collection**, with these inputs:
   - `shards: [oa5.jsonl]`, `collection_id: <id>`, `collection: <physical name from GET>`, `registry: <dev name>`, `tenant: <dev tenant>`
   - `chunk_method`, `chunk_size`, `chunk_overlap` exactly as recorded on the entry
   - `embedding_url: [<embedding endpoint A>, <embedding endpoint B>]`, `qdrant_url`, `es_url`
   - `max_chunks: 0`, `glossary_llm: "off"`, `symbol_glossary: "rules"`

   Run the fixed_token 512/64 collection first, then the semantic one.
4. **Check the results:** receipt `status=COMPLETED` and `n_chunks`, the semantic `[chunker] size_cap=…` line on stderr, Qdrant counts, and the "dropped record metadata" log line (which is expected until B2 is fixed).

**Files I could not find:** none. All the requested files exist. I did not read `embed_shard.py`, `load_embeddings.py` or `plan_shards.py` in depth, and I did not open `the owner's BV-BRC token file`.
