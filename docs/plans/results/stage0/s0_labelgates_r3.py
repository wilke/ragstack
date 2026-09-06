"""Stage 0b' step 2, gates -- the machine label gates under the **revision-3** protocol.

Reads the two r3 label files written by `s0_label_r3.py` and produces
``artifacts/r3/gates-r3.json`` plus a markdown table. Per judge:

* **hallucinated-span rate** -- spans whose quoted words are NOWHERE in the document, over
  ALL spans the judge attempted (accepted + failed), so a pair that lost one span of three
  cannot flatter the gate. Wilson 95 % upper reported. Gate: <= 0.05.
* **self-consistency** -- 10 % of pairs re-presented with the units in a different
  (seeded) order; consistent iff char-span Jaccard >= 0.5, two "no localizable evidence"
  verdicts counting as consistent. Reported under **all three** readings of SS6.4 rule 4's
  "primary-set char-span Jaccard" (union of all sets / first set only / best-matching set
  pair), as Stage 0 reported them. Gate: >= 0.90 on the union reading.
* **document-level whether-agreement** -- the NEW r3 SS3.7 gate: on the same duplicated
  pairs, does the judge agree with itself about *whether* the document contains any
  localizable evidence? Stage 0 measured 0.871 under the index-primary protocol.
  Gate: >= 0.90.
* **"no localizable evidence" rate** -- descriptive, with its denominator.

Across judges:

* **kappa(scout-qwen)** at pair level on the binary evidence/none verdict (Cohen's kappa,
  with the confusion matrix printed so the reader can recompute it);
* **span-union Jaccard between judges** on the pairs both call positive -- mean, median and
  the fraction at or above 0.5, with n.

Then the r3 SS5 step 2 decision line: a judge passes iff self-consistency >= 0.90 AND
hallucinated-span <= 0.05 AND whether-agreement >= 0.90. If neither passes, the study
stops and this module says so.

**No human statistic is computed here.** kappa(human-human) and kappa(judge-human) require
the two-reader R-dev read (SS6.6.2) and remain ``PENDING-HUMAN``; no agent read substitutes
for one and none was performed.
"""
from __future__ import annotations

import argparse
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

import s0_common as C            # noqa: E402
import s0_math as M              # noqa: E402
from s0_score import jaccard     # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
R3 = C.WORK / "r3"
ART = HERE / "artifacts" / "r3"

JUDGES = ("scout", "qwen")
GATE_SC = 0.90
GATE_HALL = 0.05
GATE_WHETHER = 0.90


def union_of(sets) -> list[list[int]]:
    return [[sp["start"], sp["end"]] for s in sets for sp in s["spans"]]


def spans_of(one_set) -> list[list[int]]:
    return [[sp["start"], sp["end"]] for sp in one_set["spans"]]


def _consistent(a_sets, b_sets, reading: str) -> bool:
    """Jaccard >= 0.5 under one of the three defensible readings of SS6.4 rule 4."""
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
           "observed_agreement": round(po, 4),
           "expected_agreement": round(pe, 4),
           "a_positive_rate": round(pa, 4), "b_positive_rate": round(pb, 4)}
    if abs(1 - pe) < 1e-12:
        out["kappa"] = ("UNRESOLVED — chance agreement is 1.0 (one judge is degenerate); "
                        "kappa is undefined, not zero")
    else:
        out["kappa"] = round((po - pe) / (1 - pe), 4)
    # A rater that never uses one of the two classes makes kappa degenerate: pe collapses
    # onto po and kappa is 0 by construction, whatever the observed agreement. Say so,
    # rather than let a mechanical 0 be read as "no agreement beyond chance".
    if min(pa, pb) == 0.0 or max(pa, pb) == 1.0:
        out["degenerate_marginal"] = (
            f"one judge used only one class (scout positive rate {pa}, qwen {pb}); "
            f"kappa is 0 by construction here and carries no information — read the "
            f"observed agreement {round(po, 4)} and the confusion counts instead")
    return out


