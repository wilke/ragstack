"""The duplicate-work report (#747 D7): grouping and the read-only ES scan.

No cluster: ``scan_index`` runs over an ``httpx.MockTransport`` serving the
trimmed shape of the dev ``openchia`` aggregation — the Bazant & Bush article's
six copies (three enriched, two with the malformed ``…/-/`` DOI and no other
id, one Europe PMC rendering with no id field but a ``PMC…`` file name), an
unrelated paper, and a document with no id at all.
"""
from __future__ import annotations

import json

import httpx

from ragstack.ops.duplicates import DocEntry, group_documents, render_text, scan_index

DOI = "10.1073/pnas.2018995118"
FULL = {"doi": DOI, "pmcid": "PMC8092463", "pmid": "33858987"}


def _bucket(doc_id: str, meta: dict, count: int = 50) -> dict:
    return {"key": {"doc_id": doc_id}, "doc_count": count,
            "exemplar": {"hits": {"hits": [{"_source": {"doc_id": doc_id, "metadata": meta}}]}}}


PAGE = [
    _bucket("a", {**FULL, "filename": "sha-42e1.md", "title": "A guideline"}, 83),
    _bucket("b", {**FULL, "filename": "sha-3772.md"}, 79),
    _bucket("c", {"doi": f"{DOI}/-/", "filename": "sha-4874.md"}, 77),
    _bucket("d", {"doi": f"{DOI}/-/", "filename": "sha-5035.md"}, 74),
    _bucket("e", {**FULL, "filename": "sha-9e85.md"}, 69),
    _bucket("f", {"filename": "PMC8092463.md"}, 43),
    _bucket("g", {"doi": "10.1000/other", "filename": "sha-x.md"}, 12),
    _bucket("h", {"filename": "notes.md"}, 3),
]


def _transport(seen: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path.endswith("/_search")
        body = json.loads(request.content)
        seen.append(body)
        if body["aggs"]["docs"]["composite"].get("after"):
            return httpx.Response(200, json={"aggregations": {"docs": {"buckets": []}}})
        return httpx.Response(200, json={"aggregations": {"docs": {
            "buckets": PAGE[:5], "after_key": {"doc_id": "e"}}}})
    return handler


def test_scan_groups_the_six_copies_and_counts_the_unidentified(monkeypatch) -> None:
    import ragstack.ops.duplicates as dup

    monkeypatch.setattr(dup, "PAGE_SIZE", 5)
    seen: list[dict] = []
    pages = iter([PAGE[:5], PAGE[5:], []])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        buckets = next(pages)
        agg: dict = {"buckets": buckets}
        if buckets:
            agg["after_key"] = buckets[-1]["key"]
        return httpx.Response(200, json={"aggregations": {"docs": agg}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        rep = scan_index("http://es.test", "ragstack_lib_openchia_x", client=c)
    assert rep.error == "" and rep.n_docs == 8 and rep.n_without_ids == 1
    (group,) = rep.groups
    assert [d.doc_id for d in group.docs] == ["a", "b", "c", "d", "e", "f"]
    assert group.keys == [f"doi:{DOI}", "pmcid:PMC8092463", "pmid:33858987"]
    # read-only, size 0, paged by the composite after_key, exemplar ids only
    assert all(b["size"] == 0 for b in seen) and len(seen) == 2
    assert seen[1]["aggs"]["docs"]["composite"]["after"] == {"doc_id": "e"}
    assert "content" not in json.dumps(seen[0]["aggs"]["docs"]["aggs"])
    text = render_text([rep])
    assert "1 duplicate group(s) covering 6 document(s)" in text and "PMC8092463.md" in text


def test_scan_filters_by_tenant_when_asked() -> None:
    seen: list[dict] = []
    with httpx.Client(transport=httpx.MockTransport(_transport(seen))) as c:
        scan_index("http://es.test", "i", tenants=["dev"], client=c)
    assert seen[0]["query"] == {"bool": {"filter": [{"terms": {"metadata.tenant_id": ["dev"]}}]}}


def test_scan_failure_is_recorded_not_raised() -> None:
    with httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(500, json={}))) as c:
        rep = scan_index("http://es.test", "i", client=c)
    assert rep.error.startswith("HTTPStatusError") and rep.groups == []


def test_grouping_is_transitive_across_key_kinds() -> None:
    docs = [
        DocEntry("x", 1, ["doi:10.1/a"]),
        DocEntry("y", 1, ["doi:10.1/a", "pmcid:PMC1"]),
        DocEntry("z", 1, ["pmcid:PMC1"]),
        DocEntry("solo", 1, ["pmid:9"]),
        DocEntry("none", 1, []),
    ]
    groups, no_ids = group_documents("i", docs)
    assert no_ids == 1
    assert [sorted(d.doc_id for d in g.docs) for g in groups] == [["x", "y", "z"]]


def test_help_renders(capsys) -> None:
    import pytest

    from ragstack.ops.duplicates import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0 and "Read-only" in capsys.readouterr().out
