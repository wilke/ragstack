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
from ragstack.models import Document  # noqa: E402
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
            vec, meta, cid, did = c["embedding"], c["metadata"], c["id"], c["doc_id"]
        else:
            text, s, e, vec, meta = c.content, c.start_char, c.end_char, c.embedding, c.metadata
            cid, did = c.id, c.doc_id
        out[(text, s, e)] = (list(vec) if vec is not None else None,
                             {k: meta[k] for k in STAMP_KEYS if k in meta}, did, cid)
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

    assert a and a == b == c == d  # text, offsets, vectors, stamps, doc ids, chunk ids
    assert len({v[2] for v in a.values()}) == len(text_files)
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

def test_the_extract_step_takes_no_marker_and_writes_the_raw_text(
    tmp_path, text_files, no_pdf_loader, capsys  # noqa: F811
) -> None:
    """The extract step has no marker input (#829: never a CWL input for an
    identity value) and reads no registry — so the shard it writes cannot
    depend on a collection's marker: raw text, no structure key for Markdown,
    deterministic. The render happens at ingest. (What this proves is the
    absence of any input through which a marker could reach the step, plus
    determinism; there is no "with the marker" run to compare, by design.)"""
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


# --------------------------------------------------------------------------- #
# an UNMARKED entry: the tools' receipt and header are what they were
# --------------------------------------------------------------------------- #

def test_an_unmarked_entry_leaves_the_receipt_and_the_header_unchanged(
    tmp_path, text_files, no_pdf_loader, registry, monkeypatch  # noqa: F811
) -> None:
    """Argo S4: through ``ingest_shard.amain`` and ``embed_shard`` (which runs
    ``run_embed_shard``), an unmarked, non-semantic entry gives a receipt with
    NO ``chunking`` key, and a header ``chunking`` object with exactly PR 3's
    keys. MUTATIONS: ``applied() or None`` → ``applied()``; ``applied_chunking``
    returning ``{}``; markers read regardless of the entry → fail."""
    monkeypatch.delenv("COLLECTION_MANIFEST_DIR", raising=False)
    registry(_entry_spec({}))
    target = it.resolve(CID, registry=REG)
    shard = _extract(tmp_path, text_files)
    receipt_b = tmp_path / "b.receipt.json"
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _Embedder())
    args_b = ingest_shard.parse_args([
        str(shard), "--vector-backend", "memory", "--text-backend", "memory",
        "--qdrant-url", DEAD, "--es-url", DEAD, *_SIZE, "--out", str(receipt_b)])
    assert asyncio.run(ingest_shard.amain(args_b, target)) == 0
    assert "chunking" not in json.loads(receipt_b.read_text(encoding="utf-8"))
    emb_c, receipt_c = tmp_path / "c.emb.jsonl", tmp_path / "c.receipt.json"
    monkeypatch.setattr(embed_shard, "make_embedder_auto", lambda **kw: _Embedder())
    assert embed_shard.main([str(shard), "--out", str(emb_c), "--receipt", str(receipt_c),
                             "--collection-id", CID, "--registry", REG, *_SIZE]) == 0
    assert "chunking" not in json.loads(receipt_c.read_text(encoding="utf-8"))
    assert set(read_header(emb_c)["chunking"]) == {"collection_id", "method", "chunk_size",
                                                   "chunk_overlap"}
    assert any("# Installing" in r["content"] for r in _emb_rows(emb_c))  # raw, unrendered


# --------------------------------------------------------------------------- #
# B1: a glossary file is refused with text_render (path B)
# --------------------------------------------------------------------------- #

