"""Checkpoint round-trip for the JSONL ingest script.

Guards the resume-safety fix: the checkpoint persists the active --doc-types
filter so a resume under a different filter can be detected rather than
silently skipping lines the new filter would keep. The script lives in
scripts/ (not the package), so load it by path.
"""
import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ingest_jsonl.py"
_spec = importlib.util.spec_from_file_location("ingest_jsonl", _SCRIPT)
ingest_jsonl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest_jsonl)


def test_checkpoint_roundtrips_line_and_doc_types(tmp_path):
    ckpt = tmp_path / "c.ckpt"
    ingest_jsonl._write_checkpoint(ckpt, 42, ["article", "supplement"])
    assert ingest_jsonl._read_checkpoint(ckpt) == {
        "line": 42,
        "doc_types": ["article", "supplement"],
        "done_ranges": [],
    }


def test_checkpoint_none_filter(tmp_path):
    ckpt = tmp_path / "c.ckpt"
    ingest_jsonl._write_checkpoint(ckpt, 7, None)
    assert ingest_jsonl._read_checkpoint(ckpt) == {
        "line": 7,
        "doc_types": None,
        "done_ranges": [],
    }


def test_missing_checkpoint_is_zero(tmp_path):
    assert ingest_jsonl._read_checkpoint(tmp_path / "nope.ckpt") == {
        "line": 0,
        "doc_types": None,
        "done_ranges": [],
    }


@pytest.mark.parametrize("garbage", ["", "not json", "{", "{\"line\": \"x\"}"])
def test_corrupt_checkpoint_falls_back_to_zero(tmp_path, garbage):
    ckpt = tmp_path / "c.ckpt"
    ckpt.write_text(garbage)
    assert ingest_jsonl._read_checkpoint(ckpt) == {
        "line": 0,
        "doc_types": None,
        "done_ranges": [],
    }


def test_legacy_bare_int_checkpoint_still_read(tmp_path):
    # Pre-fix checkpoints were a bare integer line number, no filter recorded.
    ckpt = tmp_path / "c.ckpt"
    ckpt.write_text("123")
    assert ingest_jsonl._read_checkpoint(ckpt) == {
        "line": 123,
        "doc_types": None,
        "done_ranges": [],
    }


def test_checkpoint_roundtrips_done_ranges(tmp_path):
    ckpt = tmp_path / "c.ckpt"
    ingest_jsonl._write_checkpoint(ckpt, 3, None, [[5, 9], [12, 14]])
    assert ingest_jsonl._read_checkpoint(ckpt) == {
        "line": 3,
        "doc_types": None,
        "done_ranges": [[5, 9], [12, 14]],
    }


def test_read_checkpoint_sanitizes_malformed_done_ranges(tmp_path):
    # A hand-edited/corrupt done_ranges must not break resume — it degrades to [].
    ckpt = tmp_path / "c.ckpt"
    ckpt.write_text('{"line": 3, "doc_types": null, "done_ranges": "garbage"}')
    assert ingest_jsonl._read_checkpoint(ckpt)["done_ranges"] == []


def test_union_range_coalesces_overlap_and_abut():
    # Overlapping and adjacent (gap-of-1) intervals merge; disjoint stay separate.
    assert ingest_jsonl._union_range([[1, 3]], 4, 6) == [[1, 6]]  # abut (3,4)
    assert ingest_jsonl._union_range([[1, 5]], 3, 9) == [[1, 9]]  # overlap
    assert ingest_jsonl._union_range([[1, 3]], 6, 8) == [[1, 3], [6, 8]]  # gap
    assert ingest_jsonl._union_range([[6, 8], [1, 3]], 4, 5) == [[1, 8]]  # bridge


def test_trim_below_drops_and_clips():
    assert ingest_jsonl._trim_below([[1, 5], [8, 10]], 5) == [[8, 10]]  # drop covered
    assert ingest_jsonl._trim_below([[3, 10]], 5) == [[6, 10]]  # clip straddling
    assert ingest_jsonl._trim_below([[3, 5]], 10) == []  # all subsumed


def test_line_covered_frontier_or_range():
    assert ingest_jsonl._line_covered(2, 5, []) is True  # <= frontier
    assert ingest_jsonl._line_covered(7, 5, [[6, 9]]) is True  # in a range
    assert ingest_jsonl._line_covered(10, 5, [[6, 9]]) is False  # gap above ranges
    assert ingest_jsonl._line_covered(6, 5, [[8, 9]]) is False  # between frontier and range
