"""Live integration test for ElasticsearchTextIndex — **opt-in only** (#432).

This test writes to whatever **cluster** ``RAGSTACK_TEST_ES_URL`` names: it
creates indices, deletes them, consumes heap and shard budget, and shows up in
that cluster's logs. The per-worker index name below keeps it from colliding
with anything already stored there, but that is *index*-level tidiness while
the blast radius is *cluster*-level — do not read it as "this never touches
real data". Point it at a SCRATCH Elasticsearch you own.

It used to default to ``http://localhost:9200``, which on the dev host is the
production cluster holding the open-access index; unattended full-suite runs
therefore created and dropped indices there. So:

- The variable is ``RAGSTACK_TEST_ES_URL``, deliberately **not** the old
  ``TEST_ES_URL`` — a stale export of the old name from before this fix cannot
  silently re-arm a live run. Same rename, same reason, as ``pg_test_dsn``'s
  ``RAGSTACK_TEST_PG_DSN`` (``tests/conftest.py``).
- Unset means **skip**, never a fallback URL. The skip is placed before the
  ``elasticsearch`` import check so the reason names the opt-in rather than a
  missing package.
- ``_reachable()`` stays as a second gate, so an opted-in-but-down cluster
  skips too — with a different, honest message.
"""
import os

import pytest

URL = os.environ.get("RAGSTACK_TEST_ES_URL")
if not URL:
    pytest.skip(
        "set RAGSTACK_TEST_ES_URL to a SCRATCH Elasticsearch cluster to run the ES "
        "integration tests — they create and delete indices on whatever cluster the "
        "variable names. There is no default (it used to be the production cluster, "
        "#432), and the old TEST_ES_URL name is deliberately not honoured.",
        allow_module_level=True,
    )

pytest.importorskip("elasticsearch")

from ragstack.models import Chunk  # noqa: E402
from ragstack.stores.elasticsearch import ElasticsearchTextIndex  # noqa: E402

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
        pytest.skip(f"elasticsearch not reachable at RAGSTACK_TEST_ES_URL={URL}")

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


@pytest.mark.asyncio
async def test_es_empty_terms_matches_nothing_server_side():
    """Pin the *server-side* half of the fail-closed filter contract (#196).

    The unit tests assert we emit ``{"terms": {field: []}}``; only a live ES can
    say whether ES reads that as match-nothing or ignores it. If an upgrade ever
    turns empty-terms into a no-op, the fail-open regression is back and every
    unit test still passes — this is the test that would catch it.
    """
    if not await _reachable():
        pytest.skip(f"elasticsearch not reachable at RAGSTACK_TEST_ES_URL={URL}")

    idx = ElasticsearchTextIndex(URL, INDEX)
    await idx.ensure_index()
    try:
        await idx.index(
            [
                _chunk("1", "dA", "alice", "vector databases store dense embeddings",
                       source="guide.pdf"),
                _chunk("2", "dB", "alice", "more about vector databases",
                       source="other.pdf"),
            ]
        )

        # Key absent => unfiltered on that key. Both alice chunks come back.
        unfiltered = await idx.search(
            "vector databases", top_k=10, filters={"tenant_id": ["alice"]}
        )
        assert {r.chunk.doc_id for r in unfiltered} == {"dA", "dB"}

        # Same query, same tenant, plus an empty list for ``source`` => ES must
        # match nothing, not ignore the clause.
        constrained = await idx.search(
            "vector databases",
            top_k=10,
            filters={"tenant_id": ["alice"], "source": []},
        )
        assert constrained == []
    finally:
        await idx._es.indices.delete(index=INDEX, ignore_unavailable=True)
        await idx.close()
