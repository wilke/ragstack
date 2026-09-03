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
#   cwltool cwl/eval-scifact-chunking.cwl cwl/eval-scifact-chunking.inputs.yml
#
# NOTE: `chunk_one` needs the live SFR embedding fleet + Qdrant + ES (it ingests
# into isolated scifact_m7_<config> stores and tears them down); it is not a CI
# step. `aggregate_stats` is pure computation over the metric files.
#
# `qdrant_url` and `es_url` are REQUIRED inputs with no default, and are bound to
# chunk_one's own required flags. A run that omits them refuses instead of falling
# through to a localhost address that is production on the deployment host (#476).
cwlVersion: v1.2
class: Workflow

requirements:
  ScatterFeatureRequirement: {}

inputs:
  configs:
    type: string[]
    doc: |
      Chunking configs to compare. Any key in
      chunking_compare_7way.ALL_CONFIG_KEYS — the legacy 7-way set plus the
      24-config stage-1 grid (chunking_compare_7way.STAGE1_CONFIG_KEYS, see
      docs/plans/chunking-evaluation.md). chunk_one rejects anything else.
  qdrant_url:
    type: string
    doc: |
      Qdrant base URL each scatter task ingests into. REQUIRED, no default:
      the localhost address chunk_one used to fall through to is the PRODUCTION
      instance on the deployment host (#476, same class as #407/#454). The
      isolated-store naming guards NAMES, not hosts — the caller names the host.
  es_url:
    type: string
    doc: "Elasticsearch base URL for the per-config index (same caveat as qdrant_url)."
  embedding_api_key:
    type: string?
    doc: "Bearer token for keyed embedding endpoints; omit for keyless."

steps:
  chunk_one:
    doc: "One isolated SciFact ingest + score per config -> metrics.json."
    scatter: config
    in:
      config: configs
      qdrant_url: qdrant_url
      es_url: es_url
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
        qdrant_url:
          type: string
          inputBinding: {prefix: --qdrant-url, position: 3}
        es_url:
          type: string
          inputBinding: {prefix: --es-url, position: 4}
        embedding_api_key:
          type: string?
          inputBinding: {prefix: --embedding-api-key, position: 5}
      arguments:
        - {position: 1, valueFrom: $(inputs.evaldir.basename)/chunk_one.py}
        - {position: 6, prefix: --out, valueFrom: metrics.json}
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
