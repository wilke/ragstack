#!/usr/bin/env python3
"""CURRENT-CODE DELTA, D3: re-run the span LOCATOR of the current checkout over the stored
model answers and compare with the stored label records.

Unlike the recompute_* scripts this one deliberately IMPORTS the harness's locator functions
-- from THIS checkout (main), not from a pin -- because the question is what main's code
does with the same model output. Nothing is edited. No endpoint is contacted: the stored
post-strip answer text (`raws` in each committed label record) is the input.

For every primary reading it reassembles the record exactly as the labelers do (one parse
per answer; a failed parse means the next stored answer is the one re-prompt; sets of the
re-prompt replace the first attempt's only when non-empty; stats summed), then compares
sets (char spans and sentence ids), problems and vstats with the stored record.

Not re-located: the Stage 0 and r3 duplicate / audit presentations (their answer text is not
in the committed record), and the #513 span filter (`s0c_span_filter.py` is not run under
this task's constraints; its code is unchanged on main since 2f85092 -- see README).

Requires STAGE0_BIG pointing at a scratch directory (the harness module mkdirs under it).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import vcommon as V

assert os.environ.get("STAGE0_BIG") and not os.path.realpath(os.environ["STAGE0_BIG"]).startswith(
    os.path.realpath(V.DEV_WORK.parent)), "set STAGE0_BIG to a scratch directory"
sys.path.insert(0, str(V.STAGE0))
WORK = V.DEV_WORK                                   # docs/units of the DEV corpus


def reassemble(raws, parse, fail, seg, text):
    allsets, problems, vstats = [], [], {}
    i = 0
    while i < len(raws):
        sets, probs, stx = parse(raws[i], seg, text)
        probs_all = list(probs)
        i += 1
        if any(x in fail or x.startswith("bad_json") for x in probs) and i < len(raws):
            sets2, probs2, stx2 = parse(raws[i], seg, text)
            i += 1
            for k, v in stx2.items():
                stx[k] = stx.get(k, 0) + v
            probs_all += list(probs2) + ["reprompted"]
            sets = sets2 if sets2 else sets
        allsets.extend(sets)
        problems.extend(probs_all)
        for k, v in stx.items():
            vstats[k] = vstats.get(k, 0) + v
    return allsets, problems, vstats


_CTX: dict = {}


def _one(arg):
    run, r = arg
    d = r["docno"]
    sets, probs, vs = reassemble(r["raws"], _CTX["parse"][run], _CTX["fail"][run],
                                 _CTX["segs"][d], _CTX["texts"][d])
    a = key_sets(sets) == key_sets(r["sets"])
    b = probs == r["problems"]
    c = ({k: v for k, v in vs.items() if v} == {k: v for k, v in r["vstats"].items() if v})
    diff = None if (a and b and c) else {
        "topic": r["topic"], "docno": d, "presentation": r.get("presentation", 0),
        "sets_equal": a, "problems_equal": b, "vstats_equal": c}
    return {"a": a, "b": b, "c": c, "seen": vs.get("spans_seen", 0),
            "hall": vs.get("hallucinated", 0), "diff": diff}


def key_sets(sets):
    return [[(sp["unit"], sp["first_sentence"], sp["last_sentence"], sp["start"], sp["end"])
             for sp in s["spans"]] for s in sets]


def main() -> None:
    # the harness's pin_repo() insists on importing ragstack from the primary checkout
    # (stage1_common.REPO_PY, hard-coded in the helper); import the harness first, then
    # take provenance of THIS checkout, and record which ragstack the segmenter came from.
    import s0_label as L0
    import s0_label_r3 as L3
    import s0_label_r31 as L31
    import ragstack
    prov = V.provenance()
    rs_file = ragstack.__file__
    rs_repo = str(pathlib.Path(rs_file).parents[2])
    rs_head = subprocess.run(["git", "-C", rs_repo, "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
    rs_seg_diff = subprocess.run(
        ["git", "-C", rs_repo, "diff", "--stat", prov.get("commit", "HEAD"), "--",
         "python/ragstack/ingestion/chunkers.py"], capture_output=True, text=True).stdout
    rs_status = subprocess.run(["git", "-C", rs_repo, "status", "--porcelain", "--",
                                "python/ragstack/"], capture_output=True, text=True).stdout

    fail0 = ("quote_not_in_document", "last_quote_not_in_document", "no_first_words",
             "span_crosses_unit_or_unlocatable", "no_json")
    plan = [("s0", None, L0.parse_and_verify, fail0),
            ("r3", ("scout", "qwen"), L3.parse_and_verify_r3, L3.FAIL_PROBLEMS),
            ("r31ext", ("scout", "qwen"), L31.parse_and_verify_r31, L31.FAIL_PROBLEMS),
            ("claude", ("sonnet5", "opus5", "fable51"), L31.parse_and_verify_r31,
             L31.FAIL_PROBLEMS)]
    need = set()
    recs = {}
    for run, judges, _p, _f in plan:
        for j in (judges or ("scout",)):
            fp = V.F[run] if judges is None else V.F[run][j]
            recs[(run, j)] = V.read_jsonl(fp)
            need |= {r["docno"] for r in recs[(run, j)]}
    texts, units = {}, {}
    with open(WORK / "units.jsonl") as f:
        for line in f:
            r = json.loads(line)
            if r["docno"] in need:
                units[r["docno"]] = r["units"]
    with open(WORK / "docs.jsonl") as f:
        for line in f:
            r = json.loads(line)
            if r["docno"] in need:
                texts[r["docno"]] = r["text"]
    segs = {d: L0.segment(texts[d], units[d]) for d in need}

    global _CTX
    _CTX = {"segs": segs, "texts": texts,
            "parse": {"s0": L0.parse_and_verify, "r3": L3.parse_and_verify_r3,
                      "r31ext": L31.parse_and_verify_r31, "claude": L31.parse_and_verify_r31},
            "fail": {run: f for run, _j, _p, f in plan}}
    import multiprocessing as mp
    workers = int(os.environ.get("PAPER_A_WORKERS", "16"))
    out = {}
    with mp.get_context("fork").Pool(workers) as pool:
        for run, judges, _parse, _fail in plan:
            for j in (judges or ("scout",)):
                rows = pool.map(_one, [(run, r) for r in recs[(run, j)]], chunksize=8)
                diffs = [x["diff"] for x in rows if x["diff"]][:20]
                out[f"{run}.{j}"] = {
                    "records": len(rows),
                    "sets_identical": sum(x["a"] for x in rows),
                    "problems_identical": sum(x["b"] for x in rows),
                    "vstats_identical": sum(x["c"] for x in rows),
                    "relocated_spans_seen": sum(x["seen"] for x in rows),
                    "relocated_hallucinated": sum(x["hall"] for x in rows),
                    "first_differences": diffs}
                print(run, j, {k: v for k, v in out[f"{run}.{j}"].items()
                               if k != "first_differences"}, flush=True)
    V.write("relocate_main", prov, {
        "locator_source": V.portable(V.STAGE0), "ragstack_imported_from": V.portable(rs_file),
        "ragstack_checkout_head": rs_head,
        "ragstack_checkout_chunkers_diff_vs_this_commit": rs_seg_diff or "EMPTY",
        "ragstack_checkout_python_status": rs_status or "CLEAN",
        "results": out})


if __name__ == "__main__":
    main()
