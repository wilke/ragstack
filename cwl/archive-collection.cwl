#!/usr/bin/env cwl-runner
# Archive step (#357, phase 2 of #353): pack the embed stage's embedding file(s)
# + the load stage's receipt(s) into ONE version directory, `<version>/`, in the
# ragstack-archive/1 format (python/ragstack/ingestion/archive.py), and emit it
# as a CWL `Directory` output.
#
# WHY A DIRECTORY NAMED BY THE VERSION. GoWe uploads every workflow-level
# File/Directory output to the submission's output_destination, and a Directory
# output's *basename becomes a subfolder* with its listing uploaded recursively.
# So an `archive` output whose basename is `N` lands at
# `<output_destination>/N/…` — i.e. `…/collections/<id>/versions/N/` when the
# API submits with that destination — with no Workspace call and no token
# inside this task. `version` is typed `string` (not int) because it is used in
# the output glob, and parameter references in globs are proven on both runners
# for string values only; the CLI insists on digits.
#
# Pure local I/O (no NetworkAccess): the tool reads the staged inputs and writes
# `./<version>/{manifest.json,chunks.jsonl.gz,vectors.f32,receipt.json}`.
# Streaming — 35k x 4096-d packs in well under the 30 s budget without holding
# the vectors in memory. Deletes use the sibling `archive-tombstone.cwl`.
#
# `receipt` is `File[]`: the scatter-per-PDF workflow gathers N per-item
# receipts (written as a JSON array); a single receipt is copied verbatim. The
# inlined copy in cwl/pdf-ingest.cwl types it `File` (its load step emits one
# summary) — GoWe resolves a single-source list to the bare value, so a
# linkMerge wrapper would give the two runners different types. Keep the
# inlined copies in cwl/pdf-ingest*.cwl in sync with this file.
#
# CONTAINERIZED (#135): runs in the ragstack-worker image; both docker keys are
# declared (GoWe reads dockerPull, cwltool --singularity reads dockerImageId —
# see cwl/README.md). Scripts live at /opt/ragstack/scripts.
#
#   CWL_SINGULARITY_CACHE=apptainer/images \
#     cwltool --singularity cwl/archive-collection.cwl archive.inputs.yml
cwlVersion: v1.2
class: CommandLineTool

requirements:
  DockerRequirement:
    dockerPull: ragstack-worker.sif
    dockerImageId: ragstack-worker.sif

baseCommand: [python, /opt/ragstack/scripts/archive_version.py]

inputs:
  version:
    type: string
    doc: "Version number N (digits). The output directory — and therefore the
      Workspace subfolder — is named exactly this."
    inputBinding: {prefix: --version, position: 1}
  chunks:
    type: File[]
    doc: "ragstack.embedding_file/v1 JSONL file(s) from the embed stage, in row
      order. Vectors are split out to vectors.f32; the rest goes to chunks.jsonl.gz."
    inputBinding: {prefix: --chunks, position: 2}
  receipt:
    type: File[]
    doc: "The load stage's receipt(s) — copied verbatim (one) or as a JSON array
      (several) to receipt.json."
    inputBinding: {prefix: --receipt, position: 3}
  collection_id:
    type: string
    doc: "Registry collection id (#263) recorded in manifest.json — restore
      refuses a manifest whose identity disagrees with the registry row."
    inputBinding: {prefix: --collection-id, position: 4}
  tenant:
    type: string
    default: "public"
    inputBinding: {prefix: --tenant, position: 5}
  spec_hash:
    type: ["null", string]
    doc: "The collection's build-spec hash (ADR-0002); restore refuses a
      mismatch. Registry knowledge — the tool cannot derive it, so pass it."
    inputBinding: {prefix: --spec-hash, position: 6}
  job_id:
    type: ["null", string]
    doc: "The RAGStack ingest job id this version came from."
    inputBinding: {prefix: --job-id, position: 7}

arguments:
  - {position: 8, prefix: --out, valueFrom: "."}

outputs:
  archive:
    type: Directory
    doc: "The version directory, basename == version: manifest.json,
      chunks.jsonl.gz, vectors.f32, receipt.json."
    outputBinding: {glob: $(inputs.version)}
