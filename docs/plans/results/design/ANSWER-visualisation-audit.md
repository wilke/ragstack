# What the chunking study can honestly be graphed as

*Audit of every candidate figure against the phase-0 measurements. Analysis only — no
experiment was run, no store was touched, no GPU was used. Repo `/home/wilke/Development/ragstack`,
main `d225cea`. Everything below is recomputed from existing artefacts under
`scratchpad/phase0/` with `/rag/envs/ragstack/bin/python`; `/rag/` was read-only throughout.*

---

## 0. The short answer

**Yes — there is enough data for four or five figures that carry real information, and the
study's most important results are exactly the ones that plot well.** The chunking grid is a
*census* on the structural side (every chunk of every config was counted) and a *paired
design* on the quality side (per-topic arrays on Leg A, per-query arrays on Leg B), so
uncertainty can be shown honestly wherever it exists.

The risk is not lack of data. The risk is that the two most tempting plots — a ranked bar
chart of the 24 configs, and a chunk-size trend line — would both draw *unresolved* results as
if they were effects. Both are rejected below.

### Recommended shortlist, ranked by information per square inch

| # | figure | what it settles | uncertainty | status |
|---|---|---|---|---|
| **1** | **Realised vs nominal chunk tokens, by kind** (`fig1`) | The single most explanatory fact in the study: `semantic` emits ~350-token blocks whatever its cap says, `words` is stuck at 0.64 fill, and "size" is not a comparable axis across kinds. Kills the kind-vs-kind reading before it starts. | none needed — census of every chunk | **made** |
| **2** | **Quality vs realised tokens, both legs** (`fig2`) | Realised tokens beat nominal size as a predictor on *both* legs (r +0.811 / −0.974) and kind nearly vanishes once it is controlled. Shows the legs agreeing on the variable and disagreeing on the slope's sign, in one picture. | 95% bootstrap CI per config, both legs | **made** |
| **3** | **Dense vs reranked contrast forest** (`fig3`) | The decision-relevant result: every size contrast that resolves dense is unresolved behind the cross-encoder, and production reranks. Shows δ80 — the instrument's resolution floor — so an unresolved contrast cannot be misread as a null. | 95% bootstrap CI + δ80 floor + bar X per leg | **made** |
| **4** | **Position of evidence** (`fig4`) | Why long-document chunking is worth studying at all: 55.4% of the evidence begins past token 1,024 and only 11.9% is in abstract+intro. | topic-clustered bootstrap band and CIs | **made** |
| 5 | **Document-length ECDFs vs BEIR** (`fig5`) | Why the benchmark corpora were abandoned: trec-covid's *longest* document is 925 tokens, so 12 of 24 grid cells are dead on it. | none for the measured curves; BEIR shown as reported summary stats only, not as distributions | **made** |

Figures 6 and 7 of the candidate list are **not** recommended as figures — reasons in §3.

---

## 1. What data actually exists, at what granularity

| artefact | granularity | supports |
|---|---|---|
| `stage1/cstats_*.json` (24), `stage1-legB/cstats_x0_*.json` (24), `cstats_x11_5_*.json` (12) | per config: `n_chunks`, `chunks_per_doc`, `tok_median`, `tok_p95`, `tok_max`, `fill` — a **census**, not a sample | figs 1, 5; overlap vector inflation |
| `stage1/report1.json` → `per_topic["summary|1"]` | **per topic × per config × dense and `_rr`**, 10 topics × 24 configs × 4 metrics | any Leg A CI or contrast |
| `stage1-legB/runs_{x0,x11_5}_*.json` → `per_query` | **per query × per config**, 396 generated (260 accepted) × dense and `rr` | any Leg B CI or contrast |
| `stage1-legB/report-legB.json` | published means, 11-contrast family, Holm, panels, cost | cross-check only |
| `pilots/oracle_results.jsonl` | **2,161 (topic, document) pairs**, 90 topics, per-pair `argmax_start_tok`, `argmax_cls`, `doc_tokens`, per-unit score vectors | fig 4, fig 5, topic-clustered bootstrap |
| `pilots/legb2_final.json` | 400 generated Leg B queries, `doc_tokens`, `sec_start_tok`, `sec_class`, `accepted` | fig 5; construction diagnostics |
| `review/lengths_xml200.csv` | 197 CDS judged documents, body tokens (median 4,097) | fig 5 (alternative definition) |
| `step3/report3.json`, `runs3.json` | step-3 per-topic metrics for 5 arms | reproduction check only — superseded by stage 1 |
| `stage1/semantic_cost.json` | notional vs actual breakpoint tokens per semantic config | a possible cost figure, §3.8 |

