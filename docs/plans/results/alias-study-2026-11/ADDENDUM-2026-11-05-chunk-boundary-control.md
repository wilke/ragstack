# Addendum, 2026-11-05: chunk-boundary control and its test-retest null (exploratory)

**Tier: record. Exploratory, post-freeze.** This file changes no confirmatory number, no hypothesis label,
cell, record, judge, seed or gate of the frozen pre-registration (`prereg/PREREG-alias-study.md`, sha256
`d54f4201…`). Every number below is exploratory, and every sentence that quotes one says so or sits under a
heading that does. Direction, stratum, arm, coverage band and token band were pre-specified (plan §5) but
read after the overall flip rate; the split by stage-A route (C and E agreed vs tiebroken) and the
test-retest null were added after the control's results had been seen. The splits describe this sample
and confirm nothing.

**Source.** Every figure comes from [`control/artifacts/control-judge.json`](control/artifacts/control-judge.json)
(`schema alias-control-report/1`, counts and rates only), written by `alias_control_chunks.py report` at
`aeafaeb` from a clean tree (`dirty false`, `untracked 0`, `citable true`, `in_image false`). The retest's
seed summary is [`control/artifacts/control-retest-seed.json`](control/artifacts/control-retest-seed.json)
(`retest-seed` at `c0c62ea`, clean). The plan and the pre-judge counts were committed before any judge
call (`control/CONTROL-PLAN.md`, `control/README.md`). Intervals are Wilson 95 % as the study's statistics
module computes them. No significance test was run on any comparison here; none was planned.

## The question

Do the judges' labels change when the same question is judged against a chunk that respects sentence and
section boundaries, instead of the 512-token window (`fixed_token/512/0`) it was judged against in stage A?
The control chunk covers the same text: `sentence/512/0` with section cuts (`structure_cuts sections/1`),
`size_tolerance 0.25` (ceiling 640 tokens), `min_piece 128`, SFR tokenizer, Markdown adapter `markdown/1`
in raw mode, Punkt `punkt-abbrev-v1` (`chunker` and `environment` blocks of the artifact). Each stage-A
window maps to the control chunk of largest character overlap (ties to the earlier chunk).

## What was run, and when (UTC, 2026-11-05)

| step | pairs judged | gateway calls (Argon / Opus) | Qwen | gateway |
|---|---|---|---|---|
| control (300 stage-A pairs, control chunks) | 300 | 32 / 15 | ~17:14–17:26 | 19:01:47–19:02:45 |
| cover-any (every control chunk overlapping a flipped window) | 85 | 11 / 4 | ~19:05–19:10 | 19:10:35–19:11:01 |
| test-retest null (the same 300 stage-A windows, unchanged) | 300 | 47 / 15 | 19:47–20:00 | 20:02:18–20:03:29 |

Call counts are `judge_files` in the artifact. The times, the call statuses and the preflight counts come
from the private run logs and call ledgers, not from a committed artifact. Statements attributed to the
gateway coordinator are reported messages from its gateway audit, also not committed artifacts. All three
steps used the frozen judge byte for byte (prompt `alias-judge/2`, Opus on `alias-judge/1` as the prereg
table states; C = Qwen3.6-35B-A3B, n = 5, T 0.7, thinking;
E = Qwen3.8-27B, n = 1, T 0.7; Argon tiebreak on any C/E disagreement; Opus 5 % drift reference, which never
sets a label; `fallback_model ""`, ≤ 4 in flight, chat endpoint). Every gateway call returned `ok`; there
was no provider notice and no re-issue. The gateway coordinator reconciled all 124 calls exactly against
the gateway's own audit, per model and per UTC hour.

