"""Embedding sidecar service for generating text embeddings."""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

MODEL_NAME = os.environ.get("MODEL_NAME", "BAAI/bge-base-en-v1.5")
PORT = int(os.environ.get("PORT", "50053"))
DEVICE = os.environ.get("DEVICE", "cpu")
# Readiness probe (#751 PR-1, #748): /ready runs one real encode on the real
# device, bounded by HEALTH_TIMEOUT_S, and the verdict is cached for
# HEALTH_CACHE_S so pollers never become load.
HEALTH_TIMEOUT_S = float(os.environ.get("HEALTH_TIMEOUT_S", "5"))
HEALTH_CACHE_S = float(os.environ.get("HEALTH_CACHE_S", "10"))
_PROBE_TEXTS = ["probe"]

_model = None
DIMENSIONS = None
# (monotonic ts, http status, body) of the last completed probe.
_health_cache: tuple[float, int, dict] | None = None
# Concurrent /ready callers share one in-flight probe.
_health_lock = asyncio.Lock()
# The probe's worker-thread future; see crossencoder/main.py for why a still
# running (timed-out) probe is reported instead of being started again.
_probe_future: asyncio.Future | None = None


def _get_model():
    global _model, DIMENSIONS
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_NAME, device=DEVICE)
        DIMENSIONS = _model.get_sentence_embedding_dimension()
    return _model


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the model at startup (same as the crossencoder sidecar) so /ready is
    # honest from boot and a model/device failure surfaces at start, not on the
    # first /embed. Loaded in a worker thread so startup stays cooperative.
    await run_in_threadpool(_get_model)
    yield


app = FastAPI(title="Embedding Service", lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    dimensions: int


@app.post("/embed", response_model=EmbedResponse)
async def embed(request: EmbedRequest):
    if not request.texts:
        model = _get_model()
        return EmbedResponse(embeddings=[], dimensions=DIMENSIONS)

    model = _get_model()
    embeddings = model.encode(request.texts).tolist()
    return EmbedResponse(embeddings=embeddings, dimensions=DIMENSIONS)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return (text.splitlines()[0] if text else "")[:300]


def _model_device(model) -> str:
    dev = getattr(model, "device", None)
    return str(dev) if dev is not None else DEVICE


def _run_probe(model) -> None:
    model.encode(_PROBE_TEXTS)


async def _check() -> tuple[int, dict]:
    global _probe_future
    model = _model
    base = {"model": MODEL_NAME, "dimensions": DIMENSIONS}
    if model is None:
        return 503, {"status": "loading", **base, "loaded": False, "checked_at": _now_iso()}
    if _probe_future is not None and not _probe_future.done():
        return 503, {"status": "fail", **base, "loaded": True, "error": "TimeoutError",
                     "detail": f"previous probe still running past {HEALTH_TIMEOUT_S:g}s",
                     "checked_at": _now_iso()}
    start = time.perf_counter()
    _probe_future = asyncio.ensure_future(run_in_threadpool(_run_probe, model))
    # A timed-out probe may fail later with nobody awaiting it; consume that.
    _probe_future.add_done_callback(lambda f: f.cancelled() or f.exception())
    try:
        await asyncio.wait_for(asyncio.shield(_probe_future), timeout=HEALTH_TIMEOUT_S)
    except TimeoutError:
        return 503, {"status": "fail", **base, "loaded": True, "error": "TimeoutError",
                     "detail": f"probe exceeded {HEALTH_TIMEOUT_S:g}s",
                     "checked_at": _now_iso()}
    except Exception as exc:  # report the class, never a traceback
        return 503, {"status": "fail", **base, "loaded": True,
                     "error": type(exc).__name__, "detail": _first_line(exc),
                     "checked_at": _now_iso()}
    return 200, {"status": "ok", **base, "device": _model_device(model), "loaded": True,
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
    """Readiness: a real one-text encode on the configured device passed."""
    code, body = await _readiness()
    return JSONResponse(status_code=code, content=body)


# /health is an alias of /ready so every existing poller (restore.sh, the API's
# embed pool health checks, /v1/models/status) gets the stricter answer (#751).
app.add_api_route("/health", ready, methods=["GET"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
