"""Conformance: the ``filters`` value grammar is a 400 on both impls (#471).

The defect this pins was mode-dependent, which is exactly what a black-box
suite is for. ``{"year": {"gte": 2025}}`` was a **500** — a bare ``pydantic``
``ValidationError`` from Qdrant's ``MatchValue`` escaping into Starlette — while
``{"year": "2025"}`` was a **200 with a hit count that depended on the
retrieval leg**: Elasticsearch coerces a numeric string on a dynamically-mapped
``long``, Qdrant compares typed and matched nothing. Neither is something the
caller can see from the contract, so both become one refusal at the boundary.

**Ungated on purpose.** Go's handlers are stubs that never read ``Filters``, so
without a decode-level shape check there this file would pass vacuously against
Go — a stub answering 200 to everything is not agreement with Python, it is the
absence of a test. The check ships in ``go/internal/api/filters.go`` in the same
change, so both implementations really answer 400 here.

Read-only: no collection is created, no document ingested, nothing deleted.
"""

from __future__ import annotations

import httpx
import pytest


pytestmark = pytest.mark.asyncio


# Each row is a value no vector store in this system can represent. The bound is
# what Qdrant's MatchValue / MatchAny actually accept, measured rather than
# assumed: MatchValue takes a bool but MatchAny does not, and MatchAny is
# list[str] | list[int] rather than "a list of scalars".
REFUSED = [
    ("dict-range-operator", {"year": {"gte": 2025}}),
    ("float", {"score": 1.5}),
    ("null", {"doi": None}),
    ("nested-list", {"tags": [["a"]]}),
    ("bool-in-list", {"is_oa": [True]}),
    ("mixed-str-int-list", {"doc_type": ["article", 3]}),
    ("str-for-int-field", {"year": "2025"}),
]
REFUSED_IDS = [r[0] for r in REFUSED]

ACCEPTED = [
    ("str", {"doc_type": "article"}),
    ("int", {"year": 2025}),
    ("bool", {"is_oa": True}),
    ("str-list", {"doc_type": ["article", "supplement"]}),
    ("int-list", {"year": [2025, 2026]}),
    # #196: a legal filter that matches nothing — NOT a refusal, and not
    # "unconstrained" either.
    ("empty-list", {"doc_type": []}),
]
ACCEPTED_IDS = [a[0] for a in ACCEPTED]

ENDPOINTS = ["/v1/query", "/v1/retrieve"]


@pytest.mark.parametrize("_name,filters", REFUSED, ids=REFUSED_IDS)
@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_unsupported_filter_value_is_400(
    client: httpx.AsyncClient, endpoint: str, _name: str, filters: dict
) -> None:
    """400 — never a 500, and never a 200 whose result silently depends on
    which retrieval leg ran."""
    resp = await client.post(endpoint, json={"query": "anything", "filters": filters})
    assert resp.status_code == 400, (
        f"{endpoint} with filters={filters!r} answered {resp.status_code}: {resp.text}"
    )


@pytest.mark.parametrize("_name,filters", REFUSED, ids=REFUSED_IDS)
@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_refusal_body_names_the_grammar(
    client: httpx.AsyncClient, endpoint: str, _name: str, filters: dict
) -> None:
    """A caller must be able to fix the request from the response alone, so the
    body names the accepted grammar and says range operators are unsupported
    rather than leaving them to look like a silently-ignored input.

    ``detail`` is deliberately untyped in ``error.json`` (it is a string here
    and a list of field errors on a 422), so this reads it as text."""
    resp = await client.post(endpoint, json={"query": "anything", "filters": filters})
    assert resp.status_code == 400
    detail = str(resp.json().get("detail", ""))
    assert "a string, an integer or a boolean" in detail, detail
    assert "range operators" in detail, detail


@pytest.mark.parametrize("_name,filters", ACCEPTED, ids=ACCEPTED_IDS)
@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_supported_filter_values_are_accepted(
    client: httpx.AsyncClient, endpoint: str, _name: str, filters: dict
) -> None:
    """The refusal must not have swallowed the grammar it protects: every
    documented value shape still returns 200."""
    resp = await client.post(endpoint, json={"query": "anything", "filters": filters})
    assert resp.status_code == 200, (
        f"{endpoint} with filters={filters!r} answered {resp.status_code}: {resp.text}"
    )
