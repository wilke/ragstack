"""Embedding-file contract (ADR-0001 offline plane, #141).

The offline plane splits ingestion into an **embed** stage (GPU-bound: load →
chunk → embed) and a separate **load** stage (store-bound: delete-prior → upsert
with backpressure). The two stages communicate through *files*, not a shared
process, so the GPU fleet is never blocked by Qdrant. This module is that file's
**versioned contract**: what :meth:`IngestionPipeline.embed_source` produces and
what the load stage consumes.

Format — newline-delimited JSON (JSONL), streamable and human-inspectable:

    {"schema":"ragstack.embedding_file/v1","tenant":"public","dim":4096,"count":N}
    {"id":...,"doc_id":...,"content":...,"embedding":[...],"metadata":{...}, ...}
    ...

Line 1 is a **header** (schema tag + embedding dim + count + tenant); every
subsequent line is one embedded :class:`~ragstack.models.Chunk` (``model_dump``).
The header ``dim`` is the guard that catches the classic footgun — loading a file
of 768-d BGE vectors into a 4096-d SFR collection — before a single point is
written. Chunks are written with ``sort_keys`` so a re-embed of unchanged input
yields a byte-identical file (idempotent + diff-able), matching the receipt
contract's determinism.

Kept dependency-light (json + the Chunk model) so a future non-Python loader can
depend on the *schema*, not on this code — the seam that keeps a possible Go
load stage decoupled.
"""
from __future__ import annotations

import json
from pathlib import Path

from ragstack.models import Chunk

SCHEMA = "ragstack.embedding_file/v1"


class EmbeddingFileError(ValueError):
    """A malformed or inconsistent embedding file — attributed to path (+ line)."""


def write_embedding_file(
    path: str | Path, chunks: list[Chunk], *, tenant: str = ""
) -> None:
    """Serialize ``chunks`` (all must carry an embedding) to ``path`` as JSONL.

    Raises :class:`EmbeddingFileError` if a chunk has no embedding or the
    embedding dimensions are not uniform — a file that would silently poison the
    load stage is never written.
    """
    dims = {len(c.embedding) for c in chunks if c.embedding is not None}
    missing = sum(1 for c in chunks if c.embedding is None)
    if missing:
        raise EmbeddingFileError(
            f"{path}: {missing} chunk(s) have no embedding; embed_source only "
            "returns embedded chunks — refusing to write an incomplete file"
        )
    if len(dims) > 1:
        raise EmbeddingFileError(f"{path}: non-uniform embedding dims {sorted(dims)}")
    dim = next(iter(dims)) if dims else 0
    header = {"schema": SCHEMA, "tenant": tenant, "dim": dim, "count": len(chunks)}
    lines = [json.dumps(header, sort_keys=True)]
    lines += [json.dumps(c.model_dump(), sort_keys=True) for c in chunks]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_header(path: str | Path) -> dict:
    """Read + validate just the header line (cheap dim/tenant/count probe)."""
    p = Path(path)
    with p.open(encoding="utf-8") as fh:
        first = fh.readline()
    if not first.strip():
        raise EmbeddingFileError(f"{p}: empty file (no header)")
    try:
        header = json.loads(first)
    except json.JSONDecodeError as e:
        raise EmbeddingFileError(f"{p}:1: bad header json: {e}") from e
    if header.get("schema") != SCHEMA:
        raise EmbeddingFileError(
            f"{p}:1: unknown schema {header.get('schema')!r} (expected {SCHEMA!r})"
        )
    return header


def read_embedding_file(path: str | Path) -> tuple[list[Chunk], dict]:
    """Load ``path`` → (chunks, header). Validates the schema tag and that every
    chunk carries an embedding of the header's ``dim`` — errors are attributed to
    ``path:line`` so one corrupt file names itself instead of a raw traceback."""
    p = Path(path)
    header = read_header(p)
    dim = int(header.get("dim", 0))
    chunks: list[Chunk] = []
    with p.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if lineno == 1 or not line.strip():
                continue  # header (already parsed) / trailing blank
            try:
                chunk = Chunk.model_validate_json(line)
            except ValueError as e:
                raise EmbeddingFileError(f"{p}:{lineno}: bad chunk: {e}") from e
            if chunk.embedding is None:
                raise EmbeddingFileError(f"{p}:{lineno}: chunk has no embedding")
            if dim and len(chunk.embedding) != dim:
                raise EmbeddingFileError(
                    f"{p}:{lineno}: embedding dim {len(chunk.embedding)} != header {dim}"
                )
            chunks.append(chunk)
    if len(chunks) != int(header.get("count", len(chunks))):
        raise EmbeddingFileError(
            f"{p}: count mismatch — header says {header.get('count')}, "
            f"file has {len(chunks)} chunk(s)"
        )
    return chunks, header
