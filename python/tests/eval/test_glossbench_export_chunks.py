"""Hermetic tests for the round-2 chunk exporter (httpx.MockTransport; no network)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "eval" / "glossbench"))
import export_chunks as ec  # noqa: E402

PREFIXES = ["bench-"]
INDEX = "bench-synth-1"


def _src(i: int) -> dict:
    # Row shape copied from the round-2 drafting fixtures (glossbench_r2_support.chunk_rows, PR #863):
    # id/doc_id/content/start_char/end_char/metadata{source_path,title}; ES names the id chunk_id.
    return {"chunk_id": f"d{i // 3}-c{i}", "doc_id": f"d{i // 3}", "content": f"text {i} Qb = {i} m/s",
            "start_char": i * 10, "end_char": i * 10 + 9,
            "metadata": {"source_path": f"PMC{i // 3}", "title": f"Boat paper {i // 3}"}}


class FakeES:
    def __init__(self, n: int, count: int | None = None) -> None:
        self.docs = [_src(i) for i in range(n)]
        self.count = n if count is None else count
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        body = json.loads(req.content) if req.content else {}
        self.bodies.append(body)
        if req.url.path.endswith("/_pit") and req.method == "POST":
            return httpx.Response(200, json={"id": "PIT1"})
        if req.url.path.endswith("/_count"):
            return httpx.Response(200, json={"count": self.count})
        if req.method == "DELETE":
            return httpx.Response(200, json={"succeeded": True})
        size, start = body["size"], (body.get("search_after") or [-1])[0] + 1
        part = self.docs[start:start + size]
        hits = [{"_id": d["chunk_id"], "_source": d, "sort": [start + k]} for k, d in enumerate(part)]
        return httpx.Response(200, json={"pit_id": "PIT1", "hits": {"hits": hits}})


def client(fake: FakeES) -> httpx.Client:
    return ec.make_client("http://es.invalid", INDEX, "k", inner=httpx.MockTransport(fake))


def run(fake: FakeES, tmp_path: Path, page: int = 4, index: str = INDEX):
    return ec.export(client(fake), index, PREFIXES, tmp_path / "out" / "chunks.jsonl", "docs-A", page)


def test_pagination_and_manifest(tmp_path: Path) -> None:
    fake = FakeES(10)
    m = run(fake, tmp_path)
    out = tmp_path / "out" / "chunks.jsonl"
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["chunk_id"] for r in rows] == [d["chunk_id"] for d in fake.docs]
    assert sum(1 for c in fake.calls if c[1] == "/_search") == 4  # 4+4+2+empty
    assert m["chunks"] == 10 and m["documents"] == 4 and m["index"] == "docs-A"
    assert m["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert m["symbol_glossary_exported"] is False and "provenance" in m
    man = json.loads((tmp_path / "out" / "chunks.jsonl.manifest.json").read_text())
    assert "Qb =" not in json.dumps(man) and INDEX not in json.dumps(man)
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert not list((tmp_path / "out").glob("*.tmp*"))


def test_source_includes_exclude_glossary(tmp_path: Path) -> None:
    fake = FakeES(3)
    run(fake, tmp_path)
    for b in fake.bodies:
        if "size" in b:
            assert "symbol_glossary" not in json.dumps(b["_source"])
            assert b["_source"]["includes"] == ec.SOURCE_INCLUDES


def test_pit_closed_and_only_allowed_calls(tmp_path: Path) -> None:
    fake = FakeES(5)
    run(fake, tmp_path)
    assert fake.calls[-1] == ("DELETE", "/_pit")
    assert {(m, re.sub(INDEX, "IDX", p)) for m, p in fake.calls} <= {
        ("GET", "/IDX/_count"), ("POST", "/IDX/_pit"), ("POST", "/_search"), ("DELETE", "/_pit")}


def test_count_mismatch_refuses_and_writes_nothing(tmp_path: Path) -> None:
    fake = FakeES(10, count=11)
    with pytest.raises(SystemExit, match="_count"):
        run(fake, tmp_path)
    assert not (tmp_path / "out" / "chunks.jsonl").exists()
    assert fake.calls[-1] == ("DELETE", "/_pit")


def test_non_bench_index_refused_before_any_call(tmp_path: Path) -> None:
    fake = FakeES(3)
    with pytest.raises(SystemExit, match="allow-listed"):
        run(fake, tmp_path, index="prod-collection")
    assert fake.calls == []
    for bad in ("bench-*", "bench-a,prod", "bench-a/_all", "_all"):
        with pytest.raises(SystemExit):
            ec.check_index(bad, PREFIXES)
    with pytest.raises(SystemExit):
        ec.check_index(INDEX, [])


@pytest.mark.parametrize("method,path", [
    ("PUT", f"/{INDEX}/_doc/1"), ("POST", f"/{INDEX}/_doc"), ("DELETE", f"/{INDEX}"),
    ("POST", f"/{INDEX}/_delete_by_query"), ("POST", "/_bulk"), ("POST", f"/{INDEX}/_update/1"),
    ("GET", "/_cluster/settings"), ("POST", "/other-index/_pit"), ("DELETE", "/_pit/extra"),
    ("POST", f"/{INDEX}/_search"), ("PUT", "/_pit")])
def test_guard_rejects_everything_else(method: str, path: str) -> None:
    fake = FakeES(1)
    with pytest.raises(SystemExit, match="guard"):
        client(fake).request(method, path, json={})
    assert fake.calls == []


def test_guard_rejects_search_without_fixed_includes() -> None:
    fake = FakeES(1)
    with pytest.raises(SystemExit, match="guard"):
        client(fake).post("/_search", json={"pit": {"id": "x"}, "_source": {"includes": ["symbol_glossary"]}})
    with pytest.raises(SystemExit, match="guard"):
        client(fake).post("/_search", json={"_source": {"includes": ec.SOURCE_INCLUDES}})


def test_in_repo_output_refused(tmp_path: Path) -> None:
    fake = FakeES(2)
    with pytest.raises(SystemExit, match="inside the repository"):
        ec.export(client(fake), INDEX, PREFIXES, ec.REPO / "chunks.jsonl")
    assert fake.calls == []
    link = tmp_path / "ln"
    link.symlink_to(ec.REPO)
    with pytest.raises(SystemExit, match="inside the repository"):
        ec.export(client(fake), INDEX, PREFIXES, link / "chunks.jsonl")


def test_incomplete_row_and_duplicate_refused(tmp_path: Path) -> None:
    fake = FakeES(3)
    del fake.docs[1]["end_char"]
    with pytest.raises(SystemExit, match="partial"):
        run(fake, tmp_path)
    fake = FakeES(3)
    fake.docs[2]["chunk_id"] = fake.docs[0]["chunk_id"]
    with pytest.raises(SystemExit, match="duplicate"):
        run(fake, tmp_path)
    assert not (tmp_path / "out" / "chunks.jsonl").exists()


def _reader_contract(obj: dict) -> tuple:
    """Replica of ``parse_chunk``/``read_chunks`` in PR #863 (_glossbench_r2.py)."""
    src = obj.get("_source") if isinstance(obj.get("_source"), dict) else obj
    cid = src.get("chunk_id") or src.get("id") or obj.get("_id")
    doc, content = src.get("doc_id"), src.get("content")
    start, end = src.get("start_char"), src.get("end_char")
    assert cid and doc and content is not None and start is not None and end is not None
    md = src.get("metadata") or {}
    return str(cid), str(doc), str(content), int(start), int(end), str(md.get("source_path") or ""), str(md.get("title") or "")


