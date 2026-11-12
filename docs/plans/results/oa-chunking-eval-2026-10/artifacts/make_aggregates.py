"""Regenerate oa-eval-aggregates.json: the public, aggregate-only record of the OA chunking eval.

    PYTHONPATH=<repo>/python python make_aggregates.py SRC_DIR [OUT_JSON]

SRC_DIR is the private experiments directory ``oa-chunking-eval-2026-10/`` (it holds
``main-run-2026-11/`` and ``sections-2026-11/``). OUT_JSON defaults to
``oa-eval-aggregates.json`` beside this script.

What it does:

* COPIES aggregates the frozen outputs already contain (``score.json``, ``decision.json``,
  ``noise.json``, ``addendum_c.json``, the three ``posthoc/*.json``, the data summaries).
  Every copied entry names its source file and carries ``recomputed: false``.
* RECOMPUTES only what no frozen file holds (``recomputed: true`` on each such entry):
  a few paired contrasts from ``per_question.jsonl.xz`` with the harness's own
  ``oa_chunk_eval.stats.paired_diff`` (10,000 resamples, seed 0, same question order as
  the harness via ``set_rows``), passage length quantiles and section-type counts from
  ``passages.jsonl``, and receipt chunk totals. Before using the bootstrap it re-derives
  three frozen intervals and refuses to write if they do not reproduce exactly.
* CHECKS every Markdown figure listed in ``MARKDOWN_CHECKS`` against the entry it points
  at, at the printed precision, and lists the result (mismatches are reported, never
  patched).

Hygiene: the output holds counts, rates, differences and intervals only. No question,
quote or passage text, no record ids or PMCIDs, no hostnames, ports, private paths or
collection physical names. Source files are named by their path inside the private
directory, with their sha256. ``provenance.host`` is dropped and warnings are reduced to
their codes.

Deterministic: identical inputs give identical output except ``provenance.recorded_at``.
"""
from __future__ import annotations

import hashlib
import json
import lzma
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[4]
SCHEMA = "ragstack.oa_eval_aggregates/v1"

MAIN = "main-run-2026-11"
SEC = "sections-2026-11"
MAIN_DIRS = {"a": "a-hybrid-rerank", "d": "d-hybrid-norerank", "b": "b-vector", "c": "c-bm25"}
SEC_DIRS = {"a": "a-hybrid-rerank", "d": "d-hybrid", "b": "b-vector", "c": "c-bm25",
            "e": "rr-vector", "f": "rr-bm25"}
SETTING_LABEL = {
    "a": "hybrid (BM25 + dense, RRF) + cross-encoder reranker over the top 50; production; PRIMARY decision",
    "d": "hybrid (BM25 + dense, RRF), no reranker; secondary report (Amendment B.1, before retrieval)",
    "b": "dense vector only, no reranker; secondary report",
    "c": "BM25 only, no reranker; secondary report",
    "e": "dense vector only + reranker; post hoc (hypothesis C2), server v1.6.25",
    "f": "BM25 only + reranker; post hoc (hypothesis C2), server v1.6.25",
}
MAIN_ARMS = ["ft512o64", "ft512o0", "sent512", "ft1024o0", "sempool512", "sem512"]
SEC_ARMS = ["ft512o64s", "sent512s", "sent512sh", "sent512rc"]
ALL_ARMS = MAIN_ARMS + SEC_ARMS
ARM_LABEL = {
    "ft512o64": "fixed_token 512, overlap 64 (baseline)",
    "ft512o0": "fixed_token 512, overlap 0",
    "sent512": "sentence, 512 tokens",
    "ft1024o0": "fixed_token 1024, overlap 0",
    "sempool512": "semantic_pooled, cap 512",
    "sem512": "legacy semantic, cap 512",
    "ft512o64s": "fixed_token 512/64 cut at JATS section boundaries (Addendum C)",
    "sent512s": "sentence 512 cut at JATS section boundaries (Addendum C)",
    "sent512sh": "sentence 512 + section cuts + heading path in the embedded text (Addendum C)",
    "sent512rc": "sentence 512 + random cuts count-matched to the section cuts (Addendum C control)",
}
DECISION_SET, SENS_SET = "clean_excl_noise", "clean"
ITERS, SEED = 10_000, 0
BOOT = {"type": "paired bootstrap over questions (percentile interval)", "resamples": ITERS,
        "seed": SEED, "ci_level": 0.95}
METRIC = "hit@5: the top-5 chunks from the gold article cover >= 90% of the gold quote's characters; points = percentage points"

FAMILY = {"confirmatory": "frozen main run (PLAN-AND-PASS-RULE.md + Addenda A/B, Amendment B.1; six arms)",
          "addendum-C": "Addendum C (section-bounded arms; rules committed privately after the main run, before any retrieval on these arms)",
          "exploratory": "post hoc exploratory (POSTHOC-HYPOTHESES-2026-11-06.md and the main run's 'analysis, not the rule' section)"}


# --------------------------------------------------------------------------- inputs
class Src:
    """Reads private files, recording each file's sha256 under its relative name."""

    def __init__(self, root: Path):
        self.root = root
        self.sha: dict[str, str] = {}

    def _bytes(self, rel: str) -> bytes:
        b = (self.root / rel).read_bytes()
        self.sha[rel] = hashlib.sha256(b).hexdigest()
        return b

    def json(self, rel: str) -> Any:
        return json.loads(self._bytes(rel))

    def jsonl(self, rel: str) -> list[dict]:
        b = self._bytes(rel)
        if rel.endswith(".xz"):
            b = lzma.decompress(b)
        return [json.loads(line) for line in b.decode().splitlines() if line.strip()]

    def text(self, rel: str) -> str:
        return self._bytes(rel).decode()


def r(x, nd=4):
    return None if x is None else round(float(x), nd)


def ci(d: dict, nd=4) -> dict:
    return {"n": d.get("n"), "point": r(d["point"], nd), "lo": r(d["lo"], nd), "hi": r(d["hi"], nd)}


def entry(eid, *, status, setting, pair, src, set_name=DECISION_SET, n=None, point=None, lo=None,
          hi=None, adjustment="none (unadjusted 95% interval)", recomputed=False, metric="hit@5",
          method=None, **extra) -> tuple[str, dict]:
    e = {"id": eid, "status": status, "family": FAMILY[status], "setting": setting, "pair": pair,
         "set": "decision" if set_name == DECISION_SET else ("sensitivity" if set_name == SENS_SET else set_name),
         "metric": metric, "n": n, "point": point, "lo": lo, "hi": hi,
         "method": method or BOOT, "adjustment": adjustment, "recomputed": recomputed, "source": src}
    e.update(extra)
    return eid, e


def slug(s: str) -> str:
    """A pointer-safe id fragment: no spaces, dots or brackets."""
    for a, b in ((">=", "ge"), (".", "p"), (" ", "_")):
        s = s.replace(a, b)
    return s


# --------------------------------------------------------------------------- recompute
def harness():
    sys.path.insert(0, str(REPO_ROOT / "python" / "scripts" / "eval"))
    from oa_chunk_eval import stats as st  # noqa: E402
    from oa_chunk_eval.score import set_rows  # noqa: E402
    for mod in (st,):
        if not Path(mod.__file__).resolve().is_relative_to(REPO_ROOT):
            raise SystemExit(f"REFUSED: harness resolved outside the repo: {mod.__file__}")
    return st.paired_diff, set_rows


