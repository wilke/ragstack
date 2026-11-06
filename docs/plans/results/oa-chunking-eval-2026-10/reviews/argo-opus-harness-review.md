# Review: OA chunking eval harness, checked against HARNESS-SPEC.md and PLAN-AND-PASS-RULE.md (incl. Addendum A)

I read every module, the tests, the example config, `contracts/openapi.yaml`, and the ingest code that feeds this harness: `JsonlLoader`, `enrich`, `_make_chunk`, `_source_metadata` and `jats.py` record paths. I did not run anything and did not change any files.

**These parts are correct:** the union-coverage maths, the normalised-to-raw offset mapping, the paired bootstrap (including the fast multinomial path), the one-sided p and Holm, the non-inferiority and superiority signs, the stratum guard direction, the Addendum A margin formula, the in-flight cap of 4, the pause check before each new call, and the API key handling.

**Verdict: not ready for the noise run.** Four blocking items are listed at the end.

---

## 1. score.py: quote-coverage hit@k

**1a. A quote that appears twice in the gold record is scored by position for primary but by text for strict, and the audit never checks for it.** Wrong result.
- `score.py:120,137`: primary measures coverage of the single span `[quote_start, quote_end)`.
- `score.py:138`: strict counts any gold-path chunk whose text contains the quote, wherever it is.
- `audit.py:55`: the audit only searches records with `path != gold`. A repeat inside the same record is never flagged.
- **Failing input:** the record contains "…lysed in PBS buffer…" and later "…washed in PBS buffer…". The quote "in PBS buffer" (13 chars, which passes the 10-char minimum) comes from the second occurrence. A chunk holding only the first occurrence scores strict = 1 and primary = 0. Short quotes ("37 degrees", "P < 0.05", a strain name) repeat often in an article. This adds misses that depend on where each arm's chunk boundaries fall, and it inflates discordance, which feeds directly into the noise margins.
- **Fix:** in the audit, also count normalised occurrences inside the gold record and flag `ambiguous_gold` (or a separate `within_record_repeat`) when there is more than one. Alternatively, score primary as the maximum coverage over every occurrence of the quote in the record. Do one of these before the noise run.

**1b. `offsets_unverified` gives coverage from offsets that just failed verification.** Wrong result.
- `score.py:93-94`. The docstring (lines 11-12) says "A chunk that cannot be located contributes no coverage", but the code does the opposite.
- **Failing input:** `extracted` points at a re-extract whose text differs slightly from what was ingested. The text at `start..end` no longer matches the chunk, and neither exact nor normalised search finds the chunk. The code still returns `(start, end)`, so coverage is measured against shifted text.
- **Fix:** return `(None, "unlocated")`.

**1c. Questions whose gold record text is missing count as misses in every arm.** Wrong decision.
- `score.py:126,283`: when `rec is None`, every hit is labelled `other_record`, so primary = 0 in all arms. The questions stay in the paired set, and line 288 only counts them.
- **Effect:** concordant misses push N up and the difference toward 0, and they narrow the CI. That makes non-inferiority easier, so the error favours cheap arms. Strict ignores `rec`, so strict can be 1 while primary is 0.
- **Fix:** drop these questions from `complete` and fail (or refuse to run decide) when `records_without_text > 0`.

**1d. No check on located chunks or missing `source_path`.** Wrong decision.
- `retrieve.py:62` falls back to `""` when metadata has neither `source_path` nor `filename`. `score.py:126` then treats every hit as `other_record`.
- **Failing input:** one arm's collection was ingested by a path that doesn't stamp `source_path`. That arm scores 0 on every question, and decide quietly keeps the baseline (or a different winner). The `locate` counts are written but nothing reads them.
- **Fix:** report, per arm, the share of hits with an empty `source_path` and the counts of `unlocated` and `offsets_unverified`. Make `score` or `decide` refuse above a small threshold (for example 1%).

**These parts of score.py are fine:**
- `PMC123#table-2` records correctly never cover an article-record quote.
- `article_of` handles `#` paths.
- k cut-offs (`located[:k]`, `hits[:k]`) are correct.
- The interval merge in `coverage()` is correct.
- The `norm_ws_map` back-mapping is correct.
- Choosing the occurrence that overlaps the quote is defensible.
- Server offsets come from `doc.content[start:end]` (`chunkers.py:218-235`), so the `offsets` path should cover nearly every chunk.

## 2. stats.py, decide.py, noise.py

