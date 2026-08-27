"""Shared fixtures for RAGStack conformance tests.

All tests run as pure HTTP black-box tests against a running
RAGStack-compatible API server. Nothing here imports from ``python/`` or
``go/`` — the suite must be able to test an implementation it cannot import.

**The skip/fail doctrine** (shared with ``python/tests/conftest.py``, #432/#405):

* an **absent** credential or opt-in → *skip*, loudly, naming the variable;
* a credential that is **present but is not what it claims to be** → *fail*,
  naming the violated precondition and both sides of the comparison.

A fixture that guards a precondition and cannot prove it is worse than no
fixture: every test built on it passes while asserting nothing. That is what
``run_authz_keyed.sh`` did by wiring ``RAGSTACK_API_KEY`` and
``RAGSTACK_API_KEY_NONADMIN`` to the **same value** (#405) — the suite had two
names for one principal and therefore one principal.

**Principals.** The suite reads five key variables; ``run_authz_keyed.sh``
provisions all five, distinct, against a server it boots itself:

==============================  ==================================================
``RAGSTACK_API_KEY``            the suite's default principal. :func:`client`
                                sends it on every request. The scripted keyed run
                                wires this to the **admin** key — several files
                                probe admin-gated surfaces through it.
``RAGSTACK_API_KEY_ADMIN``      an admin principal (P4). ``GET /v1/config`` → 200.
``RAGSTACK_API_KEY_NONADMIN``   an authenticated non-admin, **distinct from every
                                other key**. ``GET /v1/config`` → 403.
``RAGSTACK_API_KEY_P2``         the ``caller_without_default_access`` persona —
                                non-admin, and its readable set EXCLUDES the
                                registry pointer's target. See the fixture.
``RAGSTACK_API_KEY_B``          a second tenant-mapped principal, for the
                                cross-tenant leak check in test_stats_stores.py.
==============================  ==================================================
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import AsyncGenerator, Iterator, NoReturn

import httpx
import pytest
import pytest_asyncio

from personas import P2_COLLECTION_PREFIX, PersonaFacts, assert_persona_preconditions

# --------------------------------------------------------------------------- #
# Credential-gated skips
# --------------------------------------------------------------------------- #
#: Every skip caused by a *missing or insufficient principal* carries this
#: prefix. ``run_authz_keyed.sh`` greps ``-rs`` output for it and fails the run
#: when one appears: on a server the script itself provisioned, "I had no
#: credential for that" is a harness bug, not a legitimate absence. Skips for any
#: OTHER reason (no identity provider configured, a surface the impl does not
#: have) are untouched — a blanket zero-skip rule over the whole suite would be a
#: permanent red rather than a signal.
CREDENTIAL_SKIP = "RAGSTACK_CREDENTIAL_SKIP:"


def skip_no_credential(reason: str) -> NoReturn:
    """Skip because the principal this assertion needs is absent or too weak.

    Tagged so a run that *claims* to have provisioned every principal can prove
    it did — see :data:`CREDENTIAL_SKIP`.
    """
    pytest.skip(f"{CREDENTIAL_SKIP} {reason}")


def key(name: str) -> str | None:
    """One ``RAGSTACK_API_KEY*`` variable, treating empty as unset."""
    return os.environ.get(name) or None


def api_key_headers(name: str = "RAGSTACK_API_KEY") -> dict[str, str]:
    """``X-API-Key`` header for *name*, or ``{}`` on a keyless server."""
    k = key(name)
    return {"X-API-Key": k} if k else {}


# --------------------------------------------------------------------------- #
# Server under test
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def base_url() -> str:
    """Base URL of the RAGStack API server under test.

    **Required, deliberately no default.** It used to default to
    ``http://localhost:8000`` — the Python port convention — which on the
    deployment host resolves to a live *production* API. A bare
    ``pytest conformance/`` therefore pointed a suite that CREATES AND DELETES
    COLLECTIONS at production. That is the same "a default that resolves to
    production" class as #363/#369/#392/#407/#432 (catalogued in
    ``docs/plans/README.md``); the port convention lives in the Make targets,
    where it cannot fire by accident.
    """
    url = os.environ.get("RAGSTACK_BASE_URL")
    if not url:
        raise pytest.UsageError(
            "RAGSTACK_BASE_URL is required and has no default: this suite writes "
            "to whatever server it is pointed at, and the old default "
            "(http://localhost:8000) resolves to a live production API on the "
            "deployment host. Use `make test-conformance-python` (:8000), "
            "`make test-conformance-go` (:8080) or `make test-conformance-keyed` "
            "(self-booted, in-memory), or export RAGSTACK_BASE_URL yourself."
        )
    return url


@pytest.fixture(scope="session")
def impl() -> str:
    """Implementation identifier (e.g. 'python', 'go', 'rust')."""
    return os.environ.get("RAGSTACK_IMPL", "unknown")


@pytest.fixture(scope="session")
def auth_headers() -> dict[str, str]:
    """The default principal's auth header — ``{}`` on a keyless server."""
    return api_key_headers("RAGSTACK_API_KEY")


