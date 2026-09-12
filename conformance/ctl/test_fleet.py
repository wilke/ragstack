"""Conformance: ``GET /v1/fleet`` — the dashboard poll, and the redaction pin.

The fleet view is the viewer shape by construction, so beyond the schema the
assertion that matters is the recursive one: no field NAME anywhere in the
tree matches ``(?i)(api_key|password|secret|token|dsn)`` (no allowlist here —
the fleet has no business naming secrets at all), and no string VALUE looks
like a credential.
"""

from __future__ import annotations

import httpx
import pytest

from ctl.helpers import assert_request_id, find_secret_names, find_secret_values, validate

pytestmark = pytest.mark.asyncio


async def test_fleet_conforms(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await client.get("/v1/fleet")
    assert resp.status_code == 200, resp.text
    validate(resp.json(), "fleet_response", schemas)
    assert_request_id(resp)


async def test_fleet_names_no_secret_field(client: httpx.AsyncClient) -> None:
    body = (await client.get("/v1/fleet")).json()
    assert find_secret_names(body, allow=frozenset()) == []


async def test_fleet_carries_no_credential_shaped_value(client: httpx.AsyncClient) -> None:
    body = (await client.get("/v1/fleet")).json()
    assert find_secret_values(body) == []


async def test_fleet_rows_are_unique_and_named(client: httpx.AsyncClient) -> None:
    rows = (await client.get("/v1/fleet")).json()["tenants"]
    names = [r["name"] for r in rows]
    assert len(names) == len(set(names)), f"duplicate fleet rows: {names}"


async def test_fleet_is_the_same_shape_for_a_viewer(
    client: httpx.AsyncClient, viewer_client: httpx.AsyncClient
) -> None:
    """Viewer and operator get the SAME fleet body (modulo timestamps and live
    health, which can move between the two calls): the contract says the
    summary shape needs no per-role reduction."""
    op = (await client.get("/v1/fleet")).json()
    vw = await viewer_client.get("/v1/fleet")
    assert vw.status_code == 200, vw.text
    vw_body = vw.json()
    assert set(op) == set(vw_body)
    assert [r["name"] for r in op["tenants"]] == [r["name"] for r in vw_body["tenants"]]
    for a, b in zip(op["tenants"], vw_body["tenants"]):
        assert set(a) == set(b), f"viewer row for {a['name']} has different keys"
