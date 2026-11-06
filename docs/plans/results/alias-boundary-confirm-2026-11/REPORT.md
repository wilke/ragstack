# Report: does a boundary-respecting chunk lose *answers* labels beyond judge noise?

Confirmatory test, alias study follow-up, run 2026-11-06. This is the reader's version: hypothesis, short
results summary, how it was tested, results, conclusion. The record it reads from is
[RESULTS.md](RESULTS.md) (the decision sentence, every table, the procedural notes) and
[`artifacts/confirm-report.json`](artifacts/confirm-report.json). How the test pairs were made is in
[TEST-DATA.md](TEST-DATA.md). Deviations are in [DEVIATIONS.md](DEVIATIONS.md). Unlike those record files, this
report is revised in place, as a paper is, and must read as finished whenever it is read.

## 1. Hypothesis

In the alias study, retrieval and judging used fixed 512-token windows, cut blind to sentences and sections.
The chunking the project intends to adopt cuts within sections at sentence boundaries (owner, 2026-11-05).
An exploratory control saw the
frozen LLM judge withdraw *answers* from such a chunk far more often than from the original window: 18.7 % vs
4.0 % on pairs both Qwen judges had agreed answer. That contrast was chosen after the fact, so it needed a
fresh, pre-registered test.

**H (one-sided):** on fresh pairs whose window both Qwen judges agreed *answers*, the frozen judge labels the
largest-overlap boundary-respecting chunk *not* more often than it labels the identical window *not* when it
re-reads it. The re-read measures the judge's own test-retest noise on the same pairs.

The test was frozen before any data were drawn: PREREG v3.2, sha256 `b3a2d9e6…`, signed tag
`prereg-alias-boundary-confirm-2026-11` at `016fb61`.

## 2. Results summary

**Confirmed.** All six validity gates pass.
- **Rates.** On 351 fresh pairs (218 questions), the judge withdrew *answers* from the new chunk on 75 pairs
  (21.4 %). It withdrew *answers* from the re-read window on 16 pairs (4.6 %).
- **Difference.** 16.8 percentage points, where one point is about 3.5 of the 351 pairs. The 90 % interval
  is 12.9 to 20.8 points.
- **Test.** Exact one-sided sign-flip over questions, p = 1.2 × 10⁻¹¹.
- **Where the excess sits** (descriptive, not tested): pairs whose new chunk keeps less than 80 % of the
  original window's text. Where it keeps at least 80 %, the two passages lose the label equally often (4.8 %
  vs 4.1 %).
- **Where the answer went** (step-7 cover-any descriptive): in 53 of the 75 lost pairs (70.7 %), another
  chunk overlapping the window still answers. In 18 (24 %), no overlapping chunk answers on its own.

## 3. How it was tested

- **Pairs.** 351 question–window pairs were drawn by a frozen, seeded rule from the 1,871 stage-A pairs that
  both Qwen judges had agreed *answers* and that the exploratory control had not used. The draw took:
  - 117 pairs per corpus arm (base, neighbour, random);
  - at most one pair per question per arm, and one per window.

  Full generation chain: [TEST-DATA.md](TEST-DATA.md).
- **Two passages per pair.**
  - **Control:** the document re-chunked by `sentence/512/0` (section cuts, a 512-token target, a 640-token
    ceiling); the pair's passage is the new chunk with the largest character overlap with the window.
  - **Retest:** the same window, unchanged, with a byte-identical prompt.
- **Judge.** The alias study's frozen judge labelled both:
  - Qwen3.6-35B-A3B (C) and Qwen3.8-27B (E);
  - Gemini 4 Argon breaks their ties;
  - Claude Opus 5.5 reads a 5 % drift sample and sets no label.

  It never saw which arm a pair was in, and the arm judged first alternated by backend.
- **Test.** The per-question score is the number of the question's pairs that lost the label only in the
  control, minus the number that lost it only in the retest. The test is an exact one-sided sign-flip over
  questions at α = 0.05, with exact McNemar as a sensitivity.
- **Gates.** Six gates, read before the p:
  1. at least 316 judged pairs;
  2. at most 5 % unjudged per arm;
  3. draw and registry integrity;
  4. chunker and environment;
  5. complete arms;
  6. input hashes equal the committed artifacts.
- **Power.** The design had a simulated power of .81 at the realised 351 pairs, for the planning effect of
  13 % vs 6 % (assumed). Its minimum detectable excess at 80 % power is 7.5 points.

## 4. Results

**The primary table** (`primary.table`):