def load(judge: str, tag: str = "") -> list[dict]:
    p = R3 / f"labels-r3-{judge}{('-' + tag) if tag else ''}.jsonl"
    if not p.exists():
        return []
    recs = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    bad = {r["topic"] for r in recs} - set(C.DEV_TOPICS)
    assert not bad, f"{p} contains non-development topics: {sorted(bad)}"
    return recs


def per_judge(judge: str, labels: list[dict], manifest: dict | None) -> dict:
    # The gate's denominator is the PRIMARY presentation's spans (attempt + the one
    # re-prompt), exactly as Stage 0 computed it: `s0_labelgates.py` summed `vstats` and
    # Stage 0's labeler discarded the duplicate presentation's verification stats
    # entirely. The duplicate pass is aggregated separately below so it is reported and
    # not silently folded into a gate whose Stage 0 value it never entered.
    V: dict[str, int] = {}
    D: dict[str, int] = {}
    for r in labels:
        for k, v in (r.get("vstats") or {}).items():
            V[k] = V.get(k, 0) + v
        for k, v in (r.get("dup_vstats") or {}).items():
            D[k] = D.get(k, 0) + v
    attempted = V.get("spans_seen", 0)
    failed = V.get("hallucinated", 0)
    # which of the three quoted strings the judge failed to copy verbatim
    anchor: dict[str, int] = {}
    for r in labels:
        for p in r["problems"]:
            if p in ("quote_not_in_document", "last_quote_not_in_document",
                     "no_first_words"):
                anchor[p] = anchor.get(p, 0) + 1
    hl = failed / attempted if attempted else float("nan")
    hl_u = M.wilson(failed, attempted)[1] if attempted else 1.0

    dup = [r for r in labels if "dup_sets" in r]
    readings = {}
    for reading in ("union", "primary", "best_pair"):
        ok = [_consistent(r["sets"], r["dup_sets"], reading) for r in dup]
        readings[reading] = {
            "n_duplicated": len(ok), "consistent": sum(ok),
            "rate": round(sum(ok) / len(ok), 4) if ok else None}
    sc = readings["union"]["rate"]

    whether = [bool(r["sets"]) == bool(r["dup_sets"]) for r in dup]
    wa = sum(whether) / len(whether) if whether else None

    none_v = sum(1 for r in labels if not r["dropped"] and not r["sets"])
    drops = sum(1 for r in labels if r["dropped"])
    by_grade: dict[str, dict[str, int]] = {}
    for r in labels:
        d = by_grade.setdefault(str(r["grade"]), {"pairs": 0, "none": 0})
        d["pairs"] += 1
        if not r["dropped"] and not r["sets"]:
            d["none"] += 1

    # descriptive: abstract bias (SS6.6.6). Stage 0 found only 3 of 308 pairs had EVERY
    # span outside the abstract and the first body unit -- the stratum the human read most
    # needs is the one the labels barely populate. Re-measured here under quote-primary.
    pos = [r for r in labels if r["sets"]]
    deep = sum(1 for r in pos
               if all(sp["unit"] >= 2 for s in r["sets"] for sp in s["spans"]))
    abstract_only = sum(1 for r in pos
                        if all(sp["unit"] == 0 for s in r["sets"] for sp in s["spans"]))
    spans_per_pos = [sum(len(s["spans"]) for s in r["sets"]) for r in pos]
    sets_per_pos = [len(r["sets"]) for r in pos]

    passes = {
        "self_consistency": bool(sc is not None and sc >= GATE_SC),
        "hallucinated_span": bool(attempted and hl <= GATE_HALL),
        "whether_agreement": bool(wa is not None and wa >= GATE_WHETHER),
    }
    return {
        "judge": judge,
        "served_model": (manifest or {}).get("stats", {}).get("served_model"),
        "prompt_sha256": (manifest or {}).get("prompt_sha256"),
        "pairs": len(labels),
        "hallucinated_span_rate": {
            "failed_spans": failed, "attempted_spans": attempted,
            "rate": None if attempted == 0 else round(hl, 5),
            "wilson95_upper": round(hl_u, 5),
            "gate": "<= 0.05", "PASS": passes["hallucinated_span"],
            "by_anchor": anchor,
            "by_anchor_note": "which of the span's quoted strings failed to appear in the "
                              "document verbatim: `quote_not_in_document` is the first-ten-"
                              "words anchor, `last_quote_not_in_document` the last-ten-"
                              "words anchor",
            "definition": "spans whose quoted words are NOWHERE in the document, over all "
                          "spans attempted (SS6.4 rule 2). Under the quote-primary "
                          "protocol this is the only span-verification failure mode that "
                          "counts against the gate."},
        "self_consistency": {
            "readings": readings, "rate": sc, "gate": ">= 0.90",
            "PASS": passes["self_consistency"],
            "definition": "10 % of pairs re-presented at a different (seeded) unit order; "
                          "consistent iff char-span Jaccard >= 0.5; two empty verdicts "
                          "count as consistent. The gate is read on the union reading; "
                          "the other two are reported so the verdict is checker-"
                          "independent."},
        "whether_agreement": {
            "n_duplicated": len(whether), "agree": sum(whether),
            "rate": None if wa is None else round(wa, 4),
            "gate": ">= 0.90", "PASS": passes["whether_agreement"],
            "definition": "r3 SS3.7 gate 3: does the judge agree with itself about WHETHER "
                          "the document contains any localizable evidence, across the two "
                          "presentations? Stage 0 measured 0.871 under the index-primary "
                          "protocol."},
        "no_localizable_evidence": {
            "n": none_v, "denominator": len(labels),
            "rate": round(none_v / len(labels), 4) if labels else None,
            "gate": "descriptive — a legal verdict, not a failure"},
        "dropped_pairs": drops,
        "duplicate_presentation_spans": {
            "attempted": D.get("spans_seen", 0),
            "hallucinated": D.get("hallucinated", 0),
            "rate": (round(D["hallucinated"] / D["spans_seen"], 5)
                     if D.get("spans_seen") else None),
            "note": "the re-presented 10 % pass; reported separately because Stage 0's "
                    "gate denominator did not include it"},
        "hallucinated_span_rate_incl_duplicates": {
            "failed_spans": failed + D.get("hallucinated", 0),
            "attempted_spans": attempted + D.get("spans_seen", 0),
            "rate": (round((failed + D.get("hallucinated", 0))
                           / (attempted + D.get("spans_seen", 0)), 5)
                     if attempted + D.get("spans_seen", 0) else None),
            "note": "sensitivity reading only; the gate is read on the primary pass"},
        "spans_emitted": V.get("spans_emitted", 0),
        "split_across_units": V.get("split_across_units", 0),
        "unlocatable_spans": V.get("unlocatable", 0),
        "ambiguous_quotes": V.get("ambiguous_quote", 0),
        "spans_without_last_words": V.get("no_last_words", 0),
        "unit_title_landed": V.get("title_landed", 0),
        "unit_title_elsewhere": V.get("title_elsewhere", 0),
        "unit_title_not_a_unit": V.get("title_unknown", 0),
        "windowed_pairs": sum(1 for r in labels if r["windowed"]),
        "shape": {
            "positive_pairs": len(pos),
            "mean_sets_per_positive_pair":
                round(statistics.fmean(sets_per_pos), 3) if pos else None,
            "mean_spans_per_positive_pair":
                round(statistics.fmean(spans_per_pos), 3) if pos else None,
            "abstract_only_pairs": abstract_only,
            "deep_section_pairs": deep,
            "deep_section_definition":
                "every span of the pair sits in unit index >= 2, i.e. outside the abstract "
                "and the first body unit (SS6.6.6 abstract bias; Stage 0 found 3/308)"},
        "by_grade": by_grade,
        "cost": (manifest or {}).get("stats"),
        "wall_seconds": (manifest or {}).get("wall_seconds"),
        "ALL_THREE_GATES_PASS": all(passes.values()),
    }