def test_output_readable_by_pool_reader(tmp_path: Path) -> None:
    run(FakeES(7), tmp_path)
    lines = (tmp_path / "out" / "chunks.jsonl").read_text().splitlines()
    parsed = [_reader_contract(json.loads(line)) for line in lines]
    assert len(parsed) == 7 and len({p[0] for p in parsed}) == 7
    assert parsed[0][5] == "PMC0" and parsed[0][6] == "Boat paper 0"
    assert set(json.loads(lines[0])) == {"chunk_id", "doc_id", "content", "start_char", "end_char", "metadata"}


def test_cli_requires_config_no_defaults(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no defaults"):
        ec.main([])
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"es_url": "http://es.invalid", "index": "prod-x",
                               "allowed_index_prefixes": ["bench-"], "out": str(tmp_path / "o.jsonl")}))
    with pytest.raises(SystemExit, match="allow-listed"):
        ec.main(["--private-config", str(cfg)])


# --- --with-glossary: the reranker test's chunk dump (post-freeze tooling addition) ---------

def _src_full(i: int, n: int) -> dict:
    """A chunk as the text index stores it: the glossary at top level, its stamp and the
    neighbour links in metadata (``ragstack.stores.elasticsearch`` / ``metadata_schema``)."""
    d = _src(i)
    doc, first, last = i // 3, (i // 3) * 3, min((i // 3) * 3 + 2, n - 1)
    if i % 2 == 0:
        d["symbol_glossary"] = f"Qb — breathing rate {i}"
    d["metadata"].update({"symbol_glossary_version": "rules/3@file/1", "tenant_id": "t",
                          "prev_chunk_id": f"d{doc}-c{i - 1}" if i > first else None,
                          "next_chunk_id": f"d{doc}-c{i + 1}" if i < last else None})
    return d


