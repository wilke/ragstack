"""The eviction policy (#359, phase 4 of #353): victim selection and the act.

``choose_victims`` is pure: least-recently-accessed first among records that
are evictable (active, archive current) AND not in flight AND not protected;
it returns what it can plus a per-reason shortfall. ``evict`` swaps the row
``active → dormant`` BEFORE dropping anything, drops nothing when the swap
lost, and keeps ``dormant`` with the leftover named when a drop fails.

The protected-registry test constructs EXACTLY the hazard shape from the #376
review: a spec that claims the settings-derived default's physical stores
(the ``claimed_by`` branch of the registry build — no ``default`` entry is
synthesised, so nothing in the registry flags it) — evicting it would destroy
every tenant's legacy data.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pytest

from ragstack.collection_store import (
    ACTIVE,
    ARCHIVING,
    DORMANT,
    LOST,
    RESTORING,
    CollectionRecord,
    CollectionSpec,
    InMemoryCollectionStore,
    evictable,
)
from ragstack.ops.evict import (
    REASON_ARCHIVE_PENDING,
    REASON_IN_FLIGHT,
    REASON_NO_ARCHIVE,
    REASON_NOT_ACTIVE,
    REASON_PROTECTED,
    REASON_UNREGISTERED,
    REASONS,
    Shortfall,
    choose_victims,
    evict,
    ineligible_reason,
    lru_stamp,
    make_protected,
    protected,
)

NOW = 1_800_000_000.0  # unix seconds; the stamps below are relative to it

DERIVED = ("ragstack", "ragstack")  # the settings-derived default's (qdrant, es)


# --------------------------------------------------------------------------- #
# fixtures: records + a duck-typed registry
# --------------------------------------------------------------------------- #


def _stamp(seconds_ago: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(NOW - seconds_ago, tz=UTC).isoformat()


def _rec(
    cid: str, *, accessed_ago: float | None = 0, created_ago: float = 10_000,
    versions: list[int] | None = None, state: str = ACTIVE, archive_pending: bool = False,
    collection: str | None = None, text_index: str = "",
) -> CollectionRecord:
    spec = CollectionSpec(
        id=cid, collection=collection or f"ragstack_lib_{cid}", text_index=text_index,
        embedding_model="m", embedding_model_dim=4, chunk_method="fixed",
    )
    return CollectionRecord(
        spec=spec, spec_hash="h", created_at=_stamp(created_ago),
        last_accessed_at="" if accessed_ago is None else _stamp(accessed_ago),
        versions=[1] if versions is None else versions,
        state=state, archive_pending=archive_pending,
    )


class _Store:
    """Records the drop calls, in order, onto a shared log."""

    def __init__(self, log: list, name: str, *, fail: bool = False, existed: bool = True):
        self.log, self.name, self.fail, self.existed = log, name, fail, existed

    async def drop_collection(self) -> bool:
        return self._drop()

    async def drop_index(self) -> bool:
        return self._drop()

    def _drop(self) -> bool:
        self.log.append(("drop", self.name))
        if self.fail:
            raise RuntimeError("connection refused")
        return self.existed


@dataclass
class _Entry:
    id: str
    collection: str
    text_index_name: str = ""
    is_shared_surface: bool = False
    vector_store: Any = None
    text_index: Any = None
    log: list = field(default_factory=list)

    def es_index(self) -> str:
        return self.text_index_name or self.collection


class _Registry:
    def __init__(self, *entries: _Entry) -> None:
        self._entries = {e.id: e for e in entries}

    def entries(self) -> list[_Entry]:
        return list(self._entries.values())

    def has(self, cid: str) -> bool:
        return cid in self._entries

    def resolve(self, cid: str) -> _Entry:
        return self._entries[cid]


def _entry_for(rec: CollectionRecord, log: list | None = None, **kw: Any) -> _Entry:
    log = [] if log is None else log
    return _Entry(
        id=rec.spec.id, collection=rec.spec.collection, text_index_name=rec.spec.text_index,
        vector_store=_Store(log, f"vectors:{rec.spec.id}"),
        text_index=_Store(log, f"text:{rec.spec.id}"), log=log, **kw,
    )


def _registry_for(*records: CollectionRecord) -> _Registry:
    return _Registry(*(_entry_for(r) for r in records))


class _RecordingStore(InMemoryCollectionStore):
    """The registry store with its state writes logged onto the same list the
    fake physical stores log their drops to — the order assertion."""

    def __init__(self, log: list) -> None:
        super().__init__()
        self.log = log

    async def set_state(self, cid, state, *, expect=None, reason=""):
        won = await super().set_state(cid, state, expect=expect, reason=reason)
        self.log.append(("set_state", cid, state, expect, won))
        return won


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #


def test_victims_are_least_recently_accessed_first():
    recs = [
        _rec("fresh", accessed_ago=10),
        _rec("stale", accessed_ago=86_400 * 30),
        _rec("older", accessed_ago=86_400 * 90),
        _rec("mid", accessed_ago=86_400 * 7),
    ]
    victims, shortfall = choose_victims(recs, 2, now=NOW)
    assert [v.collection_id for v in victims] == ["older", "stale"]
    assert victims[0].idle_seconds == pytest.approx(86_400 * 90)
    assert shortfall == Shortfall(needed=2, found=2, reasons=dict.fromkeys(REASONS, 0))
    # need covers everything: the whole order, oldest first.
    victims, _ = choose_victims(recs, 10, now=NOW)
    assert [v.collection_id for v in victims] == ["older", "stale", "mid", "fresh"]


def test_never_accessed_falls_back_to_creation_time_then_sorts_first():
    """A collection nobody touched since it was made is as idle as its age;
    one with no stamp at all is the least recently used the registry knows."""
    recs = [
        _rec("touched", accessed_ago=60),
        _rec("untouched-old", accessed_ago=None, created_ago=86_400 * 400),
        _rec("untouched-new", accessed_ago=None, created_ago=30),
    ]
    unknown = _rec("unknown", accessed_ago=None)
    unknown = unknown.model_copy(update={"created_at": ""})
    assert lru_stamp(unknown) == -math.inf
    victims, _ = choose_victims([*recs, unknown], 4, now=NOW)
    assert [v.collection_id for v in victims] == [
        "unknown", "untouched-old", "touched", "untouched-new",
    ]
    assert victims[0].idle_seconds is None
    assert victims[1].idle_seconds == pytest.approx(86_400 * 400)


def test_ties_break_on_the_id_deterministically():
    recs = [_rec("b", accessed_ago=100), _rec("a", accessed_ago=100), _rec("c", accessed_ago=100)]
    victims, _ = choose_victims(recs, 3, now=NOW)
    assert [v.collection_id for v in victims] == ["a", "b", "c"]


def test_archive_pending_is_refused():
    """The last load's archive step failed: the data exists nowhere else."""
    recs = [_rec("oldest", accessed_ago=1_000, archive_pending=True), _rec("newer", accessed_ago=10)]
    victims, shortfall = choose_victims(recs, 1, now=NOW)
    assert [v.collection_id for v in victims] == ["newer"]
    assert shortfall.reasons[REASON_ARCHIVE_PENDING] == 1
    assert shortfall.found == 1 and shortfall.missing == 0


