"""_build_chunker must honour every canonical chunk method.

Regression: the validation list was a hand-copied literal missing `fixed_token`
and `semantic_pooled`, so those silently fell back to char-based `fixed` before
the method-specific handling could run — a token-window request became char
chunking. We spy on `make_chunker` to isolate the guard from the tokenizer/embed
machinery each method otherwise pulls in."""
import pytest

from ragstack.api import deps
from ragstack.ingestion.chunkers import CHUNK_METHODS


def _spy_method(monkeypatch, method):
    seen = {}

    def fake_make_chunker(m, **kw):
        seen["method"] = m
        return object()

    monkeypatch.setattr(deps.settings, "chunk_method", method)
    monkeypatch.setattr(deps.settings, "embedding_model", "some/model")
    monkeypatch.setattr(deps, "make_chunker", fake_make_chunker)
    # semantic_pooled/semantic build a real embed bridge; neutralise it.
    monkeypatch.setattr(deps, "SyncEmbedBridge", lambda *_a, **_k: object())
    # fixed_token/token paths build a token counter; neutralise that too.
    monkeypatch.setattr(deps, "make_token_counter", lambda *_a, **_k: object(), raising=False)
    deps._build_chunker()
    return seen.get("method")


@pytest.mark.parametrize("method", ["fixed_token", "semantic_pooled"])
def test_canonical_methods_reach_make_chunker_unchanged(monkeypatch, method):
    assert _spy_method(monkeypatch, method) == method  # not silently "fixed"


def test_unknown_method_falls_back_to_fixed(monkeypatch):
    assert _spy_method(monkeypatch, "not-a-method") == "fixed"


def test_guard_uses_canonical_set():
    assert "fixed_token" in CHUNK_METHODS and "semantic_pooled" in CHUNK_METHODS
