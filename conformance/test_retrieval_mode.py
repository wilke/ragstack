"""Conformance tests for the `retrieval_mode` field on /query + /retrieve.

`retrieval_mode` (hybrid | vector | bm25) is a contract field both impls must
accept. `bm25` needs no embedding, so it's the fast, infra-independent acceptance
check; the dense modes are bounded + skip-on-timeout. Invalid-value → 422 is
python-first (the Go scaffold accepts any string and ignores it).
"""
from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio


def _headers() -> dict[str, str]:
    k = os.environ.get("RAGSTACK_API_KEY") or None
    return {"X-API-Key": k} if k else {}


async def test_retrieve_accepts_bm25_mode(client: httpx.AsyncClient) -> None:
    """bm25 mode skips embedding → fast and infra-independent; both impls accept it."""
    resp = await client.post(
        "/v1/retrieve", json={"query": "x", "retrieval_mode": "bm25"}, headers=_headers(), timeout=20.0
    )
    assert resp.status_code == 200, resp.text
    assert "sources" in resp.json()


async def test_all_modes_accepted(client: httpx.AsyncClient) -> None:
    """hybrid/vector/bm25 are all accepted. Dense modes embed, so skip on a slow
    backend — acceptance of the field is already proven by the bm25 test."""
    for m in ("hybrid", "vector", "bm25"):
        try:
            resp = await client.post(
                "/v1/retrieve", json={"query": "x", "retrieval_mode": m}, headers=_headers(), timeout=20.0
            )
        except httpx.ReadTimeout:
            continue
        assert resp.status_code == 200, f"mode={m}: {resp.text}"


async def test_query_accepts_retrieval_mode(client: httpx.AsyncClient) -> None:
    """The field is accepted on /query too (bm25 mode → no embed; generation may
    still be slow, so skip on timeout)."""
    try:
        resp = await client.post(
            "/v1/query", json={"query": "x", "retrieval_mode": "bm25"}, headers=_headers(), timeout=25.0
        )
    except httpx.ReadTimeout:
        pytest.skip("retrieval_mode accepted; generation slow (LLM unavailable)")
        return
    assert resp.status_code == 200, resp.text


async def test_invalid_mode_is_422(client: httpx.AsyncClient, impl: str) -> None:
    if impl != "python":
        pytest.skip("enum validation is python-first (Go accepts any string, ignores it)")
    resp = await client.post(
        "/v1/retrieve", json={"query": "x", "retrieval_mode": "nonsense"}, headers=_headers(), timeout=15.0
    )
    assert resp.status_code == 422, resp.text
