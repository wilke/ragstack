"""API-level coverage for issue #87: per-principal rate limits + request bounds
on the write endpoints (POST /v1/ingest, POST /v1/ingest/upload,
POST /v1/collections, POST /v1/collections/{id}/shares) plus the shape bounds
(top_k, GET /v1/chunks `ids`, list `limit`, JSON body size) that apply more
broadly. See tests/unit/test_ratelimit.py for the limiter's own refill/LRU
math; this file exercises it wired into the API (dependency wiring, 429
Retry-After, 413/422, per-tenant isolation, and the admin bucket exemption).
"""
from __future__ import annotations

import pytest

from ragstack.api import security
from ragstack.api.security import ROLE_ADMIN, ROLE_USER
from ragstack.config import settings

pytestmark = pytest.mark.asyncio

KEYS = {"alice": "k-alice", "bob": "k-bob", "admin": "k-admin"}


@pytest.fixture
def _two_principals(monkeypatch):
    """Two ordinary, keyed callers (alice/bob) plus an admin key — for tests
    that need the bucket to be genuinely per-tenant rather than relying on the
    single keyless default principal."""
    monkeypatch.setattr(security.settings, "api_keys", list(KEYS.values()))
    monkeypatch.setattr(
        security.settings,
        "api_key_tenants",
        {"k-alice": "alice", "k-bob": "bob", "k-admin": "admin"},
    )
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


def _h(who: str) -> dict:
    return {"X-API-Key": KEYS[who]}


# --- POST /v1/ingest + /v1/ingest/upload: shared "ingest" bucket ----------- #


async def test_11th_ingest_in_an_hour_is_429_with_retry_after(client):
    for _ in range(settings.rate_limit_ingest_per_hour):
        r = await client.post("/v1/ingest", json={"source": "/tmp/does-not-matter.txt"})
        assert r.status_code == 200, r.text

    r = await client.post("/v1/ingest", json={"source": "/tmp/does-not-matter.txt"})
    assert r.status_code == 429, r.text
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) >= 1


async def test_ingest_and_upload_share_one_bucket(client, tmp_path):
    """The spec gives ingest/upload ONE shared hourly rate, not one each: N
    JSON ingests followed by an upload must see the SAME budget, not a fresh
    one."""
    pdf = tmp_path / "f.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%mock")

    for _ in range(settings.rate_limit_ingest_per_hour):
        r = await client.post("/v1/ingest", json={"source": "/tmp/does-not-matter.txt"})
        assert r.status_code == 200, r.text

    with pdf.open("rb") as fh:
        r = await client.post(
            "/v1/ingest/upload", files={"files": ("f.pdf", fh, "application/pdf")}
        )
    assert r.status_code == 429, r.text
    assert "Retry-After" in r.headers


async def test_rate_limit_is_per_principal_not_global(client, _two_principals):
    for _ in range(settings.rate_limit_ingest_per_hour):
        r = await client.post(
            "/v1/ingest", json={"source": "/tmp/x.txt"}, headers=_h("alice")
        )
        assert r.status_code == 200, r.text
    # Alice is spent...
    r = await client.post("/v1/ingest", json={"source": "/tmp/x.txt"}, headers=_h("alice"))
    assert r.status_code == 429, r.text
    # ...but bob has his own untouched bucket.
    r = await client.post("/v1/ingest", json={"source": "/tmp/x.txt"}, headers=_h("bob"))
    assert r.status_code == 200, r.text


async def test_admin_is_exempt_from_the_bucket_but_not_from_bounds(
    client, _two_principals, caplog
):
    # Exhaust the bucket as a plain user first...
    for _ in range(settings.rate_limit_ingest_per_hour + 3):
        await client.post("/v1/ingest", json={"source": "/tmp/x.txt"}, headers=_h("alice"))
    r = await client.post("/v1/ingest", json={"source": "/tmp/x.txt"}, headers=_h("alice"))
    assert r.status_code == 429, r.text

    # ...admin sails through the SAME number of calls (its own tenant, and
    # exempt from the bucket regardless) — and each bypass is logged, per spec.
    with caplog.at_level("INFO", logger="ragstack.api.deps"):
        for _ in range(settings.rate_limit_ingest_per_hour + 3):
            r = await client.post(
                "/v1/ingest", json={"source": "/tmp/x.txt"}, headers=_h("admin")
            )
            assert r.status_code == 200, r.text
    bypass_logs = [
        rec.getMessage() for rec in caplog.records if "bypassed for admin" in rec.getMessage()
    ]
    assert len(bypass_logs) == settings.rate_limit_ingest_per_hour + 3
    assert "bucket='ingest'" in bypass_logs[0]
    assert "tenant='admin'" in bypass_logs[0]

    # But admin is NOT exempt from the request bounds (413 body cap).
    oversized = "a" * (settings.max_json_body_bytes + 1)
    r = await client.post(
        "/v1/ingest",
        json={"source": "/tmp/x.txt", "metadata": {"pad": oversized}},
        headers=_h("admin"),
    )
    assert r.status_code == 413, r.text


