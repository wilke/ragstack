"""Chunk-level boilerplate detection for scholarly PDFs.

:mod:`ragstack.ingestion.enrich` already classifies a whole *document*
(``doc_type`` = article / front-matter / supplement / …). That is the wrong
granularity for the failure this module exists to fix: a perfectly good research
article is one ``article`` document, but 10-20% of its chunks are not content at
all — the licence footer, the ``© The Author(s)`` line, the acknowledgements /
funding / competing-interests block, and above all the reference list.

Those chunks are lexically "about" everything (a bibliography names every topic
in the field) and semantically about nothing, so they score weakly against *any*
query and float to the top whenever no chunk scores strongly. Observed live: a
"What is the role of bees?" query returned a Creative Commons footer, a
copyright line, and two reference-list entries in its top 5 — and the answering
LLM then cited a paper it had only seen *inside* a retrieved bibliography.

This module is the chunk-level counterpart to ``enrich.classify``: same idea
(tag the text, let the caller decide), one level down. It is pure — no I/O, no
network, no model — so it is cheap to unit-test and safe to run both at ingest
(to flag/drop) and at query time (to demote already-indexed chunks).

Design constraints, in priority order:

1. **Dropping real content is far worse than keeping a boilerplate chunk.**
   Every rule below is anchored on structure (a section header at the start of
   the chunk, a run of numbered citation entries) or on *multiple* independent
   markers — never on a single incidental phrase. A methods paragraph that
   happens to say "funding" or cites "(Aizen et al., 2019)" must not trip.
2. **Tunable, not magic.** Every threshold is a field on :class:`BoilerplateConfig`
   with the calibrated default stated next to it, so an operator can loosen or
   tighten per corpus without editing code.
3. **Explainable.** :func:`classify_chunk` returns *why* it fired
   (:class:`BoilerplateVerdict`), so a drop can be logged and counted rather
   than happening silently — the known defect of ``scripts/ingest_jsonl.py``'s
   ``_kept()``, which we deliberately do not reproduce.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ragstack.models import Chunk

log = logging.getLogger(__name__)

# --- chunk metadata keys ----------------------------------------------------
#: The section a chunk belongs to, e.g. ``"references"``. Only written when the
#: chunk is NOT body text, so a body chunk's payload is byte-for-byte unchanged.
SECTION_KEY = "section"
#: Boolean convenience flag mirroring ``section in BOILERPLATE_SECTIONS``. Kept
#: as its own key so a store-side filter is a single equality term rather than a
#: set membership over section labels.
BOILERPLATE_KEY = "is_boilerplate"

# --- section labels ---------------------------------------------------------
# Stamped onto a chunk as ``metadata["section"]``. ``BODY`` is the non-verdict
# (real content) and is never written to metadata — absence means body.
BODY = "body"
REFERENCES = "references"
LICENSE = "license"
ACKNOWLEDGEMENTS = "acknowledgements"

#: The labels that mean "not content". Anything here is boilerplate.
BOILERPLATE_SECTIONS = frozenset({REFERENCES, LICENSE, ACKNOWLEDGEMENTS})


# --- licence / copyright markers --------------------------------------------
# Each entry is one *independent* piece of evidence. Two distinct markers (or one
# in a chunk that is nothing but the footer) is the bar — a body paragraph that
# mentions a licence once in passing stays content.
_LICENSE_MARKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"creativecommons\.org/licenses",
        r"creative\s+commons\s+(?:attribution|licen[cs]e|public\s+domain)",
        r"\bCC[- ]BY(?:[- ](?:NC|ND|SA))*\b",
        r"©\s*(?:the\s+)?author\(?s\)?",
        r"©\s*\d{4}\s+(?:the\s+)?(?:author|elsevier|springer|wiley|oxford|the\s+american)",
        r"\ball\s+rights\s+reserved\b",
        r"\bthis\s+(?:article|work)\s+is\s+(?:licen[cs]ed|distributed|an\s+open[- ]access)",
        r"\breprints?\s+and\s+permissions?\b",
        r"\bobtain\s+permission\s+directly\s+from\s+the\s+copyright\s+holder\b",
        r"\bpermitted\s+by\s+statutory\s+regulation\b",
        r"\bopen\s+access\s+(?:article|funding|this\s+article)\b",
        r"\bunrestricted\s+use,\s+distribution",
        r"\bprovided\s+you\s+give\s+appropriate\s+credit\b",
        r"\bno\s+modifications?\s+or\s+adaptations?\s+are\s+made\b",
        r"\bnot\s+included\s+in\s+the\s+article'?s?\s+creative\s+commons\b",
        r"\bpublic\s+domain\s+dedication\s+waiver\b",
        r"\bthe\s+images\s+or\s+other\s+third\s+party\s+material\b",
    )
)

# --- acknowledgement-family section headers ---------------------------------
# Header-anchored on purpose: the phrase must open the chunk (or start its own
# line near the top), which is what distinguishes "this chunk IS the funding
# block" from "this methods paragraph mentions funding".
_ACK_HEADERS = re.compile(
    r"(?:^|\n)[\s\d.·•*]{0,6}"
    r"(?:acknowledge?ments?"
    r"|acknowledgements?\s+and\s+funding"
    r"|funding(?:\s+(?:information|statement|sources?|support))?"
    r"|financial\s+(?:support|disclosure)"
    r"|author\s+(?:contributions?|information|details)"
    r"|contributions?\s+of\s+authors"
    r"|(?:competing|conflicts?\s+of)\s+interests?\s*(?:statement)?"
    r"|declarations?\s+of\s+(?:competing\s+)?interests?"
    r"|conflict\s+of\s+interest\s*(?:statement)?"
    r"|data\s+(?:and\s+code\s+)?availability(?:\s+statement)?"
    r"|availability\s+of\s+data(?:\s+and\s+materials?)?"
    r"|ethics?\s+(?:approval|statement|declarations?)"
    r"|consent\s+for\s+publication"
    r"|supplementary\s+(?:information|material|data)"
    r"|additional\s+information"
    r"|abbreviations"
    r"|orcid(?:\s+i\.?d\.?s?)?"
    r"|publisher'?s?\s+note"
    r"|declarations?)"
    r"\s*[:.\n]",
    re.IGNORECASE,
)

# --- reference-list signals -------------------------------------------------
# A reference list is dense in *bibliographic* tokens and thin in prose. None of
# these alone is evidence; their combined density per 100 words is.
_REF_HEADER = re.compile(
    r"(?:^|\n)[\s\d.]{0,4}"
    r"(?:references?|literature\s+cited|bibliography|works\s+cited|reference\s+list)"
    r"\s*[:.]?\s*(?:\n|$)",
    re.IGNORECASE,
)
# "1. " / "12) " / "[3] " at the start of a line — a numbered citation entry.
_NUMBERED_ENTRY = re.compile(r"(?m)^[ \t]*(?:\[\d{1,3}\]|\d{1,3}[.)])[ \t]+\S")
# A year, optionally with the a/b/c disambiguator bibliographies use.
_YEAR_TOKEN = re.compile(r"\b(?:1[89]\d{2}|20[0-4]\d)[a-z]?\b")
# Author initials in either order: "Aizen MA," / "Garibaldi LA" / "M. A. Aizen".
_INITIALS = re.compile(
    r"\b[A-Z][a-z]{1,20},?\s+(?:[A-Z]\.?){1,4}\b"
    r"|\b(?:[A-Z]\.\s*){1,3}[A-Z][a-z]{1,20}\b"
)
_DOI_TOKEN = re.compile(r"\b(?:doi:|https?://doi\.org/|10\.\d{4,9}/)", re.IGNORECASE)
# Journal volume(issue):pages — "35(2):119-128" / "12:1-9" / "9, 4833-4840".
_VOL_PAGES = re.compile(r"\b\d{1,4}\s*(?:\(\d{1,4}\))?\s*[:,]\s*\d{1,6}\s*[-–]\s*\d{1,6}\b")
_ET_AL = re.compile(r"\bet\s+al\b", re.IGNORECASE)
# Bibliography-only publication furniture.
_BIB_WORDS = re.compile(
    r"\b(?:pp\.|eds?\.|vol\.|no\.|in\s+press|PubMed|PMID|PMCID|arXiv|bioRxiv|medRxiv"
    r"|Proc\.|J\.|Nat\.|Sci\.|Am\.|Biol\.|Ecol\.|Environ\.|Univ\.\s+Press)\b"
)

# --- prose gate -------------------------------------------------------------
# A reference list is a list of *names, titles, journals and numbers*; it is
# almost devoid of the function words that hold sentences together. Measuring
# that directly is a far sharper discriminator than bibliographic density alone:
# on the g1-corpus sample the two populations do not overlap — every chunk with
# density >= 20 (unambiguous bibliography) had a function-word ratio <= 0.224,
# while the 10th percentile of unambiguous prose (density < 5) was 0.208.
# It is what stops a heavily-cited review paragraph — "(Doi, 2019; Doi and
# Paterson, 2015; Reyes et al., 2019)" — from being mistaken for a bibliography.
_FUNCTION_WORDS = frozenset(
    """the of and in to a is was were that for with as are on by we this be an from
    at have has been not or which it its their our can may these those than then
    when but if there also into during between such more most both each other""".split()
)
_WORD = re.compile(r"[a-z']+")


def function_word_ratio(text: str) -> float:
    """Fraction of alphabetic tokens that are common English function words.

    High (~0.30) for prose, low (~0.12) for a reference list. Public alongside
    :func:`reference_signal_density` because it is the second number an operator
    calibrates ``reference_max_prose_ratio`` against on their own corpus.
    """
    words = _WORD.findall(text.lower())
    if not words:
        return 0.0
    return sum(1 for w in words if w in _FUNCTION_WORDS) / len(words)


@dataclass(frozen=True)
class BoilerplateConfig:
    """Tunable thresholds for :func:`classify_chunk`.

    Defaults were calibrated on a 3.1k-chunk sample of the open-access
    ``g1-corpus`` PDFs (60 papers, 1200-char windows): they flag the licence /
    acknowledgement / reference chunks that motivated this module while leaving
    every hand-checked body paragraph unflagged. They are deliberately on the
    *conservative* side of the observed separation — the reference-density
    distribution is bimodal with a wide gap, so the threshold sits nearer the
    boilerplate end of that gap than the prose end.
    """

    #: Minimum number of *distinct* licence markers to call a chunk a licence
    #: footer. Two, because a single "open access" or "CC BY" mention is common
    #: in body text (e.g. a data-availability sentence) while real footers stack
    #: several. See ``license_short_chars`` for the one-marker exception.
    license_min_markers: int = 2
    #: A chunk this short (chars) needs only ONE licence marker: a 300-character
    #: chunk that is mostly a copyright line has no room to also be content.
    license_short_chars: int = 400

    #: Bibliographic signals per 100 words above which a chunk reads as a
    #: reference list. Prose that cites heavily lands around 3-6; reference lists
    #: land above 20. 12 sits in the empty middle.
    reference_density: float = 12.0
    #: Number of line-initial numbered entries ("1. ", "[2] ") that, combined
    #: with at least ``reference_density_relaxed`` density, is enough on its own.
    #: This catches the mid-list chunk that has no "References" header above it.
    reference_min_entries: int = 3
    #: The lower density bar that applies when the structural evidence
    #: (a References header, or ``reference_min_entries`` numbered entries) is
    #: already present.
    reference_density_relaxed: float = 7.0
    #: Chunks shorter than this (words) are not density-tested on their own — too
    #: few words make the per-100-word ratio wildly unstable. They can still be
    #: flagged as licence/acknowledgement text, which is header/marker-anchored.
    reference_min_words: int = 25
    #: Hard veto on the reference rule: a chunk whose function-word ratio is
    #: ABOVE this reads as sentences, not as a list of citations, whatever its
    #: bibliographic density. Calibrated at the clean separation point measured
    #: on the g1-corpus sample (unambiguous bibliographies topped out at 0.224;
    #: the 10th percentile of unambiguous prose was 0.208). This is the guard
    #: that protects heavily-cited review prose. See :func:`function_word_ratio`.
    reference_max_prose_ratio: float = 0.22

    #: How far into the chunk (chars) an acknowledgement-family header may start
    #: and still condemn the whole chunk on its own. Beyond this the chunk opens
    #: with content, and dropping it would drop that content.
    ack_header_window: int = 240
    #: Number of *distinct* acknowledgement-family headers that flags a chunk
    #: past ``ack_header_window`` — a chunk carrying "Funding", "Competing
    #: interests" *and* "Author contributions" is the end-matter block. Still
    #: subject to ``max_onset_fraction``.
    ack_min_headers: int = 2

    #: Positional guard shared by the header/entry-anchored rules: the evidence
    #: must *begin* within this fraction of the chunk. Chunks straddle section
    #: boundaries — the last conclusions paragraph and the "Acknowledgements"
    #: header land in one window — and without this guard a header in the final
    #: 10% of a chunk condemns the 90% of real content above it. Measured on the
    #: g1-corpus sample, this was the single largest source of false positives.
    #: 0.5 = the boilerplate must be at least half the chunk.
    max_onset_fraction: float = 0.5

    #: Section labels this config treats as boilerplate. Narrow it to keep, say,
    #: acknowledgements while still dropping reference lists.
    boilerplate_sections: frozenset[str] = field(default_factory=lambda: BOILERPLATE_SECTIONS)


DEFAULT_CONFIG = BoilerplateConfig()


@dataclass(frozen=True)
class BoilerplateVerdict:
    """Why a chunk was (or was not) called boilerplate.

    ``section`` is :data:`BODY` for real content. ``reason`` is a short,
    log-safe explanation — the thing that makes an ingest-time drop *visible*
    and countable instead of silent.
    """

    section: str
    reason: str = ""
    #: The bibliographic-signal density (signals per 100 words) that was
    #: measured. Exposed for calibration/benchmarking, not for control flow.
    density: float = 0.0

    @property
    def is_boilerplate(self) -> bool:
        return self.section in BOILERPLATE_SECTIONS


_BODY_VERDICT = BoilerplateVerdict(BODY)


def _license_hits(text: str) -> list[str]:
    """The distinct licence markers present in ``text`` (pattern source strings)."""
    return [p.pattern for p in _LICENSE_MARKERS if p.search(text)]


def _ack_headers(text: str) -> list[re.Match[str]]:
    return list(_ACK_HEADERS.finditer(text))


def _dominates(onset: int, text: str, cfg: BoilerplateConfig) -> bool:
    """Does boilerplate starting at ``onset`` make up most of ``text``?

    The positional guard behind ``max_onset_fraction``. Chunk windows straddle
    section boundaries, so a "Acknowledgements" header or a "References" heading
    routinely lands in the *tail* of an otherwise-content chunk; condemning the
    chunk then throws away the content above it. Requiring the evidence to begin
    in the leading fraction keeps those straddling chunks as content — the
    conservative side of the trade, since the boilerplate tail they retain is
    small and the following chunk (which is all boilerplate) still gets flagged.
    """
    return onset <= cfg.max_onset_fraction * max(len(text), 1)


def reference_signal_density(text: str) -> float:
    """Bibliographic signals per 100 words — the reference-list discriminator.

    Counts years, author initials, DOIs, volume:page ranges, ``et al.`` and
    bibliography furniture (``pp.``, ``eds.``, abbreviated journal names), then
    normalises by length. A reference list is *made of* these tokens; prose,
    even prose that cites, is made of words with a few of them sprinkled in.

    Public because it is the number an operator tunes ``reference_density``
    against on their own corpus.
    """
    words = len(text.split())
    if not words:
        return 0.0
    signals = (
        len(_YEAR_TOKEN.findall(text))
        + len(_INITIALS.findall(text))
        + len(_DOI_TOKEN.findall(text))
        + len(_VOL_PAGES.findall(text))
        + len(_ET_AL.findall(text))
        + len(_BIB_WORDS.findall(text))
    )
    return 100.0 * signals / words


def classify_chunk(
    text: str, config: BoilerplateConfig | None = None
) -> BoilerplateVerdict:
    """Classify one chunk as body / references / license / acknowledgements.

    Order matters: licence and acknowledgement text is header/marker-anchored and
    therefore the *most* certain, so it is tested first; the reference test is
    density-based and least certain, so it is tested last and only on chunks with
    enough words for the density to mean anything.

    Empty/whitespace text is :data:`BODY` — deciding what to do with an empty
    chunk is the chunker's and the pipeline's job, not this module's.
    """
    cfg = config or DEFAULT_CONFIG
    if not text or not text.strip():
        return _BODY_VERDICT

    lic = _license_hits(text)
    if len(lic) >= cfg.license_min_markers or (
        lic and len(text) <= cfg.license_short_chars
    ):
        return BoilerplateVerdict(
            LICENSE, f"{len(lic)} licence marker(s): {', '.join(lic[:3])}"
        )

    acks = _ack_headers(text)
    if acks and _dominates(acks[0].start(), text, cfg):
        distinct = {m.group(0).strip().lower().rstrip(":. ") for m in acks}
        if acks[0].start() <= cfg.ack_header_window:
            return BoilerplateVerdict(
                ACKNOWLEDGEMENTS,
                f"opens with end-matter header {acks[0].group(0).strip()!r}",
            )
        if len(distinct) >= cfg.ack_min_headers:
            return BoilerplateVerdict(
                ACKNOWLEDGEMENTS,
                f"{len(distinct)} end-matter headers: {', '.join(sorted(distinct)[:3])}",
            )

    density = reference_signal_density(text)
    prose = function_word_ratio(text)
    if len(text.split()) >= cfg.reference_min_words and prose <= cfg.reference_max_prose_ratio:
        entries = _NUMBERED_ENTRY.findall(text)
        header = _REF_HEADER.search(text)
        # Structural evidence only relaxes the density bar when it *starts* in
        # the dominant part of the chunk — see ``max_onset_fraction``.
        onsets = [m.start() for m in _NUMBERED_ENTRY.finditer(text)]
        structural = (header is not None and _dominates(header.start(), text, cfg)) or (
            len(entries) >= cfg.reference_min_entries
            and bool(onsets)
            and _dominates(onsets[0], text, cfg)
        )
        if density >= cfg.reference_density or (
            structural and density >= cfg.reference_density_relaxed
        ):
            why = (
                f"bibliographic density {density:.1f}/100w, prose {prose:.2f}"
                f"{f', {len(entries)} numbered entries' if entries else ''}"
                f"{', References header' if header else ''}"
            )
            return BoilerplateVerdict(REFERENCES, why, density)

    return BoilerplateVerdict(BODY, "", density)


def is_boilerplate(text: str, config: BoilerplateConfig | None = None) -> bool:
    """Convenience predicate over :func:`classify_chunk`, honouring the config's
    ``boilerplate_sections`` narrowing."""
    cfg = config or DEFAULT_CONFIG
    return classify_chunk(text, cfg).section in cfg.boilerplate_sections


def config_from_json(raw: str) -> BoilerplateConfig:
    """Build a :class:`BoilerplateConfig` from a JSON object of overrides.

    One config *string* rather than a dozen individual settings: the thresholds
    are a calibration set that operators tune together against their own corpus,
    and they should not each need a new ``Settings`` field (and a new release) to
    become adjustable. Unknown keys and malformed JSON degrade to the defaults
    with a warning — a typo in an env var must never hard-fail an ingest.

    ``{"reference_density": 15, "boilerplate_sections": ["references"]}``
    """
    if not raw or not raw.strip():
        return DEFAULT_CONFIG
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("expected a JSON object")
    except Exception as e:
        log.warning("ignoring malformed boilerplate config (%s); using defaults", e)
        return DEFAULT_CONFIG
    # Coerce every value to its declared field type. A dataclass does NOT
    # validate, so ``{"reference_density": "high"}`` would construct happily and
    # then raise TypeError on the first ``density >= cfg.reference_density``
    # comparison — deep inside an ingest, once per chunk. Reject it here instead.
    types = {f.name: f.type for f in fields(BoilerplateConfig)}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in types:
            log.warning("ignoring unknown boilerplate config key %r", key)
            continue
        try:
            if key == "boilerplate_sections":
                kwargs[key] = frozenset(str(s) for s in value)
            elif types[key] == "float":
                kwargs[key] = float(value)
            elif types[key] == "int":
                kwargs[key] = int(value)
            else:  # pragma: no cover - no other field types exist today
                kwargs[key] = value
        except (TypeError, ValueError) as e:
            log.warning("ignoring boilerplate config key %r (%s)", key, e)
    return BoilerplateConfig(**kwargs)


@dataclass(frozen=True)
class FilterResult:
    """What :meth:`BoilerplateFilter.apply` did — the *visible* drop record.

    ``scripts/ingest_jsonl.py``'s ``_kept()`` drops records silently, which makes
    an over-aggressive filter invisible until someone notices missing answers.
    This carries the counts back to the caller so every drop is logged and
    countable.
    """

    chunks: list[Chunk]
    flagged: Counter[str]
    dropped: int = 0
    #: doc_ids whose chunks were ALL boilerplate and were therefore kept anyway
    #: (see the all-boilerplate guard in :meth:`BoilerplateFilter.apply`).
    rescued_docs: tuple[str, ...] = ()

    def summary(self) -> str:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(self.flagged.items()))
        return f"flagged {sum(self.flagged.values())} ({parts or 'none'}), dropped {self.dropped}"


class BoilerplateFilter:
    """Stamp — and optionally drop — boilerplate chunks at ingest time.

    Two-stage on purpose:

    * **Flag** (``drop=False``, the default) is non-destructive. Every non-body
      chunk gets ``metadata["section"]`` and ``metadata["is_boilerplate"]``, which
      ride into Qdrant and Elasticsearch and make the boilerplate *measurable*
      (and filterable at query time) without changing what is indexed. Turning
      this on cannot lose data, which is why it is safe to default on.
    * **Drop** (``drop=True``) additionally removes them before embedding, which
      also saves the embed cost. Opt-in, because a false positive here is
      permanent for that ingest.

    **All-boilerplate guard.** If *every* surviving chunk of a document would be
    dropped, none of that document's chunks are dropped. A document reduced to
    zero chunks either disappears from the corpus or (via ``EmptyIngestError``)
    fails the whole ingest — both far worse outcomes than keeping a few
    boilerplate chunks, and exactly the case a mis-tuned threshold produces on a
    corpus this classifier was not calibrated for (a one-page notice, an
    extraction that recovered only the reference list).
    """

    def __init__(
        self, config: BoilerplateConfig | None = None, *, drop: bool = False
    ) -> None:
        self.config = config or DEFAULT_CONFIG
        self.drop = drop

    def apply(self, chunks: list[Chunk]) -> FilterResult:
        """Classify ``chunks``, stamp the non-body ones, and return the survivors."""
        flagged: Counter[str] = Counter()
        verdicts: list[str] = []
        for chunk in chunks:
            section = classify_chunk(chunk.content, self.config).section
            if section != BODY:
                flagged[section] += 1
                chunk.metadata[SECTION_KEY] = section
                chunk.metadata[BOILERPLATE_KEY] = section in self.config.boilerplate_sections
            verdicts.append(section)

        if not self.drop:
            return FilterResult(chunks, flagged)

        droppable = self.config.boilerplate_sections
        # Per-document survivor census first, so the all-boilerplate guard can be
        # applied before anything is actually removed.
        total: Counter[str] = Counter()
        boiler: Counter[str] = Counter()
        for chunk, section in zip(chunks, verdicts, strict=True):
            total[chunk.doc_id] += 1
            if section in droppable:
                boiler[chunk.doc_id] += 1
        rescued = tuple(sorted(d for d, n in boiler.items() if n == total[d]))
        if rescued:
            log.warning(
                "boilerplate filter: %d document(s) were entirely boilerplate; "
                "kept their chunks rather than emptying them: %s",
                len(rescued), list(rescued[:5]),
            )
        rescued_set = set(rescued)
        kept = [
            chunk
            for chunk, section in zip(chunks, verdicts, strict=True)
            if section not in droppable or chunk.doc_id in rescued_set
        ]
        return FilterResult(kept, flagged, len(chunks) - len(kept), rescued)


def filter_from_mode(mode: str, config_json: str = "") -> BoilerplateFilter | None:
    """Build a filter from the operator-facing ``off`` / ``flag`` / ``drop`` mode.

    The single place the three CLI ingest paths (``ingest_jsonl.py``,
    ``ingest_shard.py``, ``embed_shard.py``) turn their ``--boilerplate`` flag
    into a filter, so the offline plane and the online API can't drift apart on
    what "flag" means.
    """
    if mode == "off":
        return None
    return BoilerplateFilter(config_from_json(config_json), drop=mode == "drop")
