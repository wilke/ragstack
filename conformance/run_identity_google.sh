#!/usr/bin/env bash
# Boot a RAGStack API configured as a Google OIDC relying party and run the
# identity conformance suite against it, then tear the server down.
#
# Why this exists, and why it is a script rather than a Make variable: the
# identity tests all `skip` when the server under test has no identity provider,
# exactly like test_authz.py skips without RAGSTACK_API_KEY. A suite that skips
# reports green while proving nothing. This script is the counterpart of
# run_authz_keyed.sh — it stands up the configuration the tests need, exports the
# env vars they key off, and FAILS LOUDLY if the configuration is missing, so a
# green run of *this* script is never vacuous.
#
# Required:
#   GOOGLE_OIDC_CLIENT_ID   OAuth 2.0 client id from the Google Cloud console.
#                           This pins `aud`; without it the server refuses to
#                           boot (an unpinned aud accepts ID tokens minted for
#                           any other application on the IdP).
# Optional:
#   GOOGLE_ID_TOKEN         A real Google ID token for that client id. With it,
#                           the positive path (200 + tenant google:<sub>) is
#                           asserted too; without it only the negative paths run.
#                           NOTE: the token is sent only to the local server
#                           booted here — never to any third party.
#   IDENTITY_CONF_PORT      default 8138
#   PYTHON                  default `python`
#
# The server needs outbound HTTPS to accounts.google.com to fetch discovery +
# JWKS. Without it, unverifiable credentials come back 503 rather than 401 —
# which the tests accept, because "I could not check" is a legitimate answer and
# is emphatically not an allow.
set -euo pipefail

PORT="${IDENTITY_CONF_PORT:-8138}"
HOST=127.0.0.1
PYTHON="${PYTHON:-python}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # the conformance/ dir
# Boot from python/ so `ragstack` resolves to THIS checkout. Without it, an
# editable install elsewhere on the host wins and the script silently tests a
# different tree than the one it lives in.
PY_DIR="$(cd "$HERE/../python" && pwd)"
LOG="$(mktemp -t identity-conf-XXXXXX.log)"
INGEST_ROOT="$(mktemp -d -t identity-conf-ingest-XXXXXX)"

if [[ -z "${GOOGLE_OIDC_CLIENT_ID:-}" ]]; then
  cat >&2 <<'EOF'
[identity-conf] GOOGLE_OIDC_CLIENT_ID is not set.

  This suite is only meaningful against a server that is actually configured as
  an OIDC relying party, so it refuses to run a vacuous green pass. Set the
  client id of a Google OAuth 2.0 "Web application" credential:

      GOOGLE_OIDC_CLIENT_ID=<id>.apps.googleusercontent.com \
        conformance/run_identity_google.sh

  Optionally add GOOGLE_ID_TOKEN=<an ID token for that client> to exercise the
  positive path as well. It is sent only to the local server this script boots.
EOF
  exit 2
fi

# A synthetic API key, so the both-credentials-is-400 assertion has a second
# credential to present. Obviously not a production key.
API_KEY="identity-conf-$$"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$INGEST_ROOT" "$LOG"
}
trap cleanup EXIT

echo "[identity-conf] booting Google-OIDC in-memory API on $HOST:$PORT ..."
IDENTITY_PROVIDER=oidc \
IDENTITY_OIDC_ISSUER="https://accounts.google.com" \
IDENTITY_OIDC_CLIENT_IDS="$GOOGLE_OIDC_CLIENT_ID" \
IDENTITY_OIDC_ISSUER_LABEL=google \
API_KEYS="[\"$API_KEY\"]" \
API_KEY_ROLES="{\"$API_KEY\":\"admin\"}" \
VECTOR_BACKEND=memory TEXT_BACKEND=memory GRAPH_BACKEND=memory \
REQUIRE_DURABLE_BACKENDS=false INGEST_ROOT="$INGEST_ROOT" \
  bash -c "cd '$PY_DIR' && exec '$PYTHON' -m uvicorn ragstack.api.main:app \
    --host '$HOST' --port '$PORT'" >"$LOG" 2>&1 &
SERVER_PID=$!

# Wait up to ~20s for /health, failing fast if the server dies during boot (a
# misconfigured identity layer is a boot-time RuntimeError, by design).
ready=
for _ in $(seq 1 40); do
  if curl -sf -m 2 "http://$HOST:$PORT/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[identity-conf] server exited during boot:" >&2
    cat "$LOG" >&2
    exit 1
  fi
  sleep 0.5
done
if [[ -z "$ready" ]]; then
  echo "[identity-conf] server did not become healthy within timeout:" >&2
  cat "$LOG" >&2
  exit 1
fi

echo "[identity-conf] server healthy; running identity conformance ..."
cd "$HERE"
RAGSTACK_BASE_URL="http://$HOST:$PORT" RAGSTACK_IMPL=python \
RAGSTACK_IDENTITY_ENABLED=1 \
RAGSTACK_IDENTITY_ISSUER_LABEL=google \
RAGSTACK_API_KEY="$API_KEY" \
RAGSTACK_GOOGLE_ID_TOKEN="${GOOGLE_ID_TOKEN:-}" \
  "$PYTHON" -m pytest test_identity.py "$@"
