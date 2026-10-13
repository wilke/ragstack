#!/usr/bin/env python3
"""Layer C: the arithmetic of the five `derived` claims, other arithmetic the claims and
DRAFT.md state, and an audit of every numeral in DRAFT.md against the claim inventory.

Inputs are the COMMITTED claim values (claims.json) and the committed artifacts; nothing is
re-measured here, only re-derived. The numeral audit lists every number in the DRAFT body
(the reference list is excluded) that equals no claim value under the usual renderings
(rounding to the printed precision, thousands separators, percentages); those are checked
by hand in README.md.
"""
from __future__ import annotations

import json
import re

import vcommon as V


def leaves(x):
    if isinstance(x, bool) or x is None:
        return
    if isinstance(x, (int, float)):
        yield float(x)
    elif isinstance(x, dict):
        for v in x.values():
            yield from leaves(v)
    elif isinstance(x, list):
        for v in x:
            yield from leaves(v)
    elif isinstance(x, str):
        for m in re.findall(r"-?\d[\d,]*\.?\d*", x):
            try:
                yield float(m.replace(",", ""))
            except ValueError:
                pass


def main() -> None:
    prov = V.provenance()
    claims = {c["id"]: c for c in json.loads(V.CLAIMS.read_text())["claims"]}
    g = json.loads((V.ART / "r31ext" / "gates-r31ext.json").read_text())
    res: dict = {}

    # ---- the five derived claims
    def frac(km, n):
        return n / (km + n)
    for cid, n, judge in (("r31ext-scout-set-union-fraction-of-asymptote", 20, "scout"),
                          ("r31ext-qwen-set-union-fraction-of-asymptote", 10, "qwen")):
        v = claims[cid]["value"]
        km = v["set_saturation_incremental.mm_fit.Km"]
        vm = v["set_saturation_incremental.mm_fit.Vmax"]
        u = v[f"union_saturation.over_pairs_positive_somewhere.mean_distinct_sets_at_k.{n}"]
        res[cid] = {"stated": claims[cid]["derived"]["result"],
                    "recomputed": round(frac(km, n), 4),
                    "stated_alternative": claims[cid]["derived"]["alternative"]["result"],
                    "recomputed_alternative": round(u / vm, 4),
                    "check_inputs_same_curve": g["judges"][judge]["set_saturation_incremental"][
                        "mean_distinct_sets_at_k"][str(n)] == u}
    for cid in ("graded-support-implied-single-reading-pooled",
                "graded-support-implied-single-reading-scout",
                "graded-support-implied-single-reading-qwen"):
        v = claims[cid]["value"]
        out = {}
        for key, R in v.items():
            idx = int(key.split("]")[0][1:])
            src = (g["pooled"]["support_reliability"] if "pooled" in cid else
                   g["judges"]["scout" if "scout" in cid else "qwen"]["support_reliability"])
            k = src[idx]["n_readings"]
            out[str(k)] = round(V.sb_invert(R, k), 4)
        res[cid] = {"stated": claims[cid]["derived"]["result"], "recomputed": out}

    # ---- other arithmetic stated in claims / DRAFT
    arith = {}
    pe2 = claims["prereg-p-ext-2-fit-parameters"]["value"]
    vm, km = pe2["Vmax_prereg"], pe2["Km_prereg"]
    arith["projected_U20 = Vmax*20/(Km+20)"] = [pe2["projected_U20"], vm * 20 / (km + 20)]
    arith["projected_U20/Vmax = 20/(Km+20)"] = [pe2["projected_U20_as_fraction_of_Vmax"],
                                                20 / (km + 20)]
    arith["threshold_U20 = 0.9*Vmax"] = [pe2["threshold_U20"], 0.9 * vm]
    sc = claims["prereg-p-ext-2-scored"]["value"]
    arith["observed_over_prereg_Vmax = 133.8267/72.3277"] = [
        sc["observed_over_prereg_Vmax"], sc["observed_U20"] / vm]
    arith["relative_error = |133.8267-58.7603|/58.7603"] = [
        sc["secondary_projection_accuracy.relative_error"],
        abs(sc["observed_U20"] - pe2["projected_U20"]) / pe2["projected_U20"]]
    ex = claims["prereg-p-ext-2-excluding-blowups"]["value"]
    arith["relative_error_excluding = |58.6187-58.7603|/58.7603"] = [
        ex["secondary_projection_relative_error"],
        abs(ex["observed_U20_excluding_blowups"] - pe2["projected_U20"]) / pe2["projected_U20"]]
    for k in (10, 20, 30):
        x = claims[f"graded-support-reliability-at-{k}"]["value"]
        arith[f"SB(2) at {k}: 2r/(1+r), r={x['split_half_r']}"] = [
            x["spearman_brown_full_n"], V.spearman_brown(x["split_half_r"])]
    p1 = claims["prereg-p-ext-1-as-registered"]["value"]
    arith["P-ext-1 projected from reviewer r=0.556 at n=10 to 30 (m=6 on the half)"] = [
        p1["projected"], V.spearman_brown(0.556, 6)]
    arith["P-ext-1 projected from SB10=0.714 tripled"] = [p1["projected"],
                                                          V.spearman_brown(0.714, 3)]
    arith["PREDICTIONS.md: 0.705 tripled -> stated 0.877"] = [0.877, V.spearman_brown(0.705, 3)]
    arith["s0 index agreement 307/476"] = [claims["s0-span-index-agreement"]["value"], 307 / 476]
    arith["s0 whether 27/31"] = [0.871, 27 / 31]
    arith["claude total = sum of three"] = [195.32, 40.8278 + 93.1172 + 61.3743]
    arith["867 = 308+308+251"] = [867, 308 + 308 + 251]
    arith["112,140 = 3,738*20 + 3,738*10"] = [112140, 3738 * 20 + 3738 * 10]
    arith["74,760 = 3,738*20"] = [74760, 3738 * 20]
    arith["37,380 = 3,738*10"] = [37380, 3738 * 10]
    arith["3,738 = 2,962 + 776"] = [3738, 2962 + 776]
    arith["46.73 = 3,738/80"] = [46.73, 3738 / 80]
    arith["dev 308 = 208 + 100"] = [308, 208 + 100]
    arith["Qwen resumed: 33,238 + 4,142 = 37,380"] = [37380, 33238 + 4142]
    arith["filter dev Scout 82/6,160 = 1.33 %"] = [1.33, 100 * 82 / 6160]
    arith["filter conf Scout 1,050/74,760 = 1.40 %"] = [1.40, 100 * 1050 / 74760]
    arith["filter conf Qwen 1,653/9 = 'about 184 each'"] = [184, 1653 / 9]
    arith["span filter delta 0.9192-0.9205"] = [-0.0013, 0.9192 - 0.9205]
    arith["sentence refit Vmax within 5.5 % of prereg"] = [5.5, 100 * (72.3277 - 68.3302) / 72.3277]
    arith["refit 20 readings at 0.8118 => '19 % short'"] = [19, 100 * (1 - 0.8118)]
    arith["r3 scout closing-anchor share 54/58 = 93 %"] = [93, 100 * 54 / 58]
    arith["r31 scout closing-anchor share 6/22 = 27 %"] = [27, 100 * 6 / 22]
    arith["Scout share of pooled readings at 20 = 1/2, at 30 = 2/3"] = [
        [0.5, 2 / 3], [10 / 20, 20 / 30]]
    arith["lift sonnet 10.7843-2.8634"] = [7.9209, 10.7843 - 2.8634]
    arith["lift opus 9.1771-2.8634"] = [6.3137, 9.1771 - 2.8634]
    arith["lift fable 10.8024-5.3469"] = [5.4555, 10.8024 - 5.3469]
    arith["Sonnet ancillary Haiku $5.44 = 13.3 % of $40.83"] = [13.3, 100 * 5.44 / 40.8278]
    arith["Opus picks ~ twice Sonnet's (1,327 vs 626)"] = [2, 1327 / 626]
    arith["unfiltered degenerate slope 3810.7637/500.0409"] = [7.62, 3810.7637 / 500.0409]
    arith["pooled degenerate slope 2169.7085/500.0409"] = [4.3391, 2169.7085 / 500.0409]
    res["arithmetic"] = {k: {"stated": a, "recomputed": b} for k, (a, b) in arith.items()}

    # ---- numeral audit of DRAFT.md
    draft = (V.HERE.parent / "DRAFT.md").read_text()
    body = draft.split("## References")[0]
    body_nocode = re.sub(r"`[^`]*`", "", body)
    vals = set()
    for c in claims.values():
        for x in leaves(c["value"]):
            vals.add(x)
        if c.get("derived"):
            for x in leaves(c["derived"]):
                vals.add(x)

    def traceable(tok: str) -> bool:
        x = float(tok.replace(",", ""))
        dp = len(tok.split(".")[1]) if "." in tok else 0
        for v in vals:
            for cand in (v, 100 * v):
                if round(cand, dp) == round(x, dp) or abs(cand - x) < 1e-9:
                    return True
        return False

    found = []
    for m in re.finditer(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", body_nocode):
        tok = m.group(0).rstrip(",")
        if not tok or tok in {"-"}:
            continue
        line = body_nocode.count("\n", 0, m.start()) + 1
        ctx = body_nocode[max(0, m.start() - 50):m.end() + 30].replace("\n", " ")
        found.append({"token": tok, "line_in_code_stripped_body": line,
                      "traceable_to_a_claim_value": traceable(tok), "context": ctx})
    res["draft_numeral_audit"] = {
        "n_numerals": len(found),
        "n_untraceable": sum(not f["traceable_to_a_claim_value"] for f in found),
        "untraceable": [f for f in found if not f["traceable_to_a_claim_value"]]}
    V.write("recompute_derived", prov, {"results": res})


if __name__ == "__main__":
    main()
