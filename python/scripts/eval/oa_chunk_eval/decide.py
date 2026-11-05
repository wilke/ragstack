"""``decide``: the pass rule of PLAN-AND-PASS-RULE.md with Addendum A.

Inputs: ``score.json`` and ``per_question.jsonl`` from ``score``; margins from the config
(``margins: {"M_ni", "M_mid", "M_high"}``, set after the noise run; refused when absent).
All tests are on the primary metric (union coverage >= 90%) at ``decide.k`` (5), on the
primary question set (``score.json``'s ``primary_set``: ``clean`` when the audit ran, else
``all``; with the ``_excl_noise`` suffix when ``score.exclude_noise_subset`` is true),
paired vs the baseline. Rows are selected with :func:`score.set_rows`, the same definition
``score`` uses.

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
   Multiplicity (``decide.stratum_correction``, owner 2026-11-05): ``"none"`` (default, the
   rule above with the 95% CI from ``score.json``) or ``"bonferroni"``: the interval used by
   the guard is the two-sided (1 - 0.05/k) paired bootstrap interval, k = the number of
   strata the guard actually tests for that arm (n >= ``min_stratum_n``, across all
   ``strata_dims``). The point condition is unchanged. The corrected bounds are percentiles
   of the same seeded bootstrap distribution ``score`` draws for its 95% CI: ``decide``
   recomputes it from ``per_question.jsonl`` with :func:`stats.paired_diff` on the rows of
   :func:`score.set_rows` grouped by :func:`score.by_stratum` (same rows, order, iterations
   and seed, so the 95% bounds equal ``score.json``'s; the stratum sizes are cross-checked).
   Per arm, ``stratum_guard_detail`` records the correction, k, the level and every tested
   stratum's point and bounds.
5. Expensive arms also need ``workflow_feasible: true`` on the arm in the config.

An arm passes when 3, 4 and 5 hold. The pick (``decide.tie_break``):

- ``"cheapest"`` (default, the rule before 2026-11-05): among passing arms the cheapest
  ingest: cost class (cheap < mid < expensive), then the arm's optional ``ingest_cost``
  number, then fewer context tokens, then name. Nothing passes: keep the baseline.
- ``"paired_ladder"`` (owner 2026-11-05, option (c)): candidates = the baseline (always, as
  the reference) + the passing challengers, ordered by cost: the arm's integer
  ``cost_rank`` (lower = cheaper; set on every arm or on none, ties by config order), else
  cost class then config order. The incumbent starts as the cheapest candidate; each
  costlier candidate in order replaces it only if the paired difference (candidate -
  incumbent) on primary hit@``k`` over the same paired set has a paired bootstrap lower
  bound > 0 at the pass rule's level (two-sided 1 - 2 x ``alpha_one_sided``, i.e. 95%).
  The last incumbent is chosen. Every step (pair, n, point, lo, hi, switched) is written to
  ``ladder``. The differences are computed from the per-question hits in
  ``per_question.jsonl`` with :func:`stats.paired_diff`, the function and seed ``score``
  uses for every reported difference. The context-token rule applies before the ladder (it
  is part of pass/fail).

Sensitivity (``score.exclude_noise_subset`` true): the primary set excludes the noise
subset; the same rule (same config) is also run on ``score.json``'s ``sensitivity_set``
(noise subset included) and written as ``sensitivity`` (pass/fail per arm, the arm it would
choose, the ladder), labelled as NOT the decision. A config whose
``score.exclude_noise_subset`` differs from what ``score.json`` was computed with is a
refusal.

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
from .score import SENSITIVITY_LABEL, by_stratum, set_rows, vectors
from .stats import holm_bonferroni, p_greater, paired_diff

CLASS_RANK = {"cheap": 0, "mid": 1, "expensive": 2}
NEXT_CLASS = {"cheap": "mid", "mid": "expensive", "expensive": None}
#: family level of the stratum guard (its uncorrected interval is the 95% CI)
STRATUM_ALPHA = 0.05
STRATUM_CORRECTIONS = ("none", "bonferroni")
TIE_BREAKS = ("cheapest", "paired_ladder")


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
                  min_n: int = 0, not_tested: list[dict[str, Any]] | None = None,
                  correction: str = "none", detail: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Failing strata: point < ``point_limit`` AND interval upper < 0, among strata with
    ``n >= min_n``. ``strata`` is ``{dim: {value: {"point", "lo", "hi", "n"[, "_dist"]}}}``.
    Smaller strata are appended to ``not_tested`` (when given) and never fail the guard.

    ``correction="none"`` uses the given ``lo``/``hi`` (the 95% CI). ``"bonferroni"`` uses
    the two-sided (1 - 0.05/k) percentile interval of each stratum's bootstrap distribution
    ``_dist``, k = the number of strata tested here. ``detail`` (when given) receives the
    correction, k, the level and every tested stratum's point and bounds."""
    if correction not in STRATUM_CORRECTIONS:
        raise ConfigError(f"decide.stratum_correction must be one of {STRATUM_CORRECTIONS}, not {correction!r}")
    tested = []
    for dim, vals in strata.items():
        for v, ci in vals.items():
            n = ci.get("n")
            if min_n and (n is None or n < min_n):
                if not_tested is not None:
                    not_tested.append({"dim": dim, "value": v, "n": n})
                continue
            if ci.get("point") is None:
                continue
            tested.append((dim, v, ci))
    k = len(tested)
    alpha = STRATUM_ALPHA / k if correction == "bonferroni" and k else STRATUM_ALPHA
    bad, rows = [], []
    for dim, v, ci in tested:
        if correction == "bonferroni":
            dist = ci.get("_dist")
            if dist is None or len(dist) == 0:
                raise ValueError(f"stratum {dim}={v}: bonferroni needs the bootstrap distribution (_dist)")
            lo = float(np.percentile(dist, 100 * alpha / 2))
            hi = float(np.percentile(dist, 100 * (1 - alpha / 2)))
        else:
            lo, hi = ci.get("lo"), ci.get("hi")
            if hi is None:
                continue
        fails = ci["point"] < point_limit and hi < 0
        rows.append({"dim": dim, "value": v, "n": ci.get("n"), "point": _r(ci["point"]), "lo": _r(lo),
                     "hi": _r(hi), "fails": fails})
        if fails:
            bad.append({"dim": dim, "value": v, "point": round(ci["point"], 3), "hi": round(hi, 3),
                        "n": ci.get("n")})
    if detail is not None:
        detail.update({"correction": correction, "k": k, "alpha_two_sided": round(alpha, 6),
                       "level": round(1 - alpha, 6), "strata": rows})
    return bad


