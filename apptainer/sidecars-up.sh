#!/usr/bin/env bash
# Start the ragstack ML sidecar instances via Apptainer.
#
# Design: rather than building a separate SIF per sidecar (which would
# need fakeroot, which this host lacks), we use one python:3.12-slim SIF
# and bind-mount each sidecar's source + a per-sidecar deps directory.
# Deps are pip-installed into the host deps dir on first run; subsequent
# `up` invocations skip the install. Model files (HF cache) are bound
# from apptainer/data/<svc>/cache/ so first-call downloads persist.
#
# Currently wraps: embedding (port 50053) + crossencoder reranker (port 50052).

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${RAG_DATA:-$HERE/data}"
IMG="${RAG_IMAGES:-$HERE/images}"
SIDECARS_SRC="$(cd "$HERE/.." && pwd)/sidecars"
# Cross-encoder model the reranker sidecar loads. Default matches
# config.reranker_model (a small, CPU-friendly MS-MARCO MiniLM).
CROSSENCODER_MODEL="${CROSSENCODER_MODEL:-cross-encoder/ms-marco-MiniLM-L-6-v2}"

mkdir -p "$DATA"/embedding/{deps,cache}
mkdir -p "$DATA"/crossencoder/{deps,cache}

SIF="$IMG/python.sif"
[[ -f "$SIF" ]] || { echo "ERROR: missing $SIF — run ./sidecars-pull.sh"; exit 1; }

already_running() {
    apptainer instance list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$1"
}

# pip install --target into the host deps dir on first run.
# `python -m uvicorn` (not the uvicorn console script) avoids shebang
# issues with the relocated install.
seed_deps_if_empty() {
    local svc="$1" reqs="$2" host_deps="$3"
    if [[ -n "$(ls -A "$host_deps" 2>/dev/null)" ]]; then return; fi
    echo "[$svc] installing deps into $host_deps (one-time, several minutes — pulls torch + sentence-transformers)"
    apptainer exec \
        --bind "$host_deps:/__deps" \
        --bind "$reqs:/__reqs.txt:ro" \
        "$SIF" \
        pip install --no-cache-dir --target /__deps -r /__reqs.txt
}

start_embedding() {
    seed_deps_if_empty embedding \
        "$SIDECARS_SRC/embedding/requirements.txt" \
        "$DATA/embedding/deps"

    if already_running embedding; then
        echo "[embedding] already running — skipping"
        return
    fi
    echo "[embedding] starting on :50053"
    apptainer instance run \
        --bind "$SIDECARS_SRC/embedding:/app:ro" \
        --bind "$DATA/embedding/deps:/deps:ro" \
        --bind "$DATA/embedding/cache:/cache" \
        --env PYTHONPATH=/deps \
        --env HF_HOME=/cache \
        --env TRANSFORMERS_CACHE=/cache \
        --env SENTENCE_TRANSFORMERS_HOME=/cache \
        --env PORT=50053 \
        "$SIF" embedding \
        /bin/sh -c 'cd /app && exec /usr/local/bin/python -m uvicorn main:app --host 0.0.0.0 --port 50053'
}

start_crossencoder() {
    seed_deps_if_empty crossencoder \
        "$SIDECARS_SRC/crossencoder/requirements.txt" \
        "$DATA/crossencoder/deps"

    if already_running crossencoder; then
        echo "[crossencoder] already running — skipping"
        return
    fi
    echo "[crossencoder] starting on :50052 (model: $CROSSENCODER_MODEL)"
    apptainer instance run \
        --bind "$SIDECARS_SRC/crossencoder:/app:ro" \
        --bind "$DATA/crossencoder/deps:/deps:ro" \
        --bind "$DATA/crossencoder/cache:/cache" \
        --env PYTHONPATH=/deps \
        --env HF_HOME=/cache \
        --env TRANSFORMERS_CACHE=/cache \
        --env SENTENCE_TRANSFORMERS_HOME=/cache \
        --env MODEL_NAME="$CROSSENCODER_MODEL" \
        --env PORT=50052 \
        "$SIF" crossencoder \
        /bin/sh -c 'cd /app && exec /usr/local/bin/python -m uvicorn main:app --host 0.0.0.0 --port 50052'
}

start_embedding
start_crossencoder

echo
apptainer instance list
