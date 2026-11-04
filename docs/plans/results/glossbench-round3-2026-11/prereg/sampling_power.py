#!/usr/bin/env python3
"""Round-3 planning: paper eligibility, pool size per n_papers, and power.

Reads per-paper-{O,A,P}.json from count_attachment.py (counts per paper, no text).
Eligibility (arm-blind w.r.t. the ATTACHMENT arms; the glossary model is a fixed factor):
  pop   = V + M_num chunks of the paper (arm-blind population)
  glos  = the fixed Qwen3.6 glossary renders >= 1 chunk of the paper under mention
          (equivalently: the paper's glossary has entries and some chunk mentions one)
Gate quantity per sampled paper: min(cap, pop) sampled chunks; expected Qwen3.6 mention reach
on the sampled chunks = cap_p * (qwen36p_mention_V + qwen36p_mention_Mnum)/pop (uniform sampling).
Power: chunktrial_stats.simulate_power on the realised layout of each design.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, "python/scripts/eval")
import chunktrial_stats as cs  # noqa: E402

S = Path(sys.argv[1])
out: dict = {"eligibility": {}, "designs": {}, "power": {}}


def load(set_):
    return json.load(open(S / f"per-paper-{set_}.json"))


def summarise(xs):
    xs = sorted(xs)
    if not xs:
        return {}
    return {"mean": round(statistics.mean(xs), 1), "p5": xs[int(.05 * (len(xs) - 1))],
            "median": xs[len(xs) // 2], "p95": xs[int(.95 * (len(xs) - 1))]}


for set_ in "OAP":
    pp = load(set_)
    rows = []
    for pid, r in pp.items():
        pop = r["V"] + r["M_num"]
        reach_m = r["qwen36p_mention_V"] + r["qwen36p_mention_Mnum"]
        reach_v = r["qwen36p_value_V"]
        rows.append({"pop": pop, "V": r["V"], "M": r["M_num"], "reach_m": reach_m, "reach_v": reach_v,
                     "glos": r["qwen36p_mention"] > 0, "eval25": r["eval25"],
                     "distract_m": r["qwen36p_mention"], "chunks": r["chunks"]})
    elig = {}
    for min_pop in (3, 5, 8, 10, 12, 15, 20):
        for need_glos in (False, True):
            e = [r for r in rows if r["pop"] >= min_pop and (r["glos"] or not need_glos)]
            elig[f"min_pop>={min_pop}|glos={need_glos}"] = {
                "papers": len(e), "pop_sum": sum(r["pop"] for r in e),
                "reach_m_sum": sum(r["reach_m"] for r in e), "reach_v_sum": sum(r["reach_v"] for r in e),
                "V_sum": sum(r["V"] for r in e), "M_sum": sum(r["M"] for r in e)}
    out["eligibility"][set_] = elig
    # current round-2 eval-25 under the two rules (for reference)
    e25 = [r for r in rows if r["eval25"]]
    out["eligibility"][set_]["round2_eval25"] = {
        "papers": len(e25), "pop_sum": sum(r["pop"] for r in e25), "V_sum": sum(r["V"] for r in e25),
        "M_sum": sum(r["M"] for r in e25), "reach_m_sum": sum(r["reach_m"] for r in e25),
        "reach_v_sum": sum(r["reach_v"] for r in e25)}
    designs = {}
    for min_pop, need_glos in ((5, True), (8, True), (10, True), (12, True), (3, False), (5, False)):
        e = [r for r in rows if r["pop"] >= min_pop and (r["glos"] or not need_glos)]
        for n in (20, 25, 30, 40, 50, 60):
            if n > len(e):
                continue
            for cap in (8, 10, 12, 16):
                rng = random.Random(f"r3plan|{set_}|{min_pop}|{need_glos}|{n}|{cap}")
                sampled, reach_m, reach_v, disc_proxy = [], [], [], []
                layouts = []
                for _ in range(1000):
                    smp = rng.sample(e, n)
                    k = [min(cap, r["pop"]) for r in smp]
                    sampled.append(sum(k))
                    # uniform chunk sampling within the paper: expected reached among sampled
                    rm = sum(kk * r["reach_m"] / r["pop"] for kk, r in zip(k, smp))
                    rv = sum(kk * r["reach_v"] / r["pop"] for kk, r in zip(k, smp))
                    reach_m.append(rm); reach_v.append(rv)
                    disc_proxy.append(rm - rv)  # gold chunks whose field differs between the two arms
                    layouts.append(k)
                designs[f"{set_}|min_pop>={min_pop}|glos={need_glos}|n={n}|cap={cap}"] = {
                    "eligible": len(e), "sampled_chunks": summarise(sampled),
                    "exp_reach_mention": summarise([round(x) for x in reach_m]),
                    "exp_reach_value": summarise([round(x) for x in reach_v]),
                    "exp_gold_field_differs": summarise([round(x) for x in disc_proxy]),
                    "layout_example": sorted(layouts[0], reverse=True),
                }
    out["designs"][set_] = designs
    print(set_, "eligibility+designs done", flush=True)

# power on representative layouts, with the committed simulate_power
power = {}
for label, n, cap, min_pop in (("O|n=40|cap=12", 40, 12, 10), ("O|n=50|cap=10", 50, 10, 10), ("O|n=30|cap=12", 30, 12, 10),
                               ("O|n=25|cap=12", 25, 12, 10), ("A|n=25|cap=8", 25, 8, 5), ("P|n=25|cap=12", 25, 12, 10)):
    set_ = label[0]
    key = f"{set_}|min_pop>={min_pop}|glos=True|n={n}|cap={cap}"
    d = out["designs"][set_].get(key)
    if not d:
        continue
    layout = d["layout_example"]
    # accepted records ~ 1/1.3 of drafted chunks: shrink the layout
    layout_acc = [max(1, round(k / 1.3)) for k in layout]
    grid = {}
    for alt, fam in (("greater", 4), ("two-sided", 4)):
        for disc in (0.08, 0.10, 0.17, 0.25):
            for s in (0.60, 0.70, 0.85):
                icc = min(0.99, 0.15 ** 2 / (s * (1 - s)))
                r = cs.simulate_power(len(layout_acc), layout_acc, disc, 2 * s - 1, share_icc=icc, alpha=0.05,
                                      n_family=fam, alternative=alt, n_sims=1500, n_flips=1500, seed=20261030)
                grid[f"{alt}|d={disc}|s={s}"] = round(r["power"], 3)
    power[label] = {"layout_accepted": layout_acc, "n_records_accepted": sum(layout_acc),
                    "n_records_drafted": sum(layout), "grid": grid}
    print(label, "power done", flush=True)
out["power"] = power
(S / "sampling_power.json").write_text(json.dumps(out, indent=1, sort_keys=True))
print("written")
