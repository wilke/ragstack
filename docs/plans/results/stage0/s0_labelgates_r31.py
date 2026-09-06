"""Stage 0b' step 2, second attempt -- the gates and statistics for the **r3.1** relabel.

Reads the two label files written by `s0_label_r31.py` (five presentations per pair, per
judge) and produces ``artifacts/r31/gates-r31.json`` plus a rendered markdown table.

Per judge:

* **hallucinated-span rate** (gate <= 0.05) over the **primary presentation (k = 0)**, which
  is #501's denominator and keeps the two runs comparable; the all-presentations rate is
  reported beside it as a sensitivity reading. **Split by anchor** -- first-sentence quote
  vs last-sentence quote -- because that split is the direct test of r3 SS10 item 3's
  whole-sentence decision against #501's 54 of 58.
* **self-consistency** (gate >= 0.90), now measurable on every pair rather than on a 10 %
  duplicate draw. Reported as (i) the #501-comparable reading -- presentation 0 vs
  presentation 1, span-union Jaccard >= 0.5 -- over all pairs AND over the same 31 pairs
  #501 used; and (ii) the mean over all **10** presentation pairs, under each of SS6.4 rule
  4's three readings (union / first set only / best-matching set pair).
* **document-level whether-agreement** (gate >= 0.90) across the five presentations: the
  fraction of pairs on which all five agree evidence/none, and the mean pairwise agreement.
* **Union saturation** -- r3 SS3.7 item 6 / SS10 item 2. For k = 1..5, the number of
  DISTINCT evidence sets in the union of the first k presentations, "distinct" being D3
  rule 1's rule (two sets merge iff their span-union Jaccard >= 0.5, `C.JACCARD_MERGE`).
  Per-pair mean and the marginal gain at each k; the cross-judge union at k = 5; and the
  pooled curve. The saturation verdict is stated plainly: is the marginal gain at k = 5
  below 5 % of the union size, or not?
* **Enumeration proxy** (r3 SS3.7 item 5, standing in for enumeration recall against a
  human read that does not exist yet): asymmetric coverage between the judges' k = 5
  unions, and per judge the fraction of its own k = 5 union that presentation 0 alone
  recovered.

Across judges: Cohen's kappa on the binary evidence/none verdict and the span-union Jaccard
where both are positive, both at k = 0 (#501-comparable) and on the k = 5 unions.

Then the r3 SS3.7 decision line, and separately a **split-half stability** reading: if the
union of a judge's five presentations were itself the labeler, would it pass
self-consistency? (union of presentations 0-2 vs union of 3-4.)

**No human statistic is computed here.** kappa(human-human) and kappa(judge-human) require
the two-reader R-dev read (SS6.6.2) and remain ``PENDING-HUMAN``; no agent read substitutes
for one and none was performed. Absent statistics are called absent, never null.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import pathlib
import statistics
import sys

_HELPERS = pathlib.Path(os.environ.get(
    "STAGE0_HELPERS", "/home/wilke/Development/worktrees/phase0-rescue/phase0"))
for _p in (_HELPERS / "stage1", _HELPERS / "pilots"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

import s0_common as C                                    # noqa: E402
import s0_math as M                                      # noqa: E402
from s0_score import inter, jaccard, merge_iv, total     # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
R31 = C.WORK / "r31"
ART = HERE / "artifacts" / "r31"

JUDGES = ("scout", "qwen")
GATE_SC = 0.90
GATE_HALL = 0.05
GATE_WHETHER = 0.90
SATURATED_AT = 0.05        # "the marginal gain at k=5 is below 5 % of the union size"
N_PRES = 5

ANCHOR_FIRST = "quote_not_in_document"        # the first-SENTENCE quote failed to locate
ANCHOR_LAST = "last_quote_not_in_document"    # the last-SENTENCE quote failed to locate
ANCHOR_EMPTY = "no_first_words"


# ------------------------------------------------------------------ set algebra
def union_of(sets) -> list[list[int]]:
    return [[sp["start"], sp["end"]] for s in sets for sp in s["spans"]]


def spans_of(one_set) -> list[list[int]]:
    return [[sp["start"], sp["end"]] for sp in one_set["spans"]]


def distinct_union(setlists) -> list[list[list[int]]]:
    """D3 rule 1 accumulation: a set joins the union unless it merges with one already in.

    ``setlists`` is an ORDERED sequence (presentation 0 first). Two sets merge iff their
    span-union Jaccard is >= ``C.JACCARD_MERGE``; a set that merges with an accepted one
    contributes no new location. The accumulation is order-dependent by construction, and
    the order is stated (presentation 0 first, then 1, ...) rather than hidden.
    """
    acc: list[list[list[int]]] = []
    for sl in setlists:
        for s in sl:
            iv = spans_of(s)
            if all(jaccard(iv, a) < C.JACCARD_MERGE for a in acc):
                acc.append(iv)
    return acc


def covered_frac(x, y):
    """Fraction of x's characters that y also covers. ``None`` when x selects nothing."""
    X, Y = merge_iv(x), merge_iv(y)
    tx = total(X)
    return total(inter(X, Y)) / tx if tx else None


def _consistent(a_sets, b_sets, reading: str) -> bool:
    """SS6.4 rule 4 under one of its three defensible readings. Kept identical to r3's."""
    if not a_sets and not b_sets:
        return True                      # two "no localizable evidence" verdicts agree
    if not a_sets or not b_sets:
        return False
    if reading == "union":
        return jaccard(union_of(a_sets), union_of(b_sets)) >= C.JACCARD_MERGE
    if reading == "primary":
        return jaccard(spans_of(a_sets[0]), spans_of(b_sets[0])) >= C.JACCARD_MERGE
    best = max(jaccard(spans_of(x), spans_of(y)) for x in a_sets for y in b_sets)
    return best >= C.JACCARD_MERGE


