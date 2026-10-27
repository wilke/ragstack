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
        "is_shared_surface": False, "retriever": object(),
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

    # The factory (#647) is where the counter is built now, for every method.
    monkeypatch.setattr("ragstack.ingestion.chunker_config.make_token_counter", _fake)
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
async def test_semantic_library_without_a_size_gets_the_default_token_cap(
    client, _admin, monkeypatch, tmp_path
):
    """Replaces the old "semantic records no fake size" rule (owner decision on
    #829): a semantic library that sends no size is capped at the deployment's
    default size — CHUNK_SIZE, tokens under the default fixed_token method —
    recorded as a number with the marker, so the spec says what the chunker does."""
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
    assert body["chunk_method"] == "semantic" and body["chunk_size"] == 512

    spec = next(
        CollectionSpec.model_validate(d) for d in json.loads(f.read_text()) if d["id"] == "sem"
    )
    assert spec.chunk_size == 512 and spec.chunk_overlap is None
    assert spec.chunk_params == {"buffer_size": 4, "size_cap": "tokens",
                                 "cap_split": "greedy", "breakpoint_cap": "size"}


@pytest.mark.asyncio
async def test_semantic_default_size_is_refused_when_chunk_size_counts_characters(
    client, _admin, monkeypatch, tmp_path
):
    """CHUNK_METHOD=fixed makes CHUNK_SIZE a character count; it is not
    reinterpreted as a token cap. An explicit size still works."""
    monkeypatch.setattr("ragstack.config.settings.collections_file", str(tmp_path / "l.json"))
    monkeypatch.setattr("ragstack.config.settings.chunk_method", "fixed")
    await _register(client)
    r = await client.post("/v1/collections", json={
        "embedding": "emb-sfr", "id": "semc", "chunk": {"method": "semantic"}})
    assert r.status_code == 400 and "counts characters" in r.json()["detail"], r.text
    r = await client.post("/v1/collections", json={
        "embedding": "emb-sfr", "id": "semc", "chunk": {"method": "semantic", "size": 256}})
    assert r.status_code == 201, r.text


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


# --------------------------------------------------------------------------- #
# size_unit (#318 / #802): validated at create, the same check ingest runs
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["sentence", "words"])
@pytest.mark.parametrize("unit", ["chars", "tokens"])
async def test_size_unit_is_accepted_for_sentence_and_words(client, _admin, method, unit):
    await _register(client)
    r = await client.post("/v1/collections", json={
        "embedding": "emb-sfr", "id": f"su-{method}-{unit}",
        "chunk": {"method": method, "size": 256, "overlap": 32, "params": {"size_unit": unit}},
    })
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "unit", "needle"),
    [
        ("sentence", "bytes", "must be one of chars, tokens"),
        ("words", 7, "must be one of"),
        ("fixed", "tokens", "applies only to sentence, words"),
        ("fixed", "chars", "applies only to sentence, words"),
        ("fixed_token", "chars", "applies only to sentence, words"),
        ("semantic", "tokens", "applies only to sentence, words"),
    ],
)
async def test_bad_size_unit_is_refused_at_create(client, _admin, method, unit, needle):
    """400, like every other invalid chunk config (contracts/openapi.yaml lists
    400 "chunk config is invalid" for POST /v1/collections; 422 is reserved for a
    body that fails the schema, and `params` is free-form there)."""
    await _register(client)
    chunk: dict = {"method": method, "params": {"size_unit": unit}}
    if method not in ("semantic", "semantic_pooled"):
        chunk |= {"size": 256, "overlap": 32}
    r = await client.post("/v1/collections", json={
        "embedding": "emb-sfr", "id": f"bad-{method}", "chunk": chunk,
    })
    assert r.status_code == 400, r.text
    assert needle in r.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["sentence", "words"])
async def test_default_size_unit_is_dropped_so_the_hash_does_not_fork(
    client, _admin, monkeypatch, tmp_path, method
):
    """`{"size_unit": "chars"}` IS the default chunking, so it must content-address
    to the same physical store as `{}` — chunk_params feed chunk_descriptor ->
    spec_hash -> the physical name. The key is dropped before the spec is stored;
    a non-default unit is kept (and so, correctly, forks the name)."""
    from ragstack.api.collections import CollectionSpec

    f = tmp_path / "libs.json"
    monkeypatch.setattr("ragstack.config.settings.collections_file", str(f))
    await _register(client)
    base = {"method": method, "size": 256, "overlap": 32}
    def create(params):
        # No explicit id: the physical name is content-addressed over the spec.
        return client.post("/v1/collections", json={
            "embedding": "emb-sfr", "chunk": base | ({"params": params} if params else {}),
        })

    plain = await create({})
    assert plain.status_code == 201, plain.text
    plain_name = plain.json()["id"]
    # Same chunking -> the SAME content address, so it is the existing collection.
    chars = await create({"size_unit": "chars"})
    assert chars.status_code == 409 and plain_name in chars.json()["detail"], chars.text
    tokens = await create({"size_unit": "tokens"})
    assert tokens.status_code == 201, tokens.text
    tokens_name = tokens.json()["id"]
    assert tokens_name != plain_name
    specs = {d["collection"]: CollectionSpec.model_validate(d) for d in json.loads(f.read_text())}
    assert specs[plain_name].chunk_params == {}
    assert specs[tokens_name].chunk_params == {"size_unit": "tokens"}


