#!/usr/bin/env cwl-runner
# graph-extract workflow (#350, phase 6 of #201): the standalone, opt-in graph
# leg of a collection. One archived chunk version in → the graph leg extracted
# beside it (`extract`, cwl/extract-graph.cwl inlined) → loaded into the graph
# store scoped by (tenant, collection) within the collection's triple budget
# (`load`) → the version-named delta Directory as the ONLY workflow output.
#
# HOW IT IS RUN. `POST /v1/collections/{id}/graph` submits this AS THE USER
# (the caller's BV-BRC token authenticates the submission) with `version_dir`
# = the `ws://` versions/<n> Directory, which the engine pre-stages locally
# with that token — no task ever sees it — and output_destination = the
# owner's `versions/` folder, onto which the engine post-stages the delta
# (manifest.json overwritten to graph: true + triples.jsonl.gz added; see the
# extract tool's header for why that overwrite is the intended one). Never
# part of the ingest critical path: one LLM call per chunk is ~10x the embed
# cost, so this runs only when asked, once per version, budgeted.
#
# ORDER MATTERS. `archive` is sourced from `extract`, but `load` runs after it
# and the workflow completes only when both steps do: a load that REFUSES at
# the cap (exit 4) fails the submission, and GoWe post-stages only COMPLETED
# submissions — so a refusal leaves the Workspace archive untouched and the
# graph store unwritten (the tool checks the cap before its first write). The
# API classifies the failed submission by `error.context.exit_code == 4` into
# the job label `graph_cap_exceeded`.
#
# `load` resolves the PHYSICAL collection name from the REGISTRY ENTRY named by
# `collection_id` (#263) — never from the command line — so the worker must see
# the same registry the API does (as for restore-collection.cwl); the Neo4j
# credentials come from the worker's environment (NEO4J_USER / NEO4J_PASSWORD),
# never from a workflow input. Only the bolt URI is an input.
#
# The two tools are INLINED (GoWe registers CWL text — no external `run:`);
# python/tests/ingestion/test_extract_graph.py pins them to their standalone
# copies (cwl/extract-graph.cwl; the load tool has none).
#
# CONTAINERIZED (#135): runs in the ragstack-worker image; both docker keys are
# declared (GoWe reads dockerPull, cwltool --singularity reads dockerImageId —
# see cwl/README.md). Scripts live at /opt/ragstack/scripts.
#
#   CWL_SINGULARITY_CACHE=apptainer/images \
#     cwltool --singularity cwl/graph-extract.cwl graph-extract.inputs.yml
cwlVersion: v1.2
class: Workflow

inputs:
  version_dir:
    type: Directory
    doc: "The archived chunk version (ragstack-archive/1) —
      `ws:///<user>/home/.ragstack/collections/<id>/versions/<n>`, pre-staged
      locally by the engine with the submitter's token."
  version:
    type: string
    doc: "Version number N (digits): the output Directory's basename, hence the
      versions/ subfolder the leg is merged into."
  collection_id:
    type: string
    doc: "Registry collection id (#263): the manifest must name it, and the
      load step resolves the physical graph scope from its entry."
  tenant:
    type: string
    default: "public"
  spec_hash:
    type: ["null", string]
    doc: "The registry row's build-spec hash (ADR-0002) the manifest must carry."
  llm_endpoint:
    type: string
    doc: "OpenAI-compatible chat endpoint as seen from the worker."
  llm_model:
    type: string
  concurrency:
    type: int
    default: 8
  max_triples:
    type: int
    default: 0
    doc: "The collection's triple budget (graph_max_triples_per_collection):
      the extract step refuses a version that alone exceeds it, the load step
      refuses when live + incoming would. 0 = unlimited."
  max_triples_per_chunk:
    type: int
    default: 0
  max_failed_fraction:
    type: float
    default: 0.5
    doc: "Share of attempted chunks whose LLM call may fail before the extract
      step refuses the run as an outage (exit 1, retryable, nothing archived)."
  graph_backend:
    type: string
    default: "neo4j"
    doc: "neo4j | memory (memory is the process-local dev/test store)."
  neo4j_uri:
    type: string
    default: "bolt://localhost:7687"
    doc: "The graph store as seen from the worker (credentials from its env)."
  job_id:
    type: ["null", string]

