"""FAISS sidecar service for legacy vector index search."""

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DATA_DIR = os.environ.get("DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "50051"))

app = FastAPI(title="FAISS Index Search Service")

_indices: dict = {}


def _load_index(name: str):
    """Lazy-load a FAISS index by name from DATA_DIR."""
    if name in _indices:
        return _indices[name]

    try:
        import faiss
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="faiss is not installed. Install faiss-cpu to use this service.",
        )

    import numpy as np

    index_path = Path(DATA_DIR) / f"{name}.faiss"
    if not index_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Index '{name}' not found at {index_path}",
        )

    index = faiss.read_index(str(index_path))
    _indices[name] = index
    return index


def _list_available_indices() -> list[str]:
    """List all .faiss files in DATA_DIR."""
    data_path = Path(DATA_DIR)
    if not data_path.exists():
        return []
    return [p.stem for p in data_path.glob("*.faiss")]


class SearchRequest(BaseModel):
    query_vector: list[float]
    top_k: Optional[int] = 5
    index: str


class SearchResult(BaseModel):
    id: int
    score: float


class SearchResponse(BaseModel):
    results: list[SearchResult]


class HealthResponse(BaseModel):
    status: str
    indices: list[str]


class IndicesResponse(BaseModel):
    indices: list[str]


@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    try:
        import numpy as np
    except ImportError:
        raise HTTPException(status_code=503, detail="numpy is not installed.")

    index = _load_index(request.index)
    query = np.array([request.query_vector], dtype=np.float32)

    top_k = min(request.top_k, index.ntotal) if index.ntotal > 0 else 0
    if top_k == 0:
        return SearchResponse(results=[])

    distances, ids = index.search(query, top_k)

    results = []
    for i in range(top_k):
        if ids[0][i] == -1:
            break
        results.append(SearchResult(id=int(ids[0][i]), score=float(distances[0][i])))

    return SearchResponse(results=results)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", indices=list(_indices.keys()))


@app.get("/indices", response_model=IndicesResponse)
async def indices():
    return IndicesResponse(indices=_list_available_indices())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
