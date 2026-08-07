"""`default` is a POINTER, never a second registry entry over one store (#275/#276).

The defect these tests pin: `_build_collection_registry` used to ALWAYS synthesise
an entry with id `default` from the top-level settings, then append every spec.
When a spec named the same physical store — the ordinary shape for a deployment
with `QDRANT_COLLECTION_EXPLICIT` plus a matching named spec — the same corpus was
served under two registry ids.

Access control is asserted at the collection (ADR-0003), so two ids over one store
are two INDEPENDENT ACLs over one dataset. Revoking a grant on one id leaves the
same bytes readable through the other: an owner who un-publishes a corpus has not
un-published it. That was reproduced live before this fix, and the 21-second
window is still in the audit trail of the tenant it was reproduced on.

`_build_collection_registry` had NO test coverage at all, so these are new rather
than updated.
"""
from __future__ import annotations

import httpx
import pytest

from ragstack.api import deps
from ragstack.collection_store import CollectionSpec

pytestmark = pytest.mark.asyncio


class _FakeStore:
    """Minimal CollectionStore: only `list_specs` is reached from here."""

    def __init__(self, specs: list[CollectionSpec]) -> None:
        self._specs = specs

    async def list_specs(self) -> list[CollectionSpec]:
        return list(self._specs)


def _spec(cid: str, collection: str, *, text_index: str = "") -> CollectionSpec:
    return CollectionSpec(
        id=cid,
        collection=collection,
        text_index=text_index,
        embedding_model="m",
        embedding_model_dim=8,
        chunk_method="fixed_token",
        chunk_size=512,
    )


async def _build(
    monkeypatch, specs, *, derived="phys", default_id_setting="", derived_es=None
):
    """Build a registry with the ES/Qdrant legs stubbed out.

    ``derived_es`` pins the derived entry's ES index. It is NOT the collection
    name — it comes from ``ELASTICSEARCH_INDEX`` via ``_es_index_name()`` — which
    is exactly why the two legs are compared separately in the fix."""
    monkeypatch.setattr(deps, "_es_index_name", lambda: derived_es or derived)
    monkeypatch.setattr(deps.settings, "default_collection_id", default_id_setting)
    monkeypatch.setattr(deps.settings, "embedding_model", "m")
    monkeypatch.setattr(deps.settings, "embedding_model_dim", 8)
    # Manifest writes are a side effect we are not testing here.
    monkeypatch.setattr(deps, "_materialize_config_manifest", lambda *a, **k: None)
    monkeypatch.setattr(deps, "materialize_config_manifest_for_spec", lambda *a, **k: None)
    async with httpx.AsyncClient() as http:
        return await deps._build_collection_registry(
            http,
            graph_store=None,
            default_embedder=object(),
            default_vector_store=object(),
            default_text_index=object(),
            default_retriever=object(),
            default_collection=derived,
            store=_FakeStore(specs),
        )


async def test_no_second_entry_when_a_spec_already_serves_the_store(monkeypatch):
    """THE BUG. A named spec over the same Qdrant collection as the derived
    default must not produce a second id for that data."""
    reg = await _build(monkeypatch, [_spec("lucid", "phys")], derived="phys")

    ids = sorted(e.id for e in reg.entries())
    assert ids == ["lucid"], "a synthetic 'default' would be a second ACL over one store"
    # ...and a request that omits `collection` still resolves — to the real entry.
    assert reg.default_id == "lucid"
    assert reg.resolve(None).id == "lucid"


async def test_a_shared_ES_INDEX_also_counts_as_the_same_store(monkeypatch):
    """Aliasing on the text leg hides the same data under two ids just as
    effectively as aliasing the Qdrant collection, so both legs are compared."""
    # Vectors differ; only the ES index collides.
    reg = await _build(
        monkeypatch,
        [_spec("named", "other-vectors", text_index="shared-es")],
        derived="phys-vectors",
        derived_es="shared-es",
    )
    assert sorted(e.id for e in reg.entries()) == ["named"]
    assert reg.default_id == "named"


async def test_one_registry_entry_per_physical_store(monkeypatch):
    """The invariant stated positively (ADR-0002 amendment): whatever the specs
    say, no physical store may appear under two ids."""
    reg = await _build(
        monkeypatch, [_spec("a", "phys"), _spec("b", "other")], derived="phys"
    )
    physical = [e.collection for e in reg.entries()]
    assert len(physical) == len(set(physical)), physical


async def test_the_derived_entry_survives_when_nothing_claims_it(monkeypatch):
    """Backward compatibility: a deployment whose specs name DIFFERENT stores
    keeps its `default` entry. Renaming it would orphan its ACL rows — the owner
    row and public grant are keyed by registry id — and lock people out of their
    own collection. That rename belongs to the migration, not to this fix."""
    reg = await _build(monkeypatch, [_spec("open-access", "other")], derived="phys")

    assert sorted(e.id for e in reg.entries()) == ["default", "open-access"]
    assert reg.default_id == "default"
    assert reg.resolve(None).collection == "phys"


async def test_a_single_collection_deployment_is_unchanged(monkeypatch):
    """No specs at all — the commonest shape. Still exactly one entry."""
    reg = await _build(monkeypatch, [], derived="phys")
    assert [e.id for e in reg.entries()] == ["default"]
    assert reg.default_id == "default"


async def test_default_collection_id_repoints_the_pointer(monkeypatch):
    reg = await _build(
        monkeypatch, [_spec("open-access", "other")], derived="phys",
        default_id_setting="open-access",
    )
    assert reg.default_id == "open-access"
    assert reg.resolve(None).id == "open-access"


async def test_an_unresolvable_default_collection_id_is_fatal(monkeypatch):
    """An operator typo must stop the server, not silently serve a different
    corpus (or 404 every no-collection request from deep inside a router)."""
    with pytest.raises(RuntimeError, match="names no registered collection"):
        await _build(
            monkeypatch, [_spec("open-access", "other")], derived="phys",
            default_id_setting="open-acces",  # typo
        )


async def test_the_surviving_entry_keeps_the_shared_surface_exemptions(monkeypatch):
    """`is_shared_surface` is NOT `is default`. It marks the legacy
    tenant-stamped surface and carries two authz exemptions with it: no
    share-based scope widening, and read-not-write on the omitted-collection
    ingest branch. A named spec must never inherit them just because the pointer
    happens to name it."""
    reg = await _build(monkeypatch, [_spec("lucid", "phys")], derived="phys")
    lucid = reg.resolve("lucid")
    assert lucid.id == reg.default_id  # it IS the pointer target...
    assert lucid.is_shared_surface is False  # ...and still gets no exemption

    reg2 = await _build(monkeypatch, [], derived="phys")
    assert reg2.resolve("default").is_shared_surface is True
