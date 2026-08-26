#!/usr/bin/env bash
# start-ragstack-workers.sh — bring up the compute layer for cwl/jats-ingest.cwl.
#
# Three tiers (see cwl/jats-ingest.cwl header for the workflow shape):
#
#   1. GoWe CPU workers on coconut (group "ragstack") — run ALL CWL tasks:
#      extract (CPU XML parsing), embed (HTTP client), merge/load. No GPUs here;
#      the CWL steps never touch a GPU directly.
#   2. vLLM embedding replicas — one per GPU, serving
#      Salesforce/SFR-Embedding-Mistral via the OpenAI /v1/embeddings API
#      (--runner pooling). GPU load-balancing happens client-side: each embed
#      task's PooledEmbedder does least-loaded + failover across every URL in
#      the workflow's embedding_url list, probing <url>/health.
#   3. Same replicas on lambda13's 8x H100 (run this script THERE with
#      "vllm 0-7"). 8 single-GPU replicas, NOT tensor-parallel: the model fits
#      on one GPU, and independent replicas out-throughput a TP=8 shard while
#      isolating failures.
#
# Usage:
#   ./start-ragstack-workers.sh workers [N]        # N GoWe workers (default 24)
#   ./start-ragstack-workers.sh vllm [GPUS]        # vLLM replicas, e.g. "0,3-7"
#   ./start-ragstack-workers.sh urls               # print embedding_url YAML
#   ./start-ragstack-workers.sh stop [workers|vllm]
#   ./start-ragstack-workers.sh status
#
#   coconut:   ./start-ragstack-workers.sh workers 24
#              ./start-ragstack-workers.sh vllm 0,3-7      # GPUs 1,2 belong to
#                                                          # the folding workers
#   lambda13:  ./start-ragstack-workers.sh vllm 0-7
#
# Then submit with:
#   gowe submit cwl/jats-ingest.cwl -i cwl/jats-ingest.inputs.yml --group ragstack

set -euo pipefail

# ---------------------------------------------------------------- configuration
GOWE_SERVER="${GOWE_SERVER:-http://localhost:8091}"
GOWE_WORKER_BIN="${GOWE_WORKER_BIN:-/scout/Experiments/GoWe/bin/gowe-worker}"
WORKDIR_ROOT="${WORKDIR_ROOT:-/scout/wf/gowe/workdir}"
LOG_DIR="${LOG_DIR:-/scout/wf/gowe/logs/ragstack}"
IMAGE_DIR="${IMAGE_DIR:-/scout/containers}"
STAGE_OUT="${STAGE_OUT:-file:///scout/wf/data}"
RAG_DATA="${RAG_DATA:-/rag}"            # corpus, shards, registry db live here
# Container env + secrets for the ragstack group. Without these every engine-side
# ragstack task exits 2 (no collection registry in the container). Both files
# are passed only when present.
WORKER_ENV_FILE="${WORKER_ENV_FILE:-/scout/wf/gowe/ragstack-worker-env.env}"
WORKER_SECRET_FILE="${WORKER_SECRET_FILE:-/scout/wf/gowe/ragstack-worker-secrets.env}"
WORKER_GROUP="ragstack"
DEFAULT_WORKERS=24

EMBED_MODEL="${EMBED_MODEL:-Salesforce/SFR-Embedding-Mistral}"
EMBED_API_KEY="${EMBED_API_KEY:-BRCMistral}"   # bearer key the workflow passes
BASE_PORT="${BASE_PORT:-9001}"                 # replica i -> BASE_PORT + i
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"         # chunks are 512 tok; 4k is ample
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.90}"
HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}" # shared weight cache

# vLLM launcher: native `vllm` if installed (lambda13), else the vllm-openai
# container via apptainer --nv (coconut has no native vLLM).
if command -v vllm >/dev/null 2>&1; then
  vllm_cmd() { # $1=gpu $2=port
    CUDA_VISIBLE_DEVICES="$1" HF_HOME="$HF_HOME" \
    vllm serve "$EMBED_MODEL" \
      --runner pooling \
      --host 0.0.0.0 --port "$2" \
      --api-key "$EMBED_API_KEY" \
      --gpu-memory-utilization "$GPU_MEM_UTIL" \
      --max-model-len "$MAX_MODEL_LEN" \
      --disable-log-requests
  }
else
  VLLM_IMAGE="${VLLM_IMAGE:-docker://vllm/vllm-openai:latest}"
  vllm_cmd() { # $1=gpu $2=port
    apptainer exec --nv \
      --bind "$HF_HOME:/root/.cache/huggingface" \
      --env CUDA_VISIBLE_DEVICES="$1" \
      "$VLLM_IMAGE" \
      vllm serve "$EMBED_MODEL" \
        --runner pooling \
        --host 0.0.0.0 --port "$2" \
        --api-key "$EMBED_API_KEY" \
        --gpu-memory-utilization "$GPU_MEM_UTIL" \
        --max-model-len "$MAX_MODEL_LEN" \
        --disable-log-requests
  }
fi

mkdir -p "$LOG_DIR"

# -------------------------------------------------------------------- helpers
expand_gpus() { # "0,3-7" -> "0 3 4 5 6 7"
  echo "$1" | tr ',' '\n' | while IFS=- read -r a b; do
    if [ -n "${b:-}" ]; then seq "$a" "$b"; else echo "$a"; fi
  done | tr '\n' ' '
}

