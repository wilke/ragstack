"""Glossary benchmark round 2: the found@10 analysis (prereg §5 M1, §7, §8).

Pure functions over per-record outcomes; no I/O except :func:`main`. The
statistics are **not** reimplemented here: every test, interval and Holm step is
:mod:`chunktrial_stats` (via :func:`deployed_docs_part4.contrast_test`, part 4's
wrapper), so round 2 runs exactly part 4's code.

Units. A *record* is one question with its gold chunk(s); a *paper* is the
exchangeable cluster (``<corpus>/<paper>``, so two corpora never share one). For
a contrast arm A − arm B, ``d = found_A − found_B ∈ {−1, 0, +1}`` per record.

* superiority (H1, H3, H4): the paper sign-flip test, one-sided ``> 0``;
* non-inferiority (H2): every ``d`` shifted by ``+δ``, the same test one-sided
  (:func:`chunktrial_stats.noninferiority_test`);
* intervals: the paper-cluster percentile bootstrap of the net share
  ``(gained − lost)/n`` at ``ci_level`` (0.90 two-sided = the one-sided 95% bound);
* Holm over the confirmatory family at ``alpha`` (a missing or gated p enters as
  1.0: the family size never shrinks silently; dropping a member is a deviation
  made in the config, not here);
* **GATE-NOT-EVALUABLE**: a contrast whose minimum attainable p exceeds ``alpha``
  (:func:`min_attainable_p`), or an arm/condition the runner marked not evaluable
  (depth read-back short, census refused). Never reported as a null;
* missing: a record whose retrieval call failed on either side is excluded from
  that contrast and counted; the sensitivity reading scores it not found.

Outcome per hypothesis: ``found`` (found@10 under a condition) or ``proxy``
(the wording-proxy pass of prereg §5 M2, read from a per-record file produced
by the proxy tool; this module only consumes it).
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_EVAL = Path(__file__).resolve().parent.parent
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import chunktrial_stats as cs  # noqa: E402
import deployed_docs_part4 as p4  # noqa: E402

GATE = "GATE-NOT-EVALUABLE"
ARMS = ["none", "rules", "scoutp", "qwen38p", "qwen36p", "frontierp", "qwen36p-shuf"]
CONDITIONS = ["base", "bm25", "norerank"]

#: prereg §2. [arm A, arm B, kind, outcome]
DEFAULT_HYPOTHESES: dict[str, list[str]] = {
    "H1": ["qwen36p", "rules", "superiority", "found"],
    "H2": ["qwen36p", "frontierp", "noninferiority", "found"],
    "H3": ["qwen36p", "qwen36p-shuf", "superiority", "found"],
    "H4": ["qwen36p", "rules", "superiority", "proxy"],
}
#: secondary contrasts outside the family (prereg §2), unadjusted
DEFAULT_SECONDARY: dict[str, list[str]] = {
    "frontierp-rules": ["frontierp", "rules", "superiority", "found"],
    "frontierp-qwen36p (two-sided)": ["frontierp", "qwen36p", "two-sided", "found"],
}

DEFAULT_ANALYSIS: dict[str, Any] = {
    "alpha": 0.05,
    "delta": 0.05,
    "n_perm": 10_000,
    "n_boot": 10_000,
    "ci_level": 0.90,
    "seed": 261101,
    "family_corpora": ["O", "A"],
    "family_strata": ["S1"],
    "family_condition": "base",
    "hypotheses": DEFAULT_HYPOTHESES,
    "secondary": DEFAULT_SECONDARY,
    "repeat_arm": "rules",
}


# --------------------------------------------------------------------------- helpers
def cluster_of(rec: Mapping[str, Any]) -> str:
    """The exchangeable unit: ``<corpus>/<paper>``."""
    return f"{rec['corpus']}/{rec['paper']}"


def min_attainable_p(n_nonzero: int, n_flips: int, exact_max: int = cs.EXACT_MAX_PAGES) -> float:
    """The smallest p the one-sided paper sign-flip test can return with ``k`` non-zero
    paper sums: exact enumeration gives ``2^-k`` (only the all-plus sign vector reaches
    ``T = Σ|S_j|``); Monte-Carlo can go no lower than ``1/(1 + n_flips)`` either.
    ``k = 0`` gives 1."""
    if n_nonzero <= 0:
        return 1.0
    exact = 2.0 ** -n_nonzero
    return exact if n_nonzero <= exact_max else max(exact, 1.0 / (1 + n_flips))


def mde_split(discordant: int, alpha: float) -> list[int] | None:
    """The gained/lost split a one-sided exact McNemar test at ``alpha`` would have
    needed with ``discordant`` discordant records: the smallest ``g`` with
    ``P(G ≥ g | D, ½) ≤ alpha``, as ``[g, D − g]``. ``None`` when even ``D/0``
    does not reach it. (McNemar ignores the paper clustering: this is the
    floor beside a null, not the test.)"""
    for g in range(discordant + 1):
        if cs.mcnemar_exact(g, discordant - g, "greater") <= alpha:
            return [g, discordant - g]
    return None


def paired(
    records: Sequence[Mapping[str, Any]],
    outcome: Mapping[str, Mapping[str, int | None]],
    arm_a: str,
    arm_b: str,
    missing: str = "exclude",
) -> tuple[list[str], list[int], list[int], dict[str, int]]:
    """Clusters and paired 0/1 outcomes for records ``records``.

    ``outcome[record_id][arm]`` is 1, 0 or ``None`` (call failed / not measured).
    A record absent from an arm entirely is a harness error (KeyError): every arm
    is run on every record. ``missing="exclude"`` drops a record with ``None`` on
    either side (counted); ``"as_not_found"`` scores it 0 (the sensitivity)."""
    pages: list[str] = []
    a: list[int] = []
    b: list[int] = []
    n_missing = 0
    for rec in records:
        rid = rec["record_id"]
        va, vb = outcome[rid][arm_a], outcome[rid][arm_b]
        if va is None or vb is None:
            n_missing += 1
            if missing == "exclude":
                continue
            va, vb = va or 0, vb or 0
        pages.append(cluster_of(rec))
        a.append(int(va))
        b.append(int(vb))
    return pages, a, b, {"excluded_missing" if missing == "exclude" else "scored_not_found": n_missing}


def contrast_result(
    pages: list[str], a: list[int], b: list[int], kind: str, an: Mapping[str, Any]
) -> dict[str, Any]:
    """One contrast with part 4's test (:func:`deployed_docs_part4.contrast_test`), plus
    the minimum attainable p, the gate and the MDE. ``kind``: ``superiority``
    (one-sided), ``two-sided`` (superiority read two-sided; secondary only) or
    ``noninferiority`` (shift by ``+delta``)."""
    seed = int(an["seed"])
    if kind == "two-sided":
        r = p4.contrast_test(pages, a, b, "superiority", dict(an), seed, alternative="two-sided")
        r["type"] = "superiority (two-sided)"
    else:
        r = p4.contrast_test(pages, a, b, kind, dict(an), seed)
    if not a:
        r.update(min_attainable_p=1.0, gate=GATE, gate_reason="no records in this contrast")
        return r
    k = int(r["n_pages_nonzero"])
    mp = min_attainable_p(k, int(an["n_perm"]))
    if kind == "two-sided":
        mp = min(1.0, 2 * mp)
    r["min_attainable_p"] = mp
    if mp > float(an["alpha"]):
        r["gate"] = GATE
        r["gate_reason"] = f"minimum attainable p {mp:.4g} > alpha {an['alpha']} ({k} non-zero papers)"
    r["discordant"] = int(r["gained"]) + int(r["lost"])
    return r


def _status(kind: str, significant: bool | None, gated: bool) -> str:
    if gated:
        return GATE
    if kind == "noninferiority":
        return "shown non-inferior" if significant else "not shown non-inferior"
    return "confirmed" if significant else "not confirmed"


def holm_family(tests: dict[str, dict[str, Any]], order: Sequence[str], alpha: float) -> dict[str, dict[str, Any]]:
    """Holm over ``order`` in place. A gated or missing p enters as 1.0. Adds ``p_holm``,
    ``significant``, ``status``, and the mid-p sensitivity (``p_holm_mid``,
    ``significant_mid``) — never decisive."""
    def p_of(h: str, key: str) -> float:
        t = tests[h]
        v = t.get(key)
        return 1.0 if (v is None or t.get("gate")) else float(v)

    hm = cs.holm([p_of(h, "p") for h in order], alpha)
    hmid = cs.holm([p_of(h, "p_mid") for h in order], alpha)
    m = len(order)
    for i, h in enumerate(order):
        t = tests[h]
        gated = bool(t.get("gate"))
        t["p_holm"] = hm["p_adj"][i]
        t["significant"] = bool(hm["reject"][i]) and not gated
        t["p_holm_mid"] = hmid["p_adj"][i]
        t["significant_mid"] = bool(hmid["reject"][i]) and not gated
        t["status"] = _status(t.get("type", "superiority"), t["significant"], gated)
        if not t["significant"] and not gated and t.get("type") == "superiority":
            t["mde_split_holm_worst"] = mde_split(int(t.get("discordant", 0)), alpha / m)
            t["mde_split_alpha"] = mde_split(int(t.get("discordant", 0)), alpha)
    return tests


def select(records: Sequence[Mapping[str, Any]], corpora: Sequence[str] | None = None,
           strata: Sequence[str] | None = None) -> list[Mapping[str, Any]]:
    return [r for r in records
            if (corpora is None or r["corpus"] in corpora) and (strata is None or r["stratum"] in strata)]


def outcome_table(
    per_record: Mapping[str, Mapping[str, Mapping[str, Any]]], kind: str, condition: str
) -> dict[str, dict[str, int | None]]:
    """``{record_id: {arm: 0/1/None}}`` for ``kind`` ``found`` (under ``condition``) or
    ``proxy``. ``per_record[rid][arm]`` holds ``{"found": {cond: 0/1/None},
    "proxy": 0/1/None}``."""
    out: dict[str, dict[str, int | None]] = {}
    for rid, arms in per_record.items():
        row: dict[str, int | None] = {}
        for arm, v in arms.items():
            if kind == "found":
                row[arm] = v.get("found", {}).get(condition)
            elif kind == "proxy":
                row[arm] = v.get("proxy")
            else:
                raise ValueError(f"unknown outcome {kind!r}")
        out[rid] = row
    return out


def run_hypotheses(
    records: Sequence[Mapping[str, Any]],
    per_record: Mapping[str, Mapping[str, Mapping[str, Any]]],
    hypotheses: Mapping[str, Sequence[str]],
    condition: str,
    an: Mapping[str, Any],
    gated_arms: Mapping[str, str] | None = None,
    missing: str = "exclude",
) -> dict[str, dict[str, Any]]:
    """Every hypothesis on ``records`` under ``condition``. ``gated_arms[f"{arm}|{cond}"]``
    names an arm/condition the runner could not evaluate: a contrast touching it is
    gated without being tested."""
    gated_arms = gated_arms or {}
    out: dict[str, dict[str, Any]] = {}
    for h, (arm_a, arm_b, kind, oc) in hypotheses.items():
        cond = condition if oc == "found" else "-"
        why = [gated_arms[f"{x}|{cond}"] for x in (arm_a, arm_b) if f"{x}|{cond}" in gated_arms]
        head = {"contrast": f"{arm_a} - {arm_b}", "outcome": oc, "condition": cond if oc == "found" else None}
        if why:
            out[h] = {**head, "type": kind, "gate": GATE, "gate_reason": "; ".join(why), "p": None, "n": 0}
            continue
        missing_arm = {x: sum(x not in per_record[r["record_id"]] for r in records) for x in (arm_a, arm_b)}
        missing_arm = {x: n for x, n in missing_arm.items() if n}
        if missing_arm:
            out[h] = {**head, "type": kind, "gate": GATE, "p": None, "n": 0,
                      "gate_reason": f"arm(s) not measured on every record: {missing_arm}"}
            continue
        table = outcome_table(per_record, oc, cond)
        pages, a, b, cnt = paired(records, table, arm_a, arm_b, missing)
        out[h] = {**head, **contrast_result(pages, a, b, kind, an), **cnt}
    return out


def found_rates(records: Sequence[Mapping[str, Any]], per_record: Mapping[str, Any],
                arms: Sequence[str], condition: str) -> dict[str, dict[str, Any]]:
    """found@10 per arm: ``{found, n, rate, failed}`` (failed calls excluded from n)."""
    out = {}
    for arm in arms:
        vals = [per_record[r["record_id"]].get(arm, {}).get("found", {}).get(condition) for r in records]
        n = sum(v is not None for v in vals)
        f = sum(int(v) for v in vals if v is not None)
        out[arm] = {"found": f, "n": n, "failed": len(vals) - n,
                    "rate": round(f / n, 4) if n else None}
    return out


def noise_floor(records: Sequence[Mapping[str, Any]], per_record: Mapping[str, Any],
                arm: str, repeat: str, condition: str) -> dict[str, Any] | None:
    """Run-to-run noise: discordance between an arm and its repeat run (prereg §5)."""
    if not records or not all(repeat in per_record[r["record_id"]] and arm in per_record[r["record_id"]]
                              for r in records):
        return None
    table = outcome_table(per_record, "found", condition)
    pages, a, b, cnt = paired(records, table, repeat, arm)
    g = sum(x > y for x, y in zip(a, b, strict=True))
    lo = sum(x < y for x, y in zip(a, b, strict=True))
    return {"arm": arm, "repeat": repeat, "condition": condition, "n": len(a),
            "discordant": g + lo, "repeat_gained": g, "repeat_lost": lo, **cnt}


def corpus_dependent(per_corpus: Mapping[str, Mapping[str, Any]]) -> bool:
    """A sign of the net share that differs between corpora (both non-zero)."""
    signs = {math.copysign(1, t["net_share"]) for t in per_corpus.values()
             if t.get("net_share") not in (None, 0) and not (isinstance(t.get("net_share"), float)
                                                              and math.isnan(t["net_share"]))}
    return len(signs) > 1


# --------------------------------------------------------------------------- power
def power_table(layout: Sequence[int], an: Mapping[str, Any], n_sims: int = 2000, n_flips: int = 2000,
                share_sd: float = 0.15, family_size: int = 4) -> dict[str, Any]:
    """Prereg §7 power grid with :func:`chunktrial_stats.simulate_power` on ``layout``
    (records per paper; the realised family layout or the planned 50 × 10).

    ``gain share`` s = gained/discordant (the prereg's parametrisation) maps to part 4's
    ``discordant_share = 2s − 1``; the between-paper spread of s (SD ``share_sd``) to a
    beta ICC ``sd² / (s(1 − s))``. Superiority at ``alpha/family_size`` (the Holm worst
    case); NI at the same level with the true net 0 or −0.02."""
    alpha = float(an["alpha"])
    seed = int(an["seed"])

    def icc(s: float) -> float:
        return min(0.99, share_sd ** 2 / (s * (1 - s))) if 0 < s < 1 else 0.0

    rows: list[dict[str, Any]] = []
    for disc in (0.05, 0.10, 0.17, 0.25):
        for s in (0.70, 0.85):
            r = cs.simulate_power(len(layout), list(layout), disc, 2 * s - 1, share_icc=icc(s), alpha=alpha,
                                  n_family=family_size, n_sims=n_sims, n_flips=n_flips, seed=seed)
            rows.append({"test": "superiority", "discordance": disc, "gain_share": s,
                         "power": r["power"], "mc_se": round(r["mc_se"], 4), "alpha_test": r["alpha_test"]})
        for delta, net in ((0.05, 0.0), (0.03, 0.0), (0.05, -0.02)):
            s = 0.5 + net / (2 * disc)
            if not 0 <= s <= 1:
                continue
            r = cs.simulate_power(len(layout), list(layout), disc, 2 * s - 1, share_icc=icc(s), alpha=alpha,
                                  n_family=family_size, delta=delta, n_sims=n_sims, n_flips=n_flips, seed=seed)
            rows.append({"test": "noninferiority", "discordance": disc, "delta": delta, "true_net": net,
                         "power": r["power"], "mc_se": round(r["mc_se"], 4), "alpha_test": r["alpha_test"]})
    return {"n_papers": len(layout), "n_records": int(sum(layout)), "share_sd": share_sd,
            "family_size": family_size, "n_sims": n_sims, "n_flips": n_flips, "seed": seed, "rows": rows}


# --------------------------------------------------------------------------- the analysis
def analyse(
    records: Sequence[Mapping[str, Any]],
    per_record: Mapping[str, Mapping[str, Mapping[str, Any]]],
    analysis: Mapping[str, Any] | None = None,
    gated_arms: Mapping[str, str] | None = None,
    arms: Sequence[str] | None = None,
    power: bool = True,
    power_sims: int = 2000,
) -> dict[str, Any]:
    """The round-2 found@10 analysis block.

    ``records``: ``[{record_id, corpus, paper, stratum}]`` (the accepted set).
    ``per_record[rid][arm]``: ``{"found": {condition: 0/1/None}, "proxy": 0/1/None}``;
    the repeat run of the repeat arm is the arm ``"<arm>#2"``."""
    an = {**DEFAULT_ANALYSIS, **(analysis or {})}
    hyps = an["hypotheses"]
    order = list(hyps)
    fam_cond = an["family_condition"]
    alpha = float(an["alpha"])
    arms = list(arms or sorted({a for v in per_record.values() for a in v}))
    fam = select(records, an["family_corpora"], an["family_strata"])

    family = holm_family(run_hypotheses(fam, per_record, hyps, fam_cond, an, gated_arms), order, alpha)
    sens = holm_family(run_hypotheses(fam, per_record, hyps, fam_cond, an, gated_arms, missing="as_not_found"),
                       order, alpha)

    def unadjusted(rs: Sequence[Mapping[str, Any]], cond: str, hs: Mapping[str, Sequence[str]]) -> dict[str, Any]:
        out = run_hypotheses(rs, per_record, hs, cond, an, gated_arms)
        for t in out.values():
            if t.get("p") is not None and not t.get("gate"):
                t["significant_unadjusted"] = float(t["p"]) <= alpha
        return out

    secondary: dict[str, Any] = {}
    found_hyps = {h: v for h, v in hyps.items() if v[3] == "found"}
    for cond in CONDITIONS:
        if cond != fam_cond:
            secondary[f"family-population|{cond}"] = unadjusted(fam, cond, found_hyps)
    per_corpus: dict[str, Any] = {}
    for c in an["family_corpora"]:
        per_corpus[c] = unadjusted(select(records, [c], an["family_strata"]), fam_cond, hyps)
    for h in order:
        per_corpus.setdefault("_corpus_dependent", {})[h] = corpus_dependent(
            {c: per_corpus[c][h] for c in an["family_corpora"]})
    secondary["per_corpus"] = per_corpus
    strata = sorted({r["stratum"] for r in records} - set(an["family_strata"]))
    secondary["other_strata"] = {s: unadjusted(select(records, an["family_corpora"], [s]), fam_cond, found_hyps)
                                 for s in strata}
    secondary["contrasts"] = unadjusted(fam, fam_cond, an["secondary"])
    other_sets = sorted({r["corpus"] for r in records} - set(an["family_corpora"]))
    secondary["other_sets"] = {c: unadjusted(select(records, [c], None), fam_cond, hyps) for c in other_sets}

    descriptives = {
        "found_at_10": {c: {cond: found_rates(select(records, [c], None), per_record, arms, cond)
                            for cond in CONDITIONS} for c in sorted({r["corpus"] for r in records})},
        "found_at_10_family": {cond: found_rates(fam, per_record, arms, cond) for cond in CONDITIONS},
        "noise_floor": {c: {cond: noise_floor(select(records, [c], None), per_record, an["repeat_arm"],
                                              f"{an['repeat_arm']}#2", cond) for cond in CONDITIONS}
                        for c in sorted({r["corpus"] for r in records})},
        "sizes": {"records": len(records), "family_records": len(fam),
                  "family_papers": len({cluster_of(r) for r in fam}),
                  "by_corpus_stratum": _count_by(records)},
        "gated_arms": dict(gated_arms or {}),
    }
    out: dict[str, Any] = {
        "schema": "glossbench-r2-found10-analysis/1",
        "analysis": dict(an),
        "test": "paper sign-flip permutation (standard p; exact when <= 20 non-zero papers), paper-cluster "
                "bootstrap percentile CI of the net share, Holm over the family, McNemar descriptive; "
                "H2 non-inferiority by shifting every d by +delta",
        "family": {"corpora": an["family_corpora"], "strata": an["family_strata"], "condition": fam_cond,
                   "order": order, "hypotheses": family},
        "family_missing_as_not_found": {"hypotheses": sens},
        "secondary": {"note": "outside the confirmatory family; unadjusted", **secondary},
        "descriptives": descriptives,
    }
    if power:
        layout = sorted(_count_clusters(fam).values()) or [10] * 50
        out["power"] = {"realised": power_table(layout, an, n_sims=power_sims, n_flips=power_sims,
                                                family_size=len(order)),
                        "planned_50x10": power_table([10] * 50, an, n_sims=power_sims, n_flips=power_sims,
                                                     family_size=len(order))}
    return out


def _count_clusters(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    c: dict[str, int] = defaultdict(int)
    for r in records:
        c[cluster_of(r)] += 1
    return dict(c)


def _count_by(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in records:
        out[r["corpus"]][r["stratum"]] += 1
    return {c: dict(v) for c, v in out.items()}


# =========================================================================== round 3
# The attachment rule is the arm (prereg round 3 §2, §7). Added beside round 2's
# analysis, which is unchanged: :func:`analyse` stays the frozen round-2 record's code.
#
# Each hypothesis carries its OWN population (strata) — H1 on M_num, H2 on V, H3 on
# V ∪ M_num — so the family is not one record set read under several contrasts:
# ``[arm A, arm B, kind, outcome, strata]``. ``kind`` ``two-sided`` is the sign-flip
# test read two-sided (H2); a confirmed two-sided member is reported with its sign.
ARMS_R3 = ["value", "mention", "mention-shuf", "none", "rules-value", "rules-mention"]
STRATA_R3 = ["V", "M_num"]
R3_HYPOTHESES: dict[str, list[Any]] = {
    "H1": ["mention", "value", "superiority", "found", ["M_num"]],
    "H2": ["mention", "value", "two-sided", "found", ["V"]],
    "H3": ["mention", "mention-shuf", "superiority", "found", ["V", "M_num"]],
}
#: secondary, outside F, unadjusted (prereg round 3 §2)
R3_SECONDARY: dict[str, list[Any]] = {
    "pooled mention-value (two-sided)": ["mention", "value", "two-sided", "found", ["V", "M_num"]],
    "mention-none": ["mention", "none", "superiority", "found", ["V", "M_num"]],
    "value-none": ["value", "none", "superiority", "found", ["V", "M_num"]],
    "rules-mention - rules-value (two-sided)": ["rules-mention", "rules-value", "two-sided", "found",
                                                ["V", "M_num"]],
    "mention - rules-mention (two-sided)": ["mention", "rules-mention", "two-sided", "found", ["V", "M_num"]],
    "value - rules-value (two-sided)": ["value", "rules-value", "two-sided", "found", ["V", "M_num"]],
}
R3_DEFAULT_ANALYSIS: dict[str, Any] = {
    "round": 3,
    "alpha": 0.05,
    "n_perm": 10_000,
    "n_boot": 10_000,
    "ci_level": 0.90,
    "seed": 261103,
    "family_corpora": ["O"],
    "family_condition": "base",
    "hypotheses": R3_HYPOTHESES,
    "secondary": R3_SECONDARY,
    "repeat_arm": "value",
    #: the record field that splits M_num into symbol-bound / incidental (drafter, §3.2)
    "split_field": "symbol_is_the_quantity",
}


def _r3_spec(spec: Sequence[Any]) -> tuple[str, str, str, str, list[str]]:
    if len(spec) != 5:
        raise ValueError(f"a round-3 hypothesis is [arm A, arm B, kind, outcome, strata], got {spec!r}")
    a, b, kind, oc, strata = spec
    if kind not in ("superiority", "two-sided"):
        raise ValueError(f"round-3 kind {kind!r}: superiority (one-sided) or two-sided")
    return str(a), str(b), str(kind), str(oc), [str(s) for s in strata]


def run_hypotheses_r3(
    records: Sequence[Mapping[str, Any]],
    per_record: Mapping[str, Mapping[str, Mapping[str, Any]]],
    hypotheses: Mapping[str, Sequence[Any]],
    condition: str,
    an: Mapping[str, Any],
    corpora: Sequence[str] | None,
    gated_arms: Mapping[str, str] | None = None,
    missing: str = "exclude",
) -> dict[str, dict[str, Any]]:
    """Each hypothesis on ITS strata of ``corpora`` (round 2's :func:`run_hypotheses`
    per member, so gating, missing-arm and missing-call rules are round 2's)."""
    out: dict[str, dict[str, Any]] = {}
    for h, spec in hypotheses.items():
        a, b, kind, oc, strata = _r3_spec(spec)
        sel = select(records, corpora, strata)
        t = run_hypotheses(sel, per_record, {h: [a, b, kind, oc]}, condition, an, gated_arms, missing)[h]
        t["strata"] = strata
        t["n_records_population"] = len(sel)
        if kind == "two-sided":
            t["type"] = "superiority (two-sided)"
        out[h] = t
    return out


def _sign(t: Mapping[str, Any]) -> str | None:
    ns = t.get("net_share")
    if ns is None or (isinstance(ns, float) and math.isnan(ns)) or ns == 0:
        return None
    return "A > B" if ns > 0 else "A < B"


def holm_family_r3(tests: dict[str, dict[str, Any]], order: Sequence[str], alpha: float) -> dict[str, dict[str, Any]]:
    """Round 2's :func:`holm_family` (gated or missing p enters as 1.0, the family never
    shrinks), plus the sign of every two-sided member and its floor beside a null."""
    holm_family(tests, order, alpha)
    m = len(order)
    for h in order:
        t = tests[h]
        if str(t.get("type", "")).endswith("(two-sided)"):
            t["sign"] = _sign(t) if t.get("significant") else None
            if not t["significant"] and not t.get("gate"):
                # the two-sided floor: each tail at alpha/2 (Holm worst case alpha/(2m))
                t["mde_split_holm_worst"] = mde_split(int(t.get("discordant", 0)), alpha / (2 * m))
                t["mde_split_alpha"] = mde_split(int(t.get("discordant", 0)), alpha / 2)
    return tests


def _truthy(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("yes", "no", "true", "false"):
        return v.strip().lower() in ("yes", "true")
    return None


def power_table_r3(layouts: Mapping[str, Sequence[int]], kinds: Mapping[str, str], an: Mapping[str, Any],
                   n_sims: int = 2000, share_sd: float = 0.15) -> dict[str, Any]:
    """Per member, :func:`chunktrial_stats.simulate_power` on its realised layout at
    alpha / family size (Holm worst case), one- or two-sided as the member is."""
    alpha, seed, m = float(an["alpha"]), int(an["seed"]), len(layouts)

    def icc(s: float) -> float:
        return min(0.99, share_sd ** 2 / (s * (1 - s))) if 0 < s < 1 else 0.0

    out: dict[str, Any] = {}
    for h, layout in layouts.items():
        alt = "two-sided" if kinds[h] == "two-sided" else "greater"
        rows = []
        for disc in (0.10, 0.17, 0.25):
            for s in (0.70, 0.85):
                if not layout:
                    continue
                r = cs.simulate_power(len(layout), list(layout), disc, 2 * s - 1, share_icc=icc(s), alpha=alpha,
                                      n_family=m, alternative=alt, n_sims=n_sims, n_flips=n_sims, seed=seed)
                rows.append({"discordance": disc, "gain_share": s, "power": r["power"],
                             "mc_se": round(r["mc_se"], 4), "alpha_test": r["alpha_test"]})
        out[h] = {"alternative": alt, "n_papers": len(layout), "n_records": int(sum(layout)), "rows": rows}
    return {"family_size": m, "share_sd": share_sd, "n_sims": n_sims, "seed": seed, "members": out}


def analyse_r3(
    records: Sequence[Mapping[str, Any]],
    per_record: Mapping[str, Mapping[str, Mapping[str, Any]]],
    analysis: Mapping[str, Any] | None = None,
    gated_arms: Mapping[str, str] | None = None,
    arms: Sequence[str] | None = None,
    power: bool = True,
    power_sims: int = 2000,
) -> dict[str, Any]:
    """The round-3 found@10 analysis: family F = H1–H3 on ``family_corpora`` (set O;
    ``["O", "P"]`` after the §3.5 promotion, with H1-P… named in ``hypotheses``),
    each on its own strata, Holm over F; secondary readings unadjusted. Same inputs
    as :func:`analyse`; the repeat run of the repeat arm is ``"<arm>#2"``."""
    an = {**R3_DEFAULT_ANALYSIS, **(analysis or {})}
    hyps = an["hypotheses"]
    order = list(hyps)
    corpora = list(an["family_corpora"])
    fam_cond = an["family_condition"]
    alpha = float(an["alpha"])
    arms = list(arms or sorted({a for v in per_record.values() for a in v}))

    def family_of(missing: str) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for h, spec in hyps.items():
            # a promoted member names its set as a 6th element: [A, B, kind, outcome, strata, set]
            cs_ = [spec[5]] if len(spec) == 6 else corpora
            out.update(run_hypotheses_r3(records, per_record, {h: spec[:5]}, fam_cond, an, cs_, gated_arms,
                                         missing))
        return holm_family_r3(out, order, alpha)

    family = family_of("exclude")
    sens = family_of("as_not_found")

    def unadjusted(cs_: Sequence[str] | None, cond: str, hs: Mapping[str, Sequence[Any]],
                   rs: Sequence[Mapping[str, Any]] = records) -> dict[str, Any]:
        out = run_hypotheses_r3(rs, per_record, {h: v[:5] for h, v in hs.items()}, cond, an, cs_, gated_arms)
        for t in out.values():
            if t.get("p") is not None and not t.get("gate"):
                t["significant_unadjusted"] = float(t["p"]) <= alpha
                if str(t.get("type", "")).endswith("(two-sided)"):
                    t["sign"] = _sign(t) if t["significant_unadjusted"] else None
        return out

    secondary: dict[str, Any] = {}
    base_hyps = {h: v for h, v in hyps.items() if len(v) == 5}
    for cond in CONDITIONS:
        if cond != fam_cond:
            secondary[f"family|{cond}"] = unadjusted(corpora, cond, base_hyps)
    other_sets = sorted({r["corpus"] for r in records} - set(corpora))
    secondary["other_sets"] = {c: {cond: unadjusted([c], cond, base_hyps) for cond in CONDITIONS}
                               for c in other_sets}
    secondary["contrasts"] = {c: unadjusted([c], fam_cond, an["secondary"])
                              for c in sorted({r["corpus"] for r in records})}
    # the M_num split by the drafter's symbol_is_the_quantity (recorded, never a reject)
    split: dict[str, Any] = {}
    h1 = hyps.get("H1")
    if h1 is not None:
        fld = an["split_field"]
        m_recs = select(records, corpora, _r3_spec(h1[:5])[4])
        if not any(fld in r for r in m_recs):
            split = {"note": f"no record carries {fld!r}"}
        else:
            for label, want in (("symbol-bound", True), ("incidental", False)):
                sub = [r for r in m_recs if _truthy(r.get(fld)) is want]
                split[label] = unadjusted(corpora, fam_cond, {"H1": h1[:5]}, sub)["H1"]
            split["unrecorded"] = sum(_truthy(r.get(fld)) is None for r in m_recs)
    secondary["m_num_split"] = split

    sets = sorted({r["corpus"] for r in records})
    descriptives = {
        "found_at_10": {c: {s: {cond: found_rates(select(records, [c], [s]), per_record, arms, cond)
                                for cond in CONDITIONS} for s in STRATA_R3} for c in sets},
        "noise_floor": {c: {cond: noise_floor(select(records, [c], None), per_record, an["repeat_arm"],
                                              f"{an['repeat_arm']}#2", cond) for cond in CONDITIONS}
                        for c in sets},
        "sizes": {"records": len(records), "by_corpus_stratum": _count_by(records),
                  "family_members": {h: t.get("n_records_population") for h, t in family.items()}},
        "gated_arms": dict(gated_arms or {}),
    }
    out: dict[str, Any] = {
        "schema": "glossbench-r3-found10-analysis/1",
        "analysis": dict(an),
        "test": "paper sign-flip permutation (standard p; exact when <= 20 non-zero papers; H2 two-sided), "
                "paper-cluster bootstrap percentile CI of the net share, Holm over the family (gated or "
                "missing members at p = 1.0), McNemar descriptive; each member on its own strata",
        "family": {"corpora": corpora, "condition": fam_cond, "order": order, "hypotheses": family},
        "family_missing_as_not_found": {"hypotheses": sens},
        "secondary": {"note": "outside the confirmatory family; unadjusted", **secondary},
        "descriptives": descriptives,
    }
    if power:
        layouts = {}
        for h, spec in hyps.items():
            cs_ = [spec[5]] if len(spec) == 6 else corpora
            sel = select(records, cs_, _r3_spec(spec[:5])[4])
            layouts[h] = sorted(_count_clusters(sel).values())
        out["power"] = {"realised": power_table_r3(layouts, {h: _r3_spec(v[:5])[2] for h, v in hyps.items()},
                                                   an, n_sims=power_sims)}
    return out
