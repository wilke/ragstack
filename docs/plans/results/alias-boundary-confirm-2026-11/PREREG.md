# Pre-registration: does a boundary-respecting chunk lose *answers* labels beyond judge noise? (alias study follow-up) — DRAFT v2, 2026-11-05

**Status: DRAFT — not frozen; owner review pending.** Nothing in this text binds a run until it is frozen
by the procedure in §9. At freeze the text is frozen **as written**, draft status line included; its sha256
goes into this directory's `README.md`, a private `PREREG.sha256` and the message of a signed annotated tag.
After freeze this file is never edited; any later change is a `DEVIATIONS.md` entry (§9.3).

**v2 (2026-11-05)** folds in two independent reviews of v1 (PR #956, Fable and an argo-agent read, both
"freeze after fixes") and the owner's decision of the same day on the draw cap and the primary test (§13):
the cap is one pair per (record, arm); the primary test is the exact sign-flip over records; exact McNemar
is a pre-specified sensitivity; the power is re-done by simulation under the clustered design, and **90 %
power is not reachable within the pool** under the sizing assumptions (§3.3) — the choice of draw size is
the one open decision (§13 Q4).

**Tier: record** (study-writeup skill). This is a **confirmatory** follow-up to the **exploratory**
chunk-boundary control of the alias study
([`../alias-study-2026-11/ADDENDUM-2026-11-05-chunk-boundary-control.md`](../alias-study-2026-11/ADDENDUM-2026-11-05-chunk-boundary-control.md),
plan [`../alias-study-2026-11/control/CONTROL-PLAN.md`](../alias-study-2026-11/control/CONTROL-PLAN.md),
artifact [`../alias-study-2026-11/control/artifacts/control-judge.json`](../alias-study-2026-11/control/artifacts/control-judge.json)).
It changes no confirmatory number, cell, record, judge, seed or gate of the frozen alias prereg
(`../alias-study-2026-11/prereg/PREREG-alias-study.md`, sha256 `d54f4201…`, tag `prereg-alias-study-2026-11`).
It tests **one** new, pre-specified hypothesis on **fresh** pairs.

> **Public copy.** This file names no tenant, host, endpoint, collection, person or gateway owner. It holds
> counts, rates, model ids, version strings, commit shas and sha256 hashes only; no question, passage, paper
> id, record id, pair id or file path.

**What had been seen before writing.** The drafting session read the exploratory addendum and its public
artifact in full (every figure of §1 below is from there), the control plan, and the committed alias prereg.
It stayed **blind to every outcome a drawn pair could carry**: from the private stage-A registry it read only
the fields that define the eligible pool and its structure (`stage`, `source`, `label`, `arm`, `record_id`,
`chunk_id`, the passage paper id for a cluster count), and from the control's private sample only pair,
record and window ids with stratum and arm; it opened no control, cover-any or retest label of any pair, no
chunk or record text, and no spot-check sheet. The pool count of §4.1 is also derivable from the committed
public artifact `control/artifacts/control-draw.json` alone (`counts.available.answers` summed minus
`counts.drawn.answers` summed), and the two agree. The record × arm structure the power simulation uses
is embedded in `power.py` as anonymised counts (per record, its eligible pairs per arm; no id).

---

## 1. The question, and the exploratory finding it tests

**Question.** On stage-A pairs whose two Qwen judges agreed that the 512-token window *answers* the
question, does the frozen judge say *not* more often when it reads the boundary-respecting control chunk
covering the same text than when it re-reads the identical window?

**The exploratory finding (ADDENDUM-2026-11-05, every number exploratory).** On the 150 control pairs of the
*agreed answers* stratum, judging the control chunk flipped **28 / 150 = 18.7 %** (Wilson 95 % 13.2–25.7) to
*not*; re-judging the identical stage-A window (the test-retest null) flipped **6 / 150 = 4.0 %** (1.8–8.5).
On the 99 *agreed not* pairs neither arm gained an answer (0 and 1). On the 51 tiebroken pairs the two arms
were indistinguishable (23.5 % vs 25.5 %). Overall: 40 / 300 = 13.3 % against 20 / 300 = 6.7 %. No test
was run on any of these.

**Why a confirmatory test is needed.** The agreed-*answers* contrast is the clearest pattern in that sample,
but the stratum, the direction and the route were read after the overall flip rate, the retest null was
added after the control's result had been seen, and the comparison was chosen because it was the largest.
Its point estimate is therefore biased upward (winner's curse) and its interval does not carry the
selection. This test fixes the endpoint, the direction, the sample size and the analysis before any new
label exists, on pairs the control never judged. **Owner decision (2026-11-05):** paired design, fresh
pairs, a single named primary endpoint, direction fixed in advance.

**What it does not test.** Retrieval (found@10) under the control chunking; any *not → answers* direction;
the tiebroken route; other chunker settings; whether the judge is right. The outcome is a judge label, and
the claim is about the judge's reading of one passage under two cuts (CONTROL-PLAN §9), not about answer
quality.

## 2. The estimand and the single primary endpoint

**Unit.** One stage-A judged pair (record × stage-A window) from the eligible pool of §4.1, drawn by the
rule of §4.3. A **record** is one question; a record can contribute up to three pairs, one per corpus arm.

**Two arms, both on every pair, both judged by the frozen judge at the frozen settings:**

| arm | passage judged | pair id in the registry |
|---|---|---|
| **control** (K) | the control chunk of the window's document to which the window maps by `max-overlap` (§5, step 2): `sentence/512/0` + `structure_cuts sections/1` + `size_tolerance 0.25` (ceiling 640 SFR tokens) + `min_piece 128`, Markdown adapter `markdown/1` raw mode, Punkt `punkt-abbrev-v1`, tokens in `Salesforce/SFR-Embedding-Mistral` — the control plan's §2 chunking, byte for byte the same product code path | `pair_id(record, control chunk)`, `control_of` = the stage-A pair id |
| **retest** (R) | the identical stage-A window (`fixed_token/512/0`), byte-identical prompt to stage A's | the stage-A pair id |

**The frozen judge** (alias prereg §6.3, D8; `main/judge-config.json` with only `out_dir` and
`chunk_exports` changed, as `alias_study_judge.control_config_problems` enforces): primary **C** =
`Qwen3.6-35B-A3B`, prompt `alias-judge/2`, thinking on, n = 5, T 0.7, `max_tokens` 8192, majority of the
valid samples; primary **E** = `Qwen3.8-27B`, same prompt, thinking on, n = 1, T 0.7, `max_tokens` 8192;
C = E final, otherwise the **tiebreaker** `Gemini 4 Argon` via the gateway (`alias-judge/2`, `max_tokens`
4096, **provider default temperature, unrecorded**, `fallback_model ""`, ≤ 4 in flight, one re-issue of an
empty or unparseable reply, then *unjudged*); the **drift reference** `Claude Opus 5.5` (`alias-judge/1`,
1 repetition, `max_tokens` 2048) on a seeded 5 % sample per arm, **never setting a label**, the topic-filter
rule of §6.3 (one re-issue, then *no-reference*); served-model check on every backend. **The treatment
includes the definitions block:** the prompt's definitions are selected from the passage's own paper's
glossary by the terms the passage contains (`definitions_for`), so a control chunk and its window can
carry different definitions. That is part of "judging the control chunk", not a confound to remove.

**Outcomes per pair.** X = 1 when the control arm's final label is *not*, else 0. Y = 1 when the retest
arm's final label is *not*, else 0. (Every pool pair's stage-A label is *answers*, so "final label = *not*"
is the same event as "flipped".) Per record j, the **score S_j = Σ (xᵢ − yᵢ)** over its drawn pairs
(∈ {−3, …, 3}).

