# Alias study follow-up: confirmatory test of the chunk-boundary effect on judged labels

**Status: DRAFT pre-registration (v3), not frozen, not run.** No draw, no chunk, no judge call has been
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

`power.json` was produced by `python3 -I -B power.py` from a **clean tree at commit `9867f9d`** (its
provenance: `dirty: false`, CPython 3.8.5, `script_sha256` below) and committed byte for byte; a re-run from
that commit reproduces it (seeded `261105|alias|confirm|power`, no RNG outside `random.Random`).

| file | sha256 |
|---|---|
| `power.py` | `d79fcd3fe12d90b111f7c53a3c800cfb73b4910add259fbcfc29a4cda1da3953` |
| `power.json` | `b50e37f7519861944c321162c1a784f66b76a55845e7267d826e6e20abbd76c3` |

**Versions.** v1 (2026-11-05, `a88f4d5`): one pair per record, exact McNemar, n = 318; the owner's v1
answers in PREREG §13 (`01991ed`). v2 (`7eeb295`, `7feb0f0`): after two independent reviews (PR #956,
Fable and an argo-agent read) and the owner's decision on the cap and the primary test; power by
simulation; the owner's v2 answers (`55c8d11`, `ede2337`). v3 (`9867f9d` and this commit): after the
second round of both reviews and the owner's draw-size decision (351); the floor, the power-by-realised-n
curve, the Qwen cache check, quotas and shared-window structure restated.

**Blindness of the draft.** The drafting session read the exploratory addendum, its public artifact and
the control plan in full, and from the private stage-A registry only the fields that define the eligible
pool and its structure (stage, source, label, arm, record, window and passage-paper ids) and, for the
cache check, the **key names** of the Qwen and gateway label rows (no value); it opened no control,
cover-any or retest label of any pair, no text, and no spot-check sheet. The pool count (2,064
agreed-*answers* − 150 in the control = 1,914) also follows from the committed
`control/artifacts/control-draw.json` alone. The record × arm structure in `power.py` is anonymised
counts (per record, its eligible pairs per arm), no ids.

**Freeze** (PREREG §9.2): owner's go naming the commit → `sha256sum PREREG.md` into a private
`PREREG.sha256` → signed annotated tag `prereg-alias-boundary-confirm-2026-11` pushed → the status line
above becomes `FROZEN <sha> at <UTC> on the owner's go (commit <sha>)` in this README only → the draw.
Later changes go to a `DEVIATIONS.md` in this folder.
