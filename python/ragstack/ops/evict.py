"""LRU eviction of archived collections (#359, phase 4 of #353).

A collection slot — 8 segments, ~160 memory mappings, a thread pool, an ES
shard — is held forever by an idle collection, and collection *count* is the
binding per-tenant constraint (ADR-0003). ``max_collections`` therefore bounds
**active** collections, and when the bound is met the least-recently-accessed
collection whose archive is current is made ``dormant``: its physical stores
are dropped, only its Workspace archive remains, and the first access restores
it (:mod:`ragstack.api.lifecycle`). Nothing is ever deleted that exists
nowhere else.

Two halves, kept apart on purpose:

:func:`choose_victims`
    Pure and synchronous — the policy. Least-recently-accessed first among the
    records that are :func:`~ragstack.collection_store.evictable` (``active ∧
    ¬archive_pending ∧ versions non-empty``) AND not in flight (an
    ``accepted``/``running`` ingest job targets them) AND not
    :func:`protected`. Returns as many victims as it can plus a
    :class:`Shortfall` counting, per reason, why the rest were ineligible.
    The perf test pins selection over 1,000 rows under 5 ms.
:func:`evict`
    The act, per victim, in this order and never another: compare-and-swap the
    registry row ``active → dormant`` FIRST (concurrent readers immediately get
    503 + ``Retry-After`` from the lifecycle gate), THEN drop the Qdrant
    collection and the ES index. Nothing is dropped when the CAS lost — the
    row moved under us (a restore, a purge, a sibling evictor) and the stores
    are no longer ours to touch. Deletion is best-effort per target like the
    purge path: a failed drop is logged and the row keeps ``state=dormant``
    with a ``state_reason`` naming the leftover physical store, which the store
    inventory (#299) reports as claimed-but-present.

**The hazard this module is designed against** (the #376 review): a registry
spec that *claims* the settings-derived default's physical stores (the
``claimed_by`` branch of the registry build, ADR-0002 decision 5) sits over
the LEGACY MULTI-TENANT DATA — every tenant's chunks, isolated only by the
per-chunk ``tenant_id``. Evicting it would destroy that data at eviction time
(the restore side would be safe: by then the stores are empty, and the archive
holds only the owner's versions). :func:`protected` is the one predicate that
says no: a record whose physical stores are the derived default's, or an
``is_shared_surface`` entry's, or any store another registry id also claims
(the purge guard's ``_shared_store_users`` case — dropping one id's store
destroys the other's data), is never a candidate.

The graph leg: one graph backend holds every collection's triples and the
``GraphStore`` protocol has no per-collection delete (only ``delete_by_doc``),
so an evicted collection's triples stay until the per-collection delete lands
(tracked as #<pending>, the graph leg of #353).
Reads of them are already collection-scoped, so nothing leaks meanwhile.

This module imports nothing from ``ragstack.api``: the registry is duck-typed
(``entries()`` / ``has()`` / ``resolve()`` over entries with ``collection``,
``es_index()``, ``is_shared_surface``, ``vector_store``, ``text_index``) and
gate invalidation is an ``on_change`` callback, the ``CollectionRestorer``
precedent.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ragstack.collection_store import (
    ACTIVE,
    DORMANT,
    CollectionRecord,
    CollectionStore,
    evictable,
)

log = logging.getLogger(__name__)

# Why a record was NOT chosen — the keys of ``Shortfall.reasons``, in the
# order the checks run. Mirrored by contracts/schemas/eviction_response.json.
REASON_NOT_ACTIVE = "not_active"          #: state != active (dormant, restoring, archiving, lost)
REASON_ARCHIVE_PENDING = "archive_pending"  #: the last load's archive step failed
REASON_NO_ARCHIVE = "no_archive"          #: no version was ever archived
REASON_IN_FLIGHT = "in_flight"            #: an accepted/running ingest job targets it
REASON_PROTECTED = "protected"            #: its stores are the shared surface's / shared
REASON_UNREGISTERED = "unregistered"      #: not served by this process's registry
REASONS: tuple[str, ...] = (
    REASON_NOT_ACTIVE, REASON_ARCHIVE_PENDING, REASON_NO_ARCHIVE,
    REASON_IN_FLIGHT, REASON_PROTECTED, REASON_UNREGISTERED,
)

# Physical-store targets, named as the purge report names them.
TARGET_VECTORS = "vectors"
TARGET_TEXT = "text_index"


def parse_stamp(value: str) -> float | None:
    """ISO-8601 → unix seconds; ``None`` for an empty/unparseable stamp."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC).timestamp()
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# the policy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Shortfall:
    """How far :func:`choose_victims` fell short of ``need`` and why: a count
    per :data:`REASONS` key over every record examined (the counts are over
    ALL records, not just the ones inspected before ``need`` was met, so a
    dry-run plan shows the whole picture). Eligible records beyond ``need``
    appear in neither ``victims`` nor ``reasons`` — they were simply not
    needed — so ``found + sum(reasons)`` may be less than the record count."""

    needed: int
    found: int
    reasons: dict[str, int] = field(default_factory=lambda: dict.fromkeys(REASONS, 0))

    @property
    def missing(self) -> int:
        return max(0, self.needed - self.found)

    def describe(self) -> str:
        """One line for a log or an HTTP detail: ``needed 1, found 0
        (not_active=2, archive_pending=1, ...)``."""
        counts = ", ".join(f"{k}={self.reasons.get(k, 0)}" for k in REASONS)
        return f"needed {self.needed}, found {self.found} ({counts})"


