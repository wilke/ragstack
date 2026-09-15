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
  collection_id:
    type: ["null", string]
    doc: "Registry collection id (#263) — the id the load RESOLVES through the
      registry, from which the physical store names come. Optional here only for
      compatibility with existing inputs files: without it the tool falls back
      to matching the PHYSICAL `collection` name against the registry
      (ingest_target.resolve_by_store_name), which still refuses an unregistered
      store but cannot tell two entries over one store apart. Give it. When both
      are given the id wins and `collection` is CHECKED against the entry rather
      than used to name anything."
  registry:
    type: ["null", string]
    doc: "WHICH collection registry to resolve `collection_id` against, by NAME
      (#563) — e.g. `hackathon`. A name, never coordinates and never a
      credential: the worker reads COLLECTION_STORE_BACKEND_<NAME> and
      COLLECTION_STORE_{PATH,DSN}_<NAME> from its own environment, where the DSN
      arrives through `gowe-worker --secret-file` (a workflow input would land
      in the submission's immutable `submitted_inputs` snapshot forever). Seeded
      per job by the tenant API from COLLECTION_REGISTRY_NAME. Omitted = the
      worker's unsuffixed COLLECTION_STORE_* variables, i.e. the pre-#563
      behaviour; a name the worker has nothing configured for is REFUSED rather
      than silently fallen back from — one worker group could otherwise serve
      only one tenant's registry."
  es_index:
    type: ["null", string]
    doc: "Elasticsearch index (defaults to the collection name)."
  tenant:
    type: ["null", string]
    doc: "Override tenant (default: each file's header tenant)."
  qdrant_url:
    type: string
    doc: "The Qdrant instance to WRITE to, as seen from the worker.
      REQUIRED, deliberately without a default (#407): a default here decided
      where a run wrote whenever a caller omitted it, and the old default
      (localhost:6333) is production on the deployment host — a dev-tenant
      ingest built a collection on the production instance. The API seeds this
      per run from its own settings; a hand-run must name it."
  es_url:
    type: string
    doc: "The Elasticsearch instance to WRITE to, as seen from the worker.
      REQUIRED, no default — see qdrant_url."
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
      collection_id: collection_id
      registry: registry
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
        collection_id:
          type: ["null", string]
          inputBinding:
            prefix: --collection-id
            position: 11
        registry:
          type: ["null", string]
          inputBinding:
            prefix: --registry
            position: 12
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
