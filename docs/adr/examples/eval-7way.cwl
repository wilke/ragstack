#!/usr/bin/env cwl-runner
# Template (ADR 0001, Appendix B) — scatter the 7-way chunking eval over configs,
# one independent ingest+score run per config, then gather the statistics layer.
#
# This is illustrative: it assumes two thin CLI wrappers exist around the existing
# eval code (python/scripts/eval/) —
#   ragstack.scripts.eval.chunk_one       : ingest+retrieve+score ONE config -> metrics.json
#   ragstack.scripts.eval.aggregate_stats : paired-bootstrap CIs + Wilcoxon/Holm (_stats.py)
# The per-config runs are independent, so GoWe/any CWL runner can spread them across GPUs.
cwlVersion: v1.2
class: Workflow

requirements:
  ScatterFeatureRequirement: {}
  InlineJavascriptRequirement: {}

inputs:
  configs:
    type: string[]
    doc: "Chunking configs to compare, e.g. [fixed_char512, fixed_char2048, fixed_tok256, fixed_tok512, sentence_tok512, words_tok512, semantic_tokcap]"
  corpus:
    type: File
    doc: "Deterministic document subset (JSONL) ingested identically for every config."
  embedding_api_key:
    type: string?
    doc: "Bearer token for the embedding endpoint(s); omit for the keyless sidecar."

steps:
  ingest_and_score:
    doc: "One isolated ingest + known-item/SciFact scoring run per config."
    scatter: config
    in:
      config: config
      corpus: corpus
      embedding_api_key: embedding_api_key
    out: [metrics]
    run:
      class: CommandLineTool
      baseCommand: [python, -m, ragstack.scripts.eval.chunk_one]
      inputs:
        config:
          type: string
          inputBinding: { prefix: --config }
        corpus:
          type: File
          inputBinding: { prefix: --corpus }
        embedding_api_key:
          type: string?
          inputBinding: { prefix: --embedding-api-key }
      outputs:
        metrics:
          type: File
          outputBinding: { glob: metrics.json }

  aggregate:
    doc: "Gather: pairwise diff CIs + Wilcoxon signed-rank + Holm-Bonferroni over all configs."
    in:
      metrics: ingest_and_score/metrics
    out: [report]
    run:
      class: CommandLineTool
      baseCommand: [python, -m, ragstack.scripts.eval.aggregate_stats]
      inputs:
        metrics:
          type: File[]
          inputBinding: { prefix: --metrics }
      outputs:
        report:
          type: File
          outputBinding: { glob: report.md }

outputs:
  report:
    type: File
    outputSource: aggregate/report
