"""Read-only chunk-level exporter for the glossary-benchmark round-2 pool builder.

Scrolls one Elasticsearch bench index (point-in-time + ``search_after``) and writes
one chunk per JSONL line in the shape ``_glossbench_r2.read_chunks`` reads::

    {"chunk_id", "doc_id", "content", "start_char", "end_char",
     "metadata": {"source_path", "title"}}

``_source`` is limited to exactly those fields. ``symbol_glossary`` is NEVER
requested: the drafter must stay blind to glossaries.

Safety, all enforced in code:

* nothing defaults: base URL, index name, allow-listed index prefixes and the output
  path come from ``--private-config`` JSON or arguments;
* the index name must start with one of the configured bench prefixes;
* the only requests ever sent are ``POST /<index>/_pit``, ``POST /_search``,
  ``GET|POST /<index>/_count`` and ``DELETE /_pit`` (a transport guard raises on
  anything else, so a bug cannot write);
* output is written atomically (tmp + rename), mode 0600, and refused inside the repo;
* the exported chunk count must equal ``_count``, else no output is published.

The manifest beside the output is counts-only: chunk count, document count, sha256 of
the output, the index as an alias (or a short hash) and the provenance block.

Private config keys: ``es_url``, ``index``, ``allowed_index_prefixes`` (list),
``api_key_file`` (optional), ``index_alias`` (optional), ``out`` (output path).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

_REPO_PY = Path(__file__).resolve().parents[3]
if str(_REPO_PY) not in sys.path:
    sys.path.insert(0, str(_REPO_PY))
REPO = _REPO_PY.parent.resolve()

#: Exactly the fields the reader needs. Deliberately no ``symbol_glossary``.
SOURCE_INCLUDES = ["chunk_id", "doc_id", "content", "start_char", "end_char",
                   "metadata.source_path", "metadata.title"]
PAGE_SIZE = 500
KEEP_ALIVE = "5m"


class ExportError(SystemExit):
    pass


def refuse_in_repo(path: Path, what: str) -> None:
    p = path.resolve()
    if p == REPO or REPO in p.parents:
        raise ExportError(f"refusing to write {what} inside the repository: {path}")


def check_index(index: str, prefixes: list[str]) -> None:
    if not prefixes or not all(isinstance(p, str) and p for p in prefixes):
        raise ExportError("allowed_index_prefixes must be a non-empty list of non-empty strings")
    if not index or "/" in index or "," in index or "*" in index or index.startswith(("_", "-", "+")):
        raise ExportError("index must be a single concrete index name (no wildcard, comma or slash)")
    if not any(index.startswith(p) for p in prefixes):
        raise ExportError("index does not start with an allow-listed bench prefix; refusing")


# ------------------------------------------------------------------ guarded client

def _allowed(method: str, path: str, index: str) -> bool:
    q = re.escape(index)
    rules = [("POST", rf"/{q}/_pit"), ("POST", r"/_search"),
             ("GET", rf"/{q}/_count"), ("POST", rf"/{q}/_count"), ("DELETE", r"/_pit")]
    return any(method == m and re.fullmatch(p, path) for m, p in rules)


class GuardedTransport(httpx.BaseTransport):
    """Wraps a transport; raises on any method/path outside the read-only allow-list."""

    def __init__(self, inner: httpx.BaseTransport, index: str) -> None:
        self.inner, self.index = inner, index
        self.log: list[tuple[str, str]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        m, p = request.method.upper(), request.url.path
        if not _allowed(m, p, self.index):
            raise ExportError(f"guard: {m} {p} is not on the read-only allow-list")
        if m == "POST" and p == "/_search":
            body = json.loads(request.content or b"{}")
            if not isinstance(body.get("pit"), dict) or body.get("_source", {}).get("includes") != SOURCE_INCLUDES:
                raise ExportError("guard: _search must carry a pit and the fixed _source includes")
        self.log.append((m, p))
        return self.inner.handle_request(request)

    def close(self) -> None:
        self.inner.close()


def make_client(url: str, index: str, api_key: str | None = None,
                inner: httpx.BaseTransport | None = None) -> httpx.Client:
    headers = {"Authorization": f"ApiKey {api_key}"} if api_key else {}
    return httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=60.0,
                        transport=GuardedTransport(inner or httpx.HTTPTransport(), index))


# ------------------------------------------------------------------ export

def _ok(r: httpx.Response, what: str) -> dict[str, Any]:
    if r.status_code != 200:
        raise ExportError(f"{what}: HTTP {r.status_code}")
    return r.json()


def es_count(client: httpx.Client, index: str) -> int:
    return int(_ok(client.get(f"/{index}/_count"), "_count")["count"])


def scroll_hits(client: httpx.Client, index: str, page_size: int = PAGE_SIZE) -> Iterator[dict[str, Any]]:
    """Yield every hit's ``_source`` via PIT + search_after; always closes the PIT."""
    pit = _ok(client.post(f"/{index}/_pit", params={"keep_alive": KEEP_ALIVE}), "open pit")["id"]
    try:
        after: list[Any] | None = None
        while True:
            body: dict[str, Any] = {"size": page_size, "pit": {"id": pit, "keep_alive": KEEP_ALIVE},
                                    "sort": [{"_shard_doc": "asc"}], "track_total_hits": False,
                                    "_source": {"includes": SOURCE_INCLUDES}}
            if after is not None:
                body["search_after"] = after
            resp = _ok(client.post("/_search", json=body), "_search")
            pit = resp.get("pit_id") or pit
            hits = resp["hits"]["hits"]
            if not hits:
                return
            yield from hits
            after = hits[-1]["sort"]
    finally:
        client.request("DELETE", "/_pit", json={"id": pit})