@dataclass(frozen=True)
class Victim:
    """A chosen record plus how long it has been idle at selection time
    (``None`` when it carries no usable stamp at all)."""

    record: CollectionRecord
    idle_seconds: float | None

    @property
    def collection_id(self) -> str:
        return self.record.spec.id


def lru_stamp(rec: CollectionRecord) -> float:
    """The LRU key: ``last_accessed_at``, else ``created_at`` (a collection
    nobody has touched since it was made is exactly as idle as its age), else
    ``-inf`` so a row with no stamp at all sorts first — never accessed, as far
    as the registry knows."""
    stamp = parse_stamp(rec.last_accessed_at)
    if stamp is None:
        stamp = parse_stamp(rec.created_at)
    return -math.inf if stamp is None else stamp


def ineligible_reason(
    rec: CollectionRecord,
    *,
    in_flight: Collection[str],
    protected: Callable[[CollectionRecord], bool],
    registered: Collection[str] | None = None,
) -> str | None:
    """Why ``rec`` may not be evicted — a :data:`REASONS` key — or ``None``
    when it may. The checks run cheapest-first; the state checks are exactly
    :func:`~ragstack.collection_store.evictable`, split so each failure has a
    name."""
    if rec.state != ACTIVE:
        return REASON_NOT_ACTIVE
    if rec.archive_pending:
        return REASON_ARCHIVE_PENDING
    if not rec.versions:
        return REASON_NO_ARCHIVE
    assert evictable(rec)  # the three checks above ARE the predicate
    cid = rec.spec.id
    if cid in in_flight:
        return REASON_IN_FLIGHT
    if protected(rec):  # the safety property is named before the local one
        return REASON_PROTECTED
    if registered is not None and cid not in registered:
        return REASON_UNREGISTERED
    return None


def choose_victims(
    records: Iterable[CollectionRecord],
    need: int,
    *,
    now: float,
    in_flight: Collection[str] = frozenset(),
    protected: Callable[[CollectionRecord], bool] = lambda rec: False,
    registered: Collection[str] | None = None,
) -> tuple[list[Victim], Shortfall]:
    """Pick up to ``need`` eviction victims, least-recently-accessed first.

    Pure and synchronous: ``in_flight`` (ids with an ``accepted``/``running``
    job — one ``JobStore.active_collection_ids()`` query) and ``protected``
    (:func:`make_protected` over the live registry) are gathered by the caller.
    ``registered``, when given, is the set of ids this process can actually
    drop the stores of; a record outside it counts as ``unregistered``.
    ``now`` (unix seconds) is only used to report each victim's idle time.
    Ties on the stamp break on the id, so the order is deterministic."""
    if need < 0:
        raise ValueError(f"need must be >= 0 (got {need})")
    reasons = dict.fromkeys(REASONS, 0)
    victims: list[Victim] = []
    ordered = sorted(records, key=lambda r: (lru_stamp(r), r.spec.id))
    for rec in ordered:
        why = ineligible_reason(
            rec, in_flight=in_flight, protected=protected, registered=registered
        )
        if why is not None:
            reasons[why] += 1
            continue
        if len(victims) < need:
            stamp = lru_stamp(rec)
            idle = None if stamp == -math.inf else max(0.0, now - stamp)
            victims.append(Victim(record=rec, idle_seconds=idle))
    return victims, Shortfall(needed=need, found=len(victims), reasons=reasons)


