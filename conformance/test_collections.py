"""Conformance tests for the multi-collection query surface (black-box over HTTP).

GET /v1/collections and the `collection` field on /v1/query + /v1/retrieve are
served by BOTH implementations (the Go scaffold returns a schema-valid default
entry / accepts the field), so these run on every impl. The unknown-collection
404 is Python-first behavior (the Go stub doesn't route by collection yet).
"""
from __future__ import annotations

import os

import httpx
import jsonschema
import pytest

pytestmark = pytest.mark.asyncio


def _headers() -> dict[str, str]:
    k = os.environ.get("RAGSTACK_API_KEY") or None
    return {"X-API-Key": k} if k else {}


def _validate(data, schemas: dict[str, dict]) -> None:
    store = {s.get("$id", n): s for n, s in schemas.items()}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=data, schema=schemas["collections_response"], resolver=resolver)


async def test_collections_schema(client: httpx.AsyncClient, schemas: dict[str, dict]) -> None:
    resp = await client.get("/v1/collections", headers=_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schemas)
    ids = {c["id"] for c in body["collections"]}
    assert body["default"] in ids, "default id must be one of the listed collections"


async def _default_collection(client: httpx.AsyncClient) -> str:
    body = (await client.get("/v1/collections", headers=_headers())).json()
    return body["default"]


async def test_query_accepts_collection(client: httpx.AsyncClient) -> None:
    """The `collection` field is part of the contract — both impls must accept it.

    /query runs generation, so a down/slow LLM makes this slow; the field's
    *acceptance* is independent of that and is also covered fast by
    test_retrieve_accepts_collection. Skip (don't fail) on a generation timeout."""
    cid = await _default_collection(client)
    try:
        resp = await client.post(
            "/v1/query", json={"query": "x", "collection": cid}, headers=_headers(), timeout=25.0
        )
    except httpx.ReadTimeout:
        pytest.skip("collection field accepted; generation slow (LLM unavailable) — see retrieve test")
        return
    assert resp.status_code == 200, resp.text
    assert "answer" in resp.json()


async def test_retrieve_accepts_collection(client: httpx.AsyncClient) -> None:
    """The `collection` field must be accepted on /retrieve (no generation).

    Bounded so a slow embedding backend doesn't hang the run; skip on timeout."""
    cid = await _default_collection(client)
    try:
        resp = await client.post(
            "/v1/retrieve", json={"query": "x", "collection": cid}, headers=_headers(), timeout=25.0
        )
    except httpx.ReadTimeout:
        pytest.skip("collection field accepted; retrieval backend slow")
        return
    assert resp.status_code == 200, resp.text
    assert "sources" in resp.json()


async def test_unknown_collection_is_404(client: httpx.AsyncClient, impl: str) -> None:
    if impl != "python":
        pytest.skip("unknown-collection 404 is python-first behavior in phase 1")
    resp = await client.post(
        "/v1/query", json={"query": "x", "collection": "__no_such_collection__"}, headers=_headers()
    )
    assert resp.status_code == 404, resp.text


# --- POST/DELETE /v1/collections (build-time model selection, Phase 3) ------- #


def _validate_info(info: dict, schemas: dict[str, dict]) -> None:
    """Validate one CollectionInfo by wrapping it in the CollectionsResponse shape
    (there is no standalone CollectionInfo schema; it lives as the array item)."""
    _validate({"collections": [info], "default": info["id"]}, schemas)


async def _register_embedding(client: httpx.AsyncClient, model_id: str) -> int:
    """Register an embedding model via the admin surface; return the status so the
    caller can skip when not admin. localhost is in the default SSRF allowlist."""
    resp = await client.post(
        "/v1/admin/models/registry",
        json={
            "id": model_id, "task": "embedding", "provider": "vllm",
            "base_urls": ["http://localhost:9100"], "model": "conformance/emb", "dim": 8,
        },
        headers=_headers(),
    )
    return resp.status_code


async def test_create_collection_python(
    client: httpx.AsyncClient, impl: str, schemas: dict[str, dict]
) -> None:
    """Full Python create→list→delete round-trip (real registry + persistence).

    Skips on non-python (the Go scaffold neither resolves the model ref nor
    persists) and when the caller lacks admin (create/registry are admin-gated)."""
    if impl != "python":
        pytest.skip("create/list/delete round-trip is python-authoritative in phase 3")
    mid = "conf-emb-create"
    if await _register_embedding(client, mid) in (401, 403):
        pytest.skip("caller lacks admin access to register a model / create a collection")

    created = await client.post(
        "/v1/collections",
        json={"embedding": mid, "chunk": {"method": "fixed", "size": 200, "overlap": 20}},
        headers=_headers(),
    )
    assert created.status_code == 201, created.text
    info = created.json()
    _validate_info(info, schemas)
    assert info["model"] == "conformance/emb" and info["dim"] == 8
    cid = info["id"]

    listed = (await client.get("/v1/collections", headers=_headers())).json()
    assert cid in {c["id"] for c in listed["collections"]}

    deleted = await client.delete(f"/v1/collections/{cid}?purge=true", headers=_headers())
    assert deleted.status_code == 200, deleted.text
    after = (await client.get("/v1/collections", headers=_headers())).json()
    assert cid not in {c["id"] for c in after["collections"]}


