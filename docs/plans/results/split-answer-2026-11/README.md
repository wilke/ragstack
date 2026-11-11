<!-- experiment: Split answers under the adopted chunking: overlap, neighbours, or other cuts? | 2026-11-09 | EXPLORATORY, STOPPED AFTER STEP 1 -->
# Split answers: overlap, judge-time neighbours, or another cut?

**Status: step 1 done and the plan stopped, 2026-11-11 (owner decision). Exploratory.** Judge-time neighbours keep 98 % of kept answers but recover only 8 of 18 split answers (44 %) against a 23 % false-*answers* rate on the negative control; no step 2, no production change. Read [REPORT.md](REPORT.md).

This is the follow-up to the confirmatory boundary test's cover-any addendum
([`../alias-boundary-confirm-2026-11/`](../alias-boundary-confirm-2026-11/README.md)). Of the 75 pairs that lost
*answers* under the adopted chunking, 18 have no single overlapping chunk that answers. The plan set out to compare three
mitigations: chunk overlap, judging a chunk with its neighbours (the production `context_window`), and other cuts
the chunker supports, neighbours first. Only neighbours (step 1) ran: the neighbour passage in the production
context-window format (exploratory protocol extension), section cuts kept, full controls, and a negative control of
150 stage-A *not* pairs (446 items; ≈ 959 judge requests planned, 942 made: 892 Qwen, 50 gateway). The result was
weak, so under OPEN-5 nothing changes in production, and the owner stopped the plan: step 2 (strict + overlap) does
not run.

| file | tier | what |
|---|---|---|
| [PLAN.md](PLAN.md) | plan, stopped after step 1 (frozen as written on 2026-11-11) | options, what the chunker supports (file:line), feasibility counts, code needed before step 1 (§3.2), cost, owner decisions (2026-11-10, 2026-11-11) |
| [split_feasibility.py](split_feasibility.py) | record | offline re-chunking of the 343 pair documents under 9 chunkings; refuses ids, text, hosts and paths |
| [artifacts/feasibility.json](artifacts/feasibility.json) | record | the counts, produced at `57d2f15c` on a clean tree (`citable: true`); that id predates the rebase onto `main`, which left the script and its imports byte-identical (now `78595001`) |
| [artifacts/negative-draw-seed.json](artifacts/negative-draw-seed.json) | record | §7 step 0a: the negative-draw seed, committed before the draw (PLAN §3.2 (b)6) |
| [artifacts/negative-draw.json](artifacts/negative-draw.json) | record | §7 step 0a: counts of the seeded draw of the 150 stage-A *not* pairs (quotas, candidates, exclusions per cell); `split_answer_prep.py draw-negative` |
| [artifacts/neighbour-items.json](artifacts/neighbour-items.json) | record | §7 step 0a: counts of the 446 neighbour items per group and edge class, matching PLAN §5 (b); `split_answer_prep.py items`. Its `provenance.citable` is false only because the sibling step-0 artifacts were still untracked when it was written (§7 step 0a commits them together) |
| [artifacts/wall-read-counts.json](artifacts/wall-read-counts.json) | record | §7 step 0b: the blind, counts-only read of the 10 wall-crossing split windows (needs both sides 0, one side 9, unclear 1; reader: a reviewer agent with no other role), made before any neighbour label existed; `split_answer_prep.py wall-read` / `wall-counts`. `citable` false for the same reason as the items row. Same-paper negatives are all in base by construction: the neighbour and random arms hold other papers' passages |
| [RESULTS.md](RESULTS.md) | record | step 1: rates per group, against PLAN §7's rule, the owner's stop decision (2026-11-11) |
| [artifacts/neighbour-report.json](artifacts/neighbour-report.json) | record | step 1: counts-only report of the 446 neighbour labels (`split_answer_prep.py report`, offline, `citable: true` at `e8b94454`) |
| [REPORT.md](REPORT.md) | report, revised in place | reader's version: hypothesis, short results, how tested, results, conclusion |
| [TEST-DATA.md](TEST-DATA.md) | record | how step 1's items and labels were made |
| [DEVIATIONS.md](DEVIATIONS.md) | record, append-only | D-1: step 1's gateway share ran on the second gateway on the owner's go |
