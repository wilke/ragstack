"""In-memory store adapters — used for local development and testing."""
from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence
from typing import Any

from ragstack.documents import (
    DocumentSummary,
    decode_cursor,
    document_from_chunk_metadata,
    encode_cursor,
)
from ragstack.models import Chunk, ScoredChunk, Triple
from ragstack.stores.filters import payload_matches, validate_filters
from ragstack.tenancy import readable_tenants, tenant_of


def _matches(chunk: Chunk, filters: dict[str, Any]) -> bool:
    """A chunk matches when every filter holds; a list value matches any entry
    (MatchAny — used for tenant reads: own + public; the ``tenant_id`` key is
    the historical owner field — see tenancy.OWNER_FIELD).

    An empty list matches *nothing* rather than lifting the constraint (#196):
    membership in the empty set is false, and reading it as "no constraint" would
    silently widen a scope key into a cross-tenant read. Only a key that is
    absent from ``filters`` is unconstrained. Keep in sync with ``_build_filter``
    in stores/qdrant.py and ``_build_query`` in stores/elasticsearch.py.

    Used by ``search()`` (free-form, client-suppliable filter keys). ``get_chunks``
    uses ``payload_matches`` in stores/filters.py instead — same bare-key
    grammar, plus a refusal for the handful of keys that can never address a
    real chunk field; see that module's docstring for why."""
    for key, value in filters.items():
        actual = chunk.metadata.get(key)
        if isinstance(value, (list, tuple, set)):
            if actual not in value:
                return False
        elif actual != value:
            return False
    return True


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryVectorStore:
    """Flat cosine-similarity vector store backed by a Python list."""

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []

    async def upsert(self, chunks: list[Chunk]) -> None:
        # Identity is (tenant, chunk id) so two tenants' copies of the same chunk
        # coexist rather than clobbering each other.
        incoming = {(tenant_of(c), c.id) for c in chunks}
        self._chunks = [c for c in self._chunks if (tenant_of(c), c.id) not in incoming]
        self._chunks.extend(chunks)

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[ScoredChunk]:
        candidates = self._chunks
        if filters:
            candidates = [c for c in candidates if _matches(c, filters)]
        scored = [
            ScoredChunk(
                chunk=c,
                score=_cosine(query_vector, c.embedding or []),
                retrieval_method="vector",
            )
            for c in candidates
            if c.embedding
        ]
        return sorted(scored, key=lambda x: x.score, reverse=True)[:top_k]

    async def drop_collection(self) -> bool:
        """Discard every chunk — the in-memory analogue of dropping the physical
        Qdrant collection (see ``QdrantVectorStore.drop_collection``). Returns
        whether anything was actually there, so the purge report can distinguish
        "removed" from "already empty"."""
        existed = bool(self._chunks)
        self._chunks = []
        return existed

    async def delete(self, doc_id: str, tenant_id: str | None = None) -> None:
        self._chunks = [
            c
            for c in self._chunks
            if not (c.doc_id == doc_id and (tenant_id is None or tenant_of(c) == tenant_id))
        ]

    async def delete_except(
        self, doc_id: str, keep_chunk_ids: set[str], tenant_id: str | None = None
    ) -> None:
        """Prune the doc's orphan chunks (id not in keep), tenant-scoped."""
        self._chunks = [
            c
            for c in self._chunks
            if not (
                c.doc_id == doc_id
                and (tenant_id is None or tenant_of(c) == tenant_id)
                and c.id not in keep_chunk_ids
            )
        ]

    async def count_tenants(self, tenants: list[str]) -> int:
        """Count chunks owned by any of ``tenants``. Fails closed (0) on empty."""
        allowed = set(tenants)
        if not allowed:
            return 0
        return sum(1 for c in self._chunks if tenant_of(c) in allowed)

    async def count(self) -> int:
        """Every chunk in the store — the live figure the chunk cap reads (#291)."""
        return len(self._chunks)

    async def get_chunks(
        self, chunk_ids: list[str], filters: dict[str, Any] | None = None
    ) -> list[Chunk]:
        """Fetch chunks by id, tenant-scoped via ``filters``; request order kept,
        missing/invisible ids omitted.

        Uses the shared ``payload_matches`` (stores/filters.py) rather than
        ``_matches`` — the same predicate ``QdrantVectorStore.get_chunks`` applies,
        so the two stores can't diverge on which filter keys they honour (#197).
        ``filters`` is validated before the ``ids`` early return — a refused key
        must reject the call outright, not get silently skipped because there was
        nothing to filter."""
        validate_filters(filters)
        ids = list(dict.fromkeys(chunk_ids))
        if not ids:
            return []
        wanted = set(ids)
        by_id = {
            c.id: c
            for c in self._chunks
            if c.id in wanted and payload_matches(c.metadata, filters)
        }
        return [by_id[c] for c in ids if c in by_id]


