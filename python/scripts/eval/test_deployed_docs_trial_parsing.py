"""Label parsing for the deployed-docs trial's judges/graders (Opus 5 writes "**Label** — sentence")."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deployed_docs_trial as ct  # noqa: E402


def test_bold_partial_with_sentence() -> None:
    assert ct.judge_word("**Partial** — x") == "partial"


def test_judge_words() -> None:
    assert ct.judge_word("Yes.") == "yes"
    assert ct.judge_word("**No** - the text is about something else") == "no"
    assert ct.judge_word("") == "unparseable"
    assert ct.judge_word("Maybe") == "unparseable"


def test_legacy_grade_word_first_token() -> None:
    """The k10 / part-3 parser (first token only); still used by those subcommands."""
    assert ct.grade_word("**Partial** — x") == "partial"
    assert ct.grade_word("**Correct** — states the reference") == "correct"
    assert ct.grade_word("Abstained.") == "abstained"
    assert ct.grade_word("**Incorrect**") == "incorrect"
    assert ct.grade_word("The answer is correct") == "unparseable"


def test_part4_grade_parser() -> None:
    """Part 4 replaces the first-token rule (prereg §5): bold / Grade: label, else exactly one
    distinct label word, never a negated one. Full cases in test_deployed_docs_part4.py."""
    import deployed_docs_part4 as p4

    assert p4.parse_grade("**Partial** — x")[0] == "partial"
    assert p4.parse_grade("The answer is correct")[0] == "correct"
    assert p4.parse_grade("The answer is not correct")[0] == "unparseable"
    assert p4.parse_grade("Not correct: it is incorrect")[0] == "unparseable"
    assert p4.parse_grade("Grade: abstained")[0] == "abstained"


def test_missing_private_config_exits() -> None:
    import pytest

    with pytest.raises(SystemExit, match="private config is required"):
        ct.load_private_config(None)
