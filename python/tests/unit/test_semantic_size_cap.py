"""The semantic size cap: ``chunk_size`` as a token bound, behind a recorded marker.

Before this, ``semantic`` / ``semantic_pooled`` used ``chunk_size`` only for the
oversized-document fallback; the only cap was the model window (~4,080 tokens for
SFR), so a collection created as "semantic, chunk_size 512" held chunks of 3,398
tokens. Now a collection whose ``chunk_params`` record ``size_cap="tokens"`` is
capped at ``chunk_size`` tokens, split by ``cap_split`` (greedy / balanced /
topic). A collection WITHOUT the marker is chunked exactly as on main 51d5622 —
asserted here against spans recorded from that commit
(``fixtures/semantic_cap_baseline_51d5622.json``).

Offline: a whitespace word counter, a fake window, deterministic embed fakes, and
a fixed tiling sentence splitter patched in (so the spans do not depend on whether
``nltk`` is installed).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ragstack.api import deps
from ragstack.ingestion import chunkers
from ragstack.ingestion.chunker_config import build_chunker, semantic_cap_from_params
from ragstack.ingestion.chunkers import CAP_SPLITS, SemanticChunker, make_chunker
from ragstack.models import Document
from tests.unit.test_chunker_factory_parity import (
    BUDGET,
    COUNTER,
    ENDPOINT,
    MODEL,
    _entry,
    _topic_doc,
    topic_embed_fn,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ingest_shard  # noqa: E402

SEMANTIC = ("semantic", "semantic_pooled")
BASELINE = json.loads(
    (Path(__file__).parent / "fixtures" / "semantic_cap_baseline_51d5622.json").read_text()
)["spans"]


def _tiling_spans(text: str) -> list[tuple[int, int]]:
    """Sentence = text up to and including '. ' + trailing whitespace. Tiles."""
    return [(m.start(), m.end()) for m in re.finditer(r".+?(?:\.\s+|$)", text, re.S)]


@pytest.fixture
def fake_backends(monkeypatch):
    """test_chunker_factory_parity's fake window + counter: the endpoint reports
    a window whose budget is BUDGET (80), and every counter is the word COUNTER."""
    from ragstack.ingestion import chunker_config

    def fake_make_token_counter(backend="hf", **kw):
        return COUNTER

    def fake_resolve_max_tokens(explicit, *, base_url, api_key=None, default=4096, **kw):
        if explicit is not None:
            return max(1, explicit - 16)
        return BUDGET if base_url == ENDPOINT else default

    monkeypatch.setattr(chunker_config, "make_token_counter", fake_make_token_counter)
    monkeypatch.setattr(chunker_config, "resolve_max_tokens", fake_resolve_max_tokens)
    monkeypatch.setattr(deps.settings, "chunk_max_tokens", None)
    monkeypatch.setattr(deps.settings, "chunk_token_counter", "hf")


@pytest.fixture(autouse=True)
def _fixed_segmenter(monkeypatch):
    monkeypatch.setattr(chunkers, "sentence_spans", _tiling_spans)


def _spans(chunks) -> list[list[int]]:
    return [[c.start_char, c.end_char] for c in chunks]


def _tiles(doc: Document, chunks) -> bool:
    return "".join(c.content for c in chunks) == doc.content and all(
        a.end_char == b.start_char for a, b in zip(chunks, chunks[1:], strict=False)
    )


def _sentence_edges(text: str) -> set[int]:
    return {e for _s, e in _tiling_spans(text)} | {0}


# --------------------------------------------------------------------------- #
# Backward compatibility: no marker -> exactly main's chunks
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method", SEMANTIC)
@pytest.mark.parametrize("max_tokens", [None, 80, 20])
def test_no_marker_chunks_exactly_as_main(method, max_tokens):
    chunker = make_chunker(
        method, chunk_size=512, chunk_overlap=64, embed_fn=topic_embed_fn,
        min_chunk_length=100, max_tokens=max_tokens,
        token_counter=COUNTER if max_tokens else None,
    )
    assert _spans(chunker.chunk(_topic_doc())) == BASELINE[f"{method}|{max_tokens}"]


@pytest.mark.parametrize("method", SEMANTIC)
def test_unmarked_collection_ignores_its_recorded_chunk_size(fake_backends, method):
    """The deployed case: a pre-marker semantic collection records chunk_size=512
    — LARGER than this window (80) — and was chunked uncapped. It must neither be
    refused nor capped: same chunks as main, through the API's own builder."""
    entry = _entry(method, chunk_size=512, chunk_overlap=64,
                   chunk_params={"min_chunk_length": 100})
    chunks = deps._chunker_for(entry, embed_fn=topic_embed_fn).chunk(_topic_doc())
    assert _spans(chunks) == BASELINE[f"{method}|{BUDGET}"]