def to_row(hit: dict[str, Any]) -> dict[str, Any]:
    src = hit.get("_source") or {}
    cid = src.get("chunk_id") or hit.get("_id")
    start, end = src.get("start_char"), src.get("end_char")
    if not cid or not src.get("doc_id") or src.get("content") is None or start is None or end is None:
        raise ExportError("a chunk lacks chunk_id/doc_id/content/start_char/end_char; refusing a partial export")
    md = src.get("metadata") or {}
    return {"chunk_id": str(cid), "doc_id": str(src["doc_id"]), "content": str(src["content"]),
            "start_char": int(start), "end_char": int(end),
            "metadata": {"source_path": str(md.get("source_path") or ""), "title": str(md.get("title") or "")}}


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def export(client: httpx.Client, index: str, prefixes: list[str], out: Path,
           alias: str | None = None, page_size: int = PAGE_SIZE) -> dict[str, Any]:
    check_index(index, prefixes)
    refuse_in_repo(out, "the chunk export")
    refuse_in_repo(out.with_name(out.name + ".manifest.json"), "the manifest")
    out.parent.mkdir(parents=True, exist_ok=True)
    expected = es_count(client, index)
    rows, seen, docs = [], set(), set()
    for hit in scroll_hits(client, index, page_size):
        row = to_row(hit)
        if row["chunk_id"] in seen:
            raise ExportError("duplicate chunk id in the scroll; refusing")
        seen.add(row["chunk_id"])
        docs.add(row["doc_id"])
        rows.append(row)
    if len(rows) != expected:
        raise ExportError(f"exported {len(rows)} chunks but _count is {expected}; nothing written")
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    from ragstack.provenance import experiment_provenance

    manifest = {"chunks": len(rows), "documents": len(docs), "es_count": expected, "count_matches": True,
                "sha256": sha, "index": alias or "idx-" + hashlib.sha256(index.encode()).hexdigest()[:12],
                "fields": SOURCE_INCLUDES, "symbol_glossary_exported": False,
                "provenance": experiment_provenance(repo=str(REPO))}
    _atomic_write(out, data)
    _atomic_write(out.with_name(out.name + ".manifest.json"),
                  (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--private-config")
    ap.add_argument("--es-url")
    ap.add_argument("--index")
    ap.add_argument("--allowed-prefix", action="append")
    ap.add_argument("--api-key-file")
    ap.add_argument("--index-alias")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    cfg = json.loads(Path(a.private_config).read_text()) if a.private_config else {}
    url, index = a.es_url or cfg.get("es_url"), a.index or cfg.get("index")
    prefixes = a.allowed_prefix or cfg.get("allowed_index_prefixes") or []
    out = a.out or cfg.get("out")
    if not (url and index and out):
        raise SystemExit("es_url, index and out are required (arguments or --private-config); there are no defaults")
    check_index(index, prefixes)
    key_file = a.api_key_file or cfg.get("api_key_file")
    key = Path(key_file).read_text().strip() if key_file else None
    with make_client(url, index, key) as client:
        m = export(client, index, prefixes, Path(out), a.index_alias or cfg.get("index_alias"))
    print(json.dumps({k: m[k] for k in ("chunks", "documents", "sha256", "index")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
