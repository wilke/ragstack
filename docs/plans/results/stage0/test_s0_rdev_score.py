"""Tests for ``s0_rdev_score.py`` -- the R-dev two-reader scorer.

Run with::

    cd docs/plans/results/stage0 && python3 -m pytest test_s0_rdev_score.py -q

Every verdict in this file is **synthetic**: it is generated from a rule stated in the test
itself. Nothing here reads an R-dev pair, and the one test that touches the real artifacts
asserts only that the scorer REFUSES them, because they are blank and the read is still
``PENDING-HUMAN``.

The suite's own honesty check is ``test_broken_kappa_is_caught``: it substitutes the
classic wrong implementation (raw observed agreement in place of the chance-corrected
statistic) and proves an assertion in this file goes red. A test that cannot fail is not a
test.
"""
from __future__ import annotations

import csv
import json
import pathlib
import subprocess
import sys

import pytest

import s0_math as M
import s0_rdev_score as S

HERE = pathlib.Path(__file__).resolve().parent
PY = sys.executable
V = list(S.VERDICTS)


# ------------------------------------------------------------------ fixtures/helpers
def _pair_ids(n: int) -> list[str]:
    return [f"2014_5__{1000 + i}" for i in range(n)]


def write_sample(path: pathlib.Path, pids, strata=None) -> pathlib.Path:
    strata = strata or ["model_positive"] * len(pids)
    meta = {
        "seed": 20260915, "target": len(pids), "drawn": len(pids),
        "strata_definition": {"model_positive": "Scout returned >= 1 evidence set"},
        "drawn_by_stratum": {}, "shortfalls": {},
        "pairs": [{"topic": p.split("__")[0], "docno": p.split("__")[1],
                   "stratum": s, "n_sets": 1, "windowed": False, "doc_chars": 5000}
                  for p, s in zip(pids, strata)],
    }
    path.write_text(json.dumps(meta))
    return path


def write_csv(path: pathlib.Path, pids, verdicts) -> pathlib.Path:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["pair_id", "verdict", "notes"])
        for p, v in zip(pids, verdicts):
            w.writerow([p, v or "", ""])
    return path


def write_labels(path: pathlib.Path, pids, positives) -> pathlib.Path:
    with open(path, "w") as fh:
        for p, pos in zip(pids, positives):
            t, d = p.split("__")
            sets = [{"spans": [{"unit": 0}]}] if pos else []
            fh.write(json.dumps({"topic": t, "docno": d, "sets": sets}) + "\n")
    return path


def scenario(tmp_path, va, vb, strata=None, positives=None, adjudicated=None,
             extras=None) -> dict:
    """Build a complete synthetic input set and score it."""
    pids = _pair_ids(len(va))
    sample = write_sample(tmp_path / "sample.json", pids, strata)
    a = write_csv(tmp_path / "A.csv", pids, va)
    b = write_csv(tmp_path / "B.csv", pids, vb)
    if positives is None:
        positives = [v not in ("correctly-none",) for v in va]
    labels = write_labels(tmp_path / "labels.jsonl", pids, positives)
    adj = write_csv(tmp_path / "ADJ.csv", pids, adjudicated) if adjudicated else None
    ex = {}
    for name, vs in (extras or {}).items():
        ex[name] = str(write_csv(tmp_path / f"{name}.csv", pids, vs))
    return S.build_report(a, b, sample, labels, adj, ex)


# ---------------------------------------------------------------------------- kappa
def test_identical_readers_give_kappa_one(tmp_path):
    """Two readers who wrote the same verdict everywhere: kappa = 1.0 exactly."""
    va = [V[i % 6] for i in range(60)]
    rep = scenario(tmp_path, va, list(va))
    assert rep["status"] == "SCORED"
    assert rep["n_scored"] == 60
    hh = rep["kappa_human_human_6cat"]
    assert hh["kappa"] == pytest.approx(1.0)
    assert hh["percent_agreement"] == pytest.approx(1.0)
    lo, hi = hh["kappa_ci95"]
    assert lo == pytest.approx(1.0) and hi == pytest.approx(1.0)


