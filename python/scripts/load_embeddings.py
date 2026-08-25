#!/usr/bin/env python
"""**Load** stage for the decoupled bulk pipeline (ADR-0001 offline plane, #141).

Reads one or more JSONL **embedding files** (produced by ``embed_shard.py``) and
upserts them into Qdrant/ES by reusing ``IngestionPipeline.index_chunks`` — the
same delete-prior → upsert → index logic as the coupled pipeline, no fork. The
embed work is already done, so this stage needs **only** the stores (no embedding
fleet). The collection's vector dim is taken from the embedding file **header**
(no embedder probe), and mismatched-dim files are rejected before any write.

**Backpressure follow-up:** #141's must-have — throttling upserts on Qdrant's
live health — will land as a ``BackpressuredVectorStore`` decorator wrapping the
pipeline's ``vector_store``; ``index_chunks`` is unchanged when it arrives, so is
this tool. Until then it loads at full rate (safe on an uncapped Qdrant).

Idempotent: deterministic ids + upsert-only + per-doc delete-prior, so re-running
(engine retry / resume) overwrites in place.

**Chunk cap** (#291): a load into a USER-CREATED collection is bounded by the
collection's chunk cap — the registry entry's ``max_chunks`` override, else
``MAX_CHUNKS_PER_COLLECTION`` (50,000) when the entry records a creator
(``owner``) who is not the backfill owner or an ``ADMIN_SUBJECTS`` entry. The
invocation is the job: the files' header ``count`` values are summed, ONE live
``count()`` is taken before the first file is read, and a load that would cross
the cap is refused whole (exit 1, nothing written, the summary and stderr report
``live``/``incoming``/``cap``/``would_fit`` under ``chunk_cap_exceeded``).
``--replay`` is never capped: it restores what was already admitted.

**Replay mode** (``--replay``, #358): instead of embedding files, an ORDERED list
of archive version directories (``ragstack-archive/1``, the ``versions/<n>/``
folders the ingest workflows archive into the owner's Workspace) — the
restore-collection workflow's tool. Every version is verified (sha256s,
geometry, and ``manifest.spec_hash == --spec-hash``, the registry row's) BEFORE
anything is written — or created: the physical stores are ensured only after
verification passes. A refusal exits **3** with an ``ArchiveCorrupt:`` /
``SpecMismatch:`` line (the API marks the collection ``lost``); a registry
disagreement between this worker and the API exits **2**; a mid-stream failure
exits **1** (both leave it ``dormant``, retried). Chunk versions replace their
documents and upsert both legs; tombstone versions delete by doc id.

Usage::

    python scripts/load_embeddings.py shard.s0.emb.jsonl shard.s1.emb.jsonl \
        --collection ragstack_sfr_tok256 --es-index ragstack_sfr_tok256 \
        --qdrant-url http://localhost:6333 --es-url http://localhost:9200 \
        --out load-summary.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from ragstack.ingestion.chunk_cap import ChunkCapExceeded, check_chunk_cap, effective_chunk_cap
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.embedding_file import read_header
from ragstack.ingestion.load_embeddings import (
    REPLAY_BATCH,
    ReplayRefused,
    run_load_file,
    run_replay,
    verify_replay,
)
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import merge_summary
from ragstack.ops import ingest_target
from ragstack.stores.backpressure import BackpressuredVectorStore
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from ragstack.stores.qdrant import QdrantVectorStore


# Placeholder embedder/chunker: the load stage never embeds or chunks. A stub
# with no dependencies keeps the pipeline constructor happy without importing the
# embedding stack.
class _NoEmbed:
    async def embed(self, texts):  # pragma: no cover - never called
        raise RuntimeError("load stage does not embed")


async def _build_pipeline(args, target=None) -> IngestionPipeline:
    if (args.vector_backend == "qdrant") != (args.text_backend == "elasticsearch"):
        raise SystemExit(
            "vector-backend and text-backend must be consistent (both durable or "
            f"both in-memory); got vector={args.vector_backend} text={args.text_backend}"
        )
    if args.backpressure and args.backpressure_poll <= 0:
        # A zero/negative poll would busy-loop get_collection at full request rate
        # while the collection is not green — throttle, don't hammer.
        raise SystemExit("--backpressure-poll must be > 0")
    if args.vector_backend == "memory":
        vstore = InMemoryVectorStore()
        tindex = InMemoryTextIndex()
    else:
        if target is None:  # pragma: no cover — main() resolves before calling
            raise SystemExit("internal: no ingest target resolved")
        # Dim comes from the embedding files themselves (their header), and every
        # file must agree — a wrong-dim file (e.g. 768-d BGE into a 4096-d SFR
        # collection) is caught here, before the collection is created/written.
        dims = set()
        if getattr(args, "replay", None):
            # The archive manifests carry the geometry (already verified by
            # _replay before this runs); a tombstone-only version has none,
            # and the registry entry's dim is authoritative anyway
            # (check_build below refuses a disagreeing archive).
            from ragstack.ingestion.archive import read_manifest

            for vdir in args.replay:
                geom = read_manifest(vdir).get("vectors") or {}
                if geom.get("dim"):
                    dims.add(int(geom["dim"]))
            dims = dims or {target.dim}
        for f in args.embeddings:
            d = read_header(f).get("dim")
            if not d:
                raise SystemExit(f"{f}: embedding file header missing/zero 'dim'")
            dims.add(int(d))
        if len(dims) > 1:
            raise SystemExit(f"embedding files disagree on dim: {sorted(dims)}")
        dim = next(iter(dims))
        # The files' dim must also match the registry entry, or this load builds a
        # store whose vectors are not the ones the entry promises (#263/ADR-0002).
        target.check_build(dim=dim)
        # Both names come from the registry entry, never from the command line.
        # A contradicting --es-index was refused during resolution.
        es_index = target.es_index
        vstore = QdrantVectorStore(url=target.qdrant_url, collection=target.collection,
                                   vector_size=dim, timeout=args.qdrant_timeout,
                                   upsert_batch_size=args.upsert_batch_size,
                                   upsert_concurrency=args.upsert_concurrency)
        await vstore.ensure_collection()
        if args.backpressure:
            # #141: hold each upsert until the collection is green, so a bulk load
            # never piles unindexed vectors onto a Qdrant that is optimizing (the
            # VMA-exhaustion driver). Transparent to index_chunks/the pipeline.
            vstore = BackpressuredVectorStore(
                vstore, poll_interval=args.backpressure_poll,
                max_wait=args.backpressure_max_wait,
            )
        # --bulk-refresh turns OFF the per-write forced refresh. That is the whole
        # win: measured mid-build, forced refreshes were ~99% of the text leg's
        # wall clock. Parking index.refresh_interval (below) is the smaller,
        # complementary half — it governs the periodic background refresh, which an
        # explicit per-request refresh bypasses entirely.
        tindex = ElasticsearchTextIndex(url=args.es_url, index=es_index,
                                        refresh_on_write=not args.bulk_refresh)
        await tindex.ensure_index()
    # Replay does its own per-version delete-prior (a document's chunks may
    # span two upsert batches, and index_chunks' per-batch delete would remove
    # the first batch's rows) — see run_replay.
    replay = bool(getattr(args, "replay", None))
    # The graph leg rides along ONLY for replay (#380): a tombstone version
    # must drop its documents' triples too, scoped to THIS collection — the
    # stamp `collection=` is what keeps `delete_by_doc` from crossing into
    # another collection's copy of the same doc id (#209). The load path
    # deliberately gets none: it never wrote triples, so it has none to drop.
    graph_store = _graph_store_for_replay() if replay and target is not None else None
    return IngestionPipeline(loader=JsonlLoader(), chunker=RecursiveCharacterChunker(),
                             embedder=_NoEmbed(), vector_store=vstore, text_index=tindex,
                             graph_store=graph_store,
                             collection=target.collection if target is not None else None,
                             delete_concurrency=args.delete_concurrency,
                             delete_prior=not (args.no_delete_prior or replay))


def _graph_store_for_replay():
    """The durable graph backend the API writes triples to, built from the
    same settings (``GRAPH_BACKEND`` / ``NEO4J_*``) — or ``None``. Only
    ``neo4j`` counts: an in-memory graph is process-local, so a worker's copy
    would hold nothing to delete. Constructed directly rather than through
    ``api.deps`` so the worker stays free of the FastAPI app."""
    from ragstack.config import settings

    if settings.graph_backend != "neo4j":
        return None
    from ragstack.stores.neo4j import Neo4jGraphStore

    return Neo4jGraphStore(uri=settings.neo4j_uri, user=settings.neo4j_user,
                           password=settings.neo4j_password,
                           database=settings.neo4j_database or None)


async def _replay(args, target) -> int:
    """``--replay``: verify every version, THEN build the stores, then replay.

    Exit codes are the API's classification signal (restore.py): **3** = the
    archive was refused (nothing written, not even the physical collection —
    verification runs before ``_build_pipeline`` ensures it) → ``lost``;
    **2** = the worker's registry disagrees with the API's (a deployment
    misconfiguration, retried once fixed) → ``dormant``; **1** = a mid-stream
    failure after verification → ``dormant``, retried.
    """
    spec_hash = args.spec_hash or (target.spec.spec_hash() if target is not None else "")
    if target is not None and args.spec_hash and target.spec.spec_hash() != args.spec_hash:
        # The API passed the registry row's hash; the worker resolved the same
        # id to a different spec. Not an archive problem — the two processes
        # read different registries — so exit 2 (dormant, retried), not 3.
        print(f"registry disagreement: --spec-hash {args.spec_hash!r} != registry entry "
              f"{target.collection_id!r} spec_hash {target.spec.spec_hash()!r} as this "
              "worker reads it; fix the worker's registry configuration",
              file=sys.stderr, flush=True)
        return 2
    collection_id = target.collection_id if target is not None else (args.collection_id or "")
    try:
        manifests = verify_replay(list(args.replay), spec_hash=spec_hash,
                                  collection_id=collection_id)
    except ReplayRefused as e:
        # Nothing was written — and nothing created: the stores are ensured
        # only below. Exit 3 is what the API's restore watcher classifies as
        # `lost` (the marker line is its secondary signal).
        print(str(e), file=sys.stderr, flush=True)
        return 3
    pipeline = await _build_pipeline(args, target)
    tindex = pipeline.text_index
    can_park = args.bulk_refresh and hasattr(tindex, "bulk_load_refresh")
    prior = await tindex.bulk_load_refresh(True) if can_park else None
    try:
        summary = await run_replay(
            pipeline, list(args.replay), spec_hash=spec_hash, collection_id=collection_id,
            batch_size=args.replay_batch, delete_concurrency=args.delete_concurrency,
            log=lambda msg: print(msg, flush=True), manifests=manifests,
        )
    finally:
        if can_park:
            await tindex.restore_refresh(prior)
            await tindex.refresh()
        if pipeline.graph_store is not None and hasattr(pipeline.graph_store, "close"):
            await pipeline.graph_store.close()
    out = summary.as_dict()
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    print(f"replayed {summary.n_versions} version(s): {summary.n_chunks} chunk(s) upserted, "
          f"{summary.n_docs_deleted} doc(s) tombstoned in {summary.seconds:.1f}s → {args.out}",
          flush=True)
    if summary.status != "completed":
        print(summary.error, file=sys.stderr, flush=True)
        return 1
    return 0


def _chunk_cap_for(target) -> int | None:
    """The cap this load is bounded by (``None`` = unlimited): the entry's
    ``max_chunks`` override, else the deployment default for a user-created
    entry. "User-created" here is decided from the spec-recorded creator and
    settings alone (no ACL / user store in a CLI): a non-empty ``owner`` that is
    neither the backfill owner nor an ``ADMIN_SUBJECTS`` entry. An admin whose
    role comes only from a stored ``users.role`` grant is therefore capped on
    this path unless the entry carries an override — the conservative side."""
    if target is None:
        return None
    from ragstack.config import settings

    spec = target.spec
    owner = getattr(spec, "owner", "") or ""
    user_created = bool(owner) and owner != settings.acl_backfill_owner and (
        owner not in set(settings.admin_subjects or ())
    )
    return effective_chunk_cap(
        override=getattr(spec, "max_chunks", None), user_created=user_created,
        default_cap=settings.max_chunks_per_collection,
    )


def _file_chunk_count(path: str) -> int:
    """Chunks in one embedding file: the header's ``count`` (what
    ``write_embedding_file`` records), else one line per record."""
    header = read_header(path)
    if header.get("count") is not None:
        return int(header["count"])
    with open(path, encoding="utf-8") as fh:
        return max(0, sum(1 for line in fh if line.strip()) - 1)


async def _refuse_over_cap(args, pipeline, cap: int | None) -> int | None:
    """The bulk path's cap gate (#291): sum the files' header counts, take ONE
    live count, and refuse the whole invocation before the first file is read
    when it would cross ``cap``. Returns an exit code on refusal, else None."""
    if cap is None:
        return None
    incoming = sum(_file_chunk_count(f) for f in args.embeddings)
    try:
        await check_chunk_cap(pipeline.vector_store, incoming, cap)
    except ChunkCapExceeded as e:
        files = list(args.embeddings)
        summary = {
            "n_shards": len(files), "n_shards_failed": len(files), "n_docs": 0,
            "n_chunks": 0, "failed_shards": sorted(files),
            "errors": dict.fromkeys(files, str(e)), "chunk_cap": e.detail(),
        }
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, sort_keys=True)
        print(str(e), file=sys.stderr, flush=True)
        print(f"refused: nothing written → {args.out}", flush=True)
        return 1
    return None


async def amain(args, target=None) -> int:
    if getattr(args, "replay", None):
        return await _replay(args, target)
    pipeline = await _build_pipeline(args, target)
    # The chunk cap (#291): whole-invocation, one count, before the first read.
    refused = await _refuse_over_cap(args, pipeline, _chunk_cap_for(target))
    if refused is not None:
        return refused
    # Files hold disjoint document sets and chunk ids are deterministic, so
    # concurrent files cannot race on a doc_id or duplicate a point (#323). Receipts
    # are collected positionally, so the summary is identical to the serial run
    # regardless of completion order.
    sem = asyncio.Semaphore(max(1, args.file_concurrency))
    receipts: list = [None] * len(args.embeddings)

    async def _one(i: int, path: str) -> None:
        async with sem:
            r = await run_load_file(pipeline, path, file_id=path, tenant=args.tenant)
        receipts[i] = r
        print(f"[{path}] status={r.status} chunks={r.n_chunks}"
              + (f"  ERROR: {r.error}" if r.error else ""), flush=True)

    # Refresh off for the duration, restored in `finally` even on failure — and an
    # explicit refresh before we return, so the count-based verification the driver
    # runs immediately after does not read a stale index (#323).
    tindex = pipeline.text_index
    can_park = args.bulk_refresh and hasattr(tindex, "bulk_load_refresh")
    prior = await tindex.bulk_load_refresh(True) if can_park else None
    if can_park:
        print(f"refresh_interval parked (was {prior or 'default'})", flush=True)
    try:
        # return_exceptions + re-raise, for the same reason index_chunks does it:
        # a bare gather propagates the first failure while the other files keep
        # loading unsupervised. run_load_file already converts per-file errors into
        # FAILED receipts, so this should be unreachable — but "should be" is what
        # the sequential version looked like too, and an unreachable guard is
        # cheaper than a load that keeps writing after it reported failure.
        outcomes = await asyncio.gather(
            *(_one(i, p) for i, p in enumerate(args.embeddings)),
            return_exceptions=True,
        )
        for o in outcomes:
            if isinstance(o, BaseException):
                raise o
    finally:
        if can_park:
            await tindex.restore_refresh(prior)
            await tindex.refresh()
            print("refresh_interval restored + index refreshed", flush=True)
    summary = merge_summary(receipts)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
    print(f"loaded {summary['n_chunks']} chunk(s) from {summary['n_shards']} file(s); "
          f"failed={summary['n_shards_failed']} → {args.out}", flush=True)
    # Arm ADR-0002's build-spec guard for this store. Without a manifest,
    # check_ingest_build_spec early-returns forever and a later API ingest with a
    # different chunker interleaves silently — the failure #263 is about.
    manifest_dir = args.manifest_dir or os.getenv("COLLECTION_MANIFEST_DIR", "")
    if target is not None and manifest_dir and not summary["n_shards_failed"]:
        spec_hash = target.write_manifest(
            manifest_dir,
            corpus=f"{summary['n_shards']} embedding file(s)",
            chunk_count=summary["n_chunks"],
        )
        print(f"wrote provenance manifest ({spec_hash}) to {manifest_dir}", flush=True)
    if args.fail_on_error and summary["n_shards_failed"]:
        return 1
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("embeddings", nargs="*", help="one or more JSONL embedding files "
                   "(omit with --replay)")
    p.add_argument("--replay", nargs="+", metavar="VERSION_DIR", default=[],
                   help="RESTORE mode (#358): replay these ragstack-archive/1 version "
                        "directories IN ORDER instead of loading embedding files. Every "
                        "directory is verified (sha256s, geometry, spec_hash) before any "
                        "write; a failure exits 3 with an ArchiveCorrupt:/SpecMismatch: "
                        "line and nothing written. Chunk versions replace their documents, "
                        "tombstone versions delete by doc id")
    p.add_argument("--spec-hash", default="",
                   help="with --replay: the registry row's build-spec hash every manifest "
                        "must match (default: the resolved entry's own hash)")
    p.add_argument("--replay-batch", type=int, default=REPLAY_BATCH,
                   help=f"with --replay: chunks per upsert batch (default {REPLAY_BATCH})")
    p.add_argument("--out", default="load-summary.json", help="output summary path")
    p.add_argument("--tenant", default=None,
                   help="override tenant (default: each file's header tenant)")
    p.add_argument("--fail-on-error", action="store_true",
                   help="exit non-zero if any file failed to load")
    p.add_argument("--backpressure", action="store_true",
                   help="hold each upsert until the Qdrant collection is green (#141). "
                        "OFF by default: the capped-Qdrant A/B benchmark showed it adds "
                        "latency without preventing drops below crash-scale (millions of "
                        "vectors + deferred indexing). Enable for a very large corpus on a "
                        "capped Qdrant")
    p.add_argument("--backpressure-poll", type=float, default=2.0,
                   help="seconds between health polls while holding (default 2.0)")
    p.add_argument("--backpressure-max-wait", type=float, default=None,
                   help="give up (error) if not green after this many seconds; "
                        "default None = wait indefinitely")
    p.add_argument("--vector-backend", choices=["qdrant", "memory"], default="qdrant")
    p.add_argument("--collection", default=None,
                   help="DEPRECATED — the PHYSICAL store name. Accepted only when "
                        "a registry entry already claims it; prefer --collection-id")
    p.add_argument("--qdrant-url", default="http://localhost:6333")
    p.add_argument("--qdrant-timeout", type=int, default=120)
    p.add_argument("--upsert-batch-size", type=int, default=256,
                   help="points per Qdrant upsert request (bounds payload size)")
    p.add_argument("--upsert-concurrency", type=int, default=4,
                   help="concurrent upsert batches (pipelines the load; default 4). "
                        "1 = serial, safest under a capped/optimizing collection")
    p.add_argument("--delete-concurrency", type=int, default=8,
                   help="concurrent delete-prior operations (default 8)")
    p.add_argument("--no-delete-prior", action="store_true",
                   help="skip the per-doc_id delete before upserting. ONLY safe when "
                        "the chunk ids cannot have moved since the last load of these "
                        "documents — i.e. a replay from UNCHANGED embedding files, "
                        "where ids are read from the file rather than recomputed. "
                        "Saves ~550k round-trips per 64-shard batch. If any document "
                        "here was ever ingested with different chunk boundaries, its "
                        "old chunks survive as orphans: do not use it to 'speed up' a "
                        "load whose inputs were re-extracted or re-chunked")
    p.add_argument("--bulk-refresh", action="store_true",
                   help="stop forcing a synchronous text-index refresh on every "
                        "write, and park the periodic refresh interval too. Measured "
                        "mid-build on an 11.9M-doc index: 1,355 refreshes in 90s "
                        "(~15/s, one per bulk and per delete) burning 89.1s of that "
                        "90s window, against 1.5s deleting and 0.0s indexing — "
                        "refresh was ~99%% of the text leg's wall clock. The loader "
                        "forces one explicit refresh before returning, so a count "
                        "check straight after the load is still accurate. Do not use "
                        "it if something must search the index DURING the load")
    p.add_argument("--file-concurrency", type=int, default=1,
                   help="embedding files loaded concurrently (default 1 = serial, the "
                        "previous behaviour). Files hold disjoint documents and ids "
                        "are deterministic, so concurrency cannot race or duplicate. "
                        "COSTS MEMORY: each in-flight file is read fully into RAM — "
                        "measured ~6 GB resident for a 1.3 GB file (~4.6x expansion, "
                        "float lists dominate), so N files is ~6N GB. Size this "
                        "against free memory, NOT against store headroom; overshoot "
                        "evicts the page cache the stores read through. It also "
                        "MULTIPLIES the other knobs: the delete semaphore is "
                        "per-call, so N files means N x --delete-concurrency "
                        "concurrent deletes and N x --upsert-concurrency upserts")
    p.add_argument("--text-backend", choices=["elasticsearch", "memory"], default="elasticsearch")
    p.add_argument("--es-url", default="http://localhost:9200")
    p.add_argument("--es-index", default=None)
    p.add_argument("--manifest-dir", default="",
                   help="write a provenance manifest here (defaults to "
                        "$COLLECTION_MANIFEST_DIR). Arms ADR-0002's build-spec "
                        "guard for this store; skipped if neither is set")
    ingest_target.add_arguments(p)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if bool(args.replay) == bool(args.embeddings):
        raise SystemExit("give either embedding files or --replay VERSION_DIR..., not both/neither")
    if args.replay and args.replay_batch < 1:
        raise SystemExit("--replay-batch must be >= 1")
    # Resolve the registry entry before any store is created or written (#263).
    # The in-memory backend is the dev/test path and owns no physical store, so
    # it has nothing to register.
    target = (
        ingest_target.resolve_or_exit(args)
        if args.vector_backend == "qdrant"
        else None
    )
    return asyncio.run(amain(args, target))


if __name__ == "__main__":
    sys.exit(main())
