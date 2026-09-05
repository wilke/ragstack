# Stage 1 — the chunking grid on Leg B, and what it does to the Leg A disagreement

*Run 2026-09-05 on `coconut` against [`PREREG-stage1-legB.md`](PREREG-stage1-legB.md),
written before any embedding call of this run. Repo `/home/wilke/Development/ragstack`,
main at `d225cea`, pinned past the `/rag/repos` editable-install meta-path finder. Grid =
the committed `chunking_compare_7way.STAGE1_CONFIGS`, imported not re-declared. Predecessors:
[`RESULTS-stage1-legA.md`](../stage1/RESULTS-stage1-legA.md),
[`RESULTS-legB-rerun.md`](../pilots/RESULTS-legB-rerun.md).*

*All 24 configs ran at the judged-only rung and all 12 `token_window` cells at the pilot's
×11.5 rung. 811 M tokens actually embedded, 0 retries in 132,689 requests, 0 store writes,
GPUs 6 and 7 never touched.*

> ## Read this first
>
> **Three things changed shape, and only one of them is the thing this run was sent to
> measure.**
>
> 1. **The overlap null replicates, on uncontested evidence.** Overlap 12.5% − 0% on Leg B is
>    **−0.0040**, CI [−0.0098, +0.0015], with `δ80 = 0.0081` **below** the bar — an
>    *adequately powered* null, the same verdict Leg A returned (−0.0210, δ80 0.041 < its
>    0.05 bar). At the deep rung, overlap moves recall@100 by **exactly 0.0000** at every
>    size. Both legs, both metrics, both adequately powered. **Overlap is the study's
>    first uncontested axis.** (§6.3 states where the replication is weaker.)
> 2. **The size disagreement is NOT uniform — it is one step wide.** Of the three adjacent
>    size steps, the two legs' point estimates point the **same** way at 256→512 and at
>    1024→2048 (finer better on both). They disagree at exactly one step, **512→1024**, and
>    that is also the *only* step Leg A can resolve at all (A **+0.1204**, CI [+0.052,
>    +0.186]; B **−0.0182**, CI [−0.031, −0.006]). Leg A's "coarse wins" is not a trend
>    across the size axis: it is a single 0.12-nDCG jump at 1024 with nothing on either side
>    of it.
> 3. **Behind the reranker, no size contrast resolves on either leg.** Leg B's dense size
>    effect is large and monotone (2048 − 256 = **−0.0754**); reranked it is **−0.0136** with
>    a CI spanning zero. The shrinkage is itself a resolved effect: dense-minus-reranked
>    **−0.0618**, CI [−0.090, −0.034]. On Leg A the one resolvable dense step (+0.1204)
>    collapses to +0.0120, unresolved. **The entire Leg A / Leg B size disagreement is a
>    first-stage phenomenon that the cross-encoder erases**, and the production pipeline
>    reranks.
>
> **This run prunes nothing**, as directed. Its output is the uncontested/contested split in
> §11, not a config short-list.

---

## 1. Answers, in one place

| question | answer |
|---|---|
| **Q1 — does the overlap null replicate on Leg B?** | **YES.** 12.5% − 0% = **−0.0040**, CI [−0.0098, +0.0015], **δ80 = 0.0081 < X_B = 0.010**. 25% − 0% = −0.0020, δ80 0.0069 — also a powered null. The interaction *slope* form: +0.0011, δ80 0.0061 — also a powered null. recall@100 at ×11.5: **±0.0000** at every size. |
| **Q2 — uniform or size-localised?** | **Size-localised, and the localisation is at 512→1024.** Under a **post-hoc tightening** of the pre-registered rule (§7.3) the verdict is *not establishable* — only 1 of 3 steps resolves on both legs. Descriptively the answer is unambiguous: same sign at 256→512 (A −0.024 / B −0.028) and at 1024→2048 (A −0.006 / B −0.029); **opposite** at 512→1024 (A **+0.120** / B **−0.018**). |
| **Q3 — does realised-token size explain the grid on Leg B too?** | **YES, with the pre-registered negative sign.** `corr(log₂ realised median tokens, nDCG@10) = −0.974` vs `−0.870` for nominal size; the per-kind spread collapses **0.0143 → 0.0023** once realised size is controlled (Leg A, identical code: +0.811 vs +0.654, spread 0.1012 → 0.0538). |
| **Behind the reranker** | **No size contrast resolves on either leg.** Leg B ×11.5 dense spread 0.0920 → reranked **0.0276**; dense↔reranked config-mean r = **+0.612** (×11.5) / **+0.767** (×0), against Leg A's +0.553. |
| **The 24-row grid (×0, dense)** | `fixed_tok256_ov12_5pct` 0.9838 · `fixed_tok256_ov0pct` 0.9821 · `sentence_tok256` 0.9820 · `words_tok256` 0.9819 · `semantic_tok256` 0.9806. **Every 256-token config, of every kind, is in the top 6.** |
| **The 24-row grid (×0, reranked)** | `semantic_tok256` 0.9837 · `sentence_tok256` 0.9814 · `words_tok512` 0.9798 · **`fixed_tok512` (shipping) 0.9770**, up from dense rank 14 to reranked rank 4. |
| **Reproduction gate (PB6)** | **PASS, exactly.** Five grid cells re-chunked and re-embedded from scratch reproduce the pilot's per-query rank, nDCG@10, MRR@10 and recall@100 with **max \|diff\| = 0.0e+00** over 396 queries, at **both** rungs, and identical chunk counts and token totals. The ×11.5 corpus file-list hash matches the pilot's `c6fb04503fdee62a`. |
| **Cost** | **811 M actual tokens** (870 M notional) in **76.6 min** of fleet embedding = **1.28 GPU-h**; chunk-embed leg **175k tok/s** against the measured 161k model (109%). **0 retries in 132,689 requests.** |
| **Stores** | Qdrant `:24041` and ES `:24043` **byte-identical** before / mid / after, SHA-256 verified. `:6333` / `:9200` never contacted. |

---

## 2. The reproduction gate (PB6) — PASS, at both rungs

Five of the grid's cells are the pilot's five cells. They were re-chunked and **re-embedded
from scratch** by this harness — nothing in `legb2_rung_x0.json` or `legb2_sigma.json` is read
by `legb_grid.py`, only compared against afterwards by
[`verify_pilot.py`](verify_pilot.py).

| rung | cells | queries compared | max \|diff\| on rank / nDCG@10 / MRR@10 / recall@100 | chunk count + token total |
|---|---:|---:|---|---|
| ×0 (400 docs) | 5 | 396 each | **0.0e+00** | identical |
| ×11.5 (5,000 docs) | 5 | 396 each | **0.0e+00** | identical |

