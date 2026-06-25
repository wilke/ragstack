"""Store adapters. Concrete implementations conform to the protocols in
ragstack.protocols (VectorStore, TextIndex, GraphStore)."""
from __future__ import annotations

from ragstack.stores.memory import (
    InMemoryGraphStore,
    InMemoryTextIndex,
    InMemoryVectorStore,
)

__all__ = [
    "InMemoryGraphStore",
    "InMemoryTextIndex",
    "InMemoryVectorStore",
]

try:
    from ragstack.stores.qdrant import QdrantVectorStore as QdrantVectorStore

    __all__.append("QdrantVectorStore")
except ImportError:
    # qdrant-client not installed (it's an optional extra)
    pass
