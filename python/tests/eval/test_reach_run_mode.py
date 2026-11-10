"""The reach comparison's S-side run (``alias_study_run.py`` with ``rechunk: {all_records: true}``,
reach PREREG §2, §6 step 2, §10): every accepted record in every corpus arm, the export checks
without a label table, and **byte-identity** of the existing modes (dry run, pilot, main, rechunk
stage 1) against the runner as it was before the option existed. A fake API and a fake census
index; no store, no network."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
_EVAL = _HERE.parents[1] / "scripts" / "eval"
for _p in (_HERE, _EVAL, _EVAL / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import alias_study_common as asc  # noqa: E402
import alias_study_run as ar  # noqa: E402
import found10 as f10  # noqa: E402
import test_alias_study_run as t  # noqa: E402

#: the branch's last commit before the reach run's option (the runner and rechunk_prep as they were)
BASE_COMMIT = "a8c2d19f90a4563ad7f2bee2bd887d9565afd1d0"
REPO = _HERE.parents[2]


def reach_setup(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
    """The stage-1 rechunk config with the label table replaced by ``all_records``."""
    w, cfg = t.rechunk_setup(tmp, monkeypatch)
    cfg["rechunk"] = {"all_records": True}
    return w, cfg


def test_reach_runs_every_record_in_every_arm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w, cfg = reach_setup(tmp_path, monkeypatch)
    every = {r["record_id"] for r in w.records}
    for c in asc.CORPUS_ARMS:
        api = t.API(w)
        assert t.run(cfg, api, corpus=c, census=t.Census()) == 0
        asked = [api.q2rec[b["query"]]["record_id"] for b in api.bodies]
        assert set(asked) == every and len(asked) == 3 * len(every)
        assert {b["top_k"] for b in api.bodies} == {50} and {b["collection"] for b in api.bodies} == {
            t.collection(c, "none")}
        s = t.summary(cfg, c, "none")
        assert s["status"] == "complete" and s["mode"] == "rechunk" and s["exploratory"] is True
        st = s["study"]
        assert st["mode"] == "rechunk" and st["rechunk_scope"] == "all_records" and st["stage"] == "A"
        assert st["record_set"]["rechunk_records"] == len(every) and st["record_set"]["rechunk_scope"] == "all_records"
        pres = st["rechunk_presence"]
        assert pres["scope"] == "all_records" and pres["records"] == len(every)
        assert pres["chunks_in_export"] == pres["expected_chunks"] == 100
        assert "label_table_sha256" not in pres and "gold_presence" not in st
        for cond in s["conditions"].values():
            assert not set(ar.PILOT_WITHHELD_CONDITION_KEYS) & set(cond)
            assert cond["outcomes"] == ar.REACH_OUTCOMES_NOTE
        rows = asc.read_jsonl(Path(cfg["out_dir"]) / c / "none.jsonl")
        assert len(rows) == 3 * len(every) and all(len(x["ranked_chunk_ids"]) == 50 for x in rows)


def test_reach_export_checks_without_a_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The export must hold expected_chunks chunks and carry the confirmatory descriptor (and the
    receipt's, when it records one); refused before any API call."""
    import alias_control_chunks as acc

    w, cfg = reach_setup(tmp_path, monkeypatch)
    api = t.API(w)
    (tmp_path / "small").mkdir()
    cfg["corpora"]["base"]["chunk_export"] = t.rechunk_exports(tmp_path / "small", w, n=90)["base"]
    with pytest.raises(f10.Refused, match="holds 90 chunks, corpora.base.expected_chunks is 100"):
        t.run(cfg, api, census=t.Census())
    (tmp_path / "fixed").mkdir()
    cfg["corpora"]["base"]["chunk_export"] = t.rechunk_exports(tmp_path / "fixed", w,
                                                               desc=acc.STAGE_A_DESCRIPTOR)["base"]
    with pytest.raises(f10.Refused, match="another chunk_descriptor"):
        t.run(cfg, api, census=t.Census())
    (tmp_path / "ok").mkdir()
    cfg["corpora"]["base"]["chunk_export"] = t.rechunk_exports(tmp_path / "ok", w)["base"]
    rpath = Path(cfg["collections"]["base"]["none"]["load_receipt"])
    rec = json.loads(rpath.read_text())
    rpath.write_text(json.dumps({**rec, "chunk_descriptor": acc.STAGE_A_DESCRIPTOR}))
    with pytest.raises(f10.Refused, match="load receipt records chunk_descriptor"):
        t.run(cfg, api, census=t.Census())
    assert api.bodies == [] and api.config_reads == 0


