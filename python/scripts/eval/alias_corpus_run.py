"""Alias study, build step 1: select, fetch, convert, screen and chunk the added papers (OFFLINE).

PREREG-alias-study.md §5.1 / §5.5 (draft v1). The rules live in :mod:`alias_corpus`; this is the
I/O driver. **It writes to no store and calls no model**: OpenAlex metadata, the PMC open-access
bucket and open-access PDF hosts are read; files land under ``--root`` only.

Stages (``run`` does them in order; each is resumable)::

    study       derive the 300 satisfied study papers from the accepted-record file and check the
                balanced-set identity sha (§4.1)
    neighbours  OpenAlex API: OA-1000 and study records, references, citers; pick and attribute
                the neighbours (D4, O5); the citation-link set for the random arms
    scan        OpenAlex parquet snapshot (anonymous S3 range reads, column projection): every work
                in a study paper's primary topic within ± 3 years with open-access full text —
                the random candidate pools (D5). The API is not used for this: enumerating ~5 M
                works is ~25,000 list calls, and the keyless API allows 1,000 a day
    neighbours-fetch   fetch, convert, chunk and screen every attributed neighbour
    random      the two seeded draws (D5, D6, O6), fetching on demand
    finalize    per-arm chunk files, private manifests, public counts, disk use
    chunkcheck  re-chunk 3 OA-1000 papers through the same path and diff against the export

Safety: downloaded files are untrusted data. They are stored under ``<root>/raw`` (0700 dirs, 0600
files), parsed with stdlib ElementTree (JATS) or PyMuPDF in a **separate, resource-limited**
``python -I`` process (PDF), never executed. This process imports ragstack from the checkout that
holds this file (``sys.path`` is set explicitly so ``python -I`` works), never from an editable
install elsewhere.
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures as cf
import datetime as dt
import hashlib
import io
import json
import os
import resource
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_PY = _HERE.parent.parent  # .../python
for _p in (str(_PY), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import alias_corpus as ac  # noqa: E402

DEFAULT_ROOT = "/rag/tmp/alias-study-2026-11/corpus"
OA_EXPORT = "/rag/tmp/glossbench-round2-2026-11/exports/chunks-oa.jsonl"
OA_EXPORT_SHA = "ff16e611a28c48022a41d9de162b7aeabaa0e0c9b95d7ad2516fe515dc37cf8d"
OA_MANIFEST = "/rag/tmp/bench-samples-261028/oa-1000.manifest.jsonl"
OA_CORPUS = "/rag/oa/corpus"
ACCEPTED = "/rag/tmp/alias-study-2026-11/out/main/accepted-alias-O.jsonl"
ACCEPTED_SHA = "09fc5ca434f50c5d0e527f2424dd31a2d59e019f59762c7f961dd7d7804ad24f"
BALANCED_SHA = "2be86f8c5fd7dafaeb5a183c4dd55cc3e7b31366a48267424916dcbcf2557a0f"

OPENALEX = "https://api.openalex.org/works"
OA_FIELDS = ("id,ids,doi,cited_by_count,publication_date,publication_year,open_access,"
             "best_oa_location,locations,primary_topic,referenced_works,type")
SNAPSHOT_MANIFEST = "https://openalex.s3.amazonaws.com/data/parquet/manifest.json"
PMC_S3 = "https://pmc-oa-opendata.s3.amazonaws.com/"
UA_API = "ragstack-alias-corpus/1"
UA_FETCH = "Mozilla/5.0 (compatible; ragstack-alias-corpus/1; offline research corpus build)"
TOKENIZER = "Salesforce/SFR-Embedding-Mistral"
PASSTHROUGH = "content_type,pmcid,pmid,journal,publisher,licence,section_title,sha256,source_url,graphic"
MAX_PDF_BYTES = 100 * 1024 * 1024
PDF_URL_TRIES = 3

_LOG_LOCK = threading.Lock()


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    with _LOG_LOCK:
        print(f"{now()} {msg}", flush=True)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: str | Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


class Layout:
    """``<root>/{raw/{jats,pdf},text,chunks,logs,meta,manifests,state}`` — 0700 dirs, 0600 files."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)
        for sub in ("raw/jats", "raw/pdf", "text", "chunks", "logs", "meta", "meta/pool",
                    "manifests", "state", "tmp"):
            d = self.root / sub
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o700)
        os.chmod(self.root, 0o700)

    def __getattr__(self, name: str) -> Path:
        return self.root / name.replace("_", "/", 1) if name.startswith("raw_") else self.root / name


def write_json(path: str | Path, obj: Any) -> None:
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True, default=str)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def write_bytes(path: str | Path, data: bytes) -> None:
    p = Path(path)
    tmp = p.with_name("." + p.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


# ====================================================================== OpenAlex API


class OpenAlex:
    """Keyless, email-less OpenAlex API client: ≤ 4 req/s, a URL cache, a query log, the credit floor.

    The cache is keyed by the exact URL; the feasibility count of 2026-11-01 built its URLs the same
    way (``urlencode(params, safe=":|,")``), so its responses can seed the cache (``--seed-cache``):
    every seeded entry is logged with ``"seeded": true`` and that run's timestamp.
    """

    def __init__(self, lay: Layout, min_interval: float = 0.25, floor: int = 25) -> None:
        self.cache_path = lay.meta / "openalex-cache.json"
        self.log_path = lay.logs / "openalex-query-log.jsonl"
        try:
            self.cache: dict[str, Any] = json.load(open(self.cache_path))
        except Exception:
            self.cache = {}
        self.min_interval, self.floor = min_interval, floor
        self._last = 0.0
        self.calls = 0
        self.hits = 0
        self.dirty = 0

    def seed(self, cache_file: str, query_log: str) -> int:
        src = json.load(open(cache_file))
        n = 0
        times = {}
        for line in open(query_log):
            try:
                r = json.loads(line)
                if r.get("status") == 200:
                    times[r["url"]] = r["t"]
            except Exception:
                pass
        with open(self.log_path, "a") as lf:
            for url, body in src.items():
                if url not in self.cache:
                    self.cache[url] = body
                    n += 1
                    lf.write(json.dumps({"t": times.get(url), "url": url, "status": 200, "seeded": True,
                                         "seed_source": "feasibility count 2026-11-01 (same URL)",
                                         "count": (body or {}).get("meta", {}).get("count")}) + "\n")
        self.save()
        return n

    def save(self) -> None:
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(self.cache, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.cache_path)
        self.dirty = 0

    def get(self, params: dict[str, Any]) -> dict:
        q = urllib.parse.urlencode(params, safe=":|,")
        url = f"{OPENALEX}?{q}"
        if url in self.cache:
            self.hits += 1
            return self.cache[url]
        delay = 2.0
        for _attempt in range(8):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            t0 = time.time()
            rem = reset = None
            body = None
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA_API})
                with urllib.request.urlopen(req, timeout=90) as r:
                    body = json.load(r)
                    status: Any = r.status
                    rem, reset = r.headers.get("x-ratelimit-remaining"), r.headers.get("x-ratelimit-reset")
            except urllib.error.HTTPError as e:
                status = e.code
                rem, reset = e.headers.get("x-ratelimit-remaining"), e.headers.get("x-ratelimit-reset")
            except Exception as e:  # noqa: BLE001
                status = f"err:{type(e).__name__}"
            self.calls += 1
            with open(self.log_path, "a") as lf:
                lf.write(json.dumps({"t": now(), "url": url, "status": status, "ms": int((time.time() - t0) * 1000),
                                     "count": (body or {}).get("meta", {}).get("count"),
                                     "credits_remaining": rem}) + "\n")
            if body is not None:
                self.cache[url] = body
                self.dirty += 1
                if self.dirty >= 20:
                    self.save()
                if rem is not None and int(rem) < self.floor:
                    self._sleep_reset(reset)
                return body
            if status == 429 or (rem is not None and int(rem) < self.floor):
                self._sleep_reset(reset)
                continue
            if isinstance(status, int) and 400 <= status < 500:
                raise SystemExit(f"OpenAlex: HTTP {status} (not retried): {url[:200]}")
            time.sleep(delay)
            delay = min(delay * 2, 120)
        raise SystemExit(f"OpenAlex: failed after retries: {url[:200]}")

    def _sleep_reset(self, reset: str | None) -> None:
        self.save()
        secs = int(reset) + 60 if reset and str(reset).isdigit() else 3600
        log(f"openalex: credit floor reached; sleeping {secs}s until the daily reset")
        time.sleep(secs)

    def batch(self, filter_key: str, values: list[str], extra: str = "") -> list[dict]:
        """count.py's batching, verbatim (100 values per call, per-page 200)."""
        out: list[dict] = []
        values = list(values)
        for i in range(0, len(values), 100):
            chunk = values[i:i + 100]
            f = f"{filter_key}:" + "|".join(chunk) + extra
            out += self.get({"filter": f, "per-page": 200, "select": OA_FIELDS})["results"]
        return out

    def all_citers(self, wid: str) -> list[str]:
        """Every work citing ``wid`` (page paging, ids only) — for the citation-link set. Cursor
        paging past the first page answers 401 without a key; page paging reaches 10,000 results,
        far above the largest study paper's citation count."""
        out: list[str] = []
        page = 1
        while page:
            b = self.get({"filter": f"cites:{wid}", "per-page": 200, "page": page, "select": "id"})
            out += [ac.short_id(r["id"]) for r in b["results"]]
            n = b.get("meta", {}).get("count") or 0
            if n > 10000:
                raise SystemExit(f"{wid}: {n} citers exceed page paging")
            page = page + 1 if (b["results"] and page * 200 < n) else None
        return out


