"""Load an archived graph leg into the graph store (#350) — the ``load-graph``
step's logic (CLI in ``scripts/load_graph.py``) and the restore replay's graph
half (``ingestion.load_embeddings.run_replay``).

Every triple is stamped ``collection = <the registry entry's physical name>``
on the way in, so the loaded graph is scoped by ``(tenant_id, collection)`` —
``tenant_id`` was archived per triple (the chunk's tenant), ``collection`` is
deliberately not (see ``graph.extract_version``). Loading is idempotent: both
graph stores MERGE on the triple's identity key, so a re-run (engine retry)
converges without duplicates.

The per-collection budget (:mod:`ragstack.graph.budget`) is checked ONCE,
before the first write, with one live count — a load that would cross the cap
raises :class:`GraphCapExceeded` and nothing is written.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ragstack.graph.budget import check_graph_cap
from ragstack.ingestion import archive
from ragstack.models import Triple

#: Triples per ``add_triples`` call — bounded regardless of the leg's size.
LOAD_BATCH = 1000


@dataclass
class GraphLoadSummary:
    version: int = 0
    n_triples: int = 0
    live_before: int | None = None
    cap: int | None = None
    seconds: float = 0.0
    collection: str = ""
    versions: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "graph", "version": self.version, "collection": self.collection,
            "n_triples": self.n_triples, "live_before": self.live_before, "cap": self.cap,
            "seconds": round(self.seconds, 3),
        }


async def load_triples(
    version_dir: str | Path,
    graph_store: Any,
    *,
    collection: str,
    cap: int | None = None,
    batch_size: int = LOAD_BATCH,
    manifest: dict[str, Any] | None = None,
    log: Callable[[str], None] | None = None,
) -> GraphLoadSummary:
    """Load ``version_dir``'s graph leg into ``graph_store`` scoped to
    ``collection``.

    ``manifest``: the dict :func:`archive.verify_version` /
    :func:`archive.verify_triples` already returned for this directory (skips
    re-hashing); omitted, the leg alone is verified first — a delta directory
    (manifest + triples, no chunks) is enough. A verified manifest without a
    ``triples`` role loads nothing. ``cap`` (``None`` = unlimited) is checked
    against the manifest's ``counts.triples`` BEFORE the first write.

    **Duplication across versions on restore.** A replay loads every
    version's leg in order and never deletes a chunk version's prior triples
    (``_delete_docs(graph=False)``, #380's trade-off), so a document ingested
    in two versions contributes both legs: the SAME fact — identical
    ``(subject, predicate, object, doc_id, tenant_id, collection)`` — MERGEs
    into one edge (evidence fields last-writer-wins), while a REPHRASED fact
    becomes a second edge with the same doc and collection. Reads are
    confidence-floored and collection-scoped, so the duplicate is noise, not
    a leak; a tombstone version removes both.
    """
    if not collection:
        raise ValueError("load_triples needs the collection's physical name to stamp")
    say = log if log is not None else (lambda *_a: None)
    t0 = time.perf_counter()
    vdir = Path(version_dir)
    if manifest is None:
        manifest = archive.verify_triples(vdir)
    summary = GraphLoadSummary(version=int(manifest.get("version", 0) or 0),
                               collection=collection, cap=cap)
    if archive.ROLE_TRIPLES not in manifest["files"]:
        summary.seconds = time.perf_counter() - t0
        return summary
    incoming = int((manifest.get("counts") or {}).get("triples", 0) or 0)
    summary.live_before = await check_graph_cap(graph_store, incoming, cap, collection=collection)
    batch: list[Triple] = []
    for triple in archive.read_triples(vdir, manifest=manifest):
        triple.collection = collection
        batch.append(triple)
        if len(batch) >= max(1, batch_size):
            await graph_store.add_triples(batch)
            summary.n_triples += len(batch)
            batch = []
    if batch:
        await graph_store.add_triples(batch)
        summary.n_triples += len(batch)
    summary.seconds = time.perf_counter() - t0
    say(f"[{vdir}] graph: loaded {summary.n_triples} triple(s) into {collection!r} "
        f"in {summary.seconds:.1f}s")
    return summary
