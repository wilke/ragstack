"""Store error types.

Kept dependency-free (no qdrant-client import) so callers can catch them
without pulling in optional backends.
"""
from __future__ import annotations


class StoreUnavailable(RuntimeError):
    """A backing store could not answer: unreachable, timed out, or returned a
    server-side error. Transient by nature — the API maps it to 503 (never 500)
    so callers can tell "retry later" from "bug". ``store`` names the backend."""

    def __init__(self, store: str, message: str) -> None:
        super().__init__(message)
        self.store = store


class VectorDimMismatch(RuntimeError):
    """An existing collection's vector size disagrees with the configured
    embedding dimension. Fatal: writing would mix incompatible vectors."""
