"""Per-DOCUMENT receipt mapping on the GoWe user path (#203 2b, Option B).

Under batch-per-task the archive's ``receipt.json`` holds one ``ShardReceipt``
per BATCH, so the backend can no longer map receipts to work items by position.
It maps each item to its row in ``docs`` by source basename:

* a mixed batch yields the exact per-document status and chunk ids;
* a failed batch attributes the batch error to every document of that batch
  (a document with its own error keeps it);
* the constant ``NO_TEXT_ERROR`` reaches the job's per-item error VERBATIM — the
  #377 review's gap — so ``GROUP BY error`` counts scanned PDFs on this path;
* an Option-A archive (one receipt per item) still maps positionally;
* receipts naming none of the items are a contract error, never "all failed";
* the poll interval is per submission: fast for an upload-sized one.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from ragstack.ingestion.gowe_backend import (
    BATCH_FAILED,
    NO_READABLE_RECEIPT,
    NO_RECEIPT_ENTRY,
    GoWeBackend,
    GoWeContractError,
    map_receipt_entries,
)
from ragstack.ingestion.loaders import NO_TEXT_ERROR
from ragstack.ingestion.manifest import WorkItem
from ragstack.ingestion.receipts import (
    COMPLETED,
    FAILED,
    NO_CHUNKS_ERROR,
    DocRow,
    ShardReceipt,
)
from ragstack.workspace import WorkspaceNotFound

CWL_TEXT = "cwlVersion: v1.2\nclass: Workflow\n"
TOKEN = "un=alice@patricbrc.org|tokenid=t-1|expiry=9999999999|sig=SECRET"
HOME = "/alice@patricbrc.org/home/.ragstack/collections/lib1"
DEST = f"ws://{HOME}/versions/"
RECEIPT_PATH = f"{HOME}/versions/7/receipt.json"
STAGED = "/tmp/gowe-ws-stage/sub_1"  # the engine pre-stages ws:// inputs under their basename


def _wi(name: str) -> WorkItem:
    path = f"{HOME}/sources/{name}"
    return WorkItem(item_id=path, source=f"ws://{path}")


def _row(name: str, *ids: str, error: str = "") -> DocRow:
    return DocRow(doc_id=f"id-{name}", source=f"{STAGED}/{name}", chunk_ids=list(ids), error=error)


def _batch(shard_id: str, rows: list[DocRow], *, status: str | None = None, error: str = "") -> ShardReceipt:
    failed = sum(1 for r in rows if r.error)
    ids = [c for r in rows for c in r.chunk_ids]
    st = status if status is not None else (COMPLETED if failed < len(rows) else FAILED)
    return ShardReceipt(shard_id, "public", st, n_docs=len(rows), n_chunks=len(ids),
                        chunk_ids=ids, docs=rows, n_docs_failed=failed, error=error)


def _entries(*receipts: ShardReceipt) -> list[dict[str, Any]]:
    return [json.loads(r.to_json()) for r in receipts]


def _archive_bytes(*receipts: ShardReceipt) -> bytes:
    if len(receipts) == 1:  # archive.py copies a single receipt verbatim (an object)
        return receipts[0].to_json().encode()
    return json.dumps(_entries(*receipts)).encode()


class _Client:
    def __init__(self, *, state: str = "COMPLETED") -> None:
        self.state = state
        self.wait_kwargs: dict[str, Any] = {}
        self.submitted: dict[str, Any] | None = None

    async def register_workflow(self, name, cwl, **kw) -> str:
        return "wf_fake"

    async def submit(self, wf_id, inputs, **kw):
        self.submitted = inputs
        return {"id": "sub_1", "state": "PENDING"}

    async def wait(self, sub_id, **kw):
        self.wait_kwargs = kw
        return {"id": sub_id, "state": self.state, "output_state": "delivered"}


class _Workspace:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.reads: list[tuple[str, str]] = []

    async def read_file(self, token: str, path: str) -> bytes:
        self.reads.append((token, path))
        if path not in self.files:
            raise WorkspaceNotFound(f"{path} does not exist")
        return self.files[path]


def _backend(ws: _Workspace, client: _Client | None = None, **kw) -> GoWeBackend:
    return GoWeBackend(client or _Client(), CWL_TEXT, poll_interval=0, timeout=1,
                       output_wait_timeout=0.2, workspace=ws, **kw)


async def _run(ws: _Workspace, items: list[WorkItem], client: _Client | None = None):
    run = await _backend(ws, client).run_submission(
        items, inputs={"version": "7"}, token=TOKEN, output_destination=DEST)
    return run.results


# --- per-document mapping from docs ------------------------------------------ #

@pytest.mark.asyncio
async def test_mixed_batches_map_exact_per_document_status() -> None:
    """20 PDFs in 2 batches of 10 (2 scanned) → 18 completed with their OWN chunk
    ids, 2 failed with NO_TEXT_ERROR verbatim — regardless of receipt count."""
    names = [f"p{i:02d}.pdf" for i in range(20)]
    rows_a = [_row(n, f"{n}-c0", f"{n}-c1") for n in names[:8]] + \
             [_row(n, error=NO_TEXT_ERROR) for n in names[8:10]]
    rows_b = [_row(n, f"{n}-c0") for n in names[10:]]
    ws = _Workspace({RECEIPT_PATH: _archive_bytes(_batch("batch-0", rows_a), _batch("batch-1", rows_b))})
    # Items in an order that is NOT the receipt order: mapping must be by name.
    items = [_wi(n) for n in reversed(names)]
    results = await _run(ws, items)

    assert [r.item_id for r in results] == [i.item_id for i in items]
    by_name = {r.item_id.rsplit("/", 1)[1]: r for r in results}
    assert sum(r.status == "completed" for r in results) == 18
    failed = [n for n, r in by_name.items() if r.status == "failed"]
    assert sorted(failed) == ["p08.pdf", "p09.pdf"]
    assert {by_name[n].error for n in failed} == {NO_TEXT_ERROR}
    assert all(by_name[n].chunk_ids == [] for n in failed)
    assert by_name["p00.pdf"].chunk_ids == ["p00.pdf-c0", "p00.pdf-c1"]
    assert by_name["p19.pdf"].chunk_ids == ["p19.pdf-c0"] and by_name["p19.pdf"].error == ""
    assert ws.reads == [(TOKEN, RECEIPT_PATH)]


@pytest.mark.asyncio
async def test_no_text_error_survives_verbatim_into_the_item_error() -> None:
    """The #377 gap: the archived receipt now carries the constant string and
    the backend copies it unchanged — the job's per-item error is GROUP BY-able."""
    rows = [_row("ok.pdf", "c1"), _row("scan.pdf", error=NO_TEXT_ERROR),
            _row("blank.pdf", error=NO_CHUNKS_ERROR)]
    ws = _Workspace({RECEIPT_PATH: _archive_bytes(_batch("b", rows))})
    results = await _run(ws, [_wi("scan.pdf"), _wi("blank.pdf"), _wi("ok.pdf")])
    assert [(r.status, r.error) for r in results] == [
        ("failed", NO_TEXT_ERROR), ("failed", NO_CHUNKS_ERROR), ("completed", "")]
    assert results[0].error == NO_TEXT_ERROR  # byte-identical, no prefix, no filename


