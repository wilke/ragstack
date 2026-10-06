"""``fixed_token`` is the default chunk method for NEW collections (owner
decision 2026-10-06) — and only for new ones.

Three properties, each one a way the flip could go wrong:

1. A create that names no chunk strategy records ``fixed_token/512/64``; an
   explicit ``fixed`` still works.
2. EXISTING collections are untouched (ADR-0002 identity): a registry spec with
   no recorded method, and the settings-derived default collection, keep
   resolving to ``fixed`` when ``CHUNK_METHOD`` is unset — same chunker, same
   physical name, same ``spec_hash`` as before the flip.
3. ``fixed_token`` needs the model's HF tokenizer. When it cannot load, the
   unconfigured default falls back to ``fixed`` (recorded in the spec), an
   explicitly configured ``CHUNK_METHOD=fixed_token`` refuses with 503, and the
   boot-time chunker of an unconfigured deployment never touches a tokenizer.
"""
from __future__ import annotations

import pytest

from ragstack.api import deps, security
from ragstack.api.collections import CollectionEntry
from ragstack.api.security import ROLE_ADMIN
from ragstack.collection_store import CollectionSpec
from ragstack.config import LEGACY_CHUNK_METHOD, Settings, settings
from ragstack.ingestion.chunkers import FixedTokenWindowChunker, RecursiveCharacterChunker
from ragstack.ingestion.tokenization import TokenCounterUnavailable
from ragstack.provenance import chunk_descriptor, spec_hash
from ragstack.stores.qdrant import collection_name

#: Captured at import, before tests/api/conftest.py pins the probe to "loads".
_REAL_PROBE = deps._hf_tokenizer_error


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)


@pytest.fixture
def _unconfigured(monkeypatch):
    """The global settings as a deployment that never set CHUNK_METHOD sees them."""
    monkeypatch.delenv("CHUNK_METHOD", raising=False)
    monkeypatch.setattr(settings, "chunk_method", "fixed_token")
    monkeypatch.setattr(settings, "_chunk_method_configured", False)


def _no_tokenizer(monkeypatch):
    err = TokenCounterUnavailable("hf", "text-embedding-3-small", OSError("not on the hub"))
    monkeypatch.setattr(deps, "_hf_tokenizer_error", lambda model: err)


def _entry(method: str | None) -> CollectionEntry:
    return CollectionEntry(
        id="legacy", label="legacy", collection="physical_legacy", model="m", dim=8,
        chunk_method=method, chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=False, retriever=object(), vector_store=object(),
        text_index=object(), embedder=object(),
    )


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def test_the_default_is_fixed_token_with_size_and_overlap_unchanged(monkeypatch):
    monkeypatch.delenv("CHUNK_METHOD", raising=False)
    s = Settings(_env_file=None)
    assert s.chunk_method == "fixed_token"
    assert (s.chunk_size, s.chunk_overlap) == (512, 64)


def test_unrecorded_method_stays_fixed_unless_chunk_method_is_configured(monkeypatch):
    monkeypatch.delenv("CHUNK_METHOD", raising=False)
    assert Settings(_env_file=None).unrecorded_chunk_method() == LEGACY_CHUNK_METHOD == "fixed"
    # Configured explicitly — even to the new default — it is honoured, as before.
    assert Settings(_env_file=None, chunk_method="fixed_token").unrecorded_chunk_method() == "fixed_token"
    monkeypatch.setenv("CHUNK_METHOD", "semantic")
    assert Settings(_env_file=None).unrecorded_chunk_method() == "semantic"


def test_assign_then_restore_does_not_flip_the_unrecorded_method(monkeypatch):
    """A runtime assignment of a non-default value is honoured; restoring the
    default (a test's monkeypatch undo) must not leave the answer flipped."""
    monkeypatch.delenv("CHUNK_METHOD", raising=False)
    s = Settings(_env_file=None)
    s.chunk_method = "sentence"
    assert s.unrecorded_chunk_method() == "sentence"
    s.chunk_method = "fixed_token"
    assert s.unrecorded_chunk_method() == "fixed"


# --------------------------------------------------------------------------- #
# Collection create
# --------------------------------------------------------------------------- #


