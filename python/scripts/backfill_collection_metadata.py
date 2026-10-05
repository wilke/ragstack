#!/usr/bin/env python3
"""Backfill or repair scholarly metadata on a user-uploaded collection, keyed on DOI.

**REPAIR ONLY since #596.** Ingest now does this itself, at upload time, on both
backends — ``ragstack.ingestion.doi_metadata`` resolves each distinct DOI between
load and chunk, so the fields ride on every chunk from the start. What is left
for this script is collections built BEFORE that landed, the rare collection
whose lookups all failed during an outage, and (``--overwrite``) collections an
earlier version of this script wrote in a format ingest does not produce. A
collection ingested after #596 that is still bare has a cause worth finding
first; see ``docs/runbooks/tenant-admin.md`` § 6c.

**One mapper (#637).** Resolution goes through the ingest path's own
:class:`~ragstack.ingestion.doi_metadata.DoiMetadataResolver` — Crossref via
``map_crossref``, DataCite on a Crossref 404, the NCBI ID Converter via
``map_idconv`` — so title, authors (``"Given Family"``, a consortium by its
``name``), journal, year (bounded by ``coerce_year``) and pmid/pmcid (strings)
are exactly what an ingest would have written. This script used to carry a
private copy of that mapping; the copy wrote authors as ``"Family, Given"``, and
that drift is what ``--fields authors --overwrite`` exists to repair.

Modes:

* default (fill): write a field only on chunks where it is absent in BOTH
  stores, and stamp the provenance keys (``metadata_source`` /
  ``doi_enriched_from``) the way ``merge_enrichment`` does when it fills.
* ``--overwrite``: also rewrite a present value that differs from the resolved
  one — but only on a chunk that carries enrichment provenance. A value with no
  provenance came from the corpus or the operator, and ingest's rule is that
  such a value is never clobbered by a remote lookup; it is reported as kept.

Either way a value that would become identical counts as unchanged and nothing is
written for it, and ``--fields`` limits the run to the named fields.

Both stores, and they are not the same shape:

* Elasticsearch nests these under ``metadata.*``
* Qdrant keeps them FLAT at the payload top level

Every chunk's update is ONE dict, written to both stores (ES as a partial
``{"metadata": dict}``, Qdrant as ``set_payload(dict)``), so the two legs cannot
be handed different values. After ``--apply`` the script re-reads every changed
chunk from both stores and exits non-zero if any written field differs between
the stores or from the resolved value.

Dry-run by default::

    python3 scripts/backfill_collection_metadata.py \\
        --es http://127.0.0.1:24083 --qdrant http://127.0.0.1:24081 \\
        --tenant-env /rag/data/tenants/<t>/config/tenant.env \\
        --index ragstack_lib_dengue_..._2127eaf9 \\
        --fields authors --overwrite

Scope: this is for COLLECTIONS BUILT FROM USER UPLOADS.

* A JATS corpus (chunks carrying ``content_type``/``licence``) or any collection
  where most titles carry no enrichment provenance is refused unless
  ``--force``: its metadata came from the article XML or the operator and is
  better than a lookup.
* A ROUTED or shared store is refused outright, ``--force`` or not: a write from
  one tenant would change what every tenant reads. ``--apply`` therefore needs
  ``--tenant-env``, which is checked for routes (this tenant's and its siblings')
  and for ``--es``/``--qdrant`` being the tenant's own default instances.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# The mapper must be THIS checkout's, not whatever ragstack the env has installed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ragstack.ingestion.doi_metadata import (  # noqa: E402
    ENRICHED_FROM_KEY,
    FIELD_TO_METADATA_KEY,
    METADATA_SOURCE_KEY,
    Resolution,
    build_resolver,
    normalize_doi,
)
from ragstack.ops.store_inventory import canonical_url, parse_env_file  # noqa: E402

#: The fields a run touches when ``--fields`` is not given (unchanged from the
#: pre-#637 script).
DEFAULT_FIELDS = ("title", "authors", "journal", "pmid", "pmcid")
#: Metadata key -> the resolver's normalized field name. ``doi`` is excluded: it
#: is the key every chunk is matched on, never a value this script rewrites.
METADATA_KEY_TO_FIELD = {v: k for k, v in FIELD_TO_METADATA_KEY.items() if k != "doi"}
SELECTABLE_FIELDS = tuple(METADATA_KEY_TO_FIELD)
PROVENANCE_KEYS = (METADATA_SOURCE_KEY, ENRICHED_FROM_KEY)
#: Keys only ``ingestion.jats`` emits. Any chunk carrying one means the corpus
#: was built from article XML.
JATS_MARKERS = ("content_type", "licence")
EXAMPLES_PER_FIELD = 3
PAGE = 500


class Refusal(SystemExit):
    """A guard said no: non-zero exit, with the reason on stderr."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"refusing: {reason}")


