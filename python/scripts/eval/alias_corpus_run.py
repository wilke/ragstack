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

v2 (``run-v2``: chunkcheck-v2, neighbours-v2, random-v2, finalize-v2): neighbour walk-down, type
filter, matched random draws. v3 (``run-v3``: chunkcheck-v3, random-v3, finalize-v3; owner
2026-11-02): v2's open top length band split at 80 chunks; only the slots the split moves, or that v2
filled at a band-relaxed level, are re-drawn (``V3_REDRAW_RULE``); ``random-v3 --dry-run`` replays it
on memoised outcomes without fetching. v4 (``run-v4``: chunkcheck-v4, neighbours-v4, random-v4,
finalize-v4; owner 2026-11-03): v3 plus the repetition screen (every arm) and the nearest-band fill at
the ``any`` level; only the slots those rules touch are re-drawn (``V4_REDRAW_RULE``);
``neighbours-v4 --dry-run`` and ``random-v4 --dry-run`` replay on memoised outcomes. Every stage
records the provenance captured at launch.

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
from collections.abc import Callable, Mapping
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
#: provenance captured once at process launch (main); stages record this, not the tree at stage end
LAUNCH_PROVENANCE: dict | None = None


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
        for sub in ("raw/jats", "raw/pdf", "text", "chunks", "logs", "meta", "meta/pool-v2",
                    "manifests", "state", "tmp", "raw-v2/jats", "raw-v2/pdf", "text-v2", "chunks-v2"):
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
        #: a dry run sets this: the cache file is then never written, whatever the calls
        self.read_only = False
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

    def cached(self, params: dict[str, Any]) -> bool:
        """True when :meth:`get` would answer ``params`` from the cache (no network call)."""
        return f"{OPENALEX}?{urllib.parse.urlencode(params, safe=':|,')}" in self.cache

    def save_if_dirty(self) -> bool:
        """Write the cache only when a response was added since the last write (never when read-only)."""
        if not self.dirty or self.read_only:
            return False
        self.save()
        return True

    def save(self) -> None:
        if self.read_only:
            return
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


def neighbour_inputs(lay: Layout, api: OpenAlex) -> dict[str, Any]:
    """The OpenAlex inputs of the neighbour rule (cached URLs: a re-run reads the same responses):
    the OA-1000 exclusion, the study records, reference metadata and the citer lists."""
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
    api.save_if_dirty()   # every input above usually comes from the cache: do not rewrite it unchanged
    return {"oa_pmc": oa_pmc, "still_missing": still_missing, "excl": excl, "study_w": study_w,
            "study_missing": study_missing, "ref_meta": ref_meta, "light": light, "heavy": heavy, "acc": acc,
            "citers": citers}


def stage_neighbours(lay: Layout, api: OpenAlex) -> dict:
    inp = neighbour_inputs(lay, api)
    oa_pmc, still_missing, excl, study_w = inp["oa_pmc"], inp["still_missing"], inp["excl"], inp["study_w"]
    study_missing, ref_meta, light, heavy = inp["study_missing"], inp["ref_meta"], inp["light"], inp["heavy"]
    acc, citers = inp["acc"], inp["citers"]
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
    api.save_if_dirty()
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


PMC_IDS_URL = "https://ftp.ncbi.nlm.nih.gov/pub/pmc/PMC-ids.csv.gz"
POOL_DIR = "pool-v2"  # v1 (superseded): the snapshot's ``ids`` carries no pmcid, so v1 saw no PMC route
_PMC_MAP: dict[str, dict[str, str]] = {}


def _scan_init(map_path: str) -> None:
    import pickle

    with open(map_path, "rb") as f:
        _PMC_MAP.update(pickle.load(f))


