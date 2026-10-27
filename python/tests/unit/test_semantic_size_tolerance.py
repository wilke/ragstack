"""The OPT-IN size tolerance and small-piece merge for semantic chunking (#831).

Under a recorded ``chunk_params.size_tolerance`` a size-capped semantic
collection treats ``chunk_size`` as a TARGET: chunks may reach the ceiling
``min(floor(chunk_size * (1 + size_tolerance)), window)``; only a block over the
ceiling is split (by ``cap_split``, aimed at the target); and a piece under
``min_piece`` tokens joins the neighbour across the weaker topic boundary when
the result fits the ceiling. Without the key a collection is chunked exactly as
under #829's strict cap.

Offline, with the same fakes as ``test_semantic_size_cap`` (a word counter, a
fake window, deterministic embed fakes and a tiling sentence splitter).
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from ragstack.api import deps
from ragstack.ingestion import chunkers
from ragstack.ingestion.chunker_config import build_chunker, semantic_cap_from_params
from ragstack.ingestion.chunkers import (
    CAP_SPLITS,
    SemanticChunker,
    _merge_small_pieces,
    make_chunker,
    semantic_ceiling,
    semantic_chunking_provenance,
)
from ragstack.models import Document
from tests.unit.test_chunker_factory_parity import (
    COUNTER,
    ENDPOINT,
    MODEL,
    _entry,
    _topic_doc,
    topic_embed_fn,
)
from tests.unit.test_semantic_size_cap import (  # noqa: F401 — the fixtures, re-exported
    BASELINE,
    _fixed_segmenter,  # noqa: F401
    _shard_args,
    _spans,
    _tiles,
    _tiling_spans,
    ingest_shard,
)
from tests.unit.test_semantic_size_cap import fake_backends as fake_backends  # noqa: F401

SEMANTIC = ("semantic", "semantic_pooled")


def _constant_embed(texts):
    return [[1.0, 0.0] for _ in texts]


def _make(method, cap_split="greedy", *, target=48, tol=0.25, min_piece=None,
          embed=_constant_embed, max_tokens=80, **kw):
    base = {
        "chunk_size": target, "chunk_overlap": 0, "embed_fn": embed, "buffer_size": 1,
        "min_chunk_length": 0, "max_tokens": max_tokens, "token_counter": COUNTER,
        "size_cap": "tokens", "cap_split": cap_split, "size_tolerance": tol,
        "min_piece": min_piece,
    }
    base.update(kw)
    return make_chunker(method, **base)


def _counts(chunks) -> list[int]:
    return [COUNTER.count(c.content) for c in chunks]


def _one_block_doc(n: int) -> Document:
    # n four-token sentences, one block under a constant embedding (no breakpoint).
    return Document(id="b", content=" ".join(f"w{i} a b c." for i in range(n)), metadata={})


def _varied_doc() -> Document:
    """Topic blocks with sentences from 2 to 75 words: some fit the target, some
    only the ceiling, some neither — every branch of the split fires."""
    lengths = [3, 9, 52, 4, 2, 75, 6, 7, 30, 58, 3, 11, 2, 40, 5, 66, 2, 2, 9, 21]
    topics = ("enzyme", "galaxy", "harbor", "violin")
    parts = []
    for i, n in enumerate(lengths):
        topic = topics[(i // 3) % 4]
        parts.append(f"S{i} {topic} " + " ".join(f"{topic}{j}" for j in range(max(0, n - 2))) + ".")
    return Document(id="varied", content=" ".join(parts), metadata={})


# --------------------------------------------------------------------------- #
# The ceiling
# --------------------------------------------------------------------------- #


def test_ceiling_is_the_tolerance_clamped_to_the_window():
    assert semantic_ceiling(512, 0.25, 4080) == 640
    assert semantic_ceiling(512, 0.25, 600) == 600  # never above the window
    assert semantic_ceiling(100, 0.1, None) == 110  # 110.00000000000001, not 109
    assert semantic_ceiling(512, 0.0, 4080) == 512
    assert _make("semantic", target=80, max_tokens=80).ceiling_tokens == 80


# --------------------------------------------------------------------------- #
# Invariants: <= ceiling, <= window, tiling, sentence cuts
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method", SEMANTIC)
@pytest.mark.parametrize("cap_split", CAP_SPLITS)
@pytest.mark.parametrize(("target", "tol", "window"), [(20, 0.25, 80), (48, 0.25, 80),
                                                       (48, 0.5, 60), (80, 0.25, 80)])
@pytest.mark.parametrize("doc_fn", [_topic_doc, _varied_doc])
def test_every_chunk_fits_the_ceiling_and_the_window_and_tiles(
    method, cap_split, target, tol, window, doc_fn
):
    doc = doc_fn()
    chunker = _make(method, cap_split, target=target, tol=tol, max_tokens=window,
                    embed=topic_embed_fn, buffer_size=1)
    ceiling = chunker.ceiling_tokens
    assert ceiling == min(int(target * (1 + tol)), window)
    chunks = chunker.chunk(doc)
    assert max(_counts(chunks)) <= ceiling <= window
    assert _tiles(doc, chunks)


@pytest.mark.parametrize("method", SEMANTIC)
@pytest.mark.parametrize("cap_split", CAP_SPLITS)
def test_no_cut_inside_a_sentence_that_fits_the_ceiling(method, cap_split):
    """A 55-token sentence (over the target 48, under the ceiling 60) is never
    cut; a 70-token one (over the ceiling) is the only place a cut may fall
    inside a sentence."""
    fits = "Mid " + " ".join(f"m{j}" for j in range(54)) + "."  # 55 tokens
    over = "Big " + " ".join(f"g{j}" for j in range(69)) + "."  # 70 tokens
    text = " ".join([f"s{i} a b c." for i in range(6)] + [fits]
                    + [f"t{i} a b c." for i in range(6)] + [over]
                    + [f"u{i} a b c." for i in range(6)])
    doc = Document(id="d", content=text, metadata={})
    chunks = _make(method, cap_split).chunk(doc)
    assert _tiles(doc, chunks) and max(_counts(chunks)) <= 60
    edges = {e for _s, e in _tiling_spans(text)} | {0}
    over_s = text.index(over)
    inner = [c.start_char for c in chunks if c.start_char not in edges]
    assert inner and all(over_s < x < over_s + len(over) for x in inner), inner
    # The 55-token sentence lies whole inside one chunk.
    fits_s = text.index(fits)
    assert any(c.start_char <= fits_s and fits_s + len(fits) <= c.end_char for c in chunks)


# --------------------------------------------------------------------------- #
# Split only over the ceiling, aimed at the target
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method", SEMANTIC)
@pytest.mark.parametrize("cap_split", CAP_SPLITS)
def test_a_block_between_target_and_ceiling_stays_whole(method, cap_split):
    doc = _one_block_doc(13)  # 52 tokens: target 48 < 52 <= ceiling 60
    strict = make_chunker(method, chunk_size=48, chunk_overlap=0, embed_fn=_constant_embed,
                          buffer_size=1, min_chunk_length=0, max_tokens=80,
                          token_counter=COUNTER, size_cap="tokens", cap_split=cap_split)
    assert len(strict.chunk(doc)) == 2  # control: the strict cap splits it
    assert _counts(_make(method, cap_split).chunk(doc)) == [52]


@pytest.mark.parametrize("method", SEMANTIC)
def test_a_block_over_the_ceiling_is_split_toward_the_target(method):
    """The issue's example scaled by 1/8: target 64 (512), ceiling 80 (640); an
    88-token block (700) becomes about 2 x 44 under balanced — 'about 2 x 350'."""
    doc = _one_block_doc(22)  # 88 tokens
    balanced = _counts(_make(method, "balanced", target=64, max_tokens=96).chunk(doc))
    assert balanced == [44, 44]
    greedy = _counts(_make(method, "greedy", target=64, max_tokens=96).chunk(doc))
    assert greedy == [64, 24]  # 24 >= min_piece 16: left as is


@pytest.mark.parametrize("method", SEMANTIC)
@pytest.mark.parametrize("cap_split", CAP_SPLITS)
def test_zero_tolerance_without_merge_is_the_strict_split(method, cap_split):
    """With tolerance 0 and min_piece 0 the ceiling IS the target and nothing
    merges, so (with no sentence over the target) the split is #829's own."""
    kw = {"chunk_size": 20, "chunk_overlap": 0, "embed_fn": topic_embed_fn,
          "min_chunk_length": 100, "max_tokens": 80, "token_counter": COUNTER,
          "size_cap": "tokens", "cap_split": cap_split}
    topics = ("enzyme", "galaxy", "harbor", "violin")
    short = Document(id="short", metadata={}, content=" ".join(
        f"S{i} {topics[(i // 5) % 4]} " + " ".join(f"w{j}" for j in range(3 + (i * 7) % 15)) + "."
        for i in range(40)))
    assert max(COUNTER.count(short.content[s:e]) for s, e in _tiling_spans(short.content)) <= 20
    strict = make_chunker(method, **kw).chunk(short)
    zero = make_chunker(method, size_tolerance=0.0, min_piece=0, **kw).chunk(short)
    assert _spans(zero) == _spans(strict)


