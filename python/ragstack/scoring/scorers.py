"""Scoring and reranking of retrieved chunks."""
from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from ragstack.models import Chunk, ScoredChunk

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder


class RRFScorer:
    """
    Reciprocal Rank Fusion (RRF) — combine multiple ranked lists into one.

    Given several lists of ScoredChunks (e.g. from vector search and BM25),
    fuse them into a single ranked list without requiring score normalisation.
    """

    def __init__(self, k: int = 60) -> None:
        self.k = k

    async def score(
        self, query: str, candidates: list[Chunk]  # noqa: ARG002
    ) -> list[ScoredChunk]:
        # Trivial case: assign uniform score when no ranking information available.
        return [ScoredChunk(chunk=c, score=1.0 / (self.k + i + 1)) for i, c in enumerate(candidates)]

    def fuse(self, ranked_lists: list[list[ScoredChunk]]) -> list[ScoredChunk]:
        """Fuse multiple ranked lists using RRF."""
        scores: dict[str, float] = {}
        chunks: dict[str, Chunk] = {}
        for ranked in ranked_lists:
            for rank, scored in enumerate(ranked):
                cid = scored.chunk.id
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (self.k + rank + 1)
                chunks[cid] = scored.chunk
        fused = [
            ScoredChunk(chunk=chunks[cid], score=score, retrieval_method="hybrid")
            for cid, score in sorted(scores.items(), key=lambda x: x[1], reverse=True)
        ]
        return fused


class CrossEncoderScorer:
    """
    Reranker using a cross-encoder model (HuggingFace sentence-transformers).

    Requires `sentence-transformers` to be installed.
    Falls back gracefully if the library is not available.
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        self.model_name = model_name
        self._model: CrossEncoder | None = None

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import]

            self._model = CrossEncoder(self.model_name)
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is required for CrossEncoderScorer. "
                "Install it with: pip install sentence-transformers"
            ) from exc

    async def score(self, query: str, candidates: list[Chunk]) -> list[ScoredChunk]:
        self._load_model()
        assert self._model is not None  # _load_model guarantees this or raises
        pairs = [(query, c.content) for c in candidates]
        raw_scores: list[float] = self._model.predict(pairs).tolist()
        scored = [
            ScoredChunk(chunk=c, score=float(s), retrieval_method="reranked")
            for c, s in zip(candidates, raw_scores, strict=True)
        ]
        return sorted(scored, key=lambda x: x.score, reverse=True)


class SidecarReranker:
    """Cross-encoder reranker backed by the crossencoder sidecar (POST ``/rerank``).

    Mirrors :class:`~ragstack.embedders.SidecarEmbedder`: it keeps the heavy
    model out of the API process and behind an HTTP boundary that can scale or
    swap models independently. Implements the ``Scorer`` protocol so it drops in
    wherever ``CrossEncoderScorer`` would, without pulling sentence-transformers
    into the API environment.

    The sidecar ranks and truncates to its ``top_k``; we pass ``top_k =
    len(candidates)`` so the full pool comes back rescored and the caller decides
    the final cut. Returns ``ScoredChunk``s in the sidecar's ranked order.
    """

    def __init__(self, base_url: str, http: httpx.AsyncClient) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http

    async def score(self, query: str, candidates: list[Chunk]) -> list[ScoredChunk]:
        if not candidates:
            return []
        r = await self.http.post(
            f"{self.base_url}/rerank",
            json={
                "query": query,
                "documents": [c.content for c in candidates],
                "top_k": len(candidates),
            },
            timeout=120.0,
        )
        r.raise_for_status()
        data = r.json()
        # The sidecar returns parallel `scores`/`indices` arrays already sorted
        # by descending score; `indices` point back into the documents we sent.
        return [
            ScoredChunk(
                chunk=candidates[i], score=float(s), retrieval_method="reranked"
            )
            for s, i in zip(data["scores"], data["indices"], strict=True)
        ]
