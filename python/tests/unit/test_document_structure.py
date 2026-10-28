"""#828 PR 1: the structure model, the §2 invariants, ``rules/1`` and the stamp.

Design: docs/plans/828-document-structure.md (§2 invariants, §3 model, §5 roles,
§6 stamp). Fixtures are synthetic and built by an offset-accumulating emitter
(tests/structure_support.py), so every offset is exact by construction.
"""
from __future__ import annotations

import copy
import json

import pytest

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.structure import (
    STAMP_KEYS,
    StructureInvalid,
    citations,
    classify_roles,
    classify_title,
    content_sha256,
    merge_enrichment,
    normalise_label,
    stamp,
    stamp_documents,
    structure_from_record,
    structure_provenance,
    validate_structure,
)
from ragstack.models import Block, Document, DocumentStructure, Section
from tests.structure_support import Emitter, paper, random_structure, record


def _classified(content: str, structure: DocumentStructure) -> DocumentStructure:
    s = structure.model_copy(deep=True)
    classify_roles(s.sections, inferred=s.structure_source == "inferred")
    validate_structure(s, content, require_roles=True)
    return s


def _by_title(structure: DocumentStructure) -> dict[str | None, Section]:
    return {s.title: s for s in structure.sections}


# --------------------------------------------------------------------------- #
# The model and its serialisation
# --------------------------------------------------------------------------- #

def test_document_dump_is_byte_identical_without_structure() -> None:
    """``structure=None`` is omitted, so every record written without structure
    is byte-identical to one written before the field existed (§3)."""
    doc = Document(id="d1", content="some text", metadata={"a": 1}, source="s.md")
    before = {"id": "d1", "content": "some text", "metadata": {"a": 1}, "source": "s.md"}
    assert doc.model_dump() == before
    assert json.dumps(doc.model_dump()) == json.dumps(before)
    assert doc.model_dump_json() == json.dumps(before, separators=(",", ":"))
    assert "structure" not in Document(id="x", content="").model_dump()


def test_document_dump_carries_the_structure_when_present() -> None:
    content, structure = paper()
    doc = Document(id="d", content=content, structure=structure)
    dumped = doc.model_dump()
    assert dumped["structure"] == structure.model_dump()
    assert dumped["structure"]["schema_version"] == "ragstack.structure/1"
    assert Document.model_validate(dumped) == doc


def test_section_and_structure_field_names_are_the_design_notes() -> None:
    """§3 names the fields exactly; the JSON shape is ``model_dump()``."""
    assert set(Section.model_fields) == {
        "start", "end", "level", "title", "path", "roles", "role_source", "rank", "locator"}
    assert set(Block.model_fields) == {"start", "end", "kind", "label"}
    assert set(DocumentStructure.model_fields) == {
        "schema_version", "content_sha256", "sections", "blocks", "structure_source", "adapter"}


# --------------------------------------------------------------------------- #
# §2 invariants
# --------------------------------------------------------------------------- #

def test_the_fixture_is_valid() -> None:
    content, structure = paper()
    validate_structure(structure, content)


@pytest.mark.parametrize("seed", range(200))
def test_property_random_valid_trees_pass(seed: int) -> None:
    content, structure = random_structure(seed)
    validate_structure(structure, content)


@pytest.mark.parametrize("seed", range(60))
def test_property_moving_any_section_boundary_breaks_an_invariant(seed: int) -> None:
    """Partition with extended ends: shifting ONE section start or end by one
    character always breaks invariant 1, 2, 3 or 5 — with exactly one place of
    slack, by design: a FIRST child's start, which borders its parent's own lead
    text (invariant 3 does not make children cover their parent, so where the
    lead text ends is the adapter's statement, not something the validator can
    pin)."""
    from ragstack.ingestion.structure import _tree

    content, structure = random_structure(seed)
    parents = _tree(structure.sections)
    for i in range(len(structure.sections)):
        first_child = parents[i] is not None and parents[i] == i - 1
        for attr in ("start", "end"):
            for delta in (-1, 1):
                bad = structure.model_copy(deep=True)
                setattr(bad.sections[i], attr, getattr(bad.sections[i], attr) + delta)
                if attr == "start" and first_child:
                    validate_structure(bad, content)  # the one slack, stated above
                    continue
                with pytest.raises(StructureInvalid):
                    validate_structure(bad, content)


