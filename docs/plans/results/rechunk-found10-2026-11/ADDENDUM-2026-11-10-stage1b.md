# Addendum: stage 1b, the unlabelled top-10 chunks judged (exploratory), 2026-11-10

Descriptive and exploratory, like stage 1. No test was planned. This file adds to [RESULTS.md](RESULTS.md),
which stays as committed. Stage 1b is the option in PLAN §4: judge the unlabelled top-10 chunks of the 75
lost pairs (at most 750) with the frozen judge, after a gateway notice. The owner approved it on 2026-11-09,
for the default retriever.

- **Sources.** [`artifacts/stage1b-seed-summary.json`](artifacts/stage1b-seed-summary.json) (the seed) and
  [`artifacts/stage1b-report.json`](artifacts/stage1b-report.json) (the stage-1 report re-run on the merged
  label table). Both are counts only, and both were written by committed code from a clean tree at `8afb4ca`
  (`provenance.citable` true, `dirty` false, `untracked` 0).
- **Pointers.** Below, *seed* means `stage1b-seed-summary.json`, *report* means `stage1b-report.json`, and
  `Q1d.<group>` stands for `q1_lost_pairs.<group>.by_retriever.default` in the report. *Stage 1* means
  [`artifacts/stage1.json`](artifacts/stage1.json), the committed stage-1 report.
- **Gateway.** The gateway calls ran on a different gateway from every earlier stage
  ([DEVIATIONS.md](DEVIATIONS.md) D-9).
- **Not in a committed artifact.** Figures marked † come from the operator's run record or from private
  files, not from a committed JSON. Each is named where it is used.

## Provenance

| step | when (UTC) | what | source |
|---|---|---|---|
| code | 2026-11-09 22:37:55 | PR #988 merged as `8afb4ca`: the stage-1b seed builder, `gold-map --extra-final`, the merged-table report, and `build-control --any-paper` | git |
| seed | 2026-11-09 22:38:37 | `rechunk_prep.py stage1b-seed` at `8afb4ca` | seed `provenance.recorded_at`, `provenance.commit` |
| registry | 2026-11-09 | `alias_study_judge.py build-control --any-paper`: 671 pairs | † operator record |
| local judging | 2026-11-09 22:38–23:07 | Qwen C and E, 671 pairs each | † operator record |
| gateway judging | 2026-11-10 03:11:12–03:13:44 | Gemini 4 Argon 80 calls, then Claude Opus 5.5 34 calls (D-9) | † homes gateway audit |
| merge | 2026-11-10 03:14:14 | `rechunk_prep.py gold-map --extra-final` at `8afb4ca` | † private `gold-map-merged.json` |
| report | 2026-11-10 03:14:15 | `rechunk_stage1.py --run-label-table` on the merged table, at `8afb4ca` | report `provenance.recorded_at`, `provenance.commit` |

- **No new retrieval.** The report reads the same four stage-A retrieval runs as stage 1: every
  `runs.<arm>.rows_sha256` and `summary_sha256` is unchanged from stage 1, and the seed pins the same values
  (seed `inputs_sha256.runs`).
- **The input label table is stage 1's.** Report `merged_label_table.run_label_table_sha256` (`62e63538…`)
  equals stage 1's `label_table_sha256` and the seed's `inputs_sha256.label_table`. The merged table is
  report `label_table_sha256` (`11b53b7b…`).

## What was judged

