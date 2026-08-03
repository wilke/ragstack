#!/usr/bin/env cwl-runner
# PDF ingest workflow (#202/#203) — closes the PDF -> CWL/GoWe gap end to end.
# Chains: extract (PdfLoader -> JSONL shard) -> embed (embed_shard -> embedding
# file, no store contact) -> load (load_embeddings -> Qdrant/ES). It mirrors the
# decoupled bulk pipeline (embed-bulk.cwl + load-embeddings.cwl) but takes PDFs as
# its input instead of a pre-extracted JSONL shard.
#
# One shard per run (a single PDF batch): extract yields one JSONL shard, embed
# turns it into one embedding file, load upserts it. For many PDF batches, scatter
# at the GoWe/driver layer (submit N pdf-ingest runs) — the same way ingest-bulk
# scatters JSONL shards.
#
# CONTAINERIZED (#135). Every step runs inside the ragstack-worker image via
# DockerRequirement; scripts live at /opt/ragstack/scripts. embed + load need the
# embedding fleet / Qdrant / ES over the network (NetworkAccess) and embed reads
# the HF tokenizer from a bind-mounted HF_HOME — export APPTAINER_BIND (e.g.
# /rag/cache) and HF_HOME on the host so cwltool/apptainer pass them through.
# Image resolution + build: see cwl/ingest-bulk.cwl header.
#
# SELF-CONTAINED: every step's tool is inlined (no `run: <file>.cwl`), because
# GoWeClient.register_workflow POSTs the CWL text and an external reference cannot
# be resolved engine-side. `cwl/pdf-extract.cwl` is the same tool standalone.
#
#   CWL_SINGULARITY_CACHE=apptainer/images \
#     cwltool --singularity cwl/pdf-ingest.cwl cwl/pdf-ingest.inputs.yml
cwlVersion: v1.2
class: Workflow

inputs:
  pdfs:
    type: File[]
    doc: "PDF files to ingest."
  out_name:
    type: string
    default: "pdf-shard.jsonl"
    doc: "Name of the intermediate JSONL shard."
  collection:
    type: string
    doc: "Qdrant collection name (vector dim read from the embedding header)."
  es_index:
    type: ["null", string]
    doc: "Elasticsearch index (defaults to the collection name)."
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
  fail_on_error:
    type: boolean
    default: true
  backpressure:
    type: boolean
    default: false

steps:
  extract:
    doc: "PDF -> one JSONL shard (+ skipped-files report)."
    in:
      pdfs: pdfs
      out_name: out_name
    out: [shard, report]
    # INLINED (not `run: pdf-extract.cwl`) on purpose: GoWeClient.register_workflow
    # POSTs the CWL *text*, so an external `run:` reference has nothing to resolve
    # against engine-side. The other two steps were already inlined; the standalone
    # cwl/pdf-extract.cwl stays for direct `cwltool` use — keep the two in sync.
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
          doc: "PDF files to extract (each staged into the container by the runner)."
          inputBinding: {position: 2}
        out_name:
          type: string
          default: "pdf-shard.jsonl"
          doc: "Name of the emitted JSONL shard (fed to embed/ingest steps)."
          inputBinding: {prefix: --out, position: 3}
      arguments:
        - {position: 4, prefix: --report, valueFrom: $(inputs.out_name).report.json}
      outputs:
        shard:
          type: File
          doc: "The JSONL shard: one {text,path,metadata} line per extracted PDF."
          outputBinding: {glob: $(inputs.out_name)}
        report:
          type: File
          doc: "Sidecar JSON report: counts + the list of skipped files with reasons."
          outputBinding: {glob: $(inputs.out_name).report.json}

  embed:
    doc: "Embed the shard -> one embedding file (no store contact)."
    in:
      shard: extract/shard
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
          dockerPull: ragstack-worker.sif
          dockerImageId: ragstack-worker.sif
        NetworkAccess:
          networkAccess: true
      baseCommand: [python, /opt/ragstack/scripts/embed_shard.py]
      inputs:
        shard: {type: File, inputBinding: {position: 2}}
        tenant: {type: string, inputBinding: {prefix: --tenant, position: 3}}
        chunk_method: {type: string, inputBinding: {prefix: --chunk-method, position: 4}}
        chunk_size: {type: int, inputBinding: {prefix: --chunk-size, position: 5}}
        chunk_overlap: {type: int, inputBinding: {prefix: --chunk-overlap, position: 6}}
        embedding_model: {type: string, inputBinding: {prefix: --embedding-model, position: 7}}
        embedding_api_key:
          type: ["null", string]
          inputBinding: {prefix: --embedding-api-key, position: 8}
        embedding_url:
          type: string[]
          inputBinding: {prefix: --embedding-url, position: 9}
      arguments:
        - {position: 10, prefix: --out, valueFrom: $(inputs.shard.nameroot).emb.jsonl}
        - {position: 11, prefix: --receipt, valueFrom: receipt.json}
      outputs:
        # File[] (a 1-element array) so it feeds load's File[] input directly,
        # matching embed-bulk.cwl's File[] embeddings output.
        embeddings: {type: "File[]", outputBinding: {glob: $(inputs.shard.nameroot).emb.jsonl}}
        receipt: {type: File, outputBinding: {glob: receipt.json}}

  load:
    doc: "Upsert the embedding file into Qdrant/ES -> load summary."
    in:
      embeddings: embed/embeddings
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
          inputBinding: {position: 2}
        collection: {type: string, inputBinding: {prefix: --collection, position: 3}}
        es_index:
          type: ["null", string]
          inputBinding: {prefix: --es-index, position: 4}
        tenant:
          type: ["null", string]
          inputBinding: {prefix: --tenant, position: 5}
        qdrant_url: {type: string, inputBinding: {prefix: --qdrant-url, position: 6}}
        es_url: {type: string, inputBinding: {prefix: --es-url, position: 7}}
        fail_on_error: {type: boolean, inputBinding: {prefix: --fail-on-error, position: 8}}
        backpressure: {type: boolean, inputBinding: {prefix: --backpressure, position: 9}}
      arguments:
        - {position: 10, prefix: --out, valueFrom: load-summary.json}
      outputs:
        summary: {type: File, outputBinding: {glob: load-summary.json}}

outputs:
  shard:
    type: File
    outputSource: extract/shard
  report:
    type: File
    outputSource: extract/report
  embeddings:
    type: File[]
    outputSource: embed/embeddings
  summary:
    type: File
    outputSource: load/summary
