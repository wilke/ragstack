#!/usr/bin/env python3
"""Build the counts-only public artifacts of the dedicated reranker test from the private outputs.

The harness (``python/scripts/eval/reranker_test.py``) writes its outputs with ids, ranks and counts only
(``--omit-text``). This script copies the *summary* blocks of those outputs into two public files and
drops everything else: per-record rows, per-cluster rows, record and probe ids, under-served id lists,
file paths, host, image and installed-distribution provenance. It performs no analysis: every number in
the output is a copied count, share, p-value, sha256 or declared setting, except the gold-size
distribution, which is a count of distinct gold chunk ids (and of the documents they belong to) per
included record of the gold file, and the one hand-copied block named ``d5_exhaustion_check`` (the
read-only Elasticsearch counts of DEVIATIONS.md D-5, which the harness did not produce).

    python3 -I make_rr_artifacts.py --out-dir PRIVATE_OUT --dump DUMP.jsonl --dest .

``PRIVATE_OUT`` is the run's output directory; ``DUMP.jsonl`` the basename of the set-P chunk dump in it. The output is deterministic for a given set of inputs.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import statistics
from pathlib import Path

PROVENANCE_DROP = {"host", "image", "installed_distribution", "python"}
DROP_KEYS = {"per_record", "per_cluster", "under_served_ids", "probes", "path", "query", "request"}

# DEVIATIONS.md D-5: the five distinct bm25@100 queries served short on set P, verified read-only against
# the bench index. Hand-copied from the run log (counts only; the harness has no such step).
D5_EXHAUSTION_CHECK = {
    "source": "run log, hand-copied (not a harness output); DEVIATIONS.md D-5",
    "what": "for each distinct short bm25@100 query: the served list length against the index's own "
    "document count for the server's most_fields clause (content + symbol_glossary) and for content only",
    "rows": [
        {"records": 2, "served": 23, "count_most_fields": 23, "count_content_only": 22},
        {"records": 2, "served": 23, "count_most_fields": 23, "count_content_only": 23},
        {"records": 4, "served": 23, "count_most_fields": 23, "count_content_only": 17},
        {"records": 10, "served": 36, "count_most_fields": 36, "count_content_only": 36},
        {"records": 1, "served": 79, "count_most_fields": 79, "count_content_only": 79},
    ],
    "served_equals_count_most_fields_on_all": True,
    "queries_where_most_fields_count_exceeds_content_only": 2,
    "verdict": "verified exhausted (not a clamp); the harness mark GATE-NOT-EVALUABLE stands as written",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scrub(o, key: str | None = None):
    """Drop private or per-item keys recursively; keep basenames of ``file`` values."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if k in DROP_KEYS:
                continue
            if k == "file" and isinstance(v, str):
                out[k] = os.path.basename(v)
                continue
            out[k] = scrub(v, k)
        return out
    if isinstance(o, list):
        return [scrub(x) for x in o]
    return o


def public_provenance(prov: dict) -> dict:
    out = scrub({k: v for k, v in prov.items() if k not in PROVENANCE_DROP})
    out["redacted"] = sorted(PROVENANCE_DROP) + ["nested path keys"]
    return out


def identity_gate_public(g: dict) -> dict:
    probes = g.get("probes") or {}
    return {
        "status": g.get("status"),
        "n_probes": g.get("n_probes"),
        "reference": g.get("reference"),
        "probes_equal": sum(1 for p in probes.values() if p.get("equal")),
        "probes_same_set": sum(1 for p in probes.values() if p.get("same_set")),
    }


