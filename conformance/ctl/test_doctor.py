"""Conformance: ``GET /v1/doctor`` — fleet, tenant and op-scoped runs."""

from __future__ import annotations

import httpx
import pytest

from ctl.helpers import assert_error, assert_request_id, find_secret_values, validate

pytestmark = pytest.mark.asyncio


async def test_fleet_doctor_conforms(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await client.get("/v1/doctor")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate(body, "doctor_response", schemas)
    assert_request_id(resp)
    assert body["scope"] == {"tenant": None, "op": None}
    assert find_secret_values(body) == []


async def test_status_is_the_max_over_findings(client: httpx.AsyncClient) -> None:
    body = (await client.get("/v1/doctor")).json()
    levels = {f["level"] for f in body["findings"]}
    expected = "red" if "error" in levels else "yellow" if "warn" in levels else "green"
    assert body["status"] == expected, (body["status"], levels)


async def test_hash_is_stable_for_identical_findings(client: httpx.AsyncClient) -> None:
    """Two runs with the same findings must hash the same — the hash is what a
    plan pins and what ``force_with_doctor_diff`` quotes."""
    a = (await client.get("/v1/doctor")).json()
    b = (await client.get("/v1/doctor")).json()
    if a["findings"] == b["findings"]:
        assert a["hash"] == b["hash"]
    else:
        assert a["hash"] != b["hash"]


async def test_tenant_and_op_scoped_doctor(
    client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    resp = await client.get("/v1/doctor", params={"tenant": some_tenant, "op": "start"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate(body, "doctor_response", schemas)
    assert body["scope"] == {"tenant": some_tenant, "op": "start"}
    for f in body["findings"]:
        assert f["tenant"] in (some_tenant, None), f


async def test_unknown_tenant_is_404(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await client.get("/v1/doctor", params={"tenant": "zz-conformance-nope"})
    assert_error(resp, 404, "not_found", schemas)


async def test_unknown_op_is_422(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await client.get("/v1/doctor", params={"op": "explode"})
    assert_error(resp, 422, "validation", schemas)
