"""Producer-side metadata TYPE consistency — the other half of #471.

#471 pinned the *filter* side: values are matched by type and never coerced, so
``{"year": "2025"}`` is a 400 rather than a silent zero-hit read. That rule is
only half an answer. A producer that *stores* ``year`` as a string builds
documents no correct filter can ever reach, and the failure is silent in the
worst way — the query succeeds, the count looks plausible, and the documents are
simply missing.

It is not hypothetical. Measured read-only against the live stores on 2026-09-15:

===================================  ==========  ==========
collection                           Qdrant      ES
===================================  ==========  ==========
``lucid_sfr_tok256`` (1.55M chunks)  ``str``     ``keyword``
``oa_smoke_tok512``                  ``str``     ``keyword``
ASM ``ragstack_sfr_tok256`` et al.   ``int``     ``long``
===================================  ==========  ==========

On ``lucid``, ``{"year": 2021}`` — the *only* form the API accepts — matches
129,248 chunks on the Elasticsearch leg (ES coerces at query time) and **0** on
the Qdrant leg (Qdrant compares payload types exactly). Hybrid retrieval hides
the disagreement behind the BM25 leg.

These tests pin the producer side so the next corpus cannot join that table:

1. ``jats.py`` emits ``year`` as an ``int`` (it emitted the raw ``<year>`` text).
2. ``enrich``/``JsonlLoader`` consult the record's DECLARED year — the whole of
   the ``open-access`` 14.8% year coverage is that they did not.
3. A passthrough value for a declared-int field can never land as a string, so
   one collection cannot hold ``year: 2019`` and ``year: "2019"`` side by side.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ragstack import metadata_schema
from ragstack.ingestion.enrich import derive_year, enrich
from ragstack.ingestion.jats import article_records, front_meta
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.metadata_schema import KNOWN_INT_FIELDS, coerce_year
from ragstack.stores.filters import validate_filter_values

# Long enough to classify as ARTICLE rather than SHORT (threshold 1500 chars).
_BODY = "article body " * 200


def _jats(year_text: str = "2019", pmcid: str = "PMC77") -> str:
    return f"""<article>
  <front><journal-meta><journal-title>J Synthetic Res</journal-title></journal-meta>
    <article-meta>
      <article-id pub-id-type="doi">10.1234/synth.7</article-id>
      <article-id pub-id-type="pmc">{pmcid}</article-id>
      <article-title>A synthetic article</article-title>
      <pub-date pub-type="epub"><year>{year_text}</year></pub-date>
      <abstract><p>{_BODY}</p></abstract>
    </article-meta></front>
  <body><sec><title>Intro</title><p>{_BODY}</p></sec></body>