**2a. A tiny stratum can veto an arm.** Wrong decision.
- `decide.py:65-76` on top of `score.py:315`.
- **Failing input:** `journal_family="unknown"` has n = 1, the baseline hits and the arm misses. The point is −100. Every bootstrap resample is −100, so `hi` = −100, which is below 0, and the arm fails the guard. With n = 2 and both missed, the same happens. Owner-defined family maps and the "unknown"/"other" buckets make small strata likely.
- **Fix:** apply the guard only where the stratum has n ≥ some minimum (for example 30; the owner should set it at freeze). List smaller strata as not tested. Or use an exact or Wilson-type interval for small n.

**2b. `margins_match_noise` is computed but not enforced, and decide runs without the audit.** Robustness.
- `decide.py:243-246` records a mismatch but still decides.
- `decide.py:147-149` and `score.py:300` fall back to the `all` set when `audit.jsonl` is missing, with no warning.
- **Fix:** refuse (or require an explicit override) when the margins don't match `noise.json`, or when the audit is missing.

**Checked and correct:**
- `p_greater` tests H0: diff ≤ thr for both the non-inferiority threshold (−M_ni) and the superiority thresholds (+M).
- `ci_ok` is `lo ≥ thr` on the 2.5th percentile.
- Holm is the shared step-down function, applied across all challengers.
- The context rule uses `>` 1.5 (exactly 1.5 does not bump).
- The tie-break order is class, then `ingest_cost`, then context tokens.
- Margins: `M_ni = M_mid = max(2.0, hw at planned N)` and `M_high = 2·M_mid`, which matches Addendum A.
- The half-width scaling `hw_sub·√(n/N)` and `n_for_target` are correct.

**Policy points for the owner (not bugs):**
- The cheapest passing arm wins even if a mid arm is clearly better. That is the literal tie-break in Addendum A.
- An expensive arm over 1.5× context keeps +M_high (`no_next_class`).
- Margins come from the most similar pair (512/64 vs 512/0), so they understate the CI width for very different arms.
- `planned_n` should be the expected clean, paired N after skips and ambiguous items, not 2000.

## 3. questions.py and the ledger

**3a. The 4-word-run check splits hyphenated names and numbers into separate words.** Wrong result (selection and skip bias by stratum).
- `questions.py:46,53-64`: `[0-9a-z]+` turns "SARS-CoV-2 spike" into `sars cov 2 spike`, which is four "words".
- **Failing input:** the passage contains "SARS-CoV-2 spike protein" and the question is "How does the SARS-CoV-2 spike bind ACE2?". It is rejected for a 2-word copy. "E. coli K-12" is also four tokens. Non-ASCII letters are dropped ("β-lactamase" becomes "lactamase"). Virology passages will be skipped more often, so the question set itself will be skewed by stratum.
- **Fix:** split on whitespace, strip edge punctuation, keep internal `-`/`.`, and use Unicode-aware lowercase. Do this before generating any questions.

**3b. The ledger isn't tied to the passage text or the model.** Wrong result after any re-cut.
- `questions.py:202-204,225`: the key is `(passage_id, writer, i)`. `passage_id` is `PMC…#p0` whatever the text.
- **Failing input:** `passages` is re-run with a different `chars` or seed. The old `ok` rows, checked against the old text, are reused. Line 249 `.index()` then either raises (and the CLI reports it as a config error) or gives offsets for a question written about a different passage. A model change under the same writer name is reused the same way.
- **Fix:** store the passage sha256 and the model in each row, and ignore rows that don't match.

**3c. Passages that never complete are dropped silently, and so are articles skipped before question generation.** Wrong result (Addendum A.5 skip-bias reporting is incomplete).
- `questions.py:233-235`: a passage with any call that never succeeds (a persistent 400 or empty reply) stays `pending` forever. It is neither selected nor written to `skips.jsonl`.
- `score.py:173-187` counts only `skips.jsonl`.
- `passages.skipped.jsonl` (`no_fitting_passage` needs at least 900 contiguous chars in one sub-heading region) isn't counted either. That will hit the short-article stratum hardest.
- **Fix:** after a set number of attempts, treat the call as final and select from the completed calls. Add `pending` and passage-stage skips, by stratum, to `skip_loss` and `decision.md`.

