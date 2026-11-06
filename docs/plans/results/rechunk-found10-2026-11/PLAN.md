# Plan: is the answering chunk retrieved under the new chunking? (exploratory, stage 1)

STATUS: DRAFT 2026-11-06. Exploratory. This file is committed before the first retrieval call, and later
changes go in a dated addendum. Stage 2 (judged found@10 over all 1,091 alias questions, with glossary and
growth interactions) is not planned here; the owner decides on it after stage 1.

## 1. Question

The confirmatory chunk-boundary test ([`../alias-boundary-confirm-2026-11/`](../alias-boundary-confirm-2026-11/REPORT.md))
found the following.
- **The loss.** Re-chunking at sentence and section boundaries (`sentence/512/0`, section cuts) makes the
  frozen judge withdraw *answers* from the largest-overlap new chunk on 75 of 351 pairs. Those were pairs it
  had accepted on the 512-token window.
- **Where the answer went.** On 53 of those 75, another new chunk overlapping the window still answers. On
  18, none does ([addendum](../alias-boundary-confirm-2026-11/ADDENDUM-2026-11-06-cover-any.md)).

That test judged passages, not retrieval. The owner asked whether the answering neighbouring chunk would also
be in the top n. Stage 1 asks:

- **Q1 (primary, descriptive).** For the 75 lost pairs, if the question is run against a collection built
  with the new chunking, is a chunk the judge labelled *answers* in its top 10? This is read separately for
  the 53 recovered pairs and the 18 others.
- **Q2 (secondary, descriptive).** For all 351 pairs, is the new counterpart of a retrieved window (its
  max-overlap chunk) also retrieved? And is any labelled-*answers* chunk in the top 10?

A related, judge-free result already exists: the OA chunking evaluation
([`../oa-chunking-eval-2026-10/`](../oa-chunking-eval-2026-10/POSTHOC-HYPOTHESES-2026-11-06.md); 2,035
questions, gold-quote coverage).
- **Hit@5.** `sentence 512 + section cuts` reached 82.06 % hit@5 under production retrieval, against 76.95 %
  for fixed 512/0.
- **Its limit.** Its passages lay inside one section, so no gold quote crossed a section boundary. The alias
  windows can cross one, and stage 1 reads that harder case on a small set.

## 2. Data

- **Questions and pairs.** No new questions and no new draw. The 351 pairs (218 questions) of the
  confirmatory test, and among them the 75 lost pairs. Their generation is in
  [`../alias-boundary-confirm-2026-11/TEST-DATA.md`](../alias-boundary-confirm-2026-11/TEST-DATA.md).
- **Corpora.** The alias study's four, with their record texts unchanged: base OA-1000 (12,560 documents),
  and base plus neighbour, random-1 or random-2 (30,408 / 32,671 / 33,409 added documents).
- **Chunking.** `sentence/512/0` with
  `{"min_piece":128,"pack":"318","size_tolerance":0.25,"size_unit":"tokens","structure_cuts":"sections/1"}`,
  Punkt `punkt-abbrev-v1`, markdown structure `markdown/1`, and the SFR tokenizer. That is the descriptor in
  the confirmatory `artifacts/chunk.json`.
  - **Reproduction.** Run through the production ingest code on the deployed tools image `v1.6.24-b1`, it
    reproduced the confirmatory control chunks exactly: 343 of 343 documents, 9,150 of 9,150 chunks (ids,
    spans and content). The condition is that each record carries a precomputed `markdown/1` `structure` key.
    Without that key, the section cuts do not happen.
  - **Expected sizes** (offline count, the same code): base 38,548 chunks; neighbour 141,593; random-1
    136,403; random-2 136,188 (452,732 in all).
  - **Why labels carry over.** Chunk ids are identical, so the confirmatory labels apply unchanged:
    control 351 and cover-any 174.
- **Collections.** Four bench collections on the non-production (dev) tenant, glossary *none*:
  `bench-rechunk-{base,neighbour,random1,random2}`. They are built through the same embed and load workflows
  as the alias collections, under a reviewed live-ops plan, and deleted after the analysis.

## 3. Retrieval

- **Settings.** The alias study's three retrievers, with its settings unchanged:
  - default: hybrid BM25 + SFR dense (RRF), the top 50 reranked by `BAAI/bge-reranker-v2-m3`;
  - norerank: the same hybrid;
  - BM25 alone.

  Every call asks for 50, and the top 10 is read.
