# GoWe Integration & Performance Evaluation

**Status:** implemented and validated on the live engine · **Date:** 2026-07-04 · **Host:** `coconut`

This document is the reference for RAGStack's integration with **[GoWe](../../GoWe)**, the
CWL v1.2 workflow engine that runs the [ADR-0001](adr/0001-execution-topology.md) **offline
/ throughput plane**. It covers: what is supported, the workflows, the client/backend
code and its tests, the execution runtimes, and a rigorous performance evaluation with
its methodology, results, threats to validity, and reproduction steps.

---

## 1. Summary

Per ADR-0001, bulk ingestion and the eval/benchmark harnesses are expressed as **CWL
DAGs** and executed by GoWe, so the workflow engine owns scatter / retry / resume instead
of a hand-rolled in-process loop. RAGStack provides:

- **CWL workflows** — `cwl/ingest-bulk.cwl` (bulk ingest) and `cwl/eval-scifact-chunking.cwl`
  (chunking eval), each a scatter→gather DAG over a package-installed Python step tool.
- **A REST client** — `ragstack.ingestion.gowe_client.GoWeClient` (register → submit →
  wait → download), authenticated with a **BV-BRC token** (anonymous submission is disabled
  on the deployed server).
- **An `IngestBackend`** — `ragstack.ingestion.gowe_backend.GoWeBackend`, which submits the
  shards as a scatter workflow, collects the per-shard **receipts**, and maps them to
  `ItemResult`s (positionally — CWL scatter preserves order).

The design goal is **not** single-node throughput (the workload is GPU-bound on the
embedder — see §5) but **orchestration**: scatter across a cluster, deterministic retry/
resume, and reproducible runs. The measurements in §5 confirm this framing quantitatively.

---

## 2. Supported workflows

Both are scatter→gather DAGs; both are `cwltool --validate`-clean and have been executed
end-to-end on the live GoWe server.

| Workflow | Scatter step (per element) | Gather step | Workflow output |
|---|---|---|---|
| **`cwl/ingest-bulk.cwl`** | `scripts/ingest_shard.py` — ingest one JSONL shard → `receipt.json` | `scripts/merge_receipts.py` → run summary | `summary` (JSON) + `receipts[]` (per-shard) |
| **`cwl/eval-scifact-chunking.cwl`** | `scripts/eval/chunk_one.py` — ingest+score one chunking config on SciFact → `metrics.json` | `scripts/eval/aggregate_stats.py` → stats report | `report.md` (metrics table + paired-bootstrap CIs + Holm–Wilcoxon) |

**Inputs (bulk):** JSONL shard files (`File[]`), target `collection` (Qdrant collection +
ES index), embedding endpoint URL(s), model, chunk method/size/overlap, tenant.
**Inputs (eval):** the chunking configs to compare (`string[]`) and an optional embedding key.

