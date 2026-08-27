#!/usr/bin/env bash
# Boot a keyed, in-memory RAGStack API with FOUR DISTINCT principals and run
# conformance against it, then tear the server down.
#
# Why this exists: the standard `test-conformance-*` targets point at a running
# server but set no API keys, so every 401/403 assertion in test_authz.py hits
# its `if not RAGSTACK_API_KEY: skip` guard — the suite reports green while
# proving nothing (#88).
#
# Why it was rewritten (#405): the first version wired `RAGSTACK_API_KEY` and
# `RAGSTACK_API_KEY_NONADMIN` to the SAME VALUE. The suite had two names for one
# principal, so its only real axis was *role* — and P2, the caller whose
# readable set excludes the registry pointer's target, was inexpressible. That
# is why #419 and #420 shipped: their branch was unreachable from any suite.
# This version provisions four principals with distinct keys and distinct
# subjects, plus a collection topology in which P2 genuinely cannot read the
# tenant default:
#
#   the pointer target  the settings-derived collection. Ownerless, so the
#                       startup ACL backfill publishes it `read` to `public` —
#                       which would make EVERY principal able to read it and the
#                       persona vacuous. provision_keyed.py revokes that grant as
#                       admin and verifies from P2's own listing that it took.
#   conf-p2-mine        created by the P2 fixture, as P2, over HTTP.
#
# Env knobs:
#   AUTHZ_CONF_PORT        API port (default 8137)
#   AUTHZ_CONF_SCOPE       pytest selection (default `test_authz.py`; the
#                          `test-conformance-keyed` target passes `.`)
#   AUTHZ_CONF_CREATE_GATE 1 → also run the A3 create-gate phase (second boot)
#   PYTHON                 interpreter (default `python`)
# Extra args are forwarded to pytest: `conformance/run_authz_keyed.sh -v -k admin`.
set -euo pipefail

PORT="${AUTHZ_CONF_PORT:-8137}"
HOST=127.0.0.1
PYTHON="${PYTHON:-python}"
SCOPE="${AUTHZ_CONF_SCOPE:-test_authz.py}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # the conformance/ dir
WORK="$(mktemp -d -t authz-conf-XXXXXX)"
chmod 700 "$WORK"
LOG="$WORK/api.log"
PYTEST_OUT="$WORK/pytest.out"

