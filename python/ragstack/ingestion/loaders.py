"""Document loaders."""
from __future__ import annotations

import json
import logging
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
from ragstack.ingestion.jats import JATS_PASSTHROUGH_KEYS, convert_file, inherit_article_ids
from ragstack.ingestion.jats_structure import record_structure
from ragstack.ingestion.structure import structure_from_record
from ragstack.models import Document
from ragstack.protocols import DocumentLoader

log = logging.getLogger(__name__)

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

# Per-item job errors of the GoWe extract step (``scripts/pdf_extract.py``,
# #828), constant so they count like ``NO_TEXT_ERROR``:
#: a ``.md``/``.txt`` whose text is empty or whitespace only. ``JsonlLoader``
#: would drop such a record downstream with no receipt row, so the extract step
#: reports it instead. (In-process, such a file is a document with no chunks.)
EMPTY_TEXT_ERROR = "no text (empty document)"
#: a ``.jsonl`` batch file staged into the GoWe extract step. One file is many
#: documents there, which the per-file receipt cannot account for (and path-less
#: records would share one id), so it is refused rather than half-ingested.
JSONL_UNSUPPORTED_ERROR = "jsonl is not ingestable through the GoWe extract step"


NO_LOADER_LABEL = "no_loader"


def no_loader_error(suffix: str) -> str:
    """The per-item job error for a file whose suffix has no loader (#202,
    #833 item 1): constant per suffix, so it is countable like
    :data:`NO_TEXT_ERROR` — ``"no loader for .rst"``. The same string on every
    path: the upload staging refusal, :class:`NoLoader` from
    :meth:`LoaderRegistry.load` (by-reference ingest), and the GoWe extract
    step (``scripts/pdf_extract.py``)."""
    return f"no loader for {suffix.lower() or '(no suffix)'}"


class NoLoader(LoaderError):
    """A source whose suffix has no registered loader (#833 item 1).

    Raised by :meth:`LoaderRegistry.load` BEFORE a byte of the file is read:
    an unknown suffix is refused, never read as text (it used to fall back to
    :class:`TextFileLoader`, so a by-reference ``.rst`` or ``.csv`` was
    ingested as raw text while the upload and GoWe paths refused it). The
    item's ``job_error`` is :func:`no_loader_error`; the message carries the
    :data:`NO_LOADER_LABEL` too, so the per-item warning line is countable."""

    job_error: str

    def __init__(self, suffix: str) -> None:
        self.suffix = suffix
        self.job_error = no_loader_error(suffix)
        super().__init__(f"{self.job_error} [{NO_LOADER_LABEL}]")


#: The per-item job error for a well-formed ``.xml`` that yields no JATS
#: record at all — no abstract/body prose and no table or figure (#828 PR 5a):
#: most likely not a JATS article. Constant, like :data:`NO_TEXT_ERROR`.
NO_JATS_CONTENT_ERROR = "no JATS article content (not a JATS article?)"
NO_JATS_CONTENT_LABEL = "no_jats_content"

#: The per-item job error for a ``.xml``/``.nxml`` that is not well-formed XML
#: (#828 PR 5a). Constant (no file name — the item id names the file), so it
#: counts like :data:`NO_TEXT_ERROR`.
JATS_PARSE_ERROR = "could not parse JATS XML"
JATS_PARSE_LABEL = "jats_parse_error"


class NoTextExtracted(LoaderError):
    """A PDF opened and parsed fine but produced no text at all — an image-only
    (scanned) PDF, or one whose text is outlined. Distinguished from the other
    ``LoaderError`` cases so the job records the actionable ``job_error`` string
    per item instead of a bare class name, and so the count of such items per
    job is visible (the data #202's OCR decision needs)."""

    job_error = NO_TEXT_ERROR


class NoJatsContent(LoaderError):
    """A well-formed XML file that produced no JATS record (#828 PR 5a) — a
    document element other than ``<article>`` (``<config/>``, an XHTML page),
    or an article with neither prose nor floats. The item records the
    actionable :data:`NO_JATS_CONTENT_ERROR`."""

    job_error = NO_JATS_CONTENT_ERROR


