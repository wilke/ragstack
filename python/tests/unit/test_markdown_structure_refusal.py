"""#828 PR 2: a missing ``[markdown]`` extra is a refusal, never a fallback (§6).

When the structure step is on and the parser is absent: the adapter raises
(never returns "no structure"), the pipeline item fails, the bulk tools exit 2
at configuration before reading a record or writing a file, and ``content`` is
never touched. When the step is off, nothing asks for the parser and every
path is what it was. (The API's 503 is in
``tests/api/test_ingest_structure_markdown_refusal.py``.)

No parser is needed here: the extra is hidden per test
(``structure_support.hide_markdown_extra``), so these run in any env.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ragstack.ingestion.chunker_config import build_chunker
from ragstack.ingestion.loaders import default_loader_registry
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.structure import (
    STAMP_KEYS,
    StructureBackendUnavailable,
    attach_structure,
    require_structure_backends,
    require_structure_backends_or_exit,
)
from ragstack.models import Document
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from tests.structure_support import hide_markdown_extra, record, write_shard

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import embed_shard  # noqa: E402
import ingest_jsonl  # noqa: E402
import ingest_shard  # noqa: E402

_MD = "# Title\n\nBody text of a synthetic page.\n\n## Part\n\nMore text.\n"


class _Embedder:
    async def embed(self, texts):  # noqa: ANN001
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _pipeline(on: bool) -> IngestionPipeline:
    chunker, _, _ = build_chunker("fixed", chunk_size=40, chunk_overlap=0, model="m",
                                  token_backend="estimate", max_tokens=4096)
    return IngestionPipeline(loader=default_loader_registry(), chunker=chunker,
                             embedder=_Embedder(), vector_store=InMemoryVectorStore(),
                             text_index=InMemoryTextIndex(), structure_metadata=on)


def test_attach_raises_naming_the_extra_and_leaves_the_document_alone(monkeypatch) -> None:
    """MUTATION: let the adapter return ``None`` (or swallow the error) when the
    parser is missing → the document silently ingests unstructured → fails."""
    hide_markdown_extra(monkeypatch)
    doc = Document(id="d", content=_MD, source="/d/page.md")
    with pytest.raises(StructureBackendUnavailable) as e:
        attach_structure([doc])
    assert e.value.extra == "markdown" and e.value.adapter == "markdown/1"
    assert "[markdown]" in e.value.client_detail
    assert doc.structure is None and doc.content == _MD


def test_require_refuses_and_a_non_markdown_batch_is_refused_too(monkeypatch) -> None:
    """The check is configuration-level: it does not wait for a `.md` record,
    so a run cannot pass or fail by what its batch happens to hold."""
    hide_markdown_extra(monkeypatch)
    with pytest.raises(StructureBackendUnavailable):
        require_structure_backends()
    # A .pdf record alone triggers no parse, so attach itself is a no-op ...
    doc = Document(id="d", content="pdf text", source="/d/a.pdf")
    attach_structure([doc])
    assert doc.structure is None
    # ... which is why the tools and the API call require_* up front.
    with pytest.raises(SystemExit) as ex:
        require_structure_backends_or_exit("on")
    assert ex.value.code == 2
    require_structure_backends_or_exit("off")  # off: nothing asks for the parser


async def test_pipeline_on_fails_the_item_and_does_not_touch_content(tmp_path,
                                                                      monkeypatch) -> None:
    hide_markdown_extra(monkeypatch)
    src = tmp_path / "page.md"
    src.write_text(_MD, encoding="utf-8")
    pipe = _pipeline(on=True)
    with pytest.raises(StructureBackendUnavailable):
        await pipe.prepare_source(str(src))
    assert src.read_text(encoding="utf-8") == _MD


async def test_pipeline_off_never_asks_for_the_parser(tmp_path, monkeypatch) -> None:
    """Off is byte-for-byte the old path, extra or no extra."""
    src = tmp_path / "page.md"
    src.write_text(_MD, encoding="utf-8")
    baseline = (await _pipeline(on=False).prepare_source(str(src))).chunks
    hide_markdown_extra(monkeypatch)
    hidden = (await _pipeline(on=False).prepare_source(str(src))).chunks
    assert [c.model_dump() for c in hidden] == [c.model_dump() for c in baseline]
    assert baseline and not any(set(c.metadata) & set(STAMP_KEYS) for c in baseline)


def _shard(tmp_path: Path) -> Path:
    shard = tmp_path / "s0.jsonl"
    write_shard(shard, [record(_MD, None, path="/docs/page.md")])
    return shard


def test_ingest_shard_exits_2_before_writing_anything(tmp_path, monkeypatch, capsys) -> None:
    hide_markdown_extra(monkeypatch)
    shard, out = _shard(tmp_path), tmp_path / "receipt.json"
    argv = [str(shard), "--vector-backend", "memory", "--text-backend", "memory",
            "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1",
            "--chunk-method", "fixed", "--chunk-size", "120", "--chunk-overlap", "0",
            "--embedding-model", "", "--chunk-token-counter", "estimate",
            "--symbol-glossary", "off", "--out", str(out), "--structure-metadata", "on"]
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _Embedder())
    with pytest.raises(SystemExit) as ex:
        ingest_shard.main(argv)
    assert ex.value.code == 2 and not out.exists()
    assert "[markdown]" in capsys.readouterr().err
    # Off: the same shard ingests (the extra is still hidden).
    assert ingest_shard.main(argv[:-2]) == 0 and out.exists()


def test_embed_shard_exits_2_before_writing_anything(tmp_path, monkeypatch, capsys) -> None:
    hide_markdown_extra(monkeypatch)
    shard, out = _shard(tmp_path), tmp_path / "s0.emb.jsonl"
    argv = [str(shard), "--out", str(out), "--receipt", str(tmp_path / "r.json"),
            "--chunk-method", "fixed", "--chunk-size", "200", "--chunk-overlap", "20",
            "--embedding-model", "", "--chunk-token-counter", "estimate",
            "--symbol-glossary", "off", "--structure-metadata", "on"]
    monkeypatch.setattr(embed_shard, "make_embedder_auto", lambda **kw: _Embedder())
    with pytest.raises(SystemExit) as ex:
        embed_shard.main(argv)
    assert ex.value.code == 2 and not out.exists()
    assert "[markdown]" in capsys.readouterr().err
    assert embed_shard.main(argv[:-2]) == 0 and out.exists()


def test_ingest_jsonl_exits_2_before_the_registry_or_any_store(tmp_path, monkeypatch,
                                                                capsys) -> None:
    hide_markdown_extra(monkeypatch)
    shard = _shard(tmp_path)

    def _no_registry(*a, **kw):  # pragma: no cover - must not be reached
        raise AssertionError("resolved the registry before the configuration check")

    monkeypatch.setattr(ingest_jsonl.ingest_target, "resolve_or_exit", _no_registry)
    monkeypatch.setattr(sys, "argv", ["ingest_jsonl.py", str(shard), "--structure-metadata",
                                      "on"])
    with pytest.raises(SystemExit) as ex:
        ingest_jsonl.main()
    assert ex.value.code == 2
    assert "[markdown]" in capsys.readouterr().err
    assert json.loads(shard.read_text(encoding="utf-8").splitlines()[0])["text"] == _MD