**Atomic step tools** (reused, not re-implemented — the ADR's #25 no-fork rule):

- `ingest_shard` is a thin, **stateless, idempotent** CLI over `IngestionPipeline.ingest`
  (deterministic `uuid5` ids + upsert → a retry overwrites in place). It carries no
  checkpoint/resume — the engine owns that.
- `chunk_one` / `aggregate_stats` wrap the existing SciFact harness (`scifact_chunk_eval`)
  and the shared `_stats` layer.
- Receipt contract: `ragstack.ingestion.receipts.ShardReceipt` (chunk ids + per-doc
  catalog). A CWL step is file-in/file-out, but the real output is the Qdrant/ES upsert
  (the side effect); the receipt is the auditable file artifact the gather step folds.

---

## 3. Client, backend, and tests

| Component | Responsibility |
|---|---|
| `GoWeClient` | Async REST wrapper: `register_workflow`, `submit` (+ `dry_run`), `get_submission`, `wait` (poll to a terminal state, bounded by `timeout`), `download`, `upload`. Loads the BV-BRC token from `$GOWE_TOKEN`/`$BVBRC_TOKEN` or the standard token files. |
| `GoWeBackend` | Implements the `IngestBackend` protocol. `run_shards` submits the shard files as a scatter workflow, waits, downloads the per-shard receipts, and maps them positionally to `ItemResult`s. Never raises for an engine failure (a failed/timed-out submission → all-items-failed). `worker_group=` routes via a submission label. |

**Automated tests** (`python/tests/ingestion/`):

- `test_gowe_client.py` — over `httpx.MockTransport`: auth header, register/submit/wait/
  download sequencing, `dry_run`, error → `GoWeError`, `wait` timeout, empty-body download,
  token loading (env precedence + JSON/bare files). Plus a **`GOWE_LIVE=1`** guarded
  round-trip against the real server.
- `test_gowe_backend.py` — over a fake client: positional receipt→`ItemResult` mapping,
  same-basename shards don't collide, single-`File` vs `File[]` receipts output,
  unreadable/malformed receipt → failed-not-raised, failed submission / submit error /
  missing receipt, `worker_group` label sent (and blank/whitespace normalized to none).

The full suite is 550+ passing; the GoWe modules are `ruff`-clean.

---

## 4. Runtimes & execution model

- **Server:** `http://localhost:8091`, `--default-executor worker`. **Auth is a BV-BRC
  token** in the `Authorization` header (the server strips an optional `Bearer` prefix).
- **Workers** run tasks in one of three runtimes (`internal/worker/runtime.go`): `apptainer`
  (default deployment; container per task), `docker`, or **`none`** (runs the command
  directly on the host — no container).
- **The RAGStack execution path (#135, "Option A", validated):** a dedicated
  **`--runtime none` worker in the ragstack conda env**, in its own group, routed to via a
  submission `worker_group` label. The tool's `python` is the env's (so ragstack's deps —
  qdrant-client / httpx / elasticsearch / tokenizers — resolve), and the ragstack *code* is
  staged into the task sandbox from the CWL's `python/` directory on `PYTHONPATH`. No image
  to build.

  ```bash
  PATH="/rag/envs/ragstack/bin:$PATH" HF_HOME=/rag/cache gowe-worker \
      --server http://localhost:8091 --runtime none \
      --name ragstack-cpu-1 --group ragstack-cpu \
      --workdir /scout/wf/data/ragstack-workdir --stage-out file:///scout/wf/data
  ```

  **Routing** is verified against the GoWe scheduler source: a task labeled
  `worker_group=ragstack-cpu` can **never** be claimed by a `default`-group worker
  (`store/sqlite.go` matcher), so ingest cannot silently land on a stock container and
  `ModuleNotFound`. The label propagates to every scatter + merge task.

  **`--runtime none` gotchas** (`internal/toolexec/execute.go`): the tool runs with
  `HOME`=the ephemeral task workdir and a per-task `TMPDIR`. The production chunk method
  `fixed_token` **requires** the HF offset tokenizer (`--chunk-token-counter estimate` is
  force-reverted to `hf`), and the default HF cache is `$HOME/.cache/huggingface` — so a
  shared **`HF_HOME`** is required, else the model tokenizer re-downloads every task.
  Input/output files must live under the server's `--upload-download-dirs`.

- **Alternative (#135, "Option B", follow-up):** a ragstack-provisioned apptainer SIF
  referenced via `DockerRequirement` — the reproducible, portable, multi-host path.

---

## 5. Performance evaluation

### 5.1 Objective

Quantify (a) GoWe's **per-shard orchestration overhead** (scheduling + input staging), and
(b) realistic **ingest throughput** (docs/s, chunks/s) — and locate both relative to the
dominant cost (the embedder).

### 5.2 Environment

| Axis | Value |
|---|---|
| Host | `coconut` — 8× NVIDIA H200 NVL (144 GB) |
| GoWe | server `:8091`; workers: 4× apptainer (`default` group) + 1× `--runtime none` (`ragstack-cpu`, this study). **Single ragstack worker → scatter serializes.** |
| Embedders | **SFR-Embedding-Mistral** (4096-d) on vLLM `:9001–9008` (GPU); **BGE-base** (768-d) sidecar `:50053` (CPU) |
| Stores | Qdrant `:6333`, Elasticsearch `:9200` (throwaway collection/index `ragstack_perf_bench`, dim 4096; torn down after) |
| Chunker | `fixed_token`, 256-token window / 32 overlap (production default) |
| Runtime | ragstack conda env, Python 3.12; worker poll interval **5 s** (default) |
| **Confound** | A live 448k-doc corpus build was **concurrently embedding on `:9001–9008`** during the SFR measurements — a real, uncontrolled source of embed-latency variance. |

### 5.3 Method

Two experiments; each `ingest_shard` invocation is a fresh Python process (so each pays a
fixed ~2 s of interpreter + ragstack import + tokenizer load — the same cost GoWe tasks
pay). "GoWe (server)" is the submission's `created_at → completed_at` (isolates the engine
from the client's poll granularity). "Direct" runs the identical CLI locally.

- **Exp 1 — overhead isolation:** one 15-doc shard → 15 chunks; direct vs GoWe; measured on
  the **isolated BGE** sidecar (no build contention) and on **SFR** (shared). n=3/2.
- **Exp 2 — realistic throughput:** a deterministic 300-doc corpus (~5 KB/doc) split into
  **6 shards × 50 docs** → 1500 chunks; **SFR**, durable Qdrant+ES. Direct = 6 shards run
  sequentially; GoWe = 6-shard scatter (serial on the single worker). n=2 direct, n=1 GoWe.

### 5.4 Results

**Exp 1 — per-shard overhead** (15 docs → 15 chunks):

| Embedder | Direct (warm) | GoWe (server) | Overhead / shard |
|---|---|---|---|
| BGE (isolated) | ~2.4 s | 3.4–5.4 s (med ~4.3) | **~1–3 s** |
| SFR (shared) | 2.2–6.0 s (variable) | ~5.6 s | ~1.5 s |

**Exp 2 — throughput** (300 docs → 1500 chunks, 6 shards, SFR, durable):

| Mode | Wall (s) | docs/s | chunks/s | Notes |
|---|---|---|---|---|
| Direct, run 1 | 37.2 | 8.0 | 40.3 | per-shard 5.88–6.37 s (tight within a run) |
| Direct, run 2 | 52.8 | 5.68 | 28.4 | same code/data — **+42 % from endpoint contention** |
| GoWe scatter (1 worker) | 55.9 | 5.37 | 26.8 | 6/6 completed; server-side wall |

Durable write confirmed: Qdrant reported **1500 points @ 4096-d** in `ragstack_perf_bench`.

### 5.5 Analysis

1. **Throughput is embed-bound and contention-dominated.** The same direct run varied
   **37.2 s → 52.8 s (±~30–40 %)** purely from sharing `:9001` with the live build. This
   variance **exceeds** GoWe's fixed overhead — on a loaded shared system the embedder, not
   the orchestrator, sets the pace.
2. **GoWe's overhead is small and fixed per shard (~1–3 s).** The clean number comes from
   Exp 1 on the *isolated* BGE endpoint (no confound): poll pickup (≤ the 5 s poll interval)
   + staging the `python/` directory into the task sandbox. It is **independent of shard
   size**, so it amortizes to noise on realistic shards (thousands of docs, minutes of
   embed). At the production loader's ~24 shards/file, ~3 s × 24 ≈ 72 s of orchestration vs
   ~30 min/file of embedding — under 4 %.
3. **On a single worker, GoWe scatter serializes → no parallelism, net slower by the
   overhead.** GoWe (5.37 docs/s) sits inside the direct range (5.7–8.0) — i.e. its overhead
   is comparable to the contention noise. GoWe's throughput *benefit* requires **multiple
   workers** (fan-out was not measured — see §7).
4. **Conclusion:** the value of the GoWe path is **orchestration** — scatter across a
   cluster, deterministic retry/resume, reproducibility, and the receipt audit trail — not
   single-node speed. This matches ADR-0001's explicit thesis.

### 5.6 Threats to validity

- **Uncontrolled contention.** The live build shared the SFR endpoints; SFR numbers carry
  ±30–40 % run-to-run variance. The *clean* overhead figure is therefore taken from the
  isolated-BGE experiment, not the SFR one.
- **Low replication** (n=1 for the 300-doc GoWe run; n=2 direct) — reported as ranges, not
  significance-tested. The overhead conclusion rests on the more-replicated Exp 1.
- **Single worker** — no scatter parallelism measured; the throughput comparison is
  serial-vs-serial (fair for overhead, not for GoWe's fan-out value).
- **Synthetic corpus** (deterministic, uniform ~5 KB docs) — real scientific articles vary
  widely in length (chunks/doc), which changes the fixed-cost amortization.
- **Cold-process cost** (~2 s import/tokenizer per invocation) is charged to every shard in
  both modes; fewer, larger shards amortize it better than many small ones.

### 5.7 Reproduction

Generate a deterministic corpus + 6 shards, then run direct vs GoWe (`GoWeClient` /
`GoWeBackend`) against a throwaway `ragstack_perf_*` collection, and tear it down
(prefix-guarded). The exact harness used is in the PR that introduced this document; the
step tool and defaults (`:9001`, SFR, qdrant/es, `fixed_token` 256/32) are `ingest_shard`'s.

---

## 6. Findings & recommendations

- **GoWe is production-viable for RAGStack bulk ingest today** via the `--runtime none`
  worker path (§4), with orchestration — not throughput — as the payoff.
- **Do not over-shard.** GoWe's ~3 s/shard fixed cost favors fewer, larger shards; the
  ~24-shards/file scheme is fine (overhead < 4 %), but shard-per-doc would be wasteful.
- **Tune for lower overhead if needed:** worker `--poll 500ms` (vs the 5 s default) cuts
  scheduling latency; packaging the step tools as console-scripts (#132) or a SIF removes
  the per-task `python/` staging copy.
- **Measure fan-out next** (multiple `ragstack-cpu` workers) — that's the experiment that
  shows GoWe's actual throughput value; single-node numbers cannot.

---

## 7. Known issues & follow-ups

| Item | Detail |
|---|---|
| **CWL flow-YAML incompatibility** | GoWe's Go YAML parser rejects flow-style array/optional types (`{type: "string[]"}`, `string?`) that `cwltool` accepts. **Fixed in this PR**: `cwl/ingest-bulk.cwl` now uses block-style array types and `["null", string]` optionals — verified it both `cwltool --validate`s and *registers* on the live GoWe (it did not before). |
| **#135 Option B** | A ragstack SIF for portable/multi-host workers (Option A is env-coupled to `/rag/envs/ragstack`). |
| **#132** | Console-scripts packaging → removes CWL dir-staging + the per-task copy. |
| **Seam wiring** | `INGEST_BACKEND=gowe` into the API's `ShardedIngestor` so an API ingest fans out to GoWe. |
| **Fan-out benchmark** | Plan-3 scatter-scaling (2–4 workers) — the unmeasured throughput dimension. |

See also: [`adr/0001-execution-topology.md`](adr/0001-execution-topology.md),
[`../cwl/README.md`](../cwl/README.md).
