"""The API's use of the eviction policy (#359): gather what the pure policy in
:mod:`ragstack.ops.evict` needs from the live app, run it, and shape the
answer. Shared by ``POST /v1/collections`` (make room for ONE create), the
admin endpoint ``POST /v1/admin/collections/evict`` (evict ``need``, or plan)
and — through :class:`RestoreCapacity` — the lifecycle gate's restore
admission (#381: a restore takes a slot exactly as a create does).

What the policy needs and where it comes from:

* the registry rows — ``CollectionStore.list_records()``, after flushing the
  batched ``last_accessed_at`` touches (the LRU key is otherwise up to
  ``collection_access_flush_seconds`` stale);
* the in-flight set — ONE ``JobStore.active_collection_ids()`` query;
* the protected predicate — :func:`ragstack.ops.evict.make_protected` over the
  in-process registry plus the settings-derived default's store names
  (:func:`ragstack.api.deps.derived_default_stores`), so a spec that CLAIMS the
  legacy shared surface is refused even though no ``default`` entry exists to
  flag it (the hazard the module docstring of ``ops/evict.py`` spells out);
* the registered set — the ids this process holds store objects for.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from ragstack.api.collections import CollectionRegistry
from ragstack.api.deps import derived_default_stores
from ragstack.api.lifecycle import get_lifecycle_gate
from ragstack.collection_store import PHYSICAL, CollectionRecord, CollectionStore
from ragstack.config import settings
from ragstack.ops.evict import (
    REASONS,
    EvictionOutcome,
    Shortfall,
    Victim,
    choose_victims,
    evict,
    make_protected,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# response shape (contracts/schemas/eviction_response.json)
# --------------------------------------------------------------------------- #


class EvictionFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str
    error: str


class EvictionVictimInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection_id: str
    last_accessed_at: str
    idle_seconds: float | None
    state: str
    reason: str
    deleted: list[str]
    absent: list[str]
    failed: list[EvictionFailure]
    ok: bool


class ShortfallReasons(BaseModel):
    model_config = ConfigDict(extra="forbid")
    not_active: int = 0
    archive_pending: int = 0
    no_archive: int = 0
    in_flight: int = 0
    protected: int = 0
    unregistered: int = 0


class ShortfallInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    needed: int
    found: int
    reasons: ShortfallReasons


class EvictionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    need: int
    dry_run: bool
    evicted: int
    victims: list[EvictionVictimInfo]
    shortfall: ShortfallInfo


def shortfall_info(shortfall: Shortfall) -> ShortfallInfo:
    return ShortfallInfo(
        needed=shortfall.needed, found=shortfall.found,
        reasons=ShortfallReasons(**{k: shortfall.reasons.get(k, 0) for k in REASONS}),
    )


def _victim_info(victim: Victim, outcome: EvictionOutcome | None) -> EvictionVictimInfo:
    rec = victim.record
    if outcome is None:  # planned only (dry run)
        return EvictionVictimInfo(
            collection_id=rec.spec.id, last_accessed_at=rec.last_accessed_at,
            idle_seconds=victim.idle_seconds, state=rec.state, reason="",
            deleted=[], absent=[], failed=[], ok=True,
        )
    return EvictionVictimInfo(
        collection_id=outcome.collection_id, last_accessed_at=rec.last_accessed_at,
        idle_seconds=victim.idle_seconds, state=outcome.state, reason=outcome.reason,
        deleted=list(outcome.deleted), absent=list(outcome.absent),
        failed=[EvictionFailure(target=t, error=e) for t, e in outcome.failed],
        ok=outcome.ok,
    )


# --------------------------------------------------------------------------- #
# gathering + running
# --------------------------------------------------------------------------- #


async def _in_flight(app_state: Any) -> frozenset[str]:
    job_store = getattr(app_state, "job_store", None)
    fn = getattr(job_store, "active_collection_ids", None)
    if fn is None:
        return frozenset()
    return frozenset(await fn())


async def plan_eviction(
    app_state: Any, registry: CollectionRegistry, store: CollectionStore, need: int,
) -> tuple[list[Victim], Shortfall, frozenset[str]]:
    """Choose ``need`` victims against the live app (module docstring).
    Returns the victims, the shortfall and the in-flight set (handed on to
    :func:`ragstack.ops.evict.evict`, which re-checks it)."""
    gate = get_lifecycle_gate()
    if gate is not None and gate.tracker is not None:
        try:
            await gate.tracker.flush()
        except Exception:  # noqa: BLE001 — a stale LRU key is not a reason to refuse
            log.warning("eviction: flushing last-accessed touches failed", exc_info=True)
    records = await store.list_records()
    in_flight = await _in_flight(app_state)
    victims, shortfall = choose_victims(
        records, need, now=time.time(), in_flight=in_flight,
        protected=make_protected(registry, derived=derived_default_stores(), records=records),
        registered={e.id for e in registry.entries()},
    )
    return victims, shortfall, in_flight


async def run_eviction(
    app_state: Any, registry: CollectionRegistry, store: CollectionStore,
    victims: list[Victim], in_flight: frozenset[str],
) -> list[EvictionOutcome]:
    """Evict the chosen victims, invalidating the lifecycle gate's cached row
    after every registry write so readers see ``dormant`` at once.

    The in-flight set is RE-QUERIED here, immediately before the act: a job
    minted between selection and now (``in_flight`` is the plan-time answer,
    kept only as the fallback when the job store cannot answer) must still
    win. What remains is the window between a request passing the gate on a
    cached ``active`` row and its job being minted — the CAS can land inside
    it. That is closed only by the loader failing on the dropped store; the
    archive step still packs the embed outputs, so nothing is lost — the job
    fails and is re-run against the restored collection."""
    gate = get_lifecycle_gate()
    on_change = gate.invalidate if gate is not None else None
    try:
        in_flight = await _in_flight(app_state)
    except Exception:  # noqa: BLE001 — keep the plan-time answer rather than evict blind
        log.warning("eviction: re-querying in-flight jobs failed; using the plan-time set",
                    exc_info=True)
    return await evict(
        registry, store, [v.collection_id for v in victims],
        in_flight=in_flight, on_change=on_change,
    )


async def evict_collections(
    app_state: Any, registry: CollectionRegistry, store: CollectionStore,
    *, need: int, dry_run: bool,
) -> EvictionResponse:
    """The admin endpoint's whole body: plan, act unless ``dry_run``, shape."""
    victims, shortfall, in_flight = await plan_eviction(app_state, registry, store, need)
    if dry_run:
        return EvictionResponse(
            need=need, dry_run=True, evicted=0,
            victims=[_victim_info(v, None) for v in victims],
            shortfall=shortfall_info(shortfall),
        )
    outcomes = await run_eviction(app_state, registry, store, victims, in_flight)
    by_id = {o.collection_id: o for o in outcomes}
    return EvictionResponse(
        need=need, dry_run=False,
        evicted=sum(1 for o in outcomes if o.evicted),
        victims=[_victim_info(v, by_id.get(v.collection_id)) for v in victims],
        shortfall=shortfall_info(shortfall),
    )


