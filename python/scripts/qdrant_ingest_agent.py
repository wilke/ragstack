#!/usr/bin/env python
"""Drain pre-embedded JSONL shards into Qdrant under backpressure (issue #141).

The companion to ``ingest_jsonl.py --embed-out``. That stage embeds a corpus to
sharded JSONL at full GPU speed; this agent reads those shards and upserts them
into Qdrant as a **single, gentle writer** that throttles on collection health, so
sustained writes never trigger the ``ResponseHandlingException`` connection drops
(#77) that collapsed the semantic ingest on the capped Qdrant.

Why a *single* writer: parallelism belongs to the embed stage (many shard files,
many loaders). The drain is deliberately one throttled throat — concurrent writers
are exactly what overwhelms the capped Qdrant. ``--max-inflight`` (default 2) keeps
a tiny pipeline for throughput without a burst.

Backpressure: before each upsert the agent polls ``QdrantVectorStore.collection_health``
and proceeds only while the collection is ``green``, its optimizer is idle, and the
unindexed backlog (points accepted but not yet HNSW-indexed) and segment count are
below the configured ceilings. Otherwise it sleeps ``--poll-interval`` and re-checks.

Idempotent + resumable: point ids are deterministic uuid5 and every write is
upsert-only (no delete), so replaying a shard is a no-op. A checkpoint records
completed shards + the offset within the partial one, so ``--resume`` skips drained
work; correctness never depends on it.

SCOPE BOUNDARY — this agent does NOT manage indexing. It never sets
``indexing_threshold=0`` and never triggers a bulk index rebuild: that deferred
all-at-once build under an uncapped optimizer is exactly the VMA-exhaustion crash
(#140, ``/rag/documents/vma-exhaustion-incident-2026-07-04.md``). Qdrant stays
capped and indexes incrementally at its existing non-zero threshold (proven safe:
``tok256`` ran at 10000 throughout, never crashed). Backpressure fixes *upserts*
only; the index-build risk is #140's separate track.

Usage::

    cd python && . /rag/bin/activate
    python scripts/qdrant_ingest_agent.py --embed-in /rag/cache/embed/semantic \\
        --collection ragstack_sfr_semantic \\
        --qdrant-url http://localhost:6333 \\
        --batch-size 256 --max-inflight 2 --max-unindexed 100000 \\
        --resume --delete-shards
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ragstack.ingestion.retry import is_transient_error, retry_delay
from ragstack.ingestion.sinks import iter_embedded_records, list_shards, read_manifests
from ragstack.models import Chunk
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.qdrant import QdrantVectorStore


@dataclass
class Batch:
    """One unit of drain work: ``chunks`` from ``shard``, ending at cumulative
    within-shard record offset ``end_offset``. ``is_shard_end`` marks the final
    (possibly empty) batch of a shard, which completes it (and, with
    ``--delete-shards``, unlinks the file)."""

    seq: int
    shard: str
    end_offset: int
    is_shard_end: bool
    chunks: list[Chunk]


def iter_shard_batches(
    shards: list[Path],
    batch_size: int,
    completed_shards: set[str],
    partial: dict[str, Any] | None,
) -> Iterator[Batch]:
    """Stream ``Batch``es across all shards in deterministic order.

    Skips fully-completed shards and, for the one partially-drained shard, the
    first ``partial['offset']`` records — so a ``--resume`` re-reads only undrained
    work. Constant memory: reads each shard line-by-line. A global monotonic ``seq``
    lets the drainer advance a contiguous checkpoint frontier under ``--max-inflight``."""
    seq = 0
    for shard in shards:
        name = shard.name
        if name in completed_shards:
            continue
        skip = partial["offset"] if (partial and partial.get("shard") == name) else 0
        recs = iter_embedded_records(shard)
        idx = 0
        for _ in range(skip):
            next(recs, None)
            idx += 1
        buf: list[Chunk] = []
        for c in recs:
            buf.append(c)
            idx += 1
            if len(buf) >= batch_size:
                yield Batch(seq, name, idx, False, buf)
                seq += 1
                buf = []
        # Final batch marks the shard complete even when empty (exact-multiple or
        # already-drained shard), so completion + delete + checkpoint still fire.
        yield Batch(seq, name, idx, True, buf)
        seq += 1


class HealthGate:
    """Backpressure gate over ``QdrantVectorStore.collection_health``.

    ``acquire`` returns immediately on a cached-healthy reading (fast path — one
    poll doesn't run per batch when Qdrant is comfortable); otherwise it polls every
    ``poll_interval`` seconds until healthy, logging the tripping signal on entry and
    the recovery on exit."""

    def __init__(
        self,
        store: Any,
        *,
        poll_interval: float = 2.0,
        max_unindexed: int = 100_000,
        max_segments: int = 0,
        log: Any = sys.stderr,
    ) -> None:
        self._store = store
        self._poll_interval = max(0.0, poll_interval)
        self._max_unindexed = max_unindexed
        self._max_segments = max_segments
        self._log = log
        self._cached: Any = None
        self._cached_at = -1e18
        self.polls = 0  # visible for tests / observability

    async def _poll(self, *, use_cache: bool) -> Any:
        now = time.monotonic()
        if use_cache and self._cached is not None and now - self._cached_at < self._poll_interval:
            return self._cached
        self._cached = await self._store.collection_health()
        self._cached_at = now
        self.polls += 1
        return self._cached

    def _blocked_reason(self, h: Any) -> str | None:
        if h.status != "green":
            return f"status={h.status}"
        if not h.optimizer_ok:
            return "optimizer busy"
        if h.unindexed > self._max_unindexed:
            return f"unindexed backlog {h.unindexed} > {self._max_unindexed}"
        if self._max_segments > 0 and h.segments_count > self._max_segments:
            return f"segments {h.segments_count} > {self._max_segments}"
        return None

    async def acquire(self) -> None:
        h = await self._poll(use_cache=True)
        reason = self._blocked_reason(h)
        if reason is None:
            return
        print(f"  backpressure: throttling — {reason} (poll every "
              f"{self._poll_interval}s)", file=self._log)
        waited = 0.0
        while True:
            await asyncio.sleep(self._poll_interval)
            waited += self._poll_interval
            h = await self._poll(use_cache=False)
            reason = self._blocked_reason(h)
            if reason is None:
                print(f"  backpressure: resumed after {waited:.0f}s "
                      f"(status={h.status}, unindexed={h.unindexed})", file=self._log)
                return


async def _upsert_with_retries(
    store: Any, text_index: Any | None, chunks: list[Chunk], retries: int
) -> None:
    """Upsert one batch, retrying only TRANSIENT faults (a capped-Qdrant upsert
    drop self-heals; a 4xx surfaces). Idempotent, so retry can't duplicate."""
    attempt = 0
    while True:
        try:
            await store.upsert(chunks)
            if text_index is not None:
                await text_index.index(chunks)
            return
        except Exception as e:  # noqa: BLE001 — reclassified below
            if attempt < retries and is_transient_error(e):
                attempt += 1
                delay = retry_delay(attempt)
                print(f"  upsert transient {type(e).__name__} "
                      f"(retry {attempt}/{retries} in {delay:.1f}s): {e}", file=sys.stderr)
                await asyncio.sleep(delay)
                continue
            raise


class _Checkpoint:
    """Contiguous-frontier resume state: completed shards + the partial one's offset.

    Advanced strictly in ``seq`` order so a crash never records a shard/offset past
    an unfinished batch. Written atomically after every advance."""

    def __init__(
        self,
        path: Path,
        completed_shards: set[str],
        partial: dict[str, Any] | None,
        shard_by_name: dict[str, Path],
        delete_shards: bool,
    ) -> None:
        self._path = path
        self.completed_shards = completed_shards
        self.partial = partial
        self._shard_by_name = shard_by_name
        self._delete_shards = delete_shards

    def advance(self, b: Batch) -> None:
        if b.is_shard_end:
            self.completed_shards.add(b.shard)
            self.partial = None
            if self._delete_shards:
                p = self._shard_by_name.get(b.shard)
                if p is not None and p.exists():
                    p.unlink()
        else:
            self.partial = {"shard": b.shard, "offset": b.end_offset}
        self._write()

    def _write(self) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps({
            "completed_shards": sorted(self.completed_shards),
            "partial": self.partial,
        }))
        tmp.replace(self._path)


