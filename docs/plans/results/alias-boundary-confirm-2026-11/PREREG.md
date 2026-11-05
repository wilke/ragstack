# Pre-registration: does a boundary-respecting chunk lose *answers* labels beyond judge noise? (alias study follow-up) — DRAFT v1, 2026-11-05

**Status: DRAFT — not frozen; owner review pending.** Nothing in this text binds a run until it is frozen
by the procedure in §9. At freeze the text is frozen **as written**, draft status line included; its sha256
goes into this directory's `README.md`, a private `PREREG.sha256` and the message of a signed annotated tag.
After freeze this file is never edited; any later change is a `DEVIATIONS.md` entry (§9.3).

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
the fields that define the eligible pool (`stage`, `source`, `label`, `arm`, `record_id`, `chunk_id`), and from
the control's private sample only pair, record and window ids with stratum and arm; it opened no control,
cover-any or retest label of any pair, no chunk or record text, and no spot-check sheet. The pool count of
§4.1 is also derivable from the committed public artifact `control/artifacts/control-draw.json` alone
(`counts.available.answers` summed minus `counts.drawn.answers` summed), and the two agree.

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

**Unit.** One stage-A judged pair (record × stage-A window) from the eligible pool of §4.1.

**Two arms, both on every pair, both judged by the frozen judge at the frozen settings:**

| arm | passage judged | pair id in the registry |
|---|---|---|
| **control** (K) | the control chunk of the window's document to which the window maps by `max-overlap` (§5.2): `sentence/512/0` + `structure_cuts sections/1` + `size_tolerance 0.25` (ceiling 640 SFR tokens) + `min_piece 128`, Markdown adapter `markdown/1` raw mode, Punkt `punkt-abbrev-v1`, tokens in `Salesforce/SFR-Embedding-Mistral` — the control plan's §2 chunking, byte for byte the same product code path | `pair_id(record, control chunk)`, `control_of` = the stage-A pair id |
| **retest** (R) | the identical stage-A window (`fixed_token/512/0`), byte-identical prompt to stage A's | the stage-A pair id |

**The frozen judge** (alias prereg §6.3, D8; `main/judge-config.json` with only `out_dir` and
`chunk_exports` changed, as `alias_study_judge.control_config_problems` enforces): primary **C** =
`Qwen3.6-35B-A3B`, prompt `alias-judge/2`, thinking on, n = 5, T 0.7, `max_tokens` 8192, majority of the
valid samples; primary **E** = `Qwen3.8-27B`, same prompt, n = 1, T 0.7; C = E final, otherwise the
**tiebreaker** `Gemini 4 Argon` via the gateway (`alias-judge/2`, `max_tokens` 4096, **provider default
temperature, unrecorded**, `fallback_model ""`, ≤ 4 in flight, one re-issue of an empty or unparseable
reply, then *unjudged*); the **drift reference** `Claude Opus 5.5` (`alias-judge/1`, 1 repetition,
`max_tokens` 2048) on a seeded 5 % sample per arm, **never setting a label**, the topic-filter rule of §6.3
(one re-issue, then *no-reference*); served-model check on every backend.

**Outcomes per pair.** X = 1 when the control arm's final label is *not*, else 0. Y = 1 when the retest
arm's final label is *not*, else 0. (Every pool pair's stage-A label is *answers*, so "final label = *not*"
is the same event as "flipped".)

**The primary endpoint.** The paired contrast **P(X = 1) − P(Y = 1)** over the eligible pool at the
equal-arm mix of §4.3, read through the discordant pairs: b = #(X = 1, Y = 0), c = #(X = 0, Y = 1).

**Hypotheses.** H0: P(X = 1) ≤ P(Y = 1) (the control chunk loses *answers* no more often than the judge's
own re-read does). H1: P(X = 1) > P(Y = 1). **One-sided, direction fixed now.**

## 3. The test, and the sample size

### 3.1 The test

**Exact McNemar on the discordant pairs, one-sided.** Under H0, b ~ Binomial(b + c, ½); p = P(Binomial(b + c, ½) ≥ b),
by `chunktrial_stats.mcnemar_exact(b, c, "greater")` (integer arithmetic, already on `main`). **α = 0.05.**
The test decides; nothing else does.

