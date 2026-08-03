"""DOI-based scholarly metadata enrichment (Crossref, DataCite fallback).

Why this exists: PDFs uploaded through ``/v1/ingest`` reach
:class:`~ragstack.ingestion.loaders.PdfLoader`, which can only report what the
file itself carries — ``{filename, pages}`` and (sometimes) an embedded DOI. No
title, no authors, no journal. Every downstream consumer that labels a document
(the UI's ``title -> filename -> source_path -> doi -> doc_id`` precedence, the
``/v1/query`` source list, citation rendering) therefore falls through to a bare
filename like ``PMC10100743.pdf``. Resolving the document's DOI against Crossref
recovers the real bibliographic record once, at ingest, and stamps it onto every
chunk — so it is present everywhere downstream with no query-time cost.

This module **composes with** :mod:`ragstack.ingestion.enrich` rather than
duplicating it:

* DOI discovery reuses :func:`ragstack.ingestion.enrich.derive_doi` for its
  trustworthy legs (explicit metadata, then the publisher filename->DOI rule) and
  therefore honours the configured
  :class:`~ragstack.ingestion.enrich.PublisherProfile`. Only the *text* leg is
  reimplemented here (:func:`scan_text_for_doi`), because full PDFs need a wider
  window than the JSONL corpus ``derive_doi`` was tuned for and widening it there
  would change existing JSONL ingest behaviour.
* ``enrich`` stays pure/offline (it is the local, no-network leg); everything
  network-touching lives here, behind an explicit opt-in.

Three properties are non-negotiable:

**Never fail an ingest.** Every network path is wrapped: a timeout, a 404, a
rate-limit, malformed JSON, an unreachable host, or a nonsense DOI logs at
warning/debug and returns ``None``. The document keeps whatever metadata it
already had. :meth:`DoiEnricher.enrich_documents` additionally catches at the
top level, so no exception can escape into the pipeline.

**Never clobber better local data.** Precedence is *existing explicit metadata
wins; enrichment only fills gaps* — see :func:`merge_enrichment`. A title the
loader (or the operator, or the JSONL corpus) already supplied is never
overwritten by a remote record, in either direction of disagreement. The only
thing enrichment adds unconditionally is its own provenance stamp
(``doi_enriched_from``).

**Be polite.** Bounded concurrency, a per-request timeout, a descriptive
``User-Agent`` carrying a contact address (Crossref's "polite pool"), a single
bounded retry that honours ``Retry-After``, and an on-disk cache so re-ingests
and repeated DOIs never re-hit the API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from ragstack.ingestion.enrich import PublisherProfile, derive_doi
from ragstack.models import Document

log = logging.getLogger(__name__)

CROSSREF_URL = "https://api.crossref.org/works/{doi}"
DATACITE_URL = "https://api.datacite.org/dois/{doi}"

# Accepts the standard DOI shape and the usual prose wrappers ("doi:10.x/y",
# "https://doi.org/10.x/y"). Case-insensitive: DOI suffixes are defined to be
# case-insensitive, so "10.1128/JVI.02415-06" and the lowercase form are the
# same record.
_DOI_PREFIXES = re.compile(
    r"^\s*(?:(?:https?://)?(?:dx\.)?doi\.org/|doi:\s*|info:doi/)", re.IGNORECASE
)
_DOI_SHAPE = re.compile(r"^10\.\d{4,9}/\S+$")
# A DOI as it appears in running prose (same shape enrich._DOI_IN_TEXT uses).
_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")

# Inline markup tags Crossref/DataCite embed in titles (JATS + a little HTML).
# A whitelist, not a generic ``<[^>]+>``: stripping anything angle-bracketed
# would silently mangle a title containing a real "<" (e.g. "pH < 7", "<0.05").
_JATS_TAGS = (
    "i|b|em|strong|sub|sup|scp|sc|span|p|br|italic|bold|roman|monospace|"
    "overline|underline|sans-serif|inline-formula|alternatives|tex-math|"
    "mml:[a-z]+"
)
_JATS_TAG = re.compile(rf"</?(?:{_JATS_TAGS})(?:\s[^>]*)?/?>", re.IGNORECASE)

# How far into a document to look for its own DOI. Wide enough to cover a full
# PDF's front matter (title page + running headers), narrow enough to stop short
# of the reference list, which is a dense field of *other* papers' DOIs. See
# scan_text_for_doi.
TEXT_SCAN_CHARS = 20_000

# Fields the resolver produces, in the normalized vocabulary. ``type`` is
# Crossref's work type ("journal-article"); it is deliberately NOT called
# ``doc_type`` — that key already means something else in this codebase
# (enrich.classify's article/supplement/front-matter/short/empty), and reusing
# it would silently change how existing filters behave. It lands on chunk
# metadata as ``publication_type`` (see FIELD_TO_METADATA_KEY).
NORMALIZED_FIELDS = (
    "title",
    "authors",
    "journal",
    "year",
    "doi",
    "publisher",
    "type",
    "url",
)

# Normalized field -> the metadata key it is written to. Everything is identity
# except ``type``; ``journal`` is the key this codebase (and the frontend) reads,
# Crossref calls the same thing ``container-title``.
FIELD_TO_METADATA_KEY = {f: f for f in NORMALIZED_FIELDS}
FIELD_TO_METADATA_KEY["type"] = "publication_type"

# Provenance stamp: which service supplied the filled-in fields. Present only on
# documents enrichment actually changed, so its absence is meaningful.
ENRICHED_FROM_KEY = "doi_enriched_from"

# Upper bound on how long a Retry-After may park a request. Politeness must not
# turn into an unbounded ingest stall, so a longer Retry-After is treated as
# "come back later" — we give up on this DOI for this run instead of sleeping.
MAX_RETRY_AFTER_SECONDS = 30.0


def normalize_doi(raw: str) -> str:
    """Canonicalize a DOI string, or return ``""`` if it isn't one.

    Strips prose wrappers (``doi:``, ``https://doi.org/``), surrounding
    whitespace/angle-brackets, and trailing sentence punctuation, then validates
    the ``10.NNNN/suffix`` shape. Lowercased: DOIs are case-insensitive and both
    Crossref and DataCite key on the lowercase form, so this makes the cache key
    and the API path agree regardless of how the DOI was written in the PDF.
    """
    if not raw:
        return ""
    doi = _DOI_PREFIXES.sub("", str(raw)).strip().strip("<>")
    doi = _trim_trailing_punctuation(doi)
    if not _DOI_SHAPE.match(doi):
        return ""
    return doi.lower()


def _trim_trailing_punctuation(doi: str) -> str:
    """Drop sentence punctuation a prose match may have glued onto a DOI.

    Mirrors ``enrich._trim_text_doi``: ``.``/``,``/``;``/``"``/``'`` at the end
    are always prose; a trailing ``)`` is dropped only when *unbalanced*, since
    DOIs legitimately contain balanced parens (``10.1016/S0140-6736(98)01085-X``).
    """
    doi = doi.rstrip(".,;\"'")
    while doi.endswith(")") and doi.count(")") > doi.count("("):
        doi = doi[:-1].rstrip(".,;\"'")
    return doi


def scan_text_for_doi(text: str, limit: int = TEXT_SCAN_CHARS) -> str:
    """The document's own DOI from the leading ``limit`` characters, or ``""``.

    Two things make this more than "first regex match":

    *Window.* ``enrich.derive_doi``'s text leg looks at the first 4000 chars,
    which is right for the pre-extracted JSONL corpus but too narrow for a full
    PDF: on a real open-access article the front-page DOI footer routinely lands
    just past it (measured at char 4017 on one of the g1 corpus PDFs). The window
    here is wider, but still bounded to the article's front matter so it stops
    well short of the reference list, where every *other* paper's DOI lives.

    *Ranking.* Within that window the article's own DOI is typically repeated by
    the running header/footer on each page, while an incidentally-captured
    reference DOI appears once. So the most frequent candidate wins, with
    earliest position as the tie-break (which reduces to "first match" whenever
    the counts are flat). This is a heuristic — it is why enrichment never
    overwrites metadata that is already present.
    """
    counts: dict[str, int] = {}
    first_at: dict[str, int] = {}
    for match in _DOI_IN_TEXT.finditer(text[:limit]):
        doi = normalize_doi(match.group(0))
        if not doi:
            continue
        counts[doi] = counts.get(doi, 0) + 1
        first_at.setdefault(doi, match.start())
    if not counts:
        return ""
    return min(counts, key=lambda d: (-counts[d], first_at[d]))


def document_doi(
    doc: Document, profile: PublisherProfile | None = None
) -> tuple[str, str]:
    """Find ``doc``'s DOI, returning ``(doi, source)`` — source is one of
    ``metadata`` / ``filename`` / ``text`` / ``""``.

    The trustworthy legs — an explicit ``metadata["doi"]``, then the publisher
    profile's filename->DOI rule — are delegated to
    :func:`ragstack.ingestion.enrich.derive_doi` (called with empty text so only
    those two legs run), so DOI discovery stays in one place and honours the
    configured publisher profile. The *text* leg is handled here instead by
    :func:`scan_text_for_doi`, because full PDFs need a wider window and
    frequency ranking than the JSONL corpus ``derive_doi`` was tuned for;
    changing ``derive_doi``'s own window would alter existing JSONL ingest
    behaviour, which this feature must not do.

    The result is run through :func:`normalize_doi`, so a malformed or non-DOI
    value degrades to ``("", "")`` rather than producing a request we know will
    404.
    """
    path = doc.source or str(doc.metadata.get("source_path") or "") or str(
        doc.metadata.get("filename") or ""
    )
    meta_doi = str(doc.metadata.get("doi") or "")
    raw, source = derive_doi(path, "", meta_doi, profile=profile)
    doi = normalize_doi(raw)
    if doi:
        return doi, source
    text_doi = scan_text_for_doi(doc.content)
    return (text_doi, "text") if text_doi else ("", "")


# --------------------------------------------------------------------------- #
# Response mapping
# --------------------------------------------------------------------------- #

def _clean(value: Any) -> str:
    """Normalize a Crossref/DataCite string into displayable plain text.

    These strings become citation labels and ``/v1/query`` source titles, so
    three things are fixed here once instead of in every consumer — all three
    observed on real Crossref records during validation against the g1 corpus:

    1. *JATS inline markup.* Titles come back with literal tags:
       ``"<i>In Vitro</i> Activity of ..."``. Only a **whitelist** of known
       inline tags is stripped, so a title that legitimately contains ``<`` (a
       chemistry or maths expression) survives intact.
    2. *HTML entities.* ``European Journal of ... &amp; Infectious Diseases``.
       Decoded *after* tag stripping, so a ``&lt;`` in the source text can never
       be turned into a ``<`` and then eaten as if it were markup.
    3. *Wrapped whitespace.* Crossref pretty-prints long titles with embedded
       newlines and indentation; collapse runs of whitespace to single spaces.
    """
    if not isinstance(value, str):
        return ""
    text = _JATS_TAG.sub("", value)
    return " ".join(unescape(text).split())


def _first_str(value: Any) -> str:
    """Crossref returns most single-valued strings as a list of strings."""
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and _clean(item):
                return _clean(item)
    return ""


def _year_from_date_parts(value: Any) -> int | None:
    """Pull the year out of a Crossref ``{"date-parts": [[YYYY, MM, DD]]}``."""
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
        try:
            return int(parts[0][0])
        except (TypeError, ValueError):
            return None
    return None


def _crossref_authors(value: Any) -> list[str]:
    """``[{given, family}|{name}]`` -> ``["Given Family", ...]``, order preserved."""
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        given = _clean(entry.get("given"))
        family = _clean(entry.get("family"))
        name = " ".join(p for p in (given, family) if p) or _clean(entry.get("name"))
        if name:
            authors.append(name)
    return authors


def map_crossref(message: dict[str, Any]) -> dict[str, Any]:
    """Map a Crossref ``message`` object to the normalized vocabulary.

    Empty/absent fields are omitted entirely rather than emitted as ``""``/``[]``
    — :func:`merge_enrichment` only ever fills *missing* keys, so an omitted
    field and an empty one would behave identically, and omitting keeps the
    cached JSON small and readable.
    """
    year = _year_from_date_parts(message.get("issued"))
    if year is None:
        for key in ("published", "published-print", "published-online", "created"):
            year = _year_from_date_parts(message.get(key))
            if year is not None:
                break
    resolved: dict[str, Any] = {
        "title": _first_str(message.get("title")),
        "authors": _crossref_authors(message.get("author")),
        "journal": _first_str(message.get("container-title")),
        "year": year,
        "doi": normalize_doi(str(message.get("DOI") or "")),
        "publisher": _clean(message.get("publisher")),
        "type": _clean(message.get("type")),
        "url": _clean(message.get("URL")),
    }
    return _drop_empty(resolved)


def map_datacite(data: dict[str, Any]) -> dict[str, Any]:
    """Map a DataCite ``data`` object (``{"attributes": {...}}``) to the same
    normalized vocabulary as :func:`map_crossref`, so callers never have to know
    which service answered."""
    attrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else data
    if not isinstance(attrs, dict):
        return {}
    titles = attrs.get("titles")
    title = ""
    if isinstance(titles, list):
        for entry in titles:
            if isinstance(entry, dict) and _clean(entry.get("title")):
                title = _clean(entry["title"])
                break
    authors: list[str] = []
    creators = attrs.get("creators")
    if isinstance(creators, list):
        for entry in creators:
            if not isinstance(entry, dict):
                continue
            given = _clean(entry.get("givenName"))
            family = _clean(entry.get("familyName"))
            name = " ".join(p for p in (given, family) if p) or _clean(entry.get("name"))
            if name:
                authors.append(name)
    container = attrs.get("container")
    journal = ""
    if isinstance(container, dict):
        journal = _clean(container.get("title"))
    year = attrs.get("publicationYear")
    try:
        year_int = int(year) if year is not None else None
    except (TypeError, ValueError):
        year_int = None
    types = attrs.get("types")
    work_type = ""
    if isinstance(types, dict):
        work_type = _clean(
            types.get("resourceTypeGeneral") or types.get("resourceType")
        )
    resolved: dict[str, Any] = {
        "title": title,
        "authors": authors,
        "journal": journal,
        "year": year_int,
        "doi": normalize_doi(str(attrs.get("doi") or "")),
        "publisher": _clean(attrs.get("publisher")),
        "type": work_type,
        "url": _clean(attrs.get("url")),
    }
    return _drop_empty(resolved)


def _drop_empty(resolved: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in resolved.items() if v not in ("", [], None, {})}


# --------------------------------------------------------------------------- #
# Precedence
# --------------------------------------------------------------------------- #

def _is_missing(value: Any) -> bool:
    """True when ``value`` carries no information and may be filled in.

    Whitespace-only strings and empty collections count as missing — a PDF
    extractor that emitted ``title=" "`` has told us nothing, and treating that
    as "already populated" would permanently block enrichment for that document.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | tuple | dict | set):
        return len(value) == 0
    return False


