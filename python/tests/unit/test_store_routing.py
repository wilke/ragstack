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
from ragstack.store_routing import es_url_for, qdrant_url_for


@pytest.fixture(autouse=True)
def _pinned(monkeypatch):
    monkeypatch.setattr(settings, "qdrant_url", "http://127.0.0.1:1/default")
    monkeypatch.setattr(settings, "qdrant_collection_routes", {})
    monkeypatch.setattr(settings, "elasticsearch_url", "http://127.0.0.1:2/default")
    monkeypatch.setattr(settings, "es_collection_routes", {})


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


# --------------------------------------------------------------------------- #
# the text leg: es_url_for, the twin of qdrant_url_for
# --------------------------------------------------------------------------- #


def test_es_empty_routes_is_byte_for_byte_the_bare_setting():
    """The whole compatibility promise: a deployment that sets no routes gets
    `elasticsearch_url`, unchanged, for every index — including one whose name
    happens to match a Qdrant route."""
    assert es_url_for("anything", settings) == "http://127.0.0.1:2/default"
    assert es_url_for("", settings) == "http://127.0.0.1:2/default"


def test_es_routed_index_uses_its_cluster(monkeypatch):
    monkeypatch.setattr(settings, "es_collection_routes",
                        {"routed_idx": "http://127.0.0.1:2/routed"})
    assert es_url_for("routed_idx", settings) == "http://127.0.0.1:2/routed"


def test_es_unrouted_index_is_not_captured_by_a_neighbours_route(monkeypatch):
    monkeypatch.setattr(settings, "es_collection_routes",
                        {"routed_idx": "http://127.0.0.1:2/routed"})
    assert es_url_for("other_idx", settings) == "http://127.0.0.1:2/default"


def test_es_key_is_the_physical_index_not_the_collection_id(monkeypatch):
    """The deliberate asymmetry with the Qdrant table.

    A spec's id, its Qdrant collection and its ES index are three different
    strings. `es_collection_routes` is keyed by the LAST of them, so a table that
    names the id or the collection routes nothing — which is the safe direction
    (the default cluster, i.e. today's behaviour) rather than a half-routed
    corpus."""
    from ragstack.collection_store import CollectionSpec

    spec = CollectionSpec(
        id="sfr-512",
        collection="ragstack_sfr_tok512",
        text_index="shared_text_v1",
        embedding_model="m",
        embedding_model_dim=8,
    )
    assert (spec.id, spec.collection, spec.es_index()) == (
        "sfr-512", "ragstack_sfr_tok512", "shared_text_v1"
    )
    monkeypatch.setattr(settings, "es_collection_routes",
                        {"shared_text_v1": "http://127.0.0.1:2/routed"})
    assert es_url_for(spec.es_index(), settings) == "http://127.0.0.1:2/routed"
    # …and neither of the other two names is a key.
    assert es_url_for(spec.id, settings) == "http://127.0.0.1:2/default"
    assert es_url_for(spec.collection, settings) == "http://127.0.0.1:2/default"


def test_es_routes_are_independent_of_qdrant_routes(monkeypatch):
    """One leg routed, the other not, is the expected shape — the vector leg's
    reason (a per-process VMA budget) has no ES counterpart."""
    monkeypatch.setattr(settings, "qdrant_collection_routes",
                        {"phys": "http://127.0.0.1:1/routed"})
    assert qdrant_url_for("phys", settings) == "http://127.0.0.1:1/routed"
    assert es_url_for("phys", settings) == "http://127.0.0.1:2/default"


def test_es_deps_helper_is_the_same_function(monkeypatch):
    """`api.deps._es_url_for` is a delegate, not a second copy — see the Qdrant
    test above; a divergence between the cluster the API queries and the one it
    seeds into an ingest is #407 on the text leg."""
    monkeypatch.setattr(settings, "es_collection_routes",
                        {"routed_idx": "http://127.0.0.1:2/routed"})
    for index in ("routed_idx", "other_idx"):
        assert deps._es_url_for(index) == es_url_for(index, settings)


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
    monkeypatch.setattr(settings, "elasticsearch_url", "http://127.0.0.1:1/THIS-ES")
    other = si.settings_from_env({
        "QDRANT_URL": "http://127.0.0.1:2/other-deployment",
        "QDRANT_COLLECTION_ROUTES": '{"routed_phys": "http://127.0.0.1:3/other-routed"}',
        "ELASTICSEARCH_URL": "http://127.0.0.1:2/other-es",
        "ES_COLLECTION_ROUTES": '{"routed_idx": "http://127.0.0.1:4/other-routed-es"}',
    })[0]
    with si._patched_settings(other) as patched:
        assert patched._qdrant_url_for("routed_phys") == "http://127.0.0.1:3/other-routed"
        assert patched._qdrant_url_for("plain_phys") == "http://127.0.0.1:2/other-deployment"
        # The text leg too — and this also pins that ES_COLLECTION_ROUTES survives
        # the env round-trip as a dict (pydantic-settings JSON-decodes complex
        # fields only through the ENV source, never through init kwargs; see
        # settings_from_env's "Fidelity" note).
        assert patched._es_url_for("routed_idx") == "http://127.0.0.1:4/other-routed-es"
        assert patched._es_url_for("plain_idx") == "http://127.0.0.1:2/other-es"
    # …and the swap is undone: this process answers for itself again.
    assert deps._qdrant_url_for("plain_phys") == "http://127.0.0.1:1/THIS-PROCESS"
    assert deps._es_url_for("plain_idx") == "http://127.0.0.1:1/THIS-ES"


