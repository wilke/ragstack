<!-- experiment-page: results -->
# Results: chunk size on a deployed documentation collection (2026-10-23)

**EXPLORATORY — not citable.** Every number below is copied from the public export in
[`artifacts/`](artifacts/); the source key is given beside each table as `file: key`. The export's
`export_manifest.json` records the private source files' sha256 prefixes, their run provenance (all
`citable: false`) and the as-run harness sha256. All token counts are **SFR-Embedding-Mistral** tokens of
chunk text. Arm names: **L** live chunks (semantic, uncapped); **SEM512/SEM256** semantic with a token cap;
**T*s*o*v*** fixed-token windows of *s* tokens with *v* overlap; **X*s*o*v*** the same inside markdown
sections. Budgets: **b2048/b5120** walk the reranked list and deliver chunks until the next would exceed
the budget; **r10** is the reranked top 10.

Decision rule (pre-registered): net ≥ +5 on the primary leg, no loss at b5120 (retrieval) or the same
sign under the other reader (k10), and net gains in ≥ 2 strata. A contrast with fewer than 10 discordant
pairs is **unresolved**, not null; with n ≈ 100, a net of about +5 is the smallest resolvable effect.

## Q1. The seed questions (part 1)

**Method.** 3 positives with page-level gold (P), 15 negatives the documentation cannot answer (N), and 13
how-to rewordings of the negatives (W), run through every arm.

**Result.**
- P: all 3 positives find a gold page at b2048, b5120 and r10 in every arm shown below.
- Separation of answerable (P ∪ W) from unanswerable (N) by the top-1 reranker score:

| arm | AUROC | AUROC without M5 | negatives inside the positive range |
|---|---|---|---|
| L | 0.8708 | 0.8616 | 5 |
| T128o0 | 0.875 | 0.8661 | 5 |
| T256o0 | 0.8979 | 0.8906 | 5 |
| T512o0 | 0.8833 | 0.875 | 7 |
| T1024o0 | 0.9083 | 0.9018 | 5 |
| X128o0 | 0.9042 | 0.8973 | 4 |
| X256o0 | 0.9625 | 0.9598 | 1 |
| X512o0 | 0.9625 | 0.9598 | 4 |
| X1024o0 | 0.9167 | 0.9107 | 3 |

`part12.json: part1.per_config.<arm>|gon|cap0|all.{auroc_PW_vs_N, auroc_PW_vs_N_without_M5, negatives_in_positive_range, P_found}`

- W: the pre-registered judge (Llama-4-Scout) labelled 0 of 3,731 pairs "yes" (2,905 partial, 825 no, 1
  unparseable), so the part-1 W tables are **void** (degenerate judge). `part12.json: part1.judge_verdicts`

**Reading.** Descriptive only: 31 questions, no contrast was pre-registered as decisive. Section-split
small chunks separate answerable from unanswerable questions best by top-1 score (X256o0, X512o0), but
with 15 negatives this is not a resolved difference.

## Q2. Size, overlap and sections on single-passage questions (part 2)

**Method.** 150 pages sampled, stratified by page family; one 1,200-character passage each; Qwen3.8-27B
wrote one question and an exact answer quote. 116 questions validated (30 quote not in page, 4 too long);
96 low-echo. `part12.json: generation, n_questions, n_low_echo, questions_by_stratum`

**Result (rules glossary, all 116 questions).** `part12.json: summary.<arm>|gon|cap0|all.all`

| arm | b2048 | b5120 | r10 |
|---|---|---|---|
| L | 106 | 113 | 112 |
| T128o0 | 108 | 110 | 105 |
| T128o16 | 111 | 113 | 110 |
| T256o0 | 112 | 114 | 112 |
| T512o0 | 108 | 110 | 110 |
| T512o64 | 110 | 113 | 113 |
| T1024o0 | 98 | 108 | 112 |
| X128o0 | 105 | 106 | 101 |
| X256o0 | 106 | 113 | 106 |
| X512o0 | 102 | 109 | 106 |
| X1024o0 | 98 | 104 | 107 |

Rule verdicts (`part12.json: verdicts`):
- **Overlap:** one pass, T128o16 − T128o0: net +5 on r10 (6 gained, 1 lost, p = 0.125), +3 at b5120, gains
  in 2 strata. `contrasts["1 overlap"]["rules|all|T128o16 - T128o0"]`. The other 7 overlap contrasts do
  not pass (nets 0 to +4).
- **Against live:** no arm passes; nets at b5120 range from −9 (X1024o0) to +1 (T256o0, X256o32).
- **Size** (descriptive, no rule): T1024o0 − T512o0 loses at b2048 (1 gained, 11 lost, p = 0.0063); the
  other T sizes are within ±4. `contrasts["2 size"]`
