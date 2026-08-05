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


def _prod(monkeypatch, tmp_path):
    # ingest_root must resolve to an existing directory (see _validate_ingest_root),
    # so production fixtures point it at a real temp dir, not a literal path.
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    monkeypatch.setattr(deps.settings, "ingest_root", str(tmp_path))
    # The ACL database must be durable in production too (#243); these fixtures
    # exercise the tenant-map rule, so give them a durable store to get past the
    # ACL-durability check.
    monkeypatch.setattr(deps.settings, "user_store_backend", "sqlite")


def test_partial_tenant_map_rejected_in_production(monkeypatch, tmp_path):
    # A configured key with no tenant mapping would collapse into the shared
    # "default" tenant and break isolation — production must fail closed.
    _prod(monkeypatch, tmp_path)
    monkeypatch.setattr(deps.settings, "api_keys", ["ka", "kb"])
    monkeypatch.setattr(deps.settings, "api_key_tenants", {"ka": "alice"})  # kb unmapped
    with pytest.raises(RuntimeError, match="tenant mapping"):
        deps._validate_production_settings()


def test_full_tenant_map_accepted_in_production(monkeypatch, tmp_path):
    _prod(monkeypatch, tmp_path)
    monkeypatch.setattr(deps.settings, "api_keys", ["ka", "kb"])
    monkeypatch.setattr(deps.settings, "api_key_tenants", {"ka": "alice", "kb": "bob"})
    deps._validate_production_settings()  # no raise


def test_no_tenant_map_is_single_tenant_mode(monkeypatch, tmp_path):
    # No mapping at all is the legitimate single-(default-)tenant mode, not a
    # partial-map misconfig — must not raise.
    _prod(monkeypatch, tmp_path)
    monkeypatch.setattr(deps.settings, "api_keys", ["ka", "kb"])
    monkeypatch.setattr(deps.settings, "api_key_tenants", {})
    deps._validate_production_settings()  # no raise


def test_qdrant_backend_under_durable_returns_qdrant(monkeypatch):
    from ragstack.stores.qdrant import QdrantVectorStore

    monkeypatch.setattr(deps.settings, "vector_backend", "qdrant")
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    # Constructing the client is offline (no connection until first call).
    assert isinstance(deps._build_vector_store(), QdrantVectorStore)


def test_qdrant_collection_derived_by_default(monkeypatch):
    # Override empty (default): the collection is derived from
    # (qdrant_collection, embedding_model, embedding_model_dim) via collection_name().
    from ragstack.stores.qdrant import collection_name

    monkeypatch.setattr(deps.settings, "vector_backend", "qdrant")
    monkeypatch.setattr(deps.settings, "qdrant_collection_explicit", "")
    monkeypatch.setattr(deps.settings, "qdrant_collection", "ragstack")
    monkeypatch.setattr(
        deps.settings, "embedding_model", "Salesforce/SFR-Embedding-Mistral"
    )
    monkeypatch.setattr(deps.settings, "embedding_model_dim", 4096)

    store = deps._build_vector_store()
    expected = collection_name("ragstack", "Salesforce/SFR-Embedding-Mistral", 4096)
    assert store._collection == expected


def test_qdrant_collection_explicit_override(monkeypatch):
    # Override set: the literal collection is served verbatim, ignoring derivation.
    monkeypatch.setattr(deps.settings, "vector_backend", "qdrant")
    monkeypatch.setattr(
        deps.settings, "qdrant_collection_explicit", "ragstack_sfr_tok256"
    )
    monkeypatch.setattr(deps.settings, "qdrant_collection", "ragstack")
    monkeypatch.setattr(
        deps.settings, "embedding_model", "Salesforce/SFR-Embedding-Mistral"
    )
    monkeypatch.setattr(deps.settings, "embedding_model_dim", 4096)

    store = deps._build_vector_store()
    assert store._collection == "ragstack_sfr_tok256"


