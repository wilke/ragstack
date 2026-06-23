#!/usr/bin/env bash
# Pre-pull the Docker images backing the infra stack as Apptainer SIFs.
# Idempotent: skips images already present under apptainer/images/.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMG="${RAG_IMAGES:-$HERE/images}"
mkdir -p "$IMG"

declare -A IMAGES=(
    [qdrant]="docker://qdrant/qdrant:latest"
    [elasticsearch]="docker://elasticsearch:8.13.4"
    [neo4j]="docker://neo4j:5"
    [postgres]="docker://postgres:16"
    [redis]="docker://redis:7-alpine"
)

for name in qdrant elasticsearch neo4j postgres redis; do
    sif="$IMG/$name.sif"
    if [[ -f "$sif" ]]; then
        echo "[$name] $sif exists — skipping (delete to re-pull)"
        continue
    fi
    echo "[$name] pulling ${IMAGES[$name]} -> $sif"
    apptainer pull "$sif" "${IMAGES[$name]}"
done
