"""Conformance: the mutation surface — ``POST /v1/tenants/{name}/ops/{verb}``,
``POST /v1/tenants``, the two gateway mutations, the three job continuations,
``PUT /v1/settings``, and the job and audit reads they produce.

The file has two halves, and the split is the point.

The first half asserts what the HTTP layer owns on its own: who may mutate (a
viewer may not, and a session may not without re-presenting a ctl key), what a
well-formed envelope is (unknown members, the ``idempotency_key`` pattern, the
verb enum, the tenant-name pattern, the writable subset of settings), and the
shape of every answer (``error.json`` plus an ``X-Request-Id``). None of that
needs a job engine, so none of it is gated: it runs, and must pass, on any
daemon that answers at all.

The second half asserts what only the ENGINE can answer — a plan, a job that
reaches a terminal state, idempotency, confirm, the deliver-once secrets
envelope, the audit rows, the viewer reduction of a job that actually ran. On
this branch ``api.BuildEngine`` returns "job engine not wired" and ``serve``
carries on with a nil engine, so every mutation answers 409 ``refused``. The
:func:`job_engine` fixture probes for exactly that, once per session, and skips
the second half by name when it finds it. It is a plain :func:`pytest.skip` and
NOT a credential skip: nothing is missing from the harness, the daemon under
test simply has no engine, and tagging it would make
``conformance/run_ctl_local.sh`` fail a run that is behaving as designed.

A module-level skip would have taken the authorization assertions down with the
engine-dependent ones, which is how a suite comes to prove nothing on the
branch where the surface is most likely to be got wrong.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

import httpx
import pytest

from ctl.helpers import assert_error, assert_request_id, validate

pytestmark = pytest.mark.asyncio

#: A ULID that is syntactically valid and names no job. Authorization and
#: envelope validation both precede lookup, so every assertion in the first
#: half reaches its answer without a real job existing.
NO_SUCH_JOB = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

#: A tenant name that matches ``^[a-z][a-z0-9-]{0,31}$`` and names nothing.
NO_SUCH_TENANT = "zz-conformance-ops"

#: The states a job stops moving in. ``awaiting_cutover`` and ``interrupted``
#: are parked, not finished: a poll that treated them as terminal would report
#: a hung job as a passing one.
TERMINAL_STATES = frozenset({"succeeded", "failed", "rolled_back", "cancelled"})


def idem() -> str:
    """A fresh idempotency key. Every test mints its own: a key reused across
    tests would make the second test read the first one's job back and assert
    against a mutation it never submitted."""
    return f"conformance-{uuid.uuid4()}"


# The generic EXECUTABLE mutation. It must plan and run on any tenant the
# fixture holds — the four coconut tenants are hand-started (supervisor:
# manual), on which `start` is refused by design until the handover. Setting a
# PUBLIC env key is non-destructive, needs no confirm, and writes through the
# files driver, so it exercises the whole engine path.
EXEC_VERB = "env-set"
EXEC_ARGS: dict[str, Any] = {"key": "LOG_LEVEL", "value": "info"}
EXEC_ARGS_OTHER: dict[str, Any] = {"key": "LOG_LEVEL", "value": "debug"}


def op_body(**over: Any) -> dict[str, Any]:
    """A minimal VALID ``op_request.json`` envelope, dry by default so that a
    body sent only to reach an authorization answer cannot mutate anything."""
    body: dict[str, Any] = {"dry_run": True, "idempotency_key": idem(), "args": {}}
    body.update(over)
    return body


def create_body(**over: Any) -> dict[str, Any]:
    """The same envelope with ``create``'s typed args."""
    return op_body(args={"name": NO_SUCH_TENANT, "artifact_id": "conformance-none"}, **over)


def mutations() -> list[tuple[str, str, str, dict[str, Any]]]:
    """Every mutating operation as ``(operationId, method, path, body)``.

    ``test_authz_matrix.py`` parametrizes itself from the contract; this table
    is written out because these tests care about the BODY each route takes,
    which the contract expresses as a schema rather than as a value.
    """
    return [
        ("ctlTenantOp", "POST", f"/v1/tenants/{NO_SUCH_TENANT}/ops/start", op_body()),
        ("ctlTenantCreate", "POST", "/v1/tenants", create_body()),
        ("ctlGatewayApply", "POST", "/v1/gateway/apply", op_body()),
        ("ctlGatewayReload", "POST", "/v1/gateway/reload", op_body()),
        ("ctlJobResume", "POST", f"/v1/jobs/{NO_SUCH_JOB}/resume", op_body()),
        ("ctlJobContinue", "POST", f"/v1/jobs/{NO_SUCH_JOB}/continue", op_body()),
        ("ctlJobCancel", "POST", f"/v1/jobs/{NO_SUCH_JOB}/cancel", op_body()),
        ("ctlSettingsPut", "PUT", "/v1/settings", op_body(args={"ctl": {"gateway_enabled": True}})),
    ]


MUTATIONS = mutations()
MUTATION_IDS = [m[0] for m in MUTATIONS]