**3d. A crash mid-append can make the ledger unreadable.** Robustness.
- `common.py:118-125` together with `common.py:94`.
- **Failing input:** the process is killed mid-write of a large retrieve row (10 chunks, tens of KB, so several write syscalls). The truncated last line is skipped on the next read, but the next `append_jsonl` writes straight after it. That line is now malformed and no longer last, so `iter_jsonl` raises and the ledger can't be resumed.
- **Fix:** in `append_jsonl`, write `"\n"` first if the file doesn't end with one, or truncate the partial tail.

**Checked and correct:**
- Exact case-sensitive substring check, the length bounds, the self-contained regex (matches the spec's list exactly), echo with a lowest-echo tie on index.
- Pause before each new call, while in-flight calls finish.
- More than 20 failures in total across runs, back to the last reset; 5 in a row.
- One empty-reply retry after 30 s.
- `stream: false` and `max_tokens` can't be overridden.
- In-flight capped at 4.

There is no lock file, so two concurrent `questions` runs would put 8 calls in flight (minor).

## 4. sample.py

- **Determinism and disjointness are correct.** Ranks are sha256-based, eligible IDs are sorted, and background is drawn from what is left after the eval set. Largest-remainder allocation is correct.
- **`sample.py:33,36-38` (robustness, wrong stratum):** `[^0-9a-z]` deletes accented letters. "Microbiología" becomes "microbiolog a", so a pattern like `microbiolog(y|ia)` misses and the article lands in `other`. **Fix:** apply NFKD and strip combining marks before normalising.
- **`sample.py:107-110` (minor):** for duplicate PMCIDs the first manifest row wins, so the result depends on manifest order. Fine as long as the manifest file doesn't change.

## 5. retrieve.py, checked against /v1/retrieve

- **Correct:** `{query, top_k, collection}`; reserved keys can't be overridden by `request_extra`. `source_path`, `start_char` and `end_char` are read from `metadata`, matching `_source_metadata` (`query.py:749-766`) and `EnrichedDoc.source_path`. `degraded` counts as a failure by default, as the contract recommends.
- **Key handling is correct.** The key is read per call from the file and sent only as `X-API-Key`. Exceptions record only the type name, the key is never in a row or log, and the test asserts it never reaches any output file.
- **`retrieve.py:85-86` (robustness):** a 503 from a dormant or restoring collection carries a Retry-After header, which is ignored. Four or five 503s per arm across six arms exceeds 20 total failures, so the run needs `--reset-failures` before it can continue. **Fix:** warm up each collection first, or treat 503 with Retry-After as wait-and-retry, not a failure.
- **`retrieve.py:134-146` (robustness):** all questions run for arm 1, then arm 2, and so on, so any drift in the server or reranker over time lines up with arm. The noise-subset rows are also reused in the main run even if they were fetched weeks earlier. **Fix:** interleave arms per question, and record the server version and request settings in each row.
- **`retrieve.py:43-46` (robustness):** `rerank`, `use_graph` and `retrieval_mode` aren't pinned; the server defaults apply. **Fix:** set them explicitly in `request_extra` so graph pseudo-chunks or a default change can't slip in between the noise and main runs.
- **Spec gap:** `retrieve.py:118` reads only `questions.jsonl`. The check-writer set is generated and audited but never retrieved or scored, so it has no effect on any result.

## 6. Eval text leaking into decision.json / decision.md

No question text, quotes, collection IDs or question IDs reach either file. One small leak: `score.py:157` writes `str(kw.get("model"))` as the tokenizer name, and `decide.py:162,179` copy it into both files. If `score.tokenizer` is a dict pointing at a local model path, a private path ends up in files meant to be committed. **Fix:** write only a basename or an explicit label.

---

## Verdict: not ready for the noise run

**Blocking (these change the metric or the question set that the margins will be measured on):**
1. **1a:** repeated quotes inside the gold record. Add the audit check, or score the maximum over all occurrences.
2. **1b:** remove the `offsets_unverified` fallback.
3. **1c + 1d:** exclude questions with no record text, and fail on empty `source_path` or a high unlocated share.
4. **3a and 3b, before any questions are generated:** fix the copied-run tokenisation, and key the ledger to the passage sha256 and the model.

**Fix before decide or freeze:** 2a (minimum n for the stratum guard), 3c (complete skip-loss reporting), 2b (enforce matching margins and require the audit).

**Fix soon (robustness):** 3d (ledger tail repair), the 503/Retry-After handling, interleaving arms, pinning the request settings, the accent folding in journal names, and the tokenizer label leak.

The tests pass on their own fixtures, but none of them covers 1a–1d, 2a, 3a–3d or the 503 case.
