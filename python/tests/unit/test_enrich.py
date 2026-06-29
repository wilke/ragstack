"""Unit tests for scholarly-metadata enrichment — DOI recovery (per source),
document classification, citation extraction, author/keyword parsing, and the
index-safe metadata projection."""
from __future__ import annotations

from ragstack.ingestion.enrich import (
    ARTICLE,
    EMPTY,
    FRONT_MATTER,
    SHORT,
    SUPPLEMENT,
    classify,
    derive_doi,
    derive_year,
    enrich,
    extract_citations,
    index_metadata,
    parse_authors,
    split_keywords,
)

# --- DOI recovery -----------------------------------------------------------

def test_doi_from_filename():
    doi, src = derive_doi("/scratch/x/jvi.02415-06.pdf", text="", meta_doi="")
    assert doi == "10.1128/jvi.02415-06"
    assert src == "filename"


def test_doi_from_volume_style_filename():
    doi, src = derive_doi("/x/iai.70.9.4833-4840.2002.pdf", text="", meta_doi="")
    assert doi == "10.1128/iai.70.9.4833-4840.2002"
    assert src == "filename"


def test_doi_prefers_existing_metadata():
    doi, src = derive_doi("/x/jvi.02415-06.pdf", text="", meta_doi="10.9999/custom.1")
    assert (doi, src) == ("10.9999/custom.1", "metadata")


def test_doi_falls_back_to_text_for_unparseable_filename():
    doi, src = derive_doi(
        "/x/cover.pdf", text="see https://doi.org/10.1128/Spectrum.00571-21 for more", meta_doi=""
    )
    assert doi == "10.1128/Spectrum.00571-21"
    assert src == "text"


def test_doi_trims_trailing_punctuation_from_text():
    doi, _ = derive_doi("/x/note.pdf", text="published as 10.1128/jvi.00155-22.", meta_doi="")
    assert doi == "10.1128/jvi.00155-22"


def test_doi_text_strips_unbalanced_closing_paren():
    doi, _ = derive_doi("/x/note.pdf", text="(see 10.1128/jvi.00155-22) for details", meta_doi="")
    assert doi == "10.1128/jvi.00155-22"


def test_doi_text_keeps_balanced_parens():
    # Some DOIs legitimately contain parentheses — those must be preserved.
    doi, src = derive_doi(
        "/x/note.pdf", text="cited as 10.1016/S0140-6736(98)01085-X in the review", meta_doi=""
    )
    assert doi == "10.1016/S0140-6736(98)01085-X"
    assert src == "text"


def test_doi_text_internal_parens_wrapped_in_prose_parens():
    # A DOI that BOTH contains balanced parens AND is wrapped in prose parens:
    # the enclosing ')' must be stripped while the DOI's own '(98)' is kept.
    doi, _ = derive_doi(
        "/x/note.pdf",
        text="cited as (10.1016/S0140-6736(98)01085-X) in the review",
        meta_doi="",
    )
    assert doi == "10.1016/S0140-6736(98)01085-X"


def test_doi_absent():
    assert derive_doi("/x/cover.pdf", text="no identifier here", meta_doi="") == ("", "")


def test_doi_custom_prefix():
    doi, src = derive_doi("/x/abc.12345.pdf", text="", meta_doi="", prefix="10.1099")
    assert doi == "10.1099/abc.12345"
    assert src == "filename"


# --- classification ---------------------------------------------------------

def test_classify_article():
    assert classify("/x/jvi.02415-06.pdf", "x" * 5000) == ARTICLE


def test_classify_front_matter():
    assert classify("/x/masthead.pdf", "x" * 5000) == FRONT_MATTER


def test_classify_supplement_by_dir():
    assert classify("/x/suppl/jcm.01127-19-s0001.pdf", "x" * 5000) == SUPPLEMENT


def test_classify_empty_and_short():
    assert classify("/x/cover.pdf", "   ") == EMPTY
    assert classify("/x/jvi.02415-06.pdf", "tiny") == SHORT


# --- year -------------------------------------------------------------------

def test_year_from_issue_dir():
    assert derive_year("/x/jvi.1972.9.issue-2/jvi.foo.pdf", doi="", text="") == 1972


def test_year_from_doi_when_path_has_none():
    assert derive_year("/x/foo.pdf", doi="10.1128/iai.70.9.4833-4840.2002", text="") == 2002


def test_year_none_when_unknown():
    assert derive_year("/x/foo.pdf", doi="10.1128/jvi.02415-06", text="abc") is None


def test_year_from_text_requires_publication_context():
    # A bare 4-digit number in body text must NOT be taken as the year...
    assert derive_year(
        "/x/foo.pdf", doi="10.1128/jvi.02415-06",
        text="cells were spun at 2000 rpm for 5 min in a 2010 model centrifuge",
    ) is None
    # ...but a year next to a publication-context anchor is accepted.
    assert derive_year(
        "/x/foo.pdf", doi="10.1128/jvi.02415-06",
        text="Copyright © 2007, American Society for Microbiology. All rights reserved.",
    ) == 2007
    assert derive_year(
        "/x/foo.pdf", doi="10.1128/jvi.02415-06",
        text="Received 12 March 2011; accepted 4 June 2011.",
    ) == 2011


