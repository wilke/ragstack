"""Build the blinded, offline human-grading page from a ``part4 --export-human`` items file.

The page is ``template.html`` with the items embedded as JSON. Only ``item``, ``question``,
``reference`` and ``answer`` are carried, so nothing about arm, condition, reader, part or model
grade reaches the page. The export id is the one ``part4_human_import`` checks:
``part4-`` + the first 12 hex digits of sha256(items file).

Both input and output are PRIVATE, and the builder refuses to write inside the repository::

    PYTHONPATH=python python python/scripts/eval/part4_grading_page/build.py \\
        --items /private/human-items-part4.json --out /private/grading-page-part4.html
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import part4_human_import as hi  # noqa: E402

FIELDS = ("item", "question", "reference", "answer")


def embed(payload: object) -> str:
    """JSON safe inside ``<script type="application/json">``: every ``<`` becomes the JSON escape
    ``\\u003c`` (so no ``</script`` or ``<!--`` can appear), and raw U+2028/9 are escaped too.
    The result is still valid JSON for ``JSON.parse``."""
    s = json.dumps(payload, ensure_ascii=False)
    return s.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def build(items_path: Path) -> str:
    d = json.loads(items_path.read_text())
    payload = {"export_id": hi.export_id(items_path),
               "items": [{k: it[k] for k in FIELDS} for it in d["items"]]}
    template = (HERE / "template.html").read_text()
    if template.count("__ITEMS__") != 1:
        raise SystemExit("template must contain exactly one __ITEMS__ placeholder")
    return template.replace("__ITEMS__", embed(payload))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--items", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    repo = HERE.parents[3]
    rp = args.out.resolve()
    if rp == repo or repo in rp.parents:
        raise SystemExit("refusing to write the private grading page inside the repository")
    args.out.write_text(build(args.items))
    print(json.dumps({"wrote": str(args.out), "export_id": hi.export_id(args.items)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
