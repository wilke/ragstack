"""Server-side prompt templates on ``/v1/query`` (ADR-0008).

The wiring, not the template semantics — ``tests/test_prompts.py`` owns the
loader, the validator and the renderer. What matters here is what the HTTP
surface promises:

* an untemplated request is **byte-identical** to a server without the feature;
* the failures are refused BEFORE retrieval, so a bad request costs nothing;
* the response says what produced it, including the model that actually ran.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from ragstack.api.main import app
from ragstack.config import settings
from ragstack.llm import RagGenerator
from ragstack.models import Chunk
from ragstack.prompts import load_templates

pytestmark = pytest.mark.asyncio

_ROOT = Path(__file__).resolve().parents[3]
_SCHEMAS = _ROOT / "contracts" / "schemas"

_TEMPLATES = [
    {
        "id": "ppi-extraction",
        "version": 3,
        "label": "Protein-Protein Interaction (PPI)",
        "output": "table",
        "columns": ["Pathogen", "Protein A", "Protein B", "Reference"],
        "max_output_tokens": 2500,
        "slots": [
            {"name": "organism", "required": True, "max_len": 120},
            {"name": "genes", "required": False, "max_len": 200},
        ],
        "system": "You extract structured data. Use ONLY the provided passages.",
        "user": (
            'Extract {{label}} for organism "{{organism}}"'
            "{{#genes}} involving: {{genes}}{{/genes}}.\n"
            "Columns:\n{{columns}}\n\n--- CONTEXT ---\n\n{{context}}"
        ),
    },
    {
        "id": "uncapped",
        "version": 1,
        "label": "Uncapped prose",
        "output": "text",
        # Deliberately declares NO max_output_tokens: the server setting is the
        # fallback, and that fallback was untested.
        "slots": [{"name": "organism", "required": True, "max_len": 120}],
        "system": "You answer questions about literature.",
        "user": "About {{organism}}.\n{{context}}",
    },
]


@pytest.fixture
def templates(tmp_path, monkeypatch):
    """Install a loaded template set on app.state, as the lifespan would."""
    path = tmp_path / "templates.json"
    path.write_text(json.dumps(_TEMPLATES))
    loaded = load_templates(path)
    monkeypatch.setattr(app.state, "prompt_templates", loaded, raising=False)
    return loaded


@pytest.fixture
def capturing_llm(monkeypatch):
    """A generator that records the messages it was asked to send."""

    class _LLM:
        model = "test-model-v1"

        def __init__(self) -> None:
            self.messages: list[dict[str, str]] | None = None

        async def complete_detailed(
            self, messages, max_tokens: int = 512, temperature: float = 0.0
        ) -> tuple[str, str]:
            self.messages = messages
            self.max_tokens = max_tokens
            return (
                "Pathogen\tProtein A\tProtein B\tReference\nSARS-CoV-2\tSpike\tACE2\t10.1/x",
                "stop",
            )

        async def complete(self, messages, max_tokens: int = 512, temperature: float = 0.0) -> str:
            text, _ = await self.complete_detailed(messages, max_tokens, temperature)
            return text

    llm = _LLM()
    monkeypatch.setattr(app.state, "generator", RagGenerator(llm), raising=False)
    return llm


async def test_untemplated_response_carries_no_provenance_keys(client, capturing_llm):
    """The compatibility guarantee, asserted on the wire rather than the model.

    The four fields must be ABSENT, not null: a client parsing the old shape
    sees exactly what it saw before, and `null` would be a new key.
    """
    resp = await client.post("/v1/query", json={"query": "spike protein", "top_k": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("template", "template_version", "template_hash", "model"):
        assert key not in body, f"{key} leaked into an untemplated response"


async def test_unknown_template_is_404_and_runs_no_retrieval(client, templates, monkeypatch):
    """A name that does not exist is a fact about the request, knowable up front.

    Guarded by a retriever that fails the test if it is reached: answering this
    after a full retrieval would burn embedding and store work to return an error.
    """
    called = False
    original = app.state.retriever.retrieve

    async def _tripwire(*a, **k):
        nonlocal called
        called = True
        return await original(*a, **k)

    # Patch the RETRIEVER, not the vector store. An earlier version of this test
    # guarded app.state.vector_store.query and passed even when template
    # resolution was deliberately moved AFTER retrieval — the handler reaches the
    # store through the retriever, so the guard was never on the path and the
    # test asserted nothing. Verified by mutation: moving the resolution now
    # fails this test.
    monkeypatch.setattr(app.state.retriever, "retrieve", _tripwire, raising=False)
    resp = await client.post(
        "/v1/query", json={"query": "spike", "template": "no-such-template"}
    )
    assert resp.status_code == 404, resp.text
    assert "no-such-template" in resp.text
    assert not called, "retrieval ran for a request that could not be served"


@pytest.mark.parametrize(
    "vars_, why",
    [
        ({}, "a required slot left unset"),
        ({"organism": "SARS-CoV-2", "nope": "x"}, "an undeclared slot"),
        ({"organism": "x" * 121}, "a value over the declared max_len"),
    ],
)
async def test_bad_template_vars_are_422(client, templates, vars_, why, monkeypatch):
    # Same tripwire as the 404 test. Bad slot values are a fact about the
    # request, so they must cost nothing either — and without this guard the
    # validation could move after retrieval and the suite would not see it.
    called = False
    original = app.state.retriever.retrieve

    async def _tripwire(*a, **k):
        nonlocal called
        called = True
        return await original(*a, **k)

    monkeypatch.setattr(app.state.retriever, "retrieve", _tripwire, raising=False)
    resp = await client.post(
        "/v1/query",
        json={"query": "spike", "template": "ppi-extraction", "template_vars": vars_},
    )
    assert resp.status_code == 422, f"{why}: {resp.text}"
    assert not called, f"{why}: retrieval ran for a request that could not be served"


async def test_templated_request_echoes_what_produced_it(client, templates, capturing_llm):
    resp = await client.post(
        "/v1/query",
        json={
            "query": "spike protein ACE2",
            "top_k": 2,
            "template": "ppi-extraction",
            "template_vars": {"organism": "SARS-CoV-2", "genes": "Spike, ACE2"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["template"] == "ppi-extraction"
    assert body["template_version"] == 3
    assert body["template_hash"] == templates["ppi-extraction"].hash
    # The model that ACTUALLY ran, read off the live client — this is what makes
    # a swapped server default attributable instead of looking like a prompt
    # regression (ADR-0008 decision 5).
    assert body["model"] == "test-model-v1"
    jsonschema.validate(
        body,
        json.loads((_SCHEMAS / "query_response.json").read_text()),
        resolver=jsonschema.RefResolver(base_uri=_SCHEMAS.as_uri() + "/", referrer={}),
    )


async def test_the_template_shapes_generation_and_query_stays_the_retrieval_string(
    client, templates, capturing_llm
):
    """The distinction the schema spends a paragraph on, asserted.

    `query` reaches retrieval; the rendered template reaches the model. The
    instruction text must NOT appear in what was embedded, and the system message
    must be the template's own — not the module default.
    """
    await client.post(
        "/v1/query",
        json={
            "query": "spike protein ACE2",
            "top_k": 2,
            "template": "ppi-extraction",
            "template_vars": {"organism": "SARS-CoV-2"},
        },
    )
    system, user = capturing_llm.messages
    assert system["content"] == _TEMPLATES[0]["system"]
    assert "You are a helpful assistant" not in system["content"]
    assert 'for organism "SARS-CoV-2"' in user["content"]
    # An unset optional slot renders its section away rather than leaving a stub.
    assert "involving:" not in user["content"]
    # The template's columns, tab-joined, reached the model.
    assert "Pathogen\tProtein A" in user["content"]


async def test_a_slot_value_cannot_become_template_syntax(client, templates, capturing_llm):
    """Values are substituted, never re-read as source.

    This proves a value cannot become SYNTAX. It proves nothing about a value
    that reads as an instruction — ADR-0008 is explicit that prompt injection is
    out of scope, and this test is not evidence otherwise.
    """
    await client.post(
        "/v1/query",
        json={
            "query": "spike",
            "top_k": 1,
            "template": "ppi-extraction",
            "template_vars": {"organism": "{{context}}"},
        },
    )
    _, user = capturing_llm.messages
    assert 'for organism "{{context}}"' in user["content"]


async def test_prompt_templates_endpoint_lists_declarations_not_bodies(client, templates):
    resp = await client.get("/v1/prompt-templates")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    jsonschema.validate(
        body, json.loads((_SCHEMAS / "prompt_templates_response.json").read_text())
    )
    entry = next(t for t in body["templates"] if t["id"] == "ppi-extraction")
    assert entry["hash"] == templates["ppi-extraction"].hash
    assert {s["name"] for s in entry["slots"]} == {"organism", "genes"}
    # The prompt text is operator configuration. A caller needs to know which
    # knobs exist, not what the server will say to the model.
    assert "system" not in entry
    assert "user" not in entry


async def test_no_templates_configured_is_an_empty_list_not_404(client, monkeypatch):
    """An unconfigured capability is a normal state, not an error.

    An empty list lets a client hide its template picker without a version check
    — the same idiom the Grading tab uses against GET /v1/grading/batches.
    """
    monkeypatch.setattr(app.state, "prompt_templates", {}, raising=False)
    resp = await client.get("/v1/prompt-templates")
    assert resp.status_code == 200
    assert resp.json() == {"templates": []}


async def test_no_llm_configured_claims_no_template(client, templates, monkeypatch):
    """ADR-0008 §3b case 1, which had no test at all.

    A templated request against a server with no LLM gets the retrieval-only
    fallback. It must not echo provenance: claiming a template produced
    "[LLM not configured] …" attributes text to a prompt that never ran.
    """
    monkeypatch.setattr(app.state, "generator", None, raising=False)
    resp = await client.post(
        "/v1/query",
        json={
            "query": "spike",
            "top_k": 1,
            "template": "ppi-extraction",
            "template_vars": {"organism": "SARS-CoV-2"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "[LLM not configured]" in body["answer"]
    for key in ("template", "template_version", "template_hash", "model", "truncated"):
        assert key not in body, f"{key} claimed a template ran when generation did not"


async def test_generation_failure_claims_no_template(client, templates, monkeypatch):
    """ADR-0008 §3b case 2, likewise untested.

    Retrieval succeeded, so the request is not failed — but the answer is the
    fallback, and the template did not produce it.
    """

    class _Broken:
        model = "test-model-v1"

        async def complete(self, messages, max_tokens: int = 512, temperature: float = 0.0) -> str:
            raise RuntimeError("upstream is down")

    monkeypatch.setattr(app.state, "generator", RagGenerator(_Broken()), raising=False)
    resp = await client.post(
        "/v1/query",
        json={
            "query": "spike",
            "top_k": 1,
            "template": "ppi-extraction",
            "template_vars": {"organism": "SARS-CoV-2"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "[answer generation failed]" in body["answer"]
    for key in ("template", "template_version", "template_hash", "model", "truncated"):
        assert key not in body, f"{key} claimed a template ran when generation failed"


async def test_the_templated_prompt_is_grounded_in_the_retrieved_passages(
    client, templates, capturing_llm
):
    """{{context}} must receive the PASSAGES, not the query.

    llm.py promises the templated path renders the same context text the default
    path builds — same budget, same passage-first fitting. Nothing asserted it,
    so swapping format_context(sources) for request.query left the whole suite
    green while silently un-grounding every templated answer.
    """
    # Seed the default collection directly: the shared client fixture's personas
    # seed other collections, and this test needs a passage it can look for in
    # the rendered prompt.
    chunks = [
        Chunk(
            id=f"ground-{i}",
            doc_id=f"ground-doc-{i}",
            content=f"Distinctive passage {i} about zirconium widget calibration.",
            embedding=[0.1, 0.2, 0.3, 0.4],
            metadata={"tenant_id": "default"},
        )
        for i in range(2)
    ]
    await app.state.vector_store.upsert(chunks)
    await app.state.text_index.index(chunks)

    resp = await client.post(
        "/v1/query",
        json={
            "query": "zirconium widget calibration",
            "top_k": 3,
            "template": "ppi-extraction",
            "template_vars": {"organism": "SARS-CoV-2"},
        },
    )
    assert resp.status_code == 200, resp.text
    sources = resp.json()["sources"]
    assert sources, "this test needs at least one retrieved passage to be meaningful"
    _, user = capturing_llm.messages
    for s in sources:
        snippet = s["content"][:40].strip()
        assert snippet and snippet in user["content"], "a retrieved passage did not reach the prompt"


async def test_a_truncated_answer_is_reported_not_hidden(client, templates, monkeypatch):
    """The defect this closes: a table cut off at the token ceiling lost ROWS with
    nothing on screen to say so, so the row count tracked how verbose the model
    was per row rather than what the corpus contained — which is why raising
    top_k could REDUCE the number of rows returned."""

    class _Truncating:
        model = "test-model-v1"

        async def complete_detailed(self, messages, max_tokens=512, temperature=0.0):
            return "A\tB\nx\ty\nz\tcut-off-mid-wo", "length"

        async def complete(self, messages, max_tokens=512, temperature=0.0):
            text, _ = await self.complete_detailed(messages, max_tokens, temperature)
            return text

    monkeypatch.setattr(app.state, "generator", RagGenerator(_Truncating()), raising=False)
    resp = await client.post(
        "/v1/query",
        json={
            "query": "spike",
            "top_k": 1,
            "template": "ppi-extraction",
            "template_vars": {"organism": "SARS-CoV-2"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["truncated"] is True


async def test_an_untruncated_answer_omits_the_flag(client, templates, capturing_llm):
    """Omitted, not false — the same rule the provenance fields follow, so a
    response only carries what it has something to say about."""
    resp = await client.post(
        "/v1/query",
        json={
            "query": "spike",
            "top_k": 1,
            "template": "ppi-extraction",
            "template_vars": {"organism": "SARS-CoV-2"},
        },
    )
    assert "truncated" not in resp.json()


async def test_the_template_ceiling_reaches_the_model(client, templates, monkeypatch):
    """A template's max_output_tokens must actually be sent, not merely stored."""
    seen: dict[str, int] = {}

    class _Recording:
        model = "test-model-v1"

        async def complete_detailed(self, messages, max_tokens=512, temperature=0.0):
            seen["max_tokens"] = max_tokens
            return "A\tB\nx\ty", "stop"

        async def complete(self, messages, max_tokens=512, temperature=0.0):
            text, _ = await self.complete_detailed(messages, max_tokens, temperature)
            return text

    monkeypatch.setattr(app.state, "generator", RagGenerator(_Recording()), raising=False)
    await client.post(
        "/v1/query",
        json={
            "query": "spike",
            "top_k": 1,
            "template": "ppi-extraction",
            "template_vars": {"organism": "SARS-CoV-2"},
        },
    )
    assert seen["max_tokens"] == templates["ppi-extraction"].max_output_tokens


