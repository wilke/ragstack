"""Shared plumbing for the stage-1 24-config grid on the Leg A (CDS) pilot set.

Extends the step-3 harness (../step3/{chunk3,embed3,score3}.py). Its design is kept
deliberately: **zero store writes** — no Qdrant/ES client is constructed anywhere in
this harness family; retrieval is exact brute-force cosine over in-memory embeddings.

Two things this module owns that step 3 did not need:

* ``pin_repo()`` — the ``/rag/envs/ragstack`` environment carries an editable-install
  META-PATH FINDER pointing at ``/rag/repos/ragstack`` (a different commit). A meta-path
  finder runs BEFORE ``sys.path``, so ``PYTHONPATH`` does not win. It is removed here and
  re-applied in every multiprocessing worker initialiser.
* ``Fleet`` — the embedding client, with step-3's politeness policy unchanged (<=2
  in-flight per endpoint, <=16 items / 8192 est. tokens per request) plus an optional
  text->vector cache for the semantic breakpoint pass, which accounts NOTIONAL vs ACTUAL
  tokens separately so the cost model stays honest.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

REPO_PY = "/home/wilke/Development/ragstack/python"
HERE = pathlib.Path(__file__).resolve().parent
STEP2 = HERE.parent / "step2"
XML = STEP2 / "xml"

ENDPOINTS = [f"http://localhost:{p}" for p in range(9001, 9007)]  # 6 only: GPUs 6/7 reserved
MODEL = "Salesforce/SFR-Embedding-Mistral"
DIM = 4096
MAX_ITEMS = 16          # step-3 policy, unchanged (the 164k tok/s comparison depends on it)
MAX_BATCH_TOKENS = 8192
PER_ENDPOINT = 2        # politeness: <=2 in-flight per endpoint => 12 global
RERANKER_URL = "http://localhost:50052"


def pin_repo() -> None:
    """Force ``ragstack`` to resolve to the working copy at d225cea, and prove it."""
    sys.meta_path[:] = [
        f for f in sys.meta_path if "editable" not in type(f).__module__
    ]
    for p in (REPO_PY, os.path.join(REPO_PY, "scripts", "eval")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import ragstack

    if not ragstack.__file__.startswith(REPO_PY):
        raise SystemExit(
            f"ragstack resolved to {ragstack.__file__!r}, not the working copy under "
            f"{REPO_PY!r} — the editable-install finder for /rag/repos won. Refusing to "
            f"run: the two checkouts are different commits."
        )


def stage1_configs():
    """The 24 committed grid cells, imported (never re-declared) from the repo."""
    import chunking_compare_7way as C

    return list(C.STAGE1_CONFIGS), C.HARD_CAP_TOKENS


# --------------------------------------------------------------------------- #
# Embedding fleet
# --------------------------------------------------------------------------- #
def make_batches(items, ntoks, max_items=MAX_ITEMS, max_tokens=MAX_BATCH_TOKENS):
    """Step-3's batching, verbatim in behaviour: <=max_items and <=max_tokens each.

    Returns [(start_index, count)] so results can be written back in order.
    """
    out, start, n, tok = [], 0, 0, 0
    for i in range(len(items)):
        t = max(int(ntoks[i]), 1)
        if n and (n >= max_items or tok + t > max_tokens):
            out.append((start, n))
            start, n, tok = i, 0, 0
        n += 1
        tok += t
    if n:
        out.append((start, n))
    return out


class Fleet:
    """Embedding client over :9001-:9006 with step-3's in-flight budget.

    ``slots`` holds each endpoint ``PER_ENDPOINT`` times; a request must hold a slot,
    which caps concurrency at 2 per endpoint *and* 12 globally without any round-robin
    guesswork. Counters separate NOTIONAL (what the configuration implies a production
    ingest would embed) from ACTUAL (what was sent after cache hits) — see PREREG §8.1.
    """

    def __init__(self, cache: bool = False):
        import httpx

        self._httpx = httpx
        self.client = httpx.Client(
            timeout=600, limits=httpx.Limits(max_connections=64, max_keepalive_connections=32)
        )
        self.slots: queue.Queue = queue.Queue()
        for e in ENDPOINTS:
            for _ in range(PER_ENDPOINT):
                self.slots.put(e)
        self.pool = ThreadPoolExecutor(len(ENDPOINTS) * PER_ENDPOINT)
        self.lock = threading.Lock()
        self.actual_tokens = 0
        self.actual_items = 0
        self.notional_tokens = 0
        self.notional_items = 0
        self.requests = 0
        self.retries = 0
        self.cache: dict[bytes, np.ndarray] | None = {} if cache else None
        self.cache_lock = threading.Lock()

    # -- low level ---------------------------------------------------------- #
    def _post(self, texts: list[str], ntok: int) -> list[list[float]]:
        ep = self.slots.get()
        try:
            for attempt in range(5):
                try:
                    r = self.client.post(
                        ep + "/v1/embeddings", json={"model": MODEL, "input": texts}
                    )
                    r.raise_for_status()
                    data = sorted(r.json()["data"], key=lambda d: d["index"])
                    if len(data) != len(texts):
                        raise RuntimeError(f"{len(data)} vectors for {len(texts)} inputs")
                    with self.lock:
                        self.requests += 1
                        self.actual_items += len(texts)
                        self.actual_tokens += ntok
                    return [d["embedding"] for d in data]
                except Exception as e:  # noqa: BLE001 - retried, then re-raised
                    with self.lock:
                        self.retries += 1
                    if attempt == 4:
                        raise
                    print(f"  retry {ep}: {type(e).__name__} {e}", flush=True)
                    time.sleep(2 * (attempt + 1))
            raise AssertionError("unreachable")
        finally:
            self.slots.put(ep)

    def embed(self, texts, ntoks, dtype=np.float32, label="", every=200):
        """Embed ``texts`` in order. No cache. Returns (len(texts), DIM) ``dtype``."""
        with self.lock:
            self.notional_items += len(texts)
            self.notional_tokens += int(sum(ntoks))
        batches = make_batches(texts, ntoks)
        out = np.zeros((len(texts), DIM), dtype=dtype)
        t0 = time.time()
        done = [0]

        def one(start_n):
            start, n = start_n
            vecs = self._post(texts[start:start + n], int(sum(ntoks[start:start + n])))
            out[start:start + n] = np.asarray(vecs, dtype=np.float32)
            with self.lock:
                done[0] += 1
                d = done[0]
            if every and d % every == 0:
                el = time.time() - t0
                print(f"  [{label}] {d}/{len(batches)} batches {el:.0f}s "
                      f"({d / len(batches) * 100:.0f}%)", flush=True)

        list(self.pool.map(one, batches))
        return out

    def embed_cached(self, texts, ntoks, label="", every=200):
        """Embed with a shared text->vector cache. Returns a float32 array.

        Exactly equivalent to :meth:`embed`: identical input text, identical model,
        identical vector. Only ACTUAL counters move on a hit; NOTIONAL counts every
        text, so the per-config cost a production ingest would pay is still reported.
        """
        assert self.cache is not None, "Fleet(cache=True) required"
        with self.lock:
            self.notional_items += len(texts)
            self.notional_tokens += int(sum(ntoks))
        keys = [hashlib.blake2b(t.encode("utf-8"), digest_size=16).digest() for t in texts]
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        miss_idx, miss_seen = [], set()
        with self.cache_lock:
            for i, k in enumerate(keys):
                hit = self.cache.get(k)
                if hit is not None:
                    out[i] = hit
                elif k not in miss_seen:      # dedupe within this call too
                    miss_seen.add(k)
                    miss_idx.append(i)
        if miss_idx:
            mt = [texts[i] for i in miss_idx]
            mn = [ntoks[i] for i in miss_idx]
            vecs = self.embed_raw(mt, mn, label=label, every=every)
            with self.cache_lock:
                for j, i in enumerate(miss_idx):
                    self.cache[keys[i]] = vecs[j]
        # fill every position (including intra-call duplicates) from the cache
        with self.cache_lock:
            for i, k in enumerate(keys):
                v = self.cache.get(k)
                if v is not None:
                    out[i] = v
        return out

    def embed_raw(self, texts, ntoks, label="", every=200):
        """:meth:`embed` without touching the notional counters (cache-miss path)."""
        batches = make_batches(texts, ntoks)
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        t0 = time.time()
        done = [0]

        def one(start_n):
            start, n = start_n
            vecs = self._post(texts[start:start + n], int(sum(ntoks[start:start + n])))
            out[start:start + n] = np.asarray(vecs, dtype=np.float32)
            with self.lock:
                done[0] += 1
                d = done[0]
            if every and d % every == 0:
                print(f"  [{label}] {d}/{len(batches)} batches {time.time()-t0:.0f}s", flush=True)

        list(self.pool.map(one, batches))
        return out

    def stats(self) -> dict:
        return {
            "actual_tokens": self.actual_tokens,
            "actual_items": self.actual_items,
            "notional_tokens": self.notional_tokens,
            "notional_items": self.notional_items,
            "requests": self.requests,
            "retries": self.retries,
            "cache_entries": len(self.cache) if self.cache is not None else 0,
        }


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def atomic_json(path: pathlib.Path, obj) -> None:
    """Write ``obj`` to ``path`` atomically, so a checkpoint is never half-written."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def doc_text(root, article_prose, front_meta) -> str:
    """``title \\n\\n abstract \\n\\n body`` — the step-2/step-3 text, unchanged."""
    abstract, body = article_prose(root)
    title = front_meta(root).get("title", "")
    return "\n\n".join(x for x in (title, abstract, body) if x)
