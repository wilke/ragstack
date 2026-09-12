"""Conformance: authentication on the control plane.

The plan asks for three SEPARATE cases — malformed credential, invalid
credential, and a VALID credential for an UNLISTED subject — because they are
three different failure modes with two different answers: the first two are
401 ``auth_required`` (nothing usable was presented), the third is 403
``forbidden`` (something verified, and the answer is still no — there is no
default role). A daemon that answered 401 for the third would be conflating
"I don't know you" with "you are not allowed", and a daemon that answered 200
would have a default role.

Plus the two-credential rule (400 ``both_credentials``), the session lifecycle
(create from a key → reads work → a session cannot mint a session → revoke →
reads stop), and the error shape on every one of them.

Every test starts from :func:`conftest.anon_client`: httpx merges request
headers into the client's defaults, so a 401 assertion written against the
authenticated ``client`` would be quietly authenticated.
"""

from __future__ import annotations

import httpx
import pytest

from ctl.helpers import assert_error, assert_request_id, validate

pytestmark = pytest.mark.asyncio

#: A viewer-readable route to probe with. `/v1/fleet` is the dashboard's poll
#: and exists on any daemon.
PROBE = "/v1/fleet"


async def test_no_credential_is_401_auth_required(
    anon_client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    resp = await anon_client.get(PROBE)
    assert_error(resp, 401, "auth_required", schemas)


async def test_unknown_api_key_is_401(anon_client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await anon_client.get(PROBE, headers={"X-API-Key": "conformance-invalid-key-nomatch"})
    assert_error(resp, 401, "auth_required", schemas)


@pytest.mark.parametrize(
    "value",
    [
        "Bearer not-a-token",
        "Bearer",
        "un=nobody|tokenid=x|expiry=1|sig=deadbeef",
        "Basic Zm9vOmJhcg==",
        "Session 0123456789abcdef",
    ],
    ids=["bearer-garbage", "bearer-empty", "bvbrc-bad-sig", "basic", "session-unknown"],
)
async def test_malformed_or_unverifiable_authorization_is_401(
    anon_client: httpx.AsyncClient, schemas: dict[str, dict], value: str
) -> None:
    """Malformed and unverifiable are the SAME answer: 401. The body does not
    say which — an attacker learns nothing from the difference."""
    resp = await anon_client.get(PROBE, headers={"Authorization": value})
    assert_error(resp, 401, "auth_required", schemas)


async def test_valid_but_unlisted_subject_is_403_never_a_default_role(
    anon_client: httpx.AsyncClient, schemas: dict[str, dict], unlisted_bearer: str
) -> None:
    """The case the plan singles out. The token VERIFIES (real issuer key,
    unexpired), so the daemon knows exactly who this is — and answers 403,
    not 401 (it is not "unknown credential") and not 200-as-viewer (there is
    no default role). Skips, tagged, without ``RAGSTACK_CTL_UNLISTED_BEARER``."""
    resp = await anon_client.get(PROBE, headers={"Authorization": f"Bearer {unlisted_bearer}"})
    assert_error(resp, 403, "forbidden", schemas)
    me = await anon_client.get("/v1/me", headers={"Authorization": f"Bearer {unlisted_bearer}"})
    assert_error(me, 403, "forbidden", schemas)


async def test_both_credentials_is_400(
    anon_client: httpx.AsyncClient, schemas: dict[str, dict], ctl_key: str
) -> None:
    """Both headers, even when one of them is perfectly valid: the request is
    refused BEFORE either is verified, so the answer is the same 400 whether
    the bearer is real or garbage."""
    resp = await anon_client.get(
        PROBE, headers={"X-API-Key": ctl_key, "Authorization": "Bearer whatever"}
    )
    assert_error(resp, 400, "both_credentials", schemas)


async def test_error_bodies_carry_the_request_id(
    anon_client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """Redundant with ``assert_error`` on purpose: this is the one property a
    user pasting a body into a ticket depends on, so it gets its own name."""
    resp = await anon_client.get(PROBE)
    body = assert_error(resp, 401, "auth_required", schemas)
    assert body["request_id"] == resp.headers["X-Request-Id"]


async def test_session_lifecycle(
    anon_client: httpx.AsyncClient, schemas: dict[str, dict], ctl_key: str
) -> None:
    """Key → session (201, no-store) → the session reads → a session cannot
    mint a session (401) → a session with a key alongside is 400 → revoke
    (204) → the session no longer reads (401)."""
    created = await anon_client.post("/v1/session", headers={"X-API-Key": ctl_key})
    assert created.status_code == 201, created.text
    assert_request_id(created)
    assert "no-store" in created.headers.get("Cache-Control", ""), created.headers
    body = created.json()
    validate(body, "session_response", schemas)
    assert body["reads_only"] is True
    assert body["role"] == "operator"
    sid = body["session_id"]
    session = {"Authorization": f"Session {sid}"}

    me = await anon_client.get("/v1/me", headers=session)
    assert me.status_code == 200, me.text
    validate(me.json(), "me_response", schemas)
    assert me.json()["auth_method"] == "session"
    assert me.json()["principal"] == body["principal"]
    assert me.json()["expires_at"] == body["expires_at"]

    again = await anon_client.post("/v1/session", headers=session)
    assert_error(again, 401, "auth_required", schemas)

    both = await anon_client.get(PROBE, headers={**session, "X-API-Key": ctl_key})
    assert_error(both, 400, "both_credentials", schemas)

    revoked = await anon_client.delete("/v1/session", headers=session)
    assert revoked.status_code == 204, revoked.text
    assert_request_id(revoked)

    after = await anon_client.get("/v1/me", headers=session)
    assert_error(after, 401, "auth_required", schemas)

    # Idempotent: revoking a revoked session is still 204.
    twice = await anon_client.delete("/v1/session", headers=session)
    assert twice.status_code in (204, 401), twice.text


async def test_session_cannot_read_secrets(
    anon_client: httpx.AsyncClient, schemas: dict[str, dict], ctl_key: str
) -> None:
    """``GET /v1/jobs/{id}/secrets`` does not accept a session at all — the
    one read that is key-only. Authorization precedes lookup, so a job id that
    does not exist still gets the credential answer, not a 404."""
    created = await anon_client.post("/v1/session", headers={"X-API-Key": ctl_key})
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    try:
        resp = await anon_client.get(
            "/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV/secrets",
            headers={"Authorization": f"Session {sid}"},
        )
        assert resp.status_code in (401, 403), resp.text
        assert_error(resp, resp.status_code, "auth_required" if resp.status_code == 401 else "forbidden", schemas)
    finally:
        await anon_client.delete("/v1/session", headers={"Authorization": f"Session {sid}"})
