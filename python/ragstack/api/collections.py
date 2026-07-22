"""Multi-collection registry.

Lets one API serve several corpora — different embedding models and/or chunk
strategies — selectable per request via the ``collection`` field on ``/query``
and ``/retrieve``. Each :class:`CollectionEntry` binds a Qdrant collection + ES
index + an embedder (matched to that collection's model/dim) into its own
``HybridRetriever``; the graph store, reranker, generator, and rewriters are
shared. An empty registry means single-collection mode: the pinned/derived
collection is the sole ``default`` entry and behaviour is unchanged.

The registry is *built* in ``api/deps.py`` (which owns the embedder/store/
retriever construction helpers); this module holds the config shape and the
lookup container so both the builder and the routers can import them without a
cycle.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class CollectionSpec(BaseModel):
    """One registry entry as authored in JSON (``collections_file`` / ``_json``)."""

    id: str
    label: str = ""
    collection: str  # Qdrant collection name (BM25 index defaults to the same)
    text_index: str = ""  # ES index; "" → same as `collection`
    embedding_api: str = "openai"  # sidecar | openai
    embedding_model: str = ""
    embedding_model_dim: int
    embedding_endpoints: list[str] = Field(default_factory=list)
    embedding_sidecar_url: str = ""  # single-endpoint fallback when no `endpoints`
    chunk_method: str = ""  # how the corpus was chunked (see chunk_overlap/params below)
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    chunk_params: dict[str, Any] = Field(default_factory=dict)

    def es_index(self) -> str:
        return self.text_index or self.collection

    def emb_signature(self) -> tuple[str, str, tuple[str, ...], str]:
        """Identity of the embedding backend — entries that share it reuse one
        embedder instance (one pool), so N same-model collections don't spin up N
        redundant connection pools."""
        eps = tuple(sorted(self.embedding_endpoints)) or (self.embedding_sidecar_url,)
        return (self.embedding_api, self.embedding_model, eps, str(self.embedding_model_dim))


@dataclass
class CollectionEntry:
    """A built, ready-to-serve collection: metadata + its bound retriever/stores."""

    id: str
    label: str
    collection: str
    model: str
    dim: int
    chunk_method: str
    chunk_size: int | None
    chunk_overlap: int | None
    chunk_params: dict[str, Any]
    is_default: bool
    retriever: Any
    vector_store: Any
    text_index: Any
    embedder: Any = None  # the collection's embedder (matched to its model/dim); for ingest


class CollectionRegistry:
    """Lookup over built collections with a designated default."""

    def __init__(self, entries: list[CollectionEntry], default_id: str) -> None:
        if not entries:
            raise ValueError("CollectionRegistry requires at least one entry")
        self._entries: dict[str, CollectionEntry] = {e.id: e for e in entries}
        self._default_id = default_id

    @property
    def default_id(self) -> str:
        return self._default_id

    def entries(self) -> list[CollectionEntry]:
        return list(self._entries.values())

    def has(self, cid: str) -> bool:
        return cid in self._entries

    def add(self, entry: CollectionEntry) -> None:
        """Register a runtime-created collection (``POST /v1/collections``).
        Raises ``KeyError`` on a duplicate id so the router can 409."""
        if entry.id in self._entries:
            raise KeyError(entry.id)
        self._entries[entry.id] = entry

    def remove(self, cid: str) -> bool:
        """Drop a collection binding. Returns ``False`` if the id is unknown."""
        return self._entries.pop(cid, None) is not None

    def resolve(self, cid: str | None) -> CollectionEntry:
        """Entry for ``cid``, or the default when ``cid`` is None. Raises
        ``KeyError`` for an unknown non-None id so the router can 400 (explicit
        selection should fail loudly, not silently serve the wrong corpus)."""
        if cid is None:
            return self._entries[self._default_id]
        return self._entries[cid]  # KeyError → 404/400 at the router


def load_collection_specs(settings: Any) -> list[CollectionSpec]:
    """Parse ``collections_file`` (preferred) or ``collections_json`` into specs.
    Returns [] (single-collection mode) when neither is set."""
    raw: str = ""
    if settings.collections_file:
        try:
            with open(settings.collections_file, encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            raise RuntimeError(f"collections_file unreadable: {e}") from e
    elif settings.collections_json:
        raw = settings.collections_json
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"collections config is not valid JSON: {e}") from e
    if not isinstance(data, list):
        raise RuntimeError("collections config must be a JSON list of specs")
    specs = [CollectionSpec.model_validate(d) for d in data]
    ids = [s.id for s in specs]
    if len(set(ids)) != len(ids):
        raise RuntimeError(f"duplicate collection ids in registry: {ids}")
    return specs


def persist_collection_spec(settings: Any, spec: CollectionSpec) -> bool:
    """Write-through append a newly created spec to ``collections_file`` so it
    survives restart (the lifespan re-reads that file). Returns ``False`` when no
    file is configured (in-memory only, lost on restart — same single-worker
    caveat as the model registry). Atomic via a temp file + ``os.replace``."""
    path: str = getattr(settings, "collections_file", "") or ""
    if not path:
        return False
    existing: list[Any] = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"collections_file unreadable for append: {e}") from e
        if not isinstance(existing, list):
            raise RuntimeError("collections_file must be a JSON list to append to")
    existing.append(spec.model_dump())
    _atomic_write_json(path, existing)
    return True


def forget_collection_spec(settings: Any, cid: str) -> bool:
    """Write-through remove the spec with id ``cid`` from ``collections_file`` so a
    delete survives restart. Returns ``False`` when no file is configured or the id
    isn't present in the file (e.g. an in-memory-only or default entry)."""
    path: str = getattr(settings, "collections_file", "") or ""
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"collections_file unreadable for removal: {e}") from e
    if not isinstance(existing, list):
        raise RuntimeError("collections_file must be a JSON list to remove from")
    kept = [d for d in existing if not (isinstance(d, dict) and d.get("id") == cid)]
    if len(kept) == len(existing):
        return False
    _atomic_write_json(path, kept)
    return True


def _atomic_write_json(path: str, data: Any) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
