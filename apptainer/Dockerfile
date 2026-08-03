# RAGStack CPU-only worker image (#135) — Docker/OCI parity build.
#
# Parallel to apptainer/ragstack-worker.def: same package + same CPU-only extras,
# so a site with Docker/Podman can build the identical worker. On the coconut dev
# host (no Docker, no --fakeroot) the .def / SIF is the build GoWe + cwltool use;
# this Dockerfile is for portability / other hosts / CI registries.
#
# CPU-ONLY BY DESIGN: every CWL step (ingest_shard / embed_shard / load_embeddings
# / pdf_extract) calls the embedding fleet over HTTP and never runs a model
# locally, so torch/CUDA are deliberately omitted. Deps = the ragstack package +
# its [vector,pdf,text,tokenization] extras (qdrant-client, pymupdf, elasticsearch,
# transformers-for-tokenizer). Versions are pinned by python/pyproject.toml.
#
# MODELS ARE NOT BAKED IN: the HF tokenizer is read at runtime from a bind-mounted
# HF_HOME (default /rag/cache). Mount it when you run:
#   docker run --rm -v /rag/cache:/rag/cache ragstack-worker \
#       python /opt/ragstack/scripts/pdf_extract.py ...
#
# Build (from the repo root, so the python/ build context is in scope):
#   docker build -f apptainer/Dockerfile -t ragstack-worker:latest .
#
# Convert to a SIF for apptainer hosts (optional, needs docker running):
#   apptainer build ragstack-worker.sif docker-daemon://ragstack-worker:latest
FROM python:3.12-slim-bookworm

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/rag/cache \
    HF_HUB_DISABLE_TELEMETRY=1

# The build context is the repo root; copy the package tree in.
COPY python /opt/ragstack/python

# elasticsearch pinned <9: a 9.x client's `compatible-with=9` header is rejected
# by an ES 8 server (the deployed cluster / prod ragstack env runs 8.x). The
# pyproject `text` extra is unbounded (>=8.13), so constrain it explicitly here.
RUN python -m pip install --upgrade pip \
    && python -m pip install "elasticsearch[async]>=8.13,<9" "/opt/ragstack/python[vector,pdf,text,tokenization]" \
    && python -m pip install sentencepiece protobuf \
    && cp -r /opt/ragstack/python/scripts /opt/ragstack/scripts \
    && chmod -R a+rX /opt/ragstack/scripts \
    && python -c "import ragstack, fitz, qdrant_client, elasticsearch, transformers; print('build import ok')"

WORKDIR /opt/ragstack
# No default CMD: the CWL steps invoke an explicit script path, e.g.
#   python /opt/ragstack/scripts/pdf_extract.py ...
