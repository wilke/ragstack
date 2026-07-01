"""Profile the CPU vs GPU split of the semantic chunker, in isolation.

Runs the real ``SemanticChunker`` with prod params over a sample of real docs,
with a MOCK embed_fn (instant, content-varied vectors) so we measure ONLY the
CPU cost — no GPU contention with a running ingest. Answers: can #66 phase-2
(concurrent chunking) recover wall-clock, or is the ceiling GPU embed throughput
(the per-sentence breakpoint embed)?

See reports/semantic-chunking-experiments.md for rationale, results, provenance.

Reproduce (prod env has the HF tokenizer; a env without `transformers` silently
falls back to the estimate counter and UNDERCOUNTS the token-count CPU cost):

    . /rag/env.sh
    INPUT=/rag/ingest/inputs/<file>.jsonl EMBED_MODEL=Salesforce/SFR-Embedding-Mistral \\
      /rag/envs/ragstack/bin/python python/scripts/eval/profile_semantic_cpu.py

Env vars: INPUT, EMBED_MODEL, MAX_TOKENS (4096), BUFFER_SIZE (3), N_SAMPLE (120).
"""
import hashlib
import json
import os
import statistics
import sys
import time

from ragstack.ingestion.chunkers import SemanticChunker, sentence_spans
from ragstack.ingestion.tokenization import make_token_counter
from ragstack.models import Document

INPUT = os.environ.get("INPUT", "/rag/ingest/inputs/09320c55-a8a7-4f4d-81b3-ae55b7a329fa.jsonl")
MODEL = os.environ.get("EMBED_MODEL", "Salesforce/SFR-Embedding-Mistral")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))
BUFFER_SIZE = int(os.environ.get("BUFFER_SIZE", "3"))
N_SAMPLE = int(os.environ.get("N_SAMPLE", "120"))
DIM = 32  # mock embed dim; only feeds cosine distance — keep small & fast


def mock_embed(buffers):
    # Deterministic content-varied vectors so breakpoint distances (and thus chunk
    # counts / emit cost) are realistic, but instant (no GPU).
    out = []
    for b in buffers:
        h = hashlib.blake2b(b.encode("utf-8", "ignore"), digest_size=DIM * 2).digest()
        out.append([((h[2 * i] << 8 | h[2 * i + 1]) / 65535.0) for i in range(DIM)])
    return out


def main():
    print(f"loading hf token counter for {MODEL} ...", file=sys.stderr)
    tc = make_token_counter("hf", model=MODEL)
    chunker = SemanticChunker(
        embed_fn=mock_embed, buffer_size=BUFFER_SIZE, breakpoint_percentile_threshold=80.0,
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
            text = rec.get("text", "") or ""
            if len(text) >= 500:
                docs.append(Document(id="x", content=text, metadata={}, source=""))

    print(f"profiling {len(docs)} docs ...", file=sys.stderr)
    rows = []
    for d in docs:
        t0 = time.perf_counter()
        spans = sentence_spans(d.content)
        t1 = time.perf_counter()
        for i in range(len(spans)):  # replicate the per-buffer token-count cost
            lo = max(0, i - BUFFER_SIZE)
            hi = min(i + 1 + BUFFER_SIZE, len(spans))
            tc.count(d.content[spans[lo][0] : spans[hi - 1][1]])
        t2 = time.perf_counter()
        chunks = chunker.chunk(d)  # full CPU path incl. mock (instant) embed
        t3 = time.perf_counter()
        rows.append({
            "chars": len(d.content), "sentences": len(spans), "buffers": len(spans),
            "chunks": len(chunks), "t_sentence_ms": (t1 - t0) * 1e3,
            "t_tokcount_ms": (t2 - t1) * 1e3, "t_total_cpu_ms": (t3 - t2) * 1e3,
        })

    med = lambda k: statistics.median(r[k] for r in rows)  # noqa: E731
    mean = lambda k: sum(r[k] for r in rows) / len(rows)  # noqa: E731
    print("\n=== semantic chunker CPU profile (mock embed, no GPU) ===")
    print(f"docs={len(rows)}  model={MODEL}  buffer_size={BUFFER_SIZE}  max_tokens={MAX_TOKENS}")
    for k, label in [
        ("chars", "chars/doc"), ("sentences", "sentences/doc"),
        ("buffers", "buffers/doc (breakpoint embeds)"), ("chunks", "chunks/doc (final embeds)"),
        ("t_sentence_ms", "sentence_spans ms"), ("t_tokcount_ms", "token-count ms (per-buffer)"),
        ("t_total_cpu_ms", "TOTAL cpu chunk() ms"),
    ]:
        print(f"  {label:38s} median={med(k):9.2f}  mean={mean(k):9.2f}")
    med_cpu_s = med("t_total_cpu_ms") / 1e3
    print(f"\n  single-thread chunk throughput ~ {1/med_cpu_s:6.1f} docs/s (CPU only)")
    print(f"  breakpoint embeds/doc (median) ~ {med('buffers'):6.0f}  vs final embeds/doc ~ {med('chunks'):.0f}")


if __name__ == "__main__":
    main()
