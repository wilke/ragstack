#!/usr/bin/env bash
# ops/coconut/restore.sh — bring coconut's service stack back after a reboot.
#
#   ./restore.sh [--dry-run] [--only GROUP[,GROUP]] [--skip GROUP[,GROUP]] [--proxy]
#   ./restore.sh --tenant NAME [--dry-run]      one tenant, from its registry row
#
# Groups, in start order (each one waits for its health check before the next):
#   preflight   mounts, sysctl, images, envs, GPUs
#   stores      the SHARED ones — qdrant :6333, qdrant2 :6343, elasticsearch :9200,
#               neo4j-dev :24046/24047, postgres :5432, redis :6379, neo4j :7474 (best effort) —
#               and then every store a REGISTRY tenant owns outright (its instance name,
#               SIF and ports come from the row; the tenant's own bin/up.sh runs it when
#               it has one, because that script owns the binds and the pg password)
#   sidecars    crossencoder :50052 (GPU 0), embedding :50053 (CPU)
#   sfr         six SFR-Embedding-Mistral vLLM endpoints :9001–:9006 on GPUs 0–5
#   apis        every registry tenant's API, on the port, bind, pidfile and log its row
#               records, from the worktree its row records
#   uis         every registry tenant whose ui.mode is `dev` or `external` — both are a
#               Vite server THIS account runs (`external` is one the control plane
#               deliberately does not manage; ui.mode `static` is a build nginx serves
#               from <data_dir>/ui/dist and has no process at all)
#   gowe        gowe-server :8091 + 25 workers via /scout/wf/gowe/start-gowe.sh, then prometheus :9090, grafana :3001
#               (25 = 21 + the four `ragstack-hackathon` workers added 2026-09-15; see #563)
#   labelers    the quarantined confirmation-run labelers (Scout/Qwen) via their supervisor
#   proxy       nginx :9000 — only reports unless --proxy (see below)
#   legacy-ui   :5173 and :5175 (skipped by default; their APIs :8000/:8010 were already down)
#
# Every step is idempotent: a port that already answers or an instance that is
# already listed is skipped, so the script is safe to re-run and safe to run on
# a live system (it will report "already running" for everything).
#
# TENANTS COME FROM THE REGISTRY. /rag/data/tenants/registry.json is the source
# of truth for which tenants exist, which ports they hold, where their code and
# data are, which stores are theirs alone and how their UI is served — and this
# script reads it with jq. There is no literal tenant list here any more: a
# tenant added to the registry comes back after a reboot without this file being
# edited, which is the failure `hackathon` hit in September (it existed for days
# before anybody noticed it was in none of the lists).
#
# Two consequences worth knowing before you read on:
#
#   * a row whose `state` is `handover` is skipped by a FLEET-WIDE run and
#     started by an explicit `--tenant <n>`: the first would race the service
#     account's take, the second is the documented way back from one.
#   * a row whose `owner` is `svcbvbrc` is SKIPPED, with a line saying so. That
#     tenant has been handed over to the control plane and is started by
#     `ragstack-ctl fleet start --all` from the service account's @reboot
#     crontab line. Two starters for one tenant is two servers on one port.
#   * `--tenant NAME` does exactly one tenant — its own stores, its API and its
#     UI — and nothing else: no shared stores, no sidecars, no GoWe. It is the
#     rollback half of a handover (PR-E: release → take → `restore.sh --tenant
#     <n>`), and it is also the fastest way to bring one tenant back by hand.
#
# What it still does NOT read from the registry: the shared stores (:6333,
# :6343, :9200, postgres, redis, neo4j), the sidecars, the SFR fleet and GoWe.
# None of them is a registry tenant; they are this host's furniture.
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
DRY=0; ONLY=""; SKIP=""; PROXY=0; TENANT=""
REGISTRY=${REGISTRY:-$DATA/tenants/registry.json}
JQ=${JQ:-/usr/bin/jq}
while [[ $# -gt 0 ]]; do
  case $1 in
    --dry-run) DRY=1 ;;
    --only) [[ -n ${2:-} ]] || { echo "--only needs a group list" >&2; exit 2; }; ONLY=$2; shift ;;
    --skip) [[ -n ${2:-} ]] || { echo "--skip needs a group list" >&2; exit 2; }; SKIP=$2; shift ;;
    --tenant) [[ -n ${2:-} ]] || { echo "--tenant needs a name" >&2; exit 2; }; TENANT=$2; shift ;;
    --proxy) PROXY=1 ;;
    -h|--help) sed -n '2,/^set -/p' "$0" | sed '$d'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac; shift
