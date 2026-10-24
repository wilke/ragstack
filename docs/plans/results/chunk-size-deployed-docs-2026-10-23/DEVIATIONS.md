# Deviations and implementation choices (redacted public copy)

> **Redacted copy** of the private deviation log, which stays with the outputs off GitHub. Hosts, file
> paths, process ids, the collection's name and the names of other sessions are removed or replaced:
> "the gateway" is the Claude gateway the judge and grader calls went through; "the gateway owner" is
> the session that operates it; "the coordinator" is the session that relayed approvals. Stratum
> "topic" replaces a private stratum name. Entries are in time order, with their original UTC times.
> Numbers are as logged at the time; the record's tables come from `artifacts/`, not from here.

## 2026-10-23T14:27:12Z, logged before any generation or retrieval

1. **Sentence snapping (part 2 §3).** A sentence start is the first position after a run of `[.!?]` plus
   an optional closing quote or bracket plus whitespace, or after a run of newlines, at or after the drawn
   start; if none follows, the drawn start is kept; a draw of 0 stays 0; the passage runs 1,200
   characters from the snapped start, or to the end of the page.
2. **Sampling.** Strata are sampled in a fixed order (cli, quickref, tutorial, topic, other), each with
   `random.Random(261023).sample` over its sorted eligible doc_ids. The same generator then draws each
   page's passage start. Largest-remainder ties are broken by that stratum order.
3. **Retry rule.** One generation pass. Only unparseable answers are retried, once. A question whose quote
   fails validation is dropped, as pre-registered.
