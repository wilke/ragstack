"""/v1/graph/stats — tenant-scoped knowledge-graph counts.

entities/relationships reflect only the caller's readable tenants (own + public);
another tenant's triples never contribute. When no graph store is configured the
endpoint degrades to available=false with null counts (no 500).
"""
import pytest

from ragstack.api import security
from ragstack.api.main import app
from ragstack.api.security import ROLE_USER
from ragstack.models import Triple

pytestmark = pytest.mark.asyncio


def _configure_keys(monkeypatch) -> None:
    monkeypatch.setattr(security.settings, "api_keys", ["k-acme", "k-other"])
    monkeypatch.setattr(
        security.settings, "api_key_tenants", {"k-acme": "acme", "k-other": "other"}
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


async def _seed() -> None:
    await app.state.graph_store.add_triples([
        Triple(subject="A", predicate="rel", object="B", doc_id="d1", tenant_id="acme"),
        Triple(subject="B", predicate="rel", object="C", doc_id="d1", tenant_id="acme"),
        Triple(subject="P", predicate="rel", object="Q", doc_id="d2", tenant_id="public"),
        Triple(subject="X", predicate="rel", object="Y", doc_id="d3", tenant_id="other"),
    ])


async def test_scoped_counts_exclude_other_tenant(client, monkeypatch):
    _configure_keys(monkeypatch)
    await _seed()

    body = (await client.get("/v1/graph/stats", headers={"X-API-Key": "k-acme"})).json()
    # acme (2 rels: A,B,C) + public (1 rel: P,Q). other's X-Y excluded.
    assert body["available"] is True
    assert body["relationships"] == 3
    assert body["entities"] == 5  # {A, B, C, P, Q}


async def test_no_cross_tenant_leak(client, monkeypatch):
    _configure_keys(monkeypatch)
    await _seed()

    body = (await client.get("/v1/graph/stats", headers={"X-API-Key": "k-other"})).json()
    # other (1 rel: X,Y) + public (1 rel: P,Q); acme's entities never counted.
    assert body["relationships"] == 2
    assert body["entities"] == 4  # {X, Y, P, Q}


async def test_no_graph_store_degrades_gracefully(client, monkeypatch):
    _configure_keys(monkeypatch)
    monkeypatch.setattr(app.state, "graph_store", None)

    resp = await client.get("/v1/graph/stats", headers={"X-API-Key": "k-acme"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["entities"] is None and body["relationships"] is None


async def test_missing_key_is_401_when_keys_configured(client, monkeypatch):
    _configure_keys(monkeypatch)
    assert (await client.get("/v1/graph/stats")).status_code == 401
