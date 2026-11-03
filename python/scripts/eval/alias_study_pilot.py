#!/usr/bin/env python
"""Alias study (OA-1000): the pre-freeze pilot report (DRAFT prereg v3 §5.6, R8).

    alias_study_pilot.py --records ACCEPTED.jsonl --runs-dir RUNS --judge-dir JUDGE \\
                         --out pilot.json [--md pilot.md] [--n-boot 10000]

Reads one **pilot** directory pair (the runner's ``runs_dir`` and the judge's ``out_dir``, both
written with ``pilot: true``) and writes what §5.6 says the pilot estimates, each with its n and
a 95 % interval, as counts that can be committed before the freeze:

* **discordance** between the two cells of each Layer-1 contrast: the glossary contrast (base x
  default, merged vs none) on the G records and on the C records **separately**, and the growth
  contrast (default x none, neighbour vs random-1 / random-2) on all pilot records, against each
  draw, against either (the record's H2 value is non-zero) and against both. A record is
  discordant when its found@10 differs between the cells; nothing else is kept. 95 % interval:
  the paper-cluster percentile bootstrap (seed ``261101|alias|boot``, §5.5);
* the share of judged pairs labelled *answers*, per arm (base / neighbour / random, §6.2);
* the **tiebreaker share**: pairs C and E did not settle (sent to the tiebreaker), the pairs
  whose final label is the tiebreaker's, and the unjudged, per arm;
* the **Opus-filtered** (no-reference) share of the drift sample, per arm (§6.3);
* the **deduplicated pair count** of the judge's registry, in total and per judging stage, and
  the gateway calls made so far (:func:`alias_study_judge.gateway_calls_so_far`), for the judge
  caps (§14 O7).

§5.6's baseline C found@10 is **not** reported, with or without found@10-gold (owner,
2026-11-03, R28; *Blind to outcomes*).

Pair-level shares carry a Wilson 95 % interval (pairs, not papers, are the unit there).

**What it never does.** It computes no Layer-1 test, no net change, no direction, no gate and
no p value: a contrast is read only as *discordant or not* per record (:func:`discordance`), and
the output is refused when any key names a test quantity (:data:`FORBIDDEN_KEYS`). The pilot's
records are not a sample of the balanced set (§5.6 *Its limits*); its estimates are added to §8
as rows, never substituted.

**Blind to outcomes (owner, 2026-11-03, R28).** No figure is printed from which a leg's
found@10, found@10-gold or RR@10 level, or the direction of any contrast, could be read. The
report prints no single-leg level at all: a level beside a contrast's discordant count would
bound the direction of that contrast. Its only outcome-bearing figures are the contrasts'
unsigned discordant counts, which are unchanged when a record's found@10 flips in every cell at
once. The registry counts (deduplicated across legs, per judging stage and in total) and the
per-arm judge shares pool every leg; per leg the report holds only the run status, the row count
and the failed calls.

**Refusals (before any outcome is read).** Both directories must carry ``STUDY-MODE`` ``pilot``;
:func:`alias_study_judge.analysis_gate` must pass and say ``pilot`` (stage A committed and the
commit still verifying; the spot checks are waived for the pilot and the waiver is recorded);
``--judge-dir`` must be the directory the commit was made from; the registry manifest and every
leg summary must say ``pilot``; the record file must be the frozen one
(:func:`alias_study_common.verify_record_set`); and every leg row, registry pair and final label
must name a pilot record (one on an unsatisfied paper, §4.1): a balanced-set record is refused
(§5.6: no balanced-set record is retrieved or judged by the pilot). A found@10 pair a contrast
cell demands without a final label is refused (it would count as *not answering* silently).

**Output.** ``--out`` (JSON): counts only, the input sha256s (by relative name), the stage-A
commit sha256, the seed and its rule, and ``experiment_provenance``; refused when it would
carry a record, paper or chunk id or a question (:func:`alias_study_common.public_problems`).
``--md``: the same figures as a short table.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402
import alias_study_judge as aj  # noqa: E402
import alias_study_run as ar  # noqa: E402
import alias_study_stats as ast  # noqa: E402
import chunktrial_stats as cs  # noqa: E402
import glossbench_alias_judge as jd  # noqa: E402

SCHEMA = "alias-study-pilot/1"
MODE = "pilot"
N_BOOT = 10_000
L = asc.leg_id
#: §3.1: the cells of each Layer-1 contrast
GLOSSARY_CELLS = (L("base", "merged", "default"), L("base", "none", "default"))
GROWTH_NEIGHBOUR = L("neighbour", "none", "default")
GROWTH_DRAWS = (L("random1", "none", "default"), L("random2", "none", "default"))
CONTRAST_LEGS = (*GLOSSARY_CELLS, GROWTH_NEIGHBOUR, *GROWTH_DRAWS)
#: a key that would carry a test quantity, a direction or a verdict: refused anywhere in the output
FORBIDDEN_KEYS = frozenset({"p", "p_value", "pvalue", "p_one_sided", "p_two_sided", "min_p", "statistic",
                            "net", "net_points", "net_exact", "gained", "lost", "direction", "delta", "verdict",
                            "confirmed", "test", "tests", "layer1", "family_g", "family_n", "c_gate"})
#: a key named for a Layer-1 member, and string values naming a verdict: refused anywhere in the output
FORBIDDEN_MEMBERS = frozenset({"H1", "H1b", "H2", "H3"})
FORBIDDEN_VALUES = ("confirmed", asc.GATE.casefold(), "significant")
#: provenance keys a committed file drops (the statistics' public copy: the host and the warnings)
PROVENANCE_DROP = ast.PUBLIC_DROP_PROVENANCE
#: per leg, only these are reported (module docstring, *Blind to outcomes*)
LEG_FIELDS = ("run_status", "stages", "rows", "failed")


# ------------------------------------------------------------------ before anything is read

def preflight(runs_dir: Path, judge_dir: Path) -> dict[str, Any]:
    """The pilot's directories, and nothing else (module docstring, *Refusals*). Raises
    SystemExit; returns the analysis gate's record."""
    for name, d in (("runs_dir", runs_dir), ("judge_dir", judge_dir)):
        m = d / ar.MODE_MARKER
        have = m.read_text(encoding="utf-8").strip() if m.is_file() else None
        if have != MODE:
            raise SystemExit(f"{name}/{ar.MODE_MARKER} is {have!r}: the pilot report reads a pilot directory only "
                             "(§5.6); a main or dry-run directory is refused")
    gate = aj.analysis_gate(runs_dir)
    if gate.get("mode") != MODE:
        raise SystemExit(f"stage A was committed in mode {gate.get('mode')!r}, not {MODE!r}")
    marker = ar.read_commit_marker(runs_dir) or {}
    cpath = Path(marker["commit"]).resolve()
    commit = json.loads(cpath.read_text(encoding="utf-8"))
    want = judge_dir.resolve()
    where = {"commit file": cpath.parent}
    for f in commit.get("files") or []:
        if f["name"] in ("final", "pairs"):
            where[f["name"]] = Path(f["path"]).resolve().parent
    bad = sorted(k for k, have in where.items() if have != want)
    if bad or not {"final", "pairs"} <= set(where):
        raise SystemExit(f"judge_dir is not the judge directory stage A was committed from ({bad or 'no files'})")
    man = json.loads((judge_dir / "pairs-manifest.json").read_text(encoding="utf-8"))
    if man.get("mode") != MODE:
        raise SystemExit(f"the pair registry was built in mode {man.get('mode')!r}, not {MODE!r}")
    return gate


