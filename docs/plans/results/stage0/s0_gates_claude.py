"""Gates and cross-judge statistics for the **Claude judge family** (r3 SS10 item 4).

Reads the one-reading label files written by `s0_label_claude.py` and the r3.1 local
judges' five-reading files from ``work/r31/``, and writes ``artifacts/claude/gates-claude.json``
plus a rendered markdown table.

What is measured, and what is **not**:

* **Per judge** -- hallucinated-span rate (gate <= 0.05, Wilson 95 %) split by anchor
  (first-sentence quote vs last-sentence quote), the locator-ladder breakdown, "no
  localizable evidence" rate, evidence sets and spans per positive pair, median span
  length in sentences and in characters, pairs dropped, and the unit-title landing rate.
  The set algebra, the Wilson interval and Cohen's kappa are **imported** from
  `s0_labelgates_r31`, never re-declared, so the two families' numbers are computed by one
  implementation.
* **Self-consistency is ABSENT.** It needs two readings of the same pair and this run has
  one. It is reported as absent, not as 1.0 and not as null-with-a-number-shaped-hole. The
  same goes for the five-presentation whether-agreement gate and for union saturation: one
  reading has no union to saturate.
* **Cross-family** -- against each local judge's k = 0 reading and against its five-reading
  union: Cohen's kappa on the binary evidence/none verdict, span-union Jaccard where both
  are positive, and asymmetric character coverage in both directions.
* **Per-sentence support** -- the informative one. Pool the local judges' **ten** readings
  (scout k=0..4 and qwen k=0..4) and count, for every sentence of every document, how many
  of the ten touched it: its *support*, 0..10. Then ask what mean support the sentences a
  Claude model picked carry, against the mean support of every sentence the local pool
  touched at all. A stronger model picking the **more-supported** sentences is the reading
  the owner asked for; a model picking sentences of support 0 is finding locations the
  local pool never proposed, which is a different thing from being wrong and is counted
  separately rather than folded in.
* **Within-family** -- pairwise Jaccard and whether-kappa among sonnet5 / opus5 / fable51.
* **Cost and latency** per model, from the CLI's own accounting.

**No human statistic is computed here.** kappa(human-human), kappa(judge-human) and
enumeration recall against a human read remain ``PENDING-HUMAN``.
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

import s0_common as C                                          # noqa: E402
import s0_math as M                                            # noqa: E402
from s0_score import jaccard                                   # noqa: E402
import s0_labelgates_r31 as G                                  # noqa: E402
from s0_labelgates_r31 import (ANCHOR_EMPTY, ANCHOR_FIRST,     # noqa: E402
                               ANCHOR_LAST, cohen_kappa,
                               covered_frac, distinct_union, union_of)

HERE = pathlib.Path(__file__).resolve().parent
CLW = C.WORK / "claude"
R31 = C.WORK / "r31"
ART = HERE / "artifacts" / "claude"

CLAUDE = ["sonnet5", "opus5", "fable51"]
LOCAL = ["scout", "qwen"]
N_PRES_LOCAL = 5
GATE_HALL = G.GATE_HALL


# ---------------------------------------------------------------------- loading
def load_claude(judge: str, tag: str = "") -> dict[tuple[str, str], dict]:
    p = CLW / f"labels-claude-{judge}{('-' + tag) if tag else ''}.jsonl"
    if not p.exists():
        return {}
    by = {}
    for line in p.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            by[(r["topic"], r["docno"])] = r
    bad = {t for t, _d in by} - set(C.DEV_TOPICS)
    assert not bad, f"{p} contains non-development topics: {sorted(bad)}"
    return by


def load_local(judge: str) -> dict[tuple[str, str], dict[int, dict]]:
    return G.load(judge)


def sentences_of(rec) -> set[tuple[int, int]]:
    """The (unit, sentence) keys a record's evidence sets touch."""
    out: set[tuple[int, int]] = set()
    for s in rec["sets"]:
        for sp in s["spans"]:
            for k in range(sp["first_sentence"], sp["last_sentence"] + 1):
                out.add((sp["unit"], k))
    return out