**Why McNemar and not a clustered randomisation test.** Both arms are judged on the same pair, so the
contrast is paired by construction and the between-pair variation cancels. Pairs are not fully independent
(several pairs share a question, and windows share papers); §4.3 limits this at the draw (one pair per
record), and the residual paper-level dependence is a stated threat (§10), not a modelled one. A
sign-flip test over paper clusters as in part 4 is **not** run: with one pair per record and 1,063 distinct
passage papers in the pool, clusters would be nearly all singletons, and the alias prereg's choice of exact
McNemar for its own paired member (§7) is kept for the same reason.

**Beside the p (descriptive, deciding nothing):** the paired difference d = (b − c) / n in points with the
Wald interval SE = √(b + c − (b − c)² / n) / n (95 %); each arm's rate with its Wilson 95 % interval; the
2 × 2 table (both *not*, control only, retest only, neither); and the counts behind each.

### 3.2 Power, computed by committed code

`power.py` beside this file (standard library only, no RNG, exact to floating point) enumerates the
trinomial (b, c, rest) and the exact one-sided critical value for every discordant count; its output is
`power.json` (sha256 in `README.md`). **The planning effect is deliberately below the exploratory point
estimate:** control flip **13 %** against retest flip **6 %** (the exploratory *overall* rates, 13.3 % and
6.7 %, rather than the selected stratum's 18.7 % and 4.0 %). The joint distribution is set by the phi
coefficient between X and Y: **independence (φ = 0) is the sizing case** — it gives the most discordant pairs
with the smallest share in the hypothesised direction, so it is the conservative case for a fixed
difference — and positive correlation (the same borderline pairs flipping in both arms) is the sensitivity.
"Smallest n" means the smallest n at which exact power reaches the target and stays there for the next 25
values of n, so a sawtooth dip of the discrete test cannot pick a lucky n.

| scenario (pX control, pY retest, φ) | discordant share | smallest n, 80 % | smallest n, 90 % | power at n = 318 |
|---|---|---|---|---|
| **13 % vs 6 %, φ 0 (sizing case)** | .174 | **237** | **318** | **.900** |
| 13 % vs 6 %, φ 0.2 | .142 | 194 | 258 | .949 |
| 13 % vs 6 %, φ 0.4 | .110 | 150 | 197 | .986 |
| 11 % vs 6 %, φ 0 | .157 | 417 | 564 | .691 |
| 11 % vs 6 %, φ 0.2 | .127 | 339 | 455 | .776 |
| 13 % vs 8 %, φ 0 | .189 | 501 | 676 | .617 |
| 13 % vs 8 %, φ 0.2 | .153 | 406 | 549 | .701 |
| 10 % vs 6 %, φ 0 | .148 | 613 | 827 | .533 |
| *reference only, selected, not for sizing:* 18.7 % vs 4.0 %, φ 0 | .212 | 66 | 84 | 1.000 |

(`power.json` `scenarios[]`; the normal-approximation n, 218 and 302 for the sizing case, is printed beside
each exact n and is smaller because the exact test is discrete.)

### 3.3 The chosen n

- **n = 318 pairs with both final labels** (90 % power in the sizing case; `power.json` `n_chosen`).
- **Draw n_draw = 334** = ⌈318 × 1.05⌉, the 5 % oversample for pre-judging exclusions (§5.2). Every drawn
  pair that maps is judged; the realised n is whatever maps and is judged in both arms, quoted beside every
  figure.
- **The 80 % floor is n₈₀ = 237.** Below it the test is GATE-NOT-EVALUABLE (§8).
- **Minimum detectable effect** (80 % power, pY = 6 %, φ = 0): at n = 318 a control rate of **11.9 %**, an
  excess of **5.9 points**; at n₈₀ = 237, **13.0 %**, 7.0 points (`power.json` `mde`; the curve by realised
  n in steps of 10 is `mde_px_by_realised_n`, and the results quote the entry at or below the realised n).
- What the design **cannot** resolve: an excess of 4–5 points (power .53–.69 at n = 318). A null is reported
  with that floor in the sentence (§7).

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
| distinct records (questions) in the 1,871 | 263 (base 198 / neighbour 154 / random 118 records yield one pair each under the cap of §4.3) |

A pair's **arm** is the arm group its passage paper belongs to (base / neighbour / random), as the drift
and spot-check strata define it (alias prereg §6.2). The pool is not re-derived from anything but the
committed registry's final rows; no retrieval, no new stage-A label.

### 4.2 Exclusions fixed before the draw

1. The 300 control pairs (all strata), by pair id.
2. The 43 pool pairs whose stage-A window is a control pair's window under another record. Their control
   chunk already exists and was judged against another question; excluding them makes "fresh" hold for the
   passage as well as the pair, at 2 % of the pool. (Owner decision Q1, §12.)
3. Nothing else. In particular, pairs whose **record** has a control pair (136 of 263 records) stay: the
   control judged a different window of that question, and no label of it is read.

### 4.3 The draw rule

- **Stratified by arm, equal across base / neighbour / random**, the remainder to base, then neighbour
  (`alias_study_judge.drift_allocation`, the rule the control's draw and the drift sample use): 334 →
  **112 / 111 / 111**. Equal arms keep the mix the exploratory *answers* stratum had (50 / 50 / 50), so the
  estimand is read at the same composition; the by-arm rates are secondary descriptives (§6). Simple random
  sampling would weight the arms 40 / 26 / 34 and would not change the test, but would make the estimand a
  pool-weighted one that no earlier figure was read at. (Owner decision Q2.)
- **One pair per record** (per question): within an arm, pairs are visited in ascending order of
  `sha256(261105|alias|confirm|draw|<arm>|<pair id>)` and a pair is taken only when no pair of its record has
  been taken; the walk stops at the arm's quota. This removes the within-question dependence that would
  otherwise be the largest violation of McNemar's independence (the pool holds up to 51 pairs of one
  record). Capped availability is 198 / 154 / 118 records, above every quota; a short arm's shortfall would
  go to the other arms as `drift_allocation` does. (Owner decision Q3: cap 1, or cap 2 with 335 / 254 / 205
  available.)
- **Seed string `261105|alias|confirm|draw`** (new; the project's `<yymmdd>|<study>|<purpose>` pattern,
  alias prereg §5.5). The judge's labelling order stays the frozen `261101|alias|judge|order`, and the
  drift sample stays `261101|alias|drift|<batch>` (one batch of ≤ 2,000: 5 % of 318 rounded half up = 16,
  split 6 / 5 / 5 by arm), because the judge configuration is the frozen one.
- **Run by committed code before any judging** (`draw-confirm`, §11), after the freeze, from a clean
  checkout: it refuses unless the stage-A commit sha256 and the control sample's sha256 are the two above.
  Its private output `sample.jsonl` (pair, record, window ids; arm; stratum *answers*) stays off the
  repository; its public `draw.json` (the counts of the table above as recounted, the per-arm availability
  and quotas, the seed, the sha256 of the private sample and of the inputs, provenance) is committed
  **before** `chunk` runs. The draw is run by the operator; the analyst does not see pair ids.

## 5. Procedure (every step gated; nothing judged before step 4)

0. **Freeze** (§9.1). Then, in a clean checkout at the frozen commit (`dirty false`, `untracked 0`,
   `citable true` in every artifact's provenance), with `PYTHONPATH=<checkout>/python`, the Punkt
   segmenter (`punkt-abbrev-v1`), the `[markdown]` extra and the SFR tokenizer present
   (`alias_control_chunks.require_env` refuses otherwise):
1. **`draw-confirm`** (§4.3) → private `sample.jsonl`, public `draw.json`. *Gate:* the two input shas verify;
   no drawn pair is a control pair; counts as expected; `draw.json` committed.
2. **`chunk`** on the sampled documents (unchanged subcommand): record text resolved for every document by
   reproducing **every** stage-A chunk of it (`text[start:end] == content`); the control chunks with the P2
   invariants measured (no fragment of two segments, sentence or segment boundaries only, ≤ 640 tokens,
   tiling). → private `chunks-control.jsonl`, `docs.jsonl`; public `chunk.json`. *Gate:* `p2_gate` passes;
   documents excluded as `no_text` / `no_match` / `ambiguous` counted.
   **`map`** (unchanged): each window's offsets checked, then `max-overlap` (largest character overlap, ties
   to the earlier chunk), coverage and purity recorded. → private `pairs-seed.jsonl`; public `map.json`.
   **`retest-seed`** (unchanged): the same pairs re-pointed at their own stage-A windows, the windows as an
   export → private `chunks-retest.jsonl`, `pairs-seed-retest.jsonl`; public `retest-seed.json`.
   *Gate:* the mapped count ≥ 237 (else stop and record; §8); exclusions counted by reason; the three public
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
   the registry (alias prereg §6.5; ≈ 22 of 318, 16 expected); no Argon cap (R30).
5. **`label`**, per backend with the two arms back to back so each backend sees both arms as close in time
   as the harness allows: C control → C retest → E control → E retest → Argon control → Argon retest → Opus
   control → Opus retest. Then `finalize` both. **Hard stop** on any provider notice (`provider_notice`,
   ACCESS DENIED, a served-model mismatch, the budget stop, the arm-gap pause of §6.3): the run halts, the
   state is reported, and **the owner's word in chat is required to resume**; a resume under the same frozen
   design is recorded, a change is a deviation. Finishing without Opus after a notice is a separate owner
   decision, reported as such (CONTROL-PLAN Q6).
   **Timestamps:** the first and last call time (UTC) of every backend in every arm go into the public
   report (§11). The exploratory took these from private logs; here they are an artifact field.
6. **`confirm-report`** (§11), run **once**, after both arms are finalized, before any label is looked at by
   a person: the primary table, p, d, intervals, counts, unjudged counts, timestamps, shas, provenance, this
   prereg's sha256 → public `confirm-report.json`, committed. *Gate:* `public_problems` clean (no id, text,
   host); the frozen sha matches.
