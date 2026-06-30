"""Scholarly-metadata enrichment for pre-extracted JSONL corpora.

The input corpus ships one JSON object per line — ``{"text", "path", "metadata"}``
— where ``metadata`` carries OCR/PDF-extracted fields (``title``, ``authors``,
``doi``, ``keywords``, ``abstract``, ``creationdate``, ``first_page``, …). In
practice those fields are sparse: ``doi`` and ``abstract`` are *never* populated
and ``authors``/``title`` only sometimes. This module recovers the scholarly
metadata that retrieval and the (future) knowledge-graph leg care about — DOI,
title, authors, citations, year, document class — from the signals that *are*
present: the source path (the filename is the DOI suffix for this publisher),
the body text, and whatever metadata did survive extraction.

Everything here is pure (no I/O, no network) so it is cheap to unit-test and
reusable from both the :class:`~ragstack.ingestion.loaders.JsonlLoader` (which
keeps only the *index-safe* subset, see :func:`index_metadata`) and the bulk
operator script (which also emits the full catalog, citations included).
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# --- document classes -------------------------------------------------------
# Tagged onto every chunk as ``doc_type`` so retrieval can filter (e.g. exclude
# front-matter/supplements) without re-deriving it.
ARTICLE = "article"
SUPPLEMENT = "supplement"
FRONT_MATTER = "front-matter"
SHORT = "short"
EMPTY = "empty"

# Non-article PDFs that recur across issues (mastheads, ads, ToCs, …). Matched
# on the bare filename, case-insensitively. This is the ASM/default front-matter
# set; a different publisher can supply its own via a PublisherProfile.
_FRONT_MATTER_NAMES = frozenset({
    "admin.pdf", "cover.pdf", "advertising.pdf", "masthead.pdf",
    "editorial-board.pdf", "table-of-contents.pdf", "reviewer-comments.pdf",
    "front-matter.pdf", "back-matter.pdf", "index.pdf", "errata.pdf",
})

# This publisher (ASM) names the per-article PDF after the DOI suffix, e.g.
# ``jvi.02415-06.pdf`` -> 10.1128/jvi.02415-06 and the older volume-style
# ``iai.70.9.4833-4840.2002.pdf`` -> 10.1128/iai.70.9.4833-4840.2002.
_DEFAULT_DOI_PREFIX = "10.1128"
_FN_DOI = re.compile(r"^([a-z]{2,6}\.[0-9][-.0-9a-z]+)$", re.IGNORECASE)
_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
# Year encoded in the issue directory (``jvi.1972.9.issue-2``) or a volume DOI.
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
# In free text a bare 4-digit number is unreliable (measurements, counts, ports),
# so the text fallback requires a publication-context anchor near the year.
_YEAR_IN_TEXT = re.compile(
    r"(?:©|\(c\)|copyright|received|accepted|revised|published).{0,40}?(19[5-9]\d|20[0-4]\d)",
    re.IGNORECASE,
)

# Citation block: a "LITERATURE CITED" / "REFERENCES" header on its own line
# (MULTILINE so it also matches at the very start of the text), then numbered or
# author-year entries beneath it.
_LIT_HEADER = re.compile(
    r"^[ \t]*(LITERATURE CITED|REFERENCES|Literature Cited|References|BIBLIOGRAPHY)[ \t]*$",
    re.MULTILINE,
)
_CITE_LINE = re.compile(r"^\s*(\d{1,3})[.)]\s+(\S.{8,})")

_SHORT_TEXT_THRESHOLD = 1500


class PublisherProfile(BaseModel):
    """Publisher-specific knobs that drive DOI recovery and classification.

    Everything in here is *publisher* (not corpus) specific: how the DOI prefix
    looks, how a per-article filename maps to its DOI suffix, and which bare
    filenames are recurring front-matter. The rest of enrichment (year, citation,
    text-DOI fallback, author/keyword parsing) is publisher-agnostic and lives in
    the module-level functions.

    ``filename_doi_rule`` is a compiled regex applied to the *stem* (the filename
    with any ``.pdf`` suffix stripped); when it matches, the DOI is
    ``f"{doi_prefix}/{group(1) or group(0)}"``. This generalises the ASM rule
    (``jvi.02415-06`` -> ``10.1128/jvi.02415-06``) to other publishers that name
    files after their DOI suffix without baking ASM specifics into the code.

    Frozen so a single shared instance (e.g. the module ``DEFAULT_PROFILE`` or a
    config-selected one) is safe to reuse across calls without aliasing hazards.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str = "asm"
    doi_prefix: str = _DEFAULT_DOI_PREFIX
    filename_doi_rule: re.Pattern[str] = _FN_DOI
    front_matter_names: frozenset[str] = _FRONT_MATTER_NAMES

    def doi_from_filename(self, stem: str) -> str | None:
        """Return the DOI for ``stem`` if the filename rule matches, else None.

        ``stem`` is the filename with any ``.pdf`` suffix already stripped. The
        rule's first capture group (or the whole match when it has none) is the
        DOI suffix appended to ``doi_prefix``.
        """
        m = self.filename_doi_rule.match(stem)
        if not m:
            return None
        suffix = m.group(1) if m.groups() else m.group(0)
        return f"{self.doi_prefix}/{suffix}"


