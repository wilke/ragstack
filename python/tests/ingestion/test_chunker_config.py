"""Tests for the shared chunker factory (build_chunker) used by both bulk
ingesters. Offline — no network/tokenizer download except where patched.

Regression-guards the #133 blocker: `fixed_token` must receive a token_counter,
not crash in make_chunker.
"""
from __future__ import annotations

import pytest

from ragstack.ingestion import chunker_config
from ragstack.ingestion.chunker_config import build_chunker, resolve_token_backend


def test_fixed_uses_estimate_without_model_offline() -> None:
    # No model → estimate backend (zero-dep), no network. Proves the char path.
    chunker, counter, max_tokens = build_chunker("fixed", chunk_size=200, chunk_overlap=20)
    assert chunker is not None
    assert counter is not None
    assert max_tokens > 0


def test_fixed_token_requires_model() -> None:
    with pytest.raises(ValueError, match="requires an embedding model"):
        build_chunker("fixed_token", chunk_size=256, chunk_overlap=32)


def test_fixed_token_gets_token_counter(monkeypatch) -> None:
    # THE blocker regression: with a model, build_chunker must hand make_chunker a
    # (non-None) token_counter — previously ingest_shard called make_chunker with
    # none, crashing with "fixed_token requires a token_counter". Patch the
    # resolvers + make_chunker so the test needs no HF download / real endpoint and
    # doesn't hit FixedTokenWindowChunker's HF-tokenizer requirement.
    class _DummyCounter:
        pass

    captured: dict = {}

    def fake_make_chunker(method, **kw):
        captured["method"] = method
        captured["token_counter"] = kw.get("token_counter")
        captured["max_tokens"] = kw.get("max_tokens")
        return object()

    monkeypatch.setattr(chunker_config, "make_token_counter", lambda *a, **k: _DummyCounter())
    monkeypatch.setattr(chunker_config, "resolve_max_tokens", lambda *a, **k: 512)
    monkeypatch.setattr(chunker_config, "make_chunker", fake_make_chunker)
    chunker, counter, max_tokens = build_chunker(
        "fixed_token", chunk_size=256, chunk_overlap=32, model="some-model"
    )
    assert chunker is not None
    assert captured["method"] == "fixed_token"
    assert captured["token_counter"] is not None  # a counter IS passed now
    assert isinstance(counter, _DummyCounter) and max_tokens == 512


def test_resolve_token_backend_rules() -> None:
    warns: list[str] = []
    # fixed_token forces hf even if asked for estimate...
    assert resolve_token_backend("fixed_token", "estimate", "m", warns.append) == "hf"
    assert warns  # ...and warns about the override
    # a hf/endpoint request with no model degrades to estimate
    assert resolve_token_backend("fixed", "hf", None, lambda m: None) == "estimate"
    # fixed_token with no model is a hard error
    with pytest.raises(ValueError):
        resolve_token_backend("fixed_token", "hf", None, lambda m: None)