**The primary endpoint.** The paired contrast **P(X = 1) − P(Y = 1)**, read as the per-pair difference
d = mean(xᵢ − yᵢ) over the drawn pairs, tested through the record scores S_j. **The population the estimand
refers to is defined by the draw rule of §4.3, not by uniform sampling from the pool:** arms are drawn in
equal numbers, at most one pair per (record, arm), and within an arm a record with more eligible pairs is
reached earlier (it is reached with probability proportional to its pair count). The figure is the
contrast at that composition; the pool-weighted contrast is not estimated.

**Hypotheses.** H0: P(X = 1) ≤ P(Y = 1) (the control chunk loses *answers* no more often than the judge's
own re-read does). H1: P(X = 1) > P(Y = 1). **One-sided, direction fixed now.**

## 3. The tests, and the sample size

### 3.1 The primary test, and the sensitivity

**Primary: the exact one-sided sign-flip test over records** (owner decision, 2026-11-05):
T = Σ_j S_j; under the sharp null each record's score is sign-symmetric (swapping the two arms negates every
pair's difference and so S_j), independently across records; **p = P(T* ≥ T)**, exact by enumeration of the
2^k sign vectors when the non-zero records k ≤ 20 and by lattice convolution otherwise, with the Monte-Carlo
p printed beside it as descriptive — `chunktrial_stats.sign_flip_test_exact(sums, alternative="greater")`,
already on `main`, with its `min_attainable_p`. **α = 0.05.** The test decides; nothing else does.

**Why this test.** Both arms are judged on the same pair, so the between-pair variation cancels, but pairs
of one record are not independent: they share the question, the glossary definitions and the judge's
reading of that question, and a record contributes up to three pairs. A test that treats pairs as
independent (McNemar) is then anti-conservative when within-record outcomes are positively correlated —
the simulation of §3.2 puts its size at **.071** under the sizing correlation against **.038** for the
sign-flip test (`power.json`, the null scenario). The sign-flip test conditions on the record scores, so
its size holds under any within-record dependence, and it equals exact McNemar when every record gives one
pair. Clusters above the record (passage papers: 1,050 distinct in the pool, the largest holding 15 pairs;
documents) are not modelled; the realised largest paper cluster and the number of records drawn in ≥ 2 arms
are reported (§6).

**Pre-specified sensitivity: exact one-sided McNemar on the pooled discordant pairs**, b = #(X = 1, Y = 0),
c = #(X = 0, Y = 1), p = P(Binomial(b + c, ½) ≥ b) (`chunktrial_stats.mcnemar_exact(b, c, "greater")`),
printed beside the primary, **deciding nothing**; a primary and a sensitivity that disagree are both quoted
in the sentence.

**Intervals (descriptive, deciding nothing).** Beside the primary: the **record-cluster bootstrap**
percentile interval of d (resample records with replacement, 10,000 resamples, seed
`261105|alias|confirm|boot` by the project's seed rule), **90 % two-sided**, whose upper end is the
one-sided 95 % upper bound the null wording of §7 quotes. Beside the McNemar sensitivity: the **Tango
(1998) asymptotic score interval** for the paired difference, 90 % two-sided (the interval that inverts the
score form of the paired test; the Wald form of v1 is withdrawn). Each arm's rate with its Wilson 95 %
interval; the 2 × 2 table (both *not*, control only, retest only, neither); the distribution of S_j; the
counts behind each.

### 3.2 Power, computed by committed code

`power.py` beside this file (standard library only; seeded `261105|alias|confirm|power`; 5,000 simulated
studies per cell, Monte-Carlo SE ≈ .004 at power .9) simulates the design as specified: the draw rule of
§4.3 on the pool's anonymised record × arm structure (262 records; 470 (record, arm) cells = 198 base /
154 neighbour / 118 random; records in 1 / 2 / 3 arms = 118 / 80 / 64), a 5 % independent exclusion per
drawn pair, pair outcomes with within-record correlation, the exact sign-flip test (its null distribution by
convolution over the records grouped by |S_j|, exact) and the McNemar sensitivity on the same data. Its
output is `power.json` (sha256 in `README.md`).

**The planning effect.** Control flip **pX = 13 %** against retest flip **pY = 6 %**. 13 % is about the
Wilson lower bound (13.2 %) of the selected stratum's 18.7 %, which roughly halves the selected excess
(14.7 points → 7); 6 % is the exploratory overall retest rate and is conservative against the stratum's
4.0 %. Re-judging **both** arms afresh cancels regression to the mean within each pair: the stage-A
*answers* label selects borderline passages into the pool, but it does so for the control chunk and the
window alike, so the selection raises pY as much as it raises pX and the difference is what the test reads.

**The discordance model.** On one pair the joint of (X, Y) is set by the phi coefficient:
p11 = pX pY + φ √(pX(1−pX) pY(1−pY)), p10 = pX − p11, p01 = pY − p11. **φ = 0 is the sizing case:** among
non-negative φ it gives the most discordant pairs with the smallest share in the hypothesised direction, so
the least power for a fixed difference. A **negative φ** — a passage that flips under one cut being *less*
likely to flip under the other — has no mechanism here: both arms re-judge the same borderline text with
the same judge, and anything that makes a pair borderline raises both flip rates; φ < 0 is therefore not
simulated. **Within-record correlation ρ:** with probability ρ all pairs of a record share one (X, Y) draw,
otherwise they draw independently (the intra-record correlation of X, of Y and of X − Y is then ρ).
**ρ = 0.5 is the sizing case** (conservative: half the records behave as one unit); 0, 0.3 and 1.0 are
sensitivities.

