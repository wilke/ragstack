# Pre-registration: does the adopted chunking cost reach? (fixed 512-token windows vs boundary-respecting chunks) — DRAFT v1, 2026-11-09

STATUS: DRAFT. Not frozen. The owner decided confirmatory (2026-11-09). The freeze follows the alias
study's procedure (signed tag, private sha file) after two independent reviews. Open questions for the owner
are in §11; nothing below that depends on them is final.

## 1. Question and hypotheses

**Question.** The project is adopting the chunking used in the confirmatory chunk-boundary test and rechunk
stage 1 (owner, 2026-11-09):
- `sentence/512/0` with `size_unit tokens`;
- `size_tolerance 0.25`, so a 512-token target with a 640-token ceiling;
- `structure_cuts sections/1`;
- `min_piece 128`.

Call it **S**. The alias study retrieved with fixed 512-token windows, `fixed_token/512/0`; call that **W**.
For the alias study's 1,091 questions: does retrieval under S reach the answer as often as under W?

**Why the earlier work cannot answer it.**
- The confirmatory test and stage 1 judged passages, or retrieved only for pairs selected from W's retrieved
  windows.
- The OA chunking evaluation (`../oa-chunking-eval-2026-10/`) compared related arms on another question set.
  Its passages sat inside a single section, so no gold quote crossed a section boundary.

**Two outcome families,** each tested on its own (§4):
- **Q (judge-free):** quote@10.
- **J (judged):** found@10 with the frozen alias judge.

**Hypotheses.** These depend on the owner's choice in §11 Q2. The draft takes non-inferiority as primary:
- **HQ:** quote@10(S) − quote@10(W) > −δ, with **δ = 3 points** (owner, 2026-11-09, from `power.json`). If
  non-inferiority holds, superiority is tested next (hierarchical, no extra α).
- **HJ:** the same, for judged found@10.

## 2. Data (fixed before any S-side outcome exists)

- **Questions.** The alias study's 1,091 accepted records (900 balanced + 191; private). The questions,
  answers and quotes are unchanged.
  - Each record carries a `quote`, about 160 characters (range 18–300), taken from its gold chunk.
  - After whitespace normalisation, the same normalisation quote@10 uses, 1,089 of the 1,091 quotes are
    verbatim in their gold chunk (`feasibility.json` `eligibility`; 1,065 without normalisation). The 2 others
    are excluded from Q and kept in J (§11 Q4).
- **W side (existing).** The alias study's committed stage-A retrieval runs, legs `<corpus>/none`, which hold
  each record's ranked chunk ids under default, norerank and BM25. The text comes from the W chunk exports.
  Nothing new is run on the W side.
- **S side (to be run).** The four bench collections of rechunk stage 1, `bench-rechunk-{base,neighbour,random1,random2}`
  (glossary none, 452,729 chunks, `../rechunk-found10-2026-11/artifacts/build-summary.json`). Run with the
  same harness settings as W: top 50 requested, top 10 read, the same three retrievers and server settings.
- **Prior S-side exposure, declared.** Rechunk stage 1 retrieved the S collections for the 218 questions of
  the confirmatory test. It computed no quote@10 and no found@10 for any of them. Those questions are re-run
  here with the rest. §11 Q5 asks whether to exclude them.

## 3. Outcomes

- **quote@10 (Q).** A question is found when the union of its top-10 chunks covers at least 90 % of the
  quote's characters, after whitespace normalisation. This is the OA evaluation's definition, implemented once
  as `quote_cover` / `quote_hit` in `python/scripts/eval/reach_quote.py` (tested). One function scores both
  sides.
  - It is chunking-symmetric: a quote split across two retrieved chunks counts.
  - It needs no judge.
  - It credits only the gold location.
- **judged found@10 (J).** A question is found when quote@10 holds, **or** any top-10 chunk carries an
  *answers* label from the frozen alias judge.
  - This one rule applies on both sides. It replaces the alias study's gold-window shortcut, which has no
    S counterpart.
  - Lists with no quote hit are judged on demand. Already-labelled (record, chunk) pairs are reused, and so
    are labels for S chunks whose text is byte-identical to a judged W chunk of the same question (the
    content-hash rule).
  - Remaining labels come from new judging with the frozen judge: alias-judge prompts, C/E, Argon tiebreak,
    Opus drift reference. New (record, chunk) pairs are built with `build-control --any-paper`, the stage-1b
    mode.