def _mutate(fn):
    content, structure = paper()
    bad = structure.model_copy(deep=True)
    fn(bad, content)
    return content, bad


@pytest.mark.parametrize("name,fn,reason", [
    ("end past the text", lambda s, c: setattr(s.sections[-1], "end", len(c) + 1), "offsets"),
    ("empty section (half-open)", lambda s, c: setattr(s.sections[0], "end", 0), "offsets"),
    ("negative block start", lambda s, c: setattr(s.blocks[0], "start", -1), "offsets"),
    ("child level is not parent + 1", lambda s, c: setattr(s.sections[2], "level", 3), "level"),
    ("level-1 gap (end not extended)",
     lambda s, c: setattr(s.sections[0], "end", s.sections[0].end - 2), "partition"),
    ("first level-1 does not start at 0", lambda s, c: setattr(s.sections[0], "start", 1),
     "partition"),
    ("partial overlap", lambda s, c: setattr(s.sections[3], "end", s.sections[4].start + 3),
     "nesting"),
    ("path is not the titled ancestry",
     lambda s, c: setattr(s.sections[2], "path", ["Sample preparation"]), "path"),
    ("blank title", lambda s, c: setattr(s.sections[1], "title", " "), "title"),
    ("blocks out of order", lambda s, c: s.blocks.reverse(), "blocks"),
    ("unknown block kind", lambda s, c: setattr(s.blocks[0], "kind", "paragraph"), "block_kind"),
    ("label on a non-caption", lambda s, c: setattr(s.blocks[0], "label", "Figure 1"), "label"),
    ("content_sha256 mismatch", lambda s, c: setattr(s, "content_sha256", "0" * 64),
     "content_sha256"),
    ("schema_version", lambda s, c: setattr(s, "schema_version", "ragstack.structure/2"),
     "schema_version"),
    ("structure_source", lambda s, c: setattr(s, "structure_source", "guessed"),
     "structure_source"),
    ("unsorted roles", lambda s, c: (setattr(s.sections[4], "roles", ["results", "discussion"]),
                                     setattr(s.sections[4], "role_source", "rules/1")), "roles"),
    ("role outside roles/1", lambda s, c: (setattr(s.sections[1], "roles", ["method"]),
                                           setattr(s.sections[1], "role_source", "rules/1")),
     "roles"),
    ("other beside a tag", lambda s, c: (setattr(s.sections[1], "roles", ["methods", "other"]),
                                         setattr(s.sections[1], "role_source", "rules/1")),
     "roles"),
    ("empty role list", lambda s, c: (setattr(s.sections[1], "roles", []),
                                      setattr(s.sections[1], "role_source", "rules/1")), "roles"),
    ("role_source without roles", lambda s, c: setattr(s.sections[1], "role_source", "rules/1"),
     "roles"),
    ("unknown producer", lambda s, c: (setattr(s.sections[1], "roles", ["methods"]),
                                       setattr(s.sections[1], "role_source", "rules/9")),
     "role_source"),
])
def test_each_invariant_violation_is_named(name, fn, reason) -> None:
    content, bad = _mutate(fn)
    with pytest.raises(StructureInvalid) as e:
        validate_structure(bad, content)
    assert e.value.reason == reason, (name, str(e.value))


def test_heading_block_must_be_first_in_its_section() -> None:
    em = Emitter()
    em.section(None, 1)
    em.block("preamble")
    em.section("Titled", 1)
    em.block("body")
    content, structure = em.build()
    # Put a prose block BEFORE the heading, inside the titled section.
    sec = structure.sections[1]
    heading = next(b for b in structure.blocks if b.kind == "heading")
    bad = structure.model_copy(deep=True)
    bad.blocks.insert(1, Block(start=sec.start, end=heading.start - 1, kind="prose"))
    with pytest.raises(StructureInvalid) as e:
        validate_structure(bad, content)
    assert e.value.reason == "heading"