7. **Cover-any** (descriptive, §6): only after step 6 is committed, `map --cover-any` over the control-arm
   *not* pairs, a third registry, the same gates, a second gateway notice; then `confirm-report` again with
   `--cover-config` adding the `cover_any` block. The primary numbers of step 6 are not recomputed.
8. `RESULTS.md` in this directory, with the decision sentence of §7 verbatim.

## 6. Secondary and descriptive quantities (reported, **not tested**)

All with counts and Wilson 95 % intervals where a rate is shown; **no p value but the primary's is
computed, and no quantity below decides anything.**

1. **By arm:** X and Y rates and the discordant counts per arm (base / neighbour / random).
2. **Cover-any recovery** on the control arm's *not* pairs: the share for which some other control chunk
   overlapping the window is labelled *answers* (`recovered_by_other_chunk`), the four-way split
   (other only / both / primary only / none), and the primary chunk's same-bytes re-judge agreement on those
   selected pairs.
3. **Escalation:** C-alone and E-alone *answers* rates and the escalation share per arm; the overlap of the
   escalated sets between the arms (Jaccard) and, for pairs escalated in both arms, the Argon verdict
   agreement — **not** a byte-identical replicate here, since the prompts differ between arms; the
   byte-identical replicate is the retest arm's Argon rows against stage A's (`tiebreak_replicate`).
