"""The mapping-conformance comparison (#603), against captured mappings.

No cluster. ``compare_mapping`` is pure by design precisely so this suite can
exercise it: the repo's rule is that tests needing a live store use the dev
tenant and never a default URL, and the safest live store is no live store.

The mappings below are TRIMMED COPIES OF REAL ONES, read read-only from the
deployed clusters on 2026-09-17, so the assertions are about what is actually
out there rather than about a fixture invented to pass.
"""
from __future__ import annotations

from ragstack.ops import metadata_conformance as mc

_TEMPLATE = [
    {
        "metadata_strings_as_keyword": {
            "path_match": "metadata.*",
            "match_mapping_type": "string",
            "mapping": {"type": "keyword", "ignore_above": 8191},
        }
    }
]


def _mappings(metadata_props: dict, *, template: bool = True) -> dict:
    return {
        "dynamic_templates": _TEMPLATE if template else [],
        "properties": {
            "content": {"type": "text"},
            "metadata": {"type": "object", "properties": metadata_props},
        },
    }


# Trimmed from ragstack_lib_dengue_... on the hackathon cluster: the collection
# the pmid incident happened to, as it stands after its reindex.
_DENGUE = {
    "authors": {"type": "keyword"},
    "chunk_index": {"type": "long"},
    "is_boilerplate": {"type": "boolean"},
    "pmcid": {"type": "keyword"},
    "pmid": {"type": "keyword"},
    "section": {"type": "keyword"},
    "tenant_id": {"type": "keyword"},
    "year": {"type": "long"},
}

# Trimmed from lucid_sfr_tok256 on the shared cluster — 1,554,790 chunks whose
# `year` is a keyword, which is the live divergence this tool is for.
_LUCID = {
    "authors": {"type": "keyword"},
    "chunk_index": {"type": "long"},
    "citation": {"type": "keyword"},
    "doi_url": {"type": "keyword"},
    "tenant_id": {"type": "keyword"},
    "title": {"type": "keyword"},
    "year": {"type": "keyword"},
}


def _verdict(fields, name: str) -> str:
    return next(f.verdict for f in fields if f.name == name)


def test_a_matching_field_is_ok():
    fields, _ = mc.compare_mapping(_mappings(_DENGUE))
    assert _verdict(fields, "pmid") == mc.OK
    assert _verdict(fields, "year") == mc.OK
    assert _verdict(fields, "is_boilerplate") == mc.OK


def test_a_keyword_year_is_reported_divergent():
    fields, _ = mc.compare_mapping(_mappings(_LUCID))
    (bad,) = [f for f in fields if f.verdict == mc.DIVERGENT]
    assert bad.name == "year"
    assert (bad.observed, bad.declared) == ("keyword", "long")


def test_a_long_pmid_is_reported_divergent():
    """The mapping the issue was opened about. It is no longer live — the index
    was recreated on 2026-09-17 and now maps a keyword — but it is the shape the
    check has to catch, and an ES mapping cannot be changed in place, so the only
    reason it is gone is that somebody reindexed 382 chunks."""
    fields, _ = mc.compare_mapping(_mappings({**_DENGUE, "pmid": {"type": "long"}}))
    (bad,) = [f for f in fields if f.verdict == mc.DIVERGENT]
    assert (bad.name, bad.observed, bad.declared) == ("pmid", "long", "keyword")


def test_a_field_no_document_has_carried_is_absent_not_divergent():
    """ES materializes a field's mapping the first time a document carries one,
    so 'absent' is the normal state of an optional field — not a defect, and the
    report must not turn it into one."""
    fields, _ = mc.compare_mapping(_mappings(_DENGUE))
    assert _verdict(fields, "journal") == mc.ABSENT
    assert not [f for f in fields if f.verdict == mc.DIVERGENT]


def test_an_undeclared_field_is_listed_but_does_not_count_against_conformance():
    fields, has_template = mc.compare_mapping(_mappings(_LUCID))
    undeclared = {f.name for f in fields if f.verdict == mc.UNDECLARED}
    assert undeclared == {"citation", "doi_url"}
    report = mc.IndexReport("http://es", "i", fields=fields, has_template=has_template)
    # It diverges on `year`, not on the two undeclared fields.
    assert not report.conforms
    clean, tmpl = mc.compare_mapping(_mappings({**_LUCID, "year": {"type": "long"}}))
    assert mc.IndexReport("http://es", "i", fields=clean, has_template=tmpl).conforms


def test_a_missing_dynamic_template_is_itself_a_finding():
    """Without it an undeclared string field maps as an UNBOUNDED keyword, and a
    >32 KB value aborts the whole bulk request."""
    fields, has_template = mc.compare_mapping(_mappings(_DENGUE, template=False))
    assert not has_template
    assert not mc.IndexReport("http://es", "i", fields=fields).conforms


def test_an_index_with_no_metadata_mapping_at_all_is_not_an_error():
    """A freshly created, never-written index. Every declared field is simply not
    materialized yet."""
    fields, _ = mc.compare_mapping(_mappings({}))
    assert {f.verdict for f in fields} == {mc.ABSENT}


def test_a_nested_object_where_a_scalar_is_declared_reads_as_object():
    fields, _ = mc.compare_mapping(
        _mappings({**_DENGUE, "title": {"properties": {"raw": {"type": "keyword"}}}})
    )
    (bad,) = [f for f in fields if f.verdict == mc.DIVERGENT]
    assert (bad.name, bad.observed) == ("title", "object")


def test_an_unreadable_index_never_reads_as_conforming():
    assert not mc.IndexReport("http://es", "i", error="ConnectError: refused").conforms


def test_the_text_report_names_the_divergence_and_says_it_is_report_only():
    fields, tmpl = mc.compare_mapping(_mappings(_LUCID))
    text = mc.render_text(
        [mc.IndexReport("http://es", "lucid_sfr_tok256", fields=fields, has_template=tmpl)]
    )
    assert "REPORT ONLY" in text
    assert "cannot be changed in place" in text
    assert "DIVERGENT  metadata.year: mapped 'keyword', declared 'long'" in text
    assert "1 diverge" in text


def test_the_json_report_carries_the_declared_types():
    fields, tmpl = mc.compare_mapping(_mappings(_LUCID))
    doc = mc.to_dict([mc.IndexReport("http://es", "i", fields=fields, has_template=tmpl)])
    assert doc["declared_fields"]["pmid"] == "keyword"
    assert doc["declared_fields"]["year"] == "long"
    assert doc["indices"][0]["conforms"] is False


def test_divergence_alone_does_not_fail_the_run(monkeypatch, capsys):
    """The load-bearing behaviour: existing collections do not conform and the
    fleet has to be migrated deliberately, so the default exit status is 0."""
    fields, tmpl = mc.compare_mapping(_mappings(_LUCID))
    reports = [mc.IndexReport("http://es", "i", fields=fields, has_template=tmpl)]
    monkeypatch.setattr(mc, "resolve_targets", lambda *a, **k: [("http://es", "i")])
    monkeypatch.setattr(mc, "collect", lambda *a, **k: reports)
    assert mc.main(["--es-url", "http://es"]) == 0
    assert "DIVERGENT" in capsys.readouterr().out
    assert mc.main(["--es-url", "http://es", "--fail-on-divergence"]) == 1