# ====================================================================== stage: study


def stage_study(lay: Layout) -> dict:
    """The 300 satisfied papers (≥ 2 accepted G on distinct alias keys and ≥ 1 accepted C), with the
    balanced-set identity checked against the prereg's sha (§4.1)."""
    from glossbench_alias_checks import alias_key  # committed function (§4.1)

    got = sha256_file(ACCEPTED)
    if got != ACCEPTED_SHA:
        raise SystemExit(f"accepted-record file sha {got} != {ACCEPTED_SHA}")
    by_paper: dict[str, dict] = defaultdict(lambda: {"g": [], "c": []})
    for line in open(ACCEPTED):
        r = json.loads(line)
        if r["kind"] == "G":
            by_paper[r["paper"]]["g"].append((r["record_id"], alias_key(r["alias"], r.get("alias_kind"))))
        else:
            by_paper[r["paper"]]["c"].append(r["record_id"])
    study, ids = [], []
    for p, v in by_paper.items():
        if len({k for _, k in v["g"]}) >= 2 and v["c"]:
            study.append(p)
            ids += [i for i, _ in v["g"]] + v["c"]
    bal = sha256_bytes("".join(f"{i}\n" for i in sorted(ids)).encode())
    res = {"n_study": len(study), "n_records": len(ids), "balanced_sha256": bal,
           "balanced_sha_matches_prereg": bal == BALANCED_SHA, "accepted_sha256": got}
    if bal != BALANCED_SHA or len(study) != 300:
        raise SystemExit(f"study set mismatch: {res}")
    write_json(lay.state / "study.json", {"study": sorted(study), **res})
    log(f"study: {res}")
    return res


# ====================================================================== stage: neighbours


def load_oa1000() -> tuple[list[str], dict[str, str], dict[str, str]]:
    """(sorted OA-1000 PMCIDs, pmcid -> title from the export, pmcid -> DOI from the sample manifest)."""
    titles: dict[str, str] = {}
    for line in open(OA_EXPORT):
        d = json.loads(line)
        sp = d["metadata"]["source_path"]
        if "#" not in sp:
            titles.setdefault(sp, d["metadata"].get("title") or "")
    for line in open(OA_EXPORT):
        d = json.loads(line)
        titles.setdefault(d["metadata"]["source_path"].split("#")[0], d["metadata"].get("title") or "")
    dois = {}
    for line in open(OA_MANIFEST):
        r = json.loads(line)
        if r.get("doi_xml"):
            dois[r["pmcid"]] = ac.norm_doi(r["doi_xml"])
    pm = sorted(titles)
    assert len(pm) == 1000, len(pm)
    return pm, titles, dois


