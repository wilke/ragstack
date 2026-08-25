#!/usr/bin/env cwl-runner
# PDF ingest, SCATTER-PER-BATCH (#203 "Option B", the production shape; `batch_size: 1`
# is "Option A", one PDF per task). The PDFs are grouped into batches of
# `batch_size` (default 20) and ONE task chain ingests a batch:
#
#   pdfs: File[]  --batch---->  File[][]  (an ExpressionTool; no container, no task)
#                 --scatter-->  extract   (one batch -> one JSONL shard + one report)
#                 --scatter-->  ingest    (one shard -> Qdrant/ES + ONE ShardReceipt with a
#                                          row PER DOCUMENT + the batch's embedding file)
#                 --gather--->  pack      (embedding files + receipts -> versions/N)
#
# WHY BATCHES. Per-task fixed overhead (dispatch + container start + interpreter +
# tokenizer load, ~2-4 s) is the same order as the work for ONE typical PDF (~0.1 s
# to extract, ~28 chunks to embed in one call), and the engine's effective
# parallelism is a handful of workers — so a per-PDF scatter is overhead-dominated
# past ~50 PDFs. A batch amortizes every fixed cost over `batch_size` PDFs: the
# tokenizer is loaded once per batch instead of once per PDF, and a 1000-PDF
# library is 50 tasks, not 1000. Batching happens INSIDE the workflow (the
# `batch` ExpressionTool) so the driver still submits a flat `pdfs: File[]` and
# the workflow is self-contained; the API never pre-groups.
#
# PER-DOCUMENT STATUS (the contract this file exists to satisfy). The API maps
# results back to its work items PER DOCUMENT, from the `docs` rows of each
# batch's receipt (matched by source basename — the engine pre-stages a `ws://`
# input under its basename, which is the path the extract tool records), not one
# receipt per item. So:
#
#   * an input named `pdfs` holds the File[] to ingest (GoWeBackend's
#     `shards_input_key`, settings `gowe_shards_input_key`, default "pdfs");
#   * ONE workflow-level output, `archive: Directory` (basename == version), whose
#     `receipt.json` is the JSON ARRAY of the per-BATCH receipts in batch order
#     (`ragstack.ingestion.receipts.ShardReceipt`), each with `docs[i].error`
#     ("" = upserted, else why not) and `docs[i].chunk_ids` per document.
#
# FAILURE SEMANTICS (#203 2b; the #377 review). A document that fails on its own —
# a scanned/image-only PDF (the extract step skips it and its report names it with
# the constant `NO_TEXT_ERROR`), a document with no embeddable chunk — is recorded
# on ITS row and the batch CONTINUES; the embedding file holds only the successful
# documents' chunks. `ingest_shard.py` exits non-zero — failing the task and hence
# the submission — ONLY when EVERY document of the batch failed, or the batch
# itself could not be loaded/embedded/indexed (an infra failure: then every row of
# that batch carries the batch error, and the engine retries the task). One bad
# file can no longer sink its 19 neighbours.
#
# WHY `archive` IS THE ONLY OUTPUT. GoWe post-stages EVERY top-level File output
# of a submission with an `output_destination` into that folder — flat, by
# basename, overwriting — and rewrites the File's location to `ws://`. Exposing
# `receipts`/`shards`/`reports`/`embeddings` here would therefore dump N shards +
# N reports + N embedding files + ONE surviving `receipt.json` into the user's
# `versions/` folder next to `versions/N/` (the embedding payload uploaded twice),
# and the engine's download endpoint could no longer serve the receipts to the
# API. So the receipts travel INSIDE the archive and the API (`GoWeBackend`)
# reads `versions/N/receipt.json` from the Workspace with the caller's token.
# Driven without an `output_destination` (a hand run, cwltool) the archive
# Directory is simply the run's output directory.
#
# The receipt is produced by `ingest_shard.py` itself (via `run_shard`), so the
# schema is not re-implemented here — it is the same producer `ingest-bulk.cwl`
# uses, and `chunk_ids` are the ids actually upserted.
#
# WHY EMBED+LOAD ARE ONE TASK. The decoupled halves (`embed_shard` ->
# `load_embeddings`, #141) exist so a capped/stalling Qdrant cannot back-pressure
# onto the GPU fleet — and `load_embeddings` is deliberately a **single**
# (un-scattered) task, because that is where Qdrant backpressure will live.
# Scattering it per batch would contradict that design, and the embed-stage
# receipt would report `completed` for chunks that were never upserted.
# `ingest_shard` (chunk -> embed -> quarantine -> delete-prior -> upsert ->
# neighbor-link) keeps the receipt honest and halves the container starts.
#
# Each task is stateless + idempotent (deterministic uuid5 ids + upsert), so an
# engine retry is safe; the engine owns scatter/retry/resume.
#
# ARCHIVE (#357, phase 2 of #353). `ingest_shard --embedding-file` also writes
# the embedded chunks (ragstack.embedding_file/v1) on their way to the stores —
# the literal decomposition embed -> file -> index_chunks of the coupled ingest,
# so the receipt still reports what was actually upserted. The gathered `pack`
# step packs those files + the receipts into ONE directory named `<version>`
# (manifest, chunks.jsonl.gz, vectors.f32, receipt.json = the receipts as a JSON
# array) and the workflow emits it as `archive: Directory`. GoWe uploads a
# Directory output under its basename, so it lands at
# `<output_destination>/<version>/`; no token inside any task. `version` and
# `collection_id` are REQUIRED workflow inputs — the API passes them per job
# (the registry's `next_version()` and the collection id); a hand-driven run
# sets them in the inputs file.
#
# TOKENIZER CACHE. The `ingest` step's `fixed_token` chunking loads the embedding
# model's HF tokenizer (~1.5 s of import + load from a warm cache per task —
# amortized over the batch here). The worker image reads it from `HF_HOME`
# (`/rag/cache` in `apptainer/ragstack-worker.def`'s %environment) which is NOT
# baked in: the GoWe worker must bind that cache root into the container
# (`gowe-worker --extra-bind <cache root>`), or every task re-downloads the
# tokenizer (or fails offline). `gowe:Execution` has no bind field, so this is a
# worker-side requirement — see cwl/README.md.
#
# CONTAINERIZED (#135). Every CommandLineTool runs inside the ragstack-worker
# image via DockerRequirement, declared under BOTH `dockerPull` (GoWe reads only
# this) and `dockerImageId` (cwltool --singularity needs this) — neither runner
# falls back to the other key; see cwl/README.md. Scripts live at
# /opt/ragstack/scripts. `ingest` needs the embedding fleet + Qdrant/ES over the
# network (NetworkAccess) and reads the HF tokenizer from the bound HF_HOME;
# `extract` is pure local I/O (PyMuPDF) and gets neither; `batch` is an
# ExpressionTool the engine evaluates in-process (no container, no task).
#
# SELF-CONTAINED: every step's tool is inlined (no `run: <file>.cwl`), because
# GoWeClient.register_workflow POSTs the CWL text and an external reference cannot
# be resolved engine-side. `cwl/pdf-extract.cwl` is the extract tool standalone —
# keep the two in sync (this copy adds the batch id naming).
#
#   CWL_SINGULARITY_CACHE=apptainer/images APPTAINER_BIND=/rag/cache HF_HOME=/rag/cache \
#     cwltool --singularity cwl/pdf-ingest-scatter.cwl cwl/pdf-ingest-scatter.inputs.yml
cwlVersion: v1.2
class: Workflow

