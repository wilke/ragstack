"""semantic vs semantic_pooled on IDENTICAL corpus extraction, through the real chunkers.

Corpus-scale successor of docs/plans/results/semantic-vs-pooled-2026-09-18/compare.py.
Differences from the 09-18 script, all deliberate:

* the RAW per-document distance series are written to disk (the 09-18 known gap);
* each arm embeds ONCE per run: distances come from ``SemanticChunker._buffer_embeddings``
  and chunk spans are derived by the chunker's own ``_breakpoint_groups`` →
  ``_merge_short`` → ``_emit`` sequence (``SemanticChunker.chunk`` lines 199-219 in
  v1.6.4), instead of calling ``chunk()`` and paying the embed twice;
* per-chunk token counts (HF tokenizer of the embedding model) are stored so the
  chunk-length distribution needs no second pass;
* ``--tag`` names the run, so two runs (run1/run2) double as the reproducibility control.

Runs INSIDE ragstack-worker-v1.6.4.sif. Embedding endpoints are the BULK ones (:9005/:9006).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, "/opt/ragstack/scripts")

from ragstack.ingestion.chunkers import _cosine_distance, make_chunker, sentence_spans
from ragstack.ingestion.embed_bridge import SyncEmbedBridge
from ragstack.ingestion.loaders import Document
from ragstack.ingestion.tokenization import make_token_counter

ap = argparse.ArgumentParser()
ap.add_argument("jsonl")
ap.add_argument("outdir")
ap.add_argument("--tag", default="run1")
ap.add_argument("--url", action="append", default=None)
ap.add_argument("--model", default="Salesforce/SFR-Embedding-Mistral")
ap.add_argument("--buffer-size", type=int, default=3)
ap.add_argument("--percentile", type=float, default=80.0)
ap.add_argument("--min-chunk-length", type=int, default=500)
ap.add_argument("--arms", default="semantic,semantic_pooled")
a = ap.parse_args()
URLS = a.url or ["http://localhost:9005", "http://localhost:9006"]
os.makedirs(a.outdir, exist_ok=True)

tc = make_token_counter("hf", model=a.model)


class Counting:
    """Wraps the bridge; records how many TEXTS and TOKENS each arm embeds, and the embed wall time."""

    def __init__(self, inner):
        self.inner, self.texts, self.tokens, self.calls, self.embed_s = inner, 0, 0, 0, 0.0

    def __call__(self, texts):
        self.texts += len(texts)
        self.tokens += sum(tc.count(t) for t in texts)
        self.calls += 1
        t0 = time.perf_counter()
        try:
            return self.inner(texts)
        finally:
            self.embed_s += time.perf_counter() - t0


docs = []
for line in open(a.jsonl, encoding="utf-8"):
    r = json.loads(line)
    fn = (r.get("metadata") or {}).get("filename") or os.path.basename(r.get("path", ""))
    docs.append(Document(id=fn, content=r["text"], metadata={}, source=fn))
print(f"{len(docs)} docs, {sum(len(d.content) for d in docs)} chars, tag={a.tag}, urls={URLS}")

# The TOOL's own bridge + chunker builders, so this runs on the same path the GoWe
# worker ran for the two live arms: `ingest_shard.py … --chunk-method <m> --chunk-size 256
# --chunk-overlap 32 --embedding-model <model> --embedding-url <urls>` with no semantic
# params on the CLI (the collection's chunk_params were {} → build_chunker defaults
# buffer 3 / p80 / min 500) and the token budget resolved from GET <url>/v1/models
# (max_model_len 4096 − reserve 16 = 4080). Only the endpoints differ (:9005/:9006 bulk
# here vs :9001/:9002 in the worker command).
import ingest_shard  # noqa: E402


def _args(method):
    return ingest_shard.parse_args(
        ["/dev/null", "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1",
         "--chunk-method", method, "--chunk-size", "256", "--chunk-overlap", "32",
         "--embedding-model", a.model, "--embedding-url", *URLS])


bridge = ingest_shard._build_bridge(_args("semantic_pooled"))

summary = {"tag": a.tag, "model": a.model, "urls": URLS, "buffer_size": a.buffer_size,
           "percentile": a.percentile, "min_chunk_length": a.min_chunk_length,
           "n_docs": len(docs), "chars": sum(len(d.content) for d in docs), "arms": {}}
try:
    for method in a.arms.split(","):
        counter = Counting(bridge)
        ch = ingest_shard._build_chunker(_args(method), None, embed_fn=counter)
        assert (ch.buffer_size, ch.breakpoint_percentile_threshold, ch.min_chunk_length) == (
            a.buffer_size, a.percentile, a.min_chunk_length), (
            ch.buffer_size, ch.breakpoint_percentile_threshold, ch.min_chunk_length)
        print(f"{method}: chunker={type(ch).__name__} pool={ch.pool_sentences} round={ch.distance_round} "
              f"max_tokens={ch.max_tokens} breakpoint_max_tokens={ch.breakpoint_max_tokens} "
              f"max_breakpoint_sentences={ch.max_breakpoint_sentences}", flush=True)
        dists_out, spans_out, per_doc = {}, {}, {}
        t_arm = time.perf_counter()
        for d in docs:
            text = d.content
            sp = sentence_spans(text)
            assert 1 < len(sp) <= (ch.max_breakpoint_sentences or 10**9), (d.id, len(sp))
            t0 = time.perf_counter()
            vecs = ch._buffer_embeddings(text, sp)
            dd = [_cosine_distance(vecs[i], vecs[i + 1]) for i in range(len(vecs) - 1)]
            if ch.distance_round is not None:
                dd = [round(x, ch.distance_round) for x in dd]
            # SemanticChunker.chunk, lines 208-219 (v1.6.4), verbatim sequence:
            groups = ch._breakpoint_groups(dd, len(sp))
            chunk_spans = [(sp[s][0], sp[e - 1][1]) for s, e in groups if e > s]
            chunk_spans = ch._merge_short(chunk_spans)
            chunks = [c for s, e in chunk_spans for c in ch._emit(d, s, e)]
            dists_out[d.id] = dd
            spans_out[d.id] = [
                {"start": c.start_char, "end": c.end_char, "chars": c.end_char - c.start_char,
                 "tokens": tc.count(c.content)} for c in chunks]
            per_doc[d.id] = {"n_sentences": len(sp), "n_chunks": len(chunks),
                             "doc_s": round(time.perf_counter() - t0, 3)}
            print(f"  {method:16s} {d.id:16s} sent={len(sp):5d} chunks={len(chunks):4d} "
                  f"{per_doc[d.id]['doc_s']:7.1f}s", flush=True)
        wall = time.perf_counter() - t_arm
        summary["arms"][method] = {
            "texts_embedded": counter.texts, "tokens_embedded": counter.tokens,
            "embed_calls": counter.calls, "embed_s": round(counter.embed_s, 2),
            "wall_s": round(wall, 2), "distance_round": ch.distance_round,
            "max_tokens": ch.max_tokens, "pool_sentences": ch.pool_sentences,
            "n_chunks": sum(v["n_chunks"] for v in per_doc.values()),
            "n_distance_pairs": sum(len(v) for v in dists_out.values()),
            "per_doc": per_doc,
        }
        json.dump(dists_out, open(f"{a.outdir}/distances-{method}-{a.tag}.json", "w"))
        json.dump(spans_out, open(f"{a.outdir}/spans-{method}-{a.tag}.json", "w"))
        print(f"{method}: texts={counter.texts} tokens={counter.tokens} chunks={summary['arms'][method]['n_chunks']} "
              f"embed={counter.embed_s:.1f}s wall={wall:.1f}s", flush=True)
finally:
    bridge.close()

json.dump(summary, open(f"{a.outdir}/summary-{a.tag}.json", "w"), indent=1)
print("wrote", f"{a.outdir}/summary-{a.tag}.json")
