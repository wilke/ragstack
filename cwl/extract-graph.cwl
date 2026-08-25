#!/usr/bin/env cwl-runner
# extract-graph step (#350, phase 6 of #201): extract knowledge-graph triples
# from ONE archived chunk version (`versions/<n>/`, ragstack-archive/1) with the
# LLM extractor, and emit the graph leg — `triples.jsonl.gz` + the UPDATED
# `manifest.json` (`graph: true`, the `triples` role, sha256/bytes, counts) — as
# a CWL `Directory` output whose basename is the version number.
#
# WHY A DELTA DIRECTORY NAMED BY THE VERSION. The API submits the standalone
# workflow (cwl/graph-extract.cwl, which inlines this tool) AS THE USER with
# output_destination = the collection's `versions/` folder. GoWe post-stages a
# Directory output's listing under `<output_destination>/<basename>/`, each
# file by basename WITH OVERWRITE (pkg/bvbrc/workspace.go WorkspaceUpload,
# Overwrite: true; scheduler/workspace.go stageFileInTree) — so this output
# lands ON `versions/<n>/`: the manifest there is overwritten (the one intended
# overwrite of an archived file — it now says graph: true and names the leg),
# `triples.jsonl.gz` is added, and the chunk/vector/receipt files already there
# are untouched because they are not in this output. Post-staging happens only
# for COMPLETED submissions, so a downstream step that fails (the load tool
# refusing at the cap) delivers nothing: the archive is never half-updated.
#
# `version` is typed `string` and used ONLY in the output glob (parameter
# references in globs are proven on both runners for string values); the tool
# cross-checks it against the manifest's own version. The tool never sees a
# token: the engine pre-stages the `ws://` Directory with the submitter's.
# The LLM endpoint is reached over NetworkAccess; its API key (if any) comes
# from $OPENAI_API_KEY in the worker's environment, never from an input.
#
# Exit 3 = the archive was REFUSED (ArchiveCorrupt:/SpecMismatch: on stderr —
# permanent, the engine must not retry). Exit 4 = the version's own triples
# exceed `max_triples` (graph_cap_exceeded: … on stderr — permanent; the API
# classifies the job by this code). Exit 1 = RETRYABLE: the LLM endpoint failed
# for every attempted chunk or for more than `max_failed_fraction` of them
# (llm_unavailable: … on stderr; nothing written — an outage must never be
# archived as an empty graph), or a write failure.
#
# CONTAINERIZED (#135): runs in the ragstack-worker image; both docker keys are
# declared (GoWe reads dockerPull, cwltool --singularity reads dockerImageId —
# see cwl/README.md). Scripts live at /opt/ragstack/scripts.
#
#   CWL_SINGULARITY_CACHE=apptainer/images \
#     cwltool --singularity cwl/extract-graph.cwl extract.inputs.yml
cwlVersion: v1.2
class: CommandLineTool

requirements:
  DockerRequirement:
    dockerPull: ragstack-worker.sif
    dockerImageId: ragstack-worker.sif
  NetworkAccess:
    networkAccess: true

permanentFailCodes: [3, 4]

baseCommand: [python, /opt/ragstack/scripts/extract_graph.py]

inputs:
  version_dir:
    type: Directory
    doc: "The archived chunk version to extract from —
      `ws:///<user>/home/.ragstack/collections/<id>/versions/<n>`, pre-staged
      locally by the engine with the submitter's token. Only chunks.jsonl.gz
      and manifest.json are read."
    inputBinding: {prefix: --version-dir, position: 1}
  version:
    type: string
    doc: "Version number N (digits). The output directory — and therefore the
      Workspace subfolder the leg is merged into — is named exactly this; the
      manifest's own version must agree."
    inputBinding: {prefix: --version, position: 2}
  collection_id:
    type: string
    doc: "Registry collection id the manifest must name (refused otherwise)."
    inputBinding: {prefix: --collection-id, position: 3}
  tenant:
    type: string
    default: "public"
    doc: "The tenant the caller expects — informational: the tool prints a
      note when the manifest disagrees. Triples keep their chunk's tenant_id
      (the manifest's tenant is the fallback), never this value."
    inputBinding: {prefix: --tenant, position: 4}
  spec_hash:
    type: ["null", string]
    doc: "The registry row's build-spec hash (ADR-0002) the manifest must carry."
    inputBinding: {prefix: --spec-hash, position: 5}
  llm_endpoint:
    type: string
    doc: "OpenAI-compatible chat endpoint as seen from the worker."
    inputBinding: {prefix: --llm-endpoint, position: 6}
  llm_model:
    type: string
    doc: "Model name the endpoint serves."
    inputBinding: {prefix: --llm-model, position: 7}
  concurrency:
    type: int
    default: 8
    doc: "LLM calls in flight at once (the throughput lever; bounded so one job
      cannot monopolise the shared endpoint)."
    inputBinding: {prefix: --concurrency, position: 8}
  max_triples:
    type: int
    default: 0
    doc: "Graph budget: refuse (exit 4) when this version alone yields more
      triples than this; 0 = unbounded. The load step applies the same cap
      against the live graph."
    inputBinding: {prefix: --max-triples, position: 9}
  max_triples_per_chunk:
    type: int
    default: 0
    doc: "Keep at most N triples per chunk (0 = unbounded)."
    inputBinding: {prefix: --max-triples-per-chunk, position: 10}
  max_failed_fraction:
    type: float
    default: 0.5
    doc: "Refuse the run (exit 1, RETRYABLE, nothing written, no delta) when
      more than this share of the attempted chunks failed their LLM call — and
      always when every one did: an outage is not an empty graph, and a
      delivered empty leg would be permanent (idempotent per version)."
    inputBinding: {prefix: --max-failed-fraction, position: 11}
  job_id:
    type: ["null", string]
    doc: "The RAGStack job id (recorded by the API; not used by the tool)."

arguments:
  - {position: 12, prefix: --out, valueFrom: "."}
  - {position: 13, prefix: --summary, valueFrom: extract-graph-summary.json}

outputs:
  archive:
    type: Directory
    doc: "The delta directory, basename == version: manifest.json (updated,
      graph: true) + triples.jsonl.gz. Merged onto versions/<version>/ by
      post-staging."
    outputBinding: {glob: $(inputs.version)}
  summary:
    type: File
    doc: "Run summary (counts, chunks/s). A step-level output only — never a
      workflow-level one, which would be post-staged flat into versions/."
    outputBinding: {glob: extract-graph-summary.json}
