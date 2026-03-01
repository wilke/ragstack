"""Embedding sidecar service for generating text embeddings."""

import os

from fastapi import FastAPI
from pydantic import BaseModel

MODEL_NAME = os.environ.get("MODEL_NAME", "BAAI/bge-base-en-v1.5")
PORT = int(os.environ.get("PORT", "50053"))
DEVICE = os.environ.get("DEVICE", "cpu")

app = FastAPI(title="Embedding Service")

_model = None
DIMENSIONS = None


def _get_model():
    global _model, DIMENSIONS
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_NAME, device=DEVICE)
        DIMENSIONS = _model.get_sentence_embedding_dimension()
    return _model


class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    dimensions: int


class HealthResponse(BaseModel):
    status: str
    model: str
    dimensions: int | None


@app.post("/embed", response_model=EmbedResponse)
async def embed(request: EmbedRequest):
    if not request.texts:
        model = _get_model()
        return EmbedResponse(embeddings=[], dimensions=DIMENSIONS)

    model = _get_model()
    embeddings = model.encode(request.texts).tolist()
    return EmbedResponse(embeddings=embeddings, dimensions=DIMENSIONS)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", model=MODEL_NAME, dimensions=DIMENSIONS)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