def test_reach_config_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w, cfg = reach_setup(tmp_path, monkeypatch)
    ar.validate_config(cfg)
    assert ar.rechunk_all_records(cfg) and ar.config_mode(cfg) == "rechunk"

    def bad(mut: Any, msg: str) -> None:
        c = json.loads(json.dumps(cfg))
        mut(c)
        with pytest.raises(f10.Refused, match=msg):
            ar.validate_config(c)

    bad(lambda c: c.update(rechunk={"all_records": False}), "must be true")
    bad(lambda c: c.update(rechunk={"all_records": "yes"}), "must be true")
    bad(lambda c: c.update(rechunk={"all_records": True, "label_table": "x.jsonl"}), "exclude each other")
    bad(lambda c: c.update(pilot=True), "excludes dry_run and pilot")
    bad(lambda c: c.update(dry_run=True), "excludes dry_run and pilot")
    bad(lambda c: c["collections"]["random2"].pop("none"), "exactly the attachments")
    # the stage-1 shape still needs its table
    bad(lambda c: c.update(rechunk={"other": 1}), "label_table")
    assert not ar.rechunk_all_records({"rechunk": {"label_table": "x"}})
    # the record set is verified (the full accepted file) as in every non-dry mode
    monkeypatch.setattr(asc, "verify_record_set", lambda path, recs: (_ for _ in ()).throw(SystemExit("not frozen")))
    with pytest.raises(SystemExit, match="not frozen"):
        ar.load_study_records(ar.validate_config(cfg))


def test_reach_and_stage1_dirs_share_the_mode_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both are mode ``rechunk``; a main-run directory is still refused."""
    w, cfg = reach_setup(tmp_path, monkeypatch)
    Path(cfg["out_dir"]).mkdir(parents=True)
    (Path(cfg["out_dir"]) / ar.MODE_MARKER).write_text("main\n")
    with pytest.raises(f10.Refused, match="STUDY-MODE"):
        t.run(cfg, t.API(w), census=t.Census())


# ------------------------------------------------------------------ byte-identity of the existing modes

def _old_modules(dst: Path, mutate: bool = False) -> Path | None:
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("alias_study_run.py", "rechunk_prep.py"):
        r = subprocess.run(["git", "show", f"{BASE_COMMIT}:python/scripts/eval/{name}"], cwd=REPO,
                           capture_output=True, text=True)
        if r.returncode != 0:
            return None
        text = r.stdout
        if mutate and name == "alias_study_run.py":
            text = text.replace('"chunking; rechunk outcomes come from the label table (rechunk_stage1.py)"',
                                '"chunking; rechunk outcomes come from the label table (elsewhere)"')
        (dst / name).write_text(text, encoding="utf-8")
    return dst


def _snapshot(work: Path, out: Path, old: Path | None = None) -> str:
    env = {**os.environ, "PYTHONPATH": str(REPO / "python")}
    cmd = [sys.executable, str(_HERE / "run_mode_snapshot.py"), str(work), str(out)]
    if old is not None:
        cmd.append(str(old))
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=work.parent)
    assert r.returncode == 0, r.stderr[-2000:]
    return out.read_text(encoding="utf-8")


def test_existing_modes_are_byte_identical_to_the_base_commit(tmp_path: Path) -> None:
    """Dry run, pilot, main and rechunk stage 1 write the same rows and summaries (times, latencies,
    provenance and the fixture's timestamped file shas removed) under this runner as under the
    runner of the base commit; a mutated copy of the old runner is told apart (the check is not
    vacuous)."""
    old = _old_modules(tmp_path / "old")
    if old is None:
        pytest.skip(f"the base commit {BASE_COMMIT[:8]} is not in this clone")
    new = _snapshot(tmp_path / "w-new", tmp_path / "new.json")
    was = _snapshot(tmp_path / "w-old", tmp_path / "old.json", old)
    snaps = json.loads(new)
    assert set(snaps) == {"dry", "pilot", "main", "rechunk"}
    assert all(any(k.endswith(".summary.json") for k in v["files"]) for v in snaps.values())
    assert snaps["rechunk"]["rc"] == [0, 0, 0] and snaps["main"]["rc"] == [0, 0]
    assert new == was
    mutated = _old_modules(tmp_path / "mutated", mutate=True)
    assert mutated is not None
    assert _snapshot(tmp_path / "w-mut", tmp_path / "mut.json", mutated) != new