def stage_neighbours(lay: Layout, api: OpenAlex) -> dict:
    study = json.load(open(lay.state / "study.json"))["study"]
    oa_pmc, oa_titles, oa_dois = load_oa1000()
    recs = api.batch("ids.pmcid", [p.replace("PMC", "") for p in oa_pmc])
    by_pmc = {ac.norm_pmcid((w.get("ids") or {}).get("pmcid")): w for w in recs}
    missing = [p for p in oa_pmc if p not in by_pmc]
    # second chance by DOI for the OA-1000 papers the PMCID lookup did not resolve
    miss_doi = [oa_dois[p] for p in missing if oa_dois.get(p)]
    doi_hits = api.batch("doi", miss_doi) if miss_doi else []
    for w in doi_hits:
        d = ac.norm_doi(w.get("doi"))
        for p in missing:
            if oa_dois.get(p) == d:
                by_pmc[p] = w
    still_missing = [p for p in oa_pmc if p not in by_pmc]
    excl = ac.Exclusion()
    for p in oa_pmc:
        excl.pmcids.add(p)
        if oa_dois.get(p):
            excl.dois.add(oa_dois[p])
        t = ac.norm_title(oa_titles.get(p))
        if t:
            excl.titles.add(t)
        if p in by_pmc:
            excl.add(ac.work_from_api(by_pmc[p]))
    study_w = {p: by_pmc[p] for p in study if p in by_pmc}
    study_missing = [p for p in study if p not in by_pmc]
    # references (count.py's query, verbatim)
    all_refs = sorted({ac.short_id(r) for w in study_w.values() for r in (w.get("referenced_works") or [])})
    ref_meta = {ac.short_id(w["id"]): w for w in api.batch("openalex", all_refs)}
    # citers: light papers batched (count.py's grouping, verbatim); heavy ones first 200 by date
    sid = {ac.short_id(w["id"]): p for p, w in study_w.items()}
    light = [p for p, w in study_w.items() if (w.get("cited_by_count") or 0) <= 150]
    heavy = [p for p in study_w if p not in light]
    groups, cur, tot = [], [], 0
    for p in sorted(light, key=lambda p: study_w[p].get("cited_by_count") or 0):
        n = study_w[p].get("cited_by_count") or 0
        if cur and (tot + n > 3000 or len(cur) >= 100):
            groups.append(cur)
            cur, tot = [], 0
        cur.append(p)
        tot += n
    if cur:
        groups.append(cur)
    acc: dict[str, dict[str, dict]] = {p: {} for p in study_w}
    for g in groups:
        page = 1
        while page:
            body = api.get({"filter": "cites:" + "|".join(ac.short_id(study_w[p]["id"]) for p in g),
                            "per-page": 200, "page": page, "select": OA_FIELDS})
            for c in body["results"]:
                for r in c.get("referenced_works") or []:
                    if ac.short_id(r) in sid and sid[ac.short_id(r)] in g:
                        acc[sid[ac.short_id(r)]][ac.short_id(c["id"])] = c
            page = page + 1 if (body["results"] and page * 200 < body["meta"]["count"]) else None
    citers: dict[str, tuple[list[dict], int, bool]] = {}
    for p in light:
        citers[p] = (list(acc[p].values()), study_w[p].get("cited_by_count") or 0, True)
    for p in heavy:
        sp = ac.short_id(study_w[p]["id"])
        b = api.get({"filter": f"cites:{sp}", "sort": "publication_date:desc", "per-page": 200,
                     "select": OA_FIELDS})
        citers[p] = (b["results"], b["meta"]["count"], b["meta"]["count"] <= 200)
    api.save()
    # picks
    works: dict[str, ac.Work] = {}
    picks: dict[str, list[tuple[str, str]]] = {}
    per_study: dict[str, dict] = {}
    for p, w in study_w.items():
        refs = [ac.work_from_api(ref_meta[ac.short_id(r)]) for r in (w.get("referenced_works") or [])
                if ac.short_id(r) in ref_meta]
        rsel, rstat = ac.pick_references(p, refs, excl)
        cl, ctotal, complete = citers[p]
        cws = [ac.work_from_api(c) for c in cl]
        csel, cstat = ac.pick_citers(p, cws, excl)
        if not complete and len(csel) < ac.CITER_CAP:
            raise SystemExit(f"{p}: fewer than {ac.CITER_CAP} eligible citers in the first page; page further")
        for x in rsel + csel:
            works[x.wid] = x
        picks[p] = [(x.wid, "ref") for x in rsel] + [(x.wid, "citer") for x in csel]
        sw = ac.work_from_api(w)
        per_study[p] = {"wid": sw.wid, "topic": sw.topic, "year": sw.year, "refs_listed": len(w.get("referenced_works") or []),
                        "refs": rstat, "citers": {**cstat, "cited_by_count": ctotal, "list_complete": complete},
                        "picked_refs": len(rsel), "picked_citers": len(csel)}
    attributed, dropped = ac.attribute(picks)
    # the citation-link set: everything a study paper cites, and every work citing one (complete lists)
    linked: set[str] = set()
    for w in study_w.values():
        linked.update(ac.short_id(r) for r in (w.get("referenced_works") or []))
    for p in light:
        linked.update(acc[p].keys())
    for p in heavy:
        linked.update(api.all_citers(ac.short_id(study_w[p]["id"])))
    api.save()
    order = list(ac.attribution_order(study_w))
    sel = {
        "schema": "alias-corpus-neighbour-selection/1",
        "seed": ac.SEED_NEIGHBOURS, "oa_definition": ac.OA_DEFINITION,
        "rule": "<= 5 most-cited references + <= 5 most-recent citers with open-access full text, "
                "not in OA-1000 (wid, PMCID, DOI or normalised title >= 30 chars) and not a study paper; "
                "shared neighbours attributed to the first study paper in the seeded order (O5)",
        "queried": now(), "study_resolved": len(study_w), "study_unresolved": study_missing,
        "oa1000_resolved": len(oa_pmc) - len(still_missing), "oa1000_unresolved": len(still_missing),
        "order": order, "attributed": attributed, "picks_before_dedupe": sum(len(v) for v in picks.values()),
        "duplicates_dropped": dropped, "per_study": per_study,
        "works": {k: _work_json(v) for k, v in works.items()},
        "study_works": {p: _work_json(ac.work_from_api(w)) for p, w in study_w.items()},
    }
    write_json(lay.manifests / "neighbour-selection.json", sel)
    write_json(lay.state / "exclusion.json", {"wids": sorted(excl.wids), "pmcids": sorted(excl.pmcids),
                                               "dois": sorted(excl.dois), "titles": sorted(excl.titles)})
    write_json(lay.state / "linked.json", sorted(x for x in linked if x))
    n_attr = sum(len(v) for v in attributed.values())
    log(f"neighbours: study resolved {len(study_w)}/300, picks {sel['picks_before_dedupe']}, "
        f"unique attributed {n_attr}, linked set {len(linked)}; api calls {api.calls}, cache hits {api.hits}")
    return {"attributed": n_attr}


def _work_json(w: ac.Work) -> dict:
    return {"wid": w.wid, "pmcid": w.pmcid, "doi": w.doi, "title": w.title, "year": w.year,
            "pub_date": w.pub_date, "cited_by_count": w.cited_by_count, "topic": w.topic, "type": w.type,
            "is_oa": w.is_oa, "pdf_locations": [[loc.url, loc.license, loc.source] for loc in w.pdf_locations]}


def _work_from_json(d: dict) -> ac.Work:
    return ac.Work(wid=d["wid"], pmcid=d.get("pmcid"), doi=d.get("doi"), title=d.get("title"), year=d.get("year"),
                   pub_date=d.get("pub_date"), cited_by_count=d.get("cited_by_count") or 0, topic=d.get("topic"),
                   type=d.get("type"), is_oa=bool(d.get("is_oa")),
                   pdf_locations=tuple(ac.PdfLocation(u, lic, s) for u, lic, s in d.get("pdf_locations") or []))


def load_exclusion(lay: Layout) -> ac.Exclusion:
    d = json.load(open(lay.state / "exclusion.json"))
    return ac.Exclusion(wids=set(d["wids"]), pmcids=set(d["pmcids"]), dois=set(d["dois"]), titles=set(d["titles"]))


# ====================================================================== stage: scan (snapshot)


SCAN_COLS = ["id", "doi", "title", "type", "publication_year", "primary_topic.id", "open_access.is_oa", "ids",
             "locations.list.element.is_oa", "locations.list.element.pdf_url", "locations.list.element.license",
             "best_oa_location.pdf_url", "best_oa_location.license"]


