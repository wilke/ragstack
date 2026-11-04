"""``decide``: the pass rule of PLAN-AND-PASS-RULE.md with Addendum A.

Inputs: ``score.json`` and ``per_question.jsonl`` from ``score``; margins from the config
(``margins: {"M_ni", "M_mid", "M_high"}``, set after the noise run; refused when absent).
All tests are on the primary metric (union coverage >= 90%) at ``decide.k`` (5), on the
primary question set (``clean`` when the audit ran, else ``all``), paired vs the baseline.

Per challenger arm (every non-baseline arm):

1. Threshold by cost class: cheap ``-M_ni`` (non-inferiority), mid ``+M_mid``,
   expensive ``+M_high`` (superiority).
2. Context-token rule: if the arm's mean context tokens per query at k exceed
   ``context_ratio`` (1.5) x the baseline's, it needs the NEXT class's margin
   (cheap -> +M_mid, mid -> +M_high). Expensive has no next class: it keeps +M_high and the
   bump is recorded as ``context_rule: "no_next_class"``.
3. Class test: paired bootstrap 95% CI lower bound >= threshold, AND the one-sided bootstrap
   p-value for H0 "diff <= threshold" is rejected after Holm across all challengers at
   ``alpha_one_sided`` (0.025, the one-sided level of a two-sided 95% CI).
4. Stratum guard (Addendum A.4): for every reported stratum of ``score.strata_dims``
   (journal family, length band; not their cross product) with at least
   ``decide.min_stratum_n`` (30) paired questions, the arm fails if the point difference <
   ``stratum_point`` (-2.0) AND its CI upper bound < 0. Smaller strata are listed as
   ``strata_not_tested`` (a single question would otherwise give a degenerate CI that can
   veto an arm on its own).
5. Expensive arms also need ``workflow_feasible: true`` on the arm in the config.

An arm passes when 3, 4 and 5 hold. Among passing arms the pick is the cheapest ingest:
cost class (cheap < mid < expensive), then the arm's optional ``ingest_cost`` number, then
fewer context tokens, then name. Nothing passes: keep the baseline.

Refusals (exit 2, nothing written) unless ``--force-unchecked`` is given, in which case
the overridden checks are recorded in ``decision.json`` (``forced_unchecked``) and flagged
in ``decision.md``:

- ``score.json`` lists quality ``refusals`` (missing gold record text, empty
  ``source_path`` or unlocated share above ``score.max_unlocated_share``);
- ``noise.json`` is missing, records scoring refusals, or the config margins differ from
  its margins;
- ``audit.jsonl`` was missing when ``score`` ran (no ``clean`` set).

Writes ``decision.json`` and ``decision.md``: aggregates only (arm names, classes, numbers;
no question text, no collection ids, no paths), safe to commit.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from .common import log, read_json, read_jsonl, write_json
from .config import ConfigError, baseline_arm, out_path, require
from .config import arms as cfg_arms
from .stats import holm_bonferroni, p_greater, paired_diff

CLASS_RANK = {"cheap": 0, "mid": 1, "expensive": 2}
NEXT_CLASS = {"cheap": "mid", "mid": "expensive", "expensive": None}


def margins_from(cfg: dict[str, Any]) -> dict[str, float]:
    m = cfg.get("margins")
    if not isinstance(m, dict) or any(m.get(k) is None for k in ("M_ni", "M_mid", "M_high")):
        raise ConfigError("decide needs margins {M_ni, M_mid, M_high} in the config (set after the noise run)")
    return {k: float(m[k]) for k in ("M_ni", "M_mid", "M_high")}


def class_threshold(cost_class: str, margins: dict[str, float]) -> float:
    return {"cheap": -margins["M_ni"], "mid": margins["M_mid"], "expensive": margins["M_high"]}[cost_class]


def effective_class(cost_class: str, ctx_ratio: float | None, limit: float) -> tuple[str, str | None]:
    if ctx_ratio is None or ctx_ratio <= limit:
        return cost_class, None
    nxt = NEXT_CLASS[cost_class]
    return (nxt, "bumped") if nxt else (cost_class, "no_next_class")


def stratum_guard(strata: dict[str, dict[str, dict[str, Any]]], point_limit: float,
                  min_n: int = 0, not_tested: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Failing strata: point < ``point_limit`` AND CI upper < 0, among strata with
    ``n >= min_n``. ``strata`` is ``{dim: {value: {"point", "lo", "hi", "n"}}}``. Smaller
    strata are appended to ``not_tested`` (when given) and never fail the guard."""
    bad = []
    for dim, vals in strata.items():
        for v, ci in vals.items():
            n = ci.get("n")
            if min_n and (n is None or n < min_n):
                if not_tested is not None:
                    not_tested.append({"dim": dim, "value": v, "n": n})
                continue
            if ci.get("point") is None or ci.get("hi") is None:
                continue
            if ci["point"] < point_limit and ci["hi"] < 0:
                bad.append({"dim": dim, "value": v, "point": round(ci["point"], 3), "hi": round(ci["hi"], 3),
                            "n": ci.get("n")})
    return bad


