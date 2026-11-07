# How the test data was generated

Stage 1 reuses the confirmatory test's questions, pairs and labels. It adds four collections built with the
new chunking. Every count below names its committed artifact; private files (texts, ids, labels) stay off the
repository.

## 1. Questions, pairs and labels (unchanged)

- **Questions and pairs.** The 351 pairs (218 questions) of the confirmatory test. Their generation, from the
  OA-1000 papers to the draw, is in
  [`../alias-boundary-confirm-2026-11/TEST-DATA.md`](../alias-boundary-confirm-2026-11/TEST-DATA.md).
- **Labels.** The frozen judge's final labels from that test's control registry (351 chunks) and cover-any
  registry (174 chunks). `rechunk_prep.py gold-map` joined them into a private label table: per pair, its
  largest-overlap chunk, its cover-any chunks and its corpus arm; per (question, chunk), the label. The
  `stage1.json` header records the table's sha.
- **Counts.** Reproduced from the confirmatory record: 351 pairs; 75 lost (53 recovered, 18 none, 4
  primary-only); corpus arms 117 / 117 / 64 / 53.
- **Assigning random pairs.** A random-arm pair goes to the random draw whose judged stage-A leg retrieved
  its window. 6 windows were retrieved in both draws and went to random-1. No base window was found only in
  a larger arm's legs (`base_windows_only_in_larger_arm_legs` 0).

## 2. Corpora and records

The alias study's four corpora, with their record texts unchanged: base OA-1000 (12,560 records), and the
neighbour, random-1 and random-2 added papers (30,408 / 32,671 / 33,409 records; 109,048 in all). The added
papers come in 25 batch shards per corpus, each verified against the alias load's batch record.

**Structure key.** `rechunk_prep.py add-structure` gave every record a `markdown/1` structure key, with path,
document id and text unchanged; it refuses on any change of text sha. Without that key, the production
pipeline makes no section cuts (PLAN §2).

## 3. Chunking

`sentence/512/0`, with descriptor
`{"min_piece":128,"pack":"318","size_tolerance":0.25,"size_unit":"tokens","structure_cuts":"sections/1"}`.
That is the confirmatory `chunk.json` descriptor, spec hash `13cd978e`.

- **Host exports** (`rechunk_prep.py export`, at `002e98a`, clean). The planned counts reproduced exactly:
  38,548 / 103,045 / 97,855 / 97,640 chunks. `build-summary.json` gives each export's chunk sha.
- **Collections** (production path). GoWe `embed-bulk.cwl` (with `chunk_size_unit: tokens`, PR #983) and
  `load-embeddings.cwl`, tools image `v1.6.24-b1`, SFR-Embedding-Mistral (4096-d). Each collection was
  embedded separately: base alone, then base plus each corpus.
  - **Base.** It matched the host export exactly.
  - **Expanded corpora.** 3, 8 and 8 documents chunk differently in the image, whose libraries are newer
    (DEVIATIONS D-5). Their exports were rebuilt from their own embedding files, so the harness reads exactly
    the chunks the collections hold.

## 4. The collections as built

| collection | chunks | documents | glossary |
|---|---|---|---|
| bench-rechunk-base | 38,548 | 12,560 | none (an explicit empty file) |
| bench-rechunk-neighbour | 141,592 | 42,968 | none |
| bench-rechunk-random1 | 136,402 | 45,231 | none |
| bench-rechunk-random2 | 136,187 | 45,969 | none |

Each was checked after loading (`build-summary.json`):
- the same count in the vector store, the text store and the API;
- every document verified;
- the census stamp;
- every other collection unchanged.

## 5. Retrieval

- **Harness.** `alias_study_run.py` in `rechunk` mode, stage A, glossary none, at `002e98a`.
- **Retrievers.** The alias study's three, unchanged: default (hybrid, top 50 reranked), norerank, and BM25.
  Top 50 asked, top 10 read.
- **Which questions.** Each corpus arm ran only the questions with a pair in that arm. The mode refuses
  unless every pair's mapped chunks are in that arm's export and the export's chunk count equals the
  collection's.
- **Report.** `rechunk_stage1.py` turned the served lists and the label table into `artifacts/stage1.json`.

## What a different site would need

- The OA-1000 and added-paper texts, the confirmatory registries and the alias question records. These are
  private; their shas are pinned in the artifacts.
- This repository at `467b1fe` or later (the build-summary generator; the report code is at `16220f8`).
- The tools image `v1.6.24-b1` and an SFR-Embedding-Mistral endpoint.
- A RAGStack deployment with the same retriever settings.
