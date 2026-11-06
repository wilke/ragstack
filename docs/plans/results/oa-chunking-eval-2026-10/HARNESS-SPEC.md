# OA chunking eval harness: spec (2026-11-04)

Implements PLAN-AND-PASS-RULE.md (with Addendum A; the 2026-11-05 freeze options of Addendum B are
config keys, see `score.exclude_noise_subset`, `decide.stratum_correction`, `decide.tie_break`). Code lives in this repo under
`python/scripts/eval/oa_chunk_eval/`; all eval data (samples, passages, questions, answers,
ledgers, results with text) is written to a PRIVATE output directory given on the command line,
never into the repo. No hostnames, ports, keys or private paths in code defaults: everything
comes from a JSON config file passed with `--config` (example with placeholders committed as
`examples/oa-chunk-eval.config.example.json`).

## Config (JSON)
- `out_dir`: private output root.
- `manifest`: OA manifest JSONL (keys incl. pmcid, journal_xml, body_chars, year if present, doi_xml, licence).
- `include_pmcids`: file with one PMCID per line = the bacteria/virus definition (built elsewhere).
- `corpus_root`: root with `clean/xx/yy/PMC….xml` (path = sha1(pmcid)[:2]/[2:4]).
- `sample`: {n_eval_articles, n_background, seed, strata: {journal_family_map, length_bands, year_bands}}.
- `passages`: {chars: 1200, per_article: 1, section_targets: [...], fallback_body: false}. With
  `fallback_body` true, an article whose target regions give no passage is retried over all of its
  text (type `body`, row flag `fallback: true`); added 2026-11-04 (owner) for unheaded letters.
- `writer`: {url, model, n, temperature, top_p, max_tokens, extra_body (e.g. chat_template_kwargs), max_in_flight: 4}.
- `check_writer` (optional second-family set): same shape, plus `fraction`.
- `api`: {base_url, api_key_file, top_k: 10, endpoint: "/v1/retrieve", request_extra, max_503_waits: 5,
  max_retry_after_s: 120}. The key is read from the file at call time, sent only as `X-API-Key`, never
  logged or written. `request_extra` MUST set the RetrieveRequest fields `rerank`, `use_graph` and
  `retrieval_mode` explicitly (`retrieve` refuses otherwise), so server defaults cannot drift between the
  noise and main runs. A 503 with `Retry-After` waits (capped at `max_retry_after_s`) and retries, up to
  `max_503_waits` times, before it counts as a failure.
- `arms`: list of {name, collection_id, cost_class: cheap|mid|expensive, baseline: bool, cost_rank (optional
  integer, lower = cheaper; set on every arm or on none; used by the `paired_ladder` tie-break, which falls
  back to cost class then config order when it is absent)}.
- `stop`: {max_failures: 20, max_consecutive: 5, pause_file}.
- `questions`: {max_attempts: 3}: attempts per writer call (across runs) before the call is final; selection
  then uses the passage's completed calls.
