<!-- experiment: Alias study (OA-1000), pre-registered | 2026-11-05 | COMPLETE — H1 confirmed, H1b confirmed, H2 not confirmed; judge validity below threshold (§13.5) on every result -->
# Alias study (OA-1000) results: glossary attachment and citation-neighbour growth, pre-registered

**Status: COMPLETE.** Both post-freeze stages ran and were analysed as the frozen pre-registration
defines them (prereg §5.7). **Family G: H1 confirmed, H1b confirmed. Family N: H2 not confirmed.** Every
result in this file carries the flag ***judge validity below threshold (§13.5)***: the owner's 100-pair spot
check agreed with the final judge label on 0.87 re-weighted and on 0.82 of the *answers* stratum, both
below the frozen 0.90 rule, so found@10-gold is printed beside every Layer-1, Layer-2 and per-cell figure as
the prereg's named sensitivity. The labels *confirmed*, *not confirmed* and *GATE-NOT-EVALUABLE* are the committed statistics
script's own (`layer1.<H>.status`). Every number in this file is in a committed artifact under
[`artifacts/`](artifacts/) or in [`DEVIATIONS.md`](DEVIATIONS.md), and each table names its source and
key path. Three deviations touch what is reported: **D-1** (the tercile cut points and the other R6 inputs
were built after the stage-A analysis had run; the binding figures are the first re-run with them),
**D-2** (H1b's found@10-gold figure was computed afterwards, display only) and **D-3** (wording only: the
frozen §3.3 table has no serving-statement row for a *not resolved* glossary reading beside H1 confirmed).

## Provenance

