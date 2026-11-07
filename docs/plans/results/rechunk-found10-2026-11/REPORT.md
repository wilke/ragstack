# Report: is the answering chunk retrieved under the new chunking?

Exploratory, stage 1, run 2026-11-06/07. This is the reader's version, revised in place:
- **Record:** [RESULTS.md](RESULTS.md), with the artifacts in [`artifacts/`](artifacts/).
- **Test data:** [TEST-DATA.md](TEST-DATA.md).
- **Deviations:** [DEVIATIONS.md](DEVIATIONS.md).

## 1. Hypothesis

The confirmatory chunk-boundary test found the following
([REPORT](../alias-boundary-confirm-2026-11/REPORT.md),
[addendum](../alias-boundary-confirm-2026-11/ADDENDUM-2026-11-06-cover-any.md)):
- **The loss.** Re-chunking at sentence and section boundaries makes the frozen judge withdraw *answers* on
  75 of 351 pairs it had accepted on the 512-token window.
- **Where the answer went.** On 53 of those 75, another chunk overlapping the window still answers when read
  alone.

That test judged passages, not retrieval. The owner asked whether that neighbouring chunk would also be in
the top n.

**Question:** if the questions are retrieved against collections built with the new chunking, is a chunk the
judge labelled *answers* in the top 10? It is read for the 53 recovered pairs against two references (PLAN §5):
- the pairs that never lost the label;
- the stage-A retrieval of the same windows.

## 2. Results summary

Under production retrieval (hybrid search with the reranker):
- **The 53 recovered pairs.** Their own answering chunk is in the top 10 for **35 of 53 (66 %, 95 % interval
  53–77 %)**. That is about the same rate as the 276 pairs whose new chunk kept *answers*: 178 of 276 (64.5 %,
  59–70 %).
- **Among windows that stage A had retrieved,** the figures are 32 of 40 (80 %) against 172 of 217 (79 %).

So for most of the 53 recovered pairs the answer is still retrieved, from the neighbouring chunk. Over all 75
lost pairs, the pair's own answering chunk is retrieved for about half: 39 of 75. For the recovered pairs,
the loss the confirmatory test measured comes from judging single passages.

The 18 lost pairs whose answer is split across chunks cannot be credited by any single chunk; their split
chunk is retrieved for 5 of 18. All figures are descriptive.

## 3. How it was tested

**Collections.**
- Four collections on the non-production (dev) tenant: the alias study's base corpus, and base plus its
  citation neighbours or one of two random draws.
- Chunking: `sentence/512/0` with section cuts, through the production embed and load workflows (tools image
  `v1.6.24-b1`).
- 452,729 chunks in all, each collection verified after loading.
- Details: [TEST-DATA.md](TEST-DATA.md).

**Labels.** No new judging. The confirmatory test's labels carry over by chunk id, because the production
chunking reproduces those chunks exactly.
- For base, all 12,560 documents chunk identically.
- In the three larger corpora, 19 documents chunk slightly differently in the image than in the host-made
  export: the image's tokenizer and nltk are newer. The image's chunking was taken as the truth (owner
  decision), and none of the 19 holds a test pair (DEVIATIONS D-5).

**Retrieval.** The alias study's three retrievers (default, norerank, BM25), top 50 asked, top 10 read. Each
of the 218 questions runs against its pair's corpus arm.

**Measures.**
- **A_own:** one of the pair's own chunks (its largest-overlap chunk, or its cover-any chunks) is labelled
  *answers* and is in the top 10.
- **M:** the largest-overlap chunk is in the top 10.

Wilson 95 % intervals; pairs of one question share one top-10 list.

## 4. Results

**The 75 lost pairs** (default retriever):

| group | A_own | among retained windows | M |
|---|---|---|---|
| recovered: another chunk answers (53) | **35 / 53 (66 %)** | 32 / 40 (80 %) | 11 / 53 |
| split: no chunk answers alone (18) | 0 by construction | — | 5 / 18 |
| judge noise: re-read answered (4) | 4 / 4 | 4 / 4 | 4 / 4 |

**References** (default retriever):
- the 276 pairs whose new chunk kept *answers*: A_own **178 / 276 (64.5 %)**; among retained windows,
  172 / 217 (79.3 %);
- recovered windows that stage A had in the same top 10: 40 / 53 (75.5 %).

**Other retrievers.** They lower both the recovered figure and the reference:
- recovered: 31 / 53 without the reranker, 25 / 53 with BM25;
- reference: 155 / 276 and 138 / 276.

**By corpus** (A_own, default, all pairs). The larger corpora retrieve the answering chunk less often:

| corpus | A_own |
|---|---|
| base | 73.5 % |
| neighbour | 53.8 % |
| random-1 | 54.7 % |
| random-2 | 62.3 % |

**By coverage** (default, all pairs). A_own is flat across coverage bands, at 60–63 %. M falls from 61 % when
the new chunk keeps at least 80 % of the window to 42 % when it keeps less than half.

## 5. Conclusion

- **What is shown.** For the lost pairs whose answer moved to a neighbouring chunk, production retrieval
  delivers that chunk as often as it delivers the answer for pairs that never lost it. Under single-passage
  judging, the confirmatory loss is therefore mostly a measurement effect: the answer did not leave the
  retrieved set; it moved to another chunk.
- **What is not shown.**
  - The intervals are wide (53 pairs, 49 questions), and nothing was tested.
  - The labels are the judge's, under the parent study's judge-validity caveat.
  - Every pair came from a window retrieved in stage A, so the overall M (56 %) is not a measure of reach
    lost. A reach comparison between the two chunkings needs its own design.
- **What remains a real loss.** The 18 split answers. No single chunk carries them, so any single-chunk
  evaluation counts them as misses whatever is retrieved. Overlap between chunks, or judging a chunk together
  with its neighbours, are the next things to test. That fits the OA chunking evaluation's finding that
  overlap helps exactly on answers a boundary splits.
