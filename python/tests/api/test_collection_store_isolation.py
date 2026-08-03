"""End-to-end: two named libraries built from the SAME embedding + chunk spec must
not see each other's content.

The bug this pins: ``collection_name()`` derived the physical Qdrant collection /
ES index from ``(model, dim, chunk)`` only, so every library created through
``POST /v1/collections`` with the same build spec mapped onto ONE physical store.
The registry ids differed, the data did not — an ingest into ``andy`` showed up in
``open-access`` and a delete would have hit both.

The doubles here are keyed by *physical name*: two entries that derive the same
name get the identical store object, exactly as they would against a real
Qdrant/ES. That is what makes this an isolation test rather than a tautology —
revert the fix and the ingest leaks.
"""
from __future__ import annotations

import pytest

from ragstack.api import deps, security
from ragstack.api.security import ROLE_ADMIN
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore
from ragstack.tenancy import DEFAULT_TENANT

pytestmark = pytest.mark.asyncio

EMB = {
    "id": "emb-sfr", "task": "embedding", "provider": "vllm",
    "base_urls": ["http://localhost:9100"], "model": "test/sfr", "dim": 4,
}
# `fixed` needs no HF tokenizer, so a targeted ingest runs fully offline.
CHUNK = {"method": "fixed", "size": 200, "overlap": 20}


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


@pytest.fixture
def bank(monkeypatch):
    """In-memory vector stores / text indices keyed by physical name."""
    vec: dict[str, InMemoryVectorStore] = {}
    txt: dict[str, InMemoryTextIndex] = {}

    def _vector_store(*, collection: str, **_kw):
        return vec.setdefault(collection, InMemoryVectorStore())

    monkeypatch.setattr("ragstack.stores.qdrant.QdrantVectorStore", _vector_store)
    monkeypatch.setattr(
        deps, "_build_text_index_for", lambda index: txt.setdefault(index, InMemoryTextIndex())
    )
    monkeypatch.setattr(deps, "_embedder_for_spec", lambda http, spec: _FakeEmbedder())
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)
    return vec, txt


async def _create(client, cid: str) -> None:
    r = await client.post(
        "/v1/collections", json={"embedding": "emb-sfr", "chunk": CHUNK, "id": cid}
    )
    assert r.status_code == 201, r.text


async def test_ingest_into_one_library_does_not_appear_in_the_other(
    client, bank, monkeypatch, tmp_path
):
    from ragstack.api.main import app

    vec, txt = bank
    monkeypatch.setattr(app.state, "kg_extractor", None, raising=False)

    assert (await client.post("/v1/admin/models/registry", json=EMB)).status_code == 201
    await _create(client, "andy")
    await _create(client, "open-access")
    a = app.state.collections.resolve("andy")
    b = app.state.collections.resolve("open-access")
    assert a.collection != b.collection
    # Two libraries, two physical stores — not one aliased by two ids.
    assert len(vec) == 2 and len(txt) == 2

    doc = tmp_path / "andys-paper.txt"
    doc.write_text("phage therapy against multidrug resistant klebsiella " * 20)
    monkeypatch.setattr(deps.settings, "ingest_root", str(tmp_path))

    r = await client.post("/v1/ingest", json={"source": str(doc), "collection": "andy"})
    assert r.status_code == 200, r.text

    job = (await client.get(f"/v1/ingest/{r.json()['job_id']}")).json()
    assert job["status"] == "completed" and job["items"]["completed"] == 1, job

    tenants = [DEFAULT_TENANT, "public"]
    assert await a.vector_store.count_tenants(tenants) > 0, "ingest wrote nothing"
    assert await b.vector_store.count_tenants(tenants) == 0, "content leaked across libraries"
    hits = await b.text_index.search("phage therapy klebsiella", top_k=5)
    assert hits == []
    assert await a.text_index.search("phage therapy klebsiella", top_k=5)


async def test_two_ids_with_the_same_physical_name_do_share(client, bank, monkeypatch):
    """Negative control for the double above: the bank aliases on name, so a test
    asserting isolation is only meaningful because same name ⇒ same object."""
    vec, _txt = bank
    s1 = vec.setdefault("same", InMemoryVectorStore())
    s2 = vec.setdefault("same", InMemoryVectorStore())
    assert s1 is s2
