#!/usr/bin/env python
"""#754 evaluation baseline: Anthropic-style "Contextual Retrieval" (2024)
context sentences for every chunk of one index. EVALUATION ONLY — there is no
ingest support for this, and none is proposed by it.

For each chunk, one LLM call receives the chunk's document (or a window of it,
below) and the chunk, and writes a short context sentence situating the chunk in
the document — Anthropic's prompt, unchanged in substance, with no
symbol-specific instruction (it is the generic baseline the per-document symbol
glossary is compared against).

* **Read-only** on the index (PIT + search via ``reassembly.scroll_index``), or
  from a local chunk dump (``--dump``).
* **Cached and resumable:** one JSONL record per chunk keyed by
  ``sha256(document window) : sha256(chunk) : model : prompt hash``. A re-run
  skips cached chunks. Keep the cache OUTSIDE the repo — it holds model output
  about the corpus.
* **Truncation:** a document of at most ``--window-chars`` (120k chars, ~30-36k
  tokens; the model's context is 60k tokens) is sent whole. A longer one is cut
  into fixed blocks: the chunk gets the ``--window-chars`` window starting at
  ``(start_char // stride) * stride`` (stride = window − 20k), which always
  contains a chunk shorter than 20k chars, and which every chunk in the same
  stride shares, so the server's prefix cache still applies.
* **Polite:** at most ``--concurrency`` (default 4, the cap) calls in flight;
  documents are processed in order, chunks of one document consecutively, so
  the document prefix is reused. Logs throughput and the total call count.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

from ragstack.ingestion.reassembly import reassemble
from ragstack.llm import OpenAILLM

log = logging.getLogger("contextual_chunks")

# Anthropic, "Introducing Contextual Retrieval" (2024), the situating prompt.
PROMPT = """<document>
{document}
</document>
Here is the chunk we want to situate within the whole document
<chunk>
{chunk}
</chunk>
Please give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. Answer only with the succinct context and nothing else."""

MAX_CONCURRENCY = 4


def prompt_hash() -> str:
    return hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:12]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def window_for(doc_text: str, start_char: int, window: int) -> str:
    if len(doc_text) <= window:
        return doc_text
    stride = max(1, window - 20_000)
    begin = (max(0, start_char) // stride) * stride
    return doc_text[begin : begin + window]


def cache_key(doc_window: str, chunk: str, model: str) -> str:
    return f"{sha(doc_window)}:{sha(chunk)}:{model}:{prompt_hash()}"


def load_cache(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                out[rec["k"]] = rec["c"]
            except (json.JSONDecodeError, KeyError):
                continue
    return out


def load_dump(path: Path) -> dict[str, list[dict[str, Any]]]:
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            by_doc[str(r["doc_id"])].append(r)
    return by_doc


async def run(args: argparse.Namespace) -> dict[str, Any]:
    endpoint = args.llm_endpoint or os.environ.get("LLM_ENDPOINT", "")
    model = args.llm_model or os.environ.get("LLM_MODEL", "")
    if not endpoint or not model:
        raise SystemExit("set --llm-endpoint/--llm-model or LLM_ENDPOINT/LLM_MODEL")
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or None
    by_doc = load_dump(Path(args.dump))
    cache_path = Path(args.cache)
    cache = load_cache(cache_path)
    todo: list[tuple[str, str, str]] = []  # (key, window, chunk)
    total = 0
    for doc_id in sorted(by_doc):
        rows = sorted(by_doc[doc_id], key=lambda r: int(r["start_char"]))
        text = reassemble(doc_id, rows).text
        for r in rows:
            total += 1
            win = window_for(text, int(r["start_char"]), args.window_chars)
            key = cache_key(win, str(r["content"]), model)
            if key not in cache:
                todo.append((key, win, str(r["content"])))
    cached_before = total - len(todo)
    if args.limit:
        todo = todo[: args.limit]
    log.info("%d chunks, %d cached, %d to generate (%s, prompt %s)",
             total, cached_before, len(todo), model, prompt_hash())
    stats = {"chunks": total, "cached_before": cached_before, "calls": 0, "failed": 0}
    sem = asyncio.Semaphore(min(MAX_CONCURRENCY, max(1, args.concurrency)))
    t0 = time.monotonic()
    fh = cache_path.open("a", encoding="utf-8")
    async with httpx.AsyncClient(timeout=300) as http:
        llm = OpenAILLM(endpoint, model, http, api_key=api_key)

        async def one(key: str, win: str, chunk: str) -> None:
            async with sem:
                for attempt in range(3):
                    try:
                        stats["calls"] += 1
                        text, _ = await llm.complete_detailed(
                            [{"role": "user", "content": PROMPT.format(document=win, chunk=chunk)}],
                            max_tokens=args.max_tokens, temperature=0.0,
                        )
                        break
                    except Exception as e:  # noqa: BLE001
                        log.warning("call failed (%s), attempt %d", type(e).__name__, attempt + 1)
                        await asyncio.sleep(3 * (attempt + 1))
                else:
                    stats["failed"] += 1
                    return
            fh.write(json.dumps({"k": key, "c": " ".join(text.split())}, ensure_ascii=False) + "\n")
            fh.flush()
            done = stats["calls"]
            if done % 200 == 0:
                el = time.monotonic() - t0
                log.info("%d calls in %.0fs (%.2f calls/s)", done, el, done / max(el, 1e-9))

        # Bounded task creation: at most a few hundred pending at once.
        pending: set[asyncio.Task[None]] = set()
        for item in todo:
            pending.add(asyncio.create_task(one(*item)))
            if len(pending) >= 64:
                _, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        if pending:
            await asyncio.gather(*pending)
    fh.close()
    el = time.monotonic() - t0
    stats["seconds"] = round(el, 1)
    stats["calls_per_s"] = round(stats["calls"] / max(el, 1e-9), 3)
    log.info("done: %s", stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dump", required=True, help="chunk dump JSONL (glossary_scratch_experiment --dump)")
    p.add_argument("--cache", required=True, help="context cache JSONL (outside the repo)")
    p.add_argument("--llm-endpoint", default="")
    p.add_argument("--llm-model", default="")
    p.add_argument("--concurrency", type=int, default=MAX_CONCURRENCY)
    p.add_argument("--window-chars", type=int, default=120_000)
    p.add_argument("--max-tokens", type=int, default=160)
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    stats = asyncio.run(run(args))
    print(json.dumps(stats, sort_keys=True))
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