def span_lengths(rec) -> tuple[list[int], list[int]]:
    """(sentences per span, characters per span) over one record's spans."""
    ns, nc = [], []
    for s in rec["sets"]:
        for sp in s["spans"]:
            ns.append(sp["last_sentence"] - sp["first_sentence"] + 1)
            nc.append(sp["end"] - sp["start"])
    return ns, nc


def _mean(x):
    return round(statistics.fmean(x), 4) if x else None


def _median(x):
    return round(statistics.median(x), 4) if x else None


# ---------------------------------------------------------------------- per judge
def per_judge(judge: str, by, manifest) -> dict:
    keys = sorted(by)
    V: dict[str, int] = {}
    anchor: dict[str, int] = {}
    for k in keys:
        for a, v in (by[k].get("vstats") or {}).items():
            V[a] = V.get(a, 0) + v
        for p in by[k]["problems"]:
            if p in (ANCHOR_FIRST, ANCHOR_LAST, ANCHOR_EMPTY):
                anchor[p] = anchor.get(p, 0) + 1
    att, fail = V.get("spans_seen", 0), V.get("hallucinated", 0)
    lo, up = M.wilson(fail, att) if att else (0.0, 1.0)
    hl_pass = bool(att and fail / att <= GATE_HALL)

    pos = [k for k in keys if by[k]["sets"]]
    none_ = [k for k in keys if not by[k]["dropped"] and not by[k]["sets"]]
    drops = [k for k in keys if by[k]["dropped"]]
    ns_all, nc_all = [], []
    for k in pos:
        a, b = span_lengths(by[k])
        ns_all += a
        nc_all += b
    deep = sum(1 for k in pos
               if all(sp["unit"] >= 2 for s in by[k]["sets"] for sp in s["spans"]))
    abstract_only = sum(1 for k in pos
                        if all(sp["unit"] == 0 for s in by[k]["sets"]
                               for sp in s["spans"]))
    st = (manifest or {}).get("stats") or {}
    dur = [by[k]["duration_ms"] for k in keys if by[k].get("duration_ms")]
    costs = [by[k]["cost_usd"] for k in keys if by[k].get("cost_usd") is not None]

    return {
        "judge": judge, "model": (manifest or {}).get("model"),
        "tier": (manifest or {}).get("tier"),
        "served_model_rows": st.get("served_model_rows"),
        "prompt_sha256": (manifest or {}).get("prompt_sha256"),
        "pairs_labeled": len(keys), "pairs_expected": 308,
        "presentations": 1,
        "stopped_by_usage_limit": (manifest or {}).get("stopped_by_usage_limit"),
        "stop_message": (manifest or {}).get("stop_message"),
        "isolation_assertion": (manifest or {}).get("isolation_assertion"),
        "hallucinated_span_rate": {
            "failed_spans": fail, "attempted_spans": att,
            "rate": None if att == 0 else round(fail / att, 5),
            "wilson95": [round(lo, 5), round(up, 5)], "wilson95_upper": round(up, 5),
            "by_anchor": {"first_sentence_quote": anchor.get(ANCHOR_FIRST, 0),
                          "last_sentence_quote": anchor.get(ANCHOR_LAST, 0),
                          "empty_first_quote": anchor.get(ANCHOR_EMPTY, 0)},
            "gate": "<= 0.05", "PASS": hl_pass,
            "denominator": "every span the judge emitted in its single reading"},
        "locate_ladder": {
            "first_anchor": {m: V.get("first_" + m, 0)
                             for m in ("exact", "normalised", "eight_word")},
            "last_anchor": {m: V.get("last_" + m, 0)
                            for m in ("exact", "normalised", "eight_word")},
            "note": "`eight_word` counts quotes the whole-sentence match missed and the "
                    "first-eight/last-eight fallback rescued — spans that would have been "
                    "hallucinations under an exact-or-normalised-only rule"},
        "self_consistency": {
            "ABSENT": "NOT MEASURABLE from this run. Self-consistency compares two "
                      "readings of the same pair; this run has one reading per pair. It "
                      "is absent, not 1.0 and not 0.",
            "rate": None},
        "whether_agreement_across_presentations": {
            "ABSENT": "NOT MEASURABLE — one presentation.", "rate": None},
        "union_saturation": {
            "ABSENT": "NOT MEASURABLE — one reading has no union to saturate."},
        "no_localizable_evidence": {
            "n": len(none_), "denominator": len(keys),
            "rate": round(len(none_) / len(keys), 4) if keys else None,
            "gate": "descriptive — a legal verdict, not a failure"},
        "dropped_pairs": len(drops),
        "dropped_pair_keys": [list(k) for k in drops],
        "spans_emitted": V.get("spans_emitted", 0),
        "split_across_units": V.get("split_across_units", 0),
        "unlocatable_spans": V.get("unlocatable", 0),
        "ambiguous_quotes": V.get("ambiguous_quote", 0),
        "one_sentence_conventions": {
            "last_quote_equal_to_first": V.get("last_same_as_first", 0),
            "last_quote_omitted": V.get("no_last_words", 0)},
        "legacy_field_names": V.get("legacy_field_names", 0),
        "unit_title_landed": V.get("title_landed", 0),
        "unit_title_elsewhere": V.get("title_elsewhere", 0),
        "unit_title_not_a_unit": V.get("title_unknown", 0),
        "windowed_pairs": sum(1 for k in keys if by[k]["windowed"]),
        "reprompted_pairs": sum(1 for k in keys if "reprompted" in by[k]["problems"]),
        "shape": {
            "positive_pairs": len(pos),
            "mean_sets_per_positive_pair":
                _mean([len(by[k]["sets"]) for k in pos]),
            "mean_spans_per_positive_pair":
                _mean([sum(len(s["spans"]) for s in by[k]["sets"]) for k in pos]),
            "median_span_length_sentences": _median(ns_all),
            "mean_span_length_sentences": _mean(ns_all),
            "median_span_length_chars": _median(nc_all),
            "mean_span_length_chars": _mean(nc_all),
            "n_spans_measured": len(ns_all),
            "abstract_only_pairs": abstract_only, "deep_section_pairs": deep,
            "deep_section_definition":
                "every span of the pair sits in unit index >= 2 (§6.6.6 abstract bias)"},
        "cost_and_latency": {
            # The auditable figure: the sum over the label records, each of which carries
            # the `total_cost_usd` of its own CLI call(s), and recomputable from
            # `raw-<judge>.jsonl`. The labeler's in-process counter is carried beside it.
            "total_cost_usd": round(sum(costs), 4) if costs else None,
            "total_cost_usd_source": "sum over label records (auditable from raw-<judge>.jsonl)",
            "manifest_recorded_cost_usd": (manifest or {}).get("recorded_cost_usd"),
            "in_process_counter_cost_usd": st.get("cost_usd"),
            "mean_cost_usd_per_pair": _mean(costs), "n_pairs_costed": len(costs),
            "median_latency_ms": _median(dur), "mean_latency_ms": _mean(dur),
            "cli_seconds": st.get("cli_seconds"),
            "wall_seconds": (manifest or {}).get("wall_seconds"),
            "input_tokens": st.get("input_tokens"),
            "output_tokens": st.get("output_tokens"),
            "cache_creation_input_tokens": st.get("cache_creation_input_tokens"),
            "cache_read_input_tokens": st.get("cache_read_input_tokens"),
            "requests": st.get("requests"), "retries": st.get("retries"),
            "failures": st.get("failures")},
        "GATE_PASS_hallucinated_span": hl_pass,
        "GATES_NOT_MEASURABLE": ["self_consistency", "whether_agreement",
                                 "union_saturation"],
    }


