I've fixed every finding in the review. The test file now passes 94 of 94 (65 before), and `ruff check` on the package and the test file is clean. Nothing was committed and git state is unchanged. Edits are only in the harness package, the test file, the example config and HARNESS-SPEC.md.

| ID | Fix | Test(s) |
|---|---|---|
| 1a | Both parts done. The audit counts how often the normalised quote occurs in its gold record and flags `within_record_repeat` when it is more than once; those items go in the ambiguous set (the noise subset excludes them too). Primary coverage is now the best coverage over every occurrence of the quote. | `test_audit_flags_within_record_repeat`, `test_audit_run_writes_within_record_repeat_and_score_treats_it_as_ambiguous`, `test_primary_coverage_is_max_over_all_quote_occurrences` |
| 1b | Offsets that fail verification, where the chunk text also isn't found, now give no coverage. The hit is still counted as `offsets_unverified`. | `test_offsets_failing_verification_give_no_coverage` |
| 1c | Questions whose gold record text is missing are dropped from the paired set and counted. `score` exits 2 and `decide` refuses. | `test_questions_without_record_text_are_dropped_and_score_refuses` |
| 1d | `score.json` now shows, per arm, the share of hits with an empty `source_path` and the unlocated / `offsets_unverified` counts and share. Above `score.max_unlocated_share` (default 0.01), `score` exits 2 and `decide` refuses. `noise` refuses too, so margins can't come from broken scoring. | `test_hit_quality_reports_empty_source_path_and_unlocated_per_arm`, `test_unlocated_share_above_threshold_refuses`, `test_noise_refuses_on_scoring_refusals` |
| 3a | Words are split on whitespace, edge punctuation is stripped, internal `-` and `.` are kept, and case is folded Unicode-aware. Other internal punctuation such as `/` splits a word. | `test_words_keep_hyphenated_names_and_unicode`, `test_copied_run_does_not_split_hyphenated_names` |
| 3b | Each ledger row stores the passage sha256 and the model. Rows that don't match the current passage or model, including old rows with no sha256, are ignored. | `test_ledger_rows_ignored_when_passage_text_or_model_changes` |
| 2a | New `decide.min_stratum_n` (default 30). Smaller strata skip the stratum guard and are listed in `strata_not_tested` and in decision.md. | `test_stratum_guard_skips_strata_below_min_n`, `test_min_stratum_n_default_and_listed_in_decision` |
| 2b | `decide` refuses when the margins differ from noise.json, when noise.json or the audit is missing, or when score reported refusals. The new `--force-unchecked` flag overrides this; the overridden checks are written to `forced_unchecked` in decision.json and flagged in decision.md. | `test_decide_refuses_margin_mismatch_unless_forced`, `test_decide_refuses_without_noise_json_or_audit` |
| 3c | New `questions.max_attempts` (default 3). After that a call is final and the passage is selected from its completed calls. Passages still waiting on calls go to `pending.jsonl` with their strata. Pending passages and articles skipped at the passage stage now appear by stratum in `skip_loss` and in a decision.md table. | `test_exhausted_call_is_final_and_selection_uses_completed_calls`, `test_skip_loss_counts_pending_and_passage_stage_by_stratum` |
| 3d | Before each append, a partial last line is truncated; a complete row that only lost its newline gets the newline added. | `test_append_after_truncated_tail_keeps_ledger_readable` |
| 503 | A 503 with `Retry-After` waits (capped by the new `api.max_retry_after_s`, default 120) and retries, up to `api.max_503_waits` (default 5) times. A 503 without the header is still a failure. | `test_retrieve_503_with_retry_after_waits_and_retries` |
| Interleave | All arms are called for one question before the next question. Each arm keeps its own ledger. | `test_retrieve_interleaves_arms_per_question` |
| Version and settings | `GET /v1/version` is called once per run. Its fields (or the error) and the request settings, minus the query, are stored in every retrieve row. | `test_retrieve_records_server_version_once_and_request_settings` |
| Pinned settings | `retrieve` refuses unless `rerank`, `use_graph` and `retrieval_mode` are set in `api.request_extra`. All three names are RetrieveRequest fields in contracts/openapi.yaml. | `test_retrieve_refuses_without_pinned_request_settings` (once per key), `test_pinned_names_are_retrieve_request_fields_in_openapi` |
| Check-writer set | `retrieve` now includes `questions.check.jsonl` (not in the noise subset). `score.json` has a separate `check_writer` block, and decision.md has one secondary line; it never feeds the decision. | `test_check_writer_set_is_retrieved_and_scored_as_separate_report`, `test_retrieve_also_calls_check_writer_questions` |
| Accent folding | Journal names get NFKD and combining marks removed before matching. | `test_journal_names_fold_accents` |
| Tokenizer label | The new `score.tokenizer_label` is used if set; otherwise only the model's basename is written, never a path. | `test_tokenizer_label_is_explicit_or_basename` |

New config keys, documented in HARNESS-SPEC.md and added to the example config:
- `questions.max_attempts`
- `score.max_unlocated_share`
- `score.tokenizer_label`
- `decide.min_stratum_n`
- `api.max_503_waits`
- `api.max_retry_after_s`

**Behaviour changes to note:**
- **Example config:** it now sets `request_extra` to `{"rerank": false, "use_graph": false, "retrieval_mode": "hybrid"}` as placeholders. You need to choose the real values for the eval.
- **Existing ledgers:** question-ledger rows written before this change have no sha256, so they are ignored and those calls will be redone. Nothing has been generated yet, so this shouldn't matter.
- **Passage-stage skips:** these are counted under `section_type = "None"`, because a skipped article has no section type.

**What I could not do:** nothing in the list was left out. Two things from the review weren't on your list, so I didn't do them: the missing lock file against two concurrent `questions` runs, and the duplicate-PMCID manifest-order note. Nothing was tested against a live server; every HTTP call in the tests goes to a fake.
