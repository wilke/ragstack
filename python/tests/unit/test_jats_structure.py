"""The ``jats/1`` structure adapter (#828 §9.2, PR 5b part i).

``jats_structure.article_structure(text, spans)`` turns the span list of
``jats.article_spans`` into a :class:`DocumentStructure`. These tests pin that
the result passes ``validate_structure`` on every JATS fixture (extended ends,
level-1 partition), that heading blocks are exactly the title text (the hash
run clamped at 6), that untitled ``<sec>`` and body-lead runs are first-class
sections, the ``declared/1`` ``sec-type`` map of §5, and that the rules never
touch declared roles. Synthetic JATS only.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ragstack.ingestion import jats
from ragstack.ingestion.jats_structure import (
    ADAPTER,
    DECLARED_SEC_TYPES,
    article_structure,
    declared_roles,
)
from ragstack.ingestion.structure import (
    DECLARED_VERSION,
    RULES_VERSION,
    StructureInvalid,
    classify_roles,
    classify_title,
    validate_structure,
)

# The span tests' fixtures (and through them test_jats_extract's) are the
# shapes this adapter must accept; tests/ingestion is not a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
from test_jats_extract import FIG_1, TABLE_1, _article  # noqa: E402
from test_jats_spans import FIXTURES as SPAN_FIXTURES  # noqa: E402


def _nest(depth: int, levels: int) -> str:
    """``levels`` titled <sec>s nested one inside the next, starting at <sec>
    depth ``depth``; each holds a paragraph before its child."""
    if levels == 0:
        return ""
    return (f"<sec><title>Level {depth}</title><p>Paragraph at depth {depth}.</p>"
            f"{_nest(depth + 1, levels - 1)}</sec>")


#: Every §9.2 / §12-row-5b case in one article: an abstract, a two-paragraph
#: body lead, ``sec-type`` present (mapped, combined, unmapped, mixed) and
#: absent, a paragraph between top-level sections, nested and untitled
#: sections, a paragraph after a parent's last child section, every leaf kind,
#: floats nested in ``<p>``, in a ``<sec>`` and in ``<floats-group>``, a
#: section nested 7 deep (``<sec>`` depths 0..6, past the 6-hash clamp), and a
#: trailing body paragraph after the last section.
RICH_BODY = f"""
    <p>Lead paragraph one.</p>
    <p>Lead paragraph two holds a float {TABLE_1} in the middle.</p>
    <sec id="s-intro" sec-type="intro">
      <title>Introduction</title>
      <p>Introductory prose.</p>
    </sec>
    <p>A body paragraph between two top-level sections.</p>
    <sec sec-type="materials|methods">
      <label>2.</label>
      <title>Materials and Methods</title>
      <p>Methods lead paragraph.</p>
      <sec sec-type="subjects">
        <title>Patients and Methods</title>
        <p>The cohort.</p>
      </sec>
      <sec>
        <p>An untitled nested section.</p>
        <list><list-item><p>Alpha.</p></list-item><list-item><p>Beta.</p></list-item></list>
      </sec>
      <sec sec-type="foo|methods">
        <title>Assays</title>
        <disp-formula>E = m c 2</disp-formula>
        <preformat>raw preformatted text</preformat>
        <code>print(1)</code>
        <disp-quote><p>A quoted passage.</p></disp-quote>
        {FIG_1}
      </sec>
      <p>A trailing paragraph after the last child section.</p>
    </sec>
    <sec sec-type="results">
      <title>Results</title>
      <p>Results lead.</p>
      {_nest(1, 6)}
    </sec>
    <sec sec-type="Unmapped-Type">
      <title>Odd</title>
      <p>An unmapped sec-type.</p>
    </sec>
    <sec>
      <title>Discussion</title>
      <p>No sec-type here.</p>
      <boxed-text><p>An unknown leaf tag.</p></boxed-text>
    </sec>
    <p>A trailing body paragraph after the last section.</p>
