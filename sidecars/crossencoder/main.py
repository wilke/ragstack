"""Cross-encoder sidecar service for reranking documents."""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

# Default matches python config.reranker_model and apptainer/sidecars-up.sh so
# every launch path loads the same model unless MODEL_NAME is set explicitly.
MODEL_NAME = os.environ.get("MODEL_NAME", "BAAI/bge-reranker-v2-m3")
# bge-reranker-v2-m3 handles up to 8k tokens; default to the SFR window / chunk
# hard cap (4096) so the reranker sees whole chunks and doesn't truncate the
# large-chunk configs — a fair cross-config benchmark. Lower it (e.g. 512) to
# trade recall on long chunks for query-serving latency at prod scale.
MAX_LENGTH = int(os.environ.get("MAX_LENGTH", "4096"))
# Per-forward-pass batch cap so a deep candidate pool of long (up to MAX_LENGTH-
# token) chunks can't OOM the shared GPU — predict() iterates in batches of this
# size rather than padding the whole pool into one tensor.
BATCH_SIZE = int(os.environ.get("RERANK_BATCH_SIZE", "32"))
PORT = int(os.environ.get("PORT", "50052"))
DEVICE = os.environ.get("DEVICE", "cpu")
# Readiness probe (#751 PR-1, #748): /ready runs one real two-pair predict on the
# real device, bounded by HEALTH_TIMEOUT_S, and the verdict is cached for
# HEALTH_CACHE_S so pollers (restore.sh, the API, fleet status, Prometheus)
# never become load.
HEALTH_TIMEOUT_S = float(os.environ.get("HEALTH_TIMEOUT_S", "5"))
HEALTH_CACHE_S = float(os.environ.get("HEALTH_CACHE_S", "10"))
_PROBE_PAIRS = [["probe query", "probe document a"], ["probe query", "probe document b"]]

_model = None
# (monotonic ts, http status, body) of the last completed probe.
_health_cache: tuple[float, int, dict] | None = None
# Concurrent /ready callers share one in-flight probe.
_health_lock = asyncio.Lock()
# The probe's worker-thread future. A probe that timed out keeps running in its
# thread (a thread cannot be cancelled); while it is still running the next
# check reports the timeout again instead of stacking another call onto a hung
# device.
_probe_future: asyncio.Future | None = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(MODEL_NAME, max_length=MAX_LENGTH, device=DEVICE)
        # Half precision on GPU ~halves memory and speeds inference with no
        # measurable rerank-quality loss; skipped on CPU (fp16 is slow there).
        if DEVICE.startswith("cuda"):
            _model.model.half()
    return _model


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the model at startup so the (large) download + GPU load + fp16 cast
    # happen off the request path: the first /rerank no longer blocks the event
    # loop, and a GPU/model failure surfaces at boot (fail-fast) rather than
    # mid-request. Loaded in a worker thread so startup stays cooperative.
    await run_in_threadpool(_get_model)
    yield


app = FastAPI(title="Cross-Encoder Reranking Service", lifespan=lifespan)


class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_k: int | None = 5


class RerankResponse(BaseModel):
    scores: list[float]
    indices: list[int]


@app.post("/rerank", response_model=RerankResponse)
async def rerank(request: RerankRequest):
    if not request.documents:
        return RerankResponse(scores=[], indices=[])

    model = _get_model()
    pairs = [[request.query, doc] for doc in request.documents]
    # predict() is a blocking (CPU/GPU-bound) call; run it off the event loop so
    # a single uvicorn worker keeps accepting concurrent requests instead of
    # stalling and dropping connections (httpx ReadError) under load. batch_size
    # bounds peak GPU memory regardless of pool depth.
    scores = (
        await run_in_threadpool(model.predict, pairs, batch_size=BATCH_SIZE)
    ).tolist()

    scored_indices = sorted(
        enumerate(scores), key=lambda x: x[1], reverse=True
    )

    top_k = min(request.top_k, len(scores))
    scored_indices = scored_indices[:top_k]

    return RerankResponse(
        scores=[s for _, s in scored_indices],
        indices=[i for i, _ in scored_indices],
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return (text.splitlines()[0] if text else "")[:300]


def _model_device(model) -> str:
    dev = getattr(model, "device", None)
    if dev is None:  # older sentence-transformers keep it here
        dev = getattr(model, "_target_device", None)
    return str(dev) if dev is not None else DEVICE


def _run_probe(model) -> None:
    model.predict(_PROBE_PAIRS, batch_size=2)


async def _check() -> tuple[int, dict]:
    global _probe_future
    model = _model
    if model is None:
        return 503, {"status": "loading", "model": MODEL_NAME, "loaded": False,
                     "checked_at": _now_iso()}
    if _probe_future is not None and not _probe_future.done():
        return 503, {"status": "fail", "model": MODEL_NAME, "loaded": True,
                     "error": "TimeoutError",
                     "detail": "previous probe still running past "
                               f"{HEALTH_TIMEOUT_S:g}s",
                     "checked_at": _now_iso()}
    start = time.perf_counter()
    _probe_future = asyncio.ensure_future(run_in_threadpool(_run_probe, model))
    # A timed-out probe may fail later with nobody awaiting it; consume that.
    _probe_future.add_done_callback(lambda f: f.cancelled() or f.exception())
    try:
        await asyncio.wait_for(asyncio.shield(_probe_future), timeout=HEALTH_TIMEOUT_S)
    except TimeoutError:
        return 503, {"status": "fail", "model": MODEL_NAME, "loaded": True,
                     "error": "TimeoutError",
                     "detail": f"probe exceeded {HEALTH_TIMEOUT_S:g}s",
                     "checked_at": _now_iso()}
    except Exception as exc:  # report the class, never a traceback
        return 503, {"status": "fail", "model": MODEL_NAME, "loaded": True,
                     "error": type(exc).__name__, "detail": _first_line(exc),
                     "checked_at": _now_iso()}
    return 200, {"status": "ok", "model": MODEL_NAME, "device": _model_device(model),
                 "loaded": True,
                 "probe_ms": round((time.perf_counter() - start) * 1000, 1),
                 "checked_at": _now_iso()}


async def _readiness() -> tuple[int, dict]:
    global _health_cache
    cached = _health_cache
    if cached is not None and time.monotonic() - cached[0] < HEALTH_CACHE_S:
        return cached[1], cached[2]
    async with _health_lock:
        cached = _health_cache  # another caller may have just refreshed it
        if cached is not None and time.monotonic() - cached[0] < HEALTH_CACHE_S:
            return cached[1], cached[2]
        code, body = await _check()
        if body["status"] != "loading":  # "loading" is not a verdict worth holding
            _health_cache = (time.monotonic(), code, body)
        return code, body


@app.get("/live")
async def live():
    """Liveness: the process serves HTTP. Says nothing about the model."""
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """Readiness: a real rerank of two pairs on the configured device passed."""
    code, body = await _readiness()
    return JSONResponse(status_code=code, content=body)


# /health is an alias of /ready so every existing poller (restore.sh, the API's
# startup model check, /v1/models/status) gets the stricter answer (#751).
app.add_api_route("/health", ready, methods=["GET"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