4. **Opus drift reference:** agreement between each arm's final label and Opus on the 5 % sample, overall
   and per arm, the *no-reference* count. The arm-gap pause of §6.3 cannot trigger (it needs ≥ 100
   Opus-answered pairs per arm); the reference lines are printed, not gated.
5. **Boundary diagnostics** of the sampled windows and control chunks, as the exploratory artifact's
   `metrics.boundary`; X and Y by window-coverage band and control-chunk token band; the LaTeX-preamble and
   one-untitled-section strata.
6. **Sensitivity of the primary to unjudged pairs** (the same test, recomputed, printed beside, deciding
   nothing): the primary drops any pair without a final row in **either** arm (pairwise deletion, counted);
   the sensitivity scores an unjudged pair as *not* in that arm, the alias prereg's primary-analysis
   convention.
7. **Timing:** the per-backend, per-arm first and last call times, and the gap between the two arms' gateway
   batches.

**Explicitly not done:** any test by arm, route, band or stratum; any test of the cover-any share; any
comparison with the exploratory 300; any *not → answers* figure (none can exist in this pool); any
retrieval figure; any change to any alias-study number.

## 7. Decision rules and the wording of each outcome

Let n be the pairs with a final row in both arms, b and c as in §2, p the one-sided exact McNemar p.