@pytest.fixture(scope="session")
def admin_headers() -> dict[str, str]:
    """The admin principal's auth header, or ``{}`` when none is configured."""
    return api_key_headers("RAGSTACK_API_KEY_ADMIN")


@pytest.fixture(scope="session")
def nonadmin_headers() -> dict[str, str]:
    """A non-admin principal's auth header, or ``{}`` when none is configured."""
    return api_key_headers("RAGSTACK_API_KEY_NONADMIN")


@pytest_asyncio.fixture
async def client(
    base_url: str, auth_headers: dict[str, str]
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client for the server under test, **authenticated**.

    Sends ``X-API-Key: $RAGSTACK_API_KEY`` on every request when that variable
    is set, and nothing when it is not — so one fixture serves a keyless dev
    server and a key-protected one. Before #405 it sent no credential at all,
    so eleven conformance files (query, retrieve, ingest, documents, graph,
    jobs, health, request-id, schema-validation, error-shape, stats-models)
    could only be run against a KEYLESS server: against a keyed one every
    assertion in them was a 401. "The suite is green" therefore meant "the suite
    has never been run against a server with authentication on".

    A test that must be **unauthenticated** (every 401 assertion, and the
    identity suite's which-credential-won checks) uses :func:`anon_client`:
    httpx MERGES per-request headers with the client's defaults, so passing
    ``headers={}`` here would not strip the key.

    Function-scoped: a session-scoped AsyncClient binds its transport to the
    first test's event loop, so under pytest-asyncio's per-test loops the reused
    client raises "Event loop is closed" on the second test. A fresh client per
    test is cheap for black-box HTTP and avoids the cross-loop teardown crash.
    """
    async with httpx.AsyncClient(
        base_url=base_url, timeout=30.0, headers=auth_headers
    ) as c:
        yield c


@pytest_asyncio.fixture
async def anon_client(base_url: str) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client that sends **no** credential unless a test adds one.

    The 401 assertions need a client whose baseline is anonymous, and the
    identity tests need one whose only credential is the ``Authorization``
    header they set — a merged-in ``X-API-Key`` would turn a "which credential
    is this?" 401 into a "two credentials" 400. :func:`client` cannot serve
    either, because httpx merges request headers into the client's defaults
    rather than replacing them.
    """
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="session")
def schemas() -> dict[str, dict]:
    """Load all JSON schemas from ``contracts/schemas/`` and return them
    as a mapping of schema name (without extension) to parsed dict.
    """
    schemas_dir = Path(__file__).resolve().parent.parent / "contracts" / "schemas"
    result: dict[str, dict] = {}
    if schemas_dir.is_dir():
        for schema_file in sorted(schemas_dir.glob("*.json")):
            with open(schema_file, encoding="utf-8") as fh:
                result[schema_file.stem] = json.load(fh)
    return result


# --------------------------------------------------------------------------- #
# The `caller_without_default_access` persona (P2) — #405
# --------------------------------------------------------------------------- #
async def _listing(
    c: httpx.AsyncClient, headers: dict[str, str]
) -> tuple[list[str], str]:
    resp = await c.get("/v1/collections", headers=headers)
    assert resp.status_code == 200, (
        "PRECONDITION key_is_valid: GET /v1/collections must answer 200 for a "
        f"principal this suite was told is valid; got {resp.status_code}: "
        f"{resp.text}"
    )
    body = resp.json()
    return [entry["id"] for entry in body["collections"]], body["default"]


def _sync_call(
    method: str, url: str, headers: dict[str, str], body: dict | None = None
) -> tuple[int, object]:
    """One blocking HTTP call, stdlib only.

    The persona's *write* step runs in a session-scoped fixture, and a
    session-scoped **async** fixture would have to hand a client bound to one
    event loop to tests running in per-test loops (the reason :func:`client` is
    function-scoped in the first place). Doing the two write calls synchronously
    sidesteps that entirely.
    """
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = dict(headers)
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, headers=hdrs, data=data)
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
def _p2_owns_a_collection(base_url: str, impl: str) -> Iterator[int | None]:
    """Session-scoped: make sure P2 owns exactly one collection, and clean up.

    **Session**-scoped on purpose. Per test, this created and purged a
    collection on every persona test — and `POST /v1/collections` is rate
    limited, so the fifth one came back 429 and the fixture failed its own
    "P2 has a collection" precondition. It also meant the suite's write volume
    scaled with its test count, which for a fixture that can be pointed at a
    real server is the wrong shape (plan R5).

    Yields the ``POST /v1/collections`` status, or ``None`` when P2 already
    owned something and no create was attempted — the persona guard reports it
    so "empty readable set" and "creation is disabled on this deployment" are
    distinguishable.
    """
    p2, admin = key("RAGSTACK_API_KEY_P2"), key("RAGSTACK_API_KEY_ADMIN")
    if impl != "python" or not p2 or not admin:
        yield None  # the persona fixture itself decides skip vs. fail
        return
    p2_headers = {"X-API-Key": p2}
    base = base_url.rstrip("/")

    def listing(headers: dict[str, str]) -> tuple[list[str], str]:
        status, body = _sync_call("GET", f"{base}/v1/collections", headers)
        assert status == 200 and isinstance(body, dict), (
            "PRECONDITION key_is_valid: GET /v1/collections must answer 200 for "
            f"a principal this suite was told is valid; got {status}: {body!r}"
        )
        return [c["id"] for c in body["collections"]], body["default"]

    p2_ids, _ = listing(p2_headers)
    _, pointer = listing({"X-API-Key": admin})

    created: str | None = None
    create_status: int | None = None
    if not [cid for cid in p2_ids if cid != pointer]:
        # Create is open to any authenticated principal (ADR-0003); the
        # `embedding`/`chunk` build-spec overrides are admin-only (#287), so both
        # are omitted and the server-default build spec is used.
        create_status, body = _sync_call(
            "POST",
            f"{base}/v1/collections",
            p2_headers,
            {"id": f"{P2_COLLECTION_PREFIX}mine"},
        )
        if create_status == 201 and isinstance(body, dict):
            created = body["id"]
    try:
        yield create_status
    finally:
        if created:
            _sync_call(
                "DELETE", f"{base}/v1/collections/{created}?purge=true", p2_headers
            )
            # Verify by LISTING, never by trusting the delete's status: a
            # teardown that believes a response it did not check is how scratch
            # collections accumulate on a real server (plan R5).
            remaining, _ = listing(p2_headers)
            assert created not in remaining, (
                f"teardown did not remove {created!r}: it is still in P2's "
                f"listing ({remaining}). Delete it by hand before re-running."
            )