**Power of the primary by draw size, sizing case (13 % vs 6 %, φ 0, ρ 0.5)** — `power.json`
`sizing_power_by_n_draw`; realised n ≈ 0.95 × draw; records ≈ 215 at 370, of which ≈ 100 in two or three
arms:

| n_draw | 300 | 334 | 350 | **370** | 390 | 410 | 430 | 450 | 470 (all cells) |
|---|---|---|---|---|---|---|---|---|---|
| sign-flip power | .751 | .773 | .794 | **.806** | .824 | .838 | .859 | .866 | .879 |
| McNemar (sensitivity) | .833 | .849 | .866 | .883 | .891 | .901 | .915 | .924 | .933 |
| realised n (mean) | 285 | 317 | 333 | 352 | 370 | 390 | 409 | 428 | 447 |

**90 % power is not reachable** within the 470 (record, arm) cells under the sizing assumptions
(`feasible_within_availability` false). It would be reached at a draw of **450 with ρ = 0.3** or **350 with
ρ = 0** (`sizing_power_by_n_draw_by_rho`). The 80 % target is reached at **n_draw = 370** (the smallest grid
value at or above .80 at every larger grid value).

**Sensitivities at n_draw = 370** (`scenarios[].at_n_draw_80`, sign-flip / McNemar):

| scenario | sign-flip | McNemar |
|---|---|---|
| **13 % vs 6 %, φ 0, ρ 0.5 (sizing)** | **.814** | .885 |
| ρ 0 | .925 | .925 |
| ρ 0.3 | .860 | .901 |
| ρ 1.0 | .712 | .852 |
| φ 0.2, ρ 0.5 | .884 | .935 |
| φ 0.4, ρ 0.5 | .955 | .980 |
| 11 % vs 6 %, φ 0, ρ 0.5 | .589 | .707 |
| 13 % vs 8 %, φ 0, ρ 0.5 | .519 | .644 |
| 10 % vs 6 %, φ 0, ρ 0.5 | .440 | .553 |
| **null 6 % vs 6 %, ρ 0.5 (size)** | **.038** | **.071** |
| *reference only, selected, not for sizing:* 18.7 % vs 4.0 % | 1.000 | 1.000 |

For continuity: the v1 design (one pair per record, independent pairs, exact McNemar, analytic) needed
237 / 318 pairs for 80 % / 90 % (`analytic_reference`).

### 3.3 The draw size (owner decision pending, §13 Q4)

- **Recommended: n_draw = 370** (80 % power in the sizing case; `n_draw_recommended`). Realised n ≈ 352
  pairs with both final labels over ≈ 215 records; quotas 124 / 123 / 123 by arm (§4.3), within every arm's
  availability (198 / 154 / 118).
- **The 80 % floor is n₈₀ = 352 realised pairs** (the simulation's mean at 370). Below it the test is
  GATE-NOT-EVALUABLE (§8).
- **Minimum detectable effect** at n_draw = 370 (80 % power, pY 6 %, φ 0, ρ 0.5, on a 0.5-point grid):
  a control rate of **13.0 %**, an excess of **7.0 points** (`mde.at_n_draw_80`; power .757 at 12.5 %, .816
  at 13.0 %). What the design **cannot** resolve: an excess of 4–5 points (power .44–.59).
- **Alternatives for the owner** (§13 Q4): draw **every cell, 470** → power ≈ .88, realised ≈ 447, but the
  random arm is exhausted (118) so the arms are unequal (198 / 154 / 118) and the estimand's mix changes, and
  there is no slack for exclusions beyond the 5 % modelled; **410** → .84; or state ρ = 0.3 as the planning
  assumption and draw 450 for 90 % — not recommended, since ρ is unknown and 0.5 is the conservative choice.
  Reverting to one pair per record (318 for 90 %) is the v1 design the owner replaced.

## 4. The population and the draw

### 4.1 The eligible pool (defined before any new label exists)

Every stage-A judged pair of the alias study's **committed** registry (stage-A commit sha256
`f53ca9d0b18fb66753ae3fbe219b7b63e2163aee44f8dfe0e9ce386b04d266cd`, the one `control-draw.json` and
`control-judge.json` name) whose final row has `source = agree` **and** label *answers* (the C and E
primaries agreed on *answers*; no tiebreak), **minus** every pair of the exploratory control's 300-pair
sample (private `sample.jsonl`, sha256 `64a8bbc1…` as committed in `control-draw.json`).