# ------------------------------------------------------------------ reading

class Pilot:
    """The pilot's records, legs and labels, read after :func:`preflight` has passed."""

    def __init__(self, records: Path, runs_dir: Path, judge_dir: Path) -> None:
        import found10 as f10

        self.gate = preflight(runs_dir, judge_dir)
        self.records_path, self.runs_dir, self.judge_dir = records, runs_dir, judge_dir
        self.all_records = asc.load_records(records)
        self.identity = asc.verify_record_set(records, self.all_records)
        bal, desc = asc.split_records(self.all_records)
        self.balanced_ids = {r["record_id"] for r in bal}
        self.recs = desc
        self.by_id = {r["record_id"]: r for r in desc}
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.legs: dict[str, dict[str, Any]] = {}
        self.inputs: dict[str, str] = {"records": asc.sha256_file(records)}
        self.utc: list[str] = []
        for c, g, r, rep in asc.all_legs():
            leg = L(c, g, r, rep)
            rp, sp = ar.leg_files(runs_dir, c, g, r, rep)
            st = ar.leg_stages(c, g, r, rep)
            if not sp.is_file():
                self.legs[leg] = {"run_status": "missing", "stages": st}
                self.rows[leg] = []
                continue
            s = json.loads(sp.read_text(encoding="utf-8"))
            mode = (s.get("study") or {}).get("mode")
            if mode != MODE:
                raise SystemExit(f"leg {leg} was measured in mode {mode!r}, not {MODE!r} (§5.6)")
            cond = asc.RETRIEVERS[r]
            self.inputs[f"runs/{c}/{sp.name}"] = asc.sha256_file(sp)
            if s.get("utc"):
                self.utc.append(str(s["utc"]))
            rows = [x for x in f10.read_jsonl_repair_tail(rp) if x["condition"] == cond] if rp.is_file() else []
            if rp.is_file():
                self.inputs[f"runs/{c}/{rp.name}"] = asc.sha256_file(rp)
            self._check_ids((x["record_id"] for x in rows), f"leg {leg}")
            self.rows[leg] = rows
            self.legs[leg] = {"run_status": s["status"] if cond in (s.get("conditions") or {}) else "missing",
                              "stages": st, "rows": len(rows), "failed": sum(bool(x.get("failed")) for x in rows)}
        jdir = judge_dir
        for name in ("final.jsonl", "pairs.jsonl", "judge-summary.json", "pairs-manifest.json"):
            if (jdir / name).is_file():
                self.inputs[f"judge/{name}"] = asc.sha256_file(jdir / name)
        self.final = {x["pair_id"]: x for x in asc.read_jsonl(jdir / "final.jsonl")
                      if str(x.get("k", "")).startswith("pair|")}
        self.registry = asc.read_jsonl(jdir / "pairs.jsonl")
        self._check_ids((x["record_id"] for x in self.final.values()), "final.jsonl")
        self._check_ids((x["record_id"] for x in self.registry), "pairs.jsonl")
        self.pair_paper = {(p["record_id"], p["chunk_id"]): p["c_paper"] for p in self.registry}
        self.label_of = {(x["record_id"], x["chunk_id"]): x.get("label") for x in self.final.values()}

    def _check_ids(self, ids: Iterable[str], where: str) -> None:
        bal = unknown = 0
        for rid in ids:
            if rid in self.balanced_ids:
                bal += 1
            elif rid not in self.by_id:
                unknown += 1
        if bal:
            raise SystemExit(f"{where}: {bal} row(s) name a balanced-set record; the pilot retrieves and judges the "
                             "records on unsatisfied papers only (§5.6)")
        if unknown:
            raise SystemExit(f"{where}: {unknown} row(s) name a record that is not in the record file")

    def label(self, rid: str, cid: str) -> int | None:
        v = self.label_of.get((rid, cid))
        return None if v is None else int(v)

    def outcomes(self, legs: Sequence[str]) -> dict[str, dict[str, Any]]:
        """hit / hit_gold per record (§6.1), for the legs named, after checking that every
        found@10 pair they demand carries a final label."""
        missing = 0
        for leg in legs:
            for row in self.rows.get(leg) or []:
                rec = self.by_id[row["record_id"]]
                missing += sum((row["record_id"], c) not in self.label_of for c in aj.demanded(rec, row, "found10"))
        if missing:
            raise SystemExit(f"{missing} found@10 pair instance(s) of the contrast cells have no final label: judge "
                             "them before the pilot report (an unlabelled pair would count as not answering)")
        return {leg: ast.leg_outcomes(self.rows.get(leg) or [], self.by_id, self.label, self.pair_paper)
                for leg in legs}


