"""Offline tests for the JATS -> JSONL extraction tool (#301).

``scripts/jats_extract.py`` runs the pure parser in ``ragstack/ingestion/jats.py``
over PubMed-Central JATS XML and emits a JSONL shard in the ``{text, path,
metadata}`` shape JsonlLoader (and thus embed_shard / ingest_shard) consume, plus
a sidecar skip report. These tests prove the behaviours the out-of-tree converter
was validated on:

* two record kinds (``article`` + ``table``/``figure``) with the right ``content_type``;
* every ``<table-wrap>``/``<fig>`` is lifted OUT of the prose flow, at any depth —
  the article record must not contain the table grid;
* ``<floats-group>`` floats (MDPI parks them outside ``<body>``) are still captured;
* one unique ``path`` per unit, because JsonlLoader derives the doc id from it;
* an oversized table is split BY ROW with caption+header repeated, no piece over
  the cap;
* units under ``--min-unit-chars`` are counted in the report, not silently dropped;
* ``--shard`` consumes exactly the listed articles, with the line's own keys as
  that article's manifest row;
* a corrupt XML is reported and does not sink the rest of the shard.

Synthetic JATS only — no corpus, no network, no embedding fleet.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# jats_extract.py lives under python/scripts (same convention as test_pdf_extract).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import jats_extract  # noqa: E402

from ragstack.ingestion import jats  # noqa: E402

FRONT = """
  <front>
    <journal-meta>
      <journal-title-group><journal-title>J Synthetic Res</journal-title></journal-title-group>
      <publisher><publisher-name>Test Publisher</publisher-name></publisher>
    </journal-meta>
    <article-meta>
      <article-id pub-id-type="pmc">PMC{n}</article-id>
      <article-id pub-id-type="pmid">99{n}</article-id>
      <article-id pub-id-type="doi">10.1234/synth.{n}</article-id>
      <title-group><article-title>A synthetic article {n}</article-title></title-group>
      <contrib-group>
        <contrib contrib-type="author">
          <name><surname>Doe</surname><given-names>Jane</given-names></name>
        </contrib>
        <contrib contrib-type="author">
          <name><surname>Roe</surname><given-names>Rick</given-names></name>
        </contrib>
      </contrib-group>
      <pub-date pub-type="epub"><year>2024</year></pub-date>
      <permissions>
        <license xlink:href="https://creativecommons.org/licenses/by/4.0/">
          <license-p>Open Access under CC BY 4.0.</license-p>
        </license>
      </permissions>
      <abstract><p>The abstract of article {n} describes the synthetic study.</p></abstract>
      <kwd-group><kwd>synthetic</kwd><kwd>jats</kwd></kwd-group>
    </article-meta>
  </front>
"""

TABLE_1 = """
      <table-wrap id="T1">
        <label>Table 1</label>
        <caption><p>Counts by treatment group.</p></caption>
        <table>
          <thead><tr><th>Group</th><th>Count</th></tr></thead>
          <tbody>
            <tr><td>ZZCELLZZ</td><td>17</td></tr>
            <tr><td>control</td><td>19</td></tr>
          </tbody>
        </table>
        <table-wrap-foot><p>Values are ZZFOOTZZ counts.</p></table-wrap-foot>
      </table-wrap>
"""

FIG_1 = """
      <fig id="F1">
        <label>Figure 1</label>
        <caption><p>Survival of the synthetic cohort over twelve months of follow-up.</p></caption>
        <graphic xlink:href="synth-f001.jpg"/>
      </fig>
"""


def _article(n: str = "1", body: str = "", floats_group: str = "") -> str:
    """Wrap a body (and optional floats-group) in a minimal but realistic JATS doc."""
    fg = f"<floats-group>{floats_group}</floats-group>" if floats_group else ""
    return (
        '<article xmlns:xlink="http://www.w3.org/1999/xlink" article-type="research-article">'
        + FRONT.format(n=n)
        + f"<body>{body}</body>{fg}</article>"
    )


def _write(tmp_path: Path, name: str, xml: str) -> Path:
    p = tmp_path / name
    p.write_text(xml, encoding="utf-8")
    return p


BODY_WITH_FLOATS = f"""
    <sec>
      <title>Results</title>
      <p>The prose sentence lives here and mentions QQPROSEQQ once.</p>
      <p>An inline paragraph that legally nests a float: {TABLE_1}</p>
      {FIG_1}
    </sec>