- **Sections:** X − T is ≤ 0 at every size at both budgets for the o0 arms; with overlap, X256o32 − T256o32 is +1 at
  b5120 (e.g. X512o0 − T512o0, b2048: 4 gained, 10
  lost, p = 0.18). `contrasts["3 sections X-T"]`

**Reading.** At its ceiling: live already delivers 113/116 at b5120, so this set cannot show a size
effect. Section-bounded splitting produces many small chunks here (X512o0 median 108 tokens,
`part12.json: storage.X512o0.tokens_median`) and does not help.

## Q3. Does semantic chunking ignore `chunk_size`? (Addendum S)

**Method.** Re-chunk the reassembled pages with the live semantic parameters and no `max_tokens`
(SEMlive), then with a cap of 512 (SEM512) or 256 (SEM256).

**Result.** `addendum_S.json: reproduction, size_distributions, bug_criterion`
- SEMlive reproduces the live chunks exactly: identical boundaries and text on 336 of 336 pages, 1,265
  chunks.
- Sizes:

| arm | chunks | median | p90 | max | over 512 |
|---|---|---|---|---|---|
| L (live) | 1,265 | 311 | 665 | 3,398 | 235 |
| SEMlive | 1,265 | 311 | 665 | 3,398 | 235 |
| SEM512 | 1,564 | 297 | 511 | 512 | 0 |
| SEM256 | 2,491 | 247 | 256 | 257 | 0 |

- **Bug criterion met:** SEMlive has 235 chunks over 512, SEM512 has 0, so the missing cap is the cause.
  `chunk_size` is recorded but has no effect on the semantic path. Tracked as issue #647.
- Retrieval, all 116 questions (`addendum_S.json: contrasts["all|SEM512 - SEMlive"]`): SEM512 − SEMlive at
  b2048 6 gained / 0 lost (p = 0.0312); at b5120 +1 (rule leg); r10 +2. Rule: **not passed** (b5120 net
  +1, below the ceiling). SEM256 − SEMlive at b5120 −2.

**Reading.** The oversize chunks are a defect, not a tuning choice, and capping costs nothing on this set
while helping the tight budget. Part 3 (Q4) tests the cap where the ceiling does not mask it.

## Q4. Harder questions (part 3)

**Method.** 76 pages of at least 2,048 tokens. Type **M**: two passages ≥ 1,500 characters apart, one
question needing both, found only if both quotes are delivered (primary b5120). Type **D**: a specific
detail, low-echo by construction (primary b2048). 105 M and 65 D questions validated.
`part3.json: generation, n, by_stratum`

**Result.** `part3.json: summary.<arm>.{M,D}`

| arm | chunks | M b2048 | M b5120 | M r10 | D b2048 | D b5120 |
|---|---|---|---|---|---|---|
| L | 1,265 | 41 | 73 | 79 | 56 | 60 |
| SEM512 | 1,564 | 56 | 81 | 76 | 58 | 61 |
| SEM256 | 2,491 | 64 | 76 | 66 | 60 | 60 |
| T128o0 | 3,955 | 62 | 72 | 55 | 59 | 59 |
| T256o0 | 2,044 | 70 | 79 | 72 | 60 | 61 |
| T512o0 | 1,113 | 57 | 82 | 82 | 58 | 61 |
| T1024o0 | 657 | 53 | 83 | 94 | 57 | 61 |
| X256o0 | 3,508 | 60 | 75 | 66 | 62 | 62 |
| X512o0 | 2,818 | 58 | 75 | 68 | 60 | 61 |

`chunks: part3.json: arms_chunks`. Rule verdicts (`part3.json: verdicts`), M at b5120:

| contrast | net | discordant | strata with gain | verdict |
|---|---|---|---|---|
| SEM512 − L (cap) | +8 | 12 | 2 | **passes** |
| T512o0 − L | +9 | 23 | 2 | **passes** |
| T1024o0 − L | +10 | 22 | 4 | **passes** |
| T256o0 − L | +6 | 24 | 2 | **passes** |
| T128o0 − L | −1 | 23 | 1 | fails |
| T128o0 − T512o0 (size) | −10 | 22 | 0 | fails (loss) |
| T1024o0 − T512o0 (size) | +1 | 17 | 3 | fails |
| SEM256 − L | +3 | 17 | 1 | fails |

