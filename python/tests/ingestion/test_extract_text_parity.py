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
    EMPTY_TEXT_ERROR,
    JSONL_UNSUPPORTED_ERROR,
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
    # #828 PR 2: what the structure adapter must parse, not pattern-match —
    # setext headings, a `#` line inside a fence, a thematic break that is not
    # a setext underline, image-only paragraphs with captions, `$$` block maths
    # and an ATX heading with a closing sequence. The `$$` block sits AFTER the
    # `$$E = mc^2$$ and $x_i$` line on purpose: with dollarmath's default
    # allow_blank_lines=True that line opened a math block running to the next
    # line ending in `$$`, swallowing every heading in between (review of #843).
    "\n"
    "Setext title\n"
    "============\n"
    "\n"
    "Lead text of the setext section.\n"
    "\n"
    "```python\n"
    "# not a heading: a comment inside a fence\n"
    "```\n"
    "\n"
    "Second *setext*\n"
    "---------------\n"
    "\n"
    "![Figure 1. A synthetic pipeline diagram](images/fig1.png)\n"
    "\n"
    "![plain alt](images/fig2.png)\n"
    "\n"
    "Figure 2: the second synthetic figure.\n"
    "\n"
    "$$\n"
    "a^2 + b^2 = c^2\n"
    "$$\n"
    "\n"
    "---\n"
    "\n"
    "### Results and discussion ###\n"
    "\n"
    "Closing text.\n"
    "\n"
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
    upper = (tmp_path / "X.PDF")  # suffix matching is case-insensitive
    upper.write_bytes(b"%PDF-1.4 stub")
    md = _write(tmp_path / "b.md", "# b\n")
    records, skipped = pdf_extract.extract_documents([pdf.resolve(), upper.resolve(), md])
    assert skipped == []
    assert seen == ["a.pdf", "X.PDF"]
    assert [r["metadata"] for r in records] == [{"filename": "a.pdf", "pages": 1},
                                                {"filename": "a.pdf", "pages": 1},
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
        (str(empty), EMPTY_TEXT_ERROR),
    ]
    assert no_pdf_loader == []


def test_undecodable_text_file_is_skipped_not_crashed(tmp_path, no_pdf_loader):
    bad = tmp_path / "latin1.txt"
    bad.write_bytes(b"caf\xe9\n")
    records, skipped = pdf_extract.extract_documents([bad.resolve()])
    assert records == []
    # error: the class name, as the in-process path labels it (countable);
    # reason: the detail.
    assert skipped[0]["error"] == "UnicodeDecodeError"
    assert skipped[0]["reason"].startswith("UnicodeDecodeError: ")


def test_jsonl_is_refused_not_half_ingested(tmp_path, no_pdf_loader):
    """A ``.jsonl`` batch file is many documents behind one receipt item, and
    path-less records would all get the shard file's id: refused (#830)."""
    shard = tmp_path / "batch.jsonl"
    shard.write_text(
        json.dumps({"text": "with a path", "path": "/x/a.pdf", "metadata": {}}) + "\n"
        + json.dumps({"text": "no path one", "metadata": {}}) + "\n"
        + json.dumps({"text": "no path two", "metadata": {}}) + "\n",
        encoding="utf-8",
    )
    upper = tmp_path / "B.JSONL"
    upper.write_text(json.dumps({"text": "t", "path": "/x/b.pdf"}) + "\n", encoding="utf-8")
    records, skipped = pdf_extract.extract_documents([shard.resolve(), upper.resolve()])
    assert records == []
    assert [(s["path"], s["error"]) for s in skipped] == [
        (str(shard.resolve()), JSONL_UNSUPPORTED_ERROR),
        (str(upper.resolve()), JSONL_UNSUPPORTED_ERROR),
    ]


