"""Unit tests for the ONE default-collection resolver (#419).

Pure logic — no HTTP, no stores. The API-level equivalence (the listing's
``default`` IS what an omitted ``collection`` serves) lives in
``test_default_collection_resolution.py``; this file pins the rules the shared
module encodes so a future edit has to break an assertion rather than a
production request.

Several tests here MOVED from ``test_collection_access_control.py``, where they
covered ``query.py::_effective_collection``'s copy of the fallback. That copy is
gone: the explicit-allowlist half is now ``_check_allowlist`` and the implicit
half is :func:`~ragstack.api.default_collection.pick_default`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ragstack.api import security
from ragstack.api.default_collection import (
    NO_ACCESSIBLE_COLLECTION,
    pick_default,
    resolve_default_entry,
    visible_entries,
)
from ragstack.api.routers.query import _check_allowlist
from ragstack.api.security import ROLE_USER, Principal
from ragstack.config import settings


def _reg(ids: list[str], default: str):
    """A registry double: insertion order is the order of ``ids``."""
    entries = [SimpleNamespace(id=i) for i in ids]
    return SimpleNamespace(
        default_id=default,
        entries=lambda: list(entries),
        permitted=lambda allowed: allowed,
        resolve=lambda cid: next(e for e in entries if e.id == (default if cid is None else cid)),
    )


def _entries(*ids: str) -> list:
    return [SimpleNamespace(id=i) for i in ids]


# --------------------------------------------------------------------------- #
# T1 — pick_default
# --------------------------------------------------------------------------- #


def test_pick_default_prefers_the_registry_pointer_when_it_is_visible():
    reg = _reg(["a", "ptr", "b"], "ptr")
    assert pick_default(_entries("a", "ptr", "b"), reg) == "ptr"


def test_pick_default_prefers_the_pointer_even_when_it_is_not_first():
    """The pointer wins over ``entries[0]``. A picker that just took the first
    visible entry would pass every other test in this file and this one only."""
    reg = _reg(["a", "ptr"], "ptr")
    assert pick_default(_entries("a", "ptr"), reg) == "ptr"


def test_pick_default_falls_back_to_the_first_visible_entry():
    reg = _reg(["a", "ptr", "b"], "ptr")
    assert pick_default(_entries("a", "b"), reg) == "a"


def test_pick_default_uses_insertion_order_not_lexicographic():
    """**D2, and a deliberate behaviour change.** ``_effective_collection`` used
    ``sorted(present)[0]``; the listing used ``entries[0]``. One shared function
    forces ONE order, and it is the listing's — the order the user already sees
    in the picker, so "the first one in your list" is literally true on screen.

    The registry here is built insertion-first ``"z"``, lexicographic-first
    ``"a"``, so the two rules give different answers and this cannot pass under
    ``sorted()``. An implementer who "fixes" this back to ``sorted()`` has
    reintroduced the drift #419 is about."""
    reg = _reg(["z", "a", "ptr"], "ptr")
    assert pick_default(_entries("z", "a"), reg) == "z"


def test_pick_default_is_none_when_nothing_is_visible():
    """Not the registry pointer: handing back an id that is absent from the
    caller's listing, and that every read endpoint now refuses to serve, is the
    exact lie #419 is about."""
    assert pick_default([], _reg(["ptr"], "ptr")) is None


def test_pick_default_does_no_io_and_ignores_the_principal():
    """PURE by construction — a registry double with no store surface at all
    and no principal argument. The visibility decision (which DOES cost an ACL
    round trip) belongs to `visible_entries`, so the listing and the query path
    can share the pick without sharing a round trip."""
    assert pick_default.__code__.co_varnames[: pick_default.__code__.co_argcount] == (
        "entries",
        "registry",
    )


# --------------------------------------------------------------------------- #
# T9 — visible_entries with auth unconfigured (invariant 2.3b)
# --------------------------------------------------------------------------- #


@pytest.fixture
def _keyless(monkeypatch):
    """Auth unconfigured → ``filter_readable`` is a no-op, so the allowlist is
    the only filter. This is the whole keyless dev path and most of the suite."""
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(settings, "tenant_collections", {})