# --------------------------------------------------------------------------- #
# The semantic size cap at create (marker recorded; overlap warned, not refused)
# --------------------------------------------------------------------------- #


async def _create_semantic(client, monkeypatch, tmp_path, cid, chunk):
    from ragstack.api.collections import CollectionSpec

    f = tmp_path / "libs.json"
    monkeypatch.setattr("ragstack.config.settings.collections_file", str(f))
    reg = await client.post("/v1/admin/models/registry", json=EMB)
    assert reg.status_code == 201 or "already exists" in reg.text, reg.text  # 2nd create
    r = await client.post("/v1/collections", json={
        "embedding": "emb-sfr", "id": cid, "chunk": chunk,
    })
    spec = None
    if r.status_code == 201:
        spec = next(
            CollectionSpec.model_validate(d) for d in json.loads(f.read_text()) if d["id"] == cid
        )
    return r, spec


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["semantic", "semantic_pooled"])
async def test_semantic_with_a_size_records_the_token_cap(client, _admin, monkeypatch, tmp_path,
                                                          method):
    """A NEW semantic collection with a size records that the size is a token cap,
    and the split strategy, as concrete values — so the bulk path and a later
    default change cannot read the spec any other way."""
    r, spec = await _create_semantic(client, monkeypatch, tmp_path, "capped", {
        "method": method, "size": 512, "overlap": 0, "params": {"buffer_size": 4},
    })
    assert r.status_code == 201, r.text
    assert spec.chunk_size == 512
    assert spec.chunk_params == {"buffer_size": 4, "size_cap": "tokens", "cap_split": "greedy",
                                 "breakpoint_cap": "size"}


@pytest.mark.asyncio
async def test_semantic_keeps_an_explicit_cap_split(client, _admin, monkeypatch, tmp_path):
    r, spec = await _create_semantic(client, monkeypatch, tmp_path, "bal", {
        "method": "semantic", "size": 256,
        "params": {"cap_split": "balanced", "breakpoint_cap": "window"},
    })
    assert r.status_code == 201, r.text
    assert spec.chunk_params == {"size_cap": "tokens", "cap_split": "balanced",
                                 "breakpoint_cap": "window"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("chunk", "needle"), [
    ({"method": "semantic", "size": 512, "params": {"cap_split": "smart"}}, "cap_split"),
    ({"method": "semantic", "size": 512, "params": {"size_cap": "chars"}}, "size_cap"),
    ({"method": "semantic", "size": 512, "params": {"breakpoint_cap": "all"}}, "breakpoint_cap"),
    ({"method": "words", "size": 512, "params": {"breakpoint_cap": "size"}}, "applies only"),
    ({"method": "semantic", "size": 0}, "at least 1"),
    ({"method": "fixed_token", "size": 512, "params": {"size_cap": "tokens"}}, "applies only"),
    ({"method": "sentence", "size": 512, "params": {"cap_split": "greedy"}}, "applies only"),
])
async def test_bad_semantic_cap_is_a_400(client, _admin, monkeypatch, tmp_path, chunk, needle):
    r, _spec = await _create_semantic(client, monkeypatch, tmp_path, "bad", chunk)
    assert r.status_code == 400, r.text
    assert needle in r.json()["detail"]


@pytest.mark.asyncio
async def test_semantic_cap_over_chunk_max_tokens_is_refused_at_create(
    client, _admin, monkeypatch, tmp_path
):
    monkeypatch.setattr("ragstack.config.settings.chunk_max_tokens", 528)  # budget 512
    ok, _ = await _create_semantic(client, monkeypatch, tmp_path, "fits",
                                   {"method": "semantic", "size": 512})
    assert ok.status_code == 201, ok.text
    r, _ = await _create_semantic(client, monkeypatch, tmp_path, "over",
                                  {"method": "semantic", "size": 513})
    assert r.status_code == 400, r.text
    assert "512" in r.json()["detail"]