"""


# --------------------------------------------------------------- record kinds

def test_two_record_kinds_and_content_types(tmp_path: Path):
    xml = _write(tmp_path, "PMC1.xml", _article("1", BODY_WITH_FLOATS))
    records, skipped = jats.convert_file(xml)

    kinds = [r["metadata"]["content_type"] for r in records]
    assert kinds.count("article") == 1
    assert kinds.count("table") == 1
    assert kinds.count("figure") == 1
    assert [s for s in skipped if s["kind"] == "article"] == []

    for rec in records:
        assert set(rec) == {"text", "path", "metadata"}
        assert isinstance(rec["text"], str) and rec["text"].strip()


def test_table_is_lifted_out_of_the_prose_flow(tmp_path: Path):
    """The article record must carry the prose but NOT the grid, even though the
    <table-wrap> is nested inside a <p> (JATS allows it; a direct-children check
    would leave the cell soup spliced into the sentence stream)."""
    xml = _write(tmp_path, "PMC1.xml", _article("1", BODY_WITH_FLOATS))
    records, _ = jats.convert_file(xml)

    article = next(r for r in records if r["metadata"]["content_type"] == "article")
    assert "QQPROSEQQ" in article["text"]
    assert "ZZCELLZZ" not in article["text"]      # no table cells
    assert "ZZFOOTZZ" not in article["text"]      # no table-wrap-foot
    assert "Counts by treatment group" not in article["text"]   # no caption
    assert "Survival of the synthetic cohort" not in article["text"]  # no fig caption
    assert "## Results" in article["text"]        # section titles are kept

    table = next(r for r in records if r["metadata"]["content_type"] == "table")
    assert "ZZCELLZZ | 17" in table["text"]       # grid rendered here instead
    assert "Group | Count" in table["text"]       # header row travels with it
    assert "Table 1 Counts by treatment group." in table["text"]
    assert "ZZFOOTZZ" in table["text"]            # legend kept with the unit

    figure = next(r for r in records if r["metadata"]["content_type"] == "figure")
    assert "Survival of the synthetic cohort" in figure["text"]
    assert figure["metadata"]["graphic"] == "synth-f001.jpg"


def test_floats_group_outside_body_is_captured(tmp_path: Path):
    """MDPI-style: <floats-group> sits outside <body>; a body-only walk loses it."""
    body = "<sec><title>Intro</title><p>Body prose with no floats at all.</p></sec>"
    xml = _write(tmp_path, "PMC2.xml", _article("2", body, TABLE_1 + FIG_1))
    records, _ = jats.convert_file(xml)

    kinds = [r["metadata"]["content_type"] for r in records]
    assert kinds.count("table") == 1
    assert kinds.count("figure") == 1
    table = next(r for r in records if r["metadata"]["content_type"] == "table")
    assert "ZZCELLZZ | 17" in table["text"]


def test_paths_are_unique_per_unit(tmp_path: Path):
    body = "<sec><title>Results</title><p>Prose.</p>" + TABLE_1 + TABLE_1 + FIG_1 + "</sec>"
    xml = _write(tmp_path, "PMC3.xml", _article("3", body))
    records, _ = jats.convert_file(xml)

    paths = [r["path"] for r in records]
    assert len(paths) == len(set(paths))
    assert "PMC3" in paths                    # the article record
    assert "PMC3#table-1" in paths
    assert "PMC3#table-2" in paths            # second table gets its own suffix
    assert "PMC3#figure-1" in paths
    # section_title carries the unit suffix for the unit records.
    unit = next(r for r in records if r["path"] == "PMC3#table-2")
    assert unit["metadata"]["section_title"] == "table-2"


def test_metadata_from_xml_and_manifest(tmp_path: Path):
    xml = _write(tmp_path, "PMC4.xml", _article("4", BODY_WITH_FLOATS))
    manifest = {"sha256": "deadbeef", "source_url": "https://example.org/PMC4.xml"}
    records, _ = jats.convert_file(xml, manifest=manifest)

    article = next(r for r in records if r["metadata"]["content_type"] == "article")
    m = article["metadata"]
    assert m["doi"] == "10.1234/synth.4"
    assert m["pmid"] == "994"
    assert m["pmcid"] == "PMC4"
    assert m["title"] == "A synthetic article 4"
    assert m["authors"] == "Jane Doe; Rick Roe"       # "; "-joined, enrich's contract
    assert m["keywords"] == "synthetic; jats"
    assert m["journal"] == "J Synthetic Res"
    assert m["publisher"] == "Test Publisher"
    assert m["year"] == "2024"
    assert m["licence"].startswith("https://creativecommons.org/licenses/by/4.0/")
    assert m["sha256"] == "deadbeef"
    assert m["source_url"] == "https://example.org/PMC4.xml"
    assert m["n_tables"] == 1 and m["n_figures"] == 1
    assert m["abstract"].startswith("The abstract of article 4")

    # Unit records carry the same bibliographic set (minus the article abstract).
    table = next(r for r in records if r["metadata"]["content_type"] == "table")
    assert table["metadata"]["doi"] == "10.1234/synth.4"
    assert table["metadata"]["sha256"] == "deadbeef"
    assert "abstract" not in table["metadata"]


# ------------------------------------------------------------------ splitting

def _big_table(n_rows: int = 60) -> str:
    rows = "".join(
        f"<tr><td>subject-{i:03d}</td><td>{i}</td><td>value-{i:03d}</td></tr>"
        for i in range(n_rows)
    )
    return f"""
      <table-wrap id="TBIG">
        <label>Table 9</label>
        <caption><p>A large table.</p></caption>
        <table>
          <thead><tr><th>Subject</th><th>N</th><th>Value</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </table-wrap>
    """


def test_oversized_table_split_by_rows_with_header_repeated(tmp_path: Path):
    cap = 400
    body = "<sec><title>Results</title><p>Prose.</p>" + _big_table() + "</sec>"
    xml = _write(tmp_path, "PMC5.xml", _article("5", body))
    records, _ = jats.convert_file(xml, max_chars=cap)

    pieces = [r for r in records if r["metadata"]["content_type"] == "table"]
    assert len(pieces) > 1, "a 60-row table must not fit one 400-char unit"
    paths = [r["path"] for r in pieces]
    assert paths == [f"PMC5#table-1-part-{i}" for i in range(1, len(pieces) + 1)]
    assert len(set(paths)) == len(paths)

    for piece in pieces:
        text = piece["text"]
        assert len(text) <= cap, f"piece over the {cap}-char cap: {len(text)}"
        assert text.startswith("Table 9 A large table.")     # caption repeated
        assert "Subject | N | Value" in text                 # header repeated
        assert "--- | --- | ---" in text
    # Every body row survives the split exactly once.
    joined = "\n".join(p["text"] for p in pieces)
    for i in (0, 17, 59):
        assert joined.count(f"subject-{i:03d}") == 1


def test_token_budget_splits_dense_tables_a_char_cap_would_pass(tmp_path: Path):
    """The 32.2% failure measured on the live corpus: table text tokenizes at
    1.61–4.30 chars/token (p50 2.84), so a unit under the 1,800-CHAR cap can
    still blow past one 512-TOKEN window — whereupon the stock chunker splits it
    with no caption/header context, exactly the contamination the lift-out
    prevents. With a token counter, every piece must fit the token budget and
    still carry the caption + header row."""
    # A dense counter: ~1.6 chars/token, the measured p10 for real tables.
    def dense(s: str) -> int:
        return max(1, int(len(s) / 1.6))

    body = "<sec><title>Results</title><p>Prose.</p>" + _big_table(40) + "</sec>"
    xml = _write(tmp_path, "PMC9.xml", _article("9", body))

    # Char cap alone: the ~1.7k-char table passes the 1800-char gate whole...
    char_only, _ = jats.convert_file(xml, max_chars=1800)
    whole = [r for r in char_only if r["metadata"]["content_type"] == "table"]
    assert len(whole) == 1, "fixture must reproduce the failing shape"
    assert dense(whole[0]["text"]) > jats.DEFAULT_MAX_TOKENS, (
        "…while measuring over the token budget — the shape the char cap misses")

    # …with the counter, it is split and every piece fits the budget WITH context.
    records, _ = jats.convert_file(xml, max_chars=1800, count_tokens=dense,
                                   max_tokens=jats.DEFAULT_MAX_TOKENS)
    pieces = [r for r in records if r["metadata"]["content_type"] == "table"]
    assert len(pieces) > 1
    for piece in pieces:
        assert dense(piece["text"]) <= jats.DEFAULT_MAX_TOKENS
        assert piece["text"].startswith("Table 9 A large table.")
        assert "Subject | N | Value" in piece["text"]
    joined = "\n".join(p["text"] for p in pieces)
    for i in (0, 21, 39):
        assert joined.count(f"subject-{i:03d}") == 1


def test_oversized_bitmap_only_table_splits_without_duplicating_caption(tmp_path: Path):
    """A <table-wrap> with no <table> and a footless, over-budget caption must
    split with the short <label> as the repeated prefix — not with the whole
    caption as both prefix and body, which duplicated it into every piece."""
    long_caption = " ".join(f"Sentence number {i} about the imaged assay." for i in range(60))
    tw = f"""
      <table-wrap id="TBMP">
        <label>Table 4</label>
        <caption><p>{long_caption}</p></caption>
      </table-wrap>"""
    body = "<sec><title>R</title><p>Prose.</p>" + tw + "</sec>"
    xml = _write(tmp_path, "PMC11.xml", _article("11", body))
    records, _ = jats.convert_file(xml, max_chars=400)
    pieces = [r for r in records if r["metadata"]["content_type"] == "table"]
    assert len(pieces) > 1
    marker = "Sentence number 0"
    joined = " ".join(p["text"] for p in pieces)
    assert joined.count(marker) == 1, "caption text duplicated across pieces"
    for p in pieces:
        assert p["text"].startswith("Table 4")
        assert len(p["text"]) <= 400


def test_token_budget_never_splits_a_unit_that_fits(tmp_path: Path):
    """A unit inside the token budget is one piece regardless of its char count —
    prose-dense captions must not get chopped just because chars run long."""
    airy = lambda s: max(1, int(len(s) / 6.0))  # noqa: E731 - 6 chars/token
    body = "<sec><title>R</title><p>Prose.</p>" + _big_table(30) + "</sec>"
    xml = _write(tmp_path, "PMC10.xml", _article("10", body))
    records, _ = jats.convert_file(xml, max_chars=900, count_tokens=airy,
                                   max_tokens=jats.DEFAULT_MAX_TOKENS)
    pieces = [r for r in records if r["metadata"]["content_type"] == "table"]
    # ~1.3k chars is over the 900-char cap, but ~215 "tokens" fits the budget.
    assert len(pieces) == 1
    assert pieces[0]["path"] == "PMC10#table-1"


def test_oversized_figure_caption_split_on_sentences(tmp_path: Path):
    cap = 300
    caption = " ".join(f"Panel {i} shows the synthetic measurement series." for i in range(20))
    fig = (f'<fig id="FB"><label>Figure 4</label><caption><p>{caption}</p></caption>'
           '<graphic xlink:href="big.jpg"/></fig>')
    body = f"<sec><title>Results</title><p>Prose.</p>{fig}</sec>"
    xml = _write(tmp_path, "PMC6.xml", _article("6", body))
    records, _ = jats.convert_file(xml, max_chars=cap)

    pieces = [r for r in records if r["metadata"]["content_type"] == "figure"]
    assert len(pieces) > 1
    assert [r["path"] for r in pieces] == [f"PMC6#figure-1-part-{i}"
                                           for i in range(1, len(pieces) + 1)]
    for piece in pieces:
        assert len(piece["text"]) <= cap
        assert piece["text"].startswith("Figure 4")          # label repeated
        assert piece["metadata"]["graphic"] == "big.jpg"     # href on every piece


# ---------------------------------------------------------------- skip report

def test_short_units_are_reported_not_silently_dropped(tmp_path: Path):
    """A caption-only table-wrap (bitmap table) below the floor is reported."""
    tiny = ('<table-wrap id="TT"><label>Table 2</label>'
            '<caption><p>Flow chart.</p></caption></table-wrap>')
    body = f"<sec><title>Results</title><p>Prose paragraph.</p>{tiny}{TABLE_1}</sec>"
    xml = _write(tmp_path, "PMC7.xml", _article("7", body))
    records, skipped = jats.convert_file(xml)

    tables = [r for r in records if r["metadata"]["content_type"] == "table"]
    assert len(tables) == 1                       # only the real table is emitted
    units = [s for s in skipped if s["kind"] == "unit"]
    assert len(units) == 1
    assert units[0]["path"] == "PMC7#table-1"     # the tiny one, by its own path
    assert "shorter than 40 chars" in units[0]["reason"]


def test_article_without_prose_is_reported(tmp_path: Path):
    xml_text = (
        '<article xmlns:xlink="http://www.w3.org/1999/xlink">'
        f"<front><article-meta><article-id pub-id-type=\"pmc\">PMC8</article-id>"
        f"</article-meta></front><body></body><floats-group>{TABLE_1}</floats-group></article>"
    )
    xml = _write(tmp_path, "PMC8.xml", xml_text)
    records, skipped = jats.convert_file(xml)

    assert [r["metadata"]["content_type"] for r in records] == ["table"]
    assert [s["kind"] for s in skipped] == ["prose"]
    assert skipped[0]["path"] == "PMC8"


def test_corrupt_xml_is_reported_not_raised(tmp_path: Path):
    bad = _write(tmp_path, "PMC9.xml", "<article><body><p>truncated")
    records, skipped = jats.convert_file(bad)

    assert records == []
    assert len(skipped) == 1
    assert skipped[0]["kind"] == "article"
    assert skipped[0]["reason"].startswith("parse error:")


def test_missing_file_is_reported_not_raised(tmp_path: Path):
    records, skipped = jats.convert_file(tmp_path / "nope.xml")
    assert records == []
    assert skipped[0]["kind"] == "article" and skipped[0]["reason"]


# ---------------------------------------------------------------- CLI / shard

def _corpus(tmp_path: Path, ids=("1", "2", "3")) -> Path:
    """A miniature harvest tree: clean/<fan>/PMC<n>.xml + manifest.jsonl."""
    corpus = tmp_path / "corpus"
    clean = corpus / "clean" / "ab" / "cd"
    clean.mkdir(parents=True)
    for n in ids:
        (clean / f"PMC{n}.xml").write_text(_article(n, BODY_WITH_FLOATS), encoding="utf-8")
    corpus.joinpath("manifest.jsonl").write_text(
        "\n".join(json.dumps({"pmcid": f"PMC{n}", "sha256": f"sha-{n}",
                              "source_url": f"https://example.org/PMC{n}.xml"})
                  for n in ids) + "\n",
        encoding="utf-8",
    )
    return corpus


def test_shard_mode_consumes_only_the_listed_articles(tmp_path: Path):
    corpus = _corpus(tmp_path)
    shard = tmp_path / "s0.jsonl"
    shard.write_text(
        json.dumps({"pmcid": "PMC2", "xml_path": "clean/ab/cd/PMC2.xml",
                    "sha256": "from-shard",
                    "source_url": "https://shard.example/PMC2.xml"}) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.jsonl"
    report = tmp_path / "out.report.json"

    rc = jats_extract.main(["--shard", str(shard), "--corpus", str(corpus),
                            "--out", str(out), "--report", str(report)])
    assert rc == 0

    recs = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert {r["metadata"]["pmcid"] for r in recs} == {"PMC2"}   # PMC1/PMC3 untouched
    assert {r["path"] for r in recs} == {"PMC2", "PMC2#table-1", "PMC2#figure-1"}
    # The shard line's own keys are that article's manifest row (no corpus
    # manifest is read in shard mode), so provenance travels with the shard.
    assert {r["metadata"]["sha256"] for r in recs} == {"from-shard"}

    rep = json.loads(report.read_text(encoding="utf-8"))
    assert rep["n_input"] == 1 and rep["n_articles"] == 1 and rep["n_failed"] == 0
    assert rep["by_content_type"] == {"article": 1, "table": 1, "figure": 1}
    assert rep["n_extracted"] == 3


def test_skip_report_flag_alias_matches_the_cwl_binding(tmp_path: Path):
    """jats-ingest.cwl binds --skip-report; pdf_extract's name is --report."""
    corpus = _corpus(tmp_path, ids=("1",))
    out = tmp_path / "out.jsonl"
    report = tmp_path / "out.skips.json"
    rc = jats_extract.main(["--corpus", str(corpus), "--out", str(out),
                            "--skip-report", str(report)])
    assert rc == 0
    assert json.loads(report.read_text(encoding="utf-8"))["n_articles"] == 1


