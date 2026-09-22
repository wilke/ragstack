#!/usr/bin/env python3
"""Job 1: measured throughput of candidate metadata routes on real ASM DOIs."""
import json, sys, time, threading, queue, urllib.parse, urllib.request, urllib.error

MAILTO = "awilke1972@gmail.com"
UA = f"RAGStack-metadata-audit/0.1 (https://github.com/wilke/ragstack; mailto:{MAILTO})"
S = "/tmp/claude-3581/-home-wilke-Development-ragstack/5f5c3e4a-7165-4b98-84ba-6da7bc9b431c/scratchpad"

CR_SELECT = ("DOI,title,author,container-title,short-container-title,published,"
             "published-print,published-online,issued,publisher,type,ISSN,volume,issue,page,subject")


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r), dict(r.headers), time.time() - t
    except urllib.error.HTTPError as e:
        return e.code, None, dict(e.headers), time.time() - t
    except Exception as e:
        return -1, {"err": str(e)}, {}, time.time() - t


def cr_batch_url(dois, rows=None, select=CR_SELECT):
    q = [("rows", str(rows or len(dois))), ("mailto", MAILTO), ("select", select),
         ("filter", ",".join("doi:" + d for d in dois))]
    return "https://api.crossref.org/works?" + urllib.parse.urlencode(q)


def cr_single_url(doi):
    return ("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
            + "?mailto=" + MAILTO)


def oa_batch_url(dois):
    q = [("filter", "doi:" + "|".join("https://doi.org/" + d for d in dois)),
         ("per-page", str(len(dois))), ("mailto", MAILTO)]
    return "https://api.openalex.org/works?" + urllib.parse.urlencode(q)


def run(name, urls, conc, extract, pace=None):
    """Run `urls` with `conc` workers; return measured stats."""
    q = queue.Queue()
    for i, u in enumerate(urls):
        q.put((i, u))
    res = {"ok": 0, "http429": 0, "err": 0, "records": 0, "lat": [], "hdr": {}}
    lock = threading.Lock()
    gate = threading.Lock()
    last = [0.0]

    def worker():
        while True:
            try:
                i, u = q.get_nowait()
            except queue.Empty:
                return
            if pace:
                with gate:
                    d = last[0] + pace - time.time()
                    if d > 0:
                        time.sleep(d)
                    last[0] = time.time()
            code, body, hdr, lat = get(u)
            with lock:
                res["lat"].append(lat)
                if code == 429:
                    res["http429"] += 1
                elif code == 200 and body is not None:
                    res["ok"] += 1
                    res["records"] += extract(body)
                    for k in ("x-rate-limit-limit", "x-ratelimit-remaining", "x-ratelimit-credits-used"):
                        if k in {h.lower() for h in hdr}:
                            pass
                    res["hdr"] = {k.lower(): v for k, v in hdr.items()
                                  if k.lower().startswith(("x-rate", "x-api"))}
                else:
                    res["err"] += 1

    t0 = time.time()
    ths = [threading.Thread(target=worker) for _ in range(conc)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    el = time.time() - t0
    lat = sorted(res["lat"])
    print(json.dumps({
        "route": name, "concurrency": conc, "requests": len(urls),
        "elapsed_s": round(el, 2),
        "req_per_s": round(len(urls) / el, 2),
        "records_returned": res["records"],
        "docs_per_s": round(res["records"] / el, 1),
        "ok": res["ok"], "http429": res["http429"], "err": res["err"],
        "lat_p50": round(lat[len(lat) // 2], 3) if lat else None,
        "lat_p95": round(lat[int(len(lat) * .95)], 3) if lat else None,
        "hdrs": res["hdr"],
    }), flush=True)
    return res


def chunks(xs, n):
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def main():
    dois = [l.strip() for l in open(f"{S}/sample400.txt") if l.strip()]
    which = sys.argv[1]

    if which == "cr-single-seq":
        run("crossref/single/seq-1rps", [cr_single_url(d) for d in dois[:60]], 1,
            lambda b: 1 if b.get("message") else 0, pace=1.0)
    elif which == "cr-single-c4":
        run("crossref/single/polite-c4", [cr_single_url(d) for d in dois[:200]], 4,
            lambda b: 1 if b.get("message") else 0)
    elif which == "cr-single-c8":
        run("crossref/single/polite-c8", [cr_single_url(d) for d in dois[:200]], 8,
            lambda b: 1 if b.get("message") else 0)
    elif which == "cr-batch":
        for n, conc in ((50, 1), (200, 1), (200, 2), (200, 4)):
            urls = [cr_batch_url(c) for c in chunks(dois, n)]
            run(f"crossref/batch-{n}", urls, conc, lambda b: len(b["message"]["items"]))
            time.sleep(2)
    elif which == "oa-batch":
        for n, conc in ((50, 1), (50, 2), (50, 4)):
            urls = [oa_batch_url(c) for c in chunks(dois, n)]
            run(f"openalex/batch-{n}", urls, conc, lambda b: len(b["results"]))
            time.sleep(2)
    elif which == "ncbi":
        # PMC ID Converter, 200 ids/request, 3 req/s ceiling without a key
        urls = ["https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?tool=ragstack-audit"
                f"&email={MAILTO}&format=json&ids=" + urllib.parse.quote(",".join(c))
                for c in chunks(dois, 200)]
        run("ncbi/idconv-200", urls, 1, lambda b: len(b.get("records", [])), pace=0.34)


if __name__ == "__main__":
    main()