done
# --tenant is one tenant and nothing else. The groups it implies are the three
# a tenant is made of; --only still narrows further (`--tenant dev --only apis`
# restarts just its API), and --skip still subtracts.
if [[ -n $TENANT ]]; then
  [[ $TENANT =~ ^[a-z][a-z0-9-]{0,31}$ ]] || { echo "--tenant: $TENANT is not a tenant name" >&2; exit 2; }
  [[ -z $ONLY ]] && ONLY="stores,apis,uis"
fi

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

# ---------------------------------------------------------------- the registry
#
# Four helpers and one rule: every tenant fact this script uses comes from
# registry.json, read with jq. Nothing below has a tenant name in it.
#
# `reg_rows` is the ONE place the "handed over" skip lives, so a tenant that has
# moved to the control plane is skipped by every group at once rather than by
# each of them remembering to.
reg_ready() {                 # reg_ready → 0 when the registry can be read
  [[ -r $REGISTRY ]] || { say "  ✗ $REGISTRY is not readable — no tenant can be started from it"; return 1; }
  [[ -x $JQ ]] || { say "  ✗ $JQ is missing — the tenant lists are read with jq"; return 1; }
  "$JQ" -e . "$REGISTRY" >/dev/null 2>&1 || { say "  ✗ $REGISTRY is not valid JSON"; return 1; }
  return 0
}
# reg_rows → the tenant names this script may act on, one per line, in display
# order. SILENT: three groups capture its output, and a log line that ends up
# inside a captured tenant list becomes a "tenant" the script then tries to
# start (it did, in review — every word of the handover notice became a row).
# Everything worth saying about the skips is said once, by reg_announce.
reg_rows() {
  local names name
  # display_order first (the order the gateway advertises), then anything the
  # registry holds that nobody ordered — the same rule the ctl itself applies.
  names=$("$JQ" -r '(.display_order // []) + ((.tenants|keys) - (.display_order // [])) | .[]' "$REGISTRY") || return 1
  for name in $names; do
    [[ -n $TENANT && $name != "$TENANT" ]] && continue
    "$JQ" -e --arg n "$name" '.tenants[$n]' "$REGISTRY" >/dev/null 2>&1 || continue
    [[ $(reg_get "$name" '.owner') == svcbvbrc ]] && continue
    [[ $(reg_get "$name" '.state') == decommissioned ]] && continue
    # A tenant MID-HANDOVER is skipped by a fleet-wide run and acted on by an
    # explicit `--tenant`. Those are different situations: a boot that started
    # a tenant the service account is halfway through taking would put two
    # copies of it on one port, while `restore.sh --tenant <n>` is precisely
    # the documented way back from a handover that did not convince.
    if [[ $(reg_get "$name" '.state') == handover && -z $TENANT ]]; then continue; fi
    echo "$name"
  done
}