def test_identical_readers_with_skewed_marginals_still_kappa_one(tmp_path):
    """Skew does not dent perfect agreement: 57 'correct' + 3 others is still kappa 1.

    Note for the reader of this file: this case does NOT discriminate a chance-corrected
    kappa from raw agreement -- both are 1.0. The case that does is
    ``test_broken_kappa_is_caught`` below, which is why it exists.
    """
    va = ["correct"] * 57 + ["wrong-location", "non-minimal", "missed-evidence"]
    rep = scenario(tmp_path, va, list(va))
    assert rep["kappa_human_human_6cat"]["kappa"] == pytest.approx(1.0)
    assert rep["kappa_human_human_6cat"]["percent_agreement"] == pytest.approx(1.0)


def test_fixed_permutation_of_categories_gives_kappa_near_zero(tmp_path):
    """Balanced data, reader B = a fixed 1-step rotation of A's categories.

    Six categories, 20 pairs each, B never agrees with A: observed agreement 0, expected
    agreement 1/6, so kappa = (0 - 1/6)/(1 - 1/6) = -0.2 exactly. The CI must contain that
    and must sit far below any of §6.6.4's thresholds.
    """
    va = [V[i % 6] for i in range(120)]
    vb = [V[(V.index(x) + 1) % 6] for x in va]
    rep = scenario(tmp_path, va, vb)
    hh = rep["kappa_human_human_6cat"]
    assert hh["percent_agreement"] == pytest.approx(0.0)
    assert hh["kappa"] == pytest.approx(-0.2, abs=1e-9)
    lo, hi = hh["kappa_ci95"]
    assert lo <= hh["kappa"] <= hi
    assert hi < 0.40, "a rotation of the categories must not clear the §6.6.4 floor"


def test_permuted_balanced_readers_are_kappa_zero_when_shuffled_independently(tmp_path):
    """Independent (seeded) reader verdicts over balanced categories: kappa ~ 0, CI spans 0."""
    import random
    rng = random.Random(4242)
    va = [rng.choice(V) for _ in range(300)]
    vb = [rng.choice(V) for _ in range(300)]
    rep = scenario(tmp_path, va, vb)
    hh = rep["kappa_human_human_6cat"]
    assert abs(hh["kappa"]) < 0.15
    lo, hi = hh["kappa_ci95"]
    assert lo < 0.0 < hi, "an independent pair of readers must have a CI containing zero"


def test_broken_kappa_is_caught():
    """The suite bites: raw observed agreement in place of kappa goes red here.

    The discriminating case is skewed marginals with near-perfect agreement -- 90 pairs
    both readers call `correct`, 5 each where one says `correct` and the other does not.
    Raw agreement is 0.90; the chance-corrected statistic is NEGATIVE, because two readers
    who both say `correct` almost always would agree about that often by luck alone. This
    is exactly the case §6.6.4 must not be fooled by.
    """
    a = ["correct"] * 90 + ["correct"] * 5 + ["missed-evidence"] * 5
    b = ["correct"] * 90 + ["missed-evidence"] * 5 + ["correct"] * 5
    ai = [S.VIDX[x] for x in a]
    bi = [S.VIDX[x] for x in b]
    kappa, po, _pe = M.cohen_kappa(ai, bi, len(V))

    # what the implementation actually does
    assert po == pytest.approx(0.90)
    assert kappa < 0.0
    assert kappa < 0.40, "chance-corrected kappa must fail the §6.6.4 floor here"

    # the deliberately broken implementation: observed agreement, no chance correction
    def broken_kappa(x, y, k):
        cm = M.confusion(x, y, k)
        return float(cm.trace()) / float(cm.sum())

    broken = broken_kappa(ai, bi, len(V))
    assert broken == pytest.approx(0.90)

    # and the proof that the assertion above would go red on the broken implementation
    with pytest.raises(AssertionError):
        assert broken < 0.40, "chance-corrected kappa must fail the §6.6.4 floor here"

    # the same substitution flips the acceptance table from FAIL to PASS -- the bug is not
    # cosmetic, it changes the study's verdict.
    real_rows = S.acceptance_table(kappa, [kappa, kappa], None, None, None, None)
    broken_rows = S.acceptance_table(broken, [broken, broken], None, None, None, None)
    real = next(r for r in real_rows if r["condition"] == "< 0.40")
    fake = next(r for r in broken_rows if r["condition"] == "< 0.40")
    assert real["verdict"] == "FAIL" and fake["verdict"] == "PASS"


