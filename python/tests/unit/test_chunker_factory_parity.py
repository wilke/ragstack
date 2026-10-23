"""#647: one chunker factory — the API path and the bulk path agree, and both cap.

Before #647 there were two answers for one collection spec. ``build_chunker``
(bulk / GoWe) resolved a token counter and a ``max_tokens`` budget for every
method; ``api/deps.py`` ``_chunker_for`` (in-process per-collection ingest) built a
counter only for ``fixed_token`` and passed no budget at all, so semantic chunks
went to the embedder uncapped and a document over ``max_breakpoint_sentences``
spans fell through ``SemanticChunker._oversize_fallback`` to ONE whole-document
chunk.

Everything here is offline: ``make_token_counter`` returns a whitespace HF-like
counter (it exposes ``_tokenizer`` with an offset map, so ``fixed_token`` and the
semantic oversize fallback really run), ``resolve_max_tokens`` answers for one
fake endpoint, and the semantic ``embed_fn`` is topic-sensitive (a constant
vector would make every distance 0 and fire no breakpoint, passing vacuously —
docs/plans/chunking-one-factory.md §8).
"""
from __future__ import annotations

import inspect
import re
import time

import pytest

from ragstack.api import deps
from ragstack.api.collections import CollectionEntry
from ragstack.ingestion import chunker_config
from ragstack.ingestion.chunker_config import ModelWindowUnavailable, build_chunker
from ragstack.ingestion.chunkers import CHUNK_METHODS
from ragstack.ingestion.tokenization import resolve_max_tokens as real_resolve_max_tokens
from ragstack.models import Document

_WORD_RE = re.compile(r"\S+")
ENDPOINT = "http://emb.parity.test"
MODEL = "acme/parity-embedder"
#: The window the fake endpoint reports, and the budget that implies (reserve 16).
WINDOW = 96
BUDGET = WINDOW - 16


class _FastTok:
    def __call__(self, text, return_offsets_mapping=False, add_special_tokens=True):
        return {"offset_mapping": [(m.start(), m.end()) for m in _WORD_RE.finditer(text)]}

    def encode(self, text, add_special_tokens=True):
        return _WORD_RE.findall(text)


class WordCounter:
    """Fake HF counter: one token per whitespace run."""

    def __init__(self) -> None:
        self._tok = _FastTok()

    def _tokenizer(self):
        return self._tok

    def count(self, text: str) -> int:
        return len(_WORD_RE.findall(text))


COUNTER = WordCounter()

_TOPICS = ("enzyme", "galaxy", "harbor", "violin")


def topic_embed_fn(texts):
    """Vector = topic-word frequencies: same topic -> near, topic change -> far."""
    out = []
    for t in texts:
        low = t.lower()
        v = [float(low.count(w)) for w in _TOPICS]
        out.append(v if any(v) else [1e-3] * len(_TOPICS))
    return out


def _topic_doc() -> Document:
    """~48 sentences in topic blocks, some long, so breakpoints AND the token cap
    both fire."""
    parts = []
    for block, topic in enumerate(_TOPICS * 3):
        for i in range(4):
            filler = " ".join(f"{topic}{j}" for j in range(30 if i == 0 else 6))
            parts.append(f"The {topic} sentence {block}.{i} talks about {topic} {filler}.")
    return Document(id="topics", content=" ".join(parts), metadata={})


def _huge_doc(n: int = 3200) -> Document:
    """More than the default max_breakpoint_sentences (3000) sentence spans."""
    return Document(
        id="huge",
        content=" ".join(f"Sentence {i} mentions the {_TOPICS[i % 4]} once." for i in range(n)),
        metadata={},
    )


@pytest.fixture
def fake_backends(monkeypatch):
    """The factory's two external dependencies, faked, with their calls recorded."""
    calls: dict = {"counter": [], "window": []}

    def fake_make_token_counter(backend="hf", **kw):
        calls["counter"].append((backend, kw.get("model")))
        return COUNTER

    def fake_resolve_max_tokens(explicit, *, base_url, api_key=None, default=4096, **kw):
        calls["window"].append((explicit, base_url))
        if explicit is not None:
            return max(1, explicit - 16)
        return BUDGET if base_url == ENDPOINT else default

    monkeypatch.setattr(chunker_config, "make_token_counter", fake_make_token_counter)
    monkeypatch.setattr(chunker_config, "resolve_max_tokens", fake_resolve_max_tokens)
    monkeypatch.setattr(deps.settings, "chunk_max_tokens", None)
    monkeypatch.setattr(deps.settings, "chunk_token_counter", "hf")
    return calls


