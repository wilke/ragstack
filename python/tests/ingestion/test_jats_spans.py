"""Element -> offset spans over the JATS article record text (#318 P3, #688 §5 step 1).

``jats.article_spans(root)`` returns ``(prose_text, spans)``. These tests pin:

* IDENTITY — ``prose_text`` is the article record's ``text`` byte for byte, and
  ``article_prose``/``section_text`` (now thin wrappers over the span walk) still
  produce exactly what the pre-#318 implementation produced. The pre-change code
  is frozen below as ``_ref_*`` and compared on every fixture.
* ROUND TRIP — every span slices back to its element's normalised text, and the
  ``path`` resolves to that element.
* TILING — leaf spans are sorted, disjoint, and cover the text with exactly one
  ``"\\n\\n"`` between neighbours; depth-0 spans do the same one level up.
* the structural cases the section chunker needs: untitled ``<sec>``, body-lead
  paragraphs, nesting depth, floats nested in ``<p>``, ``<floats-group>``, no
  ``<body>``, elements that normalise to empty (they get NO span).

Synthetic JATS only (plus the shared fixtures of ``test_jats_extract.py``).
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest
from test_jats_extract import (  # same dir; pytest puts it on sys.path
    BODY_WITH_FLOATS,
    FIG_1,
    TABLE_1,
    _article,
    _big_table,
)

from ragstack.ingestion import jats

# ------------------------------------------------- frozen pre-change reference
# Verbatim copies of section_text / article_prose as they were before the span
# walk replaced them (origin/main 0b43ebb). Do not "fix" these: they are the
# definition of byte-identity.


def _ref_section_text(sec, depth: int = 0) -> str:
    parts: list[str] = []
    title = sec.find("title")
    if title is not None:
        t = jats.itext(title)
        if t:
            parts.append(("#" * min(depth + 2, 6)) + " " + t)
    for child in sec:
        if child.tag == "title":
            continue
        if child.tag in jats.LIFT:
            continue
        if child.tag == "sec":
            parts.append(_ref_section_text(child, depth + 1))
        else:
            parts.append(jats.clean_itext(child))
    return "\n\n".join(p for p in parts if p)


def _ref_article_prose(root) -> tuple[str, str]:
    abst = root.find(".//article-meta//abstract")
    abstract = jats.clean_itext(abst)
    body = root.find(".//body")
    if body is None:
        return abstract, ""
    chunks: list[str] = []
    for child in body:
        if child.tag in jats.LIFT:
            continue
        chunks.append(_ref_section_text(child) if child.tag == "sec"
                      else jats.clean_itext(child))
    return abstract, "\n\n".join(c for c in chunks if c)


# ------------------------------------------------------------------- fixtures

REPEATED = "The identical sentence appears in two sections."

#: Every structural case in one article. Comments name what each part exercises.
SYNTHETIC_BODY = f"""
    <p id="lead1">A body-lead paragraph before any section.</p>
    <p>   </p>
    <p>Second   lead​ paragraph — with lookalikes.</p>
    <sec id="s1">
      <label>1.</label>
      <title>Introduction</title>
      <p id="p-intro">{REPEATED}</p>
      <p>A paragraph nesting a float: before {TABLE_1} after.</p>
      <p>{TABLE_1}</p>
      {FIG_1}
      <sec id="s1-1">
        <title>Background</title>
        <p>Nested one level.</p>
        <sec>
          <title>Deeper</title>
          <p>Nested two levels.</p>
        </sec>
      </sec>
    </sec>
    <sec id="s2">
      <p>{REPEATED}</p>
      <p>An untitled section, so no heading marks its boundary.</p>
    </sec>
    <sec>
      <title>  </title>
      <p>Section whose title normalises to empty.</p>
    </sec>
    <sec><title>Empty</title></sec>
    <sec><p> </p></sec>
    <sec>
      <title>Discussion</title>
      <list><list-item><p>Item one.</p></list-item><list-item><p>Item two.</p></list-item></list>
    </sec>