def test_symlink_dispatches_on_its_target(tmp_path, no_pdf_loader):
    """``loader_for`` reads the suffix as given, ``registry.load`` the resolved
    one; the extract step resolves first, so a ``.md`` link to an ``.xml``
    target is refused like the ``.xml`` — not read as raw XML."""
    target = _write(tmp_path / "paper.xml", "<article/>")
    link = tmp_path / "paper.md"
    link.symlink_to(target)
    records, skipped = pdf_extract.extract_documents([link])  # unresolved on purpose
    assert records == []
    assert [(s["path"], s["error"]) for s in skipped] == [(str(target), "no loader for .xml")]


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


# --------------------------------------------------------------------------- #
# #828 PR 2: the structure half — one file, one structure, on every path
# --------------------------------------------------------------------------- #
#
# The Markdown adapter runs at INGEST on every path (§2's placement table),
# over exactly the `content` each path holds — which the tests above prove is
# the same text. So the structure the GoWe ingest step builds from the shard
# must equal the in-process one field for field, and the stamped chunk
# metadata must be identical on all four chunk loops.

import asyncio  # noqa: E402
import importlib.util  # noqa: E402
from collections import Counter  # noqa: E402

from ragstack.ingestion import pipeline as pipeline_mod  # noqa: E402
from ragstack.ingestion.chunker_config import build_chunker  # noqa: E402
from ragstack.ingestion.pipeline import IngestionPipeline  # noqa: E402
from ragstack.ingestion.receipts import ShardReceipt  # noqa: E402
from ragstack.ingestion.structure import STAMP_KEYS, attach_structure  # noqa: E402
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore  # noqa: E402

requires_markdown = pytest.mark.skipif(
    importlib.util.find_spec("mdit_py_plugins") is None,
    reason="the [markdown] extra is not installed",
)


class _Embedder:
    async def embed(self, texts):  # noqa: ANN001
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _attached(docs):
    counts: Counter[str] = Counter()
    attach_structure(docs, counts=counts)
    return {d.source: (d.structure.model_dump() if d.structure else None) for d in docs}, counts


def _extract_shard(tmp_path: Path, text_files: list[Path]) -> Path:
    out = tmp_path / "shard.jsonl"
    assert pdf_extract.main([*map(str, text_files), "--out", str(out)]) == 0
    return out


@requires_markdown
def test_structure_built_from_the_shard_equals_the_in_process_structure(
    tmp_path, text_files, no_pdf_loader
) -> None:
    in_process, a_counts = _attached([_in_process(p) for p in text_files])
    shard = _extract_shard(tmp_path, text_files)
    # The extract step writes no structure for Markdown: it is built at ingest.
    assert all("structure" not in json.loads(line)
               for line in shard.read_text(encoding="utf-8").splitlines())
    gowe, b_counts = _attached(JsonlLoader().load(str(shard)))
    assert gowe == in_process  # field for field, every section and block
    assert [in_process[str(p)] is not None for p in text_files] == [True, True, False]
    assert a_counts == b_counts


def _chunker():
    chunker, _, _ = build_chunker("fixed", chunk_size=200, chunk_overlap=20, model="m",
                                  token_backend="estimate", max_tokens=4096)
    return chunker


def _stamps(chunks) -> dict:
    """Stamped fields keyed by what a chunk IS (its text and offsets), so the
    four loops compare even where they mint doc ids differently."""
    out = {}
    for c in chunks:
        meta = c["metadata"] if isinstance(c, dict) else c.metadata
        key = (c["content"], c["start_char"], c["end_char"]) if isinstance(c, dict) \
            else (c.content, c.start_char, c.end_char)
        out[key] = {k: meta[k] for k in STAMP_KEYS if k in meta}
    return out


