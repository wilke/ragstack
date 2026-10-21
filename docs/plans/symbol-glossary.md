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
    and are hash-excluded. **Not** `chunk_params`: that feeds `spec_hash` and the
    collection name.
  - The record comes from the pipeline that ran the step. It counts applied and
    degraded batches, so a step that only degraded is never recorded as applied.
  - **`enrichment` is INTERIM.** ADR-0011 is Accepted. When its PR-3 lands, the record
    folds into the ingest activity's `core.activity.params`, and the field goes away.
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

The first experiment's recommendation was to ship with the boost at 0 and enable per
tenant only after a reranked measurement and a crowding check. That measurement is the
next section.

## Review re-run (2026-10-21, after the Fable review of PR #763)

**What changed before the re-run:**
- `rules/2`: the appositive rule no longer steals the previous clause's definition, and
  `where Qb (L/min) is …` is now found.
- **Value-only attachment, on by default** (`attach_only_with_value`). A chunk gets a
  symbol's definition only where it states a value for that symbol: `=`, `≈` or `~`
  followed by a number. A bare equation does not count.
  - The reviewer's prototype regex, which needs no number, gave 925 glossaried chunks;
    this one gives 683, against 2,651 for every mention.
- **`symbol_glossary` mapped with `norms: false`.**
- The locality gate gained the verbatim "another symbol in between" rejection.

**Setup.** Scratch copies only. Every scratch index and collection was deleted, and both
ES and Qdrant listed none left. The legs:
- BM25 (top 200);
- hybrid: RRF of BM25 and the base dense vectors, which the glossary does not change;
- **reranked**: the hybrid top 50, rescored by the live cross-encoder sidecar
  (`bge-reranker-v2-m3`, at most 4 requests in flight, read-only, 731 distinct calls),
  then the per-document cap, then the top 10. This is the /v1/retrieve shape.

`cap2` is `RETRIEVAL_MAX_PER_DOC=2`. Columns: s@10 and s@50 are symbol pairs (of 4) in
the top 10 and top 50; lost and gained are counted against the same leg's base.

| arm | BM25 r@10 | rerank r@10 | rerank r@10 B (51) | rerank s@10 / s@50 | rerank lost / gained | rerank docs@10 |
|---|---|---|---|---|---|---|
| base | 0.181 | 0.219 | 0.039 | 1 / 2 | – | 7.12 |
| base cap2 | 0.213 | 0.252 | 0.039 | 1 / 2 | 0 / 5 | 7.93 |
| mention, norms, @2 | 0.219 | 0.252 | 0.039 | 1 / 2 | 4 / 9 | 6.70 |
| value-only, norms, @0.5 | 0.226 | 0.232 | 0.059 | 1 / 2 | 2 / 4 | 6.97 |
| value-only, norms, @1 | 0.297 | 0.245 | 0.059 | 1 / 2 | 2 / 6 | 7.06 |
| value-only, norms, @2 | 0.323 | 0.252 | 0.078 | 1 / 3 | 3 / 8 | 7.03 |
| value-only, no norms, @0.5 | 0.381 | 0.271 | 0.078 | 1 / 2 | 2 / 10 | 7.04 |
| **value-only, no norms, @1** | 0.465 | **0.348** | **0.196** | **2 / 4** | 2 / 22 | 7.03 |
| value-only, no norms, @2 | 0.465 | 0.336 | 0.157 | 1 / 3 | 3 / 21 | 6.93 |
| **value-only, no norms, @1, cap2** | 0.465 | **0.361** | 0.196 | 2 / 4 | 2 / 24 | 7.65 |

- **Deactivation** (`deactivation_rate_s:2198638f`) reaches reranked rank 9 at value-only,
  no norms, @1. It is the first arm to get it into the top 10 (2 of 6 queries).
- **Crowding is gone on the reranked leg:** docs@10 is 7.0, against 7.1 for base.
- **Caveat: the eval set favours this lever.** Its targets are value-stating chunks,
  because it was built from parameter-extraction runs, and value-only attachment
  promotes exactly those. Queries that do not seek a value are not measured here.
- The scratch base agrees with live openchia's ranks on 66% of records (see above).

**Recommendation for openchia (dev):**
- `SYMBOL_GLOSSARY_EXTRACTION=rules`;
- `SYMBOL_GLOSSARY_CACHE_PATH=<the LLM cache built over openchia>`;
- the defaults `attach_only_with_value=true` and `norms: false`;
- `TEXT_SYMBOL_GLOSSARY_BOOST=1`;
- `RETRIEVAL_MAX_PER_DOC=2`.

**Order of operations:**
1. Release, and let the API restart add the field. `ensure_index`'s `put_mapping`
   adds `symbol_glossary` (`norms: false`) to existing indices. That is additive and
   writes no data.
2. Backfill openchia: dry run first, then `--apply` on the owner's go.
3. Set the boost.

Before step 3, a boost on an index without the field is a measured no-op. Any other
tenant should first get its own measurement.

## Follow-ups

- JATS `<def-list>` in the JATS extractor, emitted as a per-document table the pipeline
  prefers.
- A reranked, end-to-end measurement before any boost goes above 0 on a tenant.
- Embedding definitions (option c): evaluate at the open-access re-ingest (owner).
- The #747 work-level dedup, to counter the crowding.
