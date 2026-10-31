# Deviations from the frozen pre-registration: glossary attachment benchmark, round 3

The pre-registration is frozen at commit a27b581 (`prereg/PREREG-glossbench-round3.md`, sha256
`43637555d234922323655ca6f51216ca08c39596422f53d2d5437419f10d3765`, frozen 2026-10-30T15:34:08Z). The file
is never edited. Every change after the freeze is recorded here, dated, with what was known when it was
made. The partner that holds the papers-OC collection is "the papers-OC owner". Sets keep the prereg's
names: **O** (OA-1000), **A** (ASM-1000), **P** (papers-OC).

## D-1, 2026-10-30: the set-P pilot's yield; P is stopped for round 3 and is not promoted

**What the frozen text says.** §3.4: the pilot runs first, with 100 records from 5 papers in fields mode,
layers 1–3, the adapter, and the papers-OC owner's verdicts on all 100. §3.5: P is promoted into F only if
the Wilson 95 % upper bound of the judged pilot error rate is ≤ 0.15 on **≥ 30 judged** records.
Otherwise P stays a replication, "with no further option". §3's eval-set table and §11 D5 set P at
25 papers × (6 + 6) = 300 jobs.
§3.2 assumes 1.3 attempts per accepted record.

**What happened.** The pilot drafted on 2026-10-30, 15:51–15:57Z, at `6e620c2` (clean, `citable: true`):

- **100 jobs over 5 papers, 105 drafter calls.** That is 100 first attempts and 5 redraws, on Gemini
  2.5 Pro through the papers-OC owner's gateway. The gateway counted **94,614 prompt / 2,185 completion
  tokens**. All 105 calls finished `stop` on the first HTTP attempt: **0 empty**, 0 truncated, 0 rate
  limited.
- **88 of the 100 first attempts answered "no such quantity"** (`{"none": true}`). Counting the redraws,
  89 of 105 drafts did (M_num 47, V 42). Such answers are exempt from redraw by design.
- **Layer 1 passed 7 of the 100 jobs** (V 4, M_num 3). Over the 105 drafts, the 9 rejects other than
  "no such quantity" were: lexical leak 4; quantity not in quote 2; quantity not in quote and unit
  incompatible 3 (7 + 89 + 9 = 105).
- **Verification (Claude CLI, layer 2) verified 6 of the 7.** 4 passed (V 2, M_num 2) and 2 were rejected
  as a single reading. The seventh record failed on all 4 calls made for it (2 in the run, 2 in a
  diagnostic resume that captured the error): the CLI's usage-policy safeguards refused it, which is
  deterministic and not a usage limit. The code had no branch for a refusal, so verify paused for good.
  In this run at `6e620c2`, the summary and the partner export did not run.

**Cause.** In fields mode, a (chunk, field) job paired a field with a chunk **by quota, not by content**.
§3.3's arm-blind lexical prefilter admits a chunk if it names *some* candidate binding field. The job then
assigned the chunk a field from the quota, and that field need not be one the chunk names. **41 of the 88
"no such quantity" jobs' chunks name none of the assigned field's terms** under round 2's term match
(exact, at word boundaries, after NFKC and casefold). Under #878's stemmed word-set match (D-2 item 1) the
count is 36. The drafter's answer was correct: those chunks hold no such quantity.

**Decision (owner, 2026-10-30).**

- **P is stopped for round 3.** No non-pilot P record is drafted. The pilot's yield is P's recorded result.
- **Promotion fails by the frozen rule.** At most 7 records could be judged, which is fewer than the 30
  that §3.5 requires. P stays a replication. The outcome is **not promoted**, and no verdict is needed to
  establish it.
- **A second pilot would not reopen promotion.** §3.5 allows "no further option", and a pilot run after
  the fix of D-2 would be a different draw under different code.
- **The pilot's 7 layer-1 passes go to the papers-OC owner as a format check of the adapter only.** They
  are never pooled with any set, and they are data for no hypothesis.

§3.5 names a form for the promotion outcome: a dated README section quoting n, rejects, seed and the
Wilson bound. That form presupposes imported pilot verdicts (§3.5's "When": after the pilot's verdicts are
imported), and none had been imported when P was stopped. The outcome is recorded here instead, and
`prereg/README.md` is not edited.

**What was known.** The pilot's drafting, layer-1 and layer-2 counts above. No non-pilot record had been
drafted on any set, and no retrieval, swap or analysis had run. The pilot's papers are excluded from P's
sample by §3.1 step 2 in any case.

**Effect on the design.** F stays at the three O hypotheses (§2), with Holm over 3; the six-member family
of §3.5 does not arise. Round 3 has no P leg beyond the pilot's counts. R1, the papers-OC owner's
155-record set, is unaffected.

