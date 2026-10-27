# Pre-registration, part 4: confirmatory chunk-size test on the deployed documentation collection (redacted public copy)

> **Redacted copy.** The private original stays with the outputs, off GitHub. Its sha256 is
> `a41394e3cd88771e5a3a69159d556c8a3a453ed6e87d16295615d26383103b10` (frozen 2026-10-25T03:41:39Z; recorded in the private `PREREG.sha256` before any part-4 question was generated).
> Endpoint ports, file paths and the gateway owner's name are replaced by neutral descriptions;
> the prompts are not reproduced (they are the earlier parts' prompts, cited by sha); design, numbers and rules are unchanged.


**v4, frozen 2026-10-25T03:41:39Z on the owner's go.** The frozen file still carries its draft header ("DRAFT v4, 2026-10-24. Not frozen."); it was frozen as written, and the sha above is of that text.

Version history:
- **v1** (sha a648560a): Fable review of the design. Verdict: redesign §5–§6, approve-with-changes elsewhere.
- **v2:** applied that review and the owner's choices of the non-inferiority margin, the conditions, the generator fallback and the use of the human grades. It also added pause and resume (owner).
- **v3:** applied a Fable review of the part-4 code (approve-with-fixes), the owner's choice of Holm over a fixed-sequence procedure, mid-p as a reported sensitivity only, the power table from the code, and the 5M output stop agreed with the gateway owner.
- **v4:** applied the final pre-freeze Fable review. Only wording and omissions changed; no number or rule changed.

**Terms.**
- **SFR:** the SFR-Embedding-Mistral tokenizer, in which every token count here is measured.
- **f32:** dense vectors stored unquantised.
- **Rules glossary:** the symbol glossary from the rule-based extractor (`symbol_glossary.py`, version rules/3). It attaches the definitions of symbols used in a chunk when they are defined elsewhere on the page, the same way in every arm. On this collection it is almost never non-empty.
- **Gold normalisation:** NFKC, then casefold, then all whitespace removed.

**Freezing.** The draft is frozen when the owner approves it. Its sha256 and a UTC timestamp are then appended to PREREG.sha256. That happens:
- after the part-4 code and its unit tests are committed on main (§7);
- before any part-4 question is generated.

**Status:** CONFIRMATORY for H1–H4 on set M under B4096 (§6). Everything else is a replication or descriptive.

**What had been seen before writing:** all results of parts 1–3, Addendum S and the k=10 test.
- The design is chosen *because of* those results.
- It is a fair confirmatory test only because every part-4 question is new: new seed, new passages, a different generator family. None exists yet.
- The 76 long pages are a **census** of the collection's pages of at least 2,048 SFR tokens, not a sample. Reusing them leaks nothing about page selection; they are the target population.

The collection is read in place, read-only, from the same store and index as parts 1–3. Outputs go to a private directory on the run host only. The served pipeline is unchanged:
- BM25 top 100 + SFR dense top 100;
- RRF k=60, cut to 50;
- bge-reranker-v2-m3.

## 1. What this tests, and why

The exploratory runs had three weaknesses: the grader changed after results were seen, 66 contrasts went uncorrected, and one model family generated, answered and graded. Part 4 re-tests what a rebuild decision needs:

| id | hypothesis | type | contrast |
|---|---|---|---|
| H1 | 512-token chunks give more correct answers than 128 | superiority, one-sided | T512o0 − T128o0 > 0 |
| H2 | capping semantic at 512 is no worse than live | non-inferiority | SEM512 − L > −δ |
| H3 | 512 is no worse than 1,024 | non-inferiority | T512o0 − T1024o0 > −δ |
| H4 | fixed 512 is no worse than live | non-inferiority | T512o0 − L > −δ |

- **δ = 0.075:** a net share of 7.5 percentage points of questions (owner, 2026-10-24).
- **Primary condition:** B4096, equal text per arm (§4). The k=10 test confounded chunk size with the amount of text.
- **Prior for H1:** the exploratory H1 effect (−18 of 30 discordants) was measured at k10, where T128 had a quarter of T512's text. It is **not** a prior for B4096. Part 3's budget-matched retrieval gives T128 − T512 of −10 at b5120 and +5 at b2048 (n = 105). So the H1 sign may depend on the budget, which is why B2048 is measured (§4). A sign that depends on the budget is a result, not a failure.

## 2. Questions (new; none generated yet)

**Generator.** Claude Opus 4.8 via the gateway: temperature 0, max_tokens 1024, `fallback_model ""`.
- It is a different family from the readers (Llama-4-Scout, Qwen3.8) and from the primary grader (GPT-5.6 Sol).
  - The secondary grader (Opus 4.8) is the generator's own model, so the Sol–Opus κ is measured on questions Opus wrote. That is a mild self-consistency risk, and the human sample arbitrates it.
- The prompts are the part-2 prompt for S and the part-3 M prompt for M, verbatim (private preregs, sha 3619ce0d and 2ddf5407).
- **Validation:** as in parts 2–3:
  - every quote is found under the gold normalisation;
  - every quote is at most 300 characters;
  - for M, the two quotes start at least 1,500 characters apart.
  - Failures are dropped and counted. There is no redraw.
- **Duplicates:** within a page, exact-duplicate questions (under the gold normalisation) are dropped, keeping the first. Near-duplicates (a shared A or B window) are counted and reported.
- **Empty responses.** Opus 4.8 returns 0 tokens on some pages every time. In the exploratory runs these were 4 pages, in three strata.
  - An empty response gets one retry after 2 s.
  - If it is still empty, the passage is regenerated with **GPT-5.6 Sol** (no temperature, max_tokens 4096) and flagged `gen=sol`. This is the owner's choice.
  - The confirmatory results are reported with and without the `gen=sol` questions.
  - The pages, strata and page lengths of `gen=sol` items are reported against the rest.
  - The generator never sees chunks, and every contrast is paired within a question, so generator bias cannot favour an arm. It can only shift which pages are represented.
- **Fresh passages:** a passage window may not overlap any gold quote used in parts 2 or 3. Such windows are excluded before sampling.
- **Seed:** 261025. Derived seeds: `261025|M` and `261025|S` for the passage draws, `|k10`, `|secondary`, `|retest` and `|human` for the subsets (§5).
- **Yields:** the expected yields below are Qwen's from parts 2–3. Opus 4.8's may differ, and the realised n is reported.

**Set M: two passages, needing both. This is the confirmatory set.**
- All 76 pages of at least 2,048 SFR tokens.
- 6 attempts per page: 456 attempts, each a new pair of 1,000-character A and B windows, as in part 3.
- Expected about 315 valid, about 4 per page.

**Set S: single passage. Replication only.**
- Pages with **at least 2 live chunks**. The 110 single-chunk pages cannot separate arms.
- One 1,200-character passage per eligible page, its start uniform and snapped to a sentence start.
- Then a second, non-overlapping passage on the longest pages, in descending order, up to 420 attempts.
- If more than 420 pages are eligible, the first pass is a stratified random sample of 420 of them (largest-remainder quotas by stratum, seed `261025|S`), and the second pass is skipped. At most 226 pages are expected to be eligible, so this branch is not expected to run.
- The eligible count is logged before generation.
- S is reported with the same tests, outside the confirmatory family. The exploratory analogue was near ceiling (95–113 of 116).

**Set R: real user questions (optional).**
- Only if real questions, with answers locatable in the collection, arrive before freezing.
- Its gold rule and size then go into an addendum frozen before any R retrieval, with identical grading.
- R is outside the confirmatory family.
- Otherwise R is *absent*, and the report says the claims rest on generated questions only.

Part 3's set D is dropped, because it was at its ceiling.

## 3. Arms

Five arms, as built in parts 2–3 and Addendum S, with the rules glossary and f32 only:
- **L:** live, semantic, uncapped;
- **SEM512:** semantic, capped at 512 SFR tokens;
- **T128o0, T512o0, T1024o0:** fixed token, no overlap.

**Manipulation check (a gate, run before any reader call).** Chunk counts must equal:

| arm | chunks |
|---|---|
| L | 1,265 |
| SEM512 | 1,564 |
| T128o0 | 3,955 |
| T512o0 | 1,113 |
| T1024o0 | 657 |

These were verified against the part-3 results file (`arms_chunks`).
- The **M census** must also hold exactly **76** pages of at least 2,048 SFR tokens.
- SFR-token size distributions are reported per arm.
- A mismatch in either check stops the run before any call.

## 4. Answering

**Readers.**
- Llama-4-Scout and Qwen3.8-27B (thinking off), each on its own local endpoint.
- Temperature 0, **max_tokens 600**, at most 4 in flight per endpoint. Never the deployed assistant's own endpoint.
- The system and user prompts are the k=10 test's, verbatim (private prereg sha dda8af1e).
- `finish=length` counts are reported per arm and reader.

**Conditions.**
- **B4096, primary.** Walk the reranked list in order and add whole chunks while the running SFR-token total of chunk text stays at or under the budget.
  - The chunk that would exceed it is **truncated at the last sentence boundary within the remaining budget**, and added. Then the walk stops.
    - The boundary rule is the harness's sentence-end rule: `[.!?]` with an optional closing quote or bracket, then whitespace; or a run of newlines. It is the same rule that snaps passage windows. The T/X arms are fixed-token windows and have no sentence splitter of their own.
    - If no sentence boundary fits, nothing more is added.
  - So every arm gets the budget **to within one sentence** whenever its reranked list holds enough text.
  - Each item records its `shortfall` (budget minus realised chunk-text tokens). `truncated_empty` is flagged only when the shortfall is positive and no sentence fitted.
  - Without truncation, live, whose chunks reach 3,398 tokens, would have been under-supplied by 5–13% in simulation.
  - Reported per arm and question: realised chunk-text tokens, and realised prompt tokens (chunk text + glossary + excerpt labels; T128's 32 excerpts carry about 350 tokens of labels).
  - If an arm's mean realised chunk-text tokens are more than 2% under the budget, that is a reported limitation.
  - 4,096 is about T512's k10 context. It is larger than 55 of the 76 M pages, so for most M questions this is a whole-page regime, and arms differ mainly in how much text intrudes from other pages. The report says so.
- **B2048, secondary.** The same rule at 2,048. Here part 3 showed the size ordering flipping.
- **k10, secondary.** The top 10 reranked chunks, whatever their size: what deployed systems do.

**Calls:** about 635 questions × 5 arms × 3 conditions × 2 readers, about 19,000 local calls, split evenly between the two reader endpoints.

## 5. Grading

**Primary grader: GPT-5.6 Sol via the gateway.**
- No temperature (the model rejects it), **max_tokens 4096**, `fallback_model ""`.
  - Its hidden reasoning is billed as completion tokens. In the exploratory run, 107 of 440 Sol grades used more than 64.
  - The visible output is one word.
- **Coverage:**
  - all B4096 and B2048 answers;
  - k10 answers for a 50% subset of questions, stratified by set × stratum (largest-remainder quotas), seed `261025|k10`.
- Calls are deduplicated by (question, normalised answer).
- The prompt is the k=10 test's grader prompt, verbatim.

**Secondary grader: Claude Opus 4.8,** temperature 0, max_tokens 512, `fallback_model ""`.
- 512 because, in the exploratory run, 14 of 3,196 Opus 4.8 grades used more than 256 completion tokens (max 393), all of them hidden.
- It grades **every** answer of a 25% subset of **questions** (stratified by set × stratum, seed `261025|secondary`), so its contrasts stay paired.
- The two subsets use derived seeds and are drawn independently.
- It is used for κ and as the grader-sensitivity reading of H1–H4.
- Qwen is not a grader, because it would be grading its own answers.

**Parsing (fixed now).** The rules are applied in order:
1. a bold label (`**Correct**`), or `Grade: <label>`, wins;
2. otherwise, take the label words that occur anywhere (`correct`, `partial`, `incorrect`, `abstained`; also `abstain`, `abstains` and `abstention`, which count as `abstained`; whole words, case-insensitive). A label is *rejected* if any occurrence of it is negated or qualified, that is, if one of these words appears within the 3 words before it:
   - negators: not, never, no, nor, neither, hardly, cannot, or any word ending in n't;
   - qualifiers: partially, partly, mostly, largely, somewhat, half, nearly, almost, only.

   If exactly one distinct label remains and none was rejected, that label;
3. otherwise, unparseable. For example, "partially correct" is unparseable, not `correct`, and not `partial`.
- The full raw text of every grade is stored privately.
- The parser is unit-tested on synthetic cases, covering negations, qualifiers, the abstain forms and the 3-word window. The legacy first-word parser stays only for the exploratory subcommands.
- Unparseable and `no_label` rates are reported per arm. An arm-dependent rate would be a bias.

**Test–retest.** Sol has no temperature, so 200 random graded items (seed `261025|retest`) are re-graded once, bypassing the cache. Agreement and κ are reported. If κ(correct vs not) is below 0.8, the primary results carry that caveat in the sentence.

**Gateway safeguards** (standing approval, unchanged):
- at most 4 in flight;
- what counts as a **failure**, for the graders:
  - an empty visible answer that still used completion tokens;
  - an HTTP error that remains after its retries.
- what is **not** a failure:
  - a **0-token** empty, which is retried and then becomes `no_label (empty)` (below);
  - a length-cut empty, which becomes `no_label (truncated)` (below);
  - any empty from the **generator**, which goes to the Sol fallback (§2).
- the run pauses at 5 failures in a row or at more than 20 in total.
  - "In total" means over the **whole run**, across resumes; the count is persisted.
  - "In a row" restarts at 0 on a resume.
- a 0-token empty is retried up to 2 times with a 30 s backoff, then marked `no_label (empty)`;
- a response cut off by max_tokens with no visible content (`finish_reason length`) is marked `no_label (truncated)`. It is not retried and not counted as a failure. For the generator, it goes to the Sol fallback, like an empty response;
- a stop at **5M output tokens** in total, counted from the start of part 4 and across resumes.
  - This is the owner's proposal (2026-10-24), raised from the standing 3M.
  - **Agreed by the gateway owner on 2026-10-24**, as proposed. Every other safeguard is unchanged, and Sol runs with temperature omitted.
  - Any cap or rate-limit hit pauses the run (exit 75). It resumes only after a fresh notice to the gateway owner.
  - The private config's `limits.output_token_cap` is set to 5,000,000. The code default stays at 3M.

The gateway owner gets the batch size before each batch, and the totals after it.

**Estimated gateway load**, from the token usage in the exploratory call logs:

| step | calls | input | output |
|---|---|---|---|
| generation | ~950 (at most ~1,400 with fallbacks) | ~0.8M | ~0.2M |
| Sol | ~11,100 + 200 retest | ~3.9M | ~0.45M |
| Opus 4.8 subset | ~3,300 | ~1.4M | ~0.05M |
| **total** | **~15.5k** | **~6.1M** | **~0.7M** |

The previous phase was 7.9k calls, 3.6M input and 82k output.

### Human grades (about 100, owner's split)
Built only after all model grades exist. The grader sees the question, the reference quote(s) and the answer. They do not see the arm, condition, reader or any model grade, and the order is shuffled. They use the same four labels and instructions.

**Part A: grader validation, 40 items.** These are items where Sol and Opus 4.8 disagree on correct vs not, within the Opus subset (seed `261025|human`).
- Reported: P(human sides with Sol), with a 95% CI. This statistic decides how much to trust the primary grader.
- A second person grades these 40 if available, and human–human κ is reported.

**Part B: decision validation, about 30 pairs (60 answers).** These are discordant pairs of H4 (T512o0 vs L, set M, B4096, Scout), drawn at random. Both answers of each pair are graded.
- Reported:
  - the human's gained / lost on those pairs against Sol's;
  - κ(correct vs not), human vs Sol, with a 95% CI.

**Rules (fixed now):**
- The primary grader is *validated* if, on part B, κ(correct vs not) between human and Sol is at least 0.6 at the point estimate (the CI is published), **and**, on part A, the human sides with Sol at least half the time.
- If the human agrees with **neither** model (κ < 0.6 against both), H1–H4 are reported as *grader-unvalidated*, and the human-graded pairs are reported descriptively.
- The primary grader is never switched.
- Human grades stay private. Only agreement statistics are exported.

## 6. Confirmatory analysis

**Endpoint.** A question is *correct* for an arm when the primary grader says `correct`. On M, `partial` means one part is missing, so it is not correct.

**Missing data.** A question whose answer on either side of a contrast is `no_label`, unparseable, or a failed reader call is excluded from that contrast. The count is reported per contrast. A sensitivity reading scores those answers as not correct.

**Family:** H1–H4 on **set M under B4096**, per reader. Readers are not pooled.

**Test (primary):** a sign-flip permutation test with **pages as the exchangeable units**.
- For each page, sum the per-question differences d ∈ {−1, 0, +1}, then flip the signs of the page sums: 10,000 flips, seed 261025.
- This is the randomisation test for paired outcomes under clustering; McNemar assumes independent pairs.
- **H1** is one-sided (> 0).
- **H2–H4** test non-inferiority: shift each question's difference by +δ and test, one-sided, that the page-sum mean is greater than 0.
- **Holm correction** over the 4 tests, α = 0.05, separately per reader. This is the owner's choice over a fixed-sequence procedure, made knowing that fixed sequence would give H2–H4 more power. Holm keeps every hypothesis testable regardless of order.
- **p-value:**
  - (1 + #{flipped ≥ observed}) / (1 + 10,000);
  - exact by enumeration when at most 20 pages are non-zero;
  - **no mid-p and no randomised tie-break** in the decision.
- **Mid-p, as a reported sensitivity check only** (owner, 2026-10-24). For every test the mid-p is also reported:
  - #{flipped > observed} + ½ · #{flipped = observed}, divided by the number of flips (or of exact enumerations);
  - Holm is applied to the mid-p values separately.
  - It never decides "confirmed". If a hypothesis would be confirmed under mid-p but is not under the standard p, the report says so in the sentence.
- **Known properties, from simulation by the committed code at 76 pages and 315 questions:**
  - The superiority test is conservative: null rejection is about 0.037–0.045 at α 0.05, because page sums are integers.
  - The non-inferiority test is slightly liberal at the margin: about 0.050–0.058, because d is bounded.
  - Both properties are stated in the report.
- Exact McNemar is reported beside it, as a descriptive.

**Intervals.** The net share (gained − lost) / n gets a page cluster bootstrap CI: percentile, 10,000 resamples, seed 261025, 90% two-sided (the matching one-sided 95% bound is used for H2–H4).

**Confirmed when:** the hypothesis is Holm-significant under **both** readers. This is an intersection-union rule, so it needs no further correction.

**Otherwise:**
- the **decision** is Holm on the sign-flip p. Any H2–H4 that is not confirmed is *not shown non-inferior*. The bootstrap CI and whether its lower bound clears −δ are reported as descriptive; they are unadjusted and never decide;
- H1 is *not confirmed*;
- if the two readers disagree, the result is *reader-dependent*.
- "No difference" is never written.

**Power.** Computed by the committed code (`part4 --power-table`), with the page-clustered test. Setup:
- 76 pages and 315 questions, spread evenly;
- 2,000 simulations, seed 261025;
- Holm worst case α/4.

Each cell is per-reader power, with joint power for both readers in brackets. Joint power is the product of the two, which is conservative if the readers' errors are correlated.

| | discordance .20 | .25 | .30 | .35 |
|---|---|---|---|---|
| H1, share .4 | 0.79 [0.63] | 0.88 [0.78] | 0.94 [0.89] | 0.97 [0.93] |
| H2–H4, true net 0, δ .075 | 0.76 [0.58] | 0.66 [0.43] | 0.57 [0.33] | 0.49 [0.24] |

- **H1** at share .3 has per-reader power of 0.48–0.79, and at share .5 it is at least 0.94. At B4096 the share itself is unknown (§1).
- **H2–H4:**
  - At a true net of −0.02 and discordance .25, per-reader power is 0.38. At −0.04 it is at most 0.41 anywhere in the grid.
  - So a small real loss will most likely end **not shown non-inferior**, not confirmed.
  - **The rebuild question (H4, H2) may well end unresolved.** That outcome is acceptable and is reported as such.
- A page effect on the direction of discordance (ICC 0.1) lowers each cell by 0–0.09.
- The full 96-row table is produced by `part4 --power-table` before the run and saved **beside** the results, as `power-table-part4.json`.

**Secondary (reported, not part of the family):**
- the same tests under B2048 and k10 (k10 on Sol's 50% question subset);
- set S as a replication;
- H1–H4 under Opus 4.8 on its 25% subset;
- correct + partial;
- abstentions;
- per-stratum nets;
- found@budget (retrieval) beside accuracy, so retrieval and reading failures can be told apart. A quote counts as found if it is delivered in full, or if at least 60% of it is delivered as a prefix or suffix, which is the parts 2–3 quote rule. For M, both quotes must be found;
- realised tokens per arm and condition.

## 7. Code, pin, stops and deviations

**Before freezing, these must be on main with unit tests, Fable-reviewed:**
1. a `part4` subcommand: arms, conditions, and question files from config;
2. gateway generation with the Sol fallback;
3. the S and M allocation, the fresh-window exclusion and the duplicate rule;
4. the budget walk with sentence-boundary truncation and realised-token recording;
5. Sol-all + Opus-by-question grading, and the k10 50% subset;
6. the parser, with full raw text stored privately;
7. a test–retest path that bypasses the cache;
8. the sign-flip page permutation test, the shifted non-inferiority test, the page cluster bootstrap, and Holm, each tested on hand-checkable tables;
9. the manipulation-check gate;
10. the blinded human-grade **export**, with the items and the key in separate files, plus the statistics it feeds (`cohen_kappa_ci`, `proportion_ci`).
    - The grading **page** and the **import** are built after the answers exist and before human grading starts. That is by design, not a deviation, and it is logged when done.
11. pause and resume: pause triggers, `part4-state.json`, exit code 75, `--resume` with sha checks, call keys that make a re-issued call identical, and the forced-pause and identical-resume tests against a mock endpoint.

**Pin.** The run uses one main commit, no earlier than 47e65db, that contains all of the above. It runs with `--private-config`, and the results embed `experiment_provenance()`. They are citable only from a clean tree (`dirty: false`, `untracked: 0`).

**Pause and resume (owner requirement, 2026-10-24).** Hitting a budget or rate limit **pauses** the run; it does not abort it.

*Pauses:*
- the configured output-token cap (§5: 5M, counted from the start of part 4);
- a per-batch budget of calls or input or output tokens, set by the owner or the gateway owner;
- the failure stops (5 in a row, more than 20 in total);
- HTTP 429 or a quota error from the gateway after 3 backoffs (30 s, 2 min, 10 min);
- a local endpoint unreachable for 10 minutes;
- an operator stop: a `PAUSE` file in the output directory, or SIGTERM, which finishes the in-flight calls and flushes.

*On a pause, the run:*
1. stops issuing new calls;
2. lets the in-flight calls finish and writes their results;
3. flushes every cache and log (append-only JSONL with one fsync per write, so a crash loses at most the in-flight calls);
4. writes `part4-state.json`:
   - the step (generation, retrieval, readers, grading, retest);
   - the reason for the pause;
   - the counts done and remaining per step;
   - token and call totals since the start and since the last resume;
   - the commit, the frozen prereg sha and the config sha;
5. tells the gateway owner that the run has paused and why, with the totals so far;
6. exits with code 75 ("paused", distinct from success 0 and failure 1).

*Resume* is `part4 --resume`. It:
- refuses unless the commit, the frozen prereg sha and the config sha all match `part4-state.json`;
- re-reads every cache;
- re-issues only the calls whose results are missing;
- continues with the same seeds and the same subset draws;
- gives the gateway owner a batch notice (the remaining size) before its first call.

Every call is keyed so that a re-issued call is identical to the one it replaces: (step, question, arm, condition, reader or grader). A 0-token empty that was already given up on stays `no_label`. It is not retried on resume.

*Recording:* pause and resume times, reasons and totals are recorded in the results (`run_segments`). A pause followed by a resume under the same frozen design is **not** a deviation. Any change made while paused (a cap, a model, a parameter) **is** a deviation.

*Tested before freezing:*
- a forced pause, by a `PAUSE` file and by a mock 429, at each step;
- a resume that ends with byte-identical results to an uninterrupted run, against a mock endpoint.

**Crashes and torn files.**
- An unexpected exception writes `status: crashed`, with the reason and totals, to the state file, prints the notice line, and exits 1. `--resume` from a crash is allowed, with the same sha checks.
- A torn last line in a log (a crash between write and fsync) is moved aside to `<file>.torn` and logged on resume. A malformed line anywhere else stops the run.

**Hard stops** (no resume without a new decision, logged as a deviation):
- a manipulation-check mismatch;
- more than 2% of reader calls failing after retries;
- a frozen-prereg sha mismatch at start.

A commit, prereg or config sha mismatch on `--resume` is not a hard stop. The resume is **refused** (exit 1) and proceeds once the files match. Changing any of them after freezing is a deviation.

Any change after freezing is a deviation, logged in DEVIATIONS.md with the time and the reason.

## 8. Load summary

| step | calls | endpoint |
|---|---|---|
| generation | ~950 (≤ ~1,400) | gateway (Opus 4.8; Sol fallback), after notice |
| retrieval | ~635 queries × 5 arms; reranker ~160k pairs | the embedding fleet (≤ 2 in flight per replica) and the reranker (≤ 4) |
| readers | ~19,000 | the two reader endpoints (≤ 4 in flight each) |
| Sol grading | ~11,100 + 200 | gateway, after notice |
| Opus 4.8 grading | ~3,300 | gateway, after notice |
| human | ~100 answers (+40 second rater) | a private grading page |

No store writes. Never the deployed assistant's own endpoint.
