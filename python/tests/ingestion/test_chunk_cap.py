"""Per-collection chunk cap (#291, phase 3 of #201) — the ingestion-side rules.

Against in-memory stores and a fake embedder (nothing live), through the same
``ShardedIngestor.ingest_manifest`` the API's local path runs, the GoWe
worker's ``run_shard``, and the bulk ``load_embeddings`` CLI:

* a job that lands EXACTLY at the cap succeeds — the cap is inclusive;
* one chunk more refuses the WHOLE job with the four numbers (``live``,
  ``incoming``, ``cap``, ``would_fit``) and writes nothing to either leg;
* an exempt (curated) collection ignores the cap and never even counts;
* an explicit registry override wins over the derived default, both ways;
* a delete frees budget — the count is live, not a counter;
* a replay (restore) is never capped.

And the perf rule (#355), asserted directly: ONE ``count()`` per job.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ragstack.collection_store import CollectionSpec
from ragstack.ingestion.backends import LocalAsyncIORunner
from ragstack.ingestion.chunk_cap import (
    CHUNK_CAP_EXCEEDED,
    ChunkCapExceeded,
    check_chunk_cap,
    effective_chunk_cap,
    format_refusal,
    is_cap_refusal,
)
from ragstack.ingestion.load_embeddings import run_replay
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.manifest import Manifest, WorkItem
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.shard import run_shard
from ragstack.ingestion.sharded import ShardedIngestor
from ragstack.jobstore import FAILED, InMemoryJobStore
from ragstack.models import Chunk, Document
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from tests.archive_support import chunk_version, embed_file

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import load_embeddings as load_cli  # noqa: E402

# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #


class CountingVectorStore(InMemoryVectorStore):
    """InMemoryVectorStore that records every store call by method name."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: dict[str, int] = {}

    def _hit(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    async def count(self) -> int:
        self._hit("count")
        return await super().count()

    async def upsert(self, chunks):
        self._hit("upsert")
        await super().upsert(chunks)

    async def delete(self, doc_id, tenant_id=None):
        self._hit("delete")
        await super().delete(doc_id, tenant_id)


class CountingTextIndex(InMemoryTextIndex):
    def __init__(self) -> None:
        super().__init__()
        self.indexes = 0

    async def index(self, chunks):
        self.indexes += 1
        await super().index(chunks)


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class _WordsLoader:
    """``source`` is ``"<doc id>:<n chunks>"`` — no disk, so a 1,000-doc job
    is cheap and each document's chunk count is exact."""

    def load(self, source: str) -> list[Document]:
        doc_id, _, n = source.partition(":")
        if doc_id == "broken":
            raise ValueError("unreadable source")
        return [Document(id=doc_id, content=" ".join(f"w{i}" for i in range(int(n))),
                         source=source)]


class _OneChunkPerWord:
    def chunk(self, doc: Document) -> list[Chunk]:
        words = doc.content.split()
        return [
            Chunk(id=f"{doc.id}#{i}", doc_id=doc.id, content=w, metadata=dict(doc.metadata),
                  start_char=i, end_char=i + 1)
            for i, w in enumerate(words)
        ]


def _pipeline(vstore=None, tindex=None) -> IngestionPipeline:
    return IngestionPipeline(
        loader=_WordsLoader(), chunker=_OneChunkPerWord(), embedder=_FakeEmbedder(),
        vector_store=vstore if vstore is not None else CountingVectorStore(),
        text_index=tindex if tindex is not None else CountingTextIndex(),
    )


def _manifest(*sizes: int, prefix: str = "doc") -> Manifest:
    """One item per size: ``doc<i>`` producing ``size`` chunks."""
    return Manifest(items=[
        WorkItem(item_id=f"{prefix}{i}", source=f"{prefix}{i}:{n}") for i, n in enumerate(sizes)
    ])


def _ingestor(pipeline: IngestionPipeline, job_store=None) -> ShardedIngestor:
    return ShardedIngestor(pipeline, LocalAsyncIORunner(max_concurrency=4), shard_size=3,
                           job_store=job_store)


# --------------------------------------------------------------------------- #
# the pure rule
# --------------------------------------------------------------------------- #


def test_effective_cap_derivation_and_override():
    # derived: user-created gets the default; a curated corpus gets nothing
    assert effective_chunk_cap(override=None, user_created=True, default_cap=50_000) == 50_000
    assert effective_chunk_cap(override=None, user_created=False, default_cap=50_000) is None
    # the default switched off deployment-wide
    assert effective_chunk_cap(override=None, user_created=True, default_cap=0) is None
    # an explicit override wins over the derivation, in both directions
    assert effective_chunk_cap(override=7, user_created=False, default_cap=50_000) == 7
    assert effective_chunk_cap(override=7, user_created=True, default_cap=50_000) == 7
    assert effective_chunk_cap(override=0, user_created=True, default_cap=50_000) is None


def test_refusal_carries_the_four_numbers_and_the_label():
    e = ChunkCapExceeded(live=49_990, incoming=34, cap=50_000)
    assert (e.live, e.incoming, e.cap, e.would_fit) == (49_990, 34, 50_000, 10)
    assert e.job_error == CHUNK_CAP_EXCEEDED == "chunk_cap_exceeded"
    assert e.detail() == {"error": "chunk_cap_exceeded", "live": 49_990, "incoming": 34,
                          "cap": 50_000, "would_fit": 10}
    assert str(e) == format_refusal(49_990, 34, 50_000)
    assert str(e) == "chunk_cap_exceeded: live=49990 incoming=34 cap=50000 would_fit=10"
    assert is_cap_refusal(str(e)) and not is_cap_refusal("GoWeError") and not is_cap_refusal("")
    # would_fit never goes negative when the collection is already over the cap
    assert ChunkCapExceeded(live=60, incoming=1, cap=50).would_fit == 0


async def test_check_is_one_count_and_none_when_uncapped():
    vstore = CountingVectorStore()
    assert await check_chunk_cap(vstore, incoming=10, cap=None) is None
    assert vstore.calls == {}  # uncapped: the store is not even contacted
    assert await check_chunk_cap(vstore, incoming=10, cap=10) == 0
    assert vstore.calls == {"count": 1}
    with pytest.raises(ChunkCapExceeded):
        await check_chunk_cap(vstore, incoming=11, cap=10)


# --------------------------------------------------------------------------- #
# the API/local path: ShardedIngestor.ingest_manifest
# --------------------------------------------------------------------------- #


async def test_exactly_at_cap_succeeds_with_one_count():
    vstore, tindex = CountingVectorStore(), CountingTextIndex()
    results = await _ingestor(_pipeline(vstore, tindex)).ingest_manifest(
        _manifest(4, 3, 3), chunk_cap=10,
    )
    assert [r.status for r in results] == ["completed"] * 3
    assert await vstore.count() == 10 and tindex.indexes == 3
    # ONE count for the job (the assertion above added a second), one upsert
    # + one delete-prior per item — exactly the uncapped path plus the count.
    assert vstore.calls == {"count": 2, "upsert": 3, "delete": 3}


async def test_one_chunk_over_refuses_the_whole_job_and_writes_nothing():
    vstore, tindex = CountingVectorStore(), CountingTextIndex()
    job_store = InMemoryJobStore()
    job = await job_store.create(source="dir", tenant_id="t")
    with pytest.raises(ChunkCapExceeded) as ei:
        await _ingestor(_pipeline(vstore, tindex), job_store).ingest_manifest(
            _manifest(4, 3, 4), job_id=job.job_id, chunk_cap=10,
        )
    e = ei.value
    assert (e.live, e.incoming, e.cap, e.would_fit) == (0, 11, 10, 10)
    # nothing written to either leg — not the items that would have fit either
    assert vstore.calls == {"count": 1} and tindex.indexes == 0
    assert vstore._chunks == [] and tindex._chunks == []
    # every item is checkpointed failed under the formatted refusal
    counts = await job_store.item_counts(job.job_id)
    assert counts == {"pending": 0, "completed": 0, "failed": 3}
    items = job_store._items[job.job_id]
    assert {i.error for i in items.values()} == {str(e)}
    assert all(is_cap_refusal(i.error) for i in items.values())


async def test_exempt_corpus_ignores_the_cap_and_never_counts():
    vstore = CountingVectorStore()
    results = await _ingestor(_pipeline(vstore)).ingest_manifest(_manifest(40, 30, 40))
    assert [r.status for r in results] == ["completed"] * 3
    assert len(vstore._chunks) == 110
    assert "count" not in vstore.calls  # uncapped: no store round-trip added at all


async def test_override_wins_over_the_default():
    # A curated corpus (not user-created) with an explicit override of 5 IS capped...
    cap = effective_chunk_cap(override=5, user_created=False, default_cap=50_000)
    vstore = CountingVectorStore()
    with pytest.raises(ChunkCapExceeded) as ei:
        await _ingestor(_pipeline(vstore)).ingest_manifest(_manifest(3, 3), chunk_cap=cap)
    assert (ei.value.cap, ei.value.incoming, ei.value.would_fit) == (5, 6, 5)
    assert vstore._chunks == []
    # ...and a user-created one with override 0 is exempt despite the default.
    cap = effective_chunk_cap(override=0, user_created=True, default_cap=5)
    results = await _ingestor(_pipeline(vstore)).ingest_manifest(_manifest(3, 3), chunk_cap=cap)
    assert [r.status for r in results] == ["completed"] * 2 and len(vstore._chunks) == 6


async def test_delete_frees_budget_because_the_count_is_live():
    vstore = CountingVectorStore()
    ing = _ingestor(_pipeline(vstore))
    await ing.ingest_manifest(_manifest(5, 5), chunk_cap=10)  # at the cap: 10 live
    with pytest.raises(ChunkCapExceeded) as ei:
        await ing.ingest_manifest(_manifest(2, prefix="new"), chunk_cap=10)
    assert (ei.value.live, ei.value.incoming, ei.value.would_fit) == (10, 2, 0)
    # A delete (the DELETE /v1/documents path) frees exactly its chunks...
    await vstore.delete("doc1")
    assert await vstore.count() == 5
    # ...and the very same job now fits: the figure is read from the store
    # each time, never accumulated in a counter that a delete would not touch.
    results = await ing.ingest_manifest(_manifest(2, prefix="new"), chunk_cap=10)
    assert results[0].status == "completed" and await vstore.count() == 7


async def test_re_ingest_of_a_document_at_cap_is_counted_conservatively():
    """A byte-identical re-ingest at the cap is refused even though delete-prior
    would make it net-zero: ``incoming`` is what the job would write, and the
    refusal is the honest, conservative reading of "refuse the whole batch"."""
    vstore = CountingVectorStore()
    ing = _ingestor(_pipeline(vstore))
    await ing.ingest_manifest(_manifest(10), chunk_cap=10)
    with pytest.raises(ChunkCapExceeded) as ei:
        await ing.ingest_manifest(_manifest(10), chunk_cap=10)
    assert (ei.value.live, ei.value.incoming) == (10, 10)


async def test_a_source_that_fails_to_load_is_its_own_failure_not_the_jobs():
    vstore = CountingVectorStore()
    manifest = Manifest(items=[WorkItem(item_id="broken", source="broken:3"),
                               WorkItem(item_id="doc0", source="doc0:3")])
    results = await _ingestor(_pipeline(vstore)).ingest_manifest(manifest, chunk_cap=10)
    assert {r.item_id: r.status for r in results} == {"broken": FAILED, "doc0": "completed"}
    assert results[0].error == "ValueError"  # the per-item label, as before
    assert len(vstore._chunks) == 3 and vstore.calls["count"] == 1


async def test_resume_counts_only_the_remaining_items():
    vstore = CountingVectorStore()
    job_store = InMemoryJobStore()
    job = await job_store.create(source="dir", tenant_id="t")
    ing = _ingestor(_pipeline(vstore), job_store)
    await ing.ingest_manifest(_manifest(6, 4), job_id=job.job_id, chunk_cap=10)  # 10 live
    # Re-running the same job: both items are already completed, so incoming
    # is 0 and the (full) collection is not refused.
    results = await ing.ingest_manifest(_manifest(6, 4), job_id=job.job_id, chunk_cap=10)
    assert results == [] and vstore.calls["count"] == 2


async def test_refused_job_does_not_hold_the_whole_corpus_in_memory():
    """Once a job is known to be refused the prepare pass stops RETAINING chunk
    text (it keeps counting, so ``incoming`` stays exact)."""
    vstore = CountingVectorStore()
    ing = _ingestor(_pipeline(vstore))
    seen: list[int] = []
    real = ing._pipeline.prepare_source

    async def spy(source):
        p = await real(source)
        seen.append(len(p.chunks))
        return p

    ing._pipeline.prepare_source = spy  # type: ignore[method-assign]
    with pytest.raises(ChunkCapExceeded) as ei:
        await ing.ingest_manifest(_manifest(4, 4, 4, 4), chunk_cap=5)
    assert ei.value.incoming == 16
    assert seen == [4, 4, 4, 4]  # every source was still sized...


# --------------------------------------------------------------------------- #
# the GoWe worker: run_shard --max-chunks
# --------------------------------------------------------------------------- #


def _jsonl_shard(tmp_path: Path, name: str, n_docs: int, words: int) -> str:
    p = tmp_path / name
    p.write_text("\n".join(
        json.dumps({"text": " ".join(f"w{i}" for i in range(words)), "path": f"/c/{name}-{i}.txt"})
        for i in range(n_docs)
    ), encoding="utf-8")
    return str(p)


def _shard_pipeline(vstore) -> IngestionPipeline:
    return IngestionPipeline(loader=JsonlLoader(), chunker=_OneChunkPerWord(),
                             embedder=_FakeEmbedder(), vector_store=vstore,
                             text_index=CountingTextIndex())


async def test_run_shard_refuses_at_cap_before_the_embedding_file_and_the_stores(tmp_path):
    vstore = CountingVectorStore()
    shard = _jsonl_shard(tmp_path, "s0.jsonl", n_docs=2, words=3)  # 6 chunks
    emb = tmp_path / "s0.emb.jsonl"
    r = await run_shard(_shard_pipeline(vstore), shard, "public", "s0", embedding_file=emb,
                        max_chunks=5)
    assert r.status == FAILED and r.n_docs == 2 and r.n_chunks == 0
    assert r.error == format_refusal(0, 6, 5) and is_cap_refusal(r.error)
    assert not emb.exists() and vstore._chunks == []
    assert vstore.calls == {"count": 1}
    # exactly at the cap: written, one count
    ok = await run_shard(_shard_pipeline(vstore), shard, "public", "s0", embedding_file=emb,
                         max_chunks=6)
    assert ok.status == "completed" and ok.n_chunks == 6 and emb.exists()
    assert vstore.calls == {"count": 2, "delete": 2, "upsert": 1}
    # 0 = unlimited: the coupled path, no count at all
    free = CountingVectorStore()
    ok2 = await run_shard(_shard_pipeline(free), shard, "public", "s0", max_chunks=0)
    assert ok2.status == "completed" and "count" not in free.calls


# --------------------------------------------------------------------------- #
# the bulk loader: load_embeddings.py (capped) vs --replay (never capped)
# --------------------------------------------------------------------------- #


def _spec(**over) -> CollectionSpec:
    base: dict = {"id": "lib", "collection": "lib_phys", "embedding_model": "m",
                  "embedding_model_dim": 16, "chunk_method": "fixed",
                  "owner": "bvbrc:alice@patricbrc.org"}
    base.update(over)
    return CollectionSpec(**base)


def _target(spec: CollectionSpec):
    return SimpleNamespace(collection_id=spec.id, spec=spec, qdrant_url="", collection=spec.collection,
                           es_index=spec.es_index())


@pytest.fixture
def cap_settings(monkeypatch):
    from ragstack.config import settings

    monkeypatch.setattr(settings, "max_chunks_per_collection", 10)
    monkeypatch.setattr(settings, "acl_backfill_owner", "legacy:admin")
    monkeypatch.setattr(settings, "admin_subjects", ["bvbrc:root@patricbrc.org"])
    return settings


def test_cli_cap_derivation(cap_settings):
    assert load_cli._chunk_cap_for(None) is None                       # memory backend
    assert load_cli._chunk_cap_for(_target(_spec())) == 10             # user-created
    assert load_cli._chunk_cap_for(_target(_spec(owner=""))) is None   # curated
    assert load_cli._chunk_cap_for(_target(_spec(owner="legacy:admin"))) is None
    assert load_cli._chunk_cap_for(_target(_spec(owner="bvbrc:root@patricbrc.org"))) is None
    assert load_cli._chunk_cap_for(_target(_spec(owner="", max_chunks=3))) == 3  # override
    assert load_cli._chunk_cap_for(_target(_spec(max_chunks=0))) is None          # exempt


async def test_bulk_load_is_refused_whole_before_the_first_read(tmp_path, cap_settings, capsys):
    f1, f2 = tmp_path / "a.emb.jsonl", tmp_path / "b.emb.jsonl"
    embed_file(f1, 6)
    embed_file(f2, 6, start=6)
    out = tmp_path / "summary.json"
    args = load_cli.parse_args([str(f1), str(f2), "--vector-backend", "memory",
                                "--text-backend", "memory", "--out", str(out)])
    vstore = CountingVectorStore()
    real = load_cli._build_pipeline

    async def with_counting(a, target=None):
        p = await real(a, target)
        p.vector_store = vstore
        return p

    load_cli._build_pipeline = with_counting
    try:
        rc = await load_cli.amain(args, _target(_spec()))
    finally:
        load_cli._build_pipeline = real
    assert rc == 1
    assert vstore._chunks == [] and vstore.calls == {"count": 1}
    summary = json.loads(out.read_text())
    assert summary["chunk_cap"] == {"error": "chunk_cap_exceeded", "live": 0, "incoming": 12,
                                    "cap": 10, "would_fit": 10}
    assert summary["n_shards_failed"] == 2 and summary["n_chunks"] == 0
    assert capsys.readouterr().err.startswith("chunk_cap_exceeded: live=0 incoming=12 cap=10")

    # The override lifts it: the same files load in full.
    args = load_cli.parse_args([str(f1), str(f2), "--vector-backend", "memory",
                                "--text-backend", "memory", "--out", str(out)])
    rc = await load_cli.amain(args, _target(_spec(max_chunks=0)))
    assert rc == 0 and json.loads(out.read_text())["n_chunks"] == 12


async def test_replay_is_never_capped(tmp_path, cap_settings):
    """A restore re-admits what was already admitted: no cap, no count — the
    library path and the CLI's --replay both, even for a user-created entry
    whose override would refuse any live ingest."""
    spec = _spec(max_chunks=1)
    vdir, recs = chunk_version(tmp_path, 1, 12, spec_hash=spec.spec_hash())
    vstore = CountingVectorStore()
    pipeline = IngestionPipeline(loader=JsonlLoader(), chunker=_OneChunkPerWord(),
                                 embedder=_FakeEmbedder(), vector_store=vstore,
                                 text_index=CountingTextIndex(), delete_prior=False)
    summary = await run_replay(pipeline, [vdir], spec_hash=spec.spec_hash(), collection_id="lib")
    assert summary.status == "completed" and summary.n_chunks == 12
    assert "count" not in vstore.calls and len(vstore._chunks) == 12

    out = tmp_path / "summary.json"
    args = load_cli.parse_args(["--replay", str(vdir), "--spec-hash", spec.spec_hash(),
                                "--vector-backend", "memory", "--text-backend", "memory",
                                "--out", str(out)])
    rc = await load_cli.amain(args, _target(spec))
    assert rc == 0 and json.loads(out.read_text())["n_chunks"] == 12