- `score`: {ks, coverage: 0.90, offsets: verify, tokenizer, tokenizer_label, max_unlocated_share: 0.01,
  bootstrap_iters, bootstrap_seed, strata_dims, report_dims, skip_loss_threshold: 0.10}. `tokenizer_label`
  is the name written to outputs (default: basename of the tokenizer model, never a path).
  `max_unlocated_share`: per arm, the largest allowed share of hits with an empty `source_path`, and of
  gold-record hits that are `unlocated` or `offsets_unverified`; above it `score`, `noise` and `decide` refuse.
  `exclude_noise_subset` (default false = the rule before 2026-11-05): when true, the questions of the noise
  subset (selected by `noise.noise_questions`, the noise run's own function, seed and size) are flagged
  `noise_subset` per question and left out of the primary set (`clean_excl_noise`, or `all_excl_noise`
  without an audit); the set with them (`clean` / `all`) is still scored and named `sensitivity_set` (a
  labelled sensitivity result, not the decision).
- `decide`: {k: 5, alpha_one_sided: 0.025, stratum_point: -2.0, context_ratio: 1.5, min_stratum_n: 30}.
  Strata with fewer paired questions than `min_stratum_n` are not tested by the stratum guard and are listed
  as not tested (the owner sets the value at freeze).
  `stratum_correction`: `"none"` (default, the rule before 2026-11-05: the stratum's 95% CI) or `"bonferroni"`:
  the guard uses the two-sided (1 - 0.05/k) paired bootstrap interval, k = strata the guard tests for that arm
  (n >= `min_stratum_n`, across all `strata_dims`); the point condition is unchanged.
  `tie_break`: `"cheapest"` (default, the rule before 2026-11-05) or `"paired_ladder"` (see `decide`).

## Subcommands (one CLI, `python -m oa_chunk_eval <cmd> --config C`)
1. `sample`: filter manifest to include_pmcids; normalise journal names into families; stratify by
   journal family x length band (year reported, not crossed); draw eval articles and a disjoint background
   set; deterministic from seed. Writes `sample.jsonl` (pmcid, role eval|background, strata) and
   `shard.jsonl` (pmcid, xml_path) for jats_extract.
2. `passages`: read the extracted JSONL (records with content_type article only, for eval articles),
   cut one ~1,200-char passage per article at a sentence boundary from a target section region; record
   pmcid, record path, char start/end within the record text, sha256 of the passage.
3. `questions`: for each passage call the writer n times (OpenAI-compatible chat completions,
   stream false, always max_tokens). Ask for JSON {"question","answer_quote"}. Programmatic checks, no model
   judging: quote is an exact substring of the passage; question 25-400 chars; quote 10-300 chars; no copied
   run of 4+ consecutive words from the passage in the question (words: whitespace split, edge punctuation
   stripped, internal `-`/`.` kept, Unicode casefold); self-contained regex (reject
   this/the study|paper|article|authors|text|passage, "according to"). Select the passing candidate with the
   lowest echo (word overlap with the passage). Passages with no passer go to `skips.jsonl` with stratum.
   Ledger `questions.ledger.jsonl` is append-only and checkpointed: a rerun resumes, a pause file stops new
   calls after in-flight ones finish; stop after max_failures total or max_consecutive in a row; one retry after
   30 s on an empty reply. Each row carries the passage sha256 and the writer model; rows that do not match
   the current passage text or model are ignored. A call is final after `questions.max_attempts`; passages
   with calls still open go to `pending.jsonl` with stratum. A partial last line left by a crash is repaired
   before the next append.
4. `audit`: gold-label audit. For each selected quote, check whether the quote text (normalised whitespace)
   also occurs in another eval or background record (exact search over the extracted JSONL). Flag
   `ambiguous_gold`; also flag `within_record_repeat` when it occurs more than once in the gold record. Both
   are scored separately (ambiguous set).
5. `retrieve`: for every question x arm, call the API retrieve endpoint for that collection_id with
   top_k; store ranked hits (source_path, chunk content, start_char, end_char, score) in
   `retrieval/<arm>.jsonl`. Checkpointed and resumable; same stop rules; max 4 in flight. Arms are
   interleaved per question. `GET /v1/version` once per run; the version and the request settings are stored
   in every row. The check-writer questions are retrieved too.
6. `score`: per question x arm x k in {1,5,10}:
   - primary hit: the union of top-k chunks whose source_path equals the gold record path covers
     >= 90% of the gold quote's characters (use start_char/end_char offsets when present and verified; fall
     back to locating the chunk content inside the record text; offsets that fail verification give no
     coverage). When the quote occurs more than once in the gold record, coverage is the maximum over all
     occurrences. Questions whose gold record text is missing are dropped and make `score` refuse;
   - secondary strict hit: one chunk contains the whole quote;
   - context tokens per query (sum of top-k chunk lengths; tokenizer name in config, char/4 fallback flagged).
   Outputs per-arm hit@k, paired differences vs the baseline arm with paired bootstrap 95% CI
   (10,000 resamples, seeded), per stratum and pooled; discordance rate per arm pair; journal-confusion
   (share of wrong top-1 chunks from another article of the same journal family); per-arm hit quality
   (empty `source_path` share, `unlocated` / `offsets_unverified` counts); skip loss by stratum including
   pending passages and articles skipped at the passage stage; the check-writer set as a separate secondary
   report. With `score.exclude_noise_subset` the primary set leaves out the noise subset and the set with it is
   reported beside it as the sensitivity set (`noise_exclusion` records the sizes).
7. `decide`: applies the pass rule from PLAN-AND-PASS-RULE.md Addendum A: cheap arms non-inferiority
   (lower CI >= -M_ni), mid superiority (>= +M_mid), expensive (>= +M_high); Holm across challengers;
   stratum guard (any reported stratum with point < -2.0 and CI upper < 0 fails); context-token rule
   (> 1.5x baseline needs the next class's margin); tie-break cheaper ingest then fewer context tokens.
   Options (owner 2026-11-05, defaults keep the rule above):
   - `decide.stratum_correction: "bonferroni"`: the stratum guard's interval is the two-sided (1 - 0.05/k)
     paired bootstrap interval, k = strata tested for the arm. `decide` recomputes the per-stratum bootstrap
     from `per_question.jsonl` with the same rows, order, iterations and seed as `score` (so its 95% bounds
     equal `score.json`'s; stratum sizes are cross-checked). k, the level and every tested stratum's bounds
     are written per arm (`stratum_guard_detail`) and in the Markdown summary.
   - `decide.tie_break: "paired_ladder"`: candidates = baseline + passing challengers, ordered by `cost_rank`
     (fallback: cost class, then config order); the cheapest is the incumbent; each costlier candidate
     replaces it only if the paired bootstrap lower bound of (candidate - incumbent) on primary hit@k, at
     the pass rule's level (two-sided 1 - 2 x `alpha_one_sided`), is > 0. Every step (pair, n, point, lo,
     hi, switched) is written to `ladder` and the Markdown summary. The context-token rule still applies to
     pass/fail first.
   - With `score.exclude_noise_subset`, the same rule is also run on the sensitivity set and written as
     `sensitivity` (pass/fail per arm, the arm it would choose, its ladder), labelled as not the decision.
     A config whose `exclude_noise_subset` differs from the one `score` ran with is a refusal.
   Margins come from config (set after the noise run). Refuses when the margins differ from `noise.json`,
   `noise.json` or the audit is missing, or `score` reported refusals, unless `--force-unchecked` (recorded
   in `decision.json`). Writes `decision.json` and a Markdown summary with aggregates only (no question
   text), safe to commit.
8. `noise`: on a question subset and two arms (baseline + one cheap arm), compute split-half spread of the
   baseline, discordance d, CI half-width at the planned N, and the margins per Addendum A
   (M_ni = max(2.0, half-width), M_mid = max(2.0, half-width), M_high = 2 x M_mid). Writes `noise.json`.

## Tests (pytest, no network)
- Unit tests for every check in `questions` (substring, lengths, 4-word run, regex, echo selection).
- Coverage scoring with offsets and with the content-search fallback, including a quote split across two chunks.
- Paired bootstrap CI determinism with a seed; Holm; the decide rules on synthetic numbers.
- Ledger resume and stop rules with a fake writer.
- The sampler is deterministic and keeps eval and background disjoint.

## Non-goals
No ingest (done by the operator with ingest-bulk.cwl), no LLM grading in v1 (subset grading comes later),
no changes outside `python/scripts/eval/oa_chunk_eval/`, its tests and the example config.