def evaluate(arms: list[dict[str, Any]], base: str, diffs: dict[str, dict[str, Any]],
             strata: dict[str, dict[str, dict[str, dict[str, Any]]]], ctx: dict[str, float | None],
             margins: dict[str, float], dcfg: dict[str, Any]) -> dict[str, Any]:
    """The rule on precomputed numbers. ``diffs[arm]`` = paired_diff output incl. ``_dist``
    (the bootstrap distribution of the difference in points); ``strata[arm]`` = per-dim
    per-value diff CIs; ``ctx[arm]`` = mean context tokens at k."""
    alpha = float(dcfg["alpha_one_sided"])
    challengers = [a for a in arms if a["name"] != base]
    per: dict[str, dict[str, Any]] = {}
    pvals: dict[str, float] = {}
    for a in challengers:
        name = a["name"]
        ratio = None
        if ctx.get(base) and ctx.get(name) is not None:
            ratio = ctx[name] / ctx[base]
        eff, rule = effective_class(a["cost_class"], ratio, float(dcfg["context_ratio"]))
        thr = class_threshold(eff, margins)
        d = diffs[name]
        p = p_greater(d["_dist"], thr)
        pvals[name] = p
        per[name] = {"cost_class": a["cost_class"], "test_class": eff, "context_rule": rule,
                     "context_ratio": None if ratio is None else round(ratio, 4),
                     "threshold": thr, "test": "non_inferiority" if thr < 0 else "superiority",
                     "n": d["n"], "point": _r(d["point"]), "lo": _r(d["lo"]), "hi": _r(d["hi"]),
                     "p_one_sided": round(p, 6), "ingest_cost": a.get("ingest_cost")}
    holm = holm_bonferroni(pvals, alpha=alpha) if pvals else {}
    for a in challengers:
        name = a["name"]
        r = per[name]
        adj, rej = holm[name]
        r["p_holm"] = round(adj, 6)
        r["ci_ok"] = r["lo"] is not None and r["lo"] >= r["threshold"]
        r["holm_ok"] = bool(rej)
        r["class_test"] = r["ci_ok"] and r["holm_ok"]
        untested: list[dict[str, Any]] = []
        r["stratum_failures"] = stratum_guard(strata.get(name, {}), float(dcfg["stratum_point"]),
                                              int(dcfg.get("min_stratum_n") or 0), untested)
        r["strata_not_tested"] = untested
        r["stratum_guard"] = not r["stratum_failures"]
        r["workflow_ok"] = True if a["cost_class"] != "expensive" else bool(a.get("workflow_feasible"))
        reasons = []
        if not r["class_test"]:
            reasons.append("class_test")
        if not r["stratum_guard"]:
            reasons.append("stratum_guard")
        if not r["workflow_ok"]:
            reasons.append("workflow_feasibility")
        r["pass"] = not reasons
        r["fail_reasons"] = reasons
        r["context_tokens"] = ctx.get(name)
    passing = [a for a in challengers if per[a["name"]]["pass"]]

    def key(a):
        ic = a.get("ingest_cost")
        return (CLASS_RANK[a["cost_class"]], math.inf if ic is None else float(ic),
                math.inf if ctx.get(a["name"]) is None else ctx[a["name"]], a["name"])

    passing.sort(key=key)
    winner = passing[0]["name"] if passing else base
    return {"winner": winner, "kept_baseline": not passing, "passing_order": [a["name"] for a in passing],
            "arms": per, "baseline": base, "baseline_context_tokens": ctx.get(base)}


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 4)


