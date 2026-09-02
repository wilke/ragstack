#!/usr/bin/env python
"""Compare chunking modes (fixed / sentence / semantic) for retrieval quality + cost.

A self-contained operator/eval harness (not wired into the API). It ingests the
*same* fixed subset of article documents three ways — once per ``chunk_method`` —
into isolated Qdrant collections + Elasticsearch indices, then measures known-item
retrieval quality (recall@k / MRR@10 / nDCG@10, hybrid and reranked) and chunk
structure/cost per mode, and writes a markdown report.

It uses the package internals **directly** (chunkers, stores, retriever, reranker)
rather than shelling out to ``ingest_jsonl.py`` so the three modes share one
chunk/index/retrieve code path and one subset. The embedding layer is the
exception: it's a self-contained async fan-out over the four SFR endpoints (with
its own retry / 400-bisect / oversize-shrink), because the harness needs to
*measure* the oversize-cap count and guarantee no chunk is dropped — eval-specific
semantics the production embedder path doesn't express.

Embedding backend is the production SFR/4096 model served by four vLLM endpoints
(``http://localhost:9001..9004``, OpenAI ``/v1/embeddings`` API). SFR's max context
is 4096 tokens, so oversized chunks (notably from ``semantic``, whose chunks are
unbounded) are split to a safe char budget before embedding — that cap count is
reported as a real cost of the mode.

Idempotent: deterministic chunk ids mean a re-run upserts in place. Teardown
(default ON) drops the three ``chunkcmp_*`` collections/indices at the end. The
production SFR corpus (``ragstack_salesforce_sfr_embedding_mistral_4096_928f8ebe``
/ ES ``ragstack_sfr``) uses different names and is never touched.

Usage::

    cd python
    . /rag/bin/activate
    python scripts/eval/chunking_compare.py \
        --qdrant-url http://QDRANT-HOST:PORT --es-url http://ES-HOST:PORT
    python scripts/eval/chunking_compare.py --limit 50 --no-teardown \
        --qdrant-url http://QDRANT-HOST:PORT --es-url http://ES-HOST:PORT  # smoke

``--qdrant-url`` / ``--es-url`` are REQUIRED and have no default: this harness
creates and drops collections, and the localhost literals it used to carry are
production on the deployment host (#476).
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import math
import os
import random
import statistics
import sys
import threading
import time
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from ragstack.embedders import make_embedder
from ragstack.ingestion.chunkers import make_chunker, split_text_to_token_budget
from ragstack.ingestion.enrich import ARTICLE, enrich, index_metadata
from ragstack.ingestion.loaders import deterministic_doc_id
from ragstack.ingestion.tokenization import (
    TokenCounter,
    make_token_counter,
    resolve_max_tokens,
)
from ragstack.models import Chunk, Document
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.scoring.scorers import SidecarReranker
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.qdrant import QdrantVectorStore
from ragstack.tenancy import scope_filters

# --------------------------------------------------------------------------- #
# Configuration constants
# --------------------------------------------------------------------------- #
#: Where the ASM source JSONL lives. A DIRECTORY, not a file: the corpus was
#: consolidated into one canonical place (40 files, 448,650 documents, see its
#: MANIFEST.tsv), and the two duplicate copies this script used to name a file
#: inside were deleted. Naming one shard also silently scoped the comparison to
#: ~8% of the corpus, which is not what "the ASM corpus" means.
ASM_CORPUS_DIR = "/rag/ingest/docs/asm"


def discover_corpus(directory: str = ASM_CORPUS_DIR) -> list[str]:
    """Every ``*.jsonl`` shard in ``directory``, sorted for determinism.

    Sorted because an unordered glob makes a re-run a different experiment:
    the eval takes the first ``--limit`` documents, so listing order decides
    the sample. Raises rather than silently comparing nothing when the corpus
    is absent — an empty run reports zeros that look like a result.
    """
    import glob as _glob

    files = sorted(_glob.glob(os.path.join(directory, "*.jsonl")))
    if not files:
        raise SystemExit(
            f"{directory}: no *.jsonl found. Pass --input/--inputs explicitly, "
            "or point at the consolidated ASM corpus directory."
        )
    return files
SFR_ENDPOINTS = [
    "http://localhost:9001",
    "http://localhost:9002",
    "http://localhost:9003",
    "http://localhost:9004",
]
SFR_MODEL = "Salesforce/SFR-Embedding-Mistral"
# Bearer token sent to the embedding endpoints (set from --embedding-api-key /
# $OPENAI_API_KEY in main()). None = no Authorization header. Keyless endpoints
# (e.g. coconut :9001-9008) ignore the header, so sending one key to a mixed pool
# of keyless + token-authed endpoints (e.g. lambda13 :9990-9997) is safe.
EMBED_API_KEY: str | None = None
VECTOR_SIZE = 4096
# Store targets — deliberately None, NOT localhost literals: this harness creates
# and drops collections, and on the deployment host the localhost addresses are the
# PRODUCTION Qdrant/ES. main() sets them from REQUIRED --qdrant-url/--es-url; the
# ``store_urls`` guard below turns "nobody set them" into a refusal, because
# ``QdrantClient(url=None)`` does NOT fail — it falls back to localhost:6333 (#476).
QDRANT_URL: str | None = None
ES_URL: str | None = None
RERANKER_URL = "http://localhost:50052"
TENANT = "public"
MODES = ("fixed", "sentence", "semantic")
# Collection/index prefix for this run's isolated stores. Always begins with
# ``chunkcmp`` so the teardown guard (assert startswith "chunkcmp") still protects
# the production corpus. A non-default suffix (``--collection-prefix``) lets two
# harness runs share one Qdrant/ES without colliding on the same collections.
DEFAULT_PREFIX = "chunkcmp"
# Per-run prefix, set from args in main(); functions read it via _store_name().
_PREFIX = DEFAULT_PREFIX


def _store_name(mode: str) -> str:
    """Qdrant collection / ES index name for ``mode`` under the active prefix."""
    return f"{_PREFIX}_{mode}"


def store_urls() -> tuple[str, str]:
    """``(QDRANT_URL, ES_URL)``, or a refusal naming the flags that set them.

    Every store client here is built from this rather than from the globals:
    ``QdrantClient(url=None)`` silently falls back to ``localhost:6333`` —
    production on the deployment host — so an unset target has to be caught by
    name here (#476)."""
    if not QDRANT_URL or not ES_URL:
        raise SystemExit(
            "store URLs unset — pass --qdrant-url and --es-url (required; there is "
            "no default, because the default would be production on the deployment "
            f"host). Currently: QDRANT_URL={QDRANT_URL!r} ES_URL={ES_URL!r}"
        )
    return QDRANT_URL, ES_URL


# SFR's context window is 4096 tokens. Rather than approximate it with a char cap
# (this corpus is dense scientific text at ~2.45 chars/token, and some passages —
# tables, references, formulae — are denser still), chunks are sized/capped by
# *tokens*: any chunk over MAX_TOKENS is split to <=MAX_TOKENS pieces by tokens
# before embedding (text preserved, just re-segmented). MAX_TOKENS + TOKEN_COUNTER
# are set from the live endpoint in main(). A defensive bisect + iterative shrink
# still guards the rare batch that trips a 400 so a single chunk can't abort the run.
MAX_TOKENS = VECTOR_SIZE  # placeholder; overwritten from the endpoint in main()
TOKEN_COUNTER: TokenCounter | None = None

EMBED_BATCH = 64
EMBED_CONCURRENCY = 8  # bounded in-flight embed requests across endpoints
EMBED_RETRIES = 4
# Number of documents whose semantic buffer-embeds run concurrently. Each doc's
# embed_fn already fans its buffer batches across all four endpoints, so a small
# pool of docs in flight keeps every GPU busy without oversubscribing them.
SEMANTIC_DOC_WORKERS = 4
# Bounded concurrency for the known-item eval loop (independent queries, each a
# few network round-trips: Qdrant + ES retrieval + a crossencoder rerank call).
EVAL_CONCURRENCY = 12

REPORT_PATH = Path(__file__).resolve().parent / "chunking_compare_report.md"
CSV_PATH = Path(__file__).resolve().parent / "chunking_compare_results.csv"
# Per-mode ingest stats are checkpointed here so a --resume run (e.g. after a
# long ingest is interrupted) reuses already-ingested modes instead of re-paying.
STATS_PATH = Path(__file__).resolve().parent / ".chunking_compare_ingest_stats.json"


# --------------------------------------------------------------------------- #
# Subset selection
# --------------------------------------------------------------------------- #
def load_subset(input_path: str, limit: int) -> list[Document]:
    """Stream the JSONL corpus; take the first ``limit`` ARTICLE records with a
    non-empty title. Returns one Document per record (the shared subset for all
    modes). The doc id matches the production ingest path: deterministic_doc_id of
    the resolved source path.
    """
    docs: list[Document] = []
    path = Path(input_path)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            enriched = enrich(record)
            if enriched.doc_type != ARTICLE or not enriched.title:
                continue
            text = record.get("text", "") or ""
            if not text:
                continue
            src = enriched.source_path
            doc_id = deterministic_doc_id(str(Path(src).resolve()))
            meta = index_metadata(enriched)
            meta["tenant_id"] = TENANT
            docs.append(Document(id=doc_id, content=text, metadata=meta, source=src))
            if len(docs) >= limit:
                break
    return docs


# --------------------------------------------------------------------------- #
# Embedding (async, bounded, retrying, round-robin over the 4 SFR endpoints)
# --------------------------------------------------------------------------- #
async def _post_embeddings(
    client: httpx.AsyncClient, base_url: str, texts: list[str]
) -> list[list[float]]:
    """One raw embeddings POST with retries on *transient* (non-400) errors.

    A 400 (token-limit / bad input) is raised immediately so the caller can
    bisect/truncate instead of retrying an unfixable request."""
    headers = (
        {"Authorization": f"Bearer {EMBED_API_KEY}"} if EMBED_API_KEY else None
    )
    last_exc: Exception | None = None
    for attempt in range(EMBED_RETRIES):
        try:
            resp = await client.post(
                f"{base_url}/v1/embeddings",
                json={"input": texts, "model": SFR_MODEL},
                headers=headers,
                timeout=300.0,
            )
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda item: item["index"])
            return [item["embedding"] for item in data]
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 400:
                raise
            last_exc = exc
            await asyncio.sleep(1.5 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001 - transient embed errors are retried
            last_exc = exc
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"embed batch failed after {EMBED_RETRIES} retries: {last_exc}")