**The seed** (seed). The 75 lost pairs (`lost_pairs`; `lost_pairs_read` 75, `lost_pairs_failed` 0) belong to
69 questions (`questions`). The seed reads their 75 top-10 lists under the default retriever (`top10_lists`
75, `top_k` 10; `retriever_condition` `base` is the harness's name for the default retriever). That gives
750 top-10 slots (`top10_slots`):

| top-10 slots by label (`top10_slots_by_label`) | slots |
|---|---|
| already *answers* | 43 |
| already *not* | 20 |
| unjudged | 0 |
| no label row (seeded) | 687 |

- **Distinct seeds.** The 687 unlabelled slots are 671 distinct (question, chunk) seeds (`seeds`), because 16
  seeds sit in more than one lost pair's list (`seeds_in_several_lost_pairs`). PLAN §4's cap was 750.
- **By best rank** (`seeds_by_best_rank`): rank 1: 64; ranks 2–3: 127; ranks 4–10: 480.
- **By corpus arm** (`seeds_by_corpus_arm`): base 214, neighbour 206, random-1 139, random-2 112.
- **Paper.** 174 seeds lie in the same paper as their lost pair's stage-A window
  (`seeds_same_paper_as_window`), and 497 lie in another paper (`seeds_other_paper`). A default
  `build-control` refuses the other-paper seeds (`build_control_note`). They were built with
  `build-control --any-paper` (owner-approved), which takes the passage's paper and arm from the chunk's own
  paper.
- **Export.** The private chunk export the judge read has 661 rows (`export_rows`).

**The registry** (†, `build-control --any-paper` counts, operator record):
- 671 pairs;
- `same_paper` 197 (the passage's paper is the question's own paper);
- `passage_paper_not_stage_a_window_paper` 497, which equals the seed's `seeds_other_paper`;
- definitions coverage base 351/351, neighbour 148/148, random 170/172.

The two "same paper" splits differ: 174 counts the lost pair's window paper, and 197 the question's paper.
The 48 % breakdown below uses the registry's split, 197 against 474.

**The judge.** The confirmatory test's judge pipeline:
- Qwen C and E locally on all 671 pairs;
- Gemini 4 Argon as tiebreak where C and E disagree;
- Claude Opus 5.5 on the drift check.

| judging step | count | source |
|---|---|---|
| C/E disagreements, sent to the tiebreak | 80 of 671 (11.9 %) | † operator record |
| for comparison: the confirmatory stratum's escalation rate | 7.0 % | † operator record |
| for comparison: stage A's escalation rate | 17 % | † operator record |
| Argon tiebreak calls | 80 | † homes audit (D-9) |
| Opus drift-check calls | 34 (= round(33.55)) | † homes audit (D-9) |

**Final labels.**

| route | *answers* | *not* | total | source |
|---|---|---|---|---|
| C and E agree | 295 | 296 | 591 | † operator record |
| tiebreak | 28 | 52 | 80 | † operator record |
| all | **323** | **348** | **671** | † private merged label table (`source` `stage1b`) |

**Merge.** `gold-map --extra-final` added 671 new labels, with 0 already judged and 0 conflicts with the
confirmatory registries or within the batch (†, private `gold-map-merged.json` `stage1b` block). The report
records `merged_label_table.labels_added` 671 and `extra_registries` `["stage1b"]`.

## Result (default retriever)

After stage 1b, no top-10 chunk of a lost pair is unlabelled under the default retriever. `Q1d.<group>.U_any`
is 0 in every group (it was the whole group in stage 1), and `U_mean` is 0.0. **A is therefore no longer a
lower bound for these 75 pairs.** It equals the upper bound "A or any unlabelled chunk"
(`A_or_unlabelled`), which was 75 / 75 in stage 1 as well.

| group (`Q1d.…`) | pairs | A, stage 1 (lower bound) | **A, stage 1b** (Wilson 95 %) | A without A_own (`A_from_other_pairs_only`), 1 → 1b | A_own, stage 1 and 1b |
|---|---|---|---|---|---|
| `recovered` | 53 | 37 / 53 | **53 / 53** (93.2–100) | 2 → 18 | 35 / 53 |
| `others` (none) | 18 | 1 / 18 | **18 / 18** (82.4–100) | 1 → 18 | 0 / 18 (by construction) |
| `primary_only` | 4 | 4 / 4 | **4 / 4** (51.0–100) | 0 → 0 | 4 / 4 |
| `all_lost` | 75 | 42 / 75 | **75 / 75** (95.1–100) | 3 → 36 | **39 / 75** (40.9–62.9) |

- **A_own is unchanged.** A field-by-field comparison of the report with stage 1 finds changes only in `A`,
  `A_from_other_pairs_only`, `U_any`, `U_mean` (with their rates and intervals), `label_table_sha256`,
  `merged_label_table`, the `measures.A` text and `provenance`. Every `A_own`, `A_own_window_retained`, `M`,
  `M_rank` and `M_window_retained`, in every group, retriever, band and arm, is identical. This follows from
  the design: the stage-1b chunks are not any pair's own mapped chunks, so they can credit A but not A_own.
  RESULTS.md's reading, which uses A_own (PLAN §5), therefore stands as committed.
- **Reading `A_from_other_pairs_only`.** The field counts A true and A_own false
  (`rechunk_stage1.py`: `A and not A_own`). Its `measures` text, "mapped for another pair", predates stage
  1b. After the merge, the crediting chunk can also be a stage-1b chunk that is mapped to no pair.
- **Not judged: the other two retrievers.** The seed took only the default retriever's lists. Under norerank
  and BM25, A also rose: all 75 went from 37 to 69 (norerank) and from 28 to 68 (BM25)
  (`q1_lost_pairs.all_lost.by_retriever.{norerank,bm25}.A`). Their lists still hold unlabelled chunks,
  though (`U_mean` 4.6 and 4.89 of 10), so A stays a lower bound there and is not read.
- **Q2 shifts only incidentally.** Labels are per (question, chunk), so the new labels also reach other pairs
  of the 69 questions. Default A on all 351 went from 232 to 276 (`q2_all_pairs.all_pairs.all.by_retriever.default.A`),
  and on the 276 control-*answers* pairs from 190 to 201 (`q2_all_pairs.headline.A_control_answers_pairs.default`).
  Those lists were not seeded: 276 of 351 still hold an unlabelled chunk (`…default.U_any`). Q2's A_own and
  M are unchanged.

**How many new chunks answer** (†, not in a committed artifact):
- all 671: 323 (48 %);
- other-paper chunks: 202 of 474 (43 %);
- chunks from the question's own paper: 121 of 197 (61 %).

## Interpretation (analysis, not a measured result)

- **"Any chunk answers" saturates.** These are alias questions: questions about a term or an abbreviation,
  which many papers answer. Once every top-10 chunk is judged, some chunk in the top 10 answers for all 75
  lost pairs. That includes the 18 split pairs, which no own chunk can carry. For 36 of the 75, only a chunk
  that is not the pair's own credits A. A therefore cannot tell one chunking from another on this question set.
  A_own, the plan's reading (PLAN §4–5), is the informative measure, and it is unchanged at 39 / 75
  (recovered 35 / 53).
- **The 48 % is selection-driven.** The seeded chunks are the top 10 the retriever chose for these questions,
  not a sample of the corpus. That 48 % of them answer reflects the selection and is not independent support
  for any reading (the gateway coordinator's note).
- **The tiebroken pairs lean *not*.** 52 of the 80 tiebroken labels are *not* (65 %), against 348 of all 671
  (52 %). The coordinator noted this; it was not investigated, and it does not touch A_own.
- **Scope.** The labels are the frozen judge's. They carry the parent study's judge-validity caveat and say
  nothing about human relevance.
