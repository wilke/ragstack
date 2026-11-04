"""#828 PR 7: ``structure_cuts`` and ``embed_heading_path`` on every ingest path.

One registry entry, one structured shard (plus an unstructured neighbour),
four paths: the in-process pipeline (path A/B's chunk loop, as the API builds
it), ``ingest_shard.py`` (path B), ``embed_shard.py`` resolved through
``--collection-id`` (path C) and ``ingest_jsonl.py`` (path D). They must agree
on chunk text, offsets, vectors and stamps. ``STRUCTURE_METADATA`` is OFF on
every path: the markers alone force the structure step (§6).

* ``embed_heading_path`` changes the vectors only: stored content, offsets and
  ids equal the unmarked run's, the embedding file's ``content`` has no prefix,
  and every vector is the embedding of ``section path + "\\n" + content``.
* ``structure_cuts``: no chunk crosses a segment edge on any path; the receipt
  and the header record the marker (and, on ``sentence``, the #831 tolerance).
* ``ingest_jsonl``'s segmentation cache does not serve a cut run from an
  uncut one, nor one structure's spans to another.

Path C covers fixed_token/sentence/words only: ``embed_shard.py`` refuses the
semantic methods (#609). Offline: fake embedders, in-memory stores, a scratch
sqlite registry, dead store URLs. Needs the ``[markdown]`` extra (a marker
that needs structure requires the parser at configuration).
"""
from __future__ import annotations

import asyncio
import bisect
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mdit_py_plugins", reason="the [markdown] extra is not installed")

from ragstack.ingestion.chunker_config import build_chunker, structure_from_params  # noqa: E402
from ragstack.ingestion.chunkers import doc_cut_segments  # noqa: E402
from ragstack.ingestion.embedding_file import read_header  # noqa: E402
from ragstack.ingestion.loaders import JsonlLoader  # noqa: E402
from ragstack.ingestion.pipeline import IngestionPipeline  # noqa: E402
from ragstack.ingestion.receipts import ShardReceipt  # noqa: E402
from ragstack.ingestion.segmentation_cache import (  # noqa: E402
    SegmentationCache,
    structure_cache_key,
)
from ragstack.ingestion.structure import STAMP_KEYS  # noqa: E402
from ragstack.models import Document  # noqa: E402
from ragstack.ops import ingest_target as it  # noqa: E402
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore  # noqa: E402
from tests.structure_support import Emitter, paper, record, write_shard  # noqa: E402
from tests.unit.test_embed_shard_registry import (  # noqa: E402,F401 — the fixture
    CID,
    DEAD,
    REG,
    _spec,
    registry,
)
from tests.unit.test_ingest_jsonl_reingest import _args, _FakeStore  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import embed_shard  # noqa: E402
import ingest_jsonl  # noqa: E402
import ingest_shard  # noqa: E402

PREFIX = {"embed_heading_path": "prefix/1"}
CUTS = {"structure_cuts": "sections/1"}
BOTH = {**CUTS, **PREFIX}
TOLERANT = {**BOTH, "size_unit": "tokens", "size_tolerance": 0.25, "min_piece": 10}


class _Embedder:
    """Deterministic on the text, so "embeds identically" — and "embedded the
    prefix" — is observable."""

    async def embed(self, texts):  # noqa: ANN001
        return [_vec(t) for t in texts]


def _vec(t: str) -> list[float]:
    return [float(len(t)), float(sum(map(ord, t)) % 9973), 1.0, 2.0]


def _sizing(params: dict) -> tuple[int, int, str]:
    if params.get("size_unit") == "tokens":
        return 40, 0, "tokens"
    return 200, 0, "chars"


def _long_paper() -> tuple[str, object]:
    """paper() plus a nested section long enough to split."""
    em = Emitter()
    em.section(None, 1)
    em.block("A synthetic preamble paragraph that has no heading of its own.")
    em.section("Methods", 1)
    em.section("Sample preparation", 2)
    em.block(" ".join(f"Samples in batch {i} were prepared with care and kept cold."
                      for i in range(8)))
    em.section("Statistics", 2)
    em.block("A paired comparison was run on every measured quantity.")
    em.section("Results and Discussion", 1)
    em.block(" ".join(f"Result {i} agrees with the expectation stated earlier." for i in range(6)))
    return em.build()


def _shard(tmp_path: Path) -> Path:
    content, structure = _long_paper()
    shard = tmp_path / "s.jsonl"
    write_shard(shard, [record(content, structure),
                        record("An unstructured neighbour document. " * 12, None,
                               path="/corpus/plain.pdf")])
    return shard


