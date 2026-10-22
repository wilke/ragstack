"""Every eval harness records ``rerank_symbol_glossary`` in its run output (#792).

The API's reranker reads the chunk's symbol glossary by default (#754); the
harnesses do so only with ``--rerank-symbol-glossary`` (default off, so past
results stay comparable). A result file must say which condition produced it,
wherever the harness records its other run parameters. No store or network:
the writers are called directly with tmp paths."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(_EVAL_DIR))

import chunk_one  # noqa: E402
import chunking_compare as cc  # noqa: E402
import chunking_compare_7way as c7  # noqa: E402
import g1_library_sweep as g1  # noqa: E402
import scifact_chunk_eval as sfe  # noqa: E402

DEAD = "http://127.0.0.1:1"
STORES = ["--qdrant-url", DEAD, "--es-url", DEAD]


@pytest.mark.parametrize(
    ("module", "extra"),
    [
        (cc, []),
        (c7, []),
        (sfe, []),
        (chunk_one, ["--config", "fixed_tok512"]),
        (g1, ["--doc-counts", "50", "--smoke"]),
    ],
)
def test_every_harness_takes_the_flag_default_off(module, extra) -> None:
    assert module.parse_args([*STORES, *extra]).rerank_symbol_glossary is False
    on = module.parse_args([*STORES, *extra, "--rerank-symbol-glossary"])
    assert on.rerank_symbol_glossary is True


@pytest.mark.parametrize("flag", [True, False])
def test_chunk_one_metrics_record_it(flag: bool) -> None:
    stats = {"means": {"ndcg@10": 0.5}, "per_query": {"ndcg@10": [0.5]}}
    payload = chunk_one.build_metrics_payload(
        "fixed_tok512", "src", stats, ["q1"], rerank_symbol_glossary=flag
    )
    assert payload["rerank_symbol_glossary"] is flag


@pytest.mark.parametrize("flag", [True, False])
def test_scifact_metrics_json_records_it(tmp_path, flag: bool) -> None:
    out = tmp_path / "metrics.json"
    stats = {"k": {"means": {"ndcg@10": 0.5}, "per_query": {"ndcg@10": [0.5]}}}
    sfe._write_metrics_json(out, stats, "src", 1, rerank_symbol_glossary=flag)
    assert json.loads(out.read_text())["rerank_symbol_glossary"] is flag


@pytest.mark.parametrize("flag", [True, False])
def test_7way_and_scifact_provenance_records_it(tmp_path, flag: bool) -> None:
    """``c7.write_provenance`` is shared by the 7-way and SciFact harnesses."""
    report, csv, out = tmp_path / "r.md", tmp_path / "r.csv", tmp_path / "p.json"
    report.write_text("# report\n", encoding="utf-8")
    c7.write_provenance(
        {}, report_path=report, csv_path=csv, out_path=out, rerank_symbol_glossary=flag
    )
    assert json.loads(out.read_text())["run_params"] == {"rerank_symbol_glossary": flag}


@pytest.mark.parametrize("flag", [True, False])
def test_chunking_compare_provenance_records_it(tmp_path, monkeypatch, flag: bool) -> None:
    monkeypatch.setattr(cc, "REPORT_PATH", tmp_path / "r.md")
    monkeypatch.setattr(cc, "CSV_PATH", tmp_path / "r.csv")
    monkeypatch.setattr(cc, "PROVENANCE_PATH", tmp_path / "p.json")
    (tmp_path / "r.md").write_text("# report\n", encoding="utf-8")
    cc.write_provenance({}, rerank_symbol_glossary=flag)
    record = json.loads((tmp_path / "p.json").read_text())
    assert record["run_params"] == {"rerank_symbol_glossary": flag}


@pytest.mark.parametrize("flag", [True, False])
def test_g1_run_manifest_records_it(flag: bool) -> None:
    argv = [*STORES, "--doc-counts", "50", "--smoke"]
    if flag:
        argv.append("--rerank-symbol-glossary")
    args = g1.parse_args(argv)
    manifest = g1.build_run_manifest(
        run_id="g1-test",
        argv=["g1_library_sweep.py", *argv],
        args=args,
        dataset={},
        indexes={},
        grid=g1.build_grid([50], ["hybrid"], [60], [10], [False], [50]),
        started_at="2026-01-01T00:00:00+00:00",
        qvec_cache={"path": "x", "sha256": "sha256:abc", "hit": False},
        reranker={"url": "http://x", "model": "m", "revision": "r"},
    )
    assert manifest["rerank_symbol_glossary"] is flag