| count (drafting session's read-only pass; reproduced by `draw-confirm` at the draw) | n |
|---|---|
| stage-A pairs | 9,205 |
| … C and E agreed | 8,580 (tiebroken 625) |
| … agreed **answers** | **2,064** (base 816 / neighbour 542 / random 706: `control-draw.json` `counts.available.answers`) |
| in the exploratory control (its *answers* stratum) | 150 (50 per arm) |
| **eligible pool** | **1,914** (base 766 / neighbour 492 / random 656) |
| … whose window is also a control pair's window (same chunk, another record) | 43 → excluded (§4.2); pool **1,871** (748 / 481 / 642) |
| distinct records (questions) in the 1,871 | **262**; in 1 / 2 / 3 arms: 118 / 80 / 64; up to 51 pairs in one record |
| (record, arm) cells, the draw's availability | **470** = 198 base / 154 neighbour / 118 random |
| distinct passage papers | **1,050**; the largest holds 15 pairs |
| distinct windows | 1,734; 123 windows carry pairs of more than one record (260 pairs) |

A pair's **arm** is the arm group its passage paper belongs to (base / neighbour / random), as the drift
and spot-check strata define it (alias prereg §6.2). The pool is not re-derived from anything but the
committed registry's final rows; no retrieval, no new stage-A label.

### 4.2 Exclusions fixed before the draw

1. The 300 control pairs (all strata), by pair id.
2. The 43 pool pairs whose stage-A window is a control pair's window under another record. Their control
   chunk already exists and was judged against another question; excluding them makes "fresh" hold for the
   passage as well as the pair, at 2 % of the pool. (Owner answer Q1, §13.)
3. Nothing else. In particular, pairs whose **record** has a control pair (136 of 262 records) stay: the
   control judged a different window of that question, and no label of it is read.

### 4.3 The draw rule (owner decision, 2026-11-05)

- **Stratified by arm, equal across base / neighbour / random**, the remainder to base, then neighbour
  (`alias_study_judge.drift_allocation`, the rule the control's draw and the drift sample use): 370 →
  **124 / 123 / 123**. Equal arms keep the mix the exploratory *answers* stratum had (50 / 50 / 50), so the
  estimand is read at the same composition; the by-arm rates are secondary descriptives (§6). Simple random
  sampling would weight the arms 40 / 26 / 34 and would not change the test, but would make the estimand a
  pool-weighted one that no earlier figure was read at. (Owner answer Q2.)
- **At most one pair per (record, arm)**, so a record contributes up to three pairs, one per arm: within an
  arm, pairs are visited in ascending order of `sha256(261105|alias|confirm|draw|<arm>|<pair id>)` and a
  pair is taken only when (i) no pair of its record has been taken **in this arm** and (ii) its window has
  not been taken in any arm (**one pair per window**, so no two drawn pairs share a passage); the walk stops
  at the arm's quota. A short arm's shortfall goes to the other arms as `drift_allocation` does.
  Availability is 198 / 154 / 118 cells, above every quota. **This does not remove within-question
  dependence** — the simulation expects ≈ 100 of ≈ 215 drawn records in two or three arms — which is why the
  primary test is clustered by record (§3.1); the realised number of records drawn in ≥ 2 arms is reported
  (§6). Because the first pair of a record to appear in an arm's order is the minimum of its pairs' hashes,
  a record with more eligible pairs in that arm is reached earlier (with probability proportional to its
  pair count); this is the estimand's composition (§2) and the power simulation reproduces it.
- **Seed string `261105|alias|confirm|draw`** (new; the project's `<yymmdd>|<study>|<purpose>` pattern,
  alias prereg §5.5). The judge's labelling order stays the frozen `261101|alias|judge|order`, and the
  drift sample stays `261101|alias|drift|<batch>` (one batch of ≤ 2,000; 5 % of the registry rounded half
  up, ≈ 18 at a registry of ≈ 352 or 19 at 370, split equally with the remainder to base then neighbour:
  6 / 6 / 6 or 7 / 6 / 6), because the judge configuration is the frozen one.
- **Run by committed code before any judging** (`draw-confirm`, §11), after the freeze, from a clean
  checkout: it refuses unless the stage-A commit sha256 and the control sample's sha256 are the two above.
  Its private output `sample.jsonl` (pair, record, window ids; arm; stratum *answers*) stays off the
  repository; its public `draw.json` (the counts of the table above as recounted, the per-arm availability
  and quotas, records drawn in 1 / 2 / 3 arms, the seed, the sha256 of the private sample and of the
  inputs, provenance) is committed **before** `chunk` runs. The draw is run by the operator; the analyst does
  not see pair ids.

## 5. Procedure (every step gated; nothing judged before step 4)

0. **Freeze** (§9.2). Then, in a clean checkout at the frozen commit (`dirty false`, `untracked 0`,
   `citable true` in every artifact's provenance), with `PYTHONPATH=<checkout>/python`, the Punkt
   segmenter (`punkt-abbrev-v1`), the `[markdown]` extra and the SFR tokenizer present
   (`alias_control_chunks.require_env` refuses otherwise). Every subcommand of steps 1–2 runs with
   `--confirmatory <this prereg's sha256>` (§11), so no public file of this test carries `exploratory: true`.
1. **`draw-confirm`** (§4.3) → private `sample.jsonl`, public `draw.json`. *Gate:* the two input shas verify;
   no drawn pair is a control pair; no two drawn pairs share a window; counts as expected; `draw.json`
   committed.
2. **`chunk`** on the sampled documents (unchanged subcommand): record text resolved for every document by
   reproducing **every** stage-A chunk of it (`text[start:end] == content`); the control chunks with the P2
   invariants measured (no fragment of two segments, sentence or segment boundaries only, ≤ 640 tokens,
   tiling). → private `chunks-control.jsonl`, `docs.jsonl`; public `chunk.json`. *Gate:* `p2_gate` passes;
   documents excluded as `no_text` / `no_match` / `ambiguous` counted.
   **`map`** (unchanged): each window's offsets checked, then `max-overlap` (largest character overlap, ties
   to the earlier chunk), coverage and purity recorded. → private `pairs-seed.jsonl`; public `map.json`.
   **`retest-seed`** (unchanged): the same pairs re-pointed at their own stage-A windows, the windows as an
   export → private `chunks-retest.jsonl`, `pairs-seed-retest.jsonl`; public `retest-seed.json`.
   *Gate:* the mapped count ≥ n₈₀ (else stop and record; §8); exclusions counted by reason; the three public
   files committed. No label exists yet.
3. **Two registries** by `alias_study_judge.py build-control` (unchanged), each in its own new `out_dir`
   under one control config: the control arm from `pairs-seed.jsonl` with `chunks-control.jsonl` as the
   control export; the retest arm from `pairs-seed-retest.jsonl` with `chunks-retest.jsonl`. Each config is
   the main judge config with only `out_dir` and `chunk_exports` changed (`control_config_problems`), and
   `pinned_problems` still enforces the alias prereg's sha. *Gate:* the two registries hold the same set of
   `control_of` ids; `pairs-manifest.json` `inputs_sha256` recorded.
4. **`preflight`** per backend and arm (C, E, Argon, Opus × 2). The preflight writes the size notice. **The
   gateway notice goes to the gateway coordinator, naming both arms' expected call counts, and no gateway
   call is made before its acknowledgement in chat** (`label` refuses without `--notice-acknowledged`).
   *Gate:* served-model check on every backend; `fallback_model ""`; ≤ 4 in flight; the Opus cap at 7 % of
   the registry (alias prereg §6.5; ≈ 24 at a registry of ≈ 352, 25 at 370; ≈ 18–19 expected); no Argon cap
   (R30).
5. **`label`**, per backend, the two arms back to back, **the arm that goes first alternating by backend**
   so that neither arm is always the later one: **C: control → retest; E: retest → control; Argon:
   control → retest; Opus: retest → control.** Then `finalize` both. (The harness labels one registry at a
   time and the batch size is pinned to the main config's 2,000, so finer interleaving is not available
   without a harness change; the alternation is what the frozen harness allows, and the time confound that
   remains is read by the descriptive of §6.7.) **Hard stop** on any provider notice (`provider_notice`,
   ACCESS DENIED, a served-model mismatch, the budget stop, the arm-gap pause of §6.3): the run halts, the
   state is reported, and **the owner's word in chat is required to resume**; a resume under the same frozen
   design is recorded, a change is a deviation. Finishing without Opus after a notice is a separate owner
   decision, reported as such (CONTROL-PLAN Q6).
   **Timestamps:** the first and last call time (UTC) of every backend in every arm go into the public
   report, read from the label rows' `t` (and `duration_s`) in `labels-qwen-C.jsonl` / `labels-qwen-E.jsonl`
   for the Qwen backends and from the calls logs' `t`, `step` and `status` in `calls-gw-argon.jsonl` /
   `calls-gw-opus.jsonl` for the gateway backends. The exploratory took these from private logs; here they
   are an artifact field.
   **No one inspects labels between `label` and `confirm-report`.** `LOG` and `judge-summary.json` print
   per-arm counts as labelling proceeds, and `final.jsonl` is readable; the operator reads none of
   `final.jsonl`, `labels-*.jsonl`, `judge-summary.json` or the per-arm counts in `LOG` of either arm until
   step 6 has written `confirm-report.json`. The run's progress is followed by the `status` subcommand's
   totals only. Any look at a label or a per-arm count before that is reported in `RESULTS.md` with what was
   seen and when.
6. **`confirm-report`** (§11), run **once**, after both arms are finalized, before any label is looked at by
   a person: the primary table, the sign-flip p, the McNemar sensitivity, d and its intervals, counts,
   unjudged counts, cluster counts, timestamps, shas, provenance, this prereg's sha256 → public
   `confirm-report.json`, committed. *Gate:* `public_problems` clean (no id, text, host); the frozen sha
   matches.
7. **Cover-any** (descriptive, §6): only after step 6 is committed, `map --cover-any` over the control-arm
   *not* pairs, a third registry, the same gates, a second gateway notice; then `confirm-report` again with
   `--cover-config` adding the `cover_any` block. The primary numbers of step 6 are not recomputed.
8. `RESULTS.md` in this directory, with the decision sentence of §7 verbatim.

## 6. Secondary and descriptive quantities (reported, **not tested**)

All with counts and Wilson 95 % intervals where a rate is shown; **no p value but the primary's and the
pre-specified McNemar sensitivity's is computed, and no quantity below decides anything.**

1. **By arm:** X and Y rates and the discordant counts per arm (base / neighbour / random).
2. **Cluster structure as realised:** records drawn, records drawn in 1 / 2 / 3 arms, the distribution of
   S_j, distinct passage papers and the largest paper cluster, distinct documents.
3. **Cover-any recovery** on the control arm's *not* pairs: the share for which some other control chunk
   overlapping the window is labelled *answers* (`recovered_by_other_chunk`), the four-way split
   (other only / both / primary only / none), and the primary chunk's same-bytes re-judge agreement on those
   selected pairs.
4. **Escalation:** C-alone and E-alone *answers* rates and the escalation share per arm; the overlap of the
   escalated sets between the arms (Jaccard) and, for pairs escalated in both arms, the Argon verdict
   agreement — **not** a byte-identical replicate here, since the prompts differ between arms; the
   byte-identical replicate is the retest arm's Argon rows against stage A's (`tiebreak_replicate`), read
   beside the exploratory baseline of 27 / 34 (79.4 %) and used for the cache check of §10.10.
5. **Opus drift reference:** agreement between each arm's final label and Opus on the 5 % sample, overall
   and per arm, the *no-reference* count. The arm-gap pause of §6.3 cannot trigger (it needs ≥ 100
   Opus-answered pairs per arm); the reference lines are printed, not gated.
6. **Boundary diagnostics** of the sampled windows and control chunks, as the exploratory artifact's
   `metrics.boundary`; X and Y by window-coverage band and control-chunk token band; the LaTeX-preamble and
   one-untitled-section strata.
7. **Time:** the per-backend, per-arm first and last call times and the gap between the two arms' batches
   per backend; and **discordance by time block**: b and c with each arm's pairs split into halves by their
   position in the (seeded) labelling order, and by whether the pair's C call fell in the first or second
   half of C's calls for that arm — a check that the arm effect is not a drift of the judge over the run.
8. **Sensitivities of the primary to unjudged pairs** (the same test, recomputed, printed beside, deciding
   nothing). A pair is **unjudged** in an arm when its final row has `source == "unjudged"` (equivalently
   `label` is `None`; `finalize` writes a row for every pair, and `label_of` maps such a row to 0). The
   primary **drops any pair unjudged in either arm** (pairwise deletion; the counts by arm are printed).
   Three re-computations: (a) *unjudged = not* in that arm, through `label_of` (the alias prereg's
   primary-analysis convention); (b) the **adverse bound**: an unjudged control pair scored *answers*
   (X = 0) and an unjudged retest pair scored *not* (Y = 1); (c) the **favourable bound**: the reverse. If
   (b) and the primary disagree on confirmation, the sentence says so.

**Explicitly not done:** any test by arm, route, band, block or stratum; any test of the cover-any share;
any comparison with the exploratory 300; any *not → answers* figure (none can exist in this pool); any
retrieval figure; any change to any alias-study number.

## 7. Decision rules and the wording of each outcome

Let n be the pairs with a final label in both arms, b and c as in §3.1, p the primary's exact sign-flip p,
d the per-pair difference, U the one-sided 95 % upper bound on d from the record-cluster bootstrap, pX̂ and
pŶ the arm rates, and E the minimum detectable excess at the realised n (the §3.3 MDE, 7.0 points at
n ≈ 352, pY 6 %, φ 0, ρ 0.5).

| outcome | rule | the sentence (filled from `confirm-report.json`) |
|---|---|---|
| **Confirmed** | gates pass (§8) **and** p ≤ 0.05 | "Confirmed: on n fresh stage-A pairs (r records) whose two Qwen judges had agreed the 512-token window *answers*, the frozen judge labelled the `max-overlap` control chunk under this chunker (`sentence/512/0`, section cuts, 640 ceiling) *not* on b pairs where it kept *answers* on the identical window re-read, against c the other way (exact one-sided sign-flip over records p = …; control pX̂ %, retest pŶ %, difference d points, 90 % cluster-bootstrap interval …; McNemar sensitivity p = …). The control chunking as produced — its cut positions, coverage and shorter chunks together — loses *answers* labels beyond the judge's own test-retest noise, under single-passage judging." |
| **Not confirmed (powered null)** | gates pass **and** p > 0.05 | "Not confirmed: on n fresh pairs (r records) the control chunk was labelled *not* on b pairs against c for the identical window (sign-flip p = …; control pX̂ %, retest pŶ %, difference d points; the one-sided 95 % upper bound on the excess is U points; McNemar sensitivity p = …). At this n the design had 80 % power for an excess of E points (a control rate of 13.0 % against a retest rate of 6 %, independent discordance within a pair, within-record correlation 0.5); smaller excesses are not resolved by this test. The exploratory 18.7 %-vs-4.0 % contrast was not confirmed." *Never* "no difference", *never* "the chunking has no effect". |
| **GATE-NOT-EVALUABLE** | any §8 gate fails | "GATE-NOT-EVALUABLE: <the gate, with its count>. The test was computed (p = …, n = …) and decides nothing." The table is still printed in full. |

The sentence always carries: the realised n and r, b and c, p, both arm rates, U or the interval, the MDE at
the realised n with its assumptions, the word *exploratory* for any figure quoted from the earlier control,
*judge incomplete* when unjudged pairs exceed 1 % of mapped pairs in either arm (alias prereg §6.3's rule,
carried over), and *cache suspected* when the check of §10.10 fires.

## 8. Gates (a third answer, preserved in every table)

The result is **GATE-NOT-EVALUABLE** when any of these holds; a gate is read before the p is read, by the
report, not by a person:

1. **Realised n < n₈₀ = 352** pairs with a final label in both arms after the exclusions of §5 step 2 and
   the pairwise deletion of §6.8. With 370 drawn this needs a loss above the 5 % modelled — a harness
   failure, not noise. (If the owner chooses another draw size in §13 Q4, n₈₀ is that size's
   `mean_realised_n` in `power.json`.)
2. **Unjudged > 5 %** of mapped pairs in either arm (1–5 %: the *judge incomplete* caveat, not a gate).
3. **The two registries do not hold the same `control_of` set**, or a drawn pair is a control pair, or two
   drawn pairs share a window, or the stage-A commit sha256 or the control sample sha256 differs from §4.1.
4. **The chunker gate fails** (`chunk.json` `p2_gate` false) or the environment check fails (segmenter ≠
   `punkt-abbrev-v1`, adapter ≠ `markdown/1`, `[markdown]` absent, SFR tokenizer absent).
5. **An arm is incomplete after a hard stop** (§5 step 5) and the owner did not resume under the same
   frozen design; a served-model mismatch stored no reply, by the harness's rule.
6. **The report's input shas** (both registries' `pairs-manifest.json` `inputs_sha256`, the seed files, the
   chunk exports) do not match the committed `draw.json` / `chunk.json` / `map.json` / `retest-seed.json`.

A gate that fails is named with its count; *absent* is written *absent*, never left blank.

## 9. What is frozen, how, and the deviations policy

### 9.1 Frozen before data (the freeze set)

This text; `power.py` and `power.json` as committed (produced from a clean tree at the commit their
provenance names); the endpoint, direction, primary test, sensitivity, α, the draw size the owner chooses
in §13 Q4 and its n₈₀; the pool definition and the two exclusions (§4.1–4.2); the draw rule, the
(record, arm) cap, the one-pair-per-window rule and the seed strings (§4.3, §3.1); the control chunking (the
control plan's §2 markers, `chunk_descriptor`
`sentence/512/0/{"min_piece":128,"pack":"318","size_tolerance":0.25,"size_unit":"tokens","structure_cuts":"sections/1"}`);
the `max-overlap` mapping; the judge configuration (the main judge config byte for byte but for `out_dir`
and `chunk_exports`); the step order and the arm alternation of §5; the gates of §8; the wording of §7; the
code of §11, merged and reviewed, at one `main` commit that the freeze names.

### 9.2 Freeze procedure (the alias prereg's, R10)

1. The owner says go, in writing, naming the commit. The text is frozen as written.
2. In a clean checkout of that commit, `sha256sum PREREG.md` and a UTC timestamp go into a private
   `PREREG.sha256` kept with the outputs, **before the draw**.
3. A **signed annotated tag `prereg-alias-boundary-confirm-2026-11`** at that commit, its message holding
   this file's sha256, the timestamp, the sha256 of `power.json`, the stage-A commit sha256 and the control
   sample sha256 of §4.1; pushed to the public repository before the draw. `git tag -v` verifies it; the tag
   is never moved.
4. One commit records the freeze in this directory's `README.md` only. The file is never touched again.
5. `confirm-report` takes `--prereg PREREG.md --prereg-sha256 <sha>` and refuses on a mismatch, so the
   public report carries the frozen sha; `--confirmatory <sha>` on the earlier subcommands stamps the same.

### 9.3 Deviations

After freeze, any change to the endpoint, direction, test, α, n, pool, exclusion, draw, seed, chunker
setting, mapping, judge setting, step order, arm order, gate or wording is a dated entry in `DEVIATIONS.md`
in this directory, stating what changed, why, when, what had been seen at that time and which result it
touches; a result whose computation depends on it says so in its sentence. A resume after a hard stop under
the same design is recorded in the report (`run_segments`), not a deviation; any change made while stopped
is one. Re-running the draw, for any reason, is a deviation with a new seed string.

## 10. Threats to validity and limitations

1. **The judge is not truth, and it is the same judge in both arms.** A systematic preference of this judge
   for or against boundary-respecting text would show up as a confirmed result that another judge would not
   reproduce. The exploratory spot check found the judge and the owner disagreeing mostly on partial
   answers (CONTROL-PLAN Q4); a control chunk that holds only part of what the window held is exactly where
   that line matters. The Opus drift sample is the only outside reading, at 5 % and never a label.
2. **Judge non-determinism is the null, and it is not stationary.** The retest arm measures the judge's own
   flip rate at the time of the run; the exploratory retest flipped 4.0 % of this stratum and 6.7 % overall.
   The gateway models run at the provider's default temperature, which is **unrecorded** (alias prereg
   §6.3; ADDENDUM *Limitations*).
