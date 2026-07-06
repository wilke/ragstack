"""GET /v1/chunks — fetch chunks by id for context expansion.

Seeds the in-memory vector store (which the fixture's one-entry registry resolves
through) with a 2-chunk document carrying neighbour links, then exercises the
endpoint: order preservation, unknown-id omission, empty ids, and the
unknown-collection 404.
"""
import pytest

from ragstack.api.main import app
from ragstack.models import Chunk

pytestmark = pytest.mark.asyncio


async def _seed() -> None:
    await app.state.vector_store.upsert(
        [
            Chunk(
                id="ck0",
                doc_id="D",
                content="alpha chunk zero",
                metadata={"tenant_id": "public", "chunk_index": 0, "next_chunk_id": "ck1"},
            ),
            Chunk(
                id="ck1",
                doc_id="D",
                content="beta chunk one",
                metadata={"tenant_id": "public", "chunk_index": 1, "prev_chunk_id": "ck0"},
            ),
        ]
    )


async def test_fetch_chunks_preserves_order_and_metadata(client) -> None:
    await _seed()
    resp = await client.get("/v1/chunks", params={"ids": "ck1,ck0"})
    assert resp.status_code == 200, resp.text
    chunks = resp.json()["chunks"]
    assert [c["chunk_id"] for c in chunks] == ["ck1", "ck0"]  # request order kept
    assert chunks[0]["content"] == "beta chunk one"
    assert chunks[0]["doc_id"] == "D"
    assert chunks[0]["metadata"]["chunk_index"] == 1
    assert chunks[0]["metadata"]["prev_chunk_id"] == "ck0"


async def test_no_ids_returns_empty(client) -> None:
    resp = await client.get("/v1/chunks")
    assert resp.status_code == 200
    assert resp.json()["chunks"] == []


async def test_unknown_id_is_omitted(client) -> None:
    await _seed()
    resp = await client.get("/v1/chunks", params={"ids": "ck0,__missing__"})
    assert resp.status_code == 200
    assert [c["chunk_id"] for c in resp.json()["chunks"]] == ["ck0"]


async def test_unknown_collection_is_404(client) -> None:
    resp = await client.get("/v1/chunks", params={"ids": "ck0", "collection": "__nope__"})
    assert resp.status_code == 404, resp.text