# --- authors / keywords -----------------------------------------------------

def test_parse_authors_semicolon():
    assert parse_authors("Jing-hsiung James Ou;Richard J. Kuhn; Charles M. Rice") == [
        "Jing-hsiung James Ou",
        "Richard J. Kuhn",
        "Charles M. Rice",
    ]


def test_parse_authors_empty():
    assert parse_authors("") == []
    assert parse_authors("   ") == []


def test_split_keywords():
    assert split_keywords("virus, hepatitis; CD8") == ["virus", "hepatitis", "CD8"]


# --- citations --------------------------------------------------------------

_REFS = """Some article body text here.

LITERATURE CITED
1. Smith J, Doe A. 2006. A global perspective on the use of antibiotics.
   J Bacteriol 12:345-356.
2. Lee K. 2009. Genetic determinants of pathogenicity. Nature 4:1-9.
3. Brown W. 1979. Rapid evolution of animal mitochondria. PNAS 76:1967.
"""


def test_extract_citations_numbered_and_wrapped():
    cites = extract_citations(_REFS)
    assert len(cites) == 3
    assert cites[0].startswith("Smith J, Doe A. 2006.")
    # wrapped continuation line is coalesced into the same entry
    assert "J Bacteriol 12:345-356." in cites[0]
    assert cites[1].startswith("Lee K. 2009.")


def test_extract_citations_none_without_section():
    assert extract_citations("Body text with no reference section at all.") == []


def test_extract_citations_cap():
    body = "REFERENCES\n" + "\n".join(f"{i}. Author {i}. 2000. Title number {i}." for i in range(1, 50))
    assert len(extract_citations(body, cap=10)) == 10


def test_extract_citations_multicolumn_interleave_not_truncated():
    # A mis-ordered two-column extraction interleaves low/high numbers
    # (1, 26, 2, 27, ...). The old "any backwards step > 5" heuristic truncated
    # at the first 26->2 drop; requiring a reset to the list start (n<=2) keeps
    # the whole list.
    lines = []
    for a, b in zip(range(1, 6), range(26, 31), strict=True):
        lines.append(f"{a}. Author {a}. 2001. Title about subject number {a}.")
        lines.append(f"{b}. Author {b}. 2001. Title about subject number {b}.")
    cites = extract_citations("REFERENCES\n" + "\n".join(lines))
    assert len(cites) == 10  # all kept, not cut off at the first column jump


def test_extract_citations_truncates_at_reset_to_start():
    # A genuine new numbered list after the references (a figure list restarting
    # at 1) is still excluded — the intended behavior is preserved. Needs enough
    # references that the reset is a >5 drop (the heuristic's threshold).
    refs = [f"{i}. Author {i}. 2001. Real reference number {i} here." for i in range(1, 21)]
    figs = [f"{i}. Figure caption number {i} describing the panel." for i in range(1, 4)]
    cites = extract_citations("REFERENCES\n" + "\n".join(refs + figs))
    assert len(cites) == 20  # the restart-at-1 figure list is dropped


# --- end-to-end enrich + index projection -----------------------------------

def test_enrich_full_record():
    record = {
        "path": "/scratch/jvi.1972.9.issue-2/jvi.00155-22.pdf",
        "text": "Body.\n\nLITERATURE CITED\n1. Simmons DT, Strauss JH. 1972. Replication of Sindbis virus.\n"
        + "x" * 3000,
        "metadata": {
            "title": "Biographical Feature: James H. Strauss, Jr.",
            "authors": "Jing-hsiung James Ou;Charles M. Rice",
            "doi": "",
            "keywords": "",
        },
    }
    e = enrich(record)
    assert e.doc_type == ARTICLE
    assert e.doi == "10.1128/jvi.00155-22"
    assert e.doi_source == "filename"
    assert e.year == 1972
    assert e.title.startswith("Biographical Feature")
    assert e.authors == ["Jing-hsiung James Ou", "Charles M. Rice"]
    assert e.n_citations == 1
    assert e.citations[0].startswith("Simmons DT")


def test_enrich_empty_record_tagged_empty():
    e = enrich({"path": "/x/cover.pdf", "text": "", "metadata": {}})
    assert e.doc_type == EMPTY
    assert e.citations == []


def test_index_metadata_excludes_heavy_fields_and_blanks():
    record = {
        "path": "/x/jvi.02415-06.pdf",
        "text": "REFERENCES\n1. A. 2000. T.\n" + "y" * 3000,
        "metadata": {"title": "", "authors": "Alice;Bob"},
    }
    meta = index_metadata(enrich(record))
    assert "citations" not in meta  # heavy field stays out of the chunk payload
    assert "abstract" not in meta
    assert "title" not in meta  # blank value dropped
    assert meta["authors"] == ["Alice", "Bob"]
    assert meta["doi"] == "10.1128/jvi.02415-06"
    assert meta["n_citations"] == 1  # lightweight count is kept
    assert meta["doc_type"] == ARTICLE
