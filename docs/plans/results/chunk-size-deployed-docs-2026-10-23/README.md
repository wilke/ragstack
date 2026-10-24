<!-- experiment: Chunk size on a deployed documentation collection | 2026-10-23 | EXPLORATORY -->
# Chunk size on a deployed documentation collection (2026-10-23)

**Status: EXPLORATORY. Not citable.** The results were produced by a harness that was not committed when
it ran, and the primary LLM grader was changed after its first results were seen. See
[RESULTS.md § Threats to validity](RESULTS.md#threats-to-validity).

| | |
|---|---|
| Report | [RESULTS.md](RESULTS.md) |
| Pre-registrations (redacted) | [prereg/](prereg/) |
| Deviations (redacted) | [DEVIATIONS.md](DEVIATIONS.md) |
| Public artifacts | [artifacts/](artifacts/) (counts, contrasts, ids; `export_manifest.json` has provenance) |
| Harness | `python/scripts/eval/deployed_docs_trial.py` (+ `section_aware_trial.py`), export `chunktrial_export_public.py` |

## The collection

A deployed collection of 336 public software-documentation pages (1,265 live chunks), read in place,
read-only, from its tenant's store. Its live chunking is semantic, with `chunk_size 512, chunk_overlap 64`
recorded in the manifest. The tenant and collection are not named in this record (owner decision,
2026-10-24).

## Overall goal

Find out whether chunk size, overlap and section-aware splitting change what the served pipeline hands a
model — on a real deployed collection rather than a benchmark corpus — and whether the collection's live
setting (semantic, nominally 512/64) is a good choice or should be rebuilt.

The served pipeline is fixed throughout: BM25 + SFR-Embedding-Mistral dense → RRF k=60 → cut to 50 →
bge-reranker-v2-m3 → top 10.

## Subquestions

| # | Question | Part |
|---|---|---|
| Q1 | Do the deployed assistant's seed test questions behave as expected? Do the positives find their pages? Does the top-1 score tell answerable questions from unanswerable ones? | Part 1 (31 seed questions: 18 in groups P and N, plus 13 how-to rewordings in group W) |
| Q2 | Does chunk size (128–1,024) or overlap (0 vs 1/8) change whether the answer passage gets delivered within a fixed token budget? Does splitting within sections help? | Part 2 (116 single-passage questions, budgets of 2,048 and 5,120 tokens) |
| Q3 | Does semantic chunking produce oversized chunks? That is, is `chunk_size` ignored without a token cap, and does adding a cap help retrieval? | Addendum S (reproduction plus SEM512 and SEM256 arms) |
| Q4 | Part 2 hit its ceiling. Do harder questions separate the arms? These are answers that need two passages far apart on a long page, and specific detail questions. | Part 3 (M: 105 questions, D: 65) |
| Q5 | For how-to questions with no exact gold, which arm delivers chunks that actually tell the user how to do the task? Can an LLM judge make that call reliably? | Part 3 B (group W, 3,731 question–chunk pairs; Qwen, Opus 5 and Opus 4.8 judges) |
| Q6 | In practice, with top 10 chunks passed to a model the way most systems do it (not budget-matched), which chunking gives the most correct answers, and at what context cost? | k=10 test (286 questions × 8 arms × 2 answering models, graded by Opus 4.8 / Qwen) |
| Q7 | Are the LLM graders reliable and unbiased? Do the Opus versions and GPT-5.6 Sol agree, and does any grader silently drop items? | Cross-judge and filter checks (κ, Sol subset, the no-answer items) |

Q3 and Q7 were not planned at the start: Q3 came from the owner's "potential bug" remark, Q7 from silent
empty responses from Opus 5.

## What is private, and where it stays

Kept off GitHub, with the outputs on the host that ran them:

- the eval set: every question text, gold quote, passage, the seed questions and their gold pages;
- model answers, chunk text and page names;
- the store URL and index name, endpoint hosts, and the stratum filename prefixes;
- the as-run harness file, the full pre-registrations and the deviation log.

Public here: counts, contrasts, label distributions, κ, context-token statistics, gateway usage totals,
and per-record rows keyed by question id (`g…`, `m…`, `d…`, and the seed ids `E1`…`H6`, `E1w`…`H5w`).
Document ids appear only as salted hashes (`d:…`). One stratum is relabelled `topic`.

**Residual link, accepted (owner decision, 2026-10-24).** This record names no tenant or collection, but
the repository's own status documents list which tenant runs a semantic collection, and this record has
to say the live chunking is semantic (that is the finding in Q3). A reader of both can infer the tenant.
The owner accepted that link: what must stay private is the eval set, and it does.

The public export is allow-listed and checked: every string must be a short token without a private
term, and no question, quote or seed text (nor any six-word run from one) may appear in any file. The
export refuses to write otherwise (`chunktrial_export_public.py`).

## Reproducing

All private values (store URL, index, endpoint hosts, seed questions, prompts with the product name, file
names, the export salt) live in one JSON file outside the repo, passed as `--private-config` or
`CHUNKTRIAL_PRIVATE_CONFIG`. Without it, both scripts exit. The public harness was checked to set every
moved constant to the as-run value from that file (65 module and 52 helper-module globals compared, plus
behavioural probes of the stratum and endpoint-deny functions).

```bash
export CHUNKTRIAL_PRIVATE_CONFIG=/path/to/private-config.json
PYTHONPATH=python python python/scripts/eval/deployed_docs_trial.py prepare
PYTHONPATH=python python python/scripts/eval/deployed_docs_trial.py run          # parts 1-2
PYTHONPATH=python python python/scripts/eval/deployed_docs_trial.py addendum-s
PYTHONPATH=python python python/scripts/eval/deployed_docs_trial.py part3 [--opus ...]
PYTHONPATH=python python python/scripts/eval/deployed_docs_trial.py k10   [--opus ...]
PYTHONPATH=python python python/scripts/eval/chunktrial_export_public.py
```

The gateway judge and grader runs need the gateway owner's approval and the safeguards in
[DEVIATIONS.md](DEVIATIONS.md) (`fallback_model ""`, an output-token stop, failure stops).