def ladder_order(arms: list[dict[str, Any]], names: list[str]) -> tuple[list[str], str]:
    """``names`` ordered cheapest first: by ``cost_rank`` when every arm has one, else by
    cost class; ties and the rest by config order. A partial ``cost_rank`` is refused (an
    unranked arm has no defined place on the ranked scale)."""
    idx = {a["name"]: i for i, a in enumerate(arms)}
    ranked = [a for a in arms if a.get("cost_rank") is not None]
    for a in ranked:
        if isinstance(a["cost_rank"], bool) or not isinstance(a["cost_rank"], int):
            raise ConfigError(f"arm {a['name']}: cost_rank must be an integer")
    if ranked and len(ranked) != len(arms):
        raise ConfigError("cost_rank must be set on every arm or on none (missing on: "
                          + ", ".join(a["name"] for a in arms if a.get("cost_rank") is None) + ")")
    by = {a["name"]: a for a in arms}
    if ranked:
        return sorted(names, key=lambda n: (by[n]["cost_rank"], idx[n])), "cost_rank"
    return sorted(names, key=lambda n: (CLASS_RANK[by[n]["cost_class"]], idx[n])), "cost_class_then_config_order"


def paired_ladder(order: list[str], vec: dict[str, Any], iters: int, seed: int,
                  alpha_two_sided: float) -> tuple[str, list[dict[str, Any]]]:
    """Walk ``order`` (cheapest first): a candidate replaces the incumbent only if the paired
    bootstrap lower bound of (candidate - incumbent) at ``alpha_two_sided`` is > 0.
    ``vec[arm]`` = per-question 0/1 hits, aligned. Returns (chosen arm, steps)."""
    inc = order[0]
    steps = []
    for cand in order[1:]:
        d = paired_diff(vec[cand], vec[inc], iters, seed, alpha=alpha_two_sided)
        switched = d["lo"] is not None and d["lo"] > 0
        steps.append({"candidate": cand, "incumbent": inc, "pair": f"{cand} - {inc}", "n": d["n"],
                      "point": _r(d["point"]), "lo": _r(d["lo"]), "hi": _r(d["hi"]), "switched": switched})
        if switched:
            inc = cand
    return inc, steps


