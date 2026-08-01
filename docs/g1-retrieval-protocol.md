# G1 — retrieval parameter defaults for small, user-owned libraries

**An experiment protocol (pre-registration).** Version 1.1, 2026-07-31.
Gate: [`docs/libraries-spec.md`](libraries-spec.md) §-1 G1 (issue #200).

**Status of this document.** This is a *pre-registration*. The hypotheses, the parameter
grid, the primary metric, the query splits, the minimum effect size, and the decision
rules are fixed **before** any measurement. Every run records a SHA-256 of this file
(`protocol_version`) in its manifest. Deviations are recorded as dated amendments at the
end of this document, never by editing the body.

**Deliverable.** A normative `LibraryRetrievalDefaults` block for `libraries-spec.md`,
with evidence. §11 gives its exact shape, including the shape it takes if the answer is
"no change".

**Citation convention.** Code claims cite `file:line` against the commit that introduced
this document. Claims about `libraries-spec.md` cite *sections*, not lines, because this
change edits that file.

---

## 0. Staging: pilot first, ladder second

The study runs in two parts. They answer different questions and the first does not
subsume the second.

| | **Part I — Pilot** (§4) | **Part II — Full study** (§5) |
|---|---|---|
| Corpus | real ASM PDF libraries, **50 / 100 / 200 documents** | BEIR judged cores in a distractor ladder, **6k → 1M chunks** |
| Chunk span | 1.8k → 7.2k (4×) | 6k → 1M (170×) |
| Ground truth | pooled LLM-judged graded qrels + a SciFact anchor | real graded qrels (SciFact, NFCorpus) |
| Queries | **600** per rung (60% confirm split → n = 360) + the 300-claim SciFact anchor | 300 (SciFact) + 323 (NFCorpus), pooled confirm n = 374 |
| Settles | H1b, hit-count sanity, the noise floor and realized MDE, the corrected corpus arithmetic; then — conditional on that measured power — H2c and a first-pass defaults recommendation for 50–200-doc libraries (§4.6) | H1, H1c, H2, H2b, **H3 (the size branch)**, H4 |
| Cost | ≈ 6 developer-days (5.25 build + ~1 run/analysis) + judge compute | ≈ 3.5 further developer-days (2 build + ~1.5 run/analysis) + a few GPU-hours |

**The pilot is decision-useful on its own.** It runs at exactly the scale v1 ships at, on
the actual target distribution (real full-text PDFs at ≈36 chunks/doc, not 1.18-chunk
abstracts), and its mechanism results (§6.4) are near-deterministic counts rather than
noisy means — they falsify or confirm the spec's stated premise about BM25 depth without
any labels at all.

**What the pilot's quality track can and cannot return.** At 600 queries the 60% confirm
split gives n = 360, a Holm-adjusted MDE of ≈ 0.027 ≈ 1.35 δ, and a 90% TOST half-width of
≈ 0.013–0.017 — inside δ. So **EQUIVALENT is reachable, and DIFFERENT is reachable only for
effects ≳ 1.35 δ**; a real difference of exactly δ returns INCONCLUSIVE. The shippable pilot
outcome is therefore the equivalence one: if the pilot finds the shipping defaults EQUIVALENT
to every alternative across 50–200 documents, that is a shippable answer for v1. A claim that
some alternative *beats* the default by δ is not one this pilot can make. §7.4 gives the
derivation and the pre-registered response if realized power comes in worse than the design
values; §4.6 sorts every pilot output by whether it depends on that power at all.

**The pilot cannot settle H3, and the ladder is why — the reason is power, not confounding.**
The pilot's nesting (§4.1) already holds the relevant set constant, and the DiD estimator
(§7.6) does difference the "small corpora are easy" main effect out; the pilot computes θ and
reports it. What the pilot lacks is a **lever arm**. H3 is an **interaction** — the *ranking*
of configurations must change with size — and θ over a 4× span is a difference of four
correlated terms whose SE exceeds that of any single contrast, while the interaction it must
detect is a fraction of what a 170× span produces. Single-cluster distractors compound it: an
interaction that did clear the bar could not be told apart from cluster-specific
idiosyncrasy. §5.2 supplies the lever arm. Running the pilot alone and branching the config
surface on its point estimates is precisely the error §5.2 exists to prevent.

---

## 1. Background

### 1.1 What ships versus what was measured

Shipping retrieval defaults live in `python/ragstack/config.py:304-325`:

| Setting | Default | Line |
|---|---|---|
| `top_k` | `5` | `config.py:305` |
| `retrieval_candidate_multiplier` | `2` | `config.py:309` |
| `rrf_k` | `60` | `config.py:310` |
| `rerank_enabled` | `False` | `config.py:323` |
| `rerank_candidates` | `50` | `config.py:324` |

Composed depth arithmetic:

- `python/ragstack/api/routers/query.py:199-204` — `depth = max(top_k, rerank_candidates)`
  when reranking (`:202`), else `depth = top_k` (`:204`).
- `python/ragstack/retrieval/retriever.py:53` — `depth = top_k * self.candidate_multiplier`,
  and **both legs are asked for the same depth** (`retriever.py:60`, `:65`).

With shipping defaults and rerank off, each leg is asked for **10** candidates. With
rerank on it is `max(5, 50) * 2 = 100`. Turning the reranker on changes first-stage
breadth by 10× as a side effect — a confound that must be pinned (§5.3, H2c).

Every retrieval-quality measurement in this repository used a *different* configuration
from the one that ships:

| Run | File | Corpus | Queries / ground truth | Retrieval settings | Significance |
|---|---|---|---|---|---|
| R1 | `python/scripts/eval/chunking_compare_report.md` | 300 ASM articles, 7k–33k chunks | 300 known-item title→own-doc proxy | `top_k=10`, rerank-pool 50, rerank **on**, `rrf_k=60`, mult 2 | none |
| R2 | `python/scripts/eval/chunking_compare_full_report.md` | 3,817 ASM articles, 90k–415k chunks | 1,000 title-proxy | same | none |
| R3 | `reports/chunking-comparison-overview.md:197` §6.1 | 1,500 ASM docs, 30k–150k chunks | 1,000 title-proxy | same | none (declared underpowered) |
| R4 | `python/scripts/eval/chunking_compare_7way_report.md` | 300 ASM docs, 5.6k–28k chunks | 300 title-proxy | `top_k=10`, rerank-pool 50, rerank on | paired bootstrap + Holm–Wilcoxon |
| R5 | `python/scripts/eval/scifact_chunk_eval_report.md` | SciFact 5,183 abstracts, 5.8k–19k chunks | **300 claims, real graded qrels** | retrieve=rerank=100, rerank on | paired bootstrap + Holm–Wilcoxon |
| R5b | `reports/semantic-chunking-experiments.md:357` (Exp 7) | same SciFact | same 300 claims | retrieve **300** / rerank 100 | same |

**No experiment in this repository has ever varied `rrf_k`,
`retrieval_candidate_multiplier`, `top_k`, or `rerank_enabled`.** Verified: all three
harnesses construct `HybridRetriever(vstore, tindex, embedder)` with no `rrf_scorer` and
no `candidate_multiplier` (`chunking_compare_7way.py:765`, `scifact_chunk_eval.py:313`,
`chunking_compare.py:608`), so `rrf_k=60` and `multiplier=2` are inherited defaults
(`scorers.py:23`, `retriever.py:25`). No harness has ever used
`HybridRetriever.retrieve(mode="vector")` or `mode="bm25"` despite the parameter existing
(`retriever.py:47`, documented at `:49-52`). `ROADMAP.md:80` states the gap outright: the
ablation harness (#122) is "the missing ability to isolate
embedding/BM25/RRF-k/rewriting/graph/answer-quality; today only chunking is isolated."

### 1.2 The one accidental data point

R5 and R5b score the *same* corpus, queries, qrels, embedding model, and reference
chunker. They differ in one thing: R5 ran before commit `84223aa` ("decouple retrieval
breadth from rerank pool") with a single pool of 100; R5b ran after, with
`--retrieve-pool 300 --rerank-pool 100`.

**`fixed_tok512` nDCG@10: 0.698 (R5) → 0.744 (R5b). Δ ≈ +0.046.**

That uncontrolled retrieval-breadth change moved the primary metric by roughly **twice**
the largest chunking effect ever observed here (`fixed_tok256`, ΔnDCG@10 = +0.023
[+0.006, +0.040], Holm p = 0.077, not distinguishable). Seven chunkers were statistically
indistinguishable; one unmeasured pool-depth knob was not. This is the strongest available
prior that the G1 axes matter, and a demonstration of how easy it is to move these numbers
without noticing.

### 1.3 The corpus arithmetic the spec gets wrong

`libraries-spec.md` §-1 G1 motivated the gate with "a ~200-doc library is ~4k chunks where
BM25 may return 3–4 hits against dense's 20." Both halves are wrong, and both are material
to the design. This change corrects them in the spec; the correction is restated here
because the pilot's rung sizes derive from it.

**(a) The chunk count is understated by ~1.8×.** `ragstack_lib_v1` shares
`ragstack_sfr_tok512`'s build spec (`libraries-spec.md` §4). Measured chunks-per-document
for `fixed_tok512` on real ASM articles is **36.2**
(`reports/chunking-comparison-overview.md:223`, 1,500 docs → 54,270 chunks) and **33.8** on
a 300-doc subset (`chunking_compare_7way_report.md:96`). Therefore:

| Library | Chunks at `fixed_tok512` (33.8–36.2/doc) |
|---|---|
| 50 docs | 1,700 – 1,810 |
| 100 docs | 3,380 – 3,620 |
| 200 docs | **6,760 – 7,240** (spec said ~4,000) |
| 1,000 docs | **33,800 – 36,200** (spec said ~72,000) |

`libraries-spec.md` §4 separately asserted "a 1000-PDF library is ~72,000 chunks", which
is 72 chunks/doc — the measured figure for `fixed_tok256`
(`chunking-comparison-overview.md:222`), not `tok512`. The spec was internally inconsistent
by a factor of two, and its segment-boundary argument rested on the larger number. Both
figures are corrected in this change; the *measured* replacement is a deliverable (§11).

The segment-boundary argument itself is **withdrawn rather than re-derived**. Correcting the
numerator leaves the denominator — segment count — unmeasured, and the ~99 segments the spec
divided by came from the 24.8M-point production collection. The two segment counts this
repository actually has at anything like library scale (500k points / 8 segments from G2, §2.1;
6,108 points / 2 segments in §8.2's manifest example) put a 36k-chunk library at
4,500–18,000 points per segment, well *above* the ~625 full-scan handoff rather than below it.
The spec now says the side is undetermined until measured, and this protocol supplies the
measurement: `segments_count` is recorded per rung (§8.1) on collections built to the
`ragstack_lib_v1` spec, which is the first library-sized observation of it.

**(b) The interesting scale variable is chunk count, not document count.** Both retrieval
legs index *chunks*. BM25 document-frequency statistics, HNSW graph connectivity, and
Qdrant's segment-level full-scan handoff are all functions of indexed chunk count. This
has a direct consequence for corpus design:

> The full SciFact corpus at `fixed_tok512` is **6,108 chunks**
> (`scifact_chunk_eval_report.md`, chunk-structure table) — almost exactly the chunk count
> of a **180-document real library**. Subsampling SciFact to 200 *documents* would produce
> ~236 chunks, which is not a library; it is a toy.

So the pilot uses real PDFs (correct chunks/doc by construction) and the full study holds
a judged core whole and grows the index around it (§5.2).

**(c) The "BM25 returns 3–4 hits" premise is probably backwards.**
`ElasticsearchTextIndex.search` (`stores/elasticsearch.py:143-168`) issues `size=top_k`
(`:150`) against a single `match` on `content` (`elasticsearch.py:82`) — an exact,
non-approximate search returning `min(size, |matching docs|)`. A BM25 leg asked for 10
returns 10 unless fewer than 10 chunks in the whole index contain *any* query term, which
is implausible for a multi-term biomedical query over 7,000 chunks.

Conversely `QdrantVectorStore.search` (`stores/qdrant.py:243-249`) passes `limit=top_k`
(`:246`) with **no `search_params`** — no `hnsw_ef` override, no `exact`, no oversampling
— so the *dense* leg is the one that can silently under-return under a selective filter on
approximate HNSW. That is the failure mode sibling gate G2 measured (§2.1). The spec's
premise is therefore restated as a registered hypothesis (H1b) rather than an assumption,
and the risk is relocated to the dense leg.

### 1.4 Why RRF might behave differently at shallow depth

`RRFScorer.fuse` (`scoring/scorers.py:40`) computes `score += 1.0 / (self.k + rank + 1)`
with 0-based `rank` and `k=60`. At the shipping depth of 10 candidates per leg every rank
term lies in `[1/61, 1/70]` — a spread of 13%. The constant `k=60` was chosen (Cormack et
al., 2009) for TREC runs of depth 1,000, where it suppresses the long tail. At depth 10 it
flattens the ranking almost completely: the fusion approximates a pure *vote count* (how
many legs found the chunk) with rank order as a tiebreaker. Whether that is good at
library scale is the open question of H4.

Two structural notes for the analysis:

- `fuse` returns the **full union untruncated** (`scorers.py:46`); truncation happens at
  `retriever.py:74`. There is no padding or re-fetch when one leg is short — a short leg
  contributes fewer entries and the result degrades toward single-leg behaviour with **no
  signal at any API layer**.
- `fuse` relabels every chunk `retrieval_method="hybrid"` (`scorers.py:43`), **erasing leg
  provenance**. Per-leg attribution requires new instrumentation (§10, gap 2).

---

## 2. Scope

**In scope.** First-stage retrieval and fusion for a *single* library: `rrf_k`, per-leg
candidate depth (and the `top_k` × `candidate_multiplier` pairs that realize it), `top_k`,
`rerank_enabled`, `rerank_candidates`, and retrieval mode (`hybrid` | `vector` | `bm25`),
as a function of index size.

**Out of scope** (fixed at defaults, recorded in every manifest). Query rewriting
(`rewrite_strategies="passthrough"`), the graph leg (`use_graph=False`), multi-library RRF
fan-out (`LIBRARY_FUSION_MAX_LEGS`, `libraries-spec.md` §4), the embedding model, the
chunker, and answer generation except as the tertiary track in §6.3.

### 2.1 Relationship to G2 — **G2 has now PASSED**

G2 (`libraries-spec.md` §-1) asked whether a *filtered* Qdrant search returns
`min(k, |match set|)`. G1 asks what parameters to use given that it does. The dependency is
hard: if the dense leg under-returns at depth D, every G1 conclusion at that depth is about
a degraded pipeline.

**Recorded result (harness: `python/scripts/bench_filter_truncation.py`).**

| | |
|---|---|
| Verdict | **PASS** |
| Cells | 45 |
| Trials | 1,080 |
| Outcome | **every** trial returned `min(k, \|match\|)` (pass criterion `bench_filter_truncation.py:154`, expectation set at `:628` from `min(k, msize)` per `:144`) |
| Recall vs `exact:true` | **1.000** |
| Index | 500k real 4096-d SFR vectors, HNSW fully built across 8 segments (coverage banner, `bench_filter_truncation.py:913-939`) |

**Interpretation, stated conservatively.** #199's truncation did not reproduce in the v1
conjunction shape (`library_id == X AND tenant_id ANY […]`). This **confines #199's finding
to its original conditions** — a single key/value at 1% selectivity on synthetic 128-d
vectors — rather than refuting it. Nothing here licenses the general claim that filtered
HNSW never truncates.

**Consequences for G1.**

1. The §6.4 returned-hit-count assertion is **retained on every cell** — in its per-leg form
   (a single `min(D, N_matching_filter)` ceiling is right for the dense leg and wrong for
   BM25; §6.4). It is cheap, it is the guard that makes a G1 number interpretable, and a pass
   on a 500k synthetic-scope sweep is not a pass on every G1 cell. **G1 results measured on
   cells that fail §6.4's *deficit* criterion are void, not interesting** — a *starved* leg,
   by contrast, is a finding, not a failure.
2. The filtered arm (§5.5) is retained but downgraded from *blocking* to *confirmatory*:
   its expected result is agreement with the unfiltered arm.
3. Threat T7 (§9) drops from *medium* residual risk to *low*.

---

## 3. Research questions and hypotheses

All hypotheses carry a falsification condition. δ is the minimum shippable effect, fixed at
**δ = 0.02 nDCG@10** and justified in §7.4. Unless stated otherwise a directional claim
requires both `|Δ| ≥ δ` and Holm-adjusted `p < 0.05` on the confirm split (§7), with
consistent sign in each dataset separately. **δ is the threshold worth acting on; it is not
claimed to be the threshold this design resolves.** §7.4 puts the Holm-adjusted confirm-split
MDE at ≈ 1.35 δ in both parts, so directional claims are reachable only above that, while
equivalence claims (TOST, §7.5) are reachable at δ. Whether δ itself becomes resolvable is
settled empirically by the pilot's noise floor and realized SDs, with a pre-registered
response either way (§7.4, §12 step 6).

The **Part** column says where each hypothesis is decided. Hypotheses marked *Pilot* are
answerable at 50–200 documents; those marked *Full* need the ladder.

| ID | Claim | Part |
|---|---|---|
| H1 | Hybrid ≥ dense-only at library scale | Pilot (screen) → Full (decide) |
| H1b | Neither leg is depth-starved at D=10 over ≈7k chunks | **Pilot (decides outright — exact counters)** |
| H1c | BM25-only is materially worse than both | Pilot (screen) → Full (decide) |
| H2 | Reranking with pool `C ≥ 25` improves nDCG@5 by ≥ δ | Full |
| H2b | Rerank effect is monotone non-decreasing in `C` | Full |
| H2c | The shipping "rerank gain" is a breadth effect, not a cross-encoder effect | Pilot (confirms at 200 docs via TOST; falsifies only above ≈1.35 δ — §4.6) → Full |
| H3 | The optimal configuration differs between library and production scale | **Full only** |
| H3b | The crossover chunk count `N*` is estimable to within 3× | Full |
| H4 | Optimal `rrf_k` rises with per-leg depth; at D=10 the optimum is below 60 | Pilot (screen) → Full (decide) |
| H5 | `top_k` beyond context-precision@k < 0.5 does not improve answers | Tertiary (§6.3) |

### RQ1 — Does hybrid RRF still beat dense-only at library scale?

**H1.** At library scale (≈7k chunks) and shipping depth, hybrid retrieval has
nDCG@10 ≥ dense-only.
*Falsified if* dense-only exceeds hybrid by ≥ δ with Holm p < 0.05 on the confirm split.
*Concluded equivalent if* the 90% CI of (hybrid − dense) lies entirely within ±δ (TOST,
§7.5) — in which case hybrid is retained anyway, on the grounds that it costs one extra
exact ES query and provides lexical-match coverage that nDCG under-weights.

**H1b (mechanism; no ground truth needed; decided by the pilot).** The premise formerly in
`libraries-spec.md` §-1 — that BM25 returns 3–4 hits where dense returns 20 — is false at
chunk level in a 50–200-document library. Registered prediction, in terms of §6.4's
`*_starved_rate` (returned < D for *any* reason): at per-leg depth D = 10 and at *every*
pilot rung, `bm25_starved_rate ≤ 5%` and `dense_starved_rate ≤ 1%` (unfiltered) — i.e.
**neither** leg is depth-starved and any hybrid/dense difference is a *ranking* effect, not a
*depth* effect.
*Falsified, per leg, if* that leg's starved rate exceeds its threshold: BM25 above 5%, dense
above 1%. (The thresholds are the complements of the predictions; an earlier draft predicted
≥ 99% for dense but falsified at > 5% for both, which are different claims.) A falsification
is the more interesting outcome: it reframes RQ1 as a depth problem and must be reported as
such rather than absorbed — and, per §6.4, it is *not* a stop condition and does not void the
cell; only a non-zero `*_deficit_rate` does that.
*Estimability.* These are exact per-query integers, not noisy means, so no MDE applies — but
a rate bound still needs enough queries to be observable. At 600 queries per rung the rule of
three puts a one-sided 95% upper bound of 3/600 = **0.5%** on a zero-observation starved rate,
so the 1% dense threshold is genuinely estimable. At the 150 queries an earlier draft used
the same rule gives only 2%, which cannot distinguish "≥ 99%" from "≥ 98%" — another reason
the query set is 600 (§4.2). Report `*_starved_rate` with an exact Clopper–Pearson interval,
per leg, per rung, per D.

**H1c.** BM25-only is materially worse than both at library scale.
*Falsified if* BM25-only is within δ of hybrid on the confirm split.

### RQ2 — Does reranking help or hurt at low candidate depth?

**H2.** With the reranker enabled and candidate pool `C ≥ 25`, nDCG@5 improves by ≥ δ over
`rerank_enabled=False` **at matched first-stage depth**.
*Falsified if* the effect is negative, or within ±δ by TOST.

**H2b.** The rerank effect is monotone non-decreasing in `C` over
`C ∈ {10, 25, 50, 100, 200}` at fixed first-stage depth: a cross-encoder cannot recover
recall the first stage never produced, so a shallow pool can only reorder what is there.
*Falsified if* the point estimate at some `C_i < C_j` exceeds that at `C_j` by ≥ δ with a
diff CI excluding 0 — i.e. reranking a *deeper* pool is actively worse, indicating the
cross-encoder promotes distractors.

**H2c (the confound; decided by the pilot).** At *unmatched* depth — the shipping
behaviour, where enabling rerank multiplies first-stage breadth by 10×
(`query.py:199-204`) — any measured "rerank gain" is attributable to breadth, not to the
cross-encoder. Registered decomposition, run at the 200-doc pilot rung:

- *reranker effect* = `Δ(rerank on, D=100) − Δ(rerank off, D=100)`
- *breadth effect* = `Δ(rerank off, D=100) − Δ(rerank off, D=10)`

*Confirmed if* the matched-depth reranker effect is EQUIVALENT to zero (90% CI ⊂ ±δ);
*falsified if* it is DIFFERENT, i.e. ≥ δ **and** Holm p < 0.05. The pilot can run the
decomposition because it is a within-rung contrast needing no size lever, only matched depth
— but only the confirmation arm is comfortably within its power. At the pilot's confirm-split
MDE of ≈ 1.35 δ, a genuine reranker contribution of exactly δ returns INCONCLUSIVE, so the
falsification arm carries over to Part II (§4.6 item 5).

### RQ3 — Does the tuned configuration need a size-dependent branch?

**H3 (the actual gate question; full study only).** The configuration maximizing nDCG@10 at
library scale (L0/L1) differs from the one maximizing it at production scale (L2/L3).
Formally, for at least one candidate configuration `c`, the difference-in-differences

  θ(c) = [M(c, L0) − M(default, L0)] − [M(c, L2) − M(default, L2)]

satisfies |θ(c)| ≥ δ with a 95% paired-bootstrap CI excluding 0.
*Falsified if* every candidate's θ CI lies within ±δ → **no size branch; one set of
defaults for all index sizes.** That is a positive, publishable result and it simplifies
the config surface; §11.3 gives that deliverable.

H3 is an **interaction**, not a main effect. Small corpora are easier — that is guaranteed
and uninteresting (§5.2 iii). A size-dependent parameter branch is justified only if the
*ranking* of configurations changes with size, which a uniform easiness shift cannot
produce.

**H3b (crossover location).** If H3 holds, the crossover chunk count `N*` at which the
argmax changes is estimable to within a factor of 3 from the four-rung ladder.
*Falsified if* the argmax changes non-monotonically across rungs, in which case report "no
stable crossover" and branch on the coarser of the two adjacent rungs.

### RQ4 — Is `rrf_k = 60` right at shallow depth?

**H4.** The nDCG-optimal `rrf_k` increases with per-leg depth D; at D = 10 the optimum is
below 60.
*Falsified if* nDCG@10 is flat in `rrf_k` (all pairwise diff CIs across
`rrf_k ∈ {1, 10, 20, 60, 120, 240}` within ±δ at every D), or if the optimum does not move
with D.

### RQ5 — What is `top_k` for a chatbot?

**H5.** Because these chunks are packed into an LLM prompt (`llm_max_context_chars = 8000`,
`config.py:315`; `RagGenerator._format_context`, `ragstack/llm.py:107`), increasing `top_k`
beyond the point where *context precision*@k falls below 0.5 does not improve answer quality
and increases distraction.
*Tested only as a tertiary non-inferiority check* (§6.3); `top_k` is otherwise chosen from
the retrieval metrics and the latency budget.

---

# Part I — Pilot: the 50 / 100 / 200-document sweep

## 4. Pilot design

The pilot answers "what should a v1 library use?" at the sizes v1 actually ships at, on
real full-text PDFs, and it does so with a corpus construction that is paired across
rungs so its size contrasts are not pure easiness artefacts.

### 4.1 Corpora — nested real-PDF libraries

Three libraries drawn from the production ASM corpus, built with the `ragstack_lib_v1`
build spec (`fixed_tok512`, SFR-Embedding-Mistral, 4096-d):

| Rung | Documents | Expected chunks (33.8–36.2/doc) | Construction |
|---|---|---|---|
| P50 | 50 | 1,700 – 1,810 | the **judged core** |
| P100 | 100 | 3,380 – 3,620 | P50 **∪** 50 more from the same topical cluster |
| P200 | 200 | 6,760 – 7,240 | P100 **∪** 100 more from the same topical cluster |

**Nesting is the whole point.** P50 ⊂ P100 ⊂ P200, queries and the judged core are
identical at every rung, and rungs differ **only** in how many unjudged in-cluster
distractor documents are present. That makes every metric **fully paired across rungs** and
makes a difference-in-differences estimator available inside the pilot (§7.6), exactly as
in the full ladder — only over a 4× lever arm instead of 170×.

**Topical concentration is deliberate.** Documents are selected as a single topical cluster
(k-means over document-centroid embeddings, or one journal/subject facet). A personal
library is topically concentrated; concentration raises inter-document similarity and makes
discrimination *harder*, working against the "small is easy" effect. A second,
labelling-free contrast is available at zero cost:

- **P200-random** — composition is **`P50 ∪ 150 documents sampled uniformly`** from the
  production ASM corpus, *not* 200 uniform documents. Holding the judged core fixed is what
  makes it comparable: P200-random differs from P200 in exactly one factor — whether the 150
  added distractors are in-cluster or uniform — and its queries, relevant set and P50 core are
  identical to every other rung, so it is paired with them the same way P100 and P200 are. Run
  with Track-C mechanism metrics only (no judging). It isolates topical concentration as its
  own factor and tells us whether the clustered result generalizes. Note P200 adds 150
  documents to P50 too, so the two 200-doc rungs are matched in size as well as in core.

**Anchor: SciFact at L0.** The full SciFact corpus at `fixed_tok512` is 6,108 chunks
(§1.3b) — within 10% of P200's chunk count, but with **real graded qrels** rather than
LLM judgments. The pilot runs the same grid over it. Its role is threefold: a decision-grade
quality reading at the top of the pilot's chunk range; a calibration point for the LLM
judge (§4.4); and continuity with R5/R5b, so pilot numbers are comparable to a published
baseline. It is **not** a doc-count rung — at 1.18 chunks/doc it has the wrong document
geometry (T2, §9).

### 4.2 Queries

**600 per library**, generated by a local LLM from randomly sampled **chunks of the P50 core**
(not documents, and not from the added distractor documents), then filtered. A query is
discarded if:

- it is answerable from its source chunk's document title alone;
- it names a document explicitly;
- two human reviewers judge it not to be a plausible researcher question (on a sampled audit
  of 100; the rest are screened by the same rubric applied by the generator's critic pass).

Queries are generated **before** any retrieval run and pinned as a fixture (§8). Generating
from the P50 core is what keeps the relevant set nested: the intended answer for every query
lives in the smallest rung.

**Why 600 and not 150.** 150 was the earlier figure and it does not work: at a 60% confirm
split it leaves n = 90, where MDE ≈ 0.044 and the 90% TOST half-width ≈ 0.026 — *both* larger
than δ, so every quality comparison would be INCONCLUSIVE by construction (§7.4). 600 gives a
confirm split of 360, matching Part II's pooled 374. The reason enlargement is the affordable
fix here rather than dropping the split or inflating δ is that the pilot's binding cost is
**judge compute, not developer time**: query generation is one LLM pass, and pooled judging at
depth 20 over the pilot grid is on the order of 40–80 (query, document) pairs per query,
i.e. ≈ 25k–50k judgments at temperature 0 on the local fleet. The human-annotation subsample
for κ stays at 100 pairs (§4.4) and does not scale with the query count.

**Generation bias.** Generating queries from the text they are meant to retrieve imports a
lexical-overlap bias that favours the sparse leg. It is registered as threat **T1b** (§9),
which also fixes the generation mode (paraphrase-first) and requires overlap to be recorded
as a per-query covariate.

### 4.3 Judgments — TREC-style pooling, fixed depth

For each query, form the pool as the **union of the top-20 chunks from every pilot grid cell
at every rung**, mapped to documents. Judge each (query, document) pair once, graded 0/1/2,
and reuse the judgments across all cells and rungs.

**Fixed pool depth of 20 for every cell is mandatory.** If pool depth varied with the cell's
parameters, configurations contributing more to the pool would be systematically advantaged
(classic pooling bias) — and sweeping `rerank_candidates` *is* a pool-depth manipulation.

**Two qrel sets are reported, and they answer different questions.**

| Qrel set | Definition | Used for |
|---|---|---|
| `core` | judged relevant **and** in P50 | the paired across-rung contrasts and θ (§7.6): the relevant set is identical at every rung by construction |
| `pooled` | every judged-relevant document, wherever it came from | the realistic per-rung quality reading |

Divergence between the two is itself the measurement of T3 (unjudged/newly-judged relevant
distractors, §9) at pilot scale.

**Pooling-robust metrics.** Because pilot qrels are pool-derived and therefore incomplete,
report **bpref** and judged-only condensed-list nDCG alongside plain nDCG@10. Plain recall@k
is **not** reported for the pilot's LLM-judged libraries — it is unidentifiable with
incomplete judgments. The SciFact anchor has complete qrels and does report recall.

### 4.4 The LLM judge: secondary authority, measured quality

**Position.** The LLM judge may **veto** and may **screen**; on the pilot it is the only
graded label source for real PDFs, so it also *selects* — but only for a recommendation
explicitly scoped to 50–200 documents, and only when the SciFact anchor agrees in sign. A
pilot recommendation that the SciFact anchor contradicts is reported as a **conflict**, not
as a result.

**Judge quality is measured, not assumed.** Two human annotators independently judge a
random 100-pair subsample; report Cohen's κ (human–human) and κ (judge–human). **If
κ(judge–human) < 0.4 the pilot's quality track is descriptive only**, the recommendation
falls back to the SciFact anchor plus Track C, and the size question defers entirely to
Part II.

**Known biases, with mitigations.**

| Bias | Effect here | Mitigation |
|---|---|---|
| Position / order | Judge favours earlier-presented candidates | Randomize order per pair; judge one (query, doc) pair at a time, never a ranked list |
| Verbosity / length | Longer chunks judged more relevant | Report length distribution of judged-relevant vs judged-irrelevant; chunk length as a covariate in sensitivity analysis |
| Self-preference | Judge favours its own family's output | Judge model must be a different family from the answer generator; both recorded in the manifest |
| Leniency / topical drift | Topically-related but non-answering passages marked relevant → recall inflated, differences flattened | Rubric requires the passage to *answer* the query, with a negative exemplar; measure κ against humans |
| Score compression | Graded scale collapses to 2 values | Report the label histogram; if >90% of positives are one grade, treat as binary |
| Non-determinism | Same pair, different label | Temperature 0, fixed seed; duplicate 10% of pairs, report self-consistency |
| Pooling bias | Unjudged relevant documents counted irrelevant | Fixed pool depth across cells; bpref and condensed-list metrics |
| Config leakage | Judge infers which system produced a candidate | Judge sees only (query, chunk text) — no scores, no ranks, no configuration identifiers |

### 4.5 Pilot grid

Reduced from the full grid (§5.4) to what the pilot can resolve.

| Factor | Pilot levels | Note |
|---|---|---|
| rung | P50, P100, P200 (+ P200-random, mechanism only) (+ SciFact L0 anchor) | §4.1 |
| `mode` | `hybrid`, `vector`, `bm25` | `retriever.py:47` |
| `rrf_k` | 1, 20, 60, 240 | hybrid only; coarse — H4 is screened, not decided |
| `D` — per-leg depth | 10, 100 | **absolute** (§5.3); D=10 is the shipping default, D=100 is the shipping rerank-on breadth |
| `rerank_enabled` | False, True (at D = 100 only) | matched-depth arm for H2c |
| `C` — `rerank_candidates` | 25, 50, 100, with `C ≤ D` | derived offline (§5.4b) |
| `k` — report cutoff | 1, 3, 5, 10, 20 | derived, free (§5.4a) |

First-stage retrievals actually run, per rung: `bm25` 2 (one per D) + `vector` 2 +
`hybrid` 4 `rrf_k` × 2 D = 8 → **12 cells per rung**. Over 3 judged rungs + P200-random +
SciFact = 5 rung-equivalents → **60 first-stage cells**, 600 queries each (300 for the
SciFact anchor, which has a fixed test set) ≈ 33k query-executions. Cross-encoder work is
bounded by the D ≤ 100 ceiling and the score cache (§5.4b): a few GPU-hours.

### 4.6 What the pilot decides, and what it does not

The pilot's outputs split three ways by *what they depend on*: the label-free ones depend on
nothing but exact counters; the quality ones depend on the realized power, which is measured
in step 4 (§12) before any of them is claimed.

**Decides unconditionally — no labels, no power budget.**

1. **H1b** — whether the spec's BM25-depth premise is true, at all three sizes. Exact
   per-query integers; at 600 queries the rule of three bounds a zero-starvation rate at
   0.5%, so even the ≥ 99% arm of the prediction is estimable (§3, H1b).
2. **§6.4 hit-count sanity** at library scale, in the shape v1 ships — the per-leg ceiling
   probe and the starved/deficit split.
3. **The noise floor and the realized MDE.** The A/A replicate null (§7.2 replication) gives
   the per-query SD; the realized per-query difference SDs on the actual G1 contrasts give
   the confirm-split MDE and the 90% TOST half-width. **This is a stop-and-amend gate**: if
   the realized MDE exceeds 1.5 δ or the realized 90% half-width exceeds δ, no quality
   verdict is claimed until §7.4's pre-registered response is applied and recorded as an
   amendment (§12 step 6).
4. **The corrected corpus arithmetic** — measured chunks/doc on the real libraries, plus the
   first library-sized `segments_count` (§1.3a, §8.1). Both ship regardless of outcome
   (§11.4).

**Decides conditionally, on the realized power measured in item 3.** At the design values
(confirm n = 360, Holm-adjusted MDE ≈ 0.027, 90% TOST half-width ≈ 0.013–0.017) these are
reachable; at materially worse realized SDs they are not, and they then move to "does not
decide" by the same gate.

5. **H2c** — the breadth/reranker decomposition at P200, a within-rung contrast.
   Asymmetrically decidable: H2c is *confirmed* when the matched-depth reranker effect is
   EQUIVALENT (90% CI ⊂ ±δ), which the design does reach; it is *falsified* only by an effect
   ≳ 1.35 δ, so a genuine reranker contribution of exactly δ would be reported INCONCLUSIVE.
   The falsification arm is therefore power-limited and Part II retains it.
6. **A v1 defaults recommendation scoped to 50–200 documents**, provided the SciFact anchor
   agrees in sign, κ(judge–human) ≥ 0.4, **and** every comparison behind the recommendation
   lands DIFFERENT or EQUIVALENT rather than INCONCLUSIVE. The realistic shape of this output
   is "the shipping defaults are EQUIVALENT to every alternative across 50–200 documents" —
   an equivalence claim, which is the verdict the design is powered for. A claim that some
   alternative *beats* the default by δ is not reachable here.

**Does not decide.**

1. **H3.** See §5.2 — and note the reason is *power*, not confounding: the DiD in §7.6 does
   difference the easiness effect out, and the pilot computes it. What the pilot lacks is a
   lever arm. θ over a 4× span is a difference of four correlated terms whose SE exceeds any
   single contrast's, while the interaction it must detect is a fraction of the one a 170×
   span would produce; and single-cluster distractors would make any interaction that *did*
   clear the bar cluster-specific. A non-zero pilot θ is a reason to prioritize Part II, never
   a reason to branch the config surface.
2. **H2 / H2b** at pool depths beyond 100, or **H4** at fine `rrf_k` resolution — the pilot
   grid is coarse by design.
3. **Any quality difference at or just above δ.** The design resolves ≈ 1.35 δ; effects
   between δ and that are INCONCLUSIVE by construction and pre-registered as such.
4. Anything about production scale. The pilot's largest index is ≈ 7k chunks; production is
   3M–25M (`libraries-spec.md` §16).

---

# Part II — Full study: the distractor ladder

## 5. Full-study design

### 5.1 Ground truth available in this repository

| Source | Ground truth | Size | Status |
|---|---|---|---|
| SciFact (BEIR) | **real graded document-level qrels**, 300 test claims, 339 judgments | 5,183 abstracts → 6,108 chunks at `tok512` | **implemented**: `python/scripts/eval/scifact_chunk_eval.py`, three loader fallbacks (HF `datasets` → `beir` → `ir_datasets`), cached under `HF_HOME` |
| ASM / BV-BRC corpus | **none** (unlabelled) | 448k docs; `ragstack_sfr_tok256` 24.8M chunks, `tok512` 12.6M, `semantic` 3.0M (`libraries-spec.md` §16) | live in prod Qdrant/ES |
| Known-item title→doc proxy | weak pseudo-qrels; chunking-*insensitive* by construction | 300–1,000 queries over ASM | implemented: `chunking_compare_7way.py` |
| NFCorpus, BioASQ | real qrels | NFCorpus 3.6k docs / 323 test queries / graded 0–2; BioASQ 14.9M docs | **not implemented**; `ROADMAP.md:81` ("add BioASQ (#56) + NFCorpus"), `scratchpad.md:281` |
| LLM-judged relevance | none | — | **not implemented** as a harness; a hot-swappable LLM registry exists (`python/ragstack/api/model_registry.py`, `docs/model-registry.md`) |

The tension cannot be dissolved: SciFact has genuine qrels but is 1.18 chunks/doc of
abstract text; the ASM corpus is the target distribution but has no labels. Part I resolves
it by labelling real PDFs with a measured-quality judge; Part II resolves it by using real
qrels and growing the *index* around them.

**Track A — decision-grade, selects the defaults.** Two BEIR datasets with real qrels, each
embedded in a distractor ladder.

- **A1 — SciFact-in-a-haystack.** All 5,183 abstracts chunked with the `ragstack_lib_v1`
  build spec, giving the 6,108-chunk judged core; 300 test claims, graded qrels, unchanged
  at every rung.
- **A2 — NFCorpus-in-a-haystack.** Held out for generalization: a configuration must win (or
  be equivalent) on both, or it does not ship. NFCorpus is biomedical, its queries are *real
  user queries* rather than synthetic claims, its qrels are graded 0–2, and its documents are
  longer than SciFact abstracts. `_stats.ndcg_at_k` already handles graded gains
  (`_stats.py:240`, gain `2**grade - 1` at `:251`/`:253`). The loader is a gap (§10, gap 4).

**Track B — external validity, veto only.** Part I's real-PDF libraries extended to
**1,000 clustered documents** (≈ 36k chunks, §1.3a), with the same pooled-judgment
machinery. Its role is to catch a Track-A conclusion that inverts on real full-text PDFs at
the L1 rung. A Track-A winner that loses on Track B by ≥ 2δ triggers human adjudication of
that comparison and blocks the recommendation pending resolution. Rationale for veto-only:
a label set produced by a language model is not evidence *independent* of the systems being
ranked, and LLM relevance labels compress score distributions, which biases toward "no
difference" and would let a weak configuration pass by failing to be distinguished.

**Track C — mechanism and sanity, no ground truth.** §6.4 counters on every cell of every
track. Cheapest track; the one that decides H1b.

### 5.2 The distractor ladder — how to avoid measuring "small corpora are easy"

The trap: subsample a labelled corpus, observe that everything gets better, conclude
something about parameters. **This is also precisely why the pilot cannot settle H3.** The
pilot's nesting (§4.1) removes the first-order version of the trap — its relevant set is
constant — but its lever arm is 4× within one order of magnitude and its distractors come
from one topical cluster, so a genuine interaction of the size H3 posits would be
indistinguishable from noise and from cluster-specific idiosyncrasy. The ladder fixes the
lever arm; the three parts below fix the estimator.

**(i) Hold the relevant set constant; vary only the distractors.** At every rung the index
contains *all* judged documents and *all* qrels. Rungs differ only in distractor count.
Queries are identical, so every metric is fully paired across rungs and a
difference-in-differences estimator is available (§7.6).

**(ii) Distractors are real, in-domain, and pre-embedded.** Drawn by a read-only scroll from
the production `ragstack_sfr_tok512` collection — real 4096-d SFR vectors with their
`content` payload (`stores/qdrant.py:198-205` writes `chunk_id`, `doc_id`, `content`,
`start_char`, `end_char` at `:199-203`; `_chunk_from_payload` at `:411-422` reconstructs
them). Same technique the G2 harness uses (`bench_filter_truncation.py:250-293`,
`scroll_vectors`). Consequences:

- **No re-embedding.** The ladder to 1M chunks costs zero GPU time for distractors.
- **No manifold mismatch** — *provided* the distractor collection's build spec matches the
  judged core's. Hard precondition, asserted in the manifest:
  `provenance.spec_hash(model, dim, chunk_descriptor(...))` (`ragstack/provenance.py:51`,
  `:27`) must be **byte-identical** between judged-core index and source collection. If it
  is not, the ladder is invalid and must be rebuilt by embedding distractors directly.
- **Distractors MUST be written to ES as well as Qdrant.** BM25 corpus statistics (document
  frequency, `avgdl`) are the entire reason the ladder exists for the sparse leg. A
  Qdrant-only ladder would hold the BM25 index at L0 while the dense index grows, producing
  a **spurious hybrid-vs-dense interaction**. `bench_filter_truncation.py` is Qdrant-only;
  the ES half is gap 3 (§10).

**(iii) The size effect is a main effect; the decision rests on the interaction.** Report
`M(default, L_i)` for every rung as the easiness curve. It will decrease with size. That is
expected and is *not* evidence for anything. The shipping decision uses only θ(c) (§7.6),
which differences the easiness curve out.

**Rungs.**

| Rung | Total chunks | Distractors added | Real-world analogue |
|---|---|---|---|
| L0 | ≈ 6.1k (A1) / ≈ 4k (A2) | 0 | 170–200-document personal library (= the pilot's P200) |
| L1 | ≈ 36k | +30k | 1,000-document personal library (= Track B's 1,000-doc rung) |
| L2 | ≈ 200k | +194k | large shared / departmental index |
| L3 | ≈ 1.0M | +994k | production-shaped (prod is 3M–25M; L3 is the affordable upper anchor) |

L3 is optional if the schedule slips; **L0/L1/L2 are mandatory**, since L0↔L2 is the
difference-in-differences pair for H3. Distractor sampling is a seeded reservoir sample over
a scroll of the source collection, with the seed and a digest of the sampled point-id
sequence recorded in the manifest so the exact distractor set is reproducible.

**Cost.** Judged-core embedding is small: SciFact `fixed_tok512` ingested 6,108 chunks in
24.1 s across 16 SFR endpoints (`scifact_chunk_eval_report.md`) ≈ 253 chunks/s. NFCorpus is
comparable. Distractors cost zero GPU. Total judged-core embed time across both datasets and
all rungs: **< 2 minutes of fleet time**.

### 5.3 Parameterize absolute depth, not the product

In production, per-leg depth is `max(top_k, rerank_candidates) * candidate_multiplier`
(`query.py:199-204` composed with `retriever.py:53`). `top_k` is therefore **not** a pure
truncation of a fixed ranking — changing it changes what was retrieved. Sweeping `top_k`
and `candidate_multiplier` independently would confound the report cutoff with retrieval
breadth, and would make the rerank on/off comparison a 10× breadth comparison in disguise
(H2c).

Both parts therefore sweep **absolute per-leg depth D directly** and map back to shippable
`(top_k, candidate_multiplier, rerank_candidates)` triples at reporting time. Example
realizations of D = 100: `(top_k=5, mult=2, rerank_candidates=50)` — today's default with
rerank on; `(top_k=5, mult=20, rerank off)`; `(top_k=10, mult=10, rerank off)`. The
deliverable in §11 states the triple, not D.

### 5.4 Full grid, runs executed, and what is derived

| Factor | Levels |
|---|---|
| `N` — index chunk count | L0 ≈ 6k, L1 ≈ 36k, L2 ≈ 200k, L3 ≈ 1M |
| `dataset` | SciFact (A1), NFCorpus (A2) — A2 held out for confirmation |
| `mode` | `hybrid`, `vector`, `bm25` (`retriever.py:47`) |
| `rrf_k` | 1, 10, 20, 60, 120, 240 (hybrid only; 60 ships) |
| `D` — per-leg depth | 10, 20, 50, 100, 200 (absolute) |
| `rerank_enabled` | False, True |
| `C` — `rerank_candidates` | 10, 25, 50, 100, 200, with `C ≤ D` (50 ships) |
| `k` — report cutoff | 1, 3, 5, 10, 20 (derived, free) |

Two properties collapse an apparently 1,800-cell grid into something small.

**(a) The report cutoff k is free.** Store the top-200 ranked chunk ids per query per
first-stage cell; every k ∈ {1,…,20}, every doc-collapse variant, and every metric is
recomputed offline from that file with no store and no GPU.

**(b) `rerank_candidates` is nearly free.** A cross-encoder is a *pointwise* scorer —
`SidecarReranker.score` (`scoring/scorers.py:97-162`, `score` at `:129-162`) produces a
score per (query, chunk) pair independent of the pool. So: rerank the deepest pool once,
cache scores keyed by `(query_id, chunk_id, reranker_model, reranker_revision)`, and derive
every smaller `C` by truncating the first-stage list to C and re-sorting by cached scores.
Zero additional sidecar calls.
*Caveat, and it must be honoured:* in production `C` also raises first-stage depth
(`query.py:201`). The offline derivation is faithful only when D is held fixed and C ≤ D —
which the grid enforces. The production coupling is re-imposed at reporting time (§5.3).

**First-stage retrievals actually run**, per (dataset × rung): `bm25` 5 (one per D; `rrf_k`
is a no-op) + `vector` 5 + `hybrid` 6 `rrf_k` × 5 D = 30 → **40 cells**. Across 2 datasets ×
4 rungs = **320 cells**, each over ~300 queries ≈ 96k query-executions. With query vectors
cached (§5.6) each execution is one Qdrant `query_points` plus one ES `search`.

**Cross-encoder work.** Per (dataset × rung), score the union over cells of each query's
top-200. Heavy overlap across cells makes the union far smaller than worst case; budget
~1,000 unique chunks/query → ~300k pairs per (dataset × rung), ~2.4M total. At
bge-reranker-v2-m3 throughput on the H200 fleet this is a few GPU-hours; it is the dominant
compute cost and is capped by the D ≤ 200 ceiling.

### 5.5 One arm through the real filtered path (confirmatory)

Track A's rungs are unfiltered scratch collections, the clean way to vary size. The shipping
library path instead issues `library_id == L AND tenant_id ∈ (readable ∪ {owner_of(L)})`
(`libraries-spec.md` §4, `scoped` row) against a *shared* index, via `_build_filter`
(`stores/qdrant.py:425-455`).

**Which payload indexes exist is a precondition, not a detail.** Today `_ensure_tenant_index`
(`qdrant.py:176-188`) creates a payload index on `tenant_id` only. `libraries-spec.md` §4
requires `ragstack_lib_v1` — and only that collection — to *additionally* index `library_id`,
and that is not implemented. The filtered arm must therefore run against a collection built
to the spec'd index set, and the actual set present at query time is recorded in the manifest
(`index.payload_indexes`, §8.1). Running it with `library_id` unindexed measures a different
planner path than the one v1 ships, so a filtered-arm result must state which set it had.

One Track-A arm — the shipping default plus the top-2 nominated configurations, at L1 — is
additionally run with the judged core loaded into a `ragstack_lib_v1`-shaped collection
alongside distractors under a *different* `library_id`, and queried through the real filter.
The §6.4 deficit assertion is the pass criterion (per leg, against the filtered `*_matchable`
ceilings — the filtered rung is the case where `matchable < D` is a live possibility for the
dense leg too). Since G2 passed (§2.1) the expected result is agreement; if filtered and
unfiltered results diverge by ≥ δ, the G1 recommendation is conditional and must say so, and
the divergence is escalated as a G2 regression.

### 5.6 Determinism and caching (both parts)

- **Query vectors are embedded once per dataset** and cached to disk
  (`cache/query_vectors/<dataset>.<spec_hash>.npy`). This removes the embedding fleet from
  the variance budget — different endpoints, batch composition, and endpoint availability
  (`chunking_compare_7way.detect_live_endpoints`) otherwise perturb results between cells.
- **Indexes are frozen** during measurement: no concurrent ingest; the Qdrant optimizer
  quiesced and `segments_count` / `indexed_vectors` / `points` recorded before and after each
  rung's sweep. Precedent: the `hnsw_built` coverage banner in
  `bench_filter_truncation.py:913-939` — an unqualified result from a collection whose HNSW
  never built is meaningless.
- **Seeds** for the distractor sample, the query split, and the bootstrap (`_stats.SEED = 0`
  at `_stats.py:34`, `BOOTSTRAP_ITERS = 10_000` at `:33`) are fixed and recorded.
- **The sweep runs in-process against `HybridRetriever`, not over HTTP.** `rrf_k` is baked
  into a module-level singleton at import (`api/routers/query.py:36`) and the multiplier into
  the app-startup retriever (`api/deps.py:274`, `:994`); an HTTP sweep would need a process
  restart per cell. The in-process harness constructs
  `HybridRetriever(vstore, tindex, embedder, rrf_scorer=RRFScorer(k=…), candidate_multiplier=…)`
  directly — the same constructor `deps.py:267-277` uses, so no behaviour is forked.

### 5.7 Why not the alternatives

- **Subsample SciFact to 200 documents.** ~236 chunks. Not a library; also destroys most
  qrels coverage. Rejected (§1.3b).
- **BioASQ as the ladder corpus.** Attractive — real qrels at 14.9M documents would span
  library→production natively. Rejected for v1 on cost: a streaming loader for a
  14.9M-document HF dataset plus ≥ 1M SFR embeddings from scratch. The right *follow-up*
  (§10).
- **Known-item title→doc proxy on real ASM PDFs.** Free, implemented, and genuinely
  full-text PDF. Rejected as primary because it is retrieval-easy by construction — the title
  is verbatim in the lead chunk, giving BM25 an unfair lexical advantage on precisely the
  axis RQ1 measures. Using it would bias H1 toward hybrid. Retained as an optional zero-cost
  sanity arm, never as evidence for or against H1. **The pilot's generated queries are a
  weaker form of the same objection**, not an escape from it — see T1b (§9), which is why
  they are generated from a paraphrase and why query↔source-chunk overlap is a reported
  covariate.
- **Citation-based pseudo-qrels** (query = a citing sentence with the citation removed;
  relevant = the cited paper when present in the library). Objective, free, derivable from
  existing enrichment output (`ingestion/enrich.py` extracts DOIs and citations). Rejected as
  primary because it measures *cited-work retrieval* — a different task from
  question-answering, with a systematically different query distribution. Optional Track-B
  supplement.

---

# Common apparatus (both parts)

## 6. Metrics

Every metric is retained as a **per-query array**, not a mean. This is already the contract
of the stats layer (`python/scripts/eval/_stats.py` module docstring) and of `chunk_one.py`'s
`metrics.json` (`build_metrics_payload`, `chunk_one.py:51`, emitting
`{config, source, n_queries, query_ids, means, per_query}` at `:66-71`).

### 6.1 Primary

**nDCG@10, document-level, graded.** `_stats.ndcg_at_k` (`_stats.py:240`; gain
`2**grade - 1`, discount `log2(rank+1)`, IDCG over the best ordering). Chosen because: it is
the BEIR standard for SciFact and NFCorpus; it handles the graded multi-relevant qrels both
datasets provide; it is the primary metric of the only real-qrels measurement already in this
repository (R5), so new results are directly comparable to a published baseline; and
`_stats.build_stats_table` (`:283`) already tests it.

**Co-primary: nDCG@5 at chunk level.** The deliverable includes a `top_k` and the shipping
default is 5 (`config.py:305`); a metric evaluated only at k=10 cannot recommend a k=5.
Chunk-level (no doc collapse) because it is *chunks* that are packed into the LLM prompt. A
recommended configuration must not be worse than the default on either primary; a split
decision between the two is resolved in favour of nDCG@5 and reported explicitly as a split.

### 6.2 Secondary

- **recall@{10, 20, 100}** (document level; complete-qrels corpora only — see §4.3).
  recall@100 measures the ceiling available to the reranker and is the diagnostic for H2b: a
  rerank gain is only possible where recall@C exceeds recall@k.
- **MRR@10** and **MAP** (`_stats.reciprocal_rank` `:219`, `_stats.average_precision` `:266`).
  MRR is the right summary for the chatbot's single-best-citation behaviour.
- **bpref** and **judged-only condensed-list nDCG** — mandatory wherever qrels are
  pool-derived (§4.3) or where unjudged-relevant intrusion is expected (L2/L3, T3).
- **Context precision@k** — fraction of returned top-k chunks that are relevant, for
  k ∈ {1,3,5,10}. Governs LLM distraction; the bridge between retrieval and answer quality.
  Not a standard IR metric; reported as a diagnostic, never as a decision metric.
- **unique_docs@k** — the doc-collapse ratio. At 36 chunks/doc a depth-100 chunk pool may
  contain only ~20 distinct documents, structurally capping document-level recall. This is
  the single largest expected difference between BEIR (1.18 chunks/doc) and a real library
  (36) and must be reported for every cell.
- **k-curve.** All of the above at k ∈ {1, 3, 5, 10, 20}, read from a single stored ranking
  (§5.4a). Reporting a curve rather than a point makes the `top_k` recommendation legible.

### 6.3 Tertiary — answer-level metrics

**Do answer-level metrics belong?** Partly. Against making them primary: substantially
higher per-query variance and therefore much lower power at confirm-split n ≈ 360–374; they
conflate retrieval with generation, attenuating any retrieval-parameter effect; and scoring them
requires an LLM judge, with the biases §4.4 enumerates. For including them at all: the
deliverable feeds a chatbot, and a retrieval configuration that maximizes nDCG while
degrading answers is the wrong answer.

**Resolution: a non-inferiority confirmation on the top two configurations only**, run after
Track A selects. Never a selection metric, never swept.

- **Groundedness / faithfulness** — proportion of atomic claims in the generated answer
  entailed by the retrieved context. Claim-level, judge-scored.
- **Answer relevance** — does the answer address the query.
- **Citation correctness** — for each `[n]` marker, does source *n* support the adjacent
  claim. `RagGenerator` already produces cited answers (`ragstack/llm.py:95-127`).

**Decision rule:** the recommended configuration must be non-inferior to the current default
on groundedness with margin 0.05 (TOST on the paired proportion). It is not required to be
*better*.

### 6.4 Sanity — the returned-hit-count metric (G2 sibling)

G2 passed (§2.1), but the assertion is retained on every cell of both parts: a pass on the
G2 sweep is not a pass on a G1 cell, and this is the guard that makes a G1 number
interpretable. Every cell records, per query:

| Counter | Definition |
|---|---|
| `dense_hits` | length of the list returned by `QdrantVectorStore.search` at requested depth D |
| `bm25_hits` | length returned by `ElasticsearchTextIndex.search` at depth D |
| `dense_matchable` | **per-leg ceiling**: Qdrant `count` of points passing the same filter (all points, on an unfiltered rung) |
| `bm25_matchable` | **per-leg ceiling**: ES `_count` of the *same* `{"match": {"content": q}}` query under the same filter — i.e. `\|filter-passing ∩ term-matching\|` |
| `dense_starved`, `bm25_starved` | `hits < D` — returned fewer than asked, **for any reason** |
| `dense_deficit`, `bm25_deficit` | `hits < min(D, matchable)` — returned fewer than the leg *could* have |
| `union_depth` | `\|dense ∪ bm25\|` — the actual input cardinality to RRF |
| `overlap` | `\|dense ∩ bm25\|` — how much of the fusion is agreement vs coverage |
| `fused_depth` | `len(RRFScorer.fuse(...))` before truncation |
| `unique_docs@k` | doc-collapse ratio, per k |
| `rerank_pool_occupancy` | `min(C, union_depth) / C` — how full the cross-encoder pool actually was |

**Each leg has its own ceiling, and the two must not be conflated.** An earlier draft of this
section asserted `dense_hits == min(D, N_chunks_matching_filter)` **and**
`bm25_hits == min(D, N_chunks_matching_filter)`. The second half is wrong, for the reason
§1.3c already gives: `ElasticsearchTextIndex.search` returns
`min(size, |filter-passing ∩ term-matching|)`, and `N_chunks_matching_filter` is only the
*first* of those two sets. A query whose terms are scarce in the index legitimately returns
fewer than D hits with nothing broken — the most plausible place for that is P50, the
smallest rung. The ceilings are therefore **probed per leg** as the `*_matchable` counters
above — once per (index, filter) for the dense leg, whose ceiling is query-independent, and
once per (index, query) for BM25, whose ceiling is not. This is the correction PR #210
arrived at independently, from a smoke run that marked a valid cell `INVALID`; this protocol
adopts its form.

**Two rates, and only one of them is a failure.**

| Rate | Definition | Meaning |
|---|---|---|
| `dense_starved_rate`, `bm25_starved_rate` | fraction of queries with `hits < D` | **This is H1b's measurement.** A leg can be starved because the index genuinely holds fewer matching chunks than D. Not a failure; a finding — and the whole point of registering H1b. |
| `dense_deficit_rate`, `bm25_deficit_rate` | fraction of queries with `hits < min(D, matchable)` | The leg returned fewer than it could have. This is truncation, and it is a bug. |

**Pass assertion, evaluated per cell, mirroring G2's criterion
(`bench_filter_truncation.py:154`):** `hits == min(D, matchable)` **per leg** on ≥ 99% of
queries — i.e. `dense_deficit_rate ≤ 1%` and `bm25_deficit_rate ≤ 1%`. A cell failing *this*
is **void**: quality metrics are reported as `INVALID (hit deficit)` and excluded from all
statistical tests, and the failure is escalated as a G2 regression. A non-zero
`*_starved_rate` with a zero `*_deficit_rate` voids nothing and escalates nothing; it is
recorded, and it decides H1b.

Keeping these apart is not bookkeeping. Under the flat assertion, a *correct falsification of
H1b* — BM25 running out of term-matching chunks at P50, exactly the condition the spec's
original premise described — and an Elasticsearch or Qdrant truncation bug are the same
event, and §12's stop rule would halt the study on the former.

`rerank_pool_occupancy` deserves emphasis: it is the direct test of the spec's concern that
"the reranker's candidate pool assumes depth that will not exist." At the shipping default
`rerank_candidates=50` with rerank *off*, occupancy is undefined; with rerank on, effective
first-stage depth is 100/leg (§1.1) and occupancy should be ≈ 1.0. If occupancy is < 1.0 at
P200 or L0, the reranker is fed a partly empty pool and H2 is being tested on a degraded
configuration.

### 6.5 Cost

p50 / p95 end-to-end query latency, per-leg latency, cross-encoder calls per query,
cross-encoder GPU seconds, index build time, index size on disk. A configuration that buys
+0.005 nDCG for 3× latency is not a shipping default. The cost budget is a **pre-registered
constraint**, not a tiebreaker: candidate configurations nominated in §7.2 must have p95
latency ≤ 2× the shipping default's p95 at the same rung.

---

## 7. Statistical treatment

### 7.1 Unit of analysis and existing machinery

The unit is the **query**. Per-query metric arrays are retained for every cell and are the
input to every test. `python/scripts/eval/_stats.py` already provides:

- `bootstrap_metric_ci` (`:62`) — paired bootstrap 95% CI per configuration; the *same*
  resampled query-index matrix is used for all configurations on each iteration, which is
  what makes the intervals comparable.
- `bootstrap_diff_ci` (`:95`) — paired bootstrap CI of `(config − reference)`.
- `wilcoxon_signed_rank` (`:141`) — two-sided, tie- and continuity-corrected normal
  approximation, dependency-free.
- `holm_bonferroni` (`:189`) — step-down FWER control.
- `build_stats_table` (`:283`) — assembles the table and an interpretation line, and already
  emits the "statistically indistinguishable" verdict when nothing survives.

Reused unchanged. §7.5 and §7.6 add what is missing (gap 5, §10).

### 7.2 Query splits, staging, and multiple comparisons

**Split.** Each query set is split **40% tune / 60% confirm**, stratified by per-query
difficulty measured on the shipping-default configuration at the smallest rung (quintiles of
nDCG@10), seed 0, written to a pinned fixture *before* any sweep run. Stratification prevents
the splits from differing in baseline difficulty, which would inflate or deflate every
stage-2 effect.

**Two-stage protocol, applied within each part.**

- *Stage 1 (exploratory)* runs the grid on the **tune** split. Point estimates and CIs only.
  **No significance claims, no recommendation.** Nominates ≤ 5 candidate configurations by
  the rule below. Error criterion: **Benjamini–Hochberg FDR at q = 0.10** — the right
  criterion for a screen, since stage 2 will kill a false lead.
- *Stage 2 (confirmatory)* runs only the nominated ≤ 5 configurations plus the shipping
  default on the **confirm** split. Error criterion: **Holm–Bonferroni at α = 0.05** over
  exactly the pre-registered comparisons (≤ 5 tests per metric per dataset). FWER is the
  right criterion for a ship/no-ship claim.

Holm–Bonferroni over 320 cells would be so conservative only a huge effect could survive;
320 uncorrected tests would guarantee false positives. The split is the answer.

**Nomination rule (pre-registered, applied mechanically to stage-1 output).** The shortlist
is (1) the shipping default, (2) the highest-mean-nDCG@10 cell at the smallest rung
satisfying the §6.5 latency constraint, (3) the highest-mean cell at the largest rung
satisfying the same, (4) the best dense-only cell, (5) the best rerank-on cell at matched
depth. Ties broken by lower p95 latency, then lower D. If (2) and (3) coincide the shortlist
is shorter — itself weak evidence against H3.

**Second corpus (Part II).** NFCorpus is the external check. A recommendation must hold on
both datasets: same sign, and pooled-confirm Δ ≥ δ. A configuration that wins on SciFact and
loses on NFCorpus is not recommended, regardless of pooled significance.

**Pilot cross-check.** The pilot's analogue is the SciFact anchor (§4.1): a pilot
recommendation must hold in sign on both the LLM-judged libraries and the anchor.

**Metric multiplicity.** Holm is applied within each primary metric family separately, and a
recommendation requires the *same* configuration to clear the bar on nDCG@10 and to be
non-inferior on nDCG@5. Secondary metrics are reported with CIs and never used to declare
significance.

**Replication.** With frozen indexes and cached query vectors, retrieval is deterministic
except for HNSW nondeterminism under concurrent optimizer activity — which
`libraries-spec.md` §16 documents explicitly as a reason to cache query vectors and raise
count timeouts. Therefore: **10 replicates of one designated reference cell per rung** (an
A/A null), not replicates of everything. Report SD of nDCG@10 across replicates and
rank-biased overlap (RBO) of the top-20 lists.

**The gate uses the upper confidence limit on the SD, not the point estimate**, and the
replicate count follows from that. An SD from `r` replicates has `r − 1` degrees of freedom
and its one-sided 95% upper limit is `s × √((r−1)/χ²_{0.05, r−1})`: at r = 3 that factor is
**4.4**, which makes a point-estimate gate close to meaningless — a true SD four times the
observed one is entirely consistent with three replicates. At r = 5 the factor is 2.4; at
r = 10 it is **1.65**. A/A replicates are pure retrieval against a frozen index with cached
query vectors — no judging, no GPU — so 10 costs minutes and buys a gate that means
something. **δ must exceed 3× the *upper 95% limit* of the A/A SD**; if it does not, the
experiment is under-resolved and §7.4's pre-registered response applies before any claim is
made.

### 7.3 Confidence intervals

Every reported number is `point [lo, hi]` from the paired bootstrap, 10,000 iterations, seed
0 — the existing `CI.fmt()` convention (`_stats.py:45`). Difference CIs against the reference
configuration are reported for the primary metrics. **Bare means without an interval are not
acceptable output from this protocol.**

### 7.4 Minimum effect size worth acting on

**δ = 0.02 absolute nDCG@10.** Justification, from measurements already in this repository:

1. *Resolvability — an open question, not a settled one.* R5's paired-bootstrap difference
   CIs at n = 300 had half-widths 0.005–0.023 (`fixed_tok256`: ΔnDCG@10 +0.023
   [+0.006, +0.040], half-width 0.017). A 95% half-width of `h` corresponds to an 80%-power
   MDE of `h × (1 + z_β/z_{α/2}) = h × 1.43`, and MDE scales as `1/√n`. Three corrections to
   the naive version of this arithmetic, all of which cut the same way:

   - **Every significance claim happens on the confirm split, not the full query set** (§7.2).
     The confirm split is 60%: SciFact 180 + NFCorpus 194 = **n = 374**, not 623. At n = 374,
     `0.017 × 1.43 × √(300/374)` ≈ **0.022 — already above δ**.
   - **Holm inflates it further.** Stage 2 runs ≤ 5 pre-registered tests per metric per
     dataset, so the operative two-sided level is 0.01 (`z = 2.576`, against 1.96). MDE scales
     as `(z_{α/2} + z_β)`, i.e. `(2.576 + 0.84)/(1.96 + 0.84) = 1.22`. Holm-adjusted confirm
     MDE ≈ **0.027 ≈ 1.35 δ**.
   - **The 0.017 half-width is the wrong prior for G1's contrasts.** It comes from
     *chunker-vs-chunker* differences on an identical query set, identical retriever and
     identical index — near-maximally paired, so the difference SE is small. G1's headline
     contrasts (hybrid vs dense-only, D = 10 vs D = 100) change *which documents come back*,
     decorrelating the paired scores and inflating the difference SE. R5's own largest
     half-width, 0.0225, already gives MDE ≈ 0.032 at n = 300 — and G1's contrasts should be
     expected at or beyond that end of the range, not at the 0.017 end.

   So: **the claim that this design resolves δ = 0.02 is withdrawn.** Under the most
   favourable in-repo prior the pooled confirm split resolves ≈ 0.027 (1.35 δ); under the
   least favourable, ≈ 0.035 (1.75 δ). **Whether δ is resolvable at all is an open question
   the pilot settles empirically**, from the A/A noise floor and the realized per-query
   difference SDs on the actual G1 contrasts (§7.2 replication, §4.6 item 3, §12 step 4).
   Pooling both datasets remains mandatory in Part II — it is worth a factor of `√(374/180)`
   = 1.44 on the MDE — but it is no longer claimed to be *sufficient*.

   **Pre-registered response if realized power is worse than the design values.** Trigger:
   realized Holm-adjusted confirm MDE > 1.5 δ, or realized 90% TOST half-width > δ. Responses,
   in order of preference: (i) enlarge the confirm split — for the pilot this means generating
   and judging more queries, the cheapest lever because the cost is judge compute (§4.2);
   (ii) shrink the pre-registered test family from 5 to 3, which buys `(2.394+0.84)/(2.576+0.84)`
   = 5% on the MDE; (iii) raise δ by amendment, with the raised value justified against the
   §1.2 effect size. Until one is applied and recorded as an amendment, near-δ effects are
   reported INCONCLUSIVE — a correct outcome, not a failure. Note (ii) is a small lever and
   (iii) weakens the deliverable, which is why the query count is set generously up front.

   *Corollary for the pilot.* The pilot uses **600 queries per rung** (§4.2), giving a confirm
   split of **n = 360** — deliberately matched to Part II's 374 so the two parts have
   comparable power. Holm-adjusted confirm MDE ≈ **0.027**; the 90% TOST half-width is
   ≈ **0.013** (optimistic prior) to ≈ **0.017** (pessimistic), both inside δ, so
   **EQUIVALENT is reachable and DIFFERENT is reachable only for effects ≳ 1.35 δ**. §4.6
   states what that does and does not buy. At the 150 queries an earlier draft specified, the
   confirm split would have been n = 90, MDE ≈ **0.044** and the 90% half-width ≈ **0.026** —
   *both* verdicts out of reach, i.e. every quality comparison INCONCLUSIVE by construction.
   That is why the query set is 600.
2. *Below the effect the experiment exists to find.* The uncontrolled retrieval-breadth change
   in §1.2 moved nDCG@10 by ≈ 0.046 — more than 2δ. If the G1 axes matter at all, they matter
   at a scale this experiment can see.
3. *Above the noise that has previously tempted a conclusion.* The largest chunker effect
   observed here was +0.023 and did not survive Holm. δ = 0.02 puts the threshold right at
   that boundary, which is why the decision rule requires **both** `|Δ| ≥ δ` **and**
   Holm-adjusted `p < 0.05` — magnitude alone is not enough, and neither is significance
   alone.
4. *Bounded below by measured noise.* δ must exceed 3× the **upper 95% confidence limit** of
   the A/A replicate SD (§7.2), not 3× its point estimate. If that limit exceeds 0.0067, δ is
   raised accordingly and the change is recorded as an amendment.

For nDCG@5 the same δ = 0.02 applies. For the answer-level non-inferiority check the margin
is 0.05 on a proportion.

### 7.5 What counts as "no difference"

Failing to reject a null is not a finding. This protocol can conclude equivalence, using
**TOST (two one-sided tests)** with equivalence margin δ: two configurations are *practically
equivalent* on a metric when the **90% paired-bootstrap CI of their difference lies entirely
within (−δ, +δ)**. (A 90% CI is the correct interval for a 5% one-sided TOST pair.)

Every comparison in every report carries one of three labels:

| Label | Condition | Meaning |
|---|---|---|
| **DIFFERENT** | `\|Δ\| ≥ δ` and Holm `p < 0.05` | act on it |
| **EQUIVALENT** | 90% CI ⊂ (−δ, +δ) | genuinely no practical difference — prefer the simpler/cheaper configuration |
| **INCONCLUSIVE** | neither | underpowered; report as such, do not ship on it |

"Everything is EQUIVALENT" is a legitimate and useful result: the shipping defaults survive
contact with library scale, no size branch is needed, and the config surface stays flat.
§11.3 gives that deliverable. `_stats.build_stats_table` currently emits only
DIFFERENT/not-DIFFERENT; adding TOST is gap 5 (§10).

### 7.6 The size-branch test is a difference-in-differences

H3 is an interaction and must be tested as one. Because the query set is identical at every
rung, per-query scores are paired *across rungs* as well as across configurations, so

  θ(c) = [M(c, L0) − M(default, L0)] − [M(c, L2) − M(default, L2)]

is computed per query and bootstrapped with the same resampled index matrix used everywhere
else. A size-dependent branch is recommended only when `|θ(c)| ≥ δ` **and** the 95% CI of
θ(c) excludes 0, for a configuration that has already cleared stage 2 at one of the two
rungs.

The same estimator is computed on the pilot over (P50, P200) with the `core` qrel set (§4.3),
which is paired by construction. It is reported as an **exploratory** quantity: over a 4×
span with single-cluster distractors it is expected to be indistinguishable from 0, and a
non-zero pilot θ is a reason to prioritize Part II, never a reason to branch the config
surface.

The easiness main effect is reported once, as a curve: `M(default, L_i)` versus
`log10(N_chunks)`, with CIs. It is context, not evidence.

### 7.7 Pre-specified analyses and the honest-reporting rule

Fixed before any run: the grids (§4.5, §5.4), the primary and co-primary metrics (§6.1), the
splits (§7.2), the nomination rule (§7.2), δ (§7.4), the equivalence procedure (§7.5), and
the DiD estimator (§7.6). Any analysis not listed here is **exploratory** and must be
labelled as such in the report; exploratory findings may motivate a follow-up experiment but
may not enter the `LibraryRetrievalDefaults` block.

---

## 8. Provenance and reproducibility

Treated as a deliverable, not as documentation. The standard is: **a third party with access
to the datasets and the hardware can reproduce every number from the artefacts alone.**

### 8.1 What must be captured

| Category | Fields |
|---|---|
| Protocol | SHA-256 of this file; amendment list |
| Code | git commit, branch, dirty flag, `provenance.ragstack_version()` (`provenance.py:39`) |
| Dataset | name, source string (`"hf:BeIR/scifact"` — the harness reports which of its three loaders won), HF revision, SHA-256 of corpus / queries / qrels, counts (`n_docs`, `n_queries`, `n_judgments`) |
| Query set | fixture path under `contracts/fixtures/queries/`, SHA-256, split label, split seed. Precedent: `contracts/fixtures/queries/test_queries.json`, and `libraries-spec.md` §16 already requires a pinned query fixture for the Tier-0 gate |
| Embedding | model, dim, revision/digest, `embedding_api`, `embedding_endpoints` (the live set, not the candidate set), batch size, token counter model, hard cap |
| Chunker | `chunk_method`, `chunk_size`, `chunk_overlap`, `chunk_params`, and `chunk_descriptor(...)` (`provenance.py:27`) |
| Build identity | `spec_hash(model, dim, chunk)` (`provenance.py:51`) for the judged core **and** the distractor source collection, with an equality assertion |
| Index config | Qdrant: `m`, `ef_construct`, server-side search `ef`, `full_scan_threshold`, `max_segment_size`, `indexing_threshold`, `on_disk_payload`, `segments_count`, `points`, `indexed_vectors`, `hnsw_coverage`, **`payload_indexes`** (the field names actually indexed — §5.5). ES: version, index name, similarity, analyzer, `n_docs`, `avgdl`. `segments_count` on the pilot rungs is the first library-sized observation of it and settles the `libraries-spec.md` §4 segment-boundary question (§1.3a) |
| Distractors | source collection, sample seed, count, digest of the sampled point-id sequence |
| Judging (pilot / Track B) | judge model + revision, temperature, seed, rubric hash, pool depth, label histogram, κ(human–human), κ(judge–human), self-consistency rate |
| Query generation (pilot / Track B) | generator model + revision, **paraphrase-pass prompt hash and query-pass prompt hash** (T1b, §9), temperature, seed, accept/discard counts by filter reason, and the per-query IDF-weighted query↔source-chunk overlap distribution |
| Parameters | `mode`, `rrf_k`, `D`, `top_k`, `candidate_multiplier`, `rerank_enabled`, `rerank_candidates`, reranker model + revision, `use_graph=false`, `rewrite="passthrough"` |
| Software | Python, `qdrant-client`, `elasticsearch`, `httpx`, `numpy`, `transformers`/`tokenizers`, Qdrant server, ES server, sidecar image digests |
| Hardware | host, GPU model and count, which endpoints served the run, concurrency settings (`EMBED_CONCURRENCY`, `EVAL_CONCURRENCY`) |
| Seeds | distractor sample, query split, bootstrap (`_stats.SEED`), judge seed |
| Invocation | the exact `argv`, verbatim |
| Results | `query_ids`, per-query arrays for every metric, means, Track-C counters, cost |

Two of these are currently unobtainable from a running server and are worth fixing:

- `GET /v1/admin/config` (`api/routers/admin.py:47-90`, route at `:93`) exposes `top_k`
  (`:78`), `rerank_enabled` (`:79`), `rerank_candidates` (`:80`) but **not** `rrf_k` and
  **not** `retrieval_candidate_multiplier` — a deployed instance cannot self-report the two
  constants this experiment is about.
- `settings.top_k` (`config.py:305`) is a **phantom knob**. There is no reader in the
  retrieval path; the API default is the literal `5` at `query.py:72` and `:107`, and the
  effective value always comes from `request.top_k` (`query.py:351`, `:432`). The only thing
  that touches `settings.top_k` at all is the reflective loop in `admin.py:98-99`
  (`getattr(settings, name)` over `ConfigResponse.model_fields`), which reports it without
  anything consuming it.

Both are recorded in §10 (gap 9) and §11.4, and the second is now noted in the spec's G1
section, because it determines whether the deliverable is even expressible as configuration.

### 8.2 Manifest schema

Reuse the `provenance.py` vocabulary rather than inventing a parallel one. `CollectionManifest`
already carries `collection`, `model`, `dim`, `embedding_api`, `embedding_endpoints`,
`chunk_method`, `chunk_size`, `chunk_overlap`, `chunk_params`, `spec_hash`, `corpus`,
`chunk_count`, `ingested_at`, `ragstack_version`, `source`. An `EvalRunManifest` **embeds** a
`CollectionManifest` verbatim and adds the eval-specific sections.

```jsonc
{
  "schema_version": "ragstack.eval_run/v1",
  "run_id": "g1-a1-L0-hybrid-rrf60-d100-rr0-0007",
  "protocol_version": "sha256:…",          // SHA-256 of docs/g1-retrieval-protocol.md
  "part": "pilot" | "full",
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
             "hnsw_coverage": 1.0,
             "payload_indexes": ["tenant_id"]},   // spec'd for ragstack_lib_v1:
                                                  //   ["tenant_id", "library_id"] (§5.5)
    "es": {"version": "8.x", "index": "g1_a1_l0", "similarity": "BM25",
           "analyzer": "standard", "n_docs": 6108, "avgdl": 0.0}
  },

  "distractors": {"source_collection": "ragstack_sfr_tok512",
                  "source_spec_hash": "…", "spec_hash_match": true,
                  "n_chunks": 0, "sample_seed": 0, "point_id_digest": "…"},

  "judging": {"applies": false, "judge_model": null, "judge_revision": null,
              "temperature": 0, "seed": 0, "rubric_sha256": null, "pool_depth": null,
              "label_histogram": null, "kappa_human_human": null,
              "kappa_judge_human": null, "self_consistency": null},

  "query_generation": {"applies": false,          // pilot / Track B only (T1b, §9)
                       "generator_model": null, "generator_revision": null,
                       "paraphrase_prompt_sha256": null, "query_prompt_sha256": null,
                       "temperature": 0, "seed": 0,
                       "n_generated": 0, "discarded": {"title_answerable": 0,
                                                       "names_document": 0,
                                                       "implausible": 0},
                       "idf_overlap": {"mean": null, "p50": null, "p90": null,
                                       "tertile_edges": null}},

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

  "sanity": {"dense_starved_rate": 0.0, "bm25_starved_rate": 0.0,   // H1b; not a failure
             "dense_deficit_rate": 0.0, "bm25_deficit_rate": 0.0,   // voids the cell
             "assertion": "hits_leg == min(D, matchable_leg), per leg (§6.4)",
             "verdict": "PASS"},

  "results": {"n_queries": 180, "query_ids": ["…"],
              "means": {"ndcg@10": 0.0, "ndcg@5_chunk": 0.0, "recall@10": 0.0,
                        "recall@20": 0.0, "recall@100": 0.0, "map": 0.0, "mrr@10": 0.0,
                        "bpref": 0.0},
              "per_query": {"ndcg@10": [], "map": [], "recall@10": []},
              "counters": {"union_depth": [], "overlap": [], "unique_docs@10": [],
                           "dense_hits": [], "bm25_hits": [],
                           "dense_matchable": [], "bm25_matchable": [],
                           "rerank_pool_occupancy": []}},

  "cost": {"wall_s": 0.0, "p50_query_ms": 0.0, "p95_query_ms": 0.0,
           "crossencoder_pairs": 0, "gpu_s": 0.0}
}
```

`results.per_query` deliberately matches the existing `chunk_one.py` contract
(`{config, source, n_queries, query_ids, means, per_query}`, `chunk_one.py:66-71`) so that
`scripts/eval/aggregate_stats.py` — which already validates query-id alignment across files
before pairing them (`aggregate_stats.py:82-94`) — works on G1 output with minimal change.

### 8.3 On-disk layout

```
reports/g1-library-retrieval/
  PROTOCOL.md -> ../../docs/g1-retrieval-protocol.md   # its sha256 is protocol_version
  AMENDMENTS.md
  fixtures/                                  # copied into contracts/fixtures/queries/
    g1_pilot_{p50,p100,p200}_queries.json
    g1_scifact_{tune,confirm}.json
    g1_nfcorpus_{tune,confirm}.json
    g1_trackb_clustered1000_queries.json
  qrels/
    scifact/{corpus,queries,qrels}.sha256
    nfcorpus/{corpus,queries,qrels}.sha256
    pilot/judgments.jsonl                    # (query_id, doc_id, grade, judge, ts)
    pilot/agreement.json                     # kappa, label histogram, self-consistency
  manifests/<run_id>.json                    # EvalRunManifest, one per cell
  raw/<run_id>/
    per_query.json                           # chunk_one-compatible
    counters.jsonl                           # Track C, one row per query
    rankings.jsonl.zst                       # top-200 chunk ids per query  <-- the key artefact
  cache/
    query_vectors/<dataset>.<spec_hash>.npy
    crossencoder/<dataset>.<index_id>.<reranker_rev>.jsonl
  analysis/
    00_sanity.md          # §6.4 assertions, hnsw coverage, A/A noise floor
    01_mechanism.md       # Track C: H1b, H2c, leg depths, overlap, doc collapse
    05_pilot.md           # Part I: 50/100/200, judge agreement, scoped recommendation
    10_stage1_screen.md   # tune split, BH-FDR, shortlist
    20_stage2_confirm.md  # confirm split, Holm, CIs, DIFFERENT/EQUIVALENT/INCONCLUSIVE
    30_interaction.md     # theta(c), easiness curve, crossover
    40_trackb.md          # real-PDF veto check at 1,000 docs, bpref, kappa
    50_answer_level.md    # non-inferiority on top-2
  LibraryRetrievalDefaults.md                # the normative deliverable
```

**`rankings.jsonl.zst` is the highest-value artefact.** With the top-200 chunk ids per query
per cell stored, every metric, every k, every doc-collapse rule, and every re-analysis after
a reviewer objection is recomputable with no GPU, no store, and no network — exactly the
property that makes `aggregate_stats.py` runnable anywhere today.

### 8.4 Regression use

Once recorded, the confirm-split per-query arrays for the shipping default at P200 and L1
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
leg specifically, relative to BM25, which is exactly the axis of RQ1/H1. "Dense-only suffices
at library scale" is therefore the single most contamination-vulnerable output of this
experiment.
*Mitigations:* (a) that conclusion requires clearing the pilot and Track B, whose ASM PDFs
are not in any public training mixture; (b) report dense-only and BM25-only arms separately
so leg contributions are visible rather than hidden inside a fusion score; (c) state the bias
direction in the report.
*Residual risk: high* for Part II alone; **reduced by staging** — the pilot's corpora are
uncontaminated, so a pilot/Part-II disagreement on H1 is itself the contamination signal.

**T1b — Query-generation contamination, and the sparse leg's home advantage.** *The mirror of
T1, and it points the other way.* The pilot's queries are LLM-generated **from the target
chunk itself** (§4.2), so the query and the chunk it is supposed to retrieve share vocabulary
by construction — entities, gene and strain names, assay terms, numbers. That is exactly the
lexical bias §5.7 invokes to disqualify the known-item title→doc proxy as primary evidence
("retrieval-easy by construction — the title is verbatim in the lead chunk, giving BM25 an
unfair lexical advantage on precisely the axis RQ1 measures"), and the objection does not
weaken merely because the source is a body chunk rather than a title. Like T1 it is
*directional*, but toward the **sparse** leg: it inflates BM25-only and hybrid relative to
dense-only, on RQ1/H1 and H1c — the same axis T1 biases the opposite way. It also feeds H1b:
term-rich generated queries make BM25 starvation *less* likely than real user queries would,
so a clean H1b pass is a weaker guarantee than it looks.
*Mitigations, all pre-registered:*
(a) **Generate from a paraphrase, not the chunk.** The generator's default mode is two-pass —
an abstractive summary of the source chunk is produced first and the query is written from
the summary, with the verbatim chunk never in the query-writing context. The generator model,
both prompts and their hashes go in the manifest (§8.1, judging row).
(b) **Measure the overlap and report it as a covariate.** For every pilot query record the
**IDF-weighted term overlap between the query and its source chunk** (IDF from the P200
index, so it is comparable across rungs), plus the unweighted Jaccard. Report the
distribution; it goes in `05_pilot.md` and in the `LibraryRetrievalDefaults` evidence block
as part of the evidence base.
(c) **Sensitivity analysis on the leg contrasts.** Recompute the hybrid-vs-dense and
BM25-only contrasts on the lowest-overlap tertile, and report the interaction between overlap
and (hybrid − dense). A leg conclusion that only holds in the high-overlap tertile is
reported as an artefact of generation, not as a result.
(d) **T1 and T1b bracket each other.** The SciFact anchor's claims are human-written against
real qrels and carry T1 but not T1b; the pilot libraries carry T1b but not T1. A leg
conclusion that survives both is credible; the §7.2 pilot cross-check ("must hold in sign on
both") is what enforces it, and a sign disagreement on H1 between the two is now interpretable
rather than merely alarming.
*Residual risk: medium* for the leg-comparison outputs (H1, H1c), *low* for the
depth/parameter outputs (H1b's counters, H2c, H4), which do not turn on lexical match.

**T2 — Distribution shift: chunks-per-document and the doc-collapse step.**
SciFact is 1.18 chunks/doc and NFCorpus is comparable; a real library at `fixed_tok512` is
≈ 36. This changes three things at once: BM25 length normalization and `avgdl`; how many
*distinct documents* a depth-D chunk pool can contain (`unique_docs@k` measures exactly
this); and how much a document benefits from having many chunks compete for the same rank
positions. A `top_k` or depth recommendation tuned where 100 chunks are 100 documents may be
wrong where 100 chunks are 20 documents.
*Mitigations:* **the pilot has the correct ratio by construction — this is the main reason it
runs first**; the ladder is built on chunk count, not document count (§1.3b); `unique_docs@k`
is reported for every cell; distractor shells are real ASM chunks, so at L1+ the index-level
chunk-length and `avgdl` statistics are library-realistic even though the judged core is not.
*Residual risk: medium* (was high before staging).

**T3 — Unjudged-relevant distractors confound the size ladder.**
Distractors are ASM biomedical chunks; SciFact claims are biomedical. Some distractors will
genuinely answer some claims and be scored irrelevant for want of a qrel. This depresses
measured quality at higher rungs *for reasons unrelated to corpus size*, inflating the
apparent easiness gradient and — worse — potentially producing a spurious interaction if
configurations that retrieve more broadly surface more unjudged relevant material.
*Mitigations:* (a) quantify it — LLM-judge a random sample of top-10 distractor intrusions at
L2 and report the estimated unjudged-relevant rate; (b) prefer bpref and condensed-list nDCG
at L2/L3; (c) sensitivity analysis: recompute θ(c) after removing queries whose L2 top-10
contains an intrusion judged relevant; (d) the pilot's `core`-vs-`pooled` qrel divergence
(§4.3) is a direct measurement of the same phenomenon at small scale. Out-of-domain
distractors were rejected: they make the haystack artificially easy and destroy the realism
the ladder exists to provide.
*Residual risk: medium.* Quantifiable, and it biases mainly the main effect, which the DiD
estimator differences out.

**T4 — Single-host measurement noise and HNSW nondeterminism.**
Measurements run on `coconut`, which concurrently hosts the shared 8×H200 fleet, GoWe, and
two live production API servers. Qdrant HNSW results are nondeterministic under concurrent
optimizer activity — `libraries-spec.md` §16 says so explicitly and prescribes cached query
vectors for that reason. Endpoint availability varies
(`chunking_compare_7way.detect_live_endpoints`); R1–R5 ran with 4, 8, and 16 endpoints.
*Mitigations:* query vectors cached once per dataset; indexes frozen with segment/coverage
telemetry per rung; scratch collections on a dedicated Qdrant instance, never prod; the A/A
replicate null quantifies the residual noise floor and δ must exceed 3× it; latency is
reported as *ranges* and never used as a primary decision input on a shared host.
*Residual risk: low for quality metrics, medium for latency.*

**T5 — LLM-judge bias.** Enumerated with mitigations in §4.4. Structurally contained in Part
II by the veto-only rule. **In Part I the judge is load-bearing**, which is why the κ floor
(§4.4) and the SciFact anchor (§4.1) are both mandatory and why the pilot's recommendation is
explicitly scoped to 50–200 documents.
*Residual risk: medium in Part I, low in Part II.*

**T6 — Overfitting to the grid.** 320 cells on 300 queries invites selection on noise; the
pilot's 60 cells on 600 queries invite it too — the tune split is 240 queries against 60
cells.
*Mitigations:* two-stage tune/confirm with a pre-registered nomination rule, the
cross-corpus consistency requirement, and the exploratory-labelling rule (§7.7).
*Residual risk: low if the protocol is followed; the risk is procedural, not statistical.*

**T7 — Filtered production path vs unfiltered measurement.** `_build_filter`
(`stores/qdrant.py:425-455`) ANDs conditions into `Filter(must=[...])` (`:455`), and an empty
list is deliberately unsatisfiable (`:430-434`) — a scope mistake returns zero rows silently
rather than raising. A filtered HNSW search can in principle return fewer than `limit`. The
index set matters and is currently short: `_ensure_tenant_index` (`qdrant.py:176-188`) builds
a payload index on `tenant_id` only, while `libraries-spec.md` §4 requires `ragstack_lib_v1`
to index `library_id` as well — unimplemented, and a different planner path (§5.5).
*Mitigations:* G2 has now measured this shape and passed (§2.1); §6.4's per-leg deficit
assertion runs on every cell; §5.5 runs the filtered arm and records
`index.payload_indexes` so a result cannot be silently read as covering the spec'd
configuration when it did not.
*Residual risk: **low*** (downgraded from medium on the G2 result).

**T8 — Chunk-length confounds within the reranker.** Cross-encoder scores correlate with
passage length, and judged cores (short abstracts) and distractor shells (full-text chunks)
have different length distributions, so at L1+ a length-biased reranker could systematically
prefer one population. *Mitigation:* report the chunk-length distribution of the reranked
top-k, split by judged-core vs distractor origin, at every rung. The pilot is immune — every
document is a real full-text PDF.

**T9 — Generalization beyond BV-BRC.** All real-PDF evidence comes from one corpus in one
subdomain, and the pilot's clustered libraries from a single topical cluster within it.
*Mitigations:* the P200-random contrast (§4.1) bounds the cluster-specific component of the
mechanism results. The `LibraryRetrievalDefaults` block must state its evidence base rather
than claiming generality.

---

## 10. What this repository does not have yet

Honest gap list with sizing. The per-gap numbers below are **build effort only**; run time and
analysis are counted separately so the totals add up.

| Roll-up | Gaps | Build | + run/analysis | Total |
|---|---|---|---|---|
| **Part I — pilot** | 1 (1.5) + 2 (0.5) + 5 (0.5) + 6 (0.5) + 7p (2) + 9 (0.25) | **5.25 d** | ≈ 1 d | **≈ 6 d** + judge compute |
| **Part II — full study** | 3 (0.5) + 4 (0.5) + 7f (1) | **2 d** | ≈ 1.5 d | **≈ 3.5 d** + a few GPU-hours |
| Conditional on H3 | 8 | 1–1.5 d | — | 1–1.5 d |

These match §0's cost row. Judge compute (§4.2: ≈ 25k–50k pairs at temperature 0) and
cross-encoder GPU time (§5.4) are fleet hours, not developer time, and are excluded from all
three totals.

| # | Gap | Why it is needed | Part | Size |
|---|---|---|---|---|
| 1 | **`python/scripts/eval/retrieval_sweep.py`** — the sweep driver: parameterized `mode`/`rrf_k`/`D`/`C`, query-vector cache, cross-encoder score cache, `rankings.jsonl.zst` dump, manifest emission. Must run **in-process** (§5.6). Emits `chunk_one`-compatible `per_query.json` so `aggregate_stats.py` works. Nothing today varies any retrieval parameter; `chunk_one.py` sweeps chunkers only and `scifact_chunk_eval.evaluate_config` hardcodes `HybridRetriever(vstore, tindex, embedder)` at `:313`. | I + II | ~450 LOC, **1.5 d** |
| 2 | **Per-leg instrumentation, including the per-leg ceiling probe.** `retrieve()` returns only the fused list and `RRFScorer.fuse` overwrites `retrieval_method="hybrid"` (`scorers.py:43`), erasing provenance. Recommend an **eval-only subclass** returning `(fused, LegStats)` rather than touching the production class. Must also probe `dense_matchable` (filtered Qdrant `count`) and `bm25_matchable` (ES `_count` of the same match query) and keep `*_starved_rate` and `*_deficit_rate` apart (§6.4). PR #210 has a working implementation of exactly this. Track C / §6.4 / H1b / H2c are unmeasurable without it. | I + II | ~90 LOC, **0.5 d** |
| 3 | **Distractor-shell builder.** Scroll prod `ragstack_sfr_tok512` → scratch Qdrant *and* ES. `bench_filter_truncation.py:250-293` (`scroll_vectors`) is the template but is **Qdrant-only**; the ES half is new and non-optional (§5.2 ii). Prefix-guarded teardown modelled on `bench_filter_truncation.guard_scratch:82-89` and `scifact_chunk_eval.teardown`. | II | ~200 LOC, **0.5 d** |
| 4 | **NFCorpus loader**, mirroring `scifact_chunk_eval._load_via_datasets` (graded 0–2 qrels; `_stats.ndcg_at_k` already supports graded gains). Second dataset is mandatory for the MDE budget (§7.4) and for generalization. | II | ~80 LOC, **0.5 d** |
| 5 | **`_stats.py` additions:** TOST equivalence, Benjamini–Hochberg, MDE/power helper (Holm-aware, computed on the confirm split — §7.4), Clopper–Pearson intervals for the §6.4 rates, the χ²-based upper limit on the A/A SD (§7.2), DiD bootstrap, RBO, bpref, condensed-list nDCG. `build_stats_table` extended to emit DIFFERENT / EQUIVALENT / INCONCLUSIVE. | I + II | ~200 LOC, **0.5 d** |
| 6 | **`EvalRunManifest`** in `ragstack/provenance.py` (or `ragstack/eval/manifest.py`), embedding `CollectionManifest` and reusing `spec_hash` / `chunk_descriptor` / `ragstack_version`. | I + II | ~120 LOC, **0.5 d** |
| 7p | **Pilot library + judging harness:** nested clustered/random library sampling at 50/100/200 (P200-random = `P50 ∪ 150 uniform`, §4.1), two-pass paraphrase-then-query LLM generation at 600/rung with the filters and the IDF-overlap covariate (T1b, §9), fixed-depth pooling, judging loop, κ, self-consistency. | I | ~550 LOC + judge compute, **2 d** |
| 7f | **Track-B extension to 1,000 documents**, reusing 7p. | II | **1 d** |
| 8 | **Conditional — config surface,** only if H3 holds: per-collection retrieval overrides. There is **no size-dependent or per-collection retrieval branch anywhere today** — `CollectionSpec` (`api/collections.py:29-54`) carries no retrieval fields, and both retriever construction sites (`deps.py:267-277`, `deps.py:988-997`) read the same global `settings`. Natural seams: `CollectionSpec` + `_hybrid_retriever`. | II | **1–1.5 d** |
| 9 | **Reporting-surface fixes** (small, worth doing regardless): expose `rrf_k` and `retrieval_candidate_multiplier` in `GET /v1/admin/config` (`admin.py:47-90` omits both); resolve the phantom `settings.top_k` (`config.py:305` has no reader; the API default is the literal `5` at `query.py:72`/`:107`). Without these a deployed server cannot report, and a settings file cannot set, the parameters this experiment tunes. | I | ~40 LOC, **0.25 d** |
| — | **Deferred: BioASQ.** The right long-term ladder corpus (real qrels at 14.9M docs). Needs a streaming loader and ≥ 1M SFR embeddings. `ROADMAP.md:81`, issue #56. | follow-up | ~3 d |

---

## 11. The deliverable

### 11.1 Form

A normative block in `docs/libraries-spec.md`, replacing the G1 paragraph in §-1:

```yaml
LibraryRetrievalDefaults:            # NORMATIVE. Applies to `scoped` libraries (§4).
  evidence_scope:                    # what was actually measured
    documents: "50-1000"
    index_chunk_count: "1.8k-1M"
  applies_when:
    index_chunk_count: "<= N*"       # measured crossover; OMIT the branch entirely if H3 falsified
  retrieval_mode: hybrid | vector
  top_k: <int>
  retrieval_candidate_multiplier: <int>
  rrf_k: <int>
  rerank_enabled: <bool>
  rerank_candidates: <int | null>
  evidence:
    protocol: docs/g1-retrieval-protocol.md@<sha256>
    part: pilot | full
    datasets: [asm-pilot-{50,100,200}, scifact@<rev>, nfcorpus@<rev>]
    primary_metric: ndcg@10
    delta_vs_current_default: "<point> [<lo>, <hi>]  (paired bootstrap, n=<n>)"
    holm_p: <float>
    realized_mde: <float>            # confirm-split, Holm-adjusted (§7.4)
    verdict: DIFFERENT | EQUIVALENT | INCONCLUSIVE
    judge_agreement: "kappa(judge-human)=<float>"
    query_source_idf_overlap: "<mean> (p50 <x>, p90 <y>)"   # T1b covariate, §9
    track_b_veto: passed | not_applicable | blocked
    manifests: reports/g1-library-retrieval/manifests/
  measured_corpus_facts:             # replaces the corrected estimates in §-1 and §4
    chunks_per_document_tok512: <float>
    chunks_200_docs: <int>
    chunks_1000_docs: <int>
    segments_count_by_rung: {<chunks>: <int>, …}   # settles the §4 handoff question (§1.3a)
```

**Staging rule for the block.** The pilot may publish this block with
`evidence.part: pilot`, `evidence_scope.documents: "50-200"`, and **no** `applies_when`
guard — a scoped recommendation, explicitly not a size branch. Only Part II may add an
`applies_when` guard, and only under §11.2.

### 11.2 If H3 holds (a size branch is justified)

The block carries an `applies_when.index_chunk_count <= N*` guard, and gap 8 (§10) becomes
required work: `CollectionSpec` gains optional retrieval overrides, `_hybrid_retriever`
(`deps.py:267-277`) reads them, and the library registration path sets them from the
library's chunk count. The spec must also state the behaviour when a library *crosses* N*
through ingestion — defaults are read at retriever construction, so a crossing does not take
effect until the next construction; either that is documented as acceptable or the lookup
moves per-request.

### 11.3 If H3 is falsified (no branch)

The block carries no `applies_when` guard and reads as a single global recommendation —
possibly identical to today's defaults, in which case the deliverable is:

> `LibraryRetrievalDefaults` = the existing global defaults. Measured EQUIVALENT (90% CI
> within ±0.02 nDCG@10) at 1.8k, 3.6k, 7k, 36k, 200k and 1M chunks, on real-PDF libraries and
> two BEIR datasets. No size-dependent parameter branch is required; the config surface does
> not grow.

That is a complete and useful answer to G1, and the protocol is designed so it can be reached
— which is the point of §7.5.

### 11.4 Regardless of outcome

Four things ship no matter what the sweep finds. **The first three are already applied to
`docs/libraries-spec.md` by the same change that added this protocol**; the fourth follows
the pilot.

1. **The corrected chunks-per-document arithmetic, and a withdrawn conclusion.** "~4k chunks
   per 200 docs" (§-1 G1) and "~72,000 chunks per 1000 PDFs" (§4) are replaced with the
   measured `fixed_tok512` figure of 36.2 chunks/doc. §4's segment-boundary argument is **not
   re-derived in a new direction** — correcting the numerator left the denominator (segment
   count) unmeasured, so the spec now states that the side of the full-scan/HNSW handoff is
   *undetermined*, notes that the only in-repo segment counts near library scale (8 at 500k
   points, 2 at 6.1k) would put a library well above the ~625 handoff rather than below it,
   and observes that the uncertainty strengthens rather than weakens the requirement to pin
   both `max_segment_size` and `full_scan_threshold` (§1.3a).
2. **The BM25 premise restated as a hypothesis.** The assertion that BM25 returns 3–4 hits
   against dense's 20 is now flagged in the spec as unverified and pointing the wrong way
   (§1.3c), and is registered here as H1b. The pilot decides it. If H1b holds, the spec
   sentence is struck and the depth risk relocated to the dense leg, where G2 already looks.
   Note that the *correct* form of the BM25 ceiling is
   `min(D, |filter-passing ∩ term-matching|)`, not `min(D, |filter-passing|)` — §6.4 measures
   the two sets separately for exactly this reason.
3. **The `settings.top_k` config gap.** The spec's G1 section now records that `top_k` is not
   currently readable as configuration (§8.1), so part of the deliverable is not expressible
   until gap 9 is done.
4. **The §10 gap-9 reporting fixes**, so a deployed instance can report the parameters it is
   running.

---

## 12. Execution order

**Part I — pilot.**

1. Gaps 5, 6, 9 (statistics, manifest, reporting surface) — offline, no infrastructure.
2. Gaps 1, 2 (sweep driver, per-leg instrumentation).
3. Gap 7p: build the nested P50 ⊂ P100 ⊂ P200 libraries + P200-random; generate and pin
   queries; **do not judge yet**.
4. **Track C first, on unlabelled data:** run the pilot grid, probe the per-leg ceilings and
   evaluate the §6.4 assertions, decide **H1b**, run the A/A null (10 replicates), and
   measure the per-query SDs, the realized confirm-split MDE and the realized 90% TOST
   half-width — on the actual G1 contrasts, not on a chunker proxy. This step needs no
   judgments at all and is the cheapest decision-useful output in the whole protocol.
   **Stop rule — deficit only.** If `dense_deficit_rate` or `bm25_deficit_rate` breaches
   §6.4's threshold at any rung, **stop**: a leg returned fewer hits than it could have, the
   finding is a G2 regression, and it supersedes G1. A non-zero `*_starved_rate` with zero
   deficit **does not
   stop anything** — it falsifies H1b, which is a registered outcome, gets reported as such
   (§6.4), and the study continues. Conflating the two would halt the study on legitimate
   BM25 term scarcity, which is most likely at P50.
5. Judge the pool; measure κ. **If κ(judge–human) < 0.4, the pilot's quality track is
   descriptive only** (§4.4) and step 6 is skipped.
6. Pilot stage 1 (tune) → stage 2 (confirm) on P50/P100/P200 + the SciFact anchor, including
   the **H2c** matched-depth decomposition (it needs labels, so it lands here, not in step 4).
   Publish `05_pilot.md` and, if warranted, a scoped `LibraryRetrievalDefaults` under §11.1's
   staging rule. **Gate:** compare the realized confirm-split MDE and 90% TOST half-width
   from step 4 against the design values (≈ 0.027 and ≈ 0.013–0.017). If either is materially
   worse — specifically, if the realized MDE exceeds 1.5 δ or the 90% half-width exceeds δ —
   apply §7.4's pre-registered response (enlarge the confirm split, shrink the test family,
   or raise δ) and **record it as an amendment before making any quality claim**. Comparisons
   that remain unresolvable are published as INCONCLUSIVE; the step-4 outputs ship either way.

**Part II — full study.**

7. Gaps 3, 4 (ladder builder incl. the ES half, NFCorpus loader).
8. Stage 1 screen: full grid, tune split, both datasets, L0 and L2. Publish the shortlist.
9. Stage 2 confirm: shortlist + default, confirm split, all rungs. Publish
   DIFFERENT/EQUIVALENT/INCONCLUSIVE and θ(c).
10. Filtered arm (§5.5) — confirmatory since G2 passed.
11. Track B at 1,000 documents (gap 7f), veto check.
12. Answer-level non-inferiority on the top two.
13. Write `LibraryRetrievalDefaults.md`; open the spec PR; pin the confirm-split baselines as
    the regression gate (§8.4).

Steps 1–6 are a defensible, publishable answer for v1-scale libraries. Steps 7–9 are the
minimum for a defensible answer to the *size-branch* question. Steps 10–12 are what make the
whole thing publishable.

---

## Amendments

*(none yet — record dated deviations here, never by editing the body above)*
