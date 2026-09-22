#!/usr/bin/env python3
"""Job 3: build a resumable local bibliographic cache for the ASM DOI population.

Routes, both batched and paced to the service's own announced limit:

  crossref  GET /works?filter=doi:a,doi:b,...  (200 DOIs/request)
            pool `polite-array`, announced x-rate-limit-limit: 3 / 1s -> paced 3 req/s
  ncbi      GET /pmc/utils/idconv/v1.0/?ids=.. (200 ids/request)
            documented 3 req/s without an API key -> paced 3 req/s

Cache: one JSON record per line, keyed by DOI.
Resume: on start, every DOI already present in the output file is skipped
(including negative results, which are recorded as found:false so a resume does
not re-query them forever).

Nothing here writes to Elasticsearch, Qdrant, or any store. Output is files only.
"""
import json, os, sys, time, threading, queue, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone

CACHE = "/rag/data/asm-metadata-cache"
MAILTO = os.environ.get("ASM_CACHE_MAILTO", "awilke1972@gmail.com")
UA = f"RAGStack-ASM-metadata-cache/0.1 (https://github.com/wilke/ragstack; mailto:{MAILTO})"
BATCH = 200

CR_SELECT = ("DOI,title,subtitle,author,container-title,short-container-title,publisher,type,"
             "ISSN,volume,issue,page,published,published-print,published-online,issued,"
             "created,deposited,subject,abstract,URL,references-count,"
             "is-referenced-by-count,license,issn-type,alternative-id,group-title")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Paced:
    """Token gate: at most `rate` calls per second across all threads."""

    def __init__(self, rate):
        self.interval = 1.0 / rate
        self.lock = threading.Lock()
        self.next = 0.0

    def slow(self, factor=1.5, cap=2.0):
        with self.lock:
            self.interval = min(self.interval * factor, cap)
            return self.interval

    def wait(self):
        with self.lock:
            t = time.time()
            if t < self.next:
                time.sleep(self.next - t)
                t = self.next
            self.next = max(t, self.next) + self.interval


