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


def _prod(monkeypatch):
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    monkeypatch.setattr(deps.settings, "ingest_root", "/data")


def test_partial_tenant_map_rejected_in_production(monkeypatch):
    # A configured key with no tenant mapping would collapse into the shared
    # "default" tenant and break isolation — production must fail closed.
    _prod(monkeypatch)
    monkeypatch.setattr(deps.settings, "api_keys", ["ka", "kb"])
    monkeypatch.setattr(deps.settings, "api_key_tenants", {"ka": "alice"})  # kb unmapped
    with pytest.raises(RuntimeError, match="tenant mapping"):
        deps._validate_production_settings()


def test_full_tenant_map_accepted_in_production(monkeypatch):
    _prod(monkeypatch)
    monkeypatch.setattr(deps.settings, "api_keys", ["ka", "kb"])
    monkeypatch.setattr(deps.settings, "api_key_tenants", {"ka": "alice", "kb": "bob"})
    deps._validate_production_settings()  # no raise


def test_no_tenant_map_is_single_tenant_mode(monkeypatch):
    # No mapping at all is the legitimate single-(default-)tenant mode, not a
    # partial-map misconfig — must not raise.
    _prod(monkeypatch)
    monkeypatch.setattr(deps.settings, "api_keys", ["ka", "kb"])
    monkeypatch.setattr(deps.settings, "api_key_tenants", {})
    deps._validate_production_settings()  # no raise


def test_qdrant_backend_under_durable_returns_qdrant(monkeypatch):
    from ragstack.stores.qdrant import QdrantVectorStore

    monkeypatch.setattr(deps.settings, "vector_backend", "qdrant")
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    # Constructing the client is offline (no connection until first call).
    assert isinstance(deps._build_vector_store(), QdrantVectorStore)


def test_graph_backend_memory_returns_inmemory(monkeypatch):
    from ragstack.stores import InMemoryGraphStore

    monkeypatch.setattr(deps.settings, "graph_backend", "memory")
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    assert isinstance(deps._build_graph_store(), InMemoryGraphStore)


def test_graph_backend_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(deps.settings, "graph_backend", "disabled")
    assert deps._build_graph_store() is None


def test_graph_backend_neo4j_returns_neo4j_store(monkeypatch):
    # Constructing the driver opens no socket (offline-safe), so this works
    # without a live Neo4j as long as the neo4j driver is importable.
    pytest.importorskip("neo4j")
    from ragstack.stores import Neo4jGraphStore

    monkeypatch.setattr(deps.settings, "graph_backend", "neo4j")
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    store = deps._build_graph_store()
    assert isinstance(store, Neo4jGraphStore)


def test_graph_memory_warns_under_durable(monkeypatch, caplog):
    import logging

    from ragstack.stores import InMemoryGraphStore

    monkeypatch.setattr(deps.settings, "graph_backend", "memory")
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    with caplog.at_level(logging.WARNING):
        store = deps._build_graph_store()
    assert isinstance(store, InMemoryGraphStore)
    assert any("knowledge graph is in-memory" in r.message for r in caplog.records)


class _FakeLLM:
    """Stand-in for OpenAILLM — _build_kg_extractor only checks for not-None."""


def test_kg_extractor_none_when_disabled(monkeypatch):
    monkeypatch.setattr(deps.settings, "kg_extraction_enabled", False)
    assert deps._build_kg_extractor(_FakeLLM()) is None


def test_kg_extractor_none_without_llm(monkeypatch):
    # Enabled but no LLM configured → no extractor (extraction needs an LLM).
    monkeypatch.setattr(deps.settings, "kg_extraction_enabled", True)
    assert deps._build_kg_extractor(None) is None


def test_kg_extractor_built_when_enabled_with_llm(monkeypatch):
    from ragstack.graph.extractor import LLMKGExtractor

    monkeypatch.setattr(deps.settings, "kg_extraction_enabled", True)
    monkeypatch.setattr(deps.settings, "kg_extraction_max_chunks", 3)
    monkeypatch.setattr(deps.settings, "kg_extraction_max_triples_per_chunk", 7)
    extractor = deps._build_kg_extractor(_FakeLLM())
    assert isinstance(extractor, LLMKGExtractor)
    assert extractor._max_chunks == 3
    assert extractor._max_triples_per_chunk == 7


def test_text_index_is_inmemory_but_warns_under_durable(monkeypatch, caplog):
    import logging

    from ragstack.stores import InMemoryTextIndex

    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    with caplog.at_level(logging.WARNING):
        index = deps._build_text_index()
    assert isinstance(index, InMemoryTextIndex)
    assert any("text index is in-memory" in r.message for r in caplog.records)
