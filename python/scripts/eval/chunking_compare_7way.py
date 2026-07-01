#!/usr/bin/env python
"""7-way chunking-method comparison (char vs token unit, size, semantic) for ragstack.

A self-contained operator/eval harness (not wired into the API). It ingests the
*same* deterministic subset of article documents — sampled balanced across the 3
input JSONL files — **seven** different ways (one named config each) into isolated
Qdrant collections + Elasticsearch indices, then measures known-item retrieval
quality (recall@k / MRR@10 / nDCG@10, hybrid and reranked) and chunk
structure/cost per config (chars, tokens, token-cap/overflow counts), and writes a
markdown report + CSV.

It is the multi-config sibling of ``chunking_compare.py`` (which runs char-mode
fixed/sentence/semantic over ONE file). The seven configs deliberately probe three
questions:

  (a) does the char-vs-token *unit* matter once size is matched
      (fixed_char2048 ~ fixed_tok512, both ~512 tokens)?
  (b) does chunk *size* matter (fixed_tok256 vs fixed_tok512)?
  (c) does any method beat the cheap fixed baseline at full token-safety?
  (d) per-config token-overflow counts — the token-safety payoff.

The 7 configs (see ``CONFIGS`` below):

  fixed_char512    RecursiveCharacterChunker, char size 512 / overlap 64 (prod baseline)
  fixed_char2048   RecursiveCharacterChunker, char size 2048 / overlap 256 (size-matched control)
  fixed_tok256     sliding TOKEN window, 256 tokens / 32 overlap (local helper)
  fixed_tok512     sliding TOKEN window, 512 tokens / 64 overlap (local helper)
  sentence_tok512  SentenceChunker (Punkt), token budget 512 / ~64-tok overlap
  words_tok512     WordChunker, token budget 512 / ~64-tok overlap
  semantic_tokcap  SemanticChunker, buffer 3 / pct 80 / min_chunk 500, token-cap 4080

Every config is hard-capped at ``HARD_CAP_TOKENS`` (4080 = SFR 4096 window minus a
16-token reserve) so NO chunk overflows the embedder, even where the config's own
budget is smaller. Char/token medians+p95, the token-cap/overflow count, ingest
seconds, throughput, and all retrieval metrics are reported per config.

Embedding backend is the production SFR/4096 model served by up to 16 vLLM
endpoints — coconut ``:9001-9008`` (keyless) + lambda13 ``:9990-9997`` (Bearer
key). The harness probes which endpoints are live at start and uses whatever
responds (warning if <16), round-robin across the live pool to spread GPU load.

Idempotent: deterministic chunk ids mean a re-run upserts in place. Teardown
(default ON) drops the seven ``chunkcmp_m7_*`` collections/indices at the end. The
production SFR corpus (``ragstack_salesforce_sfr_embedding_mistral_4096_928f8ebe``
/ ES ``ragstack_sfr``) uses different names and is never touched, and the guard
asserts every dropped name starts with ``chunkcmp_m7``.

Usage::

    cd python
    . /rag/bin/activate
    python scripts/eval/chunking_compare_7way.py \
        --embedding-api-key BRCMistral            # full run + teardown
    python scripts/eval/chunking_compare_7way.py --limit 30 --eval-sample 50 \
        --no-teardown --embedding-api-key BRCMistral   # quick smoke
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
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ragstack.embedders import make_embedder
from ragstack.ingestion.chunkers import make_chunker, split_text_to_token_budget
from ragstack.ingestion.enrich import ARTICLE, enrich, index_metadata
from ragstack.ingestion.loaders import deterministic_doc_id
from ragstack.ingestion.tokenization import HFTokenCounter, TokenCounter
from ragstack.models import Chunk, Document
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.scoring.scorers import SidecarReranker
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.qdrant import QdrantVectorStore
from ragstack.tenancy import scope_filters

# --------------------------------------------------------------------------- #
# Configuration constants
# --------------------------------------------------------------------------- #
DEFAULT_INPUTS = [
    "/rag/ingest/inputs/09320c55-a8a7-4f4d-81b3-ae55b7a329fa.jsonl",
    "/rag/ingest/inputs/0a4f7390-da10-4305-bf94-e0cb8d9e7de8.jsonl",
    "/rag/ingest/inputs/0d1bb3c8-ecf7-44d1-a898-6215f00f1592.jsonl",
]
# 16 endpoints: coconut keyless :9001-9008 + lambda13 keyed :9990-9997. The live
# subset is detected at startup; the keyless endpoints ignore the Bearer header,
# so one --embedding-api-key safely covers the mixed pool.
DEFAULT_ENDPOINTS = [f"http://localhost:{p}" for p in range(9001, 9009)] + [
    f"http://lambda13.cels.anl.gov:{p}" for p in range(9990, 9998)
]
SFR_MODEL = "Salesforce/SFR-Embedding-Mistral"
# Bearer token sent to embedding endpoints (set in main()). Keyless endpoints
# ignore it, so a single key covers a mixed keyless + keyed pool.
EMBED_API_KEY: str | None = None
# Live endpoints, narrowed to the responding subset in main(); used round-robin.
SFR_ENDPOINTS: list[str] = list(DEFAULT_ENDPOINTS)
VECTOR_SIZE = 4096
QDRANT_URL = "http://localhost:6333"
ES_URL = "http://localhost:9200"
RERANKER_URL = "http://localhost:50052"
TENANT = "public"

# SFR window is 4096 tokens; reserve 16 for BOS/EOS/pooling specials → hard cap
# 4080. EVERY config is capped at this so no chunk can overflow the embedder,
# even configs whose own budget is smaller (the per-config budget is the primary
# sizing; this is the global safety ceiling). Set as default; CLI can override.
HARD_CAP_TOKENS = 4080

# Collection/index prefix. Always begins with ``chunkcmp_m7`` so the teardown
# guard (assert startswith) protects the prod corpus AND leaves other harness
# leftovers (plain ``chunkcmp_*``) untouched.
DEFAULT_PREFIX = "chunkcmp_m7"
_PREFIX = DEFAULT_PREFIX

# Token counter shared by every config (built once in main()).
TOKEN_COUNTER: TokenCounter | None = None

EMBED_BATCH = 64
EMBED_CONCURRENCY = 16  # bounded in-flight embed requests across the live pool
EMBED_RETRIES = 4
SEMANTIC_DOC_WORKERS = 8  # docs whose semantic buffer-embeds run concurrently
# Eval concurrency is kept modest: each query does 2 retrieves + a 50-doc rerank
# against the single crossencoder sidecar (:50052), which drops connections under
# heavy concurrent load (httpx ReadError). 8 in flight keeps it busy without
# overruning it; the rerank call also retries transient connection errors.
EVAL_CONCURRENCY = 8
RERANK_RETRIES = 4

REPORT_PATH = Path(__file__).resolve().parent / "chunking_compare_7way_report.md"
CSV_PATH = Path(__file__).resolve().parent / "chunking_compare_7way_results.csv"
STATS_PATH = (
    Path(__file__).resolve().parent / ".chunking_compare_7way_ingest_stats.json"
)


# --------------------------------------------------------------------------- #
# The 7 named configs
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChunkConfig:
    """One named chunking configuration.

    ``kind`` selects the implementation:
      - ``"fixed_char"`` → RecursiveCharacterChunker (char sizing); ``max_tokens``
        is only the hard cap, not the primary size.
      - ``"token_window"`` → the local sliding TOKEN-window helper (primary token
        size = ``size``, overlap = ``overlap`` tokens).
      - ``"sentence"`` / ``"words"`` → Sentence/Word chunker on the token-budget
        path (``max_tokens=size``).
      - ``"semantic"`` → SemanticChunker (adaptive) with a token cap = ``size``.
    """

    key: str
    kind: str
    size: int  # chars (fixed_char), tokens (token_window/sentence/words), tok-cap (semantic)
    overlap: int = 0  # chars (fixed_char) or tokens (token_window)
    char_overlap: int = 0  # explicit char overlap for sentence/words token-budget packing
    label: str = ""
    extra: dict = field(default_factory=dict)


CONFIGS: list[ChunkConfig] = [
    ChunkConfig(
        key="fixed_char512", kind="fixed_char", size=512, overlap=64,
        label="fixed (char) 512/64 — legacy/prod baseline",
    ),
    ChunkConfig(
        key="fixed_char2048", kind="fixed_char", size=2048, overlap=256,
        label="fixed (char) 2048/256 — size-matched char<->token control",
    ),
    ChunkConfig(
        key="fixed_tok256", kind="token_window", size=256, overlap=32,
        label="fixed TOKEN window 256/32",
    ),
    ChunkConfig(
        key="fixed_tok512", kind="token_window", size=512, overlap=64,
        label="fixed TOKEN window 512/64",
    ),
    ChunkConfig(
        key="sentence_tok512", kind="sentence", size=512, char_overlap=160,
        label="sentence (Punkt), pack to <=512 tokens, ~1-sentence overlap",
    ),
    ChunkConfig(
        key="words_tok512", kind="words", size=512, char_overlap=160,
        label="words, pack to <=512 tokens, ~64-tok overlap",
    ),
    ChunkConfig(
        key="semantic_tokcap", kind="semantic", size=4080,
        label="semantic (buffer 3 / pct 80 / min 500), token-cap 4080",
        extra={"buffer_size": 3, "breakpoint_percentile_threshold": 80.0,
               "min_chunk_length": 500},
    ),
]
CONFIG_KEYS = [c.key for c in CONFIGS]
CONFIG_BY_KEY = {c.key: c for c in CONFIGS}


def _store_name(key: str) -> str:
    """Qdrant collection / ES index name for config ``key`` under the prefix."""
    return f"{_PREFIX}_{key}"


# --------------------------------------------------------------------------- #
# Subset selection — balanced + deterministic across the 3 files
# --------------------------------------------------------------------------- #
def load_subset(inputs: list[str], total: int, scan_cap: int) -> list[Document]:
    """Load ``total`` article docs balanced across ``inputs`` deterministically.

    For each file we stream up to ``scan_cap`` lines, keep ARTICLE-class records
    with a non-empty title (the known-item eval needs titles), then deterministically
    sample ``total / n_files`` of them by sorting candidates by doc id and drawing a
    ``random.Random(0)`` sample. The chosen set is identical on every run and the
    SAME 1500 docs feed all 7 configs. doc ids match the production ingest path
    (deterministic_doc_id of the resolved source path), unique across files.
    """
    per_file = max(1, total // len(inputs))
    docs: list[Document] = []
    for fi, input_path in enumerate(inputs):
        cands: list[Document] = []
        path = Path(input_path)
        with path.open(encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i >= scan_cap:
                    break
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
                cands.append(
                    Document(id=doc_id, content=text, metadata=meta, source=src)
                )
        # Deterministic balanced draw from this file's candidates.
        ordered = sorted(cands, key=lambda d: d.id)
        # Last file absorbs the remainder so the total lands exactly on ``total``.
        want = per_file if fi < len(inputs) - 1 else total - per_file * (len(inputs) - 1)
        want = min(want, len(ordered))
        if want >= len(ordered):
            chosen = ordered
        else:
            idx = sorted(random.Random(0).sample(range(len(ordered)), want))
            chosen = [ordered[i] for i in idx]
        print(
            f"[subset] {path.name[:12]}: {len(cands)} article+title candidates, "
            f"took {len(chosen)}",
            flush=True,
        )
        docs.extend(chosen)
    # Guard against an id collision across files (shouldn't happen — paths differ).
    seen: set[str] = set()
    uniq: list[Document] = []
    for d in docs:
        if d.id in seen:
            continue
        seen.add(d.id)
        uniq.append(d)
    return uniq


# --------------------------------------------------------------------------- #
# Endpoint liveness
# --------------------------------------------------------------------------- #
def detect_live_endpoints(candidates: list[str], api_key: str | None) -> list[str]:
    """Return the subset of ``candidates`` whose ``/v1/models`` answers 200."""
    live: list[str] = []
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    with httpx.Client(timeout=8.0) as client:
        for url in candidates:
            try:
                r = client.get(f"{url.rstrip('/')}/v1/models", headers=headers)
                if r.status_code == 200:
                    live.append(url)
                else:
                    print(f"[endpoints] {url} -> HTTP {r.status_code} (skip)", flush=True)
            except Exception as exc:  # noqa: BLE001 - unreachable endpoint -> skip
                print(f"[endpoints] {url} unreachable ({exc}); skip", flush=True)
    return live


# --------------------------------------------------------------------------- #
# Embedding (async, bounded, retrying, round-robin over the live SFR endpoints)
# --------------------------------------------------------------------------- #
async def _post_embeddings(
    client: httpx.AsyncClient, base_url: str, texts: list[str]
) -> list[list[float]]:
    """One raw embeddings POST with retries on transient (non-400) errors."""
    headers = {"Authorization": f"Bearer {EMBED_API_KEY}"} if EMBED_API_KEY else None
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
    a last resort, truncating a single offending text by char caps. With the global
    4080-token hard cap this should essentially never fire; it remains a backstop."""
    async with sem:
        try:
            return await _post_embeddings(client, base_url, texts)
        except httpx.HTTPStatusError as exc:
            if exc.response is None or exc.response.status_code != 400:
                raise
    if len(texts) == 1:
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


