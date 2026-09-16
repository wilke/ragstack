"""Unit tests for backend wiring — the require_durable_backends gate."""
import json

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
    # Absolute: a relative sqlite path is refused under
    # require_durable_backends (two servers in one CWD would share it).
    monkeypatch.setattr(deps.settings, "user_store_path", "/tmp/rs-test-users.db")
    # Same reasoning for the collection registry: it backs the atomic
    # MAX_COLLECTIONS reservation (#286), so production refuses a json store
    # with nowhere to write. These fixtures exercise the tenant-map rule.
    monkeypatch.setattr(deps.settings, "collection_store_backend", "sqlite")
    monkeypatch.setattr(deps.settings, "collection_store_path", "/tmp/rs-test-colls.db")


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


def test_es_url_for_routes_configured_index(monkeypatch):
    # A routed INDEX resolves to its cluster; others fall back to elasticsearch_url.
    monkeypatch.setattr(deps.settings, "elasticsearch_url", "http://localhost:9200")
    monkeypatch.setattr(
        deps.settings, "es_collection_routes",
        {"ragstack_sfr_semantic": "http://localhost:9243"},
    )
    assert deps._es_url_for("ragstack_sfr_semantic") == "http://localhost:9243"
    assert deps._es_url_for("ragstack_sfr_tok256") == "http://localhost:9200"


def test_es_url_for_default_when_no_routes(monkeypatch):
    # Empty routes → every index uses elasticsearch_url (single-instance, unchanged).
    monkeypatch.setattr(deps.settings, "elasticsearch_url", "http://localhost:9200")
    monkeypatch.setattr(deps.settings, "es_collection_routes", {})
    assert deps._es_url_for("anything") == "http://localhost:9200"


def test_build_text_index_uses_routed_url(monkeypatch):
    # The routed cluster URL reaches the ES client, not the default elasticsearch_url
    # — the text-leg mirror of test_build_vector_store_uses_routed_url.
    import ragstack.stores.elasticsearch as esmod

    captured: dict = {}

    class _Stub:
        def __init__(self, url, index, api_key=None, **kw):
            captured["url"] = url
            captured["index"] = index

    monkeypatch.setattr(esmod, "ElasticsearchTextIndex", _Stub)
    monkeypatch.setattr(deps.settings, "text_backend", "elasticsearch")
    monkeypatch.setattr(deps.settings, "elasticsearch_url", "http://localhost:9200")
    monkeypatch.setattr(
        deps.settings, "es_collection_routes",
        {"ragstack_sfr_semantic": "http://localhost:9243"},
    )

    deps._build_text_index_for("ragstack_sfr_semantic")
    assert captured == {"url": "http://localhost:9243", "index": "ragstack_sfr_semantic"}
    # An unrouted index on the same call path still gets the bare setting.
    deps._build_text_index_for("ragstack_sfr_tok256")
    assert captured["url"] == "http://localhost:9200"


def test_build_text_index_routes_on_the_index_not_the_collection(monkeypatch):
    """The route key is the ES index name.

    `_build_text_index_for` is called with `spec.es_index()` (and with
    `_es_index_name()` for the default), so a table keyed by the Qdrant
    collection routes nothing here — the safe direction, but worth pinning: it
    is the asymmetry between the two tables."""
    import ragstack.stores.elasticsearch as esmod

    captured: dict = {}

    class _Stub:
        def __init__(self, url, index, api_key=None, **kw):
            captured["url"] = url

    monkeypatch.setattr(esmod, "ElasticsearchTextIndex", _Stub)
    monkeypatch.setattr(deps.settings, "text_backend", "elasticsearch")
    monkeypatch.setattr(deps.settings, "elasticsearch_url", "http://localhost:9200")
    monkeypatch.setattr(
        deps.settings, "es_collection_routes",
        {"ragstack_sfr_tok512": "http://localhost:9243"},  # a COLLECTION name
    )
    deps._build_text_index_for("shared_text_v1")  # the entry's actual index
    assert captured["url"] == "http://localhost:9200"


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


def test_chunker_refuses_to_boot_on_an_unloadable_tokenizer(monkeypatch):
    """The API-side half of the refusal, through the REAL factory.

    ``_build_chunker`` is called from ``lifespan``, so this failure is a **boot
    refusal**: a deployment with token sizing on and a broken tokenizer does not
    start, instead of starting and capping every chunk with a heuristic budget
    that is ~1.4x off. Every other chunker test here stubs ``make_token_counter``
    out, so none of them would notice a re-added fallback — this one deliberately
    does not stub it.

    ``chunk_max_tokens`` is set (the gate on the counter being built at all), and
    is an explicit int, so ``resolve_max_tokens`` returns from the override
    without probing the endpoint — no network on this path.
    """
    from ragstack.ingestion.tokenization import HFTokenCounter

    def boom(self):
        raise RuntimeError("no transformers")

    monkeypatch.setattr(HFTokenCounter, "_tokenizer", boom)
    monkeypatch.setattr(deps.settings, "chunk_method", "fixed")
    monkeypatch.setattr(deps.settings, "chunk_max_tokens", 256)
    monkeypatch.setattr(deps.settings, "chunk_token_counter", "hf")
    monkeypatch.setattr(deps.settings, "embedding_endpoints", [])
    monkeypatch.setattr(deps.settings, "embedding_sidecar_url", "http://127.0.0.1:1")
    monkeypatch.setattr(deps.settings, "embedding_model", "ragstack-tests/no-such-tokenizer")
    _capture_make_chunker(monkeypatch)

    with pytest.raises(RuntimeError, match="chunk_token_counter"):
        deps._build_chunker()