| outcome | rule | the sentence (filled from `confirm-report.json`) |
|---|---|---|
| **Confirmed** | gates pass (§8) **and** p ≤ 0.05 (so b > c) | "Confirmed: on n fresh stage-A pairs whose two Qwen judges had agreed the 512-token window *answers*, the frozen judge labelled the boundary-respecting control chunk *not* on b pairs where it kept *answers* on the identical window re-read, against c the other way (exact one-sided McNemar p = …; control X %, retest Y %, paired difference d points, Wald 95 % …). The boundary-respecting cut loses *answers* labels beyond the judge's own test-retest noise, at this chunker setting, under single-passage judging." |
| **Not confirmed (powered null)** | gates pass **and** p > 0.05 | "Not confirmed: on n fresh pairs the control chunk was labelled *not* on b pairs against c for the identical window (p = …; control X %, retest Y %, difference d points, 95 % …). At n the design had 80 % power for an excess of E points (control 13 % vs retest 6 %, independent discordance; `mde_px_by_realised_n`), so an excess of that size or larger is unlikely to have been missed; smaller excesses are not resolved by this test. The exploratory 18.7 %-vs-4.0 % contrast was not reproduced at this size." *Never* "no difference", *never* "the chunking has no effect". |
| **GATE-NOT-EVALUABLE** | any §8 gate fails | "GATE-NOT-EVALUABLE: <the gate, with its count>. The test was computed (p = …, n = …) and decides nothing." The table is still printed in full. |

The sentence always carries: the realised n, b and c, p, both arm rates, the MDE at the realised n, the
word *exploratory* for any figure quoted from the earlier control, and *judge incomplete* when unjudged
pairs exceed 1 % of mapped pairs in either arm (alias prereg §6.3's rule, carried over).

## 8. Gates (a third answer, preserved in every table)

The result is **GATE-NOT-EVALUABLE** when any of these holds; a gate is read before the p is read, by the
report, not by a person:

1. **Realised n < 237** (n₈₀): pairs with a final row in both arms after the exclusions of §5.2 and the
   unjudged of §6.6. With 334 drawn, this needs a 29 % loss — a harness failure, not noise.
2. **Unjudged > 5 %** of mapped pairs in either arm (1–5 %: the *judge incomplete* caveat, not a gate).
3. **The two registries do not hold the same `control_of` set**, or a drawn pair is a control pair, or the
   stage-A commit sha256 or the control sample sha256 differs from §4.1.
4. **The chunker gate fails** (`chunk.json` `p2_gate` false) or the environment check fails (segmenter ≠
   `punkt-abbrev-v1`, adapter ≠ `markdown/1`, `[markdown]` absent, SFR tokenizer absent).
5. **An arm is incomplete after a hard stop** (§5.5) and the owner did not resume under the same frozen
   design; a served-model mismatch stored no reply, by the harness's rule.
6. **The report's input shas** (both registries' `pairs-manifest.json` `inputs_sha256`, the seed files, the
   chunk exports) do not match the committed `draw.json` / `chunk.json` / `map.json` / `retest-seed.json`.

A gate that fails is named with its count; *absent* is written *absent*, never left blank.

## 9. What is frozen, how, and the deviations policy

### 9.1 Frozen before data (the freeze set)