def vectors(rows, set_rows, arms, k="5"):
    rs = set_rows(rows, DECISION_SET)
    return len(rs), {a: np.array([q["arms"][a][k]["primary"] for q in rs], float) for a in arms}


# --------------------------------------------------------------------------- build
def build(src: Src) -> dict:
    paired_diff, set_rows = harness()
    out: dict[str, Any] = {
        "schema": SCHEMA,
        "study": "OA chunking eval 2026-10 (main run 2026-11-05, Addendum C 2026-11-06, post hoc 2026-11-06)",
        "aggregate_only": True,
        "public_record": ["docs/plans/results/oa-chunking-eval-2026-10/PLAN-AND-PASS-RULE.md",
                          "docs/plans/results/oa-chunking-eval-2026-10/POSTHOC-HYPOTHESES-2026-11-06.md"],
        "metric": METRIC,
        "status_labels": FAMILY,
        "settings_legend": SETTING_LABEL,
        "arms_legend": ARM_LABEL,
    }
    contrasts: dict[str, dict] = {}

    def add(kv):
        k, v = kv
        if k in contrasts:
            raise SystemExit(f"duplicate entry id {k}")
        contrasts[k] = v

    # ---------------- question sets and construction facts
    sample = src.json(f"{MAIN}/data/sample.summary.json")
    psum = src.json(f"{MAIN}/data/passages.summary.json")
    qsum = src.json(f"{MAIN}/data/questions.summary.json")["main"]
    audit = src.json(f"{MAIN}/data/audit.summary.json")["main"]
    passages = src.jsonl(f"{MAIN}/data/passages.jsonl")
    skipped = src.jsonl(f"{MAIN}/data/passages.skipped.jsonl")
    lens = np.array([len(p["text"]) for p in passages], float)
    main_a_score = src.json(f"{MAIN}/settings/a-hybrid-rerank/score.json")
    sets_a = main_a_score["sets"]
    out["question_sets"] = {
        "_source": [f"{MAIN}/data/sample.summary.json", f"{MAIN}/data/passages.summary.json",
                    f"{MAIN}/data/questions.summary.json", f"{MAIN}/data/audit.summary.json",
                    f"{MAIN}/settings/a-hybrid-rerank/score.json"],
        "sample": {"n_eval_articles": sample["n_eval"], "n_background_articles": sample["n_background"],
                   "n_sampled_articles": sample["n_eval"] + sample["n_background"],
                   "n_eligible_articles": sample["n_eligible"], "seed": sample["seed"],
                   "strata": "journal_family (8) x length_band (3)"},
        "corpus_articles_indexed": {"value": None, "note": "22,593 in POSTHOC (22,600 sampled minus 7 with no extracted record) is not derivable from the files this script reads (the extraction output is not in the private record); not covered here."},
        "records_ingested_per_arm": None,  # filled from receipts below
        "passages": {"n_passages": psum["n_passages"], "n_skipped_articles": psum["n_skipped_articles"],
                     "skipped_by_reason": dict(sorted(Counter(s["reason"] for s in skipped).items())),
                     "target_chars": psum["chars"], "segmenter": psum["segmenter"]},
        "questions": {"selected": qsum["selected"], "passages_failed_all_attempts": qsum["skipped"],
                      "max_attempts": qsum["max_attempts"],
                      "check_failures_by_reason_over_all_attempts": qsum["check_failures"]},
        "audit": {"ambiguous_gold": audit["ambiguous_gold"], "within_record_repeat": audit["within_record_repeat"],
                  "either_flag": sets_a["ambiguous"]["n"],
                  "both_flags": audit["ambiguous_gold"] + audit["within_record_repeat"] - sets_a["ambiguous"]["n"],
                  "gold_record_missing": audit["gold_record_missing"]},
        "all_questions": sets_a["all"]["n"],
        "clean_set": sets_a["clean"]["n"],
        "noise_subset": main_a_score["noise_exclusion"]["n_noise_subset"],
        "decision_set": sets_a[DECISION_SET]["n"],
        "sensitivity_set": sets_a[SENS_SET]["n"],
        "decision_set_without_lossy_strata": sets_a[DECISION_SET]["without_lossy_strata"]["n"],
        "lossy_strata": main_a_score["lossy_strata"],
    }
    pq_a = src.jsonl(f"{SEC}/settings/a-hybrid-rerank/per_question.jsonl.xz")
    dec_rows = set_rows(pq_a, DECISION_SET)
    out["construction"] = {
        "passage_chars": {"recomputed": True, "source": f"{MAIN}/data/passages.jsonl",
                          "how": "len(text) over all passages; numpy linear-interpolation percentiles",
                          "n": int(lens.size), "median": r(np.median(lens), 1),
                          "p10": r(np.percentile(lens, 10), 1), "p90": r(np.percentile(lens, 90), 1)},
        "passage_section_type": {"recomputed": True, "source": f"{MAIN}/data/passages.jsonl",
                                 "n": len(passages),
                                 "counts": dict(sorted(Counter(p["section_type"] for p in passages).items()))},
        "decision_set_section_type": {"recomputed": True,
                                      "source": f"{SEC}/settings/a-hybrid-rerank/per_question.jsonl.xz",
                                      "n": len(dec_rows),
                                      "counts": dict(sorted(Counter(str(q["strata"]["section_type"]) for q in dec_rows).items()))},
        "decision_set_length_band": {"recomputed": True,
                                     "source": f"{SEC}/settings/a-hybrid-rerank/per_question.jsonl.xz",
                                     "counts": dict(sorted(Counter(q["strata"]["length_band"] for q in dec_rows).items()))},
        "decision_set_journal_family": {"recomputed": True,
                                        "source": f"{SEC}/settings/a-hybrid-rerank/per_question.jsonl.xz",
                                        "counts": dict(sorted(Counter(q["strata"]["journal_family"] for q in dec_rows).items()))},
    }
    oa = src.json(f"{SEC}/posthoc/overlap_artifact.json")
    so = src.json(f"{SEC}/posthoc/size_overlap.json")
    addc = src.json(f"{SEC}/addendum_c.json")
    g = oa["geometry"]
    out["construction"].update({
        "gold_quote_chars": {"source": f"{SEC}/posthoc/overlap_artifact.json", "n": oa["n"],
                             "median": g["quote_chars"]["median"], "p10": r(g["quote_chars"]["p10"], 1),
                             "p90": r(g["quote_chars"]["p90"], 1), "max": g["quote_chars"]["max"],
                             "cap_by_construction": 300},
        "overlap_width_chars_ft512o64": {"source": f"{SEC}/posthoc/overlap_artifact.json", **g["overlap_chars_o64"]},
        "gold_quote_split_by_boundary": {
            "source": [f"{SEC}/posthoc/overlap_artifact.json", f"{SEC}/posthoc/size_overlap.json"],
            "n": oa["n"],
            "ft512o0": g["straddle_rate_o0"],
            "ft512o64": r(1 - so["chunk_geometry"]["ft512o64"]["quote_in_one_chunk"], 4),
            "ft1024o0": r(1 - so["chunk_geometry"]["ft1024o0"]["quote_in_one_chunk"], 4),
            "note": ("ft512o64 and ft1024o0 are 1 - quote_in_one_chunk (derived). The shares POSTHOC prints for "
                     "sem512, sempool512, sent512, sent512s, ft512o64s and sent512rc were measured against the live "
                     "stores and saved in no private file; they are not covered here."),
            "by_quote_length_ft512o0": g["by_quote_length"]},
        "gold_quote_crosses_section_boundary": {"source": f"{SEC}/addendum_c.json", **addc["gold_quote_crossing"]},
        "chunk_geometry_in_stores": {"source": f"{SEC}/posthoc/size_overlap.json",
                                     "scope": "chunks of the gold records behind the decision-set questions",
                                     **so["chunk_geometry"]},
        "density_tercile_cuts": {"source": f"{SEC}/posthoc/size_overlap.json", **so["tertile_cuts"]},
    })

    # ---------------- chunk counts (receipts)
    rec = src.jsonl(f"{MAIN}/ingest/receipts-summary.jsonl")
    cc: dict[str, dict] = {}
    for row in rec:
        a = cc.setdefault(row["arm"], {"shards": 0, "docs": 0, "docs_failed": 0, "chunks": 0})
        a["shards"] += 1; a["docs"] += row["n_docs"]; a["docs_failed"] += row["n_docs_failed"]
        a["chunks"] += row["n_chunk_ids"]
    sec_lines = [ln.split() for ln in src.text(f"{SEC}/ingest/receipts-summary.txt").splitlines() if ln.strip()]
    full = sorted((int(x[4]) for x in sec_lines if int(x[1]) == 16 and int(x[2]) == 241930))
    if full != [850119, 850119, 859087, 868479]:
        raise SystemExit(f"unexpected section receipt totals {full}")
    cc_sec = {"ft512o64s": 859087, "sent512s": 850119, "sent512sh": 850119, "sent512rc": 868479}
    out["chunk_counts"] = {
        "recomputed": True,
        "what": "sum of n_chunk_ids over the 16 shard receipts of the full ingest (241,930 records, 0 failed); excludes the sentinel/timing test chunks that the stores also hold",
        "source": [f"{MAIN}/ingest/receipts-summary.jsonl", f"{SEC}/ingest/receipts-summary.txt"],
        "arms": {a: {**cc[a]} for a in MAIN_ARMS},
        "section_arms": {a: {"chunks": v, "docs": 241930, "docs_failed": 0, "shards": 16} for a, v in cc_sec.items()},
        "section_arm_assignment": ("receipts-summary.txt carries task ids, not arm names. ft512o64s = 859,087 and "
                                   "sent512s = 850,119 are the figures the Addendum C RESULTS prints; sent512sh = 850,119 "
                                   "(the second identical total: the heading prefix changes only embedded text) and "
                                   "sent512rc = 868,479 are assigned by elimination."),
        "store_counts_note": ("The main-run RESULTS table prints store counts that include test chunks "
                              "(705,204 / 648,801 / 677,580 / 439,153 / 830,791 / 868,937); those are not in any "
                              "private JSON and are not reproduced here."),
    }
    out["question_sets"]["records_ingested_per_arm"] = 241930

    # ---------------- noise run
    noise = {}
    for s, d in MAIN_DIRS.items():
        nz = src.json(f"{MAIN}/settings/{d}/noise.json")
        hw = nz["half_width_at_planned_n"]
        noise[s] = {"source": f"{MAIN}/settings/{d}/noise.json", "n_subset": nz["n_subset"],
                    "baseline_hit5": nz["baseline_hit"], "ft512o0_hit5": nz["arm_hit"],
                    "paired_diff_ft512o0_minus_ft512o64": nz["paired_diff"],
                    "discordance": r(nz["discordance"], 4), "planned_n": nz["planned_n"],
                    "half_width_at_planned_n": hw,
                    "half_width_at_n2035_scaled": {"recomputed": True, "value": r(hw * math.sqrt(nz["planned_n"] / 2035), 2),
                                                    "how": "half_width_at_planned_n x sqrt(planned_n / 2035)"},
                    "floor": nz["floor"], "margins": nz["margins"]}
    out["noise_run"] = {"status": "confirmatory (sets the frozen margins)", "settings": noise}

    # ---------------- per-setting per-arm tables
    settings: dict[str, Any] = {}
    cross: list[dict] = []
    scores_main = {s: (main_a_score if s == "a" else src.json(f"{MAIN}/settings/{d}/score.json"))
                   for s, d in MAIN_DIRS.items()}
    scores_sec = {s: src.json(f"{SEC}/settings/{d}/score.json") for s, d in SEC_DIRS.items()}
    for s in "adbcef":
        tab = {}
        for arm in ALL_ARMS:
            if s in MAIN_DIRS and arm in MAIN_ARMS:
                sc, rel = scores_main[s], f"{MAIN}/settings/{MAIN_DIRS[s]}/score.json"
            else:
                sc, rel = scores_sec[s], f"{SEC}/settings/{SEC_DIRS[s]}/score.json"
            st = sc["sets"][DECISION_SET]
            h = {k: st["primary"][k]["pooled"]["hit"][arm] for k in ("1", "5", "10")}
            tab[arm] = {"n": st["n"], "hit@1": r(h["1"]["point"]), "hit@5": r(h["5"]["point"]),
                        "hit@10": r(h["10"]["point"]),
                        "hit@5_ci95": [r(h["5"]["lo"]), r(h["5"]["hi"])],
                        "ctx_tokens@5": st["context_tokens"]["5"][arm],
                        "sensitivity_hit@5": r(sc["sets"][SENS_SET]["primary"]["5"]["pooled"]["hit"][arm]["point"]),
                        "sensitivity_n": sc["sets"][SENS_SET]["n"],
                        "source": rel}
            if s in MAIN_DIRS and arm in MAIN_ARMS:  # the sections score re-scored the old arms
                o = scores_sec[s]["sets"][DECISION_SET]["primary"]
                same = all(o[k]["pooled"]["hit"][arm]["point"] == h[k]["point"] for k in ("1", "5", "10"))
                cross.append({"setting": s, "arm": arm, "main_run_equals_sections_rescore": same})
        settings[s] = {"label": SETTING_LABEL[s],
                       "status": "confirmatory" if s in "adbc" else "exploratory",
                       "context_tokens_estimated": "chars/4",
                       "bootstrap": scores_sec[s]["bootstrap"], "arms": tab}
    out["settings"] = settings
    out["cross_check_old_arms_rescored"] = {"all_equal": all(c["main_run_equals_sections_rescore"] for c in cross),
                                            "n_checked": len(cross)}

    # ---------------- confirmatory: frozen main run
    decisions = {}
    for s, d in MAIN_DIRS.items():
        rel = f"{MAIN}/settings/{d}/decision.json"
        dj = src.json(rel)
        decisions[s] = dj
        for arm in MAIN_ARMS[1:]:
            a = dj["arms"][arm]
            add(entry(f"main_{s}_{arm}_minus_ft512o64", status="confirmatory", setting=s,
                      pair=f"{arm} - ft512o64", src=rel, n=a["n"], point=a["point"], lo=a["lo"], hi=a["hi"],
                      adjustment="Holm across the 5 challengers, one-sided alpha 0.025 (p-values); interval is the unadjusted 95% paired bootstrap",
                      p_one_sided=a["p_one_sided"], p_holm=a["p_holm"], cost_class=a["cost_class"],
                      test=a["test"], test_class=a["test_class"], threshold=a["threshold"],
                      context_ratio=a["context_ratio"], context_rule=a["context_rule"],
                      passed=a["pass"], fail_reasons=a["fail_reasons"],
                      decision_role="primary" if s == "a" else "secondary report"))
            sa = dj["sensitivity"]["arms"][arm]
            add(entry(f"main_{s}_{arm}_minus_ft512o64_sens", status="confirmatory", setting=s,
                      pair=f"{arm} - ft512o64", src=rel, set_name=SENS_SET,
                      n=scores_main[s]["sets"][SENS_SET]["n"], point=sa["point"], lo=sa["lo"], hi=sa["hi"],
                      adjustment="Holm across the 5 challengers (p-values); unadjusted 95% interval",
                      p_holm=sa["p_holm"], passed=sa["pass"], fail_reasons=sa["fail_reasons"],
                      decision_role="sensitivity (not the decision)"))
        for i, stp in enumerate(dj["ladder"]["steps"], 1):
            add(entry(f"main_{s}_ladder{i}_{stp['candidate']}_minus_{stp['incumbent']}", status="confirmatory",
                      setting=s, pair=stp["pair"], src=rel, n=stp["n"], point=stp["point"], lo=stp["lo"],
                      hi=stp["hi"], adjustment="none (paired-ladder tie-break: switch iff 95% lower bound > 0)",
                      switched=stp["switched"]))
    out["main_run_decision"] = {
        s: {"source": f"{MAIN}/settings/{d}/decision.json", "winner": decisions[s]["winner"],
            "passing_order": decisions[s]["passing_order"], "margins": decisions[s]["margins"],
            "context_ratio_rule": decisions[s]["context_ratio"], "alpha_one_sided": decisions[s]["alpha_one_sided"],
            "sensitivity_agrees": decisions[s]["sensitivity"]["agrees_with_decision"],
            "margins_match_noise": decisions[s]["margins_match_noise"]}
        for s, d in MAIN_DIRS.items()}

    # ---------------- addendum C
    for which, set_name in (("decision_set", DECISION_SET), ("sensitivity_set", SENS_SET)):
        for s in "adbc":
            blk = addc[which][s]
            for c, v in blk["contrasts"].items():
                add(entry(f"addC_{s}_{c}" + ("" if which == "decision_set" else "_sens"), status="addendum-C",
                          setting=s, pair=v["pair"], src=f"{SEC}/addendum_c.json", set_name=set_name,
                          n=v["n"], point=v["point"], lo=v["lo"], hi=v["hi"],
                          adjustment="Holm across the four pre-specified contrasts C1-C4 (two-sided p); interval is the unadjusted 95% paired bootstrap",
                          p=v["p"], p_holm=v["p_holm"], holm_significant=v["holm_sig"], contrast_label=c))
            if which == "decision_set":
                for i, stp in enumerate(blk["step3"], 1):
                    cand, inc = [x.strip() for x in stp["pair"].split(" - ")]
                    add(entry(f"addC_{s}_step3_{i}_{cand}_minus_{inc}", status="addendum-C", setting=s,
                              pair=stp["pair"], src=f"{SEC}/addendum_c.json", n=blk["n"], point=stp["point"],
                              lo=stp["lo"], hi=stp["hi"],
                              adjustment="none (step 3 replacement: switch iff 95% lower bound > 0)",
                              switched=stp["switched"]))
    sec_dec = {}
    for s in "adbc":
        rel = f"{SEC}/settings/{SEC_DIRS[s]}/decision.json"
        dj = src.json(rel)
        sec_dec[s] = {"source": rel, "passing_vs_baseline": dj["passing_order"],
                      "final_after_step3": addc["decision_set"][s]["final"],
                      "winner_before": addc["decision_set"][s]["winner_before"]}
        for arm in ("ft512o64s", "sent512s", "sent512sh"):
            a = dj["arms"][arm]
            add(entry(f"addC_{s}_step2_{arm}_minus_ft512o64", status="addendum-C", setting=s,
                      pair=f"{arm} - ft512o64", src=rel, n=a["n"], point=a["point"], lo=a["lo"], hi=a["hi"],
                      adjustment="Holm across the 3 section candidates (step 2), one-sided alpha 0.025 (p-values); unadjusted 95% interval",
                      p_one_sided=a["p_one_sided"], p_holm=a["p_holm"], test=a["test"], threshold=a["threshold"],
                      context_ratio=a["context_ratio"], passed=a["pass"], fail_reasons=a["fail_reasons"]))
    out["addendum_c_decision"] = sec_dec

    # ---------------- post hoc: size_overlap (hypotheses A, B)
    rel = f"{SEC}/posthoc/size_overlap.json"
    for s in "adbc":
        A = so["settings"][s]["A_size_ft1024o0_minus_ft512o0"]
        add(entry(f"ph_A_{s}_pooled", status="exploratory", setting=s, pair="ft1024o0 - ft512o0", src=rel,
                  **ci(A["pooled"], 2)))
        for dim in ("section_type", "length_band", "digit_share", "mean_word_len", "nonword_token_share", "echo"):
            for val, v in A[dim].items():
                if v is None:
                    continue
                add(entry(f"ph_A_{s}_{dim}_{val}", status="exploratory", setting=s, pair="ft1024o0 - ft512o0",
                          src=rel, subgroup={"dimension": dim, "value": val,
                                             "kind": "tercile of the gold-quote proxy" if dim not in ("section_type", "length_band") else "stratum"},
                          **ci(v, 2)))
        B = so["settings"][s]["B_overlap_ft512o64_minus_ft512o0"]
        names = {"pooled": "pooled", "quote_straddles_o0_boundary": "split_in_ft512o0",
                 "quote_inside_one_o0_chunk": "inside_one_ft512o0_chunk",
                 "straddle_and_inside_one_o64_chunk": "split_in_ft512o0_and_inside_one_ft512o64_chunk"}
        for k, nm in names.items():
            add(entry(f"ph_B_{s}_{nm}", status="exploratory", setting=s, pair="ft512o64 - ft512o0", src=rel,
                      note=("sign-reversed restatement of the confirmatory main_%s_ft512o0_minus_ft512o64" % s) if k == "pooled" else None,
                      subgroup=None if k == "pooled" else {"dimension": "gold quote vs ft512o0 chunk boundary", "value": nm},
                      **ci(B[k], 2)))
        add(entry(f"ph_B_{s}_ft1024o0_minus_ft512o64", status="exploratory", setting=s, pair="ft1024o0 - ft512o64",
                  src=rel, note="same contrast as the confirmatory main_%s_ft1024o0_minus_ft512o64" % s,
                  **ci(B["ft1024o0_minus_ft512o64"], 2)))
        out.setdefault("posthoc_ctx_tokens@5", {})[s] = so["settings"][s]["ctx5"]

    # ---------------- post hoc: overlap_artifact (hypothesis D)
    rel = f"{SEC}/posthoc/overlap_artifact.json"
    for s in "adbc":
        blk = oa["settings"][s]
        for thr, v in blk["coverage_thresholds"].items():
            add(entry(f"ph_D_{s}_coverage_{slug(thr)}", status="exploratory", setting=s, pair="ft512o64 - ft512o0",
                      src=rel, metric=("hit@5 at coverage " + thr) if thr.startswith(">=") else
                      ("hit@5 requiring the whole quote inside one chunk" if thr == "strict_single_chunk"
                       else "mean quote coverage at k=5, x100"), **ci(v, 2)))
        art = blk["article_hit5"]
        add(entry(f"ph_D_{s}_article_hit5", status="exploratory", setting=s, pair="ft512o64 - ft512o0", src=rel,
                  metric="article-level hit@5 (any top-5 chunk from the gold article)",
                  arm_values={"ft512o0": art["ft512o0"], "ft512o64": art["ft512o64"]}, **ci(art["diff"], 2)))
        add(entry(f"ph_D_{s}_article_hit5_split_in_ft512o0", status="exploratory", setting=s,
                  pair="ft512o64 - ft512o0", src=rel, metric="article-level hit@5",
                  subgroup={"dimension": "gold quote vs ft512o0 chunk boundary", "value": "split_in_ft512o0"},
                  **ci(art["diff_on_straddled"], 2)))
        out.setdefault("posthoc_split_quote_mean_coverage_ft512o0", {})[s] = blk["straddled_coverage_o0_mean"]

    # ---------------- post hoc: rerank_spread (hypotheses C1, C2)
    rs = src.json(f"{SEC}/posthoc/rerank_spread.json")
    rel = f"{SEC}/posthoc/rerank_spread.json"
    spread = {"source": rel, "status": "exploratory", "n": rs["n"],
              "settings": {s: {k: v for k, v in blk.items() if k != "hit5"} for s, blk in rs["settings"].items()},
              "pairs": {}}
    for pname, blk in rs["pairs"].items():
        spread["pairs"][pname] = {
            "reranked_setting": blk["reranked"], "not_reranked_setting": blk["not_reranked"],
            "sd_ratio_reranked_over_not": {**blk["sd_ratio"],
                                           "method": {"type": "bootstrap over questions of SD-across-arms ratio",
                                                      "resamples": ITERS, "seed": 0, "rng": "numpy default_rng",
                                                      "ci_level": 0.95}},
            "median_abs_pairwise_gap": blk["median_abs_pairwise_gap"],
            "pairwise_slope_through_origin": blk["pairwise_slope_through_origin"],
            "pairwise_corr": blk["pairwise_corr"], "rank_spearman": blk["rank_spearman"],
            "reranker_gain_range": blk["gain_range"]}
        for arm, v in blk["gain_per_arm"].items():
            add(entry(f"ph_C_gain_{blk['reranked']}_vs_{blk['not_reranked']}_{arm}", status="exploratory",
                      setting=f"{blk['reranked']} vs {blk['not_reranked']}", pair=f"{arm}: reranked - not reranked",
                      src=rel, n=rs["n"], **{k: v[k] for k in ("point", "lo", "hi")}))
        for cname, v in blk["contrasts"].items():
            kind, pair = cname.rsplit(" ", 1)
            x, y = pair.split("-")
            for side, s in (("reranked", blk["reranked"]), ("not_reranked", blk["not_reranked"])):
                add(entry(f"ph_C_{s}_{slug(kind)}_{x}_minus_{y}", status="exploratory", setting=s,
                          pair=f"{x} - {y}", src=rel, n=rs["n"], **{k: v[side][k] for k in ("point", "lo", "hi")}))
    out["spread"] = spread

    # ---------------- recomputed contrasts (no frozen file holds them)
    # self-check: the harness bootstrap must reproduce three frozen intervals exactly
    pq_sec = {s: (pq_a if s == "a" else src.jsonl(f"{SEC}/settings/{SEC_DIRS[s]}/per_question.jsonl.xz")) for s in "adbc"}
    pq_main = {s: src.jsonl(f"{MAIN}/settings/{d}/per_question.jsonl.xz") for s, d in MAIN_DIRS.items()}
    checks = []
    n_a, v_a = vectors(pq_sec["a"], set_rows, ALL_ARMS)
    sec_a_dec = src.json(f"{SEC}/settings/a-hybrid-rerank/decision.json")
    for (x, y), ref in ((("ft512o64s", "ft512o64"), sec_a_dec["arms"]["ft512o64s"]),
                        (("sent512", "ft512o64"), decisions["a"]["arms"]["sent512"])):
        d = paired_diff(v_a[x], v_a[y], ITERS, SEED)
        checks.append({"pair": f"{x} - {y}", "setting": "a",
                       "reproduced": all(abs(d[k] - ref[k]) < 5e-5 for k in ("point", "lo", "hi"))})
    n_b, v_b = vectors(pq_main["b"], set_rows, MAIN_ARMS)
    d = paired_diff(v_b["sempool512"], v_b["sent512"], ITERS, SEED)
    ref = decisions["b"]["ladder"]["steps"][2]
    checks.append({"pair": "sempool512 - sent512", "setting": "b",
                   "reproduced": all(abs(d[k] - ref[k]) < 5e-5 for k in ("point", "lo", "hi"))})
    if not all(c["reproduced"] for c in checks):
        raise SystemExit(f"REFUSED: harness bootstrap does not reproduce frozen intervals: {checks}")
    out["recompute_selfcheck"] = {"what": "paired_diff(seed 0, 10,000) re-derives frozen decision.json intervals to 4 dp before any new interval is computed",
                                  "checks": checks}
    recompute_note = ("recomputed here from per_question.jsonl.xz with the harness's paired_diff "
                      "(set_rows order, seed 0, 10,000 resamples); no frozen file holds it")
    for s in "adbc":
        n, v = vectors(pq_sec[s], set_rows, ALL_ARMS)
        for x, y in (("sempool512", "ft512o64s"), ("sem512", "ft512o64s"), ("sempool512", "ft512o64")):
            d = paired_diff(v[x], v[y], ITERS, SEED)
            add(entry(f"rc_{s}_{x}_minus_{y}", status="exploratory", setting=s, pair=f"{x} - {y}",
                      src=f"{SEC}/settings/{SEC_DIRS[s]}/per_question.jsonl.xz", recomputed=True, note=recompute_note,
                      **ci(d)))
        n, v = vectors(pq_main[s], set_rows, MAIN_ARMS)
        d = paired_diff(v["sempool512"], v["sent512"], ITERS, SEED)
        add(entry(f"rc_{s}_sempool512_minus_sent512", status="exploratory", setting=s, pair="sempool512 - sent512",
                  src=f"{MAIN}/settings/{MAIN_DIRS[s]}/per_question.jsonl.xz", recomputed=True,
                  note=recompute_note + " (main-run RESULTS 'analysis, not the rule')", **ci(d)))

    # ---------------- strata (per-stratum contrasts vs baseline; stratum guard)
    strata: dict[str, Any] = {"status_note": "status follows the arm: main-run arms confirmatory (guard is part of the frozen rule), section arms addendum-C",
                              "interval": "95% paired bootstrap (score.json); the guard itself uses a Bonferroni (1 - 0.05/k) interval, reported under guard",
                              "settings": {}}
    for s in "adbc":
        blk = {}
        for arm in ALL_ARMS[1:]:
            if arm in MAIN_ARMS:
                sc, rel_s = scores_main[s], f"{MAIN}/settings/{MAIN_DIRS[s]}/score.json"
                dj, rel_d = decisions[s], f"{MAIN}/settings/{MAIN_DIRS[s]}/decision.json"
            else:
                sc, rel_s = scores_sec[s], f"{SEC}/settings/{SEC_DIRS[s]}/score.json"
                dj = src.json(f"{SEC}/settings/{SEC_DIRS[s]}/decision.json")
                rel_d = f"{SEC}/settings/{SEC_DIRS[s]}/decision.json"
                if arm not in dj["arms"]:
                    dj = None
            st = sc["sets"][DECISION_SET]["primary"]["5"]["strata"]
            per = {}
            for dim, vals in st.items():
                for val, cell in vals.items():
                    if arm in cell["diff"]:
                        per[f"{dim}={val}"] = ci(cell["diff"][arm])
            gd = None
            if dj is not None:
                a = dj["arms"][arm]
                det = a.get("stratum_guard_detail") or {}
                gd = {"source": rel_d, "passes": a["stratum_guard"], "failures": a["stratum_failures"],
                      "k": det.get("k"), "level": det.get("level"), "correction": det.get("correction")}
            blk[f"{arm}_minus_ft512o64"] = {"source": rel_s, "cells": per, "guard": gd}
        strata["settings"][s] = blk
    out["strata"] = strata

    out["contrasts"] = dict(sorted(contrasts.items()))
    out["entry_counts"] = dict(sorted(Counter(v["status"] for v in contrasts.values()).items()))
    out["entry_counts_recomputed"] = sum(1 for v in contrasts.values() if v["recomputed"])
    out["markdown_checks"] = markdown_checks(out)
    out["sources_sha256"] = dict(sorted(src.sha.items()))
    return out


