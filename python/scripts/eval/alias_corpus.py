"""Alias study (PREREG-alias-study.md §5.1, §5.5): the pure rules behind the added-paper corpus.

The neighbour arm and the two random arms of the alias study are OA-1000 plus added papers. This
module holds the **rules** that pick and admit those papers, with no I/O, so each rule is unit-tested
offline; ``alias_corpus_run.py`` is the I/O driver (OpenAlex, the PMC open-access bucket, PDF hosts,
the converter and the chunker).

Rules, as written in the prereg (draft v1, decisions D4, D5, D6, O5, O6, O9):

* **Seeds** (§5.5): base ``261101|alias``. Every order is sha256 of the seed string followed by ``|``
  and the named parts, ascending (:func:`seeded_hash`).
* **Neighbours** (D4): per study paper, of the works it references that have open-access full text
  and are not excluded, the 5 most cited (OpenAlex ``cited_by_count``); of the works that cite it,
  likewise, the 5 most recent (``publication_date``). Ties by
  ``261101|alias|neighbours|tie|<refs or citers>|<study paper>|<work id>`` ascending.
* **Shared neighbours** (O5): a work picked for several study papers is added once and attributed to
  the first study paper in the order ``261101|alias|neighbours|order|<study paper>``; the others draw
  no replacement.
* **Random draws** (D5, D6): per study paper, as many works as were attributed to it in the neighbour
  arm (after the fetch and the control-character screen), from works with the same OpenAlex primary
  topic, open-access full text, publication year within ± 3, not in OA-1000, not in the neighbour
  arm, citing no study paper and cited by none, not drawn for another study paper in the same draw.
  Candidates ordered by ``<draw seed>|<study paper>|<work id>``; study papers served in the
  neighbour arm's attribution order.
* **Text source and PDF share** (O6): JATS from the PMC open-access subset where it exists, else
  the open-access PDF. The random draw takes, per study paper, as many non-JATS papers as that study
  paper's attributed neighbours have, the rest JATS; where one pool is short the other fills in.
* **Control-character screen** (O6): C0 characters other than tab, newline and carriage return. A
  PDF-sourced paper is excluded when any occurs in more than 0.5 % of its chunks; a JATS-sourced
  paper is counted, never excluded. An excluded neighbour is not replaced; an excluded random
  candidate is skipped and the draw goes on.

**Open-access full text, operationally** (the prereg says "open-access full text"; this is how it is
read here, and every manifest repeats it): a work qualifies when OpenAlex records a PMCID for it
(the PMC route, checked against the PMC open-access flag at fetch time), or when it has at least one
OpenAlex location with ``is_oa`` true, a ``pdf_url`` **and a non-empty ``license``** (the PDF route;
only such locations are ever fetched). A landing page alone is not full text.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

SEED_BASE = "261101|alias"
SEED_NEIGHBOURS = f"{SEED_BASE}|neighbours"
SEED_RANDOM = {1: f"{SEED_BASE}|random|1", 2: f"{SEED_BASE}|random|2"}

REF_CAP = 5
CITER_CAP = 5
YEAR_WINDOW = 3
C0_THRESHOLD = 0.005  # a PDF paper is excluded when > 0.5 % of its chunks hold a C0 character

#: C0 control characters other than tab (U+0009), newline (U+000A) and carriage return (U+000D).
C0_CHARS = frozenset(chr(c) for c in range(0x20) if c not in (0x09, 0x0A, 0x0D))
_C0_RE = re.compile("[" + "".join(re.escape(c) for c in sorted(C0_CHARS)) + "]")

OA_DEFINITION = ("pmcid present (JATS route, PMC open-access flag checked at fetch) OR >= 1 OpenAlex "
                 "location with is_oa, pdf_url and a non-empty license (PDF route); landing pages "
                 "alone do not qualify")


def seeded_hash(seed: str, *parts: str) -> str:
    """sha256 hex of ``seed|part1|part2...`` (§5.5)."""
    return hashlib.sha256("|".join((seed, *parts)).encode("utf-8")).hexdigest()


def short_id(wid: str | None) -> str | None:
    """``https://openalex.org/W123`` -> ``W123`` (idempotent)."""
    return wid.rsplit("/", 1)[-1] if wid else None


def norm_pmcid(raw: str | None) -> str | None:
    """Any OpenAlex/PMC spelling -> ``PMC123``; ``None`` for empty."""
    if not raw:
        return None
    x = str(raw).rsplit("/", 1)[-1].strip().upper()
    if not x:
        return None
    return x if x.startswith("PMC") else "PMC" + x


def norm_doi(raw: str | None) -> str | None:
    if not raw:
        return None
    x = str(raw).strip().lower()
    for pre in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if x.startswith(pre):
            x = x[len(pre):]
    return x or None