3. **Arm order is confounded with time within a backend.** The harness judges one registry at a time, so
   within each backend one arm is always judged after the other; the alternation of §5 step 5 balances
   which arm is later across the four backends but not within one, and the descriptive of §6.7 reads the
   drift it leaves. A drift of the judge over the hour or so of a backend's run would show as a block effect
   there; it cannot be removed by design under the frozen harness.
4. **Dependence between pairs.** The primary test clusters by record and so holds its size under any
   within-record dependence (§3.1); it does not cluster by passage paper or document, where pairs of
   different records can share text (1,050 papers, the largest with 15 pairs in the pool). The realised
   paper and document clustering is reported, not modelled.
5. **The contrast bundles everything the control chunking changes.** A control chunk differs from its
   window in cut position, in how much of the window it covers (coverage), in length (mean 405 vs 449 SFR
   tokens in the exploratory 300) and in the definitions block its text selects (§2). A confirmed result is
   about **this control chunking as produced**, read through the `max-overlap` mapping — not about
   boundary-respect in isolation; the §7 wording says so, and the coverage and token-band descriptives of
   §6.6 show where the flips sit without attributing them.
6. **The pool comes from the same study** whose exploratory control motivated the test. The pairs are
   fresh, but the population (OA-1000 and its neighbour and random arms, stage-A retrieval outputs, the
   records of §4 of the alias prereg) is the one the pattern was first seen in. The result generalises no
   further than that population, and no further than the draw rule's composition (§2).