# reg_announce says, ONCE, which rows this run is deliberately not acting on.
# It is the line an operator needs after a handover: a tenant that does not
# appear in any group below has not been forgotten, it has an owner.
reg_announce() {
  local name owner state
  reg_ready || return 0
  for name in $("$JQ" -r '(.display_order // []) + ((.tenants|keys) - (.display_order // [])) | .[]' "$REGISTRY"); do
    [[ -n $TENANT && $name != "$TENANT" ]] && continue
    owner=$(reg_get "$name" '.owner'); state=$(reg_get "$name" '.state')
    if [[ $owner == svcbvbrc ]]; then
      say "  [$name] handed over to the control plane: svcbvbrc's @reboot line starts it (ragstack-ctl fleet start --all). This script leaves it alone."
    elif [[ $state == decommissioned ]]; then
      say "  [$name] decommissioned: nothing of it is started"
    elif [[ $state == handover ]]; then
      if [[ -n $TENANT ]]; then
        say "  [$name] mid-handover: starting it anyway because you named it — this is the documented way back (afterwards run 'ragstack-ctl tenant handover $name --abandon' so the row agrees)"
      else
        say "  [$name] mid-handover: NOT started by a fleet-wide run (svcbvbrc may be taking it). Name it explicitly to bring it back: $0 --tenant $name"
      fi
    fi
  done
}
reg_get() {                   # reg_get NAME '.json.path' → the value, "" for null/absent
  "$JQ" -r --arg n "$1" ".tenants[\$n]$2 // empty" "$REGISTRY" 2>/dev/null
}
# reg_absent NAME — "there is no such row", as opposed to "there is one and it
# is not ours to start". The two are different outcomes: a name nobody knows is
# an operator's typo and fails the run; a handed-over tenant is the system
# working as designed and must not.
reg_absent() {
  "$JQ" -e --arg n "$1" '.tenants[$n]' "$REGISTRY" >/dev/null 2>&1 && return 1 || return 0
}
reg_handed_over() {           # reg_handed_over NAME → 0 when the ctl owns it
  [[ $(reg_get "$1" '.owner') == svcbvbrc ]]
}
# tenant_rows_or_note NAME-LIST-VAR — the rows a group should act on, with the
# right thing said and the right exit status when --tenant produced none.
#
# Returns 0 when the group should carry on (even with nothing to do) and 1 when
# the run should fail.
reg_tenant_scope_ok() {
  [[ -n $TENANT ]] || return 0
  if reg_absent "$TENANT"; then
    say "  ✗ no registry row for '$TENANT' in $REGISTRY"
    return 1
  fi
  if reg_handed_over "$TENANT"; then
    say "  [$TENANT] handed over: nothing for this script to do here (the ctl starts and stops it)"
    return 0
  fi
  return 0
}
# reg_ready's messages are the same case: it is called inside `if`, not inside
# a capture, so its `say` lines stay on stdout with the rest of the log.

# tenant_store_specs NAME → one "<kind> <instance> <port>" line per store leg
# this tenant owns ALONE. A shared leg is deliberately absent: it is somebody
# else's furniture and appears in the shared-stores block above, once.
tenant_store_specs() {
  local name=$1 inst port
  if [[ $(reg_get "$name" '.stores.qdrant.ownership') == exclusive ]]; then
    inst=$(reg_get "$name" '.stores.qdrant.instance'); port=$(reg_get "$name" '.ports.qdrant_http')
    [[ -n $inst && -n $port ]] && echo "qdrant $inst $port"
  fi
  if [[ $(reg_get "$name" '.stores.elasticsearch.ownership') == exclusive ]]; then
    inst=$(reg_get "$name" '.stores.elasticsearch.instance'); port=$(reg_get "$name" '.ports.es_http')
    [[ -n $inst && -n $port ]] && echo "elasticsearch $inst $port"
  fi
  if [[ $(reg_get "$name" '.stores.postgres.kind') == local ]]; then
    inst=$(reg_get "$name" '.stores.postgres.instance'); port=$(reg_get "$name" '.stores.postgres.port')
    [[ -z $port ]] && port=$(reg_get "$name" '.ports.pg')
    [[ -n $inst && -n $port ]] && echo "postgres $inst $port"
  fi
}

