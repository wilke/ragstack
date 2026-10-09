"""Conformance: ``GET /v1/artifacts`` — the prepared artifacts a ``tenant
create`` picks from.

A viewer read (the matrix row is ``viewer, session: true``). Beyond the
schema: newest ``prepared_at`` first, no host path in the answer, and the
``tenants`` list is the registry's own ``artifact_id`` relation — a tenant the
operator view says was built from an artifact is listed under it. The same
read lists the prepared API server images (``server_images``, PR-F).
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


async def test_a_viewer_sees_the_prepared_server_images(
    client: httpx.AsyncClient, viewer_client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """``server_images`` (PR-F) is part of the same viewer read: every image
    the registry's ``server_images{}`` holds, sorted by name, with the tenants
    whose ``server_image`` names it — and no store path. The list is always
    present (``[]`` before the first ``fleet image prepare``)."""
    resp = await viewer_client.get("/v1/artifacts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate(body, "artifacts_response", schemas)
    images = body["server_images"]
    assert isinstance(images, list), body
    names = [i["name"] for i in images]
    assert names == sorted(names), f"not sorted by name: {names}"
    assert "/images/server" not in resp.text, "the image store path reached a viewer read"
    if not images:
        pytest.skip("no server image is prepared on this host")

    # The tenants list is the registry's own server_image relation.
    users = {i["name"]: set(i["tenants"]) for i in images}
    tenants = await client.get("/v1/tenants")
    assert tenants.status_code == 200, tenants.text
    for row in tenants.json()["tenants"]:
        reg = row.get("registry") or {}
        image = (reg.get("server_image") or {}).get("name")
        if image in users:
            assert reg["name"] in users[image], (
                f"{reg['name']} runs {image!r} and is not in its tenants: {sorted(users[image])}"
            )
        mode = row["summary"].get("api_mode")
        assert mode == ("image" if image else "worktree"), (reg.get("name"), mode, image)


async def test_artifacts_need_a_credential(anon_client: httpx.AsyncClient) -> None:
    resp = await anon_client.get("/v1/artifacts")
    assert resp.status_code == 401, resp.text
