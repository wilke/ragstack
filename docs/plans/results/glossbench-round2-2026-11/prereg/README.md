# Pre-registration, glossary-model benchmark round 2

**Status: DRAFT v1.3 (2026-10-29). NOT FROZEN.** No eval paper has been sampled, no question generated,
no gateway glossary call made. Every freeze precondition is met (below); the text awaits the owner's go.

| file | tier | what |
|---|---|---|
| [PREREG-glossbench-round2.md](PREREG-glossbench-round2.md) | record (frozen on the owner's go) | the design: hypotheses, sets, arms, measures, analysis, stops, code pin, decisions |
| [PLAN-round2.md](PLAN-round2.md) | **lab book** (dated, superseded-not-deleted) | ordered steps, owners, budget estimates, timeline — not a design constraint |
| [power_sim.py](power_sim.py), [power_sim.json](power_sim.json) | planning aid | the stdlib power simulation behind prereg §7's table; seed 20261029; re-running the script reproduces the JSON byte for byte (checked 2026-10-29). A design assumption, not a measurement; the run's own table comes from `found10_analysis.power_table` |

## Freeze procedure

The part-4 procedure (`../../chunk-size-deployed-docs-part4-2026-10-25/prereg/`), with one difference
stated in step 4:

1. The owner says go, in writing, naming the commit of this directory the go applies to. The prereg is
   frozen **as written** — its draft header included; no edit is made to mark it frozen.
2. In a clean checkout of that commit, `sha256sum PREREG-glossbench-round2.md` and a UTC timestamp
   (`date -u +%FT%TZ`) are appended to a **private** `PREREG.sha256` kept with the outputs, off GitHub,
   **before** any eval paper is sampled, any question is generated or any gateway glossary call is made.
3. The same sha goes into the private config (`prereg.file` = the committed prereg, `prereg.sha256`): the
   drafter refuses to start on a mismatch (`glossbench_r2_draft.py`, hard stop `prereg_sha_mismatch`, exit
   1, checked at every stage and resume).
4. One commit records the freeze **in this README only**: the status line becomes `FROZEN <sha> at <UTC>
   on the owner's go (commit <sha of the frozen text>)`. **The prereg file is not touched, not even for a
   "frozen" note** — the runner hashes `prereg.file` whole, so a note at its top would make the committed
   file fail its own check; part 4 could annotate its public copy because the hashed original was a
   private file, here the public file is the original. The prereg's sha256 on `main` therefore equals the
   recorded sha for as long as the record stands. Any later change is a `DEVIATIONS.md` entry; the one
   pre-stated exception is the set-P promotion addendum (below).
5. The PR carrying the freeze commit is merged; its merge commit is the freeze record's commit. The first
   eval-paper sample, question or gateway call happens after that merge.

Preconditions, **all met** on `main` at `b5251db` (2026-10-29; checked by re-reading every module, flag,
config key and reason code the prereg names against that tree, re-running `power_sim.py`, which
reproduced `power_sim.json` byte for byte, and running the #867/#868 unit tests, 178 passed):

| precondition | merge commit |
|---|---|
| harness: pure/empty arms, shuffle, wording proxy (#861); pool and drafter (#863); runner, analysis, swap, gold tools (#864); chunk export (#865); reranker harness (#862) | `e3002ba` (the v1 pin) |
| **#867** (`exp/r2-manip-checks`): glossary-boost behavioural probe (`glossary_boost_probe.py`, in `found10.py` and `reranker_test.py`; prereg §5 M1), the reranker test's inactive-reranker gate, Holm with gated or absent members at p = 1.0 as the default with `holm_drop_gated` as the labelled sensitivity (prereg §7, D18) | **`48d5c73`** (commits `e96c722`, `8076265`) |
| **#868** (`exp/r2-setp-fields`): set P's field-list drafting mode, the partner adapter (`export-partner`, `import-partner-verdicts`), the pilot (`--pilot N --papers K`), the per-set stratum quotas (`pool.per_set`; S3 on P = 6, D16), the `n_terms == len(terms)` assertion on import (`6ae6804`), the abbreviation-like term reject `term_abbreviation` with bracket stripping (`300f0eb`, `159db38`), `KNOWN_FIELD_LISTS` making 13/13 the default split for the v2 file (prereg §3.4, D14–D16) | **`b5251db`** (commits `d52efef`, `6ae6804`, `300f0eb`, `159db38`) |
| the owner's decisions D1–D18 folded in (prereg §11) | this directory |

Every run is at or after `b5251db`. The field list is the partner's binding export **v2**
(`binding_fields_261029_v2.json`, sha256 `1c377891…`, 9,445 bytes); v1 (`08309d0f…`) is superseded —
truncated term lists for two fields — and kept only for provenance.

After the freeze, exactly one addendum may be appended to this README: the set-P promotion decision of
prereg §3.5 (D17), *promoted* or *not promoted*, quoting the imported pilot verdict summary. Its terms
are fixed in the prereg; nothing else is ever appended here.

## What changed from the v0 draft (2026-10-29, same day)

Substance, not wording:

- **Frontier arm fixed to GPT-5.5**; the reason (empty long-generation answers from Opus 5.5 through the
  gateway, 14 of 60 in the OA chunking pilot vs 1 transport-level empty in 1,522 short grading calls)
  now cites the committed addendum-Q artifacts and labels the pilot ledger as private.
- **Drafter Gemini 2.5 Pro, `max_tokens` 4096; verifier Claude Opus 5.5 via the CLI** (off-gateway),
  with the exact CLI invocation and the context-isolation check from the code.
- **Stamp grammar** written out (`<base>[+merge=<mode>]@file/1[+shuf/1:<seed>:<perm12>]`) and the
  `none+merge=llm-only` case for documents with no LLM version; the contract test named.
- **OA reach under pure** stated with its estimand consequence (≈ 10,965 of 12,560 OA units are float
  units with no LLM half; dry run 777 with entries / 11,783 empty; O is article-unit-dominated).
- **Value-statement statistical stoplist** (pinned `STAT_TOKENS`) added to the pool rule.
- **Symbol collision is a flag to the verifier, not a reject**, with the reason (the check reads the
  rules extractor's sentences).
- **One redraw after a layer-1 reject; every attempt logged**; record ids carry the attempt number.
- **Layer-1 reason codes** replaced by the implemented table; the verifier sees the first 2,000 characters
  of the paper (not "title/abstract").
- **Wording proxy vs the partner's reference**: the two behavioural differences (first definition per
  normalised symbol; dropped ≤ 3-character terms plus a field-name term) stated with every proxy figure.
- **Shuffle keying**: ES `_id` in the backfill, `chunk_id` in the proxy and `part4-glossary`; same
  assignment, different `permutation_sha256`.
- **Arm label `qwen36p-shuf`** throughout; conditions as the runner requests them (`top_k 50`,
  `rerank_candidates 50`, `use_graph false`); `server_assumed` multiplier/RRF k; `base` gated when
  identical to `norerank`.
- **Owner page**: fresh storage key, "Missed symbols" line dropped.
- **Gateway**: the partner set no cap and no end date; the per-batch notice stays; our own pause at
  5.0M in / 0.75M out / 4,000 calls (was 6.0M / 1.0M / 4,000 with a 5.0M notice).
- **Set P**: the partner's acceptance pass is undecided → each set carries a status label; all sets run
  on our host; the partner's 155 may be held privately, never publicly.
- **Holm**: a gated H2 enters at p = 1.0; shrinking the family is a logged config deviation (was "F
  shrinks to 3").
- **Decisions section** replaces the separate decisions file.

## v1.1 — review of 2026-10-29 (same day)

- **Set P redesigned around the partner's answer (prereg §3.4, D4, D14–D16):** the partner accepts P in a
  sampled form — scripted checks on every record plus a judged random sample of 50 with its seed; P's
  label is *scripted-clean, judged sample of 50, error rate X% (95% CI …)*, printed beside every P
  number; P is never called equivalent to the 155-record set. P's drafter is restricted to the partner's
  fresh binding export `binding_fields_261029.json` (26 fields, sha in the prereg; 13 covered / 13
  uncovered asserted; 4 constants capped at 2; the 10 new-ground fields at 2 : 1; definition-words
  questions with a terms-leak guard; value as printed, dimension-checked). A ≈ 50-record pilot from 2–3
  papers runs after freezing as a format check and is data for no hypothesis. S3 on P is named as the
  stratum of interest with its honest size (≈ 50 records, exploratory). The P addendum deadline moves
  from "before P's first retrieval" to "before any P record is drafted".
- **Glossary-boost behavioural probe** required in `found10.py` before freezing (ships at 0.0, not in
  `/v1/config`); a failed probe is a hard stop. The inactive-reranker rule is named as already in code.
- **Holm is always over four**; a gated member enters at p = 1.0 and the family is never shrunk after a
  result is seen (the v1 text allowed a logged shrink to Holm over 3).
- The 2,234-field count is labelled a review measurement, not a committed artifact.

## v1.2 — final pre-freeze edits (owner decisions of 2026-10-29)

- **D16 closed: S3 on P is 6 per paper** (≈ 150 records over 25 papers before rejects), through #868's
  per-set override `pool.per_set = {"P": {"s3_per_paper": 6}}`; O and A stay at 2; S3 stays exploratory.
- **D17: promotion of set P into the confirmatory family is allowed by one bounded addendum** (prereg
  §3.5). Fixed now so the addendum has no free choice: it adds exactly H1-P, H3-P and H4-P (H2 is not
  mirrored); Holm goes from 4 to 7 with gated members at p = 1.0; the condition is a Wilson 95% upper
  bound ≤ 0.15 on the partner's judged error rate over the pilot (≥ 30 judged records; at n = 50 at
  most 2 rejects), the owner-accepted default taken from the layer-3 validity bar; the deadline is
  before any non-pilot P record is drafted; if the condition fails P stays a replication with no further
  option. Stated plainly as one post-pilot decision point, a disclosed forking path bounded by the rule.
- **D18: Holm with gated or absent members at p = 1.0** is the code's default (#867); `holm_drop_gated` is
  a sensitivity only.
- **Binding export re-pointed at v2** (sha256 `1c377891…`, 9,445 bytes; same 26 fields, units and
  definitions; `air_exchange_rate_s` 12 → 14 terms, `deactivation_rate_s` 12 → 13; `n_terms ==
  len(terms)` for every field). v1 is superseded: its truncation would have let "ACH" questions pass the
  term-leak guard for the most ACH-heavy field.
- **Set-P identifiers aligned to #868's code**: `term_leak` (was `terms_leak`), `unit_incompatible` (was
  `unit_dimension_mismatch`), the dimension-bridge flag (was `different_dimension`; named
  `unit_dimension_differs` in v1.3), `expect_in_set`, `constant_cap`, `weight_new_ground`; a field
  outside the file is structurally unplannable and refused, `field_not_in_list` in the partner export.
- **Both #867 and #868 are freeze preconditions** (D13); v1.1 had #868's content as a pilot precondition
  only.
- The §5 M1 probe is described as #867 implements it (token-based candidates, five verdicts,
  `--boost-declared`).

## v1.3 — freeze-readiness check against `main` at `b5251db` (2026-10-29)

Text only; no hypothesis, measure, test, threshold, set, arm or decision changed:

- **Both code PRs are merged** — #867 at `48d5c73`, #868 at `b5251db` — so every "required before
  freezing" clause in the prereg (header, §3.4, §5 M1, §9 second table, D13) and here now states the
  merge commit instead; the pin is `b5251db`. The `n_terms == len(terms)` assertion is in code
  (`6ae6804`), so its "required before the freeze" sentence is gone.
- **Set-P identifiers corrected to the merged code**: `term_leak` rejects at a share **of at least 0.5**
  (`term_leak_threshold`, not `leak_threshold`, which is the open-mode `lexical_leak` key), measured over
  the question's content words after removing stop words, question words, the definition's words and the
  stated case's words; a second reject, **`term_abbreviation`**, fires when the question contains an
  abbreviation-like term as a whole token (brackets stripped; `300f0eb`, `159db38`); the unit flags are
  `unit_dimension_differs` / `unit_unknown` / `field_unit_unknown` (the prereg's "bridge" named the
  concept, not a code identifier); `--in-set-records` is the pool `build` flag; the 13/13 split is the
  loader's default for the v2 file's sha (`KNOWN_FIELD_LISTS`) and cannot be skipped.
- **Shuffled-arm probe**: the private config's `corpora.<c>.es_id_prefix` is required for the permutation
  re-derivation (`8076265`).
- **Freeze record**: the sha is recorded in this README only, never in the prereg file, because the
  runner hashes `prereg.file` whole (procedure step 4; part 4's public copy could carry the sha because
  its hashed original was private).
