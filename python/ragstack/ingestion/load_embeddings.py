"""Single embedding-file **load** stage (ADR-0001 offline plane, #141).

The store-bound half of the decoupled bulk pipeline: read one JSONL embedding
file (produced by :func:`ragstack.ingestion.embed_shard.run_embed_shard`) and
push it into Qdrant/ES by reusing :meth:`IngestionPipeline.index_chunks` — the
*exact* delete-prior → upsert → index → KG logic of the coupled pipeline, no
fork. The embed work is already done; this stage only writes to the stores.

**Backpressure is not here yet.** #141's must-have is throttling these upserts on
Qdrant's live health (status/optimizer/segments). That lands as a
``BackpressuredVectorStore`` decorator wrapping the pipeline's ``vector_store`` —
``index_chunks`` calls ``vector_store.upsert`` and the decorator throttles
transparently, so *this* module needs no change when it arrives. Until then the
load runs at full rate (fine on an uncapped Qdrant; the reason #141 is tracked).

Never-raises, mirroring :func:`ragstack.ingestion.shard.run_shard`: a read or
index error becomes a ``status=failed`` receipt for just this file. Idempotent —
deterministic ids + upsert-only + per-doc delete-prior — so re-loading a file (an
engine retry or resume) overwrites in place.

**Replay** (#358, phase 2 of #353) — :func:`run_replay` is the restore path:
an ordered list of archive version directories (:mod:`ragstack.ingestion.archive`)
is verified IN FULL first — every file's sha256 and size, the vectors geometry,
and each manifest's ``spec_hash`` against the registry row's — and only then
replayed in order: a chunk version deletes each of its documents' prior chunks
and upserts both legs (deterministic ids, so a re-run after a crash converges);
a tombstone version deletes its doc ids from both legs. A verification failure
raises :class:`ReplayRefused` before the first write, so a restore can never
leave a collection half-built from a tampered or truncated archive. Streaming:
``read_version`` yields one ``(chunk, vector)`` at a time and this batches
``batch_size`` chunks per upsert — a version's vectors are never a list.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ragstack.ingestion import archive
from ragstack.ingestion.archive import ArchiveCorrupt, ArchiveError
from ragstack.ingestion.embedding_file import EmbeddingFileError, read_embedding_file
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import COMPLETED, FAILED, ShardReceipt
from ragstack.models import Chunk


async def run_load_file(
    pipeline: IngestionPipeline,
    embedding_path: str | Path,
    file_id: str,
    tenant: str | None = None,
) -> ShardReceipt:
    """Load one embedding file into the stores via ``pipeline.index_chunks``.

    ``tenant`` defaults to the file header's tenant (embed_source stamped it on
    the chunks); pass it only to override. Returns a receipt whose
    ``embedding_file`` echoes the input and ``n_chunks`` is what was indexed.
    """
    try:
        chunks, header = read_embedding_file(embedding_path)
    except EmbeddingFileError as e:
        return ShardReceipt(file_id, tenant or "", FAILED, error=f"read: {e}")
    except Exception as e:  # noqa: BLE001 — a missing/corrupt file fails just itself
        return ShardReceipt(file_id, tenant or "", FAILED,
                            error=f"read: {type(e).__name__}: {e}")

    tenant = tenant or header.get("tenant", "") or ""
    n_docs = len({c.doc_id for c in chunks})
    # Re-stamp the resolved tenant onto every chunk before indexing. index_chunks
    # scopes its delete-prior by ``tenant_id=tenant``, but the upsert/index scope
    # each point/doc by ``chunk.metadata["tenant_id"]`` (via ``tenant_of``). For an
    # explicit ``--tenant`` override that differs from the embed-time tenant baked
    # into the file, those would disagree — the delete would clear the override
    # tenant (a no-op) while the write landed under the old tenant, orphaning prior
    # data and mis-scoping the load. Stamping here (as embed_source does) keeps
    # delete, upsert, and point-id tenant consistent. A no-op when tenant already
    # matches the header (the default, non-override path).
    for c in chunks:
        c.metadata["tenant_id"] = tenant
    try:
        chunk_ids = await pipeline.index_chunks(chunks, tenant_id=tenant)
    except Exception as e:  # noqa: BLE001 — isolate the file; the engine retries
        return ShardReceipt(file_id, tenant, FAILED, n_docs=n_docs, n_chunks=len(chunks),
                            embedding_file=str(embedding_path),
                            error=f"{type(e).__name__}: {e}")

    return ShardReceipt(file_id, tenant, COMPLETED, n_docs=n_docs,
                        n_chunks=len(chunk_ids), chunk_ids=chunk_ids,
                        embedding_file=str(embedding_path))


# --------------------------------------------------------------------------- #
# Replay (#358): ordered archive versions -> the stores
# --------------------------------------------------------------------------- #

#: Upsert batch for a replay. 512 x 4096-d float32 is ~8 MB of vectors as
#: Python floats in flight — bounded regardless of the version's size.
REPLAY_BATCH = 512


class ReplayRefused(RuntimeError):
    """A replay was refused BEFORE any write. ``kind`` is the loader's stable
    marker — ``ArchiveCorrupt`` (sha256 / size / geometry / format failure) or
    ``SpecMismatch`` (a manifest whose ``spec_hash`` or ``collection_id``
    disagrees with the registry row) — which the API's restore watcher looks
    for in the failed submission to mark the collection ``lost``."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind


