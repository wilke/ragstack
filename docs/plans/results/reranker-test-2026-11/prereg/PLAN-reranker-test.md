# Plan: the dedicated reranker test (lab book, 2026-10-29)

> **Lab-book tier.** Dated, append-only, superseded-not-deleted. The design is
> `PREREG-reranker-test.md`; nothing here constrains it. Compute figures are design estimates.

Owners: **owner**, **Fable** (plan, review, live-ops), **subagent** (runs, write-up scaffolding), **the
partner** (optional cross-check on set P; the round-2 papers-OC set).

## What can run now, and what waits

| needs | ready now | waits for |
|---|---|---|
| docs-A family A (A0, A2, A4-50, A10 ceilings) | the harness is merged (#862); the part-4 private config builds the arms in-process; retrieval-only, no reader, no grader, no gateway | the prereg freeze (step 3) |
| docs-A A3, A4-D, A5-D, A6, A7, A9, census | same | freeze |
| set P API arms (A0–A6, A10 to depth 100) | our API against the bench copy at `rules/3@file/1`, read-only; the 155 held privately on our host | freeze |
| set P reranker-input arms (A7) | offline on captured pools; no restart, no store write | freeze |
| set P under the four glossary conditions (E8) | — | round 2's live-ops plan (swap / census / restore) |
| round 2 (family B) | — | the R2 record sets frozen (`accepted-<set>.jsonl`) |
| A8 alternative reranker | — | owner decision (D7: exploratory only) + download + a second sidecar on a scratch port |
| E7 judged answer-presence | — | a local Qwen3.6 judge (D8); a gateway judge would be a new dependency |

## Ordered steps

1. **Lab-book corrections** — **done** (#860, addendum 2026-10-29 in `../../glossbench-2026-10/LABBOOK.md`):
   the "85 vs 65" pairing retracted; the 105/155 pool ceiling at depth 50 recorded; the partner's shuffled
   control described. Still open, separately: `config.py`'s comment quotes superseded dev-trial figures
   (62 → 81) that the correction says not to quote; a one-line fix outside this record.
2. **Harness** — **done** (#862, `python/scripts/eval/reranker_test.py`, reviewed: escaped-form text-leak
   scan, family identity gate, part-4 reference loader, A7 labels).
3. **Power table + freeze** (owner + Fable). `reranker_test.py power-table --pages 208 --questions-n 735
   --seed 261029 --out power-table-reranker.json`, saved beside the results; the owner's go; sha +
   timestamp into the private `PREREG.sha256` (README).
4. **docs-A family A** (ours, ~1 h of compute). `pools --backend inprocess --part4-arm T512o0
   --private-config CFG --depths 10,20,50,100,200`; `rerank --identity-reference part4-retrieval.jsonl
   --identity-arm T512o0`; `fuse`; `census`; `stats --set-label docs-A/T512o0`. Retrieval-only: 735
   queries; dense via the embedding fleet (≤ 2 in flight per replica), reranker ≤ 4 in flight; pairs ≈
   735 × (50 + 100 + 200 + A5 depths) ≈ 400k, minutes on one GPU. Arms T512o0 (primary) and L
   (replication). Clean worktree at the pinned commit; `dirty: false, untracked: 0`. The D3 re-score of
   the step-7 private result for A0, A2, A4-50 runs beside it as the pre-committed check.
5. **Set P, API arms** (ours, on our host). `pools --backend api --base-url … --collection … --live
   hybrid@50,bm25@50,vector@50 --depths 10,20,50,100 --server-settings SERVER.json` against the bench copy
   at `rules/3@file/1`; census before and after (read-only). Depth 200 is absent on P (D10).
6. **Set P, offline reranker-input arms** (ours). `rerank` on the captured pools; identity gate against
   the live A4-50 capture first. The partner's own read-only re-measurement over the same bench copy is
   welcome as a cross-check and is reported as such.
7. **Write-up** (Fable): `RESULTS.md` + `artifacts/*.json` in this folder, counts only, aliases only, the
   decomposition table per arm × set, every H as confirmed / not confirmed / not shown non-inferior /
   GATE-NOT-EVALUABLE with its floor. Glossbench's lab book links here. No claim before `check_claims.py`
   passes.
8. **Round 2 (family B)** when the R2 sets are frozen: steps 4–7 repeated unchanged on O, A, P; the R2
   prereg cites this one. E8 (glossary conditions × reranker arms) rides on round 2's swaps under its
   live-ops plan.
9. **Optional, exploratory:** A8 (second sidecar, `MODEL_NAME` env, scratch port, pid file, never a
   process-name kill), E7 (local judge).

## Compute and dependencies

- GPU: the deployed reranker sidecar (shared; ≤ 4 in flight) and the embedding fleet for docs-A dense
  legs. A8 would need one more GPU for the duration of its run.
- Stores: bench copies only (docs-A bench index; papers-OC bench copy). No live collection. Steps 4–6 are
  read-only; only E8 writes (swap/restore), under round 2's live-ops plan.
- No gateway dependency. E7 is the only place one could arise; it is optional and flagged.
- Round-2 dependency: family B only. Family A and set P need nothing from round 2.

## Risks named now

- docs-A's reranked arm is near ceiling (622/735 at k10 in round 1's step 7), so H1 may be unresolvable
  there; that is a reported floor, not a failure, and R2 is the decisive set for the value-stating-chunk
  regime.
- The API route caps pool capture at 100; depth-200 arms run in-process on docs-A only. The in-process
  BM25 is an approximation of the store's analyzer, so it is not used as a stand-in for the store on
  papers-OC.
- An absent identity reference gates the family (prereg §8); on docs-A the part-4 retrieval store must be
  readable by `rerank` before `stats` runs.
- The sidecar's installed `sentence-transformers` is outside its `requirements.txt` pin (`<4`); the record
  names the installed version (`--sidecar-deps`). Not a blocker; a drift to fix separately.