_TITLE_KEEP = re.compile(r"[^a-z0-9]+")


def norm_title(raw: str | None) -> str | None:
    """Lower-cased alphanumerics only; ``None`` below 30 characters (too generic to match on)."""
    if not raw:
        return None
    x = unicodedata.normalize("NFKD", str(raw)).encode("ascii", "ignore").decode().lower()
    x = _TITLE_KEEP.sub("", x)
    return x if len(x) >= 30 else None


# ------------------------------------------------------------------ work records


@dataclass(frozen=True)
class PdfLocation:
    url: str
    license: str
    source: str  # "best_oa_location" or "locations[i]"


@dataclass
class Work:
    """The metadata the rules read, from an OpenAlex work record (API or snapshot)."""

    wid: str
    pmcid: str | None = None
    doi: str | None = None
    title: str | None = None
    year: int | None = None
    pub_date: str | None = None
    cited_by_count: int = 0
    topic: str | None = None
    type: str | None = None
    is_oa: bool = False
    pdf_locations: tuple[PdfLocation, ...] = ()
    referenced: tuple[str, ...] = ()

    @property
    def oa_fulltext(self) -> bool:
        return bool(self.pmcid) or bool(self.pdf_locations)

    @property
    def predicted_class(self) -> str:
        """``pmc`` / ``pdf`` / ``unavailable`` from metadata alone (the fetch decides the source)."""
        if self.pmcid:
            return "pmc"
        if self.pdf_locations:
            return "pdf"
        return "unavailable"


def _pdf_locations(best: Mapping | None, locations: Sequence[Mapping] | None) -> tuple[PdfLocation, ...]:
    out: list[PdfLocation] = []
    seen: set[str] = set()
    cands: list[tuple[str, Mapping]] = []
    if best:
        cands.append(("best_oa_location", best))
    for i, loc in enumerate(locations or []):
        cands.append((f"locations[{i}]", loc))
    for src, loc in cands:
        if not loc or not loc.get("is_oa", src == "best_oa_location"):
            continue
        url, lic = loc.get("pdf_url"), loc.get("license")
        if not url or not lic or url in seen:
            continue
        seen.add(url)
        out.append(PdfLocation(url=str(url), license=str(lic), source=src))
    return tuple(out)


def work_from_api(w: Mapping[str, Any]) -> Work:
    """An OpenAlex API ``/works`` record -> :class:`Work`."""
    ids = w.get("ids") or {}
    best = w.get("best_oa_location")
    if best is not None and "is_oa" not in best:
        best = dict(best, is_oa=True)
    return Work(
        wid=short_id(w.get("id")) or "",
        pmcid=norm_pmcid(ids.get("pmcid")),
        doi=norm_doi(w.get("doi") or ids.get("doi")),
        title=w.get("display_name") or w.get("title"),
        year=w.get("publication_year"),
        pub_date=w.get("publication_date"),
        cited_by_count=int(w.get("cited_by_count") or 0),
        topic=short_id((w.get("primary_topic") or {}).get("id")),
        type=w.get("type"),
        is_oa=bool((w.get("open_access") or {}).get("is_oa")),
        pdf_locations=_pdf_locations(best, w.get("locations")),
        referenced=tuple(x for x in (short_id(r) for r in (w.get("referenced_works") or [])) if x),
    )


def work_from_snapshot(row: Mapping[str, Any]) -> Work:
    """A row of the parquet snapshot projection written by the pool scan -> :class:`Work`.

    ``ids`` arrives as a list of ``(key, value)`` pairs (parquet map), ``locations`` as a list of
    ``{is_oa, pdf_url, license}``, ``best_oa_location`` as ``{pdf_url, license}`` or ``None``.
    """
    ids = dict(row.get("ids") or [])
    best = row.get("best_oa_location")
    if best and (best.get("pdf_url") or best.get("license")):
        best = dict(best, is_oa=True)
    else:
        best = None
    return Work(
        wid=short_id(row.get("id")) or "",
        pmcid=norm_pmcid(ids.get("pmcid")),
        doi=norm_doi(row.get("doi") or ids.get("doi")),
        title=row.get("title"),
        year=row.get("publication_year"),
        topic=short_id((row.get("primary_topic") or {}).get("id")) if isinstance(row.get("primary_topic"), Mapping)
        else short_id(row.get("primary_topic")),
        type=row.get("type"),
        is_oa=bool((row.get("open_access") or {}).get("is_oa")) if isinstance(row.get("open_access"), Mapping)
        else bool(row.get("is_oa")),
        pdf_locations=_pdf_locations(best, row.get("locations")),
    )


# ------------------------------------------------------------------ exclusion