# --------------------------------------------------------------------------- binary
def test_binary_collapse_and_ambiguous_exclusion(tmp_path):
    """{correct, correctly-none} vs the three error verdicts; ambiguous is dropped."""
    # 20 pairs: both readers agree the label is acceptable but disagree WHICH acceptable
    # verdict -- the binary collapse must call that perfect agreement.
    va = ["correct"] * 10 + ["correctly-none"] * 10
    vb = ["correctly-none"] * 10 + ["correct"] * 10
    # 20 more where both call it an error, again with different error verdicts
    va += ["wrong-location"] * 10 + ["non-minimal"] * 10
    vb += ["non-minimal"] * 10 + ["missed-evidence"] * 10
    # 6 ambiguous pairs, one rater or the other
    va += ["ambiguous", "ambiguous", "ambiguous", "correct", "correct", "correct"]
    vb += ["correct", "correct", "correct", "ambiguous", "ambiguous", "ambiguous"]
    rep = scenario(tmp_path, va, vb)

    hh = rep["kappa_human_human_6cat"]
    hb = rep["kappa_human_human_binary"]
    assert hh["percent_agreement"] == pytest.approx(0.0), "no 6-category cell ever agrees"
    assert hb["ambiguous_excluded"] == 6, "a pair either rater called ambiguous is excluded"
    assert hb["n"] == 40
    assert hb["percent_agreement"] == pytest.approx(1.0)
    assert hb["kappa"] == pytest.approx(1.0)
    # and the exclusion is not a silent drop
    assert "EXCLUDED" in hb["definition"]


def test_binary_collapse_membership_is_the_specified_partition(tmp_path):
    """correct/correctly-none => acceptable; the other three => error."""
    assert set(S.BIN_ACCEPTABLE) == {"correct", "correctly-none"}
    assert set(S.BIN_ERROR) == {"wrong-location", "non-minimal", "missed-evidence"}
    assert set(S.BIN_EXCLUDED) == {"ambiguous"}
    assert set(S.BIN_ACCEPTABLE) | set(S.BIN_ERROR) | set(S.BIN_EXCLUDED) == set(V)

    # a reader who calls every error-verdict pair acceptable is kappa 0 on the collapse
    va = ["correct"] * 20 + ["wrong-location"] * 20
    vb = ["correct"] * 20 + ["correct"] * 20
    rep = scenario(tmp_path, va, vb)
    hb = rep["kappa_human_human_binary"]
    assert hb["percent_agreement"] == pytest.approx(0.5)
    assert hb["kappa"] == pytest.approx(0.0, abs=1e-9)


# ------------------------------------------------------------------------- unread
def test_unread_pairs_are_not_scored_and_are_counted(tmp_path):
    """Blank cells are 'not yet read'. Only pairs BOTH readers completed are scored."""
    n = 40
    va = [V[i % 6] for i in range(n)]
    vb = list(va)
    va[:5] = [None] * 5             # A left 5 blank
    vb[5:12] = [None] * 7           # B left a different 7 blank
    rep = scenario(tmp_path, va, vb)
    assert rep["coverage"]["A"]["unread_blank"] == 5
    assert rep["coverage"]["B"]["unread_blank"] == 7
    assert rep["coverage"]["A"]["read"] == 35
    assert rep["coverage"]["B"]["read"] == 33
    assert rep["n_scored"] == n - 12, "the union of the two blank sets is excluded"
    assert rep["kappa_human_human_6cat"]["n"] == n - 12
    assert rep["kappa_human_human_6cat"]["kappa"] == pytest.approx(1.0)


def test_pair_missing_from_a_sheet_is_reported(tmp_path):
    pids = _pair_ids(20)
    sample = write_sample(tmp_path / "sample.json", pids)
    va = [V[i % 6] for i in range(20)]
    a = write_csv(tmp_path / "A.csv", pids, va)
    b = write_csv(tmp_path / "B.csv", pids[:15], va[:15])       # B's sheet is short
    labels = write_labels(tmp_path / "labels.jsonl", pids, [True] * 20)
    rep = S.build_report(a, b, sample, labels)
    assert len(rep["coverage"]["B"]["missing_from_sheet"]) == 5
    assert rep["n_scored"] == 15