**A naming note for readers of the brief:** the task refers to `stage1/report3.json`. There is
no such file — Leg A's per-topic report is `stage1/report1.json`; `report3.json` belongs to
`step3/`. Both were read.

**Reproduction check performed before drawing anything.** Every quantity in figures 2 and 3 was
recomputed from the per-topic / per-query arrays and compared against the published numbers:
all 24 Leg A config means, all 36 Leg B config means (dense and reranked), 20 named contrasts,
the two OLS fits, and the reranking-shrinkage contrast. **Zero mismatches** — e.g. Leg A
`nDCG@10 ≈ 0.1185 + 0.0447·log₂(realised)`, r = +0.8115; Leg B `1.0896 − 0.0140·log₂`,
r = −0.9742; Leg B ×11.5 shrinkage −0.0618, CI [−0.0903, −0.0339] against the published
[−0.0902, −0.0340]. The figures therefore plot the study's own numbers, not a re-derivation
of them.

---

## 2. The candidate figures, one by one

### 2.1 Realised vs nominal chunk tokens per kind — **MAKE. Rank 1.**

**Data:** exists at full granularity, on both legs. `tok_median`, `tok_p95`, `tok_max` and
`fill` are computed over every chunk of every config (Leg A: 4,053 documents, 15,663–195,939
chunks per config).

**What it shows.** Panel A plots realised median tokens against the nominal budget on log–log
axes with the `fill = 1.00` diagonal. `token_window` sits exactly on the diagonal; `sentence`
runs just under it (0.89 → 0.97, *rising* with size); `words` runs parallel at a constant 0.64;
`semantic` is **flat at 255 / 359 / 357 / 343 tokens across a 256→2048 nominal range**, i.e.
its four cells are four labels on one ~350-token config. Panel B is the same information as
fill, with scifact's reported `sentence`/`words` fill overlaid to show that fill is a property
of *corpus × kind*, not of kind.

**Honesty.** No error bars are needed or appropriate: these are complete counts, not estimates.
The two caveats that must travel with it are printed on the figure — semantic's `fill` is not a
fill (its `size` is a cap that mostly does not bind), and the scifact overlay is quoted from
`docs/plans/chunking-evaluation.md`, not measured here.

**Why rank 1.** It converts three separate "kind effects" into one variable, and it is the
reason the kind comparison in the grid cannot be read at face value. It is also the only figure
here with no inferential content at all to argue about.

### 2.2 Quality vs realised tokens, both legs — **MAKE, as two panels. Rank 2.**

**Data:** exists. Config means from `report1.json` / `runs_x0_*.json`; realised medians from
`cstats_*`; per-topic and per-query arrays give genuine CIs.

**What it shows.** Leg A: positive slope, r = +0.811 against +0.654 for nominal size. Leg B:
negative slope, r = −0.974 against −0.870. Same variable, opposite sign. The `semantic` points
are marked, and they are the visual proof of the mechanism: they sit at ~350 realised tokens on
*both* panels, which is why semantic ranks 16/18/20/24 on Leg A and 5/8/9/11 on Leg B.

**The one thing that would have made this figure dishonest** is a shared y-axis. Leg A's whole
range is 0.40–0.63 on graded relevance over ~109 relevant documents per topic; Leg B's is
0.93–0.98 on a near-binary known-item task with one relevant document. Overlaying them on one
axis would flatten Leg B to a horizontal line and invite a comparison of absolute scores that
the study explicitly forbids. **Two panels, shared log₂ x, separate y.**

**Honesty.** Bars are 95% percentile bootstrap CIs of each config mean — over topics on Leg A
(n = 10, so they are wide, and they should be), over accepted queries on Leg B (n = 260). The
r values are labelled *exploratory*: the 24 points in a panel are means over the same queries,
so they are not independent observations, exactly as both RESULTS documents state.