class InMemoryTextIndex:
    """Very simple bag-of-words text search for development/testing."""

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []

    async def index(self, chunks: list[Chunk]) -> None:
        # Identity is (tenant, chunk id) so two tenants' copies of the same chunk
        # coexist rather than the second being dropped as a duplicate.
        existing = {(tenant_of(c), c.id) for c in self._chunks}
        for chunk in chunks:
            key = (tenant_of(chunk), chunk.id)
            if key not in existing:
                self._chunks.append(chunk)
                existing.add(key)

    async def search(
        self,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[ScoredChunk]:
        query_tokens = set(query.lower().split())
        candidates = self._chunks
        if filters:
            candidates = [c for c in candidates if _matches(c, filters)]
        scored = []
        for chunk in candidates:
            tokens = set(chunk.content.lower().split())
            overlap = len(query_tokens & tokens)
            if overlap > 0:
                scored.append(
                    ScoredChunk(
                        chunk=chunk,
                        score=float(overlap) / len(query_tokens | tokens),
                        retrieval_method="bm25",
                    )
                )
        return sorted(scored, key=lambda x: x.score, reverse=True)[:top_k]

    async def drop_index(self) -> bool:
        """Discard every document — the in-memory analogue of dropping the ES
        index (see ``ElasticsearchTextIndex.drop_index``). Returns whether
        anything was actually there."""
        existed = bool(self._chunks)
        self._chunks = []
        return existed

    async def delete(self, doc_id: str, tenant_id: str | None = None) -> None:
        self._chunks = [
            c
            for c in self._chunks
            if not (c.doc_id == doc_id and (tenant_id is None or tenant_of(c) == tenant_id))
        ]

    async def delete_except(
        self, doc_id: str, keep_chunk_ids: set[str], tenant_id: str | None = None
    ) -> None:
        """Prune the doc's orphan chunks (id not in keep), tenant-scoped."""
        self._chunks = [
            c
            for c in self._chunks
            if not (
                c.doc_id == doc_id
                and (tenant_id is None or tenant_of(c) == tenant_id)
                and c.id not in keep_chunk_ids
            )
        ]

    async def count_tenants(self, tenants: list[str]) -> int:
        """Count chunks owned by any of ``tenants``. Fails closed (0) on empty."""
        allowed = set(tenants)
        if not allowed:
            return 0
        return sum(1 for c in self._chunks if tenant_of(c) in allowed)

    async def list_documents(
        self, tenants: list[str], limit: int = 100, cursor: str | None = None
    ) -> tuple[list[DocumentSummary], str | None]:
        """Distinct visible documents, deduped by ``doc_id`` and paginated by a
        ``doc_id`` anchor — the in-memory mirror of the ES composite aggregation
        (same doc_id ordering and cursor semantics). Fails closed on empty."""
        allowed = set(tenants)
        if not allowed:
            return [], None
        counts: dict[str, int] = {}
        exemplar: dict[str, Chunk] = {}
        for c in self._chunks:
            if tenant_of(c) not in allowed:
                continue
            counts[c.doc_id] = counts.get(c.doc_id, 0) + 1
            exemplar.setdefault(c.doc_id, c)
        doc_ids = sorted(exemplar)
        start = bisect_right(doc_ids, decode_cursor(cursor)) if cursor else 0
        page = doc_ids[start : start + limit]
        docs = [
            document_from_chunk_metadata(did, counts[did], dict(exemplar[did].metadata))
            for did in page
        ]
        next_cursor = (
            encode_cursor(page[-1]) if page and start + limit < len(doc_ids) else None
        )
        return docs, next_cursor


def _evidence_fields(t: Triple) -> dict[str, Any]:
    """The six #347 provenance fields — what an ON MATCH write replaces."""
    return {
        "evidence": t.evidence,
        "chunk_id": t.chunk_id,
        "derived_by": t.derived_by,
        "confidence": t.confidence,
        "subject_id": t.subject_id,
        "object_id": t.object_id,
    }


class InMemoryGraphStore:
    """In-memory knowledge-graph store backed by a list of triples.

    Scoped on the same two axes as :class:`~ragstack.stores.neo4j.Neo4jGraphStore`
    — ``tenant_id`` and ``collection`` (#209). Deliberately *not* isolated by
    holding one store instance per collection: the Neo4j backend can't do that
    (Community Edition serves a single database), so isolating here by object
    identity would let the unit suite pass on a guarantee the durable backend
    doesn't provide. Both stores carry the boundary in the data instead.

    Triple identity is ``(subject, predicate, object, doc_id, tenant_id,
    collection)`` — the same effective key as Neo4j's four-key ``:REL`` MERGE
    between its two ``(name, tenant_id, collection)`` endpoint nodes. ``doc_id``
    is part of it: the same fact extracted from two documents is two records,
    each with its own evidence and each deletable by ``delete_by_doc`` on its own.

    Evidence fields (#347) follow Neo4j's ``ON CREATE SET`` / ``ON MATCH SET``
    semantics: they are never part of the identity key, and re-adding a triple
    whose key already exists replaces the stored copy's six evidence fields with
    the incoming values (last writer wins) without growing the store.
    """

    def __init__(self) -> None:
        # Insertion-ordered so reads keep first-write order (a plain list did
        # before); keyed so a re-add can update in place instead of appending.
        self._by_key: dict[tuple[str, str, str, str, str, str], Triple] = {}
        # Entity-name index for ``match_entities`` (#349): per ``(tenant_id,
        # collection)`` bucket, case-folded name -> number of stored triples it
        # is an endpoint of. Maintained incrementally on every write and delete
        # (O(1) per triple), so a lookup is a dict probe per candidate — the
        # in-memory twin of an indexed ``IN`` lookup on Neo4j. A name only ever
        # ENTERS through ``add_triples``; a delete path that forgot to decrement
        # would leave a stale positive (a wasted, empty neighbourhood call),
        # never a missed entity.
        self._entity_refs: dict[tuple[str, str], dict[str, int]] = {}

    @property
    def _triples(self) -> list[Triple]:
        return list(self._by_key.values())

    async def add_triples(self, triples: list[Triple]) -> None:
        # Dedup includes doc_id, tenant_id and collection so two documents' —
        # or two tenants' or two collections' — identical (s,p,o) triples all
        # survive, matching Neo4j's edge identity. Keying without doc_id would
        # not just drop the second copy: with the in-place evidence update below
        # it would splice d2's evidence onto d1's record and make
        # delete_by_doc(d2) a no-op.
        by_key = self._by_key
        for triple in triples:
            key = self._key(triple)
            if key in by_key:
                # ON MATCH SET: identity unchanged, evidence takes the new values.
                by_key[key] = by_key[key].model_copy(update=_evidence_fields(triple))
            else:
                by_key[key] = triple
                self._index(triple, +1)

    @staticmethod
    def _key(t: Triple) -> tuple[str, str, str, str, str, str]:
        return (t.subject, t.predicate, t.object, t.doc_id, t.tenant_id, t.collection)

    def _index(self, t: Triple, delta: int) -> None:
        """Add (+1) or remove (-1) one triple's two endpoint names from the
        entity index bucket of its ``(tenant_id, collection)``."""
        bucket = self._entity_refs.setdefault((t.tenant_id, t.collection), {})
        for name in (t.subject.lower(), t.object.lower()):
            count = bucket.get(name, 0) + delta
            if count > 0:
                bucket[name] = count
            else:
                bucket.pop(name, None)
        if not bucket:
            self._entity_refs.pop((t.tenant_id, t.collection), None)

    async def match_entities(
        self,
        candidates: list[str],
        *,
        tenant_id: str | None,
        collection: str | Sequence[str] | None,
    ) -> list[str]:
        """Exact, case-folded membership of each candidate in the entity index,
        restricted to the buckets the caller may read — the same scope rule as
        ``_visible`` (own tenant + ``public``; exact collection, one name or the
        multi-collection leg's list; ``None`` = unscoped on that axis).
        Distinct, in candidate order. Cost is one dict probe per candidate per
        readable bucket, independent of graph size."""
        if not candidates:
            return []
        allowed = None if tenant_id is None else set(readable_tenants(tenant_id))
        wanted = (
            None if collection is None
            else {collection} if isinstance(collection, str) else set(collection)
        )
        buckets = [
            names for (tenant, coll), names in self._entity_refs.items()
            if (allowed is None or tenant in allowed)
            and (wanted is None or coll in wanted)
        ]
        if not buckets:
            return []
        seen: set[str] = set()
        out: list[str] = []
        for candidate in candidates:
            folded = candidate.lower()
            if folded in seen:
                continue
            if any(folded in names for names in buckets):
                seen.add(folded)
                out.append(folded)
        return out

    def _visible(
        self, tenant_id: str | None, collection: str | Sequence[str] | None = None
    ) -> list[Triple]:
        """Triples the caller may read: all when unscoped (dev/tests), else the
        caller's own tenant plus the shared ``public`` corpus, further narrowed to
        ``collection`` when one is given — one name, or a list of names for the
        multi-collection graph leg (issue #253: one neighbourhood query with
        ``collection IN [...]``, exact on every store).

        The collection test is exact equality / membership, so an unstamped
        legacy triple (``collection == ""``) matches no real collection — the
        same fail-closed behaviour Neo4j gives, where a null ``r.collection``
        never satisfies ``r.collection = $collection`` (or ``IN``)."""
        triples = self._triples
        if tenant_id is not None:
            allowed = set(readable_tenants(tenant_id))
            triples = [t for t in triples if t.tenant_id in allowed]
        if collection is not None:
            wanted = {collection} if isinstance(collection, str) else set(collection)
            triples = [t for t in triples if t.collection in wanted]
        return triples

    async def query_neighborhood(
        self,
        entity: str,
        depth: int = 1,
        tenant_id: str | None = None,
        collection: str | Sequence[str] | None = None,
    ) -> list[Triple]:
        entity_lower = entity.lower()
        visible = self._visible(tenant_id, collection)
        direct = [
            t for t in visible
            if entity_lower in t.subject.lower() or entity_lower in t.object.lower()
        ]
        if depth <= 1:
            return direct
        # Expand one more hop. Every hop re-enters through ``_visible``, so a
        # multi-hop walk can't tunnel through another collection's edge to reach
        # an entity the caller couldn't see directly (mirrors the Cypher path
        # clause, which requires *every* relationship on the path to be in scope).
        neighbours = {t.subject for t in direct} | {t.object for t in direct}
        extended = list(direct)
        for n in neighbours:
            extended += await self.query_neighborhood(
                n, depth=depth - 1, tenant_id=tenant_id, collection=collection
            )
        # Deduplicate
        seen: set[tuple[str, str, str]] = set()
        unique = []
        for t in extended:
            key = (t.subject, t.predicate, t.object)
            if key not in seen:
                seen.add(key)
                unique.append(t)
        return unique

    async def list_entities(
        self,
        tenant_id: str | None = None,
        limit: int = 100,
        collection: str | None = None,
    ) -> list[tuple[str, int]]:
        """Distinct entities (subjects + objects) the caller may read, each with
        the count of triples it participates in, most-connected first."""
        counts: dict[str, int] = {}
        for t in self._visible(tenant_id, collection):
            counts[t.subject] = counts.get(t.subject, 0) + 1
            counts[t.object] = counts.get(t.object, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:limit]

    async def stats(
        self, tenant_id: str | None = None, collection: str | None = None
    ) -> tuple[int, int]:
        """(distinct entities, relationship count) the caller may read. Reuses
        ``_visible`` so tenant + collection scoping is applied identically to the
        other reads."""
        visible = self._visible(tenant_id, collection)
        entities: set[str] = set()
        for t in visible:
            entities.add(t.subject)
            entities.add(t.object)
        return (len(entities), len(visible))

    async def delete_by_doc(
        self,
        doc_id: str,
        tenant_id: str | None = None,
        collection: str | None = None,
    ) -> None:
        """Drop ``doc_id``'s triples within the given tenant and collection. Both
        scopes must match: the same doc_id ingested into two collections keeps a
        separate triple set per collection, and a collection-blind delete would
        take both."""
        keep: dict[tuple[str, str, str, str, str, str], Triple] = {}
        for k, t in self._by_key.items():
            if (
                t.doc_id == doc_id
                and (tenant_id is None or t.tenant_id == tenant_id)
                and (collection is None or t.collection == collection)
            ):
                self._index(t, -1)
            else:
                keep[k] = t
        self._by_key = keep

    async def delete_collection(self, tenant_id: str | None, collection: str) -> int:
        """Drop every triple stamped ``collection`` (#380) — one tenant's when
        ``tenant_id`` is given (exact equality: the shared ``public`` corpus is
        never the caller's to delete), every tenant's when ``None`` (the
        collection-wide form eviction and purge use, #295). Returns the number
        removed; a repeat call removes 0.

        There is no separate orphan sweep here: entities are derived from the
        triples on every read (``list_entities`` / ``stats``), so an entity
        whose last edge went with the collection is gone the same instant —
        and one shared by name with another collection is still reachable
        through that collection's surviving triples, exactly as Neo4j's
        per-collection node identity keeps it.

        The entity index (#349) is kept in step by dropping the affected
        ``(tenant_id, collection)`` buckets whole rather than decrementing per
        triple: a bucket is exactly the names of that scope's triples, and this
        delete removes every one of them, so the bucket's counts all reach zero
        — the same end state as ``_index(t, -1)`` per triple, in O(buckets).
        Left in place, the stale names would be false positives for
        ``match_entities`` and, with selection capped, could displace a live
        entity from another collection."""
        if not collection:
            raise ValueError("delete_collection needs a collection; '' would match unstamped triples")
        before = len(self._by_key)
        self._by_key = {
            k: t
            for k, t in self._by_key.items()
            if not (
                t.collection == collection
                and (tenant_id is None or t.tenant_id == tenant_id)
            )
        }
        self._entity_refs = {
            scope: names for scope, names in self._entity_refs.items()
            if not (scope[1] == collection and (tenant_id is None or scope[0] == tenant_id))
        }
        return before - len(self._by_key)
