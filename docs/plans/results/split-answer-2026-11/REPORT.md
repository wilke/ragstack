# Report: do judge-time neighbours recover split answers?

Exploratory, step 1 of the split-answer plan, run 2026-11-11. This is the reader's version: hypothesis, short
results, how it was tested, results, conclusion. The record it reads from is [RESULTS.md](RESULTS.md) and
[`artifacts/neighbour-report.json`](artifacts/neighbour-report.json); how the items were made is in
[TEST-DATA.md](TEST-DATA.md); the one deviation is in [DEVIATIONS.md](DEVIATIONS.md). Unlike those record files,
this report is revised in place and must read as finished whenever it is read.

## 1. Hypothesis

**The question.** Under the adopted chunking (sentence cuts inside sections, 512-token target), the confirmatory
boundary test found 75 question–passage pairs whose *answers* label was lost. For 18 of them, no single new chunk
that overlaps the old 512-token window answers the question: the answer seems split across chunks. Production can
already hand the answer model a chunk together with its neighbours (`context_window`). Does that recover the split
answers?

**What would count as yes** (PLAN §7; no pre-registered threshold): the frozen judge labels the neighbour passage
*answers* for the split pairs at a rate the controls do not show. That is, passages made longer by their neighbours
should keep the answers they had (low loss on kept pairs) and should not create answers where there were none (low
false *answers* on pairs the judge had said do not answer).

## 2. Short results

- **Split pairs:** with one neighbour on each side, the judge says *answers* for **8 of the 18** (44 %). The 2
  windows needing two neighbours each side: 0 of 2.
- **Negative control:** for pairs the judge had said do **not** answer, adding neighbours turns **35 of 150**
  (23 %) into *answers*. Either longer passages make the judge more willing to say *answers*, or a neighbour carries a genuine answer; the control was not split to tell these apart (§5).
- **Kept pairs:** 215 of 219 (98 %) stay *answers*. Neighbours cost few answers (4 of 219 flipped).
- **Verdict:** neither clear success nor clear failure. Recovery on split pairs (44 %, interval 25–66 %) sits above
  the false-*answers* floor (23 %, interval 17–31 %), but the intervals overlap (25 % against 31 %), so much of the recovery could be the
  same leniency. The owner stopped the plan here: no step 2, and no change to production.

## 3. How it was tested

- **Items.** 446 pairs from the confirmatory test's stage A, each re-judged with a new passage: the old pair's best
  overlapping S chunk M, with its neighbours before and after, in exactly the text format production's
  `context_window` builds (PLAN §3.1). Groups: the 75 lost pairs (split 18, recovered 53, primary_only 4), 219 of the 220
  kept_changed control pairs (one is a one-chunk document with no neighbours), M ± 2 for 2 split windows, and a seeded draw of 150 stage-A *not* pairs as the
  negative control. Details in [TEST-DATA.md](TEST-DATA.md).
- **Judge.** The frozen alias-study judge, unchanged but for the passage slot: two local Qwen judges (one sampled
  five times), a Gemini 4 Argon tiebreak where they disagree (28 items), and a Claude Opus 5.5 reference on a 5 %
  drift sample (22 items). Judging a neighbour passage is an exploratory extension of the frozen protocol (owner
  decision OPEN-1), so these labels are their own item type and are never pooled with chunk labels.
- **Blinding.** The counts-only read of the 10 wall-crossing split windows (step 0b) was made before any neighbour
  label existed. The report script reads only final labels and item groups, and publishes counts.
- **Statistics.** Rates with 95 % Wilson intervals. No test; this is descriptive.

## 4. Results

What one point means here: one percentage point of a group's rate is one hundredth of that group's items. For the
split group that is less than one pair (18 items); for the negative control it is 1.5 pairs (150 items).

| group | items | judged *answers* | rate (95 % CI) |
|---|---|---|---|
| split, M ± 1 | 18 | 8 | 44 % (25–66 %) |
| split, M ± 2 | 2 | 0 | 0 % (0–66 %) |
| recovered | 53 | 45 | 85 % (73–92 %) |
| primary_only | 4 | 4 | 100 % (51–100 %) |
| kept_changed control | 219 | 215 | 98 % (95–99 %) |
| **negative control (false *answers*)** | **150** | **35** | **23 % (17–31 %)** |

Nothing was left unjudged, so it does not matter whether unjudged items count as *not* or are dropped.

The step-0b read points the same way as the negative control. In 9 of the 10 wall-crossing split windows, the
reader judged that one side of the wall alone holds the answer; none needed both sides; one was unclear. So many
"split" pairs may not be split at all: the stage-A *not* on them may reflect judge variance or a missing bit of
context, not an answer cut in two.

## 5. Conclusion

Judge-time neighbours cost few answers (98 % of kept pairs stay *answers*; 4 of 219 flipped), but they also do not cleanly recover
split ones. The split-pair rate, 44 % (8 of 18 pairs), is less than twice the 23 % at which neighbours turn
non-answers into *answers*, and the two intervals overlap. At most about 8 of the 18 split answers are recovered, and
some of those may be the judge's leniency toward longer passages rather than a real recovery.

Decision (owner, 2026-11-11): stop at step 1. Step 2 (overlap with the strict sentence chunker, ceiling 8 split
pairs) does not run. Production `context_window` keeps its default; callers can already set it per request.

**Limits.** Exploratory and small: 18 split pairs give a wide interval. The negative control was not split into
same-paper and other-paper pairs; same-paper flips might be neighbours carrying a genuine answer, so 23 % may
overstate the leniency. Even 8 of 18 is an upper bound for production: in a multi-source answer prompt each source gets a share of the
budget and its context is trimmed first and the passage last (PLAN §3.1). One judge protocol and one corpus.
