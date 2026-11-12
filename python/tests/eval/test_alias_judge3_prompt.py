"""alias-judge/3 (JUDGE-3-PROMPT.md; JUDGE-VALIDATION-2-PLAN.md §2.2, §3.2): the system text, the user
message, the graded parser and its fail-closed property. Offline: no model, no network.

Each test names the mutation it catches (the guarded logic removed or changed -> the test fails).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import glossbench_alias_judge as jd  # noqa: E402

DOCS = Path(__file__).resolve().parents[3] / "docs" / "plans" / "alias-study-2026-11"


def _text_block(md: str, heading: str) -> str:
    i = md.index(heading)
    s = md.index("```text\n", i) + len("```text\n")
    return md[s:md.index("\n```", s)]


def test_system_v3_equals_the_prompt_document_byte_for_byte() -> None:
    # mutation: any edit to JUDGE_SYSTEM_V3 (or to the document's block) fails this
    block = _text_block((DOCS / "JUDGE-3-PROMPT.md").read_text(encoding="utf-8"), "### 1.2 System message")
    assert jd.JUDGE_SYSTEM_V3.encode("utf-8") == block.encode("utf-8")
    assert jd.JUDGE_SYSTEMS["alias-judge/3"] is jd.JUDGE_SYSTEM_V3


def test_plan_rubric_equals_the_prompt_rubric_blocks() -> None:
    # mutation: a drift between plan §2.2 and the Parts/Labels/Deciding blocks of the prompt fails this
    plan = _text_block((DOCS / "JUDGE-VALIDATION-2-PLAN.md").read_text(encoding="utf-8"),
                       "### 2.2 Rubric (verbatim from `alias-judge/3`)")
    b = jd.judge_v3_blocks()
    assert plan == b["rubric"]
    assert b["rubric"] == "\n\n".join((b["parts"], b["labels"], b["deciding"]))
    assert b["examples"].startswith("Examples (synthetic") and b["examples"].count("\n- ") == 6


def test_v3_keeps_the_v1_v2_pieces_verbatim() -> None:
    # mutation: rewording the opening's second sentence, /1's first two bullets or JUDGE_RULE_V2 in /3
    v1_lines = jd.JUDGE_SYSTEM.split("\n")
    opening2 = "Judge only from the text you are given, never from outside knowledge of any specific paper."
    assert opening2 in v1_lines[0] and opening2 in jd.JUDGE_SYSTEM_V3.split("\n")[0]
    bullets = [x for x in v1_lines if x.startswith("- ")][:2]
    assert len(bullets) == 2 and all(b in jd.JUDGE_SYSTEM_V3.split("\n") for b in bullets)
    assert jd.JUDGE_RULE_V2 in jd.JUDGE_SYSTEM_V3.split("\n")
    assert jd.JUDGE_SYSTEM_V3.endswith(jd.JUDGE_OUTPUT_FORMAT_V3)
    assert jd.JUDGE_OUTPUT_FORMAT not in jd.JUDGE_SYSTEM_V3
    # /1 and /2 are untouched by the registry change
    assert jd.JUDGE_SYSTEMS["alias-judge/1"] is jd.JUDGE_SYSTEM
    assert jd.JUDGE_SYSTEMS["alias-judge/2"] is jd.JUDGE_SYSTEM_V2


def test_user_message_v3_differs_in_the_last_line_only() -> None:
    # mutation: changing anything but the closing line of judge_prompt_v3 (or not replacing it) fails
    args = ("What is the half-life of tarsin?", "tarsin", "Tarsin had a half-life of 12 days.", "A title",
            [("TS", "tarsin")])
    a, b = jd.judge_prompt(*args).split("\n"), jd.judge_prompt_v3(*args).split("\n")
    assert a[:-1] == b[:-1] and a[-1] == jd.JUDGE_CLOSING and b[-1] == jd.JUDGE_CLOSING_V3
    assert jd.judge_user_message("alias-judge/3", *args) == jd.judge_prompt_v3(*args)
    assert jd.judge_user_message("alias-judge/2", *args) == jd.judge_prompt(*args)


VALID = [
    ('{"reason": "temperature stated", "answers": "full"}', "full", "temperature stated"),
    ('```json\n{"reason": "r", "answers": "partial"}\n```', "partial", "r"),
    ('Here it is: {"reason": "r", "answers": "none"} done.', "none", "r"),
    ('<think>{"answers": "none"}</think>{"reason": "r", "answers": "full"}', "full", "r"),
    ('{"answers": "partial", "reason": "order", "extra": 1}', "partial", "order"),
    ('{"answers": " FULL "}', "full", ""),
    ('{"reason": "a", "answers": "none"} then {"reason": "b", "answers": "full"}', "full", "b"),
]
INVALID = ['{"reason": "r", "answers": "yes"}', '{"reason": "r", "answers": "no"}',
           '{"reason": "r", "answers": "unsure"}', '{"reason": "r", "answers": "partially"}',
           '{"reason": "r", "answers": "full answer"}', '{"reason": "r", "answers": "Full."}',
           '{"reason": "r", "answers": ["full"]}', '{"reason": "r", "answers": 1}', "full", "",
           # an echoed example uses arrow notation, not JSON
           '-> "full": the temperature is stated; the duration and the counting are extra.']


@pytest.mark.parametrize("text,grade,reason", VALID)
def test_parse_v3_valid(text: str, grade: str, reason: str) -> None:
    # mutation: dropping the think-strip, the last-object rule or the case-fold fails one case
    assert jd.parse_judgement_v3(text) == (grade, reason)


@pytest.mark.parametrize("text", INVALID)
def test_parse_v3_invalid_no_synonyms(text: str) -> None:
    # mutation: mapping any synonym (yes -> full, partially -> partial, ...) fails this
    assert jd.parse_judgement_v3(text) == (None, "")


@pytest.mark.parametrize("g", jd.GRADES)
@pytest.mark.parametrize("b", ["yes", "no"])
def test_cross_version_fails_closed(g: str, b: str) -> None:
    # mutation: letting either parser accept the other version's value set fails this
    v3 = json.dumps({"reason": "r", "answers": g})
    v2 = json.dumps({"reason": "r", "answers": b})
    assert jd.parse_judgement(v3) == (None, "")
    assert jd.parse_judgement_v3(v2) == (None, "")
    assert jd.parse_judgement(v2)[0] is (b == "yes")


def test_grade_readings() -> None:
    # mutation: swapping strict and lenient, or reading partial as strict-yes, fails this
    assert jd.grade_readings("full") == {"strict": True, "lenient": True}
    assert jd.grade_readings("partial") == {"strict": False, "lenient": True}
    assert jd.grade_readings("none") == {"strict": False, "lenient": False}
    assert jd.grade_readings(None) == {"strict": None, "lenient": None}
    with pytest.raises(ValueError):
        jd.grade_readings("yes")


@pytest.mark.parametrize("blk", ["qwen", "judge_gw"])
def test_binary_backends_refuse_the_graded_version(blk: str) -> None:
    # mutation: removing the merged() guard lets a binary backend store /3 replies as invalid verdicts
    with pytest.raises(SystemExit, match="graded"):
        jd.merged({blk: {"judge_version": "alias-judge/3"}})
    jd.merged({blk: {"judge_version": "alias-judge/2"}})
