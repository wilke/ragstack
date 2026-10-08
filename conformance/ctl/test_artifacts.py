"""Conformance: ``GET /v1/artifacts`` — the prepared artifacts a ``tenant
create`` picks from.

A viewer read (the matrix row is ``viewer, session: true``). Beyond the
schema: newest ``prepared_at`` first, no host path in the answer, and the
``tenants`` list is the registry's own ``artifact_id`` relation — a tenant the
operator view says was built from an artifact is listed under it.
"""

from __future__ import annotations

import httpx
import pytest

from ctl.helpers import assert_request_id, find_secret_names, find_secret_values, validate

pytestmark = pytest.mark.asyncio


async def test_artifacts_conform_for_a_viewer(
    viewer_client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    resp = await viewer_client.get("/v1/artifacts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate(body, "artifacts_response", schemas)
    assert_request_id(resp)
    stamps = [a["prepared_at"] for a in body["artifacts"]]
    assert stamps == sorted(stamps, reverse=True), f"not newest first: {stamps}"
    assert find_secret_names(body, allow=frozenset()) == []
    assert find_secret_values(body) == []
    assert "/rag/" not in resp.text, "a host path reached a viewer read"


async def test_artifacts_name_the_tenants_built_from_them(
    client: httpx.AsyncClient, viewer_client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """Every tenant whose registry row names an ``artifact_id`` the fleet
    holds appears in that artifact's ``tenants``."""
    listed = (await viewer_client.get("/v1/artifacts")).json()["artifacts"]
    users = {a["id"]: set(a["tenants"]) for a in listed}

    tenants = await client.get("/v1/tenants")
    assert tenants.status_code == 200, tenants.text
    checked = 0
    for row in tenants.json()["tenants"]:
        reg = row.get("registry") or {}
        art = reg.get("artifact_id")
        if not art or art not in users:
            continue
        assert reg["name"] in users[art], (
            f"{reg['name']} records artifact_id {art!r} and is not in its tenants: {sorted(users[art])}"
        )
        checked += 1
    if checked == 0:
        pytest.skip("no tenant in this fleet records a prepared artifact")


async def test_artifacts_need_a_credential(anon_client: httpx.AsyncClient) -> None:
    resp = await anon_client.get("/v1/artifacts")
    assert resp.status_code == 401, resp.text
