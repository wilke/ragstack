"""Alias study pilot report (prereg §5.6): on the dry run's real outputs relabelled as a pilot
directory, the estimates, their reproducibility, and every refusal."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parents[1] / "scripts" / "eval", _HERE.parents[1] / "scripts" / "eval" / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import alias_study_common as asc  # noqa: E402
import alias_study_dryrun as dry  # noqa: E402
import alias_study_fixture as fx  # noqa: E402
import alias_study_judge as aj  # noqa: E402
import alias_study_pilot as pilot  # noqa: E402
import alias_study_run as ar  # noqa: E402

PROV = {"commit": "f" * 40, "dirty": False, "untracked": 0}
N_BOOT = 300


# ------------------------------------------------------------------ the fixture

def rehash_commit(work: Path, mode: str = "pilot") -> None:
    """Re-seal the dry run's stage-A commit over the relabelled files: every exact entry's sha256
    and length recomputed, the commit's mode set, and the marker and the public file pointed at
    the new commit sha256, so :func:`alias_study_judge.verify_marker` runs on real outputs."""
    runs = work / "runs"
    mp = runs / ar.COMMIT_MARKER
    marker = json.loads(mp.read_text())
    cpath = Path(marker["commit"])
    commit = json.loads(cpath.read_text())
    commit["mode"] = mode
    for f in commit["files"]:
        if f["mode"] == "exact":
            f["sha256"], f["bytes"] = asc.sha256_file(f["path"]), Path(f["path"]).stat().st_size
    cpath.write_text(json.dumps(commit))
    csha = asc.sha256_file(cpath)
    pp = Path(marker["public"])
    pub = json.loads(pp.read_text())
    pub.update(commit_sha256=csha, mode=mode)
    pub["files"] = {f["name"]: {"sha256": f["sha256"], "bytes": f["bytes"], "mode": f["mode"]} for f in commit["files"]}
    pp.write_text(json.dumps(pub))
    marker["commit_sha256"] = csha
    mp.write_text(json.dumps(marker))


def to_pilot(work: Path) -> None:
    """The dry run's outputs as a pilot directory: the STUDY-MODE markers, ``study.mode`` of
    every leg summary, the registry manifest's and judge summary's mode, then the commit
    re-sealed (:func:`rehash_commit`). Rows, labels and registry are the harness's own."""
    runs, judge = work / "runs", work / "judge"
    for d in (runs, judge):
        (d / ar.MODE_MARKER).write_text("pilot\n")
    for sp in runs.glob("*/*.summary.json"):
        s = json.loads(sp.read_text())
        s["study"].update(mode="pilot", dry_run=False, pilot=True)
        sp.write_text(json.dumps(s))
    for name in ("pairs-manifest.json", "judge-summary.json"):
        m = json.loads((judge / name).read_text())
        m["mode"] = "pilot"
        (judge / name).write_text(json.dumps(m))
    rehash_commit(work)


def full_records(run_records: list[dict[str, Any]], extra_satisfied: int = 3) -> list[dict[str, Any]]:
    """The accepted file the pilot reads: the run records (all on unsatisfied papers) plus
    satisfied papers that the pilot never runs (the balanced set)."""
    out = list(run_records)
    for r in fx.make_records(n_papers=extra_satisfied, set_="B"):
        if r["paper"].startswith("P"):
            out.append({**r, "paper": "B" + r["paper"], "record_id": r["record_id"].replace("-P", "-B")})
    return out


