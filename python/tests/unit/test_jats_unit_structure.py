"""``jats/1`` unit-record structures (#828 §9.2, PR 5b part ii).

``table_units`` / ``fig_units`` record a :class:`~ragstack.ingestion.jats.UnitLayout`
per emitted piece from the lengths of the strings they join, and
``jats_structure.unit_structure`` turns one into a one-section structure: a
``heading`` over the leading label, the caption block with the normalised
label, the ``table`` block over the grid. These tests pin that the result
passes ``validate_structure`` for every piece of every fixture (split tables,
bitmap-only and label-less floats, oversized captions, token-budget and char
builds), that the supplementary role comes only from a
``<supplementary-material>`` ancestor, and that a rebuild never leaves a stale
part's layout behind. Synthetic JATS only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ragstack.ingestion import jats
from ragstack.ingestion.jats import UnitLayout
from ragstack.ingestion.jats_structure import ADAPTER, unit_structure
from ragstack.ingestion.structure import DECLARED_VERSION, StructureInvalid, validate_structure

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
from test_jats_extract import FIG_1, TABLE_1, _article  # noqa: E402

_ROWS = "".join(f"<tr><td>row {i} alpha beta gamma</td><td>{i * 7} units measured here</td></tr>"
                for i in range(12))

#: 12 rows: three pieces at a 300-char cap, two at 400, one at the default.
BIG_TABLE = f"""
  <table-wrap id="T4">
    <label>Table 4</label>
    <caption><p>A big table of many rows.</p></caption>
    <table><thead><tr><th>Name</th><th>Value</th></tr></thead><tbody>{_ROWS}</tbody></table>
    <table-wrap-foot><p>Foot note on the big table.</p></table-wrap-foot>
  </table-wrap>
"""

#: A bitmap-only table (no <table>), with a caption and a foot.
BITMAP_TABLE = """
  <table-wrap id="T5">
    <label>Table 5</label>
    <caption><p>A scanned table that only exists as an image of the original.</p></caption>
    <graphic xlink:href="t5.jpg"/>
    <table-wrap-foot><p>Abbreviations are defined in the methods.</p></table-wrap-foot>
  </table-wrap>
"""

#: A bitmap-only figure with neither label nor caption text beyond a label.
LABEL_ONLY_FIG = '<fig id="F9"><label>Figure 9</label><graphic xlink:href="f9.jpg"/></fig>'

#: A label-less table: an untitled section whose caption block starts at 0.
UNLABELLED_TABLE = """
  <table-wrap id="T6">
    <caption><p>A table without any label at all, only this caption.</p></caption>
    <table><tbody><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></tbody></table>
  </table-wrap>
"""

_LONG = " ".join(f"Sentence number {i} of an extremely long figure legend." for i in range(80))

#: An oversized caption (split like prose, the label repeated on every piece),
#: under a label the normaliser rewrites.
LONG_FIG = f"""
  <fig id="F2">
    <label>Fig. 2A</label>
    <caption><p>{_LONG}</p></caption>
    <graphic xlink:href="f2.jpg"/>
  </fig>
"""

#: One figure inside <supplementary-material>, one outside it.
SUPP_BODY = f"""
  <sec><title>Results</title><p>Results text.</p>{FIG_1}</sec>
  <sec sec-type="supplementary-material"><title>Supplementary</title>
    <supplementary-material id="S1">
      <fig id="SF1"><label>Figure S1</label>
        <caption><p>A supplementary figure that sits inside the supplementary material.</p>
        </caption></fig>
    </supplementary-material>
  </sec>