# --------------------------------------------------------------------------- markdown checks
P = "docs/plans/results/oa-chunking-eval-2026-10/POSTHOC-HYPOTHESES-2026-11-06.md"
RM = "main-run-2026-11/RESULTS.md (private)"
RS = "sections-2026-11/RESULTS.md (private)"
C = "contrasts"
# (pointer, markdown file, printed figure, scale). scale multiplies the pointed value first.
MARKDOWN_CHECKS: list[tuple[str, str, str, float]] = []


def _ci(pointer, file, point, lo=None, hi=None):
    MARKDOWN_CHECKS.append((pointer + ".point", file, point, 1))
    if lo is not None:
        MARKDOWN_CHECKS.append((pointer + ".lo", file, lo, 1))
        MARKDOWN_CHECKS.append((pointer + ".hi", file, hi, 1))


# POSTHOC: semantic vs sections (recomputed)
_ci(f"{C}.rc_a_sempool512_minus_ft512o64s", P, "-2.21", "-3.73", "-0.69")
_ci(f"{C}.rc_a_sem512_minus_ft512o64s", P, "-3.34", "-4.91", "-1.87")
_ci(f"{C}.rc_b_sempool512_minus_ft512o64s", P, "-0.15")
_ci(f"{C}.rc_b_sem512_minus_ft512o64s", P, "+0.29")
_ci(f"{C}.rc_b_sempool512_minus_ft512o64", P, "+7.76")
_ci(f"{C}.rc_d_sempool512_minus_ft512o64", P, "+3.4")
_ci(f"{C}.rc_a_sempool512_minus_ft512o64", P, "+1.3")
# main RESULTS head-to-head (recomputed)
_ci(f"{C}.rc_a_sempool512_minus_sent512", RM, "+0.74", "-0.69", "+2.16")
_ci(f"{C}.rc_d_sempool512_minus_sent512", RM, "+1.03", "-0.74", "+2.75")
_ci(f"{C}.rc_b_sempool512_minus_sent512", RM, "+4.08", "+2.21", "+5.95")
_ci(f"{C}.rc_c_sempool512_minus_sent512", RM, "-2.65", "-4.23", "-1.08")
# main RESULTS confirmatory diff column
for _s, _rows in {
    "a": [("ft512o0", "-2.06", "-3.49", "-0.59"), ("sent512", "+0.54", "-0.74", "+1.87"), ("ft1024o0", "-2.36", "-4.03", "-0.79"),
          ("sempool512", "+1.28", "-0.20", "+2.75"), ("sem512", "+0.15", "-1.38", "+1.67")],
    "d": [("ft512o0", "-1.28", "-2.95", "+0.34"), ("sent512", "+2.36", "+0.74", "+3.98"), ("ft1024o0", "+1.08", "-0.74", "+2.85"),
          ("sempool512", "+3.39", "+1.62", "+5.16"), ("sem512", "+1.67", "-0.20", "+3.54")],
    "b": [("ft512o0", "+1.38", "-0.29", "+3.10"), ("sent512", "+3.69", "+1.96", "+5.41"), ("ft1024o0", "-4.42", "-6.39", "-2.46"),
          ("sempool512", "+7.76", "+5.80", "+9.78"), ("sem512", "+8.21", "+6.24", "+10.22")],
    "c": [("ft512o0", "-2.21", "-3.64", "-0.79"), ("sent512", "-1.28", "-2.60", "+0.00"), ("ft1024o0", "+3.10", "+1.52", "+4.67"),
          ("sempool512", "-3.93", "-5.55", "-2.36"), ("sem512", "-6.34", "-7.96", "-4.77")]}.items():
    for _arm, _p, _lo, _hi in _rows:
        _ci(f"{C}.main_{_s}_{_arm}_minus_ft512o64", RM, _p, _lo, _hi)