async def test_create_unknown_model_is_404(client: httpx.AsyncClient, impl: str) -> None:
    if impl != "python":
        pytest.skip("unknown-model 404 on create is python-first behavior in phase 3")
    resp = await client.post(
        "/v1/collections",
        json={"embedding": "__no_such_model__", "chunk": {"method": "fixed"}},
        headers=_headers(),
    )
    if resp.status_code in (401, 403):
        pytest.skip("caller lacks admin access to the create surface")
    assert resp.status_code == 404, resp.text


async def test_create_collection_go_scaffold(
    client: httpx.AsyncClient, impl: str, schemas: dict[str, dict]
) -> None:
    """The Go scaffold serves POST /v1/collections with a schema-valid 201 echo, so
    the endpoint/contract exists in both impls."""
    if impl == "python":
        pytest.skip("covered by the full python round-trip test")
    resp = await client.post(
        "/v1/collections",
        json={"embedding": "any-ref", "chunk": {"method": "fixed"}, "label": "L"},
        headers=_headers(),
    )
    if resp.status_code in (401, 403):
        pytest.skip("caller lacks admin access to the create surface")
    assert resp.status_code == 201, resp.text
    _validate_info(resp.json(), schemas)


async def test_create_malformed_body_is_422(client: httpx.AsyncClient) -> None:
    """Both impls validate the request body. `embedding` and `chunk` are now
    OPTIONAL (omitted → the server-default build spec, ADR-0003), so a body
    without them is valid — but a mistyped field is still rejected."""
    resp = await client.post(
        "/v1/collections", json={"chunk": {"method": 123}}, headers=_headers()
    )
    if resp.status_code in (401, 403):
        pytest.skip("caller lacks access to the create surface")
    assert resp.status_code == 422, resp.text


async def test_create_without_build_spec_python(
    client: httpx.AsyncClient, impl: str, schemas: dict[str, dict]
) -> None:
    """POST /v1/collections with only {id, label}: creation is open to any
    authenticated principal, and the server resolves its default build spec into
    the collection (ADR-0003 decision 3). A black-box test cannot know WHICH
    defaults the server runs with — only that concrete values were resolved in
    (non-empty model, positive dim, a chunk method) rather than echoed back null.

    Python-gated: the Go scaffold echoes the request without resolving defaults."""
    if impl != "python":
        pytest.skip("server-default build-spec resolution is python-authoritative in phase 3")
    cid = "conf-default-spec"
    resp = await client.post(
        "/v1/collections", json={"id": cid, "label": "Server default spec"}, headers=_headers()
    )
    if resp.status_code == 401:
        pytest.skip("caller is unauthenticated")
    if resp.status_code == 409:
        # Leftover from an earlier run whose caller lacked admin for the cleanup
        # delete below; the id already existing still means creation worked once.
        pytest.skip("collection already exists from a previous run (cleanup needs admin)")
    assert resp.status_code == 201, resp.text
    info = resp.json()
    _validate_info(info, schemas)
    assert info["id"] == cid
    assert info["model"], "server default embedding model must be resolved, not empty"
    assert info["dim"] > 0
    assert info["chunk_method"], "server default chunk method must be resolved, not null"
    # Cleanup is admin-only; tolerate a non-admin creator (the 409-skip above
    # keeps re-runs honest in that case).
    deleted = await client.delete(f"/v1/collections/{cid}?purge=true", headers=_headers())
    assert deleted.status_code in (200, 401, 403), deleted.text


async def test_admin_evict_dry_run_schema(
    client: httpx.AsyncClient, schemas: dict[str, dict], impl: str
) -> None:
    """POST /v1/admin/collections/evict?dry_run=true (#359) reports the plan
    without acting; admin-gated, so a keyless / non-admin caller gets 401/403
    -> skip. Python-only (the Go scaffold has no admin evict route). The body
    must match contracts/schemas/eviction_response.json."""
    if impl != "python":
        pytest.skip("admin eviction is Python-only")
    resp = await client.post(
        "/v1/admin/collections/evict", params={"need": 1, "dry_run": "true"}, headers=_headers()
    )
    if resp.status_code in (401, 403):
        pytest.skip("caller lacks admin access to eviction")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    jsonschema.validate(instance=body, schema=schemas["eviction_response"])
    assert body["dry_run"] is True and body["evicted"] == 0
    assert body["shortfall"]["needed"] == 1
    assert body["shortfall"]["found"] == len(body["victims"])
