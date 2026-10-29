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
# GLOSSARY (#847 G2). A `glossary` step (cwl/glossary-step.cwl, inlined) runs per
# shard in parallel with the embed step (both read the same shard), and
# `merge_glossary` gathers the per-shard files into ONE glossary.jsonl — the
# `glossary_file` output, to hand to load-embeddings.cwl's `glossary_file`.
# KNOWN LIMITATION: on this plane `symbol_glossary` governs only embed_shard's
# in-process pass, which the load then overrides from the file;
# `symbol_glossary="off"` does NOT suppress the glossary field at load (the
# loader has no such switch, and the CWL cannot make the file binding
# conditional without JS). "No glossary at all" on the bulk plane is a
# follow-up. Shard basenames must be distinct (#852).
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
    type:
      type: enum
      symbols: [fixed, fixed_token, sentence, words]
    default: fixed_token
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
  symbol_glossary:
    type: string
    default: "rules"
    doc: "Per-document symbol glossary passed to embed_shard --symbol-glossary
      (off|rules; #754, #783). A separate BM25 field: content, the embedded text,
      the chunk ids and the collection identity are unchanged either way."
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
  embed_shard:
    doc: "Embed one JSONL shard -> <shard>.emb.jsonl + <shard>.receipt.json (no store contact)."
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
        symbol_glossary:
          type: string
          default: "rules"
          inputBinding:
            prefix: --symbol-glossary
            position: 12
        workflow_id:
          type: ["null", string]
          inputBinding:
            prefix: --workflow-id
            position: 13
        tool_image:
          type: ["null", string]
          inputBinding:
            prefix: --tool-image
            position: 14
        tool_image_digest:
          type: ["null", string]
          inputBinding:
            prefix: --tool-image-digest
            position: 15
      arguments:
        - position: 10
          prefix: --out
          valueFrom: $(inputs.shard.nameroot).emb.jsonl
        - position: 11
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
    doc: "One shard -> its glossary.jsonl + summary (#847 G2), in parallel
      with embed_shard; merged below for load-embeddings.cwl's
      glossary_file."
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
    doc: "Gather per-shard receipts -> run summary (totals + failed shards)."
    in:
      receipts: embed_shard/receipt
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
  # #847: hand this to load-embeddings.cwl's glossary_file.
  glossary_file:
    type: File
    outputSource: merge_glossary/glossary_file
  glossary_summaries:
    type: File[]
    outputSource: glossary/summary