def get(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
        except Exception:
            body = None
        return e.code, body, {k.lower(): v for k, v in e.headers.items()}
    except Exception as e:
        return -1, {"_err": repr(e)}, {}


# ---------------------------------------------------------------- crossref

def cr_url(dois):
    return "https://api.crossref.org/works?" + urllib.parse.urlencode([
        ("rows", str(len(dois))), ("mailto", MAILTO), ("select", CR_SELECT),
        ("filter", ",".join("doi:" + d for d in dois)),
    ])


def cr_record(item):
    """Keep dates in Crossref's native date-parts form. Do NOT flatten."""
    def first(k):
        v = item.get(k)
        return v[0] if isinstance(v, list) and v else (v if not isinstance(v, list) else None)

    dates = {k: item[k] for k in ("published", "published-print", "published-online",
                                  "issued", "created", "deposited") if item.get(k)}
    # convenience year, derived — the unflattened `dates` above stays authoritative
    year = None
    for k in ("issued", "published-print", "published", "published-online"):
        dp = (item.get(k) or {}).get("date-parts") or []
        if dp and dp[0] and dp[0][0]:
            year = dp[0][0]
            break
    return {
        "doi": (item.get("DOI") or "").lower(),
        "source": "crossref",
        "retrieved": now(),
        "found": True,
        "title": first("title"),
        "subtitle": first("subtitle"),
        "authors": item.get("author"),
        "container_title": first("container-title"),
        "short_container_title": first("short-container-title"),
        "issn": item.get("ISSN"),
        "publisher": item.get("publisher"),
        "type": item.get("type"),
        "volume": item.get("volume"),
        "issue": item.get("issue"),
        "page": item.get("page"),
        "subject": item.get("subject"),
        "issn_type": item.get("issn-type"),
        "group_title": item.get("group-title"),
        "alternative_id": item.get("alternative-id"),
        "url": item.get("URL"),
        "abstract": item.get("abstract"),
        "reference_count": item.get("references-count"),
        "cited_by_count": item.get("is-referenced-by-count"),
        "license": item.get("license"),
        "dates": dates,
        "year": year,
        "pmid": None,
        "pmcid": None,
    }


# ---------------------------------------------------------------- ncbi

def ncbi_url(dois):
    return ("https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?"
            + urllib.parse.urlencode([
                ("tool", "ragstack-asm-metadata-cache"), ("email", MAILTO),
                ("format", "json"), ("versions", "no"), ("ids", ",".join(dois))]))


def ncbi_record(rec):
    return {
        "doi": (rec.get("doi") or "").lower(),
        "source": "ncbi-idconv",
        "retrieved": now(),
        "found": not rec.get("status") == "error" and bool(rec.get("pmcid") or rec.get("pmid")),
        "pmcid": rec.get("pmcid"),
        "pmid": rec.get("pmid"),
        "errmsg": rec.get("errmsg"),
    }


# ---------------------------------------------------------------- driver

def load_done(path):
    done = set()
    if os.path.exists(path):
        with open(path) as f:
            for l in f:
                try:
                    done.add(json.loads(l)["doi"])
                except Exception:
                    pass
    return done


def chunks(xs, n, max_chars=5200):
    """Batch by count AND by URL budget: a batch of long DOIs otherwise trips HTTP 414."""
    out, cur, ln = [], [], 0
    for x in xs:
        c = len(x) + 5  # "doi:" + separator
        if cur and (len(cur) >= n or ln + c > max_chars):
            out.append(cur); cur, ln = [], 0
        cur.append(x); ln += c
    if cur:
        out.append(cur)
    return out


def drive(route, dois, out_path, rate, conc, build_url, parse, log_path):
    done = load_done(out_path)
    todo = [d for d in dois if d.lower() not in done]
    print(f"[{route}] {len(dois)} DOIs, {len(done)} already cached, {len(todo)} to fetch", flush=True)
    if not todo:
        return
    batches = chunks(todo, BATCH)
    q = queue.Queue()
    for i, b in enumerate(batches):
        q.put((i, b))
    pace = Paced(rate)
    out = open(out_path, "a")
    log = open(log_path, "a")
    lock = threading.Lock()
    st = {"req": 0, "rec": 0, "miss": 0, "429": 0, "err": 0, "t0": time.time()}
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            try:
                i, b = q.get_nowait()
            except queue.Empty:
                return
            backoff = 0
            while True:
                pace.wait()
                code, body, hdr = get(build_url(b))
                if code == 429:
                    try:
                        ra = float(hdr.get("retry-after", 0) or 0)
                    except ValueError:
                        ra = 5.0
                    iv = pace.slow()
                    with lock:
                        st["429"] += 1
                        n429 = st["429"]
                        log.write(json.dumps({"t": now(), "batch": i, "http": 429,
                                              "new_interval_s": round(iv, 3)}) + "\n")
                    # back off globally; only give up on a sustained wall
                    if n429 > 500:
                        with lock:
                            st["err"] += 1
                            log.write(json.dumps({"t": now(), "fatal": "429 wall"}) + "\n")
                        stop.set()
                        return
                    time.sleep(max(ra, 5.0))
                    continue
                if code == 414 and len(b) > 1:
                    h = len(b) // 2
                    q.put((i, b[:h])); q.put((i, b[h:]))
                    with lock:
                        log.write(json.dumps({"t": now(), "batch": i, "http": 414,
                                              "action": "split", "n": len(b)}) + "\n")
                    break
                if code != 200 or body is None:
                    with lock:
                        st["err"] += 1
                        log.write(json.dumps({"t": now(), "batch": i, "http": code,
                                              "body": str(body)[:300]}) + "\n")
                    backoff += 1
                    if backoff > 3:
                        break
                    time.sleep(2 ** backoff)
                    continue
                recs, seen = parse(body)
                missing = [d for d in b if d.lower() not in seen]
                with lock:
                    for r in recs:
                        out.write(json.dumps(r, ensure_ascii=False) + "\n")
                    for d in missing:
                        out.write(json.dumps({"doi": d.lower(), "source": route,
                                              "retrieved": now(), "found": False}) + "\n")
                    out.flush()
                    st["req"] += 1
                    st["rec"] += len(recs)
                    st["miss"] += len(missing)
                    el = time.time() - st["t0"]
                    if st["req"] % 50 == 0:
                        print(f"[{route}] {st['req']}/{len(batches)} req  "
                              f"{st['rec']} found  {st['miss']} miss  {st['429']} 429  "
                              f"{st['err']} err  {(st['rec']+st['miss'])/el:.0f} doi/s  "
                              f"{el:.0f}s", flush=True)
                break

    ths = [threading.Thread(target=worker) for _ in range(conc)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    el = time.time() - st["t0"]
    summary = {"route": route, "requests": st["req"], "batches": len(batches),
               "found": st["rec"], "not_found": st["miss"], "http429": st["429"],
               "errors": st["err"], "elapsed_s": round(el, 1),
               "req_per_s": round(st["req"] / el, 2) if el else None,
               "doi_per_s": round((st["rec"] + st["miss"]) / el, 1) if el else None,
               "stopped_early": stop.is_set(), "finished": now()}
    print("SUMMARY " + json.dumps(summary), flush=True)
    log.write(json.dumps(summary) + "\n")
    out.close()
    log.close()


def cr_parse(body):
    items = body["message"]["items"]
    recs = [cr_record(i) for i in items]
    return recs, {r["doi"] for r in recs}


def ncbi_parse(body):
    recs = [ncbi_record(r) for r in body.get("records", [])]
    recs = [r for r in recs if r["doi"]]
    return recs, {r["doi"] for r in recs}


def main():
    route = sys.argv[1]
    dois = [l.strip() for l in open(f"{CACHE}/state/dois.txt") if l.strip()]
    if len(sys.argv) > 2:
        dois = dois[:int(sys.argv[2])]
    if route == "crossref":
        drive("crossref", dois, f"{CACHE}/crossref.jsonl", rate=2.5, conc=3,
              build_url=cr_url, parse=cr_parse, log_path=f"{CACHE}/state/crossref.log")
    elif route == "ncbi":
        drive("ncbi-idconv", dois, f"{CACHE}/ncbi-idconv.jsonl", rate=3.0, conc=4,
              build_url=ncbi_url, parse=ncbi_parse, log_path=f"{CACHE}/state/ncbi.log")


if __name__ == "__main__":
    main()