_RR_COUNTER = itertools.count()
_RR_LOCK = threading.Lock()


def _next_rr_offset() -> int:
    with _RR_LOCK:
        return next(_RR_COUNTER)


async def embed_texts_async(
    client: httpx.AsyncClient, texts: list[str]
) -> list[list[float]]:
    """Embed ``texts`` in batches, round-robin across the live endpoints, bounded."""
    if not texts:
        return []
    sem = asyncio.Semaphore(EMBED_CONCURRENCY)
    base = _next_rr_offset()
    batches: list[list[str]] = [
        texts[start : start + EMBED_BATCH]
        for start in range(0, len(texts), EMBED_BATCH)
    ]
    tasks = [
        _embed_one_batch(
            client, SFR_ENDPOINTS[(base + i) % len(SFR_ENDPOINTS)], batch, sem
        )
        for i, batch in enumerate(batches)
    ]
    results = await asyncio.gather(*tasks)
    out: list[list[float]] = []
    for vecs in results:
        out.extend(vecs)
    return out


def make_sync_embed_fn():
    """A SYNC embed function for the semantic chunker's buffer embeddings that fans
    out across all live SFR endpoints concurrently (run from worker threads)."""

    def _embed(buffers: Sequence[str]) -> list[list[float]]:
        texts = list(buffers)
        if not texts:
            return []

        async def _run() -> list[list[float]]:
            timeout = httpx.Timeout(300.0, connect=30.0)
            limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
            async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
                return await embed_texts_async(client, texts)

        return asyncio.run(_run())

    return _embed


