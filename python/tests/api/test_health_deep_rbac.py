"""/v1/health/deep — admin-only deep dependency probe.

The endpoint's ``detail`` strings can carry backend hostnames/versions/errors, so
authorization is the guardrail: no key → 401, non-admin → 403 (with a body that
carries no backend detail), admin → 200 with per-dependency checks.
"""
import re

import httpx
import pytest

from ragstack.api import security
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN, ROLE_RESEARCHER, ROLE_USER
from ragstack.config import settings
from ragstack.observability.dependencies import dependency_health

pytestmark = pytest.mark.asyncio


class _MutationTrapVectorStore:
    """A vector store whose liveness probe is read-only: healthcheck() is fine,
    but ensure_collection() (which would provision infra) is a trap."""

    def __init__(self) -> None:
        self.healthchecked = False

    async def healthcheck(self) -> None:
        self.healthchecked = True

    async def ensure_collection(self) -> None:  # pragma: no cover - must not run
        raise AssertionError("health probe must not call ensure_collection (mutates infra)")

# Backend identifiers that must never appear in a non-admin (403) response body.
_BACKEND_LEAK_RE = re.compile(
    r"(qdrant|elasticsearch|neo4j|postgres|sqlite|localhost|:\d{4}|latency_ms|checks)", re.I
)


def _configure(monkeypatch, roles: dict[str, str]) -> None:
    monkeypatch.setattr(security.settings, "api_keys", list(roles))
    monkeypatch.setattr(security.settings, "api_key_roles", dict(roles))
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


async def test_no_key_is_401(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    assert (await client.get("/v1/health/deep")).status_code == 401


# `user` is the only non-admin role (ADR-0003); the deprecated `researcher`
# alias must land on the same 403, not on a different surface.
@pytest.mark.parametrize("role", [ROLE_USER, ROLE_RESEARCHER])
async def test_non_admin_is_403_and_leaks_no_backend_detail(client, monkeypatch, role):
    _configure(monkeypatch, {"k": role})
    resp = await client.get("/v1/health/deep", headers={"X-API-Key": "k"})
    assert resp.status_code == 403
    # The refusal body must not carry any backend detail (hostnames/ports/checks).
    assert not _BACKEND_LEAK_RE.search(resp.text), resp.text


async def test_admin_gets_checks(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    resp = await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "degraded"}
    names = {c["name"] for c in body["checks"]}
    assert {"vector", "text", "graph", "jobstore"} <= names
    # In-memory doubles are all live → ok.
    assert body["status"] == "ok"
    assert all(c["ok"] for c in body["checks"])


async def test_probe_is_read_only_uses_healthcheck_not_ensure(client, monkeypatch):
    """The vector probe must call the read-only healthcheck(), never
    ensure_collection() (which would create the collection — provisioning infra
    from a health check)."""
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    trap = _MutationTrapVectorStore()
    monkeypatch.setattr(app.state, "vector_store", trap)

    resp = await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})
    assert resp.status_code == 200
    assert trap.healthchecked is True  # read-only probe ran
    vector = next(c for c in resp.json()["checks"] if c["name"] == "vector")
    assert vector["ok"] is True  # ensure_collection was NOT called (would have raised)


# --------------------------------------------------------------------------- #
# #751 PR-3: the routed-around dependencies — reranker, embedder, llm,
# ingest_engine, doi_resolver — with `state`, a cache, and ONE verdict shared
# with the request path.
# --------------------------------------------------------------------------- #

NEW_CHECKS = {"reranker", "embedder", "llm", "ingest_engine", "doi_resolver"}


