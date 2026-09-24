"""Read every chunk of the three arms back from Elasticsearch (read-only) and describe them.

Per arm (index = the collection's physical name): chunk count, distinct documents,
per-document chunk counts, chunk-length distribution in TOKENS (HF tokenizer of the
embedding model, offline from HF_HOME) and in characters, and the metadata keys present.
Per-chunk records (filename, chunk_index, start_char, end_char, chars, tokens) go to
readback-<arm>.json so the offline spans can be checked against what the API built.

Runs inside ragstack-worker-v1.6.4.sif (for the tokenizer). Only GETs against ES.
"""
import json
import os
import sys
import urllib.request

ES = os.environ.get("ES_URL", "http://localhost:24083")
MODEL = "Salesforce/SFR-Embedding-Mistral"
ARMS = {
    "fixed_512_64": "ragstack_lib_salmonella_amr_salesforce_sfr_embedding_4096_fixed_512_64_4a77730a",
    "semantic": "ragstack_lib_salmonella_amr_semantic_salesforce_sfr_embedding_4096_semantic_9bcfa50f",
    "semantic_pooled": "ragstack_lib_salmonella_amr_pooled_salesforce_sfr_embedding_4096_semantic_pooled_300cec5e",
}
outdir = sys.argv[1]
os.makedirs(outdir, exist_ok=True)

from ragstack.ingestion.tokenization import make_token_counter  # noqa: E402

tc = make_token_counter("hf", model=MODEL)


def get(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def quantiles(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return {}
    q = lambda p: xs[min(n - 1, int(round(p * (n - 1))))]  # noqa: E731
    return {"n": n, "min": xs[0], "p10": q(.1), "p25": q(.25), "p50": q(.5), "p75": q(.75),
            "p90": q(.9), "max": xs[-1], "mean": round(sum(xs) / n, 1), "sum": sum(xs)}


summary = {}
for arm, index in ARMS.items():
    count = get(f"{ES}/{index}/_count")["count"]
    hits, search_after = [], None
    while True:
        body = {"size": 5000, "sort": [{"chunk_id": "asc"}], "_source": {"excludes": ["embedding", "vector"]}}
        if search_after:
            body["search_after"] = search_after
        page = get(f"{ES}/{index}/_search", body)["hits"]["hits"]
        if not page:
            break
        hits += page
        search_after = page[-1]["sort"]
        if len(page) < 5000:
            break
    assert len(hits) == count, (arm, len(hits), count)
    recs, meta_keys, top_keys = [], {}, {}
    for h in hits:
        s = h["_source"]
        for k in s:
            top_keys[k] = top_keys.get(k, 0) + 1
        m = s.get("metadata") or {}
        for k, v in m.items():
            if v is not None and v != "":
                meta_keys[k] = meta_keys.get(k, 0) + 1
        recs.append({"filename": m.get("filename"), "doc_id": s.get("doc_id"), "chunk_index": m.get("chunk_index"),
                     "start": s.get("start_char"), "end": s.get("end_char"),
                     "chars": len(s.get("content") or ""), "tokens": tc.count(s.get("content") or "")})
    recs.sort(key=lambda r: (r["filename"] or "", r["chunk_index"] if r["chunk_index"] is not None else -1))
    per_doc = {}
    for r in recs:
        per_doc[r["filename"]] = per_doc.get(r["filename"], 0) + 1
    summary[arm] = {
        "index": index, "es_count": count, "n_chunks": len(recs), "n_docs": len(set(r["doc_id"] for r in recs)),
        "n_filenames": len(per_doc), "per_doc_chunks": dict(sorted(per_doc.items())),
        "tokens": quantiles([r["tokens"] for r in recs]), "chars": quantiles([r["chars"] for r in recs]),
        "top_level_fields": top_keys, "metadata_fields_nonempty": dict(sorted(meta_keys.items())),
        "section_title_present": meta_keys.get("section_title", 0),
    }
    json.dump(recs, open(f"{outdir}/readback-{arm}.json", "w"))
    print(f"{arm:16s} es_count={count} chunks={len(recs)} docs={summary[arm]['n_docs']} "
          f"tokens p50={summary[arm]['tokens']['p50']} mean={summary[arm]['tokens']['mean']} "
          f"max={summary[arm]['tokens']['max']} sum={summary[arm]['tokens']['sum']}", flush=True)
json.dump(summary, open(f"{outdir}/readback-summary.json", "w"), indent=1)
print("wrote", f"{outdir}/readback-summary.json")