@dataclass
class Exclusion:
    """Works that may never be added: OA-1000 papers (and so every study paper), by any identity."""

    wids: set[str] = field(default_factory=set)
    pmcids: set[str] = field(default_factory=set)
    dois: set[str] = field(default_factory=set)
    titles: set[str] = field(default_factory=set)

    def reason(self, w: Work) -> str | None:
        """Why ``w`` is excluded (``wid``/``pmcid``/``doi``/``title``), or ``None``."""
        if w.wid in self.wids:
            return "wid"
        if w.pmcid and w.pmcid in self.pmcids:
            return "pmcid"
        if w.doi and w.doi in self.dois:
            return "doi"
        t = norm_title(w.title)
        if t and t in self.titles:
            return "title"
        return None

    def add(self, w: Work) -> None:
        self.wids.add(w.wid)
        if w.pmcid:
            self.pmcids.add(w.pmcid)
        if w.doi:
            self.dois.add(w.doi)
        t = norm_title(w.title)
        if t:
            self.titles.add(t)


# ------------------------------------------------------------------ neighbours


def _desc_key(h: str) -> str:
    """Invert a hex digest so that a *descending* sort orders it ascending."""
    return "".join(chr(255 - ord(x)) for x in h)


def pick_references(study: str, refs: Iterable[Work], excl: Exclusion, cap: int = REF_CAP) -> tuple[list[Work], dict]:
    """The ``cap`` most-cited eligible references; ties by the seeded hash, ascending."""
    refs = list(refs)
    cand = [r for r in refs if excl.reason(r) is None]
    elig = [r for r in cand if r.oa_fulltext]
    elig.sort(key=lambda r: (-r.cited_by_count, seeded_hash(SEED_NEIGHBOURS, "tie", "refs", study, r.wid)))
    return elig[:cap], {"found": len(refs), "not_excluded": len(cand), "eligible": len(elig),
                        "excluded": len(refs) - len(cand)}


def pick_citers(study: str, citers: Iterable[Work], excl: Exclusion, cap: int = CITER_CAP) -> tuple[list[Work], dict]:
    """The ``cap`` most recent eligible citers (``publication_date``); ties by the seeded hash, ascending."""
    citers = list(citers)
    cand = [c for c in citers if excl.reason(c) is None]
    elig = [c for c in cand if c.oa_fulltext]
    elig.sort(key=lambda c: ((c.pub_date or ""), _desc_key(seeded_hash(SEED_NEIGHBOURS, "tie", "citers", study, c.wid))),
              reverse=True)
    return elig[:cap], {"found": len(citers), "not_excluded": len(cand), "eligible": len(elig),
                        "excluded": len(citers) - len(cand)}


def attribution_order(studies: Iterable[str]) -> list[str]:
    """Study papers in the seeded attribution order (O5)."""
    return sorted(studies, key=lambda p: seeded_hash(SEED_NEIGHBOURS, "order", p))


def attribute(picks: Mapping[str, Sequence[tuple[str, str]]]) -> tuple[dict[str, list[tuple[str, str]]], int]:
    """Shared-neighbour attribution (O5). ``picks``: study -> [(work id, side)] in pick order.

    Returns (study -> attributed [(work id, side)], number of duplicate picks dropped). A work goes
    to the first study paper in :func:`attribution_order`; the others draw no replacement.
    """
    seen: set[str] = set()
    out: dict[str, list[tuple[str, str]]] = {}
    dropped = 0
    for p in attribution_order(picks):
        out[p] = []
        for wid, side in picks[p]:
            if wid in seen:
                dropped += 1
                continue
            seen.add(wid)
            out[p].append((wid, side))
    return out, dropped


# ------------------------------------------------------------------ random draws


def random_order(draw: int, study: str, wids: Iterable[str]) -> list[str]:
    """Candidates of one study paper in the seeded order of draw ``draw`` (1 or 2)."""
    seed = SEED_RANDOM[draw]
    return sorted(set(wids), key=lambda w: seeded_hash(seed, study, w))


def eligible_random(w: Work, study_topic: str, study_year: int, excl: Exclusion, linked: set[str],
                    neighbour_wids: set[str]) -> str | None:
    """Why ``w`` is not a random candidate for a study paper, or ``None`` when it is (D5)."""
    if w.topic != study_topic:
        return "topic"
    if w.year is None or abs(int(w.year) - int(study_year)) > YEAR_WINDOW:
        return "year"
    if not w.oa_fulltext:
        return "no_oa_fulltext"
    if excl.reason(w):
        return "oa1000"
    if w.wid in neighbour_wids:
        return "neighbour"
    if w.wid in linked:
        return "citation_link"
    return None


