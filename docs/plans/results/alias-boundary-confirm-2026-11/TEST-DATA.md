# How the test data was generated

This document follows a test pair from the source papers to the 351 pairs the confirmatory test judged. Each
stage names the committed artifact or prereg section its numbers come from. The private files (questions,
texts, chunk ids, labels) stay off the repository.

A **test pair** here is *one question + one passage*. The question comes from the alias study's question set.
Each pair has two versions of the passage: the 512-token window the alias study retrieved and judged, and the
chunk that a boundary-respecting chunker produces at the same place in the same document.

## Stage 1 — the papers and their text (alias study)

Source: the alias study's deck slides 4–5 (`docs/presentations/alias-study-2026-11.html`), filled from its
committed artifacts; alias RESULTS.md.

- **Base corpus, OA-1000.**
  - 1,000 open-access articles, as JATS XML from PubMed Central, giving 12,560 documents (articles plus their
    table and figure units). How the 1,000 were drawn is not stated in the study record.
  - **Text extraction:** abstract and body, with titled sections kept as markdown heading lines (`## Title`,
    deeper for nested sections); no reference list.
  - Tables and figures are lifted out as their own unit records: label, caption and grid.
- **Added papers.** Chosen from OpenAlex metadata only:
  - **neighbours:** the 5 most-cited references and the 5 most recent citers of each study paper;
  - **two random draws:** papers matched to the neighbours on topic, year, article type, chunk length and
    PDF share.
  - Each added arm has 2,423 papers.
  - **Text:** PMC JATS where a copy exists, otherwise the licensed open-access PDF. PDF text has no headings
    and keeps its reference list, which is a known difference.
  - **Screens:** for control characters and for repetition.
- **Corpus arm.** Every passage belongs to one arm, set by its paper: **base** (OA-1000), **neighbour** or
  **random**.

## Stage 2 — the questions (alias study)

- **Drafting and verification.**
  - From a seeded pool of 857 eligible papers (532 activated in waves), Gemini 2.5 Pro drafted questions,
    each written from one **gold chunk**.
  - Every question was checked mechanically, then verified by Claude Opus 5.5 (Codex on 54).
  - 1,091 questions were accepted, on 408 papers. They were built blind to retrieval.
- **Two kinds of question.**
  - **G:** the question names a concept by its full term, but the answering passage carries only the paper's
    short form (a symbol or an abbreviation).
  - **C:** the passage already carries the term.
- A question is a **record**; the confirmatory test clusters by record.

## Stage 3 — the 512-token windows and the judged pairs (alias study, stage A)

- **Chunking.** Every corpus was chunked by `fixed_token/512/0`: a fixed 512-token window with no overlap,
  blind to sentences and sections. Tokens are counted in the `Salesforce/SFR-Embedding-Mistral` tokenizer.
  These chunks are the **windows**. The base corpus has 32,765 of them.
- **Embedding and retrieval.**
  - Each corpus was embedded once (SFR dense).
  - Retrieval took the top 10 under three retrievers:
    - the served default, a BM25 + dense hybrid reranked by `BAAI/bge-reranker-v2-m3`;
    - the same hybrid without the reranker;
    - BM25 alone.
  - **Stage A** ran 19 retrieval legs over all 1,091 records: the 5 Layer-1 attachments under each
    retriever, plus 4 noise repeats.
- **Judging.** A retrieved (question, window) pair was judged only when needed: non-gold windows in the top
  10 when the gold chunk was not there, plus the pairs a rank metric needed.
  - The frozen judge reads the question and that one window, with the paper title and the window's glossary
    entries. It answers one question: does this passage, read alone, state the core of the answer?
  - **C** = Qwen3.6-35B-A3B (5 samples at temperature 0.7) and **E** = Qwen3.8-27B (1 sample).
  - When C and E disagree, Gemini 4 Argon breaks the tie. Claude Opus 5.5 reads a 5 % drift sample and never
    sets a label.
- **The result.** 9,205 deduplicated judged pairs, committed as the stage-A registry on 2026-11-04 (commit
  sha256 `f53ca9d0…`, `../alias-study-2026-11/artifacts/stage-a-commit.public.json`).

## Stage 4 — the exploratory control, and what it removed from the pool

Source: `../alias-study-2026-11/control/artifacts/control-draw.json`, `control-chunk.json`; the addendum.

- **The draw.** After the alias study, a 300-pair sample was drawn from the stage-A registry by stratum:
  - 150 pairs where C and E agreed on *answers*;
  - 99 where they agreed on *not*;
  - 51 tiebroken.

  Each was re-judged on a boundary-respecting chunk, and later on its own window again.
- **What it found after the fact.** On the agreed-*answers* stratum, 28 of 150 control chunks lost the label
  against 6 of 150 re-reads (18.7 % vs 4.0 %). That contrast is what this test confirms.
- **Excluded from the pool.** Its 300 pairs, and every window they used, are excluded, so the confirmatory
  pairs are fresh.