def evaluate(arms: list[dict[str, Any]], base: str, diffs: dict[str, dict[str, Any]],
             strata: dict[str, dict[str, dict[str, dict[str, Any]]]], ctx: dict[str, float | None],
             margins: dict[str, float], dcfg: dict[str, Any], vec: dict[str, Any] | None = None,
             iters: int = 10_000, seed: int = 0) -> dict[str, Any]:
    """The rule on precomputed numbers. ``diffs[arm]`` = paired_diff output incl. ``_dist``
    (the bootstrap distribution of the difference in points); ``strata[arm]`` = per-dim
    per-value diff CIs (with ``_dist`` per stratum for the bonferroni guard); ``ctx[arm]`` =
    mean context tokens at k; ``vec[arm]`` = per-question 0/1 hits on the paired set
    (needed by the ``paired_ladder`` tie-break), bootstrapped with ``iters``/``seed``."""
    alpha = float(dcfg["alpha_one_sided"])
    correction = dcfg.get("stratum_correction") or "none"
    tie = dcfg.get("tie_break") or "cheapest"
    if correction not in STRATUM_CORRECTIONS:
        raise ConfigError(f"decide.stratum_correction must be one of {STRATUM_CORRECTIONS}, not {correction!r}")
    if tie not in TIE_BREAKS:
        raise ConfigError(f"decide.tie_break must be one of {TIE_BREAKS}, not {tie!r}")
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
        detail: dict[str, Any] = {}
        r["stratum_failures"] = stratum_guard(strata.get(name, {}), float(dcfg["stratum_point"]),
                                              int(dcfg.get("min_stratum_n") or 0), untested, correction, detail)
        r["strata_not_tested"] = untested
        r["stratum_guard_detail"] = detail
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
    ladder = None
    if tie == "paired_ladder":
        if vec is None:
            raise ValueError("tie_break paired_ladder needs the per-question hit vectors (vec)")
        order, basis = ladder_order(arms, [base] + [a["name"] for a in passing])
        a2 = 2 * alpha
        winner, steps = paired_ladder(order, vec, iters, seed, a2)
        ladder = {"order": order, "order_basis": basis, "alpha_two_sided": round(a2, 6),
                  "level": round(1 - a2, 6), "steps": steps}
    else:
        winner = passing[0]["name"] if passing else base
    return {"winner": winner, "kept_baseline": winner == base, "no_arm_passed": not passing,
            "passing_order": [a["name"] for a in passing], "tie_break": tie, "ladder": ladder,
            "stratum_correction": correction, "arms": per, "baseline": base,
            "baseline_context_tokens": ctx.get(base)}


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 4)


def strata_from_rows(rows: list[dict[str, Any]], arm_names: list[str], base: str, k: int, dims: list[str],
                     iters: int, seed: int) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
    """``{arm: {dim: {value: paired_diff incl. _dist}}}`` per stratum of ``dims``, computed
    exactly as ``score`` computes its stratum CIs (same grouping, row order, iterations and
    seed), with the bootstrap distribution kept so any interval level can be read off."""
    def fn(g):
        v = vectors(g, arm_names, k, "primary")
        return {a: paired_diff(v[a], v[base], iters, seed) for a in arm_names if a != base}
    st = by_stratum(rows, dims, fn)
    return {a: {d: {val: per[a] for val, per in st[d].items()} for d in dims} for a in arm_names if a != base}