@pytest.mark.parametrize("method", SEMANTIC)
def test_greedy_cap_at_the_window_is_the_legacy_chunking(method):
    """size_cap at exactly max_tokens with greedy = the old window-only rule."""
    chunker = make_chunker(
        method, chunk_size=80, chunk_overlap=0, embed_fn=topic_embed_fn,
        min_chunk_length=100, max_tokens=80, token_counter=COUNTER, size_cap="tokens",
    )
    assert _spans(chunker.chunk(_topic_doc())) == BASELINE[f"{method}|80"]


# --------------------------------------------------------------------------- #
# The cap
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method", SEMANTIC)
@pytest.mark.parametrize("cap_split", CAP_SPLITS)
def test_marker_caps_every_chunk_and_tiles(method, cap_split):
    doc = _topic_doc()
    chunker = make_chunker(
        method, chunk_size=20, chunk_overlap=0, embed_fn=topic_embed_fn,
        min_chunk_length=100, max_tokens=80, token_counter=COUNTER,
        size_cap="tokens", cap_split=cap_split,
    )
    chunks = chunker.chunk(doc)
    assert max(COUNTER.count(c.content) for c in chunks) <= 20
    assert _tiles(doc, chunks)
    # Non-vacuous: under the window alone (80) the same doc has bigger chunks.
    assert max(COUNTER.count(doc.content[a:b]) for a, b in BASELINE[f"{method}|80"]) > 20


@pytest.mark.parametrize("method", SEMANTIC)
@pytest.mark.parametrize("cap_split", CAP_SPLITS)
def test_never_cuts_inside_a_sentence_that_fits(method, cap_split):
    """Every cut is a sentence edge, except inside the one sentence over the cap."""
    text = " ".join(
        [f"Enzyme {i} a b c d e." for i in range(9)]
        + ["Galaxy " + " ".join(f"g{j}" for j in range(30)) + "."]  # 31 tokens > cap
        + [f"Galaxy {i} a b c d e." for i in range(9)]
    )
    doc = Document(id="d", content=text, metadata={})
    long_s, long_e = next((s, e) for s, e in _tiling_spans(text) if text[s:e].startswith("Galaxy g0"))
    chunker = make_chunker(
        method, chunk_size=20, chunk_overlap=0, embed_fn=topic_embed_fn,
        buffer_size=1, min_chunk_length=0, max_tokens=80, token_counter=COUNTER,
        size_cap="tokens", cap_split=cap_split,
    )
    chunks = chunker.chunk(doc)
    assert _tiles(doc, chunks) and max(COUNTER.count(c.content) for c in chunks) <= 20
    edges = _sentence_edges(text)
    inner = [c.start_char for c in chunks if c.start_char not in edges]
    assert inner and all(long_s < x < long_e for x in inner), inner


# --------------------------------------------------------------------------- #
# balanced vs greedy
# --------------------------------------------------------------------------- #


def _one_block_doc(n: int = 13) -> Document:
    # n four-token sentences -> 4n tokens in ONE block (constant embed: no breakpoint).
    return Document(id="b", content=" ".join(f"w{i} a b c." for i in range(n)), metadata={})


def _constant_embed(texts):
    return [[1.0, 0.0] for _ in texts]


def _capped(method, cap_split, cap=48, embed=_constant_embed, **kw):
    base = {
        "chunk_size": cap, "chunk_overlap": 0, "embed_fn": embed, "buffer_size": 1,
        "min_chunk_length": 0, "max_tokens": 80, "token_counter": COUNTER,
        "size_cap": "tokens", "cap_split": cap_split,
    }
    base.update(kw)
    return make_chunker(method, **base)


