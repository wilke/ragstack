# Results: rechunk found@10, stage 1 (exploratory), 2026-11-07

Every figure is descriptive and exploratory; no test was planned (PLAN §5). Numbers are read from
[`artifacts/stage1.json`](artifacts/stage1.json) (the stage-1 report) and
[`artifacts/build-summary.json`](artifacts/build-summary.json) (the collections as built). Both were written
by committed code at `16220f8` from a clean tree (`citable: true`).

- **Plan:** [PLAN.md](PLAN.md).
- **Deviations:** [DEVIATIONS.md](DEVIATIONS.md).
- **Data:** [TEST-DATA.md](TEST-DATA.md).
- **Reader's version:** [REPORT.md](REPORT.md).

## Provenance

| step | when (UTC) | what |
|---|---|---|
| Phase D, offline | 2026-11-06 21:05–21:29 | structure keys on 109,048 records; host exports at `002e98a` reproduce the planned counts exactly: 38,548 / 103,045 / 97,855 / 97,640 chunks |
| collections | 21:31 – 2026-11-07 01:37 | 4 collections created, embedded and loaded on the dev tenant through the GoWe workflows, tools image `v1.6.24-b1` with the size-unit input of PR #983; every load verified (`build-summary.json`) |
| retrieval | 01:37:42–01:38:43 | `alias_study_run.py` in `rechunk` mode, stage A, glossary none, at `002e98a` (clean); 0 failed calls; no harness gate fired |
| report | 01:38:49, regenerated at `16220f8` | `rechunk_stage1.py`, with `A_own_window_retained` added in this PR |

**Collections as built** (`build-summary.json` `collections`):

| collection | chunks | documents (all verified) | differs from host export |
|---|---|---|---|
| base | 38,548 | 12,560 | no |
| neighbour | 141,592 | 42,968 | 3 documents (DEVIATIONS D-5) |
| random-1 | 136,402 | 45,231 | 8 documents |
| random-2 | 136,187 | 45,969 | 8 documents |

- **Totals:** 452,729 chunks in all.
- **Clean builds:** every embed and load ran with `retry_count` 0, peak ES old-gen was 25 %, and every collection passed its census check.
- **Labels unaffected:** none of the 19 differing documents holds a confirmatory pair or a label-table chunk.

## Measures (PLAN §4)

All are per pair, per retriever, over the pair's question run against its corpus arm's collection:

- **A:** a chunk labelled *answers* (by any pair of the same question) is in the top 10.
- **A_own:** one of this pair's own chunks (its max-overlap chunk, or its cover-any chunks) is labelled
  *answers* and in the top 10.
- **M:** the pair's max-overlap chunk is in the top 10.
- **`_window_retained`:** the same quantities among the pairs whose stage-A window was in that retriever's
  stage-A top 10 in the same corpus arm.

Retrievers:
- **default:** hybrid search, reranked;
- **norerank:** the same hybrid, without reranking;
- **bm25:** BM25 alone.

Intervals are Wilson 95 %. Pairs of one question in one arm share one top-10 list, so the intervals are
over pairs that cluster by question; the record counts are given.

## Q1: the 75 lost pairs (`q1_lost_pairs`)

**Recovered (53 pairs, 49 questions):** another overlapping chunk answers.

| retriever | A_own | A | A_own, window retained | M |
|---|---|---|---|---|
| default | **35 / 53 (66.0 %; 52.6–77.3)** | 37 / 53 | 32 / 40 (80.0 %; 65.2–89.5) | 11 / 53 |
| norerank | 31 / 53 (58.5 %; 45.1–70.7) | 32 / 53 | 26 / 42 (61.9 %) | 13 / 53 |
| bm25 | 25 / 53 (47.2 %; 34.4–60.3) | 26 / 53 | 20 / 30 (66.7 %) | 9 / 53 |

**Others (18 pairs):** no overlapping chunk answers. A_own is 0 by construction, so stage 1 reads M.
- default: M 5 / 18 (27.8 %; 12.5–50.9); A 1 / 18, credited through another pair of the same question.
- norerank: M 4 / 18.
- bm25: M 3 / 18.

**Primary-only (4 pairs):** the re-read labelled the max-overlap chunk *answers*. A_own: default 4 / 4,
norerank 3 / 4, bm25 1 / 4.

**All 75, default:** A_own 39 / 75 (52.0 %; 40.9–62.9) and A 42 / 75. A comes from another pair's chunk only
on 3 of 75.

**The upper bound** "A or any unlabelled chunk" is 75 / 75 under every retriever. The plan called it loose
(PLAN §4) and reads it for completeness only.

**Stage 1b** (PLAN §4: the owner decides on a plain count): 18 of the 53 recovered pairs have A_own false
under the default retriever.

## Q2: all 351 pairs (`q2_all_pairs`)

| retriever | M, all pairs | M, window retained | A_own on the 276 control-*answers* pairs | A_own, window retained (all pairs) |
|---|---|---|---|---|
| default | 198 / 351 (56.4 %) | 190 / 276 (68.8 %) | **178 / 276 (64.5 %; 58.7–69.9)** | 208 / 276 (75.4 %; 70.0–80.1) |
| norerank | 175 / 351 (49.9 %) | 165 / 258 (63.9 %) | 155 / 276 (56.2 %) | 175 / 258 (67.8 %) |
| bm25 | 151 / 351 (43.0 %) | 143 / 210 (68.1 %) | 138 / 276 (50.0 %) | 152 / 210 (72.4 %) |

**By coverage band** (default, all pairs):

| coverage band | M | A_own |
|---|---|---|
| ≥ 0.8 | 89 / 145 (61.4 %) | 87 / 145 (60.0 %) |
| 0.5–0.8 | 99 / 182 (54.4 %) | 115 / 182 (63.2 %) |
| < 0.5 | 10 / 24 (41.7 %) | 15 / 24 (62.5 %) |

**By corpus arm** (default):

| corpus arm | M | A_own |
|---|---|---|
| base | 77 / 117 (65.8 %) | 86 / 117 (73.5 %) |
| neighbour | 59 / 117 (50.4 %) | 63 / 117 (53.8 %) |
| random-1 | 31 / 64 (48.4 %) | 35 / 64 (54.7 %) |
| random-2 | 31 / 53 (58.5 %) | 33 / 53 (62.3 %) |

**Reference 1** (PLAN §5): of the 53 recovered pairs, 40 had their window in the default retriever's
same-arm stage-A top 10 (75.5 %).

## Reading (PLAN §5, exploratory)

**Recovered pairs.** Under the default retriever, the recovered pairs' own answering chunk is in the top 10
at about the rate of pairs that never lost the label:
- all pairs: 66.0 % against 64.5 % on the 276;
- among retained windows: 80.0 % against 75.4 %.

The plan's reading for that case applies. For these pairs, the confirmatory loss is mostly a single-passage
judging artefact: retrieval still delivers the answer, from the neighbouring chunk. This is descriptive, the
intervals overlap widely, and it says nothing about human relevance.

**The 18 others.** A_own is 0 by construction, and their split chunk is in the top 10 for 5 of 18 (default).
A single-chunk measure cannot credit them. They are the candidates for overlap, or for judging a chunk
together with its neighbours.

**Not a reach comparison.** Every pair was drawn from windows retrieved in at least one stage-A leg, so
`M_all_pairs` (56.4 %) is set against a selected 100 % and must not be read as a loss of reach. A reach
comparison needs its own design.
