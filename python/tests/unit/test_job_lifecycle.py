"""``api/job_lifecycle.job_lifecycle`` — the row reaches a terminal state on
every exit path from the scope that minted it (#415).

Pins: any exception type — ``Exception``, ``HTTPException``, and the two
``BaseException``s that matter (``CancelledError`` from a client disconnect or
a shutdown, ``KeyboardInterrupt``) — leaves the row ``failed`` and is re-raised
unchanged; ``dispatched()`` hands the row off and the scope then leaves it
alone; a clean exit that forgot ``dispatched()`` fails the row (the footgun,
failing safe); an already-terminal row keeps ITS label (the re-read — otherwise
the scope clobbers the specific ``"rejected"``/engine labels the call sites
set); and a marking write that itself fails logs a WARNING and lets the
original exception through rather than swallowing it.
"""
from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import HTTPException

from ragstack.api.job_lifecycle import NEVER_DISPATCHED, job_lifecycle
from ragstack.jobstore import ACCEPTED, COMPLETED, FAILED, KIND_GRAPH, InMemoryJobStore

pytestmark = pytest.mark.asyncio


def _boom(kind: type[BaseException]) -> BaseException:
    if kind is HTTPException:
        return HTTPException(status_code=500, detail="boom")
    return kind("boom")


RAISERS = [RuntimeError, HTTPException, asyncio.CancelledError, KeyboardInterrupt]


@pytest.mark.parametrize("exc_type", RAISERS, ids=lambda t: t.__name__)
async def test_any_exception_leaves_the_row_terminal_and_is_re_raised(exc_type):
    store = InMemoryJobStore()
    with pytest.raises(exc_type):
        async with job_lifecycle(store, source="upload", tenant_id="t1") as job:
            job_id = job.job_id
            assert job.status == ACCEPTED
            raise _boom(exc_type)

    row = await store.get(job_id)
    assert row is not None and row.status == FAILED
    assert row.error == exc_type.__name__  # caller-safe label: the class name
    # And the guard it was holding is released.
    assert await store.count_active("t1") == 0


async def test_dispatched_leaves_the_row_untouched():
    store = InMemoryJobStore()
    async with job_lifecycle(
        store, source="upload", tenant_id="t1", collection_id="lib1", kind=KIND_GRAPH
    ) as job:
        job.dispatched()
        job_id = job.job_id

    row = await store.get(job_id)
    assert row is not None and row.status == ACCEPTED and row.error == ""
    assert row.collection_id == "lib1" and row.kind == KIND_GRAPH and row.tenant_id == "t1"
    # Still in flight: the background worker owns it now, and the guard counts it.
    assert await store.count_active("t1", kind=KIND_GRAPH) == 1


async def test_dispatched_survives_a_later_exception_in_the_same_scope():
    """Once handed off, the worker owns the row — a raise on the way out of the
    scope (e.g. building the response) must not fail a job that IS running."""
    store = InMemoryJobStore()
    with pytest.raises(RuntimeError):
        async with job_lifecycle(store, source="upload", tenant_id="t1") as job:
            job.dispatched()
            job_id = job.job_id
            raise RuntimeError("after the hand-off")

    row = await store.get(job_id)
    assert row is not None and row.status == ACCEPTED


async def test_a_clean_exit_that_forgot_dispatched_fails_the_row():
    """The new footgun, pinned to its safe polarity: nothing owns the row, so
    it is failed (visible, retryable) rather than left to wedge the tenant."""
    store = InMemoryJobStore()
    async with job_lifecycle(store, source="upload", tenant_id="t1") as job:
        job_id = job.job_id

    row = await store.get(job_id)
    assert row is not None and row.status == FAILED and row.error == NEVER_DISPATCHED
    assert await store.count_active("t1") == 0


@pytest.mark.parametrize("status,label", [(FAILED, "rejected"), (COMPLETED, "")])
async def test_an_already_terminal_row_keeps_its_own_label(status, label):
    """A3: the call sites that mark their own failure use a MORE specific label
    ("rejected" for a refused upload, the engine error's class name for a
    refused submission) and then re-raise. The scope re-reads the row and must
    leave it exactly as they left it."""
    store = InMemoryJobStore()
    with pytest.raises(HTTPException):
        async with job_lifecycle(store, source="upload", tenant_id="t1") as job:
            await store.update(job.job_id, status=status, error=label)
            job_id = job.job_id
            raise HTTPException(status_code=415, detail="unsupported")

    row = await store.get(job_id)
    assert (row.status, row.error) == (status, label)


async def test_a_failing_marking_write_logs_and_lets_the_original_through(caplog):
    class Broken(InMemoryJobStore):
        async def update(self, job_id: str, **fields: object) -> None:
            raise OSError("job store is unwritable")

    store = Broken()
    caplog.set_level(logging.WARNING)
    with pytest.raises(RuntimeError, match="the original"):
        async with job_lifecycle(store, source="upload", tenant_id="t1") as job:
            job_id = job.job_id
            raise RuntimeError("the original")

    row = await store.get(job_id)
    assert row is not None and row.status == ACCEPTED  # the write really did fail
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and any(job_id in r.getMessage() for r in warnings)
    assert any("marking the job failed also failed" in r.getMessage() for r in warnings)


async def test_a_failing_re_read_also_logs_rather_than_swallowing():
    class Blind(InMemoryJobStore):
        async def get(self, job_id, tenant_id=None, *, is_admin=False):
            raise OSError("job store is unreadable")

    store = Blind()
    with pytest.raises(RuntimeError, match="the original"):
        async with job_lifecycle(store, source="upload", tenant_id="t1"):
            raise RuntimeError("the original")


async def test_the_marking_write_is_shielded_from_a_re_delivered_cancellation():
    """The write that repairs the row must survive the cancellation that
    triggered the unwind — the same shielding the collection-create
    reservation uses (api/routers/collections.py:703).

    A single ``cancel()`` is delivered once, so a naive cleanup await would
    survive it and this test would be vacuous. What shielding actually buys is
    survival of a RE-delivered cancellation — a shutdown escalating, or an
    ``asyncio.timeout`` that fires again while the handler is unwinding. So
    cancel a second time, while the repair write is mid-flight: unshielded,
    the write dies inside its own suspension point and the row stays
    ``accepted``, holding the guard for 6 h.
    """
    started, writing = asyncio.Event(), asyncio.Event()
    seen: dict[str, str] = {}

    class Slow(InMemoryJobStore):
        async def update(self, job_id: str, **fields: object) -> None:
            writing.set()
            await asyncio.sleep(0.05)  # a real suspension point in the write
            await super().update(job_id, **fields)

    store = Slow()

    async def request() -> None:
        async with job_lifecycle(store, source="upload", tenant_id="t1") as job:
            seen["job_id"] = job.job_id
            started.set()
            await asyncio.sleep(3600)  # the slow part: streaming the upload

    task = asyncio.get_running_loop().create_task(request())
    await started.wait()
    task.cancel()
    await writing.wait()
    task.cancel()  # the second delivery: this is what the shield defends
    with pytest.raises(asyncio.CancelledError):
        await task
    # The shielded write outlives the cancelled task; give it its tick.
    await asyncio.sleep(0.2)
    row = await store.get(seen["job_id"])
    assert row is not None and row.status == FAILED
    assert row.error == "CancelledError"