def _filter(src: dict, includes: list[str]) -> dict:
    """``_source.includes`` as Elasticsearch applies it (top-level and ``metadata.*`` paths)."""
    out: dict = {}
    for f in includes:
        if f.startswith("metadata."):
            k = f.split(".", 1)[1]
            if k in (src.get("metadata") or {}):
                out.setdefault("metadata", {})[k] = src["metadata"][k]
        elif f in src:
            out[f] = src[f]
    return out


class FilteringES(FakeES):
    """FakeES whose documents carry the glossary and which honours ``_source.includes``."""

    def __init__(self, n: int) -> None:
        super().__init__(n)
        self.docs = [_src_full(i, n) for i in range(n)]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        resp = super().__call__(req)
        body = self.bodies[-1]
        if req.url.path == "/_search":
            data = resp.json()
            for h in data["hits"]["hits"]:
                h["_source"] = _filter(h["_source"], body["_source"]["includes"])
            return httpx.Response(200, json=data)
        return resp


def gclient(fake: FakeES) -> httpx.Client:
    return ec.make_client("http://es.invalid", INDEX, "k", inner=httpx.MockTransport(fake), with_glossary=True)


def test_default_mode_never_requests_the_glossary_even_when_the_index_has_one(tmp_path: Path) -> None:
    fake = FilteringES(9)
    run(fake, tmp_path)
    searches = [b for b in fake.bodies if "size" in b]
    assert searches and all(b["_source"]["includes"] == ec.SOURCE_INCLUDES for b in searches)
    assert all("symbol_glossary" not in json.dumps(b) and "_chunk_id" not in json.dumps(b) for b in searches)
    rows = [json.loads(x) for x in (tmp_path / "out" / "chunks.jsonl").read_text().splitlines()]
    assert all("symbol_glossary" not in json.dumps(r) and "prev_chunk_id" not in r["metadata"] for r in rows)
    # the glossary code path exists, but a default-mode client cannot carry its field list
    with pytest.raises(SystemExit, match="guard"):
        ec.export(client(FilteringES(3)), INDEX, PREFIXES, tmp_path / "o" / "dump-x.jsonl", with_glossary=True)
    with pytest.raises(SystemExit, match="guard"):
        client(fake).post("/_search", json={"pit": {"id": "x"},
                                            "_source": {"includes": ec.GLOSSARY_SOURCE_INCLUDES}})


