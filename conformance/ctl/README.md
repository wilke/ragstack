# Control-plane conformance (`conformance/ctl`)

Black-box HTTP tests for `ragstack-ctl serve`, the tenant control plane. The
contract is `contracts/ctl/openapi.yaml` + `contracts/ctl/schemas/*.json`; the
suite reads the contract (schemas, the `x-ctl-role` matrix) and never imports
from `python/` or `go/`.

## Run

```bash
. /rag/bin/activate            # pytest, httpx, jsonschema, pyyaml

# the whole suite, against a daemon the runner boots and tears down itself
make test-conformance-ctl      # = build-ctl + conformance/run_ctl_local.sh

# static checks only — no server needed
pytest conformance/ctl/test_contract_static.py -q
make validate-contracts        # = python contracts/ctl/validate.py

# or point it at a daemon you booted yourself
RAGSTACK_CTL_URL=http://127.0.0.1:23999 \
RAGSTACK_CTL_API_KEY=<operator ctl key> \
RAGSTACK_CTL_API_KEY_VIEWER=<viewer ctl key> \
pytest conformance/ctl -q
```

`RAGSTACK_CTL_URL` is **required and has no default**: on the deployment host
the conventional bind (`:23990`) is the live control plane.
`conformance/run_ctl_local.sh` is the safe way — a `--fake-drivers` daemon on
`:23999` (it refuses `:23990` outright), two generated ctl keys, an unlisted
BV-BRC bearer minted from the committed fixture key, everything killed **by
pid** at the end, and any `RAGSTACK_CREDENTIAL_SKIP:` skip fails the run.

### This suite does not run with the tenant suite

`conformance/` holds two suites for two different servers. A tenant run
(`pytest conformance/`, `make test-conformance-python|go|keyed`) **skips this
directory** — the root `conftest.py`'s `pytest_ignore_collect` drops it when
`RAGSTACK_CTL_URL` is unset and the invocation did not name it, and prints one
line saying so. Naming it (`pytest conformance/ctl`) without the variable still
raises the `UsageError` on purpose: "I asked for the ctl suite" deserves an
answer, not a silent skip.

## Principals

| Variable | What | Without it |
|---|---|---|
| `RAGSTACK_CTL_API_KEY` | an **operator** ctl key (the default principal) | every authenticated test skips, tagged |
| `RAGSTACK_CTL_API_KEY_VIEWER` | a **viewer** ctl key, distinct from the operator key | `test_authz_matrix.py` and the viewer-reduction tests skip, tagged |
| `RAGSTACK_CTL_UNLISTED_BEARER` | a BV-BRC token that **verifies** for a subject the ctl does not list | the 403-never-default-role case skips, tagged |

Present-but-wrong is a **failure**, not a skip: each key is proven through
`GET /v1/me` before use (operator is `operator`, viewer is `viewer`, the two
are distinct). See `conftest.py`.

## Files

| File | Pins |
|---|---|
| `test_contract_static.py` | `contracts/ctl/validate.py` as pytest: every `$ref` resolves, every operation has `x-ctl-role` + a matrix row, plan paths present, schemas valid + closed, verb enum, settings-name guard identical and effective, no secret-shaped property names |
| `test_health.py` | anonymous `/health`; `X-Request-Id` generated, unique, never echoed; unknown route is an `Error` body |
| `test_version.py` | `/v1/version` schema; agrees with `/health`; needs a credential |
| `test_auth.py` | none → 401; unknown key → 401; malformed/unverifiable bearer → 401; **valid unlisted subject → 403**; both → 400; session lifecycle; sessions cannot read secrets |
| `test_me.py` | roles and `auth_method`; the key is never echoed |
| `test_fleet.py` | schema; **no field name matching `(?i)(api_key|password|secret|token|dsn)` anywhere**; no credential-shaped value; same shape for a viewer |
| `test_tenants.py` | list/show/env schemas; same name check (allowlist: `secret_refs`, `secrets_file_sha256`); `settings` never carries a forbidden key; keys are fingerprints; viewer gets `registry: null`; 404/422 |
| `test_doctor.py` | schema; status = max(findings); hash stability; scoped runs; 404/422 |
| `test_authz_matrix.py` | parametrized from `x-ctl-role`: anonymous → 401 on everything; **viewer → 403 on every operator operation**, GET and mutation; viewer reaches every viewer operation |
| `test_ops.py` | module-level skip — mutations are PR-C |

## Against a real-driver daemon

`run_ctl_local.sh` disables rate limiting because it boots a `--fake-drivers`
daemon. Against a real daemon the anonymous-failure budget (20/min) turns nine
of the 401 assertions into 429s; run a separate daemon with
`CTL_RATE_LIMIT_PER_CREDENTIAL=-1` for the suite (see
`docs/runbooks/ctl-deploy.md`, "Running the conformance suite against the
deployed daemon"). The unlisted-bearer test skips there: the fixture key
server exists only under `--fake-drivers`.