@pytest.mark.parametrize("method", SEMANTIC)
def test_balanced_leaves_no_tiny_tail_where_greedy_does(method):
    doc = _one_block_doc(13)  # 52 tokens at cap 48
    greedy = [COUNTER.count(c.content) for c in _capped(method, "greedy").chunk(doc)]
    balanced_chunks = _capped(method, "balanced").chunk(doc)
    balanced = [COUNTER.count(c.content) for c in balanced_chunks]
    assert greedy == [48, 4]  # the defect balanced exists for
    assert len(balanced) == 2 and max(balanced) <= 48
    assert min(balanced) >= 24  # 28 + 24: within one sentence of an even split
    assert _tiles(doc, balanced_chunks)


def test_balanced_raises_k_when_k_pieces_cannot_fit():
    """Three 20-token sentences at cap 30: ceil(60/30)=2 pieces cannot fit at
    sentence edges (40 + 20), so balanced uses 3."""
    text = " ".join(f"s{i} " + " ".join(["x"] * 19) + "." for i in range(3))
    doc = Document(id="k", content=text, metadata={})
    chunks = _capped("semantic", "balanced", cap=30).chunk(doc)
    assert [COUNTER.count(c.content) for c in chunks] == [20, 20, 20]


def test_balanced_cuts_unit():
    from ragstack.ingestion.chunkers import _balanced_cuts

    assert _balanced_cuts([4] * 13, 2, 48) == [0, 6, 13]  # 24 + 28; ties -> earliest cut
    assert _balanced_cuts([20, 20, 20], 2, 30) is None
    assert _balanced_cuts([1, 1, 10, 1, 1], 3, 12) == [0, 2, 3, 5]


# --------------------------------------------------------------------------- #
# topic
# --------------------------------------------------------------------------- #

#: Ten sentences; the embedding of each is chosen so that the consecutive
#: distances are: tiny everywhere, MEDIUM before s3, LARGEST before s7.
_TOPIC_VECS = [[1.0, 0.0]] * 3 + [[0.95, 0.31]] * 4 + [[0.3, 0.95]] * 3


def _topic_text() -> str:
    return " ".join(f"s{i} a b c." for i in range(10))  # 4 tokens each, 40 in all


def _vec_embed(calls):
    def embed(texts):
        calls.append(list(texts))
        return [list(_TOPIC_VECS[int(t.split()[0][1:])]) for t in texts]
    return embed


@pytest.mark.parametrize("method", SEMANTIC)
def test_topic_cuts_at_the_largest_internal_distance(method):
    doc = Document(id="t", content=_topic_text(), metadata={})
    spans = _tiling_spans(doc.content)
    calls: list = []
    # buffer_size=0: each buffer is its own sentence, so distances follow _TOPIC_VECS;
    # percentile 100: nothing is above the max, so the whole doc is ONE block.
    kw = {"buffer_size": 0, "breakpoint_percentile_threshold": 100.0}
    topic = _capped(method, "topic", cap=36, embed=_vec_embed(calls), **kw).chunk(doc)
    greedy = _capped(method, "greedy", cap=36, embed=_vec_embed([]), **kw).chunk(doc)
    assert [c.start_char for c in topic] == [0, spans[7][0]]  # 28 + 12, at the topic edge
    assert [c.start_char for c in greedy] == [0, spans[9][0]]  # 36 + 4: control
    assert _tiles(doc, topic)


@pytest.mark.parametrize("method", SEMANTIC)
def test_topic_recurses_to_the_next_strongest_breakpoint(method):
    doc = Document(id="t", content=_topic_text(), metadata={})
    spans = _tiling_spans(doc.content)
    kw = {"buffer_size": 0, "breakpoint_percentile_threshold": 100.0}
    chunks = _capped(method, "topic", cap=20, embed=_vec_embed([]), **kw).chunk(doc)
    # 0..7 (28) still over 20 -> its strongest internal edge is before s3.
    assert [c.start_char for c in chunks] == [0, spans[3][0], spans[7][0]]
    assert max(COUNTER.count(c.content) for c in chunks) <= 20


