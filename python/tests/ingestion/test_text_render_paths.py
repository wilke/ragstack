"""#828 PR 4: ``text_render`` on every ingest path, read from the registry entry.

* The markers' parity test: one entry recording ``text_render="markdown-plain/1"``
  chunks and embeds identically through path A (the in-process pipeline the API
  builds), ``ingest_shard.py`` (path B, the extract shard), ``embed_shard.py``
  resolved through PR 3's ``--collection-id`` (path C) and ``ingest_jsonl.py``
  (path D) — the same chunk text, offsets, vectors and stamps — and what they
  store is the stripped render, not the raw Markdown.
* Absent marker: ``content`` is byte-identical to ``TextFileLoader``'s on both
  paths, and the marker leaves non-``.md`` documents alone.
* The GoWe extract shard is identical with and without the marker: the
  extract step has no marker input and writes the raw text; the render is
  applied at ingest.
* The receipt and the embedding-file header record the marker as applied, and
  the load step accepts that file for the marked entry and refuses an
  unresolved (unmarked) one.

Offline: fake embedders, in-memory stores, a scratch sqlite registry selected
by name, dead store URLs. Needs the ``[markdown]`` extra.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mdit_py_plugins", reason="the [markdown] extra is not installed")

from ragstack.ingestion import markdown_structure as md  # noqa: E402
from ragstack.ingestion.chunker_config import build_chunker, structure_from_params  # noqa: E402
from ragstack.ingestion.embedding_file import read_header  # noqa: E402
from ragstack.ingestion.loaders import (  # noqa: E402
    JsonlLoader,
    TextFileLoader,
    default_loader_registry,
)
from ragstack.ingestion.pipeline import IngestionPipeline  # noqa: E402
from ragstack.ingestion.receipts import ShardReceipt  # noqa: E402
from ragstack.ingestion.structure import STAMP_KEYS  # noqa: E402
from ragstack.ops import ingest_target as it  # noqa: E402
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore  # noqa: E402
from tests.ingestion.test_extract_text_parity import (  # noqa: E402,F401 — fixtures
    _MARKDOWN,
    no_pdf_loader,
    text_files,
)
from tests.unit.test_embed_shard_registry import (  # noqa: E402,F401 — the fixture
    CID,
    DEAD,
    REG,
    _spec,
    registry,
)
from tests.unit.test_ingest_jsonl_reingest import _args, _FakeStore  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import embed_shard  # noqa: E402
import ingest_jsonl  # noqa: E402
import ingest_shard  # noqa: E402
import pdf_extract  # noqa: E402

PLAIN = {"text_render": "markdown-plain/1"}
_SIZE = ["--chunk-method", "fixed", "--chunk-size", "200", "--chunk-overlap", "20",
         "--embedding-model", "", "--chunk-token-counter", "estimate",
         "--chunk-max-tokens", "4096", "--symbol-glossary", "off"]


class _Embedder:
    """Deterministic on the text, so "embeds identically" is observable."""

    async def embed(self, texts):  # noqa: ANN001
        return [[float(len(t)), float(sum(map(ord, t)) % 9973), 1.0, 2.0] for t in texts]


def _chunker():
    chunker, _, _ = build_chunker("fixed", chunk_size=200, chunk_overlap=20, model="m",
                                  token_backend="estimate", max_tokens=4096)
    return chunker


def _entry_spec(params):
    return _spec(chunk_method="fixed", chunk_size=200, chunk_overlap=20, chunk_params=params)


def _rows(chunks) -> dict:
    """``(text, start, end) -> (vector, stamps)``: what a chunk IS, so paths that
    mint document ids differently still compare."""
    out = {}
    for c in chunks:
        if isinstance(c, dict):
            text, s, e = c["content"], c["start_char"], c["end_char"]
            vec, meta = c["embedding"], c["metadata"]
        else:
            text, s, e, vec, meta = c.content, c.start_char, c.end_char, c.embedding, c.metadata
        out[(text, s, e)] = (list(vec) if vec is not None else None,
                             {k: meta[k] for k in STAMP_KEYS if k in meta})
    return out


def _emb_rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()[1:]
            if x.strip()]


def _extract(tmp_path: Path, files: list[Path], name: str = "shard.jsonl") -> Path:
    out = tmp_path / name
    assert pdf_extract.main([*map(str, files), "--out", str(out)]) == 0
    return out


async def _path_a(files: list[Path], markers) -> list:
    chunks = []
    for p in files:
        pipe = IngestionPipeline(loader=default_loader_registry(ingest_root=str(p.parent)),
                                 chunker=_chunker(), embedder=_Embedder(),
                                 vector_store=InMemoryVectorStore(),
                                 text_index=InMemoryTextIndex(), structure_metadata=True,
                                 structure_markers=markers)
        chunks.extend(await pipe.embed_source(str(p)))
    return chunks


# --------------------------------------------------------------------------- #
# the markers' parity test
# --------------------------------------------------------------------------- #

def test_a_marked_entry_chunks_and_embeds_identically_on_every_path(
    tmp_path, text_files, no_pdf_loader, registry, monkeypatch  # noqa: F811
) -> None:
    monkeypatch.delenv("COLLECTION_MANIFEST_DIR", raising=False)
    registry(_entry_spec(dict(PLAIN)))
    target = it.resolve(CID, registry=REG)
    markers = structure_from_params("fixed", target.spec.chunk_params)
    shard = _extract(tmp_path, text_files)

    # A: the in-process pipeline, with the markers the API reads from the entry.
    a = _rows(asyncio.run(_path_a(text_files, markers)))

    # B: ingest_shard.py's amain, with the resolved target main() would pass.
    emb_b, receipt_b = tmp_path / "b.emb.jsonl", tmp_path / "b.receipt.json"
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _Embedder())
    args_b = ingest_shard.parse_args([
        str(shard), "--vector-backend", "memory", "--text-backend", "memory",
        "--qdrant-url", DEAD, "--es-url", DEAD, *_SIZE, "--structure-metadata", "on",
        "--embedding-file", str(emb_b), "--out", str(receipt_b)])
    assert asyncio.run(ingest_shard.amain(args_b, target)) == 0
    b = _rows(_emb_rows(emb_b))

    # C: embed_shard.py, resolving the entry itself (PR 3).
    emb_c, receipt_c = tmp_path / "c.emb.jsonl", tmp_path / "c.receipt.json"
    monkeypatch.setattr(embed_shard, "make_embedder_auto", lambda **kw: _Embedder())
    assert embed_shard.main([
        str(shard), "--out", str(emb_c), "--receipt", str(receipt_c),
        "--collection-id", CID, "--registry", REG, *_SIZE, "--structure-metadata", "on",
    ]) == 0
    c = _rows(_emb_rows(emb_c))

    # D: ingest_jsonl.py's own loop, with the resolved target.
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _Embedder())
    asyncio.run(ingest_jsonl.run(_args(shard, checkpoint=tmp_path / "d.ckpt",
                                       structure_metadata="on"), target))
    d = _rows(store.points.values())

    assert a and a == b == c == d
    # What was stored is the stripped render of the .md files, and the .txt raw.
    [guide] = TextFileLoader().load(str(text_files[0]))
    rendered, _s = md.markdown_plain(guide)
    texts = [k[0] for k in a]
    assert not any("# Installing" in t or "](" in t for t in texts)
    assert any("user guide (https://example.org/docs/guide.html#install)" in t for t in texts)
    starts = sorted((s, e, t) for t, s, e in a if rendered[s:e] == t)
    assert starts and starts[0][0] == 0
    assert any(t.startswith("Plain notes\n") for t in texts)  # .txt: untouched
    stamped = [v[1] for v in a.values() if v[1]]
    assert stamped and all(x["structure_version"] == "markdown/1;stamp/1" for x in stamped)
    assert any(x.get("section_title") == "Usage" for x in stamped)

    # Records: the receipt's and the header's chunking objects show the marker.
    rec_b = ShardReceipt.load(receipt_b)
    assert rec_b.chunking == PLAIN
    assert rec_b.enrichment["structure"]["backend"].startswith("markdown-it-py/")  # type: ignore[index]
    header = read_header(emb_c)
    assert header["chunking"]["text_render"] == "markdown-plain/1"
    assert header["chunking"]["collection_id"] == CID
    assert ShardReceipt.load(receipt_c).chunking == PLAIN
    it.check_header_chunking(header, target)  # the load step accepts it


def test_an_unresolved_embed_file_is_refused_by_the_marked_entry(
    tmp_path, text_files, no_pdf_loader, registry, monkeypatch  # noqa: F811
) -> None:
    """The silent case the backstops exist for: an embed step that did not
    resolve the entry (an unwired workflow text) embeds the RAW text."""
    registry(_entry_spec(dict(PLAIN)))
    shard = _extract(tmp_path, text_files)
    out = tmp_path / "u.emb.jsonl"
    monkeypatch.setattr(embed_shard, "make_embedder_auto", lambda **kw: _Embedder())
    assert embed_shard.main([
        str(shard), "--out", str(out), "--receipt", str(tmp_path / "u.json"), *_SIZE]) == 0
    assert any("# Installing" in r["content"] for r in _emb_rows(out))
    with pytest.raises(it.TargetError, match="document-structure markers"):
        it.check_header_chunking(read_header(out), it.resolve(CID, registry=REG))


# --------------------------------------------------------------------------- #
# absent marker: today's behaviour, byte for byte
# --------------------------------------------------------------------------- #

async def test_absent_marker_content_is_byte_identical_to_the_loader_on_both_paths(
    tmp_path, text_files, no_pdf_loader  # noqa: F811
) -> None:
    """MUTATION: render whatever the marker says (or default it on) → the
    first chunk no longer starts with the file's BOM and front matter → fails."""
    shard = _extract(tmp_path, text_files)
    for path in text_files:
        [loaded] = TextFileLoader().load(str(path))
        raw = loaded.content
        # A: in-process.
        chunks_a = await _path_a([path], None)
        # B: the shard document through JsonlLoader.
        pipe = IngestionPipeline(loader=JsonlLoader(), chunker=_chunker(), embedder=_Embedder(),
                                 vector_store=InMemoryVectorStore(),
                                 text_index=InMemoryTextIndex(), structure_metadata=True)
        docs = [d for d in JsonlLoader().load(str(shard)) if d.source == str(path)]
        assert [d.content.encode() for d in docs] == [raw.encode()]
        chunks_b, _ = await pipe.prepare_documents(docs)
        for chunks in (chunks_a, chunks_b):
            assert chunks and min(c.start_char for c in chunks) == 0
            assert all(raw[c.start_char:c.end_char] == c.content for c in chunks)
        assert _rows(chunks_a).keys() == _rows(chunks_b).keys()
        if path.name == "guide.md":
            assert chunks_a[0].content.startswith("﻿---\ntitle:")


