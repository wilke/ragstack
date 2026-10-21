# Per-document symbol glossary (#754 fix A)

Status: `IN PROGRESS`, code in PR "feat(retrieval): per-document symbol glossary field (#754 A)".
Nothing is deployed and nothing live was written. Every switch defaults to off.

## The problem

A paper defines a symbol once ("where Qb is the breathing flow rate"). A table caption
far away then holds its value ("Qb = 0.5 m3/h") without the words. A chunk-level BM25
index cannot match "breathing rate" to the caption. That is a locality problem: the
definition and the value sit in different chunks.

## What was built

- **Extraction, two ways (owner decision 2026-10-21: both):**
  - `ingestion/symbol_glossary.py`: deterministic rules, `rules/1`. The rules cover:
    - `where X is …` clauses and their continuations;
    - appositives (`the … radius r̄`);
    - `X … is the …`;
    - parentheticals (`… speed (vs)`);
    - "respectively" lists.

    They run in the ingest pipeline.
  - `scripts/symbol_glossary_llm.py`: an **offline**, document-level LLM pass. It never
    runs inside ingest or a worker. It writes a cache keyed by
    `sha256(document text)` and `llm/1:<model>:<prompt hash>`, in the
    `segmentation_cache` pattern.
  - Ingest prefers a cached LLM glossary for the document's exact text. Otherwise it
    uses the rules.
