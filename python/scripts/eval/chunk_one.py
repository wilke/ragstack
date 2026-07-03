#!/usr/bin/env python
"""Scatter step for the CWL chunking-eval workflow (ADR-0001, offline plane).

Ingests + scores exactly ONE chunking config against the SciFact (BEIR) benchmark
and writes a single ``metrics.json`` (per-query metric arrays + means). The CWL
workflow scatters this over the 7 configs — one independent task each, spread
across GPUs — and ``aggregate_stats.py`` gathers the files into the stats report.

This is a thin CLI over the already-factored SciFact harness: ``load_scifact`` →
``ingest_config`` → ``evaluate_config`` for the one config, then emit the metrics
file. The chunking / embedding / ingest / scoring logic is reused verbatim from
``scifact_chunk_eval`` + ``chunking_compare_7way`` (no fork — the #25 rule).

Unlike a bulk-ingest step this needs the live embedding fleet + Qdrant + ES (it
ingests into an isolated ``scifact_m7_<config>`` store and tears it down after),
so it is NOT run in CI — same as the harness it wraps. The isolated store and the
prefix-guarded teardown mean it never touches a production collection.

Usage::

    python scripts/eval/chunk_one.py --config fixed_tok512 \
        --embedding-api-key BRCMistral --out metrics.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from ragstack.ingestion.tokenization import HFTokenCounter
from ragstack.scoring.scorers import SidecarReranker

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import chunking_compare_7way as c7  # noqa: E402
import scifact_chunk_eval as sc  # noqa: E402


def _ordered_qids(queries: dict) -> list[str]:
    """The query id order evaluate_config scores in (numeric-aware sort), so the
    recorded query_ids line up 1:1 with the per_query arrays."""
    return sorted(queries, key=lambda q: int(q) if q.isdigit() else q)


def build_metrics_payload(config: str, source: str, stats: dict, query_ids: list[str]) -> dict:
    """Assemble the metrics.json payload from evaluate_config's return.

    Deterministic (no timestamp) so a re-run against an unchanged store diffs
    clean; the shape is exactly what aggregate_stats.load_metrics consumes.
    ``query_ids`` (aligned with the per_query arrays) let the gather step verify
    that every config was scored over the SAME queries before pairing them.
    """
    per_query = stats["per_query"]
    if len(query_ids) != len(per_query["ndcg@10"]):
        raise ValueError(
            f"query_ids ({len(query_ids)}) misaligned with per_query "
            f"({len(per_query['ndcg@10'])})"
        )
    return {
        "config": config,
        "source": source,
        "n_queries": len(query_ids),
        "query_ids": list(query_ids),
        "means": stats["means"],
        "per_query": per_query,
    }


async def amain(args, cfg) -> int:
    corpus_docs, queries, qrels, source = sc.load_scifact()
    print(f"[data] source={source} corpus={len(corpus_docs)} queries={len(queries)}",
          flush=True)
    if args.query_limit and args.query_limit < len(queries):
        keep = sorted(queries, key=lambda q: int(q) if q.isdigit() else q)[: args.query_limit]
        queries = {q: queries[q] for q in keep}
        qrels = {q: qrels[q] for q in keep}
        print(f"[data] limited to {len(queries)} queries (--query-limit).", flush=True)

    timeout = httpx.Timeout(300.0, connect=30.0)
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        reranker = SidecarReranker(c7.RERANKER_URL, http=client)
        await sc.ingest_config(cfg, corpus_docs, client)
        stats = await sc.evaluate_config(
            cfg, queries, qrels, client, args.rerank_pool, args.retrieve_pool, reranker
        )
        if args.teardown:
            await sc.teardown(client, [cfg.key])

    payload = build_metrics_payload(cfg.key, source, stats, _ordered_qids(queries))
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True),
                              encoding="utf-8")
    m = stats["means"]
    print(f"[{cfg.key}] nDCG@10={m['ndcg@10']:.4f} MAP={m['map']:.4f} → {args.out}",
          flush=True)
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", required=True, choices=c7.CONFIG_KEYS,
                   help="the single chunking config to ingest + score")
    p.add_argument("--out", default="metrics.json",
                   help="output metrics path (default: metrics.json)")
    p.add_argument("--corpus", default=None,
                   help="reserved — SciFact self-loads its corpus (cached under "
                        "HF_HOME/rag cache); accepted for CWL-input symmetry, ignored")
    p.add_argument("--query-limit", type=int, default=0, help="cap #test queries (0=all)")
    p.add_argument("--rerank-pool", type=int, default=100)
    p.add_argument("--retrieve-pool", type=int, default=300)
    p.add_argument("--endpoints", default=None,
                   help="comma-separated SFR base URLs (else the built-in defaults)")
    p.add_argument("--embedding-api-key", default=None,
                   help="Bearer token for keyed endpoints; keyless endpoints ignore it")
    p.add_argument("--hard-cap-tokens", type=int, default=c7.HARD_CAP_TOKENS)
    p.add_argument("--no-teardown", dest="teardown", action="store_false",
                   help="keep the scifact_m7_<config> store (default: tear down)")
    p.set_defaults(teardown=True)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.retrieve_pool < args.rerank_pool:
        raise SystemExit(
            f"--retrieve-pool ({args.retrieve_pool}) must be >= --rerank-pool "
            f"({args.rerank_pool})"
        )
    if args.corpus:
        print("NOTE: --corpus is ignored; SciFact self-loads its benchmark corpus.",
              file=sys.stderr)
    # Mirror the harness's global setup, for one config.
    c7.HARD_CAP_TOKENS = args.hard_cap_tokens
    c7.EMBED_API_KEY = args.embedding_api_key or os.environ.get("OPENAI_API_KEY")
    candidates = (
        [u.strip() for u in args.endpoints.split(",") if u.strip()]
        if args.endpoints else list(c7.DEFAULT_ENDPOINTS)
    )
    print(f"Probing {len(candidates)} candidate endpoints ...", flush=True)
    c7.SFR_ENDPOINTS = c7.detect_live_endpoints(candidates, c7.EMBED_API_KEY)
    if not c7.SFR_ENDPOINTS:
        raise SystemExit("No live embedding endpoints; aborting.")
    print(f"Using {len(c7.SFR_ENDPOINTS)} live SFR endpoint(s).", flush=True)
    c7.TOKEN_COUNTER = HFTokenCounter(model=c7.SFR_MODEL)
    c7.TOKEN_COUNTER._tokenizer()
    cfg = c7.CONFIG_BY_KEY[args.config]
    return asyncio.run(amain(args, cfg))


if __name__ == "__main__":
    raise SystemExit(main())
