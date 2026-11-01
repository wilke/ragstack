"""Alias study dry run: the whole chain (runner, judge, statistics) on non-study records with a
fake retriever and fake judges; and its refusals."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parents[1] / "scripts" / "eval", _HERE.parents[1] / "scripts" / "eval" / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import alias_study_dryrun as dry  # noqa: E402
import alias_study_fixture as fx  # noqa: E402


def test_dry_run_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()
    w = fx.World(tmp_path / "in", set_="V")
    out = dry.dry_run(w.records_path, w.base_export, w.glossary, tmp_path / "work", added_per_arm=3,
                      batch_size=60, n_perm=500, n_boot=200)
    assert not any(out["legs"].values()) and len(out["legs"]) == 12
    assert out["retrieve_calls"] == len(w.records) * 28
    assert out["stats_exit"] == 0 and all(v == 0 for v in out["judge"].values())
    res = json.loads((tmp_path / "work" / "results-dryrun.json").read_text())
    assert res["dry_run"] is True and res["citable"] is False
    assert set(res["layer1"]) >= {"H1", "H2", "H1b"} and (tmp_path / "work" / "public-dryrun.json").is_file()
    s = json.loads((tmp_path / "work" / "runs" / "base" / "none.summary.json").read_text())
    assert s["study"]["dry_run"] is True and s["study"]["config_hash_identical"] is True
    assert s["manipulation_check"]["glossary_boost"]["status"] == "declared"


def test_dry_run_refuses_study_records(tmp_path: Path) -> None:
    (tmp_path / "o").mkdir()
    w = fx.World(tmp_path / "o", set_="O")
    with pytest.raises(SystemExit, match="not validation records"):
        dry.dry_run(w.records_path, w.base_export, w.glossary, tmp_path / "work")
    (tmp_path / "v").mkdir()
    v = fx.World(tmp_path / "v", set_="V")
    with pytest.raises(SystemExit, match="study records"):
        dry.dry_run(v.records_path, v.base_export, v.glossary, tmp_path / "work", study_records=v.records_path)


def test_dry_run_refuses_work_in_repo(tmp_path: Path) -> None:
    (tmp_path / "v").mkdir()
    v = fx.World(tmp_path / "v", set_="V")
    with pytest.raises(SystemExit, match="inside the repository"):
        dry.dry_run(v.records_path, v.base_export, v.glossary, Path(__file__).resolve().parent / "dry")