# ------------------------------------------------------------------ cross family
def cross_pair(A: dict, B: dict, a_name: str, b_name: str) -> dict:
    """A is a Claude judge's single reading; B is {key: intervals} for the other side."""
    keys = sorted(set(A) & set(B))
    if not keys:
        return {"n": 0, "note": "UNRESOLVED — no co-labeled pair"}
    va = [1 if A[k] else 0 for k in keys]
    vb = [1 if B[k] else 0 for k in keys]
    both = [k for k in keys if A[k] and B[k]]
    js = [jaccard(A[k], B[k]) for k in both]
    ca = [covered_frac(A[k], B[k]) for k in both]
    cb = [covered_frac(B[k], A[k]) for k in both]
    return {
        "co_labeled_pairs": len(keys),
        "whether_kappa": cohen_kappa(va, vb),
        "span_union_jaccard_where_both_positive": {
            "n": len(js), "mean": _mean(js), "median": _median(js),
            "frac_at_or_above_0.5": (round(sum(1 for x in js if x >= 0.5) / len(js), 4)
                                     if js else None)},
        "asymmetric_coverage": {
            f"{a_name}_chars_also_covered_by_{b_name}":
                _mean([x for x in ca if x is not None]),
            f"{b_name}_chars_also_covered_by_{a_name}":
                _mean([x for x in cb if x is not None]),
            "n": len(both),
            "definition": "mean over pairs both call positive of the fraction of one "
                          "side's selected characters that lie inside the other's"},
    }


