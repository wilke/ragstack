"""Offline tests for the PDF -> JSONL extraction tool (#202/#203).

`scripts/pdf_extract.py` runs the real PdfLoader (PyMuPDF) over a set of PDFs and
emits a JSONL shard in the `{text, path, metadata}` shape that JsonlLoader (and
thus ingest_shard / embed_shard) consume. These tests prove:

* two real one-page PDFs -> two JSONL records with the correct shape + real text;
* a non-PDF / empty file is *skipped* (recorded in the report), never crashes;
* the output ordering is deterministic.

No embedding fleet / Qdrant / ES — pure PyMuPDF + filesystem.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")

# pdf_extract.py lives under python/scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import pdf_extract  # noqa: E402


def _write_pdf(path: Path, text: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def _write_blank_pdf(path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page()  # a page with no text (stand-in for a scanned/image-only PDF)
    doc.save(str(path))
    doc.close()


def test_extract_two_pdfs_shape_and_text(tmp_path: Path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _write_pdf(a, "The quick brown fox")
    _write_pdf(b, "jumps over the lazy dog")

    paths = pdf_extract.iter_pdf_paths([str(tmp_path)])
    assert paths == [a.resolve(), b.resolve()]  # sorted / deterministic

    records, skipped = pdf_extract.extract_pdfs(paths)
    assert skipped == []
    assert len(records) == 2

    # Exact shape JsonlLoader consumes: text / path / metadata.
    for rec in records:
        assert set(rec) == {"text", "path", "metadata"}
        assert isinstance(rec["text"], str) and rec["text"]
        assert rec["path"].endswith(".pdf")
        assert rec["metadata"]["pages"] == 1
        assert rec["metadata"]["filename"] in {"a.pdf", "b.pdf"}

    texts = [r["text"] for r in records]
    assert any("quick brown fox" in t for t in texts)
    assert any("lazy dog" in t for t in texts)


def test_scanned_pdf_is_skipped_not_crashed(tmp_path: Path):
    good = tmp_path / "good.pdf"
    blank = tmp_path / "blank.pdf"
    _write_pdf(good, "real extractable text")
    _write_blank_pdf(blank)  # no extractable text -> PdfLoader raises LoaderError

    records, skipped = pdf_extract.extract_pdfs([good.resolve(), blank.resolve()])

    assert len(records) == 1
    assert "real extractable text" in records[0]["text"]
    assert len(skipped) == 1
    assert skipped[0]["path"] == str(blank.resolve())
    assert skipped[0]["reason"]  # a caller-safe reason string, not a crash


def test_non_pdf_file_is_skipped(tmp_path: Path):
    notpdf = tmp_path / "notes.pdf"  # .pdf suffix but not a PDF
    notpdf.write_text("this is plain text, not a pdf", encoding="utf-8")

    records, skipped = pdf_extract.extract_pdfs([notpdf.resolve()])

    assert records == []
    assert len(skipped) == 1
    assert skipped[0]["path"] == str(notpdf.resolve())


def test_main_writes_jsonl_and_report(tmp_path: Path):
    good = tmp_path / "g.pdf"
    blank = tmp_path / "blank.pdf"
    _write_pdf(good, "hello ragstack")
    _write_blank_pdf(blank)
    out = tmp_path / "shard.jsonl"
    report = tmp_path / "shard.report.json"

    rc = pdf_extract.main(
        [str(good), str(blank), "--out", str(out), "--report", str(report)]
    )
    assert rc == 0

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # only the good PDF made it into the shard
    rec = json.loads(lines[0])
    assert "hello ragstack" in rec["text"]
    assert set(rec) == {"text", "path", "metadata"}

    rep = json.loads(report.read_text(encoding="utf-8"))
    assert rep["n_extracted"] == 1
    assert rep["n_skipped"] == 1
    assert rep["skipped"][0]["path"] == str(blank.resolve())