# tenant_up_script NAME → the tenant's own store launcher, or "".
#
# It is used for ONE thing: the postgres leg. That instance needs the role
# password new-tenant.sh generated, which lives in the tenant's secrets.env and
# must not be read into this script's environment or onto its argv — so the
# script that already holds it starts it.
#
# It is deliberately NOT used for qdrant or elasticsearch. up.sh is a
# PROVISION-TIME artefact: it carries the values the tenant was created with,
# and those drift. dev's says `ES_JAVA_OPTS=-Xms512m -Xmx512m` while the
# elasticsearch that has been serving dev for months runs with 1g — the
# registry records the live value (stores.elasticsearch.heap, extra_env) and
# carries an `es_heap_drift` row saying the two disagree. A restore that ran
# up.sh would quietly halve the heap of a store that came back after a reboot,
# which is the class of change nobody notices until a query times out.
#
# `up-es.sh` is the older one-store spelling (lucid has one and no up.sh).
tenant_up_script() {
  local data=$1 cand
  for cand in "$data/bin/up.sh" "$data/bin/up-es.sh"; do
    [[ -x $cand ]] && { echo "$cand"; return 0; }
  done
  return 1
}

# reg_env NAME '<jq expression yielding an object>' → "K=V" lines.
#
# The env a store runs with, straight out of the row. jq builds the object so
# that a value with a space in it (ES_JAVA_OPTS is two words) survives as one
# line, and the caller turns each line into one `--env` argv element — no shell
# ever sees it.
reg_env() {
  "$JQ" -r --arg n "$1" ".tenants[\$n] as \$t | ($2) | to_entries[] | \"\(.key)=\(.value)\"" "$REGISTRY" 2>/dev/null
}

# tenant_stores_up NAME — bring up one tenant's exclusive store instances.
#
# qdrant and elasticsearch are rebuilt FROM THE ROW: the instance name, the
# image, the ports, the binds and the environment the store is actually running
# with. postgres is started through the tenant's own launcher, for the password.
tenant_stores_up() {
  local name=$1 data up kind inst port sif specs rc=0
  data=$(reg_get "$name" '.data_dir')
  [[ -n $data ]] || { say "  [$name] the registry row has no data_dir — stores NOT started"; return 1; }
  specs=$(tenant_store_specs "$name")
  [[ -n $specs ]] || { say "  [$name] no exclusively-owned stores (it runs on the shared ones)"; return 0; }
  # Mark the legs whose port is not answering YET, so the waits below are full
  # waits for those and a glance for the rest. (A leg started by the tenant's
  # own launcher never goes through start_instance, which is what would
  # otherwise set this.)
  while read -r kind inst port; do
    [[ -n $inst ]] || continue
    port_up "$port" || STARTED[$inst]=1
  done <<< "$specs"

  while read -r kind inst port; do
    [[ -n $inst ]] || continue
    case $kind in
      qdrant)     tenant_qdrant_up "$name" "$data" "$inst" "$port" || rc=1 ;;
      elasticsearch) tenant_es_up "$name" "$data" "$inst" "$port" || rc=1 ;;
      postgres)
        if up=$(tenant_up_script "$data"); then
          # up.sh starts every leg it knows; the two above are already running
          # by now and its own idempotency check skips them.
          run "[$name postgres $inst :$port] via $up (it holds the generated password)" "$up" || rc=1
        else
          say "  ✗ [$name] postgres $inst (:$port) needs $data/bin/up.sh, which is missing — NOT started."
          say "    Its password is in the tenant's secrets.env and is never reconstructed here."
          rc=1
        fi ;;
    esac
  done <<< "$specs"
  return $rc
}

# tenant_qdrant_up — one tenant's own qdrant, from its row.
tenant_qdrant_up() {
  local name=$1 data=$2 inst=$3 port=$4 sif opts=() kv
  sif=$(reg_get "$name" '.stores.qdrant.sif'); [[ -n $sif ]] || sif=$IMG/qdrant.sif
  # extra_env is what the running store's environment actually holds; the port
  # keys are overridden from the block, which is what every other reader of
  # this registry computes and therefore the one authority on a tenant's ports.
  while read -r kv; do
    [[ -n $kv ]] && opts+=(--env "$kv")
  done <<< "$(reg_env "$name" '($t.stores.qdrant.extra_env // {})
                + {QDRANT__SERVICE__HTTP_PORT: ($t.ports.qdrant_http|tostring),
                   QDRANT__SERVICE__GRPC_PORT: ($t.ports.qdrant_grpc|tostring)}')"
  start_instance "$inst" "$sif" \
    --bind "$data/qdrant/storage:/qdrant/storage" \
    --bind "$data/qdrant/snapshots:/qdrant/snapshots" \
    "${opts[@]}" \
    -- /bin/sh -c 'cd /qdrant && exec ./entrypoint.sh'
}

