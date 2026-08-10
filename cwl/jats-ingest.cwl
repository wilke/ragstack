#!/usr/bin/env cwl-runner
# JATS/OA ingest workflow (#301). A hash-fanned PubMed-Central harvest → Qdrant/ES,
# as: scatter(jats_extract → embed_shard) → gather → single load step.
#
# Shape notes, in the order they matter:
#
# - EXTRACT and EMBED are TWO TOP-LEVEL SCATTERS with a phase barrier between
#   them — not a scatter over a chained sub-workflow. The chained shape is
#   prettier dataflow, but GoWe executes scatter-over-subworkflow children
#   strictly serially (GoWe#164: executeChildSubmission blocks inside the
#   dispatch loop), which single-filed a 64-shard batch at ~5.3 min/shard with
#   the whole fleet idle. Flat scatters dispatch as parallel tasks. The barrier
#   costs little: extract is ~1 min/shard and cheap next to embed. LOAD stays a
#   single task (see load-embeddings.cwl: backpressure is a control loop, not a
#   dataflow fan-out).
#
# - The shard files come from `plan_shards.py`, run OUTSIDE this workflow. Shards
#   are a pure function of article identity, so the plan is stable while the
#   harvest is still downloading; the workflow just consumes the shard files it is
#   given. Each shard line identifies one article inside `corpus`.
#
# - #263 IS VISIBLE IN THE DAG: the load step takes `collection_id` (a REGISTRY id,
#   not a physical store name) plus the registry itself (`registry_db`), and
#   `load_embeddings.py` refuses an id the registry does not hold. Register the
#   collection through POST /v1/collections BEFORE running this — registration is
#   the first step of an OA build, not the last. The physical Qdrant/ES names come
#   from the registry entry; nothing here names a store.
#
# - ONE chunk config covers both record kinds. jats_extract caps table/figure
#   units at ~1.8k chars (~450 tokens), inside one 512-token window — so
#   fixed_token 512/64 passes prose through the normal window and emits each unit
#   as exactly one chunk. No second "whole-doc" pass, hence no ADR-0002
#   build-spec conflict on the collection.
#
# CONTAINERIZED (#135): every step runs in the ragstack-worker image; scripts live
# at /opt/ragstack/scripts. Image resolution + build notes: cwl/ingest-bulk.cwl.
#
#   CWL_SINGULARITY_CACHE=apptainer/images \
#     cwltool --singularity cwl/jats-ingest.cwl cwl/jats-ingest.inputs.yml
cwlVersion: v1.2
class: Workflow

requirements:
  ScatterFeatureRequirement: {}
  StepInputExpressionRequirement: {}

inputs:
  corpus:
    type: Directory
    doc: "Harvest root (clean/xx/yy/PMC*.xml + manifest.jsonl). Mounted read-only
      into the extract tasks; shard lines resolve against it."
  shards:
    type: File[]
    doc: "Shard files from plan_shards.py — each a bounded, deterministic list of
      articles. One extract+embed task pair per shard."
  collection_id:
    type: string
    doc: "REGISTRY collection id (#263) — e.g. 'oa-dev'. Must already exist in
      registry_db; the load step refuses an unregistered id. NOT a Qdrant name."
  registry_db:
    type: File
    doc: "The tenant's sqlite collection store (COLLECTION_STORE_PATH). Read-only
      here: the load step resolves collection_id through it."
  tenant:
    type: string
    default: "public"
    doc: "Stamped on every chunk; point ids are uuid5('{tenant}:{chunk_id}'), so
      changing this later means a full re-ingest."
  chunk_method:
    type: string
    default: "fixed_token"
  chunk_size:
    type: int
    default: 512
  chunk_overlap:
    type: int
    default: 64
  embedding_url:
    type: string[]
    doc: "SFR embedding endpoint base URLs."
  embedding_model:
    type: string
    default: "Salesforce/SFR-Embedding-Mistral"
  embedding_api_key:
    type: ["null", string]
    doc: "Bearer token for keyed endpoints."
  metadata_passthrough:
    type: string
    default: "content_type,pmcid,pmid,journal,publisher,licence,section_title,sha256,source_url,graphic"
    doc: "Record-metadata keys carried onto chunks verbatim (the loader's fixed
      schema has no slot for them). content_type is load-bearing: without it,
      'filter to tables' is unanswerable at query time and re-stamping means a
      full re-ingest."
  qdrant_url:
    type: string
    doc: "The TENANT's Qdrant (routed entries override this per the registry)."
  es_url:
    type: string
    doc: "The tenant's Elasticsearch."
  backpressure:
    type: boolean
    default: false

