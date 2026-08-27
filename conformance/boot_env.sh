#!/usr/bin/env bash
# Shared boot preconditions for the conformance runner scripts (#432).
#
# `source` this, don't execute it. It fixes two things that made a self-booted
# conformance run prove less than it claimed:
#
# 1. **Which code the server runs.** `run_authz_keyed.sh` booted uvicorn from
#    the caller's CWD (the repo root under `make`) and only `cd`'d into
#    `conformance/` afterwards. From the repo root, `import ragstack` on this
#    host resolves through the conda env's editable install to
#    `/rag/repos/ragstack/python` — a legacy PRODUCTION checkout. So every
#    `make test-conformance-authz` run to date contract-tested that checkout,
#    not the branch. `ragstack_pin_pythonpath` + `ragstack_assert_import_origin`
#    make the server's import origin explicit and *checked*, in the same
#    interpreter and CWD the boot will use.
#
# 2. **Which services the server talks to.** `rerank_enabled` defaults true and
#    the real lifespan makes a live `GET {crossencoder_sidecar_url}/health`; the
#    runners pinned no store or sidecar URL, and on this host :50052 is a live
#    cross-encoder. `ragstack_pin_dead_backends` points every outbound default
#    at 127.0.0.1:1 — the in-memory backends these runners select need none of
#    them, so anything that does reach out fails loudly instead of touching a
#    real service. Keep in sync with `python/tests/pinned_env_support.py`.

# Absolute path to this checkout's `python/` directory. $1 is the conformance/
# dir (each script's own $HERE).
ragstack_py_dir() {
  (cd "$1/../python" && pwd)
}

# Prepend this checkout to PYTHONPATH for everything launched from here on.
ragstack_pin_pythonpath() {
  local py_dir="$1"
  export PYTHONPATH="${py_dir}${PYTHONPATH:+:${PYTHONPATH}}"
}

# Point every store/model URL `ragstack.config` defaults to a live local port at
# a dead one. Exported, so the server inherits them.
ragstack_pin_dead_backends() {
  local dead="http://127.0.0.1:1"
  export QDRANT_URL="$dead"
  export ELASTICSEARCH_URL="$dead"
  export EMBEDDING_SIDECAR_URL="$dead"
  export CROSSENCODER_SIDECAR_URL="$dead"
  export FAISS_SIDECAR_URL="$dead"
  export GOWE_URL="$dead"
  export WORKSPACE_URL="$dead"
  export NEO4J_URI="bolt://127.0.0.1:1"
  export REDIS_URL="redis://127.0.0.1:1"
}

# Abort, naming both paths, unless $2 (the interpreter that is about to boot the
# server) imports `ragstack` from inside $1. Run this with the same environment
# and CWD as the boot, or it proves nothing about the boot.
#   $1 py_dir  $2 interpreter  $3 log tag
ragstack_assert_import_origin() {
  local py_dir="$1" python="$2" tag="$3"
  "$python" - "$py_dir" "$tag" <<'PY' || exit 1
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
tag = sys.argv[2]

try:
    import ragstack
except Exception as exc:
    print(f"[{tag}] ABORT: cannot import `ragstack` at all: {exc!r}", file=sys.stderr)
    print(f"[{tag}]   expected under: {root}", file=sys.stderr)
    sys.exit(1)

origin = getattr(ragstack, "__file__", None)
if origin is None:
    print(f"[{tag}] ABORT: `ragstack` has no __file__ (namespace package shadowing "
          f"the checkout)", file=sys.stderr)
    print(f"[{tag}]   expected under: {root}", file=sys.stderr)
    sys.exit(1)

imported = pathlib.Path(origin).resolve()
if not imported.is_relative_to(root):
    print(f"[{tag}] ABORT: the server would boot the WRONG `ragstack` (#432).",
          file=sys.stderr)
    print(f"[{tag}]   would import: {imported}", file=sys.stderr)
    print(f"[{tag}]   expected under: {root}", file=sys.stderr)
    print(f"[{tag}] A conformance run against another checkout's code is not "
          f"evidence about this one.", file=sys.stderr)
    sys.exit(1)

print(f"[{tag}] import origin OK: {imported}")
PY
}
