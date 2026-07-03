#!/usr/bin/env cwl-runner
# Runnable realization of ADR-0001 Appendix B (docs/adr/examples/eval-7way.cwl was
# the illustrative template). Scatters `chunk_one` over the chunking configs — one
# independent SciFact ingest+score task per config, spreadable across GPUs — then
# gathers the per-config metrics.json into the stats report via `aggregate_stats`.
#
# The step tools are the ragstack eval CLIs under python/scripts/eval/. Each step
# stages that directory (InitialWorkDirRequirement) into its job sandbox, so the
# workflow is CWD-independent and portable across CWL runners (cwltool, GoWe) — no
# reliance on PATH or a particular working directory. The tools still need the
# ragstack package importable in the runtime env (installed, or in the run SIF).
#
#   cd python && cwltool ../cwl/eval-scifact-7way.cwl ../cwl/eval-scifact-7way.inputs.yml
#
# NOTE: `chunk_one` needs the live SFR embedding fleet + Qdrant + ES (it ingests
# into isolated scifact_m7_<config> stores and tears them down); it is not a CI
# step. `aggregate_stats` is pure computation over the metric files.
cwlVersion: v1.2
class: Workflow

requirements:
  ScatterFeatureRequirement: {}

inputs:
  configs:
    type: string[]
    doc: "Chunking configs to compare (chunking_compare_7way.CONFIG_KEYS)."
  embedding_api_key:
    type: string?
    doc: "Bearer token for keyed embedding endpoints; omit for keyless."

steps:
  chunk_one:
    doc: "One isolated SciFact ingest + score per config -> metrics.json."
    scatter: config
    in:
      config: configs
      embedding_api_key: embedding_api_key
    out: [metrics]
    run:
      class: CommandLineTool
      requirements:
        InitialWorkDirRequirement:
          listing:
            - $(inputs.evaldir)
      baseCommand: [python]
      inputs:
        evaldir:
          type: Directory
          default: {class: Directory, location: ../python/scripts/eval}
        config:
          type: string
          inputBinding: {prefix: --config, position: 2}
        embedding_api_key:
          type: string?
          inputBinding: {prefix: --embedding-api-key, position: 3}
      arguments:
        - {position: 1, valueFrom: $(inputs.evaldir.basename)/chunk_one.py}
        - {position: 4, prefix: --out, valueFrom: metrics.json}
      outputs:
        metrics:
          type: File
          outputBinding: {glob: metrics.json}

  aggregate:
    doc: "Gather: metrics table + paired diff CIs + Holm-Wilcoxon -> report.md."
    in:
      metrics: chunk_one/metrics
    out: [report]
    run:
      class: CommandLineTool
      requirements:
        InitialWorkDirRequirement:
          listing:
            - $(inputs.evaldir)
      baseCommand: [python]
      inputs:
        evaldir:
          type: Directory
          default: {class: Directory, location: ../python/scripts/eval}
        metrics:
          type: File[]
          inputBinding: {position: 2}
      arguments:
        - {position: 1, valueFrom: $(inputs.evaldir.basename)/aggregate_stats.py}
        - {position: 3, prefix: --out, valueFrom: report.md}
      outputs:
        report:
          type: File
          outputBinding: {glob: report.md}

outputs:
  report:
    type: File
    outputSource: aggregate/report