async def test_create_without_chunk_records_fixed_token(client, _unconfigured):
    r = await client.post("/v1/collections", json={"id": "newlib"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["chunk_method"], body["chunk_size"]) == ("fixed_token", 512)
    from ragstack.api.main import app

    entry = app.state.collections.resolve("newlib")
    assert (entry.chunk_method, entry.chunk_overlap) == ("fixed_token", 64)


async def test_explicit_fixed_still_works(client, _unconfigured):
    r = await client.post(
        "/v1/collections",
        json={"id": "charlib", "chunk": {"method": "fixed", "size": 512, "overlap": 64}},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chunk_method"] == "fixed"


async def test_unloadable_tokenizer_falls_back_to_fixed_when_unconfigured(
    client, _unconfigured, monkeypatch, caplog
):
    _no_tokenizer(monkeypatch)
    r = await client.post("/v1/collections", json={"id": "openai-lib"})
    assert r.status_code == 201, r.text
    assert r.json()["chunk_method"] == "fixed"  # recorded, not left to ingest time
    assert "cannot load" in caplog.text


async def test_unloadable_tokenizer_is_503_when_fixed_token_is_configured(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "chunk_method", "fixed_token")
    monkeypatch.setattr(settings, "_chunk_method_configured", True)
    _no_tokenizer(monkeypatch)
    r = await client.post("/v1/collections", json={"id": "refused"})
    assert r.status_code == 503, r.text
    assert "could not load the tokenizer" in r.json()["detail"]


def test_tokenizer_probe_runs_once_per_model(monkeypatch, _unconfigured):
    """The real probe (not the conftest pin): cached per model, so the method a
    create resolves is stable for the life of the process."""
    monkeypatch.setattr(deps, "_hf_tokenizer_error", _REAL_PROBE)
    monkeypatch.setattr(deps, "_HF_TOKENIZER_PROBES", {})
    calls: list[str] = []

    def _fail(backend, *, model, **kw):
        calls.append(model)
        raise TokenCounterUnavailable(backend, model, OSError("no tokenizer"))

    monkeypatch.setattr(deps, "make_token_counter", _fail)
    assert deps.default_chunk_method_for("text-embedding-3-small") == "fixed"
    assert deps.default_chunk_method_for("text-embedding-3-small") == "fixed"
    assert calls == ["text-embedding-3-small"]


# --------------------------------------------------------------------------- #
# ADR-0002: existing collections keep their identity and their chunker
# --------------------------------------------------------------------------- #


def test_method_less_spec_identity_does_not_depend_on_the_default():
    """spec_hash/chunk_descriptor are computed from the spec's OWN fields, so a
    spec recording no method hashes the same before and after the flip."""
    spec = CollectionSpec(
        id="hand", collection="hand_phys", embedding_model="m", embedding_model_dim=8,
        chunk_size=512, chunk_overlap=64,
    )
    assert spec.chunk_descriptor() == "/512/64"
    assert spec.spec_hash() == spec_hash("m", 8, "/512/64")


def test_method_less_entry_is_still_chunked_with_fixed(_unconfigured, monkeypatch):
    def _boom(*a, **kw):  # pragma: no cover - must not be reached
        raise AssertionError("a method-less entry must not load a tokenizer")

    monkeypatch.setattr(deps, "make_token_counter", _boom)
    assert isinstance(deps._chunker_for(_entry(None)), RecursiveCharacterChunker)
    assert isinstance(deps._chunker_for(_entry("")), RecursiveCharacterChunker)


def test_recorded_fixed_token_entry_still_gets_fixed_token(monkeypatch):
    class _Counter:
        def count(self, text):  # pragma: no cover
            return len(text.split())

        def _tokenizer(self):
            return object()

    monkeypatch.setattr(deps, "make_token_counter", lambda backend, **kw: _Counter())
    assert isinstance(deps._chunker_for(_entry("fixed_token")), FixedTokenWindowChunker)


def test_derived_default_name_is_unchanged_with_chunk_in_the_name(_unconfigured, monkeypatch):
    """With collection_name_include_chunk on, the settings-derived collection's
    physical name folds in its chunk descriptor. The flip must not move it — that
    would repoint a live default at a new, empty store."""
    monkeypatch.setattr(settings, "qdrant_collection_explicit", "")
    monkeypatch.setattr(settings, "collection_name_include_chunk", True)
    before = collection_name(
        settings.qdrant_collection, settings.embedding_model, settings.embedding_model_dim,
        chunk=chunk_descriptor("fixed", settings.chunk_size, settings.chunk_overlap),
    )
    assert deps._derived_collection_name() == before


def test_boot_chunker_of_an_unconfigured_deployment_loads_no_tokenizer(
    _unconfigured, monkeypatch
):
    """The app-default chunker serves the settings-derived collection, so it keeps
    `fixed` — and a deployment with no HF tokenizer (the code-default OpenAI
    model has none) boots exactly as before."""
    monkeypatch.setattr(settings, "chunk_max_tokens", None)

    def _boom(*a, **kw):  # pragma: no cover - must not be reached
        raise AssertionError("boot must not load a tokenizer")

    monkeypatch.setattr(deps, "make_token_counter", _boom)
    chunker, bridge = deps._build_chunker()
    assert isinstance(chunker, RecursiveCharacterChunker) and bridge is None


def test_boot_chunker_with_configured_fixed_token_and_no_tokenizer_fails_clearly(monkeypatch):
    """Explicit CHUNK_METHOD=fixed_token on a deployment that cannot load the
    tokenizer refuses at boot with the named error — unchanged behaviour."""
    monkeypatch.setattr(settings, "chunk_method", "fixed_token")
    monkeypatch.setattr(settings, "_chunk_method_configured", True)
    monkeypatch.setattr(settings, "chunk_max_tokens", None)

    def _fail(backend, *, model, **kw):
        raise TokenCounterUnavailable(backend, model, OSError("no tokenizer"))

    monkeypatch.setattr(deps, "make_token_counter", _fail)
    with pytest.raises(TokenCounterUnavailable):
        deps._build_chunker()
