<!-- experiment: Split answers under the adopted chunking: overlap, neighbours, or other cuts? | 2026-11-09 | EXPLORATORY, DRAFT -->
# Split answers: overlap, judge-time neighbours, or another cut?

**Status: DRAFT plan, 2026-11-09; owner decisions 2026-11-10 and 2026-11-11. Exploratory. Offline feasibility counts only; no judge, store, retrieval or LLM call.**

This is the follow-up to the confirmatory boundary test's cover-any addendum
([`../alias-boundary-confirm-2026-11/`](../alias-boundary-confirm-2026-11/README.md)). Of the 75 pairs that lost
*answers* under the adopted chunking, 18 have no single overlapping chunk that answers. The plan compares three
mitigations: chunk overlap, judging a chunk with its neighbours (the production `context_window`), and other cuts
the chunker supports. The owner's answers of 2026-11-10 and 2026-11-11 are folded in: judge the neighbour passage
in the production context-window format (exploratory protocol extension), keep section cuts, full controls, and a
negative control of 150 stage-A *not* pairs (446 items, ≈ 959 judge requests). Neighbours first; strict + overlap
only if neighbours fail. No owner question is open; whether a positive result changes the production answer path
(OPEN-5) is decided after the result. Step 1 needs code that does not exist yet; PLAN §3.2 specifies it.

| file | tier | what |
|---|---|---|
| [PLAN.md](PLAN.md) | plan, DRAFT | options, what the chunker supports (file:line), feasibility counts, code needed before step 1 (§3.2), cost, owner decisions (2026-11-10, 2026-11-11) |
| [split_feasibility.py](split_feasibility.py) | record | offline re-chunking of the 343 pair documents under 9 chunkings; refuses ids, text, hosts and paths |
| [artifacts/feasibility.json](artifacts/feasibility.json) | record | the counts, produced at `57d2f15c` on a clean tree (`citable: true`); that id predates the rebase onto `main`, which left the script and its imports byte-identical (now `78595001`) |