@pytest.mark.asyncio
async def test_semantic_overlap_is_warned_not_refused(client, _admin, monkeypatch, tmp_path,
                                                      caplog):
    with caplog.at_level("WARNING", logger="ragstack.api.routers.collections"):
        r, spec = await _create_semantic(client, monkeypatch, tmp_path, "ov", {
            "method": "semantic", "size": 512, "overlap": 64,
        })
    assert r.status_code == 201, r.text
    assert spec.chunk_overlap == 64  # recorded as sent: not refused, not rewritten
    warned = [m.getMessage() for m in caplog.records if "ignores chunk_overlap" in m.getMessage()]
    assert warned and "64" in warned[0]
    caplog.clear()
    with caplog.at_level("WARNING", logger="ragstack.api.routers.collections"):
        r, _ = await _create_semantic(client, monkeypatch, tmp_path, "nov", {
            "method": "semantic", "size": 512, "overlap": 0,
        })
    assert r.status_code == 201
    assert not [m for m in caplog.records if "ignores chunk_overlap" in m.getMessage()]


def test_unmarked_semantic_entry_builds_uncapped(monkeypatch):
    """A pre-marker entry (chunk_size recorded, no size_cap) keeps window-only
    chunking; a marked one is capped at its size."""
    monkeypatch.setattr("ragstack.config.settings.chunk_max_tokens", 4096)
    monkeypatch.setattr("ragstack.config.settings.chunk_token_counter", "estimate")
    embed = lambda texts: [[0.0] * 8 for _ in texts]  # noqa: E731
    old = _chunker_for(_entry("semantic", chunk_size=512), embed_fn=embed)
    assert old.size_cap_tokens is None and old.cap_split == "greedy"
    new = _chunker_for(
        _entry("semantic", chunk_size=512,
               chunk_params={"size_cap": "tokens", "cap_split": "topic"}),
        embed_fn=embed,
    )
    assert new.size_cap_tokens == 512 and new.cap_split == "topic"


@pytest.mark.asyncio
async def test_semantic_server_default_cap_is_clamped_to_the_budget_at_create(
    client, _admin, monkeypatch, tmp_path, caplog
):
    """A create that omits `chunk` takes the server default — which, for a semantic
    default, records the size-cap marker. CHUNK_SIZE=1024 over a 512-token budget
    used to mint the collection (201) recording 1024 and then fail every ingest
    with a 400. The deployment default is now clamped to the budget and the
    clamped number is what the spec records, so every ingest can honour it."""
    from ragstack.api.collections import CollectionSpec

    f = tmp_path / "libs.json"
    monkeypatch.setattr("ragstack.config.settings.collections_file", str(f))
    monkeypatch.setattr("ragstack.config.settings.chunk_method", "semantic")
    monkeypatch.setattr("ragstack.config.settings.chunk_max_tokens", 528)  # budget 512
    monkeypatch.setattr("ragstack.config.settings.chunk_size", 1024)
    await _register(client)
    with caplog.at_level("INFO", logger="ragstack.api.routers.collections"):
        r = await client.post("/v1/collections", json={"embedding": "emb-sfr", "id": "dflt"})
    assert r.status_code == 201, r.text
    spec = next(CollectionSpec.model_validate(d) for d in json.loads(f.read_text()))
    assert spec.chunk_size == 512
    assert spec.chunk_params == {"size_cap": "tokens", "cap_split": "greedy",
                                 "breakpoint_cap": "size"}
    assert any("default semantic chunk size 1024" in m.getMessage() for m in caplog.records)


@pytest.mark.asyncio
async def test_semantic_default_size_is_clamped_but_an_explicit_one_is_refused(
    client, _admin, monkeypatch, tmp_path, caplog
):
    """A size-less semantic create (what the UI sends) on a tight-window
    deployment: the default CHUNK_SIZE (512) over the budget (256) is clamped and
    recorded as 256. A size the CALLER sends over the budget is refused, never
    clamped."""
    monkeypatch.setattr("ragstack.config.settings.chunk_max_tokens", 272)  # budget 256
    with caplog.at_level("INFO", logger="ragstack.api.routers.collections"):
        r, spec = await _create_semantic(client, monkeypatch, tmp_path, "nosize",
                                         {"method": "semantic"})
    assert r.status_code == 201, r.text
    assert spec.chunk_size == 256 and spec.chunk_params["size_cap"] == "tokens"
    assert any("recording 256" in m.getMessage() for m in caplog.records)
    r, _ = await _create_semantic(client, monkeypatch, tmp_path, "explicit",
                                  {"method": "semantic", "size": 257})
    assert r.status_code == 400 and "256" in r.json()["detail"], r.text
    # Within the budget the default is not touched.
    monkeypatch.setattr("ragstack.config.settings.chunk_max_tokens", 4096)
    r, spec = await _create_semantic(client, monkeypatch, tmp_path, "roomy",
                                     {"method": "semantic"})
    assert r.status_code == 201 and spec.chunk_size == 512, r.text
