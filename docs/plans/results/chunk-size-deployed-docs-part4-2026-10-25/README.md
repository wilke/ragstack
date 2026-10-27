<!-- experiment: Chunk size on a deployed documentation collection, part 4 (confirmatory) | 2026-10-25 | CONFIRMATORY · PROVISIONAL -->
# Chunk size on a deployed documentation collection, part 4: the confirmatory test (2026-10-25)

**Status: CONFIRMATORY for H1–H4, PROVISIONAL.** The model-graded results are final as computed. The
pre-registered human validation of the grader is in progress (2–3 graders), and the record will be
updated when it lands. Addendum Q is EXPLORATORY.

| | |
|---|---|
| Report | [RESULTS.md](RESULTS.md) |
| Lab book (status, timeline, decisions, what's next) | [LABBOOK.md](LABBOOK.md) |
| Pre-registrations (redacted) | [prereg/PREREG-part4-confirmatory.md](prereg/PREREG-part4-confirmatory.md), [prereg/PREREG-part4-addendum-Q.md](prereg/PREREG-part4-addendum-Q.md) |
| Deviations and decisions (redacted) | [DEVIATIONS.md](DEVIATIONS.md) |
| Public artifacts | [artifacts/](artifacts/) (`export_manifest.json` has provenance) |
| The exploratory parts 1–3 | [../chunk-size-deployed-docs-2026-10-23/](../chunk-size-deployed-docs-2026-10-23/README.md) |
| Code | `python/scripts/eval/deployed_docs_part4.py`, `chunktrial_stats.py`, `part4_addendum_q.py`, `part4_human_import.py`, `part4_grading_page/`, export `part4_export_public.py` |

## Overall goal

Decide, with a pre-registered test on new questions, whether the deployed collection should be rebuilt
with smaller, capped chunks. The exploratory parts 1–3 suggested it should. Those parts had three
weaknesses:
- the grader was changed after results were seen;
- 66 contrasts went uncorrected;
- one model family generated, answered and graded.

Part 4 re-tests only what the rebuild decision needs, and fixes all three.

## Subquestions

| # | Question | Where |
|---|---|---|
| H1 | Do 512-token chunks give more correct answers than 128-token chunks? | confirmatory |
| H2 | Is semantic chunking capped at 512 tokens no worse than the live, uncapped semantic chunking? | confirmatory |
| H3 | Are 512-token chunks no worse than 1,024-token chunks? | confirmatory |
| H4 | Are fixed 512-token chunks no worse than live? | confirmatory |
| S1 | Do the answers change when the budget is smaller (2,048 tokens), or when a fixed 10 chunks are passed, as deployed systems do? | secondary |
| S2 | Do single-passage questions replicate? | secondary |
| S3 | Is the primary grader (GPT-5.6 Sol) reliable, and does a second grader (Opus 4.8) agree? | secondary |
| Q | Are the two-passage reference quotes actually answer-bearing, and do H1–H4 hold on the questions whose quotes are? | addendum Q, exploratory |
| V | Do human graders agree with the model grader? | prereg §5, **pending** |

## The collection

This is the same collection as parts 1–3: 336 public software-documentation pages (1,265 live chunks),
read in place and read-only. The tenant and collection are not named in this record (owner decision,
2026-10-24). The residual link through the repository's status documents was accepted then, and still
applies. See the [part 1–3 record](../chunk-size-deployed-docs-2026-10-23/README.md#what-is-private-and-where-it-stays).

## What is private, and where it stays

These stay off GitHub, on the host that ran them:
- the eval set: every question, reference quote and passage;
- model answers, chunk text and page names;
- the human grades and labels, and the blinding keys;
- the private config (hosts, store, prompts, salt);
- the full pre-registrations and the deviation log.

Public here: counts, contrasts, statistics, κ, token statistics, gateway usage, and per-record labels
keyed by question id (`M…k…`, `S…`). Page ids appear only as salted hashes. One stratum is relabelled
`topic`.

The export is allow-listed. It refuses to write if any string is not a short safe token, contains a
private term, or contains a six-word run from any question or quote.
