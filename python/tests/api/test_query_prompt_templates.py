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
from ragstack.llm import RagGenerator
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

        async def complete(self, messages, max_tokens: int = 512, temperature: float = 0.0) -> str:
            self.messages = messages
            return "Pathogen\tProtein A\tProtein B\tReference\nSARS-CoV-2\tSpike\tACE2\t10.1/x"

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
    for key in ("template", "template_version", "template_hash"):
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
async def test_bad_template_vars_are_422(client, templates, vars_, why):
    resp = await client.post(
        "/v1/query",
        json={"query": "spike", "template": "ppi-extraction", "template_vars": vars_},
    )
    assert resp.status_code == 422, f"{why}: {resp.text}"


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
    (entry,) = body["templates"]
    assert entry["id"] == "ppi-extraction"
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
