"""Qdrant-backed VectorStore adapter."""
from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Condition,
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointIdsList,
    PointStruct,
    VectorParams,
)

from ragstack.models import Chunk, ScoredChunk
from ragstack.stores.errors import VectorDimMismatch
from ragstack.tenancy import DEFAULT_TENANT, tenant_of

__all__ = [
    "QdrantVectorStore",
    "VectorDimMismatch",
    "collection_name",
    "CollectionHealth",
]

_PAYLOAD_RESERVED = {"chunk_id", "doc_id", "content", "start_char", "end_char"}


@dataclass(frozen=True)
class CollectionHealth:
    """A point-in-time read of a Qdrant collection's optimizer state, for
    backpressure (#141). ``status`` is Qdrant's collection status
    (``green`` = idle/indexed, ``yellow`` = optimizing, ``grey`` = pending,
    ``red`` = error); ``optimizer_ok`` is False when the optimizer reports an
    error; ``segments_count`` is the current segment count (a coarse progress
    signal). See :class:`ragstack.stores.backpressure.BackpressuredVectorStore`."""

    status: str
    optimizer_ok: bool
    segments_count: int


def _slug(s: str, n: int = 40) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s or "").strip("_").lower()[:n]


def collection_name(
    base: str, model: str | None, dim: int, *, chunk: str | None = None
) -> str:
    """Derive a collection name scoped to the corpus's build spec.

    Models of different dimensions are physically incompatible in one collection,
    so the name is scoped to ``(model, dim)`` and a short hash disambiguates
    models that slugify to the same string.

    ``chunk`` is a *canonical chunk descriptor* (e.g. ``"fixed_token/512/64"``).
    When given, the collection is **content-addressed over the full build spec**:
    the name gains a chunk slug and the hash covers ``model|dim|chunk`` — so the
    SAME spec always maps to the SAME collection (idempotent) and DIFFERENT
    chunkers on the same model get DIFFERENT collections instead of silently
    overwriting each other. ``chunk=None`` keeps the legacy ``(model, dim)``-only
    name byte-for-byte unchanged (back-compat for callers that don't opt in).
    """
    slug = _slug(model or "default")
    if chunk is None:
        digest = hashlib.sha1((model or "").encode()).hexdigest()[:8]
        return f"{base}_{slug}_{dim}_{digest}"
    digest = hashlib.sha1(f"{model or ''}|{dim}|{chunk}".encode()).hexdigest()[:8]
    return f"{base}_{slug}_{dim}_{_slug(chunk, 24)}_{digest}"


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
        timeout: int | None = None,
        upsert_batch_size: int = 256,
        upsert_concurrency: int = 1,
    ) -> None:
        # `timeout` (seconds) bounds each request; raise it for heavy ops (large
        # filtered deletes) so they fail fast/explicitly instead of hanging.
        self._client = AsyncQdrantClient(url=url, api_key=api_key or None, timeout=timeout)
        self._collection = collection
        self._vector_size = vector_size
        self._distance = distance
        # Upserts are chunked so a single request never carries the whole shard:
        # one all-at-once upsert of a large shard (e.g. 6000×4096-d ≈ 98 MB) makes
        # the Qdrant client raise ResponseHandlingException (see the #144 A/B
        # benchmark). ``upsert_concurrency`` > 1 pipelines the batches (bounded) to
        # recover throughput on a healthy collection; the default 1 is serial (safe
        # under a capped/optimizing collection).
        self._upsert_batch_size = max(1, upsert_batch_size)
        self._upsert_concurrency = max(1, upsert_concurrency)

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
        await self._upsert_points(points)

    async def _upsert_points(self, points: list[PointStruct]) -> None:
        """Upsert in bounded batches so one request never carries an oversized
        payload; pipeline the batches when ``upsert_concurrency`` > 1. Idempotent
        (deterministic point ids), so batch order and partial retries are safe."""
        bs = self._upsert_batch_size
        batches = [points[i : i + bs] for i in range(0, len(points), bs)]
        if len(batches) <= 1 or self._upsert_concurrency == 1:
            for batch in batches:
                await self._client.upsert(collection_name=self._collection, points=batch)
            return
        sem = asyncio.Semaphore(self._upsert_concurrency)

        async def _one(batch: list[PointStruct]) -> None:
            async with sem:
                await self._client.upsert(collection_name=self._collection, points=batch)

        await asyncio.gather(*(_one(b) for b in batches))

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

    async def count_tenants(self, tenants: list[str]) -> int:
        """Count points visible to ``tenants`` (own + public) via a FILTERED
        count.

        Uses ``client.count(count_filter=..., exact=True)`` — never
        ``get_collection().points_count``, which is the *whole shared
        collection* total and would leak every tenant's chunk count to a
        non-admin. Fails closed on an empty ``tenants`` list: ``_build_filter``
        drops an empty multi-value list as "no constraint" (fail-open → a global
        count), so the guard must happen here, before any filter is built.
        """
        if not tenants:
            return 0
        resp = await self._client.count(
            collection_name=self._collection,
            count_filter=_build_filter({"tenant_id": list(tenants)}),
            exact=True,
        )
        return int(resp.count)

    async def healthcheck(self) -> None:
        """Read-only liveness probe: a connectivity check that never mutates state.

        Uses ``get_collections`` (a plain list) rather than ``ensure_collection``,
        which would *create* the collection as a side effect — a health probe must
        not provision infrastructure. Raises on an unreachable server."""
        await self._client.get_collections()

    async def collection_health(self) -> CollectionHealth:
        """Read this collection's optimizer state for backpressure (#141).

        Wraps ``get_collection`` and normalizes the two shapes qdrant-client
        returns for status/optimizer across versions (enum vs. bare string;
        ``optimizer_status == "ok"`` vs. an object with an ``error``). Read-only —
        never provisions or mutates."""
        info = await self._client.get_collection(self._collection)
        # status may be a CollectionStatus enum ("CollectionStatus.GREEN" → "green")
        # or already a bare string; normalize to a lowercase name either way.
        raw_status = getattr(info, "status", "green")
        status = str(getattr(raw_status, "value", raw_status)).lower()
        # optimizer_status is "ok" (string/enum) when healthy, or an object with a
        # truthy ``error`` when it has failed.
        opt = getattr(info, "optimizer_status", "ok")
        optimizer_ok = not bool(getattr(opt, "error", None))
        segments = int(getattr(info, "segments_count", 0) or 0)
        return CollectionHealth(status=status, optimizer_ok=optimizer_ok,
                                segments_count=segments)

    async def delete(self, doc_id: str, tenant_id: str | None = None) -> None:
        # Tenant-scoped: a caller can only delete its own documents, even if it
        # knows another tenant's doc_id. tenant_id=None deletes across tenants.
        selector: dict[str, Any] = {"doc_id": doc_id}
        if tenant_id is not None:
            selector["tenant_id"] = tenant_id
        points_filter = _build_filter(selector)
        # selector always contains doc_id, so _build_filter never returns None here.
        assert points_filter is not None
        await self._client.delete(
            collection_name=self._collection,
            points_selector=points_filter,
        )

    async def delete_except(
        self, doc_id: str, keep_chunk_ids: set[str], tenant_id: str | None = None
    ) -> None:
        """Prune a document's *orphan* points — those whose chunk is no longer
        produced (e.g. an edited doc shifted offsets → new chunk ids). Scrolls the
        doc's existing point ids and deletes only the stale remainder **by id**
        (cost O(stale), not O(collection)), so it avoids the filtered-delete-at-
        scale timeout. Caller must upsert the kept chunks first, so a failure here
        can never lose data."""
        keep = {_point_id(cid, tenant_id or DEFAULT_TENANT) for cid in keep_chunk_ids}
        selector: dict[str, Any] = {"doc_id": doc_id}
        if tenant_id is not None:
            selector["tenant_id"] = tenant_id
        scroll_filter = _build_filter(selector)
        stale: list[str] = []
        offset: Any = None
        while True:
            points, offset = await self._client.scroll(
                collection_name=self._collection,
                scroll_filter=scroll_filter,
                with_payload=False,
                with_vectors=False,
                limit=1024,
                offset=offset,
            )
            stale.extend(str(p.id) for p in points if str(p.id) not in keep)
            if offset is None:
                break
        if stale:
            await self._client.delete(
                collection_name=self._collection,
                points_selector=PointIdsList(points=stale),  # type: ignore[arg-type]
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
    conditions: list[Condition] = []
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
