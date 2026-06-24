"""Document loaders."""
from __future__ import annotations

import uuid
from pathlib import Path

from ragstack.models import Document
from ragstack.protocols import DocumentLoader

# Namespace for deriving *deterministic* document IDs. Chunk IDs — and therefore
# the Qdrant point IDs (uuid5 of the chunk ID, see stores/qdrant.py) — are derived
# from the document ID, so a random per-load doc ID makes every re-ingest write
# fresh points and silently duplicate the corpus. Deriving the doc ID from a
# stable key (resolved path / content) makes re-ingest overwrite in place.
_DOC_NAMESPACE = uuid.NAMESPACE_URL


def deterministic_doc_id(key: str) -> str:
    """Stable document ID for a normalized source key (path or content)."""
    return str(uuid.uuid5(_DOC_NAMESPACE, key))


class LoaderError(Exception):
    """A source could not be loaded (unsupported, unparseable, oversized, or
    outside the permitted ingest root). Carries a caller-safe message — never
    embed raw filesystem paths or upstream exception text in it."""


def confine_to_root(source: str, root: str | Path | None) -> Path:
    """Resolve ``source`` and confine it to ``root`` (the LFI / path-traversal
    guard). Returns the resolved path; raises ``LoaderError`` if it escapes root.
    The single home for this check — both per-file loads and directory manifest
    builds call it so the guard can't drift."""
    path = Path(source).resolve()  # resolve() collapses .. and follows symlinks
    if root is not None:
        root_path = Path(root).resolve()
        if path != root_path and not path.is_relative_to(root_path):
            raise LoaderError("source is outside the permitted ingest root")
    return path


class TextFileLoader:
    """Load plain-text or Markdown files from disk."""

    def load(self, source: str) -> list[Document]:
        path = Path(source)
        content = path.read_text(encoding="utf-8")
        return [
            Document(
                id=deterministic_doc_id(str(path.resolve())),
                content=content,
                metadata={"filename": path.name},
                source=source,
            )
        ]


class StringLoader:
    """Load a document directly from a string — useful for testing."""

    def load(self, source: str) -> list[Document]:
        return [
            Document(
                # The string itself is the only stable key we have here.
                id=deterministic_doc_id(source),
                content=source,
                source="<string>",
            )
        ]


class PdfLoader:
    """Extract text from a PDF.

    PyMuPDF is a parser: text extraction does not execute embedded JavaScript
    and does not fetch remote resources, so the usual malicious-PDF active
    vectors (JS, SSRF via remote URIs) are not exercised here. The import is
    lazy so the optional ``pdf`` extra is only required when PDFs are ingested.
    """

    def load(self, source: str) -> list[Document]:
        try:
            import pymupdf
        except ImportError as e:  # pragma: no cover - exercised only without the extra
            raise LoaderError(
                "PDF support requires the 'pdf' extra (pip install ragstack[pdf])"
            ) from e

        path = Path(source)
        try:
            doc = pymupdf.open(path)
        except Exception as e:
            raise LoaderError(f"could not open PDF '{path.name}'") from e
        try:
            pages = [page.get_text() for page in doc]
        except Exception as e:
            raise LoaderError(f"could not extract text from PDF '{path.name}'") from e
        finally:
            doc.close()

        content = "\n".join(pages).strip()
        if not content:
            # Scanned/image-only PDFs yield no extractable text; surface it as a
            # typed loader error rather than silently ingesting an empty document.
            raise LoaderError(f"no extractable text in PDF '{path.name}'")
        return [
            Document(
                id=deterministic_doc_id(str(path.resolve())),
                content=content,
                metadata={"filename": path.name, "pages": len(pages)},
                source=source,
            )
        ]


class LoaderRegistry:
    """Dispatch a source path to a loader by file extension.

    Also the single ingest ingress guard: it (1) confines the source to
    ``ingest_root`` when set — defeating the path-traversal / arbitrary-file-read
    (LFI) exposure of feeding ``request.source`` straight into ``open()`` — and
    (2) rejects oversized inputs. Satisfies the ``DocumentLoader`` protocol so it
    drops into the pipeline in place of a bare loader.
    """

    def __init__(
        self,
        ingest_root: str | None = None,
        max_bytes: int = 0,
        default: DocumentLoader | None = None,
    ) -> None:
        self._root = Path(ingest_root).resolve() if ingest_root else None
        self._max_bytes = max_bytes
        self._loaders: dict[str, DocumentLoader] = {}
        self._default: DocumentLoader = default if default is not None else TextFileLoader()

    def register(self, suffix: str, loader: DocumentLoader) -> None:
        self._loaders[suffix.lower()] = loader

    def _resolve(self, source: str) -> Path:
        path = confine_to_root(source, self._root)
        if not path.is_file():
            raise LoaderError("source not found")
        if self._max_bytes and path.stat().st_size > self._max_bytes:
            raise LoaderError("source exceeds the maximum allowed size")
        return path

    def load(self, source: str) -> list[Document]:
        path = self._resolve(source)
        loader = self._loaders.get(path.suffix.lower(), self._default)
        return loader.load(str(path))


# Suffixes the built-in registry handles — the single source of truth for what a
# directory ingest enqueues. Keep default_loader_registry registrations in sync.
DEFAULT_INGEST_SUFFIXES = (".pdf", ".txt", ".md")


def default_loader_registry(
    ingest_root: str | None = None, max_bytes: int = 0
) -> LoaderRegistry:
    """A registry wired with the built-in loaders (PDF + text/markdown)."""
    registry = LoaderRegistry(ingest_root=ingest_root, max_bytes=max_bytes)
    text = TextFileLoader()
    registry.register(".pdf", PdfLoader())
    registry.register(".txt", text)
    registry.register(".md", text)
    return registry
