"""Neo4j-backed GraphStore (knowledge graph), tenant- and collection-scoped.

Triples become a property graph: ``(:Entity {name, tenant_id, collection})-[:REL
{predicate, doc_id, tenant_id, collection, +evidence props}]->(:Entity {...})``. The
relationship's *identity* is the four-key MERGE; the evidence properties (#347:
``evidence``, ``chunk_id``, ``derived_by``, ``confidence``, ``subject_id``,
``object_id``) are set alongside it with ``ON CREATE SET`` / ``ON MATCH SET`` so
re-ingest stays idempotent and the latest write wins. Entities are keyed
by ``(name, tenant_id, collection)`` so the same surface form under two tenants —
or in two collections — is two distinct nodes; neither boundary can be read or
deleted across. Reads filter relationships to the caller's *readable* tenants (own
+ the shared ``public`` corpus) and to the caller's collection; an unscoped read
(``tenant_id=None`` / ``collection=None``) is allowed only for dev/tests and admin
inspection, and sees everything on that axis.

Query-side entity extraction (#349) is ``match_entities``: the retriever's n-gram
candidates against ``toLower(e.name) IN $candidates`` plus the scope predicates,
one round trip. No index was added for it: Neo4j 5 has no expression indexes, so
a ``toLower()`` predicate can't be index-backed whatever is declared, and the
graph holds no production data (#350) to make that cost measurable. The upgrade
path, if graph scale ever matters, is a stored case-folded ``name_lc`` property
with its own index (+ a one-off backfill in ``ensure_schema``).

Why the collection lives in the data rather than in one store instance per
collection (#209): unlike Qdrant/ES, where each collection gets its own physical
collection/index, Neo4j Community serves a single database — N ``Neo4jGraphStore``
objects would all point at the same graph. Stamping is the only mechanism that
isolates identically here and in ``InMemoryGraphStore``.

The ``neo4j`` driver import is lazy so the optional ``graph`` extra is only
required when this backend is actually selected. LLM-driven triple extraction
quality and graph/vector retriever fusion are deferred to M4 Phase 2 (see the
TODO in ``query_neighborhood``).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ragstack.models import CONFIDENCE_MAX, DERIVED_BY_LLM, LLM_MAX_CONFIDENCE, Triple
from ragstack.tenancy import DEFAULT_TENANT, readable_tenants

# Upper bound on neighbourhood hops. ``depth`` is interpolated into the Cypher
# variable-length pattern ``REL*1..{depth}``, so an unbounded value would make
# Neo4j enumerate exponentially many paths (a DoS). The API caps it too.
_MAX_DEPTH = 5

if TYPE_CHECKING:  # pragma: no cover - typing only
    from neo4j import AsyncDriver


# Evidence properties on the :REL edge (#347), in one place so the write (SET)
# and the read (RETURN) can't drift apart. Never part of the MERGE key.
_EVIDENCE_PROPS = ("evidence", "chunk_id", "derived_by", "confidence", "subject_id", "object_id")
_EVIDENCE_SET = ", ".join(f"r.{p} = row.{p}" for p in _EVIDENCE_PROPS)
_EVIDENCE_RETURN = ", ".join(f"r.{p} AS {p}" for p in _EVIDENCE_PROPS)


log = logging.getLogger(__name__)


def _tenant_or_default(tenant_id: str) -> str:
    return tenant_id or DEFAULT_TENANT


def _read_confidence(rec: Any) -> int:
    """``r.confidence`` as the model accepts it. Every write path goes through
    ``Triple``, so a stored value that is not an int in 0–3 — or an ``"llm"`` edge
    above the no-launder cap — means something bypassed the model (a hand edit,
    another writer). Repair it so the read doesn't fail, but WARN: silently
    clamping would hide the bypass."""
    raw = rec.get("confidence")
    if raw is None:
        return 0  # pre-#347 edge: unknown, not invisible
    try:
        value = int(raw)
    except (TypeError, ValueError):
        log.warning("neo4j: non-integer r.confidence %r; reading as 0", raw)
        return 0
    clamped = min(max(value, 0), CONFIDENCE_MAX)
    if clamped != value:
        log.warning("neo4j: r.confidence %r outside 0..%d; reading as %d",
                    raw, CONFIDENCE_MAX, clamped)
    if rec.get("derived_by") == DERIVED_BY_LLM and clamped > LLM_MAX_CONFIDENCE:
        log.warning("neo4j: %r edge stored with confidence %d above the cap %d; reading as %d",
                    DERIVED_BY_LLM, clamped, LLM_MAX_CONFIDENCE, LLM_MAX_CONFIDENCE)
        clamped = LLM_MAX_CONFIDENCE
    return clamped


class Neo4jGraphStore:
    """GraphStore protocol backed by Neo4j 5.

    Note: Neo4j 5 rejects the literal password ``neo4j`` — the deployed stack
    uses ``ragstack`` (``config/rag.env``). The driver is constructed eagerly but
    opens no socket until the first query, so building this in ``deps`` stays
    offline-safe for unit tests.
    """

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        database: str | None = None,
    ) -> None:
        from neo4j import AsyncGraphDatabase

        self._driver: AsyncDriver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    async def ensure_schema(self) -> None:
        """Create the uniqueness/index constraints idempotently so node lookups and
        MERGE stay fast and ``(name, tenant_id, collection)`` identity is enforced
        by the DB.

        The pre-#209 ``entity_name_tenant`` constraint is dropped first: keyed on
        ``(name, tenant_id)`` alone it would *reject* the same entity name existing
        in two collections, which is exactly what collection scoping requires. Both
        statements are idempotent, so this doubles as the one-way migration for an
        existing graph."""
        async with self._session() as session:
            await session.run("DROP CONSTRAINT entity_name_tenant IF EXISTS")
            await session.run(
                "CREATE CONSTRAINT entity_name_tenant_collection IF NOT EXISTS "
                "FOR (e:Entity) REQUIRE (e.name, e.tenant_id, e.collection) IS UNIQUE"
            )

    def _session(self) -> Any:
        if self._database:
            return self._driver.session(database=self._database)
        return self._driver.session()

    async def add_triples(self, triples: list[Triple]) -> None:
        """Upsert triples. Each triple's ``tenant_id`` *and* ``collection`` are
        stamped onto both endpoint nodes and the relationship, so both isolation
        boundaries are properties of the stored data rather than something the
        read path has to reconstruct."""
        if not triples:
            return
        rows = [
            {
                "subject": t.subject,
                "predicate": t.predicate,
                "object": t.object,
                "doc_id": t.doc_id,
                "tenant_id": _tenant_or_default(t.tenant_id),
                "collection": t.collection,
                "evidence": t.evidence,
                "chunk_id": t.chunk_id,
                "derived_by": t.derived_by,
                "confidence": t.confidence,
                "subject_id": t.subject_id,
                "object_id": t.object_id,
            }
            for t in triples
        ]
        # MERGE on (name, tenant_id, collection) keeps entities scoped on both
        # axes; the relationship is keyed by (predicate, doc_id, tenant_id,
        # collection) so re-ingesting the same doc is idempotent rather than
        # piling up duplicate edges. The evidence properties (#347) are
        # deliberately OUTSIDE the MERGE key — putting them in it would turn a
        # re-extraction with a different quote into a duplicate edge — and are
        # written with ON CREATE SET / ON MATCH SET, so a matched edge takes the
        # latest write's values (last writer wins, same as the in-memory store).
        query = (
            "UNWIND $rows AS row "
            "MERGE (s:Entity {name: row.subject, tenant_id: row.tenant_id, "
            "collection: row.collection}) "
            "MERGE (o:Entity {name: row.object, tenant_id: row.tenant_id, "
            "collection: row.collection}) "
            "MERGE (s)-[r:REL {predicate: row.predicate, doc_id: row.doc_id, "
            "tenant_id: row.tenant_id, collection: row.collection}]->(o) "
            "ON CREATE SET " + _EVIDENCE_SET + " "
            "ON MATCH SET " + _EVIDENCE_SET
        )
        async with self._session() as session:
            await session.run(query, rows=rows)

    def _scope(
        self,
        params: dict[str, Any],
        tenant_id: str | None,
        collection: str | Sequence[str] | None,
    ) -> list[str]:
        """Register the scope parameters and return the per-relationship predicate
        templates (``{alias}`` is the relationship variable) shared by every read.

        Keeping the two axes in one place means a new read path can't accidentally
        apply the tenant filter and forget the collection filter.

        ``collection`` is one physical name (``= $collection``, unchanged) or,
        for the multi-collection graph leg (issue #253), a list of names
        (``IN $collections``). Both are exact property predicates in Cypher —
        this is a graph store, not an HNSW index: the many-valued-filter
        truncation of #199 does not apply here."""
        preds: list[str] = []
        if tenant_id is not None:
            params["tenants"] = readable_tenants(tenant_id)
            preds.append("{alias}.tenant_id IN $tenants")
        if isinstance(collection, str):
            params["collection"] = collection
            # Exact equality, so a legacy triple written before #209 (null/empty
            # ``collection``) matches no real collection — fail closed.
            preds.append("{alias}.collection = $collection")
        elif collection is not None:
            params["collections"] = list(collection)
            preds.append("{alias}.collection IN $collections")
        return preds

    async def query_neighborhood(
        self,
        entity: str,
        depth: int = 1,
        tenant_id: str | None = None,
        collection: str | Sequence[str] | None = None,
    ) -> list[Triple]:
        """Return triples within ``depth`` hops of ``entity``, scoped to the caller's
        readable tenants and to ``collection`` (one name, or a list of names for
        the multi-collection leg — see :meth:`_scope`). Matching is case-insensitive
        substring on the entity name to mirror the in-memory store. The retriever
        no longer passes a raw query here (#349): ``entity`` is one name that
        ``match_entities`` already confirmed, so the substring match only widens
        the walk to names that *contain* that entity.

        ``tenant_id=None`` / ``collection=None`` are deliberate unscoped reads
        (dev/tests/library use, per the module docstring) and return every
        tenant's / every collection's triples. Requests never reach either: the
        API resolves a concrete tenant server-side and the retriever is bound to
        one collection before calling here, and
        :meth:`HybridRetriever._graph_context` re-applies both scopes to whatever
        comes back.

        TODO(M4 Phase 2): fuse this neighbourhood into the hybrid retriever
        (graph-aware reranking / passage expansion) instead of returning raw
        triples; couple with LLM extraction quality work.
        """
        depth = max(1, min(depth, _MAX_DEPTH))
        params: dict[str, Any] = {"entity": entity.lower()}
        preds = self._scope(params, tenant_id, collection)
        # Anchor the traversal inside the collection too: without this a start
        # node in collection y could seed a walk that the path filter then prunes
        # to nothing — correct but wasteful — and, more importantly, entity
        # identity is per-collection so the in-scope node is a different node.
        if isinstance(collection, str):
            start_clause = "AND start.collection = $collection "
        elif collection is not None:
            start_clause = "AND start.collection IN $collections "
        else:
            start_clause = ""
        path_clause = ""
        edge_clause = ""
        if preds:
            # Scope the TRAVERSAL, not just the returned edges: every hop on the
            # path must be readable, so a multi-hop query can't tunnel through
            # another tenant's (or collection's) edge to reach an entity the
            # caller can't otherwise see (a connectivity leak at depth > 1).
            path_clause = (
                "WHERE all(rel IN rels WHERE "
                + " AND ".join(p.format(alias="rel") for p in preds)
                + ") "
            )
            edge_clause = "".join(f"AND {p.format(alias='r')} " for p in preds)
        # Variable-length path 1..depth from any node whose name contains the term
        # (either as subject or object). Collect each relationship's endpoints so we
        # can reconstruct directed (subject, predicate, object) triples.
        query = (
            "MATCH (start:Entity) "
            "WHERE toLower(start.name) CONTAINS $entity " + start_clause +
            f"MATCH (start)-[rels:REL*1..{depth}]-(:Entity) "
            + path_clause +
            "UNWIND rels AS r "
            "WITH DISTINCT r "
            "WHERE true " + edge_clause +
            "MATCH (s:Entity)-[r]->(o:Entity) "
            "RETURN s.name AS subject, r.predicate AS predicate, o.name AS object, "
            "r.doc_id AS doc_id, r.tenant_id AS tenant_id, r.collection AS collection, "
            + _EVIDENCE_RETURN
        )
        return await self._run_triples(query, params)

    async def match_entities(
        self,
        candidates: list[str],
        *,
        tenant_id: str | None,
        collection: str | Sequence[str] | None,
    ) -> list[str]:
        """The candidates that name an entity in scope (#349): one ``IN``
        lookup, exact and case-folded, never ``CONTAINS``. Candidates are folded
        and deduplicated here so ``$candidates`` is as short as possible and the
        returned names are exactly the candidate strings that hit. Scope goes on
        the *node* (``e.tenant_id`` / ``e.collection``) — entity identity is per
        tenant and per collection, so the node's stamps are authoritative."""
        folded: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            name = candidate.lower()
            if name not in seen:
                seen.add(name)
                folded.append(name)
        if not folded:
            return []
        params: dict[str, Any] = {"candidates": folded}
        preds = self._scope(params, tenant_id, collection)
        scope_clause = "".join(f"AND {p.format(alias='e')} " for p in preds)
        query = (
            "MATCH (e:Entity) "
            "WHERE toLower(e.name) IN $candidates " + scope_clause +
            "RETURN DISTINCT toLower(e.name) AS name"
        )
        async with self._session() as session:
            result = await session.run(query, **params)
            records = [record async for record in result]
        hits = {str(rec["name"]) for rec in records}
        return [name for name in folded if name in hits]

    async def list_entities(
        self,
        tenant_id: str | None = None,
        limit: int = 100,
        collection: str | None = None,
    ) -> list[tuple[str, int]]:
        """Distinct entities the caller may read, each with its relationship degree,
        most-connected first."""
        params: dict[str, Any] = {"limit": limit}
        preds = self._scope(params, tenant_id, collection)
        where = (
            "WHERE " + " AND ".join(p.format(alias="r") for p in preds) + " " if preds else ""
        )
        query = (
            "MATCH (e:Entity)-[r:REL]-(:Entity) " + where +
            "WITH e.name AS name, count(r) AS degree "
            "RETURN name, degree ORDER BY degree DESC, name ASC LIMIT $limit"
        )
        async with self._session() as session:
            result = await session.run(query, **params)
            records = [record async for record in result]
        return [(str(rec["name"]), int(rec["degree"])) for rec in records]

    async def stats(
        self, tenant_id: str | None = None, collection: str | None = None
    ) -> tuple[int, int]:
        """Return ``(entities, relationships)`` visible to the caller.

        Both counts are scoped to the caller's readable tenants (own + public) and
        to ``collection``; an unscoped call (``None``, dev/tests) counts everything
        on that axis. The WHERE clauses fail closed on an empty tenant set — Cypher
        ``x IN []`` is false, so no rows match — rather than counting across
        tenants. Entities are counted on the node, which is safe because node
        identity is ``(name, tenant_id, collection)``: a node belongs to exactly
        one collection.
        """
        params: dict[str, Any] = {}
        preds = self._scope(params, tenant_id, collection)
        ent_clause = ""
        rel_clause = ""
        if preds:
            ent_clause = "WHERE " + " AND ".join(p.format(alias="e") for p in preds) + " "
            rel_clause = "WHERE " + " AND ".join(p.format(alias="r") for p in preds) + " "
        # count(e) with no grouping key yields a single row (entities=0 even over
        # zero matches). The relationship side is an OPTIONAL MATCH so that row
        # survives when there are entities but no relationships — a plain MATCH
        # would drop it and wrongly report (0, 0).
        query = (
            "MATCH (e:Entity) " + ent_clause +
            "WITH count(e) AS entities "
            "OPTIONAL MATCH ()-[r:REL]->() " + rel_clause +
            "RETURN entities, count(r) AS relationships"
        )
        async with self._session() as session:
            result = await session.run(query, **params)
            rec = await result.single()
        if rec is None:
            return (0, 0)
        return (int(rec["entities"]), int(rec["relationships"]))

    async def delete_by_doc(
        self,
        doc_id: str,
        tenant_id: str | None = None,
        collection: str | None = None,
    ) -> None:
        """Delete the relationships a document contributed, never crossing tenants
        **or** collections. Orphaned entities (no remaining edges) are removed too.

        The collection clause matters as much as the tenant one: the same
        ``doc_id`` re-ingested into a second collection has its own triples there,
        and the delete-prior step of a re-ingest into collection x would otherwise
        wipe collection y's copy."""
        params: dict[str, Any] = {"doc_id": doc_id}
        scope_clause = ""
        if tenant_id is not None:
            params["tenant_id"] = tenant_id
            scope_clause += "AND r.tenant_id = $tenant_id "
        if collection is not None:
            params["collection"] = collection
            scope_clause += "AND r.collection = $collection "
        # Delete this doc's relationships, then sweep ONLY their endpoint entities
        # if they're now edgeless — scoped to the entities this delete touched, so
        # it never deletes another tenant's nodes and never full-scans the graph.
        query = (
            "MATCH (s:Entity)-[r:REL]->(o:Entity) "
            "WHERE r.doc_id = $doc_id " + scope_clause +
            "WITH collect(DISTINCT s) + collect(DISTINCT o) AS ends, collect(r) AS rels "
            "FOREACH (x IN rels | DELETE x) "
            "WITH ends "
            "UNWIND ends AS e "
            "WITH DISTINCT e WHERE NOT (e)--() "
            "DELETE e"
        )
        async with self._session() as session:
            await session.run(query, **params)

    async def _run_triples(self, query: str, params: dict[str, Any]) -> list[Triple]:
        async with self._session() as session:
            result = await session.run(query, **params)
            records = [record async for record in result]
        return [
            Triple(
                subject=str(rec["subject"]),
                predicate=str(rec["predicate"]),
                object=str(rec["object"]),
                doc_id=str(rec.get("doc_id") or ""),
                tenant_id=str(rec.get("tenant_id") or ""),
                collection=str(rec.get("collection") or ""),
                # Edges written before #347 have no evidence properties (null);
                # they read back as the model defaults — unknown, not invisible.
                # ``confidence`` is repaired (with a warning) so a hand-edited
                # edge can't turn a read into a validation error.
                evidence=str(rec.get("evidence") or ""),
                chunk_id=str(rec.get("chunk_id") or ""),
                derived_by=str(rec.get("derived_by") or ""),
                confidence=_read_confidence(rec),
                subject_id=str(rec.get("subject_id") or ""),
                object_id=str(rec.get("object_id") or ""),
            )
            for rec in records
        ]

    async def close(self) -> None:
        await self._driver.close()