wait_healthy() { # $1=url $2=timeout_s
  local deadline=$(( $(date +%s) + $2 ))
  until curl -sf "$1/health" >/dev/null 2>&1; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "  TIMEOUT waiting for $1/health" >&2; return 1
    fi
    sleep 5
  done
}

# ------------------------------------------------------------------- commands
start_workers() {
  local n="${1:-$DEFAULT_WORKERS}"
  [ -f "$WORKER_ENV_FILE" ] || { echo "  note: $WORKER_ENV_FILE missing; starting without --env-file"; WORKER_ENV_FILE=""; }
  [ -f "$WORKER_SECRET_FILE" ] || { echo "  note: $WORKER_SECRET_FILE missing; starting without --secret-file"; WORKER_SECRET_FILE=""; }
  echo "Starting $n GoWe workers (group=$WORKER_GROUP) -> $GOWE_SERVER"
  for i in $(seq 1 "$n"); do
    local name="ragstack-oa-$i"
    mkdir -p "$WORKDIR_ROOT/$name"
    nohup "$GOWE_WORKER_BIN" \
      --server "$GOWE_SERVER" \
      --name "$name" \
      --group "$WORKER_GROUP" \
      --runtime apptainer \
      --image-dir "$IMAGE_DIR" \
      --extra-bind "$RAG_DATA" \
      ${WORKER_ENV_FILE:+--env-file "$WORKER_ENV_FILE"} \
      ${WORKER_SECRET_FILE:+--secret-file "$WORKER_SECRET_FILE"} \
      --stage-out "$STAGE_OUT" \
      --workdir "$WORKDIR_ROOT/$name" \
      --poll 500ms --log-level info \
      >"$LOG_DIR/$name.log" 2>&1 &
    echo "$!" >"$LOG_DIR/$name.pid"
  done
  echo "Logs: $LOG_DIR/ragstack-oa-*.log"
}

start_vllm() {
  local gpus; gpus=$(expand_gpus "${1:?usage: vllm <gpus, e.g. 0-7 or 0,3-7>}")
  local first=1 port urls=()
  echo "Starting vLLM replicas of $EMBED_MODEL on GPUs: $gpus"
  local i=0
  for gpu in $gpus; do
    port=$(( BASE_PORT + i )); i=$(( i + 1 ))
    echo "  GPU $gpu -> :$port"
    nohup bash -c "$(declare -f vllm_cmd); vllm_cmd $gpu $port" \
      >"$LOG_DIR/vllm-gpu$gpu.log" 2>&1 &
    echo "$!" >"$LOG_DIR/vllm-gpu$gpu.pid"
    urls+=("http://$(hostname -s):$port")
    # First replica downloads the weights into the shared HF cache; wait for it
    # before starting the rest so they load from cache instead of racing the
    # download. Later replicas start in parallel (cache is warm).
    if [ "$first" = 1 ]; then
      echo "  waiting for first replica (weight download + load)..."
      wait_healthy "http://localhost:$port" 1800
      first=0
    fi
  done
  echo "Waiting for remaining replicas..."
  i=0
  for gpu in $gpus; do
    port=$(( BASE_PORT + i )); i=$(( i + 1 ))
    wait_healthy "http://localhost:$port" 900 && echo "  :$port healthy"
  done
  echo
  echo "embedding_url entries for jats-ingest.inputs.yml:"
  printf '  - %s\n' "${urls[@]}"
}

print_urls() {
  echo "embedding_url:"
  for h in "$@"; do
    local host="${h%%:*}" spec="${h#*:}"
    local i=0
    for gpu in $(expand_gpus "$spec"); do
      echo "  - http://$host:$(( BASE_PORT + i ))"; i=$(( i + 1 ))
    done
  done
}

stop_tier() {
  local pat="${1:-}"
  case "$pat" in
    workers) pat="ragstack-oa-" ;;
    vllm)    pat="vllm-" ;;
    "")      ;;
    *) echo "unknown tier '$pat' (workers|vllm)" >&2; return 1 ;;
  esac
  for pid in "$LOG_DIR"/${pat:-*}*.pid; do
    [ -f "$pid" ] || continue
    kill "$(cat "$pid")" 2>/dev/null || true
    rm -f "$pid"
  done
  echo "Stopped ${pat:-everything} (pid files in $LOG_DIR cleared)."
}

status() {
  echo "== GoWe workers (server view) =="
  curl -s "$GOWE_SERVER/api/v1/workers" | \
    python3 -c "import json,sys; ws=json.load(sys.stdin)['data']; \
[print(f\"  {w['name']:<18} {w['group']:<12} {w['state']}\") for w in ws]" \
    2>/dev/null || echo "  (server unreachable)"
  echo "== vLLM replicas (local pid files) =="
  for pid in "$LOG_DIR"/vllm-*.pid; do
    [ -f "$pid" ] || continue
    local name; name=$(basename "$pid" .pid)
    if kill -0 "$(cat "$pid")" 2>/dev/null; then
      echo "  $name running (pid $(cat "$pid"))"
    else
      echo "  $name DEAD (stale pid file)"
    fi
  done
}

case "${1:-}" in
  workers) start_workers "${2:-}" ;;
  vllm)    start_vllm "${2:-}" ;;
  urls)    shift; print_urls "$@" ;;   # e.g. urls lambda13:0-7 coconut:0,3-7
  stop)    stop_tier "${2:-}" ;;
  status)  status ;;
  *) sed -n '2,30p' "$0"; exit 1 ;;
esac
