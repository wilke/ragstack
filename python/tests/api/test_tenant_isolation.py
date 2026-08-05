"""End-to-end tenant isolation: cross-tenant reads/deletes are blocked, public
is shared."""
import asyncio

import pytest

from ragstack.api import security

KEYS = {"alice": "ka", "bob": "kb", "public": "kp"}


@pytest.fixture(autouse=True)
def _tenants(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings,
        "api_key_tenants",
        {"ka": "alice", "kb": "bob", "kp": "public"},
    )
    # This suite exercises per-chunk `tenant_id` isolation (defence in depth,
    # ADR-0003 decision 3) on the shared `default` collection, which the
    # conftest's ACL fixture seeds exactly as the startup backfill would
    # (public read). Document-level writes there are tenant-stamped and open to
    # any principal that can READ the collection — so plain (non-admin) callers
    # exercise the real gate; ownership of NON-default collections is covered in
    # test_collection_ownership.


def _h(tenant: str) -> dict:
    return {"X-API-Key": KEYS[tenant]}


async def _ingest(client, tenant: str, path: str):
    r = await client.post("/v1/ingest", json={"source": path}, headers=_h(tenant))
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    for _ in range(100):
        s = (await client.get(f"/v1/ingest/{job_id}", headers=_h(tenant))).json()
        if s["status"] in ("completed", "failed"):
            assert s["status"] == "completed", s
            return
        await asyncio.sleep(0.01)
    raise AssertionError("ingest did not complete")


async def _contents(client, tenant: str) -> set[str]:
    r = await client.post("/v1/retrieve", json={"query": "doc", "top_k": 50}, headers=_h(tenant))
    assert r.status_code == 200
    return {s["content"] for s in r.json()["sources"]}


@pytest.mark.asyncio
async def test_cross_tenant_read_isolation(client, tmp_path):
    (tmp_path / "alice.txt").write_text("ALICE secret document", encoding="utf-8")
    (tmp_path / "bob.txt").write_text("BOB secret document", encoding="utf-8")
    (tmp_path / "pub.txt").write_text("PUBLIC shared document", encoding="utf-8")

    await _ingest(client, "alice", str(tmp_path / "alice.txt"))
    await _ingest(client, "bob", str(tmp_path / "bob.txt"))
    await _ingest(client, "public", str(tmp_path / "pub.txt"))

    alice_sees = " ".join(await _contents(client, "alice"))
    bob_sees = " ".join(await _contents(client, "bob"))

    assert "ALICE" in alice_sees and "PUBLIC" in alice_sees  # own + public
    assert "BOB" not in alice_sees  # not another tenant's
    assert "BOB" in bob_sees and "PUBLIC" in bob_sees
    assert "ALICE" not in bob_sees


@pytest.mark.asyncio
async def test_unauthenticated_blocked(client):
    assert (await client.post("/v1/retrieve", json={"query": "x"})).status_code == 401


@pytest.mark.asyncio
async def test_delete_is_tenant_scoped(client, tmp_path):
    f = tmp_path / "shared.txt"
    f.write_text("SHARED name document", encoding="utf-8")
    # Both tenants ingest the same path → same doc_id, isolated by tenant.
    await _ingest(client, "alice", str(f))
    await _ingest(client, "bob", str(f))

    # doc_id is the loader's deterministic id for the resolved path.
    from ragstack.ingestion.loaders import deterministic_doc_id

    doc_id = deterministic_doc_id(str(f.resolve()))

    # Bob deletes that doc_id — must only remove Bob's copy.
    resp = await client.delete(f"/v1/documents/{doc_id}", headers=_h("bob"))
    assert resp.status_code == 204

    assert "SHARED" in " ".join(await _contents(client, "alice"))  # alice's survives
    assert "SHARED" not in " ".join(await _contents(client, "bob"))  # bob's gone

    # Delete must purge BOTH retrieval legs. "name" lexically hits the BM25/text
    # leg (the "doc" query above does not), so this catches a stale text-index
    # entry that would otherwise resurface a "deleted" document.
    async def _retrieve(tenant: str, query: str) -> str:
        r = await client.post(
            "/v1/retrieve", json={"query": query, "top_k": 50}, headers=_h(tenant)
        )
        return " ".join(s["content"] for s in r.json()["sources"])

    assert "SHARED" in await _retrieve("alice", "name")  # alice's still indexed
    assert "SHARED" not in await _retrieve("bob", "name")  # bob's gone from text leg too


@pytest.mark.asyncio
async def test_graph_leg_sources_are_scoped_and_stamped(client):
    """The graph leg of /v1/retrieve (use_graph defaults to True) must be tenant
    scoped at the source AND stamp each pseudo-chunk with its owning tenant, so
    graph-derived context reaching the LLM prompt is filterable like any chunk."""
    from ragstack.api.main import app
    from ragstack.models import Triple
    from ragstack.retrieval.retriever import HybridRetriever
    from ragstack.stores import InMemoryGraphStore

    graph = InMemoryGraphStore()
    await graph.add_triples([
        Triple(subject="Reactor", predicate="operated_by", object="ALICECORP",
               doc_id="da", tenant_id="alice"),
        Triple(subject="Reactor", predicate="operated_by", object="BOBCORP",
               doc_id="db", tenant_id="bob"),
        Triple(subject="Reactor", predicate="described_in", object="PUBLICDOC",
               doc_id="dp", tenant_id="public"),
    ])
    app.state.retriever = HybridRetriever(
        app.state.vector_store, app.state.text_index, app.state.embedder,
        graph_store=graph,
    )

    r = await client.post(
        "/v1/retrieve", json={"query": "Reactor", "top_k": 10}, headers=_h("alice")
    )
    assert r.status_code == 200, r.text
    sources = r.json()["sources"]

    contents = " ".join(s["content"] for s in sources)
    assert "ALICECORP" in contents and "PUBLICDOC" in contents  # own + public
    assert "BOBCORP" not in contents  # never another tenant's triples
    # Every graph source carries a tenant stamp a downstream filter can evaluate.
    assert sources and all(
        s["metadata"].get("tenant_id") in ("alice", "public") for s in sources
    )