def _entry(method: str, **over) -> CollectionEntry:
    kw: dict = {
        "id": "acme", "label": "acme", "collection": "physical_acme", "model": MODEL,
        "dim": 4, "chunk_method": method, "chunk_size": 40, "chunk_overlap": 8,
        "chunk_params": {}, "is_shared_surface": False, "retriever": object(),
        "vector_store": object(), "text_index": object(), "embedder": object(),
        "embedding_endpoints": [ENDPOINT],
    }
    if method == "fixed":
        kw |= {"chunk_size": 1200, "chunk_overlap": 100}  # chars: > BUDGET tokens, so the cap bites
    kw.update(over)
    return CollectionEntry(**kw)


def _bulk(method: str, entry: CollectionEntry):
    """The bulk plane's build of the same spec — as ``scripts/ingest_shard.py``
    calls it (its semantic tunables are the settings defaults when the spec's
    ``chunk_params`` is empty)."""
    chunker, _c, _m = build_chunker(
        method,
        chunk_size=entry.chunk_size,
        chunk_overlap=entry.chunk_overlap,
        model=entry.model,
        token_backend="hf",
        max_tokens=None,
        base_url=entry.embedding_endpoints[0],
        api_key=None,
        embed_fn=topic_embed_fn,
        buffer_size=deps.settings.chunk_buffer_size,
        breakpoint_percentile=deps.settings.chunk_breakpoint_percentile,
        min_chunk_length=deps.settings.chunk_min_length,
    )
    return chunker


#: A small oversize threshold for the parity test, so a 150-sentence document
#: takes ``SemanticChunker._oversize_fallback`` while the 48-sentence topic
#: document runs the breakpoint rule. Injected below ``build_chunker`` (into the
#: factory's ``make_chunker``), so BOTH builders get it and parity is untouched.
SMALL_MAX_BREAKPOINT_SENTENCES = 100


@pytest.fixture
def small_oversize_threshold(monkeypatch):
    real = chunker_config.make_chunker

    def make_chunker(method, **kw):
        kw["max_breakpoint_sentences"] = SMALL_MAX_BREAKPOINT_SENTENCES
        return real(method, **kw)

    monkeypatch.setattr(chunker_config, "make_chunker", make_chunker)


def _long_doc() -> Document:
    return _huge_doc(150)


def _spans(chunks):
    return [(c.id, c.start_char, c.end_char, c.content) for c in chunks]


@pytest.mark.parametrize("method", CHUNK_METHODS)
@pytest.mark.parametrize("doc_factory", [_topic_doc, _long_doc], ids=["topics", "oversize"])
def test_api_and_bulk_builders_chunk_identically_and_cap(
    fake_backends, small_oversize_threshold, method, doc_factory
):
    entry = _entry(method)
    doc = doc_factory()
    api = deps._chunker_for(entry, embed_fn=topic_embed_fn)
    bulk = _bulk(method, entry)
    api_chunks, bulk_chunks = api.chunk(doc), bulk.chunk(doc)

    assert api_chunks, "no chunks — the test exercises nothing"
    assert _spans(api_chunks) == _spans(bulk_chunks)
    # Both resolved the window from the COLLECTION's endpoint, with its model.
    assert (None, ENDPOINT) in fake_backends["window"]
    assert ("hf", MODEL) in fake_backends["counter"]
    # Every chunk fits the resolved budget, by the counter that sized it.
    over = [c.end_char - c.start_char for c in api_chunks if COUNTER.count(c.content) > BUDGET]
    assert not over, f"{method}: {len(over)} chunk(s) exceed max_tokens={BUDGET}"
    # Lossless tiling where the output does not overlap (a cheap sanity check that
    # the cap split, rather than dropped, text). The oversize fallback is a
    # sliding window WITH overlap, so it is excluded.
    if method == "sentence" or (method in ("semantic", "semantic_pooled")
                                and doc_factory is _topic_doc):
        assert "".join(c.content for c in api_chunks) == doc.content


