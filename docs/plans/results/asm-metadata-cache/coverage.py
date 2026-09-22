#!/usr/bin/env python3
"""Final coverage roll-up: how many of the ~440k ASM documents the cache can now
give a title / pmcid / pmid / journal, and what is left."""
import json, collections, sys

C = "/rag/data/asm-metadata-cache"


def load_keyed(path, keep):
    d = {}
    try:
        f = open(path)
    except FileNotFoundError:
        return d
    for l in f:
        try:
            r = json.loads(l)
        except Exception:
            continue
        cur = d.get(r["doi"])
        if cur and cur.get("found") and not r.get("found"):
            continue  # never let a later negative overwrite a positive
        d[r["doi"]] = {k: r.get(k) for k in keep}
    return d


cr = load_keyed(f"{C}/crossref.jsonl",
                ["found", "title", "authors", "container_title", "short_container_title",
                 "publisher", "type", "year", "dates", "issn", "volume", "issue", "page",
                 "abstract", "subject"])
nc = load_keyed(f"{C}/ncbi-idconv.jsonl", ["found", "pmcid", "pmid"])
print(f"crossref cache: {len(cr)} DOIs   ncbi cache: {len(nc)} DOIs")

# parent DOIs for the no-DOI documents
parent = {}
try:
    for l in open(f"{C}/state/nodoi-parent-doi.jsonl"):
        r = json.loads(l)
        parent[r["doc_id"]] = r["parent_doi"]
except FileNotFoundError:
    pass

docs = [json.loads(l) for l in open(f"{C}/doc-index.jsonl")]
print(f"documents: {len(docs)}")

agg = collections.Counter()
by_key = collections.Counter()
for d in docs:
    doi = (d["doi"] or "").lower()
    key = doi
    route = "direct-doi"
    if not doi:
        key = parent.get(d["doc_id"], "")
        route = "parent-doi" if key else "none"
    c = cr.get(key) or {}
    n = nc.get(key) or {}
    agg["docs"] += 1
    by_key[route] += 1
    if d["has_title"]:
        agg["title_before"] += 1
    if c.get("found") and c.get("title"):
        agg["title_from_crossref"] += 1
        if not d["has_title"]:
            agg["title_newly_recoverable"] += 1
    if c.get("found") and c.get("container_title"):
        agg["journal_from_crossref"] += 1
    if c.get("found") and c.get("authors"):
        agg["authors_from_crossref"] += 1
    if c.get("found") and c.get("publisher"):
        agg["publisher_from_crossref"] += 1
    if c.get("found") and c.get("dates"):
        agg["dates_from_crossref"] += 1
    if c.get("found") and c.get("abstract"):
        agg["abstract_from_crossref"] += 1
    if n.get("pmcid"):
        agg["pmcid"] += 1
    if n.get("pmid"):
        agg["pmid"] += 1
    if not (c.get("found") or n.get("pmcid")):
        agg["no_record_at_all"] += 1
        agg["no_record_" + (d["doc_type"] or "?")] += 1

n = agg["docs"]
print("\n== document-level coverage (of every ASM doc_id in all three collections) ==")
for k in ["title_before", "title_from_crossref", "title_newly_recoverable",
          "journal_from_crossref", "authors_from_crossref", "publisher_from_crossref",
          "dates_from_crossref", "abstract_from_crossref", "pmcid", "pmid",
          "no_record_at_all"]:
    print(f"  {k:26s} {agg[k]:7d}  {agg[k]/n:6.1%}")
print("\n  join route:", dict(by_key))
print("  unrecoverable by doc_type:",
      {k[10:]: v for k, v in agg.items() if k.startswith("no_record_")})

# DOI-level
found = sum(1 for v in cr.values() if v.get("found"))
print(f"\n== DOI-level ==\n  crossref found {found}/{len(cr)} = {found/max(len(cr),1):.1%}")
pm = sum(1 for v in nc.values() if v.get("pmcid"))
print(f"  ncbi pmcid     {pm}/{len(nc)} = {pm/max(len(nc),1):.1%}")

# date richness — how many carry a full y-m-d, y-m, y only
rich = collections.Counter()
for v in cr.values():
    if not v.get("found"):
        continue
    best = 0
    for dk, dv in (v.get("dates") or {}).items():
        dp = (dv or {}).get("date-parts") or [[]]
        best = max(best, len(dp[0]))
    rich[best] += 1
print("  crossref date granularity (parts: 0=none,1=year,2=y-m,3=y-m-d):", dict(rich))
