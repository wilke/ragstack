"""The decision sequence of PREREG §4.1, the §4.5 sentences, H3 (§4.3) and the secondary
Holm family (§4.4), as pure functions on numbers.

Nothing here reads a file. Every interval is a dict ``{"point", "lo", "hi", "n"}`` in hit@5
points (the harness's ``stats.paired_diff`` output without ``_dist``). ``order`` is ``"A"``
(Addendum C's convention: a tie deploys ft512o64s) or ``"B"`` (Addendum B's within-class
rule, the owner's ruling: a tie deploys S unless the frozen ladder finds ft512o64s superior).

The one entry point a caller needs is :func:`decide_both_orders`: it applies §4.1 under the
frozen order and under the other one, and renders the §4.5 sentence whose closing clause
("Under cost order <other> the decision would be <X>") is the computed result of the other
order on the same numbers, never fixed text.
"""
from __future__ import annotations

from typing import Any

#: the frozen margin (Addendum B; owner Q2), hit@5 points
M_NI = 2.0
#: the next class's margin, applied when the ctx@5 rule raises the threshold (§3)
M_MID = 2.0
#: ctx@5(S) / ctx@5(ft512o64) above this moves H1 to the +M_MID threshold (§3)
CTX_RATIO = 1.5
#: the stratum guard (§4.1): a stratum fails on point < STRATUM_POINT and corrected upper < 0
STRATUM_POINT = -2.0
MIN_STRATUM_N = 30
STRATUM_ALPHA = 0.05
ORDERS = ("A", "B")
OTHER = {"A": "B", "B": "A"}

S, INC, BASE, SREGEX, SENT = "S", "ft512o64s", "ft512o64", "S-regex", "sent512s"
GATE_NOT_EVALUABLE = "GATE-NOT-EVALUABLE"

#: the §4.5 rows, by number
ROWS = {
    1: "S (H2 holds)",
    2: "ft512o64s (tie, Order A)",
    3: "S (tie, Order B)",
    4: "ft512o64s (ladder, Order B)",
    5: "ft512o64s (S not NI, margin)",
    6: "ft512o64s (ctx rule)",
    7: "ft512o64s (stratum guard only)",
    8: GATE_NOT_EVALUABLE,
}


class RuleError(ValueError):
    pass


def _iv(d: dict[str, Any], name: str) -> dict[str, Any]:
    for k in ("point", "lo", "hi"):
        if d.get(k) is None:
            raise RuleError(f"{name}: interval has no {k}")
    return d


def stratum_failures(tested: list[dict[str, Any]], point_limit: float = STRATUM_POINT) -> list[dict[str, Any]]:
    """The strata that fail the guard among ``tested`` (each ``{dim, value, n, point, lo, hi}``
    with ``hi`` already at the two-sided 1 - 0.05/k level, k = len(tested)): point < limit and
    hi < 0. The caller has already dropped strata with n < MIN_STRATUM_N."""
    out = []
    for s in tested:
        if s.get("point") is None or s.get("hi") is None:
            raise RuleError(f"stratum {s.get('dim')}={s.get('value')}: no point/hi")
        if s["point"] < point_limit and s["hi"] < 0:
            out.append(dict(s))
    return out


def decide(order: str, *, gates: list[str], D: dict[str, Any], E: dict[str, Any] | None,
           ctx_ratio: float | None, strata_failed: list[dict[str, Any]], k_strata: int) -> dict[str, Any]:
    """§4.1 under ``order``. ``gates`` = the gate failures that void the decision (any → row 8).
    ``D`` = S − ft512o64s, ``E`` = ft512o64s − S (needed on the tie branch of Order B only),
    ``ctx_ratio`` = ctx@5(S)/ctx@5(ft512o64), ``strata_failed`` = :func:`stratum_failures` on
    S − ft512o64s, ``k_strata`` = the number of strata the guard tested."""
    if order not in ORDERS:
        raise RuleError(f"order must be A or B, not {order!r}")
    out: dict[str, Any] = {"order": order, "gates": list(gates), "k_strata": k_strata,
                           "strata_failed": [dict(s) for s in strata_failed]}
    if gates:
        out.update(decision=GATE_NOT_EVALUABLE, row=8, outcome="gate", H1=None, H2=None)
        return out
    _iv(D, "D")
    ctx_raised = ctx_ratio is not None and ctx_ratio > CTX_RATIO
    threshold = M_MID if ctx_raised else -M_NI
    pooled_ok = D["lo"] >= threshold
    h1 = pooled_ok and not strata_failed
    out.update(ctx_ratio=ctx_ratio, ctx_raised=ctx_raised, h1_threshold=threshold, pooled_ok=pooled_ok,
               stratum_ok=not strata_failed, H1=h1)
    if not h1:
        if not pooled_ok:
            row = 6 if ctx_raised else 5
        else:
            row = 7
        out.update(decision=INC, row=row, outcome="S not shown non-inferior", H2=None)
        return out
    h2 = D["lo"] > 0
    out["H2"] = h2
    if h2:
        out.update(decision=S, row=1, outcome="S superior")
        return out
    if order == "A":
        out.update(decision=INC, row=2, outcome="tie")
        return out
    if E is None:
        raise RuleError("Order B on a tie needs E = ft512o64s − S")
    _iv(E, "E")
    if E["lo"] > 0:
        out.update(decision=INC, row=4, outcome="tie, ladder switched")
    else:
        out.update(decision=S, row=3, outcome="tie")
    return out