def _load_checkpoint(path: Path, resume: bool) -> tuple[set[str], dict[str, Any] | None]:
    if not resume or not path.exists():
        return set(), None
    try:
        data = json.loads(path.read_text())
        return set(data.get("completed_shards", [])), data.get("partial")
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return set(), None


async def drain(
    shards: list[Path],
    store: Any,
    text_index: Any | None,
    gate: HealthGate,
    *,
    batch_size: int,
    max_inflight: int,
    checkpoint_path: Path,
    resume: bool,
    delete_shards: bool,
    batch_retries: int,
) -> dict[str, int]:
    """Drain all shards into ``store`` under ``gate`` backpressure.

    Batches run under a ``max_inflight`` semaphore; completions advance a contiguous
    ``seq`` frontier that drives the checkpoint (so an out-of-order finish above a gap
    never records a not-yet-drained offset). Returns ``{shards, batches, chunks}``."""
    completed_shards, partial = _load_checkpoint(checkpoint_path, resume)
    if completed_shards or partial:
        print(f"resuming: {len(completed_shards)} shard(s) done"
              + (f", partial {partial['shard']}@{partial['offset']}" if partial else ""),
              file=sys.stderr)
    shard_by_name = {p.name: p for p in shards}
    cp = _Checkpoint(checkpoint_path, completed_shards, partial, shard_by_name, delete_shards)

    sem = asyncio.Semaphore(max(1, max_inflight))
    lock = asyncio.Lock()
    completed: dict[int, Batch] = {}
    next_seq = 0
    stats = {"shards": 0, "batches": 0, "chunks": 0}
    tasks: set[asyncio.Task] = set()

    async def submit(b: Batch) -> None:
        nonlocal next_seq
        async with sem:
            await gate.acquire()  # BACKPRESSURE — block until Qdrant can take a batch
            if b.chunks:
                await _upsert_with_retries(store, text_index, b.chunks, batch_retries)
        async with lock:
            completed[b.seq] = b
            while next_seq in completed:
                bb = completed.pop(next_seq)
                cp.advance(bb)  # updates + persists checkpoint (+ deletes finished shard)
                stats["batches"] += 1
                stats["chunks"] += len(bb.chunks)
                if bb.is_shard_end:
                    stats["shards"] += 1
                    print(f"  drained shard {bb.shard} "
                          f"({stats['chunks']} chunks total)", file=sys.stderr)
                next_seq += 1

    def _reap() -> None:
        for t in list(tasks):
            if t.done():
                exc = t.exception()
                if exc is not None:
                    raise exc

    for b in iter_shard_batches(shards, batch_size, completed_shards, partial):
        t = asyncio.create_task(submit(b))
        tasks.add(t)
        t.add_done_callback(tasks.discard)
        # Bound outstanding tasks so we don't buffer every shard into memory; the
        # semaphore bounds concurrency, this bounds look-ahead.
        while len(tasks) >= max(1, max_inflight) * 2:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            _reap()
    if tasks:
        await asyncio.gather(*tasks)
    return stats


