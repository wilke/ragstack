# Results: confirmatory test of the chunk-boundary effect on judged labels (2026-11-06)

**Outcome: Confirmed.** All six gates pass. Pre-registration: [`PREREG.md`](PREREG.md), v3.2, frozen at
`016fb61` (sha256 `b3a2d9e6…`, signed tag `prereg-alias-boundary-confirm-2026-11`). Every number below is
read from the committed [`artifacts/confirm-report.json`](artifacts/confirm-report.json), unless a section
names another committed artifact.

## Provenance

| step (PREREG §5) | UTC | artifact | commit |
|---|---|---|---|
| 1 `draw-confirm` | 2026-11-06 04:52:41 (`recorded_at`) | `artifacts/draw.json` | 709fd9e, committed 04:53:05 and pushed before `chunk` started (merged in #977) |
| 2 `chunk`, `map`, `retest-seed` | started 04:53:12†; `recorded_at` 04:54:04 / 04:54:15 / 04:54:22 | `artifacts/chunk.json`, `map.json`, `retest-seed.json` | 5e434ad (#977) |
| 3 `build-control` ×2 | 04:55† | private registries, the same 351 `control_of` ids | — |
| 5 Qwen C / E | 04:56:20 – 05:26:08 | `times.C`, `times.E` | — |
| 5 Argon / Opus, then `finalize` ×2 | 14:29:30 – 14:32:59; `finalize` done 14:33:03† | `times.argon`, `times.opus` | — |
| 6 `confirm-report`, once | 14:34:20 (`recorded_at`; file written 14:34:26†) | `artifacts/confirm-report.json` | this commit |

† Operator shell time, from the private run log; in no committed artifact.

Every step ran from a clean detached checkout at the frozen tag. Each artifact records commit `016fb61`,
`dirty: false`, `untracked: 0` and `citable: true`, with `exploratory: false` and the `confirmatory` block
holding the frozen sha. `confirm-report.json` gate 6 compared 27 input shas against the committed copies:
0 mismatches.

## The decision sentence (PREREG §7, filled from `confirm-report.json`)

> Confirmed: on 351 fresh stage-A pairs (218 records) whose two Qwen judges had agreed the 512-token window
> *answers*, the frozen judge labelled the `max-overlap` control chunk under this chunker (`sentence/512/0`,
> section cuts, 640 ceiling) *not* on 68 pairs where it kept *answers* on the identical window re-read,
> against 9 the other way (exact one-sided sign-flip over records p = 1.2 × 10⁻¹¹; control 21.4 %, retest
> 4.6 %, difference 16.8 points, 90 % cluster-bootstrap interval 12.9 to 20.8 points; McNemar sensitivity
> p = 1.2 × 10⁻¹²). The control chunking as produced — its cut positions, coverage and shorter chunks
> together — loses *answers* labels beyond the judge's own test-retest noise, under single-passage judging.

The §7 additions:
- **Power and the design's MDE.** P(351) = .815 is the simulated power for the planning effect: a control
  rate of 13 % against 6 %, φ = 0 and ρ = 0.5, all assumed. E = 7.5 points is the design's minimum
  detectable excess at 80 % power, under the same assumptions.
- **Caveats.** None applies: 0 unjudged pairs in either arm, so not *judge incomplete*. The cache check is
  evaluable on both Qwen backends and did not fire, so neither *cache suspected* nor *not evaluable*.
- **Exploratory comparison.** The control's 18.7 % vs 4.0 % on its *answers* stratum was exploratory.

**What one point is here.** The denominator is the 351 pairs, so one point is about 3.5 pairs. *Not* labels
go from 16 of 351 when the window is re-read (4.6 %) to 75 of 351 for the control chunk (21.4 %). The
difference d = (b − c) / n = (68 − 9) / 351 = 16.8 points.

## Primary table (`primary`)

| | control *answers* | control *not* |
|---|---|---|
| **retest *answers*** | 267 (neither) | **68** (b, control only) |
| **retest *not*** | **9** (c, retest only) | 7 (both) |

| quantity | value |
|---|---|
| realised n / mapped / drawn | 351 / 351 / 351 |
| records (clusters) / with a nonzero score | 218 / 72; records in 1 / 2 / 3 arms: 124 / 55 / 39 |
| passage papers / largest paper cluster / documents | 337 / 2 pairs / 343 |
| record scores S_j | −1: 9 records; 0: 146; +1: 58; +2: 5 |
| primary: exact one-sided sign-flip over records | statistic 59, p = 1.17 × 10⁻¹¹ (mid-p 6.7 × 10⁻¹²) |
| sensitivity: exact one-sided McNemar | p = 1.22 × 10⁻¹² |
| d | 0.1681 |
| 90 % record-cluster bootstrap (10,000, seed `261105\|alias\|confirm\|boot`) | [0.1295, 0.2085]; one-sided 95 % upper bound U = 0.2085 |
| 90 % Tango score interval | [0.1308, 0.2080] |
| arm rates, Wilson 95 % | control *not* 75 / 351 = 21.4 % [17.4, 26.0]; retest *not* 16 / 351 = 4.6 % [2.8, 7.3] |
| unjudged, both arms | 0 — so the adverse, favourable and *unjudged = not* recomputations of §6.8 all equal the primary |
| power at the realised n | .8147 (`power.json` grid 350 / 355, interpolated) |

**By corpus arm** (secondary, not tested; 117 pairs each):

| arm | b | c | control *not* | retest *not* |
|---|---|---|---|---|
| base | 24 | 3 | 24 (20.5 %) | 3 (2.6 %) |
| neighbour | 20 | 4 | 23 (19.7 %) | 7 (6.0 %) |
| random | 24 | 2 | 28 (23.9 %) | 6 (5.1 %) |

## Gates (§8, read by the report before the p)

| gate | result |
|---|---|
| 1 realised n ≥ 316 | pass: 351 |
| 2 unjudged ≤ 5 % per arm | pass: 0 and 0 of 351 |
| 3 draw and registries | pass: same `control_of` set; 0 drawn control pairs; 0 shared windows; both input shas match; n_draw 351; quotas 117 / 117 / 117 |
| 4 chunker and environment | pass: `p2_gate` true; `punkt-abbrev-v1`, `markdown/1`, `HFTokenCounter` / SFR-Embedding-Mistral |
| 5 arms complete | pass: 0 missing final rows in either arm |
| 6 input shas vs committed artifacts | pass: 27 checked, 0 mismatched |

## Descriptives (§6; reported, not tested)

- **Judge-side checks:**
  - **Cache check (§6.9 / §10.10).** Retest prompts are byte-identical to stage A's: the prompt and system
    sha differ on 0 of 351, for both C and E. Yet the Qwen `reasoning_sample` was byte-identical on
    **0 of 351** for C and 0 of 351 for E (351 eligible each). Flag false; the gateway's response-cache
    status is *absent* (not recorded). Descriptives beside their exploratory base rates (§6.9): `texts_sample`
    identical to stage A's on 5 of 351 for C and 26 of 351 for E (exploratory 1 / 300 and 14 / 300); `ct` equal
    on 0 of 351 for C and 3 of 351 for E (exploratory E 1 / 300).
  - **Stage-A agreement.** The retest arm's verdicts agree with stage A's on 345 of 351 for C (98.3 %) and
    330 of 351 for E (94.0 %).
  - **Escalation to the Argon tiebreak.** Control 26 of 351 (7.4 %), retest 23 of 351 (6.6 %). Overlap: 5
    pairs escalated in both arms (Jaccard 0.11), and Argon agreed with itself on all 5. Those 5 are not a
    replicate, because the two arms' prompts differ.
  - **Opus drift reference.** 18 pairs per arm (6 / 6 / 6). Agreement with the final label: control 16 of 18
    (88.9 %), retest 15 of 18 (83.3 %). It is report-only (R29) and never sets a label.
- **Time (§6.7).** b and c by halves of each arm's labelling order and of its C-call order:
  - control, labelling order: 33 / 5 in the first half vs 35 / 4 in the second;
  - control, C-call order: 33 / 4 vs 35 / 5;
  - retest, either order: 37 / 4 vs 31 / 5.

  No drift with time is visible. Each backend's two arms ran 10–18 s apart.
- **Where the excess sits (boundary descriptives):**

  | control-chunk coverage of the window | n | control *not* | retest *not* |
  |---|---|---|---|
  | ≥ 0.8 | 145 | 7 (4.8 %) | 6 (4.1 %) |
  | 0.5–0.8 | 182 | 56 (30.8 %) | 10 (5.5 %) |
  | < 0.5 | 24 | 12 (50.0 %) | 0 (0 %) |

  | document structure | n | control *not* | retest *not* |
  |---|---|---|---|
  | sectioned | 284 | 75 (26.4 %) | 14 (4.9 %) |
  | one untitled section | 67 | 0 (0 %) | 2 (3.0 %) |

  By control-chunk token band: 128–511, 66 of 289 vs 13 of 289; 512–640, 9 of 49 vs 3 of 49; under 128,
  0 of 13 vs 0 of 13.
- **What the chunks look like.**
  - The control chunks end in sentence punctuation 94.3 % of the time, against 16.5 % for the stage-A
    windows.
  - 6.3 % of control chunks cross a section, and none splits a sentence.
  - Mean length is 412 tokens for control chunks and 467 for windows (SFR-Embedding-Mistral tokenizer).

## What this shows

**Evidence.** On fresh pairs drawn by a frozen rule, the effect the exploratory control found after the fact
is confirmed: 21.4 % vs 4.6 %. Descriptively (PREREG §6 makes no comparison with the exploratory 300), the
exploratory 18.7 % vs 4.0 % lies within this run's intervals. It holds in all
three corpus arms, and its p does not depend on how unjudged pairs are scored, because there were none.

**Analysis (not tested).** The tested contrast bundles cut position, coverage and length (PREREG §7, §10).
The coverage table suggests which of these carries it:
- Where the control chunk holds at least 80 % of the original window, the two arms are level: 4.8 % vs 4.1 %.
- The excess grows as coverage falls: 30.8 % at 0.5–0.8 coverage, and 50 % below 0.5.
- In documents with a single untitled section, where section cuts cannot move a boundary, no control chunk
  lost its label.

In the exploratory 300, most lost labels had moved to a neighbouring chunk (24 of 28). If the same holds here,
the answer-bearing text has been split across chunks and single-passage judging misses it; the step-7
cover-any descriptive reads that on these pairs, and until it does this is a hypothesis. The result does not show that respecting sentence and section boundaries makes a chunk worse at holding an answer, and
the test was not designed to separate that. A coverage-matched or answer-span-aware design would be needed.

**What the labels are.** The labels are the frozen judge's. The parent study's judge-validity flag applies
here as well (alias RESULTS, §13.5: re-weighted owner–judge agreement 0.872 against the 0.90 threshold).
This result is a statement about that judge's labels, not about human relevance.

## Procedural notes

1. **Gateway notice** (a step-order deviation, recorded in [`DEVIATIONS.md`](DEVIATIONS.md) D-1; it touches no
   computation). PREREG §5 step 4 requires the gateway coordinator's acknowledgement before the first
   gateway call. The coordinator session was unreachable on 2026-11-06: it was not among the reachable
   sessions, and the size notice bounced.
   - The owner's choice in chat replaced the ack: "Continue but test if the gateway is reachable first."
   - The gateway's model list answered with both models served, and no model call was made for that check.
   - The batch then ran as sized: Argon 26 + 23 and Opus 18 + 18 calls, 85 in all, every one `ok`.
   - There was no provider notice, access-denied reply or served-model mismatch. The Opus cap (24 per
     registry) was not reached.
2. **First `confirm-report` invocation.** It ran at 14:33Z under the judge interpreter, which lacks
   `transformers`, and stopped in the environment check while building the chunker
   (`TokenCounterUnavailable`). By then it had read the registries into memory but computed nothing: it
   printed no label, count or statistic and wrote no `--out` or `--write-flipped` file. The one run that
   produced the report followed (`recorded_at` 14:34:20Z) under the interpreter that ran `chunk`, from the same clean
   checkout and with the same arguments. The run-once rule (§5 step 6) is enforced by `--out`, which did
   not exist before that run. No §9.3 element changed, so this is not a deviation.
3. **Blindness.** No label, `final.jsonl`, `judge-summary.json` or per-arm `LOG` count was read before the
   report.
   - Progress was followed by exit codes and the Qwen completion lines, which hold token totals only.
   - The gateway size notices give per-arm escalation counts (26 / 23), which §5 step 4 requires the notice
     to name. They were seen before the report.

## Next (PREREG §5 step 7)

The cover-any descriptive comes next:
1. `map --cover-any` over the 75 control-arm *not* pairs (the private list was written by `--write-flipped`).
2. A third registry, under the same gates.
3. A second gateway notice.
4. `confirm-report --cover-config`, which recomputes the primary table and refuses unless it equals this one.

It runs only on the owner's go.