The ×11.5 corpus was rebuilt with the pilot's own generator and seed
(`distractors(4600, exclude=sources, rng=Random(20260911))`) and its file-list
`sha256[:16] = c6fb04503fdee62a` **asserted against the pilot's recorded value before a single
token was embedded** — the harness refuses to run on a mismatch. So this is not "similar
corpus, similar numbers": it is the same 5,000 files, the same chunker at the same commit, an
independent re-embedding on a fleet batched differently, and a bit-identical result.

That is what licenses every side-by-side in this document.

---

## 3. The judged set, and what "recall" means on Leg B

**Construction.** 260 accepted queries from the Leg B re-run (`legb2_final.json`,
`accepted == True`) — LLM-written, rule-gated for shape and rare-entity specificity, and
passed by an LLM abstract-answerability verifier. **Exactly one relevant document per query**:
the article whose deep section the query was written from. Binary grade.

| metric | definition here | Leg A's definition |
|---|---|---|
| nDCG@10 | `1/log2(rank+1)` if the gold document is in the top 10, else 0 | graded DCG over ~109 relevants/topic |
| recall@k | **1 if the gold document is in the top k, else 0** — a per-query *hit rate* | fraction of ~109 relevants retrieved |
| MRR@10 | `1/rank` if rank ≤ 10 | same |

Document score is the **max over the document's chunks** of the query·chunk cosine (Leg A's
rollup, unchanged), depth 200.

**Three consequences that must travel with every number below.**

* **Leg B is an easy task.** nDCG@10 spans 0.931–0.984 at the judged-only rung and 0.868–0.960
  at ×11.5. Total headroom is 1–13 nDCG points against Leg A's 0.40–0.63. **An absolute Leg B
  score is not comparable with an absolute Leg A score**, and 0.02 means something very
  different on the two legs. This is why the bar is re-derived in §5.
* **Most paired differences are exactly zero.** With one relevant document, two configs agree
  on the great majority of queries; 205–253 of 260 per-contrast differences are ties. The
  variance comes from the ~5–20% of queries that flip, which is why the sign criterion is
  stated over *non-zero* differences.
* **recall@100 is degenerate at the judged-only rung and was declared so before the run**
  (PREREG §4.2). Measured: 0.9923–1.0000, with several cells at exactly 1.0 and every pair's
  `sd_d = 0.062` — one query flipping. A Wald SE at p = 1.0 is **zero**, which is precisely the
  trap that makes a naive floor read "resolvable"; the Wilson lower bound on 260/260 is 98.6%.
  **recall@100 is read for inference at ×11.5 only.**

**Two rungs, both the pilot's.** ×0 = the 400 sampled source articles (query-independent: the
140 sources whose query was rejected are topically matched deep science, i.e. harder
distractors than a random draw). ×11.5 = those 400 plus the pilot's own 4,600 seeded PMC OA
distractors.

**Deviation to declare.** Only the **12 `token_window` cells** ran at ×11.5. Both headline
questions are token_window-only questions (the overlap axis exists only there; Q2's per-size
panel is that block), and running all 24 at ×11.5 would have cost ≈ 3.1 GPU-h against a
~2 GPU-h ceiling — the whole deep-rung semantic breakpoint pass is what the restriction
removes. This was pre-registered in PREREG §2.4, with the drop order, before the run.

**Carried-forward limitations, named not fixed.** The pilot's §8.3 requires three fixes
*before assembly of the full 1,500-query set*: 2/400 sampled articles are retracted (both
accepted), 35/260 accepted sources are not `research-article`, and the verifier lets through
roughly one bad accept in eight on a human read. **None is fixed here** — changing the query
set would destroy the reproduction gate of §2. Every number below inherits them.

**Robustness.** Every contrast was also computed on all 396 generated queries. Directions and
verdicts are unchanged; magnitudes shrink about 30% as the 136 rejected queries dilute the
signal, and the floors shrink with √n:

| contrast | accepted (n = 260) | all generated (n = 396) |
|---|---|---|
| overlap 12.5% − 0% (×0) | −0.0040, δ80 0.0081 | −0.0026, δ80 0.0054 |
| size 1024 − 512 (×11.5) | −0.0182, δ80 0.0180 | −0.0125, δ80 0.0119 |
| size 2048 − 256 (×11.5) | −0.0754, δ80 0.0357 | −0.0522, δ80 0.0243 |

---

## 4. The grid

### 4.1 All 24 configs at the judged-only rung (n = 260, dense)

`fill` = median realised tokens ÷ nominal size. **Descriptive — neighbouring rows differ by
less than the noise floor and are not ordered claims.** Full table with recall@10 in
[`tables-legB.md`](tables-legB.md).

