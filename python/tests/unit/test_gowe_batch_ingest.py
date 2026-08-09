"""Tests for the GoWe batch driver (#301).

Offline: `gowe` is a stub script, the stores are a stub HTTP server, and the
stage-out dir is tmp_path. The properties pinned are the operational ones —
resume skips done batches, verification fails on leg disagreement and on failed
shards, cleanup deletes only this batch's embedding intermediates.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import gowe_batch_ingest as gbi  # noqa: E402

# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


class _Stores(BaseHTTPRequestHandler):
    """Stub Qdrant + ES count endpoints, counts settable per test."""

    qdrant = 0
    es = 0

    def do_GET(self):  # noqa: N802
        if "missing_store" in self.path:  # simulate a store that does not exist yet
            self.send_response(404)
            self.end_headers()
            return
        if self.path.startswith("/collections/"):
            body = {"result": {"points_count": type(self).qdrant, "status": "green"}}
        elif self.path.endswith("/_count"):
            body = {"count": type(self).es}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # silence
        pass


@pytest.fixture
def stores():
    srv = HTTPServer(("127.0.0.1", 0), _Stores)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Stores.qdrant = _Stores.es = 0
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _stub_gowe(tmp_path: Path, *, state="COMPLETED") -> Path:
    """A fake gowe CLI: records submit calls, answers status with `state`."""
    stub = tmp_path / "gowe"
    stub.write_text(f"""#!/bin/bash
if [ "$1" = submit ]; then
  echo "$@" >> {tmp_path}/submits.log
  echo "Submission created: sub_stub_$(wc -l < {tmp_path}/submits.log | tr -d ' ')"
elif [ "$1" = status ]; then
  echo "  State:    {state}"
