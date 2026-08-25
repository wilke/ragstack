"""Extract a knowledge graph from ONE archived chunk version (#350, phase 6 of
#201) — the ``extract-graph`` step's logic, CLI in ``scripts/extract_graph.py``.

Reads the version's ``chunks.jsonl.gz`` through :func:`archive.read_chunks`
(text only — the vectors are never touched), runs the LLM extractor over every
chunk with a bounded concurrency (one LLM call per chunk is ~10x the embed
cost, which is why this is a separate, opt-in workflow and never part of the
ingest critical path), and writes the graph leg — ``triples.jsonl.gz`` + the
updated manifest — with :func:`archive.write_triples`.

What every archived triple carries (the #347 stamping, done by the extractor
and verified by ``tests/ingestion/test_extract_graph.py``): ``chunk_id`` of the
chunk it came from, ``evidence`` = the verbatim span the model quoted (kept
only when it occurs in the chunk), ``derived_by="llm"``, ``confidence=1`` (the
no-launder cap). This module adds ``tenant_id`` from the chunk's own metadata
(falling back to the manifest's tenant) and leaves ``collection`` EMPTY: the
physical store name is registry knowledge the loader stamps at load time, so
the archive stays portable across a rename of the physical store.

Determinism: results are collected per chunk and written in chunk order,
deduplicated on ``(subject, predicate, object, doc_id)`` exactly as
:meth:`LLMKGExtractor.extract` does, so the same model output yields a
byte-identical leg regardless of which LLM call finished first.

Refusals: a manifest that fails its shape/chunk-hash check, a tombstone
version, or an identity (``collection_id`` / ``spec_hash``) that disagrees
with what the caller expects is :class:`ExtractRefused` (the CLI exits 3,
permanent — the archive is the problem); more triples than ``max_triples``
is :class:`GraphCapExceeded` (exit 4, the graph budget) and the leg is NOT
written.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ragstack.graph.budget import GraphCapExceeded
from ragstack.ingestion import archive
from ragstack.ingestion.archive import ArchiveCorrupt, ArchiveError
from ragstack.models import Chunk, Triple

#: Default LLM calls in flight at once. A vLLM server batches concurrent
#: requests, so this is the throughput lever; it is bounded so one extraction
#: job cannot monopolise the shared endpoint.
DEFAULT_CONCURRENCY = 8


class ExtractRefused(RuntimeError):
    """The version was refused BEFORE anything was written. ``kind`` is the
    stable marker — ``ArchiveCorrupt`` (manifest / chunk-hash failure, a
    tombstone) or ``SpecMismatch`` (identity disagrees with the caller's) —
    mirroring the replay loader's :class:`ReplayRefused`."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind


@dataclass
class ExtractionSummary:
    """What one extraction did."""

    version: int = 0
    n_chunks: int = 0
    n_chunks_empty: int = 0  #: chunks with no text (skipped without an LLM call)
    n_chunks_without_triples: int = 0  #: LLM answered with no fact (or failed)
    n_triples: int = 0
    n_duplicates: int = 0
    seconds: float = 0.0
    out_dir: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version, "n_chunks": self.n_chunks,
            "n_chunks_empty": self.n_chunks_empty,
            "n_chunks_without_triples": self.n_chunks_without_triples,
            "n_triples": self.n_triples, "n_duplicates": self.n_duplicates,
            "seconds": round(self.seconds, 3), "out_dir": self.out_dir,
            "chunks_per_second": round(self.n_chunks / self.seconds, 2) if self.seconds else None,
        }


def _chunk_from_record(rec: dict[str, Any]) -> Chunk:
    return Chunk(
        id=str(rec.get("id", "")), doc_id=str(rec.get("doc_id", "")),
        content=str(rec.get("content", "")), metadata=dict(rec.get("metadata") or {}),
        start_char=int(rec.get("start_char", 0) or 0), end_char=int(rec.get("end_char", 0) or 0),
    )


def load_chunks(
    version_dir: str | Path, *, collection_id: str = "", spec_hash: str = ""
) -> tuple[dict[str, Any], list[Chunk]]:
    """Verify the version (manifest shape + the chunks file's sha256, the
    identity when given) and return ``(manifest, chunks)``. Raises
    :class:`ExtractRefused` on any refusal; nothing is written by this."""
    vdir = Path(version_dir)
    try:
        manifest = archive.read_manifest(vdir)
        if manifest.get("has_tombstone"):
            raise ExtractRefused("ArchiveCorrupt", f"{vdir}: a tombstone version has no chunks")
        chunks = [_chunk_from_record(rec) for rec in archive.read_chunks(vdir)]
    except ArchiveCorrupt as e:
        raise ExtractRefused("ArchiveCorrupt", str(e)) from e
    except ArchiveError as e:
        raise ExtractRefused("ArchiveCorrupt", f"{vdir}: {e}") from e
    if collection_id and str(manifest.get("collection_id") or "") != collection_id:
        raise ExtractRefused(
            "SpecMismatch",
            f"{vdir}: manifest collection_id {manifest.get('collection_id')!r} != {collection_id!r}",
        )
    if spec_hash and str(manifest.get("spec_hash") or "") != spec_hash:
        raise ExtractRefused(
            "SpecMismatch",
            f"{vdir}: manifest spec_hash {manifest.get('spec_hash')!r} != registry {spec_hash!r}",
        )
    return manifest, chunks


