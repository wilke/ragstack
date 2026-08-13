"""`--file-concurrency` correctness for the bulk loader (#323).

Files hold disjoint document sets and chunk ids are deterministic, so loading
several concurrently cannot race or duplicate. What concurrency *can* break is
bookkeeping: receipts arriving out of completion order, or a summary that
depends on the order files happened to finish. These tests pin that down —
the summary must be identical to a serial run regardless of who finishes first.
"""
import argparse
import asyncio
import importlib.util
from pathlib import Path

import pytest

from ragstack.ingestion.receipts import COMPLETED, FAILED, ShardReceipt, merge_summary

_SPEC = importlib.util.spec_from_file_location(
    "_load_embeddings_cli",
    Path(__file__).resolve().parents[2] / "scripts" / "load_embeddings.py",
)
cli = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cli)


class _FakeTextIndex:
    """Records refresh parking so the ordering can be asserted."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def bulk_load_refresh(self, disable: bool):
        self.events.append("park")
        return "1s"

    async def restore_refresh(self, prior) -> None:
        self.events.append(f"restore:{prior}")

    async def refresh(self) -> None:
        self.events.append("refresh")


class _FakePipeline:
    def __init__(self, text_index) -> None:
        self.text_index = text_index


def _args(paths, **kw):
    ns = argparse.Namespace(
        embeddings=list(paths), tenant="t", out="/dev/null",
        file_concurrency=1, bulk_refresh=False, manifest_dir="",
        fail_on_error=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _install(monkeypatch, delays, order):
    """Patch run_load_file so each path takes a controlled amount of time."""

    async def _fake(pipeline, path, file_id, tenant=None):
        await asyncio.sleep(delays[path])
        order.append(path)
        return ShardReceipt(file_id, tenant or "", COMPLETED,
                            n_docs=1, n_chunks=10,
                            chunk_ids=[f"{path}-c1"], embedding_file=path)

    monkeypatch.setattr(cli, "run_load_file", _fake)


@pytest.mark.asyncio
async def test_receipts_follow_input_order_not_completion_order(monkeypatch):
    """The whole point: 'c' finishes first, but the summary must not care."""
    paths = ["a", "b", "c"]
    delays = {"a": 0.06, "b": 0.03, "c": 0.0}
    order: list[str] = []
    _install(monkeypatch, delays, order)

    tindex = _FakeTextIndex()
    monkeypatch.setattr(cli, "_build_pipeline",
                        lambda *a, **k: _done(_FakePipeline(tindex)))
    monkeypatch.setattr(cli, "merge_summary", _capture := _Capture())

    await cli.amain(_args(paths, file_concurrency=3))

    assert order == ["c", "b", "a"], "test setup did not actually interleave"
    assert [r.shard_id for r in _capture.receipts] == paths
    assert all(r is not None for r in _capture.receipts)


@pytest.mark.asyncio
async def test_concurrent_summary_matches_serial_summary(monkeypatch):
    paths = ["a", "b", "c", "d"]
    delays = {"a": 0.04, "b": 0.0, "c": 0.02, "d": 0.01}

    results = {}
    for conc in (1, 4):
        order: list[str] = []
        _install(monkeypatch, delays, order)
        tindex = _FakeTextIndex()
        monkeypatch.setattr(cli, "_build_pipeline",
                            lambda *a, _t=tindex, **k: _done(_FakePipeline(_t)))
        cap = _Capture()
        monkeypatch.setattr(cli, "merge_summary", cap)
        await cli.amain(_args(paths, file_concurrency=conc))
        results[conc] = merge_summary(cap.receipts)

    assert results[1] == results[4], "summary depends on file concurrency"


@pytest.mark.asyncio
async def test_concurrency_is_bounded_by_the_flag(monkeypatch):
    """file_concurrency=2 must not run all four files at once."""
    live = 0
    peak = 0

    async def _fake(pipeline, path, file_id, tenant=None):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.02)
        live -= 1
        return ShardReceipt(file_id, tenant or "", COMPLETED, n_chunks=1)

    monkeypatch.setattr(cli, "run_load_file", _fake)
    tindex = _FakeTextIndex()
    monkeypatch.setattr(cli, "_build_pipeline",
                        lambda *a, **k: _done(_FakePipeline(tindex)))
    monkeypatch.setattr(cli, "merge_summary", _Capture())

    await cli.amain(_args(["a", "b", "c", "d"], file_concurrency=2))
    assert peak == 2, f"semaphore did not bound concurrency (peak={peak})"


@pytest.mark.asyncio
async def test_refresh_is_restored_even_when_a_file_fails(monkeypatch):
    """Leaving refresh parked is the worst outcome here: the index silently
    stops updating. The restore must survive a failing load."""

    async def _boom(pipeline, path, file_id, tenant=None):
        raise RuntimeError("load exploded")

    monkeypatch.setattr(cli, "run_load_file", _boom)
    tindex = _FakeTextIndex()
    monkeypatch.setattr(cli, "_build_pipeline",
                        lambda *a, **k: _done(_FakePipeline(tindex)))
    monkeypatch.setattr(cli, "merge_summary", _Capture())

    with pytest.raises(RuntimeError, match="load exploded"):
        await cli.amain(_args(["a"], file_concurrency=1, bulk_refresh=True))

    assert tindex.events == ["park", "restore:1s", "refresh"], tindex.events


class _Capture:
    """Stands in for merge_summary, keeping the receipts it was handed."""

    def __init__(self) -> None:
        self.receipts: list = []

    def __call__(self, receipts):
        self.receipts = list(receipts)
        return {"n_chunks": sum(r.n_chunks for r in receipts),
                "n_shards": len(receipts),
                "n_shards_failed": sum(1 for r in receipts if r.status == FAILED)}


def _done(value):
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    fut.set_result(value)
    return fut