# --------------------------------------------------------------- sentence support
def support_map(local: dict[str, dict], keys) -> dict[tuple[str, str], dict]:
    """Per pair: {(unit, sentence): how many of the TEN local readings touched it}.

    Ten readings = scout presentations 0..4 plus qwen presentations 0..4. A reading
    contributes at most 1 to a sentence however many of its evidence sets cover it, so the
    support of a sentence is a count of *readers*, in 0..10, not a count of spans.
    """
    out = {}
    for k in keys:
        sup: dict[tuple[int, int], int] = {}
        n_readings = 0
        for j in LOCAL:
            byj = local[j]
            if k not in byj:
                continue
            for p in range(N_PRES_LOCAL):
                if p not in byj[k]:
                    continue
                n_readings += 1
                for s in sentences_of(byj[k][p]):
                    sup[s] = sup.get(s, 0) + 1
        out[k] = {"support": sup, "n_readings": n_readings}
    return out


def support_agreement(judge: str, by, sup) -> dict:
    """Mean local support of the sentences this Claude judge picked, vs the baseline.

    Baseline = the mean support of every sentence the local ten-reading pool touched at
    all (support >= 1) on the same pairs. A model that picks the sentences the local pool
    converged on scores above the baseline; a model that picks sentences the pool touched
    once scores below it; a model that picks sentences the pool never touched contributes
    support 0 and those are ALSO counted separately, because "the local pool never
    proposed this location" is not the same fact as "this location is wrong".
    """
    picked_sup, base_sup = [], []
    per_pair_picked, per_pair_base = [], []
    zero, n_picked_sentences, pairs_used = 0, 0, 0
    max_sup = 0
    for k in sorted(by):
        if k not in sup or not sup[k]["support"]:
            continue
        s_map = sup[k]["support"]
        max_sup = max(max_sup, max(s_map.values()))
        mine = sentences_of(by[k])
        base_vals = list(s_map.values())
        base_sup += base_vals
        per_pair_base.append(statistics.fmean(base_vals))
        if not mine:
            continue
        pairs_used += 1
        vals = [s_map.get(s, 0) for s in mine]
        picked_sup += vals
        n_picked_sentences += len(vals)
        zero += sum(1 for v in vals if v == 0)
        per_pair_picked.append(statistics.fmean(vals))
    nz = [v for v in picked_sup if v > 0]
    return {
        "judge": judge,
        "n_pairs_with_local_support": pairs_used,
        "n_sentences_picked": n_picked_sentences,
        "max_possible_support": 10,
        "max_observed_support": max_sup,
        "mean_support_of_picked_sentences": _mean(picked_sup),
        "median_support_of_picked_sentences": _median(picked_sup),
        "mean_support_of_all_locally_touched_sentences": _mean(base_sup),
        "median_support_of_all_locally_touched_sentences": _median(base_sup),
        "n_locally_touched_sentences": len(base_sup),
        "lift_over_baseline": (round(statistics.fmean(picked_sup)
                                     - statistics.fmean(base_sup), 4)
                               if picked_sup and base_sup else None),
        "per_pair_mean_of_means_picked": _mean(per_pair_picked),
        "per_pair_mean_of_means_baseline": _mean(per_pair_base),
        "picked_sentences_with_support_0": zero,
        "frac_picked_with_support_0": (round(zero / n_picked_sentences, 4)
                                       if n_picked_sentences else None),
        "mean_support_excluding_support_0": _mean(nz),
        "definition": "support of a sentence = how many of the local judges' TEN readings "
                      "(scout k=0..4, qwen k=0..4) placed evidence on it. The baseline is "
                      "the mean over every sentence with support >= 1 on the same pairs; "
                      "sentences no local reading touched have support 0 and are inside "
                      "the picked mean but outside the baseline by construction.",
    }