7. **One chunker setting**, the control plan's: `sentence/512/0` with section cuts at the 640 ceiling.
   Nothing here speaks to other sizes, overlaps or an untitled-section adapter (PR 5b) or PDF headings
   (PR 8); the one-untitled-section share is reported (25.3 % of the exploratory 300).
8. **A reach metric read on a label.** The outcome is "a single passage, read alone, states the core of the
   answer". It is the alias study's found@10 reading (CONTROL-PLAN §9), not answer quality under synthesis.
   A confirmed result says the *label* moves with the cut; it does not say retrieval would.
9. **Only the *answers → not* direction** on agreed pairs is tested. The exploratory saw no *not → answers*
   on agreed-*not* pairs (0 / 99) and nothing on tiebroken pairs; both are untested here.
10. **A response cache would favour the retest arm.** The retest arm's prompts are byte-identical to stage
    A's; a gateway- or endpoint-side cache returning a stage-A answer would make the retest flip rate
    artificially low and the contrast artificially large. The Qwen endpoints sample at T 0.7 (prefix
    caching there changes speed, not sampling); the gateway's behaviour is not known. **Check** (§6.4):
    the retest arm's Argon verdict agreement with stage A on byte-identical prompts, read beside the
    exploratory 27 / 34 = 79.4 % (63.2–89.7); the report carries *cache suspected* when that agreement is
    at or above 0.95 on at least 20 pairs, and the Opus drift rows of the retest arm are compared with stage
    A's on the same footing. The harness records no response headers; the §11 change adds any cache header
    the gateway returns to the calls log (`cache` field), else the report says *absent*.
