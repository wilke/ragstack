"""Qdrant-backed VectorStore adapter."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass
from time import perf_counter
from typing import Any

# httpx is a hard dependency of qdrant-client (which this module imports
# unconditionally), so this adds no new install requirement. Needed at module
# scope because _failure_kind classifies the transport exception types.
import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import ApiException
from qdrant_client.models import (
    Condition,
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointIdsList,
    PointStruct,
    VectorParams,
)

from ragstack.models import Chunk, ScoredChunk
from ragstack.stores.errors import (
    KIND_ERROR,
    KIND_TIMEOUT,
    KIND_UNREACHABLE,
    StoreUnavailable,
    VectorDimMismatch,
)
from ragstack.stores.filters import PAYLOAD_RESERVED as _PAYLOAD_RESERVED
from ragstack.stores.filters import payload_matches, validate_filters
from ragstack.tenancy import DEFAULT_TENANT, OWNER_FIELD, tenant_of

log = logging.getLogger(__name__)

# Payload field carrying the tenant on every point. Indexed so tenant-filtered
# counts and searches use the index instead of scanning the whole collection.
# The payload key records the chunk's writer/owner — see tenancy.OWNER_FIELD
# and ADR-0003 decision 1; access is decided at the collection (resolve_access),
# this filter is defence in depth.
_TENANT_FIELD = OWNER_FIELD
# Upper bound (seconds) on a tenant-filtered count, so an unindexed large
# collection degrades to "unavailable" fast instead of hanging the read.
_COUNT_TIMEOUT_S = 5
# Upper bound (seconds) on the opt-in post-mortem probe (#427 W9). It CANNOT be
# left to the client's own timeout: that is `qdrant_timeout`, 30 s by default and
# 60 s on the two tenants serving the collection from the incident, so an
# unbounded probe would add a minute to a request that has already failed.
_PROBE_TIMEOUT_S = 2.0
# One probe per collection per this many seconds. A store having a bad minute
# must not earn a probe per failed request.
_PROBE_MIN_INTERVAL_S = 60.0
# Module-level alias, deliberately: it is the seam tests drive the rate limiter
# through (monkeypatch a controllable clock) — same style as the existing
# `monkeypatch.setattr(qdrant_mod, "AsyncQdrantClient", ...)`. Monotonic, so a
# system clock step cannot open or wedge the window.
_monotonic = time.monotonic

__all__ = [
    "QdrantVectorStore",
    "VectorDimMismatch",
    "collection_name",
    "CollectionHealth",
]


def _failure_kind(e: Exception) -> str:
    """Classify a qdrant-client failure into a :data:`STORE_FAILURE_KINDS` value.

    Lives here rather than in ``stores/errors.py`` on purpose: ``errors.py`` is
    documented dependency-free so a caller can catch ``StoreUnavailable``
    without importing an optional backend, and this mapping needs ``httpx``.

    .. rubric:: The ordering is load-bearing

    ``httpx.ConnectTimeout`` **is a subclass of** ``httpx.TimeoutException``, so
    the connect branch must be checked FIRST. Get it the other way round and a
    connect timeout is reported as ``timeout``, which tells the user "retry, the
    second read is warm" about a store they never reached. See
    ``stores/errors.py`` for why that distinction is worth a comment this long.
    """
    # `source` is what ResponseHandlingException carries the transport error on;
    # __cause__ covers a plain `raise ... from`. Deliberately duplicated from
    # `_describe_failure` rather than factored out — that method's sentence is
    # the one artefact that made the #427 incident diagnosable and it is left
    # byte-for-byte alone.
    cause = getattr(e, "source", None) or getattr(e, "__cause__", None)
    inner = cause if cause is not None else e
    if isinstance(inner, (httpx.ConnectTimeout, httpx.ConnectError)):
        return KIND_UNREACHABLE
    if isinstance(inner, httpx.TimeoutException):
        # ReadTimeout / WriteTimeout / PoolTimeout — connected, then too slow.
        return KIND_TIMEOUT
    return KIND_ERROR


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
    #: Points stored vs. vectors actually indexed, as OBSERVABILITY only — do not
    #: derive a backlog from their difference. Measured on live collections, that
    #: difference is unsound in both regimes:
    #:
    #: - below Qdrant's ``indexing_threshold`` no HNSW is built, so
    #:   ``indexed_vectors_count`` is 0 *by design, forever* — a permanent phantom
    #:   backlog exactly during the ramp-up of every bulk load;
    #: - on a mature collection ``indexed`` routinely EXCEEDS ``points`` (observed
    #:   +125,051 on a 24.8M-point production collection), so any clamped
    #:   difference pins to 0 precisely where a backlog signal would matter.
    #:
    #: Both default to 0, so every existing constructor call stays valid.
    points_count: int = 0
    indexed_vectors_count: int = 0


def _slug(s: str, n: int = 40) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s or "").strip("_").lower()[:n]


def collection_name(
    base: str, model: str | None, dim: int, *, chunk: str | None = None, name: str | None = None
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

    ``name`` switches from *corpus* semantics to *named-library* semantics. Content
    addressing is right for a corpus (re-ingesting the same build spec is
    idempotent) but WRONG for a user-named library: two libraries built with the
    same embedding model + chunker are different data and MUST NOT share a physical
    store, or content uploaded to one shows up in the other and a delete hits both.
    When ``name`` is given the name gains a ``lib`` marker + a slug of the name, and
    the hash covers ``name|model|dim|chunk`` — so distinct names always yield
    distinct collections, including names that slugify identically ("open access"
    vs "open-access") or slugify to nothing at all. The result stays deterministic,
    lowercase ``[a-z0-9_]``, never leading-``_``, and short enough for both Qdrant
    and Elasticsearch (≲110 chars vs ES's 255-byte index-name limit).
    """
    if name:
        # ``.strip("_")`` because _slug truncates *after* stripping, so a cut mid-slug
        # can leave a trailing "_" and a doubled separator. Stripping here (not inside
        # _slug) keeps every already-built content-addressed name byte-identical.
        parts = [
            base,
            "lib",
            _slug(name, 32).strip("_"),
            _slug(model or "default", 24).strip("_"),
            str(dim),
        ]
        if chunk is not None:
            parts.append(_slug(chunk, 20).strip("_"))
        digest = hashlib.sha1(f"{name}|{model or ''}|{dim}|{chunk or ''}".encode()).hexdigest()[:8]
        parts.append(digest)
        return "_".join(p for p in parts if p)
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
        postmortem_probe: bool = False,
    ) -> None:
        # `timeout` (seconds) bounds each request; raise it for heavy ops (large
        # filtered deletes) so they fail fast/explicitly instead of hanging.
        # None leaves qdrant-client on httpx's 5 s default — the API passes
        # settings.qdrant_timeout so a slow search is not mistaken for an outage.
        self._client = AsyncQdrantClient(url=url, api_key=api_key or None, timeout=timeout)
        self._url = url
        self._timeout = timeout
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
        # #427 W9, opt-in and default off — see _postmortem_probe. The rate-limit
        # state lives on the instance because an instance is bound to exactly one
        # collection (``self._collection`` is fixed at construction), so
        # per-instance IS per-collection, with no module-global to leak between
        # requests, processes or tests.
        self._postmortem_enabled = postmortem_probe
        self._probe_last: float | None = None

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
        else:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(
                    size=self._vector_size, distance=self._distance
                ),
            )
        # Index the fields our filters actually use, so filtered operations hit
        # an index rather than a full scan. Runs for pre-existing collections too
        # (back-fills a missing index; Qdrant builds it in the background).
        await self._ensure_payload_indexes()

    async def _ensure_payload_indexes(self) -> None:
        """Keyword payload indexes on ``tenant_id`` AND ``doc_id``. Idempotent and
        best-effort: a pre-existing index (or transient failure) is non-fatal —
        operations just fall back to the scan path until it is built.

        ``doc_id`` is not optional at ingest scale: ``delete()`` (the per-document
        delete-prior every re-ingest and bulk load performs) filters on it, and
        without the index each delete is a full collection scan. Measured on the
        OA pilot: a 64-shard load ground to ~1 delete/s once the store passed
        ~150k points — the "hung" container load was this — and jumped to
        ~125/s the moment the index was created live."""
        for field in (_TENANT_FIELD, "doc_id"):
            try:
                await self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception as e:  # noqa: BLE001 — already-exists / transient; non-fatal
                log.debug("%s index on %r not (re)created: %s", field, self._collection, e)

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
        #
        # Timed, and the elapsed time is NOT inferable from the timeout setting:
        # a ReadTimeout burns the whole bound while a ConnectError fails in
        # milliseconds against the same one. #427's incident could not answer
        # "how much of the 30s did this consume?" — this is that answer.
        started = perf_counter()
        try:
            response = await self._client.query_points(
                collection_name=self._collection,
                query=query_vector,
                limit=top_k,
                query_filter=q_filter,
                with_payload=True,
            )
        except ApiException as e:
            # ResponseHandlingException wraps the transport error (ReadTimeout,
            # ConnectError); UnexpectedResponse is a Qdrant-side non-2xx. Both are
            # "the store didn't answer", not a bug in this request — surface them
            # as StoreUnavailable so the API answers 503 with the reason instead
            # of a bare 500 and a 40-frame httpx traceback.
            kind = _failure_kind(e)
            # Captured BEFORE the probe, deliberately: `elapsed_s` is the STORE's
            # own latency — the measurement #427 could not make — and letting the
            # probe's up-to-2 s ride into it would corrupt the number this field
            # exists to report.
            elapsed_s = perf_counter() - started
            if kind == KIND_TIMEOUT:
                await self._postmortem_probe()
            raise StoreUnavailable(
                "qdrant",
                self._describe_failure(e),
                kind=kind,
                elapsed_s=elapsed_s,
            ) from e
        return [
            ScoredChunk(
                chunk=_chunk_from_payload(r.payload, r.id),
                score=r.score,
                retrieval_method="vector",
            )
            for r in response.points
        ]

    def _describe_failure(self, e: Exception) -> str:
        """One readable line: what failed, where, and the knob that bounds it."""
        cause = getattr(e, "source", None) or getattr(e, "__cause__", None)
        inner = cause if cause is not None else e
        reason = f"{type(inner).__name__}: {inner}".rstrip(": ")
        bound = (
            f"{self._timeout}s (QDRANT_TIMEOUT)"
            if self._timeout is not None
            else "client default 5s (QDRANT_TIMEOUT unset)"
        )
        return (
            f"qdrant search on {self._collection!r} at {self._url} failed — {reason}; "
            f"per-request timeout is {bound}"
        )

    async def _postmortem_probe(self) -> None:
        """After a search TIMED OUT, read this collection's optimizer state once
        and log the raw counters (#427 W9). Opt-in, default off, never raises.

        .. rubric:: What this buys that the shipped fields do not

        W2a already puts ``elapsed_s`` and ``reason`` on every store failure, so
        the log answers *how long* the store took and *which failure class* it
        was. Neither can see **optimizer or indexing churn**: a collection
        mid-optimize is a genuinely different candidate cause from the cold page
        cache everyone assumes, and today the two are indistinguishable after the
        fact. ``status`` (``yellow``/``grey``/``red``), ``optimizer_ok`` and the
        segment count are the only evidence in this repo's reach that separates
        them. That distinction is the entire justification for this method.

        .. rubric:: What it does NOT buy

        It does **not** distinguish a cold page cache — the incident's other
        leading hypothesis. Host-level cache state is Qdrant-side telemetry and
        is not observable from this process at all. Do not read a green, idle
        probe as "so it must have been the cache"; read it as "not optimizer
        churn", which is a smaller claim and the only one it supports.

        .. rubric:: Why it is opt-in, bounded and rate-limited

        It sends a request to a store that has just failed to answer one. So:
        off unless ``QDRANT_POSTMORTEM_PROBE`` is set; bounded at
        ``_PROBE_TIMEOUT_S`` by ``asyncio.wait_for`` rather than by the client's
        own 30–60 s ``QDRANT_TIMEOUT``; and at most one per collection per
        ``_PROBE_MIN_INTERVAL_S``, so a store having a bad minute cannot earn a
        probe per failed request. Only ``kind="timeout"`` reaches here — a store
        that was never reached (``unreachable``) will not answer a probe either,
        and trying costs the caller 2 s for nothing.

        .. rubric:: Raw counters only

        ``points_count`` and ``indexed_vectors_count`` are logged as read. No
        backlog is derived from their difference: :class:`CollectionHealth`
        records, from live measurement, that the difference is meaningless in
        both regimes (0 by design below the indexing threshold; routinely
        NEGATIVE on a mature collection). A human reads the two numbers.

        .. rubric:: It must never make the failure worse

        Every ``Exception`` the probe raises — including its own timeout — is
        swallowed to one log line, and the original ``StoreUnavailable`` then
        propagates unchanged. The one deliberate exception is
        ``asyncio.CancelledError``: it is a ``BaseException``, it means the
        caller went away mid-probe, and swallowing a cancellation to keep
        answering a request nobody is waiting for is the wrong trade (W1's
        middleware already records that case as ``client_disconnected``).

        The line is emitted at WARNING, matching the store-failure line it
        explains: an operator who has set ``LOG_LEVEL=WARNING`` (or flipped it at
        runtime via ``PUT /v1/admin/log-level``) keeps both lines or neither.
        Keeping the failure and losing its explanation would defeat the point.
        No ``rid`` is passed: ``RequestContextFilter`` stamps it on every record
        from the contextvar, so this line already carries the id of the request
        whose failure it explains — that correlation is what makes it usable.
        """
        if not self._postmortem_enabled:
            return
        now = _monotonic()
        last = self._probe_last
        if last is not None and (now - last) < _PROBE_MIN_INTERVAL_S:
            return
        # Check and set with NO await in between: on a single-threaded event loop
        # that makes the gate atomic, so N concurrent timeouts on this collection
        # yield exactly one probe rather than N in flight together.
        self._probe_last = now
        started = perf_counter()
        try:
            health = await asyncio.wait_for(self.collection_health(), _PROBE_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 — see "never make the failure worse"
            # Same shape as _describe_failure's sentence, and it names the bound:
            # the commonest case here is our own TimeoutError, whose str() is
            # empty, which would otherwise log a reason of nothing at all.
            reason = f"{type(e).__name__}: {e}".rstrip(": ")
            log.warning(
                "qdrant post-mortem probe failed on %r — %s; probe bound is %ss",
                self._collection, reason, _PROBE_TIMEOUT_S,
                extra={
                    "store": "qdrant",
                    # The PHYSICAL Qdrant collection, which is not necessarily the
                    # failure line's `coll` (that one is the registry id).
                    "probe_collection": self._collection,
                    "probe_ms": round((perf_counter() - started) * 1000),
                },
            )
            return
        log.warning(
            "qdrant post-mortem probe on %r", self._collection,
            extra={
                "store": "qdrant",
                "probe_collection": self._collection,
                "status": health.status,
                "optimizer_ok": health.optimizer_ok,
                "segments": health.segments_count,
                "points": health.points_count,
                "indexed_vectors": health.indexed_vectors_count,
                "probe_ms": round((perf_counter() - started) * 1000),
            },
        )

    async def count_tenants(self, tenants: list[str]) -> int:
        """Count points visible to ``tenants`` (own + public) via a FILTERED
        count.

        Uses ``client.count(count_filter=..., exact=True)`` — never
        ``get_collection().points_count``, which is the *whole shared
        collection* total and would leak every tenant's chunk count to a
        non-admin. Fails closed on an empty ``tenants`` list — ``_build_filter``
        now fails closed too (an empty list matches nothing, #196), so this guard
        is belt-and-braces: it just skips a round trip that can only return 0.

        Exact where affordable, estimate where not. The tenant field is indexed
        (see ``_ensure_payload_indexes``) so *filtering* is fast — but an EXACT count
        still enumerates every matching point, which on a huge, largely
        single-tenant collection (e.g. 24.8M points all ``public``) is O(matches)
        and blows past ``_COUNT_TIMEOUT_S``. On that timeout we fall back to
        Qdrant's segment-based ESTIMATE (``exact=False``), which is ~instant, so
        the /collections + /stats reads always return a number (approximate for
        the giants, exact for everything small enough) instead of hanging or
        degrading to null.
        """
        if not tenants:
            return 0
        count_filter = _build_filter({"tenant_id": list(tenants)})
        try:
            resp = await self._client.count(
                collection_name=self._collection,
                count_filter=count_filter,
                exact=True,
                timeout=_COUNT_TIMEOUT_S,
            )
        except Exception as e:  # noqa: BLE001 — exact timed out on a large match set
            log.debug(
                "exact tenant count on %r fell back to estimate: %s", self._collection, e
            )
            resp = await self._client.count(
                collection_name=self._collection, count_filter=count_filter, exact=False
            )
        return int(resp.count)

    async def count(self) -> int:
        """Every point in the collection — the live figure the per-collection
        chunk cap compares against, ONCE per ingest job (#291). Unfiltered on
        purpose (the cap bounds the collection, not a tenant's stripe), so it is
        for the ingest path only — a reader gets ``count_tenants``. Same exact-
        with-estimate-fallback pattern as ``count_tenants``: an exact count of
        a capped (<= 50k) collection is instant, and a curated giant under an
        explicit override falls back to Qdrant's segment estimate rather than
        hanging the job."""
        try:
            resp = await self._client.count(
                collection_name=self._collection, exact=True, timeout=_COUNT_TIMEOUT_S,
            )
        except Exception as e:  # noqa: BLE001 — exact timed out on a giant collection
            log.debug("exact count on %r fell back to estimate: %s", self._collection, e)
            resp = await self._client.count(collection_name=self._collection, exact=False)
        return int(resp.count)

    async def get_chunks(
        self, chunk_ids: list[str], filters: dict[str, Any] | None = None
    ) -> list[Chunk]:
        """Fetch chunks by id, tenant-scoped via ``filters`` (the ``tenant_id``
        read scope produced by ``scope_filters``, same as ``search``). Preserves
        request order; missing/invisible ids are omitted.

        Point ids are deterministic (``_point_id(chunk_id, tenant)``), so this
        resolves by point id — an O(1) ``retrieve`` per ``(id, tenant)`` — rather
        than filtering the (unindexed) ``chunk_id`` payload, which would scan the
        collection. Only ids under a readable tenant are ever computed, so the
        lookup is tenant-scoped by construction; with no readable scope it fails
        closed (empty), mirroring ``count_tenants``.

        The ``retrieve`` only narrows by (id, tenant) — it cannot express the
        *rest* of ``filters`` (e.g. ``collection``, #197) server-side, so every
        returned record is re-checked against the full ``filters`` dict via the
        shared ``payload_matches`` predicate (stores/filters.py) before being
        kept. This also re-asserts ``tenant_id`` against the actual payload
        rather than trusting the point-id derivation alone, matching what
        ``InMemoryVectorStore.get_chunks`` has always done. That re-check
        assumes every writer stamps ``tenant_id`` on the chunk it ingests —
        true today (``ingestion/pipeline.py``, ``ingestion/load_embeddings.py``,
        ``scripts/ingest_chunks.py`` all set ``chunk.metadata["tenant_id"]``
        before the chunk reaches a store) — an unstamped chunk would simply
        never match and would be omitted, not raise.

        ``filters`` is validated FIRST, before the ids/tenants early return —
        an unsupported key must refuse the call outright, not just get silently
        skipped when the (id, tenant) narrowing happens to come back empty."""
        validate_filters(filters)
        ids = list(dict.fromkeys(chunk_ids))  # de-dup, keep order
        tenants = (filters or {}).get("tenant_id")
        if not ids or not isinstance(tenants, (list, tuple, set)) or not tenants:
            return []
        records = await self._client.retrieve(
            collection_name=self._collection,
            ids=[_point_id(cid, str(t)) for cid in ids for t in tenants],
            with_payload=True,
            with_vectors=False,
        )
        found: dict[str, Chunk] = {}
        for r in records:
            ch = _chunk_from_payload(r.payload, r.id)
            if payload_matches(ch.metadata, filters):
                found.setdefault(ch.id, ch)
        return [found[c] for c in ids if c in found]

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
        points = int(getattr(info, "points_count", 0) or 0)
        indexed = int(getattr(info, "indexed_vectors_count", 0) or 0)
        return CollectionHealth(status=status, optimizer_ok=optimizer_ok,
                                segments_count=segments, points_count=points,
                                indexed_vectors_count=indexed)

    async def drop_collection(self) -> bool:
        """Delete the entire physical collection — every vector, every tenant.

        The nuclear counterpart to :meth:`ensure_collection`, and the only method
        on this class that is NOT tenant-scoped: dropping a collection is a
        registry/ops operation (``DELETE /v1/collections/{id}?purge=true``), never
        something a query-path caller reaches.

        Idempotent: returns ``True`` when a collection was actually removed and
        ``False`` when there was nothing there, so a purge can report "already
        gone" honestly instead of inventing a deletion. Only *errors* raise.
        """
        if not await self._client.collection_exists(self._collection):
            return False
        await self._client.delete_collection(collection_name=self._collection)
        return True

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


def _chunk_from_payload(payload: Any, fallback_id: Any) -> Chunk:
    """Reconstruct a Chunk from a Qdrant point payload (reserved keys back into
    their fields, the remainder as metadata)."""
    p = dict(payload or {})
    return Chunk(
        id=str(p.pop("chunk_id", fallback_id)),
        doc_id=str(p.pop("doc_id", "")),
        content=str(p.pop("content", "")),
        start_char=int(p.pop("start_char", 0) or 0),
        end_char=int(p.pop("end_char", 0) or 0),
        metadata=p,
    )


def _build_filter(filters: dict[str, Any] | None) -> Filter | None:
    """Build a Qdrant filter from a flat dict. A list value matches *any* of its
    entries (MatchAny) — used for tenant reads (own + public); a scalar is an
    exact match.

    An **empty** list matches nothing, it is not "no constraint" (#196):
    membership in the empty set is false, and treating it as unconstrained would
    silently drop a scope key — with every key empty the read would go out
    unfiltered, across all tenants. ``MatchAny(any=[])`` is unsatisfiable
    server-side, so the fail-closed reading needs no special condition type.
    Only ``None`` or a dict with *no keys at all* means unfiltered; a key that is
    present with an empty list is a real (unsatisfiable) constraint. Keep this in
    sync with ``_matches`` in stores/memory.py and ``_build_query`` in
    stores/elasticsearch.py. (``get_chunks`` uses ``payload_matches`` in
    stores/filters.py instead of this builder — same bare-key grammar, plus a
    refusal for the handful of keys that can never address a real chunk field;
    see that module's docstring for why.)

    Why match-nothing rather than raising as ``_build_query`` does for its tenant
    key: this builder also serves the deliberately unscoped delete paths
    (``delete``/``delete_except`` with ``tenant_id=None``), so it cannot require
    a tenant key — and every caller then gets fail-closed behaviour for free
    instead of needing its own guard."""
    if not filters:
        return None
    conditions: list[Condition] = []
    for key, value in filters.items():
        if isinstance(value, (list, tuple, set)):
            conditions.append(FieldCondition(key=key, match=MatchAny(any=list(value))))
        else:
            conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
    # Every key contributes a condition, so a non-empty ``filters`` can never
    # collapse to ``None`` (an unfiltered read).
    return Filter(must=conditions)
