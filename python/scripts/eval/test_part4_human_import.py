"""Tests for part4_human_import on synthetic keys and grades (no private data)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import part4_human_import as hi  # noqa: E402


def make_key(n_a: int = 4, n_pairs: int = 3) -> dict:
    key = {}
    for j in range(n_a):  # Sol and Opus disagree on correct vs not, by construction
        key[f"a{j}"] = {"part": "A", "record_id": f"S{j}", "sol": "correct" if j % 2 else "incorrect",
                        "opus48": "incorrect" if j % 2 else "correct", "answer_key": f"k{j}"}
    for j in range(n_pairs):  # discordant under Sol: T512o0 correct, L not
        for arm, lab in (("T512o0", "correct"), ("L", "partial")):
            key[f"b{j}{arm}"] = {"part": "B", "record_id": f"M{j}", "reader": "scout",
                                 "condition": "B4096", "arm": arm, "sol": lab}
    return key


def test_human_agrees_with_sol_everywhere_is_validated():
    key = make_key()
    human = {i: v["sol"] for i, v in key.items()}
    r = hi.analyse(key, human)
    assert r["part_a"]["sides_with_sol"]["p"] == 1.0
    assert r["part_b"]["human"] == {"gained": 3, "lost": 0, "net": 3} == r["part_b"]["sol"]
    assert r["part_b"]["kappa_correct_vs_not_human_vs_sol"]["kappa"] == pytest.approx(1.0)
    assert r["verdict"] == "validated"


def test_human_sides_with_opus_on_a_is_not_validated():
    key = make_key()
    human = {i: v["sol"] for i, v in key.items()}
    for i, v in key.items():
        if v["part"] == "A":
            human[i] = v["opus48"]
    r = hi.analyse(key, human)
    assert r["part_a"]["sides_with_sol"]["p"] == 0.0
    assert r["verdict"] == "not validated"


def test_human_agrees_with_neither_is_grader_unvalidated():
    key = make_key()
    # On A the human calls everything partial (not correct). Sol says correct on half, Opus on the other half.
    # On B the human reverses Sol.
    human = {}
    for i, v in key.items():
        if v["part"] == "A":
            human[i] = "partial"
        else:
            human[i] = "partial" if v["sol"] == "correct" else "correct"
    r = hi.analyse(key, human)
    assert r["kappa_correct_vs_not"]["human_vs_sol_all"]["kappa"] < 0.6
    assert r["kappa_correct_vs_not"]["human_vs_opus48"]["kappa"] < 0.6
    assert r["verdict"] == "grader-unvalidated"


def test_incomplete_pairs_and_ungraded_items_are_counted_not_scored():
    key = make_key()
    human = {i: v["sol"] for i, v in key.items()}
    del human["b0L"], human["a0"]
    r = hi.analyse(key, human)
    assert r["part_b"]["pairs_complete"] == 2
    assert r["part_a"]["n"] == 3
    assert r["ungraded_items"] == ["a0", "b0L"]


def test_grades_from_another_export_are_refused(tmp_path):
    items = tmp_path / "items.json"
    items.write_text(json.dumps({"items": []}))
    g = tmp_path / "g.json"
    g.write_text(json.dumps({"export_id": "part4-000000000000", "grades": []}))
    with pytest.raises(SystemExit):
        hi.load_grades(g, hi.export_id(items))


def test_unknown_grade_is_refused(tmp_path):
    items = tmp_path / "items.json"
    items.write_text("{}")
    eid = hi.export_id(items)
    g = tmp_path / "g.json"
    g.write_text(json.dumps({"export_id": eid, "grades": [{"item": "a0", "grade": "maybe"}]}))
    with pytest.raises(SystemExit):
        hi.load_grades(g, eid)


def test_second_rater_kappa():
    key = make_key()
    human = {i: v["sol"] for i, v in key.items()}
    r = hi.analyse(key, human, dict(human))
    assert r["human_human"]["n"] == 4
    assert r["human_human"]["kappa_4class"]["kappa"] == pytest.approx(1.0)


def test_refuses_to_write_inside_repo(tmp_path):
    items = tmp_path / "items.json"
    items.write_text("{}")
    (tmp_path / "items.key.json").write_text(json.dumps({"key": {}}))
    g = tmp_path / "g.json"
    g.write_text(json.dumps({"export_id": hi.export_id(items), "grades": []}))
    inside = Path(hi.__file__).resolve().parent / "should-not-exist.json"
    with pytest.raises(SystemExit):
        hi.main(["--items", str(items), "--grades", str(g), "--out", str(inside)])
    assert not inside.exists()