def test_never_archived_is_refused():
    recs = [_rec("oldest", accessed_ago=1_000, versions=[]), _rec("newer", accessed_ago=10)]
    victims, shortfall = choose_victims(recs, 1, now=NOW)
    assert [v.collection_id for v in victims] == ["newer"]
    assert shortfall.reasons[REASON_NO_ARCHIVE] == 1


def test_in_flight_job_is_refused():
    """An accepted/running ingest job targets the LRU collection: dropping
    its stores mid-load would lose what the job already wrote."""
    recs = [_rec("loading", accessed_ago=1_000), _rec("idle", accessed_ago=10)]
    victims, shortfall = choose_victims(recs, 1, now=NOW, in_flight={"loading"})
    assert [v.collection_id for v in victims] == ["idle"]
    assert shortfall.reasons[REASON_IN_FLIGHT] == 1


def test_non_active_states_are_refused_with_the_not_active_reason():
    recs = [_rec(st, state=st, accessed_ago=1_000) for st in (DORMANT, RESTORING, ARCHIVING, LOST)]
    victims, shortfall = choose_victims(recs, 4, now=NOW)
    assert victims == []
    assert shortfall.reasons[REASON_NOT_ACTIVE] == 4
    assert shortfall.describe().startswith("needed 4, found 0 (not_active=4, ")


