# PREREG — the stage-1 chunking grid on Leg B

*Written 2026-09-05 on `coconut`, **before any embedding call of this run**. Repo
`/home/wilke/Development/ragstack` at `d225cea`, pinned past the `/rag/repos` editable-install
meta-path finder. Harness reused from
[`../stage1/`](../stage1/) (Leg A) and [`../pilots/legb2_sigma.py`](../pilots/legb2_sigma.py)
(Leg B corpus construction). Predecessors:
[`RESULTS-stage1-legA.md`](../stage1/RESULTS-stage1-legA.md),
[`RESULTS-legB-rerun.md`](../pilots/RESULTS-legB-rerun.md).*

---

## 0. Why this run exists, in one sentence

Leg A's 24-config grid says **coarse chunks win**; Leg B's five-cell pilot says **fine chunks
win**, with opposite signs and non-overlapping intervals on recall@100 — but that comparison
rests on **one contrast**, so this run puts the *same grid* on Leg B to find out whether the
legs disagree **everywhere or only at particular sizes**.

**This run prunes nothing.** Per the group's direction, its only job is to establish which
axes are *uncontested* (agreeing on both legs) and which are *contested*.

---

## 1. The two questions, in quantitative form

### Q1 — does the overlap null replicate on Leg B?

Leg A found overlap to be a **real null with an adequate power floor**: main effect
`12.5% − 0% = −0.0210`, CI `[−0.047, +0.007]`, `δ80 = 0.046 < bar 0.05`, and
`|Δ recall@100| ≤ 0.0033` at every size — while costing up to **1.32×** the vectors.

> **Q1 resolves as REPLICATES** iff, on Leg B at the primary rung, contrast
> `4_overlap_12_5_minus_0` on nDCG@10 satisfies **all three**:
> (a) `|mean| < X_B`, (b) the 95% bootstrap CI contains 0, and
> (c) `δ80 < X_B` — the instrument could have seen an effect of bar size.
>
> **Q1 resolves as CONTRADICTS** iff `|mean| ≥ X_B` **and** the CI excludes 0 **and**
> `δ80 ≤ |mean|` **and** the contrast survives Holm across the family in §3.
>
> Any other outcome — including `δ80 ≥ X_B` — is written **UNRESOLVED**, not "null".

The same three-way verdict is applied to `3_overlap_25_minus_0` and to the size×overlap
interaction contrasts (`1`, `2`).

### Q2 — is the size disagreement uniform across the grid, or size-localised?

Leg A's per-size profile is monotone-ish upward (coarse better); Leg B's five-cell pilot is
monotone downward. The question is whether the *sign flip* holds at **every adjacent step**.

> For each adjacent step `s → 2s` ∈ {256→512, 512→1024, 1024→2048}, compute the paired
> contrast `M(2s) − M(s)` averaged over the three overlap fractions, on both legs.
>
> **UNIFORM** iff every step whose verdict is resolvable on *both* legs has opposite signs.
> **SIZE-LOCALISED** iff at least one such step agrees in sign while at least one disagrees.
> A step that is unresolvable on either leg is reported as **unresolvable at that step**, and
> explicitly excluded from the uniform/localised verdict — it is not evidence of agreement.

Leg A's own per-step numbers are re-read from `../stage1/report1.json` rather than
re-derived, so the side-by-side is the same arithmetic on both sides.

### Q3 (secondary, may reframe the study) — does the realised-tokens explanation replicate?

Leg A: `corr(log2 realised median chunk tokens, nDCG@10) = +0.811`,
`corr(·, recall@100) = +0.891`, nominal size only `+0.654`, and the *kind* residual spread
(0.054) collapses to about the noise floor once realised size is controlled.

> **Replicates** iff, on Leg B, `|corr(log2 realised median tokens, nDCG@10)| ≥ 0.70`
> **and** `|corr(log2 realised)| > |corr(log2 nominal)|` **and** the spread of mean per-kind
> residuals about the realised-size fit is **smaller** than the spread of raw per-kind means.
>
> **The sign is expected to be NEGATIVE on Leg B** (fine wins). A negative `r` of large
> magnitude is a *replication of the mechanism*, not a failure: the claim under test is that
> realised tokens is the explanatory variable, not that more tokens is better. This is
> written down here so a negative `r` cannot be re-read as a falsification after the fact.
>
> This is a **descriptive** analysis over 24 config means that share the same queries. It
> gets no Holm protection and no inferential claim, exactly as on Leg A.