# ------------------------------------------------------------------ the estimates

def boot_share_ci(clusters: Sequence[str], flags: Sequence[int], n_boot: int, seed: int,
                  scale: float = 1.0) -> list[float] | None:
    """Paper-cluster percentile 95 % interval of ``sum(flags) / n`` (x ``scale``)."""
    _, sums, cnt = ast.cluster_sums(clusters, [float(x) for x in flags])
    if sums.size == 0:
        return None
    vals = cs._bootstrap_net_shares(np.asarray(sums, float), np.asarray(cnt, float), n_boot, seed)
    return [round(scale * float(x), 4) for x in np.percentile(vals, [2.5, 97.5])]


def discordance(recs: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]], a: str,
                b: Sequence[str], n_boot: int, seed: int, how: str = "any") -> dict[str, Any]:
    """Share of records whose found@10 in cell ``a`` differs from that in the cell(s) ``b``:
    ``any`` (differs from at least one: for the growth contrast, the record's H2 value
    hit(n) - mean(hit(r1), hit(r2)) is non-zero exactly then) or ``all`` (differs from every
    one). Only *differs or not* is computed, never which way. A record failed in any of the
    cells is excluded and counted."""
    clusters: list[str] = []
    flags: list[int] = []
    missing = 0
    for r in recs:
        oa = (outcomes.get(a) or {}).get(r["record_id"])
        obs = [(outcomes.get(x) or {}).get(r["record_id"]) for x in b]
        if oa is None or any(o is None for o in obs):
            missing += 1
            continue
        diff = [int(oa["hit"]) != int(o["hit"]) for o in obs if o is not None]
        clusters.append(str(r["paper"]))
        flags.append(int(any(diff) if how == "any" else all(diff)))
    n, k = len(flags), sum(flags)
    return {"cells": [a, *b], "rule": how, "n": n, "papers": len(set(clusters)), "missing": missing,
            "discordant": k, "share": round(k / n, 4) if n else None,
            "ci95": boot_share_ci(clusters, flags, n_boot, seed)}


