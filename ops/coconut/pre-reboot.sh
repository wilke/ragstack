#!/usr/bin/env bash
# ops/coconut/pre-reboot.sh — stop coconut's service stack cleanly before a planned reboot.
#
#   ./pre-reboot.sh [--dry-run] [--all]
#
# Order (reverse of restore.sh, consumers before providers):
#   1. labelers      s0c_supervise.sh stop scout|qwen  (checkpointed; resume after)
#   2. uis           the Vite dev servers on :5210 :5211 :5212 :8090
#   3. apis          the four tenant APIs, by their pid files (cwd verified)
#   4. gowe          workers, then the server, then prometheus/grafana — by pid, cwd verified
#   5. sidecars      apptainer instance stop crossencoder, embedding
#   6. sfr           the six vLLM endpoints, by the pid that owns each port (cmdline verified)
#   7. stores        apptainer instance stop, dependents first; Qdrant/ES/Neo4j/Postgres get a
#                    graceful SIGTERM so their WALs flush before the disk goes away
#
# Never by process-name pattern (MEMORY: #402 took the fleet down that way). Every kill here
# resolves a pid from a pid file, a listening port or an apptainer instance name, then checks
# /proc/<pid>/cwd or cmdline before signalling.
#
# NOT stopped unless --all: personal dev processes (p3-web :3000, VaxpipeApp :5174, codex,
# the docs server :8899, the legacy UIs :5173/:5175) — the reboot will take them anyway; they
# are listed so the owner can decide. The gateway nginx (:9000, svcbvbrc) cannot be stopped
# from this account and does not need to be.
#
# Run snapshot.sh FIRST so verify.sh has a baseline to compare against after the restore.
set -uo pipefail
DRY=0; ALL=0
for a in "$@"; do case $a in --dry-run) DRY=1 ;; --all) ALL=1 ;; -h|--help) sed -n '2,/^set -/p' "$0" | sed '$d'; exit 0 ;; *) echo "unknown arg $a" >&2; exit 2 ;; esac; done
ts() { date -u +%FT%TZ; }
say() { echo "$(ts) $*"; }
port_pid() { ss -ltnpH 2>/dev/null | awk -v p="$1" '$4 ~ "[:.]"p"$"' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2; }
cmd_of() { { tr '\0' ' ' < "/proc/$1/cmdline"; } 2>/dev/null; }
cwd_of() { readlink "/proc/$1/cwd" 2>/dev/null; }
sig() {                       # sig PID label [SIGNAL]
  local p=$1 label=$2 s=${3:-TERM}
  [[ -n $p ]] && kill -0 "$p" 2>/dev/null || { say "  [$label] not running"; return 0; }
  if (( DRY )); then say "  [dry-run] kill -$s $p  ($label: $(cmd_of "$p" | cut -c1-80))"; return 0; fi
  say "  [$label] kill -$s $p"; kill "-$s" "$p" 2>/dev/null || true
}
wait_gone() {                 # wait_gone PID seconds
  local p=$1 n=${2:-30} i=0; (( DRY )) && return 0
  while kill -0 "$p" 2>/dev/null && (( i < n )); do sleep 1; i=$((i+1)); done
  kill -0 "$p" 2>/dev/null && { say "    still alive after ${n}s: $p"; return 1; } || return 0
}
stop_port() {                 # stop_port PORT label expected-cmd-substring [expected-cwd-prefix]
  local port=$1 label=$2 want=$3 cwdp=${4:-}; local p; p=$(port_pid "$port")
  [[ -z $p ]] && { say "  [$label :$port] nothing listening"; return 0; }
  local c; c=$(cmd_of "$p"); local d; d=$(cwd_of "$p")
  [[ $c == *"$want"* ]] || { say "  [$label :$port] REFUSING: pid $p cmdline does not contain '$want' ($c)"; return 1; }
  [[ -z $cwdp || $d == "$cwdp"* ]] || { say "  [$label :$port] REFUSING: pid $p cwd $d is not under $cwdp"; return 1; }
  sig "$p" "$label :$port"; wait_gone "$p" 30
}
stop_instance() {
  apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$1" || { say "  [$1] no such instance"; return 0; }
  if (( DRY )); then say "  [dry-run] apptainer instance stop -s SIGTERM -t 120 $1"; return 0; fi
  # SIGTERM with a real grace period: the default is a SIGKILL after 10 s, which is inside an ES/Postgres flush
  say "  [$1] apptainer instance stop -s SIGTERM -t 120"; apptainer instance stop -s SIGTERM -t 120 "$1" >/dev/null 2>&1 || say "    (stop returned non-zero)"
  local i=0; while apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$1" && (( i < 130 )); do sleep 2; i=$((i+2)); done
  apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$1" && say "    ✗ [$1] still listed after ${i}s" || say "    ✓ [$1] gone"
}

