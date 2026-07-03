# CWL workflows (ADR-0001 offline plane)

Scaffold for the **offline / throughput plane** of [ADR-0001](../docs/adr/0001-execution-topology.md):
bulk ingestion and the eval/benchmark harnesses expressed as CWL DAGs, executed by
[GoWe](../../GoWe) (or any CWL v1.2 runner). This directory is **step 1** of the
ADR rollout — the eval harness — the lowest-risk entry point (pure batch, file
outputs, no service to break).

## What's here

| File | Role |
|---|---|
| `eval-scifact-chunking.cwl` | Scatter/gather workflow: ingest+score each chunking config independently, then aggregate the stats. |
| `eval-scifact-chunking.inputs.yml` | Example inputs (the configs to compare + the embedding key). |

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
