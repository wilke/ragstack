"""Public build summary for rechunk stage 1 (counts, hashes and versions only; no paths, hosts or ids).

Reads the private ops directory of the live-ops plan and writes artifacts/build-summary.json.
Usage: PYTHONPATH=python python make_build_summary.py OPS_DIR OUT_JSON
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from ragstack.provenance import experiment_provenance

ARMS = ("base", "neighbour", "random1", "random2")
FORBIDDEN = re.compile(r"(/rag/|/scout/|/home/|localhost|127\.0\.0\.1|:\d{4,5}\b|sub_[0-9a-f-]{8,}|task_[0-9a-f-]{8,})")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def peak(log: Path, key: str) -> int | None:
    vals = [int(m.group(1)) for m in re.finditer(rf"\b{key}=(\d+)", log.read_text())] if log.exists() else []
    return max(vals) if vals else None


DERIVE_KEYS = ("derived_chunks", "host_chunks", "identical_span_content", "only_in_image", "only_in_host",
               "docs_differing", "derived_sha256")


def image_vs_host(derive: Path, diffd: Path) -> dict:
    """Counts only. Refuses if a differing document carries a confirmatory pair or a label-table chunk
    (the build script would have stopped; this guard keeps any such id out of the public file)."""
    if not derive.exists():
        return {"identical": True}
    d, c = json.loads(derive.read_text()), json.loads(diffd.read_text())
    if c.get("in_confirm_docs") or c.get("label_table_chunks_in_differing_docs"):
        raise SystemExit("REFUSED: a differing document carries a confirmatory pair or label-table chunk")
    out = {k: d[k] for k in DERIVE_KEYS}
    out["docs_differing_with_confirmatory_pair"] = 0
    out["label_table_chunks_in_differing_docs"] = 0
    return out


def main() -> None:
    ops, out = Path(sys.argv[1]), Path(sys.argv[2])
    cfg = json.loads((ops / "study-config.json").read_text())
    names = [row.split("\t") for row in (ops / "names.tsv").read_text().splitlines()]
    desc = next(r[0].split(" ", 1)[1] for r in names if r[0].startswith("#descriptor"))
    spec = next(r[0].split(" ", 1)[1] for r in names if r[0].startswith("#spec_hash"))
    cols = {}
    for arm in ARMS:
        cid = f"bench-rechunk-{arm}"
        rc = json.loads((ops / "rcpt" / f"{cid}.json").read_text())
        exp_host = json.loads((ops / "prep" / f"export-{arm}.summary.json").read_text())
        derive = ops / "state" / f"{cid}.derive.json"
        diffd = ops / "state" / f"{cid}.diffdocs.json"
        census = json.loads((ops / "state" / f"{cid}.census.json").read_text())
        dcheck = json.loads((ops / "state" / f"{cid}.dcheck.json").read_text())
        emb_tasks = json.loads((ops / "state" / f"{cid}.embed.tasks.json").read_text())["data"]
        load_tasks = json.loads((ops / "state" / f"{cid}.load.tasks.json").read_text())["data"]
        gdocs = (rc.get("glossary_file") or {}).get("documents") or rc.get("documents") or {}
        cols[arm] = {
            "collection": cid,
            "chunks_loaded": rc["n_chunks"], "documents": rc["n_docs"], "embedding_files": rc["n_shards"],
            "failed_shards": rc["n_shards_failed"],
            "documents_verified": gdocs.get("verified"), "documents_verified_ws": gdocs.get("verified_ws"),
            "documents_unverified": gdocs.get("unverified"),
            "census": census.get("live"), "census_ok": census.get("ok"),
            "d_check_ok": dcheck.get("ok"), "spec_hash": spec,
            "embed_tasks": len(emb_tasks), "embed_retry_max": max(t["retry_count"] for t in emb_tasks),
            "load_retry_max": max(t["retry_count"] for t in load_tasks),
            "peak_es_old_gen_pct": peak(ops / "mon" / f"heap-{cid}.log", "old"),
            "load_receipt_sha256": sha(ops / "rcpt" / f"{cid}.json"),
            "host_export": {"documents": exp_host["documents"], "chunks": exp_host["chunks"],
                            "chunks_sha256": exp_host["chunks_sha256"],
                            "commit": exp_host["provenance"]["commit"], "dirty": exp_host["provenance"]["dirty"]},
            "export_used": ("host" if arm == "base" else "image-derived"),
            "image_vs_host": image_vs_host(derive, diffd),
        }
    b1 =json.loads((ops / "tools" / "B1-gate.json").read_text())
    rec = {
        "schema": "rechunk-build-summary/1", "exploratory": True,
        "chunk_descriptor": desc, "spec_hash": spec,
        "chunks_total": sum(v["chunks_loaded"] for v in cols.values()),
        "collections": cols,
        "expected_chunks_in_harness_config": {a: cfg["corpora"][a]["expected_chunks"] for a in ARMS},
        # Operator measurement, 2026-11-06 (importlib.metadata in the host env and inside the tools image);
        # typed in here, not read from the ops dir. DEVIATIONS D-5 cites the same values.
        "library_versions_source": "operator measurement 2026-11-06, importlib.metadata host vs image",
        "library_versions": {
            "host_export_env": {"tokenizers": "0.22.2", "transformers": "5.12.1", "nltk": "3.9.4", "regex": "2026.6.28",
                                "markdown-it-py": "4.2.0", "mdit-py-plugins": "0.6.1"},
            "tools_image_v1.6.24-b1": {"tokenizers": "0.23.3", "transformers": "5.22.1", "nltk": "3.10.4",
                                       "regex": "2026.9.29", "markdown-it-py": "4.2.0", "mdit-py-plugins": "0.6.1"},
        },
        "tools_image_sha256": "ecf81440e64f9be88834e5b6911555f718ad71084f983ad41d962e962170cbf8",  # cwl/tool-image.receipt.json
        "embed_workflow": {"pr": b1.get("pr"), "merge_commit": b1.get("merge_commit"), "cwl_sha256": b1.get("cwl_sha256")},
        "repo_head_at_run": (ops / "preflight" / "repo-head.txt").read_text().strip(),
        "provenance": experiment_provenance(),
    }
    rec["provenance"].pop("host", None)
    w = rec["provenance"].pop("warnings", None)
    rec["provenance"]["warnings_n"] = len(w or [])
    text = json.dumps(rec, indent=1, sort_keys=True)
    bad = FORBIDDEN.findall(text)
    if bad:
        raise SystemExit(f"REFUSED: public summary would carry forbidden strings: {sorted(set(bad))[:5]}")
    out.write_text(text + "\n")
    print(json.dumps({"chunks_total": rec["chunks_total"], "out": out.name}))


if __name__ == "__main__":
    main()
