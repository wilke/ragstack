<!-- experiment: Split answers, step 1: judge-time neighbours | 2026-11-11 | EXPLORATORY, RECORD -->
# Results, step 1: judge-time neighbours (option b)

**Record file, append-only. Exploratory: no threshold was pre-registered (PLAN §7), and every rate here is
descriptive.** Source: [`artifacts/neighbour-report.json`](artifacts/neighbour-report.json) (`split_answer_prep.py
report`, counts only, offline; `provenance.commit` `e8b94454`, `dirty: false`, `citable: true`). The reader's
version is [REPORT.md](REPORT.md); the gateway deviation is [DEVIATIONS.md](DEVIATIONS.md) D-1.

## What was judged

446 neighbour items in one registry, as PLAN §5 (b) and [`artifacts/neighbour-items.json`](artifacts/neighbour-items.json)
fix them. Each item is a stage-A pair whose passage is the max-overlap chunk M with its neighbours, in the production
context-window format (PLAN §3.1): M ± 1 for every item, except M ± 2 for the 2 split windows that M ± 1 does not
contain. The frozen judge (Qwen C n = 5 and E, Argon tiebreak, Opus 5 % drift reference) labelled every item;
`final_rows` 446, `items_without_final_row` 0, unjudged 0. Because nothing is unjudged, the two rate policies of the
report (`unjudged_as_not`, `unjudged_dropped`) give identical numbers, and one column is shown.

## Rates (share of items the judge labelled *answers*; 95 % Wilson interval)

| group | what it is | items | *answers* | rate | 95 % CI |
|---|---|---|---|---|---|
| split, M ± 1 | lost pairs no single S chunk answers (the target) | 18 | 8 | 44.4 % | 24.6–66.3 % |
| — of which both neighbours present | | 17 | 8 | 47.1 % | 26.2–69.0 % |
| — of which at a document edge | | 1 | 0 | 0 % | 0–79.4 % |
| split, M ± 2 | the 2 windows M ± 1 does not contain | 2 | 0 | 0 % | 0–65.8 % |
| recovered | lost pairs another S chunk answers | 53 | 45 | 84.9 % | 72.9–92.2 % |
| — both neighbours / edge | | 50 / 3 | 43 / 2 | 86.0 % / 66.7 % | |
| primary_only | lost pairs answered only by the primary S chunk | 4 | 4 | 100 % | 51.0–100 % |
| kept_changed (control: do longer passages lose answers?) | | 219 | 215 | 98.2 % | 95.4–99.3 % |
| — both neighbours / edge | | 183 / 36 | 179 / 36 | 97.8 % / 100 % | |
| **negative control** (stage-A *not* pairs; *answers* here is a false *answers*) | | **150** | **35** | **23.3 %** | **17.3–30.7 %** |

The negative control's 150 items are the seeded draw of PLAN §3.2 (b) ([`artifacts/negative-draw.json`](artifacts/negative-draw.json)):
about two-thirds same-paper and one-third other-paper *not* pairs, all with neighbours on both sides. The report
does not break the false-*answers* rate down by that stratum, and this record does not either.

## Context from step 0b (made before any neighbour label existed)

The blind read of the 10 wall-crossing split windows ([`artifacts/wall-read-counts.json`](artifacts/wall-read-counts.json)):
needs text from both sides of the section wall 0, one side suffices 9, unclear 1.

## Calls

All requests ran 2026-11-11 UTC. Qwen C and E: 446 each (local). Gateway: Gemini 4 Argon 28 (tiebreak), Claude
Opus 5.5 22 (drift reference), all answered, no re-issue; the figures and the gateway used are in DEVIATIONS D-1. The
Opus drift sample (22 items) is below the harness's 100-pair minimum for a drift alarm, so drift was not evaluable;
this is stated, not measured (`judge-summary.json` is private).

## Against PLAN §7's step-1 rule

PLAN §7: "If split pairs recover at a rate the controls do not show (low kept_changed loss and low false recovery
on the OPEN-2 *not* pairs), report and stop." The kept_changed loss is low (4 of 219). The false recovery is not
low: 35 of 150 (23.3 %). The split recovery (8 of 18, 44.4 %) lies above it, but the two intervals meet (24.6 % lower
bound against 30.7 % upper bound). The result is neither the clear success nor the clear failure the rule names.

## Owner decision (2026-11-11)

Asked whether to run step 2 (strict + 512 vs strict, cover-any, on the lost 75; ceiling 8 split pairs), to stop, or
to split the negative control by stratum first, the owner chose **stop here**. Step 2 does not run. Under OPEN-5
(PLAN §9.1) a weak result changes nothing: the production default `context_window` stays as it is, and callers can
still set it per request.