| rank | config | kind | size | ovl | c/doc | med tok | fill | nDCG@10 | R@100 | MRR@10 | reranked |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `fixed_tok256_ov12_5pct` | token_window | 256 | 12.5% | 43.6 | 256 | 1.00 | **0.9838** | 1.0000 | 0.9808 | 0.9705 |
| 2 | `fixed_tok256_ov0pct` | token_window | 256 | 0% | 38.3 | 256 | 1.00 | **0.9821** | 1.0000 | 0.9788 | 0.9736 |
| 3 | `sentence_tok256_ov12_5pct` | sentence | 256 | 12.5% | 45.0 | 229 | 0.89 | **0.9820** | 1.0000 | 0.9785 | 0.9814 |
| 4 | `words_tok256_ov12_5pct` | words | 256 | 12.5% | 67.3 | 164 | 0.64 | **0.9819** | 1.0000 | 0.9771 | 0.9740 |
| 5 | `semantic_tok256_ov12_5pct` | semantic | 256 | 12.5% | 48.9 | 255 | 1.00 | **0.9806** | 1.0000 | 0.9780 | **0.9837** |
| 6 | `fixed_tok256_ov25pct` | token_window | 256 | 25% | 50.6 | 256 | 1.00 | 0.9770 | 1.0000 | 0.9721 | 0.9743 |
| 7 | `words_tok512_ov12_5pct` | words | 512 | 12.5% | 33.7 | 328 | 0.64 | 0.9737 | 1.0000 | 0.9677 | 0.9798 |
| 8 | `semantic_tok2048_ov12_5pct` | semantic | 2048 | 12.5% | 22.3 | 351 | 0.17 | 0.9703 | 1.0000 | 0.9646 | 0.9737 |
| 9 | `semantic_tok512_ov12_5pct` | semantic | 512 | 12.5% | 29.6 | 324 | 0.63 | 0.9700 | 1.0000 | 0.9652 | 0.9724 |
| 10 | `fixed_tok512_ov0pct` | token_window | 512 | 0% | 19.4 | 512 | 1.00 | 0.9676 | 0.9962 | 0.9610 | 0.9667 |
| 11 | `semantic_tok1024_ov12_5pct` | semantic | 1024 | 12.5% | 23.3 | 350 | 0.34 | 0.9674 | 1.0000 | 0.9619 | 0.9737 |
| 12 | `fixed_tok512_ov25pct` | token_window | 512 | 25% | 25.3 | 512 | 1.00 | 0.9647 | 1.0000 | 0.9583 | 0.9645 |
| 13 | `sentence_tok512_ov12_5pct` | sentence | 512 | 12.5% | 22.1 | 479 | 0.94 | 0.9645 | 1.0000 | 0.9590 | 0.9720 |
| 14 | **`fixed_tok512`** (shipping) | token_window | 512 | 12.5% | 21.9 | 512 | 1.00 | 0.9585 | 1.0000 | 0.9498 | **0.9770** |
| 15 | `sentence_tok1024_ov12_5pct` | sentence | 1024 | 12.5% | 11.1 | 979 | 0.96 | 0.9575 | 0.9962 | 0.9528 | 0.9699 |
| 16 | `words_tok1024_ov12_5pct` | words | 1024 | 12.5% | 17.0 | 657 | 0.64 | 0.9564 | 0.9923 | 0.9483 | 0.9618 |
| 17 | `fixed_tok1024_ov25pct` | token_window | 1024 | 25% | 12.7 | 1024 | 1.00 | 0.9515 | 0.9962 | 0.9449 | 0.9636 |
| 18 | `fixed_tok1024_ov12_5pct` | token_window | 1024 | 12.5% | 11.1 | 1024 | 1.00 | 0.9503 | 0.9923 | 0.9452 | 0.9618 |
| 19 | `fixed_tok1024_ov0pct` | token_window | 1024 | 0% | 9.9 | 1024 | 1.00 | 0.9482 | 1.0000 | 0.9414 | 0.9653 |
| 20 | `words_tok2048_ov12_5pct` | words | 2048 | 12.5% | 8.7 | 1312 | 0.64 | 0.9478 | 0.9923 | 0.9386 | 0.9581 |
| 21 | `fixed_tok2048_ov0pct` | token_window | 2048 | 0% | 5.2 | 2048 | 1.00 | 0.9413 | 0.9962 | 0.9345 | 0.9666 |
| 22 | `fixed_tok2048_ov25pct` | token_window | 2048 | 25% | 6.5 | 2048 | 1.00 | 0.9379 | 0.9923 | 0.9293 | 0.9584 |
| 23 | `sentence_tok2048_ov12_5pct` | sentence | 2048 | 12.5% | 5.7 | 1979 | 0.97 | 0.9311 | 0.9923 | 0.9202 | 0.9602 |
| 24 | `fixed_tok2048_ov12_5pct` | token_window | 2048 | 12.5% | 5.7 | 2048 | 1.00 | **0.9307** | 0.9923 | 0.9221 | 0.9650 |

**Read the ordering as a realised-size ordering, not a kind ordering.** The top six rows are
every 256-token config of every one of the four kinds plus one 256 overlap variant; the bottom
five are 2048s. The four `semantic` rows sit at 8/9/11 and 5 — mid-table — because semantic
emits ~350-token blocks whatever its cap says, so it is a *fine* config wearing a coarse
label, and on Leg B fine is what wins. §8 makes this quantitative.

**Leg A's ranking is close to the exact reverse of this one.** `fixed_tok512`, rank 21 of 24
on Leg A, is rank 14 here; `sentence_tok2048`, Leg A's rank 1, is rank 23 here.

### 4.2 The 12 `token_window` cells at ×11.5 (5,000 docs, n = 260, dense)

| rank | config | size | ovl | chunks | nDCG@10 | R@100 | MRR@10 | reranked |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `fixed_tok256_ov0pct` | 256 | 0% | 191,430 | **0.9595** | 0.9923 | 0.9536 | 0.9390 |
| 2 | `fixed_tok256_ov12_5pct` | 256 | 12.5% | 217,806 | 0.9582 | 0.9923 | 0.9507 | 0.9291 |
| 3 | `fixed_tok256_ov25pct` | 256 | 25% | 252,989 | 0.9518 | 0.9923 | 0.9463 | 0.9431 |
| 4 | `fixed_tok512_ov25pct` | 512 | 25% | 126,641 | 0.9339 | 0.9846 | 0.9252 | 0.9340 |
| 5 | `fixed_tok512_ov0pct` | 512 | 0% | 96,812 | 0.9291 | 0.9846 | 0.9184 | 0.9364 |
| 6 | **`fixed_tok512`** | 512 | 12.5% | 109,626 | 0.9214 | 0.9846 | 0.9096 | **0.9424** |
| 7 | `fixed_tok1024_ov25pct` | 1024 | 25% | 63,688 | 0.9145 | 0.9692 | 0.9054 | 0.9279 |
| 8 | `fixed_tok1024_ov0pct` | 1024 | 0% | 49,612 | 0.9094 | 0.9692 | 0.8988 | 0.9310 |
| 9 | `fixed_tok1024_ov12_5pct` | 1024 | 12.5% | 55,677 | 0.9060 | 0.9654 | 0.8943 | 0.9399 |
| 10 | `fixed_tok2048_ov25pct` | 2048 | 25% | 32,279 | 0.8895 | 0.9577 | 0.8759 | 0.9207 |
| 11 | `fixed_tok2048_ov0pct` | 2048 | 0% | 26,077 | 0.8863 | 0.9577 | 0.8692 | 0.9341 |
| 12 | `fixed_tok2048_ov12_5pct` | 2048 | 12.5% | 28,727 | **0.8675** | 0.9500 | 0.8504 | 0.9155 |

Distractors triple the spread (0.0531 → 0.0920) without changing the ordering: size dominates,
overlap does nothing, and the three cells at each size sit within 0.02 of each other.

---

## 5. The pre-registered family, and the bar

Bar **X_B = 0.010 nDCG@10**, derived in PREREG §3 as 22.3% of the pilot's realised ×0
five-cell spread — the same fraction of its own leg's spread that Leg A's 0.05 is of Leg A's
0.224. `δ80 = 2.802 · sd_d / √n`. `resolved` = |mean| ≥ X_B **and** CI excludes 0 **and**
≥ 60% of non-zero paired differences agree in sign **and** Holm across the 11 **and**
`δ80 ≤ |mean|`.

