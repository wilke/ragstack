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
    key = make_key(n_pairs=6)  # 12 part-B items, above MIN_OPUS_B
    opus_b = {i: v["sol"] for i, v in key.items() if v["part"] == "B"}  # Opus agrees with Sol on B
    # On B the human reverses both models; on A the human calls everything partial.
    human = {i: ("partial" if v["part"] == "A" else ("partial" if v["sol"] == "correct" else "correct"))
             for i, v in key.items()}
    r = hi.analyse(key, human, opus_b=opus_b)
    assert r["neither_check"]["evaluable"] is True
    assert r["neither_check"]["kappa_human_vs_sol_B"] < 0.6
    assert r["neither_check"]["human_vs_opus48_B"]["kappa"] < 0.6
    assert r["verdict"] == "grader-unvalidated"


def test_sides_with_sol_on_a_but_low_kappa_b_is_not_validated_not_neither():
    # The review's case: P_A = 1, κ_B low. The degenerate part-A Opus κ must not make it "neither".
    key = make_key(n_pairs=6)
    human = {i: v["sol"] for i, v in key.items() if v["part"] == "A"}
    human.update({i: ("partial" if v["sol"] == "correct" else "correct") for i, v in key.items() if v["part"] == "B"})
    r = hi.analyse(key, human)  # no Opus labels for B: neither-check not evaluable
    assert r["part_a"]["sides_with_sol"]["p"] == 1.0
    assert r["neither_check"]["evaluable"] is False
    assert r["verdict"] == "not validated"
    assert r["kappa_human_vs_opus48_A_complement_of_P_A"]["kappa"] == pytest.approx(-1.0)


def test_too_few_opus_b_items_is_not_evaluable():
    key = make_key(n_pairs=3)  # 6 part-B items < MIN_OPUS_B
    opus_b = {i: v["sol"] for i, v in key.items() if v["part"] == "B"}
    human = {i: ("partial" if v["sol"] == "correct" else "correct") for i, v in key.items()}
    r = hi.analyse(key, human, opus_b=opus_b)
    assert r["neither_check"]["evaluable"] is False
    assert r["verdict"] == "not validated"


def test_part_a_is_scored_correct_vs_not_not_exact_label():
    key = make_key()
    human = {i: v["sol"] for i, v in key.items()}
    # Item a0: Sol incorrect, Opus correct. The human says partial, which is not correct, so it sides with Sol.
    human["a0"] = "partial"
    r = hi.analyse(key, human)
    assert r["part_a"]["sides_with_sol"]["p"] == 1.0


def test_incomplete_pairs_and_ungraded_items_are_counted_not_scored():
    key = make_key()
    human = {i: v["sol"] for i, v in key.items()}
    del human["b0L"], human["a0"]
    r = hi.analyse(key, human)
    assert r["part_b"]["pairs_complete"] == 2
    assert r["part_b"]["kappa_correct_vs_not_human_vs_sol"]["n"] == 4  # items of complete pairs only
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
    key = make_key()
    items = tmp_path / "items.json"
    items.write_text("{}")
    (tmp_path / "items.key.json").write_text(json.dumps({"key": key}))
    g = tmp_path / "g.json"
    g.write_text(json.dumps({"export_id": hi.export_id(items),
                             "grades": [{"item": i, "grade": v["sol"], "note": ""} for i, v in key.items()]}))
    ok = tmp_path / "out.json"
    assert hi.main(["--items", str(items), "--grades", str(g), "--out", str(ok)]) == 0 and ok.exists()
    inside = Path(hi.__file__).resolve().parent / "should-not-exist.json"
    with pytest.raises(SystemExit, match="inside the repository"):
        hi.main(["--items", str(items), "--grades", str(g), "--out", str(inside)])
    assert not inside.exists()


def test_null_grades_are_skipped_and_duplicates_or_unknown_items_refused(tmp_path):
    items = tmp_path / "items.json"
    items.write_text("{}")
    eid = hi.export_id(items)
    g = tmp_path / "g.json"
    g.write_text(json.dumps({"export_id": eid, "grades": [{"item": "a0", "grade": None, "note": ""},
                                                           {"item": "a1", "grade": "correct", "note": ""}]}))
    assert hi.load_grades(g, eid) == {"a1": "correct"}
    g.write_text(json.dumps({"export_id": eid, "grades": [{"item": "a1", "grade": "correct"},
                                                           {"item": "a1", "grade": "partial"}]}))
    with pytest.raises(SystemExit, match="twice"):
        hi.load_grades(g, eid)
    with pytest.raises(SystemExit, match="not in the key"):
        hi.analyse(make_key(), {"zz": "correct"})


def test_opus_labels_for_b_from_per_item():
    key = make_key(n_pairs=1)
    per_item = {"M0": {"T512o0": {"B4096": {"readers": {"scout": {"opus48": "correct"}}}},
                       "L": {"B4096": {"readers": {"scout": {"opus48": None}}}}}}
    assert hi.opus_labels_for_b(key, per_item) == {"b0T512o0": "correct"}