async def test_the_server_setting_reaches_an_UNTEMPLATED_generation(client, monkeypatch):
    """The whole of the untemplated half of this feature was uncovered.

    llm_max_output_tokens is what an operator raises to stop plain answers being
    cut off. Replacing it with a literal 512 at the call site left the entire
    suite green, which means the feature could be reverted invisibly.
    """
    seen: dict[str, int] = {}

    class _Recording:
        model = "test-model-v1"

        async def complete_detailed(self, messages, max_tokens=512, temperature=0.0):
            seen["max_tokens"] = max_tokens
            return "an answer", "stop"

        async def complete(self, messages, max_tokens=512, temperature=0.0):
            text, _ = await self.complete_detailed(messages, max_tokens, temperature)
            return text

    monkeypatch.setattr(app.state, "generator", RagGenerator(_Recording()), raising=False)
    monkeypatch.setattr(settings, "llm_max_output_tokens", 4321, raising=False)
    await client.post("/v1/query", json={"query": "spike", "top_k": 1})
    assert seen["max_tokens"] == 4321


async def test_the_server_setting_is_the_fallback_on_the_TEMPLATED_path(
    client, templates, monkeypatch
):
    """A template that declares no ceiling must fall back to the setting, not to
    a literal. Mutating `template.max_output_tokens or settings...` to
    `... or 512` also left the suite green."""
    seen: dict[str, int] = {}

    class _Recording:
        model = "test-model-v1"

        async def complete_detailed(self, messages, max_tokens=512, temperature=0.0):
            seen["max_tokens"] = max_tokens
            return "A\tB\nx\ty", "stop"

        async def complete(self, messages, max_tokens=512, temperature=0.0):
            text, _ = await self.complete_detailed(messages, max_tokens, temperature)
            return text

    monkeypatch.setattr(app.state, "generator", RagGenerator(_Recording()), raising=False)
    monkeypatch.setattr(settings, "llm_max_output_tokens", 4321, raising=False)
    # `uncapped` declares no max_output_tokens, so the setting must apply.
    await client.post(
        "/v1/query",
        json={
            "query": "spike",
            "top_k": 1,
            "template": "uncapped",
            "template_vars": {"organism": "SARS-CoV-2"},
        },
    )
    assert seen["max_tokens"] == 4321


async def test_a_listed_template_carries_its_declared_ceiling(client, templates):
    """to_wire omitted this field entirely and nothing noticed, because a MISSING
    OPTIONAL field still validates against the schema — the exact hole the fix's
    own comment describes, shipped without a test closing it."""
    resp = await client.get("/v1/prompt-templates")
    by_id = {t["id"]: t for t in resp.json()["templates"]}
    assert by_id["ppi-extraction"]["max_output_tokens"] == 2500
    # Absent, not null, for a template that declares none.
    assert "max_output_tokens" not in by_id["uncapped"]