11. **Mapping, not straddle.** A window's answer span is unknown; `max-overlap` picks one control chunk, and
    the cover-any descriptive bounds how often the evidence sits in another overlapping chunk. The primary
    endpoint is about the mapped chunk, so part of any confirmed effect is attributable to where the cut
    fell rather than to the judge's reading of boundary-respecting text; the cover-any share says how much.
12. **Operator exposure.** Per-arm counts are visible in the harness's logs before the report is written;
    §5 step 5 forbids reading them and requires any look to be reported. This is a rule, not a mechanism.
13. **Single run, single corpus, single judge configuration.** No replication is planned inside this record.

## 11. Code needed (small; everything else exists)

Branch from `main` in a worktree under the designated worktree directory; one PR; two independent reviews
(owner, §13); the §4.3 draw and the primary table re-run by a reviewer on a fixture. Nothing below changes an
existing subcommand's default behaviour.

| file | change |
|---|---|
| `python/scripts/eval/alias_control_chunks.py` | constants `CONFIRM_SEED = "261105\|alias\|confirm\|draw"`, `CONFIRM_BOOT_SEED = "261105\|alias\|confirm\|boot"`; `SCHEMA` entries `"draw-confirm": "alias-confirm-draw/1"`, `"confirm-report": "alias-confirm-report/1"`. |
| — `--confirmatory PREREG_SHA256` on `chunk`, `map`, `retest-seed` | `run_chunk`, `run_map` and `run_retest_seed` hard-code `"exploratory": True` and the `alias-control-*/1` schemas; the switch writes `"exploratory": false` and a `"confirmatory": {"prereg": "alias-boundary-confirm-2026-11", "prereg_sha256": …}` block into their public files (the schema strings stay: they name the file's shape, which is unchanged). `draw-confirm` and `confirm-report` write the block always. |
| — `draw_confirm(final, *, exclude_pairs, exclude_windows, n, seed) -> (picked, counts)` | the §4.1 filter (`stage == "A"`, `source == "agree"`, `label_of(f) == 1`), the §4.2 exclusions, availability per arm (distinct records with a pair in the arm), quotas by `alias_study_judge.drift_allocation(n, sizes)`, the seeded walk per arm taking a pair when its (record, arm) is free **and** its window is untaken, the shortfall chain; counts as §4.1's table plus records drawn in 1 / 2 / 3 arms and distinct windows. |
| — `run_draw_confirm(judge_config, control_draw_json, control_sample, n, out_dir)` | `asp.committed_stage_a(cfg)` (the committed final rows); refuses unless `sha256_file(control_sample) == control_draw_json["sample_sha256"]` and the stage-A commit sha256 matches; reads the control sample's pair and chunk ids; `asp.refuse_in_repo(out_dir)`; writes private `sample.jsonl` in the control's row shape (`pair_id, record_id, chunk_id, stratum = "answers", arm, in_spot_sheet = false`, so `chunk`, `map` and `retest-seed` run unchanged) and public `draw.json` by `write_summary` with the ids forbidden. |
| — `confirm_table(rows, *, boot_seed)` | from one row per `control_of` with the two final rows (or `None` when a registry holds no row): **unjudged** = `source == "unjudged"` (or `label is None`); pairwise deletion with unjudged counts by arm; the record scores S_j; `cs.sign_flip_test_exact(sums, alternative="greater")`; `cs.mcnemar_exact(b, c, "greater")`; d, the record-cluster bootstrap 90 % interval (10,000 resamples, `seed_int(CONFIRM_BOOT_SEED)`) and its upper end; the Tango score 90 % interval; per-arm Wilson; the three unjudged re-computations of §6.8 (the *unjudged = not* one through `label_of`); the cluster counts; the §8 gates with their counts. |
| — `run_confirm_report(judge_config, control_config, retest_config, control_dir, retest_dir, prereg, prereg_sha256, out, cover_config=None, write_flipped=None)` | joins the control arm by `aj.pair_id(record_id, control chunk id)` and the retest arm by the stage-A pair id; the §6 descriptives by reusing `compute_report`-style helpers, `compute_retest`'s escalation overlap, `tiebreak_replicate`, `cover_split`; the time-block split of §6.7; per-backend, per-arm first/last call UTC from the sources named in §5 step 5; the cache flag of §10.10; input shas against the committed `draw.json` / `chunk.json` / `map.json` / `retest-seed.json`; `experiment_provenance()` without host; `write_public`. `--write-flipped` writes the private control-arm *not* pair ids for `map --cover-any --pairs`. |
| — `main` | two subparsers: `draw-confirm --judge-config --control-draw --control-sample --n --out-dir`; `confirm-report --judge-config --control-config --retest-config --control-dir --retest-dir --prereg --prereg-sha256 --out [--cover-config] [--write-flipped]`; the `--confirmatory` option on `chunk`, `map`, `retest-seed`. |
| `python/scripts/eval/glossbench/glossbench_alias_judge.py` (gateway backend) | record any cache-related response header the gateway returns as a `cache` field of the calls-log row (`None` when none); no other change. If the review finds this touches the frozen study's harness in a way the alias prereg's pin forbids, the field is dropped and §10.10 reads *absent*. |
| `python/scripts/eval/alias_study_judge.py` | **no change expected**: `build-control`, `preflight`, `label`, `finalize` ran both arm shapes in the exploratory (the retest export under the control key, duplicate chunk ids with identical content accepted). |
| `python/tests/eval/test_alias_control_chunks.py` | `draw_confirm` is deterministic, excludes the control pairs and windows, honours the (record, arm) cap, the one-pair-per-window rule and the arm quotas with a shortfall on a fixture; `run_draw_confirm` refuses a control sample whose sha differs from `control-draw.json`; `confirm_table` on hand-checkable tables (every record one pair with b = 10, c = 2 → sign-flip p = McNemar p = 79 / 4096; a two-pair record with scores (+2) vs singletons; b = c → p > 0.5); **a real unjudged final row** (`label: None, source: "unjudged"`) is dropped pairwise and counted, and the three re-computations of §6.8 move as specified; every gate trips on a forged input; `confirm-report` refuses ids, text and host fields and a prereg sha mismatch; `--confirmatory` sets the flag and block. |
| `docs/plans/results/alias-boundary-confirm-2026-11/power.py`, `power.json` | this commit, from a clean tree (`README.md`). |

## 12. Cost (rates from the alias prereg §6.5 and the exploratory control; planning only, at n_draw = 370)

- **Qwen** (local, 4 in flight): C 1.85 s/pair, E 0.74 s/pair → ≈ 352 × 2 arms ≈ 704 pairs ≈ **30 min**;
  the cover-any registry ≈ 2.1 chunks per flipped pair (exploratory 85 / 40) × ≈ 13 % × 352 ≈ 95 pairs ≈
  4 min.
- **Gateway, Argon:** escalation share × 2 × n. The exploratory escalated 10.7 % (control) and 15.7 %
  (retest) of 300 → ≈ 0.13 × 704 ≈ **90 calls** (range 75–115), plus ≈ 13 for cover-any; no cap (R30).
- **Gateway, Opus:** 5 % per arm → ≈ 18 + 18 = **36 calls**, plus ≈ 5 for cover-any; capped at 7 % of each
  registry (≈ 24–25). ≈ 1 k input tokens per call, `max_tokens` 4096 / 2048.
- **Notices:** one before the main gateway batch (both arms), one before cover-any; each acknowledged in
  chat before the first call. **Owner time:** none at the bench; the go, the acks, any resume.
- **Store:** none; no collection, no retrieval; the non-production environment only.
- At 470 drawn (§13 Q4): ≈ 27 % more of each line.

## 13. Open decisions for the owner (with recommendations)

1. **Exclude the 43 shared-window pairs (§4.2)?** — Recommend yes (pool 1,871; 2 % cost; "fresh" then holds
   for passage and pair).
2. **Arm-stratified equal draw, or simple random?** — Recommend equal by arm (§4.3), the control's and the
   drift sample's rule; the test is the same either way, the estimand's mix is not.
3. **The cap.** — *Decided* (below): one pair per (record, arm).
4. **Draw size: n_draw = 370 (80 % power, recommended), 410 (.84), or every cell, 470 (.88, unequal arms, no
   slack)?** 90 % is not reachable under the sizing assumptions (§3.2–3.3). — Recommend 370, with the 80 %
   floor and the 7.0-point MDE stated in every result sentence.
5. **The sizing effect 13 % vs 6 % and ρ = 0.5.** — Recommend keeping both: 13 % is about the selected
   stratum's Wilson lower bound and 6 % is above its retest rate (§3.2); ρ = 0.5 is the conservative
   within-record assumption, and 0.3 would already make 90 % reachable at 450.
6. **Unjudged handling in the primary**: pairwise deletion with the three re-computations of §6.8. —
   Recommend as written: *unjudged = not* in a flip-to-*not* endpoint would count a gateway failure as a
   flip, so it is a sensitivity, not the primary.
7. **Cover-any after the primary (§5 step 7)?** — Recommend yes; descriptive, ≈ 95 Qwen pairs and ≈ 18
   gateway calls, and it is what makes "the evidence moved" or "the evidence vanished" sayable.
8. **Tag name** `prereg-alias-boundary-confirm-2026-11` and the freeze set of §9.1. — Recommend as written.
9. **When?** — After the §11 PR is merged and reviewed, in a gap on the Qwen endpoints, under one gateway
   notice; not interleaved with any other gateway work.
10. **The arm alternation of §5 step 5** (C: control first; E: retest first; Argon: control first; Opus:
    retest first). — Recommend as written; the alternative, a harness change to interleave blocks, is not
    worth a change to the frozen judge harness.

**Owner answers, 2026-11-05 (v1).** n = 318 at 90 % power, 334 drawn (4); one pair per record (3); pairwise
deletion of unjudged pairs with the *unjudged = not* sensitivity printed beside it (6); 1, 2, 5, 7, 8 and 9 as
recommended. Review: this pre-registration and its §11 code each get two independent reviews (Fable and an
argo-agent read) before merge. The freeze itself still needs the owner's go naming the commit (§9.2).

**Owner decision, 2026-11-05 (v2, after the two reviews), superseding the v1 answers to 3 and 4.** The cap
is **one pair per (record, arm)**, up to three pairs per question. The **primary test** is the exact
sign-flip over records on the per-record score Σ(xᵢ − yᵢ) (`chunktrial_stats.sign_flip_test_exact`), which
equals McNemar when every record gives one pair; exact McNemar stays as a pre-specified sensitivity. The
power was re-done under this design by simulation (§3.2), with a stated within-record correlation and the
draw rule's own cluster frequencies. **Under those assumptions 90 % power is not reachable within the 470
(record, arm) cells; the draw size (Q4) is open**, with 370 (80 %) recommended. Answers 1, 2, 5–9 stand; Q10
is new.

## 14. What is released

**Private, never in the repository:** `sample.jsonl`, the chunk and document files, the two seed files, both
registries and every label, call and preflight file, `PREREG.sha256`, the private config.
**Public, in this directory:** this file; `README.md`; `power.py`, `power.json`; after the draw `artifacts/draw.json`,
`chunk.json`, `map.json`, `retest-seed.json` (each with `exploratory: false` and the `confirmatory` block of
§11); after the run `artifacts/confirm-report.json` (with its `cover_any` block once step 7 ran);
`RESULTS.md`; `DEVIATIONS.md` if any entry exists. Every public JSON holds counts, rates, p, intervals, model
ids, version strings, commit shas and sha256 hashes only, passes `alias_study_common.public_problems`, and
embeds `experiment_provenance()` without `host` and `warnings`.