def test_qdrant_url_for_routes_configured_collection(monkeypatch):
    # A routed collection resolves to its instance URL; others fall back to qdrant_url.
    monkeypatch.setattr(deps.settings, "qdrant_url", "http://localhost:6333")
    monkeypatch.setattr(
        deps.settings, "qdrant_collection_routes",
        {"ragstack_sfr_semantic": "http://localhost:6343"},
    )
    assert deps._qdrant_url_for("ragstack_sfr_semantic") == "http://localhost:6343"
    assert deps._qdrant_url_for("ragstack_sfr_tok256") == "http://localhost:6333"


def test_qdrant_url_for_default_when_no_routes(monkeypatch):
    # Empty routes → every collection uses qdrant_url (single-instance, unchanged).
    monkeypatch.setattr(deps.settings, "qdrant_url", "http://localhost:6333")
    monkeypatch.setattr(deps.settings, "qdrant_collection_routes", {})
    assert deps._qdrant_url_for("anything") == "http://localhost:6333"


def test_build_vector_store_uses_routed_url(monkeypatch):
    # The routed instance URL reaches the store, not the default qdrant_url.
    import ragstack.stores.qdrant as qmod

    captured: dict = {}

    class _Stub:
        def __init__(self, url, collection, vector_size, api_key=None, **kw):
            captured["url"] = url
            captured["collection"] = collection

    monkeypatch.setattr(qmod, "QdrantVectorStore", _Stub)
    monkeypatch.setattr(deps.settings, "vector_backend", "qdrant")
    monkeypatch.setattr(
        deps.settings, "qdrant_collection_explicit", "ragstack_sfr_semantic"
    )
    monkeypatch.setattr(deps.settings, "qdrant_url", "http://localhost:6333")
    monkeypatch.setattr(
        deps.settings, "qdrant_collection_routes",
        {"ragstack_sfr_semantic": "http://localhost:6343"},
    )
    monkeypatch.setattr(deps.settings, "embedding_model_dim", 4096)

    deps._build_vector_store()
    assert captured["url"] == "http://localhost:6343"
    assert captured["collection"] == "ragstack_sfr_semantic"


def test_es_index_follows_explicit_collection_when_default(monkeypatch):
    # With the explicit override set and elasticsearch_index left at its default,
    # the BM25 leg follows the pinned collection so hybrid reads one corpus.
    monkeypatch.setattr(deps.settings, "qdrant_collection_explicit", "ragstack_sfr_tok256")
    monkeypatch.setattr(deps.settings, "elasticsearch_index", "ragstack")  # default
    assert deps._es_index_name() == "ragstack_sfr_tok256"


def test_es_index_explicit_override_wins(monkeypatch):
    # A non-default elasticsearch_index is respected even under the collection override.
    monkeypatch.setattr(deps.settings, "qdrant_collection_explicit", "ragstack_sfr_tok256")
    monkeypatch.setattr(deps.settings, "elasticsearch_index", "my_custom_bm25")
    assert deps._es_index_name() == "my_custom_bm25"


def test_es_index_default_unchanged_without_override(monkeypatch):
    # No explicit collection → ES index is exactly elasticsearch_index (unchanged).
    monkeypatch.setattr(deps.settings, "qdrant_collection_explicit", "")
    monkeypatch.setattr(deps.settings, "elasticsearch_index", "ragstack")
    assert deps._es_index_name() == "ragstack"


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


def _capture_make_chunker(monkeypatch):
    """Patch deps.make_chunker to record the kwargs it was called with.

    Returns the dict that will hold the captured call. Also stubs out the
    semantic SyncEmbedBridge path is avoided by leaving chunk_method at its
    default ("fixed"), so no embedder is built.
    """
    captured: dict = {}

    def fake_make_chunker(method, **kwargs):
        captured["method"] = method
        captured.update(kwargs)
        return object()  # a stand-in chunker; deps only returns it

    monkeypatch.setattr(deps, "make_chunker", fake_make_chunker)
    return captured


