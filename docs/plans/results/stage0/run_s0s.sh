#!/usr/bin/env bash
# Pointed-at-scale -- step 1 (free): reach vs corpus size by subsampling DOWN.
#
# Every stage writes a log and a pid file under `run/` and is polled BY THE RECORDED PID,
# never by process name (MEMORY: a name-pattern kill once took down every API on the host).
# Step 1 makes ZERO embedding calls: it masks the frozen matrices under `emb/`, which it
# opens read-only, and reuses Stage 0b''s frozen query vectors.
#
#   ./run_s0s.sh smoke     # 500 documents, one arm -- the stop-before-scaling check
#   ./run_s0s.sh           # the full step 1
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
mkdir -p run

export HF_HOME=/rag/cache
export PYTHONPATH=/home/wilke/Development/ragstack/python
export STAGE0_HELPERS=/home/wilke/Development/worktrees/phase0-rescue/phase0
PY=/rag/envs/ragstack/bin/python3

SMOKE=""
if [[ "${1:-}" == "smoke" ]]; then SMOKE="--smoke"; fi

stage () {                      # stage <name> <script> [args...]
  local name="$1"; shift
  echo "=== $name  $(date -Is)"
  nohup "$PY" "$@" > "run/$name.log" 2>&1 &
  local pid=$!
  echo "$pid" > "run/$name.pid"
  while kill -0 "$pid" 2>/dev/null; do sleep 10; done
  wait "$pid" || { echo "!! $name FAILED; see run/$name.log"; tail -30 "run/$name.log"; exit 1; }
  tail -3 "run/$name.log"
}

stage s0s_retrieve s0s_retrieve.py $SMOKE
stage s0s_score    s0s_score.py    $SMOKE
if [[ -z "$SMOKE" ]]; then
  stage s0s_fit    s0s_fit.py
  "$PY" s0s_report.py > run/s0s_report.log 2>&1
  echo "tables: artifacts/pointed-scale/TABLES.md"
fi
echo "=== done $(date -Is)"
