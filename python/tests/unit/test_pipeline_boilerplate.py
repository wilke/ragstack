"""IngestionPipeline honours the chunk-level boilerplate filter.

Covers the wiring rather than the classifier (see test_boilerplate.py): the
filter runs between chunk and embed, so a dropped chunk is never embedded and
never reaches a store, and both the materialized and the streaming halves of
ingestion apply it.
"""
from __future__ import annotations

import logging

import pytest

from ragstack.ingestion.boilerplate import BOILERPLATE_KEY, SECTION_KEY, BoilerplateFilter
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.models import Document
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore

PROSE = (
    "Bees visited flowers of Brassica napus significantly more often than those of "
    "Trifolium pratense, and seed set increased with visitation rate up to an "
    "asymptote at roughly six visits per flower in every year of the study."
)
LICENCE = (
    "© The Author(s) 2026. This article is licensed under a Creative Commons "
    "Attribution 4.0 International License. To view a copy of this licence visit "
    "http://creativecommons.org/licenses/by/4.0/."
)
REFERENCES = (
    "2. Aizen MA, Aguiar S, Biesmeijer JC, Garibaldi LA. Global agricultural "
    "productivity is threatened by increasing pollinator dependence. Glob Chang "
    "Biol. 2019;25(10):3516-3527. doi:10.1111/gcb.14736\n"
    "3. Klein AM, Vaissiere BE, Cane JH, Kremen C. Importance of pollinators in "
    "changing landscapes for world crops. Proc R Soc B. 2007;274:303-313.\n"
)


_SEP = "\n@@\n"


class _ParagraphLoader:
    """ONE document whose paragraphs are joined by a sentinel separator.

    A single document on purpose: with one paragraph per document, every
    boilerplate document would be *entirely* boilerplate and the filter's
    all-boilerplate guard (correctly) rescues it — which is a different
    behaviour, tested in test_boilerplate.py. A real paper is one document
    holding prose *and* its licence footer and reference list.
    """

    def __init__(self, *paragraphs: str) -> None:
        self._text = _SEP.join(paragraphs)

    def load(self, source: str) -> list[Document]:  # noqa: ARG002
        return [Document(id="doc-1", content=self._text, source="test")]


class _SeparatorChunker:
    """One chunk per paragraph — a deterministic stand-in for a real chunker."""

    def chunk(self, doc: Document):
        from ragstack.models import Chunk

        return [
            Chunk(id=f"{doc.id}-{i}", doc_id=doc.id, content=part)
            for i, part in enumerate(doc.content.split(_SEP))
        ]


class _FakeEmbedder:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [[1.0, 0.0] for _ in texts]


def _pipeline(embedder, boilerplate_filter):
    return IngestionPipeline(
        loader=_ParagraphLoader(PROSE, LICENCE, REFERENCES),
        chunker=_SeparatorChunker(),
        embedder=embedder,
        vector_store=InMemoryVectorStore(),
        text_index=InMemoryTextIndex(),
        boilerplate_filter=boilerplate_filter,
    )


@pytest.mark.asyncio
async def test_no_filter_leaves_ingest_unchanged() -> None:
    embedder = _FakeEmbedder()
    chunks = await _pipeline(embedder, None).embed_source("src")
    assert len(chunks) == 3
    assert all(SECTION_KEY not in c.metadata for c in chunks)


@pytest.mark.asyncio
async def test_flag_mode_stamps_every_chunk_but_indexes_all_of_them() -> None:
    embedder = _FakeEmbedder()
    chunks = await _pipeline(embedder, BoilerplateFilter()).embed_source("src")

    assert len(chunks) == 3
    assert len(embedder.seen) == 3
    by_id = {c.id: c for c in chunks}
    assert SECTION_KEY not in by_id["doc-1-0"].metadata          # prose untouched
    assert by_id["doc-1-1"].metadata[SECTION_KEY] == "license"
    assert by_id["doc-1-2"].metadata[SECTION_KEY] == "references"
    assert by_id["doc-1-2"].metadata[BOILERPLATE_KEY] is True


@pytest.mark.asyncio
async def test_drop_mode_never_embeds_the_dropped_chunks() -> None:
    """The drop happens before the embed, so it also saves the GPU round-trip."""
    embedder = _FakeEmbedder()
    chunks = await _pipeline(embedder, BoilerplateFilter(drop=True)).embed_source("src")

    assert [c.id for c in chunks] == ["doc-1-0"]
    assert embedder.seen == [PROSE]


@pytest.mark.asyncio
async def test_drops_are_logged_not_silent(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="ragstack.ingestion.pipeline"):
        await _pipeline(_FakeEmbedder(), BoilerplateFilter(drop=True)).embed_source("src")
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "boilerplate" in messages
    assert "dropped 2" in messages


@pytest.mark.asyncio
async def test_streaming_path_applies_the_filter_too() -> None:
    embedder = _FakeEmbedder()
    pipeline = _pipeline(embedder, BoilerplateFilter(drop=True))
    groups = [g async for g in pipeline.iter_embed_source("src", group_size=1)]

    assert [c.id for group in groups for c in group] == ["doc-1-0"]


@pytest.mark.asyncio
async def test_a_classifier_failure_never_fails_the_ingest() -> None:
    class _Exploding:
        def apply(self, chunks):
            raise RuntimeError("boom")

    chunks = await _pipeline(_FakeEmbedder(), _Exploding()).embed_source("src")
    assert len(chunks) == 3
