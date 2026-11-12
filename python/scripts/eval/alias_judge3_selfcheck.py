"""Alias study, judge validation 2: the ``alias-judge/3`` self-check cases (JUDGE-3-PROMPT.md §4;
JUDGE-VALIDATION-2-PLAN.md §3.2, §4, §11 step 3).

Synthetic cases, **not** the prompt's six examples, at least one per rule, in the shape of
``VERIFY_CASES`` (``tests/eval/test_glossbench_alias.py``): one dict per case with

* ``name`` -- a unique id; the committed list order is the canary selection's tie order (plan §4);
* ``rule`` -- which rule of ``JUDGE_SYSTEM_V3`` the case exercises (free text);
* ``title``, ``defs`` (a list of ``[alias, primary]``), ``passage``, ``term``, ``question`` -- the
  fields every judge sees (:func:`glossbench_alias_judge.judge_prompt_v3`);
* ``expected_label`` -- ``full`` / ``partial`` / ``none``, **set by the owner**. It is empty until then,
  and :func:`require_owner_labels` refuses to run a self-check, a canary selection or a canary while
  any case's label is empty.

The draft list is provided separately; :data:`SELF_CHECK_CASES_V3` stays empty here until the owner
has set the labels, and :func:`load_cases` reads a JSON file of the same shape meanwhile.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

GRADES = ("full", "partial", "none")
CASE_FIELDS = ("name", "rule", "title", "defs", "passage", "term", "question", "expected_label")

#: the committed self-check cases (placeholder: the owner-labelled list replaces it before the freeze)
SELF_CHECK_CASES_V3: list[dict[str, Any]] = []


def validate_cases(cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Shape check (every field present, names unique, ``defs`` a list of pairs, a set label one of
    :data:`GRADES`); an EMPTY ``expected_label`` passes here (it is the owner's to set)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, c in enumerate(cases):
        missing = [f for f in CASE_FIELDS if f not in c]
        if missing:
            raise ValueError(f"self-check case {i}: missing {missing}")
        extra = sorted(set(c) - set(CASE_FIELDS))
        if extra:
            raise ValueError(f"self-check case {i}: unknown keys {extra}")
        name = str(c["name"])
        if not name or name in seen:
            raise ValueError(f"self-check case {i}: name {name!r} empty or repeated")
        seen.add(name)
        defs = c["defs"]
        if not isinstance(defs, list) or any(not isinstance(d, list | tuple) or len(d) != 2 for d in defs):
            raise ValueError(f"self-check case {name}: defs must be a list of [alias, primary]")
        lab = str(c["expected_label"] or "")
        if lab and lab not in GRADES:
            raise ValueError(f"self-check case {name}: expected_label {lab!r} not one of {GRADES}")
        out.append({**dict(c), "defs": [(str(a), str(p)) for a, p in defs], "expected_label": lab})
    return out


def load_cases(path: Path | None = None) -> list[dict[str, Any]]:
    """The cases from ``path`` (a JSON list) or, without one, :data:`SELF_CHECK_CASES_V3`."""
    raw = json.loads(Path(path).read_text(encoding="utf-8")) if path is not None else SELF_CHECK_CASES_V3
    if not isinstance(raw, list):
        raise ValueError("self-check cases: a JSON list is required")
    return validate_cases(raw)


def require_owner_labels(cases: Sequence[Mapping[str, Any]]) -> None:
    """Refuse (``SystemExit``) while the list is empty or any ``expected_label`` is empty: the
    self-check and the canaries are scored against owner-set labels only."""
    if not cases:
        raise SystemExit("self-check: no cases (the owner-labelled list is not committed yet)")
    empty = [str(c.get("name")) for c in cases if not str(c.get("expected_label") or "")]
    if empty:
        raise SystemExit(f"self-check: {len(empty)} case(s) without an owner-set expected_label: {empty[:5]}")
