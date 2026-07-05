"""Model status + throughput probes for the Ops dashboard (role: ``admin``).

Included under the ``require_role(ROLE_ADMIN)`` group in ``api/main.py``, so —
like ``/health/deep`` — the whole surface is admin-only *by construction*. That
gate is load-bearing: the responses carry internal endpoint URLs (GPU hostnames,
tunnel ports) and backend error text, which must never reach a non-admin.

Two endpoints, split by cost so the dashboard can poll one cheaply and trigger
the other explicitly:

* ``GET  /stats/models``            — cheap liveness: one bounded GET per model
  endpoint (``/health`` for sidecars/vLLM, ``/v1/models`` for the LLM). Safe to
  auto-refresh; never touches the GPU compute path.
* ``POST /stats/models/benchmark``  — on demand: a small, bounded real workload
  (embed N sentences, generate M tokens) timed for a rough throughput estimate.
  Not something to run on a poll — it consumes the same fleet the app serves.
"""
from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ragstack.config import settings

log = logging.getLogger(__name__)

router = APIRouter()

# Fixed sample workload for the throughput benchmark — kept small and constant so
# runs are comparable and can't be turned into a load generator from the client.
_SAMPLE_TEXTS = [
    "Retrieval-augmented generation grounds a language model in retrieved passages.",
    "Dense vectors capture semantic similarity; BM25 captures lexical overlap.",
    "Reciprocal rank fusion blends ranked lists without tuning score scales.",
    "A cross-encoder reranks the fused candidates for final precision.",
]
_LLM_PROMPT = "In two sentences, explain what retrieval-augmented generation is."


