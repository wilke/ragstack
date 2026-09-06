"""Stage 0b step 7 -- the R-dev stratified sample, as a READY-TO-READ human artifact.

**Item 8 of Stage 0 is OUT OF SCOPE for any agent.** This module produces the >= 100-pair
two-reader draw and renders it for humans; it does **not** read it, does not simulate a
reader, and emits no kappa. The label-validation gate stays ``PENDING-HUMAN``.

SS6.6.2 stratification, seeded and recorded BEFORE any pair is read:

| stratum | share |
|---|---|
| model positives (Scout returned >= 1 evidence set) | 40% |
| model negatives ("no localizable evidence") | 25% |
| deep-section attributions (every supplied span outside the Abstract and the first body
  unit -- the operational reading of SS6.6.2's "outside the abstract/intro") | 20% |
| long documents (top doc-length tercile, incl. SS6.5-windowed docs) | 15% |

Two independent readers get the SAME pairs in INDEPENDENTLY shuffled order. Readers see the
topic and the segmented document and the supplied spans; they never see a ranking, an arm,
a chunk boundary, or each other's verdicts.
"""
from __future__ import annotations

import html
import json
import random
import statistics as st

import s0_common as C
from s0_label import segment

TARGET = 100
SHARES = [("model_positive", 0.40), ("model_negative", 0.25),
          ("deep_section", 0.20), ("long_document", 0.15)]