@dataclass
class ReplaySummary:
    """What a replay did, per version and in total (the load summary)."""

    versions: list[dict[str, Any]] = field(default_factory=list)
    n_versions: int = 0
    n_chunks: int = 0
    n_docs_deleted: int = 0
    status: str = COMPLETED
    error: str = ""
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "replay", "status": self.status, "error": self.error,
            "n_versions": self.n_versions, "n_chunks": self.n_chunks,
            "n_docs_deleted": self.n_docs_deleted, "seconds": round(self.seconds, 3),
            "versions": self.versions,
        }


def verify_replay(
    version_dirs: Sequence[str | Path],
    *,
    spec_hash: str,
    collection_id: str = "",
) -> list[dict[str, Any]]:
    """Verify EVERY version directory before a replay writes anything.

    Per directory: :func:`archive.verify_version` (format, every sha256 and
    byte size, the vectors geometry) and then the identity check —
    ``manifest.spec_hash`` must equal ``spec_hash`` (the registry row's; ADR-0002's
    build-spec guard applied to an archive the user can edit) and, when
    ``collection_id`` is given, ``manifest.collection_id`` must equal it. Returns
    the manifests in order. Raises :class:`ReplayRefused` on the first failure;
    an empty list is refused too (a restore of nothing is a bug, not a no-op).
    """
    if not version_dirs:
        raise ReplayRefused("ArchiveCorrupt", "no version directories to replay")
    if not spec_hash:
        raise ReplayRefused("SpecMismatch", "no registry spec_hash to verify the archive against")
    manifests: list[dict[str, Any]] = []
    for vdir in version_dirs:
        try:
            manifest = archive.verify_version(vdir)
        except ArchiveCorrupt as e:
            raise ReplayRefused("ArchiveCorrupt", str(e)) from e
        except ArchiveError as e:
            raise ReplayRefused("ArchiveCorrupt", f"{vdir}: {e}") from e
        got = str(manifest.get("spec_hash") or "")
        if got != spec_hash:
            raise ReplayRefused(
                "SpecMismatch",
                f"{vdir}: manifest spec_hash {got!r} != registry {spec_hash!r} — this "
                "archive was built with a different embedding model / dim / chunker "
                "than the collection it would be restored into (ADR-0002)",
            )
        if collection_id and str(manifest.get("collection_id") or "") != collection_id:
            raise ReplayRefused(
                "SpecMismatch",
                f"{vdir}: manifest collection_id {manifest.get('collection_id')!r} != "
                f"{collection_id!r}",
            )
        manifests.append(manifest)
    return manifests


def _chunk_from_record(rec: dict[str, Any], vector: Any) -> Chunk:
    """One archive record + its ``array('f')`` row -> a :class:`Chunk` with the
    embedding as the list the stores expect (the only per-row list, and it is
    freed with the batch)."""
    return Chunk(
        id=str(rec["id"]), doc_id=str(rec.get("doc_id", "")),
        content=str(rec.get("content", "")), embedding=vector.tolist(),
        metadata=dict(rec.get("metadata") or {}),
        start_char=int(rec.get("start_char", 0) or 0), end_char=int(rec.get("end_char", 0) or 0),
    )


