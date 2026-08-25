"""Scoring and reranking of retrieved chunks."""
from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from ragstack.models import Chunk, ScoredChunk
from ragstack.sidecar_http import DEFAULT_TIMEOUT, SidecarClient

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
        """Fuse multiple ranked lists using RRF.

        Identity is ``(collection, chunk id)``: a chunk that several lists rank
        is one candidate whose reciprocal ranks add up — unless the lists come
        from different collections (``ScoredChunk.collection``, stamped by the
        multi-collection fan-out of issue #253), in which case the same chunk
        id from two collections is two candidates, each keeping its own stamp.
        With no stamps (every ``collection`` ``None`` — the single-collection
        path, and the vector/BM25/graph legs inside one retriever) this is
        exactly the chunk-id keyed fusion it always was.
        """
        scores: dict[tuple[str | None, str], float] = {}
        chunks: dict[tuple[str | None, str], Chunk] = {}
        for ranked in ranked_lists:
            for rank, scored in enumerate(ranked):
                key = (scored.collection, scored.chunk.id)
                scores[key] = scores.get(key, 0.0) + 1.0 / (self.k + rank + 1)
                chunks[key] = scored.chunk
        fused = [
            ScoredChunk(
                chunk=chunks[key], score=score, retrieval_method="hybrid",
                collection=key[0],
            )
            for key, score in sorted(scores.items(), key=lambda x: x[1], reverse=True)
        ]
        return fused


class CrossEncoderScorer:
    """
    Reranker using a cross-encoder model (HuggingFace sentence-transformers).

    Requires `sentence-transformers` to be installed.
    Falls back gracefully if the library is not available.
    """

    def __init__(self, model_name: str | None = None) -> None:
        # Default to config.reranker_model so the reranker model has ONE Python
        # source of truth (config.py) rather than a re-literal here.
        if model_name is None:
            from ragstack.config import Settings

            model_name = Settings().reranker_model
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

    def __init__(
        self,
        base_url: str,
        http: httpx.AsyncClient,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._client = SidecarClient(base_url, http, timeout=timeout)

    @property
    def base_url(self) -> str:
        return self._client.base_url

    @property
    def http(self) -> httpx.AsyncClient:
        return self._client.http

    async def score(
        self, query: str, candidates: list[Chunk], top_k: int | None = None
    ) -> list[ScoredChunk]:
        if not candidates:
            return []
        data = await self._client.post_json(
            "rerank",
            {
                "query": query,
                "documents": [c.content for c in candidates],
                "top_k": len(candidates) if top_k is None else min(top_k, len(candidates)),
            },
        )
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