## D-2, 2026-10-30: drafting-pipeline changes after the freeze (#878, merge `b501ec7`)

**What the frozen text says.** §9 pins the drafting code as reviewed on `main` before the freeze. §3.1
step 5 and §3.3 describe P's fields-mode build through round 2's arm-blind lexical prefilter, with a
dry-run gate reading of 80. §3.2 names the Claude CLI as the layer-2 verifier.

**What changed** (#878, reviewed by Opus in two passes at `1a03f23` and `6dae40c`; its review note
records that the Fable review was cut short when credits ran out). Each change is listed with why it was
made:

1. **Field assignment in fields mode.** A (chunk, field) job is now eligible only if the chunk prints a
   **number with a dimension-compatible unit** for the field (related dimensions count). A count or
   dimensionless field is eligible on any printed number with no incompatible unit. **Terms only rank**:
   chunks that name the field's terms are drawn first, and within a rank the seeded order is kept.
   `term_in_chunk` is recorded per job, and `field_prefilter`, `field_candidates` and `field_terms` are
   recorded per row.
   - *Why not a term gate.* The papers-OC owner argued that requiring term presence would drop the
     symbol-only gold passages, where a quantity is printed only as a symbol and a number. Those are the
     passages a glossary exists to reach, so a term gate would **bias the glossary effect toward zero**.
     The owner's first estimate of how often this happens was later corrected to about **1 in 60** gold
     records (external, the papers-OC owner's measurement). The bias argument holds at any non-zero rate.
   - *Known limitation.* The dimensionless and count fields admit every chunk that prints a number. On P's
     build they account for **178 of 300** jobs, so the eligibility rule does little for them.
2. **Pre-drafting yield estimate.** `expected_yield` and `yield_gate` (0.5) run offline before any
   drafting. Below the gate the pool is `YIELD-NOT-ADEQUATE`, and `draft` refuses unless given
   `--allow-low-yield`, which is recorded. In fields mode the rates per job class come from a calibration
   that the new `backtest` subcommand derives from a drafted pilot.
   - On the pilot, a leave-one-paper-out backtest predicts **6.9 %** against the **7 %** observed.
   - P's pool reads **0.102** calibrated, which is **YIELD-NOT-ADEQUATE**. That agrees with D-1's stop,
     which was decided on the pilot itself.
3. **Yield watch.** After 100 jobs, a layer-1 pass rate below **0.30** makes `draft` pause with exit 75.
   Resuming needs `--accept-low-yield`, which is recorded. This applies to round-3 pools only.
4. **Fallback verifier on a usage-policy refusal.** A refusal by the Claude CLI is not treated as a limit
   or an error. That record is verified by the Codex CLI instead, with the same prompt, the same contract
   and the same empty working directory, one call at a time, and its own isolation check.
   - Every record carries `verifier` and `verifier_refused`.
   - **A refusal is not a record defect**, so a refused record is not rejected for being refused.
   - If Codex also fails, the record is `verifier_unavailable` and excluded, not rejected.
   - So layer 2 can now have two verifiers in one set, and every figure that depends on layer 2 can be
     split by `verifier`.
5. **Negative control.** The pilot's **88** first-attempt "no such quantity" jobs are kept as a
   negative-control file. It holds ids only and is private. It is not data for any hypothesis.

**What was known.** Everything in D-1. The eligibility rule, the yield calibration and the refusal path
were designed after the pilot's counts were seen, and the calibration is fitted to them. No non-pilot
record had been drafted on any set.

**Effect on the design.**
- §3.1 step 5's and §3.3's description of P's build (the prefilter, the 80 reading) no longer matches the
  code. Because P is stopped (D-1), no round-3 P sample is drawn under either description.
- Items 1 and 5 touch fields mode only, so they do not touch O or A.
- Items 2–4 apply to O and A. The yield gate and the watch can stop drafting but cannot change an accepted
  record. The fallback verifier changes *who* verifies a refused record, not the contract it is verified
  against.
- No hypothesis, arm, measure, threshold or test changes.

## D-3, 2026-10-30: O and A proceed under the yield watch

**Decision (owner, 2026-10-30).** Sets O and A are drafted as pre-registered (§3.1, §3.2), at or after
`b501ec7`, with the yield watch of D-2 item 3 active and the fallback verifier of D-2 item 4 in place. If
the watch pauses a set, that pause is a new entry here, and so is any `--accept-low-yield` resume.

**What was known.** D-1 and D-2. No O or A record had been drafted, and no retrieval had run on any set.

**Effect on the design.** None on any hypothesis, arm, measure or threshold. O still carries F, and A is
still descriptive only.

## D-4, 2026-10-30T17:50:56Z: the pilot's verification rerun, the partner's format check, and layer-1 changes (#880, merge `1627f9d`)

**The verification rerun.** Resuming the pilot's paused `verify` at `b501ec7` was refused, correctly, by
the run-state commit check: the run had started at `6e620c2`. Verification was restarted fresh, using the
code's own route (a new output directory, with the pool and draft files copied byte-identical; no state
file was edited). The first attempt's outputs are kept unchanged. Of the 7 layer-1 passes:
- 4 passed the Claude CLI verifier;
- 2 were rejected for single reading;
- 1 was refused by Claude's usage-policy safeguard and passed by the Codex fallback.