# --------------------------------------------------------------------------- #
# The small-piece merge
# --------------------------------------------------------------------------- #

#: Blocks A (s0..sA-1), B (one sentence), C (the rest); the embedding changes at
#: A|B and B|C only, so those are the two breakpoints, with distances set by B.
def _abc_doc(n_a: int = 6, n_c: int = 6) -> Document:
    n = n_a + 1 + n_c
    return Document(id="abc", content=" ".join(f"s{i} a b c." for i in range(n)), metadata={})


def _abc_embed(n_a: int, vec_b):
    def embed(texts):
        out = []
        for t in texts:
            i = int(t.split()[0][1:])
            out.append([1.0, 0.0] if i < n_a else list(vec_b) if i == n_a else [0.0, 1.0])
        return out
    return embed


#: B leans to A: distance A|B = 0.2 < B|C = 0.4, so A|B is the WEAKER boundary.
NEAR_A = (0.8, 0.6)
#: B leans to C: A|B = 0.4 > B|C = 0.2.
NEAR_C = (0.6, 0.8)


def _abc_chunker(method, vec_b, n_a=6, *, target=24, tol=0.25, min_piece=6, embed=None, **kw):
    return _make(method, "greedy", target=target, tol=tol, min_piece=min_piece,
                 embed=embed or _abc_embed(n_a, vec_b), buffer_size=0,
                 breakpoint_percentile_threshold=80.0, **kw)