| # | contrast | rung | mean | 95% CI | δ80 | signs (non-zero) | Holm p | verdict |
|---|---|---|---:|---|---:|---:|---:|---|
| 1 | `I` = E(256) − E(2048) | ×0 | −0.0017 | [−0.0149, +0.0110] | 0.0187 | 50% of 18 | 1.000 | **UNRESOLVED** — declared unreachable in advance |
| 2 | slope of E(s) per doubling | ×0 | +0.0011 | [−0.0030, +0.0055] | **0.0061** | 55% of 29 | 1.000 | **NULL at the bar** |
| 3 | overlap 25% − 0% | ×0 | −0.0020 | [−0.0070, +0.0026] | **0.0069** | 57% of 30 | 1.000 | **NULL at the bar** |
| 4 | **overlap 12.5% − 0%** | ×0 | **−0.0040** | **[−0.0098, +0.0015]** | **0.0081** | 63% of 27 | 0.666 | **NULL at the bar** |
| 5 | size 2048 − 256 | ×11.5 | **−0.0754** | [−0.1015, −0.0519] | 0.0357 | 95% of 55 | 0.0011 | **RESOLVED** |
| 6 | size 256 − 512 | ×11.5 | **+0.0284** | [+0.0134, +0.0449] | 0.0225 | 79% of 38 | 0.0011 | **RESOLVED** |
| 10 | size 1024 − 512 | ×11.5 | **−0.0182** | [−0.0313, −0.0062] | 0.0180 | 68% of 37 | 0.0144 | **RESOLVED** |
| 11 | size 2048 − 1024 | ×11.5 | **−0.0289** | [−0.0426, −0.0165] | 0.0182 | 84% of 45 | 0.0011 | **RESOLVED** |
| 7 | sentence512 − fixed512 | ×0 | +0.0061 | [−0.0006, +0.0141] | 0.0103 | 86% of 7 | 0.443 | UNRESOLVED (δ80 ≥ bar) |
| 8 | words512 − fixed512 | ×0 | +0.0152 | [+0.0041, +0.0272] | 0.0167 | 82% of 17 | 0.028 | UNRESOLVED — CI excludes 0 but \|effect\| < δ80 |
| 9 | semantic512 − fixed512 | ×0 | +0.0116 | [−0.0022, +0.0268] | 0.0210 | 62% of 16 | 0.513 | UNRESOLVED (δ80 ≥ bar) |

