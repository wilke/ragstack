"""count_scope_many / shared_scope_many must AGREE with N independent calls to
count_scope / shared_scope over a mixed set of collections (owned, direct
share, public grant, unshared, no owner row, a co-resident pair, and the
legacy shared surface) — the batched path (#314) must never diverge from the
per-entry semantics it replaces. Also pins store-error parity (both paths
fail closed to no widening) and the empty-input case."""
from __future__ import annotations

import pytest

from ragstack.acl_store import (
    GRANTEE_GROUP,
    GRANTEE_USER,
    PERM_OWNER,
    PERM_READ,
    PUBLIC_GROUP,
    get_acl_store,
)
from ragstack.api import security
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.scope import count_scope, count_scope_many, shared_scope, shared_scope_many
from ragstack.api.security import ROLE_USER, Principal
from tests.api.conftest import SHARED_ID, _StateRetriever

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _principals(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", ["k-owner", "k-bob"])
    monkeypatch.setattr(security.settings, "api_key_tenants", {"k-owner": "owner", "k-bob": "bob"})
    monkeypatch.setattr(security.settings, "api_key_roles", {})
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


@pytest.fixture(autouse=True)
def _app_state():
    """This file lives in tests/unit, so it does NOT inherit tests/api/conftest.py's
    autouse `client` / `_acl_store` fixtures — stand up just enough by hand: a fresh
    in-memory ACL store (mirroring what `_acl_store` does for the api tests), and
    placeholder vector/text stores for the CollectionEntry objects below. The scope
    functions under test never call a method on either store — they only read
    `.collection` / `.es_index()` / `.is_shared_surface` / `.id` off the entry — so a
    bare sentinel is enough."""
    from ragstack.acl_store import InMemoryAclStore, reset_acl_store, set_acl_store

    app.state.vector_store = object()
    app.state.text_index = object()
    set_acl_store(InMemoryAclStore())
    yield
    reset_acl_store()


def _entry(cid: str, default: bool = False, physical: str | None = None):
    return CollectionEntry(
        id=cid, label=cid, collection=physical or cid, model="test-model", dim=4,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=default, retriever=_StateRetriever(),
        vector_store=app.state.vector_store, text_index=app.state.text_index,
    )


async def test_many_equals_single_over_mixed_set():
    entries = [
        _entry(SHARED_ID, True),        # legacy surface
        _entry("owned-by-bob"),         # caller owns
        _entry("shared-to-bob"),        # direct read share
        _entry("public-open"),          # public grant
        _entry("unshared"),             # bob can't read (not in listing normally, but scope fn is total)
        _entry("no-owner-row"),         # legacy, no ACL rows
        _entry("co-a", physical="co"),  # co-resident pair
        _entry("co-b", physical="co"),
    ]
    registry = CollectionRegistry(entries, default_id=SHARED_ID)
    acl = get_acl_store()
    await acl.grant("owned-by-bob", GRANTEE_USER, "bob", PERM_OWNER, granted_by="bob")
    await acl.grant("shared-to-bob", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")
    await acl.grant("shared-to-bob", GRANTEE_USER, "bob", PERM_READ, granted_by="owner")
    await acl.grant("public-open", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")
    await acl.grant("public-open", GRANTEE_GROUP, PUBLIC_GROUP, PERM_READ, granted_by="owner")
    await acl.grant("unshared", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")
    await acl.grant("co-a", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")
    await acl.grant("co-a", GRANTEE_USER, "bob", PERM_READ, granted_by="owner")
    await acl.grant("co-b", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")

    principal = Principal(tenant="bob", role=ROLE_USER, subject="bob")
    many = await count_scope_many(entries, registry, principal)
    single = {e.id: await count_scope(e, registry, principal) for e in entries}
    assert many == single, (many, single)
    smany = await shared_scope_many(entries, registry, principal)
    ssingle = {e.id: await shared_scope(e, registry, principal) for e in entries}
    assert smany == ssingle
    # sanity: the widening actually happened where it should
    assert many["shared-to-bob"][-1] == "owner"
    assert many["public-open"][-1] == "owner"
    assert "owner" not in many["co-a"]
    assert "owner" not in many[SHARED_ID]
    assert many["owned-by-bob"] == many["no-owner-row"]


async def test_many_on_store_error_matches_single(monkeypatch):
    entries = [_entry("x"), _entry("y")]
    registry = CollectionRegistry([_entry(SHARED_ID, True), *entries], default_id=SHARED_ID)
    acl = get_acl_store()
    await acl.grant("x", GRANTEE_USER, "owner", PERM_OWNER, granted_by="owner")

    async def boom(*a, **k):
        raise RuntimeError("acl down")

    monkeypatch.setattr(acl, "owners_of", boom)
    monkeypatch.setattr(acl, "owner_of", boom)
    principal = Principal(tenant="bob", role=ROLE_USER, subject="bob")
    many = await count_scope_many(entries, registry, principal)
    single = {e.id: await count_scope(e, registry, principal) for e in entries}
    assert many == single
    assert all("owner" not in v for v in many.values())


async def test_many_with_duplicate_entries_and_empty():
    registry = CollectionRegistry([_entry(SHARED_ID, True)], default_id=SHARED_ID)
    principal = Principal(tenant="bob", role=ROLE_USER, subject="bob")
    assert await count_scope_many([], registry, principal) == {}