def cohen_kappa(a: list[int], b: list[int]) -> dict:
    """Cohen's kappa on two binary readings of the same items, matrix included."""
    n = len(a)
    if n == 0:
        return {"n": 0, "kappa": "UNRESOLVED — no overlapping pairs"}
    n11 = sum(1 for x, y in zip(a, b) if x and y)
    n00 = sum(1 for x, y in zip(a, b) if not x and not y)
    n10 = sum(1 for x, y in zip(a, b) if x and not y)
    n01 = sum(1 for x, y in zip(a, b) if not x and y)
    po = (n11 + n00) / n
    pa, pb = (n11 + n10) / n, (n11 + n01) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    out = {"n": n, "both_positive": n11, "both_negative": n00,
           "a_only_positive": n10, "b_only_positive": n01,
           "observed_agreement": round(po, 4), "expected_agreement": round(pe, 4),
           "a_positive_rate": round(pa, 4), "b_positive_rate": round(pb, 4)}
    if abs(1 - pe) < 1e-12:
        out["kappa"] = ("UNRESOLVED — chance agreement is 1.0 (one judge is degenerate); "
                        "kappa is undefined, not zero")
    else:
        out["kappa"] = round((po - pe) / (1 - pe), 4)
    if min(pa, pb) == 0.0 or max(pa, pb) == 1.0:
        out["degenerate_marginal"] = (
            f"one judge used only one class (a positive rate {pa}, b {pb}); kappa is 0 by "
            f"construction here and carries no information — read the observed agreement "
            f"{round(po, 4)} and the confusion counts instead")
    return out


# ------------------------------------------------------------------ loading
def load(judge: str, tag: str = "") -> dict[tuple[str, str], dict[int, dict]]:
    p = R31 / f"labels-r31-{judge}{('-' + tag) if tag else ''}.jsonl"
    if not p.exists():
        return {}
    by: dict[tuple[str, str], dict[int, dict]] = {}
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        by.setdefault((r["topic"], r["docno"]), {})[r["presentation"]] = r
    bad = {t for t, _d in by} - set(C.DEV_TOPICS)
    assert not bad, f"{p} contains non-development topics: {sorted(bad)}"
    return by


def complete(by, n_pres: int) -> list[tuple[str, str]]:
    """Pairs with all ``n_pres`` presentations. Incomplete pairs are reported, not used."""
    return sorted(k for k, v in by.items() if all(j in v for j in range(n_pres)))