say "== 1. labelers"
S=$HOME/Development/worktrees/confirmation-run/docs/plans/results/stage0
if [[ -x $S/s0c_supervise.sh ]]; then
  for j in scout qwen; do
    if (( DRY )); then say "  [dry-run] $S/s0c_supervise.sh stop $j"; else "$S/s0c_supervise.sh" stop "$j" 2>&1 | sed 's/^/  /'; fi
  done
  (( DRY )) || sleep 3
else say "  supervisor not found ($S)"; fi

say "== 2. tenant UIs"
for spec in "demo 5210 /rag/repos/tenants/demo" "lucid-next 5211 /rag/repos/tenants/lucid-next" "asm-next 5212 /rag/repos/tenants/asm-next" "dev 8090 /rag/repos/tenants/dev"; do
  set -- $spec; stop_port "$2" "ui $1" "vite" "$3"
done

say "== 3. tenant APIs (pid files, cwd verified)"
for spec in "lucid-next lucid 24000" "asm-next asm 24020" "dev dev 24040" "demo demo 24060"; do
  set -- $spec; name=$1 tdir=/rag/data/tenants/$2 port=$3; pf=$tdir/api-$name.pid
  p=$(cat "$pf" 2>/dev/null || true)
  [[ -n $p ]] && kill -0 "$p" 2>/dev/null || p=$(port_pid "$port")
  [[ -z $p ]] && { say "  [api $name] not running"; continue; }
  d=$(cwd_of "$p"); c=$(cmd_of "$p")
  if [[ $c == *"uvicorn ragstack.api.main:app"* && $d == /rag/repos/tenants/$name/python ]]; then sig "$p" "api $name :$port"; wait_gone "$p" 30
  else say "  [api $name] REFUSING pid $p: cwd=$d cmd=$(echo "$c" | cut -c1-60)"; fi
done

say "== 4. GoWe workers → server → monitoring"
G=/scout/Experiments/GoWe
for p in /proc/[0-9]*; do
  pid=${p#/proc/}; c=$(cmd_of "$pid"); [[ $c == ./bin/gowe-worker* ]] || continue
  [[ $(cwd_of "$pid") == "$G" ]] || { say "  skipping worker pid $pid: cwd is not $G"; continue; }
  n=$(echo "$c" | grep -o -- '--name [^ ]*' | cut -d' ' -f2); sig "$pid" "gowe-worker $n"
done
(( DRY )) || sleep 5
for p in /proc/[0-9]*; do
  pid=${p#/proc/}; c=$(cmd_of "$pid"); [[ $c == ./bin/gowe-server* ]] || continue
  [[ $(cwd_of "$pid") == "$G" ]] || continue
  sig "$pid" "gowe-server :8091"; wait_gone "$pid" 30
done
stop_port 9090 prometheus prometheus
stop_port 3001 grafana grafana

say "== 5. sidecars"
stop_instance crossencoder; stop_instance embedding

say "== 6. SFR vLLM fleet"
for port in 9001 9002 9003 9004 9005 9006; do stop_port "$port" "sfr" "vllm serve Salesforce/SFR-Embedding-Mistral" /rag/repos/ragstack/python; done

say "== 7. stores (dependents first, then the shared prod stores)"
for i in qdrant-dev elasticsearch-dev neo4j-dev elasticsearch-lucid qdrant2 elasticsearch qdrant neo4j postgres redis; do stop_instance "$i"; done
if (( ! DRY )); then
  sleep 5; left=$(apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | tr '\n' ' ')
  [[ -z $left ]] && say "  ✓ no apptainer instances left" || say "  ✗ still listed: $left"
fi

say "== not stopped (personal / other-account; the reboot takes them):"
for spec in "3000 p3-web" "5174 VaxpipeApp-vite" "5173 legacy-ui-asm" "5175 legacy-ui-lucid" "8899 docs-http.server"; do
  set -- $spec; p=$(port_pid "$1"); [[ -n $p ]] && say "  :$1 $2 pid $p $(cwd_of "$p")" || true
done
ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE '[:.]9000$' && say "  :9000 gateway nginx (svcbvbrc — another account; cannot and need not be stopped from here)" || true
if (( ALL )); then
  say "== --all: stopping the personal dev processes too"
  stop_port 3000 p3-web p3-web; stop_port 5174 VaxpipeApp vite; stop_port 5173 legacy-ui-asm vite; stop_port 5175 legacy-ui-lucid vite; stop_port 8899 docs http.server
fi
say "== done. remaining listeners owned by $(id -un):"
ss -ltnpH 2>/dev/null | grep -c "users:" | sed 's/^/  /'
