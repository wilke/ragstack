#!/usr/bin/env cwl-runner
# Tombstone version (#357, phase 2 of #353): the DELETE form of
# archive-collection.cwl. Same tool (archive_version.py), tombstone mode — no
# embed output, no receipt; the input is the list of removed doc ids and the
# output is a Directory named `<version>` holding ONLY manifest.json +
# tombstone.json. Restore replays versions in registry order and honours the
# tombstone by dropping those doc ids.
#
# Standalone on purpose: a delete is a tiny submission of its own (no ingest
# workflow to hang the step on), submitted by the API as the user with the same
# output_destination as ingests, so the folder lands at `versions/<N>/` by the
# same mechanism (Directory basename -> subfolder). No token inside the task.
#
# CONTAINERIZED (#135): both docker keys declared — see cwl/README.md.
#
#   cwltool --singularity cwl/archive-tombstone.cwl tombstone.inputs.yml
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
    doc: "Version number N (digits) — the output directory's basename."
    inputBinding: {prefix: --version, position: 1}
  tombstone:
    type: File
    doc: "JSON: a list of removed doc ids, or an object with a `doc_ids` list.
      Must be non-empty — a delete of nothing is not a version."
    inputBinding: {prefix: --tombstone, position: 2}
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
  job_id:
    type: ["null", string]
    inputBinding: {prefix: --job-id, position: 6}

arguments:
  - {position: 7, prefix: --out, valueFrom: "."}

outputs:
  archive:
    type: Directory
    doc: "The version directory, basename == version: manifest.json + tombstone.json."
    outputBinding: {glob: $(inputs.version)}