# ------------------------------------------------------------------ per judge
def per_judge(judge: str, by, manifest, n_pres: int) -> dict:
    keys = complete(by, n_pres)
    incomplete = sorted(set(by) - set(keys))
    dup31 = sorted(k for k in keys if by[k][0].get("in_501_duplicate_31"))

    # ---- hallucinated-span rate, primary presentation and all presentations
    def hall(pres_range) -> dict:
        V: dict[str, int] = {}
        anchor: dict[str, int] = {}
        for k in keys:
            for j in pres_range:
                r = by[k][j]
                for a, v in (r.get("vstats") or {}).items():
                    V[a] = V.get(a, 0) + v
                for p in r["problems"]:
                    if p in (ANCHOR_FIRST, ANCHOR_LAST, ANCHOR_EMPTY):
                        anchor[p] = anchor.get(p, 0) + 1
        att, fail = V.get("spans_seen", 0), V.get("hallucinated", 0)
        lo, up = M.wilson(fail, att) if att else (0.0, 1.0)
        return {"failed_spans": fail, "attempted_spans": att,
                "rate": None if att == 0 else round(fail / att, 5),
                "wilson95": [round(lo, 5), round(up, 5)],
                "wilson95_upper": round(up, 5),
                "by_anchor": {"first_sentence_quote": anchor.get(ANCHOR_FIRST, 0),
                              "last_sentence_quote": anchor.get(ANCHOR_LAST, 0),
                              "empty_first_quote": anchor.get(ANCHOR_EMPTY, 0)},
                "vstats": V}

    h0 = hall([0])
    hall_all = hall(range(n_pres))
    hl_pass = bool(h0["attempted_spans"] and h0["rate"] <= GATE_HALL)

    # ---- self-consistency
    def rate_over(ks, a, b, reading):
        ok = [_consistent(by[k][a]["sets"], by[k][b]["sets"], reading) for k in ks]
        return {"n": len(ok), "consistent": sum(ok),
                "rate": round(sum(ok) / len(ok), 4) if ok else None}

    reading_i = {r: {"all_pairs": rate_over(keys, 0, 1, r),
                     "the_501_31_pairs": rate_over(dup31, 0, 1, r)}
                 for r in ("union", "primary", "best_pair")}
    sc = reading_i["union"]["all_pairs"]["rate"]

    combos = list(itertools.combinations(range(n_pres), 2))
    reading_ii = {}
    for r in ("union", "primary", "best_pair"):
        per_combo = [rate_over(keys, a, b, r)["rate"] for a, b in combos]
        reading_ii[r] = {
            "n_presentation_pairs": len(combos), "n_pairs": len(keys),
            "mean_rate": round(statistics.fmean(per_combo), 4) if per_combo else None,
            "min_rate": min(per_combo) if per_combo else None,
            "max_rate": max(per_combo) if per_combo else None,
            "per_presentation_pair": {f"{a}v{b}": v
                                      for (a, b), v in zip(combos, per_combo)}}
    raw_j = []
    for a, b in combos:
        for k in keys:
            A, B = by[k][a]["sets"], by[k][b]["sets"]
            if A or B:
                raw_j.append(jaccard(union_of(A), union_of(B)))
    reading_ii["mean_pairwise_span_union_jaccard"] = {
        "mean": round(statistics.fmean(raw_j), 4) if raw_j else None,
        "median": round(statistics.median(raw_j), 4) if raw_j else None,
        "n": len(raw_j),
        "note": "raw Jaccard, not the >= 0.5 indicator; pairs where BOTH presentations "
                "returned nothing are excluded because their Jaccard is 0/0"}

    # ---- whether-agreement across the five presentations
    all5 = [len({bool(by[k][j]["sets"]) for j in range(n_pres)}) == 1 for k in keys]
    pw = [bool(by[k][a]["sets"]) == bool(by[k][b]["sets"])
          for a, b in combos for k in keys]
    wa = sum(all5) / len(all5) if all5 else None
    whether = {"n_pairs": len(all5), "all_five_agree": sum(all5),
               "rate": None if wa is None else round(wa, 4),
               "mean_pairwise": round(sum(pw) / len(pw), 4) if pw else None,
               "gate": ">= 0.90",
               "PASS": bool(wa is not None and wa >= GATE_WHETHER),
               "definition": "fraction of pairs on which all five presentations agree "
                             "about WHETHER the document contains localizable evidence"}

    # ---- union saturation
    unions = {k: [distinct_union([by[k][j]["sets"] for j in range(m)])
                  for m in range(1, n_pres + 1)] for k in keys}
    pos_keys = [k for k in keys if unions[k][-1]]          # positive somewhere in the five
    sat = saturation_curve(unions, keys, pos_keys, n_pres)

    # ---- enumeration proxy, the within-judge half
    k0_frac = []
    for k in pos_keys:
        n5 = len(unions[k][-1])
        n0 = len(distinct_union([by[k][0]["sets"]]))
        k0_frac.append(n0 / n5 if n5 else None)
    k0_frac = [x for x in k0_frac if x is not None]

    # ---- shape, descriptive
    pos0 = [k for k in keys if by[k][0]["sets"]]
    deep = sum(1 for k in pos0
               if all(sp["unit"] >= 2 for s in by[k][0]["sets"] for sp in s["spans"]))
    abstract_only = sum(1 for k in pos0
                        if all(sp["unit"] == 0 for s in by[k][0]["sets"]
                               for sp in s["spans"]))
    none0 = sum(1 for k in keys if not by[k][0]["dropped"] and not by[k][0]["sets"])
    drops0 = sum(1 for k in keys if by[k][0]["dropped"])
    V0 = h0["vstats"]

    passes = {"self_consistency": bool(sc is not None and sc >= GATE_SC),
              "hallucinated_span": hl_pass,
              "whether_agreement": whether["PASS"]}
    return {
        "judge": judge,
        "served_model": (manifest or {}).get("stats", {}).get("served_model"),
        "prompt_sha256": (manifest or {}).get("prompt_sha256"),
        "pairs_complete": len(keys), "presentations": n_pres,
        "records": sum(len(v) for v in by.values()),
        "pairs_incomplete": [list(k) for k in incomplete],
        "hallucinated_span_rate": {
            **h0, "gate": "<= 0.05", "PASS": hl_pass,
            "denominator": "presentation k = 0 only, as #501's gate was read",
            "by_anchor_note": "which quoted SENTENCE failed to locate under any of the "
                              "three ladders (exact / normalised / first-eight + "
                              "last-eight words in one sentence). #501, with ten-word "
                              "anchors, had 54 of Scout's 58 failures on the closing "
                              "anchor; this split is the direct test of r3 §10 item 3"},
        "hallucinated_span_rate_all_presentations": {
            **hall_all, "note": "sensitivity reading over all five presentations; the "
                                "gate is read on k = 0"},
        "locate_ladder": {
            "first_anchor": {m: V0.get("first_" + m, 0)
                             for m in ("exact", "normalised", "eight_word")},
            "last_anchor": {m: V0.get("last_" + m, 0)
                            for m in ("exact", "normalised", "eight_word")},
            "note": "k = 0 only. `eight_word` counts the quotes the whole-sentence match "
                    "missed and the first-eight/last-eight fallback rescued — spans that "
                    "would have been hallucinations under an exact-or-normalised-only rule"},
        "self_consistency": {
            "rate": sc, "gate": ">= 0.90", "PASS": passes["self_consistency"],
            "reading_i_presentation_0_vs_1": reading_i,
            "reading_ii_all_ten_presentation_pairs": reading_ii,
            "definition": "consistent iff span-union Jaccard >= 0.5 between the two "
                          "presentations' evidence sets; two 'no localizable evidence' "
                          "verdicts count as consistent. The gate is read on reading (i), "
                          "union, over ALL pairs — #501 could only read it on 31."},
        "whether_agreement": whether,
        "union_saturation": sat,
        "enumeration_proxy_within_judge": {
            "n_pairs": len(k0_frac),
            "mean_frac_of_k5_union_found_by_k0":
                round(statistics.fmean(k0_frac), 4) if k0_frac else None,
            "median": round(statistics.median(k0_frac), 4) if k0_frac else None,
            "frac_of_k0_sets_still_in_k5_union": 1.0,
            "frac_of_k0_sets_still_in_k5_union_note":
                "1.0 BY CONSTRUCTION, not by measurement: the union is accumulated in "
                "presentation order starting from k = 0, so every k = 0 set is a seed of "
                "it and cannot be absent. The informative direction is the one above — "
                "how much of the five-presentation union ONE presentation recovers.",
        },
        "no_localizable_evidence": {
            "n": none0, "denominator": len(keys),
            "rate": round(none0 / len(keys), 4) if keys else None,
            "presentation": 0,
            "gate": "descriptive — a legal verdict, not a failure"},
        "dropped_pairs_k0": drops0,
        "spans_emitted_k0": V0.get("spans_emitted", 0),
        "split_across_units_k0": V0.get("split_across_units", 0),
        "unlocatable_spans_k0": V0.get("unlocatable", 0),
        "ambiguous_quotes_k0": V0.get("ambiguous_quote", 0),
        "one_sentence_spans_k0": {
            "last_quote_equal_to_first": V0.get("last_same_as_first", 0),
            "last_quote_omitted": V0.get("no_last_words", 0)},
        "legacy_field_names_k0": V0.get("legacy_field_names", 0),
        "unit_title_landed_k0": V0.get("title_landed", 0),
        "unit_title_elsewhere_k0": V0.get("title_elsewhere", 0),
        "unit_title_not_a_unit_k0": V0.get("title_unknown", 0),
        "windowed_pairs_k0": sum(1 for k in keys if by[k][0]["windowed"]),
        "shape_k0": {
            "positive_pairs": len(pos0),
            "mean_sets_per_positive_pair":
                round(statistics.fmean([len(by[k][0]["sets"]) for k in pos0]), 3)
                if pos0 else None,
            "mean_spans_per_positive_pair":
                round(statistics.fmean([sum(len(s["spans"]) for s in by[k][0]["sets"])
                                        for k in pos0]), 3) if pos0 else None,
            "abstract_only_pairs": abstract_only, "deep_section_pairs": deep,
            "deep_section_definition":
                "every span of the pair sits in unit index >= 2, i.e. outside the abstract "
                "and the first body unit (SS6.6.6 abstract bias; Stage 0 found 3/308)"},
        "cost": (manifest or {}).get("stats"),
        "wall_seconds": (manifest or {}).get("wall_seconds"),
        "ALL_THREE_GATES_PASS": all(passes.values()),
    }