# tenant_es_up — one tenant's own elasticsearch, from its row.
#
# Dotted settings go as native `-E` arguments, never through `--env`: apptainer
# shell-sources its environment and mangles them. docker-entrypoint.sh is called
# directly because /bin/tini parses `-E` as its own option.
tenant_es_up() {
  local name=$1 data=$2 inst=$3 port=$4 sif heap repo transport opts=() kv
  sif=$(reg_get "$name" '.stores.elasticsearch.sif'); [[ -n $sif ]] || sif=$IMG/elasticsearch.sif
  heap=$(reg_get "$name" '.stores.elasticsearch.heap'); [[ -n $heap ]] || heap=1g
  transport=$(reg_get "$name" '.ports.es_transport')
  # The LIVE environment first (extra_env carries ES_JAVA_OPTS as the store is
  # really running), with the recorded heap as the fallback for a row that has
  # no extra_env at all.
  while read -r kv; do
    [[ -n $kv ]] && opts+=(--env "$kv")
  done <<< "$(reg_env "$name" '($t.stores.elasticsearch.extra_env // {})
                | if has("ES_JAVA_OPTS") then . else . + {ES_JAVA_OPTS: "-Xms'"$heap"' -Xmx'"$heap"'"} end')"
  # The config bind SHADOWS the image's own config directory: an empty host
  # directory is an Elasticsearch that exits before it logs anything useful.
  seed_if_empty "$sif" /usr/share/elasticsearch/config "$data/elasticsearch/config"
  # path.repo only when the directory is really there — apptainer refuses a bind
  # whose source is missing, and not every adopted tenant has one yet (doctor's
  # es_snapshots_dir_missing).
  repo=()
  [[ -d $data/elasticsearch/snapshots ]] && repo=(--bind "$data/elasticsearch/snapshots:/usr/share/elasticsearch/snapshots")
  start_instance "$inst" "$sif" \
    --bind "$data/elasticsearch/data:/usr/share/elasticsearch/data" \
    --bind "$data/elasticsearch/logs:/usr/share/elasticsearch/logs" \
    --bind "$data/elasticsearch/config:/usr/share/elasticsearch/config" \
    "${repo[@]}" "${opts[@]}" \
    -- /usr/local/bin/docker-entrypoint.sh eswrapper \
       -Ediscovery.type=single-node -Expack.security.enabled=false \
       -Ehttp.port="$port" -Etransport.port="$transport"
}

# tenant_stores_wait NAME — readiness for the legs tenant_stores_up started.
tenant_stores_wait() {
  local name=$1 kind inst port rc=0 pgi pgok
  while read -r kind inst port; do
    [[ -n $inst ]] || continue
    case $kind in
      qdrant)        wait_if_started "$inst" "http://127.0.0.1:$port/collections" 120 "$inst :$port" || rc=1 ;;
      elasticsearch) wait_if_started "$inst" "http://127.0.0.1:$port/" 180 "$inst :$port" || rc=1 ;;
      postgres)
        # No HTTP surface: pg_isready stands in for wait_http. It IS fatal —
        # a tenant with a dedicated postgres keeps its users, ACL grants,
        # collections and jobs in it, so its API cannot serve without it.
        if (( ! DRY )); then
          pgi=0; pgok=0
          while (( pgi < 60 )); do
            apptainer exec "$IMG/postgres.sif" pg_isready -h 127.0.0.1 -p "$port" >/dev/null 2>&1 && { pgok=1; break; }
            sleep 5; pgi=$((pgi+5))
          done
          (( pgok )) && say "    ✓ $inst ready (:$port)" || { say "    ✗ $inst (:$port) not ready within ${pgi}s"; rc=1; }
        fi ;;
    esac
  done <<< "$(tenant_store_specs "$name")"
  return $rc
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
# Which tenants this run will NOT act on, said once and up front.
if [[ -z $ONLY || $ONLY == *tenant* || $ONLY == *apis* || $ONLY == *stores* || $ONLY == *uis* ]]; then
  reg_announce
