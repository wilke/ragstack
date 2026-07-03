#!/usr/bin/env bash
# Boot a keyed, in-memory RAGStack API and run the authz conformance suite
# against it, then tear the server down.
#
# Why this exists: the standard `test-conformance-*` targets point at a running
# server but set no API keys, so every 401/403 assertion in test_authz.py hits
# its `if not RAGSTACK_API_KEY: skip` guard — the suite reports green while
# proving nothing. This target stands up a self-contained key-protected server
# (memory backends, no external infra) with a known admin + non-admin key, wires
# the three RAGSTACK_API_KEY* env vars the suite reads, and actually exercises
# the gates. Closes the #88 "tests are inert in CI" gap.
#
# Env knobs: AUTHZ_CONF_PORT (default 8137), PYTHON (default `python`). Extra
# args are forwarded to pytest, e.g. `make test-conformance-authz PYTEST_ARGS=-v`
# is not wired, but `conformance/run_authz_keyed.sh -v -k admin` works directly.
set -euo pipefail

PORT="${AUTHZ_CONF_PORT:-8137}"
HOST=127.0.0.1
PYTHON="${PYTHON:-python}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # the conformance/ dir
LOG="$(mktemp -t authz-conf-XXXXXX.log)"
INGEST_ROOT="$(mktemp -d -t authz-conf-ingest-XXXXXX)"

# Distinct, obviously-synthetic keys so a stray run can't be mistaken for prod.
ADMIN_KEY="conformance-admin-$$"
USER_KEY="conformance-user-$$"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$INGEST_ROOT" "$LOG"
}
trap cleanup EXIT

echo "[authz-conf] booting keyed in-memory API on $HOST:$PORT ..."
API_KEYS="[\"$ADMIN_KEY\",\"$USER_KEY\"]" \
API_KEY_ROLES="{\"$ADMIN_KEY\":\"admin\",\"$USER_KEY\":\"researcher\"}" \
VECTOR_BACKEND=memory TEXT_BACKEND=memory GRAPH_BACKEND=memory \
REQUIRE_DURABLE_BACKENDS=false INGEST_ROOT="$INGEST_ROOT" \
  "$PYTHON" -m uvicorn ragstack.api.main:app --host "$HOST" --port "$PORT" \
  >"$LOG" 2>&1 &
SERVER_PID=$!

# Wait up to ~20s for /health, failing fast if the server dies during boot.
ready=
for _ in $(seq 1 40); do
  if curl -sf -m 2 "http://$HOST:$PORT/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[authz-conf] server exited during boot:" >&2
    cat "$LOG" >&2
    exit 1
  fi
  sleep 0.5
done
if [[ -z "$ready" ]]; then
  echo "[authz-conf] server did not become healthy within timeout:" >&2
  cat "$LOG" >&2
  exit 1
fi

echo "[authz-conf] server healthy; running authz conformance ..."
cd "$HERE"
RAGSTACK_BASE_URL="http://$HOST:$PORT" RAGSTACK_IMPL=python \
RAGSTACK_API_KEY="$USER_KEY" \
RAGSTACK_API_KEY_NONADMIN="$USER_KEY" \
RAGSTACK_API_KEY_ADMIN="$ADMIN_KEY" \
  "$PYTHON" -m pytest test_authz.py "$@"
