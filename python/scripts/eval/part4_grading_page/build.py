"""Build the blinded, offline human-grading page from a ``part4 --export-human`` items file.

The page is ``template.html`` with the items embedded as JSON. Only ``item``, ``question``,
``reference`` and ``answer`` are carried, so nothing about arm, condition, reader, part or model
grade reaches the page. The export id is the one ``part4_human_import`` checks:
``part4-`` + the first 12 hex digits of sha256(items file).

``--kind labelling`` builds addendum Q's quote-labelling page instead (from a
``part4_addendum_q.py export-human`` items file): ``labelling-template.html``, the fields
``item``, ``question`` and ``quote`` only (no record id, no position, no model label), and the
export id ``addq-`` + the same 12 hex digits, which ``part4_addendum_q.py import-human --items``
checks.

Both input and output are PRIVATE, and the builder refuses to write inside the repository::

    PYTHONPATH=python python python/scripts/eval/part4_grading_page/build.py \\
        --items /private/human-items-part4.json --out /private/grading-page-part4.html
    PYTHONPATH=python python python/scripts/eval/part4_grading_page/build.py --kind labelling \\
        --items /private/addq-human-items.json --out /private/labelling-page-addq.html
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import part4_human_import as hi  # noqa: E402

FIELDS = ("item", "question", "reference", "answer")
LABELLING_FIELDS = ("item", "question", "quote")
KINDS = {"grading": ("template.html", FIELDS), "labelling": ("labelling-template.html", LABELLING_FIELDS)}


def labelling_export_id(items_path: Path) -> str:
    """Addendum Q's page id: ``addq-`` + the first 12 hex of the items file's sha256."""
    return "addq-" + hashlib.sha256(items_path.read_bytes()).hexdigest()[:12]


def export_id(items_path: Path, kind: str = "grading") -> str:
    return labelling_export_id(items_path) if kind == "labelling" else hi.export_id(items_path)


def embed(payload: object) -> str:
    """JSON safe inside ``<script type="application/json">``: every ``<`` becomes the JSON escape
    ``\\u003c`` (so no ``</script`` or ``<!--`` can appear), and raw U+2028/9 are escaped too.
    The result is still valid JSON for ``JSON.parse``."""
    s = json.dumps(payload, ensure_ascii=False)
    return s.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def build(items_path: Path, kind: str = "grading") -> str:
    tname, fields = KINDS[kind]
    d = json.loads(items_path.read_text())
    payload = {"export_id": export_id(items_path, kind),
               "items": [{k: it[k] for k in fields} for it in d["items"]]}
    template = (HERE / tname).read_text()
    if template.count("__ITEMS__") != 1:
        raise SystemExit("template must contain exactly one __ITEMS__ placeholder")
    return template.replace("__ITEMS__", embed(payload))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--kind", choices=sorted(KINDS), default="grading",
                    help="grading (part 4, default) or labelling (addendum Q)")
    ap.add_argument("--items", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    repo = HERE.parents[3]
    rp = args.out.resolve()
    if rp == repo or repo in rp.parents:
        raise SystemExit("refusing to write the private grading page inside the repository")
    args.out.write_text(build(args.items, args.kind))
    print(json.dumps({"wrote": str(args.out), "export_id": export_id(args.items, args.kind)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
