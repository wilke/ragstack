#!/usr/bin/env bash
# Boot a LOCAL, fixture-backed `ragstack-ctl serve` with its own principals and
# run the control-plane conformance suite (conformance/ctl) against it.
#
# Why this exists: `conformance/ctl/conftest.py` requires RAGSTACK_CTL_URL and
# gives it no default, because the conventional bind on this host
# (127.0.0.1:23990) IS the live control plane — the one process that holds every
# tenant's admin key. So there has to be one obvious, safe way to run the suite,
# and this is it: a daemon on a DIFFERENT port, with --fake-drivers (the recorded
# 2026-09-10 fixture, never this host's real registry), with keys generated for
# this run and dead at the end of it.
#
# It is `run_authz_keyed.sh` for the control plane, and it inherits that script's
# two hard-won rules:
#
#   * principals are PROVEN, not assumed — the suite's conftest checks each key
#     through GET /v1/me and fails if the operator key is not an operator or the
#     viewer key is the operator key under another name (#405);
#   * a skip for want of a credential is a HARNESS BUG on a server this script
#     provisioned itself, so any RAGSTACK_CREDENTIAL_SKIP fails the run (#88).
#
# Env knobs:
#   CTL_CONF_PORT   port for the daemon under test (default 23999 — NOT 23990)
#   CTL_CONF_BIN    the binary (default <repo>/go/bin/ragstack-ctl; `make build-ctl`)
#   PYTHON          interpreter for pytest and the token mint (default `python`)
# Extra args are forwarded to pytest: `conformance/run_ctl_local.sh -v -k authz`.
set -euo pipefail

PORT="${CTL_CONF_PORT:-23999}"
HOST=127.0.0.1
PYTHON="${PYTHON:-python}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # the conformance/ dir
ROOT="$(cd "$HERE/.." && pwd)"
BIN="${CTL_CONF_BIN:-$ROOT/go/bin/ragstack-ctl}"
FIXTURES="$ROOT/contracts/fixtures/identity/bvbrc"

if [[ "$PORT" == "23990" ]]; then
  echo "[ctl-conf] ABORT: 23990 is the LIVE control plane's conventional bind." >&2
  echo "[ctl-conf] This script boots a throwaway daemon; pick another port." >&2
  exit 1
fi
if [[ ! -x "$BIN" ]]; then
  echo "[ctl-conf] ABORT: no ragstack-ctl binary at $BIN — run \`make build-ctl\`." >&2
  exit 1
fi

WORK="$(mktemp -d -t ctl-conf-XXXXXX)"
chmod 700 "$WORK"
LOG="$WORK/ctl.log"
PYTEST_OUT="$WORK/pytest.out"

SERVER_PID=
cleanup() {
  # Stop by the pid recorded AT LAUNCH. Never by process-name pattern: this
  # host runs a REAL ragstack-ctl, and `pkill -f ragstack-ctl` would take the
  # production control plane down with it (the #402 lesson, which cost 17
  # hours when it was `pkill -f uvicorn`).
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------- #
# Principals
# ---------------------------------------------------------------------------- #
# Two ctl API keys — one operator, one viewer — and a BV-BRC bearer that
# VERIFIES for a subject the daemon does not list. The third is what makes
# "valid credential, unknown principal → 403" distinguishable from "malformed
# credential → 401"; without it that test skips and the daemon could be
# defaulting an unknown subject into a role with nobody the wiser.
OPERATOR_KEY="$(openssl rand -hex 32)"
VIEWER_KEY="$(openssl rand -hex 32)"
if [[ "$OPERATOR_KEY" == "$VIEWER_KEY" || -z "$OPERATOR_KEY" || -z "$VIEWER_KEY" ]]; then
  echo "[ctl-conf] ABORT: the two keys are not two distinct values." >&2
  exit 1
fi

# No credential reaches a terminal or a CI log, whatever the daemon or pytest
# printed. The two keys are hex, but a BV-BRC token is full of ERE
# metacharacters — `|` above all, which as an unescaped alternation would make
# the pattern match (and blank out) almost every line — so it is escaped before
# it is used as one.
_ere_escape() { printf '%s' "$1" | sed -e 's#[][\\^$.*+?(){}|/]#\\&#g'; }

redact() {
  sed -E \
    -e "s#$OPERATOR_KEY#<operator-key>#g" \
    -e "s#$VIEWER_KEY#<viewer-key>#g" \
    -e "s#${BEARER_PATTERN:-__no_bearer_yet__}#<unlisted-bearer>#g" \
    -e 's#(sig=)[0-9a-f]{16,}#\1<redacted>#g' \
    -e 's#((CTL_API_KEYS|CTL_API_KEY_ROLES|[A-Za-z_]*(KEY|TOKEN|SECRET|PASSWORD))[[:space:]]*[=:][[:space:]]*)[^[:space:]]+#\1<redacted>#g'
}

# The unlisted bearer is signed with the COMMITTED test key
# (contracts/fixtures/identity/bvbrc), and the daemon is told to read that key
# server's body from the committed public_key.json — so the token verifies for
# real, with no network and no real credential anywhere near this run. The
# subject is deliberately absent from CTL_ADMIN_SUBJECTS / CTL_VIEWER_SUBJECTS,
# which are not set at all here.
export PYTHONPATH="$ROOT/python${PYTHONPATH:+:${PYTHONPATH}}"
UNLISTED_BEARER="$("$PYTHON" - "$FIXTURES/test-signing-key.pem" <<'PY'
import sys
import time

from cryptography.hazmat.primitives import serialization

from tests.identity_support import bvbrc_payload, sign_bvbrc

key = serialization.load_pem_private_key(open(sys.argv[1], "rb").read(), password=None)
payload = bvbrc_payload(un="unlisted@example.org", expiry=int(time.time()) + 3600)
print(sign_bvbrc(payload, key))
PY
)"
if [[ -z "$UNLISTED_BEARER" ]]; then
  echo "[ctl-conf] ABORT: could not mint the unlisted BV-BRC bearer." >&2
  exit 1