fi

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
  # The shared stores are this host's furniture, not any tenant's: `--tenant`
  # leaves them exactly where it found them (a tenant-scoped restore runs while
  # the rest of the fleet is up).
  if [[ -n $TENANT ]]; then
  say "  (--tenant $TENANT: the shared stores are not this tenant's and are left alone)"
  else
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

  # ES: dotted settings as native -E args (apptainer --env shell-sources and mangles them);
  # call docker-entrypoint.sh directly (tini eats -E). Heaps are the LIVE values, not up.sh's.
  seed_if_empty "$IMG/elasticsearch.sif" /usr/share/elasticsearch/config "$DATA/elasticsearch/config"
  start_instance elasticsearch "$IMG/elasticsearch.sif" \
    --bind "$DATA/elasticsearch/data:/usr/share/elasticsearch/data" --bind "$DATA/elasticsearch/logs:/usr/share/elasticsearch/logs" \
    --bind "$DATA/elasticsearch/config:/usr/share/elasticsearch/config" --env ES_JAVA_OPTS="-Xms1g -Xmx1g" \
    -- /usr/local/bin/docker-entrypoint.sh eswrapper -Ediscovery.type=single-node -Expack.security.enabled=false

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
  wait_if_started elasticsearch http://127.0.0.1:9200/ 180 "elasticsearch :9200" || fail=1
  wait_if_started neo4j-dev http://127.0.0.1:24046/ 120 "neo4j-dev :24046" || say "    (neo4j-dev: graph leg is disabled on every tenant; not fatal)"
  if [[ -n ${STARTED[neo4j]:-} ]]; then wait_http http://127.0.0.1:7474/ 60 "neo4j :7474" || say "    (neo4j: was already dead before the reboot; not fatal)"; else say "    (neo4j: not started by this run — skipped; it has been dead since 2026-06-04)"; fi
  (( DRY )) || { apptainer exec "$IMG/redis.sif" redis-cli -p 6379 ping 2>/dev/null | grep -q PONG && say "    ✓ redis PONG" || say "    ✗ redis"; }
  (( DRY )) || { apptainer exec "$IMG/postgres.sif" pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1 && say "    ✓ postgres ready" || say "    ✗ postgres"; }
  fi

  # ---- per-tenant stores, from the registry -------------------------------
  #
  # One loop for every tenant that owns a store outright — dev's qdrant and
  # elasticsearch, lucid's elasticsearch, hackathon's three — where there used
  # to be a hand-written block per tenant and a matching wait twenty lines
  # further down. A tenant added to the registry is covered by both halves at
  # once, which is the whole point.
  if reg_ready; then
    reg_tenant_scope_ok || fail=1
    rows=$(reg_rows)
    for t in $rows; do
      tenant_stores_up "$t" || fail=1
    done
    for t in $rows; do
      tenant_stores_wait "$t" || fail=1
    done
  else
    say "  ✗ no readable registry: per-tenant stores were NOT started"; fail=1
  fi
fi

