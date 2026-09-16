#!/usr/bin/env bash
# ops/coconut/pre-reboot.sh — stop coconut's service stack cleanly before a planned reboot.
#
#   ./pre-reboot.sh [--dry-run] [--all]
#   ./pre-reboot.sh --tenant NAME [--dry-run]   one tenant, from its registry row
#
# Order (reverse of restore.sh, consumers before providers):
#   1. labelers      s0c_supervise.sh stop scout|qwen  (checkpointed; resume after)
#   2. uis           the Vite dev servers on :5210 :5211 :5212 :8090 (hackathon's UI is a
#                    static build served by nginx — nothing of ours to stop)
#   3. apis          the five tenant APIs, by their pid files (cwd verified)
#   4. gowe          workers, then the server, then prometheus/grafana — by pid, cwd verified
#   5. sidecars      apptainer instance stop crossencoder, embedding
#   6. sfr           the six vLLM endpoints, by the pid that owns each port (cmdline verified)
#   7. stores        apptainer instance stop, dependents first (hackathon's three dedicated
#                    stores lead, then the dev/lucid ones, then the shared prod stores);
#                    Qdrant/ES/Neo4j/Postgres get a graceful SIGTERM so their WALs flush
#                    before the disk goes away
#
# TENANTS COME FROM THE REGISTRY, exactly as in restore.sh: /rag/data/tenants/registry.json,
# read with jq, is what says which tenants exist, which UI each has and where its pidfile is.
# There is no literal tenant list here any more, so the two scripts cannot drift apart.
#
# A row whose `owner` is `svcbvbrc` is SKIPPED: the control plane owns that tenant, and this
# account cannot signal its processes anyway (/proc/<pid>/cwd is unreadable across accounts).
# Stop it with `ragstack-ctl fleet stop --all` — or `ragstack-ctl tenant stop <n>` — as the
# service account, before or after this script.
#
# `--tenant NAME` stops exactly one tenant — its UI, its API, its own store instances — and
# nothing else. It is the rollback half of a handover and the quickest way to take one
# tenant down by hand.
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
DRY=0; ALL=0; TENANT=""
DATA=${DATA:-/rag/data}
REGISTRY=${REGISTRY:-$DATA/tenants/registry.json}
JQ=${JQ:-/usr/bin/jq}
while [[ $# -gt 0 ]]; do
  case $1 in
    --dry-run) DRY=1 ;;
    --all) ALL=1 ;;
    --tenant) [[ -n ${2:-} ]] || { echo "--tenant needs a name" >&2; exit 2; }; TENANT=$2; shift ;;
    -h|--help) sed -n '2,/^set -/p' "$0" | sed '$d'; exit 0 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac; shift
done
[[ -z $TENANT || $TENANT =~ ^[a-z][a-z0-9-]{0,31}$ ]] || { echo "--tenant: $TENANT is not a tenant name" >&2; exit 2; }
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
# stop_tenant_ui NAME — the tenant's Vite server, by port AND identity.
#
# Which tenants have one is the registry's answer: ui.mode `dev` or `external`
# is a Vite server this account runs, `static` is a directory nginx serves.
stop_tenant_ui() {
  local name=$1 mode port wt
  mode=$(reg_get "$name" '.ui.mode'); port=$(reg_get "$name" '.ui.port'); wt=$(reg_get "$name" '.worktree')
  case $mode in
    dev|external)
      [[ -n $port ]] || { say "  [ui $name] ui.mode=$mode with no port in the registry — nothing to stop"; return 0; }
      stop_port "$port" "ui $name" "vite" "$wt" ;;
    static) say "  [ui $name] static build served by nginx — no process to stop" ;;
    *)      say "  [ui $name] ui.mode=${mode:-unrecorded} — nothing to stop" ;;
  esac
}

# stop_tenant_api NAME — by pidfile first, port second, identity always.
stop_tenant_api() {
  local name=$1 tdir port pf code p d c
  tdir=$(reg_get "$name" '.data_dir'); port=$(reg_get "$name" '.ports.api')
  pf=$(reg_get "$name" '.api.pidfile'); [[ -n $pf ]] || pf=$tdir/api-$name.pid
  code=$(reg_get "$name" '.worktree')/python
  [[ -n $port ]] || { say "  [api $name] the registry row has no api port — nothing to stop"; return 0; }
  p=$(cat "$pf" 2>/dev/null || true)
  [[ -n $p ]] && kill -0 "$p" 2>/dev/null || p=$(port_pid "$port")
  [[ -z $p ]] && { say "  [api $name] not running"; return 0; }
  d=$(cwd_of "$p"); c=$(cmd_of "$p")
  if [[ $c == *"uvicorn ragstack.api.main:app"* && $d == "$code" ]]; then
    sig "$p" "api $name :$port"; wait_gone "$p" 30
  else
    say "  [api $name] REFUSING pid $p: cwd=$d cmd=$(echo "$c" | cut -c1-60)"
  fi
}

stop_instance() {
  apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$1" || { say "  [$1] no such instance"; return 0; }
  if (( DRY )); then say "  [dry-run] apptainer instance stop -s SIGTERM -t 120 $1"; return 0; fi
  # SIGTERM with a real grace period: the default is a SIGKILL after 10 s, which is inside an ES/Postgres flush
  say "  [$1] apptainer instance stop -s SIGTERM -t 120"; apptainer instance stop -s SIGTERM -t 120 "$1" >/dev/null 2>&1 || say "    (stop returned non-zero)"
  local i=0; while apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$1" && (( i < 130 )); do sleep 2; i=$((i+2)); done
  apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$1" && say "    ✗ [$1] still listed after ${i}s" || say "    ✓ [$1] gone"
}

