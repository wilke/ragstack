"""Conformance tests for ``include_symbol_glossary`` on POST /v1/retrieve
(#754 follow-up — return each source's ingest-time symbol glossary).

Black-box: the request field is accepted (never a 422), the response stays
schema-valid (``Source.symbol_glossary`` is the only addition, optional, a
string), with the flag EVERY source carries the key, and without it NO source
does. Whether a glossary is non-empty is data-dependent (needs a document that
defines a symbol), so that is asserted only on the Python implementation, over
a document this file seeds and removes itself — the Go handler is a stub per
ADR-0006 and returns no sources, so for Go the per-source assertions are
vacuous and the contract half (field accepted, no key by default) is what is
proven. The field is ``/v1/retrieve`` only: ``/v1/query`` does not declare it.
"""
from __future__ import annotations

import asyncio
import os
import time

import httpx
import jsonschema
import pytest

pytestmark = pytest.mark.asyncio


def _headers() -> dict[str, str]:
    k = os.environ.get("RAGSTACK_API_KEY") or None
    return {"X-API-Key": k} if k else {}


def _validate(data, schema_name: str, schemas: dict[str, dict]) -> None:
    store = {s.get("$id", n): s for n, s in schemas.items()}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=data, schema=schemas[schema_name], resolver=resolver)


async def test_contract_declares_include_symbol_glossary(schemas: dict[str, dict]) -> None:
    prop = schemas["retrieve_request"]["properties"]["include_symbol_glossary"]
    assert prop["type"] == "boolean" and prop["default"] is False
    assert "include_symbol_glossary" not in schemas["query_request"]["properties"]
    src = schemas["source"]["properties"]["symbol_glossary"]
    assert src["type"] == "string"  # optional, not nullable
    assert "symbol_glossary" not in schemas["source"]["required"]


async def test_flag_accepted_and_every_source_carries_the_key(
    client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    resp = await client.post(
        "/v1/retrieve",
        json={"query": "What is RAG?", "top_k": 3, "include_symbol_glossary": True},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, "retrieve_response", schemas)
    for s in body["sources"]:
        assert "symbol_glossary" in s, "with the flag, EVERY source carries the key"
        assert isinstance(s["symbol_glossary"], str)
        assert "symbol_glossary" not in s.get("metadata", {})


async def test_default_puts_no_key_on_any_source(client: httpx.AsyncClient) -> None:
    for body in ({"query": "What is RAG?"}, {"query": "What is RAG?", "include_symbol_glossary": False}):
        resp = await client.post("/v1/retrieve", json=body, headers=_headers())
        assert resp.status_code == 200, resp.text
        assert all("symbol_glossary" not in s for s in resp.json()["sources"])


async def test_seeded_definition_comes_back_as_the_glossary(
    client: httpx.AsyncClient, schemas: dict[str, dict], impl: str
) -> None:
    """Python-first: ingest a document that defines a symbol, retrieve with
    the flag, and the chunk that uses the symbol carries its definition —
    while the same request without the flag returns the same sources with no
    key. Seeds through /v1/ingest/upload and removes the document after."""
    if impl != "python":
        pytest.skip("glossary hydration is python-first (Go's retrieve handler is a stub)")
    name = "conformance-symbol-glossary.txt"
    text = (
        "In this model, where Qb is the breathing flow rate of one person. "
        + "Further discussion of the chamber follows here. " * 3
        + "The measured value was Qb = 0.5 m3/h in the chamber.\n"
    )
    up = await client.post(
        "/v1/ingest/upload",
        files={"files": (name, text.encode(), "text/plain")},
        headers=_headers(),
    )
    if up.status_code in (404, 501, 503):
        pytest.skip(f"POST /v1/ingest/upload answered {up.status_code}; cannot seed a document")
    assert up.status_code == 202, up.text
    job_id = up.json()["job_id"]
    deadline = time.monotonic() + 30.0
    while True:
        poll = await client.get(f"/v1/ingest/{job_id}", headers=_headers())
        assert poll.status_code == 200, poll.text
        status = poll.json().get("status")
        if status == "completed":
            break
        assert status != "failed", poll.text
        assert time.monotonic() < deadline, f"seed ingest still {status!r} after 30 s"
        await asyncio.sleep(0.2)

    body = {"query": "breathing flow rate Qb", "top_k": 5, "retrieval_mode": "bm25"}
    try:
        plain = await client.post("/v1/retrieve", json=body, headers=_headers())
        withg = await client.post(
            "/v1/retrieve", json={**body, "include_symbol_glossary": True}, headers=_headers()
        )
        assert plain.status_code == 200 and withg.status_code == 200, withg.text
        _validate(withg.json(), "retrieve_response", schemas)
        sources = withg.json()["sources"]
        ours = [s for s in sources if "Qb" in s["content"]]
        assert ours, "the seeded document must be retrievable"
        assert all("symbol_glossary" in s for s in sources)
        assert any("breathing flow rate" in s["symbol_glossary"] for s in ours), [
            s["symbol_glossary"] for s in ours
        ]
        assert all("Qb —" not in s["content"] for s in sources), "never merged into content"
        # The flag adds the key and nothing else: same sources, same order.
        stripped = [{k: v for k, v in s.items() if k != "symbol_glossary"} for s in sources]
        assert stripped == plain.json()["sources"]
        assert all("symbol_glossary" not in s for s in plain.json()["sources"])
    finally:
        listing = await client.get("/v1/documents", headers=_headers())
        if listing.status_code == 200:
            for doc in listing.json():
                if str(doc.get("source", "")).endswith(name):
                    await client.delete(f"/v1/documents/{doc['doc_id']}", headers=_headers())