def test_ineligible_reason_is_exactly_the_evictable_predicate_plus_the_two_conjuncts():
    """The state checks name each failure of ``evictable``; a record that
    passes them IS evictable, and only in-flight/protected can then refuse."""
    never = lambda rec: False  # noqa: E731
    for rec in (
        _rec("a", state=DORMANT), _rec("b", archive_pending=True), _rec("c", versions=[]),
    ):
        assert not evictable(rec)
        assert ineligible_reason(rec, in_flight=(), protected=never) in (
            REASON_NOT_ACTIVE, REASON_ARCHIVE_PENDING, REASON_NO_ARCHIVE,
        )
    ok = _rec("d")
    assert evictable(ok)
    assert ineligible_reason(ok, in_flight=(), protected=never) is None
    assert ineligible_reason(ok, in_flight={"d"}, protected=never) == REASON_IN_FLIGHT
    assert ineligible_reason(ok, in_flight=(), protected=lambda r: True) == REASON_PROTECTED
    assert ineligible_reason(ok, in_flight=(), protected=never, registered={"x"}) == REASON_UNREGISTERED


def test_need_beyond_candidates_returns_what_it_can_with_per_reason_counts():
    recs = [
        _rec("ok-1", accessed_ago=500),
        _rec("ok-2", accessed_ago=400),
        _rec("dormant", state=DORMANT, accessed_ago=900),
        _rec("restoring", state=RESTORING, accessed_ago=900),
        _rec("pending", archive_pending=True, accessed_ago=800),
        _rec("unarchived", versions=[], accessed_ago=700),
        _rec("loading", accessed_ago=600),
        _rec("elsewhere", accessed_ago=650),
    ]
    victims, shortfall = choose_victims(
        recs, 5, now=NOW, in_flight={"loading"},
        registered={r.spec.id for r in recs} - {"elsewhere"},
    )
    assert [v.collection_id for v in victims] == ["ok-1", "ok-2"]
    assert shortfall.needed == 5 and shortfall.found == 2 and shortfall.missing == 3
    assert shortfall.reasons == {
        REASON_NOT_ACTIVE: 2, REASON_ARCHIVE_PENDING: 1, REASON_NO_ARCHIVE: 1,
        REASON_IN_FLIGHT: 1, REASON_PROTECTED: 0, REASON_UNREGISTERED: 1,
    }
    assert "needed 5, found 2" in shortfall.describe()
    assert "in_flight=1" in shortfall.describe()


def test_need_zero_and_negative():
    victims, shortfall = choose_victims([_rec("a")], 0, now=NOW)
    assert victims == [] and shortfall.found == 0 and shortfall.missing == 0
    with pytest.raises(ValueError):
        choose_victims([], -1, now=NOW)


# --------------------------------------------------------------------------- #
# protected: the legacy shared surface is never a candidate
# --------------------------------------------------------------------------- #


def test_a_spec_claiming_the_derived_defaults_stores_is_never_a_victim():
    """THE hazard (the #376 review). The registry was built from a spec whose
    physical stores are the settings-derived default's — ADR-0002 decision
    5's ``claimed_by`` branch: no ``default`` entry is synthesised, the
    claiming entry is ``is_shared_surface=False``, and it sits over the
    legacy multi-tenant data. It is the least recently accessed row by far
    and its archive is current — and it must still be skipped."""
    legacy = _rec("legacy", accessed_ago=86_400 * 365, collection=DERIVED[0], text_index=DERIVED[1])
    lib = _rec("lib", accessed_ago=60)
    registry = _Registry(_entry_for(legacy), _entry_for(lib))  # exactly that shape: no `default`
    assert not any(e.is_shared_surface for e in registry.entries())
    assert evictable(legacy)  # the state predicate alone would let it through

    assert protected(legacy, registry, derived=DERIVED) is True
    assert protected(lib, registry, derived=DERIVED) is False

    victims, shortfall = choose_victims(
        [legacy, lib], 2, now=NOW, protected=make_protected(registry, derived=DERIVED),
    )
    assert [v.collection_id for v in victims] == ["lib"]
    assert shortfall.reasons[REASON_PROTECTED] == 1