# --------------------------------------------------------------------------- #
# protection: the stores eviction must never drop
# --------------------------------------------------------------------------- #


def protected_stores(registry: Any, derived: tuple[str, str] | None) -> frozenset[str]:
    """The physical store names (Qdrant collection / ES index) eviction must
    never drop: the settings-derived default's two legs (``derived`` —
    present whether or not a ``default`` entry was synthesised, which is the
    whole point: when a spec CLAIMS those stores no shared-surface entry
    exists to flag them) plus both legs of every ``is_shared_surface``
    entry."""
    legs: set[str] = set()
    if derived is not None:
        legs.update(leg for leg in derived if leg)
    for entry in registry.entries():
        if getattr(entry, "is_shared_surface", False):
            legs.add(entry.collection)
            legs.add(entry.es_index())
    return frozenset(legs)


def make_protected(
    registry: Any,
    *,
    derived: tuple[str, str] | None = None,
    records: Iterable[CollectionRecord] = (),
) -> Callable[[CollectionRecord], bool]:
    """Build :func:`protected` for ``registry`` ONCE (the leg sets are
    precomputed) so selection over a thousand records stays O(n).

    ``records`` are the DURABLE registry rows: a sibling over the same store
    may exist only there (a hand-authored ``collections_file`` row, a CLI
    write after startup — the window ``_shared_store_users`` exists for) and
    not in this process's registry, and it must still protect the store."""
    legs = protected_stores(registry, derived)
    claimants: dict[str, set[str]] = {}
    for entry in registry.entries():
        claimants.setdefault(entry.collection, set()).add(entry.id)
        claimants.setdefault(entry.es_index(), set()).add(entry.id)
    for rec in records:
        claimants.setdefault(rec.spec.collection, set()).add(rec.spec.id)
        claimants.setdefault(rec.spec.es_index(), set()).add(rec.spec.id)

    def _protected(rec: CollectionRecord) -> bool:
        spec = rec.spec
        own = (spec.collection, spec.es_index())
        if any(leg in legs for leg in own):
            return True
        # Sibling-shared: another registry id — in this process or only in
        # the durable store — serves either leg (a hand-authored alias, or
        # the half-shared shape the purge guard also refuses). Dropping this
        # id's store would destroy the other's data.
        return any(claimants.get(leg, set()) - {spec.id} for leg in own)

    return _protected


def protected(
    rec: CollectionRecord,
    registry: Any,
    *,
    derived: tuple[str, str] | None = None,
    records: Iterable[CollectionRecord] = (),
) -> bool:
    """May eviction NEVER drop this record's physical stores?

    True when the record's Qdrant collection or ES index is one of the
    settings-derived default's legs (``derived`` = ``(collection, es_index)``
    from ``deps.derived_default_stores()``) — the legacy shared surface
    carrying every tenant's data, whether served by the synthesised
    ``default`` entry or by a spec that claims it (ADR-0002 decision 5's
    ``claimed_by`` branch) — or one of any ``is_shared_surface`` entry's legs,
    or a store another registry id also claims (in ``registry`` or among the
    durable ``records``). See the module docstring for why the claimed case
    is the dangerous one."""
    return make_protected(registry, derived=derived, records=records)(rec)


# --------------------------------------------------------------------------- #
# the act
# --------------------------------------------------------------------------- #


@dataclass
class EvictionOutcome:
    """What happened to ONE chosen victim. ``evicted`` is whether THIS call
    won the ``active → dormant`` swap; the three target lists mirror the purge
    report (a drop that landed cannot be undone by one that failed, so each is
    reported on its own)."""

    collection_id: str
    evicted: bool
    state: str
    reason: str = ""
    deleted: list[str] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.evicted and not self.failed


