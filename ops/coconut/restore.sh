#!/usr/bin/env bash
# ops/coconut/restore.sh — bring coconut's service stack back after a reboot.
#
#   ./restore.sh [--dry-run] [--only GROUP[,GROUP]] [--skip GROUP[,GROUP]] [--proxy]
#
# Groups, in start order (each one waits for its health check before the next):
#   preflight   mounts, sysctl, images, envs, GPUs
#   stores      qdrant :6333, qdrant2 :6343, qdrant-dev :24041,
#               elasticsearch :9200, elasticsearch-lucid :24003, elasticsearch-dev :24043,
#               neo4j-dev :24046/24047, postgres :5432, redis :6379, neo4j :7474 (best effort)
#   sidecars    crossencoder :50052 (GPU 0), embedding :50053 (CPU)
#   sfr         six SFR-Embedding-Mistral vLLM endpoints :9001–:9006 on GPUs 0–5
#   apis        the four tenant APIs :24000 lucid-next, :24020 asm-next, :24040 dev, :24060 demo
#   uis         the four base-aware Vite dev servers :5210 demo, :5211 lucid-next, :5212 asm-next, :8090 dev
#   gowe        gowe-server :8091 + 21 workers via /scout/wf/gowe/start-gowe.sh, then prometheus :9090, grafana :3001
#   labelers    the quarantined confirmation-run labelers (Scout/Qwen) via their supervisor
#   proxy       nginx :9000 — only reports unless --proxy (see below)
#   legacy-ui   :5173 and :5175 (skipped by default; their APIs :8000/:8010 were already down)
#
# Every step is idempotent: a port that already answers or an instance that is
# already listed is skipped, so the script is safe to re-run and safe to run on
# a live system (it will report "already running" for everything).
#
# What it deliberately does NOT do:
#   - stop anything (see pre-reboot.sh)
#   - kill by process-name pattern, ever (MEMORY: #402)
#   - restart the proxy under this account by default. nginx :9000 runs as the
#     service account svcbvbrc and its systemd unit is NOT installed, so after
#     a reboot the gateway is down until either an admin installs
#     /rag/config/proxy/coconut-proxy.service (preferred) or --proxy is passed,
#     which regenerates the self-signed cert under THIS account and starts nginx
#     here (the svcbvbrc-owned key is unreadable to us and gets replaced).
#   - touch mango. Its three endpoints are verified by verify.sh only.
#   - start personal dev processes (p3-web :3000, VaxpipeApp :5174, codex, the
#     docs server on :8899). They are listed in INVENTORY.md for the owner.
#
# Launch commands below reproduce what /proc showed on 2026-09-09 (see
# snapshot.json → listeners_by_process / apptainer_instances), not what the older
# scripts in apptainer/ would do: heap sizes, the optimizer budget and one extra
# bind differ from those scripts. Where a canonical launcher exists and matches
# the live state (sidecars-up.sh, ui-dev.sh, start-gowe.sh, start-monitoring.sh,
# s0c_supervise.sh) it is called instead of re-typed.
set -uo pipefail

RUN=${RUN:-/rag/backups/reboot-2026-09-10}
LOG=$RUN/logs; PIDS=$RUN/pids
mkdir -p "$LOG" "$PIDS"
IMG=/rag/apptainer/images
DATA=/rag/data
DRY=0; ONLY=""; SKIP=""; PROXY=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --dry-run) DRY=1 ;;
    --only) [[ -n ${2:-} ]] || { echo "--only needs a group list" >&2; exit 2; }; ONLY=$2; shift ;;
    --skip) [[ -n ${2:-} ]] || { echo "--skip needs a group list" >&2; exit 2; }; SKIP=$2; shift ;;
    --proxy) PROXY=1 ;;
    -h|--help) sed -n '2,/^set -/p' "$0" | sed '$d'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac; shift
done

