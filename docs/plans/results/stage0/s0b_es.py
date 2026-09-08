"""Stage 0b' -- the BM25 concordance check against the **dev tenant's** Elasticsearch.

r3 SS3.4 moved this check out of Stage 2 and into Stage 0b': *"a miss is fixed in the
harness before any contrast is read, not discovered in Stage 2 after the confirmation
topics are labeled."* Pre-set threshold: **overlap@50 >= 0.90**.

**One store, one index, and it is deleted.** The URL is read from
``/rag/data/tenants/dev/config/tenant.env`` and asserted to be the dev tenant's
(``:24043``); ``:9200``, ``:6333`` and every other tenant are refused by an explicit check,
not by convention. The index is named ``chkconf_<runid>_tok512``, it is the only index this
module creates, and it is deleted at the end with a **verifying listing** printed. Nothing
else in the cluster is read, written or touched.

The mapping mimics production's: ``text`` with **no analyzer** (ES ``standard``), one
shard, no replicas, BM25 with k1 = 1.2 and b = 0.75 stated explicitly rather than left to
the default so that the comparison is against a known configuration. Queries are the plain
``match`` production issues.
"""
from __future__ import annotations

import json
import statistics as st
import sys
import time
import urllib.request

import s0b_common as K
import s0_common as C  # noqa: F401
from s0b_bm25 import ArmBM25, CorpusTokens

TENANT_ENV = "/rag/data/tenants/dev/config/tenant.env"
ARM = "fixed_tok512"
FORBIDDEN_PORTS = (9200, 6333, 24041)


def dev_es_url() -> str:
    url = None
    for line in open(TENANT_ENV):
        line = line.strip()
        if line.startswith("ELASTICSEARCH_URL="):
            url = line.split("=", 1)[1].strip()
    assert url, f"no ELASTICSEARCH_URL in {TENANT_ENV}"
    assert url.endswith(":24043"), f"refusing a non-dev-tenant Elasticsearch: {url!r}"
    for p in FORBIDDEN_PORTS:
        assert f":{p}" not in url, f"refusing port {p}: {url!r}"
    return url