async def drop_stores(entry: Any) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """Drop ``entry``'s Qdrant collection and ES index, best-effort per target,
    never raising: ``(deleted, absent, failed)`` with ``failed`` as
    ``(target, error)`` pairs. Shared with the purge path so the two cannot
    drift on what "dropped" means."""
    deleted: list[str] = []
    absent: list[str] = []
    failed: list[tuple[str, str]] = []
    drops: list[tuple[str, Any]] = [
        (TARGET_VECTORS, getattr(entry.vector_store, "drop_collection", None)),
        (TARGET_TEXT, getattr(entry.text_index, "drop_index", None)),
    ]
    for target, fn in drops:
        if fn is None:
            failed.append((target, "backend does not support dropping"))
            continue
        try:
            existed = await fn()
        except Exception as e:  # noqa: BLE001 — reported, not raised: fail soft + honest
            log.warning("drop %r: %s failed: %s", getattr(entry, "id", "?"), target, e)
            failed.append((target, f"{type(e).__name__}: {e}"))
        else:
            (deleted if existed else absent).append(target)
    return deleted, absent, failed


def _physical_name(entry: Any, target: str) -> str:
    return entry.collection if target == TARGET_VECTORS else entry.es_index()


async def evict(
    registry: Any,
    store: CollectionStore,
    ids: Iterable[str],
    *,
    in_flight: Collection[str] = frozenset(),
    on_change: Callable[[str], None] | None = None,
) -> list[EvictionOutcome]:
    """Make each of ``ids`` dormant: CAS ``active → dormant`` on the registry
    row FIRST, then drop the physical stores (module docstring). ``on_change``
    is called with the id after every row write (the lifecycle gate's cache
    invalidation). ``in_flight`` is re-checked here as well as at selection:
    a job accepted between the two must still win."""
    outcomes: list[EvictionOutcome] = []
    for cid in ids:
        if not registry.has(cid):
            outcomes.append(EvictionOutcome(
                cid, evicted=False, state="?",
                reason="not served by this process's registry; nothing to drop",
            ))
            continue
        rec = await store.get(cid)
        if rec is None:
            outcomes.append(EvictionOutcome(cid, evicted=False, state="?", reason="no registry row"))
            continue
        if not evictable(rec):
            outcomes.append(EvictionOutcome(
                cid, evicted=False, state=rec.state,
                reason=f"not evictable now (state={rec.state}, archive_pending="
                       f"{rec.archive_pending}, versions={len(rec.versions)})",
            ))
            continue
        if cid in in_flight:
            outcomes.append(EvictionOutcome(
                cid, evicted=False, state=rec.state, reason="an ingest job is in flight",
            ))
            continue
        idle_since = rec.last_accessed_at or rec.created_at or "never"
        reason = f"evicted (LRU; last accessed {idle_since})"
        # 1. The swap. From here on every reader gets 503 + Retry-After; if it
        #    lost, the row moved under us and the stores are not ours to drop.
        won = await store.set_state(cid, DORMANT, expect=ACTIVE, reason=reason)
        if on_change is not None:
            on_change(cid)
        if not won:
            current = await store.get(cid)
            outcomes.append(EvictionOutcome(
                cid, evicted=False, state=current.state if current else "?",
                reason="lost the active -> dormant swap; stores left untouched",
            ))
            continue
        # 2. The drops, best-effort per target.
        entry = registry.resolve(cid)
        deleted, absent, failed = await drop_stores(entry)
        if failed:
            leftovers = "; ".join(
                f"{target} {_physical_name(entry, target)!r} still present ({error})"
                for target, error in failed
            )
            reason = f"{reason}; leftover physical store(s): {leftovers}"
            # Keep `dormant` (the row must not claim stores it half has) but
            # record what is left for the store inventory; the CAS guard means
            # a restore that already started is never overwritten.
            await store.set_state(cid, DORMANT, expect=DORMANT, reason=reason)
            if on_change is not None:
                on_change(cid)
            log.warning("evict %r: dormant with leftovers: %s", cid, leftovers)
        else:
            log.info("evicted collection %r (store=%s): deleted=%s absent=%s",
                     cid, entry.collection, deleted, absent)
        outcomes.append(EvictionOutcome(
            cid, evicted=True, state=DORMANT, reason=reason,
            deleted=deleted, absent=absent, failed=failed,
        ))
    return outcomes
