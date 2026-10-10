# The system study: quality and cost of the whole RAG configuration

**Status, 2026-10-10:** `PROPOSED`, first version. Nothing here is decided, measured or scheduled.
No experiment was run and no live service was queried to write it. The chunking study
([chunking-study-plan.md](chunking-study-plan.md)) becomes sub-study 1 of this plan; it is
`BLOCKED` on the two-reader human read and is not changed by this page.

**Summary.** The owner asked (2026-10-10) for a study plan that starts from the cost function and
treats chunking as one component among several: chunking, the vector store, text search, the
knowledge graph, the models. The question is which decisions and components move the quality and
the cost of the *system*, which move them most, and what sub-tasks follow. This page states the
objective in two forms and leaves the choice to the owner (§2, S1); inventories every component
with its knobs, what production runs, and what the record has measured about its quality and cost
effects (§3); maps component × outcome with each cell labelled *measured*, *inferred* or *unknown*
(§4); lists the interactions that make the components non-separable (§5); proposes sub-studies
ranked by value of information (§6); names the shared infrastructure, above all a cost ledger
that does not exist (§7); and numbers the owner decisions S1–S10 (§8). Every number links to the
committed file it comes from; anything else is labelled estimate, inference or unknown. The
headline finding of writing it: **storage and ingest cost are measured or arithmetic; per-query
cost, generator cost on the served path, and every cost of the knowledge graph have no measurement
at all** (§4.3, §7.1).

