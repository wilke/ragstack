# CWL workflows (ADR-0001 offline plane)

Scaffold for the **offline / throughput plane** of [ADR-0001](../docs/adr/0001-execution-topology.md):
bulk ingestion and the eval/benchmark harnesses expressed as CWL DAGs, executed by
[GoWe](../../GoWe) (or any CWL v1.2 runner). Covers **step 1** (the eval harness —
the lowest-risk entry point) and **step 2** (bulk ingest — the atomic per-shard
tool + receipts).

## What's here

| File | Role |
|---|---|
| `eval-scifact-chunking.cwl` | **Step 1.** Scatter/gather: ingest+score each chunking config independently, then aggregate the stats. |
| `eval-scifact-chunking.inputs.yml` | Example inputs (the configs to compare + the embedding key). |
| `ingest-bulk.cwl` | **Step 2.** Scatter/gather: ingest each JSONL shard independently → receipt, then merge receipts into a run summary. |
| `ingest-bulk.inputs.yml` | Example inputs (shard files + collection + embedding endpoints). |

### Step 2 tools (bulk ingest)

- **`python/scripts/ingest_shard.py`** — scatter step. Ingests **one** JSONL shard
  → `receipt.json` (chunk ids + per-doc catalog), reusing `IngestionPipeline`
  (chunk→embed→quarantine→delete-prior→upsert→neighbor-link). **Stateless +
  idempotent**: no checkpoint/resume — the engine owns scatter/retry/resume, and a
  re-run overwrites in place (deterministic uuid5 ids + upsert), so a GoWe retry is
  safe. This is what retires `ingest_jsonl.py`'s bespoke machinery (#71) without
  forking the pipeline (#25). Needs live infra — not a CI step. The reusable core
  is `ragstack.ingestion.shard.run_shard` (offline-tested with in-memory stores).
- **`python/scripts/merge_receipts.py`** — gather step. Folds the per-shard
  receipts into a run summary (totals + **failed-shard ids**, so partial failure is
  surfaced, not silently under-ingested). Pure computation. `--fail-on-shard-error`
  makes it a gate. Verified end-to-end under `cwltool`.
- Receipt contract: `ragstack.ingestion.receipts` (`ShardReceipt`/`DocRow`) — the
  step's file output; the Qdrant/ES upsert is the side effect (#62). `DocRow.metadata`
  is the light `index_metadata` catalog subset (title/doc_type/doi/authors/year/…);
  it does **not** carry `ingest_jsonl.py --catalog-out`'s full enriched dump
  (citations/abstract are dropped).

### Runtime requirement (real GoWe workers)

Both steps stage `python/` and set `PYTHONPATH` so the staged `ragstack` imports —
this is what makes them run on a GoWe worker (default-executor `worker` →
apptainer), not only under cwltool. Verified: the **merge step runs end-to-end
with no external `PYTHONPATH`** (it needs only stdlib + pure-python `ragstack`).

**`ingest_shard` additionally needs `ragstack`'s dependencies** (qdrant-client,
httpx, elasticsearch, the tokenizer stack) in the worker's runtime — staging the
source is necessary but not sufficient. Two ways to provide them (a dedicated
`--runtime none` worker in the ragstack env, or a ragstack SIF) are covered in the
**dedicated-worker path** below; on a stock container the step
`ModuleNotFoundError`s on those deps.

### Step 2b — submitting to a live GoWe engine

- **`ragstack.ingestion.gowe_client.GoWeClient`** — async REST wrapper over the
  GoWe API: `register_workflow` → `submit` → `wait` → `download`. **Auth is a
  BV-BRC token** (anonymous submission is disabled on the deployed server); it's
  loaded from `$GOWE_TOKEN`/`$BVBRC_TOKEN` or the standard token files
  (`~/.gowe/credentials.json`, `~/.patric_token`, …) and sent verbatim in the
  `Authorization` header. Validated end-to-end against the live server (register a
  workflow, submit with the token, poll to COMPLETED on a worker, download the
  output) — see the `GOWE_LIVE=1` round-trip test.
- **`ragstack.ingestion.gowe_backend.GoWeBackend`** — satisfies the in-process
  `IngestBackend` protocol: `run_shards` submits the shards' source files as a
  scatter workflow (this `ingest-bulk.cwl`, whose `receipts` output it collects),
  waits, downloads the per-shard receipts, and maps them to `ItemResult`s
  (positionally — CWL scatter preserves order). Pass `worker_group=` to route the
  run to a dedicated worker (below); each `WorkItem.source` is a shard file GoWe's
  workers can read.

### Running `ingest_shard` on GoWe — the dedicated-worker path (#135)

`ingest_shard` needs ragstack's *dependencies* (qdrant-client/httpx/elasticsearch/
tokenizers), which a stock apptainer worker container lacks. Two ways to provide
them:

