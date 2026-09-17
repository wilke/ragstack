#!/usr/bin/env python3
"""Backfill scholarly metadata onto a user-uploaded collection, keyed on DOI.

**REPAIR ONLY since #596.** Ingest now does this itself, at upload time, on both
backends — ``ragstack.ingestion.doi_metadata`` resolves each distinct DOI between
load and chunk, so the fields ride on every chunk from the start. What is left
for this script is collections built BEFORE that landed, and the rare collection
whose lookups all failed during an outage. A collection ingested after #596 that
is still bare has a cause worth finding first; see
``docs/runbooks/tenant-admin.md`` § 6c.

Two things this script gets subtly wrong that the ingest-path implementation does
not — both found while porting the logic across. They are left here because this
is now a rarely-run repair whose output an operator reads directly, but do not
copy them:

* it keys ID-Converter records on ``rec["doi"]``, which is the DOI in the
  PUBLISHER's case — so the record for ``10.1128/JVI.02415-06`` never matches the
  lowercase DOI it was asked about, and pmid/pmcid are silently dropped for every
  uppercase-suffix publisher (most of ASM). ``rec["requested-id"]`` is the key
  that echoes what was sent.
* it writes the ID Converter's ``pmid`` through unchanged, and that field is a
  JSON *integer* — while ``ingestion.jats`` keeps pmid/pmcid as strings on
  purpose, so a corpus repaired here and a corpus ingested from JATS disagree
  about the field's type.

A PDF uploaded through the API arrives with whatever the loader could scrape
out of the file — in practice a DOI lifted from the text, plus year and
document class. Title, authors, journal and the PubMed ids are simply absent,
because ``ingestion.enrich`` is deliberately pure: it recovers metadata from
the filename and the body text and never makes a network call. This script is
the network half, run after the fact.

It resolves each DISTINCT DOI once (not once per chunk) against Crossref for
title/authors/journal/year and the NCBI ID Converter for pmid/pmcid, then
stamps the result onto every chunk of that document in BOTH stores.

Both stores, and they are not the same shape:

* Elasticsearch nests these under ``metadata.*``
* Qdrant keeps them FLAT at the payload top level

Writing one shape to both leaves the text leg and the vector leg disagreeing
about the same chunk, which no count-based parity check will notice.

Dry-run by default::

    python3 scripts/backfill_collection_metadata.py \\
        --es http://127.0.0.1:24083 --qdrant http://127.0.0.1:24081 \\
        --index ragstack_lib_dengue_..._2127eaf9

Scope: this is for COLLECTIONS BUILT FROM USER UPLOADS. A corpus ingested from
JATS already carries these fields (the PMC open-access build is at 100%), and a
collection whose stores are ROUTED to a shared instance must never be written
to from a tenant — the guard below refuses both cases unless forced.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CROSSREF = "https://api.crossref.org/works/"
IDCONV = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
FIELDS = ("title", "authors", "journal", "pmid", "pmcid")


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "ragstack-backfill/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url, body, timeout=120):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # A bare "HTTP Error 400: Bad Request" tells the operator nothing about
        # WHICH store refused and why; both of these speak JSON on the error path.
        detail = e.read().decode("utf-8", "replace")[:800]
        raise SystemExit(f"\n{e.code} from {url}\n{detail}") from None


def distinct_dois(es, index):
    body = {"size": 0, "aggs": {"d": {"terms": {"field": "metadata.doi", "size": 10000}}}}
    agg = _post(f"{es}/{index}/_search", body)
    return [(b["key"], b["doc_count"]) for b in agg["aggregations"]["d"]["buckets"]]


def coverage(es, index, field):
    body = {"query": {"exists": {"field": f"metadata.{field}"}}}
    return _post(f"{es}/{index}/_count", body)["count"]


def resolve(dois, email, sleep=0.2):
    """One Crossref call per DOI; one batched ID-Converter call for all of them."""
    out = {d: {} for d in dois}
    ids = _get(IDCONV + "?" + urllib.parse.urlencode(
        {"ids": ",".join(dois), "format": "json", "tool": "ragstack", "email": email}))
    for rec in ids.get("records", []):
        # Key on requested-id, NOT doi. The converter echoes what you SENT in
        # "requested-id" and returns the publisher's canonical casing in "doi":
        # ask for 10.1128/jvi.02415-06 and "doi" comes back 10.1128/JVI.02415-06.
        # Keying on "doi" therefore drops pmid/pmcid for every DOI stored in a
        # case other than canonical — most of ASM — silently and per-record.
        doi = rec.get("requested-id") or rec.get("doi")
        if doi in out:
            # pmid arrives as a JSON int; ingestion/jats.py keeps pmid/pmcid as
            # strings, so coerce or a repaired collection disagrees with a
            # JATS-ingested one about the field's type.
            if rec.get("pmid"):
                out[doi]["pmid"] = str(rec["pmid"])
            if rec.get("pmcid"):
                out[doi]["pmcid"] = str(rec["pmcid"])
    for doi in dois:
        try:
            m = _get(CROSSREF + urllib.parse.quote(doi) + "?" + urllib.parse.urlencode({"mailto": email}))["message"]
        except Exception as e:                                    # noqa: BLE001
            print(f"  !! crossref failed for {doi}: {type(e).__name__}")
            continue
        if m.get("title"):
            out[doi]["title"] = m["title"][0]
        authors = [", ".join(x for x in (a.get("family"), a.get("given")) if x) for a in m.get("author", [])]
        if authors:
            out[doi]["authors"] = authors
        ct = m.get("container-title") or []
        if ct:
            out[doi]["journal"] = ct[0]
        time.sleep(sleep)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--es", required=True)
    ap.add_argument("--qdrant", required=True)
    ap.add_argument("--index", required=True, help="physical store name (same in both)")
    ap.add_argument("--email", default="wilke@anl.gov", help="contact for Crossref/NCBI politeness")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true", help="override the already-populated guard")
    a = ap.parse_args()

    total = _post(f"{a.es}/{a.index}/_count", {"query": {"match_all": {}}})["count"]
    have_title = coverage(a.es, a.index, "title")
    print(f"index {a.index}")
    print(f"  chunks {total}, title already on {have_title} ({100*have_title//max(total,1)}%)")
    if have_title > total * 0.5 and not a.force:
        sys.exit("refusing: this collection already has titles — it is not an upload-built "
                 "collection. Use --force only if you know why.")

    dois = distinct_dois(a.es, a.index)
    print(f"  {len(dois)} distinct DOI(s) covering {sum(n for _, n in dois)} chunks\n")
    if not dois:
        sys.exit("no DOIs — nothing this script can key on")

    resolved = resolve([d for d, _ in dois], a.email)
    for doi, n in dois:
        r = resolved.get(doi) or {}
        print(f"  {doi}  ({n} chunks)")
        for f in FIELDS:
            v = r.get(f)
            v = ("; ".join(v[:3]) + (" …" if len(v) > 3 else "")) if isinstance(v, list) else v
            print(f"      {f:8} {v if v else '— unresolved'}")
        print()

    if not a.apply:
        print("DRY RUN — nothing written. Re-run with --apply.")
        return

    for doi, _n in dois:
        r = {k: v for k, v in (resolved.get(doi) or {}).items() if v}
        if not r:
            print(f"  skip {doi}: nothing resolved")
            continue
        # Elasticsearch: nested under metadata.*, only filling what is absent.
        # Each fragment already ends in ";" — joining on "; " produces ";;", which
        # painless rejects as an empty statement.
        src = " ".join(f"if (ctx._source.metadata.{k} == null) ctx._source.metadata.{k} = params.{k};" for k in r)
        es_body = {"query": {"term": {"metadata.doi": doi}},
                   "script": {"source": src + " ctx._source.metadata.metadata_source = 'crossref+idconv';",
                              "params": r, "lang": "painless"}}
        got = _post(f"{a.es}/{a.index}/_update_by_query?refresh=true&conflicts=proceed", es_body)
        # Qdrant: FLAT at the payload top level.
        qd_body = {"payload": dict(r, metadata_source="crossref+idconv"),
                   "filter": {"must": [{"key": "doi", "match": {"value": doi}}]}}
        _post(f"{a.qdrant}/collections/{a.index}/points/payload?wait=true", qd_body)
        print(f"  {doi}: es updated {got.get('updated')} / qdrant payload set on doi match")

    print("\ndone. Re-run without --apply to see the new coverage.")


if __name__ == "__main__":
    main()
