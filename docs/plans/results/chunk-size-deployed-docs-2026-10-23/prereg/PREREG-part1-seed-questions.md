# Pre-registration, part 1: the seed questions (redacted public copy)

> **Redacted copy.** The private original is kept with the outputs, off GitHub. Its sha256 is
> `8b5a37e035945f798f038aede159b9fd5c0430e96267243d4eb7b32cc865cb18` (recorded 2026-10-23T14:13:00Z).
> Question texts, gold page names, endpoint hosts and the collection's name are replaced by
> `[REDACTED: private eval set]` or a neutral label. Design, groups, endpoints and the reading are unchanged.

Written 2026-10-23, before the collection had been seen, built or run. Owner go, 2026-10-23: use all 18
seed questions, some as negatives; the rewordings were approved as drafted. EXPLORATORY.

Part 2, generated questions (the main set), is pre-registered separately once the collection's structure
is known.

The chunk arms, served path, budgets and storage variants are those of section-aware Addendum D:
T128–T1024 with overlap 0 and s/8, plain token, f32/int8/bin. The X (section) family is applied only if
documents have sections. Glossary state: whatever the collection has (rules/3 if no LLM cache); no LLM
glossary calls.

## Questions

The seed questions are hand-written test questions for the deployed assistant, with no user data.
Their texts are `[REDACTED: private eval set]`.

### Group P: positives with weak gold
3 questions (E5, E6, M6). Weak gold is the documentation pages the deployed assistant cited for each
(3–4 pages per question; names `[REDACTED: private eval set]`).

- Scoring: a chunk counts as a hit if its source document is one of the gold pages.
- If the collection contains none of those pages, group P has no gold; it is then judged like group W and
  the change is logged.

### Group N: negatives (no answer expected in a documentation collection)
- **Lookups:** E1–E4, M1–M4, H1, H3, H4 (database lookups the documentation cannot answer).
- **Tool runs:** H2, H5.
- **Out of scope:** H6.
- **Literature (reported separately):** M5.

### Group W: how-to rewordings of the 13 lookup and tool-run negatives
E1w–E4w, M1w–M4w, H1w–H5w: each asks *how to do* the corresponding lookup with the product. Texts
`[REDACTED: private eval set]`.

**Gold for W: pooled relevance judgements.**
- The pool is the union of every arm's reranked top 10, for each W question, judged at document level.
- The judge is a local LLM (Llama-4-Scout or Qwen3.8-27B with thinking off) on an endpoint the trial is
  allowed to use; never the deployed assistant's own endpoint.
- The prompt is fixed in part 2 before judging.
- Judgement: does this chunk tell a user how to do what the question asks? Answer: yes / partial / no.
- Scoring: a hit is any "yes" chunk within the cut; "partial" is reported separately.

## Endpoints
- **P and W:** found within b2048 and b5120 (primary across sizes), and reranked@10, per arm.
- **N (and W vs N):** the top-1 reranker score per question and arm.
  - The separation is AUROC of the top-1 score, with P ∪ W as positives and N as negatives. M5 and H6
    are also reported separately.
  - The paired contrast, per topic and arm: top-1 score of the w question minus that of the original.
- Any negative whose top-1 score falls inside the positive range is listed by id.

## Reading
- There are 3 P questions, 13 W and 15 N (14 plus M5).
- No contrast is decisive. This group is reported beside the part-2 set and never decides alone.