async def _embed_one_batch(
    client: httpx.AsyncClient,
    base_url: str,
    texts: list[str],
    sem: asyncio.Semaphore,
) -> list[list[float]]:
    """Embed one batch, bisecting on a 400 to isolate an over-budget input and, as
    a last resort, truncating a single offending text by char caps so the run never
    aborts. With token-based sizing this path should essentially never fire (chunks
    are pre-capped to the token budget); it remains a defensive backstop.
    Order-preserving."""
    async with sem:
        try:
            return await _post_embeddings(client, base_url, texts)
        except httpx.HTTPStatusError as exc:
            if exc.response is None or exc.response.status_code != 400:
                raise
    # 400: an input is over the token budget. Bisect to find it.
    if len(texts) == 1:
        # Single over-budget input: descend through absolute char caps until it
        # embeds. A fixed-ratio shrink with a 512-char floor can stall (512 chars
        # of dense reference text can still exceed 4096 tokens), so we step down
        # to a hard 200-char floor — well under 4096 tokens for any text — which
        # guarantees convergence. This path only happens for pathological inputs
        # like a single multi-page "sentence" buffer with no sentence breaks;
        # truncating it only affects breakpoint detection, not stored chunk text.
        text = texts[0]
        for cap in (3000, 2000, 1200, 800, 400, 200):
            cand = text[:cap]
            try:
                async with sem:
                    vecs = await _post_embeddings(client, base_url, [cand])
                print(
                    f"[embed] shrank over-budget chunk to {len(cand)} chars "
                    f"to fit the SFR token window",
                    flush=True,
                )
                return vecs
            except httpx.HTTPStatusError as exc:
                if exc.response is None or exc.response.status_code != 400:
                    raise
        raise RuntimeError(
            "could not shrink chunk under the SFR token budget even at 200 chars"
        )
    mid = len(texts) // 2
    left = await _embed_one_batch(client, base_url, texts[:mid], sem)
    right = await _embed_one_batch(client, base_url, texts[mid:], sem)
    return left + right