def test_bad_verdict_word_is_rejected(tmp_path):
    pids = _pair_ids(12)
    p = write_csv(tmp_path / "A.csv", pids, ["correct"] * 11 + ["looks-fine"])
    with pytest.raises(SystemExit) as e:
        S.read_verdicts(p)
    assert "vocabulary" in str(e.value)


def test_duplicate_pair_id_is_rejected(tmp_path):
    pids = _pair_ids(5)
    p = write_csv(tmp_path / "A.csv", pids + pids[:1], ["correct"] * 6)
    with pytest.raises(SystemExit) as e:
        S.read_verdicts(p)
    assert "duplicate" in str(e.value)


# ------------------------------------------------------------------------ refusal
def test_fewer_than_ten_scored_pairs_is_refused(tmp_path):
    n = 30
    va = [V[i % 6] for i in range(n)]
    vb = list(va)
    vb[9:] = [None] * (n - 9)                # only 9 pairs read by both
    rep = scenario(tmp_path, va, vb)
    assert rep["n_scored"] == 9
    assert rep["kappa_reported"] is False
    assert "fewer than 10 scored pairs" in rep["status"]
    assert "kappa_human_human_6cat" not in rep
    assert "acceptance_table_6_6_4" not in rep
    md = S.render_md(rep)
    assert "κ" not in md.split("## No statistics")[1] or "No statistics" in md


def test_exactly_ten_scored_pairs_is_accepted(tmp_path):
    n = 30
    va = [V[i % 6] for i in range(n)]
    vb = list(va)
    vb[10:] = [None] * (n - 10)
    rep = scenario(tmp_path, va, vb)
    assert rep["n_scored"] == 10
    assert rep["kappa_reported"] is True


def test_cli_on_the_real_blank_sheets_exits_nonzero_and_prints_no_kappa(tmp_path):
    """The artifacts that ship in this directory are BLANK. Running the scorer on them
    must refuse -- never a kappa, never a fabricated verdict. This is the guard that keeps
    item 8 PENDING-HUMAN."""
    art = HERE / "artifacts"
    out = tmp_path / "r.json"
    md = tmp_path / "r.md"
    r = subprocess.run(
        [PY, str(HERE / "s0_rdev_score.py"),
         "--a", str(art / "rdev_verdicts_A.csv"),
         "--b", str(art / "rdev_verdicts_B.csv"),
         "--sample", str(art / "rdev_sample.json"),
         "--labels", str(art / "labels-dev.jsonl"),
         "--out", str(out), "--md", str(md)],
        cwd=str(HERE), capture_output=True, text=True)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "fewer than 10 scored pairs" in r.stderr
    rep = json.loads(out.read_text())
    assert rep["n_scored"] == 0
    assert rep["kappa_reported"] is False
    assert rep["coverage"]["A"]["unread_blank"] == 100
    assert rep["coverage"]["B"]["unread_blank"] == 100
    body = md.read_text()
    assert "PENDING-HUMAN" in rep["status"]
    assert "κ(A–B)" not in body


# -------------------------------------------------------------------- per-stratum
def test_per_stratum_kappa_respects_the_n_threshold(tmp_path):
    va, vb, strata = [], [], []
    for name, n in (("model_positive", 24), ("model_negative", 12), ("deep_section", 3)):
        for i in range(n):
            va.append(V[i % 6])
            vb.append(V[i % 6])
            strata.append(name)
    rep = scenario(tmp_path, va, vb, strata=strata)
    per = rep["kappa_per_stratum_6cat"]["strata"]
    assert per["model_positive"]["kappa"] == pytest.approx(1.0)
    assert per["model_negative"]["kappa"] == pytest.approx(1.0)
    assert per["deep_section"]["kappa"] is None
    assert "n too small" in per["deep_section"]["note"]
    assert rep["scored_by_stratum"] == {"deep_section": 3, "model_negative": 12,
                                        "model_positive": 24}


