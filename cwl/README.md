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
httpx, elasticsearch, the tokenizer stack) in the worker's container — staging the
source is necessary but not sufficient. So the `ingest_shard` step requires a
**ragstack-provisioned worker image**; on a stock container it will
`ModuleNotFoundError` on those deps. Provisioning that image (or a `DockerRequirement`
hint) is the deployment follow-up — see the issue linked from the PR.

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
  waits, downloads the per-shard receipts, and maps them to `ItemResult`s. Each
  `WorkItem.source` is a shard file GoWe's workers can read.

**Still gated:** actually *running ingest* end-to-end on GoWe needs the
ragstack-provisioned worker image (#135) — the merge/receipt plumbing is proven,
but `ingest_shard` `ModuleNotFound`s ragstack's deps in a stock worker container.
Wiring `GoWeBackend` into the API's `ShardedIngestor` (config `INGEST_BACKEND=gowe`)
is the remaining seam work.

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