# The default (ASM) profile: 10.1128 prefix, the ``jvi.02415-06`` filename rule,
# and the front-matter set above. Selecting nothing keeps behaviour byte-for-byte
# identical to before profiles existed.
DEFAULT_PROFILE = PublisherProfile()

# Registry of named, built-in publisher profiles selectable via config
# (``Settings.publisher_profile``). Keyed by lowercase name. Today only ASM ships
# built in; add more here (or, longer term, load from a config file) as new
# corpora arrive.
PROFILES: dict[str, PublisherProfile] = {DEFAULT_PROFILE.name: DEFAULT_PROFILE}


def resolve_profile(name: str | None) -> PublisherProfile:
    """Look up a built-in profile by name, degrading to the default.

    Unknown / empty names fall back to :data:`DEFAULT_PROFILE` rather than raising
    — enrichment must never hard-fail an ingest over a misconfigured profile name
    (graceful degradation); the worst case is ASM defaults on a non-ASM corpus,
    recoverable by re-ingesting once the name is fixed.
    """
    if not name:
        return DEFAULT_PROFILE
    return PROFILES.get(name.strip().lower(), DEFAULT_PROFILE)


def parse_authors(raw: str) -> list[str]:
    """Split the extractor's ``;``-separated author string into a clean list."""
    if not raw:
        return []
    parts = re.split(r"[;\n]", raw)
    return [a.strip() for a in parts if a.strip()]


def split_keywords(raw: str) -> list[str]:
    """Split a keywords string on the usual separators (``;`` / ``,``)."""
    if not raw:
        return []
    parts = re.split(r"[;,\n]", raw)
    return [k.strip() for k in parts if k.strip()]


def classify(path: str, text: str, profile: PublisherProfile | None = None) -> str:
    """Classify a record as article / supplement / front-matter / short / empty.

    ``profile`` supplies the publisher-specific front-matter name set; it
    defaults to the ASM :data:`DEFAULT_PROFILE`.
    """
    profile = profile or DEFAULT_PROFILE
    base = path.rsplit("/", 1)[-1].lower()
    if not text or not text.strip():
        return EMPTY
    if "/suppl/" in path.lower() or base.startswith("suppl"):
        return SUPPLEMENT
    if base in profile.front_matter_names:
        return FRONT_MATTER
    if len(text) < _SHORT_TEXT_THRESHOLD:
        return SHORT
    return ARTICLE


def derive_doi(
    path: str,
    text: str,
    meta_doi: str = "",
    prefix: str | None = None,
    profile: PublisherProfile | None = None,
) -> tuple[str, str]:
    """Recover the DOI, preferring the most trustworthy source.

    Returns ``(doi, source)`` where ``source`` is one of ``metadata`` /
    ``filename`` / ``text`` / ``""`` (not found). The filename rule and DOI prefix
    come from ``profile`` (default ASM): exact for that publisher and validated
    against the in-text DOI across the corpus; the text scan is a publisher-
    agnostic fallback for the volume-style names the filename rule misses.

    ``prefix`` is a back-compat shortcut to override just the profile's DOI prefix
    (e.g. ``derive_doi(..., prefix="10.1099")``); pass a full ``profile`` to also
    swap the filename rule.
    """
    profile = profile or DEFAULT_PROFILE
    if prefix is not None and prefix != profile.doi_prefix:
        profile = profile.model_copy(update={"doi_prefix": prefix})
    if meta_doi and meta_doi.strip():
        return meta_doi.strip(), "metadata"
    stem = path.rsplit("/", 1)[-1]
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    fn_doi = profile.doi_from_filename(stem)
    if fn_doi is not None:
        return fn_doi, "filename"
    m = _DOI_IN_TEXT.search(text[:4000])
    if m:
        return _trim_text_doi(m.group(0)), "text"
    return "", ""


def _trim_text_doi(doi: str) -> str:
    """Strip sentence punctuation a prose match may have appended to a DOI.

    ``.``/``,``/``;`` at the end are always sentence punctuation. A trailing
    ``)`` is stripped only when it is *unbalanced* — i.e. it closes a paren from
    the surrounding prose rather than one inside the DOI. DOIs can legitimately
    contain balanced parens, e.g. ``10.1016/S0140-6736(98)01085-X``; the old
    "contains no ``(``" check wrongly kept the stray ``)`` when such a DOI was
    itself wrapped in prose parens, so count parens instead."""
    doi = doi.rstrip(".,;")
    while doi.endswith(")") and doi.count(")") > doi.count("("):
        doi = doi[:-1].rstrip(".,;")
    return doi