steps:
  extract:
    doc: "chunks.jsonl.gz -> LLM triples -> the delta directory <version>/."
    in:
      version_dir: version_dir
      version: version
      collection_id: collection_id
      tenant: tenant
      spec_hash: spec_hash
      llm_endpoint: llm_endpoint
      llm_model: llm_model
      concurrency: concurrency
      max_triples: max_triples
      max_triples_per_chunk: max_triples_per_chunk
      max_failed_fraction: max_failed_fraction
      job_id: job_id
    out: [archive, summary]
    run:
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
          inputBinding: {prefix: --version-dir, position: 1}
        version:
          type: string
          inputBinding: {prefix: --version, position: 2}
        collection_id:
          type: string
          inputBinding: {prefix: --collection-id, position: 3}
        tenant:
          type: string
          default: "public"
          inputBinding: {prefix: --tenant, position: 4}
        spec_hash:
          type: ["null", string]
          inputBinding: {prefix: --spec-hash, position: 5}
        llm_endpoint:
          type: string
          inputBinding: {prefix: --llm-endpoint, position: 6}
        llm_model:
          type: string
          inputBinding: {prefix: --llm-model, position: 7}
        concurrency:
          type: int
          default: 8
          inputBinding: {prefix: --concurrency, position: 8}
        max_triples:
          type: int
          default: 0
          inputBinding: {prefix: --max-triples, position: 9}
        max_triples_per_chunk:
          type: int
          default: 0
          inputBinding: {prefix: --max-triples-per-chunk, position: 10}
        max_failed_fraction:
          type: float
          default: 0.5
          inputBinding: {prefix: --max-failed-fraction, position: 11}
        job_id:
          type: ["null", string]
      arguments:
        - {position: 12, prefix: --out, valueFrom: "."}
        - {position: 13, prefix: --summary, valueFrom: extract-graph-summary.json}
      outputs:
        archive:
          type: Directory
          outputBinding: {glob: $(inputs.version)}
        summary:
          type: File
          outputBinding: {glob: extract-graph-summary.json}

  load:
    doc: "The delta's triples -> the graph store, scoped to the registry entry's
      physical collection, refused whole at the cap (exit 4, nothing loaded)."
    in:
      version_dir: extract/archive
      collection_id: collection_id
      max_triples: max_triples
      graph_backend: graph_backend
      neo4j_uri: neo4j_uri
    out: [summary]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif
          dockerImageId: ragstack-worker.sif
        NetworkAccess:
          networkAccess: true
      # 3 = the leg failed verification (permanent); 4 = the graph budget
      # (permanent — the API classifies the job by it). 2 (registry
      # disagreement) and 1 (mid-load store failure) stay retryable.
      permanentFailCodes: [3, 4]
      baseCommand: [python, /opt/ragstack/scripts/load_graph.py]
      inputs:
        version_dir:
          type: Directory
          inputBinding: {prefix: --version-dir, position: 1}
        collection_id:
          type: string
          inputBinding: {prefix: --collection-id, position: 2}
        max_triples:
          type: int
          default: 0
          inputBinding: {prefix: --max-triples, position: 3}
        graph_backend:
          type: string
          default: "neo4j"
          inputBinding: {prefix: --graph-backend, position: 4}
        neo4j_uri:
          type: string
          default: "bolt://localhost:7687"
          inputBinding: {prefix: --neo4j-uri, position: 5}
      arguments:
        - {position: 6, prefix: --out, valueFrom: graph-load-summary.json}
      outputs:
        summary:
          type: File
          outputBinding: {glob: graph-load-summary.json}

outputs:
  archive:
    type: Directory
    doc: "The delta directory named by the version — the ONLY workflow output,
      so nothing else is post-staged into versions/ (a top-level File output
      would land there flat by basename)."
    outputSource: extract/archive
