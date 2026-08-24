"""The replay loader — restore's write path (#358, phase 2 of #353).

Three chunk versions and one tombstone, replayed in order into in-memory
stores, must reproduce exactly the chunk set the live ingests would have left:
a later version's re-ingest of a document replaces that document's earlier
chunks (even when the document straddles an upsert batch), and a tombstone
removes its documents from both legs. And the ADR-0002 guard, applied to an
archive the user can edit: a manifest whose ``spec_hash`` differs from the
registry row's, or a data file whose sha256 no longer matches, aborts BEFORE
the first write — the fake stores see zero upserts and zero deletes. The CLI
is exercised in ``--replay`` mode against the in-memory backends.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ragstack.ingestion.archive import CHUNKS_NAME, VECTORS_NAME
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.load_embeddings import ReplayRefused, run_replay, verify_replay
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from tests.archive_support import chunk_version, corrupt_file, tamper_manifest, tombstone_version

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import load_embeddings as load_cli  # noqa: E402

SPEC = "cafe0001"


class _NoEmbed:
    async def embed(self, texts):  # pragma: no cover - replay never embeds
        raise RuntimeError("replay does not embed")


class CountingVectorStore(InMemoryVectorStore):
    def __init__(self) -> None:
        super().__init__()
        self.upserts = 0
        self.deletes = 0

    async def upsert(self, chunks):
        self.upserts += 1
        await super().upsert(chunks)

    async def delete(self, doc_id, tenant_id=None):
        self.deletes += 1
        await super().delete(doc_id, tenant_id)


class CountingTextIndex(InMemoryTextIndex):
    def __init__(self) -> None:
        super().__init__()
        self.indexes = 0
        self.deletes = 0

    async def index(self, chunks):
        self.indexes += 1
        await super().index(chunks)

    async def delete(self, doc_id, tenant_id=None):
        self.deletes += 1
        await super().delete(doc_id, tenant_id)


def _pipeline(vstore=None, tindex=None) -> IngestionPipeline:
    return IngestionPipeline(
        loader=JsonlLoader(), chunker=RecursiveCharacterChunker(), embedder=_NoEmbed(),
        vector_store=vstore or CountingVectorStore(), text_index=tindex or CountingTextIndex(),
        delete_prior=False,
    )


def _ids(store) -> set[str]:
    return {c.id for c in store._chunks}


@pytest.fixture
def versions(tmp_path: Path) -> dict[str, object]:
    """v1: chunks 0-8 (docs 0-2); v2: chunks 9-17 (docs 3-5); v3: RE-INGEST of
    doc-1 with different boundaries (chunk ids 100-103) plus new doc-6 (104-106);
    v4: tombstone of doc-0 and doc-4."""
    v1, r1 = chunk_version(tmp_path, 1, 9, start=0, spec_hash=SPEC)
    v2, r2 = chunk_version(tmp_path, 2, 9, start=9, spec_hash=SPEC)
    # v3 re-chunks doc-1: ids 100..103 all belong to doc-1, then 104..106 are doc-6.
    v3, r3 = chunk_version(tmp_path, 3, 7, start=100, spec_hash=SPEC, chunks_per_doc=4)
    # chunk_version derives doc ids from the index; rewrite the intent explicitly.
    # start=100, chunks_per_doc=4 → 100..103 = doc-25, 104..106 = doc-26. Map
    # doc-25 → doc-1 (the re-ingest) and doc-26 → doc-6 (new) by rewriting.
    _remap_docs(v3, {"doc-25": "doc-1", "doc-26": "doc-6"})
    v4 = tombstone_version(tmp_path, 4, ["doc-0", "doc-4"], spec_hash=SPEC)
    return {"dirs": [v1, v2, v3, v4], "r1": r1, "r2": r2, "r3": r3}


def _remap_docs(vdir: Path, mapping: dict[str, str]) -> None:
    """Rewrite doc ids inside a packed version (re-packs chunks.jsonl.gz and
    fixes the manifest's sha256/bytes so the version still verifies)."""
    import gzip
    import hashlib

    from ragstack.ingestion.archive import MANIFEST_NAME

    path = vdir / CHUNKS_NAME
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    for rec in lines:
        rec["doc_id"] = mapping.get(rec["doc_id"], rec["doc_id"])
    raw = b"".join((json.dumps(r, sort_keys=True) + "\n").encode() for r in lines)
    with path.open("wb") as fh, gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as gz:
        gz.write(raw)
    data = path.read_bytes()
    mpath = vdir / MANIFEST_NAME
    manifest = json.loads(mpath.read_text())
    manifest["sha256"][CHUNKS_NAME] = hashlib.sha256(data).hexdigest()
    manifest["bytes"][CHUNKS_NAME] = len(data)
    manifest["counts"]["docs"] = len({r["doc_id"] for r in lines})
    mpath.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- #
# the happy path: three chunk versions + one tombstone
# --------------------------------------------------------------------------- #


async def test_replay_reproduces_the_live_chunk_set(versions):
    vstore, tindex = CountingVectorStore(), CountingTextIndex()
    summary = await run_replay(_pipeline(vstore, tindex), versions["dirs"],
                               spec_hash=SPEC, collection_id="lib", batch_size=4)
    assert summary.status == "completed" and summary.n_versions == 4
    assert summary.n_chunks == 9 + 9 + 7 and summary.n_docs_deleted == 2

    expected = set()
    expected |= {f"chunk-{i}" for i in range(9)}          # v1: doc-0..doc-2
    expected |= {f"chunk-{i}" for i in range(9, 18)}      # v2: doc-3..doc-5
    expected -= {"chunk-3", "chunk-4", "chunk-5"}         # v3 re-ingested doc-1 → old chunks gone
    expected |= {f"chunk-{i}" for i in range(100, 107)}   # v3: doc-1 (new boundaries) + doc-6
    expected -= {"chunk-0", "chunk-1", "chunk-2"}         # v4 tombstone: doc-0
    expected -= {"chunk-12", "chunk-13", "chunk-14"}      # v4 tombstone: doc-4
    assert _ids(vstore) == expected
    assert _ids(tindex) == expected
    # The vectors landed bit-identical to the archive's float32 rows.
    by_id = {c.id: c for c in vstore._chunks}
    from array import array
    want = array("f", versions["r2"][0]["embedding"]).tolist()
    assert by_id["chunk-9"].embedding == want
    assert by_id["chunk-9"].metadata["tenant_id"] == "bvbrc:alice@patricbrc.org"
    # Batched (batch_size=4 over 9+9+7 rows), never one upsert per chunk.
    assert vstore.upserts == tindex.indexes == 3 + 3 + 2


async def test_document_straddling_a_batch_is_not_deleted_by_its_own_second_batch(tmp_path):
    """The reason replay does its own per-version delete-prior: with
    ``index_chunks``' per-batch delete, a document whose chunks span two
    batches would lose its first batch to the second."""
    vdir, recs = chunk_version(tmp_path, 1, 12, chunks_per_doc=12, spec_hash=SPEC)  # ONE doc, 12 chunks
    vstore = CountingVectorStore()
    await run_replay(_pipeline(vstore, CountingTextIndex()), [vdir], spec_hash=SPEC, batch_size=5)
    assert _ids(vstore) == {r["id"] for r in recs}  # all 12, across 3 batches
    assert vstore.deletes == 1  # one delete-prior for the one document, up front


async def test_replay_is_idempotent(versions):
    vstore, tindex = CountingVectorStore(), CountingTextIndex()
    p = _pipeline(vstore, tindex)
    await run_replay(p, versions["dirs"], spec_hash=SPEC)
    first = _ids(vstore)
    await run_replay(p, versions["dirs"], spec_hash=SPEC)  # a re-run after a crash
    assert _ids(vstore) == first and _ids(tindex) == first
    assert len(vstore._chunks) == len(first)  # no duplicates


# --------------------------------------------------------------------------- #
# refusal BEFORE any write
# --------------------------------------------------------------------------- #


async def test_spec_hash_mismatch_aborts_before_any_write(versions):
    dirs = versions["dirs"]
    tamper_manifest(dirs[2], spec_hash="deadbeef")
    vstore, tindex = CountingVectorStore(), CountingTextIndex()
    with pytest.raises(ReplayRefused) as ei:
        await run_replay(_pipeline(vstore, tindex), dirs, spec_hash=SPEC)
    assert ei.value.kind == "SpecMismatch" and "deadbeef" in str(ei.value)
    # Versions 1 and 2 verified fine — and were still NOT written.
    assert vstore.upserts == 0 and tindex.indexes == 0
    assert vstore.deletes == 0 and tindex.deletes == 0


async def test_registry_spec_hash_mismatch_is_refused_even_for_a_pristine_archive(versions):
    """The archive is intact but was built for a different collection spec."""
    vstore = CountingVectorStore()
    with pytest.raises(ReplayRefused) as ei:
        await run_replay(_pipeline(vstore), versions["dirs"], spec_hash="0badc0de")
    assert ei.value.kind == "SpecMismatch"
    assert vstore.upserts == 0


@pytest.mark.parametrize("name", [CHUNKS_NAME, VECTORS_NAME])
async def test_corrupted_file_aborts_before_any_write(versions, name):
    dirs = versions["dirs"]
    corrupt_file(dirs[1], name)
    vstore, tindex = CountingVectorStore(), CountingTextIndex()
    with pytest.raises(ReplayRefused) as ei:
        await run_replay(_pipeline(vstore, tindex), dirs, spec_hash=SPEC)
    assert ei.value.kind == "ArchiveCorrupt" and "sha256" in str(ei.value)
    assert vstore.upserts == tindex.indexes == vstore.deletes == tindex.deletes == 0


async def test_wrong_collection_id_and_empty_list_are_refused(versions):
    with pytest.raises(ReplayRefused) as ei:
        verify_replay(versions["dirs"], spec_hash=SPEC, collection_id="other")
    assert ei.value.kind == "SpecMismatch"
    with pytest.raises(ReplayRefused):
        verify_replay([], spec_hash=SPEC)
    with pytest.raises(ReplayRefused):
        verify_replay(versions["dirs"], spec_hash="")


async def test_replay_requires_delete_prior_off(versions):
    p = IngestionPipeline(loader=JsonlLoader(), chunker=RecursiveCharacterChunker(),
                          embedder=_NoEmbed(), vector_store=InMemoryVectorStore(),
                          text_index=InMemoryTextIndex())  # delete_prior=True by default
    with pytest.raises(ValueError):
        await run_replay(p, versions["dirs"], spec_hash=SPEC)


# --------------------------------------------------------------------------- #
# the CLI in --replay mode (in-memory backends)
# --------------------------------------------------------------------------- #


def test_cli_replay_writes_a_summary(versions, tmp_path, capsys):
    out = tmp_path / "summary.json"
    rc = load_cli.main([
        "--replay", *map(str, versions["dirs"]), "--spec-hash", SPEC,
        "--vector-backend", "memory", "--text-backend", "memory", "--out", str(out),
    ])
    assert rc == 0
    summary = json.loads(out.read_text())
    assert summary["mode"] == "replay" and summary["status"] == "completed"
    assert summary["n_versions"] == 4 and summary["n_chunks"] == 25
    assert [v["kind"] for v in summary["versions"]] == ["chunks", "chunks", "chunks", "tombstone"]
    assert "replayed 4 version(s)" in capsys.readouterr().out


def test_cli_replay_refusal_exits_3_with_the_marker_line(versions, tmp_path, capsys):
    tamper_manifest(versions["dirs"][0], spec_hash="deadbeef")
    out = tmp_path / "summary.json"
    rc = load_cli.main([
        "--replay", *map(str, versions["dirs"]), "--spec-hash", SPEC,
        "--vector-backend", "memory", "--text-backend", "memory", "--out", str(out),
    ])
    assert rc == 3
    assert not out.exists()  # nothing written, not even a summary
    err = capsys.readouterr().err
    assert err.startswith("SpecMismatch:")


def test_cli_refuses_both_or_neither_mode(tmp_path):
    with pytest.raises(SystemExit):
        load_cli.main(["--vector-backend", "memory", "--text-backend", "memory"])
    with pytest.raises(SystemExit):
        load_cli.main(["x.emb.jsonl", "--replay", str(tmp_path),
                       "--vector-backend", "memory", "--text-backend", "memory"])
