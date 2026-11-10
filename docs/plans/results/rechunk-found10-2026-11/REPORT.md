# Report: is the answering chunk retrieved under the new chunking?

Exploratory. Stage 1 ran on 2026-11-06/07, and stage 1b, which judged the remaining top-10 chunks of the lost
pairs, on 2026-11-09/10. This is the reader's version, revised in place:
- **Record:** [RESULTS.md](RESULTS.md) (stage 1) and
  [ADDENDUM-2026-11-10-stage1b.md](ADDENDUM-2026-11-10-stage1b.md) (stage 1b), with the artifacts in
  [`artifacts/`](artifacts/).
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

The plan reads this per pair (A_own: one of the pair's *own* chunks answers and is retrieved). A looser
measure, A (*any* chunk labelled *answers* for the question is retrieved), was only a lower bound in stage 1,
because most top-10 chunks carried no label. Stage 1b judged those chunks for the 75 lost pairs to settle A.

## 2. Results summary

Under production retrieval (hybrid search with the reranker):
- **The 53 recovered pairs.** Their own answering chunk is in the top 10 for **35 of 53 (66 %, 95 % interval
  53–77 %)**. That is about the same rate as the 276 pairs whose new chunk kept *answers*: 178 of 276 (64.5 %,
  59–70 %).
- **Among windows that stage A had retrieved,** the figures are 32 of 40 (80 %) against 172 of 217 (79 %).
- **Any answering chunk.** Once every top-10 chunk of the 75 lost pairs was judged (stage 1b), some chunk in
  the top 10 answers for **all 75** (stage 1's lower bound: 42 of 75). That includes the 18 split pairs. The
  pairs' own figure is unchanged, at 39 of 75.

So for most of the 53 recovered pairs the answer is still retrieved, from the neighbouring chunk. For the
recovered pairs, the loss the confirmatory test measured comes from judging single passages. "Any chunk
answers" saturates on these alias questions, which many papers answer, so it cannot distinguish chunkings;
the per-pair measure carries the reading.

The 18 lost pairs whose answer is split across chunks cannot be credited by any of their own chunks; their
split chunk is retrieved for 5 of 18. All figures are descriptive.

## 3. How it was tested

**Collections.**
- Four collections on the non-production (dev) tenant: the alias study's base corpus, and base plus its
  citation neighbours or one of two random draws.
- Chunking: `sentence/512/0` with section cuts, through the production embed and load workflows (tools image
  `v1.6.24-b1`).
- 452,729 chunks in all, each collection verified after loading.
- Details: [TEST-DATA.md](TEST-DATA.md).

**Labels.** Stage 1 did no new judging. The confirmatory test's labels carry over by chunk id, because the
production chunking reproduces those chunks exactly.
- For base, all 12,560 documents chunk identically.
- In the three larger corpora, 19 documents chunk slightly differently in the image than in the host-made
  export: the image's tokenizer and nltk are newer. The image's chunking was taken as the truth (owner
  decision), and none of the 19 holds a test pair (DEVIATIONS D-5).

**Retrieval.** The alias study's three retrievers (default, norerank, BM25), top 50 asked, top 10 read. Each
of the 218 questions runs against its pair's corpus arm.

**Stage 1b judging.** No new retrieval; the stage-1 top-10 lists were reused.
- **What was judged.** The default retriever's top-10 lists of the 75 lost pairs (69 questions) hold 750
  slots, and 687 of them had no label. These are 671 distinct (question, chunk) pairs: 497 lie in a paper
  other than the lost pair's window paper.
- **The judge.** The confirmatory test's pipeline: Qwen C and E locally, Gemini 4 Argon to break their 80
  disagreements, and Claude Opus 5.5 on a 34-call drift check. The passage side takes its paper from the
  chunk's own paper (`--any-paper`, owner-approved).
- **The gateway.** The 114 gateway calls ran on a different gateway (and credential) from every earlier
  stage. Its totals were reviewed by the gateway coordinator but could not be independently verified
  (DEVIATIONS D-9).
- **Merging.** The 671 new labels were merged into the label table with no conflicts, and the stage-1 report
  was re-run on it.

**Measures.**
- **A_own:** one of the pair's own chunks (its largest-overlap chunk, or its cover-any chunks) is labelled
  *answers* and is in the top 10. This is the plan's per-pair reading.
- **A:** any chunk labelled *answers* for the question is in the top 10, whichever pair or paper it came from.
- **M:** the largest-overlap chunk is in the top 10.

Wilson 95 % intervals; pairs of one question share one top-10 list.

## 4. Results

**The 75 lost pairs** (default retriever):

| group | A_own | among retained windows | M | A, every top-10 chunk judged (stage 1b) |
|---|---|---|---|---|
| recovered: another chunk answers (53) | **35 / 53 (66 %)** | 32 / 40 (80 %) | 11 / 53 | 53 / 53 |
| split: no chunk answers alone (18) | 0 by construction | — | 5 / 18 | 18 / 18 |
| judge noise: re-read answered (4) | 4 / 4 | 4 / 4 | 4 / 4 | 4 / 4 |
| all 75 | 39 / 75 (52 %) | — | — | **75 / 75** (95 % interval 95–100 %) |

**References** (default retriever):
- the 276 pairs whose new chunk kept *answers*: A_own **178 / 276 (64.5 %)**; among retained windows,
  172 / 217 (79.3 %);
- recovered windows that stage A had in the same top 10: 40 / 53 (75.5 %).

**Other retrievers.** They lower both the recovered figure and the reference:
- recovered: 31 / 53 without the reranker, 25 / 53 with BM25;
- reference: 155 / 276 and 138 / 276.

Stage 1b judged only the default retriever's lists, so A under these two remains a lower bound.

**By corpus** (A_own, default, all pairs). The larger corpora retrieve the answering chunk less often:

| corpus | A_own |
|---|---|
| base | 73.5 % |
| neighbour | 53.8 % |
| random-1 | 54.7 % |
| random-2 | 62.3 % |

**By coverage** (default, all pairs). A_own is flat across coverage bands, at 60–63 %. M falls from 61 % when
the new chunk keeps at least 80 % of the window to 42 % when it keeps less than half.

**Stage 1b descriptives** (default retriever, the 75 lost pairs):
- **A.** A rose from 42 to 75 of 75. For 36 of the 75, a chunk that is not the pair's own credits A (stage
  1: 3).
- **A_own and M.** Both are unchanged in every group, retriever and stratum: the new labels fall only on
  chunks that are no pair's own.
- **Labels.** Of the 671 newly judged chunks, 323 (48 %) were labelled *answers*: 61 % of those from the
  question's own paper and 43 % of those from other papers.

## 5. Conclusion

- **What is shown.** For the lost pairs whose answer moved to a neighbouring chunk, production retrieval
  delivers that chunk as often as it delivers the answer for pairs that never lost it. Under single-passage
  judging, the confirmatory loss is therefore mostly a measurement effect: the answer did not leave the
  retrieved set; it moved to another chunk.
- **Which measure is informative.** With every top-10 chunk judged, "any chunk answers" (A) holds for all 75
  lost pairs, split answers included. Alias questions ask about a term or an abbreviation, and many papers
  answer them, so every lost pair's top 10 held some answering chunk. A therefore saturates and cannot tell
  one chunking from another on this question set. The per-pair measure A_own, which the plan reads, is the
  informative one, and stage 1b leaves it unchanged.
- **What is not shown.**
  - The intervals are wide (53 pairs, 49 questions), and nothing was tested.
  - The labels are the judge's, under the parent study's judge-validity caveat. The 48 % *answers* rate among
    the newly judged chunks reflects their selection (the retriever's top 10 for these questions). It is not
    independent support for any reading. Tiebroken labels lean *not* (65 % against 52 % overall).
  - Every pair came from a window retrieved in stage A, so the overall M (56 %) is not a measure of reach
    lost. A reach comparison between the two chunkings needs its own design.
- **What remains a real loss.** The 18 split answers. No single own chunk carries them, so a per-pair,
  single-chunk evaluation counts them as misses whatever is retrieved. A credits them only because other
  chunks answer the same term question. Overlap between chunks, or judging a chunk together with its
  neighbours, are the candidate remedies; neither was tested here. That fits the OA chunking evaluation's
  finding that overlap helps exactly on answers a boundary splits.