# From here on the groups are fleet-wide furniture: the sidecars, the SFR fleet,
# GoWe, the labelers and the gateway. `--tenant` never touches them — it already
# narrowed ONLY to the three groups a tenant is made of, so these are skipped by
# `want` without a special case.

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
  if ! reg_ready; then
    say "  ✗ no readable registry: no tenant API was started"; fail=1
  else
  reg_tenant_scope_ok || fail=1
  rows=$(reg_rows)
  for name in $rows; do
    tdir=$(reg_get "$name" '.data_dir'); port=$(reg_get "$name" '.ports.api')
    code=$(reg_get "$name" '.worktree')/python
    penv=$(reg_get "$name" '.python_env'); [[ -n $penv ]] || penv=/rag/envs/ragstack
    pidf=$(reg_get "$name" '.api.pidfile'); [[ -n $pidf ]] || pidf=$tdir/api-$name.pid
    logf=$(reg_get "$name" '.api.log');     [[ -n $logf ]] || logf=$tdir/logs/api-$name.log
    # The BIND comes from the row too (api.bind). It is the one place the value
    # lives — `ragstack-ctl tenant set-bind` writes it, render.APIArgv reads it
    # for a ctl-supervised start, and this reads it for a hand start — so a
    # tenant moved to loopback before its handover comes back on loopback here
    # rather than on 0.0.0.0 because a script remembered the old default.
    bind=$(reg_get "$name" '.api.bind'); [[ -n $bind ]] || bind=127.0.0.1
    if [[ -z $tdir || -z $port || $code == /python ]]; then
      say "  [api $name] incomplete registry row (data_dir=$tdir port=$port worktree=$code) — NOT started"; fail=1; continue
    fi
    if port_up "$port"; then say "  [api $name :$port] already listening — skipping"; continue; fi
    [[ -f $tdir/config/tenant.env ]] || { say "  [api $name] missing $tdir/config/tenant.env"; fail=1; continue; }
    # /v1/version must report the artifact this launch actually runs, the way the ctl unit
    # does it (ADR-0007, go/internal/ctl/render/storeargv.go): the TENANT WORKTREE's tag and
    # sha, not whatever checkout `ragstack` happens to import from. `-c safe.directory=*` so
    # a worktree owned by another account still answers; empty on failure is tolerated —
    # ragstack.version falls back, and an unset value must not stop a reboot recovery.
    wt=$(reg_get "$name" '.worktree')
    gtag=$(git -c safe.directory='*' -C "$wt" describe --tags --always 2>/dev/null || true)
    gsha=$(git -c safe.directory='*' -C "$wt" rev-parse HEAD 2>/dev/null || true)
    if (( DRY )); then echo "  [dry-run] api $name: (set -a; . $tdir/config/tenant.env; [ -f $tdir/config/secrets.env ] && . $tdir/config/secrets.env; set +a; cd $code; HF_HOME=/rag/cache PYTHONPATH=$code RAGSTACK_GIT_TAG=$gtag RAGSTACK_GIT_SHA=$gsha nohup $penv/bin/python -m uvicorn ragstack.api.main:app --host $bind --port $port >> $logf) ; pid → $pidf"; continue; fi
    say "  [api $name :$port] launching from $code (${gtag:-no tag} ${gsha:0:12}) on $bind"
    STARTED[api-$name]=1
    mkdir -p "$(dirname "$logf")"
    ( set -a; . "$tdir/config/tenant.env"; [[ -f $tdir/config/secrets.env ]] && . "$tdir/config/secrets.env"; set +a
      export HF_HOME=/rag/cache PYTHONPATH=$code RAGSTACK_GIT_TAG="$gtag" RAGSTACK_GIT_SHA="$gsha"
      cd "$code" && exec setsid nohup "$penv/bin/python" -m uvicorn ragstack.api.main:app --host "$bind" --port "$port" \
        >> "$logf" 2>&1 < /dev/null ) &
  done
  for name in $rows; do
    tdir=$(reg_get "$name" '.data_dir'); port=$(reg_get "$name" '.ports.api')
    pidf=$(reg_get "$name" '.api.pidfile'); [[ -n $pidf ]] || pidf=$tdir/api-$name.pid
    [[ -n $port ]] || continue
    wait_if_started "api-$name" "http://127.0.0.1:$port/health" 180 "api $name :$port" || { fail=1; continue; }
    # the pid file is the handle pre-reboot.sh and the restart recipes use — it must hold the uvicorn pid, not a shell
    [[ -n ${STARTED[api-$name]:-} ]] && (( ! DRY )) && record_pid_by_port "api-$name" "$port" "$pidf"
  done
  fi