- **Definitions come from the document only, never a lexicon.**
  - Rules copy spans of the document.
  - LLM output is validated against the document, and the cache is re-validated at
    ingest:
    1. the symbol must occur in the document;
    2. every word of the definition must occur in the document (the owner's gate);
    3. **locality:** every content word must occur within `definition_window` (200)
       characters of an occurrence of the symbol.
  - Gate 3 was added after the experiment. On one openchia copy of Bazant & Bush, the
    model wrote "λv: rate of ventilation". Every word of that occurs somewhere in the
    paper, so gate 2 passed it. In that paper λv is the viral **deactivation** rate.
- **Canonical symbol keys.** One function serves extraction and chunk matching:
  - bar spellings become a combining macron;
  - NFKD folds µ to μ and math-italic letters to plain ones;
  - Greek variant letters are unified (ϕ→φ);
  - LaTeX names and `_`/`_{}` subscripts are folded.

  Units and acronyms are not symbols.
- **Positional definitions.** Every distinct definition keeps its raw offset. A chunk
  gets the nearest definition at or before its start, or failing that the first one
  after it. Symbols with more than one definition are flagged.
- **Representation (owner decision): a separate ES `text` field, `symbol_glossary`.**
  - It is not embedded, it is never added to `content`, and Qdrant is untouched.
  - Query side: `TEXT_SYMBOL_GLOSSARY_BOOST`. The default 0 emits the byte-identical
    pre-#754 query. A value above 0 uses `multi_match` `most_fields` over `content` and
    `symbol_glossary^b`.
- **Pipeline:** `IngestionPipeline(glossary_attacher=…)` runs after the boilerplate
  filter at both `prepare_documents` and `iter_embed_source`. It is never fatal.
- **Provenance (ADR-0011):**
  - `metadata.symbol_glossary_version` is stamped on every processed chunk;
  - `CollectionManifest.enrichment` and `ShardReceipt.enrichment` hold the step record
    and are hash-excluded.
  - **Not** `chunk_params`: that feeds `spec_hash` and the collection name.
- **Settings and flags** (all default off):
  - `SYMBOL_GLOSSARY_EXTRACTION=off|rules`, `SYMBOL_GLOSSARY_CONFIG_JSON`,
    `SYMBOL_GLOSSARY_CACHE_PATH`, `TEXT_SYMBOL_GLOSSARY_BOOST`;
  - `ingest_shard.py` / `embed_shard.py --symbol-glossary {off,rules}`, default off.

  The CWL files are unchanged. A worker-side glossary needs a tools-image release
  (ADR-0010).
- **Backfill:** `scripts/backfill_symbol_glossary.py` is a dry run by default.
  - It streams by `doc_id` and writes partial updates only.
  - It is resumable.
  - It needs `--apply --confirm-index <name>`.
  - It was verified on a scratch copy of openchia: 2,113 of 2,113 chunks were
    identical to the in-process attacher, and a second run wrote nothing.
  - It has not been run on any live index. The owner plans to re-ingest open-access
    under a new chunking policy, so there is no open-access backfill.

## The experiment (2026-10-21)

- **Setup.** Dev ES, scratch copies only (`scratch754_*`, deleted and verified by
  listing ES and Qdrant). The source was openchia (16,514 chunks, 289 documents, read
  read-only), with 0 reassembly gaps. The eval set is the private
  `openchia-261021` set: 155 query records, 73 distinct queries. Hits are scored on the
  quote appearing in the chunk text, per the eval set's README.
- **Index fidelity.** The base scratch copy agrees with the live index's own BM25 rank
  on 66% of records. The live index had grown by 115 chunks since the dump, and its
  term statistics differ. The arms compare against the scratch base, not against live.
- **Extraction coverage:**
  - rules: 797 entries in 159 documents, glossary on 2,113 chunks;
  - LLM, after the locality gate: 1,401 entries in 190 documents, glossary on 2,670
    chunks;
  - LLM run: 6,090 proposals, 4,506 rejected (mostly single letters and units).

**BM25 leg** (r@10 = recall@10 over all 155 records; "sym in" = symbol pairs, out of 4,
whose target is in the top 50 for a run or keyword query; "entered" = newly in the top 50;
"gloss@10" = mean top-10 slots held by chunks with a glossary):

| arm | r@10 all | r@10 B (51) | sym in / entered | lost / gained top-10 | gloss@10 | docs@10 |
|---|---|---|---|---|---|---|
| base | 0.181 | 0.039 | 1 / – | – | 0 | 7.10 |
| rules, boost 1 | 0.181 | 0.078 | 2 / 2 | 9 / 9 | 8.5 | 5.79 |
| rules, boost 2 | **0.148** | 0.059 | 3 / 3 | 14 / 9 | 9.7 | 4.80 |
| rules+LLM, boost 1 | 0.219 | 0.059 | 2 / 2 | 7 / 13 | 9.0 | 6.13 |
| **rules+LLM, boost 2** | **0.206** | 0.039 | **3 / 3** | 9 / 13 | 9.7 | 5.50 |
| rules+LLM, boost 3 | 0.213 | 0.059 | 3 / 3 | 9 / 14 | 9.9 | 4.87 |
| ctx-bm25 (Anthropic contextual) | 0.174 | 0.039 | 2 / 1 | 2 / 1 | – | 6.87 |

`best_fields` was worse than `most_fields` at every boost. Merging rules and LLM (a
union) was not better than rules+LLM.

**Dense and hybrid** (the same embedder, SFR-Embedding-Mistral; scratch Qdrant; hybrid is
RRF with k=60 of the BM25 and dense top 200, with no reranker, so it is not the live
pipeline):

| arm | r@10 all | sym in |
|---|---|---|
| dense base | 0.103 | 2 |
| dense ctx | 0.136 | 1 |
| hybrid base | 0.136 | 2 |
| hybrid rules+LLM, boost 2 | 0.155 | 2 |
| hybrid ctx | 0.148 | 2 |

**Go/no-go** (the plan's bar: at one boost, at least 3 of 4 symbol pairs enter the BM25
top 50, and regression recall@10 does not fall):

- **rules+LLM: GO at boost 2** (also at 3): 3 of 4 pairs, and recall@10 goes from 0.181
  to 0.206.
- **rules alone: NO-GO.** It reaches 3 of 4 pairs only at boost 2, where recall@10 falls
  to 0.148.

**What the numbers do not say**:

- **The deactivation pair is not fixed.** λv's correct definition now sits on the target
  chunk, but the target still ranks only 113 at best. The other three symbol pairs move.
- **The glossary lifts whole documents.** At boost 2, glossary chunks hold about 9.7 of
  the top-10 slots, and distinct documents in the top 10 fall from 7.1 to 5.5 (plan risk
  4). Nine records lose their top-10 hit even though the total rises.
- **The eval set is narrow.** 55 of its 65 pairs come from one paper. A gain may reflect
  that paper being lifted rather than better retrieval in general.
- **The pool matters.** The operational question is the reranker's pool, and simple
  hybrid recall@10 moves less.
- **index_case_count** pairs are not symbol problems. Only 6 of the 9 have an openchia
  target in this index, and none moves consistently.

Recommendation: ship the field with the boost at 0 (this PR). Enable it per tenant only
after a reranked `/v1/retrieve` measurement and a check on document crowding.

## Follow-ups

- JATS `<def-list>` in the JATS extractor, emitted as a per-document table the pipeline
  prefers.
- A reranked, end-to-end measurement before any boost goes above 0 on a tenant.
- Embedding definitions (option c): evaluate at the open-access re-ingest (owner).
- The #747 work-level dedup, to counter the crowding.