- **Judge drift check** (owner, 2026-11-09). W's labels are from 2026-11-04/06; S's will be new. At run time, a
  seeded 5 % sample of the W (record, chunk) pairs that J uses (seed `261109|reach|drift|W`; ≈ 615 of
  12,310) is re-judged by the frozen judge. The report gives the agreement between the original and the new
  labels, overall and by original label. This is descriptive and does not enter the test. If agreement on
  W's *answers* labels falls below 0.90, the J result carries a *judge drift* caveat.
- **Context tokens per query** (top 10, SFR tokenizer): descriptive.

## 4. Tests and families

- **Primary cell** (§11 Q1). The default retriever, pooled over the four corpus collections, with records
  clustered by paper.
- **Clusters.** 408 papers holding 1,089 eligible records: 25, 85 and 298 papers with 1, 2 and 3 records.
  Each record contributes one paired difference per corpus.
- **Statistic.** The paired difference per record and corpus, S − W, in {−1, 0, +1}. For non-inferiority, a
  sign-flip over papers on the null-shifted cluster sums X_k = T_k + m_k·δ (m_k = the cluster's paired
  differences), one-sided, α = 0.05. The p is computed exactly by lattice convolution
  (`power.py` `sign_flip_p_exact`). Superiority, after non-inferiority, is the exact sign-flip on T_k. Each
  family (Q, J) is tested at α = 0.05 on its own.
- **Size, declared.** The randomisation p is exact, but the composite null does not make d + δ
  sign-symmetric, so the test's size is only asymptotically α.
  - Simulated size at the sizing case: 0.0545 (`power.json` `recommendation.size_at_sizing_case`).
  - At δ = 3, across the ρ grid: at most 0.0586 at 10 % discordance and 0.0766 at 5 %
    (`power.json` `size_summary_max_over_rho`). At 2 % discordance, δ = 3 is not evaluable in the grid.
  - For comparison, δ = 2 reaches 0.138 at 2 % discordance.

  A rejection at low observed discordance is reported with this caveat.
- **Secondary, not tested.** Each corpus and each retriever separately; G vs C records; context tokens;
  discordant records broken down by coverage of the gold window under S.

## 5. Power (`power.py`, `power.json`, seeded `261109|reach|rechunk|power`, 10,000 studies per cell)

**W baseline quote@10** (`feasibility.json` `w_side`, the committed scorer, eligible records):

| corpus | default | norerank | BM25 |
|---|---|---|---|
| base | 94.2 % | 86.8 % | 82.3 % |
| neighbour | 87.7 % | 74.0 % | 73.7 % |
| random-1 | 90.0 % | 78.8 % | 77.3 % |
| random-2 | 89.5 % | 78.9 % | 77.2 % |

**Correlations**, from a W-only proxy (`feasibility.json` `w_miss_correlation`):
- misses of the same record correlate 0.74 across corpora;
- different records of the same paper correlate 0.005.

The sizing case therefore takes ρ_record 0.75 and ρ_paper 0. The design effect is about 3.2: the four corpora
add far less than four times the information.

**Non-inferiority power** at true Δ = 0 under the sizing case (`power.json` `recommendation`):

| discordance | δ = 1 | δ = 2 | δ = 3 |
|---|---|---|---|
| 2 % | 0.82 | 1.00 | 1.00 |
| 5 % | 0.50 | 0.95 | 1.00 |
| 10 % | 0.32 | 0.76 | **0.97** |

δ = 3 is the smallest margin with power ≥ 0.90 at 10 % discordance. 10 % is the top of the grid, because S's
discordance cannot be observed before S exists. At true Δ = −1 and 10 % discordance, power is 0.75.

**Hierarchical superiority** (sizing correlation): at true +1, power is 0.30, 0.49 and 0.86 for 10, 5 and 2 %
discordance; at true +2, it is 0.76, 0.96 and 1.00. With the base corpus at 94 %, superiority is weak unless
discordance is low.

**Sensitivity.** Excluding the 26 raw non-verbatim quotes instead (1,065 records) gives practically the same
power (`raw_exclusion_cells`).