def insufficient_storage(
    limit: int, shortfall: Shortfall | None, *, why: str | None = None
) -> HTTPException:
    """The create path's 507: the active bound is met and nothing could be
    evicted (``shortfall`` names why), or ``why`` says what else went wrong
    (the freed slot was taken concurrently; the chosen row moved under us) —
    those are transient, so they carry ``Retry-After``."""
    headers = None if why is None else {"Retry-After": "5"}
    if why is None:
        assert shortfall is not None
        why = f"no active collection can be evicted to make room — {shortfall.describe()}"
    return HTTPException(
        507,
        f"active collection bound reached ({limit}): {why}. Each active collection "
        "holds physical Qdrant/Elasticsearch resources (ADR-0003); the bound is met "
        "by evicting the least-recently-accessed collection whose archive is current. "
        "Delete unused collections, wait for in-flight ingests and pending archives, "
        "or have the operator raise MAX_COLLECTIONS",
        headers=headers,
    )


async def make_room_for_create(
    app_state: Any, registry: CollectionRegistry, store: CollectionStore, *, limit: int,
) -> list[EvictionOutcome]:
    """``POST /v1/collections`` at the bound: evict EXACTLY ONE least-recently-
    accessed archived collection, or raise 507 naming why none could be."""
    victims, shortfall, in_flight = await plan_eviction(app_state, registry, store, 1)
    if not victims:
        raise insufficient_storage(limit, shortfall)
    outcomes = await run_eviction(app_state, registry, store, victims, in_flight)
    if not any(o.evicted for o in outcomes):
        # The chosen row moved under us (a sibling evictor / restore / purge).
        raise insufficient_storage(
            limit, None,
            why="the collection chosen for eviction changed state concurrently "
                f"({'; '.join(o.reason for o in outcomes)}); retry",
        )
    return outcomes


