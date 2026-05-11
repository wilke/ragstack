#!/usr/bin/env bash
# Stop the RAGStack infra Apptainer instances. Idempotent.

set -euo pipefail
for name in qdrant elasticsearch neo4j postgres redis; do
    if apptainer instance stop "$name" 2>/dev/null; then
        echo "[$name] stopped"
    else
        echo "[$name] not running"
    fi
done
