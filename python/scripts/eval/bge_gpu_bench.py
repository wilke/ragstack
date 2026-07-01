"""Benchmark BGE-base on a GPU (transformers + torch, no sidecar HTTP).

Loads BAAI/bge-base-en-v1.5 on cuda and times encoding real sentence-buffers at a
few batch sizes -> peak embeds/s. Comparison point for the CPU sidecar (23/s) and
the SFR vLLM fleet (78/s per endpoint, 281/s pooled x8, both under live load).
"""
import json
import time

import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer

INPUT = "/rag/ingest/inputs/09320c55-a8a7-4f4d-81b3-ae55b7a329fa.jsonl"
MODEL = "BAAI/bge-base-en-v1.5"
N = 512
MAXLEN = 512


def buffers(n):
    import re
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
            # crude sentence chunks of ~7 sentences to mimic breakpoint buffers
            sents = re.split(r"(?<=[.!?])\s+", text)
            for i in range(0, len(sents), 7):
                if len(bufs) >= n:
                    break
                bufs.append(" ".join(sents[i : i + 7]))
    return bufs


def main():
    dev = "cuda"
    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModel.from_pretrained(MODEL).to(dev).eval().half()
    bufs = buffers(N)
    print(f"device={torch.cuda.get_device_name(0)} buffers={len(bufs)} "
          f"avg_chars={sum(len(b) for b in bufs)/len(bufs):.0f}")

    @torch.no_grad()
    def encode(batch, bs):
        # warmup
        enc = tok(batch[:bs], padding=True, truncation=True, max_length=MAXLEN, return_tensors="pt").to(dev)
        _ = model(**enc)
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        for i in range(0, len(batch), bs):
            enc = tok(batch[i : i + bs], padding=True, truncation=True,
                      max_length=MAXLEN, return_tensors="pt").to(dev)
            out = model(**enc)
            emb = out.last_hidden_state[:, 0]  # BGE uses CLS
            F.normalize(emb, p=2, dim=1)
        torch.cuda.synchronize()
        return len(batch) / (time.perf_counter() - t0)

    for bs in (32, 64, 128, 256):
        rate = encode(bufs, bs)
        print(f"  bge-base GPU bs={bs:4d} -> {rate:8.1f} embeds/s")


if __name__ == "__main__":
    main()
