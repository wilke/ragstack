"""Synthetic JATS fixtures shared by the JATS tests (#301, #828 PR 5a).

Not a test module: the article builders and float snippets that
``tests/ingestion/test_jats_extract.py`` introduced, imported by every module
that needs a JATS article (the loader, parity, duplicate-guard and upload
tests). Synthetic only — no corpus content.
"""
from __future__ import annotations

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


def _article(n: str = "1", body: str = "", floats_group: str = "", *,
             pmc: bool = True, doi: str | None = None) -> str:
    """Wrap a body (and optional floats-group) in a minimal but realistic JATS doc.

    ``pmc=False`` leaves out the ``<article-id pub-id-type="pmc">`` (the record
    key then comes from the file name, ``jats.record_key`` step 3); ``doi``
    replaces the default ``10.1234/synth.<n>``."""
    fg = f"<floats-group>{floats_group}</floats-group>" if floats_group else ""
    front = FRONT.format(n=n)
    if not pmc:
        front = front.replace(f'<article-id pub-id-type="pmc">PMC{n}</article-id>', "")
    if doi is not None:
        front = front.replace(f">10.1234/synth.{n}<", f">{doi}<")
    return (
        '<article xmlns:xlink="http://www.w3.org/1999/xlink" article-type="research-article">'
        + front
        + f"<body>{body}</body>{fg}</article>"
    )


BODY_WITH_FLOATS = f"""
    <sec>
      <title>Results</title>
      <p>The prose sentence lives here and mentions QQPROSEQQ once.</p>
      <p>An inline paragraph that legally nests a float: {TABLE_1}</p>
      {FIG_1}
    </sec>
"""


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
