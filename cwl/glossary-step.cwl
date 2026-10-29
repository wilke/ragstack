#!/usr/bin/env cwl-runner
# The symbol glossary as a recorded workflow step (#847 G2): one JSONL shard of
# pre-extracted documents in (the same {text,path,metadata} shape ingest_shard /
# embed_shard read), one glossary.jsonl out (ragstack.glossary_file/1: a row per
# document — rules entries, the LLM entries when asked, the merged glossary,
# drops by reason, provenance, `degraded` with its reason) plus a summary.
#
# The ingest/load step renders every chunk's `symbol_glossary` field FROM this
# file (ingest_shard / load_embeddings --glossary-file) and refuses a row whose
# text_sha256 does not match the document the chunks were cut from. The pack
# step keeps the file in the version (role `glossary`), so a restore brings the
# glossary back with the chunks.
#
# glossary_llm="off" (the default): rules only, no network, seconds per shard.
# glossary_llm="on": also the LLM half from an OpenAI-compatible endpoint —
# bounded (a fixed number of calls per document, per-call timeout, transport
# retries, an optional deadline) and DEGRADING per document: a document whose
# model call fails gets the rules glossary and `degraded: <reason>`, and the task
# still exits 0. Every limit is an input defaulting to 0, required (> 0) with
# "on" (#849: no hidden defaults). The key is read from the ONE environment
# variable glossary_llm_api_key_env names (the worker's --secret-file), never
# from an input: workflow inputs are an immutable, rendered snapshot.
#
# Output names carry the shard's nameroot (#852): GoWe stages a gathered File[]
# flat by basename. Shard basenames must therefore be distinct.
#
# This standalone tool is inlined as the `glossary` step of
# cwl/pdf-ingest-scatter.cwl, cwl/ingest-bulk.cwl, cwl/jats-ingest.cwl and
# cwl/embed-bulk.cwl (GoWe registers the CWL text and cannot resolve an
# external `run:`) — keep the copies in sync
# (test_inlined_glossary_agrees_with_the_standalone_tool).
#
#   CWL_SINGULARITY_CACHE=apptainer/images \
#     cwltool --singularity cwl/glossary-step.cwl --shard batch-00000.jsonl
cwlVersion: v1.2
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
    doc: "ragstack.glossary_file/1: a row per document of the shard."
    outputBinding: {glob: $(inputs.shard.nameroot).glossary.jsonl}
  summary:
    type: File
    doc: "The step summary: counts, degraded reasons, settings, provenance."
    outputBinding: {glob: $(inputs.shard.nameroot).glossary-summary.json}