def test_chunker_no_token_sizing_by_default(monkeypatch):
    # Default (chunk_max_tokens=None): the token-sizing path is off — make_chunker
    # gets max_tokens/token_counter=None and no token counter is built (no
    # tokenizer load / endpoint probe at startup).
    monkeypatch.setattr(deps.settings, "chunk_method", "fixed")
    monkeypatch.setattr(deps.settings, "chunk_max_tokens", None)

    def boom(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("make_token_counter must not run when feature is off")

    monkeypatch.setattr(deps, "make_token_counter", boom)
    monkeypatch.setattr(deps, "resolve_max_tokens", boom)
    captured = _capture_make_chunker(monkeypatch)

    chunker, bridge = deps._build_chunker()
    assert bridge is None
    assert captured["max_tokens"] is None
    assert captured["token_counter"] is None


def test_chunker_token_sizing_when_enabled(monkeypatch):
    # chunk_max_tokens set: a TokenCounter is built and a resolved budget +
    # counter are passed into make_chunker, so /v1/ingest caps chunks by tokens.
    monkeypatch.setattr(deps.settings, "chunk_method", "fixed")
    monkeypatch.setattr(deps.settings, "chunk_max_tokens", 256)
    monkeypatch.setattr(deps.settings, "chunk_token_counter", "estimate")
    monkeypatch.setattr(deps.settings, "embedding_endpoints", ["http://emb-1:8000"])
    monkeypatch.setattr(deps.settings, "embedding_model", "BAAI/bge-base")
    monkeypatch.setattr(deps.settings, "openai_api_key", "sk-test")

    sentinel_counter = object()
    counter_calls: dict = {}
    resolve_calls: dict = {}

    def fake_make_token_counter(backend, **kwargs):
        counter_calls["backend"] = backend
        counter_calls.update(kwargs)
        return sentinel_counter

    def fake_resolve_max_tokens(explicit, **kwargs):
        resolve_calls["explicit"] = explicit
        resolve_calls.update(kwargs)
        return 240

    monkeypatch.setattr(deps, "make_token_counter", fake_make_token_counter)
    monkeypatch.setattr(deps, "resolve_max_tokens", fake_resolve_max_tokens)
    captured = _capture_make_chunker(monkeypatch)

    deps._build_chunker()

    # counter built from the configured backend + embedding endpoint/model/key
    assert counter_calls["backend"] == "estimate"
    assert counter_calls["model"] == "BAAI/bge-base"
    assert counter_calls["base_url"] == "http://emb-1:8000"
    assert counter_calls["api_key"] == "sk-test"
    # budget resolved from the explicit override against the same endpoint
    assert resolve_calls["explicit"] == 256
    assert resolve_calls["base_url"] == "http://emb-1:8000"
    # both threaded into make_chunker
    assert captured["max_tokens"] == 240
    assert captured["token_counter"] is sentinel_counter


def test_chunker_token_sizing_falls_back_to_sidecar_url(monkeypatch):
    # With no embedding_endpoints fan-out configured, the single
    # embedding_sidecar_url is used as the budget/counter endpoint.
    monkeypatch.setattr(deps.settings, "chunk_method", "fixed")
    monkeypatch.setattr(deps.settings, "chunk_max_tokens", 128)
    monkeypatch.setattr(deps.settings, "chunk_token_counter", "estimate")
    monkeypatch.setattr(deps.settings, "embedding_endpoints", [])
    monkeypatch.setattr(deps.settings, "embedding_sidecar_url", "http://localhost:50053")

    seen: dict = {}
    monkeypatch.setattr(
        deps, "make_token_counter", lambda backend, **kw: seen.update(kw) or object()
    )
    monkeypatch.setattr(
        deps, "resolve_max_tokens", lambda explicit, **kw: 100
    )
    _capture_make_chunker(monkeypatch)

    deps._build_chunker()
    assert seen["base_url"] == "http://localhost:50053"


def test_text_index_is_inmemory_but_warns_under_durable(monkeypatch, caplog):
    import logging

    from ragstack.stores import InMemoryTextIndex

    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    with caplog.at_level(logging.WARNING):
        index = deps._build_text_index()
    assert isinstance(index, InMemoryTextIndex)
    assert any("text index is in-memory" in r.message for r in caplog.records)