def gather(cfg: dict[str, Any], set_name: str | None = None) -> tuple[dict, dict, dict, dict[str, Any], dict]:
    """diffs (with bootstrap dists), strata CIs, context tokens, info and the per-question
    hit vectors from the score outputs, on ``set_name`` (default: the primary set)."""
    score = read_json(out_path(cfg, "score.json"))
    all_rows = read_jsonl(out_path(cfg, "per_question.jsonl"))
    set_name = set_name or score["primary_set"]
    rows = set_rows(all_rows, set_name)
    k = str(int(cfg["decide"]["k"]))
    base = baseline_arm(cfg)["name"]
    sc = cfg["score"]
    iters, seed = int(sc["bootstrap_iters"]), int(sc["bootstrap_seed"])
    names = [a["name"] for a in cfg_arms(cfg)]
    vec = {a: np.array([r["arms"][a][k]["primary"] for r in rows]) for a in names}
    diffs = {a: paired_diff(v, vec[base], iters, seed) for a, v in vec.items() if a != base}
    block = score["sets"][set_name]
    st = block["primary"][k]["strata"]
    dims = [d for d in sc["strata_dims"] if d in st]
    if (cfg["decide"].get("stratum_correction") or "none") == "bonferroni":
        strata = strata_from_rows(rows, names, base, int(k), dims, iters, seed)
        for a, per_dim in strata.items():  # same rows as score.json's strata, or refuse
            for d, vals in per_dim.items():
                want = {v: c["diff"].get(a, {}).get("n") for v, c in st[d].items()}
                got = {v: c["n"] for v, c in vals.items()}
                if want != got:
                    raise ValueError(f"per_question.jsonl and score.json disagree on stratum sizes ({a}, {d})")
    else:
        strata = {a: {d: {v: c["diff"].get(a, {}) for v, c in st[d].items()} for d in dims}
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
            "check_writer": _check_writer_summary(score.get("check_writer"), k),
            "noise_exclusion": score.get("noise_exclusion") or {"enabled": False},
            "sensitivity_set": score.get("sensitivity_set")}
    return diffs, strata, ctx, info, vec


def _check_writer_summary(cw: dict[str, Any] | None, k: str) -> dict[str, Any] | None:
    """Pooled primary diffs of the check-writer set at k (aggregates only)."""
    if not cw:
        return None
    blk = cw["sets"].get(cw.get("primary_set") or "all") or {}
    pooled = (blk.get("primary") or {}).get(k, {}).get("pooled") or {}
    return {"set": cw.get("primary_set"), "n": blk.get("n"), "diff": pooled.get("diff"),
            "hit": pooled.get("hit"), "refusals": cw.get("refusals")}


def sensitivity_block(sdec: dict[str, Any], s_info: dict[str, Any], decision: str) -> dict[str, Any]:
    """The rule's outcome on the sensitivity set, labelled as not the decision."""
    keep = ("pass", "fail_reasons", "point", "lo", "hi", "threshold", "p_holm", "stratum_failures")
    return {"label": SENSITIVITY_LABEL, "is_decision": False, "set": s_info["set"], "n": s_info["n"],
            "winner": sdec["winner"], "kept_baseline": sdec["kept_baseline"],
            "passing_order": sdec["passing_order"], "agrees_with_decision": sdec["winner"] == decision,
            "arms": {a: {k: r[k] for k in keep} | {"stratum_k": r["stratum_guard_detail"].get("k"),
                                                  "stratum_level": r["stratum_guard_detail"].get("level")}
                     for a, r in sdec["arms"].items()},
            "ladder": sdec.get("ladder")}


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
    want = bool(cfg["score"].get("exclude_noise_subset"))
    got = bool((info.get("noise_exclusion") or {}).get("enabled"))
    if want != got:
        out.append(f"config score.exclude_noise_subset={want} but score.json was computed with {got}: rerun score")
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
        f"Stratum guard correction: {dec.get('stratum_correction', 'none')}. Tie-break: {dec.get('tie_break', 'cheapest')}.",
    ]
    ne = info.get("noise_exclusion") or {}
    if ne.get("enabled"):
        lines.append(f"Noise subset excluded from the decision set: {ne['n_excluded_from_primary']} questions removed "
                     f"(noise subset {ne['n_noise_subset']}); decision n = {ne['n_primary']}, sensitivity n = "
                     f"{ne['n_sensitivity']} (see Sensitivity below).")
    if dec["kept_baseline"]:
        why = (" (nothing cleared its test; baseline kept)" if dec.get("no_arm_passed", True)
               else " (baseline kept by the paired ladder)")
    else:
        why = ""
    lines += ["", f"**Decision: `{dec['winner']}`**" + why, ""]
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
    lines += stratum_detail_lines(dec)
    lines += ladder_lines(dec.get("ladder"))
    lines += sensitivity_lines(dec.get("sensitivity"))
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


