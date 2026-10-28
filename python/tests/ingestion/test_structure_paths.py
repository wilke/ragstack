"""#828 PR 1: structure through every chunk loop, and the JSONL carrier.

The same structured shard is fed to the three chunk loops the design names
(§6, §8): ``IngestionPipeline.prepare_documents`` (paths A/B, via
``prepare_source``), ``IngestionPipeline.iter_embed_source`` (path C, what
``embed_shard.py`` runs) and ``scripts/ingest_jsonl.py``'s own loop (path D,
which builds ``Document`` itself and bypasses ``JsonlLoader``). The stamped
metadata must be identical on all three. Also: ``JsonlLoader`` and
``ingest_jsonl`` drop an invalid structure with a counted reason and keep the
document, ignore the key byte-for-byte when it is absent, and the extract tool
writes the key only when a loader built a structure.

In-memory stores and fake embedders only; no live store, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ragstack.ingestion.chunker_config import build_chunker
from ragstack.ingestion.loaders import JsonlLoader, LoaderRegistry
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import ShardReceipt
from ragstack.ingestion.structure import STAMP_KEYS
from ragstack.models import Document
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from tests.structure_support import paper, record, write_shard
from tests.unit.test_ingest_jsonl_reingest import _args, _FakeEmbedder, _FakeStore, ingest_jsonl

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import embed_shard  # noqa: E402
import ingest_shard  # noqa: E402
import pdf_extract  # noqa: E402


class _Embedder:
    async def embed(self, texts):  # noqa: ANN001
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _chunker():
    # The chunker ingest_jsonl builds from _args(): fixed 200/20, estimate counter.
    chunker, _, _ = build_chunker("fixed", chunk_size=200, chunk_overlap=20, model="m",
                                  token_backend="estimate", max_tokens=4096)
    return chunker


def _pipeline(*, on: bool = True) -> IngestionPipeline:
    return IngestionPipeline(loader=JsonlLoader(), chunker=_chunker(), embedder=_Embedder(),
                             vector_store=InMemoryVectorStore(),
                             text_index=InMemoryTextIndex(), structure_metadata=on)


def _stamps(chunks) -> dict[str, dict]:
    return {c.id: {k: c.metadata[k] for k in STAMP_KEYS if k in c.metadata} for c in chunks}


async def _via_prepare(shard: Path, on: bool = True):
    return (await _pipeline(on=on).prepare_source(str(shard))).chunks


async def _via_iter(shard: Path, on: bool = True):
    out = []
    async for group in _pipeline(on=on).iter_embed_source(str(shard)):
        out.extend(group)
    return out


async def _via_ingest_jsonl(shard: Path, tmp_path: Path, monkeypatch, on: bool = True,
                            tag: str = "a"):
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
    monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")
    metrics = tmp_path / f"run-{tag}.jsonl"
    await ingest_jsonl.run(_args(shard, checkpoint=tmp_path / f"{tag}.ckpt",
                                 structure_metadata="on" if on else "off",
                                 run_metrics_out=metrics))
    row = json.loads(metrics.read_text(encoding="utf-8").splitlines()[-1])
    return list(store.points.values()), row


# --------------------------------------------------------------------------- #
# Parity: one shard, three chunk loops
# --------------------------------------------------------------------------- #

async def test_one_structured_shard_stamps_identically_on_all_three_loops(
    tmp_path, monkeypatch
) -> None:
    content, structure = paper()
    shard = tmp_path / "s.jsonl"
    write_shard(shard, [record(content, structure),
                        record("An unstructured neighbour document. " * 12, None,
                               path="/corpus/plain.pdf")])
    a = _stamps(await _via_prepare(shard))
    c = _stamps(await _via_iter(shard))
    d_chunks, _ = await _via_ingest_jsonl(shard, tmp_path, monkeypatch)
    d = _stamps(d_chunks)
    assert a and a == c == d
    structured = [v for v in a.values() if v]
    plain = [v for v in a.values() if not v]
    assert len(structured) >= 3 and plain, "both documents must contribute chunks"
    assert any(v.get("section_title") == "Sample preparation"
               and "methods" in v["section_roles"] for v in structured)
    for v in structured:
        assert {"section_level", "section_roles", "structure_source",
                "structure_version"} <= set(v)


async def test_stamping_off_writes_no_structure_key_on_any_loop(tmp_path, monkeypatch) -> None:
    content, structure = paper()
    shard = tmp_path / "s.jsonl"
    write_shard(shard, [record(content, structure)])
    for chunks in (await _via_prepare(shard, on=False), await _via_iter(shard, on=False),
                   (await _via_ingest_jsonl(shard, tmp_path, monkeypatch, on=False))[0]):
        assert chunks and not any(set(c.metadata) & set(STAMP_KEYS) for c in chunks)


async def test_stamping_changes_no_chunk_id_content_or_offsets(tmp_path) -> None:
    """Stamping is not identity (Q3): the same shard, on and off, gives the same
    chunk ids, text and offsets — only payload keys differ."""
    content, structure = paper()
    shard = tmp_path / "s.jsonl"
    write_shard(shard, [record(content, structure)])
    on, off = await _via_prepare(shard, on=True), await _via_prepare(shard, on=False)
    assert [(c.id, c.content, c.start_char, c.end_char) for c in on] == \
        [(c.id, c.content, c.start_char, c.end_char) for c in off]


# --------------------------------------------------------------------------- #
# The carrier: JsonlLoader and ingest_jsonl read the key through one reader
# --------------------------------------------------------------------------- #

def test_jsonl_loader_round_trips_a_valid_structure(tmp_path) -> None:
    content, structure = paper()
    shard = tmp_path / "s.jsonl"
    write_shard(shard, [record(content, structure)])
    loader = JsonlLoader()
    [doc] = loader.load(str(shard))
    assert doc.structure == structure
    assert not loader.dropped


def test_jsonl_loader_ignores_an_absent_key_byte_for_byte(tmp_path) -> None:
    content, _ = paper()
    shard = tmp_path / "s.jsonl"
    write_shard(shard, [record(content, None)])
    loader = JsonlLoader()
    [doc] = loader.load(str(shard))
    assert doc.structure is None and "structure" not in doc.model_dump()
    rebuilt = Document(id=doc.id, content=doc.content, metadata=doc.metadata, source=doc.source)
    assert json.dumps(doc.model_dump()) == json.dumps(rebuilt.model_dump())
    assert not loader.dropped.structure_invalid


def test_jsonl_loader_drops_an_invalid_structure_and_keeps_the_document(tmp_path) -> None:
    """MUTATION: skip ``validate_structure`` in the shared reader → the stale
    structure (sha of a different text) is accepted → this fails."""
    content, structure = paper()
    stale = structure.model_copy(update={"content_sha256": "0" * 64})
    broken = structure.model_dump()
    broken["sections"][1]["level"] = 2
    shard = tmp_path / "s.jsonl"
    write_shard(shard, [record(content, stale, path="/corpus/one.pdf"),
                        record(content, broken, path="/corpus/two.pdf"),
                        record(content, {"sections": "nope"}, path="/corpus/three.pdf")])
    loader = JsonlLoader()
    docs = loader.load(str(shard))
    assert len(docs) == 3 and all(d.structure is None for d in docs)
    assert all(d.content == content for d in docs)
    assert loader.dropped.structure_invalid == {"content_sha256": 1, "level": 1, "malformed": 1}
    assert "structure dropped, document kept" in loader.dropped.summary()


async def test_ingest_jsonl_drops_an_invalid_structure_and_keeps_the_document(
    tmp_path, monkeypatch
) -> None:
    content, structure = paper()
    stale = structure.model_copy(update={"content_sha256": "0" * 64})
    shard = tmp_path / "bad.jsonl"
    write_shard(shard, [record(content, stale)])
    chunks, row = await _via_ingest_jsonl(shard, tmp_path, monkeypatch, tag="bad")
    assert chunks, "the document must still be ingested"
    assert not any(set(c.metadata) & set(STAMP_KEYS) for c in chunks)
    assert row["structure_dropped"] == 1

    plain = tmp_path / "plain.jsonl"
    write_shard(plain, [record(content, None)])
    plain_chunks, plain_row = await _via_ingest_jsonl(plain, tmp_path, monkeypatch, tag="p")
    # Dropped structure == no structure, chunk for chunk; and an absent key adds
    # no run-metrics field (byte-identical row shape).
    assert [c.model_dump() for c in chunks] == [c.model_dump() for c in plain_chunks]
    assert "structure_dropped" not in plain_row


async def test_ingest_jsonl_absent_key_is_byte_identical_with_stamping_on(
    tmp_path, monkeypatch
) -> None:
    content, _ = paper()
    shard = tmp_path / "plain.jsonl"
    write_shard(shard, [record(content, None)])
    on, _ = await _via_ingest_jsonl(shard, tmp_path, monkeypatch, on=True, tag="on")
    off, _ = await _via_ingest_jsonl(shard, tmp_path, monkeypatch, on=False, tag="off")
    assert [c.model_dump() for c in on] == [c.model_dump() for c in off]


async def test_a_double_jsonl_pass_round_trips_the_key_unchanged(tmp_path) -> None:
    """#833 item 3: ``.jsonl`` is read twice on GoWe; the key survives a
    load → re-serialise → load cycle unchanged."""
    content, structure = paper()
    first = tmp_path / "a.jsonl"
    write_shard(first, [record(content, structure)])
    [doc] = JsonlLoader().load(str(first))
    second = tmp_path / "b.jsonl"
    assert doc.structure is not None
    write_shard(second, [record(doc.content, doc.structure)])
    [again] = JsonlLoader().load(str(second))
    assert again.structure == structure


# --------------------------------------------------------------------------- #
# The extract tool writes the key, only when a loader built a structure
# --------------------------------------------------------------------------- #

class _StructuredLoader:
    def __init__(self, with_structure: bool) -> None:
        self.with_structure = with_structure

    def load(self, source: str) -> list[Document]:
        content, structure = paper()
        return [Document(id="d", content=content, metadata={"filename": "x.pdf"},
                         source=source, structure=structure if self.with_structure else None)]


@pytest.mark.parametrize("with_structure", [True, False])
def test_pdf_extract_writes_the_key_only_when_the_loader_built_one(
    tmp_path, with_structure
) -> None:
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-stub")
    registry = LoaderRegistry()
    registry.register(".pdf", _StructuredLoader(with_structure))
    records, skipped = pdf_extract.extract_documents([f], registry=registry)
    assert not skipped and len(records) == 1
    if with_structure:
        assert set(records[0]) == {"text", "path", "metadata", "structure"}
        assert records[0]["structure"]["adapter"] == "test/1"
        # ... and it is never inside metadata (§4).
        assert "structure" not in records[0]["metadata"]
        shard = tmp_path / "s.jsonl"
        pdf_extract.write_jsonl(records, str(shard))
        [doc] = JsonlLoader().load(str(shard))
        assert doc.structure is not None and doc.structure.adapter == "test/1"
    else:
        assert set(records[0]) == {"text", "path", "metadata"}


# --------------------------------------------------------------------------- #
# The shard tools: flag, receipt, embedding file
# --------------------------------------------------------------------------- #

def test_both_shard_tools_take_the_flag_default_off() -> None:
    a = ingest_shard.parse_args(["x.jsonl", "--qdrant-url", "http://127.0.0.1:1",
                                 "--es-url", "http://127.0.0.1:1"])
    b = embed_shard.parse_args(["x.jsonl"])
    assert a.structure_metadata == "off" and b.structure_metadata == "off"
    assert embed_shard.parse_args(["x.jsonl", "--structure-metadata", "on"]).structure_metadata \
        == "on"
    with pytest.raises(SystemExit):
        embed_shard.parse_args(["x.jsonl", "--structure-metadata", "yes"])


def _run_ingest_shard(tmp_path: Path, monkeypatch, *extra: str) -> tuple[ShardReceipt, dict]:
    content, structure = paper()
    shard = tmp_path / "s0.jsonl"
    write_shard(shard, [record(content, structure),
                        record("plain text " * 30, None, path="/corpus/plain.pdf")])
    out = tmp_path / "receipt.json"
    emb_file = tmp_path / "s0.emb.jsonl"
    argv = [str(shard), "--vector-backend", "memory", "--text-backend", "memory",
            "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1",
            "--chunk-method", "fixed", "--chunk-size", "120", "--chunk-overlap", "0",
            "--embedding-model", "", "--chunk-token-counter", "estimate",
            "--symbol-glossary", "off", "--embedding-file", str(emb_file),
            "--out", str(out), *extra]
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _Embedder())
    assert ingest_shard.main(argv) == 0
    raw = json.loads(out.read_text(encoding="utf-8"))
    return ShardReceipt.load(out), raw


def test_ingest_shard_records_enrichment_structure_when_on(tmp_path, monkeypatch) -> None:
    receipt, _ = _run_ingest_shard(tmp_path, monkeypatch, "--structure-metadata", "on")
    rec = receipt.enrichment["structure"]  # type: ignore[index]
    assert rec["stamp"] == "stamp/1"
    assert rec["adapters"] == {"test/1": 1} and rec["structure_missing"] == 1
    assert rec["chunks_stamped"] > 0


def test_ingest_shard_off_receipt_is_unchanged(tmp_path, monkeypatch) -> None:
    receipt, raw = _run_ingest_shard(tmp_path, monkeypatch)
    assert receipt.enrichment is None and "enrichment" not in raw


def test_embed_shard_stamps_the_embedding_file(tmp_path, monkeypatch) -> None:
    """Path C: ``embed_shard.py`` → ``run_embed_shard`` → ``iter_embed_source``
    stamps, and the stamped chunks reach the embedding file the load step reads."""
    content, structure = paper()
    shard = tmp_path / "s0.jsonl"
    write_shard(shard, [record(content, structure)])
    out, receipt = tmp_path / "s0.emb.jsonl", tmp_path / "r.json"
    monkeypatch.setattr(embed_shard, "make_embedder_auto", lambda **kw: _Embedder())
    argv = [str(shard), "--out", str(out), "--receipt", str(receipt),
            "--chunk-method", "fixed", "--chunk-size", "200", "--chunk-overlap", "20",
            "--embedding-model", "", "--chunk-token-counter", "estimate",
            "--symbol-glossary", "off", "--structure-metadata", "on"]
    assert embed_shard.main(argv) == 0
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
    chunk_rows = [x for x in lines if "metadata" in x]
    assert chunk_rows and all("structure_version" in x["metadata"] for x in chunk_rows)
    assert ShardReceipt.load(receipt).enrichment["structure"]["adapters"] == {"test/1": 1}
