#!/usr/bin/env python
"""Glossary benchmark, gold labelling: a self-contained, offline labelling page (PRIVATE output).

Committed form of round 1's scratch ``make_sheet_html.py``. Reads the sample
(``build_sample.py``'s ``sample.jsonl``) and the owner's label template (its
``owner_labels.csv``: which items to show, blind) and writes one HTML file that
holds the documents' text, so it must stay private (mode 0600):

    make_sheet_html.py --sample DIR/sample.jsonl --labels DIR/owner_labels.csv \\
        --storage-key glossbench-r2-owner --out DIR/owner_sheet.html [--missed]

The page loads nothing from the network; labels are kept in the browser under
``--storage-key`` (one key per labelling round, so an earlier round's labels never
carry over) and exported as a CSV in the template's column order.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import gold_common as gc  # noqa: E402

HEADER = ["doc", "item", "symbol", "definition", "label (correct|partial|wrong|not-a-symbol)", "note"]


def build_data(sample_rows: list[dict[str, Any]], label_rows: list[list[str]], instructions: str,
               storage_key: str, missed: bool, export_name: str) -> dict[str, Any]:
    """The page's data: the documents named in ``label_rows``, each with only its listed
    items (in the sample's shown order) and its judged text."""
    by_key = {r["key"]: r for r in sample_rows}
    wanted: dict[str, set[str]] = {}
    order: list[str] = []
    for r in label_rows:
        if len(r) < 2 or r[1] == "missed":
            continue
        if r[0] not in by_key:
            raise SystemExit(f"labels name document {r[0]!r}, which is not in the sample")
        if r[0] not in wanted:
            wanted[r[0]] = set()
            order.append(r[0])
        wanted[r[0]].add(r[1])
    docs = []
    for k in order:
        row = by_key[k]
        items = [{"id": it["id"], "symbol": it["symbol"], "definition": it["definition"]}
                 for it in row["items"] if it["id"] in wanted[k]]
        if len(items) != len(wanted[k]):
            raise SystemExit(f"{k}: the labels name items the sample does not hold")
        docs.append({"id": k, "group": row.get("corpus") or gc.doc_alias(k), "items": items,
                     "text": row["judged_text"], "chars": int(row.get("text_chars") or len(row["judged_text"]))})
    return {"instructions": instructions, "header": HEADER, "docs": docs, "storage_key": storage_key,
            "missed": missed, "export_name": export_name}


def render(template: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    if template.count("/*__DATA__*/null") != 1:
        raise SystemExit("template has no single /*__DATA__*/null placeholder")
    return template.replace("/*__DATA__*/null", payload)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sample", required=True)
    ap.add_argument("--labels", required=True, help="the label template CSV (which items to show)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--storage-key", required=True, help="browser storage key, one per labelling round")
    ap.add_argument("--missed", action="store_true", help="also ask for missed symbols per document")
    ap.add_argument("--rubric-html", default=str(_HERE / "rubric_v3_human.html"))
    ap.add_argument("--template", default=str(_HERE / "sheet_template.html"))
    args = ap.parse_args(argv)
    rows = [json.loads(x) for x in Path(args.sample).read_text(encoding="utf-8").splitlines() if x.strip()]
    labels = list(csv.reader(Path(args.labels).open(encoding="utf-8")))[1:]
    data = build_data(rows, labels, Path(args.rubric_html).read_text(encoding="utf-8"), args.storage_key,
                      args.missed, Path(args.labels).name)
    out = render(Path(args.template).read_text(encoding="utf-8"), data)
    gc.write_private(Path(args.out), out)
    print(args.out, len(out), "bytes;", len(data["docs"]), "docs;", sum(len(d["items"]) for d in data["docs"]), "items")
    return 0


if __name__ == "__main__":
    sys.exit(main())