**What differs from the plan, and why.**
- *Owner agreement (plan §5.4) was dropped* by owner decision on 2026-11-05 (the artifact's `note`).
- *Cover-any ran* as plan §3 and owner answer Q5 specify. It re-judged the primary control chunk as well as
  its neighbours, so it also yields a repeatability measure on the same bytes (by construction: same pair
  id, same chunk text).
- *The test-retest null was added after the control's flip rate had been seen*, on owner approval
  (2026-11-05), because cover-any showed the judge does not repeat itself on identical text. It re-judges
  the 300 stage-A windows through the identical path. Its prompts are byte-identical to stage A's: the
  Argon replicate pairs match on prompt and system sha256 (34 of 34, `retest.tiebreak_replicate`). Its
  gateway calls ran about an hour after the control's, which matters because the gateway models run at the
  provider's default temperature (below).
- *Call caps.* There is no Argon call cap (prereg R30; the harness refuses one in main mode). Opus is capped
  at the prereg's 7 % share of the registry (21 of 300 for the control, 15 used). Each step's call count
  equalled its preflight count.

## Results (exploratory)

### Flip rate, with the null beside it

| (exploratory) | control: control chunk vs stage-A window | null: stage-A window re-judged |
|---|---|---|
| all pairs | 40 / 300 = 13.3 % (9.9–17.7) | 20 / 300 = 6.7 % (4.4–10.1) |
| answers → not | 36 / 170 = 21.2 % (15.7–27.9) | 12 / 170 = 7.1 % (4.1–11.9) |
| not → answers | 4 / 130 = 3.1 % (1.2–7.7) | 8 / 130 = 6.2 % (3.2–11.7) |
| stage-A route: C and E agreed | 28 / 249 = 11.2 % (7.9–15.8) | 7 / 249 = 2.8 % (1.4–5.7) |
| … agreed *answers* stratum | 28 / 150 = 18.7 % (13.2–25.7) | 6 / 150 = 4.0 % (1.8–8.5) |
| … agreed *not* stratum | 0 / 99 = 0.0 % (0.0–3.7) | 1 / 99 = 1.0 % (0.2–5.5) |
| stage-A route: tiebroken | 12 / 51 = 23.5 % (14.0–36.8) | 13 / 51 = 25.5 % (15.5–38.9) |

(`metrics.flip`, `direction_rates`, `by_route`, `by_stratum`; and the same keys under `metrics.retest`.)

Exploratory reading:
- **About half of the overall 13.3 % is the judge's own non-repeatability.** Re-judging the identical
  window flips 6.7 %. The two overall intervals overlap slightly near 10 %, so the overall rates are not cleanly
  separated (`flip_minus_retest` 0.067, display only).
- **On pairs whose two Qwen judges agreed in stage A, the control only lost answers.** 28 of 150 agreed
  *answers* pairs flipped to *not*, and none of the 99 agreed *not* pairs gained an answer. The null flips
  4.0 % and 1.0 % of the same strata. On the agreed route pooled (11.2 % vs 2.8 %) and on the agreed
  *answers* stratum, the control's and the null's intervals do not overlap. The 4 *not → answers* flips of the control are all on tiebroken pairs.
- **On tiebroken pairs the control is indistinguishable from the null** (23.5 % vs 25.5 %). These pairs
  carry no boundary evidence in this sample. The stratum resolves nothing below about 10 % (plan §4).

### Where the lost answers went (cover-any, exploratory)

Every control chunk that overlaps a flipped window was judged on its own (85 chunks for 40 pairs, none
capped; `control-map.json` `n_overlapping`). `metrics.cover_any.split.by_route`:

| (exploratory) | n | another overlapping chunk answers, primary does not | both | only the re-judged primary answers | none |
|---|---|---|---|---|---|
| agreed route, answers → not | 28 | 24 | 0 | 2 | 2 |
| tiebroken, answers → not | 8 | 2 | 1 | 1 | 4 |
| tiebroken, not → answers | 4 | 0 | 0 | 2 | 2 |

On agreed pairs, an overlapping neighbour chunk answers for 24 of the 28 lost answers (85.7 %, 68.5–94.3;
`recovered_by_other_chunk`): the evidence moved to an overlapping chunk rather than disappearing. On tiebroken
answers → not flips the share is 3 of 8. This count comes from which chunk holds the text, not from a single
verdict, so the judge noise measured below doesn't produce it on its own, though each "answers" behind it is
still a judge label. Flips also concentrate where the control chunk covers less of the window: 8 of 149
(5.4 %) at coverage ≥ 0.8, 27 of 130 (20.8 %) at 0.5–0.8, 5 of 21 at < 0.5 (`by_coverage_band`).

### How repeatable the frozen judge is (exploratory)

- *Final labels on identical windows:* 20 of 300 flip (the null above). By the stage-A route, 7 of 249
  agreed pairs and 13 of 51 tiebroken ones flip (`retest.by_route`).
- *Which pairs escalate:* stage A sent 51 of the 300 windows to the Argon tiebreak and the retest 47. Only
  34 escalated in both; 17 dropped out and 13 were new (Jaccard 0.53; `retest.escalation_overlap`). The
  escalation **rate** is stable while its **membership** churns. The gateway coordinator independently
  confirmed the overlap of 34 from the gateway audit's request hashes, without reference to the registry.
- *Argon tiebreak verdicts on byte-identical prompts:* the same verdict on 27 of the 34 shared pairs
  (79.4 %, 63.2–89.7; `retest.tiebreak_replicate.verdict_agreement`). This is **verdict agreement**.
- *Primary control chunks re-judged in cover-any:* the final label repeats on 34 of 40 (85.0 %; these are
  selected, borderline pairs, so the rate is biased low; `cover_any.split.primary_rejudge_agreement_selected`).
- *Gateway side, from the audit (not a committed artifact):* the gateway coordinator reported that on
  byte-identical requests the response **length** differed for most Argon and all Opus pairs. That is
  evidence of non-deterministic sampling independent of our verdicts. It is a different measure from the
  verdict agreement above, on a different footing, and is quoted without numbers for that reason.

### Boundary diagnostics (exploratory)

Of the 300 control chunks, 94 % end in sentence punctuation against 21 % of the stage-A windows, none is a
piece of a split sentence, and 5.3 % hold two whole sibling sections (`section_crossing`). Control chunks
are shorter than the windows (mean 405 vs 449 SFR tokens, p50 468 vs 512; none above the 640 ceiling).
76 of 300 pairs (25.3 %) come from documents the adapter sees as one untitled section; their flip rate is
6 of 76 against 34 of 224 for sectioned documents (`metrics.boundary`, `by_section_structure`). The judge
says *answers* less often on control chunks (C 148 vs 183 of 300 on the stage-A window; `judge_stage`).

## Limitations

- **Judge-only.** The control says nothing about found@10 or retrieval under the new chunking (plan §3,
  option (a)). It measures whether a single chunk, read alone, still states the answer.
- **Selected comparisons.** Route, direction and stratum were read after the overall rate. The agreed-pair
  *answers → not* contrast is the clearest pattern in this sample; confirming it would need a new,
  pre-registered test on a single named endpoint with fresh pairs. None has been run or approved.
- **Gateway temperature is unrecorded.** The frozen judge sets the Argon tiebreak at "default temperature"
  (prereg §6.3), and the Opus drift reference states none; the requests send no temperature, so the
  provider's default applies and its value is not recorded. The null's gateway calls ran about an hour
  after the control's, so it is the best available floor for the control; it may not transfer to
  re-judges made later.
- **Opus empty answers** are handled by the frozen topic-filter rule (prereg §6.5): an empty reply is
  re-issued once, then the pair is *no-reference*, filtered from the drift agreement and never a drift
  failure; Opus never sets a label. In these three steps every Opus call returned an answer (34 calls, 34
  label rows, no re-issue; `judge_files`), so no pair was filtered and no figure above depends on the rule.
- **One chunker setting** (512 target, 640 ceiling, section cuts from the Markdown adapter). Untitled JATS
  sections and PDF-sourced documents have no headings for it to cut on (plan §2).
- **Owner judgement is absent** from this control by decision; every label here is the frozen judge's.

## What is not reported

Flip rates by arm (`metrics.by_arm`), by control-chunk token band (`by_control_token_band`) and for the
LaTeX-preamble stratum the owner asked for (`by_latex_preamble`) are pre-specified and in the artifact but
not restated here. Owner agreement on control chunks (dropped); any test of the control against the null (none planned); any
stage-A study figure (none is restated here); found@10 under the control chunking (option (b), not run).
