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
# Image resolution: `ragstack-worker.sif` is a bare filename, not a host path, and
# it is declared under BOTH `dockerPull` and `dockerImageId` — each runner reads a
# different key and neither falls back to the other:
#   * GoWe's CWL parser reads ONLY `dockerPull`. With just `dockerImageId` the task
#     registers fine but dies at execution with "Apptainer execution requested but
#     no docker image specified". A GoWe worker joins the bare name onto its
#     `--image-dir`, so the SIF must be deployed into that dir on every worker host
#     (override per run with a gowe:Execution.docker_image hint).
#   * cwltool --singularity treats a lone `dockerPull` as a REGISTRY reference: it
#     searches for "<value>.sif"/"<value>.img" (i.e. ragstack-worker.sif.sif) and,
#     not finding it, runs `singularity pull docker://ragstack-worker.sif` — which
#     fails. Only `dockerImageId` makes it match the bare filename in
#     $CWL_SINGULARITY_CACHE (point that at apptainer/images/).
# With both keys present cwltool takes the dockerImageId branch and never pulls, so
# the pair is the one spelling that runs on both. cwltool does NOT expand
# `$(inputs...)` expressions inside DockerRequirement, so the image can't be a CWL
# input — the filename is the seam instead. Build it with:
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
# GLOSSARY (#847 G2). A `glossary` step (cwl/glossary-step.cwl, inlined) runs per
# shard first; `ingest_shard` takes its `<shard>.glossary.jsonl` as
# --glossary-file (dotproduct scatter: shard i with glossary i) and renders every
# chunk's symbol_glossary field from it. symbol_glossary="off" still runs the
# step but ingest_shard ignores the file. Shard basenames must be distinct (#852).
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
  collection_id:
    type: ["null", string]
    doc: "Registry collection id (#263) — the id the ingest RESOLVES through the
      registry, from which the physical store names and the build spec come.
      Optional here only for compatibility with existing inputs files: without
      it the tool falls back to matching the PHYSICAL `collection` name against
      the registry (ingest_target.resolve_by_store_name), which still refuses an
      unregistered store but cannot tell two entries over one store apart. Give
      it. When both are given the id wins and `collection` is CHECKED against
      the entry rather than used to name anything."
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
  tenant: {type: string, default: "public"}
  chunk_method:
    type:
      type: enum
      symbols: [fixed, fixed_token, sentence, words, semantic, semantic_pooled]
    default: fixed_token
  chunk_size: {type: int, default: 256}
  chunk_overlap: {type: int, default: 32}
  embedding_url:
    type: string[]
    doc: "SFR embedding endpoint base URLs."
  embedding_model: {type: string, default: "Salesforce/SFR-Embedding-Mistral"}
  embedding_api_key:
    type: ["null", string]
    doc: "Bearer token for keyed endpoints."
  qdrant_url:
    type: string
    doc: "The Qdrant instance to WRITE to, as seen from the worker.
      REQUIRED, deliberately without a default (#407/#454): a default here
      decided where a run wrote whenever a caller omitted it, and the old
      fallback (the CLI's localhost:6333) is production on the deployment host.
      This workflow had no such input at all, so every bulk run inherited that
      fallback."
  es_url:
    type: string
    doc: "The Elasticsearch instance to WRITE to, as seen from the worker.
      REQUIRED, no default — see qdrant_url."
  symbol_glossary:
    type: string
    default: "rules"
    doc: "Per-document symbol glossary passed to ingest_shard --symbol-glossary
      (off|rules; #754, #783). A separate BM25 field: content, the embedded text,
      the chunk ids and the collection identity are unchanged either way."
  max_chunks:
    type: int
    default: 0
    doc: "Per-collection chunk cap (#291, #849), passed to the tool's
      --max-chunks: the task refuses its shard, whole, when the collection's
      live chunk count plus the shard's chunks would exceed it. 0 (the default)
      = no cap from this input; the registry entry's explicit max_chunks
      override still applies. The submitter decides the cap (the API for user
      jobs, the operator for bulk jobs); nothing is derived from the worker."
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
  glossary:
    doc: "One shard -> its glossary.jsonl + summary (#847 G2)."
    scatter: shard
    in:
      shard: shards
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

  ingest_shard:
    doc: "Ingest one JSONL shard -> <shard>.receipt.json (stateless, idempotent)."
    scatter: [shard, glossary]
    scatterMethod: dotproduct
    in:
      shard: shards
      glossary: glossary/glossary
      collection: collection
      tenant: tenant
      chunk_method: chunk_method
      chunk_size: chunk_size
      chunk_overlap: chunk_overlap
      embedding_url: embedding_url
      embedding_model: embedding_model
      embedding_api_key: embedding_api_key
      qdrant_url: qdrant_url
      es_url: es_url
      collection_id: collection_id
      registry: registry
      symbol_glossary: symbol_glossary
      max_chunks: max_chunks
      workflow_id: workflow_id
      tool_image: tool_image
      tool_image_digest: tool_image_digest
    out: [receipt]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-tools-v1.6.20-b1.sif
          dockerImageId: ragstack-tools-v1.6.20-b1.sif
        NetworkAccess:
          networkAccess: true
      baseCommand: [python, /opt/ragstack/scripts/ingest_shard.py]
      # Exit 4 = the shard was refused at the chunk cap (#291): never worth a
      # retry. GoWe parses but does not yet honour this; cwltool does.
      # Exit 6 = the glossary file does not match the shard (#847).
      permanentFailCodes: [4, 6]
      inputs:
        shard: {type: File, inputBinding: {position: 2}}
        collection: {type: string, inputBinding: {prefix: --collection, position: 3}}
        tenant: {type: string, inputBinding: {prefix: --tenant, position: 4}}
        chunk_method:
          type:
            type: enum
            symbols: [fixed, fixed_token, sentence, words, semantic, semantic_pooled]
          inputBinding: {prefix: --chunk-method, position: 5}
        chunk_size: {type: int, inputBinding: {prefix: --chunk-size, position: 6}}
        chunk_overlap: {type: int, inputBinding: {prefix: --chunk-overlap, position: 7}}
        embedding_model: {type: string, inputBinding: {prefix: --embedding-model, position: 8}}
        embedding_api_key:
          type: ["null", string]
          inputBinding: {prefix: --embedding-api-key, position: 9}
        embedding_url:
          type: string[]
          inputBinding: {prefix: --embedding-url, position: 10}
        qdrant_url: {type: string, inputBinding: {prefix: --qdrant-url, position: 13}}
        es_url: {type: string, inputBinding: {prefix: --es-url, position: 14}}
        collection_id:
          type: ["null", string]
          inputBinding: {prefix: --collection-id, position: 15}
        registry:
          type: ["null", string]
          inputBinding: {prefix: --registry, position: 16}
        symbol_glossary:
          type: string
          default: "rules"
          inputBinding: {prefix: --symbol-glossary, position: 17}
        # #847 G2: the glossary step's file for THIS shard; every chunk's
        # symbol_glossary field is rendered from it. A missing or mismatched
        # row refuses the batch with exit 6 (a wiring bug, never data).
        glossary:
          type: File
          inputBinding: {prefix: --glossary-file, position: 18}
        # #849: exit 4 on a refusal (permanentFailCodes below).
        max_chunks: {type: int, default: 0, inputBinding: {prefix: --max-chunks, position: 19}}
        # Provenance (ADR-0010 decision 8, #821): recorded on the receipt.
        workflow_id:
          type: ["null", string]
          inputBinding: {prefix: --workflow-id, position: 20}
        tool_image:
          type: ["null", string]
          inputBinding: {prefix: --tool-image, position: 21}
        tool_image_digest:
          type: ["null", string]
          inputBinding: {prefix: --tool-image-digest, position: 22}
      arguments:
        - {position: 11, prefix: --es-index, valueFrom: $(inputs.collection)}
        - {position: 12, prefix: --out, valueFrom: $(inputs.shard.nameroot).receipt.json}
      outputs:
        receipt: {type: File, outputBinding: {glob: $(inputs.shard.nameroot).receipt.json}}

  merge:
    doc: "Gather per-shard receipts -> run summary (totals + failed shards)."
    in:
      receipts: ingest_shard/receipt
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
          inputBinding: {position: 2}
        workflow_id:
          type: ["null", string]
          inputBinding: {prefix: --workflow-id, position: 4}
        tool_image:
          type: ["null", string]
          inputBinding: {prefix: --tool-image, position: 5}
        tool_image_digest:
          type: ["null", string]
          inputBinding: {prefix: --tool-image-digest, position: 6}
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
  glossary_summaries:
    type: File[]
    doc: "Per-shard glossary step summaries (#847): counts, degraded reasons."
    outputSource: glossary/summary