</article>"""


def _load_script(name: str):
    """Import a ``python/scripts/*.py`` operator tool as a module.

    They are scripts, not package members, so there is no import path for them.
    """
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_script_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, records: list[dict]) -> str:
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return str(path)


# --- 1. jats.py emits an int ------------------------------------------------


def test_jats_front_meta_year_is_an_int():
    """The defect, stated directly: ``front_meta`` returned ``"2019"``."""
    meta = front_meta(ET.fromstring(_jats("2019")))
    assert meta["year"] == 2019
    assert isinstance(meta["year"], int) and not isinstance(meta["year"], bool)


def test_jats_year_survives_to_every_record_kind_as_an_int():
    records, _ = article_records(ET.fromstring(_jats("2019")), "PMC77")
    assert records, "the fixture must produce at least the article record"
    for record in records:
        assert record["metadata"]["year"] == 2019


def test_jats_year_is_the_type_the_filter_grammar_demands():
    """The whole point: the stored value must satisfy the API's own validator.

    ``validate_filter_values`` is what a query goes through. Feeding it the
    producer's own output is the end-to-end statement of the invariant — a value
    the producer writes must be one a caller may legally filter on."""
    year = front_meta(ET.fromstring(_jats("2019")))["year"]
    assert "year" in KNOWN_INT_FIELDS
    validate_filter_values({"year": year})  # must not raise


@pytest.mark.parametrize("raw", ["2o19", "", "n/a", "19", "0000"])
def test_jats_unparseable_year_is_omitted_not_stringified(raw: str):
    """A year that is not a year is absent — never a string, never a null value
    on a chunk. Absence is filterable-correct; a string is not."""
    assert front_meta(ET.fromstring(_jats(raw)))["year"] is None


def test_jats_implausible_year_is_refused():
    """``open-access`` carries 8,408 chunks dated 2047-2049 from parse errors
    (docs/plans/date-filtering.md). A future year is a parse failure, not data."""
    assert front_meta(ET.fromstring(_jats("2049")))["year"] is None
    assert front_meta(ET.fromstring(_jats("1200")))["year"] is None
    # ...and the boundary is open enough for the real corpus: PMC holds articles
    # from the early 1800s, and next year's ahead-of-print is legitimate.
    assert front_meta(ET.fromstring(_jats("1809")))["year"] == 1809
    assert front_meta(ET.fromstring(_jats(str(date.today().year + 1))))["year"] == (
        date.today().year + 1
    )


def test_jats_picks_the_first_PLAUSIBLE_pub_date_not_the_first_one():
    """A malformed leading ``<pub-date>`` must not shadow a good later one — the
    old loop took the first non-empty string whatever it said."""
    xml = _jats("2019").replace(
        '<pub-date pub-type="epub"><year>2019</year></pub-date>',
        '<pub-date pub-type="collection"><year>n/a</year></pub-date>'
        '<pub-date pub-type="epub"><year>2019</year></pub-date>',
    )
    assert front_meta(ET.fromstring(xml))["year"] == 2019


# --- 2. the record's DECLARED year is consulted -----------------------------


def test_derive_year_prefers_the_records_declared_year():
    """Metadata beats inference — the precedence ``derive_doi`` already applies.

    Without this the JATS path has no year source at all: a JATS record's
    ``path`` is a bare ``PMC123``, its DOI carries no year, and its prose has no
    copyright anchor. That is the entire ``open-access`` gap."""
    assert derive_year("PMC123", doi="", text="", meta_year=2019) == 2019
    assert derive_year("PMC123", doi="", text="", meta_year="2019") == 2019
    # A declared year overrides one guessed from a volume-style DOI.
    assert derive_year(
        "/x/foo.pdf", doi="10.1128/iai.70.9.4833-4840.2002", text="", meta_year=2019
    ) == 2019
    # An unusable declared year falls through to the existing inference chain
    # rather than blanking the field.
    assert derive_year(
        "/x/foo.pdf", doi="10.1128/iai.70.9.4833-4840.2002", text="", meta_year="n/a"
    ) == 2002


def test_jats_shaped_record_reaches_a_chunk_with_its_year(tmp_path: Path):
    """The end-to-end statement of the ``open-access`` defect: a record that
    declares ``year`` must produce a document that carries it, as an int, with
    NO passthrough flag set."""
    record = {
        "path": "PMC123",
        "text": _BODY,
        "metadata": {"doi": "10.1234/synth.7", "pmcid": "PMC123", "year": 2019},
    }
    meta = JsonlLoader().load(_write(tmp_path / "c.jsonl", [record]))[0].metadata
    assert meta["year"] == 2019
    assert isinstance(meta["year"], int)


def test_enrich_never_returns_a_string_year():
    doc = enrich({"path": "PMC1", "text": _BODY, "metadata": {"year": "2019"}})
    assert doc.year == 2019 and isinstance(doc.year, int)


# --- 3. a passthrough value can never split a field's type ------------------


def test_a_declared_year_reaches_a_chunk_as_an_int_with_passthrough_on(tmp_path: Path):
    """END-TO-END, not a guard test. Read the name literally.

    This was originally called "passthrough year is coerced", which was not what
    it pinned: ``derive_year`` now consults the record's declared year FIRST, so
    ``index_metadata`` fills the slot and ``_metadata``'s ``key in meta`` branch
    skips the passthrough entirely. Deleting the ``KNOWN_INT_FIELDS`` block in
    ``_passthrough_value`` leaves this test GREEN. Instrumented: for ``year`` the
    passthrough arm is reached but returns non-None zero times.

    It still earns its place as the end-to-end statement — a string year in a
    corpus must reach a chunk as an int, by whichever route — but the guard it
    appears to test is pinned by
    :func:`test_a_declared_int_field_is_dropped_rather_than_stringified`, which
    uses a field the enricher has no slot for."""
    record = {"path": "PMC123", "text": _BODY, "metadata": {"year": "2019"}}
    meta = (
        JsonlLoader(passthrough_keys={"year"})
        .load(_write(tmp_path / "c.jsonl", [record]))[0]
        .metadata
    )
    assert meta["year"] == 2019
    assert isinstance(meta["year"], int)
    validate_filter_values({"year": meta["year"]})


def test_an_unusable_year_is_dropped_by_every_route(tmp_path: Path):
    record = {"path": "PMC123", "text": _BODY, "metadata": {"year": "not a year"}}
    meta = (
        JsonlLoader(passthrough_keys={"year"})
        .load(_write(tmp_path / "c.jsonl", [record]))[0]
        .metadata
    )
    assert "year" not in meta


def test_a_declared_int_field_is_dropped_rather_than_stringified(monkeypatch, tmp_path: Path):
    """THIS is the test that pins ``_passthrough_value``'s int branch.

    It uses a field the enricher has no slot for, so ``index_metadata`` cannot
    fill it and the passthrough arm is genuinely the only route. Deleting the
    ``KNOWN_INT_FIELDS`` block makes this fail; the two tests above stay green,
    which is exactly why they are named for what they actually pin.

    Monkeypatching the table is deliberate: the guard must hold for whatever
    ``KNOWN_INT_FIELDS`` grows to contain, and today's sole entry (``year``) is
    the one field that cannot exercise it."""
    monkeypatch.setattr(metadata_schema, "KNOWN_INT_FIELDS", frozenset({"n_pages"}))
    record = {"path": "PMC123", "text": _BODY, "metadata": {"n_pages": "12"}}
    meta = (
        JsonlLoader(passthrough_keys={"n_pages"})
        .load(_write(tmp_path / "n.jsonl", [record]))[0]
        .metadata
    )
    assert meta["n_pages"] == 12 and isinstance(meta["n_pages"], int)

    record = {"path": "PMC124", "text": _BODY, "metadata": {"n_pages": "many"}}
    meta = (
        JsonlLoader(passthrough_keys={"n_pages"})
        .load(_write(tmp_path / "n2.jsonl", [record]))[0]
        .metadata
    )
    assert "n_pages" not in meta, (
        "a declared integer filter field was stamped as a string — that document "
        "is unreachable by any legal filter"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2019, 2019),
        ("2019", 2019),
        ("2019 Mar", 2019),   # JATS <year> is occasionally a date fragment
        ("c2019", 2019),
        (2019.0, 2019),
        (2019.5, None),
        (True, None),         # bool is an int in Python but it is not a year
        (None, None),
        ("", None),
        ("2o19", None),
        (2049, None),         # the future-dated parse errors on `open-access`
        (1200, None),
    ],
)
def test_coerce_year_table(value, expected):
    assert coerce_year(value) == expected


# --- 4. the drop is no longer silent ----------------------------------------


def test_dropped_metadata_keys_are_reported(tmp_path: Path):
    """A dropped key is how ``open-access`` lost fields nobody can re-stamp
    without a full re-ingest. The keys were always dropped; what was missing was
    any way to find out."""
    record = {
        "path": "PMC123",
        "text": _BODY,
        "metadata": {"pmcid": "PMC123", "journal": "J Synth", "licence": "CC-BY"},
    }
    loader = JsonlLoader(passthrough_keys={"pmcid"})
    meta = loader.load(_write(tmp_path / "c.jsonl", [record]))[0].metadata
    assert meta["pmcid"] == "PMC123"
    assert dict(loader.dropped.not_allowed) == {"journal": 1, "licence": 1}
    assert not loader.dropped.unusable
    assert "pmcid" not in loader.dropped.not_allowed


def test_default_loader_reports_every_jats_key_it_drops(tmp_path: Path):
    """The default construction — the one ``scripts/ingest_shard.py`` and the
    API path use — drops the whole JATS set. It must say so."""
    record = {
        "path": "PMC123",
        "text": _BODY,
        "metadata": {"pmcid": "PMC123", "pmid": "994", "journal": "J Synth",
                     "content_type": "article", "sha256": "deadbeef"},
    }
    loader = JsonlLoader()
    loader.load(_write(tmp_path / "c.jsonl", [record]))
    assert set(loader.dropped.not_allowed) == {
        "pmcid", "pmid", "journal", "content_type", "sha256",
    }


def test_a_by_design_exclusion_is_not_reported_as_a_drop(tmp_path: Path):
    """``abstract`` is a ``_HEAVY_FIELDS`` exclusion: dropped from every chunk on
    every corpus, forever. Counting it would put a constant line on every JATS
    shard and train the operator to stop reading the report."""
    record = {"path": "PMC123", "text": _BODY,
              "metadata": {"abstract": "an abstract", "journal": "J Synth"}}
    loader = JsonlLoader()
    loader.load(_write(tmp_path / "c.jsonl", [record]))
    assert "abstract" not in loader.dropped.not_allowed
    assert "journal" in loader.dropped.not_allowed


def test_the_drop_report_separates_policy_from_data_and_counts_records(tmp_path: Path):
    """"Never in the allow-list" and "in the list but unusable on N records" need
    different actions from different people, so they are not one number."""
    records = [
        {"path": f"PMC{i}", "text": _BODY,
         "metadata": {"journal": "J Synth", "pmid": ("994" if i else "  ")}}
        for i in range(3)
    ]
    loader = JsonlLoader(passthrough_keys={"pmid"})
    loader.load(_write(tmp_path / "c.jsonl", records))
    assert loader.dropped.not_allowed["journal"] == 3   # policy: every record
    assert loader.dropped.unusable["pmid"] == 1         # data: one record
    assert "1 record(s)" in loader.dropped.summary()
    assert "journal (x3)" in loader.dropped.summary()


# --- 5. the INFERENCE arms are bounded too ----------------------------------
#
# The original fix bounded only the DECLARED value, which was measurably the
# wrong half: ASM records declare no year at all, so all 16,176 future-dated
# chunks across ASM's three production indices came out of the arms below.


@pytest.mark.parametrize(
    ("path", "doi", "text", "why"),
    [
        ("/local/scratch/03220d5a-2049-4ab5-b0bf-93e110a69eb0/x.pdf", "", "",
         "a scratch UUID segment read as a year — the live ASM mechanism"),
        ("/x/f.pdf", "10.1186/2049-2618-1-3", "",
         "an ISSN inside a DOI (Microbiome, ISSN 2049-2618)"),
        ("/x/f.pdf", "", "Figure 2. copyright BGS.GSE2028/9680 sample",
         "an accession number next to a publication-context anchor"),
    ],
)
def test_an_implausible_inferred_year_is_refused(path, doi, text, why):
    got = derive_year(path, doi, text)
    assert got is None or got <= date.today().year + 1, why


def test_the_first_PLAUSIBLE_inferred_year_wins_not_the_first_match():
    """Bounding alone would only drop the false year. Scanning every candidate
    recovers the true one: the real year is further along the very same path,
    behind the scratch UUID that produced the 2049."""
    path = ("/local/scratch/03220d5a-2049-4ab5-b0bf-93e110a69eb0/"
            "MRAv10i33/mra.2021.10.issue-33/mra.00641-21.pdf")
    assert derive_year(path, "10.1128/mra.00641-21", "") == 2021


def test_every_route_into_year_agrees_on_the_bound():
    """No arm may admit a year another arm refuses — that asymmetry WAS the bug."""
    for y in (2049, 1200, 9999):
        assert coerce_year(y) is None
        assert derive_year(f"/x/{y}/f.pdf", "", "") != y
        assert derive_year("/x/f.pdf", "", "", meta_year=y) is None
    # ...and a year every arm admits is admitted by every arm.
    assert coerce_year(2019) == 2019
    assert derive_year("/x/2019/f.pdf", "", "") == 2019
    assert derive_year("/x/f.pdf", "", "", meta_year=2019) == 2019


def test_the_inference_window_is_narrower_than_the_validity_bound_on_purpose():
    """Two ranges coexist and that is deliberate, not an oversight.

    ``_YEAR``'s 1950-2049 is an INFERENCE precision heuristic — a bare 4-digit
    number in a path is only probably a year. ``plausible_year_range()``'s
    1500..next-year is the VALIDITY bound and applies to every year whatever its
    source. So a DECLARED 1809 is trusted, while an inferred 1809 is never even
    proposed."""
    assert coerce_year(1809) == 1809
    assert derive_year("/j/1809/x", "", "", meta_year="1809") == 1809
    assert derive_year("/j/1809/x", "", "") is None


# --- 6. the tool that built the headline live defect ------------------------


def test_ingest_chunks_flatten_types_a_declared_int_field():
    """``scripts/ingest_chunks.py`` copied caller JSON onto a payload verbatim —
    the fingerprint on the live ``lucid`` collection (string ``year``, string
    ``chunk_index``, ", "-joined authors). It is the one tool that can still
    write an unfilterable document by hand."""
    ingest_chunks = _load_script("ingest_chunks")
    docs = [{
        "doc_id": "d1",
        "metadata": {"year": "2016", "authors": "A, B", "title": "T"},
        "chunks": [{"text": "hello world", "chunk_index": 0}],
    }]
    md = ingest_chunks.flatten(docs)[0].metadata
    assert md["year"] == 2016 and isinstance(md["year"], int)
    validate_filter_values({"year": md["year"]})
    # Undeclared keys are still carried verbatim — that is the script's purpose.
    assert md["authors"] == "A, B" and md["title"] == "T"


def test_ingest_chunks_flatten_drops_an_uncoercible_declared_int():
    ingest_chunks = _load_script("ingest_chunks")
    docs = [{"doc_id": "d1", "metadata": {"year": "n/a"},
             "chunks": [{"text": "hello world"}]}]
    assert "year" not in ingest_chunks.flatten(docs)[0].metadata
