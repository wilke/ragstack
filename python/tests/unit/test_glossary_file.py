"""#847 G1: the ``glossary.jsonl`` format, its strict reader and writer, the
builder script, and attaching the glossary from the file at load
(``load_embeddings.py --glossary-file``) and on the backfill's re-attach path
(``backfill_symbol_glossary.py --glossary-file``).

Offline throughout: synthetic text (no paper is copied), a fake embedder, the
in-memory stores, store URLs on the dead port ``127.0.0.1:1``, and a fake
Elasticsearch client for the backfill. "Refused before any write" is observed
as "the first thing that builds or writes a store was never reached".
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ragstack.ingestion.chunkers import RecursiveCharacterChunker, SentenceChunker, WordChunker
from ragstack.ingestion.embed_shard import run_embed_shard
from ragstack.ingestion.embedding_file import read_embedding_file, write_embedding_file
from ragstack.ingestion.glossary_file import (
    FILE_STAMP,
    MISMATCH,
    MISSING,
    SCHEMA,
    UNVERIFIABLE,
    VERIFIED,
    VERIFIED_WS,
    GlossaryFileError,
    GlossaryRow,
    build_row,
    check_document,
    masked_key,
    read_glossary_file,
    resolve_documents,
    stamp_version,
    write_glossary_file,
)
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.reassembly import reassemble
from ragstack.ingestion.symbol_glossary import (
    DEFAULT_CONFIG,
    EXTRACTOR_VERSION,
    VERSION_KEY,
    Definition,
    Glossary,
    GlossaryConfig,
    GlossaryEntry,
    SymbolGlossaryAttacher,
    SymbolGlossaryCache,
    content_key,
)
from ragstack.models import Chunk, Document
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import backfill_symbol_glossary as bf  # noqa: E402
import glossary_file as gf_cli  # noqa: E402
import load_embeddings as load_cli  # noqa: E402

DEAD = "http://127.0.0.1:1"
PAD = " ".join(["filler words keep the chunks apart"] * 6)

# Qb is defined twice, differently, each followed by a value statement, so the
# positional choice of definition is visible in the rendered field. Paragraph
# breaks and single line breaks exercise the reassembly and the line folding.
DOC_A = (
    "Methods\n\nThe exhaled flux N = Qb Cq, where Qb is the breathing flow rate, and Cq is "
    "the quanta concentration.\n" + PAD + "\n\nTable 1: Qb = 0.5 m3/h and Cq = 30 per m3.\n"
    + PAD + "\n\nIn the second model, where Qb is the exhaled particle\nvolume per breath. "
    + PAD + "\nTable 2: Qb = 3 L.\n" + PAD
)
DOC_B = (
    "We introduce the viral deactivation rate λv, a key constant.\n" + PAD
    + "\n\nMeasured: λv = 0.3 per hour, with r¯ ≈ 1 μm where r¯ is the effective "
    "infectious drop radius.\n" + PAD
)
DOC_C = "No symbols here, only words.\n\n" + PAD


def _write_jsonl(path: Path, docs: dict[str, str]) -> Path:
    path.write_text("".join(json.dumps({"text": t, "path": f"{name}.pdf", "metadata": {}}) + "\n"
                            for name, t in docs.items()), encoding="utf-8")
    return path


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0, 2.0] for t in texts]


async def _embed(shard: Path, out: Path, attacher: SymbolGlossaryAttacher | None) -> None:
    pipeline = IngestionPipeline(
        loader=JsonlLoader(), chunker=RecursiveCharacterChunker(chunk_size=120, chunk_overlap=20),
        embedder=_FakeEmbedder(), vector_store=InMemoryVectorStore(),
        text_index=InMemoryTextIndex(), glossary_attacher=attacher,
    )
    receipt = await run_embed_shard(pipeline, str(shard), "public", "s0", out)
    assert receipt.status == "completed", receipt.error


@pytest.fixture
def corpus(tmp_path: Path) -> dict[str, Path]:
    """A shard, its embedding file built by TODAY's in-process rules attach,
    and a glossary file built from the same documents by the builder script."""
    shard = _write_jsonl(tmp_path / "shard.jsonl", {"a": DOC_A, "b": DOC_B, "c": DOC_C})
    emb = tmp_path / "shard.emb.jsonl"
    asyncio.run(_embed(shard, emb, SymbolGlossaryAttacher()))
    gfile = tmp_path / "glossary.jsonl"
    assert gf_cli.main(["build", "--jsonl", str(shard), "--out", str(gfile)]) == 0
    return {"shard": shard, "emb": emb, "gfile": gfile, "dir": tmp_path}


# --- the format: round-trip ---------------------------------------------------


def _multi_def_glossary(version: str) -> Glossary:
    return Glossary((
        GlossaryEntry("Cq", (Definition("quanta concentration", 40),)),
        GlossaryEntry("Qb", (Definition("breathing flow rate", 12),
                             Definition("exhaled particle volume per breath", 900),
                             Definition("volume — with a dash and ünïcode", 1500))),
        GlossaryEntry("r̄", (Definition("effective infectious drop radius", 0),)),
        GlossaryEntry("λv", (Definition("viral deactivation rate", 5),)),
    ), version)


def _row(doc_id: str = "d1", **kw: Any) -> GlossaryRow:
    rules = _multi_def_glossary(EXTRACTOR_VERSION)
    llm = _multi_def_glossary("llm/1:m:abc:w80000x4:t2048")
    base: dict[str, Any] = {
        "doc_id": doc_id, "text_sha256": content_key(doc_id), "text_chars": len(doc_id),
        "text_ws_sha256": masked_key(doc_id), "rules": rules, "llm": llm,
        "merged": llm, "drops": {"revalidation": 2},
        "provenance": {"source": "llm_cache", "x": [1, {"y": None}]}, "degraded": None}
    base.update(kw)
    return GlossaryRow(**base)


def test_round_trip_is_lossless(tmp_path: Path) -> None:
    rows = [_row("d1"), _row("d2", llm=None, merged=_multi_def_glossary(EXTRACTOR_VERSION),
                             drops={}, degraded="endpoint unavailable")]
    path = tmp_path / "g.jsonl"
    counts = write_glossary_file(path, rows, producer="test", created="2026-10-28T00:00:00Z")
    assert counts == {"documents": 2, "with_llm": 1, "degraded": 1, "merged_entries": 8}
    back = read_glossary_file(path)
    assert back.header["schema"] == SCHEMA and back.header["counts"] == counts
    assert back.config == DEFAULT_CONFIG
    assert back.rows == {"d1": rows[0], "d2": rows[1]}
    # Identical Glossary objects, offsets and multi-definition order included...
    assert back.rows["d1"].merged == rows[0].merged
    assert back.rows["d1"].merged.entries[1].definitions[1].offset == 900
    # ...and identical rendering, positional choice included.
    chunk = "Table: Qb = 3 L, λv = 0.3 /h, r̄ ≈ 1 μm and Cq = 30."
    for start in (0, 12, 500, 900, 5000):
        assert (back.rows["d1"].merged.for_chunk(chunk, DEFAULT_CONFIG, start_char=start)
                == rows[0].merged.for_chunk(chunk, DEFAULT_CONFIG, start_char=start))
    picks = {s: back.rows["d1"].merged.for_chunk(chunk, DEFAULT_CONFIG, start_char=s)
             for s in (0, 901, 1600)}
    assert "breathing flow rate" in picks[0]
    assert "exhaled particle volume" in picks[901]
    assert "with a dash and ünïcode" in picks[1600]


def test_round_trip_of_a_real_built_file(corpus: dict[str, Path]) -> None:
    """The builder's rows read back equal to what build_row computes."""
    gfile = read_glossary_file(corpus["gfile"])
    attacher = SymbolGlossaryAttacher()
    for doc in JsonlLoader().load(str(corpus["shard"])):
        assert gfile.rows[doc.id] == build_row(doc.id, doc.content, attacher)
    assert any(e.ambiguous for r in gfile.rows.values() for e in r.merged.entries)