def _principal(tenant: str = "t") -> Principal:
    return Principal(tenant=tenant, role=ROLE_USER, subject=tenant)


@pytest.mark.asyncio
async def test_visible_entries_is_the_allowlist_alone_when_auth_is_unconfigured(_keyless):
    reg = _reg(["ptr", "a", "b"], "ptr")
    got = await visible_entries(reg, _principal())
    assert [e.id for e in got] == ["ptr", "a", "b"]


@pytest.mark.asyncio
async def test_visible_entries_applies_the_tenant_allowlist(_keyless, monkeypatch):
    monkeypatch.setattr(settings, "tenant_collections", {"t": ["b", "a"]})
    reg = _reg(["ptr", "a", "b"], "ptr")
    got = await visible_entries(reg, _principal())
    # Filtered by the allowlist, but in REGISTRY order, not allowlist order.
    assert [e.id for e in got] == ["a", "b"]


@pytest.mark.asyncio
async def test_resolve_default_entry_404s_with_no_id_when_nothing_is_visible(
    _keyless, monkeypatch
):
    monkeypatch.setattr(settings, "tenant_collections", {"t": ["nothing-here"]})
    with pytest.raises(HTTPException) as ei:
        await resolve_default_entry(_reg(["ptr", "a"], "ptr"), _principal())
    assert ei.value.status_code == 404
    assert ei.value.detail == NO_ACCESSIBLE_COLLECTION
    # The message names NO collection: the id would have been chosen by the
    # server, not by the caller (§2.5).
    assert "ptr" not in ei.value.detail and "a" not in ei.value.detail.split()


@pytest.mark.asyncio
async def test_resolve_default_entry_returns_the_pointer_when_permitted(
    _keyless, monkeypatch
):
    monkeypatch.setattr(settings, "tenant_collections", {"t": ["ptr", "a"]})
    entry = await resolve_default_entry(_reg(["ptr", "a"], "ptr"), _principal())
    assert entry.id == "ptr"


@pytest.mark.asyncio
async def test_resolve_default_entry_falls_back_in_insertion_order(_keyless, monkeypatch):
    """MOVED from ``test_collection_access_control.py::
    test_effective_collection_falls_back_to_first_allowed``, which asserted
    ``"a"`` because it pinned ``sorted()``. It now asserts ``"b"`` — the
    registry's insertion-first permitted entry. **Deliberate (D2).**"""
    monkeypatch.setattr(settings, "tenant_collections", {"t": ["b", "a"]})
    entry = await resolve_default_entry(_reg(["ptr", "b", "a"], "ptr"), _principal())
    assert entry.id == "b"


# --------------------------------------------------------------------------- #
# _check_allowlist — the EXPLICIT half, byte-for-byte unchanged
# --------------------------------------------------------------------------- #
# MOVED from test_collection_access_control.py. `_effective_collection` returned
# the id to resolve; `_check_allowlist` only raises or does not, because the
# explicit branch resolves the caller's own string.


def test_check_allowlist_unrestricted_passes(_keyless):
    assert _check_allowlist(_reg(["ptr", "a"], "ptr"), "a", "t") is None


def test_check_allowlist_allowed_passes(_keyless, monkeypatch):
    monkeypatch.setattr(settings, "tenant_collections", {"t": ["a", "b"]})
    assert _check_allowlist(_reg(["ptr", "a", "b"], "ptr"), "a", "t") is None


def test_check_allowlist_disallowed_is_404(_keyless, monkeypatch):
    monkeypatch.setattr(settings, "tenant_collections", {"t": ["a"]})
    with pytest.raises(HTTPException) as ei:
        _check_allowlist(_reg(["ptr", "a", "b"], "ptr"), "b", "t")
    assert ei.value.status_code == 404
    # ...and the body names the id the CALLER supplied, nothing else.
    assert ei.value.detail == "unknown collection 'b'; see GET /v1/collections"


def test_check_allowlist_unlisted_tenant_is_unrestricted(_keyless, monkeypatch):
    monkeypatch.setattr(settings, "tenant_collections", {"other": ["x"]})
    assert _check_allowlist(_reg(["ptr", "a"], "ptr"), "a", "t") is None
