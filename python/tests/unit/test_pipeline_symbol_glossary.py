"""IngestionPipeline wiring of the per-document symbol glossary (#754).

The step runs after the boilerplate filter and before the embed, on BOTH the
materialized (``prepare_documents``) and the streaming (``iter_embed_source``)
halves; it never changes what is embedded; it degrades to "no glossary" on an
extractor bug; and the field survives the embedding file into the text index.
"""
from __future__ import annotations

import asyncio

from ragstack.ingestion.boilerplate import BoilerplateFilter
from ragstack.ingestion.embedding_file import read_embedding_file, write_embedding_file
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.symbol_glossary import VERSION_KEY, SymbolGlossaryAttacher
from ragstack.models import Chunk, Document
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore
from ragstack.tenancy import DEFAULT_TENANT

DEFINE = "In this model, where Qb is the breathing flow rate of one person."
VALUE = "Table 1 lists the parameters: Qb = 0.5 m3/h for a resting adult."
PLAIN = "Nothing symbolic is said in this paragraph about anything at all."
LICENCE = (
    "© The Author(s) 2026. This article is licensed under a Creative Commons "
    "Attribution 4.0 International License. Qb is mentioned here too."
)
_SEP = "\n@@\n"


class _Loader:
    def __init__(self, *docs: tuple[str, ...]) -> None:
        self._docs = docs

    def load(self, source: str) -> list[Document]:  # noqa: ARG002
        return [Document(id=f"doc-{i}", content=_SEP.join(p)) for i, p in enumerate(self._docs)]


class _Chunker:
    def chunk(self, doc: Document) -> list[Chunk]:
        out, pos = [], 0
        for i, part in enumerate(doc.content.split(_SEP)):
            start = doc.content.index(part, pos)
            out.append(Chunk(id=f"{doc.id}-{i}", doc_id=doc.id, content=part,
                             start_char=start, end_char=start + len(part)))
            pos = start + len(part)
        return out


class _Embedder:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [[1.0, 0.0] for _ in texts]


def _pipeline(embedder, attacher, boilerplate=None, *docs, text_index=None):
    return IngestionPipeline(
        loader=_Loader(*docs) if docs else _Loader((DEFINE, VALUE, PLAIN)),
        chunker=_Chunker(),
        embedder=embedder,
        vector_store=InMemoryVectorStore(),
        text_index=text_index or InMemoryTextIndex(),
        boilerplate_filter=boilerplate,
        glossary_attacher=attacher,
    )


def test_prepare_documents_attaches_and_embeds_content_only() -> None:
    emb = _Embedder()
    chunks = asyncio.run(_pipeline(emb, SymbolGlossaryAttacher()).embed_source("x"))
    by_id = {c.id: c for c in chunks}
    assert by_id["doc-0-1"].symbol_glossary == "Qb — breathing flow rate of one person"
    assert by_id["doc-0-2"].symbol_glossary == ""
    assert all(c.metadata[VERSION_KEY] == "rules/2" for c in chunks)
    assert emb.seen == [DEFINE, VALUE, PLAIN]  # the glossary is never embedded


def test_off_is_unchanged() -> None:
    chunks = asyncio.run(_pipeline(_Embedder(), None).embed_source("x"))
    assert all(c.symbol_glossary == "" and VERSION_KEY not in c.metadata for c in chunks)


def test_runs_after_boilerplate_drop() -> None:
    seen: list[list[str]] = []

    class Spy(SymbolGlossaryAttacher):
        def apply(self, documents, chunks):  # noqa: ANN001
            seen.append([c.id for c in chunks])
            return super().apply(documents, chunks)

    pipe = _pipeline(_Embedder(), Spy(), BoilerplateFilter(drop=True), (DEFINE, VALUE, LICENCE))
    chunks = asyncio.run(pipe.embed_source("x"))
    assert seen == [["doc-0-0", "doc-0-1"]]  # the licence chunk was dropped first
    assert {c.id for c in chunks} == {"doc-0-0", "doc-0-1"}