def test_ingest_shard_refuses_a_glossary_file_for_a_text_render_entry(
    tmp_path, text_files, no_pdf_loader, registry, monkeypatch, capsys  # noqa: F811
) -> None:
    """Reproduced first: the shard ingested with exit 0 and
    ``degraded_batches: 1`` (no glossary), the only trace a log line. Now it
    exits 2 before anything is embedded. MUTATION: drop the refusal → the
    run reaches amain → fails."""
    import glossary_step

    registry(_entry_spec(dict(PLAIN)))
    shard = _extract(tmp_path, text_files)
    gl = tmp_path / "glossary.jsonl"
    assert glossary_step.main([str(shard), "--out", str(gl)]) == 0
    monkeypatch.setattr(ingest_shard, "amain", lambda *a, **k: pytest.fail("reached amain"))
    argv = [str(shard), "--collection-id", CID, "--registry", REG, "--qdrant-url", DEAD,
            "--es-url", DEAD, *_SIZE, "--out", str(tmp_path / "r.json")]
    argv = [a for a in argv if a not in ("--symbol-glossary", "off")]
    with pytest.raises(SystemExit) as ex:
        ingest_shard.main([*argv, "--glossary-file", str(gl)])
    assert ex.value.code == 2 and "glossary file cannot be used" in capsys.readouterr().err
    # Without the file the same entry proceeds (to the amain spy).
    with pytest.raises(pytest.fail.Exception):
        ingest_shard.main(argv)


# --------------------------------------------------------------------------- #
# a render that fails on one document fails that document only
# --------------------------------------------------------------------------- #

def _two_pages(tmp_path: Path) -> Path:
    shard = tmp_path / "two.jsonl"
    shard.write_text(
        json.dumps({"text": "# Good\n\nA page that renders.\n", "path": "/d/good.md",
                    "metadata": {}}) + "\n"
        + json.dumps({"text": "# Bad\n\nBOOM in this page.\n", "path": "/d/bad.md",
                      "metadata": {}}) + "\n", encoding="utf-8")
    return shard


@pytest.fixture
def boom(monkeypatch):
    real = md.markdown_plain

    def flaky(doc):
        if "BOOM" in doc.content:
            raise ValueError("synthetic render failure")
        return real(doc)

    monkeypatch.setattr(md, "markdown_plain", flaky)


def test_a_failed_render_fails_its_row_and_the_batch_goes_on(
    tmp_path, registry, monkeypatch, boom  # noqa: F811
) -> None:
    """Argo S2. Path B: the good page is rendered and indexed; the bad one is
    a failed receipt row naming the render failure; never ingested raw.
    MUTATION: let the exception propagate (the old behaviour) → the whole
    batch fails → fails."""
    monkeypatch.delenv("COLLECTION_MANIFEST_DIR", raising=False)
    registry(_entry_spec(dict(PLAIN)))
    target = it.resolve(CID, registry=REG)
    emb, receipt = tmp_path / "e.jsonl", tmp_path / "r.json"
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _Embedder())
    args = ingest_shard.parse_args([
        str(_two_pages(tmp_path)), "--vector-backend", "memory", "--text-backend", "memory",
        "--qdrant-url", DEAD, "--es-url", DEAD, *_SIZE, "--embedding-file", str(emb),
        "--out", str(receipt)])
    assert asyncio.run(ingest_shard.amain(args, target)) == 0
    rec = ShardReceipt.load(receipt)
    rows = {Path(r.source).name: r for r in rec.docs}
    assert rows["good.md"].error in ("", None) and rows["good.md"].chunk_ids
    assert rows["bad.md"].error.startswith("text_render_failed: ValueError")
    assert not rows["bad.md"].chunk_ids
    texts = [r["content"] for r in _emb_rows(emb)]
    assert texts == ["Good\n\nA page that renders."]


def test_a_failed_render_on_paths_a_c_and_d(tmp_path, registry, monkeypatch, boom) -> None:  # noqa: F811
    from ragstack.ingestion.structure import TextRenderFailed

    markers = structure_from_params("fixed", PLAIN)
    shard = _two_pages(tmp_path)
    # C: iter_embed_source — the good page only, the failure counted.
    pipe = IngestionPipeline(loader=JsonlLoader(), chunker=_chunker(), embedder=_Embedder(),
                             vector_store=InMemoryVectorStore(),
                             text_index=InMemoryTextIndex(), structure_markers=markers)

    async def _iter():
        return [c async for g in pipe.iter_embed_source(str(shard)) for c in g]

    assert [c.content for c in asyncio.run(_iter())] == ["Good\n\nA page that renders."]
    assert pipe.text_render_failures() == 1
    # A: one source whose only document fails → the item fails with the reason.
    bad = tmp_path / "bad.md"
    bad.write_text("# Bad\n\nBOOM\n", encoding="utf-8")
    pipe_a = IngestionPipeline(loader=default_loader_registry(ingest_root=str(tmp_path)),
                               chunker=_chunker(), embedder=_Embedder(),
                               vector_store=InMemoryVectorStore(),
                               text_index=InMemoryTextIndex(), structure_markers=markers)
    with pytest.raises(TextRenderFailed) as e:
        asyncio.run(pipe_a.prepare_source(str(bad)))
    assert e.value.job_error == "text_render_failed: ValueError"
    # D: ingest_jsonl — the bad page is left out, the good one stored rendered.
    registry(_entry_spec(dict(PLAIN)))
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _Embedder())
    asyncio.run(ingest_jsonl.run(_args(shard, checkpoint=tmp_path / "d.ckpt"),
                                 it.resolve(CID, registry=REG)))
    assert [c.content for c in store.points.values()] == ["Good\n\nA page that renders."]