# Addendum C: C1-C4 (sections RESULTS + POSTHOC)
for _s, _rows in {
    "a": [("C1", "+2.51", "+1.13", "+3.88"), ("C2", "+3.49", "+2.11", "+4.86"), ("C3", "-0.20", "-0.84", "+0.39"), ("C4", "+0.84", "-0.39", "+2.11")],
    "d": [("C1", "+2.85", "+1.13", "+4.57"), ("C2", "+4.13", "+2.36", "+5.95"), ("C3", "+0.88", "-0.05", "+1.82"), ("C4", "+2.26", "+0.64", "+3.88")],
    "b": [("C1", "+6.09", "+4.18", "+7.96"), ("C2", "+7.91", "+5.90", "+9.93"), ("C3", "+0.25", "-0.93", "+1.43"), ("C4", "+4.82", "+3.05", "+6.58")],
    "c": [("C1", "+0.64", "-0.88", "+2.16"), ("C2", "-0.29", "-1.72", "+1.13"), ("C3", "0.00", None, None), ("C4", "+0.84", "-0.44", "+2.16")]}.items():
    for _c, _p, _lo, _hi in _rows:
        _ci(f"{C}.addC_{_s}_{_c}", RS, _p, _lo, _hi)
_ci(f"{C}.addC_a_C2_sens", RS, "+3.04", "+1.76", "+4.33")
_ci(f"{C}.addC_a_step3_2_sent512s_minus_ft512o64s", RS, "-0.44", "-1.38", "+0.49")
_ci(f"{C}.addC_a_step3_3_sent512sh_minus_ft512o64s", RS, "-0.64", "-1.67", "+0.34")
_ci(f"{C}.addC_b_step3_2_sent512s_minus_sempool512", RS, "+2.01", "+0.10", "+3.98")
# POSTHOC hypothesis A
for _s, _p, _lo, _hi in (("b", "-5.80", "-7.67", "-3.93"), ("d", "+2.36", "+0.64", "+4.08"),
                         ("c", "+5.31", "+3.78", "+6.83"), ("a", "-0.29", "-1.82", "+1.23")):
    _ci(f"{C}.ph_A_{_s}_pooled", P, _p, _lo, _hi)
