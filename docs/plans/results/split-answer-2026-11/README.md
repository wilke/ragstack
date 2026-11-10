<!-- experiment: Split answers under the adopted chunking: overlap, neighbours, or other cuts? | 2026-11-09 | EXPLORATORY, DRAFT -->
# Split answers: overlap, judge-time neighbours, or another cut?

**Status: DRAFT plan, 2026-11-09. Exploratory. Offline feasibility counts only; no judge, store, retrieval or LLM call.**

This is the follow-up to the confirmatory boundary test's cover-any addendum
([`../alias-boundary-confirm-2026-11/`](../alias-boundary-confirm-2026-11/README.md)). Of the 75 pairs that lost
*answers* under the adopted chunking, 18 have no single overlapping chunk that answers. The plan compares three
mitigations: chunk overlap, judging a chunk with its neighbours (the production `context_window`), and other cuts
the chunker supports. Owner questions are open.

| file | tier | what |
|---|---|---|
| [PLAN.md](PLAN.md) | plan, DRAFT | options, what the chunker supports (file:line), feasibility counts, cost, open owner questions |
| [split_feasibility.py](split_feasibility.py) | record | offline re-chunking of the 343 pair documents under 9 chunkings; refuses ids, text, hosts and paths |
| [artifacts/feasibility.json](artifacts/feasibility.json) | record | the counts, produced at `57d2f15c` on a clean tree (`citable: true`) |
