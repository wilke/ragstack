# r3.1 extension — gate table, saturation and the reliability of graded support

*SPEC-confirmation-run-r3.md SS3.7 item 6 / SS10 item 4, extended: scout x20 and qwen x10 presentations of the same 308 development pairs, same prompt revision 3.1, temperature 0, seeded unit orders*

Extends `artifacts/r31/gates-r31.json (#507)`.

## Gate table (all readings)

| gate | requirement | scout | qwen |
|---|---|---|---|
| readings | — | 20 | 10 |
| self-consistency (k=0 vs k=1, union) | ≥ 0.90 | 0.3831 FAIL | 0.5617 FAIL |
| — mean over all C(k,2) presentation pairs | reported | 0.421 | 0.5437 |
| — mean pairwise raw span-union Jaccard | reported | 0.444 | 0.5439 |
| hallucinated-span rate, k = 0 | ≤ 0.05 | 0.02517 PASS | 0.00279 PASS |
| — all readings | reported | 0.01956 | 0.00974 |
| — the NEW presentations only (P-ext-3) | ≤ 0.05 | 0.01956 | 0.01061 |
| whether-agreement, all readings agree | ≥ 0.90 | 0.8539 FAIL | 0.961 PASS |
| — mean pairwise | reported | 0.9581 | 0.9895 |
| ALL THREE GATES | conjunctive | **FAIL** | **FAIL** |

**NEITHER JUDGE PASSES THE CONJUNCTION on any reading** — the r3.1 stop stands. scout: self-consistency 0.3831 (FAIL), hallucinated 0.02517 (PASS), whether 0.8539 (FAIL); qwen: self-consistency 0.5617 (FAIL), hallucinated 0.00279 (PASS), whether 0.961 (PASS).


### A locator blow-up contaminates the mean — read this before the table

`spans_emitted >= 10 * max(spans_seen, 1) in a single reading` flags **1** reading(s) of 9240:

* **scout** 2016_1 / 4212306, presentation 11: the model offered **22** spans, the locator emitted **986**, covering **22,366** distinct sentences (`split_across_units` = 1).

One span whose first and last quoted sentence located in *different* units is expanded by the r3.1 locator into one whole-unit span per intervening unit. Every column below is therefore given three ways: the **mean** (the pre-registered estimator, and what P-ext-2 is scored on), the **median** (immune to this), and the mean with the flagged pairs dropped.


## Saturation — distinct evidence sentences

| k | scout mean | qwen mean | scout median | scout mean, blow-up dropped | pooled mean |
|---|---|---|---|---|---|
| 1 | 12.26 | 1.6124 | 3.0 | 12.2609 | 11.9805 |
| 2 | 22.3633 | 2.3844 | 10.0 | 22.3244 | 12.886 |
| 3 | 27.0467 | 2.987 | 14.0 | 26.8963 | 22.5537 |
| 4 | 33.5867 | 3.4528 | 20.0 | 33.4013 | 22.9967 |
| 5 | 36.82 | 3.8925 | 23.0 | 36.495 | 27.4104 |
| 6 | 38.7033 | 4.2508 | 25.5 | 38.3043 | 27.7427 |
| 7 | 41.15 | 4.5375 | 28.0 | 40.6388 | 34.0358 |
| 8 | 42.7 | 4.8046 | 28.5 | 42.0635 | 34.2606 |
| 9 | 44.5367 | 5.0065 | 31.0 | 43.8395 | 37.3713 |
| 10 | 46.3733 | 5.3681 | 33.0 | 45.2274 | 37.5993 |
| 11 | 47.7267 | — | 34.0 | 46.5151 | 39.3811 |
| 12 | 122.9633 | — | 35.5 | 48.0268 | 39.5505 |
| 13 | 124.77 | — | 37.0 | 49.8161 | 41.8827 |
| 14 | 125.97 | — | 38.0 | 50.9833 | 42.0782 |
| 15 | 127.0567 | — | 38.5 | 52.0435 | 43.5505 |
| 16 | 127.62 | — | 39.0 | 52.5452 | 43.7036 |
| 17 | 128.8133 | — | 40.0 | 53.7057 | 45.443 |
| 18 | 129.3833 | — | 40.0 | 54.2475 | 45.5537 |
| 19 | 132.2267 | — | 41.5 | 57.0268 | 47.3029 |
| 20 | 133.8267 | — | 42.0 | 58.6187 | 47.4658 |
| 21 | — | — | — | — | 48.7394 |
| 22 | — | — | — | — | 121.8111 |
| 23 | — | — | — | — | 123.5179 |
| 24 | — | — | — | — | 124.6645 |
| 25 | — | — | — | — | 125.6938 |
| 26 | — | — | — | — | 126.215 |
| 27 | — | — | — | — | 127.3746 |
| 28 | — | — | — | — | 127.9218 |
| 29 | — | — | — | — | 130.658 |
| 30 | — | — | — | — | 132.2085 |