for _k, _p, _lo, _hi in (("section_type_methods", "-13.9", "-18.5", "-9.5"), ("section_type_results", "-10.4", "-15.0", "-5.9"),
                         ("section_type_introduction", "-7.1", "-11.6", "-2.6"), ("section_type_discussion", "-2.4", None, None),
                         ("section_type_abstract", "+0.8", None, None), ("digit_share_high", "-10.1", "-13.3", "-6.8"),
                         ("digit_share_low", "-3.6", "-6.6", "-0.6"), ("nonword_token_share_high", "-9.3", None, None),
                         ("nonword_token_share_low", "-3.8", None, None), ("length_band_long", "-8.0", None, None),
                         ("length_band_short", "-2.5", None, None), ("echo_high", "-8.3", None, None), ("echo_low", "-3.3", None, None)):
    _ci(f"{C}.ph_A_b_{_k}", P, _p, _lo, _hi)
_ci(f"{C}.ph_A_d_echo_high", P, "+1.3")
_ci(f"{C}.ph_A_d_echo_low", P, "+5.1")
_ci(f"{C}.ph_A_a_section_type_discussion", P, "-3.9", "-7.4", "-0.3")
_ci(f"{C}.ph_A_a_section_type_introduction", P, "+3.7")
# POSTHOC hypothesis B
for _s, _rows in {"a": [("pooled", "+2.06", "+0.59", "+3.49"), ("split_in_ft512o0", "+34.5", "+25.2", "+43.7"),
                        ("inside_one_ft512o0_chunk", "+0.05", "-1.36", "+1.46")],
                  "d": [("pooled", "+1.28", None, None), ("split_in_ft512o0", "+30.3", None, None), ("inside_one_ft512o0_chunk", "-0.52", None, None)],
                  "b": [("pooled", "-1.38", None, None), ("split_in_ft512o0", "+19.3", None, None), ("inside_one_ft512o0_chunk", "-2.66", "-4.38", "-0.94")],
                  "c": [("pooled", "+2.21", "+0.79", "+3.64"), ("split_in_ft512o0", "+31.9", None, None), ("inside_one_ft512o0_chunk", "+0.37", None, None)]}.items():
    for _k, _p, _lo, _hi in _rows:
        _ci(f"{C}.ph_B_{_s}_{_k}", P, _p, _lo, _hi)
