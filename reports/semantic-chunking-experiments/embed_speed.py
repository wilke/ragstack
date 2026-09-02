"""Measure embed throughput of the breakpoint-embedding candidates (issue #73).

Times embedding the SAME real sentence-buffers with each model/serving. Reveals
that "smaller model = faster" depends entirely on SERVING: BGE-base on the CPU
sidecar is slower than SFR-Mistral on the vLLM H200 fleet. A fair model-vs-model
comparison needs the small model served on GPU (vLLM), which is not set up.

NOTE: SFR endpoints are under live prod load, so SFR numbers are a *loaded* lower
bound on capacity (the real gap on an idle fleet is larger).

See reports/semantic-chunking-experiments.md. Reproduce:

    . /rag/env.sh
    /rag/envs/ragstack/bin/python reports/semantic-chunking-experiments/embed_speed.py

Env vars: INPUT, N_BUFFERS (256), SUB (32), REF_MODEL, REF_URLS (comma-sep),
REF_KEY (REQUIRED, no default — empty for keyless endpoints), CHEAP_URL.
"""
import asyncio
import json
import os
import time

import httpx

from ragstack.embed_pool import make_pooled_embedder
from ragstack.embedders import make_embedder
from ragstack.ingestion.chunkers import sentence_spans

INPUT = os.environ.get("INPUT", "/rag/ingest/inputs/09320c55-a8a7-4f4d-81b3-ae55b7a329fa.jsonl")
REF_MODEL = os.environ.get("REF_MODEL", "Salesforce/SFR-Embedding-Mistral")
REF_URLS = os.environ.get("REF_URLS", ",".join(f"http://localhost:900{i}" for i in range(1, 9))).split(",")
# REF_KEY is REQUIRED and has NO default — see breakpoint_model_compare.py.
# Export it empty for the keyless endpoints; unset is an error, not "keyless".
if "REF_KEY" not in os.environ:
    raise SystemExit(
        "REF_KEY is not set and has no default. Export REF_KEY with the bearer key "
        "for the endpoints in REF_URLS, or REF_KEY='' for keyless endpoints."
    )
REF_KEY = os.environ["REF_KEY"]
CHEAP_URL = os.environ.get("CHEAP_URL", "http://localhost:50053")
N_BUFFERS = int(os.environ.get("N_BUFFERS", "256"))
SUB = int(os.environ.get("SUB", "32"))


def buffers_from_input(n):
    bufs = []
    with open(INPUT, encoding="utf-8") as fh:
        for line in fh:
            if len(bufs) >= n:
                break
            line = line.strip()
            if not line:
                continue
            try:
                text = json.loads(line).get("text") or ""
            except json.JSONDecodeError:
                continue
            spans = sentence_spans(text)
            for i in range(len(spans)):
                if len(bufs) >= n:
                    break
                lo, hi = max(0, i - 3), min(i + 1 + 3, len(spans))
                bufs.append(text[spans[lo][0] : spans[hi - 1][1]])
    return bufs


async def timed(embedder, bufs, label):
    await embedder.embed(bufs[:SUB])  # warmup
    t0 = time.perf_counter()
    for i in range(0, len(bufs), SUB):
        await embedder.embed(bufs[i : i + SUB])
    dt = time.perf_counter() - t0
    rate = len(bufs) / dt
    print(f"  {label:28s} {len(bufs)} buffers in {dt:6.2f}s -> {rate:7.1f} embeds/s")
    return rate


async def main():
    bufs = buffers_from_input(N_BUFFERS)
    print(f"buffers={len(bufs)} avg_chars={sum(len(b) for b in bufs)/len(bufs):.0f}")
    async with httpx.AsyncClient(timeout=180.0) as http:
        cheap = make_embedder(api="sidecar", http=http, base_url=CHEAP_URL)
        ref1 = make_embedder(api="openai", http=http, base_url=REF_URLS[0],
                             model=REF_MODEL, api_key=REF_KEY)
        refN = make_pooled_embedder("openai", http, REF_URLS, model=REF_MODEL,
                                    api_key=REF_KEY, max_concurrency=8)
        r_cheap = await timed(cheap, bufs, "cheap sidecar (CPU?)")
        r_ref1 = await timed(ref1, bufs, "ref 1 endpoint (loaded)")
        r_refN = await timed(refN, bufs, f"ref pooled x{len(REF_URLS)} (loaded)")
    print(f"\n  cheap vs ref(1-endpoint): {r_cheap / r_ref1:5.1f}x")
    print(f"  cheap vs ref(pooled):     {r_cheap / r_refN:5.1f}x  (ref under live load)")


if __name__ == "__main__":
    asyncio.run(main())