@pytest_asyncio.fixture
async def caller_without_default_access(
    base_url: str, impl: str, _p2_owns_a_collection: int | None
):
    """P2: an authenticated non-admin whose readable set EXCLUDES the registry
    pointer's target — the #201 default new-user state, not an edge case.

    This is ``python/tests/api/conftest.py::caller_without_default_access``
    proven over HTTP instead of by monkeypatch: the same persona, the same
    vacuity guard, one layer out. See :mod:`personas` for what each precondition
    is protecting against.

    **Skips** when ``RAGSTACK_API_KEY_P2`` or ``RAGSTACK_API_KEY_ADMIN`` is
    unset — ``D``, the pointer's target, is only observable through an admin
    listing, and P2 by construction cannot see it. Every other precondition is
    **asserted**, and a miss is a hard failure: a configured persona that is not
    the persona is a harness bug, and skipping past it would rebuild the exact
    vacuity this issue exists to remove.

    Yields a namespace mirroring the Python fixture's shape, so the two suites
    read alike: ``client``, ``headers``, ``admin_headers``, ``default_id``
    (``D``), ``readable_id``, ``impl``.

    **The persona writes** — once per session, in
    :func:`_p2_owns_a_collection`: when P2 owns nothing it creates
    ``conf-p2-mine`` and deletes it again, verifying by listing. Pointed at a
    real server with real credentials it will do that *there*. The documented
    invocation is ``make test-conformance-keyed``, which self-boots an in-memory
    server; exporting a production key into a conformance run hands this suite
    write access to production, and the suite cannot tell the difference.
    """
    from types import SimpleNamespace

    if impl != "python":
        # Not a credential skip: the Go phase-1 scaffold has no auth middleware
        # at all, so there is no ownership seam for a second principal to be on
        # the far side of. Asserting one there would be asserting a stub.
        pytest.skip("the ownership seam is python-authoritative in phase 1")

    p2 = key("RAGSTACK_API_KEY_P2")
    admin = key("RAGSTACK_API_KEY_ADMIN")
    if not p2 or not admin:
        skip_no_credential(
            "the P2 persona needs BOTH a restricted non-admin key "
            "(RAGSTACK_API_KEY_P2) and an admin key (RAGSTACK_API_KEY_ADMIN, to "
            "observe the registry pointer's target, which P2 by construction "
            "cannot see). `make test-conformance-keyed` provisions both."
        )
    p2_headers = {"X-API-Key": p2}
    admin_headers = {"X-API-Key": admin}

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as c:
        # --- observe -------------------------------------------------------- #
        anon = await c.get("/v1/collections")
        p2_config = await c.get("/v1/config", headers=p2_headers)
        p2_ids, _ = await _listing(c, p2_headers)
        _, pointer_target = await _listing(c, admin_headers)

        # A4: the TENANT_COLLECTIONS allowlist IS observable black-box —
        # `restricted_to` is null exactly when the caller is unconfined
        # (api/routers/stats.py, contract at openapi.yaml's tenants_response).
        # The plan called this a blind spot; it is not one.
        tenants = await c.get("/v1/stats/tenants", headers=p2_headers)
        restricted_to: object = (
            tenants.json().get("restricted_to", "<absent>")
            if tenants.status_code == 200
            else f"<GET /v1/stats/tenants returned {tenants.status_code}>"
        )

        # --- prove it is the persona, or fail naming what is not ------------- #
        # `_p2_owns_a_collection` (session-scoped) has already created
        # `conf-p2-mine` if P2 owned nothing; it hands back the create's status
        # so the guard can tell "empty readable set" from "this deployment
        # refuses non-admin creates".
        assert_persona_preconditions(
            PersonaFacts(
                anonymous_status=anon.status_code,
                p2_config_status=p2_config.status_code,
                p2_ids=p2_ids,
                pointer_target=pointer_target,
                restricted_to=restricted_to,
                create_status=_p2_owns_a_collection,
            )
        )

        yield SimpleNamespace(
            client=c,
            headers=p2_headers,
            admin_headers=admin_headers,
            default_id=pointer_target,
            readable_id=next(cid for cid in p2_ids if cid != pointer_target),
            impl=impl,
        )