@dataclass
class Outcome:
    """What fetching, converting, chunking and screening one work produced."""

    ok: bool
    source: str | None = None  # "jats" | "pdf"
    reason: str | None = None  # failure or exclusion reason when not ok
    excluded_c0: bool = False
    n_chunks: int = 0
    c0_chunks: int = 0


def draw_for_study(order: Sequence[str], need_jats: int, need_pdf: int, taken: set[str],
                   klass: Callable[[str], str | None], resolve: Callable[[str], Outcome],
                   is_dup: Callable[[str], bool] | None = None,
                   on_take: Callable[[str], None] | None = None) -> dict[str, Any]:
    """One study paper's share of one random draw (D5 with the O6 PDF-share rule).

    ``order``: the candidates in the draw's seeded order (already eligible by metadata).
    ``klass(wid)``: the candidate's source class before fetching — ``"jats"`` when a PMC
    open-access copy exists, ``"pdf"`` when only the PDF route does, ``None`` when neither.
    ``resolve(wid)``: fetch, convert, chunk and screen; an :class:`Outcome`. A candidate whose class
    quota is full is deferred, not drawn, so it stays open to later study papers; after the pass,
    the short class is filled from the deferred candidates of the other class, in order.
    A failed or screened-out candidate is skipped (counted) and the draw goes on.
    ``is_dup(wid)``: true when the candidate is another OpenAlex record of a paper already in the
    draw (same PMCID or DOI); skipped as ``duplicate``. ``on_take(wid)`` is called on every take.
    """
    need = need_jats + need_pdf
    got: dict[str, list[str]] = {"jats": [], "pdf": []}
    deferred: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    quota = {"jats": need_jats, "pdf": need_pdf}

    def full() -> bool:
        return len(got["jats"]) >= quota["jats"] and len(got["pdf"]) >= quota["pdf"]

    def take(wid: str, src: str) -> None:
        got[src].append(wid)
        taken.add(wid)
        if on_take is not None:
            on_take(wid)

    for wid in order:
        if full():
            break
        if wid in taken:
            continue
        if is_dup is not None and is_dup(wid):
            skipped.append((wid, "duplicate"))
            continue
        k = klass(wid)
        if k is None:
            skipped.append((wid, "no_oa_fulltext"))
            continue
        if len(got[k]) >= quota[k]:
            deferred.append((wid, k))
            continue
        out = resolve(wid)
        if not out.ok:
            skipped.append((wid, "c0_screen" if out.excluded_c0 else (out.reason or "failed")))
            continue
        src = out.source or k
        if src != k and len(got[src]) >= quota[src]:
            # the fetch fell back to the other route and that class is full: defer it
            deferred.append((wid, src))
            continue
        take(wid, src)
    filled_from_other = 0
    if len(got["jats"]) + len(got["pdf"]) < need:
        for wid, k in deferred:
            if len(got["jats"]) + len(got["pdf"]) >= need:
                break
            if wid in taken or (is_dup is not None and is_dup(wid)):
                continue
            out = resolve(wid)
            if not out.ok:
                skipped.append((wid, "c0_screen" if out.excluded_c0 else (out.reason or "failed")))
                continue
            take(wid, out.source or k)
            filled_from_other += 1
    n = len(got["jats"]) + len(got["pdf"])
    return {"jats": got["jats"], "pdf": got["pdf"], "need": need, "need_jats": need_jats,
            "need_pdf": need_pdf, "got": n, "shortfall": max(0, need - n),
            "filled_from_other_class": filled_from_other, "skipped": skipped,
            "deferred": len(deferred)}


# ------------------------------------------------------------------ control-character screen


def has_c0(text: str) -> bool:
    return bool(_C0_RE.search(text))


def c0_screen(chunks: Sequence[str], source: str, threshold: float = C0_THRESHOLD) -> dict[str, Any]:
    """C0 screen of one paper's chunks (O6). ``excluded`` only ever for ``source == "pdf"``."""
    n = len(chunks)
    hit = sum(1 for c in chunks if has_c0(c))
    ff = sum(1 for c in chunks if "\x0c" in c)
    rate = hit / n if n else 0.0
    return {"n_chunks": n, "c0_chunks": hit, "formfeed_chunks": ff, "rate": rate,
            "excluded": source == "pdf" and rate > threshold}


# ------------------------------------------------------------------ export rows


def export_row(chunk: Any) -> dict[str, Any]:
    """One chunk -> the ``chunks-oa.jsonl`` export shape (fields and order as that file)."""
    md = chunk.metadata or {}
    return {"chunk_id": chunk.id, "doc_id": chunk.doc_id, "content": chunk.content,
            "start_char": chunk.start_char, "end_char": chunk.end_char,
            "metadata": {"source_path": md.get("source_path"), "title": md.get("title")}}
