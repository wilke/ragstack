"""#647, end to end: the in-process build REFUSES when no model window is known.

The unit half is ``tests/unit/test_chunker_factory_parity.py``; this file pins
what the caller sees — a 503 (a dependency failure the payload cannot fix) with
``Retry-After`` because the endpoint exists and may recover, a detail naming the
collection and the fix, and no internal endpoint URL.
"""
from __future__ import annotations

import pytest

from ragstack.ingestion import chunker_config
from ragstack.ingestion.tokenization import resolve_max_tokens as real_resolve_max_tokens
from tests.unit.test_chunker_factory_parity import _entry


@pytest.mark.asyncio
async def test_ingest_answers_503_naming_the_collection_when_no_window(client, monkeypatch, tmp_path):
    """End to end: the refusal reaches the caller as a 503 + Retry-After."""
    from ragstack.api.collections import CollectionRegistry
    from ragstack.api.main import app
    from ragstack.config import settings
    from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore

    monkeypatch.setattr(chunker_config, "resolve_max_tokens", real_resolve_max_tokens)
    monkeypatch.setattr(settings, "chunk_max_tokens", None)
    monkeypatch.setattr(settings, "chunk_token_counter", "estimate")
    monkeypatch.setattr(settings, "ingest_root", str(tmp_path))
    src = tmp_path / "doc.txt"
    src.write_text("a short document about marmalade")
    entry = _entry(
        "fixed", id="nowin", embedding_endpoints=["http://127.0.0.1:1"],
        vector_store=InMemoryVectorStore(), text_index=InMemoryTextIndex(),
    )
    registry = app.state.collections
    monkeypatch.setattr(
        app.state, "collections",
        CollectionRegistry([*registry.entries(), entry], default_id=registry.default_id),
    )
    r = await client.post("/v1/ingest", json={"source": str(src), "collection": "nowin"})
    assert r.status_code == 503, r.text
    assert r.headers.get("retry-after") == "5"
    detail = r.json()["detail"]
    assert "'nowin'" in detail and "CHUNK_MAX_TOKENS" in detail
    assert "127.0.0.1" not in detail
