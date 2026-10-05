"""Tests for the metadata repair tool (scripts/backfill_collection_metadata.py, #637).

Entirely offline. Both stores are in-memory fakes behind an ``httpx.MockTransport``
that speak the handful of Elasticsearch / Qdrant endpoints the script uses, in
each store's real shape (ES nests metadata under ``metadata``; Qdrant keeps it
flat on the payload). Crossref / NCBI are a second MockTransport. Hosts are
``*.invalid`` and never dialled.

What is pinned, and the failure each guards:

* authors come out of the INGEST mapper (``"Given Family"``, consortium by name)
  — the private copy wrote ``"Family, Given"``, which is the drift being repaired;
* ``--overwrite`` rewrites only the named fields, every other key byte-identical
  in both store shapes, and an already-identical value is not written at all;
* a dry run makes no mutating request;
* the post-apply verification catches a store that did not take the write;
* the routed/shared-store and JATS-corpus refusals.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import backfill_collection_metadata as bf  # noqa: E402

from ragstack.ingestion.doi_metadata import map_crossref  # noqa: E402

ES = "http://es.invalid:9"
QD = "http://qdrant.invalid:9"
INDEX = "ragstack_lib_dengue_test_2127eaf9"
TENANT = "lab"

# --------------------------------------------------------------------------- #
# fake Crossref / ID Converter
# --------------------------------------------------------------------------- #

DOI_A, DOI_B, DOI_C = "10.1128/jvi.02415-06", "10.1000/xyz.1", "10.1000/xyz.2"

CROSSREF = {
    DOI_A: {
        "DOI": "10.1128/JVI.02415-06",
        "title": ["Dengue <i>in vitro</i>"],
        "container-title": ["Journal of Virology"],
        "issued": {"date-parts": [[2007, 3]]},
        "author": [
            {"given": "Ada", "family": "Lovelace"},
            {"given": "Alan", "family": "Turing"},
            {"name": "The Dengue Consortium"},
        ],
    },
    DOI_B: {
        "DOI": DOI_B,
        "title": ["Second Paper"],
        "container-title": ["J Test"],
        "issued": {"date-parts": [[2019]]},
        "author": [{"given": "Grace", "family": "Hopper"}, {"family": "Solo"}],
    },
    DOI_C: {
        "DOI": DOI_C,
        "title": ["Third Paper"],
        "container-title": ["J Test"],
        "issued": {"date-parts": [[2020]]},
        "author": [{"given": "Barbara", "family": "Liskov"}],
    },
}
# The real ID-Converter shape: pmid is a JSON int, "doi" is the publisher's case.
IDCONV = [
    {"requested-id": DOI_A, "doi": "10.1128/JVI.02415-06", "pmid": 17229691,
     "pmcid": "PMC1865936"},
    {"requested-id": DOI_B, "doi": DOI_B, "pmid": 111, "pmcid": "PMC222"},
]


def doi_handler(seen: list[httpx.Request] | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        host = request.url.host
        if "crossref" in host:
            doi = unquote(request.url.path.split("/works/", 1)[1])
            if doi in CROSSREF:
                return httpx.Response(200, json={"status": "ok", "message": CROSSREF[doi]})
            return httpx.Response(404)
        if "ncbi" in host:
            asked = set(request.url.params["ids"].split(","))
            return httpx.Response(200, json={"records": [r for r in IDCONV
                                                          if r["requested-id"] in asked]})
        return httpx.Response(404)  # datacite
    return httpx.MockTransport(handle)


# --------------------------------------------------------------------------- #
# fake stores
# --------------------------------------------------------------------------- #

def _get_path(src: dict[str, Any], dotted: str) -> Any:
    cur: Any = src
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _matches(src: dict[str, Any], q: dict[str, Any]) -> bool:
    if "match_all" in q:
        return True
    if "exists" in q:
        v = _get_path(src, q["exists"]["field"])
        return v is not None and v != []
    if "bool" in q:
        b = q["bool"]
        if not all(_matches(src, c) for c in b.get("filter", []) + b.get("must", [])):
            return False
        if any(_matches(src, c) for c in b.get("must_not", [])):
            return False
        if "should" in b:
            need = b.get("minimum_should_match", 1)
            return sum(_matches(src, c) for c in b["should"]) >= need
        return True
    raise AssertionError(f"fake ES does not understand query {q}")


def _project(src: dict[str, Any], includes: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for inc in includes:
        v = _get_path(src, inc)
        if v is None:
            continue
        cur = out
        parts = inc.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = copy.deepcopy(v)
    return out


def _merge(dst: dict[str, Any], patch: dict[str, Any]) -> None:
    """ES partial-update semantics: objects merge, everything else replaces."""
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


class FakeStores:
    """Elasticsearch + Qdrant over the same chunks, in each store's own shape."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.es: dict[str, dict[str, Any]] = {}
        self.qd: dict[str, dict[str, Any]] = {}
        for i, meta in enumerate(chunks):
            chunk_id = f"doc{i // 2}:{i}"
            meta = dict(meta, tenant_id=TENANT)
            self.es[f"{TENANT}:{chunk_id}"] = {
                "content": f"text {i}", "doc_id": f"doc{i // 2}", "chunk_id": chunk_id,
                "start_char": 0, "end_char": 6, "metadata": copy.deepcopy(meta)}
            self.qd[f"00000000-0000-0000-0000-{i:012d}"] = {
                "chunk_id": chunk_id, "doc_id": f"doc{i // 2}", "content": f"text {i}",
                "start_char": 0, "end_char": 6, **copy.deepcopy(meta)}
        self.requests: list[tuple[str, str]] = []
        self.scrolls: dict[str, list[dict[str, Any]]] = {}
        #: Qdrant point ids whose payload writes are silently dropped (drift).
        self.qdrant_drops: set[str] = set()
        self.es_present = True

    def snapshot(self) -> tuple[str, str]:
        return (json.dumps(self.es, sort_keys=True), json.dumps(self.qd, sort_keys=True))

    def mutating(self) -> list[tuple[str, str]]:
        return [r for r in self.requests if r[1].endswith(("_bulk", "/points/payload"))]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        body = json.loads(request.content) if request.content and "_bulk" not in path else {}
        if request.url.host == "es.invalid":
            return self._es(request, path, body)
        return self._qdrant(request, path, body)

    def _es(self, request: httpx.Request, path: str, body: dict[str, Any]) -> httpx.Response:
        if request.method == "GET" and path == f"/{INDEX}":
            return httpx.Response(200 if self.es_present else 404, json={})
        if path == f"/{INDEX}/_count":
            return httpx.Response(200, json={"count": sum(
                _matches(s, body["query"]) for s in self.es.values())})
        if path == f"/{INDEX}/_search" and "aggs" in body:
            counts: dict[str, int] = {}
            for s in self.es.values():
                d = s["metadata"].get("doi")
                if d:
                    counts[d] = counts.get(d, 0) + 1
            return httpx.Response(200, json={"aggregations": {"d": {"buckets": [
                {"key": k, "doc_count": n} for k, n in counts.items()]}}})
        if path == f"/{INDEX}/_search":
            assert request.url.params.get("scroll"), "a full read must scroll"
            hits = [{"_id": i, "_source": _project(s, body["_source"]["includes"])}
                    for i, s in self.es.items() if _matches(s, body["query"])]
            self.scrolls["s1"] = hits
            return self._page("s1", 2)
        if path == "/_search/scroll" and request.method == "POST":
            return self._page(body["scroll_id"], 2)
        if path == "/_search/scroll" and request.method == "DELETE":
            return httpx.Response(200, json={"succeeded": True})
        if path == "/_bulk":
            lines = [json.loads(x) for x in request.content.decode().splitlines() if x]
            items = []
            for action, doc in zip(lines[::2], lines[1::2], strict=True):
                es_id = action["update"]["_id"]
                assert action["update"]["_index"] == INDEX
                _merge(self.es[es_id], doc["doc"])
                items.append({"update": {"_id": es_id, "status": 200}})
            return httpx.Response(200, json={"errors": False, "items": items})
        if path == f"/{INDEX}/_mget":
            return httpx.Response(200, json={"docs": [
                {"_id": i, "found": i in self.es, "_source": copy.deepcopy(self.es.get(i))}
                for i in body["ids"]]})
        raise AssertionError(f"fake ES: unexpected {request.method} {path}")

    def _page(self, sid: str, size: int) -> httpx.Response:
        page, self.scrolls[sid] = self.scrolls[sid][:size], self.scrolls[sid][size:]
        return httpx.Response(200, json={"_scroll_id": sid, "hits": {"hits": page}})

    def _qdrant(self, request: httpx.Request, path: str, body: dict[str, Any]) -> httpx.Response:
        base = f"/collections/{INDEX}"
        if request.method == "GET" and path == base:
            return httpx.Response(200, json={"result": {}})
        if path == f"{base}/points/scroll":
            ids = sorted(self.qd)
            start = int(body.get("offset") or 0)
            limit = 2  # small pages so paging is exercised
            include = body["with_payload"]["include"]
            pts = [{"id": i, "payload": {k: copy.deepcopy(self.qd[i][k])
                                         for k in include if k in self.qd[i]}}
                   for i in ids[start:start + limit]]
            nxt = start + limit if start + limit < len(ids) else None
            return httpx.Response(200, json={"result": {"points": pts, "next_page_offset": nxt}})
        if path == f"{base}/points/payload":
            for pid in body["points"]:
                if pid not in self.qdrant_drops:
                    self.qd[pid].update(copy.deepcopy(body["payload"]))
            return httpx.Response(200, json={"result": {"status": "completed"}})
        if path == f"{base}/points":
            return httpx.Response(200, json={"result": [
                {"id": i, "payload": copy.deepcopy(self.qd[i])} for i in body["ids"] if i in self.qd]})
        raise AssertionError(f"fake Qdrant: unexpected {request.method} {path}")


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

