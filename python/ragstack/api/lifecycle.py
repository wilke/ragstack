"""The lifecycle gate on the collection-resolution path (#358, phase 2 of #353).

Every collection-scoped read or ingest passes through :func:`enforce_access`
(``api/access.py``); once authorization allows it, the gate here asks the
registry what STATE the collection is in and answers accordingly:

``active`` / ``archiving``
    proceed (and record the access — batched, see :class:`AccessTracker`).
``dormant``
    submit a restore ONCE and answer **503** with ``Retry-After``. "Once" is a
    compare-and-swap on the registry row (``dormant → restoring``): N
    concurrent requests all CAS, exactly one wins and submits, the rest see
    ``restoring``. The restore runs AS THE CALLER (their bearer token), so a
    caller without one — keyless / API key — gets the 503 with a message
    saying a user token is required, and the row stays ``dormant``.
``restoring``
    **503** + ``Retry-After``. A row that has been ``restoring`` longer than
    ``collection_restore_timeout`` is presumed orphaned (its API process died
    before the watcher could flip it) and is CASed back to ``dormant`` so the
    next access restores again instead of 503ing forever.
``lost``
    **409** with the recorded reason; the owner repairs the archive and calls
    ``POST /v1/collections/{id}/restore`` (which MAY retry from ``lost``).

The check is ONE registry read, memoized per collection for
``collection_state_cache_seconds`` (the perf test pins it under 0.2 ms p95 and
asserts on the store's call count). Transitions made by this process
invalidate the entry immediately; a sibling process's change is seen within
the TTL.

Installed once per process (:func:`set_lifecycle_gate`, from the lifespan);
:func:`enforce_lifecycle` is a no-op when nothing is installed — an app
assembled without a lifespan (duck-typed ``app.state`` in tests) keeps the
pre-lifecycle behaviour, exactly as ``enforce_access`` does for authz when auth
is unconfigured.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from ragstack.api.security import Principal, gowe_caller
from ragstack.collection_store import (
    ACTIVE,
    ARCHIVING,
    DORMANT,
    LOST,
    RESTORING,
    AccessTracker,
    CollectionRecord,
    CollectionStore,
)
from ragstack.restore import CollectionRestorer, RestoreError

log = logging.getLogger(__name__)


def _parse_stamp(value: str) -> float | None:
    """ISO-8601 → unix seconds; ``None`` for an empty/unparseable stamp."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC).timestamp()
    except ValueError:
        return None


