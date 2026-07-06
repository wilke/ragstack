"""Admin model registry + hot-swappable assignments (Phase 1).

Covers CRUD, the SSRF allowlist, and that assigning llm/reranker actually swaps
the live app.state client — plus the guardrails (unknown model, wrong task,
build-time task, delete-while-assigned).
"""
import pytest

from ragstack.api import security
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN

pytestmark = pytest.mark.asyncio

LLM = {
    "id": "local-llm",
    "task": "llm",
    "provider": "vllm",
    "base_urls": ["http://localhost:9999"],
    "model": "test/model",
}
RR = {
    "id": "local-rr",
    "task": "reranker",
    "provider": "sidecar",
    "base_urls": ["http://localhost:9998"],
}


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    # keyless dev → admin, so the admin-gated registry surface is reachable
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)


async def test_list_starts_empty(client):
    body = (await client.get("/v1/admin/models/registry")).json()
    assert body == {"models": [], "assignments": {}}


async def test_register_and_list(client):
    resp = await client.post("/v1/admin/models/registry", json=LLM)
    assert resp.status_code == 201, resp.text
    body = (await client.get("/v1/admin/models/registry")).json()
    assert [m["id"] for m in body["models"]] == ["local-llm"]


async def test_register_duplicate_is_400(client):
    await client.post("/v1/admin/models/registry", json=LLM)
    dup = await client.post("/v1/admin/models/registry", json=LLM)
    assert dup.status_code == 400


async def test_register_ssrf_rejected(client):
    bad = {**LLM, "base_urls": ["http://evil.example.com"]}
    resp = await client.post("/v1/admin/models/registry", json=bad)
    assert resp.status_code == 400
    assert "allowlist" in resp.text


async def test_register_ssrf_prefix_lookalike_rejected(client):
    # A host that merely *starts with* an allowed prefix must not slip through —
    # host is matched on the parsed authority, not a raw string prefix.
    for host in ("http://localhost.evil.com", "http://127.0.0.1.evil.com:9005"):
        bad = {**LLM, "base_urls": [host]}
        resp = await client.post("/v1/admin/models/registry", json=bad)
        assert resp.status_code == 400, host
        assert "allowlist" in resp.text


async def test_register_allows_any_port_on_allowed_host(client):
    # An allowlist entry with no explicit port permits any port on that host.
    ok = {**LLM, "base_urls": ["http://localhost:12345"]}
    assert (await client.post("/v1/admin/models/registry", json=ok)).status_code == 201


async def test_embedding_requires_dim(client):
    emb = {"id": "e", "task": "embedding", "base_urls": ["http://localhost:1"], "model": "m"}
    resp = await client.post("/v1/admin/models/registry", json=emb)
    assert resp.status_code == 400
    assert "dim" in resp.text


async def test_update_replaces(client):
    await client.post("/v1/admin/models/registry", json=LLM)
    resp = await client.put(
        "/v1/admin/models/registry/local-llm", json={**LLM, "model": "test/other"}
    )
    assert resp.status_code == 200
    assert resp.json()["model"] == "test/other"


async def test_update_unknown_is_404(client):
    resp = await client.put("/v1/admin/models/registry/nope", json=LLM)
    assert resp.status_code == 404


async def test_delete(client):
    await client.post("/v1/admin/models/registry", json=LLM)
    assert (await client.delete("/v1/admin/models/registry/local-llm")).status_code == 204
    body = (await client.get("/v1/admin/models/registry")).json()
    assert body["models"] == []


async def test_assign_llm_hot_swaps(client):
    # No LLM by default → no generator, no LLM rewriters.
    assert app.state.generator is None
    await client.post("/v1/admin/models/registry", json=LLM)
    resp = await client.patch("/v1/admin/config/assignments", json={"llm": "local-llm"})
    assert resp.status_code == 200
    assert resp.json()["assignments"] == {"llm": "local-llm"}
    # Live swap: a generator now exists and the LLM-backed rewriters are wired.
    assert app.state.generator is not None
    assert "hyde" in app.state.rewriters and "multiquery" in app.state.rewriters


async def test_assign_reranker_hot_swaps(client):
    assert app.state.reranker is None
    await client.post("/v1/admin/models/registry", json=RR)
    resp = await client.patch("/v1/admin/config/assignments", json={"reranker": "local-rr"})
    assert resp.status_code == 200
    assert app.state.reranker is not None
    assert app.state.reranker.base_url.startswith("http://localhost:9998")


async def test_update_assigned_model_rebuilds_live_client(client):
    # Assign a reranker, then PUT a new base_url for it → the live client must
    # follow the update, not keep serving the old endpoint.
    await client.post("/v1/admin/models/registry", json=RR)
    await client.patch("/v1/admin/config/assignments", json={"reranker": "local-rr"})
    assert app.state.reranker.base_url.startswith("http://localhost:9998")
    resp = await client.put(
        "/v1/admin/models/registry/local-rr", json={**RR, "base_urls": ["http://localhost:9000"]}
    )
    assert resp.status_code == 200
    assert app.state.reranker.base_url.startswith("http://localhost:9000")


async def test_patch_all_or_nothing_on_invalid_task(client):
    # A multi-field patch where the second task is invalid must apply neither —
    # no half-swapped, unpersisted state.
    await client.post("/v1/admin/models/registry", json=LLM)
    assert app.state.generator is None
    resp = await client.patch(
        "/v1/admin/config/assignments", json={"llm": "local-llm", "reranker": "ghost"}
    )
    assert resp.status_code == 404
    # llm must NOT have been applied despite being valid and listed first.
    assert app.state.generator is None
    body = (await client.get("/v1/admin/models/registry")).json()
    assert body["assignments"] == {}


async def test_unassign_reverts(client):
    await client.post("/v1/admin/models/registry", json=LLM)
    await client.patch("/v1/admin/config/assignments", json={"llm": "local-llm"})
    assert app.state.generator is not None
    # null → revert to the settings default (no llm_endpoint in tests → None)
    resp = await client.patch("/v1/admin/config/assignments", json={"llm": None})
    assert resp.status_code == 200
    assert resp.json()["assignments"] == {}
    assert app.state.generator is None


async def test_delete_assigned_is_409(client):
    await client.post("/v1/admin/models/registry", json=LLM)
    await client.patch("/v1/admin/config/assignments", json={"llm": "local-llm"})
    resp = await client.delete("/v1/admin/models/registry/local-llm")
    assert resp.status_code == 409


async def test_assign_unknown_model_is_404(client):
    resp = await client.patch("/v1/admin/config/assignments", json={"llm": "ghost"})
    assert resp.status_code == 404


async def test_assign_wrong_task_is_400(client):
    await client.post("/v1/admin/models/registry", json=RR)
    resp = await client.patch("/v1/admin/config/assignments", json={"llm": "local-rr"})
    assert resp.status_code == 400


async def test_assign_build_time_task_is_422(client):
    # embedding isn't a hot-swappable field → extra="forbid" rejects it
    resp = await client.patch("/v1/admin/config/assignments", json={"embedding": "x"})
    assert resp.status_code == 422


async def test_registry_requires_admin(client, monkeypatch):
    from ragstack.api.security import ROLE_RESEARCHER

    monkeypatch.setattr(security.settings, "default_role", ROLE_RESEARCHER)
    assert (await client.get("/v1/admin/models/registry")).status_code == 403