def gather(cfg: dict[str, Any]) -> tuple[dict, dict, dict, dict[str, Any]]:
    """diffs (with bootstrap dists), strata CIs and context tokens from the score outputs."""
    score = read_json(out_path(cfg, "score.json"))
    rows = read_jsonl(out_path(cfg, "per_question.jsonl"))
    set_name = score["primary_set"]
    if set_name == "clean":
        rows = [r for r in rows if not r["ambiguous_gold"]]
    k = str(int(cfg["decide"]["k"]))
    base = baseline_arm(cfg)["name"]
    sc = cfg["score"]
    iters, seed = int(sc["bootstrap_iters"]), int(sc["bootstrap_seed"])
    vec = {a["name"]: np.array([r["arms"][a["name"]][k]["primary"] for r in rows]) for a in cfg_arms(cfg)}
    diffs = {a: paired_diff(v, vec[base], iters, seed) for a, v in vec.items() if a != base}
    block = score["sets"][set_name]
    st = block["primary"][k]["strata"]
    strata = {a: {d: {v: c["diff"].get(a, {}) for v, c in st[d].items()} for d in sc["strata_dims"] if d in st}
              for a in vec if a != base}
    ctx = block["context_tokens"][k]
    info = {"set": set_name, "k": int(k), "n": len(rows), "tokens_estimated": score.get("tokens_estimated"),
            "tokenizer": score.get("tokenizer"), "pooled_hit": block["primary"][k]["pooled"]["hit"],
            "strict": block["strict"][k]["pooled"], "skip_loss": score.get("skip_loss"),
            "lossy_strata": score.get("lossy_strata"), "without_lossy": block.get("without_lossy_strata"),
            "ambiguous": score["sets"].get("ambiguous", {}).get("primary", {}).get(k, {}).get("pooled"),
            "journal_confusion": score.get("journal_confusion"), "discordance": block["discordance"][k],
            "missing_by_arm": score.get("missing_by_arm"), "hit_quality": score.get("hit_quality"),
            "score_refusals": score.get("refusals") or [], "audit_present": bool(score.get("audit_present")),
            "records_without_text": score.get("records_without_text"),
            "check_writer": _check_writer_summary(score.get("check_writer"), k)}
    return diffs, strata, ctx, info


def _check_writer_summary(cw: dict[str, Any] | None, k: str) -> dict[str, Any] | None:
    """Pooled primary diffs of the check-writer set at k (aggregates only)."""
    if not cw:
        return None
    blk = cw["sets"].get(cw.get("primary_set") or "all") or {}
    pooled = (blk.get("primary") or {}).get(k, {}).get("pooled") or {}
    return {"set": cw.get("primary_set"), "n": blk.get("n"), "diff": pooled.get("diff"),
            "hit": pooled.get("hit"), "refusals": cw.get("refusals")}


def preflight(cfg: dict[str, Any], margins: dict[str, float], info: dict[str, Any]) -> list[str]:
    """Reasons decide must not run without ``--force-unchecked``."""
    out = [f"score: {r}" for r in info.get("score_refusals") or []]
    npath = out_path(cfg, "noise.json")
    if not npath.exists():
        out.append("noise.json missing: margins cannot be checked against the noise run")
    else:
        noise = read_json(npath)
        nm = noise.get("margins") or {}
        if noise.get("refusals"):
            out.append("noise.json was computed on scoring with refusals: " + "; ".join(noise["refusals"]))
        if not all(abs(float(nm.get(k, math.nan)) - margins[k]) < 1e-9 for k in margins):
            out.append(f"config margins {margins} differ from noise.json margins {nm}")
    if not info.get("audit_present"):
        out.append("audit.jsonl was missing when score ran: no clean set, ambiguous gold not excluded")
    return out