**J judging load** (`feasibility.json`):
- **W:** needs no new judging. All 12,310 (record, chunk) pairs in its no-hit top-10 lists already carry a
  final label.
- **S:** estimated at the same miss rate, all three retrievers: about 12,300 new pairs, about 24.6k Qwen
  calls. That means about 860 / 1,480 / 2,090 Argon calls at 7 / 12 / 17 % escalation, and about 615 Opus
  calls.
- **Drift check:** about 615 more pairs.

## 6. Procedure

1. Freeze.
2. Run the S-side retrieval for 1,091 records × 3 retrievers × 4 corpora, with the `rechunk` mode extended
   to all records (§10).
3. Compute quote@10 on both sides, using one committed function applied to both.
4. Judge on demand for J: one gateway notice, OpenChia's ack, a hard stop on any provider notice.
5. Run the report once, with the frozen decision wording.

## 7. Gates and decision rules

### 7.1 Gates (a third answer, as in the earlier preregs)

- Any retrieval leg with failed calls or a fired depth or reranker gate.
- An S collection whose census differs from `build-summary.json`.
- Unjudged pairs above 5 % for J.
- Label-table integrity: merged tables may only add labels (the stage-1b guard).

How `python/scripts/eval/reach_report.py` reads each gate:
- **Retrieval** (both families, W and S): a run summary whose status is not `complete`; any condition with
  a non-null `gate`; any failed call, missing row or duplicate row in any (corpus, retriever) leg. Also an
  S run that is not the reach run (`study.mode` `rechunk` with `rechunk_scope` `all_records`).
- **Census** (both families): per S collection, the summary's live census before and after (total and
  stamps), the collection name, the load receipt's chunk count and sha256, all equal to
  `build-summary.json`.
- **Inputs** (both families): a top-10 chunk with no text in its side's exports; a W gold chunk missing; a
  record count other than 1,091 or a Q-eligible count other than 1,089.
- **Unjudged** (J only): on either side, more than 5 % of the unique (record, chunk) pairs J needs (the
  top 10 of every list without a quote hit, all three retrievers) carry no *answers* / *not* label.
- **Label integrity** (J only): a registry row without ids or with a `pair_id` that is not
  sha256(record|chunk)[:16]; one (record, chunk) with two different judged labels in one registry; a new
  S label for a key that already had a judged label (prior registry or content-hash rule); no S registry.

The drift check (§3) is not a gate.

### 7.2 Decision rules and the wording of each outcome

Each family (Q, J) is decided on its own, in its primary cell (§4). Let n be the paired (record, corpus)
units, r the records and k the papers behind them; S% and W% the two rates; D = S% − W% in points; b and
c the units found under S only and under W only; π = (b + c) / n the observed discordance; p_NI the exact
one-sided sign-flip p over papers on X_k = 100 T_k + m_k δ with δ = 3; p_sup the exact sign-flip p on
T_k, computed only when non-inferiority holds. α = 0.05 one-sided, per family.

| outcome | rule | the sentence (filled from the report) |
|---|---|---|
| **Superior** | gates pass, p_NI ≤ 0.05 and p_sup ≤ 0.05 | "Superior (F): under the default retriever pooled over the four corpus collections, <F's outcome> was S% under S against W% under W on n paired (record, corpus) units (r records in k papers), a difference of D points (b units found under S only, c under W only; discordance π). Non-inferiority at δ = 3 points holds (exact one-sided sign-flip over papers, p = p_NI), and S is superior to W (p = p_sup; hierarchical, no extra α)." |
| **Non-inferior** | gates pass, p_NI ≤ 0.05, p_sup > 0.05 | "Non-inferior (F): <the same counts>. Non-inferiority at δ = 3 points holds (exact one-sided sign-flip over papers, p = p_NI): S is not worse than W by 3 points or more. Superiority is not shown (p = p_sup)." |
| **Non-inferiority not shown** | gates pass, p_NI > 0.05 | "Non-inferiority not shown (F): <the same counts>. The exact one-sided sign-flip over papers gives p = p_NI > 0.05: a loss of 3 points or more under S is not ruled out by this test (this is not evidence that S is worse)." *Never* "S is inferior", *never* "S loses reach". |
| **GATE-NOT-EVALUABLE** | any gate of §7.1 that applies to the family fails | "GATE-NOT-EVALUABLE (F): <each gate, with its count>. The test was computed (NI p = p_NI, n = n units) and decides nothing." The counts are still printed in full. |

