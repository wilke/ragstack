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
#
# After starting, waits until each sidecar's GET /ready answers 200 — /ready runs
# one real inference on the configured device (#751), so "up" means "the model
# works", not "the port is open". SIDECARS_READY_TIMEOUT (seconds, default 300;
# 0 skips the wait) bounds it; on timeout the last /ready body is printed and the
# script exits 1.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${RAG_DATA:-$HERE/data}"
IMG="${RAG_IMAGES:-$HERE/images}"
SIDECARS_SRC="$(cd "$HERE/.." && pwd)/sidecars"
# Cross-encoder model the reranker sidecar loads. Default matches
# config.reranker_model (BGE reranker v2-m3 — multilingual, 8k-context, ~560M
# params; runs on GPU by default below).
CROSSENCODER_MODEL="${CROSSENCODER_MODEL:-BAAI/bge-reranker-v2-m3}"
# Reranker device. Default cuda on this GPU host (~1.1 GB fp16, so it shares one
# card with the SFR fleet). Set CROSSENCODER_DEVICE=cpu to disable GPU (no --nv,
# and note this model is slow on CPU). CROSSENCODER_GPU pins which card.
CROSSENCODER_DEVICE="${CROSSENCODER_DEVICE:-cuda}"
CROSSENCODER_GPU="${CROSSENCODER_GPU:-0}"
SIDECARS_READY_TIMEOUT="${SIDECARS_READY_TIMEOUT:-300}"

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
    # Expose one GPU to the container when DEVICE=cuda. CUDA_VISIBLE_DEVICES pins
    # the card; inside the container it is remapped to cuda:0, so DEVICE=cuda is
    # correct regardless of which physical card CROSSENCODER_GPU selects. If cuda
    # is requested but the host has no NVIDIA driver, fall back to CPU rather than
    # letting `--nv` abort instance startup with a cryptic error.
    local gpu_args=()
    if [[ "$CROSSENCODER_DEVICE" == cuda* ]]; then
        if command -v nvidia-smi >/dev/null 2>&1; then
            gpu_args=(--nv --env CUDA_VISIBLE_DEVICES="$CROSSENCODER_GPU")
        else
            echo "[crossencoder] WARNING: CROSSENCODER_DEVICE=$CROSSENCODER_DEVICE but no" \
                 "nvidia-smi found — falling back to CPU (set CROSSENCODER_DEVICE=cpu to silence)"
            CROSSENCODER_DEVICE=cpu
        fi
    fi
    echo "[crossencoder] starting on :50052 (model: $CROSSENCODER_MODEL, device: $CROSSENCODER_DEVICE)"
    apptainer instance run \
        --bind "$SIDECARS_SRC/crossencoder:/app:ro" \
        --bind "$DATA/crossencoder/deps:/deps:ro" \
        --bind "$DATA/crossencoder/cache:/cache" \
        "${gpu_args[@]}" \
        --env PYTHONPATH=/deps \
        --env HF_HOME=/cache \
        --env TRANSFORMERS_CACHE=/cache \
        --env SENTENCE_TRANSFORMERS_HOME=/cache \
        --env MODEL_NAME="$CROSSENCODER_MODEL" \
        --env DEVICE="$CROSSENCODER_DEVICE" \
        --env PORT=50052 \
        "$SIF" crossencoder \
        /bin/sh -c 'cd /app && exec /usr/local/bin/python -m uvicorn main:app --host 0.0.0.0 --port 50052'
}

# wait_ready NAME URL SECONDS — poll URL until it answers 200. 503 (probe failed,
# model loading) and connection refused both mean "keep waiting"; at the deadline
# print the last status + body (the 503 names the exception class) and fail.
wait_ready() {
    local name="$1" url="$2" secs="$3" waited=0 code body tmp
    tmp="$(mktemp)"
    while :; do
        # -m 15: the sidecar's own probe is bounded at 5 s; leave headroom.
        code="$(curl -s -m 15 -o "$tmp" -w '%{http_code}' "$url" 2>/dev/null || true)"
        if [[ "$code" == 200 ]]; then
            echo "[$name] ready ($url)"
            rm -f "$tmp"
            return 0
        fi
        if (( waited >= secs )); then
            body="$(head -c 500 "$tmp" 2>/dev/null || true)"
            rm -f "$tmp"
            echo "[$name] ERROR: not ready after ${secs}s ($url)" >&2
            echo "[$name]   last answer: HTTP ${code:-000} ${body:-<no body>}" >&2
            return 1
        fi
        sleep 5
        waited=$((waited + 5))
    done
}

start_embedding
start_crossencoder

echo
apptainer instance list

if (( SIDECARS_READY_TIMEOUT > 0 )); then
    echo
    ready_fail=0
    wait_ready embedding http://127.0.0.1:50053/ready "$SIDECARS_READY_TIMEOUT" || ready_fail=1
    wait_ready crossencoder http://127.0.0.1:50052/ready "$SIDECARS_READY_TIMEOUT" || ready_fail=1
    exit "$ready_fail"
fi
