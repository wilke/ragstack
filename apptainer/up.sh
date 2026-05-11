#!/usr/bin/env bash
# Start the RAGStack infrastructure stack via Apptainer.
# Mirrors deploy/docker-compose.infra.yml. Services share the host network,
# so each binds the same port as the Compose mapping (no remap needed).
#
# Persistence model: every directory each service writes to is bind-mounted
# from a host directory under apptainer/data/, so state survives restarts
# and is observable from the host (no opaque tmpfs overlay).
#
# Prereqs:
#   - SIFs in apptainer/images/ (run ./pull.sh first)
#   - vm.max_map_count >= 262144 for Elasticsearch:
#       sudo sysctl -w vm.max_map_count=262144

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="$HERE/data"
IMG="$HERE/images"

# Load repo-root .env if present.
if [[ -f "$HERE/../.env" ]]; then
    set -a; . "$HERE/../.env"; set +a
fi
# Neo4j 5 rejects the literal "neo4j" as a password, so default to something else.
: "${NEO4J_PASSWORD:=ragstack}"
if [[ "$NEO4J_PASSWORD" == "neo4j" ]]; then
    echo "ERROR: NEO4J_PASSWORD cannot be 'neo4j' (Neo4j 5 rejects the default)."
    echo "       Set NEO4J_PASSWORD=... in .env or environment."
    exit 1
fi

# Per-service writable bind mounts. Keep this list authoritative — if a
# service starts complaining about a read-only path, add it here.
mkdir -p \
    "$DATA"/qdrant/{storage,snapshots} \
    "$DATA"/elasticsearch/{data,logs,config} \
    "$DATA"/neo4j/{data,logs,conf} \
    "$DATA"/postgres/{data,run} \
    "$DATA"/redis/data \
    "$IMG"

mmc=$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)
if (( mmc < 262144 )); then
    echo "WARN: vm.max_map_count=$mmc (<262144) — Elasticsearch will fail to start."
    echo "      Fix once with: sudo sysctl -w vm.max_map_count=262144"
fi

require_sif() {
    [[ -f "$1" ]] || { echo "ERROR: missing $1 — run ./pull.sh"; exit 1; }
}

already_running() {
    apptainer instance list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$1"
}

# Seed an empty host bind dir from the image's copy of the same path.
# Needed for paths the image populates with required files (e.g. ES config).
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

# Apptainer has no `--cwd` flag, and qdrant's CMD (`./entrypoint.sh`) plus
# its script (`./qdrant`) are CWD-relative. Override the CMD with a shell
# wrapper that cd's first.
start qdrant "$IMG/qdrant.sif" \
    --bind "$DATA/qdrant/storage:/qdrant/storage" \
    --bind "$DATA/qdrant/snapshots:/qdrant/snapshots" \
    -- /bin/sh -c 'cd /qdrant && exec ./entrypoint.sh'

# ES needs its config dir populated (elasticsearch.yml, jvm.options, ...).
# Seed from the image on first run, then bind for subsequent edits.
seed_if_empty "$IMG/elasticsearch.sif" /usr/share/elasticsearch/config \
    "$DATA/elasticsearch/config"

# ES config keys with dots (discovery.type, xpack.security.enabled) can't be
# passed via --env/--env-file (both go through shell sourcing). Use ES's
# native `-E key=value` CLI args instead. Skip /bin/tini wrapper: it
# greedily consumes `-E` as its own option, and tini isn't PID 1 here.
start elasticsearch "$IMG/elasticsearch.sif" \
    --bind "$DATA/elasticsearch/data:/usr/share/elasticsearch/data" \
    --bind "$DATA/elasticsearch/logs:/usr/share/elasticsearch/logs" \
    --bind "$DATA/elasticsearch/config:/usr/share/elasticsearch/config" \
    --env ES_JAVA_OPTS="-Xms512m -Xmx512m" \
    -- /usr/local/bin/docker-entrypoint.sh eswrapper \
        -Ediscovery.type=single-node \
        -Expack.security.enabled=false

# Neo4j writes to /var/lib/neo4j/conf/neo4j.conf at startup; seed it from
# the image so neo4j.conf and friends are present on first run.
seed_if_empty "$IMG/neo4j.sif" /var/lib/neo4j/conf \
    "$DATA/neo4j/conf"

start neo4j "$IMG/neo4j.sif" \
    --bind "$DATA/neo4j/data:/data" \
    --bind "$DATA/neo4j/logs:/logs" \
    --bind "$DATA/neo4j/conf:/var/lib/neo4j/conf" \
    --env NEO4J_AUTH="neo4j/${NEO4J_PASSWORD}"

# PGDATA points at a subdir so the postgres entrypoint doesn't choke on
# unrelated files in the bind-mount root.
start postgres "$IMG/postgres.sif" \
    --bind "$DATA/postgres/data:/var/lib/postgresql/data" \
    --bind "$DATA/postgres/run:/var/run/postgresql" \
    --env POSTGRES_USER=ragstack \
    --env POSTGRES_PASSWORD=ragstack \
    --env POSTGRES_DB=ragstack \
    --env PGDATA=/var/lib/postgresql/data/pgdata

start redis "$IMG/redis.sif" \
    --bind "$DATA/redis/data:/data"

echo
apptainer instance list
