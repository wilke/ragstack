<!-- experiment: Glossary-model benchmark, round 1 | 2026-10-28/29 | EXPLORATORY -->
# Glossary-model benchmark, round 1 (2026-10-28 to 2026-10-29)

**Status: EXPLORATORY.** Nothing was pre-registered. This folder is a lab book plus counts-only
artifacts. It is not a confirmatory record, and it is not paper text. A pre-registered round 2 would
get its own record.

The question: which model should write the symbol glossary that is attached to chunks? Four arms were
compared: rules, Llama-4-Scout, Qwen3.8 and Qwen3.6. Each was scored on four corpora for coverage,
precision against a gold sample, and retrieval.

| | |
|---|---|
| Lab book (design, chronology, results, threats, next steps) | [LABBOOK.md](LABBOOK.md) |
| Counts-only artifacts | [artifacts/](artifacts/) |
| papers-OC retrieval (external) | OpenChia's write-up, `docs/reviews/bench-glossary-261029.md` in OpenChia's episode-study repository, merge 257a27e. External pointer, not fetched by us. |
| Raw data (private) | wilke/ragstack-experiments, glossbench-2026-10/ (commit: PENDING) |
| Code | `python/scripts/glossary_step.py`, `python/scripts/backfill_symbol_glossary.py`, `python/scripts/eval/deployed_docs_part4.py` with `--glossary-file` (#856) |

## Artifacts

Each file opens with an `artifact_provenance` block. The block gives:
- the sha256 of the private source file;
- whether the file is citable, and why;
- the private-data pointer.

The analysis files also keep the `provenance` dict their run recorded.

| file | what | citable |
|---|---|---|
| [intrinsic.json](artifacts/intrinsic.json) | step 6: coverage, gate drops, cost and backfill dry-run counts, per corpus × arm | **no**: scratch analysis script |
| [agreement.json](artifacts/agreement.json) | step 6: agreement between the models on canonical symbol keys | **no**: scratch analysis script |
| [docsA-found.json](artifacts/docsA-found.json) | step 7: docs-A retrieval-only re-score, counts only | **yes**: clean tree, db97c28 |
| [gold-r1.json](artifacts/gold-r1.json) | gold precision under rubric v1, superseded; kept for the rubric history | **no**: scratch analysis script |
| [gold-r3.json](artifacts/gold-r3.json) | gold precision under rubric v3, the final analysis | **no**: scratch analysis script |

**Why most of them are not citable.** The step-6 and gold analyses ran from scratch scripts outside the
repository, not from a clean tree. Under the provenance rule in `docs/papers/README.md`, that makes them
`citable: false`. They can be reported in the lab book, but no claim may point at them.

The step-7 harness ran at db97c28 with `dirty: false` and `untracked: 0`, so its run is citable. The
committed copy has the per-item and stratum fields removed. The private full result has sha256 prefix
`86ff4551`.

**How the public copies were made.** They were produced mechanically from the private files:
- corpus keys were renamed to the aliases;
- every path, host and endpoint field was dropped;
- `precision_by_corpus_majority` was turned from `"a/b"` strings into `{correct, n}`.

None of them holds a symbol, a definition, a judge's reason, a document id, a question id or a title.
That was checked by listing every string value in every file.

## What is public, and what is private

**Public here:**
- counts, rates, κ and confidence intervals;
- model ids;
- glossary version strings;
- sha256 hashes of the private files;
- OpenChia's transcribed record counts and sign tests.

**Private** (in wilke/ragstack-experiments, glossbench-2026-10/, or on the host that ran them):
- the corpus shards and the glossary files, with every symbol and definition;
- the gold sample, the judge prompts with their document text, and the judge labels and reasons;
- the owner's labels;
- the docs-A question set and its per-item results;
- the run logs, which name hosts, indices and collections;
- the scratch analysis scripts.

OpenChia's gold set and per-record results stay with OpenChia.

**Aliases.** The record names no tenant, collection, host or product. The aliases are:
- **docs-A:** the software-documentation collection of the part-4 study;
- **papers-OC:** OpenChia's paper collection;
- **OA-1000:** the 1,000-article open-access sample;
- **ASM-1000:** the 1,000-article journal sample.
