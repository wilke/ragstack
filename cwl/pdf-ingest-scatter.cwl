#!/usr/bin/env cwl-runner
# PDF ingest, SCATTER-PER-PDF (#203 "Option A"). One PDF = one work item = one
# task chain = one receipt — the shape a user-triggered browser upload maps onto
# directly.
#
# Difference from cwl/pdf-ingest.cwl: that workflow is *one shard per run* — all
# PDFs are extracted into a single JSONL shard, embedded once and loaded once, and
# it emits no receipts. This one scatters, so N PDFs produce N independent tasks
# and — critically — **N receipts**:
#
#   pdfs: File[]  --scatter-->  extract (one PDF -> one JSONL shard)
#                 --scatter-->  ingest  (one shard -> Qdrant/ES + one receipt
#                                        + its embedding file)
#                 --gather--->  merge   (receipts -> run summary)
#                 --gather--->  pack    (embedding files + receipts -> versions/N)
#
# WHY THIS SHAPE (receipts are the point). `GoWeBackend._map_outputs` reads the
# workflow's `receipts` output and maps it **positionally**, one receipt per
# submitted work item, failing any item without one. A workflow that emits no
# `receipts` (pdf-ingest.cwl) makes a fully successful run report every item
# failed. So the contract this file exists to satisfy is:
#
#   * an input named `pdfs` holding the scattered File[] (GoWeBackend's
#     `shards_input_key` must be set to "pdfs" — its default is "shards"), and
#   * an output named `receipts` of type File[], one per input PDF, in the
#     `ragstack.ingestion.receipts.ShardReceipt` shape.
#
# The receipt is produced by `ingest_shard.py` itself (via `run_shard`), so the
# schema is not re-implemented here — it is the same producer `ingest-bulk.cwl`
# uses, and `chunk_ids` are the ids actually upserted.
#
# WHY EMBED+LOAD ARE ONE TASK. The decoupled halves (`embed_shard` ->
# `load_embeddings`, #141) exist so a capped/stalling Qdrant cannot back-pressure
# onto the GPU fleet — and `load_embeddings` is deliberately a **single**
# (un-scattered) task, because that is where Qdrant backpressure will live.
# Scattering it per PDF would contradict that design, and the embed-stage receipt
# would report `completed` for chunks that were never upserted. `ingest_shard`
# (chunk -> embed -> quarantine -> delete-prior -> upsert -> neighbor-link) keeps
# the receipt honest and halves the per-PDF container starts, which matter here:
# per-task fixed overhead (dispatch + container start + tokenizer load) is the
# same order as the per-PDF work itself. For large libraries prefer batching
# (#203 "Option B": 10-20 PDFs per task) over this per-PDF scatter.
#
# Each task is stateless + idempotent (deterministic uuid5 ids + upsert), so an
# engine retry is safe; the engine owns scatter/retry/resume.
#
# ARCHIVE (#357, phase 2 of #353). `ingest_shard --embedding-file` also writes
# the embedded chunks (ragstack.embedding_file/v1) on their way to the stores —
# the literal decomposition embed_source -> file -> index_chunks of the coupled
# ingest, so the receipt still reports what was actually upserted. The gathered
# `pack` step packs those N files + the N receipts into ONE directory named
# `<version>` (manifest, chunks.jsonl.gz, vectors.f32, receipt.json = the N
# receipts as a JSON array) and the workflow emits it as `archive: Directory`.
# GoWe uploads a Directory output under its basename, so it lands at
# `<output_destination>/<version>/`; no token inside any task. `version` and
# `collection_id` are REQUIRED workflow inputs — a GoWeBackend driver must carry
# them in `gowe_workflow_inputs_json` until the API side of #353 passes them per
# job. Archiving runs after every ingest task succeeded (it consumes their
# receipts), so a failed PDF fails the run before any archive exists.
#
# FAILURE SEMANTICS. `ingest_shard.py` exits non-zero on a failed shard, so a PDF
# with no extractable text (scanned/image-only -> empty shard -> EmptyIngestError)
# fails its task and therefore the whole submission. Per-PDF receipts still give
# exact attribution once a run completes; pre-filtering unextractable PDFs (the
# extract step's `report` output lists them) is the way to avoid one bad file
# sinking a batch.
#
# CONTAINERIZED (#135). Every step runs inside the ragstack-worker image via
# DockerRequirement, declared under BOTH `dockerPull` (GoWe reads only this) and
# `dockerImageId` (cwltool --singularity needs this) — neither runner falls back
# to the other key; see cwl/README.md and the cwl/ingest-bulk.cwl header for the
# full rationale and the SIF build commands. Scripts live at /opt/ragstack/scripts.
# `ingest` needs the embedding fleet + Qdrant/ES over the network (NetworkAccess)
# and reads the HF tokenizer from a bind-mounted HF_HOME; `extract` is pure local
# I/O (PyMuPDF) and gets neither.
#
# SELF-CONTAINED: every step's tool is inlined (no `run: <file>.cwl`), because
# GoWeClient.register_workflow POSTs the CWL text and an external reference cannot
# be resolved engine-side. `cwl/pdf-extract.cwl` is the extract tool standalone —
# keep the two in sync.
#
#   CWL_SINGULARITY_CACHE=apptainer/images APPTAINER_BIND=/rag/cache HF_HOME=/rag/cache \
#     cwltool --singularity cwl/pdf-ingest-scatter.cwl cwl/pdf-ingest-scatter.inputs.yml
cwlVersion: v1.2
class: Workflow

