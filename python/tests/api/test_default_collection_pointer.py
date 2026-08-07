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


async def test_a_HALF_shared_default_is_refused_at_startup(monkeypatch):
    """A spec that claims only ONE of the derived entry's two legs is a
    misconfiguration, not something to resolve silently.

    Suppressing the derived entry would close the ACL hole on the claimed leg
    while STRANDING the other: `app.state`'s vector store and ingestor keep
    serving a physical store that no registry entry covers, so nothing
    authorizes access to it at all. Serving one store under two ids is the bug
    this change fixes; serving it under NO id is worse. (This one needs no
    DEFAULT_COLLECTION_ID to reach — a `collections_file` is enough.)"""
    with pytest.raises(RuntimeError, match="only one leg"):
        await _build(
            monkeypatch,
            [_spec("named", "other-vectors", text_index="shared-es")],
            derived="phys-vectors",
            derived_es="shared-es",
        )


async def test_both_legs_shared_suppresses_the_derived_entry(monkeypatch):
    """The ordinary case: a spec serving the same vectors AND the same text
    index is simply that collection, so no second id is minted for it."""
    reg = await _build(
        monkeypatch,
        [_spec("named", "phys", text_index="phys-es")],
        derived="phys",
        derived_es="phys-es",
    )
    assert sorted(e.id for e in reg.entries()) == ["named"]
    assert reg.default_id == "named"


async def test_two_specs_may_not_alias_each_other(monkeypatch):
    """The invariant is about PHYSICAL stores, not about the synthesised entry:
    two specs over one store are the same two-ACLs-over-one-dataset defect,
    reachable straight from a hand-authored collections_file."""
    with pytest.raises(RuntimeError, match="both serve"):
        await _build(
            monkeypatch, [_spec("a", "shared"), _spec("b", "shared")], derived="phys"
        )


async def test_a_spec_may_not_claim_the_reserved_pointer_id(monkeypatch):
    """`default` names the pointer. A spec taking that id used to silently shadow
    the server default (last-wins in the id dict) — with its own stores and
    without the shared-surface flag."""
    with pytest.raises(RuntimeError, match="reserved id"):
        await _build(monkeypatch, [_spec("default", "somewhere-else")], derived="phys")


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


# --- the surfaces that resolve the pointer ---------------------------------- #
#
# These are regression tests for a defect this change INTRODUCED and an adversarial
# review caught: authorization moved to the pointer target while the STORE stayed
# `app.state`'s. The two were the same object for as long as the pointer could
# only be the settings-derived entry, so nothing noticed. Once they can differ,
# authorizing against one collection and reading/writing another is the #275
# defect inverted — one id's ACL gating another id's data.


async def test_list_documents_reads_the_entry_it_authorized(client, monkeypatch):
    """F2 REGRESSION. `GET /v1/documents` authorized `read` on the pointer target
    but read `app.state`'s text index. Once those can differ, it served one
    collection's documents under another collection's ACL."""
    from ragstack.api.main import app
    from ragstack.models import Chunk
    from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore

    # The app-level index holds a document; the pointer target's does not.
    app_index = app.state.text_index
    await app_index.index(
        [Chunk(id="c1", doc_id="secret-doc", content="x", embedding=[0.1] * 4)]
    )

    target = deps.CollectionEntry(
        id="pointed-at", label="pointed-at", collection="other-phys",
        model="m", dim=4, chunk_method="fixed", chunk_size=256, chunk_overlap=32,
        chunk_params={}, is_shared_surface=False, retriever=None,
        vector_store=InMemoryVectorStore(), text_index=InMemoryTextIndex(),
    )
    app.state.collections.add(target)
    monkeypatch.setattr(app.state.collections, "_default_id", target.id)

    r = await client.get("/v1/documents")
    assert r.status_code == 200, r.text
    doc_ids = [d["doc_id"] for d in r.json()]
    assert "secret-doc" not in doc_ids, (
        "served a document from the app-level index while authorizing against "
        "the pointer target's collection"
    )


async def test_the_delete_document_exemption_keys_on_the_surface():
    """F3 REGRESSION. The read-not-write exemption was hardcoded here, and the
    split that fixed the ingest branch missed its sibling mutation. Checked on
    the compiled function so a comment cannot satisfy it."""
    from ragstack.api.routers import documents

    consts = documents.delete_document.__code__.co_consts
    assert "write" in consts, "delete_document never asks for write access"
    # ...and it resolves the entry rather than taking app-level stores.
    params = set(documents.delete_document.__annotations__) | set(
        documents.delete_document.__code__.co_varnames
    )
    assert "text_index" not in params and "vector_store" not in params