fi

# ---------------------------------------------------------------- uis
if want uis; then
  say "== tenant UIs (base-aware Vite dev servers, via /rag/config/proxy/ui-dev.sh)"
  # Which tenants have one is the REGISTRY's answer, not a list here: ui.mode
  # `dev` and `external` are both a Vite server this account runs (`external`
  # is the mode the control plane records for a UI it deliberately does not
  # manage — plan PR-E decision D2, which is how `dev` keeps its dev server
  # after its handover), and `static` is a build nginx serves from
  # <data_dir>/ui/dist with no process to start. A static tenant in these loops
  # would wait out its full timeout on a port nobody will ever own.
  if ! reg_ready; then
    say "  ✗ no readable registry: no tenant UI was started"; fail=1
  else
  rows=$(reg_rows)
  for t in $rows; do
    mode=$(reg_get "$t" '.ui.mode'); port=$(reg_get "$t" '.ui.port')
    [[ $mode == dev || $mode == external ]] || continue
    [[ -n $port ]] || { say "  [ui $t] ui.mode=$mode with no port in the registry — nothing to start"; continue; }
    dir=$(reg_get "$t" '.worktree')/frontend
    if port_up "$port"; then say "  [ui $t :$port] already listening — skipping"; continue; fi
    [[ -d $dir/node_modules ]] || say "  [ui $t] WARNING: $dir/node_modules missing — run npm install there first"
    STARTED[ui-$t]=1
    run "[ui $t :$port]" /rag/config/proxy/ui-dev.sh "$t" "$port" "$dir"
  done
  for t in $rows; do
    mode=$(reg_get "$t" '.ui.mode'); port=$(reg_get "$t" '.ui.port')
    [[ $mode == dev || $mode == external ]] || continue
    [[ -n $port ]] || continue
    wait_if_started "ui-$t" "http://127.0.0.1:$port/" 120 "ui $t :$port" || { fail=1; continue; }
    [[ -n ${STARTED[ui-$t]:-} ]] && (( ! DRY )) && record_pid_by_port "ui-$t" "$port"
  done
  fi
fi

# ---------------------------------------------------------------- gowe
if want gowe; then
  say "== GoWe (server :8091 + 25 workers, then monitoring) — via the fleet's own launcher"
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
    sleep 3; n=0; declare -A gn=()
    # one /proc pass: count the workers, tally them per --group, and record each
    # one's pid under its --name
    for pr in /proc/[0-9]*; do
      c=$({ tr '\0' ' ' < "$pr/cmdline"; } 2>/dev/null); [[ $c == ./bin/gowe-worker* ]] || continue
      n=$((n+1)); wn=$(echo "$c" | grep -o -- '--name [^ ]*' | cut -d' ' -f2); [[ -n $wn ]] && echo "${pr#/proc/}" > "$PIDS/$wn.pid"
      wg=$(echo "$c" | grep -o -- '--group [^ ]*' | cut -d' ' -f2); gn[${wg:-default}]=$(( ${gn[${wg:-default}]:-0} + 1 ))
    done
    say "    $n gowe-worker processes running (expected 25); pids recorded under $PIDS"; (( n == 25 )) || fail=1
    # The total alone is not enough: 25 is satisfiable with ZERO workers in a
    # group if four of something else came up, and a labelled submission never
    # falls back to another group — hackathon ingests would queue PENDING
    # forever rather than fail fast (#563). Assert the per-group shape too.
    for spec in "ragstack 4" "ragstack-hackathon 4" "ragstack-cpu 1"; do
      set -- $spec
      if (( ${gn[$1]:-0} == $2 )); then say "    ✓ group $1: ${gn[$1]:-0} worker(s)"
      else say "    ✗ group $1: ${gn[$1]:-0} worker(s), expected $2 — that group's tenants will queue, not fail"; fail=1; fi
    done
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
    say "          cd /rag/repos/ragstack/ops/ansible && ./check.sh root -K     # preview"
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
