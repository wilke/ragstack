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
# GLOSSARY (#847 G2). A `glossary` step (cwl/glossary-step.cwl, inlined) runs per
# shard in parallel with `embed` (both read the same shard), and
# `merge_glossary` gathers the per-shard files into ONE glossary.jsonl, which
# `load` renders every chunk's field from (`load_embeddings --glossary-file`).
# JATS table/figure units are documents: set glossary_llm_min_chars (e.g. 1500)
# to keep them off the model.
# KNOWN LIMITATION: on this plane `symbol_glossary` governs only embed_shard's
# in-process pass, which the load then overrides from the file;
# `symbol_glossary="off"` does NOT suppress the glossary field at load (the
# loader has no such switch, and the CWL cannot make the file binding
# conditional without JS). "No glossary at all" on the bulk plane is a
# follow-up. Shard basenames must be distinct (#852).
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
    type:
      type: enum
      symbols: [fixed, fixed_token, sentence, words]
    default: fixed_token
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
  no_delete_prior:
    type: boolean
    default: false
    doc: "Skip the per-doc_id delete before upserting (#323). ONLY for a replay
      from UNCHANGED embedding files, where chunk ids are read from the file and
      cannot have moved — then the delete removes exactly what the upsert is about
      to rewrite (~550k round-trips per 64-shard batch). NEVER set it for a batch
      whose inputs were re-extracted or re-chunked: the old chunks survive as
      orphans. Default false = the safe, id-agnostic behaviour."
  file_concurrency:
    type: int
    default: 1
    doc: "Embedding files loaded concurrently (#323). Shards hold disjoint
      documents and ids are deterministic, so this cannot race or duplicate.
      1 = the previous serial behaviour. COSTS MEMORY — each in-flight file is
      read fully into RAM (~6 GB resident per 1.3 GB file), so size it against
      free memory, not store headroom. It also multiplies the other knobs: the
      delete semaphore is per-call, so N files means N x delete-concurrency
      concurrent deletes. Start at 2."
  bulk_refresh:
    type: boolean
    default: false
    doc: "Park the ES refresh interval for the load and restore it after (#323).
      The index spent ~28x longer refreshing than indexing on the last build. The
      tool forces an explicit refresh before returning, so the driver's count
      verification stays accurate."
  symbol_glossary:
    type: string
    default: "rules"
    doc: "Per-document symbol glossary passed to embed_shard --symbol-glossary
      (off|rules; #754, #783). A separate BM25 field: content, the embedded text,
      the chunk ids and the collection identity are unchanged either way."
  max_chunks:
    type: int
    default: 0
    doc: "Per-collection chunk cap (#291, #849), passed to load_embeddings
      --max-chunks: the load refuses, whole and before any write, when the
      collection's live chunk count plus the files' chunks would exceed it.
      0 (the default) = no cap from this input; the registry entry's explicit
      max_chunks override still applies. The submitter decides the cap (the
      API for user jobs, the operator for bulk jobs); nothing is derived from
      the worker."
  # Provenance (ADR-0010 decision 8, #821). Recorded on the receipts and the
  # summary, never verified. A hand run or `gowe_batch_ingest.py` seeds
  # `tool_image`/`tool_image_digest` from the stamped tree; `workflow_id` stays
  # null unless the driver is told it. All optional: null omits the flag.
  workflow_id:
    type: ["null", string]
    doc: "The GoWe workflow id this submission was pinned to (null on a bulk run
      unless the driver is given it)."
  tool_image:
    type: ["null", string]
    doc: "The stamped tools image name the text's DockerRequirement names."
  tool_image_digest:
    type: ["null", string]
    doc: "Its sha256 from the committed cwl/tool-image.receipt.json."
  # #847 G2: the glossary step's knobs (see cwl/glossary-step.cwl). With
  # glossary_llm="off" (the default) the step is rules-only — no network, every
  # limit below ignored — so zero defaults are legal; with "on" the submitter
  # must give the endpoint, the model and every limit marked required, or the
  # task exits 2 before any call (#849: the workflow carries no policy).
  # The key travels by NAME only; the API seeds these from
  # SYMBOL_GLOSSARY_LLM_* (documents.py::_gowe_inputs).
  glossary_llm:
    type: string
    default: "off"
    doc: "off (default): rules only, no network. on: also the LLM glossary from
      glossary_llm_endpoint. A quoted string, not a boolean: the tool takes
      --llm off|on, and an unquoted off would be a YAML 1.1 boolean."
  glossary_llm_endpoint:
    type: ["null", string]
    doc: "OpenAI-compatible base URL, as seen from the worker (required with
      glossary_llm=on). Recorded as host:port only. Never a credential."
  glossary_llm_model:
    type: ["null", string]
    doc: "The served model id (required with glossary_llm=on)."
  glossary_llm_api_key_env:
    type: string
    default: OPENAI_API_KEY
    doc: "The NAME of the environment variable holding the key — never the key:
      the worker injects it from its --secret-file. Unset or empty = no
      Authorization header."
  glossary_llm_concurrency:
    type: int
    default: 0
    doc: "Documents in flight per task (0 = 1)."
  glossary_llm_timeout_s:
    type: float
    default: 0.0
    doc: "Per-call timeout in seconds; required (> 0) with glossary_llm=on."
  glossary_llm_max_tokens:
    type: int
    default: 0
    doc: "max_tokens per call; required (> 0) with glossary_llm=on."
  glossary_llm_window_chars:
    type: int
    default: 0
    doc: "Characters per window; required (> 2000, the overlap) with glossary_llm=on."
  glossary_llm_max_windows:
    type: int
    default: 0
    doc: "Windows per document (the tail is recorded as truncated); required
      (> 0) with glossary_llm=on."
  glossary_llm_thinking:
    type: string
    default: "off"
    doc: "off|on|unset: chat_template_kwargs.enable_thinking (vLLM); unset sends
      none (a strict OpenAI-compatible server)."
  glossary_llm_repair_rounds:
    type: int
    default: 0
    doc: "Rounds sending the gate's repairable rejections back (0 = none; no
      measured gain yet)."
  glossary_llm_cutoff_retry:
    type: string
    default: "off"
    doc: "off|split: re-ask a window cut off at max_tokens as two halves, once."
  glossary_llm_deadline_s:
    type: float
    default: 0.0
    doc: "Wall-clock bound on the task's model calls (0 = none beyond the per-call
      bounds); documents not done by then degrade 'deadline'."
  glossary_llm_min_chars:
    type: int
    default: 0
    doc: "Documents shorter than this skip the model (0 = every document). On
      jats-ingest set it (e.g. 1500) to keep table/figure units off the model."
  glossary_llm_seed:
    type: ["null", int]
    doc: "Send this seed with every call (null = none sent)."
  glossary_llm_extra_body:
    type: ["null", string]
    doc: "A JSON object of extra request fields, recorded verbatim and hashed into
      the version — never credentials (the tool refuses credential-like keys)."

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
      workflow_id: workflow_id
      tool_image: tool_image
      tool_image_digest: tool_image_digest
    out: [jsonl, skips]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-tools-v1.6.20-b1.sif
          dockerImageId: ragstack-tools-v1.6.20-b1.sif
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
        workflow_id:
          type: ["null", string]
          inputBinding:
            prefix: --workflow-id
            position: 6
        tool_image:
          type: ["null", string]
          inputBinding:
            prefix: --tool-image
            position: 7
        tool_image_digest:
          type: ["null", string]
          inputBinding:
            prefix: --tool-image-digest
            position: 8
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
      symbol_glossary: symbol_glossary
      workflow_id: workflow_id
      tool_image: tool_image
      tool_image_digest: tool_image_digest
    out: [embeddings, receipt]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-tools-v1.6.20-b1.sif
          dockerImageId: ragstack-tools-v1.6.20-b1.sif
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
          type:
            type: enum
            symbols: [fixed, fixed_token, sentence, words]
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
        symbol_glossary:
          type: string
          default: "rules"
          inputBinding:
            prefix: --symbol-glossary
            position: 13
        workflow_id:
          type: ["null", string]
          inputBinding:
            prefix: --workflow-id
            position: 14
        tool_image:
          type: ["null", string]
          inputBinding:
            prefix: --tool-image
            position: 15
        tool_image_digest:
          type: ["null", string]
          inputBinding:
            prefix: --tool-image-digest
            position: 16
      arguments:
        - position: 11
          prefix: --out
          valueFrom: $(inputs.shard.nameroot).emb.jsonl
        - position: 12
          prefix: --receipt
          valueFrom: $(inputs.shard.nameroot).receipt.json
      outputs:
        embeddings:
          type: File
          outputBinding:
            glob: $(inputs.shard.nameroot).emb.jsonl
        receipt:
          type: File
          outputBinding:
            glob: $(inputs.shard.nameroot).receipt.json

  glossary:
    doc: "One extracted shard -> its glossary.jsonl + summary (#847 G2), in
      parallel with embed (both read the extract output); joined at load."
    scatter: shard
    in:
      shard: extract/jsonl
      glossary_llm: glossary_llm
      glossary_llm_endpoint: glossary_llm_endpoint
      glossary_llm_model: glossary_llm_model
      glossary_llm_api_key_env: glossary_llm_api_key_env
      glossary_llm_concurrency: glossary_llm_concurrency
      glossary_llm_timeout_s: glossary_llm_timeout_s
      glossary_llm_max_tokens: glossary_llm_max_tokens
      glossary_llm_window_chars: glossary_llm_window_chars
      glossary_llm_max_windows: glossary_llm_max_windows
      glossary_llm_thinking: glossary_llm_thinking
      glossary_llm_repair_rounds: glossary_llm_repair_rounds
      glossary_llm_cutoff_retry: glossary_llm_cutoff_retry
      glossary_llm_deadline_s: glossary_llm_deadline_s
      glossary_llm_min_chars: glossary_llm_min_chars
      glossary_llm_seed: glossary_llm_seed
      glossary_llm_extra_body: glossary_llm_extra_body
      workflow_id: workflow_id
      tool_image: tool_image
      tool_image_digest: tool_image_digest
    out: [glossary, summary]
    # INLINED copy of cwl/glossary-step.cwl — keep in sync.
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-tools-v1.6.20-b1.sif
          dockerImageId: ragstack-tools-v1.6.20-b1.sif
        NetworkAccess:
          networkAccess: true
      baseCommand: [python, /opt/ragstack/scripts/glossary_step.py]
      # Exit 2 = a configuration refusal before any call (glossary_llm=on
      # without its endpoint/model/limits, a bad extra body); 3 = the shard is
      # unreadable or repeats a document id. Neither is worth a retry. An
      # unreachable or failing model is NOT an exit: the documents degrade to
      # the rules glossary and the task exits 0 (counts in the summary).
      permanentFailCodes: [2, 3]
      inputs:
        shard: {type: File, inputBinding: {position: 1}}
        glossary_llm: {type: string, default: "off", inputBinding: {prefix: --llm, position: 3}}
        glossary_llm_endpoint: {type: ["null", string], inputBinding: {prefix: --llm-endpoint, position: 4}}
        glossary_llm_model: {type: ["null", string], inputBinding: {prefix: --llm-model, position: 5}}
        glossary_llm_api_key_env: {type: string, default: OPENAI_API_KEY, inputBinding: {prefix: --llm-api-key-env, position: 6}}
        glossary_llm_concurrency: {type: int, default: 0, inputBinding: {prefix: --llm-concurrency, position: 7}}
        glossary_llm_timeout_s: {type: float, default: 0.0, inputBinding: {prefix: --llm-timeout-s, position: 8}}
        glossary_llm_max_tokens: {type: int, default: 0, inputBinding: {prefix: --llm-max-tokens, position: 9}}
        glossary_llm_window_chars: {type: int, default: 0, inputBinding: {prefix: --llm-window-chars, position: 10}}
        glossary_llm_max_windows: {type: int, default: 0, inputBinding: {prefix: --llm-max-windows, position: 11}}
        glossary_llm_thinking: {type: string, default: "off", inputBinding: {prefix: --llm-thinking, position: 12}}
        glossary_llm_repair_rounds: {type: int, default: 0, inputBinding: {prefix: --llm-repair-rounds, position: 13}}
        glossary_llm_cutoff_retry: {type: string, default: "off", inputBinding: {prefix: --llm-cutoff-retry, position: 14}}
        glossary_llm_deadline_s: {type: float, default: 0.0, inputBinding: {prefix: --llm-deadline-s, position: 15}}
        glossary_llm_min_chars: {type: int, default: 0, inputBinding: {prefix: --llm-min-chars, position: 16}}
        glossary_llm_seed: {type: ["null", int], inputBinding: {prefix: --llm-seed, position: 17}}
        glossary_llm_extra_body: {type: ["null", string], inputBinding: {prefix: --llm-extra-body, position: 18}}
        workflow_id:
          type: ["null", string]
          inputBinding: {prefix: --workflow-id, position: 19}
        tool_image:
          type: ["null", string]
          inputBinding: {prefix: --tool-image, position: 20}
        tool_image_digest:
          type: ["null", string]
          inputBinding: {prefix: --tool-image-digest, position: 21}
      arguments:
        # Per-shard names (#852): GoWe stages a gathered File[] flat by
        # basename, so a constant name would leave ONE file for N tasks.
        - {position: 2, prefix: --out, valueFrom: $(inputs.shard.nameroot).glossary.jsonl}
        - {position: 22, prefix: --summary, valueFrom: $(inputs.shard.nameroot).glossary-summary.json}
      outputs:
        glossary:
          type: File
          outputBinding: {glob: $(inputs.shard.nameroot).glossary.jsonl}
        summary:
          type: File
          outputBinding: {glob: $(inputs.shard.nameroot).glossary-summary.json}

  merge_glossary:
    doc: "Gather the per-shard glossary files -> ONE glossary.jsonl for the load
      step (glossary_file.py merge: a doc_id in two files, or files built under
      different configs, refuses with exit 2). One gather task, so the constant
      output name is safe."
    in:
      files: glossary/glossary
    out: [glossary_file]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-tools-v1.6.20-b1.sif
          dockerImageId: ragstack-tools-v1.6.20-b1.sif
      baseCommand: [python, /opt/ragstack/scripts/glossary_file.py, merge]
      permanentFailCodes: [2]
      inputs:
        files:
          type: File[]
          inputBinding: {position: 2}
      arguments:
        - {position: 3, prefix: --out, valueFrom: glossary.jsonl}
      outputs:
        glossary_file:
          type: File
          outputBinding: {glob: glossary.jsonl}

  merge:
    doc: "Gather per-shard embed receipts -> run summary."
    in:
      receipts: embed/receipt
      workflow_id: workflow_id
      tool_image: tool_image
      tool_image_digest: tool_image_digest
    out: [summary]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-tools-v1.6.20-b1.sif
          dockerImageId: ragstack-tools-v1.6.20-b1.sif
      baseCommand: [python, /opt/ragstack/scripts/merge_receipts.py]
      inputs:
        receipts:
          type: File[]
          inputBinding:
            position: 2
        workflow_id:
          type: ["null", string]
          inputBinding:
            prefix: --workflow-id
            position: 4
        tool_image:
          type: ["null", string]
          inputBinding:
            prefix: --tool-image
            position: 5
        tool_image_digest:
          type: ["null", string]
          inputBinding:
            prefix: --tool-image-digest
            position: 6
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
      no_delete_prior: no_delete_prior
      file_concurrency: file_concurrency
      bulk_refresh: bulk_refresh
      glossary_file: merge_glossary/glossary_file
      max_chunks: max_chunks
      workflow_id: workflow_id
      tool_image: tool_image
      tool_image_digest: tool_image_digest
    out: [summary]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-tools-v1.6.20-b1.sif
          dockerImageId: ragstack-tools-v1.6.20-b1.sif
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
        no_delete_prior:
          type: boolean
          inputBinding:
            prefix: --no-delete-prior
            position: 8
        file_concurrency:
          type: int
          inputBinding:
            prefix: --file-concurrency
            position: 9
        bulk_refresh:
          type: boolean
          inputBinding:
            prefix: --bulk-refresh
            position: 10
        # #847 G2: every chunk's symbol_glossary field is rendered from the
        # merged glossary file; a document whose text_sha256 does not match
        # refuses the load before any write (exit 2).
        glossary_file:
          type: File
          inputBinding:
            prefix: --glossary-file
            position: 13
        max_chunks:
          type: int
          default: 0
          inputBinding:
            prefix: --max-chunks
            position: 14
        workflow_id:
          type: ["null", string]
          inputBinding:
            prefix: --workflow-id
            position: 15
        tool_image:
          type: ["null", string]
          inputBinding:
            prefix: --tool-image
            position: 16
        tool_image_digest:
          type: ["null", string]
          inputBinding:
            prefix: --tool-image-digest
            position: 17
      arguments:
        - position: 11
          prefix: --out
          valueFrom: load-summary.json
        - position: 12
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
  glossary_file:
    type: File
    doc: "The merged glossary.jsonl the load rendered from (#847)."
    outputSource: merge_glossary/glossary_file
  glossary_summaries:
    type: File[]
    doc: "Per-shard glossary step summaries: counts, degraded reasons."
    outputSource: glossary/summary
  skip_reports:
    type: File[]
    doc: "Per-shard extract skips (missing/corrupt/under-min XML). Read these —
      an article absent here and absent from the stores was never attempted."
    outputSource: extract/skips