# -------------------------------------------------------------------------- rates
def test_rates_and_wilson_uppers(tmp_path):
    # 100 pairs: 6 wrong-location, 4 non-minimal, 10 missed-evidence, 20 correctly-none,
    # 5 ambiguous, 55 correct
    va = (["wrong-location"] * 6 + ["non-minimal"] * 4 + ["missed-evidence"] * 10
          + ["correctly-none"] * 20 + ["ambiguous"] * 5 + ["correct"] * 55)
    rep = scenario(tmp_path, va, list(va))
    r = rep["rates"]["A"]
    assert r["label_error_rate"]["k"] == 10
    assert r["label_error_rate"]["rate"] == pytest.approx(0.10)
    assert r["missed_evidence_rate"]["rate"] == pytest.approx(0.10)
    assert r["correctly_none_rate"]["rate"] == pytest.approx(0.20)
    assert r["ambiguous_rate"]["rate"] == pytest.approx(0.05)
    assert r["label_error_rate"]["wilson95_upper"] == pytest.approx(
        M.wilson(10, 100, 0.95)[1])
    assert (r["label_error_rate"]["wilson95_upper"]
            > r["label_error_rate"]["wilson95_upper_one_sided"] > 0.10)
    # the label-error gate is on the Wilson UPPER, so p̂ = 0.10 already trips > 0.10
    row = next(x for x in rep["acceptance_table_6_6_4"] if x["condition"] == "> 0.10")
    assert row["verdict"] == "FAIL"
    # missed-evidence p̂ = 0.10 on n = 100 has a Wilson upper of 0.1744, above the 0.15
    # trigger -- the point estimate alone would have passed. That gap is the whole reason
    # §6.6.4 gates on the upper bound.
    assert r["missed_evidence_rate"]["wilson95_upper"] == pytest.approx(0.17437, abs=1e-4)
    me_row = next(x for x in rep["acceptance_table_6_6_4"] if x["condition"] == "> 0.15")
    assert me_row["verdict"] == "FAIL"
    assert r["missed_evidence_rate"]["rate"] < 0.15 < \
        r["missed_evidence_rate"]["wilson95_upper"]


def test_adjudicated_verdicts_govern_the_rates(tmp_path):
    n = 40
    va = ["wrong-location"] * n
    vb = ["correct"] * n
    adj = ["correct"] * n                       # the joint read said the labels were fine
    rep = scenario(tmp_path, va, vb, adjudicated=adj)
    assert "adjudicated" in rep["rates"]
    assert "A" not in rep["rates"]
    assert rep["rates"]["adjudicated"]["label_error_rate"]["k"] == 0
    assert rep["rates_governing"]["source"] == "adjudicated"
    # §6.6.3: the PRE-adjudication kappa is the one reported, and it is 0 here
    assert rep["kappa_human_human_6cat"]["percent_agreement"] == pytest.approx(0.0)
    assert rep["rates"]["adjudicated"]["note"].startswith("§6.6.3")


def test_without_adjudication_rates_are_per_reader(tmp_path):
    va = ["wrong-location"] * 20 + ["correct"] * 20
    vb = ["correct"] * 40
    rep = scenario(tmp_path, va, vb)
    assert set(rep["rates"]) == {"A", "B", "_note"}
    assert rep["rates"]["A"]["label_error_rate"]["k"] == 20
    assert rep["rates"]["B"]["label_error_rate"]["k"] == 0
    assert "worse of the two readers" in rep["rates_governing"]["source"]
    assert rep["rates_governing"]["label_error_rate"]["k"] == 20


# ------------------------------------------------------------- labeler vs human
def test_labeler_human_kappa_mapping_and_positive_class_agreement(tmp_path):
    # 30 pairs the labeler called positive, 20 it called negative
    va = (["correct"] * 20            # labeler +, human says evidence exists
          + ["wrong-location"] * 5    # labeler +, human says evidence exists
          + ["missed-evidence"] * 5   # labeler +, human says evidence exists
          + ["correctly-none"] * 15   # labeler -, human agrees there is none
          + ["missed-evidence"] * 5   # labeler -, human found evidence => disagreement
          + ["ambiguous"] * 5)        # excluded from the binary entirely
    positives = [True] * 30 + [False] * 20 + [True] * 5
    rep = scenario(tmp_path, va, list(va), positives=positives)
    lh = rep["kappa_labeler_human_binary"]
    assert lh["ambiguous_excluded"] == 5
    assert lh["n"] == 50
    pa = lh["positive_class_agreement"]
    # human-positive = 30 (the first 30) + 5 (labeler-negative missed-evidence) = 35
    assert pa["n_human_positive"] == 35
    assert pa["n_labeler_positive"] == 30
    assert pa["n_both_positive"] == 30
    assert pa["human_to_labeler"] == pytest.approx(30 / 35)
    assert pa["labeler_to_human"] == pytest.approx(1.0)
    # the mapping is stated in the output, not left implicit
    assert "missed-evidence" in lh["mapping"]
    assert "no localizable evidence" in lh["mapping"]
    # kappa: confusion [[30,0],[5,15]] with rows = labeler
    assert lh["confusion"] == [[30, 0], [5, 15]]
    assert lh["kappa"] == pytest.approx(
        M.kappa_from_confusion([[30, 0], [5, 15]]))