def test_writer_is_deterministic_and_atomic(tmp_path: Path) -> None:
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    write_glossary_file(a, [_row("d1")], producer="t", created="2026-10-28T00:00:00Z")
    write_glossary_file(b, [_row("d1")], producer="t", created="2026-10-28T00:00:00Z")
    assert a.read_bytes() == b.read_bytes()
    with pytest.raises(GlossaryFileError, match="duplicate doc_id"):
        write_glossary_file(a, [_row("d1"), _row("d1")], producer="t")
    # The failed write left the previous file intact and no temporary behind.
    assert a.read_bytes() == b.read_bytes()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.jsonl", "b.jsonl"]


def test_writer_refuses_what_the_reader_would_refuse(tmp_path: Path) -> None:
    bad = _row("d1", merged=Glossary((GlossaryEntry("Qb", (Definition("x y z", 9),
                                                           Definition("a b c", 3))),), "v"))
    with pytest.raises(GlossaryFileError, match="document order"):
        write_glossary_file(tmp_path / "g.jsonl", [bad], producer="t")
    assert not (tmp_path / "g.jsonl").exists()


# --- the strict reader --------------------------------------------------------


def _good_lines(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "good.jsonl"
    write_glossary_file(path, [_row("d1"), _row("d2")], producer="t",
                        created="2026-10-28T00:00:00Z")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_lines(path: Path, lines: list[Any]) -> Path:
    path.write_text("".join((x if isinstance(x, str) else json.dumps(x, ensure_ascii=False))
                            + "\n" for x in lines), encoding="utf-8")
    return path


def _mutate(lines: list[dict[str, Any]], i: int, fn: Any) -> list[dict[str, Any]]:
    out = json.loads(json.dumps(lines))
    fn(out[i])
    return out


@pytest.mark.parametrize("schema", ["ragstack.glossary_file/v2", "ragstack.glossary_file/v0",
                                    "ragstack.embedding_file/v1", None])
def test_reader_refuses_an_unknown_schema_version(tmp_path: Path, schema: Any) -> None:
    lines = _mutate(_good_lines(tmp_path), 0, lambda h: h.update(schema=schema))
    with pytest.raises(GlossaryFileError, match="unknown schema"):
        read_glossary_file(_write_lines(tmp_path / "x.jsonl", lines))


def _set(key: str, value: Any) -> Any:
    return lambda r: r.__setitem__(key, value)


def _del(key: str) -> Any:
    return lambda r: r.pop(key)


def _swap_defs(r: dict[str, Any]) -> None:
    r["merged"]["entries"][1]["definitions"].reverse()


def _swap_entries(r: dict[str, Any]) -> None:
    r["merged"]["entries"].reverse()


def _dup_entry(r: dict[str, Any]) -> None:
    r["rules"]["entries"].insert(0, r["rules"]["entries"][0])


@pytest.mark.parametrize("line, mutate, match", [
    (0, _set("kind", "document"), "kind 'header'"),
    (0, _set("extra", 1), "unknown"),
    (0, _del("producer"), "missing"),
    (0, _set("created", "yesterday"), "ISO-8601"),
    (0, _set("counts", {"documents": 2}), "counts"),
    (0, lambda h: h["counts"].update(documents=3), "truncated or spliced"),
    (0, lambda h: h["config"].update(positional="yes"), "not a valid bool"),
    (0, lambda h: h["config"].update(no_such_knob=1), "unknown config key"),
    (1, _set("kind", "header"), "kind 'document'"),
    (1, _set("extra", 1), "unknown"),
    (1, _del("drops"), "missing"),
    (1, _set("doc_id", ""), "doc_id"),
    (1, _set("text_sha256", "ABC"), "text_sha256"),
    (1, _set("text_sha256", "A" * 64), "text_sha256"),
    (1, _set("glossary_version", "rules/3"), "glossary_version"),
    (1, _set("merged", None), "glossary is an object"),
    (1, _set("drops", {"gate": -1}), "drops"),
    (1, _set("drops", {"gate": True}), "drops"),
    (1, _set("provenance", []), "provenance"),
    (1, _set("degraded", ""), "degraded"),
    (1, _swap_defs, "document order"),
    (1, _swap_entries, "strictly ascending"),
    (1, _dup_entry, "strictly ascending"),
    (1, lambda r: r["merged"]["entries"][0]["definitions"][0].update(offset=-1), "offset"),
    (1, lambda r: r["merged"]["entries"][0]["definitions"][0].update(offset=1.5), "offset"),
    (1, lambda r: r["merged"]["entries"][0]["definitions"][0].update(offset=True), "offset"),
    (1, lambda r: r["merged"]["entries"][0]["definitions"][0].update(text=""), "text"),
    (1, lambda r: r["merged"]["entries"][0].update(definitions=[]), "non-empty list"),
    (1, lambda r: r["llm"].update(extra=1), "exactly 'version' and 'entries'"),
    (2, _set("doc_id", "d1"), "duplicate doc_id"),
])
def test_reader_refuses_malformed_rows(tmp_path: Path, line: int, mutate: Any, match: str) -> None:
    lines = _mutate(_good_lines(tmp_path), line, mutate)
    with pytest.raises(GlossaryFileError, match=match):
        read_glossary_file(_write_lines(tmp_path / "x.jsonl", lines))


@pytest.mark.parametrize("raw, match", [
    (lambda ls: ls[:-1], "truncated or spliced"),
    (lambda ls: [*ls[:1], "", *ls[1:]], "blank line"),
    (lambda ls: [*ls, '{"kind": "document", '], "bad JSON"),
    (lambda ls: [], "empty file"),
])
def test_reader_refuses_truncation_and_garbage(tmp_path: Path, raw: Any, match: str) -> None:
    lines = raw(_good_lines(tmp_path))
    with pytest.raises(GlossaryFileError, match=match):
        read_glossary_file(_write_lines(tmp_path / "x.jsonl", lines))


def test_reader_filter_keeps_only_named_rows_but_validates_all(tmp_path: Path) -> None:
    lines = _good_lines(tmp_path)
    path = _write_lines(tmp_path / "x.jsonl", lines)
    assert set(read_glossary_file(path, doc_ids=["d2"]).rows) == {"d2"}
    broken = _mutate(lines, 1, _set("text_sha256", "nope"))
    with pytest.raises(GlossaryFileError):
        read_glossary_file(_write_lines(tmp_path / "y.jsonl", broken), doc_ids=["d2"])


def test_header_config_is_honoured(tmp_path: Path) -> None:
    cfg = GlossaryConfig(positional=False, max_entries_per_chunk=3)
    path = tmp_path / "g.jsonl"
    write_glossary_file(path, [_row("d1")], producer="t", config=cfg)
    assert read_glossary_file(path).config == cfg


# --- the builder script ---------------------------------------------------------


def test_build_with_an_llm_cache_records_both_halves(tmp_path: Path) -> None:
    shard = _write_jsonl(tmp_path / "s.jsonl", {"a": DOC_A, "b": DOC_B})
    cache_path = tmp_path / "cache.jsonl"
    cache = SymbolGlossaryCache(cache_path, writable=True)
    llm = Glossary((GlossaryEntry("Cq", (Definition("quanta concentration", 60),)),),
                   "llm/1:m:abc:w80000x4:t2048")
    cache.put(content_key(DOC_A), llm, {"cut_off": 0})
    cache.close()
    out = tmp_path / "g.jsonl"
    assert gf_cli.main(["build", "--jsonl", str(shard), "--llm-cache", str(cache_path),
                        "--out", str(out)]) == 0
    g = read_glossary_file(out)
    assert g.header["counts"]["with_llm"] == 1
    ids = {d.content: d.id for d in JsonlLoader().load(str(shard))}
    a, b = g.rows[ids[DOC_A]], g.rows[ids[DOC_B]]
    assert a.llm is not None and a.merged == a.llm and a.glossary_version.startswith("llm/1:")
    assert a.rules.version == EXTRACTOR_VERSION and a.rules != a.merged
    assert a.provenance["source"] == "llm_cache"
    assert b.llm is None and b.merged == b.rules
    # Parity with today's attacher, document by document.
    attacher = SymbolGlossaryAttacher(cache=SymbolGlossaryCache(cache_path))
    for doc in JsonlLoader().load(str(shard)):
        assert g.rows[doc.id].merged == attacher.glossary_for(doc.content)


def test_verify_subcommand(corpus: dict[str, Path], tmp_path: Path, capsys: Any) -> None:
    assert gf_cli.main(["verify", str(corpus["gfile"]), "--jsonl", str(corpus["shard"])]) == 0
    other = _write_jsonl(tmp_path / "other.jsonl", {"a": DOC_A + " changed", "z": DOC_C + "!"})
    capsys.readouterr()
    assert gf_cli.main(["verify", str(corpus["gfile"]), "--jsonl", str(other)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["mismatch"] == 1 and out["missing"] == 1
    bad = tmp_path / "bad.jsonl"
    bad.write_text(corpus["gfile"].read_text(encoding="utf-8").replace(SCHEMA, SCHEMA + "x"),
                   encoding="utf-8")
    assert gf_cli.main(["verify", str(bad)]) == 2


# --- the load-side check ------------------------------------------------------


def _reassembled(emb: Path) -> list[Any]:
    return load_cli._documents_in([str(emb)])


def test_resolution_kinds(corpus: dict[str, Path]) -> None:
    gfile = read_glossary_file(corpus["gfile"])
    docs = {d.doc_id: d for d in _reassembled(corpus["emb"])}
    assert len(docs) == 3
    ok = resolve_documents(gfile, docs.values())
    assert not ok.problems and set(ok.glossaries) == set(docs)
    assert all(g.version == stamp_version(EXTRACTOR_VERSION) for g in ok.glossaries.values())
    a = next(iter(docs.values()))
    changed = a.__class__(a.doc_id, a.text.replace("Methods", "Method5"), 0, 0)
    gappy = a.__class__(a.doc_id, a.text.replace("Methods", "       "), 1, 0)
    absent = a.__class__("nobody", a.text, 0, 0)
    assert resolve_documents(gfile, [changed]).problems == [(a.doc_id, MISMATCH)]
    assert resolve_documents(gfile, [gappy]).problems == [(a.doc_id, UNVERIFIABLE)]
    allowed = resolve_documents(gfile, [gappy], allow_unverifiable=True)
    assert not allowed.problems and allowed.unverified == [a.doc_id]
    assert resolve_documents(gfile, [absent]).problems == [("nobody", MISSING)]
    # The override never lets a COMPLETE reassembly that mismatches through.
    assert resolve_documents(gfile, [changed], allow_unverifiable=True).problems


def test_reassembly_reproduces_the_documents_text(corpus: dict[str, Path]) -> None:
    texts = {d.id: d.content for d in JsonlLoader().load(str(corpus["shard"]))}
    for doc in _reassembled(corpus["emb"]):
        assert doc.gaps == 0 and doc.length_mismatches == 0
        assert content_key(doc.text) == content_key(texts[doc.doc_id])


@pytest.mark.parametrize("chunker", [
    RecursiveCharacterChunker(chunk_size=120, chunk_overlap=20),
    SentenceChunker(chunk_size=200, chunk_overlap=40),
    WordChunker(chunk_size=25, chunk_overlap=5),
], ids=["recursive", "sentence", "word"])
@pytest.mark.parametrize("text", [DOC_A, DOC_B, "  \n\n" + DOC_C + "\n\n  "])
def test_reassembly_is_exact_for_the_chunkers(chunker: Any, text: str) -> None:
    """The load's text source: a complete set of chunks reassembles the text
    the glossary file's text_sha256 was computed on."""
    doc = Document(id="d", content=text, source="x")
    r = reassemble("d", [c.model_dump() for c in chunker.chunk(doc)])
    assert r.length_mismatches == 0
    assert content_key(r.text) == content_key(text)


# --- load_embeddings.py --glossary-file -----------------------------------------


@pytest.fixture
def load(monkeypatch: Any, tmp_path: Path) -> Any:
    """Run ``load_embeddings.main`` on the memory backend and capture the text
    index it builds. ``amain`` is wrapped so a test can see whether the run got
    as far as building a store."""
    built: list[InMemoryTextIndex] = []
    reached: list[bool] = []

    def _index() -> InMemoryTextIndex:
        ti = InMemoryTextIndex()
        built.append(ti)
        return ti

    monkeypatch.setattr(load_cli, "InMemoryTextIndex", _index)
    real_amain = load_cli.amain

    async def amain(args: Any, target: Any = None) -> int:
        reached.append(True)
        return await real_amain(args, target)

    monkeypatch.setattr(load_cli, "amain", amain)

    def _run(*argv: str) -> tuple[Any, list[Chunk] | None, bool]:
        built.clear()
        reached.clear()
        try:
            rc: Any = load_cli.main([*argv, "--out", str(tmp_path / "summary.json"),
                                     "--collection-id", "scratch-g1",
                                     "--vector-backend", "memory", "--text-backend", "memory",
                                     "--qdrant-url", DEAD, "--es-url", DEAD])
        except SystemExit as e:
            rc = e.code
        chunks = list(built[0]._chunks) if built else None
        return rc, chunks, bool(reached)

    return _run


def _by_id(chunks: list[Chunk]) -> dict[str, Chunk]:
    return {c.id: c for c in chunks}


def test_load_renders_from_the_file(corpus: dict[str, Path], load: Any) -> None:
    rc, chunks, _ = load(str(corpus["emb"]), "--glossary-file", str(corpus["gfile"]))
    assert rc == 0 and chunks
    assert {c.metadata[VERSION_KEY] for c in chunks} == {f"{EXTRACTOR_VERSION}@{FILE_STAMP}"}
    assert any(c.symbol_glossary for c in chunks)


def test_parity_rules_only_file_renders_todays_field(corpus: dict[str, Path], load: Any) -> None:
    """A file built from the rules alone renders exactly the field today's
    in-process attach put into the embedding file, chunk for chunk."""
    file_chunks, _ = read_embedding_file(corpus["emb"])
    today = _by_id(file_chunks)
    assert sum(bool(c.symbol_glossary) for c in file_chunks) >= 3
    # The positional choice is exercised: one symbol renders two definitions
    # across the document's chunks.
    qb = {line for c in file_chunks for line in c.symbol_glossary.splitlines()
          if line.startswith("Qb")}
    assert len(qb) == 2, qb
    rc, chunks, _ = load(str(corpus["emb"]), "--glossary-file", str(corpus["gfile"]))
    assert rc == 0 and chunks is not None and set(_by_id(chunks)) == set(today)
    for cid, c in _by_id(chunks).items():
        assert c.symbol_glossary == today[cid].symbol_glossary, cid
        assert c.metadata[VERSION_KEY] == stamp_version(today[cid].metadata[VERSION_KEY])


def test_parity_with_an_llm_cache(tmp_path: Path, load: Any) -> None:
    shard = _write_jsonl(tmp_path / "s.jsonl", {"a": DOC_A, "b": DOC_B})
    cache_path = tmp_path / "cache.jsonl"
    cache = SymbolGlossaryCache(cache_path, writable=True)
    cache.put(content_key(DOC_A), Glossary(
        (GlossaryEntry("Cq", (Definition("quanta concentration", 60),)),),
        "llm/1:m:abc:w80000x4:t2048"))
    cache.close()
    emb = tmp_path / "s.emb.jsonl"
    asyncio.run(_embed(shard, emb, SymbolGlossaryAttacher(cache=SymbolGlossaryCache(cache_path))))
    gpath = tmp_path / "g.jsonl"
    assert gf_cli.main(["build", "--jsonl", str(shard), "--llm-cache", str(cache_path),
                        "--out", str(gpath)]) == 0
    today = _by_id(read_embedding_file(emb)[0])
    assert any(c.metadata[VERSION_KEY].startswith("llm/") for c in today.values())
    rc, chunks, _ = load(str(emb), "--glossary-file", str(gpath))
    assert rc == 0 and chunks
    for c in chunks:
        assert c.symbol_glossary == today[c.id].symbol_glossary
        assert c.metadata[VERSION_KEY] == stamp_version(today[c.id].metadata[VERSION_KEY])


def test_load_without_the_flag_is_byte_identical(corpus: dict[str, Path], load: Any,
                                                 tmp_path: Path) -> None:
    """No --glossary-file: every chunk is stored exactly as the embedding file
    holds it — sentinel field and stamp included, which no render produces."""
    chunks, _ = read_embedding_file(corpus["emb"])
    for c in chunks:
        c.symbol_glossary = f"SENTINEL {c.id}"
        c.metadata[VERSION_KEY] = "sentinel/0"
    emb = tmp_path / "sentinel.emb.jsonl"
    write_embedding_file(emb, chunks, tenant="public")
    rc, stored, _ = load(str(emb))
    assert rc == 0 and stored is not None
    expected = {c.id: json.dumps(c.model_dump(), sort_keys=True) for c in chunks}
    assert {c.id: json.dumps(c.model_dump(), sort_keys=True) for c in stored} == expected


def _rewrite_rows(src: Path, dst: Path, fn: Any) -> Path:
    lines = src.read_text(encoding="utf-8").splitlines()
    header, rows = json.loads(lines[0]), [json.loads(x) for x in lines[1:]]
    rows = fn(rows)
    header["counts"]["documents"] = len(rows)
    header["counts"]["merged_entries"] = sum(len(r["merged"]["entries"]) for r in rows)
    header["counts"]["with_llm"] = sum(r["llm"] is not None for r in rows)
    return _write_lines(dst, [header, *rows])


def test_load_refuses_a_text_sha256_mismatch_before_any_write(
        corpus: dict[str, Path], load: Any, capsys: Any) -> None:
    def tamper(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows[0]["text_sha256"] = content_key("some other text")
        rows[0]["text_ws_sha256"] = masked_key("some other text")
        return rows

    bad = _rewrite_rows(corpus["gfile"], corpus["dir"] / "bad.jsonl", tamper)
    rc, chunks, reached = load(str(corpus["emb"]), "--glossary-file", str(bad))
    assert rc == 2 and chunks is None and not reached
    err = capsys.readouterr().err
    assert "mismatch: 1" in err and "Nothing was written" in err


def test_load_refuses_a_document_absent_from_the_file(
        corpus: dict[str, Path], load: Any, capsys: Any) -> None:
    """The absent-document rule: an error, never a rules fallback."""
    partial = _rewrite_rows(corpus["gfile"], corpus["dir"] / "partial.jsonl", lambda r: r[1:])
    rc, chunks, reached = load(str(corpus["emb"]), "--glossary-file", str(partial))
    assert rc == 2 and chunks is None and not reached
    assert "missing: 1" in capsys.readouterr().err


def test_load_refuses_an_unreadable_file_before_any_write(corpus: dict[str, Path],
                                                          load: Any) -> None:
    bad = corpus["dir"] / "v2.jsonl"
    bad.write_text(corpus["gfile"].read_text(encoding="utf-8").replace(SCHEMA, SCHEMA + "2"),
                   encoding="utf-8")
    rc, chunks, reached = load(str(corpus["emb"]), "--glossary-file", str(bad))
    assert rc == 2 and chunks is None and not reached


def test_a_file_covering_more_documents_than_the_load_is_fine(
        corpus: dict[str, Path], load: Any, tmp_path: Path) -> None:
    chunks, _ = read_embedding_file(corpus["emb"])
    one = [c for c in chunks if c.doc_id == chunks[0].doc_id]
    emb = tmp_path / "one.emb.jsonl"
    write_embedding_file(emb, one, tenant="public")
    rc, stored, _ = load(str(emb), "--glossary-file", str(corpus["gfile"]))
    assert rc == 0 and stored is not None and len(stored) == len(one)


def test_load_unverifiable_needs_the_override(corpus: dict[str, Path], load: Any,
                                              tmp_path: Path) -> None:
    chunks, _ = read_embedding_file(corpus["emb"])
    doc = next(c.doc_id for c in chunks if sum(x.doc_id == c.doc_id for x in chunks) >= 3)
    mine = sorted((c for c in chunks if c.doc_id == doc), key=lambda c: c.start_char)
    dropped = [c for c in chunks if c.id != mine[1].id]  # a hole, as --boilerplate drop leaves
    emb = tmp_path / "hole.emb.jsonl"
    write_embedding_file(emb, dropped, tenant="public")
    rc, stored, reached = load(str(emb), "--glossary-file", str(corpus["gfile"]))
    assert rc == 2 and stored is None and not reached
    rc, stored, _ = load(str(emb), "--glossary-file", str(corpus["gfile"]),
                         "--glossary-allow-unverifiable")
    assert rc == 0 and stored is not None and len(stored) == len(dropped)


def test_flags_that_do_not_combine(corpus: dict[str, Path], load: Any) -> None:
    rc, _, reached = load(str(corpus["emb"]), "--glossary-allow-unverifiable")
    assert rc == 2 and not reached


# --- backfill_symbol_glossary.py --glossary-file --------------------------------


class FakeES:
    """PIT/search sorted by doc_id; records every write (bulk and mapping)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.bulks: list[list[dict[str, Any]]] = []
        self.mappings: list[Any] = []
        self.indices = self

    async def put_mapping(self, index: str, body: Any) -> None:
        self.mappings.append(body)

    async def get(self, index: str) -> dict[str, Any]:
        return {index: {}}

    async def open_point_in_time(self, index: str, keep_alive: str) -> dict[str, str]:
        return {"id": "pit"}

    async def close_point_in_time(self, body: Any) -> None:
        return None

    async def search(self, body: dict[str, Any]) -> dict[str, Any]:
        ordered = sorted(self.rows, key=lambda r: (r["doc_id"], r["_id"]))
        start = int(body["search_after"][0]) if "search_after" in body else 0
        page = ordered[start : start + body["size"]]
        return {"pit_id": "pit", "hits": {"hits": [
            {"_id": r["_id"], "_source": {k: v for k, v in r.items() if k != "_id"},
             "sort": [start + j + 1]} for j, r in enumerate(page)]}}

    async def count(self, index: str, query: dict[str, Any]) -> dict[str, int]:
        ((field, prefix),) = query["prefix"].items()
        key = field.split(".", 1)[1]
        return {"count": sum(1 for r in self.rows
                             if str((r.get("metadata") or {}).get(key) or "").startswith(prefix))}

    async def bulk(self, operations: list[dict[str, Any]], refresh: bool) -> dict[str, Any]:
        self.bulks.append(operations)
        for action, doc in zip(operations[::2], operations[1::2], strict=True):
            row = next(r for r in self.rows if r["_id"] == action["update"]["_id"])
            row["symbol_glossary"] = doc["doc"]["symbol_glossary"]
            row["metadata"].update(doc["doc"]["metadata"])
        return {"errors": False, "items": []}


def _es_rows(emb: Path, *, strip_glossary: bool = True) -> list[dict[str, Any]]:
    """The index the embedding file would have produced, as ES rows."""
    chunks, _ = read_embedding_file(emb)
    rows = []
    for c in chunks:
        meta = dict(c.metadata)
        if strip_glossary:
            meta.pop(VERSION_KEY, None)
        rows.append({"_id": f"public:{c.id}", "doc_id": c.doc_id, "content": c.content,
                     "start_char": c.start_char, "end_char": c.end_char, "metadata": meta,
                     **({} if strip_glossary else {"symbol_glossary": c.symbol_glossary})})
    return rows


def _bf_args(gfile: Path, apply: bool = False, **kw: Any) -> Any:
    ns: dict[str, Any] = {
        "es_url": DEAD, "index": "i", "llm_cache": None, "config": "", "batch_size": 3,
        "apply": apply, "confirm_index": "i" if apply else "", "refresh_off": False,
        "replace_llm": False, "glossary_file": str(gfile), "glossary_allow_unverifiable": False}
    ns.update(kw)
    return bf.argparse.Namespace(**ns)


def test_backfill_renders_from_the_file(corpus: dict[str, Path]) -> None:
    today = _by_id(read_embedding_file(corpus["emb"])[0])
    es = FakeES(_es_rows(corpus["emb"]))
    out = asyncio.run(bf.run(_bf_args(corpus["gfile"], apply=True), es=es))
    assert out["chunks_written"] == out["chunks"] == len(today)
    for r in es.rows:
        cid = r["_id"].split(":", 1)[1]
        assert (r["symbol_glossary"] or "") == today[cid].symbol_glossary
        assert r["metadata"][VERSION_KEY] == stamp_version(EXTRACTOR_VERSION)
    again = asyncio.run(bf.run(_bf_args(corpus["gfile"], apply=True), es=es))
    assert again["chunks_to_update"] == 0


@pytest.mark.parametrize("apply", [False, True])
def test_backfill_refuses_a_mismatch_before_any_write(corpus: dict[str, Path],
                                                      apply: bool) -> None:
    def tamper(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows[-1]["text_sha256"] = content_key("another text")
        rows[-1]["text_ws_sha256"] = masked_key("another text")
        return rows

    bad = _rewrite_rows(corpus["gfile"], corpus["dir"] / "bad.jsonl", tamper)
    es = FakeES(_es_rows(corpus["emb"]))
    with pytest.raises(SystemExit, match="mismatch: 1"):
        asyncio.run(bf.run(_bf_args(bad, apply=apply), es=es))
    assert es.bulks == [] and es.mappings == []


def test_backfill_refuses_an_absent_document_before_any_write(corpus: dict[str, Path]) -> None:
    partial = _rewrite_rows(corpus["gfile"], corpus["dir"] / "p.jsonl", lambda r: r[:-1])
    es = FakeES(_es_rows(corpus["emb"]))
    with pytest.raises(SystemExit, match="missing: 1"):
        asyncio.run(bf.run(_bf_args(partial, apply=True), es=es))
    assert es.bulks == [] and es.mappings == []


def test_backfill_file_never_silently_replaces_an_llm_glossary(corpus: dict[str, Path]) -> None:
    rows = _es_rows(corpus["emb"])
    for r in rows:
        r["metadata"][VERSION_KEY] = "llm/1:m:abc"
    es = FakeES(rows)
    with pytest.raises(SystemExit, match="--replace-llm"):
        asyncio.run(bf.run(_bf_args(corpus["gfile"], apply=True), es=es))
    assert es.bulks == [] and es.mappings == []
    out = asyncio.run(bf.run(_bf_args(corpus["gfile"], apply=True, replace_llm=True), es=es))
    assert out["chunks_written"] == len(rows)


def test_backfill_cli_flag_combinations(corpus: dict[str, Path]) -> None:
    base = ["--es-url", DEAD, "--index", "i", "--glossary-file", str(corpus["gfile"])]
    with pytest.raises(SystemExit):
        bf.main([*base, "--config", "{}"])
    with pytest.raises(SystemExit):
        bf.main([*base, "--llm-cache", str(corpus["gfile"])])
    with pytest.raises(SystemExit):
        bf.main(["--es-url", DEAD, "--index", "i", "--glossary-allow-unverifiable"])
    with pytest.raises(SystemExit):
        bf.main(["--es-url", DEAD, "--index", "i", "--glossary-file",
                 str(corpus["dir"] / "nope.jsonl")])


def test_reassemble_from_es_rows_matches_the_embedding_path(corpus: dict[str, Path]) -> None:
    """The backfill and the load reassemble the same text for a document."""
    load_docs = {d.doc_id: d.text for d in _reassembled(corpus["emb"])}
    rows = _es_rows(corpus["emb"])
    for doc_id, text in load_docs.items():
        assert reassemble(doc_id, [r for r in rows if r["doc_id"] == doc_id]).text == text


def test_backfill_unverifiable_needs_the_override(corpus: dict[str, Path]) -> None:
    rows = _es_rows(corpus["emb"])
    doc = rows[0]["doc_id"]
    mine = sorted((r for r in rows if r["doc_id"] == doc), key=lambda r: r["start_char"])
    rows = [r for r in rows if r["_id"] != mine[1]["_id"]]  # a hole in one document
    es = FakeES(rows)
    with pytest.raises(SystemExit, match="unverifiable: 1"):
        asyncio.run(bf.run(_bf_args(corpus["gfile"], apply=True), es=es))
    assert es.bulks == [] and es.mappings == []
    out = asyncio.run(bf.run(_bf_args(corpus["gfile"], glossary_allow_unverifiable=True), es=es))
    assert out["docs_glossary_unverified"] == 1 and out["docs"] == 3


# --- review fixes (#848): fixed_token under a WordPiece-style offset mapping ----


class _FakeTokenCounter:
    """A fast-tokenizer stand-in for ``FixedTokenWindowChunker``: pieces of at
    most three characters with their character offsets.

    ``wordpiece``: whitespace and Cc/Cf characters (BOM, ZWSP, bidi marks,
    form feed) belong to NO token — what BERT's normaliser and whitespace
    pre-tokenizer do, so window boundaries leave them uncovered. ``bytelevel``:
    every character belongs to a token (leading whitespace rides on the next
    piece), as with a byte-level sentencepiece tokenizer."""

    def __init__(self, style: str) -> None:
        import re
        import unicodedata

        self.style = style
        if style == "wordpiece":
            skip = "".join(chr(c) for c in range(0x110000)
                           if chr(c).isspace() or unicodedata.category(chr(c)) in ("Cc", "Cf"))
            self._re = re.compile(f"[^{re.escape(skip)}]{{1,3}}")
        else:
            self._re = re.compile(r"\s*\S{1,3}|\s+")

    def _offsets(self, text: str) -> list[tuple[int, int]]:
        return [(m.start(), m.end()) for m in self._re.finditer(text)]

    def _tokenizer(self) -> Any:
        def encode(text: str, return_offsets_mapping: bool = True,
                   add_special_tokens: bool = False) -> dict[str, Any]:
            return {"offset_mapping": self._offsets(text)}
        return encode

    def count(self, text: str) -> int:
        return len(self._offsets(text))


_FT_PARA = ("We write the flux as N = Qb Cq, where Qb is the breathing flow rate and Cq is "
            "the quanta concentration. In Table 1, Qb = 0.5 m3/h and Cq = 30 per m3.")
_FT_DOCS = {
    "newline boundaries": "\n\n".join(f"Section {i}\n\n{_FT_PARA}" for i in range(6)),
    "crlf": "\r\n\r\n".join(f"Section {i}\r\n{_FT_PARA}" for i in range(6)),
    "tabs": "\n".join(["a\tb\tc\t1.5"] * 30 + [_FT_PARA]),
    "nbsp": (_FT_PARA.replace(" ", " ") + "\n") * 4,
    "trailing zwsp": _FT_PARA + "​",
    "leading bom": "﻿" + _FT_PARA,
    "leading rlo": "‮" + _FT_PARA,
    "trailing formfeed": _FT_PARA + "\x0c",
}


def _ft_check(style: str, overlap: int, text: str) -> tuple[str, Any]:
    from ragstack.ingestion.chunkers import FixedTokenWindowChunker

    chunker = FixedTokenWindowChunker(8, overlap, token_counter=_FakeTokenCounter(style))
    chunks = chunker.chunk(Document(id="d", content=text, source="x"))
    for c in chunks:
        assert text[c.start_char:c.end_char] == c.content
    row = build_row("d", text, SymbolGlossaryAttacher())
    r = reassemble("d", [c.model_dump() for c in chunks])
    return check_document(row, r), r


@pytest.mark.parametrize("overlap", [0, 3])
@pytest.mark.parametrize("name", list(_FT_DOCS))
def test_fixed_token_wordpiece_is_verified_by_the_masked_identity(name: str, overlap: int) -> None:
    kind, r = _ft_check("wordpiece", overlap, _FT_DOCS[name])
    assert kind in (VERIFIED, VERIFIED_WS), (kind, r.gaps)


@pytest.mark.parametrize("overlap", [0, 3])
@pytest.mark.parametrize("name", list(_FT_DOCS))
def test_fixed_token_bytelevel_verifies_exactly(name: str, overlap: int) -> None:
    kind, r = _ft_check("bytelevel", overlap, _FT_DOCS[name])
    assert kind == VERIFIED and r.gaps == 0


@pytest.mark.parametrize("name", ["trailing zwsp", "leading bom", "newline boundaries",
                                  "tabs", "nbsp", "crlf"])
def test_the_wordpiece_cases_need_the_fallback(name: str) -> None:
    """Each named case fails the EXACT check and passes only the masked one."""
    kind, r = _ft_check("wordpiece", 0, _FT_DOCS[name])
    assert kind == VERIFIED_WS and r.gaps


def test_a_trailing_zwsp_with_no_gap_is_no_longer_a_mismatch() -> None:
    """No whitespace inside, so the chunks tile the text up to the ZWSP: no
    gap at all, and the exact check alone would have called it a MISMATCH —
    a hard refusal the override could not lift."""
    text = "Qb=0.5;Cq=30;λv=0.3." + "\u200b"
    kind, r = _ft_check("wordpiece", 0, text)
    assert r.gaps == 0 and len(r.text) == len(text) - 1
    assert kind == VERIFIED_WS


def test_the_masked_identity_still_refuses_a_real_difference() -> None:
    text = _FT_DOCS["newline boundaries"]
    _, r = _ft_check("wordpiece", 0, text)
    other = build_row("d", text.replace("breathing", "breathinG", 1), SymbolGlossaryAttacher())
    assert check_document(other, r) == "unverifiable"   # gaps: the override could apply
    longer = build_row("d", text[:-5], SymbolGlossaryAttacher())  # chunks reach past the text
    assert check_document(longer, r) != VERIFIED_WS
    # Whitespace moved INSIDE a covered region shifts every later offset: refused.
    shifted = build_row("d", text.replace("We write", "We  write", 1), SymbolGlossaryAttacher())
    assert check_document(shifted, r) not in (VERIFIED, VERIFIED_WS)


def test_load_of_a_fixed_token_wordpiece_shard_verifies_and_records_it(
        tmp_path: Path, load: Any) -> None:
    from ragstack.ingestion.chunkers import FixedTokenWindowChunker

    shard = _write_jsonl(tmp_path / "ft.jsonl", {"a": _FT_DOCS["newline boundaries"],
                                                 "b": _FT_DOCS["trailing zwsp"]})
    pipeline = IngestionPipeline(
        loader=JsonlLoader(),
        chunker=FixedTokenWindowChunker(8, 0, token_counter=_FakeTokenCounter("wordpiece")),
        embedder=_FakeEmbedder(), vector_store=InMemoryVectorStore(),
        text_index=InMemoryTextIndex(), glossary_attacher=SymbolGlossaryAttacher())
    emb = tmp_path / "ft.emb.jsonl"
    assert asyncio.run(run_embed_shard(pipeline, str(shard), "public", "s0", emb)).status \
        == "completed"
    gpath = tmp_path / "g.jsonl"
    assert gf_cli.main(["build", "--jsonl", str(shard), "--out", str(gpath)]) == 0
    rc, chunks, _ = load(str(emb), "--glossary-file", str(gpath))
    assert rc == 0 and chunks
    today = _by_id(read_embedding_file(emb)[0])
    assert all(c.symbol_glossary == today[c.id].symbol_glossary for c in chunks)
    prov = json.loads((tmp_path / "summary.json").read_text())["glossary_file"]
    assert prov["documents"] == {"verified": 0, "verified_ws": 2, "unverified": 0}


# --- review fixes (#848): the header config is what renders ----------------------


def _positional_off_file(corpus: dict[str, Path]) -> Path:
    out = corpus["dir"] / "pos_off.jsonl"
    assert gf_cli.main(["build", "--jsonl", str(corpus["shard"]), "--config",
                        '{"positional": false}', "--out", str(out)]) == 0
    assert read_glossary_file(out).config.positional is False
    return out


def _expected_under(gpath: Path, chunks: list[Chunk]) -> dict[str, str]:
    g = read_glossary_file(gpath)
    return {c.id: g.rows[c.doc_id].merged.for_chunk(c.content, g.config, start_char=c.start_char)
            for c in chunks}


def test_load_renders_with_the_header_config(corpus: dict[str, Path], load: Any) -> None:
    gpath = _positional_off_file(corpus)
    today = _by_id(read_embedding_file(corpus["emb"])[0])
    rc, chunks, _ = load(str(corpus["emb"]), "--glossary-file", str(gpath))
    assert rc == 0 and chunks
    expected = _expected_under(gpath, chunks)
    assert {c.id: c.symbol_glossary for c in chunks} == expected
    # Under positional=False a twice-defined symbol renders both definitions:
    # the field differs from today's (positional) one on some chunk.
    assert any(c.symbol_glossary != today[c.id].symbol_glossary for c in chunks)


def test_backfill_renders_with_the_header_config(corpus: dict[str, Path]) -> None:
    gpath = _positional_off_file(corpus)
    chunks, _ = read_embedding_file(corpus["emb"])
    expected = _expected_under(gpath, chunks)
    es = FakeES(_es_rows(corpus["emb"]))
    asyncio.run(bf.run(_bf_args(gpath, apply=True), es=es))
    got = {r["_id"].split(":", 1)[1]: r["symbol_glossary"] or "" for r in es.rows}
    assert got == expected
    assert any(c.symbol_glossary != expected[c.id] for c in chunks)


# --- review fixes (#848): --replay refuses --glossary-file -------------------------


def test_replay_refuses_a_glossary_file(corpus: dict[str, Path], load: Any,
                                        capsys: Any) -> None:
    rc, _, reached = load("--replay", str(corpus["dir"]), "--glossary-file",
                          str(corpus["gfile"]))
    assert rc == 2 and not reached
    assert "--replay" in capsys.readouterr().err


# --- review fixes (#848): provenance -------------------------------------------------


def test_load_and_backfill_record_the_file(corpus: dict[str, Path], load: Any,
                                          tmp_path: Path) -> None:
    from ragstack.ingestion.glossary_file import file_sha256

    rc, _, _ = load(str(corpus["emb"]), "--glossary-file", str(corpus["gfile"]))
    assert rc == 0
    prov = json.loads((tmp_path / "summary.json").read_text())["glossary_file"]
    header = read_glossary_file(corpus["gfile"]).header
    assert prov["sha256"] == file_sha256(corpus["gfile"])
    assert prov["path"] == str(corpus["gfile"]) and prov["file_stamp"] == FILE_STAMP
    assert (prov["producer"], prov["created"], prov["counts"]) == \
        (header["producer"], header["created"], header["counts"])
    assert prov["documents"] == {"verified": 3, "verified_ws": 0, "unverified": 0}
    out = asyncio.run(bf.run(_bf_args(corpus["gfile"]), es=FakeES(_es_rows(corpus["emb"]))))
    assert out["glossary_file"]["sha256"] == prov["sha256"]
    assert out["glossary_file"]["documents"]["verified"] == 3


def test_a_load_without_the_flag_records_no_file(corpus: dict[str, Path], load: Any,
                                                 tmp_path: Path) -> None:
    assert load(str(corpus["emb"]))[0] == 0
    assert "glossary_file" not in json.loads((tmp_path / "summary.json").read_text())


def test_the_manifest_names_the_file(corpus: dict[str, Path], tmp_path: Path,
                                     monkeypatch: Any) -> None:
    """``write_manifest`` gets the file's record as hash-excluded enrichment."""
    from types import SimpleNamespace

    seen: dict[str, Any] = {}

    def write_manifest(manifest_dir: str, **kw: Any) -> str:
        seen.update(kw)
        return "h"

    target = SimpleNamespace(collection="scratch-g1", write_manifest=write_manifest)
    monkeypatch.setattr(load_cli, "_chunk_cap_for",
                        lambda a, t: (None, {"effective": 0, "source": "none"}))
    args = load_cli.parse_args([str(corpus["emb"]), "--out", str(tmp_path / "s.json"),
                                "--vector-backend", "memory", "--text-backend", "memory",
                                "--manifest-dir", str(tmp_path / "m"),
                                "--glossary-file", str(corpus["gfile"])])
    args.glossary_attach = load_cli._resolve_glossary_file(args)
    assert asyncio.run(load_cli.amain(args, target)) == 0
    rec = seen["enrichment"]["symbol_glossary"]
    assert rec["source"] == "glossary_file" and rec["file_stamp"] == FILE_STAMP
    seen.clear()
    plain = load_cli.parse_args([str(corpus["emb"]), "--out", str(tmp_path / "s.json"),
                                 "--vector-backend", "memory", "--text-backend", "memory",
                                 "--manifest-dir", str(tmp_path / "m")])
    assert asyncio.run(load_cli.amain(plain, target)) == 0
    assert "enrichment" not in seen


# --- review fixes (#848): trivial ----------------------------------------------------


def test_a_lone_surrogate_is_a_glossary_file_error(tmp_path: Path) -> None:
    bad = _row("d1", merged=Glossary((GlossaryEntry("Qb", (Definition("rate \ud800", 1),)),), "v"))
    with pytest.raises(GlossaryFileError, match="surrogate"):
        write_glossary_file(tmp_path / "g.jsonl", [bad], producer="t")
    assert not (tmp_path / "g.jsonl").exists()


@pytest.mark.parametrize("created, ok", [
    ("2026-10-28T00:00:00Z", True), ("2026-10-28T00:00:00+00:00", True),
    ("2026-10-28T02:00:00+02:00", False), ("2026-10-28T00:00:00", False),
])
def test_created_must_be_utc(tmp_path: Path, created: str, ok: bool) -> None:
    lines = _mutate(_good_lines(tmp_path), 0, _set("created", created))
    path = _write_lines(tmp_path / "x.jsonl", lines)
    if ok:
        read_glossary_file(path)
    else:
        with pytest.raises(GlossaryFileError, match="UTC"):
            read_glossary_file(path)


def test_a_malformed_glossary_file_fails_before_the_embedding_files_are_read(
        corpus: dict[str, Path], load: Any, tmp_path: Path, capsys: Any) -> None:
    bad = tmp_path / "v2.jsonl"
    bad.write_text(corpus["gfile"].read_text(encoding="utf-8").replace(SCHEMA, SCHEMA + "2"),
                   encoding="utf-8")
    garbage = tmp_path / "garbage.emb.jsonl"
    garbage.write_text("not json\n", encoding="utf-8")
    rc, _, reached = load(str(garbage), "--glossary-file", str(bad))
    err = capsys.readouterr().err
    assert rc == 2 and not reached
    assert "unknown schema" in err and "garbage" not in err


# --- #848 follow-up: a short reassembly is unverifiable, not a mismatch -------------


@pytest.mark.parametrize("tail", ["", "́"], ids=["U+F0B7", "U+0301"])
def test_a_trailing_uncovered_unmasked_character_is_unverifiable(tail: str) -> None:
    """A tokenizer that leaves a trailing private-use bullet or combining accent
    in no token makes the reassembly one character short, with no gap. That is
    incomplete, so UNVERIFIABLE (the override applies), never a hard MISMATCH.
    The mask deliberately does not list Mn/Co."""
    text = "where Qb is the breathing flow rate. Qb = 0.5 m3/h." + tail
    row = build_row("d", text, SymbolGlossaryAttacher())
    short = reassemble("d", [{"content": text[:-1], "start_char": 0,
                              "end_char": len(text) - 1}])
    assert short.gaps == 0 and short.length_mismatches == 0
    assert check_document(row, short) == UNVERIFIABLE
    assert resolve_documents(read_glossary_file_rows({"d": row}), [short],
                             allow_unverifiable=True).unverified == ["d"]


def read_glossary_file_rows(rows: dict[str, GlossaryRow]) -> Any:
    from ragstack.ingestion.glossary_file import GlossaryFile

    return GlossaryFile(Path("mem"), {}, DEFAULT_CONFIG, rows)


def test_a_dropped_last_chunk_is_unverifiable(corpus: dict[str, Path]) -> None:
    chunks, _ = read_embedding_file(corpus["emb"])
    doc = max({c.doc_id for c in chunks}, key=lambda d: sum(c.doc_id == d for c in chunks))
    mine = sorted((c for c in chunks if c.doc_id == doc), key=lambda c: c.start_char)
    gfile = read_glossary_file(corpus["gfile"])
    r = reassemble(doc, [c.model_dump() for c in mine[:-1]])
    assert r.gaps == 0 and len(r.text) < gfile.rows[doc].text_chars
    assert check_document(gfile.rows[doc], r) == UNVERIFIABLE


def test_a_longer_reassembly_is_never_verified() -> None:
    """Chunks reaching past text_chars are never verified, even when the
    extra character is masked and the exact key matches after the strip."""
    text = "where Qb is the breathing flow rate. Qb = 0.5 m3/h."
    row = build_row("d", text, SymbolGlossaryAttacher())
    longer = reassemble("d", [{"content": text + " ", "start_char": 0,
                               "end_char": len(text) + 1}])
    assert content_key(longer.text) == row.text_sha256  # equal after the strip...
    padded = reassemble("d", [{"content": text + "​", "start_char": 0,
                               "end_char": len(text) + 1}])
    assert check_document(row, padded) == MISMATCH      # ...but past text_chars


def test_a_same_length_swap_within_the_mask_passes_by_design() -> None:
    text = "where Qb is the breathing flow rate.\n\nQb = 0.5 m3/h."
    row = build_row("d", text, SymbolGlossaryAttacher())
    swapped = reassemble("d", [{"content": text.replace("\n\n", "  ").replace(" is", " is"),
                                "start_char": 0, "end_char": len(text)}])
    assert check_document(row, swapped) == VERIFIED_WS


# --- #848 follow-up: the real tokenizers, offline only --------------------------------


def _offline_counter(model: str, monkeypatch: Any) -> Any:
    """An HFTokenCounter whose tokenizer comes from the local HF cache ONLY
    (``local_files_only``; HF_HUB_OFFLINE set), or a skip."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    try:
        from transformers import AutoTokenizer

        from ragstack.ingestion.tokenization import HFTokenCounter

        tok = AutoTokenizer.from_pretrained(model, local_files_only=True)
    except Exception as e:  # noqa: BLE001 — not cached / no transformers: skip
        pytest.skip(f"{model} tokenizer not available offline ({type(e).__name__})")
    counter = HFTokenCounter(model)
    counter._tok = tok
    return counter


_REAL_PARA = ("We write the exhaled flux as N = Qb Cq, where Qb is the breathing flow rate "
              "and Cq is the quanta concentration. In Table 1, Qb = 0.5 m3/h.")
_REAL_WS = {
    "paragraphs": "\n\n".join(f"Section {i}\n\n{_REAL_PARA}" for i in range(8)),
    "crlf": "\r\n\r\n".join(f"Section {i}\r\n\r\n{_REAL_PARA}" for i in range(8)),
    "trailing zwsp": _REAL_PARA + "​",
    "leading bom": "﻿" + _REAL_PARA,
}
_REAL_EDGE = {"pua bullet": _REAL_PARA + "", "combining acute": _REAL_PARA + " café"}


def _real_kind(counter: Any, text: str, size: int, overlap: int) -> str:
    from ragstack.ingestion.chunkers import FixedTokenWindowChunker

    chunks = FixedTokenWindowChunker(size, overlap, token_counter=counter).chunk(
        Document(id="d", content=text, source="x"))
    return check_document(build_row("d", text, SymbolGlossaryAttacher()),
                          reassemble("d", [c.model_dump() for c in chunks]))


@pytest.mark.parametrize("model", ["BAAI/bge-base-en-v1.5", "Salesforce/SFR-Embedding-Mistral"])
def test_real_tokenizers_offline(model: str, monkeypatch: Any) -> None:
    counter = _offline_counter(model, monkeypatch)
    docs = {**_REAL_WS, **_REAL_EDGE}
    if "SFR" in model:
        for name, text in docs.items():
            for size, ov in ((64, 0), (64, 8)):
                assert _real_kind(counter, text, size, ov) == VERIFIED, (name, size, ov)
        return
    kinds = {name: _real_kind(counter, text, 64, 0) for name, text in _REAL_WS.items()}
    assert all(k in (VERIFIED, VERIFIED_WS) for k in kinds.values()), kinds
    assert VERIFIED_WS in kinds.values(), kinds
    for name, text in _REAL_EDGE.items():
        assert _real_kind(counter, text, 64, 0) == UNVERIFIABLE, name


# --- v1.6.20 Gap A: the load summary carries the degraded reasons -----------------


def test_load_summary_glossary_file_carries_degraded_reasons(
        corpus: dict[str, Path], load: Any, tmp_path: Path) -> None:
    """The bulk/JATS load records ``degraded_reasons`` and the distinct
    ``glossary_version``s of the WHOLE file (not just the rows it loaded),
    so the API can lift them from the load summary without the step's
    ``glossary-summary.json``."""
    import dataclasses

    g = read_glossary_file(corpus["gfile"])
    rows = sorted(g.rows.values(), key=lambda r: r.doc_id)
    rows = [dataclasses.replace(rows[0], degraded="timeout"),
            dataclasses.replace(rows[1], degraded="timeout"), *rows[2:]]
    extra = build_row("zz-not-loaded", "An unrelated document. " + PAD,
                      SymbolGlossaryAttacher())
    rows.append(dataclasses.replace(extra, degraded="http_400"))
    gpath = tmp_path / "degraded.jsonl"
    write_glossary_file(gpath, rows, producer="test", config=g.config)
    rc, chunks, _ = load(str(corpus["emb"]), "--glossary-file", str(gpath))
    assert rc == 0 and chunks
    prov = json.loads((tmp_path / "summary.json").read_text())["glossary_file"]
    assert prov["degraded_reasons"] == {"http_400": 1, "timeout": 2}
    assert sum(prov["degraded_reasons"].values()) == prov["counts"]["degraded"] == 3
    assert prov["glossary_versions"] == [EXTRACTOR_VERSION]
    # a file with nothing degraded records an empty object, not a missing key
    assert load(str(corpus["emb"]), "--glossary-file", str(corpus["gfile"]))[0] == 0
    clean = json.loads((tmp_path / "summary.json").read_text())["glossary_file"]
    assert clean["degraded_reasons"] == {} and clean["glossary_versions"] == [EXTRACTOR_VERSION]
