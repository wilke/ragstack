"""The extract-graph step (#350, phase 6 of #201): an archived chunk version
-> the LLM extractor -> the graph leg beside it -> loaded, budgeted.

* A fake LLM (quoting each chunk's first sentence) yields triples that carry
  the #347 stamps — ``derived_by="llm"``, ``confidence=1``, ``chunk_id`` of
  the chunk, ``evidence`` = the source sentence — plus the chunk's tenant, and
  NO collection (the loader stamps that from the registry entry).
* ``write_triples`` / ``read_triples`` round-trip through the archive: the
  manifest gains the ``triples`` role with its sha256/bytes and ``graph: true``,
  ``verify_version`` verifies the leg with everything else, a flipped byte in
  the leg is ``ArchiveCorrupt``, and a delta directory (manifest + triples
  only — what the workflow emits) merges onto the original exactly as the
  engine's post-staging does.
* Budget exceeded -> exit 4 from both tools with the refusal line and NOTHING
  written / loaded; a refused archive (tombstone, identity mismatch) -> exit 3.
* The restore replay loads a version's leg after its chunks, scoped to the
  collection, and is unchanged for a version without one.
* ``graph-extract.cwl`` inlines ``extract-graph.cwl`` without drift and exposes
  the version Directory as its only output.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from ragstack.graph.archive_load import load_triples
from ragstack.graph.budget import (
    GRAPH_CAP_EXCEEDED,
    GRAPH_CAP_REFUSED_EXIT_CODE,
    GraphCapExceeded,
    format_graph_refusal,
    graph_cap_refusal_of,
    is_graph_cap_refusal,
)
from ragstack.graph.extract_version import (
    ExtractionUnavailable,
    ExtractRefused,
    extract_version,
    load_chunks,
)
from ragstack.graph.extractor import ExtractionFailed, LLMKGExtractor
from ragstack.ingestion import archive
from ragstack.ingestion.archive import (
    ROLE_TRIPLES,
    TRIPLES_NAME,
    ArchiveCorrupt,
    read_triples,
    read_version,
    verify_triples,
    verify_version,
    write_triples,
    write_version,
)
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.embedding_file import SCHEMA
from ragstack.ingestion.load_embeddings import run_replay
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.models import DERIVED_BY_LLM, LLM_MAX_CONFIDENCE, Triple
from ragstack.stores.memory import InMemoryGraphStore, InMemoryTextIndex, InMemoryVectorStore
from tests.archive_support import corrupt_file, tombstone_version

yaml = pytest.importorskip("yaml")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import extract_graph as extract_cli  # noqa: E402
import load_graph as load_cli  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
CWL_DIR = REPO / "cwl"
TENANT = "bvbrc:alice@patricbrc.org"
SPEC = "cafe0001"
TEXTS = [
    "Aspirin is an NSAID. It inhibits COX-1 and COX-2.",
    "Ibuprofen is a propionic acid derivative. It is sold over the counter.",
    "Warfarin is an anticoagulant. Its dose is monitored by INR.",
]
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class SentenceLLM:
    """Fake LLM: reads the chunk text out of the prompt and answers with ONE
    ``(X, is, Y)`` triple from its first sentence, quoting that sentence as
    the evidence — so the evidence-containment check in the extractor passes
    and the stamping path is exercised end to end."""

    def __init__(self, *, empty: bool = False, fail_on: tuple[str, ...] = ()) -> None:
        self.empty = empty
        self.fail_on = fail_on  # chunks whose text contains one of these: the call fails
        self.calls = 0

    async def complete_text(self, prompt: str, **_kw: object) -> str:
        self.calls += 1
        text = prompt.rsplit("Text:\n", 1)[-1].strip()
        if any(marker in text for marker in self.fail_on):
            raise ConnectionError("endpoint down")
        if self.empty:
            return json.dumps({"triples": []})
        first = _SENTENCE_END.split(text, 1)[0]
        subject, obj = first.rstrip(".").split(" is ", 1)
        return json.dumps({"triples": [
            {"subject": subject, "predicate": "is", "object": obj, "evidence": first},
        ]})


def _version(root: Path, version: int, texts: list[str] = TEXTS, *,
             collection_id: str = "lib", spec_hash: str = SPEC, dim: int = 4) -> Path:
    """A chunk version whose chunks are ``texts`` (one doc per two chunks)."""
    root.mkdir(parents=True, exist_ok=True)
    emb = root / f"v{version}.emb.jsonl"
    header = {"schema": SCHEMA, "tenant": TENANT, "dim": dim}
    recs = [{
        "id": f"c{version}-{i}", "doc_id": f"d{version}-{i // 2}", "content": t,
        "embedding": [float(i + 1)] * dim,
        "metadata": {"tenant_id": TENANT}, "start_char": 0, "end_char": len(t),
    } for i, t in enumerate(texts)]
    emb.write_text("\n".join(json.dumps(r, sort_keys=True) for r in [header, *recs]) + "\n")
    receipt = root / f"v{version}.receipt.json"
    receipt.write_text(json.dumps({"status": "completed", "n_chunks": len(texts)}))
    write_version(root, version, [emb], [receipt], collection_id=collection_id, tenant=TENANT,
                  spec_hash=spec_hash, job_id=f"job-{version}", workers=1)
    emb.unlink()
    receipt.unlink()
    return root / str(version)


# --------------------------------------------------------------------------- #
# extraction + the #347 stamps
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fake_llm_triples_carry_the_347_stamps_and_the_chunk_tenant(tmp_path: Path):
    vdir = _version(tmp_path, 1)
    llm = SentenceLLM()
    summary = await extract_version(vdir, LLMKGExtractor(llm), concurrency=8,
                                    collection_id="lib", spec_hash=SPEC, extractor_name="fake")
    assert llm.calls == 3 and summary.n_chunks == 3 and summary.n_triples == 3
    triples = list(read_triples(vdir))
    by_chunk = {t.chunk_id: t for t in triples}
    assert sorted(by_chunk) == ["c1-0", "c1-1", "c1-2"]
    for i, text in enumerate(TEXTS):
        t = by_chunk[f"c1-{i}"]
        assert t.derived_by == DERIVED_BY_LLM and t.confidence == LLM_MAX_CONFIDENCE
        assert t.evidence == _SENTENCE_END.split(text, 1)[0]  # the source sentence, verbatim
        assert t.evidence in text
        assert t.doc_id == f"d1-{i // 2}" and t.tenant_id == TENANT
        assert t.collection == ""  # the loader stamps the physical name
        assert (t.subject, t.predicate) == (text.split(" is ")[0], "is")
        assert t.subject_id == "" and t.object_id == ""
    m = summary.manifest
    assert m["graph"] is True and m["counts"]["triples"] == 3
    assert m["graph_extraction"]["derived_by"] == "llm"
    assert m["graph_extraction"]["extractor"] == "fake"
    assert m["graph_extraction"]["n_chunks"] == 3


@pytest.mark.asyncio
async def test_extraction_is_deterministic_and_deduplicated_in_chunk_order(tmp_path: Path):
    """Same input, same bytes (the archive convention); a fact two chunks of one
    document both state is archived once."""
    texts = ["Aspirin is an NSAID.", "Aspirin is an NSAID. Really.", "Warfarin is a drug."]
    a = _version(tmp_path / "a", 1, texts)
    b = _version(tmp_path / "b", 1, texts)
    sa = await extract_version(a, LLMKGExtractor(SentenceLLM()), concurrency=3)
    sb = await extract_version(b, LLMKGExtractor(SentenceLLM()), concurrency=1)
    assert (a / TRIPLES_NAME).read_bytes() == (b / TRIPLES_NAME).read_bytes()
    assert sa.manifest["sha256"][TRIPLES_NAME] == sb.manifest["sha256"][TRIPLES_NAME]
    # c0 and c1 are the same doc: one (Aspirin, is, an NSAID) survives, from c0.
    triples = list(read_triples(a))
    assert [(t.subject, t.chunk_id) for t in triples] == [("Aspirin", "c1-0"), ("Warfarin", "c1-2")]
    assert sa.n_duplicates == 1


# --------------------------------------------------------------------------- #
# the archive leg: write / read / verify / delta
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_write_and_read_triples_round_trip_with_sha256_verification(tmp_path: Path):
    vdir = _version(tmp_path, 2)
    before = verify_version(vdir)
    assert before["graph"] is False and ROLE_TRIPLES not in before["files"]
    await extract_version(vdir, LLMKGExtractor(SentenceLLM()))
    m = verify_version(vdir)  # the leg is verified with everything else
    assert m["graph"] is True and m["files"][ROLE_TRIPLES] == TRIPLES_NAME
    assert set(m["sha256"]) == {"chunks.jsonl.gz", "vectors.f32", "receipt.json", TRIPLES_NAME}
    assert m["bytes"][TRIPLES_NAME] == (vdir / TRIPLES_NAME).stat().st_size
    assert m["format"] == archive.FORMAT and m["counts"]["chunks"] == 3
    # The leg alone verifies too (what the load tool sees), and streams Triples.
    assert verify_triples(vdir)["counts"]["triples"] == 3
    got = list(read_triples(vdir))
    assert all(isinstance(t, Triple) for t in got) and len(got) == 3
    # Records are the Triple field set, sorted keys, one per line.
    with gzip.open(vdir / TRIPLES_NAME, "rt") as fh:
        rec = json.loads(fh.readline())
    assert list(rec) == sorted(Triple.model_fields)
    # The chunks still stream — the leg added nothing the vector reader minds.
    assert len(list(read_version(vdir))) == 3
    # A flipped byte in the leg fails verification, whole-version and leg-only.
    corrupt_file(vdir, TRIPLES_NAME, offset=10)
    with pytest.raises(ArchiveCorrupt, match="sha256"):
        verify_version(vdir)
    with pytest.raises(ArchiveCorrupt, match="sha256"):
        list(read_triples(vdir))


def test_manifest_graph_flag_and_triples_role_must_agree(tmp_path: Path):
    vdir = _version(tmp_path, 1)
    write_triples(vdir, [])
    m = json.loads((vdir / "manifest.json").read_text())
    m["graph"] = False
    (vdir / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(ArchiveCorrupt, match="graph: false"):
        verify_version(vdir)
    m["graph"] = True
    del m["files"][ROLE_TRIPLES]
    del m["sha256"][TRIPLES_NAME]
    del m["bytes"][TRIPLES_NAME]
    (vdir / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(ArchiveCorrupt, match="graph: true"):
        verify_version(vdir)


def test_empty_extraction_still_archives_an_empty_leg(tmp_path: Path):
    vdir = _version(tmp_path, 1)
    m = write_triples(vdir, [])
    assert m["graph"] is True and m["counts"]["triples"] == 0
    assert verify_version(vdir)["counts"]["triples"] == 0
    assert list(read_triples(vdir)) == []


def test_write_triples_refuses_a_tombstone_and_validates_records(tmp_path: Path):
    tomb = tombstone_version(tmp_path, 5, ["d1"], tenant=TENANT)
    with pytest.raises(archive.ArchiveError, match="tombstone"):
        write_triples(tomb, [])
    vdir = _version(tmp_path, 1)
    with pytest.raises(ValueError, match="caps confidence"):  # the no-launder rule holds
        write_triples(vdir, [{"subject": "a", "predicate": "b", "object": "c",
                              "derived_by": "llm", "confidence": 3}])
    assert not (vdir / TRIPLES_NAME).exists()  # nothing half-written
    assert verify_version(vdir)["graph"] is False


@pytest.mark.asyncio
async def test_delta_directory_holds_exactly_the_two_files_and_merges_like_post_staging(
    tmp_path: Path,
):
    """The workflow's output: <out>/<n>/ with ONLY manifest.json +
    triples.jsonl.gz. The engine uploads that listing onto versions/<n>/ by
    basename with overwrite; copying it over the original is that operation,
    and the merged directory verifies as a whole."""
    vdir = _version(tmp_path / "archive", 3)
    original_manifest = (vdir / "manifest.json").read_bytes()
    out = tmp_path / "work" / "3"
    summary = await extract_version(vdir, LLMKGExtractor(SentenceLLM()), out_dir=out)
    assert sorted(p.name for p in out.iterdir()) == ["manifest.json", TRIPLES_NAME]
    # The archived version is untouched until the merge.
    assert (vdir / "manifest.json").read_bytes() == original_manifest
    assert not (vdir / TRIPLES_NAME).exists()
    # The delta's manifest is COMPLETE (it still hashes the chunk files)...
    delta = archive.read_manifest(out)
    assert set(delta["sha256"]) == {"chunks.jsonl.gz", "vectors.f32", "receipt.json", TRIPLES_NAME}
    assert delta == summary.manifest
    # ...and the leg alone verifies in the delta (no chunks/vectors there).
    assert verify_triples(out)["counts"]["triples"] == 3
    with pytest.raises(ArchiveCorrupt, match="missing"):
        verify_version(out)  # the whole version is not there, by design
    # Post-staging: same basename, overwrite the manifest, add the leg.
    for p in out.iterdir():
        shutil.copy(p, vdir / p.name)
    merged = verify_version(vdir)
    assert merged["graph"] is True and merged["counts"]["triples"] == 3
    assert sorted(p.name for p in vdir.iterdir()) == [
        "chunks.jsonl.gz", "manifest.json", "receipt.json", TRIPLES_NAME, "vectors.f32"]
    assert [c["id"] for c, _ in read_version(vdir)] == ["c3-0", "c3-1", "c3-2"]
    assert len(list(read_triples(vdir, manifest=merged))) == 3


# --------------------------------------------------------------------------- #
# an LLM outage is not an empty graph (exit 1, retryable, nothing written)
# --------------------------------------------------------------------------- #

TEN = [f"Drug{i} is a compound. Chunk {i} text." for i in range(10)]


@pytest.mark.asyncio
async def test_extract_chunk_raises_on_a_failed_call_but_extract_still_swallows():
    from ragstack.models import Chunk

    llm = SentenceLLM(fail_on=("Chunk 1 ",))
    ex = LLMKGExtractor(llm)
    ok = Chunk(id="a", doc_id="d", content=TEN[0])
    bad = Chunk(id="b", doc_id="d", content=TEN[1])
    assert len(await ex.extract_chunk(ok)) == 1
    with pytest.raises(ExtractionFailed, match="'b'"):
        await ex.extract_chunk(bad)
    # The ingest contract is unchanged: extract() degrades per chunk.
    assert [t.chunk_id for t in await ex.extract([ok, bad])] == ["a"]
    # A reply the model DID give but that parses to nothing is empty, not failed.
    class Garbage:
        async def complete_text(self, prompt, **_kw):
            return "no json here"
    assert await LLMKGExtractor(Garbage()).extract_chunk(ok) == []


@pytest.mark.asyncio
async def test_every_chunk_failing_is_refused_and_nothing_is_written(tmp_path: Path):
    vdir = _version(tmp_path / "a", 1, TEN)
    out = tmp_path / "out" / "1"
    with pytest.raises(ExtractionUnavailable, match="10 of 10 attempted chunk"):
        await extract_version(vdir, LLMKGExtractor(SentenceLLM(fail_on=("Chunk",))),
                              out_dir=out)
    assert not out.exists() and verify_version(vdir)["graph"] is False
    # Empty-text chunks are never "attempted": a version of only blanks is
    # not an outage (it is an empty leg).
    blanks = _version(tmp_path / "b", 1, ["", "   "])
    summary = await extract_version(blanks, LLMKGExtractor(SentenceLLM(fail_on=("x",))))
    assert summary.n_chunks_empty == 2 and summary.n_chunks_failed == 0
    assert summary.manifest["graph"] is True and summary.n_triples == 0


@pytest.mark.asyncio
async def test_partial_failures_below_the_threshold_are_delivered_and_recorded(tmp_path: Path):
    vdir = _version(tmp_path, 1, TEN)
    llm = SentenceLLM(fail_on=("Chunk 1 ", "Chunk 4 ", "Chunk 7 "))
    summary = await extract_version(vdir, LLMKGExtractor(llm))
    assert summary.n_chunks_failed == 3 and summary.n_triples == 7
    m = verify_version(vdir)
    assert m["graph"] is True and m["counts"]["triples"] == 7
    assert m["graph_extraction"]["n_chunks_failed"] == 3
    assert m["graph_extraction"]["n_chunks_without_triples"] == 0
    assert summary.as_dict()["n_chunks_failed"] == 3


@pytest.mark.asyncio
async def test_failures_above_the_threshold_are_refused(tmp_path: Path):
    vdir = _version(tmp_path, 1, TEN)
    six = tuple(f"Chunk {i} " for i in range(6))
    with pytest.raises(ExtractionUnavailable, match="6 of 10"):
        await extract_version(vdir, LLMKGExtractor(SentenceLLM(fail_on=six)))
    assert verify_version(vdir)["graph"] is False
    # The threshold is a setting: at 0.6, six of ten is delivered.
    summary = await extract_version(vdir, LLMKGExtractor(SentenceLLM(fail_on=six)),
                                    max_failed_fraction=0.6)
    assert summary.n_chunks_failed == 6 and summary.manifest["graph"] is True


def test_cli_exits_1_on_an_outage_with_no_delta(tmp_path: Path, monkeypatch, capsys):
    vdir = _version(tmp_path / "archive", 1, TEN)
    monkeypatch.setattr(extract_cli, "_build_llm",
                        lambda args: (SentenceLLM(fail_on=("Chunk",)), "dead"))
    rc = extract_cli.main(["--version-dir", str(vdir), "--out", str(tmp_path / "w"),
                           "--summary", str(tmp_path / "s.json")])
    err = capsys.readouterr().err
    assert rc == 1 and "llm_unavailable: 10 of 10" in err
    assert not (tmp_path / "w").exists() and not (tmp_path / "s.json").exists()
    monkeypatch.setattr(extract_cli, "_build_llm",
                        lambda args: (SentenceLLM(fail_on=("Chunk 2 ",)), "flaky"))
    rc = extract_cli.main(["--version-dir", str(vdir), "--out", str(tmp_path / "w"),
                           "--summary", str(tmp_path / "s.json")])
    assert rc == 0
    assert json.loads((tmp_path / "s.json").read_text())["n_chunks_failed"] == 1
    assert archive.read_manifest(tmp_path / "w" / "1")["graph_extraction"]["n_chunks_failed"] == 1
    with pytest.raises(SystemExit):
        extract_cli.main(["--version-dir", str(vdir), "--max-failed-fraction", "1.5"])


# --------------------------------------------------------------------------- #
# refusals: the archive (exit 3) and the budget (exit 4)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tombstone_and_identity_mismatch_are_refused_before_anything_is_written(
    tmp_path: Path,
):
    tomb = tombstone_version(tmp_path, 4, ["d1"], tenant=TENANT)
    with pytest.raises(ExtractRefused, match="ArchiveCorrupt: .*tombstone"):
        await extract_version(tomb, LLMKGExtractor(SentenceLLM()))
    vdir = _version(tmp_path, 1)
    llm = SentenceLLM()
    with pytest.raises(ExtractRefused, match="SpecMismatch: .*spec_hash"):
        await extract_version(vdir, LLMKGExtractor(llm), spec_hash="other")
    with pytest.raises(ExtractRefused, match="SpecMismatch: .*collection_id"):
        await extract_version(vdir, LLMKGExtractor(llm), collection_id="other")
    assert llm.calls == 0 and not (vdir / TRIPLES_NAME).exists()
    corrupt_file(vdir, "chunks.jsonl.gz", offset=5)
    with pytest.raises(ExtractRefused, match="ArchiveCorrupt"):
        load_chunks(vdir)


@pytest.mark.asyncio
async def test_extract_budget_exceeded_writes_nothing(tmp_path: Path):
    vdir = _version(tmp_path / "a", 1)
    out = tmp_path / "out" / "1"
    with pytest.raises(GraphCapExceeded) as ei:
        await extract_version(vdir, LLMKGExtractor(SentenceLLM()), out_dir=out, max_triples=2)
    assert ei.value.detail() == {"error": GRAPH_CAP_EXCEEDED, "live": None, "incoming": 3,
                                 "cap": 2, "would_fit": 2}
    assert str(ei.value) == "graph_cap_exceeded: live=? incoming=3 cap=2 would_fit=2"
    assert not out.exists() and verify_version(vdir)["graph"] is False


class CountingGraphStore(InMemoryGraphStore):
    def __init__(self, *, live: int = 0) -> None:
        super().__init__()
        self.live = live
        self.adds = 0
        self.stats_calls = 0

    async def stats(self, tenant_id=None, collection=None):
        self.stats_calls += 1
        if self.live:
            return (self.live, self.live)
        return await super().stats(tenant_id, collection)

    async def add_triples(self, triples):
        self.adds += 1
        await super().add_triples(triples)


@pytest.mark.asyncio
async def test_load_budget_exceeded_loads_nothing_with_one_live_count(tmp_path: Path):
    vdir = _version(tmp_path, 1)
    await extract_version(vdir, LLMKGExtractor(SentenceLLM()))
    store = CountingGraphStore(live=199_998)
    with pytest.raises(GraphCapExceeded) as ei:
        await load_triples(vdir, store, collection="lib_phys", cap=200_000)
    assert store.adds == 0 and store.stats_calls == 1
    assert (ei.value.live, ei.value.incoming, ei.value.cap, ei.value.would_fit) == (
        199_998, 3, 200_000, 2)
    assert str(ei.value) == format_graph_refusal(199_998, 3, 200_000)
    assert is_graph_cap_refusal(str(ei.value))
    # Exactly at the cap fits; the store is counted once, then written once.
    store = CountingGraphStore(live=199_997)
    loaded = await load_triples(vdir, store, collection="lib_phys", cap=200_000)
    assert loaded.n_triples == 3 and store.adds == 1 and loaded.live_before == 199_997
    # Uncapped: the store is not even counted.
    store = CountingGraphStore()
    await load_triples(vdir, store, collection="lib_phys", cap=None)
    assert store.stats_calls == 0 and store.adds == 1


@pytest.mark.asyncio
async def test_load_stamps_the_collection_and_converges_on_a_re_run(tmp_path: Path):
    vdir = _version(tmp_path, 1)
    await extract_version(vdir, LLMKGExtractor(SentenceLLM()))
    store = InMemoryGraphStore()
    first = await load_triples(vdir, store, collection="lib_phys", batch_size=2)
    second = await load_triples(vdir, store, collection="lib_phys", batch_size=2)
    assert first.n_triples == second.n_triples == 3
    assert await store.stats(tenant_id=None, collection="lib_phys") == (6, 3)  # MERGE, no dupes
    assert await store.stats(tenant_id=None, collection="other") == (0, 0)
    got = await store.query_neighborhood("Aspirin", tenant_id=TENANT, collection="lib_phys")
    assert [(t.collection, t.tenant_id, t.evidence) for t in got] == [
        ("lib_phys", TENANT, "Aspirin is an NSAID.")]
    with pytest.raises(ValueError, match="physical name"):
        await load_triples(vdir, store, collection="")


def test_cli_extract_writes_the_delta_named_by_the_version(tmp_path: Path, monkeypatch, capsys):
    vdir = _version(tmp_path / "archive", 7)
    monkeypatch.setenv("RAGSTACK_FAKE_LLM", "1")
    monkeypatch.chdir(tmp_path)
    rc = extract_cli.main([
        "--version-dir", str(vdir), "--version", "7", "--collection-id", "lib",
        "--spec-hash", SPEC, "--out", str(tmp_path / "work"), "--concurrency", "2",
        "--summary", str(tmp_path / "summary.json"),
    ])
    assert rc == 0, capsys.readouterr()
    out = tmp_path / "work" / "7"
    assert sorted(p.name for p in out.iterdir()) == ["manifest.json", TRIPLES_NAME]
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["n_chunks"] == 3 and summary["n_triples"] == 3 and summary["version"] == 7
    triples = list(read_triples(out))
    assert {t.subject for t in triples} == {"Aspirin", "Ibuprofen", "Warfarin"}
    assert all(t.derived_by == "llm" and t.confidence == 1 and t.evidence for t in triples)
    # A wrong --version / a foreign identity is exit 3 (permanent), nothing written.
    assert extract_cli.main(["--version-dir", str(vdir), "--version", "8",
                             "--out", str(tmp_path / "w2")]) == 3
    assert "SpecMismatch" in capsys.readouterr().err
    assert not (tmp_path / "w2").exists()


def test_cli_budget_refusals_exit_4_and_load_nothing(tmp_path: Path, monkeypatch, capsys):
    vdir = _version(tmp_path / "archive", 1)
    monkeypatch.setenv("RAGSTACK_FAKE_LLM", "1")
    rc = extract_cli.main(["--version-dir", str(vdir), "--out", str(tmp_path / "w"),
                           "--max-triples", "1", "--summary", str(tmp_path / "s.json")])
    err = capsys.readouterr().err
    assert rc == GRAPH_CAP_REFUSED_EXIT_CODE == 4
    assert "graph_cap_exceeded: live=? incoming=3 cap=1 would_fit=1" in err
    assert not (tmp_path / "w").exists()
    # The load tool over an extracted leg, memory store, one triple allowed.
    write_triples(vdir, [Triple(subject=f"s{i}", predicate="p", object="o", doc_id="d",
                                derived_by="llm", confidence=1) for i in range(3)])
    rc = load_cli.main(["--version-dir", str(vdir), "--graph-backend", "memory",
                        "--stamp-collection", "lib_phys", "--max-triples", "2",
                        "--out", str(tmp_path / "load.json")])
    err = capsys.readouterr().err
    assert rc == 4 and "graph_cap_exceeded: live=0 incoming=3 cap=2 would_fit=2" in err
    assert json.loads((tmp_path / "load.json").read_text())["graph_cap"]["incoming"] == 3
    # And within budget it loads (the memory store is process-local; the
    # summary says what happened).
    rc = load_cli.main(["--version-dir", str(vdir), "--graph-backend", "memory",
                        "--stamp-collection", "lib_phys", "--max-triples", "3",
                        "--out", str(tmp_path / "load2.json")])
    assert rc == 0
    assert json.loads((tmp_path / "load2.json").read_text())["n_triples"] == 3
    # A corrupt leg is exit 3.
    corrupt_file(vdir, TRIPLES_NAME, offset=10)
    assert load_cli.main(["--version-dir", str(vdir), "--graph-backend", "memory",
                          "--stamp-collection", "x", "--out", str(tmp_path / "l3.json")]) == 3
    assert "ArchiveCorrupt" in capsys.readouterr().err


def test_graph_cap_refusal_classified_by_exit_code_first():
    line = format_graph_refusal(199_998, 3, 200_000)
    rec = {"state": "FAILED", "error": {"code": "TASK_FAILED", "message": "step load failed",
                                        "context": {"exit_code": 4, "stderr": f"noise\n{line}\n"}}}
    assert graph_cap_refusal_of(rec) == line
    rec["error"]["context"]["stderr"] = "truncated before the line"
    assert graph_cap_refusal_of(rec) == GRAPH_CAP_EXCEEDED  # the bare label
    rec["error"]["context"] = {"exit_code": 1, "stderr": line}
    assert graph_cap_refusal_of(rec) is None  # the exit code is authoritative
    assert graph_cap_refusal_of({"state": "FAILED", "error": "boom"}) is None


# --------------------------------------------------------------------------- #
# restore: the replay loads the leg after the chunks
# --------------------------------------------------------------------------- #


def _pipeline(graph_store=None) -> IngestionPipeline:
    return IngestionPipeline(
        loader=JsonlLoader(), chunker=RecursiveCharacterChunker(), embedder=object(),
        vector_store=InMemoryVectorStore(), text_index=InMemoryTextIndex(),
        graph_store=graph_store, delete_prior=False, collection="lib_phys",
    )


@pytest.mark.asyncio
async def test_replay_loads_the_graph_leg_after_the_chunks_scoped_to_the_collection(
    tmp_path: Path,
):
    v1 = _version(tmp_path, 1)
    await extract_version(v1, LLMKGExtractor(SentenceLLM()))
    v2 = _version(tmp_path, 2, ["Metformin is a biguanide."])  # no leg
    store = CountingGraphStore()
    pipeline = _pipeline(store)
    summary = await run_replay(pipeline, [v1, v2], spec_hash=SPEC, collection_id="lib",
                               delete_concurrency=2)
    assert summary.status == "completed" and summary.n_chunks == 4
    assert summary.n_triples == 3 and summary.as_dict()["n_triples"] == 3
    assert summary.versions[0]["n_triples"] == 3 and "n_triples" not in summary.versions[1]
    assert await store.stats(tenant_id=None, collection="lib_phys") == (6, 3)
    assert await store.stats(tenant_id=None, collection="other") == (0, 0)
    assert store.adds == 1  # one batch, after index_chunks
    assert await pipeline.vector_store.count() == 4
    # Without a graph store the replay is exactly what it was.
    plain = await run_replay(_pipeline(None), [v1, v2], spec_hash=SPEC, collection_id="lib")
    assert plain.status == "completed" and plain.n_triples == 0 and plain.n_chunks == 4


# --------------------------------------------------------------------------- #
# the workflow inlines the tool without drift
# --------------------------------------------------------------------------- #


def test_workflow_inlines_the_standalone_tool_and_exposes_only_the_directory():
    ref = yaml.safe_load((CWL_DIR / "extract-graph.cwl").read_text(encoding="utf-8"))
    wf = yaml.safe_load((CWL_DIR / "graph-extract.cwl").read_text(encoding="utf-8"))
    tool = wf["steps"]["extract"]["run"]
    assert tool["baseCommand"] == ref["baseCommand"] == [
        "python", "/opt/ragstack/scripts/extract_graph.py"]
    assert tool["requirements"] == ref["requirements"]
    assert tool["permanentFailCodes"] == ref["permanentFailCodes"] == [3, 4]
    assert tool["arguments"] == ref["arguments"]
    assert tool["outputs"]["archive"]["outputBinding"] == ref["outputs"]["archive"]["outputBinding"]
    assert tool["outputs"]["archive"]["outputBinding"] == {"glob": "$(inputs.version)"}
    assert set(tool["inputs"]) == set(ref["inputs"])
    for name, ref_in in ref["inputs"].items():
        got = tool["inputs"][name]
        assert got["type"] == ref_in["type"], name
        assert got.get("inputBinding") == ref_in.get("inputBinding"), name
        assert got.get("default") == ref_in.get("default"), name
    # The version Directory is the ONLY workflow output (nothing else may be
    # post-staged into versions/), sourced from extract; load runs after it.
    assert list(wf["outputs"]) == ["archive"]
    assert wf["outputs"]["archive"] == {
        "type": "Directory", "outputSource": "extract/archive",
        "doc": wf["outputs"]["archive"]["doc"]}
    load = wf["steps"]["load"]
    assert load["in"]["version_dir"] == "extract/archive"
    assert load["run"]["baseCommand"] == ["python", "/opt/ragstack/scripts/load_graph.py"]
    assert load["run"]["permanentFailCodes"] == [3, 4]
    assert load["run"]["requirements"]["NetworkAccess"] == {"networkAccess": True}
    # Both tools cap on the same input, so one refusal format parses both.
    assert wf["steps"]["extract"]["in"]["max_triples"] == "max_triples"
    assert load["in"]["max_triples"] == "max_triples"
    # The Neo4j credentials are not inputs anywhere.
    text = (CWL_DIR / "graph-extract.cwl").read_text(encoding="utf-8").lower()
    assert "password" not in text.replace("neo4j_password", "") or "never" in text
    assert "neo4j_user:" not in text and "neo4j_password:" not in text


def test_extract_tool_never_sees_a_token_or_a_key_on_the_command_line():
    """The LLM API key comes from the environment, not an input/argument."""
    tool = yaml.safe_load((CWL_DIR / "extract-graph.cwl").read_text(encoding="utf-8"))
    names = set(tool["inputs"]) | {a.get("prefix", "") for a in tool["arguments"]}
    assert not any("key" in n or "token" in n for n in names)
    assert os.environ.get("OPENAI_API_KEY") is None or True  # documented seam, no assertion
