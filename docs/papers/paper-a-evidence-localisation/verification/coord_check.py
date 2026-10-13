#!/usr/bin/env python3
"""Coordinate check (CURRENT-CODE DELTA, D2): are the stored sentence-keyed labels valid in
main's sentence coordinates?

Reads the two offset dumps written by `coord_spans.py` (one at the Stage 0 pin 55a0fc2, one
at this checkout), and on main computes:
  * `sentence_spans_fingerprint()` over the 306 development documents' unit texts (main's
    segmenter, run here), and `span_fingerprint()` over the pin's dumped offsets on the same
    texts; equal sha256 + equal texts_sha256 = same coordinate system;
  * per document, the share of sentences whose offsets moved (0 when equal);
  * that every stored label's (unit, first..last sentence) lies inside main's segmentation.
"""
from __future__ import annotations

import json
import sys

import vcommon as V

WORK = V.DEV_WORK


def main() -> None:
    pin_p, main_p = sys.argv[1], sys.argv[2]
    prov = V.provenance()
    from ragstack.provenance import sentence_spans_fingerprint, span_fingerprint
    pin, cur = json.loads(open(pin_p).read()), json.loads(open(main_p).read())
    docnos = sorted(pin["spans"])
    texts, units = {}, {}
    with open(WORK / "units.jsonl") as f:
        for line in f:
            r = json.loads(line)
            if r["docno"] in pin["spans"]:
                units[r["docno"]] = r["units"]
    with open(WORK / "docs.jsonl") as f:
        for line in f:
            r = json.loads(line)
            if r["docno"] in pin["spans"]:
                texts[r["docno"]] = r["text"]
    unit_texts = [texts[d][u["start_char"]:u["end_char"]] for d in docnos for u in units[d]]
    fp_main = sentence_spans_fingerprint(unit_texts)
    fp_pin = span_fingerprint([tuple(map(tuple, s)) for d in docnos for s in pin["spans"][d]],
                              kind="sentences", texts=unit_texts,
                              producer="ragstack.ingestion.chunkers.sentence_spans @ 55a0fc2")
    fp_main_dump = span_fingerprint(
        [tuple(map(tuple, s)) for d in docnos for s in cur["spans"][d]], kind="sentences",
        texts=unit_texts, producer="dump @ main")
    moved = {}
    for d in docnos:
        a = [tuple(x) for u in pin["spans"][d] for x in u]
        b = [tuple(x) for u in cur["spans"][d] for x in u]
        if a != b:
            moved[d] = 1 - len(set(a) & set(b)) / max(len(a), 1)
    # every stored label span must sit exactly on main's sentence offsets: start = the first
    # sentence's start, end = the last sentence's end (non-blank sentences, unit-relative)
    sents = {d: [[(s, e) for s, e in u_sp
                  if texts[d][units[d][i]["start_char"]:units[d][i]["end_char"]][s:e].strip()]
                 for i, u_sp in enumerate(cur["spans"][d])] for d in docnos}
    checked = {}
    for run, judges in (("s0", None), ("r3", ("scout", "qwen")), ("r31ext", ("scout", "qwen")),
                        ("claude", ("sonnet5", "opus5", "fable51"))):
        files = [V.F[run]] if judges is None else [V.F[run][j] for j in judges]
        n = bad = 0
        for fp in files:
            for r in V.read_jsonl(fp):
                d = r["docno"]
                for key in ("sets", "dup_sets"):
                    for st in r.get(key) or []:
                        for sp in st["spans"]:
                            n += 1
                            u0 = units[d][sp["unit"]]["start_char"]
                            S = sents[d][sp["unit"]] if sp["unit"] < len(sents[d]) else []
                            ok = (sp["last_sentence"] < len(S)
                                  and sp["start"] == u0 + S[sp["first_sentence"]][0]
                                  and sp["end"] == u0 + S[sp["last_sentence"]][1])
                            bad += not ok
        checked[run] = {"label_spans_checked": n, "not_on_main_sentence_offsets": bad}
    body = {"pin_commit": pin["repo_head"], "main_commit": cur["repo_head"],
            "nltk": [pin["nltk"], cur["nltk"]],
            "n_docs": len(docnos), "n_unit_texts": len(unit_texts),
            "fingerprint_main_sentence_spans_fingerprint": fp_main,
            "fingerprint_pin_offsets": fp_pin, "fingerprint_main_offsets_dump": fp_main_dump,
            "EQUAL": (fp_main["sha256"] == fp_pin["sha256"]
                      and fp_main["texts_sha256"] == fp_pin["texts_sha256"]),
            "docs_with_moved_offsets": moved,
            "label_spans_vs_main_offsets": checked}
    V.write("coord_check", prov, body)
    print("EQUAL" if body["EQUAL"] else "DIFFER", len(moved), checked)


if __name__ == "__main__":
    main()