def _rows(chunks) -> dict:
    out = {}
    for c in chunks:
        if isinstance(c, dict):
            text, s, e = c["content"], c["start_char"], c["end_char"]
            vec, meta = c["embedding"], c["metadata"]
        else:
            text, s, e, vec, meta = c.content, c.start_char, c.end_char, c.embedding, c.metadata
        out[(text, s, e)] = (list(vec) if vec is not None else None,
                             {k: meta[k] for k in STAMP_KEYS if k in meta})
    return out


def _emb_rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()[1:]
            if x.strip()]


def _tool_size(params: dict) -> list[str]:
    size, overlap, unit = _sizing(params)
    return ["--chunk-method", "sentence", "--chunk-size", str(size),
            "--chunk-overlap", str(overlap), "--embedding-model", "",
            "--chunk-token-counter", "estimate", "--chunk-max-tokens", "4096",
            "--symbol-glossary", "off"]


def _run_all(tmp_path: Path, monkeypatch, params: dict) -> dict:
    """Every path over the shard for an entry recording ``params``; returns
    each path's rows plus the receipts and header."""
    size, overlap, unit = _sizing(params)
    monkeypatch.delenv("COLLECTION_MANIFEST_DIR", raising=False)
    tmp_path.mkdir(parents=True, exist_ok=True)
    shard = _shard(tmp_path)
    target = it.resolve(CID, registry=REG)
    markers = structure_from_params("sentence", target.spec.chunk_params)
    from ragstack.ingestion.chunker_config import semantic_cap_from_params

    tol = semantic_cap_from_params("sentence", target.spec.chunk_params, recorded_size=size)

    # A: the pipeline the API builds (STRUCTURE_METADATA off, the entry's markers).
    chunker, _, _ = build_chunker(
        "sentence", chunk_size=size, chunk_overlap=overlap, model="m",
        token_backend="estimate", max_tokens=4096, size_unit=unit,
        **({"structure_cuts": markers.structure_cuts} if markers.structure_cuts else {}),
        **tol)
    pipe = IngestionPipeline(loader=JsonlLoader(), chunker=chunker, embedder=_Embedder(),
                             vector_store=InMemoryVectorStore(),
                             text_index=InMemoryTextIndex(), structure_metadata=False,
                             structure_markers=markers)
    a = _rows(asyncio.run(pipe.embed_source(str(shard))))

    # B: ingest_shard.py's amain with the resolved target; no --structure-metadata.
    emb_b, receipt_b = tmp_path / "b.emb.jsonl", tmp_path / "b.receipt.json"
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _Embedder())
    args_b = ingest_shard.parse_args([
        str(shard), "--vector-backend", "memory", "--text-backend", "memory",
        "--qdrant-url", DEAD, "--es-url", DEAD, *_tool_size(params),
        "--embedding-file", str(emb_b), "--out", str(receipt_b)])
    assert asyncio.run(ingest_shard.amain(args_b, target)) == 0
    b = _rows(_emb_rows(emb_b))

    # C: embed_shard.py resolving the entry itself.
    emb_c, receipt_c = tmp_path / "c.emb.jsonl", tmp_path / "c.receipt.json"
    monkeypatch.setattr(embed_shard, "make_embedder_auto", lambda **kw: _Embedder())
    assert embed_shard.main([
        str(shard), "--out", str(emb_c), "--receipt", str(receipt_c),
        "--collection-id", CID, "--registry", REG, *_tool_size(params),
        *(["--chunk-size-unit", "tokens"] if unit == "tokens" else []),
    ]) == 0
    c = _rows(_emb_rows(emb_c))

    # D: ingest_jsonl.py's own loop with the resolved target (setting off).
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _Embedder())
    seen_markers = []
    real_step_on = ingest_jsonl.structure_step_on

    def step_on(mode, markers=None):
        seen_markers.append(markers)
        return real_step_on(mode, markers)

    monkeypatch.setattr(ingest_jsonl, "structure_step_on", step_on)
    asyncio.run(ingest_jsonl.run(_args(shard, checkpoint=tmp_path / "d.ckpt",
                                       chunk_method="sentence", chunk_size=size,
                                       chunk_overlap=overlap, chunk_size_unit=unit,
                                       structure_metadata="off"), target))
    d = _rows(store.points.values())
    return {"a": a, "b": b, "c": c, "d": d, "emb_c": emb_c,
            "receipt_b": ShardReceipt.load(receipt_b), "receipt_c": ShardReceipt.load(receipt_c),
            "header": read_header(emb_c), "target": target, "seen_markers": seen_markers,
            "content": _long_paper()[0]}


