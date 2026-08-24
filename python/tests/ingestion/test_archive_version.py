"""Offline tests for the ``ragstack-archive/1`` version format (#357).

Round-trip a synthetic embed file through the writer and reader (vectors
bit-identical, manifest sha256s verify), prove that corruption of either data
file — and any geometry disagreement — raises ``ArchiveCorrupt`` **before** a
single row is yielded, and pin the tombstone version's contents. The CLI is
exercised in both modes. No store, no network.
"""
from __future__ import annotations

import json
import random
import struct
import sys
from array import array
from pathlib import Path

import pytest

from ragstack.ingestion import archive
from ragstack.ingestion.archive import (
    CHUNKS_NAME,
    FORMAT,
    MANIFEST_NAME,
    RECEIPT_NAME,
    TOMBSTONE_NAME,
    VEC_HEADER_BYTES,
    VECTORS_NAME,
    ArchiveCorrupt,
    ArchiveError,
    pack_vector_header,
    parse_vector_header,
    read_tombstone,
    read_version,
    verify_version,
    write_tombstone,
    write_version,
)
from ragstack.ingestion.embedding_file import SCHEMA

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import archive_version  # noqa: E402

DIM = 16


def _embed_file(path: Path, n: int, *, dim: int = DIM, seed: int = 0,
                start: int = 0, with_count: bool = False) -> list[dict]:
    """Write a ``ragstack.embedding_file/v1`` JSONL with ``n`` synthetic chunks;
    return the records (with their float vectors) in file order."""
    rng = random.Random(seed)
    header: dict = {"schema": SCHEMA, "tenant": "public", "dim": dim}
    if with_count:
        header["count"] = n
    recs = []
    for i in range(start, start + n):
        recs.append({
            "id": f"chunk-{i}", "doc_id": f"doc-{i // 3}",
            "content": f"passage {i} about hybrid retrieval",
            "embedding": [rng.uniform(-1.0, 1.0) for _ in range(dim)],
            "metadata": {"title": f"T{i // 3}", "n": i},
            "start_char": 0, "end_char": 30,
        })
    lines = [json.dumps(header, sort_keys=True)] + [json.dumps(r, sort_keys=True) for r in recs]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return recs


def _receipt(path: Path, **extra) -> dict:
    d = {"shard_id": "s0", "status": "completed", "n_chunks": 9, **extra}
    path.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")
    return d


def _f32(vals: list[float]) -> bytes:
    return array("f", vals).tobytes()


@pytest.fixture
def version_dir(tmp_path: Path) -> tuple[Path, list[dict], dict]:
    recs = _embed_file(tmp_path / "s0.emb.jsonl", 9)
    _receipt(tmp_path / "receipt.json")
    manifest = write_version(tmp_path / "out", 3, [tmp_path / "s0.emb.jsonl"],
                             [tmp_path / "receipt.json"], collection_id="col-a",
                             tenant="public", spec_hash="abcd1234", job_id="job-1",
                             workers=1)
    return tmp_path / "out" / "3", recs, manifest


# --------------------------------------------------------------------------- #
# round trip
# --------------------------------------------------------------------------- #

def test_round_trip_vectors_bit_identical(version_dir) -> None:
    vdir, recs, manifest = version_dir
    assert vdir.name == "3"
    assert sorted(p.name for p in vdir.iterdir()) == sorted(
        [MANIFEST_NAME, CHUNKS_NAME, VECTORS_NAME, RECEIPT_NAME])

    rows = list(read_version(vdir))
    assert len(rows) == len(recs) == 9
    for (chunk, vec), rec in zip(rows, recs, strict=True):
        assert "embedding" not in chunk
        assert chunk["id"] == rec["id"]
        assert chunk["doc_id"] == rec["doc_id"]
        assert chunk["content"] == rec["content"]
        assert chunk["metadata"] == rec["metadata"]
        assert chunk["start_char"] == 0 and chunk["end_char"] == 30
        assert isinstance(vec, array) and vec.typecode == "f" and len(vec) == DIM
        # bit-identical to a float32 cast of the source floats
        assert vec.tobytes() == _f32(rec["embedding"])