# Global round-robin offset so that *concurrent* embed_texts_async calls (e.g. the
# parallel semantic per-doc buffer embeds) don't all start their first batch on the
# same endpoint and pile onto a single GPU. itertools.count is atomic under CPython.
_RR_COUNTER = itertools.count()
_RR_LOCK = threading.Lock()


def _next_rr_offset() -> int:
    with _RR_LOCK:
        return next(_RR_COUNTER)


async def embed_texts_async(
    client: httpx.AsyncClient, texts: list[str]
) -> list[list[float]]:
    """Embed ``texts`` in batches of EMBED_BATCH, round-robin across endpoints,
    with bounded concurrency. Returns vectors aligned to ``texts``."""
    if not texts:
        return []
    sem = asyncio.Semaphore(EMBED_CONCURRENCY)
    base = _next_rr_offset()
    batches: list[tuple[int, list[str]]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batches.append((start, texts[start : start + EMBED_BATCH]))
    tasks = [
        _embed_one_batch(
            client, SFR_ENDPOINTS[(base + i) % len(SFR_ENDPOINTS)], batch, sem
        )
        for i, (_, batch) in enumerate(batches)
    ]
    results = await asyncio.gather(*tasks)
    out: list[list[float]] = []
    for vecs in results:
        out.extend(vecs)
    return out


def make_sync_embed_fn():
    """A SYNC embed function for the semantic chunker's buffer embeddings that fans
    out across **all four** SFR endpoints concurrently.

    SemanticChunker.chunk() is synchronous and calls ``embed_fn(buffers)`` once per
    document with every sentence-buffer of that doc. The original implementation
    posted those buffers to a single endpoint in serial EMBED_BATCH batches, so the
    semantic pass used only one of the four GPUs and dominated wall-time (~30 min).

    Here ``embed_fn`` instead drives the same async, round-robin, bounded-concurrency
    embedder used for the final chunk embeddings (``embed_texts_async``), which
    splits the buffers into EMBED_BATCH batches and POSTs them concurrently across
    ``:9001-9004`` with EMBED_CONCURRENCY in flight. It inherits the transient-retry
    and 400-bisect/shrink behaviour for free. Vectors are returned in input order.

    Because ``chunk()`` is sync, we run an internal event loop via ``asyncio.run``.
    A fresh ``AsyncClient`` is created per call so the function is safe to invoke
    from worker threads (semantic docs are chunked concurrently): each thread runs
    its own loop and owns its own client. ``asyncio.run`` requires that no event
    loop is already running in the calling thread, which holds for the worker
    threads and for the ingest loop's default thread alike (the ingest loop does
    not ``await`` ``chunk()``; it offloads it to the executor).
    """

    def _embed(buffers: Sequence[str]) -> list[list[float]]:
        texts = list(buffers)
        if not texts:
            return []

        async def _run() -> list[list[float]]:
            timeout = httpx.Timeout(300.0, connect=30.0)
            limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
            async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
                return await embed_texts_async(client, texts)

        return asyncio.run(_run())

    return _embed


# --------------------------------------------------------------------------- #
# Oversize cap
# --------------------------------------------------------------------------- #
def cap_oversized(chunks: list[Chunk]) -> tuple[list[Chunk], int]:
    """Split any chunk whose content exceeds the token budget (MAX_TOKENS) into
    <=budget pieces by tokens (TOKEN_COUNTER). All text is preserved; doc_id and
    deterministic ids are kept. Returns (capped_chunks, n_oversized).

    Token-based replacement for the old SAFE_CHUNK_CHARS char cap: it guarantees
    no chunk exceeds the embedder's *token* context, which a char cap could only
    approximate. The split pieces tile each oversized chunk gaplessly, so the
    re-derived ids (uuid5 of doc_id:start:end relative to the sub-document) stay
    deterministic and re-runnable."""
    assert TOKEN_COUNTER is not None, "TOKEN_COUNTER must be set before cap_oversized"
    out: list[Chunk] = []
    n_oversized = 0
    for c in chunks:
        if TOKEN_COUNTER.count(c.content) <= MAX_TOKENS:
            out.append(c)
            continue
        n_oversized += 1
        # Split the oversized content by tokens; pieces tile the chunk gaplessly,
        # so each maps to a contiguous [cursor, cursor+len) char range with a
        # deterministic id (uuid5 of doc_id:start:end), re-runnable across runs.
        pieces = split_text_to_token_budget(c.content, MAX_TOKENS, TOKEN_COUNTER)
        cursor = 0
        for piece in pieces:
            out.append(
                Chunk(
                    id=str(uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{c.doc_id}:{cursor}:{cursor + len(piece)}",
                    )),
                    doc_id=c.doc_id,
                    content=piece,
                    metadata=dict(c.metadata),
                    start_char=cursor,
                    end_char=cursor + len(piece),
                )
            )
            cursor += len(piece)
    return out, n_oversized


