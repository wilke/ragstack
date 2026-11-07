"""Conformance: a by-reference file whose suffix has no loader fails as an item
(#828 PR 5a, #833 items 1-2).

The contract (``POST /v1/ingest`` in ``contracts/openapi.yaml``): a file is
dispatched to a loader by its suffix — ``.pdf``, ``.txt``, ``.md``, ``.jsonl``
and ``.xml`` (a JATS article). A file whose suffix has no loader is still
accepted (200) and becomes a FAILED item with ``no loader for <suffix>``; its
bytes are never read as text. Before this, the in-process registry fell back to
the text loader, so ``x.rst`` was ingested as plain text (#833 item 1) while the
GoWe extract step refused it — two paths, two answers.

Three jobs, each one by-reference file into one scratch collection:

* ``conf-noloader-<hex>.rst`` → job ``failed``, items ``{1, 0, 1, 0}``;
* the SAME bytes as ``.txt`` → job ``completed``, items ``{1, 1, 0, 0}`` — the
  control: the bytes are ingestible, so the ``.rst`` failure is the suffix and
  nothing else (and a server that refused everything fails here);
* a minimal JATS article ``PMC<hex>.xml`` → ``completed``, every item completed,
  none failed (#833 item 2: ``.xml`` has a loader in-process too) — AND its table
  is retrievable as its own document (``filters: {"content_type": "table"}``).
  The status alone cannot tell the JATS loader from the old text fallback (which
  "completed" the raw XML as one text document); the separate table document
  carrying the JATS ``content_type`` key is what only the JATS loader produces.

The per-item error string is not on the wire (``IngestResponse`` exposes counts
only); the Python unit tests pin it. Needs a server whose ``INGEST_ROOT`` this
process can write into — ``make test-conformance-keyed`` boots one and exports
``RAGSTACK_CONFORMANCE_INGEST_ROOT``; elsewhere the module skips. Python-first:
Go's ingest handler is a stub that runs no job (ADR-0006).

Creates one collection (prefix ``conf-noloader-``) and purges it, verified by
listing; removes the files it wrote.
"""
from __future__ import annotations

import os
import secrets
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

SCRATCH_PREFIX = "conf-noloader-"
PARAGRAPH = (
    "The synthetic conformance corpus describes a measurement procedure in some "
    "detail, so that the document is long enough to be cut into several chunks. "
)

# A minimal JATS article: front matter, one prose section, one table. The shape
# is copied from python/tests/ingestion/test_jats_extract.py (`_article`,
# `FRONT`, `TABLE_1`) — inline, because conformance imports nothing from python/.
JATS_ARTICLE = """<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink" article-type="research-article">
  <front>
    <journal-meta>
      <journal-title-group><journal-title>J Synthetic Res</journal-title></journal-title-group>
      <publisher><publisher-name>Test Publisher</publisher-name></publisher>
    </journal-meta>
    <article-meta>
      <article-id pub-id-type="pmc">{pmcid}</article-id>
      <title-group><article-title>A synthetic conformance article</article-title></title-group>
      <contrib-group>
        <contrib contrib-type="author">
          <name><surname>Doe</surname><given-names>Jane</given-names></name>
        </contrib>
        <contrib contrib-type="author">
          <name><surname>Roe</surname><given-names>Rick</given-names></name>
        </contrib>
      </contrib-group>
      <pub-date pub-type="epub"><year>2024</year></pub-date>
      <abstract><p>The abstract describes the synthetic conformance study in one sentence.</p></abstract>
      <kwd-group><kwd>synthetic</kwd><kwd>jats</kwd></kwd-group>
    </article-meta>
  </front>
  <body>
    <sec>
      <title>Results</title>
      <p>{prose}</p>
      <table-wrap id="T1">
        <label>Table 1</label>
        <caption><p>Counts by treatment group.</p></caption>
        <table>
          <thead><tr><th>Group</th><th>Count</th></tr></thead>
          <tbody>
            <tr><td>ZZCELLZZ</td><td>17</td></tr>
            <tr><td>control</td><td>19</td></tr>
          </tbody>
        </table>
      </table-wrap>
    </sec>
  </body>
</article>
"""