def test_streaming_path_attaches_per_group() -> None:
    emb = _Embedder()
    pipe = _pipeline(emb, SymbolGlossaryAttacher(), None, (DEFINE, VALUE), (PLAIN, VALUE))

    async def collect() -> list[Chunk]:
        out: list[Chunk] = []
        async for group in pipe.iter_embed_source("x", group_size=1):
            out.extend(group)
        return out

    by_id = {c.id: c for c in asyncio.run(collect())}
    assert by_id["doc-0-1"].symbol_glossary == "Qb — breathing flow rate of one person"
    # doc-1 never defines Qb: a definition from ANOTHER document must not leak in.
    assert by_id["doc-1-1"].symbol_glossary == ""
    assert by_id["doc-1-1"].metadata[VERSION_KEY] == "rules/2"


def test_extractor_bug_degrades_to_no_glossary(caplog) -> None:
    class Boom(SymbolGlossaryAttacher):
        def apply(self, documents, chunks):  # noqa: ANN001
            chunks[0].symbol_glossary = "half-applied"
            chunks[0].metadata[VERSION_KEY] = "rules/2"
            raise RuntimeError("bug")

    chunks = asyncio.run(_pipeline(_Embedder(), Boom()).embed_source("x"))
    assert len(chunks) == 3
    assert all(c.symbol_glossary == "" and VERSION_KEY not in c.metadata for c in chunks)
    assert "symbol glossary skipped" in caplog.text


def test_field_survives_embedding_file_and_reaches_text_index(tmp_path) -> None:
    chunks = asyncio.run(_pipeline(_Embedder(), SymbolGlossaryAttacher()).embed_source("x"))
    path = tmp_path / "e.jsonl"
    write_embedding_file(path, chunks)
    back, _ = read_embedding_file(path)
    assert [c.symbol_glossary for c in back] == [c.symbol_glossary for c in chunks]

    idx = InMemoryTextIndex(symbol_glossary_boost=1.0)
    pipe = _pipeline(_Embedder(), None, text_index=idx)
    asyncio.run(pipe.index_chunks(list(back)))
    hits = asyncio.run(idx.search("breathing flow", filters={"tenant_id": DEFAULT_TENANT}))
    assert {h.chunk.id for h in hits} == {"doc-0-0", "doc-0-1"}
    # Boost 0: the glossary is not consulted — the value chunk is invisible.
    idx0 = InMemoryTextIndex()
    asyncio.run(_pipeline(_Embedder(), None, text_index=idx0).index_chunks(list(back)))
    hits0 = asyncio.run(idx0.search("breathing flow", filters={"tenant_id": DEFAULT_TENANT}))
    assert "doc-0-1" not in [h.chunk.id for h in hits0]


def test_embedding_file_without_glossary_is_unchanged(tmp_path) -> None:
    on = asyncio.run(_pipeline(_Embedder(), None).embed_source("x"))
    path = tmp_path / "off.jsonl"
    write_embedding_file(path, on)
    assert "symbol_glossary" not in path.read_text(encoding="utf-8")


def test_provenance_reflects_what_the_step_did() -> None:
    from collections import Counter

    pipe = _pipeline(_Embedder(), SymbolGlossaryAttacher())
    assert pipe.glossary_provenance() is None  # configured, never ran
    asyncio.run(pipe.embed_source("x"))
    rec = pipe.glossary_provenance()["symbol_glossary"]  # type: ignore[index]
    assert rec["extractor"] == "rules/2" and rec["applied_batches"] == 1
    assert rec["degraded_batches"] == 0 and rec["chunks_with_glossary"] == 1
    snap = Counter(pipe.glossary_counts)
    assert pipe.glossary_provenance(snap) is None  # nothing since the snapshot
    assert _pipeline(_Embedder(), None).glossary_provenance() is None


def test_degraded_step_is_recorded_not_claimed() -> None:
    class Boom(SymbolGlossaryAttacher):
        def apply(self, documents, chunks):  # noqa: ANN001
            raise RuntimeError("bug")

    pipe = _pipeline(_Embedder(), Boom())
    asyncio.run(pipe.embed_source("x"))
    rec = pipe.glossary_provenance()["symbol_glossary"]  # type: ignore[index]
    assert rec["applied_batches"] == 0 and rec["degraded_batches"] == 1
