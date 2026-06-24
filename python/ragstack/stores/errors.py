"""Store error types.

Kept dependency-free (no qdrant-client import) so callers can catch them
without pulling in optional backends.
"""
from __future__ import annotations


class VectorDimMismatch(RuntimeError):
    """An existing collection's vector size disagrees with the configured
    embedding dimension. Fatal: writing would mix incompatible vectors."""