# --------------------------------------------------------------------------- #
# Ingest one mode
# --------------------------------------------------------------------------- #
async def ingest_mode(
    mode: str,
    docs: list[Document],
    client: httpx.AsyncClient,
    chunk_size: int,
    chunk_overlap: int,
    resume: bool = False,
) -> dict:
    """Chunk + embed + upsert/index the subset for one chunk_method. Returns stats.

    When ``resume`` is set and this mode's Qdrant collection already holds points
    *and* a checkpointed stats record exists, the ingest is skipped and the saved
    stats are returned — so a run interrupted partway (these long ingests can be
    killed by a wall-clock cap) resumes without re-paying for finished modes."""
    if resume:
        cached = _load_ingest_stats().get(mode)
        if cached and await _collection_count(mode) > 0:
            print(
                f"\n[{mode}] resume: collection already populated "
                f"({cached['n_chunks']} chunks) — skipping ingest.",
                flush=True,
            )
            return cached

    print(f"\n[{mode}] chunking {len(docs)} docs ...", flush=True)
    embed_fn = make_sync_embed_fn() if mode == "semantic" else None
    chunker = make_chunker(
        mode,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        embed_fn=embed_fn,
        max_tokens=MAX_TOKENS,
        token_counter=TOKEN_COUNTER,
    )

    t0 = time.perf_counter()
    all_chunks: list[Chunk] = []
    if mode == "semantic":
        # Semantic chunking is dominated by per-doc buffer embeds. Run several docs
        # concurrently in a thread pool; each doc's embed_fn fans its buffer batches
        # across all four endpoints, so a small pool saturates every GPU. Threads
        # are fine: the work blocks on network I/O (releasing the GIL) and each
        # chunk() call is independent. Results are reassembled in input order.

        def _chunk_one(doc: Document) -> list[Chunk]:
            chunks = chunker.chunk(doc)
            for c in chunks:
                c.metadata = dict(c.metadata)
                c.metadata["tenant_id"] = TENANT
            return chunks

        with ThreadPoolExecutor(max_workers=SEMANTIC_DOC_WORKERS) as pool:
            per_doc = list(pool.map(_chunk_one, docs))
        for chunks in per_doc:
            all_chunks.extend(chunks)
    else:
        for doc in docs:
            chunks = chunker.chunk(doc)
            for c in chunks:
                c.metadata = dict(c.metadata)
                c.metadata["tenant_id"] = TENANT
            all_chunks.extend(chunks)
    chunk_time = time.perf_counter() - t0
    raw_count = len(all_chunks)

    all_chunks, n_capped = cap_oversized(all_chunks)
    print(
        f"[{mode}] {raw_count} chunks ({n_capped} oversized split), "
        f"chunk time {chunk_time:.1f}s",
        flush=True,
    )

    sizes = [len(c.content) for c in all_chunks]

    collection = _store_name(mode)
    qdrant_url, es_url = store_urls()
    vstore = QdrantVectorStore(
        url=qdrant_url, collection=collection, vector_size=VECTOR_SIZE, timeout=120
    )
    tindex = ElasticsearchTextIndex(url=es_url, index=collection)
    await vstore.ensure_collection()
    await tindex.ensure_index()

    t1 = time.perf_counter()
    print(f"[{mode}] embedding {len(all_chunks)} chunks ...", flush=True)
    vectors = await embed_texts_async(client, [c.content for c in all_chunks])
    if len(vectors) != len(all_chunks):
        raise RuntimeError(
            f"[{mode}] embed count {len(vectors)} != chunk count {len(all_chunks)}"
        )
    for c, v in zip(all_chunks, vectors, strict=True):
        c.embedding = v

    print(f"[{mode}] upserting to Qdrant + indexing to ES ...", flush=True)
    upsert_batch = 256
    for start in range(0, len(all_chunks), upsert_batch):
        batch = all_chunks[start : start + upsert_batch]
        await vstore.upsert(batch)
        await tindex.index(batch)
    await tindex.close()
    ingest_time = time.perf_counter() - t1

    stats = {
        "mode": mode,
        "n_chunks": len(all_chunks),
        "n_capped": n_capped,
        "chunks_per_doc": len(all_chunks) / len(docs) if docs else 0.0,
        "mean_chars": statistics.mean(sizes) if sizes else 0.0,
        "median_chars": statistics.median(sizes) if sizes else 0.0,
        "p95_chars": _percentile(sizes, 95) if sizes else 0.0,
        "chunk_time_s": chunk_time,
        "ingest_time_s": ingest_time,
        "total_time_s": chunk_time + ingest_time,
    }
    _save_ingest_stats(mode, stats)
    return stats