"""

FIXTURES = {
    "basic": _article("70", f"<sec><title>R</title><p>Prose.</p>{TABLE_1}</sec>", FIG_1),
    "big": _article("71", "<p>Prose.</p>", BIG_TABLE),
    "bitmap": _article("72", "<p>Prose.</p>", BITMAP_TABLE + LABEL_ONLY_FIG),
    "unlabelled": _article("73", "<p>Prose.</p>", UNLABELLED_TABLE),
    "long_fig": _article("74", "<p>Prose.</p>", LONG_FIG),
    "supp": _article("75", SUPP_BODY),
}


def _words(s: str) -> int:
    """A deterministic stand-in tokenizer (about 1 token per 2.5 chars)."""
    return max(1, len(s) * 2 // 5)


#: (max_chars, count_tokens, max_tokens): char builds at the default and at
#: two small caps, and a token-budget build that splits on the counter.
BUILDS = {
    "chars": (jats.DEFAULT_MAX_CHARS, None, jats.DEFAULT_MAX_TOKENS),
    "chars300": (300, None, jats.DEFAULT_MAX_TOKENS),
    "chars400": (400, None, jats.DEFAULT_MAX_TOKENS),
    "tokens": (jats.DEFAULT_MAX_CHARS, _words, 160),
}


def _units(name: str, build: str = "chars"):
    max_chars, counter, max_tokens = BUILDS[build]
    records, _, _, units = jats.article_records(
        ET.fromstring(FIXTURES[name]), f"PMC{name}", None, max_chars, 1, counter,
        max_tokens, with_spans=True)
    out = []
    for r in records:
        kind = r["metadata"]["content_type"]
        if kind == "article":
            assert r["path"] not in units
            continue
        layout, supplementary = units[r["path"]]
        out.append((r, layout, supplementary,
                    unit_structure(r["text"], layout, kind=kind, supplementary=supplementary)))
    return out


# ------------------------------------------------------------------ validity

@pytest.mark.parametrize("build", sorted(BUILDS))
@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_piece_of_every_fixture_is_valid(name, build):
    got = _units(name, build)
    assert got
    for rec, layout, _sup, structure in got:
        text = rec["text"]
        validate_structure(structure, text)
        assert structure.adapter == ADAPTER and structure.structure_source == "parsed"
        (sec,) = structure.sections
        assert (sec.start, sec.end, sec.level, sec.rank, sec.locator) == (0, len(text), 1,
                                                                          None, None)
        if layout.label:
            head = structure.blocks[0]
            assert head.kind == "heading"
            assert text[head.start:head.end] == layout.label == sec.title
            assert sec.path == [layout.label]
        kinds = [b.kind for b in structure.blocks]
        ct = rec["metadata"]["content_type"]
        assert set(kinds) <= {"heading", f"{ct}_caption", ct}
        if ct == "figure":
            assert "figure" not in kinds                       # a figure has no body


def test_split_table_pieces_repeat_caption_and_cover_the_grid():
    got = _units("big", "chars300")
    assert [r["path"] for r, *_ in got] == [f"PMCbig#table-1-part-{i}" for i in (1, 2, 3)]
    for rec, layout, _sup, structure in got:
        text = rec["text"]
        by_kind = {b.kind: text[b.start:b.end] for b in structure.blocks}
        assert by_kind["heading"] == "Table 4"
        assert by_kind["table_caption"] == "A big table of many rows."
        assert by_kind["table"].startswith("Name | Value\n--- | ---\nrow ")
        assert layout.body_end == len(text)
    assert got[-1][0]["text"].endswith("Foot note on the big table.")
    assert "Foot note" not in got[0][0]["text"]
    labels = {b.label for *_, s in got for b in s.blocks if b.kind == "table_caption"}
    assert labels == {"Table 4"}


def test_single_table_layout():
    (rec, layout, _sup, structure), = [u for u in _units("basic") if "table" in u[0]["path"]]
    text = rec["text"]
    assert layout == UnitLayout("Table 1", 8, 8 + len("Counts by treatment group."),
                                len("Table 1 Counts by treatment group.") + 2, len(text))
    table = next(b for b in structure.blocks if b.kind == "table")
    assert text[table.start:].startswith("Group | Count")
    assert text[table.start:table.end].endswith("Values are ZZFOOTZZ counts.")


def test_bitmap_and_label_only_floats():
    got = {r["path"]: (r, lay, s) for r, lay, _sup, s in _units("bitmap")}
    rec, layout, structure = got["PMCbitmap#table-1"]
    assert layout.body_start is None and layout.body_end is None
    assert [b.kind for b in structure.blocks] == ["heading", "table_caption"]
    cap = structure.blocks[1]
    assert rec["text"][cap.start:cap.end].startswith("A scanned table")
    assert rec["text"][cap.start:cap.end].endswith("defined in the methods.")
    # The label alone: a titled section with only its heading block.
    rec, layout, structure = got["PMCbitmap#figure-1"]
    assert rec["text"] == "Figure 9"
    assert layout == UnitLayout("Figure 9", 8, 8, None, None)
    assert [(b.start, b.end, b.kind) for b in structure.blocks] == [(0, 8, "heading")]
    validate_structure(structure, rec["text"])


def test_oversized_caption_splits_with_label_heading_and_normalised_label():
    for build in ("chars", "tokens"):
        got = _units("long_fig", build)
        assert len(got) > 1, build
        for rec, _layout, _sup, structure in got:
            text = rec["text"]
            assert text.startswith("Fig. 2A ")
            head, cap = structure.blocks
            assert text[head.start:head.end] == "Fig. 2A"                   # raw label
            assert (cap.kind, cap.label, cap.start, cap.end) == (
                "figure_caption", "Figure 2", len("Fig. 2A "), len(text))  # normalised
            assert text[cap.start:cap.end].startswith("Sentence number")


def test_label_less_table_is_untitled_with_caption_at_zero():
    (rec, layout, _sup, structure), = _units("unlabelled")
    (sec,) = structure.sections
    assert sec.title is None and sec.path == []
    assert layout.label == ""
    cap = structure.blocks[0]
    assert (cap.kind, cap.start, cap.label) == ("table_caption", 0, None)
    assert rec["text"][cap.start:cap.end] == \
        "A table without any label at all, only this caption."
    assert [b.kind for b in structure.blocks] == ["table_caption", "table"]
    assert "heading" not in {b.kind for b in structure.blocks}


# ---------------------------------------------------------------------- roles

def test_supplementary_role_only_inside_supplementary_material():
    """MUTATION: ``_supplementary`` always True → the outside figure is
    declared supplementary → fails. Always False → the inside one is not →
    fails."""
    got = {r["path"]: (sup, s.sections[0]) for r, _lay, sup, s in _units("supp")}
    outside_sup, outside = got["PMCsupp#figure-1"]
    inside_sup, inside = got["PMCsupp#figure-2"]
    assert inside.title == "Figure S1" and outside.title == "Figure 1"
    assert (inside_sup, inside.roles, inside.role_source) == (
        True, ["supplementary"], DECLARED_VERSION)
    assert (outside_sup, outside.roles, outside.role_source) == (False, None, None)


# --------------------------------------------------------------- stale parts

def test_a_rebuild_leaves_no_stale_part_layout(monkeypatch):
    """``_fit_pieces`` rebuilds pieces; a first attempt with three parts and a
    final build with two must leave exactly the two final layouts.
    MUTATION: drop the ``_reset_layouts`` call in ``build`` → ``part-3``
    survives → fails."""
    tw = ET.fromstring(BIG_TABLE)
    assert len(jats.table_units(tw, 1, 300)) == 3 and len(jats.table_units(tw, 1, 400)) == 2

    def two_attempts(build, text_of, whole, max_chars, count_tokens, max_tokens):
        build(300)
        return build(400)

    monkeypatch.setattr(jats, "_fit_pieces", two_attempts)
    layouts: dict[str, UnitLayout] = {"table-2": UnitLayout("x", 0, 0, None, None)}
    pieces = jats.table_units(tw, 1, layouts=layouts)
    assert [s for s, _ in pieces] == ["table-1-part-1", "table-1-part-2"]
    # Another unit's entry in a shared dict is left alone.
    assert sorted(layouts) == ["table-1-part-1", "table-1-part-2", "table-2"]
    for s, t in pieces:
        validate_structure(unit_structure(t, layouts[s], kind="table", supplementary=False), t)


def test_layouts_do_not_change_the_pieces():
    for xml in (BIG_TABLE, BITMAP_TABLE, UNLABELLED_TABLE, TABLE_1):
        el = ET.fromstring(xml.replace('xlink:href', 'href'))
        for cap in (300, 400, jats.DEFAULT_MAX_CHARS):
            layouts: dict[str, UnitLayout] = {}
            assert jats.table_units(el, 3, cap, layouts=layouts) == jats.table_units(el, 3, cap)
            assert set(layouts) == {s for s, _ in jats.table_units(el, 3, cap)}
    for xml in (LONG_FIG, FIG_1):
        el = ET.fromstring(xml.replace('xlink:href', 'href'))
        layouts = {}
        assert jats.fig_units(el, 1, layouts=layouts) == jats.fig_units(el, 1)
        assert set(layouts) == {p[0] for p in jats.fig_units(el, 1)}


# -------------------------------------------------------------- wrong pairing

def test_a_layout_paired_with_the_wrong_text_is_refused():
    (rec, layout, _sup, _s), = [u for u in _units("basic") if "table" in u[0]["path"]]
    with pytest.raises(ValueError):
        unit_structure("X" + rec["text"], layout, kind="table", supplementary=False)
    with pytest.raises(ValueError):
        unit_structure(rec["text"][:20], layout, kind="table", supplementary=False)
    with pytest.raises(StructureInvalid):          # wrong text, same label: the sha
        s = unit_structure(rec["text"], layout, kind="table", supplementary=False)
        validate_structure(s, rec["text"] + " ")
