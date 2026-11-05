# Alias study follow-up: confirmatory test of the chunk-boundary effect on judged labels

**Status: DRAFT pre-registration, not frozen, not run.** No draw, no chunk, no judge call has been made
for this test. There is no experiment marker in this folder until the run exists.

The exploratory chunk-boundary control of the alias study
([`../alias-study-2026-11/ADDENDUM-2026-11-05-chunk-boundary-control.md`](../alias-study-2026-11/ADDENDUM-2026-11-05-chunk-boundary-control.md))
saw, on stage-A pairs whose two Qwen judges agreed on *answers*, the control chunk flip 28 of 150 to
*not* against 6 of 150 for a re-read of the identical window — a contrast chosen after the fact. This
folder pre-registers a paired, one-sided confirmatory test of that contrast on fresh pairs, with one
primary endpoint, exact McNemar on discordant pairs at α 0.05, n = 318 (90 % power at a planning effect
of 13 % vs 6 %, independent discordance), drawn 334 with a 5 % oversample from the 1,914 agreed-*answers*
stage-A pairs the control did not use.

| file | tier | what |
|---|---|---|
| [PREREG.md](PREREG.md) | record, once frozen; a draft until then | question, estimand, test, power, pool and draw, procedure, descriptives, decision wording, gates, freeze set and procedure, threats, cost, the code needed, the owner's open decisions |
| [power.py](power.py), [power.json](power.json) | record, once frozen | the exact paired power calculation (standard library, no RNG) behind n = 318 / n₈₀ = 237 / n_draw = 334 and the MDE curve; a planning calculation, no study data |

`power.json` was produced at `a349629` from a tree with untracked files (its provenance says so); it is
re-run at the freeze commit, where it must be byte-identical but for the provenance block.

| file | sha256 |
|---|---|
| `power.py` | `142950eb70c239dd8237b1c9c5341ee83649e2cbf1a66dcd5722722c7415f09b` |
| `power.json` | `da5876cfdb2c5a0565051b9d0c044c53e3dcc963d928a792705cc5c280a4eac0` |

**Blindness of the draft.** The drafting session read the exploratory addendum, its public artifact and
the control plan in full, and from the private stage-A registry only the fields that define the eligible
pool (stage, source, label, arm, record and window ids); it opened no control, cover-any or retest label
of any pair, no text, and no spot-check sheet. The pool count (2,064 agreed-*answers* − 150 in the control
= 1,914) also follows from the committed `control/artifacts/control-draw.json` alone.

**Freeze** (PREREG §9.2): owner's go naming the commit → `sha256sum PREREG.md` into a private
`PREREG.sha256` → signed annotated tag `prereg-alias-boundary-confirm-2026-11` pushed → the status line
above becomes `FROZEN <sha> at <UTC> on the owner's go (commit <sha>)` in this README only → the draw.
Later changes go to a `DEVIATIONS.md` in this folder.
