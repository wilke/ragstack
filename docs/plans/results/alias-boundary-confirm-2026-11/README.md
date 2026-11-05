# Alias study follow-up: confirmatory test of the chunk-boundary effect on judged labels

**Status: DRAFT pre-registration (v2), not frozen, not run.** No draw, no chunk, no judge call has been
made for this test. There is no experiment marker in this folder until the run exists.

The exploratory chunk-boundary control of the alias study
([`../alias-study-2026-11/ADDENDUM-2026-11-05-chunk-boundary-control.md`](../alias-study-2026-11/ADDENDUM-2026-11-05-chunk-boundary-control.md))
saw, on stage-A pairs whose two Qwen judges agreed on *answers*, the control chunk flip 28 of 150 to
*not* against 6 of 150 for a re-read of the identical window — a contrast chosen after the fact. This
folder pre-registers a paired, one-sided confirmatory test of that contrast on fresh pairs, with one
primary endpoint, the exact sign-flip test over records (questions) at α 0.05 and exact McNemar as a
pre-specified sensitivity, drawn at most one pair per (record, arm) from the 1,871 agreed-*answers*
stage-A pairs the control did not use (470 (record, arm) cells). Under the sizing assumptions (13 % vs
6 %, independent discordance within a pair, within-record correlation 0.5) **90 % power is not reachable
within the pool**; the recommended draw is **370** (80 % power, realised ≈ 352 pairs, minimum detectable
excess 7.0 points), and the draw size is the open owner decision (PREREG §13 Q4).

| file | tier | what |
|---|---|---|
| [PREREG.md](PREREG.md) | record, once frozen; a draft until then | question, estimand, tests, power, pool and draw, procedure, descriptives, decision wording, gates, freeze set and procedure, threats, cost, the code needed, the owner's open decisions and answers |
| [power.py](power.py), [power.json](power.json) | record, once frozen | the seeded power simulation (standard library) of the clustered design: the draw rule on the pool's anonymised record × arm structure, a within-record correlation model, the exact sign-flip test and the McNemar sensitivity; a planning calculation, no study data |

`power.json` was produced by `python3 -I -B power.py` from a **clean tree at commit `7eeb295`** (its
provenance: `dirty: false`, CPython 3.8.5, `script_sha256` below) and committed byte for byte; a re-run from
that commit reproduces it (seeded, no RNG outside `random.Random`).

| file | sha256 |
|---|---|
| `power.py` | `a9c5fd7260d5d725c438178e44eb5889715537941265fb0d79000403e5baa2d2` |
| `power.json` | `ec061b22248f2cbe02d9065fadfd7c039bf7d4cd7d9b126a3eca26439fd99021` |

**Versions.** v1 (2026-11-05, commit `a88f4d5`): one pair per record, exact McNemar, n = 318. The owner's
v1 answers are recorded in PREREG §13 (`01991ed`). v2 (2026-11-05): after two independent reviews (PR #956,
Fable and an argo-agent read) and the owner's decision on the cap and the primary test; the power was
re-done by simulation under the clustered design.

**Blindness of the draft.** The drafting session read the exploratory addendum, its public artifact and
the control plan in full, and from the private stage-A registry only the fields that define the eligible
pool and its structure (stage, source, label, arm, record, window and passage-paper ids); it opened no
control, cover-any or retest label of any pair, no text, and no spot-check sheet. The pool count (2,064
agreed-*answers* − 150 in the control = 1,914) also follows from the committed
`control/artifacts/control-draw.json` alone. The record × arm structure in `power.py` is anonymised
counts (per record, its eligible pairs per arm), no ids.

**Freeze** (PREREG §9.2): owner's go naming the commit → `sha256sum PREREG.md` into a private
`PREREG.sha256` → signed annotated tag `prereg-alias-boundary-confirm-2026-11` pushed → the status line
above becomes `FROZEN <sha> at <UTC> on the owner's go (commit <sha>)` in this README only → the draw.
Later changes go to a `DEVIATIONS.md` in this folder.
