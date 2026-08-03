#!/usr/bin/env cwl-runner
# Bulk LOAD workflow (ADR-0001 offline plane, #141). The store-bound second half
# of the decoupled pipeline: takes the embedding files produced by
# `embed-bulk.cwl` and upserts them into Qdrant/ES via `load_embeddings.py`
# (reusing `IngestionPipeline.index_chunks`). No embedding fleet needed.
#
# Deliberately a SINGLE task, not a scatter: the load is where Qdrant backpressure
# belongs (throttle upserts on live collection health), a stateful control loop,
# not a dataflow fan-out. Backpressure is a `BackpressuredVectorStore` decorator in
# `load_embeddings.py`, OFF by default — the capped-Qdrant A/B benchmark found it
# adds latency without preventing drops below crash-scale (millions of vectors +
# deferred indexing). Set `backpressure: true` for a very large corpus on a capped
# Qdrant.
#
#   cwltool cwl/load-embeddings.cwl cwl/load-embeddings.inputs.yml
#
# Input/output files must live under GoWe's --upload-download-dirs.
#
# CONTAINERIZED (#135). The task runs inside the ragstack-worker image via
# DockerRequirement — ragstack's store deps (qdrant-client / elasticsearch) come
# from the pinned image, replacing the old `pkgdir: ../python` staging + PYTHONPATH
# hack. Script lives at /opt/ragstack/scripts (baseCommand). Image resolution +
# build + network notes: see cwl/ingest-bulk.cwl header. (No HF tokenizer needed
# here — load is store-only.)
#
#   CWL_SINGULARITY_CACHE=apptainer/images \
#     cwltool --singularity cwl/load-embeddings.cwl cwl/load-embeddings.inputs.yml
cwlVersion: v1.2
class: Workflow

inputs:
  embeddings:
    type: File[]
    doc: "JSONL embedding files from embed-bulk.cwl (<shard>.emb.jsonl)."
  collection:
    type: string
    doc: "Qdrant collection name (vector dim is read from the embedding headers)."
  es_index:
    type: ["null", string]
    doc: "Elasticsearch index (defaults to the collection name)."
  tenant:
    type: ["null", string]
    doc: "Override tenant (default: each file's header tenant)."
  qdrant_url:
    type: string
    default: "http://localhost:6333"
  es_url:
    type: string
    default: "http://localhost:9200"
  fail_on_error:
    type: boolean
    default: true
  backpressure:
    type: boolean
    default: false
    doc: "Hold each upsert until the collection is green (#141). OFF by default;
      set true for a very large corpus on a capped Qdrant."

steps:
  load:
    doc: "Upsert all embedding files into Qdrant/ES -> load summary."
    in:
      embeddings: embeddings
      collection: collection
      es_index: es_index
      tenant: tenant
      qdrant_url: qdrant_url
      es_url: es_url
      fail_on_error: fail_on_error
      backpressure: backpressure
    out: [summary]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif
          dockerImageId: ragstack-worker.sif
        NetworkAccess:
          networkAccess: true
      baseCommand: [python, /opt/ragstack/scripts/load_embeddings.py]
      inputs:
        embeddings:
          type: File[]
          inputBinding:
            position: 2
        collection:
          type: string
          inputBinding:
            prefix: --collection
            position: 3
        es_index:
          type: ["null", string]
          inputBinding:
            prefix: --es-index
            position: 4
        tenant:
          type: ["null", string]
          inputBinding:
            prefix: --tenant
            position: 5
        qdrant_url:
          type: string
          inputBinding:
            prefix: --qdrant-url
            position: 6
        es_url:
          type: string
          inputBinding:
            prefix: --es-url
            position: 7
        fail_on_error:
          type: boolean
          inputBinding:
            prefix: --fail-on-error
            position: 8
        backpressure:
          type: boolean
          inputBinding:
            prefix: --backpressure
            position: 9
      arguments:
        - position: 10
          prefix: --out
          valueFrom: load-summary.json
      outputs:
        summary:
          type: File
          outputBinding:
            glob: load-summary.json

outputs:
  summary:
    type: File
    outputSource: load/summary