@pytest.mark.parametrize("method", SEMANTIC)
def test_a_small_piece_merges_across_the_weaker_boundary(method):
    doc = _abc_doc()
    spans = _tiling_spans(doc.content)
    strict = make_chunker(method, chunk_size=24, chunk_overlap=0, embed_fn=_abc_embed(6, NEAR_A),
                          buffer_size=0, min_chunk_length=0, max_tokens=80,
                          token_counter=COUNTER, size_cap="tokens")
    assert _counts(strict.chunk(doc)) == [24, 4, 24]  # control: B stands alone
    left = _abc_chunker(method, NEAR_A).chunk(doc)
    assert _counts(left) == [28, 24] and left[1].start_char == spans[7][0]
    right = _abc_chunker(method, NEAR_C).chunk(doc)
    assert _counts(right) == [24, 28] and right[1].start_char == spans[6][0]
    assert _tiles(doc, left) and _tiles(doc, right)


@pytest.mark.parametrize("method", SEMANTIC)
def test_the_other_neighbour_when_the_weaker_side_would_overflow(method):
    """A is 28 tokens: A+B = 32 > ceiling 30, so B goes right although A|B is weaker."""
    doc = _abc_doc(n_a=7, n_c=5)
    chunks = _abc_chunker(method, NEAR_A, n_a=7).chunk(doc)
    assert _counts(chunks) == [28, 24]


@pytest.mark.parametrize("method", SEMANTIC)
def test_a_small_piece_stays_when_neither_neighbour_fits(method):
    doc = _abc_doc(n_a=7, n_c=7)  # 28 | 4 | 28: both merges would be 32 > 30
    assert _counts(_abc_chunker(method, NEAR_A, n_a=7).chunk(doc)) == [28, 4, 28]


@pytest.mark.parametrize("method", SEMANTIC)
def test_min_piece_zero_never_merges(method):
    doc = _abc_doc()
    assert _counts(_abc_chunker(method, NEAR_A, min_piece=0).chunk(doc)) == [24, 4, 24]


