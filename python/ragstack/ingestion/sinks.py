"""Pluggable terminal sinks for the bulk ingest pipeline (issue #141).

The bulk ingester (``scripts/ingest_jsonl.py``) runs *load → enrich → chunk →
embed → link-neighbors → **write***. This module isolates that final **write**
step behind a small :class:`BatchSink` protocol so the same producer / chunker /
embedder / checkpoint machinery can drive two very different back ends:

- :class:`QdrantSink` — the default coupled behaviour: upsert to Qdrant (+ ES),
  optionally prune orphans under ``--replace``.
- :class:`FileSink` — the *decoupled* embed-to-file stage: append each embedded
  chunk to sharded, streaming JSONL so the GPU fleet runs at full speed and is
  never blocked by Qdrant. A separate agent (``scripts/qdrant_ingest_agent.py``)
  later drains those shards into Qdrant under backpressure.

Why decouple: on the capped Qdrant (``OPTIMIZER_CPU_BUDGET=12``, forced while
``vm.max_map_count`` can't be raised — see
``/rag/documents/vma-exhaustion-incident-2026-07-04.md``) sustained inline
upserts drop connections (``ResponseHandlingException``, #77) and collapse the
ingest. Writing vectors to files first removes Qdrant from the hot path.

File format — **sharded, streaming JSONL** (one ``Chunk`` per line, incl. its
embedding), gzip by default. One object per line keeps memory constant (never a
whole file in RAM), and many records per file + many files per run gives the
drain agent (and parallel embed shards) natural, coordination-free parallelism.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ragstack.models import Chunk

__all__ = [
    "BatchSink",
    "QdrantSink",
    "FileSink",
    "iter_embedded_records",
    "read_manifests",
    "list_shards",
    "MANIFEST_GLOB",
]

MANIFEST_GLOB = "manifest-*.json"
_SHARD_SUFFIXES = (".jsonl.gz", ".jsonl")


@runtime_checkable
class BatchSink(Protocol):
    """Terminal write for one already-embedded, neighbor-linked batch.

    ``kept`` chunks all have ``.embedding`` set (unembeddable ones were dropped
    upstream). ``write`` must be idempotent so ``--batch-retries`` can re-run a
    batch after a transient failure without duplicating or losing data."""

    async def write(self, kept: list[Chunk]) -> None: ...

    async def aclose(self) -> None: ...


class QdrantSink:
    """Upsert to Qdrant (+ optional Elasticsearch), with optional orphan prune.

    Verbatim behaviour of the original coupled ``_store_batch`` tail: upsert
    FIRST (deterministic ids overwrite in place; a failure never deletes), then
    only under ``--replace`` prune an EDITED doc's stale points by id with
    bounded concurrency."""

    def __init__(
        self,
        store: Any,
        text_index: Any | None,
        tenant: str,
        *,
        replace: bool = False,
        delete_concurrency: int = 4,
    ) -> None:
        self._store = store
        self._text_index = text_index
        self._tenant = tenant
        self._replace = replace
        self._delete_sem = asyncio.Semaphore(max(1, delete_concurrency))

    async def write(self, kept: list[Chunk]) -> None:
        if not kept:
            return
        await self._store.upsert(kept)
        if self._text_index is not None:
            await self._text_index.index(kept)
        if not self._replace:
            return
        by_doc: dict[str, set[str]] = {}
        for c in kept:
            by_doc.setdefault(c.doc_id, set()).add(c.id)
        for doc_id, keep in by_doc.items():
            async with self._delete_sem:
                await self._store.delete_except(doc_id, keep, tenant_id=self._tenant)
                if self._text_index is not None:
                    await self._text_index.delete_except(
                        doc_id, keep, tenant_id=self._tenant
                    )

    async def aclose(self) -> None:
        # Own the text-index lifecycle so the caller doesn't double-close it.
        if self._text_index is not None and hasattr(self._text_index, "close"):
            await self._text_index.close()


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "run"


class FileSink:
    """Append embedded chunks to sharded, streaming JSONL (the embed-to-file stage).

    Each record is ``Chunk.model_dump()`` (id / doc_id / content / offsets /
    metadata / embedding) as one JSON line. Shards roll to a new file every
    ``shard_size`` records: ``{run_id}-000.jsonl.gz``, ``{run_id}-001…``. A
    ``manifest-{run_id}.json`` sidecar records the embedding metadata (model /
    dim / chunk-method / target collection / tenant) and the shard list so the
    drain agent can default the collection + pre-check the vector dimension.

    ``run_id`` MUST be unique per concurrently-writing process (the loader passes
    its shard index) so parallel embed shards don't clobber each other's files or
    manifest. Writes are serialized by an internal lock, so a ``--concurrency>1``
    worker pool can share one sink safely; ``write`` flushes before returning so a
    crash loses at most the in-flight batch (which the idempotent drain re-applies).
    """

    def __init__(
        self,
        out_dir: Path,
        run_id: str,
        *,
        shard_size: int = 500_000,
        compress: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self._dir = Path(out_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._run_id = _slug(run_id)
        self._shard_size = max(1, shard_size)
        self._compress = compress
        self._meta = dict(meta or {})
        self._lock = asyncio.Lock()
        self._shard_idx = -1
        self._in_shard = 0
        self._total = 0
        self._shards: list[str] = []
        self._fh: Any | None = None

    @property
    def suffix(self) -> str:
        return ".jsonl.gz" if self._compress else ".jsonl"

    def _open_next_shard(self) -> None:
        if self._fh is not None:
            self._fh.close()
        self._shard_idx += 1
        self._in_shard = 0
        name = f"{self._run_id}-{self._shard_idx:03d}{self.suffix}"
        self._shards.append(name)
        path = self._dir / name
        # Text mode: gzip.open(...,"wt") streams line-by-line just like a plain
        # file, so neither writer nor reader ever holds a whole shard in memory.
        self._fh = gzip.open(path, "wt", encoding="utf-8") if self._compress \
            else open(path, "w", encoding="utf-8")
        self._write_manifest()

    def _write_manifest(self) -> None:
        manifest = {
            **self._meta,
            "run_id": self._run_id,
            "compress": self._compress,
            "shard_size": self._shard_size,
            "shards": list(self._shards),
            "record_count": self._total,
        }
        tmp = self._dir / f"manifest-{self._run_id}.json.tmp"
        tmp.write_text(json.dumps(manifest, indent=2))
        tmp.replace(self._dir / f"manifest-{self._run_id}.json")

    async def write(self, kept: list[Chunk]) -> None:
        if not kept:
            return
        async with self._lock:
            for c in kept:
                if self._fh is None or self._in_shard >= self._shard_size:
                    self._open_next_shard()
                self._fh.write(json.dumps(c.model_dump()) + "\n")
                self._in_shard += 1
                self._total += 1
            self._fh.flush()
            # Keep record_count current so a resumed/monitoring drain sees progress.
            self._write_manifest()

    async def aclose(self) -> None:
        async with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
            self._write_manifest()


def iter_embedded_records(path: Path) -> Iterator[Chunk]:
    """Stream one embedded ``Chunk`` per line from a (optionally gzip) JSONL shard.

    Constant memory: yields line-by-line, never materializing the whole shard.
    Malformed lines are skipped (a partial trailing line from a crashed writer is
    harmless — the drain is idempotent and a resume/re-run re-reads cleanly)."""
    path = Path(path)
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield Chunk.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                continue


def list_shards(embed_dir: Path) -> list[Path]:
    """All shard files under ``embed_dir``, sorted for deterministic drain order."""
    embed_dir = Path(embed_dir)
    shards: list[Path] = []
    for p in embed_dir.iterdir():
        if p.is_file() and any(p.name.endswith(s) for s in _SHARD_SUFFIXES):
            shards.append(p)
    return sorted(shards, key=lambda p: p.name)


def read_manifests(embed_dir: Path) -> list[dict[str, Any]]:
    """Read every ``manifest-*.json`` in ``embed_dir`` (one per embed-shard run)."""
    embed_dir = Path(embed_dir)
    out: list[dict[str, Any]] = []
    for p in sorted(embed_dir.glob(MANIFEST_GLOB)):
        try:
            out.append(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return out
