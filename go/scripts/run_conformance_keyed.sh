#!/usr/bin/env bash
# Boot a keyed Go API on an EPHEMERAL port with four distinct principals and run
# the conformance suite against it, then tear the server down.
#
# The Go counterpart of `conformance/run_authz_keyed.sh`. The standard
# `make test-conformance-go` target points at :8080 and sets no API keys, so
# every 401/403 assertion in test_authz.py takes its "server is keyless" skip
# and `conformance/test_grading.py` — whose whole subject is that reader A
# cannot see reader B's verdict — cannot resolve a principal at all. A suite
# that skips its own subject reports green while proving nothing (#88, #405).
#
# The four principals are the same four names test_grading.py reads:
#
#   RAGSTACK_API_KEY_ADMIN     admin — creates, adjudicates, exports, deletes
#   RAGSTACK_API_KEY_NONADMIN  reader A
#   RAGSTACK_API_KEY_P2        reader B
#   RAGSTACK_API_KEY_B         an authenticated caller on no batch
#
# Unlike the Python script this one provisions no collection topology: the Go
# side has no ownership seam for the P2 persona to be on the far side of, and
# `conformance/test_persona_p2.py` skips for that reason on any Go server.
#
# Env knobs:
#   CONF_SCOPE   pytest selection, relative to conformance/ (default `.`)
#   PYTHON       interpreter with pytest+httpx (default `python3`)
# Extra args are forwarded to pytest.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"      # go/scripts
GO_DIR="$(cd "$HERE/.." && pwd)"                          # go/
REPO="$(cd "$GO_DIR/.." && pwd)"
PYTHON="${PYTHON:-python3}"
SCOPE="${CONF_SCOPE:-.}"
HOST=127.0.0.1

WORK="$(mktemp -d -t go-conf-XXXXXX)"
chmod 700 "$WORK"
LOG="$WORK/api.log"
SERVER_PID=

cleanup() {
  # Stop by the pid recorded AT LAUNCH. NEVER by process-name pattern: every
  # scratch server on the deployment host shares a command line with
  # production, and one `pkill -f` took the whole fleet down for 17 hours
  # (#402).
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

gen_key() {
  printf 'go-conformance-%s-%s' "$1" \
    "$(head -c 18 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9')"
}
ADMIN_KEY="$(gen_key admin)"
NONADMIN_KEY="$(gen_key nonadmin)"
P2_KEY="$(gen_key p2)"
B_KEY="$(gen_key b)"

# Four names must be four values. Asserted rather than assumed: two names for
# one principal is the #405 defect, and it survived two years of green runs
# because nothing ever compared them.
if [[ "$(printf '%s\n%s\n%s\n%s\n' "$ADMIN_KEY" "$NONADMIN_KEY" "$P2_KEY" "$B_KEY" \
        | sort -u | wc -l)" -ne 4 ]]; then
  echo "[go-conf] ABORT: the four principals do not have four distinct keys." >&2
  exit 1
fi

# An EPHEMERAL port, not :8080: the port convention lives in the Make targets,
# and on the deployment host a fixed port is somebody else's server.
PORT="$("$PYTHON" - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"

redact() {
  sed -E \
    -e "s|$ADMIN_KEY|<admin-key>|g" \
    -e "s|$NONADMIN_KEY|<nonadmin-key>|g" \
    -e "s|$P2_KEY|<p2-key>|g" \
    -e "s|$B_KEY|<b-key>|g" \
    -e 's/((API_KEYS|API_KEY_ROLES|API_KEY_TENANTS|[A-Za-z_]*(KEY|TOKEN|SECRET|PASSWORD))[[:space:]]*[=:][[:space:]]*)[^[:space:]]+/\1<redacted>/g'
}

echo "[go-conf] building ..."
(cd "$GO_DIR" && go build -o "$WORK/api" ./cmd/api)

echo "[go-conf] booting keyed Go API on $HOST:$PORT (4 principals) ..."
# Every backend URL is pinned DEAD (127.0.0.1:1). The Go handlers are stubs and
# reach none of them, but the defaults on the deployment host resolve to
# production (#363/#369/#392) and a suite that creates and deletes must never
# have a live one within reach.
PORT="$PORT" \
QDRANT_URL=http://127.0.0.1:1 \
ELASTICSEARCH_URL=http://127.0.0.1:1 \
NEO4J_URI=bolt://127.0.0.1:1 \
POSTGRES_URL=postgres://127.0.0.1:1/none \
REDIS_URL=redis://127.0.0.1:1 \
EMBEDDING_SIDECAR_URL=http://127.0.0.1:1 \
CROSSENCODER_SIDECAR_URL=http://127.0.0.1:1 \
FAISS_SIDECAR_URL=http://127.0.0.1:1 \
API_KEYS="[\"$ADMIN_KEY\",\"$NONADMIN_KEY\",\"$P2_KEY\",\"$B_KEY\"]" \
API_KEY_ROLES="{\"$ADMIN_KEY\":\"admin\"}" \
API_KEY_TENANTS="{\"$ADMIN_KEY\":\"conf-admin\",\"$NONADMIN_KEY\":\"conf-nonadmin\",\"$P2_KEY\":\"conf-p2\",\"$B_KEY\":\"conf-b\"}" \
  "$WORK/api" >"$LOG" 2>&1 &
SERVER_PID=$!

ready=
for _ in $(seq 1 40); do
  if curl -sf -m 2 "http://$HOST:$PORT/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[go-conf] server exited during boot:" >&2
    redact < "$LOG" >&2
    exit 1
  fi
  sleep 0.25
done
if [[ -z "$ready" ]]; then
  echo "[go-conf] server did not become healthy within timeout:" >&2
  redact < "$LOG" >&2
  exit 1
fi

echo "[go-conf] server healthy; running conformance ($SCOPE) ..."
cd "$REPO/conformance"
status=0
set +e
RAGSTACK_BASE_URL="http://$HOST:$PORT" RAGSTACK_IMPL=go \
RAGSTACK_API_KEY="$ADMIN_KEY" \
RAGSTACK_API_KEY_ADMIN="$ADMIN_KEY" \
RAGSTACK_API_KEY_NONADMIN="$NONADMIN_KEY" \
RAGSTACK_API_KEY_P2="$P2_KEY" \
RAGSTACK_API_KEY_B="$B_KEY" \
  "$PYTHON" -m pytest $SCOPE -rs "$@" > "$WORK/pytest.out" 2>&1
status=$?
set -e
redact < "$WORK/pytest.out"

# The vacuity invariant, from run_authz_keyed.sh: on a server THIS SCRIPT
# provisioned, "I had no credential for that" is a harness bug, not a skip.
if grep -q "RAGSTACK_CREDENTIAL_SKIP" "$WORK/pytest.out"; then
  echo "[go-conf] ABORT: this run skipped for want of a credential, on a server" >&2
  echo "[go-conf] it provisioned itself. That is the #405 vacuity:" >&2
  grep "RAGSTACK_CREDENTIAL_SKIP" "$WORK/pytest.out" | redact >&2
  status=1
fi

exit "$status"
