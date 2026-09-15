"""Document loaders."""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ragstack import metadata_schema
from ragstack.ingestion.enrich import (
    EMPTY,
    EnrichedDoc,
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


# The per-item job error recorded for a PDF that yields no text (#202): an
# actionable, constant, caller-safe string (no filename — the item id already
# names the file), so it can be counted per job with a GROUP BY on the SQL job
# stores. ``NO_TEXT_LABEL`` is the short label the INFO count line carries.
NO_TEXT_ERROR = "no extractable text (scanned PDF?)"
NO_TEXT_LABEL = "no_text"


NO_LOADER_LABEL = "no_loader"


def no_loader_error(suffix: str) -> str:
    """The per-item job error for a staged file whose suffix has no loader
    (#202): constant per suffix, so it is countable like :data:`NO_TEXT_ERROR`
    — ``"no loader for .xml"``."""
    return f"no loader for {suffix.lower() or '(no suffix)'}"


class NoTextExtracted(LoaderError):
    """A PDF opened and parsed fine but produced no text at all — an image-only
    (scanned) PDF, or one whose text is outlined. Distinguished from the other
    ``LoaderError`` cases so the job records the actionable ``job_error`` string
    per item instead of a bare class name, and so the count of such items per
    job is visible (the data #202's OCR decision needs)."""

    job_error = NO_TEXT_ERROR


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
            raise NoTextExtracted(f"no extractable text in PDF '{path.name}' (scanned PDF?)")
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


# Value types an opted-in passthrough key may carry onto a chunk. Chunk metadata
# is indexed in Elasticsearch, where ``metadata.*`` is mapped by a dynamic
# template (stores/elasticsearch._MAPPINGS) whose ``path_match`` matches exactly
# ONE level: a nested object's subfields (``metadata.x.y``) miss the template and
# fall back to ES's default dynamic mapping (text + a ``.keyword`` subfield),
# which breaks the exact-term filter contract ``_build_query`` assumes for every
# metadata key. So dicts — and lists containing them — are dropped rather than
# mapped badly. Scalars and *flat* lists of scalars are exactly what the enriched
# schema itself already emits (``authors``/``keywords``) and index correctly.
_PASSTHROUGH_SCALARS = (str, int, float, bool)


def _passthrough_value(key: str, value: object) -> Any | None:
    """Return the value to stamp on every chunk for ``key``, or ``None`` to drop it.

    ``None`` means "drop this key", which is how the empty-value rule of
    :func:`~ragstack.ingestion.enrich.index_metadata` is kept consistent for
    passthrough keys: a record that simply lacks a value for an opted-in key must
    not litter the payload (and the ES keyword index) with ``""`` / ``[]`` /
    ``null``. Blank-but-not-empty strings count as empty here too, so a
    whitespace-only raw value can never take the slot of an enriched field that
    ``index_metadata`` dropped for being empty.

    A key the type table declares an INTEGER field
    (:data:`ragstack.metadata_schema.KNOWN_INT_FIELDS` — today ``year``) is
    coerced here, and dropped when it cannot be. Passing the raw value through
    was a *silent, within-collection* type split: ``index_metadata`` drops a
    field when the enricher derived none, and the passthrough then filled the
    empty slot with the corpus's raw string — so one collection could hold
    ``year: 2019`` on one chunk and ``year: "2019"`` on the next, with a correct
    ``{"year": 2019}`` filter matching only the first. The producer must write
    the type the filter demands; that rule lives in exactly one table, and this
    is where the loader consults it.

    NOTE on reachability: for ``year`` specifically, ``enrich.derive_year`` now
    consults the record's declared year first, so ``index_metadata`` almost
    always fills the slot and this branch never runs. It is the guard for the
    other fields ``KNOWN_INT_FIELDS`` will grow to hold, and for a record whose
    declared year ``derive_year`` rejected but whose raw value the corpus still
    carries under an opted-in key.
    """
    # Read through the module, not a bound copy: there is ONE table, and a
    # second reference to it is a second thing to keep in sync.
    if key in metadata_schema.KNOWN_INT_FIELDS:
        return metadata_schema.coerce_declared(key, value)
    if isinstance(value, _PASSTHROUGH_SCALARS):
        if isinstance(value, str) and not value.strip():
            return None
        return value
    if isinstance(value, list) and value and all(
        isinstance(v, _PASSTHROUGH_SCALARS) for v in value
    ):
        return value
    return None


#: Record-metadata keys that are dropped from every chunk BY DESIGN, on every
#: corpus — ``enrich._HEAVY_FIELDS``. Excluded from the drop report because a
#: line that appears unconditionally on every run carries no information and
#: teaches an operator to stop reading the report.
_CONSUMED_BY_DESIGN = frozenset({"abstract", "citations"})


@dataclass
class DropReport:
    """Which record-metadata keys did not reach a document, by CAUSE and count.

    The two causes need different actions from different people, so they are not
    one number:

    ``not_allowed``
        The key is not in ``passthrough_keys`` at all. A **policy** gap: the
        allow-list is a per-corpus argument, and this is constant across the
        corpus — every record loses the key. This is the shape of the
        ``open-access`` defect.

    ``unusable``
        The key IS opted in, but its value could not be stamped on ``n`` records
        — empty, a nested object, or a declared-int field carrying something that
        is not an int. A **data** problem, usually a minority of records, and the
        count is the interesting part: ``pmid`` unusable on 3 of 40,000 records
        is noise, on 40,000 of 40,000 it is a broken extractor.
    """

    not_allowed: Counter[str] = field(default_factory=Counter)
    unusable: Counter[str] = field(default_factory=Counter)

    def __bool__(self) -> bool:
        return bool(self.not_allowed or self.unusable)

    def summary(self) -> str:
        """One line for an operator log, or ``""`` when nothing was dropped."""
        parts = []
        if self.not_allowed:
            parts.append("never in --metadata-passthrough: " + ", ".join(
                f"{k} (x{n})" for k, n in sorted(self.not_allowed.most_common())))
        if self.unusable:
            parts.append("opted in but unusable: " + ", ".join(
                f"{k} on {n} record(s)" for k, n in sorted(self.unusable.most_common())))
        return "; ".join(parts)


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

    ``passthrough_keys`` opts specific raw ``record["metadata"]`` keys back in
    (see :meth:`_metadata`). It is deliberately **per-instance only** — there is
    no settings/config field for it, and none should be added on spec: the
    allow-list is a property of the *corpus* being loaded, not of the server, and
    a globally-configured list would silently change what a shard's chunks carry.
    Callers that need it (the bulk shard scripts) pass it explicitly.
    """

    def __init__(
        self,
        skip_types: Iterable[str] | None = None,
        profile: PublisherProfile | None = None,
        passthrough_keys: Iterable[str] | None = None,
    ) -> None:
        self._skip = frozenset(skip_types) if skip_types is not None else frozenset({EMPTY})
        # None → enrich() uses the ASM DEFAULT_PROFILE; pass a profile to ingest
        # a different publisher's corpus (different DOI prefix / filename rule /
        # front-matter set). Resolve from config via enrich.resolve_profile().
        self._profile = profile
        # Opt-in passthrough for raw metadata keys the fixed EnrichedDoc schema
        # has no slot for. Needed by non-PDF corpora: JATS/PMC carries pmcid,
        # pmid, journal, publisher, licence, section_title, sha256, source_url —
        # and, load-bearing, content_type, without which "filter to tables" is
        # unanswerable at query time and re-stamping means a full re-ingest (the
        # Qdrant point id is uuid5 of tenant+chunk_id). Default off, so the
        # existing ASM/PDF path is byte-for-byte unchanged.
        self._passthrough = frozenset(passthrough_keys or ())
        # What this loader discarded, accumulated across ``load()`` calls. NOT a
        # behaviour change — the keys were always dropped; what was missing is
        # any way to find out. A silent drop is how the 47.6M-chunk
        # ``open-access`` collection ended up without ``year`` on 85.2% of its
        # chunks, and a caller who cannot see the drop cannot notice the omission
        # until a filter returns a plausible-looking wrong answer months later.
        self.dropped = DropReport()

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

    def _metadata(self, enriched: EnrichedDoc, record: dict) -> dict[str, Any]:
        """The enriched index subset, plus any opted-in raw keys it has no slot for.

        The enriched value **always wins** on a name collision: a passthrough key
        may never shadow a field the enricher derived and validated. That matters
        for ``doi``/``title``, whose derivation encodes real precedence rules
        (``enrich.derive_doi``: metadata > filename > text, with normalisation) —
        letting the raw record overwrite the result would quietly undo them.

        Only keys present in the record with a non-empty, index-safe value are
        added (see :func:`_passthrough_value`); an absent or empty key is simply
        omitted rather than stamped as ``""``/``null``, matching
        :func:`~ragstack.ingestion.enrich.index_metadata`.

        Every key that does NOT make it onto the document is counted in
        :attr:`dropped`, by cause, so the drop is observable instead of silent —
        the allow-list is a per-corpus argument and forgetting an entry is
        otherwise invisible until a filter quietly under-returns.
        """
        meta = index_metadata(enriched)
        # No isinstance guard on ``raw``: ``enrich()`` ran first on this same
        # record and already did ``metadata.get(...)``, so a non-dict ``metadata``
        # raised before we got here (see the note in the module tests).
        raw = record.get("metadata") or {}
        extra: dict[str, Any] = {}
        # Iterate the record (not the allow-list) so key order is the record's —
        # deterministic — rather than a frozenset's arbitrary iteration order.
        for key, value in raw.items():
            if key in meta:
                # The enriched field won a collision — not a loss: the value
                # reached the document under the same name.
                continue
            if key in _CONSUMED_BY_DESIGN:
                # ``abstract``/``citations`` are ``_HEAVY_FIELDS``: excluded from
                # every chunk on purpose, on every corpus, forever. Counting them
                # would put a constant line of noise on every JATS shard and
                # train an operator to ignore the report.
                continue
            if key not in self._passthrough:
                self.dropped.not_allowed[key] += 1
                continue
            safe = _passthrough_value(key, value)
            if safe is None:
                self.dropped.unusable[key] += 1
                continue
            extra[key] = safe
        return {**extra, **meta}

    def _document(self, record: dict) -> Document | None:
        enriched = enrich(record, profile=self._profile)
        if enriched.doc_type in self._skip:
            return None
        text = record.get("text", "") or ""
        rec_path = record.get("path", "") or ""
        # The id key. Three cases, in order:
        # - ABSOLUTE path: resolve() (normalizes symlinks) — stable, and what every
        #   existing corpus was ingested with, so those doc ids are preserved.
        # - RELATIVE path: use the LITERAL string. resolve() here would prepend the
        #   process CWD, making the "deterministic" id a function of where the
        #   loader happened to run — found live when a GoWe worker re-ingesting
        #   the same JATS shard minted a second id family for every record
        #   (its task workdir differed from the first run's cwd) and delete-prior
        #   matched nothing, duplicating the corpus instead of upserting it.
        #   Opaque identifiers like "PMC123#table-2" are relative paths too.
        # - No path: the line content, so a path-less record still gets a stable,
        #   content-derived id.
        if rec_path:
            key = str(Path(rec_path).resolve()) if Path(rec_path).is_absolute() else rec_path
        else:
            key = text
        return Document(
            id=deterministic_doc_id(key),
            content=text,
            metadata=self._metadata(enriched, record),
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