@pytest.mark.asyncio
async def test_batch_level_failure_attributes_the_batch_error_to_every_document() -> None:
    """Batch 0 failed at upsert: run_shard wrote the batch error on every row that
    had no error of its own (the scanned one keeps NO_TEXT_ERROR). Batch 1 is fine."""
    boom = "RuntimeError: qdrant down"
    rows_a = [_row("a.pdf", error=boom), _row("b.pdf", error=boom), _row("scan.pdf", error=NO_TEXT_ERROR)]
    rows_b = [_row("c.pdf", "c-1")]
    ws = _Workspace({RECEIPT_PATH: _archive_bytes(
        _batch("batch-0", rows_a, status=FAILED, error=boom), _batch("batch-1", rows_b))})
    results = await _run(ws, [_wi("a.pdf"), _wi("b.pdf"), _wi("scan.pdf"), _wi("c.pdf")])
    assert [(r.status, r.error, r.chunk_ids) for r in results] == [
        ("failed", boom, []), ("failed", boom, []), ("failed", NO_TEXT_ERROR, []),
        ("completed", "", ["c-1"])]


def test_failed_receipt_whose_rows_carry_no_error_inherits_the_receipt_error() -> None:
    """A receipt not written by run_shard (rows without errors, shard failed):
    the batch error still reaches every document; with no error text at all,
    a constant label rather than 'completed'."""
    rows = [_row("a.pdf", "x"), _row("b.pdf")]
    r = _batch("b", rows, status=FAILED, error="gowe task killed")
    out = map_receipt_entries([_wi("a.pdf"), _wi("b.pdf")], _entries(r))
    assert [(o.status, o.error) for o in out] == [("failed", "gowe task killed")] * 2
    r2 = _batch("b", rows, status=FAILED)
    out = map_receipt_entries([_wi("a.pdf")], _entries(r2))
    assert out[0].status == "failed" and out[0].error == BATCH_FAILED