def test_labeler_human_uses_adjudicated_when_supplied(tmp_path):
    n = 30
    va = ["correct"] * n
    vb = ["correctly-none"] * n
    adj = ["correctly-none"] * n
    rep = scenario(tmp_path, va, vb, positives=[False] * n, adjudicated=adj)
    lh = rep["kappa_labeler_human_binary"]
    assert lh["human_side"] == "adjudicated"
    assert lh["percent_agreement"] == pytest.approx(1.0)
    assert "kappa_labeler_human_binary_readerB" not in rep


# --------------------------------------------------------------------- extras
def test_extra_grader_is_scored_but_quarantined(tmp_path):
    n = 40
    va = [V[i % 6] for i in range(n)]
    vb = list(va)
    agent = [V[(V.index(x) + 1) % 6] for x in va]      # the agent never agrees
    rep = scenario(tmp_path, va, vb, extras={"agent_grader": agent})
    g = rep["extra_graders"]
    assert "never a substitute for κ(human–human)" in g["header"]
    assert "cannot be satisfied" in g["warning"] or "can only be satisfied" in g["warning"]
    e = g["graders"]["agent_grader"]
    assert set(e["vs"]) == {"reader A", "reader B"}
    assert e["vs"]["reader A"]["six_category"]["percent_agreement"] == pytest.approx(0.0)
    # and the agent's numbers never touch kappa(human-human)
    assert rep["kappa_human_human_6cat"]["kappa"] == pytest.approx(1.0)
    assert rep["kappa_human_human_6cat"]["n"] == n


def test_extra_flag_parsing(tmp_path):
    with pytest.raises(SystemExit):
        S.main(["--a", "x", "--b", "y", "--extra", "no-equals-sign"])


# ------------------------------------------------------------- acceptance table
@pytest.mark.parametrize("kappa,expect_lt40,expect_mid", [
    (0.10, "FAIL", "PASS"),
    (0.50, "PASS", "FAIL"),
    (0.75, "PASS", "PASS"),
])
def test_acceptance_table_human_human_tiers(kappa, expect_lt40, expect_mid):
    rows = S.acceptance_table(kappa, None, None, None, None, None)
    hh = [r for r in rows if r["statistic"] == "κ(human–human)"]
    assert len(hh) == 2
    assert hh[0]["condition"] == "< 0.40" and hh[0]["verdict"] == expect_lt40
    assert hh[1]["condition"] == "0.40 – 0.60" and hh[1]["verdict"] == expect_mid
    assert "RUBRIC_FAILURE" in hh[0]["consequence"]
    assert "fresh ≥ 100-pair draw" in hh[1]["consequence"]


def test_acceptance_table_permission_row_uses_positive_class_agreement():
    rows = S.acceptance_table(0.8, None, 0.45, None,
                              {"human_to_labeler": 0.90, "labeler_to_human": 0.88}, None)
    perm = next(r for r in rows if r["polarity"] == "permission")
    assert perm["verdict"] == "PASS", "positive-class agreement >= 0.85 permits full strength"
    rows = S.acceptance_table(0.8, None, 0.45, None,
                              {"human_to_labeler": 0.90, "labeler_to_human": 0.50}, None)
    perm = next(r for r in rows if r["polarity"] == "permission")
    assert perm["verdict"] == "FAIL", "the WEAKER direction governs"


