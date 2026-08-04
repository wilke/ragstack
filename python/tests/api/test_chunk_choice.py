"""Choosing a chunker when creating a library, end to end.

Three things have to hold for the UI's chunker picker to be honest:

1. ``POST /v1/collections`` accepts every method in ``CHUNK_METHODS``, records it
   in the spec/provenance, and refuses a config that cannot chunk (with a message
   worth showing a user) rather than minting a dead collection.
2. A targeted ingest builds the *collection's* chunker, not the server default.
3. The picker in the UI offers exactly the methods the server accepts — the list
   is mirrored in TypeScript, so it is diffed against ``CHUNK_METHODS`` here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from ragstack.api import security
from ragstack.api.collections import CollectionEntry
from ragstack.api.deps import _chunker_for, _embed_fn_for, build_ingestor_for
from ragstack.api.security import ROLE_ADMIN
from ragstack.ingestion.chunkers import (
    CHUNK_METHODS,
    FixedTokenWindowChunker,
    RecursiveCharacterChunker,
    SemanticChunker,
    SentenceChunker,
    WordChunker,
)

EMB = {
    "id": "emb-sfr", "task": "embedding", "provider": "vllm",
    "base_urls": ["http://localhost:9100"], "model": "test/sfr", "dim": 8,
}


# --------------------------------------------------------------------------- #
# 3. UI / server parity of the method list
# --------------------------------------------------------------------------- #

_TS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "chunkers.ts"


def test_frontend_offers_exactly_the_server_chunk_methods():
    """The picker's method list is mirrored in TypeScript (no endpoint serves it),
    so it can drift silently. Diff it here: a chunker added or removed server-side
    must be reflected in frontend/src/lib/chunkers.ts."""
    if not _TS.exists():  # pragma: no cover - python-only checkout
        pytest.skip(f"frontend not present at {_TS}")
    src = _TS.read_text(encoding="utf-8")
    m = re.search(r"export const CHUNK_METHODS = \[(.*?)\]", src, re.DOTALL)
    assert m, "CHUNK_METHODS array not found in frontend/src/lib/chunkers.ts"
    listed = tuple(re.findall(r'"([a-z_]+)"', m.group(1)))
    assert listed == CHUNK_METHODS, (
        f"frontend chunk methods {listed} != server CHUNK_METHODS {CHUNK_METHODS}"
    )


# --------------------------------------------------------------------------- #
# 2. A targeted ingest uses the COLLECTION's chunker (unit, offline)
# --------------------------------------------------------------------------- #


def _entry(method: str = "fixed", **over) -> CollectionEntry:
    kwargs: dict = {
        "id": "acme", "label": "acme", "collection": "physical_acme", "model": "m", "dim": 8,
        "chunk_method": method, "chunk_size": 200, "chunk_overlap": 20, "chunk_params": {},
        "is_default": False, "retriever": object(),
        "vector_store": object(), "text_index": object(), "embedder": object(),
    }
    kwargs.update(over)
    return CollectionEntry(**kwargs)


@pytest.mark.parametrize(
    ("method", "cls"),
    [("fixed", RecursiveCharacterChunker), ("sentence", SentenceChunker), ("words", WordChunker)],
)
def test_chunker_for_builds_the_collections_method(method, cls):
    c = _chunker_for(_entry(method))
    assert isinstance(c, cls)
    # ...sized by the COLLECTION's numbers, not the server defaults (512/64).
    assert (c.chunk_size, c.chunk_overlap) == (200, 20)


@pytest.mark.parametrize("method", ["semantic", "semantic_pooled"])
def test_chunker_for_builds_semantic_with_a_bridge(method):
    """Both semantic variants are now buildable per collection — previously
    _chunker_for rejected them outright, so a semantic library could be created
    but never ingested into."""
    c = _chunker_for(_entry(method), embed_fn=lambda texts: [[0.0] * 8 for _ in texts])
    assert isinstance(c, SemanticChunker)
    assert c.pool_sentences is (method == "semantic_pooled")


def test_chunker_for_honours_semantic_params():
    entry = _entry(
        "semantic",
        chunk_params={
            "buffer_size": 5,
            "breakpoint_percentile_threshold": 92.5,
            "min_chunk_length": 250,
        },
    )
    c = _chunker_for(entry, embed_fn=lambda texts: [[0.0] * 8 for _ in texts])
    assert c.buffer_size == 5
    assert c.breakpoint_percentile_threshold == 92.5
    assert c.min_chunk_length == 250


def test_chunker_for_rejects_a_junk_semantic_param():
    entry = _entry("semantic", chunk_params={"buffer_size": "lots"})
    with pytest.raises(ValueError, match="buffer_size"):
        _chunker_for(entry, embed_fn=lambda texts: [])


@pytest.mark.parametrize("method", ["semantic", "semantic_pooled"])
def test_chunker_for_still_refuses_semantic_without_an_embed_fn(method):
    # Never silently fall back to a different chunking — the router turns this
    # ValueError into a 400.
    with pytest.raises(ValueError):
        _chunker_for(_entry(method))


def _app_state() -> SimpleNamespace:
    return SimpleNamespace(
        graph_store=None, kg_extractor=None, http_client=httpx.AsyncClient(), job_store=None
    )


def test_embed_fn_only_built_for_semantic_collections():
    state = _app_state()
    assert _embed_fn_for(state, _entry("fixed_token")) is None
    assert _embed_fn_for(state, _entry("semantic")) is not None


def test_embed_bridge_is_cached_per_collection():
    """One background loop + httpx client per collection, not per ingest request."""
    state = _app_state()
    entry = _entry("semantic")
    first = _embed_fn_for(state, entry)
    assert _embed_fn_for(state, entry) is first
    assert state.collection_embed_bridges == {"acme": first}
    # a different collection gets its own bridge (its own embedding backend)
    other = _embed_fn_for(state, _entry("semantic", id="other"))
    assert other is not first
    for b in state.collection_embed_bridges.values():
        b.close()


def test_semantic_ingestor_builds_end_to_end():
    """The whole path a semantic library takes on /v1/ingest: entry → bridge →
    chunker → pipeline. This used to raise ValueError at _chunker_for."""
    state = _app_state()
    ing = build_ingestor_for(state, _entry("semantic", chunk_params={"buffer_size": 4}))
    chunker = ing._pipeline.chunker
    assert isinstance(chunker, SemanticChunker)
    assert chunker.buffer_size == 4
    for b in state.collection_embed_bridges.values():
        b.close()


def test_fixed_token_binds_the_collections_tokenizer(monkeypatch):
    """fixed_token sizes its window in the COLLECTION's embedding-model tokens, so
    the counter must be built from entry.model."""
    seen: dict = {}

    class _Counter:
        def _tokenizer(self):
            return object()

    def _fake(backend, **kw):
        seen.update({"backend": backend, **kw})
        return _Counter()

    monkeypatch.setattr("ragstack.api.deps.make_token_counter", _fake)
    c = _chunker_for(_entry("fixed_token", model="acme/embedder"))
    assert isinstance(c, FixedTokenWindowChunker)
    assert seen["backend"] == "hf" and seen["model"] == "acme/embedder"


# --------------------------------------------------------------------------- #
# 1. The create endpoint (API level)
# --------------------------------------------------------------------------- #

@pytest.fixture
def _admin(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)


async def _register(client):
    r = await client.post("/v1/admin/models/registry", json=EMB)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("method", list(CHUNK_METHODS))
async def test_every_chunk_method_is_creatable(client, _admin, method):
    """Every method the picker offers must actually mint a collection, and the
    chosen method must come back on the created entry."""
    await _register(client)
    chunk: dict = {"method": method}
    if method not in ("semantic", "semantic_pooled"):
        chunk |= {"size": 256, "overlap": 32}
    r = await client.post(
        "/v1/collections", json={"embedding": "emb-sfr", "chunk": chunk, "id": f"lib-{method}"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["chunk_method"] == method


@pytest.mark.asyncio
async def test_semantic_library_records_no_fake_size(client, _admin, monkeypatch, tmp_path):
    """A semantic library sends no size/overlap, so its spec (and therefore its
    manifest/provenance) must not claim a window it never used."""
    from ragstack.api.collections import CollectionSpec

    f = tmp_path / "libs.json"
    monkeypatch.setattr("ragstack.config.settings.collections_file", str(f))
    await _register(client)
    r = await client.post(
        "/v1/collections",
        json={
            "embedding": "emb-sfr",
            "chunk": {"method": "semantic", "params": {"buffer_size": 4}},
            "id": "sem",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["chunk_method"] == "semantic" and body["chunk_size"] is None

    spec = next(
        CollectionSpec.model_validate(d) for d in json.loads(f.read_text()) if d["id"] == "sem"
    )
    assert spec.chunk_size is None and spec.chunk_overlap is None
    assert spec.chunk_params == {"buffer_size": 4}


@pytest.mark.asyncio
async def test_chosen_chunker_survives_into_the_built_entry(client, _admin):
    await _register(client)
    r = await client.post(
        "/v1/collections",
        json={
            "embedding": "emb-sfr",
            "chunk": {"method": "sentence", "size": 900, "overlap": 90},
            "id": "sent",
        },
    )
    assert r.status_code == 201, r.text

    from ragstack.api.main import app

    entry = app.state.collections.resolve("sent")
    assert (entry.chunk_method, entry.chunk_size, entry.chunk_overlap) == ("sentence", 900, 90)
    # ...and an ingest targeting it builds THAT chunker.
    chunker = _chunker_for(entry)
    assert isinstance(chunker, SentenceChunker)
    assert (chunker.chunk_size, chunker.chunk_overlap) == (900, 90)


@pytest.mark.asyncio
async def test_created_entry_retains_its_embedding_backend(client, _admin):
    """Needed so a semantic ingest rebuilds the collection's OWN embedder on the
    bridge loop rather than the server default."""
    await _register(client)
    r = await client.post(
        "/v1/collections",
        json={"embedding": "emb-sfr", "chunk": {"method": "semantic"}, "id": "sem2"},
    )
    assert r.status_code == 201, r.text

    from ragstack.api.main import app

    entry = app.state.collections.resolve("sem2")
    assert entry.embedding_endpoints == ["http://localhost:9100"]
    assert entry.embedding_api == "openai"


# --- validation: a bad config must 400 with a readable reason --------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chunk", "expect"),
    [
        ({"method": "nope"}, "unknown chunk method"),
        ({"method": "fixed_token", "size": 0}, "at least 1"),
        ({"method": "fixed_token", "size": -5}, "at least 1"),
        ({"method": "fixed", "size": -1}, "whole document"),
        ({"method": "fixed_token", "size": 256, "overlap": 256}, "never advances"),
        ({"method": "fixed_token", "size": 256, "overlap": 300}, "never advances"),
        ({"method": "fixed_token", "size": 256, "overlap": -1}, "negative"),
        ({"method": "semantic", "params": {"buffer_size": 0}}, "between"),
        ({"method": "semantic", "params": {"breakpoint_percentile_threshold": 150}}, "between"),
        ({"method": "semantic", "params": {"buffer_size": "many"}}, "must be a number"),
        ({"method": "semantic", "params": {"min_chunk_length": 1.5}}, "whole number"),
    ],
)
async def test_bad_chunk_config_is_a_readable_400(client, _admin, chunk, expect):
    await _register(client)
    r = await client.post("/v1/collections", json={"embedding": "emb-sfr", "chunk": chunk})
    assert r.status_code == 400, r.text
    assert expect in r.json()["detail"]


@pytest.mark.asyncio
async def test_whole_document_size_is_allowed_for_sentence(client, _admin):
    # -1 is "one chunk per document", implemented by sentence/words only.
    await _register(client)
    r = await client.post(
        "/v1/collections",
        json={"embedding": "emb-sfr", "chunk": {"method": "words", "size": -1, "overlap": 0}},
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_unknown_semantic_params_ride_along(client, _admin):
    # `params` is free-form in the contract; only the tunables we know are checked.
    await _register(client)
    r = await client.post(
        "/v1/collections",
        json={
            "embedding": "emb-sfr",
            "chunk": {"method": "semantic_pooled", "params": {"future_knob": "x"}},
        },
    )
    assert r.status_code == 201, r.text
