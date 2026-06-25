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
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from ragstack.models import Chunk, ScoredChunk
from ragstack.stores.errors import VectorDimMismatch
from ragstack.tenancy import DEFAULT_TENANT, tenant_of

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
            tenant = tenant_of(c)
            payload: dict[str, Any] = {
                "chunk_id": c.id,
                "doc_id": c.doc_id,
                "content": c.content,
                "start_char": c.start_char,
                "end_char": c.end_char,
                **{k: v for k, v in c.metadata.items() if k not in _PAYLOAD_RESERVED},
            }
            points.append(
                PointStruct(
                    # Scope the point id by tenant so two tenants ingesting the same
                    # source (same chunk_id) don't overwrite each other's points.
                    id=_point_id(c.id, tenant),
                    vector=c.embedding,
                    payload=payload,
                )
            )
        await self._client.upsert(collection_name=self._collection, points=points)

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[ScoredChunk]:
        q_filter = _build_filter(filters)
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

    async def delete(self, doc_id: str, tenant_id: str | None = None) -> None:
        # Tenant-scoped: a caller can only delete its own documents, even if it
        # knows another tenant's doc_id. tenant_id=None deletes across tenants.
        selector: dict[str, Any] = {"doc_id": doc_id}
        if tenant_id is not None:
            selector["tenant_id"] = tenant_id
        await self._client.delete(
            collection_name=self._collection,
            points_selector=_build_filter(selector),
        )


def _point_id(chunk_id: str, tenant_id: str = DEFAULT_TENANT) -> str:
    """Deterministic UUID point id, scoped by tenant so the same chunk under two
    tenants maps to two distinct points."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{tenant_id}:{chunk_id}"))


def _build_filter(filters: dict[str, Any] | None) -> Filter | None:
    """Build a Qdrant filter from a flat dict. A list value matches *any* of its
    entries (MatchAny) — used for tenant reads (own + public); a scalar is an
    exact match. Keep the empty-list handling in sync with ``_matches`` in
    stores/memory.py."""
    if not filters:
        return None
    conditions = []
    for key, value in filters.items():
        if isinstance(value, (list, tuple, set)):
            if not value:
                continue  # empty multi-value filter = no constraint on this key
            conditions.append(FieldCondition(key=key, match=MatchAny(any=list(value))))
        else:
            conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
    if not conditions:
        return None
    return Filter(must=conditions)