def test_acceptance_table_marks_unmeasurable_rows_not_evaluable():
    rows = S.acceptance_table(0.7, None, 0.7, None,
                              {"human_to_labeler": 0.9, "labeler_to_human": 0.9}, None)
    hall = next(r for r in rows if r["statistic"].startswith("hallucinated-span"))
    sc = next(r for r in rows if r["statistic"].startswith("self-consistency"))
    for r in (hall, sc, *[x for x in rows if "Wilson upper" in x["statistic"]]):
        assert r["verdict"] == "NOT-EVALUABLE"
        assert r["note"]
    assert "label_gates.json" in hall["note"]


def test_acceptance_table_thresholds_match_the_spec():
    """The numbers in the table are the SPEC's, not re-derived."""
    spec = (HERE.parent / "design" / "SPEC-confirmation-run.md").read_text()
    body = spec.split("#### 6.6.4")[1].split("#### 6.6.5")[0]
    for token in ("0.40", "0.60", "0.85", "0.10", "0.15", "0.05", "0.90",
                  "RUBRIC_FAILURE", "UNRESOLVED-BY-LABEL-OMISSION", "MODERATE"):
        assert token in body, f"{token} is not in §6.6.4 -- the scorer is out of date"
    rows = S.acceptance_table(0.7, None, 0.7, None,
                              {"human_to_labeler": 0.9, "labeler_to_human": 0.9}, None)
    conds = [r["condition"] for r in rows]
    assert conds == ["< 0.40", "0.40 – 0.60", "< 0.40", "0.40 – 0.60",
                     "≥ 0.60 / ≥ 0.85", "> 0.10", "> 0.15", "> 0.05", "< 0.90"]


# ------------------------------------------------------------------------- render
def test_markdown_renders_every_section(tmp_path):
    n = 60
    va = [V[i % 6] for i in range(n)]
    vb = [V[(i + (i % 3 == 0)) % 6] for i in range(n)]
    rep = scenario(tmp_path, va, vb, extras={"agent_grader": va})
    md = S.render_md(rep)
    for heading in ("## 1. Coverage", "## 2. κ(A–B)", "## 3. Rates",
                    "## 4. κ(labeler–human)", "agent/model graders",
                    "## 6. §6.6.4 acceptance table"):
        assert heading in md, heading
    assert "Cohen's kappa = (po - pe)/(1 - pe)" in md
    assert "10000 resamples" in md.replace(",", "")


def test_markdown_section_numbering_is_stable_without_extras(tmp_path):
    n = 30
    va = [V[i % 6] for i in range(n)]
    rep = scenario(tmp_path, va, list(va))
    md = S.render_md(rep)
    assert "## 5. agent/model graders" in md
    assert "None supplied" in md
    assert "## 6. §6.6.4 acceptance table" in md


def test_cli_end_to_end(tmp_path):
    n = 40
    pids = _pair_ids(n)
    va = [V[i % 6] for i in range(n)]
    write_sample(tmp_path / "sample.json", pids)
    write_csv(tmp_path / "A.csv", pids, va)
    write_csv(tmp_path / "B.csv", pids, va)
    write_labels(tmp_path / "labels.jsonl", pids, [v != "correctly-none" for v in va])
    out, md = tmp_path / "r.json", tmp_path / "r.md"
    rc = S.main(["--a", str(tmp_path / "A.csv"), "--b", str(tmp_path / "B.csv"),
                 "--sample", str(tmp_path / "sample.json"),
                 "--labels", str(tmp_path / "labels.jsonl"),
                 "--out", str(out), "--md", str(md)])
    assert rc == 0
    rep = json.loads(out.read_text())
    assert rep["n_scored"] == n
    assert rep["kappa_human_human_6cat"]["kappa"] == pytest.approx(1.0)
    assert rep["seed_scoring"] == 20260917 and rep["n_boot"] == 10000
    assert "§6.6.4 acceptance table" in md.read_text()


def test_bootstrap_is_deterministic(tmp_path):
    import random
    rng = random.Random(7)
    va = [rng.choice(V) for _ in range(50)]
    vb = [rng.choice(V) for _ in range(50)]
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    r1 = scenario(tmp_path / "a", va, vb)
    r2 = scenario(tmp_path / "b", va, vb)
    ci1 = r1["kappa_human_human_6cat"]["kappa_ci95"]
    ci2 = r2["kappa_human_human_6cat"]["kappa_ci95"]
    assert ci1 == ci2
    assert ci1[0] < ci1[1], "a 50-pair bootstrap CI must have width"
