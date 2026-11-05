# Lab book: alias study (OA-1000)

**Tier: lab book.** Append-only and dated. An entry that turns out wrong is superseded by a later dated
entry that says so; nothing here is edited or deleted. Status lines are true on the day they carry and may
be false later. Numbers still resolve to committed artifacts: each one names its file and key path, and the
record ([RESULTS.md](RESULTS.md), [DEVIATIONS.md](DEVIATIONS.md), the
[control addendum](ADDENDUM-2026-11-05-chunk-boundary-control.md)) wins wherever this page disagrees with
it. Times are UTC. "The owner" is the project owner.

Artifacts cited below, all under this folder: `artifacts/results-stage-A2.public.json` (A2, Layer 1),
`artifacts/results-stage-B.public.json` (B, Layer 2 and the cells), `artifacts/h1b-gold.public.json`,
`artifacts/disagreement-tags.json`, `control/artifacts/control-judge.json`.

## Contents

- [2026-11-05: results recorded, control added, what the numbers mean](#2026-11-05-results-recorded-control-added-what-the-numbers-mean)

---

## 2026-11-05: results recorded, control added, what the numbers mean

### Status

- **The record is merged** (#954, merge `a349629`): `RESULTS.md` with deviations D-1 to D-3. H1
  *confirmed*, H1b *confirmed*, H2 *not confirmed*, each with *judge validity below threshold (§13.5)*
  (`layer1.<H>.status` in A2).
- **The chunk-boundary control addendum is merged** (#953, merge `95cf2b9`), exploratory, changing no
  confirmatory figure.
- **A presentation deck** (15 slides) and this lab book are added. The deck is generated from the
  committed JSON by [`presentation/make_deck.py`](presentation/make_deck.py) and published as a static
  page of the docs site.

**What ran when** (from the provenance blocks and the records):

| when (UTC) | what | source |
|---|---|---|
| 2026-11-04 05:39:34 | prereg frozen, sha256 `d54f4201…`, at commit `e6ecfa6`; signed tag `prereg-alias-study-2026-11` | `prereg/README.md` |
| 2026-11-04 12:12:27 | stage A committed (19 retrieval legs, 9,205 judged pairs) | `artifacts/stage-a-commit.public.json`; RESULTS *Provenance* |
| 2026-11-04 23:18:52 | binding stage-A analysis A2 (statistics at `15ee347`), after the D-1 inputs | A2 `provenance.recorded_at` |
| 2026-11-05 14:15:52 | stage-B analysis (29 legs; 24,559 judged pairs in all) | B `provenance.recorded_at`, `judge.pairs_finalized` |
| 2026-11-05 ~17:14–20:03 | chunk-boundary control: control, cover-any, test-retest null | ADDENDUM *What was run, and when* |
| 2026-11-05 20:28:01 | control report written (`aeafaeb`) | control-judge `provenance.recorded_at` |
| 2026-11-05 21:34:40 | owner's disagreement tags counted (`d93edda`) | disagreement-tags `provenance.recorded_at` |

### What the results say, in plain words

A **point** is one percentage point of found@10. One record is 0.17 points of the 600 G records, 0.33
points of the 300 C records and 0.11 points of all 900 (RESULTS *What found@10 measures*). Every figure in
this section carries *judge validity below threshold (§13.5)*.

**H1 (Family G): does the merged glossary help G records?** Yes, by a little. Under the served path, 591
of the 600 G records were already found without any glossary; the merged glossary found 597, so it rescued
6 of the 9 that were missed and lost none (+1.00 point, 95 % CI [+0.33, +1.83], p 0.015625, its minimum
attainable p; `layer2.placebo.G.found10`, A2 `layer1.H1`). The glossary is attached by the **mention
rule**: a paper's entry `alias — term` is rendered on every chunk where the alias occurs as a whole token,
capped at 12 lines and 1,200 characters (prereg §5.3, R27). The **placebo** glossary, another paper's
entries under the same rule, found 591, the same as none (1 gained, 1 lost; `layer2.placebo.G`), so
glossary-shaped text by itself did nothing. It is a weaker dose than merged (817 against 24,749 chunks
with a field; `layer2.placebo.field_coverage`), which bounds "extra text" only at that coverage.

**The C gate on H1.** It fires only if all three hold: ΔC > 0 (C rises with G), |ΔC| ≥ ½|ΔG|, and ΔC's
95 % CI excludes 0 (prereg §3.1). A C drop can never fire it. Here ΔC was −0.33 points (1 of 300 lost) and
the baseline C was 300 of 300, so the gate was *ceiling-limited*: it could not have fired on a C rise of any
size (A2 `layer1.H1.c_gate`).

**H1b (Family G): is the gain alias-specific?** The G change (+1.00 point, 6 records) minus the C change
(−0.33, 1 record) is +1.33 points [+0.50, +2.50]; 7 papers moved, all in the hypothesised direction
(p 0.0293, its minimum attainable p; A2 `layer1.H1b`). An invented example shows what the three readings
look like. A paper defines "PDT (photodynamic therapy)" once; its results chunk says only "PDT reduced
tumour volume by 40%", and under merged that chunk carries the line "PDT — photodynamic therapy" because
"PDT" occurs on it. A G question asks "By how much did photodynamic therapy reduce tumour volume?"; a C
question asks "Which light wavelength was used for photodynamic therapy?" and is answered by a passage
that spells the term out. *The example is invented; no study record is shown.*

| scenario | G question | C question | difference-in-differences |
|---|---|---|---|
| alias bridging | rises: the field supplies the full term | flat: the term is already there | positive |
| general boost | rises | rises as much: any added text helps | about 0 |
| clutter | flat or falls | falls: the added lines crowd the chunk | 0 or below, or a C cost |

The observed pattern (G +6 of 600, C −1 of 300) is the alias-bridging row. **Caveat:** C started at 300 of
300, so a general boost could not have shown on C; the evidence against it from C is one-sided. The placebo
supports the alias reading from the other side.

**H2 (Family N): do neighbours cost more than random papers?** Not on the judged measure. Gold-only, which
needs no judge and is H2's direction gate, the neighbour arm found 776 of 900 against a random average of
795.5 (−2.17 points [−3.50, −0.94]; the gate passes). Judged, it found 882 against 881.5 (+0.06 points,
11 gained, 9 lost, p 0.615; B `layer2.cells.<arm>|none|default`, A2 `layer1.H2`). H2 is *not confirmed*,
with power .96 and .90 to confirm at the planning scenarios (`power-v4.json` `simulation.H2.rho1`). The
**equivalence bound is two-sided**: the 90 % CI [−0.56, +0.67] lies inside ±1.39 points, so the record says
*no effect larger than 1.39 points* (about 12.5 of 900 records) in either direction. The neighbour arm had
67 judged hits from other papers against 48 and 49 in the random draws (A2
`fixed_sensitivities.origin.hits_by_origin`); that neighbours displaced gold chunks and answered in their
place is **a reading** from per-cell totals, not a measured per-record transition.

**Layer 2 (descriptive, no tests).**
- *Attach the merged glossary:* **not resolved.** The lift is 6 of 600 G records against the 2-point action
  threshold, 12 of 600; the C cost rule did not fire (B `layer2.decisions.attach_merged_glossary`). Quoted
  beside H1 and H1b confirmed (D-3).
- *Keep the reranker default:* **keep the default (non-inferior to BM25).** The rule is non-inferiority with
  a −3-point margin: keep the default if the CI's lower end is above −3. Default minus BM25 on the
  neighbour arm was +6.33 points [+4.67, +8.11], 57 of 900 records (62 gained, 5 lost), so the default is
  ahead, not merely non-inferior (B `layer2.default_minus_bm25_neighbour`).
- *Related-paper growth:* **not a concern**, beside H2 not confirmed (B
  `layer2.decisions.related_paper_growth_concern`).

**Judge validity.** The owner agreed with the final judge label on 0.872 re-weighted and 41 of 50 on the
*answers* stratum, both below 0.90, so the flag fired on both clauses (A2 `spot_checks.judge_validity`).
The owner tagged the 19 disagreements: 7 *full*, 12 *partial*, 0 *none*, 0 *other*
(`disagreement-tags.json` `counts.tags`). On those tags none is a misreading of a passage with no answer.
The owner discussed some items with an LLM before deciding; the tags are descriptive, exploratory, and
not an independent annotation.

**Chunk-boundary control (exploratory).** Re-judging 300 stage-A pairs against chunks that respect
sentence and section boundaries flipped 28 of 249 pairs on which C and E had agreed, against 7 of 249 in
the test-retest null; on tiebroken pairs 12 of 51 against 13 of 51, indistinguishable from the null. 24 of
the 28 agreed-pair lost answers were found in an overlapping control chunk (`control-judge.json`
`metrics.by_route`, `metrics.retest.by_route`, `metrics.cover_any.split.by_route`). The route split and
the null were added after the overall rate had been seen.

### Decision ledger, 2026-11-05 (owner decisions)

| decision | what it fixes | where it is recorded |
|---|---|---|
| the flag phrase | every result states *judge validity below threshold (§13.5)*, in prose and in every table | RESULTS *The two owner spot checks* |
| D-3 | the glossary serving statement uses the nearest frozen rule: *not resolved*, beside H1 and H1b confirmed; no threshold or reading changed | DEVIATIONS D-3 |
| drift is report-only | the Opus drift reference lines are reported and never pause labelling; only the arm gap could (R29) | RESULTS *Judge reporting* |
| the collision split stays absent | H1 by alias collision is reported as *absent* (no collision annotation was supplied) rather than built after the results | RESULTS *What is not reported*; B `layer2.h1_by_collision` |
| the control's owner agreement dropped | the control carries no owner labels; every control label is the frozen judge's | ADDENDUM *What differs from the plan*; control-judge `note` |
| the test-retest null approved | the same 300 stage-A windows re-judged unchanged, as the floor for the control | ADDENDUM *What differs from the plan* |
| a confirmatory paired test approved (owner, 2026-11-05, after the addendum merged; this supersedes the addendum's "none has been run or approved", which was true when it was written) | a new pre-registered test of the agreed-pair *answers → not* pattern on fresh pairs. **Its pre-registration is a draft in review (not frozen); nothing has run.** | this entry |
| the paper tier started | an outline and two drafted sections are in review as a draft pull request, not yet merged into `docs/papers/`; its numbers must resolve to the record's artifacts | this entry |

### Open questions

- **Review-split policy:** answered by the owner on 2026-11-05: pre-registrations, data paths, auth and durable formats get two independent reviews; record-only and docs changes one.
- **Gateway refusals at 00:26Z and 11:04Z (2026-11-05):** the gateway twice refused study calls (from the private call
  ledger, not a committed artifact); the cause is unexplained as of this entry. Neither touched the control's three steps, whose 124 gateway calls all
  returned `ok` (ADDENDUM *What was run, and when*).