def build_pmc_map(lay: Layout) -> dict:
    """NCBI's PMC-ids.csv.gz (PMID / DOI -> PMCID), pickled for the scan workers.

    The parquet snapshot's ``ids`` map has no ``pmcid`` key (the API's has), so the snapshot alone
    cannot tell a PMC copy exists; this file is the public mapping PMC itself publishes."""
    import csv
    import gzip
    import pickle

    gz = lay.meta / "PMC-ids.csv.gz"
    if not gz.exists():
        st, body, _ = http_get(PMC_IDS_URL, None, 2 << 30, timeout=120, deadline_s=3600)
        if body is None:
            raise SystemExit(f"PMC-ids download failed: {st}")
        write_bytes(gz, body)
    pmid: dict[str, str] = {}
    doi: dict[str, str] = {}
    with gzip.open(gz, "rt", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            pm = ac.norm_pmcid(row.get("PMCID"))
            if not pm:
                continue
            if row.get("PMID"):
                pmid[row["PMID"].strip()] = pm
            d = ac.norm_doi(row.get("DOI"))
            if d:
                doi[d] = pm
    out = lay.meta / "pmc-map.pkl"
    with open(out, "wb") as f:
        pickle.dump({"pmid": pmid, "doi": doi}, f, protocol=pickle.HIGHEST_PROTOCOL)
    return {"pmc_ids_sha256": sha256_file(gz), "pmid_keys": len(pmid), "doi_keys": len(doi), "path": str(out)}


def _with_pmcid(r: dict) -> dict:
    """Inject the PMCID from the PMC-ids map into a snapshot row's ``ids`` (by PMID, then DOI)."""
    ids = dict(r.get("ids") or [])
    if ids.get("pmcid"):
        return r
    pm = None
    if ids.get("pmid"):
        pm = _PMC_MAP.get("pmid", {}).get(ids["pmid"].rsplit("/", 1)[-1].strip())
    if not pm:
        d = ac.norm_doi(r.get("doi") or ids.get("doi"))
        pm = _PMC_MAP.get("doi", {}).get(d) if d else None
    if pm:
        r = dict(r, ids=[*list(r.get("ids") or []), ["pmcid", pm]])
    return r


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
                    r = _with_pmcid(r)
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
    pmc = build_pmc_map(lay)
    log(f"scan: PMC-ids map {pmc['pmid_keys']:,} PMIDs, {pmc['doi_keys']:,} DOIs")
    pool_dir = lay.meta / POOL_DIR
    jobs = [(u, str(pool_dir / (hashlib.sha1(u.encode()).hexdigest()[:16] + ".jsonl")), win) for u in files]
    log(f"scan: snapshot {man['date']}, {len(files)} files, {works['content_length'] / 1e9:.0f} GB; "
        f"{len(win)} topics; {workers} workers")
    done = n_in = n_keep = 0
    t0 = time.time()
    with cf.ProcessPoolExecutor(max_workers=workers, initializer=_scan_init, initargs=(pmc["path"],)) as ex:
        for _url, a, b in ex.map(_scan_file, jobs, chunksize=1):
            done += 1
            if a >= 0:
                n_in += a
                n_keep += b
            if done % 50 == 0:
                log(f"scan: {done}/{len(files)} files, rows read {n_in:,}, kept {n_keep:,}, {time.time() - t0:.0f}s")
    res = {"pmc_map": {k: v for k, v in pmc.items() if k != "path"}, "pool_dir": POOL_DIR,
           "snapshot_date": man["date"], "files": len(files), "topics": len(win), "rows_read_this_run": n_in,
           "rows_kept_this_run": n_keep, "columns": SCAN_COLS, "windows": win,
           "manifest_sha256": sha256_file(lay.meta / "snapshot-manifest.json"), "finished": now()}
    write_json(lay.state / "scan.json", res)
    log(f"scan: done {res['files']} files in {time.time() - t0:.0f}s")
    return res


def load_pool(lay: Layout) -> dict[str, list[ac.Work]]:
    """topic -> candidate works (deduplicated by work id)."""
    by_topic: dict[str, dict[str, ac.Work]] = defaultdict(dict)
    for f in sorted((lay.meta / POOL_DIR).glob("*.jsonl")):
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

    def __init__(self, lay: Layout, workers: int = 16, v2: bool = False, version: int | None = None) -> None:
        """``version`` N >= 2 (``v2=True`` is version 2): memoised outcomes are read from the v1 state
        files, then v2, ..., vN; every new outcome and file goes to ``state/*-vN.jsonl`` and
        ``raw-vN/``, ``text-vN/``, ``chunks-vN/`` (earlier files are read, never rewritten). Hosts
        that refused (403/429) in an earlier version stay refused (``host_refused_earlier``)."""
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
        self.version = version or (2 if v2 else 1)
        self.v2 = self.version >= 2
        sfx = f"-v{self.version}" if self.version >= 2 else ""
        for sub in (f"raw{sfx}/jats", f"raw{sfx}/pdf", f"text{sfx}", f"chunks{sfx}"):
            (lay.root / sub).mkdir(parents=True, exist_ok=True)
            os.chmod(lay.root / sub, 0o700)
        self.out_path = lay.state / f"outcomes{sfx}.jsonl"
        self.pmc_path = lay.state / f"pmc-meta{sfx}.jsonl"
        self.raw_jats_dir = lay.root / f"raw{sfx}" / "jats"
        self.raw_pdf_dir = lay.root / f"raw{sfx}" / "pdf"
        self.text_dir = lay.root / f"text{sfx}"
        self.chunks_dir = lay.root / f"chunks{sfx}"
        self.done: dict[str, dict] = {}
        self.pmc: dict[str, dict] = {}
        sources = [(self.out_path, self.done), (self.pmc_path, self.pmc)]
        earlier = [""] + [f"-v{k}" for k in range(2, self.version)] if self.version >= 2 else []
        sources = [*[(lay.state / f"{n}{e}.jsonl", st) for e in earlier
                     for n, st in (("outcomes", self.done), ("pmc-meta", self.pmc))], *sources]
        for path, store in sources:
            if path.exists():
                for line in open(path):
                    try:
                        r = json.loads(line)
                        store[r["key"]] = r
                    except Exception:
                        pass
        self.works: dict[str, ac.Work] = {}
        if self.v2:
            for r in self.done.values():
                for a in [r, *(r.get("attempts") or [])]:
                    if (a or {}).get("reason") in ("host_refused_403", "host_refused_429") and a.get("host"):
                        self.gate.refused.setdefault(a["host"], "earlier:" + a["reason"][-3:])

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
                if w.wid in self.done and self._stale(w, self.done[w.wid]):
                    del self.done[w.wid]
                if w.wid in self.done:
                    f = cf.Future()
                    f.set_result(self.done[w.wid])
                else:
                    f = self.pool.submit(self._resolve_safe, w)
                self.futs[w.wid] = f
            return f

    @staticmethod
    def _stale(w: ac.Work, rec: dict) -> bool:
        """A memoised outcome made without trying the JATS route, for a work now known to have a
        PMCID (the v1 pool lacked PMCIDs): resolve it again."""
        tried_jats = rec.get("route") == "jats" or any(
            (a or {}).get("route") == "jats" for a in rec.get("attempts") or [])
        return bool(w.pmcid) and not tried_jats

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
        rawp = self.raw_jats_dir / f"{w.pmcid}.xml"
        write_bytes(rawp, raw)
        try:
            clean = strip_back(raw)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "route": "jats", "reason": "jats_parse_error", "detail": str(e)[:200]}
        cleanp = self.raw_jats_dir / f"{w.pmcid}.clean.xml"
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
        rawp = self.raw_pdf_dir / f"{w.wid}.pdf"
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
        textp = self.text_dir / f"{key}.jsonl"
        with open(textp, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.chmod(textp, 0o600)
        with self.chunk_lock:
            prep = asyncio.run(self.pipe.prepare_source(str(textp)))
        rows = [ac.export_row(c) for c in prep.chunks]
        chunkp = self.chunks_dir / f"{key}.jsonl"
        with open(chunkp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.chmod(chunkp, 0o600)
        scr = ac.c0_screen([r["content"] for r in rows], source)
        ok = bool(rows) and not scr["excluded"]
        return {"ok": ok, "source": source, "excluded_c0": scr["excluded"], "n_chunks": scr["n_chunks"],
                "c0_chunks": scr["c0_chunks"], "formfeed_chunks": scr["formfeed_chunks"], "c0_rate": scr["rate"],
                "source_path": key, "chunk_file": chunkp.name, "chunk_dir": chunkp.parent.name, "chunks_sha256": sha256_file(chunkp),
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


def stage_chunkcheck(lay: Layout, b: Builder, n: int = 3, seed: str = "261101|alias|chunkcheck",
                     out_name: str = "chunkcheck.json") -> dict:
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
    write_json(lay.manifests / out_name, res)
    log(f"chunkcheck: {res}")
    return res


# ====================================================================== v2 (owner, 2026-11-01)
#
# The v2 rebuild: the neighbour walk-down, the article-type filter in both arms, and random draws
# matched per study paper on (type, length band, source). Every v1 file is kept; v2 writes
# ``*-v2`` manifests, chunk files and state, and reuses every v1 fetch outcome.

V2_BANDS = 5
V2_CAP = {"ref": ac.REF_CAP, "citer": ac.CITER_CAP}
V2_SCHEMA_TYPE = ("OpenAlex `type` in {article, review} kept (article -> article, review -> review); every other "
                  "type dropped, both arms; type_crossref not read (absent from the snapshot projection)")
V2_RULE = ("per study paper, down the ranked list of references (most cited first) and of citers (most recent "
           "first) with open-access full text, not in OA-1000: skip works whose OpenAlex type is not article/"
           "review, that fail to fetch (not in the PMC OA subset, robots, 403/429, not a PDF, ...) or that the "
           "C0 screen excludes, until 5 references and 5 citers are obtained or the list is exhausted; shared "
           "neighbours attributed to the first study paper in the seeded order (O5, unchanged: a later study "
           "paper's slot is used and not replaced); another work id with the same PMCID or DOI is the same paper "
           "and is added once")


def _known_fn(b: Builder, works: dict[str, ac.Work], outcome=None):
    """``outcome``: memoised record -> :class:`ac.Outcome` (default :func:`outcome_of`; v4 passes the
    repetition-screened one)."""
    outcome = outcome or outcome_of

    def known(wid: str) -> ac.Outcome | None:
        rec = b.done.get(wid)
        if rec is None or b._stale(works[wid], rec):
            return None
        return outcome(rec)
    return known


def load_sel_v2(lay: Layout) -> dict:
    return json.load(open(lay.manifests / "neighbour-selection-v2.json"))


def stage_neighbours_v2(lay: Layout, api: OpenAlex, b: Builder, version: int = 2, outcome=None,
                        dry_run: bool = False, rule: str = V2_RULE) -> dict:
    """The v2 neighbour walk-down. v4 calls it with ``version=4`` and the repetition-screened
    ``outcome`` (record -> Outcome). ``dry_run`` (v4 only): memoised outcomes and cached OpenAlex
    responses only — an unknown outcome counts as a failed fetch, an uncached citer page as the end
    of the list — and the selection goes to ``tmp/neighbour-selection-v<N>.dryrun.json``."""
    outcome = outcome or outcome_of
    inp = neighbour_inputs(lay, api)
    excl, study_w, ref_meta = inp["excl"], inp["study_w"], inp["ref_meta"]
    saved = json.load(open(lay.state / "exclusion.json"))
    if any(set(saved[k]) != getattr(excl, k) for k in ("wids", "pmcids", "dois", "titles")):
        raise SystemExit("v2: OA-1000 exclusion differs from state/exclusion.json")
    ranked: dict[tuple[str, str], list[ac.Work]] = {}
    cit: dict[str, dict[str, Any]] = {}
    works: dict[str, ac.Work] = {}
    for p, w in study_w.items():
        refs = [ac.work_from_api(ref_meta[ac.short_id(r)]) for r in (w.get("referenced_works") or [])
                if ac.short_id(r) in ref_meta]
        ranked[(p, "ref")] = ac.rank_references(p, refs, excl)
        cl, total, complete = inp["citers"][p]
        cit[p] = {"raw": {ac.short_id(c["id"]): c for c in cl}, "total": total, "complete": complete, "pages": 1}
        ranked[(p, "citer")] = ac.rank_citers(p, [ac.work_from_api(c) for c in cit[p]["raw"].values()], excl)
    for lst in ranked.values():
        for x in lst:
            works.setdefault(x.wid, x)
    known0 = _known_fn(b, works, outcome)
    dry_unknown: set[str] = set()
    dry_uncached_pages = 0

    def known(wid: str) -> ac.Outcome | None:
        if wid in dry_unknown:
            return ac.Outcome(ok=False, reason="dry_run_unknown")
        return known0(wid)

    def final_outcome(wid: str) -> ac.Outcome:
        o = known(wid)
        return o if o is not None else outcome(b.done[wid])

    t0 = time.time()
    rnd = 0
    while True:
        rnd += 1
        want: list[str] = []
        paged = 0
        for (p, side), lst in ranked.items():
            nx = ac.next_to_resolve(lst, V2_CAP[side], known)
            if not nx and side == "citer" and not cit[p]["complete"]:
                if ac.walk_down(lst, V2_CAP[side], final_outcome)["exhausted"]:   # every needed outcome is known here
                    c = cit[p]
                    params = {"filter": f"cites:{study_w[p]['id'].rsplit('/', 1)[-1]}",
                              "sort": "publication_date:desc", "per-page": 200, "page": c["pages"] + 1,
                              "select": OA_FIELDS}
                    if dry_run and not api.cached(params):
                        dry_uncached_pages += 1
                        c["complete"] = True
                        continue
                    c["pages"] += 1
                    body = api.get(params)
                    for r in body["results"]:
                        c["raw"].setdefault(ac.short_id(r["id"]), r)
                    c["complete"] = not body["results"] or c["pages"] * 200 >= body["meta"]["count"]
                    ranked[(p, side)] = ac.rank_citers(p, [ac.work_from_api(x) for x in c["raw"].values()], excl)
                    for x in ranked[(p, side)]:
                        works.setdefault(x.wid, x)
                    paged += 1
                    continue
            want += nx
        want = list(dict.fromkeys(want))
        if not want and not paged:
            break
        if dry_run:
            dry_unknown.update(want)
        else:
            futs = [b.resolve_async(works[w]) for w in want]
            for f in cf.as_completed(futs):
                f.result()
        log(f"neighbours-v{version}: round {rnd}: resolved {len(want)}, citer pages fetched {paged}, "
            f"{time.time() - t0:.0f}s" + (f" (dry run: {len(want)} unknown)" if dry_run else ""))
    api.save_if_dirty()   # a no-op in a dry run (read-only client) and when nothing was fetched
    picks: dict[str, list[tuple[str, str]]] = {}
    per_study: dict[str, dict] = {}
    skips: dict[str, dict[str, list]] = {}
    for p, w in study_w.items():
        sides = {s: ac.walk_down(ranked[(p, s)], V2_CAP[s], final_outcome) for s in ("ref", "citer")}
        picks[p] = [(x, "ref") for x in sides["ref"]["picked"]] + [(x, "citer") for x in sides["citer"]["picked"]]
        skips[p] = {s: [list(t) for t in sides[s]["skipped"]] for s in sides}
        sw = ac.work_from_api(w)
        per_study[p] = {"wid": sw.wid, "topic": sw.topic, "year": sw.year,
                        **{s: {"ranked": r["ranked"], "examined": r["examined"], "picked": len(r["picked"]),
                               "exhausted": r["exhausted"], "skipped": dict(Counter(x for _, x in r["skipped"]))}
                           for s, r in sides.items()},
                        "citer_list": {"cited_by_count": cit[p]["total"], "complete": cit[p]["complete"],
                                       "pages": cit[p]["pages"]}}
    attributed, dropped = ac.attribute(picks)
    order = list(ac.attribution_order(study_w))
    ident = ac.Exclusion()
    id_dropped: list[list[str]] = []
    for p in order:
        keep = []
        for wid, side in attributed[p]:
            x = works[wid]
            if ident.reason(ac.Work(wid="", pmcid=x.pmcid, doi=x.doi)):
                id_dropped.append([p, wid, side])
                continue
            ident.add(ac.Work(wid=wid, pmcid=x.pmcid, doi=x.doi))
            keep.append((wid, side))
        attributed[p] = keep
    examined = {wid for p in skips for s in skips[p].values() for wid, _ in s}
    examined |= {wid for v in picks.values() for wid, _ in v}
    sel = {
        "schema": f"alias-corpus-neighbour-selection/{version}", "version": f"v{version}", "seed": ac.SEED_NEIGHBOURS,
        "oa_definition": ac.OA_DEFINITION, "type_filter": V2_SCHEMA_TYPE, "rule": rule, "queried": now(),
        "study_resolved": len(study_w), "study_unresolved": inp["study_missing"],
        "oa1000_resolved": len(inp["oa_pmc"]) - len(inp["still_missing"]), "oa1000_unresolved": len(inp["still_missing"]),
        "order": order, "attributed": attributed, "picks_before_dedupe": sum(len(v) for v in picks.values()),
        "duplicates_dropped": dropped, "identity_duplicates_dropped": id_dropped, "per_study": per_study,
        "skips": skips, "rounds": rnd,
        "works": {k: _work_json(works[k]) for k in sorted(examined)},
        "study_works": {p: _work_json(ac.work_from_api(w)) for p, w in study_w.items()},
    }
    if version >= 4:
        sel["dry_run"] = {"unknown_outcomes": len(dry_unknown), "uncached_citer_pages": dry_uncached_pages} if dry_run else None
        sel["provenance"] = LAUNCH_PROVENANCE
    name = f"neighbour-selection-v{version}.json"
    write_json((lay.tmp / name.replace(".json", ".dryrun.json")) if dry_run else (lay.manifests / name), sel)
    n_attr = sum(len(v) for v in attributed.values())
    log(f"neighbours-v{version}: picks {sel['picks_before_dedupe']}, shared dropped {dropped}, identity dropped "
        f"{len(id_dropped)}, attributed {n_attr}, examined {len(examined)}")
    return {"attributed": n_attr, "sel": sel}


def _v2_cuts(sel: dict, b: Builder) -> list[float]:
    vals = [b.done[w]["n_chunks"] for p in sel["order"] for w, _ in sel["attributed"][p]]
    return ac.length_cuts(vals, V2_BANDS)


def stage_random_v2(lay: Layout, b: Builder, lookahead: int = 2) -> dict:
    sel = load_sel_v2(lay)
    sel1 = json.load(open(lay.manifests / "neighbour-selection.json"))
    excl = load_exclusion(lay)
    linked = set(json.load(open(lay.state / "linked.json")))
    nworks = {k: _work_from_json(v) for k, v in {**sel1["works"], **sel["works"]}.items()}
    nexcl = ac.Exclusion()
    for w in nworks.values():
        nexcl.add(w)
    nwids = set(nworks)
    cuts = _v2_cuts(sel, b)

    def band(n: int) -> int:
        return ac.band_of(n, cuts)

    quota: dict[str, Counter] = {}
    for p in sel["order"]:
        q: Counter = Counter()
        for wid, _ in sel["attributed"][p]:
            r = b.done[wid]
            q[(ac.type_class(sel["works"][wid]["type"]), band(r["n_chunks"]), r["source"])] += 1
        quota[p] = q
    pool = load_pool(lay)
    log(f"random-v2: pool {sum(len(v) for v in pool.values()):,} works; length cuts {cuts}")
    cands: dict[str, list[str]] = {}
    cand_stats: dict[str, dict] = {}
    works: dict[str, ac.Work] = {}
    for p in sel["order"]:
        m = sel["per_study"][p]
        st: Counter = Counter()
        keep = []
        for w in pool.get(m["topic"], []):
            why = ac.eligible_random(w, m["topic"], m["year"], excl, linked, nwids)
            if why is None and nexcl.reason(w):
                why = "neighbour_identity"
            if why is None and ac.type_class(w.type) is None:
                why = "type"
            st[why or "eligible"] += 1
            if why is None:
                keep.append(w.wid)
                works[w.wid] = w
        cands[p] = keep
        cand_stats[p] = dict(st)

    def run_draw(d: int) -> dict:
        taken: set[str] = set()
        ident = ac.Exclusion()
        orders = {p: ac.random_order(d, p, cands[p], seeds=ac.SEED_RANDOM_V2) for p in sel["order"]}
        per: dict[str, dict] = {}

        def prefetch(p: str) -> None:
            q = quota[p]
            need = sum(q.values())
            if not need:
                return
            ts = {(t, s) for t, _, s in q}
            tt = {t for t, _ in ts}
            looked = sent = 0
            for wid in orders[p]:
                if sent >= 3 * need + 6 or looked >= 12 * need + 40:
                    break
                if wid in taken or ac.type_class(works[wid].type) not in tt:
                    continue
                looked += 1
                k = b.klass_async(works[wid]).result()
                if (ac.type_class(works[wid].type), k) in ts:
                    sent += 1
                    b.resolve_async(works[wid])

        order = sel["order"]
        for i, p in enumerate(order):
            for q in order[i: i + 1 + lookahead]:
                if q not in per:
                    threading.Thread(target=prefetch, args=(q,), daemon=True).start()
            need = sum(quota[p].values())
            per[p] = ac.draw_matched(
                orders[p], quota[p], taken,
                type_of=lambda wid: works[wid].type,
                klass=lambda wid: b.klass_async(works[wid]).result(),
                resolve=lambda wid: outcome_of(b.resolve(works[wid])),
                band=band,
                is_dup=lambda wid: ident.reason(ac.Work(wid="", pmcid=works[wid].pmcid, doi=works[wid].doi)) is not None,
                on_take=lambda wid: ident.add(ac.Work(wid=wid, pmcid=works[wid].pmcid, doi=works[wid].doi)),
                max_resolves=max(40, 15 * need),
            )
            if (i + 1) % 25 == 0:
                log(f"random-{d}-v2: {i + 1}/{len(order)} study papers; taken {len(taken)}")
        return per

    results: dict[int, dict] = {}
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        futs = {d: ex.submit(run_draw, d) for d in (1, 2)}
        for d, f in futs.items():
            results[d] = f.result()
    for d in (1, 2):
        write_json(lay.manifests / f"random-{d}-draw-v2.json", {
            "schema": "alias-corpus-random-draw/2", "version": "v2", "draw": d, "seed": ac.SEED_RANDOM_V2[d],
            "oa_definition": ac.OA_DEFINITION, "type_filter": V2_SCHEMA_TYPE,
            "matching": {"cells": "(type class, length band, source) per study paper, from its attributed neighbours",
                         "length_bands": f"{V2_BANDS} bands at the quantiles of the neighbour arm's chunks per paper",
                         "cuts": cuts, "relax_order": list(ac.RELAX_ORDER),
                         "max_resolves": "max(40, 15 x need) per study paper"},
            "order": sel["order"], "quota": {p: {"|".join(map(str, c)): n for c, n in sorted(quota[p].items())}
                                             for p in sel["order"]},
            "per_study": results[d], "candidates_per_study": cand_stats,
            "works": {t["wid"]: _work_json(works[t["wid"]]) for p in results[d] for t in results[d][p]["took"]},
            "finished": now()})
    log("random-v2: both draws written")
    return {d: sum(r["got"] for r in results[d].values()) for d in (1, 2)}


def _stats(xs: list[int]) -> dict:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {"n": len(s), "sum": sum(s), "mean": round(sum(s) / len(s), 2), "median": s[len(s) // 2] if len(s) % 2
            else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2, "p10": s[len(s) // 10], "p90": s[(9 * len(s)) // 10],
            "max": s[-1]}


def stage_finalize_v2(lay: Layout, b: Builder, version: int = 2, sel: dict | None = None) -> dict:
    """Per-arm chunk files and counts of version ``version`` (2 or 3: the neighbour arm is v2's in both;
    4 passes its own neighbour selection as ``sel``)."""
    sfx = f"-v{version}"
    sel = sel or load_sel_v2(lay)
    draws = {d: json.load(open(lay.manifests / f"random-{d}-draw{sfx}.json")) for d in (1, 2)}
    cuts = draws[1]["matching"]["cuts"]
    wtype = {k: v["type"] for k, v in sel["works"].items()}
    for d in (1, 2):
        wtype.update({k: v["type"] for k, v in draws[d]["works"].items()})
    arms: dict[str, list[tuple[str, str]]] = {"neighbour": [(p, w) for p in sel["order"] for w, _ in sel["attributed"][p]]}
    for d in (1, 2):
        arms[f"random-{d}"] = [(p, t["wid"]) for p in draws[d]["order"] for t in draws[d]["per_study"][p]["took"]]
    counts: dict[str, Any] = {"schema": f"alias-corpus-counts/{version}", "version": f"v{version}", "public": "counts only",
                              "finished": now(), "length_cuts": cuts, "type_filter": V2_SCHEMA_TYPE}
    per_study_chunks: dict[str, dict[str, int]] = {}
    for arm, items in arms.items():
        out = lay.root / f"chunks-{arm}{sfx}.jsonl"
        ids: set[str] = set()
        dup = 0
        n_chunks = 0
        papers: list[int] = []
        src: Counter = Counter()
        typ: Counter = Counter()
        bands: Counter = Counter()
        psc: Counter = Counter()
        with open(out, "w", encoding="utf-8") as f:
            for p, wid in items:
                r = b.done[wid]
                if not r.get("ok"):
                    raise SystemExit(f"{arm}: {wid} in the arm but not ok")
                src[r["source"]] += 1
                typ[ac.type_class(wtype[wid])] += 1
                bands[ac.band_of(r["n_chunks"], cuts)] += 1
                papers.append(r["n_chunks"])
                for line in open(lay.root / r.get("chunk_dir", "chunks") / r["chunk_file"], encoding="utf-8"):
                    row = json.loads(line)
                    if row["chunk_id"] in ids:
                        dup += 1
                        continue
                    ids.add(row["chunk_id"])
                    f.write(line)
                    n_chunks += 1
                    psc[p] += 1
        os.chmod(out, 0o600)
        per_study_chunks[arm] = dict(psc)
        counts[arm] = {"papers": len(items), "chunks": n_chunks, "duplicate_chunk_ids": dup,
                       "chunks_per_paper": _stats(papers), "source": dict(src),
                       "pdf_share": round(src["pdf"] / len(items), 4) if items else None,
                       "type_mix": dict(typ), "band_counts": {str(k): v for k, v in sorted(bands.items())},
                       "chunks_sha256": sha256_file(out)}
    # neighbour targets, shortfalls, skips
    tgt = 2 * ac.REF_CAP
    per_n = Counter()
    for p in sel["order"]:
        per_n[p] = len(sel["attributed"][p])
    sk: dict[str, Counter] = {"ref": Counter(), "citer": Counter()}
    for p in sel["skips"]:
        for s, lst in sel["skips"][p].items():
            sk[s].update(x for _, x in lst)
    ps = sel["per_study"]
    counts["neighbour"].update({
        "target_per_study": tgt, "target_total": tgt * len(sel["order"]),
        "per_study_attributed_hist": dict(sorted(Counter(per_n[p] for p in sel["order"]).items())),
        "studies_below_target": sum(1 for p in sel["order"] if per_n[p] < tgt),
        "picked_before_dedupe": sel["picks_before_dedupe"], "shared_dropped": sel["duplicates_dropped"],
        "identity_dropped": len(sel["identity_duplicates_dropped"]),
        "exhausted": {s: sum(1 for p in ps if ps[p][s]["exhausted"]) for s in ("ref", "citer")},
        "picked_by_side": {s: sum(ps[p][s]["picked"] for p in ps) for s in ("ref", "citer")},
        "skipped_by_reason": {s: dict(sk[s].most_common()) for s in sk},
        "citer_pages_beyond_first": sum(ps[p]["citer_list"]["pages"] - 1 for p in ps)})
    ntot = counts["neighbour"]["chunks"]
    for d in (1, 2):
        arm = f"random-{d}"
        per = draws[d]["per_study"]
        skd: Counter = Counter()
        lv: Counter = Counter()
        for r in per.values():
            skd.update(r["skipped"])
            lv.update(t["level"] for t in r["took"])
        short = {p: r["shortfall"] for p, r in per.items() if r["shortfall"]}
        counts[arm].update({
            "target_total": sum(r["need"] for r in per.values()), "shortfall_total": sum(short.values()),
            "studies_short": len(short), "shortfall_per_study": short,
            "match_level": dict(lv), "candidates_skipped": dict(skd.most_common()),
            "rejected_for_band": skd.get("rejected_for_band", 0),
            "budget_hit_studies": sum(1 for r in per.values() if r["budget_hit"]),
            "resolves": sum(r["resolves"] for r in per.values()),
            "pdf_share_minus_neighbour": round((counts[arm]["pdf_share"] or 0) - (counts["neighbour"]["pdf_share"] or 0), 4),
            "chunks_vs_neighbour": round(counts[arm]["chunks"] / ntot - 1, 4) if ntot else None})
    tot = (counts["random-1"]["chunks"] + counts["random-2"]["chunks"]) / 2
    counts["volume_verdict"] = {"neighbour_chunks": ntot, "random_mean_chunks": tot,
                                "random_mean_vs_neighbour": round(tot / ntot - 1, 4) if ntot else None,
                                "within_5pct_each_draw": all(abs(counts[f"random-{d}"]["chunks_vs_neighbour"]) <= 0.05
                                                             for d in (1, 2))}
    s1 = {w for _, w in arms["random-1"]}
    s2 = {w for _, w in arms["random-2"]}
    counts["draw_overlap"] = len(s1 & s2)
    counts["hosts_refused"] = len(b.gate.refused)
    counts["disk_bytes"] = {k: _du(lay.root / k) for k in (f"raw{sfx}", f"text{sfx}", f"chunks{sfx}")}
    counts["disk_bytes"][f"arm_chunk_files_v{version}"] = sum((lay.root / f"chunks-{a}{sfx}.jsonl").stat().st_size for a in arms)
    counts["disk_bytes"]["root_total"] = _du(lay.root)
    counts["seeds"] = {"neighbours": ac.SEED_NEIGHBOURS, "random_1": ac.SEED_RANDOM_V2[1], "random_2": ac.SEED_RANDOM_V2[2]}
    # the per-study files first, so that manifest_sha256 covers them: every *-vN.json manifest except
    # counts-vN (this file) and power-vN (written later, by alias_study_power)
    write_json(lay.manifests / f"per-study-chunks{sfx}.json", per_study_chunks)
    write_json(lay.manifests / f"neighbour-counts{sfx}.json", {p: len(sel["attributed"][p]) for p in sel["order"]})
    counts["manifest_sha256"] = {f.name: sha256_file(f) for f in sorted(lay.manifests.glob(f"*{sfx}.json"))
                                 if not f.name.startswith(("counts-", "power-"))}
    counts["manifest_sha256_covers"] = f"every manifests/*{sfx}.json except counts{sfx}.json and power{sfx}.json"
    counts["provenance"] = LAUNCH_PROVENANCE or _provenance()
    write_json(lay.manifests / f"counts{sfx}.json", counts)
    log(f"finalize{sfx}: " + json.dumps({a: {k: counts[a].get(k) for k in ("papers", "chunks", "pdf_share")} for a in arms})
        + " " + json.dumps(counts["volume_verdict"]))
    return counts


# ====================================================================== v3 (owner, 2026-11-02)
#
# v3 splits v2's open top length band at 80 chunks (bands ..., 46-79, >= 80; the neighbour arm is
# unchanged) and re-draws, in both random draws, only the slots that the split moves or that v2
# filled at a band-relaxed level; every other v2 take is kept as it was. Same seeds, same seeded
# candidate order; v3 writes ``*-v3`` manifests and state and reuses every earlier fetch outcome.

V3_REDRAW_RULE = (
    "per study paper and draw, the v2 takes are carried over in v2 take order (alias_corpus.carry_over): a take "
    "matched at a band-relaxed level ('any') is re-drawn (relaxed_on_band); every other take keeps its v2 level and "
    "goes to the v3 slot (slot type, its own v3 band, slot source) when that cell has room, else it is re-drawn "
    "(band_assignment_changed: the split moved its slot). The open slots are then re-drawn with the v2 matched draw "
    "rule (alias_corpus.draw_matched) on the same seeded candidate order (seeds 261101|alias|random-{1,2}-v2), "
    "walked from the top: candidates taken by any kept or re-drawn slot of the same draw (any study paper, or the "
    "same PMCID/DOI) are skipped, i.e. the walk continues past already-consumed candidates; released v2 takes and "
    "passed-over candidates are eligible again; study papers in the v2 order")
V3_BUDGET_RULE = ("v2's rule unchanged: per study paper with open slots, per phase of draw_matched (strict pass, "
                  "relaxation), at most max(40, 15 x need) resolves, need = ALL the study paper's slots (kept + "
                  "open), as a fresh v2-rule walk of that study paper would have; memoised outcomes count against "
                  "it (the walk from the top re-reads the candidates v2 examined); new network fetches are a subset")


def _count_lines(p: Path) -> int:
    return sum(1 for _ in open(p)) if p.exists() else 0


def stage_random_v3(lay: Layout, b: Builder, lookahead: int = 2, dry_run: bool = False) -> dict:
    """v3 re-draw (see :data:`V3_REDRAW_RULE`). ``dry_run``: memoised outcomes and PMC metadata
    only (an unknown one counts as a failed fetch, an unknown PMC record as JATS-eligible); writes
    ``tmp/random-*-draw-v3.dryrun.json`` and no manifest."""
    sel = load_sel_v2(lay)
    sel1 = json.load(open(lay.manifests / "neighbour-selection.json"))
    v2 = {d: json.load(open(lay.manifests / f"random-{d}-draw-v2.json")) for d in (1, 2)}
    excl = load_exclusion(lay)
    linked = set(json.load(open(lay.state / "linked.json")))
    nworks = {k: _work_from_json(v) for k, v in {**sel1["works"], **sel["works"]}.items()}
    nexcl = ac.Exclusion()
    for w in nworks.values():
        nexcl.add(w)
    nwids = set(nworks)
    cuts2 = _v2_cuts(sel, b)
    if cuts2 != v2[1]["matching"]["cuts"] or cuts2 != v2[2]["matching"]["cuts"]:
        raise SystemExit(f"v3: neighbour cuts {cuts2} differ from the v2 draws' {v2[1]['matching']['cuts']}")
    cuts = ac.v3_cuts(cuts2)

    def band(n: int) -> int:
        return ac.band_of(n, cuts)

    quota: dict[str, Counter] = {}
    for p in sel["order"]:
        q: Counter = Counter()
        for wid, _ in sel["attributed"][p]:
            r = b.done[wid]
            q[(ac.type_class(sel["works"][wid]["type"]), band(r["n_chunks"]), r["source"])] += 1
        quota[p] = q
    pool = load_pool(lay)
    log(f"random-v3: pool {sum(len(v) for v in pool.values()):,} works; length cuts {cuts}; dry_run={dry_run}")
    cands: dict[str, list[str]] = {}
    cand_stats: dict[str, dict] = {}
    type_dropped: Counter = Counter()          # (study x work) pairs dropped by the type filter, by OpenAlex type
    type_dropped_works: dict[str, set] = defaultdict(set)
    works: dict[str, ac.Work] = {}
    for p in sel["order"]:
        m = sel["per_study"][p]
        st: Counter = Counter()
        keep = []
        for w in pool.get(m["topic"], []):
            why = ac.eligible_random(w, m["topic"], m["year"], excl, linked, nwids)
            if why is None and nexcl.reason(w):
                why = "neighbour_identity"
            if why is None and ac.type_class(w.type) is None:
                why = "type"
                type_dropped[w.type or "none"] += 1
                type_dropped_works[w.type or "none"].add(w.wid)
            st[why or "eligible"] += 1
            if why is None:
                keep.append(w.wid)
                works[w.wid] = w
        cands[p] = keep
        cand_stats[p] = dict(st)
    if any(cand_stats[p] != v2[1]["candidates_per_study"][p] for p in sel["order"]):
        raise SystemExit("v3: candidate pools differ from v2's")

    def n_chunks(wid: str) -> int:
        return int(b.done[wid]["n_chunks"])

    def klass(wid: str) -> str | None:
        if not dry_run:
            return b.klass_async(works[wid]).result()
        w = works[wid]
        if w.pmcid:
            m = b.pmc.get(w.pmcid)
            if m is None or (m.get("in_bucket") and m.get("is_pmc_openaccess")):
                return "jats"
        return "pdf" if w.pdf_locations else None

    unknown: Counter = Counter()

    def resolve(wid: str) -> ac.Outcome:
        if not dry_run:
            return outcome_of(b.resolve(works[wid]))
        rec = b.done.get(wid)
        if rec is None or b._stale(works[wid], rec):
            unknown["outcome"] += 1
            return ac.Outcome(ok=False, reason="dry_run_unknown")
        return outcome_of(rec)

    cand_sets = {p: set(v) for p, v in cands.items()}

    def run_draw(d: int) -> dict:
        orders = {p: ac.random_order(d, p, cands[p], seeds=ac.SEED_RANDOM_V2) for p in sel["order"]}
        carry = {p: ac.carry_over(v2[d]["per_study"][p]["took"], quota[p], band, n_chunks) for p in sel["order"]}
        taken: set[str] = set()
        ident = ac.Exclusion()
        for p in sel["order"]:
            for t in carry[p]["kept"]:
                if t["wid"] not in cand_sets[p]:
                    raise SystemExit(f"v3: kept take {t['wid']} is not a candidate of {p}")
                taken.add(t["wid"])
                ident.add(ac.Work(wid=t["wid"], pmcid=works[t["wid"]].pmcid, doi=works[t["wid"]].doi))
        per: dict[str, dict] = {}

        def prefetch(p: str) -> None:
            q = carry[p]["open"]
            need = sum(q.values())
            if not need or dry_run:
                return
            ts = {(t, s) for t, _, s in q}
            tt = {t for t, _ in ts}
            looked = sent = 0
            for wid in orders[p]:
                if sent >= 3 * need + 6 or looked >= 12 * need + 40:
                    break
                if wid in taken or ac.type_class(works[wid].type) not in tt:
                    continue
                looked += 1
                k = b.klass_async(works[wid]).result()
                if (ac.type_class(works[wid].type), k) in ts:
                    sent += 1
                    b.resolve_async(works[wid])

        order = sel["order"]
        for i, p in enumerate(order):
            for q in order[i: i + 1 + lookahead]:
                if q not in per and carry[q]["open"]:
                    threading.Thread(target=prefetch, args=(q,), daemon=True).start()
            c = carry[p]
            need_open = sum(c["open"].values())
            rec: dict[str, Any] = {"need": sum(quota[p].values()), "kept": len(c["kept"]),
                                   "released": [{k: t.get(k) for k in ("wid", "type", "band", "source", "level",
                                                                       "for_cell", "why", "v3_band")}
                                                for t in c["released"]],
                                   "open": {"|".join(map(str, k)): n for k, n in c["open"].items()}}
            if need_open:
                budget = max(40, 15 * rec["need"])
                r = ac.draw_matched(
                    orders[p], c["open"], taken,
                    type_of=lambda wid: works[wid].type, klass=klass, resolve=resolve, band=band,
                    is_dup=lambda wid: ident.reason(ac.Work(wid="", pmcid=works[wid].pmcid, doi=works[wid].doi)) is not None,
                    on_take=lambda wid: ident.add(ac.Work(wid=wid, pmcid=works[wid].pmcid, doi=works[wid].doi)),
                    max_resolves=budget)
                new = [{**t, "redrawn": True} for t in r["took"]]
                rec.update({"redraw_budget": budget, "skipped": r["skipped"], "resolves": r["resolves"],
                            "resolves_by_phase": r["resolves_by_phase"], "budget_hit": r["budget_hit"],
                            "relaxed": r["relaxed"], "unfilled": r["unfilled"], "passed_over": r["passed_over"]})
            else:
                new = []
                rec.update({"redraw_budget": 0, "skipped": {}, "resolves": 0, "budget_hit": False, "relaxed": {},
                            "unfilled": {}})
            took = [{**t, "redrawn": False} for t in c["kept"]] + new
            rec.update({"took": took, "got": len(took), "shortfall": max(0, rec["need"] - len(took)),
                        "redrawn_slots": need_open, "redrawn_got": len(new),
                        "strict_shortfall": {"|".join(map(str, k)): n
                                             for k, n in ac.strict_shortfall(quota[p], took).items()}})
            per[p] = rec
            if (i + 1) % 50 == 0:
                log(f"random-{d}-v3: {i + 1}/{len(order)} study papers; taken {len(taken)}")
        return per

    out3 = lay.state / "outcomes-v3.jsonl"
    lines0 = _count_lines(out3)
    results: dict[int, dict] = {}
    if dry_run:
        for d in (1, 2):
            results[d] = run_draw(d)
    else:
        with cf.ThreadPoolExecutor(max_workers=2) as ex:
            futs = {d: ex.submit(run_draw, d) for d in (1, 2)}
            for d, f in futs.items():
                results[d] = f.result()
    new_fetches = _count_lines(out3) - lines0
    qtot: Counter = Counter()
    for p in sel["order"]:
        qtot.update(quota[p])
    for d in (1, 2):
        per = results[d]
        lv: Counter = Counter(t["level"] for r in per.values() for t in r["took"])
        why: Counter = Counter(t["why"] for r in per.values() for t in r["released"])
        ss: Counter = Counter()
        for r in per.values():
            ss.update(r["strict_shortfall"])
        summary = {
            "slots_total": sum(r["need"] for r in per.values()), "kept": sum(r["kept"] for r in per.values()),
            "redrawn_slots": sum(r["redrawn_slots"] for r in per.values()),
            "redrawn_by_reason": dict(why), "redrawn_got": sum(r["redrawn_got"] for r in per.values()),
            "studies_with_redraw": sum(1 for r in per.values() if r["redrawn_slots"]),
            "redraw_resolves": sum(r["resolves"] for r in per.values()),
            "redraw_budget_hit_studies": sum(1 for r in per.values() if r["budget_hit"]),
            "shortfall_total": sum(r["shortfall"] for r in per.values()),
            "match_level": dict(lv), "strict_share": round(lv["strict"] / max(1, sum(lv.values())), 4),
            "strict_shortfall_per_cell": {k: {"quota": qtot[tuple(_cell_of(k))], "short": n}
                                          for k, n in sorted(ss.items())},
            "redraw_skipped": dict(sum((Counter(r["skipped"]) for r in per.values()), Counter())),
        }
        if dry_run:
            summary["dry_run_unknown_outcomes"] = unknown["outcome"]
        log(f"random-{d}-v3 summary: " + json.dumps({k: v for k, v in summary.items() if k != "strict_shortfall_per_cell"}))
        for k, v in summary["strict_shortfall_per_cell"].items():
            log(f"random-{d}-v3 strict shortfall cell {k}: {v['short']} of {v['quota']}")
        doc = {
            "schema": "alias-corpus-random-draw/3", "version": "v3", "draw": d, "seed": ac.SEED_RANDOM_V2[d],
            "oa_definition": ac.OA_DEFINITION, "type_filter": V2_SCHEMA_TYPE,
            "matching": {"cells": "(type class, length band, source) per study paper, from its attributed neighbours",
                         "length_bands": f"v2's {V2_BANDS} quantile bands with the open top band split at "
                                         f"{ac.V3_SPLIT_AT:g} chunks", "cuts": cuts, "v2_cuts": cuts2,
                         "relax_order": list(ac.RELAX_ORDER),
                         "relax_rule": "source first, then type, then band last ('any')",
                         "max_resolves": V3_BUDGET_RULE, "redraw_rule": V3_REDRAW_RULE},
            "summary": summary, "new_network_fetches_both_draws": None if dry_run else new_fetches,
            "type_dropped_candidate_pairs": dict(type_dropped.most_common()),
            "type_dropped_unique_works": {k: len(v) for k, v in sorted(type_dropped_works.items())},
            "order": sel["order"], "quota": {p: {"|".join(map(str, c)): n for c, n in sorted(quota[p].items())}
                                             for p in sel["order"]},
            "per_study": per, "candidates_per_study": cand_stats,
            "works": {t["wid"]: _work_json(works[t["wid"]]) for p in per for t in per[p]["took"]},
            "provenance": LAUNCH_PROVENANCE, "finished": now()}
        target = (lay.tmp / f"random-{d}-draw-v3.dryrun.json") if dry_run else (lay.manifests / f"random-{d}-draw-v3.json")
        write_json(target, doc)
    log(f"random-v3: both draws written (new network fetches {new_fetches})")
    return {d: sum(r["got"] for r in results[d].values()) for d in (1, 2)}


def _cell_of(key: str) -> tuple:
    t, bnd, s = key.split("|")
    return (t, int(bnd), s)


def stage_finalize_v3(lay: Layout, b: Builder) -> dict:
    counts = stage_finalize_v2(lay, b, version=3)
    sel = load_sel_v2(lay)
    draws = {d: json.load(open(lay.manifests / f"random-{d}-draw-v3.json")) for d in (1, 2)}
    cuts = draws[1]["matching"]["cuts"]
    nb = len(cuts) + 1
    # per band: papers and chunks, neighbour vs each draw
    def per_band(items: list[str]) -> dict[str, dict[str, int]]:
        out = {str(k): {"papers": 0, "chunks": 0} for k in range(nb)}
        for wid in items:
            n = int(b.done[wid]["n_chunks"])
            o = out[str(ac.band_of(n, cuts))]
            o["papers"] += 1
            o["chunks"] += n
        return out
    arms = {"neighbour": [w for p in sel["order"] for w, _ in sel["attributed"][p]]}
    for d in (1, 2):
        arms[f"random-{d}"] = [t["wid"] for p in draws[d]["order"] for t in draws[d]["per_study"][p]["took"]]
    bands = {a: per_band(v) for a, v in arms.items()}
    labels = [f"<{cuts[0]:g}"] + [f"{cuts[i - 1]:g}-{cuts[i] - 1:g}" for i in range(1, len(cuts))] + [f">={cuts[-1]:g}"]
    counts["band_labels"] = labels
    counts["per_band"] = bands
    top = str(nb - 1)
    counts["top_band_papers_matched"] = all(bands[f"random-{d}"][top]["papers"] == bands["neighbour"][top]["papers"]
                                            for d in (1, 2))
    # neighbour-side facts: type drops (preprints named), host_refused_earlier skips
    nsk: Counter = Counter()
    for p in sel["skips"]:
        for lst in sel["skips"][p].values():
            nsk.update(x for _, x in lst)
    for d in (1, 2):
        D = draws[d]
        v2d = json.load(open(lay.manifests / f"random-{d}-draw-v2.json"))
        v2sk: Counter = Counter()
        for r in v2d["per_study"].values():
            v2sk.update(r["skipped"])
        counts[f"random-{d}"].update({
            "v3_redraw": D["summary"], "new_network_fetches_both_draws": D["new_network_fetches_both_draws"],
            "host_refused_earlier_skips": {"v2_draw_walk": v2sk.get("host_refused_earlier", 0),
                                           "v3_redraw_walk": D["summary"]["redraw_skipped"].get("host_refused_earlier", 0)},
            "preprints_dropped": {"candidate_pairs": D["type_dropped_candidate_pairs"].get("preprint", 0),
                                  "unique_works": D["type_dropped_unique_works"].get("preprint", 0)},
            "type_dropped_unique_works": D["type_dropped_unique_works"],
            "fetch_budget": V3_BUDGET_RULE + "; v2's strict-pass and relax budgets were max(40, 15 x need) per study paper"})
    counts["neighbour"].update({
        "preprints_dropped": nsk.get("type:preprint", 0),
        "type_dropped": {k[5:]: v for k, v in sorted(nsk.items()) if k.startswith("type:")},
        "host_refused_earlier_skips": nsk.get("host_refused_earlier", 0)})
    counts["relax_rule"] = "source first, then type, then band last; each take records its level"
    counts["redraw_rule"] = V3_REDRAW_RULE
    st = json.loads((lay.state / "provenance-v3.json").read_text()) if (lay.state / "provenance-v3.json").exists() else {}
    commits = {k: v.get("commit") for k, v in st.items()}
    # this stage's own marker is written after it returns: record its launch commit here
    commits["finalize-v3"] = (LAUNCH_PROVENANCE or {}).get("commit")
    counts["stage_provenance_commits"] = commits
    counts["stage_provenance_authority"] = ("state/provenance-v3.json (full provenance per stage, written when each "
                                            "stage's done marker is written); this map repeats its commits")
    counts["stage_commits_agree"] = len(set(commits.values())) <= 1
    counts["v2_provenance_note"] = (
        "counts-v2.json recorded the commit checked out when finalize-v2 ran (c83f15f), not the launch commit of "
        "run-v2 (c51b382, logs/run-v2.launch.json); v3 records the provenance captured at process launch, per stage")
    write_json(lay.manifests / "counts-v3.json", counts)
    log("finalize-v3: per band " + json.dumps(bands) + f"; top band matched {counts['top_band_papers_matched']}")
    return counts


# ====================================================================== v4 (owner, 2026-11-03)
#
# v4 = v3 plus two rules. (1) Nearest-band fill at the band-relaxed level ``any``: the slot takes the
# candidate already fetched by that study paper's walk whose length band is nearest (ties: type
# match, source match, seeded order), no new fetch. (2) The repetition screen: every added paper in
# every arm, any source, after the C0 screen, is excluded when its distinct-chunk ratio is below
# 0.90; excluded neighbours are replaced by the existing walk-down, excluded random takes by
# continuing the walk. v3 takes the rules do not touch are kept exactly. Same seeds, same bands
# (v3's cuts, fixed), same budget; v4 writes ``*-v4`` manifests and state.

V4_SCREEN_RULE = ("repetition screen (owner, 2026-11-03): every added paper in every arm (neighbour, random-1, "
                  "random-2), any source, after the C0 screen; distinct-chunk ratio = distinct chunk texts (exact "
                  "match after NFC + whitespace collapse) / chunks at fixed_token 512 / overlap 0; excluded when "
                  f"< {ac.REPETITION_THRESHOLD:.2f}; random arms continue their walk in seeded order, the neighbour "
                  "arm uses its walk-down")
V4_NEAREST_RULE = ("nearest-band fill at the 'any' level (owner, 2026-11-03): instead of the first passed-over "
                   "candidate in seeded order, a study paper's open 'any' slots are filled together from the "
                   "candidates its walk already fetched and accepted (not taken, not a duplicate): over all (open "
                   "slot, candidate) pairs, the pair with the smallest key is filled first, then the smallest of the "
                   "pairs left, and so on (greedy, not a minimum-total assignment); key = band-index distance (over "
                   "<20/20-26/27-33/34-45/46-79/>=80), then type mismatch, then source (JATS/PDF) mismatch, then the "
                   "slot's position in the sorted order of the open cells, then the candidate's seeded order; no new "
                   "fetch")
V4_REDRAW_RULE = (
    "per study paper and draw, the v3 takes are carried over in v3 take order (alias_corpus.carry_over_v4): a take "
    "is released when its paper fails the repetition screen (repetition_screen), when it was matched at the band-"
    "relaxed level (any_nearest_band), or when its slot has no room in the v4 quota (quota_changed: the study "
    "paper's neighbours changed under the screen); every other take is kept exactly. A study paper whose only "
    "released slots are any_nearest_band (quota unchanged) refills them with alias_corpus.nearest_band_fill from "
    "the candidates its v3 walk fetched before the 'any' level (replayed from memoised outcomes and checked "
    "take-for-take against the v3 manifest), screened, not taken in the draw: no walk, no fetch. Any other study "
    "paper with open slots walks again with alias_corpus.draw_matched (any_rule='nearest', repetition-screened "
    "outcomes) on the same seeded order from the top, skipping candidates taken in the draw; study papers in "
    "the v3 order")
V4_BUDGET_RULE = V3_BUDGET_RULE


class Repetition:
    """Distinct-chunk ratio per accepted work, read from its chunk file (sha checked against the record)."""

    def __init__(self, lay: Layout, b: Builder, enabled: bool = True) -> None:
        self.lay, self.b, self.enabled = lay, b, enabled
        self.cache: dict[tuple[str, str, str], dict] = {}
        self.lock = threading.Lock()

    def screen(self, rec: dict) -> dict | None:
        if not rec or not rec.get("ok"):
            return None
        key = (rec.get("chunk_dir", "chunks"), rec["chunk_file"], rec["chunks_sha256"])
        with self.lock:
            hit = self.cache.get(key)
        if hit is not None:
            return hit
        path = self.lay.root / key[0] / key[1]
        data = path.read_bytes()
        if sha256_bytes(data) != key[2]:
            raise SystemExit(f"v4: chunk file {key[0]}/{key[1]} differs from its outcome record")
        texts = [json.loads(x)["content"] for x in data.split(b"\n") if x.strip()]
        r = ac.repetition_screen(texts)
        with self.lock:
            self.cache[key] = r
        return r

    def outcome(self, rec: dict) -> ac.Outcome:
        scr = self.screen(rec) if self.enabled else None
        return ac.screen_outcome(outcome_of(rec), scr["ratio"] if scr else None)

    def ratio(self, wid: str) -> float | None:
        scr = self.screen(self.b.done.get(wid) or {})
        return scr["ratio"] if scr else None

    def excluded(self, wid: str) -> bool:
        r = self.ratio(wid)
        return self.enabled and r is not None and r < ac.REPETITION_THRESHOLD


def _sel_v4(lay: Layout, dry_run: bool = False) -> dict:
    return json.load(open((lay.tmp / "neighbour-selection-v4.dryrun.json") if dry_run
                          else (lay.manifests / "neighbour-selection-v4.json")))


def stage_neighbours_v4(lay: Layout, api: OpenAlex, b: Builder, dry_run: bool = False,
                        rep: Repetition | None = None) -> dict:
    """The v2 walk-down with repetition-screened outcomes; every change against v2 is listed."""
    rep = rep or Repetition(lay, b)
    sel2 = load_sel_v2(lay)
    lines0 = _count_lines(b.out_path)
    sel = stage_neighbours_v2(lay, api, b, version=4, outcome=rep.outcome, dry_run=dry_run,
                              rule=V2_RULE + "; v4: " + V4_SCREEN_RULE)["sel"]
    if sel["order"] != sel2["order"]:
        raise SystemExit("v4: attribution order differs from v2's")
    excl2 = [[p, wid, side] for p in sel2["order"] for wid, side in sel2["attributed"][p] if rep.excluded(wid)]
    changed = {}
    for p in sel["order"]:
        a2 = [tuple(x) for x in sel2["attributed"][p]]
        a4 = [tuple(x) for x in sel["attributed"][p]]
        if a2 != a4:
            changed[p] = {"removed": [list(x) for x in a2 if x not in a4], "added": [list(x) for x in a4 if x not in a2]}
    screened_ids = {wid for _, wid, _ in excl2}
    cascade = [p for p in changed if not any(w in screened_ids for w, _ in changed[p]["removed"])]
    arm_ratios = sorted((round(rep.ratio(wid) or 1.0, 4), int(b.done[wid]["n_chunks"]), b.done[wid]["source"])
                        for p in sel2["order"] for wid, _ in sel2["attributed"][p])
    sel["v4_changes"] = {
        "screen_rule": V4_SCREEN_RULE, "v2_attributed_screened_out": excl2, "studies_changed": changed,
        "studies_changed_without_a_screened_neighbour": cascade,
        "v2_arm_ratios_below_0.95": [list(x) for x in arm_ratios if x[0] < 0.95],
        "new_outcomes_this_stage": _count_lines(b.out_path) - lines0}
    name = "neighbour-selection-v4.json"
    write_json((lay.tmp / name.replace(".json", ".dryrun.json")) if dry_run else (lay.manifests / name), sel)
    log(f"neighbours-v4: v2 neighbours screened out {len(excl2)}; studies changed {len(changed)} "
        f"(without a screened neighbour: {len(cascade)}); new outcomes {sel['v4_changes']['new_outcomes_this_stage']}; "
        f"v2 arm ratios < 0.95: {sel['v4_changes']['v2_arm_ratios_below_0.95']}")
    return sel


def _random_candidates(pool: dict[str, list[ac.Work]], sel: dict, excl: ac.Exclusion, linked: set[str],
                       nworks: dict[str, ac.Work]) -> tuple[dict[str, list[str]], dict[str, ac.Work], dict[str, dict]]:
    """The v2/v3 random candidate rule (stage_random_v3), against the neighbour works ``nworks``."""
    nexcl = ac.Exclusion()
    for w in nworks.values():
        nexcl.add(w)
    nwids = set(nworks)
    cands: dict[str, list[str]] = {}
    stats: dict[str, dict] = {}
    works: dict[str, ac.Work] = {}
    for p in sel["order"]:
        m = sel["per_study"][p]
        st: Counter = Counter()
        keep = []
        for w in pool.get(m["topic"], []):
            why = ac.eligible_random(w, m["topic"], m["year"], excl, linked, nwids)
            if why is None and nexcl.reason(w):
                why = "neighbour_identity"
            if why is None and ac.type_class(w.type) is None:
                why = "type"
            st[why or "eligible"] += 1
            if why is None:
                keep.append(w.wid)
                works[w.wid] = w
        cands[p] = keep
        stats[p] = dict(st)
    return cands, works, stats


def _take_key(t: dict) -> tuple:
    return (t["wid"], t["level"], tuple(t["for_cell"]))


def v4_carry(took3: list[dict], candidates: set[str], quota4: Mapping[ac.Cell, int],
             excluded: Callable[[str], bool]) -> dict[str, Any]:
    """One study paper's v4 carry-over (:func:`ac.carry_over_v4`), refusing a v3 take that is no longer
    a random candidate (a v4 neighbour-walk work would leave the pool; none did)."""
    gone = [t["wid"] for t in took3 if t["wid"] not in candidates]
    if gone:
        raise SystemExit(f"v4: v3 takes {gone} are no longer candidates (the v4 neighbour arm)")
    return ac.carry_over_v4(took3, quota4, excluded)


def v4_redraw_mode(carry: Mapping[str, Any], quota_unchanged: bool) -> str | None:
    """How a study paper's open slots are filled in v4: ``None`` (nothing open), ``nearest_from_v3_walk``
    (every release is ``any_nearest_band`` and the quota is unchanged: refill from the v3 walk, no fetch)
    or ``walk`` (anything else: walk again with the v4 rules)."""
    if not sum(carry["open"].values()):
        return None
    if {t["why"] for t in carry["released"]} == {"any_nearest_band"} and quota_unchanged:
        return "nearest_from_v3_walk"
    return "walk"


def v4_study_redraw(carry: Mapping[str, Any], quota3: Mapping[ac.Cell, int], quota4: Mapping[ac.Cell, int],
                    order_p: list[str], fetched3: Mapping[str, ac.Cell], taken: set[str], *,
                    excluded: Callable[[str], bool], is_dup: Callable[[str], bool], on_take: Callable[[str], None],
                    type_of: Callable[[str], str | None], klass: Callable[[str], str | None],
                    resolve: Callable[[str], ac.Outcome], band: Callable[[int], int]) -> dict[str, Any]:
    """One study paper's v4 step after the carry-over: the per-study record of a v4 draw manifest.

    ``fetched3``: the candidates its v3 walk fetched and accepted before the ``any`` level (wid -> cell).
    ``nearest_from_v3_walk`` refills from them (screened by ``excluded``, not taken, not duplicates)
    with :func:`ac.nearest_band_fill`; ``walk`` runs :func:`ac.draw_matched` (``any_rule="nearest"``)
    on ``order_p`` with the budget max(40, 15 x all slots). ``taken`` and ``on_take`` see every take.
    """
    c = carry
    need = sum(quota4.values())
    need_open = sum(c["open"].values())
    mode = v4_redraw_mode(c, quota3 == quota4)
    rec: dict[str, Any] = {
        "need": need, "kept": len(c["kept"]),
        "released": [{k: t.get(k) for k in ("wid", "type", "band", "source", "level", "for_cell", "why")}
                     for t in c["released"]],
        "open": {"|".join(map(str, k)): n for k, n in c["open"].items()},
        "quota_changed": quota3 != quota4, "mode": mode}
    new: list[dict] = []
    if mode is None:
        rec.update({"skipped": {}, "resolves": 0, "budget_hit": False, "unfilled": {}})
    elif mode == "nearest_from_v3_walk":
        pool_p = {w: cell for w, cell in fetched3.items() if not excluded(w)}
        rank = {w: i for i, w in enumerate(order_p)}
        missing = [w for w in pool_p if w not in rank]
        if missing:
            raise SystemExit(f"v4: fetched candidates {missing} not in the v4 order")

        def pick(wid: str, cell: ac.Cell, slot: ac.Cell, dist: int) -> None:
            on_take(wid)
            new.append({"wid": wid, "type": cell[0], "band": cell[1], "source": cell[2], "level": "any",
                        "for_cell": list(slot), "band_distance": dist})

        ac.nearest_band_fill(c["open"], pool_p, taken, rank, is_dup, pick)
        left = Counter(c["open"])
        for t in new:
            left[tuple(t["for_cell"])] -= 1
        rec.update({"nearest_pool": len(pool_p), "skipped": {}, "resolves": 0, "budget_hit": False,
                    "unfilled": {"|".join(map(str, k)): n for k, n in sorted(left.items()) if n > 0}})
    else:
        budget = max(40, 15 * need)
        r = ac.draw_matched(order_p, c["open"], taken, type_of=type_of, klass=klass, resolve=resolve, band=band,
                            is_dup=is_dup, on_take=on_take, max_resolves=budget, any_rule="nearest")
        new = r["took"]
        rec.update({"redraw_budget": budget, "skipped": r["skipped"], "resolves": r["resolves"],
                    "resolves_by_phase": r["resolves_by_phase"], "budget_hit": r["budget_hit"],
                    "relaxed": r["relaxed"], "unfilled": r["unfilled"], "passed_over": r["passed_over"]})
    took = [{**t, "redrawn": False} for t in c["kept"]] + [{**t, "redrawn": True} for t in new]
    rec.update({"took": took, "got": len(took), "shortfall": max(0, need - len(took)),
                "redrawn_slots": need_open, "redrawn_got": len(new),
                "strict_shortfall": {"|".join(map(str, k)): n for k, n in ac.strict_shortfall(quota4, took).items()}})
    return rec


def stage_random_v4(lay: Layout, b: Builder, dry_run: bool = False, rep: Repetition | None = None) -> dict:
    """v4 carry-over and re-draw (see :data:`V4_REDRAW_RULE`). ``dry_run``: memoised outcomes only, the
    dry-run neighbour selection; writes ``tmp/random-*-draw-v4.dryrun.json`` and no manifest."""
    rep = rep or Repetition(lay, b)
    sel1 = json.load(open(lay.manifests / "neighbour-selection.json"))
    sel2 = load_sel_v2(lay)
    sel4 = _sel_v4(lay, dry_run)
    if sel4["order"] != sel2["order"]:
        raise SystemExit("v4: attribution order differs from v2's")
    order = sel2["order"]
    v2 = {d: json.load(open(lay.manifests / f"random-{d}-draw-v2.json")) for d in (1, 2)}
    v3 = {d: json.load(open(lay.manifests / f"random-{d}-draw-v3.json")) for d in (1, 2)}
    excl = load_exclusion(lay)
    linked = set(json.load(open(lay.state / "linked.json")))
    nworks3 = {k: _work_from_json(v) for k, v in {**sel1["works"], **sel2["works"]}.items()}
    nworks4 = {**nworks3, **{k: _work_from_json(v) for k, v in sel4["works"].items()}}
    cuts = [float(c) for c in v3[1]["matching"]["cuts"]]
    if cuts != v3[2]["matching"]["cuts"] or cuts != ac.v3_cuts(_v2_cuts(sel2, b)):
        raise SystemExit(f"v4: the v3 cuts {cuts} are not reproduced")
    arm4_n = [int(b.done[w]["n_chunks"]) for p in order for w, _ in sel4["attributed"][p]]
    cuts_if_recomputed = ac.v3_cuts(ac.length_cuts(arm4_n, V2_BANDS))

    def band(n: int) -> int:
        return ac.band_of(n, cuts)

    def quota_of(sel: dict) -> dict[str, Counter]:
        out: dict[str, Counter] = {}
        for p in order:
            q: Counter = Counter()
            for wid, _ in sel["attributed"][p]:
                r = b.done[wid]
                q[(ac.type_class(sel["works"][wid]["type"]), band(r["n_chunks"]), r["source"])] += 1
            out[p] = q
        return out

    quota3, quota4 = quota_of(sel2), quota_of(sel4)
    for p in order:
        if {"|".join(map(str, c)): n for c, n in sorted(quota3[p].items())} != v3[1]["quota"][p]:
            raise SystemExit(f"v4: v3 quota of {p} not reproduced")
    pool = load_pool(lay)
    cands3, works3, stats3 = _random_candidates(pool, sel2, excl, linked, nworks3)
    if any(stats3[p] != v3[1]["candidates_per_study"][p] for p in order):
        raise SystemExit("v4: v3 candidate pools not reproduced")
    cands4, works, stats4 = _random_candidates(pool, sel2, excl, linked, nworks4)
    cand4_sets = {p: set(v) for p, v in cands4.items()}
    log(f"random-v4: pool {sum(len(v) for v in pool.values()):,} works; cuts {cuts} (recomputed on the v4 "
        f"neighbour arm they would be {cuts_if_recomputed}); quotas changed for "
        f"{sum(1 for p in order if quota3[p] != quota4[p])} study papers; candidate pools changed for "
        f"{sum(1 for p in order if stats3[p] != stats4[p])}; dry_run={dry_run}")
    unknown: Counter = Counter()

    def klass_memo(wid: str) -> str | None:
        w = works3.get(wid) or works[wid]
        if w.pmcid:
            m = b.pmc.get(w.pmcid)
            if m is None or (m.get("in_bucket") and m.get("is_pmc_openaccess")):
                return "jats"
        return "pdf" if w.pdf_locations else None

    def memo(wid: str, screened: bool) -> ac.Outcome:
        w = works3.get(wid) or works[wid]
        rec = b.done.get(wid)
        if rec is None or b._stale(w, rec):
            unknown["outcome"] += 1
            return ac.Outcome(ok=False, reason="dry_run_unknown")
        return rep.outcome(rec) if screened else outcome_of(rec)

    def n_chunks(wid: str) -> int:
        return int(b.done[wid]["n_chunks"])

    # ---- A: replay v3 (v3 rules, memoised outcomes) for the walks' fetched sets; must equal the manifest
    def replay_v3(d: int) -> tuple[dict[str, dict[str, ac.Cell]], int]:
        orders = {p: ac.random_order(d, p, cands3[p], seeds=ac.SEED_RANDOM_V2) for p in order}
        carry = {p: ac.carry_over(v2[d]["per_study"][p]["took"], quota3[p], band, n_chunks) for p in order}
        taken: set[str] = set()
        ident = ac.Exclusion()
        for p in order:
            for t in carry[p]["kept"]:
                taken.add(t["wid"])
                ident.add(ac.Work(wid=t["wid"], pmcid=works3[t["wid"]].pmcid, doi=works3[t["wid"]].doi))
        fetched: dict[str, dict[str, ac.Cell]] = {}
        bad = 0
        for p in order:
            c = carry[p]
            new: list[dict] = []
            if c["open"]:
                f: dict[str, tuple[ac.Cell, str]] = {}
                r = ac.draw_matched(
                    orders[p], c["open"], taken, type_of=lambda wid: works3[wid].type, klass=klass_memo,
                    resolve=lambda wid: memo(wid, False), band=band,
                    is_dup=lambda wid: ident.reason(ac.Work(wid="", pmcid=works3[wid].pmcid, doi=works3[wid].doi)) is not None,
                    on_take=lambda wid: ident.add(ac.Work(wid=wid, pmcid=works3[wid].pmcid, doi=works3[wid].doi)),
                    max_resolves=max(40, 15 * sum(quota3[p].values())), fetched_out=f)
                new = r["took"]
                fetched[p] = {w: cell for w, (cell, lv) in f.items() if lv not in ac.BAND_RELAXED}
            if [_take_key(t) for t in c["kept"] + new] != [_take_key(t) for t in v3[d]["per_study"][p]["took"]]:
                bad += 1
        return fetched, bad

    # ---- B + C: carry over the v3 takes, re-draw the open slots
    def run_v4(d: int, fetched3: dict[str, dict[str, ac.Cell]]) -> dict:
        orders = {p: ac.random_order(d, p, cands4[p], seeds=ac.SEED_RANDOM_V2) for p in order}
        carry = {p: v4_carry(v3[d]["per_study"][p]["took"], cand4_sets[p], quota4[p], rep.excluded) for p in order}
        taken: set[str] = set()
        ident = ac.Exclusion()

        def is_dup(wid: str) -> bool:
            return ident.reason(ac.Work(wid="", pmcid=works[wid].pmcid, doi=works[wid].doi)) is not None

        def on_take(wid: str) -> None:
            ident.add(ac.Work(wid=wid, pmcid=works[wid].pmcid, doi=works[wid].doi))

        for p in order:
            for t in carry[p]["kept"]:
                taken.add(t["wid"])
                on_take(t["wid"])

        def resolve(wid: str) -> ac.Outcome:
            if dry_run:
                return memo(wid, True)
            return rep.outcome(b.resolve(works[wid]))

        def klass(wid: str) -> str | None:
            return klass_memo(wid) if dry_run else b.klass_async(works[wid]).result()

        per: dict[str, dict] = {}
        for p in order:
            per[p] = v4_study_redraw(
                carry[p], quota3[p], quota4[p], orders[p], fetched3.get(p, {}), taken, excluded=rep.excluded,
                is_dup=is_dup, on_take=on_take, type_of=lambda wid: works[wid].type, klass=klass, resolve=resolve,
                band=band)
        return per

    lines0 = _count_lines(b.out_path)
    results: dict[int, dict] = {}
    replay_bad: dict[int, int] = {}
    for d in (1, 2):
        fetched3, replay_bad[d] = replay_v3(d)
        if replay_bad[d]:
            raise SystemExit(f"v4: replaying v3 draw {d} differs from its manifest for {replay_bad[d]} study papers")
        log(f"random-{d}-v4: v3 replay reproduces the manifest take for take ({len(fetched3)} walks)")
        results[d] = run_v4(d, fetched3)
    new_fetches = _count_lines(b.out_path) - lines0
    qtot: Counter = Counter()
    for p in order:
        qtot.update(quota4[p])
    for d in (1, 2):
        per = results[d]
        took_all = [t for r in per.values() for t in r["took"]]
        lv: Counter = Counter(t["level"] for t in took_all)
        why: Counter = Counter(t["why"] for r in per.values() for t in r["released"])
        ss: Counter = Counter()
        for r in per.values():
            ss.update(r["strict_shortfall"])
        dist = Counter(t["band_distance"] for t in took_all if t["level"] == "any")
        rep_skips = sum(r["skipped"].get("repetition_screen", 0) for r in per.values())
        summary = {
            "slots_total": sum(r["need"] for r in per.values()), "kept": sum(r["kept"] for r in per.values()),
            "redrawn_slots": sum(r["redrawn_slots"] for r in per.values()), "released_by_reason": dict(why),
            "redrawn_got": sum(r["redrawn_got"] for r in per.values()),
            "studies_with_redraw": sum(1 for r in per.values() if r["redrawn_slots"]),
            "studies_by_mode": dict(Counter(r["mode"] for r in per.values() if r["mode"])),
            "redraw_resolves": sum(r["resolves"] for r in per.values()),
            "redraw_budget_hit_studies": sum(1 for r in per.values() if r["budget_hit"]),
            "shortfall_total": sum(r["shortfall"] for r in per.values()),
            "match_level": dict(lv), "strict_share": round(lv["strict"] / max(1, sum(lv.values())), 4),
            "any_band_distance": {str(k): v for k, v in sorted(dist.items())},
            "repetition_screen": {"v3_takes_released": why.get("repetition_screen", 0),
                                  "redraw_walk_candidates_skipped": rep_skips},
            "strict_shortfall_per_cell": {k: {"quota": qtot[tuple(_cell_of(k))], "short": n} for k, n in sorted(ss.items())},
            "redraw_skipped": dict(sum((Counter(r["skipped"]) for r in per.values()), Counter())),
        }
        if dry_run:
            summary["dry_run_unknown_outcomes"] = unknown["outcome"]
        log(f"random-{d}-v4 summary: " + json.dumps({k: v for k, v in summary.items() if k != "strict_shortfall_per_cell"}))
        doc = {
            "schema": "alias-corpus-random-draw/4", "version": "v4", "draw": d, "seed": ac.SEED_RANDOM_V2[d],
            "oa_definition": ac.OA_DEFINITION, "type_filter": V2_SCHEMA_TYPE,
            "matching": {"cells": "(type class, length band, source) per study paper, from its attributed neighbours (v4 arm)",
                         "length_bands": "v3's bands, fixed: <20/20-26/27-33/34-45/46-79/>=80", "cuts": cuts,
                         "cuts_if_recomputed_on_the_v4_neighbour_arm": cuts_if_recomputed,
                         "relax_order": list(ac.RELAX_ORDER), "relax_rule": "source first, then type, then band last ('any')",
                         "any_rule": V4_NEAREST_RULE, "screen_rule": V4_SCREEN_RULE,
                         "max_resolves": V4_BUDGET_RULE, "redraw_rule": V4_REDRAW_RULE},
            "summary": summary, "new_network_fetches_both_draws": None if dry_run else new_fetches,
            "v3_replay_mismatched_studies": replay_bad[d],
            "order": order, "quota": {p: {"|".join(map(str, c)): n for c, n in sorted(quota4[p].items())} for p in order},
            "quota_changed_studies": [p for p in order if quota3[p] != quota4[p]],
            "per_study": per, "candidates_per_study": stats4,
            "works": {t["wid"]: _work_json(works[t["wid"]]) for p in per for t in per[p]["took"]},
            "provenance": LAUNCH_PROVENANCE, "finished": now()}
        target = (lay.tmp / f"random-{d}-draw-v4.dryrun.json") if dry_run else (lay.manifests / f"random-{d}-draw-v4.json")
        write_json(target, doc)
    log(f"random-v4: both draws written (new network fetches {new_fetches})")
    return {d: sum(r["got"] for r in results[d].values()) for d in (1, 2)}


def _per_band(b: Builder, items: list[str], cuts: list[float]) -> dict[str, dict[str, int]]:
    out = {str(k): {"papers": 0, "chunks": 0} for k in range(len(cuts) + 1)}
    for wid in items:
        n = int(b.done[wid]["n_chunks"])
        o = out[str(ac.band_of(n, cuts))]
        o["papers"] += 1
        o["chunks"] += n
    return out


def stage_finalize_v4(lay: Layout, b: Builder, rep: Repetition | None = None) -> dict:
    rep = rep or Repetition(lay, b)
    sel4 = _sel_v4(lay)
    sel2 = load_sel_v2(lay)
    counts = stage_finalize_v2(lay, b, version=4, sel=sel4)
    draws = {d: json.load(open(lay.manifests / f"random-{d}-draw-v4.json")) for d in (1, 2)}
    draws3 = {d: json.load(open(lay.manifests / f"random-{d}-draw-v3.json")) for d in (1, 2)}
    c3 = json.load(open(lay.manifests / "counts-v3.json"))
    cuts = draws[1]["matching"]["cuts"]
    arms = {"neighbour": [w for p in sel4["order"] for w, _ in sel4["attributed"][p]]}
    arms3 = {"neighbour": [w for p in sel2["order"] for w, _ in sel2["attributed"][p]]}
    for d in (1, 2):
        arms[f"random-{d}"] = [t["wid"] for p in draws[d]["order"] for t in draws[d]["per_study"][p]["took"]]
        arms3[f"random-{d}"] = [t["wid"] for p in draws3[d]["order"] for t in draws3[d]["per_study"][p]["took"]]
    nb = len(cuts) + 1
    counts["band_labels"] = [f"<{cuts[0]:g}"] + [f"{cuts[i - 1]:g}-{cuts[i] - 1:g}" for i in range(1, len(cuts))] + [f">={cuts[-1]:g}"]
    counts["per_band"] = {a: _per_band(b, v, cuts) for a, v in arms.items()}
    top = str(nb - 1)
    counts["top_band_papers_matched"] = all(counts["per_band"][f"random-{d}"][top]["papers"] == counts["per_band"]["neighbour"][top]["papers"]
                                            for d in (1, 2))
    cap = 500

    def capped(items: list[str]) -> int:
        return sum(min(cap, int(b.done[w]["n_chunks"])) for w in items)

    def vs(x: int, y: int) -> float:
        return round(x / y - 1, 4)

    papers_chunks = {a: sum(int(b.done[w]["n_chunks"]) for w in v) for a, v in arms.items()}
    capn = {a: capped(v) for a, v in arms.items()}
    counts["chunks_per_paper_cap"] = {
        "cap": cap, "note": "chunks per paper summed over the arm's papers (no chunk-id dedupe), uncapped and capped",
        "uncapped": papers_chunks, "capped": capn,
        "vs_neighbour_uncapped": {a: vs(papers_chunks[a], papers_chunks["neighbour"]) for a in ("random-1", "random-2")},
        "vs_neighbour_capped": {a: vs(capn[a], capn["neighbour"]) for a in ("random-1", "random-2")},
        "papers_above_cap": {a: sum(1 for w in v if int(b.done[w]["n_chunks"]) > cap) for a, v in arms.items()}}
    # the repetition screen, every arm: ratios below 0.95 (counts only), before (v3 arms) and after
    def low(items: list[str]) -> list[list]:
        return [list(x) for x in sorted((round(rep.ratio(w) or 1.0, 4), int(b.done[w]["n_chunks"]), b.done[w]["source"])
                                        for w in items) if x[0] < 0.95]
    ch = sel4["v4_changes"]
    counts["repetition_screen"] = {
        "rule": V4_SCREEN_RULE, "threshold": ac.REPETITION_THRESHOLD,
        "v3_arm_ratios_below_0.95": {a: low(v) for a, v in arms3.items()},
        "v4_arm_ratios_below_0.95": {a: low(v) for a, v in arms.items()},
        "v3_arm_papers_excluded": {a: sum(1 for w in v if rep.excluded(w)) for a, v in arms3.items()},
        "v4_arm_papers_excluded": {a: sum(1 for w in v if rep.excluded(w)) for a, v in arms.items()},
        "neighbour": {"v2_attributed_screened_out": len(ch["v2_attributed_screened_out"]),
                      "studies_changed": len(ch["studies_changed"]),
                      "studies_changed_without_a_screened_neighbour": len(ch["studies_changed_without_a_screened_neighbour"]),
                      "walk_skips_repetition": sum(1 for p in sel4["skips"] for lst in sel4["skips"][p].values()
                                                   for _, x in lst if x == "repetition_screen")},
        **{f"random-{d}": draws[d]["summary"]["repetition_screen"] for d in (1, 2)}}
    for d in (1, 2):
        D = draws[d]["summary"]
        counts[f"random-{d}"].update({"v4_redraw": D, "new_network_fetches_both_draws": draws[d]["new_network_fetches_both_draws"],
                                      "any_band_distance": D["any_band_distance"], "strict_share": D["strict_share"],
                                      "quota_changed_studies": len(draws[d]["quota_changed_studies"])})
    counts["neighbour"]["v4_changes"] = {"studies_changed": len(ch["studies_changed"]),
                                         "new_outcomes_neighbour_stage": ch["new_outcomes_this_stage"]}
    counts["vs_v3"] = {a: {"v3_chunks": c3[a]["chunks"], "v4_chunks": counts[a]["chunks"],
                           "v3_vs_neighbour": c3[a].get("chunks_vs_neighbour"), "v4_vs_neighbour": counts[a].get("chunks_vs_neighbour"),
                           "v3_band_counts": c3[a]["band_counts"], "v4_band_counts": counts[a]["band_counts"],
                           "v3_pdf_share": c3[a]["pdf_share"], "v4_pdf_share": counts[a]["pdf_share"],
                           "v3_type_mix": c3[a]["type_mix"], "v4_type_mix": counts[a]["type_mix"],
                           "papers_added": len(set(arms[a]) - set(arms3[a])), "papers_removed": len(set(arms3[a]) - set(arms[a]))}
                       for a in arms}
    counts["rules"] = {"any": V4_NEAREST_RULE, "screen": V4_SCREEN_RULE, "redraw": V4_REDRAW_RULE, "budget": V4_BUDGET_RULE}
    counts["relax_rule"] = "source first, then type, then band last; each take records its level"
    st = json.loads((lay.state / "provenance-v4.json").read_text()) if (lay.state / "provenance-v4.json").exists() else {}
    commits = {k: v.get("commit") for k, v in st.items()}
    commits["finalize-v4"] = (LAUNCH_PROVENANCE or {}).get("commit")
    counts["stage_provenance_commits"] = commits
    counts["stage_provenance_authority"] = ("state/provenance-v4.json (full provenance per stage, written when each "
                                            "stage's done marker is written); this map repeats its commits")
    counts["stage_commits_agree"] = len(set(commits.values())) <= 1
    write_json(lay.manifests / "counts-v4.json", counts)
    log("finalize-v4: " + json.dumps({a: counts["vs_v3"][a] for a in arms}) + " cap: " + json.dumps(counts["chunks_per_paper_cap"]))
    return counts


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
                                      "finalize", "chunkcheck", "pdf-extract", "run-v2", "chunkcheck-v2",
                                      "neighbours-v2", "random-v2", "finalize-v2", "run-v3", "chunkcheck-v3",
                                      "random-v3", "finalize-v3", "run-v4", "chunkcheck-v4", "neighbours-v4",
                                      "random-v4", "finalize-v4"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--seed-cache", nargs=2, metavar=("CACHE_JSON", "QUERY_LOG"), default=None,
                    help="seed the OpenAlex URL cache from an earlier run of the same queries")
    ap.add_argument("--scan-workers", type=int, default=32)
    ap.add_argument("--fetch-workers", type=int, default=16)
    ap.add_argument("--dry-run", action="store_true",
                    help="random-v3, neighbours-v4, random-v4: memoised outcomes only, no fetch; writes "
                         "tmp/*.dryrun.json, no manifest")
    a = ap.parse_args(argv)
    if a.stage == "pdf-extract":
        sys.path.insert(0, str(_PY / "scripts"))
        return pdf_extract_main(*a.args)
    os.umask(0o077)
    os.environ.setdefault("HF_HOME", "/rag/cache")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    lay = Layout(a.root)
    global LAUNCH_PROVENANCE
    LAUNCH_PROVENANCE = _provenance()
    log(f"alias_corpus_run {a.stage}: root {lay.root}; python {sys.executable}; launch commit "
        f"{LAUNCH_PROVENANCE.get('commit')} dirty={LAUNCH_PROVENANCE.get('dirty')}")
    api = OpenAlex(lay)
    api.read_only = a.dry_run
    if a.seed_cache:
        log(f"openalex: seeded {api.seed(*a.seed_cache)} cached responses")
    v2 = a.stage.endswith(("-v2", "-v3", "-v4"))
    v3 = a.stage.endswith("-v3")
    v4 = a.stage.endswith("-v4")
    if a.stage == "run":
        stages = ["study", "neighbours", "scan", "chunkcheck", "neighbours-fetch", "random", "finalize"]
    elif a.stage == "run-v2":
        stages = ["chunkcheck-v2", "neighbours-v2", "random-v2", "finalize-v2"]
    elif a.stage == "run-v3":
        stages = ["chunkcheck-v3", "random-v3", "finalize-v3"]
    elif a.stage == "run-v4":
        stages = ["chunkcheck-v4", "neighbours-v4", "random-v4", "finalize-v4"]
    else:
        stages = [a.stage]
    if v2:
        for need in ("study", "neighbours", "scan"):
            if not (lay.state / f"done-{need}").exists():
                raise SystemExit(f"v2 needs the v1 stage {need} done")
    if v3:
        for need in ("neighbours-v2", "random-v2", "finalize-v2"):
            if not (lay.state / f"done-{need}").exists():
                raise SystemExit(f"v3 needs the v2 stage {need} done")
    if v4:
        for need in ("random-v3", "finalize-v3"):
            if not (lay.state / f"done-{need}").exists():
                raise SystemExit(f"v4 needs the v3 stage {need} done")
    if a.dry_run and a.stage not in ("random-v3", "neighbours-v4", "random-v4"):
        raise SystemExit("--dry-run is for the random-v3, neighbours-v4 and random-v4 stages only")
    b: Builder | None = None
    rep: Repetition | None = None
    for st in stages:
        marker = lay.state / f"done-{st}"
        if a.stage in ("run", "run-v2", "run-v3", "run-v4") and marker.exists():
            log(f"{st}: already done")
            continue
        if st.endswith("-v4"):
            b = b or Builder(lay, a.fetch_workers, version=4)
            rep = rep or Repetition(lay, b)
            if st == "chunkcheck-v4":
                stage_chunkcheck(lay, b, n=5, out_name="chunkcheck-v4.json")
            elif st == "neighbours-v4":
                stage_neighbours_v4(lay, api, b, dry_run=a.dry_run, rep=rep)
            elif st == "random-v4":
                stage_random_v4(lay, b, dry_run=a.dry_run, rep=rep)
            else:
                stage_finalize_v4(lay, b, rep=rep)
            if a.dry_run:
                log(f"{st} dry run: no marker, no manifest")
                continue
        elif st == "chunkcheck-v3":
            b = b or Builder(lay, a.fetch_workers, version=3)
            stage_chunkcheck(lay, b, n=5, out_name="chunkcheck-v3.json")
        elif st == "random-v3":
            b = b or Builder(lay, a.fetch_workers, version=3)
            stage_random_v3(lay, b, dry_run=a.dry_run)
            if a.dry_run:
                log("random-v3 dry run: no marker, no manifest")
                continue
        elif st == "finalize-v3":
            b = b or Builder(lay, a.fetch_workers, version=3)
            stage_finalize_v3(lay, b)
        elif st == "chunkcheck-v2":
            b = b or Builder(lay, a.fetch_workers, v2=True)
            stage_chunkcheck(lay, b, n=5, out_name="chunkcheck-v2.json")
        elif st == "neighbours-v2":
            b = b or Builder(lay, a.fetch_workers, v2=True)
            stage_neighbours_v2(lay, api, b)
        elif st == "random-v2":
            b = b or Builder(lay, a.fetch_workers, v2=True)
            stage_random_v2(lay, b)
        elif st == "finalize-v2":
            b = b or Builder(lay, a.fetch_workers, v2=True)
            stage_finalize_v2(lay, b)
        elif st == "study":
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
        if v3 or v4:
            pp = lay.state / f"provenance-v{4 if v4 else 3}.json"
            prov = json.loads(pp.read_text()) if pp.exists() else {}
            prov[st] = {**(LAUNCH_PROVENANCE or {}), "stage_done": now()}
            write_json(pp, prov)
    api.save_if_dirty()   # written only when a response was added; never in a dry run (read_only)
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