# --------------------------------------------------------------------------- #
# The engine gate
# --------------------------------------------------------------------------- #
def _sync_post(url: str, headers: dict[str, str], payload: dict[str, Any]) -> tuple[int, Any]:
    """One blocking POST, stdlib only. A session-scoped fixture cannot use the
    event-loop-bound async client, which is the same reason
    ``conftest._sync_get`` exists."""
    req = urllib.request.Request(
        url,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw.decode("utf-8", "replace")


@pytest.fixture(scope="session")
def job_engine(ctl_url: str, ctl_key: str, some_tenant: str) -> None:
    """Skip, once per session and by name, when the daemon under test has no
    job engine.

    The probe is the cheapest real mutation there is — a dry run, which locks
    nothing and writes nothing — and its answer is read the way the handler
    writes it: ``api.BuildEngine`` failing leaves the server's engine nil, and
    every mutation then answers 409 ``refused`` with ``ErrEngineNotWired``'s
    text in the detail. A 501 is accepted as the same answer from a daemon that
    reports it that way instead.

    Deliberately NOT a credential skip. ``run_ctl_local.sh`` fails a run that
    skipped for want of a credential, because on a daemon it provisioned itself
    that is a harness bug; an unwired engine is a fact about the build under
    test, and failing the run for it would make the branch untestable rather
    than honestly reported.
    """
    status, body = _sync_post(
        f"{ctl_url}/v1/tenants/{some_tenant}/ops/{EXEC_VERB}",
        {"X-API-Key": ctl_key},
        {"dry_run": True, "idempotency_key": idem(), "args": EXEC_ARGS},
    )
    detail = body.get("detail", "") if isinstance(body, dict) else str(body)
    code = body.get("code") if isinstance(body, dict) else None
    if status == 501 or (status == 409 and code == "refused" and "not wired" in detail):
        pytest.skip(
            f"the job engine is not wired in this daemon (POST /v1/tenants/{some_tenant}"
            f"/ops/{EXEC_VERB} answered {status} {code}: {detail}); the mutation conformance "
            "lands with the engine"
        )


# --------------------------------------------------------------------------- #
# Submit and poll (the engine-gated half's shared path)
# --------------------------------------------------------------------------- #
async def doctor_force(
    client: httpx.AsyncClient, path: str, *, method: str = "POST", args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Dry-run *path* and return the body members an execute needs to pass the
    doctor gate: ``force_with_doctor_diff`` when the plan's doctor is yellow,
    nothing when it is green. A red doctor is left alone — the execute must
    then be refused, and a test that wants that refusal asserts it itself."""
    resp = await client.request(method, path, json=op_body(dry_run=True, args=args or {}))
    if resp.status_code != 200:
        return {}
    doctor = resp.json().get("doctor") or {}
    if doctor.get("status") == "yellow" and doctor.get("hash"):
        return {"force_with_doctor_diff": doctor["hash"]}
    return {}


async def submit_and_settle(
    client: httpx.AsyncClient,
    path: str,
    schemas: dict[str, dict],
    *,
    method: str = "POST",
    args: dict[str, Any] | None = None,
    confirm: str | None = None,
    key: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Execute *path*, then poll the job to a terminal state and return it.

    A dry run comes first, because the contract gates every execute on the
    op-scoped doctor: red is never forced, and a YELLOW doctor is accepted only
    when the caller quotes the hash it saw (``force_with_doctor_diff``) — the
    same thing the CLI does with ``--force-with-doctor-diff``. A fixture fleet
    has warnings, so the helper carries the hash the way an operator would."""
    body = op_body(dry_run=False, args=args or {}, idempotency_key=key or idem())
    if confirm is not None:
        body["confirm"] = confirm
    body.update(await doctor_force(client, path, method=method, args=args or {}))
    accepted = await client.request(method, path, json=body)
    assert accepted.status_code == 202, (
        f"execute {method} {path} expected 202, got {accepted.status_code}: {accepted.text[:400]}"
    )
    assert_request_id(accepted)
    job = accepted.json()
    validate(job, "job", schemas)
    assert accepted.headers.get("Location") == f"/v1/jobs/{job['id']}", (
        "the 202 must point at the job it accepted; Location was "
        f"{accepted.headers.get('Location')!r} for job {job['id']}"
    )
    return await poll_to_terminal(client, job["id"], schemas, timeout=timeout)


async def poll_to_terminal(
    client: httpx.AsyncClient, job_id: str, schemas: dict[str, dict], *, timeout: float = 30.0
) -> dict[str, Any]:
    """Poll one job until it settles. Bounded on purpose: a job that never
    settles is a failure with the last body attached, not a suite that hangs
    until CI kills it and prints nothing about which job was stuck."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        resp = await client.get(f"/v1/jobs/{job_id}")
        assert resp.status_code == 200, (
            f"GET /v1/jobs/{job_id}: {resp.status_code}: {resp.text[:300]}"
        )
        last = resp.json()
        validate(last, "job", schemas)
        if last["state"] in TERMINAL_STATES:
            return last
        await asyncio.sleep(0.25)
    pytest.fail(
        f"job {job_id} did not reach a terminal state within {timeout:.0f}s; "
        f"last body: {json.dumps(last)[:800]}"
    )


# =========================================================================== #
# Engine-independent — these run on every daemon, engine or not
# =========================================================================== #
@pytest.mark.parametrize("opid,method,path,body", MUTATIONS, ids=MUTATION_IDS)
async def test_a_viewer_is_403_on_every_mutation(
    viewer_client: httpx.AsyncClient, schemas: dict[str, dict],
    opid: str, method: str, path: str, body: dict[str, Any],
) -> None:
    """Every mutating route refuses a viewer with 403 ``forbidden`` — not 404
    (authorization precedes lookup), not 422 (the body is a valid dry-run
    envelope), not 409 (the engine is never consulted). The body is well formed
    precisely so that authorization is the only reason the request could be
    refused; a 422 here would mean the envelope check ran first, and a viewer
    could map the contract with it."""
    resp = await viewer_client.request(method, path, json=dict(body, idempotency_key=idem()))
    assert_error(resp, 403, "forbidden", schemas)


async def test_an_unknown_body_member_is_422_naming_the_member(
    client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    """``ctl_api_kye`` used to be DROPPED by the decoder, so a request that
    meant to carry a credential read as one that carried none and the
    misspelling was invisible. It is now a 422 whose ``extra.fields`` names the
    member, so the caller does not have to diff its body against the schema to
    find the typo."""
    resp = await client.post(
        f"/v1/tenants/{some_tenant}/ops/start",
        json={"dry_run": True, "idempotency_key": idem(), "args": {}, "ctl_api_kye": "x"},
    )
    body = assert_error(resp, 422, "validation", schemas)
    assert body.get("extra", {}).get("fields") == ["ctl_api_kye"], body


@pytest.mark.parametrize(
    "key,why",
    [
        (None, "absent"),
        ("short", "shorter than the schema's eight characters"),
        ("has spaces in it", "outside ^[A-Za-z0-9._:-]{8,128}$"),
        ("", "empty"),
    ],
    ids=["absent", "too-short", "bad-characters", "empty"],
)
async def test_a_missing_or_malformed_idempotency_key_is_422(
    client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str,
    key: str | None, why: str,
) -> None:
    """The idempotency key is what makes a retry safe, so a request without a
    usable one is refused before planning rather than executed once per click.
    The offending member is named in ``extra.fields``."""
    body: dict[str, Any] = {"dry_run": True, "args": {}}
    if key is not None:
        body["idempotency_key"] = key
    resp = await client.post(f"/v1/tenants/{some_tenant}/ops/start", json=body)
    err = assert_error(resp, 422, "validation", schemas)
    assert "idempotency_key" in err.get("extra", {}).get("fields", []), (
        f"a key {why} must be reported against idempotency_key: {err}"
    )


async def test_a_verb_outside_the_contract_enum_is_422(
    client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    """An unknown verb is a path parameter outside its schema and never reaches
    the engine, which is what keeps "I mistyped the verb" from arriving as
    "that tenant does not exist"."""
    resp = await client.post(f"/v1/tenants/{some_tenant}/ops/frobnicate", json=op_body())
    err = assert_error(resp, 422, "validation", schemas)
    # `extra.fields` is sorted and may name more than one member, so every
    # assertion on it here is a membership one.
    assert "verb" in err.get("extra", {}).get("fields", []), err


async def test_a_tenant_name_outside_the_pattern_is_422(
    client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """Outside ``^[a-z][a-z0-9-]{0,31}$`` the name never reaches the registry: a
    validation answer, not a lookup miss, so a name that could not exist is
    never confused with one that merely does not."""
    resp = await client.post("/v1/tenants/Not_A_Valid_Name/ops/start", json=op_body())
    err = assert_error(resp, 422, "validation", schemas)
    assert "name" in err.get("extra", {}).get("fields", []), err


@pytest.mark.parametrize(
    "args,field",
    [
        ({"recipients": {"file": "/etc/ragstack/recipients.txt"}}, "recipients"),
        ({"registry_generation": 99}, "registry_generation"),
        ({"python_env_default": "/rag/envs/ragstack"}, "python_env_default"),
    ],
    ids=["recipients", "registry_generation", "python_env_default"],
)
async def test_settings_cli_only_members_are_409_refused(
    client: httpx.AsyncClient, schemas: dict[str, dict], args: dict[str, Any], field: str
) -> None:
    """``recipients`` decides who can DECRYPT a backup, ``python_env_default``
    re-points the interpreter every tenant API is started with, and
    ``registry_generation`` is the server's own counter. The contract makes all
    three 409 ``refused`` over HTTP, and the status matters: 422 would tell a
    client its document was malformed and invite it to fix the spelling, when
    the document is fine and the CALLER is the problem."""
    resp = await client.put("/v1/settings", json=op_body(args=args))
    err = assert_error(resp, 409, "refused", schemas)
    assert field in err.get("extra", {}).get("fields", []), err


async def test_settings_outside_the_writable_subset_is_422(
    client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """A member settings_response.json does not have at all is a malformed
    document: 422 ``validation``, naming the member."""
    resp = await client.put("/v1/settings", json=op_body(args={"not_a_setting": 1}))
    err = assert_error(resp, 422, "validation", schemas)
    assert "not_a_setting" in err.get("extra", {}).get("fields", []), err


async def test_a_valid_partial_settings_document_is_not_422(client: httpx.AsyncClient) -> None:
    """``PUT /v1/settings {"ctl": {"gateway_enabled": true}}`` is the flip that
    mattered on coconut. A partial must be accepted as a partial: demanding the
    whole document would turn every settings change into a read-modify-write
    race against the registry generation. What happens next depends on the
    engine (200 or 202 with one, 409 ``refused`` without), so the assertion
    here is only that the envelope itself was not rejected."""
    resp = await client.put("/v1/settings", json=op_body(args={"ctl": {"gateway_enabled": True}}))
    assert resp.status_code != 422, (
        f"a valid partial settings document was rejected as invalid: {resp.text[:400]}"
    )
    assert_request_id(resp)


async def test_a_session_must_re_present_a_ctl_key_to_mutate(
    anon_client: httpx.AsyncClient, schemas: dict[str, dict], ctl_key: str, some_tenant: str
) -> None:
    """A session authenticates READS. It is a credential a browser holds for
    hours, so on its own it may not change anything: the UI prompts for the ctl
    key per mutation and sends it in the body, never storing it. Without this
    rule a stolen session id would mutate the fleet.

    The second half proves the rule is a gate and not a wall — the same session
    WITH the operator key in ``ctl_api_key`` gets past it, and whatever answers
    next is not a 403."""
    created = await anon_client.post("/v1/session", headers={"X-API-Key": ctl_key})
    assert created.status_code == 201, created.text
    session = {"Authorization": f"Session {created.json()['session_id']}"}
    try:
        bare = await anon_client.post(
            f"/v1/tenants/{some_tenant}/ops/start", headers=session, json=op_body()
        )
        err = assert_error(bare, 403, "forbidden", schemas)
        assert "ctl_api_key" in err["detail"] or "ctl API key" in err["detail"], (
            "the refusal must tell the browser what to DO — re-present the ctl "
            f"key: {err['detail']}"
        )

        with_key = await anon_client.post(
            f"/v1/tenants/{some_tenant}/ops/start",
            headers=session,
            json=op_body(ctl_api_key=ctl_key),
        )
        assert with_key.status_code != 403, (
            "a session that re-presented the operator ctl key must get PAST the "
            f"session rule; it was still refused: {with_key.text[:400]}"
        )
        assert_request_id(with_key)
    finally:
        await anon_client.delete("/v1/session", headers=session)


async def test_jobs_list_conforms(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    """The list is a READ, so it answers on a daemon with no engine too: an
    empty list is the honest answer to "what has this daemon run", and
    reporting "not wired" here would make a caller treat a working read as a
    broken one."""
    resp = await client.get("/v1/jobs")
    assert resp.status_code == 200, resp.text
    validate(resp.json(), "jobs_response", schemas)
    assert_request_id(resp)


@pytest.mark.parametrize(
    "params,field",
    [({"state": "nonsense"}, "state"), ({"limit": 9999}, "limit")],
    ids=["unknown-state", "limit-over-the-cap"],
)
async def test_jobs_list_rejects_a_query_outside_the_contract(
    client: httpx.AsyncClient, schemas: dict[str, dict], params: dict[str, Any], field: str
) -> None:
    """Out of range is a 422 and never a silent clamp: a caller that asked for
    9999 jobs should learn the cap, not receive 500 and believe that was
    everything."""
    resp = await client.get("/v1/jobs", params=params)
    err = assert_error(resp, 422, "validation", schemas)
    assert err.get("extra", {}).get("fields") == [field], err


async def test_a_malformed_job_id_is_422(
    client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """A job id that is not a ULID is a validation answer, not a 404: "no such
    job" and "that could not be a job id" are different facts, and only the
    first tells a caller its id was real and is gone."""
    resp = await client.get("/v1/jobs/not-a-ulid")
    err = assert_error(resp, 422, "validation", schemas)
    assert "id" in err.get("extra", {}).get("fields", []), err


@pytest.mark.parametrize(
    "op", ["resume", "continue", "cancel"], ids=["resume", "continue", "cancel"]
)
async def test_a_continuation_has_no_dry_run(
    client: httpx.AsyncClient, schemas: dict[str, dict], op: str
) -> None:
    """The contract lists a 200 Plan for the three continuations, but the
    engine seam gives them no way to produce one: they act on a job that
    already carries its plan. Rather than invent a second planning path that
    would answer with a plan the engine would never execute, the dry run is
    refused and the detail says where the plan actually is. The refusal is
    written before the engine is consulted, so it is the answer with or without
    a wired engine — which is why this test is not gated."""
    resp = await client.post(f"/v1/jobs/{NO_SUCH_JOB}/{op}", json=op_body())
    err = assert_error(resp, 409, "refused", schemas)
    assert f"GET /v1/jobs/{NO_SUCH_JOB}" in err["detail"], (
        f"the refusal must point at the job whose plan the caller wanted: {err['detail']}"
    )


async def test_job_secrets_are_key_only_and_never_cached(
    anon_client: httpx.AsyncClient, client: httpx.AsyncClient,
    schemas: dict[str, dict], ctl_key: str,
) -> None:
    """``GET /v1/jobs/{id}/secrets`` is the one read the matrix marks ``session:
    false``: the caller must present the ctl key itself, because a session is a
    credential a browser can hold and this response carries a secret VALUE.
    Authorization precedes lookup, so a job id that names nothing still gets
    the credential answer rather than a 404.

    The ``Cache-Control: no-store`` claim is asserted on the operator's own
    call, which is the one that reaches the handler — the handler sets the
    header before it consults the engine, so its refusals are no-store too. The
    session refusal comes from the authorization gate, before any handler runs,
    and the contract attaches the header to the responses the handler writes;
    asserting it on the gate's 403 would pin behaviour the contract does not
    promise."""
    created = await anon_client.post("/v1/session", headers={"X-API-Key": ctl_key})
    assert created.status_code == 201, created.text
    session = {"Authorization": f"Session {created.json()['session_id']}"}
    try:
        refused = await anon_client.get(f"/v1/jobs/{NO_SUCH_JOB}/secrets", headers=session)
        assert_error(refused, 403, "forbidden", schemas)
    finally:
        await anon_client.delete("/v1/session", headers=session)

    handled = await client.get(f"/v1/jobs/{NO_SUCH_JOB}/secrets")
    assert handled.headers.get("Cache-Control") == "no-store", (
        "every answer from the secrets handler — the 404 and the 410 included — "
        f"must be no-store; headers were {dict(handled.headers)}"
    )
    assert_request_id(handled)


async def test_audit_is_operator_only_and_conforms(
    client: httpx.AsyncClient, viewer_client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """The audit log names principals and their redacted arguments, so a viewer
    may not read it at all. For an operator it conforms even when empty."""
    assert_error(await viewer_client.get("/v1/audit"), 403, "forbidden", schemas)
    resp = await client.get("/v1/audit")
    assert resp.status_code == 200, resp.text
    validate(resp.json(), "audit_response", schemas)
    assert_request_id(resp)


@pytest.mark.parametrize("opid,method,path,body", MUTATIONS, ids=MUTATION_IDS)
async def test_every_mutation_answers_in_the_contract_envelope(
    client: httpx.AsyncClient, schemas: dict[str, dict],
    opid: str, method: str, path: str, body: dict[str, Any],
) -> None:
    """Whatever a mutation answers — a plan, a job, or a refusal — it carries a
    correlation id, and every refusal is an ``error.json`` whose ``request_id``
    equals the header. A route that answered a bare string, or one that lost
    the header on the error path, would leave an operator with a failure they
    cannot find in the log."""
    resp = await client.request(method, path, json=dict(body, idempotency_key=idem()))
    rid = assert_request_id(resp)
    if resp.status_code >= 400:
        err = resp.json()
        validate(err, "error", schemas)
        assert err["request_id"] == rid, err
    else:
        assert resp.status_code in (200, 202), f"{opid}: {resp.status_code}: {resp.text[:300]}"


# =========================================================================== #
# Engine-gated — the mutation conformance proper
# =========================================================================== #
async def test_a_dry_run_answers_a_plan(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    """``dry_run: true`` is the contract's preview: 200 ``plan.json``, nothing
    locked, nothing written. It is also the thing a client must have seen
    before it executes, so the plan hash and the confirm value have to be in
    the body rather than derivable only server-side."""
    resp = await client.post(f"/v1/tenants/{some_tenant}/ops/{EXEC_VERB}", json=op_body(args=EXEC_ARGS))
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    validate(plan, "plan", schemas)
    assert_request_id(resp)
    assert plan["op"] == EXEC_VERB
    assert plan["tenant"] == some_tenant


async def test_an_execute_is_accepted_and_reaches_a_terminal_state(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    """``dry_run: false`` answers 202 with the job and a ``Location`` pointing
    at it, and the job then actually finishes. A 202 that never settles is the
    failure this poll exists to catch: the surface looks right and the fleet
    never changes."""
    job = await submit_and_settle(client, f"/v1/tenants/{some_tenant}/ops/{EXEC_VERB}", schemas, args=EXEC_ARGS)
    assert job["op"] == EXEC_VERB
    assert job["tenant"] == some_tenant
    # `succeeded`, not "any terminal state". TERMINAL_STATES includes `failed`,
    # so asserting membership passed on a job every step of which refused —
    # which is exactly the outcome this test exists to catch, and exactly what
    # a build with an unwired driver produces.
    assert job["state"] == "succeeded", (
        f"the execute settled as {job['state']}, not succeeded: "
        f"{json.dumps(job.get('error'))} · steps "
        + json.dumps([{"n": s["n"], "state": s["state"], "error": s.get("error")} for s in job["steps"]])
    )


async def test_the_same_key_returns_the_same_job_and_a_different_body_is_409(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    """The idempotency key is persisted with the request it was used for. A
    retry of the SAME request returns the original job, which is what makes a
    dropped response safe to retry; the same key with a DIFFERENT request is
    409 ``duplicate``, which is what stops a client from quietly running a
    second operation under a key an operator already approved."""
    path = f"/v1/tenants/{some_tenant}/ops/{EXEC_VERB}"
    key = idem()
    body = op_body(dry_run=False, idempotency_key=key, args=EXEC_ARGS)
    body.update(await doctor_force(client, path, args=EXEC_ARGS))

    first = await client.post(path, json=body)
    assert first.status_code == 202, first.text
    job_id = first.json()["id"]

    retry = await client.post(path, json=body)
    assert retry.status_code == 202, retry.text
    assert retry.json()["id"] == job_id, (
        "a retry with the same key and the same body must return the ORIGINAL "
        f"job; got {retry.json()['id']} for {job_id}"
    )

    different = await client.post(
        path,
        json={
            **op_body(dry_run=False, idempotency_key=key, args=EXEC_ARGS_OTHER),
            **await doctor_force(client, path, args=EXEC_ARGS_OTHER),
        },
    )
    assert_error(different, 409, "duplicate", schemas)
    await poll_to_terminal(client, job_id, schemas)


async def test_a_destructive_op_requires_its_confirm_value(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    """A plan that says ``requires_confirm`` is refused with 428 until the
    caller quotes the plan's own ``confirm_value`` — the tenant name for a
    tenant op, exactly like the CLI's ``--yes-destructive <name>``. The 428
    carries that value in ``extra`` because a caller that cannot see it has a
    refusal it can never satisfy.

    The verb is chosen by asking the daemon which of its destructive ones
    actually plans with confirmation, rather than by hard-coding a list this
    suite would then have to keep in step with the op registry.

    ``env-unset`` leads the list on purpose. The four earlier candidates are
    all refused or non-destructive on the fixture fleet, so this test used to
    SKIP — and a confirmation gate that is never exercised is a confirmation
    gate nobody would notice the loss of. Unsetting a PUBLIC env key is
    destructive by the registry's own rule, plans on the fixture, and is put
    back at the end."""
    candidates = await destructive_candidates(client, schemas, some_tenant)
    verb, args, plan = None, None, None
    for candidate, candidate_args in candidates:
        preview = await client.post(
            f"/v1/tenants/{some_tenant}/ops/{candidate}", json=op_body(args=candidate_args)
        )
        if preview.status_code != 200 or not preview.json()["requires_confirm"]:
            continue
        if not plan_is_executable(preview.json()):
            # A plan whose step could not be previewed is one whose RUN will
            # refuse. Choosing it would make this test assert on the engine's
            # refusal path rather than on the confirmation gate.
            continue
        verb, args, plan = candidate, candidate_args, preview.json()
        break
    assert verb is not None, (
        "no destructive verb on this tenant plans with requires_confirm, so the confirmation gate went "
        f"untested. Tried {[c for c, _ in candidates]}"
    )

    path = f"/v1/tenants/{some_tenant}/ops/{verb}"
    bare = await client.post(path, json=op_body(dry_run=False, args=args))
    err = assert_error(bare, 428, "confirm_required", schemas)
    confirm = err.get("extra", {}).get("confirm_value")
    assert confirm == plan["confirm_value"], (
        f"the 428 must carry the plan's own confirm_value ({plan['confirm_value']!r}); "
        f"extra was {err.get('extra')!r}"
    )

    job = await submit_and_settle(client, path, schemas, args=args, confirm=confirm)
    assert job["state"] == "succeeded", (
        f"the confirmed {verb} settled as {job['state']}: {json.dumps(job.get('error'))}"
    )
    if verb == "env-unset":
        # Put it back: every test in this module reads the same fixture.
        await submit_and_settle(
            client, f"/v1/tenants/{some_tenant}/ops/env-set", schemas,
            args={"key": args["key"], "value": "100"},
        )


def plan_is_executable(plan: dict[str, Any]) -> bool:
    """A plan every step of which could be previewed.

    ``would_write[].preview`` is null and a warning says so when the step's
    edit could not be applied to the file it read — the same thing that will
    happen when it runs. It is the contract's own signal that a plan is not
    going to work, so the candidate search reads it rather than guessing."""
    for step in plan.get("steps", []):
        for warning in step.get("warnings", []):
            if "could not be previewed" in warning or "does not parse" in warning:
                return False
    return True


async def destructive_candidates(
    client: httpx.AsyncClient, schemas: dict[str, dict], tenant: str
) -> list[tuple[str, dict[str, Any]]]:
    """The destructive verbs to try, with the arguments each one needs, best
    first.

    The fixture fleet carries no public settings and no key ledger, so the
    first candidate is one this helper PROVISIONS: a public marker key set with
    ``env-set`` — an operation the suite has already proved works — which
    ``env-unset`` can then destroy. That is the point: the confirmation gate
    has to be exercised against a plan that really runs, and waiting for a
    fixture to happen to contain a suitable target is how this test came to
    skip on every run.

    ``key-revoke`` and the rest are read off the daemon and offered after it; a
    verb whose target does not exist refuses at plan time and is not chosen.
    """
    out: list[tuple[str, dict[str, Any]]] = []

    # A key the settings allowlist knows — the typed env API edits nothing
    # else — and not the one the executable tests use.
    marker, marker_value = "MAX_COLLECTIONS", "100"
    seeded = await submit_and_settle(
        client, f"/v1/tenants/{tenant}/ops/env-set", schemas,
        args={"key": marker, "value": marker_value},
    )
    if seeded["state"] == "succeeded":
        out.append(("env-unset", {"key": marker}))

    env = await client.get(f"/v1/tenants/{tenant}/env")
    if env.status_code == 200:
        for row in env.json().get("keys", []):
            # Every other public key is offered too; the caller drops the ones
            # whose plan says it could not preview the edit. Not LOG_LEVEL,
            # which the executable tests set and read.
            if row.get("class") == "public" and row.get("key") not in (EXEC_ARGS["key"], marker):
                out.append(("env-unset", {"key": row["key"]}))

    show = await client.get(f"/v1/tenants/{tenant}")
    if show.status_code == 200:
        for key in show.json().get("keys") or []:
            if not key.get("revoked_at"):
                out.append(("key-revoke", {"id": key["id"]}))
                break

    out += [("stop", {}), ("restart", {}), ("backup", {}), ("decommission", {})]
    return out


async def test_an_unknown_argument_is_422(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    """``args`` is validated against the verb's own schema before planning, so
    a misspelt argument is refused rather than ignored — an ignored ``force``
    is an operation that did something other than what was approved."""
    resp = await client.post(f"/v1/tenants/{some_tenant}/ops/start", json=op_body(args={"nope": 1}))
    assert_error(resp, 422, "validation", schemas)


async def test_update_code_is_refused_until_v1_1(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    """``update-code`` is contracted for v1.1 and answers 409 ``refused`` with a
    detail saying so. It is in the verb enum on purpose: a client that can see
    the verb and gets a 422 would conclude the verb does not exist."""
    resp = await client.post(
        f"/v1/tenants/{some_tenant}/ops/update-code",
        json=op_body(args={"artifact_id": "conformance-none"}),
    )
    err = assert_error(resp, 409, "refused", schemas)
    assert "not wired" not in err["detail"], (
        "update-code must be refused as a v1.1 operation, not as an unwired "
        f"engine: {err['detail']}"
    )


async def test_a_viewer_sees_a_reduced_job(
    job_engine: None, client: httpx.AsyncClient, viewer_client: httpx.AsyncClient,
    schemas: dict[str, dict], some_tenant: str,
) -> None:
    """``ctlJobsList``'s ``viewer_fields``: worker and lock null, reservations
    and every step's external ids empty, every step log null. The operator's
    view of the SAME job must show at least one of them populated — otherwise
    the reduction is asserted against a job that had nothing to hide and the
    test passes for the wrong reason."""
    job = await submit_and_settle(client, f"/v1/tenants/{some_tenant}/ops/{EXEC_VERB}", schemas, args=EXEC_ARGS)
    job_id = job["id"]

    full = (await client.get(f"/v1/jobs/{job_id}")).json()
    populated = (
        full["worker"] is not None
        or full["lock"] is not None
        or bool(full["reservations"])
        or any(s["external_ids"] for s in full["steps"])
        or any(s["log"] for s in full["steps"])
    )
    assert populated, (
        f"job {job_id} carries nothing a viewer could be denied — worker, lock, "
        "reservations, external_ids and step logs are all empty for the OPERATOR "
        "too, so the reduction asserted below would pass vacuously"
    )

    reduced = await viewer_client.get(f"/v1/jobs/{job_id}")
    assert reduced.status_code == 200, reduced.text
    row = reduced.json()
    validate(row, "job", schemas)
    assert row["worker"] is None, row["worker"]
    assert row["lock"] is None, row["lock"]
    assert row["reservations"] == [], row["reservations"]
    for step in row["steps"]:
        assert step["external_ids"] == [], step
        assert step["log"] is None, step

    listed = await viewer_client.get("/v1/jobs", params={"tenant": some_tenant})
    assert listed.status_code == 200, listed.text
    validate(listed.json(), "jobs_response", schemas)
    for listed_row in listed.json()["jobs"]:
        assert listed_row["worker"] is None and listed_row["lock"] is None, listed_row
        assert listed_row["reservations"] == [], listed_row
        for step in listed_row["steps"]:
            assert step["external_ids"] == [] and step["log"] is None, step


async def test_minted_secrets_are_delivered_exactly_once(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    """The delivery envelope is the only response in this API that carries a
    secret value, and the first successful read destroys it. A second read is
    410 with code ``not_found`` — the one place the contract lets a code and a
    status disagree — because "you already collected this" and "there was never
    such a job" are different things to tell an operator hunting for a key they
    lost."""
    job = await submit_and_settle(
        client,
        f"/v1/tenants/{some_tenant}/ops/key-mint",
        schemas,
        args={"label": f"conformance-{uuid.uuid4().hex[:8]}", "role": "user"},
    )
    assert job["state"] == "succeeded", job

    first = await client.get(f"/v1/jobs/{job['id']}/secrets")
    assert first.status_code == 200, first.text
    validate(first.json(), "secrets_response", schemas)
    assert first.headers.get("Cache-Control") == "no-store", dict(first.headers)
    assert first.json()["job_id"] == job["id"]
    assert first.json()["secrets"], "a key-mint that succeeded delivered no secret"

    second = await client.get(f"/v1/jobs/{job['id']}/secrets")
    assert_error(second, 410, "not_found", schemas)
    assert second.headers.get("Cache-Control") == "no-store", dict(second.headers)


async def test_a_mutation_writes_audit_rows(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    """Every mutation writes an ``intent`` row before anything runs and a
    ``result`` row after. The intent row is what survives a crash mid-job: a
    log written only on success cannot answer "what was this daemon doing when
    it died"."""
    job = await submit_and_settle(client, f"/v1/tenants/{some_tenant}/ops/{EXEC_VERB}", schemas, args=EXEC_ARGS)
    resp = await client.get("/v1/audit", params={"tenant": some_tenant})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate(body, "audit_response", schemas)
    mine = [row for row in body["rows"] if row["job_id"] == job["id"]]
    assert mine, f"no audit row for job {job['id']} among {len(body['rows'])} rows"
    assert "intent" in {row["phase"] for row in mine}, mine


@pytest.mark.parametrize(
    "path", ["/v1/gateway/reload", "/v1/gateway/apply"], ids=["reload", "apply"]
)
async def test_the_gateway_mutations_plan(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict], path: str
) -> None:
    """Both gateway mutations are ordinary operations with the ordinary
    envelope, so a dry run previews them. ``reload`` exists separately from
    ``apply`` because adopting a hand-written proxy change through ``apply``
    would first overwrite it with a fresh render, so a reload plan that named a
    new generation would be publishing something nobody previewed."""
    resp = await client.post(path, json=op_body())
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    validate(plan, "plan", schemas)
    assert plan["tenant"] is None, plan["tenant"]
    assert_request_id(resp)


async def test_settings_round_trip_bumps_the_registry_generation(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """A settings write is a job like any other, and what proves it landed is
    the READ afterwards plus a higher generation — the counter every plan is
    validated against. A write that changed the document without moving the
    generation would leave a plan computed before it still executable."""
    before = await client.get("/v1/settings")
    assert before.status_code == 200, before.text
    was = before.json()
    validate(was, "settings_response", schemas)
    flipped = not was["ctl"]["gateway_enabled"]

    await submit_and_settle(
        client, "/v1/settings", schemas, method="PUT", args={"ctl": {"gateway_enabled": flipped}}
    )

    after = await client.get("/v1/settings")
    assert after.status_code == 200, after.text
    now = after.json()
    validate(now, "settings_response", schemas)
    assert now["ctl"]["gateway_enabled"] is flipped, now["ctl"]
    assert now["registry_generation"] > was["registry_generation"], (
        f"the generation did not move: {was['registry_generation']} -> "
        f"{now['registry_generation']}"
    )


# =========================================================================== #
# backup and decommission (PR-D)
# =========================================================================== #
async def managed_tenant(client: httpx.AsyncClient) -> str:
    """The fixture tenant the ctl itself supervises.

    The four captured coconut tenants are hand-started with every store
    capability false — a fleet on which `backup` skips both stores and
    `decommission` is refused by design. Asserting the backup legs against one
    of those would be asserting that nothing happened. The fixture carries one
    systemd/svcbvbrc tenant with exclusive stores for exactly this, and it is
    FOUND here rather than named, so the suite does not encode the fixture's
    spelling."""
    resp = await client.get("/v1/fleet")
    assert resp.status_code == 200, resp.text
    for row in resp.json().get("tenants", []):
        if row.get("supervisor") == "systemd":
            return row["name"]
    pytest.skip("the fixture fleet has no ctl-supervised tenant; the backup legs cannot be exercised")


def step_titled(plan_or_job: dict[str, Any], needle: str) -> dict[str, Any] | None:
    """The first step whose title contains *needle*."""
    for step in plan_or_job.get("steps", []):
        if needle in step.get("title", ""):
            return step
    return None


async def test_a_fenced_backup_runs_every_leg_to_succeeded(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """The whole verb, end to end, on the fake host: fence, snapshot both
    stores, copy the state, write the manifest, rename the bundle into place
    and record it in the registry.

    ``succeeded`` and not merely terminal: a build whose drivers refuse leaves
    a job that FAILED with a tidy plan attached, which is the outcome this test
    exists to catch."""
    tenant = await managed_tenant(client)
    job = await submit_and_settle(
        client, f"/v1/tenants/{tenant}/ops/backup", schemas, args={"fence": True}, timeout=60.0
    )
    assert job["state"] == "succeeded", (
        f"the fenced backup settled as {job['state']}: {json.dumps(job.get('error'))} · steps "
        + json.dumps([{"n": s["n"], "title": s["title"], "state": s["state"], "error": s.get("error")}
                      for s in job["steps"]])
    )
    assert job["result"]["fenced"] is True and job["result"]["best_effort"] is False, job["result"]

    # The manifest step, by name: a bundle without one is a directory of files
    # no restore can read.
    manifest = step_titled(job, "bundle manifest")
    assert manifest is not None, [s["title"] for s in job["steps"]]
    assert manifest["state"] == "succeeded", manifest

    # The rename is the LAST write of the bundle, and it happens after the
    # manifest: that ordering is what makes `<id>.partial` mean "unfinished".
    rename = step_titled(job, "rename the bundle into place")
    assert rename is not None and rename["n"] > manifest["n"], [s["title"] for s in job["steps"]]

    # And the registry learned its recovery point, unverified.
    record = step_titled(job, "record the bundle as this tenant's last backup")
    assert record is not None and record["state"] == "succeeded", record
    shown = await client.get(f"/v1/tenants/{tenant}")
    assert shown.status_code == 200, shown.text
    last = shown.json()["summary"]["last_backup"]
    assert last and last["fenced"] is True and last["verified"] is False, last


async def test_the_elasticsearch_leg_verifies_and_unregisters_its_repository(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """`_restore` has no dry run, so the bundle's proof that its snapshot is
    readable is a SECOND, read-only registration of the same directory, listed
    and then dropped. Both registrations are the ctl's own and both are
    unregistered before the directory moves — a repository elasticsearch still
    holds while its files walk away is the way this leg corrupts a cluster."""
    tenant = await managed_tenant(client)
    job = await submit_and_settle(
        client, f"/v1/tenants/{tenant}/ops/backup", schemas, args={"fence": True}, timeout=60.0
    )
    assert job["state"] == "succeeded", job.get("error")
    leg = step_titled(job, "elasticsearch index into a per-bundle repo")
    assert leg is not None, [s["title"] for s in job["steps"]]
    assert "verify it and move it into the bundle" in leg["title"], leg["title"]
    ids = leg["external_ids"]
    assert any(i.startswith("es:verify:") for i in ids), (
        f"the verification repository is not recorded before it is registered: {ids}"
    )
    assert any(i.startswith("es:ctl-") for i in ids), ids


async def test_an_unfenced_backup_is_best_effort(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """Without ``--fence`` nothing stopped the tenant writing, so the bundle is
    `best_effort` and the plan says in as many words that it can never be
    restored, handed over or decommissioned from."""
    tenant = await managed_tenant(client)
    preview = await client.post(f"/v1/tenants/{tenant}/ops/backup", json=op_body(args={}))
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    validate(plan, "plan", schemas)
    assert any("best_effort" in w for w in plan["warnings"]), plan["warnings"]
    assert step_titled(plan, "fence verify") is None, [s["title"] for s in plan["steps"]]
    assert step_titled(plan, "read-only") is None, [s["title"] for s in plan["steps"]]

    job = await submit_and_settle(client, f"/v1/tenants/{tenant}/ops/backup", schemas, args={}, timeout=60.0)
    assert job["state"] == "succeeded", job.get("error")
    assert job["result"]["best_effort"] is True and job["result"]["fenced"] is False, job["result"]


async def test_without_recipients_the_secrets_are_excluded_and_the_plan_says_so(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """The fail-closed rule, read where an operator would read it. This daemon
    has no age recipient configured, so the bundle is written WITHOUT the
    tenant's secret files — never with them in the clear — and the plan warns
    before anything runs that a restore from it will mint fresh credentials."""
    tenant = await managed_tenant(client)
    preview = await client.post(f"/v1/tenants/{tenant}/ops/backup", json=op_body(args={"fence": True}))
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    warned = [w for w in plan["warnings"] if "no age recipient is configured" in w]
    assert warned, plan["warnings"]
    assert "EXCLUDED" in warned[0], warned[0]
    skip = step_titled(plan, "skip the encrypted secrets payload")
    assert skip is not None, [s["title"] for s in plan["steps"]]
    # And no step claims it would write a secrets payload.
    for step in plan["steps"]:
        for write in step.get("would_write", []):
            assert not write["path"].endswith("secrets.age"), step["title"]


async def test_the_postgres_leg_is_planned_only_for_a_postgres_local_tenant(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    """A tenant whose relational state is SQLite files has nothing to dump, and
    the plan says so as a skipped step rather than silently omitting it: the
    operator asked for a backup of everything, and "there is no postgres here"
    is an outcome."""
    managed = await managed_tenant(client)
    dumped = await client.post(f"/v1/tenants/{managed}/ops/backup", json=op_body(args={"fence": True}))
    assert dumped.status_code == 200, dumped.text
    assert step_titled(dumped.json(), "dump the tenant's postgres database") is not None, (
        [s["title"] for s in dumped.json()["steps"]]
    )

    sqlite_only = await client.post(f"/v1/tenants/{some_tenant}/ops/backup", json=op_body(args={}))
    assert sqlite_only.status_code == 200, sqlite_only.text
    plan = sqlite_only.json()
    assert step_titled(plan, "dump the tenant's postgres database") is None, [s["title"] for s in plan["steps"]]
    skipped = step_titled(plan, "skip the postgres leg")
    assert skipped is not None, [s["title"] for s in plan["steps"]]
    assert skipped["warnings"], skipped


# =========================================================================== #
# restore --as (PR-D) — the deep verify
# =========================================================================== #
async def fenced_bundle(
    client: httpx.AsyncClient, schemas: dict[str, dict], tenant: str, *, fence: bool = True
) -> str:
    """Take a bundle of *tenant* and return its id."""
    job = await submit_and_settle(
        client, f"/v1/tenants/{tenant}/ops/backup", schemas, args={"fence": fence}, timeout=60.0
    )
    assert job["state"] == "succeeded", json.dumps(job)[:800]
    bundle = (job["result"] or {}).get("bundle")
    assert isinstance(bundle, str) and bundle.endswith("-backup"), job["result"]
    return bundle


async def test_restore_rebuilds_a_fresh_tenant_and_marks_the_bundle_verified(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """The whole verb, end to end: a fenced bundle, a tenant that did not exist
    a moment ago built from the source's artifact, the stores recovered into it,
    its counts checked against the manifest, and — only then — `verified` set on
    the backup.

    ``verified`` is the point. Nothing else in the control plane sets it, and
    `decommission` and `handover` both refuse without it, so this is the
    operation that makes a backup a recovery point rather than a directory."""
    source = await managed_tenant(client)
    bundle = await fenced_bundle(client, schemas, source)
    target = new_tenant_name()

    job = await submit_and_settle(
        client, f"/v1/tenants/{source}/ops/restore", schemas,
        args={"from": bundle, "as": target}, confirm=source, timeout=120.0,
    )
    assert job["state"] == "succeeded", (
        f"the restore settled as {job['state']}: {json.dumps(job.get('error'))} · steps "
        + json.dumps([{"n": s["n"], "title": s["title"], "state": s["state"], "error": s.get("error")}
                      for s in job["steps"]])
    )
    result = job["result"] or {}
    assert result.get("restored_as") == target and result.get("bundle") == bundle, result
    assert result.get("counts"), f"the restore reports no counts: {result}"

    # The fresh tenant is real, active, and the read surface serves it.
    shown = await client.get(f"/v1/tenants/{target}")
    assert shown.status_code == 200, shown.text
    body = shown.json()
    validate(body, "tenant_response", schemas)
    assert body["summary"]["state"] == "active", body["summary"]
    assert body["registry"]["last_ops"].get("restore", {}).get("outcome") == "succeeded", (
        body["registry"]["last_ops"]
    )

    # …and the SOURCE's bundle is now proved.
    src = await client.get(f"/v1/tenants/{source}")
    assert src.status_code == 200, src.text
    last = src.json()["summary"]["last_backup"]
    assert last and last["verified"] is True, (
        f"a succeeded restore did not mark {source}'s bundle verified: {last}"
    )

    # The restored tenant's credentials are FRESH and come back exactly once.
    first = await client.get(f"/v1/jobs/{job['id']}/secrets")
    assert first.status_code == 200, first.text
    validate(first.json(), "secrets_response", schemas)
    labels = {s["label"] for s in first.json()["secrets"]}
    assert "bootstrap-admin" in labels, labels
    for secret in first.json()["secrets"]:
        assert len(secret["value"]) == 64, f"{secret['label']} is not token_hex(32)"
    second = await client.get(f"/v1/jobs/{job['id']}/secrets")
    assert_error(second, 410, "not_found", schemas)

    # And the job result carries no credential, only fingerprints.
    for key in result.get("keys") or []:
        assert key["fingerprint"].startswith("sha256:"), key
        assert "value" not in key, f"the restore result carries a key VALUE: {key}"


async def test_restore_from_a_best_effort_bundle_fails_with_the_reason(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """An unfenced bundle is a copy of a moving target. The restore does not
    discover that halfway through — the FIRST step reads the manifest and
    refuses — and the refusal says `best_effort`, so an operator reading a failed
    job knows to take a fenced backup rather than to go looking at the stores."""
    source = await managed_tenant(client)
    bundle = await fenced_bundle(client, schemas, source, fence=False)
    target = new_tenant_name()

    job = await submit_and_settle(
        client, f"/v1/tenants/{source}/ops/restore", schemas,
        args={"from": bundle, "as": target}, confirm=source, timeout=120.0,
    )
    assert job["state"] == "failed", json.dumps(job)[:800]
    detail = json.dumps(job.get("error")) + json.dumps([s.get("error") for s in job["steps"]])
    assert "best_effort" in detail, detail

    # And nothing was left behind: the name is free again.
    after = await client.get(f"/v1/tenants/{target}")
    assert after.status_code == 404, (
        f"a failed restore left {target} in the registry ({after.status_code})"
    )


async def test_restore_is_destructive_on_the_SOURCE_and_says_so(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """The plan is the approval document, and the thing being approved is an
    operation on TWO tenants. Its confirm value is the source's name — that is
    the row whose backup record changes and the row the lock is held on — and
    the plan names the fresh tenant in its steps so an operator can see what is
    about to be built."""
    source = await managed_tenant(client)
    bundle = await fenced_bundle(client, schemas, source)
    target = new_tenant_name()

    preview = await client.post(
        f"/v1/tenants/{source}/ops/restore",
        json=op_body(dry_run=True, args={"from": bundle, "as": target}),
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    validate(plan, "plan", schemas)
    assert plan["requires_confirm"] is True and plan["confirm_value"] == source, plan

    titles = [s["title"] for s in plan["steps"]]
    for needle in ("verify the bundle", "allocate " + target, "recover every collection",
                   "verify the restored tenant against the bundle"):
        assert any(needle in t for t in titles), f"no step matching {needle!r} in {titles}"

    # The fresh tenant's credential file is named and NOT previewed.
    writes = {w["path"]: w for s in plan["steps"] for w in s["would_write"]}
    secrets = next((p for p in writes if p.endswith("/config/secrets.env")), None)
    assert secrets and target in secrets, f"the plan does not name {target}'s secrets.env: {sorted(writes)}"
    assert not writes[secrets]["preview"], writes[secrets]["preview"]

    # An execute without the confirm is 428, not a restore.
    resp = await client.post(
        f"/v1/tenants/{source}/ops/restore",
        json=op_body(dry_run=False, args={"from": bundle, "as": target}),
    )
    assert resp.status_code == 428, f"a destructive op ran without a confirm: {resp.status_code}"


async def test_decommission_refuses_a_tenant_the_ctl_does_not_run(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    """v1 quarantines only what the ctl supervises and owns. Renaming the data
    directory of a hand-started tenant belonging to another account is
    destroying somebody else's work with a tool that cannot put it back, so it
    is refused at PLAN time — before any lock, and with a sentence naming what
    the tenant actually is."""
    resp = await client.post(f"/v1/tenants/{some_tenant}/ops/decommission", json=op_body(args={}))
    err = assert_error(resp, 409, "refused", schemas)
    assert "quarantines only what the ctl runs" in err["detail"], err["detail"]
    assert "not wired" not in err["detail"], (
        f"decommission must be refused as a policy decision, not as an unwired engine: {err['detail']}"
    )


async def test_decommission_of_a_managed_tenant_needs_a_verified_bundle(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """The prerequisite that makes quarantine reversible: a fenced bundle a
    restore has PROVED. The fixture tenant's bundles are unverified (only a
    `restore --as` sets that flag), so the refusal names the bundle and what to
    do with it rather than proceeding."""
    tenant = await managed_tenant(client)
    resp = await client.post(f"/v1/tenants/{tenant}/ops/decommission", json=op_body(args={}))
    err = assert_error(resp, 409, "refused", schemas)
    assert "backup" in err["detail"], err["detail"]


# =========================================================================== #
# `tenant create` — the verb that makes a tenant (#537)
#
# Every case runs against the FIXTURE host (--fake-drivers), which carries one
# prepared artifact. The tenant names are unique per run because a create that
# succeeded is a registry row that stays there for the life of the daemon: a
# fixed name would make the second test in the session assert against the first
# test's tenant.
# =========================================================================== #

#: The artifact the fixture daemon has prepared (go/internal/ctl/api/fake.go).
CONFORMANCE_ARTIFACT = "conformance-artifact"


def new_tenant_name() -> str:
    """A fresh tenant name matching ``^[a-z][a-z0-9-]{0,31}$``."""
    return f"conf-{uuid.uuid4().hex[:8]}"


async def test_create_dry_run_plans_every_driver_it_will_touch(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """A create's dry run is the approval document for the whole operation, so
    it has to name every kind of work the job will do — the allocation, the
    directories, the credential file, the env files, the checkout, the UI build,
    the units, the start, the tenant-API calls and the gateway publish. A plan
    that showed only the steps whose drivers happen to be wired would be an
    approval for something other than what runs."""
    name = new_tenant_name()
    resp = await client.post(
        "/v1/tenants",
        json=op_body(dry_run=True, args={"name": name, "artifact_id": CONFORMANCE_ARTIFACT}),
    )
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    validate(plan, "plan", schemas)
    assert plan["op"] == "create" and plan["tenant"] == name, plan
    kinds = {s["kind"] for s in plan["steps"]}
    for want in {"registry", "fs", "envfile", "git", "apptainer", "systemd", "probe", "nginx"}:
        assert want in kinds, f"no {want!r} step in the create plan: {sorted(kinds)}"

    # The whole tenant is visible: its env file and its units are previewed…
    writes = {w["path"]: w for s in plan["steps"] for w in s["would_write"]}
    env = next((p for p in writes if p.endswith("/config/tenant.env")), None)
    assert env, f"the plan previews no tenant.env: {sorted(writes)}"
    assert writes[env]["preview"], "tenant.env was previewed as null"
    unit = next((p for p in writes if p.endswith("-api.service")), None)
    assert unit, f"the plan previews no api unit: {sorted(writes)}"

    # …and the credential file is named WITHOUT a preview. plan.json makes the
    # preview null for secret-bearing content, and a create's secrets.env is the
    # one file in the fleet whose whole content is credentials.
    secrets = next((p for p in writes if p.endswith("/config/secrets.env")), None)
    assert secrets, f"the plan does not name secrets.env: {sorted(writes)}"
    assert not writes[secrets]["preview"], (
        f"secrets.env was previewed: {writes[secrets]['preview'][:200]!r}"
    )

    # A dry run writes nothing: the tenant does not exist afterwards.
    after = await client.get(f"/v1/tenants/{name}")
    assert after.status_code == 404, f"the dry run created the tenant: {after.status_code}"


async def test_create_executes_and_delivers_the_credentials_once(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """The end-to-end case: a create that succeeds produces a tenant the read
    surface serves, a result that carries FINGERPRINTS, and an envelope that
    carries the values exactly once."""
    name = new_tenant_name()
    job = await submit_and_settle(
        client, "/v1/tenants", schemas,
        args={
            "name": name, "artifact_id": CONFORMANCE_ARTIFACT,
            "keys": [{"label": "ops", "role": "user"}],
        },
        timeout=60.0,
    )
    assert job["state"] == "succeeded", json.dumps(job)[:1200]

    result = job["result"] or {}
    assert result.get("name") == name, result
    assert isinstance(result.get("ports"), dict), result
    assert result.get("artifact_id") == CONFORMANCE_ARTIFACT, result
    keys = result.get("keys") or []
    labels = {k["label"] for k in keys}
    assert "bootstrap-admin" in labels, (
        f"create must always mint the bootstrap admin the ctl itself uses: {labels}"
    )
    assert "ops" in labels, labels
    for k in keys:
        assert k["fingerprint"].startswith("sha256:"), k
        assert "value" not in k, f"the job result carries a key VALUE: {k}"

    # The tenant is real: the read surface serves it, with no secret in it.
    shown = await client.get(f"/v1/tenants/{name}")
    assert shown.status_code == 200, shown.text
    body = shown.json()
    validate(body, "tenant_response", schemas)
    assert body["summary"]["state"] in {"active", "provisioned"}, body["summary"]
    assert body["registry"]["ports"]["base"] == result["ports"]["base"], (
        "the tenant the read surface serves is not the one the job reported"
    )
    # Fingerprints, never values: the registry is not a place a key lives.
    for k in body["registry"]["keys"]:
        assert k["fingerprint"].startswith("sha256:"), k
        assert "value" not in k, k

    # The values come back once, and only once.
    first = await client.get(f"/v1/jobs/{job['id']}/secrets")
    assert first.status_code == 200, first.text
    validate(first.json(), "secrets_response", schemas)
    delivered = {s["label"]: s["value"] for s in first.json()["secrets"]}
    assert set(delivered) == labels, f"envelope {sorted(delivered)} != ledger {sorted(labels)}"
    for label, value in delivered.items():
        assert len(value) == 64, f"{label} is {len(value)} characters, want token_hex(32)"
    second = await client.get(f"/v1/jobs/{job['id']}/secrets")
    assert_error(second, 410, "not_found", schemas)


async def test_create_is_idempotent_under_one_key(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """The same create sent twice under the same idempotency key is ONE tenant.
    Without this, a client that retried after a dropped response would allocate
    a second port block and a second set of credentials for a tenant that
    already exists."""
    name = new_tenant_name()
    key = idem()
    args = {"name": name, "artifact_id": CONFORMANCE_ARTIFACT}
    body = op_body(dry_run=False, args=args, idempotency_key=key)
    body.update(await doctor_force(client, "/v1/tenants", args=args))

    first = await client.post("/v1/tenants", json=body)
    assert first.status_code == 202, first.text
    job = await poll_to_terminal(client, first.json()["id"], schemas, timeout=60.0)
    assert job["state"] == "succeeded", json.dumps(job)[:800]

    again = await client.post("/v1/tenants", json=body)
    assert again.status_code == 202, again.text
    assert again.json()["id"] == job["id"], (
        f"a replay minted a second job ({again.json()['id']} != {job['id']}) — and therefore "
        "a second tenant"
    )

    # A DIFFERENT request under the same key is a conflict, not a silent
    # substitution of one operation for another.
    other = dict(body, args={"name": new_tenant_name(), "artifact_id": CONFORMANCE_ARTIFACT})
    assert_error(await client.post("/v1/tenants", json=other), 409, "duplicate", schemas)


async def test_create_with_postgres_local_adds_the_unit_and_the_pg_secrets(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """``postgres: local`` is the PR-D addition to CreateArgs. It changes three
    things an operator can see in the plan: a postgres unit, the instance's two
    writable directories, and the provision record that says which kind this
    tenant was built as."""
    name = new_tenant_name()
    resp = await client.post(
        "/v1/tenants",
        json=op_body(
            dry_run=True,
            args={"name": name, "artifact_id": CONFORMANCE_ARTIFACT, "postgres": "local"},
        ),
    )
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    validate(plan, "plan", schemas)

    writes = {w["path"]: w for s in plan["steps"] for w in s["would_write"]}
    pg_unit = next((p for p in writes if p.endswith("-postgres.service")), None)
    assert pg_unit, f"postgres: local planned no postgres unit: {sorted(writes)}"
    body = writes[pg_unit]["preview"] or ""
    assert "EnvironmentFile=" in body and "/config/secrets.env" in body, body
    # The password is a REFERENCE, never a literal: /rag/config/ctl/units is
    # world-readable. (The plan redactor flattens the reference too, so what is
    # asserted here is the absence of an assignment.)
    assert "TENANT_PG_PASSWORD=" not in body, f"the unit assigns the password:\n{body}"

    provision = next((p for p in writes if p.endswith("/config/provision.env")), None)
    assert provision, sorted(writes)
    assert "TENANT_STORE_KIND=postgres-local" in (writes[provision]["preview"] or ""), (
        writes[provision]["preview"]
    )

    targets = {t for s in plan["steps"] for t in s["targets"]}
    for want in ("postgres/data", "postgres/run"):
        assert any(want in t for t in targets), (
            f"the instance's {want} directory is never created; apptainer refuses a missing bind"
        )

    # The default is sqlite, and it plans none of that.
    plain = await client.post(
        "/v1/tenants",
        json=op_body(dry_run=True, args={"name": new_tenant_name(), "artifact_id": CONFORMANCE_ARTIFACT}),
    )
    assert plain.status_code == 200, plain.text
    plain_writes = {w["path"] for s in plain.json()["steps"] for w in s["would_write"]}
    assert not any(p.endswith("-postgres.service") for p in plain_writes), plain_writes


@pytest.mark.parametrize(
    "args,why",
    [
        ({"artifact_id": "not-prepared"}, "an artifact nobody prepared"),
        (
            {"artifact_id": CONFORMANCE_ARTIFACT, "identity_provider": "none",
             "admin_subjects": ["bvbrc:alice@patricbrc.org"]},
            "admin subjects with no identity provider to issue them",
        ),
        (
            {"artifact_id": CONFORMANCE_ARTIFACT, "identity_provider": "bvbrc",
             "admin_subjects": ["oidc:alice@example.com"]},
            "an admin subject issued by somebody other than the provider",
        ),
        (
            {"artifact_id": CONFORMANCE_ARTIFACT, "template_from": "zz-not-a-tenant"},
            "a template tenant that does not exist",
        ),
    ],
    ids=["unknown-artifact", "subjects-without-provider", "foreign-issuer", "unknown-template"],
)
async def test_create_refusals_are_409_with_a_reason(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict],
    args: dict[str, Any], why: str,
) -> None:
    """Each of these is a policy refusal, answered 409 ``refused`` at PLAN time
    — before a lock is taken and before anything is written. A 422 would tell
    the caller its document was malformed and invite it to fix the spelling; it
    is not malformed, it is asking for something the control plane will not do."""
    resp = await client.post(
        "/v1/tenants", json=op_body(dry_run=True, args=dict(args, name=new_tenant_name()))
    )
    err = assert_error(resp, 409, "refused", schemas)
    assert "not wired" not in err["detail"], f"{why}: refused for the wrong reason: {err['detail']}"


async def test_create_refuses_a_name_that_is_taken(
    job_engine: None, client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    """A tenant name is a directory, a gateway segment and a set of instance
    names. Creating a second tenant with one that is taken would have two
    tenants writing the same tree."""
    resp = await client.post(
        "/v1/tenants",
        json=op_body(dry_run=True, args={"name": some_tenant, "artifact_id": CONFORMANCE_ARTIFACT}),
    )
    err = assert_error(resp, 409, "refused", schemas)
    assert some_tenant in err["detail"], err["detail"]


@pytest.mark.parametrize("verb", ["artifact-prepare"])
async def test_a_cli_only_op_has_no_http_route(
    client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str, verb: str
) -> None:
    """``fleet artifact prepare`` is a job like any other — planned, locked,
    audited — and it has NO route. It runs ``npm ci`` (the one step in the
    control plane that reaches the network) and it takes a repository path, so
    it is a trusted-operator, ``--direct`` operation. The router refuses it as a
    verb outside the enum, which is the same answer a name nobody defined gets:
    a CLI-only op must not be half-reachable.

    Not gated on the engine: the verb enum is the router's own, so this holds on
    any daemon that answers."""
    resp = await client.post(f"/v1/tenants/{some_tenant}/ops/{verb}", json=op_body())
    body = assert_error(resp, 422, "validation", schemas)
    assert body.get("extra", {}).get("fields") == ["verb"], body
    for path in (f"/v1/fleet/artifacts", "/v1/artifacts"):
        other = await client.post(path, json=op_body())
        assert other.status_code in (404, 405), (
            f"POST {path} answered {other.status_code}; a CLI-only op must have no route"
        )