def share(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "share": round(k / n, 4) if n else None, "wilson95": jd.wilson(k, n)}


def by_arm(rows: Sequence[Mapping[str, Any]], pred: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
    out = {a: share(sum(bool(pred(x)) for x in rows if x.get("arm") == a), sum(x.get("arm") == a for x in rows))
           for a in asc.ARM_GROUPS}
    out["overall"] = share(sum(bool(pred(x)) for x in rows), len(rows))
    return out


def judge_shares(final: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """§5.6: *answers* share per arm, the tiebreaker share, the Opus-filtered share of the drift
    sample per arm, over every final label of the pilot (unjudged = not *answers*, §6.3)."""
    rows = list(final.values())
    drift = [x for x in rows if x.get("drift_sampled")]
    return {"pairs_finalized": len(rows), "per_arm_pairs": dict(Counter(x.get("arm") for x in rows)),
            "answers": by_arm(rows, lambda x: x.get("label") == 1),
            "tiebreaker": {
                "escalated": by_arm(rows, lambda x: x.get("source") != "agree"),
                "tiebroken": by_arm(rows, lambda x: x.get("source") in ("tiebreak", "tiebreak_r2")),
                "unjudged": by_arm(rows, lambda x: x.get("source") == "unjudged"),
                "rule": "escalated: C and E did not agree (sent to the tiebreaker); tiebroken: the tiebreaker's "
                        "label is final; unjudged: no label after the re-issue (§6.3)"},
            "opus_filtered": by_arm(drift, lambda x: x.get("opus_status") == "no_reference")}


def registry_counts(p: Pilot) -> dict[str, Any]:
    """§14 O7: the judge registry's deduplicated pairs, in total and per judging stage. Never per
    leg (module docstring, *Blind to outcomes*)."""
    reg = p.registry
    return {"pairs": len(reg), "per_judging_stage": dict(sorted(Counter(str(x.get("stage")) for x in reg).items()))}


def gateway_calls(judge_dir: Path, n_pairs: int) -> dict[str, Any]:
    """Calls per gateway backend as the judge's caps count them: the judge's own
    :func:`alias_study_judge.gateway_calls_so_far`, the function ``Pipeline.calls_so_far`` itself
    calls (per tag the larger of the calls log and the dispatch log, re-issues included), read
    without opening a pipeline (which may write)."""
    out: dict[str, Any] = {}
    for kind in ("argon", "opus"):
        n = aj.gateway_calls_so_far(judge_dir, kind)
        out[kind] = {"calls": n, "per_registry_pair": round(n / n_pairs, 4) if n_pairs else None}
    return out


def _forbidden_key(k: str) -> bool:
    low = k.casefold()
    return (low in FORBIDDEN_KEYS or k in FORBIDDEN_MEMBERS or low.startswith(("p_", "net", "verdict", "status_h"))
            or low.endswith(("_p", "_pvalue")))


def no_test_quantities(obj: Any, path: str = "") -> None:
    """Refuse a key naming a test quantity, a direction or a member (:data:`FORBIDDEN_KEYS`,
    :data:`FORBIDDEN_MEMBERS`, and the prefixes ``p_``, ``net``, ``verdict``), and a string value
    naming a verdict (:data:`FORBIDDEN_VALUES`), at any depth (module docstring)."""
    where = path or "the top level"
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if _forbidden_key(str(k)):
                raise SystemExit(f"the pilot report would carry {path + '.' + str(k) if path else str(k)!r}: no "
                                 "Layer-1 test quantity is computed or printed by the pilot (§5.6)")
            no_test_quantities(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            no_test_quantities(v, path)
    elif isinstance(obj, str) and any(x in obj.casefold() for x in FORBIDDEN_VALUES):
        raise SystemExit(f"the pilot report would carry a verdict at {where}: no Layer-1 test quantity is computed or "
                         "printed by the pilot (§5.6)")


def provenance() -> dict[str, Any]:
    try:
        from ragstack.provenance import experiment_provenance
        prov = experiment_provenance()
    except Exception:  # noqa: BLE001
        prov = jd.provenance()
    return {k: v for k, v in prov.items() if k not in PROVENANCE_DROP}


def report(records: Path, runs_dir: Path, judge_dir: Path, n_boot: int = N_BOOT,
           prov: Mapping[str, Any] | None = None) -> dict[str, Any]:
    p = Pilot(records, runs_dir, judge_dir)
    seed = asc.seed_int(asc.SEEDS["boot"])
    out = p.outcomes(CONTRAST_LEGS)
    g_recs = [r for r in p.recs if r["kind"] == "G"]
    c_recs = [r for r in p.recs if r["kind"] == "C"]
    disc = {
        "glossary_G": discordance(g_recs, out, GLOSSARY_CELLS[0], GLOSSARY_CELLS[1:], n_boot, seed),
        "glossary_C": discordance(c_recs, out, GLOSSARY_CELLS[0], GLOSSARY_CELLS[1:], n_boot, seed),
        "growth": discordance(p.recs, out, GROWTH_NEIGHBOUR, GROWTH_DRAWS, n_boot, seed, "any"),
        "growth_both_draws": discordance(p.recs, out, GROWTH_NEIGHBOUR, GROWTH_DRAWS, n_boot, seed, "all"),
        "growth_vs_random1": discordance(p.recs, out, GROWTH_NEIGHBOUR, GROWTH_DRAWS[:1], n_boot, seed),
        "growth_vs_random2": discordance(p.recs, out, GROWTH_NEIGHBOUR, GROWTH_DRAWS[1:], n_boot, seed),
        "note": "growth: the record's H2 value is non-zero (differs from at least one draw); growth_both_draws: "
                "differs from both (the shared part, rho in alias_study_power). The pilot papers' own neighbours "
                "are not in the neighbour arm, so growth understates H2's discordance (§5.6)."}
    pairs = registry_counts(p)
    res: dict[str, Any] = {
        "schema": SCHEMA, "mode": MODE, "prereg": "DRAFT v3 §5.6 (R8)",
        "reads": "unsigned discordance and judge shares only; no found@10 level, Layer-1 test, net change, direction, gate "
                 "or p value is computed",
        "records": {"pilot": len(p.recs), "G": len(g_recs), "C": len(c_recs),
                    "papers": len({r["paper"] for r in p.recs})},
        "record_set": {k: p.identity[k] for k in ("accepted_sha256", "balanced_sha256", "records", "balanced",
                                                   "descriptive")},
        "run_dates_utc": {"first": min(p.utc), "last": max(p.utc)} if p.utc else None,
        "legs": {leg: {k: v[k] for k in LEG_FIELDS if k in v} for leg, v in p.legs.items()},
        "discordance": disc,
        "judge": judge_shares(p.final),
        "judge_registry": pairs,
        "gateway_calls": gateway_calls(judge_dir, pairs["pairs"]),
        "intervals": {"discordance": "paper-cluster percentile bootstrap, 95 %",
                      "pair_shares": "Wilson score, 95 %", "n_boot": n_boot, "seed": asc.SEEDS["boot"],
                      "numpy_seed": seed,
                      "seed_rule": asc.SEED_RULE},
        "stage_a_commit_sha256": p.gate["stage_a_commit_sha256"],
        "spot_checks_waiver": p.gate.get("spot_checks_waiver"),
        "inputs": dict(sorted(p.inputs.items())),
        "experiment_provenance": dict(prov) if prov is not None else provenance(),
    }
    ep = res["experiment_provenance"]
    res["citable"] = bool(ep.get("dirty") is False and ep.get("untracked") == 0)
    no_test_quantities(res)
    chunk_ids = {c for rows in p.rows.values() for x in rows for c in (x.get("ranked_chunk_ids") or [])}
    chunk_ids |= {x["chunk_id"] for x in p.registry}
    forbidden = ([r["record_id"] for r in p.all_records] + [str(r["paper"]) for r in p.all_records]
                 + sorted(chunk_ids) + [str(x.get("c_paper")) for x in p.registry])
    bad = asc.public_problems(res, forbidden, [r["question"] for r in p.all_records])
    if bad:
        raise SystemExit(f"the pilot report would carry {len(bad)} private string(s); refused")
    return res


# ------------------------------------------------------------------ markdown

def _pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.1f} %"


def _ci(ci: Sequence[float] | None, scale: float = 100.0) -> str:
    return "–" if not ci else f"[{scale * ci[0]:.1f}, {scale * ci[1]:.1f}]"


def markdown(res: Mapping[str, Any]) -> str:
    d = res["discordance"]
    lines = ["# Alias study pre-freeze pilot (§5.6): counts", "",
             f"{res['records']['pilot']} pilot records ({res['records']['G']} G, {res['records']['C']} C) on "
             f"{res['records']['papers']} papers. {res['reads']}.", "",
             "| contrast | n | discordant | share | 95 % CI (paper bootstrap) |", "|---|---|---|---|---|"]
    for k in ("glossary_G", "glossary_C", "growth", "growth_both_draws", "growth_vs_random1", "growth_vs_random2"):
        x = d[k]
        lines.append(f"| {k} | {x['n']} | {x['discordant']} | {_pct(x['share'])} | {_ci(x['ci95'])} |")
    j = res["judge"]
    lines += ["", f"Judge: {j['pairs_finalized']} final labels.", "",
              "| arm | answers | escalated | tiebroken | unjudged | Opus filtered (of drift sample) |",
              "|---|---|---|---|---|---|"]
    for a in (*asc.ARM_GROUPS, "overall"):
        t = j["tiebreaker"]
        cells = [j["answers"][a], t["escalated"][a], t["tiebroken"][a], t["unjudged"][a], j["opus_filtered"][a]]
        lines.append(f"| {a} | " + " | ".join(f"{c['k']}/{c['n']} ({_pct(c['share'])})" for c in cells) + " |")
    reg = res["judge_registry"]
    gw = res["gateway_calls"]
    stages = ", ".join(f"stage {k} {v}" for k, v in reg["per_judging_stage"].items())
    lines += ["", f"Judge registry: {reg['pairs']} deduplicated pairs ({stages}). Gateway calls: Argon "
              f"{gw['argon']['calls']}, Opus {gw['opus']['calls']}.", "",
              f"Stage-A commit `{res['stage_a_commit_sha256'][:12]}…`; seed `{res['intervals']['seed']}`, "
              f"{res['intervals']['n_boot']} resamples; citable {str(res['citable']).lower()}.", ""]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--records", required=True, type=Path)
    ap.add_argument("--runs-dir", required=True, type=Path)
    ap.add_argument("--judge-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--md", type=Path, default=None)
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    a = ap.parse_args(argv)
    res = report(a.records, a.runs_dir, a.judge_dir, a.n_boot)
    a.out.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if a.md:
        a.md.write_text(markdown(res), encoding="utf-8")
    print(json.dumps({"out": str(a.out), "records": res["records"], "citable": res["citable"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