class LifecycleGate:
    """Per-process lifecycle enforcement over one registry (see the module
    docstring). ``restorer`` may be ``None`` on a deployment with no workflow
    engine — a dormant collection then answers 503 without submitting."""

    def __init__(
        self,
        store: CollectionStore,
        *,
        restorer: CollectionRestorer | None = None,
        tracker: AccessTracker | None = None,
        cache_seconds: float = 5.0,
        retry_after: int = 30,
        restore_timeout: float = 3600.0,
    ) -> None:
        self.store = store
        self.restorer = restorer
        self.tracker = tracker
        self.cache_seconds = max(0.0, float(cache_seconds))
        self.retry_after = max(1, int(retry_after))
        self.restore_timeout = max(1.0, float(restore_timeout))
        self._cache: dict[str, tuple[float, CollectionRecord | None]] = {}
        self._pending: set[asyncio.Task] = set()
        #: Registry reads performed (the perf test asserts the cache holds).
        self.reads = 0

    # -- cached registry read ---------------------------------------------- #

    async def record(self, cid: str) -> CollectionRecord | None:
        now = time.monotonic()
        hit = self._cache.get(cid)
        if hit is not None and now < hit[0]:
            return hit[1]
        self.reads += 1
        rec = await self.store.get(cid)
        if self.cache_seconds > 0:
            self._cache[cid] = (now + self.cache_seconds, rec)
        return rec

    def invalidate(self, cid: str | None = None) -> None:
        if cid is None:
            self._cache.clear()
        else:
            self._cache.pop(cid, None)

    # -- the gate ------------------------------------------------------------ #

    def _retry(self, cid: str, state: str, detail: str) -> HTTPException:
        return HTTPException(
            status_code=503,
            detail=f"collection {cid!r} is {state}: {detail}",
            headers={"Retry-After": str(self.retry_after)},
        )

    def is_stale_restore(self, rec: CollectionRecord) -> bool:
        started = _parse_stamp(rec.state_changed_at)
        # No stamp at all is anomalous (a hand-edited row): treat as stale so
        # it cannot wedge the collection.
        return started is None or (time.time() - started) > self.restore_timeout

    async def enforce(self, principal: Principal, cid: str, action: str = "read") -> None:
        """Raise 503/409 per the collection's state, or return (and record the
        access) when it may be served."""
        rec = await self.record(cid)
        if rec is None:
            return  # not a registry-tracked collection (the settings-derived default)
        state = rec.state
        if state in (ACTIVE, ARCHIVING):
            if self.tracker is not None:
                self.tracker.touch(cid)
            return
        if state == LOST:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"collection {cid!r} is lost: its archive could not be restored"
                    + (f" ({rec.state_reason})" if rec.state_reason else "")
                    + "; repair the Workspace archive and POST "
                    f"/v1/collections/{cid}/restore"
                ),
            )
        if state == RESTORING:
            # The watchdog is for a row ORPHANED by a dead process. One this
            # process is still watching is merely slow — resetting it would
            # submit a second restore over the same versions while the engine
            # is still running the first.
            watched = self.restorer is not None and self.restorer.watching(cid)
            if watched or not self.is_stale_restore(rec):
                raise self._retry(cid, state, "a restore is in progress; retry shortly")
            # Orphaned restore: un-stick it, then treat as dormant below.
            if await self.store.set_state(
                cid, DORMANT, expect=RESTORING,
                reason=f"restore watchdog: `restoring` since {rec.state_changed_at or '?'} "
                       f"exceeded {int(self.restore_timeout)}s",
            ):
                log.warning("collection %r: stale `restoring` row reset to dormant", cid)
            self.invalidate(cid)
            rec = await self.record(cid)
            if rec is None or rec.state != DORMANT:
                raise self._retry(cid, rec.state if rec else "?", "a restore is in progress; retry shortly")
        # dormant. The restore is submitted AS THE CALLER, which needs a BV-BRC
        # user token (the one rule, security.gowe_caller): an API-key / keyless
        # caller or a bearer identity from another issuer cannot trigger it.
        caller = gowe_caller(principal)
        if caller is None:
            raise self._retry(
                cid, DORMANT,
                "its stores were evicted and only its Workspace archive remains; "
                "a BV-BRC user (bearer) token is required to restore it — retry with one",
            )
        token, _subject = caller
        if self.restorer is None:
            raise self._retry(
                cid, DORMANT,
                "its stores were evicted and this server has no restore workflow configured",
            )
        won = await self.store.set_state(
            cid, RESTORING, expect=DORMANT,
            reason=f"restore requested by {principal.tenant} ({action})",
        )
        self.invalidate(cid)
        if won:
            self._spawn(self._submit(rec, token))
        raise self._retry(
            cid, RESTORING,
            "a restore from its Workspace archive was submitted; retry shortly",
        )

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _submit(self, rec: CollectionRecord, token: str) -> None:
        assert self.restorer is not None
        try:
            await self.restorer.submit(rec, token)
        except RestoreError as e:
            # The restorer already recorded the outcome on the row.
            log.warning("collection %r: restore not submitted: %s", rec.spec.id, e)
        finally:
            self.invalidate(rec.spec.id)

    async def drain(self) -> None:
        """Await in-flight submissions (and their watchers) — shutdown / tests."""
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)
        if self.restorer is not None:
            await self.restorer.drain()


_gate: LifecycleGate | None = None


def set_lifecycle_gate(gate: LifecycleGate | None) -> None:
    global _gate
    _gate = gate


def get_lifecycle_gate() -> LifecycleGate | None:
    return _gate


def reset_lifecycle_gate() -> None:
    global _gate
    _gate = None


async def enforce_lifecycle(principal: Principal, cid: str, action: str = "read") -> None:
    """The gate as ``enforce_access`` calls it — a no-op with none installed."""
    gate = _gate
    if gate is not None:
        await gate.enforce(principal, cid, action)