def _mock_http(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class _SidecarReranker:
    """Looks like SidecarReranker to the probe: has a base_url, never scores."""

    base_url = "http://reranker.test:50052"

    async def score(self, query, candidates, top_k=None):
        raise RuntimeError("not used")


def _check(body, name):
    return next(c for c in body["checks"] if c["name"] == name)


async def test_every_check_carries_a_state_and_the_new_names_are_present(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    body = (await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})).json()
    names = {c["name"] for c in body["checks"]}
    assert {"vector", "text", "graph", "jobstore"} | NEW_CHECKS <= names
    assert all(c["state"] in {"ok", "degraded", "down", "disabled"} for c in body["checks"])
    assert all(c["ok"] == (c["state"] in {"ok", "disabled"}) for c in body["checks"])
    # The fixture wires no reranker, no LLM, no GoWe, no DOI enricher: those
    # are `disabled` — a fact about settings, not a failure — so status is ok.
    assert _check(body, "reranker")["state"] == "disabled"
    assert _check(body, "llm")["state"] == "disabled"
    assert _check(body, "ingest_engine")["state"] == "disabled"
    assert _check(body, "doi_resolver")["state"] == "disabled"
    # The in-memory embedder has no endpoint: live by construction, said so.
    assert _check(body, "embedder")["state"] == "ok"
    assert body["status"] == "ok"


async def test_reranker_down_is_a_degraded_status_with_the_sidecar_answer(client, monkeypatch):
    """The #748 shape: the sidecar's /ready answers 503 fail (#760) → check
    `reranker` ok:false state:down, top status degraded. And it is CACHED:
    a second call inside the window does not hit the sidecar again."""
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(
            503, json={"status": "fail", "model": "bge", "loaded": True,
                       "error": "RuntimeError", "detail": "cuDNN workspace"},
        )

    monkeypatch.setattr(app.state, "reranker", _SidecarReranker())
    async with _mock_http(handler) as http:
        monkeypatch.setattr(app.state, "http_client", http)
        body = (await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})).json()
        rr = _check(body, "reranker")
        assert rr["ok"] is False and rr["state"] == "down"
        assert "RuntimeError: cuDNN workspace" in rr["detail"]
        assert rr["latency_ms"] is not None
        assert body["status"] == "degraded"
        assert hits == ["http://reranker.test:50052/ready"]

        body = (await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})).json()
        assert _check(body, "reranker")["state"] == "down"
        assert len(hits) == 1, "a poll inside the cache window must not become load"


async def test_reranker_loading_is_degraded_and_ready_is_ok(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    answers = [httpx.Response(503, json={"status": "loading", "model": "bge", "loaded": False}),
               httpx.Response(200, json={"status": "ok", "model": "bge", "loaded": True})]

    def handler(request: httpx.Request) -> httpx.Response:
        return answers.pop(0)

    monkeypatch.setattr(app.state, "reranker", _SidecarReranker())
    async with _mock_http(handler) as http:
        monkeypatch.setattr(app.state, "http_client", http)
        body = (await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})).json()
        assert _check(body, "reranker")["state"] == "degraded"
        assert body["status"] == "degraded"
        dependency_health.reset()  # expire the cache
        body = (await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})).json()
        assert _check(body, "reranker")["state"] == "ok"
        assert _check(body, "reranker")["detail"] == "http://reranker.test:50052/ready: bge"
        assert body["status"] == "ok"


async def test_reranker_transport_error_is_down_and_bounded(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN})

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr(app.state, "reranker", _SidecarReranker())
    async with _mock_http(handler) as http:
        monkeypatch.setattr(app.state, "http_client", http)
        body = (await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})).json()
        rr = _check(body, "reranker")
        assert rr["state"] == "down" and rr["detail"] == "ConnectError: All connection attempts failed"


async def test_deep_health_reads_the_verdict_the_request_path_wrote(client, monkeypatch):
    """Principle 4 (one fact, projected everywhere): a /v1/retrieve whose
    rerank just failed leaves `reranker: down [request path]` in the cache,
    and deep health serves THAT — it does not run its own probe (the mock
    transport would have answered 200 and contradicted the request)."""
    from tests.api.test_reranking import _BoomReranker, _StubRetriever

    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(200, json={"status": "ok", "model": "bge"})

    class _BoomSidecar(_BoomReranker):
        base_url = "http://reranker.test:50052"

    monkeypatch.setattr(app.state, "retriever", _StubRetriever(4))
    monkeypatch.setattr(app.state, "reranker", _BoomSidecar())
    async with _mock_http(handler) as http:
        monkeypatch.setattr(app.state, "http_client", http)
        resp = await client.post(
            "/v1/retrieve", json={"query": "q", "top_k": 2}, headers={"X-API-Key": "adm"}
        )
        assert resp.json()["degraded"][0]["component"] == "rerank"

        body = (await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})).json()
        rr = _check(body, "reranker")
        assert rr["state"] == "down"
        assert rr["detail"] == "RuntimeError: reranker down [request path]"
        assert body["status"] == "degraded"
        assert hits == [], "a fresh request-path verdict must be served, not re-probed"


