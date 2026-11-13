"""#632: ``archive_pending`` is set on every GoWe failure that may have written.

The flag means "the stores may be ahead of the archive" and eviction refuses a
row that carries it (ADR-0005 §5). Before #632 only ``OutputStagingFailed`` set
it, so a scattered submission that failed after sibling batches had upserted
(a cap refusal, a batch that failed after its retries), an exception after the
engine accepted the submission, or a ``GoWeContractError`` on a DELIVERED
archive left the row evictable — and a row with an earlier recorded version
was then eviction's candidate with chunks that exist in no version.

Each test runs one successful upload first, so the row has ``versions == [1]``:
without the flag it is evictable, which is exactly the loss the flag prevents.

Not cleared by a later successful archive: a version is a per-job delta, so it
does not cover the failed load's chunks (pinned below).
"""
from __future__ import annotations

import pytest

from ragstack.api.main import app
from ragstack.collection_store import evictable
from ragstack.ingestion.gowe_client import GoWeError
from ragstack.ingestion.receipts import (
    COMPLETED,
    NOTHING_TO_ARCHIVE_EXIT_CODE,
    DocRow,
    ShardReceipt,
    format_nothing_to_archive,
)
from tests.api.test_ingest_gowe_path import (  # noqa: F401 — the `gowe` fixture
    _upload,
    gowe,
)

pytestmark = pytest.mark.asyncio


async def _seed_one_version(client, gowe) -> None:  # noqa: F811
    r = await _upload(client, "seed.pdf")
    assert r.status_code == 202, r.text
    row = await gowe["store"]._inner.get("lib1")
    assert row.versions == [1] and row.archive_pending is False and evictable(row)


async def _job(r):
    assert r.status_code == 202, r.text
    return await app.state.job_store.get(r.json()["job_id"])


def _failed_wait(state: str, error: dict | None = None):
    async def wait(sub_id, **kw):
        return {"id": sub_id, "state": state, "output_state": "",
                "error": error or {"code": "TASK_FAILED",
                                   "message": "step 'ingest' task failed",
                                   "context": {"exit_code": 1, "stderr": "boom"}}}
    return wait


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED"])
async def test_a_non_completed_submission_flags_the_row(client, gowe, state):  # noqa: F811
    """Sibling batches may have upserted before the run failed; `pack` never
    ran, so no version holds those chunks."""
    await _seed_one_version(client, gowe)
    gowe["backend"].client.wait = _failed_wait(state)  # type: ignore[method-assign]
    job = await _job(await _upload(client, "a.pdf", "b.pdf"))
    assert job.status == "failed" and job.archive_ref == ""
    row = await gowe["store"]._inner.get("lib1")
    assert row.versions == [1]  # the reserved version 2 names no folder
    assert row.archive_pending is True and not evictable(row)


async def test_nothing_to_archive_does_not_flag_the_row(client, gowe):  # noqa: F811
    """#768: every document failed on its own, so nothing was upserted — the
    stores are not ahead of the archive."""
    await _seed_one_version(client, gowe)
    stderr = format_nothing_to_archive([ShardReceipt("batch0", "t", COMPLETED, docs=[
        DocRow(doc_id="a", source="/stage/a.pdf", error="no text"),
    ])])
    gowe["backend"].client.wait = _failed_wait("FAILED", {  # type: ignore[method-assign]
        "code": "TASK_FAILED", "message": "step 'pack' task failed with exit code -1",
        "context": {"step_id": "pack", "exit_code": -1, "stderr":
                    f"execute: apptainer command failed (exit {NOTHING_TO_ARCHIVE_EXIT_CODE}): "
                    + stderr}})
    job = await _job(await _upload(client, "a.pdf"))
    assert job.status == "failed"
    row = await gowe["store"]._inner.get("lib1")
    assert row.archive_pending is False and evictable(row)
    assert gowe["store"].calls.get("set_archive_pending", 0) == 0


async def test_a_contract_error_on_a_delivered_archive_flags_the_row(client, gowe):  # noqa: F811
    """The run completed and delivered; its receipts are unusable, so the
    version is not recorded — the stores hold chunks the row does not account
    for."""
    await _seed_one_version(client, gowe)
    gowe["workspace"].empty_receipts = True
    job = await _job(await _upload(client, "a.pdf", "b.pdf"))
    assert job.status == "failed" and job.error == "GoWeContractError"
    row = await gowe["store"]._inner.get("lib1")
    assert row.versions == [1]
    assert row.archive_pending is True and not evictable(row)


async def test_a_gowe_error_after_submit_flags_the_row(client, gowe):  # noqa: F811
    """A poll failure / timeout after the engine accepted the submission: its
    tasks may still be writing."""
    await _seed_one_version(client, gowe)

    async def wait(sub_id, **kw):
        raise GoWeError(f"gowe submission {sub_id} did not finish within 5s")

    gowe["backend"].client.wait = wait  # type: ignore[method-assign]
    job = await _job(await _upload(client, "a.pdf"))
    assert job.status == "failed" and job.error == "GoWeError"
    row = await gowe["store"]._inner.get("lib1")
    assert row.archive_pending is True and not evictable(row)


async def test_any_exception_after_submit_flags_the_row(client, gowe):  # noqa: F811
    await _seed_one_version(client, gowe)

    async def wait(sub_id, **kw):
        raise RuntimeError("unexpected")

    gowe["backend"].client.wait = wait  # type: ignore[method-assign]
    job = await _job(await _upload(client, "a.pdf"))
    assert job.status == "failed" and job.error == "RuntimeError"
    row = await gowe["store"]._inner.get("lib1")
    assert row.archive_pending is True and not evictable(row)


async def test_a_failure_before_submit_does_not_flag_the_row(client, gowe):  # noqa: F811
    """Workflow registration failed: nothing was submitted, nothing written."""
    await _seed_one_version(client, gowe)
    gowe["engine"].fail = "transport"
    job = await _job(await _upload(client, "a.pdf"))
    assert job.status == "failed" and job.error == "GoWeError"
    assert len(gowe["engine"].submissions) == 1  # the seed's only
    row = await gowe["store"]._inner.get("lib1")
    assert row.archive_pending is False and evictable(row)
    assert gowe["store"].calls.get("set_archive_pending", 0) == 0


async def test_a_later_successful_archive_does_not_clear_the_flag(client, gowe):  # noqa: F811
    """Version 3 holds only its own job's chunks, not the failed load's: the
    stores are still ahead of the archive, so the row must stay unevictable."""
    await _seed_one_version(client, gowe)
    real_wait = gowe["backend"].client.wait
    gowe["backend"].client.wait = _failed_wait("FAILED")  # type: ignore[method-assign]
    await _job(await _upload(client, "a.pdf"))
    gowe["backend"].client.wait = real_wait  # type: ignore[method-assign]
    job = await _job(await _upload(client, "b.pdf"))
    assert job.status == "completed"
    row = await gowe["store"]._inner.get("lib1")
    assert row.versions == [1, 3]
    assert row.archive_pending is True and not evictable(row)


async def test_a_flag_that_cannot_be_written_is_not_fatal(client, gowe, monkeypatch):  # noqa: F811
    await _seed_one_version(client, gowe)

    async def broken(cid, pending):
        raise RuntimeError("registry down")

    monkeypatch.setattr(gowe["store"]._inner, "set_archive_pending", broken)
    gowe["backend"].client.wait = _failed_wait("FAILED")  # type: ignore[method-assign]
    job = await _job(await _upload(client, "a.pdf"))
    assert job.status == "failed"  # the run's own outcome, not the flag's failure
    assert gowe["store"].calls.get("set_archive_pending") == 1
