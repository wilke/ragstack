"""Archive **version** format — ``ragstack-archive/1`` (#357, phase 2 of #353).

A collection's archive is a sequence of *versions*; each is one directory
named by its version number, produced as the **last step of an ingest
workflow** (or by a standalone delete) and uploaded verbatim by the engine to
the owner's Workspace under ``versions/<n>/``. The portable form is canonical:
the text leg (Elasticsearch) is rebuilt from ``chunks``, the vector leg
(Qdrant) from ``vectors``; nothing here is bound to a store version.

Layout of one version directory::

    <n>/
      manifest.json      format tag, identity (collection_id/tenant/spec_hash/
                         version/job_id), counts, sha256 + byte size per file
      chunks.jsonl.gz    one JSON object per chunk — id, doc_id, content,
                         metadata, offsets — WITHOUT the vector (gzip, mtime=0)
      vectors.f32        64-byte header + float32 rows, little-endian,
                         row-aligned with chunks.jsonl.gz
      receipt.json       the load stage's receipt(s), copied verbatim
      tombstone.json     DELETE versions only: the removed doc ids

A tombstone version holds only ``manifest.json`` + ``tombstone.json``.

**The manifest's ``files`` map is what a reader follows.** It maps a *role* to
the filename that plays it — ``{"manifest": "manifest.json", "chunks":
"chunks.jsonl.gz", "vectors": "vectors.f32", "receipt": "receipt.json"}`` (or
``manifest`` + ``tombstone``). Every non-manifest value must have a ``sha256``
entry and every ``sha256`` key must be a value of the map; the reader never
assumes a filename. The role ``triples`` (the graph leg, #350) is reserved and
carries no file today. ``sha256``/``bytes`` are over the bytes **as stored**
(the gzip stream), so verification needs no decompression.

**vectors.f32 header** (64 bytes, all integers little-endian)::

    offset  size  field
    0       8     magic  b"RSF32VEC"
    8       4     header version (1)
    12      4     header length in bytes (64)
    16      4     dim
    20      8     rows
    28      4     dtype code (1 = float32)
    32      1     byte order b"<"
    33      31    reserved — readers must ignore

so ``len(file) == 64 + rows * dim * 4`` and a numpy consumer can
``memmap(path, dtype="<f4", offset=64, shape=(rows, dim))``. The geometry is
duplicated in ``manifest.json["vectors"]`` and the two must agree.

**Compression.** The design names ``chunks.jsonl.zst``; the ``zstandard``
package is not a project dependency and this repo does not add packages to a
shared environment, so the chunks file is **gzip** (``chunks.jsonl.gz``,
``manifest["chunks_compression"] == "gzip"``). Readers resolve the chunks
filename through the ``files`` role map and dispatch on
``chunks_compression`` — ``gzip`` is the only value this reader supports and
anything else is refused loudly (``ArchiveCorrupt: unsupported
chunks_compression``), so a later zstd writer is a manifest change plus a
reader branch, not a format break.

**Streaming both ways.** The writer packs one input line at a time (a bounded
block of lines when ``workers > 1``) straight into the two output streams and
never materialises the vectors; the reader verifies every sha256 and the
vector geometry *before* yielding the first row, then yields one
``(chunk_dict, array('f'))`` pair at a time. The #342 lesson — a JSONL of float
arrays expands ~4.6x when parsed into Python lists — is what this avoids.

**Determinism.** Same input -> byte-identical output (sorted JSON keys, gzip
with ``mtime=0`` and no filename, no timestamps), matching the receipt
contract: an engine retry of the archive step reproduces the same sha256s.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import struct
import sys
from array import array
from collections.abc import Iterable, Iterator
from multiprocessing.pool import Pool
from pathlib import Path
from typing import Any, BinaryIO

from ragstack.ingestion.embedding_file import SCHEMA as EMBEDDING_FILE_SCHEMA

FORMAT = "ragstack-archive/1"

MANIFEST_NAME = "manifest.json"
CHUNKS_NAME = "chunks.jsonl.gz"
VECTORS_NAME = "vectors.f32"
RECEIPT_NAME = "receipt.json"
TOMBSTONE_NAME = "tombstone.json"

# Roles in manifest["files"]. "triples" (the graph leg) is reserved: no writer
# emits it and this reader ignores it if present.
ROLE_MANIFEST = "manifest"
ROLE_CHUNKS = "chunks"
ROLE_VECTORS = "vectors"
ROLE_RECEIPT = "receipt"
ROLE_TOMBSTONE = "tombstone"
ROLE_TRIPLES = "triples"

CHUNKS_COMPRESSION = "gzip"
SUPPORTED_CHUNKS_COMPRESSION = ("gzip",)

VEC_MAGIC = b"RSF32VEC"
VEC_HEADER_VERSION = 1
VEC_HEADER_BYTES = 64
VEC_DTYPE_FLOAT32 = 1
_VEC_STRUCT = struct.Struct("<8sIIIQIc")  # magic, hver, hlen, dim, rows, dtype, order
_FLOAT32_BYTES = 4
_HASH_BLOCK = 1 << 20


class ArchiveError(ValueError):
    """Bad input to the writer (no vectors, mixed dims, missing files, ...)."""


class ArchiveCorrupt(ArchiveError):
    """A version directory that fails verification: a sha256 mismatch, a
    truncated/oversized vectors file, geometry that disagrees between the
    vectors header and the manifest, or a manifest that is not this format."""


# --------------------------------------------------------------------------- #
# vectors.f32 header
# --------------------------------------------------------------------------- #

def pack_vector_header(dim: int, rows: int) -> bytes:
    """The 64-byte ``vectors.f32`` header for a ``rows x dim`` float32 matrix."""
    if dim <= 0 or rows < 0:
        raise ArchiveError(f"invalid vector geometry dim={dim} rows={rows}")
    head = _VEC_STRUCT.pack(VEC_MAGIC, VEC_HEADER_VERSION, VEC_HEADER_BYTES, dim, rows,
                            VEC_DTYPE_FLOAT32, b"<")
    return head.ljust(VEC_HEADER_BYTES, b"\0")


def parse_vector_header(buf: bytes) -> tuple[int, int]:
    """Validate a ``vectors.f32`` header -> ``(dim, rows)``; :class:`ArchiveCorrupt`
    on anything that is not a version-1 little-endian float32 header."""
    if len(buf) < VEC_HEADER_BYTES:
        raise ArchiveCorrupt(f"{VECTORS_NAME}: short header ({len(buf)} < {VEC_HEADER_BYTES} bytes)")
    magic, hver, hlen, dim, rows, dtype, order = _VEC_STRUCT.unpack_from(buf)
    if magic != VEC_MAGIC:
        raise ArchiveCorrupt(f"{VECTORS_NAME}: bad magic {magic!r}")
    if hver != VEC_HEADER_VERSION or hlen != VEC_HEADER_BYTES:
        raise ArchiveCorrupt(f"{VECTORS_NAME}: unsupported header version={hver} length={hlen}")
    if dtype != VEC_DTYPE_FLOAT32 or order != b"<":
        raise ArchiveCorrupt(f"{VECTORS_NAME}: unsupported dtype={dtype} byte_order={order!r}")
    if dim <= 0:
        raise ArchiveCorrupt(f"{VECTORS_NAME}: header dim={dim}")
    return dim, rows


# --------------------------------------------------------------------------- #
# writer
# --------------------------------------------------------------------------- #

class _HashingWriter:
    """Pass-through binary writer that keeps a running sha256 + byte count."""

    def __init__(self, fh: BinaryIO) -> None:
        self._fh = fh
        self.sha = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        self.sha.update(data)
        self.size += len(data)
        return self._fh.write(data)

    def flush(self) -> None:
        self._fh.flush()


def _pack_line(line: str) -> tuple[bytes, bytes, int, str]:
    """One embedding-file record -> ``(chunk_json_line, vector_bytes, dim, doc_id)``.

    Module-level (not a closure) so a :class:`multiprocessing.pool.Pool` can
    pickle it. The vector is packed as native float32 and byte-swapped to
    little-endian on a big-endian host; the record is re-serialised with sorted
    keys and the ``embedding`` key removed.
    """
    obj = json.loads(line)
    if not isinstance(obj, dict):
        raise ArchiveError(f"chunk record is not a JSON object: {line[:80]!r}")
    emb = obj.pop("embedding", None)
    if not emb:
        raise ArchiveError(f"chunk {obj.get('id')!r} has no embedding")
    vec = array("f", emb)
    if sys.byteorder != "little":  # pragma: no cover — no big-endian host to test on
        vec.byteswap()
    text = (json.dumps(obj, sort_keys=True) + "\n").encode("utf-8")
    return text, vec.tobytes(), len(vec), str(obj.get("doc_id", ""))


def _iter_records(paths: Iterable[str | Path]) -> Iterator[tuple[str, int | None]]:
    """Yield ``(line, header_dim)`` for every chunk record across the embedding
    files, in file order. The first line of each file must be the
    ``ragstack.embedding_file/v1`` header; its ``dim`` (when present) travels
    with every record so the packer can cross-check it."""
    for path in paths:
        p = Path(path)
        with p.open(encoding="utf-8") as fh:
            first = fh.readline()
            if not first.strip():
                raise ArchiveError(f"{p}: empty embedding file (no header)")
            try:
                header = json.loads(first)
            except json.JSONDecodeError as e:
                raise ArchiveError(f"{p}:1: bad header json: {e}") from e
            if not isinstance(header, dict) or header.get("schema") != EMBEDDING_FILE_SCHEMA:
                raise ArchiveError(
                    f"{p}:1: not a {EMBEDDING_FILE_SCHEMA!r} file "
                    f"(schema={header.get('schema') if isinstance(header, dict) else None!r})"
                )
            hdim = header.get("dim")
            hdim = int(hdim) if hdim else None
            for line in fh:
                if line.strip():
                    yield line, hdim


def _blocks(records: Iterator[tuple[str, int | None]], size: int) -> Iterator[list[tuple[str, int | None]]]:
    block: list[tuple[str, int | None]] = []
    for rec in records:
        block.append(rec)
        if len(block) >= size:
            yield block
            block = []
    if block:
        yield block


def default_workers() -> int:
    """Packer processes: the input's decimal-float parsing is the bottleneck
    (~0.8 ms per 4096-d line on one core, so ~28 s for 35k chunks), and it
    parallelises trivially. Capped at 4 — a container may see a 384-core host."""
    return max(1, min(4, os.cpu_count() or 1))


def _sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while True:
            buf = fh.read(_HASH_BLOCK)
            if not buf:
                break
            h.update(buf)
            size += len(buf)
    return h.hexdigest(), size


def _version_dir(out_dir: str | Path, version: int) -> Path:
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise ArchiveError(f"version must be a non-negative integer, got {version!r}")
    return Path(out_dir) / str(version)


def _identity(collection_id: str, tenant: str, spec_hash: str, job_id: str,
              version: int) -> dict[str, Any]:
    if not collection_id:
        raise ArchiveError("collection_id is required")
    if not tenant:
        raise ArchiveError("tenant is required")
    return {
        "format": FORMAT,
        "collection_id": collection_id,
        "tenant": tenant,
        "spec_hash": spec_hash or "",
        "version": version,
        "job_id": job_id or "",
    }


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _publish(tmp: Path, final: Path) -> None:
    """Move the fully-written staging dir into place, replacing a prior run's
    output (an engine retry overwrites in place, like every other tool here)."""
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)


def write_version(
    out_dir: str | Path,
    version: int,
    chunks_paths: Iterable[str | Path],
    receipt_paths: Iterable[str | Path],
    *,
    collection_id: str,
    tenant: str,
    spec_hash: str = "",
    job_id: str = "",
    workers: int | None = None,
) -> dict[str, Any]:
    """Pack embedding files (+ the load receipt) into ``<out_dir>/<version>/``.

    Returns the manifest that was written. ``chunks_paths`` are
    ``ragstack.embedding_file/v1`` JSONL files, consumed in order — the row
    order of the archive is the concatenation order. ``receipt_paths``: one
    file is copied verbatim to ``receipt.json``; several (the scatter-per-PDF
    workflow's per-item receipts) are written as a JSON **array** in order.

    ``workers`` > 1 packs blocks of lines in a process pool (ordered, bounded
    to ``workers x 32`` lines in flight); ``None`` picks :func:`default_workers`.
    Raises :class:`ArchiveError` for no records, mixed dims, or a record whose
    dim disagrees with its file header; nothing is left behind on failure.
    """
    chunks_list = [Path(p) for p in chunks_paths]
    receipts_list = [Path(p) for p in receipt_paths]
    if not chunks_list:
        raise ArchiveError("at least one embedding file is required")
    if not receipts_list:
        raise ArchiveError("at least one receipt is required")
    for p in [*chunks_list, *receipts_list]:
        if not p.is_file():
            raise ArchiveError(f"{p}: no such file")
    manifest = _identity(collection_id, tenant, spec_hash, job_id, version)
    final = _version_dir(out_dir, version)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.parent / f".{final.name}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    nworkers = default_workers() if workers is None else max(1, int(workers))
    try:
        rows, dim, docs, chunks_sha, chunks_size = _pack_chunks(
            tmp, chunks_list, nworkers
        )
        vec_sha, vec_size = _sha256_file(tmp / VECTORS_NAME)
        receipt_sha, receipt_size = _write_receipt(tmp / RECEIPT_NAME, receipts_list)
        manifest.update({
            "counts": {"chunks": rows, "docs": docs},
            "files": {ROLE_MANIFEST: MANIFEST_NAME, ROLE_CHUNKS: CHUNKS_NAME,
                      ROLE_VECTORS: VECTORS_NAME, ROLE_RECEIPT: RECEIPT_NAME},
            "sha256": {CHUNKS_NAME: chunks_sha, VECTORS_NAME: vec_sha,
                       RECEIPT_NAME: receipt_sha},
            "bytes": {CHUNKS_NAME: chunks_size, VECTORS_NAME: vec_size,
                      RECEIPT_NAME: receipt_size},
            "chunks_compression": CHUNKS_COMPRESSION,
            "vectors": {"dim": dim, "rows": rows, "dtype": "float32",
                        "byte_order": "little", "header_bytes": VEC_HEADER_BYTES},
            "receipts": len(receipts_list),
            "graph": False,
            "has_tombstone": False,
        })
        _write_json(tmp / MANIFEST_NAME, manifest)
        _publish(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return manifest


def _pack_chunks(vdir: Path, chunks_list: list[Path], nworkers: int) -> tuple[int, int, int, str, int]:
    """Stream every record into ``chunks.jsonl.gz`` + ``vectors.f32`` under
    ``vdir``. Returns ``(rows, dim, distinct_docs, chunks_sha256, chunks_bytes)``.

    The vectors header needs ``rows``, unknown until the end, so a placeholder
    is written first and patched in place; the caller hashes the finished
    vectors file (the chunks stream is hashed inline)."""
    rows = 0
    dim = 0
    doc_ids: set[str] = set()
    records = _iter_records(chunks_list)
    block_size = nworkers * 32
    pool: Pool | None = Pool(nworkers) if nworkers > 1 else None
    try:
        with (vdir / CHUNKS_NAME).open("wb") as raw_chunks, (vdir / VECTORS_NAME).open("wb") as vfh:
            chunks_hw = _HashingWriter(raw_chunks)
            vfh.write(b"\0" * VEC_HEADER_BYTES)  # placeholder, patched below
            with gzip.GzipFile(filename="", mode="wb", fileobj=chunks_hw, mtime=0,
                               compresslevel=6) as gz:
                for block in _blocks(records, block_size):
                    lines = [line for line, _hdim in block]
                    packed = (pool.map(_pack_line, lines, chunksize=8) if pool is not None
                              else [_pack_line(line) for line in lines])
                    for (text, vec, d, doc_id), (_line, hdim) in zip(packed, block, strict=True):
                        if hdim is not None and d != hdim:
                            raise ArchiveError(
                                f"record dim {d} != its embedding-file header dim {hdim}")
                        if dim == 0:
                            dim = d
                        elif d != dim:
                            raise ArchiveError(f"non-uniform embedding dim {d} != {dim}")
                        gz.write(text)
                        vfh.write(vec)
                        rows += 1
                        doc_ids.add(doc_id)
            if rows == 0:
                raise ArchiveError("no chunk records in the embedding file(s); "
                                   "refusing to write an empty version")
            vfh.seek(0)
            vfh.write(pack_vector_header(dim, rows))
    finally:
        if pool is not None:
            pool.terminate()
            pool.join()
    return rows, dim, len(doc_ids), chunks_hw.sha.hexdigest(), chunks_hw.size


def _write_receipt(dest: Path, receipts: list[Path]) -> tuple[str, int]:
    if len(receipts) == 1:
        data = receipts[0].read_bytes()
        json.loads(data)  # must at least be JSON — attribute a bad receipt to its file
    else:
        merged = []
        for p in receipts:
            try:
                merged.append(json.loads(p.read_text(encoding="utf-8")))
            except json.JSONDecodeError as e:
                raise ArchiveError(f"{p}: invalid receipt json: {e}") from e
        data = (json.dumps(merged, indent=2, sort_keys=True) + "\n").encode("utf-8")
    dest.write_bytes(data)
    return hashlib.sha256(data).hexdigest(), len(data)


def write_tombstone(
    out_dir: str | Path,
    version: int,
    doc_ids: Iterable[str],
    *,
    collection_id: str,
    tenant: str,
    spec_hash: str = "",
    job_id: str = "",
) -> dict[str, Any]:
    """Write a DELETE version: ``<out_dir>/<version>/`` holding only
    ``manifest.json`` + ``tombstone.json`` (the removed doc ids, sorted, unique).
    Returns the manifest. An empty id list is refused — a delete of nothing is
    not a version."""
    ids = sorted({str(d) for d in doc_ids if str(d)})
    if not ids:
        raise ArchiveError("tombstone needs at least one doc id")
    manifest = _identity(collection_id, tenant, spec_hash, job_id, version)
    final = _version_dir(out_dir, version)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.parent / f".{final.name}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    try:
        tomb = {"format": FORMAT, "count": len(ids), "doc_ids": ids}
        _write_json(tmp / TOMBSTONE_NAME, tomb)
        sha, size = _sha256_file(tmp / TOMBSTONE_NAME)
        manifest.update({
            "counts": {"chunks": 0, "docs": len(ids)},
            "files": {ROLE_MANIFEST: MANIFEST_NAME, ROLE_TOMBSTONE: TOMBSTONE_NAME},
            "sha256": {TOMBSTONE_NAME: sha},
            "bytes": {TOMBSTONE_NAME: size},
            "graph": False,
            "has_tombstone": True,
        })
        _write_json(tmp / MANIFEST_NAME, manifest)
        _publish(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return manifest


# --------------------------------------------------------------------------- #
# reader
# --------------------------------------------------------------------------- #

def read_manifest(version_dir: str | Path) -> dict[str, Any]:
    """Load + shape-check ``manifest.json`` (format tag, sha256 map, counts)."""
    vdir = Path(version_dir)
    mpath = vdir / MANIFEST_NAME
    if not mpath.is_file():
        raise ArchiveCorrupt(f"{vdir}: no {MANIFEST_NAME}")
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ArchiveCorrupt(f"{mpath}: invalid json: {e}") from e
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ArchiveCorrupt(
            f"{mpath}: format {manifest.get('format') if isinstance(manifest, dict) else None!r} "
            f"is not {FORMAT!r}")
    if not isinstance(manifest.get("sha256"), dict) or not manifest["sha256"]:
        raise ArchiveCorrupt(f"{mpath}: missing sha256 map")
    if not isinstance(manifest.get("counts"), dict):
        raise ArchiveCorrupt(f"{mpath}: missing counts")
    files = manifest.get("files")
    if not isinstance(files, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v for k, v in files.items()):
        raise ArchiveCorrupt(f"{mpath}: 'files' must be a role -> filename map")
    if files.get(ROLE_MANIFEST) != MANIFEST_NAME:
        raise ArchiveCorrupt(f"{mpath}: files.manifest must be {MANIFEST_NAME!r}")
    return manifest


def verify_version(version_dir: str | Path) -> dict[str, Any]:
    """Verify every file named in the manifest (sha256 + byte size) and, for a
    chunk version, the vector geometry: header agrees with the manifest and
    ``len(vectors.f32) == 64 + rows * dim * 4`` with ``rows == counts.chunks``.
    Returns the manifest. Raises :class:`ArchiveCorrupt` on the first mismatch
    — nothing is trusted until everything checks out."""
    vdir = Path(version_dir)
    manifest = read_manifest(vdir)
    files: dict[str, str] = manifest["files"]
    tombstone = bool(manifest.get("has_tombstone"))
    # Resolve roles BEFORE hashing: a manifest this reader cannot follow fails on
    # that, not on a misleading hash message.
    if tombstone:
        if ROLE_TOMBSTONE not in files:
            raise ArchiveCorrupt(f"{vdir}: tombstone version without a {ROLE_TOMBSTONE!r} file")
    else:
        for role in (ROLE_CHUNKS, ROLE_VECTORS):
            if role not in files:
                raise ArchiveCorrupt(f"{vdir}: manifest names no {role!r} file")
        compression = manifest.get("chunks_compression")
        if compression not in SUPPORTED_CHUNKS_COMPRESSION:
            raise ArchiveCorrupt(
                f"{vdir}: unsupported chunks_compression {compression!r} "
                f"(this reader supports {', '.join(SUPPORTED_CHUNKS_COMPRESSION)})")
    listed = {name for role, name in files.items() if role != ROLE_MANIFEST}
    hashed = set(manifest["sha256"])
    if listed != hashed:
        raise ArchiveCorrupt(
            f"{vdir}: files map {sorted(listed)} != sha256 entries {sorted(hashed)} — "
            "every listed file must be hashed and nothing else")
    sizes = manifest.get("bytes") or {}
    for name, want in manifest["sha256"].items():
        path = vdir / name
        if not path.is_file():
            raise ArchiveCorrupt(f"{vdir}: {name} listed in manifest but missing")
        got, size = _sha256_file(path)
        if got != want:
            raise ArchiveCorrupt(f"{path}: sha256 {got} != manifest {want}")
        if name in sizes and int(sizes[name]) != size:
            raise ArchiveCorrupt(f"{path}: {size} bytes != manifest {sizes[name]}")

    if tombstone:
        return manifest

    geom = manifest.get("vectors")
    if not isinstance(geom, dict):
        raise ArchiveCorrupt(f"{vdir}: manifest has no vectors geometry")
    if geom.get("dtype") != "float32" or geom.get("byte_order") != "little" \
            or int(geom.get("header_bytes", 0)) != VEC_HEADER_BYTES:
        raise ArchiveCorrupt(f"{vdir}: unsupported vectors geometry {geom}")
    vpath = vdir / files[ROLE_VECTORS]
    with vpath.open("rb") as fh:
        dim, rows = parse_vector_header(fh.read(VEC_HEADER_BYTES))
    if dim != int(geom.get("dim", 0)) or rows != int(geom.get("rows", -1)):
        raise ArchiveCorrupt(
            f"{vpath}: header dim={dim} rows={rows} != manifest "
            f"dim={geom.get('dim')} rows={geom.get('rows')}")
    if rows != int(manifest["counts"].get("chunks", -1)):
        raise ArchiveCorrupt(
            f"{vpath}: rows={rows} != manifest counts.chunks={manifest['counts'].get('chunks')}")
    expected = VEC_HEADER_BYTES + rows * dim * _FLOAT32_BYTES
    actual = vpath.stat().st_size
    if actual != expected:
        raise ArchiveCorrupt(
            f"{vpath}: {actual} bytes != {VEC_HEADER_BYTES} + {rows} x {dim} x 4 = {expected}")
    return manifest


def read_version(
    version_dir: str | Path, *, manifest: dict[str, Any] | None = None
) -> Iterator[tuple[dict[str, Any], array]]:
    """Verify, then stream ``(chunk_dict, vector)`` pairs — ``chunk_dict`` is the
    record as written (no ``embedding`` key) and ``vector`` an ``array('f')`` of
    ``dim`` floats (a fresh copy per row). Verification is complete before the
    first pair is yielded. A tombstone version yields nothing (see
    :func:`read_tombstone`).

    ``manifest``: pass the dict :func:`verify_version` returned for THIS
    directory to skip re-hashing (a replay verifies every version up front,
    before its first write, and must not pay the sha256 pass twice)."""
    vdir = Path(version_dir)
    if manifest is None:
        manifest = verify_version(vdir)
    if manifest.get("has_tombstone"):
        return
    dim = int(manifest["vectors"]["dim"])
    rows = int(manifest["vectors"]["rows"])
    row_bytes = dim * _FLOAT32_BYTES
    seen = 0
    files = manifest["files"]
    # chunks_compression was verified to be gzip — the only branch there is.
    with gzip.open(vdir / files[ROLE_CHUNKS], "rt", encoding="utf-8") as chunks, \
            (vdir / files[ROLE_VECTORS]).open("rb") as vfh:
        vfh.seek(VEC_HEADER_BYTES)
        for line in chunks:
            if not line.strip():
                continue
            if seen >= rows:
                raise ArchiveCorrupt(f"{vdir}: more chunk records than vector rows ({rows})")
            buf = vfh.read(row_bytes)
            if len(buf) != row_bytes:
                raise ArchiveCorrupt(f"{vdir}: vectors.f32 ended at row {seen} of {rows}")
            vec = array("f")
            vec.frombytes(buf)
            if sys.byteorder != "little":  # pragma: no cover
                vec.byteswap()
            seen += 1
            yield json.loads(line), vec
    if seen != rows:
        raise ArchiveCorrupt(f"{vdir}: {seen} chunk records != {rows} vector rows")


def iter_doc_ids(version_dir: str | Path, manifest: dict[str, Any]) -> Iterator[str]:
    """The ``doc_id`` of every chunk record in a VERIFIED chunk version, in
    row order (duplicates included) — a cheap pass over ``chunks.jsonl.gz``
    that never touches the vectors. A replay uses it to delete each document's
    prior chunks from the stores BEFORE streaming the version in, so a document
    whose chunks span two upsert batches is not deleted by its own second
    batch. Yields nothing for a tombstone version."""
    if manifest.get("has_tombstone"):
        return
    vdir = Path(version_dir)
    with gzip.open(vdir / manifest["files"][ROLE_CHUNKS], "rt", encoding="utf-8") as chunks:
        for line in chunks:
            if line.strip():
                yield str(json.loads(line).get("doc_id", ""))


def read_tombstone(
    version_dir: str | Path, *, manifest: dict[str, Any] | None = None
) -> list[str]:
    """Verify a tombstone version and return its removed doc ids (``manifest``
    as for :func:`read_version`)."""
    vdir = Path(version_dir)
    if manifest is None:
        manifest = verify_version(vdir)
    if not manifest.get("has_tombstone"):
        raise ArchiveError(f"{vdir}: not a tombstone version")
    tpath = vdir / manifest["files"][ROLE_TOMBSTONE]
    tomb = json.loads(tpath.read_text(encoding="utf-8"))
    ids = tomb.get("doc_ids") if isinstance(tomb, dict) else None
    if not isinstance(ids, list):
        raise ArchiveCorrupt(f"{tpath}: no doc_ids list")
    return [str(d) for d in ids]
