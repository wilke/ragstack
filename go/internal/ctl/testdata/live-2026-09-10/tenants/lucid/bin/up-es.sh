#!/usr/bin/env bash
# Dedicated Elasticsearch for tenant 'lucid'.
#
# ES ONLY: lucid keeps the shared Qdrant on :6343 (which it shares with the
# lucid PRODUCTION api on :8010). This script gives it its own text leg so the
# BM25 side stops living in the communal :9200 alongside both production
# corpora. Ports 24003/24004 are lucid's block (+3/+4 of base 24000); +1/+2 stay
# reserved and unused for as long as the vector leg is shared.
#
# House rules (MEMORY.md / apptainer/up.sh):
#   - dotted ES settings go as NATIVE -E args, never --env: apptainer
#     shell-sources --env and mangles dotted keys
#   - call docker-entrypoint.sh directly; /bin/tini eats -E flags
#   - every writable path is bind-mounted to an enumerated host dir, no tmpfs
#   - the config bind MUST be seeded from the image or ES will not boot
set -euo pipefail
IMG="/rag/apptainer/images"
TDIR="/rag/data/tenants/lucid"
NAME="elasticsearch-lucid"

[[ -f "$IMG/elasticsearch.sif" ]] || { echo "ERROR: missing $IMG/elasticsearch.sif — run apptainer/pull.sh"; exit 1; }

if apptainer instance list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$NAME"; then
    echo "[$NAME] already running — skipping"; exit 0
fi

mmc=$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)
(( mmc >= 262144 )) || echo "WARN: vm.max_map_count=$mmc (want >= 262144)"

mkdir -p "$TDIR/elasticsearch/data" "$TDIR/elasticsearch/logs" "$TDIR/elasticsearch/config"
if [[ -z "$(ls -A "$TDIR/elasticsearch/config" 2>/dev/null)" ]]; then
    echo "  seeding config from the image"
    apptainer exec --bind "$TDIR/elasticsearch/config:/__seed" "$IMG/elasticsearch.sif" \
        sh -c 'cp -R /usr/share/elasticsearch/config/. /__seed/'
fi

# 2g, not the 512m default: list_documents runs a composite terms agg with a
# top_hits sub-agg over 1.55M docs, which is the heaviest heap consumer here,
# and this host has ~1.4TB free.
echo "[$NAME] starting (http :24003, transport :24004, heap 2g)"
apptainer instance run \
    --bind "$TDIR/elasticsearch/data:/usr/share/elasticsearch/data" \
    --bind "$TDIR/elasticsearch/logs:/usr/share/elasticsearch/logs" \
    --bind "$TDIR/elasticsearch/config:/usr/share/elasticsearch/config" \
    --env ES_JAVA_OPTS="-Xms2g -Xmx2g" \
    "$IMG/elasticsearch.sif" "$NAME" \
    /usr/local/bin/docker-entrypoint.sh eswrapper \
        -Ediscovery.type=single-node \
        -Expack.security.enabled=false \
        -Ehttp.port=24003 \
        -Etransport.port=24004