**Contrast 1 was declared unreachable before the run and is reported as UNRESOLVED whatever it
returned** — Leg A's process lesson (a difference of four cells compounds its variance) applied
in advance rather than discovered afterwards. The slope form (#2) is the interaction contrast
that gets interpreted, exactly as Leg A recommended, and at `δ80 = 0.0061` it is the tightest
null in the study.

### 5.1 Bar sensitivity — the one place it matters

The pre-registered 0.010 was derived from the pilot's five ×0 cells. Re-deriving it from this
run's own realised spreads at the same 22.3%: **0.0118** from the ×0 24-config spread (0.0531),
**0.0205** from the ×11.5 12-cell spread (0.0920).

* At **0.0118**, no verdict in the table changes.
* At **0.0205** (a rung-matched bar for the four contrasts read at ×11.5), contrast **10**
  (size 1024 − 512, −0.0182) falls just below the practical bar and becomes *detected but
  sub-threshold* rather than *resolved*. Contrasts 5, 6 and 11 are unaffected.
* No overlap or interaction verdict is sensitive to any of the three bars: their δ80 values
  (0.0061–0.0081) sit below all of them.

Contrast 10 is the step Q2 turns on, so this sensitivity is stated in §7 rather than buried.
Its **sign** is not bar-sensitive: CI [−0.031, −0.006], Holm p = 0.014.

---

## 6. Q1 — the overlap null replicates, and this is the run's one clean prune-able fact

### 6.1 The panels

nDCG@10, means over 260 accepted queries.

| size | ×0: 0% | 12.5% | 25% | E(s) | | ×11.5: 0% | 12.5% | 25% | E(s) |
|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 256 | 0.9821 | 0.9838 | 0.9770 | −0.0051 | | 0.9595 | 0.9582 | 0.9518 | −0.0078 |
| 512 | 0.9676 | 0.9585 | 0.9647 | −0.0029 | | 0.9291 | 0.9214 | 0.9339 | +0.0049 |
| 1024 | 0.9482 | 0.9503 | 0.9515 | +0.0033 | | 0.9094 | 0.9060 | 0.9145 | +0.0050 |
| 2048 | 0.9413 | 0.9307 | 0.9379 | −0.0034 | | 0.8863 | 0.8675 | 0.8895 | +0.0032 |

recall@100 at ×11.5, the metric on which the legs were said to contradict each other:

| size | 0% | 12.5% | 25% | 25% − 0% |
|---:|---:|---:|---:|---:|
| 256 | 0.9923 | 0.9923 | 0.9923 | **+0.0000** |
| 512 | 0.9846 | 0.9846 | 0.9846 | **+0.0000** |
| 1024 | 0.9692 | 0.9654 | 0.9692 | **+0.0000** |
| 2048 | 0.9577 | 0.9500 | 0.9577 | **+0.0000** |

Overlap at 25% changes the retrieved-at-100 hit rate by **exactly zero at every size**, on a
5,000-document corpus, with 260 queries. Leg A's equivalent was |Δ| ≤ 0.0033.

### 6.2 The two legs side by side

| contrast | Leg A (n = 10 topics) | Leg A δ80 vs bar 0.05 | Leg B (n = 260) | Leg B δ80 vs bar 0.010 | joint verdict |
|---|---:|---|---:|---|---|
| overlap 12.5% − 0%, nDCG@10 (B at ×0) | −0.0210 [−0.048, +0.007] | 0.0413 → **powered** | −0.0040 [−0.0098, +0.0015] | 0.0081 → **powered** | **null on both legs** |
| overlap 25% − 0%, nDCG@10 (B at ×0) | −0.0273 [−0.069, +0.022] | 0.0682 → unresolved | −0.0020 [−0.0070, +0.0026] | 0.0069 → **powered** | null on Leg B; Leg A cannot say |
| interaction slope, nDCG@10 (B at ×0) | +0.0101 [−0.023, +0.044] | 0.0501 → unresolved | +0.0011 [−0.0030, +0.0055] | 0.0061 → **powered** | null on Leg B; Leg A borderline |
| overlap 12.5% − 0%, recall@100 (B at ×11.5) | +0.0022 [−0.005, +0.010] | 0.0106 → **powered** | −0.0029 [−0.0087, +0.0038] | 0.0089 → **powered** | **null on both legs** |
| overlap 25% − 0%, recall@100 (B at ×11.5) | +0.0002 [−0.010, +0.010] | 0.0151 → unresolved | +0.0000 [−0.0058, +0.0058] | 0.0085 → **powered** | null on Leg B |

**On the two contrasts where both legs are adequately powered — the 12.5% main effect on
nDCG@10 and on recall@100 — both legs return a null, and the point estimates are of opposite
sign and both within 0.004 of zero.** The legs agree.

**What overlap costs.** Measured chunk inflation on the ×11.5 corpus:
1.000 / 1.138 / 1.322 at size 256, 1.000 / 1.132 / 1.308 at 512 — essentially the theoretical
1/(1−f). At size 256 that is **61,559 extra vectors on a 5,000-document corpus** for a change
in nDCG@10 of −0.0077 and a change in recall@100 of 0.0000.

### 6.3 Where the replication is weaker, stated plainly

The pre-registered primary rung for the overlap contrasts is **×0**, and there the null is
clean. The ×11.5 **replication on nDCG@10 is not powered to the bar**, and one of its two
contrasts has a CI that just excludes zero:

| contrast, nDCG@10 at ×11.5 | mean | 95% CI | δ80 | status |
|---|---:|---|---:|---|
| overlap 12.5% − 0% | **−0.0078** | [−0.0162, **−0.0002**] | **0.0114** | **unresolved** — δ80 above the 0.010 bar, and \|effect\| < δ80 |
| overlap 25% − 0% | +0.0013 | [−0.0059, +0.0087] | 0.0105 | unresolved — δ80 0.0005 above the bar |

Read this as a **direction, not a result**: the 12.5% point estimate at the deep rung is
negative, i.e. it points the same way as Leg A's −0.0210 and this run's ×0 −0.0040 — *overlap
mildly hurts* — and its magnitude is still under the bar. It does not weaken Q1, whose reading
was assigned to ×0 in advance and whose recall@100 replication at ×11.5 **is** a powered null
on both contrasts (§6.2). But it is not a second powered nDCG@10 null and this document does
not claim one.

> **Q1 answer: the overlap null replicates on Leg B, with a power floor (0.0081) below the
> bar (0.010), on the same contrast Leg A powered. Overlap is the study's first uncontested
> axis. Dropping the two non-zero overlap rungs takes the token_window block from 12 cells to
> 4 without touching the disputed size axis.**

---

## 7. Q2 — the size disagreement is one step wide

### 7.1 The per-size profile, both legs

Marginal means over the three overlap fractions, nDCG@10:

| size | Leg A (CDS, 4,053 docs) | Leg B ×11.5 (5,000 docs) |
|---:|---:|---:|
| 256 | 0.5045 | 0.9565 |
| 512 | 0.4808 | 0.9281 |
| 1024 | **0.6011** | 0.9100 |
| 2048 | 0.5949 | 0.8811 |

Leg B is **monotone decreasing**. Leg A is **not monotone increasing** — it *falls* from 256 to
512, jumps 0.12 at 1024, and falls again at 2048.

### 7.2 Step by step, same bootstrap on both legs

Leg A recomputed from `../stage1/report1.json` with this file's code, so both columns are the
same arithmetic. Status is read against each leg's **own** bar (A: 0.05; B: 0.010) — a single
absolute bar would be meaningless when Leg B's whole headroom is 1–13 nDCG points.

| adjacent step | Leg A mean [CI] | Leg A δ80 | Leg A status | Leg B mean [CI] | Leg B δ80 | Leg B status | signs |
|---|---:|---:|---|---:|---:|---|---|
| 512 − 256 | −0.0238 [−0.090, +0.042] | 0.0994 | unresolved | **−0.0284** [−0.045, −0.013] | 0.0225 | **effect** | **same** (both: finer better) |
| **1024 − 512** | **+0.1204** [+0.052, +0.186] | 0.1008 | **effect** | **−0.0182** [−0.031, −0.006] | 0.0180 | **effect** | **OPPOSITE** |
| 2048 − 1024 | −0.0062 [−0.044, +0.034] | 0.0586 | unresolved | **−0.0289** [−0.043, −0.017] | 0.0182 | **effect** | **same** (both: finer better) |
| 2048 − 256 (extremes) | +0.0904 [−0.027, +0.223] | 0.1872 | unresolved | **−0.0754** [−0.102, −0.052] | 0.0357 | **effect** | — (A unresolved) |

Same table on **recall@100** — Leg B at ×11.5 only:

| step | Leg A mean [CI] | Leg A δ80 | Leg B mean [CI] | Leg B δ80 | status |
|---|---:|---:|---:|---:|---|
| 512 − 256 | −0.0081 [−0.017, +0.002] | 0.0139 | −0.0077 [−0.021, +0.001] | 0.0160 | both sub-floor; **same sign** |
| 1024 − 512 | +0.0302 [+0.010, +0.053] | 0.0333 | −0.0167 [−0.030, −0.005] | 0.0178 | A sub-floor, B sub-floor; **opposite sign** |
| 2048 − 1024 | +0.0212 [+0.003, +0.039] | 0.0266 | −0.0128 [−0.026, −0.003] | 0.0167 | both sub-floor; opposite sign |
| 2048 − 256 | +0.0432 [+0.015, +0.076] | **0.0466** | **−0.0372** [−0.060, −0.018] | 0.0313 | A **sub-floor**, B **effect** |

**A correction to the pilot's headline.** RESULTS-legB-rerun.md §6.3 called the recall@100
extremes contrast "the head-to-head that does stand — both legs resolve". Recomputed here with
the identical δ80 convention on both legs, **Leg A's side does not clear its own power floor**:
+0.0432 against `δ80 = 0.0466`. Its CI does exclude zero, so it is a detection; but by the
project's own reachability rule — the rule that caught six of fifteen readings in the Leg B
round — it is *at* the design's resolution, not above it. The head-to-head is therefore
weaker than advertised, and it is the **nDCG@10 1024→512 step**, not the recall@100 extremes,
that is the study's one genuine both-legs-resolvable disagreement.

### 7.3 The verdict

> **Q2 answer.** **Not establishable** by the (post-hoc tightened) rule below: only 1 of 3
> adjacent steps resolves on both legs, and two-or-more are needed to distinguish "uniform"
> from "size-localised". By the point estimates, the answer is not close:
>
> * at **256→512** both legs say finer is better (A −0.024, B −0.028);
> * at **1024→2048** both legs say finer is better (A −0.006, B −0.029);
> * at **512→1024** they disagree, and disagree by a lot (A **+0.120**, B **−0.018**).
>
> **The disagreement is not a disagreement about the size axis. It is a disagreement about one
> step, and Leg A's whole "coarse wins" direction rests on that single step** — its extremes
> contrast (+0.0904) does not resolve, and neither of its other two steps does.

**A pre-registration deviation, declared.** PREREG §1 Q2's literal rule reads "**UNIFORM** iff
every step whose verdict is resolvable on *both* legs has opposite signs". With exactly one
both-resolvable step, that rule returns **UNIFORM vacuously** — a one-step "uniform" verdict
about a three-step axis, which is not a claim anyone should make. The `≥ 2 both-resolvable
steps` requirement was therefore **added after seeing the data**, in the conservative
direction: it turns a vacuous positive into "not establishable". The literal rule's answer is
recorded here so the reader can apply whichever they prefer; the *substance* — one-step
localisation at 512→1024 — is the same under both, and it is descriptive either way.

Three caveats on this verdict, all real:

1. **Leg A's two "same sign" steps are unresolved on Leg A**, at n = 10 with floors of 0.06–0.10.
   Agreement in an unresolved point estimate is weak evidence. What is *not* weak is the
   negative statement: **Leg A has no resolvable evidence that coarser is better anywhere
   except at 512→1024.**
2. **Contrast 10 is bar-sensitive** (§5.1). Against a rung-matched 0.0205 bar it is detected
   but sub-threshold, which would drop the both-legs-resolvable count to zero and make Q2
   *entirely* descriptive. Its sign and its Holm-corrected significance are not bar-sensitive.

---

## 8. Behind the reranker — the result that reframes the whole disagreement

Leg A found dense↔reranked config-rank correlation of only +0.553 and warned that "a config
chosen on dense nDCG is not necessarily the config that wins after reranking". Measured here
over `bge-reranker-v2-m3` on the top-100 documents' winning chunk texts, 936,000 pairs:

| | Leg A | Leg B ×0 | Leg B ×11.5 |
|---|---:|---:|---:|
| configs | 24 | 24 | 12 |
| dense↔reranked config-mean r | **+0.553** | **+0.767** | **+0.612** |
| dense spread → reranked spread | 0.2240 → 0.2053 | 0.0531 → 0.0255 | 0.0920 → **0.0276** |

On Leg B the reranker does not merely reorder — it **collapses** the grid. And it collapses it
*in one direction*: the coarse configs gain and the fine configs lose.

| ×11.5 config | dense | reranked | Δ |
|---|---:|---:|---:|
| `fixed_tok256_ov12_5pct` | 0.9582 | 0.9291 | **−0.0291** |
| `fixed_tok256_ov0pct` | 0.9595 | 0.9390 | −0.0206 |
| `fixed_tok512` (shipping) | 0.9214 | 0.9424 | +0.0209 |
| `fixed_tok1024_ov12_5pct` | 0.9060 | 0.9399 | +0.0339 |
| `fixed_tok2048_ov0pct` | 0.8863 | 0.9341 | **+0.0478** |
| `fixed_tok2048_ov12_5pct` | 0.8675 | 0.9155 | **+0.0480** |

### 8.1 Every size contrast becomes unresolved, on both legs

| contrast | Leg B ×11.5 dense | Leg B ×11.5 **reranked** | Leg A dense | Leg A **reranked** |
|---|---:|---:|---:|---:|
| size 2048 − 256 | **−0.0754** *(effect)* | −0.0136 [−0.042, +0.013] *(unresolved)* | +0.0904 *(unresolved)* | +0.0970 *(unresolved)* |
| size 1024 − 512 | **−0.0182** *(effect)* | −0.0047 [−0.023, +0.013] *(unresolved)* | **+0.1204** *(effect)* | +0.0120 *(unresolved)* |
| size 2048 − 1024 | **−0.0289** *(effect)* | −0.0095 [−0.022, +0.003] *(unresolved)* | −0.0062 *(unresolved)* | +0.0582 *(unresolved)* |
| size 256 − 512 | **+0.0284** *(effect)* | −0.0005 [−0.020, +0.020] *(unresolved)* | +0.0238 *(unresolved)* | −0.0269 *(unresolved)* |
| overlap 12.5% − 0% | −0.0078 [−0.016, −0.000] *(unresolved, δ80 0.0114)* | −0.0034 [−0.015, +0.008] *(unresolved)* | −0.0210 *(null)* | −0.0158 *(unresolved)* |

**The shrinkage is itself a measured, resolved effect, not a power artefact.** Testing the
per-query difference (dense contrast) − (reranked contrast) directly:

| rung | contrast | dense − reranked | 95% CI | δ80 | verdict |
|---|---|---:|---|---:|---|
| ×11.5 | size 2048 − 256 | **−0.0618** | [−0.0902, −0.0340] | 0.0403 | **RESOLVED** |
| ×0 | size 2048 − 256 | **−0.0349** | [−0.0571, −0.0145] | 0.0303 | **RESOLVED** |

Reranking removes **82%** of Leg B's extreme size effect (−0.0754 → −0.0136) at ×11.5, and the
removal clears its own power floor.

> **Behind the reranker, neither leg has resolvable evidence that chunk size matters.** Leg B's
> fine-wins direction and Leg A's one coarse-wins step both evaporate. On Leg A this is partly
> a power statement (n = 10, reranked floors 0.08–0.17); on Leg B it is not — n = 260, floors
> 0.018–0.040, and the point estimates themselves shrink five-fold.

The step-3 label is kept: **reranked numbers rank arms; they do not grade the product.** But
the production pipeline reranks, and a size recommendation made on dense scores would be
recommending something the pipeline then undoes.

---

## 9. Q3 — realised chunk tokens, not nominal size and not kind, on both legs

> **Exploratory, not pre-registered for inference.** The correlations are across config means
> that share the same queries and are not independent observations. PREREG §1 Q3 fixed the
> expected sign as **negative** on Leg B in advance, so that a large negative `r` could not be
> re-read as a falsification after the fact.

Identical code applied to both legs' 24 config means:

| | Leg A (CDS) | Leg B (×0) |
|---|---:|---:|
| `corr(log₂ **nominal** size, nDCG@10)` | +0.654 | −0.870 |
| `corr(log₂ **realised** median tokens, nDCG@10)` | **+0.811** | **−0.974** |
| same, recall@100 / recall@10 | +0.891 | −0.952 |
| per-kind mean spread, **raw** | 0.1012 | 0.0143 |
| per-kind mean spread, **residual about the realised-size fit** | 0.0538 (−47%) | **0.0023 (−84%)** |

Fit on Leg B: `nDCG@10 ≈ 1.0896 − 0.0140 × log₂(realised median tokens)`.

Per-kind residuals on Leg B: `semantic` −0.0016, `words` −0.0011, `sentence` +0.0004,
`token_window` +0.0007 — **a total spread of 0.0023 against a bar of 0.010 and a δ80 of
0.010–0.021 on the kind contrasts.** Once you know how many tokens land in a chunk, the
chunking *method* is invisible on Leg B.

**This is the strongest cross-leg agreement in the study.** Both legs: realised beats nominal,
and kind nearly vanishes once realised size is controlled — on Leg B almost completely. The
two legs disagree about the *sign of the slope*, not about the *variable*.

It also explains three things that look like kind effects and are not:

* `semantic` ranks 5/8/9/11 here and 16/18/20/24 on Leg A **for the same reason**: it emits
  ~350-token blocks whatever its cap says (fill 0.17 at a nominal 2048). It is a fine chunker
  mislabelled, which is a win on Leg B and a loss on Leg A.
* `words_tok512` (median 328 tokens) beats `fixed_tok512` (median 512) here by +0.0152 and
  loses to it on Leg A by −0.0132 — a realised-size difference wearing a kind label.
* `sentence_tok2048` is Leg A's rank 1 and Leg B's rank 23; its realised median is 1,979
  tokens, i.e. it really is a coarse config on both legs.

**Consequence for the study's shape.** If both legs are functions of realised tokens, the
recommendation is expressible as a **monotone function of realised median chunk tokens
conditioned on query target**, not as a config short-list — and the two legs' slopes
(+0.0447/doubling on Leg A, −0.0140/doubling on Leg B) are the two endpoints a
query-mix-weighted recommendation would interpolate between. That is a stage-2 hypothesis, not
a result; it needs pre-registering with a parameterisation by realised tokens, which is
exactly what Leg A's recommendation 10.2 asked for.

---

## 10. Cost, throughput, GPU citizenship, and the store proof

### 10.1 Measured cost

| leg | tokens | wall | achieved | requests | retries |
|---|---:|---:|---:|---:|---:|
| ×0, 24 configs, chunk embed | 103 M | 8.6 min | **198k tok/s** | 17,982 | **0** |
| ×11.5, 12 configs, chunk embed | 666 M | 64.8 min | **171k tok/s** | 104,822 | **0** |
| ×0 semantic breakpoint pass | 42 M actual (**101 M notional**) | 3.2 min | 218k tok/s actual | 9,885 | **0** |
| **fleet total** | **811 M actual** (870 M notional) | **76.6 min = 1.28 GPU-h** | **175k tok/s** | **132,689** | **0** |
| CPU chunking (48–64 procs) | — | 5.5 min | — | — | — |
| scoring + reranking leg (936,000 rerank pairs) | — | 49.4 min | — | — | — |
| **wall clock, end to end** | | **2 h 09 min** | | | |

**Against the ~2 GPU-hour ceiling: the SFR embedding fleet consumed 1.28 h.** The scoring +
reranking leg (49.4 min) is a mix of CPU `numpy` cosine and crossencoder GPU time and the
harness does not separate them; attributing all of it to GPU gives an upper bound of **2.10 h**,
attributing none gives 1.28 h. Measured crossencoder rates on this host today were 1,037 /
786 / 391 pairs/s at 256- / 512- / 2048-token chunks, which puts the crossencoder share of
that leg at roughly 60%, i.e. a realistic total of **≈ 1.8 GPU-h**. The projection in PREREG
§6.1 was 1.83 GPU-h; the drop order was never triggered and no config was dropped.

**Against the 161k tok/s model:** the deep-rung chunk-embed leg came in at **171k**, 106% of
model, with zero retries across 104,822 requests. The ×0 rung's 198k is not comparable to the
model — a 400-document corpus fits the fleet's in-flight budget with less queueing.

### 10.2 The semantic cost, re-measured on a long-document corpus

Leg A found `semantic` costs ~7× a `token_window` config of the same nominal size, not the
brief's 2×. Confirmed here: notional **101 M** breakpoint tokens on a 3.87 M-token corpus =
**26× corpus over the four configs**, ~6.5× per config, on top of the chunk embed. The
per-document cache shared across the four sizes cut the actual to 42 M (58% saved), with the
same hit pattern Leg A saw — 2048 pays 26.8 M, 1024 pays 0.4 M (99.6% hits), 512 pays 1.5 M,
256 pays 13.5 M as `_cap_tokens` starts truncating buffers. **Project stage 2 from notional.**

**PB7 FALSIFIED.** Predicted that Leg B's longer documents would push more articles past
`max_breakpoint_sentences = 3000`. Measured: **0 of 400**, median 212 sentences/document. Leg B
documents are longer in *tokens* (median 8,778 vs Leg A's 4,532) but their sentences are
longer too; the 3,000-sentence fallback that fired on 12 of Leg A's 4,053 documents never
fires here. Every semantic row on Leg B is genuinely semantically chunked, which Leg A's were
not quite.

### 10.3 GPU citizenship

* Six embedding endpoints only, **`:9001`–`:9006`**, ≤ 2 in flight each (12 global), enforced
  by the fleet client's slot queue.
* **GPUs 6 and 7: 0 MiB before, during and after. No endpoint was started on them and nothing
  was sent to them.** Verified at launch and recorded in [`gpu_after.txt`](gpu_after.txt).
* Crossencoder sidecar `:50052`, one request in flight.
* **0 retries in 132,689 fleet requests** — the fleet was never pushed into backpressure.

### 10.4 Store-untouched proof

The harness constructs **no Qdrant or Elasticsearch client anywhere**; retrieval is exact
brute-force cosine over in-memory `numpy`. Production `:6333` / `:9200` were never contacted.
Only the dev-tenant stores were *read*, to snapshot them — listings plus **exact per-collection
document counts** — before the run, between the two rungs, and after.

```
IDENTICAL: Qdrant :24041 and ES :24043 unchanged before/after
64a5698876f8a9fa5e2fc534ca6ba1a9bef0dcad81bd27c13601cb0956853976  stores_before.txt
64a5698876f8a9fa5e2fc534ca6ba1a9bef0dcad81bd27c13601cb0956853976  stores_mid.txt
64a5698876f8a9fa5e2fc534ca6ba1a9bef0dcad81bd27c13601cb0956853976  stores_after.txt
```

Contents, unchanged throughout: Qdrant `ragstack_lib_oa_dev_..._e788c5be` = 24,263 points,
`ragstack_salesforce_sfr_embedding_mistral_4096_928f8ebe` = 0; ES `ragstack` = 0,
`ragstack_lib_oa_dev_..._e788c5be` = 24,263 docs. Nothing was written under `/rag/`; `/rag/oa`
was read-only; every output is in this directory.

---

## 11. Which axes are uncontested, and which are contested

This is the deliverable the group asked for. **Nothing is pruned here; this is the evidence
state.**

### UNCONTESTED — both legs agree, both adequately powered

| axis | evidence | what it licenses |
|---|---|---|
| **Overlap (fraction), on nDCG@10** | A: −0.0210, δ80 0.041 < bar 0.05. B **at the pre-registered ×0 rung**: −0.0040, δ80 0.0081 < bar 0.010. Both nulls, both powered, both negative point estimates. The ×11.5 nDCG@10 replication is directionally the same (−0.0078) but **not powered to the bar** — §6.3. | Overlap can be dropped from stage 2 on uncontested evidence: **12 token_window cells → 4**, with no touch to the size axis. Saves 1/(1−f) vectors — up to **1.32×** — for an index's lifetime. |
| **Overlap, on recall@100** | A: +0.0022, δ80 0.0106 < bar. B: −0.0029, δ80 0.0089 < bar; and **exactly 0.0000** at all four sizes for 25% − 0% on 5,000 documents. | Same. This is the strongest null in the study. |
| **Overlap × size interaction (slope form)** | A: +0.0101, δ80 0.050 ≈ bar. B: +0.0011, **δ80 0.0061**. | The overlap null is not hiding a size-dependent effect. |
| **Realised chunk tokens is the explanatory variable, not nominal size and not kind** | A: r +0.811 vs +0.654 nominal, kind spread 0.1012 → 0.0538. B: r −0.974 vs −0.870, kind spread 0.0143 → **0.0023**. | Stage 2 should parameterise by realised tokens. The *variable* is uncontested; the *slope's sign* is not. |
| **Reranking reorders the grid** | A r = +0.553, B r = +0.767 (×0) / +0.612 (×11.5). | Any stage-2 decision that ships behind a reranker must be measured behind one. |

### CONTESTED — the legs disagree, or one leg cannot speak

| axis | state | detail |
|---|---|---|
| **Chunk size at the 512→1024 step** | **CONTESTED, resolvable on both legs, opposite signs** | A **+0.1204** [+0.052, +0.186]; B **−0.0182** [−0.031, −0.006]. This is the study's one genuine both-legs-resolvable contradiction. |
| **Chunk size at 256→512 and 1024→2048** | **NOT CONTESTED BY THE EVIDENCE, but not settled either** | Point estimates agree (finer better on both legs) but Leg A cannot resolve either step (δ80 0.059–0.099 against effects of 0.006–0.024). Agreement between one resolved and one unresolved estimate is weak. |
| **Chunk size behind the reranker** | **NO EVIDENCE OF AN EFFECT ON EITHER LEG** | Every size contrast is unresolved reranked, on both legs; on Leg B the shrinkage is itself a resolved effect (−0.0618, CI [−0.090, −0.034]). |
| **Chunker kind** | **CONTESTED and unresolvable at present** | `sentence512 − fixed512`: A +0.0606 (its best contrast, fails Holm), B +0.0061 (δ80 0.0103). `words512`: A −0.0132, B +0.0152. `semantic512`: A −0.0583, B +0.0116. Every kind contrast is unresolved on at least one leg — **and §9 says they are realised-size contrasts in disguise on both.** |
| **Which query population to optimise for** | **UNRESOLVED, and outside this run** | Leg A's relevance is document-level topical aboutness; Leg B's queries name a rare entity present in a deep section, and a max-rollup over chunks rewards a small chunk carrying that entity. Both directions are partly artefacts of query construction. Leg C has not run. |

---

## 12. What this run may not conclude

* **No config may be pruned on the size axis, in either direction.** Both legs' size directions
  are partly artefacts of how their queries were built, and behind the reranker neither
  survives.
* **Overlap's null is a null about *quality*, not a recommendation to remove overlap from
  production.** It says overlap does not buy retrieval quality on these two legs. Anything
  overlap does for answer-generation context continuity is untested here.
* **`fixed_tok256` is not "the best config".** It leads the dense ×0 table by **0.0017** over
  the next row (`fixed_tok256_ov0pct`, 0.9821) and by **0.0018** over the best config of a
  different kind (`sentence_tok256`, 0.9820), against a δ80 of 0.008–0.02. The top six rows
  are one result, not six — and reranking puts a different one of them first.
* **`fixed_tok512` (shipping) is not indicted.** Rank 14 of 24 dense at ×0, but **rank 4
  reranked** at ×0 and **rank 2 reranked** at ×11.5 — the largest reranker gain of any 512
  config. It is rank 21 of 24 on Leg A dense. Three orderings, three answers.
* **Absolute Leg B numbers do not transfer.** 0.93–0.98 on a near-binary known-item task with
  one relevant document is not comparable with Leg A's 0.40–0.63.
* **The pilot's three unfixed defects travel with everything here** (§3): 2 retracted articles,
  35 non-`research-article` sources, and a verifier that passes roughly one bad query in eight.
* **Q2's verdict is descriptive, and its decision rule was tightened post-hoc.** The literal
  pre-registered rule would have returned a vacuous "UNIFORM" on a single step; the ≥2-step
  requirement was added after seeing the data (§7.3). The one-step localisation is read off
  point estimates, two of which are unresolved on Leg A.

---

## 13. What stage 2 should do differently

1. **Drop the overlap axis** — 12 token_window cells to 4 — on uncontested, adequately powered
   evidence from two legs and two metrics. This is the only cut this run supports.
2. **Keep the entire size axis, and add nothing to it.** Spend the freed budget on *power at
   512 and 1024*, where the one real disagreement lives, not on more sizes.
3. **Make the reranked arm primary, not secondary.** The dense arm answers a question the
   production pipeline does not ask, and on both legs the dense and reranked answers differ.
4. **Parameterise by realised median chunk tokens** and pre-register the realised-token slope
   as the primary quantity. It explains 95% of the Leg B grid's variance and 66% of Leg A's;
   *kind* explains almost nothing on either once it is controlled.
5. **Pre-register the bar as a fraction of the leg's own realised spread, and say which
   rung's spread.** §5.1 shows one verdict in this run turns on that choice; it should be
   fixed in advance next time, per rung.
6. **Add the plan's missing clause.** Plan §7 stops a *chunker* grading its own homework; it
   has no clause for a *query construction* doing the same. Leg A's aboutness queries and
   Leg B's entity-anchored queries each favour the chunk scale their construction implies. The
   plan needs to state which query population the product optimises for before any size
   decision is taken — and Leg C is the only thing that can break the tie.

---

## 14. Files

```
stage1-legB/
  PREREG-stage1-legB.md      questions, rung-per-contrast table, every power floor, bar
                             derivation, budget + drop order — pre-committed
  RESULTS-stage1-legB.md     this document
  tables-legB.md             generated Tables 1-8 (accepted 260); -all = robustness (396)
  report-legB.json           every mean, contrast, bootstrap, panel and cost figure
  legb_common.py             corpus (with the pilot-hash assertion), queries, metrics, scoring
  legb_grid.py               chunk -> embed -> exact cosine -> inline rerank, per-config
                             checkpointed; imports the Leg A fleet client unchanged
  legb_report.py             the 11-contrast family, Holm, per-leg status, Q1/Q2/Q3 verdicts
  verify_pilot.py            the PB6 reproduction gate against the pilot, both rungs
  chain.sh                   the sequential run driver
  runs_{rung}_{key}.json     per-query dense + reranked metrics and top-10 doc sets (36)
  cstats_/estats_{rung}_*    realised chunk structure and measured cost per config
  semantic_cost_x0.json      notional vs actual breakpoint accounting
  corpus_{x0,x11_5}.json     the exact file lists
  stores_{before,mid,after}.txt, gpu_after.txt    the safety proofs
  chain.log / chain_driver.log / report_run*.log
```

Chunks and embeddings were deliberately not persisted (the ×11.5 rung's twelve cells are
~2.5 GB of text and far more of vectors, against 28 GB free on this filesystem). Reranking is
therefore done **inline**, while a config's chunk texts are still in memory; the per-config run
file is the checkpoint, so an interruption would have lost at most the config in flight.