# --------------------------------------------------------------------------- #
# store access
# --------------------------------------------------------------------------- #

def _json(resp: httpx.Response, what: str) -> Any:
    if resp.status_code >= 400:
        # A bare status tells the operator nothing about WHICH store refused and
        # why; both stores speak JSON on the error path.
        raise SystemExit(f"\n{resp.status_code} from {what}\n{resp.text[:800]}")
    return resp.json()


@dataclass
class ChunkView:
    """One chunk as one store holds it: the store's own id, plus the metadata
    keys this script reads, in the same flat shape for both stores."""

    store_id: str
    meta: dict[str, Any]


ChunkKey = tuple[str, str]  # (tenant_id, chunk_id) — the identity both stores share


class EsStore:
    """The text leg. Metadata is NESTED under ``metadata.*``."""

    def __init__(self, http: httpx.Client, base: str, index: str) -> None:
        self.http, self.base, self.index = http, base.rstrip("/"), index

    def exists(self) -> bool:
        return self.http.get(f"{self.base}/{self.index}").status_code == 200

    def count(self, query: dict[str, Any]) -> int:
        r = self.http.post(f"{self.base}/{self.index}/_count", json={"query": query})
        return int(_json(r, "elasticsearch _count")["count"])

    def distinct_dois(self) -> list[tuple[str, int]]:
        body = {"size": 0, "aggs": {"d": {"terms": {"field": "metadata.doi", "size": 10000}}}}
        r = self.http.post(f"{self.base}/{self.index}/_search", json=body)
        buckets = _json(r, "elasticsearch doi aggregation")["aggregations"]["d"]["buckets"]
        return [(b["key"], b["doc_count"]) for b in buckets]

    def scan(self, keys: Iterable[str]) -> Iterator[tuple[ChunkKey, ChunkView]]:
        """Every chunk with a DOI, read only — a scroll, cleared afterwards."""
        includes = ["chunk_id"] + [f"metadata.{k}" for k in keys]
        body = {"size": PAGE, "query": {"exists": {"field": "metadata.doi"}},
                "_source": {"includes": includes}}
        r = self.http.post(f"{self.base}/{self.index}/_search", params={"scroll": "2m"}, json=body)
        page = _json(r, "elasticsearch scroll")
        scroll_id = page.get("_scroll_id")
        try:
            while page["hits"]["hits"]:
                for hit in page["hits"]["hits"]:
                    src = hit.get("_source") or {}
                    meta = src.get("metadata") or {}
                    key = (str(meta.get("tenant_id", "")), str(src.get("chunk_id", hit["_id"])))
                    yield key, ChunkView(hit["_id"], meta)
                r = self.http.post(f"{self.base}/_search/scroll",
                                   json={"scroll": "2m", "scroll_id": scroll_id})
                page = _json(r, "elasticsearch scroll")
                scroll_id = page.get("_scroll_id", scroll_id)
        finally:
            if scroll_id:
                self.http.request("DELETE", f"{self.base}/_search/scroll",
                                  json={"scroll_id": scroll_id})

    def write(self, groups: list[tuple[dict[str, Any], list[str]]]) -> None:
        """Partial update: ``{"metadata": update}`` merges into the nested object,
        so every key not in ``update`` is left exactly as it was."""
        lines: list[str] = []
        for update, ids in groups:
            for es_id in ids:
                lines.append(json.dumps({"update": {"_index": self.index, "_id": es_id}}))
                lines.append(json.dumps({"doc": {"metadata": update}}))
        for start in range(0, len(lines), 2 * PAGE):
            body = "\n".join(lines[start:start + 2 * PAGE]) + "\n"
            r = self.http.post(f"{self.base}/_bulk", params={"refresh": "true"}, content=body,
                               headers={"Content-Type": "application/x-ndjson"})
            out = _json(r, "elasticsearch _bulk")
            if out.get("errors"):
                bad = [i for i in out.get("items", []) if i.get("update", {}).get("error")]
                raise SystemExit(f"elasticsearch _bulk reported errors: {json.dumps(bad[:3])}")

    def fetch(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for start in range(0, len(ids), PAGE):
            r = self.http.post(f"{self.base}/{self.index}/_mget",
                               json={"ids": ids[start:start + PAGE]})
            for doc in _json(r, "elasticsearch _mget")["docs"]:
                if doc.get("found"):
                    out[doc["_id"]] = (doc.get("_source") or {}).get("metadata") or {}
        return out


class QdrantStore:
    """The vector leg. Metadata is FLAT at the payload top level."""

    def __init__(self, http: httpx.Client, base: str, collection: str) -> None:
        self.http, self.base, self.collection = http, base.rstrip("/"), collection

    @property
    def _c(self) -> str:
        return f"{self.base}/collections/{self.collection}"

    def exists(self) -> bool:
        return self.http.get(self._c).status_code == 200

    def scan(self, keys: Iterable[str]) -> Iterator[tuple[ChunkKey, ChunkView]]:
        include = ["chunk_id", *keys]
        offset: Any = None
        while True:
            body: dict[str, Any] = {"limit": PAGE, "with_payload": {"include": include},
                                    "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            res = _json(self.http.post(f"{self._c}/points/scroll", json=body),
                        "qdrant scroll")["result"]
            for p in res["points"]:
                payload = p.get("payload") or {}
                if payload.get("doi") in (None, ""):
                    continue
                key = (str(payload.get("tenant_id", "")), str(payload.get("chunk_id", p["id"])))
                yield key, ChunkView(str(p["id"]), payload)
            offset = res.get("next_page_offset")
            if offset is None:
                return

    def write(self, groups: list[tuple[dict[str, Any], list[str]]]) -> None:
        """``set_payload`` overwrites the named keys and nothing else."""
        for update, ids in groups:
            for start in range(0, len(ids), PAGE):
                r = self.http.post(f"{self._c}/points/payload", params={"wait": "true"},
                                   json={"payload": update, "points": ids[start:start + PAGE]})
                _json(r, "qdrant set_payload")

    def fetch(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for start in range(0, len(ids), PAGE):
            r = self.http.post(f"{self._c}/points",
                               json={"ids": ids[start:start + PAGE], "with_payload": True})
            for p in _json(r, "qdrant retrieve")["result"]:
                out[str(p["id"])] = p.get("payload") or {}
        return out


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #

def _routes(values: dict[str, str], key: str, where: Path) -> dict[str, str]:
    raw = values.get(key, "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as e:
        raise Refusal(f"{key} in {where} is not valid JSON ({e}); cannot prove the "
                      "index is not routed") from None
    return parsed if isinstance(parsed, dict) else {}


def check_not_routed(tenant_env: str, es: str, qdrant: str, index: str) -> None:
    """Refuse a routed or shared store. NOT overridable by ``--force``.

    Three ways a write lands on a store other tenants read:

    1. this tenant routes the index elsewhere (``*_COLLECTION_ROUTES``);
    2. ``--es``/``--qdrant`` are not this tenant's own default instances — the
       operator has pointed at some other instance directly;
    3. a SIBLING tenant (same tenants root) routes this index, i.e. reads this
       very store.
    """
    path = Path(tenant_env)
    if not path.is_file():
        raise Refusal(f"--tenant-env {tenant_env} is not a file")
    values = parse_env_file(path)
    for key in ("QDRANT_COLLECTION_ROUTES", "ES_COLLECTION_ROUTES"):
        routes = _routes(values, key, path)
        if index in routes:
            raise Refusal(f"{index} is routed to {routes[index]} by {key} in {path}; "
                          "a routed store is shared with other tenants")
    for flag, url, key in (("--es", es, "ELASTICSEARCH_URL"), ("--qdrant", qdrant, "QDRANT_URL")):
        own = values.get(key, "")
        if not own:
            raise Refusal(f"{path} sets no {key}; cannot show {flag} is this tenant's own store")
        if canonical_url(url) != canonical_url(own):
            raise Refusal(f"{flag} {url} is not this tenant's {key} ({own}); "
                          "this script only writes a tenant's own stores")
    # <root>/<tenant>/config/tenant.env -> every sibling tenant's tenant.env
    resolved = path.resolve()
    if resolved.parent.name == "config":
        for sibling in sorted(resolved.parents[2].glob("*/config/tenant.env")):
            if sibling.resolve() == resolved:
                continue
            try:
                other = parse_env_file(sibling)
            except OSError:
                continue
            for key in ("QDRANT_COLLECTION_ROUTES", "ES_COLLECTION_ROUTES"):
                if index in _routes(other, key, sibling):
                    raise Refusal(f"{index} is routed by {key} in {sibling}: another "
                                  "tenant reads this store")


def check_upload_built(es: EsStore, total: int, force: bool) -> None:
    """Refuse a JATS/curated corpus unless ``--force``.

    Two signals. Chunks carrying a key only ``ingestion.jats`` emits mean the
    metadata came from the article XML. Otherwise, a collection where most
    titles carry NO enrichment provenance got its titles from the corpus or the
    operator, not from a lookup, and this tool is not for it.
    """
    jats = es.count({"bool": {"should": [{"exists": {"field": f"metadata.{k}"}}
                                          for k in JATS_MARKERS],
                              "minimum_should_match": 1}})
    curated = es.count({"bool": {
        "filter": [{"exists": {"field": "metadata.title"}}],
        "must_not": [{"exists": {"field": f"metadata.{k}"}} for k in PROVENANCE_KEYS]}})
    print(f"  JATS-marked chunks {jats}; titles without enrichment provenance {curated}")
    if force:
        return
    if jats:
        raise Refusal(f"{jats} chunk(s) carry JATS-only keys ({', '.join(JATS_MARKERS)}) — "
                      "this is a JATS corpus, whose metadata is better than a lookup. "
                      "Use --force only if you know why.")
    if curated > total * 0.5:
        raise Refusal("most titles here did not come from a DOI lookup — this is not an "
                      "upload-built collection. Use --force only if you know why.")


# --------------------------------------------------------------------------- #
# resolve
# --------------------------------------------------------------------------- #

def resolve(dois: list[str], *, email: str, cache_dir: str = "",
            transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Resolution]:
    """Resolve through the INGEST path's resolver, keyed by normalized DOI.

    Same services, same mapping, same ID-Converter handling as an upload: this
    function only owns the event loop and the client.
    """
    async def _run() -> dict[str, Resolution]:
        async with httpx.AsyncClient(transport=transport) as client:
            resolver = build_resolver(client, mailto=email, cache_dir=cache_dir)
            return await resolver.resolve_many(dois)

    return asyncio.run(_run())


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #

def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | tuple | dict):
        return len(value) == 0
    return False


@dataclass
class Change:
    es_id: str
    qd_id: str
    doi: str
    update: dict[str, Any]   # the ONE dict both stores receive
    before: dict[str, Any]


@dataclass
class FieldStats:
    changed: int = 0
    unchanged: int = 0
    kept: int = 0        # --overwrite: present, no enrichment provenance — never clobbered
    present: int = 0     # fill mode: present and consistent, left alone
    drift: int = 0       # fill mode: the two stores disagree; not touched
    unresolved: int = 0
    examples: list[tuple[str, Any, Any]] = field(default_factory=list)


@dataclass
class Plan:
    changes: list[Change]
    stats: dict[str, FieldStats]
    unpaired_es: int = 0
    unpaired_qdrant: int = 0
    doi_disagrees: int = 0


def plan(es_chunks: dict[ChunkKey, ChunkView], qd_chunks: dict[ChunkKey, ChunkView],
         resolved: dict[str, Resolution], fields: list[str], overwrite: bool) -> Plan:
    """Decide, per chunk, the ONE update dict both stores will receive."""
    stats = {f: FieldStats() for f in fields}
    out = Plan([], stats)
    out.unpaired_es = len(es_chunks.keys() - qd_chunks.keys())
    out.unpaired_qdrant = len(qd_chunks.keys() - es_chunks.keys())
    for key in sorted(es_chunks.keys() & qd_chunks.keys()):
        e, q = es_chunks[key], qd_chunks[key]
        doi = normalize_doi(str(e.meta.get("doi") or ""))
        if doi != normalize_doi(str(q.meta.get("doi") or "")):
            out.doi_disagrees += 1
            continue
        res = resolved.get(doi)
        stamped = any(not _missing(m.get(k)) for m in (e.meta, q.meta) for k in PROVENANCE_KEYS)
        update: dict[str, Any] = {}
        before: dict[str, Any] = {}
        filled = False
        for f in fields:
            st = stats[f]
            new = res.fields.get(METADATA_KEY_TO_FIELD[f]) if res else None
            if _missing(new):
                st.unresolved += 1
                continue
            ev, qv = e.meta.get(f), q.meta.get(f)
            if ev == new and qv == new:
                st.unchanged += 1
                continue
            if _missing(ev) and _missing(qv):
                filled = True
            elif not overwrite:
                if ev == qv:
                    st.present += 1
                else:
                    st.drift += 1
                continue
            elif not stamped:
                st.kept += 1
                continue
            update[f] = new
            before[f] = ev if ev == qv else {"es": ev, "qdrant": qv}
            st.changed += 1
            if len(st.examples) < EXAMPLES_PER_FIELD and all(
                    x[1] != before[f] for x in st.examples):
                st.examples.append((e.store_id, before[f], new))
        if filled and res:
            # merge_enrichment's rule: whoever fills a field stamps where it came from.
            for k in PROVENANCE_KEYS:
                if e.meta.get(k) != res.service or q.meta.get(k) != res.service:
                    update[k] = res.service
        if update:
            out.changes.append(Change(e.store_id, q.store_id, doi, update, before))
    return out


def _show(v: Any) -> str:
    if isinstance(v, list):
        return json.dumps(v[:3] + (["…"] if len(v) > 3 else []), ensure_ascii=False)
    return json.dumps(v, ensure_ascii=False)


def print_plan(p: Plan, overwrite: bool) -> None:
    print(f"plan ({'overwrite' if overwrite else 'fill missing only'}): "
          f"{len(p.changes)} chunk(s) would be written")
    for f, st in p.stats.items():
        print(f"  {f}: {st.changed} chunk(s) would change, {st.unchanged} unchanged, "
              f"{st.unresolved} unresolved"
              + (f", {st.kept} kept (no enrichment provenance)" if st.kept else "")
              + (f", {st.present} already present" if st.present else "")
              + (f", {st.drift} skipped (stores disagree; use --overwrite)" if st.drift else ""))
        for es_id, b, a in st.examples:
            print(f"      {es_id}\n        before {_show(b)}\n        after  {_show(a)}")
    for label, n in (("chunks in Elasticsearch with no Qdrant point", p.unpaired_es),
                     ("Qdrant points with no Elasticsearch chunk", p.unpaired_qdrant),
                     ("chunks whose DOI differs between the stores", p.doi_disagrees)):
        if n:
            print(f"  !! {n} {label} — not touched")


# --------------------------------------------------------------------------- #
# apply + verify
# --------------------------------------------------------------------------- #

def _groups(changes: list[Change], attr: str) -> list[tuple[dict[str, Any], list[str]]]:
    """Chunks sharing an identical update go in one request per store."""
    by: dict[str, tuple[dict[str, Any], list[str]]] = {}
    for c in changes:
        k = json.dumps(c.update, sort_keys=True)
        by.setdefault(k, (c.update, []))[1].append(getattr(c, attr))
    return list(by.values())


def apply(es: EsStore, qd: QdrantStore, changes: list[Change]) -> None:
    es.write(_groups(changes, "es_id"))
    qd.write(_groups(changes, "qd_id"))


def verify(es: EsStore, qd: QdrantStore, changes: list[Change]) -> list[str]:
    """Re-read every changed chunk from BOTH stores; every written key must equal
    the resolved value in each. Returns the mismatches (empty = clean)."""
    es_now = es.fetch([c.es_id for c in changes])
    qd_now = qd.fetch([c.qd_id for c in changes])
    bad: list[str] = []
    for c in changes:
        if c.es_id not in es_now or c.qd_id not in qd_now:
            bad.append(f"{c.es_id}: missing on re-read (elasticsearch={c.es_id in es_now}, "
                       f"qdrant={c.qd_id in qd_now})")
            continue
        for k, want in c.update.items():
            ev, qv = es_now[c.es_id].get(k), qd_now[c.qd_id].get(k)
            if not (ev == want and qv == want):
                bad.append(f"{c.es_id} / {c.qd_id} {k}: expected {_show(want)}, "
                           f"elasticsearch {_show(ev)}, qdrant {_show(qv)}")
    return bad


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def _parse_fields(raw: str) -> list[str]:
    fields = list(dict.fromkeys(f.strip() for f in raw.split(",") if f.strip()))
    unknown = [f for f in fields if f not in METADATA_KEY_TO_FIELD]
    if unknown or not fields:
        raise argparse.ArgumentTypeError(
            f"unknown field(s) {unknown}; choose from {', '.join(SELECTABLE_FIELDS)}")
    return fields


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--es", required=True)
    ap.add_argument("--qdrant", required=True)
    ap.add_argument("--index", required=True, help="physical store name (same in both)")
    ap.add_argument("--tenant-env", default="",
                    help="the tenant's tenant.env; required for --apply (routed/shared-store check)")
    ap.add_argument("--fields", type=_parse_fields, default=list(DEFAULT_FIELDS),
                    help=f"comma-separated metadata keys (default {','.join(DEFAULT_FIELDS)}; "
                         f"any of {','.join(SELECTABLE_FIELDS)})")
    ap.add_argument("--overwrite", action="store_true",
                    help="rewrite --fields even where present (enrichment-provenanced chunks only)")
    ap.add_argument("--email", default="wilke@anl.gov", help="contact for Crossref/NCBI politeness")
    ap.add_argument("--doi-cache-dir", default="", help="on-disk resolver cache (default: none)")
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--force", action="store_true",
                    help="override the JATS/curated-corpus guard (never the routed-store guard)")
    return ap


def main(argv: list[str] | None = None, *,
         store_transport: httpx.BaseTransport | None = None,
         doi_transport: httpx.AsyncBaseTransport | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.apply and not a.tenant_env:
        raise Refusal("--apply needs --tenant-env, to show the stores are this tenant's "
                      "own and the index is not routed")
    if a.tenant_env:
        check_not_routed(a.tenant_env, a.es, a.qdrant, a.index)

    with httpx.Client(transport=store_transport, timeout=120) as http:
        es, qd = EsStore(http, a.es, a.index), QdrantStore(http, a.qdrant, a.index)
        for name, store, url in (("Elasticsearch", es, a.es), ("Qdrant", qd, a.qdrant)):
            if not store.exists():
                raise Refusal(f"{a.index} is not in {name} at {url}. A routed collection's "
                              "store lives on another instance.")
        total = es.count({"match_all": {}})
        print(f"index {a.index}\n  chunks {total}")
        check_upload_built(es, total, a.force)

        dois = es.distinct_dois()
        print(f"  {len(dois)} distinct DOI(s) covering {sum(n for _, n in dois)} chunks\n")
        if not dois:
            raise SystemExit("no DOIs — nothing this script can key on")

        resolved = resolve([d for d, _ in dois], email=a.email,
                           cache_dir=a.doi_cache_dir, transport=doi_transport)
        for doi, n in dois:
            r = resolved.get(normalize_doi(doi))
            print(f"  {doi}  ({n} chunks)  via {r.service if r else '—'}")
            for f in a.fields:
                v = r.fields.get(METADATA_KEY_TO_FIELD[f]) if r else None
                print(f"      {f:8} {'— unresolved' if _missing(v) else _show(v)}")
        print()

        keys = ["doi", "tenant_id", *a.fields, *PROVENANCE_KEYS]
        es_chunks = dict(es.scan(keys))
        qd_chunks = dict(qd.scan(keys))
        p = plan(es_chunks, qd_chunks, resolved, a.fields, a.overwrite)
        print_plan(p, a.overwrite)

        if not a.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            return 0
        if not p.changes:
            print("\nnothing to write.")
            return 0
        apply(es, qd, p.changes)
        print(f"\nwrote {len(p.changes)} chunk(s) to both stores; verifying …")
        bad = verify(es, qd, p.changes)
        if bad:
            print(f"VERIFY FAILED: {len(bad)} mismatch(es)")
            for line in bad:
                print(f"  {line}")
            return 1
        print(f"verified: all {len(p.changes)} changed chunk(s) agree across both stores "
              "and with the resolved values.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