# --------------------------------------------------------------------------- #
# Sliding token-window chunker (local helper — for fixed_tok256 / fixed_tok512)
# --------------------------------------------------------------------------- #
def token_window_chunks(
    doc: Document,
    window_tokens: int,
    overlap_tokens: int,
    token_counter: TokenCounter,
) -> list[Chunk]:
    """Sliding TOKEN-window chunker with exact source char offsets.

    RecursiveCharacterChunker's ``max_tokens`` is only a CAP, so a true token-SIZE
    config needs its own chunker. Here we tokenize the whole doc with the SFR
    AutoTokenizer using ``return_offsets_mapping=True`` (so each token carries its
    exact ``[char_start, char_end)`` source span), then slide an ``window_tokens``
    window with ``overlap_tokens`` overlap. Each window maps back to a contiguous
    char span ``[offs[i][0], offs[j-1][1])`` via the offset mapping, so chunk
    content is sliced from the source and the deterministic uuid5 id
    (``doc_id:start:end``) is preserved exactly like the package chunkers.

    Falls back to a single whole-doc chunk when the counter isn't an HF fast
    tokenizer (no offset mapping); in practice the harness always uses HFTokenCounter.
    """
    text = doc.content
    if not text:
        return []
    tok = getattr(token_counter, "_tokenizer", None)
    if tok is None:
        # Not an HF counter — degrade to a single whole-doc chunk.
        return [_window_chunk(doc, 0, len(text))]
    tokenizer = tok()
    enc = tokenizer(text, return_offsets_mapping=True, add_special_tokens=False)
    offsets = enc["offset_mapping"]
    n = len(offsets)
    if n == 0:
        return [_window_chunk(doc, 0, len(text))]
    step = max(1, window_tokens - overlap_tokens)
    chunks: list[Chunk] = []
    seen_spans: set[tuple[int, int]] = set()
    start_tok = 0
    while start_tok < n:
        end_tok = min(start_tok + window_tokens, n)
        char_start = offsets[start_tok][0]
        char_end = offsets[end_tok - 1][1]
        span = (char_start, char_end)
        if char_end > char_start and span not in seen_spans:
            seen_spans.add(span)
            chunks.append(_window_chunk(doc, char_start, char_end))
        if end_tok >= n:
            break
        start_tok += step
    return chunks


def _window_chunk(doc: Document, start: int, end: int) -> Chunk:
    return Chunk(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc.id}:{start}:{end}")),
        doc_id=doc.id,
        content=doc.content[start:end],
        metadata=dict(doc.metadata),
        start_char=start,
        end_char=end,
    )


