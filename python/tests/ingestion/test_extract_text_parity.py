"""#828 invariant: one file, one text, on both ingest paths.

The GoWe extract step (``scripts/pdf_extract.py``) must produce, for a ``.md`` /
``.txt`` file, exactly the ``Document.content`` the in-process path stores —
byte for byte — because it dispatches through the same suffix -> loader map
(``default_loader_registry``). Before #828 it sent every staged file to
``PdfLoader``; PyMuPDF 2.x opens Markdown and returns a rendering that drops
link targets, inline HTML and code.

These tests do not depend on PyMuPDF's behaviour (the dev env has 1.27, which
refuses ``.md``; the tools image has 2.x, which renders it): ``PdfLoader.load``
is patched to fail if it is ever called for a text suffix, AND the texts are
compared byte for byte, including after the JSONL round trip ``ingest_shard.py``
does through ``JsonlLoader``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ragstack.ingestion import loaders
from ragstack.ingestion.loaders import (
    DEFAULT_INGEST_SUFFIXES,
    JsonlLoader,
    PdfLoader,
    TextFileLoader,
    default_loader_registry,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import pdf_extract  # noqa: E402

# Everything PyMuPDF's Markdown rendering was measured to lose or change, plus
# the bytes a text read can normalise (CRLF, lone CR, BOM, tabs, NBSP).
_MARKDOWN = (
    "﻿---\r\n"
    "title: \"Install guide\"\r\n"
    "tags: [cli, setup]\r\n"
    "---\r\n"
    "\r\n"
    "# Installing   the *tool*\r\n"
    "\r\n"
    "See the [user guide](https://example.org/docs/guide.html#install \"Guide\") and\r\n"
    "<https://example.org/raw> or ![diagram](images/arch.png).\r\n"
    "A reference link [here][ref] and an <a href=\"https://example.org/a?b=1&c=2\">"
    "anchor</a> with <span class=\"note\">inline HTML</span><br/>.\r\n"
    "\r\n"
    "[ref]: https://example.org/ref\r\n"
    "\r\n"
    "## Usage\n"  # mixed line endings on purpose
    "\n"
    "Run `ragstack-ctl --tenant $TENANT` with **bold**, _emph_, ~~strike~~ and\n"
    "snake_case_identifier and ${HOME}/bin and \\*escaped\\*.\t\ttrailing  \n"
    "Unicode: → ≤ µg ✓ α-helix \U0001f9ec  nbsp‐dash.\r"
    "lone CR above.\n"
    "\n"
    "```bash\n"
    "export RAG_DATA=$HOME/rag   # comment\n"
    "for f in *.md; do echo \"$f\"; done\n"
    "```\n"
    "\n"
    "    indented code block\n"
    "\n"
    "| flag | default | meaning |\n"
    "|------|:-------:|--------:|\n"
    "| `--size` | 512 | tokens per *chunk* |\n"
    "| `--url` | <http://127.0.0.1:1> | a \\| pipe |\n"
    "\n"
    "> quoted **text**\n"
    "\n"
    "- item one\n"
    "  1. nested <kbd>Ctrl</kbd>\n"
    "\n"
    "$$E = mc^2$$ and $x_i$\n"
    "<!-- an html comment -->\n"
    "<details><summary>More</summary>hidden body</details>\n"
)

_TEXT = (
    "Plain notes\r\n\r\n"
    "Path: /rag/data/x and $PATH; url https://example.org/?q=a+b\r\n"
    "\ttab nbsp  éè 中文\n"
    "no trailing newline"
)


@pytest.fixture
def no_pdf_loader(monkeypatch):
    """Fail loudly if anything routes a file through PdfLoader. Patched on the
    class so it holds however the instance was made (a revert to
    ``PdfLoader().load(...)`` in the script is caught too)."""
    calls: list[str] = []

    def _refuse(self, source):  # noqa: ANN001
        calls.append(source)
        raise AssertionError(f"PdfLoader.load called for {source}")

    monkeypatch.setattr(PdfLoader, "load", _refuse)
    return calls


def _write(path: Path, text: str) -> Path:
    # newline="" so CRLF / lone CR reach the disk exactly as written.
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path.resolve()


@pytest.fixture
def text_files(tmp_path: Path) -> list[Path]:
    return [
        _write(tmp_path / "guide.md", _MARKDOWN),
        _write(tmp_path / "README.MD", "# Upper-case suffix\n\n[x](http://e.org/y)\n"),
        _write(tmp_path / "notes.txt", _TEXT),
    ]


def _in_process(path: Path):
    """What the in-process API path stores: the default registry, as deps.py
    builds it (root/size guards do not change the text)."""
    [doc] = default_loader_registry(ingest_root=str(path.parent)).load(str(path))
    return doc


def test_extract_text_equals_in_process_text_byte_for_byte(text_files, no_pdf_loader):
    records, skipped = pdf_extract.extract_documents(text_files)

    assert skipped == []
    assert no_pdf_loader == []
    assert [r["path"] for r in records] == [str(p) for p in text_files]
    for path, rec in zip(text_files, records, strict=True):
        doc = _in_process(path)
        assert rec["text"].encode("utf-8") == doc.content.encode("utf-8"), path.name
        # The same metadata the in-process loader records for a text file
        # (TextFileLoader: filename only — no `pages`, which is PDF-only).
        assert rec["metadata"] == doc.metadata == {"filename": path.name}
        assert set(rec) == {"text", "path", "metadata"}


def test_markdown_content_words_survive(text_files, no_pdf_loader):
    """The words PyMuPDF's rendering dropped are in the extracted text."""
    [md, *_] = pdf_extract.extract_documents(text_files)[0]
    for needle in (
        "https://example.org/docs/guide.html#install",  # link target
        "images/arch.png",                              # image target
        "https://example.org/ref",                      # reference definition
        '<a href="https://example.org/a?b=1&c=2">',     # inline HTML
        "<!-- an html comment -->",
        "export RAG_DATA=$HOME/rag   # comment",        # fenced code, verbatim
        "`ragstack-ctl --tenant $TENANT`",              # inline code + shell var
        "${HOME}/bin",
        "| `--size` | 512 | tokens per *chunk* |",     # table row
        "# Installing   the *tool*",                    # heading + emphasis syntax
        'title: "Install guide"',                       # front matter
        "→ ≤ µg ✓",
        "$$E = mc^2$$",
    ):
        assert needle in md["text"], needle


