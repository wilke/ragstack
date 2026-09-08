# Confirmation run, option (a) — the quarantined setup

*Run 2026-09-08. Implements the mechanical half of
[`../design/SPEC-confirmation-run-r3.md`](../design/SPEC-confirmation-run-r3.md) §10 item 5
option (a): "run the confirmation on all 80 under the frozen procedure with the projected
power printed" (P.7's closing clause). This document reports **what ran and how much of
it** — counts, throughput, projections, attestations. It reports **no outcome**, because
none was computed.*

> **Read this first.** Every artifact this run produced for a confirmation topic is
> quarantined under `/rag/tmp/stage0-conf/work/conf/`, behind that directory's
> `QUARANTINE.md`. No aggregate metric over confirmation topics was computed, printed,
> logged or committed. `ERET`, `EPACK` and `EUC` are defined in `s0c_common.py` as
> functions that **raise** while the `QUARANTINE` flag is true, and the flag is true for
> the whole of this task. The analysis is blocked by pre-registration until (i) the
> two-reader human read (r3 §5 step 3) has produced κ and (ii) the labels are frozen
> (r2 §P.9 step 3). **Neither is this task's**, and neither has happened.

---

## 1. What ran

| step | what | module |
|---|---|---|
| 1 | 160 confirmation queries (80 topics × {summary, description}) embedded on `:9001-:9006` | `s0c_retrieve.py` |
| 2 | 6 index arms × 3 modes at the served shape (per-leg 100, RRF k = 60, top 50), reranked on `:50052`, pools frozen and sha256'd | `s0c_retrieve.py` |
| 3 | 11 scoring arms packed at B ∈ {4,096 / 16,384 / 32,768} SFR tokens, contexts persisted in Stage 0b′'s reference form | `s0c_pack.py`, `s0c_contexts.py` |
| 4 | the labeling set pooled (r2 §6.3) — **counts only** | `s0c_pool.py` |
| 5 | the #513 locator fix as a post-filter, verified on the development labels | `s0c_span_filter.py` |
| 6 | the 30-reading labeling pass launched detached and handed off running | `s0c_label.py`, `s0c_supervise.sh` |

</content-placeholder>