def _expected_vec(text: str, stamps: dict) -> list[float]:
    path = stamps.get("section_path")
    return _vec(" › ".join(path) + "\n" + text if path else text)


def test_embed_heading_path_changes_the_vectors_only_on_every_path(
    tmp_path, registry, monkeypatch  # noqa: F811
) -> None:
    """MUTATION: prefix ``c.content`` in place → stored content carries the
    path → the keys differ from the unmarked run → fails. MUTATION: honour
    STRUCTURE_METADATA alone → no stamps, no prefix → fails."""
    registry(_spec(chunk_size=200, chunk_overlap=0, chunk_params={}))
    plain = _run_all(tmp_path / "plain", monkeypatch, {})
    registry(_spec(chunk_size=200, chunk_overlap=0, chunk_params=dict(PREFIX)))
    marked = _run_all(tmp_path / "marked", monkeypatch, PREFIX)

    a, b, c, d = (marked[k] for k in "abcd")
    assert a and a == b == c == d
    # Same chunks (text, offsets) as the unmarked entry; only vectors differ.
    assert a.keys() == plain["a"].keys()
    assert all(not v[1] for v in plain["a"].values())  # setting off, no marker: no stamp
    prefixed = 0
    for (text, _s, _e), (vec, stamps) in a.items():
        assert vec == _expected_vec(text, stamps)
        prefixed += bool(stamps.get("section_path"))
    assert prefixed >= 3
    # The embedding file stores the content, never the embed input.
    assert not any(" › " in r["content"] for r in _emb_rows(marked["emb_c"]))
    assert marked["receipt_b"].chunking == PREFIX
    assert marked["receipt_c"].chunking == PREFIX
    assert marked["header"]["chunking"]["embed_heading_path"] == "prefix/1"
    it.check_header_chunking(marked["header"], marked["target"])
    # Chunks with no path (the unstructured neighbour, the untitled preamble) are counted.
    rec = marked["receipt_b"].enrichment["structure"]  # type: ignore[index]
    assert rec["embed"]["heading_prefix_missing"] >= 1


@pytest.mark.parametrize("params", [BOTH, TOLERANT], ids=["cuts", "cuts+tolerance"])
def test_structure_cuts_chunk_identically_on_every_path(
    tmp_path, registry, monkeypatch, params  # noqa: F811
) -> None:
    size, overlap, _unit = _sizing(params)
    registry(_spec(chunk_size=size, chunk_overlap=overlap, chunk_params=dict(params)))
    got = _run_all(tmp_path, monkeypatch, params)
    a, b, c, d = (got[k] for k in "abcd")
    assert a and a == b == c == d
    # Path D turned the structure step on from the MARKERS (setting off).
    assert got["seen_markers"] and got["seen_markers"][0].structure_cuts == "sections/1"
    # The walls hold on the structured document.
    content, structure = _long_paper()
    doc = Document(id="x", content=content, metadata={}, structure=structure)
    segs = doc_cut_segments(doc)
    starts = [g.start for g in segs]
    mine = [(s, e) for text, s, e in a if content[s:e] == text]
    assert len(mine) >= 4
    for s, e in mine:
        lo, hi = bisect.bisect_right(starts, s) - 1, bisect.bisect_right(starts, e - 1) - 1
        if lo != hi:  # only a whole-segment merge, only under the tolerance
            assert params is TOLERANT and s == segs[lo].start and e == segs[hi].end
            assert len({segs[k].group for k in range(lo, hi + 1)}) == 1
    # Every chunk is stamped (the markers force it) and prefixed.
    stamped = [v for v in a.values() if v[1]]
    assert len(stamped) >= 4
    for (text, _s, _e), (vec, stamps) in a.items():
        assert vec == _expected_vec(text, stamps)
    rec_b = got["receipt_b"]
    assert rec_b.chunking["structure_cuts"] == "sections/1"  # type: ignore[index]
    assert rec_b.chunking["embed_heading_path"] == "prefix/1"  # type: ignore[index]
    assert got["header"]["chunking"]["structure_cuts"] == "sections/1"
    it.check_header_chunking(got["header"], got["target"])
    enrichment = rec_b.enrichment["structure"]  # type: ignore[index]
    assert enrichment["cuts"]["unsegmented"] == 1  # the neighbour has no structure
    if params is TOLERANT:
        assert rec_b.chunking["size_tolerance"] == 0.25  # type: ignore[index]
        assert rec_b.chunking["min_piece"] == 10  # type: ignore[index]
        assert rec_b.chunking["ceiling"] == 50  # type: ignore[index]
        assert got["header"]["chunking"]["size_tolerance"] == 0.25
        assert got["receipt_c"].chunking["ceiling"] == 50  # type: ignore[index]


