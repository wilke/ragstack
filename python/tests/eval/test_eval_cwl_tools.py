"""Tests for the CWL eval step tools (ADR-0001 step 1): chunk_one + aggregate_stats.

`aggregate_stats` is exercised end-to-end offline (synthetic per-config metrics →
report + the validation guards). `chunk_one` — which needs the live embedding
fleet + Qdrant/ES to actually run — is covered at the plumbing level only: its
metrics-payload shaping and argument validation, no infra.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# The eval scripts live under python/scripts/eval and import each other as siblings.
_EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(_EVAL_DIR))

import aggregate_stats  # noqa: E402
import chunk_one  # noqa: E402

_METRICS = ("ndcg@10", "map", "recall@10", "recall@20", "recall@100")


def _metrics_payload(config: str, base: float, n: int = 12,
                     query_ids: list[str] | None = None) -> dict:
    # Deterministic per-query arrays; slight per-config offset so the diff CIs and
    # the signed-rank test have a non-degenerate (but tiny) signal to chew on.
    per_query = {m: [round(base + 0.01 * ((i + j) % 5), 4) for i in range(n)]
                 for j, m in enumerate(_METRICS)}
    means = {m: sum(v) / len(v) for m, v in per_query.items()}
    return {"config": config, "source": "test:synthetic", "n_queries": n,
            "query_ids": query_ids if query_ids is not None else [str(i) for i in range(n)],
            "means": means, "per_query": per_query}


def _write(tmp_path: Path, config: str, base: float, n: int = 12,
           query_ids: list[str] | None = None) -> str:
    p = tmp_path / f"{config}.json"
    p.write_text(json.dumps(_metrics_payload(config, base, n, query_ids)),
                 encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------- #
# aggregate_stats — the offline gather step
# --------------------------------------------------------------------------- #
def test_aggregate_writes_report(tmp_path: Path) -> None:
    files = [
        _write(tmp_path, "fixed_tok512", 0.70),   # the stats reference
        _write(tmp_path, "fixed_tok256", 0.71),
        _write(tmp_path, "semantic_pooled", 0.69),
    ]
    out = tmp_path / "report.md"
    assert aggregate_stats.main([*files, "--out", str(out)]) == 0
    report = out.read_text(encoding="utf-8")
    # Metrics table + significance section rendered, reference config surfaced.
    assert "Document-level metrics" in report
    assert "Significance vs the reference config" in report
    assert "fixed_tok512" in report and "Wilcoxon" in report


def test_aggregate_puts_reference_first(tmp_path: Path) -> None:
    files = [_write(tmp_path, "fixed_tok256", 0.71),
             _write(tmp_path, "fixed_tok512", 0.70)]  # reference given last
    eval_stats, keys, source, n_q = aggregate_stats.load_metrics(files)
    assert keys[0] == "fixed_tok512"  # reordered to front regardless of input order
    assert source == "test:synthetic" and n_q == 12


def test_aggregate_rejects_duplicate_config(tmp_path: Path) -> None:
    f = _write(tmp_path, "fixed_tok512", 0.70)
    with pytest.raises(SystemExit, match="duplicate"):
        aggregate_stats.load_metrics([f, f])


def test_aggregate_rejects_missing_metric(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    payload = _metrics_payload("fixed_tok512", 0.70)
    del payload["per_query"]["map"]
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit, match="per_query missing"):
        aggregate_stats.load_metrics([str(bad)])


def test_aggregate_rejects_missing_means(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    payload = _metrics_payload("fixed_tok512", 0.70)
    del payload["means"]["recall@20"]
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit, match="means missing"):
        aggregate_stats.load_metrics([str(bad)])


def test_aggregate_rejects_misaligned_query_ids(tmp_path: Path) -> None:
    # Same length, but different query sets -> the length check would miss it;
    # the query_ids cross-check catches the unpaired data.
    files = [_write(tmp_path, "fixed_tok512", 0.70, n=12),
             _write(tmp_path, "fixed_tok256", 0.71, n=12,
                    query_ids=[str(100 + i) for i in range(12)])]
    with pytest.raises(SystemExit, match="DIFFERENT queries"):
        aggregate_stats.load_metrics(files)


def test_aggregate_rejects_unequal_query_counts(tmp_path: Path) -> None:
    files = [_write(tmp_path, "fixed_tok512", 0.70, n=12),
             _write(tmp_path, "fixed_tok256", 0.71, n=10)]  # misaligned
    with pytest.raises(SystemExit, match="DIFFERENT queries"):
        aggregate_stats.load_metrics(files)


# --------------------------------------------------------------------------- #
# chunk_one — plumbing only (no infra)
# --------------------------------------------------------------------------- #
def test_chunk_one_payload_shape() -> None:
    stats = {"means": dict.fromkeys(_METRICS, 0.5),
             "per_query": {m: [0.4, 0.5, 0.6] for m in _METRICS}}
    payload = chunk_one.build_metrics_payload("fixed_tok512", "test:src", stats,
                                              ["q0", "q1", "q2"])
    assert payload["config"] == "fixed_tok512"
    assert payload["source"] == "test:src"
    assert payload["n_queries"] == 3
    assert payload["query_ids"] == ["q0", "q1", "q2"]
    assert payload["means"] == stats["means"]
    # aggregate_stats can consume what chunk_one emits (round-trip contract).
    assert all(m in payload["per_query"] for m in aggregate_stats._REQUIRED_PQ)


def test_chunk_one_payload_rejects_qid_misalignment() -> None:
    stats = {"means": dict.fromkeys(_METRICS, 0.5),
             "per_query": {m: [0.4, 0.5, 0.6] for m in _METRICS}}
    with pytest.raises(ValueError, match="misaligned"):
        chunk_one.build_metrics_payload("fixed_tok512", "s", stats, ["q0", "q1"])


def test_chunk_one_rejects_unknown_config() -> None:
    with pytest.raises(SystemExit):  # argparse choices
        chunk_one.parse_args(["--config", "not_a_real_config"])


def test_chunk_one_rejects_retrieve_lt_rerank() -> None:
    with pytest.raises(SystemExit, match="retrieve-pool"):
        chunk_one.main(["--config", "fixed_tok512",
                        "--retrieve-pool", "10", "--rerank-pool", "100"])