<F's outcome> is "quote@10 (the top 10 cover at least 90 % of the gold quote's characters)" for Q and
"judged found@10 (quote@10, or a top-10 chunk the frozen alias judge labelled answers)" for J.

Caveats appended to the sentence ("Caveats: …"):
- **Size** (either family): when non-inferiority holds and π < 10 %, "low observed discordance (π): the
  test's size is only asymptotically α", with the simulated size of the δ = 3 test from `power.json`
  `size_cells` (0.0545 at 10 % and 0.0615 at 5 % under the measured correlations, up to 0.0766 over the
  grid at 5 %; not simulated below 5 %, since δ = 3 needs a discordance of at least 3 %).
- **J, always:** W's labels come from the 2026-11-04/06 judge runs and S's from new judging, so any judge
  drift lands on S only; and J includes the 2 records whose quote is not verbatim in its gold chunk.
- **J, judge drift:** when the drift check's agreement on W's *answers* labels is below 0.90, or cannot be
  computed (no drift registry, or no original *answers* label in the re-judged sample).

The report also writes, for each family, the secondary figures of §4 (not tested), the gates that fired,
and the outcome name (`superior`, `non_inferior`, `ni_not_shown`, `gate_not_evaluable`).

## 8. Threats to validity

- quote@10 credits only the gold location. J credits answers anywhere but inherits single-passage judging,
  which the confirmatory test showed penalises answers split across S chunks.
- The W runs date from 2026-11-04 and the S runs will be later. Same server settings; tools image and API
  versions to be recorded.
- J asymmetry: W's labels come from the 2026-11-04/06 judge runs and S's from new judging, so any judge drift
  lands on S only. The content-hash reuse rule will rarely apply, because S and W chunk texts are seldom
  identical. The drift check (§3) measures this.
- 19 documents chunk differently in the image than in the host export (rechunk DEVIATIONS D-5). The S side
  uses the collections as built.

## 9. Freeze and deviations

As in the alias boundary-confirm prereg: two reviews, the owner's go, then `sha256sum` into a private file,
then a signed annotated tag, then the run. After the freeze, any change is a dated `DEVIATIONS.md` entry.

## 10. Code needed (small)

- `rechunk` mode: run all accepted records, not only the label table's. This needs a new option, so that
  stage 1's behaviour is unchanged.
- `quote_cover(quote, chunk_texts)`: committed, tested, and the same function for W and S.
- A J on-demand builder: the lists without a quote hit, the reuse of existing labels by key and content hash,
  and the new pairs for `build-control --any-paper`.
- The report: the paired sign-flip over papers, the families, the gates and the decision wording.

Implemented (2026-11-09): the option is `rechunk: {all_records: true}` in `alias_study_run.py` (presence:
`rechunk_prep.check_rechunk_export`); the builder is `reach_report.py j-seeds` (S seeds, the drift seeds,
their export rows) and the report is `reach_report.py report` (§7). The analysis's p is `power.py`'s
`sign_flip_p_exact`, imported from the committed file.

## 11. Owner decisions (2026-11-09)

| question | owner's answer |
|---|---|
| Q1: primary cell | **default retriever, pooled over the 4 corpora**, records clustered by paper |
| Q2: hypothesis | **non-inferiority, then superiority** (hierarchical, no extra α); **δ = 3 points** (owner, 2026-11-09, after the power simulation: δ = 2 had power 0.76 at 10 % discordance) |
| Q3: J judging scope | **all three retrievers** (J's primary is still the default retriever), **plus a W re-judge drift check** on a seeded 5 % sample (owner, 2026-11-09, after the load estimate: about 12,300 new S pairs) |
| Q4: non-verbatim quotes | restated by the owner (2026-11-09): **only the 2 that fail after whitespace normalisation are excluded from Q**, and they are kept in J; 1,089 records in Q |
| Q5: 218 records retrieved on S in stage 1 | **kept** (no S-side outcome was ever computed for them; declared in §2) |