# --------------------------------------------------------------------------- #
# Build the chunks for one config (returns chunks; capping happens after)
# --------------------------------------------------------------------------- #
def chunk_docs_for_config(cfg: ChunkConfig, docs: list[Document]) -> list[Chunk]:
    """Chunk all ``docs`` with config ``cfg`` (no cap yet). Stamps tenant_id."""
    assert TOKEN_COUNTER is not None
    all_chunks: list[Chunk] = []

    if cfg.kind == "token_window":
        for doc in docs:
            chunks = token_window_chunks(doc, cfg.size, cfg.overlap, TOKEN_COUNTER)
            _stamp(chunks)
            all_chunks.extend(chunks)
        return all_chunks

    if cfg.kind == "fixed_char":
        chunker = make_chunker(
            "fixed", chunk_size=cfg.size, chunk_overlap=cfg.overlap,
            max_tokens=HARD_CAP_TOKENS, token_counter=TOKEN_COUNTER,
        )
        for doc in docs:
            chunks = chunker.chunk(doc)
            _stamp(chunks)
            all_chunks.extend(chunks)
        return all_chunks

    if cfg.kind in ("sentence", "words"):
        # Token-budget packing path: max_tokens=size drives the pack; char_overlap
        # gives the ~1-sentence / ~64-token tail overlap in char terms.
        chunker = make_chunker(
            cfg.kind, chunk_size=10**9, chunk_overlap=cfg.char_overlap,
            max_tokens=cfg.size, token_counter=TOKEN_COUNTER,
        )
        for doc in docs:
            chunks = chunker.chunk(doc)
            _stamp(chunks)
            all_chunks.extend(chunks)
        return all_chunks

    if cfg.kind == "semantic":
        embed_fn = make_sync_embed_fn()
        chunker = make_chunker(
            "semantic", embed_fn=embed_fn,
            max_tokens=cfg.size, token_counter=TOKEN_COUNTER,
            **cfg.extra,
        )

        def _chunk_one(doc: Document) -> list[Chunk]:
            chunks = chunker.chunk(doc)
            _stamp(chunks)
            return chunks

        with ThreadPoolExecutor(max_workers=SEMANTIC_DOC_WORKERS) as pool:
            for chunks in pool.map(_chunk_one, docs):
                all_chunks.extend(chunks)
        return all_chunks

    raise ValueError(f"unknown config kind {cfg.kind!r}")


def _stamp(chunks: list[Chunk]) -> None:
    for c in chunks:
        c.metadata = dict(c.metadata)
        c.metadata["tenant_id"] = TENANT


# --------------------------------------------------------------------------- #
# Hard cap at HARD_CAP_TOKENS (safety: no chunk overflows the SFR window)
# --------------------------------------------------------------------------- #
def cap_oversized(chunks: list[Chunk]) -> tuple[list[Chunk], int]:
    """Split any chunk over HARD_CAP_TOKENS into <=cap pieces by tokens (lossless).

    Returns (capped_chunks, n_oversized) where n_oversized is the count of input
    chunks that exceeded 4080 tokens — the token-safety payoff metric. Most configs
    are already token-sized so this is 0; semantic and the char configs are where
    it bites."""
    assert TOKEN_COUNTER is not None
    out: list[Chunk] = []
    n_oversized = 0
    for c in chunks:
        if TOKEN_COUNTER.count(c.content) <= HARD_CAP_TOKENS:
            out.append(c)
            continue
        n_oversized += 1
        pieces = split_text_to_token_budget(c.content, HARD_CAP_TOKENS, TOKEN_COUNTER)
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
# Ingest one config
# --------------------------------------------------------------------------- #
async def ingest_config(
    cfg: ChunkConfig,
    docs: list[Document],
    client: httpx.AsyncClient,
    resume: bool = False,
) -> dict:
    """Chunk + cap + embed + upsert/index the subset for one config. Returns stats."""
    key = cfg.key
    if resume:
        cached = _load_ingest_stats().get(key)
        if cached and await _collection_count(key) > 0:
            print(
                f"\n[{key}] resume: collection already populated "
                f"({cached['n_chunks']} chunks) — skipping ingest.",
                flush=True,
            )
            return cached

    assert TOKEN_COUNTER is not None
    print(f"\n[{key}] chunking {len(docs)} docs ({cfg.label}) ...", flush=True)
    t0 = time.perf_counter()
    all_chunks = chunk_docs_for_config(cfg, docs)
    chunk_time = time.perf_counter() - t0
    raw_count = len(all_chunks)

    all_chunks, n_capped = cap_oversized(all_chunks)
    print(
        f"[{key}] {raw_count} chunks ({n_capped} over 4080-tok cap, split), "
        f"chunk time {chunk_time:.1f}s",
        flush=True,
    )

    # Structure stats: chars + tokens (median/p95) and the cap/overflow count.
    char_sizes = [len(c.content) for c in all_chunks]
    tok_sizes = [TOKEN_COUNTER.count(c.content) for c in all_chunks]

    collection = _store_name(key)
    vstore = QdrantVectorStore(
        url=QDRANT_URL, collection=collection, vector_size=VECTOR_SIZE, timeout=120
    )
    tindex = ElasticsearchTextIndex(url=ES_URL, index=collection)
    await vstore.ensure_collection()
    await tindex.ensure_index()

    t1 = time.perf_counter()
    print(f"[{key}] embedding {len(all_chunks)} chunks ...", flush=True)
    vectors = await embed_texts_async(client, [c.content for c in all_chunks])
    if len(vectors) != len(all_chunks):
        raise RuntimeError(
            f"[{key}] embed count {len(vectors)} != chunk count {len(all_chunks)}"
        )
    for c, v in zip(all_chunks, vectors, strict=True):
        c.embedding = v

    print(f"[{key}] upserting to Qdrant + indexing to ES ...", flush=True)
    upsert_batch = 256
    for start in range(0, len(all_chunks), upsert_batch):
        batch = all_chunks[start : start + upsert_batch]
        await vstore.upsert(batch)
        await tindex.index(batch)
    await tindex.close()
    ingest_time = time.perf_counter() - t1

    stats = {
        "key": key,
        "label": cfg.label,
        "n_chunks": len(all_chunks),
        "n_capped": n_capped,
        "chunks_per_doc": len(all_chunks) / len(docs) if docs else 0.0,
        "median_chars": statistics.median(char_sizes) if char_sizes else 0.0,
        "p95_chars": _percentile(char_sizes, 95) if char_sizes else 0.0,
        "median_tokens": statistics.median(tok_sizes) if tok_sizes else 0.0,
        "p95_tokens": _percentile(tok_sizes, 95) if tok_sizes else 0.0,
        "max_tokens_seen": max(tok_sizes) if tok_sizes else 0,
        "chunk_time_s": chunk_time,
        "ingest_time_s": ingest_time,
        "total_time_s": chunk_time + ingest_time,
    }
    _save_ingest_stats(key, stats)
    return stats


