"""In-memory store adapters — used for local development and testing."""
from __future__ import annotations

import math
from typing import Any

from ragstack.models import Chunk, ScoredChunk, Triple
from ragstack.tenancy import DEFAULT_TENANT


def _matches(chunk: Chunk, filters: dict[str, Any]) -> bool:
    """A chunk matches when every filter holds; a list value matches any entry
    (MatchAny — used for tenant reads: own + public)."""
    for key, value in filters.items():
        actual = chunk.metadata.get(key)
        if isinstance(value, (list, tuple, set)):
            if actual not in value:
                return False
        elif actual != value:
            return False
    return True


def _tenant_of(chunk: Chunk) -> str:
    return str(chunk.metadata.get("tenant_id", DEFAULT_TENANT))


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
        def key(c: Chunk) -> tuple[str, str]:
            return (_tenant_of(c), c.id)

        incoming = {key(c) for c in chunks}
        self._chunks = [c for c in self._chunks if key(c) not in incoming]
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

    async def delete(self, doc_id: str, tenant_id: str | None = None) -> None:
        self._chunks = [
            c
            for c in self._chunks
            if not (c.doc_id == doc_id and (tenant_id is None or _tenant_of(c) == tenant_id))
        ]


class InMemoryTextIndex:
    """Very simple bag-of-words text search for development/testing."""

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []

    async def index(self, chunks: list[Chunk]) -> None:
        existing_ids = {c.id for c in self._chunks}
        for chunk in chunks:
            if chunk.id not in existing_ids:
                self._chunks.append(chunk)

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

    async def delete(self, doc_id: str) -> None:
        self._chunks = [c for c in self._chunks if c.doc_id != doc_id]


class InMemoryGraphStore:
    """In-memory knowledge-graph store backed by a list of triples."""

    def __init__(self) -> None:
        self._triples: list[Triple] = []

    async def add_triples(self, triples: list[Triple]) -> None:
        existing = {(t.subject, t.predicate, t.object) for t in self._triples}
        for triple in triples:
            key = (triple.subject, triple.predicate, triple.object)
            if key not in existing:
                self._triples.append(triple)
                existing.add(key)

    async def query_neighborhood(self, entity: str, depth: int = 1) -> list[Triple]:
        entity_lower = entity.lower()
        direct = [
            t for t in self._triples
            if entity_lower in t.subject.lower() or entity_lower in t.object.lower()
        ]
        if depth <= 1:
            return direct
        # Expand one more hop
        neighbours = {t.subject for t in direct} | {t.object for t in direct}
        extended = list(direct)
        for n in neighbours:
            extended += await self.query_neighborhood(n, depth=depth - 1)
        # Deduplicate
        seen: set[tuple[str, str, str]] = set()
        unique = []
        for t in extended:
            key = (t.subject, t.predicate, t.object)
            if key not in seen:
                seen.add(key)
                unique.append(t)
        return unique

    async def delete_by_doc(self, doc_id: str) -> None:
        self._triples = [t for t in self._triples if t.doc_id != doc_id]