def test_titled_section_without_or_with_two_headings_is_refused() -> None:
    content, structure = paper()
    no_heading = structure.model_copy(deep=True)
    no_heading.blocks = [b for b in no_heading.blocks
                         if not (b.kind == "heading" and content[b.start:b.end] == "Statistics")]
    with pytest.raises(StructureInvalid, match="owns 0 heading"):
        validate_structure(no_heading, content)
    untitled = structure.model_copy(deep=True)
    stats = next(s for s in untitled.sections if s.title == "Statistics")
    stats.title, stats.path = None, ["Methods"]
    with pytest.raises(StructureInvalid, match="untitled section .* owns a heading"):
        validate_structure(untitled, content)


def test_children_need_not_cover_the_parent_lead_text() -> None:
    """Invariant 3: a parent's own text before its first child is legal."""
    content, structure = paper()
    methods, prep = structure.sections[1], structure.sections[2]
    assert methods.start < prep.start  # lead text exists
    validate_structure(structure, content)


def test_require_roles_refuses_an_unclassified_section() -> None:
    content, structure = paper()
    validate_structure(structure, content)  # roles=None is fine on load
    with pytest.raises(StructureInvalid) as e:
        validate_structure(structure, content, require_roles=True)
    assert e.value.reason == "roles"
    validate_structure(_classified(content, structure), content, require_roles=True)


def test_empty_text_with_no_sections_is_valid() -> None:
    validate_structure(DocumentStructure(content_sha256=content_sha256(""), sections=[],
                                         blocks=[], structure_source="parsed", adapter="t/1"), "")


def test_offsets_are_code_points() -> None:
    """§4.1 item 2: Python ``str`` semantics, like ``start_char``/``end_char``."""
    em = Emitter()
    em.section(None, 1)
    em.block("µ-scale λv and ünïcödé text")
    em.section("Résumé", 1)
    em.block("more")
    content, structure = em.build()
    validate_structure(structure, content)
    h = next(b for b in structure.blocks if b.kind == "heading")
    assert content[h.start:h.end] == "Résumé"


# --------------------------------------------------------------------------- #
# The shared reader
# --------------------------------------------------------------------------- #

def test_reader_accepts_a_valid_structure_and_round_trips_it() -> None:
    content, structure = paper()
    got, reason = structure_from_record(record(content, structure), content)
    assert reason is None and got == structure


def test_reader_absent_key_is_unknown_not_invalid() -> None:
    assert structure_from_record({"text": "x"}, "x") == (None, None)
    assert structure_from_record({"text": "x", "structure": None}, "x") == (None, None)


@pytest.mark.parametrize("raw,reason", [
    ("not an object", "malformed"),
    ({"sections": []}, "malformed"),
    ({"content_sha256": "x", "sections": [], "blocks": [], "structure_source": "parsed",
      "adapter": "t/1", "schema_version": "ragstack.structure/1"}, "content_sha256"),
])
def test_reader_names_why_it_dropped(raw, reason) -> None:
    assert structure_from_record({"text": "abc", "structure": raw}, "abc") == (None, reason)


def test_reader_is_strict_about_types() -> None:
    """A JSON "3" is not an offset (pydantic's lax mode would coerce it)."""
    content, structure = paper()
    raw = structure.model_dump()
    raw["sections"][0]["end"] = str(raw["sections"][0]["end"])
    assert structure_from_record({"structure": raw}, content) == (None, "malformed")


def test_reader_refuses_a_stale_structure_after_an_edit() -> None:
    """§14: a structure paired with an edited text fails on the sha, before any
    offset is read."""
    content, structure = paper()
    edited = content.replace("synthetic", "SYNTHETIC", 1)
    assert len(edited) == len(content)
    assert structure_from_record(record(edited, structure), edited) == (None, "content_sha256")


def test_reader_drops_an_oversized_structure(monkeypatch) -> None:
    from ragstack.ingestion import structure as mod

    content, structure = paper()
    monkeypatch.setattr(mod, "MAX_STRUCTURE_BYTES", 100)
    assert structure_from_record(record(content, structure), content) == (None, "too_large")