_ci(f"{C}.ph_B_a_ft1024o0_minus_ft512o64", P, "-2.36", "-4.03", "-0.79")
MARKDOWN_CHECKS += [
    ("construction.chunk_geometry_in_stores.ft512o0.mean_chars", P, "1,771", 1),
    ("construction.chunk_geometry_in_stores.ft512o64.mean_chars", P, "1,781", 1),
    ("construction.chunk_geometry_in_stores.ft1024o0.mean_chars", P, "3,451", 1),
    ("construction.chunk_geometry_in_stores.ft512o0.quote_in_one_chunk", P, "94.15", 100),
    ("construction.chunk_geometry_in_stores.ft512o64.quote_in_one_chunk", P, "99.75", 100),
    ("construction.chunk_geometry_in_stores.ft1024o0.quote_in_one_chunk", P, "97.3", 100),
    ("construction.gold_quote_split_by_boundary.ft512o0", P, "5.85", 100),
    ("construction.gold_quote_split_by_boundary.ft1024o0", P, "2.70", 100),
    ("construction.gold_quote_split_by_boundary.ft512o64", P, "0.25", 100),
    ("construction.gold_quote_chars.median", P, "119", 1),
    ("construction.gold_quote_chars.p10", P, "54", 1),
    ("construction.gold_quote_chars.p90", P, "214", 1),
    ("construction.overlap_width_chars_ft512o64.median", P, "229", 1),
    ("construction.overlap_width_chars_ft512o64.p10", P, "163", 1),
    ("construction.overlap_width_chars_ft512o64.p90", P, "290", 1),
    ("construction.passage_chars.median", P, "1,242", 1),
    ("construction.passage_chars.p10", P, "979", 1),
    ("construction.passage_chars.p90", P, "1,371", 1),
    ("construction.passage_section_type.counts.abstract", P, "657", 1),
    ("construction.passage_section_type.counts.methods", P, "482", 1),
    ("construction.passage_section_type.counts.results", P, "471", 1),
    ("construction.passage_section_type.counts.introduction", P, "467", 1),
    ("construction.passage_section_type.counts.discussion", P, "430", 1),
    ("construction.passage_section_type.counts.body", P, "85", 1),
    ("construction.gold_quote_crosses_section_boundary.crossing", P, "0", 1),
    ("question_sets.passages.n_passages", P, "2,592", 1),
    ("question_sets.questions.selected", P, "2,437", 1),
    ("question_sets.questions.passages_failed_all_attempts", P, "155", 1),
    ("question_sets.clean_set", P, "2,335", 1),
    ("question_sets.audit.ambiguous_gold", P, "63", 1),
    ("question_sets.audit.within_record_repeat", P, "66", 1),
    ("question_sets.decision_set", P, "2,035", 1),
    ("question_sets.sample.n_sampled_articles", P, "22,600", 1),
    ("question_sets.decision_set_without_lossy_strata", RM, "1,836", 1),
    ("chunk_counts.arms.ft512o64.chunks", RS, "705,047", 1),
    ("chunk_counts.arms.sent512.chunks", RS, "677,432", 1),
    ("chunk_counts.section_arms.ft512o64s.chunks", RS, "859,087", 1),
    ("chunk_counts.section_arms.sent512s.chunks", RS, "850,119", 1),
    ("noise_run.settings.a.half_width_at_planned_n", RM, "1.15", 1),
    ("noise_run.settings.d.half_width_at_planned_n", RM, "1.66", 1),
    ("noise_run.settings.b.half_width_at_planned_n", RM, "1.70", 1),
    ("noise_run.settings.c.half_width_at_planned_n", RM, "1.34", 1),
    ("noise_run.settings.a.half_width_at_n2035_scaled.value", "PLAN-AND-PASS-RULE.md Addendum B item 3", "1.22", 1),
    ("noise_run.settings.b.half_width_at_n2035_scaled.value", "PLAN-AND-PASS-RULE.md Addendum B item 3", "1.79", 1),
    ("noise_run.settings.c.half_width_at_n2035_scaled.value", "PLAN-AND-PASS-RULE.md Addendum B item 3", "1.41", 1),
    ("spread.pairs.C1_hybrid.sd_ratio_reranked_over_not.point", P, "0.90", 1),
    ("spread.pairs.C1_hybrid.sd_ratio_reranked_over_not.lo", P + " (C1 prose)", "0.68", 1),
    ("spread.pairs.C1_hybrid.sd_ratio_reranked_over_not.lo", P + " (C2 table)", "0.67", 1),
    ("spread.pairs.C1_hybrid.sd_ratio_reranked_over_not.hi", P, "1.19", 1),
    ("spread.pairs.C2_vector.sd_ratio_reranked_over_not.point", P, "0.75", 1),
    ("spread.pairs.C2_vector.sd_ratio_reranked_over_not.lo", P, "0.65", 1),
    ("spread.pairs.C2_vector.sd_ratio_reranked_over_not.hi", P, "0.88", 1),
    ("spread.pairs.C2_bm25.sd_ratio_reranked_over_not.point", P, "0.60", 1),
    ("spread.pairs.C2_bm25.sd_ratio_reranked_over_not.lo", P, "0.45", 1),
    ("spread.pairs.C2_bm25.sd_ratio_reranked_over_not.hi", P, "0.81", 1),
    ("spread.settings.b.sd_across_arms", P, "4.75", 1),
    ("spread.settings.d.sd_across_arms", P, "2.29", 1),
    ("spread.settings.a.sd_across_arms", P, "2.05", 1),
    ("spread.settings.e.sd_across_arms", P, "3.58", 1),
    ("spread.settings.f.sd_across_arms", P, "1.50", 1),
    ("spread.settings.a.best_minus_worst", P, "5.9", 1),
    ("spread.settings.d.best_minus_worst", P, "7.4", 1),
    ("spread.settings.b.best_minus_worst", P, "14.5", 1),
    ("spread.pairs.C1_hybrid.reranker_gain_range.0", P, "14.3", 1),
    ("spread.pairs.C1_hybrid.reranker_gain_range.1", P, "17.7", 1),
    ("spread.pairs.C2_vector.rank_spearman", P, "0.95", 1),
    ("spread.pairs.C2_bm25.rank_spearman", P, "0.76", 1),
    ("settings.a.arms.sempool512.hit@5", P, "80.29", 1),
    ("settings.a.arms.ft512o64.hit@5", P, "79.02", 1),
    ("settings.a.arms.ft512o64s.hit@5", P, "82.51", 1),
    ("settings.a.arms.sent512s.hit@5", P, "82.06", 1),
    ("settings.e.arms.ft512o64.hit@5", P, "70.91", 1),
    ("settings.f.arms.ft1024o0.hit@5", P, "73.56", 1),
]
_ci(f"{C}.ph_C_e_size_ft1024o0_minus_ft512o0", P, "-3.54", "-5.21", "-1.92")
_ci(f"{C}.ph_C_f_size_ft1024o0_minus_ft512o0", P, "+3.88", "+2.36", "+5.45")
_ci(f"{C}.ph_C_e_overlap_ft512o64_minus_ft512o0", P, "+1.28", "-0.25", "+2.75")
_ci(f"{C}.ph_C_f_overlap_ft512o64_minus_ft512o0", P, "+2.26", "+0.79", "+3.69")
_ci(f"{C}.ph_C_e_sections_ft512o64s_minus_ft512o64", P, "+5.31", "+3.69", "+6.93")
_ci(f"{C}.ph_C_f_sections_ft512o64s_minus_ft512o64", P, "+1.18", "-0.25", "+2.60")
_ci(f"{C}.ph_C_e_sections_sent512s_minus_sent512", P, "+3.24", "+1.67", "+4.82")
_ci(f"{C}.ph_C_f_sections_sent512s_minus_sent512", P, "+1.28", "-0.05", "+2.65")
_ci(f"{C}.ph_C_e_structure_sent512s_minus_sent512rc", P, "+2.11", "+0.69", "+3.54")
_ci(f"{C}.ph_C_f_structure_sent512s_minus_sent512rc", P, "+1.18", "-0.05", "+2.41")
_ci(f"{C}.ph_C_e_semantic_sempool512_minus_ft512o64", P, "+4.62", "+3.00", "+6.24")
_ci(f"{C}.ph_C_f_semantic_sempool512_minus_ft512o64", P, "-0.79", "-2.26", "+0.69")
# POSTHOC hypothesis D
for _k, _p, _lo, _hi in (("ge0p5", "+1.28", "-0.10", "+2.65"), ("ge0p7", "+1.82", "+0.44", "+3.24"),
                         ("ge0p9", "+2.06", "+0.59", "+3.49"), ("ge1p0", "+2.21", "+0.74", "+3.69"),
                         ("strict_single_chunk", "+3.59", "+2.01", "+5.11")):
    _ci(f"{C}.ph_D_a_coverage_{_k}", P, _p, _lo, _hi)