# --------------------------------------------------------------------------- #
# when EVERY document fails to render, no path reports success
# --------------------------------------------------------------------------- #

def _bad_only(tmp_path: Path) -> Path:
    shard = tmp_path / "bad-only.jsonl"
    shard.write_text(json.dumps({"text": "# Bad\n\nBOOM in this page.\n", "path": "/d/bad.md",
                                 "metadata": {}}) + "\n", encoding="utf-8")
    return shard


def test_path_b_all_failed_renders_fail_the_task(tmp_path, registry, monkeypatch,  # noqa: F811
                                                 boom) -> None:
    """Review 2: it used to be ``processed_empty`` — a COMPLETED task whose
    only trace was the rows. MUTATION: keep processed_empty → exit 0 → fails."""
    monkeypatch.delenv("COLLECTION_MANIFEST_DIR", raising=False)
    registry(_entry_spec(dict(PLAIN)))
    receipt = tmp_path / "r.json"
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _Embedder())
    args = ingest_shard.parse_args([
        str(_bad_only(tmp_path)), "--vector-backend", "memory", "--text-backend", "memory",
        "--qdrant-url", DEAD, "--es-url", DEAD, *_SIZE, "--out", str(receipt)])
    assert asyncio.run(ingest_shard.amain(args, it.resolve(CID, registry=REG))) == 1
    rec = ShardReceipt.load(receipt)
    assert rec.status == "failed" and rec.error.startswith("text_render_failed")
    [row] = rec.docs
    assert row.error.startswith("text_render_failed: ValueError")  # its own reason kept


def test_path_c_all_failed_renders_name_the_reason(tmp_path, registry, monkeypatch,  # noqa: F811
                                                   boom) -> None:
    registry(_entry_spec(dict(PLAIN)))
    out, receipt = tmp_path / "c.emb.jsonl", tmp_path / "c.json"
    monkeypatch.setattr(embed_shard, "make_embedder_auto", lambda **kw: _Embedder())
    assert embed_shard.main([str(_bad_only(tmp_path)), "--out", str(out), "--receipt",
                             str(receipt), "--collection-id", CID, "--registry", REG,
                             *_SIZE]) == 1
    rec = ShardReceipt.load(receipt)
    assert rec.status == "failed" and "text_render_failed" in rec.error


def _run_d(tmp_path, monkeypatch, shard, *, resume=False):
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _Embedder())
    metrics = tmp_path / "doc-metrics.jsonl"
    args = _args(shard, checkpoint=tmp_path / "d.ckpt", doc_metrics_out=metrics, resume=resume)
    code = 0
    try:
        asyncio.run(ingest_jsonl.run(args, it.resolve(CID, registry=REG)))
    except SystemExit as e:
        code = e.code
    rows = [json.loads(x) for x in metrics.read_text(encoding="utf-8").splitlines()] \
        if metrics.exists() else []
    return code, store, rows