@pytest.mark.parametrize("method", SEMANTIC)
def test_the_merge_embeds_nothing_extra(method):
    calls_tol: list = []
    calls_strict: list = []

    def rec(calls, inner):
        def fn(texts):
            calls.append(list(texts))
            return inner(texts)
        return fn

    doc = _abc_doc()
    _abc_chunker(method, NEAR_A, embed=rec(calls_tol, _abc_embed(6, NEAR_A))).chunk(doc)
    make_chunker(method, chunk_size=24, chunk_overlap=0, embed_fn=rec(calls_strict, _abc_embed(6, NEAR_A)),
                 buffer_size=0, min_chunk_length=0, max_tokens=80, token_counter=COUNTER,
                 size_cap="tokens").chunk(doc)
    assert calls_tol == calls_strict and len(calls_tol) == 1


def _strength(table):
    return lambda offset: table.get(offset, float("-inf"))


def test_merge_unit_weaker_side_then_fallback_then_stay():
    text = "x" * 100
    count = SimpleNamespace(count=lambda s: len(s))  # 1 token per char
    ranges = [(0, 40), (40, 45), (45, 85), (85, 100)]
    weak_left = _strength({40: 0.1, 45: 0.9, 85: 0.5})
    assert _merge_small_pieces(text, ranges, min_piece=10, ceiling=50, token_counter=count,
                               strength=weak_left) == [(0, 45), (45, 85), (85, 100)]
    weak_right = _strength({40: 0.9, 45: 0.1, 85: 0.5})
    # (40,45) -> right (45 tokens); (85,100) = 15 >= 10 stays.
    assert _merge_small_pieces(text, ranges, min_piece=10, ceiling=50, token_counter=count,
                               strength=weak_right) == [(0, 40), (40, 85), (85, 100)]
    # Neither side fits a ceiling of 44: unchanged.
    assert _merge_small_pieces(text, ranges, min_piece=10, ceiling=44, token_counter=count,
                               strength=weak_left) == ranges


def test_merge_unit_coalesces_a_run_of_small_pieces():
    text = "y" * 30
    count = SimpleNamespace(count=lambda s: len(s))
    ranges = [(0, 3), (3, 6), (6, 9), (9, 30)]
    flat = _strength({3: 0.5, 6: 0.5, 9: 0.9})
    assert _merge_small_pieces(text, ranges, min_piece=8, ceiling=40, token_counter=count,
                               strength=flat) == [(0, 9), (9, 30)]


def test_merge_never_crosses_a_barrier():
    """The #828 seam: a section start is never merged across, even when it is
    the weaker boundary; the piece goes the other way, or stays."""
    text = "z" * 100
    count = SimpleNamespace(count=lambda s: len(s))
    ranges = [(0, 40), (40, 45), (45, 85)]
    weak_left = _strength({40: 0.1, 45: 0.9})
    assert _merge_small_pieces(text, ranges, min_piece=10, ceiling=50, token_counter=count,
                               strength=weak_left, barriers=frozenset({40})) == [
        (0, 40), (40, 85)]
    assert _merge_small_pieces(text, ranges, min_piece=10, ceiling=50, token_counter=count,
                               strength=weak_left, barriers=frozenset({40, 45})) == ranges


def test_a_hard_split_fragment_is_the_weakest_boundary():
    """A boundary inside a sentence (a hard split) has no topic strength: it is
    weaker than any sentence edge, so a small fragment rejoins its sentence when
    that fits."""
    text = "q" * 60
    count = SimpleNamespace(count=lambda s: len(s))
    ranges = [(0, 30), (30, 34), (34, 60)]
    # 30 is a sentence edge (strength 0.0, as weak as an edge gets); 34 is not.
    assert _merge_small_pieces(text, ranges, min_piece=5, ceiling=40, token_counter=count,
                               strength=_strength({30: 0.0})) == [(0, 30), (30, 60)]


