"""Auth, CORS, and production-settings security tests."""
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


def test_production_settings_pass_when_set(monkeypatch):
    monkeypatch.setattr(deps.settings, "require_durable_backends", True)
    monkeypatch.setattr(deps.settings, "api_keys", ["k"])
    monkeypatch.setattr(deps.settings, "ingest_root", "/srv/corpus")
    deps._validate_production_settings()  # must not raise


def test_dev_skips_production_validation(monkeypatch):
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    monkeypatch.setattr(deps.settings, "api_keys", [])
    monkeypatch.setattr(deps.settings, "ingest_root", "")
    deps._validate_production_settings()  # must not raise
