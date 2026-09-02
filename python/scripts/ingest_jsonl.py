#!/usr/bin/env python
"""Bulk-ingest a pre-extracted JSONL corpus into Qdrant (+ optional Elasticsearch).

This is the operator tool for the large extraction dumps (hundreds of MB) that
are too big for the per-file size guard on the API ingest path. It *streams* the
file (constant memory), recovers scholarly metadata per record
(:mod:`ragstack.ingestion.enrich` — DOI / title / authors / year / doc_type /
citations), chunks the body text, embeds in batches, and upserts the points.

Each input line is one document::

    {"text": "...", "path": "/.../jvi.02415-06.pdf", "metadata": {title, authors, ...}}

Two outputs, independently selectable:

- **Index** (default): chunk + embed + upsert into Qdrant, and into Elasticsearch
  when ``--text-backend elasticsearch``. Each chunk carries the index-safe
  metadata subset (DOI/title/authors/year/doc_type/n_citations) for filtering.
- **Catalog** (``--catalog-out FILE``): one JSON line per ingested document with
  the *full* enriched metadata, including the extracted citation list — the
  document-level metadata collection. Combine with ``--no-index`` to collect
  metadata without paying for embeddings.

Resumable: documents are flushed at document boundaries and the last fully
indexed line number is checkpointed to ``<input>.ckpt`` (override with
``--checkpoint``); re-running skips everything up to the checkpoint. Chunk IDs
are deterministic, so a resumed run overwrites in place rather than duplicating.
The checkpoint also records ``done_ranges`` — line intervals of batches that
completed *out of order above* a stalled frontier (a slow/failed early batch) —
so a resume skips those too instead of re-embedding work already upserted (#65).
Resume keys on line number, so it assumes the input file is unchanged across
restarts; editing it and resuming (without a fresh ``--checkpoint``) treats
already-passed lines as done regardless of their new content. Use ``--replace``
(which disables the done_ranges skip and reprocesses) when re-ingesting edits.

Usage::

    cd python
    . /rag/bin/activate
    # full corpus into the smoke collection, tenant 'public', against the sidecar:
    python scripts/ingest_jsonl.py /rag/inputs/<file>.jsonl \\
        --tenant public \\
        --embedding-model BAAI/bge-base-en-v1.5 \\
        --catalog-out /rag/inputs/<file>.catalog.jsonl

    # metadata-only pass (no embeddings), e.g. to QA the DOI/title recovery:
    python scripts/ingest_jsonl.py /rag/inputs/<file>.jsonl --no-index \\
        --catalog-out /rag/inputs/<file>.catalog.jsonl

    # also populate the BM25 index, and cap the run for a smoke:
    python scripts/ingest_jsonl.py /rag/inputs/<file>.jsonl --tenant public \\
        --embedding-model BAAI/bge-base-en-v1.5 \\
        --text-backend elasticsearch --es-index ragstack_smoke \\
        --doc-types article --limit 200
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, TextIO

import httpx

from ragstack.embed_pool import make_pooled_embedder
from ragstack.embedders import make_embedder
from ragstack.ingestion.boilerplate import filter_from_mode
from ragstack.ingestion.chunker_config import build_chunker
from ragstack.ingestion.chunkers import link_neighbors_by_document
from ragstack.ingestion.embed_bridge import SyncEmbedBridge
from ragstack.ingestion.enrich import EMPTY, enrich, index_metadata, resolve_profile
from ragstack.ingestion.loaders import deterministic_doc_id
from ragstack.ingestion.retry import is_transient_error, retry_delay
from ragstack.ingestion.segmentation_cache import SegmentationCache, config_fingerprint
from ragstack.ingestion.tokenization import make_token_counter, resolve_max_tokens
from ragstack.models import Chunk, Document
from ragstack.ops import ingest_target
from ragstack.provenance import make_ingest_manifest, write_manifest
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.qdrant import QdrantVectorStore, collection_name


def _doc_id_key(record: dict[str, Any], text: str) -> str:
    rec_path = record.get("path", "") or ""
    return str(Path(rec_path).resolve()) if rec_path else text


def _union_range(ranges: list[list[int]], lo: int, hi: int) -> list[list[int]]:
    """Insert inclusive ``[lo, hi]`` into a sorted, non-overlapping interval list,
    coalescing any intervals that overlap or *abut* (a gap of 1 line). Returns a
    fresh sorted/coalesced list; inputs are not mutated."""
    out: list[list[int]] = []
    for r in sorted([list(x) for x in ranges] + [[lo, hi]]):
        if out and r[0] <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], r[1])
        else:
            out.append([r[0], r[1]])
    return out


def _trim_below(ranges: list[list[int]], frontier: int) -> list[list[int]]:
    """Drop the part of every interval at or below ``frontier`` (now subsumed by
    the contiguous-prefix frontier). Fully-covered intervals disappear; a straddling
    one is clipped to ``[frontier+1, hi]``. Keeps ``done_ranges`` small once a gap
    clears so it can't grow without bound behind a persistently-stuck early batch."""
    out: list[list[int]] = []
    for lo, hi in ranges:
        if hi <= frontier:
            continue
        out.append([max(lo, frontier + 1), hi])
    return out


def _line_covered(line_no: int, frontier: int, ranges: list[list[int]]) -> bool:
    """True if ``line_no`` was already durably indexed: at/below the frontier, or
    inside a persisted done-range (a batch that completed out of order above the
    frontier gap)."""
    if line_no <= frontier:
        return True
    for lo, hi in ranges:
        if lo <= line_no <= hi:
            return True
        if lo > line_no:
            break  # ranges are sorted; no later interval can contain line_no
    return False


def _sanitize_ranges(raw: Any) -> list[list[int]]:
    """Coerce a persisted ``done_ranges`` value into a sorted, coalesced list of
    ``[int, int]`` intervals, dropping anything malformed. Any structural problem
    yields ``[]`` — done_ranges is pure optimization metadata, so discarding a
    corrupt value only costs redundant work, never correctness."""
    if not isinstance(raw, list):
        return []
    out: list[list[int]] = []
    try:
        for item in raw:
            lo, hi = int(item[0]), int(item[1])
            if lo <= hi:
                out = _union_range(out, lo, hi)
    except (TypeError, ValueError, IndexError, KeyError):
        return []
    return out


def _read_checkpoint(path: Path) -> dict[str, Any]:
    """Persisted resume state:
    ``{"line": int, "doc_types": list[str] | None, "done_ranges": list[[int,int]]}``.

    ``done_ranges`` (added for #65) is a sorted, coalesced list of inclusive
    ``[lo, hi]`` line intervals for batches that COMPLETED out of order *above* the
    contiguous-prefix frontier ``line``. It is pure resume-optimization metadata so a
    restart doesn't re-embed work already durably upserted while an early batch is
    stuck; it never moves the frontier, and if dropped/corrupt it only costs
    redundant work (never data loss).

    Missing/corrupt reads as a zero checkpoint (fresh start). The legacy
    bare-integer format is still accepted (line only, no filter/ranges recorded).
    """
    zero: dict[str, Any] = {"line": 0, "doc_types": None, "done_ranges": []}
    try:
        raw = path.read_text().strip()
    except FileNotFoundError:
        return zero
    try:
        data = json.loads(raw)
        return {
            "line": int(data.get("line", 0)),
            "doc_types": data.get("doc_types"),
            "done_ranges": _sanitize_ranges(data.get("done_ranges")),
        }
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        try:
            return {"line": int(raw), "doc_types": None, "done_ranges": []}  # legacy bare-int
        except ValueError:
            return zero


def _write_checkpoint(
    path: Path,
    line_no: int,
    doc_types: list[str] | None,
    done_ranges: list[list[int]] | None = None,
) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    # Persist the active doc-type filter alongside the line so a resume with a
    # *different* filter is rejected rather than silently skipping lines the new
    # filter would now keep. done_ranges records out-of-order completions above the
    # frontier gap (see _read_checkpoint) so a resume skips them; [] on a clean run.
    tmp.write_text(
        json.dumps({"line": line_no, "doc_types": doc_types, "done_ranges": done_ranges or []})
    )
    tmp.replace(path)  # atomic so a crash mid-write can't corrupt the checkpoint


