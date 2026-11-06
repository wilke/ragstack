<!-- experiment: Alias study follow-up: confirmatory chunk-boundary test | 2026-11-06 | CONFIRMATORY · CONFIRMED -->
# Alias study follow-up: confirmatory test of the chunk-boundary effect on judged labels

**Status: RUN 2026-11-06 — Confirmed, all six gates pass ([REPORT.md](REPORT.md); record: [RESULTS.md](RESULTS.md)).** Frozen 2026-11-06T02:47:50Z on the owner's go of 2026-11-05 ("Go: freeze at 016fb61"). The freeze record below is kept as written at the freeze.

**Status: FROZEN 2026-11-06T02:47:50Z on the owner's go of 2026-11-05 ("Go: freeze at 016fb61"); not run.**
`PREREG.md` is frozen as written at commit `016fb61d03784e6505b43275e4ee7536a2fddec7`, sha256
`b3a2d9e61f42675d9dccdd84bc449a0e7507e4b9c9d15bf957b6a3613e589b0e` (its own status line, which still reads
*DRAFT*, is part of the frozen text). `power.json` sha256 `13afd2ba3e31f06455972cf001ae2b566dc4e428941bf48a0f4b8298020bc685`.
Signed annotated tag `prereg-alias-boundary-confirm-2026-11` (verify with `git tag -v`); the sha256 and the UTC
time are also in a private `PREREG.sha256`, written before the draw. No draw, no chunk, no judge call has been
made for this test. There is no experiment marker in this folder until the run exists.

The exploratory chunk-boundary control of the alias study
([`../alias-study-2026-11/ADDENDUM-2026-11-05-chunk-boundary-control.md`](../alias-study-2026-11/ADDENDUM-2026-11-05-chunk-boundary-control.md))
saw, on stage-A pairs whose two Qwen judges agreed on *answers*, the control chunk flip 28 of 150 to
*not* against 6 of 150 for a re-read of the identical window — a contrast chosen after the fact. This
folder pre-registers a paired, one-sided confirmatory test of that contrast on fresh pairs, with one
primary endpoint, the exact sign-flip test over records (questions) at α 0.05 and exact McNemar as a
pre-specified sensitivity, drawn at most one pair per (record, arm) from the 1,871 agreed-*answers*
stage-A pairs the control did not use (470 (record, arm) cells). The owner's draw is **351** (equal arms
117 / 117 / 117; 2026-11-05, v3): under the sizing assumptions (13 % vs 6 %, independent discordance
within a pair, within-record correlation 0.5, assumed) the primary's simulated power is ≈ .79 at a
realised n of ≈ 333, the floor on the realised n is 316 (a harness-failure detector), and the design's
minimum detectable excess at 80 % power is 7.5 points. 90 % power is not reachable within the pool under
those assumptions (PREREG §3.2–3.3).