ts() { date -u +%FT%TZ; }
say() { echo "$(ts) $*"; }
run() {                       # run "description" cmd...
  local d=$1; shift
  if (( DRY )); then echo "  [dry-run] $d:"; printf '      %q ' "$@"; echo; return 0; fi
  say "  $d"; "$@"
}
want() {                      # want GROUP → 0 if the group should run
  local g=$1
  [[ -n $SKIP ]] && [[ ",$SKIP," == *",$g,"* ]] && return 1
  [[ -n $ONLY ]] && [[ ",$ONLY," != *",$g,"* ]] && return 1
  return 0
}
port_up() { ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"; }
# 2xx/3xx/401/404 all mean "a server is answering" (Vite --base and GoWe answer / with a redirect)
http_ok() { local c; c=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null); [[ $c =~ ^(2[0-9][0-9]|3[0-9][0-9]|401|404)$ ]]; }
wait_http() {                 # wait_http url seconds label
  local url=$1 secs=$2 label=$3 i=0
  (( DRY )) && return 0
  while (( i < secs )); do http_ok "$url" && { say "    ✓ $label answers"; return 0; }; sleep 5; i=$((i+5)); done
  say "    ✗ $label did not answer within ${secs}s ($url)"; return 1
}
instance_up() { apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$1"; }
start_instance() {            # start_instance NAME SIF [apptainer opts...] -- [runscript args...]
  local name=$1 sif=$2; shift 2
  if instance_up "$name"; then say "  [$name] already running — skipping"; return 0; fi
  [[ -f $sif ]] || { say "  [$name] MISSING IMAGE $sif"; return 1; }
  local opts=() args=() mode=opts a
  for a in "$@"; do
    [[ $a == "--" ]] && { mode=args; continue; }
    [[ $mode == opts ]] && opts+=("$a") || args+=("$a")
  done
  STARTED[$name]=1
  run "[$name] apptainer instance run" apptainer instance run "${opts[@]}" "$sif" "$name" "${args[@]}"
}
seed_if_empty() {             # seed_if_empty SIF container_path host_path
  [[ -n "$(ls -A "$3" 2>/dev/null)" ]] && return 0
  run "seed $3 from $1:$2" apptainer exec --bind "$3:/__seed" "$1" sh -c "cp -R $2/. /__seed/"
}
launch() {                    # launch NAME cwd logfile -- cmd...   (detached in its own session)
  # NOTE: $! here would be the wrapper subshell, not the service (review finding 2); the real pid
  # is recorded by record_pid_by_port / record_pid_by_cmd after the health wait.
  local name=$1 cwd=$2 log=$3; shift 3; [[ $1 == "--" ]] && shift
  if (( DRY )); then echo "  [dry-run] $name in $cwd:"; printf '      %q ' "$@"; echo "  >> $log"; return 0; fi
  say "  [$name] launching"; STARTED[$name]=1
  ( cd "$cwd" && exec setsid nohup "$@" >> "$log" 2>&1 < /dev/null ) &
}

declare -A STARTED=()          # name → 1 when THIS run launched it (skipped services get a short wait only)
wait_if_started() {            # wait_if_started NAME url seconds label — full wait only if we started it
  local name=$1 url=$2 secs=$3 label=$4
  [[ -n ${STARTED[$name]:-} ]] || secs=10
  wait_http "$url" "$secs" "$label"
}
port_pid() { ss -ltnpH 2>/dev/null | awk -v p="$1" '$4 ~ "[:.]"p"$"' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2; }
record_pid_by_port() {         # record_pid_by_port NAME PORT [also-write-path] — the pid that OWNS the port, not the launcher shell
  local name=$1 port=$2 also=${3:-}; local p; p=$(port_pid "$port")
  [[ -n $p ]] || { say "    ✗ [$name] nothing owns :$port — pid not recorded"; return 1; }
  echo "$p" > "$PIDS/$name.pid"; [[ -n $also ]] && echo "$p" > "$also"
  say "    pid $p recorded for $name (:$port)"
}
record_pid_by_cmd() {          # record_pid_by_cmd NAME argv0-prefix substring — for services without a port (GoWe workers)
  # The prefix match on argv[0] is what keeps this from matching a shell whose command text merely
  # CONTAINS the substring (an operator's `bash -c '… restore.sh …'`, or this script itself).
  local name=$1 prefix=$2 want=$3 pid c
  for pr in /proc/[0-9]*; do pid=${pr#/proc/}; c=$({ tr '\0' ' ' < "$pr/cmdline"; } 2>/dev/null); [[ $c == "$prefix"* && $c == *"$want"* ]] && { echo "$pid" > "$PIDS/$name.pid"; return 0; }; done
  say "    ✗ [$name] no process '$prefix…$want' — pid not recorded"; return 1
}

fail=0
# ---------------------------------------------------------------- preflight
if want preflight; then
  say "== preflight"
  for m in /rag /scout; do mountpoint -q $m && say "  ✓ $m mounted" || { say "  ✗ $m NOT mounted — stop here"; exit 1; }; done
  [[ -d $HOME ]] && say "  ✓ \$HOME reachable (autofs)" || { say "  ✗ \$HOME not mounted"; exit 1; }
  mmc=$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)
  if (( mmc >= 262144 )); then say "  ✓ vm.max_map_count=$mmc"; else
    say "  ✗ vm.max_map_count=$mmc (<262144): Elasticsearch WILL NOT START."
    say "    an admin must run: sudo sysctl -w vm.max_map_count=262144  (and persist it in /etc/sysctl.d/)"
    fail=1
  fi
  for s in qdrant elasticsearch neo4j postgres redis python nginx; do [[ -f $IMG/$s.sif ]] && say "  ✓ $s.sif" || { say "  ✗ missing $IMG/$s.sif"; fail=1; }; done
  for e in /rag/envs/ragstack/bin/python /rag/envs/vllm/bin/vllm; do [[ -x $e ]] && say "  ✓ $e" || { say "  ✗ missing $e"; fail=1; }; done
  if nvidia-smi -L >/dev/null 2>&1; then say "  ✓ $(nvidia-smi -L | wc -l) GPUs visible"; else say "  ✗ nvidia-smi failed — sfr/crossencoder cannot start"; fail=1; fi
  # node/npx live only in $HOME (nvm / ~/.local) — run from a LOGIN shell (bash -l) so PATH has them
  if command -v npx >/dev/null 2>&1; then say "  ✓ npx: $(command -v npx)"; else say "  ✗ npx not on PATH — the uis group will fail; run from a login shell: bash -l -c '$0 …'"; fail=1; fi
  WT=$HOME/Development/worktrees/confirmation-run/docs/plans/results/stage0
  [[ -x $WT/s0c_supervise.sh ]] && say "  ✓ labeler worktree present" || say "  ! labeler worktree missing ($WT) — labelers group will be skipped"
  http_ok http://mango:8004/v1/models && say "  ✓ mango:8004 (Qwen) answers" || say "  ! mango:8004 not answering yet — labelers group will wait for it"
  http_ok http://mango:8003/v1/models && say "  ✓ mango:8003 (Scout) answers" || say "  ! mango:8003 not answering yet (tenant LLM_ENDPOINT)"
  (( fail )) && { say "preflight FAILED — fix the items above, then re-run"; (( DRY )) || exit 1; }
fi

# ---------------------------------------------------------------- stores
if want stores; then
  say "== stores"
  start_instance qdrant "$IMG/qdrant.sif" \
    --bind "$DATA/qdrant/storage:/qdrant/storage" --bind "$DATA/qdrant/snapshots:/qdrant/snapshots" \
    --bind "/rag/cache/load3corpus:/rag/cache/load3corpus" \
    --env QDRANT__STORAGE__PERFORMANCE__OPTIMIZER_CPU_BUDGET=12 \
    --env QDRANT__STORAGE__OPTIMIZERS__MAX_OPTIMIZATION_THREADS=1 \
    -- /bin/sh -c 'cd /qdrant && exec ./entrypoint.sh'
  start_instance qdrant2 "$IMG/qdrant.sif" \
    --bind "$DATA/qdrant2/storage:/qdrant/storage" --bind "$DATA/qdrant2/snapshots:/qdrant/snapshots" \
    --env QDRANT__SERVICE__HTTP_PORT=6343 --env QDRANT__SERVICE__GRPC_PORT=6344 \
    --env QDRANT__STORAGE__PERFORMANCE__OPTIMIZER_CPU_BUDGET=12 \
    --env QDRANT__STORAGE__OPTIMIZERS__MAX_OPTIMIZATION_THREADS=1 \
    -- /bin/sh -c 'cd /qdrant && exec ./entrypoint.sh'
  start_instance qdrant-dev "$IMG/qdrant.sif" \
    --bind "$DATA/tenants/dev/qdrant/storage:/qdrant/storage" --bind "$DATA/tenants/dev/qdrant/snapshots:/qdrant/snapshots" \
    --env QDRANT__SERVICE__HTTP_PORT=24041 --env QDRANT__SERVICE__GRPC_PORT=24042 \
    -- /bin/sh -c 'cd /qdrant && exec ./entrypoint.sh'

  # ES: dotted settings as native -E args (apptainer --env shell-sources and mangles them);
  # call docker-entrypoint.sh directly (tini eats -E). Heaps are the LIVE values, not up.sh's.
  seed_if_empty "$IMG/elasticsearch.sif" /usr/share/elasticsearch/config "$DATA/elasticsearch/config"
  start_instance elasticsearch "$IMG/elasticsearch.sif" \
    --bind "$DATA/elasticsearch/data:/usr/share/elasticsearch/data" --bind "$DATA/elasticsearch/logs:/usr/share/elasticsearch/logs" \
    --bind "$DATA/elasticsearch/config:/usr/share/elasticsearch/config" --env ES_JAVA_OPTS="-Xms1g -Xmx1g" \
    -- /usr/local/bin/docker-entrypoint.sh eswrapper -Ediscovery.type=single-node -Expack.security.enabled=false
  seed_if_empty "$IMG/elasticsearch.sif" /usr/share/elasticsearch/config "$DATA/tenants/lucid/elasticsearch/config"
  start_instance elasticsearch-lucid "$IMG/elasticsearch.sif" \
    --bind "$DATA/tenants/lucid/elasticsearch/data:/usr/share/elasticsearch/data" --bind "$DATA/tenants/lucid/elasticsearch/logs:/usr/share/elasticsearch/logs" \
    --bind "$DATA/tenants/lucid/elasticsearch/config:/usr/share/elasticsearch/config" --env ES_JAVA_OPTS="-Xms2g -Xmx2g" \
    -- /usr/local/bin/docker-entrypoint.sh eswrapper -Ediscovery.type=single-node -Expack.security.enabled=false -Ehttp.port=24003 -Etransport.port=24004
  seed_if_empty "$IMG/elasticsearch.sif" /usr/share/elasticsearch/config "$DATA/tenants/dev/elasticsearch/config"
  start_instance elasticsearch-dev "$IMG/elasticsearch.sif" \
    --bind "$DATA/tenants/dev/elasticsearch/data:/usr/share/elasticsearch/data" --bind "$DATA/tenants/dev/elasticsearch/logs:/usr/share/elasticsearch/logs" \
    --bind "$DATA/tenants/dev/elasticsearch/config:/usr/share/elasticsearch/config" --env ES_JAVA_OPTS="-Xms1g -Xmx1g" \
    -- /usr/local/bin/docker-entrypoint.sh eswrapper -Ediscovery.type=single-node -Expack.security.enabled=false -Ehttp.port=24043 -Etransport.port=24044

  # neo4j-dev: password comes from dev's secrets.env (never printed). Binds as live: /data, /logs, conf.
  if ! instance_up neo4j-dev; then
    NEOPW=$(grep -E '^NEO4J_PASSWORD=' "$DATA/tenants/dev/config/secrets.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'")
    if [[ -n $NEOPW ]]; then
      start_instance neo4j-dev "$IMG/neo4j.sif" \
        --bind "$DATA/tenants/dev/neo4j/data:/data" --bind "$DATA/tenants/dev/neo4j/logs:/logs" --bind "$DATA/tenants/dev/neo4j/conf:/var/lib/neo4j/conf" \
        --env NEO4J_AUTH="neo4j/$NEOPW" \
        --env NEO4J_server_bolt_listen__address=0.0.0.0:24047 --env NEO4J_server_http_listen__address=0.0.0.0:24046 \
        --
    else say "  [neo4j-dev] NEO4J_PASSWORD not found in dev secrets.env — skipping"; fi
    unset NEOPW
  else say "  [neo4j-dev] already running — skipping"; fi

  start_instance postgres "$IMG/postgres.sif" \
    --bind "$DATA/postgres/data:/var/lib/postgresql/data" --bind "$DATA/postgres/run:/run/postgresql" \
    --env POSTGRES_USER=ragstack --env POSTGRES_PASSWORD=ragstack --env POSTGRES_DB=ragstack --env PGDATA=/var/lib/postgresql/data/pgdata --
  start_instance redis "$IMG/redis.sif" --bind "$DATA/redis/data:/data" --

  # neo4j (shared, prod): its instance existed on 2026-09-09 but the JVM inside had been dead
  # since 2026-06-04 (no :7474/:7687 listener). Every tenant has GRAPH_BACKEND=disabled, so this
  # is best effort: started, reported, never fatal.
  seed_if_empty "$IMG/neo4j.sif" /var/lib/neo4j/conf "$DATA/neo4j/conf"
  start_instance neo4j "$IMG/neo4j.sif" \
    --bind "$DATA/neo4j/data:/data" --bind "$DATA/neo4j/logs:/logs" --bind "$DATA/neo4j/conf:/var/lib/neo4j/conf" \
    --env NEO4J_AUTH="neo4j/$(grep -E '^export NEO4J_PASSWORD=' /rag/config/rag.env | head -1 | cut -d= -f2)" -- || true

  wait_if_started qdrant http://127.0.0.1:6333/collections 120 "qdrant :6333" || fail=1
  wait_if_started qdrant2 http://127.0.0.1:6343/collections 120 "qdrant2 :6343" || fail=1
  wait_if_started qdrant-dev http://127.0.0.1:24041/collections 120 "qdrant-dev :24041" || fail=1
  wait_if_started elasticsearch http://127.0.0.1:9200/ 180 "elasticsearch :9200" || fail=1
  wait_if_started elasticsearch-lucid http://127.0.0.1:24003/ 180 "elasticsearch-lucid :24003" || fail=1
  wait_if_started elasticsearch-dev http://127.0.0.1:24043/ 180 "elasticsearch-dev :24043" || fail=1
  wait_if_started neo4j-dev http://127.0.0.1:24046/ 120 "neo4j-dev :24046" || say "    (neo4j-dev: graph leg is disabled on every tenant; not fatal)"
  if [[ -n ${STARTED[neo4j]:-} ]]; then wait_http http://127.0.0.1:7474/ 60 "neo4j :7474" || say "    (neo4j: was already dead before the reboot; not fatal)"; else say "    (neo4j: not started by this run — skipped; it has been dead since 2026-06-04)"; fi
  (( DRY )) || { apptainer exec "$IMG/redis.sif" redis-cli -p 6379 ping 2>/dev/null | grep -q PONG && say "    ✓ redis PONG" || say "    ✗ redis"; }
  (( DRY )) || { apptainer exec "$IMG/postgres.sif" pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1 && say "    ✓ postgres ready" || say "    ✗ postgres"; }
fi

# ---------------------------------------------------------------- sidecars
if want sidecars; then
  say "== sidecars (crossencoder GPU 0 first — SFR :9001 takes 90 % of that card afterwards)"
  if (( DRY )); then echo "  [dry-run] RAG_DATA=$DATA RAG_IMAGES=$IMG CROSSENCODER_GPU=0 /rag/repos/ragstack/apptainer/sidecars-up.sh"; else
    RAG_DATA=$DATA RAG_IMAGES=$IMG CROSSENCODER_GPU=0 CROSSENCODER_DEVICE=cuda /rag/repos/ragstack/apptainer/sidecars-up.sh 2>&1 | sed 's/^/  /'
  fi
  wait_http http://127.0.0.1:50052/health 300 "crossencoder :50052" || fail=1
  wait_http http://127.0.0.1:50053/health 300 "embedding :50053" || fail=1
fi

# ---------------------------------------------------------------- sfr
if want sfr; then
  say "== SFR-Embedding-Mistral fleet (:9001–:9006 on GPUs 0–5; GPUs 6–7 stay free by convention)"
  for i in 0 1 2 3 4 5; do
    port=$((9001 + i))
    if port_up "$port"; then say "  [sfr $port] already listening — skipping"; continue; fi
    launch "sfr-$port" /rag/repos/ragstack/python "$LOG/sfr-$port.log" -- \
      env CUDA_VISIBLE_DEVICES=$i HF_HOME=/rag/cache VLLM_CACHE_ROOT=/rag/cache/tmp \
      /rag/envs/vllm/bin/vllm serve Salesforce/SFR-Embedding-Mistral --runner pooling --port "$port" \
      --gpu-memory-utilization 0.9 --served-model-name Salesforce/SFR-Embedding-Mistral
  done
  for i in 0 1 2 3 4 5; do
    port=$((9001+i))
    wait_if_started "sfr-$port" "http://127.0.0.1:$port/v1/models" 600 "sfr :$port" || { fail=1; continue; }
    [[ -n ${STARTED[sfr-$port]:-} ]] && (( ! DRY )) && record_pid_by_port "sfr-$port" "$port"
  done
fi

# ---------------------------------------------------------------- apis
if want apis; then
  say "== tenant APIs"
  # name  data-dir  port
  for spec in "lucid-next lucid 24000" "asm-next asm 24020" "dev dev 24040" "demo demo 24060"; do
    set -- $spec; name=$1 tdir=$DATA/tenants/$2 port=$3
    if port_up "$port"; then say "  [api $name :$port] already listening — skipping"; continue; fi
    code=/rag/repos/tenants/$name/python
    [[ -f $tdir/config/tenant.env ]] || { say "  [api $name] missing $tdir/config/tenant.env"; fail=1; continue; }
    if (( DRY )); then echo "  [dry-run] api $name: (set -a; . $tdir/config/tenant.env; [ -f $tdir/config/secrets.env ] && . $tdir/config/secrets.env; set +a; cd $code; HF_HOME=/rag/cache PYTHONPATH=$code nohup /rag/envs/ragstack/bin/python -m uvicorn ragstack.api.main:app --host 0.0.0.0 --port $port >> $tdir/logs/api-$name.log) ; pid → $tdir/api-$name.pid"; continue; fi
    say "  [api $name :$port] launching from $code"
    STARTED[api-$name]=1
    ( set -a; . "$tdir/config/tenant.env"; [[ -f $tdir/config/secrets.env ]] && . "$tdir/config/secrets.env"; set +a
      export HF_HOME=/rag/cache PYTHONPATH=$code
      cd "$code" && exec setsid nohup /rag/envs/ragstack/bin/python -m uvicorn ragstack.api.main:app --host 0.0.0.0 --port "$port" \
        >> "$tdir/logs/api-$name.log" 2>&1 < /dev/null ) &
  done
  for spec in "lucid-next lucid 24000" "asm-next asm 24020" "dev dev 24040" "demo demo 24060"; do
    set -- $spec; name=$1 tdir=$DATA/tenants/$2 port=$3
    wait_if_started "api-$name" "http://127.0.0.1:$port/health" 180 "api $name :$port" || { fail=1; continue; }
    # the pid file is the handle pre-reboot.sh and the restart recipes use — it must hold the uvicorn pid, not a shell
    [[ -n ${STARTED[api-$name]:-} ]] && (( ! DRY )) && record_pid_by_port "api-$name" "$port" "$tdir/api-$name.pid"
  done
fi

# ---------------------------------------------------------------- uis
if want uis; then
  say "== tenant UIs (base-aware Vite dev servers, via /rag/config/proxy/ui-dev.sh)"
  for spec in "demo 5210" "lucid-next 5211" "asm-next 5212" "dev 8090"; do
    set -- $spec; t=$1 port=$2; dir=/rag/repos/tenants/$t/frontend
    if port_up "$port"; then say "  [ui $t :$port] already listening — skipping"; continue; fi
    [[ -d $dir/node_modules ]] || say "  [ui $t] WARNING: $dir/node_modules missing — run npm install there first"
    STARTED[ui-$t]=1
    run "[ui $t :$port]" /rag/config/proxy/ui-dev.sh "$t" "$port" "$dir"
  done
  for spec in "demo 5210" "lucid-next 5211" "asm-next 5212" "dev 8090"; do
    set -- $spec; wait_if_started "ui-$1" "http://127.0.0.1:$2/" 120 "ui $1 :$2" || { fail=1; continue; }
    [[ -n ${STARTED[ui-$1]:-} ]] && (( ! DRY )) && record_pid_by_port "ui-$1" "$2"
  done
fi

# ---------------------------------------------------------------- gowe
if want gowe; then
  say "== GoWe (server :8091 + 21 workers, then monitoring) — via the fleet's own launcher"
  # GoWe was redeployed to v0.19.0 on 2026-09-09 (base path, worker keys). Its operator notes
  # (/scout/wf/gowe/README.md) define the post-reboot procedure and ship an idempotent launcher
  # that reads the worker key from its 0600 file; this script defers to it rather than carrying a
  # second copy of 22 command lines that would drift on the next release swap.
  G=/scout/Experiments/GoWe; W=/scout/wf/gowe
  if [[ -x $W/start-gowe.sh ]]; then
    if (( DRY )); then echo "  [dry-run] $W/start-gowe.sh"; else
      [[ $(ss -ltnH 2>/dev/null | awk '{print $4}' | grep -cE '[:.]8091$') -eq 0 ]] && STARTED[gowe-server]=1
      "$W/start-gowe.sh" 2>&1 | sed 's/^/  /'
    fi
  else say "  ✗ $W/start-gowe.sh missing — GoWe not started (see $W/README.md)"; fail=1; fi
  wait_if_started gowe-server http://127.0.0.1:8091/api/v1/health 60 "gowe-server :8091" && { (( DRY )) || record_pid_by_port gowe-server 8091; } || fail=1
  if (( ! DRY )); then
    sleep 3; n=0
    # one /proc pass: count the workers and record each one's pid under its --name
    for pr in /proc/[0-9]*; do
      c=$({ tr '\0' ' ' < "$pr/cmdline"; } 2>/dev/null); [[ $c == ./bin/gowe-worker* ]] || continue
      n=$((n+1)); wn=$(echo "$c" | grep -o -- '--name [^ ]*' | cut -d' ' -f2); [[ -n $wn ]] && echo "${pr#/proc/}" > "$PIDS/$wn.pid"
    done
    say "    $n gowe-worker processes running (expected 21); pids recorded under $PIDS"; (( n == 21 )) || fail=1
  fi
  if (( DRY )); then echo "  [dry-run] $W/start-monitoring.sh"; else "$W/start-monitoring.sh" 2>&1 | sed 's/^/  /'; fi
  wait_http http://127.0.0.1:9090/-/ready 120 "prometheus :9090" || say "    (monitoring only)"
  wait_http http://127.0.0.1:3001/api/health 120 "grafana :3001" || say "    (monitoring only)"
fi

# ---------------------------------------------------------------- labelers
if want labelers; then
  say "== confirmation-run labelers (quarantined; checkpointed per record — restart resumes)"
  S=$HOME/Development/worktrees/confirmation-run/docs/plans/results/stage0
  if ! http_ok http://mango:8004/v1/models; then
    say "  ✗ mango:8004 (Qwen) is not answering — NOT starting the labelers (the supervisor would retry every 60 s all night)."
    say "    when mango is back: $0 --only labelers"; fail=1
  elif [[ -x $S/s0c_supervise.sh ]]; then
    for j in scout qwen; do
      hb=/rag/tmp/stage0-conf/work/conf/run/heartbeat-$j.json
      if [[ -f $hb ]] && grep -q '"state":"finished"' "$hb"; then say "  [$j] finished before the reboot — nothing to resume"; continue; fi
      if (( DRY )); then echo "  [dry-run] $S/s0c_supervise.sh start $j"; else "$S/s0c_supervise.sh" start "$j" 2>&1 | sed 's/^/  /'; fi
    done
  else say "  supervisor not found at $S — worktree gone?"; fi
fi

# ---------------------------------------------------------------- proxy
if want proxy; then
  # Whether the unit owns the gateway is a question with an answer on the host,
  # so ask it rather than printing a hand-rolled `sudo cp` recipe that would
  # overwrite whatever ops/ansible installed. `is-enabled` prints enabled /
  # disabled / static / masked and exits non-zero when the unit is unknown.
  proxy_unit_state="$(systemctl is-enabled coconut-proxy 2>/dev/null || true)"
  say "== gateway nginx :9000 (runs as svcbvbrc; unit: ${proxy_unit_state:-not installed})"
  if port_up 9000; then say "  ✓ :9000 already up"; elif (( PROXY )); then
    say "  starting the proxy under $(id -un): regenerating the self-signed cert (the svcbvbrc key is unreadable here)"
    run "proxy cert force" /rag/config/proxy/proxy.sh cert force
    run "proxy start" /rag/config/proxy/proxy.sh start
    wait_http "http://127.0.0.1:9000/ragstack/dev/api/v1/collections?counts=false" 60 "gateway :9000" || fail=1
  elif [[ $proxy_unit_state == enabled ]]; then
    say "  ✗ :9000 is DOWN, but coconut-proxy.service is installed and ENABLED — the unit owns this gateway."
    say "     a) admin: sudo systemctl start coconut-proxy"
    say "        then:  systemctl status coconut-proxy  /  journalctl -u coconut-proxy -n 50"
    say "     Do NOT start it under this account as well (--proxy) while the unit is enabled: two masters on :9000."
    fail=1
  else
    say "  ✗ :9000 is DOWN. Options:"
    say "     a) admin: install/enable the unit from ops/ansible (it owns this file now — a hand `cp` would be overwritten):"
    say "          cd ~/Development/ragstack/ops/ansible && ./check.sh root -K     # preview"
    say "          ansible-playbook -i inventory/coconut.yml site.yml --tags root -K -e proxy_switch_now=true"
    say "        (proxy_switch_now also removes svcbvbrc's @reboot start-proxy.sh crontab line, so boot start has one owner)"
    say "     b) re-run: $0 --only proxy --proxy   (starts it under this account with a fresh self-signed cert)"
    fail=1
  fi
fi

# ---------------------------------------------------------------- legacy-ui (off by default)
if [[ -n $ONLY ]] && want legacy-ui; then
  say "== legacy UIs (their APIs :8000/:8010 were already down on 2026-09-09; the gateway answers 502 for asm/lucid)"
  port_up 5173 || launch ui-asm-legacy /rag/repos/ragstack/frontend "$LOG/ui-5173.log" -- env VITE_API_TARGET=http://localhost:8010 npx vite --host 0.0.0.0
  port_up 5175 || launch ui-lucid-legacy "$HOME/Development/ragstack/frontend" "$LOG/ui-5175.log" -- env VITE_API_TARGET=http://localhost:8020 npx vite --port 5175 --strictPort --host
fi

say "== done. now run: $(dirname "$0")/verify.sh $RUN"
(( fail )) && { say "   one or more groups did not come up cleanly — see the ✗ lines above"; exit 1; }
exit 0
