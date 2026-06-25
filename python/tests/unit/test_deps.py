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


def test_text_index_is_inmemory_but_warns_under_durable(monkeypatch, caplog):
    import logging

    from ragstack.stores import InMemoryTextIndex

    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    with caplog.at_level(logging.WARNING):
        index = deps._build_text_index()
    assert isinstance(index, InMemoryTextIndex)
    assert any("text index is in-memory" in r.message for r in caplog.records)