@pytest.mark.parametrize("method", SEMANTIC)
def test_topic_embeds_nothing_extra(method):
    doc = Document(id="t", content=_topic_text(), metadata={})
    kw = {"buffer_size": 0, "breakpoint_percentile_threshold": 100.0}
    topic_calls: list = []
    uncapped_calls: list = []
    _capped(method, "topic", cap=20, embed=_vec_embed(topic_calls), **kw).chunk(doc)
    make_chunker(method, chunk_size=20, embed_fn=_vec_embed(uncapped_calls),
                 min_chunk_length=0, max_tokens=80, token_counter=COUNTER, **kw).chunk(doc)
    assert topic_calls == uncapped_calls and len(topic_calls) == 1


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method", SEMANTIC)
def test_cap_above_the_window_is_refused_not_clamped(method):
    with pytest.raises(ValueError, match=r"chunk_size=81 .*max_tokens=80"):
        _capped(method, "greedy", cap=81)
    _capped(method, "greedy", cap=80)  # the boundary itself is fine


def test_cap_split_without_a_cap_is_refused():
    with pytest.raises(ValueError, match="needs a size cap"):
        make_chunker("semantic", embed_fn=_constant_embed, cap_split="balanced")
    with pytest.raises(ValueError, match="cap_split"):
        semantic_cap_from_params("semantic", {"cap_split": "topic"}, recorded_size=512)


def test_unknown_values_are_refused():
    with pytest.raises(ValueError, match="cap_split"):
        make_chunker("semantic", embed_fn=_constant_embed, size_cap="tokens",
                     token_counter=COUNTER, cap_split="smart")
    with pytest.raises(ValueError, match="size_cap"):
        make_chunker("semantic", embed_fn=_constant_embed, size_cap="chars")
    with pytest.raises(ValueError, match="size_cap"):
        semantic_cap_from_params("semantic", {"size_cap": "chars"}, recorded_size=512)
    with pytest.raises(ValueError, match="cap_split"):
        semantic_cap_from_params("semantic", {"size_cap": "tokens", "cap_split": 3},
                                 recorded_size=512)


def test_cap_needs_a_counter_and_a_recorded_size():
    with pytest.raises(ValueError, match="token_counter"):
        make_chunker("semantic", embed_fn=_constant_embed, size_cap="tokens")
    with pytest.raises(ValueError, match="records no chunk_size"):
        semantic_cap_from_params("semantic", {"size_cap": "tokens"}, recorded_size=None)


def test_cap_params_apply_only_to_semantic():
    with pytest.raises(ValueError, match="only to the semantic"):
        make_chunker("sentence", size_cap="tokens", token_counter=COUNTER)
    # An existing non-semantic entry carrying the keys is not refused at ingest.
    assert semantic_cap_from_params("fixed", {"size_cap": "x"}, recorded_size=1) == {}
    assert semantic_cap_from_params("semantic", {}, recorded_size=512) == {}
    assert semantic_cap_from_params("semantic", {"size_cap": "tokens"}, recorded_size=512) == {
        "size_cap": "tokens", "cap_split": "greedy",
    }


@pytest.mark.parametrize("method", SEMANTIC)
def test_api_refuses_a_recorded_cap_above_the_window(fake_backends, method):
    entry = _entry(method, chunk_size=BUDGET + 1, chunk_params={"size_cap": "tokens"})
    with pytest.raises(ValueError, match=rf"collection 'acme'.*max_tokens={BUDGET}"):
        deps._chunker_for(entry, embed_fn=topic_embed_fn)


# --------------------------------------------------------------------------- #
# One spec, one chunking: API builder == bulk (ingest_shard) builder
# --------------------------------------------------------------------------- #


def _shard_args(method: str, size: int):
    return ingest_shard.parse_args([
        "x.jsonl", "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1",
        "--chunk-method", method, "--chunk-size", str(size), "--chunk-overlap", "8",
        "--embedding-model", MODEL, "--embedding-url", ENDPOINT,
    ])