OLD_AUTHORS_A = ["Lovelace, Ada", "Turing, Alan"]  # the pre-#637 script's format
NEW_AUTHORS_A = ["Ada Lovelace", "Alan Turing", "The Dengue Consortium"]
NEW_AUTHORS_B = ["Grace Hopper", "Solo"]


def _backfilled(doi: str, authors: list[str], **extra: Any) -> dict[str, Any]:
    """A chunk as the September backfill left it: stamped, old author format."""
    return {"doi": doi, "filename": "paper.pdf", "pages": 9, "year": 2007,
            "title": "Some locally different title", "journal": "J", "pmid": "1",
            "pmcid": "PMC1", "authors": authors, "metadata_source": "crossref+idconv",
            **extra}


def _dengue() -> FakeStores:
    return FakeStores([
        _backfilled(DOI_A, OLD_AUTHORS_A),
        _backfilled(DOI_A, OLD_AUTHORS_A),
        _backfilled(DOI_A, OLD_AUTHORS_A),
        _backfilled(DOI_B, ["Hopper, Grace", "Solo"]),
        _backfilled(DOI_B, NEW_AUTHORS_B),            # already right: must not be written
        _backfilled(DOI_C, ["Liskov, Barbara"]),
        {"filename": "nodoi.pdf"},                     # no DOI: never touched
    ])


