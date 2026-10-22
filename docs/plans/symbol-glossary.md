# Per-document symbol glossary (#754 fix A)

Status: `IN PROGRESS`, code in PR "feat(retrieval): per-document symbol glossary field (#754 A)".
Nothing is deployed and nothing live was written. Every switch defaults to off.

Status note (2026-10-22, #783): owner decision. Extraction now defaults to `rules` on
both ingest paths: `SYMBOL_GLOSSARY_EXTRACTION`, the shard tools' `--symbol-glossary`,
and a `symbol_glossary` input on every ingest CWL, which the API seeds from the
setting on GoWe submissions. The query side is unchanged: `TEXT_SYMBOL_GLOSSARY_BOOST`
still defaults to 0. Collections ingested earlier have no glossary until backfilled,
and a tenant must backfill them before raising the boost. Runbook:
[`tenant-admin.md` §6d](../runbooks/tenant-admin.md#6d-symbol-glossary-754).

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

## Review re-run (2026-10-21, after the Fable review and re-review of PR #763)

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

**Setup, matching /v1/retrieve as deployed:**
- Scratch copies only. Every scratch index and collection was deleted, and both ES and
  Qdrant listed none left.
- **Per-leg depth is 100**: top_k 50 × `retrieval_candidate_multiplier` 2. The first
  version of this re-run used 200.
- Legs:
  - BM25;
  - hybrid: RRF (k=60) of BM25 and the base dense vectors, which the glossary does not
    change;
  - **reranked**: the cap, then the cut to 50, then the live cross-encoder
    (`bge-reranker-v2-m3`, at most 4 in flight, read-only), then the cap again, then
    the top 10.
- `cap2` is `RETRIEVAL_MAX_PER_DOC=2`. It is applied where live applies `shape()`: before
  the cut to 50, inside `retrieve()`, and again after the rerank.

Columns: s@10 and s@50 are symbol pairs (of 4) in the top 10 and top 50; lost and gained
are counted against the same leg's base.

| arm | BM25 r@10 | BM25 gloss@10 | rerank r@10 | rerank r@10 B (51) | rerank s@10 / s@50 | rerank lost / gained | rerank docs@10 |
|---|---|---|---|---|---|---|---|
| base | 0.181 | 0 | 0.226 | 0.059 | 1 / 2 | – | 7.14 |
| base cap2 | 0.213 | 0 | 0.277 | 0.098 | 1 / 1 | 3 / 11 | 8.37 |
| mention, norms on, @2 | 0.219 | 9.7 | 0.284 | 0.098 | 1 / 2 | 3 / 12 | 6.50 |
| value-only, norms on, @1 | 0.297 | 6.6 | 0.271 | 0.059 | 1 / 2 | 2 / 9 | 7.02 |
| value-only, norms on, @2 | 0.323 | 8.5 | 0.290 | 0.078 | 1 / 3 | 2 / 12 | 6.94 |
| value-only, norms off, @0.5 | 0.381 | 6.5 | 0.303 | 0.137 | 1 / 2 | 2 / 14 | 7.03 |
| **value-only, norms off, @1** | 0.465 | 8.3 | **0.361** | 0.196 | 2 / 4 | **0 / 21** | 7.03 |
| value-only, norms off, @1, cap2 | 0.465 | 8.2 | 0.387 | 0.176 | 2 / 3 | 1 / 26 | 8.32 |
| value-only, norms off, @2 | 0.465 | 8.9 | 0.387 | 0.196 | 2 / 4 | 1 / 26 | 7.04 |
| value-only, norms off, @2, cap2 | 0.471 | 8.8 | 0.445 | 0.235 | 3 / 3 | 2 / 36 | 8.22 |

- **Headline:** value-only, norms off, boost 1, uncapped: reranked recall@10 **0.361**
  against a base of 0.226, with 0 records lost. (0.348 in the first, depth-200 version
  of this re-run.)
- **Deactivation** (`deactivation_rate_s:2198638f`) reaches reranked rank 6, or 5 with
  the cap: 2 of 6 queries in the top 10.
- **The cap, measured as deployed,** adds about 0.03 at boost 1 (0.387) and more at
  boost 2 (0.445). It costs `effective_radius_m:83410a4f`, which leaves the pool.
- The scratch base agrees with live openchia's ranks on 66% of records (see above).

**Recommendation for openchia (dev), as a trial:**
- `SYMBOL_GLOSSARY_EXTRACTION=rules`;
- `SYMBOL_GLOSSARY_CACHE_PATH=<the LLM cache built over openchia>`;
- the defaults `attach_only_with_value=true` and `norms: false`;
- `TEXT_SYMBOL_GLOSSARY_BOOST=1`;
- `RETRIEVAL_MAX_PER_DOC=2` is optional: +0.026 measured as deployed.

**Caveats that go with this recommendation:**
- **It is only safe with the reranker on.** On the BM25 leg alone, boost 1 still puts
  about 8.3 glossaried chunks in the top 10. A `mode=bm25` or `rerank=false` request
  would see the crowding.
- **The eval rewards value-only attachment by construction:**
  - every target is a value-stating chunk;
  - the `quote_terms` queries are words taken from the value sentence;
  - definition-, method- and mechanism-seeking queries are unmeasured;
  - every symbol-pair gain comes from copies of one paper.
- **Therefore boost 1 on openchia is a dev-tenant trial.** Add a sample of non-value
  queries to the eval before enabling it on any other tenant.

**Order of operations:**
1. Release, and let the API restart add the field. `ensure_index`'s `put_mapping` adds
   `symbol_glossary` (`norms: false`) to existing indices. That is additive and writes
   no data. A divergent index gets it through the narrow third push.
2. Backfill openchia: dry run first, then `--apply` on the owner's go.
3. Set the boost through the ctl, then restart the API only. The boost is query-time,
   so no re-ingest is needed:
   `ragstack-ctl env set <tenant> TEXT_SYMBOL_GLOSSARY_BOOST 1`, then
   `ragstack-ctl tenant restart <tenant> --only api`. Do not hand-edit `tenant.env`.
   `SYMBOL_GLOSSARY_EXTRACTION` and `SYMBOL_GLOSSARY_CONFIG_JSON` are set the same way
   (`env set`). They affect only in-process API ingests after the restart. The backfill
   script does not read either setting: it takes `--config` and `--llm-cache` on its
   command line.
   `SYMBOL_GLOSSARY_CACHE_PATH` is a host path, so it is CLI-only:
   `ragstack-ctl env set-surface <tenant> SYMBOL_GLOSSARY_CACHE_PATH=<path>` on the host,
   as the ctl account. On an image-mode tenant the file must exist before the restart,
   because the path is bound into the API instance and apptainer refuses a missing bind
   source.
   The API logs `symbol glossary config: extraction=… boost=…` at every start (#775).

Before step 3, a boost on an index without the field is a measured no-op.

**Known gaps (documented, behaviour unchanged):**
- **GoWe-ingested collections get no glossary record in their manifest.**
  `_run_gowe_ingest` writes `write_ingest_manifest_for` without `enrichment=`, and
  nothing lifts the workers' receipt records into the manifest. The step record exists
  only in the receipts. Merging receipt records into the manifest is a follow-up.
- **Old cache records.** Records written before the version key gained the generation
  limits (`llm/1:<model>:<hash>`, without `:w…:t…`) remain valid for ATTACHMENT: ingest
  reads with `cache_version=None`, and the last record wins. The LLM script, however,
  treats them as misses under the new key, so a re-run of the script re-extracts every
  document.

## Follow-ups

- JATS `<def-list>` in the JATS extractor, emitted as a per-document table the pipeline
  prefers.
- A reranked, end-to-end measurement before any boost goes above 0 on a tenant.
- Embedding definitions (option c): evaluate at the open-access re-ingest (owner).
- The #747 work-level dedup, to counter the crowding.
- Merge the workers' receipt `enrichment` records into the GoWe-path manifest.
- Add non-value queries to the eval (definition, method, mechanism) before any tenant
  other than dev enables the boost.