def test_shard_round_trip_gives_in_process_document(tmp_path, text_files, no_pdf_loader):
    """End to end through the CLI and the JSONL the ingest step reads: the
    Document ``ingest_shard.py`` builds (via JsonlLoader) has the in-process
    content byte for byte, and the same document id."""
    out = tmp_path / "shard.jsonl"
    report = tmp_path / "shard.report.json"
    assert pdf_extract.main([*map(str, text_files), "--out", str(out),
                             "--report", str(report)]) == 0

    shard_docs = JsonlLoader().load(str(out))
    assert len(shard_docs) == len(text_files)
    # main() sorts its inputs (deterministic shards), so pair by path.
    for path, sdoc in zip(sorted(text_files), shard_docs, strict=True):
        assert sdoc.source == str(path)
        doc = _in_process(path)
        assert sdoc.content.encode("utf-8") == doc.content.encode("utf-8"), path.name
        assert sdoc.id == doc.id
    rep = json.loads(report.read_text(encoding="utf-8"))
    assert (rep["n_extracted"], rep["n_skipped"]) == (len(text_files), 0)


def test_pdf_still_goes_to_pdf_loader(tmp_path, monkeypatch):
    """Only real PDFs reach PdfLoader — and they still do."""
    seen: list[str] = []

    def _fake(self, source):  # noqa: ANN001
        seen.append(Path(source).name)
        return [loaders.Document(id="x", content="pdf text", metadata={"filename": "a.pdf",
                                                                        "pages": 1},
                                 source=source)]

    monkeypatch.setattr(PdfLoader, "load", _fake)
    pdf = (tmp_path / "a.pdf")
    pdf.write_bytes(b"%PDF-1.4 stub")
    md = _write(tmp_path / "b.md", "# b\n")
    records, skipped = pdf_extract.extract_documents([pdf.resolve(), md])
    assert skipped == []
    assert seen == ["a.pdf"]
    assert [r["metadata"] for r in records] == [{"filename": "a.pdf", "pages": 1},
                                                {"filename": "b.md"}]


def test_unsupported_suffix_and_empty_text_are_skipped(tmp_path, no_pdf_loader):
    xml = _write(tmp_path / "paper.xml", "<article><title>T</title></article>")
    noext = _write(tmp_path / "LICENSE", "text with no suffix")
    empty = _write(tmp_path / "empty.md", " \n\t\n")
    good = _write(tmp_path / "ok.md", "ok\n")
    records, skipped = pdf_extract.extract_documents([xml, noext, empty, good])

    assert [r["path"] for r in records] == [str(good)]
    assert [(s["path"], s["error"]) for s in skipped] == [
        (str(xml), "no loader for .xml"),          # the in-process upload's string
        (str(noext), "no loader for (no suffix)"),
        (str(empty), pdf_extract.EMPTY_TEXT_ERROR),
    ]
    assert no_pdf_loader == []


def test_undecodable_text_file_is_skipped_not_crashed(tmp_path, no_pdf_loader):
    bad = tmp_path / "latin1.txt"
    bad.write_bytes(b"caf\xe9\n")
    records, skipped = pdf_extract.extract_documents([bad.resolve()])
    assert records == []
    assert skipped[0]["error"].startswith("UnicodeDecodeError")


def test_registry_loader_for_is_the_dispatch_map():
    reg = default_loader_registry()
    for sfx in DEFAULT_INGEST_SUFFIXES:
        assert reg.loader_for(f"x{sfx}") is not None, sfx
        assert reg.loader_for(f"x{sfx.upper()}") is reg.loader_for(f"x{sfx}")
    assert isinstance(reg.loader_for("a.md"), TextFileLoader)
    assert isinstance(reg.loader_for("a.txt"), TextFileLoader)
    assert isinstance(reg.loader_for("a.pdf"), PdfLoader)
    assert isinstance(reg.loader_for("a.jsonl"), JsonlLoader)
    assert reg.loader_for("a.xml") is None
    assert reg.loader_for("LICENSE") is None