## Stage 5 — the eligible pool (PREREG §4.1–§4.2)

Source: `artifacts/draw.json` (`counts`), recounted by `draw-confirm` at the draw. The run refuses unless
every count equals the frozen table.

| filter | pairs |
|---|---|
| stage-A judged pairs | 9,205 |
| … where C and E agreed (no tiebreak) | 8,580 |
| … and agreed on *answers* | 2,064 (base 816 / neighbour 542 / random 706) |
| minus the exploratory control's 150 *answers* pairs | 1,914 |
| minus 43 pairs whose window is a control pair's window | **1,871** (748 / 481 / 642) |

- **Shape of the pool.** The 1,871 pairs come from 262 questions and 1,050 papers, on 1,734 distinct windows.
- **Cells.** A (question, arm) combination is a **cell**. There are 470: 198 base, 154 neighbour, 118 random.

**Why agreed *answers* only?** These are the pairs where both judges were sure that the window answers. A
lost label on them is the clearest sign that something about the passage changed, not the judge's
indecision.

## Stage 6 — the draw (PREREG §4.3)

Source: `artifacts/draw.json`. Drawn 2026-11-06 04:52:41Z, at the frozen tag.

- **Rule.** 351 pairs, 117 per arm. Within each arm the pairs are visited in a seeded order: sha256 of
  `261105|alias|confirm|draw|<arm>|<pair id>`, ascending.
- **Caps.** A pair is taken only if:
  - its question has no pair yet in that arm (one pair per question per arm, so at most three per question);
    and
  - its window is not yet taken in any arm (one pair per window).
- **Realised draw.**
  - 351 = 117 / 117 / 117 on the first pass, with no reallocation.
  - 218 questions: 124 in one arm, 55 in two, 39 in three.
  - 337 papers; no paper holds more than 2 pairs.
  - 0 control pairs and 0 shared windows.
- **Committed first.** `draw.json` was committed before anything was chunked.

## Stage 7 — the boundary-respecting chunks (PREREG §5 step 2)

Source: `artifacts/chunk.json`.

- **Resolution.** Each of the 343 documents behind the drawn pairs was resolved to its record text. As a
  check, every stage-A window of the document must equal the text at its offsets; 0 mismatches.
- **Chunker.** Each document was then chunked as `sentence/512/0`, with descriptor
  `{"min_piece":128,"pack":"318","size_tolerance":0.25,"size_unit":"tokens","structure_cuts":"sections/1"}`:
  - Punkt sentence segmentation (`punkt-abbrev-v1`) over the markdown structure (`markdown/1`);
  - cuts at section boundaries;
  - a 512-token target and a hard ceiling of 640;
  - pieces under 128 tokens merged with a sibling.
- **Output.** 9,150 chunks: mean 398 tokens, median 454, maximum 640.
- **The P2 gate.** It passed all six checks:
  1. no chunk joins fragments of two segments;
  2. every boundary is a sentence or segment boundary;
  3. no chunk exceeds the ceiling;
  4. the chunks tile each document;
  5. the stage-A offsets reproduce;
  6. the environment is as frozen.

## Stage 8 — the two passages of each pair (PREREG §5 step 2)

Source: `artifacts/map.json`, `artifacts/retest-seed.json`.

- **Control arm** (`map`, rule `max-overlap`). Each drawn window is paired with the new chunk that shares the
  most characters with it; ties go to the earlier chunk.
  - **Coverage**, the share of the window's text inside that chunk, is recorded: ≥ 0.8 for 145 pairs, 0.5–0.8
    for 182, under 0.5 for 24.
  - 351 of 351 pairs mapped (the floor was 316).
  - Mean length is 412 tokens for the control chunks and 467 for the windows.
- **Retest arm** (`retest-seed`). The same 351 pairs, pointed back at their own stage-A window, unchanged. The
  prompt is byte-identical to stage A's. This arm measures the judge's own test-retest noise on these exact
  pairs.

## Stage 9 — judging (PREREG §5 steps 3–5)

- **Registries.** Two judge registries (control and retest) hold the same 351 pairs.
  - The judge configuration is the alias study's frozen one; only the output directory and the control chunk
    file differ.
  - The judge never sees which arm a pair is in.
- **Arm order.** It alternates by backend so that neither arm is always judged later:
  - C: control → retest;
  - E: retest → control;
  - Argon: control → retest;
  - Opus: retest → control.

The result is in [RESULTS.md](RESULTS.md) and [REPORT.md](REPORT.md).

## What a different site would need

- The OA-1000 texts, the 1,091 questions and the stage-A registry. These are private, and their hashes are
  pinned in the artifacts.
- The code at tag `prereg-alias-boundary-confirm-2026-11`.
- The SFR tokenizer.
- The two Qwen judge models, at the alias prereg's settings.

Gateway access for the Argon tiebreak and the Opus drift reference can be replaced only as a declared
deviation.