async def _delete_docs(pipeline: IngestionPipeline, doc_ids: set[str], concurrency: int) -> int:
    """Delete ``doc_ids`` from both legs (and the graph leg when present),
    collection-wide (``tenant_id=None``): a restored collection is the owner's
    unit — the shared multi-tenant surface has no registry row and can never be
    dormant, so there is no other tenant's copy of a doc id to protect here."""
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(doc_id: str) -> None:
        async with sem:
            await pipeline.vector_store.delete(doc_id, tenant_id=None)
            await pipeline.text_index.delete(doc_id, tenant_id=None)
            if pipeline.graph_store is not None:
                await pipeline.graph_store.delete_by_doc(
                    doc_id, tenant_id=None, collection=pipeline.collection
                )

    ids = sorted(d for d in doc_ids if d)
    await asyncio.gather(*(_one(d) for d in ids))
    return len(ids)


async def run_replay(
    pipeline: IngestionPipeline,
    version_dirs: Sequence[str | Path],
    *,
    spec_hash: str,
    collection_id: str = "",
    batch_size: int = REPLAY_BATCH,
    delete_concurrency: int = 8,
    log: Any = None,
    manifests: list[dict[str, Any]] | None = None,
) -> ReplaySummary:
    """Restore a collection by replaying ``version_dirs`` in order.

    Verifies everything first (:func:`verify_replay`; nothing is written when
    it raises), then per version:

    * chunk version — collect its doc ids (a cheap pass over the chunks file),
      delete those documents' prior chunks from both legs, then stream the
      version in ``batch_size`` chunks through ``pipeline.index_chunks`` with
      the pipeline's delete-prior OFF (a document spanning two batches must
      not be deleted by its own second batch). Later versions therefore
      override earlier ones exactly as the live ingests did.
    * tombstone version — delete its doc ids from both legs.

    ``pipeline`` must have been built with ``delete_prior=False``; this
    function asserts it rather than silently double-deleting. ``manifests``:
    the list :func:`verify_replay` already returned for these directories
    (the CLI verifies BEFORE it creates the physical stores) — skips the
    second verification pass.
    """
    if getattr(pipeline, "_delete_prior", False):
        raise ValueError("run_replay needs a pipeline built with delete_prior=False")
    t0 = time.perf_counter()
    if manifests is None:
        manifests = verify_replay(version_dirs, spec_hash=spec_hash, collection_id=collection_id)
    elif len(manifests) != len(version_dirs):
        raise ValueError("manifests must correspond one-to-one to version_dirs")
    summary = ReplaySummary(n_versions=len(manifests))
    say = log if log is not None else (lambda *_a, **_k: None)
    try:
        for vdir, manifest in zip(version_dirs, manifests, strict=True):
            entry: dict[str, Any] = {"dir": str(vdir), "version": manifest.get("version")}
            if manifest.get("has_tombstone"):
                ids = archive.read_tombstone(vdir, manifest=manifest)
                n = await _delete_docs(pipeline, set(ids), delete_concurrency)
                entry.update({"kind": "tombstone", "n_docs_deleted": n})
                summary.n_docs_deleted += n
                say(f"[{vdir}] tombstone: deleted {n} doc(s)")
            else:
                doc_ids = set(archive.iter_doc_ids(vdir, manifest))
                n_deleted = await _delete_docs(pipeline, doc_ids, delete_concurrency)
                n_chunks = 0
                batch: list[Chunk] = []
                for rec, vec in archive.read_version(vdir, manifest=manifest):
                    batch.append(_chunk_from_record(rec, vec))
                    if len(batch) >= batch_size:
                        await pipeline.index_chunks(batch)
                        n_chunks += len(batch)
                        batch = []
                if batch:
                    await pipeline.index_chunks(batch)
                    n_chunks += len(batch)
                entry.update({"kind": "chunks", "n_chunks": n_chunks,
                              "n_docs": len(doc_ids), "n_docs_replaced": n_deleted})
                summary.n_chunks += n_chunks
                say(f"[{vdir}] chunks: replaced {len(doc_ids)} doc(s), upserted {n_chunks} chunk(s)")
            summary.versions.append(entry)
    except ArchiveError as e:
        # A file that verified but then failed to stream (a race with a writer
        # is the only way). Deliberately NOT reported under the refusal marker
        # and NOT exit 3: the archive verified, the run is merely partial, so
        # the collection goes back to `dormant` and the next access retries —
        # idempotent ids make the re-run converge.
        summary.status, summary.error = FAILED, f"replay failed mid-stream: {type(e).__name__}: {e}"
    summary.seconds = time.perf_counter() - t0
    return summary