# --------------------------------------------------------------------------- #
# rules/1
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("title,tags", [
    ("Abstract", ["abstract"]),
    ("1. Introduction", ["introduction"]),
    ("Background", ["introduction"]),
    ("2.1 Materials and Methods", ["materials", "methods"]),
    ("Materials & Methods", ["materials", "methods"]),
    ("Patients and Methods", ["methods"]),
    ("Experimental Procedures", ["methods"]),
    ("III. RESULTS", ["results"]),
    ("Results and Discussion", ["discussion", "results"]),
    ("Results/Discussion", ["discussion", "results"]),
    ("Discussion", ["discussion"]),
    ("Conclusions", ["conclusions"]),
    ("Concluding remarks", ["conclusions"]),
    ("Acknowledgments", ["acknowledgements"]),
    ("Competing interests", ["acknowledgements"]),
    ("References", ["references"]),
    ("Bibliography", ["references"]),
    ("Reference", ["parameters"]),
    ("API Reference", ["parameters"]),
    ("Command reference", ["parameters"]),
    ("Supporting Information", ["supplementary"]),
    ("Appendix", ["supplementary"]),
    ("Overview", ["overview"]),
    ("What is the tool?", ["overview"]),
    ("Installation", ["installation"]),
    ("Getting started", ["installation"]),
    ("Usage", ["usage"]),
    ("How to run a job", ["usage"]),
    ("Options", ["parameters"]),
    ("Examples", ["examples"]),
    ("Output files", ["output"]),
    ("Troubleshooting", ["troubleshooting"]),
    ("Frequently Asked Questions", ["faq"]),
    ("Sample preparation", ["other"]),
    ("Questions and Answers", ["other"]),  # parts do not all match: matched whole
    (None, ["unknown"]),
])
def test_rules_v1_table(title, tags) -> None:
    assert classify_title(title) == tags


def test_unmatched_inferred_title_is_unknown_not_other() -> None:
    assert classify_title("Sample preparation", inferred=True) == ["unknown"]
    assert classify_title("Methods", inferred=True) == ["methods"]


def test_summary_is_positional() -> None:
    assert classify_title("Summary", first=True) == ["abstract"]
    assert classify_title("Summary", last=True) == ["conclusions"]
    assert classify_title("Summary") == ["other"]


def _docs_page(*titles: str) -> list[Section]:
    em = Emitter()
    em.section(None, 1)
    em.block("x")
    for t in titles:
        em.section(t, 1)
        em.block("body")
    return em.build()[1].sections


def test_summary_first_and_last_in_a_section_list() -> None:
    first = _docs_page("Summary", "Methods", "Discussion")
    classify_roles(first)
    assert first[1].roles == ["abstract"]
    last = _docs_page("Introduction", "Methods", "Summary")
    classify_roles(last)
    assert last[3].roles == ["conclusions"]


def test_introduction_gains_overview_beside_installation_or_usage() -> None:
    docs = _docs_page("Introduction", "Installation", "Usage")
    classify_roles(docs)
    assert docs[1].roles == ["introduction", "overview"]
    paper_like = _docs_page("Introduction", "Methods")
    classify_roles(paper_like)
    assert paper_like[1].roles == ["introduction"]


def test_classify_fills_only_unclassified_sections_and_writes_rules_v1() -> None:
    content, structure = paper()
    s = structure.model_copy(deep=True)
    s.sections[1].roles, s.sections[1].role_source = ["supplementary"], "declared/1"
    classify_roles(s.sections)
    assert s.sections[1].roles == ["supplementary"]  # declared: never overwritten
    assert s.sections[1].role_source == "declared/1"
    assert {x.role_source for x in s.sections[2:]} == {"rules/1"}
    assert _by_title(s)[None].roles == ["unknown"]
    assert _by_title(s)["Results and Discussion"].roles == ["discussion", "results"]


def test_classify_is_deterministic() -> None:
    content, structure = paper()
    a, b = structure.model_copy(deep=True), structure.model_copy(deep=True)
    classify_roles(a.sections)
    classify_roles(b.sections)
    assert a == b


# --------------------------------------------------------------------------- #
# Captions and citations
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,labels", [
    ("as in Fig. 3.", ["Figure 3"]),
    ("FIG 3 shows", ["Figure 3"]),
    ("Figure 3A and", ["Figure 3"]),
    ("see Figure 3(b)", ["Figure 3"]),
    ("Figs. 2 and 3", ["Figure 2", "Figure 3"]),
    ("Figures 1, 2, and 5", ["Figure 1", "Figure 2", "Figure 5"]),
    ("Tables 1–3", ["Table 1", "Table 2", "Table 3"]),
    ("Table S1 and Fig. S2", ["Figure S2", "Table S1"]),
    ("Supplementary Figure 2", ["Figure S2"]),
    ("Tab. 4", ["Table 4"]),
    ("press tab 3 times", []),
    ("a configuration 3 file", []),
    ("no labels here", []),
])
def test_citation_normalisation_table(text, labels) -> None:
    assert citations(text) == labels