def _load_ingest_stats() -> dict:
    if STATS_PATH.exists():
        try:
            return json.loads(STATS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_ingest_stats(mode: str, stats: dict) -> None:
    all_stats = _load_ingest_stats()
    all_stats[mode] = stats
    STATS_PATH.write_text(json.dumps(all_stats, indent=2), encoding="utf-8")


async def _collection_count(mode: str) -> int:
    """Point count for a mode's Qdrant collection (0 if absent)."""
    from qdrant_client import AsyncQdrantClient

    qc = AsyncQdrantClient(url=store_urls()[0], timeout=60)
    try:
        info = await qc.get_collection(_store_name(mode))
        return int(info.points_count or 0)
    except Exception:  # noqa: BLE001 - missing collection -> count 0
        return 0
    finally:
        await qc.close()


def _percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(ordered[int(rank)])
    frac = rank - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def _doc_rank(doc_ids: list[str], relevant: str) -> int | None:
    """1-based rank of the first occurrence of ``relevant`` in ``doc_ids``."""
    for i, d in enumerate(doc_ids):
        if d == relevant:
            return i + 1
    return None


def _metrics_from_ranks(ranks: list[int | None], top_k: int) -> dict:
    """Single-relevant-doc known-item metrics over a list of ranks (None = miss)."""
    n = len(ranks)
    if n == 0:
        return {"recall@1": 0.0, "recall@5": 0.0, "recall@10": 0.0, "mrr@10": 0.0, "ndcg@10": 0.0}
    rec1 = sum(1 for r in ranks if r is not None and r <= 1) / n
    rec5 = sum(1 for r in ranks if r is not None and r <= 5) / n
    rec10 = sum(1 for r in ranks if r is not None and r <= min(10, top_k)) / n
    mrr = sum((1.0 / r) for r in ranks if r is not None and r <= 10) / n
    # nDCG@10 with a single relevant doc: DCG = 1/log2(rank+1), ideal DCG = 1.
    ndcg = sum(
        (1.0 / math.log2(r + 1)) for r in ranks if r is not None and r <= 10
    ) / n
    return {
        "recall@1": rec1,
        "recall@5": rec5,
        "recall@10": rec10,
        "mrr@10": mrr,
        "ndcg@10": ndcg,
    }


def _sample_eval_docs(docs: list[Document], n: int) -> list[Document]:
    """Deterministically pick ``n`` docs to evaluate (0/<=0 or >= len → all).

    Sorts docs by id (stable, corpus-order-independent) then draws a fixed
    ``random.Random(0)`` sample, so the chosen query set is identical on every
    run and across all three modes — no wall-clock / global-RNG nondeterminism.
    """
    if n <= 0 or n >= len(docs):
        return docs
    ordered = sorted(docs, key=lambda d: d.id)
    idx = sorted(random.Random(0).sample(range(len(ordered)), n))
    return [ordered[i] for i in idx]


async def evaluate_mode(
    mode: str,
    docs: list[Document],
    client: httpx.AsyncClient,
    top_k: int,
    rerank_pool: int,
    reranker: SidecarReranker,
) -> dict:
    """Run known-item retrieval (title -> own doc) hybrid + reranked for one mode."""
    print(f"\n[{mode}] evaluating {len(docs)} known-item queries ...", flush=True)
    collection = _store_name(mode)
    qdrant_url, es_url = store_urls()
    vstore = QdrantVectorStore(
        url=qdrant_url, collection=collection, vector_size=VECTOR_SIZE, timeout=120
    )
    tindex = ElasticsearchTextIndex(url=es_url, index=collection)
    embedder = make_embedder(
        api="openai", http=client, base_url=SFR_ENDPOINTS[0], model=SFR_MODEL
    )
    retriever = HybridRetriever(vstore, tindex, embedder)
    filters = scope_filters({}, TENANT)

    examples: dict[str, str] = {}  # query -> top-1 chunk content (hybrid)
    # Each known-item query is independent, so evaluate them with bounded
    # concurrency (Qdrant + ES retrieval and the rerank call are all network I/O).
    # This keeps the eval phase from dominating wall-time on a 300-doc subset.
    sem = asyncio.Semaphore(EVAL_CONCURRENCY)

    async def _eval_one(doc: Document) -> tuple[int | None, int | None, float | None]:
        query = doc.metadata.get("title") or ""
        relevant = doc.id
        async with sem:
            # Hybrid pass (top_k).
            hits = await retriever.retrieve(
                query, top_k=top_k, filters=filters, use_graph=False, tenant_id=TENANT
            )
            doc_ids = [h.chunk.doc_id for h in hits]
            h_rank = _doc_rank(doc_ids, relevant)
            if hits:
                examples[query] = hits[0].chunk.content

            # Reranked pass: pull a larger candidate pool, rerank, recompute.
            pool = await retriever.retrieve(
                query, top_k=rerank_pool, filters=filters, use_graph=False,
                tenant_id=TENANT,
            )
            r_rank: int | None = None
            r_top1: float | None = None
            if pool:
                reranked = await reranker.score(query, [h.chunk for h in pool])
                rer_doc_ids = [sc.chunk.doc_id for sc in reranked][:top_k]
                r_rank = _doc_rank(rer_doc_ids, relevant)
                if reranked:
                    r_top1 = reranked[0].score
        return h_rank, r_rank, r_top1

    results = await asyncio.gather(*[_eval_one(doc) for doc in docs])
    hybrid_ranks = [r[0] for r in results]
    rer_ranks = [r[1] for r in results]
    rer_top1_scores = [r[2] for r in results if r[2] is not None]

    await tindex.close()

    hybrid = _metrics_from_ranks(hybrid_ranks, top_k)
    reranked_m = _metrics_from_ranks(rer_ranks, top_k)
    return {
        "mode": mode,
        "hybrid": hybrid,
        "reranked_recall@5": reranked_m["recall@5"],
        "reranked_mrr@10": reranked_m["mrr@10"],
        "mean_rerank_top1": (
            statistics.mean(rer_top1_scores) if rer_top1_scores else float("nan")
        ),
        "examples": examples,
    }


# --------------------------------------------------------------------------- #
# Teardown
# --------------------------------------------------------------------------- #
async def teardown(client: httpx.AsyncClient) -> None:
    """Drop the three chunkcmp_* Qdrant collections + ES indices. Never touches
    the production corpus (different names)."""
    print("\n[teardown] dropping chunkcmp_* collections and indices ...", flush=True)
    from qdrant_client import AsyncQdrantClient

    qdrant_url, es_url = store_urls()
    qc = AsyncQdrantClient(url=qdrant_url, timeout=120)
    for mode in MODES:
        name = _store_name(mode)
        assert name.startswith("chunkcmp"), name  # guard: never touch prod
        try:
            await qc.delete_collection(collection_name=name)
            print(f"[teardown] dropped Qdrant collection {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[teardown] Qdrant {name}: {exc}")
    await qc.close()
    for mode in MODES:
        name = _store_name(mode)
        assert name.startswith("chunkcmp"), name
        try:
            r = await client.delete(f"{es_url}/{name}", timeout=60.0)
            print(f"[teardown] dropped ES index {name} (HTTP {r.status_code})")
        except Exception as exc:  # noqa: BLE001
            print(f"[teardown] ES {name}: {exc}")


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _fmt(x: float, places: int = 3) -> str:
    if isinstance(x, float) and math.isnan(x):
        return "n/a"
    return f"{x:.{places}f}"


def build_table(ingest_stats: dict, eval_stats: dict) -> str:
    """Build the markdown results table (rows = modes)."""
    header = (
        "| mode | #chunks | chunks/doc | median chars | p95 chars | capped | "
        "ingest s | recall@1 | recall@5 | recall@10 | MRR@10 | nDCG@10 | "
        "rerank recall@5 | rerank MRR@10 | mean rerank score |\n"
    )
    sep = "|" + "---|" * 15 + "\n"
    rows = ""
    for mode in MODES:
        s = ingest_stats[mode]
        e = eval_stats[mode]
        h = e["hybrid"]
        rows += (
            f"| {mode} | {s['n_chunks']} | {s['chunks_per_doc']:.2f} | "
            f"{s['median_chars']:.0f} | {s['p95_chars']:.0f} | {s['n_capped']} | "
            f"{s['total_time_s']:.1f} | "
            f"{_fmt(h['recall@1'])} | {_fmt(h['recall@5'])} | {_fmt(h['recall@10'])} | "
            f"{_fmt(h['mrr@10'])} | {_fmt(h['ndcg@10'])} | "
            f"{_fmt(e['reranked_recall@5'])} | {_fmt(e['reranked_mrr@10'])} | "
            f"{_fmt(e['mean_rerank_top1'])} |\n"
        )
    return header + sep + rows


def write_csv(ingest_stats: dict, eval_stats: dict) -> None:
    import csv

    fields = [
        "mode", "n_chunks", "chunks_per_doc", "mean_chars", "median_chars",
        "p95_chars", "n_capped", "chunk_time_s", "ingest_time_s", "total_time_s",
        "recall@1", "recall@5", "recall@10", "mrr@10", "ndcg@10",
        "reranked_recall@5", "reranked_mrr@10", "mean_rerank_top1",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for mode in MODES:
            s, e = ingest_stats[mode], eval_stats[mode]
            h = e["hybrid"]
            w.writerow({
                "mode": mode,
                "n_chunks": s["n_chunks"],
                "chunks_per_doc": round(s["chunks_per_doc"], 3),
                "mean_chars": round(s["mean_chars"], 1),
                "median_chars": round(s["median_chars"], 1),
                "p95_chars": round(s["p95_chars"], 1),
                "n_capped": s["n_capped"],
                "chunk_time_s": round(s["chunk_time_s"], 2),
                "ingest_time_s": round(s["ingest_time_s"], 2),
                "total_time_s": round(s["total_time_s"], 2),
                "recall@1": round(h["recall@1"], 4),
                "recall@5": round(h["recall@5"], 4),
                "recall@10": round(h["recall@10"], 4),
                "mrr@10": round(h["mrr@10"], 4),
                "ndcg@10": round(h["ndcg@10"], 4),
                "reranked_recall@5": round(e["reranked_recall@5"], 4),
                "reranked_mrr@10": round(e["reranked_mrr@10"], 4),
                "mean_rerank_top1": (
                    "" if math.isnan(e["mean_rerank_top1"])
                    else round(e["mean_rerank_top1"], 4)
                ),
            })


def build_timing_table(ingest_stats: dict, n_docs: int) -> str:
    """Separate chunk(+embed) time vs ingest/upsert time + throughput per mode.

    The user asked to benchmark embedding and ingest time *separately*, so these
    are reported as distinct columns rather than folded into one total.
    """
    header = (
        "| mode | chunk+embed s | ingest/upsert s | total s | docs/s | chunks/s |\n"
    )
    sep = "|" + "---|" * 6 + "\n"
    rows = ""
    for mode in MODES:
        s = ingest_stats[mode]
        tot = s["total_time_s"]
        docs_s = n_docs / tot if tot else 0.0
        chunks_s = s["n_chunks"] / tot if tot else 0.0
        rows += (
            f"| {mode} | {s['chunk_time_s']:.1f} | {s['ingest_time_s']:.1f} | "
            f"{tot:.1f} | {docs_s:.2f} | {chunks_s:.1f} |\n"
        )
    return header + sep + rows


def recommend(ingest_stats: dict, eval_stats: dict) -> str:
    """Pick the quality winner (by reranked recall@5, tie-break hybrid MRR@10)."""
    def quality(mode: str) -> tuple[float, float]:
        e = eval_stats[mode]
        return (e["reranked_recall@5"], e["hybrid"]["mrr@10"])

    winner = max(MODES, key=quality)
    cheapest = min(MODES, key=lambda m: ingest_stats[m]["total_time_s"])
    sem = ingest_stats["semantic"]
    lines = [
        f"**Recommendation: `{winner}`** wins on retrieval quality "
        f"(reranked recall@5 = {_fmt(eval_stats[winner]['reranked_recall@5'])}, "
        f"hybrid MRR@10 = {_fmt(eval_stats[winner]['hybrid']['mrr@10'])}) over the "
        f"same {ingest_stats[winner]['n_chunks']}-vs-peers chunk sets.",
        "",
        f"Cost trade-off: `{cheapest}` is the cheapest to ingest "
        f"({ingest_stats[cheapest]['total_time_s']:.0f}s total). Semantic chunking "
        f"is the most expensive: it embeds a per-sentence-buffer pass on top of the "
        f"final chunk embeddings, and because its chunks are unbounded it required "
        f"splitting {sem['n_capped']} oversized chunk(s) to fit the SFR/4096-token "
        f"embedder (median {sem['median_chars']:.0f} chars, p95 {sem['p95_chars']:.0f} "
        f"chars vs. the fixed/sentence ~512-char target). With a 4096-token embedder, "
        f"semantic's variable, sometimes very-large chunks are a real liability: "
        f"capping them re-introduces arbitrary boundaries and inflates the chunk "
        f"count. If quality gains over `fixed`/`sentence` are marginal, the simpler, "
        f"uniformly-sized modes are the better default.",
    ]
    return "\n".join(lines)


def write_report(
    ingest_stats: dict,
    eval_stats: dict,
    n_docs: int,
    n_eval: int,
    args: argparse.Namespace,
) -> str:
    table = build_table(ingest_stats, eval_stats)
    eval_note = (
        f" (deterministic sample of {n_eval}/{n_docs}, seed 0)"
        if n_eval < n_docs
        else " (every ingested doc)"
    )

    # Pick one query shared across all modes for the qualitative side-by-side.
    shared_queries = set.intersection(
        *[set(eval_stats[m]["examples"].keys()) for m in MODES]
    )
    qual = ""
    chosen = sorted(shared_queries)[:3]
    for q in chosen:
        qual += f"\n**Query:** `{q[:160]}`\n\n"
        for mode in MODES:
            text = eval_stats[mode]["examples"].get(q, "(no hit)")
            snippet = " ".join(text.split())[:400]
            qual += f"- **{mode}** top-1 chunk: {snippet}\n"
    if not chosen:
        qual = "\n(No query produced a top-1 hit across all three modes.)\n"

    body = f"""# Chunking-mode comparison: fixed vs sentence vs semantic

Generated by `scripts/eval/chunking_compare.py`.

## Setup

- **Corpus subset:** first {n_docs} `article`-class records (non-empty title) from
  `{args.input}` — the *same* subset for all three modes.
- **Embedding model:** `{SFR_MODEL}` (4096-dim, 4096-token context) via
  {len(SFR_ENDPOINTS)} vLLM endpoint(s): {", ".join(SFR_ENDPOINTS)}.
- **Eval queries:** {n_eval} known-item queries{eval_note}.
- **Chunk params:** `fixed`/`sentence` = chunk_size {args.chunk_size} / overlap
  {args.chunk_overlap}; `semantic` = buffer_size 3 / breakpoint percentile 80 /
  min_chunk_length 500.
- **Oversize cap:** chunks over the token budget ({MAX_TOKENS} tokens, auto-detected
  from the endpoint's `max_model_len`) are split by tokens before embedding so they
  fit the SFR window exactly. Capped counts are reported below (a real semantic cost).
- **Retrieval:** `HybridRetriever` (Qdrant dense + ES BM25, RRF fusion), tenant
  `public`, isolated `chunkcmp_<mode>` stores. Reranked pass pulls a
  {args.rerank_pool}-candidate pool and reranks with the crossencoder sidecar
  (`:50052`).
- **Ground truth (known-item):** query = each doc's title; the relevant doc is that
  doc's id. Single relevant doc per query → recall@k / MRR@10 / nDCG@10.

## Results

{table}
## Timing & throughput (full corpus)

Chunk(+embed) time and ingest/upsert time are kept separate so embedding cost and
store-write cost can be read independently. docs/s and chunks/s are over {n_docs}
docs and the per-mode chunk count.

{build_timing_table(ingest_stats, n_docs)}
## Qualitative side-by-side (top-1 chunk per mode, shared query)
{qual}
## Recommendation

{recommend(ingest_stats, eval_stats)}

## Caveats

- **Title-query proxy.** Known-item-by-title flatters lexical/BM25 matching (the
  title's words often appear verbatim in the lead chunk). Read the hybrid and
  reranked columns together; no single number is decisive.
- **Semantic oversize handling.** Semantic chunks are unbounded; with a 4096-token
  embedder, oversized chunks must be split, which partially defeats the
  topic-boundary intent and inflates chunk count. The capped column quantifies it.
- **Single relevant doc.** nDCG/recall here assume exactly one relevant document
  per query (the source). Multi-relevant ground truth would change absolute values.
"""
    REPORT_PATH.write_text(body, encoding="utf-8")
    return table


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
async def amain(args: argparse.Namespace) -> int:
    # None -> discover. Resolved HERE, not in argparse, so --help stays fast and
    # a missing corpus fails with a message rather than at import time.
    if args.input is None:
        args.input = discover_corpus()[0]
        print(f"corpus: {args.input} (discovered in {ASM_CORPUS_DIR})", file=sys.stderr)
    docs = load_subset(args.input, args.limit)
    if not docs:
        print("No matching article documents found; aborting.", file=sys.stderr)
        return 1
    print(f"Loaded {len(docs)} article docs (target {args.limit}).", flush=True)

    timeout = httpx.Timeout(300.0, connect=30.0)
    limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        reranker = SidecarReranker(RERANKER_URL, http=client)

        ingest_stats: dict = {}
        for mode in MODES:
            ingest_stats[mode] = await ingest_mode(
                mode, docs, client, args.chunk_size,
                args.chunk_overlap, resume=args.resume,
            )

        eval_docs = _sample_eval_docs(docs, args.eval_sample)
        if len(eval_docs) != len(docs):
            print(
                f"Evaluating a deterministic sample of {len(eval_docs)} / "
                f"{len(docs)} docs (seed 0).",
                flush=True,
            )
        eval_stats: dict = {}
        for mode in MODES:
            eval_stats[mode] = await evaluate_mode(
                mode, eval_docs, client, args.top_k, args.rerank_pool, reranker
            )

        table = write_report(
            ingest_stats, eval_stats, len(docs), len(eval_docs), args
        )
        write_csv(ingest_stats, eval_stats)
        print("\n" + "=" * 80)
        print("RESULTS")
        print("=" * 80)
        print(table)
        print(f"Report written to {REPORT_PATH}")
        print(f"CSV written to {CSV_PATH}")
        for mode in MODES:
            print(
                f"  {mode}: capped {ingest_stats[mode]['n_capped']} "
                f"oversized chunk(s)"
            )

        if args.teardown:
            await teardown(client)
        else:
            print("\n[teardown] skipped (--no-teardown); chunkcmp_* left in place.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", default=None,
                   help=f"JSONL corpus file. Default: the first shard found in "
                        f"{ASM_CORPUS_DIR}, discovered at runtime.")
    p.add_argument("--limit", type=int, default=300, help="docs in the subset")
    p.add_argument("--top-k", type=int, default=10, help="retrieval cut for metrics")
    p.add_argument("--rerank-pool", type=int, default=50, help="rerank candidate pool")
    p.add_argument("--chunk-size", type=int, default=512)
    p.add_argument("--chunk-overlap", type=int, default=64)
    p.add_argument(
        "--chunk-max-tokens", type=int, default=None,
        help="the embedding model's context window in tokens; the chunker keeps a "
        "small reserve below it and caps every chunk to that budget. Default None = "
        "auto-detect the window from the SFR endpoint's max_model_len. A given value "
        "is treated as the window, so the reserve is subtracted from it too.",
    )
    p.add_argument(
        "--chunk-token-counter", choices=["hf", "endpoint", "estimate"], default="hf",
        help="token counter: 'hf' loads the SFR AutoTokenizer (exact, default), "
        "'endpoint' POSTs /tokenize, 'estimate' uses a chars/token heuristic.",
    )
    p.add_argument(
        "--endpoints",
        default=None,
        help="comma-separated SFR embedding base URLs that override the built-in "
        f"default ({len(SFR_ENDPOINTS)} endpoints: {','.join(SFR_ENDPOINTS)}). "
        "Lets a run target more (or fewer) vLLM endpoints without editing the "
        "module-level default, keeping the committed default environment-agnostic.",
    )
    p.add_argument(
        "--embedding-api-key",
        default=None,
        help="Bearer token sent as 'Authorization: Bearer <key>' to every "
        "embedding endpoint. Falls back to $OPENAI_API_KEY when omitted. "
        "Keyless endpoints ignore the header, so one key safely covers a mixed "
        "pool of keyless + token-authed vLLM endpoints (same single-key model as "
        "the production ingester).",
    )
    p.add_argument(
        "--eval-sample",
        type=int,
        default=0,
        help="deterministically sample N known-item queries for the eval phase "
        "instead of evaluating every ingested doc (0 = all). The sample is the "
        "SAME set across all three modes (seeded random.Random(0) over doc ids), "
        "so the comparison stays apples-to-apples while bounding eval cost at "
        "full corpus scale.",
    )
    p.add_argument(
        "--qdrant-url",
        required=True,
        help="Qdrant base URL this run's chunkcmp_* collections are built in. "
        "REQUIRED, no default: the harness creates and drops collections, and the "
        "localhost literal it used to carry is production on the deployment host "
        "(#476). The teardown guard guards NAMES, not hosts.",
    )
    p.add_argument(
        "--es-url",
        required=True,
        help="Elasticsearch base URL for this run's chunkcmp_* indices (same caveat "
        "as --qdrant-url).",
    )
    p.add_argument(
        "--collection-prefix",
        default=DEFAULT_PREFIX,
        help="Qdrant/ES name prefix for this run's isolated stores (default "
        f"{DEFAULT_PREFIX!r}). Must start with 'chunkcmp' so the teardown guard "
        "still protects the production corpus. Use a distinct suffix to run two "
        "harness instances against one Qdrant/ES without collisions.",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="skip re-ingesting a mode whose collection is already populated "
        "(reuses checkpointed ingest stats) — for resuming an interrupted run",
    )
    p.add_argument(
        "--no-teardown",
        dest="teardown",
        action="store_false",
        help="keep the chunkcmp_* stores after the run (default: tear down)",
    )
    p.set_defaults(teardown=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global _PREFIX, SFR_ENDPOINTS, EMBED_API_KEY, MAX_TOKENS, TOKEN_COUNTER
    global QDRANT_URL, ES_URL
    args = parse_args(argv)
    # Before anything can build a store client (#476).
    QDRANT_URL = args.qdrant_url.rstrip("/")
    ES_URL = args.es_url.rstrip("/")
    if not args.collection_prefix.startswith("chunkcmp"):
        raise SystemExit(
            "--collection-prefix must start with 'chunkcmp' (teardown safety guard)"
        )
    _PREFIX = args.collection_prefix
    EMBED_API_KEY = args.embedding_api_key or os.environ.get("OPENAI_API_KEY")
    if EMBED_API_KEY:
        print("Using a Bearer token for embedding endpoints.", flush=True)
    if args.endpoints:
        SFR_ENDPOINTS = [u.strip() for u in args.endpoints.split(",") if u.strip()]
        if not SFR_ENDPOINTS:
            raise SystemExit("--endpoints parsed to an empty list")
        print(
            f"Using {len(SFR_ENDPOINTS)} SFR endpoint(s): "
            f"{', '.join(SFR_ENDPOINTS)}",
            flush=True,
        )
    # Token budget + counter for sizing/capping chunks to the SFR window. The
    # budget is auto-detected from the endpoint's max_model_len (override with
    # --chunk-max-tokens); the counter defaults to the SFR AutoTokenizer.
    TOKEN_COUNTER = make_token_counter(
        args.chunk_token_counter,
        model=SFR_MODEL,
        base_url=SFR_ENDPOINTS[0],
        api_key=EMBED_API_KEY,
    )
    MAX_TOKENS = resolve_max_tokens(
        args.chunk_max_tokens, base_url=SFR_ENDPOINTS[0], api_key=EMBED_API_KEY
    )
    print(f"Token budget per chunk: {MAX_TOKENS} tokens.", flush=True)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
