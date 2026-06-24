"""Ingestion manifests: enumerate sources into stable units of work.

A manifest is the immutable list of items an ingest run will process. Each
``WorkItem`` carries a deterministic ``item_id`` equal to the document id the
loader will assign (see ``loaders.deterministic_doc_id``), so a run can be
checkpointed, resumed, and addressed for a later KG-only pass — all keyed by the
same id the vector store stores under.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ragstack.ingestion.loaders import deterministic_doc_id


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


def build_manifest(source: str, suffixes: list[str] | None = None) -> Manifest:
    """Expand a file or directory into a manifest.

    A directory is walked recursively (sorted for determinism); ``suffixes``
    (e.g. ``[".pdf", ".txt"]``) filters by extension when given. ``item_id`` is
    derived the same way the loaders derive the document id (resolved path), so
    manifest ids and stored document ids coincide.
    """
    path = Path(source)
    if path.is_dir():
        files = sorted(f for f in path.rglob("*") if f.is_file())
        if suffixes is not None:
            allowed = {s.lower() for s in suffixes}
            files = [f for f in files if f.suffix.lower() in allowed]
    else:
        files = [path]
    items = [
        WorkItem(item_id=deterministic_doc_id(str(f.resolve())), source=str(f))
        for f in files
    ]
    return Manifest(items=items)
