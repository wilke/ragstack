"""Phase 3 Step 3: POST /v1/collections (build-time model selection) + DELETE.

Create binds a *registered* embedding model + chunk strategy into a new
content-addressed collection (admin only); the physical name is derived from
(model, dim, chunk) so the same spec is idempotent (409) and a different chunker
mints a distinct collection. Delete drops the registry binding.
"""
import json

import pytest

from ragstack.api import security
from ragstack.api.security import ROLE_ADMIN
from ragstack.config import settings

pytestmark = pytest.mark.asyncio

EMB = {
    "id": "emb-sfr", "task": "embedding", "provider": "vllm",
    "base_urls": ["http://localhost:9100"], "model": "test/sfr", "dim": 8,
}
LLM = {
    "id": "an-llm", "task": "llm", "provider": "vllm",
    "base_urls": ["http://localhost:9101"], "model": "test/llm",
}
CHUNK = {"method": "fixed_token", "size": 256, "overlap": 32}


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    # keyless dev → admin, so both the model-registry and create surfaces are reachable
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)


async def _register(client, entry):
    r = await client.post("/v1/admin/models/registry", json=entry)
    assert r.status_code == 201, r.text


async def test_create_then_listed(client):
    await _register(client, EMB)
    r = await client.post(
        "/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK, "label": "SFR 256"}
    )
    assert r.status_code == 201, r.text
    info = r.json()
    assert info["model"] == "test/sfr" and info["dim"] == 8
    assert info["chunk_method"] == "fixed_token" and info["chunk_size"] == 256
    assert info["default"] is False and info["label"] == "SFR 256"
    cid = info["id"]
    listed = (await client.get("/v1/collections")).json()["collections"]
    assert cid in {c["id"] for c in listed}


async def test_create_unknown_model_404(client):
    r = await client.post("/v1/collections", json={"embedding": "ghost", "chunk": CHUNK})
    assert r.status_code == 404


async def test_create_wrong_task_400(client):
    await _register(client, LLM)  # an llm, not an embedding model
    r = await client.post("/v1/collections", json={"embedding": "an-llm", "chunk": CHUNK})
    assert r.status_code == 400


async def test_create_bad_chunk_method_400(client):
    await _register(client, EMB)
    r = await client.post(
        "/v1/collections", json={"embedding": "emb-sfr", "chunk": {"method": "nope"}}
    )
    assert r.status_code == 400


async def test_create_is_content_addressed_and_idempotent(client):
    await _register(client, EMB)
    a = await client.post("/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK})
    assert a.status_code == 201
    # same (model, dim, chunk) → same derived id → 409
    dup = await client.post("/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK})
    assert dup.status_code == 409
    # a different chunker → a distinct collection → 201
    other = await client.post(
        "/v1/collections",
        json={"embedding": "emb-sfr", "chunk": {"method": "fixed_token", "size": 512, "overlap": 64}},
    )
    assert other.status_code == 201
    assert other.json()["id"] != a.json()["id"]


async def test_create_requires_admin(client, monkeypatch):
    monkeypatch.setattr(security.settings, "default_role", "researcher")
    r = await client.post("/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK})
    assert r.status_code == 403


async def test_create_persists_to_collections_file(client, monkeypatch, tmp_path):
    f = tmp_path / "acme.collections.json"
    monkeypatch.setattr(settings, "collections_file", str(f))
    await _register(client, EMB)
    cid = (
        await client.post("/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK})
    ).json()["id"]
    data = json.loads(f.read_text())
    match = [d for d in data if d["id"] == cid]
    assert match and match[0]["embedding_model"] == "test/sfr"
    assert match[0]["chunk_overlap"] == 32 and match[0]["embedding_model_dim"] == 8


async def test_delete_drops_binding(client):
    await _register(client, EMB)
    cid = (
        await client.post("/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK})
    ).json()["id"]
    d = await client.delete(f"/v1/collections/{cid}")
    assert d.status_code == 204
    listed = {c["id"] for c in (await client.get("/v1/collections")).json()["collections"]}
    assert cid not in listed


async def test_delete_default_is_409(client):
    r = await client.delete("/v1/collections/default")
    assert r.status_code == 409


async def test_delete_unknown_is_404(client):
    r = await client.delete("/v1/collections/nope")
    assert r.status_code == 404