@pytest.mark.parametrize("text,label", [
    ("Figure 1. A caption.", "Figure 1"),
    ("Fig. 3: another", "Figure 3"),
    ("TABLE 2 Results", "Table 2"),
    ("Table S1. Supplementary", "Table S1"),
    ("A caption without a label", None),
])
def test_caption_label_normalisation(text, label) -> None:
    assert normalise_label(text) == label


# --------------------------------------------------------------------------- #
# The stamp (§6)
# --------------------------------------------------------------------------- #

def _range_of(content: str, needle: str) -> tuple[int, int]:
    i = content.index(needle)
    return i, i + len(needle)


def test_stamp_in_a_methods_subsection_inherits_methods() -> None:
    """Q4: ``section_roles`` is the union along the path. MUTATION: stamp only
    the owning section's own tags → this chunk lacks ``methods`` → fails."""
    content, structure = paper()
    s = _classified(content, structure)
    start, end = _range_of(content, "Samples were prepared")
    out = stamp(s, start, end, content[start:end])
    assert out["section_title"] == "Sample preparation"
    assert out["section_path"] == ["Methods", "Sample preparation"]
    assert out["section_level"] == 2
    assert out["section_roles"] == ["methods", "other"]
    assert out["section_roles_version"] == ["rules/1"]
    assert out["structure_source"] == "parsed"
    assert out["structure_version"] == "test/1;stamp/1"
    assert "section_crossing" not in out


def test_unknown_is_dropped_beside_any_other_tag_and_kept_alone() -> None:
    em = Emitter()
    em.section("Methods", 1)
    em.block("lead")
    em.section(None, 2)
    em.block("an untitled subsection")
    content, structure = em.build()
    s = _classified(content, structure)
    a, b = _range_of(content, "an untitled subsection")
    out = stamp(s, a, b, content[a:b])
    assert out["section_roles"] == ["methods"]  # unknown dropped
    assert out["section_roles_version"] == ["rules/1"]
    assert "section_title" not in out and out["section_path"] == ["Methods"]

    em = Emitter()
    em.section(None, 1)
    em.block("only untitled text")
    content, structure = em.build()
    s = _classified(content, structure)
    out = stamp(s, 0, len(content), content)
    assert out["section_roles"] == ["unknown"]
    assert "section_title" not in out and "section_path" not in out


def test_roles_version_lists_every_contributing_producer() -> None:
    em = Emitter()
    em.section("Methods", 1, roles=["methods"], role_source="declared/1")
    em.block("lead")
    em.section("Assay", 2)
    em.block("assay body")
    content, structure = em.build()
    s = _classified(content, structure)
    a, b = _range_of(content, "assay body")
    out = stamp(s, a, b, content[a:b])
    assert out["section_roles"] == ["methods", "other"]
    assert out["section_roles_version"] == ["declared/1", "rules/1"]


def test_roles_version_omits_a_producer_whose_only_tag_was_dropped() -> None:
    """Decision 6: a declared parent [methods] over an untitled child (rules/1 →
    unknown, dropped from the union) is versioned by declared/1 alone.
    MUTATION: list every producer on the path → ["declared/1", "rules/1"] → fails."""
    em = Emitter()
    em.section("Methods", 1, roles=["methods"], role_source="declared/1")
    em.block("lead")
    em.section(None, 2)
    em.block("an untitled subsection body")
    content, structure = em.build()
    s = _classified(content, structure)
    assert s.sections[1].roles == ["unknown"] and s.sections[1].role_source == "rules/1"
    a, b = _range_of(content, "an untitled subsection body")
    out = stamp(s, a, b, content[a:b])
    assert out["section_roles"] == ["methods"]
    assert out["section_roles_version"] == ["declared/1"]


def _intro_siblings(n: int) -> list[Section]:
    em = Emitter()
    em.section(None, 1)
    em.block("x")
    for _ in range(n):
        em.section("Introduction", 1)
        em.block("b")
    return em.build()[1].sections