# --------------------------------------------------------------------------- #
# The segmentation cache (path D)
# --------------------------------------------------------------------------- #


def test_a_cut_run_never_reuses_an_uncut_runs_cached_spans(
    tmp_path, registry, monkeypatch, capsys  # noqa: F811
) -> None:
    """MUTATION: leave structure_cuts out of the fingerprint → the cut run hits
    the uncut run's cache → fails."""
    shard = _shard(tmp_path)
    cache = tmp_path / "seg.jsonl"
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _Embedder())
    hits = []
    fingerprints = []
    real_fp = ingest_jsonl.config_fingerprint

    def fp_spy(**parts):
        fingerprints.append(parts)
        return real_fp(**parts)

    monkeypatch.setattr(ingest_jsonl, "config_fingerprint", fp_spy)
    for i, params in enumerate(({}, CUTS, CUTS)):
        registry(_spec(chunk_size=200, chunk_overlap=0, chunk_params=dict(params)))
        target = it.resolve(CID, registry=REG)
        asyncio.run(ingest_jsonl.run(_args(shard, checkpoint=tmp_path / f"{i}.ckpt",
                                           chunk_method="sentence", chunk_size=200,
                                           chunk_overlap=0, segmentation_cache=cache),
                                     target))
        line = [x for x in capsys.readouterr().err.splitlines()
                if x.startswith("segmentation cache:")][-1]
        hits.append(int(line.split()[2]))
    assert hits == [0, 0, 2]  # the second cut run hits its own entries
    # The marker is in the fingerprint itself (not only in the per-document key),
    # and an unmarked run's fingerprint is as before (no such key).
    assert "structure_cuts" not in fingerprints[0]
    assert fingerprints[1]["structure_cuts"] == "sections/1"


def test_the_structure_joins_a_cut_documents_cache_key(tmp_path) -> None:
    content, structure = paper()
    with_s = Document(id="a", content=content, metadata={}, structure=structure)
    without = Document(id="a", content=content, metadata={})
    assert structure_cache_key(with_s) != structure_cache_key(without)
    cache = SegmentationCache(tmp_path / "c.jsonl", "fp")
    calls = []

    def chunk(doc):
        calls.append(doc)
        from ragstack.ingestion.chunkers import _make_chunk

        return [_make_chunk(doc, 0, len(doc.content))]

    cache.get_or_compute(with_s, chunk, structure_cache_key(with_s))
    cache.get_or_compute(without, chunk, structure_cache_key(without))
    cache.get_or_compute(with_s, chunk, structure_cache_key(with_s))
    assert len(calls) == 2 and cache.hits == 1
    # No extra: the key is the content's alone, as before.
    assert cache._key(content) == cache._key(content, "")
    cache.close()


def test_an_unmarked_run_keys_the_cache_on_content_alone(
    tmp_path, registry, monkeypatch  # noqa: F811
) -> None:
    """Fable N3: an unmarked ingest_jsonl run passes no key extra, so caches
    written before PR 7 still hit after the upgrade; a cut run passes the
    structure key. MUTATION: always pass the extra → fails."""
    shard = _shard(tmp_path)
    store = _FakeStore()
    monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
    monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _Embedder())
    calls: list[tuple] = []
    real = SegmentationCache.get_or_compute

    def spy(self, doc, chunk_fn, *extra):
        calls.append(extra)
        return real(self, doc, chunk_fn, *extra)

    monkeypatch.setattr(SegmentationCache, "get_or_compute", spy)
    for i, params in enumerate(({}, CUTS)):
        registry(_spec(chunk_size=200, chunk_overlap=0, chunk_params=dict(params)))
        asyncio.run(ingest_jsonl.run(_args(shard, checkpoint=tmp_path / f"{i}.ckpt",
                                           chunk_method="sentence", chunk_size=200,
                                           chunk_overlap=0,
                                           segmentation_cache=tmp_path / f"c{i}.jsonl"),
                                     it.resolve(CID, registry=REG)))
        if not params:
            assert calls and all(extra == () for extra in calls)
            calls.clear()
    assert calls and all(len(e) == 1 and e[0].startswith("structure:") for e in calls)