### 2.3 Dense vs reranked effect sizes — **MAKE, and make it the decision figure. Rank 3.**

This subsumes candidate 5 and does more.

**Data:** exists at per-topic / per-query granularity, so dense and reranked contrasts and
their *difference* are all computable as paired quantities. All 20 published point estimates
reproduce exactly.

**What it shows.** Six contrasts × two arms × two legs, as a forest plot. Each row carries
three pieces of information that a bar chart cannot: the CI, the leg's pre-registered bar X as
a pale band, and **δ80 — the smallest effect that design could detect at 80% power — as a grey
block**. That last element is what stops the figure from lying: on Leg A, `size 2048 − 256`
has δ80 = 0.187 against a 0.05 bar, so its "unresolved" verdict is a statement about the
instrument, not about chunk size, and the reader can see that directly.

Read off the figure: Leg B's deep rung resolves all four dense size contrasts and **none** of
the reranked ones; Leg A resolves exactly one dense step (`1024 − 512`, +0.1204) which
collapses to +0.0120. Overlap is a *powered null* on both legs — marked with a distinct
symbol, because "we looked and found nothing" and "we could not have seen anything" must not
share a marker.

**Honesty.** The overlap rows take each leg's **pre-registered primary rung**, which for Leg B
is the judged-only ×0 corpus; this is annotated on the figure, and the ×11.5 replication
(−0.0078, δ80 0.0114, *not* powered to the bar) is named in the caption rather than silently
dropped. The shrinkage is quoted as its own resolved contrast (−0.0618, CI [−0.0902, −0.0340])
so nobody can dismiss the collapse as a power artefact.

### 2.4 Position-of-evidence histogram — **MAKE, but as an ECDF, not a histogram. Rank 4.**

**Data:** exists per pair, with the clustering unit recorded — `oracle_results.jsonl` has 2,161
pairs over 90 topics and 2,095 documents, each with the winning section's start token, its
class, and the full per-unit score vector.

**Why an ECDF instead of the bucket histogram.** The published buckets (0–512, 512–1,024,
1,024–2,048, 2,048–4,096, …) are unequal in width, so a bar chart of them is a density plot
with the density removed — the eye reads bar height as concentration and gets it wrong. The
ECDF asks the question a chunking study actually wants answered: *if I index only the first N
tokens, what fraction of the evidence do I have?* The four grid sizes are marked on it. Both
renderings agree with the published table; the ECDF at 1,024 reads 0.446, i.e. **55.4% past
1,024**, to the published digit.

**Honesty.** Three things are printed on the figure, all of them load-bearing:

* the oracle **is** `bge-reranker-v2-m3`, the model the production pipeline runs — this
  measures what that reranker can find, not ground truth;
* the **strict** definition is used (the section *begins* past the offset); the looser midpoint
  definition gives 78.7% and is named;
* the band is a **topic-clustered** bootstrap, not the pooled Wilson interval, because pairs
  within a topic are not independent — the pooled interval is the over-confident one and the
  study says so.

The second panel gives section-class wins with the honest denominator (`methods` wins 12.9% of
all pairs but 32.3% of the pairs whose document *has* a methods section).

**What must NOT be added to this figure:** the Leg B position distribution. Leg B's sources
were *sampled* on a positive rule — 100% of the 260 accepted sources have their evidence
section starting past token 1,024, by construction. Plotting that next to Leg A's measured
distribution would present a sampling rule as a finding.

### 2.5 Document length distributions — **MAKE, with a labelled asymmetry. Rank 5.**

**Data:** partial, and the partiality has to be visible.

* **Available at full granularity:** CDS judged documents as indexed (n = 2,095, from the
  oracle rows), Leg B PMC OA sources (n = 260 accepted), and a 197-document CDS body-only
  sample (`review/lengths_xml200.csv`, median 4,097 body tokens).
* **Not available:** per-document lengths for scifact and trec-covid. Only the reported
  summary statistics exist (scifact median 348, p95 649, exactly one document over 2,048;
  trec-covid median 378, **max 925**). Computing them would mean downloading both corpora and
  running the SFR tokenizer — an experiment, and out of scope here.