fi
BEARER_PATTERN="$(_ere_escape "$UNLISTED_BEARER")"

# ---------------------------------------------------------------------------- #
# Boot
# ---------------------------------------------------------------------------- #
# --fake-drivers: the daemon serves the recorded fixture fleet and never probes
# this host. CTL_IDENTITY_KEY_FETCH_FILE is refused WITHOUT it, by design.
echo "[ctl-conf] booting ragstack-ctl --fake-drivers on $HOST:$PORT ..."
CTL_API_KEYS="[\"$OPERATOR_KEY\",\"$VIEWER_KEY\"]" \
CTL_API_KEY_ROLES="{\"$OPERATOR_KEY\":\"operator\",\"$VIEWER_KEY\":\"viewer\"}" \
CTL_API_KEY_NAMES="{\"$OPERATOR_KEY\":\"conformance-operator\",\"$VIEWER_KEY\":\"conformance-viewer\"}" \
CTL_IDENTITY_KEY_FETCH_FILE="$FIXTURES/public_key.json" \
CTL_STATE_DIR="$WORK/state" CTL_CONFIG_DIR="$WORK/config" \
CTL_LOG_LEVEL="${CTL_LOG_LEVEL:-info}" \
  "$BIN" serve --fake-drivers --listen "$HOST:$PORT" >"$LOG" 2>&1 &
SERVER_PID=$!

ready=
for _ in $(seq 1 40); do
  if curl -sf -m 2 "http://$HOST:$PORT/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[ctl-conf] daemon exited during boot:" >&2
    redact < "$LOG" >&2
    exit 1
  fi
  sleep 0.25
done
if [[ -z "$ready" ]]; then
  echo "[ctl-conf] daemon did not become healthy within timeout:" >&2
  redact < "$LOG" >&2
  exit 1
fi

echo "[ctl-conf] daemon healthy; running conformance/ctl ..."
cd "$ROOT"
status=0
set +e
RAGSTACK_CTL_URL="http://$HOST:$PORT" \
RAGSTACK_CTL_API_KEY="$OPERATOR_KEY" \
RAGSTACK_CTL_API_KEY_VIEWER="$VIEWER_KEY" \
RAGSTACK_CTL_UNLISTED_BEARER="$UNLISTED_BEARER" \
  "$PYTHON" -m pytest conformance/ctl -q -rs "$@" > "$PYTEST_OUT" 2>&1
status=$?
set -e
redact < "$PYTEST_OUT"

# The vacuity invariant: on a daemon THIS SCRIPT provisioned, "I had no
# credential for that" is a harness bug, not a legitimate absence.
if grep -q "RAGSTACK_CREDENTIAL_SKIP" "$PYTEST_OUT"; then
  echo "[ctl-conf] ABORT: this run skipped for want of a credential, on a" >&2
  echo "[ctl-conf] daemon it provisioned itself — that is the #405 vacuity:" >&2
  grep "RAGSTACK_CREDENTIAL_SKIP" "$PYTEST_OUT" | redact >&2
  status=1
fi

if [[ "$status" -ne 0 ]]; then
  echo "[ctl-conf] daemon log:" >&2
  redact < "$LOG" >&2
fi
exit "$status"