# ---------------------------------------------------------------------- rendering
def markdown(out: dict) -> str:
    js = [j for j in CLAUDE if j in out["judges"]]
    L = ["# Stage 0b′ — the Claude judge family: gates and cross-judge agreement", "",
         "One reading per pair (presentation k = 0, natural unit order), r3.1's prompt "
         "(sha256 `" + out["prompt_sha256"][:16] + "…`), r3.1's locator.", "",
         "| statistic | requirement | " + " | ".join(f"**{j}**" for j in js) + " |",
         "|---|---|" + "---|" * len(js)]

    def row(label, req, fn):
        L.append(f"| {label} | {req} | "
                 + " | ".join(fn(out["judges"][j]) for j in js) + " |")

    def verdict(ok):
        return "**PASS**" if ok else "**FAIL**"

    row("model", "—", lambda d: f"`{d['model']}`")
    row("pairs labeled / 308", "308",
        lambda d: (f"**{d['pairs_labeled']}**"
                   + (" ⚠ stopped" if d["stopped_by_usage_limit"] else "")))
    row("**hallucinated-span rate**", "≤ 0.05",
        lambda d: (f"**{d['hallucinated_span_rate']['rate']}** "
                   f"({d['hallucinated_span_rate']['failed_spans']}/"
                   f"{d['hallucinated_span_rate']['attempted_spans']} spans; Wilson 95 % "
                   f"upper {d['hallucinated_span_rate']['wilson95_upper']}) "
                   f"{verdict(d['hallucinated_span_rate']['PASS'])}"))
    row("  — split by anchor (first-sentence / last-sentence quote)",
        "reported (#501, ten-word anchors: 4 / 54 for scout)",
        lambda d: (f"{d['hallucinated_span_rate']['by_anchor']['first_sentence_quote']} / "
                   f"{d['hallucinated_span_rate']['by_anchor']['last_sentence_quote']}"))
    row("  — quotes rescued by the eight-word ladder (first / last)", "descriptive",
        lambda d: (f"{d['locate_ladder']['first_anchor']['eight_word']} / "
                   f"{d['locate_ladder']['last_anchor']['eight_word']}"))
    row("  — quotes located byte-exact (first anchor)", "descriptive",
        lambda d: (f"{d['locate_ladder']['first_anchor']['exact']}/"
                   f"{d['hallucinated_span_rate']['attempted_spans']}"))
    row("**self-consistency**", "≥ 0.90",
        lambda d: "**ABSENT** — one reading per pair")
    row("**whether-agreement across presentations**", "≥ 0.90",
        lambda d: "**ABSENT** — one presentation")
    row("“no localizable evidence” rate", "descriptive",
        lambda d: (f"{d['no_localizable_evidence']['rate']} "
                   f"({d['no_localizable_evidence']['n']}/"
                   f"{d['no_localizable_evidence']['denominator']} pairs)"))
    row("pairs dropped (no verified span survived)", "descriptive",
        lambda d: f"{d['dropped_pairs']}/{d['pairs_labeled']}")
    row("pairs re-prompted (§6.4 rule 2)", "descriptive",
        lambda d: f"{d['reprompted_pairs']}/{d['pairs_labeled']}")
    row("mean evidence **sets** per positive pair", "descriptive",
        lambda d: (f"**{d['shape']['mean_sets_per_positive_pair']}** "
                   f"(n={d['shape']['positive_pairs']})"))
    row("mean **spans** per positive pair", "descriptive",
        lambda d: f"**{d['shape']['mean_spans_per_positive_pair']}**")
    row("median span length (sentences / characters)", "descriptive",
        lambda d: (f"{d['shape']['median_span_length_sentences']} / "
                   f"{d['shape']['median_span_length_chars']} "
                   f"(n={d['shape']['n_spans_measured']} spans)"))
    row("spans split across a unit boundary", "descriptive",
        lambda d: str(d["split_across_units"]))
    row("quote landed inside the unit whose title it named", "descriptive",
        lambda d: (f"{d['unit_title_landed']}/"
                   f"{d['unit_title_landed'] + d['unit_title_elsewhere']}"))
    row("pairs whose every span is in the abstract", "descriptive",
        lambda d: f"{d['shape']['abstract_only_pairs']}/{d['shape']['positive_pairs']}")
    row("deep-section pairs (every span in unit ≥ 2)", "descriptive (Stage 0: 3/308)",
        lambda d: f"{d['shape']['deep_section_pairs']}/{d['shape']['positive_pairs']}")

    # --- cost
    L += ["", "## Cost and latency", "",
          "| model | pairs | total cost | $/pair | median latency | mean latency | "
          "wall | input tok | output tok |", "|---|---|---|---|---|---|---|---|---|"]
    for j in js:
        d = out["judges"][j]
        c = d["cost_and_latency"]
        L.append(f"| `{d['model']}` | {d['pairs_labeled']} | "
                 f"**${c['total_cost_usd']}** | ${c['mean_cost_usd_per_pair']} | "
                 f"{c['median_latency_ms']} ms | {c['mean_latency_ms']} ms | "
                 f"{c['wall_seconds']} s | {c['input_tokens']} | {c['output_tokens']} |")
    L += ["", "Token counters are the CLI's: `input_tokens` is the uncached portion, and "
              "a prompt served from the prompt cache is billed under "
              "`cache_read_input_tokens` instead. Cost is `total_cost_usd`, which also "
              "carries the CLI's fixed ancillary `claude-haiku-4-5` call (≈ 900 input "
              "tokens per call, unchanged by the size of our prompt) — it is harness "
              "overhead, not a judge, and it is inside the cost but outside every "
              "isolation and agreement number below."]

    # --- support
    sa = out.get("sentence_support") or {}
    if sa.get("per_judge"):
        L += ["", "## Per-sentence support under the local judges' ten-reading pool", "",
              "Support of a sentence = how many of the **ten** local readings "
              "(scout k = 0…4, qwen k = 0…4) placed evidence on it, 0…10. The baseline "
              "is the mean support of every sentence the local pool touched at all "
              "(support ≥ 1) on the same pairs.", "",
              "| model | sentences picked | mean support of picked | baseline (all "
              "locally touched) | lift | picked with support 0 | mean support excl. 0 |",
              "|---|---|---|---|---|---|---|"]
        for j in js:
            s = sa["per_judge"].get(j)
            if not s:
                continue
            L.append(f"| `{out['judges'][j]['model']}` | {s['n_sentences_picked']} | "
                     f"**{s['mean_support_of_picked_sentences']}** | "
                     f"{s['mean_support_of_all_locally_touched_sentences']} | "
                     f"**{s['lift_over_baseline']:+}** | "
                     f"{s['picked_sentences_with_support_0']} "
                     f"({s['frac_picked_with_support_0']}) | "
                     f"{s['mean_support_excluding_support_0']} |")
        if sa.get("local_self_reference"):
            L += ["", "For scale, the same statistic computed on the local judges' own "
                      "k = 0 readings (each of which is one of the ten readings that "
                      "*builds* the support map, so these are self-referential upper "
                      "anchors, not comparables):", "",
                  "| local judge (k = 0) | sentences picked | mean support of picked | "
                  "baseline | lift |", "|---|---|---|---|---|"]
            for j, s in sa["local_self_reference"].items():
                L.append(f"| {j} | {s['n_sentences_picked']} | "
                         f"{s['mean_support_of_picked_sentences']} | "
                         f"{s['mean_support_of_all_locally_touched_sentences']} | "
                         f"{s['lift_over_baseline']:+} |")

    # --- cross family
    cf = out.get("cross_family") or {}
    if cf:
        L += ["", "## Agreement with the local judges", "",
              "| Claude judge | local side | co-labeled | κ(whether) | Jaccard where "
              "both + (mean / median / ≥ 0.5) | Claude chars inside local | local chars "
              "inside Claude |", "|---|---|---|---|---|---|---|"]
        for j in js:
            for side, d in (cf.get(j) or {}).items():
                if not d or "whether_kappa" not in d:
                    continue
                k = d["whether_kappa"]
                jj = d["span_union_jaccard_where_both_positive"]
                ac = d["asymmetric_coverage"]
                a = ac.get(f"{j}_chars_also_covered_by_local")
                b = ac.get(f"local_chars_also_covered_by_{j}")
                L.append(f"| `{j}` | {side} | {d['co_labeled_pairs']} | "
                         f"**{k['kappa']}** (obs {k['observed_agreement']}) | "
                         f"{jj['mean']} / {jj['median']} / "
                         f"{jj['frac_at_or_above_0.5']} (n={jj['n']}) | "
                         f"{a} | {b} |")

    # --- within family
    wf = out.get("within_family") or {}
    if wf:
        L += ["", "## Agreement among the three Claude judges", "",
              "| pair | co-labeled | κ(whether) | observed agreement | Jaccard where "
              "both + (mean / median / ≥ 0.5) |", "|---|---|---|---|---|"]
        for name, d in wf.items():
            if "whether_kappa" not in d:
                continue
            k = d["whether_kappa"]
            jj = d["span_union_jaccard_where_both_positive"]
            L.append(f"| {name} | {d['co_labeled_pairs']} | **{k['kappa']}** | "
                     f"{k['observed_agreement']} | {jj['mean']} / {jj['median']} / "
                     f"{jj['frac_at_or_above_0.5']} (n={jj['n']}) |")

    L += ["", "## What this run cannot say", "",
          out["ABSENT"], "", out["HUMAN_HALF"]]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="")
    ap.add_argument("--outdir", default=str(ART))
    args = ap.parse_args()

    judges, loaded = {}, {}
    for j in CLAUDE:
        by = load_claude(j, args.tag)
        if not by:
            continue
        loaded[j] = by
        mp = CLW / f"label-manifest-claude-{j}{('-' + args.tag) if args.tag else ''}.json"
        man = json.loads(mp.read_text()) if mp.exists() else None
        judges[j] = per_judge(j, by, man)

    if not judges:
        raise SystemExit("no Claude label files found — nothing to gate")

    local = {j: load_local(j) for j in LOCAL}
    local = {j: v for j, v in local.items() if v}

    # --- cross-family, against each local judge's k = 0 reading and its k = 5 union
    cross_family: dict[str, dict] = {}
    for j, by in loaded.items():
        A = {k: union_of(by[k]["sets"]) for k in by}
        side: dict[str, dict] = {}
        for lj, lby in local.items():
            k0 = {k: union_of(v[0]["sets"]) for k, v in lby.items() if 0 in v}
            side[f"{lj} k=0"] = cross_pair(A, k0, j, "local")
            u5 = {}
            for k, v in lby.items():
                if all(p in v for p in range(N_PRES_LOCAL)):
                    u5[k] = [iv for s in distinct_union(
                        [v[p]["sets"] for p in range(N_PRES_LOCAL)]) for iv in s]
            side[f"{lj} k=5 union"] = cross_pair(A, u5, j, "local")
        # and against the pooled union of BOTH local judges' five readings
        pooled = {}
        for k in set().union(*[set(v) for v in local.values()]):
            sl = []
            for lj, lby in local.items():
                if k in lby and all(p in lby[k] for p in range(N_PRES_LOCAL)):
                    sl += [lby[k][p]["sets"] for p in range(N_PRES_LOCAL)]
            if sl:
                pooled[k] = [iv for s in distinct_union(sl) for iv in s]
        side["scout ∪ qwen, ten-reading union"] = cross_pair(A, pooled, j, "local")
        cross_family[j] = side

    # --- within-family
    within: dict[str, dict] = {}
    names = [j for j in CLAUDE if j in loaded]
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            x, y = names[a], names[b]
            A = {k: union_of(loaded[x][k]["sets"]) for k in loaded[x]}
            B = {k: union_of(loaded[y][k]["sets"]) for k in loaded[y]}
            within[f"{x} vs {y}"] = cross_pair(A, B, x, y)

    # --- sentence support
    support: dict = {}
    if local:
        keys = sorted(set().union(*[set(v) for v in loaded.values()]))
        sup = support_map(local, keys)
        support = {
            "per_judge": {j: support_agreement(j, loaded[j], sup) for j in loaded},
            "n_readings_pooled": 10,
            "readings": "scout k=0..4 + qwen k=0..4",
            "local_self_reference": {
                lj: support_agreement(lj, {k: v[0] for k, v in lby.items() if 0 in v}, sup)
                for lj, lby in local.items()},
            "local_self_reference_note":
                "SELF-REFERENTIAL: each local judge's k = 0 reading is one of the ten "
                "readings that builds the support map, so its picked sentences have "
                "support >= 1 by construction. Read as an upper anchor for the scale, "
                "never as a comparable to the Claude rows.",
        }

    out = {
        "protocol": ("SPEC-confirmation-run-r3.md §10 item 4 — third judge family "
                     "(Anthropic), r3.1 protocol, ONE reading per pair (k = 0)"),
        "prompt_sha256": next(iter(judges.values()))["prompt_sha256"],
        "gates": {"hallucinated_span": "<= 0.05 (Wilson 95 %)",
                  "self_consistency": "NOT MEASURABLE — one reading per pair",
                  "whether_agreement": "NOT MEASURABLE — one presentation"},
        "dev_topics": C.DEV_TOPICS,
        "judges": judges,
        "cross_family": cross_family,
        "within_family": within,
        "sentence_support": support,
        "local_judges_read": sorted(local),
        "ABSENT": ("**Self-consistency, five-presentation whether-agreement and union "
                   "saturation are ABSENT for this family, not zero and not null.** They "
                   "each need more than one reading of a pair and this run bought one. "
                   "Every gate the r3.1 local judges failed on self-consistency is "
                   "therefore untested here: nothing in this file says a Claude judge "
                   "would have passed it."),
        "HUMAN_HALF": ("*κ(human–human), κ(judge–human), the wrong-location / non-minimal "
                       "/ missed-evidence rates and enumeration recall against "
                       "human-marked sets remain `PENDING-HUMAN` — they require the "
                       "two-reader R-dev read of §6.6.2. No agent read was substituted "
                       "and none was performed. The R-dev pairs were not read by any "
                       "judge in this run.*"),
    }

    od = pathlib.Path(args.outdir)
    od.mkdir(parents=True, exist_ok=True)
    C.atomic_json(od / "gates-claude.json", out)
    md = markdown(out)
    (od / "gates-claude.md").write_text(md)
    C.atomic_json(CLW / "gates-claude.json", out)
    print(md)


if __name__ == "__main__":
    main()
