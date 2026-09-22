#!/usr/bin/env python3
"""Job 2: extract distinct (doc_id, doi) from the PRODUCTION ES ASM indices. READ-ONLY.

search_after paging over metadata.chunk_index==0 (one chunk per document),
sorted by doc_id, bounded page size, small sleep between pages.
"""
import json, sys, time, urllib.request, os

ES = "http://localhost:9200"
PAGE = 2000
SLEEP = 0.05
OUT_DIR = sys.argv[1]
INDEX = sys.argv[2]

FIELDS = ["doc_id", "metadata.doi", "metadata.title", "metadata.year", "metadata.doc_type",
          "metadata.filename", "metadata.source_path", "metadata.doi_source"]


def post(path, body, timeout=120):
    req = urllib.request.Request(
        ES + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def main():
    out_path = os.path.join(OUT_DIR, f"docs-{INDEX}.jsonl")
    ck_path = out_path + ".after"
    after = None
    mode = "w"
    if os.path.exists(ck_path):
        after = json.load(open(ck_path))
        mode = "a"
        print(f"resuming after {after}", flush=True)
    out = open(out_path, mode)
    n = 0
    t0 = time.time()
    if mode == "a":
        n = sum(1 for _ in open(out_path)) - 0
    while True:
        body = {
            "size": PAGE,
            "query": {"term": {"metadata.chunk_index": 0}},
            "_source": FIELDS,
            "sort": [{"doc_id": "asc"}],
        }
        if after:
            body["search_after"] = after
        r = post(f"/{INDEX}/_search", body)
        hits = r["hits"]["hits"]
        if not hits:
            break
        for h in hits:
            m = h.get("_source", {}).get("metadata", {})
            out.write(json.dumps({
                "doc_id": h["_source"].get("doc_id"),

                "doi": m.get("doi"),
                "has_title": bool(m.get("title")),
                "title": m.get("title"),
                "year": m.get("year"),
                "doc_type": m.get("doc_type"),
                "filename": m.get("filename"),
                "source_path": m.get("source_path"),
                "doi_source": m.get("doi_source"),
            }, ensure_ascii=False) + "\n")
        n += len(hits)
        after = hits[-1]["sort"]
        out.flush()
        json.dump(after, open(ck_path, "w"))
        if n % 50000 < PAGE:
            print(f"{n} docs, {time.time()-t0:.0f}s", flush=True)
        time.sleep(SLEEP)
    out.close()
    print(f"DONE {INDEX}: {n} rows in {time.time()-t0:.0f}s -> {out_path}", flush=True)


main()