# --------------------------------------------------------------------------- #
# Absent keys: exactly #829
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method", SEMANTIC)
def test_absent_keys_reproduce_829_exactly(method):
    """The strict greedy cap at the window is #829's legacy-equivalent baseline
    (51d5622 spans); a chunker built without the keys has no tolerance at all."""
    chunker = make_chunker(method, chunk_size=80, chunk_overlap=0, embed_fn=topic_embed_fn,
                           min_chunk_length=100, max_tokens=80, token_counter=COUNTER,
                           size_cap="tokens")
    assert chunker.size_tolerance is None and chunker.ceiling_tokens is None
    assert chunker.min_piece_tokens is None
    assert _spans(chunker.chunk(_topic_doc())) == BASELINE[f"{method}|80"]
    assert semantic_cap_from_params(method, {"size_cap": "tokens"}, recorded_size=512) == {
        "size_cap": "tokens", "cap_split": "greedy", "breakpoint_cap": "size"}


@pytest.mark.parametrize("method", SEMANTIC)
def test_tolerance_changes_the_chunks_where_it_should(method):
    """Non-vacuous companion: on the same doc, the tolerance does change things."""
    common = {"chunk_size": 20, "chunk_overlap": 0, "embed_fn": topic_embed_fn,
              "min_chunk_length": 100, "max_tokens": 80, "token_counter": COUNTER,
              "size_cap": "tokens"}
    doc = _varied_doc()
    strict = make_chunker(method, **common).chunk(doc)
    tolerant = make_chunker(method, size_tolerance=0.25, **common).chunk(doc)
    assert min(_counts(strict)) < 5 and max(_counts(strict)) <= 20  # small pieces exist
    assert len(tolerant) < len(strict) and max(_counts(tolerant)) > 20
    assert max(_counts(tolerant)) <= 25 and _tiles(doc, tolerant)


# --------------------------------------------------------------------------- #
# The reader and the refusals
# --------------------------------------------------------------------------- #


def test_reader_returns_the_tolerance_only_when_recorded():
    base = {"size_cap": "tokens", "cap_split": "topic", "breakpoint_cap": "size"}
    assert semantic_cap_from_params("semantic", {**base, "size_tolerance": 0.25},
                                    recorded_size=512) == {
        **base, "size_tolerance": 0.25, "min_piece": 128}
    assert semantic_cap_from_params("semantic", {**base, "size_tolerance": 0, "min_piece": 0},
                                    recorded_size=512) == {
        **base, "size_tolerance": 0.0, "min_piece": 0}
    assert semantic_cap_from_params("semantic", {**base, "size_tolerance": 1, "min_piece": 512.0},
                                    recorded_size=512)["min_piece"] == 512
    # Non-semantic entries carrying the keys are ignored at ingest (not refused).
    assert semantic_cap_from_params("fixed", {"size_tolerance": "x"}, recorded_size=1) == {}


@pytest.mark.parametrize(("params", "needle"), [
    ({"size_tolerance": 0.25}, "size_tolerance"),  # no size_cap
    ({"min_piece": 10}, "min_piece"),  # no size_cap
    ({"size_cap": "tokens", "min_piece": 10}, "needs 'size_tolerance'"),
    ({"size_cap": "tokens", "size_tolerance": -0.1}, "size_tolerance"),
    ({"size_cap": "tokens", "size_tolerance": 1.5}, "size_tolerance"),
    ({"size_cap": "tokens", "size_tolerance": True}, "size_tolerance"),
    ({"size_cap": "tokens", "size_tolerance": "0.25"}, "size_tolerance"),
    ({"size_cap": "tokens", "size_tolerance": float("nan")}, "size_tolerance"),
    ({"size_cap": "tokens", "size_tolerance": 0.25, "min_piece": 513}, "min_piece"),
    ({"size_cap": "tokens", "size_tolerance": 0.25, "min_piece": -1}, "min_piece"),
    ({"size_cap": "tokens", "size_tolerance": 0.25, "min_piece": 1.5}, "min_piece"),
    ({"size_cap": "tokens", "size_tolerance": 0.25, "min_piece": float("inf")}, "min_piece"),
    ({"size_cap": "tokens", "size_tolerance": 0.25, "min_piece": False}, "min_piece"),
])
def test_reader_refuses_bad_tolerance_values(params, needle):
    with pytest.raises(ValueError, match=re.escape(needle)):
        semantic_cap_from_params("semantic", params, recorded_size=512)