def merge_enrichment(
    metadata: dict[str, Any], resolved: dict[str, Any], service: str = ""
) -> list[str]:
    """Fill gaps in ``metadata`` from ``resolved``; return the keys filled.

    **Precedence rule (the whole point of this function): existing explicit
    metadata wins, enrichment only fills gaps.** A key already present with a
    non-empty value is left exactly as it was, even when the remote record
    disagrees. Rationale: local metadata came from the artifact the operator
    actually ingested (or from the operator directly), and a remote lookup keyed
    on a *heuristically extracted* DOI can be wrong about which document it is —
    a mis-scanned DOI from a reference list would otherwise silently retitle the
    document. The failure mode of this rule is a stale-but-local title, which is
    visible and fixable; the failure mode of the opposite rule is invisible
    corruption of correct data.

    Mutates ``metadata`` in place (callers hold the live ``Document.metadata``)
    and stamps ``doi_enriched_from`` with ``service`` when anything was filled,
    so a chunk payload records where its bibliographic fields came from.
    """
    filled: list[str] = []
    for field, key in FIELD_TO_METADATA_KEY.items():
        value = resolved.get(field)
        if _is_missing(value):
            continue
        if not _is_missing(metadata.get(key)):
            continue  # existing explicit value wins — never overwritten
        metadata[key] = value
        filled.append(key)
    if filled and service:
        metadata[ENRICHED_FROM_KEY] = service
    return filled


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Resolution:
    """A successful DOI lookup: the normalized fields plus who supplied them."""

    fields: dict[str, Any]
    service: str