def _iter_records(fh: TextIO):
    """Yield ``(line_no, record)`` for each valid JSON line; skip blanks/garbage."""
    for line_no, raw in enumerate(fh, start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield line_no, json.loads(raw)
        except json.JSONDecodeError:
            print(f"  warn: skipping malformed line {line_no}", file=sys.stderr)


def _open_checkpoint_paths(args: argparse.Namespace, current_doc_types: list[str] | None):
    """Resolve the checkpoint path and resume start-line.

    Index and catalog-only passes keep *separate* default checkpoints: a cheap
    ``--no-index`` catalog run must not advance the checkpoint the expensive
    indexing run resumes from (which would make indexing skip every document)."""
    src = args.input
    if args.checkpoint:
        ckpt_path = args.checkpoint
    else:
        ckpt_path = src.with_suffix(src.suffix + (".catalog.ckpt" if args.no_index else ".ckpt"))
    ckpt = (
        _read_checkpoint(ckpt_path)
        if args.resume
        else {"line": 0, "doc_types": None, "done_ranges": []}
    )
    start_line = ckpt["line"]
    done_ranges = ckpt["done_ranges"]
    if start_line and ckpt["doc_types"] != current_doc_types:
        # Resume keys only on line number, so a looser filter would silently skip
        # every line the stricter run had filtered out. Fail closed instead.
        raise SystemExit(
            f"checkpoint {ckpt_path.name} was written with --doc-types "
            f"{ckpt['doc_types']}, but this run uses {current_doc_types}. Resuming "
            "would silently skip lines the new filter would keep. Re-run with the "
            "same --doc-types, a fresh --checkpoint, or without --resume."
        )
    if start_line:
        extra = f" (+ {len(done_ranges)} completed range(s) above the gap)" if done_ranges else ""
        print(f"resuming after line {start_line}{extra} (from {ckpt_path.name})", file=sys.stderr)
    return ckpt_path, start_line, done_ranges


def _open_catalog(args: argparse.Namespace, start_line: int) -> TextIO | None:
    if not args.catalog_out:
        return None
    # Append on resume so a resumed run doesn't truncate the catalog written so far.
    mode = "a" if (args.resume and start_line) else "w"
    return open(args.catalog_out, mode, encoding="utf-8")


def _kept(enriched, keep_types) -> bool:
    return enriched.doc_type != EMPTY and (keep_types is None or enriched.doc_type in keep_types)


async def _run_catalog_only(
    args, ckpt_path, start_line, catalog, stats, keep_types, current_doc_types, profile
) -> None:
    """Metadata-only pass: enrich + write the catalog, no chunking/embedding."""
    with args.input.open(encoding="utf-8") as fh:
        pending = 0
        catalog_pending: list[str] = []  # rows not yet covered by the checkpoint

        def checkpoint(up_to_line: int) -> None:
            # Flush buffered catalog rows BEFORE advancing the checkpoint so the
            # catalog never gets ahead of the resume point; a crash-resume would
            # otherwise re-append them (deterministic per doc, but duplicated).
            if catalog is not None and catalog_pending:
                catalog.write("".join(catalog_pending))
                catalog.flush()
                catalog_pending.clear()
            _write_checkpoint(ckpt_path, up_to_line, current_doc_types)

        for line_no, record in _iter_records(fh):
            if line_no <= start_line:
                continue
            # Advance over every processed line, including skipped ones, so a
            # resume doesn't re-scan a trailing run of filtered records forever.
            pending = line_no
            stats["seen"] += 1
            enriched = enrich(record, profile=profile)
            if not _kept(enriched, keep_types):
                stats["skipped"] += 1
                continue
            if catalog is not None:
                catalog_pending.append(
                    json.dumps(enriched.model_dump(), ensure_ascii=False) + "\n"
                )
            stats["docs"] += 1
            pending = line_no
            if stats["docs"] % 2000 == 0:
                checkpoint(pending)
            if args.limit and stats["docs"] >= args.limit:
                break
        checkpoint(pending)


def _make_endpoint_embedder(http, *, api, urls, model, api_key, max_concurrency):
    """A pooled fan-out when multiple ``urls`` are given, else a single-endpoint
    embedder. One place for backend selection (api / model / api_key / pooling)."""
    common = {"api": api, "http": http, "model": model,
              "api_key": api_key or os.getenv("OPENAI_API_KEY")}
    if len(urls) > 1:
        return make_pooled_embedder(base_urls=urls, max_concurrency=max_concurrency, **common)
    return make_embedder(base_url=urls[0], **common)


def _build_embedder(args: argparse.Namespace, http: httpx.AsyncClient):
    """The stored-chunk embedder for ``args``. Also the default for the semantic
    chunker's breakpoint pass (see _build_breakpoint_embedder)."""
    return _make_endpoint_embedder(
        http, api=args.embedding_api, urls=args.embedding_url,
        model=args.embedding_model, api_key=args.embedding_api_key,
        max_concurrency=args.embedding_max_concurrency,
    )


def _build_breakpoint_embedder(args: argparse.Namespace, http: httpx.AsyncClient):
    """Embedder for the semantic chunker's BREAKPOINT pass (topic-boundary
    detection). Defaults to the main --embedding-* backend; when --breakpoint-
    embedding-url is given, boundary detection runs on a SEPARATE (cheaper, e.g.
    GPU-served BGE) model while stored chunks keep using the main model. Each
    --breakpoint-embedding-* field falls back to its --embedding-* counterpart;
    unset --breakpoint-embedding-url → reuse the main embedder (no behaviour change)."""
    urls = args.breakpoint_embedding_url
    if not urls:
        return _build_embedder(args, http)
    return _make_endpoint_embedder(
        http,
        api=args.breakpoint_embedding_api or args.embedding_api,
        urls=urls,
        model=args.breakpoint_embedding_model or args.embedding_model,
        api_key=args.breakpoint_embedding_api_key or args.embedding_api_key,
        max_concurrency=(args.breakpoint_embedding_max_concurrency
                         or args.embedding_max_concurrency),
    )


async def _embed_drop_bad(embedder: Any, chunks: list[Chunk]) -> list[Chunk]:
    """Embed each chunk's content, returning the chunks that embedded (with
    ``.embedding`` set).

    Backstop: when the embedder exposes ``embed_isolated`` (the single-endpoint
    ``BatchingEmbedder``), a 4xx / over-context-window chunk is bisected out and
    **dropped** with a warning rather than failing the whole batch — so one
    oversized chunk (e.g. an estimate-counter undercount) can't abort a long
    ingest. Infra failures (5xx / network) still raise and leave the batch for
    ``--resume``. Both the single-endpoint ``BatchingEmbedder`` and the multi-
    endpoint ``PooledEmbedder`` expose ``embed_isolated``, so the pooled fan-out
    quarantines bad inputs too (5xx / network still propagate for ``--resume``)."""
    if not chunks:
        return []  # catalog-only batch (e.g. a #65 resume-skipped doc): nothing to embed
    texts = [c.content for c in chunks]
    if hasattr(embedder, "embed_isolated"):
        vecs, quarantined = await embedder.embed_isolated(texts)
        kept: list[Chunk] = []
        for c, v in zip(chunks, vecs, strict=True):
            if v is None:
                continue
            c.embedding = v
            kept.append(c)
        if quarantined:
            print(
                f"  warn: dropped {quarantined} unembeddable chunk(s) "
                "(over context window / bad input); continuing",
                file=sys.stderr,
            )
        return kept
    vecs = await embedder.embed(texts)
    for c, v in zip(chunks, vecs, strict=True):
        c.embedding = v
    return chunks


class DocMetricsWriter:
    """Streaming writer for the per-document metrics JSONL (``--doc-metrics-out``).

    One row per document::

        {doc_id, source_file, n_chunks, tokens_min, tokens_median, tokens_max,
         chunk_chars_median, skipped, error}

    Token stats are computed from the final (stored) chunks' text via the shared
    :class:`TokenCounter`; char stats from ``len(chunk.content)``. A skipped or
    fully-unembeddable document has ``n_chunks == 0`` and null token/char stats.

    Rows are appended as they finalize (skipped docs at the producer; indexed docs
    right after ``link_neighbors``). Writes go through one handle; the ingest is a
    single-threaded asyncio loop, but the caller still serialises worker writes
    under the shared lock so a row is never interleaved with another.
    """

    def __init__(self, path: Path, token_counter: Any, *, append: bool) -> None:
        self._fh = open(path, "a" if append else "w", encoding="utf-8")
        self._token_counter = token_counter

    def emit(
        self,
        doc_id: str,
        source_file: str,
        chunks: list[Chunk],
        *,
        skipped: bool = False,
        error: str | None = None,
    ) -> None:
        if chunks:
            toks = [self._token_counter.count(c.content) for c in chunks]
            chars = [len(c.content) for c in chunks]
            tokens_min: int | None = min(toks)
            tokens_median: float | None = statistics.median(toks)
            tokens_max: int | None = max(toks)
            chunk_chars_median: float | None = statistics.median(chars)
        else:
            tokens_min = tokens_median = tokens_max = chunk_chars_median = None
        row = {
            "doc_id": doc_id,
            "source_file": source_file,
            "n_chunks": len(chunks),
            "tokens_min": tokens_min,
            "tokens_median": tokens_median,
            "tokens_max": tokens_max,
            "chunk_chars_median": chunk_chars_median,
            "skipped": skipped,
            "error": error,
        }
        # No per-row flush: this is a diagnostic side-file, not the durability
        # mechanism (the .ckpt carries resume state). Normal buffering + the
        # close() flush avoid a syscall per document on a million-doc ingest; a
        # hard-crash tail loss is re-derived on --resume.
        self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def close(self) -> None:
        self._fh.close()


def _write_run_metrics(
    path: Path,
    *,
    input_file: str,
    stats: dict[str, int],
    failed_seqs: list[int],
    wall_s: float,
) -> None:
    """Append one per-FILE summary row to ``--run-metrics-out`` at end-of-run::

        {file, docs_seen, docs_indexed, docs_skipped, chunks, failed_batches,
         failed_batch_seqs, wall_s, chunks_per_s}

    ``failed_*`` mirror the ingester's own failed-batch bookkeeping (the seqs whose
    batch errored and left the checkpoint stalled). ``docs_indexed`` counts docs
    that produced chunks and were handed to a batch (``stats['docs']``); note a
    doc in a failed batch still counts here — the per-doc metrics carry the
    per-document error detail.

    ALWAYS appends (unlike the per-doc writer): each row is one file's summary, so
    ingesting several files into one --run-metrics-out — as separate invocations —
    must accumulate rows, not truncate the prior file's row.
    """
    wall = round(wall_s, 3)
    chunks = stats["chunks"]
    row = {
        "file": input_file,
        "docs_seen": stats["seen"],
        "docs_indexed": stats["docs"],
        "docs_skipped": stats["skipped"],
        "chunks": chunks,
        "boilerplate_flagged": stats.get("boilerplate_flagged", 0),
        "boilerplate_dropped": stats.get("boilerplate_dropped", 0),
        "failed_batches": len(failed_seqs),
        "failed_batch_seqs": sorted(failed_seqs),
        "wall_s": wall,
        "chunks_per_s": round(chunks / wall, 2) if wall > 0 else None,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# Transient-error classification + backoff moved to ragstack.ingestion.retry:
# library code rather than CLI-private, so a second retriable caller inherits it
# instead of growing its own. The shared version also jitters the delay.
async def run(
    args: argparse.Namespace,
    target: ingest_target.IngestTarget | None = None,
) -> None:
    """Ingest ``args.input``.

    ``target`` is the resolved registry entry (#263) and supplies every physical
    name — ``main()`` always passes one for an indexing run. It stays optional
    because the reingest/checkpoint tests drive ``run()`` directly with in-memory
    stores and a hand-built Namespace; ``None`` keeps the pre-#263 behaviour for
    those in-process callers only. The CLI cannot reach that path.
    """
    keep_types = set(args.doc_types) if args.doc_types else None
    # Publisher profile (DOI prefix / filename rule / front-matter set) for
    # enrichment; unknown names degrade to the ASM default in resolve_profile.
    profile = resolve_profile(args.publisher_profile)
    # Canonical (sorted) form of the active filter, persisted in the checkpoint so
    # a resume under a different filter is detected.
    current_doc_types = sorted(keep_types) if keep_types else None
    # Chunker selected by --chunk-method. The semantic chunkers need to embed
    # sentence buffers; they run synchronously inside the (async) ingest, so hand
    # them a SyncEmbedBridge that builds its own BREAKPOINT embedder on a background
    # loop. Built only for the semantic methods; closed at run() exit.
    embed_bridge: SyncEmbedBridge | None = None
    if args.chunk_method in ("semantic", "semantic_pooled"):
        # The breakpoint embedder defaults to the main --embedding-* backend, but
        # --breakpoint-embedding-* can route boundary detection to a separate,
        # cheaper (e.g. GPU-served BGE) model while stored chunks keep the main
        # model. batch_size lets the bridge fan one document's buffers out into
        # concurrent sub-batch calls spread across the breakpoint endpoints.
        embed_bridge = SyncEmbedBridge(
            lambda http: _build_breakpoint_embedder(args, http), batch_size=args.batch_size
        )
    # Token budget: size/cap chunks so none exceeds the embedder's context window.
    # The counter is the embedding model's tokenizer by default (--chunk-token-counter
    # hf); the budget is auto-detected from the endpoint's max_model_len unless
    # --chunk-max-tokens overrides it. Cheap/lazy, so build it for every method.
    embed_base_url = args.embedding_url[0] if args.embedding_url else None
    embed_api_key = args.embedding_api_key or os.getenv("OPENAI_API_KEY")
    # Main chunker token counter + budget are built by the shared factory
    # (build_chunker) below; here we only resolve the SEPARATE breakpoint-embed
    # budget + tokenizer when a distinct breakpoint endpoint is configured
    # breakpoint endpoint is configured (e.g. BGE-512 detecting boundaries while
    # SFR-4096 stores chunks). Count with the breakpoint model's OWN tokenizer when
    # its model name is known — a BPE stored counter (Mistral) undercounts vs a
    # wordpiece breakpoint model (BGE/BERT), so counting with the stored tokenizer
    # would still overflow the breakpoint context (HTTP 400). Explicit
    # --breakpoint-max-tokens wins; else resolve the breakpoint endpoint's window
    # (exact with its own tokenizer; padded down if we must reuse the stored one).
    breakpoint_max_tokens = None
    breakpoint_token_counter = None
    bp_urls = args.breakpoint_embedding_url
    if embed_bridge is not None and bp_urls:
        bp_model = args.breakpoint_embedding_model
        bp_key = args.breakpoint_embedding_api_key or embed_api_key
        if bp_model:
            try:
                breakpoint_token_counter = make_token_counter("hf", model=bp_model)
            except Exception as e:
                print(f"[ingest] breakpoint tokenizer for {bp_model!r} unavailable "
                      f"({type(e).__name__}); counting with the stored tokenizer.",
                      file=sys.stderr)
        if args.breakpoint_max_tokens is not None:
            breakpoint_max_tokens = args.breakpoint_max_tokens
        else:
            bp_window = resolve_max_tokens(None, base_url=bp_urls[0], api_key=bp_key)
            # Exact when counting with the bp tokenizer; otherwise pad hard for the
            # cross-tokenizer undercount.
            breakpoint_max_tokens = bp_window if breakpoint_token_counter else int(bp_window * 0.5)
    # Shared factory: resolves the main token counter + budget (fixed_token forces
    # hf; an hf/endpoint backend with no model REFUSES — it used to fall back to
    # estimate, which silently re-sized every chunk, so the estimator is now an
    # explicit --chunk-token-counter choice) and builds the chunker. Returns the
    # counter + budget for reuse below (doc-metrics writer, seg-cache fingerprint).
    chunker, token_counter, max_tokens = build_chunker(
        args.chunk_method,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        model=args.embedding_model,
        token_backend=args.chunk_token_counter,
        max_tokens=args.chunk_max_tokens,
        base_url=embed_base_url,
        api_key=embed_api_key,
        embed_fn=embed_bridge,
        buffer_size=args.chunk_buffer_size,
        breakpoint_percentile=args.chunk_breakpoint_percentile,
        min_chunk_length=args.chunk_min_length,
        breakpoint_max_tokens=breakpoint_max_tokens,
        breakpoint_token_counter=breakpoint_token_counter,
        # <=0 disables the oversized-doc fallback (None); else the span-count cap.
        max_breakpoint_sentences=(
            args.semantic_max_sentences if args.semantic_max_sentences > 0 else None
        ),
    )
    # Optional segmentation cache: store each doc's chunk spans keyed by
    # content+config, so a re-ingest rebuilds identical blocks from the cache
    # (reproducible regardless of embedding-backend jitter) and skips the
    # breakpoint embed. Keyed on the config that determines spans, so changing any
    # of it recomputes cleanly. Most valuable for the (embedding-based) semantic
    # methods; harmless for deterministic ones.
    seg_cache: SegmentationCache | None = None
    if args.segmentation_cache and not args.no_index:
        fp = config_fingerprint(
            method=args.chunk_method, buffer_size=args.chunk_buffer_size,
            pct=args.chunk_breakpoint_percentile, min_len=args.chunk_min_length,
            max_tokens=max_tokens, bp_max_tokens=breakpoint_max_tokens,
            bp_model=args.breakpoint_embedding_model or args.embedding_model,
            embed_model=args.embedding_model,
            # Threshold changes which docs fall back to fixed_token, changing spans.
            max_breakpoint_sentences=args.semantic_max_sentences,
            chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap,
        )
        seg_cache = SegmentationCache(args.segmentation_cache, fp)
        print(f"segmentation cache {args.segmentation_cache} "
              f"({len(seg_cache._spans)} cached spans loaded)", file=sys.stderr)
    ckpt_path, start_line, resume_done_ranges = _open_checkpoint_paths(args, current_doc_types)
    catalog = _open_catalog(args, start_line)
    stats = {"seen": 0, "skipped": 0, "docs": 0, "chunks": 0,
             "boilerplate_flagged": 0, "boilerplate_dropped": 0}
    # --boilerplate off|flag|drop. "flag" (the default) only *adds*
    # metadata["section"]/["is_boilerplate"] to licence / reference-list /
    # acknowledgement chunks: nothing is removed, so it cannot lose content, and
    # the corpus becomes measurable ("how much of this library is bibliography?")
    # and filterable at query time. "drop" additionally removes them before the
    # embed, which is what a scholarly-PDF corpus wants — but it is opt-in
    # because a false positive there is permanent for this ingest.
    # getattr: `args` is a hand-built Namespace in the unit tests, and a missing
    # attribute must mean the parser default ("flag"), never an AttributeError.
    bp_mode = getattr(args, "boilerplate", "flag")
    boilerplate = filter_from_mode(bp_mode, getattr(args, "boilerplate_config", ""))
    if boilerplate is not None:
        print(f"boilerplate detection: {bp_mode}", file=sys.stderr)
    # Per-document metrics (optional). Append on resume so a resumed run adds to
    # the rows already written rather than truncating them.
    doc_metrics: DocMetricsWriter | None = None
    if args.doc_metrics_out:
        doc_metrics = DocMetricsWriter(
            args.doc_metrics_out, token_counter, append=bool(args.resume and start_line)
        )
    run_started = time.monotonic()

    if args.no_index:
        await _run_catalog_only(
            args, ckpt_path, start_line, catalog, stats, keep_types, current_doc_types, profile
        )
        if catalog is not None:
            catalog.close()
            print(f"catalog written to {args.catalog_out}", file=sys.stderr)
        print(f"done: {stats['docs']} docs cataloged, {stats['skipped']} skipped "
              f"(saw {stats['seen']} records)", file=sys.stderr)
        if embed_bridge is not None:
            embed_bridge.close()
        if doc_metrics is not None:
            doc_metrics.close()  # catalog-only pass emits no per-doc chunk metrics
        if args.run_metrics_out:
            _write_run_metrics(
                args.run_metrics_out, input_file=str(args.input), stats=stats,
                failed_seqs=[], wall_s=time.monotonic() - run_started,
            )
        return

    # A saturated SFR fleet is slow, and httpx's 5 s default read timeout drops a
    # request mid-flight — surfacing as httpx.ReadError. That name IS in the
    # transient-retry set, but the semantic breakpoint-embed path runs inside
    # chunking, OUTSIDE --batch-retries, so a ReadError there crashes the whole run
    # rather than retrying. Give requests a generous window and force a fresh
    # connection per request so a stale-keepalive drop can't poison an in-flight
    # embed. (Salvaged from PR #144's hardening of the coupled ingest path.)
    async with httpx.AsyncClient(
        # getattr default so a hand-built Namespace (tests, embedded callers) that
        # predates the --embedding-timeout flag still works.
        timeout=httpx.Timeout(getattr(args, "embedding_timeout", 300.0), connect=30.0),
        limits=httpx.Limits(max_keepalive_connections=0),
    ) as http:
        embedder = _build_embedder(args, http)
        if len(args.embedding_url) > 1:
            print(f"embedding fan-out across {len(args.embedding_url)} endpoints",
                  file=sys.stderr)

        # Size the collection to the model's vector dim via a one-text probe, so
        # the store exists before the concurrent workers start.
        dim = len((await embedder.embed(["dimension probe"]))[0])
        if target is not None:
            # Every physical name comes from the registry entry (#263). The
            # auto-naming fallback below is what minted stores no registry ever
            # saw, so the CLI no longer reaches it — main() always resolves.
            target.check_build(dim=dim)
            qdrant_url, coll = target.qdrant_url, target.collection
            es_index = target.es_index
        else:
            qdrant_url, coll = args.qdrant_url, (
                args.collection or collection_name("ragstack", args.embedding_model, dim)
            )
            es_index = args.es_index
        store = QdrantVectorStore(
            url=qdrant_url, collection=coll, vector_size=dim, timeout=args.qdrant_timeout
        )
        await store.ensure_collection()
        print(f"qdrant collection {coll!r} ready (dim={dim})", file=sys.stderr)
        text_index = None
        if args.text_backend == "elasticsearch":
            text_index = ElasticsearchTextIndex(url=args.es_url, index=es_index)
            await text_index.ensure_index()
            print(f"elasticsearch index {es_index!r} ready", file=sys.stderr)

        # Concurrent pipeline: a producer streams fixed-size chunk batches onto a
        # bounded queue (constant memory on huge inputs); `concurrency` workers
        # embed+upsert in parallel, fanning out across the embedder pool. Each
        # batch carries a monotonic seq + the last record line it covers; the
        # checkpoint only advances over the contiguous *completed* prefix, so a
        # crash never records a line whose batch (or an earlier one) hadn't
        # finished — resume re-does from the first unfinished batch (idempotent,
        # deterministic chunk IDs overwrite in place).
        concurrency = max(1, args.concurrency)
        # --batch-retries: in-process transient-error retries per batch (default 0
        # = off, unchanged behaviour). getattr keeps older Namespaces safe.
        batch_retries = max(0, args.batch_retries)
        queue: asyncio.Queue = asyncio.Queue(maxsize=concurrency * 2)
        # seq -> (last record line, buffered catalog rows) for completed batches.
        completed: dict[int, tuple[int, list[str]]] = {}
        failed: list[int] = []  # seqs whose batch errored (checkpoint stalls at the gap)
        next_seq = 0
        # #65: batches complete out of order; when an early one is slow/failing the
        # contiguous-prefix frontier can't advance, so WITHOUT this the checkpoint
        # sticks at the head line and every restart re-embeds all the later batches
        # that already upserted. `done_ranges` durably records those out-of-order
        # completions (line intervals ABOVE the frontier) so a resume skips them;
        # `frontier_line` is the last persisted contiguous-prefix line. done_ranges
        # is optimization-only — a failed seq is never recorded, so its lines stay
        # in neither the frontier nor done_ranges and are always re-fed (no data
        # loss). Disabled under --replace, which must reprocess to prune orphans.
        done_ranges: list[list[int]] = list(resume_done_ranges)
        frontier_line = start_line
        track_done_ranges = not args.replace
        lock = asyncio.Lock()
        # Bound concurrent prune-deletes separately from the embed fan-out so a wide
        # --concurrency doesn't issue a burst of heavy deletes at the store.
        delete_sem = asyncio.Semaphore(max(1, args.delete_concurrency))

        async def _store_batch(chunks):
            """Embed + upsert (+ optional --replace prune) one batch, returning
            (kept, surviving-by-doc). Idempotent — upsert-first, deterministic
            uuid5 ids, no delete-before-upsert — so --batch-retries can re-run it
            after a transient failure without duplicating or losing data."""
            kept = await _embed_drop_bad(embedder, chunks)
            # Stamp prev/next/chunk_index per document on the SURVIVING chunks so
            # neighbor links never dangle to a quarantined chunk and a mixed-doc
            # batch never cross-links one doc's tail to the next doc's head.
            surviving = link_neighbors_by_document(kept)
            # Upsert FIRST. Deterministic chunk ids overwrite an unchanged doc's
            # points in place; a failure here never deletes anything (a prior
            # delete-before-upsert ordering lost data when a filtered delete on a
            # large collection timed out mid-batch: the delete landed, upsert didn't).
            if kept:
                await store.upsert(kept)
                if text_index is not None:
                    await text_index.index(kept)
            # Only an EDITED doc (shifted offsets → new chunk ids) leaves orphans;
            # prune only when asked (--replace) and only AFTER the upsert, by id,
            # with bounded concurrency so a wide fan-out can't burst heavy deletes.
            if args.replace and kept:
                by_doc: dict[str, set[str]] = {}
                for c in kept:
                    by_doc.setdefault(c.doc_id, set()).add(c.id)
                for doc_id, keep in by_doc.items():
                    async with delete_sem:
                        await store.delete_except(doc_id, keep, tenant_id=args.tenant)
                        if text_index is not None:
                            await text_index.delete_except(
                                doc_id, keep, tenant_id=args.tenant
                            )
            return kept, surviving

        async def worker() -> None:
            nonlocal next_seq, done_ranges, frontier_line
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    seq, start_ln, end_line, chunks, cat_rows, doc_info = item
                    try:
                        # --batch-retries: a TRANSIENT embed/store error (endpoint
                        # disconnect, timeout, 5xx) on a flapping endpoint can
                        # self-heal in place. _store_batch is idempotent, so re-run
                        # is safe; a 4xx/bad-input is NOT transient and surfaces at
                        # once. Exhaustion (or a non-transient error) re-raises to the
                        # failed-batch handler below → frontier stalls, --resume re-feeds.
                        attempt = 0
                        while True:
                            try:
                                kept, surviving = await _store_batch(chunks)
                                break
                            except Exception as e:
                                if attempt < batch_retries and is_transient_error(e):
                                    attempt += 1
                                    delay = retry_delay(attempt)
                                    print(f"  batch seq={seq} transient "
                                          f"{type(e).__name__} (retry {attempt}/"
                                          f"{batch_retries} in {delay:.1f}s): {e}",
                                          file=sys.stderr)
                                    await asyncio.sleep(delay)
                                    continue
                                raise
                        # Batch stored. Emit each doc's metrics ONCE here (after the
                        # retry loop) so a retried attempt never double-writes a row.
                        # A doc all of whose chunks were quarantined leaves no
                        # survivors -> a zero-chunk error row so it's not silently lost.
                        if doc_metrics is not None:
                            async with lock:
                                for d_id, src in doc_info.items():
                                    doc_chunks = surviving.get(d_id, [])
                                    err = (
                                        None if doc_chunks
                                        else "all chunks unembeddable (quarantined)"
                                    )
                                    doc_metrics.emit(
                                        d_id, src, doc_chunks, skipped=False, error=err
                                    )
                    except Exception as e:
                        # Don't kill the worker (or deadlock the producer): leave
                        # this seq out of `completed` so the checkpoint stalls at
                        # the gap and --resume reprocesses from here. Record it so
                        # the run reports failure (non-zero exit) instead of "done".
                        # NOTE: never union this seq's lines into done_ranges — a
                        # failed batch's lines must stay in neither the frontier nor
                        # done_ranges so resume always re-feeds them (no data loss).
                        print(f"  batch seq={seq} failed: {type(e).__name__}: {e}; "
                              f"will reprocess on --resume", file=sys.stderr)
                        err = f"batch seq={seq} failed: {type(e).__name__}: {e}"
                        async with lock:
                            failed.append(seq)  # under lock, like completed/next_seq
                            if doc_metrics is not None:
                                # Record every doc in the failed batch with the error;
                                # a later --resume re-runs the batch and appends fresh
                                # rows, so the failure is visible without losing docs.
                                for d_id, src in doc_info.items():
                                    doc_metrics.emit(d_id, src, [], skipped=False, error=err)
                        continue
                    async with lock:
                        completed[seq] = (end_line, cat_rows)
                        stats["chunks"] += len(kept)
                        advanced = None
                        while next_seq in completed:
                            end_line_n, rows_n = completed.pop(next_seq)
                            # Write this batch's catalog rows in seq order, in
                            # lockstep with the checkpoint, so the catalog never
                            # gets ahead of the resume point. Rows for batches that
                            # finished out of order wait in `completed` until the
                            # contiguous prefix reaches them.
                            if catalog is not None and rows_n:
                                catalog.write("".join(rows_n))
                            advanced = end_line_n
                            next_seq += 1
                        changed = False
                        if advanced is not None:
                            frontier_line = advanced
                            # The frontier now subsumes these lines: drop them from
                            # done_ranges so it can't grow unbounded once a gap clears.
                            trimmed = _trim_below(done_ranges, frontier_line)
                            if trimmed != done_ranges:
                                done_ranges = trimmed
                                changed = True
                        # If THIS batch finished above the frontier (a gap remains),
                        # durably record its line interval so a restart skips it
                        # instead of re-embedding it. Skipped under --replace.
                        if track_done_ranges and end_line > frontier_line:
                            unioned = _union_range(done_ranges, start_ln, end_line)
                            if unioned != done_ranges:
                                done_ranges = unioned
                                changed = True
                        if advanced is not None:
                            if catalog is not None:
                                catalog.flush()
                            _write_checkpoint(ckpt_path, frontier_line, current_doc_types, done_ranges)
                            print(f"  indexed {stats['chunks']} chunks / {stats['docs']} docs "
                                  f"(line {frontier_line})", file=sys.stderr)
                        elif changed:
                            # Out-of-order completion above a stuck gap: persist the
                            # new done_ranges at the unchanged frontier so the progress
                            # survives a restart (this is the #65 fix — no lost work).
                            _write_checkpoint(ckpt_path, frontier_line, current_doc_types, done_ranges)
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]

        seq = 0
        buf: list[Chunk] = []
        buf_catalog: list[str] = []  # catalog rows for the docs in `buf`
        buf_doc_info: dict[str, str] = {}  # doc_id -> source_file for docs in `buf`
        buf_start_line = 0  # first record line in the current batch (0 = empty batch)
        buf_end_line = 0
        last_line = 0  # highest processed line (kept or skipped), for the final checkpoint

        # --chunk-concurrency: chunk up to N documents at once (each fans its
        # breakpoint embeds across the pool), while a single coordinator (`_fold`)
        # folds the results into batches in STRICT FILE ORDER. Because seq /
        # buf_start_line / buf_end_line are only ever assigned inside `_fold`, which
        # runs items oldest-first, they stay monotonic and the #65 done_ranges
        # frontier is unaffected regardless of the order chunk() calls finish in.
        # Default 1 ≈ the prior single-in-flight behaviour (one doc prefetched).
        chunk_concurrency = max(1, args.chunk_concurrency)
        _max_doc_chars = max(0, int(getattr(args, "max_doc_chars", 0) or 0))
        chunk_sem = asyncio.Semaphore(chunk_concurrency)

        async def _chunk_task(doc: Document) -> list[Chunk]:
            # Off the event loop; the semaphore caps concurrent chunk() calls. The
            # embed bridge is safe for concurrent callers (one background loop);
            # seg_cache is thread-safe. On a cache hit the breakpoint embed is skipped.
            async with chunk_sem:
                if seg_cache is not None:
                    chunks = await asyncio.to_thread(
                        seg_cache.get_or_compute, doc, chunker.chunk
                    )
                else:
                    chunks = await asyncio.to_thread(chunker.chunk, doc)
            # Chunk-level boilerplate flag/drop, per document — which is also the
            # granularity BoilerplateFilter's all-boilerplate guard needs. Counted
            # into `stats` (and reported in --run-metrics-out and the final line)
            # rather than dropped silently the way _kept() drops EMPTY records.
            # NOTE: applied AFTER the segmentation cache reads/writes, so a cached
            # segmentation stays a pure function of the chunker config and toggling
            # the filter never invalidates the cache.
            if boilerplate is not None and chunks:
                result = boilerplate.apply(chunks)
                stats["boilerplate_flagged"] += sum(result.flagged.values())
                stats["boilerplate_dropped"] += result.dropped
                chunks = result.chunks
            return chunks

        async def _fold(item) -> None:
            # Fold one prepared item into the current batch. Called oldest-first, so
            # every seq/buf mutation here is strictly file-ordered.
            nonlocal seq, buf, buf_catalog, buf_doc_info, buf_start_line, buf_end_line
            kind, line_no = item[0], item[1]
            if kind == "skip":
                d_id, d_src = item[2], item[3]
                if doc_metrics is not None and d_id is not None:
                    doc_metrics.emit(d_id, d_src, [], skipped=True, error=None)
                return
            if kind == "resume":
                cat_row, d_id, d_src = item[2], item[3], item[4]
                if cat_row is not None:
                    if buf_start_line == 0:
                        buf_start_line = line_no
                    buf_catalog.append(cat_row)
                    buf_end_line = line_no
                if doc_metrics is not None and d_id is not None:
                    doc_metrics.emit(d_id, d_src, [], skipped=True,
                                     error="resumed (already indexed)")
                stats["docs"] += 1
            else:  # "chunk"
                task, doc_id, source, cat_row = item[2], item[3], item[4], item[5]
                chunks = await task
                for c in chunks:
                    c.metadata["tenant_id"] = args.tenant
                if buf_start_line == 0:
                    buf_start_line = line_no
                if cat_row is not None:
                    buf_catalog.append(cat_row)
                buf.extend(chunks)
                buf_doc_info[doc_id] = source
                buf_end_line = line_no
                stats["docs"] += 1
            if len(buf) >= args.batch_size or (
                catalog is not None and len(buf_catalog) >= args.batch_size
            ):
                await queue.put(
                    (seq, buf_start_line, buf_end_line, buf, buf_catalog, buf_doc_info)
                )
                seq += 1
                buf, buf_catalog, buf_doc_info = [], [], {}
                buf_start_line = 0

        inflight: deque = deque()
        window = chunk_concurrency + 1  # bound pending tasks/results (memory)
        dispatched_docs = 0
        # Producer wrapped so a chunk()/fold failure still shuts the workers down
        # cleanly (sentinels + gather) — completed batches keep their checkpoints and
        # no worker task is orphaned — before the error propagates.
        producer_exc: Exception | None = None
        try:
            with args.input.open(encoding="utf-8") as fh:
                for line_no, record in _iter_records(fh):
                    if line_no <= start_line:
                        continue
                    last_line = line_no
                    stats["seen"] += 1
                    enriched = enrich(record, profile=profile)
                    if _max_doc_chars and len(record.get("text", "") or "") > _max_doc_chars:
                        # A multi-MB data-table doc fans its semantic segmentation
                        # out into thousands of embeds, fails on every endpoint, and
                        # stalls the checkpoint — blocking every following doc in the
                        # file. Skip it like a filtered doc so the frontier advances.
                        stats["skipped"] += 1
                        n_chars = len(record.get("text", "") or "")
                        print(
                            f"  skip oversized doc at line {line_no}: {n_chars:,} chars "
                            f"> --max-doc-chars={_max_doc_chars:,}",
                            file=sys.stderr,
                        )
                        d_id = d_src = None
                        if doc_metrics is not None:
                            d_src = record.get("path", "") or ""
                            d_id = deterministic_doc_id(
                                _doc_id_key(record, record.get("text", "") or "")
                            )
                        inflight.append(("skip", line_no, d_id, d_src))
                    elif not _kept(enriched, keep_types):
                        # Filtered (doc_type/empty): no batch; a zero-chunk skipped
                        # metrics row (folded in file order).
                        stats["skipped"] += 1
                        d_id = d_src = None
                        if doc_metrics is not None:
                            d_src = record.get("path", "") or ""
                            d_id = deterministic_doc_id(
                                _doc_id_key(record, record.get("text", "") or "")
                            )
                        inflight.append(("skip", line_no, d_id, d_src))
                    elif track_done_ranges and _line_covered(line_no, start_line, resume_done_ranges):
                        # #65 resume fast-path: durably upserted in a prior run. Skip the
                        # expensive chunk+embed+upsert, but still buffer the cheap catalog
                        # row (folds in lockstep) and emit a "resumed" metrics row.
                        cat_row = (
                            json.dumps(enriched.model_dump(), ensure_ascii=False) + "\n"
                            if catalog is not None else None
                        )
                        d_id = d_src = None
                        if doc_metrics is not None:
                            d_src = record.get("path", "") or ""
                            d_id = deterministic_doc_id(
                                _doc_id_key(record, record.get("text", "") or "")
                            )
                        inflight.append(("resume", line_no, cat_row, d_id, d_src))
                        dispatched_docs += 1
                    else:
                        text = record.get("text", "") or ""
                        doc = Document(
                            id=deterministic_doc_id(_doc_id_key(record, text)),
                            content=text,
                            metadata=index_metadata(enriched),
                            source=record.get("path", "") or "",
                        )
                        cat_row = (
                            json.dumps(enriched.model_dump(), ensure_ascii=False) + "\n"
                            if catalog is not None else None
                        )
                        task = asyncio.create_task(_chunk_task(doc))
                        inflight.append(("chunk", line_no, task, doc.id, doc.source, cat_row))
                        dispatched_docs += 1
                    # Keep the in-flight window bounded; fold oldest-first (file order).
                    while len(inflight) > window:
                        await _fold(inflight.popleft())
                    if args.limit and dispatched_docs >= args.limit:
                        break
                while inflight:
                    await _fold(inflight.popleft())
            # Flush a trailing batch — including a catalog-only one (buf empty but
            # done-range resume rows buffered) so those rows still fold in lockstep.
            if buf or buf_catalog:
                await queue.put(
                    (seq, buf_start_line, buf_end_line, buf, buf_catalog, buf_doc_info)
                )
                seq += 1
        except Exception as e:
            # Cancel any still-pending chunk tasks so they don't leak, then fall to
            # the finally for an orderly worker drain; re-raise after.
            producer_exc = e
            for _it in inflight:
                if _it[0] == "chunk":
                    _it[2].cancel()
        finally:
            for _ in workers:  # one sentinel per worker
                await queue.put(None)
            await asyncio.gather(*workers)
        if producer_exc is not None:
            raise producer_exc

        # All batches done with no gap (next_seq caught up to the batch count):
        # advance the checkpoint over any trailing skipped lines after the last
        # kept doc, so a resume of a finished run doesn't re-scan them. A gap
        # (failed batch) leaves next_seq < seq, so the checkpoint correctly stalls.
        # A clean finish folds every batch into the frontier, so done_ranges is
        # empty here (trimmed away); persist it explicitly to clear any stale ranges
        # left by a prior resumed run.
        if next_seq == seq:
            _write_checkpoint(ckpt_path, last_line, current_doc_types, _trim_below(done_ranges, last_line))

        if text_index is not None and hasattr(text_index, "close"):
            await text_index.close()

    if catalog is not None:
        catalog.close()
        print(f"catalog written to {args.catalog_out}", file=sys.stderr)
    if embed_bridge is not None:
        embed_bridge.close()
    if seg_cache is not None:
        print(f"segmentation cache: {seg_cache.hits} hit / {seg_cache.misses} miss "
              f"(hits skipped the breakpoint embed)", file=sys.stderr)
        seg_cache.close()
    # Emit the per-file run summary + close the per-doc writer BEFORE the failure
    # exit below, so a partial (failed-batch) run still leaves its metrics behind.
    if doc_metrics is not None:
        doc_metrics.close()
    if args.run_metrics_out:
        _write_run_metrics(
            args.run_metrics_out, input_file=str(args.input), stats=stats,
            failed_seqs=failed, wall_s=time.monotonic() - run_started,
        )
    if failed:
        # Don't report success when batches errored: the checkpoint stalled at the
        # first gap, so the run is partial. Exit non-zero so the operator notices.
        print(f"FAILED: {len(failed)} batch(es) errored (seq {sorted(failed)}); "
              f"checkpoint stalled at the first gap — re-run with --resume to retry.",
              file=sys.stderr)
        raise SystemExit(1)
    # Verified provenance for this collection — same module the API + the
    # per-chunk ingester use, so a bulk-, script-, or API-built collection is
    # described identically. Skipped for catalog-only (--no-index) runs.
    # getattr default so a hand-built Namespace (the reingest tests, embedded
    # callers) that predates --manifest-dir still works.
    manifest_dir = getattr(args, "manifest_dir", "") or os.getenv("COLLECTION_MANIFEST_DIR", "")
    if manifest_dir and not args.no_index:
        params: dict[str, Any] = {}
        if args.chunk_method in ("semantic", "semantic_pooled"):
            params = {
                "breakpoint_percentile": args.chunk_breakpoint_percentile,
                "buffer_size": args.chunk_buffer_size,
                "min_chunk_length": args.chunk_min_length,
            }
        # chunk_count is the WHOLE collection's chunk count for this tenant — a
        # resumed run's stats["chunks"] counts only the segment it processed, so
        # querying the store gives the true total (falls back to the run tally).
        total_chunks = stats["chunks"]
        try:
            total_chunks = await store.count_tenants([args.tenant])
        except Exception:  # noqa: BLE001 — provenance count is best-effort
            pass
        if target is not None:
            # From the registry entry, so a bulk-built and an API-built store are
            # described identically — and so a later API ingest with a different
            # chunker has something to be refused by (ADR-0002).
            spec_hash = target.write_manifest(
                manifest_dir,
                embedding_api=args.embedding_api,
                embedding_endpoints=list(args.embedding_url),
                corpus=str(args.input),
                chunk_count=total_chunks,
            )
        else:
            manifest = make_ingest_manifest(
                collection=coll,
                model=args.embedding_model or "",
                dim=dim,
                embedding_api=args.embedding_api,
                embedding_endpoints=list(args.embedding_url),
                chunk_method=args.chunk_method,
                chunk_size=args.chunk_size,
                chunk_overlap=args.chunk_overlap,
                chunk_params=params,
                corpus=str(args.input),
                chunk_count=total_chunks,
            )
            write_manifest(manifest_dir, manifest)
            spec_hash = manifest.spec_hash
        print(f"wrote provenance manifest ({spec_hash}) to {manifest_dir}",
              file=sys.stderr)

    print(f"done: {stats['docs']} docs indexed, {stats['skipped']} skipped, "
          f"{stats['chunks']} chunks (saw {stats['seen']} records)", file=sys.stderr)
    if stats.get("boilerplate_flagged") or stats.get("boilerplate_dropped"):
        print(f"boilerplate: {stats['boilerplate_flagged']} chunks flagged, "
              f"{stats['boilerplate_dropped']} dropped", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("input", type=Path, help="JSONL file (one {text,path,metadata} per line)")
    p.add_argument("--tenant", default="public",
                   help="tenant_id stamped on every chunk (default: public = world-readable)")
    p.add_argument("--doc-types", nargs="+", default=None,
                   help="only ingest these doc_type classes (default: all non-empty). "
                        "e.g. --doc-types article supplement")
    p.add_argument("--boilerplate", choices=["off", "flag", "drop"], default="flag",
                   help="chunk-level boilerplate handling: off | flag (stamp "
                        "metadata.section/is_boilerplate, default) | drop (also "
                        "exclude licence/reference/acknowledgement chunks from the "
                        "index). Counts are reported at end-of-run and in "
                        "--run-metrics-out; a document that is ENTIRELY boilerplate "
                        "is never emptied.")
    p.add_argument("--boilerplate-config", default="",
                   help="JSON object overriding BoilerplateConfig thresholds, e.g. "
                        "'{\"reference_density\": 15}'")
    p.add_argument("--publisher-profile", default="asm",
                   help="enrichment profile (DOI prefix / filename rule / front-matter set); "
                        "unknown names fall back to the default. See enrich.PROFILES")
    p.add_argument("--limit", type=int, default=0, help="stop after N ingested docs (0 = all)")
    # outputs
    p.add_argument("--catalog-out", type=Path, default=None,
                   help="write full per-doc enriched metadata (incl. citations) here")
    p.add_argument("--doc-metrics-out", type=Path, default=None,
                   help="write ONE JSONL row per DOCUMENT here: "
                        "{doc_id, source_file, n_chunks, tokens_min, tokens_median, "
                        "tokens_max, chunk_chars_median, skipped, error}. Token stats "
                        "come from the same TokenCounter used for token-cap sizing. "
                        "Appends on --resume. No behaviour change when unset.")
    p.add_argument("--run-metrics-out", type=Path, default=None,
                   help="append ONE per-FILE summary row at end-of-run here: "
                        "{file, docs_seen, docs_indexed, docs_skipped, chunks, "
                        "failed_batches, failed_batch_seqs, wall_s, chunks_per_s}. "
                        "No behaviour change when unset.")
    p.add_argument("--no-index", action="store_true",
                   help="skip embedding/upsert; only build the catalog")
    # vector store
    p.add_argument("--qdrant-url", default="http://localhost:6333")
    p.add_argument("--qdrant-timeout", type=int, default=120,
                   help="per-request Qdrant timeout in seconds (default: %(default)s)")
    p.add_argument("--collection", default=None,
                   help="DEPRECATED — the PHYSICAL store name. Accepted only when a "
                        "registry entry already claims it; prefer --collection-id")
    p.add_argument("--replace", action="store_true",
                   help="prune orphan chunks of EDITED docs after upsert (upsert-then-prune, by id). "
                        "Default off = upsert-only, which is correct for unchanged re-ingest and never "
                        "deletes data on failure.")
    p.add_argument("--delete-concurrency", type=int, default=4,
                   help="max concurrent prune-deletes under --replace (default: %(default)s)")
    # text index (BM25)
    p.add_argument("--text-backend", choices=["none", "elasticsearch"], default="none")
    p.add_argument("--es-url", default="http://localhost:9200")
    p.add_argument("--es-index", default="ragstack")
    # embedding
    p.add_argument("--embedding-api", choices=["sidecar", "openai"], default="sidecar")
    p.add_argument("--embedding-url", nargs="+", default=["http://localhost:50053"],
                   help="embedding service URL(s); pass several to load-balance with failover")
    p.add_argument("--embedding-model", default=None,
                   help="model name (required for --embedding-api openai; used to name the collection)")
    p.add_argument("--embedding-max-concurrency", type=int, default=8)
    p.add_argument("--embedding-timeout", type=float, default=300.0,
                   help="per-request embedding HTTP timeout in seconds (default: "
                        "%(default)s). The httpx default (5s) is too tight for a "
                        "saturated SFR fleet and surfaces as ReadError crashes.")
    p.add_argument("--embedding-api-key", default=None,
                   help="Bearer token sent as 'Authorization: Bearer <key>' to every "
                        "--embedding-url (keyless endpoints ignore it, so one key is safe "
                        "for a mixed pool). Falls back to $OPENAI_API_KEY.")
    # Optional SEPARATE backend for the semantic breakpoint pass (topic-boundary
    # detection), so it can run on a cheaper/GPU model while stored chunks stay on
    # the main --embedding-* model. Each falls back to its --embedding-* counterpart
    # when unset; leaving --breakpoint-embedding-url unset = use the main embedder
    # (no behaviour change). Only used by --chunk-method semantic / semantic_pooled.
    p.add_argument("--breakpoint-embedding-api", choices=["sidecar", "openai"], default=None,
                   help="breakpoint embedder API (default: --embedding-api)")
    p.add_argument("--breakpoint-embedding-url", nargs="+", default=None,
                   help="breakpoint embedding URL(s); set to route boundary detection to "
                        "a separate (e.g. GPU-served BGE) model. Default: reuse --embedding-url")
    p.add_argument("--breakpoint-embedding-model", default=None,
                   help="breakpoint model name (default: --embedding-model)")
    p.add_argument("--breakpoint-embedding-max-concurrency", type=int, default=None,
                   help="breakpoint embedder max concurrency (default: --embedding-max-concurrency)")
    p.add_argument("--breakpoint-embedding-api-key", default=None,
                   help="breakpoint embedder Bearer token (default: --embedding-api-key)")
    p.add_argument("--breakpoint-max-tokens", type=int, default=None,
                   help="token budget for breakpoint-embed inputs when the breakpoint "
                        "model has a smaller context than the stored model (e.g. BGE 512). "
                        "Default: auto from the breakpoint endpoint's window (exact when its "
                        "own tokenizer is loaded, else padded down for the tokenizer mismatch).")
    p.add_argument("--segmentation-cache", type=Path, default=None,
                   help="cache each document's chunk SPANS to this JSONL file keyed by "
                        "content+segmentation-config. A re-ingest rebuilds identical blocks "
                        "from the cache (reproducible regardless of embedding-backend jitter) "
                        "and skips the breakpoint embed. Ignored under --no-index.")
    # chunking
    p.add_argument("--chunk-method",
                   choices=["fixed", "fixed_token", "sentence", "words", "semantic",
                            "semantic_pooled"],
                   default="fixed",
                   help="chunking strategy (default: fixed). 'fixed_token' is a "
                        "sliding TOKEN window: --chunk-size/--chunk-overlap are "
                        "interpreted as TOKENS (of the --embedding-model tokenizer), "
                        "not chars. 'semantic' embeds sentence buffers via the "
                        "configured --embedding-* backend. 'semantic_pooled' embeds "
                        "each sentence ONCE and mean-pools the buffer window "
                        "(~buffer-window× less embedding work, deterministic blocks) — "
                        "pair with --breakpoint-embedding-* to run it on a cheap model.")
    p.add_argument("--chunk-size", type=int, default=512,
                   help="chunk size (chars for fixed/sentence/words; TOKENS for "
                        "fixed_token)")
    p.add_argument("--chunk-overlap", type=int, default=64,
                   help="chunk overlap (chars for fixed/sentence/words; TOKENS for "
                        "fixed_token)")
    # Semantic-only tunables (ignored by other methods).
    p.add_argument("--chunk-buffer-size", type=int, default=3,
                   help="semantic: sentences of context on each side of a buffer")
    p.add_argument("--chunk-breakpoint-percentile", type=float, default=80.0,
                   help="semantic: distance percentile above which a chunk boundary is placed")
    p.add_argument("--chunk-min-length", type=int, default=500,
                   help="semantic: merge chunks shorter than this many chars into a neighbor")
    p.add_argument("--semantic-max-sentences", type=int, default=3000,
                   help="semantic: OVERSIZED-DOC FALLBACK. A doc that splits into more "
                        "than this many sentence spans is chunked with the fixed_token "
                        "sliding window instead of the per-span breakpoint embed, so a "
                        "giant data-table doc can't flood the embedding fleet. Default "
                        "3000 keeps normal/large prose semantic (corpus p99 ~25k tokens "
                        "is only ~1-2k spans). Pass 0 or a negative value to DISABLE the "
                        "fallback (always attempt semantic).")
    # Token-based sizing: keep every chunk within the embedder's context window.
    p.add_argument("--chunk-max-tokens", type=int, default=None,
                   help="the embedding model's context window in tokens. The chunker "
                        "keeps a small reserve below it (for BOS/EOS specials) and caps "
                        "every chunk to that budget. Default None = auto-detect the "
                        "window from the endpoint's max_model_len (falls back to 4096 if "
                        "it can't be probed). A given value is treated as the window, so "
                        "the reserve is subtracted from it too.")
    p.add_argument("--chunk-token-counter", choices=["hf", "endpoint", "estimate"],
                   default="hf",
                   help="how to count tokens: 'hf' loads the embedding model's "
                        "AutoTokenizer (exact, default), 'endpoint' POSTs /tokenize to the "
                        "embedding URL, 'estimate' uses a chars/token heuristic (zero deps). "
                        "'hf'/'endpoint' need --embedding-model; without it sizing falls "
                        "back to 'estimate'.")
    p.add_argument("--batch-size", type=int, default=128, help="chunks embedded/upserted per batch")
    p.add_argument("--batch-retries", type=int, default=0,
                   help="in-process retries for a batch that hits a TRANSIENT embed/"
                        "store error (disconnect/timeout/5xx) before deferring to "
                        "--resume; exponential backoff (1s,2s,4s… capped 30s). The "
                        "batch body is idempotent (upsert-first, deterministic ids), "
                        "so retry is safe. Default 0 = off. Helps a flapping endpoint "
                        "converge (rc=0) without a full restart.")
    p.add_argument("--concurrency", type=int, default=1,
                   help="in-flight batches embedded+upserted in parallel; set >1 to fan out "
                        "across multiple --embedding-url endpoints (default: 1 = serial)")
    p.add_argument("--chunk-concurrency", type=int, default=1,
                   help="documents chunked concurrently for the semantic methods (each "
                        "fans its breakpoint embeds across the pool); a single coordinator "
                        "folds results in strict file order so the resume checkpoint is "
                        "unaffected. Set >1 to saturate a breakpoint-model fleet (e.g. "
                        "several BGE replicas). Default 1.")
    ingest_target.add_arguments(p)
    p.add_argument("--manifest-dir", default="",
                   help="write a provenance manifest for the collection here "
                        "(defaults to $COLLECTION_MANIFEST_DIR; skipped if neither is set "
                        "or under --no-index)")
    p.add_argument("--max-doc-chars", type=int, default=0,
                   help="skip (do not embed) any document whose text exceeds this many "
                        "characters. 0 = no limit (default). Guards against multi-MB "
                        "data-table docs whose semantic segmentation fans out thousands "
                        "of embeds, fails on every endpoint, and stalls the checkpoint — "
                        "blocking all following docs in the file. Skipped docs are counted "
                        "and the checkpoint advances past them, like a filtered doc.")
    # resume
    p.add_argument("--resume", action="store_true", help="skip lines up to the checkpoint")
    p.add_argument("--checkpoint", type=Path, default=None,
                   help="checkpoint file (default: <input>.ckpt)")
    args = p.parse_args()
    # Resolve (and if asked, create) the registry entry before any I/O (#263).
    # --no-index is catalog-only: it writes to no store, so it registers nothing.
    target = None if args.no_index else ingest_target.resolve_or_exit(
        args,
        model=args.embedding_model,
        chunk_method=args.chunk_method,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )
    asyncio.run(run(args, target))


if __name__ == "__main__":
    main()