---

## 2. The judged set, the corpus, and what "recall" means here

### 2.1 Queries

* **Primary set: the 260 accepted Leg B queries** (`legb2_final.json`, `accepted == True`),
  the output of the re-run's rule gates + LLM abstract-answerability verifier.
* **Robustness set: all 396 generated queries with non-empty text.** Retrieval is run for
  every one of them at no extra fleet cost (the cost is in the chunks, not the queries), so
  the accepted subset can be checked against the whole generated population. Reranking is
  run on the accepted 260 only, and that restriction is stated wherever a reranked number
  appears.

**Carried-forward limitations, named not fixed.** The pilot's §8.3 lists three things to fix
*before assembly of the full 1,500-query set*: 2/400 sampled articles are retracted (both
accepted), 35/260 accepted sources are not `research-article`, and the verifier needs
strengthening. **None is fixed here.** This run measures the *config grid* on the pilot's
queries as they stand; changing the query set would make it incomparable with the pilot's own
five-cell numbers, which are this run's reproduction gate. The limitation travels with every
number below.

### 2.2 Judged-set construction, and how recall is defined

Leg B is **near-binary known-item**, and this changes the metrics' behaviour in ways that must
be stated before any number is read:

* **Exactly one relevant document per query**: the article whose section the query was
  written from. Grade is binary (1). There is no grade-2 condition and no `description`
  variant, so two of Leg A's four conditions do not exist here.
* `nDCG@10 = 1/log2(rank+1)` if the gold document is in the top 10, else 0 — because with a
  single graded-1 relevant the ideal DCG is 1. Per-query values are therefore drawn from the
  discrete ladder {1, 0.631, 0.5, 0.431, …, 0}, not a continuum.
* `recall@k = 1` if the gold document is in the top *k*, else 0 — a **hit rate**, not Leg A's
  ~109-relevants-per-topic fraction. The two legs' recall@100 numbers share a name and not a
  scale.
* `MRR@10 = 1/rank` if rank ≤ 10 else 0.
* Document score = **max over the document's chunks** of the query·chunk cosine, top-200
  depth. This is Leg A's rollup and step 3's, unchanged.

**Consequence for variance, stated in advance.** Because nDCG@10 sits at 0.92–0.99, most
queries score *identically* under two configs and contribute a paired difference of exactly
zero; the variance comes from the ~5% of queries that flip. Measured on the pilot,
`σ_d = 0.119` (×0 rung) / `0.152` (×11.5) — the *same or lower* than Leg A's 0.156, despite
Leg A having ~109 relevants/topic to average over. **Leg B is an easy task**: headroom is 1–8
nDCG points, so an absolute Leg B score is not comparable with a Leg A score (0.40–0.63), and
a config "improvement" of 0.02 means very different things on the two legs.

### 2.3 The two rungs

| rung | corpus | tokens | why |
|---|---|---:|---|
| **×0 (judged-only)** | the **400 sampled source articles** | 3.87 M | plan §5's pre-registered stage-1 rung ("that is where the config contrasts live"); the task's literal corpus |
| **×11.5 (deep)** | the same 400 + **4,600 seeded PMC OA distractors** | 48.37 M | the pilot's operating rung; the *only* rung where recall@100 is non-degenerate, i.e. the only place the Leg A head-to-head can be read |

Both corpora are **query-independent**. The ×0 corpus is all 400 sources, not the 260 accepted
ones: the 140 sources whose query was rejected are topically matched deep science, i.e.
*harder* distractors than a random draw, and using them keeps the corpus fixed when the accept
decision moves.

**The ×11.5 corpus is the pilot's, bit for bit.** Rebuilt with
`distractors(4600, exclude=sources, rng=Random(20260904+7))` and verified before any embedding
call: file-list `sha256[:16] = c6fb04503fdee62a`, **matching `legb2_sigma.json`'s recorded
`corpus_sha256`**. ✅ *verified 2026-09-05, before this document was finished.*

