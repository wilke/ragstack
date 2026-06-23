#!/usr/bin/env bash
# Pull the base Python SIF used by the ragstack ML sidecars.
# Sidecar code (main.py, requirements.txt) is bind-mounted from sidecars/
# at run time, so we only need a plain Python container as the SIF.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMG="${RAG_IMAGES:-$HERE/images}"
mkdir -p "$IMG"

sif="$IMG/python.sif"
if [[ -f "$sif" ]]; then
    echo "[python] $sif exists — skipping (delete to re-pull)"
else
    echo "[python] pulling docker://python:3.12-slim -> $sif"
    apptainer pull "$sif" docker://python:3.12-slim
fi