def test_path_d_all_failed_renders_exit_1_label_the_rows_and_resume_retries(
    tmp_path, registry, monkeypatch, boom  # noqa: F811
) -> None:
    """Review 2. MUTATIONS: no failed batch for an all-render-failed batch →
    exit 0 and the checkpoint moves past the document → fails; the metrics
    row says 'quarantined' → fails."""
    registry(_entry_spec(dict(PLAIN)))
    shard = _bad_only(tmp_path)
    code, store, rows = _run_d(tmp_path, monkeypatch, shard)
    assert code == 1 and not store.points
    assert rows and all(str(r.get("error", "")).find("text_render_failed") >= 0 for r in rows)
    assert not any("quarantined" in str(r.get("error", "")) for r in rows)
    # The render bug is fixed: --resume retries the document (the checkpoint
    # did not move past it).
    monkeypatch.setattr(md, "markdown_plain", lambda doc: ("Bad fixed", None))
    code, store, _rows = _run_d(tmp_path, monkeypatch, shard, resume=True)
    assert code == 0 and [c.content for c in store.points.values()] == ["Bad fixed"]


def test_path_d_a_partial_failure_keeps_going_with_a_labelled_row(
    tmp_path, registry, monkeypatch, boom  # noqa: F811
) -> None:
    registry(_entry_spec(dict(PLAIN)))
    code, store, rows = _run_d(tmp_path, monkeypatch, _two_pages(tmp_path))
    assert code == 0
    assert [c.content for c in store.points.values()] == ["Good\n\nA page that renders."]
    errors = sorted(str(r.get("error")) for r in rows)
    assert errors[0] == "None" and errors[1].startswith("text_render_failed: ValueError")


# --------------------------------------------------------------------------- #
# a render-failed document never reaches the chunker (the filters, pinned
# independently of the emptied content)
# --------------------------------------------------------------------------- #

class _SpyChunker:
    """Records the source of every document handed to ``chunk``."""

    def __init__(self, inner, seen: list[str]) -> None:
        self._inner, self.seen = inner, seen
        self.chunk_size, self.chunk_overlap = inner.chunk_size, inner.chunk_overlap

    def chunk(self, doc):  # noqa: ANN001
        self.seen.append(doc.source)
        return self._inner.chunk(doc)


@pytest.fixture
def boom_keeps_text(monkeypatch, boom):
    """``boom``, with the emptying of a failed document's content undone, so
    only the chunk loops' filters stand between it and the chunker."""
    from ragstack.ingestion import structure as structure_mod

    real = structure_mod.apply_text_render

    def keep_text(documents, render, counts=None):
        docs = list(documents)
        raw = {id(d): d.content for d in docs}
        out = real(docs, render, counts)
        for d in docs:
            if structure_mod.render_failure_of(d):
                d.content = raw[id(d)]
        return out

    monkeypatch.setattr(structure_mod, "apply_text_render", keep_text)
    import ragstack.ingestion.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "apply_text_render", keep_text)
    monkeypatch.setattr(ingest_jsonl, "apply_text_render", keep_text)


def test_a_render_failed_document_never_reaches_the_chunker_on_any_path(
    tmp_path, registry, monkeypatch, boom_keeps_text  # noqa: F811
) -> None:
    """Review 2. MUTATION: drop the filter at any of the three chunk loops
    (pipeline.prepare_documents — paths A/B —, iter_embed_source — C —,
    ingest_jsonl's loop — D) → the bad page reaches chunk() → fails."""
    from ragstack.ingestion.shard import run_shard

    markers = structure_from_params("fixed", PLAIN)
    shard = _two_pages(tmp_path)

    def pipe(seen):
        return IngestionPipeline(loader=JsonlLoader(), chunker=_SpyChunker(_chunker(), seen),
                                 embedder=_Embedder(), vector_store=InMemoryVectorStore(),
                                 text_index=InMemoryTextIndex(), structure_markers=markers)

    # A (prepare_source) and B (run_shard → embed_documents → prepare_documents).
    seen_a: list[str] = []
    asyncio.run(pipe(seen_a).prepare_source(str(shard)))
    seen_b: list[str] = []
    asyncio.run(run_shard(pipe(seen_b), str(shard), "public", "s"))
    # C (iter_embed_source).
    seen_c: list[str] = []
    p_c = pipe(seen_c)

    async def _iter():
        return [c async for g in p_c.iter_embed_source(str(shard)) for c in g]

    asyncio.run(_iter())
    # D (ingest_jsonl's own loop), with its chunker replaced by the spy.
    seen_d: list[str] = []
    real_build = ingest_jsonl.build_chunker

    def spy_build(*a, **kw):
        chunker, counter, budget = real_build(*a, **kw)
        return _SpyChunker(chunker, seen_d), counter, budget

    monkeypatch.setattr(ingest_jsonl, "build_chunker", spy_build)
    registry(_entry_spec(dict(PLAIN)))
    _run_d(tmp_path, monkeypatch, shard)
    for seen in (seen_a, seen_b, seen_c, seen_d):
        assert seen == ["/d/good.md"], seen