def test_manifest_shape_and_sha256s_verify(version_dir) -> None:
    vdir, recs, manifest = version_dir
    on_disk = json.loads((vdir / MANIFEST_NAME).read_text())
    assert on_disk == manifest
    assert manifest["format"] == FORMAT
    assert manifest["collection_id"] == "col-a"
    assert manifest["tenant"] == "public"
    assert manifest["spec_hash"] == "abcd1234"
    assert manifest["version"] == 3
    assert manifest["job_id"] == "job-1"
    assert manifest["counts"] == {"chunks": 9, "docs": 3}
    assert manifest["graph"] is False
    assert manifest["has_tombstone"] is False
    assert manifest["chunks_compression"] == "gzip"
    assert manifest["receipts"] == 1
    assert set(manifest["sha256"]) == {CHUNKS_NAME, VECTORS_NAME, RECEIPT_NAME}
    assert manifest["files"] == {"manifest": MANIFEST_NAME, "chunks": CHUNKS_NAME,
                                 "vectors": VECTORS_NAME, "receipt": RECEIPT_NAME}
    assert "triples" not in manifest["files"]  # reserved role, no file today
    assert manifest["vectors"] == {"dim": DIM, "rows": 9, "dtype": "float32",
                                   "byte_order": "little", "header_bytes": VEC_HEADER_BYTES}
    # independent recomputation of every sha256 + size
    import hashlib
    for name, want in manifest["sha256"].items():
        data = (vdir / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == want, name
        assert manifest["bytes"][name] == len(data), name
    assert verify_version(vdir) == manifest


def test_vectors_file_geometry_and_header(version_dir) -> None:
    vdir, recs, manifest = version_dir
    data = (vdir / VECTORS_NAME).read_bytes()
    assert len(data) == VEC_HEADER_BYTES + 9 * DIM * 4
    assert parse_vector_header(data[:VEC_HEADER_BYTES]) == (DIM, 9)
    assert data[:8] == b"RSF32VEC"
    assert data[:VEC_HEADER_BYTES] == pack_vector_header(DIM, 9)
    # rows are contiguous float32 little-endian, row-aligned with the chunks
    body = data[VEC_HEADER_BYTES:]
    assert body == b"".join(_f32(r["embedding"]) for r in recs)
    assert struct.unpack("<f", body[:4])[0] == array("f", [recs[0]["embedding"][0]])[0]


def test_single_receipt_is_copied_verbatim(version_dir, tmp_path: Path) -> None:
    vdir, _, _ = version_dir
    assert (vdir / RECEIPT_NAME).read_bytes() == (tmp_path / "receipt.json").read_bytes()


def test_multiple_receipts_become_an_array(tmp_path: Path) -> None:
    _embed_file(tmp_path / "a.emb.jsonl", 2)
    r1 = _receipt(tmp_path / "r1.json", shard_id="pdf-a")
    r2 = _receipt(tmp_path / "r2.json", shard_id="pdf-b")
    m = write_version(tmp_path, 1, [tmp_path / "a.emb.jsonl"],
                      [tmp_path / "r1.json", tmp_path / "r2.json"],
                      collection_id="c", tenant="t", workers=1)
    assert m["receipts"] == 2
    assert json.loads((tmp_path / "1" / RECEIPT_NAME).read_text()) == [r1, r2]


def test_multiple_embedding_files_concatenate_in_order(tmp_path: Path) -> None:
    a = _embed_file(tmp_path / "a.emb.jsonl", 4, seed=1, start=0)
    b = _embed_file(tmp_path / "b.emb.jsonl", 5, seed=2, start=100, with_count=True)
    _receipt(tmp_path / "r.json")
    m = write_version(tmp_path, 2, [tmp_path / "a.emb.jsonl", tmp_path / "b.emb.jsonl"],
                      [tmp_path / "r.json"], collection_id="c", tenant="t", workers=1)
    assert m["counts"]["chunks"] == 9
    rows = list(read_version(tmp_path / "2"))
    assert [c["id"] for c, _ in rows] == [r["id"] for r in a + b]
    assert [v.tobytes() for _, v in rows] == [_f32(r["embedding"]) for r in a + b]


def test_worker_pool_preserves_order_and_bytes(tmp_path: Path) -> None:
    """More rows than one pool block (workers x 32) so ordering across blocks
    and across worker processes is exercised; output identical to in-process."""
    recs = _embed_file(tmp_path / "big.emb.jsonl", 2 * 32 + 7, dim=8)
    _receipt(tmp_path / "r.json")
    m1 = write_version(tmp_path / "w1", 5, [tmp_path / "big.emb.jsonl"], [tmp_path / "r.json"],
                       collection_id="c", tenant="t", workers=1)
    m2 = write_version(tmp_path / "w2", 5, [tmp_path / "big.emb.jsonl"], [tmp_path / "r.json"],
                       collection_id="c", tenant="t", workers=2)
    assert m1 == m2  # byte-identical output => identical sha256s
    for name in (CHUNKS_NAME, VECTORS_NAME, MANIFEST_NAME, RECEIPT_NAME):
        assert (tmp_path / "w1" / "5" / name).read_bytes() == (tmp_path / "w2" / "5" / name).read_bytes()
    rows = list(read_version(tmp_path / "w2" / "5"))
    assert [c["id"] for c, _ in rows] == [r["id"] for r in recs]


def test_rerun_is_byte_identical_and_replaces(version_dir, tmp_path: Path) -> None:
    vdir, _, manifest = version_dir
    before = {p.name: p.read_bytes() for p in vdir.iterdir()}
    (vdir / "stale.txt").write_text("from a previous run")
    again = write_version(tmp_path / "out", 3, [tmp_path / "s0.emb.jsonl"],
                          [tmp_path / "receipt.json"], collection_id="col-a",
                          tenant="public", spec_hash="abcd1234", job_id="job-1", workers=1)
    assert again == manifest
    assert {p.name: p.read_bytes() for p in vdir.iterdir()} == before  # stale file gone


# --------------------------------------------------------------------------- #
# corruption -> ArchiveCorrupt before any row is yielded
# --------------------------------------------------------------------------- #

def _flip_byte(path: Path, offset: int) -> None:
    data = bytearray(path.read_bytes())
    data[offset] ^= 0xFF
    path.write_bytes(bytes(data))


def _assert_nothing_yielded(vdir: Path, match: str) -> None:
    it = read_version(vdir)
    with pytest.raises(ArchiveCorrupt, match=match):
        next(it)


def test_flipped_byte_in_vectors_raises_before_first_row(version_dir) -> None:
    vdir, _, _ = version_dir
    # a data byte in the last row: only a whole-file hash catches it
    _flip_byte(vdir / VECTORS_NAME, VEC_HEADER_BYTES + 8 * DIM * 4 + 3)
    _assert_nothing_yielded(vdir, "vectors.f32: sha256")


def test_flipped_byte_in_chunks_raises_before_first_row(version_dir) -> None:
    vdir, _, _ = version_dir
    size = (vdir / CHUNKS_NAME).stat().st_size
    _flip_byte(vdir / CHUNKS_NAME, size - 12)  # inside the deflate stream / trailer
    _assert_nothing_yielded(vdir, "chunks.jsonl.gz: sha256")


def test_flipped_byte_in_receipt_raises(version_dir) -> None:
    vdir, _, _ = version_dir
    _flip_byte(vdir / RECEIPT_NAME, 2)
    _assert_nothing_yielded(vdir, "receipt.json: sha256")


def test_truncated_vectors_raises(version_dir) -> None:
    vdir, _, _ = version_dir
    p = vdir / VECTORS_NAME
    p.write_bytes(p.read_bytes()[:-4])
    _assert_nothing_yielded(vdir, "sha256")


def test_geometry_mismatch_header_vs_manifest(version_dir) -> None:
    """Manifest and vectors header are made mutually consistent with the file
    size but disagree with each other's geometry -> ArchiveCorrupt (the sha256
    map is re-pointed so only the geometry check can fire)."""
    vdir, _, _ = version_dir
    import hashlib
    m = json.loads((vdir / MANIFEST_NAME).read_text())
    m["vectors"]["dim"] = DIM // 2
    m["vectors"]["rows"] = 18
    m["counts"]["chunks"] = 18
    (vdir / MANIFEST_NAME).write_text(json.dumps(m))
    # sha256 still matches (file untouched) -> the header/manifest disagreement fires
    assert hashlib.sha256((vdir / VECTORS_NAME).read_bytes()).hexdigest() == m["sha256"][VECTORS_NAME]
    _assert_nothing_yielded(vdir, "header dim=16 rows=9 != manifest")


def test_geometry_mismatch_file_size(version_dir) -> None:
    """Header + manifest agree on rows x dim, but the file holds fewer bytes."""
    vdir, _, _ = version_dir
    import hashlib
    p = vdir / VECTORS_NAME
    data = p.read_bytes()[:VEC_HEADER_BYTES + 8 * DIM * 4]  # drop the last row
    p.write_bytes(data)
    m = json.loads((vdir / MANIFEST_NAME).read_text())
    m["sha256"][VECTORS_NAME] = hashlib.sha256(data).hexdigest()
    m["bytes"][VECTORS_NAME] = len(data)
    (vdir / MANIFEST_NAME).write_text(json.dumps(m))
    _assert_nothing_yielded(vdir, r"bytes != 64 \+ 9 x 16 x 4")


def test_bad_vector_header_magic(version_dir) -> None:
    vdir, _, _ = version_dir
    import hashlib
    p = vdir / VECTORS_NAME
    data = b"NOTAVEC!" + p.read_bytes()[8:]
    p.write_bytes(data)
    m = json.loads((vdir / MANIFEST_NAME).read_text())
    m["sha256"][VECTORS_NAME] = hashlib.sha256(data).hexdigest()
    (vdir / MANIFEST_NAME).write_text(json.dumps(m))
    _assert_nothing_yielded(vdir, "bad magic")


def test_missing_file_and_wrong_format_tag(version_dir) -> None:
    vdir, _, _ = version_dir
    (vdir / RECEIPT_NAME).unlink()
    _assert_nothing_yielded(vdir, "listed in manifest but missing")
    m = json.loads((vdir / MANIFEST_NAME).read_text())
    m["format"] = "ragstack-archive/99"
    (vdir / MANIFEST_NAME).write_text(json.dumps(m))
    _assert_nothing_yielded(vdir, "is not 'ragstack-archive/1'")
    (vdir / MANIFEST_NAME).unlink()
    _assert_nothing_yielded(vdir, "no manifest.json")


# --------------------------------------------------------------------------- #
# the manifest role map drives the reader
# --------------------------------------------------------------------------- #

def _rewrite_manifest(vdir: Path, fn) -> None:
    m = json.loads((vdir / MANIFEST_NAME).read_text())
    fn(m)
    (vdir / MANIFEST_NAME).write_text(json.dumps(m))


def _rename_role(m: dict, role: str, new_name: str) -> None:
    old = m["files"][role]
    m["files"][role] = new_name
    m["sha256"][new_name] = m["sha256"].pop(old)
    m["bytes"][new_name] = m["bytes"].pop(old)


def test_future_zstd_archive_is_refused_as_unsupported_not_missing(version_dir) -> None:
    """(a) A consistent manifest naming chunks.jsonl.zst with chunks_compression
    zstd must fail on the compression, not on a filename this reader guessed."""
    vdir, _, _ = version_dir
    (vdir / CHUNKS_NAME).rename(vdir / "chunks.jsonl.zst")

    def fn(m):
        _rename_role(m, "chunks", "chunks.jsonl.zst")
        m["chunks_compression"] = "zstd"
    _rewrite_manifest(vdir, fn)
    with pytest.raises(ArchiveCorrupt, match="unsupported chunks_compression 'zstd'") as ei:
        verify_version(vdir)
    assert "sha256 map" not in str(ei.value)
    _assert_nothing_yielded(vdir, "unsupported chunks_compression")


def test_renamed_gzip_chunks_file_reads_through_role_map(version_dir, tmp_path) -> None:
    """(b) The reader follows files.chunks / files.vectors / files.receipt."""
    vdir, recs, _ = version_dir
    (vdir / CHUNKS_NAME).rename(vdir / "chunks-v1.jsonl.gz")
    (vdir / VECTORS_NAME).rename(vdir / "vec.bin")
    (vdir / RECEIPT_NAME).rename(vdir / "load.json")

    def fn(m):
        _rename_role(m, "chunks", "chunks-v1.jsonl.gz")
        _rename_role(m, "vectors", "vec.bin")
        _rename_role(m, "receipt", "load.json")
    _rewrite_manifest(vdir, fn)
    rows = list(read_version(vdir))
    assert [c["id"] for c, _ in rows] == [r["id"] for r in recs]
    assert [v.tobytes() for _, v in rows] == [_f32(r["embedding"]) for r in recs]

    # tombstone role too
    write_tombstone(tmp_path / "t", 1, ["d1"], collection_id="c", tenant="t")
    tdir = tmp_path / "t" / "1"
    (tdir / TOMBSTONE_NAME).rename(tdir / "removed.json")
    _rewrite_manifest(tdir, lambda m: _rename_role(m, "tombstone", "removed.json"))
    assert read_tombstone(tdir) == ["d1"]


def test_files_entry_without_sha256_is_corrupt(version_dir) -> None:
    """(c) Every listed file must be hashed — and nothing unlisted may be."""
    vdir, _, _ = version_dir
    (vdir / "notes.txt").write_text("x")

    def add_unhashed(m):
        m["files"]["triples"] = "notes.txt"
    _rewrite_manifest(vdir, add_unhashed)
    _assert_nothing_yielded(vdir, "every listed file must be hashed")

    def add_unlisted(m):
        del m["files"]["triples"]
        m["sha256"]["notes.txt"] = "0" * 64
    _rewrite_manifest(vdir, add_unlisted)
    _assert_nothing_yielded(vdir, "every listed file must be hashed and nothing else")


def test_manifest_role_map_shape(version_dir) -> None:
    vdir, _, _ = version_dir
    _rewrite_manifest(vdir, lambda m: m.__setitem__("files", [CHUNKS_NAME, VECTORS_NAME]))
    _assert_nothing_yielded(vdir, "'files' must be a role -> filename map")
    _rewrite_manifest(vdir, lambda m: m.__setitem__(
        "files", {"manifest": MANIFEST_NAME, "vectors": VECTORS_NAME, "receipt": RECEIPT_NAME}))
    _assert_nothing_yielded(vdir, "names no 'chunks' file")
    _rewrite_manifest(vdir, lambda m: m.__setitem__(
        "files", {"manifest": "m.json", "chunks": CHUNKS_NAME, "vectors": VECTORS_NAME,
                  "receipt": RECEIPT_NAME}))
    _assert_nothing_yielded(vdir, "files.manifest must be 'manifest.json'")


# --------------------------------------------------------------------------- #
# writer input validation
# --------------------------------------------------------------------------- #

def test_writer_refuses_bad_inputs(tmp_path: Path) -> None:
    _receipt(tmp_path / "r.json")
    empty = tmp_path / "empty.emb.jsonl"
    empty.write_text(json.dumps({"schema": SCHEMA, "dim": DIM}) + "\n")
    with pytest.raises(ArchiveError, match="no chunk records"):
        write_version(tmp_path, 1, [empty], [tmp_path / "r.json"],
                      collection_id="c", tenant="t", workers=1)
    assert not (tmp_path / "1").exists() and not (tmp_path / ".1.tmp").exists()

    mixed = tmp_path / "mixed.emb.jsonl"
    mixed.write_text("\n".join([
        json.dumps({"schema": SCHEMA, "dim": 4}),
        json.dumps({"id": "a", "doc_id": "d", "content": "x", "embedding": [1, 2, 3, 4]}),
        json.dumps({"id": "b", "doc_id": "d", "content": "y", "embedding": [1, 2, 3]}),
    ]) + "\n")
    with pytest.raises(ArchiveError, match="dim 3 != its embedding-file header dim 4"):
        write_version(tmp_path, 1, [mixed], [tmp_path / "r.json"],
                      collection_id="c", tenant="t", workers=1)

    not_emb = tmp_path / "plain.jsonl"
    not_emb.write_text(json.dumps({"id": "a", "embedding": [1.0]}) + "\n")
    with pytest.raises(ArchiveError, match="not a 'ragstack.embedding_file/v1' file"):
        write_version(tmp_path, 1, [not_emb], [tmp_path / "r.json"],
                      collection_id="c", tenant="t", workers=1)

    _embed_file(tmp_path / "ok.emb.jsonl", 1)
    with pytest.raises(ArchiveError, match="collection_id is required"):
        write_version(tmp_path, 1, [tmp_path / "ok.emb.jsonl"], [tmp_path / "r.json"],
                      collection_id="", tenant="t", workers=1)
    with pytest.raises(ArchiveError, match="non-negative integer"):
        write_version(tmp_path, -1, [tmp_path / "ok.emb.jsonl"], [tmp_path / "r.json"],
                      collection_id="c", tenant="t", workers=1)
    with pytest.raises(ArchiveError, match="no such file"):
        write_version(tmp_path, 1, [tmp_path / "missing.jsonl"], [tmp_path / "r.json"],
                      collection_id="c", tenant="t", workers=1)


# --------------------------------------------------------------------------- #
# tombstone versions
# --------------------------------------------------------------------------- #

def test_tombstone_version_contents(tmp_path: Path) -> None:
    m = write_tombstone(tmp_path, 4, ["doc-9", "doc-2", "doc-9", ""],
                        collection_id="col-a", tenant="public", spec_hash="abcd1234",
                        job_id="job-2")
    vdir = tmp_path / "4"
    assert sorted(p.name for p in vdir.iterdir()) == [MANIFEST_NAME, TOMBSTONE_NAME]
    tomb = json.loads((vdir / TOMBSTONE_NAME).read_text())
    assert tomb == {"format": FORMAT, "count": 2, "doc_ids": ["doc-2", "doc-9"]}
    assert m["has_tombstone"] is True
    assert m["graph"] is False
    assert m["counts"] == {"chunks": 0, "docs": 2}
    assert m["files"] == {"manifest": MANIFEST_NAME, "tombstone": TOMBSTONE_NAME}
    assert set(m["sha256"]) == {TOMBSTONE_NAME}
    assert "vectors" not in m
    assert json.loads((vdir / MANIFEST_NAME).read_text()) == m
    assert read_tombstone(vdir) == ["doc-2", "doc-9"]
    assert list(read_version(vdir)) == []  # verified, nothing to stream

    _flip_byte(vdir / TOMBSTONE_NAME, 5)
    with pytest.raises(ArchiveCorrupt, match="tombstone.json: sha256"):
        read_tombstone(vdir)
    with pytest.raises(ArchiveError, match="at least one doc id"):
        write_tombstone(tmp_path, 5, [], collection_id="c", tenant="t")


def test_read_tombstone_refuses_chunk_version(version_dir) -> None:
    vdir, _, _ = version_dir
    with pytest.raises(ArchiveError, match="not a tombstone version"):
        read_tombstone(vdir)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def test_cli_chunk_version(tmp_path: Path, capsys) -> None:
    _embed_file(tmp_path / "s0.emb.jsonl", 3)
    _receipt(tmp_path / "receipt.json")
    rc = archive_version.main([
        "--version", "12", "--chunks", str(tmp_path / "s0.emb.jsonl"),
        "--receipt", str(tmp_path / "receipt.json"), "--out", str(tmp_path / "o"),
        "--collection-id", "col-a", "--tenant", "acme", "--spec-hash", "ff00",
        "--job-id", "j1", "--workers", "1",
    ])
    assert rc == 0
    vdir = tmp_path / "o" / "12"
    assert sorted(p.name for p in vdir.iterdir()) == sorted(
        [MANIFEST_NAME, CHUNKS_NAME, VECTORS_NAME, RECEIPT_NAME])
    m = verify_version(vdir)
    assert (m["version"], m["tenant"], m["spec_hash"], m["job_id"]) == (12, "acme", "ff00", "j1")
    assert "version=12 chunks: chunks=3 docs=1" in capsys.readouterr().out


def test_cli_tombstone_and_mode_exclusivity(tmp_path: Path) -> None:
    (tmp_path / "ids.json").write_text(json.dumps({"doc_ids": ["d1"]}))
    rc = archive_version.main(["--version", "2", "--tombstone", str(tmp_path / "ids.json"),
                               "--out", str(tmp_path), "--collection-id", "c"])
    assert rc == 0
    assert sorted(p.name for p in (tmp_path / "2").iterdir()) == [MANIFEST_NAME, TOMBSTONE_NAME]

    with pytest.raises(SystemExit, match="exclusive"):
        archive_version.main(["--version", "2", "--tombstone", str(tmp_path / "ids.json"),
                              "--chunks", "x", "--out", str(tmp_path), "--collection-id", "c"])
    with pytest.raises(SystemExit, match="need --chunks and --receipt"):
        archive_version.main(["--version", "2", "--out", str(tmp_path), "--collection-id", "c"])
    with pytest.raises(SystemExit):  # argparse: non-integer version
        archive_version.main(["--version", "v1", "--tombstone", str(tmp_path / "ids.json"),
                              "--collection-id", "c"])
    (tmp_path / "bad.json").write_text(json.dumps({"doc_ids": [1, 2]}))
    with pytest.raises(SystemExit, match="doc-id strings"):
        archive_version.main(["--version", "2", "--tombstone", str(tmp_path / "bad.json"),
                              "--collection-id", "c"])


def test_cli_reports_archive_errors_as_exit_1(tmp_path: Path, capsys) -> None:
    empty = tmp_path / "empty.emb.jsonl"
    empty.write_text(json.dumps({"schema": SCHEMA, "dim": DIM}) + "\n")
    _receipt(tmp_path / "r.json")
    rc = archive_version.main(["--version", "1", "--chunks", str(empty), "--receipt",
                               str(tmp_path / "r.json"), "--out", str(tmp_path),
                               "--collection-id", "c", "--workers", "1"])
    assert rc == 1
    assert "no chunk records" in capsys.readouterr().err


def test_default_workers_is_capped() -> None:
    assert 1 <= archive.default_workers() <= 4