| | new chunk keeps *answers* | new chunk *not* |
|---|---|---|
| **re-read window keeps *answers*** | 267 | **68** |
| **re-read window *not*** | **9** | 7 |

| quantity | value |
|---|---|
| pairs / questions / papers | 351 / 218 / 337 |
| new chunk *not* | 75 of 351 = 21.4 % (Wilson 95 % 17.4–26.0) |
| re-read window *not* | 16 of 351 = 4.6 % (2.8–7.3) |
| difference d = (68 − 9) / 351 | 16.8 points; 90 % question-cluster bootstrap 12.9–20.8; Tango 13.1–20.8 |
| sign-flip p (primary) / McNemar p (sensitivity) | 1.2 × 10⁻¹¹ / 1.2 × 10⁻¹² |
| unjudged pairs | 0 and 0 |
| gates | 6 of 6 pass |

**By corpus arm** (secondary): base 20.5 % vs 2.6 %, neighbour 19.7 % vs 6.0 %, random 23.9 % vs 5.1 % (117
pairs each).

**Checks on the judge** (descriptive):
- **No cache reuse.** The Qwen reasoning matched stage A's word for word on 0 of 351 re-read pairs per
  backend, so the re-reads were not cached answers.
- **Stable re-reads.** On the re-read windows, C kept its stage-A verdict on 98.3 % and E on 94.0 %.
- **No drift with time.** Lost labels (b / c) in the first vs second half of labelling: control 33 / 5 vs
  35 / 4, retest 37 / 4 vs 31 / 5.
- **Opus drift reference.** It agreed with the final label on 16 of 18 control pairs and 15 of 18 retest
  pairs.

**Where the excess sits** (descriptive, not tested):

| share of the window's text in the new chunk | pairs | new chunk *not* | re-read *not* |
|---|---|---|---|
| ≥ 80 % | 145 | 4.8 % | 4.1 % |
| 50–80 % | 182 | 30.8 % | 5.5 % |
| < 50 % | 24 | 50.0 % | 0 % |

In documents with one untitled section, where section cuts cannot move a boundary, 0 of 67 new chunks lost
the label (re-read: 2 of 67).

**Where the answer went** (step 7, cover-any; descriptive, not tested; `cover_any` in
`confirm-report.json`). For each of the 75 pairs whose new chunk lost *answers*, the judge re-read every new
chunk overlapping the original window (174 chunks):

| outcome of the re-read | pairs |
|---|---|
| another overlapping chunk answers; the max-overlap chunk still does not | 53 (70.7 %, Wilson 95 % 59.6–79.8) |
| no overlapping chunk answers on its own | 18 (24.0 %) |
| the max-overlap chunk now answers (judge noise), no other does | 4 |
| both | 0 |

The re-read reproduced the max-overlap chunk's *not* on 71 of 75. This descriptive reads the same judge and
single-chunk prompt; it says where the answer text sits, not whether retrieval would find it.

## 5. Conclusion

- **What is shown.** Under single-passage judging, the control chunking as produced (its cut positions,
  coverage and shorter chunks together) makes the frozen judge withdraw *answers* on about one in five
  passages it had confidently accepted. That is 16.8 points beyond its own re-read noise. The exploratory
  finding holds on fresh pairs; descriptively, the excess appears in each corpus arm.
- **What is not shown.** The test bundles where the cut falls, how much of the window survives, and chunk
  length.
  - The descriptives point to coverage: when the new chunk keeps most of the window, little is lost beyond
    noise.
  - The cover-any descriptive points the same way: in 7 of 10 lost pairs a neighbouring chunk still
    answers, and in about a quarter no single chunk does. Answer text moved to, or split across, other
    chunks is the reading these descriptives support. It is not tested here, and respecting boundaries is
    not shown to make a chunk worse in itself.
- **What it means for retrieval.**
  - This test judged passages, not retrieval. Whether the neighbouring chunk that holds the answer would be
    retrieved in the top 10 is a found@10 question that needs a retrieval run on the new chunking. It could
    rank higher (shorter, more focused) or lower (it may lose the heading or the question's terms).
  - The labels are the judge's, not human relevance judgements. The parent study's judge-validity flag
    (owner–judge agreement 0.872 against 0.90) applies.
- **Practical reading.** If re-chunking moves or splits answer text, as the descriptives suggest, an
  evaluation that judges single passages would register it as a loss even where the answer is still in the
  collection. Overlap between chunks, or judging a chunk together with its neighbours, are the mitigations
  to test next, beside the retrieval run.
