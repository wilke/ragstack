#!/usr/bin/env python3
"""Alias study (OA-1000): counts-only record of the owner's tags on the 19 judged-pair spot-check
disagreements (prereg §13.5; RESULTS.md, *The judge-validity flag*).

    python3 -I make_disagreement_tags.py --tags TAGS.json --items ITEMS.json --out disagreement-tags.json

The owner's 100-pair spot check (prereg §13.5) disagreed with the final judge label on 19 pairs. The
owner later tagged each of the 19 as *full*, *partial*, *none* or *other*: whether the passage
answers the question in full, in part, not at all, or with another answer. The tags are
descriptive. They change no figure, enter no rule, and are not an independent annotation: the owner
set every tag, and for some items discussed alternative readings with an LLM before deciding (the
owner's own account, in the private provenance note beside the tag export).

**Inputs (private, never in the repository).** The owner's tag export (``tags``: item, tag, note)
and the sheet items of the 19 pairs (item, stratum, the judge's final label, the owner's spot-check
label, the term, question, title, definitions, passage, the owner's note). Both sha256s are pinned
below and verified before anything is read further; a mismatch stops the script.

**Output (public, counts only).** Tag totals; the cross-tab by direction of the disagreement
(*judge answers, owner not* and *owner answers, judge not*) × tag; by spot-check stratum × tag; the
two readings of the tags that RESULTS.md quotes (under *partial = not* and under *partial =
answers*, how many of the 19 the judge's label and the owner's label each match); the two input
sha256s; and ``experiment_provenance()`` without ``host`` and ``warnings`` (prereg §11, P6). The
output is refused if any string of it is an item id or a window of any sheet text
(:func:`alias_study_common.public_problems`). No id, note or text is copied out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[4]  # artifacts/ -> alias-study-2026-11/ -> results/ -> plans/ -> docs/ -> repo
for _p in (REPO / "python", REPO / "python" / "scripts" / "eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402

SCHEMA = "alias-study-spot-disagreement-tags/1"
LABEL = ("descriptive; the owner's tags on the 19 spot-check disagreements; changes no figure, enters "
         "no rule, not an independent annotation (owner-set, some items after LLM-assisted deliberation)")
TAGS_SHA256 = "825e49898289752134e7017733bfc14630d825e8203c896a7e2bdc80e789514a"
ITEMS_SHA256 = "d6176dacbe7d7d9cc2d4ff6043b0e9be8b782b35d7bde5024ec110e562958a06"
TAGS = ("full", "partial", "none", "other")
LABELS = ("answers", "not")
STRATA = ("answers", "not", "tiebroken")
N_EXPECTED = 19
#: the private sheet fields whose text must never reach the output
TEXT_FIELDS = ("term", "question", "title", "definitions", "passage", "owner_note", "note")


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def direction(judge: str, owner: str) -> str:
    """The direction of a disagreement, from the judge's final label and the owner's label."""
    if judge == "answers" and owner == "not":
        return "judge_answers_owner_not"
    if judge == "not" and owner == "answers":
        return "owner_answers_judge_not"
    raise SystemExit(f"not a disagreement: judge {judge!r}, owner {owner!r}")


def implied_label(tag: str, reading: str) -> str | None:
    """The label a tag implies under a reading of *partial*: ``partial_is_not`` (only a full answer
    counts as *answers*) or ``partial_is_answers`` (a partial answer counts too). *none* implies
    *not* under both; *other* implies neither and is counted apart."""
    if tag == "full":
        return "answers"
    if tag == "none":
        return "not"
    if tag == "partial":
        return "answers" if reading == "partial_is_answers" else "not"
    return None


def readings(rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for reading in ("partial_is_not", "partial_is_answers"):
        judge_match = owner_match = undecided = 0
        for r in rows:
            lab = implied_label(r["tag"], reading)
            if lab is None:
                undecided += 1
                continue
            judge_match += int(r["judge"] == lab)
            owner_match += int(r["owner"] == lab)
        out[reading] = {"n": len(rows), "judge_matches": judge_match, "owner_matches": owner_match,
                        "undecided_other": undecided}
    out["rule"] = ("a tag implies a label: full -> answers; none -> not; partial -> not under "
                   "partial_is_not, answers under partial_is_answers; other -> undecided. judge_matches "
                   "(owner_matches) counts the 19 pairs whose judge (owner) label equals the implied label")
    return out


def counts(rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    by_tag = Counter(r["tag"] for r in rows)
    by_dir = Counter(r["direction"] for r in rows)
    by_dir_tag = Counter((r["direction"], r["tag"]) for r in rows)
    by_stratum = Counter(r["stratum"] for r in rows)
    by_stratum_tag = Counter((r["stratum"], r["tag"]) for r in rows)
    by_stratum_dir = Counter((r["stratum"], r["direction"]) for r in rows)
    dirs = ("judge_answers_owner_not", "owner_answers_judge_not")
    return {
        "n": len(rows),
        "tags": {t: by_tag.get(t, 0) for t in TAGS},
        "by_direction": {d: {"n": by_dir.get(d, 0), **{t: by_dir_tag.get((d, t), 0) for t in TAGS}} for d in dirs},
        "by_stratum": {s: {"n": by_stratum.get(s, 0), **{t: by_stratum_tag.get((s, t), 0) for t in TAGS},
                           **{d: by_stratum_dir.get((s, d), 0) for d in dirs}} for s in STRATA},
        "readings": readings(rows),
    }


def join(tags: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    by_item = {str(i["item"]): i for i in items}
    if len(by_item) != len(items):
        raise SystemExit("duplicate item ids in the items file")
    tagged = {str(t["item"]): t for t in tags}
    if len(tagged) != len(tags):
        raise SystemExit("duplicate item ids in the tags file")
    if set(tagged) != set(by_item):
        raise SystemExit(f"the tags and the items name different item sets ({len(tagged)} vs {len(by_item)})")
    rows = []
    for item, t in tagged.items():
        i = by_item[item]
        tag, judge, owner, stratum = str(t["tag"]), str(i["judge"]), str(i["owner"]), str(i["stratum"])
        if tag not in TAGS:
            raise SystemExit(f"unknown tag {tag!r}")
        if judge not in LABELS or owner not in LABELS or stratum not in STRATA:
            raise SystemExit("unknown label or stratum")
        rows.append({"tag": tag, "judge": judge, "owner": owner, "stratum": stratum,
                     "direction": direction(judge, owner)})
    if len(rows) != N_EXPECTED:
        raise SystemExit(f"{len(rows)} disagreements, expected {N_EXPECTED}")
    return rows


def provenance() -> dict[str, Any]:
    """``experiment_provenance()`` without host or warnings (prereg §11, P6)."""
    try:
        from ragstack.provenance import experiment_provenance
        prov = dict(experiment_provenance(repo=REPO))
    except Exception as e:  # noqa: BLE001
        return {"commit": None, "dirty": None, "untracked": None, "citable": False,
                "error": f"experiment_provenance failed: {type(e).__name__}"}
    for k in ("host", "warnings"):
        prov.pop(k, None)
    return prov


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tags", required=True, type=Path, help="the owner's tag export (private)")
    ap.add_argument("--items", required=True, type=Path, help="the sheet items of the 19 pairs (private)")
    ap.add_argument("--out", required=True, type=Path, help="the public counts-only JSON")
    args = ap.parse_args(argv)
    shas = {"tags": sha256_file(args.tags), "items": sha256_file(args.items)}
    if shas["tags"] != TAGS_SHA256 or shas["items"] != ITEMS_SHA256:
        print(f"STOP: input sha256 mismatch: {shas}", file=sys.stderr)
        return 2
    tags_doc = json.loads(args.tags.read_text(encoding="utf-8"))
    items = json.loads(args.items.read_text(encoding="utf-8"))
    tags = tags_doc["tags"]
    rows = join(tags, items)
    res: dict[str, Any] = {
        "schema": SCHEMA, "label": LABEL,
        "tags_schema": tags_doc.get("schema"), "tags_exported_at": tags_doc.get("exported_at"),
        "who": "the owner set every tag; for some items the owner discussed alternative readings with an "
               "LLM before deciding (the owner's account; private provenance note beside the export)",
        "counts": counts(rows),
        "inputs_sha256": {"disagreement-tags-19.json": shas["tags"], "disagreement-items-19.json": shas["items"]},
        "provenance": provenance(),
    }
    res["citable"] = bool(res["provenance"].get("citable"))
    forbidden = {str(i["item"]) for i in items} | {str(t["item"]) for t in tags}
    texts = [str(i.get(f, "")) for i in items for f in TEXT_FIELDS] + [str(t.get("note", "")) for t in tags]
    texts.append(str(tags_doc.get("note", "")))
    bad = asc.public_problems(res, forbidden, texts)
    if bad:
        print(f"REFUSED: the output would carry {len(bad)} private string(s) ({sorted(set(bad))})", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(res["counts"], indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