def cross(a: list[dict], b: list[dict]) -> dict:
    ka = {(r["topic"], r["docno"]): r for r in a}
    kb = {(r["topic"], r["docno"]): r for r in b}
    keys = sorted(set(ka) & set(kb))
    va = [1 if ka[k]["sets"] else 0 for k in keys]
    vb = [1 if kb[k]["sets"] else 0 for k in keys]
    kap = cohen_kappa(va, vb)
    both = [k for k in keys if ka[k]["sets"] and kb[k]["sets"]]
    js = [jaccard(union_of(ka[k]["sets"]), union_of(kb[k]["sets"])) for k in both]

    # Jaccard conflates two very different failures: "B picked a SUBSET of what A picked"
    # (under-enumeration, which the rubric's E3 anticipates) and "they point at different
    # places" (real disagreement). Asymmetric coverage separates them: the fraction of each
    # judge's characters that the other judge's union also covers.
    def covered_frac(x, y):
        from s0_score import inter, merge_iv, total
        X, Y = merge_iv(x), merge_iv(y)
        tx = total(X)
        return total(inter(X, Y)) / tx if tx else None

    cov_a = [covered_frac(union_of(ka[k]["sets"]), union_of(kb[k]["sets"])) for k in both]
    cov_b = [covered_frac(union_of(kb[k]["sets"]), union_of(ka[k]["sets"])) for k in both]
    cov_a = [c for c in cov_a if c is not None]
    cov_b = [c for c in cov_b if c is not None]
    return {
        "asymmetric_coverage": {
            "scout_chars_also_covered_by_qwen":
                round(statistics.fmean(cov_a), 4) if cov_a else None,
            "qwen_chars_also_covered_by_scout":
                round(statistics.fmean(cov_b), 4) if cov_b else None,
            "n": len(cov_a),
            "definition": "mean over pairs both call positive of the fraction of one "
                          "judge's selected characters that lie inside the other's "
                          "selection. A high value one way and a low value the other is "
                          "under-enumeration; low both ways is genuine disagreement about "
                          "WHERE the evidence is."},
        "pair_level_binary_kappa": kap,
        "definition_kappa": "Cohen's kappa on the binary 'does this document contain "
                            "localizable evidence' verdict, over the pairs both judges "
                            "labeled. a = scout, b = qwen.",
        "span_union_jaccard_where_both_positive": {
            "n": len(js),
            "denominator_note": f"{len(js)} of {len(keys)} co-labeled pairs are positive "
                                f"for both judges",
            "mean": round(statistics.fmean(js), 4) if js else None,
            "median": round(statistics.median(js), 4) if js else None,
            "frac_at_or_above_0.5": round(sum(1 for x in js if x >= 0.5) / len(js), 4)
                                    if js else None},
        "co_labeled_pairs": len(keys),
    }