def saturation_curve(unions, keys, pos_keys, n_pres: int, label: str = "") -> dict:
    """Mean distinct-set count at k = 1..n, over all pairs and over positive-somewhere."""
    def curve(ks):
        means = [round(statistics.fmean([len(unions[k][m]) for k in ks]), 4)
                 if ks else None for m in range(n_pres)]
        gains = [None] + [
            (round((means[m] - means[m - 1]) / means[m], 4) if means[m] else None)
            for m in range(1, n_pres)]
        return {"n_pairs": len(ks),
                "mean_distinct_sets_at_k": {str(m + 1): means[m] for m in range(n_pres)},
                "marginal_gain_at_k": {str(m + 1): gains[m] for m in range(n_pres)}}

    allp, posp = curve(keys), curve(pos_keys)
    g5 = posp["marginal_gain_at_k"][str(n_pres)]
    if g5 is None:
        verdict = (f"UNRESOLVED — no pair carried a set, so the marginal gain at "
                   f"k = {n_pres} has no denominator")
    elif g5 < SATURATED_AT:
        verdict = (f"SATURATING — the marginal gain at k = {n_pres} is {g5:.4f}, below "
                   f"{SATURATED_AT:.2f} of the union size")
    else:
        verdict = (f"NOT SATURATING — the marginal gain at k = {n_pres} is {g5:.4f}, at "
                   f"or above {SATURATED_AT:.2f} of the union size; a fifth presentation "
                   f"is still adding locations")
    return {"label": label or "per judge",
            "over_all_pairs": allp, "over_pairs_positive_somewhere": posp,
            "distinct_rule": f"D3 rule 1 — two sets merge iff span-union Jaccard >= "
                             f"{C.JACCARD_MERGE}; accumulated in presentation order, "
                             f"k = 0 first",
            "marginal_gain_at_k5": g5,
            "saturation_threshold": SATURATED_AT,
            "VERDICT": verdict}


