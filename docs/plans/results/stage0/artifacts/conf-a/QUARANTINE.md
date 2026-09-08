# QUARANTINE — confirmation-topic artifacts

Everything under this directory belongs to the **80 TREC CDS confirmation topics** of
`docs/plans/results/design/SPEC-confirmation-run.md` (r2) §2 and
`SPEC-confirmation-run-r3.md`. It was produced by the task recorded in
`docs/plans/results/stage0/RESULTS-confirmation-run-a-setup.md` (the `s0c_*.py` harness).

## The rule

**No aggregate metric over confirmation topics is computed, printed, logged, or committed
by the task that wrote this directory.** Specifically:

1. Per-arm retrieval outputs (`pools/`), packed contexts (`contexts/`) and the labeling
   records (`labels/`) are **written and never summarised**. They are data on disk, not
   results.
2. The labeler consumes **only the pooled `(topic, doc)` id list** (`pool/labeling_set.json`).
   It never sees a ranking, a similarity, a fusion score, a rerank score, or a budget-
   realised token total for any confirmation topic.
3. `ERET`, `EPACK`, `EUC`, and every per-topic or per-arm value of them are **not computed**.
   `s0c_common.py` defines them as functions that **raise** while `QUARANTINE` is true;
   there is no code path in this harness that returns one.
4. Only **counts** are reported: pairs per topic, totals, records written, wall time,
   throughput. Counts are not outcomes.

## What may be reported out of this directory before unblinding

Pair counts, document counts, record counts, file sizes, sha256 digests, wall-clock and
throughput. Nothing else.

## What unblinds it

r2 §P.9 step 3→4: the two-reader human read (r3 §5 step 3) produces κ, the labels are
frozen and hashed, the exclusion list is fixed, and the PREREG hash is recorded. Only then
may these outputs be opened for analysis. Neither the read nor the freeze was performed by
the task that wrote this directory.

## Layout

```
conf/
  QUARANTINE.md          this file
  queries/               the 160 query strings + their embeddings + manifest
  pools/                 pool_<arm>.jsonl — frozen 50-chunk pools, 3 modes, reranked
  contexts/              packed-conf.jsonl.gz (reference form) + text/<arm>.jsonl.gz
  pool/                  labeling_set.json (ids only) + pool_counts.json
  gentok/                per-unit generator-token counts (windowing input, no outcome)
  labels/                labels-conf-<judge>.jsonl, raw-conf-<judge>.jsonl, manifests
  run/                   progress + pid files of the detached labeling supervisor
```