This text; `power.py` and `power.json` (re-run at the freeze commit: byte-identical but for the provenance
block); the endpoint, direction, test, α, n = 318, n_draw = 334, n₈₀ = 237; the pool definition and the
two exclusions (§4.1–4.2); the draw rule, the per-record cap and the seed string (§4.3); the control
chunking (the control plan's §2 markers, `chunk_descriptor`
`sentence/512/0/{"min_piece":128,"pack":"318","size_tolerance":0.25,"size_unit":"tokens","structure_cuts":"sections/1"}`);
the `max-overlap` mapping; the judge configuration (the main judge config byte for byte but for `out_dir`
and `chunk_exports`); the step order of §5; the gates of §8; the wording of §7; the code of §11, merged
and reviewed, at one `main` commit that the freeze names.

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
   public report carries the frozen sha.

### 9.3 Deviations

After freeze, any change to the endpoint, direction, test, α, n, pool, exclusion, draw, seed, chunker
setting, mapping, judge setting, step order, gate or wording is a dated entry in `DEVIATIONS.md` in this
directory, stating what changed, why, when, what had been seen at that time and which result it touches; a
result whose computation depends on it says so in its sentence. A resume after a hard stop under the same
design is recorded in the report (`run_segments`), not a deviation; any change made while stopped is one.
Re-running the draw, for any reason, is a deviation with a new seed string.

## 10. Threats to validity and limitations

1. **The judge is not truth, and it is the same judge in both arms.** A systematic preference of this judge
   for or against boundary-respecting text would show up as a confirmed result that another judge would not
   reproduce. The exploratory spot check found the judge and the owner disagreeing mostly on partial
   answers (CONTROL-PLAN Q4); a control chunk that holds only part of what the window held is exactly where
   that line matters. The Opus drift sample is the only outside reading, at 5 % and never a label.
2. **Judge non-determinism is the null, and it is not stationary.** The retest arm measures the judge's own
   flip rate at the time of the run; the exploratory retest flipped 4.0 % of this stratum and 6.7 % overall.
   The gateway models run at the provider's default temperature, which is **unrecorded** (alias prereg
   §6.3; ADDENDUM *Limitations*). The two arms are judged back to back per backend (§5.5) and their call
   times are recorded, so a drift between arms is visible, not controlled.
3. **Dependence between pairs.** One pair per record removes within-question dependence; pairs still share
   passage papers (1,063 distinct in the pool) and documents. McNemar treats pairs as independent; the
   realised number of distinct papers and the largest cluster are reported. The test is not clustered (§3.1).
4. **The pool comes from the same study** whose exploratory control motivated the test. The pairs are
   fresh, but the population (OA-1000 and its neighbour and random arms, stage-A retrieval outputs, the
   records of §4 of the alias prereg) is the one the pattern was first seen in. The result generalises no
   further than that population.
5. **One chunker setting**, the control plan's: `sentence/512/0` with section cuts at the 640 ceiling.
   Nothing here speaks to other sizes, overlaps or an untitled-section adapter (PR 5b) or PDF headings
   (PR 8); the one-untitled-section share is reported (25.3 % of the exploratory 300).
6. **A reach metric read on a label.** The outcome is "a single passage, read alone, states the core of the
   answer". It is the alias study's found@10 reading (CONTROL-PLAN §9), not answer quality under synthesis.
   A confirmed result says the *label* moves with the cut; it does not say retrieval would.
7. **Only the *answers → not* direction** on agreed pairs is tested. The exploratory saw no *not → answers*
   on agreed-*not* pairs (0 / 99) and nothing on tiebroken pairs; both are untested here.
8. **Mapping, not straddle.** A window's answer span is unknown; `max-overlap` picks one control chunk, and
   the cover-any descriptive bounds how often the evidence sits in another overlapping chunk. The primary
   endpoint is about the mapped chunk, so part of any confirmed effect is attributable to where the cut
   fell rather than to the judge's reading of boundary-respecting text; the cover-any share says how much.
9. **Single run, single corpus, single judge configuration.** No replication is planned inside this record.

## 11. Code needed (small; everything else exists)

Branch from `main` in a worktree under the designated worktree directory; one PR; Fable review; the §4.3
draw and the primary table re-run by the reviewer on a fixture. Nothing below changes an existing
subcommand's behaviour.

