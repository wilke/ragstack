"""Alias study power: the normal approximation reproduces the prereg's §8 tables, the MDEs, and
the simulation behaves (more discordance, more power; the gold-only gate costs power when the
gold direction disagrees)."""
from __future__ import annotations

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


def test_caps_lever_reads_confirm_power_at_the_configured_agreement(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lever reads confirm_a<a>, not test_only; without an a it is undecided (None)."""
    fake = {"0.025": {"test_only": 0.90, "confirm_a1": 0.90, "confirm_a0.8": 0.85, "confirm_a0.6": 0.70,
                      "confirm_a0.7": 0.79}, "0.05": {}}
    monkeypatch.setattr(pw, "simulate_h1", lambda *a, **k: {"0.025": 1.0, "0.05": 1.0})
    seen: list[tuple[float, ...]] = []

    def h2(*a: object, agreements: tuple[float, ...] = pw.AGREEMENTS, **k: object) -> dict[str, object]:
        seen.append(tuple(agreements))
        return fake
    monkeypatch.setattr(pw, "simulate_h2", h2)
    lay = {"clusters": 300, "shapes": [[2, 1]]}
    undecided = pw.compute(lay, None, 10, 10)
    assert undecided["h2_caps_lever_fires"] is None
    assert undecided["caps_lever_by_agreement"] == {"a1": False, "a0.8": False, "a0.6": True}
    assert pw.compute(lay, None, 10, 10, lever_agreement=0.8)["h2_caps_lever_fires"] is False
    assert pw.compute(lay, None, 10, 10, lever_agreement=0.6)["h2_caps_lever_fires"] is True   # test_only .90 would not fire
    assert pw.compute(lay, None, 10, 10, lever_agreement=0.7)["h2_caps_lever_fires"] is True   # an a outside the grid is simulated
    assert seen[-1] == (1.0, 0.8, 0.6, 0.7)
    with pytest.raises(SystemExit):
        pw.compute(lay, None, 10, 10, lever_agreement=1.5)