def test_chunker_explicit_estimate_setting_still_boots(monkeypatch):
    """...and the settings-level opt-in still works under the same conditions.

    ``CHUNK_TOKEN_COUNTER=estimate`` is the escape hatch the refusal message
    names, so it has to work on a host where no tokenizer can load — otherwise
    the fix leaves such a deployment with no way to run at all. Real factory
    again: a mutation that made the estimator raise too would pass every stubbed
    test above and fail here.
    """
    from ragstack.ingestion.tokenization import EstimatingTokenCounter, HFTokenCounter

    def boom(self):  # pragma: no cover - must not be reached
        raise RuntimeError("no transformers")

    monkeypatch.setattr(HFTokenCounter, "_tokenizer", boom)
    monkeypatch.setattr(deps.settings, "chunk_method", "fixed")
    monkeypatch.setattr(deps.settings, "chunk_max_tokens", 256)
    monkeypatch.setattr(deps.settings, "chunk_token_counter", "estimate")
    monkeypatch.setattr(deps.settings, "embedding_endpoints", [])
    monkeypatch.setattr(deps.settings, "embedding_sidecar_url", "http://127.0.0.1:1")
    monkeypatch.setattr(deps.settings, "embedding_model", "ragstack-tests/no-such-tokenizer")
    captured = _capture_make_chunker(monkeypatch)

    chunker, bridge = deps._build_chunker()
    assert chunker is not None and bridge is None
    assert isinstance(captured["token_counter"], EstimatingTokenCounter)
    assert captured["max_tokens"] == 240  # 256 minus the specials reserve


def test_text_index_is_inmemory_but_warns_under_durable(monkeypatch, caplog):
    import logging

    from ragstack.stores import InMemoryTextIndex

    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    with caplog.at_level(logging.WARNING):
        index = deps._build_text_index()
    assert isinstance(index, InMemoryTextIndex)
    assert any("text index is in-memory" in r.message for r in caplog.records)


# --- #563: WHICH registry, on the two submission paths that are not ingest --- #
#
# `_gowe_inputs` (the ingest path) is covered in tests/api/test_ingest_gowe_path.py.
# These two are the OTHER submitters — restore and graph-extract — and they were
# written without tests: deleting both seeds left the whole suite green. A seed
# nothing asserts is a seed that silently stops working, which on this path means
# a restore or a graph load resolving against another tenant's registry.


def _static_inputs_for_restore(monkeypatch):
    """The static workflow inputs `_build_lifecycle_gate` gives its restorer."""
    import httpx

    from ragstack.collection_store import InMemoryCollectionStore

    monkeypatch.setattr(deps.settings, "collection_restore_inputs_json", "")
    http = httpx.AsyncClient(base_url="http://127.0.0.1:1")
    gate = deps._build_lifecycle_gate(InMemoryCollectionStore(), http)
    return gate.restorer.static_inputs


def _static_inputs_for_graph_extract(monkeypatch):
    """The static workflow inputs `_build_graph_extract_runner` gives its runner."""
    import httpx

    from ragstack.collection_store import InMemoryCollectionStore

    monkeypatch.setattr(deps.settings, "graph_extract_inputs_json", "")
    http = httpx.AsyncClient(base_url="http://127.0.0.1:1")
    runner = deps._build_graph_extract_runner(None, InMemoryCollectionStore(), http)
    return runner.static_inputs


@pytest.mark.parametrize("static_inputs_for", [
    _static_inputs_for_restore, _static_inputs_for_graph_extract,
], ids=["restore", "graph-extract"])
def test_no_registry_is_seeded_when_the_setting_is_unset(
        static_inputs_for, monkeypatch):
    """Default is silence, and silence is the pre-#563 contract: the worker uses
    its own unsuffixed COLLECTION_STORE_*."""
    monkeypatch.setattr(deps.settings, "collection_registry_name", "")
    assert "registry" not in static_inputs_for(monkeypatch)


@pytest.mark.parametrize("static_inputs_for", [
    _static_inputs_for_restore, _static_inputs_for_graph_extract,
], ids=["restore", "graph-extract"])
def test_the_registry_name_is_seeded_on_every_submission_path(
        static_inputs_for, monkeypatch):
    """Restore replays into the stores named by a registry entry and
    graph-extract's load leg resolves its graph scope the same way, so both need
    to say WHICH registry for exactly the reason the ingest path does — and both
    read it from the same setting, so neither may be the one that was forgotten."""
    monkeypatch.setattr(deps.settings, "collection_registry_name", "hackathon")
    assert static_inputs_for(monkeypatch)["registry"] == "hackathon"


@pytest.mark.parametrize("static_inputs_for", [
    _static_inputs_for_restore, _static_inputs_for_graph_extract,
], ids=["restore", "graph-extract"])
def test_no_submission_path_seeds_a_registry_credential(
        static_inputs_for, monkeypatch):
    """A NAME, and only a name, on every path. `submitted_inputs` is an
    immutable snapshot the UI renders and the engine stores in plaintext, so a
    DSN placed there could never be withdrawn."""
    dsn = "postgresql+asyncpg://ragstack:hunter2@db.internal/ragstack"
    monkeypatch.setattr(deps.settings, "collection_registry_name", "hackathon")
    monkeypatch.setattr(deps.settings, "collection_store_dsn", dsn)
    monkeypatch.setattr(deps.settings, "postgres_dsn", dsn)
    blob = json.dumps(static_inputs_for(monkeypatch), default=str)
    assert "hackathon" in blob
    assert "hunter2" not in blob and dsn not in blob