"""


def _no_body(n: str = "50") -> str:
    xml = _article(n, "")
    return xml.replace("<body></body>", "")


FIXTURES = {
    # The article shapes of test_jats_extract.py, lifted as-is.
    "with_floats": _article("1", BODY_WITH_FLOATS),
    "floats_group": _article(
        "2", "<sec><title>Intro</title><p>Body prose with no floats at all.</p></sec>",
        TABLE_1 + FIG_1),
    "two_tables": _article(
        "3", "<sec><title>Results</title><p>Prose.</p>" + TABLE_1 + TABLE_1 + FIG_1
        + "</sec>"),
    "big_table": _article(
        "5", "<sec><title>Results</title><p>Prose.</p>" + _big_table() + "</sec>"),
    "tiny_unit": _article(
        "7", "<sec><title>Results</title><p>Prose paragraph.</p>"
        '<table-wrap id="TT"><label>Table 2</label><caption><p>Flow chart.</p>'
        f"</caption></table-wrap>{TABLE_1}</sec>"),
    "no_prose": (
        '<article xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<front><article-meta><article-id pub-id-type="pmc">PMC8</article-id>'
        f"</article-meta></front><body></body><floats-group>{TABLE_1}</floats-group>"
        "</article>"),
    # New.
    "synthetic": _article("40", SYNTHETIC_BODY, TABLE_1),
    "no_body": _no_body(),
    "no_abstract": _article("41", SYNTHETIC_BODY).replace(
        "<abstract><p>The abstract of article 41 describes the synthetic study.</p>"
        "</abstract>", ""),
}


def _root(name: str):
    return ET.fromstring(FIXTURES[name])


def _resolve(root, path: str):
    """A span path -> its element, through the anchor ``article_prose`` uses."""
    head, _, rest = path.partition("/")
    if head == "abstract":
        assert rest == ""
        return root.find(".//article-meta//abstract")
    assert head == "body"
    el = root.find(".//body").find(rest)
    assert el is not None, path
    return el


# ---------------------------------------------------------------- identity

@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_prose_text_is_the_article_record_text(name):
    root = _root(name)
    text, _ = jats.article_spans(root)
    records, _ = jats.article_records(root, "PMCX")
    art = [r for r in records if r["metadata"]["content_type"] == "article"]
    assert text == (art[0]["text"] if art else "")


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_wrappers_are_byte_identical_to_the_pre_change_code(name):
    root = _root(name)
    assert jats.article_prose(root) == _ref_article_prose(root)
    for depth, sec in _all_secs(root):
        assert jats.section_text(sec, depth) == _ref_section_text(sec, depth)
        # section_text's depth argument is free; any value must still agree.
        assert jats.section_text(sec, depth + 3) == _ref_section_text(sec, depth + 3)
    ab, body = _ref_article_prose(root)
    assert jats.article_spans(root)[0] == "\n\n".join(x for x in (ab, body) if x)


def _all_secs(root):
    out = []
    body = root.find(".//body")

    def walk(e, depth):
        for c in e:
            if c.tag == "sec":
                out.append((depth, c))
                walk(c, depth + 1)

    if body is not None:
        walk(body, 0)
    return out


def test_with_spans_leaves_records_unchanged_and_returns_spans_in_parallel():
    root = _root("synthetic")
    plain = jats.article_records(root, "PMC40")
    recs, skipped, spans = jats.article_records(root, "PMC40", with_spans=True)
    assert (recs, skipped) == plain
    assert spans == jats.article_spans(root)[1]
    for r in recs:                       # nothing leaks into the stored shape
        assert set(r) == {"text", "path", "metadata"}
        assert "spans" not in r["metadata"]


def test_with_spans_on_an_article_without_prose_is_empty(tmp_path):
    p = tmp_path / "PMC8.xml"
    p.write_text(FIXTURES["no_prose"], encoding="utf-8")
    recs, skipped, spans = jats.convert_file(p, with_spans=True)
    assert spans == [] and [s["kind"] for s in skipped] == ["prose"]
    bad = tmp_path / "PMC9.xml"
    bad.write_text("<article><body><p>trunc", encoding="utf-8")
    assert jats.convert_file(bad, with_spans=True) == (
        [], jats.convert_file(bad)[1], [])


# --------------------------------------------------------------- round trip

@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_span_round_trips_to_its_element(name):
    root = _root(name)
    text, spans = jats.article_spans(root)
    for s in spans:
        assert 0 <= s.start < s.end <= len(text), s
        seg = text[s.start:s.end]
        el = _resolve(root, s.path)
        assert s.xml_id == el.get("id")
        if s.kind == "abstract":
            assert seg == jats.clean_itext(el)
        elif s.kind == "sec":
            assert seg == jats.section_text(el, s.depth)
        elif s.kind == "title":
            assert el.tag == "title" and seg == "#" * min(s.depth + 1, 6) + " " + s.title
        else:
            assert el.tag == s.kind and seg == jats.clean_itext(el)


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_leaves_and_top_level_spans_tile_the_text(name):
    text, spans = jats.article_spans(_root(name))
    assert [s.start for s in spans] == sorted(s.start for s in spans)
    for layer in ([s for s in spans if s.kind != "sec"],    # leaves
                  [s for s in spans if s.depth == 0]):     # outermost partition
        pos = 0
        for i, s in enumerate(layer):
            assert text[pos:s.start] == ("" if i == 0 else "\n\n"), (s, pos)
            pos = s.end
        assert pos == len(text)


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_sections_cover_exactly_their_children(name):
    _, spans = jats.article_spans(_root(name))
    for i, sec in enumerate(spans):
        if sec.kind != "sec":
            continue
        kids = [s for s in spans if s.path.startswith(sec.path + "/")]
        assert kids and spans[i + 1] == kids[0]            # pre-order
        assert kids[0].start == sec.start and max(k.end for k in kids) == sec.end
        assert all(k.depth > sec.depth for k in kids)


# ------------------------------------------------------- the structural cases

def test_repeated_sentence_gets_its_own_offset_in_each_section():
    text, spans = jats.article_spans(_root("synthetic"))
    hits = [s for s in spans if s.kind == "p" and text[s.start:s.end] == REPEATED]
    assert [h.path for h in hits] == ["body/sec[1]/p[1]", "body/sec[2]/p[1]"]
    first, second = hits
    assert first.start != second.start
    # String search would have mapped the second section's sentence onto the
    # first section's occurrence — exactly the aliasing #318 P3 forbids.
    assert text.find(REPEATED) == first.start
    assert text.find(REPEATED) != second.start
    assert text.find(REPEATED, first.end) == second.start


def test_untitled_sections_and_body_lead_paragraphs_are_first_class():
    text, spans = jats.article_spans(_root("synthetic"))
    by_path = {s.path: s for s in spans}

    lead = [s for s in spans if s.depth == 0 and s.kind == "p"]
    assert [s.path for s in lead] == ["body/p[1]", "body/p[3]"]    # p[2] is blank
    assert lead[0].xml_id == "lead1" and lead[0].title is None
    assert text[lead[1].start:lead[1].end] == "Second lead paragraph - with lookalikes."

    untitled = by_path["body/sec[2]"]
    assert untitled.title is None and untitled.depth == 0 and untitled.xml_id == "s2"
    assert not text[untitled.start:untitled.end].startswith("#")   # no heading in text
    blank_title = by_path["body/sec[3]"]
    assert blank_title.title is None                               # "  " -> None
    assert "body/sec[3]/title[1]" not in by_path

    titled = by_path["body/sec[1]"]
    assert titled.title == "Introduction" and titled.xml_id == "s1"
    assert by_path["body/sec[1]/title[1]"].title == "Introduction"
    assert text[titled.start:].startswith("## Introduction")
    # <label> comes before <title> in the XML but the heading leads the text.
    assert by_path["body/sec[1]/label[1]"].start > by_path["body/sec[1]/title[1]"].start


def test_nested_section_depths():
    _, spans = jats.article_spans(_root("synthetic"))
    by_path = {s.path: s for s in spans}
    assert by_path["body/sec[1]"].depth == 0
    assert by_path["body/sec[1]/p[1]"].depth == 1
    assert by_path["body/sec[1]/sec[1]"].depth == 1
    assert by_path["body/sec[1]/sec[1]"].title == "Background"
    assert by_path["body/sec[1]/sec[1]/sec[1]"].depth == 2
    assert by_path["body/sec[1]/sec[1]/sec[1]/p[1]"].depth == 3
    text = jats.article_spans(_root("synthetic"))[0]
    deeper = by_path["body/sec[1]/sec[1]/sec[1]"]
    assert text[deeper.start:deeper.end].startswith("#### Deeper")   # depth 2 -> ####


def test_float_nested_in_p_is_lifted_and_the_paragraph_still_round_trips():
    root = _root("synthetic")
    text, spans = jats.article_spans(root)
    by_path = {s.path: s for s in spans}
    p = by_path["body/sec[1]/p[2]"]
    seg = text[p.start:p.end]
    assert seg == jats.clean_itext(_resolve(root, p.path))
    # (norm keeps the newlines of the float's surrounding tails; pre-existing.)
    assert " ".join(seg.split()) == "A paragraph nesting a float: before after."
    assert "ZZCELLZZ" not in text and "Survival of the synthetic cohort" not in text
    # A <p> holding ONLY a float normalises to empty: no span, no separator.
    assert "body/sec[1]/p[3]" not in by_path
    assert not any(s.kind in jats.LIFT for s in spans)
    # ...but the next sibling's path still counts it (paths count every sibling).
    assert "body/sec[1]/sec[1]" in by_path


def test_floats_group_outside_body_leaves_no_span():
    root = _root("floats_group")
    _, spans = jats.article_spans(root)
    assert [s.path for s in spans] == [
        "abstract", "body/sec[1]", "body/sec[1]/title[1]", "body/sec[1]/p[1]"]
    assert all(s.path.split("/")[0] in ("abstract", "body") for s in
               jats.article_spans(_root("synthetic"))[1])


def test_article_without_body_gives_only_the_abstract_span():
    text, spans = jats.article_spans(_root("no_body"))
    assert [s.kind for s in spans] == ["abstract"]
    assert (spans[0].start, spans[0].end) == (0, len(text))
    assert spans[0].depth == 0 and spans[0].title is None


def test_no_abstract_body_starts_at_zero():
    text, spans = jats.article_spans(_root("no_abstract"))
    assert spans[0].path == "body/p[1]" and spans[0].start == 0
    assert text.startswith("A body-lead paragraph")


def test_empty_after_norm_elements_get_no_span():
    """Documented policy: an element that normalises to "" is dropped by the
    ``if p`` join filter, takes no separator, and gets NO span."""
    text, spans = jats.article_spans(_root("synthetic"))
    paths = {s.path for s in spans}
    assert "body/p[2]" not in paths                    # whitespace-only <p>
    assert "body/sec[5]" not in paths                  # <sec><p> </p></sec>
    assert "body/sec[5]/p[1]" not in paths
    assert all(s.start < s.end for s in spans)
    assert "\n\n\n" not in text


def test_title_only_section_is_a_span_of_just_its_heading():
    text, spans = jats.article_spans(_root("synthetic"))
    by_path = {s.path: s for s in spans}
    # body/sec[4] is <sec><title>Empty</title></sec>: the heading is its text.
    s = by_path.get("body/sec[4]")
    assert s is not None and text[s.start:s.end] == "## Empty" and s.title == "Empty"


def test_span_rows_are_json_shaped():
    _, spans = jats.article_spans(_root("synthetic"))
    row = spans[0]._asdict()
    assert list(row) == ["kind", "path", "xml_id", "start", "end", "title", "depth"]
    assert row == {"kind": "abstract", "path": "abstract", "xml_id": None,
                   "start": 0, "end": row["end"], "title": None, "depth": 0}


# ------------------------------------------------------------------ CLI flag

def test_spans_out_writes_one_row_per_article_and_leaves_the_shard_alone(tmp_path):
    import json

    import jats_extract  # scripts/ is on sys.path via test_jats_extract

    corpus = tmp_path / "corpus"
    clean = corpus / "clean" / "ab"
    clean.mkdir(parents=True)
    for n, name in (("40", "synthetic"), ("8", "no_prose")):
        (clean / f"PMC{n}.xml").write_text(FIXTURES[name], encoding="utf-8")
    (corpus / "manifest.jsonl").write_text("", encoding="utf-8")

    plain, with_spans, rows = (tmp_path / "a.jsonl", tmp_path / "b.jsonl",
                               tmp_path / "spans.jsonl")
    assert jats_extract.main(["--corpus", str(corpus), "--out", str(plain),
                              "--no-token-budget", "--max-fail-rate", "0"]) == 0
    assert not rows.exists()
    assert jats_extract.main(["--corpus", str(corpus), "--out", str(with_spans),
                              "--no-token-budget", "--max-fail-rate", "0",
                              "--spans-out", str(rows)]) == 0
    assert plain.read_bytes() == with_spans.read_bytes()

    got = [json.loads(x) for x in rows.read_text(encoding="utf-8").splitlines()]
    assert [r["pmcid"] for r in got] == ["PMC40"]          # PMC8 has no prose
    article = next(json.loads(x) for x in plain.read_text().splitlines()
                   if json.loads(x)["path"] == "PMC40")
    text = article["text"]
    expected = [s._asdict() for s in jats.article_spans(_root("synthetic"))[1]]
    assert got[0]["spans"] == expected
    for s in got[0]["spans"]:
        assert 0 <= s["start"] < s["end"] <= len(text)