4. **Judge pool for W.** The pre-registered pool (every arm's reranked top 10) leaves b2048 and b5120
   undefined for small-chunk arms, whose budget walks go past the top 10. The pool is therefore the union
   of the top 10 and every chunk inside the b5120 walk, for every config.
5. **Rule legs.** Overlap contrasts use reranked@10 as the rule's net; contrasts against live (sizes differ)
   use b5120. "Net gains in at least 2 strata" means at least 2 strata with net > 0 on that same leg.
6. **Part 1 scope.** All part-1 groups are scored on every config (L, the 16 T/X arms, the storage
   variants), both glossary states; tables lead with the rules state.
7. **AUROC** is reported with all 15 negatives (including M5), and again without M5. Ties count 0.5.
8. **Score source.** Top-1 reranker scores come from the reranker cache, for the passage the pipeline sent.
9. **Run order.** Retrieval for parts 1 and 2 runs in one pass, with the same configs.
10. **Embedding.** The SFR-Embedding-Mistral fleet is addressed on loopback, as in the section-aware runs.

## 2026-10-23T16:05:43Z, part 3, logged before any part-3 generation, retrieval or judging

1. **Scout W labels voided.** The part-1 Scout judge labelled 0 of 3,731 pool pairs "yes" (2,905 partial,
   825 no, 1 unparseable). The group-W hit tables in the part-1/2 results are therefore not results; they
   stay in that file as a record only. Part 3 §B re-judges the same pool.
2. **Pool reconstruction.** The part-1/2 results do not keep the reranked lists, so the W pool is rebuilt by
   re-running the 66 part-2 configs for the 13 W questions from the caches. Check: 3,731 pairs, every pair
   already in the Scout cache.
3. **Opus request format.** OpenAI-compatible `POST /v1/chat/completions` to the gateway; model
   "Claude Opus 5"; extra body `{"fallback_model": ""}` so the gateway never substitutes another model;
   temperature 0, max_tokens 8; no key file is read. **Hold:** no gateway calls until the gateway owner's
   user confirms; without `--opus` the Opus section is written as "pending".
4. **Type M sampling.** Eligible pages in stratum order, each sorted by doc_id; one `random.Random(261024)`
   draws per page M1 (A, then B), M2 (A, then B), then D. A's raw start is uniform over [0, len − 1000],
   then snapped. B's raw start is uniform over [0, a − 1500] ∪ [a + 1500, len − 1000], then snapped; an
   empty union skips that M question (no_room); if snapping B breaks the distance or overlaps A, B's
   unsnapped start is kept (counted).
5. **Quote positions** for the 1,500-character check use a character map from the normalised page back to
   raw offsets; a quote's occurrence inside its own passage is preferred, else its first occurrence; a
   quote not resolvable in the map is dropped (quote_position_unresolved).
6. **Type D passage.** Raw start uniform over [⌊0.1·len⌋, max(⌊0.1·len⌋, ⌊0.9·len⌋ − 1200)], then
   snapped. Echo is measured against the passage, as in part 2.
7. **Type M echo** is descriptive only (maximum over the two passages); M is not dropped for echo.
8. **Type M scoring.** Retrieval uses quote_a as the record's single quote. Per leg, the delivered set is
   the top 10 or the b2048/b5120 walk; found = some delivered chunk hits quote_a AND some hits quote_b;
   both-in-one = one chunk hits both. The walk does not stop at the first hit.
9. **Arms.** L is the live chunks with the live glossary field (identical to SEMlive, Addendum S
   reproduction 336/336). SEM512 and SEM256 are rebuilt with Addendum S's code from the embedding cache,
   rules/3 glossary. Only the rules glossary state is run.
10. **Unresolved** = gained + lost < 10 on the leg the rule uses.
11. **Primary legs.** M: b5120; D: b2048. Rule leg 1 uses the primary leg; leg 2 is always "no loss at
    b5120". The overlap contrast is also judged on the primary leg (not reranked@10 as in part 2).

## Part 3 §B, Opus runs (W judge)

- **16:21:33Z, go** relayed by the coordinator: 3,731 calls, max_tokens 8, temperature 0, at most 4 in
  flight, fallback_model "". One attempt per pair; stop after > 20 failures or 5 in a row; a failed call is
  recorded as unparseable (= "no").
- **16:26:06Z, stopped by the operator** after the first 200 calls: every answer empty, completion_tokens 8
  (= max_tokens). The error caps were not tripped (HTTP 200); stopped as degenerate. The recorded pid was a
  shell wrapper; the Python child was stopped separately after verifying its working directory. Empty rows
  were set aside so a rerun would not reuse them. Likely cause (unverified then): hidden reasoning spends
  the budget before any visible word.
- **16:28:08Z, max_tokens 8 → 64** for Opus only (Qwen stays at 8). The gateway owner's probe found Opus 5
  spends about 10 hidden tokens and returns empty content with finish_reason "stop" at 8. Empty or
  whitespace-only content now counts as a failure and is never cached. A per-call log (counts only) is
  written.
- **16:38:08Z, run 2 (64) stopped by the error cap:** 49 calls, 26 ok, 23 empty, each with all 64 tokens
  spent and finish "stop". The 26 labels are kept.
- **16:38:30Z, 64 → 256**, within the gateway owner's standing approval (max_tokens ≥ 64). The k10 grader
  also moves to 256 (prereg 64).
- **16:44:43Z, run 3 (256) stopped by the error cap:** 122 calls, 100 ok, 22 empty (all 256 tokens spent).
- **17:05:44Z, owner go ("Run")**: max_tokens 4096 for both batches; a new stop at 3,000,000 total Opus
  completion tokens since the go, checked before every call; HTTP timeout 180 s → 900 s. **Parsing:** Opus
  5 writes a bolded label plus a sentence; the parser takes the first `[a-z]+` run of the lower-cased
  answer. A unit test asserts that "**Partial** — x" parses to partial.
- **17:44:49Z, run 4 (4096) stopped by the error cap:** 2,207 calls, 21 empty, 20 of them with 0
  completion tokens and finish "stop" (not truncation).
- **17:46:33Z, empty-answer handling** (coordinator option, amended by the gateway owner): a 0-token
  empty is retried twice with a 30 s backoff; a burst rule drops to 2 in flight.
- **17:51:46Z, resume stopped on 5 failures in a row:** 8 pairs, 21 requests, all 0-token empties.
- **17:55:03Z, item-specific rule** (supersedes the retry and burst rule): the same prompts return 0-token
  empties every time, so the cause is the item. An item that returns a 0-token empty twice in a row is
  "no_label (empty)" and skipped; skips do not count toward any stop; the 5-in-a-row stop applies to new-item
  failures only. Both readings are reported: no_label counted as the failure label (prereg) and excluded.
- **18:57:00Z, Opus 5 runs complete.** W: all no_label and failures are on one question, H5w (86 of 283
  pairs no_label, 8 failed); the other 12 questions have 0. k10: 38 no_label on 4 records (m006, m136,
  g136, d053), 2 failures (g136). The skipped ids (ids only) went to the gateway owner.

## 2026-10-23T16:37:42Z, k10 answer test, logged before any k10 reader or grader call

1. **Top-10 lists** are recomputed for all 286 questions from the caches (part-3 M lists were not stored);
   gate: every recomputed list equals its stored list where one exists, and 0 new reranker calls.
2. **Gold in the top 10** ("reading failure") uses the quote rule on the top-10 chunk texts; both quotes
   for M.
3. **Context tokens** are SFR-Embedding-Mistral tokens of the 10 chunk texts, without the glossary.
4. **Reader prompt.** Each excerpt is "[n] " + chunk text, plus "\n" + the glossary field when present;
   excerpts are joined by blank lines.
5. **Window guard** counts the prompt with the SFR tokenizer as a proxy, against the window minus 512.
6. **Reader errors** are retried 3 times; a still-failing call stops the run before grading.
7. **Grader dedup key** = sha(record_id + NUL + norm(answer)), norm the gold normalisation.
8. **Unparseable grades** count as incorrect and are listed (prereg).
9. **Rule leg 2** ("same sign under the other reader"): the other reader's net must be non-zero and of the
   same sign.
10. **Opus** grader runs under the gateway owner's standing approval with the same safeguards as part 3 §B.

## 2026-10-23T20:23:33Z MODEL SWITCH for the judge and grader

Owner: Opus 5 may be filtering; use Opus 4.8 or GPT-5.6 Sol. **This is a deviation: the judge and grader
were changed after the Opus 5 results were seen, so every result under the new judge is EXPLORATORY.**

- **Probe:** 4 single-attempt calls, not cached (Opus 4.8 and Sol, each on one Opus-5-skipped W pair and
  one skipped k10 grade).
- **Claude Opus 4.8 becomes the primary judge and grader**: full relabel of W (3,731) and k10 (3,155), same
  safeguards as Opus 5; its runs write the main results files.
- **Opus 5 labels** are kept as a recorded secondary (`*.opus5.json`).
- **GPT-5.6 Sol, cross-vendor check only:** every Opus-5 no_label or failed item (W 94, k10 40) plus a
  stratified sample (seed 261023) of 400 W pairs and 400 k10 grades from the items Opus 5 labelled. Its
  files (`*.sol.json`) are meaningful only for their cross_judge block.
- **cross_judge block** in both results files: per-model distributions, the filter test on the
  Opus-5-skipped items, and κ (no_label excluded; unparseable maps to no / incorrect).

**20:35:23Z, probe results.** Opus 4.8 answered the W item (3 tokens) and was empty on the k10 item.
Sol answered both. Sol returns HTTP 400 for `temperature`, so temperature is omitted for Sol: **its runs
are not temperature-0.**

**21:38:40Z, model-switch runs complete.**
- Opus 4.8, W: 0 no_label, 1 HTTP error (that pair recorded as failed, = "no").
- Opus 4.8, k10: 40 no_label (records d053, d062, m006, m136), 0 failures. **30 grades are unparseable:**
  Opus 4.8 sometimes opens with prose instead of the one-word label. Per the prereg they count as
  incorrect; the parsing rule was not changed after seeing this.
- Sol, both subsets: the first 4 in-flight requests of each run hit the temperature 400 and were re-asked
  at 1 in flight without temperature.
- Final rescoring with Opus 4.8 as primary was cache-only (0 gateway calls).
