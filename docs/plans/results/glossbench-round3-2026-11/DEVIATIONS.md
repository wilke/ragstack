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
