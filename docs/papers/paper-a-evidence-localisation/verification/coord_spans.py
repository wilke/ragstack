#!/usr/bin/env python3
"""Dump `sentence_spans()` offsets for every unit of the 308 development documents, using
whichever ragstack checkout `--repo` names (the Stage 0 pin, or main).

The labels key evidence by (unit index, sentence index) where the sentence index counts the
spans `sentence_spans()` returns over the unit's own text (blank ones skipped, as the labeler's
`segment()` does). So two checkouts are in the same coordinate system iff they produce the
same offsets on these unit texts. Unit boundaries come from the stored `units.jsonl` and the
texts from the stored `docs.jsonl` (both outside work/conf), so only the segmenter varies. This script only DUMPS offsets; `coord_check.py` (main)
compares and fingerprints them.

Run once per checkout:
    python3 coord_spans.py --repo ~/Development/worktrees/paper-a-pin-55a0fc2 --out pin.json
    python3 coord_spans.py --repo <this worktree>                          --out main.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

WORK = pathlib.Path("/rag/tmp/stage0-conf/work")      # the DEV corpus; never work/conf
LABELS = pathlib.Path(__file__).resolve().parents[4] / "docs/plans/results/stage0/artifacts"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    py = str(pathlib.Path(args.repo).resolve() / "python")
    # the env carries an editable-install finder for another checkout; drop it (as the
    # Stage 0 harness's pin_repo() does) and prove which ragstack was imported
    sys.meta_path[:] = [f for f in sys.meta_path if "editable" not in type(f).__module__]
    sys.path.insert(0, py)
    import ragstack
    from ragstack.ingestion.chunkers import sentence_spans
    assert ragstack.__file__.startswith(py), ragstack.__file__

    docnos = set()
    for line in open(LABELS / "r31ext" / "labels-r31-scout.jsonl"):
        docnos.add(json.loads(line)["docno"])
    texts, units = {}, {}
    with open(WORK / "units.jsonl") as f:
        for line in f:
            r = json.loads(line)
            if r["docno"] in docnos:
                units[r["docno"]] = r["units"]
    with open(WORK / "docs.jsonl") as f:
        for line in f:
            r = json.loads(line)
            if r["docno"] in docnos:
                texts[r["docno"]] = r["text"]
    out = {}
    for d in sorted(docnos):
        t = texts[d]
        # raw offsets, exactly as sentence_spans() returns them over each unit's text (the
        # labeler later skips blank ones; equal raw offsets imply equal labeler indices)
        out[d] = [[list(ab) for ab in sentence_spans(t[u["start_char"]:u["end_char"]])]
                  for u in units[d]]
    import subprocess
    head = subprocess.run(["git", "-C", args.repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    try:
        import nltk
        nv = nltk.__version__
    except ImportError:
        nv = None
    pathlib.Path(args.out).write_text(json.dumps(
        {"repo_head": head, "ragstack_file": ragstack.__file__, "nltk": nv,
         "n_docs": len(out), "spans": out}))
    print(head, len(out), "docs")


if __name__ == "__main__":
    main()