def pin_record_set(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """The fixture's file stands in for the frozen record set (§4.1)."""
    recs = asc.load_records(path)
    bal, _ = asc.split_records(recs)
    monkeypatch.setattr(asc, "ACCEPTED_SHA256", asc.sha256_file(path))
    monkeypatch.setattr(asc, "BALANCED_SHA256", asc.balanced_ids_sha(r["record_id"] for r in bal))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("pilot")
    (root / "in").mkdir()
    w = fx.World(root / "in", set_="V")
    run_recs = [r for r in w.records if not r["record_id"].endswith("-g1")]   # 1 G + 1 C per paper: unsatisfied
    fx.write_jsonl(w.records_path, run_recs)
    assert not asc.split_records(run_recs)[0]
    dry.dry_run(w.records_path, w.base_export, w.glossary, root / "work", added_per_arm=3, batch_size=60,
                n_perm=200, n_boot=100)
    to_pilot(root / "work")
    fx.write_jsonl(root / "accepted-full.jsonl", full_records(run_recs))
    return root


@pytest.fixture()
def world(built: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private copy of the built pilot world (absolute paths in the commit rewritten)."""
    dst = tmp_path / "w"
    shutil.copytree(built, dst)
    for p in [*dst.rglob("*.json"), dst / "work" / "runs" / ar.COMMIT_MARKER]:
        t = p.read_text()
        if str(built) in t:
            p.write_text(t.replace(str(built), str(dst)))
    rehash_commit(dst / "work")
    pin_record_set(monkeypatch, dst / "accepted-full.jsonl")
    return dst


def run(w: Path, **kw: Any) -> dict[str, Any]:
    return pilot.report(w / "accepted-full.jsonl", w / "work" / "runs", w / "work" / "judge", n_boot=N_BOOT,
                        prov=PROV, **kw)


# ------------------------------------------------------------------ the estimates

def test_report_estimates_match_the_frozen_hits(world: Path) -> None:
    res = run(world)
    assert res["mode"] == "pilot" and res["spot_checks_waiver"].startswith("pilot")
    assert res["records"] == {"pilot": 26, "G": 13, "C": 13, "papers": 13}
    assert res["record_set"]["balanced"] == 9 and res["record_set"]["descriptive"] == 26
    # discordance recomputed from the judge's own frozen stage-A hits
    hits: dict[tuple[str, str], int] = {}
    gold: dict[tuple[str, str], int] = {}
    for x in asc.read_jsonl(world / "work" / "judge" / "stage-a" / "hits.jsonl"):
        hits[(x["leg"], x["record_id"])] = x["hit"]
        gold[(x["leg"], x["record_id"])] = x["hit_gold"]
    recs = asc.load_records(world / "accepted-full.jsonl")
    desc = asc.split_records(recs)[1]

    def disc(kind: str | None, a: str, bs: tuple[str, ...], how: Any) -> int:
        return sum(how(hits[(a, r["record_id"])] != hits[(b, r["record_id"])] for b in bs)
                   for r in desc if kind is None or r["kind"] == kind)
    d = res["discordance"]
    # the prereg's cells (§3.1), spelled out here rather than read from the module
    merged, none = "base|merged|default", "base|none|default"
    nb, draws = "neighbour|none|default", ("random1|none|default", "random2|none|default")
    assert d["glossary_G"]["cells"] == [merged, none] and d["growth"]["cells"] == [nb, *draws]
    assert d["glossary_G"]["discordant"] == disc("G", merged, (none,), any)
    assert d["glossary_C"]["discordant"] == disc("C", merged, (none,), any)
    assert d["growth"]["discordant"] == disc(None, nb, draws, any)
    assert d["growth_both_draws"]["discordant"] == disc(None, nb, draws, all)
    assert d["growth_vs_random2"]["discordant"] == disc(None, nb, draws[1:], any)
    assert d["glossary_G"]["n"] == 13 and d["growth"]["n"] == 26 and d["growth"]["papers"] == 13
    for k in ("glossary_G", "glossary_C", "growth"):
        lo, hi = d[k]["ci95"]
        assert 0 <= lo <= d[k]["share"] <= hi <= 1
    c = res["baseline_c_found10"]["found10"]
    assert res["baseline_c_found10"]["found10"]["leg"] == none
    assert c["found"] == sum(hits[(none, r["record_id"])] for r in desc if r["kind"] == "C")
    assert c["n"] == 13 and c["ci95_points"][0] <= c["points"] <= c["ci95_points"][1]
    cg = res["baseline_c_found10"]["found10_gold"]
    assert cg["leg"] == none and cg["found"] == sum(gold[(none, r["record_id"])] for r in desc if r["kind"] == "C")
    # the fixture must tell found@10 from found@10-gold, or the line above proves nothing
    assert cg["found"] != c["found"]
    # every interval is the paper bootstrap seeded by 261101|alias|boot, recomputed independently
    seed = int.from_bytes(hashlib.sha256(b"261101|alias|boot").digest()[:8], "big")
    assert res["intervals"]["numpy_seed"] == seed      # the seed the intervals were drawn with
    c_recs = [r for r in desc if r["kind"] == "C"]
    assert c["ci95_points"] == indep_boot([r["paper"] for r in c_recs],
                                          [hits[(none, r["record_id"])] for r in c_recs], seed, 100.0)
    assert cg["ci95_points"] == indep_boot([r["paper"] for r in c_recs],
                                           [gold[(none, r["record_id"])] for r in c_recs], seed, 100.0)
    g_recs = [r for r in desc if r["kind"] == "G"]
    assert d["glossary_G"]["ci95"] == indep_boot(
        [r["paper"] for r in g_recs], [int(hits[(merged, r["record_id"])] != hits[(none, r["record_id"])])
                                       for r in g_recs], seed)
    # judge shares recomputed from final.jsonl
    fin = [x for x in asc.read_jsonl(world / "work" / "judge" / "final.jsonl") if x["k"].startswith("pair|")]
    j = res["judge"]
    assert j["pairs_finalized"] == len(fin)
    assert j["answers"]["overall"]["k"] == sum(x["label"] == 1 for x in fin)
    assert j["tiebreaker"]["escalated"]["overall"]["k"] == sum(x["source"] != "agree" for x in fin)
    assert j["tiebreaker"]["tiebroken"]["overall"]["k"] == sum(x["source"] in ("tiebreak", "tiebreak_r2") for x in fin)
    assert j["tiebreaker"]["unjudged"]["overall"]["k"] == sum(x["source"] == "unjudged" for x in fin)
    assert j["opus_filtered"]["overall"]["k"] == sum(x["opus_status"] == "no_reference" for x in fin)
    assert j["opus_filtered"]["overall"]["n"] == sum(bool(x["drift_sampled"]) for x in fin)
    assert sum(j["answers"][a]["n"] for a in asc.ARM_GROUPS) == len(fin)
    # the registry: deduplicated, in total and per judging stage, never per leg
    reg = asc.read_jsonl(world / "work" / "judge" / "pairs.jsonl")
    assert res["judge_registry"] == {"pairs": len(reg), "per_judging_stage": {
        "A": sum(x["stage"] == "A" for x in reg), "B": sum(x["stage"] == "B" for x in reg)}}
    def calls(tag: str) -> int:
        return max(len(asc.read_jsonl(world / "work" / "judge" / f"{k}-gw-{tag}.jsonl")) for k in ("calls", "dispatch"))
    assert res["gateway_calls"]["argon"]["calls"] == calls("argon") > 0
    assert res["gateway_calls"]["opus"]["calls"] == calls("opus") > 0
    assert set(res["inputs"]) >= {"records", "judge/final.jsonl", "runs/base/none.jsonl"}
    assert res["citable"] is True and res["intervals"]["seed"] == "261101|alias|boot"


def test_report_is_seeded_and_carries_no_test_quantity(world: Path, tmp_path: Path) -> None:
    a, b = run(world), run(world)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    pilot.no_test_quantities(a)
    text = json.dumps(a)
    for k in ("p_value", "net_points", "gained", "confirmed", "GATE"):
        assert k not in text
    md = pilot.markdown(a)
    assert "glossary_G" in md and "| base |" in md
    out = tmp_path / "pilot.json"
    assert pilot.main(["--records", str(world / "accepted-full.jsonl"), "--runs-dir", str(world / "work" / "runs"),
                       "--judge-dir", str(world / "work" / "judge"), "--out", str(out), "--md",
                       str(tmp_path / "p.md"), "--n-boot", "50"]) == 0
    assert json.loads(out.read_text())["schema"] == pilot.SCHEMA and (tmp_path / "p.md").is_file()


def indep_boot(clusters: list[str], flags: list[int], seed: int, scale: float = 1.0) -> list[float]:
    """A paper bootstrap written here from scratch (not chunktrial_stats)."""
    papers = list(dict.fromkeys(clusters))
    s = np.array([sum(f for c, f in zip(clusters, flags, strict=True) if c == q) for q in papers], float)
    n = np.array([sum(c == q for c in clusters) for q in papers], float)
    idx = np.random.default_rng(seed).integers(0, len(papers), size=(N_BOOT, len(papers)))
    return [round(scale * float(x), 4) for x in np.percentile(s[idx].sum(1) / n[idx].sum(1), [2.5, 97.5])]


def test_judge_shares_definitions() -> None:
    """The three shares on rows that tell the definitions apart (each source and Opus status)."""
    rows = [{"arm": "base", "source": "agree", "label": 1, "drift_sampled": True, "opus_status": "answered"},
            {"arm": "base", "source": "tiebreak", "label": 0, "drift_sampled": True, "opus_status": "no_reference"},
            {"arm": "neighbour", "source": "tiebreak_r2", "label": 1, "drift_sampled": True, "opus_status": None},
            {"arm": "random", "source": "unjudged", "label": None, "drift_sampled": False, "opus_status": None},
            {"arm": "random", "source": "agree", "label": 0, "drift_sampled": True, "opus_status": "answered"}]
    j = pilot.judge_shares({str(i): r for i, r in enumerate(rows)})
    assert j["answers"]["overall"]["k"] == 2 and j["answers"]["neighbour"] == {
        "k": 1, "n": 1, "share": 1.0, "wilson95": j["answers"]["neighbour"]["wilson95"]}
    t = j["tiebreaker"]
    assert (t["escalated"]["overall"]["k"], t["tiebroken"]["overall"]["k"], t["unjudged"]["overall"]["k"]) == (3, 2, 1)
    assert t["tiebroken"]["random"]["k"] == 0 and t["escalated"]["random"]["k"] == 1
    assert j["opus_filtered"]["overall"] == {**j["opus_filtered"]["overall"], "k": 1, "n": 4}
    assert j["opus_filtered"]["base"]["k"] == 1 and j["opus_filtered"]["neighbour"]["k"] == 0


def perturb_stage_b_leg(w: Path) -> int:
    """Move the gold out of, or up inside, the top 10 of a stage-B leg's rows (not pinned by the
    stage-A commit): that leg's found@10-gold, found@10 demand and RR@10 demand all change."""
    rp = w / "work" / "runs" / "neighbour" / "merged.jsonl"
    recs = {r["record_id"]: r for r in asc.load_records(w / "accepted-full.jsonl")}
    rows = asc.read_jsonl(rp)
    moved = 0
    for x in rows:
        top = x.get("ranked_chunk_ids") or []
        g = recs[x["record_id"]]["gold_chunks"][0]
        if g in top[:10]:
            top.remove(g)
            top.insert(30 if moved % 2 else 0, g)
            moved += 1
    rp.write_text("".join(json.dumps(x) + "\n" for x in rows))
    return moved


def test_no_per_leg_outcome_can_be_backed_out(world: Path) -> None:
    """The reviewer's probe, generalised: change one leg's outcomes (found@10-gold, the pairs it
    demands for found@10 and RR@10) and the report must not move, except that rows file's sha256.
    Before the owner's 2026-11-03 decision the per-leg pair counts moved with it (10 x the
    gold-only misses of the leg)."""
    a = run(world)
    assert perturb_stage_b_leg(world) > 0
    b = run(world)
    assert a["inputs"].pop("runs/neighbour/merged.jsonl") != b["inputs"].pop("runs/neighbour/merged.jsonl")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    for leg, v in a["legs"].items():
        assert set(v) <= set(pilot.LEG_FIELDS), leg

    def keys(o: Any) -> set[str]:
        if isinstance(o, dict):
            return set(o) | {k for v in o.values() for k in keys(v)}
        return {k for v in o for k in keys(v)} if isinstance(o, list) else set()
    assert not keys(a) & {"per_leg", "found10_per_leg", "found10_gold_per_leg", "pairs_per_leg", "rr10"}


def test_discordance_is_unsigned_and_excludes_missing() -> None:
    recs = [{"record_id": f"r{i}", "paper": f"p{i // 2}"} for i in range(6)]
    o = {"A": {f"r{i}": {"hit": h} for i, h in enumerate([1, 0, 1, 1, 0, 0])},
         "B": {f"r{i}": {"hit": h} for i, h in enumerate([0, 1, 1, 1, 0, 0])},
         "C": {f"r{i}": {"hit": h} for i, h in enumerate([0, 0, 1, 0, 0, 1])}}
    o["B"]["r5"] = None
    x = pilot.discordance(recs, o, "A", ["B"], 200, 7)
    assert (x["n"], x["discordant"], x["missing"]) == (5, 2, 1)
    assert pilot.discordance(recs, o, "B", ["A"], 200, 7) == {**x, "cells": ["B", "A"]}   # no direction
    assert pilot.discordance(recs, o, "A", ["B", "C"], 200, 7, "any")["discordant"] == 3
    assert pilot.discordance(recs, o, "A", ["B", "C"], 200, 7, "all")["discordant"] == 1
    assert x["ci95"] == pilot.discordance(recs, o, "A", ["B"], 200, 7)["ci95"]


# ------------------------------------------------------------------ refusals

def test_refuses_a_dry_run_directory(built: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "d"
    (root / "in").mkdir(parents=True)
    w = fx.World(root / "in", set_="V")
    recs = [r for r in w.records if not r["record_id"].endswith("-g1")]
    fx.write_jsonl(w.records_path, recs)
    dry.dry_run(w.records_path, w.base_export, w.glossary, root / "work", added_per_arm=3, batch_size=60,
                n_perm=100, n_boot=50)
    pin_record_set(monkeypatch, w.records_path)
    with pytest.raises(SystemExit, match="STUDY-MODE is 'dry_run'"):
        pilot.report(w.records_path, root / "work" / "runs", root / "work" / "judge", n_boot=50, prov=PROV)


@pytest.mark.parametrize("which", ["runs", "judge"])
def test_refuses_a_marker_that_is_not_pilot(world: Path, which: str) -> None:
    (world / "work" / which / ar.MODE_MARKER).write_text("main\n")
    with pytest.raises(SystemExit, match=f"{which}_dir/STUDY-MODE is 'main'"):
        run(world)


def test_refuses_a_commit_made_in_another_mode(world: Path) -> None:
    rehash_commit(world / "work", mode="dry_run")
    with pytest.raises(SystemExit, match="committed in mode 'dry_run'"):
        run(world)


def test_refuses_a_commit_that_no_longer_verifies(world: Path) -> None:
    p = world / "work" / "runs" / "base" / "none.jsonl"
    p.write_text(p.read_text() + "\n")
    with pytest.raises(SystemExit, match="stage-A commit"):
        run(world)


def test_refuses_another_judge_directory(world: Path) -> None:
    other = world / "work" / "judge-copy"
    shutil.copytree(world / "work" / "judge", other)
    with pytest.raises(SystemExit, match="not the judge directory stage A was committed from"):
        pilot.report(world / "accepted-full.jsonl", world / "work" / "runs", other, n_boot=N_BOOT, prov=PROV)


def test_refuses_a_registry_of_another_mode(world: Path) -> None:
    p = world / "work" / "judge" / "pairs-manifest.json"
    m = json.loads(p.read_text())
    m["mode"] = "main"
    p.write_text(json.dumps(m))
    with pytest.raises(SystemExit, match="registry was built in mode 'main'"):
        run(world)


def test_refuses_a_leg_measured_in_another_mode(world: Path) -> None:
    p = world / "work" / "runs" / "neighbour" / "merged.summary.json"   # stage B: not pinned by the commit
    s = json.loads(p.read_text())
    s["study"]["mode"] = "main"
    p.write_text(json.dumps(s))
    with pytest.raises(SystemExit, match="measured in mode 'main'"):
        run(world)


def test_refuses_a_record_file_that_is_not_the_frozen_one(world: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asc, "ACCEPTED_SHA256", "0" * 64)
    with pytest.raises(SystemExit, match="record set is not the frozen one"):
        run(world)


def test_refuses_a_balanced_record_in_the_pilot(world: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recs = asc.load_records(world / "accepted-full.jsonl")
    g0 = next(r for r in recs if r["paper"] == "P00" and r["kind"] == "G")
    recs.append({**g0, "record_id": g0["record_id"] + "x", "alias": "ZZ9", "primary": "zeta zeta"})
    fx.write_jsonl(world / "accepted-full.jsonl", recs)       # P00 is now satisfied: its run rows are balanced
    pin_record_set(monkeypatch, world / "accepted-full.jsonl")
    with pytest.raises(SystemExit, match="balanced-set record"):
        run(world)


def test_refuses_an_unlabelled_contrast_pair(world: Path) -> None:
    p = pilot.Pilot(world / "accepted-full.jsonl", world / "work" / "runs", world / "work" / "judge")
    leg = pilot.GLOSSARY_CELLS[0]
    row = next(r for r in p.rows[leg] if aj.demanded(p.by_id[r["record_id"]], r, "found10"))
    cid = aj.demanded(p.by_id[row["record_id"]], row, "found10")[0]
    del p.label_of[(row["record_id"], cid)]
    with pytest.raises(SystemExit, match="have no final label"):
        p.outcomes(pilot.CONTRAST_LEGS)


def test_refuses_a_test_quantity_in_the_output(world: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit, match="no Layer-1 test quantity"):
        pilot.no_test_quantities({"a": [{"b": {"p_value": 0.01}}]})
    for bad in ({"netchange": 3}, {"p_one": 0.01}, {"H1": 1}, {"x": {"y": "H2 confirmed"}}, {"z": ["significant"]},
                {"w": "GATE-NOT-EVALUABLE: leg not run"}):
        with pytest.raises(SystemExit, match="no Layer-1 test quantity"):
            pilot.no_test_quantities(bad)
    real = pilot.found_figure
    monkeypatch.setattr(pilot, "found_figure", lambda *a, **k: {**real(*a, **k), "net_points": 1.0})
    with pytest.raises(SystemExit, match="net_points"):
        run(world)


def test_refuses_a_private_string_in_the_output(world: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = pilot.judge_shares
    monkeypatch.setattr(pilot, "judge_shares", lambda final: {**real(final),
                                                              "x": next(iter(final.values()))["record_id"]})
    with pytest.raises(SystemExit, match="private string"):
        run(world)