@pytest.mark.parametrize("method", ["semantic", "semantic_pooled"])
def test_parity_docs_take_the_paths_they_name(fake_backends, small_oversize_threshold, method):
    """Guards the parity test against vacuity: the topic doc runs the breakpoint
    rule (it embeds, and splits on topic), the oversize doc takes the fallback
    (it embeds nothing)."""
    embedded: list[str] = []

    def spy(texts):
        embedded.extend(texts)
        return topic_embed_fn(texts)

    chunker = deps._chunker_for(_entry(method), embed_fn=spy)
    chunks = chunker.chunk(_topic_doc())
    assert embedded and len(chunks) > 1
    assert len({c.content.split()[1] for c in chunks}) > 1  # more than one topic leads
    embedded.clear()
    assert len(chunker.chunk(_long_doc())) > 1
    assert not embedded


@pytest.mark.parametrize("method", ["semantic", "semantic_pooled"])
def test_647_oversized_semantic_doc_is_not_one_whole_document_chunk(fake_backends, method):
    """The #647 comment scenario, on the in-process path: a semantic collection and
    a document with more than 3,000 sentence spans. It used to come back as ONE
    chunk holding the whole document (no counter -> no fallback window -> _emit
    without a budget)."""
    doc = _huge_doc()
    embedded: list[str] = []

    def spy_embed(texts):
        embedded.extend(texts)
        return topic_embed_fn(texts)

    chunks = deps._chunker_for(_entry(method), embed_fn=spy_embed).chunk(doc)
    assert len(chunks) > 1
    assert max(COUNTER.count(c.content) for c in chunks) <= BUDGET
    assert not embedded, "the oversize fallback must not breakpoint-embed"


# --------------------------------------------------------------------------- #
# Refuse, don't degrade
# --------------------------------------------------------------------------- #


def test_unreachable_endpoint_without_chunk_max_tokens_refuses(monkeypatch):
    """No explicit window and an endpoint that cannot report one: the build
    raises, naming the collection and the fix — it does not size the collection
    with resolve_max_tokens' silent 4096. Uses the REAL resolve_max_tokens
    against the dead 127.0.0.1:1 pin (connection refused, instantly)."""
    monkeypatch.setattr(chunker_config, "resolve_max_tokens", real_resolve_max_tokens)
    monkeypatch.setattr(deps.settings, "chunk_max_tokens", None)
    monkeypatch.setattr(deps.settings, "chunk_token_counter", "estimate")
    entry = _entry("fixed", embedding_endpoints=["http://127.0.0.1:1"])
    with pytest.raises(ModelWindowUnavailable) as exc:
        deps._chunker_for(entry)
    msg = str(exc.value)
    assert "'acme'" in msg and "CHUNK_MAX_TOKENS" in msg and "max_model_len" in msg
    assert "127.0.0.1" not in msg  # the internal URL stays in the log, not the 400 body
    assert isinstance(exc.value, ValueError)  # -> the ingest router's 400


def test_explicit_chunk_max_tokens_needs_no_endpoint(monkeypatch):
    monkeypatch.setattr(chunker_config, "resolve_max_tokens", real_resolve_max_tokens)
    monkeypatch.setattr(deps.settings, "chunk_max_tokens", 512)
    monkeypatch.setattr(deps.settings, "chunk_token_counter", "estimate")
    entry = _entry("fixed", embedding_endpoints=["http://127.0.0.1:1"])
    chunker = deps._chunker_for(entry)
    assert chunker.max_tokens == 512 - 16


def test_bulk_path_keeps_its_fallback_window(monkeypatch):
    """The refusal is the API's (require_window=True); the bulk tools' historical
    4096 fallback is unchanged — see the PR for why that is left alone here."""
    monkeypatch.setattr(chunker_config, "resolve_max_tokens", real_resolve_max_tokens)
    _c, _t, budget = build_chunker(
        "fixed", chunk_size=300, chunk_overlap=30, token_backend="estimate",
        base_url="http://127.0.0.1:1",
    )
    assert budget == 4096


