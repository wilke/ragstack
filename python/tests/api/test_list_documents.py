"""API-level tests for GET /v1/documents (#86) — the router path: tenant
resolution, the DocumentInfo projection, and the X-Next-Cursor pagination header.

Chunks are injected straight into the in-memory text index the ``client`` fixture
wired onto ``app.state`` (mirroring what an ingest would produce), so the test
exercises the endpoint without standing up ES.
"""
from __future__ import annotations

import pytest

from ragstack.api.main import app
from ragstack.models import Chunk


def _chunk(doc_id: str, idx: int, tenant: str = "public") -> Chunk:
    return Chunk(
        id=f"{doc_id}:{idx}",
        doc_id=doc_id,
        content=f"c{idx}",
        metadata={
            "tenant_id": tenant,
            "chunk_index": idx,
            "source_path": f"/corpus/{doc_id}.pdf",
            "title": f"Title {doc_id}",
        },
    )


@pytest.mark.asyncio
async def test_list_documents_projects_and_counts(client) -> None:
    await app.state.text_index.index([_chunk("d1", 0), _chunk("d1", 1), _chunk("d2", 0)])
    resp = await client.get("/v1/documents")
    assert resp.status_code == 200
    body = {d["doc_id"]: d for d in resp.json()}
    assert set(body) == {"d1", "d2"}
    assert body["d1"]["source"] == "/corpus/d1.pdf"
    assert body["d1"]["metadata"]["chunk_count"] == 2
    assert body["d1"]["metadata"]["title"] == "Title d1"
    # chunk-level keys don't leak into the document record
    assert "chunk_index" not in body["d1"]["metadata"]


@pytest.mark.asyncio
async def test_list_documents_pagination_header(client) -> None:
    await app.state.text_index.index([_chunk(f"d{i}", 0) for i in range(3)])
    first = await client.get("/v1/documents", params={"limit": 2})
    assert first.status_code == 200
    assert len(first.json()) == 2
    cursor = first.headers.get("X-Next-Cursor")
    assert cursor  # more remain

    second = await client.get("/v1/documents", params={"limit": 2, "cursor": cursor})
    assert second.status_code == 200
    assert len(second.json()) == 1
    assert "X-Next-Cursor" not in second.headers  # last page


@pytest.mark.asyncio
async def test_list_documents_malformed_cursor_is_400(client) -> None:
    resp = await client.get("/v1/documents", params={"cursor": "!!!not-base64!!!"})
    assert resp.status_code == 400
