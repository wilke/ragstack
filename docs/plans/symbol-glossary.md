# Per-document symbol glossary (#754 fix A)

Status: `DEPLOYED`, with a dev trial running.
- The code shipped in server tag v1.6.11 (#763), fleet-wide since 2026-10-22T02:43Z.
- Dev openchia was backfilled on 2026-10-22 and runs boost 1.
- Extraction is on by default from v1.6.12 (#784). v1.6.12 was rolled out from 2026-10-22,
  dev first. The boost defaults to 0 on every tenant.
- Live measurements: [Dev trial (2026-10-22)](#dev-trial-2026-10-22).

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
  - `ingestion/symbol_glossary.py`: deterministic rules (`rules/1`; now `rules/3`, see
    [rules/3](#rules3-definitions-wrapped-across-a-line-790)). The rules cover:
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
  - It is not embedded and it is never added to `content`. No vector changes; the
    Qdrant payload only gains `metadata.symbol_glossary_version`.
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
- **Settings and flags** (as built in #763; #784 changed the extraction defaults):
  - `SYMBOL_GLOSSARY_EXTRACTION=off|rules` (default `rules` since #784, `off` before),
    `SYMBOL_GLOSSARY_CONFIG_JSON`, `SYMBOL_GLOSSARY_CACHE_PATH`,
    `TEXT_SYMBOL_GLOSSARY_BOOST` (default 0);
  - `ingest_shard.py` / `embed_shard.py --symbol-glossary {off,rules}` (default `rules`
    since #784).

  Since #784 every ingest CWL has a `symbol_glossary` input. No tools-image release was
  needed: the pinned `ragstack-tools-v1.6.10-b1` already takes the flag.
- **Backfill:** `scripts/backfill_symbol_glossary.py` is a dry run by default.
  - It streams by `doc_id` and writes partial updates only.
  - It is resumable.
  - It needs `--apply --confirm-index <name>`.
  - It was verified on a scratch copy of openchia: 2,113 of 2,113 chunks were
    identical to the in-process attacher, and a second run wrote nothing.
  - It was applied to live dev openchia on 2026-10-22 (see the dev trial section). No
    other live index has been backfilled. The owner plans to re-ingest open-access
    under a new chunking policy, so there is no open-access backfill.

## The experiment (2026-10-21)

- **Setup.** Dev ES, scratch copies only (`scratch754_*`, deleted and verified by
  listing ES and Qdrant). The source was openchia (16,514 chunks, 289 documents,
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
   `ragstack-ctl tenant restart <tenant> --only api`. This works from v1.6.12 (#781). On
   v1.6.11, `env set` refuses the glossary keys, so dev's boost was appended to
   `tenant.env` by hand (runbook §6d).
   `SYMBOL_GLOSSARY_EXTRACTION` and `SYMBOL_GLOSSARY_CONFIG_JSON` are set the same way
   (`env set`). They affect only in-process API ingests after the restart. The backfill
   script does not read either setting: it takes `--config` and `--llm-cache` on its
   command line.
   `SYMBOL_GLOSSARY_CACHE_PATH` is a host path, so it is CLI-only:
   `ragstack-ctl env set-surface <tenant> SYMBOL_GLOSSARY_CACHE_PATH=<path>` on the host,
   as the ctl account. On an image-mode tenant the file must exist before the restart,
   because the path is bound into the API instance and apptainer refuses a missing bind
   source.
   From v1.6.12 (#781) the API logs `symbol glossary config: extraction=… boost=…` at every
   start. v1.6.11 does not log it.

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

## Dev trial (2026-10-22)

These are live measurements on dev, unlike the scratch-copy experiments above.

**State:**
- Dev ran v1.6.11 (#763) for all four tests. It moved to v1.6.12 after them.
- openchia was deduplicated first: 293 → 285 documents, 16,629 → 16,084 chunks.
- It was then backfilled with rules plus the cached LLM glossary (`--llm-cache`). 543
  chunks carry a glossary. Most documents are stamped `llm/1:…`; 72 chunks are stamped
  `rules/2`.
- `TEXT_SYMBOL_GLOSSARY_BOOST=1`, set at 00:21Z and live from the 00:37Z API restart, with the reranker on (`bge-reranker-v2-m3`)
  and the default `RERANK_CANDIDATES` of 50.
- Eval set: the private `openchia-261021` set, 155 records (73 distinct queries,
  51 kind B), remapped to the deduplicated chunk ids. Found means any gold chunk_id is in
  the top 10.

**Test 1: the API arms** (OpenChia, 02:52–02:59Z). Duplicate removal and glossary
changed together:

| arm | 2026-10-21 | 2026-10-22 |
|---|---|---|
| hybrid + reranker (default) | 0.226 | 0.400 |
| HyDE | – | 0.419 |
| exclude_boilerplate | – | 0.400 |
| hybrid, no reranker | – | 0.548 |
| BM25 only, no reranker | – | 0.619 |

**Test 2: the glossary alone, BM25 leg** (read-only on the live index):
- Setup: the API's own `_build_query` at boost 0 and at boost 1, on the same index, the
  same queries and the same tenant filter.
- Boost 1 reproduces the API's BM25 arm exactly: 96 found, 69 in the top 3.

| boost | found / 155 | top 3 | recall@10 | kind B recall@10 |
|---|---|---|---|---|
| 0 | 32 | 20 | 0.206 | 0.078 |
| 1 | 96 | 69 | 0.619 | 0.431 |

67 records were gained and 3 lost. The duplicate removal's share stays unmeasured, because
no BM25 number from before the dedup exists on this query set.

**Test 3: reranker input** (offline, read-only):
- Each query's pool is the API's own rerank input: `/v1/retrieve` with top_k=50 and the
  reranker off.
- The pool was reranked through the same sidecar with the same payload shape.
- The control (content only) reproduces the live reranked top 10 on all 73 queries, list
  and order.

| reranker reads | found / 155 | top 3 | recall@10 | kind B recall@10 |
|---|---|---|---|---|
| `content` (what the API does) | 62 | 30 | 0.400 | 0.275 |
| **`content` + `\n` + `symbol_glossary`** | **81** | **44** | **0.523** | **0.412** |
| `symbol_glossary` + `\n` + `content` | 77 | 48 | 0.497 | 0.373 |
| `content` + a shuffled glossary (control) | 63 | 38 | 0.406 | – |

- Appending the chunk's own glossary gains 19 records and loses none. A shuffled glossary
  does nothing, so the gain comes from what the definitions say.
- 119 of 155 records have a gold chunk in the 50-chunk pool. The other 36 are out of reach
  for any reranker at that depth.
- "Hybrid, no reranker" (85 found) is a shallow fusion: 20 per leg (`top_k` × the candidate multiplier), cut to 10. The
  50-pool's own fused
  top 10 finds 56, and the two agree on 3 of 73 queries.
- Not deployed: the reranker still scores `content` only (`scoring/scorers.py`).

**Test 4: end-to-end Runs** (OpenChia T4, 03:20–03:33Z, dev and asm-next on v1.6.11):
- Setup: RAGStack-only Runs on one case (nursing-home, natural), 3 with the reranker on
  and 3 with it off.
- Result: both arms solved 3 of 3, with identical answers. Runs with the reranker off used
  about 8% fewer request tokens on the median Run.
- In every Run the reference paper was read as Europe PMC full text, and it supplied most
  values. RAGStack contributed 13–17 facts per Run in both arms. So the eval-set recall
  gap from Test 3 did not change an outcome here, and the case for a reranker that reads
  the glossary rests on the eval set.
- For comparison, RAGStack-only Runs before the dedup and the glossary solved 7 of 9, at
  up to 472k tokens. This batch solved 6 of 6, at no more than 186k. That is consistent
  with the new condition helping, but this design does not control for it.

**What these tests do not cover:**
- **Non-symbol queries.** Every record targets a value-stating chunk, and 55 of 65 pairs
  come from one paper. Definition-, method- and mechanism-seeking queries are unmeasured,
  both for the boost and for a reranker that reads the glossary.
- **Other collections.** Only openchia has a glossary.
- **Significance.** There are no significance tests, and records share queries.

**Tests still needed:**
1. **A non-symbol query set.** Run it on dev openchia at boost 0 vs 1, and with the
   reranker reading content vs content + glossary. Use the regression snapshot (#687),
   or add definition/method/mechanism queries to `openchia-261021`. Gate: recall@10 does
   not fall.
2. **A second collection.** Backfill a scratch copy of another collection, then repeat
   Tests 2 and 3.
3. **Pool depth and fusion.** `RERANK_CANDIDATES` above 50, and the deep vs shallow fusion
   difference, against the 36-record ceiling.
4. **End to end after v1.6.12.** A scratch GoWe upload on dev gets the `rules` glossary
   without a backfill: `symbol_glossary` is set on value-stating chunks, and the receipt
   records `enrichment.symbol_glossary`.

## rules/3: definitions wrapped across a line (#790)

**The bug.** PDF text keeps the PDF's line breaks, and `rules/2` treated a single newline
as the end of a definition: `where Qb is the breathing\nflow rate.` gave no entry.

**What changed in `rules/3`:**
- **Line folding.** Definitions are matched on `definition_map(text)`: the canonical text
  with every whitespace run that holds ONE newline folded to a single space. A run with two
  or more newlines (a paragraph break) is kept, so it still ends a definition.
- **De-hyphenation, conservative.** `left-\nright` is joined only when the hyphen sits
  between two lowercase letters, each fragment has at least two letters, and the joined
  word occurs unbroken elsewhere in the same document (`breath-\ning` becomes `breathing`
  if the paper also says "breathing"). Otherwise the hyphen is kept and only the break is
  dropped (`well-\nknown` → `well-known`, `SARS-\nCoV` → `SARS-CoV`, `air-\nborne` →
  `air-borne`). A compound split at its hyphen cannot be told from a soft hyphen without a
  dictionary. Keeping the hyphen never invents a word; at worst a soft hyphen stays visible.
  With a digit on either side the hyphen is kept and the break dropped with no space
  (`COVID-\n19` → `COVID-19`, `SARS-CoV-\n2`, `2-\ndimensional`). A hyphen after a space
  is a dash and folds like any break (`rate -\nflow` → `rate - flow`).
- **Offsets stay exact.** The raw-offset map is carried through the folding character by
  character, so a definition's offset, which positional attachment compares with
  `start_char`, is the raw offset as before.
- **The next clause ends a definition**, including the forms the clause-opening rules accept:
  `…, and Cp(t) is`, `λ (h−1) is`, `μ=μ(T) is`, `η represents`, `while τd is`, `where μt is`.
  On wrapped text the line break used to stop these run-ons by accident. Without this,
  folding lost about 10 definitions that `rules/2` had found.
- **Continuation clauses are searched in bounded windows chained from clause to clause**
  (each clause may start up to 2,500 characters after the previous one; the window adds
  1,500 for the clause itself), against precomputed sentence ends. A window's end is never a
  terminator: a clause whose match ends at a window cut is re-searched from its own start
  with a fresh window, never emitted truncated. A long run with no sentence end was
  quadratic (3.5 s on a synthetic 80k-character input; now 0.6 s).
- **Appositive vs a clause with arguments.** "the initial concentration, Cp(t) is the point
  concentration" no longer gives Cp "initial concentration": the optional arguments used to
  let the match backtrack past the `(?!\s+is)` check. With arguments or a unit in
  parentheses only a defining clause (`is the`, `denotes`, `represents`) blocks the
  appositive. Blocking every `is`, as for a bare symbol, lost three correct openchia
  definitions (`the instantaneous pedestrian density ρ(t) is a major determinant`, `the
  social distance index Pd (%) is expressed …`); this form loses one (`the system time
  constant τ (s) is the reciprocal of φ` keeps only "reciprocal of φ").
- **Linear on long whitespace runs.** `\s*,?\s*and`-style splits, `\s*` before `\s+…`
  in `_DEF_END`, and backtracking through `\s+` before a letter were quadratic (seconds at a
  few thousand spaces). They are rewritten (atomic or possessive whitespace,
  `(?:,\s*)?and`, a `(?<=\S)` end check) with the same matches: 200k spaces now take
  about 0.15 s.
- **Unchanged:** chunk matching (`for_chunk`). The symbol and value patterns already allow any
  whitespace, newlines included, between symbol, `=` and number (`Qb =\n0.5` attaches; tests
  added). Also unchanged: the LLM validation gates (`validate_entries` still runs on
  `canonical_map`), so a cached `llm/…` glossary validates and renders exactly as before.

**Backfill.** A chunk is current only when its stamp and its field both equal what would be
written. So after the bump every `rules/2`-stamped chunk is rewritten with `rules/3`, and an
`llm/…`-stamped chunk is skipped, provided the run passes the same `--llm-cache`.
**A backfill without `--llm-cache` OVERWRITES the LLM glossaries in the index with rules
output** (true before this change too; more likely now that every rules chunk is re-planned).
The dry run reports `docs_llm` and `chunks_llm_replaced`; check them. `--apply` now refuses
when the index holds `llm/…`-stamped chunks and no `--llm-cache` is given, unless
`--replace-llm`.

**Release.** `rules/3` reaches each path separately:
- GoWe uploads need a tools tag, its image, and the CWL restamp;
- in-process (API) ingests need a server tag;
- the backfill needs only a checkout.

Mixed stamps are safe: each chunk carries its own version, the field is rendered per
document, and the backfill re-plans only what differs.

**Measured on dev openchia** (read-only; 285 documents reassembled from 16,084
chunks the way the backfill does; both versions run offline on the same text):

| | rules/2 | rules/3 |
|---|---|---|
| glossary entries (doc × symbol) | 690 | 971 |
| definitions | 789 | 1,141 |
| documents with ≥1 entry | 152 | 156 |
| chunks that would carry a glossary (value-only) | 272 | 344 |
| definitions at the 12-word cap | 1 | 112 |
| definitions matching a run-on heuristic (`, X is/the` inside) | 4 | 39 |

Measured on the final `rules/3` (after the #794 review). The first `rules/3` cut (aa42144)
differed by one definition (the τ appositive above); the window, digit-hyphen and
whitespace changes moved nothing on this corpus.

- 285 doc-symbols are new, and 4 are lost. Of the new definitions, about 265 span a folded
  line break.
- **Longer and worse.** 111 new definitions run to the 12-word cap. In a sample of 45, 11
  (about a quarter) ran on into the next symbol's clause (`air density, u→ is the air flow rate, …`).
  That happens where `_NEXT_CLAUSE` cannot read the next symbol (arrows, primes, spaced
  subscripts such as `M w`). The rest are long but correct. Across 265 wrapped spans, one
  definition ran on across a line into the next sentence.
- The four lost symbols:
  - `η` hits the appositive rule's greedy phrase. `mask-filtration efficiency η` is now
    one word shorter, so the phrase overshoots to the next head noun.
  - `Eh` is the symbol in `V˙Eh is …`. The next-clause pattern cannot read the dotted
    symbol, so the previous definition runs on over it.
  - Two were wrong under `rules/2` anyway: a mispaired "respectively", and a garbled
    equation.
- Live impact on openchia is small: 16,012 of its chunks are stamped from the LLM cache, and
  only 72 (3 documents) are stamped `rules/2`. The gain is for new uploads, which get
  `rules` by default since #784 (see Release above for when each path gets `rules/3`).

## Follow-ups

- JATS `<def-list>` in the JATS extractor, emitted as a per-document table the pipeline
  prefers.
- ~~A reranked, end-to-end measurement before any boost goes above 0 on a tenant.~~
  Done on dev, 2026-10-22: see Dev trial.
- A setting that makes the reranker read `content` + `symbol_glossary` (Test 3), default
  off, gated on Tests 1 and 2.
- Embedding definitions (option c): evaluate at the open-access re-ingest (owner).
- The #747 work-level dedup, to counter the crowding.
- Merge the workers' receipt `enrichment` records into the GoWe-path manifest.
- Add non-value queries to the eval before any tenant other than dev enables the boost
  (Tests still needed, item 1).
- rules/3 run-ons: end a definition at `, <token> is the` for symbols `_SYMBOL_BODY`
  cannot read (`u→`, `u′2`, `M w`), and make the appositive phrase non-greedy (#790).
- A tools tag so GoWe ingests stamp `rules/3`.