steps:
  extract:
    doc: "JATS -> {text,path,metadata} JSONL, one task per shard, all shards in
      parallel. Two record kinds: prose (content_type=article, tables/figures
      lifted OUT) and self-contained table/figure units under one chunk window.
      Skipped/corrupt articles go to the report, never silently dropped."
    scatter: shard
    in:
      shard: shards
      corpus: corpus
    out: [jsonl, skips]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif
          dockerImageId: ragstack-worker.sif
      baseCommand: [python, /opt/ragstack/scripts/jats_extract.py]
      inputs:
        shard:
          type: File
          inputBinding:
            prefix: --shard
            position: 2
        corpus:
          type: Directory
          inputBinding:
            prefix: --corpus
            position: 3
      arguments:
        - position: 4
          prefix: --out
          valueFrom: $(inputs.shard.nameroot).jsonl
        - position: 5
          prefix: --skip-report
          valueFrom: $(inputs.shard.nameroot).skips.json
      outputs:
        jsonl:
          type: File
          outputBinding:
            glob: $(inputs.shard.nameroot).jsonl
        skips:
          type: File
          outputBinding:
            glob: $(inputs.shard.nameroot).skips.json

  embed:
    doc: "Ingest JSONL -> embedding file, one task per shard, all shards in
      parallel across the worker pool (each task additionally fans requests
      across every endpoint — #309)."
    scatter: shard
    in:
      shard: extract/jsonl
      tenant: tenant
      chunk_method: chunk_method
      chunk_size: chunk_size
      chunk_overlap: chunk_overlap
      embedding_url: embedding_url
      embedding_model: embedding_model
      embedding_api_key: embedding_api_key
      metadata_passthrough: metadata_passthrough
    out: [embeddings, receipt]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif
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
        metadata_passthrough:
          type: string
          inputBinding:
            prefix: --metadata-passthrough
            position: 10
      arguments:
        - position: 11
          prefix: --out
          valueFrom: $(inputs.shard.nameroot).emb.jsonl
        - position: 12
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
    doc: "Gather per-shard embed receipts -> run summary."
    in:
      receipts: embed/receipt
    out: [summary]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif
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

  load:
    doc: "Upsert every embedding file into the registry entry's stores. Resolves
      collection_id through the sqlite registry (#263) — both physical names
      (Qdrant collection AND ES index) come from the entry, and the tool writes
      the provenance manifest that arms ADR-0002's guard."
    in:
      embeddings: embed/embeddings
      collection_id: collection_id
      registry_db: registry_db
      tenant: tenant
      qdrant_url: qdrant_url
      es_url: es_url
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
        EnvVarRequirement:
          envDef:
            COLLECTION_STORE_BACKEND: sqlite
            COLLECTION_STORE_PATH: $(inputs.registry_db.path)
      baseCommand: [python, /opt/ragstack/scripts/load_embeddings.py]
      inputs:
        embeddings:
          type: File[]
          inputBinding:
            position: 2
        collection_id:
          type: string
          inputBinding:
            prefix: --collection-id
            position: 3
        registry_db:
          type: File
          doc: "Consumed via COLLECTION_STORE_PATH above, not a CLI flag."
        tenant:
          type: ["null", string]
          inputBinding:
            prefix: --tenant
            position: 4
        qdrant_url:
          type: string
          inputBinding:
            prefix: --qdrant-url
            position: 5
        es_url:
          type: string
          inputBinding:
            prefix: --es-url
            position: 6
        backpressure:
          type: boolean
          inputBinding:
            prefix: --backpressure
            position: 7
      arguments:
        - position: 8
          prefix: --out
          valueFrom: load-summary.json
        - position: 9
          valueFrom: "--fail-on-error"
      outputs:
        summary:
          type: File
          outputBinding:
            glob: load-summary.json

outputs:
  load_summary:
    type: File
    outputSource: load/summary
  embed_summary:
    type: File
    outputSource: merge/summary
  receipts:
    type: File[]
    outputSource: embed/receipt
  skip_reports:
    type: File[]
    doc: "Per-shard extract skips (missing/corrupt/under-min XML). Read these —
      an article absent here and absent from the stores was never attempted."
    outputSource: extract/skips
