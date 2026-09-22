#!/usr/bin/env python3
"""Job 4: pull `first_page` from the ASM source shards for a sampled doc set.

Offline. Reads /rag/ingest/docs/asm/*.jsonl (read-only), matches on the record's
`path` against the sampled `source_path` values, and writes the matched
{path, title, authors, first_page} out. No network, no store.
"""
import json, os, sys
from multiprocessing import Pool

SRC = "/rag/ingest/docs/asm"
CACHE = "/rag/data/asm-metadata-cache"
TARGETS = sys.argv[1]
OUT = sys.argv[2]

targets = set()
for l in open(TARGETS):
    r = json.loads(l)
    if r.get("source_path"):
        targets.add(r["source_path"])
# cheap prefilter: the final path component, used as a raw substring test
tails = {t.rsplit("/", 1)[-1] for t in targets}


def scan(fn):
    hits = []
    with open(os.path.join(SRC, fn), errors="replace") as f:
        for line in f:
            # prefilter on the basename before paying for a JSON parse
            head = line[:400]
            i = head.find('"path"')
            if i < 0:
                # path is not in the first 400 bytes; fall back to a substring test
                if not any(t in line for t in tails):
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
            else:
                j = head.find('"', head.find(":", i) + 1)
                k = head.find('"', j + 1)
                p = head[j + 1:k]
                if p not in targets:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
            if r.get("path") not in targets:
                continue
            m = r.get("metadata") or {}
            hits.append({
                "source_path": r["path"],
                "src_title": m.get("title"),
                "src_authors": m.get("authors"),
                "src_doi": m.get("doi"),
                "creationdate": m.get("creationdate"),
                "first_page": m.get("first_page"),
                "text_head": (r.get("text") or "")[:1200],
            })
    return fn, hits


if __name__ == "__main__":
    files = sorted(f for f in os.listdir(SRC) if f.endswith(".jsonl"))
    n = 0
    with open(OUT, "w") as o, Pool(10) as pool:
        for fn, hits in pool.imap_unordered(scan, files):
            for h in hits:
                o.write(json.dumps(h, ensure_ascii=False) + "\n")
            n += len(hits)
            print(f"{fn}: {len(hits)} (total {n}/{len(targets)})", flush=True)
    print(f"DONE {n}/{len(targets)} matched -> {OUT}", flush=True)
