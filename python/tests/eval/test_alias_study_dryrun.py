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
import alias_study_common as asc  # noqa: E402
import alias_study_dryrun as dry  # noqa: E402
import alias_study_fixture as fx  # noqa: E402
import alias_study_run as ar  # noqa: E402


def test_dry_run_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()
    w = fx.World(tmp_path / "in", set_="V")
    out = dry.dry_run(w.records_path, w.base_export, w.glossary, tmp_path / "work", added_per_arm=3,
                      batch_size=60, n_perm=500, n_boot=200)
    # 9 stage-A runs (19 legs: the 5 Layer-1 attachments x 3 retrievers, the 4 repeats) and 4 stage-B
    # runs (the expanded arms x merged, and the placebo leg): 29 legs, every attachment run once
    assert not any(out["legs"].values()) and len(out["legs"]) == 13
    assert sum(k.startswith("A|") for k in out["legs"]) == 9
    assert out["retrieve_calls"] == len(w.records) * 29
    assert len(out["leg_rows"]) == 29 and set(out["leg_rows"].values()) == {len(w.records)}
    assert out["stats_exit"] == 0 and all(v == 0 for v in out["judge"].values())
    # the statistics on the harness's own output: Layer 1 after stage A, everything after stage B
    assert out["stats_exit_stage_a"] == 0
    ra = json.loads((tmp_path / "work" / "results-stageA.json").read_text())
    assert ra["analysis"]["stage"] == "A" and ra["label_coverage"]["legs"] == 14
    assert ra["label_coverage"]["pairs_without_final_label"] == 0 and ra["label_coverage"]["needs"] == ["found10"]
    assert ra["layer2"].startswith("absent (analysis stage A") and ra["frozen_stage_a"]["agree"] is True
    assert all(ra["legs_read"][asc.leg_id(*lg)] == len(w.records) for lg in ar.stage_legs("A"))
    assert ra["layer1"]["H1"]["n"] > 0 and ra["layer1"]["H2"]["n"] > 0
    fy = ra["layer1"]["H2"]["judge_false_yes_bound"]
    assert fy["source"].startswith("stage-a/judge-summary.json") and fy["cross_check_with_final_rows"] is True
    rb = json.loads((tmp_path / "work" / "results-dryrun.json").read_text())
    assert rb["analysis"]["stage"] == "B" and rb["label_coverage"]["legs"] == 29
    assert rb["label_coverage"]["pairs_without_final_label"] == 0 and rb["label_coverage"]["needs"] == ["found10", "rr10"]
    assert all(v == len(w.records) for v in rb["legs_read"].values()) and len(rb["legs_read"]) == 29
    assert rb["study"]["mode"] == "dry_run" and rb["study"]["spot_checks_waiver"] == "dry run"
    assert rb["inputs"]["stage_a"]["stage_a_commit_sha256"] == out["analysis_gate"]["stage_a_commit_sha256"]
    assert rb["inputs"]["runs"]["base/none"]["collection_build_sha256"]
    assert isinstance(rb["layer2"], dict) and rb["layer2"]["placebo"]["field_coverage"]["placebo"]
    assert out["analysis_gate"]["allowed"] and out["analysis_gate"]["spot_checks_waived"]
    res = json.loads((tmp_path / "work" / "results-dryrun.json").read_text())
    assert res["dry_run"] is True and res["citable"] is False
    assert set(res["layer1"]) >= {"H1", "H2", "H1b"} and (tmp_path / "work" / "public-dryrun.json").is_file()
    s = json.loads((tmp_path / "work" / "runs" / "base" / "none.summary.json").read_text())
    assert s["study"]["dry_run"] is True and s["study"]["config_hash_identical"] is True
    assert s["manipulation_check"]["glossary_boost"]["status"] == "declared"
    assert s["study"]["stage"] == "A"
    assert s["study"]["legs"] == ["base|none|default", "base|none|bm25", "base|none|norerank"]
    assert s["study"]["leg_stages"]["base|none|bm25"] == {"retrieval": "A", "judging": "B"}
    assert s["conditions"]["base"]["identical_to_norerank"]["records"] == len(w.records)
    assert s["census"]["collection_build"]["collection"] == "bench-dry-base-none"
    assert not (tmp_path / "work" / "runs" / "base" / "none.stageB.summary.json").exists()
    sb = json.loads((tmp_path / "work" / "runs" / "neighbour" / "merged.summary.json").read_text())
    assert sb["study"]["stage"] == "B" and sb["study"]["stage_a_commit"]["verified"]
    pub = json.loads((tmp_path / "work" / "judge" / "stage-a-commit.public.json").read_text())
    assert pub["counts"]["retrieval_legs"] == 19 and pub["counts"]["judging_legs"] == 14


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