def effective_limit(registry: CollectionRegistry, limit: int) -> int | None:
    """The bound as the durable store counts it: ``max_collections`` minus
    the slot the shared-surface pointer charges (it is not a registry row,
    so the store never counts it — #286 item 4). ``None`` = disabled
    (``MAX_COLLECTIONS=0``); ``0`` = refuse everything (a cap fully consumed
    by the reserved slot). The int|None sentinel is what the create path
    passes to ``store.create(limit=…)``; the restore admission passes the
    same value to ``store.begin_restore(limit=…)`` so both count alike."""
    reserved = 1 if any(e.is_shared_surface for e in registry.entries()) else 0
    return None if limit <= 0 else max(0, limit - reserved)


class RestoreCapacity:
    """What the lifecycle gate needs to admit a restore at the active bound
    (#381): the effective limit and the evict-one act, both over the LIVE app
    — ``app.state.collections`` / ``job_store`` and ``settings.max_collections``
    are read per call, never captured, so an operator's setting change and a
    registry rebuilt after startup are both honoured.

    ``make_room`` is the create path's sequence (:func:`make_room_for_create`)
    with a reason string instead of a 507: plan ONE victim (the in-flight
    re-query and the ``protected`` predicate exactly as #379 left them), act,
    and say why nothing was evicted — the gate turns that into 503 +
    ``Retry-After`` and leaves the row ``dormant``."""

    def __init__(self, app_state: Any) -> None:
        self._app_state = app_state

    @property
    def registry(self) -> CollectionRegistry:
        return self._app_state.collections

    @property
    def store(self) -> CollectionStore:
        return self._app_state.collection_store

    @property
    def configured(self) -> int:
        return int(settings.max_collections)

    def limit(self) -> int | None:
        return effective_limit(self.registry, self.configured)

    async def make_room(self) -> str | None:
        """Evict exactly one least-recently-accessed archived collection.
        ``None`` when one was evicted; otherwise why not."""
        victims, shortfall, in_flight = await plan_eviction(
            self._app_state, self.registry, self.store, 1
        )
        if not victims:
            return (f"the active collection bound ({self.configured}) is met and no active "
                    f"collection can be evicted to make room — {shortfall.describe()}")
        outcomes = await run_eviction(
            self._app_state, self.registry, self.store, victims, in_flight
        )
        if not any(o.evicted for o in outcomes):
            return ("the collection chosen for eviction changed state concurrently "
                    f"({'; '.join(o.reason for o in outcomes)}); retry")
        return None


def active_count(records: list[CollectionRecord], registry: CollectionRegistry) -> int:
    """The in-process fallback count when the durable store cannot reserve
    (inline/unset registry): every registry entry whose row holds its stores
    (``PHYSICAL``), an entry with no row (the settings-derived default)
    counting as present."""
    by_id = {r.spec.id: r for r in records}
    return sum(
        1 for e in registry.entries()
        if e.id not in by_id or by_id[e.id].state in PHYSICAL
    )