requirements:
  ScatterFeatureRequirement: {}
  InlineJavascriptRequirement: {}

inputs:
  pdfs:
    type: File[]
    doc: >-
      PDF files to ingest. This is the input key a driver hands its work items
      to (GoWeBackend: shards_input_key="pdfs"); the workflow groups them into
      batches itself.
  batch_size:
    type: int
    default: 20
    doc: >-
      PDFs per task (the scatter unit). 1 reproduces the per-PDF scatter
      (Option A) for small interactive uploads; a value below 1 falls back
      to the default.
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
  batch:
    doc: "Group the PDFs into batches of batch_size (File[][], input order
      preserved) and name each batch — the scatter unit of the two task steps.
      An ExpressionTool: evaluated by the engine, no container, no task."
    in:
      pdfs: pdfs
      batch_size: batch_size
    out: [batches, batch_ids]
    run:
      class: ExpressionTool
      requirements:
        InlineJavascriptRequirement: {}
      inputs:
        pdfs: File[]
        batch_size: int
      outputs:
        batches:
          type: {type: array, items: {type: array, items: File}}
          doc: "batches[i] = pdfs[i*batch_size : (i+1)*batch_size]."
        batch_ids:
          type: string[]
          doc: "batch-00000, batch-00001, … — the shard/receipt id of batches[i]."
      expression: |
        ${
          var size = (inputs.batch_size && inputs.batch_size > 0) ? inputs.batch_size : 20;
          var batches = [], ids = [];
          for (var i = 0, b = 0; i < inputs.pdfs.length; i += size, b++) {
            batches.push(inputs.pdfs.slice(i, i + size));
            ids.push("batch-" + ("00000" + b).slice(-5));
          }
          return {"batches": batches, "batch_ids": ids};
        }

  extract:
    doc: "One batch of PDFs -> one JSONL shard (a record per PDF with text) +
      its report (every input; the skipped files with their constant error)."
    scatter: [pdfs, batch_id]
    scatterMethod: dotproduct
    in:
      pdfs: batch/batches
      batch_id: batch/batch_ids
    out: [shard, report]
    # INLINED copy of cwl/pdf-extract.cwl (out_name -> the batch id) — keep in sync.
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif
          dockerImageId: ragstack-worker.sif
      baseCommand: [python, /opt/ragstack/scripts/pdf_extract.py]
      inputs:
        pdfs:
          type: File[]
          doc: "The batch this task extracts (each PDF staged in by the runner)."
          inputBinding: {position: 2}
        batch_id:
          type: string
          doc: "Names the shard, the report and — downstream — the receipt."
      arguments:
        - {position: 3, prefix: --out, valueFrom: $(inputs.batch_id).jsonl}
        - {position: 4, prefix: --report, valueFrom: $(inputs.batch_id).report.json}
      outputs:
        shard:
          type: File
          doc: "JSONL shard: one {text,path,metadata} line per PDF with text."
          outputBinding: {glob: $(inputs.batch_id).jsonl}
        report:
          type: File
          doc: "Sidecar report: `inputs` (every path attempted) + `skipped`
            ({path, reason, error} — error is the constant NO_TEXT_ERROR for a
            scanned PDF), which ingest_shard folds into the receipt."
          outputBinding: {glob: $(inputs.batch_id).report.json}

  ingest:
    doc: "One shard (a batch) -> Qdrant/ES upsert + ONE ShardReceipt with a row
      per document + the batch's embedding file (stateless, idempotent). Fails
      only when every document of the batch failed."
    scatter: [shard, report]
    scatterMethod: dotproduct
    in:
      shard: extract/shard
      report: extract/report
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
        report:
          type: File
          doc: "The extract step's report for this batch — its skipped files become
            failed rows of the receipt with their constant error (#203 2b)."
          inputBinding: {prefix: --extract-report, position: 3}
        collection: {type: string, inputBinding: {prefix: --collection, position: 4}}
        es_index:
          type: ["null", string]
          inputBinding: {prefix: --es-index, position: 5}
        tenant: {type: string, inputBinding: {prefix: --tenant, position: 6}}
        chunk_method: {type: string, inputBinding: {prefix: --chunk-method, position: 7}}
        chunk_size: {type: int, inputBinding: {prefix: --chunk-size, position: 8}}
        chunk_overlap: {type: int, inputBinding: {prefix: --chunk-overlap, position: 9}}
        embedding_model: {type: string, inputBinding: {prefix: --embedding-model, position: 10}}
        embedding_api_key:
          type: ["null", string]
          inputBinding: {prefix: --embedding-api-key, position: 11}
        embedding_url:
          type: string[]
          inputBinding: {prefix: --embedding-url, position: 12}
        qdrant_url: {type: string, inputBinding: {prefix: --qdrant-url, position: 13}}
        es_url: {type: string, inputBinding: {prefix: --es-url, position: 14}}
      arguments:
        # shard_id = the batch id (the shard's stem), so a receipt names its batch;
        # the documents are named by their rows.
        - {position: 15, prefix: --shard-id, valueFrom: $(inputs.shard.nameroot)}
        - {position: 16, prefix: --out, valueFrom: receipt.json}
        # The archive step's input (#357): the embedded chunks of the batch's
        # SUCCESSFUL documents, written between the embed and the upsert halves.
        - {position: 17, prefix: --embedding-file, valueFrom: $(inputs.shard.nameroot).emb.jsonl}
      outputs:
        receipt:
          type: File
          doc: "ShardReceipt JSON (status/n_docs/n_docs_failed/n_chunks/chunk_ids/docs
            — a row per document with its own error + chunk_ids)."
          outputBinding: {glob: receipt.json}
        embeddings:
          type: File
          doc: "ragstack.embedding_file/v1 JSONL of this batch's embedded chunks."
          outputBinding: {glob: $(inputs.shard.nameroot).emb.jsonl}

  pack:
    doc: "Gather the per-batch embedding files + receipts -> one version directory
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
  # THE ONLY workflow-level output — see the header for why nothing else may be
  # exposed. receipt.json inside it is the per-batch receipts, in batch order
  # (CWL scatter preserves order), each with a row per document — which the
  # API maps per document by source basename.
  archive:
    type: Directory
    doc: "The version directory (basename == version): manifest.json,
      chunks.jsonl.gz, vectors.f32, receipt.json (the per-batch receipts as a
      JSON array, batch order; every document is a row of its batch's receipt).
      GoWe post-stages it to <output_destination>/<version>/."
    outputSource: pack/archive