def test_a_spec_claiming_only_the_derived_es_index_is_protected_too():
    """Both legs are the shared surface; either one is enough to refuse."""
    rec = _rec("es-only", collection="ragstack_lib_es_only", text_index=DERIVED[1])
    assert protected(rec, _registry_for(rec), derived=DERIVED)


def test_the_synthesised_default_entry_is_protected_by_its_flag():
    """The other shape: the registry did synthesise ``default`` (nothing
    claims its stores). A record over those stores is refused via the
    ``is_shared_surface`` flag, with or without ``derived``."""
    default_entry = _Entry(id="default", collection="ragstack", is_shared_surface=True)
    over_default = _rec("default", collection="ragstack")
    lib = _rec("lib")
    registry = _Registry(default_entry, _entry_for(lib))
    assert protected(over_default, registry) is True
    assert protected(over_default, registry, derived=DERIVED) is True
    assert protected(lib, registry, derived=DERIVED) is False


def test_a_store_two_registry_ids_share_is_protected():
    """Sibling-shared stores (a hand-authored alias, or the half-shared shape
    the purge guard refuses): evicting either id would drop the other's
    data. Same hazard class as the claimed default, same answer."""
    a = _rec("a", collection="phys_shared")
    b = _rec("b", collection="phys_shared")
    half_a = _rec("half-a", collection="phys_a", text_index="shared_es")
    half_b = _rec("half-b", collection="phys_b", text_index="shared_es")
    solo = _rec("solo")
    registry = _registry_for(a, b, half_a, half_b, solo)
    for rec in (a, b, half_a, half_b):
        assert protected(rec, registry, derived=DERIVED), rec.spec.id
    assert not protected(solo, registry, derived=DERIVED)


def test_a_sibling_that_exists_only_in_the_durable_store_still_protects():
    """The review's counterexample: ``c1`` and ``c2`` sit over ONE physical
    store in the durable registry, but this process's registry holds only
    ``c1`` (a hand-authored ``collections_file`` row, or a CLI write after
    startup). Without the durable rows, ``c1`` would be evicted and ``c2``'s
    data dropped with it."""
    c1 = _rec("c1", collection="phys_shared", accessed_ago=1_000)
    c2 = _rec("c2", collection="phys_shared", accessed_ago=10)
    registry = _registry_for(c1)  # c2 is NOT served here
    assert protected(c1, registry, derived=DERIVED) is False  # the hole, registry-only
    assert protected(c1, registry, derived=DERIVED, records=[c1, c2]) is True
    victims, shortfall = choose_victims(
        [c1, c2], 1, now=NOW,
        protected=make_protected(registry, derived=DERIVED, records=[c1, c2]),
        registered={"c1"},
    )
    assert victims == []
    assert shortfall.reasons[REASON_PROTECTED] == 2  # c2 is protected by c1 too
    # A durable sibling over the ES leg only is the same case.
    h1 = _rec("h1", collection="phys_h1", text_index="shared_es")
    h2 = _rec("h2", collection="phys_h2", text_index="shared_es")
    assert protected(h1, _registry_for(h1), derived=DERIVED, records=[h1, h2]) is True


# --------------------------------------------------------------------------- #
# the act
# --------------------------------------------------------------------------- #


pytestmark_async = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_evict_swaps_to_dormant_before_dropping_and_records_the_reason():
    log: list = []
    store = _RecordingStore(log)
    rec = _rec("lib", accessed_ago=86_400 * 40)
    await store.put(rec.spec)
    await store.append_version("lib", 1)
    await store.touch_accessed(["lib"], rec.last_accessed_at)
    registry = _Registry(_entry_for(rec, log))
    changes: list[str] = []

    outcomes = await evict(registry, store, ["lib"], on_change=changes.append)

    assert log == [
        ("set_state", "lib", DORMANT, ACTIVE, True),  # the swap FIRST
        ("drop", "vectors:lib"),
        ("drop", "text:lib"),
    ]
    assert changes == ["lib"]  # the gate was told, once, right after the swap
    [o] = outcomes
    assert o.evicted and o.ok and o.state == DORMANT
    assert o.deleted == ["vectors", "text_index"] and o.absent == [] and o.failed == []
    row = await store.get("lib")
    assert row.state == DORMANT
    assert row.state_reason.startswith("evicted (LRU; last accessed ")
    assert rec.last_accessed_at in row.state_reason
    assert row.versions == [1]  # the archive bookkeeping is untouched