def _f(x: float | None, places: int = 2, sign: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{places}f}" if sign else f"{x:.{places}f}"


def _ivs(iv: dict[str, Any]) -> str:
    return f"{_f(iv['point'], 2, True)} [{_f(iv['lo'], 2, True)}, {_f(iv['hi'], 2, True)}]"


def _strata_clause(failed: list[dict[str, Any]], k: int) -> str:
    parts = [f"{s['dim']} = {s['value']}, point {_f(s['point'], 2, True)}, upper bound "
             f"{_f(s['hi'], 2, True)} at 1 − 0.05/{k}" for s in failed]
    return "; ".join(parts)


def sentence(res: dict[str, Any], other: dict[str, Any], numbers: dict[str, Any]) -> str:
    """The §4.5 sentence for ``res`` (one order) with the computed clause for ``other``.

    ``numbers``: ``hit_S``, ``hit_inc`` (hit@5 %), ``n``, ``D``, ``E``, ``D1``, ``pi`` (the
    discordance of S vs ft512o64s), ``gates`` (list of strings, each "name: count") and
    ``setting`` (``"a"``)."""
    tail = f" Under cost order {other['order']} the decision would be {other['decision']}."
    if res["row"] == 8:
        gates = "; ".join(res["gates"]) or "(none listed)"
        return (f"{GATE_NOT_EVALUABLE}: {gates}. The contrasts were computed ({_counts(numbers)}) and "
                f"decide nothing; the server default stays as it is." + tail)
    counts = _counts(numbers)
    g1 = f" Gate G1: ft512o64s − ft512o64 = {_ivs(numbers['D1'])}."
    row = res["row"]
    strata = res["strata_failed"]
    k = res["k_strata"]
    if row == 1:
        body = "non-inferiority at 2.0 points holds and S is superior (lower bound > 0)."
    elif row == 2:
        body = ("S is non-inferior at 2.0 points but not shown superior; the tie goes to the cheaper arm "
                f"under cost order A (ft512o64s − S = {_ivs(numbers['E'])}).")
    elif row == 3:
        body = ("S is non-inferior at 2.0 points but not shown superior, and ft512o64s is not shown superior "
                f"to S (ft512o64s − S = {_ivs(numbers['E'])}); the tie goes to the cheaper arm under cost order B.")
    elif row == 4:
        body = ("S is non-inferior at 2.0 points but not shown superior, and ft512o64s is superior to S "
                f"(ft512o64s − S = {_ivs(numbers['E'])}, lower bound > 0; frozen ladder).")
    elif row == 5:
        body = "a loss of 2.0 points or more under S is not ruled out (this is not evidence that S is worse)"
        body += (f"; and S failed the stratum guard ({_strata_clause(strata, k)})." if strata else ".")
    elif row == 6:
        body = (f"S returns {_f(res['ctx_ratio'], 2)}× the baseline's context at k = 5 (> 1.5) and did not clear "
                "+2.0 points (context-token rule)")
        body += (f"; and S failed the stratum guard ({_strata_clause(strata, k)})." if strata else ".")
    elif row == 7:
        body = ("S is within 2.0 points of ft512o64s on the pooled set but failed the stratum guard "
                f"({_strata_clause(strata, k)}).")
    else:  # pragma: no cover
        raise RuleError(f"unknown row {row}")
    return f"Deploy {res['decision']}: {counts}; {body}{g1}{tail}"


def _counts(nb: dict[str, Any]) -> str:
    n = nb.get("n")
    n_s = f"{n:,}" if isinstance(n, int) else "n/a"
    return (f"in setting ({nb.get('setting', 'a')}), hit@5 was {_f(nb['hit_S'], 2)}% under S against "
            f"{_f(nb['hit_inc'], 2)}% under ft512o64s (n = {n_s}); S − ft512o64s = {_ivs(nb['D'])}, "
            f"discordance {_f(nb['pi'], 3)}")


