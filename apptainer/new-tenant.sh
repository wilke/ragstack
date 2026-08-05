#!/usr/bin/env bash
# new-tenant.sh — provision a tenant per ADR-0005 (dedicated stateful stores,
# scripted provisioning). A tenant is one API endpoint bound to a dedicated
# Qdrant + Elasticsearch + ACL/registry state, sharing only stateless compute
# (embedding fleet, sidecars) and the host.
#
# Usage:
#   new-tenant.sh <name> [--dry-run] [--postgres <admin-dsn>] [--es-heap <size>]
#                        [--start] [--force]
#
#   <name>            lowercase slug: ^[a-z][a-z0-9-]*$ (feeds instance names,
#                     directory paths, env-file tokens and DB identifiers).
#   --dry-run         print the complete plan (dirs, ports, files, commands)
#                     and touch NOTHING — no mkdir, no manifest write, no
#                     apptainer calls. Generated secrets are shown as stable
#                     <GENERATED:*> placeholders so the output is reproducible.
#   --postgres <dsn>  use a per-tenant DATABASE in the provided Postgres server
#                     (ADR-0004 amendment) for the ACL/registry/job stores,
#                     instead of the default per-tenant sqlite files. The DSN is
#                     an admin connection, e.g.
#                     postgresql://ragstack:pw@localhost:5432/postgres
#   --es-heap <size>  Elasticsearch heap (ES_JAVA_OPTS -Xms/-Xmx). Default 512m.
#                     ADR-0005: the ES JVM heap is the dominant per-tenant cost.
#   --start           start the tenant's store instances after provisioning
#                     (default: emit the start/stop commands only).
#   --force           overwrite an existing, operator-edited tenant.env
#                     (default: keep it and print the diff).
#
# Idempotent: re-running completes missing pieces and changes nothing that
# exists. Port allocation is deterministic from the manifest at
# $RAG_DATA/tenants/manifest.tsv (next free block; existing rows are reused
# verbatim; the read-modify-write is serialized with flock so concurrent runs
# cannot allocate the same block). Secrets are generated once and read back
# thereafter. Provisioning choices (--es-heap, the --postgres store kind)
# persist in <tenant>/config/provision.env and are read back on flagless
# re-runs. bin/up.sh and bin/down.sh are derived artifacts, regenerated
# deterministically — do not hand-edit them; tenant.env is the operator-
# editable file (kept on re-runs unless --force).
#
# House conventions (see apptainer/up.sh and MEMORY.md):
#   - every writable path enumerated and bind-mounted under
#     $RAG_DATA/tenants/<name>/<service>/<purpose>/ — never --writable-tmpfs
#   - shared SIFs from $RAG_IMAGES are REUSED (run ./pull.sh first);
#     ES must stay 8.x (python client is bounded <9)
#   - dotted ES settings go as native -E args (never --env: apptainer
#     shell-sources it), bypassing /bin/tini which eats -E flags
#   - qdrant CMD is CWD-relative and apptainer has no --cwd: wrap with
#     /bin/sh -c 'cd /qdrant && exec ./entrypoint.sh'
#   - postgres PGDATA must be a SUBDIR of the bind (not used here: the default
#     store is sqlite; --postgres targets an existing server)
#
# Verified only manually on the deploy host (not in CI): instance startup,
# ES green health on the allocated port, DSN reachability, persistence across
# down/up, vm.max_map_count sufficiency.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${RAG_DATA:-$HERE/data}"
IMG="${RAG_IMAGES:-$HERE/images}"

