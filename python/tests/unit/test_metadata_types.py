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

from ragstack.ingestion.enrich import coerce_year, derive_year, enrich
from ragstack.ingestion.jats import article_records, front_meta
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.stores.filters import KNOWN_INT_FIELDS, validate_filter_values

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


def test_passthrough_year_is_coerced_not_stamped_as_a_string(tmp_path: Path):
    """The silent within-collection type split.

    ``index_metadata`` drops ``year`` when the enricher derives none; the
    passthrough then filled the empty slot with the corpus's RAW value. With a
    string corpus that produced ``year: 2019`` on one chunk and ``year: "2019"``
    on the next — and a correct ``{"year": 2019}`` filter matching only the
    first, silently."""
    # ``path``/``doi``/``text`` carry no year, so only the passthrough can supply
    # one — the exact shape that produced the split.
    record = {"path": "PMC123", "text": _BODY, "metadata": {"year": "2019"}}
    meta = (
        JsonlLoader(passthrough_keys={"year"})
        .load(_write(tmp_path / "c.jsonl", [record]))[0]
        .metadata
    )
    assert meta["year"] == 2019
    assert isinstance(meta["year"], int)
    validate_filter_values({"year": meta["year"]})


def test_passthrough_unusable_int_field_is_dropped_not_stringified(tmp_path: Path):
    record = {"path": "PMC123", "text": _BODY, "metadata": {"year": "not a year"}}
    meta = (
        JsonlLoader(passthrough_keys={"year"})
        .load(_write(tmp_path / "c.jsonl", [record]))[0]
        .metadata
    )
    assert "year" not in meta


def test_every_declared_int_field_is_coerced_by_the_passthrough(tmp_path: Path):
    """Guards the table, not just today's single entry: whatever
    ``KNOWN_INT_FIELDS`` grows to hold, the loader must not stamp a string for
    it. A new int field added to the filter grammar without a producer rule is
    the same bug under a different name."""
    for field in KNOWN_INT_FIELDS:
        record = {"path": "PMC123", "text": _BODY, "metadata": {field: "2019"}}
        meta = (
            JsonlLoader(passthrough_keys={field})
            .load(_write(tmp_path / f"{field}.jsonl", [record]))[0]
            .metadata
        )
        assert not isinstance(meta.get(field), str), (
            f"{field!r} is a declared integer filter field but the loader "
            f"stamped a string — that document is unreachable by any legal filter"
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
    assert {"journal", "licence"} <= loader.dropped_metadata_keys
    assert "pmcid" not in loader.dropped_metadata_keys


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
    assert loader.dropped_metadata_keys >= {
        "pmcid", "pmid", "journal", "content_type", "sha256",
    }