fi
""")
    stub.chmod(0o755)
    return stub


def _plan(tmp_path: Path, n_shards: int) -> Path:
    d = tmp_path / "plan"
    d.mkdir()
    for i in range(n_shards):
        (d / f"shard-{i:05d}.jsonl").write_text('{"pmcid": "PMC1"}\n')
    return d


def _registry(tmp_path: Path, cid="oa-dev", store="phys_store") -> Path:
    import sqlite3

    db = tmp_path / "reg.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE collections (id TEXT PRIMARY KEY, collection TEXT)")
        conn.execute("INSERT INTO collections VALUES (?, ?)", (cid, store))
    return db


def _template(tmp_path: Path, stores_url: str, db: Path) -> Path:
    doc = {
        "corpus": {"class": "Directory", "location": str(tmp_path)},
        "collection_id": "oa-dev",
        "registry_db": {"class": "File", "location": str(db)},
        "qdrant_url": stores_url,
        "es_url": stores_url,
    }
    p = tmp_path / "template.json"
    p.write_text(json.dumps(doc))
    return p


def _args(tmp_path: Path, stores_url: str, **over):
    plan = over.pop("plan", tmp_path / "plan")
    argv = [
        "--plan", str(plan),
        "--cwl", "x.cwl",
        "--inputs-template", str(over.pop("template")),
        "--out", str(tmp_path / "run"),
        "--gowe-bin", str(over.pop("gowe")),
        "--stage-out", str(over.pop("stage", tmp_path / "stage")),
        "--poll-interval", "0.01",
        "--batch-timeout", "5",
    ]
    for k, v in over.items():
        argv += [f"--{k.replace('_', '-')}", str(v)]
    return argv


def _stage_outputs(stage: Path, *, n_chunks: int, failed: int = 0,
                   emb_files: int = 0) -> None:
    """Pre-stage workflow outputs. The tests create these BEFORE main() runs but
    the driver windows on mtime >= batch start, so stamp them into the future —
    in a real run they appear during the batch."""
    import time

    future = time.time() + 60
    task = stage / "task_x"
    task.mkdir(parents=True, exist_ok=True)
    p = task / "load-summary.json"
    p.write_text(json.dumps(
        {"n_chunks": n_chunks, "n_shards_failed": failed, "failed_shards": []}))
    os.utime(p, (future, future))
    for i in range(emb_files):
        t = stage / f"task_e{i}"
        t.mkdir(exist_ok=True)
        e = t / f"shard-{i:05d}.emb.jsonl"
        e.write_text("x" * 100)
        os.utime(e, (future, future))


# --------------------------------------------------------------------------- #
# batching + resume
# --------------------------------------------------------------------------- #


def test_batches_are_consecutive_and_stably_keyed(tmp_path):
    _plan(tmp_path, 5)
    batches = gbi.make_batches(str(tmp_path / "plan"), 2)
    assert [k for k, _ in batches] == ["00000-00001", "00002-00003", "00004-00004"]
    assert [len(s) for _, s in batches] == [2, 2, 1]


def test_dry_run_submits_nothing(tmp_path, stores, capsys):
    _plan(tmp_path, 4)
    gowe = _stub_gowe(tmp_path)
    tpl = _template(tmp_path, stores, _registry(tmp_path))
    rc = gbi.main(_args(tmp_path, stores, template=tpl, gowe=gowe,
                        batch_size=2) + ["--dry-run"])
    assert rc == 0
    assert not (tmp_path / "submits.log").exists()
    assert "2 batch(es)" in capsys.readouterr().out


def test_resume_skips_done_batches(tmp_path, stores):
    _plan(tmp_path, 4)
    gowe = _stub_gowe(tmp_path)
    tpl = _template(tmp_path, stores, _registry(tmp_path))
    run = tmp_path / "run"
    run.mkdir()
    (run / "ledger.jsonl").write_text(json.dumps(
        {"batch": "00000-00001", "status": "done"}) + "\n")
    stage = tmp_path / "stage"
    _stage_outputs(stage, n_chunks=10)
    rc = gbi.main(_args(tmp_path, stores, template=tpl, gowe=gowe,
                        batch_size=2, stage=stage))
    assert rc == 0
    submits = (tmp_path / "submits.log").read_text().strip().splitlines()
    assert len(submits) == 1                       # only the second batch ran
    assert "inputs-00002-00003.json" in submits[0]


def test_rendered_inputs_carry_only_this_batchs_shards(tmp_path, stores):
    _plan(tmp_path, 3)
    gowe = _stub_gowe(tmp_path)
    tpl = _template(tmp_path, stores, _registry(tmp_path))
    stage = tmp_path / "stage"
    _stage_outputs(stage, n_chunks=5)
    gbi.main(_args(tmp_path, stores, template=tpl, gowe=gowe,
                   batch_size=2, stage=stage))
    doc = json.loads((tmp_path / "run" / "inputs-00000-00001.json").read_text())
    assert [Path(s["location"]).name for s in doc["shards"]] == [
        "shard-00000.jsonl", "shard-00001.jsonl"]
    assert doc["collection_id"] == "oa-dev"


def test_template_with_shards_is_refused(tmp_path, stores):
    _plan(tmp_path, 2)
    tpl = tmp_path / "bad.json"
    tpl.write_text(json.dumps({"shards": [], "collection_id": "x",
                               "registry_db": {"location": "x"},
                               "qdrant_url": stores, "es_url": stores}))
    with pytest.raises(SystemExit, match="must not contain 'shards'"):
        gbi.main(_args(tmp_path, stores, template=tpl,
                       gowe=_stub_gowe(tmp_path)))


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #


def test_leg_disagreement_fails_the_batch(tmp_path, stores):
    _plan(tmp_path, 2)
    gowe = _stub_gowe(tmp_path)
    tpl = _template(tmp_path, stores, _registry(tmp_path))
    stage = tmp_path / "stage"
    _stage_outputs(stage, n_chunks=10)
    _Stores.qdrant, _Stores.es = 10, 7             # legs disagree
    rc = gbi.main(_args(tmp_path, stores, template=tpl, gowe=gowe,
                        batch_size=2, stage=stage))
    assert rc == 1
    ledger = gbi.read_ledger(str(tmp_path / "run" / "ledger.jsonl"))
    assert ledger["00000-00001"]["status"] == "failed"
    assert "legs disagree" in ledger["00000-00001"]["error"]


def test_failed_shards_in_summary_fail_the_batch(tmp_path, stores):
    _plan(tmp_path, 2)
    gowe = _stub_gowe(tmp_path)
    tpl = _template(tmp_path, stores, _registry(tmp_path))
    stage = tmp_path / "stage"
    _stage_outputs(stage, n_chunks=10, failed=1)
    rc = gbi.main(_args(tmp_path, stores, template=tpl, gowe=gowe,
                        batch_size=2, stage=stage))
    assert rc == 1


def test_failed_submission_state_fails_the_batch(tmp_path, stores):
    _plan(tmp_path, 2)
    gowe = _stub_gowe(tmp_path, state="FAILED")
    tpl = _template(tmp_path, stores, _registry(tmp_path))
    rc = gbi.main(_args(tmp_path, stores, template=tpl, gowe=gowe, batch_size=2))
    assert rc == 1


def test_zero_delta_is_not_a_failure(tmp_path, stores):
    """An idempotent re-run advances the count by less than n_chunks — down to
    zero. That is the upsert working (#303), not data loss."""
    _plan(tmp_path, 2)
    gowe = _stub_gowe(tmp_path)
    tpl = _template(tmp_path, stores, _registry(tmp_path))
    stage = tmp_path / "stage"
    _stage_outputs(stage, n_chunks=10)
    _Stores.qdrant = _Stores.es = 10               # unchanged before/after
    rc = gbi.main(_args(tmp_path, stores, template=tpl, gowe=gowe,
                        batch_size=2, stage=stage))
    assert rc == 0


def test_missing_registry_entry_refuses_before_submitting(tmp_path, stores):
    _plan(tmp_path, 2)
    db = _registry(tmp_path, cid="other")
    tpl = _template(tmp_path, stores, db)
    with pytest.raises(SystemExit, match="not in registry"):
        gbi.main(_args(tmp_path, stores, template=tpl, gowe=_stub_gowe(tmp_path)))


# --------------------------------------------------------------------------- #
# cleanup
# --------------------------------------------------------------------------- #


def test_cleanup_deletes_only_this_batchs_embeddings(tmp_path, stores):
    _plan(tmp_path, 2)
    gowe = _stub_gowe(tmp_path)
    tpl = _template(tmp_path, stores, _registry(tmp_path))
    stage = tmp_path / "stage"
    # an OLD embedding file from a previous batch (mtime before t0)
    old = stage / "task_old"
    old.mkdir(parents=True)
    keep = old / "shard-99999.emb.jsonl"
    keep.write_text("previous batch")
    past = 1_000_000.0
    os.utime(keep, (past, past))
    _stage_outputs(stage, n_chunks=10, emb_files=2)
    rc = gbi.main(_args(tmp_path, stores, template=tpl, gowe=gowe,
                        batch_size=2, stage=stage))
    assert rc == 0
    assert keep.exists(), "a previous batch's file must not be touched"
    assert not list(stage.glob("task_e*/shard-*.emb.jsonl")), "batch files deleted"
    # receipts/summaries are kept
    assert (stage / "task_x" / "load-summary.json").exists()


def test_keep_embeddings_flag_disables_cleanup(tmp_path, stores):
    _plan(tmp_path, 2)
    gowe = _stub_gowe(tmp_path)
    tpl = _template(tmp_path, stores, _registry(tmp_path))
    stage = tmp_path / "stage"
    _stage_outputs(stage, n_chunks=10, emb_files=1)
    rc = gbi.main(_args(tmp_path, stores, template=tpl, gowe=gowe,
                        batch_size=2, stage=stage) + ["--keep-embeddings"])
    assert rc == 0
    assert list(stage.glob("task_e*/shard-*.emb.jsonl"))


def test_fresh_store_counts_as_zero_not_404(tmp_path, stores):
    """Batch 0 of a fresh collection runs before ensure_collection() has created
    the physical store — the registry row exists, the bytes don't. Found live:
    the pilot's baseline count 404ed and killed the driver before submitting."""
    assert gbi.store_counts(stores, stores, "missing_store") == (0, 0)


def test_fresh_store_batch_runs_end_to_end(tmp_path, stores):
    """The whole pilot shape: registry entry present, physical store absent at
    baseline, present after the (stubbed) load."""
    _plan(tmp_path, 2)
    gowe = _stub_gowe(tmp_path)
    db = _registry(tmp_path, store="phys_store")
    tpl = _template(tmp_path, stores, db)
    stage = tmp_path / "stage"
    _stage_outputs(stage, n_chunks=10)
    _Stores.qdrant = _Stores.es = 10
    rc = gbi.main(_args(tmp_path, stores, template=tpl, gowe=gowe,
                        batch_size=2, stage=stage))
    assert rc == 0