class DoiCache:
    """On-disk JSON cache of DOI resolutions, one file per DOI.

    Keyed by the normalized (lowercase) DOI, percent-encoded so the key is a
    safe, reversible single filename. **Negative results are cached too** (as
    ``null``): a 404 means "this DOI has no record", and re-asking on every
    re-ingest is exactly the impolite behaviour we are trying to avoid. Transient
    failures (timeout, 5xx, rate-limit) are deliberately *not* cached, so a
    network blip doesn't poison the corpus until someone clears the directory.

    An in-memory layer sits in front, so a DOI repeated within one ingest costs
    nothing and the cache still works (per-process) when no directory is
    configured. Every disk operation is defensive: an unreadable, corrupt, or
    unwritable cache degrades to a miss rather than raising into an ingest.

    What is stored is the *mapped* record, not the raw API response, so entries
    are small and readable. The cost is that a change to ``map_crossref`` /
    ``map_datacite`` / ``_clean`` would otherwise leave stale mappings cached
    forever; :data:`VERSION` guards against that — bump it whenever the mapping
    changes and every existing entry reads as a miss and is re-fetched once.
    """

    #: Returned by :meth:`get` for "not cached", distinct from a cached negative
    #: (``None``) — the two must not be confused or negatives would be re-fetched.
    MISS = object()

    #: Mapping-format version of a cache entry. Bump on any mapping change.
    #: 1 — initial. 2 — titles normalized (JATS tags stripped, entities decoded,
    #:     wrapped whitespace collapsed).
    VERSION = 2

    def __init__(self, directory: str | Path | None = None) -> None:
        self._dir = Path(directory) if directory else None
        self._memory: dict[str, Resolution | None] = {}

    def _path(self, doi: str) -> Path | None:
        if self._dir is None:
            return None
        return self._dir / f"{quote(doi, safe='')}.json"

    def get(self, doi: str) -> Resolution | None | Any:
        """``Resolution`` on a hit, ``None`` for a cached negative, ``MISS``
        when the DOI has never been looked up."""
        if doi in self._memory:
            return self._memory[doi]
        path = self._path(doi)
        if path is None:
            return self.MISS
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self.MISS
        if not isinstance(payload, dict) or "resolved" not in payload:
            return self.MISS
        if payload.get("version") != self.VERSION:
            # Written by an older mapping — re-fetch once rather than serve a
            # record this build would have mapped differently.
            return self.MISS
        raw = payload["resolved"]
        if raw is None:
            self._memory[doi] = None
            return None
        if not isinstance(raw, dict):
            return self.MISS
        entry = Resolution(fields=raw, service=str(payload.get("service") or ""))
        self._memory[doi] = entry
        return entry

    def put(self, doi: str, resolution: Resolution | None) -> None:
        """Record a hit (``Resolution``) or an authoritative miss (``None``)."""
        self._memory[doi] = resolution
        path = self._path(doi)
        if path is None or self._dir is None:
            return
        payload = {
            "version": self.VERSION,
            "doi": doi,
            "service": resolution.service if resolution else "",
            "resolved": resolution.fields if resolution else None,
        }
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            # Atomic replace: a crash mid-write must not leave a truncated JSON
            # file that every future run then has to treat as a miss.
            fd, tmp = tempfile.mkstemp(dir=str(self._dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                os.replace(tmp, path)
            except BaseException:
                with suppress(OSError):
                    os.unlink(tmp)
                raise
        except OSError as e:
            log.debug("doi cache write failed for %s: %s", doi, e)


# --------------------------------------------------------------------------- #
# Resolver
# --------------------------------------------------------------------------- #

class DoiMetadataResolver:
    """Resolve DOIs to normalized metadata via Crossref, then DataCite.

    Crossref covers journal articles (the case that motivated this); DataCite
    covers datasets, preprints and repository deposits, and is tried only when
    Crossref *authoritatively* has no record — never when Crossref merely failed
    transiently, since a second service can't fix a network problem and asking it
    anyway just doubles the load during an outage.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        mailto: str = "",
        user_agent: str = "",
        cache: DoiCache | None = None,
        timeout: float = 10.0,
        concurrency: int = 4,
        datacite_fallback: bool = True,
    ) -> None:
        self._client = client
        self._mailto = mailto.strip()
        self._user_agent = user_agent.strip() or default_user_agent(self._mailto)
        self._cache = cache if cache is not None else DoiCache()
        self._timeout = timeout
        # Bounded concurrency is the politeness contract: however many documents
        # a shard contains, at most this many requests are ever in flight.
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._datacite_fallback = datacite_fallback

    async def resolve(self, doi: str) -> Resolution | None:
        """Resolve one DOI. Returns ``None`` on any miss or failure; never raises."""
        doi = normalize_doi(doi)
        if not doi:
            return None
        cached = self._cache.get(doi)
        if cached is not DoiCache.MISS:
            return cached  # type: ignore[return-value]
        try:
            resolution, definitive = await self._fetch(doi)
        except Exception as e:  # defence in depth — _fetch already catches
            log.warning("doi enrichment failed for %s: %s", doi, e)
            return None
        if resolution is not None:
            self._cache.put(doi, resolution)
            return resolution
        if definitive:
            # A real "no such record" — cache the negative so re-ingest is free.
            self._cache.put(doi, None)
        return None

    async def resolve_many(self, dois: list[str]) -> dict[str, Resolution]:
        """Resolve many DOIs concurrently (bounded); unresolved ones are absent.

        Deduplicates first, so a corpus that repeats a DOI (a multi-part article,
        a re-ingest of overlapping shards) costs exactly one lookup.
        """
        unique = list(dict.fromkeys(d for d in (normalize_doi(x) for x in dois) if d))
        results = await asyncio.gather(
            *(self.resolve(d) for d in unique), return_exceptions=True
        )
        out: dict[str, Resolution] = {}
        for doi, result in zip(unique, results, strict=True):
            if isinstance(result, Resolution):
                out[doi] = result
            elif isinstance(result, BaseException):
                log.warning("doi enrichment failed for %s: %s", doi, result)
        return out

    async def _fetch(self, doi: str) -> tuple[Resolution | None, bool]:
        """``(resolution, definitive)`` — ``definitive`` is True only when a
        service authoritatively said the DOI has no record (the one outcome
        worth caching as a negative)."""
        fields, definitive = await self._fetch_crossref(doi)
        if fields is not None:
            return Resolution(fields, "crossref"), True
        if self._datacite_fallback and definitive:
            dc_fields, dc_definitive = await self._fetch_datacite(doi)
            if dc_fields is not None:
                return Resolution(dc_fields, "datacite"), True
            return None, dc_definitive
        return None, definitive

    async def _fetch_crossref(self, doi: str) -> tuple[dict[str, Any] | None, bool]:
        url = CROSSREF_URL.format(doi=quote(doi, safe="/"))
        params = {"mailto": self._mailto} if self._mailto else None
        payload, definitive = await self._get_json(url, params=params)
        if payload is None:
            return None, definitive
        message = payload.get("message")
        if not isinstance(message, dict):
            log.debug("crossref response for %s had no message object", doi)
            return None, False
        return (map_crossref(message) or None), definitive

    async def _fetch_datacite(self, doi: str) -> tuple[dict[str, Any] | None, bool]:
        url = DATACITE_URL.format(doi=quote(doi, safe="/"))
        payload, definitive = await self._get_json(url)
        if payload is None:
            return None, definitive
        data = payload.get("data")
        if not isinstance(data, dict):
            return None, False
        return (map_datacite(data) or None), definitive

    async def _get_json(
        self, url: str, params: dict[str, str] | None = None
    ) -> tuple[dict[str, Any] | None, bool]:
        """GET ``url`` and parse JSON. Returns ``(payload, definitive_miss)``.

        Every failure mode — connection error, timeout, non-2xx, non-JSON body,
        JSON that isn't an object — comes back as ``(None, ...)``. Nothing raises.
        A single retry is made when the server asks for one via ``Retry-After``
        on a 429/503, capped at :data:`MAX_RETRY_AFTER_SECONDS`; the semaphore is
        held across the wait, so a rate-limited request does not free a slot for
        another request to immediately re-hit the same throttled API.
        """
        headers = {"User-Agent": self._user_agent, "Accept": "application/json"}
        async with self._sem:
            for attempt in (0, 1):
                try:
                    response = await self._client.get(
                        url, params=params, headers=headers, timeout=self._timeout
                    )
                except Exception as e:
                    log.warning("doi enrichment request failed (%s): %s", url, e)
                    return None, False
                if response.status_code == 404:
                    return None, True  # authoritative: no such record
                if response.status_code in (429, 503) and attempt == 0:
                    delay = _retry_after_seconds(response.headers.get("Retry-After"))
                    if delay is not None and delay <= MAX_RETRY_AFTER_SECONDS:
                        log.info(
                            "doi enrichment rate-limited; honouring Retry-After=%ss",
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    log.warning(
                        "doi enrichment rate-limited (HTTP %s, Retry-After=%r); "
                        "skipping this lookup",
                        response.status_code,
                        response.headers.get("Retry-After"),
                    )
                    return None, False
                if response.status_code >= 400:
                    log.warning(
                        "doi enrichment got HTTP %s from %s", response.status_code, url
                    )
                    return None, False
                try:
                    payload = response.json()
                except Exception as e:
                    log.warning("doi enrichment got malformed JSON from %s: %s", url, e)
                    return None, False
                if not isinstance(payload, dict):
                    return None, False
                return payload, False
        return None, False


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a ``Retry-After`` delay-seconds value; ``None`` if unusable.

    Only the numeric form is honoured. The HTTP-date form is legal but rare from
    these APIs, and mis-parsing it would either stall the ingest or defeat the
    politeness contract — declining to guess is the safer default.
    """
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return max(0.0, seconds)


def default_user_agent(mailto: str = "") -> str:
    """A descriptive User-Agent, carrying a contact address when configured.

    Crossref routes requests with a contact into its "polite pool", which is both
    better-behaved for them and more reliable for us. Without a contact we still
    identify ourselves honestly rather than shipping httpx's default.
    """
    from ragstack.provenance import ragstack_version

    version = ragstack_version() or "dev"
    base = f"RAGStack/{version} (+https://github.com/wilke/ragstack)"
    return f"{base} mailto:{mailto}" if mailto else base


# --------------------------------------------------------------------------- #
# Document-level enrichment
# --------------------------------------------------------------------------- #

class DoiEnricher:
    """Applies DOI-resolved metadata to loaded :class:`Document` objects.

    Sits between load and chunk in
    :class:`~ragstack.ingestion.pipeline.IngestionPipeline`, so whatever it
    writes onto ``Document.metadata`` is copied verbatim onto every chunk
    (``chunkers._make_chunk`` does ``dict(doc.metadata)``) and therefore into the
    Qdrant payload, the Elasticsearch document, and ``/v1/query`` sources.

    Total by construction: :meth:`enrich_documents` catches everything and
    returns a count. An ingest can only ever be *not improved* by enrichment,
    never broken by it.
    """

    def __init__(
        self,
        resolver: DoiMetadataResolver,
        *,
        profile: PublisherProfile | None = None,
    ) -> None:
        self._resolver = resolver
        self._profile = profile

    async def enrich_documents(self, documents: list[Document]) -> int:
        """Enrich ``documents`` in place; return how many were changed.

        Best-effort in the strongest sense: any exception is logged and
        swallowed, because the caller is an ingest that must proceed.
        """
        try:
            return await self._enrich(documents)
        except Exception as e:
            log.warning("doi enrichment skipped (%s): %s", type(e).__name__, e)
            return 0

    async def _enrich(self, documents: list[Document]) -> int:
        # Pass 1: discover DOIs. Pure and local, so it also helps documents whose
        # DOI never resolves — recording the DOI alone already improves the UI's
        # label fallback and gives operators something to grep for.
        found: list[tuple[Document, str]] = []
        for doc in documents:
            doi, source = document_doi(doc, self._profile)
            if not doi:
                continue
            if _is_missing(doc.metadata.get("doi")):
                doc.metadata["doi"] = doi
                doc.metadata.setdefault("doi_source", source)
            found.append((doc, doi))
        if not found:
            return 0

        resolutions = await self._resolver.resolve_many([d for _, d in found])
        if not resolutions:
            return 0

        changed = 0
        for doc, doi in found:
            resolution = resolutions.get(doi)
            if resolution is None:
                continue
            filled = merge_enrichment(
                doc.metadata, resolution.fields, resolution.service
            )
            if filled:
                changed += 1
                log.debug("doi %s enriched %s: %s", doi, doc.id, ", ".join(filled))
        if changed:
            log.info(
                "doi enrichment filled metadata for %d of %d document(s)",
                changed,
                len(documents),
            )
        return changed


def build_resolver(
    client: httpx.AsyncClient,
    *,
    mailto: str = "",
    user_agent: str = "",
    cache_dir: str = "",
    timeout: float = 10.0,
    concurrency: int = 4,
    datacite_fallback: bool = True,
) -> DoiMetadataResolver:
    """Convenience constructor used by the API's dependency wiring."""
    return DoiMetadataResolver(
        client,
        mailto=mailto,
        user_agent=user_agent,
        cache=DoiCache(cache_dir or None),
        timeout=timeout,
        concurrency=concurrency,
        datacite_fallback=datacite_fallback,
    )
