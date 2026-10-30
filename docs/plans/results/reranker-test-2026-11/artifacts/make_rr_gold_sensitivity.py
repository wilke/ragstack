#!/usr/bin/env python3
"""Build the counts-only public artifact of the set-P gold-scope sensitivity (ADDENDUM-2026-10-30-gold-scope.md).

Reads the private sensitivity outputs (one directory, written by uncommitted offline scripts that rebuilt
set P's gold under four variants and re-ran the committed harness's ``stats`` on each) and copies their
summary counts into one public file. It performs no analysis: every number is a copied count, share, p-value
or sha256, except the derived ``differences`` block (integer subtractions of copied found@10 / ceiling
counts, named by the arms they subtract). Record ids, paper ids, document ids, quote text, paths and host are
dropped.

    python3 -I make_rr_gold_sensitivity.py --in-dir PRIVATE_SENS_DIR --dest rr-P-gold-sensitivity.json

The output is deterministic for a given set of inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

INPUTS = [
    "r2-v1-v2-v3-v3b-v4.json",
    "floored-vs-committed.json",
    "gold-floored-counts.json",
    "gold-v2-counts.json",
    "gold-v3-counts.json",
    "gold-v4-counts.json",
    "vectors-check.json",
    "v4-identity-check.json",
    "fifth-chunk.json",
    "P-stats-repro.json",
    "P-stats-floored.json",
    "P-stats-v2.json",
    "P-stats-v3.json",
    "P-stats-v3b.json",
    "P-stats-v4.json",
]
CODE = ["floor.py", "variant2.py", "variant3.py", "variant3b.py", "variant4.py", "v4check.py", "fifth.py",
        "fifth2.py", "stats.py", "compare.py", "compare3.py"]
PROV_KEYS = ["schema", "version", "describe", "commit", "dirty", "untracked", "source", "citable", "recorded_at"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", required=True, type=Path)
    ap.add_argument("--dest", required=True, type=Path)
    a = ap.parse_args()
    d = a.in_dir
    J = {n: json.loads((d / n).read_text()) for n in INPUTS}
    cmp_ = J["r2-v1-v2-v3-v3b-v4.json"]
    cols = cmp_["columns"]

    def col(vals):
        return dict(zip(cols, vals))

    f10 = {arm: col(v) for arm, v in sorted(cmp_["found@10"].items())}
    ceil = {k: col(v) for k, v in sorted(cmp_["ceilings"].items())}
    hyp = {h: {c: {"gained": x[0], "lost": x[1], "p": x[2], "status": x[3]} for c, x in zip(cols, v)}
           for h, v in sorted(cmp_["hypotheses"].items())}

    def diff(a_, b_, src):
        return {c: src[a_][c] - src[b_][c] for c in cols}

    differences = {
        "A0 - A4-50 (found@10; H1's contrast)": diff("A0", "A4-50", f10),
        "A5-50 - A0 (found@10; H3's contrast)": diff("A5-50", "A0", f10),
        "A9-bm25 - A4-50 (found@10)": diff("A9-bm25", "A4-50", f10),
        "bm25@50 - hybrid@50 (ceiling; H2's contrast)": diff("bm25@50", "hybrid@50", ceil),
    }

    # the committed column must equal what the harness re-run at the recorded commit reproduces
    repro = J["P-stats-repro.json"]
    repro_ok = all(repro["arms"][arm]["summary"]["found@10"] == f10[arm]["committed"] for arm in f10)

    def prov(p):
        r = {k: p.get(k) for k in PROV_KEYS}
        r["warnings"] = [w.split(":")[0] for w in (p.get("warnings") or [])]
        return r

    stats_prov = {n[len("P-stats-"):-len(".json")]: prov(J[n]["provenance"])
                  for n in INPUTS if n.startswith("P-stats-")}

    g1, g2, g3, g4 = (J["gold-floored-counts.json"], J["gold-v2-counts.json"], J["gold-v3-counts.json"],
                      J["gold-v4-counts.json"])
    ident = J["v4-identity-check.json"]
    vec = J["vectors-check.json"]
    fifth = next(iter(J["fifth-chunk.json"].values()))

    out = {
        "schema": "rr-P-gold-sensitivity/1",
        "label": ("EXPLORATORY SENSITIVITY — set P (papers-OC, the owner's 155 held records) of the dedicated "
                  "reranker test, re-scored under four gold variants; the record's own figures are the "
                  "'committed' column (artifacts/reranker-P-r2.json). Every H1-H3 stays GATE-NOT-EVALUABLE."),
        "columns": {
            "committed": "the record: the papers-OC owner's quote rule as #871 implements it (DEVIATIONS D-3)",
            "v1": "committed rule + a 30-character floor on the boundary (first/last 60 %) fallback",
            "v2": ("short-anchor (< 30 chars) boundary matches count only if the chunk also carries every "
                   "figure and symbol token of the quote (the papers-OC owner's symbol-token rule, aad81fe, "
                   "external)"),
            "v3": ("short-anchor boundary matches under the papers-OC owner's assignment rule (6c71ef1, "
                   "external; our implementation from its description)"),
            "v3b": ("v3 implemented from the papers-OC owner's 14 published match vectors (6fbd3f0, external); "
                    "a cross-check of v3, not a fifth variant"),
            "v4": "v3 + source-paper scoping: gold restricted to chunks of the record's own source paper",
        },
        "n_included": col(cmp_["n_included"]),
        "found@10": f10,
        "ceilings": ceil,
        "hypotheses": hyp,
        "differences": differences,
        "exploratory_sign_changes_vs_committed": cmp_["sign_changes_vs_committed"],
        "gold": {
            "size_hist": {"committed": g2["gold_size_hist"]["r2"], "v1": g1["gold_size_after"]["hist"],
                          "v2": g2["gold_size_hist"]["v2"], "v3": g3["gold_size_hist_v3"],
                          "v4": g4["gold_size_hist_v4"]},
            "v1": {"floor": g1["floor"], "records_below_floor": g1["records_below_floor"],
                   "records_gold_changed": g1["records_gold_changed"], "newly_excluded": g1["newly_excluded"],
                   "unfloored_rebuild_equals_committed_gold": g1["unfloored_rebuild_equals_P_gold_r2"]},
            "v2": {"records_short_anchor": g2["records_short_anchor"],
                   "records_gold_changed": g2["records_gold_changed_v2"],
                   "short_anchor_matches": g2["match_counts"], "newly_excluded": g2["newly_excluded_v2"]},
            "v3": {"records_short_anchor_with_assignment": g3["records_short_anchor_with_assignment"],
                   "records_differ_from_v2": g3["records_v3_differs_from_v2"],
                   "match_decisions": g3["decisions"]},
            "v3b": {"records_gold_differs_from_v3": g4["v3b_gold_differs_from_v3_records"],
                    "match_vectors": {"sha256": vec["sha256"], "n": len(vec["results"]),
                                      "v3_pass": vec["v3_pass"], "v3b_pass": vec["v3b_pass"]}},
            "v4": {"counts": g4["v4_counts"], "excluded": g4["excluded_v4"],
                   "records_losing_cross_paper_gold": ident["records_losing"],
                   "papers_mapped": g4["mapping"]["papers_mapped"],
                   "mapping_equals_round2_exclusion_list": g4["mapping"]["equals_exclude_P"]},
        },
        "v4_document_identity_check": {
            "removed_chunks_by_doc_kind": ident["removed_chunks_by_kind"],
            "removed_docs": ident["removed_docs"],
            "removed_docs_by_kind": ident["removed_docs_by_kind"],
            "removed_docs_max_share_of_a_source_paper_long_sentences": ident["removed_docs_max_share"],
            "source_papers_with_a_copy_ge_50pct": sum(bool(v["copies_ge50pct"])
                                                      for v in ident["source_papers"].values()),
            "n_source_papers": len(ident["source_papers"]),
            "bench_doc_kinds": ident["doc_kinds_bench"],
        },
        "pair_with_32_committed_gold_chunks_under_v3": {
            "records": len(J["fifth-chunk.json"]),
            "gold_v3": fifth["n_gold"], "full_containment": fifth["n_exact"],
            "boundary_only": fifth["n_boundary"],
            "documents_holding_full_containment": fifth["fifth"]["exact_docs_distinct"],
            "boundary_chunk_same_doc_as_a_full_match": any(fifth["fifth"]["same_doc_as_exact"]),
            "boundary_chunk_straddles_a_join": fifth["fifth"]["quote_straddles_join_with_any_neighbour"],
            "boundary_chunk_assigns_same_variable_same_value": fifth["fifth"]["same_var_assigned_same_value"],
        },
        "artifact_provenance": {
            "citable": False,
            "citable_reason": ("the gold variants were built by uncommitted offline scripts (sha256 below); the "
                               "stats step is the committed harness at a clean commit, but its entry point was "
                               "outside the checkout. Exploratory sensitivity, reportable, not a citable result"),
            "derived_by": "artifacts/make_rr_gold_sensitivity.py (this directory): a mechanical copy",
            "committed_column_reproduced_by_harness_rerun": repro_ok,
            "stats_provenance": stats_prov,
            "source_sha256": {n: sha(d / n) for n in INPUTS},
            "uncommitted_code_sha256": {n: sha(d / n) for n in CODE if (d / n).exists()},
            "public_copy": ("counts only: no query, quote or chunk text; no record, paper, document or chunk "
                            "id; no path, host or collection name"),
        },
    }
    a.dest.write_text(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=False) + "\n")


if __name__ == "__main__":
    main()
