"""Document loaders."""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from pathlib import Path

from ragstack.ingestion.enrich import (
    EMPTY,
    PublisherProfile,
    enrich,
    index_metadata,
)
from ragstack.models import Document
from ragstack.protocols import DocumentLoader

# Namespace for deriving *deterministic* document IDs. Chunk IDs — and therefore
# the Qdrant point IDs (uuid5 of the chunk ID, see stores/qdrant.py) — are derived
# from the document ID, so a random per-load doc ID makes every re-ingest write
# fresh points and silently duplicate the corpus. Deriving the doc ID from a
# stable key (resolved path / content) makes re-ingest overwrite in place.
_DOC_NAMESPACE = uuid.NAMESPACE_URL

# DOI as it appears inside a PDF's embedded info dictionary (see
# ``_doi_from_pdf_metadata``). Kept local to the loader so the module stays free
# of an import-time dependency on the enrichment code.
_PDF_META_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")


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

    Metadata is deliberately minimal: ``filename``, ``pages``, and — when the
    file carries one — ``doi``. The DOI is worth lifting out because it is the
    key that :mod:`ragstack.ingestion.doi_metadata` can turn into a real
    bibliographic record. The PDF's *other* embedded fields (``title``,
    ``author``) are deliberately NOT lifted: in practice they are dominated by
    producer junk ("Microsoft Word - final_v3.docx", "untitled"), and because
    enrichment's precedence rule is "existing metadata wins", writing junk here
    would permanently block the good remote title from ever landing.
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
            pages = [page.get_text() for page in doc.pages()]
            embedded_doi = _doi_from_pdf_metadata(doc)
        except Exception as e:
            raise LoaderError(f"could not extract text from PDF '{path.name}'") from e
        finally:
            doc.close()

        content = "\n".join(pages).strip()
        if not content:
            # Scanned/image-only PDFs yield no extractable text; surface it as a
            # typed loader error rather than silently ingesting an empty document.
            raise LoaderError(f"no extractable text in PDF '{path.name}'")
        metadata: dict[str, object] = {"filename": path.name, "pages": len(pages)}
        if embedded_doi:
            metadata["doi"] = embedded_doi
            metadata["doi_source"] = "pdf-metadata"
        return [
            Document(
                id=deterministic_doc_id(str(path.resolve())),
                content=content,
                metadata=metadata,
                source=source,
            )
        ]


def _doi_from_pdf_metadata(doc: object) -> str:
    """Best-effort DOI from a PDF's own info dictionary.

    Publishers commonly stamp the DOI into ``subject`` or ``keywords`` (and
    occasionally ``title``), which is more reliable than a first-page text scan
    for articles that print the DOI only in a sidebar or footer that extracts out
    of order. Free — the info dict is already parsed by the time we have the
    document — and never fatal: any surprise from a malformed PDF degrades to
    "no DOI", leaving the text scan in
    :func:`ragstack.ingestion.doi_metadata.document_doi` as the fallback.
    """
    from ragstack.ingestion.doi_metadata import normalize_doi

    try:
        info = getattr(doc, "metadata", None) or {}
        if not isinstance(info, dict):
            return ""
        for key in ("subject", "keywords", "title", "creator", "producer"):
            value = info.get(key)
            if not isinstance(value, str) or not value:
                continue
            match = _PDF_META_DOI.search(value)
            if match:
                doi = normalize_doi(match.group(0))
                if doi:
                    return doi
    except Exception:  # pragma: no cover - defensive; PDF info is untrusted input
        return ""
    return ""


class JsonlLoader:
    """Load a JSONL corpus of *pre-extracted* documents.

    Each line is one JSON object ``{"text", "path", "metadata"}`` (the shape
    emitted by the upstream PDF-extraction pipeline). Every line becomes one
    :class:`Document`, with scholarly metadata recovered by
    :mod:`ragstack.ingestion.enrich` (DOI / title / authors / year / doc_type)
    and stamped onto ``Document.metadata`` — so it propagates to every chunk and
    is filterable at query time. The heavy document-level fields (full citation
    list, abstract) are deliberately *not* propagated here to keep chunk payloads
    small; the bulk operator script captures those in a separate catalog.

    The document id is derived from the record's ``path`` (the original source
    PDF), so re-ingesting the same corpus overwrites in place rather than
    duplicating — same invariant the other loaders rely on.

    Records are skipped (not errored) when their ``doc_type`` is in
    ``skip_types`` — by default only empty-text records — and malformed lines
    are skipped too, so a single bad line never aborts a large ingest. An
    otherwise-empty file (no usable documents) raises :class:`LoaderError`.
    """

    def __init__(
        self,
        skip_types: Iterable[str] | None = None,
        profile: PublisherProfile | None = None,
    ) -> None:
        self._skip = frozenset(skip_types) if skip_types is not None else frozenset({EMPTY})
        # None → enrich() uses the ASM DEFAULT_PROFILE; pass a profile to ingest
        # a different publisher's corpus (different DOI prefix / filename rule /
        # front-matter set). Resolve from config via enrich.resolve_profile().
        self._profile = profile

    def load(self, source: str) -> list[Document]:
        path = Path(source)
        docs: list[Document] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    # One corrupt line shouldn't sink the whole corpus.
                    continue
                doc = self._document(record)
                if doc is not None:
                    docs.append(doc)
        if not docs:
            raise LoaderError("no usable documents in JSONL source")
        return docs

    def _document(self, record: dict) -> Document | None:
        enriched = enrich(record, profile=self._profile)
        if enriched.doc_type in self._skip:
            return None
        text = record.get("text", "") or ""
        rec_path = record.get("path", "") or ""
        # Fall back to the line content for the id key only if no path is given,
        # so a path-less record still gets a stable, content-derived id.
        key = str(Path(rec_path).resolve()) if rec_path else text
        return Document(
            id=deterministic_doc_id(key),
            content=text,
            metadata=index_metadata(enriched),
            source=rec_path,
        )


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
DEFAULT_INGEST_SUFFIXES = (".pdf", ".txt", ".md", ".jsonl")


def default_loader_registry(
    ingest_root: str | None = None,
    max_bytes: int = 0,
    profile: PublisherProfile | None = None,
) -> LoaderRegistry:
    """A registry wired with the built-in loaders (PDF + text/markdown + JSONL).

    ``profile`` is the publisher profile the JSONL loader enriches with (DOI
    prefix / filename rule / front-matter set); ``None`` keeps the ASM default.

    Note ``.jsonl`` is a *batch* format — one file yields many documents. The
    registry's ``max_bytes`` guard still applies per file, so very large corpora
    (the multi-hundred-MB extraction dumps) should be ingested with the
    ``scripts/ingest_jsonl.py`` operator tool, which streams and bypasses that
    single-file size ceiling.
    """
    registry = LoaderRegistry(ingest_root=ingest_root, max_bytes=max_bytes)
    text = TextFileLoader()
    registry.register(".pdf", PdfLoader())
    registry.register(".txt", text)
    registry.register(".md", text)
    registry.register(".jsonl", JsonlLoader(profile=profile))
    return registry