SERVER_PID=
STUB_PID=
GATE_PID=
cleanup() {
  # Stop by the pid recorded AT LAUNCH. Never by process-name pattern: every
  # scratch server on this host shares a command line with production, and one
  # `pkill -f uvicorn` took the whole fleet down for 17 hours (#402).
  for pid in "$SERVER_PID" "$GATE_PID" "$STUB_PID"; do
    [[ -n "$pid" ]] || continue
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  rm -rf "$WORK"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------- #
# Principals
# ---------------------------------------------------------------------------- #
# Distinct, obviously-synthetic, and unguessable — one generator for all four so
# no reader has to wonder whether one of them is special.
gen_key() {
  printf 'conformance-%s-%s' "$1" \
    "$(head -c 18 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9')"
}
ADMIN_KEY="$(gen_key admin)"
NONADMIN_KEY="$(gen_key nonadmin)"
P2_KEY="$(gen_key p2)"
B_KEY="$(gen_key b)"

ADMIN_SUBJECT=conf-admin
NONADMIN_SUBJECT=conf-nonadmin
P2_SUBJECT=conf-p2
B_SUBJECT=conf-b

# Four names must be four values. Asserted rather than assumed: the bug this
# script is being rewritten to fix was two of them holding one value, and it
# survived two years of green runs because nothing ever looked.
_distinct="$(printf '%s\n%s\n%s\n%s\n' \
  "$ADMIN_KEY" "$NONADMIN_KEY" "$P2_KEY" "$B_KEY" | sort -u | wc -l)"
if [[ "$_distinct" -ne 4 ]]; then
  echo "[authz-conf] ABORT: the four principals do not have four distinct keys." >&2
  echo "[authz-conf] Two names for one principal is the #405 defect itself; a" >&2
  echo "[authz-conf] suite wired that way proves role and nothing else." >&2
  exit 1
fi

# Mode-600, inside a mode-700 dir: these keys are synthetic and die with the run,
# but they are live credentials for a listening server while it exists.
KEYFILE="$WORK/keys.env"
(
  umask 077
  cat > "$KEYFILE" <<EOF
API_KEYS='["$ADMIN_KEY","$NONADMIN_KEY","$P2_KEY","$B_KEY"]'
API_KEY_ROLES='{"$ADMIN_KEY":"admin"}'
API_KEY_TENANTS='{"$ADMIN_KEY":"$ADMIN_SUBJECT","$NONADMIN_KEY":"$NONADMIN_SUBJECT","$P2_KEY":"$P2_SUBJECT","$B_KEY":"$B_SUBJECT"}'
EOF
)
chmod 600 "$KEYFILE"

# No key reaches a terminal or a CI log, whatever the server printed. Covers
# `API_KEYS=` as well as `KEY=`/`TOKEN=` — a leak already happened through
# exactly that gap — and substitutes the generated values themselves, which is
# the part a pattern can miss.
redact() {
  sed -E \
    -e "s|$ADMIN_KEY|<admin-key>|g" \
    -e "s|$NONADMIN_KEY|<nonadmin-key>|g" \
    -e "s|$P2_KEY|<p2-key>|g" \
    -e "s|$B_KEY|<b-key>|g" \
    -e 's/((API_KEYS|API_KEY_ROLES|API_KEY_TENANTS|[A-Za-z_]*(KEY|TOKEN|SECRET|PASSWORD))[[:space:]]*[=:][[:space:]]*)[^[:space:]]+/\1<redacted>/g'
}

# ---------------------------------------------------------------------------- #
# What the server imports, and what it may talk to (#432)
# ---------------------------------------------------------------------------- #
# The boot below used to run from the CALLER's CWD — the repo root under `make`
# — where `import ragstack` on this host resolved to the legacy production
# checkout, so every run of this target was contract-testing that code and not
# the branch. See boot_env.sh.
# shellcheck source=conformance/boot_env.sh
source "$HERE/boot_env.sh"
PY_DIR="$(ragstack_py_dir "$HERE")"
ragstack_pin_pythonpath "$PY_DIR"
ragstack_pin_dead_backends
ragstack_assert_import_origin "$PY_DIR" "$PYTHON" "authz-conf"

# ---------------------------------------------------------------------------- #
# A stub embedder, because a dead one is not a skip — it is a 500
# ---------------------------------------------------------------------------- #
# `ragstack_pin_dead_backends` points every sidecar at 127.0.0.1:1, which is
# right: on this host :50052 and :50053 carry live services. But /v1/query,
# /v1/retrieve, /v1/chunks and the context-window surface need SOME embedder,
# and against a dead one they 500 — so a keyed full-suite run would either fail
# on them or skip them, and skipping is how this suite got hollow in the first
# place. The stub speaks the sidecar's wire contract over a hash. It is not a
# model, and this run makes no retrieval-quality claim (that is an L-layer
# claim; use-case matrix F5).
EMB_DIM=8
EMB_PORT="$("$PYTHON" - <<'PY'
import socket

s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"
"$PYTHON" "$HERE/stub_embedding_sidecar.py" "$EMB_PORT" "$EMB_DIM" \
  >"$WORK/stub.log" 2>&1 &
STUB_PID=$!
export EMBEDDING_SIDECAR_URL="http://127.0.0.1:$EMB_PORT"
export EMBEDDING_API=sidecar
export EMBEDDING_MODEL=conformance-stub
export EMBEDDING_MODEL_DIM="$EMB_DIM"
# The reranker's health probe runs in the real lifespan and its sidecar is
# dead-pinned; nothing in this run is testing rerank.
export RERANK_ENABLED=false

export VECTOR_BACKEND=memory TEXT_BACKEND=memory GRAPH_BACKEND=memory
export REQUIRE_DURABLE_BACKENDS=false
export INGEST_ROOT="$WORK/ingest"
mkdir -p "$INGEST_ROOT"

# ---------------------------------------------------------------------------- #
# Boot
# ---------------------------------------------------------------------------- #
#   $1 port  $2 log  $3 tag  $4 pid
wait_for_health() {
  local port="$1" log="$2" tag="$3" pid="$4" ready=
  for _ in $(seq 1 40); do
    if curl -sf -m 2 "http://$HOST:$port/health" >/dev/null 2>&1; then
      ready=1
      break
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "[$tag] server exited during boot:" >&2
      redact < "$log" >&2
      return 1
    fi
    sleep 0.5
  done
  if [[ -z "$ready" ]]; then
    echo "[$tag] server did not become healthy within timeout:" >&2
    redact < "$log" >&2
    return 1
  fi
}

echo "[authz-conf] booting keyed in-memory API on $HOST:$PORT (4 principals) ..."
# shellcheck disable=SC1090  # generated above, mode-600, read once
set -a
source "$KEYFILE"
set +a
"$PYTHON" -m uvicorn ragstack.api.main:app --host "$HOST" --port "$PORT" \
  >"$LOG" 2>&1 &
SERVER_PID=$!
wait_for_health "$PORT" "$LOG" authz-conf "$SERVER_PID"

# ---------------------------------------------------------------------------- #
# The collection topology that makes P2 possible
# ---------------------------------------------------------------------------- #
# The registry pointer names the settings-derived collection, which has no
# recorded owner — so the startup ACL backfill grants it `read` to the built-in
# `public` group, and EVERY principal can read it. A readable pointer target is
# a vacuous P2. Revoke that one grant, as admin, and verify from P2's own
# listing that it took. See provision_keyed.py for the two topologies that were
# tried first and why they are worse.
echo "[authz-conf] making the registry pointer's target private (P2) ..."
"$PYTHON" "$HERE/provision_keyed.py" "http://$HOST:$PORT" \
  "$ADMIN_KEY" "$NONADMIN_KEY" "$NONADMIN_SUBJECT" "$P2_KEY" \
  > "$WORK/provision.out" 2>&1 || { redact < "$WORK/provision.out" >&2; exit 1; }
redact < "$WORK/provision.out"

echo "[authz-conf] server healthy; running conformance ($SCOPE) ..."
cd "$HERE"
status=0
set +e
RAGSTACK_BASE_URL="http://$HOST:$PORT" RAGSTACK_IMPL=python \
RAGSTACK_API_KEY="$ADMIN_KEY" \
RAGSTACK_API_KEY_ADMIN="$ADMIN_KEY" \
RAGSTACK_API_KEY_NONADMIN="$NONADMIN_KEY" \
RAGSTACK_API_KEY_P2="$P2_KEY" \
RAGSTACK_API_KEY_B="$B_KEY" \
  "$PYTHON" -m pytest $SCOPE -rs "$@" > "$PYTEST_OUT" 2>&1
status=$?
set -e
redact < "$PYTEST_OUT"

# ---------------------------------------------------------------------------- #
# The vacuity invariant
# ---------------------------------------------------------------------------- #
# Skips are this suite's silent failure mode: it reported green for two years
# while every authz assertion skipped for want of a key. On a server THIS SCRIPT
# provisioned, "I had no credential for that" is a harness bug — so any skip
# tagged RAGSTACK_CREDENTIAL_SKIP (conftest.skip_no_credential) fails the run.
# Skips for other reasons (no identity provider, a surface an impl lacks) are
# legitimate and untouched; a blanket zero-skip rule would be a permanent red
# rather than a signal. The expected pass/skip COUNTS live in CLAUDE.md, not
# here: a pinned number rots on the next added test; the invariant does not.
if grep -q "RAGSTACK_CREDENTIAL_SKIP" "$PYTEST_OUT"; then
  echo "[authz-conf] ABORT: this run skipped for want of a credential, on a" >&2
  echo "[authz-conf] server it provisioned itself. That is the #405 vacuity:" >&2
  grep "RAGSTACK_CREDENTIAL_SKIP" "$PYTEST_OUT" | redact >&2
  status=1
fi

# ---------------------------------------------------------------------------- #
# Phase 2 — A3: a non-admin cannot create a collection (#287)
# ---------------------------------------------------------------------------- #
# ALLOW_USER_COLLECTION_CREATE is deployment-wide, so it cannot share a boot
# with the P2 persona (whose fixture provisions itself BY creating). Second,
# short-lived server; only test_create_gate.py runs against it.
if [[ "${AUTHZ_CONF_CREATE_GATE:-0}" == "1" ]]; then
  GATE_PORT=$((PORT + 1))
  GATE_LOG="$WORK/gate.log"
  echo "[authz-conf] booting a second API on $HOST:$GATE_PORT with" \
       "ALLOW_USER_COLLECTION_CREATE=false (A3) ..."
  ALLOW_USER_COLLECTION_CREATE=false \
    "$PYTHON" -m uvicorn ragstack.api.main:app --host "$HOST" --port "$GATE_PORT" \
    >"$GATE_LOG" 2>&1 &
  GATE_PID=$!
  wait_for_health "$GATE_PORT" "$GATE_LOG" authz-conf-gate "$GATE_PID"
  set +e
  RAGSTACK_BASE_URL="http://$HOST:$GATE_PORT" RAGSTACK_IMPL=python \
  RAGSTACK_CONFORMANCE_CREATE_DISABLED=1 \
  RAGSTACK_API_KEY="$ADMIN_KEY" \
  RAGSTACK_API_KEY_ADMIN="$ADMIN_KEY" \
  RAGSTACK_API_KEY_NONADMIN="$NONADMIN_KEY" \
    "$PYTHON" -m pytest test_create_gate.py -rs > "$WORK/gate.out" 2>&1
  gate_status=$?
  set -e
  redact < "$WORK/gate.out"
  [[ "$gate_status" -eq 0 ]] || status="$gate_status"
fi

exit "$status"
