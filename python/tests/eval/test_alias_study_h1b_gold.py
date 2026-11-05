"""Alias study, H1b on found@10-gold (a display, DEVIATIONS D-2): the gold-only path differs from
the judged one only in the hit vector, the judged path reproduces the statistics module's H1b
exactly, the cross-check stops on a mismatch, and the public output passes public_problems."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parents[1] / "scripts" / "eval", _HERE.parents[1] / "scripts" / "eval" / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import alias_study_common as asc  # noqa: E402
import alias_study_fixture as fx  # noqa: E402
import alias_study_h1b_gold as hg  # noqa: E402
import alias_study_stats as st  # noqa: E402

AN = {"n_perm": 2000, "n_boot": 500, "perm_seed": 1, "boot_seed": 2}
NONE, MERGED = st.L("base", "none", "default"), st.L("base", "merged", "default")


def idx(rec: dict[str, Any]) -> int:
    return int(rec["paper"][1:])


def world(n: int = 30) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Judged and gold-only hits that disagree: under none every record is a judged hit on a third
    of the papers but a gold hit on none; under merged the G records of the first two thirds of
    papers are gold hits, and a C record is a judged-only hit on every fifth paper."""
    records = fx.make_records(n)
    bal = asc.split_records(records)[0]

    def cell(rec: dict[str, Any], leg: str) -> tuple[int, int]:
        i = idx(rec)
        if leg == NONE:
            return (1 if i % 3 == 0 else 0), 0
        if leg == MERGED:
            if rec["kind"] == "G":
                g = int(i < 2 * n // 3)
                return max(g, int(i % 3 == 0)), g
            return int(i % 5 == 0), 0
        return 1, 1

    out: dict[str, dict[str, Any]] = {}
    for c, g, r, rep in asc.all_legs():
        leg = asc.leg_id(c, g, r, rep)
        out[leg] = {}
        for rec in bal:
            h, hgold = cell(rec, leg)
            out[leg][rec["record_id"]] = {"hit": h, "hit_gold": hgold, "rr": float(h), "rr_gold": float(hgold),
                                          "kind": "gold" if hgold else ("other" if h else None)}
    return records, bal, out


def reach(bal: list[dict[str, Any]]) -> dict[str, int]:
    n_g = sum(r["kind"] == "G" for r in bal)
    return {"count": n_g, "of": n_g}


def swap_to_gold(outcomes: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The same outcomes with ``hit`` replaced by ``hit_gold`` and nothing else changed."""
    return {leg: {rid: (None if o is None else {**o, "hit": o["hit_gold"]}) for rid, o in rows.items()}
            for leg, rows in outcomes.items()}


def test_gold_path_differs_from_judged_only_in_the_hit_vector() -> None:
    _, bal, o = world()
    gold = hg.h1b_member(bal, o, AN, "hit_gold")
    judged = hg.h1b_member(bal, o, AN, "hit")
    assert gold["net_points"] != judged["net_points"]           # the fixture does separate them
    # the gold-only figure is exactly the judged path run on outcomes whose hit IS the gold hit
    assert gold == hg.h1b_member(bal, swap_to_gold(o), AN, "hit")
    # and equals the module's own H1b on those outcomes, field by field
    l1 = st.layer1(bal, swap_to_gold(o), {}, reach(bal), AN)["H1b"]
    assert all(l1[k] == gold[k] for k in hg.MEMBER_FIELDS if k in l1)


def test_judged_crosscheck_reproduces_the_module_h1b_exactly() -> None:
    _, bal, o = world()
    comp = hg.compute(bal, o, {}, reach(bal), AN)
    l1 = st.layer1(bal, o, {}, reach(bal), AN)
    assert comp["wrapper_equals_layer1"] is True and comp["wrapper_vs_layer1_differs"] == []
    assert l1["H1b"]["tested"] is True and "p" in l1["H1b"]          # the comparison covers the p
    for k in hg.MEMBER_FIELDS:
        assert comp["judged"][k] == l1["H1b"][k], k
    assert comp["h1_gold_only_net_points"] == l1["H1"]["gold_only_net_points"]
    assert comp["h2_gold_only_net_points"] == l1["H2"]["gold_only_net_points"]
    ref = {"layer1": copy.deepcopy(l1)}
    assert hg.crosscheck(comp, ref)["agree"] is True


def test_crosscheck_stops_on_any_mismatch() -> None:
    _, bal, o = world()
    comp = hg.compute(bal, o, {}, reach(bal), AN)
    l1 = st.layer1(bal, o, {}, reach(bal), AN)
    for path in (("H1b", "net_points"), ("H1b", "p"), ("H1", "gold_only_net_points")):
        ref = {"layer1": copy.deepcopy(l1)}
        ref["layer1"][path[0]][path[1]] += 1e-12
        c = hg.crosscheck(comp, ref)
        assert c["agree"] is False and f"{path[0]}.{path[1]}" in c["mismatches"]
    ref = {"layer1": copy.deepcopy(l1), "inputs": {"records": "a"}}
    assert hg.crosscheck(comp, ref, {"records": "b"})["mismatches"] == ["inputs"]


def test_public_output_passes_public_problems() -> None:
    records, bal, o = world()
    comp = hg.compute(bal, o, {}, reach(bal), AN)
    check = hg.crosscheck(comp, {"layer1": st.layer1(bal, o, {}, reach(bal), AN)})
    res = hg.result(comp, check, AN)
    res["provenance"] = {"host": "somewhere", "warnings": ["x"], "commit": "0" * 40, "dirty": False}
    final_keys = {(r["record_id"], r["gold_chunks"][0]) for r in records}
    pub, bad = hg.public_output(res, records, final_keys)
    assert bad == []
    assert pub["label"] == hg.LABEL and "host" not in pub["provenance"]
    assert pub["h1b_gold"]["key"] == "hit_gold"
    text = json.dumps(pub)
    assert not any(r["record_id"] in text for r in records)
    # the check is live: a record id smuggled in is refused
    leaked = {**res, "extra": records[0]["record_id"]}
    assert hg.public_output(leaked, records, final_keys)[1]


def test_analysis_params_refuse_other_counts_outside_a_dry_run() -> None:
    with pytest.raises(SystemExit):
        hg.analysis_params({"n_perm": 100})
    assert hg.analysis_params({"n_perm": 100, "n_boot": 50, "dry_run": True})["n_perm"] == 100
    an = hg.analysis_params({})
    assert an["perm_seed"] == asc.seed_int(asc.SEEDS["perm"]) and an["boot_seed"] == asc.seed_int(asc.SEEDS["boot"])