- **Which collection.** Each of the 218 questions is run against the collection of its pair's corpus arm. A
  random-arm pair uses the random draw (1 or 2) that its stage-A window was retrieved from. A question with
  pairs in several arms is run once per arm.
- **Code.** The alias run harness (`alias_study_run.py`), in a new exploratory mode. The mode replaces its
  gold-presence check with a check that every pair's mapped chunks are in the collection's export. Retrieval
  settings are unchanged, and each run records `mode`, commit and provenance.

## 4. Outcomes and analysis (all descriptive)

Per pair, under each retriever:
- **A:** is a chunk labelled *answers* (control or cover-any registry, for any pair of the same question) in the
  top 10?
- **A_own:** is one of *this* pair's own chunks (its max-overlap chunk or its cover-any chunks) labelled
  *answers* and in the top 10? A_own is the per-pair reading. A can also be credited by another pair of the
  same question, and the report counts how often that happens.
- **M:** is the max-overlap chunk in the top 10, and at which rank?
- **U:** the number of top-10 chunks with no label.

U bounds the answer from above: found@10 for the pair lies between "A" and "A or any unlabelled chunk in the
top 10".

Reported:
- **Q1.** For the 53 recovered pairs, the 18 others, the 4 whose max-overlap chunk the re-read labelled *answers*
  (reported alone), and all 75: counts and Wilson 95 % intervals for A and A_own, plus the upper bound and the
  number of distinct questions in each group. The default retriever is the headline; norerank and BM25 are shown beside it.
- **Q2.** For all 351: M, the share of retrieved windows whose counterpart is retrieved, by coverage band
  and by corpus arm; M among pairs whose window was in the same retriever's stage-A top 10 (counterpart retained); and A and A_own
  for the 276 pairs whose control chunk was labelled *answers*.
- **Reference.** Beside each figure, whether the pair's window was in the same retriever's stage-A top 10 in
  the same corpus arm. Base-paper windows run in the base collection; none of the 351 was found only in a
  larger arm's legs (gold-map count 0).
- **Gates.** A retrieval leg whose depth or reranker gate fired is refused, not reported.

**No new judging in stage 1.** If U makes the Q1 upper and lower bounds differ by more than 10 pairs out of
75, a small judging step is the stage-1b option, put to the owner:
- the unlabelled top-10 chunks of the 75 questions, at most 750 pairs, with the frozen judge;
- a gateway notice first.

## 5. What would change a conclusion

- **If A is high for the 53:** the loss the confirmatory test measured is mostly a single-passage judging
  artefact, because retrieval still delivers the answer.
- **If A is low:** the neighbour chunk holds the answer but is not retrieved, so the chunking costs reach on
  these questions.
- **For the 18:** a low A is expected. These are the candidates for overlap, or for judging a chunk together
  with its neighbours.

Stage 1 is too small for a test; it shows direction and size, labelled exploratory.

## 6. Safety and cost

- **Store.** Dev tenant only. Collections are named `bench-rechunk-*` and deleted afterwards, checked against
  a listing. The collection cap and eviction gate apply as in the alias live-ops plan, and that plan is
  coordinated with the management session.
- **Reviews.** A Fable review and an independent argo-agent read of the live-ops plan and of the harness
  change before any create.
- **Workflow.** The committed embed workflow cannot pass the chunk-size unit, so a token-sized collection is
  refused (exit 2). The owner chose a repo PR (2026-11-06) adding an optional `chunk_size_unit` input to
  `cwl/embed-bulk.cwl`. It merges before the first create; the image is unchanged.
- **Compute.** Four resolved embeds, about 453k chunk embeddings (about 1–1.5 h on the two SFR endpoints).
  Retrieval is about 218 questions × 3 retrievers × the arms each question touches, roughly 1–2k calls.
- **Judging.** None in stage 1. The stage-1b option is at most 750 local Qwen pairs, plus a small gateway
  batch after a notice.

## 7. Write-up

A `REPORT.md` in the owner's order (hypothesis, results summary, how it was tested, results, conclusion), a
`TEST-DATA.md` that points to the confirmatory one and adds the collections, and the public artifacts: per-leg
summaries and the stage-1 analysis JSON, with no ids or text.
