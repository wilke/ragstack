"""``fixed_token`` cannot be asked for a window wider than the token budget.

``fixed_token``'s ``chunk_size`` IS its window, and ``make_chunker`` does not
thread ``max_tokens`` into it — so before this guard, ``chunk_size=8192`` against
a 4080-token embedder budget built a chunker whose every full window overflowed
the model context, with nothing said. It now raises, naming both numbers, and
``build_chunker`` (which always resolves ``max_tokens`` to the model window)
inherits the refusal.
"""
from __future__ import annotations

import pytest

from ragstack.ingestion import chunker_config
from ragstack.ingestion.chunker_config import build_chunker
from ragstack.ingestion.chunkers import FixedTokenWindowChunker, make_chunker
from tests.unit.test_chunkers_semantic import _HFLikeCounter


def test_window_wider_than_max_tokens_raises_naming_both():
    with pytest.raises(ValueError) as ei:
        make_chunker(
            "fixed_token", chunk_size=8192, chunk_overlap=64,
            max_tokens=4080, token_counter=_HFLikeCounter(),
        )
    msg = str(ei.value)
    assert "8192" in msg and "4080" in msg


@pytest.mark.parametrize("size", [1, 512, 4080])
def test_window_at_or_under_max_tokens_builds(size):
    ch = make_chunker(
        "fixed_token", chunk_size=size, chunk_overlap=0,
        max_tokens=4080, token_counter=_HFLikeCounter(),
    )
    assert isinstance(ch, FixedTokenWindowChunker)
    assert ch.chunk_size == size


def test_no_max_tokens_is_unchanged():
    """No budget given (the in-process API path today): any window is accepted,
    exactly as before."""
    ch = make_chunker(
        "fixed_token", chunk_size=8192, chunk_overlap=0, token_counter=_HFLikeCounter(),
    )
    assert ch.chunk_size == 8192


@pytest.fixture
def hf_like(monkeypatch):
    """build_chunker forces the hf backend for fixed_token; stand a fake in for the
    tokenizer download."""
    monkeypatch.setattr(chunker_config, "make_token_counter", lambda *_a, **_k: _HFLikeCounter())


def test_build_chunker_refuses_window_wider_than_model(hf_like):
    # --chunk-max-tokens 4096 → budget 4080 (16-token reserve).
    with pytest.raises(ValueError, match="exceeds max_tokens=4080"):
        build_chunker(
            "fixed_token", chunk_size=8192, chunk_overlap=0,
            model="some/model", max_tokens=4096,
        )


def test_build_chunker_accepts_window_within_model(hf_like):
    chunker, _counter, resolved = build_chunker(
        "fixed_token", chunk_size=512, chunk_overlap=64,
        model="some/model", max_tokens=4096,
    )
    assert resolved == 4080
    assert isinstance(chunker, FixedTokenWindowChunker)