# --------------------------------------------------------------------------- #
# #828 PR 7: the render-failure filter holds under structure_cuts; stripped
# headings give URL-free heading prefixes
# --------------------------------------------------------------------------- #

CUT_PLAIN = {**PLAIN, "structure_cuts": "sections/1", "embed_heading_path": "prefix/1"}


def _cut_chunker():
    chunker, _, _ = build_chunker("sentence", chunk_size=200,
                                  chunk_overlap=20, model="m", token_backend="estimate",
                                  max_tokens=4096, structure_cuts="sections/1")
    return chunker


def test_a_failed_render_is_left_out_under_structure_cuts_on_every_path(
    tmp_path, registry, monkeypatch, boom  # noqa: F811
) -> None:
    """A render-failed document is filtered out before the (cutting) chunker on
    paths B, C and D: never chunked raw, never stamped, never prefixed.
    MUTATION: drop the filter before chunking → the raw bad page is stored → fails."""
    monkeypatch.delenv("COLLECTION_MANIFEST_DIR", raising=False)
    registry(_spec(chunk_method="sentence", chunk_size=200, chunk_overlap=20,
                   chunk_params=dict(CUT_PLAIN)))
    target = it.resolve(CID, registry=REG)
    markers = structure_from_params("sentence", CUT_PLAIN)
    shard = _two_pages(tmp_path)
    size = ["--chunk-method", "sentence", *_SIZE[2:]]
    good = ["Good\n\nA page that renders."]
    # B
    emb, receipt = tmp_path / "e.jsonl", tmp_path / "r.json"
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _Embedder())
    args = ingest_shard.parse_args([
        str(shard), "--vector-backend", "memory", "--text-backend", "memory",
        "--qdrant-url", DEAD, "--es-url", DEAD, *size, "--embedding-file", str(emb),
        "--out", str(receipt)])
    assert asyncio.run(ingest_shard.amain(args, target)) == 0
    rows = {Path(r.source).name: r for r in ShardReceipt.load(receipt).docs}
    assert rows["bad.md"].error.startswith("text_render_failed") and not rows["bad.md"].chunk_ids
    b = _emb_rows(emb)
    assert [r["content"] for r in b] == good and b[0]["metadata"]["section_path"] == ["Good"]
    # C (and A's chunk loop): the pipeline with the cutting chunker.
    chunked: list[str] = []

    def _spied():
        chunker = _cut_chunker()
        real = chunker.chunk

        def chunk(doc):
            chunked.append(Path(doc.source).name)
            return real(doc)

        chunker.chunk = chunk  # type: ignore[method-assign]
        return chunker

    pipe = IngestionPipeline(loader=JsonlLoader(), chunker=_spied(), embedder=_Embedder(),
                             vector_store=InMemoryVectorStore(),
                             text_index=InMemoryTextIndex(), structure_markers=markers)

    async def _iter():
        return [c async for g in pipe.iter_embed_source(str(shard)) for c in g]

    assert [c.content for c in asyncio.run(_iter())] == good
    assert pipe.text_render_failures() == 1
    chunks, _ = asyncio.run(pipe.prepare_documents(JsonlLoader().load(str(shard))))
    assert [c.content for c in chunks] == good
    # The cutter never saw the failed page, on either chunk loop.
    assert chunked == ["good.md", "good.md"]
    # D
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _Embedder())
    real_build = ingest_jsonl.build_chunker
    chunked.clear()

    def build(*a, **kw):
        chunker, counter, budget = real_build(*a, **kw)
        real = chunker.chunk

        def chunk(doc):
            chunked.append(Path(doc.source).name)
            return real(doc)

        chunker.chunk = chunk
        return chunker, counter, budget

    monkeypatch.setattr(ingest_jsonl, "build_chunker", build)
    asyncio.run(ingest_jsonl.run(_args(shard, checkpoint=tmp_path / "d.ckpt",
                                       chunk_method="sentence"), target))
    assert chunked == ["good.md"]
    assert [c["content"] if isinstance(c, dict) else c.content
            for c in store.points.values()] == good


