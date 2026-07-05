"""Collection provenance manifests.

One JSON file per collection under ``collection_manifest_dir`` recording how the
corpus was built — the *verified* lineage, as opposed to the registry's
operator-asserted ``chunk_method`` labels. Written at ingest (``source="ingest"``)
and, for collections that predate manifests, materialized from the registry spec
at startup (``source="config"``). Read by ``GET /v1/collections``.

Disabled when ``collection_manifest_dir`` is empty: every function no-ops /
returns ``None``, so behaviour is unchanged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import UTC
from typing import Any

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


def chunk_descriptor(
    method: str, size: int | None, overlap: int | None, params: dict[str, Any] | None = None
) -> str:
    """A canonical, stable string identifying a chunking configuration — the
    content-address input for :func:`ragstack.stores.qdrant.collection_name` and
    the manifest's ``spec_hash``. Deterministic: sorted params, no whitespace."""
    parts = [method or "", str(size if size is not None else ""), str(overlap if overlap is not None else "")]
    if params:
        parts.append(json.dumps(params, sort_keys=True, separators=(",", ":")))
    return "/".join(parts)


def spec_hash(model: str, dim: int, chunk: str) -> str:
    """Short content-address of the full build spec (matches the hash embedded in
    a content-addressed collection name)."""
    return hashlib.sha1(f"{model}|{dim}|{chunk}".encode()).hexdigest()[:8]


class CollectionManifest(BaseModel):
    """The build spec + ingest metadata for one collection."""

    collection: str
    model: str
    dim: int
    embedding_api: str = ""
    embedding_endpoints: list[str] = Field(default_factory=list)
    chunk_method: str = ""
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    chunk_params: dict[str, Any] = Field(default_factory=dict)
    spec_hash: str = ""
    corpus: str = ""  # a source hint (e.g. last ingest source path)
    chunk_count: int | None = None
    ingested_at: str = ""  # ISO 8601; caller stamps (Date.now is unavailable here)
    ragstack_version: str = ""
    source: str = "ingest"  # "ingest" (verified) | "config" (materialized from registry)


def make_ingest_manifest(
    *,
    collection: str,
    model: str,
    dim: int,
    embedding_api: str = "",
    embedding_endpoints: list[str] | None = None,
    chunk_method: str = "",
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    chunk_params: dict[str, Any] | None = None,
    corpus: str = "",
    chunk_count: int | None = None,
    ragstack_version: str = "",
    source: str = "ingest",
) -> CollectionManifest:
    """Build a verified manifest for a just-ingested collection, stamped with the
    current time and the build spec's content hash. The single constructor shared
    by the API ingest hook and the CLI ingest scripts, so both record provenance
    identically."""
    from datetime import datetime

    desc = chunk_descriptor(chunk_method, chunk_size, chunk_overlap, chunk_params)
    return CollectionManifest(
        collection=collection,
        model=model or "",
        dim=dim,
        embedding_api=embedding_api,
        embedding_endpoints=list(embedding_endpoints or []),
        chunk_method=chunk_method or "",
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunk_params=dict(chunk_params or {}),
        spec_hash=spec_hash(model or "", dim, desc),
        corpus=corpus,
        chunk_count=chunk_count,
        ingested_at=datetime.now(UTC).isoformat(),
        ragstack_version=ragstack_version,
        source=source,
    )


def _safe_name(collection: str) -> str:
    """A filesystem-safe basename for a collection (defensive — collection names
    are already slug-like, but never let one escape the manifest dir)."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", collection)


def _path(manifest_dir: str, collection: str) -> str:
    return os.path.join(manifest_dir, f"{_safe_name(collection)}.json")


def read_manifest(manifest_dir: str, collection: str) -> CollectionManifest | None:
    """Load a collection's manifest, or ``None`` (disabled dir / missing / corrupt)."""
    if not manifest_dir:
        return None
    path = _path(manifest_dir, collection)
    try:
        with open(path, encoding="utf-8") as f:
            return CollectionManifest.model_validate_json(f.read())
    except FileNotFoundError:
        return None
    except Exception as e:  # corrupt/partial file — don't take down the reader
        log.warning("provenance: manifest %s unreadable: %s", path, e)
        return None


def write_manifest(manifest_dir: str, manifest: CollectionManifest) -> None:
    """Persist a manifest (atomic replace). No-op when the dir is unset."""
    if not manifest_dir:
        return
    os.makedirs(manifest_dir, exist_ok=True)
    path = _path(manifest_dir, manifest.collection)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(manifest.model_dump_json(indent=2))
    os.replace(tmp, path)  # atomic — a reader never sees a half-written file
