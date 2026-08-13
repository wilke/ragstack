"""Unit tests for the bulk-load throughput changes (#323).

Two behaviours, both opt-in and both about *not* doing redundant work:

* ``delete_prior=False`` skips the per-doc_id delete before upserting. Safe only
  when chunk ids cannot have moved (a replay from an unchanged embedding file);
  unsafe the moment boundaries shift, which is exactly what the default guards.
* the two store legs are gathered rather than awaited in sequence, so neither
  store idles while the other works.
"""
import asyncio

import pytest

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.models import Document
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore


class _FixedDocLoader:
    def __init__(self, doc_id: str, content: str) -> None:
        self.doc_id = doc_id
        self.content = content

    def load(self, source: str) -> list[Document]:
        return [Document(id=self.doc_id, content=self.content, source=source)]


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0] for t in texts]


class _CountingVectorStore(InMemoryVectorStore):
    """Counts delete calls so a test can assert they did or did not happen."""

    def __init__(self) -> None:
        super().__init__()
        self.deletes = 0

    async def delete(self, doc_id, tenant_id=None, **kw):
        self.deletes += 1
        return await super().delete(doc_id, tenant_id=tenant_id, **kw)


class _CountingTextIndex(InMemoryTextIndex):
    def __init__(self) -> None:
        super().__init__()
        self.deletes = 0

    async def delete(self, doc_id, tenant_id=None, **kw):
        self.deletes += 1
        return await super().delete(doc_id, tenant_id=tenant_id, **kw)


def _pipeline(vstore, tindex, content, *, delete_prior=True):
    return IngestionPipeline(
        loader=_FixedDocLoader("doc-1", content),
        chunker=RecursiveCharacterChunker(chunk_size=10, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=vstore,
        text_index=tindex,
        delete_prior=delete_prior,
    )


@pytest.mark.asyncio
async def test_delete_prior_runs_by_default():
    vstore, tindex = _CountingVectorStore(), _CountingTextIndex()
    await _pipeline(vstore, tindex, "abcdefghijklmnop").ingest("f.txt")
    assert vstore.deletes == 1 and tindex.deletes == 1


@pytest.mark.asyncio
async def test_delete_prior_skipped_when_disabled():
    vstore, tindex = _CountingVectorStore(), _CountingTextIndex()
    await _pipeline(vstore, tindex, "abcdefghijklmnop", delete_prior=False).ingest("f.txt")
    assert vstore.deletes == 0 and tindex.deletes == 0


@pytest.mark.asyncio
async def test_id_stable_replay_is_identical_with_and_without_delete_prior():
    """The claim that makes --no-delete-prior safe: when chunk ids cannot move,
    skipping the delete leaves byte-identical store contents. Deterministic ids
    mean the upsert overwrites in place, so the delete was pure round-trip cost."""
    content = "abcdefghijklmnopqrstuvwxyz0123456789"

    with_delete = InMemoryVectorStore()
    tindex_a = InMemoryTextIndex()
    await _pipeline(with_delete, tindex_a, content).ingest("f.txt")
    await _pipeline(with_delete, tindex_a, content).ingest("f.txt")  # replay

    without = InMemoryVectorStore()
    tindex_b = InMemoryTextIndex()
    await _pipeline(without, tindex_b, content).ingest("f.txt")
    await _pipeline(without, tindex_b, content, delete_prior=False).ingest("f.txt")

    a = sorted(c.id for c in with_delete._chunks)
    b = sorted(c.id for c in without._chunks)
    assert a == b, "id-stable replay diverged when delete-prior was skipped"
    assert len(a) == len(set(a)), "replay duplicated chunks"


@pytest.mark.asyncio
async def test_skipping_delete_prior_orphans_chunks_when_boundaries_move():
    """The reason it is opt-in and never inferred. An *edited* document yields
    shifted spans, hence new ids; without the delete the old chunks survive."""
    vstore, tindex = InMemoryVectorStore(), InMemoryTextIndex()
    await _pipeline(vstore, tindex, "abcdefghijklmnopqrstuvwxyz0123456789").ingest("f.txt")
    n_before = len(vstore._chunks)

    # Same doc id, different content -> different spans -> different chunk ids.
    await _pipeline(vstore, tindex, "ZZZ" + "abcdefghijklmnopqrstuvwxyz0123456789",
                    delete_prior=False).ingest("f.txt")
    assert len(vstore._chunks) > n_before, (
        "expected orphaned chunks — this is the failure mode --no-delete-prior "
        "accepts, and the reason the default must stay True"
    )


@pytest.mark.asyncio
async def test_both_legs_receive_the_same_chunks_when_gathered():
    vstore, tindex = InMemoryVectorStore(), InMemoryTextIndex()
    ids = await _pipeline(vstore, tindex, "abcdefghijklmnopqrstuvwxyz").ingest("f.txt")
    assert sorted(c.id for c in vstore._chunks) == sorted(ids)
    assert sorted(c.id for c in tindex._chunks) == sorted(ids)


@pytest.mark.asyncio
async def test_legs_run_concurrently_not_sequentially():
    """Each leg sleeps; gathered they take ~one sleep, serial they take ~two."""
    delay = 0.15

    class _SlowVector(InMemoryVectorStore):
        async def upsert(self, chunks):
            await asyncio.sleep(delay)
            return await super().upsert(chunks)

    class _SlowText(InMemoryTextIndex):
        async def index(self, chunks):
            await asyncio.sleep(delay)
            return await super().index(chunks)

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await _pipeline(_SlowVector(), _SlowText(), "abcdefghijklmnop").ingest("f.txt")
    elapsed = loop.time() - t0
    assert elapsed < delay * 1.8, f"legs appear serial: {elapsed:.3f}s for 2x{delay}s"


@pytest.mark.asyncio
async def test_a_failing_leg_still_awaits_its_sibling_before_raising():
    """A bare gather would propagate the first failure while the sibling kept
    running unsupervised — a load that raised could still be writing. Both must
    be complete when index_chunks raises."""
    finished = []

    class _Boom(InMemoryVectorStore):
        async def upsert(self, chunks):
            raise RuntimeError("vector leg down")

    class _SlowText(InMemoryTextIndex):
        async def index(self, chunks):
            await asyncio.sleep(0.1)
            finished.append(True)
            return await super().index(chunks)

    with pytest.raises(RuntimeError, match="vector leg down"):
        await _pipeline(_Boom(), _SlowText(), "abcdefghijklmnop").ingest("f.txt")

    assert finished, "sibling leg was still in flight when the error propagated"
