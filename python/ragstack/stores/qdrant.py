"""Qdrant-backed VectorStore adapter."""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from ragstack.models import Chunk, ScoredChunk
from ragstack.stores.errors import VectorDimMismatch

__all__ = ["QdrantVectorStore", "VectorDimMismatch", "collection_name"]

_PAYLOAD_RESERVED = {"chunk_id", "doc_id", "content", "start_char", "end_char"}


def collection_name(base: str, model: str | None, dim: int) -> str:
    """Derive a collection name scoped to ``(model, dim)``.

    Testing different embedding models is a primary goal, and models of
    different dimensions are physically incompatible in one collection. Scoping
    the name keeps experiments isolated and makes a dimension change route to a
    fresh collection instead of corrupting an existing one. A short hash of the
    full model name disambiguates names that slugify to the same string.
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", model or "default").strip("_").lower()[:40]
    digest = hashlib.sha1((model or "").encode()).hexdigest()[:8]
    return f"{base}_{slug}_{dim}_{digest}"


def _existing_vector_size(info: Any) -> int | None:
    """Best-effort extraction of an existing collection's vector size."""
    # Defensive: the dimension check is best-effort, so an unexpected or partial
    # config shape must yield None (skip the check), never raise — an
    # AttributeError here would turn an optional reconciliation into a hard
    # startup failure.
    vectors = getattr(getattr(getattr(info, "config", None), "params", None), "vectors", None)
    if vectors is None:
        return None
    size = getattr(vectors, "size", None)
    if size is not None:
        return int(size)
    # Named-vectors config is a {name: VectorParams} mapping.
    if isinstance(vectors, dict) and len(vectors) == 1:
        only = next(iter(vectors.values()))
        only_size = getattr(only, "size", None)
        return int(only_size) if only_size is not None else None
    return None


class QdrantVectorStore:
    """VectorStore protocol implementation backed by Qdrant.

    Point IDs are UUID5-hashes of the chunk ID, so re-ingesting the same
    chunk overwrites in place. The original chunk ID is preserved in the
    payload as ``chunk_id`` and re-emitted in search results.
    """

    def __init__(
        self,
        url: str = "http://localhost:6333",
        collection: str = "ragstack",
        vector_size: int = 768,
        distance: Distance = Distance.COSINE,
        api_key: str | None = None,
    ) -> None:
        self._client = AsyncQdrantClient(url=url, api_key=api_key or None)
        self._collection = collection
        self._vector_size = vector_size
        self._distance = distance

    async def ensure_collection(self) -> None:
        """Create the collection if absent; if present, verify its vector size
        matches the configured embedding dimension. Safe to call repeatedly.

        Raises ``VectorDimMismatch`` when an existing collection's size differs —
        writing mismatched vectors would silently corrupt the index, so this is
        a fatal startup error rather than a warning.
        """
        collections = await self._client.get_collections()
        if any(c.name == self._collection for c in collections.collections):
            info = await self._client.get_collection(self._collection)
            existing = _existing_vector_size(info)
            if existing is not None and existing != self._vector_size:
                raise VectorDimMismatch(
                    f"collection {self._collection!r} has vector size {existing}, "
                    f"but the configured embedding dimension is {self._vector_size}. "
                    f"Use a different collection or embedding model."
                )
            return
        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config=VectorParams(
                size=self._vector_size, distance=self._distance
            ),
        )

    async def upsert(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        points: list[PointStruct] = []
        for c in chunks:
            if c.embedding is None:
                raise ValueError(f"chunk {c.id!r} has no embedding")
            payload: dict[str, Any] = {
                "chunk_id": c.id,
                "doc_id": c.doc_id,
                "content": c.content,
                "start_char": c.start_char,
                "end_char": c.end_char,
                **{k: v for k, v in c.metadata.items() if k not in _PAYLOAD_RESERVED},
            }
            points.append(
                PointStruct(id=_point_id(c.id), vector=c.embedding, payload=payload)
            )
        await self._client.upsert(collection_name=self._collection, points=points)

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[ScoredChunk]:
        q_filter: Filter | None = None
        if filters:
            q_filter = Filter(
                must=[
                    FieldCondition(key=k, match=MatchValue(value=v))
                    for k, v in filters.items()
                ]
            )
        # qdrant-client >= 1.10 deprecated `search()` in favour of `query_points()`.
        response = await self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=top_k,
            query_filter=q_filter,
            with_payload=True,
        )
        scored: list[ScoredChunk] = []
        for r in response.points:
            payload = dict(r.payload or {})
            chunk = Chunk(
                id=str(payload.pop("chunk_id", r.id)),
                doc_id=str(payload.pop("doc_id", "")),
                content=str(payload.pop("content", "")),
                start_char=int(payload.pop("start_char", 0) or 0),
                end_char=int(payload.pop("end_char", 0) or 0),
                metadata=payload,
            )
            scored.append(
                ScoredChunk(chunk=chunk, score=r.score, retrieval_method="vector")
            )
        return scored

    async def delete(self, doc_id: str) -> None:
        await self._client.delete(
            collection_name=self._collection,
            points_selector=Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
            ),
        )


def _point_id(chunk_id: str) -> str:
    """Map an arbitrary chunk ID to a deterministic UUID Qdrant will accept."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))