def test_legacy_rows_without_chunk_ids_fall_back_to_the_shard_ids_for_one_doc() -> None:
    """A receipt written before rows carried chunk ids (one document per
    receipt, Option A): the document gets the shard's ids."""
    entry = {"shard_id": "p1", "status": COMPLETED, "n_docs": 1, "n_chunks": 2,
             "chunk_ids": ["c1", "c2"], "docs": [{"doc_id": "d", "source": f"{STAGED}/p1.pdf"}]}
    out = map_receipt_entries([_wi("p1.pdf")], [entry])
    assert out[0].status == "completed" and out[0].chunk_ids == ["c1", "c2"]


# --- fallbacks + contract ------------------------------------------------------ #

@pytest.mark.asyncio
async def test_option_a_archive_one_receipt_per_item_maps_positionally() -> None:
    """Receipts whose rows name nothing the items match, but exactly one per
    item: positional mapping (the pre-2b archive shape) still works."""
    a = ShardReceipt("s0", "public", COMPLETED, n_chunks=2, chunk_ids=["a", "b"])
    b = ShardReceipt("s1", "public", FAILED, error="boom")
    ws = _Workspace({RECEIPT_PATH: _archive_bytes(a, b)})
    results = await _run(ws, [_wi("x.pdf"), _wi("y.pdf")])
    assert [(r.status, r.chunk_ids, r.error) for r in results] == [
        ("completed", ["a", "b"], ""), ("failed", [], "boom")]


@pytest.mark.asyncio
async def test_single_receipt_object_maps_by_name_then_position() -> None:
    r = _batch("b", [_row("only.pdf", "c1")])
    ws = _Workspace({RECEIPT_PATH: _archive_bytes(r)})
    results = await _run(ws, [_wi("only.pdf")])
    assert results[0].status == "completed" and results[0].chunk_ids == ["c1"]
    # Same single receipt, an item it does not name → positional (1 entry, 1 item).
    results = await _run(_Workspace({RECEIPT_PATH: _archive_bytes(r)}), [_wi("other.pdf")])
    assert results[0].status == "completed" and results[0].chunk_ids == ["c1"]


def test_unmatched_item_among_batches_fails_alone() -> None:
    """Two batches for three items, one item named by neither: it fails with a
    constant error; the run is not a contract error (the receipts DO report)."""
    entries = _entries(_batch("b0", [_row("a.pdf", "1")]), _batch("b1", [_row("b.pdf", "2")]))
    out = map_receipt_entries([_wi("a.pdf"), _wi("b.pdf"), _wi("ghost.pdf")], entries)
    assert [(o.status, o.error) for o in out] == [
        ("completed", ""), ("completed", ""), ("failed", NO_RECEIPT_ENTRY)]


def test_malformed_entry_fails_only_its_items() -> None:
    entries = _entries(_batch("b0", [_row("a.pdf", "1")])) + [{"garbage": True}]
    out = map_receipt_entries([_wi("a.pdf"), _wi("b.pdf")], entries)
    assert out[0].status == "completed"
    assert out[1].status == "failed" and out[1].error == NO_READABLE_RECEIPT  # positional: 2 == 2
    out = map_receipt_entries([_wi("a.pdf"), _wi("b.pdf"), _wi("c.pdf")], entries)
    assert [o.error for o in out[1:]] == [NO_RECEIPT_ENTRY] * 2