usage() {
    # Print the header comment from line 2 down to (not including) the
    # "Idempotent:" paragraph — delimiter-bounded so header edits cannot
    # silently truncate a flag's description mid-sentence.
    awk 'NR==1 { next }
         !/^#/ { exit }
         { sub(/^# ?/, ""); if ($0 ~ /^Idempotent:/) exit; print }' \
        "${BASH_SOURCE[0]}"
}

die() { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }

# --------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------
NAME=""
DRY_RUN=0
PG_ADMIN_DSN=""
ES_HEAP="512m"
ES_HEAP_SET=0
START=0
FORCE=0

while (( $# )); do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --postgres)
            [[ $# -ge 2 ]] || die "--postgres requires a DSN argument"
            PG_ADMIN_DSN="$2"; shift ;;
        --es-heap)
            [[ $# -ge 2 ]] || die "--es-heap requires a size argument (e.g. 512m)"
            ES_HEAP="$2"; ES_HEAP_SET=1; shift ;;
        --start) START=1 ;;
        --force) FORCE=1 ;;
        -h|--help) usage; exit 0 ;;
        -*) die "unknown flag: $1 (see --help)" ;;
        *)
            [[ -z "$NAME" ]] || die "unexpected argument: $1 (name already given: $NAME)"
            NAME="$1" ;;
    esac
    shift
done

[[ -n "$NAME" ]] || { usage >&2; die "tenant name required"; }

# The name feeds apptainer instance names, directory paths, env-file tokens
# and (with --postgres) SQL identifiers — validate it hard. No ':' (subject
# strings are 'issuer:sub'), no uppercase, no leading '-'.
[[ "$NAME" =~ ^[a-z][a-z0-9-]{0,31}$ ]] || \
    die "invalid tenant name '$NAME' — must match ^[a-z][a-z0-9-]{0,31}\$"
case "$NAME" in
    qdrant|elasticsearch|neo4j|postgres|redis|embedding|crossencoder|faiss|tenants|manifest|default|public)
        die "tenant name '$NAME' is reserved (collides with a shared instance or built-in)" ;;
esac

# --------------------------------------------------------------------------
# Port allocation — deterministic from the manifest, never probed or hashed.
# One row per tenant: <name>\t<index>\t<base_port>. An existing row is reused
# verbatim; a new tenant gets index = max(existing)+1. Base 24000 clears all
# locally occupied service ports (APIs, store instances, model endpoints,
# sidecars, infra defaults).
# Offsets within a block: +0 API, +1 qdrant http, +2 qdrant grpc, +3 ES http,
# +4 ES transport, +5 postgres (reserved) — room to grow within the stride.
# --------------------------------------------------------------------------
PORT_BASE="${TENANT_PORT_BASE:-24000}"
PORT_STRIDE="${TENANT_PORT_STRIDE:-20}"
MANIFEST="$DATA/tenants/manifest.tsv"

(( PORT_BASE >= 10000 )) || die "TENANT_PORT_BASE=$PORT_BASE too low — must be >= 10000 to clear host services"
(( PORT_STRIDE >= 6 )) || die "TENANT_PORT_STRIDE=$PORT_STRIDE too small — need at least 6 ports per tenant"

# Serialize the whole allocation read-modify-write: without this, two
# concurrent runs both compute max+1 and allocate the SAME block, and the
# later manifest rewrite drops the sibling's row. The lock is taken before
# the allocation read and released explicitly right after the manifest write
# (never inherited by --start children — apptainer instances would hold it
# forever). Dry-run touches nothing, so it reads lock-free.
if (( ! DRY_RUN )); then
    mkdir -p "$DATA/tenants"
    exec 9>"$MANIFEST.lock"
    # Bounded: a wedged sibling run must fail loudly, not hang a 2am operator.
    flock -w 30 9 || die "timed out (30s) waiting for $MANIFEST.lock — another new-tenant.sh is running (or left a stale lock)"
fi

ALLOC_SOURCE="new allocation"
IDX=""
BASE=""
if [[ -f "$MANIFEST" ]]; then
    row="$(awk -F'\t' -v n="$NAME" '$1==n {print; exit}' "$MANIFEST")"
    if [[ -n "$row" ]]; then
        IDX="$(cut -f2 <<<"$row")"
        BASE="$(cut -f3 <<<"$row")"
        [[ "$IDX" =~ ^[0-9]+$ && "$BASE" =~ ^[0-9]+$ ]] || \
            die "corrupt manifest row for '$NAME' in $MANIFEST: $row"
        ALLOC_SOURCE="from manifest"
    fi
fi
if [[ -z "$BASE" ]]; then
    max=-1
    if [[ -f "$MANIFEST" ]]; then
        max="$(awk -F'\t' 'BEGIN{m=-1} /^[^#]/ && NF>=3 && $2 ~ /^[0-9]+$/ {if ($2+0>m) m=$2+0} END{print m}' "$MANIFEST")"
    fi
    IDX=$(( max + 1 ))
    BASE=$(( PORT_BASE + IDX * PORT_STRIDE ))
fi
(( BASE + PORT_STRIDE <= 65535 )) || die "allocated port block $BASE+$PORT_STRIDE exceeds 65535"

# Collision check against every other manifest row (blocks must be disjoint).
# The `|| [[ -n ... ]]` keeps a hand-edited final line WITHOUT a trailing
# newline in scope — plain `read` returns nonzero on it and would skip it.
if [[ -f "$MANIFEST" ]]; then
    while IFS=$'\t' read -r oname _oidx obase || [[ -n "$oname" ]]; do
        [[ "$oname" == \#* || -z "$obase" || "$oname" == "$NAME" ]] && continue
        [[ "$obase" =~ ^[0-9]+$ ]] || continue
        if (( BASE < obase + PORT_STRIDE && obase < BASE + PORT_STRIDE )); then
            die "port block $BASE collides with tenant '$oname' (base $obase) in $MANIFEST"
        fi
    done < "$MANIFEST"
fi

PORT_API=$(( BASE + 0 ))
PORT_QDRANT_HTTP=$(( BASE + 1 ))
PORT_QDRANT_GRPC=$(( BASE + 2 ))
PORT_ES_HTTP=$(( BASE + 3 ))
PORT_ES_TRANSPORT=$(( BASE + 4 ))
PORT_PG=$(( BASE + 5 ))

# --------------------------------------------------------------------------
# Preflight: the manifest is NOT the universe of port owners.
# It only knows tenants THIS $RAG_DATA provisioned. Run from a checkout whose
# RAG_DATA differs from the deployment's (e.g. the in-repo default, without
# sourcing the deployment's rag.env) and a fresh manifest hands out a block
# another live tenant already holds — the stores fail to bind (loud), but the
# stamped tenant.env silently dials the OTHER tenant's Qdrant/ES. So probe the
# ports for real before writing anything.
# --------------------------------------------------------------------------
port_in_use() {  # 0 = occupied
    local p="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -ltn "sport = :$p" 2>/dev/null | grep -q LISTEN && return 0
    elif command -v netstat >/dev/null 2>&1; then
        netstat -ltn 2>/dev/null | grep -qE "[:.]$p[[:space:]]" && return 0
    else
        return 1  # no probe available — cannot assert, do not block
    fi
    return 1
}
busy=()
for p in "$PORT_API" "$PORT_QDRANT_HTTP" "$PORT_QDRANT_GRPC" "$PORT_ES_HTTP" "$PORT_ES_TRANSPORT"; do
    port_in_use "$p" && busy+=("$p")
done
if (( ${#busy[@]} )); then
    # A re-run of an already-STARTED tenant legitimately finds its own ports
    # listening; that is the only benign case, and it requires the manifest row
    # to already be ours.
    if [[ "$ALLOC_SOURCE" == "from manifest" ]]; then
        warn "ports already listening (${busy[*]}) — assuming this tenant's own running instances"
    else
        die "port(s) ${busy[*]} in block $BASE are already in use by something this manifest does not know about.
    The manifest at $MANIFEST only tracks tenants provisioned under RAG_DATA=$DATA.
    If this host runs a deployment with its own RAG_DATA, source its rag.env first
    (so this script sees the real manifest), or pass TENANT_PORT_BASE=<free base>."
    fi
fi

# --------------------------------------------------------------------------
# Paths — extends the apptainer/data/<service>/<purpose>/ enumeration one
# level down: $RAG_DATA/tenants/<name>/<service>/<purpose>/.
# Keep this list authoritative — every writable path an instance touches.
# --------------------------------------------------------------------------
TDIR="$DATA/tenants/$NAME"
TENANT_DIRS=(
    "$TDIR/qdrant/storage"
    "$TDIR/qdrant/snapshots"
    "$TDIR/elasticsearch/data"
    "$TDIR/elasticsearch/logs"
    "$TDIR/elasticsearch/config"
    "$TDIR/state"
    "$TDIR/manifests"
    "$TDIR/ingest"
    "$TDIR/config"
    "$TDIR/bin"
)
ENV_FILE="$TDIR/config/tenant.env"
SECRETS_FILE="$TDIR/config/secrets.env"
UP_SH="$TDIR/bin/up.sh"
DOWN_SH="$TDIR/bin/down.sh"

# --------------------------------------------------------------------------
# Postgres (only with --postgres): host/port from the admin DSN.
# --------------------------------------------------------------------------
PG_HOST=""
PG_PORT=""
if [[ -n "$PG_ADMIN_DSN" ]]; then
    rest="${PG_ADMIN_DSN#*://}"
    hostport="${rest#*@}"
    hostport="${hostport%%/*}"
    hostport="${hostport%%\?*}"
    PG_HOST="${hostport%%:*}"
    PG_PORT="${hostport##*:}"
    [[ "$PG_PORT" != "$PG_HOST" ]] || PG_PORT=5432
    [[ -n "$PG_HOST" ]] || die "could not parse host from --postgres DSN"
fi

# --------------------------------------------------------------------------
# Provisioning settings — persisted per tenant (provision.env) so a plain
# re-run keeps them: without this, re-running with no flags would silently
# revert --es-heap to the default and (with --force) flip a postgres tenant
# back to sqlite, violating the idempotency contract above.
# --------------------------------------------------------------------------
PROVISION_FILE="$TDIR/config/provision.env"
STORE_KIND=sqlite
[[ -n "$PG_ADMIN_DSN" ]] && STORE_KIND=postgres
if [[ -f "$PROVISION_FILE" ]]; then
    # shellcheck disable=SC1090
    . "$PROVISION_FILE"
    if (( ! ES_HEAP_SET )) && [[ -n "${TENANT_ES_HEAP:-}" ]]; then
        ES_HEAP="$TENANT_ES_HEAP"
    fi
    if [[ -z "$PG_ADMIN_DSN" && "${TENANT_STORE_KIND:-}" == "postgres" ]]; then
        # Postgres tenant re-run without --postgres: keep rendering the
        # postgres env (the tenant's own role/password come from secrets.env);
        # the guarded psql steps are skipped — they need the admin DSN and
        # already ran when the tenant was first provisioned.
        STORE_KIND=postgres
        PG_HOST="${TENANT_PG_HOST:-}"
        PG_PORT="${TENANT_PG_PORT:-5432}"
        [[ -n "$PG_HOST" ]] || \
            die "corrupt $PROVISION_FILE: TENANT_STORE_KIND=postgres but no TENANT_PG_HOST"
    fi
fi

render_provision() {
    cat <<EOF
# provision.env — tenant '$NAME' (written by apptainer/new-tenant.sh)
# Persists provisioning choices across re-runs; flags on a later run update it.
TENANT_ES_HEAP=$ES_HEAP
TENANT_STORE_KIND=$STORE_KIND
TENANT_PG_HOST=$PG_HOST
TENANT_PG_PORT=$PG_PORT
EOF
}

# --------------------------------------------------------------------------
# Secrets — generated ONCE, read back on every later run so re-runs are
# byte-stable. Dry-run always uses placeholders (reproducible, leak-free).
# --------------------------------------------------------------------------
gen_hex() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }

if (( DRY_RUN )); then
    KEY_USER="<GENERATED:API_KEY_USER>"
    KEY_ADMIN="<GENERATED:API_KEY_ADMIN>"
    PG_PASSWORD="<GENERATED:PG_PASSWORD>"
elif [[ -f "$SECRETS_FILE" ]]; then
    # shellcheck disable=SC1090
    . "$SECRETS_FILE"
    KEY_USER="${TENANT_API_KEY_USER:-}"
    KEY_ADMIN="${TENANT_API_KEY_ADMIN:-}"
    PG_PASSWORD="${TENANT_PG_PASSWORD:-}"
    [[ -n "$KEY_USER" && -n "$KEY_ADMIN" && -n "$PG_PASSWORD" ]] || \
        die "incomplete $SECRETS_FILE — delete it to regenerate (rotates all secrets)"
else
    KEY_USER="$(gen_hex 32)"
    KEY_ADMIN="$(gen_hex 32)"
    PG_PASSWORD="$(gen_hex 16)"
fi

# --------------------------------------------------------------------------
# Rendered artifacts. Rendering is the single source for both the dry-run
# plan and the files written in real mode.
# --------------------------------------------------------------------------

render_secrets() {
    cat <<EOF
# secrets.env — tenant '$NAME' (generated ONCE by apptainer/new-tenant.sh)
# Deleting this file makes the next run rotate every secret.
TENANT_API_KEY_USER=$KEY_USER
TENANT_API_KEY_ADMIN=$KEY_ADMIN
TENANT_PG_PASSWORD=$PG_PASSWORD
EOF
}

render_env() {
    cat <<EOF
# ==========================================================================
# tenant.env — tenant '$NAME' (generated by apptainer/new-tenant.sh)
#
# Load with:   set -a; . $ENV_FILE; set +a
# Never pipe through xargs — it strips the quotes around JSON values.
# JSON values MUST stay single-quoted (this file is shell-sourced).
# Re-running new-tenant.sh KEEPS an edited copy of this file (use --force
# to overwrite); it never re-generates the API keys or the DB password.
# ==========================================================================

# --- identity / authz -----------------------------------------------------
API_KEYS='["$KEY_USER","$KEY_ADMIN"]'
API_KEY_TENANTS='{"$KEY_USER":"$NAME","$KEY_ADMIN":"$NAME"}'
API_KEY_ROLES='{"$KEY_USER":"user","$KEY_ADMIN":"admin"}'
DEFAULT_ROLE=user
IDENTITY_PROVIDER=none
# To accept bearer identities instead, pick one and fill in the pins:
# IDENTITY_PROVIDER=oidc
# IDENTITY_OIDC_ISSUER=https://accounts.example.com
# IDENTITY_OIDC_CLIENT_IDS='["<client-id>"]'
# IDENTITY_PROVIDER=bvbrc    # issuer allowlist is pinned in config.py
MAX_COLLECTIONS=100
EOF
    if [[ "$STORE_KIND" == postgres ]]; then
        cat <<EOF
USER_STORE_BACKEND=postgres
USER_STORE_DSN=postgresql://$NAME:$PG_PASSWORD@$PG_HOST:$PG_PORT/$NAME
EOF
    else
        cat <<EOF
USER_STORE_BACKEND=sqlite
USER_STORE_PATH=$TDIR/state/ragstack_users.db
EOF
    fi
    cat <<EOF
ACL_BACKFILL_OWNER=legacy:admin

# --- dedicated stores (ADR-0005: data at rest defines the tenant) ---------
VECTOR_BACKEND=qdrant
QDRANT_URL=http://localhost:$PORT_QDRANT_HTTP
TEXT_BACKEND=elasticsearch
ELASTICSEARCH_URL=http://localhost:$PORT_ES_HTTP
GRAPH_BACKEND=disabled
EOF
    if [[ "$STORE_KIND" == postgres ]]; then
        cat <<EOF
JOB_STORE_BACKEND=postgres
POSTGRES_DSN=postgresql+asyncpg://$NAME:$PG_PASSWORD@$PG_HOST:$PG_PORT/$NAME
COLLECTION_STORE_BACKEND=postgres
COLLECTION_STORE_DSN=postgresql://$NAME:$PG_PASSWORD@$PG_HOST:$PG_PORT/$NAME
EOF
    else
        cat <<EOF
JOB_STORE_BACKEND=sqlite
JOB_STORE_PATH=$TDIR/state/ragstack_jobs.db
COLLECTION_STORE_BACKEND=sqlite
COLLECTION_STORE_PATH=$TDIR/state/ragstack_collections.db
EOF
    fi
    cat <<EOF
COLLECTION_MANIFEST_DIR=$TDIR/manifests
# COLLECTIONS_FILE=$TDIR/config/collections.json   # optional one-time registry seed

# --- embedding / rerank (shared stateless compute, per ADR-0005) ----------
EMBEDDING_API=openai
EMBEDDING_ENDPOINTS='["http://localhost:9001","http://localhost:9002"]'
EMBEDDING_MODEL=Salesforce/SFR-Embedding-Mistral
EMBEDDING_MODEL_DIM=4096
EMBEDDING_SIDECAR_URL=http://localhost:50053
CROSSENCODER_SIDECAR_URL=http://localhost:50052

# --- safety ----------------------------------------------------------------
REQUIRE_DURABLE_BACKENDS=true
INGEST_ROOT=$TDIR/ingest
MAX_DOCUMENT_BYTES=50000000

# --- API -------------------------------------------------------------------
# PORT is informational only — the API's Settings does not read it; uvicorn
# needs --port $PORT_API explicitly (see the start command below/in the plan).
PORT=$PORT_API
LOG_LEVEL=info
EOF
}

render_up_sh() {
    cat <<EOF
#!/usr/bin/env bash
# Start the dedicated stores for tenant '$NAME'.
# Generated by apptainer/new-tenant.sh — do not edit; re-run it to regenerate.
#
# Instances: qdrant-$NAME (http :$PORT_QDRANT_HTTP, grpc :$PORT_QDRANT_GRPC)
#            elasticsearch-$NAME (http :$PORT_ES_HTTP, transport :$PORT_ES_TRANSPORT)
set -euo pipefail
IMG="$IMG"
TDIR="$TDIR"
EOF
    cat <<'EOF'

require_sif() {
    [[ -f "$1" ]] || { echo "ERROR: missing $1 — run apptainer/pull.sh"; exit 1; }
}

already_running() {
    apptainer instance list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$1"
}

# Seed an empty host bind dir from the image's copy of the same path.
seed_if_empty() {
    local sif="$1" container_path="$2" host_path="$3"
    if [[ -n "$(ls -A "$host_path" 2>/dev/null)" ]]; then return; fi
    echo "  seeding $host_path from $sif:$container_path"
    apptainer exec --bind "$host_path:/__seed" "$sif" \
        sh -c "cp -R $container_path/. /__seed/"
}

start() {
    local name="$1" sif="$2"; shift 2
    require_sif "$sif"
    if already_running "$name"; then
        echo "[$name] already running — skipping"
        return
    fi
    # Split remaining args at `--`: before are apptainer options,
    # after are runscript args (passed to the container CMD).
    local opts=() args=() mode=opts
    for a in "$@"; do
        if [[ "$a" == "--" ]]; then mode=args; continue; fi
        if [[ "$mode" == opts ]]; then opts+=("$a"); else args+=("$a"); fi
    done
    echo "[$name] starting"
    apptainer instance run "${opts[@]}" "$sif" "$name" "${args[@]}"
}

mmc=$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)
if (( mmc < 262144 )); then
    echo "WARN: vm.max_map_count=$mmc (<262144) — Elasticsearch will fail to start."
    echo "      Fix once with: sudo sysctl -w vm.max_map_count=262144"
fi
EOF
    cat <<EOF

# Apptainer has no --cwd flag and qdrant's CMD is CWD-relative — cd first.
# Ports via double-underscore envs (safe: no dots for the shell to mangle).
start qdrant-$NAME "\$IMG/qdrant.sif" \\
    --bind "\$TDIR/qdrant/storage:/qdrant/storage" \\
    --bind "\$TDIR/qdrant/snapshots:/qdrant/snapshots" \\
    --env QDRANT__SERVICE__HTTP_PORT=$PORT_QDRANT_HTTP \\
    --env QDRANT__SERVICE__GRPC_PORT=$PORT_QDRANT_GRPC \\
    -- /bin/sh -c 'cd /qdrant && exec ./entrypoint.sh'

# ES config must carry the image's files before first start (an empty bind
# breaks startup).
seed_if_empty "\$IMG/elasticsearch.sif" /usr/share/elasticsearch/config \\
    "\$TDIR/elasticsearch/config"

# Dotted ES settings CANNOT go through --env (apptainer shell-sources it) —
# use native -E args, and call docker-entrypoint.sh directly: /bin/tini
# greedily parses -E as its own option.
start elasticsearch-$NAME "\$IMG/elasticsearch.sif" \\
    --bind "\$TDIR/elasticsearch/data:/usr/share/elasticsearch/data" \\
    --bind "\$TDIR/elasticsearch/logs:/usr/share/elasticsearch/logs" \\
    --bind "\$TDIR/elasticsearch/config:/usr/share/elasticsearch/config" \\
    --env ES_JAVA_OPTS="-Xms$ES_HEAP -Xmx$ES_HEAP" \\
    -- /usr/local/bin/docker-entrypoint.sh eswrapper \\
        -Ediscovery.type=single-node \\
        -Expack.security.enabled=false \\
        -Ehttp.port=$PORT_ES_HTTP \\
        -Etransport.port=$PORT_ES_TRANSPORT

echo
apptainer instance list
EOF
}

render_down_sh() {
    cat <<EOF
#!/usr/bin/env bash
# Stop the dedicated stores for tenant '$NAME'. Idempotent.
# Generated by apptainer/new-tenant.sh — do not edit; re-run it to regenerate.
set -euo pipefail
for name in qdrant-$NAME elasticsearch-$NAME; do
    if apptainer instance stop "\$name" 2>/dev/null; then
        echo "[\$name] stopped"
    else
        echo "[\$name] not running"
    fi
done
EOF
}

# Guarded psql statements — each step checked before create, so a half-
# provisioned tenant is completable and re-runs change nothing.
render_psql_plan() {
    local pw="$1"
    cat <<EOF
psql "\$PG_ADMIN_DSN" -v ON_ERROR_STOP=1 -tAc "SELECT 1 FROM pg_roles WHERE rolname='$NAME'" | grep -q 1 \\
  || psql "\$PG_ADMIN_DSN" -v ON_ERROR_STOP=1 -c "CREATE ROLE \"$NAME\" LOGIN PASSWORD '$pw'"
psql "\$PG_ADMIN_DSN" -v ON_ERROR_STOP=1 -tAc "SELECT 1 FROM pg_database WHERE datname='$NAME'" | grep -q 1 \\
  || psql "\$PG_ADMIN_DSN" -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$NAME\" OWNER \"$NAME\""
EOF
}

# --------------------------------------------------------------------------
# Plan (printed for --dry-run and before real provisioning).
# --------------------------------------------------------------------------

print_plan() {
    echo "== new-tenant plan: $NAME =="
    echo "RAG_DATA:    $DATA"
    echo "RAG_IMAGES:  $IMG"
    echo "tenant dir:  $TDIR"
    echo "acl/registry store: $STORE_KIND"
    echo "port block:  base $BASE, stride $PORT_STRIDE ($ALLOC_SOURCE, index $IDX)"
    echo
    echo "-- ports --"
    echo "api:            $PORT_API"
    echo "qdrant http:    $PORT_QDRANT_HTTP"
    echo "qdrant grpc:    $PORT_QDRANT_GRPC"
    echo "es http:        $PORT_ES_HTTP"
    echo "es transport:   $PORT_ES_TRANSPORT"
    echo "postgres:       $PORT_PG (reserved, unused — sqlite default / shared server via --postgres)"
    echo
    echo "-- manifest row ($MANIFEST) --"
    printf '%s\t%s\t%s\n' "$NAME" "$IDX" "$BASE"
    echo
    echo "-- directories (mkdir -p; every writable path enumerated — house rule: no tmpfs overlays) --"
    printf '%s\n' "${TENANT_DIRS[@]}"
    echo
    echo "-- required images (shared SIFs, reused — run apptainer/pull.sh if missing) --"
    echo "$IMG/qdrant.sif"
    echo "$IMG/elasticsearch.sif"
    if [[ -n "$PG_ADMIN_DSN" ]]; then
        echo
        echo "-- postgres provisioning (server $PG_HOST:$PG_PORT via --postgres DSN) --"
        render_psql_plan "$PG_PASSWORD"
    fi
    echo
    echo "-- file: $UP_SH --"
    render_up_sh
    echo
    echo "-- file: $DOWN_SH --"
    render_down_sh
    echo
    echo "-- file: $ENV_FILE --"
    render_env
    echo
    echo "-- start/stop --"
    echo "start stores:  $UP_SH"
    echo "stop stores:   $DOWN_SH"
    echo "start API:     set -a; . $ENV_FILE; set +a"
    echo "               uvicorn ragstack.api.main:app --host 0.0.0.0 --port $PORT_API"
}

if (( DRY_RUN )); then
    print_plan
    exit 0
fi

# --------------------------------------------------------------------------
# Real provisioning — every step independently guarded (idempotent).
# --------------------------------------------------------------------------

# Write $2 (content) to $1 iff missing or different. mode: keep|overwrite.
#   keep      — an existing, differing file is KEPT (diff printed; --force overrides).
#   overwrite — a differing file is replaced (derived artifacts, deterministic).
stamp_file() {
    local path="$1" content="$2" mode="$3" label="$4"
    local tmp
    tmp="$(mktemp "$path.XXXXXX")"
    printf '%s\n' "$content" > "$tmp"
    if [[ ! -f "$path" ]]; then
        mv "$tmp" "$path"
        echo "[$label] created $path"
    elif cmp -s "$tmp" "$path"; then
        rm -f "$tmp"
        echo "[$label] unchanged $path"
    elif [[ "$mode" == keep && $FORCE -eq 0 ]]; then
        warn "$path differs from the generated content — KEEPING the existing file."
        warn "Re-run with --force to overwrite. Diff (existing vs generated, secrets redacted):"
        # NEVER print the raw diff: a hunk touching any line within 3 lines of the
        # key block prints live API_KEYS/DSN/PASSWORD material, which a routine
        # `new-tenant.sh ... | tee provision.log` then captures to disk. Redact the
        # value of every secret-shaped assignment before it reaches stderr.
        diff -u "$path" "$tmp" 2>/dev/null \
            | sed -E 's/^([-+ ]?(#[[:space:]]*)?[A-Z_]*(API_KEY[A-Z_]*|KEY|SECRET|PASSWORD|TOKEN|DSN)[A-Z_]*=).*/\1<REDACTED>/' \
            >&2 || true
        rm -f "$tmp"
    else
        mv "$tmp" "$path"
        echo "[$label] updated $path"
    fi
}

echo "== provisioning tenant: $NAME ($STORE_KIND, port base $BASE, $ALLOC_SOURCE) =="

# 1. Directories — mkdir -p is always safe.
mkdir -p "${TENANT_DIRS[@]}" "$DATA/tenants"
# Shared HPC host: the tenant tree holds the ACL/user sqlite DBs and the corpus
# itself. 0755 would make all of it world-traversable to every account on the
# box. The env files are already 0600; this closes the directory around them.
chmod 700 "$TDIR" 2>/dev/null || warn "could not chmod 700 $TDIR"

# 2. Split-brain guard: if a tenant.env exists and will be KEPT, its ports
# must match this run's allocation. Otherwise (e.g. the manifest row was
# lost/edited and a re-run allocated a fresh block) up.sh would be
# regenerated at the NEW ports while the API keeps dialing the OLD ones —
# die before writing anything instead of silently splitting the tenant.
if [[ -f "$ENV_FILE" && $FORCE -eq 0 ]]; then
    for want in "QDRANT_URL=http://localhost:$PORT_QDRANT_HTTP" \
                "ELASTICSEARCH_URL=http://localhost:$PORT_ES_HTTP" \
                "PORT=$PORT_API"; do
        key="${want%%=*}"
        have="$(grep -E "^$key=" "$ENV_FILE" | tail -n1 || true)"
        if [[ -n "$have" && "$have" != "$want" ]]; then
            die "port split-brain for tenant '$NAME': $ENV_FILE has '$have' but this run allocated '$want'.
The manifest row in $MANIFEST was likely lost or edited. Restore the row matching tenant.env
(name<TAB>index<TAB>base_port), or delete $ENV_FILE / re-run with --force to move the tenant
to the newly allocated block."
        fi
    done
fi

# 3. Manifest — append the row only if this is a new allocation (atomic,
# and serialized by the flock taken before the allocation read).
if [[ "$ALLOC_SOURCE" == "new allocation" ]]; then
    tmp="$(mktemp "$MANIFEST.XXXXXX")"
    {
        if [[ -f "$MANIFEST" ]]; then
            cat "$MANIFEST"
        else
            printf '# tenant\tindex\tbase_port  (owned by apptainer/new-tenant.sh — do not edit)\n'
        fi
        printf '%s\t%s\t%s\n' "$NAME" "$IDX" "$BASE"
    } > "$tmp"
    mv "$tmp" "$MANIFEST"
    echo "[manifest] allocated index $IDX, base $BASE -> $MANIFEST"
else
    echo "[manifest] reusing index $IDX, base $BASE from $MANIFEST"
fi
# Allocation is durable — release the manifest lock explicitly so --start
# children (daemonized apptainer instances) can never inherit and hold it.
exec 9>&-

# 4. Provisioning settings — persisted so a flagless re-run keeps --es-heap
# and the store kind (see the provision.env section above).
stamp_file "$PROVISION_FILE" "$(render_provision)" overwrite provision
chmod 600 "$PROVISION_FILE"

# 5. Secrets — generated once, never rotated by a re-run.
if [[ ! -f "$SECRETS_FILE" ]]; then
    stamp_file "$SECRETS_FILE" "$(render_secrets)" overwrite secrets
    chmod 600 "$SECRETS_FILE"
else
    echo "[secrets] unchanged $SECRETS_FILE"
fi

# 6. Env file — the one operator-editable artifact: keep edits unless --force.
stamp_file "$ENV_FILE" "$(render_env)" keep tenant.env
chmod 600 "$ENV_FILE"

# 7. Start/stop wrappers — derived, deterministic; safe to regenerate.
stamp_file "$UP_SH" "$(render_up_sh)" overwrite up.sh
stamp_file "$DOWN_SH" "$(render_down_sh)" overwrite down.sh
chmod +x "$UP_SH" "$DOWN_SH"

# 8. Postgres database + role (only with --postgres) — checked before create.
if [[ -n "$PG_ADMIN_DSN" ]]; then
    command -v psql >/dev/null 2>&1 || die "psql not found — required for --postgres"
    # The tenant password goes in over STDIN, never on psql's argv: /proc/*/cmdline
    # is world-readable on a shared host, so an argv password is visible to every
    # user for the life of the call. `grep -c` (not `grep -q`) avoids a SIGPIPE
    # early-exit turning into a pipefail 141 that would misread "role exists".
    if [[ "$(psql "$PG_ADMIN_DSN" -v ON_ERROR_STOP=1 -tAc \
            "SELECT 1 FROM pg_roles WHERE rolname='$NAME'" | grep -c 1)" != "0" ]]; then
        echo "[postgres] role '$NAME' exists"
    else
        printf 'CREATE ROLE %s LOGIN PASSWORD %s;\n' \
            "\"$NAME\"" "'$PG_PASSWORD'" \
            | psql "$PG_ADMIN_DSN" -v ON_ERROR_STOP=1 -f - >/dev/null
        echo "[postgres] created role '$NAME'"
    fi
    if [[ "$(psql "$PG_ADMIN_DSN" -v ON_ERROR_STOP=1 -tAc \
            "SELECT 1 FROM pg_database WHERE datname='$NAME'" | grep -c 1)" != "0" ]]; then
        echo "[postgres] database '$NAME' exists"
    else
        psql "$PG_ADMIN_DSN" -v ON_ERROR_STOP=1 \
            -c "CREATE DATABASE \"$NAME\" OWNER \"$NAME\"" >/dev/null
        echo "[postgres] created database '$NAME'"
    fi
fi

# 9. Preflight warnings (never fatal — provisioning is complete without them).
for sif in "$IMG/qdrant.sif" "$IMG/elasticsearch.sif"; do
    [[ -f "$sif" ]] || warn "missing $sif — run apptainer/pull.sh before starting (SIFs are shared across tenants)"
done
mmc="$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)"
if (( mmc < 262144 )); then
    warn "vm.max_map_count=$mmc (<262144) — Elasticsearch will fail to start."
    warn "Fix once with: sudo sysctl -w vm.max_map_count=262144"
fi

echo
echo "== tenant '$NAME' provisioned =="
echo "start stores:  $UP_SH"
echo "stop stores:   $DOWN_SH"
echo "start API:     set -a; . $ENV_FILE; set +a"
echo "               uvicorn ragstack.api.main:app --host 0.0.0.0 --port $PORT_API"

if (( START )); then
    echo
    bash "$UP_SH"
fi