_ci(f"{C}.ph_D_a_article_hit5", P, "+0.64", "-0.39", "+1.67")
_ci(f"{C}.ph_D_d_article_hit5", P, "+0.34")
_ci(f"{C}.ph_D_b_article_hit5", P, "-0.29")
_ci(f"{C}.ph_D_c_article_hit5", P, "+0.64")
_ci(f"{C}.ph_D_c_article_hit5_split_in_ft512o0", P, "+9.2", "+4.2", "+15.1")
MARKDOWN_CHECKS.append(("posthoc_split_quote_mean_coverage_ft512o0.a", P, "0.54", 1))


MISMATCH_NOTES = {
    f"{C}.rc_d_sempool512_minus_sent512.hi": (
        "RESULTS prints +2.75; the harness stream (seed 0) gives +2.80. Point and lower bound match. "
        "Seed 1 or an index-resampling bootstrap also give +2.80, so the printed bound came from another "
        "bootstrap draw; the difference (0.05) is resampling noise, not a different contrast."),
    f"{C}.rc_c_sempool512_minus_sent512.hi": (
        "RESULTS prints -1.08; the harness stream (seed 0) gives -1.13. Point and lower bound match. "
        "Seed 1 and an index-resampling bootstrap give -1.08, so the printed bound came from another "
        "bootstrap draw; the difference (0.05) is resampling noise, not a different contrast."),
    "spread.pairs.C1_hybrid.sd_ratio_reranked_over_not.lo": (
        "POSTHOC's C1 prose prints 0.68; rerank_spread.json and POSTHOC's own C2 table give 0.67. "
        "The prose figure is a transcription error."),
}