def test_with_glossary_dump_rows_and_manifest(tmp_path: Path) -> None:
    fake = FilteringES(7)
    out = tmp_path / "out" / "dump-docs-A.jsonl"
    m = ec.export(gclient(fake), INDEX, PREFIXES, out, "docs-A", 3, with_glossary=True)
    assert all(b["_source"]["includes"] == ec.GLOSSARY_SOURCE_INCLUDES for b in fake.bodies if "size" in b)
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert rows[0]["symbol_glossary"] == "Qb — breathing rate 0" and rows[1]["symbol_glossary"] == ""
    assert rows[1]["metadata"] == {"source_path": "PMC0", "title": "Boat paper 0",
                                   "symbol_glossary_version": "rules/3@file/1",
                                   "prev_chunk_id": "d0-c0", "next_chunk_id": "d0-c2"}
    assert rows[0]["metadata"]["prev_chunk_id"] is None and rows[6]["metadata"]["next_chunk_id"] is None
    assert "tenant_id" not in rows[0]["metadata"]
    assert m["symbol_glossary_exported"] is True and m["fields"] == ec.GLOSSARY_SOURCE_INCLUDES
    assert m["chunks_with_glossary"] == 4 and m["glossary_versions"] == {"rules/3@file/1": 7}
    assert m["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


@pytest.mark.parametrize("name,with_glossary", [
    ("chunks-docs-A.jsonl", True), ("docs-A.jsonl", True), ("dump-docs-A.jsonl", False)])
def test_mode_and_output_name_must_agree(tmp_path: Path, name: str, with_glossary: bool) -> None:
    fake = FilteringES(3)
    c = gclient(fake) if with_glossary else client(fake)
    with pytest.raises(SystemExit, match="dump-"):
        ec.export(c, INDEX, PREFIXES, tmp_path / name, with_glossary=with_glossary)
    assert fake.calls == [] and not (tmp_path / name).exists()


def test_cli_with_glossary_refuses_a_chunks_name_before_any_call(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"es_url": "http://127.0.0.1:1", "index": INDEX, "allowed_index_prefixes": PREFIXES,
                               "out": str(tmp_path / "chunks-docs-A.jsonl")}))
    with pytest.raises(SystemExit, match="dump-"):
        ec.main(["--private-config", str(cfg), "--with-glossary"])


def test_dump_is_readable_by_the_reranker_harness(tmp_path: Path) -> None:
    """The dump through ``reranker_test.py``'s own chunk-dump reader (``--chunks``)."""
    import argparse
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "eval"))
    import reranker_test as rt

    out = tmp_path / "out" / "dump-docs-A.jsonl"
    ec.export(gclient(FilteringES(7)), INDEX, PREFIXES, out, "docs-A", 4, with_glossary=True)
    cp = rt.load_corpus(argparse.Namespace(chunks=str(out), part4_arm=None, private_config=None))
    assert [c.id for c in cp.chunks] == [f"d{i // 3}-c{i}" for i in range(7)]
    assert cp.by_id["d0-c0"].glossary == "Qb — breathing rate 0" and cp.by_id["d0-c1"].glossary == ""
    assert cp.by_id["d0-c0"].doc_id == "d0" and cp.by_id["d0-c0"].content == "text 0 Qb = 0 m/s"
    assert cp.neighbours["d0-c1"] == {"d0-c0", "d0-c2"} and cp.neighbours["d0-c0"] == {"d0-c1"}
    assert "d2-c6" not in cp.neighbours  # a one-chunk document has no links
    assert cp.meta["chunks_with_glossary"] == 4 and cp.meta["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    # the live reranker passage is built from the dumped glossary
    assert rt.render(cp.by_id["d0-c0"], "content+gloss").endswith("Qb — breathing rate 0")
