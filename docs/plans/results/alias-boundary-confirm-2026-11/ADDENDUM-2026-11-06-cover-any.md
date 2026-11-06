# Addendum: the cover-any descriptive (PREREG §5 step 7), 2026-11-06

Descriptive, not tested. It adds to [RESULTS.md](RESULTS.md), which stays as committed.

- **Source.** The `cover_any` block of [`artifacts/confirm-report.json`](artifacts/confirm-report.json),
  re-run with `--cover-config` (`recorded_at` 2026-11-06T15:08:28Z), and
  [`artifacts/map-cover.json`](artifacts/map-cover.json) (`recorded_at` 14:42:44Z, after step 6's commit b607bb4 at
  14:36:45Z).
- **The step-6 report is unchanged.** The step-6 version of `confirm-report.json` is the one committed in #979
  (`recorded_at` 14:34:20Z). The re-run recomputed the primary table, found it identical, and added only
  `cover_any` and its `judge_files` counts. `primary` and `outcome` (Confirmed) did not change.
- **Gateway.** The cover batch ran under [DEVIATIONS.md](DEVIATIONS.md) D-2: Argon 16 and Opus 9 calls, all
  `ok`.

## What was measured

The input was the 75 pairs whose max-overlap control chunk the judge labelled *not* (and not unjudged). For
each, every control chunk overlapping the original window, up to 6 per window, was judged alone with the
same frozen judge and prompt: 174 chunks in all, and no window reached the cap.

## Result

| outcome | pairs of 75 |
|---|---|
| another overlapping chunk answers; the max-overlap chunk again does not (`other_only`) | **53** (70.7 %, Wilson 95 % 59.6–79.8) |
| no overlapping chunk answers on its own (`none`) | 18 (24.0 %) |
| the max-overlap chunk answers on the re-read, no other does (`primary_only`) | 4 |
| both (`both`) | 0 |

- **Re-read agreement.** The re-read reproduced the max-overlap chunk's *not* on 71 of 75 (94.7 %, Wilson
  95 % 87.1–97.9).
- **Routes.** All 75 came through the C-and-E agreement route (`by_route.agree`); none was tiebroken.
- **Comparison (exploratory).** The exploratory control's corresponding figure was 24 of 28.

## Reading

In about seven of ten lost pairs, a neighbouring chunk of the new chunking, read alone, still answers.
In about a quarter, no single chunk carries it, consistent with answer text split across a cut. This is a
statement about where the judged answer sits under single-chunk judging. It does not say whether retrieval
would rank that neighbouring chunk in the top 10, which needs a found@10 run on the new chunking.
