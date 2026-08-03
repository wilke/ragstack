#!/usr/bin/env cwl-runner
# Bulk-ingest workflow (ADR-0001 step 2). Scatters `ingest_shard` over the JSONL
# shard files — one independent, stateless ingest task per shard, spreadable
# across GPUs/endpoints — then gathers the per-shard receipts into a run summary
# via `merge_receipts`. The workflow engine (GoWe / any CWL v1.2 runner) owns
# scatter, retry, and resume, replacing the bespoke checkpoint machinery in
# ingest_jsonl.py (#71); each shard task is idempotent (deterministic uuid5 ids +
# upsert-only), so a retry is safe.
#
# CONTAINERIZED (#135). Each step runs inside the ragstack-worker image via
# DockerRequirement, so the `ragstack` package + its deps (qdrant-client / httpx /
# elasticsearch / the HF tokenizer) come from the pinned image — NOT from the old
# `InitialWorkDirRequirement: {pkgdir: ../python}` staging + PYTHONPATH hack, which
# only worked next to a checkout and needed a ragstack-provisioned env on the host.
# The scripts live in the image at /opt/ragstack/scripts (baseCommand).
#
# Image resolution: `dockerImageId: ragstack-worker.sif` is a bare filename, not a
# host path. cwltool --singularity finds it in $CWL_SINGULARITY_CACHE (point that
# at apptainer/images/); GoWe resolves it from its image store (override per run
# with a gowe:Execution.docker_image hint). cwltool does NOT expand `$(inputs...)`
# expressions inside DockerRequirement, so the image can't be a CWL input — the
# filename is the seam instead. Build it with:
#   apptainer build --sandbox /rag/tmp/ragstack-worker.sbx apptainer/ragstack-worker.def
#   apptainer build apptainer/images/ragstack-worker.sif /rag/tmp/ragstack-worker.sbx
#
# The steps reach the embedding fleet + Qdrant/ES over the network (NetworkAccess)
# and read the HF tokenizer from a bind-mounted HF_HOME (export APPTAINER_BIND, and
# HF_HOME, on the host so cwltool/apptainer pass them through). merge_receipts is
# pure stdlib+ragstack but runs in the same image for uniformity.
#
#   CWL_SINGULARITY_CACHE=apptainer/images \
#     cwltool --singularity cwl/ingest-bulk.cwl cwl/ingest-bulk.inputs.yml
#
# NOTE: pinning shards to specific embedding endpoints (the current bash k%N
# scheme) becomes GoWe's per-task endpoint assignment; here every shard is handed
# the same endpoint list.
cwlVersion: v1.2
class: Workflow

requirements:
  ScatterFeatureRequirement: {}

inputs:
  shards:
    type: File[]
    doc: "JSONL shard files (e.g. <uuid>.s0.jsonl ...)."
  collection: {type: string, doc: "Qdrant collection + ES index name."}
  tenant: {type: string, default: "public"}
  chunk_method: {type: string, default: "fixed_token"}
  chunk_size: {type: int, default: 256}
  chunk_overlap: {type: int, default: 32}
  embedding_url:
    type: string[]
    doc: "SFR embedding endpoint base URLs."
  embedding_model: {type: string, default: "Salesforce/SFR-Embedding-Mistral"}
  embedding_api_key:
    type: ["null", string]
    doc: "Bearer token for keyed endpoints."

steps:
  ingest_shard:
    doc: "Ingest one JSONL shard -> receipt.json (stateless, idempotent)."
    scatter: shard
    in:
      shard: shards
      collection: collection
      tenant: tenant
      chunk_method: chunk_method
      chunk_size: chunk_size
      chunk_overlap: chunk_overlap
      embedding_url: embedding_url
      embedding_model: embedding_model
      embedding_api_key: embedding_api_key
    out: [receipt]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerImageId: ragstack-worker.sif
        NetworkAccess:
          networkAccess: true
      baseCommand: [python, /opt/ragstack/scripts/ingest_shard.py]
      inputs:
        shard: {type: File, inputBinding: {position: 2}}
        collection: {type: string, inputBinding: {prefix: --collection, position: 3}}
        tenant: {type: string, inputBinding: {prefix: --tenant, position: 4}}
        chunk_method: {type: string, inputBinding: {prefix: --chunk-method, position: 5}}
        chunk_size: {type: int, inputBinding: {prefix: --chunk-size, position: 6}}
        chunk_overlap: {type: int, inputBinding: {prefix: --chunk-overlap, position: 7}}
        embedding_model: {type: string, inputBinding: {prefix: --embedding-model, position: 8}}
        embedding_api_key:
          type: ["null", string]
          inputBinding: {prefix: --embedding-api-key, position: 9}
        embedding_url:
          type: string[]
          inputBinding: {prefix: --embedding-url, position: 10}
      arguments:
        - {position: 11, prefix: --es-index, valueFrom: $(inputs.collection)}
        - {position: 12, prefix: --out, valueFrom: receipt.json}
      outputs:
        receipt: {type: File, outputBinding: {glob: receipt.json}}

  merge:
    doc: "Gather per-shard receipts -> run summary (totals + failed shards)."
    in:
      receipts: ingest_shard/receipt
    out: [summary]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerImageId: ragstack-worker.sif
      baseCommand: [python, /opt/ragstack/scripts/merge_receipts.py]
      inputs:
        receipts:
          type: File[]
          inputBinding: {position: 2}
      arguments:
        - {position: 3, prefix: --out, valueFrom: summary.json}
      outputs:
        summary: {type: File, outputBinding: {glob: summary.json}}

outputs:
  summary:
    type: File
    outputSource: merge/summary
  # The per-shard receipts are surfaced as a workflow output too, so a driver
  # (GoWeBackend) can download each and map it back to an ItemResult.
  receipts:
    type: File[]
    outputSource: ingest_shard/receipt