def _load_ingest_stats() -> dict:
    if STATS_PATH.exists():
        try:
            return json.loads(STATS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_ingest_stats(key: str, stats: dict) -> None:
    all_stats = _load_ingest_stats()
    all_stats[key] = stats
    STATS_PATH.write_text(json.dumps(all_stats, indent=2), encoding="utf-8")


async def _collection_count(key: str) -> int:
    from qdrant_client import AsyncQdrantClient

    qc = AsyncQdrantClient(url=QDRANT_URL, timeout=60)
    try:
        info = await qc.get_collection(_store_name(key))
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
    for i, d in enumerate(doc_ids):
        if d == relevant:
            return i + 1
    return None


def _metrics_from_ranks(ranks: list[int | None], top_k: int) -> dict:
    n = len(ranks)
    if n == 0:
        return {"recall@1": 0.0, "recall@5": 0.0, "recall@10": 0.0,
                "mrr@10": 0.0, "ndcg@10": 0.0}
    rec1 = sum(1 for r in ranks if r is not None and r <= 1) / n
    rec5 = sum(1 for r in ranks if r is not None and r <= 5) / n
    rec10 = sum(1 for r in ranks if r is not None and r <= min(10, top_k)) / n
    mrr = sum((1.0 / r) for r in ranks if r is not None and r <= 10) / n
    ndcg = sum(
        (1.0 / math.log2(r + 1)) for r in ranks if r is not None and r <= 10
    ) / n
    return {"recall@1": rec1, "recall@5": rec5, "recall@10": rec10,
            "mrr@10": mrr, "ndcg@10": ndcg}


def _sample_eval_docs(docs: list[Document], n: int) -> list[Document]:
    """Deterministically pick ``n`` query docs (seed 0), identical across configs."""
    if n <= 0 or n >= len(docs):
        return docs
    ordered = sorted(docs, key=lambda d: d.id)
    idx = sorted(random.Random(0).sample(range(len(ordered)), n))
    return [ordered[i] for i in idx]


async def evaluate_config(
    cfg: ChunkConfig,
    docs: list[Document],
    client: httpx.AsyncClient,
    top_k: int,
    rerank_pool: int,
    reranker: SidecarReranker,
) -> dict:
    """Known-item retrieval (title -> own doc), hybrid + reranked, for one config."""
    key = cfg.key
    print(f"\n[{key}] evaluating {len(docs)} known-item queries ...", flush=True)
    collection = _store_name(key)
    vstore = QdrantVectorStore(
        url=QDRANT_URL, collection=collection, vector_size=VECTOR_SIZE, timeout=120
    )
    tindex = ElasticsearchTextIndex(url=ES_URL, index=collection)
    embedder = make_embedder(
        api="openai", http=client, base_url=SFR_ENDPOINTS[0], model=SFR_MODEL
    )
    retriever = HybridRetriever(vstore, tindex, embedder)
    filters = scope_filters({}, TENANT)
    sem = asyncio.Semaphore(EVAL_CONCURRENCY)

    async def _rerank(query: str, chunks: list[Chunk]):
        """Rerank with bounded retry — the single crossencoder sidecar can drop a
        connection (httpx ReadError) under concurrent load; retry transient errors
        rather than aborting the whole eval over one dropped request."""
        last_exc: Exception | None = None
        for attempt in range(RERANK_RETRIES):
            try:
                return await reranker.score(query, chunks)
            except (httpx.HTTPError, httpx.StreamError) as exc:
                last_exc = exc
                await asyncio.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"rerank failed after {RERANK_RETRIES} retries: {last_exc}")

    async def _eval_one(doc: Document) -> tuple[int | None, int | None, float | None]:
        query = doc.metadata.get("title") or ""
        relevant = doc.id
        async with sem:
            hits = await retriever.retrieve(
                query, top_k=top_k, filters=filters, use_graph=False, tenant_id=TENANT
            )
            h_rank = _doc_rank([h.chunk.doc_id for h in hits], relevant)
            pool = await retriever.retrieve(
                query, top_k=rerank_pool, filters=filters, use_graph=False,
                tenant_id=TENANT,
            )
            r_rank: int | None = None
            r_top1: float | None = None
            if pool:
                reranked = await _rerank(query, [h.chunk for h in pool])
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
        "key": key,
        "hybrid": hybrid,
        "reranked_recall@5": reranked_m["recall@5"],
        "reranked_mrr@10": reranked_m["mrr@10"],
        "mean_rerank_top1": (
            statistics.mean(rer_top1_scores) if rer_top1_scores else float("nan")
        ),
    }


