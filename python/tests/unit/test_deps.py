"""Unit tests for backend wiring — the require_durable_backends gate."""
import pytest

from ragstack.api import deps
from ragstack.stores import InMemoryVectorStore


def test_memory_backend_allowed_in_dev(monkeypatch):
    monkeypatch.setattr(deps.settings, "vector_backend", "memory")
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    assert isinstance(deps._build_vector_store(), InMemoryVectorStore)


def test_require_durable_rejects_memory_backend(monkeypatch):
    monkeypatch.setattr(deps.settings, "vector_backend", "memory")
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    with pytest.raises(RuntimeError):
        deps._build_vector_store()


def test_qdrant_backend_under_durable_returns_qdrant(monkeypatch):
    from ragstack.stores.qdrant import QdrantVectorStore

    monkeypatch.setattr(deps.settings, "vector_backend", "qdrant")
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    # Constructing the client is offline (no connection until first call).
    assert isinstance(deps._build_vector_store(), QdrantVectorStore)


def test_text_index_is_inmemory_but_warns_under_durable(monkeypatch, caplog):
    import logging

    from ragstack.stores import InMemoryTextIndex

    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    with caplog.at_level(logging.WARNING):
        index = deps._build_text_index()
    assert isinstance(index, InMemoryTextIndex)
    assert any("text index is in-memory" in r.message for r in caplog.records)