def pct(xs: list[int], p: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def dist(xs: list[int]) -> dict:
    return {
        "n": len(xs),
        "min": min(xs),
        "median": statistics.median(xs),
        "p90": pct(xs, 0.9),
        "max": max(xs),
        "eq1": sum(1 for x in xs if x == 1),
        "gt10": sum(1 for x in xs if x > 10),
        "histogram": {str(k): v for k, v in sorted(collections.Counter(xs).items())},
    }


def gold_sizes(gold: dict, chunk_to_doc: dict | None) -> dict:
    chunks, docs, quotes = [], [], []
    for rec in gold["records"].values():
        if rec.get("excluded"):
            continue
        ids: set = set()
        for q in rec["quotes"]:
            ids.update(q)
        chunks.append(len(ids))
        quotes.append(len(rec["quotes"]))
        if chunk_to_doc is not None:
            docs.append(len({chunk_to_doc[c] for c in ids}))
    out = {
        "what": "distinct gold chunk ids per included record (and the documents they belong to); "
        "quotes per record",
        "chunks_per_record": dist(chunks),
        "quotes_per_record": {str(k): v for k, v in sorted(collections.Counter(quotes).items())},
    }
    out["docs_per_record"] = dist(docs) if docs else "absent (no chunk-to-document map for this set)"
    return out


def stats_public(st: dict) -> dict:
    keep = [
        "schema", "set", "n_records", "n_included", "n_clusters", "excluded", "gold_rule", "analysis",
        "family", "hypotheses", "arms", "ceilings", "decomposition", "inactive_reranker", "repeat",
        "shuffle", "reranker", "manipulation_check", "boost_check", "depth_read_back", "inputs",
        "exploratory",
    ]
    out = {k: scrub(st[k]) for k in keep if k in st}
    out["identity_gate"] = identity_gate_public(st["identity_gate"])
    out["gold_input"] = {"sha256": st["gold_input"]["sha256"], "questions": scrub(st["gold_input"]["questions"])}
    out["provenance"] = public_provenance(st["provenance"])
    return out


def light(d: dict, keys: list[str]) -> dict:
    out = {k: scrub(d[k]) for k in keys if k in d}
    if "provenance" in d:
        out["provenance"] = public_provenance(d["provenance"])
    return out


def build_set(out_dir: Path, prefix: str, label: str, chunk_to_doc: dict | None) -> tuple[dict, dict]:
    files = {
        "stats": f"{prefix}-stats-r2.json",
        "stats_goldall": f"{prefix}-stats-r2-goldall.json",
        "pools": f"{prefix}-pools-r2.json",
        "pools_repeat": f"{prefix}-pools-r2rep.json",
        "rerank": f"{prefix}-rerank-r2.json",
        "fuse": f"{prefix}-fuse-r2.json",
        "census": f"{prefix}-census-r2.json",
        "gold": f"{prefix}-gold-r2.json",
    }
    loaded = {k: json.loads((out_dir / v).read_text()) for k, v in files.items()}
    shas = {v: sha256_file(out_dir / v) for v in files.values()}

    st = loaded["stats"]
    ga = loaded["stats_goldall"]
    block = {
        "set": label,
        "stats": stats_public(st),
        "stats_gold_rule_all": {
            "gold_rule": ga["gold_rule"],
            "hypotheses": scrub(ga["hypotheses"]),
            "arms_found": {a: v["summary"] for a, v in ga["arms"].items()},
            "ceilings": scrub(ga["ceilings"]),
            "decomposition": scrub(ga["decomposition"]),
        },
        "pools": light(loaded["pools"], ["backend", "status", "depths", "legs", "corpus", "depth", "manipulation",
                                          "questions", "api_calls", "config_readable", "config_status",
                                          "embed_model"]),
        "pools_repeat": light(loaded["pools_repeat"], ["status", "depth", "api_calls"]),
        "rerank": light(loaded["rerank"], ["status", "jobs", "depth", "identity_all", "sidecar", "sidecar_calls",
                                            "sidecar_pairs", "shuffle", "pools_input"]),
        "fuse": light(loaded["fuse"], ["variants", "depth"]),
        "census": light(loaded["census"], ["tokenizer", "max_length", "max_length_source", "passage_tokens",
                                            "pair_tokens", "neighbour_hits", "corpus"]),
        "gold": {
            **light(loaded["gold"], ["n", "n_included", "excluded", "corpus"]),
            "sizes": gold_sizes(loaded["gold"], chunk_to_doc),
        },
    }
    block["rerank"]["identity_gate"] = identity_gate_public(loaded["rerank"]["identity_gate"])
    return block, shas


def chunk_doc_map(dump: Path) -> dict:
    m = {}
    with dump.open() as fh:
        for line in fh:
            r = json.loads(line)
            m[r["chunk_id"]] = r["doc_id"]
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--dump", required=True,
                    help="basename of the set-P chunk dump inside --out-dir (for the docs-per-record count)")
    a = ap.parse_args()

    common = {
        "label": "RECORD — the dedicated reranker test (prereg frozen 2026-10-30T02:19:33Z); counts-only copy "
        "of the harness outputs",
        "derived_by": "artifacts/make_rr_artifacts.py (this directory): a mechanical copy of the summary blocks; "
        "no analysis except the gold-size count and the hand-copied D-5 block",
        "public_copy": "no query, quote or chunk text; no record, question, probe or chunk id; no path, host, "
        "image, endpoint, tenant, collection or index name; provenance host/image/installed_distribution/"
        "python redacted as in part 4's public export",
        "private_data": "the full outputs (per-record ranks and verdicts, per-cluster sums, ids) stay with the "
        "run outputs, off GitHub",
    }

    # docs-A: the part-4 arm's chunk ids are '<arm>|<doc uuid>|<n>', so the doc is the second field
    docs_a, shas_a = build_set(a.out_dir, "docsA", "docs-A/T512o0", None)
    gold_a = json.loads((a.out_dir / "docsA-gold-r2.json").read_text())
    ids_a = {c for r in gold_a["records"].values() if not r.get("excluded") for q in r["quotes"] for c in q}
    if all(c.count("|") == 2 for c in ids_a):
        docs_a["gold"]["sizes"] = gold_sizes(gold_a, {c: c.split("|")[1] for c in ids_a})
    extra_a = {}
    for name in ("docsA-power-r2.json", "docsA-part4-rescore-r2.json"):
        d = json.loads((a.out_dir / name).read_text())
        shas_a[name] = sha256_file(a.out_dir / name)
        if "power" in name:
            extra_a["power_realised_layout"] = light(d, ["schema", "alpha", "delta", "n_family", "n_pages",
                                                         "n_questions", "n_sims", "n_flips", "seed",
                                                         "discordance_icc", "rows"])
        else:
            extra_a["part4_rescore_d3"] = light(d, ["schema", "role", "set", "arms", "d3_complete", "d3_coverage",
                                                    "found_rule", "hypotheses", "source", "questions"])
            extra_a["part4_rescore_d3"]["input_sha256"] = d["input"]["sha256"]
    identity = a.out_dir / "identity-T512o0.jsonl"
    with identity.open() as fh:
        head = json.loads(fh.readline())
        n_rows = sum(1 for _ in fh)
    extra_a["identity_reference"] = {
        "schema": head.get("schema"),
        "arms": head.get("arms"),
        "glossary_condition": head.get("glossary_condition"),
        "n_questions": head.get("n_questions"),
        "rows": n_rows,
        "glossary_file_sha256": head.get("glossary_file", {}).get("sha256"),
        "glossary_versions": head.get("glossary_file", {}).get("glossary_versions"),
        "provenance": public_provenance(head["provenance"]),
        "sha256": sha256_file(identity),
    }
    docs_a.update(extra_a)
    docs_a_art = {"schema": "reranker-test-public/1", "artifact_provenance": {**common, "source_sha256": shas_a,
                  "citable": True, "citable_reason": "every source output records provenance.citable true "
                  "(clean tree, untracked 0, commit f408b7a); not from a versioned tools image (in_image false, "
                  "warning kept)"}, **docs_a}

    c2d = chunk_doc_map(a.out_dir / a.dump)
    set_p, shas_p = build_set(a.out_dir, "P", "papers-OC", c2d)
    set_p["d5_exhaustion_check"] = D5_EXHAUSTION_CHECK
    set_p_art = {"schema": "reranker-test-public/1", "artifact_provenance": {**common, "source_sha256": shas_p,
                 "citable": True, "citable_reason": "every source output records provenance.citable true "
                 "(clean tree, untracked 0, commit f408b7a); not from a versioned tools image (in_image false, "
                 "warning kept). Set P is exploratory by design (prereg Status); citable means reproducible from "
                 "a pinned clean tree, not confirmatory"}, **set_p}

    a.dest.mkdir(parents=True, exist_ok=True)
    for name, obj in (("reranker-docsA-r2.json", docs_a_art), ("reranker-P-r2.json", set_p_art)):
        (a.dest / name).write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")
        print(name, sha256_file(a.dest / name))


if __name__ == "__main__":
    main()
