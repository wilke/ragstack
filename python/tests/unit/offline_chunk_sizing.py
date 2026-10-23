"""Keep the chunker factory's two external dependencies off the network in tests.

Since #647 every in-process chunker — not only ``fixed_token`` — resolves a token
counter (the collection model's HF tokenizer by default) and a token budget (GET
``{embedding endpoint}/v1/models``). Unpatched, a test that merely builds a
``fixed`` collection's chunker would download a tokenizer from the Hub and probe
the configured embedding URL — which on this host defaults to a LIVE service.

So this autouse fixture (registered by ``tests/unit/conftest.py`` and
``tests/api/conftest.py``) wraps the two names *as the factory sees them*
(``ragstack.ingestion.chunker_config``):

* ``make_token_counter("hf", ...)`` returns a whitespace HF-like counter (it has
  ``_tokenizer`` with an offset mapping, so ``fixed_token`` works) — UNLESS the
  test has patched ``HFTokenCounter._tokenizer`` itself, in which case the real
  factory runs, so the tests that deliberately exercise the real refusal still do.
* ``resolve_max_tokens`` runs for real when the window is explicit or the URL is
  the dead ``127.0.0.1:1`` pin, and otherwise answers a large fixed window
  instead of touching the network.

A test that wants other behaviour monkeypatches the same names; its patch is
applied after this one and wins. The HF counter cache is cleared around every
test so nothing a test built leaks into the next.
"""
from __future__ import annotations

import re

import pytest

from ragstack.ingestion import chunker_config
from ragstack.ingestion.tokenization import HFTokenCounter

_WORD_RE = re.compile(r"\S+")
_ORIGINAL_TOKENIZER = HFTokenCounter._tokenizer
_REAL_MAKE_TOKEN_COUNTER = chunker_config.make_token_counter
_REAL_RESOLVE_MAX_TOKENS = chunker_config.resolve_max_tokens

#: The window the guard reports for a non-dead endpoint: large, so it never
#: changes how an existing test's small documents are chunked.
OFFLINE_WINDOW_BUDGET = 8192


class _WhitespaceFastTokenizer:
    def __call__(self, text, return_offsets_mapping=False, add_special_tokens=True):
        return {"offset_mapping": [(m.start(), m.end()) for m in _WORD_RE.finditer(text)]}

    def encode(self, text, add_special_tokens=True):
        return _WORD_RE.findall(text)


class OfflineHFCounter:
    """HF-shaped counter: token = whitespace run; exposes ``_tokenizer``."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self._tok = _WhitespaceFastTokenizer()

    def _tokenizer(self):
        return self._tok

    def count(self, text: str) -> int:
        return len(_WORD_RE.findall(text))


def _make_token_counter(backend="hf", **kw):
    if backend == "hf" and HFTokenCounter._tokenizer is _ORIGINAL_TOKENIZER:
        if not kw.get("model"):
            raise ValueError("backend='hf' requires model")
        return OfflineHFCounter(kw.get("model"))
    return _REAL_MAKE_TOKEN_COUNTER(backend, **kw)


def _resolve_max_tokens(explicit, *, base_url, **kw):
    if explicit is not None or not base_url or base_url.startswith("http://127.0.0.1:1"):
        return _REAL_RESOLVE_MAX_TOKENS(explicit, base_url=base_url, **kw)
    return OFFLINE_WINDOW_BUDGET


@pytest.fixture(autouse=True)
def offline_chunk_sizing(monkeypatch):
    chunker_config.clear_token_counter_cache()
    monkeypatch.setattr(chunker_config, "make_token_counter", _make_token_counter)
    monkeypatch.setattr(chunker_config, "resolve_max_tokens", _resolve_max_tokens)
    yield
    chunker_config.clear_token_counter_cache()
