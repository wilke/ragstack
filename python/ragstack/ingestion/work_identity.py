"""Work identity and the duplicate-paper guard at ingest (#747, R1 step 1).

The same paper reaches a collection more than once under different document
ids: a publisher PDF and a Europe PMC rendering, the same file uploaded twice
(each upload stages under a fresh job directory, so the path-keyed doc id
differs), a Workspace file ingested by reference twice. Every copy then takes
its own slots in the BM25 and vector top-k (#754 measured six copies of one
PNAS article in the top 21). The guard refuses the second copy at ingest by
its **work identity** — the DOI, PMCID or PMID it carries — instead of by its
path.

Where it runs. Inside ``IngestionPipeline.prepare_documents``: after the
loader and the DOI enricher have run (the ids are known) and before the first
chunk is built. That one point is on both enforcement paths, like the chunk cap
(``chunk_cap.py``, "Enforcement points"):

* the API/local path — ``ShardedIngestor.ingest_manifest`` →
  ``prepare_source``/``ingest`` (one source per item);
* the GoWe worker — ``ingestion.shard.run_shard`` → ``embed_documents``
  (``ingest_shard --duplicate-policy``, a batch of documents per task).

A best-effort pre-check on the file NAME also runs at admission in the API
(``api/routers/documents.py::_precheck_duplicate``) so the common case — a
``PMC1234567.pdf`` uploaded twice — is a 409 before a job exists.

Policies (:data:`POLICIES`): ``refuse`` drops the duplicate before chunking;
``allow`` keeps it and stamps ``duplicate_of=<existing doc_id>`` on every chunk
(a handle for work-level dedup at retrieval, #754); ``off`` does nothing — no
lookup, no id filling, byte-identical to before. The API defaults to
``refuse`` (``Settings.duplicate_policy``); the hand-run tools default to
``off``.

The lookup is **fail-open**: a text index that cannot answer (ES down) logs a
WARNING and admits. An index whose ``metadata.doi``/``pmcid`` is mapped
``text`` instead of ``keyword`` does NOT error — the term queries just match
nothing, so duplicates pass silently; ``ElasticsearchTextIndex`` logs a
one-time WARNING per index when it sees such a mapping, and
``scripts/metadata_conformance.py`` reports it. The guard is corpus hygiene,
not isolation.

Known limits (step 1): two CONCURRENT jobs ingesting the same new work can both
pass (each looks up before either has indexed; within ONE job the shared
``DuplicateCheck.seen`` closes that); an erratum or correction whose header
``DOI:`` names the article it corrects is treated as that article — the
edition/child model is R1's.

The normalisers here are pure and are meant to seed R1's work resolver. No
cross-key bridging in step 1: a new document carrying only a PMCID does not
match an existing one carrying only a DOI.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ragstack.documents import DocumentSummary
from ragstack.ingestion.doi_metadata import normalize_doi
from ragstack.ingestion.enrich import DEFAULT_PROFILE, PublisherProfile
from ragstack.ingestion.loaders import DEFAULT_INGEST_SUFFIXES
from ragstack.models import Document

log = logging.getLogger(__name__)

__all__ = [
    "DUPLICATE_OF",
    "DUPLICATE_WORK",
    "DuplicateCheck",
    "POLICIES",
    "POLICY_ALLOW",
    "POLICY_OFF",
    "POLICY_REFUSE",
    "DuplicateWork",
    "WorkIdentityStage",
    "format_refusal",
    "ids_from_filename",
    "ids_from_text",
    "is_duplicate_refusal",
    "normalize_doi",
    "normalize_pmcid",
    "normalize_pmid",
    "refusal_of",
    "work_keys",
]

#: The job-item / receipt error label. Every refusal string starts with it, so
#: a consumer recognises it by prefix (:func:`is_duplicate_refusal`) — the
#: ``chunk_cap_exceeded`` convention.
DUPLICATE_WORK = "duplicate_work"
#: The declared chunk-metadata field an ``allow``-admitted copy carries: the
#: doc_id of the copy that was already indexed.
DUPLICATE_OF = "duplicate_of"
#: Transient marker on a REFUSED document's metadata, holding its formatted
#: refusal. A refused document is never chunked, so this key never reaches a
#: store; it only lets the callers that hold the document list (``prepare_source``,
#: ``run_shard``) name the refusal without a second return channel.
_REFUSAL_KEY = "_duplicate_refusal"

POLICY_REFUSE = "refuse"
POLICY_ALLOW = "allow"
POLICY_OFF = "off"
POLICIES = (POLICY_REFUSE, POLICY_ALLOW, POLICY_OFF)

#: Key kinds, in the order a refusal names the matching one.
KEY_KINDS = ("doi", "pmcid", "pmid")

#: How far into a document's text the header scan looks for labelled ids. The
#: Europe PMC rendering puts ``PMCID: … · PMID: … · DOI: …`` in its first lines;
#: the window stops short of a reference list.
HEADER_SCAN_CHARS = 2000

# A PMCID token in a FILE NAME needs at least five digits. Real PMCIDs run from
# low numbers (old back-file scans) to ~8 digits today, but a 1–4 digit token in
# a name is far more often a year or a counter ("report-PMC2020.pdf") than an
# id; a short real PMCID is still read from labelled header text or metadata.
PMCID_NAME_MIN_DIGITS = 5
_PMC_IN_NAME = re.compile(
    rf"(?<![A-Za-z0-9])PMC(\d{{{PMCID_NAME_MIN_DIGITS},9}})(?![0-9A-Za-z])", re.IGNORECASE
)
_PMCID_PREFIX = re.compile(
    r"^\s*(?:pmcid\s*:?\s*|(?:https?://)?(?:www\.)?(?:ncbi\.nlm\.nih\.gov/pmc/articles/"
    r"|europepmc\.org/(?:article/PMC/|abstract/PMC/)?))",
    re.IGNORECASE,
)
_PMCID_SHAPE = re.compile(r"^(?:PMC)?(\d{1,9})(?:\.\d+)?/?$", re.IGNORECASE)
_PMID_PREFIX = re.compile(r"^\s*(?:pmid\s*:?\s*)", re.IGNORECASE)
_PMID_SHAPE = re.compile(r"^\d{1,9}$")
_PMCID_LABEL = re.compile(r"\bPMCID\s*:?\s*(PMC\d{1,9})\b", re.IGNORECASE)
_PMID_LABEL = re.compile(r"\bPMID\s*:?\s*(\d{1,9})\b", re.IGNORECASE)
# Label-anchored and line-wrap tolerant: "DOI: 10.1073/\npnas.2018995118".
_DOI_LABEL = re.compile(r"\bDOI\s*:?\s*(10\.\d{4,9}/)\s*([-._;()/:A-Za-z0-9]+)", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Pure normalisers
# --------------------------------------------------------------------------- #


def normalize_pmcid(raw: Any) -> str:
    """``"PMC" + digits`` or ``""``. Case-insensitive; strips a ``PMCID:``
    label, an NCBI/Europe PMC URL prefix and a ``.N`` version suffix. A bare
    number is accepted (``8092463`` → ``PMC8092463``) only from a string — an
    int from an untyped field is more likely a PMID."""
    if raw is None or isinstance(raw, bool) or not isinstance(raw, str):
        return ""
    m = _PMCID_SHAPE.match(_PMCID_PREFIX.sub("", raw).strip())
    return f"PMC{int(m.group(1))}" if m and int(m.group(1)) > 0 else ""


def normalize_pmid(raw: Any) -> str:
    """Digits (1–9, no leading zeros) or ``""``. Strips a ``PMID:`` label.
    Accepts an ``int`` (the NCBI ID Converter returns one, #594) but never a
    ``PMC…`` id."""
    if raw is None or isinstance(raw, bool):
        return ""
    if isinstance(raw, int):
        return str(raw) if raw > 0 else ""
    if not isinstance(raw, str):
        return ""
    value = _PMID_PREFIX.sub("", raw).strip()
    if not _PMID_SHAPE.match(value) or int(value) == 0:
        return ""
    return str(int(value))


def work_keys(metadata: dict[str, Any]) -> list[tuple[str, str]]:
    """The normalised identity keys a document's metadata carries, as
    ``[("doi"|"pmcid"|"pmid", value)]`` in :data:`KEY_KINDS` order. Empty when
    it carries none — such a document is never a duplicate in step 1."""
    keys: list[tuple[str, str]] = []
    doi = normalize_doi(str(metadata.get("doi") or ""))
    if doi:
        keys.append(("doi", doi))
    pmcid = normalize_pmcid(metadata.get("pmcid"))
    if pmcid:
        keys.append(("pmcid", pmcid))
    pmid = normalize_pmid(metadata.get("pmid"))
    if pmid:
        keys.append(("pmid", pmid))
    return keys


def ids_from_filename(name: str, profile: PublisherProfile | None = None) -> dict[str, str]:
    """Ids a file NAME carries: ``pmcid`` from a ``PMC<digits>`` token (at
    least :data:`PMCID_NAME_MIN_DIGITS` digits), ``doi`` from the publisher
    profile's filename rule — **for ``.pdf`` names only**, the case the rule
    was validated on (an ASM-style stem such as ``fig.1`` or ``notes.2021-draft``
    on a ``.txt``/``.md`` would otherwise become ``10.1128/fig.1``). The
    ingestable suffix is stripped before the rule runs. Pure; used by the
    admission pre-check (where only the name is known) and by the stage."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem, suffix = base, ""
    for sfx in DEFAULT_INGEST_SUFFIXES:
        if base.lower().endswith(sfx):
            stem, suffix = base[: -len(sfx)], sfx
            break
    out: dict[str, str] = {}
    m = _PMC_IN_NAME.search(stem)
    if m:
        pmcid = normalize_pmcid(f"PMC{m.group(1)}")
        if pmcid:
            out["pmcid"] = pmcid
    if stem and suffix == ".pdf":
        raw = (profile or DEFAULT_PROFILE).doi_from_filename(stem)
        doi = normalize_doi(raw) if raw else ""
        if doi:
            out["doi"] = doi
    return out


def ids_from_text(text: str, limit: int = HEADER_SCAN_CHARS) -> dict[str, str]:
    """LABELLED ids in the first ``limit`` characters — ``PMCID: PMC…``,
    ``PMID: …``, ``DOI: 10.x/…`` (the DOI tolerating a line break after the
    slash, as Europe PMC renders it). Only labelled values: an unlabelled DOI
    in the front matter may be a reference, and the enricher's frequency-ranked
    scan owns that case."""
    head = (text or "")[:limit]
    out: dict[str, str] = {}
    m = _PMCID_LABEL.search(head)
    if m and normalize_pmcid(m.group(1)):
        out["pmcid"] = normalize_pmcid(m.group(1))
    m = _PMID_LABEL.search(head)
    if m and normalize_pmid(m.group(1)):
        out["pmid"] = normalize_pmid(m.group(1))
    m = _DOI_LABEL.search(head)
    if m:
        doi = normalize_doi(m.group(1) + m.group(2))
        if doi:
            out["doi"] = doi
    return out


# --------------------------------------------------------------------------- #
# The refusal
# --------------------------------------------------------------------------- #


def format_refusal(key: tuple[str, str], existing_doc_id: str) -> str:
    """``duplicate_work: key=doi:10.x/y existing_doc_id=<id> (…)`` — the one
    wire form of a refusal (job-item ``error``, receipt row ``error``, log
    line). Starts with the label so it is recognised by prefix, and names the
    key that matched and the copy that is already indexed, so the reason is
    readable from the item alone."""
    kind, value = key
    return (
        f"{DUPLICATE_WORK}: key={kind}:{value} existing_doc_id={existing_doc_id} "
        f"(this work is already indexed in the collection; resubmit with "
        f"duplicate_policy=allow to keep a second copy)"
    )


def is_duplicate_refusal(error: str | None) -> bool:
    """Is ``error`` (an item / receipt error string) a duplicate-work refusal?"""
    return bool(error) and str(error).startswith(DUPLICATE_WORK)


def refusal_of(doc: Document) -> str:
    """The formatted refusal a guarded run left on ``doc``, or ``""``."""
    return str(doc.metadata.get(_REFUSAL_KEY) or "")


class DuplicateWork(RuntimeError):
    """Every document of a source was refused as an already-indexed work.

    Raised by ``IngestionPipeline.prepare_source`` (one source = one job item),
    before any chunk, embed or store write. ``job_error`` is the formatted
    refusal, which the ``getattr(e, "job_error", …)`` convention in
    ``sharded.py`` records as the item's error — the item names the key and
    the existing doc_id, not just a class name."""

    def __init__(self, refusal: str) -> None:
        super().__init__(refusal)
        self.job_error = refusal


# --------------------------------------------------------------------------- #
# The lookup seam and the stage
# --------------------------------------------------------------------------- #


class KeyLookup(Protocol):
    """The one store call the guard needs (``TextIndex.find_documents_by_key``)."""

    async def find_documents_by_key(
        self, keys: list[tuple[str, str]], tenants: list[str], *, limit: int = 20
    ) -> dict[tuple[str, str], list[DocumentSummary]]: ...


def match_summaries(
    keys: Iterable[tuple[str, str]], summaries: Iterable[DocumentSummary]
) -> dict[tuple[str, str], list[DocumentSummary]]:
    """Group ``summaries`` under the requested ``keys`` they carry, comparing
    NORMALISED keys (so a stored ``…/-/`` DOI matches the canonical one). The
    shared client-side half of every ``find_documents_by_key``."""
    wanted = set(keys)
    out: dict[tuple[str, str], list[DocumentSummary]] = {}
    for s in summaries:
        for k in work_keys(s.metadata):
            if k in wanted:
                out.setdefault(k, []).append(s)
    for hits in out.values():
        hits.sort(key=lambda s: s.doc_id)
    return out


@dataclass
class DuplicateCheck:
    """One job's duplicate-guard settings, threaded through the pipeline as
    ``duplicate_policy=`` (a bare policy string is accepted too).

    ``tenants`` is the lookup scope — the router resolves it once per request
    with ``api.scope.count_scope`` (own + public + a shared collection's owner
    tenant), so only documents the caller can already read are ever named;
    ``None`` = ``readable_tenants(tenant_id)`` (the worker, a hand run).
    ``seen`` maps a work key to the first doc_id ADMITTED under it in this job,
    shared across the job's items, so two copies inside one request are caught
    even when the second is prepared before the first is indexed (the chunk-cap
    sizing pass). Check-and-set on it has no ``await`` in between, so items
    running concurrently in one event loop cannot both pass."""

    policy: str = POLICY_OFF
    tenants: list[str] | None = None
    seen: dict[tuple[str, str], str] = field(default_factory=dict)

    @classmethod
    def coerce(cls, value: DuplicateCheck | str | None) -> DuplicateCheck:
        if isinstance(value, DuplicateCheck):
            return value
        return cls(policy=value or POLICY_OFF)


@dataclass
class _Match:
    key: tuple[str, str]
    existing_doc_id: str


class WorkIdentityStage:
    """The guard's two steps, run by ``IngestionPipeline.prepare_documents``:
    :meth:`fill_ids` before DOI enrichment, :meth:`admit` after it."""

    def __init__(self, text_index: Any, profile: PublisherProfile | None = None) -> None:
        self._text_index = text_index
        self._profile = profile

    def fill_ids(self, documents: list[Document]) -> None:
        """Fill MISSING ``pmcid``/``pmid``/``doi`` from labelled header text
        and a ``PMC…`` file name. Never overwrites a present value (the
        ``merge_enrichment`` precedence). Runs before enrichment so a recovered
        id can be resolved further (a header DOI becomes a Crossref title).

        A file-name DOI is deliberately NOT written here: it would pre-empt the
        enricher's frequency-ranked text scan (``document_doi`` takes a present
        ``doi`` as authoritative). The enricher applies the same filename rule
        itself; the guard uses it only as a lookup key (:meth:`keys_for`)."""
        for doc in documents:
            from_name = ids_from_filename(_name_of(doc), self._profile)
            found = {**ids_from_text(doc.content)}
            if "pmcid" in from_name:
                found.setdefault("pmcid", from_name["pmcid"])
            for kind in KEY_KINDS:
                value = found.get(kind)
                if value and _is_missing(doc.metadata.get(kind)):
                    doc.metadata[kind] = value
                    if kind == "doi":
                        doc.metadata.setdefault("doi_source", "text")

    def keys_for(self, doc: Document) -> list[tuple[str, str]]:
        """The lookup keys of ``doc``: its metadata ids, plus the ``.pdf``
        file-name DOI when the metadata carries no DOI at all."""
        keys = work_keys(doc.metadata)
        if not any(k == "doi" for k, _ in keys):
            fn_doi = ids_from_filename(_name_of(doc), self._profile).get("doi")
            if fn_doi:
                keys.insert(0, ("doi", fn_doi))
        return keys

    async def admit(
        self,
        documents: list[Document],
        *,
        tenants: list[str],
        policy: str,
        source: str = "",
        seen: dict[tuple[str, str], str] | None = None,
    ) -> list[Document]:
        """Return the documents to chunk. ``refuse``: a document whose work is
        already indexed (or appeared earlier in this same call) is left out and
        marked with its refusal (:func:`refusal_of`); ``allow``: it is kept and
        stamped ``duplicate_of``; ``off``: everything, untouched.

        A re-ingest of the SAME document (``existing.doc_id == doc.id``) is a
        replace, not a duplicate. ONE batched lookup per call; a lookup failure
        admits everything with a WARNING (fail-open). ``seen`` (shared across a
        job's items, :class:`DuplicateCheck`) records each ADMITTED document's
        keys, so a later copy in the same job is refused against it."""
        if policy == POLICY_OFF or not documents:
            return list(documents)
        if policy not in POLICIES:
            raise ValueError(f"duplicate_policy {policy!r} not in {POLICIES}")
        per_doc = [(doc, self.keys_for(doc)) for doc in documents]
        all_keys = list(dict.fromkeys(k for _, keys in per_doc for k in keys))
        if not all_keys:
            return list(documents)
        found: dict[tuple[str, str], list[DocumentSummary]] = {}
        try:
            found = await self._text_index.find_documents_by_key(all_keys, tenants)
        except Exception as e:  # noqa: BLE001 — fail-open: hygiene, not isolation
            log.warning(
                "duplicate guard: lookup failed for %r (%s: %s); admitting without "
                "the check", source, type(e).__name__, e,
            )
            found = {}
        seen_here = seen if seen is not None else {}
        admitted: list[Document] = []
        # No await from here on: check-and-set on ``seen`` is atomic in the loop.
        for doc, keys in per_doc:
            match = _first_match(doc, keys, found, seen_here)
            if match is None or policy == POLICY_ALLOW:
                for k in keys:
                    seen_here.setdefault(k, doc.id)
            if match is None:
                admitted.append(doc)
                continue
            refusal = format_refusal(match.key, match.existing_doc_id)
            doc.metadata[DUPLICATE_OF] = match.existing_doc_id
            if policy == POLICY_ALLOW:
                log.info("ingest %r: admitting %s as a duplicate of %s (%s:%s, "
                         "duplicate_policy=allow)", source, doc.id,
                         match.existing_doc_id, *match.key)
                admitted.append(doc)
            else:
                doc.metadata[_REFUSAL_KEY] = refusal
                log.info("ingest %r: refused %s — %s", source, doc.id, refusal)
        return admitted


def _first_match(
    doc: Document,
    keys: list[tuple[str, str]],
    found: dict[tuple[str, str], list[DocumentSummary]],
    seen_here: dict[tuple[str, str], str],
) -> _Match | None:
    for k in keys:  # KEY_KINDS order: a DOI match is named before a PMCID one
        for s in found.get(k, []):
            if s.doc_id != doc.id:
                return _Match(k, s.doc_id)
        other = seen_here.get(k)
        if other is not None and other != doc.id:
            return _Match(k, other)
    return None


def _name_of(doc: Document) -> str:
    return (
        doc.source
        or str(doc.metadata.get("source_path") or "")
        or str(doc.metadata.get("filename") or "")
    )


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []
