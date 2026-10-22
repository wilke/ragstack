"""Deep dependency health probe (role: ``admin``).

Included under the ``require_role(ROLE_ADMIN)`` group in ``api/main.py``, so the
whole surface is admin-only *by construction* — a wiring slip can't expose it.
That gate is load-bearing: each check's ``detail`` carries backend
identity/error text (hostnames, driver versions, exception messages), which must
never reach a non-admin. There is deliberately no non-admin "shallow-but-detailed"
variant; ``GET /health`` stays the coarse public liveness probe.

Two kinds of check (#751):

* the **stores** (``vector``, ``text``, ``graph``, ``jobstore``) — a cheap
  read-only liveness op, as before. A store the request cannot route around.
* the **routed-around dependencies** (``reranker``, ``embedder``, ``llm``,
  ``ingest_engine``, ``doi_resolver``) — the ones whose failure the request
  path survives by falling back (#748: the reranker answered 500 for two hours
  behind a green ``/health``). Each is read from
  :data:`~ragstack.observability.dependencies.dependency_health`, the
  per-process verdict cache the request path ALSO writes: while real reranks
  flow, this endpoint reports what the last one saw and runs no probe at all;
  when nothing has been learned for ~10 s it runs one bounded (3 s) probe —
  the sidecar's ``/ready`` (#760), ``/v1/models`` on an OpenAI-style server,
  GoWe's ``/api/v1/health`` — and caches that. One fact, projected everywhere;
  a poller is never load.

Every check carries ``state`` (``ok``/``degraded``/``down``/``disabled``) beside
the compatibility ``ok`` bool; ``status`` is ``degraded`` if any check is
neither ok nor disabled. A dependency this server does not configure is
``disabled`` and does not degrade the whole: that is a fact about settings,
not a failure. Nothing here provisions anything.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ragstack.api.deps import (
    get_graph_store,
    get_http_client,
    get_job_store,
    get_text_index,
    get_vector_store,
)
from ragstack.config import settings
from ragstack.observability.dependencies import (
    STATE_DEGRADED,
    STATE_DISABLED,
    STATE_DOWN,
    STATE_OK,
    DependencyHealth,
    Verdict,
    dependency_health,
)

router = APIRouter()

#: Hard bound on one dependency probe (plan principle 2: API deep 3 s/check).
PROBE_TIMEOUT_S = 3.0


class DeepCheck(BaseModel):
    name: str
    ok: bool
    state: str | None = None  # ok | degraded | down | disabled
    detail: str | None = None
    latency_ms: float | None = None


class DeepHealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    checks: list[DeepCheck]


async def _probe(name: str, backend: str, op: Callable[[], Awaitable[Any]]) -> DeepCheck:
    """Run a store liveness ``op`` and time it. Admin-only endpoint, so
    ``detail`` may carry backend identity/error text."""
    start = perf_counter()
    try:
        await op()
        ok, detail = True, backend
    except Exception as e:  # surface the backend error (admin-only)
        ok, detail = False, f"{backend}: {type(e).__name__}: {e}"
    latency_ms = round((perf_counter() - start) * 1000, 2)
    return DeepCheck(
        name=name, ok=ok, state=STATE_OK if ok else STATE_DOWN, detail=detail,
        latency_ms=latency_ms,
    )


# --------------------------------------------------------------------------- #
# Routed-around dependencies — verdicts, not probes, where a verdict exists
# --------------------------------------------------------------------------- #


def _disabled(name: str, why: str) -> DeepCheck:
    """A dependency this server does not configure. Never cached: it is
    re-derived from settings/state on every call, so a hot-swap shows up."""
    return DeepCheck(name=name, ok=True, state=STATE_DISABLED, detail=why)


def _from_verdict(v: Verdict) -> DeepCheck:
    detail = v.detail
    if v.source == "request" and detail:
        # Say WHICH side learned it: "the last real rerank failed" is a
        # different fact from "the probe failed".
        detail = f"{detail} [request path]"
    return DeepCheck(name=v.name, ok=v.ok, state=v.state, detail=detail, latency_ms=v.latency_ms)


def _ready_verdict(url: str, resp: httpx.Response) -> tuple[str, str]:
    """Map a sidecar ``/ready`` answer (#760: 200 ok / 503 ``fail``|``loading``)
    or an OpenAI-style ``/v1/models`` answer onto a state."""
    code = resp.status_code
    try:
        body = resp.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if 200 <= code < 300:
        model = body.get("model")
        return STATE_OK, f"{url}: {model}" if model else f"{url}: HTTP {code}"
    status = body.get("status")
    if code == 503 and status == "loading":
        return STATE_DEGRADED, f"{url}: loading (model not ready yet)"
    if code == 503 and status == "fail":
        what = ": ".join(str(body[k]) for k in ("error", "detail") if body.get(k))
        return STATE_DOWN, f"{url}: fail: {what or 'no detail'}"
    return STATE_DOWN, f"{url}: HTTP {code}"


def _http_check(
    http: httpx.AsyncClient, url: str, *, api_key: str | None = None
) -> Callable[[], Awaitable[tuple[str, str]]]:
    """A bounded GET of ``url`` as a dependency-health probe op. Transport
    errors raise and become ``down`` with the exception class."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None

    async def op() -> tuple[str, str]:
        resp = await http.get(url, headers=headers, timeout=PROBE_TIMEOUT_S)
        return _ready_verdict(url, resp)

    return op


async def _verdict_check(
    health: DependencyHealth, name: str, op: Callable[[], Awaitable[tuple[str, str]]]
) -> DeepCheck:
    return _from_verdict(await health.probe(name, op, timeout=PROBE_TIMEOUT_S))


def _in_process(name: str, what: str) -> DeepCheck:
    """A dependency that is wired but has no endpoint to probe (an in-process
    or test double). Live by construction; said so rather than guessed."""
    return DeepCheck(name=name, ok=True, state=STATE_OK, detail=f"{what}: in-process, nothing to probe")


async def _reranker(app_state: Any, http: httpx.AsyncClient, health: DependencyHealth) -> DeepCheck:
    """The cross-encoder sidecar. The verdict is the one the request path
    writes on every real rerank (``routers/query.py::_maybe_rerank``); only
    when that is stale does this GET ``<sidecar>/ready``."""
    reranker = getattr(app_state, "reranker", None)
    if reranker is None:
        return _disabled("reranker", "RERANK_ENABLED=false or no reranker wired")
    base = getattr(reranker, "base_url", None)
    if not base:
        return _in_process("reranker", type(reranker).__name__)
    return await _verdict_check(health, "reranker", _http_check(http, f"{base.rstrip('/')}/ready"))


def _embedder_target(embedder: Any) -> tuple[str | None, str | None]:
    """``(base_url, api_key)`` of the embedding endpoint to probe: the single
    client's, or the FIRST of a pool's (the pool keeps its own per-endpoint
    health; this probe answers "is the embedding fleet reachable at all")."""
    inner = getattr(embedder, "base", embedder)  # BatchingEmbedder wraps `base`
    load = getattr(inner, "endpoint_load", None)
    if callable(load):  # PooledEmbedder
        rows = load()
        return (rows[0][0] if rows else None), settings.openai_api_key or None
    base = getattr(inner, "base_url", None)
    return (base or None), getattr(inner, "api_key", None)


async def _embedder(app_state: Any, http: httpx.AsyncClient, health: DependencyHealth) -> DeepCheck:
    """The embedding sidecar's ``/ready`` (#760), or ``/v1/models`` on an
    OpenAI-style embedding server (vLLM) — the cheapest call that proves the
    server is up and knows its model."""
    embedder = getattr(app_state, "embedder", None)
    if embedder is None:
        return _disabled("embedder", "no embedder wired")
    base, api_key = _embedder_target(embedder)
    if not base:
        return _in_process("embedder", type(embedder).__name__)
    path = "/v1/models" if (settings.embedding_api or "sidecar").lower() == "openai" else "/ready"
    return await _verdict_check(
        health, "embedder", _http_check(http, f"{base.rstrip('/')}{path}", api_key=api_key)
    )


async def _llm(app_state: Any, http: httpx.AsyncClient, health: DependencyHealth) -> DeepCheck:
    """``GET <llm_endpoint>/v1/models`` — vLLM and OpenAI-compatible servers
    answer it without touching the compute path."""
    generator = getattr(app_state, "generator", None)
    if generator is None:
        return _disabled("llm", "LLM_ENDPOINT unset: /v1/query answers with the retrieval-only placeholder")
    llm = getattr(generator, "llm", None)
    base = getattr(llm, "base_url", None)
    if not base:
        return _in_process("llm", type(generator).__name__)
    return await _verdict_check(
        health, "llm",
        _http_check(http, f"{base.rstrip('/')}/v1/models", api_key=getattr(llm, "api_key", None)),
    )


async def _ingest_engine(http: httpx.AsyncClient, health: DependencyHealth) -> DeepCheck:
    """GoWe's public ``GET /api/v1/health`` when ``INGEST_BACKEND=gowe``; a
    local backend has no engine to ask."""
    if (settings.ingest_backend or "local").lower() != "gowe":
        return _disabled("ingest_engine", f"INGEST_BACKEND={settings.ingest_backend or 'local'}: no engine")
    url = f"{settings.gowe_url.rstrip('/')}/api/v1/health"
    return await _verdict_check(health, "ingest_engine", _http_check(http, url))


def _doi_resolver(app_state: Any) -> DeepCheck:
    """The DOI metadata resolver (Crossref / NCBI ID Converter / DataCite).

    Deliberately NOT a network probe: the resolver talks to public, rate-limited
    APIs, and a health poller on seven tenants would add a steady trickle of
    requests to them for no information a real ingest would not produce. The
    live signal is the resolver's own circuit breaker (``BREAKER_THRESHOLD``
    consecutive transport failures trip it for the rest of the run), which is
    the enricher's actual experience of the network — reported here as
    ``degraded`` when open, ``ok`` with "not probed" said plainly otherwise.
    """
    enricher = getattr(app_state, "doi_enricher", None)
    if enricher is None:
        return _disabled("doi_resolver", "DOI_ENRICHMENT_ENABLED=false or no enricher wired")
    resolver = getattr(enricher, "resolver", None)
    if resolver is None or not hasattr(resolver, "breaker_open"):
        return _in_process("doi_resolver", type(enricher).__name__)
    if resolver.breaker_open:
        return DeepCheck(
            name="doi_resolver", ok=False, state=STATE_DEGRADED,
            detail=(
                f"breaker open after {resolver.consecutive_transport_failures} consecutive "
                "transport failures; enrichment is off for the rest of this process"
            ),
        )
    return DeepCheck(
        name="doi_resolver", ok=True, state=STATE_OK,
        detail="crossref/idconv configured; breaker closed (not probed: public API)",
    )


@router.get("/health/deep", response_model=DeepHealthResponse)
async def health_deep(
    request: Request,
    vector_store: Any = Depends(get_vector_store),
    text_index: Any = Depends(get_text_index),
    graph_store: Any = Depends(get_graph_store),
    job_store: Any = Depends(get_job_store),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> DeepHealthResponse:
    """Per-dependency liveness with latency + backend detail (admin only)."""

    async def _vector() -> None:
        # Read-only connectivity check. NOT ensure_collection(), which would
        # *create* the collection — a probe must never provision infra. The
        # in-memory store has no healthcheck → trivially live.
        if hasattr(vector_store, "healthcheck"):
            await vector_store.healthcheck()

    async def _text() -> None:
        # Read-only — NOT ensure_index() (which would create the index).
        if hasattr(text_index, "healthcheck"):
            await text_index.healthcheck()

    async def _graph() -> None:
        # A bounded read confirms connectivity. None = graph disabled is
        # handled below as `disabled`, not probed.
        await graph_store.list_entities(tenant_id=None, limit=1)

    async def _jobs() -> None:
        # A miss round-trips to the store (real query for sqlite/postgres) without
        # mutating anything.
        await job_store.get("__healthcheck__")

    checks = [
        await _probe("vector", settings.vector_backend, _vector),
        await _probe("text", settings.text_backend, _text),
        (
            _disabled("graph", f"GRAPH_BACKEND={settings.graph_backend}")
            if graph_store is None
            else await _probe("graph", settings.graph_backend, _graph)
        ),
        await _probe("jobstore", settings.job_store_backend, _jobs),
    ]
    state = request.app.state
    health = dependency_health
    # The routed-around dependencies, concurrently: each is bounded to
    # PROBE_TIMEOUT_S and usually answered from the cache, so the endpoint's
    # worst case is one bound, not four.
    checks.extend(
        await asyncio.gather(
            _reranker(state, http, health),
            _embedder(state, http, health),
            _llm(state, http, health),
            _ingest_engine(http, health),
        )
    )
    checks.append(_doi_resolver(state))
    status = "ok" if all(c.state in (STATE_OK, STATE_DISABLED) for c in checks) else "degraded"
    return DeepHealthResponse(status=status, checks=checks)