Also passing on M: T256o32, T512o64 and T1024o128 against L (+8, +9, +8). No overlap contrast passes on
M or D. On D, one contrast passes **and** is resolved: X256o0 − L, net +6 at b2048 (10 discordant, 3
strata). Six more D contrasts against L pass the net and strata legs but have under 10 discordant pairs,
so they are **unresolved**. In total 39 of 76 part-3 contrasts are unresolved.

**Reading.** Once answers need two separated passages, the uncapped live chunks lose: capping semantic at
512 (+8) or any fixed window of 256–1,024 tokens beats live by +6 to +10 at the same 5,120-token budget.
128-token windows do not (−10 against 512). D stays near its ceiling (56–62 of 65), so detail questions
barely separate arms.

## Q5. How-to relevance judged by LLMs (part 3 B, group W)

**Method.** The 3,731 (W question, chunk) pairs re-judged with a stricter yes / partial / no prompt by
Qwen3.8-27B and Claude Opus (Opus 5 first, then Opus 4.8 as primary, see Q7). Degeneracy check: a judge
with under 2% or over 90% "yes" is void.

**Result.** `part3.json: judges.{opus,qwen}.distribution`, `part3.opus5.json: judges.opus.distribution`

| judge | yes | partial | no | other | yes share | degenerate |
|---|---|---|---|---|---|---|
| Opus 4.8 (primary) | 170 | 1,827 | 1,733 | 1 unparseable | 0.0456 | no |
| Opus 5 | 288 | 1,982 | 1,366 | 86 no_label, 9 unparseable | 0.0772 | no |
| Qwen3.8-27B | 298 | 2,566 | 867 | — | 0.0799 | no |

W yes-hits per arm (questions out of 13 with a "yes" chunk), r10 / b2048 / b5120.
`part3.json: judges.{opus,qwen}.W_yes_hits.<arm>|gon|cap0|all`

| arm | Opus 4.8 | Qwen |
|---|---|---|
| L | 7 / 5 / 6 | 10 / 5 / 8 |
| T128o0 | 4 / 4 / 4 | 5 / 6 / 6 |
| T256o0 | 5 / 5 / 5 | 7 / 7 / 7 |
| T512o0 | 5 / 5 / 5 | 6 / 6 / 6 |
| T1024o0 | 9 / 7 / 8 | 8 / 7 / 8 |
| X256o0 | 4 / 4 / 4 | 8 / 8 / 9 |
| X512o0 | 6 / 6 / 6 | 10 / 7 / 10 |

Agreement, Opus 4.8 vs Qwen: κ = 0.4375 (3 classes), 0.5158 (yes vs not), n = 3,730.
`part3.json: cross_judge.kappa.opus48_vs_qwen`

