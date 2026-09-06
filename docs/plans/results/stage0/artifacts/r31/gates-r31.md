# Stage 0b′ r3.1 — machine label gates: whole-sentence anchors, 5 presentations

| gate | requirement | **scout** | **qwen** |
|---|---|---|---|
| **self-consistency** — reading (i), k=0 vs k=1, all pairs | ≥ 0.90 | **0.3831** (118/308) **FAIL** | **0.5617** (173/308) **FAIL** |
|   — reading (i) on the same 31 pairs #501 used | reported (#501: 0.645 / 0.419) | 0.2903 (9/31) | 0.5484 (17/31) |
|   — reading (i), first set only / best-matching set pair | reported | 0.5422 / 0.6656 | 0.5584 / 0.5714 |
|   — reading (ii), mean over all 10 presentation pairs (union) | reported | 0.4201 (range 0.3766–0.4675) | 0.5399 (range 0.487–0.5779) |
|   — reading (ii), first set only / best-matching set pair | reported | 0.563 / 0.688 | 0.5396 / 0.551 |
|   — mean pairwise span-union Jaccard (raw, not the indicator) | reported | 0.435 (median 0.3534, n=2898) | 0.543 (median 0.7518, n=3056) |
| **hallucinated-span rate**, k = 0 | ≤ 0.05 | **0.02517** (22/874 spans; Wilson 95 % upper 0.03782) **PASS** | **0.00279** (1/358 spans; Wilson 95 % upper 0.01565) **PASS** |
|   — split by anchor (first-sentence / last-sentence quote) | reported (#501: 4 / 54 for scout) | 16 / 6 | 0 / 1 |
|   — all 5 presentations (sensitivity) | reported | 0.01957 (85/4344); anchors 58 / 27 | 0.00888 (16/1802); anchors 12 / 4 |
|   — quotes rescued by the eight-word ladder (first / last anchor) | descriptive | 2 / 2 | 0 / 0 |
| **document-level whether-agreement**, all 5 presentations | ≥ 0.90 | **0.9188** (283/308) **PASS** | **0.9805** (302/308) **PASS** |
|   — mean pairwise whether-agreement | reported | 0.961 | 0.9909 |
| “no localizable evidence” rate, k = 0 | descriptive | 0.0844 (26/308 pairs) | 0.0195 (6/308 pairs) |
| spans emitted / attempted, k = 0 | descriptive | 876 / 874 | 357 / 358 |
| spans split across a unit boundary, k = 0 | descriptive | 10 | 0 |
| quotes ambiguous (>1 occurrence), k = 0 | descriptive | 24 | 7 |
| quote landed inside the unit whose title it named, k = 0 | descriptive | 798/830 | 339/339 |
| pairs dropped (no verified span survived), k = 0 | descriptive | 0/308 | 0/308 |
| mean evidence sets / spans per positive pair, k = 0 | descriptive | 2.436 / 3.007 (n=282) | 1.182 / 1.182 (n=302) |
| pairs whose every span is in the abstract, k = 0 | descriptive | 104/282 | 200/302 |
| deep-section pairs, k = 0 | descriptive (Stage 0: 3/308) | 5/282 | 67/302 |
| **ALL THREE GATES** | conjunctive | **FAIL** | **FAIL** |
| union of five presentations, split-half stability (0–2 vs 3–4) | reported, not a gate | 0.4708 (145/308) | 0.5779 (178/308) |

## Union saturation — distinct evidence sets in the union of the first k presentations

Distinct = not merged by D3 rule 1 (span-union Jaccard ≥ 0.5); accumulated in presentation order, k = 0 first. Denominator: pairs that carry at least one set somewhere in the 5 presentations.

| union | n pairs | k=1 | k=2 | k=3 | k=4 | k=5 | marginal gain at k=5 |
|---|---|---|---|---|---|---|---|
| scout | 295 | 2.2746 | 3.6203 | 4.5017 | 5.478 | 6.1017 | 0.1022 |
| qwen | 307 | 1.1629 | 1.7231 | 2.1661 | 2.4886 | 2.8046 | 0.1127 |
| pooled (scout ∪ qwen) | 307 | 3.0293 | 4.7003 | 5.8404 | 6.987 | 7.8241 | 0.1070 |

* **scout** — NOT SATURATING — the marginal gain at k = 5 is 0.1022, at or above 0.05 of the union size; a fifth presentation is still adding locations
* **qwen** — NOT SATURATING — the marginal gain at k = 5 is 0.1127, at or above 0.05 of the union size; a fifth presentation is still adding locations
* **pooled** — NOT SATURATING — the marginal gain at k = 5 is 0.1070, at or above 0.05 of the union size; a fifth presentation is still adding locations

## Enumeration proxy (r3 §3.7 item 5 — a proxy; enumeration recall needs the human read)

| statistic | value |
|---|---|
| asymmetric coverage on the k = 5 unions (scout's chars inside qwen's / qwen's inside scout's) | **0.1852** / **0.7802** (n=295) |
| the same at k = 0 (#501: 0.1622 / 0.6592) | 0.2252 / 0.5908 (n=281) |
| scout: fraction of its own k = 5 union that presentation 0 alone recovered | 0.4248 (median 0.4, n=295) |
| qwen: fraction of its own k = 5 union that presentation 0 alone recovered | 0.5612 (median 0.5, n=307) |

## Cross-judge agreement (scout vs qwen)

| statistic | value |
|---|---|
| co-labeled pairs (complete for both) | 308 |
| κ(scout–qwen), pair-level binary evidence/none, k = 0 | **0.29** |
| observed / expected agreement | 0.9286 / 0.8994 |
| confusion (both+ / both− / scout-only+ / qwen-only+) | 281 / 5 / 1 / 21 |
| span-union Jaccard where both positive, k = 0 | mean **0.2123**, median 0.0805, ≥ 0.5 on 0.1459 of 281 |
| span-union Jaccard between the k = 5 unions | mean **0.1613**, median 0.1012, ≥ 0.5 on 0.0407 of 295 |
| distinct sets in the cross-judge (scout ∪ qwen) union at k = 5 | **7.8241** per positive pair (n=307) |

## Decision — r3 §3.7 / §5 step 2

**NEITHER JUDGE PASSES THE CONJUNCTION — stop stands per r3 §5 step 2.** scout: self-consistency 0.3831 (FAIL), hallucinated 0.02517 (PASS), whether-agreement 0.9188 (PASS); qwen: self-consistency 0.5617 (FAIL), hallucinated 0.00279 (PASS), whether-agreement 0.9805 (PASS).

**Read as a labeler, the union of five presentations is stable at:** scout 0.4708 (145/308, < 0.90); qwen 0.5779 (178/308, < 0.90). This is the union of presentations 0–2 against the union of 3–4 — halves of unequal depth, so it is a stability statistic and not the ≥ 0.90 gate re-read on a like-for-like pair.

*κ(human–human) and κ(judge–human) are `PENDING-HUMAN`: they require the two-reader R-dev read of §6.6.2. No agent read was substituted and none was performed. Enumeration recall against human-marked sets (r3 §3.7 item 5's actual gate) is **absent**, not zero — the proxy above stands in its place.*