# --------------------------------------------------------------------------- #
# Teardown
# --------------------------------------------------------------------------- #
async def teardown(client: httpx.AsyncClient) -> None:
    """Drop the seven chunkcmp_m7_* Qdrant collections + ES indices ONLY.

    The guard asserts every name starts with ``chunkcmp_m7`` so neither the prod
    corpus nor other ``chunkcmp_*`` leftovers are touched."""
    print("\n[teardown] dropping chunkcmp_m7_* collections and indices ...", flush=True)
    from qdrant_client import AsyncQdrantClient

    qc = AsyncQdrantClient(url=QDRANT_URL, timeout=120)
    for key in CONFIG_KEYS:
        name = _store_name(key)
        assert name.startswith("chunkcmp_m7"), name  # guard: never touch prod/others
        try:
            await qc.delete_collection(collection_name=name)
            print(f"[teardown] dropped Qdrant collection {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[teardown] Qdrant {name}: {exc}")
    await qc.close()
    for key in CONFIG_KEYS:
        name = _store_name(key)
        assert name.startswith("chunkcmp_m7"), name
        try:
            r = await client.delete(f"{ES_URL}/{name}", timeout=60.0)
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


def build_structure_table(ingest_stats: dict) -> str:
    header = (
        "| config | #chunks | chunks/doc | median chars | p95 chars | "
        "median tok | p95 tok | max tok | overflow>4080 |\n"
    )
    sep = "|" + "---|" * 9 + "\n"
    rows = ""
    for key in CONFIG_KEYS:
        s = ingest_stats[key]
        rows += (
            f"| {key} | {s['n_chunks']} | {s['chunks_per_doc']:.2f} | "
            f"{s['median_chars']:.0f} | {s['p95_chars']:.0f} | "
            f"{s['median_tokens']:.0f} | {s['p95_tokens']:.0f} | "
            f"{s['max_tokens_seen']} | {s['n_capped']} |\n"
        )
    return header + sep + rows


def build_retrieval_table(ingest_stats: dict, eval_stats: dict) -> str:
    header = (
        "| config | recall@1 | recall@5 | recall@10 | MRR@10 | nDCG@10 | "
        "rerank recall@5 | rerank MRR@10 | mean rerank score |\n"
    )
    sep = "|" + "---|" * 9 + "\n"
    rows = ""
    for key in CONFIG_KEYS:
        e = eval_stats[key]
        h = e["hybrid"]
        rows += (
            f"| {key} | "
            f"{_fmt(h['recall@1'])} | {_fmt(h['recall@5'])} | {_fmt(h['recall@10'])} | "
            f"{_fmt(h['mrr@10'])} | {_fmt(h['ndcg@10'])} | "
            f"{_fmt(e['reranked_recall@5'])} | {_fmt(e['reranked_mrr@10'])} | "
            f"{_fmt(e['mean_rerank_top1'])} |\n"
        )
    return header + sep + rows


def build_cost_table(ingest_stats: dict, n_docs: int) -> str:
    header = (
        "| config | chunk+embed s | ingest/upsert s | total s | docs/s | chunks/s |\n"
    )
    sep = "|" + "---|" * 6 + "\n"
    rows = ""
    for key in CONFIG_KEYS:
        s = ingest_stats[key]
        tot = s["total_time_s"]
        docs_s = n_docs / tot if tot else 0.0
        chunks_s = s["n_chunks"] / tot if tot else 0.0
        rows += (
            f"| {key} | {s['chunk_time_s']:.1f} | {s['ingest_time_s']:.1f} | "
            f"{tot:.1f} | {docs_s:.2f} | {chunks_s:.1f} |\n"
        )
    return header + sep + rows