| file | change |
|---|---|
| `python/scripts/eval/alias_control_chunks.py` | constants `CONFIRM_SEED = "261105\|alias\|confirm\|draw"`, `CONFIRM_N_DRAW`, `CONFIRM_RECORD_CAP = 1`; `SCHEMA` entries `"draw-confirm": "alias-confirm-draw/1"`, `"confirm-report": "alias-confirm-report/1"`. |
| — `draw_confirm(final, *, exclude_pairs, exclude_windows, n, record_cap) -> (picked, counts)` | the §4.1 filter (`stage == "A"`, `source == "agree"`, `label_of(f) == 1`), the §4.2 exclusions, capped availability per arm (distinct records under the cap), quotas by `alias_study_judge.drift_allocation(n, sizes)`, the seeded walk with the per-record cap, the shortfall chain; counts as §4.1's table. |
| — `run_draw_confirm(judge_config, control_draw_json, control_sample, n, out_dir)` | `asp.committed_stage_a(cfg)` (the committed final rows); refuses unless `sha256_file(control_sample) == control_draw_json["sample_sha256"]` and the stage-A commit sha256 matches; reads the control sample's pair and chunk ids; `asp.refuse_in_repo(out_dir)`; writes private `sample.jsonl` in the control's row shape (`pair_id, record_id, chunk_id, stratum = "answers", arm, in_spot_sheet = false`, so `chunk`, `map` and `retest-seed` run unchanged) and public `draw.json` by `write_summary` with the ids forbidden. |
| — `confirm_table(rows)` | from one row per `control_of` with the two final rows (or `None`): n, b, c, both, neither, unjudged per arm, `cs.mcnemar_exact(b, c, "greater")`, d and its Wald interval, per-arm Wilson, the §6.6 sensitivity, the §8 gates with their counts. |
| — `run_confirm_report(judge_config, control_config, retest_config, control_dir, retest_dir, prereg, prereg_sha256, out, cover_config=None, write_flipped=None)` | joins the control arm by `aj.pair_id(record_id, control chunk id)` and the retest arm by the stage-A pair id; the §6 descriptives by reusing `compute_report`-style helpers, `compute_retest`'s escalation overlap, `tiebreak_replicate`, `cover_split`; per-backend, per-arm first/last call UTC from the calls logs; input shas against the committed `draw.json` / `chunk.json` / `map.json` / `retest-seed.json`; `experiment_provenance()` without host; `write_public`. `--write-flipped` writes the private control-arm *not* pair ids for `map --cover-any --pairs`. |
| — `main` | two subparsers: `draw-confirm --judge-config --control-draw --control-sample --n --out-dir`; `confirm-report --judge-config --control-config --retest-config --control-dir --retest-dir --prereg --prereg-sha256 --out [--cover-config] [--write-flipped]`. |
| `python/scripts/eval/alias_study_judge.py` | **no change expected**: `build-control`, `preflight`, `label`, `finalize` ran both arm shapes in the exploratory (the retest export under the control key, duplicate chunk ids with identical content accepted). |
| `python/tests/eval/test_alias_control_chunks.py` | `draw_confirm` is deterministic, excludes the control pairs and windows, honours the record cap and the arm quotas with a shortfall on a fixture; `run_draw_confirm` refuses a control sample whose sha differs from `control-draw.json`; `confirm_table` on hand-checkable tables (b = 10, c = 2 → p = 79 / 4096; b = c → p > 0.5; unjudged dropped and counted; the sensitivity); every gate trips on a forged input; `confirm-report` refuses ids, text and host fields and a prereg sha mismatch. |
| `docs/plans/results/alias-boundary-confirm-2026-11/power.py`, `power.json` | this commit; re-run at the freeze commit. |

## 12. Cost (rates from the alias prereg §6.5 and the exploratory control; planning only)

- **Qwen** (local, 4 in flight): C 1.85 s/pair, E 0.74 s/pair → 334 × 2 arms = 668 pairs ≈ **29 min**; the
  cover-any registry ≈ 2.1 chunks per flipped pair (exploratory 85 / 40) × ≈ 13 % × 318 ≈ 90 pairs ≈ 4 min.
