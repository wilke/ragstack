#!/usr/bin/env python
"""Embed title+abstract with BAAI/bge-base-en-v1.5 (local HF cache). CLS pooling,
L2-normalised, 768-dim. Local model only -- no production sidecar is contacted."""
import json, sys, os
os.environ.setdefault("HF_HOME", "/rag/cache")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
import numpy as np, torch
from transformers import AutoTokenizer, AutoModel

MODEL = "BAAI/bge-base-en-v1.5"
src, out_npy = sys.argv[1], sys.argv[2]
dev = os.environ.get("EMB_DEVICE", "cuda:7")

rows = [json.loads(l) for l in open(src)]
texts = [((r["title"] or "") + ". " + (r["abstract"] or "")).strip() for r in rows]
tok = AutoTokenizer.from_pretrained(MODEL)
model = AutoModel.from_pretrained(MODEL, torch_dtype=torch.float16).to(dev).eval()
B = 128
out = np.zeros((len(texts), model.config.hidden_size), dtype=np.float32)
with torch.no_grad():
    for i in range(0, len(texts), B):
        enc = tok(texts[i:i + B], padding=True, truncation=True, max_length=512,
                  return_tensors="pt").to(dev)
        h = model(**enc).last_hidden_state[:, 0]
        h = torch.nn.functional.normalize(h.float(), dim=-1)
        out[i:i + B] = h.cpu().numpy()
        if (i // B) % 20 == 0:
            print(i, flush=True)
np.save(out_npy, out)
print("saved", out.shape, "dim", out.shape[1])