async def test_a_stripped_heading_prefix_carries_no_url(tmp_path) -> None:
    """#937's fix batch: a stripped heading's title is its link TEXT, so the
    embed_heading_path prefix never carries a URL, while a body link keeps its
    target in the stored content."""
    seen: list[str] = []

    class _Recording(_Embedder):
        async def embed(self, texts):  # noqa: ANN001
            seen.extend(texts)
            return await super().embed(texts)

    src = tmp_path / "page.md"
    src.write_text("# See [the docs](https://d.example/x)\n\n## Usage\n\nRun [it](https://e.example/y) once.\n",
                   encoding="utf-8")
    pipe = IngestionPipeline(loader=default_loader_registry(ingest_root=str(tmp_path)),
                             chunker=_cut_chunker(), embedder=_Recording(),
                             vector_store=InMemoryVectorStore(),
                             text_index=InMemoryTextIndex(),
                             structure_markers=structure_from_params("sentence", CUT_PLAIN))
    chunks = await pipe.embed_source(str(src))
    paths = [c.metadata.get("section_path") for c in chunks]
    assert ["See the docs", "Usage"] in paths
    prefixes = [t.split("\n", 1)[0] for t in seen]
    assert "See the docs › Usage" in prefixes
    assert not any("https://" in p for p in prefixes)
    assert any("it (https://e.example/y)" in c.content for c in chunks)
    assert not any("d.example" in c.content for c in chunks)


def test_a_failed_render_empties_and_marks_the_document(boom) -> None:  # noqa: F811
    """The second of the two deliberately redundant guards, pinned on its own:
    the render step empties a failed document (and drops its structure) as
    well as marking it, so even a caller without the filter chunks nothing.
    MUTATION: mark without emptying → fails here (the outcome tests cannot
    see it: the callers' filter alone keeps the document out)."""
    from ragstack.ingestion.structure import apply_text_render, render_failure_of

    doc = Document(id="b", content="# Bad\n\nBOOM here.\n", metadata={}, source="/d/bad.md")
    apply_text_render([doc], "markdown-plain/1")
    assert render_failure_of(doc).startswith("text_render_failed")
    assert doc.content == "" and doc.structure is None


def test_an_all_render_failed_batch_under_cuts_leaves_no_cache_entry_and_no_cut_counts(
    tmp_path, registry, monkeypatch, capsys, boom  # noqa: F811
) -> None:
    """Path D, structure_cuts + segmentation cache: a batch whose every document
    failed its render is a failed batch (exit 1) that never reached the cutter,
    so it writes no cached spans and no ``cuts`` counts; after the render is
    fixed, ``--resume`` chunks it with the cuts and caches it then."""
    registry(_spec(chunk_method="sentence", chunk_size=200, chunk_overlap=20,
                   chunk_params=dict(CUT_PLAIN)))
    shard, cache = _bad_only(tmp_path), tmp_path / "seg.jsonl"
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _Embedder())

    def run(resume: bool) -> int:
        try:
            asyncio.run(ingest_jsonl.run(_args(
                shard, checkpoint=tmp_path / "d.ckpt", chunk_method="sentence",
                segmentation_cache=cache, resume=resume), it.resolve(CID, registry=REG)))
        except SystemExit as e:
            return int(e.code or 0)
        return 0

    assert run(False) == 1 and not store.points
    err = capsys.readouterr().err
    assert "segmentation cache: 0 hit / 0 miss" in err and '"cuts"' not in err
    assert not cache.exists() or cache.read_text(encoding="utf-8") == ""
    monkeypatch.setattr(md, "markdown_plain",
                        lambda doc: ("# Fixed\n\nThe page renders now.", None))
    assert run(True) == 0
    assert [c.content for c in store.points.values()] == ["# Fixed\n\nThe page renders now."]
    assert "segmentation cache: 0 hit / 1 miss" in capsys.readouterr().err
