"""Unit tests for the EXPLORATORY QASPER dispersion script, on a tiny synthetic paper.

Run: python3 -m pytest docs/plans/results/qasper-dispersion-2026-11/test_qasper_dispersion.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import qasper_dispersion as qd

PAPER = [
    {"section_name": "Introduction", "paragraphs": ["intro one", "intro two", "shared"]},
    {"section_name": "Method", "paragraphs": ["method a"]},
    {"section_name": "Method ::: Data", "paragraphs": ["data  para", "shared"]},
    {"section_name": "Results", "paragraphs": ["result x"]},
    {"section_name": None, "paragraphs": ["orphan"]},
]


def ans(evidence, **kw):
    a = {"unanswerable": False, "extractive_spans": ["x"], "free_form_answer": "", "yes_no": None,
         "evidence": evidence, "highlighted_evidence": []}
    a.update(kw)
    return a


def test_levels_and_names():
    idx = qd.section_index(PAPER)
    assert idx["full"] == ["Introduction", "Method", "Method ::: Data", "Results", "<unnamed:4>"]
    assert idx["top"] == ["Introduction", "Method", "Method", "Results", "<unnamed:4>"]


def test_mapping_rules():
    idx = qd.section_index(PAPER)
    assert qd.map_evidence("intro one", idx) == ("exact", [0])
    assert qd.map_evidence("data para", idx) == ("normalised", [2])
    assert qd.map_evidence("Method ::: Data", idx) == ("heading", [2])
    assert qd.map_evidence("shared", idx) == ("exact", [0, 2])
    assert qd.map_evidence("nowhere", idx) == ("unmatched", [])


def test_single_section_two_paragraphs():
    r = qd.analyse_answer(ans(["intro one", "intro two"]), qd.section_index(PAPER))
    assert (r["n_para"], r["n_full"], r["n_top"]) == (2, 1, 1)


def test_subsection_counts_at_full_not_top():
    r = qd.analyse_answer(ans(["method a", "data para"]), qd.section_index(PAPER))
    assert (r["n_para"], r["n_full"], r["n_top"]) == (2, 2, 1)
    assert r["entry_contiguous"] and r["all_mapped"] and not r["all_exact"]


def test_cross_top_noncontiguous_and_first_section():
    r = qd.analyse_answer(ans(["intro one", "result x"]), qd.section_index(PAPER))
    assert (r["n_full"], r["n_top"]) == (2, 2)
    assert not r["entry_contiguous"] and not r["top_contiguous"]
    assert r["in_first_section"] and r["n_top_excl_first"] == 1


def test_ambiguous_resolves_earliest_and_floats_split_out():
    r = qd.analyse_answer(ans(["shared", "FLOAT SELECTED: Table 1"]), qd.section_index(PAPER))
    assert r["ambiguous_cross_section"] and r["entries"] == [0]
    assert (r["n_float"], r["n_text"], r["n_full"]) == (1, 1, 1)
    assert not qd.eligible_text(r)


def test_answer_types():
    assert qd.answer_type(ans([], extractive_spans=[], free_form_answer="f")) == "abstractive"
    assert qd.answer_type(ans([], extractive_spans=[], yes_no=False)) == "yes_no"
    assert qd.answer_type(ans([], unanswerable=True)) == "unanswerable"


def test_wilson_and_fleiss():
    w = qd.wilson(5, 10)
    assert w["p"] == 0.5 and abs(w["lo"] - 0.2366) < 1e-4 and abs(w["hi"] - 0.7634) < 1e-4
    perfect = qd.fleiss_variable([[1, 1], [0, 0], [0, 0, 0], [1, 1]])
    assert perfect["kappa"] == 1.0 and perfect["all_agree_share"] == 1.0
    # two raters, always disagree with balanced marginals -> kappa -1
    assert qd.fleiss_variable([[1, 0], [0, 1]])["kappa"] == -1.0
