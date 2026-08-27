"""Conformance: error bodies on the query surface match ``error.json`` (#427 W6).

.. rubric:: The triggering problem, and how this file answers it

The contract wires ``content: Error`` onto the **503** responses of
``POST /v1/query`` and ``POST /v1/retrieve`` — the response #427's incident
produced. A conformance test cannot make a *healthy* server emit that body: it
would have to break a backing store, and there is no endpoint that asks it to.
Three options were considered:

1. *Point a fixture collection at a dead store.* Rejected — the suite has no way
   to create a collection with a bad ``QDRANT_URL`` (the URL is process-wide
   configuration, not per-collection), and a fixture that could reconfigure a
   running server's store would be a far larger hole than the test is worth.
2. *Skip the whole file with a clear reason.* Rejected — a file that never
   asserts anything is indistinguishable from a file that is wrong, and this
   repo has shipped tests satisfied by an absent thing before.
3. **Split it by what is actually reachable.** Taken, and it is what follows.

So the claim is pinned in three layers, deliberately:

* **Deterministic, in-process:** ``python/tests/api/test_error_schema.py``
  swaps the retriever for one that raises ``StoreUnavailable`` and validates the
  real 503 body against this same ``error.json``. That is the actual proof, and
  it runs on every ``make test-python``.
* **Black-box, always:** the tests below validate the error bodies a healthy
  server *does* produce on those two endpoints — the 422 every implementation
  emits, and an unknown-collection error where the implementation has
  collections. ``Error`` is one shape for all of them (``detail`` required,
  everything else optional), so these exercise the same schema that the 503
  refers to, including the deliberately untyped ``detail``.
* **Black-box, opportunistic:** if a 503 *does* arrive — a real store hiccup, a
  restoring collection, a tenant at capacity — it is validated, and its
  ``reason`` (when present) must be one the contract knows. That test skips when
  no 503 occurs and says so; it is a bonus, never the file's evidence.

Both implementations run this. Go's ``writeValidationError`` already emits
``{"detail": [{loc, msg, type}]}``, which validates — so the 422 test is a real
cross-implementation pin today, not a W7 promise.
"""

from __future__ import annotations

import httpx
import jsonschema
import pytest

pytestmark = pytest.mark.asyncio

ENDPOINTS = ["/v1/query", "/v1/retrieve"]

#: The `reason` values contracts/schemas/error.json enumerates. A body carrying
#: one outside this set is a contract break under `additionalProperties: false`
#: — but the enum is what actually catches it, so it is asserted separately.
KNOWN_REASONS = {"timeout", "unreachable", "error"}


def _error_schema(schemas: dict[str, dict]) -> dict:
    schema = schemas.get("error")
    assert schema is not None, "contracts/schemas/error.json is missing (#427 W6)"
    return schema


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_a_malformed_request_is_an_error_body(
    client: httpx.AsyncClient, schemas: dict[str, dict], endpoint: str
) -> None:
    """A request with no ``query`` at all. Every implementation refuses it, so
    this is the one error body that is reachable black-box on both."""
    resp = await client.post(endpoint, json={"not_a_query": 1})

    assert 400 <= resp.status_code < 500, f"expected a client error, got {resp.status_code}"
    jsonschema.validate(instance=resp.json(), schema=_error_schema(schemas))


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_the_validation_bodys_detail_is_a_list(
    client: httpx.AsyncClient, endpoint: str
) -> None:
    """Why ``detail`` carries no ``type`` in the schema, demonstrated over HTTP.

    A validation body's ``detail`` is a LIST of per-field errors, not a
    sentence. Tightening the schema to ``{"type": "string"}`` — the obvious
    "tidy-up" — would make it wrong for this body, which is emitted by FastAPI
    and by Go's ``writeValidationError`` alike and is not ours to reshape.
    """
    resp = await client.post(endpoint, json={"not_a_query": 1})
    assert resp.status_code == 422, resp.text
    assert isinstance(resp.json()["detail"], list)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_an_unknown_collection_error_is_an_error_body(
    client: httpx.AsyncClient, schemas: dict[str, dict], endpoint: str
) -> None:
    """The other reachable error on these endpoints: naming a collection that
    does not exist. It is a ``detail``-only body with no ``request_id`` and no
    ``reason`` — which is why the schema makes both optional rather than
    required.
    """
    resp = await client.post(
        endpoint,
        json={"query": "conformance", "collection": "no_such_collection_conformance"},
    )
    if resp.status_code < 400:
        pytest.skip(
            "this implementation does not resolve `collection` yet — it answered "
            f"{resp.status_code}, so there is no error body to validate"
        )
    jsonschema.validate(instance=resp.json(), schema=_error_schema(schemas))


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_a_503_validates_and_names_a_reason_the_contract_knows(
    client: httpx.AsyncClient, schemas: dict[str, dict], endpoint: str
) -> None:
    """Opportunistic — see the module docstring. This is the body the contract
    wires ``content:`` to, and a healthy server will not produce it on demand.
    When one does arrive it is checked properly; when it does not, this skips
    rather than passing quietly, so a reader is never misled into thinking the
    503 shape was verified here.
    """
    resp = await client.post(endpoint, json={"query": "conformance"})
    if resp.status_code != 503:
        pytest.skip(
            f"the server answered {resp.status_code}; a store-unavailable 503 cannot "
            "be triggered black-box against a healthy server. The deterministic "
            "check for this body is python/tests/api/test_error_schema.py."
        )

    body = resp.json()
    jsonschema.validate(instance=body, schema=_error_schema(schemas))

    reason = body.get("reason")
    if reason is not None:
        assert reason in KNOWN_REASONS, f"unknown reason {reason!r} in a 503 body"
        # A store-unavailable 503 always carries the correlation id too; the
        # other 503 causes carry neither field.
        assert body.get("request_id") == resp.headers.get("X-Request-Id")
