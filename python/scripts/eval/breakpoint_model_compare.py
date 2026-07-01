"""Compare a cheap vs expensive embedding model for the semantic chunker's
BREAKPOINT embedding (issue #73).

The breakpoint embed is only used to compute cosine distances between adjacent
sentence-buffers -> where chunk boundaries fall. The STORED vectors are unaffected
(they use the main model). So the question is purely: does the cheap model place
the same boundaries? Same per-sentence buffers, identical prod breakpoint logic
(`SemanticChunker._breakpoint_groups` + `_merge_short`); only the model differs.

Metrics: (1) exact chunk-span Jaccard + internal-boundary F1 (expensive=ref),
(2) Spearman of per-pair distance sequences, (3) chunks/doc per model.

See reports/semantic-chunking-experiments.md for rationale, results, provenance.

Reproduce (coconut prod layout):

    . /rag/env.sh
    /rag/envs/ragstack/bin/python python/scripts/eval/breakpoint_model_compare.py

Env vars: INPUT, N_SAMPLE (10), REF_MODEL (Salesforce/SFR-Embedding-Mistral),
REF_URLS (comma-sep, default localhost:9001..9008), REF_KEY (BRCMistral),
CHEAP_URL (http://localhost:50053, the BGE sidecar), MAX_TOKENS (4096),
CHEAP_MAX_TOKENS (0 = use MAX_TOKENS; set 512 to bound buffers to BGE context),
BUFFER_SIZE (3), EMBED_SUBBATCH (32).
"""
import asyncio
import json
import os
import statistics
import sys

import httpx

from ragstack.embed_pool import make_pooled_embedder
from ragstack.embedders import make_embedder
from ragstack.ingestion.chunkers import (
    SemanticChunker,
    _cosine_distance,
    sentence_spans,
    split_text_to_token_budget,
)
from ragstack.ingestion.tokenization import make_token_counter
from ragstack.models import Document

INPUT = os.environ.get("INPUT", "/rag/ingest/inputs/09320c55-a8a7-4f4d-81b3-ae55b7a329fa.jsonl")
REF_MODEL = os.environ.get("REF_MODEL", "Salesforce/SFR-Embedding-Mistral")
REF_URLS = os.environ.get("REF_URLS", ",".join(f"http://localhost:900{i}" for i in range(1, 9))).split(",")
REF_KEY = os.environ.get("REF_KEY", "BRCMistral")
CHEAP_URL = os.environ.get("CHEAP_URL", "http://localhost:50053")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))
CHEAP_MAX_TOKENS = int(os.environ.get("CHEAP_MAX_TOKENS", "0")) or MAX_TOKENS
BUFFER_SIZE = int(os.environ.get("BUFFER_SIZE", "3"))
N_SAMPLE = int(os.environ.get("N_SAMPLE", "10"))
EMBED_SUBBATCH = int(os.environ.get("EMBED_SUBBATCH", "32"))