@pytest.fixture()
def tenant_env(tmp_path: Path) -> Path:
    root = tmp_path / "tenants"
    env = root / TENANT / "config" / "tenant.env"
    env.parent.mkdir(parents=True)
    env.write_text(f"QDRANT_URL={QD}\nELASTICSEARCH_URL={ES}\n")
    return env


def _run(stores: FakeStores, *args: str, env: Path | None = None) -> int:
    argv = ["--es", ES, "--qdrant", QD, "--index", INDEX, *args]
    if env is not None:
        argv += ["--tenant-env", str(env)]
    return bf.main(argv, store_transport=stores.transport(), doi_transport=doi_handler())


def _without(d: dict[str, Any], *keys: str) -> str:
    return json.dumps({k: v for k, v in d.items() if k not in keys}, sort_keys=True)


# --------------------------------------------------------------------------- #
# one mapper
# --------------------------------------------------------------------------- #

def test_resolve_uses_the_ingest_mapper_for_authors_ids_and_doi_case():
    seen: list[httpx.Request] = []
    got = bf.resolve(["10.1128/JVI.02415-06", DOI_B], email="ops@example.org",
                     transport=doi_handler(seen))
    a = got[DOI_A].fields  # keyed by the normalized (lowercase) DOI
    assert a["authors"] == map_crossref(CROSSREF[DOI_A])["authors"] == NEW_AUTHORS_A
    assert got[DOI_B].fields["authors"] == NEW_AUTHORS_B == map_crossref(CROSSREF[DOI_B])["authors"]
    assert a["title"] == "Dengue in vitro"
    assert a["journal"] == "Journal of Virology" and a["year"] == 2007
    # ID Converter matched on requested-id despite the publisher's uppercase "doi",
    # and pmid is a string, like ingest and JATS write it.
    assert a["pmid"] == "17229691" and a["pmcid"] == "PMC1865936"
    assert a["doi"] == DOI_A
    assert got[DOI_A].service == "crossref+idconv"
    # one batched ID-Converter call, one Crossref call per DOI
    assert sum("ncbi" in r.url.host for r in seen) == 1
    assert sum("crossref" in r.url.host for r in seen) == 2


