"""Tests for the grading-page builder on synthetic items (no private data)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "part4_grading_page"))
import build  # noqa: E402

SECRET = ("T512o0", "B4096", "scout", "opus48", "part_b", "record_id")


def items_file(tmp_path: Path) -> Path:
    p = tmp_path / "items.json"
    p.write_text(json.dumps({"labels": ["correct"], "note": "x", "items": [
        {"item": "h000", "question": "Q </script><!-- one", "reference": ["r1", "r2"],
         "answer": "A   line", "arm": "T512o0", "record_id": "M1"},
        {"item": "h001", "question": "Q two", "reference": ["r"], "answer": "B"},
    ]}))
    return p


def test_page_embeds_only_blinded_fields_and_escapes(tmp_path):
    html = build.build(items_file(tmp_path))
    data = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert "</script" not in data and "<!--" not in data and " " not in data
    payload = json.loads(data)
    assert set(payload["items"][0]) == set(build.FIELDS)
    assert payload["items"][0]["question"] == "Q </script><!-- one"
    for word in SECRET:
        assert word not in data
    assert re.fullmatch(r"part4-[0-9a-f]{12}", payload["export_id"])


def test_no_external_requests_in_template():
    t = (Path(build.__file__).parent / "template.html").read_text()
    assert not re.search(r'\b(src|href)\s*=\s*"(https?:)?//', t)
    assert "@import" not in t and "url(" not in t


def test_refuses_to_write_inside_repo(tmp_path):
    inside = Path(build.__file__).resolve().parent / "should-not-exist.html"
    with pytest.raises(SystemExit, match="inside the repository"):
        build.main(["--items", str(items_file(tmp_path)), "--out", str(inside)])
    assert not inside.exists()
    out = tmp_path / "page.html"
    assert build.main(["--items", str(items_file(tmp_path)), "--out", str(out)]) == 0 and out.exists()