def skip_loss_lines(loss: dict[str, dict[str, Any]] | None) -> list[str]:
    """One Markdown table of skip loss by stratum: skipped, pending, passage stage."""
    if not loss:
        return []
    lines = ["", "Skip loss by stratum (skipped = no passing question; pending = writer calls never "
             "completed; passage stage = eval articles with no usable passage):", "",
             "| Dim | Value | passages | skipped | pending | passage stage | loss |", "|---|---|---|---|---|---|---|"]
    for d, vals in loss.items():
        for v, x in vals.items():
            lines.append(f"| {d} | {v} | {x.get('passages', 0)} | {x.get('skipped', 0)} | {x.get('pending', 0)} | "
                         f"{x.get('passage_stage', 0)} | {_f(x.get('loss'), 3)} |")
    return lines


def markdown(dec: dict[str, Any]) -> str:
    m, info = dec["margins"], dec["inputs"]
    lines = [
        "# OA chunking eval: decision", "",
        f"Rule: PLAN-AND-PASS-RULE.md with Addendum A. Metric: primary hit@{info['k']} (union of top-k "
        f"chunks covers >= 90% of the gold quote), question set `{info['set']}`, n = {info['n']} paired questions.",
        f"Margins (points): M_ni {m['M_ni']}, M_mid {m['M_mid']}, M_high {m['M_high']}. "
        f"Holm across {len(dec['arms'])} challengers, one-sided alpha {dec['alpha_one_sided']}.",
        f"Context tokens: {info['tokenizer']}" + (" (ESTIMATED: no tokenizer configured or it failed to load)" if info.get("tokens_estimated") else "") + ".",
        "", f"**Decision: `{dec['winner']}`**" + (" (nothing cleared its test; baseline kept)"
                                                  if dec["kept_baseline"] else ""), "",
    ]
    if dec.get("forced_unchecked"):
        lines += ["**FORCED UNCHECKED** (`--force-unchecked`): " + "; ".join(dec["forced_unchecked"]), ""]
    lines += [
        "| Arm | Class (test) | hit | diff vs baseline [95% CI] | threshold | p Holm | ctx tokens (x base) | "
        "strata guard | pass |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    hit = info["pooled_hit"]
    b = dec["baseline"]
    lines.append(f"| {b} (baseline) | - | {_f(hit[b]['point'])} | - | - | - | {_f(dec['baseline_context_tokens'], 0)} | - | - |")
    for name, r in dec["arms"].items():
        cls = r["cost_class"] + ("" if r["test_class"] == r["cost_class"] else f" -> {r['test_class']}")
        guard = "ok" if r["stratum_guard"] else "; ".join(f"{s['dim']}={s['value']} {s['point']:+.1f}"
                                                           for s in r["stratum_failures"])
        lines.append(f"| {name} | {cls} | {_f(hit[name]['point'])} | {_f(r['point'], 2, True)} "
                     f"[{_f(r['lo'], 2, True)}, {_f(r['hi'], 2, True)}] | {r['threshold']:+.2f} | {r['p_holm']:.4f} | "
                     f"{_f(r['context_tokens'], 0)} ({_f(r['context_ratio'], 2)}) | {guard} | "
                     f"{'PASS' if r['pass'] else 'fail: ' + ', '.join(r['fail_reasons'])} |")
    untested = sorted({f"{s['dim']}={s['value']} (n={s['n']})" for r in dec["arms"].values()
                       for s in r.get("strata_not_tested") or []})
    if untested:
        lines += ["", f"Strata below min n = {dec.get('min_stratum_n')} (not tested by the stratum guard): "
                  + ", ".join(untested) + "."]
    lines += ["", "## Secondary and checks", ""]
    strict = info.get("strict") or {}
    if strict:
        lines.append("Strict single-chunk containment, diff vs baseline: " + ", ".join(
            f"{a} {_f(c['point'], 2, True)} [{_f(c['lo'], 2, True)}, {_f(c['hi'], 2, True)}]"
            for a, c in strict.get("diff", {}).items()) + ".")
    amb = info.get("ambiguous")
    if amb:
        lines.append(f"Ambiguous-gold items (scored separately, n = {amb['n']}): " + ", ".join(
            f"{a} {_f(c['point'], 2, True)}" for a, c in amb.get("diff", {}).items()) + ".")
    lossy = {d: v for d, v in (info.get("lossy_strata") or {}).items() if v}
    if lossy:
        wl = info.get("without_lossy") or {}
        p = (wl.get("primary") or {}).get(str(info["k"]), {})
        lines.append(f"Strata losing > 10% of passages to skips: {lossy}. Without them (n = {wl.get('n')}): " +
                     ", ".join(f"{a} {_f(c['point'], 2, True)} [{_f(c['lo'], 2, True)}, {_f(c['hi'], 2, True)}]"
                               for a, c in p.get("diff", {}).items()) + ".")
    else:
        lines.append("No stratum loses more than 10% of passages to skips.")
    jc = info.get("journal_confusion") or {}
    if jc:
        lines.append("Journal confusion (share of wrong top-1 chunks from the same journal family; chance share): " +
                     ", ".join(f"{a} {_f(v['share_same_family'], 3)} ({_f(v['chance_share'], 3)})"
                               for a, v in jc.items()) + ".")
    if info.get("discordance"):
        lines.append("Discordance per arm pair: " + ", ".join(f"{k} {_f(v, 3)}" for k, v in info["discordance"].items()) + ".")
    if any((info.get("missing_by_arm") or {}).values()):
        lines.append(f"Questions without an ok retrieval, by arm: {info['missing_by_arm']} (excluded from the paired set).")
    hq = info.get("hit_quality") or {}
    if hq:
        lines.append("Hit quality per arm (share of hits with empty source_path; share of gold-record hits "
                     "unlocated or with unverified offsets): " + ", ".join(
                         f"{a} {_f(q['empty_source_path_share'], 4)} / {_f(q['unlocated_share'], 4)}"
                         for a, q in hq.items()) + ".")
    cw = info.get("check_writer")
    if cw and cw.get("diff"):
        lines.append(f"Check-writer set (secondary, not used for the decision; `{cw['set']}`, n = {cw['n']}), "
                     "primary diff vs baseline: " + ", ".join(
                         f"{a} {_f(c['point'], 2, True)} [{_f(c['lo'], 2, True)}, {_f(c['hi'], 2, True)}]"
                         for a, c in cw["diff"].items()) + ".")
    lines += skip_loss_lines(info.get("skip_loss"))
    lines.append("")
    return "\n".join(lines)


def _f(x: Any, places: int = 1, sign: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{places}f}" if sign else f"{x:.{places}f}"


def run(cfg: dict[str, Any], force_unchecked: bool = False) -> int:
    require(cfg, "out_dir")
    margins = margins_from(cfg)
    diffs, strata, ctx, info = gather(cfg)
    problems = preflight(cfg, margins, info)
    if problems and not force_unchecked:
        for p in problems:
            log(f"decide: REFUSED: {p}")
        log("decide: fix the inputs, or rerun with --force-unchecked (recorded in decision.json)")
        return 2
    dec = evaluate(cfg_arms(cfg), baseline_arm(cfg)["name"], diffs, strata, ctx, margins, cfg["decide"])
    dec.update({"margins": margins, "alpha_one_sided": cfg["decide"]["alpha_one_sided"],
                "stratum_point": cfg["decide"]["stratum_point"], "context_ratio": cfg["decide"]["context_ratio"],
                "min_stratum_n": int(cfg["decide"].get("min_stratum_n") or 0), "inputs": info,
                "forced_unchecked": problems if force_unchecked else [],
                "margins_match_noise": not any(p.startswith(("config margins", "noise.json missing"))
                                               for p in problems)})
    write_json(out_path(cfg, "decision.json"), dec)
    out_path(cfg, "decision.md").write_text(markdown(dec), encoding="utf-8")
    log(f"decide: {dec['winner']}" + (" (baseline kept)" if dec["kept_baseline"] else ""))
    return 0
