#!/usr/bin/env python
"""SciFact (BEIR) passage-level chunking eval with REAL relevance judgments.

The known-item harness (``chunking_compare_7way.py``) queries a doc by its own
title, so every query has exactly one relevant doc and BM25 sees the title verbatim
in the lead chunk — a chunking-*insensitive* proxy that can't discriminate chunkers.
This harness fixes that: SciFact is a scientific *claim-verification* IR benchmark —
~5.2k abstracts (corpus), ~300 held-out claim queries, and **document-level qrels**
(which abstracts support/refute each claim, graded 1). Real queries + multi-relevant
qrels *can* separate chunkers.

Pipeline (mirrors the 7-way harness so results are comparable):

  1. Load SciFact via HuggingFace ``datasets`` (``BeIR/scifact`` corpus+queries +
     ``BeIR/scifact-qrels`` test split); fall back to the ``beir`` library, then to
     ``ir_datasets`` (``beir/scifact/test``). Cache under ``/rag/cache``. The source
     used + counts are reported.
  2. Chunk every abstract with each of the SAME 7 configs (imported from the 7-way
     harness), embed with SFR/4096 across the 16 vLLM endpoints, and upsert into
     ISOLATED stores prefixed ``scifact_m7_<key>`` (Qdrant + ES), tenant ``public``.
  3. For each test claim query, retrieve top-k **chunks** via the same hybrid
     (dense+BM25 → RRF) + cross-encoder rerank pipeline, map each chunk → its
     ``doc_id``, dedupe to a ranked doc list, and score against the qrels at the
     **document level** (BEIR standard): **nDCG@10 (primary), recall@{10,20,100},
     MAP** — supporting multiple relevant docs per query and graded relevance.
  4. Apply the Part-2 statistics layer (``_stats``): paired bootstrap CIs +
     pairwise difference CIs + Holm-corrected Wilcoxon vs ``fixed_tok512``.
  5. Tear down every ``scifact_m7_*`` store (verified gone). The prod SFR corpus
     (``ragstack_salesforce_sfr_embedding_mistral_4096_928f8ebe`` / ES
     ``ragstack_sfr``) is NEVER touched; the teardown guard asserts the prefix.

Usage::

    cd python
    . /rag/bin/activate
    python scripts/eval/scifact_chunk_eval.py --embedding-api-key BRCMistral
    python scripts/eval/scifact_chunk_eval.py --embedding-api-key BRCMistral \
        --no-teardown --query-limit 50   # quick smoke
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import httpx

from ragstack.embedders import make_embedder
from ragstack.ingestion.tokenization import HFTokenCounter
from ragstack.models import Document
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.scoring.scorers import SidecarReranker
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.qdrant import QdrantVectorStore
from ragstack.tenancy import scope_filters

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# Local eval helpers (under scripts/eval, added to sys.path above): the shared
# stats layer and the 7-way harness whose config defs / embedding / chunking /
# cap-split machinery we reuse verbatim.
import _stats  # noqa: E402
import chunking_compare_7way as c7  # noqa: E402

CACHE_DIR = Path(os.environ.get("HF_HOME", "/rag/cache"))
SCIFACT_PREFIX = "scifact_m7"
STATS_REFERENCE = "fixed_tok512"
REPORT_PATH = _HERE / "scifact_chunk_eval_report.md"
CSV_PATH = _HERE / "scifact_chunk_eval_results.csv"

# Doc-level metric cutoffs (BEIR standard for SciFact).
NDCG_K = 10
RECALL_KS = (10, 20, 100)


# --------------------------------------------------------------------------- #
# Data loading (datasets → beir → ir_datasets), cached under /rag/cache
# --------------------------------------------------------------------------- #
def load_scifact() -> tuple[
    list[Document], dict[str, tuple[str, str]], dict[str, dict[str, int]], str
]:
    """Return (corpus_docs, queries, qrels, source_name).

    ``queries`` maps query_id → (query_text, title). ``qrels`` maps query_id →
    {doc_id: grade}. ``corpus_docs`` are Documents whose ``id`` is the corpus ``_id``
    and content is ``title + "\\n" + text`` (SciFact abstracts). Only queries that
    appear in the test qrels are kept.
    """
    for loader in (_load_via_datasets, _load_via_beir, _load_via_ir_datasets):
        try:
            result = loader()
            if result is not None:
                return result
        except Exception as exc:  # noqa: BLE001 - try the next source
            print(f"[data] {loader.__name__} failed: {type(exc).__name__}: {exc}",
                  flush=True)
    raise SystemExit(
        "Could not load SciFact from datasets, beir, or ir_datasets. Install one "
        "(`pip install datasets`) with network access, or pre-populate /rag/cache."
    )


def _load_via_datasets():
    from datasets import load_dataset

    kw = {"cache_dir": str(CACHE_DIR)}
    corpus_ds = load_dataset("BeIR/scifact", "corpus", split="corpus", **kw)
    queries_ds = load_dataset("BeIR/scifact", "queries", split="queries", **kw)
    qrels_ds = load_dataset("BeIR/scifact-qrels", split="test", **kw)

    corpus_docs: list[Document] = []
    for row in corpus_ds:
        did = str(row["_id"])
        title = (row.get("title") or "").strip()
        text = (row.get("text") or "").strip()
        content = f"{title}\n{text}".strip() if title else text
        if not content:
            continue
        corpus_docs.append(
            Document(id=did, content=content,
                     metadata={"tenant_id": c7.TENANT, "title": title},
                     source=f"scifact:{did}")
        )

    qrels: dict[str, dict[str, int]] = {}
    for row in qrels_ds:
        qid = str(row["query-id"])
        cid = str(row["corpus-id"])
        grade = int(row["score"])
        if grade <= 0:
            continue
        qrels.setdefault(qid, {})[cid] = grade

    queries: dict[str, tuple[str, str]] = {}
    for row in queries_ds:
        qid = str(row["_id"])
        if qid not in qrels:
            continue  # keep only test-qrels queries
        text = (row.get("text") or "").strip()
        title = (row.get("title") or "").strip()
        queries[qid] = (text or title, title)
    return corpus_docs, queries, qrels, "huggingface datasets (BeIR/scifact)"


def _load_via_beir():
    from beir import util
    from beir.datasets.data_loader import GenericDataLoader

    data_path = util.download_and_unzip(
        "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip",
        str(CACHE_DIR),
    )
    corpus, queries_raw, qrels_raw = GenericDataLoader(data_folder=data_path).load(
        split="test"
    )
    corpus_docs: list[Document] = []
    for did, doc in corpus.items():
        title = (doc.get("title") or "").strip()
        text = (doc.get("text") or "").strip()
        content = f"{title}\n{text}".strip() if title else text
        if content:
            corpus_docs.append(
                Document(id=str(did), content=content,
                         metadata={"tenant_id": c7.TENANT, "title": title},
                         source=f"scifact:{did}")
            )
    qrels = {str(q): {str(d): int(s) for d, s in rels.items() if int(s) > 0}
             for q, rels in qrels_raw.items()}
    queries = {str(q): (t, "") for q, t in queries_raw.items() if str(q) in qrels}
    return corpus_docs, queries, qrels, "beir library (scifact test)"


def _load_via_ir_datasets():
    import ir_datasets

    ds = ir_datasets.load("beir/scifact/test")
    corpus_docs: list[Document] = []
    for doc in ds.docs_iter():
        title = (getattr(doc, "title", "") or "").strip()
        text = (getattr(doc, "text", "") or "").strip()
        content = f"{title}\n{text}".strip() if title else text
        if content:
            corpus_docs.append(
                Document(id=str(doc.doc_id), content=content,
                         metadata={"tenant_id": c7.TENANT, "title": title},
                         source=f"scifact:{doc.doc_id}")
            )
    qrels: dict[str, dict[str, int]] = {}
    for qr in ds.qrels_iter():
        if qr.relevance > 0:
            qrels.setdefault(str(qr.query_id), {})[str(qr.doc_id)] = int(qr.relevance)
    queries = {str(q.query_id): (q.text, "") for q in ds.queries_iter()
               if str(q.query_id) in qrels}
    return corpus_docs, queries, qrels, "ir_datasets (beir/scifact/test)"


def _store_name(key: str) -> str:
    return f"{SCIFACT_PREFIX}_{key}"


# --------------------------------------------------------------------------- #
# Ingest one config into scifact_m7_<key> stores
# --------------------------------------------------------------------------- #
async def ingest_config(cfg, docs: list[Document], client: httpx.AsyncClient) -> dict:
    """Chunk + cap + embed + upsert/index the SciFact corpus for one config."""
    key = cfg.key
    print(f"\n[{key}] chunking {len(docs)} SciFact abstracts ({cfg.label}) ...",
          flush=True)
    t0 = time.perf_counter()
    all_chunks = c7.chunk_docs_for_config(cfg, docs)
    chunk_time = time.perf_counter() - t0
    all_chunks, n_capped = c7.cap_oversized(all_chunks)
    print(f"[{key}] {len(all_chunks)} chunks ({n_capped} over cap, split), "
          f"chunk time {chunk_time:.1f}s", flush=True)

    collection = _store_name(key)
    vstore = QdrantVectorStore(
        url=c7.QDRANT_URL, collection=collection,
        vector_size=c7.VECTOR_SIZE, timeout=120,
    )
    tindex = ElasticsearchTextIndex(url=c7.ES_URL, index=collection)
    await vstore.ensure_collection()
    await tindex.ensure_index()

    t1 = time.perf_counter()
    print(f"[{key}] embedding {len(all_chunks)} chunks ...", flush=True)
    vectors = await c7.embed_texts_async(client, [c.content for c in all_chunks])
    if len(vectors) != len(all_chunks):
        raise RuntimeError(
            f"[{key}] embed count {len(vectors)} != chunk count {len(all_chunks)}"
        )
    for c, v in zip(all_chunks, vectors, strict=True):
        c.embedding = v

    upsert_batch = 256
    for start in range(0, len(all_chunks), upsert_batch):
        batch = all_chunks[start : start + upsert_batch]
        await vstore.upsert(batch)
        await tindex.index(batch)
    await tindex.close()
    ingest_time = time.perf_counter() - t1
    return {
        "key": key, "label": cfg.label, "n_chunks": len(all_chunks),
        "n_capped": n_capped,
        "chunks_per_doc": len(all_chunks) / len(docs) if docs else 0.0,
        "chunk_time_s": chunk_time, "ingest_time_s": ingest_time,
    }


# --------------------------------------------------------------------------- #
# Evaluate one config against the real qrels (document-level BEIR metrics)
# --------------------------------------------------------------------------- #
async def evaluate_config(
    cfg,
    queries: dict[str, tuple[str, str]],
    qrels: dict[str, dict[str, int]],
    client: httpx.AsyncClient,
    rerank_pool: int,
    reranker: SidecarReranker,
) -> dict:
    """Retrieve chunks, map to docs, score vs qrels. Returns per-query metric arrays.

    For each query we pull a ``rerank_pool``-chunk hybrid candidate set, rerank with
    the cross-encoder, collapse chunks → a ranked list of unique ``doc_id``s (keeping
    each doc's best rank), and compute per-query nDCG@10 / recall@{10,20,100} / AP
    against the graded qrels. Per-query arrays feed the Part-2 stats layer.
    """
    key = cfg.key
    qids = sorted(queries, key=lambda q: int(q) if q.isdigit() else q)
    print(f"\n[{key}] evaluating {len(qids)} SciFact claim queries ...", flush=True)
    collection = _store_name(key)
    vstore = QdrantVectorStore(
        url=c7.QDRANT_URL, collection=collection,
        vector_size=c7.VECTOR_SIZE, timeout=120,
    )
    tindex = ElasticsearchTextIndex(url=c7.ES_URL, index=collection)
    embedder = make_embedder(
        api="openai", http=client, base_url=c7.SFR_ENDPOINTS[0], model=c7.SFR_MODEL
    )
    retriever = HybridRetriever(vstore, tindex, embedder)
    filters = scope_filters({}, c7.TENANT)
    sem = asyncio.Semaphore(c7.EVAL_CONCURRENCY)

    async def _rerank(query: str, chunks):
        last_exc: Exception | None = None
        for attempt in range(c7.RERANK_RETRIES):
            try:
                return await reranker.score(query, chunks)
            except (httpx.HTTPError, httpx.StreamError) as exc:
                last_exc = exc
                await asyncio.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"rerank failed after retries: {last_exc}")

    def _ranked_docs(chunk_doc_ids: list[str]) -> list[str]:
        """Collapse a ranked chunk list to unique doc_ids, best-rank-first."""
        seen: set[str] = set()
        out: list[str] = []
        for did in chunk_doc_ids:
            if did not in seen:
                seen.add(did)
                out.append(did)
        return out

    async def _eval_one(qid: str):
        query = queries[qid][0]
        rels = qrels[qid]
        async with sem:
            pool = await retriever.retrieve(
                query, top_k=rerank_pool, filters=filters,
                use_graph=False, tenant_id=c7.TENANT,
            )
            if not pool:
                return {"ndcg": 0.0, "ap": 0.0,
                        **{f"recall@{k}": 0.0 for k in RECALL_KS}}
            reranked = await _rerank(query, [h.chunk for h in pool])
            ranked_docs = _ranked_docs([sc.chunk.doc_id for sc in reranked])
            return {
                "ndcg": _stats.ndcg_at_k(ranked_docs, rels, NDCG_K),
                "ap": _stats.average_precision(ranked_docs, rels),
                **{f"recall@{k}": _stats.recall_at_k(ranked_docs, rels, k)
                   for k in RECALL_KS},
            }

    results = await asyncio.gather(*[_eval_one(q) for q in qids])
    await tindex.close()
    per_query = {
        "ndcg@10": [r["ndcg"] for r in results],
        "map": [r["ap"] for r in results],
        **{f"recall@{k}": [r[f"recall@{k}"] for r in results] for k in RECALL_KS},
    }
    means = {m: (sum(v) / len(v) if v else 0.0) for m, v in per_query.items()}
    return {"key": key, "per_query": per_query, "means": means}


# --------------------------------------------------------------------------- #
# Teardown scifact_m7_* only
# --------------------------------------------------------------------------- #
async def teardown(client: httpx.AsyncClient, keys: list[str]) -> bool:
    """Drop all scifact_m7_* stores; guard asserts the prefix. Returns True if gone."""
    print("\n[teardown] dropping scifact_m7_* collections and indices ...", flush=True)
    from qdrant_client import AsyncQdrantClient

    qc = AsyncQdrantClient(url=c7.QDRANT_URL, timeout=120)
    for key in keys:
        name = _store_name(key)
        assert name.startswith("scifact_m7"), name  # never touch prod/others
        try:
            await qc.delete_collection(collection_name=name)
            print(f"[teardown] dropped Qdrant collection {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[teardown] Qdrant {name}: {exc}")
    # Verify none remain.
    remaining_q: list[str] = []
    try:
        cols = await qc.get_collections()
        remaining_q = [c.name for c in cols.collections
                       if c.name.startswith("scifact_m7")]
    except Exception:  # noqa: BLE001
        pass
    await qc.close()
    remaining_es: list[str] = []
    for key in keys:
        name = _store_name(key)
        assert name.startswith("scifact_m7"), name
        try:
            r = await client.delete(f"{c7.ES_URL}/{name}", timeout=60.0)
            print(f"[teardown] dropped ES index {name} (HTTP {r.status_code})")
        except Exception as exc:  # noqa: BLE001
            print(f"[teardown] ES {name}: {exc}")
    try:
        r = await client.get(f"{c7.ES_URL}/_cat/indices/scifact_m7*?h=index",
                             timeout=30.0)
        remaining_es = [ln for ln in r.text.split() if ln.strip()]
    except Exception:  # noqa: BLE001
        pass
    gone = not remaining_q and not remaining_es
    if gone:
        print("[teardown] verified: no scifact_m7_* stores remain.", flush=True)
    else:
        print(f"[teardown] WARNING leftover Qdrant={remaining_q} ES={remaining_es}",
              flush=True)
    return gone


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def build_metrics_table(eval_stats: dict, keys: list[str]) -> str:
    header = "| config | nDCG@10 | recall@10 | recall@20 | recall@100 | MAP |\n"
    sep = "|" + "---|" * 6 + "\n"
    rows = ""
    for k in keys:
        m = eval_stats[k]["means"]
        rows += (
            f"| `{k}` | {m['ndcg@10']:.4f} | {m['recall@10']:.4f} | "
            f"{m['recall@20']:.4f} | {m['recall@100']:.4f} | {m['map']:.4f} |\n"
        )
    return header + sep + rows


def build_significance_section(eval_stats: dict, keys: list[str], n_q: int) -> str:
    ref = STATS_REFERENCE if STATS_REFERENCE in eval_stats else keys[0]
    metrics = {
        "nDCG@10": {k: eval_stats[k]["per_query"]["ndcg@10"] for k in keys},
        "recall@10": {k: eval_stats[k]["per_query"]["recall@10"] for k in keys},
        "recall@100": {k: eval_stats[k]["per_query"]["recall@100"] for k in keys},
        "MAP": {k: eval_stats[k]["per_query"]["map"] for k in keys},
    }
    # Primary = nDCG@10; the paired test uses per-query nDCG deltas as the signed
    # quantity (a per-query graded quality delta), Holm-corrected across configs.
    ndcg_pq = metrics["nDCG@10"]
    table, interp = _stats.build_stats_table(keys, ref, metrics, "nDCG@10", ndcg_pq)
    return (
        f"Reference config = `{ref}`. 95% CIs are paired bootstraps over the {n_q} "
        f"test claim queries (10,000 iters, seed 0). `ΔnDCG@10 vs {ref}` is the "
        f"paired bootstrap difference; the Wilcoxon column is a Holm–Bonferroni-"
        f"corrected signed-rank test on per-query nDCG@10 deltas.\n\n"
        f"{table}\n{interp}\n"
    )


def write_report(eval_stats, ingest_stats, keys, n_docs, n_q, source, live_eps):
    metrics_tbl = build_metrics_table(eval_stats, keys)
    sig = build_significance_section(eval_stats, keys, n_q)
    struct_rows = ""
    for k in keys:
        s = ingest_stats[k]
        struct_rows += (
            f"| `{k}` | {s['n_chunks']} | {s['chunks_per_doc']:.2f} | "
            f"{s['n_capped']} | {s['chunk_time_s']:.1f} | {s['ingest_time_s']:.1f} |\n"
        )
    struct = (
        "| config | #chunks | chunks/doc | overflow>cap | chunk s | ingest s |\n"
        "|" + "---|" * 6 + "\n" + struct_rows
    )
    body = f"""# SciFact (BEIR) passage-level chunking eval

Generated by `scripts/eval/scifact_chunk_eval.py`. Embedding model
`{c7.SFR_MODEL}` (4096-dim), hard cap {c7.HARD_CAP_TOKENS} tokens. Data source:
**{source}**.

## Setup

- **Corpus:** {n_docs} SciFact abstracts (title + text), one Document each; doc_id
  = BEIR corpus `_id`.
- **Queries:** {n_q} held-out test claim queries (those with test qrels).
- **Qrels:** document-level relevance judgments (graded); multiple relevant docs
  per query supported.
- **Embedding fleet:** {len(live_eps)} live SFR endpoint(s), round-robin.
- **Retrieval:** `HybridRetriever` (Qdrant dense + ES BM25, RRF), tenant `public`,
  isolated `{SCIFACT_PREFIX}_<config>` stores; rerank pool reranked by the
  crossencoder sidecar (`:50052`). Chunks are collapsed to unique doc_ids
  (best-rank-first) before scoring.
- **Metrics:** document-level nDCG@10 (primary), recall@{{10,20,100}}, MAP — the
  BEIR standard for SciFact.

## Chunk structure

{struct}
## Retrieval quality (document-level, real qrels)

{metrics_tbl}
## Statistical significance (paired bootstrap CIs + Wilcoxon/Holm)

{sig}
## Notes

- Unlike the known-item title→own-doc proxy, SciFact has real claim queries and
  multi-relevant document-level qrels, so it *can* discriminate chunkers. The
  difference-CI + Wilcoxon columns say whether any config separates from
  `{STATS_REFERENCE}` with statistical support.
- All `{SCIFACT_PREFIX}_*` stores are torn down at the end; the production SFR
  corpus is never touched.
"""
    REPORT_PATH.write_text(body, encoding="utf-8")

    import csv
    with CSV_PATH.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["config", "n_chunks", "chunks_per_doc", "overflow",
                    "ndcg@10", "recall@10", "recall@20", "recall@100", "map"])
        for k in keys:
            s, m = ingest_stats[k], eval_stats[k]["means"]
            w.writerow([k, s["n_chunks"], round(s["chunks_per_doc"], 3),
                        s["n_capped"], round(m["ndcg@10"], 4),
                        round(m["recall@10"], 4), round(m["recall@20"], 4),
                        round(m["recall@100"], 4), round(m["map"], 4)])


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
async def amain(args, live_eps) -> int:
    corpus_docs, queries, qrels, source = load_scifact()
    print(f"[data] source={source} corpus={len(corpus_docs)} "
          f"queries={len(queries)} qrels={sum(len(v) for v in qrels.values())} judg.",
          flush=True)
    if args.query_limit and args.query_limit < len(queries):
        keep = sorted(queries, key=lambda q: int(q) if q.isdigit() else q)[
            : args.query_limit
        ]
        queries = {q: queries[q] for q in keep}
        qrels = {q: qrels[q] for q in keep}
        print(f"[data] limited to {len(queries)} queries (--query-limit).", flush=True)

    keys = list(c7.CONFIG_KEYS)
    timeout = httpx.Timeout(300.0, connect=30.0)
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        reranker = SidecarReranker(c7.RERANKER_URL, http=client)
        ingest_stats: dict = {}
        for cfg in c7.CONFIGS:
            ingest_stats[cfg.key] = await ingest_config(cfg, corpus_docs, client)
        eval_stats: dict = {}
        for cfg in c7.CONFIGS:
            eval_stats[cfg.key] = await evaluate_config(
                cfg, queries, qrels, client, args.rerank_pool, reranker
            )

        write_report(eval_stats, ingest_stats, keys, len(corpus_docs),
                     len(queries), source, live_eps)
        print("\n" + "=" * 80 + "\nSCIFACT RESULTS\n" + "=" * 80)
        print(build_metrics_table(eval_stats, keys))
        print(build_significance_section(eval_stats, keys, len(queries)))
        print(f"Report written to {REPORT_PATH}")
        print(f"CSV written to {CSV_PATH}")

        if args.teardown:
            gone = await teardown(client, keys)
            if not gone:
                print("[teardown] WARNING: some scifact_m7_* stores remained.",
                      flush=True)
        else:
            print("\n[teardown] skipped (--no-teardown); scifact_m7_* left in place.")
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rerank-pool", type=int, default=100,
                   help="hybrid candidate pool size reranked per query")
    p.add_argument("--query-limit", type=int, default=0,
                   help="cap #test queries (0 = all)")
    p.add_argument("--endpoints", default=None,
                   help="comma-separated SFR base URLs (else the built-in 16)")
    p.add_argument("--embedding-api-key", default=None,
                   help="Bearer token for keyed endpoints (lambda13); keyless ignore it")
    p.add_argument("--hard-cap-tokens", type=int, default=c7.HARD_CAP_TOKENS)
    p.add_argument("--no-teardown", dest="teardown", action="store_false",
                   help="keep scifact_m7_* stores (default: tear down)")
    p.set_defaults(teardown=True)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    c7.HARD_CAP_TOKENS = args.hard_cap_tokens
    c7.EMBED_API_KEY = args.embedding_api_key or os.environ.get("OPENAI_API_KEY")
    if c7.EMBED_API_KEY:
        print("Using a Bearer token for keyed embedding endpoints.", flush=True)
    candidates = (
        [u.strip() for u in args.endpoints.split(",") if u.strip()]
        if args.endpoints else list(c7.DEFAULT_ENDPOINTS)
    )
    print(f"Probing {len(candidates)} candidate endpoints ...", flush=True)
    c7.SFR_ENDPOINTS = c7.detect_live_endpoints(candidates, c7.EMBED_API_KEY)
    if not c7.SFR_ENDPOINTS:
        raise SystemExit("No live embedding endpoints; aborting.")
    if len(c7.SFR_ENDPOINTS) < len(candidates):
        print(f"WARNING: only {len(c7.SFR_ENDPOINTS)}/{len(candidates)} endpoints live.",
              flush=True)
    print(f"Using {len(c7.SFR_ENDPOINTS)} live SFR endpoint(s).", flush=True)
    c7.TOKEN_COUNTER = HFTokenCounter(model=c7.SFR_MODEL)
    c7.TOKEN_COUNTER._tokenizer()
    print(f"Token counter ready. Hard cap {c7.HARD_CAP_TOKENS} tok.", flush=True)
    return asyncio.run(amain(args, c7.SFR_ENDPOINTS))


if __name__ == "__main__":
    raise SystemExit(main())
