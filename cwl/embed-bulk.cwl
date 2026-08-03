#!/usr/bin/env cwl-runner
# Bulk EMBED workflow (ADR-0001 offline plane, #141). Scatters `embed_shard` over
# the JSONL shard files — one independent, stateless embed task per shard, GPU/
# embedding-bound and never touching Qdrant/ES — producing one JSONL **embedding
# file** per shard plus a receipt, then gathers the receipts into a run summary.
#
# This is the first half of the decoupled bulk pipeline: it replaces the coupled
# `ingest-bulk.cwl` (chunk->embed->upsert inline) whenever a busy/capped Qdrant
# would otherwise stall the embedding fleet. The produced embedding files are fed
# to the separate `load-embeddings.cwl` (or the standalone backpressure loader).
#
# CONTAINERIZED (#135). Each step runs inside the ragstack-worker image via
# DockerRequirement — the `ragstack` package, the embedder client, and the HF
# tokenizer all come from the pinned image, replacing the old `pkgdir: ../python`
# staging + PYTHONPATH hack. Scripts live at /opt/ragstack/scripts (baseCommand).
# Image resolution + build + HF_HOME/network notes: see cwl/ingest-bulk.cwl header.
#
#   CWL_SINGULARITY_CACHE=apptainer/images \
#     cwltool --singularity cwl/embed-bulk.cwl cwl/embed-bulk.inputs.yml
cwlVersion: v1.2
class: Workflow

requirements:
  ScatterFeatureRequirement: {}

inputs:
  shards:
    type: File[]
    doc: "JSONL shard files (e.g. <uuid>.s0.jsonl ...)."
  tenant:
    type: string
    default: "public"
  chunk_method:
    type: string
    default: "fixed_token"
  chunk_size:
    type: int
    default: 256
  chunk_overlap:
    type: int
    default: 32
  embedding_url:
    type: string[]
    doc: "SFR embedding endpoint base URLs."
  embedding_model:
    type: string
    default: "Salesforce/SFR-Embedding-Mistral"
  embedding_api_key:
    type: ["null", string]
    doc: "Bearer token for keyed endpoints."

steps:
  embed_shard:
    doc: "Embed one JSONL shard -> <shard>.emb.jsonl + receipt.json (no store contact)."
    scatter: shard
    in:
      shard: shards
      tenant: tenant
      chunk_method: chunk_method
      chunk_size: chunk_size
      chunk_overlap: chunk_overlap
      embedding_url: embedding_url
      embedding_model: embedding_model
      embedding_api_key: embedding_api_key
    out: [embeddings, receipt]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerImageId: ragstack-worker.sif
        NetworkAccess:
          networkAccess: true
      baseCommand: [python, /opt/ragstack/scripts/embed_shard.py]
      inputs:
        shard:
          type: File
          inputBinding:
            position: 2
        tenant:
          type: string
          inputBinding:
            prefix: --tenant
            position: 3
        chunk_method:
          type: string
          inputBinding:
            prefix: --chunk-method
            position: 4
        chunk_size:
          type: int
          inputBinding:
            prefix: --chunk-size
            position: 5
        chunk_overlap:
          type: int
          inputBinding:
            prefix: --chunk-overlap
            position: 6
        embedding_model:
          type: string
          inputBinding:
            prefix: --embedding-model
            position: 7
        embedding_api_key:
          type: ["null", string]
          inputBinding:
            prefix: --embedding-api-key
            position: 8
        embedding_url:
          type: string[]
          inputBinding:
            prefix: --embedding-url
            position: 9
      arguments:
        - position: 10
          prefix: --out
          valueFrom: $(inputs.shard.nameroot).emb.jsonl
        - position: 11
          prefix: --receipt
          valueFrom: receipt.json
      outputs:
        embeddings:
          type: File
          outputBinding:
            glob: $(inputs.shard.nameroot).emb.jsonl
        receipt:
          type: File
          outputBinding:
            glob: receipt.json

  merge:
    doc: "Gather per-shard receipts -> run summary (totals + failed shards)."
    in:
      receipts: embed_shard/receipt
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
          inputBinding:
            position: 2
      arguments:
        - position: 3
          prefix: --out
          valueFrom: summary.json
      outputs:
        summary:
          type: File
          outputBinding:
            glob: summary.json

outputs:
  # The embedding files — the input to the load stage (load-embeddings.cwl).
  embeddings:
    type: File[]
    outputSource: embed_shard/embeddings
  summary:
    type: File
    outputSource: merge/summary
  receipts:
    type: File[]
    outputSource: embed_shard/receipt
