"""#768 through the API: an upload whose every document the duplicate guard
refused reports ``duplicate_work: key=… existing_doc_id=…`` on its items,
never ``gowe submission FAILED``.

On a real engine such a run fails at the ``pack`` step — there is no chunk to
archive and ``ragstack-archive/1`` has no zero-row version (#357 residual) —
so ``archive_version.py`` exits ``NOTHING_TO_ARCHIVE_EXIT_CODE`` with each
document's receipt error first on stderr, and the engine copies that stderr
into the submission's failure record. A mixed run delivers its archive and the
refused document's row error reaches its item the ordinary way.
"""
from __future__ import annotations

import asyncio

import pytest

from ragstack.api.main import app
from ragstack.ingestion.receipts import (
    COMPLETED,
    NOTHING_TO_ARCHIVE_EXIT_CODE,
    DocRow,
    ShardReceipt,
    format_nothing_to_archive,
)
from tests.api.test_ingest_gowe_path import (  # noqa: F401 — the `gowe` fixture
    AUTH,
    _upload,
    gowe,
)

pytestmark = pytest.mark.asyncio

STAGED = "/tmp/gowe-ws-stage/sub_1"


def _refusal(existing: str) -> str:
    return f"duplicate_work: key=doi:10.1073/pnas.2018995118 existing_doc_id={existing}"


async def _poll(client, job_id: str) -> dict:
    body: dict = {}
    for _ in range(200):
        body = (await client.get(f"/v1/ingest/{job_id}", headers=AUTH)).json()
        if body["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.01)
    return body


async def test_all_refused_upload_reports_duplicate_work_per_item(client, gowe):  # noqa: F811
    stderr = format_nothing_to_archive([ShardReceipt("batch0", "t", COMPLETED, docs=[
        DocRow(doc_id="b", source=f"{STAGED}/b.pdf", error=_refusal("doc-a")),
        DocRow(doc_id="c", source=f"{STAGED}/c.pdf", error=_refusal("doc-a2")),
    ])])

    async def failed_wait(sub_id, **kw):
        return {"id": sub_id, "state": "FAILED", "output_state": "",
                "error": {"code": "TASK_FAILED", "message": "task pack failed",
                          "context": {"stderr": stderr + "\narchive: no chunk records",
                                      "exit_code": NOTHING_TO_ARCHIVE_EXIT_CODE}}}

    gowe["backend"].client.wait = failed_wait  # type: ignore[method-assign]
    r = await _upload(client, "b.pdf", "c.pdf")
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    body = await _poll(client, job_id)
    assert body["status"] == "failed"
    assert body["items"] == {"total": 2, "completed": 0, "failed": 2, "pending": 0}
    errors = sorted(i.error for i in app.state.job_store._items[job_id].values())
    assert errors == [_refusal("doc-a"), _refusal("doc-a2")]
    assert not any("gowe submission" in e for e in errors)
    job = await app.state.job_store.get(job_id)
    assert job.archive_ref == ""  # nothing delivered, no version recorded
    assert gowe["workspace"].reads == []


async def test_mixed_upload_reports_the_refused_item_and_completes(client, gowe):  # noqa: F811
    engine = gowe["engine"]
    real = engine.receipts_for

    def one_refused(version):
        (receipt,) = real(version)[:1]
        return [{**receipt, "n_docs": 2, "n_docs_failed": 1, "docs": [
            {"doc_id": "b", "source": f"{STAGED}/b.pdf", "error": _refusal("doc-a"),
             "chunk_ids": [], "metadata": {}},
            {"doc_id": "n", "source": f"{STAGED}/new.pdf", "error": "",
             "chunk_ids": ["n#0", "n#1"], "metadata": {}},
        ]}]

    engine.receipts_for = one_refused
    r = await _upload(client, "b.pdf", "new.pdf")
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    body = await _poll(client, job_id)
    assert body["status"] == "completed"
    assert body["items"] == {"total": 2, "completed": 1, "failed": 1, "pending": 0}
    errors = {i.source.rsplit("/", 1)[-1]: i.error
              for i in app.state.job_store._items[job_id].values()}
    assert errors == {"b.pdf": _refusal("doc-a"), "new.pdf": ""}
