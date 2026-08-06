"""Auth, CORS, and production-settings security tests."""
import logging

import pytest

from ragstack.api import deps, security


@pytest.mark.asyncio
async def test_v1_open_when_no_keys_configured(client):
    # Default: no api_keys -> the data API is open (dev/tests).
    assert (await client.get("/v1/documents")).status_code == 200


@pytest.mark.asyncio
async def test_v1_requires_key_when_configured(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", ["s3cret"])

    # Missing key -> 401.
    assert (await client.get("/v1/documents")).status_code == 401
    # Wrong key -> 401.
    bad = await client.get("/v1/documents", headers={"X-API-Key": "nope"})
    assert bad.status_code == 401
    # Valid key -> 200.
    ok = await client.get("/v1/documents", headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_health_stays_open_with_keys_configured(client, monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", ["s3cret"])
    assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_cors_wildcard_does_not_allow_credentials(client):
    # The app is built with the default allowed_origins=["*"], which must not be
    # combined with credentialed CORS.
    resp = await client.get("/health", headers={"Origin": "http://evil.test"})
    assert resp.headers.get("access-control-allow-credentials") != "true"


def test_production_requires_api_keys_and_ingest_root(monkeypatch):
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    monkeypatch.setattr(deps.settings, "api_keys", [])
    monkeypatch.setattr(deps.settings, "ingest_root", "")
    with pytest.raises(RuntimeError) as exc:
        deps._validate_production_settings()
    assert "api_keys" in str(exc.value)
    assert "ingest_root" in str(exc.value)


def test_production_settings_pass_when_set(monkeypatch, tmp_path):
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    monkeypatch.setattr(deps.settings, "api_keys", ["k"])
    monkeypatch.setattr(deps.settings, "ingest_root", str(tmp_path))
    # The ACL database (users + shares) must be durable in production too (#243).
    monkeypatch.setattr(deps.settings, "user_store_backend", "sqlite")
    # Absolute: a relative sqlite path is refused under
    # require_durable_backends (two servers in one CWD would share it).
    monkeypatch.setattr(deps.settings, "user_store_path", "/tmp/rs-test-users.db")
    deps._validate_production_settings()  # must not raise


def test_production_requires_durable_acl_store(monkeypatch, tmp_path):
    # Everything else set, but an in-memory ACL store would lose every owner row
    # and share on restart — refuse to boot (#243).
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    monkeypatch.setattr(deps.settings, "api_keys", ["k"])
    monkeypatch.setattr(deps.settings, "ingest_root", str(tmp_path))
    monkeypatch.setattr(deps.settings, "user_store_backend", "memory")
    with pytest.raises(RuntimeError, match="user_store_backend"):
        deps._validate_production_settings()


def test_dev_skips_production_validation(monkeypatch):
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    monkeypatch.setattr(deps.settings, "api_keys", [])
    monkeypatch.setattr(deps.settings, "ingest_root", "")
    deps._validate_production_settings()  # must not raise


# --- ingest_root: request-time gate + boot-time shape check ------------------ #


@pytest.mark.asyncio
async def test_ingest_disabled_without_ingest_root(client, monkeypatch):
    """The real exposure: with no ingest_root, POST /v1/ingest must not run.

    This is the keyless shape that actually runs on the fleet (DEFAULT_ROLE=admin,
    no API_KEYS), where an accepted job recursively reads the server filesystem
    and makes it retrievable. The gate has to be on the request; a boot-time
    policy keyed off api_keys lets exactly this configuration sail through.
    """
    monkeypatch.setattr(deps.settings, "ingest_root", "")
    monkeypatch.setattr(security.settings, "api_keys", [])  # keyless, wide open

    resp = await client.post("/v1/ingest", json={"source": "/etc"})

    assert resp.status_code == 503
    assert "INGEST_ROOT" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_disabled_for_authenticated_caller_too(client, monkeypatch):
    # Same gate with auth on: a valid key does not buy an unconfined read.
    monkeypatch.setattr(deps.settings, "ingest_root", "")
    monkeypatch.setattr(security.settings, "api_keys", ["s3cret"])

    resp = await client.post(
        "/v1/ingest", json={"source": "/etc"}, headers={"X-API-Key": "s3cret"}
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_ingest_accepted_when_ingest_root_set(client, monkeypatch, tmp_path):
    # The gate is exactly the empty root — a configured one still ingests.
    doc = tmp_path / "doc.txt"
    doc.write_text("hello " * 40, encoding="utf-8")
    monkeypatch.setattr(deps.settings, "ingest_root", str(tmp_path))

    resp = await client.post("/v1/ingest", json={"source": str(doc)})
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


def test_unset_ingest_root_warns_but_boots(monkeypatch, caplog):
    # No hard fail: an unset root disables ingest (503) instead of bricking a
    # deployment that never ingests — but it must be visible at boot.
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    monkeypatch.setattr(deps.settings, "api_keys", ["k"])
    monkeypatch.setattr(deps.settings, "ingest_root", "")
    with caplog.at_level(logging.WARNING):
        deps._validate_production_settings()  # must not raise
    assert any("ingest_root is unset" in r.message for r in caplog.records)


def test_filesystem_root_as_ingest_root_rejected(monkeypatch):
    # "/" satisfies non-emptiness while confining nothing — refuse it, so a boot
    # complaint about ingest_root can't be "fixed" by disabling the guard.
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    monkeypatch.setattr(deps.settings, "ingest_root", "/")
    with pytest.raises(RuntimeError, match="confines nothing"):
        deps._validate_production_settings()


def test_traversal_to_filesystem_root_rejected(monkeypatch):
    # ...including a root that only *resolves* to "/".
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    monkeypatch.setattr(deps.settings, "ingest_root", "/srv/..")
    with pytest.raises(RuntimeError, match="confines nothing"):
        deps._validate_production_settings()


def test_missing_ingest_root_directory_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    monkeypatch.setattr(deps.settings, "ingest_root", str(tmp_path / "nope"))
    with pytest.raises(RuntimeError, match="not an existing directory"):
        deps._validate_production_settings()


def test_file_as_ingest_root_rejected(monkeypatch, tmp_path):
    f = tmp_path / "corpus.txt"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    monkeypatch.setattr(deps.settings, "ingest_root", str(f))
    with pytest.raises(RuntimeError, match="not an existing directory"):
        deps._validate_production_settings()


def test_durable_still_requires_an_ingest_root(monkeypatch):
    # Unchanged pre-existing policy: the production marker demands one be set.
    # (Distinct from the request-time gate above — this one refuses to boot.)
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    monkeypatch.setattr(deps.settings, "api_keys", ["k"])
    monkeypatch.setattr(deps.settings, "ingest_root", "")
    with pytest.raises(RuntimeError, match="require_durable_backends is set"):
        deps._validate_production_settings()