"""

FIXTURES = {**SPAN_FIXTURES, "rich": _article("60", RICH_BODY, TABLE_1 + FIG_1)}


def _build(name: str):
    text, spans = jats.article_spans(ET.fromstring(FIXTURES[name]))
    return text, spans, article_structure(text, spans)


def _by_locator(structure):
    return {s.locator: s for s in structure.sections if s.locator is not None}


def _block_texts(text, structure, kind=None):
    return [text[b.start:b.end] for b in structure.blocks if kind is None or b.kind == kind]


# ------------------------------------------------------------------ validity

@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_valid_on_every_fixture(name):
    text, spans, structure = _build(name)
    validate_structure(structure, text)
    assert structure.adapter == ADAPTER == "jats/1"
    assert structure.structure_source == "parsed"
    assert all(s.rank is None for s in structure.sections)
    # Pure and deterministic: the same spans give the same structure.
    assert article_structure(text, spans) == structure
    # Every <sec> span is a section, in order, at level depth + 1.
    secs = [s for s in spans if s.kind == "sec"]
    assert [(s.path, s.depth + 1) for s in secs] == [
        (x.locator, x.level) for x in structure.sections
        if x.locator not in (None, "abstract")]
    if text:
        assert structure.sections[0].start == 0
        assert max(s.end for s in structure.sections) == len(text)


def test_empty_record_gives_an_empty_valid_structure():
    structure = article_structure("", [])
    assert structure.sections == [] and structure.blocks == []
    validate_structure(structure, "")


def test_spans_paired_with_the_wrong_text_are_refused():
    text, spans, _ = _build("rich")
    with pytest.raises(ValueError):
        article_structure(text[:10], spans)
    with pytest.raises(ValueError):                 # same length, heading moved
        article_structure(" " + text[:-1], spans)


# ------------------------------------------------------------------ headings

@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_heading_block_text_equals_title(name):
    text, spans, structure = _build(name)
    starts = [b.start for b in structure.blocks]
    titled = [s for s in structure.sections if s.title is not None]
    for sec in titled:
        first = next(b for b in structure.blocks if b.start >= sec.start)
        assert first.kind == "heading"
        assert text[first.start:first.end] == sec.title, sec.locator
    assert len(_block_texts(text, structure, "heading")) == len(titled)
    assert starts == sorted(starts)
    if name == "rich":
        by_loc = _by_locator(structure)
        deepest = max(structure.sections, key=lambda s: s.level)
        assert deepest.level == 7 and deepest.title == "Level 6"     # <sec> depth 6
        assert by_loc[deepest.locator].path[-3:] == ["Level 4", "Level 5", "Level 6"]
        # The rendered line is clamped at six hashes; the block is past them.
        assert text[deepest.start:].startswith("###### Level 6")
        assert any(s.title == "Level 5" and s.level == 6 for s in structure.sections)


# ------------------------------------------------------------------ sections

def test_untitled_nested_sec_is_its_own_section():
    text, spans, structure = _build("rich")
    by_loc = _by_locator(structure)
    untitled = by_loc["body/sec[2]/sec[2]"]
    span = next(s for s in spans if s.path == "body/sec[2]/sec[2]")
    assert untitled.title is None and untitled.level == 2
    assert untitled.path == ["Materials and Methods"]           # untitled adds nothing
    assert untitled.roles is None and untitled.role_source is None
    assert untitled.start == span.start
    assert text[untitled.start:].startswith("An untitled nested section.")
    patients = by_loc["body/sec[2]/sec[1]"]
    assert patients.end == untitled.start                       # predecessor stops here
    assert "An untitled nested section." not in text[patients.start:patients.end]
    # The synthetic fixture's untitled top-level <sec> is a level-1 section too.
    _, _, syn = _build("synthetic")
    top = _by_locator(syn)["body/sec[2]"]
    assert top.title is None and top.level == 1 and top.path == []


def test_abstract_and_body_lead_sections():
    text, spans, structure = _build("rich")
    abstract_span = spans[0]
    assert abstract_span.kind == "abstract"
    ab, lead = structure.sections[0], structure.sections[1]
    assert (ab.start, ab.title, ab.level, ab.path) == (0, None, 1, [])
    assert ab.roles == ["abstract"] and ab.role_source == DECLARED_VERSION
    assert ab.locator == "abstract"
    # Extended over the abstract→body "\n\n" join, which belongs to no span.
    assert abstract_span.end < ab.end == lead.start == abstract_span.end + 2
    assert text[abstract_span.end:ab.end] == "\n\n"
    # Both lead paragraphs are ONE untitled level-1 section (a maximal run).
    assert (lead.title, lead.level, lead.roles, lead.locator) == (None, 1, None, None)
    seg = text[lead.start:lead.end]
    assert seg.startswith("Lead paragraph one.") and "Lead paragraph two" in seg
    assert lead.end == _by_locator(structure)["body/sec[1]"].start
    top = [s for s in structure.sections if s.level == 1]
    between = [s for s in top if s.locator is None
               and text[s.start:].startswith("A body paragraph between")]
    assert len(between) == 1
    last = top[-1]
    assert last.locator is None and last.end == len(text)
    assert text[last.start:last.end] == "A trailing body paragraph after the last section."


def test_trailing_p_after_last_child_absorbed_and_valid():
    text, spans, structure = _build("rich")
    validate_structure(structure, text)
    by_loc = _by_locator(structure)
    parent, assays = by_loc["body/sec[2]"], by_loc["body/sec[2]/sec[3]"]
    trailing = next(s for s in spans if s.path == "body/sec[2]/p[2]")
    assert text[trailing.start:trailing.end] == \
        "A trailing paragraph after the last child section."
    # Separators (and the parent's text after its last child) belong to the
    # preceding section: the last child extends to its parent's end.
    assert assays.end == parent.end == by_loc["body/sec[3]"].start
    assert assays.start < trailing.start < trailing.end <= assays.end
    block = next(b for b in structure.blocks if b.start == trailing.start)
    assert (block.end, block.kind) == (trailing.end, "prose")


def test_no_body():
    text, _, structure = _build("no_body")
    validate_structure(structure, text)
    assert len(structure.sections) == 1
    only = structure.sections[0]
    assert (only.start, only.end, only.roles) == (0, len(text), ["abstract"])
    assert [(b.start, b.end, b.kind) for b in structure.blocks] == [(0, len(text), "prose")]


def test_no_abstract():
    text, _, structure = _build("no_abstract")
    validate_structure(structure, text)
    first = structure.sections[0]
    assert (first.start, first.title, first.roles, first.level) == (0, None, None, 1)
    assert text.startswith("A body-lead paragraph")
    assert not any(s.roles == ["abstract"] for s in structure.sections)


# ---------------------------------------------------------------------- roles

def test_sec_type_map():
    assert declared_roles("materials|methods") == (["materials", "methods"], DECLARED_VERSION)
    assert declared_roles("methods|materials") == (["materials", "methods"], DECLARED_VERSION)
    assert declared_roles("results|discussion") == (["discussion", "results"], DECLARED_VERSION)
    assert declared_roles("intro") == (["introduction"], DECLARED_VERSION)
    assert declared_roles(" Intro ") == (["introduction"], DECLARED_VERSION)
    assert declared_roles("materials-and-methods") == (["materials", "methods"],
                                                       DECLARED_VERSION)
    assert declared_roles("supplementary-material") == (["supplementary"], DECLARED_VERSION)
    assert declared_roles("cases") == (["other"], DECLARED_VERSION)
    assert declared_roles("cases|results") == (["results"], DECLARED_VERSION)
    assert declared_roles("display-objects") == (["other"], DECLARED_VERSION)
    assert declared_roles("not-a-known-type")== (["other"], DECLARED_VERSION)
    assert declared_roles("foo|methods") == (["methods"], DECLARED_VERSION)
    assert declared_roles("subjects|methods") == (["methods"], DECLARED_VERSION)
    assert declared_roles("methods|methods") == (["methods"], DECLARED_VERSION)
    assert declared_roles(None) == (None, None)
    assert declared_roles("") == (None, None)
    assert declared_roles("  ") == (None, None)
    assert declared_roles(" | ") == (None, None)
    # §5's map, exactly.
    assert DECLARED_SEC_TYPES == {
        "intro": ("introduction",), "materials": ("materials",), "methods": ("methods",),
        "materials-and-methods": ("materials", "methods"), "results": ("results",),
        "discussion": ("discussion",), "conclusions": ("conclusions",),
        "supplementary-material": ("supplementary",), "cases": ("other",),
        "subjects": ("other",)}
    # ...and through the adapter, from the attribute.
    _, _, structure = _build("rich")
    by_loc = _by_locator(structure)
    assert by_loc["body/sec[1]"].roles == ["introduction"]
    assert by_loc["body/sec[2]"].roles == ["materials", "methods"]
    assert by_loc["body/sec[2]/sec[1]"].roles == ["other"]          # subjects
    assert by_loc["body/sec[2]/sec[3]"].roles == ["methods"]        # foo|methods
    assert by_loc["body/sec[4]"].roles == ["other"]                 # Unmapped-Type
    assert by_loc["body/sec[5]"].roles is None                      # absent
    assert by_loc["body/sec[5]"].role_source is None


def test_declared_roles_survive_classify_roles():
    text, _, structure = _build("rich")
    before = {s.locator: (s.roles, s.role_source) for s in structure.sections}
    declared = {k: v for k, v in before.items() if v[0] is not None}
    assert declared["body/sec[4]"] == (["other"], DECLARED_VERSION)
    # Titles the rules read differently from the declaration: "Patients and
    # Methods" (declared subjects → other; rules → methods) and "Assays"
    # (declared foo|methods → methods; rules → other).
    assert classify_title("Patients and Methods") == ["methods"]
    assert classify_title("Assays") == ["other"]
    classify_roles(structure.sections)
    after = {s.locator: (s.roles, s.role_source) for s in structure.sections}
    for loc, value in declared.items():
        assert after[loc] == value == (value[0], DECLARED_VERSION), loc
    by_loc = _by_locator(structure)
    assert by_loc["body/sec[2]/sec[1]"].roles == ["other"]   # not methods, not both
    assert by_loc["body/sec[2]/sec[3]"].roles == ["methods"]         # not other
    assert by_loc["body/sec[5]"].roles == ["discussion"]            # rules filled it
    assert by_loc["body/sec[5]"].role_source == RULES_VERSION
    assert all(s.roles is not None for s in structure.sections)
    validate_structure(structure, text, require_roles=True)


# --------------------------------------------------------------------- blocks

def test_block_kinds():
    text, spans, structure = _build("rich")
    got = {text[b.start:b.end]: b.kind for b in structure.blocks}
    assert got["Introductory prose."] == "prose"
    assert got["2."] == "prose"                                     # <label>
    assert got["Alpha.Beta."] == "list"
    assert got["E = m c 2"] == "math"
    assert got["raw preformatted text"] == "code"
    assert got["print(1)"] == "code"
    assert got["A quoted passage."] == "quote"
    assert got["An unknown leaf tag."] == "prose"                   # unknown tag
    assert got["Introduction"] == "heading"
    abstract = spans[0]
    assert got[text[abstract.start:abstract.end]] == "prose"


def test_paragraph_with_an_inline_float_is_one_prose_block():
    """The <table-wrap> nested in "Lead paragraph two" is pruned from the prose;
    the paragraph stays ONE prose block whose text is the record's paragraph."""
    text, spans, structure = _build("rich")
    span = next(s for s in spans if s.path == "body/p[2]")
    para = text[span.start:span.end]
    assert para.startswith("Lead paragraph two holds a float")
    assert para.endswith("in the middle.") and "ZZCELLZZ" not in para
    record = jats.article_records(ET.fromstring(FIXTURES["rich"]), "PMC60")[0][0]
    assert record["text"] == text and para in record["text"]
    hits = [b for b in structure.blocks if b.start < span.end and span.start < b.end]
    assert [(b.start, b.end, b.kind) for b in hits] == [(span.start, span.end, "prose")]


@pytest.mark.parametrize("name", ["with_floats", "floats_group", "two_tables", "rich"])
def test_floats_leave_no_block(name):
    text, spans, structure = _build(name)
    assert "ZZCELLZZ" not in text and "Survival of the synthetic cohort" not in text
    # One block per non-container span, nothing for the floats.
    assert len(structure.blocks) == sum(1 for s in spans if s.kind != "sec")
    assert {b.kind for b in structure.blocks} <= {
        "prose", "heading", "list", "quote", "math", "code"}
    for b in structure.blocks:
        assert b.label is None


def test_sha_binds_text():
    text, _, structure = _build("rich")
    assert structure.content_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    with pytest.raises(StructureInvalid) as e:
        validate_structure(structure, text + " ")
    assert e.value.reason == "content_sha256"