### 2.4 Which configs run at which rung — and why the deep rung is token_window only

* **×0: all 24 `STAGE1_CONFIGS`**, imported from `chunking_compare_7way`, never re-declared.
* **×11.5: the 12 `token_window` cells only** (4 sizes × 3 overlap fractions).

This is a deliberate, pre-registered budget allocation and it is a **deviation to declare**,
not an accident:

1. Both headline questions are **token_window-only questions**. Q1's overlap axis exists only
   in the `token_window` block; Q2's per-size panel is Leg A's Table 2, which is entirely
   `token_window`. Restricting the deep rung to those 12 cells costs neither question anything.
2. The kind axis (contrasts 7–9) and the realised-tokens check (Q3) need all 24 cells, and
   they run at ×0, where a config costs 3.87 M tokens instead of 48.4 M.
3. Running all 24 at ×11.5 would cost **≈ 3.1 GPU-hours** (1,287 M chunk-embed tokens at the
   measured 161k tok/s, plus a ~440 M-token semantic breakpoint pass) — **over the ~2 GPU-hour
   ceiling this run was given**. Dropping the 12 non-`token_window` cells at the deep rung
   removes the entire deep-rung semantic breakpoint pass, the single largest line item.

### 2.5 Reranking

`bge-reranker-v2-m3` at `:50052` over the **top-100 documents' winning chunk texts** per
(config, query) — Leg A's `do_rerank`, unchanged in depth and mechanism. Run at **both** rungs
if budget allows; the deep rung has priority (it is the rung with real competition, and the
analogue of Leg A's single rung). Leg A found dense↔reranked config rank correlation of only
**+0.553**, so a first-stage-only result would not transfer to a pipeline that reranks.

---

## 3. The pre-registered contrast family, with its rung assignment

One Holm–Bonferroni family at α = 0.05, **11 contrasts**, on the primary metric **nDCG@10**.
Contrasts 1–9 are Leg A's family verbatim (same code path, same definitions); 10–11 are the
two extra adjacent size steps Q2 needs (Leg A's contrast 6 supplies the third).

Each contrast is assigned its rung **in advance**, chosen from the pilot's measured
per-contrast `σ_d` so that the reading is made where it can be made.

| # | contrast | rung read as primary | replicated at |
|---|---|---|---|
| 1 | `I = E(256) − E(2048)` (extremes DiD, `E(s) = M(s,25%) − M(s,0%)`) | ×0 | ×11.5 |
| 2 | slope of `E(s)` per doubling of size | ×0 | ×11.5 |
| 3 | overlap `25% − 0%`, averaged over the 4 sizes | **×0** | ×11.5 |
| 4 | overlap `12.5% − 0%`, averaged over the 4 sizes | **×0** | ×11.5 |
| 5 | size `2048 − 256`, averaged over the 3 fractions | **×11.5** | ×0 |
| 6 | size `256 − 512`, averaged over the 3 fractions | **×11.5** | ×0 |
| 10 | size `1024 − 512`, averaged over the 3 fractions | **×11.5** | ×0 |
| 11 | size `2048 − 1024`, averaged over the 3 fractions | **×11.5** | ×0 |
| 7 | `sentence_tok512 − fixed_tok512` | ×0 only | — |
| 8 | `words_tok512 − fixed_tok512` | ×0 only | — |
| 9 | `semantic_tok512 − fixed_tok512` | ×0 only | — |

**Bar X_B = 0.010 nDCG@10.** Derivation, written down because Leg A's 0.05 would be nearly the
whole of Leg B's headroom and would make every contrast a trivial "null": Leg A's bar is
`0.05 / 0.224 = 22.3%` of its realised 24-config nDCG@10 spread. The pilot's realised
five-cell spread at the ×0 rung is `0.9821 − 0.9413 = 0.0408`; 22.3% of that is **0.0091 ≈
0.010**. If this run's realised 24-config spread turns out materially wider, the pre-registered
0.010 bar is still the one that decides §1's verdicts; any re-derived bar is reported
**alongside** it and labelled post-hoc.

**Bar X_B applies to nDCG@10 only.** recall@100 and MRR@10 carry their own scales and are read
against `δ80` and CI alone, descriptively.

**Sign criterion.** Leg A required ≥ 7/10 topics to agree in sign. On Leg B most per-query
paired differences are **exactly zero** by construction (§2.2), so a "win/loss out of n" rule
is meaningless. It is replaced by: **among the non-zero paired differences, ≥ 60% agree with
the sign of the mean.** Ties are reported separately (`w/l/tie`).

**A contrast is `resolved` iff** `|mean| ≥ X_B` **and** CI excludes 0 **and** the sign
criterion holds **and** Holm across the 11 rejects **and** `δ80 ≤ |mean|`. The last clause is
new relative to Leg A and is the whole point of §4.

---

## 4. The power floor of every threshold this run intends to read

`δ80 = (z_{0.025} + z_{0.20}) · σ_d / √n = 2.802 · σ_d / √260 = 0.1738 · σ_d`.

Two rules, both pre-committed:

1. **δ80 is computed before the threshold is read**, from **this run's own** paired
   differences. The floors below are *planning* figures projected from the pilot's five cells
   on the **same 260 queries** — good enough to allocate rungs, not a substitute for the real
   computation. Any discrepancy is resolved in favour of the run's own number.
2. **A row whose `δ80` exceeds the distance it must travel is written UNRESOLVED, never
   "null".** On Leg A the pre-registered primary contrast had a floor **4× coarser than its own
   bar** and could never have answered its question; in the Leg B round, six of fifteen gated
   readings failed this check.

### 4.1 Projected floors, from the pilot's per-query data (n = 260 accepted)

| # | contrast | metric | rung | projected `σ_d` | projected `δ80` | vs bar 0.010 | reachable? |
|---|---|---|---|---:|---:|---|---|
| 4 | overlap 12.5% − 0% (4-size mean) | nDCG@10 | ×0 | ≈0.055 | **≈0.0096** | 0.0096 < 0.010 | **YES, but by a hair** |
| 3 | overlap 25% − 0% (4-size mean) | nDCG@10 | ×0 | ≈0.055 | ≈0.0096 | — | **YES, by a hair** |
| 2 | slope of E(s) per doubling | nDCG@10 | ×0 | ≈0.067 | ≈0.0116 | 0.0116 > 0.010 | **MARGINAL — likely unresolvable at the bar** |
| 1 | `I` = extremes DiD | nDCG@10 | ×0 | ≈0.25 | **≈0.043** | 4.3× the bar | **NO — declared unreachable in advance** |
| 5 | size 2048 − 256 | nDCG@10 | ×11.5 | 0.214 (meas.) | **0.0371** | effect −0.073 | **YES** |
| 6 | size 256 − 512 | nDCG@10 | ×11.5 | 0.159 (meas.) | **0.0276** | effect +0.031 | **YES, marginally** |
| 10 | size 1024 − 512 | nDCG@10 | ×11.5 | 0.134 (meas.) | **0.0233** | effect −0.020 | **NO — projected unresolvable** |
| 11 | size 2048 − 1024 | nDCG@10 | ×11.5 | 0.115 (meas.) | **0.0200** | effect −0.023 | **YES, marginally** |
| 5 | size 2048 − 256 | recall@100 | ×11.5 | 0.183 (meas.) | **0.0318** | effect −0.035 | **YES, marginally — this is the Leg A head-to-head** |
| 6,10,11 | adjacent steps | recall@100 | ×11.5 | 0.124–0.139 | 0.021–0.024 | effects 0.008–0.015 | **NO — projected unresolvable** |
| — | any contrast | recall@100 | **×0** | 0.062 | 0.0108 | effects ≤ 0.004 | **NO — degenerate, see §4.2** |

**Contrast 1 (`I`) is declared unreachable before the run.** This is Leg A's own process lesson
applied: the extremes DiD is a difference of four cells, its variance compounds, and its floor
is 4.3× the bar whatever the truth. It is kept in the family only because Leg A's family
contained it and the two must be comparable; **it will be reported as UNRESOLVED regardless of
what it returns**, and contrast 2 (the slope form) is the interaction contrast that gets
interpreted — Leg A's recommendation 10.1, followed.

**Three adjacent-step readings are projected unresolvable.** They stay in the family because
Q2's verdict rule (§1) explicitly handles unresolvable steps by *excluding* them rather than
counting them as agreement. Q2 may therefore return "uniform on the steps that resolve, silent
on the rest", and that is a legitimate answer to it.

### 4.2 The degenerate-proportion trap, pre-declared

Measured on the pilot at the ×0 rung, **recall@100 ∈ {0.9962, 1.0}** for all five cells, and
**every pair's `sd_d` is 0.0620** — that is one query flipping. A proportion sitting at exactly
1.0 has a **Wald SE of zero**, and a naive floor computed from it falsely reads "resolvable".
Row 13 of the pilot's own §7 table fell into this trap and had to be caught.

Pre-committed handling:

* **recall@100 and recall@10 at the ×0 rung are declared degenerate before the run** and will
  be reported as **unresolvable by construction**, with the observed proportions shown and no
  δ80 quoted from a Wald SE.
* Any proportion at exactly 0 or 1 anywhere in this run is read on its **Wilson** interval, not
  a Wald SE.
* recall@100 is read for inference **only at the ×11.5 rung**.

---

## 5. Predictions, to be scored automatically

| # | prediction | how it is scored |
|---|---|---|
| **PB1** | **The overlap null replicates.** Contrast 4 at ×0 returns `\|mean\| < 0.010`, CI containing 0, `δ80 < 0.010`. | Q1's three-way rule, §1 |
| **PB2** | **Leg B's size effect is monotone-negative**: all three adjacent steps have a negative point estimate at ×11.5. | signs of contrasts 6(negated), 10, 11 |
| **PB3** | **The size disagreement is uniform**: every adjacent step that resolves on both legs has opposite signs. | Q2's rule, §1 |
| **PB4** | **Q3 replicates with a negative sign**: `\|corr(log2 realised, nDCG@10)\| ≥ 0.70`, exceeding `\|corr(log2 nominal)\|`, and the per-kind residual spread is smaller than the raw per-kind spread. | §1 Q3 |
| **PB5** | **Reranking reorders the grid on Leg B too**: dense↔reranked config-mean correlation < 0.90. | Pearson over the 24 (×0) config means |
| **PB6** | **Reproduction gate**: the five cells shared with the pilot reproduce `legb2_rung_x0.json` and `legb2_sigma.json` **per-query, exactly** (max abs diff 0.0000 on rank, nDCG@10, MRR@10, recall@100). | hard gate, §7 |
| **PB7** | **The semantic `>3000-sentence` fallback rate is higher than Leg A's 12/4,053 = 0.30%**, because Leg B's source documents are ~2× Leg A's median length. | `semantic_cost.json` |

PB6 is a **stop condition**, not a prediction to be scored leniently: a mismatch means the
harness is not the pilot's harness and the whole comparison is void.

---

## 6. Budget, GPU citizenship, and the drop order

### 6.1 Projection

Measured constants: chunk-embed **161k tok/s**, semantic breakpoint **212k tok/s actual**,
semantic breakpoint actual ≈ **9.1× corpus** (notional ≈ 24× corpus over 4 configs), rerank
**~600 pairs/s** (measured on this host today at 256/512/2048-token chunks: 1037 / 786 / 391
pairs/s).

| leg | tokens | projected wall |
|---|---:|---:|
| ×0, 24 configs, chunk embed (≈30 × 3.87 M) | ≈116 M | ≈12 min |
| ×0, semantic breakpoint (actual, cached) | ≈35 M | ≈3 min |
| ×11.5, 12 `token_window` configs (4×48.3 + 4×55.2 + 4×63.9 M) | ≈670 M | ≈69 min |
| rerank ×11.5, 12 configs × 260 q × 100 | 312 k pairs | ≈9 min |
| rerank ×0, 24 configs × 260 q × 100 | 624 k pairs | ≈17 min |
| **total** | **≈821 M actual** | **≈110 min = 1.83 GPU-h** |

Ceiling **2.0 GPU-hours**, checked between configs by the harness's own budget clock.

### 6.2 Drop order if the budget check trips

Pre-committed, so the decision is not made under time pressure mid-run:

1. **the ×0 rerank** (the ×11.5 rerank is the one that answers Leg A's rerank finding);
2. **the ×11.5 `ov25pct` cells** — Q1 is still readable from `12.5% − 0%`, which is the
   adequately-powered contrast on Leg A;
3. **the ×11.5 rung's `ov12_5pct` cells**, leaving the four `ov0pct` size cells, which still
   carry Q2's per-size profile;
4. **the ×0 semantic cells last** — per the task's instruction, semantic is expensive and is
   run last, but it is not to be dropped in preference to anything above.

### 6.3 GPU citizenship

* Six embedding endpoints only, **`:9001`–`:9006`**, `PER_ENDPOINT = 2` in flight (12 global),
  enforced by `stage1_common.Fleet`'s slot queue — not by round-robin guesswork.
* **GPUs 6 and 7 are RESERVED. No endpoint is started on them and nothing is sent to them.**
  Verified 0 MiB before the run and to be re-verified after.
* Crossencoder sidecar `:50052`, ≤ 1 request in flight (sequential), the pilot's policy.
* Cumulative tokens and elapsed logged per config; achieved tok/s reported against the
  measured 161k.
* If projected cost exceeds ~2 GPU-hours at any between-config check, the run **stops and
  reports** rather than continuing.

### 6.4 Store safety

* **Zero store writes.** No Qdrant or Elasticsearch client is constructed in this harness or
  anything it imports; retrieval is exact brute-force cosine over in-memory `numpy`.
* Production `:6333` / `:9200` are **never contacted**.
* Dev-tenant `:24041` / `:24043` are snapshotted (collection/index listings **plus exact
  per-collection counts**) before, mid-run and after; the proof is a SHA-256 equality.
  `stores_before.txt` taken 2026-09-05 before this document was finished.
* Nothing is written under `/rag/`; `/rag/oa` is read-only. All outputs land in
  `phase0/stage1-legB/`.
* Interpreter `/rag/envs/ragstack/bin/python`, `HF_HOME=/rag/cache`, `PYTHONPATH` pinned to
  `/home/wilke/Development/ragstack/python` with `pin_repo()` asserting the resolved
  `ragstack.__file__` in the parent **and in every multiprocessing worker**.

---

## 7. Analysis plumbing held identical to Leg A

So that the two legs' numbers are the same arithmetic:

* `boot_stats` (10,000 resamples, seed 7, percentile CI, two-sided bootstrap p) and `holm`
  are **lifted verbatim** from `../stage1/stage1_report.py`.
* `Fleet`, `make_batches`, `pin_repo`, `atomic_json`, `doc_text` are **imported** from
  `../stage1/stage1_common.py`, not copied.
* `STAGE1_CONFIGS` and `HARD_CAP_TOKENS` are **imported** from the repo's
  `chunking_compare_7way`, never re-declared.
* The chunking recipes per kind, the max-rollup, the top-200 depth and the rerank call are
  Leg A's.
* **Unit of analysis changes from topic to query** (n: 10 → 260) and the metric changes to the
  single-relevant form of §2.2. Those two changes are the only intended differences.

---

## 8. What this run may not conclude

Fixed in advance, and it is the point of the exercise:

* **No config may be pruned**, in either direction, on either leg's evidence. The group's
  direction is to keep the contested size axis intact and to cut only where evidence is
  **uncontested**.
* **Leg B's direction is not bias-free either.** Fix 1 requires the query to name a rare entity
  present in the source section; document scores are a **max-rollup over chunks**; a small
  chunk carrying that entity is exactly what a max-rollup rewards. Leg B is constructed to
  favour localized matching in the same way Leg A is constructed to favour aboutness. Nothing
  in this run can separate the two.
* An agreement between the legs on an axis is evidence that the axis is **uncontested**; it is
  not evidence that either leg's query population is the right one to optimise for.
* Absolute Leg B scores are not comparable with absolute Leg A scores (§2.2).