def _scan_file(args: tuple[str, str, dict[str, list[int]]]) -> tuple[str, int, int]:
    """One snapshot parquet file -> the rows in a wanted (topic, year) window with OA full text."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.fs as pafs
    import pyarrow.parquet as pq

    url, out, windows = args
    if os.path.exists(out):
        return url, -1, -1
    fs = pafs.S3FileSystem(anonymous=True, region="us-east-1", connect_timeout=30, request_timeout=120)
    path = url.replace("s3://", "")
    keys = [f"https://openalex.org/{t}" for t in windows]
    pairs = {(f"https://openalex.org/{t}", y) for t, ys in windows.items() for y in ys}
    for attempt in range(6):
        try:
            f = pq.ParquetFile(path, filesystem=fs)
            tables, n_in = [], 0
            for rg in range(f.metadata.num_row_groups):
                tb = f.read_row_group(rg, columns=SCAN_COLS)
                n_in += tb.num_rows
                topic = pc.struct_field(tb.column("primary_topic"), "id")
                m = pc.is_in(topic, value_set=pa.array(keys))
                m = pc.fill_null(m, False)
                if not pc.any(m).as_py():
                    continue
                sub = tb.filter(m)
                rows = sub.to_pylist()
                keep = []
                for r in rows:
                    t = (r.get("primary_topic") or {}).get("id")
                    if (t, r.get("publication_year")) not in pairs:
                        continue
                    w = ac.work_from_snapshot(r)
                    if w.oa_fulltext:
                        keep.append(r)
                if keep:
                    tables.append(keep)
            flat = [r for t in tables for r in t]
            tmp = out + ".tmp"
            with open(tmp, "w") as fh:
                for r in flat:
                    fh.write(json.dumps(r, default=str) + "\n")
            os.replace(tmp, out)
            return url, n_in, len(flat)
        except Exception as e:  # noqa: BLE001
            if attempt == 5:
                raise RuntimeError(f"{url}: {type(e).__name__}: {e}") from e
            time.sleep(5 * (attempt + 1))
    return url, 0, 0


def stage_scan(lay: Layout, workers: int) -> dict:
    sel = json.load(open(lay.manifests / "neighbour-selection.json"))
    windows: dict[str, set[int]] = defaultdict(set)
    for _p, m in sel["per_study"].items():
        if m.get("topic") and m.get("year"):
            for y in range(m["year"] - ac.YEAR_WINDOW, m["year"] + ac.YEAR_WINDOW + 1):
                windows[m["topic"]].add(y)
    win = {t: sorted(ys) for t, ys in windows.items()}
    with urllib.request.urlopen(urllib.request.Request(SNAPSHOT_MANIFEST, headers={"User-Agent": UA_API}), timeout=120) as r:
        man = json.load(r)
    write_bytes(lay.meta / "snapshot-manifest.json", json.dumps(man).encode())
    works = next(e for e in man["entities"] if e["entity"] == "works")
    files = [f["url"] for f in works["files"]]
    pool_dir = lay.meta / "pool"
    jobs = [(u, str(pool_dir / (hashlib.sha1(u.encode()).hexdigest()[:16] + ".jsonl")), win) for u in files]
    log(f"scan: snapshot {man['date']}, {len(files)} files, {works['content_length'] / 1e9:.0f} GB; "
        f"{len(win)} topics; {workers} workers")
    done = n_in = n_keep = 0
    t0 = time.time()
    with cf.ProcessPoolExecutor(max_workers=workers) as ex:
        for _url, a, b in ex.map(_scan_file, jobs, chunksize=1):
            done += 1
            if a >= 0:
                n_in += a
                n_keep += b
            if done % 50 == 0:
                log(f"scan: {done}/{len(files)} files, rows read {n_in:,}, kept {n_keep:,}, {time.time() - t0:.0f}s")
    res = {"snapshot_date": man["date"], "files": len(files), "topics": len(win), "rows_read_this_run": n_in,
           "rows_kept_this_run": n_keep, "columns": SCAN_COLS, "windows": win,
           "manifest_sha256": sha256_file(lay.meta / "snapshot-manifest.json"), "finished": now()}
    write_json(lay.state / "scan.json", res)
    log(f"scan: done {res['files']} files in {time.time() - t0:.0f}s")
    return res


def load_pool(lay: Layout) -> dict[str, list[ac.Work]]:
    """topic -> candidate works (deduplicated by work id)."""
    by_topic: dict[str, dict[str, ac.Work]] = defaultdict(dict)
    for f in sorted((lay.meta / "pool").glob("*.jsonl")):
        for line in open(f):
            w = ac.work_from_snapshot(json.loads(line))
            if w.topic and w.wid:
                by_topic[w.topic][w.wid] = w
    return {t: list(v.values()) for t, v in by_topic.items()}


# ====================================================================== fetching


class HostGate:
    """≤ 2 concurrent requests per host; robots.txt honoured; a host that refuses (403) or rate-limits
    (429) is skipped for the rest of the run."""

    def __init__(self, per_host: int = 2) -> None:
        self.per_host = per_host
        self._lock = threading.Lock()
        self._sems: dict[str, threading.Semaphore] = {}
        self._robots: dict[str, Any] = {}
        self.refused: dict[str, str] = {}

    def sem(self, host: str) -> threading.Semaphore:
        with self._lock:
            if host not in self._sems:
                self._sems[host] = threading.Semaphore(self.per_host)
            return self._sems[host]

    def allowed(self, url: str) -> bool:
        u = urllib.parse.urlparse(url)
        host = f"{u.scheme}://{u.netloc}"
        with self._lock:
            rp = self._robots.get(host)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                with self.sem(u.netloc):
                    req = urllib.request.Request(host + "/robots.txt", headers={"User-Agent": UA_FETCH})
                    with urllib.request.urlopen(req, timeout=20) as r:
                        raw = r.read(512 * 1024).decode("utf-8", "replace")
                rp.parse(raw.splitlines())
            except urllib.error.HTTPError as e:
                # 4xx = no robots.txt (allow), except 401/403, read conservatively as a refusal;
                # 5xx = unreachable (disallow)
                if e.code in (401, 403) or e.code >= 500:
                    rp.disallow_all = True
                else:
                    rp.allow_all = True
            except Exception:  # noqa: BLE001 - unreachable robots.txt: assume complete disallow (RFC 9309 §2.3.1.4)
                rp.disallow_all = True
            with self._lock:
                self._robots[host] = rp
        return rp.can_fetch(UA_FETCH, url)


def http_get(url: str, gate: HostGate | None, max_bytes: int, timeout: int = 60,
             deadline_s: int = 180, tries: int = 4) -> tuple[int | str, bytes | None, str]:
    """GET with retries/backoff on 5xx and transport errors. Returns (status, body, final url)."""
    host = urllib.parse.urlparse(url).netloc
    delay = 2.0
    last: int | str = "err"
    for _attempt in range(tries):
        sem = gate.sem(host) if gate else None
        try:
            if sem:
                sem.acquire()
            req = urllib.request.Request(url, headers={"User-Agent": UA_FETCH, "Accept": "*/*"})
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                buf = io.BytesIO()
                while True:
                    blk = r.read(1 << 16)
                    if not blk:
                        break
                    buf.write(blk)
                    if buf.tell() > max_bytes:
                        return "too_large", None, r.geturl()
                    if time.time() - t0 > deadline_s:
                        return "deadline", None, r.geturl()
                return r.status, buf.getvalue(), r.geturl()
        except urllib.error.HTTPError as e:
            last = e.code
            if e.code in (404, 410, 401, 403, 429, 451) or 400 <= e.code < 500:
                return e.code, None, url
        except Exception as e:  # noqa: BLE001
            last = f"err:{type(e).__name__}"
        finally:
            if sem:
                sem.release()
        time.sleep(delay)
        delay *= 2
    return last, None, url


class Builder:
    """Fetch -> convert -> chunk -> screen for one work; memoised, thread-safe, resumable."""

    def __init__(self, lay: Layout, workers: int = 16) -> None:
        from ragstack.ingestion import jats
        from ragstack.ingestion.boilerplate import filter_from_mode
        from ragstack.ingestion.chunker_config import build_chunker
        from ragstack.ingestion.loaders import JsonlLoader
        from ragstack.ingestion.pipeline import IngestionPipeline
        from ragstack.ingestion.tokenization import HFTokenCounter
        from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore

        self.lay = lay
        self.jats = jats
        self.gate = HostGate(2)
        self.s3_sem = threading.Semaphore(2)
        self.counter = HFTokenCounter(model=TOKENIZER)
        self.counter.count("probe")
        chunker, _, _ = build_chunker("fixed_token", chunk_size=512, chunk_overlap=0, model=TOKENIZER,
                                      token_backend="hf")
        self.pipe = IngestionPipeline(loader=JsonlLoader(passthrough_keys=PASSTHROUGH.split(",")), chunker=chunker,
                                      embedder=None, vector_store=InMemoryVectorStore(),
                                      text_index=InMemoryTextIndex(), boilerplate_filter=filter_from_mode("flag"))
        self.chunk_lock = threading.Lock()
        self.pool = cf.ThreadPoolExecutor(max_workers=workers)
        self.futs: dict[str, cf.Future] = {}
        self.kfuts: dict[str, cf.Future] = {}
        self.lock = threading.Lock()
        self.out_path = lay.state / "outcomes.jsonl"
        self.pmc_path = lay.state / "pmc-meta.jsonl"
        self.done: dict[str, dict] = {}
        self.pmc: dict[str, dict] = {}
        for path, store in ((self.out_path, self.done), (self.pmc_path, self.pmc)):
            if path.exists():
                for line in open(path):
                    try:
                        r = json.loads(line)
                        store[r["key"]] = r
                    except Exception:
                        pass
        self.works: dict[str, ac.Work] = {}

    # ---------------------------------------------------------- PMC open-access metadata
    def pmc_meta(self, pmcid: str) -> dict:
        """The PMC bucket's per-article JSON (``is_pmc_openaccess``, ``license_code``, version)."""
        with self.lock:
            if pmcid in self.pmc:
                return self.pmc[pmcid]
        rec: dict[str, Any] = {"key": pmcid, "t": now()}
        folder = None
        with self.s3_sem:
            st, body, _ = http_get(f"{PMC_S3}{pmcid}.1/{pmcid}.1.json", None, 2 << 20, timeout=30)
            if body is not None:
                folder = f"{pmcid}.1"
            elif st == 404 or st == 403:
                st2, lst, _ = http_get(f"{PMC_S3}?list-type=2&max-keys=200&prefix={pmcid}.", None, 4 << 20, timeout=30)
                vers = set()
                if lst:
                    import re
                    for m in re.finditer(rf"<Key>{pmcid}\.(\d+)/", lst.decode("utf-8", "replace")):
                        vers.add(int(m.group(1)))
                if vers:
                    folder = f"{pmcid}.{max(vers)}"
                    st, body, _ = http_get(f"{PMC_S3}{folder}/{folder}.json", None, 2 << 20, timeout=30)
        if body is None:
            rec.update({"in_bucket": False, "status": st})
        else:
            try:
                m = json.loads(body)
            except Exception:  # noqa: BLE001
                m = {}
            rec.update({"in_bucket": True, "folder": folder, "is_pmc_openaccess": bool(m.get("is_pmc_openaccess")),
                        "is_manuscript": m.get("is_manuscript"), "is_retracted": m.get("is_retracted"),
                        "license_code": m.get("license_code"), "meta_sha256": sha256_bytes(body)})
        with self.lock:
            self.pmc[pmcid] = rec
            with open(self.pmc_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
        return rec

    def klass(self, w: ac.Work) -> str | None:
        """Source class before fetching: jats (PMC open-access copy) / pdf (PDF route only) / None."""
        if w.pmcid:
            m = self.pmc_meta(w.pmcid)
            if m.get("in_bucket") and m.get("is_pmc_openaccess"):
                return "jats"
        return "pdf" if w.pdf_locations else None

    def klass_async(self, w: ac.Work) -> cf.Future:
        with self.lock:
            f = self.kfuts.get(w.wid)
            if f is None:
                f = self.kfuts[w.wid] = self.pool.submit(self.klass, w)
            return f

    # ---------------------------------------------------------- resolve
    def resolve_async(self, w: ac.Work) -> cf.Future:
        with self.lock:
            self.works[w.wid] = w
            f = self.futs.get(w.wid)
            if f is None:
                if w.wid in self.done:
                    f = cf.Future()
                    f.set_result(self.done[w.wid])
                else:
                    f = self.pool.submit(self._resolve_safe, w)
                self.futs[w.wid] = f
            return f

    def resolve(self, w: ac.Work) -> dict:
        return self.resolve_async(w).result()

    def _resolve_safe(self, w: ac.Work) -> dict:
        try:
            rec = self._resolve(w)
        except Exception as e:  # noqa: BLE001 - one bad paper must not sink the run
            rec = {"ok": False, "reason": f"internal:{type(e).__name__}", "detail": str(e)[:300]}
        rec.update({"key": w.wid, "t": now()})
        with self.lock:
            self.done[w.wid] = rec
            with open(self.out_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
        return rec

    def _resolve(self, w: ac.Work) -> dict:
        attempts: list[dict] = []
        if w.pmcid:
            m = self.pmc_meta(w.pmcid)
            if m.get("in_bucket") and m.get("is_pmc_openaccess"):
                r = self._jats(w, m)
                if r.get("ok") or r.get("excluded_c0"):
                    return r
                attempts.append(r)
            else:
                attempts.append({"route": "jats", "reason": "pmc_not_in_oa_subset" if m.get("in_bucket")
                                 else "pmc_not_in_bucket"})
        if not w.pdf_locations:
            reason = attempts[-1]["reason"] if attempts else "no_oa_fulltext"
            return {"ok": False, "reason": reason, "attempts": attempts}
        for loc in w.pdf_locations[:PDF_URL_TRIES]:
            r = self._pdf(w, loc)
            attempts.append({k: r.get(k) for k in ("route", "reason", "host", "status")})
            if r.get("ok") or r.get("excluded_c0"):
                r["attempts"] = attempts
                return r
        last = attempts[-1]
        return {"ok": False, "reason": last.get("reason"), "attempts": attempts}

    def _jats(self, w: ac.Work, m: dict) -> dict:
        folder = m["folder"]
        url = f"{PMC_S3}{folder}/{folder}.xml"
        with self.s3_sem:
            st, raw, _ = http_get(url, None, 200 << 20, timeout=60)
        if raw is None:
            return {"ok": False, "route": "jats", "reason": f"jats_http_{st}"}
        rawp = self.lay.raw_jats / f"{w.pmcid}.xml"
        write_bytes(rawp, raw)
        try:
            clean = strip_back(raw)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "route": "jats", "reason": "jats_parse_error", "detail": str(e)[:200]}
        cleanp = self.lay.raw_jats / f"{w.pmcid}.clean.xml"
        write_bytes(cleanp, clean)
        row = {"sha256": sha256_bytes(raw), "source_url": url, "licence": m.get("license_code") or ""}
        recs, skips = self.jats.convert_file(cleanp, w.pmcid, row, count_tokens=self.counter.count)
        if not recs:
            return {"ok": False, "route": "jats", "reason": "jats_no_text", "skips": len(skips)}
        res = self._chunk(w, recs, w.pmcid, "jats")
        res.update({"route": "jats", "pmcid": w.pmcid, "raw_sha256": row["sha256"], "url": url,
                    "license": m.get("license_code"), "jats_records": len(recs), "jats_skips": len(skips),
                    "bytes": len(raw)})
        return res

    def _pdf(self, w: ac.Work, loc: ac.PdfLocation) -> dict:
        host = urllib.parse.urlparse(loc.url).netloc
        base = {"route": "pdf", "host": host, "url": loc.url, "license": loc.license, "location": loc.source}
        if host in self.gate.refused:
            return {**base, "ok": False, "reason": "host_refused_earlier"}
        try:
            if not self.gate.allowed(loc.url):
                return {**base, "ok": False, "reason": "robots_disallow"}
        except Exception:  # noqa: BLE001
            return {**base, "ok": False, "reason": "robots_error"}
        st, body, final = http_get(loc.url, self.gate, MAX_PDF_BYTES)
        if body is None:
            if st in (403, 429):
                self.gate.refused.setdefault(host, str(st))
                return {**base, "ok": False, "reason": f"host_refused_{st}", "status": st}
            return {**base, "ok": False, "reason": f"http_{st}", "status": st}
        if not body.lstrip()[:5].startswith(b"%PDF"):
            return {**base, "ok": False, "reason": "not_pdf", "status": st}
        rawp = self.lay.raw_pdf / f"{w.wid}.pdf"
        write_bytes(rawp, body)
        outp = self.lay.tmp / f"{w.wid}.pdf.jsonl"
        try:
            proc = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "pdf-extract", str(rawp), str(outp)],
                                  cwd=str(_HERE), capture_output=True, timeout=300, preexec_fn=_limits)
        except subprocess.TimeoutExpired:
            return {**base, "ok": False, "reason": "pdf_extract_timeout", "sha256": sha256_bytes(body)}
        if proc.returncode != 0 or not outp.exists():
            return {**base, "ok": False, "reason": "pdf_extract_failed", "sha256": sha256_bytes(body),
                    "detail": proc.stderr.decode("utf-8", "replace")[-300:]}
        recs = [json.loads(x) for x in open(outp)]
        outp.unlink()
        if not recs:
            return {**base, "ok": False, "reason": "pdf_no_text", "sha256": sha256_bytes(body)}
        for r in recs:
            r["path"] = w.wid
            r["metadata"] = {"title": w.title or "", "source_url": loc.url, "licence": loc.license,
                             "sha256": sha256_bytes(body), "content_type": "article"}
        res = self._chunk(w, recs, w.wid, "pdf")
        res.update({**base, "sha256": sha256_bytes(body), "bytes": len(body), "final_url": final})
        return res

    def _chunk(self, w: ac.Work, recs: list[dict], key: str, source: str) -> dict:
        textp = self.lay.text / f"{key}.jsonl"
        with open(textp, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.chmod(textp, 0o600)
        with self.chunk_lock:
            prep = asyncio.run(self.pipe.prepare_source(str(textp)))
        rows = [ac.export_row(c) for c in prep.chunks]
        chunkp = self.lay.chunks / f"{key}.jsonl"
        with open(chunkp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.chmod(chunkp, 0o600)
        scr = ac.c0_screen([r["content"] for r in rows], source)
        ok = bool(rows) and not scr["excluded"]
        return {"ok": ok, "source": source, "excluded_c0": scr["excluded"], "n_chunks": scr["n_chunks"],
                "c0_chunks": scr["c0_chunks"], "formfeed_chunks": scr["formfeed_chunks"], "c0_rate": scr["rate"],
                "source_path": key, "chunk_file": chunkp.name, "chunks_sha256": sha256_file(chunkp),
                "reason": None if ok else ("c0_screen" if scr["excluded"] else "no_chunks")}


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (6 << 30, 6 << 30))
    resource.setrlimit(resource.RLIMIT_CPU, (240, 240))
    os.setsid()


def strip_back(raw: bytes) -> bytes:
    """The OA harvest's index-time strip, verbatim (``/rag/oa/handoff/scripts/bulk_harvest.py``):
    drop ``<back>``, keep ``<floats-group>``. OA-1000 was converted from this ``clean/`` tree."""
    from xml.etree import ElementTree as ET

    root = ET.fromstring(raw)
    for parent in root.iter():
        for child in list(parent):
            if child.tag == "back":
                parent.remove(child)
    return ET.tostring(root, encoding="utf-8")


def outcome_of(rec: dict) -> ac.Outcome:
    return ac.Outcome(ok=bool(rec.get("ok")), source=rec.get("source"), reason=rec.get("reason"),
                      excluded_c0=bool(rec.get("excluded_c0")), n_chunks=rec.get("n_chunks") or 0,
                      c0_chunks=rec.get("c0_chunks") or 0)


# ====================================================================== stage: neighbours-fetch


def stage_neighbours_fetch(lay: Layout, b: Builder) -> dict:
    sel = json.load(open(lay.manifests / "neighbour-selection.json"))
    works = {k: _work_from_json(v) for k, v in sel["works"].items()}
    wids = [wid for p in sel["order"] for wid, _ in sel["attributed"][p]]
    futs = [b.resolve_async(works[w]) for w in wids]
    t0 = time.time()
    for i, f in enumerate(cf.as_completed(futs), 1):
        f.result()
        if i % 100 == 0:
            log(f"neighbours-fetch: {i}/{len(futs)} resolved, {time.time() - t0:.0f}s")
    res = Counter("ok:" + (b.done[w].get("source") or "") if b.done[w].get("ok") else "fail:" + str(b.done[w].get("reason"))
                  for w in wids)
    log(f"neighbours-fetch: done {dict(res)}")
    return dict(res)


# ====================================================================== stage: random


def stage_random(lay: Layout, b: Builder, lookahead: int = 2) -> dict:
    sel = json.load(open(lay.manifests / "neighbour-selection.json"))
    excl = load_exclusion(lay)
    linked = set(json.load(open(lay.state / "linked.json")))
    nworks = {k: _work_from_json(v) for k, v in sel["works"].items()}
    neighbour_wids = set(nworks)
    nexcl = ac.Exclusion()
    for w in nworks.values():
        nexcl.add(w)
    pool = load_pool(lay)
    log(f"random: pool loaded, {sum(len(v) for v in pool.values()):,} works in {len(pool)} topics")
    # per study paper: need (attributed neighbours that made it), and how many of them are PDF-sourced
    need: dict[str, tuple[int, int]] = {}
    for p in sel["order"]:
        ok = [b.done[w] for w, _ in sel["attributed"][p] if b.done.get(w, {}).get("ok")]
        n_pdf = sum(1 for r in ok if r.get("source") == "pdf")
        need[p] = (len(ok) - n_pdf, n_pdf)
    cands: dict[str, list[str]] = {}
    cand_stats: dict[str, Counter] = {}
    works: dict[str, ac.Work] = {}
    for p in sel["order"]:
        m = sel["per_study"][p]
        st = Counter()
        keep = []
        for w in pool.get(m["topic"], []):
            why = ac.eligible_random(w, m["topic"], m["year"], excl, linked, neighbour_wids)
            if why is None and nexcl.reason(w):
                why = "neighbour_identity"
            st[why or "eligible"] += 1
            if why is None:
                keep.append(w.wid)
                works[w.wid] = w
        cands[p] = keep
        cand_stats[p] = st
    results: dict[int, dict] = {}

    def run_draw(d: int) -> dict:
        taken: set[str] = set()
        ident = ac.Exclusion()
        orders = {p: ac.random_order(d, p, cands[p]) for p in sel["order"]}
        per: dict[str, dict] = {}

        def prefetch(p: str) -> None:
            nj, npdf = need[p]
            if nj + npdf == 0:
                return
            ks = [(wid, b.klass_async(works[wid])) for wid in orders[p][: 4 * (nj + npdf) + 20]]
            cj = cp = 0
            for wid, kf in ks:
                if wid in taken:
                    continue
                k = kf.result()
                if k == "jats" and cj < nj + 2:
                    cj += 1
                    b.resolve_async(works[wid])
                elif k == "pdf" and cp < 2 * npdf + 2:
                    cp += 1
                    b.resolve_async(works[wid])

        order = sel["order"]
        for i, p in enumerate(order):
            for q in order[i: i + 1 + lookahead]:
                if q not in per:
                    threading.Thread(target=prefetch, args=(q,), daemon=True).start()
            nj, npdf = need[p]
            r = ac.draw_for_study(
                orders[p], nj, npdf, taken,
                klass=lambda wid: b.klass_async(works[wid]).result(),
                resolve=lambda wid: outcome_of(b.resolve(works[wid])),
                is_dup=lambda wid: ident.reason(ac.Work(wid="", pmcid=works[wid].pmcid, doi=works[wid].doi)) is not None,
                on_take=lambda wid: ident.add(ac.Work(wid=wid, pmcid=works[wid].pmcid, doi=works[wid].doi)),
            )
            r["skipped"] = Counter(reason for _, reason in r["skipped"])
            per[p] = r
            if (i + 1) % 25 == 0:
                log(f"random-{d}: {i + 1}/{len(order)} study papers; taken {len(taken)}")
        return per

    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        futs = {d: ex.submit(run_draw, d) for d in (1, 2)}
        for d, f in futs.items():
            results[d] = f.result()
    for d in (1, 2):
        write_json(lay.manifests / f"random-{d}-draw.json", {
            "schema": "alias-corpus-random-draw/1", "draw": d, "seed": ac.SEED_RANDOM[d],
            "oa_definition": ac.OA_DEFINITION, "order": sel["order"], "need": need, "per_study": results[d],
            "candidates_per_study": {p: dict(cand_stats[p]) for p in sel["order"]},
            "works": {w: _work_json(works[w]) for p in results[d] for w in results[d][p]["jats"] + results[d][p]["pdf"]},
            "finished": now()})
    log("random: both draws written")
    return {d: sum(r["got"] for r in results[d].values()) for d in (1, 2)}


# ====================================================================== stage: finalize


def _du(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def stage_finalize(lay: Layout, b: Builder) -> dict:
    sel = json.load(open(lay.manifests / "neighbour-selection.json"))
    draws = {d: json.load(open(lay.manifests / f"random-{d}-draw.json")) for d in (1, 2)}
    arms: dict[str, list[tuple[str, str]]] = {"neighbour": []}
    side = {}
    for p in sel["order"]:
        for wid, s in sel["attributed"][p]:
            side[wid] = s
            arms["neighbour"].append((p, wid))
    for d in (1, 2):
        arms[f"random-{d}"] = [(p, w) for p in draws[d]["order"] for w in draws[d]["per_study"][p]["jats"] + draws[d]["per_study"][p]["pdf"]]
    counts: dict[str, Any] = {"schema": "alias-corpus-counts/1", "public": "counts only", "finished": now()}
    for arm, items in arms.items():
        c: Counter = Counter()
        fails: Counter = Counter()
        c0excl: Counter = Counter()
        per_study_final: Counter = Counter()
        out = lay.root / f"chunks-{arm}.jsonl"
        n_chunks = 0
        ids: set[str] = set()
        with open(out, "w", encoding="utf-8") as f:
            for p, wid in items:
                r = b.done.get(wid) or {}
                c["selected"] += 1
                if r.get("excluded_c0"):
                    c0excl[side.get(wid, "random") if arm == "neighbour" else "random"] += 1
                    c[f"fetched_{r.get('source')}"] += 1
                    continue
                if not r.get("ok"):
                    fails[str(r.get("reason"))] += 1
                    continue
                c[f"fetched_{r['source']}"] += 1
                c[f"final_{r['source']}"] += 1
                c["final"] += 1
                per_study_final[p] += 1
                c0 = r.get("c0_chunks") or 0
                if c0:
                    c[f"c0_affected_papers_{r['source']}"] += 1
                for line in open(lay.chunks / r["chunk_file"], encoding="utf-8"):
                    row = json.loads(line)
                    if row["chunk_id"] in ids:
                        c["duplicate_chunk_ids"] += 1
                        continue
                    ids.add(row["chunk_id"])
                    f.write(line)
                    n_chunks += 1
        os.chmod(out, 0o600)
        fin = c["final"]
        counts[arm] = {**dict(c), "failed_by_reason": dict(fails), "excluded_c0": dict(c0excl),
                       "chunks": n_chunks, "pdf_share": round(c["final_pdf"] / fin, 4) if fin else None,
                       "chunks_sha256": sha256_file(out),
                       "per_study_final_hist": dict(sorted(Counter(per_study_final[p] for p in sel["order"]).items()))}
    nshare = counts["neighbour"]["pdf_share"] or 0
    for d in (1, 2):
        arm = f"random-{d}"
        counts[arm]["pdf_share_minus_neighbour"] = round((counts[arm]["pdf_share"] or 0) - nshare, 4)
        counts[arm]["shortfall"] = sum(r["shortfall"] for r in draws[d]["per_study"].values())
        counts[arm]["filled_from_other_class"] = sum(r["filled_from_other_class"] for r in draws[d]["per_study"].values())
        sk: Counter = Counter()
        for r in draws[d]["per_study"].values():
            sk.update(r["skipped"])
        counts[arm]["candidates_skipped"] = dict(sk)
    s1 = {w for _, w in arms["random-1"] if (b.done.get(w) or {}).get("ok")}
    s2 = {w for _, w in arms["random-2"] if (b.done.get(w) or {}).get("ok")}
    counts["draw_overlap_final"] = len(s1 & s2)
    counts["hosts_refused"] = len(b.gate.refused)
    counts["downloads"] = {"jats_files": len(list(lay.raw_jats.glob("*.clean.xml"))),
                           "pdf_files": len(list(lay.raw_pdf.glob("*.pdf"))),
                           "resolved_works": len(b.done)}
    counts["disk_bytes"] = {k: _du(lay.root / k) for k in ("raw", "text", "chunks", "meta", "manifests", "logs", "state")}
    counts["disk_bytes"]["arm_chunk_files"] = sum((lay.root / f"chunks-{a}.jsonl").stat().st_size for a in arms)
    counts["disk_bytes"]["total"] = _du(lay.root)
    counts["selection"] = {k: sel[k] for k in ("study_resolved", "oa1000_resolved", "oa1000_unresolved",
                                               "picks_before_dedupe", "duplicates_dropped")}
    counts["selection"]["study_unresolved"] = len(sel["study_unresolved"])
    counts["seeds"] = {"neighbours": ac.SEED_NEIGHBOURS, "random_1": ac.SEED_RANDOM[1], "random_2": ac.SEED_RANDOM[2]}
    counts["manifest_sha256"] = {f.name: sha256_file(f) for f in sorted(lay.manifests.glob("*.json"))}
    scan = json.load(open(lay.state / "scan.json"))
    counts["random_pool_source"] = {"snapshot_date": scan["snapshot_date"], "manifest_sha256": scan["manifest_sha256"]}
    counts["oa_definition"] = ac.OA_DEFINITION
    counts["provenance"] = _provenance()
    write_json(lay.manifests / "counts.json", counts)
    log("finalize: " + json.dumps({a: {k: counts[a].get(k) for k in ("selected", "final", "chunks", "pdf_share")} for a in arms}))
    return counts


def _provenance() -> dict:
    try:
        import ragstack
        from ragstack.provenance import experiment_provenance

        p = experiment_provenance(repo=str(_PY.parent))
        p["ragstack_imported_from"] = str(Path(ragstack.__file__).resolve())
        return p
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


# ====================================================================== stage: chunkcheck


def stage_chunkcheck(lay: Layout, b: Builder, n: int = 3, seed: str = "261101|alias|chunkcheck") -> dict:
    """Re-chunk ``n`` OA-1000 papers (seeded pick) from the harvest's ``clean/`` tree through the same
    convert + chunk path as the added papers and diff against the export, row for row."""
    if sha256_file(OA_EXPORT) != OA_EXPORT_SHA:
        raise SystemExit("OA-1000 export sha mismatch")
    rows = [json.loads(x) for x in open(OA_MANIFEST)]
    pick = sorted(rows, key=lambda r: ac.seeded_hash(seed, r["pmcid"]))[:n]
    ref: dict[str, dict] = {}
    want = {r["pmcid"] for r in pick}
    for line in open(OA_EXPORT):
        d = json.loads(line)
        if d["metadata"]["source_path"].split("#")[0] in want:
            ref[d["chunk_id"]] = d
    mine: dict[str, dict] = {}
    for r in pick:
        manifest_row = {k: v for k, v in r.items() if k not in ("xml_path", "pmcid")}
        recs, _ = b.jats.convert_file(Path(OA_CORPUS) / r["xml_path"], r["pmcid"], manifest_row,
                                      count_tokens=b.counter.count)
        tp = lay.tmp / f"chunkcheck-{r['pmcid']}.jsonl"
        with open(tp, "w", encoding="utf-8") as f:
            for x in recs:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
        with b.chunk_lock:
            prep = asyncio.run(b.pipe.prepare_source(str(tp)))
        tp.unlink()
        for c in prep.chunks:
            mine[c.id] = ac.export_row(c)
    # and the strip_back path: re-strip the raw tree copy and compare bytes with the clean tree
    strip_equal = 0
    for r in pick:
        rawp = Path(OA_CORPUS) / r["xml_path"].replace("clean/", "xml/", 1)
        if rawp.exists() and strip_back(rawp.read_bytes()) == (Path(OA_CORPUS) / r["xml_path"]).read_bytes():
            strip_equal += 1
    diffs = [k for k in set(ref) | set(mine) if ref.get(k) != mine.get(k)]
    res = {"papers": n, "seed": seed, "ref_chunks": len(ref), "rechunked": len(mine),
           "ids_equal": set(ref) == set(mine), "rows_differing": len(diffs), "match": not diffs and bool(ref),
           "strip_back_reproduces_clean_tree": f"{strip_equal}/{n}", "export_sha256": OA_EXPORT_SHA,
           "chunker": "fixed_token 512/0, tokenizer " + TOKENIZER, "t": now()}
    write_json(lay.manifests / "chunkcheck.json", res)
    log(f"chunkcheck: {res}")
    return res


# ====================================================================== pdf-extract (subprocess)


def pdf_extract_main(src: str, out: str) -> int:
    """Run in a separate ``python -I`` process under resource limits: one PDF -> JSONL records."""
    from pdf_extract import extract_documents  # scripts/pdf_extract.py: the PDF plane's extractor

    recs, skipped = extract_documents([Path(src)])
    with open(out, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps({"text": r["text"], "path": r["path"], "metadata": {}}, ensure_ascii=False) + "\n")
    return 0


# ====================================================================== main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["run", "study", "neighbours", "scan", "neighbours-fetch", "random",
                                      "finalize", "chunkcheck", "pdf-extract"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--seed-cache", nargs=2, metavar=("CACHE_JSON", "QUERY_LOG"), default=None,
                    help="seed the OpenAlex URL cache from an earlier run of the same queries")
    ap.add_argument("--scan-workers", type=int, default=32)
    ap.add_argument("--fetch-workers", type=int, default=16)
    a = ap.parse_args(argv)
    if a.stage == "pdf-extract":
        sys.path.insert(0, str(_PY / "scripts"))
        return pdf_extract_main(*a.args)
    os.umask(0o077)
    os.environ.setdefault("HF_HOME", "/rag/cache")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    lay = Layout(a.root)
    log(f"alias_corpus_run {a.stage}: root {lay.root}; python {sys.executable}")
    api = OpenAlex(lay)
    if a.seed_cache:
        log(f"openalex: seeded {api.seed(*a.seed_cache)} cached responses")
    stages = ["study", "neighbours", "scan", "chunkcheck", "neighbours-fetch", "random", "finalize"] if a.stage == "run" else [a.stage]
    b: Builder | None = None
    for st in stages:
        marker = lay.state / f"done-{st}"
        if a.stage == "run" and marker.exists():
            log(f"{st}: already done")
            continue
        if st == "study":
            stage_study(lay)
        elif st == "neighbours":
            stage_neighbours(lay, api)
        elif st == "scan":
            stage_scan(lay, a.scan_workers)
        else:
            b = b or Builder(lay, a.fetch_workers)
            {"neighbours-fetch": stage_neighbours_fetch, "random": stage_random, "finalize": stage_finalize,
             "chunkcheck": stage_chunkcheck}[st](lay, b)
        marker.write_text(now())
    api.save()
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
