"""Perf (#355, #203 2b): mapping 1,000 documents across 50 batch receipts to
per-item results must stay well under the poll interval it sits behind —
budget p95 < 10 ms. The receipt entries are built ONCE outside the timed loop;
only ``map_receipt_entries`` (parse + index rows by name + look up 1,000 items)
is measured."""
from __future__ import annotations

import gc
import json

import pytest

from ragstack.ingestion.gowe_backend import map_receipt_entries
from ragstack.ingestion.loaders import NO_TEXT_ERROR
from ragstack.ingestion.manifest import WorkItem
from ragstack.ingestion.receipts import COMPLETED, DocRow, ShardReceipt
from tests.perf._budget import assert_budget

N_DOCS, BATCH = 1000, 20
HOME = "/alice@patricbrc.org/home/.ragstack/collections/lib1"


def _entries() -> list[dict]:
    entries = []
    for b in range(N_DOCS // BATCH):
        rows = []
        for i in range(b * BATCH, (b + 1) * BATCH):
            error = NO_TEXT_ERROR if i % 25 == 0 else ""
            ids = [] if error else [f"{i}-{k}" for k in range(28)]
            rows.append(DocRow(doc_id=f"d{i}", source=f"/stage/p{i:04d}.pdf", chunk_ids=ids,
                               error=error, metadata={"title": f"T{i}"}))
        ids = [c for r in rows for c in r.chunk_ids]
        r = ShardReceipt(f"batch-{b}", "public", COMPLETED, n_docs=BATCH, n_chunks=len(ids),
                         chunk_ids=ids, docs=rows, n_docs_failed=sum(1 for r in rows if r.error))
        entries.append(json.loads(r.to_json()))
    return entries


@pytest.mark.perf
def test_map_1000_documents_across_50_receipts_p95_budget() -> None:
    entries = _entries()
    items = [WorkItem(item_id=f"{HOME}/sources/p{i:04d}.pdf",
                      source=f"ws://{HOME}/sources/p{i:04d}.pdf") for i in range(N_DOCS)]
    assert len(entries) == 50

    def _map() -> None:
        out = map_receipt_entries(items, entries)
        assert len(out) == N_DOCS

    out = map_receipt_entries(items, entries)
    assert sum(r.status == "failed" for r in out) == 40
    assert all(r.error == NO_TEXT_ERROR for r in out if r.status == "failed")
    # GC stays ON — the mapping's own allocation→collection cost is part of
    # what production pays. What is excluded is the NEIGHBOUR's heap: a whole-
    # session perf run leaves the 3 GB archive_pack input's survivors behind,
    # and a gen-2 pass over them inside one sample lifted p95 to 84 ms
    # (14.7 ms with a bare gc.collect()). collect + freeze moves those
    # survivors out of the collector's reach for the timed loop.
    gc.collect()
    gc.freeze()
    try:
        assert_budget("gowe_map_1000_docs_50_receipts", _map, budget_s=0.010, n=50)
    finally:
        gc.unfreeze()