The 5 passes were exported to the papers-OC owner as a format check only. Each record carries a
provenance note: drafted before #878, not from the population of any later set, never pooled.

**The partner's format check.**
- The partner's scripted checks passed all 5 records. Its hand judgement rejected 2: one quote gives a
  regulatory threshold for the wrong quantity ("wrong field"; the question and the quote agree, and only
  the field's meaning is wrong), and one gives an open bound ("not a value").
- Our `import-partner-verdicts` ran end to end on the partner's file and computed the label
  "scripted-clean, judged sample of 5, error rate 40.0% (95% CI 11.8–76.9)". These figures are not used
  for anything: n = 5, and the records come from the replaced sampler.
- The check also found that the export carried no verifier provenance.

**What changed (#880, merge `1627f9d`).**
1. Every exported record now carries `verifier`, `verifier_refused` and `verifier_model`.
2. A new layer-1 reject, `value_is_bound`, applies when the record's `value` field (never the quote)
   expresses a bound rather than a value. That covers a bound word in the 3 tokens before the number, a
   trailing "or more" or similar, count phrases such as "fewer than", and a bound symbol directly before
   the number. Ranges pass.
   - Two fields are exempt (`bound_ok_fields`), because their definitions are themselves a target or a
     ceiling: `clearance_fraction` and `risk_tolerance`.
   - The rule applies to every record in open mode, so it changes which O and A records pass layer 1.
   - Disabling it (`value_bound: off`) is recorded in the acceptance summary and logged as a warning.
3. A new layer-1 flag (not a reject), `empty_case`, is set on every empty `case` in fields mode.
4. `export-partner` and `import-partner-verdicts` now refuse to write any path the run itself writes.
   The partner's verdict file sat under exactly the import's output name.

**Effect on the pilot.** Applied to the 16 of the pilot's 105 drafts that carry a `value`, `value_is_bound`
would have rejected 3, including the record the partner rejected as "not a value".

**What was known.** The pilot's 5 verdicts. No O or A record had been drafted, and no retrieval had run on
any round-3 set. The rule is in force for every O, A and later P record. The pilot keeps its layer-1
verdicts as logged, and is not re-scored.

## D-5, 2026-10-30T21:58:27Z: set O's discriminating records judged in full; per-hypothesis clean-subset sensitivities, declared before any retrieval

**What happened.**
- Set O drafting finished (17:57–19:10Z, 863 gateway calls) with **206 accepted records** (V 57, M_num 149).
  Of these, only **53** sit on chunks whose rendered field differs between the `value` and `mention`
  attachment rules (V 17, M_num 36). The design planned about 150. Only on these 53 does the gold chunk
  itself render differently between the arms; distractor fields can differ on any record. The frozen pre-drafting gate counted sampled chunks (151 against 100) and passed; the
  shortfall arises between sampling and acceptance.
- On the owner's decision, the papers-OC owner judged **all 53** of these records, a census rather than a
  sample, on one question: does the quoted value denote the quantity the question asks about? The judge
  was told only that these are the discriminating records, not which arm renders what.

**Result** (exact counts, with no sampling uncertainty):
- **2 wrong quantity** (V 1, M_num 1), rejected by the partner with a reason.
- **2 worded values** (M_num 2), rejected by the partner with no reason recorded in its files; their
  quotes print no digit. Under the frozen definition the M_num property (a symbol plus a number)
  applies to the chunk, not the quote, so these records do not violate the frozen rules.
- **8 not a quantity** (V 2, M_num 6). These are internally consistent, so the judge accepted them, but their "quantity"
  is an equation number, a supplementary-note reference, a database accession, a definitional constant,
  or a number read out of a name.
- **41 clean** (V 14, M_num 27).

The partner's verdict object (n 53, 4 rejects: the wrong-quantity and worded records) and notes are
private, with sha256s `f5183e80…` and `ea3cfa5c…`.

**Decision (owner, before any retrieval on any round-3 set).**
- H1–H3 run exactly as frozen: H1 on every M_num record (the 36 discriminating M_num records among
  them), H2 on V (the 17), H3 on both. Each hypothesis has its stratum's census breakdown printed beside
  it, alongside the realised power floor.
- **Exploratory sensitivities** are declared now. Each hypothesis is recomputed with the census-flagged
  records of its own stratum excluded. A flagged record is one the judge rejected or noted as not a
  quantity:
  - **H1** on M_num minus its 9 flagged records (3 rejected, 6 not a quantity): 140 records, 27 of them
    discriminating;
  - **H2** on V minus its 3 flagged records (1 rejected, 2 not a quantity): 54 records, 14 of them
    discriminating;
  - **H3** on both strata minus all 12 flagged records.

  These are labelled exploratory and never replace H1–H3. Only the 53 discriminating records were judged,
  so non-discriminating records are never excluded. The census flags are fixed in the private files
  above.
- Layer-1 rules for later rounds (not this one) will cover all three classes: the quote must print a
  digit; and cross-references, accessions, equation numbers and definitional constants are rejected.

**Also recorded.**
- **The `value_is_bound` hand check on set O:** 10 of its 28 rejects, drawn with seed
  `261103|vbcheck|O`. All 10 are bounds as written. 4 are arguable: 3 significance thresholds where
  the threshold is itself the quantity asked for, and 1 "up to" an observed maximum.
- **Set A paused:** the yield watch stopped set A at 100 jobs, with 28% passing layer 1 against the 0.30
  minimum. From A's first call, output tokens per call jumped (median 2,070 against O's 136, maximum
  4,092; 2 calls truncated at 4,096). No request field changed: the drafter configuration was identical
  for O and A. The partner's gateway audit shows no change on its side. A diagnostic probe is under way,
  and the owner will decide whether to resume once it explains the jump.
- **Over the size notice:** set O used 863 gateway calls against the notice's 695, because of 1.61
  attempts per job instead of the assumed 1.3. A running counter that stops a batch at +10% of observed
  calls is required before any further batch; set A ran under a manual watcher at +10%.

**What was known.** No retrieval, swap or found@10 measurement had been run on any round-3 set. The
census judged record validity only and saw no arm outcome.

## D-6, 2026-10-30T22:05:06Z: the drafter model's upstream behaviour changed between sets O and A; set A resumes under a higher cap

**What happened.**
- Set A's drafting began at 20:04:43Z. From its first call, the output tokens per call jumped: median
  2,070 against set O's 136, with a maximum of 4,092. 2 answers were truncated at the frozen
  `max_tokens` of 4,096. The yield watch then paused set A at 100 jobs, with 28% passing layer 1
  against the 0.30 minimum.
- The partner's gateway audit shows no change on its side between the two sets. The same process, model,
  served model and cap key appear on every call, and the only event in the window is one HTTP 502.
- Our request bodies carry no reasoning or effort field. The drafter configuration and code were
  identical for O and A.

**The probe (22:00–22:02Z, 8 calls).** Two set-O prompts and two set-A prompts were each replayed twice,
with bodies rebuilt by the drafter's own builder.
- The two O prompts, which had cost 148 and 177 completion tokens at about 18:00Z, now cost 1,511 to
  2,305. At least 86% of that is `reasoning_tokens`, a usage field that is now reported.
- Each A prompt reproduced its original count exactly on one of its two replays (843 and 1,525).
- Run-to-run spread was about ±25%.

**Conclusion.** Between 19:10Z and 20:04Z, the upstream model began reasoning on these requests, or began
reporting the reasoning in its token counts. Neither we nor the partner changed anything.

**Consequence: a comparability caveat.**
- Set O was drafted entirely before the change.
- Set A's first 100 jobs, and any resumed jobs, were drafted after it.
- Differences in yield or record character between O and A cannot be attributed to the corpora alone.
- Set A is descriptive only, so no hypothesis depends on the comparison.

**Decision (owner).** Set A resumes its remaining 100 jobs with the yield-watch override
(`--accept-low-yield`), under two changes:
1. the drafter's `max_tokens` rises from the frozen **4,096** (PREREG D10) **to 16,384** for the resumed jobs only, so reasoning
   cannot truncate the answers;
2. our round-3 output-token pause rises from the frozen **0.5 M** (PREREG D10) **to 1.0 M**. The partner's user set no cap.

The resume uses the code's own route: a new output directory holding byte-identical copies of set A's
pool and draft files, so the first 100 jobs are carried over, not redrafted. No state file is edited.

The two segments' configurations differ only in `max_tokens` and the caps. They are traceable through
their state files' configuration sha256s, and the summary reports set A's yield per segment.

**What was known.** Set A's first-segment layer-1 outcomes and its token usage. No retrieval had run on
any round-3 set.

## Pointer, 2026-10-30: layer-3 owner spot-check validity

Not a deviation: the owner's spot-check labels for sets O and A, imported after `RESULTS.md`, and one owner decision on how a set-O item counts. See [`ADDENDUM-2026-10-30-spot-validity.md`](ADDENDUM-2026-10-30-spot-validity.md).