**Contents:** [1 Status](#1-status-and-framing) · [2 Goal and objective](#2-goal-and-objective-function) ·
[3 Component inventory](#3-component-inventory) · [4 Impact map](#4-impact-map) ·
[5 Interactions](#5-interactions) · [6 Sub-studies](#6-sub-studies-ranked-by-value-of-information) ·
[7 Shared infrastructure](#7-shared-infrastructure) · [8 Open decisions](#8-open-decisions-for-the-owner) ·
[9 First steps](#9-proposed-first-steps) · [10 What this is not](#10-what-this-document-is-not)

---

## 1. Status and framing

**Where the framing comes from.** [chunking-study-plan.md](chunking-study-plan.md) §1 *The
objective, stated as an optimisation problem* (#726, 2026-10-10) records it: max<sub>C</sub>
E(C | Q, R, B) — C the chunking/delivery strategy, Q the query population, R the
retrieval/reranking pipeline, B the delivered token budget, E the evidence delivered — proposed in
an external review the owner shared on 2026-09-21 and **not on the record**. The chunking study's
decision rule is its cost-minimising counterpart with R *fixed* to the served path: the cheapest C
such that E(C | Q, R, B) ≥ E(C_ref | Q, R, B) − ε, ε = 0.05, one-sided non-inferiority on `ERET`
for every declared population and on `EPACK` for the population that carries containment
([SPEC-confirmation-run-r3.md](results/design/SPEC-confirmation-run-r3.md) §1, §3.6; D4(i)). That
section also names the three parts not settled — the cost term (**D16**), C_ref (contradiction 11)
and how several populations combine (**D17**) — and hands the system-level objective to this page.
This page does not restate those; it points at them.

**What this page generalises.** The decision variable becomes the whole configuration

> S = (ingest/parsing, chunking, embedding model and dimension/precision, vector store and index
> parameters, text search configuration, fusion, reranker model and depth, knowledge graph,
> metadata and filters, query rewriting, delivery/packing, generator)

and R is no longer fixed. **Neither the system-level framing nor any system-level cost term is on
the record today.** The chunking study's cost term is undefined (D16); the record has used vector
count implicitly (r3 §3.6: 2.157× fewer at 512→1024/0), and D16's options — vectors stored,
embedding compute, index disk, query latency, a weighted combination — are a subset of the terms in
§2.4. Everything in §2.4 beyond those is proposed here.

---

## 2. Goal and objective function

### 2.1 The decision

Which configuration S the production index and served path should use for the PMC OA load
(~498k articles, bacteria ∪ viruses, [oa-full-ingest.md](oa-full-ingest.md)), and in what order
the components should be decided, given that some (embedding model, dimension, precision, chunk
boundaries) are build-time and unchangeable without re-embedding (oa-full-ingest.md § *What must
land before the load*, item 5) while others (reranker depth, fusion, budget, rewriting, filters)
are query-time.

### 2.2 The population(s) that define quality

Declared by the owner on 2026-09-06: **pointed, evidence-seeking questions of the kind a research
agent asks while building an argument**; consumer a research agent, so budgets are agent-sized
(r3 §1). The sets that exist or are planned:

| population | shape | state | where |
|---|---|---|---|
| TREC CDS 2014–16, 90 topics (10 dev / 80 confirmation) | clinical case narratives; the pointed property is carried by the endpoint, not the query (r3 §1.1) | labels quarantined; human read not started | [chunking-study-plan.md](chunking-study-plan.md) §2 E1, §3 |
| pointed set, dev, 177 queries | gold passage by construction; at its ceiling (`ERET` 0.90–0.96) | descriptive (guard 1 fails on the window) | [RESULTS-stage0b-prime.md](results/stage0/RESULTS-stage0b-prime.md) §6 |
| hard pointed set (#526) | no unique lexical key, deixis and single-source screens | planned, awaiting go (D1) | [PLAN-hard-pointed-set.md](results/design/PLAN-hard-pointed-set.md) |
| microbiology/virology pointed set (D15) | on a corpus matching the OA load and the available expert readers | owner leaning *both* (D15), not decided | [PLAN-micro-pointed-set.md](results/design/PLAN-micro-pointed-set.md) |
| real agent queries (#502) | opt-in logging on dev/demo | issue open | r3 §11 |

**Which of these defines system quality, and how several combine (conjunctive, or one primary with
the others as replication), is an owner decision — D17 for the chunking study, S3 here.** The record's own caveat
applies to every sub-study below: on two legs the *difficulty* of the query construction moved
the primary metric by −0.43 to −0.48 on `PH@1`, about 15× any candidate breadth effect
([results/README.md](results/README.md) § breadth-k), so a quality ranking of components is
conditional on the population it was read on.

### 2.3 Quality, as a hierarchy

| level | measure | definition | committed instance | what it misses |
|---|---|---|---|---|
| Q1 reach | `ERET@B` | per topic, fraction of evidence-bearing documents with ≥ 1 chunk admitted into the packed context at budget B, behind the reranker | r3 §3.1; measured in 0b′ | whether the admitted chunk holds the evidence |
| Q2 containment | `EPACK@B` | per reached document, fraction of evidence units fully contained in the admitted text (confirmatory: over the intersection of both arms' reached sets) | r3 §3.1; measured in 0b′ | tokens spent to get it |
| Q3 information density | **evidence delivered per delivered token at budget B** — proposed here: `ERET × EPACK` (or located evidence units) ÷ realised delivered tokens | **no committed metric is exactly this.** Closest: budget-matched `PR_B@4096` ([RESULTS-breadth-k.md](results/breadth-k/RESULTS-breadth-k.md) §7) and `EPACK@B` read at a fixed B — both hold tokens fixed rather than dividing by them. The field's name for budget-normalised evaluation is `EM @ l tokens` ([docs/papers/README.md](../papers/README.md) § *Naming decisions*) | that the agent may not need density if B is cheap |
| Q4 answer quality | `Correct`, `Faithful`, `Cited precisely`, `Abstains` | [SPEC-synthesis-stage.md](results/design/SPEC-synthesis-stage.md) §2 | proposed, not run; judge calibration not done |

**Which level is primary is an owner decision (S4).** The chunking study is at Q1/Q2; the
owner's question ("retrieval quality or information density") names Q3, which has no metric yet.
The synthesis spec argues Q4 is "the quality the user actually receives" (its preamble) and is where
"the *generator × chunking* interaction … the thing nobody has measured" (§4) lives.

### 2.4 Cost, term by term

| term | unit | what it depends on | measured today? | source |
|---|---|---|---|---|
| C1 one-off ingest compute | fleet-hours (GPU) | Σ tokens embedded × passes; method multiplier; parse/decompress | **yes, for one corpus**: 1.926 fleet-hours = ≈ 11.6 device-GPU-hours for 1.132 B SFR tokens over 6 arms at 167k tok/s; per arm, the four overlap-free sizes took 1,149.5 / 1,116.0 / 1,119.6 / 1,128.1 s for ≈ 184 M tokens each (within 3 %), so cost tracks Σ tokens, not chunk size | [RUN-PACKAGE.md](results/RUN-PACKAGE.md) §2 row 0a-5, §3; [embed_stats.json](results/stage0/artifacts/embed_stats.json) `arms` |
| | | semantic method | **yes**: ≈ 7× a `token_window` config of the same nominal size (6.78× in tokens on 20 papers) | [results/README.md](results/README.md) § revised item 9; [salmonella-amr-semantic-vs-pooled-2026-09-24.md](results/salmonella-amr-semantic-vs-pooled-2026-09-24.md) |
| | | the ~498k OA build | **no** GPU-hour figure; only storage projected | oa-full-ingest.md § *For the OA target* |
| C2 storage / RAM | bytes (this page uses decimal GB; sources mix GB and GiB) | vectors = points × dim × bytes/component; HNSW ≈ points × m × 8 B × 1.2; payload; ES index | **production `open-access` collection, 47,625,155 chunks**: vectors 47,625,155 × 4096 × 4 B = **780 GB** (= 727 GiB; **A** — metadata-and-kg.md §2b writes "727 GB" and "~113 GB" payload+index, which are GiB); on-disk collection **909.6 GB measured** (909,628,040,668 B, 847.2 GiB); ES index **82.3 GB measured**. **Not the OA target**: the ~498k-article load is ~16M chunks ≈ 0.5 TB fp32 4096-d (estimate) | [oa-year-backfill-2026-09-16.md](results/oa-year-backfill-2026-09-16.md) §6; [metadata-and-kg.md](metadata-and-kg.md) §2b; [asm-tenant-metadata-audit-2026-09-15.md](results/asm-tenant-metadata-audit-2026-09-15.md) §3 (ES sizes, points); [oa-full-ingest.md](oa-full-ingest.md) § *What actually gets embedded* |
| | | chunk size | **yes**: 423,386 → 196,247 vectors at 512/64 → 1024/0 (2.157×); 3.98× at 2048/0 | r3 §3.6; RUN-PACKAGE §3 |
| | | dim × precision | **arithmetic + external**: 4096-d fp32 ~770 GB in RAM today (the same quantity as the 780 GB above, that report's rounding); int8 with cold originals ~240 GB; binary ~40 GB (projections from Qdrant's formulas, 2026-08-22) | [reports/quantization-research.md](../../reports/quantization-research.md) § *RAM arithmetic* |
| C3 per-query compute / latency | ms per stage; GPU-s | query embed; ANN (`vector_ms`); BM25 (`text_ms`); rerank of `rerank_candidates` pairs; neighbour expansion; generation | **instrumented, not recorded**: every request logs `embed, vector, text, graph, fuse, rerank, rewrite, authz, expand, generate` stage timings and a 5-min p50/p95 rollup; **no production distribution is on the record**. Spot values: Qdrant warm 0.02 s vs cold ~14 s; asm-semantic end-to-end median 0.55 s (n = 30) | [ADR-0006](../adr/0006-execution-topology-revised.md) §4b; [tracing-a-503.md](../runbooks/tracing-a-503.md) §2–§8; [427-observability.md](427-observability.md); [RESULTS-asm-semantic-metadata-backfill-2026-09-16.md](results/RESULTS-asm-semantic-metadata-backfill-2026-09-16.md) |
| | | reranker throughput | **yes, offline**: 1,037 / 786 / 391 pairs/s at 256 / 512 / 2048-token chunks; 332 pairs/s in 0b′ | results/README.md § revised item 9; 0b′ §8 |
| | | generator | **offline only**: Scout 1.73 s/record at ≈ 12.4k prompt tokens; Qwen3.6-35B-A3B 7.73 s with reasoning on; served-path `generate_ms` never recorded | RUN-PACKAGE §2 row r3.1-ext; SPEC-synthesis §7 |
| C4 external model $ | USD | judges; any hosted generator | **judges only**: $195.32 for 867 Claude CLI calls ($0.133 / $0.302 / $0.245 per pair by model); synthesis judge projected $250 at list to $1,000 at that rate (estimate) | RUN-PACKAGE §2 row *judges*; SPEC-synthesis §7 |
| C5 operational complexity / risk | stores on the read path; failure modes | a third store (document record / object store); the KG as a third copy; store divergence (#471) | **qualitative only**; the cases are named, not costed | metadata-and-kg.md §2 *Cost, stated honestly*, §3 |
| C6 evaluation cost | person-hours; judge $; fleet-hours per arm | the study itself | **partly**: human read 32–48 person-hours; one pointed-set corpus ≈ 1.3–3.2 fleet-hours of embedding + ≈ 8–11 h of Scout and reranker wall time + 22–33 person-hours (estimate) | RUN-PACKAGE §2; PLAN-micro-pointed-set §8 |

**Several cost terms have no measurement today.** The list is §7.1. D16 asks the owner to choose
among C1 (embedding compute), C2 (vectors stored / index disk) and C3 (query latency) or a
combination for the *chunking* study; the same choice, extended to C4–C6, is S2 here.

### 2.5 The objective, in both forms

| form | statement | what it needs that does not exist |
|---|---|---|
| (A) quality-maximising | max<sub>S</sub> Quality(S | Q, B) subject to Cost(S) ≤ C₀, with C₀ a budget the owner states per cost term (RAM, fleet-hours, latency p95, $) | the budgets C₀; a cost ledger that can evaluate Cost(S) (§7.1) |
| (B) cost-minimising, non-inferiority | min<sub>S</sub> Cost(S) subject to Quality(S | Q, B) ≥ Quality(S_ref | Q, B) − ε per endpoint, conjunctive | a reference S_ref (the chunking study uses `fixed_tok512` on hybrid + rerank, r3 §3.6; but see chunking-study-plan.md contradiction 11 — three default specs coexist); a scalar or vector Cost(S) with stated weights |

The chunking study adopts (B) for one component (chunking-study-plan.md §1 *The objective*).
**Which form the system study adopts, and how the cost terms are weighted or constrained, is an
OPEN owner decision (S1, S2; D16 is the chunking-scoped instance of S2).** Two observations the
owner may want in hand when choosing: (i) form (B) needs a reference configuration, and the record
has not settled what ships (contradiction 11, held back by the owner until the E3 re-runs); (ii)
form (A) needs budgets, and the one latency budget on the record, ADR-0006's Go trigger, is "still
unstated" (ADR-0006 § *Decision*).

---

## 3. Component inventory

One row per component. "Production today" is read from repo docs and code at `463208e`, dated
2026-10-10; where a default and a deployed value differ the row says so. Server-wide knob
locations are in `python/ragstack/config.py` and request-time fields in
`python/ragstack/api/routers/query.py` (mirroring `contracts/schemas/query_request.json`) unless
stated. Code paths are for the Python implementation; the Go scaffold is not inventoried here.
Deployed-state facts that come only from a session memory note are marked *off-record*.

| component | decision knobs (where) | production today, per repo | quality effect measured | cost measured | unknowns |
|---|---|---|---|---|---|
| **Parsing / ingest** | JATS `<sec>/<title>/<p>` extraction (`ingestion/jats.py`); `article-type`, `license_code`, `pub-date`, `section` currently discarded (oa-full-ingest.md § *What must land*); token counter `chunk_token_counter="hf"` — an `hf` counter that cannot load its tokenizer **refuses at boot** rather than demoting to `estimate`; `estimate` (2.5 chars/token) is an explicit opt-in (`config.py` comment above `chunk_token_counter`) | 1,439,753 JATS, 47.6M chunks; chars/token 3.14–3.99 (mean 3.50), so the estimator *would* under-fill by 29 % and yield ~1.4× the chunks — a historical hazard, now a refusal (oa-full-ingest.md § *The token counter*) | **none** directly; `section_title` is JATS-only and unrecoverable for ASM ([metadata-consistency.md](metadata-consistency.md) §0.3) | extract 0.06 h per ~1.49M-chunk batch; load 1.79 h ([reports/oa-ingest-run.md](../../reports/oa-ingest-run.md) § *Where a batch's time goes*) | decompress+parse throughput gzip vs zstd (oa-full-ingest.md § *Open questions*); four extractor types ([structured-ingest.md](structured-ingest.md)) |
| **Chunking** | `chunk_method` (`fixed_token` default, 6 methods), `chunk_size` 512, `chunk_overlap` 64, semantic `buffer_size` 3 / `breakpoint_percentile` 80 / `min_length` 500; `context_window` 0–3 at query time; build-time per collection | `fixed_token`/512/64 in `Settings`; `fixed_token`/256/32 on the bulk CWL path; legacy asm serves `ragstack_sfr_tok256` (contradiction 11) | **the chunking study**: overlap powered null (dense, document metric); size unresolved on CDS, calibration shows reach ↓ / containment ↑ with size; realised size predicts the grid r = +0.811 (exploratory); `sentence512 − fixed512` +0.0606 (Holm p 0.097, n = 10, dense nDCG@10) unresolved; `header512` (R2) +0.042 reach / +0.027 containment at 0b′, unresolved, the one CDS contrast with joint power 0.948 | vectors 2.157× / 3.98× (r3 §3.6); overlap: 1/(1−f) = 1.14× at 512 (**A**, [chunking-evaluation.md](chunking-evaluation.md) § *Overlap belongs in the grid*), measured 423,386 / 376,516 = 1.124× (RUN-PACKAGE §3); semantic ≈ 7× embed, 3.4× more chunks at nominal 2048 ([RESULTS-stage1-legA.md](results/stage1/RESULTS-stage1-legA.md) §7.1) | which default is the baseline (owner: settle after E3 re-runs); section method not built (D8) |
| **Embedding model** | `embedding_api` sidecar/openai, `embedding_model`, `embedding_model_dim`, `embedding_endpoints`; sidecar `MODEL_NAME` (`sidecars/embedding/main.py`, default `BAAI/bge-base-en-v1.5` 768-d, fp32, cpu) | `Salesforce/SFR-Embedding-Mistral`, 4096-d, `max_model_len` 4096, six vLLM `--runner pooling` instances `:9001–:9006` (RUN-PACKAGE §4). `Settings` defaults (`text-embedding-3-small`, dim 768) and STATUS.md § *What works today* (BGE-base) are **stale relative to production** | **none in this project**: every study arm used SFR; no second embedder was ever compared on the same arms | 167k tok/s fleet (RUN-PACKAGE §3); cost tracks Σ tokens — the four sizes embed ≈ 184 M tokens each in 1,116–1,150 s (embed_stats.json `arms`); the super-linear-in-sequence-length caveat (chunking-evaluation.md § *Cost*) is below 3 % at these sizes | quality of any alternative model or of a smaller dimension; whether SFR's components are centred (binary quantization precondition, quantization-research § *Binary*) |
| **Vector store** | Qdrant; `VectorParams(size, distance=COSINE)` hard-coded, float32 default; **no `hnsw_config`, `quantization_config`, `on_disk`, or `search_params` anywhere in product code** (`stores/qdrant.py` 230–288, 375–381); payload indexes on `tenant_id`, `doc_id` only; `qdrant_timeout` 30 s | Qdrant 1.18.0; 47,625,155 × 4096-d fp32; HNSW m=16 / ef_construct=100, in RAM, no quantization **per quantization-research (2026-08-22) — inferred from Qdrant defaults, since product code passes no `hnsw_config` or `quantization_config`, not read from the server**; 909.6 GB on disk, 192 segments ([oa-year-backfill-2026-09-16.md](results/oa-year-backfill-2026-09-16.md) §6) | **none** (ANN recall vs exact never measured; the chunking study uses brute-force cosine, r3 §3.4) | storage yes (§2.4); write 0.140 GiB per 10k points net (backfill §6), 747 pts/s (backfill §7); warm 0.02 s vs cold ~14 s ([427-observability.md](427-observability.md) preamble) | recall/latency under int8 or binary at 4096-d: **no published quantization measurement exists for SFR or any Mistral-7B-derived 4096-d embedding** (quantization-research § *The gap nobody has filled*); within that survey's 17 sources there is no int8 nDCG datapoint ≥ 1024-d, one 3072-d int8 recall figure (0.932 semantic vs 0.863 traditional) and one 4096-d binary figure (0.98 recall@50 at 2× oversampling, Cohere) | RAM headroom on a 1.5 TB host |
| **Text search (BM25)** | ES `content: text`, standard analyzer, default BM25 k1 = 1.2, b = 0.75, single-field `match` with OR semantics; **no analyzer, similarity, boosting or shard settings in code** (`stores/elasticsearch.py` 135–215); `elasticsearch_timeout` None (client 10 s) | ES 8.13.4 / 8.19.3; 82.3 GB for the OA index; heap 1 g live (*off-record*, session note) | **calibration only**: BM25 favours coarser chunks on containment (Q8, 0b′ §5); in-process BM25 reproduces served ES at overlap@50 0.9469 (0b′ §7) | index bytes yes; query latency: production ES answered real queries at 7–31 ms during the OA build ([reports/oa-ingest-run.md](../../reports/oa-ingest-run.md) § *Final*); 34–76 ms spot readings during the year backfill ([oa-year-backfill-2026-09-16.md](results/oa-year-backfill-2026-09-16.md) §7 table) | effect of a title/section field, stemming, or a biomedical analyzer; UAX#29 vs `\w+` tail (min overlap 0.22) |
| **Fusion** | RRF, `rrf_k` 60, rank-only, **unweighted — no alpha or per-leg weights exist** (`scoring/scorers.py` 23–59); `retrieval_candidate_multiplier` 2 (`retrieval/retriever.py` 173, fuse at 208); `retrieval_mode` per request (hybrid default) | hybrid, each leg fetches max(top_k, rerank_candidates) × 2 = 100 candidates, fused, cut to 50, reranked (`api/routers/query.py` 442–478) | **calibration**: hybrid is "not the average of its legs" and flattens the size contrast (0b′ §5) | nil beyond the two legs' own costs | any weighted or score-based fusion; the mode main effect read as a contrast |
| **Reranker** | `rerank_enabled` True, `rerank_candidates` 50, `reranker_model`; sidecar `MAX_LENGTH` 4096, batch 32, fp16 on cuda (`sidecars/crossencoder/main.py`); request-time `rerank`, `rerank_candidates` | `BAAI/bge-reranker-v2-m3` on `:50052`, GPU 0 shared with an SFR instance (~1.3 GB headroom), ≤ 4 in flight (RUN-PACKAGE §4) | **measured**: reverses a first-stage verdict (grade≥2 MRR@10 +0.299 [+0.074, +0.542], [RESULTS-step3-real-experiment.md](results/step3/RESULTS-step3-real-experiment.md)); rerank-off reverses signs on frozen pools (Q9, 0b′ §5); truncates at 4,096 tokens per pair, position effect real below it (chunking-evaluation.md § *The reranker*) | throughput by chunk size yes; per-query `rerank_ms` on the served path **not recorded** | depth (50 vs 100 vs 200) vs quality; a different reranker model |
| **Knowledge graph** | `graph_backend` memory/neo4j/disabled; `use_graph` True per request; `kg_extraction_enabled` False; `graph_max_triples_per_collection` 200k; `graph_context_depth` 1; `graph_context_score` 0.5 (inert under RRF); `LLMKGExtractor` one LLM call per chunk (`graph/extractor.py`) | `GRAPH_BACKEND=disabled` on all four tenants per [ops/coconut/INVENTORY-2026-09-09.md](../../ops/coconut/INVENTORY-2026-09-09.md) § *Findings* and `ops/coconut/restore.sh` (the neo4j block); [metadata-and-kg.md](metadata-and-kg.md) § *Status* says disabled on lucid/dev/demo and *unset* on asm — the two disagree on asm and neither was re-checked here; production Neo4j dead since 2026-06-04 (INVENTORY § *Findings*; [ops/coconut/NOTES.md](../../ops/coconut/NOTES.md)); worker image lacks the graph extra (#374/#404); **no tenant has triples** (metadata-and-kg.md §4 *When to narrow*) | **none**; out of scope for the chunking study (r3 §1, §6). The only measurement on the metadata track, MeSH topic transfer (micro-F1 0.596 → 0.591, [mesh-transfer/README.md](results/mesh-transfer/README.md)), is exploratory and not a graph result | **none**: STATUS.md § *Graph leg of the lifecycle (#350)* lists "the real-LLM throughput measurement" among the things not yet done; per-query leg cost described, not measured (metadata-and-kg.md §4) | entity resolution, predicate vocabulary, what to extract (metadata-and-kg.md §3 items 1–3); whether it adds evidence at all |
| **Metadata & filters** | `filters` equality/membership only, ANDed; `exclude_boilerplate`; payload indexes `tenant_id`, `doc_id`; ~33× duplication of document fields on chunks | `year` covers 14.8 % of `open-access` ([date-filtering.md](date-filtering.md)); asm `ragstack_sfr_tok256` at 0 % on journal/date/pmcid (audit); **#471: a filter works on bm25, returns nothing on vector, looks fine on hybrid** | **none** as a quality lever; #471 is a measured correctness defect | backfill: 0.140 GiB per 10k points; one document correction = ~33 payload rewrites in two stores | unindexed filters scan every point (oa-full-ingest.md item 4); range operator not built |
| **Query rewriting** | `rewrite_strategies` default `["passthrough"]`; `multiquery` (n = `multiquery_n` 3) and `hyde` exist only when an LLM is configured; prompts hard-coded (`rewriting/rewriters.py`) | off | **none** | **none**; inferred n extra LLM calls + n embeds + n retrievals per query | everything; note [ANSWER-sufficiency-and-judges.md](results/design/ANSWER-sufficiency-and-judges.md) rejects HyDE in its original form and adopts it inverted — a design position, not a measurement |
| **Delivery / packing** | `top_k` 5 (≤ 100); `context_window` 0–3 (budget × (2w+1)); `llm_max_context_chars` 8000; `retrieval_max_per_doc` 0; packing rule A1 in the harness (r3 §3.2) | 8,000 chars ≈ 2k tokens; a 16k context would be cut ~8× (#519, SPEC-synthesis §5) | **measured (calibration)**: fixed-k vs budget-matched reverses the size ordering, `tok256` over `tok2048` by 2.2–3.8× at B = 4,096 (breadth-k §7); `nbr1_512` below the 0.15 reach floor at 16k (0b′ §3) | tokens delivered ∝ B by construction | effect of the 8k cap on answers (unmeasured); whether agents want 16k (D10, product) |
| **Generator** | `llm_endpoint`, `llm_model`, `llm_max_output_tokens` 512, request-time `llm`, `template` ([prompt-templates.md](prompt-templates.md), ADR-0008) | `mango:8003` Llama-4-Scout-17B-16E (60k ctx) on every tenant; alternatives `mango:8000` Qwen3.6-27B, `mango:8004` Qwen3.6-35B-A3B ("the server admits 4", RUN-PACKAGE §4) | **none**: answer quality never measured (E4 proposed) | offline s/record only (§2.4 C3); a per-request TTFT/e2e profile of `:8004` exists only in a session note (*off-record*) | the generator × chunking interaction (SPEC-synthesis §4); thinking on/off |
| **Judges / eval models** | Scout, Qwen (local, $0); Claude via CLI or API; human readers | labels: Scout ×20 + Qwen ×10 on 3,738 pairs (RUN-PACKAGE row conf-label) | graded-support reliability 0.9205 at 30 readings on the 308 dev pairs; validity pending human read (chunking-study-plan.md E1) | $195.32 Claude; ≈ 62 h wall local labeling; 32–48 person-hours per read | whether any judge is *valid*, not only reliable |

---

## 4. Impact map

### 4.1 Component × outcome

Cells: **M** measured (cited), **I** inferred (reasoning given), **U** unknown, **A** arithmetic.
Direction is for "coarser / cheaper / on → off" as the row states.

| component (direction) | retrieval quality (Q1/Q2) | information density (Q3) | answer quality (Q4) | ingest cost (C1) | storage (C2) | query latency / cost (C3) |
|---|---|---|---|---|---|---|
| chunk size ↑ (256 → 2048) | **M** reach 0.277 → 0.112, containment 0.188 → 0.696 (0b′ §3, calibration); contested on document nDCG across legs | **M** budget-matched `PR_B@4096` favours 256 by 2.2–3.8× (breadth-k §7) — the closest Q3 reading | **U** (S1–S3 of the synthesis spec, not run) | ≈ 0 (**M** the four sizes embed ≈ 184 M tokens each in 1,116–1,150 s, embed_stats.json `arms`; super-linearity < 3 % here) | **M** 2.157× / 3.98× fewer vectors | **I** fewer, longer pairs per rerank: 391 vs 1,037 pairs/s (**M** throughput) |
| overlap 64 → 0 | **M** powered null, dense, document metric (Leg A/B) | **U** | **U** | **A** −12.5 % tokens | **A** 1/(1−f) = 1.14× at 512; **M** 1.124× (RUN-PACKAGE §3) | ≈ 0 (I) |
| chunking method: semantic vs fixed | **M** worst kind on Leg A (exploratory); not compared at matched realised size | **U** | **U** | **M** ≈ 7× embed | **M** 3.4× more chunks at nominal 2048 (Leg A §7.1) | **I** larger chunks → fewer pairs |
| structure-aware (`sentence`, `header512`, section) | **M** `sentence512 − fixed512` +0.0606 unresolved (Leg A, n = 10); `header512` R2 +0.042 / +0.027 unresolved at 0b′, the one powered CDS contrast | **U** | **U** | ≈ 0 (I) | ≈ 0 (I) | ≈ 0 (I) |
| embedding dim 4096 → 1024 | **U** here (external: not measured for SFR) | **U** | **U** | **I** a different model, re-embed everything | **A** 4× fewer bytes at equal precision; oa-full-ingest.md's 0.2 TB column is 1024-d **+ int8** (2.5× vs 0.5 TB) | **I** faster ANN; unmeasured |
| precision fp32 → int8 / binary | **U** here; external, from the 2026-08-22 survey: int8 1.5–3.5 % nDCG@10 un-rescored at ≤ 768-d; one 3072-d int8 recall figure; binary 0.98 recall@50 at 2× oversampling at 4096-d (Cohere); nothing for SFR (quantization-research) | **U** | **U** | **I** a re-index, not a re-embed | **A** ~770 → ~240 GB (int8, cold originals) or ~40 GB (binary) | **I** rescore from NVMe per query; unmeasured |
| ANN params (m, ef, hnsw_ef) | **U** (never compared with exact) | **U** | **U** | **U** | **A** HNSW ≈ 7 GB today | **U** |
| BM25 config (analyzer, fields) | **U** as a lever; **M** served ≈ in-process | **U** | **U** | **U** | **M** 82.3 GB / 47.6M chunks | **M** 7–31 ms production (oa-ingest-run); 34–76 ms backfill spot |
| retrieval mode: hybrid vs legs | **M** hybrid flattens the size contrast; BM25 favours coarse on containment (0b′ §5, calibration) | **I** follows Q1/Q2 | **U** | 0 | **A** two indexes vs one | **I** two legs + fuse vs one |
| fusion (RRF k, weights) | **U** (no weighted fusion exists to measure) | **U** | **U** | 0 | 0 | ≈ 0 |
| reranker on → off | **M** reverses verdicts (step3; 0b′ Q9) | **I** large: reranking decides what packs | **U** | 0 | 0 | **M** throughput; **U** served `rerank_ms` |
| reranker depth 50 → 100/200 | **U** | **U** | **U** | 0 | 0 | **A** pairs ∝ depth |
| knowledge graph on | **U** | **U** | **U** | **I** one LLM call per chunk: with 47.6M chunks this dwarfs embedding unless capped (200k triples cap exists) | **U** (triples + evidence spans) | **I** entity extraction + one neighbourhood query per entity (metadata-and-kg.md §4) |
| metadata filters | **M** defect: vector leg silently empty under a filter (#471) | **U** | **U** | **M** backfill 0.140 GiB / 10k points | **A** ~33× duplication | **I** unindexed filter scans every point |
| query rewriting on | **U** | **U** | **U** | 0 | 0 | **I** +n LLM calls, +n embeds, +n retrievals |
| delivered budget B ↑ (2k → 16k) | **M** ordering reverses between fixed-k and budget-matched; reach floor binds for coarse/neighbour arms at 16k | **A** density = evidence / B: more B lowers density unless evidence scales | **U** (#519 cap effect never measured) | 0 | 0 | **I** generator prompt tokens ∝ B (Scout 1.73 s/record at 12.4k) |
| `context_window` 0 → 1 | **M** `nbr1_512` reach below floor at 16k; R4 superiority underpowered (0b′) | **I** lower: 3× tokens per source | **U** (S2) | 0 | 0 | **M** one batched `get_chunks` per hop (perf test: 3 store calls) |
| generator model | — | — | **U** (never measured) | 0 | 0 | **M** offline s/record; **U** served |

### 4.2 Ranked: what has the most impact on cost

| rank | lever | status | evidence |
|---|---|---|---|
| 1 | **embedding dimension × precision** (bytes per vector) | **arithmetic, unmeasured for quality** | Two corpora, stated separately. *Production `open-access`, 47.6M chunks:* 4096-d fp32 = 780 GB of vectors (**A**; 909.6 GB on disk measured, backfill §6); int8 with cold originals ~240 GB, binary ~40 GB (quantization-research projections). *The ~498k OA target of §2.1, ~16M chunks:* ~0.5 TB fp32 4096-d vs ~0.2 TB at 1024-d + int8, 2.5× (oa-full-ingest.md table, estimates; "0.5 TB fits the free space today"); 1024-d alone would be 4× fewer bytes (**A**). Largest single storage/RAM lever on either corpus; on the production collection it decides whether the index fits a 1.5 TB host |
| 2 | **chunk size** (vectors per article) | **measured** | 2.157× / 3.98× fewer vectors (r3 §3.6); ES and payload shrink with it (I) |
| 3 | **chunking method** | **measured** | semantic ≈ 7× embed, 3.4× index; `sentence`/structure ≈ 0 extra |
| 4 | **knowledge graph extraction** | **inferred, unmeasured** | one LLM call per chunk is the only ingest step whose unit cost is a generation, not an embedding; the 200k-triple cap is the only bound |
| 5 | **delivered budget B and generator** | **inferred; offline s/record measured** | per-query generation cost ∝ prompt tokens; the #519 cap currently makes this small (≈ 2k tokens) and a 16k decision would raise it ~8× |
| 6 | **reranker depth** | **arithmetic; throughput measured** | pairs per query ∝ `rerank_candidates`; 391–1,037 pairs/s |
| 7 | **query rewriting** | **inferred** | multiplies retrieval and adds LLM calls per query; off today |
| 8 | **BM25 / ES configuration** | **measured small** | 82.3 GB vs 780 GB of vectors; 7–31 ms in production |
| 9 | **ANN parameters** | **unknown** | HNSW ≈ 7 GB (projection); latency effect unmeasured |
| 10 | **overlap** | **measured small** | 1.124× vectors at 512/64 vs 512/0 (RUN-PACKAGE §3) |

### 4.3 Ranked: what has the most impact on quality / information density

| rank | lever | status | evidence |
|---|---|---|---|
| 1 | **reranker on/off** | **measured, on thin evidence** | reverses first-stage verdicts (step3 grade≥2 MRR@10 +0.299 [+0.074, +0.542], **n = 10 topics**; 0b′ Q9, **calibration**, rerank-off on frozen pools). No powered reranker contrast exists; the rank rests on the *direction* being reproduced twice. "Anything that will ship behind a reranker must be evaluated behind one" (chunking-evaluation.md § *Reranking reorders the grid*) |
| 2 | **delivery: budget B and fixed-k vs budget-matched reading** | **measured (calibration)** | size ordering reverses, 2.2–3.8× (breadth-k §7); reach floor binds at 16k for coarse and neighbour arms (0b′ §3). On the served path the 8k-char cap, not chunking, bounds what the generator sees (SPEC-synthesis §5) — its effect on answers is **unmeasured** |
| 3 | **chunk size (realised)** | **measured, direction only; verdict unresolved** | reach ↓ containment ↑ (0b′ §3); realised tokens predict the grid r = +0.811 (exploratory); hybrid is the mode in which it matters least |
| 4 | **retrieval mode / fusion** | **calibration** | hybrid flattens N1/N3 containment contrasts; BM25 favours coarse (0b′ §5) — a modifier of other levers more than a main effect, which has not been read |
| 5 | **structure-aware chunking** | **unresolved; one powered contrast waiting** | `sentence512 − fixed512` +0.0606 (Holm p 0.097, Leg A, n = 10); `header512` R2 +0.042 / +0.027 at 0b′ with joint power 0.948 on CDS |
| 6 | **embedding model / dimension / precision** | **unknown here** | external figures only, none for SFR or any 4096-d Mistral-derived model; no second embedder or precision ever compared on the project's arms |
| 7 | **knowledge graph** | **unknown** | no triples anywhere; no measurement; the chunking study excluded it |
| 8 | **query rewriting** | **unknown** | exists, off, never measured |
| 9 | **metadata filters** | **measured as a defect** | #471 — a correctness, not a quality, lever until fixed |
| 10 | **overlap** | **measured null** | dense, document metric |

The population caveat from §2.2 stands above this table: the leg-difficulty term (−0.43 to
−0.48 on `PH@1`) is larger than every component effect on the record.

---

## 5. Interactions

From the record; each makes two components non-separable in a study design.

| interaction | what the record shows | consequence for design | source |
|---|---|---|---|
| chunk size × reranker | no size contrast resolves behind the reranker on either leg; dense 2048−256 −0.0754 shrinks to −0.0136 reranked | size must be read behind the reranker and the with/without delta reported | [RESULTS-stage1-legB.md](results/stage1-legB/RESULTS-stage1-legB.md) §8.1 |
| chunk size × fusion mode | hybrid flattens the containment contrast; BM25 sharpens it | mode is a factor in any size or structure arm, not a fixed frame | 0b′ §5 |
| chunk size × budget B × metric construction | fixed-k favours coarse; budget-matched favours fine; the 2048 arm was projected to admit 8–9 chunks at 16k (r3 §3.2) and packed 7.8 documents measured (0b′ §3) | every quality number must state B and whether k or tokens were matched | breadth-k §7; r3 §3.2; 0b′ §3 |
| chunk size × reranker truncation | 4,096-token cut per pair; position effect below it | chunk + query must stay under 4,096 reranker tokens | chunking-evaluation.md § *The reranker* |
| chunk size × tokenizer | sizes in SFR tokens vs budgets in generator tokens under-supply coarse arms ≈ 20 % | one tokenizer per study (D13) | r3 §3.3 |
| embedding dim × precision × store RAM | quantization adds a copy; RAM falls only if originals go cold, which puts NVMe rescoring on every query | dimension, precision and the on-disk tier are one decision, not three | quantization-research § *The question* |
| chunk count × metadata duplication | 33× duplication makes every document-level correction a 47.6M-point job | chunk size changes the cost of every metadata operation | metadata-and-kg.md §2 |
| filters × retrieval mode | #471: a filter empties the vector leg; hybrid looks correct | no filter study is meaningful until #471 is fixed | metadata-and-kg.md §4 |
| graph scope × collections | one graph store for all collections vs per-collection vector/text stores | the graph leg must push and re-check `collection` | metadata-and-kg.md §3 item 4 |
| `context_window` × budget | budget scales × (2w+1); neighbours cost reach at 16k | delivery arms are packing transforms on an index arm, not a crossed axis | r3 §3.5; `llm.py` 211 |
| generator × chunking | "the *generator × chunking* interaction is the thing nobody has measured" | the synthesis stage is the only place it can be read | SPEC-synthesis §4 |

---

## 6. Sub-studies, ranked by value of information

Ranking rule: expected impact (§4.2, §4.3) × uncertainty (U > I > M) ÷ experiment cost. The
ranking is a **proposal**; costs are estimates unless cited. "Decision informed" names the knob
that would change.

| # | sub-study | question | decision informed | cost terms | existing evidence | experiment (sketch) | rough cost (estimate) | depends on |
|---|---|---|---|---|---|---|---|---|
| 0 | **cost ledger** (prerequisite) | what does one query and one ingest actually cost, per stage, on the served path? | all of them; whether (A) or (B) is even evaluable | C1–C4 | stage timers exist (ADR-0006); no distribution recorded; GPU-hours per fleet measured once | read the dev tenant's `api-dev.log` rollups for a fixed query set; record `embed/vector/text/rerank/expand/generate_ms` p50/p95 per configuration; a one-page ledger schema (§7.1) | days, no GPU beyond the dev tenant | none — **first** |
| 1 | **chunking** | see [chunking-study-plan.md](chunking-study-plan.md) | chunk size, overlap, structure, delivery | C1, C2 | the whole record | as planned there | blocked on the human read | D4, D6, D13, D15 |
| 2 | **embedding dimension × precision** | at 4096-d, what do int8 / binary / a 1024-d model cost in candidate-set recall and in `ERET`/`EPACK` behind the reranker, and what do they buy in RAM? | the build-time model/quantization decision (oa-full-ingest.md item 5) | C2 (largest lever), C3 | quantization-research (external; **no published measurement for SFR or any 4096-d Mistral-derived model**; its § *Recommended protocol* is the #333 protocol, unrun) | **primary measurable:** candidate-set overlap / recall vs exact (`exact=true`) at depth 50 — the depth fed to the reranker — over ≥ 1,000 queries, with oversampling {1, 1.5, 2, 3, 4} × rescore on/off (quantization-research § *Recommended protocol*; 100 queries "is too coarse"). On the Stage 0 arms' frozen `emb/` (34 GB, 2,216,716 vectors) this needs no new embeddings. **Q1/Q2 on dev (10 CDS topics; 177 pointed queries at ceiling) are descriptive only.** A smaller-embedder variant re-embeds the pinned `rows_<arm>.json` spans. **Reuse rule:** anything reading `emb/` runs at `55a0fc2` or re-embeds — `main`'s `chunkers.py` against those vectors is a silent mismatch ([docs/papers/README.md](../papers/README.md) § *Every artifact records which code produced it*) | ≈ 0 new SFR embeddings for quantization; one re-embed (≈ 1.93 fleet-h) per alternative model | the ledger (RAM/latency numbers); `emb/` backup (D12) |
| 3 | **reranker depth and model** | does depth 50 → 100 → 200 change evidence delivered; does a second reranker agree? | `rerank_candidates`; GPU 0 sizing | C3 | throughput by chunk size; reversal measured; depth never varied | re-pack 0b′'s frozen pools at three depths; a second cross-encoder on the same pools | minutes per depth on `:50052` (0b′: 89,668 pairs in 270 s, inside a 7.5-min retrieval wall; RUN-PACKAGE §2 row 0b′, 0b′ §1) | none |
| 4 | **generator budget and answer quality** | does the 8k-char cap cost correct answers; does 16k buy any? | #519 (D10); `llm_max_context_chars`; generator choice | C3, C4 | SPEC-synthesis (proposed); no answer metric ever read | the synthesis stage as specified, with B ∈ {2k, 4k, 16k} as the primary axis instead of a descriptive one | ≈ 9,400 generations, 1–1.5 h wall; judge $250–$1,000 (SPEC-synthesis §7) | `answer-read` kind; a pointed population (D1/D15); D9 |
| 5 | **fusion and mode** | is RRF k = 60 unweighted the right fusion; what does each leg contribute on pointed queries? | `rrf_k`; a weighted fusion if one is built | C3 (small) | 0b′ mode × size tables (calibration) | re-fuse 0b′'s frozen leg rankings offline under RRF k ∈ {10, 60, 200} and weighted variants; read Q1/Q2 | CPU minutes | none |
| 6 | **BM25 configuration** | does a title/section field, stemming or a biomedical analyzer move the lexical leg? | ES mapping (build-time for the text index) | C2 (small), C3 | concordance 0.9469; UAX#29 tail | in-process BM25 variants over one arm's chunks; one dev-tenant ES index per variant with a verifying delete | CPU minutes; the one ES index 0b′ loaded (423,386 chunks) took 53 s (0b′ §7) | none |
| 7 | **query rewriting** | do multiquery / HyDE add reach on pointed queries, at what latency? | `rewrite_strategies` default | C3 | none | the 177 pointed queries, rewritten by Scout, retrieved under hybrid + rerank; paired `ERET` and `rewrite_ms` | ≈ 177 × 3 Scout calls; minutes | the ledger for latency |
| 8 | **knowledge graph** | does a graph leg add evidence a pointed query could not reach, and what does building it cost per chunk? | whether the KG is in scope at all (S6) | C1 (potentially largest), C2, C5 | none; design questions open (metadata-and-kg.md §3) | a bounded pilot: extract on the 20 Salmonella-AMR papers (already ingested), measure triples/chunk, LLM-s/chunk, and whether any pointed query's gold is reached only via the leg | Scout time per chunk × 2,090 chunks (hours); needs a working Neo4j or the memory backend | entity resolution and predicate decisions first; Neo4j restored or memory backend accepted |
| 9 | **ANN parameters** | HNSW recall vs exact at m/ef on 4096-d; latency | Qdrant collection params (build-time) | C2 (HNSW ≈ 7 GB), C3 | none | brute-force vs HNSW top-50 overlap on a dev collection; `bench_filter_truncation.py` already sweeps `hnsw_ef` | dev tenant only; hours | the ledger |
| 10 | **metadata filters** | after #471, what does filtering cost (indexed vs unindexed) and does `year`/`article-type` filtering raise density? | payload indexes; the document-record split | C2, C3 | #471 open; 14.8 % `year` coverage | blocked until #471 is fixed and a backfill exists | — | #471; date-filtering.md Part B |

**Why each sits where it does** (impact × uncertainty ÷ cost, one line each):

0. Ledger — every other row's cost column is an estimate until it exists; days of work, no GPU.
1. Chunking — already designed, pre-registered and partly run; its remaining cost is the human read, which is sunk into the critical path either way.
2. Dimension × precision — the largest cost lever (§4.2 rank 1) with the highest uncertainty (no quality datapoint for this model anywhere), and the quantization half costs no new embeddings.
3. Reranker depth — rank-1 quality lever (§4.3) whose depth has never been varied; the experiment is a re-pack of frozen pools, minutes.
4. Generator budget and answer quality — the only sub-study that can answer #519; high impact, unmeasured, but it needs a contract change, a judge calibration and ≈ $250–1,000, so it ranks below the free re-packs.
5. Fusion — moderate impact (hybrid is a modifier), cheap (CPU re-fuse), uncertainty moderate because the mode × size tables already exist.
6. BM25 configuration — low cost lever (82.3 GB, tens of ms) and a modest quality lever; cheap but low expected impact.
7. Rewriting — unknown quality effect, inferred per-query cost; cheap pilot, but off in production so no decision is pending.
8. **Knowledge graph — potentially the largest C1 term (§4.2 rank 4), yet rank 8:** its uncertainty is total, but so is its *experiment cost* — nothing can be measured until entity resolution, a predicate vocabulary and a working store exist (metadata-and-kg.md §3), and the owner excluded it from the chunking study. A bounded pilot on 20 papers is the cheapest way to turn "unknown" into a number; a full study is not affordable before that.
9. ANN parameters — low cost (HNSW ≈ 7 GB) and the quantization sub-study subsumes the recall-vs-exact measurement.
10. Filters — blocked on a correctness fix (#471); no experiment is meaningful first.

---

## 7. Shared infrastructure

### 7.1 A cost ledger (does not exist)

The owner's question cannot be answered without one. Proposed shape, one row per
(configuration, population, budget), **proposal**:

| column | unit | source today | gap |
|---|---|---|---|
| ingest fleet-hours, Σ tokens embedded, passes | h, tokens | `estats_*.json` per arm (RUN-PACKAGE §2) | only for study arms; not for production builds |
| vectors, dim, bytes/component, HNSW bytes, payload bytes, ES bytes | bytes | audit §3; metadata-and-kg.md §2b | measured once, by hand, for production; no per-arm figure |
| per-query p50/p95 per stage (`embed, vector, text, graph, fuse, rerank, rewrite, expand, generate`) | ms | the request log and 5-min rollup (tracing-a-503.md §8) | never collected into a table; only `POST /v1/query` and `/v1/retrieve` are rolled up |
| reranker pairs per query; generator prompt/completion tokens per query | count | harness only (0b′ §8; SPEC-synthesis §2 *Cost*) | not recorded on the served path |
| external $ | USD | judge runs only | none for the product path |
| operational: stores on the read path, timeouts hit, 503 reasons | count | the 503 body and `rid` lines (runbook) | not aggregated |

Terms with **no measurement anywhere** (from the ops sweep of the repo, 2026-10-10): per-query
$/GPU-s on the product path; a production per-stage latency distribution; served-path
`generate_ms`; served-path `rerank_ms`; rewrite cost; KG size, extraction throughput and leg cost;
ES bytes per chunk; Qdrant RAM in production and recall under quantization; load-test RPS/p99
(SPEC target ≥ 100 RPS at p99 < 500 ms, #118 never run); bulk span-fetch latency for the offset
model; GPU-hours for the ~498k OA build; gzip vs zstd decode throughput; ADR-0006's trigger
thresholds.

### 7.2 Everything else the sub-studies share

| need | state | where |
|---|---|---|
| query populations and gold | CDS (quarantined), pointed dev (ceiling), hard set and micro set (planned) | §2.2 |
| harness and frozen inputs | `stage0/` ships its harness; `emb/` 34 GB at `/rag/tmp/stage0-conf/`, not backed up (D12); past harnesses keep their pins | RUN-PACKAGE §3; [docs/papers/README.md](../papers/README.md) § *Every artifact records which code produced it* |
| provenance | `experiment_provenance()` on main (#682); `citable: false` on dirty trees | `python/ragstack/provenance.py` |
| snapshots and regression | `/rag/snapshots/` pinned clones; `python/tests/regression/` goldens (#687); dev-10 dense check | chunking-study-plan.md E3 |
| perf budgets | `python/tests/perf/` — all on in-memory or fake backends; **no live-store or GPU budget exists** | `make perf-python` |
| endpoint politeness | SFR ≤ 2 in flight per endpoint; `:50052` ≤ 4; mango ≤ 4 (`:8004` "admits 4", RUN-PACKAGE §4); GPUs 6–7 reserved and asserted idle. A production-vs-bulk split of the six SFR ports exists only in a session note (*off-record*); [ops/coconut/INVENTORY-2026-09-09.md](../../ops/coconut/INVENTORY-2026-09-09.md) lists `:9001–:9006` identically | RUN-PACKAGE §4; INVENTORY § ports |
| store discipline | dev tenant only (`:24041`/`:24043`), prefixed scratch indexes, verifying delete; never a default URL | CLAUDE.md § *Working notes*; 0b′ §7 |
| statistics | power floor before the bar; powered null ≠ unresolved ≠ GATE-NOT-EVALUABLE; cluster bootstrap by topic/document | results/README.md § *The discipline* |
| write-up tiers | record / lab book / paper; every number resolves to a committed artifact | `.claude/skills/study-writeup/SKILL.md` |

---

## 8. Open decisions for the owner

All **OPEN — OWNER**. Recommendations are marked as such and are this page's, not the record's.

| # | decision | options | recommendation (this page) |
|---|---|---|---|
| S1 | **objective form** | (A) max quality s.t. cost ≤ C₀ · (B) min cost s.t. quality ≥ ref − ε · (C) report the Pareto front and decide per component | (B) for build-time components where a reference exists (it matches the chunking study and needs no weights); (A) or (C) for query-time knobs, where no reference is natural. Needs S2 either way |
| S2 | **cost terms and their weights or constraints** (D16 generalised: D16's options are vectors / embedding compute / index disk / query latency / a combination, for chunking) | scalar with weights; one constraint per term (RAM ≤ x TB, p95 ≤ y ms, fleet-h ≤ z, $ ≤ w); RAM only | constraint form, one per term, with RAM and query p95 first — they are the two the host and the agent feel. The weights cannot be chosen before the ledger (sub-study 0) shows the magnitudes. Deciding D16 first, for chunking, is the cheap rehearsal |
| S3 | **which population(s) define quality, and how they combine** (D17 generalised) | CDS only · pointed (hard set on CDS) · microbiology/virology set (D15) · both · real agent queries (#502); conjunctive across all vs one primary with replication (D17) | the owner's leaning, *both* (D15), plus turning on #502 logging now so a real population exists by the time the hard set is read; on combination, defer to D17's outcome rather than deciding twice |
| S4 | **primary quality level** | Q1/Q2 evidence delivered (the chunking study's) · Q3 information density · Q4 answer quality | keep Q1/Q2 as the confirmatory level for build-time components (cheap, deterministic, already instrumented); define Q3 as a reported column (evidence per delivered token) rather than a new primary; make Q4 the primary for the generator/budget sub-study only, where it is the only thing that can answer #519 |
| S5 | **the reference configuration S_ref** | `fixed_tok512` 512/64 on hybrid + rerank as r3 §3.6 · the tok256 bulk path · settle after the E3 re-runs (owner, 2026-10-10) | as the owner already decided: after E3; until then every sub-study names its own reference arm explicitly |
| S6 | **is the knowledge graph in scope?** | out (as in r3 §1) · in as a bounded pilot (sub-study 8) · in fully (needs entity resolution, predicate vocabulary, Neo4j restored) | a bounded pilot only, gated on the three design questions in metadata-and-kg.md §3 being answered first; nothing on the KG can be *measured* until a store with triples exists |
| S7 | **embedding model / dimension / precision as a sub-study** | defer until the chunking verdict · run sub-study 2 now on the frozen `emb/` (quantization needs no new embeddings) | run the quantization half now; it is the largest cost lever (§4.2 rank 1), costs no GPU, and its result changes whether the OA load fits the host at all |
| S8 | **#519 — deliver a 16k context?** (D10, product) | keep 8k chars · raise to B · make it per-template | not a study question; but sub-study 4 cannot read "answer quality at 16k" against a product that delivers 2k — decide, or declare the study's B hypothetical |
| S9 | **order of the sub-studies** | §6 order · chunking verdict first, everything else after · cost ledger first | cost ledger first (it is cheap and every other row cites it), then 2, 3, 5 on frozen pools while the human read runs |
| S10 | **the 2026-09-21 external review** | put it on the record (committed file) · a dated, attributed summary of it under `docs/plans/results/design/` if the review text itself cannot be committed · leave it as session context | put it on the record in whichever of the first two forms is permissible, since both this plan's objective and chunking-study-plan.md §1 descend from it and neither can cite it |

---

## 9. Proposed first steps

**Proposal. None of this is decided.**

1. **Owner:** S1–S4 and S9 at one sitting; they decide what a "result" of this plan is.
2. **Agent, no GPU:** sub-study 0 — the cost ledger schema and its first fill from the dev
   tenant's request log for a fixed query set, plus the production storage row from the audit
   files already on the record. Target: one table that states, for the shipping configuration,
   every C1–C4 number that exists and *absent* for every one that does not.
3. **Agent, on frozen pools (0b′), no new embeddings:** sub-studies 3 (reranker depth) and 5
   (fusion) as descriptive re-packs with CIs and δ80, pre-registered in one short spec.
4. **Agent, on frozen `emb/`:** sub-study 2's quantization half (int8 / binary rescore vs exact)
   — after D12's backup, since it reads the only copy.
5. **Owner:** S6 (KG) and S8 (#519); both gate sub-studies that cannot otherwise start.
6. **Owner:** S10 — commit the external review so §1 can cite it.
7. Everything else waits on the chunking study's critical path (the human read), as planned there.

---

## 10. What this document is not

- **Not the record.** Every number points at `docs/plans/results/` or a committed plan; quote the
  record file, not this page.
- **Not a decision.** Every S-item stays the owner's; every ranking in §4 is labelled measured or
  inferred and the inferred rows are this page's reasoning.
- **Not a replacement for [chunking-study-plan.md](chunking-study-plan.md).** That plan becomes
  sub-study 1 here unchanged: its goal, SG1–SG6, E1–E4, experiments, gates and D1–D15 are
  authoritative for chunking, and nothing on this page alters its critical path. Where this page
  and that plan disagree, that plan wins for chunking and r3 wins for design.
- **Not costed.** The cost ledger it asks for (§7.1) is the thing that would make it so.

### Gaps found while writing it, reported and not resolved

1. **No system-level cost term is on the record, and the chunking study's own is undefined
   (D16).** The record has used vectors stored implicitly (r3 §3.6). Per-query cost on the served
   path has instrumentation but no data.
2. **Production configuration is documented in three inconsistent places.** `Settings` defaults
   name `text-embedding-3-small` / 768-d; STATUS.md § *What works today* names BGE-base on CPU;
   production serves SFR 4096-d (RUN-PACKAGE §4). A reader of the code alone would mis-state S_ref.
3. **The largest cost lever (dimension × precision) has no quality measurement for this model
   anywhere**: no published quantization measurement exists for SFR-Embedding-Mistral or any
   Mistral-7B-derived 4096-d embedding (quantization-research § *The gap nobody has filled*,
   a 17-source survey of 2026-08-22), and nothing was measured inside the project.
4. **The knowledge graph cannot be studied in its current state**: no triples, no store, no
   extraction throughput; its ingest cost is the one that could exceed embedding (§4.2 rank 4,
   inferred).
5. **The 2026-09-21 external review is not in the repository**; this plan's framing and
   chunking-study-plan.md §1 both rest on it (S10).
