"""Conformance tests for named server-side prompt templates (ADR-0008,
``GET /v1/prompt-templates`` + ``template`` / ``template_vars`` on POST /v1/query).

Black-box. The claims, each in a named test:

* **The listing is a declaration, never the prompt.** ``GET /v1/prompt-templates``
  is 200 and schema-valid whether or not any template is configured, and every
  entry carries NO ``system`` and NO ``user`` key. The bodies are operator
  configuration: publishing them hands every authenticated caller the tenant's
  tuning and turns a template into something to paste into the level-3 request
  ADR-0008 refused to build. ``additionalProperties: false`` already forbids
  them; this file also says so by name, because that is the one field pair whose
  absence is a *decision* rather than a schema accident.
* **The compatibility guarantee, which is the load-bearing one.** A ``/v1/query``
  request that omits ``template`` gets a response with no ``template``,
  ``template_version``, ``template_hash`` or ``model`` key AT ALL — absent, not
  ``null``. ADR-0008 decision 2 states this as "absent ``template`` ⇒
  byte-identical behaviour to today", and ``null`` would still be a new key: a
  client parsing the old shape with a strict decoder breaks on it. This test
  therefore asserts key ABSENCE, not falsiness, and runs against **every**
  server — a deployment that has never heard of templates satisfies it trivially,
  which is exactly the point.
* **An unknown id is a 404**, with the suite's error envelope.
* **Bad ``template_vars`` is a 422** — a required slot left unset, a slot the
  template does not declare, and a value past the slot's declared ``max_len``.
  All three are derived from the server's OWN listing (pick a template, read its
  ``slots``, build values from the declared ``required``/``max_len``) rather than
  from a hardcoded id or length, so any conformant server passes rather than only
  the one this file was written against.
* **A well-formed templated request** is 200, stays schema-valid, and echoes
  ``template``, ``template_version`` and ``template_hash`` equal to what the
  listing reported for that id, plus a non-empty ``model``. The ``answer``'s
  CONTENT is deliberately not asserted anywhere here: a model's text is not a
  contract, and a conformance suite that pinned it would fail on a model swap
  that broke nothing.
* **Resolution precedes retrieval.** An unknown id together with an expensive
  retrieval still answers 404. See the test for what that can and cannot prove
  over HTTP.

**Two different absences, deliberately distinguished.**

``GET /v1/prompt-templates`` answering **404** means the capability is not
implemented here (the Go scaffold, or any build before ADR-0008): the tests that
need the endpoint skip with *prompt templates not implemented on this server*.
That is NOT a credential skip — ``run_authz_keyed.sh`` fails the run on any
``RAGSTACK_CREDENTIAL_SKIP``, and a server that simply lacks a surface is a
legitimate absence, not a provisioning bug, so it is never reported as one.

The endpoint answering **200 with an empty list** is something else entirely: a
server that implements the capability and has no templates configured. That is a
*supported state*, so it is tested, not skipped — the listing test runs and
passes on it. Only the tests that must NAME a template skip there, with *prompt
templates not configured on this server*, because there is nothing to name.
Both are detected from the endpoint's own answer, never from a missing key.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import jsonschema
import pytest

pytestmark = pytest.mark.asyncio

NOT_IMPLEMENTED = (
    "prompt templates not implemented on this server "
    "(GET /v1/prompt-templates -> 404)"
)
NOT_CONFIGURED = (
    "prompt templates not configured on this server "
    "(GET /v1/prompt-templates -> 200 with an empty list, which is a supported "
    "state; only the tests that must name a template skip)"
)

#: The four provenance keys ADR-0008 adds to a query response. An untemplated
#: request must carry none of them.
PROVENANCE_KEYS = ("template", "template_version", "template_hash", "model")

#: An id that cannot exist. Shaped to satisfy the contract's id pattern
#: (``^[a-z0-9][a-z0-9_-]{0,63}$``) on purpose: an id the server could reject on
#: FORM would answer 422 and tell us nothing about resolution. The listing is
#: checked for it rather than assumed absent.
UNKNOWN_ID = "conf-no-such-template-0000"

#: A slot name no template should declare; asserted against the listing, not hoped.
UNDECLARED_SLOT = "conf_undeclared_slot_0000"

#: Retrieval string for every request here. What the model says about it is never
#: asserted; only the envelope is.
QUERY = "What is RAG?"


def _headers() -> dict[str, str]:
    k = os.environ.get("RAGSTACK_API_KEY") or None
    return {"X-API-Key": k} if k else {}


def _validate(data, schema_name: str, schemas: dict[str, dict]) -> None:
    store = {s.get("$id", n): s for n, s in schemas.items()}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=data, schema=schemas[schema_name], resolver=resolver)


def _assert_error_envelope(resp: httpx.Response, schemas: dict[str, dict]) -> None:
    """The body matches ``error.json``, when there is a JSON body at all.

    Tolerant of a non-JSON body for the same reason ``test_grading`` is: a
    framework's own plain-text error for a route it does not have is absence, not
    a violation of a contract that does not apply. A JSON body, however, is the
    API speaking and must be in the envelope the rest of the suite expects.
    """
    try:
        body = resp.json()
    except json.JSONDecodeError:
        return
    if isinstance(body, dict):
        jsonschema.validate(instance=body, schema=schemas["error"])


# --------------------------------------------------------------------------- #
# Absence, in its two forms
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def templates_body(
    base_url: str, auth_headers: dict[str, str], schemas: dict[str, dict]
) -> dict[str, Any]:
    """``GET /v1/prompt-templates``, or a module-level skip when it is not mounted.

    Session-scoped and synchronous — one probe for the whole file, and no
    session-scoped ``AsyncClient`` bound to the first test's event loop (see
    ``conftest.client``).
    """
    with httpx.Client(base_url=base_url, timeout=30.0, headers=auth_headers) as c:
        resp = c.get("/v1/prompt-templates")
    if resp.status_code == 404:
        _assert_error_envelope(resp, schemas)
        pytest.skip(NOT_IMPLEMENTED)
    assert resp.status_code == 200, (
        f"GET /v1/prompt-templates answered {resp.status_code}, which is neither "
        f"the surface (200) nor its absence (404): {resp.text[:300]}"
    )
    body = resp.json()
    assert isinstance(body, dict), f"expected an object, got {type(body).__name__}"
    return body


@pytest.fixture(scope="session")
def configured(templates_body: dict[str, Any]) -> list[dict[str, Any]]:
    """The configured templates, or a skip when there are none to name.

    Not a credential skip: an empty list is the endpoint reporting a supported
    state, and it is reached only after the surface is known to exist.
    """
    items = templates_body["templates"]
    if not items:
        pytest.skip(NOT_CONFIGURED)
    return items


# --------------------------------------------------------------------------- #
# Deriving requests from the server's own answer
# --------------------------------------------------------------------------- #
def _slots(template: dict[str, Any]) -> list[dict[str, Any]]:
    return list(template.get("slots") or [])


def _value(slot: dict[str, Any]) -> str:
    """A valid value for *slot*: non-empty (ADR-0008 §3a — ``""`` for a required
    slot renders the same empty clause a missing one would, and is refused) and
    within the declared cap."""
    return "x" * min(8, int(slot["max_len"]))


def _fill_required(template: dict[str, Any], skip: str | None = None) -> dict[str, str]:
    """Valid values for every required slot, optionally omitting one by name."""
    return {
        s["name"]: _value(s)
        for s in _slots(template)
        if s["required"] and s["name"] != skip
    }


def _pick(templates: list[dict[str, Any]], want: str) -> dict[str, Any]:
    """The first template satisfying *want*, or a skip naming what was missing.

    Deterministic: the listing is documented as ordered by id, so "the first" is
    stable across restarts and a failure is reproducible.
    """
    for t in templates:
        if want == "any":
            return t
        if want == "required-slot" and any(s["required"] for s in _slots(t)):
            return t
        if want == "slot" and _slots(t):
            return t
    pytest.skip(
        f"no template on this server declares a {want}; that is a property of "
        "this deployment's configuration, not a missing credential"
    )


# --------------------------------------------------------------------------- #
# 0. The contract itself — runs with no server state at all
# --------------------------------------------------------------------------- #
async def test_contract_declares_templates(schemas: dict[str, dict]) -> None:
    req = schemas["query_request"]["properties"]
    assert req["template"]["type"] == ["string", "null"]
    assert req["template"]["default"] is None
    assert req["template_vars"]["additionalProperties"]["type"] == "string"

    resp = schemas["query_response"]["properties"]
    assert resp["template"]["type"] == "string"
    assert resp["template_version"]["type"] == "integer"
    assert resp["template_hash"]["type"] == "string"
    assert resp["model"]["type"] == "string"
    # Optional, never required: absent is the untemplated shape, which is the
    # whole compatibility guarantee.
    assert not set(PROVENANCE_KEYS) & set(schemas["query_response"].get("required", []))
    assert schemas["query_response"]["additionalProperties"] is False

    item = schemas["prompt_templates_response"]["properties"]["templates"]["items"]
    assert item["additionalProperties"] is False
    assert "system" not in item["properties"] and "user" not in item["properties"]
    assert set(item["required"]) == {"id", "version", "hash", "label", "output", "slots"}
    slot = item["properties"]["slots"]["items"]
    assert set(slot["required"]) == {"name", "required", "max_len"}
    assert slot["additionalProperties"] is False


# --------------------------------------------------------------------------- #
# 1. The listing
# --------------------------------------------------------------------------- #
async def test_listing_is_schema_valid(
    templates_body: dict[str, Any], schemas: dict[str, dict]
) -> None:
    """200 and schema-valid whether or not anything is configured.

    An empty list is not skipped here — it is the answer a server with the
    capability and no templates is REQUIRED to give, and it is how a client
    learns the capability is absent without a version check.
    """
    _validate(templates_body, "prompt_templates_response", schemas)
    assert isinstance(templates_body["templates"], list)


async def test_listing_never_carries_prompt_bodies(
    templates_body: dict[str, Any]
) -> None:
    """No ``system``, no ``user``, on any entry.

    Implied by ``additionalProperties: false`` and asserted separately anyway:
    the schema forbids every unmodelled key, while ADR-0008's decision is about
    these two specifically. If the schema were ever widened, this must still fail.
    """
    for t in templates_body["templates"]:
        leaked = [k for k in ("system", "user") if k in t]
        assert not leaked, (
            f"template {t.get('id')!r} published its prompt {leaked}: the bodies "
            "are operator configuration, not caller-readable content (ADR-0008)"
        )


async def test_table_templates_declare_columns(
    configured: list[dict[str, Any]]
) -> None:
    """``columns`` is present exactly when ``output`` is ``table``."""
    for t in configured:
        if t["output"] == "table":
            assert t.get("columns"), f"{t['id']!r}: output=table with no columns"
        else:
            assert "columns" not in t, f"{t['id']!r}: output={t['output']} with columns"


# --------------------------------------------------------------------------- #
# 2. THE COMPATIBILITY GUARANTEE
# --------------------------------------------------------------------------- #
async def test_untemplated_query_carries_no_provenance_keys(
    client: httpx.AsyncClient, schemas: dict[str, dict]
) -> None:
    """A request with no ``template`` gets a response with none of the four keys.

    ABSENT, not ``null`` — hence ``key not in body`` rather than a falsiness
    check. ``{"template": null}`` is a new key on the wire, and a client with a
    strict decoder (or one that branches on ``"model" in body``) sees a shape it
    has never seen. ADR-0008 calls this a conformance assertion rather than an
    aspiration; this is it.

    Deliberately NOT gated on the presence fixture: this claim binds every
    server, including one that has never implemented templates, and gating it
    would silently drop the only assertion here that an old deployment can run.
    """
    resp = await client.post(
        "/v1/query", json={"query": QUERY, "top_k": 3}, headers=_headers()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    leaked = sorted(k for k in PROVENANCE_KEYS if k in body)
    assert not leaked, (
        f"an untemplated /v1/query response carried {leaked}. ADR-0008 decision 2: "
        "absent `template` must be byte-identical to a server without the "
        "feature, and a key present with a null value is still a new key."
    )
    _validate(body, "query_response", schemas)


# --------------------------------------------------------------------------- #
# 3. Unknown id
# --------------------------------------------------------------------------- #
async def test_unknown_template_is_404(
    client: httpx.AsyncClient,
    templates_body: dict[str, Any],
    schemas: dict[str, dict],
) -> None:
    ids = {t["id"] for t in templates_body["templates"]}
    assert UNKNOWN_ID not in ids, (
        f"this file's deliberately-unknown id {UNKNOWN_ID!r} is configured on "
        "this server; rename the constant, the test is otherwise vacuous"
    )
    resp = await client.post(
        "/v1/query",
        json={"query": QUERY, "template": UNKNOWN_ID},
        headers=_headers(),
    )
    assert resp.status_code == 404, resp.text
    _assert_error_envelope(resp, schemas)


# --------------------------------------------------------------------------- #
# 4. Bad template_vars — each case derived from the server's own declaration
# --------------------------------------------------------------------------- #
async def test_missing_required_slot_is_422(
    client: httpx.AsyncClient,
    configured: list[dict[str, Any]],
    schemas: dict[str, dict],
) -> None:
    """A required slot left unset is refused, not rendered as an empty clause."""
    t = _pick(configured, "required-slot")
    omitted = next(s["name"] for s in _slots(t) if s["required"])
    resp = await client.post(
        "/v1/query",
        json={
            "query": QUERY,
            "template": t["id"],
            "template_vars": _fill_required(t, skip=omitted),
        },
        headers=_headers(),
    )
    assert resp.status_code == 422, (
        f"{t['id']!r} accepted a request omitting required slot {omitted!r}: "
        f"{resp.status_code} {resp.text[:300]}"
    )
    _assert_error_envelope(resp, schemas)


async def test_undeclared_slot_is_422(
    client: httpx.AsyncClient,
    configured: list[dict[str, Any]],
    schemas: dict[str, dict],
) -> None:
    """A slot the template does not declare is a 422, not a silent ignore.

    ADR-0008 decision 3.3: a typo'd name that renders an empty clause produces a
    subtly wrong prompt and no error — the worst available outcome — so this is
    the rule that makes the other slot rules worth having.
    """
    t = _pick(configured, "any")
    assert UNDECLARED_SLOT not in {s["name"] for s in _slots(t)}
    resp = await client.post(
        "/v1/query",
        json={
            "query": QUERY,
            "template": t["id"],
            "template_vars": {**_fill_required(t), UNDECLARED_SLOT: "anything"},
        },
        headers=_headers(),
    )
    assert resp.status_code == 422, (
        f"{t['id']!r} accepted undeclared slot {UNDECLARED_SLOT!r}: "
        f"{resp.status_code} {resp.text[:300]}"
    )
    _assert_error_envelope(resp, schemas)


async def test_value_over_declared_max_len_is_422(
    client: httpx.AsyncClient,
    configured: list[dict[str, Any]],
    schemas: dict[str, dict],
) -> None:
    """The cap is the slot's OWN declared ``max_len``, read off the listing —
    one character past it, whatever it happens to be on this deployment."""
    t = _pick(configured, "slot")
    slot = _slots(t)[0]
    over = "x" * (int(slot["max_len"]) + 1)
    resp = await client.post(
        "/v1/query",
        json={
            "query": QUERY,
            "template": t["id"],
            "template_vars": {**_fill_required(t), slot["name"]: over},
        },
        headers=_headers(),
    )
    assert resp.status_code == 422, (
        f"{t['id']!r} accepted {len(over)} characters for slot {slot['name']!r}, "
        f"whose declared max_len is {slot['max_len']}: {resp.status_code} "
        f"{resp.text[:300]}"
    )
    _assert_error_envelope(resp, schemas)


# --------------------------------------------------------------------------- #
# 5. The happy path
# --------------------------------------------------------------------------- #
async def test_templated_query_echoes_the_templates_identity(
    client: httpx.AsyncClient,
    configured: list[dict[str, Any]],
    schemas: dict[str, dict],
) -> None:
    """A well-formed templated request is 200 and names the condition it ran under.

    ``(id, version, hash, model)`` is what makes a generated answer a replayable
    experimental condition (ADR-0008 decision 4 and #122): the version and hash
    must be the ones the LISTING reported for this id — read from the server's
    own answer, not hardcoded — and ``model`` must be non-empty, because a
    template deliberately does not pin a model and the echo is the only record
    of which one actually ran (decision 5).

    ``answer`` is NOT asserted beyond its type. A model's text is not a contract;
    a suite that pinned it would go red on a model swap that broke nothing.
    """
    t = _pick(configured, "any")
    resp = await client.post(
        "/v1/query",
        json={
            "query": QUERY,
            "top_k": 3,
            "template": t["id"],
            "template_vars": _fill_required(t),
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, "query_response", schemas)
    assert body["template"] == t["id"]
    assert body["template_version"] == t["version"], (
        "the echoed version must be the one the listing declared for this id; "
        f"listing said {t['version']}, response said {body.get('template_version')}"
    )
    assert body["template_hash"] == t["hash"], (
        "the echoed hash must be the one the listing declared for this id — that "
        "identity is what makes two tenants' drifted copies detectable rather "
        f"than silently mis-attributed; listing {t['hash']!r}, response "
        f"{body.get('template_hash')!r}"
    )
    # A templated request asked for generation, so the model that generated is a
    # fact the response must carry. (A deployment with templates configured and
    # no LLM wired is outside what ADR-0008 describes; it would fail here, which
    # is the correct reading of a generation feature that cannot generate.)
    assert isinstance(body.get("model"), str) and body["model"], (
        "a templated response must echo the resolved model id (ADR-0008 decision "
        f"5); got {body.get('model')!r}"
    )
    assert isinstance(body["answer"], str)


# --------------------------------------------------------------------------- #
# 6. Ordering: the template is resolved before retrieval
# --------------------------------------------------------------------------- #
async def test_unknown_template_is_404_even_with_expensive_retrieval(
    client: httpx.AsyncClient,
    templates_body: dict[str, Any],
    schemas: dict[str, dict],
) -> None:
    """An unknown id plus the most expensive retrieval the contract allows.

    **Only the status code is asserted, on purpose.** What we want to show is
    that the template is resolved BEFORE any retrieval leg runs, so a bad request
    costs nothing — but over HTTP the only observable that would distinguish the
    two orderings is latency, and a wall-clock assertion on a shared host with
    cold caches is a flake generator, not a proof. The unit suite asserts the
    ordering directly, with a tripwire on the retriever
    (``python/tests/api/test_query_prompt_templates.py``); here we assert the
    weaker, honest claim: an expensive-looking request with an unknown template
    is still a 404, and never a 200, a 503 from a store, or a timeout.
    """
    assert UNKNOWN_ID not in {t["id"] for t in templates_body["templates"]}
    resp = await client.post(
        "/v1/query",
        json={
            "query": QUERY,
            "template": UNKNOWN_ID,
            # The contract's ceiling (query_request.json: top_k maximum 100) plus
            # the neighbour-expansion ceiling. Deliberately AT the cap, not past
            # it: 101 would be a 422 from request validation and would conflate
            # "the schema rejected it" with "the template was resolved first".
            "top_k": 100,
            "context_window": 3,
            "use_graph": True,
        },
        headers=_headers(),
    )
    assert resp.status_code == 404, (
        "an unknown template with an expensive retrieval must still be a 404 — a "
        f"5xx here suggests retrieval ran first: {resp.status_code} {resp.text[:300]}"
    )
    _assert_error_envelope(resp, schemas)
