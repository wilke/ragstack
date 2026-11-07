"""``jats_extract.py`` writes the ``jats/1`` structure key (#828 §4, PR 5b part iii).

Every shard record — article and table/figure unit alike — carries its
validated document structure as the LAST top-level key, ``structure``, never
inside ``metadata``; ``JsonlLoader`` restores it as ``adapter == "jats/1"``. A
structure that cannot be built is dropped (counted, one stderr line) and the
record is written exactly as before the key existed. ``--spans-out`` is a
debugging side file that leaves the shard byte-identical. End to end:
``jats_extract`` → shard → ``ingest_shard`` under a registry entry with
``structure_cuts`` → chunks that respect the ``<sec>`` walls and are stamped
with the ``<sec>`` titles, the declared roles and the unit labels.

Offline: synthetic JATS, a scratch sqlite registry, in-memory stores, a fake
embedder, dead store URLs.
"""
from __future__ import annotations

import asyncio
import bisect
import json
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ragstack.ingestion import jats, jats_structure
from ragstack.ingestion.jats_structure import ADAPTER
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.receipts import ShardReceipt
from ragstack.ingestion.structure import DECLARED_VERSION
from tests.unit.test_embed_shard_registry import (  # noqa: F401 — the fixture
    CID,
    DEAD,
    REG,
    _spec,
    registry,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jats_extract  # noqa: E402
from test_jats_extract import FIG_1, TABLE_1, _article  # noqa: E402

_METHODS = " ".join(f"Cells in flask {i} were grown at thirty seven degrees for two days."
                    for i in range(6))
_RESULTS = " ".join(f"Measurement {i} agreed with the model within the stated error."
                    for i in range(6))

BODY = f"""
  <sec sec-type="intro"><title>Introduction</title>
    <p>The introduction motivates the synthetic study in a few plain sentences.</p></sec>
  <sec sec-type="materials|methods"><title>Materials and Methods</title>
    <p>The methods lead paragraph states the overall design of the study.</p>
    <sec><title>Cell culture</title><p>{_METHODS}</p></sec>
    <sec><title>Statistics</title><p>A paired comparison was run on every quantity.</p></sec>
  </sec>
  <sec sec-type="results"><title>Results</title>
    <p>{_RESULTS} The counts are in Table 1 and the survival in Figure 1.</p>{TABLE_1}</sec>
"""

XML = _article("77", BODY, FIG_1)


def _corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    clean = corpus / "clean" / "ab"
    clean.mkdir(parents=True)
    (clean / "PMC77.xml").write_text(XML, encoding="utf-8")
    (corpus / "manifest.jsonl").write_text("", encoding="utf-8")
    return corpus


def _extract(tmp_path: Path, *extra: str, name: str = "shard.jsonl") -> tuple[Path, dict]:
    corpus = tmp_path / "corpus" if (tmp_path / "corpus").exists() else _corpus(tmp_path)
    out, report = tmp_path / name, tmp_path / f"{name}.report.json"
    assert jats_extract.main(["--corpus", str(corpus), "--out", str(out),
                              "--report", str(report), "--no-token-budget",
                              "--max-fail-rate", "0", *extra]) == 0
    return out, json.loads(report.read_text(encoding="utf-8"))


def _lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


# ----------------------------------------------------------------- the key

def test_every_record_carries_the_key_last_and_never_in_metadata(tmp_path):
    """MUTATION: write the structure into ``metadata`` → fails."""
    shard, report = _extract(tmp_path)
    recs = _lines(shard)
    kinds = Counter(r["metadata"]["content_type"] for r in recs)
    assert kinds == {"article": 1, "table": 1, "figure": 1}
    for r in recs:
        assert list(r) == ["text", "path", "metadata", "structure"]
        assert "structure" not in r["metadata"]
        assert r["structure"]["adapter"] == ADAPTER == "jats/1"
    assert report["n_structured"] == 3 and report["structure_dropped"] == {}

    loader = JsonlLoader()
    docs = loader.load(str(shard))
    assert len(docs) == 3
    assert all(d.structure is not None and d.structure.adapter == "jats/1" for d in docs)
    assert not loader.dropped.structure_invalid
    by_path = {d.source: d for d in docs}
    table = by_path["PMC77#table-1"].structure
    assert table.sections[0].title == "Table 1"
    assert [b.kind for b in table.blocks] == ["heading", "table_caption", "table"]
    assert by_path["PMC77#figure-1"].structure.blocks[1].label == "Figure 1"


def _plain_records() -> dict[str, dict]:
    """The records as the parser returns them: the pre-#828 record shape."""
    recs, _ = jats.article_records(ET.fromstring(XML), "PMC77", {}, min_unit_chars=40)
    return {r["path"]: r for r in recs}


def test_a_failed_adapter_drops_the_structure_not_the_record(tmp_path, monkeypatch, capsys):
    """MUTATION: let the adapter's exception escape → main raises → fails."""
    def boom(text, spans):
        raise ValueError("synthetic adapter failure")

    # The adapter is called by jats_structure.record_structure (#828 PR 5a
    # moved the build+validate there, shared with JatsLoader): patch it there.
    monkeypatch.setattr(jats_structure, "article_structure", boom)
    shard, report = _extract(tmp_path)
    lines = shard.read_text(encoding="utf-8").splitlines()
    recs = {json.loads(x)["path"]: x for x in lines}
    plain = _plain_records()
    # Byte-identical to the record shape from before the key existed.
    assert recs["PMC77"] == json.dumps(plain["PMC77"], ensure_ascii=False)
    assert "structure" in json.loads(recs["PMC77#table-1"])     # the units are unaffected
    assert report["n_structured"] == 2
    assert report["structure_dropped"] == {"ValueError": 1}
    err = capsys.readouterr().err
    assert "PMC77: structure dropped (ValueError)" in err


def test_an_invalid_structure_is_dropped_under_the_validators_reason(tmp_path, monkeypatch):
    real = jats_structure.unit_structure

    def stale(text, layout, **kw):
        return real(text + "x", layout, **kw)                  # bound to another text

    monkeypatch.setattr(jats_structure, "unit_structure", stale)
    shard, report = _extract(tmp_path)
    recs = {r["path"]: r for r in _lines(shard)}
    plain = _plain_records()
    for path in ("PMC77#table-1", "PMC77#figure-1"):
        assert recs[path] == plain[path] and "structure" not in recs[path]
    assert report["structure_dropped"] == {"content_sha256": 2}
    assert report["n_structured"] == 1


def test_spans_out_leaves_the_shard_byte_identical(tmp_path):
    plain, _ = _extract(tmp_path, name="a.jsonl")
    rows = tmp_path / "spans.jsonl"
    with_spans, _ = _extract(tmp_path, "--spans-out", str(rows), name="b.jsonl")
    assert plain.read_bytes() == with_spans.read_bytes()
    (row,) = _lines(rows)
    article = next(r for r in _lines(plain) if r["path"] == "PMC77")
    sec_paths = [s["path"] for s in row["spans"] if s["kind"] == "sec"]
    assert sec_paths == [s["locator"] for s in article["structure"]["sections"]
                         if s["locator"] not in (None, "abstract")]


# -------------------------------------------------------------- end to end

class _Embedder:
    async def embed(self, texts):  # noqa: ANN001
        return [[float(len(t)), 1.0, 2.0, 3.0] for t in texts]


def test_end_to_end_extract_then_ingest_with_structure_cuts(
    tmp_path, registry, monkeypatch  # noqa: F811
):
    """MUTATION: ``unit_structure`` with ``title=None`` → the table chunk's
    ``section_title`` is not ``Table 1`` → fails."""
    pytest.importorskip("mdit_py_plugins", reason="the [markdown] extra is not installed")
    from ragstack.ingestion.chunkers import doc_cut_segments
    from ragstack.ops import ingest_target as it

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import ingest_shard

    shard, _ = _extract(tmp_path)
    monkeypatch.delenv("COLLECTION_MANIFEST_DIR", raising=False)
    registry(_spec(chunk_size=200, chunk_overlap=0,
                   chunk_params={"structure_cuts": "sections/1"}))
    target = it.resolve(CID, registry=REG)
    emb, receipt_path = tmp_path / "e.emb.jsonl", tmp_path / "e.receipt.json"
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _Embedder())
    args = ingest_shard.parse_args([
        str(shard), "--vector-backend", "memory", "--text-backend", "memory",
        "--qdrant-url", DEAD, "--es-url", DEAD,
        "--chunk-method", "sentence", "--chunk-size", "200", "--chunk-overlap", "0",
        "--embedding-model", "", "--chunk-token-counter", "estimate",
        "--chunk-max-tokens", "4096", "--symbol-glossary", "off",
        "--embedding-file", str(emb), "--out", str(receipt_path)])
    assert asyncio.run(ingest_shard.amain(args, target)) == 0

    docs = {d.id: d for d in JsonlLoader().load(str(shard))}
    chunks = [json.loads(x) for x in emb.read_text(encoding="utf-8").splitlines()[1:]
              if x.strip()]
    article = next(d for d in docs.values() if d.source == "PMC77")
    mine = [c for c in chunks if c["doc_id"] == article.id]
    assert len(mine) >= 6

    # Every article chunk lies inside ONE cut segment (no tolerance: no merges).
    segs = doc_cut_segments(article)
    assert segs is not None
    starts = [g.start for g in segs]
    for c in mine:
        s, e = c["start_char"], c["end_char"]
        assert article.content[s:e] == c["content"]
        assert bisect.bisect_right(starts, s) == bisect.bisect_right(starts, e - 1), c
    # section_title is the <sec> title of a section holding the chunk.
    sections = article.structure.sections
    titled = 0
    for c in mine:
        title = c["metadata"].get("section_title")
        if title is None:
            continue
        titled += 1
        assert any(x.title == title and x.start <= c["start_char"] and c["end_char"] <= x.end
                   for x in sections), c
    seen = {c["metadata"].get("section_title") for c in mine}
    assert {"Introduction", "Cell culture", "Statistics", "Results"} <= seen
    assert not any(t and t.startswith(("table-", "figure-")) for t in seen)
    # Declared roles: a chunk under the materials|methods section.
    culture = [c for c in mine if c["metadata"].get("section_title") == "Cell culture"]
    assert culture
    for c in culture:
        assert {"materials", "methods"} <= set(c["metadata"]["section_roles"])
        assert DECLARED_VERSION in c["metadata"]["section_roles_version"]
        assert c["metadata"]["section_path"] == ["Materials and Methods", "Cell culture"]
    intro = [c for c in mine if c["metadata"].get("section_title") == "Introduction"]
    assert intro and all(c["metadata"]["section_roles"] == ["introduction"] for c in intro)
    # Unit chunks: the caption label is the section title (Q13), not the suffix.
    by_source = {d.id: d.source for d in docs.values()}
    units = {by_source[c["doc_id"]]: c for c in chunks if c["doc_id"] != article.id}
    assert units["PMC77#table-1"]["metadata"]["section_title"] == "Table 1"
    assert units["PMC77#table-1"]["metadata"]["caption_labels"] == ["Table 1"]
    assert units["PMC77#figure-1"]["metadata"]["section_title"] == "Figure 1"
    for c in chunks:
        assert c["metadata"]["structure_version"] == "jats/1;stamp/1"
    # The receipt reports the jats/1 structures.
    receipt = ShardReceipt.load(receipt_path)
    record = receipt.enrichment["structure"]  # type: ignore[index]
    assert record["adapters"]["jats/1"] == 3 and record["structure_missing"] == 0
    assert receipt.chunking["structure_cuts"] == "sections/1"  # type: ignore[index]