# --------------------------------------------------------------------------- #
# the operator override (--qdrant-url / --es-url) and the ingest-target caller
# --------------------------------------------------------------------------- #


def test_route_beats_the_override_and_the_override_beats_the_setting(monkeypatch):
    """The precedence a bulk writer relies on: a ROUTED store already lives on
    its own instance, so an explicit URL must not redirect it (that builds a
    second, invisible copy — #407); an UNROUTED one goes where the operator
    said; with no override the setting decides, exactly as before."""
    monkeypatch.setattr(settings, "qdrant_collection_routes",
                        {"routed_phys": "http://127.0.0.1:1/routed"})
    monkeypatch.setattr(settings, "es_collection_routes",
                        {"routed_idx": "http://127.0.0.1:2/routed"})
    cli_q, cli_e = "http://127.0.0.1:3/cli", "http://127.0.0.1:4/cli"
    assert qdrant_url_for("routed_phys", settings, override=cli_q) == "http://127.0.0.1:1/routed"
    assert qdrant_url_for("plain_phys", settings, override=cli_q) == cli_q
    assert qdrant_url_for("plain_phys", settings, override="") == "http://127.0.0.1:1/default"
    assert es_url_for("routed_idx", settings, override=cli_e) == "http://127.0.0.1:2/routed"
    assert es_url_for("plain_idx", settings, override=cli_e) == cli_e
    assert es_url_for("plain_idx", settings, override="") == "http://127.0.0.1:2/default"


def test_an_empty_route_still_beats_the_override(monkeypatch):
    """A present-but-empty route is a configuration error that
    ``ingest_target.require_store_urls`` reports by name; the resolver must hand
    it the empty route, not quietly substitute the operator's URL."""
    monkeypatch.setattr(settings, "qdrant_collection_routes", {"routed_phys": ""})
    monkeypatch.setattr(settings, "es_collection_routes", {"routed_idx": ""})
    assert qdrant_url_for("routed_phys", settings, override="http://127.0.0.1:3/cli") == ""
    assert es_url_for("routed_idx", settings, override="http://127.0.0.1:4/cli") == ""


def test_the_ingest_target_resolves_through_store_routing(monkeypatch):
    """``ops.ingest_target`` used to carry its own copy of both resolvers with
    the override layered on; a copy is the #407 bug class. Every combination of
    routed/unrouted × override/none must agree with ``store_routing`` — and the
    private copies must be gone, so nothing can drift back onto them."""
    from ragstack.collection_store import CollectionSpec
    from ragstack.ops import ingest_target as it

    assert not hasattr(it, "_qdrant_url_for")
    assert not hasattr(it, "_es_url_for")
    monkeypatch.setattr(settings, "qdrant_collection_routes",
                        {"routed_phys": "http://127.0.0.1:1/routed"})
    monkeypatch.setattr(settings, "es_collection_routes",
                        {"routed_idx": "http://127.0.0.1:2/routed"})
    for collection, index in (("routed_phys", "routed_idx"), ("plain_phys", "plain_idx")):
        spec = CollectionSpec(
            id="c", collection=collection, text_index=index, embedding_model="m",
            embedding_model_dim=4, chunk_method="fixed_token", chunk_size=256,
            chunk_overlap=32,
        )
        for q_over, e_over in (("", ""), ("http://127.0.0.1:3/cli", "http://127.0.0.1:4/cli")):
            t = it.target_from_spec(spec, settings, qdrant_url=q_over, es_url=e_over)
            assert t.qdrant_url == qdrant_url_for(collection, settings, override=q_over)
            assert t.es_url == es_url_for(index, settings, override=e_over)