def test_fields_flag_rejects_unknown_and_the_join_key():
    with pytest.raises(SystemExit):
        bf.build_parser().parse_args(["--es", ES, "--qdrant", QD, "--index", INDEX,
                                      "--fields", "authors,doi"])
    a = bf.build_parser().parse_args(["--es", ES, "--qdrant", QD, "--index", INDEX,
                                      "--fields", "authors, year,authors"])
    assert a.fields == ["authors", "year"]
    assert bf.build_parser().parse_args(
        ["--es", ES, "--qdrant", QD, "--index", INDEX]).fields == list(bf.DEFAULT_FIELDS)


# --------------------------------------------------------------------------- #
# --overwrite
# --------------------------------------------------------------------------- #

def test_dry_run_writes_nothing_and_reports_counts_and_examples(capsys, tenant_env):
    stores = _dengue()
    before = stores.snapshot()
    assert _run(stores, "--fields", "authors", "--overwrite", env=tenant_env) == 0
    assert stores.mutating() == []
    assert stores.snapshot() == before
    out = capsys.readouterr().out
    assert "authors: 5 chunk(s) would change, 1 unchanged, 0 unresolved" in out
    assert out.count("        before ") == 3 and out.count("        after  ") == 3
    assert '["Lovelace, Ada", "Turing, Alan"]' in out
    assert '["Ada Lovelace", "Alan Turing", "The Dengue Consortium"]' in out
    assert "DRY RUN — nothing written" in out


def test_overwrite_rewrites_only_named_fields_in_both_shapes(capsys, tenant_env):
    stores = _dengue()
    es0, qd0 = copy.deepcopy(stores.es), copy.deepcopy(stores.qd)
    assert _run(stores, "--fields", "authors", "--overwrite", "--apply", env=tenant_env) == 0
    assert "verified: all 5 changed chunk(s)" in capsys.readouterr().out

    for es_id, src in stores.es.items():
        old = es0[es_id]
        # every top-level key and every metadata key except authors: byte-identical
        assert _without(src, "metadata") == _without(old, "metadata")
        assert _without(src["metadata"], "authors") == _without(old["metadata"], "authors")
    for pid, payload in stores.qd.items():
        assert _without(payload, "authors") == _without(qd0[pid], "authors")

    by_doi: dict[str, list[list[str]]] = {}
    for src in stores.es.values():
        if src["metadata"].get("doi"):
            by_doi.setdefault(src["metadata"]["doi"], []).append(src["metadata"]["authors"])
    assert by_doi[DOI_A] == [NEW_AUTHORS_A] * 3
    assert by_doi[DOI_B] == [NEW_AUTHORS_B] * 2
    # Qdrant flat, same values
    assert sorted(json.dumps(p.get("authors")) for p in stores.qd.values()) == sorted(
        json.dumps(s["metadata"].get("authors")) for s in stores.es.values())
    # title differed from Crossref but was not named: untouched
    assert {s["metadata"].get("title") for s in stores.es.values()} == {
        "Some locally different title", None}