def _resolve(doc, pointer):
    node = doc
    for seg in pointer.split("."):
        node = node[int(seg)] if isinstance(node, list) else node[seg]
    return node


def markdown_checks(doc) -> dict:
    rows, bad = [], []
    for pointer, file, printed, scale in MARKDOWN_CHECKS:
        txt = printed.replace(",", "").replace("+", "")
        nd = len(txt.split(".")[1]) if "." in txt else 0
        val = _resolve(doc, pointer)
        want = float(txt)
        # match = the printed figure is a correct rounding of the artifact value (ties either way,
        # since a 2-dp source value such as 30.25 legitimately prints as 30.3)
        ok = abs(float(val) * scale - want) <= 0.5 * 10 ** (-nd) + 1e-9
        row = {"pointer": pointer, "markdown": file, "printed": printed, "artifact_value": val, "match": ok}
        if not ok and pointer in MISMATCH_NOTES:
            row["explanation"] = MISMATCH_NOTES[pointer]
        rows.append(row)
        if not ok:
            bad.append(row)
    return {"n_checked": len(rows), "n_mismatch": len(bad), "mismatches": bad, "checks": rows}


# --------------------------------------------------------------------------- provenance / main
def provenance() -> dict:
    sys.path.insert(0, str(REPO_ROOT / "python"))
    import ragstack
    import ragstack.provenance as rp

    for m in (ragstack, rp):
        if not Path(m.__file__).resolve().is_relative_to(REPO_ROOT):
            raise SystemExit(f"REFUSED: {m.__name__} resolved outside {REPO_ROOT}: {m.__file__}")
    prov = rp.experiment_provenance(repo=REPO_ROOT)
    prov.pop("host", None)
    prov["warnings"] = [w.split(":", 1)[0] for w in prov.get("warnings") or []]
    return prov


def main(argv: list[str]) -> None:
    if len(argv) not in (2, 3):
        raise SystemExit(__doc__)
    root = Path(argv[1]).resolve()
    out = Path(argv[2]) if len(argv) == 3 else HERE / "oa-eval-aggregates.json"
    prov = provenance()  # first: refuse early if the wrong checkout is on the path
    doc = build(Src(root))
    doc["provenance"] = prov
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"entries": doc["entry_counts"], "recomputed": doc["entry_counts_recomputed"],
                      "markdown_mismatches": doc["markdown_checks"]["n_mismatch"],
                      "citable": prov.get("citable")}))


if __name__ == "__main__":
    main(sys.argv)