@pytest.mark.asyncio
async def test_evict_drops_nothing_when_the_swap_lost():
    """The row moved under us (a restore, a purge, a sibling evictor) between
    selection and the act: the stores are no longer ours to touch."""
    log: list = []
    store = _RecordingStore(log)
    rec = _rec("lib")
    await store.put(rec.spec)
    await store.append_version("lib", 1)
    await store.set_state("lib", RESTORING)  # someone else got there
    log.clear()
    [o] = await evict(_Registry(_entry_for(rec, log)), store, ["lib"])
    assert not o.evicted and o.state == RESTORING
    assert "not evictable now" in o.reason
    assert all(kind != "drop" for kind, *_ in log)

    # And the narrower race: evictable at the pre-check, gone at the CAS.
    await store.set_state("lib", ACTIVE)
    original = store.set_state

    async def stolen(cid, state, *, expect=None, reason=""):
        if expect == ACTIVE:
            await original(cid, DORMANT, reason="a sibling evictor")
        return await original(cid, state, expect=expect, reason=reason)

    store.set_state = stolen  # type: ignore[method-assign]
    log.clear()
    [o] = await evict(_Registry(_entry_for(rec, log)), store, ["lib"])
    assert not o.evicted and o.state == DORMANT
    assert "lost the active -> dormant swap" in o.reason
    assert all(kind != "drop" for kind, *_ in log)
    assert (await store.get("lib")).state_reason == "a sibling evictor"


@pytest.mark.asyncio
async def test_evict_keeps_dormant_and_names_the_leftover_when_a_drop_fails():
    log: list = []
    store = _RecordingStore(log)
    rec = _rec("lib")
    await store.put(rec.spec)
    await store.append_version("lib", 1)
    entry = _entry_for(rec, log)
    entry.text_index = _Store(log, "text:lib", fail=True)
    changes: list[str] = []

    [o] = await evict(_Registry(entry), store, ["lib"], on_change=changes.append)

    assert o.evicted and not o.ok and o.state == DORMANT
    assert o.deleted == ["vectors"] and o.failed == [("text_index", "RuntimeError: connection refused")]
    row = await store.get("lib")
    assert row.state == DORMANT
    assert "leftover physical store(s): text_index 'ragstack_lib_lib' still present" in row.state_reason
    assert "connection refused" in row.state_reason
    assert changes == ["lib", "lib"]  # invalidated after both row writes
    # The second write was a CAS on `dormant`: a restore that started in
    # between is never overwritten.
    assert log[-1] == ("set_state", "lib", DORMANT, DORMANT, True)


@pytest.mark.asyncio
async def test_evict_reports_absent_stores_and_unsupported_backends_honestly():
    log: list = []
    store = _RecordingStore(log)
    rec = _rec("lib")
    await store.put(rec.spec)
    await store.append_version("lib", 1)
    entry = _entry_for(rec, log)
    entry.vector_store = _Store(log, "vectors:lib", existed=False)
    entry.text_index = object()  # no drop_index at all
    [o] = await evict(_Registry(entry), store, ["lib"])
    assert o.evicted and o.absent == ["vectors"]
    assert o.failed == [("text_index", "backend does not support dropping")]
    assert (await store.get("lib")).state == DORMANT


@pytest.mark.asyncio
async def test_evict_rechecks_in_flight_and_skips_unregistered_and_unknown_ids():
    log: list = []
    store = _RecordingStore(log)
    rec = _rec("lib")
    await store.put(rec.spec)
    await store.append_version("lib", 1)
    registry = _Registry(_entry_for(rec, log))
    outcomes = await evict(registry, store, ["lib", "ghost", "unknown"], in_flight={"lib"})
    by_id = {o.collection_id: o for o in outcomes}
    assert not by_id["lib"].evicted and "in flight" in by_id["lib"].reason
    assert not by_id["ghost"].evicted and "not served by this process" in by_id["ghost"].reason
    assert not by_id["unknown"].evicted
    assert (await store.get("lib")).state == ACTIVE
    assert log == []  # no writes, no drops