def derive_year(path: str, doi: str, text: str) -> int | None:
    """Best-effort publication year from the issue dir / DOI / leading text.

    Path and DOI are structured, trustworthy sources and are scanned for any
    in-range year. Free text is not: a bare 4-digit number there is as likely to
    be a measurement or count as a year, so the text fallback only fires on a
    year next to a publication-context word (copyright/received/accepted/…)."""
    for hay in (path, doi):
        m = _YEAR.search(hay)
        if m:
            return int(m.group(1))
    m = _YEAR_IN_TEXT.search(text[:4000])
    if m:
        return int(m.group(1))
    return None


def extract_citations(text: str, cap: int = 250) -> list[str]:
    """Extract reference-list entries from a ``LITERATURE CITED`` / ``REFERENCES``
    section. Numbered entries are coalesced across wrapped lines; returns ``[]``
    when no recognizable reference section is present."""
    m = _LIT_HEADER.search(text)
    if not m:
        return []
    cites: list[str] = []
    cur: list[str] = []
    last_num = 0
    for line in text[m.end():].splitlines():
        cm = _CITE_LINE.match(line)
        if cm:
            n = int(cm.group(1))
            if cur:
                cites.append(" ".join(cur).strip())
                cur = []
            # A reset to the *start* of a new numbered list (1 or 2) after we
            # already have several entries signals we've run past the reference
            # list (e.g. into a figure list). Requiring n<=2 — rather than any
            # backwards step — avoids falsely truncating a mis-ordered multi-
            # column extraction that interleaves high/low numbers (1,26,2,27,…),
            # where a bare "n < last_num - 5" would cut the list off early.
            if n <= 2 and n < last_num - 5 and len(cites) > 3:
                break
            last_num = n
            cur = [cm.group(2).strip()]
        elif cur and line.strip():
            cur.append(line.strip())
        if len(cites) >= cap:
            break
    if cur and len(cites) < cap:
        cites.append(" ".join(cur).strip())
    return cites


class EnrichedDoc(BaseModel):
    """The full enriched, document-level metadata for one corpus record.

    The lightweight, filter-friendly subset (everything except ``citations`` and
    ``abstract``) is what :func:`index_metadata` propagates onto every chunk;
    the heavy fields stay here for the document-level metadata catalog so the
    per-chunk payloads in Qdrant/ES don't carry a duplicated reference list.
    """

    source_path: str
    filename: str
    doc_type: str
    doi: str = ""
    doi_source: str = ""
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    n_citations: int = 0
    citations: list[str] = Field(default_factory=list)


# Fields that should NOT be copied onto every chunk (they're large and/or
# document-level only). Everything else in EnrichedDoc rides on the chunk.
_HEAVY_FIELDS = {"citations", "abstract"}


def enrich(
    record: dict[str, Any],
    *,
    prefix: str | None = None,
    profile: PublisherProfile | None = None,
) -> EnrichedDoc:
    """Turn a raw JSONL record (``{text, path, metadata}``) into an EnrichedDoc.

    Pure and total: it always returns a record (callers decide whether to skip
    based on ``doc_type``); empty-text records come back tagged ``EMPTY``.

    ``profile`` injects the publisher specifics (DOI prefix, filename->DOI rule,
    front-matter names); it defaults to the ASM :data:`DEFAULT_PROFILE`. ``prefix``
    is a back-compat shortcut to override just the profile's DOI prefix.
    """
    profile = profile or DEFAULT_PROFILE
    if prefix is not None and prefix != profile.doi_prefix:
        profile = profile.model_copy(update={"doi_prefix": prefix})
    path = record.get("path", "") or ""
    text = record.get("text", "") or ""
    meta = record.get("metadata") or {}

    doc_type = classify(path, text, profile)
    doi, doi_source = derive_doi(path, text, meta.get("doi", ""), profile=profile)
    citations = extract_citations(text) if doc_type == ARTICLE else []

    return EnrichedDoc(
        source_path=path,
        filename=path.rsplit("/", 1)[-1],
        doc_type=doc_type,
        doi=doi,
        doi_source=doi_source,
        title=(meta.get("title") or "").strip(),
        authors=parse_authors(meta.get("authors", "")),
        keywords=split_keywords(meta.get("keywords", "")),
        year=derive_year(path, doi, text),
        abstract=(meta.get("abstract") or "").strip(),
        n_citations=len(citations),
        citations=citations,
    )


def index_metadata(doc: EnrichedDoc) -> dict[str, Any]:
    """The chunk-safe metadata subset to stamp on every chunk of ``doc``.

    Excludes the heavy document-level fields (citations, abstract) so per-chunk
    payloads stay small, but keeps ``n_citations`` so a chunk still advertises
    how richly cited its source is.
    """
    data = doc.model_dump(exclude=_HEAVY_FIELDS)
    # Drop empty values so chunk payloads (and the ES keyword index) aren't
    # littered with "" / [] / None for fields this record never had.
    return {k: v for k, v in data.items() if v not in ("", [], None)}
