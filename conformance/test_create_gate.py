"""Conformance: A3 — a read-only principal cannot create a collection (#287).

Matrix row A3 was ⚠️ "F only": ``test_collections.py``'s create round-trip
*skips* a non-admin caller instead of asserting the refusal, so the C layer had
never seen the gate. This file asserts it.

**What A3 turned out to be, black-box.** The plan proposed minting a read-only
service account via ``POST /v1/admin/service-accounts`` and creating as it. That
endpoint **registers a machine identity but does not mint a credential**
(``routers/service_accounts.py``: *"Records the account; does NOT mint a
credential"*), so conformance cannot obtain a usable one — there is nothing to
send. The refusal mechanism the create handler actually implements for that case
is the deployment-wide ``ALLOW_USER_COLLECTION_CREATE`` switch, whose own code
comment names the read-only service account as the motivating case. So the
honest C-layer assertion is: **with the switch off, a non-admin create is 403
and an admin create still succeeds.**

That is a *deployment-wide* switch, so it cannot coexist in one boot with the P2
persona (whose fixture provisions itself by creating a collection).
``run_authz_keyed.sh`` therefore boots a second, short-lived server with the
switch off and runs only this file against it, gated on
``RAGSTACK_CONFORMANCE_CREATE_DISABLED``. Without that variable the file skips —
not a credential skip: the server under test simply is not configured this way,
and flipping a live server's switch is not conformance's business.
"""

from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio

_ENABLED = os.environ.get("RAGSTACK_CONFORMANCE_CREATE_DISABLED") == "1"
requires_gate = pytest.mark.skipif(
    not _ENABLED,
    reason=(
        "server was not booted with ALLOW_USER_COLLECTION_CREATE=false; set "
        "RAGSTACK_CONFORMANCE_CREATE_DISABLED=1 when it was "
        "(`make test-conformance-keyed` does)"
    ),
)

#: Ids this file may create, distinctive enough that a leftover on a real server
#: is unmistakably conformance litter.
_ADMIN_ID = "conf-creategate-admin"
_USER_ID = "conf-creategate-user"


@requires_gate
async def test_non_admin_create_is_refused(
    client: httpx.AsyncClient, nonadmin_headers: dict[str, str], impl: str
) -> None:
    """403, and the body says which switch did it.

    A bare 403 would be indistinguishable from the build-spec-overrides gate a
    few lines above it in the handler, so the message is part of the contract a
    UI shows: an operator has to be able to tell "ask for the switch" from "drop
    these two fields".
    """
    if impl != "python":
        pytest.skip("collection creation is python-authoritative in phase 1")
    assert nonadmin_headers, (
        "RAGSTACK_CONFORMANCE_CREATE_DISABLED=1 promises a boot with the create "
        "switch off, but no RAGSTACK_API_KEY_NONADMIN was provided — there is "
        "no non-admin to refuse, so this assertion would be vacuous"
    )
    resp = await client.post(
        "/v1/collections", json={"id": _USER_ID}, headers=nonadmin_headers
    )
    assert resp.status_code == 403, (
        f"a non-admin create with ALLOW_USER_COLLECTION_CREATE=false returned "
        f"{resp.status_code}, expected 403: {resp.text}"
    )
    assert "ALLOW_USER_COLLECTION_CREATE" in resp.text, (
        "the refusal must name the switch that caused it; a caller cannot tell "
        f"it from the admin-only build-spec 403 otherwise: {resp.text}"
    )


@requires_gate
async def test_admin_create_still_works(
    client: httpx.AsyncClient, admin_headers: dict[str, str], impl: str
) -> None:
    """The control arm: the switch closes the plane for NON-ADMINS only.

    Without this, a server that had simply lost its create endpoint would pass
    the refusal test above — the classic vacuous-negative.
    """
    if impl != "python":
        pytest.skip("collection creation is python-authoritative in phase 1")
    assert admin_headers, (
        "needs an admin key to prove the switch is selective rather than a "
        "broken endpoint"
    )
    created = await client.post(
        "/v1/collections", json={"id": _ADMIN_ID}, headers=admin_headers
    )
    try:
        assert created.status_code == 201, (
            "ALLOW_USER_COLLECTION_CREATE=false must not close the endpoint for "
            f"admins (the handler exempts them explicitly): {created.text}"
        )
    finally:
        if created.status_code == 201:
            await client.delete(
                f"/v1/collections/{_ADMIN_ID}?purge=true", headers=admin_headers
            )
            listed = await client.get("/v1/collections", headers=admin_headers)
            assert _ADMIN_ID not in {
                c["id"] for c in listed.json()["collections"]
            }, f"teardown left {_ADMIN_ID!r} behind; delete it by hand"
