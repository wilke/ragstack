"""``index_chunks`` is the ingest boundary for chunk metadata (#603).

The point of refusing here is that the alternative place to discover a wrong type
is an Elasticsearch mapping — inferred from the first document to arrive, and
then not changeable in place. A ``pmid`` written as an int does not produce a bad
row; it produces a collection permanently incompatible with its peers for
cross-collection filtering, and the fix is a reindex.

Two properties matter beyond "it raises", and each has a test below: the refusal
happens BEFORE the delete-prior step (so a refused batch has not destroyed the
version it was meant to replace — the same rule ``EmptyIngestError`` enforces one
layer up), and an undeclared key still passes through (the namespace is open).
"""
from __future__ import annotations

import pytest

from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.metadata_schema import ChunkMetadataTypeError
from ragstack.models import Chunk
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore


def _chunk(cid: str, **metadata) -> Chunk:
    return Chunk(
        id=cid,
        doc_id="doc-1",
        content="x",
        embedding=[1.0, 0.0],
        metadata={"tenant_id": "public", **metadata},
    )


def _pipeline(vector_store=None, text_index=None) -> IngestionPipeline:
    return IngestionPipeline(
        loader=None,  # type: ignore[arg-type] — index_chunks needs no loader
        chunker=None,  # type: ignore[arg-type]
        embedder=None,  # type: ignore[arg-type]
        vector_store=vector_store or InMemoryVectorStore(),
        text_index=text_index or InMemoryTextIndex(),
    )


@pytest.mark.asyncio
async def test_a_conforming_batch_is_indexed():
    vs, ti = InMemoryVectorStore(), InMemoryTextIndex()
    ids = await _pipeline(vs, ti).index_chunks([_chunk("c1", pmid="31234567", year=2019)])
    assert ids == ["c1"]


@pytest.mark.asyncio
async def test_an_int_pmid_is_refused_at_the_boundary():
    with pytest.raises(ChunkMetadataTypeError) as e:
        await _pipeline().index_chunks([_chunk("c1", pmid=31234567)])
    assert "pmid" in str(e.value)
    assert "contracts/schemas/chunk_metadata.json" in str(e.value)


@pytest.mark.asyncio
async def test_a_string_year_is_refused_at_the_boundary():
    with pytest.raises(ChunkMetadataTypeError):
        await _pipeline().index_chunks([_chunk("c1", year="2019")])


@pytest.mark.asyncio
async def test_the_refusal_happens_before_the_delete_prior():
    """A refused batch must not have destroyed the prior version of the documents
    it was meant to replace."""
    vs, ti = InMemoryVectorStore(), InMemoryTextIndex()
    p = _pipeline(vs, ti)
    await p.index_chunks([_chunk("c1", pmid="31234567")])
    before = len(await vs.get_chunks(["c1"], {"tenant_id": "public"}))
    assert before == 1

    with pytest.raises(ChunkMetadataTypeError):
        await p.index_chunks([_chunk("c2", pmid=31234567)])

    # The prior chunk for doc-1 is still there — the delete never ran.
    assert len(await vs.get_chunks(["c1"], {"tenant_id": "public"})) == 1


@pytest.mark.asyncio
async def test_an_undeclared_key_still_passes_through():
    """The namespace is open by design: closing it would refuse, in bulk and at
    ingest, every corpus carrying a field the table has not caught up with."""
    vs = InMemoryVectorStore()
    await _pipeline(vs).index_chunks([_chunk("c1", grant="NIH-123", weird=[1, 2])])
    (got,) = await vs.get_chunks(["c1"], {"tenant_id": "public"})
    assert got.metadata["grant"] == "NIH-123"


@pytest.mark.asyncio
async def test_a_null_neighbour_link_is_accepted():
    """``chunkers.link_neighbors`` writes None into prev/next_chunk_id at a
    document's edges on purpose — the boundary must not read that as a bad type."""
    await _pipeline().index_chunks(
        [_chunk("c1", prev_chunk_id=None, next_chunk_id=None, chunk_index=0)]
    )


@pytest.mark.asyncio
async def test_a_chunk_with_no_tenant_stamp_is_refused():
    """``tenant_id`` is the row-level isolation boundary; an unstamped chunk is
    not one that can be retrieved safely."""
    bare = Chunk(id="c1", doc_id="d", content="x", embedding=[1.0, 0.0], metadata={})
    with pytest.raises(ChunkMetadataTypeError) as e:
        await _pipeline().index_chunks([bare])
    assert "tenant_id" in str(e.value)