# ------------------------------------------------------------------ cross judge
def cross(A, B, n_pres: int) -> dict:
    keys = sorted(set(complete(A, n_pres)) & set(complete(B, n_pres)))
    out: dict = {"co_labeled_pairs": len(keys)}
    if not keys:
        out["note"] = "UNRESOLVED — no pair is complete for both judges"
        return out

    # --- k = 0, the #501-comparable reading
    va = [1 if A[k][0]["sets"] else 0 for k in keys]
    vb = [1 if B[k][0]["sets"] else 0 for k in keys]
    both0 = [k for k in keys if A[k][0]["sets"] and B[k][0]["sets"]]
    js0 = [jaccard(union_of(A[k][0]["sets"]), union_of(B[k][0]["sets"])) for k in both0]
    ca0 = [covered_frac(union_of(A[k][0]["sets"]), union_of(B[k][0]["sets"]))
           for k in both0]
    cb0 = [covered_frac(union_of(B[k][0]["sets"]), union_of(A[k][0]["sets"]))
           for k in both0]

    # --- k = 5 unions
    UA = {k: distinct_union([A[k][j]["sets"] for j in range(n_pres)]) for k in keys}
    UB = {k: distinct_union([B[k][j]["sets"] for j in range(n_pres)]) for k in keys}
    fa = {k: [iv for s in UA[k] for iv in s] for k in keys}
    fb = {k: [iv for s in UB[k] for iv in s] for k in keys}
    both5 = [k for k in keys if fa[k] and fb[k]]
    js5 = [jaccard(fa[k], fb[k]) for k in both5]
    ca5 = [covered_frac(fa[k], fb[k]) for k in both5]
    cb5 = [covered_frac(fb[k], fa[k]) for k in both5]

    # --- the cross-judge union and the pooled saturation curve
    pooled = {k: [distinct_union([A[k][j]["sets"] for j in range(m)]
                                 + [B[k][j]["sets"] for j in range(m)])
                  for m in range(1, n_pres + 1)] for k in keys}
    pooled_pos = [k for k in keys if pooled[k][-1]]
    cross5 = [len(distinct_union([A[k][j]["sets"] for j in range(n_pres)]
                                 + [B[k][j]["sets"] for j in range(n_pres)]))
              for k in keys]
    cross5_pos = [x for x in cross5 if x]

    def mean(x):
        return round(statistics.fmean(x), 4) if x else None

    out.update({
        "at_presentation_0": {
            "pair_level_binary_kappa": cohen_kappa(va, vb),
            "span_union_jaccard_where_both_positive": {
                "n": len(js0), "mean": mean(js0),
                "median": round(statistics.median(js0), 4) if js0 else None,
                "frac_at_or_above_0.5": (round(sum(1 for x in js0 if x >= 0.5) / len(js0), 4)
                                         if js0 else None)},
            "asymmetric_coverage": {
                "scout_chars_also_covered_by_qwen": mean([x for x in ca0 if x is not None]),
                "qwen_chars_also_covered_by_scout": mean([x for x in cb0 if x is not None]),
                "n": len(both0)}},
        "at_k5_unions": {
            "span_union_jaccard_where_both_positive": {
                "n": len(js5), "mean": mean(js5),
                "median": round(statistics.median(js5), 4) if js5 else None,
                "frac_at_or_above_0.5": (round(sum(1 for x in js5 if x >= 0.5) / len(js5), 4)
                                         if js5 else None)},
            "asymmetric_coverage": {
                "scout_chars_also_covered_by_qwen": mean([x for x in ca5 if x is not None]),
                "qwen_chars_also_covered_by_scout": mean([x for x in cb5 if x is not None]),
                "n": len(both5),
                "definition": "mean over pairs both call positive of the fraction of one "
                              "judge's selected characters that lie inside the other's. "
                              "High one way and low the other is under-enumeration; low "
                              "both ways is genuine disagreement about WHERE."},
            "mean_distinct_sets_in_the_cross_judge_union": {
                "over_all_pairs": mean(cross5), "n_all": len(cross5),
                "over_positive_pairs": mean(cross5_pos), "n_positive": len(cross5_pos)}},
        "pooled_saturation": saturation_curve(pooled, keys, pooled_pos, n_pres,
                                              label="pooled (scout ∪ qwen)"),
        "definition_kappa": "Cohen's kappa on the binary 'does this document contain "
                            "localizable evidence' verdict. a = scout, b = qwen.",
    })
    return out


def split_half(by, n_pres: int) -> dict:
    """Would the UNION of a judge's presentations pass self-consistency as a labeler?

    Split-half: the union of presentations 0-2 against the union of 3-4, consistent iff the
    span-union Jaccard of the two unions is >= 0.5 (two empty unions agreeing). This is not
    a like-for-like re-reading of the gate — the halves are 3 and 2 presentations deep, not
    1 and 1 — and it is reported as a stability statistic, not as the gate.
    """
    keys = complete(by, n_pres)
    if n_pres < 5:
        return {"n_pairs": len(keys), "rate": None, "consistent": None, "PASS": False,
                "ABSENT": f"not computed — the 0-2 vs 3-4 split needs five presentations "
                          f"and this run has {n_pres}. Absent, not zero."}
    ok, js = [], []
    for k in keys:
        a = [iv for s in distinct_union([by[k][j]["sets"] for j in (0, 1, 2)]) for iv in s]
        b = [iv for s in distinct_union([by[k][j]["sets"] for j in (3, 4)]) for iv in s]
        if not a and not b:
            ok.append(True)
            continue
        if not a or not b:
            ok.append(False)
            continue
        v = jaccard(a, b)
        js.append(v)
        ok.append(v >= C.JACCARD_MERGE)
    return {"n_pairs": len(ok), "consistent": sum(ok),
            "rate": round(sum(ok) / len(ok), 4) if ok else None,
            "mean_jaccard_where_both_positive":
                round(statistics.fmean(js), 4) if js else None,
            "gate_if_read_as_a_labeler": ">= 0.90",
            "PASS": bool(ok and sum(ok) / len(ok) >= GATE_SC),
            "definition": "union of presentations 0-2 vs union of 3-4, span-union Jaccard "
                          ">= 0.5. Reported because r3 §3.7 item 6 asks whether the UNION "
                          "is the stable statistic the study needs; the halves are 3 and 2 "
                          "presentations deep, so this is NOT the >= 0.90 gate re-read."}


