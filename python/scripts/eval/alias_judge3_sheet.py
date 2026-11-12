"""Alias study, judge validation 2: the per-rater sheet writer (JUDGE-VALIDATION-2-PLAN.md §2, §2.3,
§6.3; JUDGE-3-PROMPT.md §4, §5).

The sheet reproduces ``alias-judge/3``'s *Parts and conditions*, *Labels* and *Deciding* blocks (the
plan's §2.2 rubric) and its *Examples* block **byte for byte** from
:func:`glossbench_alias_judge.judge_v3_blocks`, so that raters and judges rate one construct, and shows
each item with the same fields the judges see (the passage paper's title, the definitions block, the
passage, the TERM and the question; no reference answer, no arm, retriever, rank or cell). Each item
has a grade line (full / partial / none / unsure), the *unsure* reason code (§2.3) and the optional
doubt (full / partial) tick.
"""
from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import glossbench_alias_judge as jd  # noqa: E402

SHEET_SCHEMA = "alias-judge3-rater-sheet/1"
UNSURE_CODES = ("meaning", "text", "question", "expertise")
RUBRIC_HEADING = "## Rubric"
EXAMPLES_HEADING = "## Examples"


def item_context(item: Mapping[str, Any]) -> str:
    """The item's fields exactly as the judge's user message shows them, without its closing line."""
    msg = jd.judge_prompt_v3(item["question"], item["term"], item["passage"], item.get("title") or "",
                             [tuple(d) for d in item.get("defs") or []])
    return msg.removesuffix(jd.JUDGE_CLOSING_V3).rstrip("\n")


def sheet_text(rater_id: str, items: Sequence[Mapping[str, Any]]) -> str:
    b = jd.judge_v3_blocks()
    parts = [f"# Rater sheet ({SHEET_SCHEMA}), rater {rater_id}, {len(items)} items", "",
             "Grade each item from the passage, its title and its definitions only, with the rubric below "
             "(the judges get the same text). Use unsure only when the item cannot be graded, with a reason "
             f"code: {', '.join(UNSURE_CODES)}. Tick doubt when unsure between full and partial.", "",
             RUBRIC_HEADING, "", "```text", b["rubric"], "```", "",
             EXAMPLES_HEADING, "", "```text", b["examples"], "```", ""]
    for i, it in enumerate(items, 1):
        parts += [f"## Item {i}: {it['item_id']}", "", "```text", item_context(it), "```", "",
                  "GRADE (full / partial / none / unsure): ",
                  f"UNSURE REASON ({' / '.join(UNSURE_CODES)}): ",
                  "DOUBT (full / partial) [ ]", ""]
    return "\n".join(parts)


def write_sheet(path: Path, rater_id: str, items: Sequence[Mapping[str, Any]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sheet_text(rater_id, items), encoding="utf-8")
    return path