def _resolve_dim(args: argparse.Namespace, manifests: list[dict], shards: list[Path]) -> int:
    dims = {int(m["dim"]) for m in manifests if m.get("dim")}
    if len(dims) > 1:
        raise SystemExit(f"embed manifests disagree on vector dim: {sorted(dims)}")
    if dims:
        return next(iter(dims))
    # No manifest — peek the first record's embedding length.
    for c in iter_embedded_records(shards[0]):
        if c.embedding:
            return len(c.embedding)
    raise SystemExit("could not infer embedding dim (no manifest, no embedded records)")


def _resolve_collection(args: argparse.Namespace, manifests: list[dict]) -> str:
    if args.collection:
        return args.collection
    colls = {m["collection"] for m in manifests if m.get("collection")}
    if len(colls) == 1:
        return next(iter(colls))
    raise SystemExit(
        f"--collection is required (embed manifests offer: {sorted(colls) or 'none'})"
    )


async def run_agent(args: argparse.Namespace) -> None:
    embed_in = args.embed_in
    shards = list_shards(embed_in)
    if not shards:
        raise SystemExit(f"no embed shards (*.jsonl[.gz]) under {embed_in}")
    manifests = read_manifests(embed_in)
    dim = _resolve_dim(args, manifests, shards)
    collection = _resolve_collection(args, manifests)
    print(f"draining {len(shards)} shard(s) from {embed_in} -> collection "
          f"{collection!r} (dim={dim})", file=sys.stderr)

    store = QdrantVectorStore(
        url=args.qdrant_url, collection=collection, vector_size=dim,
        timeout=args.qdrant_timeout,
    )
    # Hard-fail on a dim mismatch BEFORE any upsert (VectorDimMismatch), so we never
    # write mixed-size vectors into an existing collection.
    await store.ensure_collection()

    text_index = None
    if args.text_backend == "elasticsearch":
        text_index = ElasticsearchTextIndex(url=args.es_url, index=args.es_index)
        await text_index.ensure_index()

    gate = HealthGate(
        store, poll_interval=args.poll_interval,
        max_unindexed=args.max_unindexed, max_segments=args.max_segments,
    )
    checkpoint_path = args.checkpoint or (embed_in / "ingest_agent.ckpt")

    stats = await drain(
        shards, store, text_index, gate,
        batch_size=args.batch_size, max_inflight=args.max_inflight,
        checkpoint_path=checkpoint_path, resume=args.resume,
        delete_shards=args.delete_shards, batch_retries=args.batch_retries,
    )
    if text_index is not None:
        await text_index.close()
    print(f"done: drained {stats['shards']} shard(s), {stats['batches']} batch(es), "
          f"{stats['chunks']} chunks into {collection!r}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--embed-in", type=Path, required=True,
                   help="directory of embed shards (*.jsonl[.gz]) + manifest-*.json "
                        "written by `ingest_jsonl.py --embed-out`")
    p.add_argument("--qdrant-url", default="http://localhost:6333")
    p.add_argument("--qdrant-timeout", type=int, default=120,
                   help="per-request Qdrant timeout in seconds (default: %(default)s)")
    p.add_argument("--collection", default=None,
                   help="target Qdrant collection (default: from the embed manifest)")
    p.add_argument("--batch-size", type=int, default=256,
                   help="points upserted per batch (default: %(default)s)")
    p.add_argument("--max-inflight", type=int, default=2,
                   help="concurrent upsert batches — keep small; this is the single "
                        "gentle writer to the capped Qdrant (default: %(default)s)")
    # backpressure thresholds
    p.add_argument("--max-unindexed", type=int, default=100_000,
                   help="pause upserts while points_count - indexed_vectors_count "
                        "exceeds this (incremental-index backlog ceiling; default: %(default)s)")
    p.add_argument("--max-segments", type=int, default=0,
                   help="pause upserts while segments_count exceeds this (0 = disabled)")
    p.add_argument("--poll-interval", type=float, default=2.0,
                   help="seconds between collection-health polls while throttled "
                        "(also the healthy-reading cache TTL; default: %(default)s)")
    # elasticsearch (optional second leg)
    p.add_argument("--text-backend", choices=["none", "elasticsearch"], default="none")
    p.add_argument("--es-url", default="http://localhost:9200")
    p.add_argument("--es-index", default="ragstack")
    # reliability / resume
    p.add_argument("--batch-retries", type=int, default=5,
                   help="in-process retries for a TRANSIENT upsert failure (capped-Qdrant "
                        "drop / timeout / 5xx); exponential backoff (default: %(default)s)")
    p.add_argument("--checkpoint", type=Path, default=None,
                   help="resume checkpoint file (default: <embed-in>/ingest_agent.ckpt)")
    p.add_argument("--resume", action="store_true",
                   help="skip shards/offsets already drained per the checkpoint")
    p.add_argument("--delete-shards", action="store_true",
                   help="unlink each shard once fully drained (bounds peak disk to the "
                        "undrained backlog instead of the whole embed set)")
    return p


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(run_agent(args))


if __name__ == "__main__":
    main()