# ---------------------------------------------------------------- the registry
#
# The same four helpers restore.sh has, for the same reason: no tenant name may
# be spelled in this file. reg_rows is SILENT (its output is captured); the
# skips are announced once, by reg_announce.
reg_ready() {
  [[ -r $REGISTRY ]] || { say "  ✗ $REGISTRY is not readable — no tenant can be stopped from it"; return 1; }
  [[ -x $JQ ]] || { say "  ✗ $JQ is missing — the tenant lists are read with jq"; return 1; }
  "$JQ" -e . "$REGISTRY" >/dev/null 2>&1 || { say "  ✗ $REGISTRY is not valid JSON"; return 1; }
  return 0
}
reg_get() { "$JQ" -r --arg n "$1" ".tenants[\$n]$2 // empty" "$REGISTRY" 2>/dev/null; }
reg_names() { "$JQ" -r '(.display_order // []) + ((.tenants|keys) - (.display_order // [])) | .[]' "$REGISTRY"; }
reg_rows() {
  local name
  for name in $(reg_names); do
    [[ -n $TENANT && $name != "$TENANT" ]] && continue
    "$JQ" -e --arg n "$name" '.tenants[$n]' "$REGISTRY" >/dev/null 2>&1 || continue
    [[ $(reg_get "$name" '.owner') == svcbvbrc ]] && continue
    echo "$name"
  done
}
reg_announce() {
  local name
  reg_ready || return 0
  for name in $(reg_names); do
    [[ -n $TENANT && $name != "$TENANT" ]] && continue
    [[ $(reg_get "$name" '.owner') == svcbvbrc ]] || continue
    say "  [$name] owned by the control plane: this account cannot signal its processes."
    say "        stop it as the service account: /rag/bin/ctl-as-svc.sh ragstack-ctl tenant stop $name"
  done
}
# The tenant's own exclusive store instances, "<kind> <instance>" per line.
tenant_store_instances() {
  local name=$1 inst
  if [[ $(reg_get "$name" '.stores.postgres.kind') == local ]]; then
    inst=$(reg_get "$name" '.stores.postgres.instance'); [[ -n $inst ]] && echo "postgres $inst"
  fi
  if [[ $(reg_get "$name" '.stores.elasticsearch.ownership') == exclusive ]]; then
    inst=$(reg_get "$name" '.stores.elasticsearch.instance'); [[ -n $inst ]] && echo "elasticsearch $inst"
  fi
  if [[ $(reg_get "$name" '.stores.qdrant.ownership') == exclusive ]]; then
    inst=$(reg_get "$name" '.stores.qdrant.instance'); [[ -n $inst ]] && echo "qdrant $inst"
  fi
}

reg_announce
if [[ -n $TENANT ]]; then
  # One tenant: its UI, its API, its own stores. Nothing shared, nothing else's.
  rows=$(reg_ready && reg_rows)
  [[ -n $rows ]] || { say "no usable registry row for '$TENANT' in $REGISTRY"; exit 1; }
  say "== $TENANT UI"
  stop_tenant_ui "$TENANT"
  say "== $TENANT API"
  stop_tenant_api "$TENANT"
  say "== $TENANT stores (its own instances only)"
  while read -r kind inst; do
    [[ -n $inst ]] && stop_instance "$inst"
  done <<< "$(tenant_store_instances "$TENANT")"
  say "== done ($TENANT). The shared stores, the sidecars, GoWe and every other tenant are untouched."
  exit 0
fi

say "== 1. labelers"
S=$HOME/Development/worktrees/confirmation-run/docs/plans/results/stage0
if [[ -x $S/s0c_supervise.sh ]]; then
  for j in scout qwen; do
    if (( DRY )); then say "  [dry-run] $S/s0c_supervise.sh stop $j"; else "$S/s0c_supervise.sh" stop "$j" 2>&1 | sed 's/^/  /'; fi
  done
  (( DRY )) || sleep 3
else say "  supervisor not found ($S)"; fi

say "== 2. tenant UIs"
# ui.mode `static` is a build nginx serves from a directory: there is no process
# of ours to stop, and hunting for a vite that does not exist would report a
# failure where there is none.
if reg_ready; then for t in $(reg_rows); do stop_tenant_ui "$t"; done; else say "  ✗ no registry: no tenant UI was stopped"; fi

say "== 3. tenant APIs (pid files, cwd verified)"
if reg_ready; then for t in $(reg_rows); do stop_tenant_api "$t"; done; else say "  ✗ no registry: no tenant API was stopped"; fi

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

say "== 7. stores (every tenant's own instances first, then the shared prod stores)"
# The per-tenant instances lead, in REVERSE display order: nothing else depends on
# them, and the "no apptainer instances left" check below is only truthful once they
# are stopped too. Their names come from the registry (stores.*.instance), never from
# a list here — which is what keeps this in step with restore.sh's half.
#
# stop_instance rather than each tenant's own bin/down.sh: down.sh takes apptainer's
# default 10 s grace, which lands a SIGKILL inside an ES or Postgres flush, and the
# names it would stop are exactly these.
if reg_ready; then
  # Reverse of the start order, so a tenant's postgres outlives nothing that needs it.
  tac_rows=$(reg_rows | tac)
  for t in $tac_rows; do
    while read -r kind inst; do
      [[ -n $inst ]] && stop_instance "$inst"
    done <<< "$(tenant_store_instances "$t")"
  done
else
  say "  ✗ no registry: no per-tenant store instance was stopped — the check below will list them"
fi
# neo4j-dev is dev's graph store and is NOT in the registry (no tenant row records a
# neo4j instance; every tenant has GRAPH_BACKEND=disabled). It stays literal until
# something records it.
for i in neo4j-dev qdrant2 elasticsearch qdrant neo4j postgres redis; do stop_instance "$i"; done
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