def test_classify_roles_is_linear_in_sibling_count() -> None:
    """Thousands of same-parent "Introduction" siblings (no installation/usage
    sibling, so no early exit) must classify in linear time: the structure key
    is caller-supplied, and a quadratic sibling scan took ~40 s at 4000.
    Robust on a loaded host: compares 4000 against 500 siblings (8x the work;
    linear ≈ 8x the time, quadratic ≈ 64x) AND bounds the absolute time
    generously."""
    import time

    def timed(n: int) -> float:
        sections = _intro_siblings(n)
        t0 = time.perf_counter()
        classify_roles(sections)
        took = time.perf_counter() - t0
        assert all(x.roles == ["introduction"] for x in sections[1:])
        return took

    timed(200)  # warm-up
    small = min(timed(500) for _ in range(3))
    large = min(timed(4000) for _ in range(3))
    assert large < 2.0, f"4000 siblings took {large:.2f}s"
    assert large < 25 * max(small, 1e-4), (small, large)


def test_stamping_a_3000_introduction_sibling_document_is_fast() -> None:
    """The whole stamp path (classify, re-validate, index, stamp) on the
    review's adversarial shape: well under the quadratic version's ~22 s."""
    import time
    from collections import Counter

    em = Emitter()
    em.section(None, 1)
    em.block("x")
    for _ in range(3000):
        em.section("Introduction", 1)
        em.block("b")
    content, structure = em.build()
    doc = Document(id="big", content=content, structure=structure)
    chunks = RecursiveCharacterChunker(chunk_size=400, chunk_overlap=0).chunk(doc)
    counts: Counter[str] = Counter()
    t0 = time.perf_counter()
    stamp_documents([doc], chunks, counts)
    took = time.perf_counter() - t0
    assert counts["chunks_stamped"] == len(chunks) and not any(
        k.startswith("degraded") for k in counts)
    assert took < 3.0, f"stamping took {took:.2f}s"


def test_owner_is_the_deepest_section_even_against_a_larger_parent_overlap() -> None:
    content, structure = paper()
    s = _classified(content, structure)
    lead_start, _ = _range_of(content, "Lead paragraph")
    _, prep_end = _range_of(content, "Samples were prepared")
    out = stamp(s, lead_start, prep_end, content[lead_start:prep_end])
    assert out["section_title"] == "Sample preparation"
    assert out["section_crossing"] is True  # parent lead text + a leaf


def test_owner_tie_goes_to_the_earliest() -> None:
    em = Emitter()
    em.section(None, 1)
    em.block("p")
    em.section("Alpha", 1)
    em.block("aaaa")
    em.section("Beta", 1)
    em.block("bbbb")
    content, structure = em.build()
    s = _classified(content, structure)
    beta = s.sections[2]
    # Equal overlap on both sides of the Alpha|Beta boundary.
    out = stamp(s, beta.start - 4, beta.start + 4, content[beta.start - 4:beta.start + 4])
    assert out["section_title"] == "Alpha"
    out = stamp(s, beta.start - 3, beta.start + 4, content[beta.start - 3:beta.start + 4])
    assert out["section_title"] == "Beta"


def test_block_types_captions_and_refers_to() -> None:
    content, structure = paper()
    s = _classified(content, structure)
    rd = _by_title(s)["Results and Discussion"]
    out = stamp(s, rd.start, rd.end, content[rd.start:rd.end])
    assert out["block_types"] == ["figure_caption", "heading", "prose"]
    assert out["caption_labels"] == ["Figure 1"]
    assert out["refers_to"] == ["Figure 1", "Figure 3"]
    # A chunk holding the caption's END but not its start gets no label.
    cap = next(b for b in s.blocks if b.kind == "figure_caption")
    out = stamp(s, cap.start + 5, rd.end, content[cap.start + 5:rd.end])
    assert "caption_labels" not in out