def test_shard_accepts_bare_path_lines_and_absolute_paths(tmp_path: Path):
    corpus = _corpus(tmp_path, ids=("1", "2"))
    shard = tmp_path / "s1.txt"
    shard.write_text(
        f"{corpus / 'clean/ab/cd/PMC1.xml'}\n\nclean/ab/cd/PMC2.xml\n", encoding="utf-8"
    )
    items = jats_extract.read_shard(shard, corpus)
    assert [i["pmcid"] for i in items] == ["PMC1", "PMC2"]
    assert all(Path(i["xml_path"]).is_file() for i in items)


def test_corrupt_article_does_not_sink_the_shard(tmp_path: Path):
    corpus = _corpus(tmp_path, ids=("1", "2"))
    corpus.joinpath("clean/ab/cd/PMC2.xml").write_text("<article><body><p>trunc",
                                                       encoding="utf-8")
    shard = tmp_path / "s2.jsonl"
    shard.write_text("\n".join(
        json.dumps({"pmcid": f"PMC{n}", "xml_path": f"clean/ab/cd/PMC{n}.xml"})
        for n in ("1", "2")) + "\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"
    report = tmp_path / "out.report.json"

    rc = jats_extract.main(["--shard", str(shard), "--corpus", str(corpus),
                            "--out", str(out), "--report", str(report),
                            "--max-fail-rate", "0.6"])
    assert rc == 0

    recs = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert {r["metadata"]["pmcid"] for r in recs} == {"PMC1"}   # good one survives
    rep = json.loads(report.read_text(encoding="utf-8"))
    assert rep["n_failed"] == 1 and rep["n_articles"] == 1
    bad = [s for s in rep["skipped"] if s["kind"] == "article"]
    assert len(bad) == 1 and bad[0]["pmcid"] == "PMC2"
    assert "parse error" in bad[0]["reason"]


def test_fail_rate_guard_trips_but_still_writes_the_good_records(tmp_path: Path):
    corpus = _corpus(tmp_path, ids=("1", "2"))
    corpus.joinpath("clean/ab/cd/PMC2.xml").write_text("<article><body><p>trunc",
                                                       encoding="utf-8")
    out = tmp_path / "out.jsonl"
    rc = jats_extract.main(["--corpus", str(corpus), "--out", str(out)])
    assert rc == 1                                  # 50% failures > default 1%
    assert out.read_text(encoding="utf-8").strip()  # PMC1's records still written


def test_corpus_mode_walks_tree_and_reads_manifest(tmp_path: Path):
    corpus = _corpus(tmp_path)
    out = tmp_path / "out.jsonl"
    report = tmp_path / "out.report.json"

    rc = jats_extract.main(["--corpus", str(corpus), "--out", str(out),
                            "--report", str(report), "--limit", "2"])
    assert rc == 0

    recs = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert {r["metadata"]["pmcid"] for r in recs} == {"PMC1", "PMC2"}  # --limit honoured
    assert {r["metadata"]["sha256"] for r in recs} == {"sha-1", "sha-2"}
    rep = json.loads(report.read_text(encoding="utf-8"))
    assert rep["by_content_type"] == {"article": 2, "table": 2, "figure": 2}


def test_kinds_filter_selects_record_kinds(tmp_path: Path):
    corpus = _corpus(tmp_path, ids=("1",))
    out = tmp_path / "out.jsonl"
    rc = jats_extract.main(["--corpus", str(corpus), "--out", str(out),
                            "--kinds", "table,figure"])
    assert rc == 0
    recs = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert {r["metadata"]["content_type"] for r in recs} == {"table", "figure"}


def test_no_input_selected_is_a_failure(tmp_path: Path):
    corpus = _corpus(tmp_path, ids=())
    out = tmp_path / "out.jsonl"
    assert jats_extract.main(["--corpus", str(corpus), "--out", str(out)]) == 1
    assert jats_extract.main(["--out", str(out)]) == 2      # neither mode given