# --- POST /v1/collections: "collections_create" bucket --------------------- #


async def test_6th_collection_create_in_an_hour_is_429(client):
    for i in range(settings.rate_limit_collections_create_per_hour):
        r = await client.post("/v1/collections", json={"id": f"c-{i}"})
        assert r.status_code in (201, 403), r.text  # 403 if MAX_COLLECTIONS is tight in env
    r = await client.post(
        "/v1/collections", json={"id": "one-too-many"}
    )
    assert r.status_code == 429, r.text
    assert "Retry-After" in r.headers


# --- POST /v1/collections/{id}/shares: "shares" bucket ---------------------- #


async def test_shares_bucket_429s_once_spent(client, _two_principals, monkeypatch):
    """Alice creates and therefore owns a collection (owner-or-admin gates the
    grant), then trips her OWN shares bucket granting on it. A tight bucket
    (monkeypatched, then the limiter rebuilt to pick it up — settings alone
    don't reach an already-built TokenBucketLimiter) keeps this fast without 60
    real grants."""
    from ragstack.api.deps import build_rate_limiters
    from ragstack.api.main import app

    r = await client.post("/v1/collections", json={"id": "alice-coll"}, headers=_h("alice"))
    assert r.status_code == 201, r.text

    monkeypatch.setattr(settings, "rate_limit_shares_per_hour", 2)
    app.state.rate_limiters = build_rate_limiters()

    for i in range(2):
        r = await client.post(
            "/v1/collections/alice-coll/shares",
            json={"grantee": f"bvbrc:user-{i}"},
            headers=_h("alice"),
        )
        assert r.status_code == 201, r.text
    r = await client.post(
        "/v1/collections/alice-coll/shares",
        json={"grantee": "bvbrc:one-more"},
        headers=_h("alice"),
    )
    assert r.status_code == 429, r.text
    assert "Retry-After" in r.headers


# --- Request bounds: top_k, ids, list limit, body size ---------------------- #


@pytest.mark.parametrize("path", ["/v1/query", "/v1/retrieve"])
async def test_top_k_over_max_is_422(client, path):
    resp = await client.post(
        path, json={"query": "q", "top_k": settings.max_top_k + 1}
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("path", ["/v1/query", "/v1/retrieve"])
async def test_top_k_at_max_is_accepted(client, path):
    resp = await client.post(path, json={"query": "q", "top_k": settings.max_top_k})
    assert resp.status_code == 200, resp.text


async def test_chunk_ids_over_max_is_422(client):
    ids = ",".join(f"c{i}" for i in range(settings.max_chunk_ids + 1))
    resp = await client.get("/v1/chunks", params={"ids": ids})
    assert resp.status_code == 422, resp.text


async def test_chunk_ids_at_max_is_accepted(client):
    ids = ",".join(f"c{i}" for i in range(settings.max_chunk_ids))
    resp = await client.get("/v1/chunks", params={"ids": ids})
    assert resp.status_code == 200, resp.text


async def test_list_documents_limit_over_max_is_422(client):
    resp = await client.get(
        "/v1/documents", params={"limit": settings.max_list_limit + 1}
    )
    assert resp.status_code == 422, resp.text


async def test_graph_entities_limit_over_max_is_422(client):
    resp = await client.get(
        "/v1/graph/entities", params={"limit": settings.max_list_limit + 1}
    )
    assert resp.status_code == 422, resp.text


async def test_oversized_json_body_on_ingest_is_413(client):
    oversized = "a" * (settings.max_json_body_bytes + 1)
    resp = await client.post(
        "/v1/ingest", json={"source": "/tmp/x.txt", "metadata": {"pad": oversized}}
    )
    assert resp.status_code == 413, resp.text


async def test_oversized_json_body_on_collection_create_is_413(client):
    oversized = "a" * (settings.max_json_body_bytes + 1)
    resp = await client.post(
        "/v1/collections", json={"label": oversized}
    )
    assert resp.status_code == 413, resp.text


async def test_body_at_the_limit_is_not_rejected_by_size(client):
    # Just under the cap: must fail (if at all) for an ordinary domain reason,
    # never 413.
    padding = "a" * (settings.max_json_body_bytes - 1000)
    resp = await client.post(
        "/v1/ingest", json={"source": "/tmp/x.txt", "metadata": {"pad": padding}}
    )
    assert resp.status_code != 413, resp.text
