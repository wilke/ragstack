"""Tests for the grading-page builder on synthetic items (no private data)."""

from __future__ import annotations

import hashlib
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


def labelling_items_file(tmp_path: Path) -> Path:
    p = tmp_path / "addq-items.json"
    p.write_text(json.dumps({"labels": ["answer", "setup", "neither"], "note": "x", "items": [
        {"item": "q000", "question": "Q </script><!-- one", "quote": "quote line", "record_id": "M000k1",
         "position": "B", "label": "scout"},
        {"item": "q001", "question": "Q two", "quote": "another"},
    ]}))
    return p


def _data(html: str) -> str:
    return re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)


def test_labelling_page_embeds_only_blinded_fields_and_escapes(tmp_path):
    p = labelling_items_file(tmp_path)
    html = build.build(p, "labelling")
    data = _data(html)
    assert "</script" not in data and "<!--" not in data and " " not in data
    payload = json.loads(data)
    assert [set(it) for it in payload["items"]] == [set(build.LABELLING_FIELDS)] * 2
    assert payload["items"][0]["question"] == "Q </script><!-- one"
    assert payload["items"][0]["quote"] == "quote line"
    for word in ("M000k1", "position", "record_id", "scout"):
        assert word not in data
    sha12 = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    assert payload["export_id"] == "addq-" + sha12
    assert "quote-labelling:" in html  # the labelling template, not the grading one
    assert build.export_id(p) == "part4-" + sha12  # the grading kind keeps its own prefix


def test_labelling_cli_kind(tmp_path):
    p, out = labelling_items_file(tmp_path), tmp_path / "lab.html"
    assert build.main(["--kind", "labelling", "--items", str(p), "--out", str(out)]) == 0
    assert out.read_text() == build.build(p, "labelling")
    with pytest.raises(SystemExit):
        build.main(["--kind", "other", "--items", str(p), "--out", str(out)])


@pytest.mark.parametrize("name", ["template.html", "labelling-template.html"])
def test_no_external_requests_in_templates(name):
    t = (Path(build.__file__).parent / name).read_text()
    assert t.count("__ITEMS__") == 1
    assert not re.search(r'\b(src|href)\s*=\s*"(https?:)?//', t)
    assert "@import" not in t and "url(" not in t and "fetch(" not in t and "XMLHttpRequest" not in t
