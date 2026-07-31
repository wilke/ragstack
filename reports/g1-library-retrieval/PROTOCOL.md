# G1 — Retrieval parameter defaults for small, user-owned libraries

**An experiment protocol.** Version 1.0 (pre-registration draft). Repository:
`/home/wilke/Development/ragstack`. Gate: `docs/libraries-spec.md` §-1 G1 (issue #200).

**Status of this document.** This is a *pre-registration*. The hypotheses, the parameter
grid, the primary metric, the query splits, the minimum effect size, and the decision
rules below are fixed **before** any measurement. Every run records a SHA-256 of this
file (`protocol_version`) in its manifest. Deviations must be recorded as dated
amendments at the end of this document, not by editing the body.

**Deliverable.** A normative `LibraryRetrievalDefaults` block for
`docs/libraries-spec.md`, with evidence. §11 gives its exact shape, including the shape
it takes if the answer is "no change".

---

## 1. Background and motivation

### 1.1 What ships versus what was measured

RAGStack's shipping retrieval defaults live in `python/ragstack/config.py:304-325`:

| Setting | Default | Line |
|---|---|---|
| `top_k` | `5` | `config.py:305` |
| `retrieval_candidate_multiplier` | `2` | `config.py:309` |
| `rrf_k` | `60` | `config.py:310` |
| `rerank_enabled` | `False` | `config.py:323` |
| `rerank_candidates` | `50` | `config.py:324` |

The composed depth arithmetic is:

- `python/ragstack/api/routers/query.py:199-204` — `depth = max(top_k, rerank_candidates)`
  when reranking, else `depth = top_k`.
- `python/ragstack/retrieval/retriever.py:53` — `depth = top_k * self.candidate_multiplier`,
  and **both legs are asked for the same depth** (`retriever.py:60`, `:65`).

So with the shipping defaults and rerank off, each leg is asked for **10** candidates.
With rerank on it is `max(5, 50) * 2 = 100`. Turning the reranker on therefore changes
first-stage breadth by 10× as a side effect — a confound that must be pinned in any
experiment (§6.2).

Every retrieval-quality measurement in the repository used a *different* configuration
from the one that ships. The inventory:

| Run | File | Corpus | Queries / ground truth | Retrieval settings | Significance |
|---|---|---|---|---|---|
| R1 | `python/scripts/eval/chunking_compare_report.md` | 300 ASM articles, 7k–33k chunks | 300 known-item title→own-doc proxy | `top_k=10`, rerank-pool 50, rerank **on**, `rrf_k=60`, mult 2 | none |
| R2 | `python/scripts/eval/chunking_compare_full_report.md` | 3,817 ASM articles, 90k–415k chunks | 1,000 title-proxy | same | none |
| R3 | `reports/chunking-comparison-overview.md` §6.1 | 1,500 ASM docs, 30k–150k chunks | 1,000 title-proxy | same | none (declared underpowered) |
| R4 | `python/scripts/eval/chunking_compare_7way_report.md` | 300 ASM docs, 5.6k–28k chunks | 300 title-proxy | `top_k=10`, rerank-pool 50, rerank on | paired bootstrap + Holm–Wilcoxon |
| R5 | `python/scripts/eval/scifact_chunk_eval_report.md` | SciFact 5,183 abstracts, 5.8k–19k chunks | **300 claims, real graded qrels** | retrieve=rerank=100, rerank on | paired bootstrap + Holm–Wilcoxon |
| R5b | `reports/semantic-chunking-experiments.md` §Exp 7 | same SciFact | same 300 claims | retrieve **300** / rerank 100 | same |

**No experiment in this repository has ever varied `rrf_k`, `retrieval_candidate_multiplier`,
`top_k`, or `rerank_enabled`.** Verified: all three harnesses construct
`HybridRetriever(vstore, tindex, embedder)` with no `rrf_scorer` and no
`candidate_multiplier` (`chunking_compare_7way.py:765`, `scifact_chunk_eval.py:313`,
`chunking_compare.py:608`), so `rrf_k=60` and `multiplier=2` are inherited defaults
(`scorers.py:23`, `retriever.py:25`). No harness has ever used
`HybridRetriever.retrieve(mode="vector")` or `mode="bm25"` despite the parameter existing
(`retriever.py:46`). `ROADMAP.md:80` states the gap outright: the ablation harness (#122)
is "the missing ability to isolate embedding/BM25/RRF-k/rewriting/graph/answer-quality;
today only chunking is isolated."

### 1.2 The one accidental data point, and why it motivates the whole experiment

R5 and R5b score the *same* corpus, *same* queries, *same* qrels, *same* embedding model,
*same* reference chunker. They differ in one thing: R5 ran before commit `84223aa`
("decouple retrieval breadth from rerank pool") with a single pool of 100; R5b ran after,
with `--retrieve-pool 300 --rerank-pool 100`.

**`fixed_tok512` nDCG@10: 0.698 (R5) → 0.744 (R5b). Δ ≈ +0.046.**

That uncontrolled retrieval-breadth change moved the primary metric by roughly **twice**
the largest chunking effect ever observed in this repository (`fixed_tok256`, ΔnDCG@10
= +0.023 [+0.006, +0.040], Holm p = 0.077, not distinguishable). Seven chunkers were
statistically indistinguishable; a single unmeasured pool-depth knob was not. This is the
strongest available prior that the G1 axes matter, and it is also a demonstration of how
easy it is to move these numbers without noticing.

### 1.3 Why library scale might differ — and the arithmetic the spec gets wrong

`docs/libraries-spec.md:9-17` motivates G1 with "a ~200-doc library is ~4k chunks where
BM25 may return 3–4 hits against dense's 20." Two corrections, both measurable and both
material to the design:

**(a) The chunk count is understated.** The library index `ragstack_lib_v1` is specified
to share `ragstack_sfr_tok512`'s build spec (`libraries-spec.md:213`). Measured
chunks-per-document for `fixed_tok512` on real ASM articles is **36.2**
(`reports/chunking-comparison-overview.md` §6.1, 1,500 docs → 54,270 chunks) and **33.8**
on the 300-doc subset (`chunking_compare_7way_report.md:96`). So:

- 200 documents ≈ **6,800–7,200 chunks**, not ~4,000.
- 1,000 documents ≈ **34,000–36,000 chunks**.

`libraries-spec.md:213` separately asserts "a 1000-PDF library is ~72,000 chunks", which
corresponds to 72 chunks/doc — the measured figure for `fixed_tok256`, not `tok512`. The
spec is internally inconsistent by a factor of two, and the segment-boundary argument at
`:213` (≈730 vectors/segment vs a ≈625 handoff threshold) rests on the larger number.
**Both figures must be replaced with a measured value from this experiment**; §11 makes
that part of the deliverable.

**(b) The interesting scale variable is chunk count, not document count.** Both retrieval
legs index *chunks*. BM25 document-frequency statistics, HNSW graph connectivity, and
Qdrant's segment-level full-scan handoff are all functions of the number of indexed
chunks. This has a direct and inconvenient consequence for corpus design:

> The full SciFact corpus at `fixed_tok512` is **6,108 chunks**
> (`scifact_chunk_eval_report.md`, chunk-structure table) — already almost exactly the
> chunk count of a **180-document real library**. Subsampling SciFact to 200 *documents*
> would produce ~236 chunks, which is not a library; it is a toy.

Corpus construction must therefore go the *other* way: hold SciFact whole and grow the
index upward. §4 does exactly this.

**(c) The "BM25 returns 3–4 hits" premise is probably false as stated, and the risk is on
the other leg.** `ElasticsearchTextIndex.search` (`stores/elasticsearch.py:143-168`)
issues `size=top_k` against a single `match` on `content`
(`elasticsearch.py:82`) — an exact, non-approximate search that returns
`min(size, |matching docs|)`. A BM25 leg asked for 10 will return 10 unless fewer than 10
chunks in the whole index contain *any* query term, which is implausible for a
multi-term biomedical query over 7,000 chunks. Conversely `QdrantVectorStore.search`
(`stores/qdrant.py:243-249`) passes `limit=top_k` with **no `search_params`** — no
`hnsw_ef` override, no `exact`, no oversampling — so the *dense* leg is the one that can
silently under-return under a selective filter on approximate HNSW. That is the same
failure mode as sibling gate G2 (`python/scripts/bench_filter_truncation.py`). §5.4 turns
this into a falsifiable prediction rather than an assumption.

### 1.4 Why RRF might behave differently at shallow depth

`RRFScorer.fuse` (`scoring/scorers.py:40`) computes
`score += 1.0 / (k + rank + 1)` with 0-based `rank` and `k=60`. At the shipping depth of
10 candidates per leg, every rank term lies in `[1/61, 1/70]` — a spread of 13%. The
constant `k=60` was chosen (Cormack et al., 2009) for TREC runs of depth 1,000, where it
suppresses the long tail. At depth 10 it flattens the ranking almost completely: the
fusion is close to a pure *vote count* (how many legs found the chunk) with rank order as
a tiebreaker. Whether that is good or bad at library scale is exactly the open question;
it is a prediction with a sign (§3, H4), not a foregone conclusion.

Two further structural notes for the analysis:

- `fuse` returns the **full union untruncated** (`scorers.py:46`); truncation happens at
  `retriever.py:74`. There is no padding or re-fetch when one leg is short — a short leg
  simply contributes fewer entries, and the result degrades toward single-leg behaviour
  with **no signal at any API layer**.
- `fuse` relabels every chunk `retrieval_method="hybrid"` (`scorers.py:43`), **erasing
  leg provenance**. Per-leg attribution therefore requires new instrumentation (§10, gap 2).

---

## 2. Scope

**In scope.** First-stage retrieval and fusion for a *single* library: `rrf_k`,
per-leg candidate depth (and the `top_k` × `candidate_multiplier` pairs that realize it),
`top_k`, `rerank_enabled`, `rerank_candidates`, and retrieval mode
(`hybrid` | `vector` | `bm25`), as a function of index size.

**Out of scope (fixed at their defaults, recorded in every manifest).** Query rewriting
(`rewrite_strategies="passthrough"`), the graph leg (`use_graph=False`), multi-library RRF
fan-out (`LIBRARY_FUSION_MAX_LEGS`, `libraries-spec.md:215-224`), the embedding model, the
chunker, and answer generation except as the tertiary track in §5.3.

**Relationship to G2.** G2 (`libraries-spec.md:19`) asks whether a *filtered* Qdrant search
returns `min(k, |match set|)`. G1 asks what parameters to use given that it does. They
interact: if the dense leg under-returns at depth D, every G1 conclusion at that depth is
about a degraded pipeline. §5.4 therefore embeds a G2-style returned-hit-count assertion
into every G1 cell, and §6.5 specifies one Track-A arm run through the real filtered path.
**G1 results measured on cells that fail the §5.4 sanity assertion are void, not
interesting.**

---

## 3. Research questions and hypotheses

All hypotheses are stated with a falsification condition. δ is the minimum shippable
effect, fixed at **δ = 0.02 nDCG@10** and justified in §7.4. "Confirm split" means the
held-out query split defined in §6.4. Unless stated otherwise, a directional claim
requires both `|Δ| ≥ δ` and Holm-adjusted `p < 0.05` on the pooled confirm split, with
consistent sign in each dataset separately.

### RQ1 — Does hybrid RRF still beat dense-only at library scale?

**H1.** At library scale (L0, ≈7k chunks) and shipping depth, hybrid retrieval has
nDCG@10 ≥ dense-only.
*Falsified if* dense-only exceeds hybrid by ≥ δ with Holm p < 0.05 on the confirm split.
*Concluded equivalent if* the 90% CI of (hybrid − dense) lies entirely within ±δ (TOST,
§7.5) — in which case hybrid is retained anyway, on the grounds that it costs one extra
exact ES query and provides lexical-match coverage that nDCG under-weights.

**H1b (mechanism, no ground truth needed).** The premise in `libraries-spec.md:11` —
that BM25 returns 3–4 hits where dense returns 20 — is false at chunk level at L0. Pre-
registered prediction: at per-leg depth D = 10 over ≈7k chunks, the BM25 leg returns
exactly D on **≥ 95%** of queries, while the dense leg returns exactly D on ≥ 99%
(unfiltered) — i.e. **neither** leg is depth-starved, and the observed hybrid/dense
difference is a *ranking* effect, not a *depth* effect.
*Falsified if* either leg returns < D on > 5% of queries. A falsification here is the more
interesting outcome and reframes RQ1 as a depth problem; it must be reported as such
rather than absorbed.

**H1c.** BM25-only is materially worse than both at library scale.
*Falsified if* BM25-only is within δ of hybrid on the confirm split.

### RQ2 — Does reranking help or hurt at low candidate depth?

**H2.** With the reranker enabled and a candidate pool `C ≥ 25`, nDCG@5 improves by ≥ δ
over `rerank_enabled=False` at matched first-stage depth.
*Falsified if* the effect is negative, or within ±δ by TOST.

**H2b.** The rerank effect is monotone non-decreasing in `C` over
`C ∈ {10, 25, 50, 100, 200}` at fixed first-stage depth: a cross-encoder cannot recover
recall the first stage never produced, so a shallow pool can only reorder what is already
there.
*Falsified if* the point estimate at some `C_i < C_j` exceeds that at `C_j` by ≥ δ with a
diff CI excluding 0 — i.e. reranking a *deeper* pool is actively worse, which would
indicate the cross-encoder is promoting distractors.

**H2c (the confound).** At *unmatched* depth — the shipping behaviour, where enabling
rerank multiplies first-stage breadth by 10× (`query.py:199-204`) — any measured "rerank
gain" is attributable to breadth, not to the cross-encoder. Pre-registered decomposition:
report `Δ(rerank on, D=100) − Δ(rerank off, D=100)` as the *reranker* effect and
`Δ(rerank off, D=100) − Δ(rerank off, D=10)` as the *breadth* effect.
*Falsified if* the reranker effect at matched depth is ≥ δ, i.e. the cross-encoder
contributes independently of breadth.

### RQ3 — Do the tuned constants need a size-dependent branch, and where is the crossover?

**H3 (the actual gate question).** The parameter configuration that maximizes nDCG@10 at
library scale (L0/L1) differs from the one that maximizes it at production scale (L2/L3).
Formally, for at least one candidate configuration `c`, the difference-in-differences

  θ(c) = [M(c, L0) − M(default, L0)] − [M(c, L2) − M(default, L2)]

satisfies |θ(c)| ≥ δ with a 95% paired-bootstrap CI excluding 0.
*Falsified if* every candidate's θ CI lies within ±δ → **no size branch; one set of
defaults for all index sizes.** This is a positive, publishable result and it simplifies
the config surface; §11.3 gives the deliverable in that case.

Note carefully: H3 is an **interaction**, not a main effect. Small corpora are easier —
that is guaranteed and uninteresting (§4.3). A size-dependent parameter branch is
justified only if the *ranking* of configurations changes with size, which a uniform
easiness shift cannot produce.

**H3b (crossover location).** If H3 holds, the crossover chunk count `N*` at which the
argmax changes is estimable to within a factor of 3 from the four-rung ladder.
*Falsified if* the argmax changes non-monotonically across rungs, in which case report
"no stable crossover" and branch on the coarser of the two adjacent rungs.

### RQ4 — Is `rrf_k = 60` right at shallow depth?

**H4.** The nDCG-optimal `rrf_k` increases with per-leg depth D; at D = 10 the optimum is
below 60.
*Falsified if* nDCG@10 is flat in `rrf_k` (all pairwise diff CIs across
`rrf_k ∈ {1, 10, 20, 60, 120, 240}` within ±δ at every D), or if the optimum does not
move with D.

### RQ5 — What is `top_k` for a chatbot?

**H5.** Because these chunks are packed into an LLM prompt (`llm_max_context_chars = 8000`,
`config.py:315`; `RagGenerator._format_context`, `ragstack/llm.py:107`), increasing `top_k`
beyond the point where *context precision*@k falls below 0.5 does not improve answer
quality and increases distraction.
*Tested only as a tertiary, non-inferiority check* (§5.3); `top_k` is otherwise chosen
from the retrieval metrics and the latency budget.

---

## 4. Ground truth and corpus construction

This is the crux, and it is where the protocol spends its rigour.

### 4.1 What is actually available in this repository

| Source | Ground truth | Size | Status |
|---|---|---|---|
| SciFact (BEIR) | **Real graded document-level qrels**, 300 test claim queries, 339 judgments | 5,183 abstracts → 6,108 chunks at `tok512` | **Implemented**: `python/scripts/eval/scifact_chunk_eval.py`, three loader fallbacks (HF `datasets` → `beir` → `ir_datasets`), cached under `HF_HOME` |
| ASM/BV-BRC scientific corpus | **None** (unlabelled) | 448k docs; `ragstack_sfr_tok256` 24.8M chunks, `tok512` 12.6M, `semantic` 3.0M (`libraries-spec.md:522`) | Live in prod Qdrant/ES |
| Known-item title→doc proxy | Weak pseudo-qrels; chunking-*insensitive* by construction (the title appears verbatim in the lead chunk) | 300–1,000 queries over ASM | Implemented: `chunking_compare_7way.py` |
| NFCorpus, BioASQ | Real qrels | NFCorpus 3.6k docs / 323 test queries / graded 0–2; BioASQ 14.9M docs | **Not implemented.** On the roadmap: `ROADMAP.md:81` ("add BioASQ (#56) + NFCorpus"), `scratchpad.md:281` |
| LLM-judged relevance | None | — | **Not implemented.** A hot-swappable LLM registry exists (`python/ragstack/api/model_registry.py`, `docs/model-registry.md`) |

The tension is real and cannot be dissolved: SciFact has genuine qrels but is 1.18
chunks/doc of abstract text; the ASM corpus is the actual target distribution but has no
labels. The resolution below is to use *both*, with explicitly asymmetric authority.

### 4.2 Decision: a three-track design

**Track A — decision-grade, selects the defaults.** Two BEIR datasets with real qrels,
each embedded in a *distractor ladder* that varies index chunk count while holding queries,
qrels, and the relevant document set constant.

- **A1 — SciFact-in-a-haystack.** All 5,183 SciFact abstracts, chunked with the
  `ragstack_lib_v1` build spec (`fixed_tok512`, SFR-Embedding-Mistral, 4096-d), giving the
  6,108-chunk judged core. 300 test claim queries, graded qrels, unchanged at every rung.
- **A2 — NFCorpus-in-a-haystack.** Held out for generalization: a configuration must win
  (or be equivalent) on both, or it does not ship. NFCorpus is biomedical, its queries are
  *real user queries* (not synthetic claims), its qrels are graded 0–2, and its documents
  are longer than SciFact abstracts. `_stats.ndcg_at_k` already handles graded gains
  (`_stats.py`, `2**grade - 1`). Loader is a gap (§10, gap 4).

**Track B — external validity, can veto but never select.** A real ASM PDF library at 200
and 1,000 documents, with pooled LLM-judged graded relevance plus a human-adjudicated
subsample. Its role is to catch a Track-A conclusion that inverts on real full-text PDFs.
Justification for treating an LLM judge as secondary-only is in §4.6.

**Track C — mechanism and sanity, no ground truth required.** Per-leg returned-hit counts,
leg overlap, doc-collapse ratios, latency. Runs on every cell of Tracks A and B. Track C
is the cheapest and is what falsifies or confirms H1b directly.

### 4.3 The distractor ladder — how to avoid measuring "small corpora are easy"

The trap: subsample a labelled corpus, observe that everything gets better, and conclude
something about parameters. The fix has three parts.

**(i) Hold the relevant set constant; vary only the distractors.** At every rung the index
contains *all* judged documents and *all* qrels. Rungs differ only in how many
non-judged distractor chunks are present. Queries are identical, so every metric is
**fully paired across rungs** and a difference-in-differences estimator is available.

**(ii) Distractors are real, in-domain, and pre-embedded.** They are drawn by a read-only
scroll from the production `ragstack_sfr_tok512` collection — real 4096-d SFR vectors with
their `content` payload (`stores/qdrant.py:196-204` writes `chunk_id`, `doc_id`, `content`,
`start_char`, `end_char`; `_chunk_from_payload` at `:411-422` reconstructs them). The
same technique the G2 harness already uses (`bench_filter_truncation.py:250-293`,
`scroll_vectors`). Consequences:

- **No re-embedding.** The ladder to 1M chunks costs zero GPU time for distractors.
- **No manifold mismatch** — provided the distractor collection's build spec matches the
  judged core's. This is a *hard precondition*, asserted in the manifest:
  `provenance.spec_hash(model, dim, chunk_descriptor(...))` (`ragstack/provenance.py:50`,
  `:28`) must be **byte-identical** between the judged-core index and the source
  collection. If it is not, the ladder is invalid and must be rebuilt by embedding the
  distractors directly.
- **Distractors must be written to ES as well as Qdrant.** BM25 corpus statistics
  (document frequency, `avgdl`) are the entire reason the ladder exists for the sparse
  leg. A Qdrant-only ladder would silently hold the BM25 index at L0 while the dense index
  grows, producing a spurious hybrid-vs-dense interaction. `bench_filter_truncation.py` is
  Qdrant-only; this is part of gap 3 in §10.

**(iii) The size effect is a measured main effect; the decision rests on the interaction.**
Report `M(default, L_i)` for every rung as the easiness curve. It will decrease with size.
That is expected and is *not* evidence for anything. The shipping decision uses only
θ(c) from H3, which differences the easiness curve out.

**Rungs.**

| Rung | Total chunks | Distractors added | Real-world analogue |
|---|---|---|---|
| L0 | ≈ 6.1k (A1) / ≈ 4k (A2) | 0 | 170–200-document personal library |
| L1 | ≈ 36k | +30k | 1,000-document personal library |
| L2 | ≈ 200k | +194k | large shared/departmental index |
| L3 | ≈ 1.0M | +994k | production-shaped (prod is 3M–25M; L3 is the affordable upper anchor) |

L3 is optional if the schedule slips; L0/L1/L2 are mandatory, since L0↔L2 is the
difference-in-differences pair for H3. Distractor sampling is by seeded reservoir sample
over a scroll of the source collection, with the seed and a digest of the sampled point-id
sequence recorded in the manifest so the exact distractor set is reproducible.

**Cost.** Judged-core embedding is small: SciFact `fixed_tok512` ingested 6,108 chunks in
24.1 s across 16 SFR endpoints (`scifact_chunk_eval_report.md`), i.e. ≈ 253 chunks/s.
NFCorpus is comparable. Distractors cost zero GPU. Total judged-core embed time across
both datasets and all rungs: **< 2 minutes of fleet time**.

### 4.4 Why not the alternatives

- **Subsample SciFact to 200 documents.** Produces ~236 chunks. Not a library; also
  destroys most qrels coverage. Rejected (§1.3b).
- **BioASQ as the ladder corpus.** Attractive — real qrels at 14.9M documents would span
  library→production natively. Rejected for v1 on cost: it requires a streaming loader for
  a 14.9M-document HF dataset and would need ≥ 1M SFR embeddings computed from scratch. It
  is the right *follow-up*; recorded as such in §10.
- **Known-item title→doc proxy on real ASM PDFs.** Free and already implemented, and it
  *is* real full-text PDF. Rejected as a primary because it is retrieval-easy by
  construction (the title is verbatim in the lead chunk, so BM25 has an unfair lexical
  advantage) — precisely the axis RQ1 measures. Using it would bias H1 toward hybrid.
  Retained as an optional zero-cost sanity arm only, never as evidence for or against H1.
- **Citation-based pseudo-qrels** (query = a citing sentence with the citation removed;
  relevant = the cited paper, when present in the library). Objective, free, and derivable
  from the existing enrichment output (`ingestion/enrich.py` extracts citations/DOIs).
  Rejected as primary because it measures *cited-work retrieval*, a different task from
  question-answering, with a systematically different query distribution. Listed as an
  optional Track-B supplement.

### 4.5 Track B — the real-PDF library

**Corpora.** Two libraries drawn from the production ASM corpus, each built as a
`fixed_tok512` index so chunks/doc ≈ 36:

- **B-clustered-200** — 200 documents forming a topical cluster (k-means over document
  centroid embeddings, or a single journal/subject facet). A personal library is topically
  concentrated; concentration raises inter-document similarity and makes discrimination
  *harder*, working against the "small is easy" effect. This is the realistic case.
- **B-random-200** — 200 documents sampled uniformly. The contrast isolates topical
  concentration as its own factor at zero extra labelling cost.
- **B-clustered-1000** — the 1,000-document rung, clustered.

**Queries.** 150 per library, generated by a local LLM from randomly sampled *chunks*
(not documents), then filtered: a query is discarded if it is answerable from its source
chunk's document title alone, if it names a document explicitly, or if two human reviewers
judge it not to be a plausible researcher question. Queries are generated **before** any
retrieval run and pinned as a fixture (§8).

**Judgments — TREC-style pooling.** For each query, form the pool as the **union of the
top-20 chunks from every Track-B grid cell**, mapped to documents. Judge each
(query, document) pair once, on a graded 0/1/2 scale, with the judgments reused across all
cells. Fixed pool depth of 20 for *every* cell is mandatory: if pool depth varied with the
cell's parameters, configurations contributing more to the pool would be systematically
advantaged (classic pooling bias), and varying `rerank_candidates` is exactly a
pool-depth manipulation.

**Metrics for Track B are pooling-robust.** Because Track B's qrels are pool-derived and
therefore incomplete, report **bpref** and judged-only condensed-list nDCG alongside plain
nDCG@10. Plain recall@k is *not* reported for Track B — it is unidentifiable with
incomplete judgments.

**Judge quality is measured, not assumed.** Two human annotators independently judge a
random 100-pair subsample; report Cohen's κ (human–human) and κ (judge–human). If
κ(judge–human) < 0.4, Track B is reported as descriptive only and loses even its veto.

### 4.6 Is an LLM-judged relevance track defensible as a secondary measure?

Yes, with the following stated position and known biases.

**Position.** Track B may **veto** (a Track-A winner that loses on Track B by ≥ 2δ
triggers human adjudication of that specific comparison and blocks the recommendation
pending resolution) but may **never select**. Rationale: a label set produced by a large
language model is not evidence *independent* of the systems being ranked, particularly
when the same model family supplies the answer generator; and LLM relevance labels are
known to compress score distributions, which biases toward "no difference" and would let a
weak configuration pass by failing to be distinguished.

**Known biases, with mitigations.**

| Bias | Effect here | Mitigation |
|---|---|---|
| Position / order bias | Judge favours earlier-presented candidates | Randomize presentation order per pair; judge one (query, doc) pair at a time, never a ranked list |
| Verbosity / length bias | Longer chunks judged more relevant | Report the length distribution of judged-relevant vs judged-irrelevant; include chunk length as a covariate in the sensitivity analysis |
| Self-preference | Judge favours output from its own family | Judge model must be a different family from the answer generator; both recorded in the manifest |
| Leniency / topical-drift | Topically-related but non-answering passages marked relevant → recall inflated, differences between configurations flattened | Rubric requires the passage to *answer* the query, with a negative exemplar; measure κ against humans |
| Score compression | Graded scale collapses to 2 values | Report the label histogram; if >90% of positives are one grade, treat as binary |
| Non-determinism | Same pair, different label | Temperature 0, fixed seed; duplicate 10% of pairs and report self-consistency |
| Pooling bias | Unjudged relevant documents counted as irrelevant | Fixed pool depth across cells; bpref and condensed-list metrics |
| Config leakage | Judge infers which system produced a candidate | Judge sees only (query, chunk text); no scores, no ranks, no configuration identifiers |

---

## 5. Metrics

Every metric is retained as a **per-query array**, not a mean. This is already the contract
of the existing stats layer (`python/scripts/eval/_stats.py` module docstring) and of
`chunk_one.py`'s `metrics.json` (`{config, source, n_queries, query_ids, means, per_query}`).

### 5.1 Primary

**nDCG@10, document-level, graded.** `_stats.ndcg_at_k` (gain `2**grade - 1`, discount
`log2(rank+1)`, IDCG over the best ordering). Chosen because: it is the BEIR standard for
SciFact and NFCorpus; it handles the graded multi-relevant qrels both datasets provide; it
is the primary metric of the only real-qrels measurement already in this repository (R5),
so the new results are directly comparable to a published baseline; and it is what
`_stats.build_stats_table` already tests.

**Co-primary: nDCG@5 at chunk level.** The deliverable includes a `top_k`, and the
shipping default is 5 (`config.py:305`). A metric evaluated only at k=10 cannot recommend
a k=5. Chunk-level (no doc collapse) because it is *chunks* that are packed into the LLM
prompt. Any configuration recommended must not be worse than the default on either
primary; a split decision between the two primaries is resolved in favour of nDCG@5 and
reported explicitly as a split.

### 5.2 Secondary

- **recall@{10, 20, 100}** (document level). recall@100 measures the ceiling available to
  the reranker; it is the diagnostic for H2b — a rerank gain is only possible where
  recall@C exceeds recall@k.
- **MRR@10** and **MAP** (`_stats.reciprocal_rank`, `_stats.average_precision`). MRR is the
  right summary for the chatbot's single-best-citation behaviour.
- **Context precision@k** — the fraction of the returned top-k chunks that are relevant,
  for k ∈ {1,3,5,10}. This is the quantity that governs LLM distraction and is the bridge
  between retrieval and answer quality. It is *not* a standard IR metric and is reported as
  a diagnostic, not a decision metric.
- **unique_docs@k** — the doc-collapse ratio. At 36 chunks/doc a depth-100 chunk pool may
  contain only ~20 distinct documents, structurally capping document-level recall. This is
  the single largest expected difference between BEIR (1.18 chunks/doc) and a real library
  (36) and must be reported for every cell.
- **k-curve.** All of the above at k ∈ {1, 3, 5, 10, 20}, read from a single stored
  ranking (§6.3). Reporting a curve rather than a point makes the `top_k` recommendation
  legible.

### 5.3 Tertiary — answer-level metrics

**Do answer-level metrics belong?** Partly. Arguments against making them primary: they
have substantially higher per-query variance and therefore much lower statistical power at
n=300; they conflate retrieval with generation, so a retrieval-parameter effect is
attenuated by whatever the generator does; and scoring them requires an LLM judge, whose
biases §4.6 has just enumerated. Arguments for including them at all: the deliverable feeds
a chatbot, and a retrieval configuration that maximizes nDCG while degrading answers is the
wrong answer.

**Resolution: a non-inferiority confirmation on the top two configurations only**, run
after Track A has selected, never as a selection metric and never swept.

- **Groundedness / faithfulness** — proportion of atomic claims in the generated answer
  entailed by the retrieved context. Claim-level, judge-scored.
- **Answer relevance** — does the answer address the query.
- **Citation correctness** — for each `[n]` marker, does source *n* support the adjacent
  claim. `RagGenerator` already produces cited answers (`ragstack/llm.py:95-121`).

**Decision rule:** the recommended configuration must be non-inferior to the current
default on groundedness with margin 0.05 (TOST on the paired proportion). It is not
required to be *better*.

### 5.4 Sanity — the returned-hit-count metric (G2 sibling)

Sibling gate G2 found Qdrant silently returning fewer hits than requested
(`libraries-spec.md:19`; harness `python/scripts/bench_filter_truncation.py`, pass
criterion `hits == min(k, |match set|)` at `:154`). The same class of failure would
invalidate any G1 cell. Every cell therefore records, per query:

| Counter | Definition |
|---|---|
| `dense_hits` | length of the list returned by `QdrantVectorStore.search` at requested depth D |
| `bm25_hits` | length returned by `ElasticsearchTextIndex.search` at depth D |
| `dense_deficit`, `bm25_deficit` | `D − hits`, per leg |
| `union_depth` | `|dense ∪ bm25|` — the actual input cardinality to RRF |
| `overlap` | `|dense ∩ bm25|` — how much of the fusion is agreement vs coverage |
| `fused_depth` | `len(RRFScorer.fuse(...))` before truncation |
| `unique_docs@k` | doc-collapse ratio, per k |
| `rerank_pool_occupancy` | `min(C, union_depth) / C` — how full the cross-encoder pool actually was |

**Pass assertion, evaluated per cell, mirroring G2:**
`dense_hits == min(D, N_chunks_matching_filter)` and
`bm25_hits == min(D, N_chunks_matching_filter)` for ≥ 99% of queries.
A cell failing this assertion is **void**: its quality metrics are reported as
`INVALID (hit deficit)` and excluded from all statistical tests. Because the unfiltered
Track-A rungs have `N_chunks >> D`, the expected value is simply `hits == D`, so any
deficit is an HNSW/segment artefact and is itself a finding to escalate to G2.

`rerank_pool_occupancy` deserves emphasis: it is the direct test of the spec's concern that
"the reranker's candidate pool assumes depth that will not exist." At the shipping default
`rerank_candidates=50` with rerank *off*, occupancy is undefined; with rerank on, effective
first-stage depth is 100/leg (§1.1) and occupancy should be ≈ 1.0. If occupancy is < 1.0 at
L0, the reranker is being fed a partly empty pool and H2 is being tested on a degraded
configuration.

### 5.5 Cost

p50 / p95 end-to-end query latency, per-leg latency, cross-encoder calls per query,
cross-encoder GPU seconds, index build time, index size on disk. A configuration that buys
+0.005 nDCG for 3× latency is not a shipping default. The cost budget is a *pre-registered
constraint*, not a tiebreaker: candidate configurations nominated in §7.2 must have p95
latency ≤ 2× the shipping default's p95 at the same rung.

---

## 6. Experimental design

### 6.1 Factors

| Factor | Levels | Notes |
|---|---|---|
| `N` — index chunk count | L0 ≈ 6k, L1 ≈ 36k, L2 ≈ 200k, L3 ≈ 1M | §4.3 |
| `dataset` | SciFact (A1), NFCorpus (A2) | A2 held out for confirmation |
| `mode` | `hybrid`, `vector` (dense-only), `bm25` | `retriever.py:46` |
| `rrf_k` | 1, 10, 20, 60, 120, 240 | hybrid only; 60 is the shipping default |
| `D` — per-leg depth | 10, 20, 50, 100, 200 | **absolute**, see §6.2 |
| `rerank_enabled` | False, True | |
| `C` — `rerank_candidates` | 10, 25, 50, 100, 200, with `C ≤ D` | 50 is the shipping default |
| `k` — report cutoff | 1, 3, 5, 10, 20 | derived, free (§6.3) |

### 6.2 Parameterize absolute depth, not the product

In production, per-leg depth is `max(top_k, rerank_candidates) * candidate_multiplier`
(`query.py:199-204` composed with `retriever.py:53`). `top_k` therefore is **not** a pure
truncation of a fixed ranking — changing it changes what was retrieved. Sweeping
`top_k` and `candidate_multiplier` independently would confound the report cutoff with the
retrieval breadth, and would make the rerank on/off comparison a 10× breadth comparison in
disguise (H2c).

The design therefore sweeps **absolute per-leg depth D directly**, and maps back to
shippable `(top_k, candidate_multiplier, rerank_candidates)` triples at reporting time.
Example realizations of D = 100: `(top_k=5, mult=2, rerank_candidates=50)` — the current
default with rerank on; `(top_k=5, mult=20, rerank off)`; `(top_k=10, mult=10, rerank off)`.
The deliverable in §11 states the triple, not D.

### 6.3 Runs actually executed, and what is derived

Two properties collapse an apparently 1,800-cell grid into something small.

**(a) The report cutoff k is free.** Store the top-200 ranked chunk ids per query per
first-stage cell; every k ∈ {1,…,20}, every doc-collapse variant, and every metric is
recomputed offline from that file with no store and no GPU.

**(b) `rerank_candidates` is nearly free.** A cross-encoder is a *pointwise* scorer:
`SidecarReranker.score` (`scoring/scorers.py:97-162`) produces a score per
(query, chunk) pair, independent of the pool. So: rerank the deepest pool once, cache
scores keyed by `(query_id, chunk_id, reranker_model, reranker_revision)`, and derive every
smaller `C` by truncating the first-stage list to C and re-sorting by the cached scores.
Zero additional sidecar calls.
*Caveat, and it must be honoured:* in production, `C` also raises first-stage depth
(`query.py:201`). The offline derivation is faithful only when D is held fixed and C ≤ D
— which the grid enforces. The production coupling is then re-imposed at reporting time
(§6.2).

**First-stage retrievals actually run**, per (dataset × rung):

- `bm25`: 5 (one per D) — `rrf_k` is a no-op
- `vector`: 5
- `hybrid`: 6 `rrf_k` × 5 D = 30

**= 40 first-stage cells.** Across 2 datasets × 4 rungs = **320 cells**, each over ~300
queries ≈ 96k query-executions. With query vectors cached (§6.6) each execution is one
Qdrant `query_points` plus one ES `search`.

**Cross-encoder work.** Per (dataset × rung), score the union over cells of each query's
top-200. Heavy overlap across cells makes the union far smaller than the worst case;
budget ~1,000 unique chunks/query → ~300k pairs per (dataset × rung), ~2.4M total. At
bge-reranker-v2-m3 throughput on the H200 fleet this is a few GPU-hours; it is the dominant
compute cost and is capped by the D ≤ 200 ceiling.

### 6.4 Query splits, replication, and overfitting control

**Split.** Each dataset's query set is split **40% tune / 60% confirm**, stratified by
per-query difficulty measured on the shipping-default configuration at L0 (quintiles of
nDCG@10), seed 0, split written to a pinned fixture *before* any sweep run. Stratification
prevents the two splits from differing in baseline difficulty, which would otherwise
inflate or deflate every stage-2 effect.

**Two-stage protocol.**
- *Stage 1 (exploratory)* runs the full grid on the **tune** split. It reports point
  estimates and CIs. **It makes no significance claims and no recommendation.** It
  nominates ≤ 5 candidate configurations by the pre-registered rule in §7.2.
- *Stage 2 (confirmatory)* runs only the nominated ≤ 5 configurations plus the shipping
  default on the **confirm** split, with Holm–Bonferroni over exactly those comparisons.

**Second corpus.** NFCorpus is the external check. A recommendation must hold on both
datasets: same sign, and pooled-confirm Δ ≥ δ. A configuration that wins on SciFact and
loses on NFCorpus is not recommended, regardless of pooled significance.

**Replication.** With a frozen index and cached query vectors, retrieval is deterministic
except for HNSW nondeterminism under concurrent optimizer activity — which
`libraries-spec.md:526-533` documents explicitly as a reason to cache query vectors and
raise count timeouts. Therefore: **3 replicates of one designated reference cell per rung**
(an A/A null), not 3 replicates of everything. Report across replicates: SD of nDCG@10,
and rank-biased overlap (RBO) of the top-20 lists. **δ must exceed 3× the A/A SD**; if it
does not, the experiment is under-resolved and the query set must be enlarged before any
claim is made.

### 6.5 One arm through the real filtered path

Track A's rungs are unfiltered scratch collections, which is the clean way to vary size.
But the shipping library path issues `library_id == L AND tenant_id ANY [...]`
(`libraries-spec.md:196`) against a *shared* index, and `_build_filter`
(`stores/qdrant.py:425-455`) with an unindexed `library_id` is the G2 failure surface. One
Track-A arm — the shipping default plus the top-2 nominated configurations, at L1 — is
therefore additionally run with the judged core loaded into a `ragstack_lib_v1`-shaped
collection alongside distractors under a *different* `library_id`, and queried through the
real filter. The §5.4 hit-count assertion is the pass criterion. If filtered and unfiltered
results diverge by ≥ δ, the G1 recommendation is conditional on G2 and must say so.

### 6.6 Determinism and caching

- **Query vectors are embedded once per dataset** and cached to disk
  (`cache/query_vectors/<dataset>.<spec_hash>.npy`). This removes the entire embedding
  fleet from the variance budget — different endpoints, different batch composition, and
  endpoint availability (`chunking_compare_7way.detect_live_endpoints`) can otherwise
  perturb results between cells.
- **Indexes are frozen** during measurement: no concurrent ingest; the Qdrant optimizer
  quiesced and `segments_count` / `indexed_vectors` / `points` recorded before and after
  each rung's sweep (the `hnsw_built` coverage banner in `bench_filter_truncation.py:925-949`
  is the precedent — an unqualified result from a collection whose HNSW never built is
  meaningless).
- **Seeds** for the distractor sample, the query split, and the bootstrap (`_stats.SEED = 0`,
  `BOOTSTRAP_ITERS = 10_000`) are fixed and recorded.
- **The sweep runs in-process against `HybridRetriever`, not over HTTP.** `rrf_k` is baked
  into a module-level singleton at import (`api/routers/query.py:36`) and the multiplier
  into the app-startup retriever (`api/deps.py:274`, `:994`); an HTTP sweep would require a
  process restart per cell. The in-process harness constructs
  `HybridRetriever(vstore, tindex, embedder, rrf_scorer=RRFScorer(k=...), candidate_multiplier=...)`
  directly — the same constructor `deps.py:267-277` uses, so no behaviour is forked.

---

## 7. Statistical treatment

### 7.1 Unit of analysis and existing machinery

The unit is the **query**. Per-query metric arrays are retained for every cell and are the
input to every test. The repository already has the needed primitives in
`python/scripts/eval/_stats.py`:

- `bootstrap_metric_ci` — paired bootstrap 95% CI per configuration; the *same* resampled
  query-index matrix is used for all configurations on each iteration, which is what makes
  the intervals comparable.
- `bootstrap_diff_ci` — paired bootstrap CI of `(config − reference)`.
- `wilcoxon_signed_rank` — two-sided, tie- and continuity-corrected normal approximation,
  dependency-free.
- `holm_bonferroni` — step-down FWER control.
- `build_stats_table` — assembles the table and an honest interpretation line, and already
  emits the "statistically indistinguishable" verdict when nothing survives.

These are reused unchanged. §7.5 and §7.6 add what is missing.

### 7.2 Multiple comparisons across the grid

Holm–Bonferroni over 320 cells would be so conservative that only a huge effect could
survive; running 320 uncorrected tests would guarantee false positives. The two-stage
split (§6.4) is the answer, with different error criteria at each stage:

- **Stage 1 — screen, tune split. Benjamini–Hochberg FDR at q = 0.10.** FDR is the right
  criterion for a screen: we are willing to carry a false lead into stage 2, because
  stage 2 will kill it. Stage 1 output is a ranked shortlist with CIs, explicitly labelled
  *not a result*.
- **Stage 2 — confirm, held-out split. Holm–Bonferroni at α = 0.05** over exactly the
  pre-registered comparisons (≤ 5 candidates + default = ≤ 5 tests per metric per dataset).
  FWER is the right criterion for a ship/no-ship claim.

**Nomination rule (pre-registered, applied mechanically to stage-1 output):** the
shortlist is (1) the shipping default, (2) the highest-mean-nDCG@10 cell at L0 satisfying
the §5.5 latency constraint, (3) the highest-mean cell at L2 satisfying the same, (4) the
best dense-only cell, (5) the best rerank-on cell at matched depth. Ties broken by lower
p95 latency, then by lower D. If (2) and (3) coincide, the shortlist is shorter — which is
itself weak evidence against H3.

**Metric multiplicity.** Holm is applied within each primary metric family separately, and
a recommendation requires the *same* configuration to clear the bar on nDCG@10 and to be
non-inferior on nDCG@5. Secondary metrics are reported with CIs and are never used to
declare significance.

### 7.3 Confidence intervals

Every reported number is `point [lo, hi]` from the paired bootstrap, 10,000 iterations,
seed 0 — the existing `CI.fmt()` convention. Difference CIs against the reference
configuration are reported for the primary metrics. Bare means without an interval are not
acceptable output from this protocol.

### 7.4 Minimum effect size worth acting on

**δ = 0.02 absolute nDCG@10.** Justification, from measurements already in this repository:

1. *Resolvable.* The R5 paired-bootstrap difference CIs at n = 300 had half-widths of
   0.005–0.023 (e.g. `fixed_tok256`: ΔnDCG@10 +0.023 [+0.006, +0.040], half-width 0.017).
   A 95% half-width of 0.017 corresponds to an 80%-power MDE of
   `0.017 × (1 + z_β/z_{α/2}) = 0.017 × 1.43 ≈ 0.024`. So n = 300 alone resolves ≈ 0.025;
   **pooling SciFact (300) and NFCorpus (323) to n = 623 gives MDE ≈ 0.017**, which is
   below δ. This is the concrete reason the second dataset is mandatory rather than nice
   to have.
2. *Below the effect the experiment exists to find.* The uncontrolled retrieval-breadth
   change in §1.2 moved nDCG@10 by ≈ 0.046 — more than 2δ. If the G1 axes matter at all,
   they matter at a scale this experiment can see.
3. *Above the noise that has previously tempted a conclusion.* The largest chunker effect
   ever observed here was +0.023 and did not survive Holm. Setting δ = 0.02 puts the
   threshold right at that boundary, which is why the decision rule requires **both**
   `|Δ| ≥ δ` **and** Holm-adjusted `p < 0.05` on the confirm split — magnitude alone is not
   enough, and neither is significance alone.
4. *Bounded below by measured noise.* δ must exceed 3× the A/A replicate SD (§6.4). If the
   A/A SD comes in above 0.0067, δ is raised accordingly and the change is recorded as an
   amendment.

For nDCG@5 the same δ = 0.02 applies. For the answer-level non-inferiority check the margin
is 0.05 on a proportion.

### 7.5 What counts as "no difference"

Failing to reject a null is not a finding. This protocol can conclude equivalence, using
**TOST (two one-sided tests)** with equivalence margin δ: two configurations are declared
*practically equivalent* on a metric when the **90% paired-bootstrap CI of their difference
lies entirely within (−δ, +δ)**. (A 90% CI is the correct interval for a 5% one-sided TOST
pair.)

Three explicit outcomes are therefore possible for every comparison, and every comparison
in the report is labelled with one:

| Label | Condition | Meaning |
|---|---|---|
| **DIFFERENT** | `|Δ| ≥ δ` and Holm `p < 0.05` | Act on it |
| **EQUIVALENT** | 90% CI ⊂ (−δ, +δ) | Genuinely no practical difference — prefer the simpler/cheaper configuration |
| **INCONCLUSIVE** | neither | Underpowered; report as such, do not ship on it |

The "everything is EQUIVALENT" outcome is a legitimate and useful result: it means the
shipping defaults survive contact with library scale, no size branch is needed, and the
config surface stays flat. §11.3 gives that deliverable. `_stats.build_stats_table`
currently emits only DIFFERENT/not-DIFFERENT; adding TOST is gap 5 in §10.

### 7.6 The size-branch test is a difference-in-differences

H3 is an interaction and must be tested as one. Because the query set is identical at every
rung, per-query scores are paired *across rungs* as well as across configurations, so:

  θ(c) = [M(c, L0) − M(default, L0)] − [M(c, L2) − M(default, L2)]

is computed per query and bootstrapped with the same resampled index matrix used everywhere
else. A size-dependent branch is recommended only when `|θ(c)| ≥ δ` **and** the 95% CI of
θ(c) excludes 0, for a configuration that has already cleared stage 2 at one of the two
rungs. Reporting θ alongside the raw per-rung numbers is what keeps the "small corpora are
easy" main effect from being mistaken for a parameter effect.

The easiness main effect itself is reported, once, as a curve: `M(default, L_i)` versus
`log10(N_chunks)`, with CIs. It is context, not evidence.

### 7.7 Pre-specified analyses and the honest-reporting rule

The following are fixed before any run: the grid (§6.1), the primary and co-primary metrics
(§5.1), the splits (§6.4), the nomination rule (§7.2), δ (§7.4), the equivalence procedure
(§7.5), and the DiD estimator (§7.6). Any analysis not listed here is **exploratory** and
must be labelled as such in the report; exploratory findings may motivate a follow-up
experiment but may not enter the `LibraryRetrievalDefaults` block.

---

## 8. Provenance and reproducibility

Treated as a deliverable, not as documentation. The standard is: **a third party with
access to the datasets and the hardware can reproduce every number from the artefacts
alone.**

### 8.1 What must be captured

| Category | Fields |
|---|---|
| Protocol | SHA-256 of this file; amendment list |
| Code | git commit, branch, dirty flag, `provenance.ragstack_version()` (`provenance.py:38`) |
| Dataset | name, source string (`"hf:BeIR/scifact"` — the harness already reports which of its three loaders won), HF revision, SHA-256 of corpus / queries / qrels, counts (`n_docs`, `n_queries`, `n_judgments`) |
| Query set | fixture path under `contracts/fixtures/queries/`, SHA-256, split label, split seed. Precedent exists: `contracts/fixtures/queries/test_queries.json`, and `libraries-spec.md:528` already requires a pinned query fixture for the §16 Tier-0 gate |
| Embedding | model, dim, revision/digest, `embedding_api`, `embedding_endpoints` (the live set, not the candidate set), batch size, token counter model, hard cap |
| Chunker | `chunk_method`, `chunk_size`, `chunk_overlap`, `chunk_params`, and `chunk_descriptor(...)` (`provenance.py:28`) |
| Build identity | `spec_hash(model, dim, chunk)` (`provenance.py:50`) for the judged core **and** for the distractor source collection, with an equality assertion |
| Index config | Qdrant: `m`, `ef_construct`, server-side search `ef`, `full_scan_threshold`, `max_segment_size`, `indexing_threshold`, `on_disk_payload`, `segments_count`, `points`, `indexed_vectors`, `hnsw_coverage`. ES: version, index name, similarity, analyzer, `n_docs`, `avgdl` |
| Distractors | source collection, sample seed, count, digest of the sampled point-id sequence |
| Parameters | `mode`, `rrf_k`, `D`, `top_k`, `candidate_multiplier`, `rerank_enabled`, `rerank_candidates`, reranker model + revision, `use_graph=false`, `rewrite="passthrough"` |
| Software | Python, `qdrant-client`, `elasticsearch`, `httpx`, `numpy`, `transformers`/`tokenizers`, Qdrant server, ES server, sidecar image digests |
| Hardware | host, GPU model and count, which endpoints served the run, concurrency settings (`EMBED_CONCURRENCY`, `EVAL_CONCURRENCY`) |
| Seeds | distractor sample, query split, bootstrap (`_stats.SEED`), judge seed |
| Invocation | the exact `argv`, verbatim |
| Results | `query_ids`, per-query arrays for every metric, means, Track-C counters, cost |

Two of these are currently unobtainable from a running server and are worth fixing:
`GET /v1/admin/config` (`api/routers/admin.py:47-91`) exposes `top_k`, `rerank_enabled`,
`rerank_candidates`, `reranker_model` but **not** `rrf_k` or
`retrieval_candidate_multiplier` — a deployed instance cannot self-report the two constants
this experiment is about. And `settings.top_k` (`config.py:305`) is a **phantom knob**: no
reader exists in the retrieval path; the API default is the literal `5` at
`query.py:72`/`:107`. Both are noted in §10 and §11.4 because they affect whether the
deliverable is even expressible as configuration.

### 8.2 Manifest schema

Reuse the `provenance.py` vocabulary rather than inventing a parallel one:
`CollectionManifest` already carries `collection`, `model`, `dim`, `embedding_api`,
`embedding_endpoints`, `chunk_method`, `chunk_size`, `chunk_overlap`, `chunk_params`,
`spec_hash`, `corpus`, `chunk_count`, `ingested_at`, `ragstack_version`, `source`. An
`EvalRunManifest` **embeds** a `CollectionManifest` verbatim and adds the eval-specific
sections.

```jsonc
{
  "schema_version": "ragstack.eval_run/v1",
  "run_id": "g1-a1-L0-hybrid-rrf60-d100-rr0-0007",
  "protocol_version": "sha256:…",          // SHA-256 of g1-protocol.md
  "started_at": "…", "finished_at": "…",
  "git": {"commit": "…", "branch": "…", "dirty": false},
  "ragstack_version": "0.15.0",

  "dataset": {"name": "scifact", "source": "hf:BeIR/scifact", "revision": "…",
              "corpus_sha256": "…", "queries_sha256": "…", "qrels_sha256": "…",
              "n_docs": 5183, "n_queries": 300, "n_judgments": 339},

  "query_fixture": {"path": "contracts/fixtures/queries/g1_scifact_confirm.json",
                    "sha256": "…", "split": "confirm", "split_seed": 0, "n": 180},

  "index": {
    "manifest": { /* CollectionManifest, verbatim */ },
    "hnsw": {"m": 16, "ef_construct": 100, "search_ef": null,
             "full_scan_threshold": 10000, "max_segment_size": null,
             "indexing_threshold": 20000, "on_disk_payload": true,
             "points": 6108, "indexed_vectors": 6108, "segments_count": 2,
             "hnsw_coverage": 1.0},
    "es": {"version": "8.x", "index": "g1_a1_l0", "similarity": "BM25",
           "analyzer": "standard", "n_docs": 6108, "avgdl": 0.0}
  },

  "distractors": {"source_collection": "ragstack_sfr_tok512",
                  "source_spec_hash": "…", "spec_hash_match": true,
                  "n_chunks": 0, "sample_seed": 0, "point_id_digest": "…"},

  "params": {"mode": "hybrid", "rrf_k": 60, "leg_depth": 100,
             "top_k": 5, "candidate_multiplier": 20,
             "rerank_enabled": false, "rerank_candidates": null,
             "reranker_model": "BAAI/bge-reranker-v2-m3", "reranker_revision": "…",
             "use_graph": false, "rewrite_strategies": ["passthrough"]},

  "runtime": {"host": "coconut", "gpu": "H200 NVL", "gpu_count": 8,
              "embedding_endpoints_live": ["…"], "crossencoder_url": "…",
              "embed_concurrency": 16, "eval_concurrency": 8,
              "qdrant_version": "…", "es_version": "…", "python": "3.12.…",
              "packages": {"qdrant-client": "…", "elasticsearch": "…", "numpy": "…"}},

  "seeds": {"distractor_sample": 0, "query_split": 0, "bootstrap": 0},
  "argv": ["python", "scripts/eval/retrieval_sweep.py", "…"],

  "sanity": {"dense_deficit_rate": 0.0, "bm25_deficit_rate": 0.0,
             "assertion": "hits == min(D, |match|)", "verdict": "PASS"},

  "results": {"n_queries": 180, "query_ids": ["…"],
              "means": {"ndcg@10": 0.0, "ndcg@5_chunk": 0.0, "recall@10": 0.0,
                        "recall@20": 0.0, "recall@100": 0.0, "map": 0.0, "mrr@10": 0.0},
              "per_query": {"ndcg@10": [], "map": [], "recall@10": []},
              "counters": {"union_depth": [], "overlap": [], "unique_docs@10": [],
                           "dense_hits": [], "bm25_hits": [],
                           "rerank_pool_occupancy": []}},

  "cost": {"wall_s": 0.0, "p50_query_ms": 0.0, "p95_query_ms": 0.0,
           "crossencoder_pairs": 0, "gpu_s": 0.0}
}
```

`results.per_query` deliberately matches the existing `chunk_one.py` contract
(`{config, source, n_queries, query_ids, means, per_query}`) so that
`scripts/eval/aggregate_stats.py` — which already validates query-id alignment across
files before pairing them — works on G1 output with minimal change.

### 8.3 On-disk layout

```
reports/g1-library-retrieval/
  PROTOCOL.md                                # this file; its sha256 is protocol_version
  AMENDMENTS.md
  fixtures/                                  # copied into contracts/fixtures/queries/
    g1_scifact_{tune,confirm}.json
    g1_nfcorpus_{tune,confirm}.json
    g1_trackb_{clustered200,random200,clustered1000}_queries.json
  qrels/
    scifact/{corpus,queries,qrels}.sha256
    nfcorpus/{corpus,queries,qrels}.sha256
    trackb/judgments.jsonl                   # (query_id, doc_id, grade, judge, ts)
    trackb/agreement.json                    # kappa, label histogram, self-consistency
  manifests/<run_id>.json                    # EvalRunManifest, one per cell
  raw/<run_id>/
    per_query.json                           # chunk_one-compatible
    counters.jsonl                           # Track C, one row per query
    rankings.jsonl.zst                       # top-200 chunk ids per query  <-- the key artefact
  cache/
    query_vectors/<dataset>.<spec_hash>.npy
    crossencoder/<dataset>.<index_id>.<reranker_rev>.jsonl
  analysis/
    00_sanity.md          # §5.4 assertions, hnsw coverage, A/A noise floor
    01_mechanism.md       # Track C: H1b, leg depths, overlap, doc collapse
    10_stage1_screen.md   # tune split, BH-FDR, shortlist
    20_stage2_confirm.md  # confirm split, Holm, CIs, DIFFERENT/EQUIVALENT/INCONCLUSIVE
    30_interaction.md     # theta(c), easiness curve, crossover
    40_trackb.md          # real-PDF veto check, bpref, kappa
    50_answer_level.md    # non-inferiority on top-2
  LibraryRetrievalDefaults.md                # the normative deliverable
```

**`rankings.jsonl.zst` is the highest-value artefact.** With the top-200 chunk ids per
query per cell stored, every metric, every k, every doc-collapse rule, and every
re-analysis after a reviewer objection is recomputable with no GPU, no store, and no
network — exactly the property that makes `aggregate_stats.py` runnable anywhere today.

### 8.4 Regression use

Once recorded, the confirm-split per-query arrays for the shipping default at L0 and L1
become a pinned baseline, in the two-tier scheme `scratchpad.md:282` already proposes:
deterministic checks (chunk counts, hit-deficit rate = 0, index parity) every CI run;
nDCG@10-vs-baseline on retrieval-path changes, nightly or label-triggered, failing when the
metric drops below `baseline_lower_CI − δ`.

---

## 9. Threats to validity

Ordered by how much they could change the recommendation.

**T1 — Embedding-model contamination, and the dense leg's home advantage.**
SFR-Embedding-Mistral is trained on an MTEB-style mixture; SciFact and NFCorpus are both
MTEB retrieval tasks, so the embedder has plausibly seen this data. The consequence is not
merely inflated absolute nDCG — it is *directional*: contamination advantages the **dense**
leg specifically, relative to BM25, which is exactly the axis of RQ1/H1. A conclusion of
the form "dense-only suffices at library scale" is therefore the single most
contamination-vulnerable output of this experiment.
*Mitigations:* (a) that specific conclusion requires clearing Track B, whose ASM PDFs are
not in any public training mixture; (b) report the dense-only and BM25-only arms separately
so the leg contributions are visible rather than hidden inside a fusion score;
(c) state the bias direction in the report so a reader can discount appropriately.
*Residual risk: high.* No available mitigation removes it; only Track B constrains it.

**T2 — Distribution shift: chunks-per-document and the doc-collapse step.**
SciFact is 1.18 chunks/doc and NFCorpus is comparable; a real library at `fixed_tok512` is
≈ 36. This changes three things at once: BM25 length normalization and `avgdl`; how many
*distinct documents* a depth-D chunk pool can contain (the `unique_docs@k` metric exists to
measure precisely this); and how much a document benefits from having many chunks compete
for the same rank positions. A `top_k` or depth recommendation tuned where 100 chunks are
100 documents may be wrong where 100 chunks are 20 documents.
*Mitigations:* the ladder is built on chunk count, not document count (§1.3b); Track B has
the correct ratio by construction; `unique_docs@k` is reported for every cell; the
distractor shells are real ASM chunks, so at L1+ the *index-level* chunk-length and
`avgdl` statistics are library-realistic even though the judged core is not.
*Residual risk: high.* This is the main reason Track A cannot be the only track.

**T3 — Unjudged-relevant distractors confound the size ladder.**
Distractors are drawn from the ASM biomedical corpus; SciFact claims are biomedical. Some
distractors will genuinely answer some claims and will be scored as irrelevant because they
carry no qrel. This depresses measured quality at higher rungs *for reasons unrelated to
corpus size*, inflating the apparent easiness gradient and — worse — potentially producing
a spurious interaction if configurations that retrieve more broadly surface more unjudged
relevant material.
*Mitigations:* (a) quantify it — LLM-judge a random sample of the top-10 distractor
intrusions at L2 and report the estimated unjudged-relevant rate; (b) prefer bpref and
condensed-list nDCG at L2/L3 where the rate is highest; (c) sensitivity analysis:
recompute θ(c) after removing queries whose L2 top-10 contains an intrusion judged
relevant. The alternative — out-of-domain distractors — was rejected because it makes the
haystack artificially easy and destroys the realism the ladder exists to provide.
*Residual risk: medium.* Quantifiable, and it biases mainly the main effect, which the DiD
estimator differences out.

**T4 — Single-host measurement noise and HNSW nondeterminism.**
Measurements run on `coconut`, which concurrently hosts the shared 8×H200 fleet, GoWe, and
two live production API servers (lucid `:8010` / Qdrant `:6343`; asm `:8000` / Qdrant
`:6333`). Qdrant HNSW results are nondeterministic under concurrent optimizer activity —
`libraries-spec.md:530` says so explicitly and prescribes cached query vectors for that
reason. Endpoint availability varies (`detect_live_endpoints`), and R1–R5 ran with 4, 8,
and 16 endpoints respectively.
*Mitigations:* query vectors cached once per dataset, removing the embedding fleet from the
variance budget entirely; indexes frozen with segment/coverage telemetry recorded per rung;
scratch collections on a dedicated Qdrant instance, never prod; the A/A replicate null
quantifies the residual noise floor and δ is required to exceed 3× it; latency metrics are
reported as *ranges* and never used as a primary decision input on a shared host.
*Residual risk: low for quality metrics, medium for latency.*

**T5 — LLM-judge biases in Track B.** Enumerated with mitigations in §4.6. Structurally
contained by the veto-only rule and by the κ floor below which Track B is descriptive only.
*Residual risk: medium, but bounded by design.*

**T6 — Overfitting to the grid.** 320 cells on 300 queries invites selection on noise.
*Mitigations:* the two-stage tune/confirm split with a pre-registered nomination rule, the
NFCorpus consistency requirement, and the exploratory-labelling rule in §7.7.
*Residual risk: low if the protocol is followed; the risk is procedural, not statistical.*

**T7 — Track A measures an unfiltered single library; production is a filtered slice of a
shared index.** `_build_filter` (`stores/qdrant.py:425-455`) ANDs conditions into
`Filter(must=[...])`, only `tenant_id` carries a payload index (`qdrant.py:176-188`), and
an empty list is deliberately unsatisfiable (`:430-434`) — a scope mistake returns zero
rows silently rather than raising. A filtered HNSW search can return fewer than `limit`.
*Mitigations:* §5.4's hit-count assertion on every cell; the filtered arm in §6.5; explicit
conditionality of the recommendation on G2's verdict.
*Residual risk: medium, and shared with G2.*

**T8 — Chunk-length confounds within the reranker.** Cross-encoder scores correlate with
passage length, and the judged cores (short abstracts) and distractor shells (full-text
chunks) have different length distributions, so at L1+ a length-biased reranker could
systematically prefer one population. *Mitigation:* report the chunk-length distribution of
the reranked top-k, split by judged-core vs distractor origin, at every rung.

**T9 — Generalization beyond BV-BRC.** All Track-B evidence comes from one corpus in one
subdomain. The `LibraryRetrievalDefaults` block must state its evidence base rather than
claiming generality.

---

## 10. What this repository does not have yet

Honest gap list with sizing. Track A + Track C is **≈ 4.5 developer-days**; Track B adds
**≈ 3**; the conditional config-surface work adds **≈ 1.5**.

| # | Gap | Why it is needed | Size |
|---|---|---|---|
| 1 | **`python/scripts/eval/retrieval_sweep.py`** — the sweep driver: parameterized `mode`/`rrf_k`/`D`/`C`, query-vector cache, cross-encoder score cache, `rankings.jsonl.zst` dump, manifest emission. Must run **in-process** (§6.6). Emits `chunk_one`-compatible `per_query.json` so `aggregate_stats.py` works. | Nothing today varies any retrieval parameter; `chunk_one.py` sweeps chunkers only, and `scifact_chunk_eval.evaluate_config` hardcodes `HybridRetriever(vstore, tindex, embedder)` at `:313` | ~450 LOC, **1.5 d** |
| 2 | **Per-leg instrumentation.** `retrieve()` returns only the fused list and `RRFScorer.fuse` overwrites `retrieval_method="hybrid"` (`scorers.py:43`), erasing provenance. Recommend an **eval-only subclass** returning `(fused, LegStats)` rather than touching the production class. | Track C / §5.4 / H1b are unmeasurable without it | ~60 LOC, **0.5 d** |
| 3 | **Distractor-shell builder.** Scroll prod `ragstack_sfr_tok512` → scratch Qdrant *and* ES. `bench_filter_truncation.py:250-293` (`scroll_vectors`) is the template but is **Qdrant-only**; the ES half is new and non-optional (§4.3ii). Prefix-guarded teardown modelled on `bench_filter_truncation.guard_scratch:82-89` and `scifact_chunk_eval.teardown`. | The ladder is the corpus design | ~200 LOC, **0.5 d** |
| 4 | **NFCorpus loader**, mirroring `scifact_chunk_eval._load_via_datasets` (graded 0–2 qrels; `_stats.ndcg_at_k` already supports graded gains). | Second dataset is mandatory for MDE and for generalization | ~80 LOC, **0.5 d** |
| 5 | **`_stats.py` additions:** TOST equivalence, Benjamini–Hochberg, MDE/power helper, DiD bootstrap, RBO. `build_stats_table` extended to emit DIFFERENT / EQUIVALENT / INCONCLUSIVE. | §7.2, §7.5, §7.6 | ~150 LOC, **0.5 d** |
| 6 | **`EvalRunManifest`** in `ragstack/provenance.py` (or `ragstack/eval/manifest.py`), embedding `CollectionManifest` and reusing `spec_hash`/`chunk_descriptor`/`ragstack_version`. | §8.2 | ~120 LOC, **0.5 d** |
| 7 | **Track B harness:** clustered/random library sampling, LLM query generation + filtering, fixed-depth pooling, judging loop, κ, bpref, condensed-list nDCG. | §4.5 | ~500 LOC + judge compute, **2–3 d** |
| 8 | **Conditional — config surface,** only if H3 holds: per-collection retrieval overrides. There is **no size-dependent or per-collection retrieval branch anywhere today** — `CollectionSpec` (`api/collections.py:29-54`) carries no retrieval fields, and both retriever construction sites (`deps.py:267-277`, `deps.py:988-997`) read the same global `settings`. Natural seams: `CollectionSpec` + `_hybrid_retriever`. | §11.2 | **1–1.5 d** |
| 9 | **Reporting-surface fixes** (small, worth doing regardless): expose `rrf_k` and `retrieval_candidate_multiplier` in `GET /v1/admin/config` (`admin.py:47-91` omits both); resolve the phantom `settings.top_k` (`config.py:305` has no reader; the API default is the literal `5` at `query.py:72`/`:107`). | Without these a deployed server cannot report, and a settings file cannot set, the very parameters this experiment tunes | ~40 LOC, **0.25 d** |
| — | **Deferred: BioASQ.** The right long-term ladder corpus (real qrels at 14.9M docs). Needs a streaming loader and ≥1M SFR embeddings. `ROADMAP.md:81`, issue #56. | follow-up | ~3 d |

---

## 11. The deliverable

### 11.1 Form

A normative block in `docs/libraries-spec.md`, replacing the G1 paragraph at `§-1:9-17`,
of the following shape:

```yaml
LibraryRetrievalDefaults:            # NORMATIVE. Applies to `scoped` libraries (§4).
  applies_when:
    index_chunk_count: "<= N*"       # measured crossover; omit the branch entirely if H3 falsified
  retrieval_mode: hybrid | vector
  top_k: <int>
  retrieval_candidate_multiplier: <int>
  rrf_k: <int>
  rerank_enabled: <bool>
  rerank_candidates: <int | null>
  evidence:
    protocol: reports/g1-library-retrieval/PROTOCOL.md@<sha256>
    datasets: [scifact@<rev>, nfcorpus@<rev>]
    primary_metric: ndcg@10
    delta_vs_current_default: "<point> [<lo>, <hi>]  (paired bootstrap, n=<n>)"
    holm_p: <float>
    verdict: DIFFERENT | EQUIVALENT
    track_b_veto: passed | not_applicable | blocked
    manifests: reports/g1-library-retrieval/manifests/
  measured_corpus_facts:             # replaces the incorrect figures at spec §-1 and §4
    chunks_per_document_tok512: <float>
    chunks_200_docs: <int>
    chunks_1000_docs: <int>
```

### 11.2 If H3 holds (a size branch is justified)

The block carries an `applies_when.index_chunk_count <= N*` guard, and gap 8 in §10 becomes
required work: `CollectionSpec` gains optional retrieval overrides, `_hybrid_retriever`
(`deps.py:267-277`) reads them, and the library registration path sets them from the
library's chunk count. The spec must also state the behaviour when a library *crosses* N*
through ingestion — the defaults are read at retriever construction, so a crossing does not
take effect until the next construction; either that is documented as acceptable or the
lookup moves per-request.

### 11.3 If H3 is falsified (no branch)

The block carries no `applies_when` guard and reads as a single global recommendation —
possibly identical to today's defaults, in which case the deliverable is:

> `LibraryRetrievalDefaults` = the existing global defaults. Measured EQUIVALENT
> (90% CI within ±0.02 nDCG@10) at 6k, 36k, 200k and 1M chunks on two datasets. No
> size-dependent parameter branch is required; the config surface does not grow.

That is a complete and useful answer to G1, and the protocol is designed so it can be
reached — which is the point of §7.5.

### 11.4 Regardless of outcome

Three things ship no matter what the sweep finds:

1. The corrected chunks-per-document arithmetic, replacing the inconsistent "~4k chunks per
   200 docs" (`libraries-spec.md:11`) and "~72,000 chunks per 1000 PDFs" (`:213`) with
   measured values — the latter feeds the segment-boundary argument at `:213`.
2. The Track-C mechanism result: whether the "BM25 returns 3–4 hits" premise
   (`libraries-spec.md:11`) is true. If H1b holds, that sentence must be struck from the
   spec, and the real depth risk relocated to the dense leg (§1.3c), where it belongs and
   where G2 is already looking.
3. The §10 gap-9 reporting fixes, so a deployed instance can report the parameters it is
   running.

---

## 12. Execution order

1. Gaps 5, 6, 9 (statistics, manifest, reporting surface) — offline, no infrastructure.
2. Gaps 1, 2, 3, 4 (sweep driver, instrumentation, ladder builder, NFCorpus).
3. **Pilot at L0/SciFact/tune-split only:** verify the §5.4 hit assertions, run the A/A
   null, measure the actual per-query SD, and confirm MDE < δ. **If MDE > δ, stop and
   amend** (enlarge the query set or raise δ) before running anything else.
4. Stage 1 screen: full grid, tune split, both datasets, L0 and L2. Publish the shortlist.
5. Stage 2 confirm: shortlist + default, confirm split, all rungs. Publish
   DIFFERENT/EQUIVALENT/INCONCLUSIVE and θ(c).
6. Filtered arm (§6.5).
7. Track B (gap 7), veto check.
8. Answer-level non-inferiority on the top two.
9. Write `LibraryRetrievalDefaults.md`; open the spec PR; pin the confirm-split baselines
   as the regression gate (§8.4).

Steps 1–5 are the minimum for a defensible answer to G1. Steps 6–8 are what make it
publishable.

---

## Amendments

*(none yet — record dated deviations here, never by editing the body above)*
