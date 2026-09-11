"""Conformance: ``GET /v1/tenants`` and ``GET /v1/tenants/{name}``.

Beyond the schema: the recursive secret-NAME check (with the two documented
registry exceptions, ``secret_refs`` and ``secrets_file_sha256``), the
secret-VALUE check, the registry ``settings`` guard applied with the
contract's own pattern, the viewer reduction (``registry: null``), and the
404/422 error bodies.
"""

from __future__ import annotations

import httpx
import pytest

from ctl.helpers import (
    READ_ALLOWLIST,
    assert_error,
    assert_request_id,
    find_secret_names,
    find_secret_values,
    forbidden_setting_pattern,
    validate,
)

pytestmark = pytest.mark.asyncio


async def test_list_conforms(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await client.get("/v1/tenants")
    assert resp.status_code == 200, resp.text
    validate(resp.json(), "tenants_response", schemas)
    assert_request_id(resp)


async def test_list_names_no_secret_field_and_no_secret_value(client: httpx.AsyncClient) -> None:
    body = (await client.get("/v1/tenants")).json()
    assert find_secret_names(body, allow=READ_ALLOWLIST) == []
    assert find_secret_values(body) == []


async def test_list_matches_fleet(client: httpx.AsyncClient) -> None:
    fleet = (await client.get("/v1/fleet")).json()["tenants"]
    tenants = (await client.get("/v1/tenants")).json()["tenants"]
    assert [r["name"] for r in fleet] == [t["summary"]["name"] for t in tenants]


async def test_show_conforms(
    client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    resp = await client.get(f"/v1/tenants/{some_tenant}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate(body, "tenant_response", schemas)
    assert_request_id(resp)
    assert body["summary"]["name"] == some_tenant
    assert body["registry"] is not None, "an operator receives the registry row"
    assert body["registry"]["name"] == some_tenant


async def test_show_names_no_secret_field_and_no_secret_value(
    client: httpx.AsyncClient, some_tenant: str
) -> None:
    body = (await client.get(f"/v1/tenants/{some_tenant}")).json()
    assert find_secret_names(body, allow=READ_ALLOWLIST) == []
    assert find_secret_values(body) == []
    # The allowlisted names carry what the contract says and nothing more.
    for ref in body["registry"]["secret_refs"]:
        assert set(ref) == {"key", "file"}, ref


async def test_settings_never_contain_a_forbidden_key(
    client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """Every tenant's ``registry.settings`` (and every store's ``extra_env``)
    is checked against the registry schema's OWN forbidden-name pattern — the
    schema validation already enforces it, but this test names the failure."""
    forbidden = forbidden_setting_pattern(schemas)
    tenants = (await client.get("/v1/tenants")).json()["tenants"]
    for t in tenants:
        row = t["registry"]
        assert row is not None
        bad = [k for k in row["settings"] if forbidden.search(k)]
        assert bad == [], f"{row['name']}: secret-class keys in settings: {bad}"
        for store in ("qdrant", "elasticsearch"):
            bad = [k for k in row["stores"][store]["extra_env"] if forbidden.search(k)]
            assert bad == [], f"{row['name']}: secret-class keys in {store}.extra_env: {bad}"


async def test_keys_are_fingerprints_only(client: httpx.AsyncClient) -> None:
    tenants = (await client.get("/v1/tenants")).json()["tenants"]
    for t in tenants:
        for k in t["registry"]["keys"]:
            assert k["fingerprint"].startswith("sha256:") and len(k["fingerprint"]) == 23
            assert "value" not in k and "key" not in k


async def test_viewer_gets_summary_only(
    viewer_client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    resp = await viewer_client.get(f"/v1/tenants/{some_tenant}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate(body, "tenant_response", schemas)
    assert body["registry"] is None, "a viewer must not receive the registry row"
    listing = await viewer_client.get("/v1/tenants")
    assert listing.status_code == 200
    assert all(t["registry"] is None for t in listing.json()["tenants"])


async def test_unknown_tenant_is_404(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await client.get("/v1/tenants/zz-conformance-nope")
    assert_error(resp, 404, "not_found", schemas)


async def test_invalid_name_is_422(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    """Outside ``^[a-z][a-z0-9-]{0,31}$`` is a validation error, not a lookup
    miss — the name never reaches the registry."""
    resp = await client.get("/v1/tenants/Not_A_Valid_Name")
    assert_error(resp, 422, "validation", schemas)


async def test_env_conforms_and_redacts(
    client: httpx.AsyncClient, schemas: dict[str, dict], some_tenant: str
) -> None:
    resp = await client.get(f"/v1/tenants/{some_tenant}/env")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate(body, "env_response", schemas)
    assert find_secret_values(body) == []
    for row in body["keys"]:
        if row["class"] != "public":
            assert row["value_redacted"] == "<redacted>", row


async def test_env_for_a_viewer_is_public_rows_only(
    viewer_client: httpx.AsyncClient, some_tenant: str
) -> None:
    resp = await viewer_client.get(f"/v1/tenants/{some_tenant}/env")
    assert resp.status_code == 200, resp.text
    assert all(row["class"] == "public" for row in resp.json()["keys"])
