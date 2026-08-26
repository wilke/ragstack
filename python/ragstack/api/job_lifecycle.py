"""The job row's request-scoped lifecycle guard (#415).

A route that mints a job row owns that row until it hands it to something that
will terminalize it — a background worker, or an engine watcher. Between the
``create()`` and that hand-off, ANY exit path that leaves the row non-terminal
wedges the caller: ``single_inflight_ingest`` (``api/deps.py``) counts
``accepted``/``running`` rows for the principal and 429s every later ingest
until the row goes stale (``jobstore.STALE_AFTER`` — 6 h). That is #415: one
500 during an upload locked a tenant out of ingest for six hours.

Per-call-site ``try``/``except`` was the shape that failed. It protects the
lines the author remembered to put inside it, and #415's own investigation
found two lines at ``routers/documents.py`` (the ``confine_to_root`` 400 and
the ``staging_dir.mkdir`` ``OSError``) sitting one line ABOVE the ``try`` that
was believed to cover them — invisible to a reviewer reading the diff. So the
guarantee lives in a scope instead: the row cannot be obtained without entering
it, and a call site written next quarter inherits the protection.

Usage::

    async with job_lifecycle(job_store, source="upload", tenant_id=t) as job:
        ...                                    # everything that can fail
        background_tasks.add_task(worker, job_store, job.job_id, ...)
        job.dispatched()                       # the worker now owns the row
        return IngestResponse(job_id=job.job_id, status=job.status)

``dispatched()`` is the one thing a new call site must remember, and forgetting
it fails SAFE — a spurious ``failed`` on a job that actually ran is visible and
retryable, the opposite polarity of today's bug (a spurious ``accepted`` that
wedges the tenant for 6 h invisibly).
"""
from __future__ import annotations

import asyncio
import logging
from types import TracebackType
from typing import Literal

from ragstack.jobstore import COMPLETED, FAILED, KIND_INGEST, IngestJob, JobStore

log = logging.getLogger(__name__)

#: The caller-safe ``error`` label for a scope that exited cleanly without ever
#: calling :meth:`TrackedJob.dispatched` — nothing owns the row, so it is
#: failed rather than left to hold the in-flight guard.
NEVER_DISPATCHED = "never-dispatched"


class TrackedJob:
    """The row :func:`job_lifecycle` yields, plus the hand-off signal.

    Exposes the two fields every call site reads off ``job_store.create()``'s
    return (``job_id``, ``status``) and the full row as :attr:`job`, so the
    context manager is a drop-in for the bare ``create()`` it replaces.
    """

    __slots__ = ("job", "_dispatched")

    def __init__(self, job: IngestJob) -> None:
        self.job = job
        self._dispatched = False

    @property
    def job_id(self) -> str:
        return self.job.job_id

    @property
    def status(self) -> str:
        """The status as minted (``accepted``) — what the 202 response echoes.
        Deliberately NOT a re-read: the response reports what the request did,
        and a background worker may already have moved the row on."""
        return self.job.status

    def dispatched(self) -> None:
        """Hand the row off: something that outlives this scope (a background
        task, an engine watcher) is now responsible for terminalizing it, so
        the scope exit leaves the row alone."""
        self._dispatched = True

    @property
    def was_dispatched(self) -> bool:
        return self._dispatched


class _JobLifecycle:
    """The async context manager :func:`job_lifecycle` returns."""

    def __init__(
        self,
        job_store: JobStore,
        *,
        source: str,
        tenant_id: str = "",
        collection_id: str = "",
        kind: str = KIND_INGEST,
    ) -> None:
        self._store = job_store
        self._source = source
        self._tenant_id = tenant_id
        self._collection_id = collection_id
        self._kind = kind
        self._tracked: TrackedJob | None = None

    async def __aenter__(self) -> TrackedJob:
        job = await self._store.create(
            source=self._source, tenant_id=self._tenant_id,
            collection_id=self._collection_id, kind=self._kind,
        )
        self._tracked = TrackedJob(job)
        return self._tracked

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        # BaseException, not Exception: `asyncio.CancelledError` derives from
        # BaseException, and a cancellation — a client disconnect or a server
        # timeout — is the MOST likely failure here, because the slow part
        # (streaming N files to the Workspace or to disk) is inside this scope.
        # The same argument, in the same words, is already made for the
        # collection-create reservation at api/routers/collections.py:694-712;
        # catching only Exception left exactly the leak that handler exists to
        # prevent. `exc_type is None` is covered too: a clean exit that never
        # called dispatched() has nobody owning the row either.
        tracked = self._tracked
        if tracked is None or tracked.was_dispatched:
            return False
        label = NEVER_DISPATCHED if exc is None else type(exc).__name__
        try:
            # Shielded so the very cancellation that triggered this unwind
            # cannot also kill the write that repairs it (the precedent is the
            # shielded withdrawal at routers/collections.py:703). The re-read
            # and the write are ONE coroutine inside the shield: shielding only
            # the write would leave the `get()` await as the place a re-
            # delivered cancellation lands.
            await asyncio.shield(self._terminalize(tracked.job_id, label))
        except Exception:  # noqa: BLE001 — best effort; the original must win
            # Layered: admission-time staleness and the startup sweep still
            # clear the row. Log so an operator can find it (the precedent is
            # routers/documents.py's "could not record the failure").
            log.warning(
                "job %s: the request failed and marking the job failed also failed; "
                "the row may hold the in-flight guard until it goes stale",
                tracked.job_id, exc_info=True,
            )
        return False  # never swallow — the caller's exception propagates

    async def _terminalize(self, job_id: str, label: str) -> None:
        """Mark the row FAILED, but ONLY if it is still non-terminal.

        The re-read is load-bearing, not defensive: several call sites already
        mark their own failures with a MORE specific label (a rejected upload
        is ``error="rejected"``; a refused graph submission is the engine
        error's class name) and then re-raise. Without this check the scope
        would clobber those labels with its own generic one.
        """
        row = await self._store.get(job_id)
        if row is not None and row.status in (COMPLETED, FAILED):
            return
        await self._store.update(job_id, status=FAILED, error=label)


def job_lifecycle(
    job_store: JobStore,
    *,
    source: str,
    tenant_id: str = "",
    collection_id: str = "",
    kind: str = KIND_INGEST,
) -> _JobLifecycle:
    """Mint a job row whose terminal state is guaranteed on every exit path.

    Arguments mirror :meth:`ragstack.jobstore.JobStore.create`. Yields a
    :class:`TrackedJob`; call :meth:`TrackedJob.dispatched` once the row's
    terminalization belongs to someone else. See the module docstring.
    """
    return _JobLifecycle(
        job_store, source=source, tenant_id=tenant_id, collection_id=collection_id, kind=kind,
    )