def main() -> None:
    labels = [json.loads(x) for x in (C.WORK / "labels.jsonl").read_text().splitlines() if x]
    labels = [r for r in labels if not r["dropped"]]
    docs, units = {}, {}
    for line in open(C.WORK / "docs.jsonl"):
        r = json.loads(line)
        docs[r["docno"]] = r["text"]
    for line in open(C.WORK / "units.jsonl"):
        r = json.loads(line)
        units[r["docno"]] = r["units"]
    tops = json.loads((C.CDS / "topics_merged.json").read_text())

    lens = sorted(r["doc_chars"] for r in labels)
    t2 = lens[int(0.667 * (len(lens) - 1))] if lens else 0

    def stratum(r):
        if r["doc_chars"] >= t2 or r["windowed"]:
            long_ok = True
        else:
            long_ok = False
        if not r["sets"]:
            return "model_negative"
        deep = all(sp["unit"] >= 2 for s in r["sets"] for sp in s["spans"])
        if deep:
            return "deep_section"
        if long_ok:
            return "long_document"
        return "model_positive"

    pools: dict[str, list] = {k: [] for k, _ in SHARES}
    for r in labels:
        pools[stratum(r)].append(r)

    rng = random.Random(C.SEED_RDEV)
    draw, short = [], {}
    for name, share in SHARES:
        want = round(TARGET * share)
        have = pools[name]
        rng.shuffle(have)
        take = have[:want]
        draw.extend(take)
        if len(take) < want:
            short[name] = {"wanted": want, "available": len(have)}
    if len(draw) < TARGET:                     # top up from the largest remaining pool
        rest = [r for name, _ in SHARES for r in pools[name] if r not in draw]
        rng.shuffle(rest)
        draw.extend(rest[:TARGET - len(draw)])

    meta = {
        "seed": C.SEED_RDEV, "target": TARGET, "drawn": len(draw),
        "strata_definition": {
            "model_positive": "Scout returned >= 1 evidence set",
            "model_negative": "Scout returned 'no localizable evidence'",
            "deep_section": "EVERY supplied span has unit index >= 2 (outside the "
                            "Abstract unit 0 and the first body unit 1)",
            "long_document": "doc_chars in the top tercile, or SS6.5-windowed"},
        "long_tercile_char_threshold": t2,
        "pool_sizes": {k: len(v) for k, v in pools.items()},
        "shortfalls": short,
        "drawn_by_stratum": {k: sum(1 for r in draw if stratum(r) == k)
                             for k, _ in SHARES},
        "pairs": [{"topic": r["topic"], "docno": r["docno"], "stratum": stratum(r),
                   "n_sets": len(r["sets"]), "windowed": r["windowed"],
                   "doc_chars": r["doc_chars"]} for r in draw],
        "STATUS": "PENDING-HUMAN — two independent readers required (SS6.6.2). "
                  "No agent may read these pairs or report kappa.",
    }
    C.atomic_json(C.WORK / "rdev_sample.json", meta)

    # ---------------------------------------------------------------- render
    for reader in ("A", "B"):
        order = list(range(len(draw)))
        random.Random(C.SEED_RDEV + (1 if reader == "A" else 2)).shuffle(order)
        parts = [
            "<style>body{font:15px/1.55 system-ui,sans-serif;max-width:52em;margin:2em auto;"
            "padding:0 1em}h2{border-top:3px solid #999;padding-top:1em;margin-top:2.5em}"
            ".topic{background:#f3f6fb;padding:.8em 1em;border-left:4px solid #446}"
            ".span{background:#fff2b8;padding:.6em .8em;margin:.4em 0;border-left:3px solid #c90}"
            ".unit{font-weight:600;margin-top:1em;color:#334}"
            ".s{display:block;margin:.15em 0}.verd{background:#eef8ee;padding:.8em;"
            "border-left:4px solid #4a4;margin-top:1em}code{background:#eee;padding:0 .25em}</style>",
            f"<h1>R-dev evidence read — reader {reader}</h1>",
            "<p><b>You are one of two independent readers.</b> Do not confer. Record one "
            "verdict per pair in <code>rdev_verdicts_" + reader + ".csv</code> "
            "(<code>pair_id,verdict,notes</code>). Verdicts: <code>correct</code>, "
            "<code>wrong-location</code>, <code>non-minimal</code>, "
            "<code>missed-evidence</code>, <code>correctly-none</code>, "
            "<code>ambiguous</code>.</p>",
            "<p>For <b>every</b> pair — model negatives included — answer both: "
            "<b>(a)</b> is each supplied span correctly located and minimal? "
            "<b>(b)</b> is there evidence in this document the labeler did <i>not</i> "
            "supply? See <code>design/RUBRIC-evidence.md</code> (sha256 "
            f"<code>{C.sha256_file(C.DESIGN / 'RUBRIC-evidence.md')[:16]}…</code>).</p>",
        ]
        for pos, k in enumerate(order, 1):
            r = draw[k]
            pid = f"{r['topic']}__{r['docno']}"
            f = tops[r["topic"]]["fields"]
            seg = segment(docs[r["docno"]], units[r["docno"]])
            parts.append(f"<h2>{pos}. pair <code>{pid}</code></h2>")
            parts.append(
                f"<div class='topic'><b>Need type:</b> {tops[r['topic']]['type']}<br>"
                f"<b>Summary:</b> {html.escape(f['summary'])}<br>"
                f"<b>Description:</b> {html.escape(f['description'])}</div>")
            if r["sets"]:
                parts.append(f"<p><b>Labeler supplied {len(r['sets'])} evidence set(s):</b></p>")
                for si, s in enumerate(r["sets"], 1):
                    for sp in s["spans"]:
                        txt = docs[r["docno"]][sp["start"]:sp["end"]]
                        parts.append(
                            f"<div class='span'><b>set {si}</b> — unit {sp['unit']}, "
                            f"sentences {sp['first_sentence']}–{sp['last_sentence']}<br>"
                            f"{html.escape(txt[:2000])}</div>")
            else:
                parts.append("<p><b>Labeler verdict: no localizable evidence.</b> "
                             "Check whether that is right (question b).</p>")
            parts.append("<details><summary>the full segmented document</summary>")
            for ui, title, sents in seg:
                parts.append(f"<div class='unit'>UNIT {ui}: {html.escape(title or '(untitled)')}</div>")
                for si, _a, _b, txt in sents:
                    parts.append(f"<span class='s'>[{si}] {html.escape(txt)}</span>")
            parts.append("</details>")
            parts.append(
                f"<div class='verd'><b>Verdict for <code>{pid}</code>:</b> "
                "___________________ &nbsp; (a) spans correct &amp; minimal? "
                "&nbsp; (b) evidence missed?</div>")
        (C.WORK / f"RDEV-readsheet-{reader}.html").write_text("\n".join(parts))
        with open(C.WORK / f"rdev_verdicts_{reader}.csv", "w") as fh:
            fh.write("pair_id,verdict,notes\n")
            for k in order:
                fh.write(f"{draw[k]['topic']}__{draw[k]['docno']},,\n")

    print(json.dumps({k: v for k, v in meta.items() if k != "pairs"}, indent=1))
    print("mean doc chars in draw:",
          round(st.mean([r["doc_chars"] for r in draw])) if draw else None)


if __name__ == "__main__":
    main()
