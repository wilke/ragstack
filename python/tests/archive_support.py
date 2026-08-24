"""Test-only builders for ``ragstack-archive/1`` version directories (#357/#358).

Shared by the replay-loader tests, the dormant-collection API tests and the
perf budget: write a synthetic ``ragstack.embedding_file/v1`` JSONL, pack it
into a version directory with the real writer, and (optionally) tamper with
the result. No store, no network.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from ragstack.ingestion.archive import MANIFEST_NAME, write_tombstone, write_version
from ragstack.ingestion.embedding_file import SCHEMA


def embed_file(
    path: Path, n: int, *, dim: int = 16, seed: int = 0, start: int = 0,
    chunks_per_doc: int = 3, tenant: str = "bvbrc:alice@patricbrc.org",
) -> list[dict[str, Any]]:
    """Write ``n`` synthetic embedded chunks (ids ``chunk-<i>``, docs
    ``doc-<i // chunks_per_doc>``) and return the records in file order."""
    rng = random.Random(seed)
    recs = []
    for i in range(start, start + n):
        recs.append({
            "id": f"chunk-{i}", "doc_id": f"doc-{i // chunks_per_doc}",
            "content": f"passage {i} about hybrid retrieval",
            "embedding": [rng.uniform(-1.0, 1.0) for _ in range(dim)],
            "metadata": {"title": f"T{i // chunks_per_doc}", "n": i, "tenant_id": tenant},
            "start_char": 0, "end_char": 30,
        })
    header = {"schema": SCHEMA, "tenant": tenant, "dim": dim}
    lines = [json.dumps(header, sort_keys=True)] + [json.dumps(r, sort_keys=True) for r in recs]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return recs


def chunk_version(
    root: Path, version: int, n: int, *, start: int = 0, dim: int = 16, seed: int | None = None,
    collection_id: str = "lib", spec_hash: str = "cafe0001", tenant: str = "bvbrc:alice@patricbrc.org",
    chunks_per_doc: int = 3,
) -> tuple[Path, list[dict[str, Any]]]:
    """``<root>/<version>/`` holding ``n`` chunks starting at chunk index
    ``start``. Returns ``(version_dir, records)``."""
    emb = root / f"v{version}.emb.jsonl"
    recs = embed_file(emb, n, dim=dim, seed=version if seed is None else seed, start=start,
                      chunks_per_doc=chunks_per_doc, tenant=tenant)
    receipt = root / f"v{version}.receipt.json"
    receipt.write_text(json.dumps({"status": "completed", "n_chunks": n}), encoding="utf-8")
    write_version(root, version, [emb], [receipt], collection_id=collection_id,
                  tenant=tenant, spec_hash=spec_hash, job_id=f"job-{version}", workers=1)
    emb.unlink()
    receipt.unlink()
    return root / str(version), recs


def tombstone_version(
    root: Path, version: int, doc_ids: list[str], *, collection_id: str = "lib",
    spec_hash: str = "cafe0001", tenant: str = "bvbrc:alice@patricbrc.org",
) -> Path:
    write_tombstone(root, version, doc_ids, collection_id=collection_id, tenant=tenant,
                    spec_hash=spec_hash, job_id=f"job-{version}")
    return root / str(version)


def tamper_manifest(version_dir: Path, **changes: Any) -> None:
    """Rewrite ``manifest.json`` with ``changes`` applied (the manifest itself
    is not hashed, so this is exactly what a user editing their archive by hand
    looks like — a changed ``spec_hash`` is the ADR-0002 guard's case)."""
    mpath = version_dir / MANIFEST_NAME
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    manifest.update(changes)
    mpath.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def corrupt_file(version_dir: Path, name: str, *, offset: int = 200) -> None:
    """Flip one byte of a data file so its sha256 no longer matches."""
    path = version_dir / name
    data = bytearray(path.read_bytes())
    idx = min(offset, len(data) - 1)
    data[idx] ^= 0xFF
    path.write_bytes(bytes(data))