def _est_tokens(s: str) -> int:
    """~4 chars/token heuristic. Good enough for a throughput *estimate*; we don't
    have per-request usage from the OpenAI-compatible embed/complete calls."""
    return max(1, len(s) // 4)


# --- status ---------------------------------------------------------------


class EndpointStatus(BaseModel):
    url: str
    reachable: bool
    latency_ms: float | None = None
    detail: str | None = None


class ModelStatus(BaseModel):
    role: str  # "embedding" | "llm" | "reranker"
    model: str
    backend: str | None = None
    dim: int | None = None
    endpoints: list[EndpointStatus]
    reachable: bool  # any endpoint up
    note: str | None = None  # "not configured" | "disabled" | None


class ModelsStatusResponse(BaseModel):
    models: list[ModelStatus]


async def _probe(http: httpx.AsyncClient, base: str, path: str) -> EndpointStatus:
    """Cheap liveness GET. Reachable = the server answered without a transport
    error and didn't 5xx. A 404 still counts as reachable (the host is up)."""
    url = base.rstrip("/") + path
    start = perf_counter()
    try:
        r = await http.get(url, timeout=4.0)
        latency = round((perf_counter() - start) * 1000, 1)
        if r.status_code >= 500:
            return EndpointStatus(url=base, reachable=False, latency_ms=latency,
                                  detail=f"HTTP {r.status_code}")
        detail = None if r.status_code < 400 else f"HTTP {r.status_code}"
        return EndpointStatus(url=base, reachable=True, latency_ms=latency, detail=detail)
    except Exception as e:  # transport error — host/tunnel down
        latency = round((perf_counter() - start) * 1000, 1)
        return EndpointStatus(url=base, reachable=False, latency_ms=latency,
                              detail=f"{type(e).__name__}: {e}")


def _embedding_urls() -> list[str]:
    # Mirror _build_embedder: fan-out endpoints override the single sidecar URL.
    return settings.embedding_endpoints or [settings.embedding_sidecar_url]


@router.get("/stats/models", response_model=ModelsStatusResponse)
async def stats_models(request: Request) -> ModelsStatusResponse:
    """Per-model endpoint liveness + latency (admin only). Cheap; safe to poll."""
    http: httpx.AsyncClient = request.app.state.http_client
    out: list[ModelStatus] = []

    # Embedding — one probe per fan-out endpoint, in parallel.
    emb_urls = _embedding_urls()
    emb_probes = await asyncio.gather(
        *(_probe(http, u, settings.embedding_health_path) for u in emb_urls)
    )
    out.append(ModelStatus(
        role="embedding",
        model=settings.embedding_model,
        backend=settings.embedding_api,
        dim=settings.embedding_model_dim,
        endpoints=list(emb_probes),
        reachable=any(e.reachable for e in emb_probes),
    ))

    # LLM — /v1/models works for both vLLM and OpenAI-compatible servers.
    if settings.llm_endpoint:
        llm_ep = await _probe(http, settings.llm_endpoint, "/v1/models")
        out.append(ModelStatus(
            role="llm", model=settings.llm_model, backend="openai",
            endpoints=[llm_ep], reachable=llm_ep.reachable,
        ))
    else:
        out.append(ModelStatus(
            role="llm", model=settings.llm_model, endpoints=[],
            reachable=False, note="not configured",
        ))

    # Reranker — optional final stage.
    if settings.rerank_enabled:
        rr = await _probe(http, settings.crossencoder_sidecar_url, "/health")
        out.append(ModelStatus(
            role="reranker", model=settings.reranker_model, backend="sidecar",
            endpoints=[rr], reachable=rr.reachable,
        ))
    else:
        out.append(ModelStatus(
            role="reranker", model=settings.reranker_model, endpoints=[],
            reachable=False, note="disabled",
        ))

    return ModelsStatusResponse(models=out)


# --- throughput benchmark -------------------------------------------------


class BenchmarkRequest(BaseModel):
    # Bounded so this can't be coaxed into a load test.
    embed_batch: int = Field(default=32, ge=1, le=128)
    llm_tokens: int = Field(default=128, ge=16, le=512)


class BenchResult(BaseModel):
    model: str
    ok: bool
    seconds: float | None = None
    items: int | None = None
    items_per_sec: float | None = None
    tokens_per_sec: float | None = None
    detail: str | None = None


class BenchmarkResponse(BaseModel):
    embedding: BenchResult
    llm: BenchResult


async def _bench_embedding(embedder: Any, batch: int) -> BenchResult:
    texts = [_SAMPLE_TEXTS[i % len(_SAMPLE_TEXTS)] for i in range(batch)]
    tok = sum(_est_tokens(t) for t in texts)
    start = perf_counter()
    try:
        await embedder.embed(texts)
    except Exception as e:
        return BenchResult(model=settings.embedding_model, ok=False,
                           detail=f"{type(e).__name__}: {e}")
    secs = perf_counter() - start
    return BenchResult(
        model=settings.embedding_model, ok=True, seconds=round(secs, 3),
        items=batch, items_per_sec=round(batch / secs, 1) if secs > 0 else None,
        tokens_per_sec=round(tok / secs, 1) if secs > 0 else None,
    )


async def _bench_llm(generator: Any, max_tokens: int) -> BenchResult:
    if generator is None:
        return BenchResult(model=settings.llm_model, ok=False, detail="not configured")
    start = perf_counter()
    try:
        answer = await generator.llm.complete_text(_LLM_PROMPT, max_tokens=max_tokens)
    except Exception as e:
        return BenchResult(model=settings.llm_model, ok=False,
                           detail=f"{type(e).__name__}: {e}")
    secs = perf_counter() - start
    out_tok = _est_tokens(answer)
    return BenchResult(
        model=settings.llm_model, ok=True, seconds=round(secs, 3),
        items=out_tok, tokens_per_sec=round(out_tok / secs, 1) if secs > 0 else None,
    )


@router.post("/stats/models/benchmark", response_model=BenchmarkResponse)
async def benchmark_models(request: Request, body: BenchmarkRequest | None = None) -> BenchmarkResponse:
    """Run a small, bounded real workload against the live embedder + LLM and
    time it for a rough throughput estimate (admin only). Consumes the serving
    fleet — call on demand, not on a poll."""
    body = body or BenchmarkRequest()
    embedder = request.app.state.embedder
    generator = request.app.state.generator
    emb, llm = await asyncio.gather(
        _bench_embedding(embedder, body.embed_batch),
        _bench_llm(generator, body.llm_tokens),
    )
    return BenchmarkResponse(embedding=emb, llm=llm)