@pytest.mark.parametrize("body", [b"[]", b"{}", b'[{"shard_id": "s", "status": "completed"}]',
                                  b"not json", b'"str"', None])
@pytest.mark.asyncio
async def test_receipts_naming_no_item_are_a_contract_error(body) -> None:
    """Fewer receipts than items AND none naming a document: the workflow cannot
    report — a visible error, never 'every document failed'."""
    ws = _Workspace({} if body is None else {RECEIPT_PATH: body})
    with pytest.raises(GoWeContractError):
        await _run(ws, [_wi("a.pdf"), _wi("b.pdf")])


@pytest.mark.asyncio
async def test_duplicate_source_basenames_are_refused_before_submission() -> None:
    """Two items whose sources share a basename could not be mapped back
    unambiguously (and would collide at the engine's pre-staging): refused
    with the names, and nothing is registered or submitted. Only the user path
    (an output_destination) is guarded — the bulk plane maps its `receipts`
    output positionally and may legitimately repeat a shard basename."""
    client = _Client()
    other = WorkItem(item_id="/bob@patricbrc.org/home/paper.pdf",
                     source="ws:///bob@patricbrc.org/home/paper.pdf")
    with pytest.raises(GoWeContractError, match="'paper.pdf'"):
        await _backend(_Workspace({}), client).run_submission(
            [_wi("paper.pdf"), _wi("other.pdf"), other], inputs={"version": "7"},
            token=TOKEN, output_destination=DEST)
    assert client.submitted is None


def test_duplicate_row_names_keep_the_first() -> None:
    entries = _entries(_batch("b0", [_row("a.pdf", "1")]), _batch("b1", [_row("a.pdf", "2")]))
    out = map_receipt_entries([_wi("a.pdf")], entries)
    assert out[0].chunk_ids == ["1"]


def test_bulk_plane_paths_match_by_basename_too() -> None:
    wi = WorkItem(item_id="i0", source="/data/shards/s0.jsonl")
    r = _batch("s0", [DocRow("d", "/scratch/task/s0.jsonl", chunk_ids=["c"])])
    out = map_receipt_entries([wi], _entries(r))
    assert out[0].status == "completed" and out[0].chunk_ids == ["c"]


# --- per-submission poll interval --------------------------------------------- #

@pytest.mark.parametrize("n_items, setting, expected", [
    (1, 5.0, 0.5), (50, 5.0, 0.5), (51, 5.0, 5.0), (3, 0.0, 0.0), (3, 0.2, 0.2), (500, 0.2, 0.2),
])
def test_poll_interval_is_per_submission(n_items, setting, expected) -> None:
    b = GoWeBackend(_Client(), CWL_TEXT, poll_interval=setting)
    assert b.poll_interval_for(n_items) == expected
    b = GoWeBackend(_Client(), CWL_TEXT, poll_interval=5.0, interactive_poll_interval=1.0,
                    interactive_max_items=10)
    assert b.poll_interval_for(10) == 1.0 and b.poll_interval_for(11) == 5.0


@pytest.mark.asyncio
async def test_small_submission_polls_fast_large_one_at_the_setting() -> None:
    rows = [_row(f"p{i}.pdf", f"c{i}") for i in range(60)]
    ws = _Workspace({RECEIPT_PATH: _archive_bytes(_batch("b", rows))})
    client = _Client()
    b = GoWeBackend(client, CWL_TEXT, poll_interval=5.0, timeout=1, output_wait_timeout=0.2,
                    workspace=ws)
    await b.run_submission([_wi("p0.pdf"), _wi("p1.pdf")], inputs={"version": "7"},
                           token=TOKEN, output_destination=DEST)
    assert client.wait_kwargs["poll_interval"] == 0.5
    await b.run_submission([_wi(f"p{i}.pdf") for i in range(60)], inputs={"version": "7"},
                           token=TOKEN, output_destination=DEST)
    assert client.wait_kwargs["poll_interval"] == 5.0