def decide_both_orders(frozen_order: str, *, gates: list[str], D: dict[str, Any], E: dict[str, Any] | None,
                       D1: dict[str, Any], ctx_ratio: float | None, strata_failed: list[dict[str, Any]],
                       k_strata: int, hit_S: float, hit_inc: float, n: int, pi: float,
                       setting: str = "a") -> dict[str, Any]:
    """§4.1 under the frozen order and under the other, plus the §4.5 sentence."""
    kw = {"gates": gates, "D": D, "E": E, "ctx_ratio": ctx_ratio, "strata_failed": strata_failed,
          "k_strata": k_strata}
    res = decide(frozen_order, **kw)
    other = decide(OTHER[frozen_order], **kw)
    numbers = {"hit_S": hit_S, "hit_inc": hit_inc, "n": n, "D": D, "E": E, "D1": D1, "pi": pi, "setting": setting}
    return {"frozen_order": frozen_order, "decision": res["decision"], "row": res["row"], "row_label": ROWS[res["row"]],
            "outcome": res["outcome"], "H1": res["H1"], "H2": res["H2"], "result": res,
            "other_order": {"order": other["order"], "decision": other["decision"], "row": other["row"],
                            "row_label": ROWS[other["row"]]},
            "sentence": sentence(res, other, numbers)}


# ------------------------------------------------------------------- H3 (§4.3)


def h3(*, gates: list[str], D3: dict[str, Any] | None, strata_failed: list[dict[str, Any]], k_strata: int) -> dict[str, Any]:
    """lower(S-regex − S) ≥ −2.0 and the stratum guard → S may be deployed on any worker;
    otherwise (or a gate on S-regex) only where Punkt is asserted."""
    if gates:
        reason = "not evaluable: " + "; ".join(gates)
        return {"evaluable": False, "any_worker": False, "reason": reason, "gates": list(gates),
                "sentence": _h3_sentence(D3, False, reason)}
    if D3 is None:
        raise RuleError("H3 needs D3 = S-regex − S")
    _iv(D3, "D3")
    margin_ok = D3["lo"] >= -M_NI
    if margin_ok and not strata_failed:
        return {"evaluable": True, "any_worker": True, "reason": "margin and stratum guard hold",
                "margin_ok": True, "strata_failed": [], "k_strata": k_strata, "sentence": _h3_sentence(D3, True, None)}
    reason = "margin" if not margin_ok else "stratum"
    if not margin_ok and strata_failed:
        reason = "margin; stratum"
    detail = reason + (f" ({_strata_clause(strata_failed, k_strata)})" if strata_failed else "")
    return {"evaluable": True, "any_worker": False, "reason": detail, "margin_ok": margin_ok,
            "strata_failed": [dict(s) for s in strata_failed], "k_strata": k_strata,
            "sentence": _h3_sentence(D3, False, detail)}


def _h3_sentence(D3: dict[str, Any] | None, any_worker: bool, reason: str | None) -> str:
    d = _ivs(D3) if D3 and D3.get("point") is not None else "not computed"
    if any_worker:
        return f"Segmenter: S-regex − S = {d}; S may be deployed on any worker."
    return f"Segmenter: S-regex − S = {d}; S may be deployed only where Punkt is asserted (reason: {reason})."


# ------------------------------------------------------------------- §4.4 secondary contrasts

CONTRASTS = {"C1": (S, SENT), "C2": (S, BASE), "C3": (SREGEX, S), "C4": (INC, BASE)}
#: in setting (a) C3 is H3 and C4 is G1, so the Holm family there is C1 and C2 only
FAMILY = {"a": ("C1", "C2"), "d": ("C1", "C2", "C3", "C4"), "b": ("C1", "C2", "C3", "C4"),
          "c": ("C1", "C2", "C3", "C4")}


def holm_family(setting: str, pvals: dict[str, float], alpha: float = 0.05) -> dict[str, dict[str, Any]]:
    """Holm across the family of ``setting`` (§4.4); members not in ``pvals`` are skipped
    (reported as absent by the caller). Returns ``{C: {"p", "p_holm", "holm_sig"}}``."""
    from oa_chunk_eval.stats import holm_bonferroni  # numpy-backed; imported lazily

    fam = [c for c in FAMILY[setting] if c in pvals]
    if not fam:
        return {}
    res = holm_bonferroni({c: pvals[c] for c in fam}, alpha=alpha)
    return {c: {"p": round(pvals[c], 6), "p_holm": round(adj, 6), "holm_sig": bool(rej)} for c, (adj, rej) in res.items()}