**How it is drawn honestly.** Two measured ECDFs; the BEIR corpora as clearly-labelled
reference marks — a vertical line at 925 ("the longest document in the whole of trec-covid")
and median ticks — never as curves. A watch-out that was checked: the three length definitions
differ (title+abstract+body vs body-only vs abstract-only for BEIR), so only the two
consistently-defined series are drawn as distributions.

**Why it earns a place.** It is the figure that answers "why did you build your own judged
set?" in one glance, and the answer is not an opinion: 12 of 24 grid cells collapse to one
chunk per document on scifact, and the overlap axis does not exist there at all.

### 2.6 Overlap cost vs benefit — **REJECT as a figure; keep as two rows of a table.**

**Data:** exists and is clean. Chunk counts per config are a census: at size 256 on Leg B's
×11.5 corpus, 191,430 / 217,806 / 252,989 vectors at 0% / 12.5% / 25% — a 1.322× inflation,
**61,559 extra vectors on 5,000 documents**. The quality side is a powered null on both legs
(Leg A −0.0210, δ80 0.041 < bar 0.05; Leg B ×0 −0.0040, δ80 0.0081 < bar 0.010), and
recall@100 moves by **exactly 0.0000** at every size at ×11.5.

**Why not a figure.** The entire content is "cost rises 1.00 → 1.14 → 1.32; benefit is zero
with a tight interval". A chart of a monotone theoretical ratio against a flat zero adds
nothing a two-row table does not, and a bar chart of a zero effect *invites* over-reading the
noise in it. The overlap contrasts are already on figure 3 with their CIs and their powered-null
marker, which is the honest home for them. **Recommendation: one table, or an inset on fig 3.**

### 2.7 Reranker truncation curve — **MAKE ONLY AS A SMALL DIAGNOSTIC, clearly labelled n = 1.**

**Data:** exists only as a **12-number table** in `docs/plans/chunking-evaluation.md`
(2026-09-02). A repository-wide search found no underlying artefact — no script, no JSON, no
per-item scores. It is **one padded chunk, one answer sentence, moved between the front and the
back of the padding**, scored at six lengths.

| approx chunk tokens | answer at start | answer at end |
|---:|---:|---:|
| 0 | 0.9868 | 0.9868 |
| 256 | 0.9302 | 0.8325 |
| 1,024 | 0.8804 | 0.8047 |
| 2,048 | 0.8589 | 0.8032 |
| 4,096 | 0.7808 | **0.0025** |
| 6,144 | **0.7808** | 0.0025 |

**Verdict.** The *mechanism* is unambiguous and does not need statistics: the start column
plateaus at 4,096 (text past the limit is never read, so adding more cannot move the score) and
the end column collapses to 0.0025 (the answer was cut away). Two independent signatures of a
hard cut. **But there are no replicates, no query sample, and no error bars are possible** —
this is a single probe, and it must be captioned as one. It is a supporting diagnostic that
licenses "no config in the study exceeds 4,096, so no reported score is a truncation artefact",
not a result about quality. **Not in the top four.** Drawing it as a smooth "score vs length
curve" without the n = 1 label would be the most misleading thing in this list.

### 2.8 Two candidates I would add

**(a) Semantic's cost, from `semantic_cost.json` — worth a small figure if GPU budget is on the
agenda.** Census data, no uncertainty: the four semantic configs cost **567 M notional
breakpoint tokens** on a 4,053-document corpus (~6× the corpus *per config*, on top of chunk
embedding), of which the per-document cache saved 60.4% (225 M actual) — and the cache hit
pattern (0% / 99.6% / 94.6% / 45.6%) confirms the mechanism rather than merely asserting it.
The honest headline is that semantic is **~7× a `token_window` config of the same nominal
size**, not the 2× the brief assumed, while also being the worst-scoring kind on Leg A and
producing a 3.4× larger index. This is a real, decision-relevant, uncertainty-free number.
*Caveat that must be printed: the study explicitly forbids pruning semantic on this evidence.*