Michaelis–Menten refits (U(k) = Vmax·k/(Km+k)), with the no-saturation linear null beside each:

* **scout** — **the MM fit is DEGENERATE**: Km reached the search ceiling (500.0): the least-squares optimum is Km -> infinity, i.e. the curve is LINEAR over the range fitted and has no identifiable asymptote. Vmax = 3810.8 and Km = 500.0 are artefacts of the grid; only the slope Vmax/Km = 7.6209 sentences per reading is identified. Do not quote this Vmax as an asymptote. The linear null fits it at r² 0.861368 with slope 7.5076 sentences per reading. **No asymptote is measurable from these points.**
* **qwen** Vmax 7.4873, Km 4.4284, r² 0.991476; at its own depth the union is 0.6931 of the asymptote (linear null r² 0.962603, slope 0.3931)
* **scout, blow-up pairs dropped** Vmax 68.3302, Km 4.6368, r² 0.988735; at its own depth the union is 0.8118 of the asymptote (linear null r² 0.886427, slope 1.9325)
* **pooled** — **the MM fit is DEGENERATE**: Km reached the search ceiling (500.0): the least-squares optimum is Km -> infinity, i.e. the curve is LINEAR over the range fitted and has no identifiable asymptote. Vmax = 2169.7 and Km = 500.0 are artefacts of the grid; only the slope Vmax/Km = 4.3391 sentences per reading is identified. Do not quote this Vmax as an asymptote. The linear null fits it at r² 0.808424 with slope 4.4255 sentences per reading. **No asymptote is measurable from these points.** — the pooled curve is a staircase (a scout reading adds many sentences, the qwen reading after it adds few), so its fit is the worst of the three

Marginal gain at the last reading, and the same curve in D3-**distinct sets** rather than sentences:

* **scout** sentences +0.012 of the union at k = 20; distinct sets 2.2367 → 11.91 (+0.0274 at the last reading), MM Vmax 16.5552
* **qwen** sentences +0.0674 of the union at k = 10; distinct sets 1.1629 → 3.7492 (+0.0461 at the last reading), MM Vmax 5.1908

Where the support mass sits at 30 readings: the union holds **132.2085** sentences per pair (median 42), the top 3 carry **0.3285** of all votes, and the best-supported sentence is in **0.7509** of readings.

## Reliability of graded per-sentence support (pooled readings)

| n readings | split-half r | Spearman–Brown full-n | top-3 overlap | SB, blow-up dropped | SB, pairs with ≥ 3 sentences |
|---|---|---|---|---|---|
| 4 | 0.1037 | 0.1879 | 0.6546 | 0.1879 | 0.1666 |
| 6 | 0.3104 | 0.4738 | 0.637 | 0.4768 | 0.4697 |
| 8 | 0.4569 | 0.6272 | 0.6475 | 0.6375 | 0.6221 |
| 10 | 0.5444 | 0.705 | 0.6557 | 0.7056 | 0.7045 |
| 14 | 0.6502 | 0.788 | 0.6736 | 0.7927 | 0.7896 |
| 20 | 0.7526 | 0.8588 | 0.7044 | 0.8639 | 0.8591 |
| 30 | 0.8527 | 0.9205 | 0.7551 | 0.9233 | 0.9209 |

*n = 2 is not on the curve and is not reported: a 1-vs-1 split makes both halves binary indicators of a single reading each, so the 'reliability' it measures is the agreement of two readings, not the stability of a graded score. The curve starts at n = 4.*

## Cross-judge

* per-sentence support correlation, scout vs qwen: mean r **0.2976** (median 0.3099, pooled over sentences 0.2657), top-3 overlap 0.4473
* κ(scout–qwen) on *whether*, k = 0: 0.29

## Predictions

| prediction | observed | scored |
|---|---|---|
| P-ext-1 (SB reliability at 30 ≥ 0.85) | 0.9205 | **PASS** |
| P-ext-2 (scout U(20) within 10 % of asymptote) | 133.8267 vs Vmax 72.3277 | **PASS** |
| — P-ext-2 with the locator blow-up dropped | 58.6187 vs threshold 65.0949 | **FAIL** |
| — P-ext-2 secondary (projection accurate to 10 %), blow-up dropped | 58.6187 vs predicted 58.7603 (relative error 0.0024) | **PASS** |
| P-ext-3 (new-presentation hallucination ≤ 0.05) | scout 0.01956, qwen 0.01061 | **PASS** |

Graded per-sentence support at 30 pooled readings has Spearman-Brown reliability 0.9205, against the >= 0.9 bar r3 sets for a labeler: **MEETS IT**. This is a measurement of the instrument SS10 item 4 (a) proposes, not a decision about it; the decision is the owner's and needs the human read besides.

*PENDING-HUMAN — no human read was performed, no kappa against a human is computed, and r3 SS3.7 item 5's enumeration recall is ABSENT, not zero. The R-dev pairs were not read.*
