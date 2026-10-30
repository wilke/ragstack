#!/usr/bin/env python3
"""Build the counts-only public artifact ``reach-r2.json`` from the three private pool manifests.

Round 2 of the glossary-model benchmark stopped at the reach gate (RESULTS.md, DEVIATIONS.md D-5).
The only measured numbers of the round are the arm-blind pool manifests written by
``glossbench_r2_pool.py build --set {O,A,P}`` (prereg §3.1). This script copies their counts and
drops everything else: ids, paths, hosts, per-paper cells, field names of the partner's binding
export, and any string that is not a count, a share, a seed, a model id, a version string or a
sha256. It performs no analysis beyond one subtraction and one comparison per set (the S1 upper
bound of DEVIATIONS.md D-5), so the result keeps the manifests' own ``citable`` verdict.

    python3 -I make_reach_r2.py --manifests DIR --out reach-r2.json

``DIR`` holds ``pool-manifest-O.json``, ``pool-manifest-A.json`` and ``pool-manifest-P.json``.
The output is byte-deterministic for a given set of manifests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SETS = ("O", "A", "P")
CORPUS_ALIAS = {"O": "OA-1000", "A": "ASM-1000", "P": "papers-OC"}
# provenance keys dropped from the public copy, as part 4's public export drops them
PROVENANCE_DROP = {"host", "image", "installed_distribution"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def public_provenance(prov: dict) -> dict:
    out = {k: v for k, v in prov.items() if k not in PROVENANCE_DROP}
    out["redacted"] = sorted(PROVENANCE_DROP)
    return out


def arm_block(name: str, arm: dict) -> dict:
    return {
        "sha256": arm["sha256"],
        "glossary_versions": list(arm["glossary_versions"]),
        "file_counts": dict(arm["file_counts"]),
    }


def reach_block(reach: dict) -> dict:
    out: dict = {}
    for arm, r in sorted(reach.items()):
        row = {
            "eval_papers": {
                "vs_chunks": r["vs_chunks"],
                "vs_chunks_reached": r["vs_chunks_reached"],
                "share": r["share"],
            }
        }
        corpus = r.get("corpus")
        row["corpus"] = (
            {
                "vs_chunks": corpus["vs_chunks"],
                "vs_chunks_reached": corpus["vs_chunks_reached"],
                "share": corpus["share"],
            }
            if corpus
            else "absent"
        )
        out[arm] = row
    return out


def one_set(m: dict) -> dict:
    counts = m["counts"]
    design = m["design"]
    gate = m["gate"]
    vs_eval = counts["vs_chunks_eval"]
    threshold = gate["threshold"]
    block = {
        "set": m["set"],
        "corpus": CORPUS_ALIAS[m["set"]],
        "reading": m["reading"],
        "created": m["created"],
        "schema_of_source": m["schema"],
        "pool_sha256": m["pool_sha256"],
        "eval_papers_sha256": m["eval_papers_sha256"],
        "design": {
            "n_papers": design["n_papers"],
            "min_vs_chunks": design["min_vs_chunks"],
            "paper_key": design["paper_key"],
            "seed": design["seed"],
            "chunk_seed": design["chunk_seed"],
            "reach_gate": design["reach_gate"],
            "reach_arms_configured": list(design["reach_arms_configured"]),
            "reach_arms_present": list(design["reach_arms_present"]),
            "quotas_per_paper": dict(design["quotas_per_paper"]),
            "quotas_overridden": list(design["quotas_overridden"]),
            "attachment": design["attachment"],
            "value_statement": design["value_statement"],
            "vs_stoplist_n": len(design["vs_stoplist"]),
        },
        "counts": {
            "chunks": counts["chunks"],
            "documents": counts["documents"],
            "papers": counts["papers"],
            "papers_with_vs": counts["papers_with_vs"],
            "eligible_papers": counts["eligible_papers"],
            "excluded_papers": counts["excluded_papers"],
            "eval_papers": counts["eval_papers"],
            "vs_chunks_corpus": counts["vs_chunks_corpus"],
            "vs_chunks_eval": vs_eval,
            "strata": dict(counts["strata"]),
            "sampled": dict(counts["sampled"]),
        },
        "gate": dict(gate),
        "s1_upper_bound": {
            "value": vs_eval,
            "rule": "S1 is a subset of the value-statement chunks of the eval papers (prereg §3.1 step 3), "
            "so S1 <= vs_chunks_eval under any set of reach arms, frontierp included",
            "threshold": threshold,
            "attainable_under_any_arm": vs_eval >= threshold,
        },
        "manipulation": {
            "chunks": m["manipulation"]["chunks"],
            "expected_chunks": m["manipulation"]["expected_chunks"],
            "match": m["manipulation"]["match"],
            "arm_resolution": {k: dict(v) for k, v in sorted(m["manipulation"]["arm_resolution"].items())},
        },
        "arms": {k: arm_block(k, v) for k, v in sorted(m["arms"].items())},
        "reach": reach_block(m["reach"]),
        "provenance": public_provenance(m["provenance"]),
    }
    fields = m.get("fields")
    if fields:
        plan = fields["plan"]
        block["fields_mode"] = {
            "draft_mode": fields["draft_mode"],
            "fields_sha256": fields["fields_sha256"],
            "n_fields": len(plan["fields"]),
            "n_new_ground": len(fields["new_ground"]),
            "n_constants": len(fields["constants"]),
            "constant_cap": plan["constant_cap"],
            "weights": dict(plan["weights"]),
            "in_set": {
                "counts": dict(fields["in_set"]["counts"]),
                "expected": dict(fields["in_set"]["expected"]),
                "records": fields["in_set"]["records"],
                "records_sha256": fields["in_set"]["records_sha256"],
                "records_without_key": fields["in_set"]["records_without_key"],
                "record_fields_outside_list": fields["in_set"]["record_fields_outside_list"],
            },
            "jobs": fields["jobs"],
            "plan": {
                "total_target": plan["total_target"],
                "realised": plan["realised"],
                "per_paper": dict(plan["per_paper"]),
                "strata": {k: dict(v) for k, v in plan["strata"].items()},
            },
        }
    return block


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifests", required=True, type=Path, help="directory holding pool-manifest-{O,A,P}.json")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    sources = {}
    sets = {}
    citable = True
    commits = set()
    for s in SETS:
        p = args.manifests / f"pool-manifest-{s}.json"
        m = json.loads(p.read_text())
        assert m["set"] == s and m["schema"] == "glossbench-r2-pool-manifest/1", p.name
        sources[p.name] = sha256_file(p)
        sets[s] = one_set(m)
        citable = citable and bool(m["provenance"]["citable"]) and bool(m.get("citable", True))
        commits.add(m["provenance"]["commit"])

    out = {
        "schema": "glossbench-r2-reach/1",
        "artifact_provenance": {
            "label": "RECORD — descriptive counts; no hypothesis tested (reach gate GATE-NOT-EVALUABLE on every set)",
            "what": "counts-only copy of the three arm-blind pool manifests of glossary-model benchmark round 2 "
            "(prereg §3.1; dry reading, without the frontierp file), plus the S1 upper bound of DEVIATIONS.md D-5",
            "derived_by": "artifacts/make_reach_r2.py (this directory), a mechanical copy: no number is computed "
            "that is not a copied count, a copied share, or vs_chunks_eval compared with the gate threshold",
            "source_sha256": sources,
            "source_commits": sorted(commits),
            "public_copy": "counts only: no chunk id, document id, paper id, title, path, host, index, collection, "
            "endpoint or field name of the partner's binding export is present; per-paper cells dropped; "
            "provenance host/image/installed_distribution redacted as in part 4's public export",
            "citable": citable,
            "citable_reason": "every manifest records provenance.citable true: the pool build ran from a clean detached "
            "worktree (dirty false, untracked 0) at the commit in source_commits; not from a versioned tools "
            "image (in_image false, warning 'not in an image' kept in each provenance block)"
            if citable
            else "a source manifest is not citable; see its provenance block",
            "private_data": "the manifests, the pools (ids, offsets, strata, reach booleans) and the arm files stay "
            "with the run outputs, off GitHub",
        },
        "sets": sets,
    }
    args.out.write_text(json.dumps(out, indent=1, sort_keys=False, ensure_ascii=False) + "\n")
    print(json.dumps({"out": str(args.out), "sets": list(sets), "citable": citable, "commits": sorted(commits)}))


if __name__ == "__main__":
    main()