def stratum_detail_lines(dec: dict[str, Any]) -> list[str]:
    """Per arm: k, level and every tested stratum's interval (only with a correction)."""
    if dec.get("stratum_correction", "none") == "none":
        return []
    lines = ["", f"## Stratum guard ({dec['stratum_correction']})", "",
             "Per arm: k = strata tested; interval = two-sided (1 - 0.05/k) paired bootstrap; a stratum fails "
             f"when point < {dec.get('stratum_point')} AND upper < 0.", "",
             "| Arm | k | level | Dim | Value | n | point | lo | hi | fails |", "|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in dec["arms"].items():
        det = r.get("stratum_guard_detail") or {}
        for s in det.get("strata") or []:
            lines.append(f"| {name} | {det['k']} | {det['level']:.4f} | {s['dim']} | {s['value']} | {s['n']} | "
                         f"{_f(s['point'], 2, True)} | {_f(s['lo'], 2, True)} | {_f(s['hi'], 2, True)} | "
                         f"{'FAIL' if s['fails'] else 'ok'} |")
    return lines


def ladder_lines(ladder: dict[str, Any] | None, heading: str = "## Tie-break: paired ladder") -> list[str]:
    if not ladder:
        return []
    lines = ["", heading, "",
             f"Order (cheapest first, by {ladder['order_basis']}): " + " < ".join(f"`{n}`" for n in ladder["order"])
             + f". A candidate replaces the incumbent when the {ladder['level'] * 100:g}% paired bootstrap lower "
             "bound of (candidate - incumbent) on the primary metric is > 0."]
    if ladder["steps"]:
        lines += ["", "| Step | Pair (candidate - incumbent) | n | point | lo | hi | switched |",
                  "|---|---|---|---|---|---|---|"]
        for i, st in enumerate(ladder["steps"], 1):
            lines.append(f"| {i} | {st['pair']} | {st['n']} | {_f(st['point'], 2, True)} | {_f(st['lo'], 2, True)} | "
                         f"{_f(st['hi'], 2, True)} | {'yes' if st['switched'] else 'no'} |")
    else:
        lines.append("Single candidate: no step.")
    return lines


def sensitivity_lines(sens: dict[str, Any] | None) -> list[str]:
    if not sens:
        return []
    lines = ["", f"## Sensitivity ({sens['label']})", "",
             f"Question set `{sens['set']}`, n = {sens['n']}. Would choose: `{sens['winner']}`"
             + (" (same as the decision)." if sens["agrees_with_decision"] else " (DIFFERS from the decision)."), "",
             "| Arm | diff vs baseline [95% CI] | pass |", "|---|---|---|"]
    for name, r in sens["arms"].items():
        lines.append(f"| {name} | {_f(r['point'], 2, True)} [{_f(r['lo'], 2, True)}, {_f(r['hi'], 2, True)}] | "
                     f"{'PASS' if r['pass'] else 'fail: ' + ', '.join(r['fail_reasons'])} |")
    lines += ladder_lines(sens.get("ladder"), "### Sensitivity paired ladder (not the decision)")
    return lines


def _f(x: Any, places: int = 1, sign: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{places}f}" if sign else f"{x:.{places}f}"


def run(cfg: dict[str, Any], force_unchecked: bool = False) -> int:
    require(cfg, "out_dir")
    margins = margins_from(cfg)
    diffs, strata, ctx, info, vec = gather(cfg)
    problems = preflight(cfg, margins, info)
    if problems and not force_unchecked:
        for p in problems:
            log(f"decide: REFUSED: {p}")
        log("decide: fix the inputs, or rerun with --force-unchecked (recorded in decision.json)")
        return 2
    sc = cfg["score"]
    iters, seed = int(sc["bootstrap_iters"]), int(sc["bootstrap_seed"])
    arm_list, base = cfg_arms(cfg), baseline_arm(cfg)["name"]
    dec = evaluate(arm_list, base, diffs, strata, ctx, margins, cfg["decide"], vec, iters, seed)
    dec["sensitivity"] = None
    if info.get("sensitivity_set"):
        s_diffs, s_strata, s_ctx, s_info, s_vec = gather(cfg, info["sensitivity_set"])
        sdec = evaluate(arm_list, base, s_diffs, s_strata, s_ctx, margins, cfg["decide"], s_vec, iters, seed)
        dec["sensitivity"] = sensitivity_block(sdec, s_info, dec["winner"])
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
