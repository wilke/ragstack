"""alias_added_glossaries_worker: setting a unit's output aside never overwrites a generation."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(_EVAL_DIR))

import alias_added_glossaries_worker as w  # noqa: E402


def _unit(tmp: Path) -> dict:
    return {"unit": "p00000-s", "out": str(tmp / "p00000-s.glossary.jsonl"),
            "summary": str(tmp / "p00000-s.summary.json"), "doc_ids": ["d"]}


def test_three_reruns_within_a_second_keep_every_generation(tmp_path: Path) -> None:
    u = _unit(tmp_path)
    t0 = time.monotonic()
    for gen in range(3):
        Path(u["out"]).write_text(f"rows {gen}\n")
        Path(u["summary"]).write_text(f"summary {gen}\n")
        assert w.prepare_unit(u, rerun=True) is True
        assert not Path(u["out"]).exists() and not Path(u["summary"]).exists()
    assert time.monotonic() - t0 < 1.0  # the case int(time.time()) stamps collided on
    rows = sorted(p.read_text() for p in tmp_path.glob("p00000-s.glossary.jsonl.superseded-*"))
    summ = sorted(p.read_text() for p in tmp_path.glob("p00000-s.summary.json.superseded-*"))
    assert rows == [f"rows {g}\n" for g in range(3)]
    assert summ == [f"summary {g}\n" for g in range(3)]


def test_bad_outputs_within_a_second_keep_every_generation(tmp_path: Path, monkeypatch) -> None:
    u = _unit(tmp_path)
    monkeypatch.setattr(w, "_verify_unit_output", lambda out, ids: (False, "test"))
    for gen in range(3):
        Path(u["out"]).write_text(f"bad {gen}\n")
        assert w.prepare_unit(u, rerun=False) is True
    bad = sorted(p.read_text() for p in tmp_path.glob("p00000-s.glossary.jsonl.bad-*"))
    assert bad == [f"bad {g}\n" for g in range(3)]


def test_verified_output_is_skipped_and_kept(tmp_path: Path, monkeypatch) -> None:
    u = _unit(tmp_path)
    monkeypatch.setattr(w, "_verify_unit_output", lambda out, ids: (True, "ok"))
    Path(u["out"]).write_text("good\n")
    assert w.prepare_unit(u, rerun=False) is False
    assert Path(u["out"]).read_text() == "good\n"


def test_set_aside_refuses_an_existing_target(tmp_path: Path, monkeypatch) -> None:
    f = tmp_path / "x.jsonl"
    f.write_text("new\n")
    monkeypatch.setattr(w.time, "time_ns", lambda: 42)
    (tmp_path / "x.jsonl.superseded-42").write_text("old\n")
    with pytest.raises(SystemExit):
        w.set_aside(f, "superseded")
    assert (tmp_path / "x.jsonl.superseded-42").read_text() == "old\n"
    assert f.read_text() == "new\n"