# ------------------------------------------------------------------ rendering
def markdown(out: dict) -> str:
    js = [j for j in JUDGES if j in out["judges"]]
    npres = out["n_presentations"]
    L = [f"# Stage 0b′ r3.1 — machine label gates: whole-sentence anchors, "
         f"{npres} presentations", "",
         "| gate | requirement | " + " | ".join(f"**{j}**" for j in js) + " |",
         "|---|---|" + "---|" * len(js)]

    def row(label, req, fn):
        L.append(f"| {label} | {req} | "
                 + " | ".join(fn(out["judges"][j]) for j in js) + " |")

    def verdict(ok):
        return "**PASS**" if ok else "**FAIL**"

    row("**self-consistency** — reading (i), k=0 vs k=1, all pairs", "≥ 0.90",
        lambda d: (f"**{d['self_consistency']['rate']}** "
                   f"({d['self_consistency']['reading_i_presentation_0_vs_1']['union']['all_pairs']['consistent']}"
                   f"/{d['self_consistency']['reading_i_presentation_0_vs_1']['union']['all_pairs']['n']}) "
                   f"{verdict(d['self_consistency']['PASS'])}"))
    row("  — reading (i) on the same 31 pairs #501 used", "reported (#501: 0.645 / 0.419)",
        lambda d: (f"{d['self_consistency']['reading_i_presentation_0_vs_1']['union']['the_501_31_pairs']['rate']} "
                   f"({d['self_consistency']['reading_i_presentation_0_vs_1']['union']['the_501_31_pairs']['consistent']}"
                   f"/{d['self_consistency']['reading_i_presentation_0_vs_1']['union']['the_501_31_pairs']['n']})"))
    row("  — reading (i), first set only / best-matching set pair", "reported",
        lambda d: (f"{d['self_consistency']['reading_i_presentation_0_vs_1']['primary']['all_pairs']['rate']}"
                   f" / {d['self_consistency']['reading_i_presentation_0_vs_1']['best_pair']['all_pairs']['rate']}"))
    row("  — reading (ii), mean over all 10 presentation pairs (union)", "reported",
        lambda d: (f"{d['self_consistency']['reading_ii_all_ten_presentation_pairs']['union']['mean_rate']}"
                   f" (range {d['self_consistency']['reading_ii_all_ten_presentation_pairs']['union']['min_rate']}–"
                   f"{d['self_consistency']['reading_ii_all_ten_presentation_pairs']['union']['max_rate']})"))
    row("  — reading (ii), first set only / best-matching set pair", "reported",
        lambda d: (f"{d['self_consistency']['reading_ii_all_ten_presentation_pairs']['primary']['mean_rate']}"
                   f" / {d['self_consistency']['reading_ii_all_ten_presentation_pairs']['best_pair']['mean_rate']}"))
    row("  — mean pairwise span-union Jaccard (raw, not the indicator)", "reported",
        lambda d: (f"{d['self_consistency']['reading_ii_all_ten_presentation_pairs']['mean_pairwise_span_union_jaccard']['mean']}"
                   f" (median {d['self_consistency']['reading_ii_all_ten_presentation_pairs']['mean_pairwise_span_union_jaccard']['median']}, "
                   f"n={d['self_consistency']['reading_ii_all_ten_presentation_pairs']['mean_pairwise_span_union_jaccard']['n']})"))
    row("**hallucinated-span rate**, k = 0", "≤ 0.05",
        lambda d: (f"**{d['hallucinated_span_rate']['rate']}** "
                   f"({d['hallucinated_span_rate']['failed_spans']}/"
                   f"{d['hallucinated_span_rate']['attempted_spans']} spans; Wilson 95 % "
                   f"upper {d['hallucinated_span_rate']['wilson95_upper']}) "
                   f"{verdict(d['hallucinated_span_rate']['PASS'])}"))
    row("  — split by anchor (first-sentence / last-sentence quote)",
        "reported (#501: 4 / 54 for scout)",
        lambda d: (f"{d['hallucinated_span_rate']['by_anchor']['first_sentence_quote']} / "
                   f"{d['hallucinated_span_rate']['by_anchor']['last_sentence_quote']}"))
    row(f"  — all {npres} presentations (sensitivity)", "reported",
        lambda d: (f"{d['hallucinated_span_rate_all_presentations']['rate']} "
                   f"({d['hallucinated_span_rate_all_presentations']['failed_spans']}/"
                   f"{d['hallucinated_span_rate_all_presentations']['attempted_spans']}); "
                   f"anchors "
                   f"{d['hallucinated_span_rate_all_presentations']['by_anchor']['first_sentence_quote']}"
                   f" / {d['hallucinated_span_rate_all_presentations']['by_anchor']['last_sentence_quote']}"))
    row("  — quotes rescued by the eight-word ladder (first / last anchor)", "descriptive",
        lambda d: (f"{d['locate_ladder']['first_anchor']['eight_word']} / "
                   f"{d['locate_ladder']['last_anchor']['eight_word']}"))
    row(f"**document-level whether-agreement**, all {npres} presentations", "≥ 0.90",
        lambda d: (f"**{d['whether_agreement']['rate']}** "
                   f"({d['whether_agreement']['all_five_agree']}/"
                   f"{d['whether_agreement']['n_pairs']}) "
                   f"{verdict(d['whether_agreement']['PASS'])}"))
    row("  — mean pairwise whether-agreement", "reported",
        lambda d: str(d['whether_agreement']['mean_pairwise']))
    row("“no localizable evidence” rate, k = 0", "descriptive",
        lambda d: (f"{d['no_localizable_evidence']['rate']} "
                   f"({d['no_localizable_evidence']['n']}/"
                   f"{d['no_localizable_evidence']['denominator']} pairs)"))
    row("spans emitted / attempted, k = 0", "descriptive",
        lambda d: (f"{d['spans_emitted_k0']} / "
                   f"{d['hallucinated_span_rate']['attempted_spans']}"))
    row("spans split across a unit boundary, k = 0", "descriptive",
        lambda d: str(d['split_across_units_k0']))
    row("quotes ambiguous (>1 occurrence), k = 0", "descriptive",
        lambda d: str(d['ambiguous_quotes_k0']))
    row("quote landed inside the unit whose title it named, k = 0", "descriptive",
        lambda d: (f"{d['unit_title_landed_k0']}/"
                   f"{d['unit_title_landed_k0'] + d['unit_title_elsewhere_k0']}"))
    row("pairs dropped (no verified span survived), k = 0", "descriptive",
        lambda d: f"{d['dropped_pairs_k0']}/{d['pairs_complete']}")
    row("mean evidence sets / spans per positive pair, k = 0", "descriptive",
        lambda d: (f"{d['shape_k0']['mean_sets_per_positive_pair']} / "
                   f"{d['shape_k0']['mean_spans_per_positive_pair']} "
                   f"(n={d['shape_k0']['positive_pairs']})"))
    row("pairs whose every span is in the abstract, k = 0", "descriptive",
        lambda d: f"{d['shape_k0']['abstract_only_pairs']}/{d['shape_k0']['positive_pairs']}")
    row("deep-section pairs, k = 0", "descriptive (Stage 0: 3/308)",
        lambda d: f"{d['shape_k0']['deep_section_pairs']}/{d['shape_k0']['positive_pairs']}")
    row("**ALL THREE GATES**", "conjunctive",
        lambda d: verdict(d['ALL_THREE_GATES_PASS']))
    row("union of five presentations, split-half stability (0–2 vs 3–4)",
        "reported, not a gate",
        lambda d: (f"{d['split_half_union_stability']['rate']} "
                   f"({d['split_half_union_stability']['consistent']}/"
                   f"{d['split_half_union_stability']['n_pairs']})"))

    # --- saturation
    L += ["", "## Union saturation — distinct evidence sets in the union of the first k "
              "presentations", "",
          "Distinct = not merged by D3 rule 1 (span-union Jaccard ≥ "
          f"{C.JACCARD_MERGE}); accumulated in presentation order, k = 0 first. "
          "Denominator: pairs that carry at least one set somewhere in the "
          f"{npres} presentations.", "",
          "| union | n pairs | "
          + " | ".join(f"k={m}" for m in range(1, npres + 1))
          + f" | marginal gain at k={npres} |",
          "|---|---|" + "---|" * (npres + 1)]

    def sat_row(name, sat):
        p = sat["over_pairs_positive_somewhere"]
        cells = " | ".join(str(p["mean_distinct_sets_at_k"][str(m)])
                           for m in range(1, npres + 1))
        g = sat["marginal_gain_at_k5"]
        L.append(f"| {name} | {p['n_pairs']} | {cells} | "
                 f"{'—' if g is None else f'{g:.4f}'} |")

    for j in js:
        sat_row(j, out["judges"][j]["union_saturation"])
    if out.get("cross_judge") and out["cross_judge"].get("pooled_saturation"):
        sat_row("pooled (scout ∪ qwen)", out["cross_judge"]["pooled_saturation"])
    L.append("")
    for j in js:
        L.append(f"* **{j}** — {out['judges'][j]['union_saturation']['VERDICT']}")
    if out.get("cross_judge") and out["cross_judge"].get("pooled_saturation"):
        L.append(f"* **pooled** — {out['cross_judge']['pooled_saturation']['VERDICT']}")

    # --- enumeration proxy
    L += ["", "## Enumeration proxy (r3 §3.7 item 5 — a proxy; enumeration recall needs "
              "the human read)", "", "| statistic | value |", "|---|---|"]
    kk = f"k = {npres}"
    x = out.get("cross_judge") or {}
    if x and "at_k5_unions" in x:
        a5 = x["at_k5_unions"]["asymmetric_coverage"]
        a0 = x["at_presentation_0"]["asymmetric_coverage"]
        L += [f"| asymmetric coverage on the {kk} unions (scout's chars inside qwen's / "
              f"qwen's inside scout's) | **{a5['scout_chars_also_covered_by_qwen']}** / "
              f"**{a5['qwen_chars_also_covered_by_scout']}** (n={a5['n']}) |",
              f"| the same at k = 0 (#501: 0.1622 / 0.6592) | "
              f"{a0['scout_chars_also_covered_by_qwen']} / "
              f"{a0['qwen_chars_also_covered_by_scout']} (n={a0['n']}) |"]
    for j in js:
        e = out["judges"][j]["enumeration_proxy_within_judge"]
        L.append(f"| {j}: fraction of its own {kk} union that presentation 0 alone "
                 f"recovered | {e['mean_frac_of_k5_union_found_by_k0']} "
                 f"(median {e['median']}, n={e['n_pairs']}) |")

    # --- cross-judge
    if x and "at_presentation_0" in x:
        k = x["at_presentation_0"]["pair_level_binary_kappa"]
        j0 = x["at_presentation_0"]["span_union_jaccard_where_both_positive"]
        j5 = x["at_k5_unions"]["span_union_jaccard_where_both_positive"]
        u5 = x["at_k5_unions"]["mean_distinct_sets_in_the_cross_judge_union"]
        L += ["", "## Cross-judge agreement (scout vs qwen)", "",
              "| statistic | value |", "|---|---|",
              f"| co-labeled pairs (complete for both) | {x['co_labeled_pairs']} |",
              f"| κ(scout–qwen), pair-level binary evidence/none, k = 0 | "
              f"**{k['kappa']}** |",
              f"| observed / expected agreement | {k['observed_agreement']} / "
              f"{k['expected_agreement']} |",
              f"| confusion (both+ / both− / scout-only+ / qwen-only+) | "
              f"{k['both_positive']} / {k['both_negative']} / {k['a_only_positive']} / "
              f"{k['b_only_positive']} |",
              f"| span-union Jaccard where both positive, k = 0 | mean **{j0['mean']}**, "
              f"median {j0['median']}, ≥ 0.5 on {j0['frac_at_or_above_0.5']} of {j0['n']} |",
              f"| span-union Jaccard between the k = {npres} unions | mean **{j5['mean']}**, "
              f"median {j5['median']}, ≥ 0.5 on {j5['frac_at_or_above_0.5']} of {j5['n']} |",
              f"| distinct sets in the cross-judge (scout ∪ qwen) union at k = {npres} | "
              f"**{u5['over_positive_pairs']}** per positive pair (n={u5['n_positive']}) |"]
        if k.get("degenerate_marginal"):
            L += ["", f"> **κ is degenerate here.** {k['degenerate_marginal']}"]

    L += ["", "## Decision — r3 §3.7 / §5 step 2", "", out["DECISION"], "",
          out["UNION_AS_LABELER"], "",
          "*κ(human–human) and κ(judge–human) are `PENDING-HUMAN`: they require the "
          "two-reader R-dev read of §6.6.2. No agent read was substituted and none was "
          "performed. Enumeration recall against human-marked sets (r3 §3.7 item 5's "
          "actual gate) is **absent**, not zero — the proxy above stands in its place.*"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="", help="read the '-<tag>' label files (e.g. smoke)")
    ap.add_argument("--presentations", type=int, default=N_PRES)
    ap.add_argument("--outdir", default=str(ART))
    args = ap.parse_args()
    n = args.presentations

    loaded, judges = {}, {}
    for j in JUDGES:
        by = load(j, args.tag)
        if not by:
            continue
        loaded[j] = by
        mp = R31 / f"label-manifest-r31-{j}{('-' + args.tag) if args.tag else ''}.json"
        man = json.loads(mp.read_text()) if mp.exists() else None
        judges[j] = per_judge(j, by, man, n)
        judges[j]["split_half_union_stability"] = split_half(by, n)

    out = {"protocol": ("SPEC-confirmation-run-r3.md §3.7 item 1 (whole-sentence anchors, "
                        "§10 item 3) + item 6 / §10 item 2 (a) (five presentations per "
                        "pair, temperature 0, seeded unit orders)"),
           "gates": {"self_consistency": ">= 0.90 (reading (i), union, all pairs)",
                     "hallucinated_span": "<= 0.05 (presentation k = 0)",
                     "whether_agreement": ">= 0.90 (all five presentations agree)"},
           "n_presentations": n, "dev_topics": C.DEV_TOPICS, "judges": judges}

    if len(judges) == 2:
        out["cross_judge"] = cross(loaded["scout"], loaded["qwen"], n)
    else:
        out["cross_judge"] = None
        out["cross_judge_note"] = (f"UNRESOLVED — only {sorted(judges)} produced labels; "
                                   "κ(scout–qwen) needs both.")

    winners = [j for j, d in judges.items() if d["ALL_THREE_GATES_PASS"]]
    if not winners:
        lines = []
        for j, d in judges.items():
            lines.append(
                f"{j}: self-consistency {d['self_consistency']['rate']} "
                f"({'PASS' if d['self_consistency']['PASS'] else 'FAIL'}), "
                f"hallucinated {d['hallucinated_span_rate']['rate']} "
                f"({'PASS' if d['hallucinated_span_rate']['PASS'] else 'FAIL'}), "
                f"whether-agreement {d['whether_agreement']['rate']} "
                f"({'PASS' if d['whether_agreement']['PASS'] else 'FAIL'})")
        out["DECISION"] = ("**NEITHER JUDGE PASSES THE CONJUNCTION — stop stands per r3 §5 "
                           "step 2.** " + "; ".join(lines) + ".")
    else:
        best = max(winners, key=lambda j: judges[j]["self_consistency"]["rate"])
        others = [j for j in winners if j != best]
        d = judges[best]
        out["DECISION"] = (
            f"**{best.upper()} PASSES all three machine gates** (self-consistency "
            f"{d['self_consistency']['rate']} ≥ 0.90; hallucinated-span "
            f"{d['hallucinated_span_rate']['rate']} ≤ 0.05; whether-agreement "
            f"{d['whether_agreement']['rate']} ≥ 0.90) and is the primary judge, chosen by "
            f"the self-consistency gate and not by preference (r3 §3.7 item 2)."
            + (f" Also passing: {', '.join(others)}." if others else ""))

    uh = []
    for j, d in judges.items():
        s = d["split_half_union_stability"]
        uh.append(f"{j} {s['rate']} ({s['consistent']}/{s['n_pairs']}, "
                  f"{'≥' if s['PASS'] else '<'} 0.90)")
    out["UNION_AS_LABELER"] = (
        "**Read as a labeler, the union of five presentations is stable at:** "
        + "; ".join(uh) + ". This is the union of presentations 0–2 against the union of "
        "3–4 — halves of unequal depth, so it is a stability statistic and not the ≥ 0.90 "
        "gate re-read on a like-for-like pair.")
    out["HUMAN_HALF"] = ("PENDING-HUMAN — κ(human–human), κ(judge–human), positive-class "
                         "agreement, the wrong-location / non-minimal / missed-evidence "
                         "rates and r3 §3.7 item 5's enumeration recall all require the "
                         "two-reader R-dev read (§6.6.2). No agent read is a substitute "
                         "and none was performed.")

    od = pathlib.Path(args.outdir)
    od.mkdir(parents=True, exist_ok=True)
    C.atomic_json(od / "gates-r31.json", out)
    md = markdown(out)
    (od / "gates-r31.md").write_text(md)
    C.atomic_json(R31 / "gates-r31.json", out)
    print(md)
    print(out["DECISION"])


if __name__ == "__main__":
    main()