# --------------------------------------------------------------------------- #
# The server-default chunker: same factory, and the boot property
# --------------------------------------------------------------------------- #


def test_default_settings_boot_loads_no_tokenizer_and_probes_nothing(monkeypatch):
    monkeypatch.setattr(deps.settings, "chunk_method", "fixed")
    monkeypatch.setattr(deps.settings, "_chunk_method_configured", False)
    monkeypatch.setattr(deps.settings, "chunk_max_tokens", None)
    called: list[str] = []
    monkeypatch.setattr(chunker_config, "make_token_counter",
                        lambda *a, **k: called.append("counter") or COUNTER)
    monkeypatch.setattr(chunker_config, "resolve_max_tokens",
                        lambda *a, **k: called.append("window") or BUDGET)
    chunker, bridge = deps._build_chunker()
    assert called == [] and bridge is None
    # ...and the first chunk resolves it, through the factory, capped.
    chunks = chunker.chunk(_topic_doc())
    assert called == ["counter", "window"]
    assert max(COUNTER.count(c.content) for c in chunks) <= BUDGET


def test_lazy_default_chunker_refuses_on_first_use_and_retries(monkeypatch):
    monkeypatch.setattr(deps.settings, "chunk_method", "fixed")
    monkeypatch.setattr(deps.settings, "_chunk_method_configured", False)
    monkeypatch.setattr(deps.settings, "chunk_max_tokens", None)
    monkeypatch.setattr(deps.settings, "chunk_token_counter", "estimate")
    answers = iter([0, BUDGET])  # 0 == "undetected" to the strict factory
    monkeypatch.setattr(chunker_config, "resolve_max_tokens", lambda *a, **k: next(answers))
    chunker, _ = deps._build_chunker()
    with pytest.raises(ModelWindowUnavailable):
        chunker.chunk(_topic_doc())
    assert chunker.chunk(_topic_doc())  # not cached as a failure


# --------------------------------------------------------------------------- #
# chunk_params
# --------------------------------------------------------------------------- #


def test_chunk_params_cannot_reach_the_factory_plumbing(fake_backends):
    """Free-form params must not steer the endpoint, key, model, or cost levers."""
    entry = _entry("semantic", chunk_params={
        "base_url": "http://evil.test", "api_key": "x", "model": "evil/model",
        "max_tokens": 10**9, "max_breakpoint_sentences": 10**9, "token_backend": "estimate",
    })
    chunker = deps._chunker_for(entry, embed_fn=topic_embed_fn)
    assert fake_backends["window"] == [(None, ENDPOINT)]
    assert fake_backends["counter"] == [("hf", MODEL)]
    assert chunker.max_tokens == BUDGET and chunker.max_breakpoint_sentences == 3000


def test_semantic_params_are_ignored_for_non_semantic_methods(fake_backends):
    """As in ingest_shard: a `fixed` collection may carry semantic junk."""
    assert deps._chunker_for(_entry("fixed", chunk_params={"buffer_size": "lots"}))