- **(A, validated) A `--runtime none` worker in the ragstack conda env.** Start a
  dedicated worker whose `python` is the ragstack env's, in its own group, and
  route ingest tasks to it. No image to build; deps come from the env, ragstack
  code from the CWL's staged `python/` (`PYTHONPATH`). **Proven end-to-end**: an
  `ingest_shard` task ran on such a worker (deps + ragstack resolved, embedded via
  the BGE sidecar, wrote a receipt).

  ```bash
  # `python` on the worker's PATH must be the ragstack env's (deps live there);
  # --runtime none runs the tool directly on the host (no container). HF_HOME points
  # the tokenizer cache at a SHARED persistent dir — see the gotcha below.
  PATH="/rag/envs/ragstack/bin:$PATH" HF_HOME=/rag/cache gowe-worker \
      --server http://localhost:8091 --runtime none \
      --name ragstack-cpu-1 --group ragstack-cpu \
      --workdir /scout/wf/data/ragstack-workdir --stage-out file:///scout/wf/data
  ```
  Route to it with `GoWeBackend(..., worker_group="ragstack-cpu")` (sends a
  submission `worker_group` label — it propagates to every scatter + merge task) or
  a `gowe:Execution.worker_group` CWL hint (which wins over the label). **The label
  selects the *group*, not the *executor*** — it assumes the server runs
  `--default-executor worker`; on a `--default-executor local` server the group is
  ignored and the tool runs on the *server* host instead.

  Gotchas with `--runtime none`: the tool runs with **`HOME`=the task workdir** (an
  ephemeral per-task dir) and a per-task `TMPDIR`. That matters for the tokenizer:
  the default chunk method `fixed_token` **requires** the HF offset tokenizer
  (`--chunk-token-counter estimate` is force-reverted to `hf` for token-window
  chunking, so it does *not* skip the download), and the default HF cache is
  `$HOME/.cache/huggingface` — so with `HOME`=workdir the model tokenizer would
  re-download **every task**. Set a shared **`HF_HOME`** (as above) so it's fetched
  once and reused. (`estimate` only helps the char/word/sentence methods, and isn't
  even wired through `ingest-bulk.cwl`.) Input/output files must live under the
  server's `--upload-download-dirs`.

- **(B, follow-up) A ragstack-provisioned worker SIF.** A pinned apptainer image
  with ragstack + deps, referenced via `DockerRequirement`/`gowe:Execution.docker_image`
  — the reproducible/portable production path (multi-host). Heavier (torch/tokenizer
  stack). Tracked in #135.

**Remaining seam:** wiring `GoWeBackend` into the API's `ShardedIngestor` (config
`INGEST_BACKEND=gowe`) so an API ingest can fan out to GoWe.

The two step tools live in the ragstack package's script tree:

- **`python/scripts/eval/chunk_one.py`** — scatter step. Ingests + scores **one**
  chunking config against SciFact (BEIR) and emits `metrics.json` (per-query metric
  arrays + means). A thin CLI over the existing `scifact_chunk_eval` harness — no
  reimplementation of the chunk/embed/ingest/score logic.
- **`python/scripts/eval/aggregate_stats.py`** — gather step. Reads the per-config
  `metrics.json` files and writes `report.md` (metrics table + paired-bootstrap
  difference CIs + Holm-corrected Wilcoxon), reusing the harness's assemblers and
  the `_stats` layer. Pure computation — no GPU/store/network.

## Running it

Each step stages `python/scripts/eval/` into its job sandbox via
`InitialWorkDirRequirement`, so the workflow is **CWD-independent and portable**
across CWL runners — no PATH or working-directory assumptions:

```bash
. /rag/bin/activate            # ragstack env + endpoints on PATH
cwltool cwl/eval-scifact-chunking.cwl cwl/eval-scifact-chunking.inputs.yml
```

On GoWe, submit the same document (local or Apptainer backend). GoWe owns scatter,
retry, and — for the future bulk-ingest workflow — checkpoint/resume, subsuming
the bespoke machinery in `ingest_jsonl.py` (#71).

The `aggregate` step is verified end-to-end under `cwltool` (it's pure
computation); the full workflow is `cwltool --validate`-clean.

### Requirements & caveats

- **`ragstack` must be importable** in the runtime env (installed in the conda env,
  or present in the run SIF) — the staged tools `import ragstack.*`.
- **`chunk_one` needs live infra**: the SFR embedding fleet + Qdrant + ES. It
  ingests into isolated `scifact_m7_<config>` stores and tears them down (the
  prefix-guarded teardown never touches a production collection). It is **not** a
  CI step — same as the harness it wraps. `aggregate_stats` *is* CI-friendly.
- **Optional follow-up**: packaging the tools as `ragstack.eval` console-scripts
  would let the `baseCommand` be a bare command (no dir-staging) — a nicety, not a
  blocker now that the steps stage the dir. Tracked issue linked from the PR.

## Roadmap (ADR-0001 rollout)

1. **Eval CWL (this)** — scatter/gather over chunking configs.
2. **`GoWeBackend` for bulk ingest** — `ingest_shard` per-shard tool + receipt
   files + a `GoWeBackend` implementing the existing `IngestBackend` seam. Retires
   the #71 resume machinery and dissolves the #25 pipeline duplication.
3. Go embedding-router sidecar; 4. Go API gateway (see the ADR).
