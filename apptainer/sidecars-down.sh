#!/usr/bin/env bash
# Stop the ragstack sidecar Apptainer instances. Idempotent.

set -euo pipefail
for name in embedding; do
    if apptainer instance stop "$name" 2>/dev/null; then
        echo "[$name] stopped"
    else
        echo "[$name] not running"
    fi
done