@pytest.mark.parametrize("method", SEMANTIC)
@pytest.mark.parametrize("cap_split", CAP_SPLITS)
def test_api_and_shard_paths_chunk_identically(fake_backends, method, cap_split):
    params = {"size_cap": "tokens", "cap_split": cap_split, "buffer_size": 1,
              "breakpoint_percentile_threshold": 70.0, "min_chunk_length": 100}
    entry = _entry(method, chunk_size=20, chunk_params=params)
    spec = SimpleNamespace(chunk_params=params, chunk_size=20)
    doc = _topic_doc()
    api = deps._chunker_for(entry, embed_fn=topic_embed_fn)
    bulk = ingest_shard._build_chunker(_shard_args(method, 20), spec, embed_fn=topic_embed_fn)
    assert isinstance(api, SemanticChunker) and api.size_cap_tokens == 20
    api_chunks = api.chunk(doc)
    assert _spans(api_chunks) == _spans(bulk.chunk(doc))
    assert max(COUNTER.count(c.content) for c in api_chunks) <= 20
    # And through build_chunker directly with the shared reader.
    direct, _c, _m = build_chunker(
        method, chunk_size=20, chunk_overlap=8, model=MODEL, base_url=ENDPOINT,
        embed_fn=topic_embed_fn, buffer_size=1, breakpoint_percentile=70.0,
        min_chunk_length=100, **semantic_cap_from_params(method, params, recorded_size=20),
    )
    assert _spans(direct.chunk(doc)) == _spans(api_chunks)


def test_shard_refuses_a_bad_recorded_cap():
    spec = SimpleNamespace(chunk_params={"size_cap": "tokens", "cap_split": "smart"},
                           chunk_size=20)
    with pytest.raises(SystemExit, match="cap_split"):
        ingest_shard._build_chunker(_shard_args("semantic", 20), spec, embed_fn=topic_embed_fn)


# --------------------------------------------------------------------------- #
# ingest_jsonl.py: the entry decides; flags only without one
# --------------------------------------------------------------------------- #


def _jsonl_args(method="semantic", cap=None, split=None, size=512):
    return SimpleNamespace(chunk_method=method, chunk_size=size,
                           semantic_size_cap=cap, semantic_cap_split=split)


def _target(params, size=512):
    return SimpleNamespace(collection_id="lib",
                           spec=SimpleNamespace(chunk_params=params, chunk_size=size))


def test_ingest_jsonl_cap_resolution():
    import ingest_jsonl

    resolve = ingest_jsonl._semantic_cap_kwargs
    assert resolve(_jsonl_args(), None) == {}
    assert resolve(_jsonl_args(cap="tokens", split="topic"), None) == {
        "size_cap": "tokens", "cap_split": "topic"}
    # The entry is the source; a flag that agrees is fine, one that contradicts exits 2.
    entry = _target({"size_cap": "tokens", "cap_split": "balanced"})
    assert resolve(_jsonl_args(), entry) == {"size_cap": "tokens", "cap_split": "balanced"}
    assert resolve(_jsonl_args(split="balanced"), entry)["cap_split"] == "balanced"
    for bad in (_jsonl_args(split="greedy"), _jsonl_args(cap="tokens", split="topic")):
        with pytest.raises(SystemExit) as e:
            resolve(bad, entry)
        assert e.value.code == 2
    # An unmarked (pre-cap) entry cannot be capped from the command line.
    with pytest.raises(SystemExit):
        resolve(_jsonl_args(cap="tokens"), _target({}))
    assert resolve(_jsonl_args(), _target({})) == {}
    with pytest.raises(SystemExit):
        resolve(_jsonl_args(method="fixed_token", cap="tokens"), None)
    with pytest.raises(SystemExit):
        resolve(_jsonl_args(split="topic"), None)  # a split with no cap


class _SeamCounter:
    """Words, plus 3 tokens for every sentence seam ('. ' followed by text): a
    joined chunk counts MORE than the sum of its sentences counted alone — the
    case where a partition chosen on per-sentence sums must be re-checked."""

    def count(self, text: str) -> int:
        return len(text.split()) + 3 * len(re.findall(r"\.\s+\S", text))