| file | tier | what |
|---|---|---|
| [PREREG.md](PREREG.md) | record, once frozen; a draft until then | question, estimand, tests, power, pool and draw, procedure, descriptives, decision wording, gates, freeze set and procedure, threats, cost, the code needed, the owner's questions and dated answers |
| [power.py](power.py), [power.json](power.json) | record, once frozen | the seeded power simulation (standard library) of the clustered design: the draw rule on the pool's anonymised record × arm structure, a within-record correlation model, the exact sign-flip test and the McNemar sensitivity, the 351 cell with its realised-n distribution, the power-by-realised-n curve the result sentence reads, the floor and the MDE; a planning calculation, no study data |
| [REPORT.md](REPORT.md) | reader's report, revised in place (like a paper; not record) | hypothesis → results summary → how it was tested → results → conclusion; cites RESULTS.md |
| [TEST-DATA.md](TEST-DATA.md) | record | how the test pairs were generated, from the papers to the 351 pairs, with counts and their artifacts |
| [RESULTS.md](RESULTS.md) | record | the outcome, the decision sentence of PREREG §7, the primary table, the gates, the §6 descriptives, procedural notes |
| [artifacts/](artifacts/) | record | `draw.json`, `chunk.json`, `map.json`, `retest-seed.json` (steps 1–2, #977), `confirm-report.json` (step 6, with the step-7 `cover_any` block added) and `map-cover.json` (step 7) |
| [ADDENDUM-2026-11-06-cover-any.md](ADDENDUM-2026-11-06-cover-any.md) | record | step 7: where the lost answers went (descriptive) |
| [DEVIATIONS.md](DEVIATIONS.md) | record, append-only | post-freeze deviations (PREREG §9.3); D-1, D-2: the owner's go replaced the gateway coordinator's ack (both gateway batches) |

`power.json` was produced by `python3 -I -B power.py` from a **clean tree at commit `0bbe2d9`** (its
provenance: `dirty: false`, CPython 3.8.5, `script_sha256` equal to the committed `power.py`'s hash below)
and committed byte for byte; a re-run from that commit reproduces it (seeded `261105|alias|confirm|power`,
no RNG outside `random.Random`). Its numbers are those of the v3 run at `9867f9d`; v3.1 changed only the
`n_floor_rule` wording ("rounded up") and the provenance.

| file | sha256 |
|---|---|
| `power.py` | `a5df6c1b109a5256cf31c263b046453b7d5adbb1a8041d2fbd219cc4578c7990` |
| `power.json` | `13afd2ba3e31f06455972cf001ae2b566dc4e428941bf48a0f4b8298020bc685` |

**Versions.** v1 (2026-11-05, `a88f4d5`): one pair per record, exact McNemar, n = 318; the owner's v1
answers in PREREG §13 (`01991ed`). v2 (`7eeb295`, `7feb0f0`): after two independent reviews (PR #956,
Fable and an argo-agent read) and the owner's decision on the cap and the primary test; power by
simulation; the owner's v2 answers (`55c8d11`, `ede2337`). v3 (`9867f9d`, `c45816d`): after the
second round of both reviews and the owner's draw-size decision (351); the floor, the power-by-realised-n
curve, the Qwen cache check, quotas and shared-window structure restated. v3.1 (`0bbe2d9` and this commit):
after the third round of both reviews (agreeing); the cache flag on `reasoning_sample` identity only with
the exploratory base rates, the floor's rounding, the §6 numbering, stale figures, and the freeze
sequencing after the §11 code PR; no design element changed. v3.2 (with the §11 code PR, #969): pins from
the two code reviews, no design change (PREREG §11.1): the draw pinned at 351 and refused unless every
§4.1 count reproduces; `--confirmatory` checked against `--prereg` from step 1; gate 6 against the
committed `artifacts/`; the record order of the scores and the bootstrap; the full control-sample sha;
exclusion 2 over all 300 control windows; the shortfall rule; the cache check's *not evaluable*; the §6
conventions. `power.json` is untouched.

**Draw note (v3.2).** The draw's greedy walk need not reach the matching maximum of PREREG §4.3: it may take
fewer than 118 cells in the random arm, and fewer than its quota of 117. This is a reviewer's structural
expectation, not a measured result (no real draw has been run): the random arm has one spare cell above its
quota, and 2 of its records have every pair on a shared window. If it happens, the shortfall rule gives
the pairs short to base, then neighbour, and `draw.json` reports each arm's `walk_capacity` and achieved
count, so it is visible in the committed artifact. An identical re-run of `draw-confirm` that
reproduces the same `sample.jsonl` is not a re-draw (PREREG §9.3).

**Blindness of the draft.** The drafting session read the exploratory addendum, its public artifact and
the control plan in full, and from the private stage-A registry only the fields that define the eligible
pool and its structure (stage, source, label, arm, record, window and passage-paper ids) and, for the
cache check, the **key names** of the Qwen and gateway label rows (no value); it opened no control,
cover-any or retest label of any pair, no text, and no spot-check sheet. The pool count (2,064
agreed-*answers* − 150 in the control = 1,914) also follows from the committed
`control/artifacts/control-draw.json` alone. The record × arm structure in `power.py` is anonymised
counts (per record, its eligible pairs per arm), no ids.

**Freeze** (PREREG §9.1–9.2): only after the §11 code PR is merged to `main` with two independent reviews;
then the owner's go naming that `main` commit → `sha256sum PREREG.md` into a private
`PREREG.sha256` → signed annotated tag `prereg-alias-boundary-confirm-2026-11` pushed → the status line
above becomes `FROZEN <sha> at <UTC> on the owner's go (commit <sha>)` in this README only → the draw.
Later changes go to a `DEVIATIONS.md` in this folder.
