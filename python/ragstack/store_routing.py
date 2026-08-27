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
"""
from __future__ import annotations

from typing import Any


def qdrant_url_for(collection: str, settings: Any) -> str:
    """The Qdrant base URL serving ``collection``, per ``settings``.

    An alternate instance when the collection is routed via
    ``qdrant_collection_routes`` (its own vm.max_map_count budget — see the config
    field), else the default ``qdrant_url``. Keeps single-instance deployments
    byte-for-byte unchanged (empty routes → always ``qdrant_url``).

    The key is the **physical** collection name, the same one the store is
    created under. There is deliberately no Elasticsearch analogue: ``config.py``
    has only ``qdrant_collection_routes``, so every collection's text index lives
    on ``elasticsearch_url`` and callers pass that setting bare — an asymmetry in
    the config, not in the callers.
    """
    routes = getattr(settings, "qdrant_collection_routes", None) or {}
    return routes.get(collection, settings.qdrant_url)
