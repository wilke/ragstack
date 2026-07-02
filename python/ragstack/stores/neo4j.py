"""Neo4j-backed GraphStore (knowledge graph), tenant-scoped.

Triples become a property graph: ``(:Entity {name, tenant_id})-[:REL {predicate,
doc_id, tenant_id}]->(:Entity {...})``. Entities are keyed by ``(name, tenant_id)``
so the same surface form under two tenants is two distinct nodes — a tenant can
never read or delete across the boundary. Reads filter relationships to the
caller's *readable* tenants (own + the shared ``public`` corpus); an unscoped
read (``tenant_id=None``) is allowed only for dev/tests and sees everything.

The ``neo4j`` driver import is lazy so the optional ``graph`` extra is only
required when this backend is actually selected. LLM-driven triple extraction
quality and graph/vector retriever fusion are deferred to M4 Phase 2 (see the
TODO in ``query_neighborhood``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ragstack.models import Triple
from ragstack.tenancy import DEFAULT_TENANT, readable_tenants

# Upper bound on neighbourhood hops. ``depth`` is interpolated into the Cypher
# variable-length pattern ``REL*1..{depth}``, so an unbounded value would make
# Neo4j enumerate exponentially many paths (a DoS). The API caps it too.
_MAX_DEPTH = 5

if TYPE_CHECKING:  # pragma: no cover - typing only
    from neo4j import AsyncDriver


def _tenant_or_default(tenant_id: str) -> str:
    return tenant_id or DEFAULT_TENANT


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
        MERGE stay fast and ``(name, tenant_id)`` identity is enforced by the DB."""
        async with self._session() as session:
            await session.run(
                "CREATE CONSTRAINT entity_name_tenant IF NOT EXISTS "
                "FOR (e:Entity) REQUIRE (e.name, e.tenant_id) IS UNIQUE"
            )

    def _session(self) -> Any:
        if self._database:
            return self._driver.session(database=self._database)
        return self._driver.session()

    async def add_triples(self, triples: list[Triple]) -> None:
        """Upsert triples. Each triple's ``tenant_id`` is stamped onto both endpoint
        nodes and the relationship, so isolation is a property of the stored data
        rather than something the read path has to reconstruct."""
        if not triples:
            return
        rows = [
            {
                "subject": t.subject,
                "predicate": t.predicate,
                "object": t.object,
                "doc_id": t.doc_id,
                "tenant_id": _tenant_or_default(t.tenant_id),
            }
            for t in triples
        ]
        # MERGE on (name, tenant_id) keeps entities tenant-scoped; the relationship
        # is keyed by (predicate, doc_id, tenant_id) so re-ingesting the same doc
        # is idempotent rather than piling up duplicate edges.
        query = (
            "UNWIND $rows AS row "
            "MERGE (s:Entity {name: row.subject, tenant_id: row.tenant_id}) "
            "MERGE (o:Entity {name: row.object, tenant_id: row.tenant_id}) "
            "MERGE (s)-[r:REL {predicate: row.predicate, doc_id: row.doc_id, "
            "tenant_id: row.tenant_id}]->(o)"
        )
        async with self._session() as session:
            await session.run(query, rows=rows)

    async def query_neighborhood(
        self, entity: str, depth: int = 1, tenant_id: str | None = None
    ) -> list[Triple]:
        """Return triples within ``depth`` hops of ``entity``, scoped to the caller's
        readable tenants. Matching is case-insensitive substring on the entity name
        to mirror the in-memory store.

        TODO(M4 Phase 2): fuse this neighbourhood into the hybrid retriever
        (graph-aware reranking / passage expansion) instead of returning raw
        triples; couple with LLM extraction quality work.
        """
        depth = max(1, min(depth, _MAX_DEPTH))
        params: dict[str, Any] = {"entity": entity.lower()}
        tenant_clause = ""
        path_clause = ""
        if tenant_id is not None:
            params["tenants"] = readable_tenants(tenant_id)
            tenant_clause = "AND r.tenant_id IN $tenants "
            # Scope the TRAVERSAL, not just the returned edges: every hop on the
            # path must be readable, so a multi-hop query can't tunnel through
            # another tenant's edge to reach an entity the caller can't otherwise
            # see (a connectivity leak at depth > 1).
            path_clause = "WHERE all(rel IN rels WHERE rel.tenant_id IN $tenants) "
        # Variable-length path 1..depth from any node whose name contains the term
        # (either as subject or object). Collect each relationship's endpoints so we
        # can reconstruct directed (subject, predicate, object) triples.
        query = (
            "MATCH (start:Entity) "
            "WHERE toLower(start.name) CONTAINS $entity "
            f"MATCH (start)-[rels:REL*1..{depth}]-(:Entity) "
            + path_clause +
            "UNWIND rels AS r "
            "WITH DISTINCT r "
            "WHERE true " + tenant_clause +
            "MATCH (s:Entity)-[r]->(o:Entity) "
            "RETURN s.name AS subject, r.predicate AS predicate, o.name AS object, "
            "r.doc_id AS doc_id, r.tenant_id AS tenant_id"
        )
        return await self._run_triples(query, params)

    async def list_entities(
        self, tenant_id: str | None = None, limit: int = 100
    ) -> list[tuple[str, int]]:
        """Distinct entities the caller may read, each with its relationship degree,
        most-connected first."""
        params: dict[str, Any] = {"limit": limit}
        tenant_clause = ""
        if tenant_id is not None:
            params["tenants"] = readable_tenants(tenant_id)
            tenant_clause = "WHERE r.tenant_id IN $tenants "
        query = (
            "MATCH (e:Entity)-[r:REL]-(:Entity) " + tenant_clause +
            "WITH e.name AS name, count(r) AS degree "
            "RETURN name, degree ORDER BY degree DESC, name ASC LIMIT $limit"
        )
        async with self._session() as session:
            result = await session.run(query, **params)
            records = [record async for record in result]
        return [(str(rec["name"]), int(rec["degree"])) for rec in records]

    async def stats(self, tenant_id: str | None = None) -> tuple[int, int]:
        """Return ``(entities, relationships)`` visible to the caller.

        Both counts are scoped to the caller's readable tenants (own + public);
        an unscoped call (``tenant_id=None``, dev/tests) counts everything. The
        WHERE clauses fail closed on an empty tenant set — Cypher ``x IN []`` is
        false, so no rows match — rather than counting across tenants.
        """
        params: dict[str, Any] = {}
        ent_clause = ""
        rel_clause = ""
        if tenant_id is not None:
            params["tenants"] = readable_tenants(tenant_id)
            ent_clause = "WHERE e.tenant_id IN $tenants "
            rel_clause = "WHERE r.tenant_id IN $tenants "
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

    async def delete_by_doc(self, doc_id: str, tenant_id: str | None = None) -> None:
        """Delete the relationships a document contributed, never crossing tenants.
        Orphaned entities (no remaining edges) are removed too."""
        params: dict[str, Any] = {"doc_id": doc_id}
        tenant_clause = ""
        if tenant_id is not None:
            params["tenant_id"] = tenant_id
            tenant_clause = "AND r.tenant_id = $tenant_id "
        # Delete this doc's relationships, then sweep ONLY their endpoint entities
        # if they're now edgeless — scoped to the entities this delete touched, so
        # it never deletes another tenant's nodes and never full-scans the graph.
        query = (
            "MATCH (s:Entity)-[r:REL]->(o:Entity) "
            "WHERE r.doc_id = $doc_id " + tenant_clause +
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
            )
            for rec in records
        ]

    async def close(self) -> None:
        await self._driver.close()