- **Gateway, Argon:** escalation share × 2 × n. The exploratory escalated 10.7 % (control) and 15.7 %
  (retest) of 300 → ≈ 0.13 × 668 ≈ **85 calls** (range 70–110), plus ≈ 12 for cover-any; no cap (R30).
- **Gateway, Opus:** 5 % per arm → 16 + 16 = **32 calls**, plus ≈ 5 for cover-any; capped at 7 % of each
  registry (≈ 22). ≈ 1 k input tokens per call, `max_tokens` 4096 / 2048.
- **Notices:** one before the main gateway batch (both arms), one before cover-any; each acknowledged in
  chat before the first call. **Owner time:** none at the bench; the go, the acks, any resume.
- **Store:** none; no collection, no retrieval; the non-production environment only.

## 13. Open decisions for the owner (with recommendations)

1. **Exclude the 43 shared-window pairs (§4.2)?** — Recommend yes (pool 1,871; 2 % cost; "fresh" then holds
   for passage and pair).
2. **Arm-stratified equal draw, or simple random?** — Recommend equal by arm (§4.3), the control's and the
   drift sample's rule; the test is the same either way, the estimand's mix is not.
3. **One pair per record (cap 1), or cap 2?** — Recommend cap 1 (availability 198 / 154 / 118 against
   quotas 112 / 111 / 111). Cap 2 doubles the slack (335 / 254 / 205) at the price of within-question
   dependence in up to half the sample.
4. **n = 318 (90 %), or n = 237 (80 %)?** — Recommend 318: the extra 81 pairs cost ≈ 7 min of Qwen and
   ≈ 25 gateway calls, and the 80 % floor then survives a 25 % loss.
5. **The sizing effect 13 % vs 6 %.** — Recommend keeping it: the overall exploratory rates, below the
   selected stratum's. A more conservative 11 % vs 6 % needs 564 pairs for 90 % (417 for 80 %); the pool
   (470 under cap 1) does not reach 564, and 417 under cap 1 leaves no oversample in the random arm (118).
6. **Unjudged handling in the primary**: pairwise deletion with the *unjudged = not* sensitivity (§6.6), or
   the alias prereg's *unjudged = not* as primary? — Recommend pairwise deletion: *unjudged = not* in a
   flip-to-*not* endpoint would count a gateway failure as a flip.
7. **Cover-any after the primary (§5.7)?** — Recommend yes; descriptive, ≈ 90 Qwen pairs and ≈ 17 gateway
   calls, and it is what makes "the evidence moved" or "the evidence vanished" sayable.
8. **Tag name** `prereg-alias-boundary-confirm-2026-11` and the freeze set of §9.1. — Recommend as written.
9. **When?** — After the §11 PR is merged and reviewed, in a gap on the Qwen endpoints, under one gateway
   notice; not interleaved with any other gateway work.

**Owner answers, 2026-11-05.** n = 318 at 90 % power, 334 drawn (4); one pair per record (3); pairwise
deletion of unjudged pairs with the *unjudged = not* sensitivity printed beside it (6); 1, 2, 5, 7, 8 and 9 as
recommended. Review: this pre-registration and its §11 code each get two independent reviews (Fable and an
argo-agent read) before merge. The freeze itself still needs the owner's go naming the commit (§9.2).

## 14. What is released

**Private, never in the repository:** `sample.jsonl`, the chunk and document files, the two seed files, both
registries and every label, call and preflight file, `PREREG.sha256`, the private config.
**Public, in this directory:** this file; `README.md`; `power.py`, `power.json`; after the draw `artifacts/draw.json`,
`chunk.json`, `map.json`, `retest-seed.json`; after the run `artifacts/confirm-report.json` (with its
`cover_any` block once step 7 ran); `RESULTS.md`; `DEVIATIONS.md` if any entry exists. Every public JSON
holds counts, rates, p, intervals, model ids, version strings, commit shas and sha256 hashes only, passes
`alias_study_common.public_problems`, and embeds `experiment_provenance()` without `host` and `warnings`.
