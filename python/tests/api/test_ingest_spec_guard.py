"""The build-spec guard: an ingest may not contradict a collection's lineage.

A collection's identity IS its build spec. Ingesting into an existing collection
with a different embedder or chunker produces an incoherent index — mismatched
vectors, inconsistent chunk boundaries — that retrieves happily and answers
wrongly, with no error anywhere. The guard compares what the ingest would build
with against the collection's recorded provenance and refuses on a concrete
disagreement, naming the field.

The other half of "authoritative" is the positive case: a matching ingest must
actually run with the *collection's* chunker and params, not the server defaults.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.deps import (
    BuildSpecMismatch,
    build_ingestor_for,
    check_ingest_build_spec,
    write_ingest_manifest_for,
)
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.provenance import make_ingest_manifest, read_manifest, write_manifest
from tests.api.conftest import SHARED_ID


def _entry(**over) -> CollectionEntry:
    base: dict = {
        "id": "acme", "label": "acme", "collection": "phys_acme", "model": "sfr", "dim": 8,
        "chunk_method": "fixed", "chunk_size": 200, "chunk_overlap": 20, "chunk_params": {},
        "is_shared_surface": False, "retriever": object(), "vector_store": object(),
        "text_index": object(), "embedder": object(), "embedding_api": "openai",
        "embedding_endpoints": ["http://localhost:9001"],
    }
    base.update(over)
    return CollectionEntry(**base)


def _record(manifest_dir, entry: CollectionEntry, **over) -> None:
    """Write the collection's verified provenance, defaulting to ``entry``'s spec."""
    kw: dict = {
        "collection": entry.collection, "model": entry.model, "dim": entry.dim,
        "chunk_method": entry.chunk_method, "chunk_size": entry.chunk_size,
        "chunk_overlap": entry.chunk_overlap, "chunk_params": entry.chunk_params,
    }
    kw.update(over)
    write_manifest(manifest_dir, make_ingest_manifest(**kw))


@pytest.fixture
def manifest_dir(tmp_path, monkeypatch):
    from ragstack.api import deps

    d = str(tmp_path / "manifests")
    monkeypatch.setattr(deps.settings, "collection_manifest_dir", d)
    monkeypatch.setattr(deps.settings, "collection_spec_guard", True)
    return d


# --------------------------------------------------------------------------- #
# the guard itself
# --------------------------------------------------------------------------- #


def test_matching_spec_passes(manifest_dir):
    entry = _entry()
    _record(manifest_dir, entry)
    check_ingest_build_spec(entry)  # no raise


@pytest.mark.parametrize(
    ("recorded", "field"),
    [
        ({"chunk_size": 512}, "chunk_size"),
        ({"chunk_overlap": 64}, "chunk_overlap"),
        ({"chunk_method": "semantic"}, "chunk_method"),
        ({"model": "bge"}, "embedding_model"),
        ({"dim": 4096}, "embedding_dim"),
        ({"chunk_params": {"buffer_size": 5}}, "chunk_params"),
    ],
)
def test_each_identity_field_is_guarded_and_named(manifest_dir, recorded, field):
    """Every field that feeds the chunker or the embedder is part of identity, and
    the refusal has to say which one moved — "spec_hash differs" is unactionable."""
    entry = _entry(chunk_params={"buffer_size": 3})
    _record(manifest_dir, entry, **recorded)
    with pytest.raises(BuildSpecMismatch) as exc:
        check_ingest_build_spec(entry)
    assert field in str(exc.value)
    assert exc.value.recorded_hash and exc.value.requested_hash
    assert exc.value.recorded_hash != exc.value.requested_hash
    assert exc.value.collection == "phys_acme"


def test_unrecorded_fields_are_not_a_conflict(manifest_dir):
    """Manifests predate several of these fields, and a config-materialized one
    may legitimately leave a size null (semantic chunkers ignore it). "Not stated"
    must never read as "differs", or shipping the guard would make existing
    corpora un-ingestable."""
    entry = _entry(chunk_method="semantic", chunk_size=None, chunk_overlap=None,
                   chunk_params={"buffer_size": 5})
    _record(manifest_dir, entry, chunk_params={}, chunk_size=None, chunk_overlap=None)
    check_ingest_build_spec(entry)


def test_no_manifest_means_nothing_to_contradict(manifest_dir):
    check_ingest_build_spec(_entry())  # never written — allowed


def test_guard_is_skipped_when_manifests_are_disabled(tmp_path, monkeypatch):
    from ragstack.api import deps

    d = str(tmp_path / "m")
    _record(d, _entry(), chunk_size=999)
    monkeypatch.setattr(deps.settings, "collection_manifest_dir", "")
    check_ingest_build_spec(_entry())


def test_guard_can_be_turned_off(manifest_dir, monkeypatch):
    """The override exists because an in-place rebuild is a legitimate (if rare)
    operation; it must be an explicit setting, not a request parameter."""
    from ragstack.api import deps

    _record(manifest_dir, _entry(), chunk_size=999)
    monkeypatch.setattr(deps.settings, "collection_spec_guard", False)
    check_ingest_build_spec(_entry())


# --------------------------------------------------------------------------- #
# the manifest an ingest writes must describe the ingest
# --------------------------------------------------------------------------- #


def test_ingest_manifest_records_the_collections_own_params(manifest_dir):
    """Regression: ``write_ingest_manifest_for`` dropped ``chunk_params`` and
    stamped the *server's* embedding api. So the first real ingest into a
    semantic library rewrote its manifest with a different spec_hash for the
    identical build — fake drift, which is precisely what the guard reads."""
    entry = _entry(chunk_method="semantic", chunk_size=None, chunk_overlap=None,
                   chunk_params={"buffer_size": 5}, embedding_api="openai")
    _record(manifest_dir, entry, source="config")
    before = read_manifest(manifest_dir, entry.collection)

    write_ingest_manifest_for(entry, source="/data/docs", chunk_count=42)
    after = read_manifest(manifest_dir, entry.collection)

    assert before is not None and after is not None
    assert after.chunk_params == {"buffer_size": 5}
    assert after.embedding_api == "openai"
    assert after.embedding_endpoints == ["http://localhost:9001"]
    # source upgrades declared -> verified, and the identity hash does NOT move.
    assert before.source == "config" and after.source == "ingest"
    assert after.spec_hash == before.spec_hash
    check_ingest_build_spec(entry)  # ...so the next ingest is still allowed


# --------------------------------------------------------------------------- #
# the matching path really uses the collection's chunker
# --------------------------------------------------------------------------- #


def test_matching_ingest_uses_the_collections_chunker_not_the_defaults(
    manifest_dir, monkeypatch
):
    from ragstack.api import deps

    # Server defaults deliberately set to something the collection does not use,
    # so "it happened to match" cannot pass this.
    monkeypatch.setattr(deps.settings, "chunk_size", 999)
    monkeypatch.setattr(deps.settings, "chunk_overlap", 111)
    monkeypatch.setattr(deps.settings, "chunk_method", "words")

    entry = _entry(chunk_method="fixed", chunk_size=200, chunk_overlap=20)
    _record(manifest_dir, entry)
    check_ingest_build_spec(entry)  # the spec matches, so the ingest may proceed

    app_state = SimpleNamespace(
        graph_store=None, kg_extractor=None, http_client=httpx.AsyncClient(),
        job_store=None,
    )
    chunker = build_ingestor_for(app_state, entry)._pipeline.chunker
    assert isinstance(chunker, RecursiveCharacterChunker)  # 'fixed', not 'words'
    assert (chunker.chunk_size, chunker.chunk_overlap) == (200, 20)


# --------------------------------------------------------------------------- #
# through the endpoint
# --------------------------------------------------------------------------- #


def _install(entry: CollectionEntry) -> None:
    """Add ``entry`` alongside the conftest default collection, with the extra
    ``app.state`` bits a *targeted* ingest needs (the conftest builds only the
    single-collection path)."""
    from ragstack.api.main import app

    existing = app.state.collections.entries()
    app.state.collections = CollectionRegistry([*existing, entry], default_id=SHARED_ID)
    app.state.kg_extractor = None
    app.state.doi_enricher = None


@pytest.mark.asyncio
async def test_ingest_into_a_mismatched_collection_is_409(client, manifest_dir):
    entry = _entry()
    _record(manifest_dir, entry, chunk_size=512, chunk_overlap=64)
    _install(entry)
    r = await client.post("/v1/ingest", json={"source": "x.txt", "collection": "acme"})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "chunk_size" in detail and "512" in detail and "200" in detail
    assert "acme" in detail and "phys_acme" in detail


@pytest.mark.asyncio
async def test_ingest_into_a_matching_collection_is_accepted(client, manifest_dir):
    entry = _entry()
    _record(manifest_dir, entry)
    _install(entry)
    r = await client.post("/v1/ingest", json={"source": "x.txt", "collection": "acme"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_the_default_collection_is_guarded_too(client, manifest_dir):
    """A pinned ``qdrant_collection_explicit`` keeps its name across a settings
    change, so the default collection is exactly where a swapped chunker would
    quietly append incoherent data to a huge existing index."""
    from ragstack.api.main import app

    default = app.state.collections.resolve(SHARED_ID)
    _record(manifest_dir, default, chunk_method="semantic")
    r = await client.post("/v1/ingest", json={"source": "x.txt"})
    assert r.status_code == 409, r.text
    assert "chunk_method" in r.json()["detail"]


@pytest.mark.asyncio
async def test_unguarded_deployments_are_unaffected(client, tmp_path, monkeypatch):
    """The shipped default (`collection_manifest_dir` unset) has no lineage to
    check, so merely upgrading cannot start refusing anybody's ingests."""
    from ragstack.api import deps

    monkeypatch.setattr(deps.settings, "collection_manifest_dir", "")
    r = await client.post("/v1/ingest", json={"source": "x.txt"})
    assert r.status_code == 200