| | |
|---|---|
| pre-registration | [prereg/PREREG-alias-study.md](prereg/PREREG-alias-study.md), sha256 `d54f4201a4d1281dd895adeca292162999b55109e518129c87f61af473e0822e`, frozen 2026-11-04T05:39:34Z on the owner's go at commit `e6ecfa6`; signed tag `prereg-alias-study-2026-11`; never edited. Every run verified the sha (`study.prereg_verified` true in both results files). (Codes such as R6, O10 and P2 point into the prereg's registers: **R** the owner decisions of 2026-11-02 and 2026-11-03, R1–R11 adopted from an independent methodology review; **O** the open decisions of §14, each settled by the owner; **P** the pre-freeze corrections of 2026-11-03. The prereg's D1–D10 are its owner decisions of 2026-11-01; D-1 to D-3, with a hyphen, are the post-freeze deviations.) |
| deviations | [DEVIATIONS.md](DEVIATIONS.md): **D-1** the R6 inputs (topic, citation count, text similarity, XML-only, added chunks, neighbour attribution) and the three declared subset lists were built after the stage-A analysis ran; the tercile cut points are therefore post hoc; the first re-run with them (A2) is the binding stage-A analysis. **D-2** H1b's found@10-gold was computed afterwards by a new script that decides nothing. **D-3** (wording only) the frozen §3.3 table has no serving-statement row for a *not resolved* glossary reading beside H1 confirmed; the nearest frozen rule is used and no threshold or reading changed |
| stages | **stage A**: 19 retrieval legs (5 Layer-1 attachments × default, norerank, BM25, plus 4 noise repeats; 20,729 calls, every leg 1,091 rows, 0 failed), 14 judged legs, 9,205 deduplicated pairs; committed 2026-11-04T12:12:27Z (`artifacts/stage-a-commit.public.json`, commit sha256 `f53ca9d0…`). **Stage B**: the remaining 9 cells and the placebo leg (10 legs), stage-B judging 15,354 further pairs; 24,559 in all |
| code pins | runs: stage A at `d0d3555`, stage B at `812fea0` (`runs.<leg>.commit`), every run `citable` true, `mode main`. Statistics: `alias_study_stats.py` at `15ee347` for the binding stage-A re-run (**A2**, recorded 2026-11-04T23:18:52Z) and for stage B (2026-11-05T14:15:52Z); inputs builder at `a83d520` (D-1); H1b-gold display at `a179383` (D-2); recount and replay at `3c138ab`; power at `617d28d`; the disagreement-tag counts at `d93eddaa`. Every provenance block records `dirty false`, `untracked 0`, `citable true`, `in_image false` (no tools image; commit and clean tree recorded instead) |
| artifacts | [`results-stage-A2.public.json`](artifacts/results-stage-A2.public.json) (Layer 1, binding), [`results-stage-B.public.json`](artifacts/results-stage-B.public.json) (Layer 2 and every cell), [`h1b-gold.public.json`](artifacts/h1b-gold.public.json) (D-2), [`stage-a-commit.public.json`](artifacts/stage-a-commit.public.json), [`spot-records-counts.json`](artifacts/spot-records-counts.json), [`spot-judge-record.json`](artifacts/spot-judge-record.json), [`disagreement-tags.json`](artifacts/disagreement-tags.json) (made by [`make_disagreement_tags.py`](artifacts/make_disagreement_tags.py)), [`tercile-cuts.json`](artifacts/tercile-cuts.json), [`power-v4.json`](artifacts/power-v4.json), [`pilot-report.json`](artifacts/pilot-report.json), [`record-recount.json`](artifacts/record-recount.json), [`judge-rule-replay.json`](artifacts/judge-rule-replay.json). Public copies hold counts, rates, CIs, p values, model ids, version strings, commit shas and sha256s of the private files only (prereg §11) |
| records | the frozen record set (sha256 `09fc5ca4…`, 1,091 accepted; `record_set`), the balanced set of 900 on 300 papers (600 G, 300 C; ids sha256 `2be86f8c…`), the 191 held-out records as the descriptive leg. Recounted by committed code: every one of the 95 checked figures of prereg §4 matches (`record-recount.json`, `checks`) |
| corpus and cells | OA-1000 (32,765 chunks, 512-token windows `tok512o0`; the token counts of these windows are in the SFR-Embedding-Mistral tokenizer, as the control addendum's `chunker` block records) plus the v4 expanded arms (2,423 added papers each; neighbour 88,765 chunks, random-1 84,762, random-2 84,552; prereg §5.1). 9 bench collections written once (R26); 24 cells + 4 noise repeats + the placebo leg = 29 legs, every leg 1,091 rows (`legs_read`) |
| judge | C `Qwen3.6-35B-A3B` (n 5, T 0.7, thinking) + E `Qwen3.8-27B` (n 1), tiebreak `Gemini 4 Argon`, drift reference `Claude Opus 5.5` on 5 % of pairs, prompt `alias-judge/2` (Opus: `alias-judge/1`) (prereg §6.3); unjudged pairs 0 (`judge.unjudged`), so no figure carries *judge incomplete* |
| chunk-boundary control | an exploratory, post-freeze judge-only control that changes no figure here is recorded in [`ADDENDUM-2026-11-05-chunk-boundary-control.md`](ADDENDUM-2026-11-05-chunk-boundary-control.md); its numbers are not restated in this file |

## Contents

- [The answer](#the-answer)
- [What found@10 measures](#what-found10-measures)
- [The two owner spot checks, and the judge-validity flag](#the-two-owner-spot-checks-and-the-judge-validity-flag)
- [Confirmatory table (stage A, Layer 1)](#confirmatory-table-stage-a-layer-1)
- [Printed beside the Layer-1 members](#printed-beside-the-layer-1-members)
- [Sensitivities](#sensitivities)
- [Layer 2 (stage B): serving decisions and secondary figures](#layer-2-stage-b-serving-decisions-and-secondary-figures)
- [Judge reporting (§6.6)](#judge-reporting-66)
- [Manipulation checks](#manipulation-checks)
- [What this shows](#what-this-shows)
- [What is not reported](#what-is-not-reported)
- [Limitations](#limitations)
- [Reproducing](#reproducing)

## The answer

The study asked two questions (prereg §1). Does attaching a paper's merged glossary (symbols and defined
abbreviations) make a passage findable when the question uses the full term and the passage uses only the
short form? And does growing the collection with the study papers' citation neighbours cost more than
growing it with the same number of random same-field papers? Every glossary claim below is *for questions
using the glossary's primary term* (prereg §3, R3), and every result sentence carries ***judge validity
below threshold (§13.5)***.

**G and C records.** A **G record** is a question asked with the full term whose answering passage uses
only the paper's short form: a symbol, or an abbreviation the paper defines once, somewhere else. A **C
record** holds the same machinery fixed on a passage that already carries the term, so a glossary effect
on G can be told apart from a general change in what is retrieved (prereg §1, §4). The balanced set holds
300 papers with exactly 2 G and 1 C each: 600 G and 300 C records, 900 in all. A **point** is one
percentage point of records ([What found@10 measures](#what-found10-measures)).

| | H1 (Family G): merged > none on 600 G, base × default, one-sided | H1b (Family G): the G gain exceeds the C change, fixed sequence after H1 | H2 (Family N): neighbour < random average on 900, default × none, one-sided, gold-only gate |
|---|---|---|---|
| **label** | **confirmed**; judge validity below threshold (§13.5); C gate ceiling-limited; not *fragile* (D-1) | **confirmed**; judge validity below threshold (§13.5); never *fragile* (exempt) | **not confirmed**; judge validity below threshold (§13.5); within random-draw variation; within the judge false-yes bound; not *fragile* (D-1) |
| found@10, net (points) [95 % CI] | +1.00 [+0.33, +1.83] | +1.33 [+0.50, +2.50] | +0.06 [−0.72, +0.78] |
| found@10-gold, net (points) | +5.50 | +5.50 [+3.17, +7.83] (display only, D-2) | **−2.17** [−3.50, −0.94] (the direction gate: passes) |
| gained / lost records | 6 / 0 | 7 / 0 (papers) | 11 / 9 |
| p (level 0.05) | 0.015625 | 0.0293 | 0.615 |

- **H1.** The merged glossary raised judged found@10 on the 600 G records by 1.00 point: 6 more of the 600
  G records found, 591 → 597 of 600 (`layer2.placebo.G.found10`, `base|none|default` and
  `base|merged|default`); 6 records gained, 0 lost, in 6 of 300 papers (exact sign-flip p 0.015625,
  which is also the minimum attainable p with 6
  non-zero papers), with found@10-gold up 5.50 points beside it; **confirmed**, judge validity below
  threshold (§13.5); the C gate is *ceiling-limited* (baseline C found@10 is 100.0 %, headroom 0, so the
  gate could not have fired on a C rise) and did not fire; not *fragile* under the bootstrap-t or topic
  clustering (D-1). The realised G discordance was 1.0 % against the 5 % and 10 % planning scenarios.
- **H1b.** The gain on G exceeded the change on C by 1.33 points: the G change +1.00 minus the C change
  −0.33, which is 1 of the 300 C records lost, 300 → 299 of 300 (`layer2.placebo.C.found10`;
  `layer1.H1b.baseline_c_found10` 300 of 300); 7 papers non-zero, all positive (label randomization p
  0.0293, also its minimum attainable p), read beside a baseline C found@10 of 100.0 % with
  no headroom; **confirmed**, judge validity below threshold (§13.5). Its power floor at the planning
  scenarios was .67 and .54. On found@10-gold the same estimator gives +5.50 points [3.17, 7.83], computed
  post hoc as a display (D-2). By prereg §3.2, H1 confirmed with H1b confirmed reads: the merged glossary
  makes alias passages findable, and the gain on G exceeds the change on C, an alias-specific gain, for
  questions using the glossary's primary term.
- **H2.** On found@10-gold the neighbour arm found 2.17 points fewer than the random average: 776 against
  795.5 of 900 records (random-1 798, random-2 793; `layer2.cells.<arm>|none|default.found10_gold`); 35
  lost, 16 gained; 95 % CI [−3.50, −0.94], so the gold-only direction gate passes. On judged found@10 the
  net is +0.06 points: 882 against 881.5 of 900 (random-1 882, random-2 881; `.found10`); 11 gained, 9
  lost; p 0.615. So H2 is **not confirmed**, judge validity below threshold (§13.5), within random-draw
  variation (the draws differ by 0.11 points, 882 against 881 records), within the judge false-yes bound
  (B 2.48 points), not *fragile* under the bootstrap-t, topic clustering or the volume covariate (D-1). The
  power to confirm at the planning scenarios was .96 and .90, so a drop of the planned sizes would have been
  seen (§8's mean change μ = q (2p − 1): 3.5 and 4.0 points at q .05 / p .85 and q .10 / p .70, where q is
  defined at the planned dose; about 3.0 and 3.5 points at the realised dose 0.865, derived as 3.5 × 0.865
  and 4.0 × 0.865). At the realised discordance (2.2 %, 20 discordant records), which lies between the
  tabulated q 2 % and q 3 % scenarios, the power to confirm at p .85 lay between .73 and .88 (`power-v4.json`
  `simulation.H2.rho1.p0.85_q0.02.0.05.confirm_a0.7` 0.7345, `…q0.03…` 0.878), so only the equivalence bound
  reads smaller effects: on judged found@10 it reads *no effect larger than 1.39 points* (≈ 12.5 of 900
  records; the realised 80 %-power MDE at the realised discordance, `layer1.H2.equivalence`). The pattern
  is consistent with neighbours displacing gold chunks and supplying judged answers in their place (a
  reading from per-cell totals; no per-record transition count is in the artifact; the other-paper hits
  are judged labels, judge validity below threshold (§13.5); [What this shows](#what-this-shows), item 3).

Layer 2 (stage B, descriptive): the glossary serving decision is ***not resolved*** (lift +1.00 point, 6
of the 600 G records, below the 2-point action threshold, which is 12 of the 600), quoted beside H1
confirmed and H1b confirmed (D-3); the reranker decision is *keep the default (non-inferior to BM25)*
(default − BM25 on the neighbour arm +6.33 points [4.67, 8.11]: 882 against 825 of 900 records,
`layer2.cells.neighbour|none|default` and `neighbour|none|bm25`); related-paper growth is *not a concern:
no effect larger than 1.39 points* (≈ 12.5 of 900 records), beside H2 not confirmed. Every Layer-2 figure
carries judge validity below threshold (§13.5) and has found@10-gold
beside it ([Layer 2](#layer-2-stage-b-serving-decisions-and-secondary-figures)).

## What found@10 measures

found@10 is the share of records with a **hit**: the record's gold chunk is in the top 10, or some other
top-10 chunk, judged on its own, carries the final label *answers* (prereg §6.1–6.2). The judge sees one
pair at a time: the question, its term, the passage, the passage paper's title and that paper's own
glossary definitions for the short forms in the passage (prompt `alias-judge/2`); it sees no arm,
retriever, rank or other chunk. So a hit means **at least one top-10 chunk that, read alone, states the
core of the answer**. found@10 is a measure of **reach**, not of answer quality: it does not grade how well
the chunk answers, does not credit two chunks that together would answer, and does not read rank (RR@10 is
the rank-based secondary of Layer 2). A hit from another paper counts. found@10-gold is the gold-only
version, the gold chunk in the top 10 and nothing judged, and it is printed beside every Layer-1, Layer-2
and per-cell figure (not beside the sensitivity, subgroup and single-draw displays; [What is not
reported](#what-is-not-reported)).

**What a point is.** found@10 is the share of records for which at least one top-10 chunk, read alone,
states the core of the answer. A **point** is one percentage point of that share. One record is 0.17
points on the 600 G records (100 / 600), 0.33 points on the 300 C records (100 / 300) and 0.11 points on
the 900 balanced records (100 / 900). So H1's +1.00 point is 6 G records, H1b's C change of −0.33 is 1 C
record, and H2's +0.06 is half a record on the average of two random draws; each carries judge validity
below threshold (§13.5). found@10-gold is quoted in the same points.

## The two owner spot checks, and the judge-validity flag

Both checks were labelled by the owner and committed before any analysis (`study.gate.spot_checks_sha256`
in both results files equals the ingest sha256s; `spot_checks_waived` false). Every Wilson upper limit in
this file is rounded up, never to nearest (prereg §3.3, R24).

**Record validity (prereg §13.4, the 0.85 rule).** 40 records drawn from the 900 by seeded hash, labelled
valid / invalid blind to kind and arm: **37 of 40 valid, 0.925**, Wilson 95 % [0.801, 0.975]
(`spot_checks.record_validity`; `spot-records-counts.json`; sheet sha256 `8fd9987e…`). The point estimate
is at or above 0.85, so the rule does not fire and no sentence carries *validity below threshold*
(`below_threshold` false). The interval is printed and decides nothing.

**Judge validity (prereg §13.5, the 0.90 rule).** 100 non-gold pairs labelled in stage A, stratified by
the final judge label (50 *answers* where C and E agreed, 33 *not* where they agreed, 17 tiebroken),
spread across the arms, blind to the label, stratum and arm (`spot-judge-record.json`: n 100, ingest
sha256 `4b723b9c…`, sheet sha256 `f0e34a7f…`, stage-A commit `f53ca9d0…`). Source:
`spot_checks.judge_validity` in `results-stage-A2.public.json`.

| stratum | owner agrees with the final label | rate | Wilson 95 % |
|---|---|---|---|
| *answers* (C and E agreed) | 41 of 50 | **0.82** | [0.692, 0.903] |
| *not* (C and E agreed) | 30 of 33 | 0.909 | [0.764, 0.969] |
| tiebroken (Argon's label final) | 10 of 17 | 0.588 | [0.360, 0.784] |
| all 100 (unweighted) | 81 of 100 | 0.81 | — |
| **re-weighted to the stage-A label shares** | — | **0.872** | — |

Final label × owner label, over the 100 (`metrics.table`; final *answers* and owner *answers* 47, final
*answers* and owner *not* 13, final *not* and owner *answers* 6, both *not* 34): precision for *answers*
0.783, recall 0.887, specificity 0.723, F1 0.832, Cohen's κ 0.615, Gwet's AC1 0.626 (n 100). Per arm, the
share of owner-*not* pairs that the final label calls *answers* (the §6.3 false-yes rate on the spot check):
base 4 of 17 [0.096, 0.473], neighbour 5 of 14 [0.163, 0.613], random 4 of 16 [0.102, 0.495]
(`false_yes_per_arm`).

**The rule fired on both of its clauses**: the re-weighted agreement 0.872 is below 0.90 at the point
estimate, and the *answers*-stratum agreement 0.82 is below 0.90 (`below_threshold` true). The frozen
text fixes the consequence and no wording: *stated in every result sentence, and found@10-gold reported
alongside each found@10 result as the sensitivity*. This file states it as ***judge validity below
threshold (§13.5)*** on every result, in prose and in every result table, and prints found@10-gold beside
every Layer-1, Layer-2 and per-cell figure. Nothing was re-labelled, and no figure changes.

**What the 19 disagreements were (exploratory, post hoc; descriptive, owner-supplied tags).** The owner later tagged each of the
19 pairs on which the owner's spot-check label and the final judge label disagree as *full* (the passage
answers the question in full), *partial*, *none* or *other answer*. Source: `disagreement-tags.json`,
counts only, made by `make_disagreement_tags.py` from the owner's private tag export and the 19 sheet items
(input sha256s `825e4989…` and `d6176dac…`; no id or text copied out). In the sentence that uses them: the
owner set every tag; for some items the owner discussed alternative readings with an LLM (GPT-6) before
deciding, by the owner's own account, recorded in the private provenance note beside the tag export; the
tags are descriptive, change no figure, and are not an independent annotation.

| direction of the disagreement (`counts.by_direction`) | n | full | partial | none | other |
|---|---|---|---|---|---|
| judge *answers*, owner *not* | 13 | 5 | 8 | 0 | 0 |
| owner *answers*, judge *not* | 6 | 2 | 4 | 0 | 0 |
| **all 19** (`counts.tags`) | **19** | **7** | **12** | **0** | **0** |

By spot-check stratum (`counts.by_stratum`): *answers* 9 (5 full, 4 partial), *not* 3 (1 full, 2
partial), tiebroken 7 (1 full, 6 partial). **The analysis's reading, exploratory (post hoc), labelled as such:**
none of the 19 is tagged *none* or *other*, so on the owner's tags none is a judge misreading of a passage that holds no
answer; all 19 sit on the partial-versus-full line. Read with *partial = not answering*, the judge's label
matches the tag-implied label on 9 of 19 and the owner's on 10 of 19; read with *partial = answers*, the
judge matches on 13 of 19 and the owner on 6 of 19 (`counts.readings`). The tags do not change the flag:
the frozen rule reads the owner's labels as the reference, and the flag stands on every result.

## Confirmatory table (stage A, Layer 1)

Source: `results-stage-A2.public.json`, `layer1.<H>` (the binding stage-A re-run of D-1, statistics at
`15ee347`; `analysis.reads` "Layer 1 only (stage A)", `study.analysis_stage` A). Tests (prereg §7): H1 and
H2 by the exact conditional paper sign-flip, H1b by the exact G / C label randomization within paper;
one-sided at α 0.05, two separate families (R1), no level passing between them. CIs are paper-bootstrap
percentile, 10,000 resamples, seed `261101|alias|boot`; the net is in points. The stage-B file carries the
same Layer-1 block (a stage-A label is never changed by stage B; its `layer1` values equal A2's on every
field quoted here, except H2's per-arm false-yes rates, which it reads from the cumulative drift sample).
**Every row: judge validity below threshold (§13.5)**; found@10-gold beside it.

| member | n (papers; non-zero) | net points [95 % CI]; 90 % CI | gained / lost | p (method) | min attainable p | mid-p | MDE split | realised discordance | power floor at q .05 / p .85 and q .10 / p .70 | found@10-gold net | label |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **H1** G, merged − none, base × default, one-sided > 0 | 600 (300; 6) | +1.00 [+0.33, +1.83]; [+0.33, +1.67] | 6 / 0 | 0.015625 (enumeration) | 0.015625 | 0.0078 | 6 / 0 | 0.010 | .987 / .930 (normal); .99 / .92 (simulation, `power-v4.json`) | +5.50 | **confirmed**; judge validity below threshold (§13.5); C gate ceiling-limited; not *fragile* (D-1) |
| **H1b** G − C difference-in-differences, base × default, one-sided > 0 | 300 (300; 7) | +1.33 [+0.50, +2.50]; [+0.50, +2.17] | 7 / 0 (papers) | 0.0293 (lattice) | 0.0293 | 0.0146 | 7 / 0 | 0.023 | .67 / .54 (simulation; `layer1.H1b.power_floor`) | +5.50 [+3.17, +7.83], display only (D-2; `h1b-gold.public.json`) | **confirmed**; judge validity below threshold (§13.5) |
| **H2** neighbour − mean(random-1, random-2), default × none, one-sided < 0 | 900 (300; 19) | +0.06 [−0.72, +0.78]; [−0.56, +0.67] | 11 / 9 | 0.615 (enumeration) | 1.9 × 10⁻⁶ | 0.557 | 15 / 5 | 0.022 | .999 / .985 (normal, test alone); .96 / .90 (simulation, power to confirm at a = 0.7, `power-v4.json` `h2_at_q05_p85_lever`, `simulation.H2.rho1`) | **−2.17** [−3.50, −0.94]; gate passes | **not confirmed**; judge validity below threshold (§13.5); within random-draw variation; within the judge false-yes bound; not *fragile* (D-1) |

*For H2 the gold-only figure is the one quoted first (§3.4); the column order is the table's layout.*

- **MDE split** is `mde_split`: the gained / lost split of the observed discordant records that the member's
  one-sided test needed at its level. H1 needed 6 / 0 and saw 6 / 0; H2 needed 15 / 5 and saw 11 / 9.
- **Minimum attainable p.** H1's p equals its floor (2⁻⁶ with 6 non-zero papers), and so does H1b's
  (0.0293, the randomization floor of §8): both confirmations rest on every discordant paper moving the
  hypothesised way. Each is above the GATE-NOT-EVALUABLE line (the floor is below 0.05), so neither member
  was gated.
- **Powered or unresolved.** H1 and H2 had power ≥ .80 at both planning scenarios. H2's *not confirmed*, judge validity
  below threshold (§13.5), is therefore a null for the hypothesised drop at those scenarios (§8's μ = q (2p − 1): 3.5 and 4.0 points at
  q .05 / p .85 and q .10 / p .70, at the planned dose where q is defined; about 3.0 and 3.5 at the
  realised dose 0.865, derived). At the realised discordance (2.2 %), between the tabulated q 2 % and 3 %,
  the power to confirm at p .85 lay between .73 and .88 (`simulation.H2.rho1.p0.85_q0.02.0.05.confirm_a0.7`
  0.7345, `…q0.03…` 0.878), so only the equivalence bound reads smaller effects: it
  (`layer1.H2.equivalence`, within true) reads *no effect larger than 1.3863 points* on judged found@10, at
  the realised discordance of 2.2 % (20 of 900 records; the planning MDE at q 5 % was 2.08 points,
  `power-v4.json` `mde80_points.H2`). H1b's power was below .80 at both scenarios (.67 / .54); it was
  confirmed, so no null is read from it. H1's equivalence reading is *not resolved* (90 % CI [0.33, 1.67]
  is not within ±1.1365, the realised MDE at 1.0 % discordance).
- **Missing data:** 0 records excluded in every member (`missing` 0; `missing_or_dropped` 0);
  `family_missing_as_not_found` equals the primary on every member. Every leg read 1,091 rows and 0 failed
  calls (`stage-a-commit.public.json` `counts.hits_failed` 0).
- **p_perm** (Monte Carlo, printed as a check, never deciding): H1 0.0180, H2 0.6195; H1b has none
  (lattice).

## Printed beside the Layer-1 members

Source: `layer1.H1.c_gate`, `layer1.H1.reach`, `layer1.H2.*`, `fixed_sensitivities`, all in
`results-stage-A2.public.json`. Each figure below is read with judge validity below threshold (§13.5).

**The C gate on H1 (rule C, §3.1).** ΔG = +1.00 point, so the gate was evaluated. ΔC (merged − none on the
300 C records) = **−0.33 points** (0 gained, 1 lost), 95 % CI [−1.00, 0.00]; ratio ΔC / ΔG = −0.33; none
of the three conditions holds (same sign false, |ΔC| ≥ ½ |ΔG| false, CI excludes 0 false); verdict
*passes*, `fires` false. **Baseline C found@10 (base × default × none) is 300 of 300 = 100.0 %, headroom
0 records, 0.0 points**, which is below ½ ΔG, so the note reads ***C gate ceiling-limited***: the gate could
not have fired on a C rise however large (`ceiling_limited` true). The C drop is read in Layer 2 by the C
cost rule, below. On found@10-gold the C change is 0.00 (2 gained, 2 lost; `layer2.c_change.gold_only`).

**The reach gate on H1.** 600 of 600 G records render their own entry on the gold chunk (need 480); `ok`
true.

**Beside H1b.** The baseline C found@10 is 100.0 % with headroom 0.0 points (`layer1.H1b.headroom_points`),
the ceiling case prereg §2 item 4 anticipated: C could not rise, so H1b's difference-in-differences reads a
G gain against a C that could only fall or stay (it fell by 1 record).

**Beside H2 (§3.4).**
- **Gold-only direction gate:** net −2.17 points (`gold_only_net_points`), `gold_gate_pass` true. Quoted
  first in every H2 sentence, because the judged net lies within the false-yes bound.
- **Draw-to-draw gap:** found@10(random-1) − found@10(random-2) = 0.11 points (7 gained / 6 lost; 95 % CI
  [−0.67, +0.89]; `layer2.random1_minus_random2`); |H2's net| 0.06 ≤ 0.11, so every H2 sentence carries
  *within random-draw variation* (`within_random_draw_variation` true). H2 against each draw alone: vs
  random-1 net 0.00, p 0.603; vs random-2 net +0.11, p 0.696; both not confirmed
  (`sensitivities.H2_vs_random1_alone`, `…random2_alone`).
- **Judge false-yes bound B** (`judge_false_yes_bound`, read from the frozen stage-A judge summary and
  cross-checked against the final rows, `cross_check_with_final_rows` true): among drift pairs the final
  label calls *answers* and Opus answered, the share Opus calls *not* is 4 of 35 in the random arm (Wilson
  upper limit U_r **0.2596**) and 14 of 110 pooled (U_p 0.2024); U = max = 0.2596 (from the random arm).
  J_r = 86 of 900 records rest on judged pairs only in the random-draw cells (averaged over the draws).
  **B = 100 · 0.2596 · 86 / 900 = 2.48 points.** |H2's net| 0.06 ≤ B, so every H2 sentence carries *within
  the judge false-yes bound* (`within_bound` true); B never changes the label.
- **Per-arm false-yes rates** (the §6.3 rate: Opus-*not* drift pairs the final label calls *answers*,
  stage A, `false_yes_per_arm`): base 6 of 96 = 6.25 % [2.9, 13.0], neighbour 4 of 108 = 3.70 % [1.45,
  9.14], random 4 of 100 = 4.00 % [1.57, 9.84]. The cumulative figures after stage B are in
  [Judge reporting](#judge-reporting-66).
- **Hits by origin** (`fixed_sensitivities.origin.hits_by_origin`, descriptive, all 900 records, default ×
  none): neighbour arm gold 776, same-paper judged 39, other-paper judged **67**; random-1 798 / 36 / 48;
  random-2 793 / 39 / 49. **Displacing chunks** (`origin.displacing_chunks`): for the 9 records that are a
  hit in the random average but not in the neighbour arm, the top-10 chunks outranking the gold came from
  the record's own paper's neighbours 21 times, another study paper's neighbours 34 times, and base 35
  times.

## Sensitivities

None of these decides anything. The three re-tests of R14 (bootstrap-t and topic clustering for H1; those
two and the volume covariate for H2) can attach *fragile*; nothing else can. **Every figure here carries
judge validity below threshold (§13.5).**

### Fixed sensitivities (R6) and the *fragile* reading, D-1

Source: `fixed_sensitivities.H1`, `.H2`. The R6 inputs (topic, citation count, text similarity, XML-only,
added chunks) were built after the stage-A analysis had run and read (**D-1**); the binding figures are
the first re-run with them. `fragile` is false for H1 and for H2: no re-test flips a verdict
(`layer1.<H>.fragile.flipped_by` empty).

| re-test | H1 | H2 |
|---|---|---|
| bootstrap-t, one-sided paper-cluster (10,000 resamples) | t 2.470, p 0.0007, **confirmed** | t 0.149, p 0.542, **not confirmed** |
| topic-level clustering (230 OpenAlex primary topics; 0 papers without a topic) | 6 non-zero clusters, p 0.015625 (enumeration), **confirmed** | 18 non-zero clusters, p 0.6125, **not confirmed** |
| chunk-volume covariate, Freedman–Lane sign-flip of the restricted residuals, studentized intercept, 10,000 flips (298 papers; 2 left out, no added paper) | — | adjusted net +0.03 points, slope 6.4 × 10⁻⁵ per chunk, p 0.526, **not confirmed** (the primary's unadjusted gold-only gate reused) |
| studentized identity check (decides nothing: with one score per paper its exact p is the primary's) | p 0.015625 | p 0.6149 |

So H1 is not *fragile* and H2 is not *fragile* (D-1: the topic input behind one re-test of each, and the
volume input behind H2's third, were built after the stage-A outputs had been read; the first re-run with
them is this one).

### Subgroup displays of H2 (never *fragile*, no test at the member's level)

Source: `fixed_sensitivities.H2.terciles`, `.xml_only_stratum`; cut points in `tercile-cuts.json`. **The
tercile cut points were fixed after the stage-A outputs had been read (D-1)**: citation count [10, 33]
(terciles of 101 / 100 / 99 papers), text similarity [0.7096, 0.7362] (100 / 99 / 99 of the 298 papers with
a value; 2 papers have no attributed neighbour and no value). Net is neighbour − random average, judged
found@10, with its 95 % CI; every row is a subgroup display with cut points fixed after the stage-A
outputs had been read (D-1), and carries judge validity below threshold (§13.5).

| split (D-1 cut points) | tercile | papers (records) | net points [95 % CI] | gained / lost |
|---|---|---|---|---|
| citation count (cuts fixed after stage-A outputs were read, D-1) | T1 (≤ 10 citations) | 101 (303) | −1.16 [−2.64, 0.00] | 1 / 6 |
| | T2 (11–33) | 100 (300) | +0.33 [−1.00, +1.67] | 5 / 2 |
| | T3 (> 33) | 99 (297) | +1.01 [0.00, +2.19] | 5 / 1 |
| text similarity to attributed neighbours (cuts fixed after stage-A outputs were read, D-1) | T1 (≤ 0.7096) | 100 (300) | +0.50 [0.00, +1.33] | 2 / 0 |
| | T2 | 99 (297) | −0.51 [−2.02, +0.84] | 4 / 5 |
| | T3 (> 0.7362) | 99 (297) | +0.17 [−1.35, +1.68] | 5 / 4 |
| XML-only stratum (every added paper JATS-sourced; 2 of the 145 vacuously) | — | 145 (435) | −0.34 [−1.49, +0.57] | 5 / 5 |

### Declared sensitivities (never deciding, never *fragile*)

Source: `sensitivities.<name>.<H>` (net points; p; the script's status label). Every entry carries judge
validity below threshold (§13.5).

| sensitivity | H1 | H1b | H2 |
|---|---|---|---|
| without the 47 Codex-verdict records (853 records, 293 papers) | n 561: +0.71, p 0.0625, **GATE-NOT-EVALUABLE** (its minimum attainable p exceeds 0.05 once the 47 are removed) | n 286: +1.05, **absent (not tested)**, because H1 is not confirmed on this subset | n 853: −0.12, p 0.439, not confirmed |
| without the 2 calibration-overlap records (898) | n 600: +1.00, p 0.015625, confirmed | n 298: +1.34, p 0.0293, confirmed | n 898: +0.06, p 0.615, not confirmed |
| without the 13 pilot papers (861 records, 287 papers) | n 574: +1.05, p 0.015625, confirmed | n 287: +1.39, p 0.0293, confirmed | n 861: +0.12, p 0.671, not confirmed |
| without the 8 validation papers (876 records, 292 papers; O10) | n 584: +1.03, p 0.015625, confirmed | n 292: +1.37, p 0.0293, confirmed | n 876: +0.06, p 0.615, not confirmed |
| unjudged pairs as *answering* (there are none) | +1.00, p 0.015625, confirmed | +1.33, p 0.0293, confirmed | +0.06, p 0.615, not confirmed |
| tiebroken pairs set to judge C's own label | +1.17, p 0.0078, confirmed | +1.50, p 0.0195, confirmed | −0.11, p 0.445, not confirmed |
| tiebroken pairs set to judge E's own label | +0.83, p 0.03125, confirmed | +0.83, p 0.132, **GATE-NOT-EVALUABLE** | +0.28, p 0.807, not confirmed |
| `family_missing_as_not_found` | = primary | = primary | = primary |

Two declared sensitivities change a label. Without the 47 Codex-verdict records H1 cannot be read
(GATE-NOT-EVALUABLE: too few non-zero papers remain for the level), and H1b is then not tested; with the
tiebroken pairs set to judge E's label, H1b is GATE-NOT-EVALUABLE. These are displays; the primary
labels stand as frozen.

## Layer 2 (stage B): serving decisions and secondary figures

Source: `results-stage-B.public.json`, `layer2.*` (statistics at `15ee347`, 2026-11-05T14:15:52Z;
`analysis.reads` "everything (stage B)"; 29 legs, 78,005 demanded pair instances, 0 without a final
label). Every figure is **Layer 2, descriptive**, with a 95 % paper-bootstrap percentile CI, no p value and
no gate; the thresholds are judgement-based (prereg §3.3). **Every figure carries judge validity below
threshold (§13.5)** and found@10-gold beside it.

### The three serving decisions (`layer2.decisions`, `layer2.serving_statements`)

| decision | figure (judged) | found@10-gold beside it | rule applied | reading, with the Layer-1 verdict beside it |
|---|---|---|---|---|
| attach the merged glossary | G lift L = **+1.00** [+0.33, +1.83] (600 G); ΔC = **−0.33** [−1.00, 0.00] (300 C) | L +5.50 [+3.50, +7.50]; ΔC 0.00 [−1.33, +1.33] | harm (L_hi < 0): no; C cost (ΔC < −1 or C_lo < −3.6): no; attach (L ≥ 2 and L_lo > 0): no, L < 2 | ***not resolved*** (Layer 2, descriptive), beside **H1 confirmed** and **H1b confirmed** (D-3); judge validity below threshold (§13.5). A C cost smaller than about 3.6 points cannot be ruled out with 300 C records (R12; C_lo here is −1.00) |
| keep the reranker default | D = default − BM25, neighbour arm, glossary none: **+6.33** [+4.67, +8.11] (62 gained / 5 lost of 900) | +14.44 [+12.11, +16.78] | non-inferiority margin −3: D_lo 4.67 > −3 | ***keep the default (non-inferior to BM25)*** (Layer 2, descriptive, no Layer-1 member); judge validity below threshold (§13.5) |
| related-paper growth is a concern | neighbour − random average: **+0.06** [−0.72, +0.78] | −2.17 [−3.50, −0.94] | a concern if ≤ −3 with the CI excluding 0: no | ***not a concern: no effect larger than 1.3863 points*** (equivalence, 90 % CI [−0.56, +0.67] within ±1.39), beside **H2 not confirmed**; judge validity below threshold (§13.5); within random-draw variation; within the judge false-yes bound |

*For H2 the gold-only figure is the one quoted first (§3.4); the column order is the table's layout.*

The serving statement for the glossary is the Layer-2 phrase *not resolved* read beside H1 confirmed and
H1b confirmed (the frozen §3.3 table has no row for this combination; DEVIATIONS D-3): the gain is
confirmed and alias-specific, and smaller than the 2-point threshold the owner fixed as worth acting on;
judge validity below threshold (§13.5).

### Other Layer-2 contrasts (900 balanced records; `layer2.<key>`)

| contrast | judged net [95 % CI]; gained / lost | found@10-gold net [95 % CI]; gained / lost | equivalence reading (judged) |
|---|---|---|---|
| **H3**: [default(neighbour) − default(base)] − [BM25(neighbour) − BM25(base)], glossary none | +1.22 [−0.56, +2.89]; 39 / 28 | +2.00 [−0.56, +4.56]; 77 / 59 | not resolved |
| neighbour − base, default × none | −1.00 [−2.11, 0.00]; 6 / 15 | **−7.22** [−9.00, −5.56]; 0 / 65 | not resolved |
| random average − base, default × none | −1.06 [−1.78, −0.33]; 4 / 17 | **−5.06** [−6.50, −3.72]; 0 / 57 | not resolved |
| random-1 − random-2, default × none (the draw gap) | +0.11 [−0.67, +0.89]; 7 / 6 | +0.56 [−0.56, +1.67]; 14 / 9 | no effect larger than 1.1177 points |
| neighbour − random average, default × none (the H2 figure) | +0.06 [−0.72, +0.78]; 11 / 9 | −2.17 [−3.50, −0.94]; 16 / 35 | no effect larger than 1.3863 points |

*For H2 the gold-only figure is the one quoted first (§3.4); the column order is the table's layout.*

**H1 and H2 under the other retrievers and corpora** (`layer2.h1_other_cells`, `layer2.h2_other`; judged
net [95 % CI], with the found@10-gold net in brackets after it; 600 G for H1, 900 for H2):

| H1 (merged − none) on | default | norerank | BM25 |
|---|---|---|---|
| base | +1.00 [+0.33, +1.83] (gold +5.50) — the primary | +2.50 [+1.17, +3.83] (gold +8.00) | +4.17 [+1.83, +6.67] (gold +13.17) |
| neighbour | +2.00 [+0.83, +3.33] (gold +9.50) | +3.17 [+1.50, +5.00] (gold +11.00) | −1.17 [−4.33, +2.00] (gold +9.00) |
| random-1 | +2.17 [+1.00, +3.50] (gold +8.00) | +3.67 [+2.00, +5.50] (gold +9.33) | +1.33 [−1.67, +4.33] (gold +10.00) |
| random-2 | +1.50 [+0.33, +2.83] (gold +9.67) | +3.00 [+1.17, +4.83] (gold +9.17) | +0.33 [−2.50, +3.17] (gold +9.67) |

| H2 (neighbour − random average) under | default | norerank | BM25 |
|---|---|---|---|
| glossary none | +0.06 [−0.72, +0.78] (gold −2.17) — the primary | −0.33 [−1.78, +1.06] (gold −5.67) | −0.50 [−1.83, +0.83] (gold −4.00) |
| glossary merged | +0.11 [−0.28, +0.50] (gold −2.44) | −0.89 [−2.11, +0.33] (gold −4.22) | **−2.28** [−3.56, −1.00] (gold −5.17) |

**H1 by alias kind** (`layer2.h1_by_alias_kind`, base × default): abbreviation (508 G, 289 papers) +0.98
[+0.20, +1.94], 5 gained / 0 lost, gold +6.10 [+3.91, +8.52]; symbol (88 G, 77 papers) +1.14 [0.00, +3.45],
1 / 0, gold +2.27 [0.00, +5.82]; both (4 G) 0.00, 0 / 0, printed and never interpreted. The split by
alias collision is **absent** (`h1_by_collision`: "no collision annotation supplied").

**RR@10** (`layer2.rr`, mean reciprocal rank in points, judged and gold-only; 95 % CI): H1 judged +8.74
[+6.94, +10.66], gold +15.03 [+12.74, +17.34]; H2 judged −0.58 [−1.64, +0.49], gold **−3.35** [−4.47,
−2.27]; H3 judged +3.11 [+1.31, +4.95], gold +2.05 [+0.26, +3.82]. Every RR@10 figure: judge validity
below threshold (§13.5).

### found@10 per cell, the 900 balanced records (`layer2.cells.<cell>.found10`, `.found10_gold`)

Points of 900; **found@10 (found@10-gold)**. Each noise repeat (`#2`) equals its first leg on every record
([Manipulation checks](#manipulation-checks)). Every cell: judge validity below threshold (§13.5).

| corpus × glossary | default | norerank | BM25 |
|---|---|---|---|
| base × none | 99.00 (93.44) | 96.11 (86.22) | 93.89 (81.00) |
| base × merged | 99.56 (97.11) | 97.56 (90.89) | 95.56 (87.89) |
| base × placebo | 99.00 (93.11) | — | — |
| neighbour × none | 98.00 (86.22) | 93.33 (72.11) | 91.67 (71.78) |
| neighbour × merged | 99.00 (91.56) | 95.00 (79.67) | 88.56 (73.67) |
| random-1 × none | 98.00 (88.67) | 93.44 (77.78) | 92.44 (75.78) |
| random-1 × merged | 99.11 (93.78) | 96.00 (84.11) | 91.89 (79.22) |
| random-2 × none | 97.89 (88.11) | 93.89 (77.78) | 91.89 (75.78) |
| random-2 × merged | 98.67 (94.22) | 95.78 (83.67) | 89.78 (78.44) |

On the G records alone, base × default (`layer2.placebo.G.found10`): none 591 of 600 (98.50), merged 597
(99.50), placebo 591 (98.50). On the C records (`layer2.placebo.C.found10`): none 300 of 300, merged 299,
placebo 300.

### The placebo glossary leg (R7, R21; `layer2.placebo`; descriptive, enters no test and no decision)

Placebo − none on G: 0.00 points [−0.50, +0.50] (1 gained / 1 lost), *no effect larger than 0.6562
points*; placebo − merged on G: −1.00 [−1.83, −0.33] (0 / 6). On C: placebo − none 0 discordant records,
*not resolved (0 discordant records)* (R24); placebo − merged +0.33 [0.00, +1.00] (1 / 0). Read with the
field coverage beside it (`field_coverage`): merged puts a field on **24,749 of 32,765** base chunks
(75.5 %, mean 71.2 characters per chunk, 94.3 where a field is rendered); the placebo on **817** chunks
(2.49 %, mean 0.76 characters, 30.4 where rendered). After the derangement 44 own entries were removed
from 38 papers' placebo lists and 0 papers were left empty (`placebo_dropped_own_entries`). So the
placebo adds far less text than merged, and the comparison bounds "extra text" only at that coverage.
Every figure here: judge validity below threshold (§13.5).

### The descriptive leg: the 191 held-out records, retrieved in the pilot

The 191 records on the 108 unsatisfied papers were run in every cell after the freeze and enter no test
and no decision (prereg §4.2); they are also the pre-freeze pilot's records, so every sentence quoting
them says *retrieved in the pilot*. On the Layer-1 attachments under the default retriever, retrieved in
the pilot (`layer2.cells.<cell>.descriptive_leg`; found@10 (found@10-gold) of 191; judge validity below
threshold (§13.5)): base × none 185 (180), base × merged 191 (185), neighbour × none 187 (172), random-1 ×
none 186 (175), random-2 × none 184 (173), base × placebo 185 (180).

## Judge reporting (§6.6)

Source: `judge` in `results-stage-A2.public.json` (stage A) and in `results-stage-B.public.json`
(cumulative after stage B); `stage-a-commit.public.json`.

**Sources of the final labels** (`judge.sources`, `judge.stages`): stage A 9,205 deduplicated pairs, 8,580
by C and E agreeing and 625 by the Argon tiebreak (6.8 %); stage B 15,354 pairs, 14,749 agree and 605
tiebroken (3.9 %); all 24,559 pairs, 23,329 agree and 1,230 tiebroken (5.0 %). Pairs per arm (by the
passage paper's arm): stage A base 3,243 / neighbour 2,525 / random 3,437; cumulative 7,718 / 7,057 /
9,784. **Unjudged: 0** in both stages (`unjudged` 0, share 0.0), so the 1 % rule of §6.3 did not fire and
no figure carries *judge incomplete*. Every served-model check passed (a mismatch stops the run;
`runs.<leg>.status` complete on all 13 runs).

**The Opus drift reference** (`judge.drift`; 5 % of each labelling batch, equal across arms; `alias-judge/1`;
never sets a label). An empty or unparseable Opus reply was re-issued at most once and the pair then
counted *no-reference* and excluded from the agreement, never a drift failure (prereg §6.3, §6.5):

| scope | sampled | no-reference (filtered) | answered | agree | agreement | reference line | below the line |
|---|---|---|---|---|---|---|---|
| stage A, overall | 460 | 53 (11.5 %) | 407 | 386 | **0.948** | 0.97 | yes |
| stage A, base | 156 | 14 | 142 | 135 | 0.951 | 0.95 | no |
| stage A, neighbour | 152 | 21 | 131 | 124 | 0.947 | 0.95 | yes |
| stage A, random | 152 | 18 | 134 | 127 | 0.948 | 0.95 | yes |
| cumulative (A + B), overall | 1,228 | 59 (4.8 %) | 1,169 | 1,122 | **0.960** | 0.97 | yes |
| cumulative, base | 417 | 17 | 400 | 380 | 0.950 | 0.95 | no |
| cumulative, neighbour | 406 | 23 | 383 | 370 | 0.966 | 0.95 | no |
| cumulative, random | 405 | 19 | 386 | 372 | 0.964 | 0.95 | no |

The overall agreement sat below the 0.97 reference line in both stages, and the neighbour and random arms
below 0.95 in stage A; by R29 these lines are reported and never pause labelling. The **arm gap** (the
only pause condition, checked once every arm had ≥ 100 Opus-answered pairs) was 0.004 in stage A and 0.016
cumulatively, against the 0.05 threshold: no pause, no remedy (`drift.alarm` false, `remedies` empty,
`drift_remedies` 0 in the stage-A commit). The Opus sample was 1,228 of 24,559 pairs (5.0 %); the call
count including re-issues, against the 7 % cap of §6.5, is not in a public artifact (*absent*).

**§6.6 metrics of the final label against Opus** (`judge.metrics_6_6_drift`; counts tp / fp / fn / tn,
final × Opus, *answers* positive):

| scope | tp / fp / fn / tn | precision | recall | specificity | F1 | κ | AC1 |
|---|---|---|---|---|---|---|---|
| stage A, overall (n 407) | 96 / 14 / 7 / 290 | 0.873 | 0.932 | 0.954 | 0.901 | 0.867 | 0.916 |
| stage A, base (142) | 45 / 6 / 1 / 90 | 0.882 | 0.978 | 0.938 | 0.928 | 0.891 | 0.910 |
| stage A, neighbour (131) | 20 / 4 / 3 / 104 | 0.833 | 0.870 | 0.963 | 0.851 | 0.819 | 0.924 |
| stage A, random (134) | 31 / 4 / 3 / 96 | 0.886 | 0.912 | 0.960 | 0.899 | 0.863 | 0.915 |
| cumulative, overall (1,169) | 189 / 33 / 14 / 933 | 0.851 | 0.931 | 0.966 | 0.889 | 0.865 | 0.943 |
| cumulative, base (400) | 76 / 13 / 7 / 304 | 0.854 | 0.916 | 0.959 | 0.884 | 0.852 | 0.925 |
| cumulative, neighbour (383) | 57 / 10 / 3 / 313 | 0.851 | 0.950 | 0.969 | 0.898 | 0.877 | 0.953 |
| cumulative, random (386) | 56 / 10 / 4 / 316 | 0.848 | 0.933 | 0.969 | 0.889 | 0.867 | 0.950 |

**Per-arm false-yes rates against Opus** (Opus-*not* pairs the final label calls *answers*,
`drift.scopes.<arm>.false_yes`): stage A base 6 / 96 (6.25 %), neighbour 4 / 108 (3.70 %), random 4 / 100
(4.00 %); cumulative base 13 / 317 (4.10 % [2.41, 6.89]), neighbour 10 / 323 (3.10 % [1.69, 5.61]), random
10 / 326 (3.07 % [1.67, 5.56]). Among final-*answers* pairs (`false_yes_among_answers`, the share the H2
bound reads) the cumulative figures are base 13 / 89, neighbour 10 / 67, random 10 / 66, overall 33 / 222
(14.9 % [10.8, 20.2]); H2's bound reads the frozen stage-A summary (P2), above.

**Against the owner** (§13.5): the table in [the spot-check section](#the-two-owner-spot-checks-and-the-judge-validity-flag)
(precision 0.783, recall 0.887, specificity 0.723, F1 0.832, κ 0.615, AC1 0.626 on 100 pairs).

**The validation figures** (prereg §6.4, §6.6, replayed by committed code; `judge-rule-replay.json`): the
composed rule reproduces 482 of 487 Opus-unanimous validation pairs (0.990), 12 escalated, 11 of them
right, 0 unlabelled; these are calibration facts, not study results, and the validation set is not fully
out of sample with respect to the study papers (O10; the sensitivity without the 8 papers is above).

## Manipulation checks

Source: `results-stage-B.public.json` `runs`, `leg_gates`, `noise_floor`, `legs_read`;
`stage-a-commit.public.json` `counts`.

| check | result |
|---|---|
| rows and failed calls | every one of the 29 legs read 1,091 rows (`legs_read`); stage A 15,274 hit rows with 0 failed (`counts.hits_rows`, `counts.hits_failed`); `missing` 0 in every contrast |
| census per leg | `census_source` `load_receipt+live` on all 13 runs: the live chunk count equalled the load receipt's and every chunk carried its glossary file's stamp (prereg §9.2–9.3); each run's `collection_build_sha256` is recorded, and the two noise-repeat runs of each arm name the same collection as their first leg |
| leg gates | **none fired**: `leg_gates` is null on all 29 legs, so no depth, exhaustion, census or inactive-reranker gate applies to any member; default never equalled norerank on every record (for example base × none 99.00 against 96.11 points) |
| frozen stage A | both analyses re-read the stage-A hits (15,274 rows, sha256 `1af69c67…`) and 9 run summaries and found them unchanged (`frozen_stage_a.agree` true); the stage-A commit sha256 `f53ca9d0…` verified before stage B ran (`study.stage_a_commit.verified`, `stage_b_runs_commit_verified` true) |
| prereg sha | verified by every run summary (`prereg_verified` true on all 13 runs) |
| noise floor (default × none run twice per corpus arm, seeded request order) | **0 discordant records of 900 in every arm**, judged and gold-only (`noise_floor.<arm>.discordant`, `.discordant_gold`); the per-cell table above shows each `#2` leg equal to its first leg |
| label coverage | stage A 19,530 demanded pair instances over 14 legs, stage B 78,005 over 29, 0 pairs without a final label |
| spot checks before analysis | the analysis gate required both ingest sha256s and the stage-A commit (`study.gate.allowed` true, `spot_checks_waived` false) |
| field coverage | merged renders a field on 24,749 of 32,765 base chunks and on 91,316 of 121,530 neighbour-arm chunks, 87,629 of 117,527 random-1, 86,071 of 117,317 random-2 (`runs.<arm>/merged.field_coverage`); the *none* collections render none (`available` false, 0 merged entries) |

## What this shows

In the record's own voice, from the figures above. Every item: judge validity below threshold (§13.5).

1. **The merged glossary helps alias questions, and the help is small on this corpus.** On the 600 G
   records under the served path, found@10 rose from 591 to 597 of 600 (+1.00 point, 6 gained and 0 lost,
   p 0.015625); H1 is **confirmed**, and H1b is **confirmed** (+1.33 points; the one C record lost makes
   the C change −0.33). By the frozen reading this is an alias-specific gain, for questions using the
   glossary's primary term. The gain is at the ceiling: base × default × none already found 98.5 % of G
   records by some chunk, and found@10-gold rose by 5.50 points (37 gained, 4 lost), so the glossary
   mostly moved the gold chunk into a top 10 that already held a judged answer. Round 3's powered null for
   a gain under mention attachment (prereg §2 item 1) was on records whose answer chunk bound a symbol to a
   value; these records are the case the glossary was built for (prereg §2 item 1), and on them the
   hypothesised direction was seen. The two designs differ in attachment rule and record type, so this is a
   reading across records, not a contrast.
2. **The serving decision stays open.** Layer 2 reads *not resolved*, beside H1 and H1b confirmed (D-3):
   the lift is confirmed but below the 2-point threshold (12 of the 600 G records) the owner fixed as worth
   acting on, and the C cost rule did not fire (ΔC −0.33, C_lo −1.00); judge
   validity below threshold (§13.5). A C cost smaller than about 3.6 points cannot be ruled out with 300 C records. Under norerank
   and BM25 the lift is larger (+2.50 and +4.17 on base; gold +8.00 and +13.17), descriptively: the
   glossary matters most where the cross-encoder is not there to recover the passage.
3. **The figures are consistent with citation neighbours displacing gold chunks and supplying answers in
   their place.** On found@10-gold the neighbour arm lost 2.17 points against the random average (35 lost,
   16 gained), and the RR@10 gold figure fell by 3.35 points; on judged found@10 the net was +0.06 (11
   gained, 9 lost): the neighbour arm's other-paper judged hits were 67 against 48 and 49 in the random
   draws. This is consistent with neighbours displacing gold chunks and supplying judged answers in their
   place (a reading from per-cell totals; no per-record transition count is in the artifact), and the
   other-paper hits are judged labels, so they carry judge validity below threshold (§13.5) like every
   judged figure. The gold-only gate pointed
   the hypothesised way, the judged test did not reject, and **H2 is not confirmed**: *no effect larger
   than 1.39 points* on judged found@10, within random-draw variation, within the judge false-yes bound,
   with power .96 and .90 to confirm at the planning scenarios (planned drops of 3.5 and 4.0 points, about
   3.0 and 3.5 at the realised dose). This is the pattern prereg §3.1 named:
   neighbours are more likely than random papers to answer a question, and an answer from another paper
   counts. The study makes no claim about the neighbours of one paper alone (the estimand, R11).
4. **Growing the collection costs gold-only reach clearly; judged reach moves by about 1 point.** Against
   base, the neighbour arm lost 7.22 gold [−9.00, −5.56] and 1.00 point judged [−2.11, 0.00]; the random
   draws lost 5.06 gold [−6.50, −3.72] and 1.06 judged [−1.78, −0.33]. On gold the loss is clear for either
   kind of growth and larger for neighbours (7.22 against 5.06: 841 → 776 and 841 → 795.5 of 900); on
   judged found@10 it is about 1 point (891 → 882 and 891 → 881.5 of 900, 9 and 9.5 records;
   `layer2.cells.<arm>|none|default`), and the neighbour CI reaches 0 (descriptive; Layer 2; judge
   validity below threshold (§13.5)).
5. **The reranker default holds on the neighbour arm**: default beat BM25 by 6.33 points [4.67, 8.11],
   14.44 on gold; non-inferior by the −3 margin. H3 (the reranker's change under growth against BM25's) is
   +1.22 [−0.56, +2.89], not resolved. The reranker-test's papers-OC reading (prereg §2 item 2) does not
   repeat here.
6. **The judge is the main threat to validity.** The owner agreed with the final label on 0.872
   re-weighted and 0.82 of *answers* labels, and on only 10 of 17 tiebroken pairs; the drift agreement with
   Opus sat below its reference lines (0.948 in stage A, 0.960 cumulative). On the owner's tags all 19
   disagreements sit on the partial-versus-full line, none on a passage with no answer (the analysis's
   reading of owner-supplied tags, exploratory, post hoc). found@10-gold, which needs no judge, is printed
   beside every Layer-1, Layer-2 and per-cell figure and
   agrees in direction with the judged figure on H1 and H1b, and points the hypothesised way on H2 where
   the judged figure does not.

## What is not reported

*Absent* means never measured or not in a public artifact. Nothing here is a null.

| item | status | why |
|---|---|---|
| H1 split by alias collision (prereg §3.3) | **absent** | `layer2.h1_by_collision`: no collision annotation was supplied to the statistics |
| Opus call count including re-issues against the 7 % cap (§6.5) | absent | not in a public artifact; the sampled pair count (1,228) is |
| H2 with the per-paper neighbour contrast separated from the other papers' neighbours | not a claim by design | the estimand (R11) adds every study paper's neighbours at once |
| record validity below the 0.85 rule | did not fire | 37 of 40, 0.925 |
| *judge incomplete* | did not fire | 0 unjudged pairs |
| any *fragile* label | did not attach | no R14 re-test flipped a verdict (D-1) |
| the chunk-boundary control's figures | in the addendum | [`ADDENDUM-2026-11-05-chunk-boundary-control.md`](ADDENDUM-2026-11-05-chunk-boundary-control.md), exploratory, changes nothing here |
| a second rater for the spot check | absent | single rater by owner decision (O12) |
| found@10-gold beside the declared, fixed (R6) and subgroup sensitivities and the H2-vs-one-draw displays | **absent** | not computed by the statistics at `15ee347`; the artifacts carry no gold companion for them, so those displays are judged found@10 only |

## Limitations

- **Judge validity below threshold (§13.5), on every result.** The reference is a single rater, the owner,
  who also designed the study (O12). The 100 pairs put the re-weighted agreement at 0.872 and the
  *answers*-stratum agreement at 0.82, below the frozen 0.90; the tiebroken stratum, the hardest pairs,
  agreed on 10 of 17. Both primary judges are Qwen models, so errors they share never reach the tiebreaker
  (§6.6). found@10-gold is the sensitivity the prereg names, and it is printed beside every Layer-1,
  Layer-2 and per-cell figure (not beside the sensitivity displays); it is not a
  substitute, because a record can be answered by a chunk that is not its gold.
- **The disagreement tags are the owner's, after LLM-assisted deliberation on some items, and their reading
  is exploratory (post hoc).** They describe the 19 pairs and are not a second annotation; the reading
  that all 19 sit on the partial-versus-full line is the analysis's.
- **A single-chunk binary rubric cannot credit partial evidence.** A hit needs one chunk that, read alone,
  states the core of the answer. Two top-10 chunks that together would answer, or a chunk that answers in
  part, score as no hit; a downstream synthesis step that combined them would see more than found@10
  does. On the owner's tags, the judge's disagreements with the owner lie on that line (exploratory, post
  hoc).
- **H1 rests on 6 records in 6 papers**, every one a gain, and its p equals its minimum attainable p; H1b
  rests on 7 papers. The realised G discordance (1.0 %) was far below the 5 % and 10 % planning
  scenarios, and the base rate on G was 98.5 % before any glossary. The gain is real under the frozen test
  and small.
- **The C ceiling.** Baseline C found@10 was 100.0 %, so the C gate was ceiling-limited and H1b's control
  could only fall. A general upward shift that the glossary might cause on term-bearing passages was not
  observable (prereg §2 item 4).
- **Terciles and the R6 inputs are post hoc (D-1).** The cut points were fixed after the stage-A outputs
  had been read by the analysing session; the figures are the first re-run with them, by the binding rule,
  and they decide nothing.
- **H1b's gold figure is post hoc (D-2)**, display only; its p is a display figure of the same
  randomization, not a test.
- **Drift agreement below the reference lines** (0.948 stage A; 0.960 cumulative, against 0.97; two arms
  below 0.95 in stage A). By R29 these are reported, not alarms; the arm gap, the only pause condition, did
  not approach its threshold. 53 of 460 stage-A drift pairs (11.5 %) and 59 of 1,228 cumulatively had no
  Opus reference after one re-issue, clustered as §6.3 anticipated; they are excluded from the agreement.
- **The random draws differ by 0.11 points** on H2's cells, and H2's net lies within that. Two draws were
  the design's hedge against one unlucky draw; they did not disagree, and the figure is within their gap.
- **The expanded arms add 2,423 papers each**, a sample of the neighbourhood under caps (5 most-cited
  references, 5 most recent citers, open access only; §5.1), and the random draws are 4.5–4.8 % short of
  the neighbour arm's chunks; the volume covariate re-test did not change H2's reading.
- **One corpus, one population.** OA-1000 only; records exist only for papers with usable glossary entries
  (300 satisfied papers; 508 of 600 G records are abbreviations); records are LLM-drafted and LLM-verified,
  plus the owner's check of 40 (37 valid).
- **No study-wide error control across the two families** (R1): up to about 0.0975 chance of at least one
  false confirmation with every null true.
- **Not from a tools image.** Every provenance block records `in_image false`; commit, clean tree and
  segmentation fingerprint are recorded instead. The statistics ran in an environment whose sentence
  segmenter reports the `regex` backend (no nltk), the H1b-gold and tag scripts under `punkt`; none of these
  scripts segments text, so the fingerprint is context here, not identity.
- **The 47 Codex-verdict records.** Without them H1 is GATE-NOT-EVALUABLE (too few non-zero papers for
  the level), a display; the primary label stands as frozen.

## Reproducing

- Retrieval and judging, from a clean checkout at `d0d3555` (stage A) and `812fea0` (stage B), with the
  private run config naming the 9 bench collections, the frozen record file and the judge endpoints:
  `PYTHONPATH=python python/scripts/eval/alias_study_run.py` per attachment (the three retriever legs of an
  attachment in one run), `alias_study_judge.py` for the on-demand labelling by stage, the stage-A commit
  step before stage B.
- The owner spot checks: `alias_study_spot.py draw-records` / `draw-pairs` / `ingest`
  (`spot-records-counts.json`, the private judge-label JSONL whose sha256 is in `spot-judge-record.json`).
- The D-1 inputs, at `a83d520`: `alias_study_inputs.py` (`tercile-cuts.json` is its committed public
  output).
- The statistics, at `15ee347`: `alias_study_stats.py` with the stage-A config (`--public` →
  `results-stage-A2.public.json`) and the stage-B config (`results-stage-B.public.json`).
- H1b's gold display, at `a179383`: `alias_study_h1b_gold.py --config <stage-B config> --reference
  artifacts/results-stage-B.public.json --public artifacts/h1b-gold.public.json`.
- The disagreement tags, at `d93eddaa`: `python3 -I artifacts/make_disagreement_tags.py --tags <private tag
  export> --items <private sheet items> --out artifacts/disagreement-tags.json` (both inputs sha256-pinned
  in the script).
- The pre-freeze artifacts: `alias_study_recount.py` and `alias_study_replay.py` at `3c138ab`,
  `alias_study_power.py` at `617d28d`, `alias_study_pilot.py` (`pilot-report.json`).
