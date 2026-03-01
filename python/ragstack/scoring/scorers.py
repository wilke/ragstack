"""Scoring and reranking of retrieved chunks."""
from __future__ import annotations

import math

from ragstack.models import Chunk, ScoredChunk


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
        self._model: object | None = None

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
        pairs = [(query, c.content) for c in candidates]
        raw_scores: list[float] = self._model.predict(pairs).tolist()  # type: ignore[attr-defined]
        scored = [
            ScoredChunk(chunk=c, score=float(s), retrieval_method="reranked")
            for c, s in zip(candidates, raw_scores)
        ]
        return sorted(scored, key=lambda x: x.score, reverse=True)
