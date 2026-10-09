#!/usr/bin/env bash
# build-tools-image.sh — kept for existing docs and callers: exactly
#   apptainer/build-image.sh --kind tools "$@"
# Flags, output names, build numbering and the receipt are build-image.sh's;
# see its header (or `apptainer/build-image.sh --help`).
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/build-image.sh" --kind tools "$@"
