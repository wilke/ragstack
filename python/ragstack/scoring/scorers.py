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
        self, query: str, candidates: list[Chunk], top_k: int | None = None  # noqa: ARG002
    ) -> list[ScoredChunk]:
        # Trivial case: assign uniform score when no ranking information available.
        scored = [ScoredChunk(chunk=c, score=1.0 / (self.k + i + 1)) for i, c in enumerate(candidates)]
        return scored if top_k is None else scored[:top_k]

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

    async def score(
        self, query: str, candidates: list[Chunk], top_k: int | None = None
    ) -> list[ScoredChunk]:
        self._load_model()
        assert self._model is not None  # _load_model guarantees this or raises
        pairs = [(query, c.content) for c in candidates]
        raw_scores: list[float] = self._model.predict(pairs).tolist()
        scored = [
            ScoredChunk(chunk=c, score=float(s), retrieval_method="reranked")
            for c, s in zip(candidates, raw_scores, strict=True)
        ]
        ranked = sorted(scored, key=lambda x: x.score, reverse=True)
        # Mirror SidecarReranker: honor the caller's cut so implementers of the
        # Scorer protocol are interchangeable (top_k=None → the full ranked pool).
        return ranked if top_k is None else ranked[:top_k]


class SidecarReranker:
    """Cross-encoder reranker backed by the crossencoder sidecar (POST ``/rerank``).

    Mirrors :class:`~ragstack.embedders.SidecarEmbedder`: it keeps the heavy
    model out of the API process and behind an HTTP boundary that can scale or
    swap models independently. Implements the ``Scorer`` protocol so it drops in
    wherever ``CrossEncoderScorer`` would, without pulling sentence-transformers
    into the API environment.

    The sidecar scores the whole pool and truncates to its ``top_k``. ``top_k``
    defaults to the full pool (caller decides the cut); pass a smaller value when
    only the top results are kept to shrink the response payload. Returns
    ``ScoredChunk``s in the sidecar's ranked order.
    """

    def __init__(self, base_url: str, http: httpx.AsyncClient) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http

    async def score(
        self, query: str, candidates: list[Chunk], top_k: int | None = None
    ) -> list[ScoredChunk]:
        if not candidates:
            return []
        r = await self.http.post(
            f"{self.base_url}/rerank",
            json={
                "query": query,
                "documents": [c.content for c in candidates],
                "top_k": len(candidates) if top_k is None else min(top_k, len(candidates)),
            },
            timeout=120.0,
        )
        r.raise_for_status()
        data = r.json()
        # The sidecar returns parallel `scores`/`indices` arrays already sorted
        # by descending score; `indices` point back into the documents we sent.
        scores, indices = data["scores"], data["indices"]
        # Validate the indices are a clean subset of what we sent before using them
        # to index `candidates`: an out-of-range index would raise IndexError and a
        # duplicate would silently duplicate one chunk while dropping another. Raise
        # on any violation so _maybe_rerank degrades to the fused order rather than
        # returning a corrupted result set.
        n = len(candidates)
        if len(scores) != len(indices):
            raise ValueError(f"rerank returned {len(scores)} scores for {len(indices)} indices")
        if any(not isinstance(i, int) or not (0 <= i < n) for i in indices):
            raise ValueError(f"rerank returned an out-of-range index (pool size {n})")
        if len(set(indices)) != len(indices):
            raise ValueError("rerank returned duplicate indices")
        return [
            ScoredChunk(
                chunk=candidates[i], score=float(s), retrieval_method="reranked"
            )
            for s, i in zip(scores, indices, strict=True)
        ]