async def extract_triples(
    chunks: list[Chunk],
    extractor: Any,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    tenant: str = "",
    summary: ExtractionSummary | None = None,
) -> list[Triple]:
    """Run ``extractor.extract_chunk`` over ``chunks`` with at most
    ``concurrency`` calls in flight; return the deduplicated triples in chunk
    order, each stamped with its chunk's ``tenant_id`` (else ``tenant``).
    ``extractor`` is any object with ``async extract_chunk(chunk) ->
    list[Triple]`` (:class:`ragstack.graph.extractor.LLMKGExtractor`)."""
    sem = asyncio.Semaphore(max(1, int(concurrency)))
    summary = summary if summary is not None else ExtractionSummary()

    async def _one(chunk: Chunk) -> list[Triple]:
        if not chunk.content.strip():
            return []
        async with sem:
            return list(await extractor.extract_chunk(chunk))

    per_chunk = await asyncio.gather(*(_one(c) for c in chunks))
    out: list[Triple] = []
    seen: set[tuple[str, str, str, str]] = set()
    for chunk, found in zip(chunks, per_chunk, strict=True):
        if not chunk.content.strip():
            summary.n_chunks_empty += 1
            continue
        if not found:
            summary.n_chunks_without_triples += 1
            continue
        tenant_id = str(chunk.metadata.get("tenant_id") or tenant or "")
        for t in found:
            key = (t.subject, t.predicate, t.object, t.doc_id)
            if key in seen:
                summary.n_duplicates += 1
                continue
            seen.add(key)
            t.tenant_id = tenant_id
            t.collection = ""  # stamped by the loader from the registry entry
            out.append(t)
    summary.n_chunks = len(chunks)
    summary.n_triples = len(out)
    return out


async def extract_version(
    version_dir: str | Path,
    extractor: Any,
    *,
    out_dir: str | Path | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    collection_id: str = "",
    spec_hash: str = "",
    max_triples: int = 0,
    extractor_name: str = "",
    log: Callable[[str], None] | None = None,
) -> ExtractionSummary:
    """The whole step: verify, extract, budget, write the leg.

    ``out_dir`` — where the delta (``manifest.json`` + ``triples.jsonl.gz``)
    goes; ``None`` writes in place. ``max_triples`` > 0 refuses (before writing)
    a version whose own triples exceed it. ``extractor_name`` is recorded in
    the manifest's ``graph_extraction`` provenance (the model name).
    """
    say = log if log is not None else (lambda *_a: None)
    t0 = time.perf_counter()
    manifest, chunks = load_chunks(version_dir, collection_id=collection_id, spec_hash=spec_hash)
    version = int(manifest.get("version", 0) or 0)
    summary = ExtractionSummary(version=version)
    say(f"[{version_dir}] version {version}: {len(chunks)} chunk(s); extracting with "
        f"concurrency={concurrency}")
    triples = await extract_triples(
        chunks, extractor, concurrency=concurrency, tenant=str(manifest.get("tenant") or ""),
        summary=summary,
    )
    if max_triples and max_triples > 0 and len(triples) > max_triples:
        raise GraphCapExceeded(None, len(triples), max_triples)
    extraction = {
        "derived_by": "llm", "extractor": extractor_name or type(extractor).__name__,
        "n_chunks": summary.n_chunks, "n_chunks_empty": summary.n_chunks_empty,
        "n_chunks_without_triples": summary.n_chunks_without_triples,
        "concurrency": int(concurrency),
    }
    summary.manifest = archive.write_triples(
        version_dir, triples, out_dir=out_dir, extraction=extraction,
    )
    summary.out_dir = str(Path(out_dir) if out_dir is not None else Path(version_dir))
    summary.seconds = time.perf_counter() - t0
    say(f"[{version_dir}] wrote {summary.n_triples} triple(s) from {summary.n_chunks} chunk(s) "
        f"in {summary.seconds:.1f}s → {summary.out_dir}")
    return summary
