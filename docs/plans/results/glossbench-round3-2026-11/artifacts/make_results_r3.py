#!/usr/bin/env python3
"""Build the counts-only public artifact ``results-r3.json`` from round 3's private harness outputs.

Round 3 of the glossary benchmark (attachment rule, prereg ``../prereg/PREREG-glossbench-round3.md``)
tested its family F (H1-H3) on set O and ran set A as a descriptive leg. Every number in the public
artifact is copied from a file a committed harness wrote:

* ``found10.py analyse`` (``analysis.round: 3``): ``results-found10-r3.json`` (the confirmatory run)
  and ``results-found10-r3-d5-sensitivity-EXPLORATORY.json`` (DEVIATIONS.md D-5, exploratory);
* the D-5 exclusion file (``d5-exclusions-O.json``): the census breakdown, counts only;
* ``wording_proxy.py`` (M2, a manipulation check): ``proxy-O.json`` and ``proxy-A.json``;
* ``found10.py run`` per arm: ``found10/<set>/<arm>.summary.json`` (depth, boost reading, census);
* the swap restores: ``swap/<set>/restore.json`` (census before and after);
* ``glossbench_r2_draft.py``: the draft / verify totals and acceptance summaries per batch.

It drops everything that is not a count, a share, a p value, a seed, a label, a model id, a stamp, a
commit or a sha256: no record id, chunk id, document id, question, quote, binding term, field name,
path, host, index, collection or endpoint is copied. Provenance blocks lose ``host``, ``image`` and
``installed_distribution``. It performs no analysis: the only derived values are sums of copied counts
(stated where they occur) and one boolean per arm (``value`` pass implies ``mention`` pass, counted from
the proxy's per-record booleans, which are not copied).

    python3 -I make_results_r3.py --root T3 --out results-r3.json

``T3`` is the round's private output root (it holds ``out/`` and ``swap/``). The output is
byte-deterministic for a given set of inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

CORPUS_ALIAS = {"O": "OA-1000", "A": "ASM-1000", "P": "papers-OC"}
PROVENANCE_DROP = {"host", "image", "installed_distribution"}
TEST_FIELDS = (
    "status", "type", "alternative", "contrast", "condition", "strata", "n", "n_records_population",
    "excluded_missing", "discordant", "gained", "lost", "net", "net_share", "ci", "ci_level", "p", "p_exact",
    "p_mid", "p_perm", "p_mcnemar", "p_mcnemar_two_sided", "p_holm", "p_holm_mid", "significant",
    "significant_mid", "significant_unadjusted", "min_attainable_p", "gate", "gate_reason", "sign",
    "mde_split_alpha", "mde_split_holm_worst", "n_pages", "n_pages_nonzero", "seed", "n_boot", "n_flips",
    "scored_not_found",
)
SUMMARY_COND_FIELDS = (
    "records", "rows", "ok", "failed", "found10", "requested", "served_min", "served_max", "depth_short",
    "depth_over", "exhausted_verified", "exhaustion_verdicts", "gate", "identical_to_norerank",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def public_provenance(prov: dict) -> dict:
    out = {k: v for k, v in prov.items() if k not in PROVENANCE_DROP}
    out["redacted"] = sorted(PROVENANCE_DROP)
    return out


def test_block(t: dict) -> dict:
    return {k: t[k] for k in TEST_FIELDS if k in t}


def tests(d: dict) -> dict:
    return {h: test_block(t) for h, t in d.items()}


def results_block(r: dict) -> dict:
    return {
        "schema_of_source": r["schema"],
        "citable": r["citable"],
        "test": r["test"],
        "analysis": r["analysis"],
        "family": {
            "condition": r["family"]["condition"],
            "corpora": [CORPUS_ALIAS[c] + f" (set {c})" for c in r["family"]["corpora"]],
            "order": r["family"]["order"],
            "hypotheses": tests(r["family"]["hypotheses"]),
        },
        "family_missing_as_not_found": {"hypotheses": tests(r["family_missing_as_not_found"]["hypotheses"])},
        "power_realised": r["power"]["realised"],
        "inputs_sha256": dict(r["inputs_sha256"]),
        "provenance": public_provenance(r["provenance"]),
    }


def secondary_block(r: dict) -> dict:
    s = r["secondary"]
    m_split = {}
    for k, v in sorted(s["m_num_split"].items()):
        m_split[k] = test_block(v) if isinstance(v, dict) else v
    return {
        "note": s.get("note"),
        "contrasts": {c: tests(v) for c, v in sorted(s["contrasts"].items())},
        "family|bm25": tests(s["family|bm25"]),
        "family|norerank": tests(s["family|norerank"]),
        "other_sets": {c: {cond: tests(v) for cond, v in sorted(cs.items())}
                       for c, cs in sorted(s["other_sets"].items())},
        "m_num_split": m_split,
    }


def proxy_block(p: dict) -> dict:
    viol = {"value_pass_mention_fail": 0, "rules_value_pass_rules_mention_fail": 0}
    for arms in p["per_record"].values():
        if arms.get("value", {}).get("pass") and not arms.get("mention", {}).get("pass"):
            viol["value_pass_mention_fail"] += 1
        if arms.get("rules-value", {}).get("pass") and not arms.get("rules-mention", {}).get("pass"):
            viol["rules_value_pass_rules_mention_fail"] += 1
    arms = {}
    for a, v in sorted(p["arms"].items()):
        row = {
            "chunks": v.get("chunks"),
            "chunks_with_field": v.get("chunks_with_field"),
            "sha256": v.get("sha256"),
            "glossary_versions": v.get("glossary_versions"),
            "documents": v.get("documents"),
        }
        sh = v.get("shuffle")
        if sh:
            row["shuffle"] = {k: sh[k] for k in ("scheme", "seed", "fixed_points", "moved", "populated",
                                                 "multiset_sha256", "permutation_sha256") if k in sh}
        arms[a] = row
    return {
        "schema_of_source": p["schema"],
        "citable": p["citable"],
        "totals": {a: {k: t[k] for k in ("n", "pass", "rate", "records_with_field") if k in t}
                   for a, t in sorted(p["totals"].items())},
        "by_stratum": p["by_stratum"],
        "subset_violations_counted_from_per_record": viol,
        "arms": arms,
        "provenance": public_provenance(p["provenance"]),
    }


def summary_block(s: dict) -> dict:
    g = (s.get("manipulation_check") or {}).get("glossary_boost") or {}
    sel = g.get("selection") or {}
    probes = g.get("probes") or []
    cen = s.get("census") or {}
    return {
        "arm": s["arm"],
        "run_label": s["run_label"],
        "calls": s["calls"],
        "failed_calls": s["failed_calls"],
        "citable": s["citable"],
        "commit": s["provenance"]["commit"],
        "census": {
            "source": cen.get("source"),
            "attach": cen.get("attach"),
            "glossary_file_sha256": cen.get("glossary_file_sha256"),
            "live_before": cen.get("live_before"),
            "live_after": cen.get("live_after"),
            "verified": cen.get("verified"),
            "swap_record_sha256": cen.get("swap_record_sha256"),
        },
        "boost": {
            "status": g.get("status"),
            "verdict": g.get("verdict"),
            "declared": g.get("declared"),
            "declared_label": g.get("declared_label"),
            "gate": g.get("gate"),
            "n_probes": g.get("n_probes"),
            "n_passed": g.get("n_passed"),
            "min_probes": g.get("min_probes"),
            "chunks_with_a_qualifying_token": sel.get("chunks_with_a_qualifying_token"),
            "chunks_with_glossary": sel.get("chunks_with_glossary"),
            "probe_results": sorted(x.get("result") for x in probes),
        },
        "conditions": {c: {k: x[k] for k in SUMMARY_COND_FIELDS if k in x}
                       for c, x in sorted(s["conditions"].items())},
    }


def restore_block(r: dict) -> dict:
    return {
        "utc_start": r["utc_start"],
        "utc_end": r["utc_end"],
        "census_before": r["census_before"],
        "census_after": r["census_after"],
        "apply": {k: r["apply"][k] for k in ("dry_run", "chunks", "chunks_written", "chunks_with_glossary",
                                              "docs", "reassembly_gaps", "reassembly_length_mismatches")
                  if k in r["apply"]},
    }


def totals_block(t: dict) -> dict:
    return {
        "set": t["set"],
        "stage": t["stage"],
        "status": t["status"],
        "reason": t.get("reason"),
        "model": t["model"],
        "served_models": t.get("served_models"),
        "rows": t.get("rows"),
        "row_status": t.get("row_status"),
        "totals_since_start": t["totals"]["since_start"],
        "cumulative_with_prior": t.get("cumulative_with_prior"),
        "cost_usd": t.get("cost_usd"),
        "config_sha256": t.get("config_sha256"),
        "pool_sha256": t.get("pool_sha256"),
        "prereg_sha256": t.get("prereg_sha256"),
        "updated": t.get("updated"),
        "citable": t.get("citable"),
        "commit": t["provenance"]["commit"],
    }


def acceptance_block(a: dict) -> dict:
    c = a["counts"]
    return {
        "set": a["set"],
        "status_label": a["status"],
        "pilot": a.get("pilot"),
        "draft_mode": a.get("draft_mode"),
        "accepted_sha256": a.get("accepted_sha256"),
        "counts": c,
        "excluded": a.get("excluded"),
        "layer1_value_bound": (a.get("layer1_rules") or {}).get("value_bound"),
        "layer2_by_verifier": a.get("layer2_by_verifier"),
        "verifier_refused": a.get("verifier_refused"),
        "rejects_by_code": a.get("rejects_by_code"),
        "round3": a.get("round3"),
        "citable": a.get("citable"),
        "commit": a["provenance"]["commit"],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, type=Path, help="round 3's private output root (holds out/ and swap/)")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    root = args.root

    src = {
        "results": "out/analysis/results-found10-r3.json",
        "sensitivity": "out/analysis/results-found10-r3-d5-sensitivity-EXPLORATORY.json",
        "exclusions": "out/analysis/d5-exclusions-O.json",
        "proxy_O": "out/proxy-O.json",
        "proxy_A": "out/A-run2/proxy-A.json",
        "found10_manifest": "out/found10/SHA256",
        "restore_O": "swap/O/restore.json",
        "restore_A": "swap/A/restore.json",
        "draft_P_pilot": "out/draft-totals-P-pilot.json",
        "verify_P_pilot_run2": "out/P-pilot-run2/verify-totals-P-pilot.json",
        "acceptance_P_pilot_run2": "out/P-pilot-run2/acceptance-summary-P-pilot.json",
        "draft_O": "out/draft-totals-O.json",
        "verify_O": "out/verify-totals-O.json",
        "acceptance_O": "out/acceptance-summary-O.json",
        "draft_A_segment1": "out/draft-totals-A.json",
        "draft_A_segment2": "out/A-run2/draft-totals-A.json",
        "verify_A": "out/A-run2/verify-totals-A.json",
        "acceptance_A": "out/A-run2/acceptance-summary-A.json",
    }
    paths = {k: root / v for k, v in src.items()}
    summaries = sorted((root / "out/found10").glob("*/*.summary.json"))
    source_sha = {v: sha256_file(paths[k]) for k, v in src.items()}
    for p in summaries:
        source_sha[str(p.relative_to(root))] = sha256_file(p)

    res = load(paths["results"])
    sen = load(paths["sensitivity"])
    exc = load(paths["exclusions"])
    assert res["schema"] == "glossbench-r3-found10-analysis/1", res["schema"]
    assert sen["schema"] == res["schema"]
    assert exc["schema"] == "glossbench-r3-d5-exclusions/1"

    cb = exc["census_breakdown"]
    census_beside = {"H1": {"M_num": cb["M_num"]}, "H2": {"V": cb["V"]}, "H3": cb}
    discriminating = {s: sum(v.values()) for s, v in cb.items()}

    cells: dict = {}
    for p in summaries:
        s = load(p)
        cells[f"{s['corpus']}/{s['run_label']}"] = summary_block(s)

    harness_citable = all([res["citable"], sen["citable"], load(paths["proxy_O"])["citable"],
                           load(paths["proxy_A"])["citable"]] + [c["citable"] for c in cells.values()])
    commits = sorted({res["provenance"]["commit"], sen["provenance"]["commit"]}
                     | {c["commit"] for c in cells.values()})

    out = {
        "schema": "glossbench-r3-results/1",
        "artifact_provenance": {
            "label": "RECORD - family F (H1-H3) on set O: H1 not confirmed; H2, H3 GATE-NOT-EVALUABLE. "
                     "D-5 sensitivities EXPLORATORY. Set A descriptive only (D-6 caveat). Set P stopped at its pilot (D-1).",
            "what": "counts-only copy of round 3's harness outputs: found10.py analyse (confirmatory and the D-5 "
                    "sensitivity), the D-5 census breakdown, the wording proxy (M2, manipulation check), the per-arm "
                    "found10 run summaries, the swap restores, and the drafting / verification totals per batch",
            "derived_by": "artifacts/make_results_r3.py (this directory): a mechanical copy; no test is recomputed",
            "source_sha256": dict(sorted(source_sha.items())),
            "source_commits": commits,
            "public_copy": "counts only: no record id, chunk id, document id, question, quote, binding term, field "
                           "name, path, host, index, collection or endpoint; provenance host/image/"
                           "installed_distribution redacted",
            "citable": harness_citable,
            "citable_reason": ("every harness output records citable true: found10.py analyse, run and the wording "
                               "proxy ran from a clean detached worktree (dirty false, untracked 0) at the commit in "
                               "source_commits; not from a versioned tools image (in_image false, warning kept)")
            if harness_citable else "a harness output is not citable; see its provenance block",
            "not_citable_parts": "none of the copied figures; the drafting totals record their own commits "
                                 "(6e620c2, b501ec7, 4c0bbb8) and citable flags beside them",
            "private_data": "accepted records, pools, per-record retrieval rows, verdict files and arm files stay with "
                            "the run outputs, off GitHub",
        },
        "confirmatory": {
            **results_block(res),
            "census_breakdown_beside": census_beside,
            "discriminating_records_by_stratum": discriminating,
        },
        "exploratory_d5_sensitivity": {
            "label": "EXPLORATORY (DEVIATIONS.md D-5): census-flagged records excluded per hypothesis's own stratum; "
                     "never replaces H1-H3",
            "per_hypothesis": exc["per_hypothesis"],
            "rule": "flagged = rejected by the papers-OC owner's census or noted as not a quantity; only the 53 "
                    "discriminating records of set O were judged",
            "excluded_by_stratum": exc["excluded_by_stratum"],
            "census_inputs_sha256": {
                "accepted-O.jsonl": exc["inputs_sha256"]["accepted-O.jsonl"],
                "census_verdicts": exc["inputs_sha256"]["partner-verdicts-O-53.json"],
                "census_notes": exc["inputs_sha256"]["partner-judging-notes-O-53.json"],
            },
            **results_block(sen),
        },
        "secondary": secondary_block(res),
        "descriptives": {
            "found_at_10": res["descriptives"]["found_at_10"],
            "noise_floor": res["descriptives"]["noise_floor"],
            "sizes": res["descriptives"]["sizes"],
            "gated_arms": res["descriptives"]["gated_arms"],
        },
        "proxy_manipulation_check": {"O": proxy_block(load(paths["proxy_O"])),
                                     "A": proxy_block(load(paths["proxy_A"]))},
        "runs": cells,
        "restore": {"O": restore_block(load(paths["restore_O"])), "A": restore_block(load(paths["restore_A"]))},
        "drafting": {
            "P_pilot": {"draft": totals_block(load(paths["draft_P_pilot"])),
                        "verify_run2": totals_block(load(paths["verify_P_pilot_run2"])),
                        "acceptance_run2": acceptance_block(load(paths["acceptance_P_pilot_run2"]))},
            "O": {"draft": totals_block(load(paths["draft_O"])),
                  "verify": totals_block(load(paths["verify_O"])),
                  "acceptance": acceptance_block(load(paths["acceptance_O"]))},
            "A": {"draft_segment1": totals_block(load(paths["draft_A_segment1"])),
                  "draft_segment2": totals_block(load(paths["draft_A_segment2"])),
                  "verify": totals_block(load(paths["verify_A"])),
                  "acceptance": acceptance_block(load(paths["acceptance_A"]))},
        },
    }
    args.out.write_text(json.dumps(out, indent=1, sort_keys=False, ensure_ascii=False) + "\n")
    print(json.dumps({"out": str(args.out), "citable": harness_citable, "commits": commits,
                      "runs": len(cells)}))


if __name__ == "__main__":
    main()
