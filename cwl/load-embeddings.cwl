#!/usr/bin/env cwl-runner
# Bulk LOAD workflow (ADR-0001 offline plane, #141). The store-bound second half
# of the decoupled pipeline: takes the embedding files produced by
# `embed-bulk.cwl` and upserts them into Qdrant/ES via `load_embeddings.py`
# (reusing `IngestionPipeline.index_chunks`). No embedding fleet needed.
#
# Deliberately a SINGLE task, not a scatter: the load is where Qdrant backpressure
# belongs (throttle upserts on live collection health — #141's must-have), which
# is a stateful control loop, not a dataflow fan-out. Backpressure is implemented
# as a `BackpressuredVectorStore` decorator inside `load_embeddings.py`; enable it
# with `backpressure: true` (default), which holds each upsert until the
# collection is green so a bulk load never piles unindexed vectors onto an
# optimizing Qdrant.
#
#   cwltool cwl/load-embeddings.cwl cwl/load-embeddings.inputs.yml
#
# Input/output files must live under GoWe's --upload-download-dirs. The task
# stages python/ and sets PYTHONPATH (CWD-independent, runs on a GoWe worker); the
# worker must have ragstack's store deps (qdrant-client/elasticsearch).
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
    default: true
    doc: "Hold each upsert until the collection is green (#141)."

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
        InitialWorkDirRequirement:
          listing:
            - $(inputs.pkgdir)
        EnvVarRequirement:
          envDef:
            PYTHONPATH: $(inputs.pkgdir.path)
      baseCommand: [python]
      inputs:
        pkgdir:
          type: Directory
          default:
            class: Directory
            location: ../python
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
        - position: 1
          valueFrom: $(inputs.pkgdir.basename)/scripts/load_embeddings.py
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