def req(method: str, url: str, body=None, timeout: int = 600):
    data = None
    headers = {}
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        else:
            data = body.encode() if isinstance(body, str) else body
            headers["Content-Type"] = "application/x-ndjson"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def spearman(a: list[float], b: list[float]):
    n = len(a)
    if n < 3:
        return None

    def rank(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        rk = [0.0] * len(x)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk
    ra, rb = rank(a), rank(b)
    ma, mb = st.mean(ra), st.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def main() -> None:
    t0 = time.time()
    runid = time.strftime("%Y%m%d%H%M%S")
    idx = f"chkconf_{runid}_tok512"
    base = dev_es_url()
    print(f"dev-tenant Elasticsearch: {base}; index {idx}", flush=True)
    out: dict = {"elasticsearch": base, "index": idx, "arm": ARM,
                 "threshold_overlap_at_50": 0.90}
    try:
        info = req("GET", base)
        out["cluster"] = {"name": info.get("cluster_name"),
                          "version": info.get("version", {}).get("number"),
                          "lucene": info.get("version", {}).get("lucene_version")}
    except Exception as e:  # noqa: BLE001
        out["status"] = f"UNREACHABLE: {type(e).__name__}: {e}"
        K.atomic_json(K.OUT / "es_concordance.json", out)
        K.atomic_json(K.ART / "es_concordance.json", out)
        print(json.dumps(out, indent=1), flush=True)
        return

    docs = K.load_docs()
    rows = K.load_rows(ARM)
    qmeta = json.loads((K.OUT / "query_meta.json").read_text())["queries"]

    mapping = {
        "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0,
                               "similarity": {"default": {"type": "BM25",
                                                          "k1": K.BM25_K1,
                                                          "b": K.BM25_B}},
                               "refresh_interval": "-1"}},
        "mappings": {"properties": {"text": {"type": "text"}}},
    }
    out["mapping"] = mapping
    created = False
    try:
        req("PUT", f"{base}/{idx}", mapping)
        created = True
        print("index created", flush=True)

        buf: list[str] = []
        n = 0
        t1 = time.time()
        for i, (d, s, e, _nt) in enumerate(rows):
            buf.append(json.dumps({"index": {"_id": str(i)}}))
            buf.append(json.dumps({"text": docs[d][s:e]}))
            if len(buf) >= 20_000:
                r = req("POST", f"{base}/{idx}/_bulk", "\n".join(buf) + "\n")
                if r.get("errors"):
                    bad = [x for x in r["items"] if "error" in x.get("index", {})][:3]
                    raise RuntimeError(f"bulk errors: {bad}")
                n += len(buf) // 2
                buf = []
                if n % 100_000 == 0:
                    print(f"  indexed {n}/{len(rows)} {time.time()-t1:.0f}s", flush=True)
        if buf:
            r = req("POST", f"{base}/{idx}/_bulk", "\n".join(buf) + "\n")
            if r.get("errors"):
                raise RuntimeError("bulk errors in the final batch")
            n += len(buf) // 2
        req("POST", f"{base}/{idx}/_refresh")
        cnt = req("GET", f"{base}/{idx}/_count")["count"]
        out["indexed"] = {"sent": n, "count_in_index": cnt,
                          "seconds": round(time.time() - t1, 1)}
        assert cnt == len(rows), (cnt, len(rows))
        print(f"indexed {cnt} chunks in {out['indexed']['seconds']}s", flush=True)

        # ---- in-process side, rebuilt here so the comparison uses one code path -----
        ct = CorpusTokens(docs)
        qterms = {t for q in qmeta for t in ct.term_ids(q["text"])}
        bm = ArmBM25(rows, ct, qterms)
        print(f"in-process BM25 rebuilt ({bm.seconds}s)", flush=True)

        per = []
        for q in qmeta:
            body = {"size": K.DEPTH, "query": {"match": {"text": q["text"]}},
                    "_source": False}
            hits = req("POST", f"{base}/{idx}/_search", body)["hits"]["hits"]
            es_ids = [int(h["_id"]) for h in hits]
            es_sc = {int(h["_id"]): h["_score"] for h in hits}
            ip_idx, ip_sc = bm.topk(ct.term_ids(q["text"]), K.DEPTH)
            ip_ids = [int(x) for x in ip_idx]
            ip_map = {int(i): float(s) for i, s in zip(ip_idx, ip_sc)}
            inter = [x for x in ip_ids if x in es_sc]
            rho = spearman([ip_map[x] for x in inter], [es_sc[x] for x in inter])
            per.append({"qid": q["qid"], "population": q["population"],
                        "overlap_at_50": len(inter) / max(len(ip_ids), 1),
                        "n_es": len(es_ids), "n_inproc": len(ip_ids),
                        "spearman_on_intersection": round(rho, 4) if rho is not None
                        else None,
                        "same_rank1": bool(es_ids and ip_ids and es_ids[0] == ip_ids[0])})
        ov = [p["overlap_at_50"] for p in per]
        rh = [p["spearman_on_intersection"] for p in per
              if p["spearman_on_intersection"] is not None]
        out["per_query"] = per
        out["summary"] = {
            "queries": len(per),
            "mean_overlap_at_50": round(st.mean(ov), 4),
            "median_overlap_at_50": round(st.median(ov), 4),
            "min_overlap_at_50": round(min(ov), 4),
            "mean_spearman_on_intersection": round(st.mean(rh), 4) if rh else None,
            "same_rank1_rate": round(st.mean(1.0 if p["same_rank1"] else 0.0
                                             for p in per), 4),
            "by_population": {
                pop: {"queries": sum(1 for p in per if p["population"] == pop),
                      "mean_overlap_at_50": round(st.mean(
                          p["overlap_at_50"] for p in per if p["population"] == pop), 4)}
                for pop in ("cds", "pointed")},
            "PASS": bool(st.mean(ov) >= 0.90)}
        out["status"] = "PASS" if out["summary"]["PASS"] else "FAIL (below 0.90)"
    except Exception as e:  # noqa: BLE001
        out["status"] = f"ERROR: {type(e).__name__}: {e}"
        print(out["status"], flush=True)
    finally:
        if created:
            try:
                req("DELETE", f"{base}/{idx}")
            except Exception as e:  # noqa: BLE001
                out["delete_error"] = f"{type(e).__name__}: {e}"
        listing = urllib.request.urlopen(
            f"{base}/_cat/indices?h=index&format=json", timeout=60).read().decode()
        names = [x["index"] for x in json.loads(listing)]
        out["verifying_listing"] = {
            "index_gone": idx not in names,
            "chkconf_indices_remaining": sorted(x for x in names
                                                if x.startswith("chkconf_")),
            "n_indices_in_cluster": len(names)}
        out["seconds"] = round(time.time() - t0, 1)
        out["provenance"] = K.provenance()
        K.atomic_json(K.OUT / "es_concordance.json", out)
        K.atomic_json(K.ART / "es_concordance.json", out)
        print(json.dumps({k: out[k] for k in
                          ("status", "summary", "verifying_listing", "seconds")
                          if k in out}, indent=1), flush=True)


if __name__ == "__main__":
    sys.exit(main())
