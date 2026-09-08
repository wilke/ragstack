#!/usr/bin/env bash
# Supervisor for the confirmation-run labeling pass (s0c_label.py). QUARANTINED.
#
# The labeling pass runs for days. This script launches it detached, restarts it if it
# dies (s0c_label.py is checkpointed per (topic, docno, presentation), so a restart resumes
# exactly where it stopped), and keeps a heartbeat so a hung process is visible.
#
#   ./s0c_supervise.sh start  scout|qwen      launch detached; records pids under run/
#   ./s0c_supervise.sh status                 progress + pid liveness (safe, read-only)
#   ./s0c_supervise.sh stop   scout|qwen      stop BY RECORDED PID ONLY
#
# NEVER stop a service on this host by process-name pattern (MEMORY / #402). `stop` reads
# the pid this script wrote, verifies /proc/<pid>/cwd is this worktree's stage0 directory,
# and only then signals it.
#
# Endpoints: mango:8003 (scout) and mango:8004 (qwen), <= 4 in flight each. Nothing else.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT=${WT:-"$(cd "$HERE/../../../.." && pwd)"}
RUN=${RUN:-"$WT/run"}
mkdir -p "$RUN"

export HF_HOME=${HF_HOME:-/rag/cache}
export PYTHONPATH=${PYTHONPATH:-/home/wilke/Development/ragstack/python}
export STAGE0_HELPERS=${STAGE0_HELPERS:-/home/wilke/Development/worktrees/phase0-rescue/phase0}
PY=${PY:-/rag/envs/ragstack/bin/python3}
LABELS=${LABELS:-/rag/tmp/stage0-conf/work/conf/labels}
CONFRUN=${CONFRUN:-/rag/tmp/stage0-conf/work/conf/run}

MAX_ATTEMPTS=${MAX_ATTEMPTS:-2000}
BACKOFF=${BACKOFF:-60}
HEARTBEAT_SECS=${HEARTBEAT_SECS:-900}
POLL_SECS=${POLL_SECS:-15}

supervise () {                       # runs detached; one judge
  local judge=$1
  local log="$RUN/label-$judge.log"
  local suplog="$RUN/label-$judge.supervisor.log"
  local pidf="$RUN/label-$judge.pid"
  local hb="$CONFRUN/heartbeat-$judge.json"
  local attempt=0
  echo "$(date -u +%FT%TZ) supervisor up for $judge" >> "$suplog"
  while [ "$attempt" -lt "$MAX_ATTEMPTS" ]; do
    attempt=$((attempt + 1))
    echo "$(date -u +%FT%TZ) attempt $attempt: $PY s0c_label.py --judge $judge" >> "$suplog"
    ( cd "$HERE" && exec "$PY" s0c_label.py --judge "$judge" ) >> "$log" 2>&1 &
    local child=$!
    echo "$child" > "$pidf"
    # Heartbeat while the child runs: counts and timestamps only, never label content.
    # Liveness is polled every POLL_SECS so a crash is noticed within seconds; the
    # heartbeat file is rewritten every HEARTBEAT_SECS.
    local waited=$HEARTBEAT_SECS
    while kill -0 "$child" 2>/dev/null; do
      if [ "$waited" -ge "$HEARTBEAT_SECS" ]; then
        local n
        n=$(wc -l < "$LABELS/labels-conf-$judge.jsonl" 2>/dev/null || echo 0)
        printf '{"judge":"%s","attempt":%d,"pid":%d,"records":%s,"utc":"%s","state":"running"}\n' \
          "$judge" "$attempt" "$child" "$n" "$(date -u +%FT%TZ)" > "$hb"
        waited=0
      fi
      sleep "$POLL_SECS" &
      wait $! 2>/dev/null
      waited=$((waited + POLL_SECS))
    done
    wait "$child"
    local rc=$?
    local n
    n=$(wc -l < "$LABELS/labels-conf-$judge.jsonl" 2>/dev/null || echo 0)
    echo "$(date -u +%FT%TZ) attempt $attempt exited rc=$rc records=$n" >> "$suplog"
    if [ "$rc" -eq 0 ]; then
      printf '{"judge":"%s","attempt":%d,"records":%s,"utc":"%s","state":"finished"}\n' \
        "$judge" "$attempt" "$n" "$(date -u +%FT%TZ)" > "$hb"
      echo "$(date -u +%FT%TZ) supervisor done for $judge" >> "$suplog"
      return 0
    fi
    printf '{"judge":"%s","attempt":%d,"records":%s,"utc":"%s","state":"restarting","rc":%d}\n' \
      "$judge" "$attempt" "$n" "$(date -u +%FT%TZ)" "$rc" > "$hb"
    sleep "$BACKOFF"
  done
  echo "$(date -u +%FT%TZ) supervisor GAVE UP for $judge after $attempt attempts" >> "$suplog"
  return 1
}

case "${1:-}" in
  start)
    judge=${2:?judge required: scout|qwen}
    sup="$RUN/label-$judge.supervisor.pid"
    if [ -f "$sup" ] && kill -0 "$(cat "$sup")" 2>/dev/null; then
      echo "supervisor for $judge already running: pid $(cat "$sup")"; exit 0
    fi
    nohup setsid "$HERE/s0c_supervise.sh" __supervise "$judge" >> "$RUN/label-$judge.supervisor.log" 2>&1 &
    echo $! > "$sup"
    sleep 2
    echo "supervisor for $judge: pid $(cat "$sup")  log $RUN/label-$judge.log"
    ;;
  __supervise)
    supervise "${2:?}"
    ;;
  status)
    "$PY" "$HERE/s0c_label.py" --status
    for f in "$RUN"/label-*.supervisor.pid "$RUN"/label-*.pid; do
      [ -f "$f" ] || continue
      p=$(cat "$f")
      if kill -0 "$p" 2>/dev/null; then
        echo "$(basename "$f"): pid $p ALIVE cwd=$(readlink -f /proc/"$p"/cwd 2>/dev/null)"
      else
        echo "$(basename "$f"): pid $p not running"
      fi
    done
    for f in "$CONFRUN"/heartbeat-*.json; do [ -f "$f" ] && { echo -n "$(basename "$f"): "; cat "$f"; }; done
    ;;
  stop)
    judge=${2:?judge required: scout|qwen}
    for f in "$RUN/label-$judge.supervisor.pid" "$RUN/label-$judge.pid"; do
      [ -f "$f" ] || continue
      p=$(cat "$f")
      cwd=$(readlink -f /proc/"$p"/cwd 2>/dev/null || true)
      case "$cwd" in
        "$WT"|"$WT"/*)
          echo "stopping $(basename "$f") pid $p (cwd $cwd)"; kill "$p" 2>/dev/null || true ;;
        "")
          echo "$(basename "$f") pid $p is not running" ;;
        *)
          echo "REFUSING to signal pid $p: cwd=$cwd is not this worktree" ;;
      esac
    done
    ;;
  *)
    echo "usage: $0 {start|stop} {scout|qwen} | $0 status" >&2; exit 2 ;;
esac
