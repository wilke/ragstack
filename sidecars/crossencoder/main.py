"""Cross-encoder sidecar service for reranking documents."""

import os
from typing import Optional

from fastapi import FastAPI
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
PORT = int(os.environ.get("PORT", "50052"))
DEVICE = os.environ.get("DEVICE", "cpu")

app = FastAPI(title="Cross-Encoder Reranking Service")

_model = None


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


class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_k: Optional[int] = 5


class RerankResponse(BaseModel):
    scores: list[float]
    indices: list[int]


class HealthResponse(BaseModel):
    status: str
    model: str


@app.post("/rerank", response_model=RerankResponse)
async def rerank(request: RerankRequest):
    if not request.documents:
        return RerankResponse(scores=[], indices=[])

    model = _get_model()
    pairs = [[request.query, doc] for doc in request.documents]
    # predict() is a blocking (CPU/GPU-bound) call; run it off the event loop so
    # a single uvicorn worker keeps accepting concurrent requests instead of
    # stalling and dropping connections (httpx ReadError) under load.
    scores = (await run_in_threadpool(model.predict, pairs)).tolist()

    scored_indices = sorted(
        enumerate(scores), key=lambda x: x[1], reverse=True
    )

    top_k = min(request.top_k, len(scores))
    scored_indices = scored_indices[:top_k]

    return RerankResponse(
        scores=[s for _, s in scored_indices],
        indices=[i for i, _ in scored_indices],
    )


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", model=MODEL_NAME)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
