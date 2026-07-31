"""Ingestion manifests: enumerate sources into stable units of work.

A manifest is the immutable list of items an ingest run will process. Each
``WorkItem`` carries a deterministic ``item_id`` equal to the document id the
loader will assign (see ``loaders.deterministic_doc_id``), so a run can be
checkpointed, resumed, and addressed for a later KG-only pass — all keyed by the
same id the vector store stores under.
"""
from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from ragstack.ingestion.loaders import (
    LoaderError,
    confine_to_root,
    deterministic_doc_id,
)

log = logging.getLogger(__name__)


class WorkItem(BaseModel):
    """One source to ingest. ``item_id`` matches the loader's document id."""

    item_id: str
    source: str


class Manifest(BaseModel):
    """An ordered, immutable set of work items for one ingest run."""

    items: list[WorkItem] = Field(default_factory=list)

    def __len__(self) -> int:
        return len(self.items)


class ItemResult(BaseModel):
    """Outcome of ingesting one work item — enough to checkpoint and to drive a
    later KG-only re-run (addressed by ``item_id`` == document id)."""

    item_id: str
    source: str
    status: str  # jobstore.COMPLETED | jobstore.FAILED
    chunk_ids: list[str] = Field(default_factory=list)
    error: str = ""


def build_manifest(
    source: str,
    suffixes: list[str] | tuple[str, ...] | None = None,
    ingest_root: str | None = None,
) -> Manifest:
    """Expand a file or directory into a manifest.

    A directory is walked recursively (sorted for determinism); ``suffixes``
    (e.g. ``[".pdf", ".txt"]``) filters by extension when given. ``item_id`` is
    derived the same way the loaders derive the document id (resolved path), so
    manifest ids and stored document ids coincide. When ``ingest_root`` is set,
    every enumerated file is confined to it (the LFI guard) — not just the
    top-level source — because ``rglob`` follows symlinks, so a link inside the
    root pointing outside it must not be enumerated. Such files are skipped.
    """
    # Normalize "" to None up front: the two are the same intent ("no root"), but
    # an empty string is truthy-different — it skipped confinement here while
    # still being passed to confine_to_root below, where Path("").resolve() is the
    # CWD, so every file resolved outside it and the manifest came back empty.
    ingest_root = ingest_root or None
    path = confine_to_root(source, ingest_root) if ingest_root else Path(source)
    if path.is_dir():
        files = sorted(f for f in path.rglob("*") if f.is_file())
        if suffixes is not None:
            allowed = {s.lower() for s in suffixes}
            files = [f for f in files if f.suffix.lower() in allowed]
    else:
        files = [path]

    items: list[WorkItem] = []
    for f in files:
        # Re-confine each file and derive item_id from the *confined* resolved
        # path, so manifest ids match what the loader will store and an escaping
        # symlink is dropped here rather than enumerated and failed later.
        # confine_to_root(x, None) just resolves (never raises), so one path
        # covers both the rooted and unrooted cases.
        try:
            resolved = confine_to_root(str(f), ingest_root)
        except LoaderError:
            log.warning("skipping %s: resolves outside the ingest root", f.name)
            continue
        items.append(WorkItem(item_id=deterministic_doc_id(str(resolved)), source=str(f)))
    return Manifest(items=items)