def test_unknown_params_are_logged_at_debug(fake_backends, caplog):
    import logging

    with caplog.at_level(logging.DEBUG, logger="ragstack.api.deps"):
        deps._chunker_for(_entry("fixed", chunk_params={"bogus_knob": 1}))
    assert any("bogus_knob" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# Cost: the HF counter is cached per model
# --------------------------------------------------------------------------- #


def test_hf_counter_is_loaded_once_per_model(monkeypatch):
    from ragstack.ingestion.tokenization import HFTokenCounter

    loads: list[str] = []

    def fake_load(self):
        loads.append(self.model)
        time.sleep(0.01)
        self._tok = _FastTok()
        return self._tok

    monkeypatch.setattr(HFTokenCounter, "_tokenizer", fake_load)
    monkeypatch.setattr(deps.settings, "chunk_max_tokens", 4096)
    chunker_config.clear_token_counter_cache()
    deps._chunker_for(_entry("fixed"))
    deps._chunker_for(_entry("fixed"))
    deps._chunker_for(_entry("fixed", model="other/model"))
    assert loads == [MODEL, "other/model"]


def test_failed_hf_load_is_not_cached(monkeypatch):
    from ragstack.ingestion.tokenization import HFTokenCounter, TokenCounterUnavailable

    outcomes = iter([OSError("hub hiccup"), None])

    def flaky(self):
        err = next(outcomes)
        if err is not None:
            raise err
        self._tok = _FastTok()
        return self._tok

    monkeypatch.setattr(HFTokenCounter, "_tokenizer", flaky)
    monkeypatch.setattr(deps.settings, "chunk_max_tokens", 4096)
    chunker_config.clear_token_counter_cache()
    with pytest.raises(TokenCounterUnavailable):
        deps._chunker_for(_entry("fixed"))
    assert deps._chunker_for(_entry("fixed"))


# --------------------------------------------------------------------------- #
# size_unit (#318 / #802) flows from chunk_params to the factory
# --------------------------------------------------------------------------- #

_FACTORY_HAS_SIZE_UNIT = "size_unit" in inspect.signature(build_chunker).parameters


def _spy_factory(monkeypatch):
    """A build_chunker that accepts size_unit (as #802's does) and records it."""
    seen: dict = {}

    def fake_build_chunker(method, *, size_unit="chars", **kw):
        seen.update(method=method, size_unit=size_unit)
        return object(), COUNTER, BUDGET

    monkeypatch.setattr(deps, "build_chunker", fake_build_chunker)
    return seen


@pytest.mark.parametrize("method", ["sentence", "words"])
def test_size_unit_reaches_a_factory_that_accepts_it(fake_backends, monkeypatch, method):
    seen = _spy_factory(monkeypatch)
    deps._chunker_for(_entry(method, chunk_params={"size_unit": "tokens"}))
    assert seen == {"method": method, "size_unit": "tokens"}


def test_absent_size_unit_leaves_the_factory_default(fake_backends, monkeypatch):
    seen = _spy_factory(monkeypatch)
    deps._chunker_for(_entry("sentence"))
    assert seen["size_unit"] == "chars"


@pytest.mark.parametrize(
    ("method", "unit"),
    [("sentence", "bytes"), ("words", 3), ("fixed", "tokens"), ("fixed", "chars"),
     ("semantic", "chars")],
)
def test_bad_size_unit_refuses_the_ingest(fake_backends, method, unit):
    with pytest.raises(ValueError, match="size_unit"):
        deps._chunker_for(_entry(method, chunk_params={"size_unit": unit}),
                          embed_fn=topic_embed_fn)


@pytest.mark.skipif(_FACTORY_HAS_SIZE_UNIT, reason="factory already takes size_unit (#802)")
def test_tokens_unit_is_refused_not_ignored_by_a_factory_without_it(fake_backends):
    """Before #802 lands the factory cannot count `size` in tokens; chunking such
    a collection by characters would contradict its identity, so it refuses."""
    with pytest.raises(ValueError, match="#802"):
        deps._chunker_for(_entry("sentence", chunk_params={"size_unit": "tokens"}))
    assert deps._chunker_for(_entry("sentence", chunk_params={"size_unit": "chars"}))


@pytest.mark.skipif(not _FACTORY_HAS_SIZE_UNIT, reason="needs #802's size_unit kwarg")
@pytest.mark.parametrize("method", ["sentence", "words"])
@pytest.mark.parametrize("doc_factory", [_topic_doc, _long_doc], ids=["topics", "oversize"])
def test_size_unit_tokens_parity(fake_backends, method, doc_factory):
    entry = _entry(method, chunk_params={"size_unit": "tokens"})
    doc = doc_factory()
    api = deps._chunker_for(entry)
    bulk, _c, _m = build_chunker(
        method, chunk_size=entry.chunk_size, chunk_overlap=entry.chunk_overlap,
        model=entry.model, token_backend="hf", max_tokens=None,
        base_url=entry.embedding_endpoints[0], size_unit="tokens",
    )
    assert _spans(api.chunk(doc)) == _spans(bulk.chunk(doc))
    assert max(COUNTER.count(c.content) for c in api.chunk(doc)) <= min(entry.chunk_size, BUDGET)