def test_make_chunker_refusals():
    with pytest.raises(ValueError, match="only to the semantic"):
        make_chunker("sentence", size_tolerance=0.25, token_counter=COUNTER)
    with pytest.raises(ValueError, match="only to the semantic"):
        make_chunker("fixed_token", min_piece=4, token_counter=COUNTER)
    with pytest.raises(ValueError, match="needs a size cap"):
        make_chunker("semantic", embed_fn=_constant_embed, size_tolerance=0.25)
    with pytest.raises(ValueError, match="needs size_tolerance"):
        _make("semantic", tol=None, min_piece=4)
    with pytest.raises(ValueError, match="size_tolerance must be"):
        _make("semantic", tol=2.0)
    with pytest.raises(ValueError, match="min_piece must be"):
        _make("semantic", min_piece=49)
    assert _make("semantic").min_piece_tokens == 12  # default: target // 4


# --------------------------------------------------------------------------- #
# One spec, one chunking: API == ingest_shard == build_chunker; ingest_jsonl
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method", SEMANTIC)
@pytest.mark.parametrize("cap_split", CAP_SPLITS)
def test_api_and_shard_paths_chunk_identically(fake_backends, method, cap_split):
    params = {"size_cap": "tokens", "cap_split": cap_split, "buffer_size": 1,
              "breakpoint_percentile_threshold": 70.0, "min_chunk_length": 100,
              "size_tolerance": 0.25, "min_piece": 6}
    entry = _entry(method, chunk_size=20, chunk_params=params)
    spec = SimpleNamespace(chunk_params=params, chunk_size=20)
    doc = _topic_doc()
    api = deps._chunker_for(entry, embed_fn=topic_embed_fn)
    bulk = ingest_shard._build_chunker(_shard_args(method, 20), spec, embed_fn=topic_embed_fn)
    direct, _c, _m = build_chunker(
        method, chunk_size=20, chunk_overlap=8, model=MODEL, base_url=ENDPOINT,
        embed_fn=topic_embed_fn, buffer_size=1, breakpoint_percentile=70.0,
        min_chunk_length=100, **semantic_cap_from_params(method, params, recorded_size=20),
    )
    for chunker in (api, bulk, direct):
        assert isinstance(chunker, SemanticChunker)
        assert (chunker.size_tolerance, chunker.min_piece_tokens, chunker.ceiling_tokens) == (
            0.25, 6, 25)
    api_chunks = api.chunk(doc)
    assert _spans(api_chunks) == _spans(bulk.chunk(doc)) == _spans(direct.chunk(doc))
    assert max(_counts(api_chunks)) <= 25 and _tiles(doc, api_chunks)


def test_shard_refuses_a_bad_recorded_tolerance():
    spec = SimpleNamespace(chunk_params={"size_cap": "tokens", "size_tolerance": 3},
                           chunk_size=20)
    with pytest.raises(SystemExit, match="size_tolerance"):
        ingest_shard._build_chunker(_shard_args("semantic", 20), spec, embed_fn=topic_embed_fn)


def test_shard_logs_the_tolerance_it_applies(fake_backends, capsys):
    spec = SimpleNamespace(chunk_params={"size_cap": "tokens", "size_tolerance": 0.5,
                                         "min_piece": 3}, chunk_size=20)
    ingest_shard._build_chunker(_shard_args("semantic", 20), spec, embed_fn=topic_embed_fn)
    assert "size_tolerance=0.5 min_piece=3 ceiling=30" in capsys.readouterr().err
    ingest_shard._build_chunker(_shard_args("semantic", 20),
                                SimpleNamespace(chunk_params={"size_cap": "tokens"},
                                                chunk_size=20), embed_fn=topic_embed_fn)
    assert "size_tolerance=None min_piece=None ceiling=None" in capsys.readouterr().err


def _jsonl_args(method="semantic", cap=None, tol=None, piece=None, size=512):
    return SimpleNamespace(chunk_method=method, chunk_size=size, semantic_size_cap=cap,
                           semantic_cap_split=None, semantic_breakpoint_cap=None,
                           semantic_size_tolerance=tol, semantic_min_piece=piece)


def _target(params, size=512):
    return SimpleNamespace(collection_id="lib",
                           spec=SimpleNamespace(chunk_params=params, chunk_size=size))