async def test_llm_and_embedder_probe_v1_models_and_ready(client, monkeypatch):
    """llm → GET <endpoint>/v1/models (bearer when a key is set); an
    OpenAI-style embedder → /v1/models too; the sidecar embedder → /ready."""
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(200, json={"object": "list", "data": []})

    class _LLM:
        base_url = "http://llm.test:9001"
        api_key = "k"

    class _Gen:
        llm = _LLM()

    class _Emb:
        base_url = "http://embed.test:50053"
        api_key = None

    class _Batching:
        base = _Emb()

    monkeypatch.setattr(app.state, "generator", _Gen())
    monkeypatch.setattr(app.state, "embedder", _Batching())
    async with _mock_http(handler) as http:
        monkeypatch.setattr(app.state, "http_client", http)
        body = (await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})).json()
        assert _check(body, "llm")["state"] == "ok"
        assert _check(body, "embedder")["state"] == "ok"
        assert sorted(hits) == [
            ("http://embed.test:50053/ready", None),
            ("http://llm.test:9001/v1/models", "Bearer k"),
        ]
        dependency_health.reset()
        hits.clear()
        monkeypatch.setattr(settings, "embedding_api", "openai")
        await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})
        assert ("http://embed.test:50053/v1/models", None) in hits


async def test_ingest_engine_probes_gowe_health_only_on_the_gowe_backend(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(settings, "ingest_backend", "gowe")
    monkeypatch.setattr(settings, "gowe_url", "http://gowe.test:8091")
    async with _mock_http(handler) as http:
        monkeypatch.setattr(app.state, "http_client", http)
        body = (await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})).json()
        assert _check(body, "ingest_engine")["state"] == "ok"
        assert hits == ["http://gowe.test:8091/api/v1/health"]


async def test_doi_resolver_reports_the_breaker_without_a_network_call(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN})

    class _Resolver:
        breaker_open = False
        consecutive_transport_failures = 0

    class _Enricher:
        resolver = _Resolver()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"doi_resolver must not make a request: {request.url}")

    monkeypatch.setattr(app.state, "doi_enricher", _Enricher(), raising=False)
    async with _mock_http(handler) as http:
        monkeypatch.setattr(app.state, "http_client", http)
        body = (await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})).json()
        assert _check(body, "doi_resolver")["state"] == "ok"
        assert "not probed" in _check(body, "doi_resolver")["detail"]
        _Resolver.breaker_open = True
        _Resolver.consecutive_transport_failures = 5
        body = (await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})).json()
        d = _check(body, "doi_resolver")
        assert d["state"] == "degraded" and d["ok"] is False and "breaker open after 5" in d["detail"]
        assert body["status"] == "degraded"


async def test_graph_disabled_is_disabled_not_probed(client, monkeypatch):
    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    monkeypatch.setattr(app.state, "graph_store", None)
    body = (await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})).json()
    g = _check(body, "graph")
    assert g["state"] == "disabled" and g["ok"] is True
    assert body["status"] == "ok"


async def test_admin_body_validates_against_the_contract(client, monkeypatch):
    import json
    from pathlib import Path

    import jsonschema

    _configure(monkeypatch, {"adm": ROLE_ADMIN})
    body = (await client.get("/v1/health/deep", headers={"X-API-Key": "adm"})).json()
    schema = json.loads(
        (Path(__file__).resolve().parents[3] / "contracts/schemas/deep_health_response.json").read_text()
    )
    jsonschema.validate(body, schema)