**Reading.** Descriptive only (the judges were chosen after the first judge's output was seen). The two
judges disagree about which arms help: Opus 4.8 favours large chunks (T1024o0), Qwen favours section
chunks (X512o0 at b5120). With κ ≈ 0.44–0.52 and 13 questions, how-to relevance is not something these
judges measure reliably at this scale.

## Q6. Top 10 chunks passed to a model (k10)

**Method.** For 286 questions (M 105, D 65, part-2 116) and 8 arms, the reranked top 10 chunks were given
to two readers (Llama-4-Scout, Qwen3.8-27B), and each answer was graded correct / partial / incorrect /
abstained against the gold quotes by Opus 4.8 (primary) and Qwen (secondary). **Not budget-matched:** the
context grows with chunk size. `k10.json: n, arms, readers`

**Result: questions graded correct (Opus 4.8).** `k10.json: summary["opus|<reader>|<set>|<arm>"].correct`

| arm | M Scout | M Qwen | D Scout | D Qwen | P2 Scout | P2 Qwen | M context mean (max) |
|---|---|---|---|---|---|---|---|
| L | 64 | 75 | 57 | 57 | 102 | 111 | 6,693 (19,463) |
| SEM512 | 72 | 72 | 56 | 56 | 98 | 106 | 3,986 (5,115) |
| SEM256 | 67 | 66 | 57 | 56 | 102 | 103 | 2,325 (2,555) |
| T128o0 | 50 | 59 | 51 | 51 | 95 | 98 | 1,273 (1,280) |
| T256o0 | 67 | 69 | 57 | 57 | 102 | 107 | 2,539 (2,560) |
| T512o0 | 68 | 77 | 55 | 56 | 100 | 108 | 4,996 (5,120) |
| T1024o0 | 63 | 78 | 51 | 59 | 105 | 113 | 9,767 (10,240) |
| X512o0 | 65 | 69 | 58 | 58 | 97 | 106 | 3,645 (5,120) |

Out of M 105, D 65, P2 116. Context: `k10.json: context_tokens_SFR.M.<arm>.{mean,max}` (tokens, rounded).

Rule verdicts under Opus 4.8 (`k10.json: verdicts["opus|…"]`, 66 contrasts, 22 unresolved):
- **128-token windows lose.** T128o0 − T512o0 on M: −18 with Scout (30 discordant, p = 0.0014) and −18 with
  Qwen (28 discordant, p = 0.0009). T128o0 − L on M: −14 (Scout), −16 (Qwen). On part 2 with Qwen: −13
  (p = 0.001).
- **No arm beats live.** No "arm − L" contrast passes. The largest, SEM512 − L on M, is +8 with Scout but −3
  with Qwen, so it fails the same-sign leg.
- **The only pass:** T1024o0 − T512o0 on part 2, +5 with both readers (11 and 7 discordant; the Qwen leg is
  unresolved), at about 2× the context (part-2 means 8,681 vs 4,590 tokens,
  `k10.json: context_tokens_SFR.P2`).
- Under the Qwen grader the only pass is T512o0 − L on M with the Scout reader (+8; +2 with Qwen), so "no
  arm beats live" depends on the grader. `k10.json: verdicts["qwen|1 vs live|M|scout|T512o0 - L"]`

**Reading.** In the way most systems are used — a fixed number of chunks — the answer is mostly about how
much text the model receives. 128-token chunks starve the model (about 1,270 tokens of context) and lose
clearly. Above 256 tokens the arms are statistically indistinguishable from live, and the capped semantic
arm (SEM512) gives about the same answers with about 40% less context than live on M (3,986 vs 6,693
tokens mean). Live's worst case is a 19,463-token context, from its oversize chunks.

## Q7. Grader reliability and silent drops

**Method.** κ between graders over the same items; GPT-5.6 Sol on the items Opus 5 returned nothing for,
plus a stratified 400-item sample; a "filter test" counting which models answer the skipped items.

**Agreement (κ).** `part3.json: cross_judge.kappa`, `k10.json: cross_judge.kappa`

| pair | W (3 classes / yes vs not) | k10 (4 classes / correct vs not) | n (W / k10) |
|---|---|---|---|
| Opus 4.8 vs Opus 5 | 0.7194 / 0.6181 | 0.7425 / 0.8127 | 3,636 / 3,109 |
| Sol vs Opus 4.8 (sample) | 0.5667 / 0.5107 | 0.6414 / 0.7064 | 400 / 400 |
| Opus 4.8 vs Qwen | 0.4375 / 0.5158 | 0.6349 / 0.7029 | 3,730 / 3,115 |
| Opus 5 vs Qwen | 0.4954 / 0.4957 | 0.6071 / 0.6978 | 3,637 / 3,115 |

**Silent drops (filter test).** `*.json: cross_judge.per_model.<model>.on_opus5_skipped`
- W: Opus 5 returned an empty answer with 0 tokens (no error, no refusal) on 94 items, all on one question
  (H5w; `part3.opus5.json: judges.opus_no_label_by_question`). Opus 4.8 answered 94/94 and Sol 94/94: the
  drop is specific to Opus 5.
- k10: Opus 5 skipped 40 items on 4 records (d053, g136, m006, m136). Opus 4.8 answered only 6 of them (34
  still empty), Sol answered all 40. Opus 4.8's own 40 empties fall on d053, d062, m006, m136, all in the
  cli, topic and tutorial strata. `k10.json: graders.{opus_no_label_items, opus_no_label_by_stratum}`
- **Unparseable grades:** Opus 4.8 opened 30 k10 grades with prose instead of the label; per the
  pre-registration they count as incorrect (`k10.json: graders.opus.distribution`: correct 2,451, partial
  299, incorrect 201, abstained 134, no_label 40, unparseable 30). Opus 5 had 78 incorrect and 2
  unparseable (`k10.opus5.json: graders.opus.distribution`).

**Reading.** Grading answers against a quoted reference is reasonably stable across graders (κ 0.61–0.81);
judging how-to relevance is not (0.44–0.72). Two Claude models silently return nothing on a small,
item-specific set of prompts; a pipeline that drops empty answers would bias its scores without noticing.
Any gateway grader needs an explicit empty-answer rule and a second model on the skipped items.

## Findings

1. **The live semantic collection has a cap bug.** `chunk_size 512` is recorded but not applied: 235 of
   1,265 chunks exceed 512 tokens, up to 3,398 (Q3, reproduced exactly).
2. **The bug costs retrieval on hard questions.** Capping at 512 recovers +8 two-passage questions at the
   same 5,120-token budget; fixed windows of 256–1,024 do as well or better (+6 to +10) (Q4).
3. **128-token chunks are too small** for this documentation: they lose at a fixed budget on two-passage
   questions and lose clearly when a model reads the top 10 (−18 against 512) (Q4, Q6).
4. **With top-10 delivery, nothing beats live** under the Opus graders (one arm does under the Qwen
   grader), and capped semantic answers about as well with about 40% less context (Q6).
5. **Section-bounded splitting does not help** on this collection (Q2, Q4); it is best only at separating
   answerable from unanswerable seed questions by top-1 score, descriptively (Q1).
6. **LLM judges of how-to relevance disagree** (κ ≈ 0.44–0.52 against Qwen); grading answers against
   references is steadier. Claude models drop some items silently (Q5, Q7).

## Threats to validity

- **Not citable.** The as-run harness was uncommitted (results' run provenance: `untracked` 2–3,
  `export_manifest.json: sources`); its sha256 is recorded
  (`export_manifest.json: as_run_harness_sha256.trial` = `3b7d16e2…`). The committed
  `deployed_docs_trial.py` is a behaviour-preserving, config-driven copy of it, checked constant by
  constant, not the file that ran. The as-run sha is of the final file; parts 1–2 ran on earlier states
  of it.
- **Grader changed after results were seen.** Opus 4.8 replaced Opus 5 as the primary judge and grader
  after the Opus 5 results were in. Every Opus-4.8 verdict is exploratory. Across graders
  (`k10.opus5.json: verdicts`, `k10.json: verdicts["qwen|…"]`): the T128o0 − T512o0 loss on M holds under
  all three (Opus 5: −22 Scout / −18 Qwen; Qwen grader: −23 / −21); "no arm beats live" holds under Opus 5
  but not under the Qwen grader (T512o0 − L on M passes with the Scout reader); and the single passing
  contrast differs (under Opus 5 it is T256o0 − T512o0 on D, net +5 / +3). SEM512 − L on M is −1 / −6
  under Opus 5.
- **Ceiling effects.** Part 2 (live 113/116 at b5120) and part 3 D (56–62 of 65) are near ceiling and
  cannot show size effects; their nulls are not evidence of no effect.
- **k10 is not budget-matched.** Larger chunks give the model more text by construction (10 × 1,024 ≈ 8 ×
  10 × 128). Wins for large arms are stated with their context size.
- **Unparseable grades.** 30 Opus 4.8 grades were prose and count as incorrect; this inflates Opus 4.8's
  incorrect count (201 vs 78 under Opus 5). No verdict changes, but the parser should look for the label
  word anywhere in a short answer.
- **Silent empties.** 40 k10 items have no Opus 4.8 grade. Results are reported with them counted as not
  correct (pre-registered) and with them excluded; the same two contrasts pass under both readings, with 22
  unresolved in each (`k10.json: verdicts["opus_excl|…"]`).
- **Self-preference.** Qwen grades and judges answers it also produced as a reader; reported, not
  corrected.
- **Sol is not temperature-0** (the gateway rejects the parameter for that model).
- **Generated questions.** Questions come from one model (Qwen3.8-27B) and sampled passages; they are not
  real user questions. Seed questions are few (31).
- **One collection.** 336 pages of one product's documentation; generalisation is untested.

## Deviations

Summarised; the full redacted log is [DEVIATIONS.md](DEVIATIONS.md).
- Part-1 W labels voided (degenerate judge); W re-judged in part 3 B with new judges and prompt.
- Opus max_tokens raised 8 → 64 → 256 → 4,096 because Opus 5 spends output tokens on hidden reasoning and
  returns empty content when truncated; an output-token stop (3M) was added.
- An item-specific empty-answer rule (0-token empty twice → no_label, skipped) replaced the failure stops
  for those items.
- Primary judge and grader switched from Opus 5 to Opus 4.8 after results were seen; Sol added as a
  cross-vendor check, without temperature.
- The W pool was extended to the budget walks; part-3 M lists were recomputed from caches (gate: identical
  where stored, 0 new reranker calls).

## Next steps

- **Fix the semantic cap (#647):** make semantic chunking honour `chunk_size` as a token cap, or refuse /
  warn when `chunk_size` is set without a token budget, and record the cap actually applied in the
  manifest. Then re-chunk the live collection.
- **Dev-collection chunk count:** a fixed-token 512/0 collection ingested on the dev tenant has 1,023
  chunks, against 1,113 for the offline T512o0 arm here. Explain the difference before using the dev
  collection as an arm.
- **Grader parsing:** accept the label word anywhere in a short grade, then rescore from the caches (no new
  calls).
- **Citable rerun:** run the committed harness from a versioned tools image, so the artifacts carry a clean
  provenance block.