def markdown(out: dict) -> str:
    L = ["# Stage 0b' — machine label gates under the revision-3 protocol", "",
         "| gate | requirement | " + " | ".join(
             f"**{j}**" for j in JUDGES if j in out["judges"]) + " |",
         "|---|---|" + "---|" * sum(1 for j in JUDGES if j in out["judges"])]

    def row(label, req, fn):
        cells = [fn(out["judges"][j]) for j in JUDGES if j in out["judges"]]
        L.append(f"| {label} | {req} | " + " | ".join(cells) + " |")

    def verdict(ok):
        return "**PASS**" if ok else "**FAIL**"

    row("**self-consistency** (span-union Jaccard, 10 % re-presented)", "≥ 0.90",
        lambda d: (f"**{d['self_consistency']['rate']}** "
                   f"({d['self_consistency']['readings']['union']['consistent']}/"
                   f"{d['self_consistency']['readings']['union']['n_duplicated']}) "
                   f"{verdict(d['self_consistency']['PASS'])}"))
    row("  — reading: first set only", "reported",
        lambda d: str(d['self_consistency']['readings']['primary']['rate']))
    row("  — reading: best-matching set pair", "reported",
        lambda d: str(d['self_consistency']['readings']['best_pair']['rate']))
    row("**hallucinated-span rate**", "≤ 0.05",
        lambda d: (f"**{d['hallucinated_span_rate']['rate']}** "
                   f"({d['hallucinated_span_rate']['failed_spans']}/"
                   f"{d['hallucinated_span_rate']['attempted_spans']} spans; Wilson 95 % "
                   f"upper {d['hallucinated_span_rate']['wilson95_upper']}) "
                   f"{verdict(d['hallucinated_span_rate']['PASS'])}"))
    row("  — of which the *last*-ten-words anchor", "reported",
        lambda d: (f"{d['hallucinated_span_rate']['by_anchor'].get('last_quote_not_in_document', 0)}"
                   f" (first-words anchor: "
                   f"{d['hallucinated_span_rate']['by_anchor'].get('quote_not_in_document', 0)})"))
    row("**document-level whether-agreement** (new, r3 §3.7)", "≥ 0.90",
        lambda d: (f"**{d['whether_agreement']['rate']}** "
                   f"({d['whether_agreement']['agree']}/"
                   f"{d['whether_agreement']['n_duplicated']}) "
                   f"{verdict(d['whether_agreement']['PASS'])}"))
    row("“no localizable evidence” rate", "descriptive",
        lambda d: (f"{d['no_localizable_evidence']['rate']} "
                   f"({d['no_localizable_evidence']['n']}/"
                   f"{d['no_localizable_evidence']['denominator']} pairs)"))
    row("spans emitted / attempted", "descriptive",
        lambda d: f"{d['spans_emitted']} / {d['hallucinated_span_rate']['attempted_spans']}")
    row("spans split across a unit boundary", "descriptive",
        lambda d: str(d['split_across_units']))
    row("quotes ambiguous (>1 occurrence)", "descriptive",
        lambda d: str(d['ambiguous_quotes']))
    row("quote landed inside the unit whose title it named", "descriptive",
        lambda d: (f"{d['unit_title_landed']}/"
                   f"{d['unit_title_landed'] + d['unit_title_elsewhere']}"))
    row("pairs dropped (no verified span survived)", "descriptive",
        lambda d: f"{d['dropped_pairs']}/{d['pairs']}")
    row("mean evidence sets / spans per positive pair", "descriptive",
        lambda d: (f"{d['shape']['mean_sets_per_positive_pair']} / "
                   f"{d['shape']['mean_spans_per_positive_pair']} "
                   f"(n={d['shape']['positive_pairs']})"))
    row("pairs whose every span is in the abstract", "descriptive",
        lambda d: f"{d['shape']['abstract_only_pairs']}/{d['shape']['positive_pairs']}")
    row("deep-section pairs (every span outside abstract + first body unit)",
        "descriptive (Stage 0: 3/308)",
        lambda d: f"{d['shape']['deep_section_pairs']}/{d['shape']['positive_pairs']}")
    row("**ALL THREE GATES**", "conjunctive",
        lambda d: verdict(d['ALL_THREE_GATES_PASS']))

    x = out.get("cross_judge") or {}
    if x:
        k = x["pair_level_binary_kappa"]
        j = x["span_union_jaccard_where_both_positive"]
        L += ["", "## Cross-judge agreement (scout vs qwen)", "",
              "| statistic | value |", "|---|---|",
              f"| co-labeled pairs | {x['co_labeled_pairs']} |",
              f"| κ(scout–qwen), pair-level binary evidence/none | **{k['kappa']}** |",
              f"| observed / expected agreement | {k['observed_agreement']} / "
              f"{k['expected_agreement']} |",
              f"| confusion (both+ / both− / scout-only+ / qwen-only+) | "
              f"{k['both_positive']} / {k['both_negative']} / {k['a_only_positive']} / "
              f"{k['b_only_positive']} |",
              f"| span-union Jaccard where both positive | mean **{j['mean']}**, median "
              f"{j['median']}, ≥ 0.5 on {j['frac_at_or_above_0.5']} of {j['n']} pairs |",
              f"| asymmetric coverage (scout's chars inside qwen's / qwen's inside "
              f"scout's) | **{x['asymmetric_coverage']['scout_chars_also_covered_by_qwen']}"
              f"** / **{x['asymmetric_coverage']['qwen_chars_also_covered_by_scout']}** "
              f"(n={x['asymmetric_coverage']['n']}) |"]
        if k.get("degenerate_marginal"):
            L += ["", f"> **κ is degenerate here.** {k['degenerate_marginal']}"]

    L += ["", "## Decision — r3 §5 step 2", "", out["DECISION"], "",
          "*κ(human–human) and κ(judge–human) are `PENDING-HUMAN`: they require the "
          "two-reader R-dev read of §6.6.2. No agent read was substituted and none was "
          "performed.*"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="", help="read the '-<tag>' label files (e.g. smoke)")
    ap.add_argument("--outdir", default=str(ART))
    args = ap.parse_args()

    judges, mans = {}, {}
    for j in JUDGES:
        labels = load(j, args.tag)
        if not labels:
            continue
        mp = R3 / f"label-manifest-{j}{('-' + args.tag) if args.tag else ''}.json"
        man = json.loads(mp.read_text()) if mp.exists() else None
        mans[j] = man
        judges[j] = per_judge(j, labels, man)

    out = {"protocol": "SPEC-confirmation-run-r3.md §3.7 — quote-primary, two judges",
           "gates": {"self_consistency": ">= 0.90", "hallucinated_span": "<= 0.05",
                     "whether_agreement": ">= 0.90"},
           "dev_topics": C.DEV_TOPICS, "judges": judges}
    if len(judges) == 2:
        out["cross_judge"] = cross(load("scout", args.tag), load("qwen", args.tag))
    else:
        out["cross_judge"] = None
        out["cross_judge_note"] = ("UNRESOLVED — only "
                                   f"{sorted(judges)} produced labels; kappa(scout-qwen) "
                                   "needs both.")

    winners = [j for j, d in judges.items() if d["ALL_THREE_GATES_PASS"]]
    if not winners:
        out["DECISION"] = ("**NEITHER JUDGE PASSES — stop per r3 §5 step 2.** "
                           "The labeler is wrong, not unstable, and the protocol needs "
                           "redesign, not another run.")
    else:
        best = max(winners, key=lambda j: judges[j]["self_consistency"]["rate"])
        others = [j for j in winners if j != best]
        out["DECISION"] = (
            f"**{best.upper()} PASSES all three machine gates** (self-consistency "
            f"{judges[best]['self_consistency']['rate']} ≥ 0.90; hallucinated-span "
            f"{judges[best]['hallucinated_span_rate']['rate']} ≤ 0.05; whether-agreement "
            f"{judges[best]['whether_agreement']['rate']} ≥ 0.90) and is the primary "
            f"judge, chosen by the self-consistency gate and not by preference (r3 §3.7 "
            f"item 2)."
            + (f" Also passing: {', '.join(others)}." if others else ""))
    out["HUMAN_HALF"] = ("PENDING-HUMAN — kappa(human-human), kappa(judge-human), "
                         "positive-class agreement and the wrong-location / non-minimal / "
                         "missed-evidence rates require the two-reader R-dev read "
                         "(SS6.6.2). No agent read is a substitute and none was performed.")

    od = pathlib.Path(args.outdir)
    od.mkdir(parents=True, exist_ok=True)
    C.atomic_json(od / "gates-r3.json", out)
    md = markdown(out)
    (od / "gates-r3.md").write_text(md)
    C.atomic_json(R3 / "gates-r3.json", out)
    print(md)
    print(out["DECISION"])


if __name__ == "__main__":
    main()