class _Scratch:
    def __init__(self, client: httpx.Client, root: Path, cid: str) -> None:
        self.client, self.root, self.cid = client, root, cid
        self.written: list[Path] = []

    def ingest(self, name: str, text: str, timeout: float = 30.0) -> dict:
        """Write ``name`` under INGEST_ROOT, ingest it by reference into the
        scratch collection, poll the job to a terminal status, return its body."""
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        self.written.append(path)
        resp = self.client.post("/v1/ingest", json={"source": str(path), "collection": self.cid})
        assert resp.status_code == 200, f"{resp.status_code} {resp.text[:300]}"
        job = resp.json()["job_id"]
        deadline = time.monotonic() + timeout
        while True:
            body = self.client.get(f"/v1/ingest/{job}").json()
            if body["status"] in ("completed", "failed"):
                return body
            assert time.monotonic() < deadline, f"job {job} did not finish: {body}"
            time.sleep(0.2)


@pytest.fixture(scope="module")
def scratch(base_url: str, impl: str, auth_headers: dict[str, str]) -> Iterator[_Scratch]:
    if impl != "python":
        pytest.skip("ingest is Python-first; the Go handler is a stub that runs no job (ADR-0006)")
    root = os.environ.get("RAGSTACK_CONFORMANCE_INGEST_ROOT")
    if not root:
        pytest.skip("needs a server whose INGEST_ROOT this suite can write into "
                    "(make test-conformance-keyed exports RAGSTACK_CONFORMANCE_INGEST_ROOT)")
    cid = f"{SCRATCH_PREFIX}{secrets.token_hex(4)}"
    with httpx.Client(base_url=base_url, timeout=30.0, headers=auth_headers) as c:
        created = c.post("/v1/collections", json={"id": cid})
        assert created.status_code == 201, f"{created.status_code} {created.text[:300]}"
        s = _Scratch(c, Path(root), cid)
        try:
            yield s
        finally:
            c.delete(f"/v1/collections/{cid}?purge=true")
            for p in s.written:
                p.unlink(missing_ok=True)
            listed = [e["id"] for e in c.get("/v1/collections").json()["collections"]]
            assert cid not in listed, f"teardown did not remove {cid!r}; delete it by hand"


def test_a_suffix_with_no_loader_is_a_failed_item_not_text(scratch: _Scratch) -> None:
    body = scratch.ingest(f"{scratch.cid}.rst", PARAGRAPH * 20)
    assert body["status"] == "failed", body
    assert body["items"] == {"total": 1, "completed": 0, "failed": 1, "pending": 0}, body


def test_the_same_bytes_as_txt_complete(scratch: _Scratch) -> None:
    """Control for the test above: identical bytes, a suffix with a loader."""
    body = scratch.ingest(f"{scratch.cid}.txt", PARAGRAPH * 20)
    assert body["status"] == "completed", body
    assert body["items"] == {"total": 1, "completed": 1, "failed": 0, "pending": 0}, body


def test_a_jats_xml_article_completes(scratch: _Scratch) -> None:
    pmcid = f"PMC{secrets.token_hex(4)}"
    body = scratch.ingest(f"{pmcid}.xml", JATS_ARTICLE.format(pmcid=pmcid, prose=PARAGRAPH * 10))
    assert body["status"] == "completed", body
    items = body["items"]
    assert items["total"] >= 1, body
    assert items["failed"] == 0 and items["pending"] == 0, body
    assert items["completed"] == items["total"], body
    # The JATS loader, not the text fallback: the table is its own document,
    # carrying the JATS passthrough key `content_type`. The fallback ingests the
    # raw XML as one document with no `content_type`, so this filter is empty.
    resp = scratch.client.post("/v1/retrieve", json={
        "query": "counts by treatment group", "collection": scratch.cid, "top_k": 5,
        "filters": {"content_type": "table"},
    })
    assert resp.status_code == 200, resp.text
    sources = resp.json()["sources"]
    assert sources, "no content_type=table document: the .xml was not loaded as JATS"
    assert all(s["metadata"].get("content_type") == "table" for s in sources), sources
    assert any("ZZCELLZZ" in s["content"] for s in sources), sources