def write_csv(ingest_stats: dict, eval_stats: dict, n_docs: int) -> None:
    import csv

    fields = [
        "config", "label", "n_chunks", "chunks_per_doc",
        "median_chars", "p95_chars", "median_tokens", "p95_tokens", "max_tokens_seen",
        "overflow_4080", "chunk_time_s", "ingest_time_s", "total_time_s",
        "docs_per_s", "chunks_per_s",
        "recall@1", "recall@5", "recall@10", "mrr@10", "ndcg@10",
        "reranked_recall@5", "reranked_mrr@10", "mean_rerank_top1",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for key in CONFIG_KEYS:
            s, e = ingest_stats[key], eval_stats[key]
            h = e["hybrid"]
            tot = s["total_time_s"]
            w.writerow({
                "config": key,
                "label": s.get("label", ""),
                "n_chunks": s["n_chunks"],
                "chunks_per_doc": round(s["chunks_per_doc"], 3),
                "median_chars": round(s["median_chars"], 1),
                "p95_chars": round(s["p95_chars"], 1),
                "median_tokens": round(s["median_tokens"], 1),
                "p95_tokens": round(s["p95_tokens"], 1),
                "max_tokens_seen": s["max_tokens_seen"],
                "overflow_4080": s["n_capped"],
                "chunk_time_s": round(s["chunk_time_s"], 2),
                "ingest_time_s": round(s["ingest_time_s"], 2),
                "total_time_s": round(tot, 2),
                "docs_per_s": round(n_docs / tot, 3) if tot else 0.0,
                "chunks_per_s": round(s["n_chunks"] / tot, 2) if tot else 0.0,
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


def _findings(ingest_stats: dict, eval_stats: dict) -> str:
    """The a/b/c/d narrative findings, computed from the measured numbers."""
    def q(key: str) -> float:
        return eval_stats[key]["reranked_recall@5"]

    def hybrid_r5(key: str) -> float:
        return eval_stats[key]["hybrid"]["recall@5"]

    lines: list[str] = []
    # (a) char vs token unit, size-matched: fixed_char2048 vs fixed_tok512.
    a1, a2 = "fixed_char2048", "fixed_tok512"
    lines.append(
        f"**(a) Does the char-vs-token *unit* matter once size is matched?** "
        f"`{a1}` (median {ingest_stats[a1]['median_tokens']:.0f} tok) vs "
        f"`{a2}` (median {ingest_stats[a2]['median_tokens']:.0f} tok): "
        f"reranked recall@5 {_fmt(q(a1))} vs {_fmt(q(a2))} "
        f"(hybrid recall@5 {_fmt(hybrid_r5(a1))} vs {_fmt(hybrid_r5(a2))}). "
        f"A small delta means the *unit* is second-order once the effective size "
        f"matches; the token window's value is determinism/safety, not raw quality."
    )
    # (b) size: tok256 vs tok512.
    b1, b2 = "fixed_tok256", "fixed_tok512"
    lines.append(
        f"**(b) Does chunk *size* matter?** `{b1}` vs `{b2}`: "
        f"reranked recall@5 {_fmt(q(b1))} vs {_fmt(q(b2))}, "
        f"chunks/doc {ingest_stats[b1]['chunks_per_doc']:.1f} vs "
        f"{ingest_stats[b2]['chunks_per_doc']:.1f}. Smaller windows cost ~2x the "
        f"chunks (and embed/store); the quality delta tells you whether that buys "
        f"anything for title-known-item retrieval."
    )
    # (c) does any method beat the cheap fixed baseline at full token-safety?
    base = "fixed_char512"
    safe_keys = [k for k in CONFIG_KEYS if k != base]
    best = max(safe_keys, key=q)
    lines.append(
        f"**(c) Does any method beat the cheap fixed baseline at full "
        f"token-safety?** Baseline `{base}` reranked recall@5 {_fmt(q(base))}. "
        f"Best token-safe config `{best}` = {_fmt(q(best))} "
        f"(delta {q(best) - q(base):+.3f}). Every non-baseline config is token-capped "
        f"at 4080 so none can overflow the SFR window."
    )
    # (d) overflow counts.
    ov = ", ".join(f"{k}={ingest_stats[k]['n_capped']}" for k in CONFIG_KEYS)
    lines.append(
        f"**(d) Per-config token-overflow (chunks that exceeded 4080 tokens and had "
        f"to be split):** {ov}. This is the token-safety payoff: the char configs and "
        f"semantic are where un-capped chunking would have sent over-window text to "
        f"the embedder; the token-sized configs never overflow by construction."
    )
    return "\n\n".join(lines)


def recommend(ingest_stats: dict, eval_stats: dict) -> str:
    def quality(key: str) -> tuple[float, float]:
        e = eval_stats[key]
        return (e["reranked_recall@5"], e["hybrid"]["mrr@10"])

    winner = max(CONFIG_KEYS, key=quality)
    cheapest = min(CONFIG_KEYS, key=lambda k: ingest_stats[k]["total_time_s"])
    w = ingest_stats[winner]
    return (
        f"**Quality winner: `{winner}`** (reranked recall@5 "
        f"{_fmt(eval_stats[winner]['reranked_recall@5'])}, hybrid MRR@10 "
        f"{_fmt(eval_stats[winner]['hybrid']['mrr@10'])}), median "
        f"{w['median_tokens']:.0f} tok / {w['chunks_per_doc']:.1f} chunks/doc, "
        f"{w['n_capped']} overflow. **Cheapest: `{cheapest}`** "
        f"({ingest_stats[cheapest]['total_time_s']:.0f}s). For a full prod rebuild, "
        f"weigh the quality winner against `fixed_tok512` — a fully token-safe, "
        f"deterministic 512-token window that matches the prod corpus' effective size "
        f"while guaranteeing zero embedder overflow."
    )


def write_report(
    ingest_stats: dict, eval_stats: dict, n_docs: int, n_eval: int,
    args: argparse.Namespace, live_endpoints: list[str],
) -> None:
    struct = build_structure_table(ingest_stats)
    retr = build_retrieval_table(ingest_stats, eval_stats)
    cost = build_cost_table(ingest_stats, n_docs)
    n_coconut = sum(1 for u in live_endpoints if "localhost" in u)
    n_lambda = len(live_endpoints) - n_coconut

    cfg_rows = "\n".join(
        f"| `{c.key}` | {c.kind} | {c.size} | {c.label} |" for c in CONFIGS
    )

    body = f"""# 7-way chunking-method comparison (char vs token unit, size, semantic)

Generated by `scripts/eval/chunking_compare_7way.py`. Embedding model
`{SFR_MODEL}` (4096-dim, 4096-token window), hard cap {HARD_CAP_TOKENS} tokens.

## Configs

| key | kind | size | description |
|---|---|---|---|
{cfg_rows}

## Setup

- **Corpus subset:** {n_docs} `article`-class records (non-empty title), sampled
  deterministically (seed 0) **balanced across the 3 input files**
  ({", ".join(Path(p).name[:12] for p in args.inputs)}). The *same* subset feeds
  all 7 configs.
- **Eval queries:** {n_eval} known-item queries (query = doc title, relevant = its
  own doc_id), deterministic sample (seed 0), top_k={args.top_k}, rerank pool
  {args.rerank_pool}. Same query set across all configs.
- **Embedding fleet:** {len(live_endpoints)} live SFR endpoint(s) used round-robin
  — {n_coconut} on coconut (`:9001-9008`, keyless) + {n_lambda} on lambda13
  (`:9990-9997`, keyed). GPU load is spread by round-robin batch assignment across
  the live pool; semantic's per-doc buffer embeds also fan across the pool.
- **Retrieval:** `HybridRetriever` (Qdrant dense + ES BM25, RRF fusion), tenant
  `public`, isolated `{_PREFIX}_<config>` stores. Reranked pass pulls a
  {args.rerank_pool}-candidate pool, reranks with the crossencoder sidecar (`:50052`).
- **Token safety:** every config is hard-capped at {HARD_CAP_TOKENS} tokens; the
  `overflow>4080` column counts chunks that exceeded it and were split.

## Structure & token-overflow

{struct}
## Retrieval quality

{retr}
## Cost & throughput

chunk(+embed) time and ingest/upsert time are reported separately. docs/s and
chunks/s are over {n_docs} docs.

{cost}
## Findings

{_findings(ingest_stats, eval_stats)}

## Recommendation

{recommend(ingest_stats, eval_stats)}

## Caveats

- **Title-query proxy.** Known-item-by-title flatters lexical/BM25 matching (the
  title's words often appear verbatim in the lead chunk). Read hybrid and reranked
  columns together.
- **Single relevant doc.** recall/MRR/nDCG assume exactly one relevant document per
  query (the source).
- **Subset, not the full corpus.** {n_docs} docs across 3 files; absolute numbers
  would shift on the full 877k-chunk corpus, but the *relative* config ordering is
  the signal.
"""
    REPORT_PATH.write_text(body, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
async def amain(args: argparse.Namespace, live_endpoints: list[str]) -> int:
    docs = load_subset(args.inputs, args.limit, args.scan_cap)
    if not docs:
        print("No matching article documents found; aborting.", file=sys.stderr)
        return 1
    print(f"Loaded {len(docs)} article docs (target {args.limit}).", flush=True)

    timeout = httpx.Timeout(300.0, connect=30.0)
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        reranker = SidecarReranker(RERANKER_URL, http=client)

        ingest_stats: dict = {}
        for cfg in CONFIGS:
            ingest_stats[cfg.key] = await ingest_config(
                cfg, docs, client, resume=args.resume
            )

        eval_docs = _sample_eval_docs(docs, args.eval_sample)
        if len(eval_docs) != len(docs):
            print(
                f"Evaluating a deterministic sample of {len(eval_docs)} / "
                f"{len(docs)} docs (seed 0).",
                flush=True,
            )
        eval_stats: dict = {}
        for cfg in CONFIGS:
            eval_stats[cfg.key] = await evaluate_config(
                cfg, eval_docs, client, args.top_k, args.rerank_pool, reranker
            )

        write_report(
            ingest_stats, eval_stats, len(docs), len(eval_docs), args, live_endpoints
        )
        write_csv(ingest_stats, eval_stats, len(docs))
        print("\n" + "=" * 80)
        print("RESULTS")
        print("=" * 80)
        print(build_structure_table(ingest_stats))
        print(build_retrieval_table(ingest_stats, eval_stats))
        print(build_cost_table(ingest_stats, len(docs)))
        print(f"Report written to {REPORT_PATH}")
        print(f"CSV written to {CSV_PATH}")

        if args.teardown:
            await teardown(client)
        else:
            print("\n[teardown] skipped (--no-teardown); chunkcmp_m7_* left in place.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--inputs", nargs="+", default=DEFAULT_INPUTS,
        help="JSONL corpus paths (the 3 input files)",
    )
    p.add_argument(
        "--limit", type=int, default=1500,
        help="total docs in the subset, split balanced across the input files",
    )
    p.add_argument(
        "--scan-cap", type=int, default=20000,
        help="max lines to stream per input file when collecting candidates",
    )
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--rerank-pool", type=int, default=50)
    p.add_argument(
        "--eval-sample", type=int, default=1000,
        help="deterministically sample N known-item queries (0 = all). Same set "
        "across all configs (seed 0 over doc ids).",
    )
    p.add_argument(
        "--hard-cap-tokens", type=int, default=HARD_CAP_TOKENS,
        help="global per-chunk token ceiling (SFR window minus reserve). No chunk "
        "from any config may exceed it.",
    )
    p.add_argument(
        "--endpoints", default=None,
        help="comma-separated SFR base URLs overriding the built-in 16-endpoint "
        "default. The live subset is still detected at startup.",
    )
    p.add_argument(
        "--embedding-api-key", default=None,
        help="Bearer token for keyed endpoints (lambda13). Falls back to "
        "$OPENAI_API_KEY. Keyless endpoints ignore it.",
    )
    p.add_argument(
        "--collection-prefix", default=DEFAULT_PREFIX,
        help=f"Qdrant/ES name prefix (default {DEFAULT_PREFIX!r}). Must start with "
        "'chunkcmp_m7' so teardown protects prod and other chunkcmp_* leftovers.",
    )
    p.add_argument(
        "--resume", action="store_true",
        help="skip re-ingesting a config whose collection is already populated",
    )
    p.add_argument(
        "--no-teardown", dest="teardown", action="store_false",
        help="keep the chunkcmp_m7_* stores after the run (default: tear down)",
    )
    p.set_defaults(teardown=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global _PREFIX, SFR_ENDPOINTS, EMBED_API_KEY, HARD_CAP_TOKENS, TOKEN_COUNTER
    args = parse_args(argv)
    if not args.collection_prefix.startswith("chunkcmp_m7"):
        raise SystemExit(
            "--collection-prefix must start with 'chunkcmp_m7' (teardown safety guard)"
        )
    _PREFIX = args.collection_prefix
    HARD_CAP_TOKENS = args.hard_cap_tokens
    EMBED_API_KEY = args.embedding_api_key or os.environ.get("OPENAI_API_KEY")
    if EMBED_API_KEY:
        print("Using a Bearer token for keyed embedding endpoints.", flush=True)

    candidates = (
        [u.strip() for u in args.endpoints.split(",") if u.strip()]
        if args.endpoints else list(DEFAULT_ENDPOINTS)
    )
    print(f"Probing {len(candidates)} candidate endpoints ...", flush=True)
    SFR_ENDPOINTS = detect_live_endpoints(candidates, EMBED_API_KEY)
    if not SFR_ENDPOINTS:
        raise SystemExit("No live embedding endpoints; aborting.")
    if len(SFR_ENDPOINTS) < len(candidates):
        print(
            f"WARNING: only {len(SFR_ENDPOINTS)}/{len(candidates)} endpoints live.",
            flush=True,
        )
    print(
        f"Using {len(SFR_ENDPOINTS)} live SFR endpoint(s): "
        f"{', '.join(SFR_ENDPOINTS)}",
        flush=True,
    )

    # Shared HF token counter (exact, offline once cached) for ALL configs.
    TOKEN_COUNTER = HFTokenCounter(model=SFR_MODEL)
    TOKEN_COUNTER._tokenizer()  # force load now so a failure surfaces early
    print(f"Token counter ready (HF {SFR_MODEL}). Hard cap {HARD_CAP_TOKENS} tok.",
          flush=True)

    return asyncio.run(amain(args, SFR_ENDPOINTS))


if __name__ == "__main__":
    raise SystemExit(main())