class JatsParseError(LoaderError):
    """A ``.xml``/``.nxml`` that is not well-formed XML (#828 PR 5a). The item
    records the constant :data:`JATS_PARSE_ERROR`; the message names the file
    and carries :data:`JATS_PARSE_LABEL` for the warning line."""

    job_error = JATS_PARSE_ERROR


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
    """Load plain-text or Markdown files from disk, as raw text.

    The file is read verbatim (UTF-8, universal newlines): Markdown syntax, link
    targets, inline HTML and code all survive. Never route these suffixes
    through :class:`PdfLoader` — PyMuPDF >= 2 *opens* ``.md`` and returns a
    rendering that drops link targets, HTML and some code (#828)."""

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
            # PyMuPDF >= 2 also opens non-PDF documents (Markdown, text, XML,
            # images…) and returns a RENDERING of them. A ``.pdf`` name on such
            # bytes must not be ingested as that rendering (#828).
            if not doc.is_pdf:
                raise LoaderError(f"could not open PDF '{path.name}' (not a PDF)")
            pages = [page.get_text() for page in doc.pages()]
            embedded_doi = _doi_from_pdf_metadata(doc)
        except LoaderError:
            raise
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

    ``structure_invalid``
        The record's top-level ``structure`` key (#828) was present but broke an
        invariant (or was malformed, or too large), counted by the reason
        :func:`~ragstack.ingestion.structure.structure_from_record` gave. The
        STRUCTURE was dropped; the document itself was kept and ingests as
        unstructured. A stale or corrupt structure is loud, never silent.
    """

    not_allowed: Counter[str] = field(default_factory=Counter)
    unusable: Counter[str] = field(default_factory=Counter)
    structure_invalid: Counter[str] = field(default_factory=Counter)

    def __bool__(self) -> bool:
        return bool(self.not_allowed or self.unusable or self.structure_invalid)

    def summary(self) -> str:
        """One line for an operator log, or ``""`` when nothing was dropped."""
        parts = []
        if self.not_allowed:
            parts.append("never in --metadata-passthrough: " + ", ".join(
                f"{k} (x{n})" for k, n in sorted(self.not_allowed.most_common())))
        if self.unusable:
            parts.append("opted in but unusable: " + ", ".join(
                f"{k} on {n} record(s)" for k, n in sorted(self.unusable.most_common())))
        if self.structure_invalid:
            parts.append("structure dropped, document kept: " + ", ".join(
                f"{k} on {n} record(s)"
                for k, n in sorted(self.structure_invalid.most_common())))
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
        # A JATS unit record takes its article's ids (#975 round 3, argo S1a):
        # ``enrich`` never derives a unit's DOI from its own text.
        inherit_article_ids(docs)
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
        # #828: the record's top-level ``structure`` key, validated against
        # ``text`` by the one shared reader. Absent → no structure, and the
        # Document is byte-identical to one built before the key existed;
        # invalid → the structure is dropped and counted, the document kept.
        structure, reason = structure_from_record(record, text)
        if reason is not None:
            self.dropped.structure_invalid[reason] += 1
        return Document(
            id=deterministic_doc_id(key),
            content=text,
            metadata=self._metadata(enriched, record),
            source=rec_path,
            structure=structure,
        )


class JatsLoader:
    """Load one JATS XML article as its records: the article, and each table
    and figure (#828 PR 5a, #833 item 2).

    The in-process route to what ``scripts/jats_extract.py`` + ``JsonlLoader``
    produce (path C of #828 §8), built from the same three pieces so the two
    cannot drift:

    1. the parser — :func:`ragstack.ingestion.jats.convert_file` with
       ``with_spans=True``; the record key is
       :func:`ragstack.ingestion.jats.record_key` (``PMC123.1.xml`` →
       ``PMC123``; else the article's own PMC id; else the file's resolved
       path), the rule ``jats_extract`` keys its records on;
    2. the structure — :func:`ragstack.ingestion.jats_structure.record_structure`,
       the helper ``jats_extract`` writes the shard's ``structure`` key with;
    3. the ``Document`` — :meth:`JsonlLoader._document` of a ``JsonlLoader``
       with ``passthrough_keys=JATS_PASSTHROUGH_KEYS`` (the
       ``cwl/jats-ingest.cwl`` default): the same enrich, ``index_metadata``,
       passthrough and ``structure_from_record`` code a shard record goes
       through. So a PMC-keyed file's ``Document.id`` is
       ``deterministic_doc_id("PMC123")`` (the record ``path`` is a relative,
       opaque id) and its ``Document.source`` is ``"PMC123"`` /
       ``"PMC123#table-1"``. A file with no PMC id in its name or its article
       is keyed on its resolved path: the article record's ``path`` is
       ``/corpus/paper.xml``, an absolute path ``_document`` resolves again
       (a no-op), so its ``Document.id`` is the id ``PdfLoader`` gives a file
       at that path, and its units are ``/corpus/paper.xml#table-1``. Two
       files share ids only when they name one PMC id (one work: a replace)
       or are one file.

    The key is read from the path the registry hands over — the RESOLVED,
    confined path, so a symlink ``PMC4.xml → store/abc123.xml`` keys on
    ``store/abc123.xml`` unless the article carries a PMC id.

    Units are split by CHARACTER budgets only (``count_tokens=None``):
    ``jats_extract`` defaults to an HF tokenizer budget the API image does not
    carry, so an oversized table can split differently (and get other unit
    ids) on the two routes; a table under the budget is identical.

    A file that does not parse raises :class:`JatsParseError`
    (:data:`JATS_PARSE_ERROR`); a well-formed file whose document element is
    not ``<article>`` (a ``<pmc-articleset>`` holding exactly one is that
    article; one holding several is refused), or that yields no record,
    raises :class:`NoJatsContent`; a file that cannot be read, a bare
    :class:`LoaderError`. Never returns ``[]``. A record whose
    structure cannot be built is kept without one and counted in
    :attr:`dropped` (``structure_invalid``), as ``jats_extract`` counts it.
    """

    def __init__(self, profile: PublisherProfile | None = None) -> None:
        self._records = JsonlLoader(profile=profile, passthrough_keys=JATS_PASSTHROUGH_KEYS)
        #: The record loader's drop report, shared: passthrough drops and
        #: structure drops, accumulated across ``load()`` calls.
        self.dropped = self._records.dropped

    def load(self, source: str) -> list[Document]:
        path = Path(source)
        records, skipped, spans, units = convert_file(
            path, with_spans=True, require_article=True)
        failed = next((s for s in skipped if s.get("kind") == "article"), None)
        if failed is not None:
            # convert_file reports a file it could not convert as a skip; here
            # it is the item's failure. Caller-safe messages: name only.
            why = str(failed.get("reason", ""))
            if why.startswith("parse error"):
                raise JatsParseError(
                    f"{JATS_PARSE_ERROR} '{path.name}' [{JATS_PARSE_LABEL}]")
            if why.startswith("not a JATS article"):
                raise NoJatsContent(f"not a JATS article: '{path.name}' "
                                    f"[{NO_JATS_CONTENT_LABEL}]")
            raise LoaderError(f"could not read JATS XML '{path.name}'")
        docs: list[Document] = []
        for record in records:
            dumped, reason = record_structure(record, spans, units)
            if dumped is not None:
                record["structure"] = dumped
            else:
                self.dropped.structure_invalid[str(reason)] += 1
                log.warning("JatsLoader: %s: structure dropped (%s)", record["path"], reason)
            doc = self._records._document(record)
            if doc is not None:
                docs.append(doc)
        if not docs:
            raise NoJatsContent(f"no JATS article content in '{path.name}'")
        inherit_article_ids(docs)  # as JsonlLoader.load (#975 round 3, argo S1a)
        return docs


class LoaderRegistry:
    """Dispatch a source path to a loader by file extension.

    Also the single ingest ingress guard: it (1) confines the source to
    ``ingest_root`` when set — defeating the path-traversal / arbitrary-file-read
    (LFI) exposure of feeding ``request.source`` straight into ``open()`` — and
    (2) rejects oversized inputs. Satisfies the ``DocumentLoader`` protocol so it
    drops into the pipeline in place of a bare loader.

    A suffix with no registration is REFUSED with :class:`NoLoader`, never read
    (#833 item 1): there is no default loader. It used to be
    :class:`TextFileLoader`, which ingested a by-reference ``.rst``/``.csv``
    as raw text while the upload and GoWe paths refused it.
    """

    def __init__(self, ingest_root: str | None = None, max_bytes: int = 0) -> None:
        self._root = Path(ingest_root).resolve() if ingest_root else None
        self._max_bytes = max_bytes
        self._loaders: dict[str, DocumentLoader] = {}

    def register(self, suffix: str, loader: DocumentLoader) -> None:
        self._loaders[suffix.lower()] = loader

    def _dispatch(self, given: Path, resolved: Path) -> tuple[DocumentLoader | None, str]:
        """``(loader, suffix)`` for one source: the RESOLVED path's suffix
        decides. Only when the resolved path has NO suffix at all does the
        suffix of the path AS GIVEN decide instead: a link to a
        content-addressed blob (``notes.md → ../blobs/<sha256>``: the HF hub
        cache, DVC), which a directory ingest enqueues by the link's suffix.
        A target with a suffix of its own is dispatched on it, registered or
        not — ``report.md → report.docx`` is ``no loader for .docx``, its
        bytes never read as text. ``(None, suffix)`` names the suffix the
        refusal reports: the resolved one, else the given one."""
        sfx = (resolved.suffix or given.suffix).lower()
        loader = self._loaders.get(sfx) if sfx else None
        return loader, sfx

    def dispatch(self, source: str) -> tuple[DocumentLoader | None, str]:
        """:meth:`_dispatch` of ``source`` — the decision :meth:`load` takes,
        without confinement, existence or size check (a path that cannot be
        resolved is taken as given)."""
        given = Path(source)
        try:
            resolved = given.resolve()
        except (OSError, RuntimeError):
            resolved = given
        return self._dispatch(given, resolved)

    def loader_for(self, source: str) -> DocumentLoader | None:
        """The loader :meth:`load` would dispatch ``source`` to, or ``None``
        when its suffix has no registration (where :meth:`load` raises
        :class:`NoLoader`). Dispatch only: no confinement, existence or size
        check.

        Lets a caller outside the API process (the GoWe extract tool, #828) ask
        the SAME suffix map the in-process path loads through, instead of
        keeping its own copy that can drift — the drift that sent ``.md`` files
        through PyMuPDF on one path and ``TextFileLoader`` on the other. Reads
        the same suffix as :meth:`load` (the resolved path's; the path's as
        given only when the resolved one has none)."""
        return self.dispatch(source)[0]

    def _resolve(self, source: str) -> Path:
        path = confine_to_root(source, self._root)
        if not path.is_file():
            raise LoaderError("source not found")
        if self._max_bytes and path.stat().st_size > self._max_bytes:
            raise LoaderError("source exceeds the maximum allowed size")
        return path

    def load(self, source: str) -> list[Document]:
        path = self._resolve(source)
        loader, suffix = self._dispatch(Path(source), path)
        if loader is None:
            raise NoLoader(suffix)
        # The loader always gets the RESOLVED (confined) path.
        return loader.load(str(path))


# Suffixes the built-in registry handles — the single source of truth for what a
# directory ingest enqueues (and what the upload staging, the file-name
# duplicate pre-check and ``work_identity.ids_from_filename`` treat as
# ingestable). Keep default_loader_registry registrations in sync.
DEFAULT_INGEST_SUFFIXES = (".pdf", ".txt", ".md", ".jsonl", ".xml", ".nxml")


def default_loader_registry(
    ingest_root: str | None = None,
    max_bytes: int = 0,
    profile: PublisherProfile | None = None,
) -> LoaderRegistry:
    """A registry wired with the built-in loaders (PDF + text/markdown + JSONL +
    JATS XML). Any other suffix is refused (:class:`NoLoader`).

    ``profile`` is the publisher profile the JSONL and JATS loaders enrich with
    (DOI prefix / filename rule / front-matter set); ``None`` keeps the ASM
    default.

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
    jats_loader = JatsLoader(profile=profile)
    registry.register(".xml", jats_loader)
    registry.register(".nxml", jats_loader)  # the PMC OA bulk package's suffix
    return registry