requirements:
  ScatterFeatureRequirement: {}

inputs:
  pdfs:
    type: File[]
    doc: >-
      PDF files to ingest, one task chain each. This is the input key a driver
      scatters over (GoWeBackend: shards_input_key="pdfs").
  collection:
    type: string
    doc: "Qdrant collection name."
  es_index:
    type: ["null", string]
    doc: "Elasticsearch index (ingest_shard defaults it to the collection name)."
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
  qdrant_url:
    type: string
    default: "http://localhost:6333"
  es_url:
    type: string
    default: "http://localhost:9200"
  version:
    type: string
    doc: "Archive version number N (digits) — the `archive` output's basename,
      hence the Workspace subfolder `versions/N/`. Assigned by the API from the
      registry's ordered version list. REQUIRED."
  collection_id:
    type: string
    doc: "Registry collection id (#263), recorded in the archive manifest. NOT
      the physical `collection` store name above. REQUIRED."
  spec_hash:
    type: ["null", string]
    doc: "The collection's build-spec hash (ADR-0002), recorded in the manifest."
  job_id:
    type: ["null", string]
    doc: "The RAGStack ingest job id, recorded in the manifest."

steps:
  extract:
    doc: "One PDF -> one JSONL shard (+ its skipped-files report)."
    scatter: pdf
    in:
      pdf: pdfs
    out: [shard, report]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif
          dockerImageId: ragstack-worker.sif
      baseCommand: [python, /opt/ragstack/scripts/pdf_extract.py]
      inputs:
        pdf:
          type: File
          doc: "The single PDF this task extracts (staged in by the runner)."
          inputBinding: {position: 2}
      arguments:
        # Name the shard after the PDF so the receipt's shard_id (and every
        # intermediate) traces back to the source document.
        - {position: 3, prefix: --out, valueFrom: $(inputs.pdf.nameroot).jsonl}
        - {position: 4, prefix: --report, valueFrom: $(inputs.pdf.nameroot).report.json}
      outputs:
        shard:
          type: File
          doc: "One-line JSONL shard: {text,path,metadata} for this PDF."
          outputBinding: {glob: $(inputs.pdf.nameroot).jsonl}
        report:
          type: File
          doc: "Sidecar report — empty `skipped` unless the PDF yielded no text."
          outputBinding: {glob: $(inputs.pdf.nameroot).report.json}

  ingest:
    doc: "One shard -> Qdrant/ES upsert + one ShardReceipt + its embedding file
      (stateless, idempotent)."
    scatter: shard
    in:
      shard: extract/shard
      collection: collection
      es_index: es_index
      tenant: tenant
      chunk_method: chunk_method
      chunk_size: chunk_size
      chunk_overlap: chunk_overlap
      embedding_url: embedding_url
      embedding_model: embedding_model
      embedding_api_key: embedding_api_key
      qdrant_url: qdrant_url
      es_url: es_url
    out: [receipt, embeddings]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif
          dockerImageId: ragstack-worker.sif
        NetworkAccess:
          networkAccess: true
      baseCommand: [python, /opt/ragstack/scripts/ingest_shard.py]
      inputs:
        shard: {type: File, inputBinding: {position: 2}}
        collection: {type: string, inputBinding: {prefix: --collection, position: 3}}
        es_index:
          type: ["null", string]
          inputBinding: {prefix: --es-index, position: 4}
        tenant: {type: string, inputBinding: {prefix: --tenant, position: 5}}
        chunk_method: {type: string, inputBinding: {prefix: --chunk-method, position: 6}}
        chunk_size: {type: int, inputBinding: {prefix: --chunk-size, position: 7}}
        chunk_overlap: {type: int, inputBinding: {prefix: --chunk-overlap, position: 8}}
        embedding_model: {type: string, inputBinding: {prefix: --embedding-model, position: 9}}
        embedding_api_key:
          type: ["null", string]
          inputBinding: {prefix: --embedding-api-key, position: 10}
        embedding_url:
          type: string[]
          inputBinding: {prefix: --embedding-url, position: 11}
        qdrant_url: {type: string, inputBinding: {prefix: --qdrant-url, position: 12}}
        es_url: {type: string, inputBinding: {prefix: --es-url, position: 13}}
      arguments:
        # shard_id = the PDF's stem, so a receipt names the document it came from
        # (the default would be the staged shard's basename).
        - {position: 14, prefix: --shard-id, valueFrom: $(inputs.shard.nameroot)}
        - {position: 15, prefix: --out, valueFrom: receipt.json}
        # The archive step's input (#357): the embedded chunks, written between
        # the embed and the upsert halves. Named after the PDF like the shard.
        - {position: 16, prefix: --embedding-file, valueFrom: $(inputs.shard.nameroot).emb.jsonl}
      outputs:
        receipt:
          type: File
          doc: "ShardReceipt JSON (status/n_docs/n_chunks/chunk_ids/docs)."
          outputBinding: {glob: receipt.json}
        embeddings:
          type: File
          doc: "ragstack.embedding_file/v1 JSONL of this PDF's embedded chunks."
          outputBinding: {glob: $(inputs.shard.nameroot).emb.jsonl}

  merge:
    doc: "Gather the per-PDF receipts -> run summary (totals + failed shards)."
    in:
      receipts: ingest/receipt
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
          inputBinding: {position: 2}
      arguments:
        - {position: 3, prefix: --out, valueFrom: summary.json}
      outputs:
        summary: {type: File, outputBinding: {glob: summary.json}}

  pack:
    doc: "Gather the per-PDF embedding files + receipts -> one version directory
      (ragstack-archive/1), basename == version."
    in:
      version: version
      chunks: ingest/embeddings
      receipt: ingest/receipt
      collection_id: collection_id
      tenant: tenant
      spec_hash: spec_hash
      job_id: job_id
    out: [archive]
    # INLINED copy of cwl/archive-collection.cwl — keep in sync.
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif
          dockerImageId: ragstack-worker.sif
      baseCommand: [python, /opt/ragstack/scripts/archive_version.py]
      inputs:
        version: {type: string, inputBinding: {prefix: --version, position: 1}}
        chunks: {type: "File[]", inputBinding: {prefix: --chunks, position: 2}}
        receipt: {type: "File[]", inputBinding: {prefix: --receipt, position: 3}}
        collection_id: {type: string, inputBinding: {prefix: --collection-id, position: 4}}
        tenant: {type: string, default: "public", inputBinding: {prefix: --tenant, position: 5}}
        spec_hash:
          type: ["null", string]
          inputBinding: {prefix: --spec-hash, position: 6}
        job_id:
          type: ["null", string]
          inputBinding: {prefix: --job-id, position: 7}
      arguments:
        - {position: 8, prefix: --out, valueFrom: "."}
      outputs:
        archive: {type: Directory, outputBinding: {glob: $(inputs.version)}}

outputs:
  # THE contract output: one receipt per input PDF, in input order (CWL scatter
  # preserves order), which is what GoWeBackend._map_outputs maps positionally.
  receipts:
    type: File[]
    outputSource: ingest/receipt
  summary:
    type: File
    outputSource: merge/summary
  shards:
    type: File[]
    outputSource: extract/shard
  reports:
    type: File[]
    outputSource: extract/report
  embeddings:
    type: File[]
    doc: "One embedding file per PDF (the archive step's input), in input order."
    outputSource: ingest/embeddings
  archive:
    type: Directory
    doc: "The version directory (basename == version): manifest.json,
      chunks.jsonl.gz, vectors.f32, receipt.json (the N per-PDF receipts as a
      JSON array). GoWe post-stages it to <output_destination>/<version>/."
    outputSource: pack/archive
