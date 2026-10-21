"""Duplicate-work report: which documents in a collection are the same paper (#747).

The ingest guard (``ragstack.ingestion.work_identity``) stops NEW copies; this
report finds the copies already indexed — the backlog the guard cannot see
into. For each Elasticsearch index it lists every document's work identity
(DOI, PMCID, PMID — normalised, so the malformed ``…/-/`` DOI form joins the
canonical one) and groups documents that share ANY key, transitively: a copy
carrying only a DOI and a copy carrying the same DOI plus a PMCID land in one
group with a third copy carrying only that PMCID.

**Read-only.** The only requests are ``GET /_cat/indices`` and ``POST
/<index>/_search`` with ``size=0`` and a composite ``doc_id`` aggregation (one
exemplar chunk per document) — O(#docs), paged. Nothing is written, and it
exits 0 whatever it finds: deciding which copy to keep is an owner's call
(``DELETE /v1/documents/{id}``), not this tool's.

A document whose stored metadata lacks an id gets it from its file name where
the name carries one (``PMC1234567.md``) — the guard's own ``fill_ids`` rule.
Documents that carry no id at all cannot be grouped; they are COUNTED per
index, not listed — a large count is the signal that the guard is blind for
that collection until its documents are enriched.

    cd python
    python scripts/duplicate_report.py --es-url http://localhost:24043 \\
        --index 'ragstack_lib_openchia_*'
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from ragstack.ingestion.work_identity import ids_from_filename, work_keys
from ragstack.ops.metadata_conformance import resolve_targets

log = logging.getLogger(__name__)

#: The metadata an exemplar chunk is asked for — enough to identify and name a
#: document, never its text.
_SOURCE_FIELDS = [
    "doc_id",
    "metadata.doi",
    "metadata.pmcid",
    "metadata.pmid",
    "metadata.filename",
    "metadata.source_path",
    "metadata.title",
    "metadata.tenant_id",
    "metadata.duplicate_of",
]
PAGE_SIZE = 500


@dataclass
class DocEntry:
    doc_id: str
    chunk_count: int
    keys: list[str]
    filename: str = ""
    source_path: str = ""
    title: str = ""
    tenant_id: str = ""
    duplicate_of: str = ""


@dataclass
class DuplicateGroup:
    """Documents of one index that share at least one work key."""

    index: str
    keys: list[str]
    docs: list[DocEntry] = field(default_factory=list)


@dataclass
class IndexDuplicates:
    es_url: str
    index: str
    n_docs: int = 0
    n_without_ids: int = 0
    groups: list[DuplicateGroup] = field(default_factory=list)
    error: str = ""


def _doc_entry(doc_id: str, count: int, meta: dict[str, Any]) -> DocEntry:
    # Ids the stored metadata lacks are filled from the file name, exactly as
    # the guard's ``fill_ids`` would at ingest (a Europe PMC ``PMC8092463.md``
    # indexed before #747 carries no id field at all).
    name = str(meta.get("filename") or meta.get("source_path") or "")
    ids = {**ids_from_filename(name), **{k: v for k, v in meta.items() if v}}
    return DocEntry(
        doc_id=doc_id,
        chunk_count=count,
        keys=[f"{k}:{v}" for k, v in work_keys(ids)],
        filename=str(meta.get("filename") or ""),
        source_path=str(meta.get("source_path") or ""),
        title=str(meta.get("title") or "")[:200],
        tenant_id=str(meta.get("tenant_id") or ""),
        duplicate_of=str(meta.get("duplicate_of") or ""),
    )


def group_documents(index: str, docs: Iterable[DocEntry]) -> tuple[list[DuplicateGroup], int]:
    """Group ``docs`` that share any key (union-find over keys). Returns
    ``(groups with two or more documents, number of documents with no key)``.
    Pure — the unit-tested half of the report."""
    docs = list(docs)
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    no_ids = 0
    for d in docs:
        if not d.keys:
            no_ids += 1
            continue
        for k in d.keys:
            parent.setdefault(k, k)
        for k in d.keys[1:]:
            union(d.keys[0], k)
    by_root: dict[str, list[DocEntry]] = {}
    for d in docs:
        if d.keys:
            by_root.setdefault(find(d.keys[0]), []).append(d)
    groups = []
    for members in by_root.values():
        if len(members) < 2:
            continue
        keys = sorted({k for m in members for k in m.keys})
        groups.append(DuplicateGroup(
            index=index, keys=keys,
            docs=sorted(members, key=lambda m: (-m.chunk_count, m.doc_id)),
        ))
    groups.sort(key=lambda g: (-len(g.docs), g.keys))
    return groups, no_ids


def scan_index(
    es_url: str,
    index: str,
    *,
    tenants: list[str] | None = None,
    api_key: str = "",
    client: httpx.Client | None = None,
) -> IndexDuplicates:
    """Page every document of ``index`` (composite ``doc_id`` agg + one
    exemplar each) and group them. A failure is recorded, not raised."""
    report = IndexDuplicates(es_url=es_url, index=index)
    owns = client is None
    c = client or httpx.Client(timeout=60.0)
    headers = {"Authorization": f"ApiKey {api_key}"} if api_key else {}
    query: dict[str, Any] = (
        {"bool": {"filter": [{"terms": {"metadata.tenant_id": list(tenants)}}]}}
        if tenants else {"match_all": {}}
    )
    entries: list[DocEntry] = []
    after: dict[str, Any] | None = None
    try:
        while True:
            composite: dict[str, Any] = {
                "size": PAGE_SIZE,
                "sources": [{"doc_id": {"terms": {"field": "doc_id"}}}],
            }
            if after:
                composite["after"] = after
            body = {
                "size": 0,
                "track_total_hits": False,
                "query": query,
                "aggs": {"docs": {
                    "composite": composite,
                    "aggs": {"exemplar": {"top_hits": {
                        "size": 1, "_source": {"includes": _SOURCE_FIELDS},
                    }}},
                }},
            }
            r = c.post(f"{es_url.rstrip('/')}/{index}/_search", json=body, headers=headers)
            r.raise_for_status()
            agg = (r.json().get("aggregations") or {}).get("docs") or {}
            buckets = agg.get("buckets") or []
            for b in buckets:
                hits = b["exemplar"]["hits"]["hits"]
                if not hits:
                    continue
                meta = dict(hits[0]["_source"].get("metadata") or {})
                entries.append(_doc_entry(str(b["key"]["doc_id"]), int(b["doc_count"]), meta))
            after = agg.get("after_key")
            if not after or len(buckets) < PAGE_SIZE:
                break
        report.n_docs = len(entries)
        report.groups, report.n_without_ids = group_documents(index, entries)
    except Exception as e:  # noqa: BLE001 — one bad index must not end the sweep
        report.error = f"{type(e).__name__}: {e}"
    finally:
        if owns:
            c.close()
    return report


def render_text(reports: list[IndexDuplicates]) -> str:
    lines: list[str] = []
    for rep in reports:
        head = f"{rep.index} @ {rep.es_url}"
        if rep.error:
            lines.append(f"{head}: ERROR {rep.error}")
            continue
        n_dup = sum(len(g.docs) for g in rep.groups)
        lines.append(
            f"{head}: {rep.n_docs} document(s), {len(rep.groups)} duplicate group(s) "
            f"covering {n_dup} document(s); {rep.n_without_ids} with no DOI/PMCID/PMID"
        )
        for g in rep.groups:
            lines.append(f"  group [{', '.join(g.keys)}] — {len(g.docs)} copies")
            for d in g.docs:
                name = d.filename or d.source_path.rsplit("/", 1)[-1] or "?"
                extra = f" duplicate_of={d.duplicate_of}" if d.duplicate_of else ""
                lines.append(
                    f"    {d.doc_id}  chunks={d.chunk_count:<4} {name}"
                    f"  keys={','.join(d.keys)}{extra}"
                )
                if d.title:
                    lines.append(f"        title: {d.title}")
    return "\n".join(lines)


def to_dict(reports: list[IndexDuplicates]) -> dict[str, Any]:
    return {"indices": [asdict(r) for r in reports]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="duplicate_report",
        description=(
            "List documents that are the same work (shared DOI / PMCID / PMID) "
            "within each Elasticsearch index. Read-only; always exits 0."
        ),
    )
    ap.add_argument("--es-url", action="append", default=[], metavar="URL",
                    help="Elasticsearch base URL; repeatable. Required.")
    ap.add_argument("--index", action="append", default=[], metavar="GLOB",
                    help="Only indices matching this shell glob; repeatable. "
                         "Default: every non-system index.")
    ap.add_argument("--tenant", action="append", default=[], metavar="TENANT",
                    help="Only documents stamped with this tenant_id; repeatable. "
                         "Default: all.")
    ap.add_argument("--api-key", default="", help="ES API key, if the cluster needs one.")
    ap.add_argument("--json", action="store_true", help="Machine-readable output.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    if not args.es_url:
        ap.error("at least one --es-url is required")
    targets = resolve_targets(args.es_url, args.index, api_key=args.api_key)
    if not targets:
        print("no indices matched", file=sys.stderr)
        return 0
    with httpx.Client(timeout=60.0) as c:
        reports = [
            scan_index(u, i, tenants=args.tenant or None, api_key=args.api_key, client=c)
            for u, i in targets
        ]
    print(json.dumps(to_dict(reports), indent=2) if args.json else render_text(reports))
    return 0