def test_the_marker_is_applied_only_where_its_suffix_matches(
    tmp_path, text_files, no_pdf_loader  # noqa: F811
) -> None:
    """``.md`` (any case) is rendered; ``.txt`` is not."""
    from ragstack.ingestion.structure import apply_text_render

    docs = [TextFileLoader().load(str(p))[0] for p in text_files]
    raw = [d.content for d in docs]
    assert apply_text_render(docs, "markdown-plain/1") == 2
    assert docs[0].content != raw[0] and docs[1].content != raw[1]
    assert docs[1].content == "Upper-case suffix\n\nx (http://e.org/y)"
    assert docs[2].content == raw[2] and docs[2].structure is None


# --------------------------------------------------------------------------- #
# the extract step never sees the marker
# --------------------------------------------------------------------------- #

def test_the_gowe_shard_is_identical_with_and_without_the_marker(
    tmp_path, text_files, no_pdf_loader, capsys  # noqa: F811
) -> None:
    """The extract step has no marker input (#829: never a CWL input for an
    identity value) and reads no registry, so the shard it writes for a marked
    collection IS the shard it writes for an unmarked one: raw text, no
    structure key for Markdown. The render happens at ingest."""
    with pytest.raises(SystemExit):
        pdf_extract.parse_args(["--help"])
    help_text = capsys.readouterr().out
    assert "text-render" not in help_text and "text_render" not in help_text
    options = vars(pdf_extract.parse_args([str(text_files[0])]))
    assert not {"text_render", "collection_id", "registry", "chunk_params"} & set(options)
    one = _extract(tmp_path, text_files, "one.jsonl").read_bytes()
    two = _extract(tmp_path, text_files, "two.jsonl").read_bytes()
    assert one == two
    records = [json.loads(x) for x in one.decode("utf-8").splitlines()]
    assert not any("structure" in r for r in records)
    [guide] = [r for r in records if r["path"].endswith("guide.md")]
    assert guide["text"].startswith("﻿---")
    assert _MARKDOWN.replace("\r\n", "\n").replace("\r", "\n")[1:60] in guide["text"]