def test_identical_value_is_not_written(tenant_env):
    """Chunk 4 already holds the ingest-format authors: it is in neither store's
    write, and no other chunk is written for a field that would not change."""
    stores = _dengue()
    es_written: list[str] = []
    qd_written: list[str] = []
    handle = stores._handle

    def spy(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_bulk":
            es_written.extend(json.loads(x)["update"]["_id"]
                              for x in request.content.decode().splitlines()[::2])
        elif request.url.path.endswith("/points/payload"):
            qd_written.extend(json.loads(request.content)["points"])
        return handle(request)

    stores._handle = spy  # type: ignore[method-assign]
    assert _run(stores, "--fields", "authors", "--overwrite", "--apply", env=tenant_env) == 0
    assert f"{TENANT}:doc2:4" not in es_written and len(es_written) == 5
    assert "00000000-0000-0000-0000-000000000004" not in qd_written and len(qd_written) == 5
    assert "00000000-0000-0000-0000-000000000006" not in qd_written  # the DOI-less chunk


def test_rerun_after_repair_is_a_no_op(capsys, tenant_env):
    stores = _dengue()
    _run(stores, "--fields", "authors", "--overwrite", "--apply", env=tenant_env)
    stores.requests.clear()
    capsys.readouterr()
    assert _run(stores, "--fields", "authors", "--overwrite", "--apply", env=tenant_env) == 0
    assert stores.mutating() == []
    assert "authors: 0 chunk(s) would change, 6 unchanged" in capsys.readouterr().out


def test_overwrite_never_clobbers_a_value_without_enrichment_provenance(capsys, tenant_env):
    chunks = [_backfilled(DOI_A, OLD_AUTHORS_A) for _ in range(3)]
    chunks.append({k: v for k, v in _backfilled(DOI_A, ["Curated, Author"]).items()
                   if k != "metadata_source"})
    stores = FakeStores(chunks)
    assert _run(stores, "--fields", "authors", "--overwrite", "--apply", env=tenant_env) == 0
    assert "3 chunk(s) would change, 0 unchanged, 0 unresolved, 1 kept" in capsys.readouterr().out
    assert sorted(json.dumps(s["metadata"]["authors"]) for s in stores.es.values()) == sorted(
        [json.dumps(NEW_AUTHORS_A)] * 3 + [json.dumps(["Curated, Author"])])


# --------------------------------------------------------------------------- #
# fill mode (the original job) — one dict, two shapes
# --------------------------------------------------------------------------- #

def test_fill_writes_one_dict_to_both_shapes_and_stamps_provenance(tenant_env):
    stores = FakeStores([{"doi": DOI_A, "filename": "a.pdf"}, {"doi": DOI_A, "filename": "a.pdf"},
                         {"doi": DOI_B, "filename": "b.pdf", "title": "Kept Local Title"}])
    assert _run(stores, "--apply", env=tenant_env) == 0
    for src in stores.es.values():
        m = src["metadata"]
        assert "authors" not in src and "title" not in src  # nested, never top-level
        pid = next(p for p, pl in stores.qd.items() if pl["chunk_id"] == src["chunk_id"])
        flat = stores.qd[pid]
        for k in (*bf.DEFAULT_FIELDS, "metadata_source", "doi_enriched_from"):
            assert flat.get(k) == m.get(k), k
        assert "metadata" not in flat
    a = next(s["metadata"] for s in stores.es.values() if s["metadata"]["doi"] == DOI_A)
    assert a["authors"] == NEW_AUTHORS_A and a["pmid"] == "17229691"
    assert a["metadata_source"] == a["doi_enriched_from"] == "crossref+idconv"
    b = next(s["metadata"] for s in stores.es.values() if s["metadata"]["doi"] == DOI_B)
    assert b["title"] == "Kept Local Title"  # fill mode never overwrites


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #

def test_verification_catches_a_store_that_drifted(capsys, tenant_env):
    stores = _dengue()
    stores.qdrant_drops = {"00000000-0000-0000-0000-000000000001"}
    assert _run(stores, "--fields", "authors", "--overwrite", "--apply", env=tenant_env) == 1
    out = capsys.readouterr().out
    assert "VERIFY FAILED: 1 mismatch(es)" in out
    assert "00000000-0000-0000-0000-000000000001 authors" in out
    assert 'qdrant ["Lovelace, Ada", "Turing, Alan"]' in out


def test_verify_reports_a_chunk_missing_on_reread():
    stores = _dengue()
    with httpx.Client(transport=stores.transport()) as http:
        es, qd = bf.EsStore(http, ES, INDEX), bf.QdrantStore(http, QD, INDEX)
        change = bf.Change("lab:gone", "00000000-0000-0000-0000-000000000000", DOI_A,
                           {"authors": NEW_AUTHORS_A}, {})
        assert "missing on re-read" in bf.verify(es, qd, [change])[0]


# --------------------------------------------------------------------------- #
# refusals
# --------------------------------------------------------------------------- #

def test_apply_requires_tenant_env():
    stores = _dengue()
    with pytest.raises(SystemExit, match="--apply needs --tenant-env"):
        _run(stores, "--fields", "authors", "--overwrite", "--apply")
    assert stores.requests == []


def test_routed_index_is_refused_even_with_force(tenant_env):
    tenant_env.write_text(tenant_env.read_text()
                          + f"ES_COLLECTION_ROUTES='{{\"{INDEX}\":\"http://localhost:9200\"}}'\n")
    stores = _dengue()
    with pytest.raises(SystemExit, match="routed to http://localhost:9200"):
        _run(stores, "--apply", "--force", env=tenant_env)
    assert stores.requests == []


def test_index_routed_by_a_sibling_tenant_is_refused(tenant_env):
    sib = tenant_env.parents[2] / "other" / "config" / "tenant.env"
    sib.parent.mkdir(parents=True)
    sib.write_text(f"QDRANT_COLLECTION_ROUTES='{{\"{INDEX}\":\"{QD}\"}}'\n")
    with pytest.raises(SystemExit, match="another tenant reads this store"):
        _run(_dengue(), "--apply", "--force", env=tenant_env)


def test_stores_that_are_not_the_tenants_own_are_refused(tenant_env):
    tenant_env.write_text("QDRANT_URL=http://localhost:6333\nELASTICSEARCH_URL="
                          f"{ES}\n")
    with pytest.raises(SystemExit, match="is not this tenant's QDRANT_URL"):
        _run(_dengue(), "--apply", "--force", env=tenant_env)


def test_index_absent_from_the_given_store_is_refused(tenant_env):
    stores = _dengue()
    stores.es_present = False
    with pytest.raises(SystemExit, match="routed collection"):
        _run(stores, env=tenant_env)


def test_jats_corpus_is_refused_unless_forced(tenant_env):
    jats = FakeStores([dict(_backfilled(DOI_A, "Lovelace A; Turing A"), content_type="article")])
    with pytest.raises(SystemExit, match="JATS corpus"):
        _run(jats, "--fields", "authors", "--overwrite", env=tenant_env)
    assert jats.mutating() == []
    assert _run(jats, "--fields", "authors", "--overwrite", "--force", env=tenant_env) == 0


def test_curated_titles_are_refused_unless_forced(tenant_env):
    curated = FakeStores([{"doi": DOI_A, "title": "From the corpus"} for _ in range(3)])
    with pytest.raises(SystemExit, match="not an upload-built collection"):
        _run(curated, env=tenant_env)
    assert _run(curated, "--force", env=tenant_env) == 0