**(b) The Leg B query-construction funnel** (400 generated → rules → verifier → 260 accepted,
with each filter's independent hit rate from `legb2_screened.json`). Useful for a PI judging
whether to trust the instrument; purely descriptive; no inference. Medium value — include only
if the audience is being asked to approve stage 2's query budget.

---

## 3. Figures that must NOT be drawn

These are rejections, not omissions. Each one would render an explicitly unresolved result as
an effect.

| rejected figure | why it would mislead |
|---|---|
| **Ranked bar chart of the 24 configs** (either leg) | Both RESULTS documents state the ranking is descriptive and neighbouring rows are not ordered claims. Leg A's top five span 0.629–0.600 against a **0.12 noise floor**; Leg B's top six span 0.9838–0.9770 against a δ80 of 0.008–0.02. A sorted bar chart makes rank differences look like quality differences. |
| **A chunk-size trend line across either leg** | Leg A is *not monotone* (it falls 256→512, jumps 0.12 at 1024, falls again) and its "coarse wins" direction rests on **one step**. Leg B is monotone dense but flat reranked. A trend line asserts a shape that Q2's verdict calls *not establishable*. |
| **Anything presenting the 512→1024 disagreement as a resolved direction** | It is the study's one genuine both-legs-resolvable contradiction, it is bar-sensitive (§5.1 of the Leg B results), and it disappears behind the reranker. It belongs on fig 3 as two opposite-signed rows with their CIs — and nowhere else. |
| **Kind-vs-kind league table or bar chart** | Every kind contrast is unresolved on at least one leg, and §9 of the Leg B results shows they are realised-size contrasts wearing kind labels. Fig 1 + fig 2 are the honest way to show this. |
| **Leg B recall@100 at the ×0 rung** | Declared degenerate *before* the run: 0.9923–1.0000 with cells at exactly 1.0, where a Wald SE is zero and a naive floor reads "resolvable". Never plot it. |
| **Both legs on one shared y-axis** | Absolute scores are not comparable (0.40–0.63 vs 0.93–0.98, different relevance definitions, different numbers of relevant documents). |
| **Leg B position-of-evidence as an empirical distribution** | 100% past token 1,024 is the sampling rule, not a finding. |
| **Any Leg C quality result** | Leg C never ran as a retrieval experiment. Only the citance-mining pilot exists (resolvability rates, position-filter survival). There is no Leg C grid, no Leg C metric, nothing to plot against chunk size. |
| **`fixed_tok512` (shipping) shown as "bad"** | Rank 21/24 on Leg A dense, 14/24 on Leg B dense, **4/24 reranked** at ×0 and 2/12 reranked at ×11.5. Three orderings, three answers; any single-panel version of this is advocacy. |

---

## 4. What was generated

All under `scratchpad/design/figures/`, standalone SVG, 1,000 px wide, greyscale-legible
(series separated by marker shape and dash pattern first, tone second), axes labelled with
units, uncertainty shown wherever it exists.

| file | figure |
|---|---|
| `fig1-realised-vs-nominal.svg` | Realised vs nominal chunk tokens by kind, plus fill, plus the scifact contrast |
| `fig2-quality-vs-realised-tokens.svg` | Dense nDCG@10 vs realised median tokens, Leg A and Leg B panels, 95% bootstrap CIs |
| `fig3-dense-vs-reranked.svg` | Contrast forest, dense vs reranked, both legs, with δ80 floors and bar X bands |
| `fig4-position-of-evidence.svg` | ECDF of evidence start token with topic-clustered band; section-class wins with honest denominators |
| `fig5-document-lengths.svg` | Document-length ECDFs vs the reported BEIR summary statistics |

**How they were produced.** `matplotlib` is not installed in `/rag/envs/ragstack/bin/python`,
`/rag/envs/vllm`, or the system interpreter, and nothing was installed. The figures are emitted
as hand-written SVG by `figlib.py` (a ~150-line dependency-free plotting layer) driven by
`prep*.py` / `fig*.py`, using only `json` and `numpy` 2.5.0 from the pinned interpreter. No
repo module is imported, so no `PYTHONPATH` pin is required; every path is absolute and every
read is inside `scratchpad/phase0/`, plus one read of `docs/plans/chunking-evaluation.md` for
the quoted scifact fill and truncation table. `check.py` audits each SVG for out-of-canvas
elements and text collisions — all five pass clean.

**One limitation of that audit, stated plainly:** there is no SVG rasteriser on this host
either (no `rsvg-convert`, `inkscape`, ImageMagick, headless browser or `cairosvg`), so the
figures were verified *programmatically* — bounds, text collisions, and build-failing
assertions that no confidence interval is clipped — but **never actually rendered**. `check.py`
estimates text width as 0.55 × font-size × characters, a heuristic rather than a font metric.
Open the five files once in a browser before circulating them.

**Rebuild:** `cd scratchpad/design && for s in prep prep2 prep3 prep4 fig1 fig2 fig3 fig4 fig5; do /rag/envs/ragstack/bin/python $s.py; done`

### 4.1 Four honesty decisions taken while drawing

1. **Leg A's y-axis in fig 2 was widened to 0.23–0.81.** A range chosen to fit the 24 *point
   estimates* (0.40–0.63) clipped **32 of the 48 CI endpoints**, which would have drawn Leg A
   as far more precise than n = 10 allows. The trend consequently looks flatter — that is the
   correct impression. An assertion in `fig2.py` now fails the build if any interval is clipped;
   the same assertion guards fig 3.
2. **The position histogram became an ECDF** — the published buckets are unequal in width, so
   bar height would misread as density (§2.4).
3. **Fig 3's overlap rows use each leg's pre-registered primary rung** (Leg B: ×0), annotated on
   the figure, with the weaker ×11.5 replication named in the caption rather than dropped.
4. **`powered null` has its own marker**, distinct from `unresolved`. "We looked and found
   nothing" and "we could not have seen anything" are different claims and must not share a
   symbol — this is the whole content of Leg A's §4.3 process lesson.

---

## 5. What could NOT be made, and what it would take

| not makeable | why | what would be needed |
|---|---|---|
| **scifact / trec-covid length distributions** | Only reported summary statistics exist; no per-document token counts were ever computed in this study | Download both BEIR corpora and run the SFR tokenizer over them (~176k documents). Cheap in GPU terms (CPU tokenization only) but it is a new measurement, not analysis. |
| **Error bars on the reranker truncation curve** | n = 1. One chunk, one answer sentence, six lengths, no replicates, and no artefact file — the numbers exist only as a markdown table | Re-run the probe over a sample of (query, answer) pairs — say 50 — recording per-pair scores. Then the plateau and the collapse could carry intervals. |
| **Any Leg C result** | Leg C ran as a *citance-mining feasibility pilot* only. There is no Leg C query set scored against a chunking grid | Run the Leg C grid. This is the leg the Leg B results name as the only thing that can break the Leg A / Leg B tie. |
| **A per-chunk token *distribution* (violins/boxes) rather than median + p95** | The 24 `chunks_*.jsonl` files hold per-chunk token counts but total ~2.8 GB; reading them all is possible on CPU but was judged not worth it — `tok_median`/`tok_p95`/`tok_max` already carry the point | ~10 minutes of CPU I/O if a full distribution is ever wanted. This is the one "not made" item that is purely a cost decision, not a data gap. |
| **A reranked arm for the 12 non-`token_window` cells at Leg B's ×11.5 rung** | Only the 12 `token_window` cells ran at ×11.5 — pre-registered before the run, to stay inside the ~2 GPU-h ceiling | A stage-2 run. Do not interpolate. |
| **Any figure about answer-generation quality** | The whole study measures *retrieval*. Overlap's null is a null about retrieval quality; what overlap does for context continuity in generation is untested | A generation-side evaluation, which does not exist yet. |
| **Per-query CIs on Leg A** | Leg A's sampling unit is the topic (n = 10); per-query resolution below that does not exist in `report1.json` | Nothing — this is correct as designed. It is why Leg A's bars are wide. |

---

## 6. One methodological recommendation for the figures themselves

Every quality figure in this set shows **δ80 or a CI, not just a point estimate**, because the
single most repeated lesson in these documents is that a point estimate without its resolution
floor is unreadable: Leg A's pre-registered primary contrast had a floor **4× its own bar** and
could not have returned a positive answer whatever the truth. If any of these figures is reused
in a slide deck, the floor must travel with it. A stripped-down version showing only the
point estimates would reproduce, in graphical form, exactly the error the study spent a stage
discovering.
