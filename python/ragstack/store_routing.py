"""Which physical store instance serves a given collection.

A pure function of a settings object — no I/O, no module-level singleton, no
import of ``ragstack.api`` — so every caller that must name a store target (the
API's own store construction, the GoWe ingest submission it builds, the ops
inventory) resolves it through **one** implementation instead of a copy.

Duplicating this is how a write lands on the wrong instance (#407): the copy that
forgets ``qdrant_collection_routes`` builds a second, invisible store for a
collection that already lives elsewhere. The settings object is passed in rather
than read from the module, because ``ops.store_inventory`` audits *other*
deployments' configs by swapping the settings it hands the serving code — a
function that reached for the ambient singleton would report this process's
stores while claiming to describe theirs.

Both legs are routable, and the two tables are keyed differently **on purpose**:
``qdrant_collection_routes`` by the physical Qdrant collection name,
``es_collection_routes`` by the physical Elasticsearch index name. Each key is
the store the leg actually addresses, which for the text leg is a collection's
``text_index``/``es_index()`` and not its id — see the config fields.
"""
from __future__ import annotations

from typing import Any


def qdrant_url_for(collection: str, settings: Any, *, override: str = "") -> str:
    """The Qdrant base URL serving ``collection``, per ``settings``.

    An alternate instance when the collection is routed via
    ``qdrant_collection_routes`` (its own vm.max_map_count budget — see the config
    field), else ``override`` when given, else the default ``qdrant_url``. Keeps
    single-instance deployments byte-for-byte unchanged (empty routes, no
    override → always ``qdrant_url``).

    ``override`` is an operator's explicit instance (a bulk writer's
    ``--qdrant-url``). A **route wins over it**: a routed collection already
    lives on its own instance, and writing it to the one the operator named
    would build a second, invisible copy of a store that exists elsewhere. For
    an unrouted collection the operator naming an instance is the more specific
    statement, so it beats the ambient setting.
    ``override`` is only for operator CLI values on the bulk-write path, which
    must also call ``ingest_target.require_store_urls``; API code must never
    pass request-derived text.

    The key is the **physical** collection name, the same one the store is
    created under — never the registry id.
    """
    routes = getattr(settings, "qdrant_collection_routes", None) or {}
    if collection in routes:
        return routes[collection]
    return override or settings.qdrant_url


def es_url_for(index: str, settings: Any, *, override: str = "") -> str:
    """The Elasticsearch base URL serving ``index``, per ``settings``.

    The text leg's twin of :func:`qdrant_url_for`: an alternate cluster when the
    index is routed via ``es_collection_routes``, else ``override`` when given,
    else the default ``elasticsearch_url``. Same guarantee — empty routes, no
    override → always ``elasticsearch_url``, so a single-instance deployment is
    byte-for-byte unchanged.

    ``override`` (a bulk writer's ``--es-url``) loses to a route for the same
    reason as on the vector leg: writing a routed index to the cluster the
    operator named would build a second, invisible half of a corpus whose
    vectors are elsewhere, and leave the API reading the routed index it never
    filled.
    ``override`` is only for operator CLI values on the bulk-write path, which
    must also call ``ingest_target.require_store_urls``; API code must never
    pass request-derived text.

    The key is the **physical index name** — what a collection's
    ``text_index``/``es_index()`` resolves to, which is not its id. Keying on the
    id would route one of several ids aliased onto one index and leave the rest
    pointing at the default cluster, splitting one corpus across two.
    """
    routes = getattr(settings, "es_collection_routes", None) or {}
    if index in routes:
        return routes[index]
    return override or settings.elasticsearch_url
