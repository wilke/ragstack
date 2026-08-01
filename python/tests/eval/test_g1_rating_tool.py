"""Behavioural tests for the browser rating tool, driven through node.

``scripts/eval/rating_tool/index.html`` is the only part of the G1 rating
apparatus a rater actually touches, and the properties the protocol depends on
live in its JavaScript: it must **refuse** an assignment that would break
blinding (§4.4), it must present items in a **seeded** order so position effects
do not correlate across raters, and its export must carry the grade, the rater,
the timestamp, the seconds-on-item and that seed. ``rating_tool_harness.js``
stubs a minimal DOM, runs a full session (load → grade → skip → undo → finish →
export), and prints what happened; this module asserts on it.

Skipped when node is unavailable — the tool itself needs no toolchain, and
neither should the rest of the suite.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_TOOL = _HERE.parents[1] / "scripts" / "eval" / "rating_tool" / "index.html"
_HARNESS = _HERE / "rating_tool_harness.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(scope="module")
def session() -> dict:
    proc = subprocess.run(
        [shutil.which("node") or "node", str(_HARNESS), str(_TOOL)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_tool_refuses_a_file_that_breaks_blinding(session):
    """Every line carrying ``llm_grade`` is rejected — not stripped. A silent
    strip would let a broken pipeline keep producing plausible-looking data."""
    assert session["refuses_leaky"] is True
    assert session["violations"] == ["cell_id"]


def test_tool_loads_a_clean_assignment(session):
    assert session["parsed"] == 12
    assert session["parse_problems"] == []


def test_presentation_order_is_shuffled_seeded_and_reproducible(session):
    assert session["shuffle_deterministic"] is True
    assert session["shuffle_seed_sensitive"] is True
    assert session["shuffle_is_permutation"] is True
    assert isinstance(session["seed"], int)
    assert sorted(int(x) for x in session["queue"].split(",")) == list(range(12))
    assert session["queue"] != ",".join(str(i) for i in range(12)), "order must not be file order"


def test_grade_skip_and_undo(session):
    assert session["after_two"] == 2
    assert session["after_undo"] == 1, "undo must remove the judgment, not just move back"
    assert session["all_graded"] == 12, "a skipped item must come back before the session ends"


def test_export_carries_everything_the_analysis_needs(session):
    assert session["export_n"] == 12
    assert set(session["export_keys"]) >= {
        "pair_id", "grade", "rater_id", "timestamp", "seconds_on_item", "shuffle_seed",
    }
    assert session["seed_on_every_line"] is True
    assert session["grades_in_range"] is True
    assert session["has_seconds"] is True


def test_session_manifest_and_crash_recovery(session):
    assert {"shuffle_seed", "blinding_check", "grade_histogram", "n_rated"} <= set(
        session["manifest_keys"]
    )
    assert session["resume_grade_count"] == 12, "progress must survive in localStorage"
