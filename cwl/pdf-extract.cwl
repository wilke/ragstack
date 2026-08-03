#!/usr/bin/env cwl-runner
# PDF -> JSONL text extraction (#202/#203). Runs the real ragstack PdfLoader
# (PyMuPDF) over a set of PDFs and emits ONE JSONL shard in the exact
# {"text","path","metadata"} shape that ingest_shard.py / embed_shard.py consume
# via JsonlLoader — the missing first step that lets a directory of PDFs flow
# through the existing bulk embed/ingest/load workflows.
#
# No OCR: a scanned/image-only or unreadable PDF is recorded as *skipped* in the
# sidecar report (never crashes the job). Deterministic (inputs sorted).
#
# CONTAINERIZED (#135). Runs inside the ragstack-worker image via
# DockerRequirement; the script lives at /opt/ragstack/scripts/pdf_extract.py.
# Extraction is offline (no embedding fleet / stores), so no NetworkAccess and no
# HF tokenizer are needed here. Image resolution + build: see cwl/ingest-bulk.cwl.
#
# This standalone tool is also inlined verbatim as the `extract` step of
# cwl/pdf-ingest.cwl (that workflow must be self-contained to register with GoWe,
# which is sent the CWL text and cannot resolve an external `run:` reference) —
# keep the two copies in sync.
#
#   CWL_SINGULARITY_CACHE=apptainer/images \
#     cwltool --singularity cwl/pdf-extract.cwl --pdfs a.pdf --pdfs b.pdf
cwlVersion: v1.2
class: CommandLineTool

requirements:
  DockerRequirement:
    dockerPull: ragstack-worker.sif
    dockerImageId: ragstack-worker.sif

baseCommand: [python, /opt/ragstack/scripts/pdf_extract.py]

inputs:
  pdfs:
    type: File[]
    doc: "PDF files to extract (each staged into the container by the runner)."
    inputBinding:
      position: 2
  out_name:
    type: string
    default: "pdf-shard.jsonl"
    doc: "Name of the emitted JSONL shard (fed to embed/ingest steps)."
    inputBinding:
      prefix: --out
      position: 3

arguments:
  - position: 4
    prefix: --report
    valueFrom: $(inputs.out_name).report.json

outputs:
  shard:
    type: File
    doc: "The JSONL shard: one {text,path,metadata} line per extracted PDF."
    outputBinding:
      glob: $(inputs.out_name)
  report:
    type: File
    doc: "Sidecar JSON report: counts + the list of skipped files with reasons."
    outputBinding:
      glob: $(inputs.out_name).report.json
