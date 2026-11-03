"""Alias study power: the normal approximation reproduces the prereg's §8 tables, the MDEs, and
the simulation behaves (more discordance, more power; the gold-only gate costs power when the
gold direction disagrees)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import alias_study_power as pw  # noqa: E402

QS = (0.02, 0.03, 0.05, 0.10, 0.20)
#: prereg §8, the two tables (rounded to two decimals there)
TABLE = {
    ("H1", 0.85, 0.025): (.68, .85, .97, 1.00, 1.00), ("H1", 0.70, 0.025): (.28, .40, .59, .88, .99),
    ("H2", 0.85, 0.025): (.85, .96, 1.00, 1.00, 1.00), ("H2", 0.70, 0.025): (.40, .55, .77, .97, 1.00),
    ("H1", 0.85, 0.05): (.79, .91, .99, 1.00, 1.00), ("H1", 0.70, 0.05): (.40, .52, .71, .93, 1.00),
    ("H2", 0.85, 0.05): (.91, .98, 1.00, 1.00, 1.00), ("H2", 0.70, 0.05): (.52, .67, .85, .99, 1.00),
}


@pytest.mark.parametrize("key", sorted(TABLE))
def test_normal_approximation_reproduces_the_prereg(key: tuple[str, float, float]) -> None:
    member, p, alpha = key
    n = 600 if member == "H1" else 900
    got = tuple(round(pw.normal_power(n, q, p, alpha), 2) for q in QS)
    assert got == TABLE[key]


def test_mde() -> None:
    assert round(pw.mde80_points(600, 0.05), 1) == 2.5
    assert round(pw.mde80_points(900, 0.05), 1) == 2.1
    assert round(pw.mde80_points(600, 0.10), 1) == 3.6
    assert round(pw.mde80_points(900, 0.10), 1) == 2.9


def test_simulation_monotone_and_near_the_approximation() -> None:
    lo = pw.simulate_h1(300, 2, 0.02, 0.85, n_sims=300, n_flips=400, seed=1)["0.025"]
    hi = pw.simulate_h1(300, 2, 0.10, 0.85, n_sims=300, n_flips=400, seed=1)["0.025"]
    assert lo < hi and hi > 0.95
    null = pw.simulate_h1(300, 2, 0.05, 0.5, sd_p=0.0, n_sims=400, n_flips=400, seed=2)["0.05"]
    assert null < 0.12


def test_h2_rho1_matches_the_three_valued_approximation_and_averaging_helps() -> None:
    three = pw.simulate_h2(300, 3, 0.02, 0.85, rho=1.0, sd_p=0.0, n_sims=600, n_flips=600, seed=4)["0.025"]
    avg = pw.simulate_h2(300, 3, 0.02, 0.85, rho=0.0, sd_p=0.0, n_sims=600, n_flips=600, seed=4)["0.025"]
    assert abs(three["test_only"] - pw.normal_power(900, 0.02, 0.85, 0.025)) < 0.06
    assert avg["test_only"] > three["test_only"]


def test_h2_gold_gate_costs_power_when_gold_disagrees() -> None:
    r = pw.simulate_h2(300, 3, 0.05, 0.85, agreements=(1.0, 0.0), n_sims=300, n_flips=400, seed=3)["0.025"]
    assert r["confirm_a1"] == pytest.approx(r["test_only"])
    assert r["confirm_a0"] == 0.0 and r["test_only"] > 0.5


def test_compute_refuses_another_layout() -> None:
    with pytest.raises(SystemExit):
        pw.compute({"clusters": 10, "shapes": [[2, 1], [1, 1]]}, None, 10, 10)


def test_dose_scales_q_with_realised_added_papers() -> None:
    papers = [f"P{i}" for i in range(300)]
    vals, per, dose = pw.dose_scale(dict.fromkeys(papers, 5), papers, 2800)
    assert per == pytest.approx(2800 / 300) and dose == pytest.approx(1500 / 2800)
    assert (vals / per).mean() == pytest.approx(dose)   # the average q multiplier is the dose, not 1
    full = pw.compute({"clusters": 300, "shapes": [[2, 1]]}, None, 20, 20)
    half = pw.compute({"clusters": 300, "shapes": [[2, 1]]}, list(vals / per), 20, 20, dose=dose)
    k = "p0.7_q0.05"
    assert half["normal_approximation"]["H2_at_realised_dose"][k]["0.025"] < full["normal_approximation"]["H2"][k]["0.025"]
    assert full["normal_approximation"]["H2_at_realised_dose"][k] == full["normal_approximation"]["H2"][k]


def test_main_passes_the_dose_scaled_q_to_compute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main() wires the realised dose into compute(): mean q multiplier = dose, not 1 (the pre-fix
    n_j / mean n_j survived the unit test above, which never ran main)."""
    papers = [f"P{i}" for i in range(300)]
    monkeypatch.setattr(pw, "layout_from_records",
                        lambda _p: {"source": "t", "clusters": 300, "shapes": [[2, 1]], "records": 900, "papers": list(papers)})
    seen: dict = {}
    real = pw.compute

    def fake_compute(layout, q_scale, n_sims, n_flips, dose=1.0):
        seen.update(q_scale=q_scale, dose=dose)
        return real(layout, q_scale, 2, 2, dose=dose)

    monkeypatch.setattr(pw, "compute", fake_compute)
    counts = tmp_path / "counts.json"
    counts.write_text(json.dumps({p: (10 if i % 2 else 4) for i, p in enumerate(papers)}))   # 2,100 added
    rc = pw.main(["--records", str(tmp_path / "r.jsonl"), "--neighbour-counts", str(counts), "--out",
                  str(tmp_path / "out.json"), "--sims", "2", "--flips", "2"])
    assert rc == 0 and seen["dose"] == pytest.approx(2100 / 2800)
    assert sum(seen["q_scale"]) / 300 == pytest.approx(2100 / 2800)
    assert seen["q_scale"][1] == pytest.approx(10 / (2800 / 300))
    assert json.loads((tmp_path / "out.json").read_text())["h2_dose"] == pytest.approx(0.75)