def test_ingest_jsonl_tolerance_resolution():
    import ingest_jsonl

    resolve = ingest_jsonl._semantic_cap_kwargs
    # No entry: the flags decide.
    assert resolve(_jsonl_args(cap="tokens", tol=0.25), None) == {
        "size_cap": "tokens", "cap_split": "greedy", "breakpoint_cap": "size",
        "size_tolerance": 0.25, "min_piece": 128}
    assert resolve(_jsonl_args(cap="tokens", tol=0.0, piece=0), None)["min_piece"] == 0
    for bad in (_jsonl_args(tol=0.25), _jsonl_args(cap="tokens", piece=10),
                _jsonl_args(method="words", tol=0.25)):
        with pytest.raises(SystemExit) as e:
            resolve(bad, None)
        assert e.value.code == 2
    # Entry: it decides; agreeing flags pass, contradicting ones exit 2.
    entry = _target({"size_cap": "tokens", "size_tolerance": 0.25, "min_piece": 100})
    assert resolve(_jsonl_args(), entry)["min_piece"] == 100
    assert resolve(_jsonl_args(tol=0.25, piece=100), entry)["size_tolerance"] == 0.25
    for bad in (_jsonl_args(tol=0.5), _jsonl_args(piece=128)):
        with pytest.raises(SystemExit) as e:
            resolve(bad, entry)
        assert e.value.code == 2
    # A strict entry cannot be made tolerant from the command line (0 is a value).
    with pytest.raises(SystemExit):
        resolve(_jsonl_args(tol=0.0), _target({"size_cap": "tokens"}))


# --------------------------------------------------------------------------- #
# Receipt provenance
# --------------------------------------------------------------------------- #


def test_receipt_records_the_applied_tolerance_and_round_trips():
    import json

    from ragstack.ingestion.receipts import ShardReceipt

    chunker = _make("semantic", "balanced", target=64, tol=0.5, min_piece=10, max_tokens=80)
    applied = semantic_chunking_provenance(chunker)
    assert applied == {
        "v": 2, "method": "semantic", "size_cap": "tokens", "chunk_size": 64,
        "cap_split": "balanced", "breakpoint_cap": "size", "window_budget": 80,
        "breakpoint_input_budget": 64,
        "size_tolerance": 0.5, "min_piece": 10, "ceiling": 80,  # 96, clamped to the window
    }
    r = ShardReceipt("s1", "t", "completed", chunking=applied)
    again = ShardReceipt.from_dict(json.loads(r.to_json()))
    assert again.chunking == applied and again.to_json() == r.to_json()
    # A v1 object (written before #831) still reads and rewrites byte for byte.
    v1 = {k: v for k, v in applied.items() if k not in ("size_tolerance", "min_piece", "ceiling")}
    v1["v"] = 1
    old = ShardReceipt("s0", "t", "completed", chunking=v1)
    assert ShardReceipt.from_dict(json.loads(old.to_json())).to_json() == old.to_json()


def test_segmenter_patch_is_in_force():
    # Guard for this module: the tiling splitter must be the one in use.
    assert chunkers.sentence_spans is _tiling_spans


@pytest.mark.parametrize("method", SEMANTIC)
def test_a_hard_split_tail_rejoins_its_sentence_in_the_chunker(method):
    """A 100-token sentence (over the ceiling 60) is hard-split at the target,
    leaving a small tail. The tail's left boundary is inside the sentence (the
    weakest kind), so it rejoins the sentence rather than the 8-token run on its
    right, which would also fit (and is a sentence edge)."""
    big = "Big " + " ".join(f"g{j}" for j in range(99)) + "."
    doc = Document(id="hs", content=" ".join([big, "t0 a b c.", "t1 a b c."]), metadata={})
    strict = make_chunker(method, chunk_size=48, chunk_overlap=0, embed_fn=_constant_embed,
                          buffer_size=1, min_chunk_length=0, max_tokens=80,
                          token_counter=COUNTER, size_cap="tokens").chunk(doc)
    assert min(_counts(strict)[:-1]) < 12  # control: a small tail of the big sentence
    chunks = _make(method).chunk(doc)
    run = doc.content.index("t0")
    assert chunks[-1].start_char == run and _counts(chunks)[-1] == 8
    assert sum(_counts(chunks)[:-1]) == 100 and len(chunks) == 3
    assert max(_counts(chunks)) <= 60 and _tiles(doc, chunks)