def test_balanced_verifies_pieces_on_the_joined_text():
    long = "L " + " ".join(["x"] * 19) + "."  # 20 tokens
    shorts = [f"s{i} y." for i in range(10)]  # 2 tokens each; 10 of them join to 47
    text = " ".join([long, *shorts])
    doc = Document(id="seam", content=text, metadata={})
    counter = _SeamCounter()
    chunks = _capped("semantic", "balanced", cap=40, token_counter=counter).chunk(doc)
    # By per-sentence sums, [long | 10 shorts] = 20 / 20 is the best 2-split; joined,
    # the second piece is 47 > 40, so balanced must go to 3 pieces.
    assert max(counter.count(c.content) for c in chunks) <= 40
    assert len(chunks) == 3 and _tiles(doc, chunks)


def test_balanced_clamps_k_when_seams_inflate_the_joined_count():
    """Three 3-token units under cap 3, where the seams make the block count 15:
    ceil(15/3) = 5 > n = 3 pieces. k must be clamped to n (an unclamped k skipped
    the loop and raised an AssertionError, a 500 at ingest); one unit per piece."""
    text = "a b c. d e f. g h i."
    doc = Document(id="clamp", content=text, metadata={})
    counter = _SeamCounter()
    assert counter.count(text) == 15
    chunks = _capped("semantic", "balanced", cap=3, token_counter=counter).chunk(doc)
    assert [c.content.strip() for c in chunks] == ["a b c.", "d e f.", "g h i."]
    assert _tiles(doc, chunks) and max(counter.count(c.content) for c in chunks) <= 3


def test_balanced_falls_back_to_one_unit_per_piece_when_nothing_else_fits():
    """Four 1-token units, cap 4, joined 4 + 3*3 = 13 -> k starts at 4 = n."""
    text = "a. b. c. d."
    doc = Document(id="fb", content=text, metadata={})
    counter = _SeamCounter()
    chunks = _capped("semantic", "balanced", cap=4, token_counter=counter).chunk(doc)
    assert max(counter.count(c.content) for c in chunks) <= 4 and _tiles(doc, chunks)


@pytest.mark.parametrize("method", SEMANTIC)
def test_api_refuses_a_marker_without_a_recorded_size(fake_backends, method):
    entry = _entry(method, chunk_size=None, chunk_params={"size_cap": "tokens"})
    with pytest.raises(ValueError, match="records no chunk_size"):
        deps._chunker_for(entry, embed_fn=topic_embed_fn)


@pytest.mark.parametrize("method", SEMANTIC)
def test_topic_ties_go_to_the_earliest_cut(method):
    """Equal distances at two gaps that balance the block equally (one 4-token
    sentence either side of a 4-sentence middle): the earliest wins."""
    # s0 | s1 s2 s3 s4 | s5 : vectors change at 0->1 and 4->5 by the same amount,
    # and cutting before s1 or before s5 is equally (im)balanced by characters.
    vecs = [[1.0, 0.0]] + [[0.0, 1.0]] * 4 + [[1.0, 0.0]]

    def embed(texts):
        return [list(vecs[int(t.split()[0][1:])]) for t in texts]

    # Trailing space: every sentence is 10 chars, so both cuts are 10 | 50 apart.
    doc = Document(id="tie", content="".join(f"s{i} a b c. " for i in range(6)), metadata={})
    spans = _tiling_spans(doc.content)
    kw = {"buffer_size": 0, "breakpoint_percentile_threshold": 100.0}
    chunks = _capped(method, "topic", cap=20, embed=embed, **kw).chunk(doc)
    assert chunks[1].start_char == spans[1][0], [c.start_char for c in chunks]


def test_shard_logs_the_cap_it_applies(fake_backends, capsys):
    spec = SimpleNamespace(chunk_params={"size_cap": "tokens", "cap_split": "topic"},
                           chunk_size=20)
    ingest_shard._build_chunker(_shard_args("semantic", 20), spec, embed_fn=topic_embed_fn)
    assert "size_cap=tokens chunk_size=20 cap_split=topic" in capsys.readouterr().err
    ingest_shard._build_chunker(_shard_args("semantic", 20),
                                SimpleNamespace(chunk_params={}, chunk_size=20),
                                embed_fn=topic_embed_fn)
    assert "no size_cap recorded" in capsys.readouterr().err