def test_stamp_is_pure_and_total_over_every_chunk() -> None:
    """Every chunk of a structured document gets section_level,
    section_roles, structure_source and structure_version; the stamp is a pure
    function (same output twice, the structure untouched)."""
    content, structure = paper()
    s = _classified(content, structure)
    frozen = copy.deepcopy(s.model_dump())
    doc = Document(id="d", content=content, structure=s)
    chunks = RecursiveCharacterChunker(chunk_size=40, chunk_overlap=8).chunk(doc)
    assert len(chunks) > 5
    for c in chunks:
        first = stamp(s, c.start_char, c.end_char, c.content)
        assert first == stamp(s, c.start_char, c.end_char, c.content)
        for key in ("section_level", "section_roles", "section_roles_version",
                    "structure_source", "structure_version"):
            assert key in first, (key, c.start_char)
        assert set(first) <= set(STAMP_KEYS)
        assert first["section_roles"] and all(v != [] for v in first.values())
    assert s.model_dump() == frozen


def test_stamp_refuses_an_unclassified_structure() -> None:
    content, structure = paper()
    with pytest.raises(StructureInvalid):
        stamp(structure, 0, 10, content[:10])


# --------------------------------------------------------------------------- #
# stamp_documents (the helper every chunk loop calls)
# --------------------------------------------------------------------------- #

def _chunked(doc: Document):
    return RecursiveCharacterChunker(chunk_size=60, chunk_overlap=0).chunk(doc)


def test_stamp_documents_stamps_structured_documents_only() -> None:
    from collections import Counter

    content, structure = paper()
    structured = Document(id="s", content=content, structure=structure, source="a.jsonl")
    plain = Document(id="p", content=content, source="b.jsonl")
    chunks = _chunked(structured) + _chunked(plain)
    counts: Counter[str] = Counter()
    stamp_documents([structured, plain], chunks, counts)
    for c in chunks:
        if c.doc_id == "s":
            assert c.metadata["structure_version"] == "test/1;stamp/1"
        else:
            assert not set(c.metadata) & set(STAMP_KEYS)
    assert counts["adapter:test/1"] == 1 and counts["structure_missing"] == 1
    assert counts["chunks_stamped"] == len(_chunked(structured))
    rec = structure_provenance(counts)
    assert rec == {"structure": {"stamp": "stamp/1", "adapters": {"test/1": 1},
                                 "structure_missing": 1,
                                 "chunks_stamped": counts["chunks_stamped"]}}
    # Own roles are recorded on the structure (classify ran), inheritance is not.
    assert _by_title(structured.structure)["Sample preparation"].roles == ["other"]


def test_stamp_documents_is_all_or_nothing_per_document() -> None:
    """A chunk whose offsets do not reproduce its text degrades the whole
    document: no chunk of it is stamped, the pre-existing metadata (a JATS
    unit's passthrough ``section_title``) is untouched, and it is counted."""
    from collections import Counter

    content, structure = paper()
    doc = Document(id="s", content=content, structure=structure)
    chunks = _chunked(doc)
    for c in chunks:
        c.metadata["section_title"] = "table-2"
    chunks[-1].content = chunks[-1].content + "drift"
    counts: Counter[str] = Counter()
    stamp_documents([doc], chunks, counts)
    assert all(c.metadata == {"section_title": "table-2"} for c in chunks)
    assert counts["degraded:offsets"] == 1 and counts["chunks_stamped"] == 0
    rec = structure_provenance(counts)
    assert rec is not None
    # Same shape as structure_invalid: a {reason: n} map, present only when non-zero.
    assert rec["structure"]["degraded"] == {"offsets": 1}
    assert "structure_invalid" not in rec["structure"]


def test_degraded_reason_is_the_exception_class_for_a_non_invariant_failure(
    monkeypatch,
) -> None:
    from collections import Counter

    from ragstack.ingestion import structure as mod

    def boom(*a, **k):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(mod, "classify_roles", boom)
    content, structure = paper()
    doc = Document(id="s", content=content, structure=structure)
    counts: Counter[str] = Counter()
    stamp_documents([doc], _chunked(doc), counts)
    assert structure_provenance(counts)["structure"]["degraded"] == {"RuntimeError": 1}


def test_provenance_is_none_when_nothing_ran_and_merges_with_the_glossary() -> None:
    from collections import Counter

    assert structure_provenance(Counter()) is None
    assert merge_enrichment(None, None) is None
    g = {"symbol_glossary": {"extractor": "rules/1"}}
    s = {"structure": {"stamp": "stamp/1"}}
    assert merge_enrichment(g, None) == g
    assert merge_enrichment(g, s) == {**g, **s}
