"""Collection provenance surfaced by GET /v1/collections.

``provenance`` is null exactly when no manifest exists — the common cause being
``COLLECTION_MANIFEST_DIR`` left unset, which disables manifests entirely. With a
dir configured, registry collections get a source='config' manifest materialized
for them (declared lineage), which an actual ingest later upgrades to
source='ingest' (verified). These tests pin that distinction and the fields the
Ops UI renders.
"""
import pytest

from ragstack.api import deps, security
from ragstack.api.collections import CollectionSpec
from ragstack.api.routers import collections as collections_router
from ragstack.api.security import ROLE_ADMIN
from ragstack.provenance import chunk_descriptor, read_manifest, spec_hash


@pytest.fixture(autouse=True)
def _keyless(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)


@pytest.fixture
def manifest_dir(tmp_path, monkeypatch):
    """Point every manifest reader/writer at a temp dir (they read `settings`
    through three different module namespaces)."""
    d = str(tmp_path / "manifests")
    for mod in (deps, collections_router):
        monkeypatch.setattr(mod.settings, "collection_manifest_dir", d)
    return d


def _spec(**kw) -> CollectionSpec:
    base = {
        "id": "c1", "label": "C1", "collection": "phys_c1", "text_index": "phys_c1",
        "embedding_api": "openai", "embedding_model": "sfr", "embedding_model_dim": 4096,
        "embedding_endpoints": ["http://embed:9998"],
        "chunk_method": "semantic", "chunk_size": None, "chunk_overlap": None,
        "chunk_params": {"threshold": 0.7},
    }
    base.update(kw)
    return CollectionSpec(**base)


@pytest.mark.asyncio
async def test_provenance_null_when_manifests_disabled(client, monkeypatch):
    # The live-deployment case: COLLECTION_MANIFEST_DIR unset → no manifest is
    # ever written or read, so every collection reports provenance: null.
    monkeypatch.setattr(collections_router.settings, "collection_manifest_dir", "")
    r = await client.get("/v1/collections")
    assert r.status_code == 200
    assert all(c["provenance"] is None for c in r.json()["collections"])


@pytest.mark.asyncio
async def test_config_manifest_is_reported_as_declared_not_verified(client, manifest_dir):
    deps.materialize_config_manifest_for_spec(_spec(collection="ragstack"))
    prov = (await client.get("/v1/collections")).json()["collections"][0]["provenance"]
    assert prov is not None
    # source distinguishes declared-from-registry lineage from a verified ingest —
    # the UI must not present the former as proof of how the corpus was built.
    assert prov["source"] == "config"
    assert prov["ingested_at"] == ""  # we genuinely don't know when it was ingested
    assert prov["chunk_count"] is None


@pytest.mark.asyncio
async def test_config_manifest_carries_build_spec(client, manifest_dir):
    deps.materialize_config_manifest_for_spec(_spec(collection="ragstack"))
    prov = (await client.get("/v1/collections")).json()["collections"][0]["provenance"]
    assert prov["collection"] == "ragstack"
    assert prov["model"] == "sfr" and prov["dim"] == 4096
    assert prov["embedding_api"] == "openai"
    assert prov["chunk_method"] == "semantic"
    assert prov["chunk_params"] == {"threshold": 0.7}
    assert prov["spec_hash"]


@pytest.mark.asyncio
async def test_endpoints_are_not_exposed(client, manifest_dir):
    # The manifest records embedding_endpoints, but /v1/collections is readable by
    # any principal — internal infra URLs must not ride along (same rule as
    # /v1/models/available hiding base_urls).
    deps.materialize_config_manifest_for_spec(_spec(collection="ragstack"))
    r = await client.get("/v1/collections")
    assert "embed:9998" not in r.text
    assert "embedding_endpoints" not in r.json()["collections"][0]["provenance"]


def test_config_manifest_hashes_chunk_params(manifest_dir, monkeypatch):
    # Regression: the config path used to drop chunk_params from the descriptor,
    # so a params-bearing chunker (semantic) hashed differently here than on the
    # ingest path — the same build looked like two different specs.
    spec = _spec()
    deps.materialize_config_manifest_for_spec(spec)
    m = read_manifest(manifest_dir, "phys_c1")
    assert m is not None
    desc = chunk_descriptor("semantic", None, None, {"threshold": 0.7})
    assert m.spec_hash == spec_hash("sfr", 4096, desc)
    assert m.chunk_params == {"threshold": 0.7}


def test_config_manifest_stamps_version(manifest_dir, monkeypatch):
    # The manifest records which build wrote this corpus: whatever
    # ``ragstack_version()`` reports. Pinned to a sentinel because its real value
    # depends on install metadata — "" from an uninstalled source tree, by design —
    # so asserting mere truthiness tested the environment, not the stamping.
    # ``deps`` imports it function-locally from ``ragstack.provenance`` at call
    # time, so patching the module attribute is the name actually looked up.
    import ragstack.provenance

    sentinel = "0.0.0+stamp-sentinel"
    monkeypatch.setattr(ragstack.provenance, "ragstack_version", lambda: sentinel)
    deps.materialize_config_manifest_for_spec(_spec())
    m = read_manifest(manifest_dir, "phys_c1")
    assert m is not None
    assert m.ragstack_version == sentinel


def test_config_manifest_never_clobbers_a_verified_one(manifest_dir):
    from ragstack.provenance import make_ingest_manifest, write_manifest

    write_manifest(
        manifest_dir,
        make_ingest_manifest(
            collection="phys_c1", model="sfr", dim=4096, corpus="/data/real", chunk_count=42
        ),
    )
    deps.materialize_config_manifest_for_spec(_spec())
    m = read_manifest(manifest_dir, "phys_c1")
    assert m is not None
    assert m.source == "ingest" and m.chunk_count == 42  # verified wins


def test_materialize_is_a_noop_when_dir_unset(monkeypatch, tmp_path):
    monkeypatch.setattr(deps.settings, "collection_manifest_dir", "")
    deps.materialize_config_manifest_for_spec(_spec())
    assert not (tmp_path / "manifests").exists()


@pytest.mark.asyncio
async def test_registry_and_manifest_drift_is_both_visible(client, manifest_dir):
    # The registry label is operator-asserted; the manifest is the build record.
    # Both are reported so the UI can flag a mismatch rather than silently
    # trusting either one. (Fixture registry says fixed/None; spec says semantic.)
    deps.materialize_config_manifest_for_spec(_spec(collection="ragstack"))
    c = (await client.get("/v1/collections")).json()["collections"][0]
    assert c["chunk_method"] == "fixed"
    assert c["provenance"]["chunk_method"] == "semantic"
