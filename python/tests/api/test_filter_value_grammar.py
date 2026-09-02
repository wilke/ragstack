"""The API boundary answers an unsupported ``filters`` value with 400 (#471).

The reported defect: ``POST /v1/retrieve`` with ``{"year": {"gte": 2025}}``
returned **500**, logged ``unhandled ValidationError`` — a bare ``pydantic``
error from Qdrant's ``MatchValue`` escaping into Starlette's generic handler.
Its sibling was quieter: ``{"year": "2025"}`` matched on the BM25 leg (ES
coerces a numeric string on a dynamically-mapped ``long``) and matched nothing
on the vector leg, so the hit count silently depended on the retrieval mode.

This file is the ENDPOINT half of the fix. The grammar itself, and the
agreement of the four interpreters that read it, is
``tests/unit/test_filter_grammar_contract.py`` — that file is what makes the
narrowest passing mutation of this one (catch the error in the router, return a
generic 400, leave the stores raising pydantic errors) fail.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


# Every row is a value the store layer could not represent; before #471 each
# was a 500, an ES search error, or a silent zero-hit read depending on the leg.
REFUSED = [
    ("dict-range-operator", {"year": {"gte": 2025}}, ("year", "range operators")),
    ("float", {"score": 1.5}, ("score", "float")),
    ("null", {"doi": None}, ("doi", "NoneType")),
    ("nested-list", {"tags": [["a"]]}, ("tags", "list")),
    ("bool-in-list", {"is_oa": [True]}, ("is_oa", "scalar-only")),
    ("mixed-list", {"doc_type": ["article", 3]}, ("doc_type", "all strings or all integers")),
    ("str-for-int-field", {"year": "2025"}, ("year", "integer field")),
    ("str-for-int-field-in-list", {"year": ["2025"]}, ("year", "integer field")),
]
REFUSED_IDS = [r[0] for r in REFUSED]

ACCEPTED = [
    ("str", {"doc_type": "article"}),
    ("int", {"year": 2025}),
    ("bool", {"is_oa": True}),
    ("str-list", {"doc_type": ["article", "supplement"]}),
    ("int-list", {"year": [2025, 2026]}),
    ("empty-list", {"doc_type": []}),  # matches nothing, but is a legal filter (#196)
]
ACCEPTED_IDS = [a[0] for a in ACCEPTED]


@pytest.mark.parametrize("_name,filters,fragments", REFUSED, ids=REFUSED_IDS)
@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
async def test_unsupported_filter_value_is_400_on_both_endpoints(
    client, endpoint, _name, filters, fragments
):
    """400, never 500 — and the body names the field AND the accepted grammar,
    so the caller can fix the request from the response alone."""
    resp = await client.post(endpoint, json={"query": "anything", "filters": filters})
    assert resp.status_code == 400, f"{endpoint} {filters!r} → {resp.status_code}: {resp.text}"
    detail = str(resp.json().get("detail", resp.text))
    for fragment in fragments:
        assert fragment in detail, f"{fragment!r} missing from {detail!r}"
    # The whole grammar, on every refusal — including that range operators are
    # a planned feature and not something quietly ignored.
    assert "a string, an integer or a boolean" in detail
    assert "range operators" in detail


@pytest.mark.parametrize("_name,filters", ACCEPTED, ids=ACCEPTED_IDS)
@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
async def test_supported_filter_values_still_pass(client, endpoint, _name, filters):
    """The refusal must not have swallowed the grammar it is protecting: every
    documented value shape still reaches retrieval and returns 200."""
    resp = await client.post(endpoint, json={"query": "anything", "filters": filters})
    assert resp.status_code == 200, f"{endpoint} {filters!r} → {resp.status_code}: {resp.text}"


@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
async def test_refusal_precedes_retrieval(client, endpoint):
    """The check runs at the boundary, before any store is touched — so an
    invalid value is refused whether or not the corpus happens to be empty,
    and a store that is down cannot turn this 400 into a 503."""
    resp = await client.post(
        endpoint,
        json={"query": "anything", "filters": {"year": {"gte": 2025}}, "top_k": 1},
    )
    assert resp.status_code == 400
    assert "range operators" in str(resp.json().get("detail", ""))