@requires_markdown
async def test_stamped_metadata_is_identical_on_every_ingest_path(
    tmp_path, text_files, no_pdf_loader, monkeypatch
) -> None:
    """A (in-process prepare_source on the file), B (ingest_shard.py on the
    extract shard), C (embed_shard.py → iter_embed_source) and D
    (ingest_jsonl.py's own loop) stamp the same chunks the same way, and B
    builds the in-process structure field for field."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import embed_shard
    import ingest_shard

    from tests.ingestion.test_structure_paths import _via_ingest_jsonl

    # A: in-process, from the files.
    a_chunks = []
    for p in text_files:
        pipe = IngestionPipeline(loader=default_loader_registry(ingest_root=str(p.parent)),
                                 chunker=_chunker(), embedder=_Embedder(),
                                 vector_store=InMemoryVectorStore(),
                                 text_index=InMemoryTextIndex(), structure_metadata=True)
        a_chunks.extend((await pipe.prepare_source(str(p))).chunks)
    a = _stamps(a_chunks)
    in_process, _ = _attached([_in_process(p) for p in text_files])

    shard = _extract_shard(tmp_path, text_files)

    # B: ingest_shard.py, spying on the structures its pipeline attaches.
    built: dict = {}
    real_attach = pipeline_mod.attach_structure

    def spy(documents, adapters=None, counts=None):
        real_attach(documents, adapters, counts)
        built.update({d.source: (d.structure.model_dump() if d.structure else None)
                      for d in documents})

    monkeypatch.setattr(pipeline_mod, "attach_structure", spy)
    emb_b, receipt = tmp_path / "b.emb.jsonl", tmp_path / "b.receipt.json"
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _Embedder())
    # main() runs its own event loop (asyncio.run): off this test's loop.
    assert await asyncio.to_thread(ingest_shard.main, [
        str(shard), "--vector-backend", "memory", "--text-backend", "memory",
        "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1",
        "--chunk-method", "fixed", "--chunk-size", "200", "--chunk-overlap", "20",
        "--embedding-model", "", "--chunk-token-counter", "estimate",
        "--chunk-max-tokens", "4096", "--symbol-glossary", "off",
        "--structure-metadata", "on", "--embedding-file", str(emb_b), "--out", str(receipt),
    ]) == 0
    assert built == in_process
    rows_b = [json.loads(x) for x in emb_b.read_text(encoding="utf-8").splitlines()[1:]]
    rec = ShardReceipt.load(receipt).enrichment["structure"]  # type: ignore[index]
    assert rec["adapters"] == {"markdown/1": 2} and rec["structure_missing"] == 1
    assert rec["backend"].startswith("markdown-it-py/")

    # C: embed_shard.py.
    emb_c = tmp_path / "c.emb.jsonl"
    monkeypatch.setattr(embed_shard, "make_embedder_auto", lambda **kw: _Embedder())
    assert await asyncio.to_thread(embed_shard.main, [
        str(shard), "--out", str(emb_c), "--receipt", str(tmp_path / "c.receipt.json"),
        "--chunk-method", "fixed", "--chunk-size", "200", "--chunk-overlap", "20",
        "--embedding-model", "", "--chunk-token-counter", "estimate",
        "--chunk-max-tokens", "4096", "--symbol-glossary", "off",
        "--structure-metadata", "on",
    ]) == 0
    rows_c = [json.loads(x) for x in emb_c.read_text(encoding="utf-8").splitlines()
              if x.strip() and "metadata" in json.loads(x)]

    # D: ingest_jsonl.py.
    d_chunks, _row = await _via_ingest_jsonl(shard, tmp_path, monkeypatch, tag="md")

    b, c, d = _stamps(rows_b), _stamps(rows_c), _stamps(d_chunks)
    assert a and a == b == c == d
    stamped = [v for v in a.values() if v]
    assert stamped and all(v["structure_version"] == "markdown/1;stamp/1" for v in stamped)
    assert any(v.get("section_title") == "Usage" and "usage" in v["section_roles"]
               for v in stamped)
    # The .txt document gets no structure key on any path.
    assert any(not v for v in a.values())
