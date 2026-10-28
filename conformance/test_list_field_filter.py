"""Conformance: a filter on a LIST-valued metadata field matches by any element
(#828 PR 1, design note §6 "List-valued fields and the in-memory predicate").

Qdrant and Elasticsearch already match a scalar filter against any element of
a multi-valued field. The in-memory predicates did not: ``stores/memory`` and
``stores/filters.payload_matches`` (the re-check ``get_chunks`` applies, i.e.
context expansion) compared the whole list, so ``{"authors": "X"}`` matched
nothing there while both real legs matched. The structure series' role filter
(``section_roles``) is a list filter, so this is its prerequisite.

The test ingests ONE synthetic two-author JSONL document by reference into a
scratch collection, then retrieves with a scalar filter on ``authors`` (a list
on every chunk, via ``enrich.parse_authors``) at ``top_k=1`` with
``context_window=1`` — so the matched chunk's neighbour is fetched through
``get_chunks`` with the same filter. It needs a server whose ``INGEST_ROOT``
this process can write into: ``make test-conformance-keyed`` boots one and
exports ``RAGSTACK_CONFORMANCE_INGEST_ROOT``; elsewhere the module skips.
Python-first: Go's ingest and retrieval handlers are stubs (ADR-0006).

Creates one collection (prefix ``conf-listfilter-``) and purges it, verified by
listing.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

import httpx
import pytest

SCRATCH_PREFIX = "conf-listfilter-"
AUTHOR = "Conformance Author Two"
PARAGRAPH = (
    "The synthetic conformance corpus describes a measurement procedure in some "
    "detail, so that the document is long enough to be cut into several chunks. "
)


@pytest.fixture(scope="module")
def scratch(base_url: str, impl: str, auth_headers: dict[str, str]):
    if impl != "python":
        pytest.skip("ingest and retrieval are Python-first; the Go handlers are stubs (ADR-0006)")
    root = os.environ.get("RAGSTACK_CONFORMANCE_INGEST_ROOT")
    if not root:
        pytest.skip("needs a server whose INGEST_ROOT this suite can write into "
                    "(make test-conformance-keyed exports RAGSTACK_CONFORMANCE_INGEST_ROOT)")
    cid = f"{SCRATCH_PREFIX}{secrets.token_hex(4)}"
    path = Path(root) / f"{cid}.jsonl"
    rec = {"text": PARAGRAPH * 40, "path": f"{cid}-paper.txt",
           "metadata": {"title": "A synthetic paper", "authors": f"Conformance Author One; {AUTHOR}"}}
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    with httpx.Client(base_url=base_url, timeout=30.0, headers=auth_headers) as c:
        created = c.post("/v1/collections", json={"id": cid})
        assert created.status_code == 201, f"{created.status_code} {created.text[:300]}"
        try:
            resp = c.post("/v1/ingest", json={"source": str(path), "collection": cid})
            assert resp.status_code == 200, resp.text
            job = resp.json()["job_id"]
            deadline = time.monotonic() + 20.0
            while True:
                body = c.get(f"/v1/ingest/{job}").json()
                if body["status"] in ("completed", "failed") or time.monotonic() > deadline:
                    break
                time.sleep(0.2)
            assert body["status"] == "completed", body
            yield cid
        finally:
            c.delete(f"/v1/collections/{cid}?purge=true")
            path.unlink(missing_ok=True)
            listed = [e["id"] for e in c.get("/v1/collections").json()["collections"]]
            assert cid not in listed, f"teardown did not remove {cid!r}; delete it by hand"


def _retrieve(base_url: str, headers: dict[str, str], cid: str, filters: dict) -> dict:
    with httpx.Client(base_url=base_url, timeout=30.0, headers=headers) as c:
        resp = c.post("/v1/retrieve", json={
            "query": "synthetic measurement procedure", "collection": cid, "top_k": 1,
            "context_window": 1, "filters": filters,
        })
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_scalar_filter_matches_one_element_of_a_list_field(
    scratch: str, base_url: str, auth_headers: dict[str, str]
) -> None:
    body = _retrieve(base_url, auth_headers, scratch, {"authors": AUTHOR})
    assert body["sources"], "a scalar filter on a list field matched nothing"
    src = body["sources"][0]
    assert AUTHOR in src["metadata"]["authors"]
    # The neighbour came through get_chunks with the same filter: the
    # context-expansion re-check agrees with the retrieval legs.
    assert src.get("context"), "context expansion dropped the neighbour of a list-field match"


def test_a_list_filter_matches_by_membership_and_a_miss_is_empty(
    scratch: str, base_url: str, auth_headers: dict[str, str]
) -> None:
    hit = _retrieve(base_url, auth_headers, scratch, {"authors": ["Nobody", AUTHOR]})
    assert hit["sources"]
    miss = _retrieve(base_url, auth_headers, scratch, {"authors": "Nobody At All"})
    assert miss["sources"] == []
