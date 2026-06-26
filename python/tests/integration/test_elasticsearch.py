"""Live integration test for ElasticsearchTextIndex.

Skipped unless the elasticsearch client is installed and an ES reachable at
TEST_ES_URL. Uses a dedicated test index it deletes afterward, so it never
touches the real ``ragstack`` index.
"""
import os

import pytest

pytest.importorskip("elasticsearch")

from ragstack.models import Chunk  # noqa: E402
from ragstack.stores.elasticsearch import ElasticsearchTextIndex  # noqa: E402

URL = os.environ.get("TEST_ES_URL", "http://localhost:9200")
# Per-worker index name so pytest-xdist workers don't race on a shared index
# (each would otherwise delete the others' data in the finally block).
INDEX = f"ragstack_estest_{os.environ.get('PYTEST_XDIST_WORKER', 'gw0')}"


async def _reachable() -> bool:
    try:
        from elasticsearch import AsyncElasticsearch

        es = AsyncElasticsearch(hosts=URL)
        ok = await es.ping()
        await es.close()
        return bool(ok)
    except Exception:
        return False


def _chunk(cid: str, doc_id: str, tenant: str, content: str, **meta: object) -> Chunk:
    return Chunk(
        id=cid, doc_id=doc_id, content=content, metadata={"tenant_id": tenant, **meta}
    )


@pytest.mark.asyncio
async def test_es_bm25_tenant_scoped_search_and_delete():
    if not await _reachable():
        pytest.skip("elasticsearch not reachable at TEST_ES_URL")

    idx = ElasticsearchTextIndex(URL, INDEX)
    await idx.ensure_index()
    try:
        await idx.index(
            [
                _chunk("1", "dA", "alice", "vector databases store dense embeddings",
                       source="guide.pdf", page=3),
                _chunk("2", "dB", "bob", "vector databases store dense embeddings"),
                _chunk("3", "dP", "public", "a public guide to vector search"),
            ]
        )

        # BM25 match, scoped to alice + public (not bob).
        res = await idx.search(
            "vector databases", top_k=10, filters={"tenant_id": ["alice", "public"]}
        )
        tenants = {r.chunk.metadata["tenant_id"] for r in res}
        assert "alice" in tenants
        assert "bob" not in tenants
        assert all(r.retrieval_method == "bm25" for r in res)

        # Full metadata round-trips (not just tenant_id), so RRF fusion won't
        # clobber metadata-rich vector chunks with BM25 chunks.
        alice_hit = next(r for r in res if r.chunk.metadata["tenant_id"] == "alice")
        assert alice_hit.chunk.metadata["source"] == "guide.pdf"
        assert str(alice_hit.chunk.metadata["page"]) == "3"

        # A non-tenant metadata filter narrows results (filter parity with the
        # vector store, which filters on chunk.metadata).
        only_alice = await idx.search(
            "vector databases",
            top_k=10,
            filters={"tenant_id": ["alice", "public"], "source": "guide.pdf"},
        )
        assert {r.chunk.doc_id for r in only_alice} == {"dA"}

        # Tenant-scoped delete removes only alice's doc.
        await idx.delete("dA", tenant_id="alice")
        res2 = await idx.search(
            "vector databases", top_k=10, filters={"tenant_id": ["alice", "public"]}
        )
        assert all(r.chunk.doc_id != "dA" for r in res2)
    finally:
        await idx._es.indices.delete(index=INDEX, ignore_unavailable=True)
        await idx.close()
