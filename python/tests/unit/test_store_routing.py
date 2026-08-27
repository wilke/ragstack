"""``ragstack.store_routing`` — the one place that answers "which instance serves
this collection".

It exists because the answer was copied (#407): the ingest submission the API
built carried no store URL at all, so the CWL's own default — production on the
deployment host — decided, and a dev-tenant ingest wrote to production. Hoisting
the routing out of ``api.deps`` lets the ingest router seed the SAME url the
API's own store construction uses, without importing the API's dependency graph.
"""
from __future__ import annotations

import pathlib

import pytest

from ragstack.api import deps
from ragstack.config import settings
from ragstack.store_routing import qdrant_url_for


@pytest.fixture(autouse=True)
def _pinned(monkeypatch):
    monkeypatch.setattr(settings, "qdrant_url", "http://127.0.0.1:1/default")
    monkeypatch.setattr(settings, "qdrant_collection_routes", {})


def test_unrouted_collection_uses_qdrant_url():
    assert qdrant_url_for("anything", settings) == "http://127.0.0.1:1/default"


def test_routed_collection_uses_its_instance(monkeypatch):
    monkeypatch.setattr(settings, "qdrant_collection_routes",
                        {"routed_phys": "http://127.0.0.1:1/routed"})
    assert qdrant_url_for("routed_phys", settings) == "http://127.0.0.1:1/routed"
    # A route for one collection must not capture its neighbours.
    assert qdrant_url_for("other_phys", settings) == "http://127.0.0.1:1/default"


def test_deps_helper_is_the_same_function(monkeypatch):
    """``api.deps._qdrant_url_for`` is a delegate, not a second copy — the
    duplication is what the hoist removes, so a divergence between the URL the
    API serves from and the URL it seeds into an ingest is unrepresentable."""
    monkeypatch.setattr(settings, "qdrant_collection_routes",
                        {"routed_phys": "http://127.0.0.1:1/routed"})
    for collection in ("routed_phys", "other_phys"):
        assert deps._qdrant_url_for(collection) == qdrant_url_for(collection, settings)


def test_the_module_does_not_import_the_api():
    """The point of the hoist: ``ragstack.api.routers.documents`` can seed store
    URLs without ``api.deps`` (which builds embedders, stores and retrievers)
    being dragged in — and ``deps`` importing the router would be backwards."""
    import ast

    import ragstack.store_routing as mod

    src = mod.__file__
    assert src is not None
    tree = ast.parse(pathlib.Path(src).read_text(encoding="utf-8"))
    imported = {
        n.module or ""
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
    } | {
        alias.name
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for alias in n.names
    }
    assert imported == {"__future__", "typing"}, imported


def test_the_ops_inventory_still_audits_another_deployments_config(monkeypatch):
    """The routing must follow the settings object it is HANDED, never an
    ambient one.

    ``ops.store_inventory`` audits other deployments by swapping ``deps.settings``
    for a settings object built from their env file and calling the serving code
    (``_patched_settings``). A hoisted helper that closed over its own
    module-level ``settings`` import would silently answer for THIS process — an
    inventory that reports production's stores while claiming to describe a
    tenant's is the same wrong-default failure as #407 itself. This caught a real
    regression in the hoist; it is here so the next one is caught too."""
    from ragstack.ops import store_inventory as si

    monkeypatch.setattr(settings, "qdrant_url", "http://127.0.0.1:1/THIS-PROCESS")
    other = si.settings_from_env({
        "QDRANT_URL": "http://127.0.0.1:2/other-deployment",
        "QDRANT_COLLECTION_ROUTES": '{"routed_phys": "http://127.0.0.1:3/other-routed"}',
        "ELASTICSEARCH_URL": "http://127.0.0.1:2",
    })[0]
    with si._patched_settings(other) as patched:
        assert patched._qdrant_url_for("routed_phys") == "http://127.0.0.1:3/other-routed"
        assert patched._qdrant_url_for("plain_phys") == "http://127.0.0.1:2/other-deployment"
    # …and the swap is undone: this process answers for itself again.
    assert deps._qdrant_url_for("plain_phys") == "http://127.0.0.1:1/THIS-PROCESS"