def _rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 3:
        return float("nan")
    ra, rb = _rank(a), _rank(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else float("nan")


async def embed_all(embedder, buffers):
    out = []
    for i in range(0, len(buffers), EMBED_SUBBATCH):
        out.extend(await embedder.embed(buffers[i : i + EMBED_SUBBATCH]))
    return out


def build_buffers(text, spans, tc, max_tokens):
    buffers = []
    for i in range(len(spans)):
        lo = max(0, i - BUFFER_SIZE)
        hi = min(i + 1 + BUFFER_SIZE, len(spans))
        buf = text[spans[lo][0] : spans[hi - 1][1]]
        if tc.count(buf) > max_tokens:
            buf = split_text_to_token_budget(buf, max_tokens, tc)[0]
        buffers.append(buf)
    return buffers


def spans_from_emb(chunker, spans, emb):
    dist = [_cosine_distance(emb[i], emb[i + 1]) for i in range(len(emb) - 1)]
    groups = chunker._breakpoint_groups(dist, len(spans))
    cs = [(spans[s][0], spans[e - 1][1]) for s, e in groups if e > s]
    return chunker._merge_short(cs), dist


async def main():
    tc = make_token_counter("hf", model=REF_MODEL)
    chunker = SemanticChunker(
        embed_fn=lambda b: [], buffer_size=BUFFER_SIZE, breakpoint_percentile_threshold=80.0,
        min_chunk_length=500, max_tokens=MAX_TOKENS, token_counter=tc,
    )
    docs = []
    with open(INPUT, encoding="utf-8") as fh:
        for line in fh:
            if len(docs) >= N_SAMPLE:
                break
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = rec.get("text", "") or ""
            if len(t) >= 2000:
                docs.append(Document(id=rec.get("path", "x"), content=t, metadata={}, source=""))

    async with httpx.AsyncClient(timeout=180.0) as http:
        ref = make_pooled_embedder("openai", http, REF_URLS, model=REF_MODEL,
                                   api_key=REF_KEY, max_concurrency=8)
        cheap = make_embedder(api="sidecar", http=http, base_url=CHEAP_URL)

        jaccards, spears, n_ref, n_cheap = [], [], [], []
        tp = fp = fn = 0
        print(f"comparing {len(docs)} docs (ref={REF_MODEL} vs cheap={CHEAP_URL}) "
              f"cheap_max_tok={CHEAP_MAX_TOKENS} ...", file=sys.stderr)
        for k, d in enumerate(docs):
            spans = sentence_spans(d.content)
            if len(spans) <= 1:
                continue
            buf_ref = build_buffers(d.content, spans, tc, MAX_TOKENS)
            buf_cheap = (buf_ref if CHEAP_MAX_TOKENS == MAX_TOKENS
                         else build_buffers(d.content, spans, tc, CHEAP_MAX_TOKENS))
            emb_ref = await embed_all(ref, buf_ref)
            emb_cheap = await embed_all(cheap, buf_cheap)
            cs_ref, dist_ref = spans_from_emb(chunker, spans, emb_ref)
            cs_cheap, dist_cheap = spans_from_emb(chunker, spans, emb_cheap)

            inter = len(set(cs_ref) & set(cs_cheap))
            union = len(set(cs_ref) | set(cs_cheap))
            jaccards.append(inter / union if union else 1.0)
            b_ref = {s for s, _ in cs_ref if s != 0}
            b_cheap = {s for s, _ in cs_cheap if s != 0}
            tp += len(b_ref & b_cheap)
            fp += len(b_cheap - b_ref)
            fn += len(b_ref - b_cheap)
            sp = spearman(dist_ref, dist_cheap)
            if sp == sp:
                spears.append(sp)
            n_ref.append(len(cs_ref))
            n_cheap.append(len(cs_cheap))
            print(f"  doc {k}: sents={len(spans)} chunks ref={len(cs_ref)} cheap={len(cs_cheap)} "
                  f"jaccard={jaccards[-1]:.2f} spearman={sp:.2f}", file=sys.stderr)

    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    print("\n=== breakpoint model comparison: cheap vs ref ===")
    print(f"docs={len(jaccards)}  buffer_size={BUFFER_SIZE} pct=80 min_len=500 "
          f"ref_max_tok={MAX_TOKENS} cheap_max_tok={CHEAP_MAX_TOKENS}")
    print(f"  chunk-span Jaccard (exact):     mean={statistics.mean(jaccards):.3f}  median={statistics.median(jaccards):.3f}")
    print(f"  internal-boundary F1 (ref):     {f1:.3f}   (precision={prec:.3f} recall={rec:.3f})")
    print(f"  distance Spearman:              mean={statistics.mean(spears):.3f}  median={statistics.median(spears):.3f}")
    print(f"  chunks/doc:                     ref mean={statistics.mean(n_ref):.1f}  cheap mean={statistics.mean(n_cheap):.1f}")


if __name__ == "__main__":
    asyncio.run(main())
