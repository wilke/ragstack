<!-- experiment: Rechunk found@10, stage 1: is the answering chunk retrieved? | 2026-11-07 | EXPLORATORY -->
# Rechunk found@10, stage 1: is the answering chunk retrieved under the new chunking?

**Status: RUN 2026-11-06/07, stage 1b 2026-11-09/10 — exploratory, descriptive ([REPORT.md](REPORT.md); record: [RESULTS.md](RESULTS.md), [stage-1b addendum](ADDENDUM-2026-11-10-stage1b.md)).**

The follow-up to the confirmatory chunk-boundary test ([`../alias-boundary-confirm-2026-11/`](../alias-boundary-confirm-2026-11/README.md)).
It asks whether, under the new chunking (`sentence/512/0`, section cuts), a chunk the judge labelled *answers*
is in the question's top 10, for the 75 pairs that lost their label and for all 351 pairs. Four bench
collections were built on the non-production tenant; they are kept until the owner's teardown go.

| file | tier | what |
|---|---|---|
| [PLAN.md](PLAN.md) | record | the stage-1 plan, committed before any retrieval (#982) |
| [REPORT.md](REPORT.md) | reader's report, revised in place (not record) | hypothesis → results summary → how it was tested → results → conclusion |
| [RESULTS.md](RESULTS.md) | record | provenance, every figure with its artifact field, the reading under PLAN §5 |
| [TEST-DATA.md](TEST-DATA.md) | record | how the pairs, labels and collections were made, with counts |
| [ADDENDUM-2026-11-10-stage1b.md](ADDENDUM-2026-11-10-stage1b.md) | record | stage 1b: the 671 unlabelled top-10 chunks of the 75 lost pairs judged; A 75/75 (was a lower bound, 42/75), A_own unchanged |
| [DEVIATIONS.md](DEVIATIONS.md) | record, append-only | departures from the plan and the live-ops plan, including the image-derived exports (D-5) and the stage-1b gateway (D-9) |
| [artifacts/stage1.json](artifacts/stage1.json) | record | the stage-1 report (`rechunk_stage1.py`), counts and intervals only |
| [artifacts/build-summary.json](artifacts/build-summary.json) | record | the four collections as built: counts, checks, hashes, library versions |
| [artifacts/stage1b-seed-summary.json](artifacts/stage1b-seed-summary.json) | record | the stage-1b seed (`rechunk_prep.py stage1b-seed`): slot and seed counts, input and output hashes |
| [artifacts/stage1b-report.json](artifacts/stage1b-report.json) | record | the stage-1 report re-run on the merged label table (`rechunk_stage1.py --run-label-table`), counts and intervals only |
| [make_build_summary.py](make_build_summary.py) | record | the generator of `build-summary.json` (reads the private ops directory; refuses paths, hosts, ids) |
