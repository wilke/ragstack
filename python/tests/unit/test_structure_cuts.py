"""#828 PR 7: ``structure_cuts`` (section-preferred cuts, §7.1) and the walls
it adds to the #831 small-piece merge.

Offline: the word COUNTER, a tiling sentence splitter (or the regex fallback),
synthetic structured documents from ``tests/structure_support``.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from ragstack.ingestion import chunkers
from ragstack.ingestion.chunkers import (
    _merge_small_pieces,
    _segment_walls,
    _units_by_segment,
    doc_cut_segments,
    make_chunker,
    sentence_tolerance_provenance,
    take_cut_stats,
)
from ragstack.ingestion.structure import (
    Segment,
    StructureIndex,
    classify_roles,
    cut_segments,
    section_segments,
    stamp_documents,
)
from ragstack.models import Document
from ragstack.provenance import sentence_spans_fingerprint
from tests.structure_support import Emitter, paper, random_structure
from tests.unit.test_chunker_factory_parity import COUNTER, _topic_doc, topic_embed_fn

SEMANTIC_BASELINE = json.loads(
    (Path(__file__).parent / "fixtures" / "semantic_cap_baseline_51d5622.json").read_text()
)["spans"]
BASELINE_812 = json.loads(
    (Path(__file__).parent / "fixtures" / "chunk_spans_baseline_812fea0.json").read_text()
)["spans"]


def _tiling_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r".+?(?:\.\s+|$)", text, re.S)]


def _set_segmenter(monkeypatch, name: str) -> None:
    if name == "tiling":
        monkeypatch.setattr(chunkers, "sentence_spans", _tiling_spans)
    else:
        monkeypatch.setattr(chunkers, "_punkt_sentence_spans", lambda text: None)


def _baseline_docs() -> dict[str, Document]:
    content, structure = paper()
    return {"topics": _topic_doc(),
            "paper": Document(id="paper", content=content, metadata={}, structure=structure)}


def _baseline_kwargs(method: str, unit: str) -> dict:
    if method == "fixed_token":
        return {"max_tokens": 512, "token_counter": COUNTER}
    if unit == "chars":
        return {}
    if unit == "chars+cap":
        return {"max_tokens": 80, "token_counter": COUNTER}
    return {"max_tokens": 512, "token_counter": COUNTER, "size_unit": "tokens"}


def _fingerprint(chunks) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(f"{c.id}\x00{c.content}\x01".encode())
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Test 1: absent markers give the pre-PR-7 spans and ids
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", sorted(BASELINE_812))
def test_absent_marker_reproduces_the_812fea0_spans_and_ids(monkeypatch, key):
    seg, doc_name, method, unit, size, ov = key.split("|")
    _set_segmenter(monkeypatch, seg)
    chunker = make_chunker(method, chunk_size=int(size), chunk_overlap=int(ov),
                           **_baseline_kwargs(method, unit))
    chunks = chunker.chunk(_baseline_docs()[doc_name])
    want = BASELINE_812[key]
    assert [[c.start_char, c.end_char] for c in chunks] == want["spans"]
    assert _fingerprint(chunks) == want["ids_content_sha256"]


# --------------------------------------------------------------------------- #
# Fixtures for the cut tests
# --------------------------------------------------------------------------- #

CUTS = "sections/1"
_VOCAB = ("enzyme", "galaxy", "harbor", "violin", "alpha", "beta", "gamma", "delta")


@pytest.fixture
def tiling(monkeypatch):
    """The tiling sentence splitter (env-independent; a heading line has no
    full stop, so it rides in the unit of the sentence after it)."""
    monkeypatch.setattr(chunkers, "sentence_spans", _tiling_spans)


def _prose(rng: random.Random, n: int, words: tuple[int, int] = (3, 9)) -> str:
    topic = rng.choice(_VOCAB[:4])
    return " ".join(
        f"The {topic} " + " ".join(rng.choice(_VOCAB) for _ in range(rng.randint(*words))) + "."
        for _ in range(n))


def sentence_paper(seed: int) -> Document:
    """A random valid structure over prose with full stops (depth <= 3,
    titled and untitled sections, heading-only parents and leaves)."""
    rng = random.Random(seed)
    em = Emitter()
    em.section(None, 1)
    em.block(_prose(rng, rng.randint(1, 3)))
    level = 1
    for i in range(rng.randint(2, 10)):
        level = rng.randint(1, min(3, level + 1))
        titled = rng.random() < 0.85
        em.section(f"Heading {i}" if titled else None, level)
        for _ in range(rng.randint(0 if titled else 1, 3)):
            em.block(_prose(rng, rng.randint(1, 6)))
    content, structure = em.build()
    return Document(id=f"sp{seed}", content=content, metadata={}, structure=structure)


def _segment_index(segs):
    starts = [g.start for g in segs]

    def span(s: int, e: int) -> tuple[int, int]:
        return bisect.bisect_right(starts, s) - 1, bisect.bisect_right(starts, e - 1) - 1

    return span


def _assert_walls(doc: Document, chunks, *, merges: bool) -> int:
    """Every chunk lies inside one cut segment, or (``merges``) is a run of
    WHOLE segments of one group. Returns how many chunks are such runs."""
    segs = doc_cut_segments(doc)
    assert segs is not None
    span = _segment_index(segs)
    crossing = 0
    for c in chunks:
        assert c.content == doc.content[c.start_char:c.end_char]
        lo, hi = span(c.start_char, c.end_char)
        if lo == hi:
            continue
        assert merges, f"chunk [{c.start_char}, {c.end_char}) crosses a segment edge"
        assert c.start_char == segs[lo].start and c.end_char == segs[hi].end, \
            "a fragment of one segment joined another segment"
        assert len({segs[k].group for k in range(lo, hi + 1)}) == 1, \
            "segments of different groups were merged"
        crossing += 1
    return crossing


def _assert_covers(doc: Document, chunks, *, tiles: bool) -> None:
    covered = [False] * len(doc.content)
    for c in chunks:
        for i in range(c.start_char, c.end_char):
            covered[i] = True
    missing = [i for i, ok in enumerate(covered) if not ok and not doc.content[i].isspace()]
    assert not missing, f"{len(missing)} non-space chars in no chunk"
    if tiles:
        assert "".join(c.content for c in chunks) == doc.content


#: name -> (method, kwargs, tiles, merges)
CUT_CONFIGS = {
    "fixed_token/0": ("fixed_token", {"chunk_size": 10, "chunk_overlap": 0,
                                      "token_counter": COUNTER, "max_tokens": 100}, False, False),
    "fixed_token/3": ("fixed_token", {"chunk_size": 10, "chunk_overlap": 3,
                                      "token_counter": COUNTER, "max_tokens": 100}, False, False),
    "sentence/chars": ("sentence", {"chunk_size": 120, "chunk_overlap": 0}, True, False),
    "sentence/chars+ov": ("sentence", {"chunk_size": 120, "chunk_overlap": 30}, False, False),
    "sentence/tokens": ("sentence", {"chunk_size": 15, "chunk_overlap": 0, "size_unit": "tokens",
                                     "token_counter": COUNTER, "max_tokens": 100}, True, False),
    "sentence/tolerance": ("sentence", {
        "chunk_size": 30, "chunk_overlap": 0, "size_unit": "tokens", "token_counter": COUNTER,
        "max_tokens": 100, "size_tolerance": 0.25, "min_piece": 20}, True, True),
    "words/chars+ov": ("words", {"chunk_size": 80, "chunk_overlap": 20}, False, False),
    "semantic/strict": ("semantic", {
        "chunk_size": 15, "chunk_overlap": 0, "embed_fn": topic_embed_fn, "buffer_size": 1,
        "min_chunk_length": 60, "max_tokens": 100, "token_counter": COUNTER,
        "size_cap": "tokens"}, True, False),
    "semantic/tolerance": ("semantic", {
        "chunk_size": 30, "chunk_overlap": 0, "embed_fn": topic_embed_fn, "buffer_size": 1,
        "min_chunk_length": 0, "max_tokens": 100, "token_counter": COUNTER,
        "size_cap": "tokens", "size_tolerance": 0.25, "min_piece": 20}, True, True),
    "semantic_pooled/topic": ("semantic_pooled", {
        "chunk_size": 15, "chunk_overlap": 0, "embed_fn": topic_embed_fn, "buffer_size": 1,
        "min_chunk_length": 0, "max_tokens": 100, "token_counter": COUNTER,
        "size_cap": "tokens", "cap_split": "topic"}, True, False),
}


def _cut_chunker(name: str):
    method, kw, _tiles, _merges = CUT_CONFIGS[name]
    return make_chunker(method, structure_cuts=CUTS, **kw)


# --------------------------------------------------------------------------- #
# Test 1 (cont.): a marked collection, an unstructured document
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", sorted(k for k in BASELINE_812 if "|topics|" in k))
def test_a_marked_collection_chunks_an_unstructured_document_as_today(monkeypatch, key):
    """Brief 3g: no structure (the topics doc has none) → exactly the
    pre-PR-7 spans, and the document is counted ``unsegmented``."""
    seg, doc_name, method, unit, size, ov = key.split("|")
    _set_segmenter(monkeypatch, seg)
    chunker = make_chunker(method, chunk_size=int(size), chunk_overlap=int(ov),
                           structure_cuts=CUTS, **_baseline_kwargs(method, unit))
    doc = _baseline_docs()[doc_name]
    assert doc.structure is None
    chunks = chunker.chunk(doc)
    assert [[c.start_char, c.end_char] for c in chunks] == BASELINE_812[key]["spans"]
    assert take_cut_stats(chunker, doc.id) == Counter({"cuts:unsegmented": 1})


@pytest.mark.parametrize("method", ["semantic", "semantic_pooled"])
@pytest.mark.parametrize("max_tokens", [None, 80, 20])
def test_a_marked_semantic_collection_matches_the_829_baseline_without_structure(
    tiling, method, max_tokens
):
    """The #829 semantic baseline (51d5622) holds for an unstructured document
    under the marker too."""
    chunker = make_chunker(method, chunk_size=512, chunk_overlap=64, embed_fn=topic_embed_fn,
                           min_chunk_length=100, max_tokens=max_tokens,
                           token_counter=COUNTER if max_tokens else None, structure_cuts=CUTS)
    got = [[c.start_char, c.end_char] for c in chunker.chunk(_topic_doc())]
    assert got == SEMANTIC_BASELINE[f"{method}|{max_tokens}"]


def test_an_unmarked_chunker_ignores_a_structure_entirely(tiling):
    """No marker: a structured document chunks exactly as the same text with no
    structure, on every cutting method (the early branch)."""
    for seed in range(10):
        doc = sentence_paper(seed)
        bare = Document(id=doc.id, content=doc.content, metadata={})
        for name, (method, kw, _t, _m) in CUT_CONFIGS.items():
            if "tolerance" in name and method == "sentence":
                continue  # the sentence tolerance exists only under the marker
            chunker = make_chunker(method, **kw)
            assert [(c.id, c.start_char, c.end_char) for c in chunker.chunk(doc)] == \
                [(c.id, c.start_char, c.end_char) for c in chunker.chunk(bare)], (seed, name)
            assert take_cut_stats(chunker, doc.id) == Counter()


# --------------------------------------------------------------------------- #
# Test 2: no chunk crosses a segment edge; chunks cover the document
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(CUT_CONFIGS))
def test_no_chunk_crosses_a_segment_edge_and_the_chunks_cover_the_document(tiling, name):
    _method, _kw, tiles, merges = CUT_CONFIGS[name]
    crossing = 0
    for seed in range(50):
        doc = sentence_paper(seed)
        chunker = _cut_chunker(name)
        chunks = chunker.chunk(doc)
        assert chunks
        crossing += _assert_walls(doc, chunks, merges=merges)
        _assert_covers(doc, chunks, tiles=tiles)
        stats = take_cut_stats(chunker, doc.id)
        assert stats.get("cuts:sibling_merges", 0) == _assert_walls(doc, chunks, merges=merges)
    if merges:
        assert crossing, "the property test never exercised a whole-segment merge"


@pytest.mark.parametrize("name", ["fixed_token/3", "sentence/chars", "words/chars+ov",
                                  "semantic/strict"])
def test_random_structures_with_no_prose_full_stops(name):
    """``random_structure`` (blocks without full stops, loose text, untitled
    sections): the walls and the cover still hold."""
    for seed in range(50):
        content, structure = random_structure(seed)
        doc = Document(id=f"r{seed}", content=content, metadata={}, structure=structure)
        chunks = _cut_chunker(name).chunk(doc)
        _assert_walls(doc, chunks, merges=False)
        _assert_covers(doc, chunks, tiles=False)


# --------------------------------------------------------------------------- #
# Test 3: headings travel with their body
# --------------------------------------------------------------------------- #


def _heading_doc() -> Document:
    em = Emitter()
    em.section("Introduction", 1)
    em.block("The enzyme study begins here. It has two sentences.")
    em.section("Methods", 1)  # heading-only parent
    em.section("Sample preparation", 2)
    em.block("Samples were prepared with care. They were kept cold.")
    em.section("Empty", 2)  # heading-only leaf
    em.section("Statistics", 2)
    em.block("A paired comparison was run. It used every sample.")
    content, structure = em.build()
    return Document(id="h", content=content, metadata={}, structure=structure)


def test_the_segmenter_attaches_a_heading_only_segment_to_the_next():
    doc = _heading_doc()
    text = doc.content
    raw = section_segments(doc.structure)
    cut = cut_segments(doc.structure, text)
    assert len(raw) == 5 and len(cut) == 3
    assert [text[g.start:g.end].split("\n")[0] for g in cut] == [
        "# Introduction", "# Methods", "## Empty"]
    assert "## Sample preparation" in text[cut[1].start:cut[1].end]
    assert "## Statistics" in text[cut[2].start:cut[2].end]
    # Methods' lead (its heading) joins its first child: grouped under Methods,
    # so it may still merge with Methods' other children.
    assert cut[1].group == cut[2].group == 1
    assert cut_segments(doc.structure, text, attach_headings=False) == raw


@pytest.mark.parametrize("name", ["fixed_token/0", "sentence/chars", "sentence/tokens",
                                  "words/chars+ov", "semantic/strict", "sentence/tolerance"])
def test_headings_travel_with_their_body(tiling, name):
    doc = _heading_doc()
    chunks = _cut_chunker(name).chunk(doc)
    heading_lines = {"# Introduction", "# Methods", "## Sample preparation", "## Empty",
                     "## Statistics"}
    assert not [c for c in chunks if c.content.strip() in heading_lines]
    [methods] = [c for c in chunks if "# Methods" in c.content]
    assert "## Sample preparation" in methods.content and "Samples" in methods.content
    [empty] = [c for c in chunks if "## Empty" in c.content]
    assert "## Statistics" in empty.content and "paired" in empty.content


def test_an_attached_heading_is_not_a_section_crossing_but_was_before():
    """Under the marker the stamp reads the cut segments (one helper with the
    cutter); without it the old definition holds, so an unmarked collection's
    stamps are unchanged."""
    doc = _heading_doc()
    structure = doc.structure
    classify_roles(structure.sections)
    cut = cut_segments(structure, doc.content)
    s, e = cut[1].start, cut[1].end  # # Methods + ## Sample preparation
    new = StructureIndex(structure, content=doc.content, attach_headings=True).stamp(
        s, e, doc.content[s:e])
    old = StructureIndex(structure).stamp(s, e, doc.content[s:e])
    assert "section_crossing" not in new
    assert old["section_crossing"] is True
    assert {k: v for k, v in old.items() if k != "section_crossing"} == new


# --------------------------------------------------------------------------- #
# Test 4: fixed_token overlap stops at segment edges
# --------------------------------------------------------------------------- #


def test_fixed_token_overlap_never_crosses_a_segment_edge():
    for seed in range(50):
        doc = sentence_paper(seed)
        chunks = make_chunker("fixed_token", chunk_size=6, chunk_overlap=3,
                              token_counter=COUNTER, structure_cuts=CUTS).chunk(doc)
        segs = doc_cut_segments(doc)
        span = _segment_index(segs)
        overlapped = False
        for a, b in zip(chunks, chunks[1:], strict=False):
            if span(a.start_char, a.end_char)[0] != span(b.start_char, b.end_char)[0]:
                assert a.end_char <= b.start_char, "overlap reached across an edge"
            elif b.start_char < a.end_char:
                overlapped = True
        assert overlapped, "the test never saw overlap inside a segment"
        # The first window of every segment starts at the segment start.
        firsts = {}
        for c in chunks:
            firsts.setdefault(span(c.start_char, c.end_char)[0], c.start_char)
        for k, start in firsts.items():
            assert start == segs[k].start


def test_fixed_token_clips_a_token_that_straddles_an_edge():
    """A token running across an edge is cut there: its head ends the left
    segment's last chunk and its tail opens the right segment's first one."""
    doc = _heading_doc()
    text = doc.content
    edge = doc_cut_segments(doc)[1].start

    class _Straddle(type(COUNTER)):  # one token spanning "...sentences.\n\n# Me"
        def __init__(self):
            super().__init__()
            outer = self

            class _Tok:
                def __call__(self, t, return_offsets_mapping=False, add_special_tokens=True):
                    spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", t)]
                    if t is text or t == text:
                        merged = []
                        for s, e in spans:
                            if merged and s <= edge + 3 and merged[-1][1] >= edge - 15 \
                                    and merged[-1][1] < edge:
                                merged[-1] = (merged[-1][0], e)
                            else:
                                merged.append((s, e))
                        spans = merged
                    return {"offset_mapping": spans}

                def encode(self, t, add_special_tokens=True):
                    return re.findall(r"\S+", t)

            outer._tok = _Tok()

    counter = _Straddle()
    chunks = make_chunker("fixed_token", chunk_size=50, chunk_overlap=0,
                          token_counter=counter, structure_cuts=CUTS).chunk(doc)
    assert any(c.end_char == edge for c in chunks)
    assert any(c.start_char == edge for c in chunks)
    assert all(c.end_char <= edge or c.start_char >= edge for c in chunks)


# --------------------------------------------------------------------------- #
# Test 5: the merge walls
# --------------------------------------------------------------------------- #


def _walls_fixture():
    # A (top leaf) | P lead | C1 | C2 (children of P) | B (top leaf)
    return [Segment(0, 10, 0, -1), Segment(10, 20, 1, 1), Segment(20, 30, 2, 1),
            Segment(30, 40, 3, 1), Segment(40, 50, 4, -1)]


@pytest.mark.parametrize(("left", "right", "ok"), [
    ((20, 25), (25, 30), True),    # two pieces of one segment
    ((20, 30), (30, 40), True),    # two whole siblings
    ((10, 20), (20, 30), True),    # a parent's lead text with its first child
    ((10, 30), (30, 40), True),    # a run of whole segments of one group, and the next
    ((25, 30), (30, 40), False),   # a fragment and a whole sibling
    ((20, 30), (30, 35), False),   # a whole segment and a fragment
    ((25, 30), (30, 35), False),   # two fragments of different segments
    ((30, 40), (40, 50), False),   # whole, but different groups
    ((0, 10), (10, 20), False),    # a top leaf and a lead text of another group
])
def test_segment_walls(left, right, ok):
    assert _segment_walls(_walls_fixture())(left, right) is ok


def test_may_join_generalises_the_barriers_exactly():
    """The default (no predicate) is the barrier test, and a predicate that
    encodes the barriers gives the same result on random inputs."""
    rng = random.Random(7)
    count = SimpleNamespace(count=lambda s: len(s))
    for _ in range(300):
        cuts = sorted(rng.sample(range(1, 200), rng.randint(1, 12)))
        edges = [0, *cuts, 200]
        ranges = list(zip(edges, edges[1:], strict=False))
        barriers = frozenset(rng.sample(cuts, rng.randint(0, len(cuts))))
        strength = {e: rng.random() for e in edges}
        kw = {"min_piece": rng.randint(1, 40), "ceiling": rng.randint(20, 120),
              "token_counter": count, "strength": strength.__getitem__}
        text = "x" * 200
        by_barrier = _merge_small_pieces(text, ranges, barriers=barriers, **kw)
        by_pred = _merge_small_pieces(text, ranges, **kw,
                                      may_join=lambda a, b, bar=barriers: a[1] not in bar)
        assert by_barrier == by_pred
        assert _merge_small_pieces(text, ranges, **kw) == _merge_small_pieces(
            text, ranges, barriers=frozenset(), **kw)


def _siblings_doc() -> Document:
    """Parent P with three tiny leaves, then a top leaf Q with a long body."""
    em = Emitter()
    em.section("Intro", 1)
    em.block("The enzyme alpha beta gamma delta harbor. The enzyme violin alpha beta.")
    em.section("Parts", 1)
    em.block("Lead text of parts.")
    em.section("One", 2)
    em.block("Tiny one.")
    em.section("Two", 2)
    em.block("Tiny two.")
    em.section("Three", 2)
    em.block("Tiny three.")
    em.section("Long", 1)
    em.block(" ".join(f"The galaxy sentence {i} has alpha beta gamma." for i in range(6)))
    content, structure = em.build()
    return Document(id="sib", content=content, metadata={}, structure=structure)


@pytest.mark.parametrize("name", ["semantic/tolerance", "sentence/tolerance"])
def test_whole_siblings_merge_and_are_the_only_section_crossing(tiling, name):
    doc = _siblings_doc()
    chunker = _cut_chunker(name)
    chunks = chunker.chunk(doc)
    assert _assert_walls(doc, chunks, merges=True) >= 1
    merged = [c for c in chunks if "Tiny one" in c.content]
    assert len(merged) == 1 and "Tiny two" in merged[0].content
    stats = take_cut_stats(chunker, doc.id)
    stamp_documents([doc], chunks, structure_cuts=True)
    crossing = [c for c in chunks if c.metadata.get("section_crossing")]
    assert crossing and all(len(_segment_index(doc_cut_segments(doc))(
        c.start_char, c.end_char)) == 2 for c in crossing)
    assert len(crossing) == stats["cuts:sibling_merges"]
    # A merged sibling chunk is owned by one of the siblings, its path is under Parts.
    for c in crossing:
        assert c.metadata["section_path"][0] == "Parts"


def test_fragments_of_different_leaves_never_merge_even_when_small():
    """Two leaves each split so that a small tail meets a small head across the
    edge: the merge must refuse, though the joined text would fit."""
    em = Emitter()
    em.section("A", 1)
    em.block("One two three four five six seven eight nine ten. Tail a.")
    em.section("B", 1)
    em.block("Head b. One two three four five six seven eight nine ten.")
    content, structure = em.build()
    doc = Document(id="frag", content=content, metadata={}, structure=structure)
    chunker = make_chunker("sentence", chunk_size=11, chunk_overlap=0, size_unit="tokens",
                           token_counter=COUNTER, max_tokens=100, structure_cuts=CUTS,
                           size_tolerance=0.0, min_piece=5)
    chunks = chunker.chunk(doc)
    _assert_walls(doc, chunks, merges=True)
    assert not any("Tail a." in c.content and "Head b." in c.content for c in chunks)


# --------------------------------------------------------------------------- #
# Test 6: sentence_spans runs once over the whole document
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["sentence/chars", "sentence/tolerance", "semantic/strict",
                                  "semantic/tolerance"])
def test_sentence_spans_runs_once_over_the_whole_document(monkeypatch, name):
    calls: list[str] = []

    def spy(text):
        calls.append(text)
        return _tiling_spans(text)

    monkeypatch.setattr(chunkers, "sentence_spans", spy)
    doc = sentence_paper(3)
    _cut_chunker(name).chunk(doc)
    assert calls == [doc.content]


def test_the_cut_units_are_the_whole_document_units_split_only_at_edges(tiling):
    for seed in range(50):
        doc = sentence_paper(seed)
        segs = doc_cut_segments(doc)
        whole = chunkers.sentence_spans(doc.content)
        parts = _units_by_segment(whole, segs)
        flat = [u for p in parts for u in p]
        assert "".join(doc.content[s:e] for s, e in flat) == doc.content
        edges = {g.start for g in segs}
        whole_bounds = {s for s, _e in whole}
        assert {s for s, _e in flat} <= whole_bounds | edges
        assert {s for s, _e in flat} >= whole_bounds
    fp = sentence_spans_fingerprint([sentence_paper(s).content for s in range(5)])
    _cut_chunker("sentence/chars").chunk(sentence_paper(0))
    assert sentence_spans_fingerprint([sentence_paper(s).content for s in range(5)]) == fp


# --------------------------------------------------------------------------- #
# Owner decision §6: sentence + #831 tolerance under the walls
# --------------------------------------------------------------------------- #


def _tolerant(target=512, **kw):
    base = {"chunk_size": target, "chunk_overlap": 0, "size_unit": "tokens",
            "token_counter": COUNTER, "max_tokens": 4096, "structure_cuts": CUTS,
            "size_tolerance": 0.25}
    base.update(kw)
    return make_chunker("sentence", **base)


def _words(n: int, tag: str) -> str:
    return " ".join(f"{tag}{i}" for i in range(n - 1)) + " end."


def _one_leaf(*sentences: str) -> Document:
    em = Emitter()
    em.section("Only", 1)
    em.block(" ".join(sentences))
    content, structure = em.build()
    return Document(id="leaf", content=content, metadata={}, structure=structure)


def test_the_defaults_are_the_owners_numbers():
    c = _tolerant()
    assert (c.target_tokens, c.ceiling_tokens, c.min_piece_tokens) == (512, 640, 128)
    assert sentence_tolerance_provenance(c) == {
        "method": "sentence", "chunk_size": 512, "size_tolerance": 0.25, "min_piece": 128,
        "ceiling": 640, "window_budget": 4096}


def test_the_ceiling_is_clamped_by_max_tokens():
    c = _tolerant(max_tokens=600)
    assert c.ceiling_tokens == 600 and c.target_tokens == 512
    doc = _one_leaf(_words(590, "a"), _words(30, "b"))
    assert all(COUNTER.count(x.content) <= 600 for x in c.chunk(doc))


def test_a_segment_within_the_ceiling_stays_whole(tiling):
    doc = _one_leaf(_words(300, "a"), _words(300, "b"))  # 600 + heading line
    [chunk] = _tolerant().chunk(doc)
    assert chunk.content == doc.content


def test_a_sentence_over_the_target_but_within_the_ceiling_is_kept_whole(tiling):
    long = _words(600, "a")
    doc = _one_leaf(long, _words(100, "b"))
    chunker = _tolerant()
    chunks = chunker.chunk(doc)
    assert any(long in c.content and COUNTER.count(c.content) > 512 for c in chunks)
    assert all(COUNTER.count(c.content) <= 640 for c in chunks)
    # A sentence over the CEILING is cut inside, at the target.
    doc = _one_leaf(_words(700, "c"))
    counts = [COUNTER.count(c.content) for c in chunker.chunk(doc)]
    assert len(counts) >= 2 and 500 <= counts[0] <= 512  # the token split keeps 1 headroom


def test_an_unmergeable_tail_stays_short_and_is_counted(tiling):
    """600 + 100 tokens: over the ceiling, so split; the 100-token tail is
    under min_piece (128) but cannot join (700 > 640) and no other segment may
    take it (a fragment): it stays, counted."""
    em = Emitter()
    em.section("A", 1)
    em.block(_words(600, "a") + " " + _words(100, "t"))
    em.section("B", 1)
    em.block(_words(50, "b"))
    content, structure = em.build()
    doc = Document(id="tail", content=content, metadata={}, structure=structure)
    chunker = _tolerant()
    chunks = chunker.chunk(doc)
    counts = [COUNTER.count(c.content) for c in chunks]
    assert len(chunks) == 3 and counts[1] < 128 and counts[2] < 128
    _assert_walls(doc, chunks, merges=True)
    assert take_cut_stats(chunker, doc.id) == Counter({"cuts:short_pieces": 2})


def test_sentence_tolerance_merges_under_the_walls(tiling):
    """Small pieces join inside their segment; whole small siblings join each
    other; a fragment never joins another segment."""
    em = Emitter()
    em.section("Parent", 1)
    em.block(_words(20, "lead"))
    em.section("One", 2)
    em.block(_words(30, "one"))
    em.section("Two", 2)
    em.block(_words(30, "two"))
    em.section("Big", 2)
    em.block(" ".join(_words(200, f"s{i}x") for i in range(4)))  # 800: split
    em.section("Other", 1)
    em.block(_words(40, "other"))
    content, structure = em.build()
    doc = Document(id="m", content=content, metadata={}, structure=structure)
    chunker = _tolerant()
    chunks = chunker.chunk(doc)
    assert _assert_walls(doc, chunks, merges=True) == 1
    [merged] = [c for c in chunks if "lead0" in c.content]
    assert "one0" in merged.content and "two0" in merged.content  # lead + 2 siblings
    assert not any("other0" in c.content and "s3x0" in c.content for c in chunks)
    assert all(COUNTER.count(c.content) <= 640 for c in chunks)


@pytest.mark.parametrize(("kw", "needle"), [
    ({"structure_cuts": None}, "structure_cuts"),
    ({"size_unit": "chars"}, "size_unit='tokens'"),
    ({"chunk_overlap": 10}, "chunk_overlap=0"),
    ({"size_tolerance": 1.5}, "between 0"),
    ({"min_piece": 600}, "min_piece"),
])
def test_the_sentence_tolerance_refuses_what_it_cannot_honour(kw, needle):
    with pytest.raises(ValueError, match=needle):
        _tolerant(**kw)


@pytest.mark.parametrize("method", ["words", "fixed_token", "fixed"])
def test_other_methods_keep_hard_walls(method):
    with pytest.raises(ValueError, match="size_cap/cap_split"):
        make_chunker(method, chunk_size=50, chunk_overlap=0, token_counter=COUNTER,
                     structure_cuts=CUTS, size_tolerance=0.25)


def test_structure_cuts_on_fixed_is_accepted_and_changes_nothing():
    doc = sentence_paper(1)
    marked = make_chunker("fixed", chunk_size=100, chunk_overlap=10, structure_cuts=CUTS)
    plain = make_chunker("fixed", chunk_size=100, chunk_overlap=10)
    assert [(c.start_char, c.end_char) for c in marked.chunk(doc)] == \
        [(c.start_char, c.end_char) for c in plain.chunk(doc)]
    with pytest.raises(ValueError, match="structure_cuts"):
        make_chunker("fixed", chunk_size=100, chunk_overlap=10, structure_cuts="sections/2")


@pytest.mark.parametrize("name", ["sentence/chars", "fixed_token/0", "semantic/strict"])
def test_stamp_documents_reads_the_cut_segments_under_the_marker(tiling, name):
    """Through stamp_documents (what every loop calls): no chunk of a strict
    cut run is a section_crossing, though the heading-attached chunks cross
    the old section segments — and without the marker they still do."""
    doc = _heading_doc()
    chunks = _cut_chunker(name).chunk(doc)
    stamp_documents([doc], chunks, structure_cuts=True)
    assert not [c for c in chunks if c.metadata.get("section_crossing")]
    for c in chunks:
        c.metadata.clear()
    stamp_documents([doc], chunks)
    assert [c for c in chunks if c.metadata.get("section_crossing")]
