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
from collections import Counter
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


def random_order(draw: int, study: str, wids: Iterable[str], seeds: Mapping[int, str] | None = None) -> list[str]:
    """Candidates of one study paper in the seeded order of draw ``draw`` (1 or 2); ``seeds``
    defaults to :data:`SEED_RANDOM` (v1), :data:`SEED_RANDOM_V2` for the v2 re-draw."""
    seed = (seeds or SEED_RANDOM)[draw]
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


# ------------------------------------------------------------------ v2 (owner, 2026-11-01): walk-down, type, bands

SEED_RANDOM_V2 = {1: f"{SEED_BASE}|random-1-v2", 2: f"{SEED_BASE}|random-2-v2"}

#: The article-type filter, both arms (v2). OpenAlex ``type`` -> the matching class. Only these two
#: are kept; every other ``type`` (preprint, letter, editorial, erratum, retraction, paratext,
#: peer-review, book-chapter, conference-paper, dissertation, supplementary-materials, other, ...)
#: is dropped. ``type_crossref`` is not read: the snapshot projection the random pool came from
#: does not carry it, and both arms must be filtered on the same field.
TYPE_CLASS = {"article": "article", "review": "review"}

#: v2/v3 random draws: the order in which an unfillable (type, band, source) cell is relaxed —
#: **source first, then type, then band last** (owner, 2026-11-02). Level names: ``strict`` (all
#: three match), ``source`` (type and band match, source relaxed), ``source+type`` (band matches,
#: source and type relaxed), ``any`` (band relaxed too: whatever is left). Each relaxation pass
#: reuses the passed-over candidates first; unfetched ones are fetched within the relax budget.
RELAX_ORDER = ("strict", "source", "source+type", "any")
#: the levels that relax the band (only the last one)
BAND_RELAXED = frozenset({"any"})


def type_class(t: str | None) -> str | None:
    """OpenAlex ``type`` -> ``article`` / ``review``, or ``None`` (dropped)."""
    return TYPE_CLASS.get(t or "")


def rank_references(study: str, refs: Iterable[Work], excl: Exclusion) -> list[Work]:
    """Every eligible reference in pick order (:func:`pick_references` is its first ``cap``)."""
    elig = [r for r in refs if excl.reason(r) is None and r.oa_fulltext]
    elig.sort(key=lambda r: (-r.cited_by_count, seeded_hash(SEED_NEIGHBOURS, "tie", "refs", study, r.wid)))
    return elig


def rank_citers(study: str, citers: Iterable[Work], excl: Exclusion) -> list[Work]:
    """Every eligible citer in pick order (:func:`pick_citers` is its first ``cap``)."""
    elig = [c for c in citers if excl.reason(c) is None and c.oa_fulltext]
    elig.sort(key=lambda c: ((c.pub_date or ""), _desc_key(seeded_hash(SEED_NEIGHBOURS, "tie", "citers", study, c.wid))),
              reverse=True)
    return elig


def walk_down(ranked: Sequence[Work], cap: int, resolve: Callable[[str], Outcome]) -> dict[str, Any]:
    """The v2 neighbour rule for one side of one study paper: down the ranked list until ``cap``
    works are obtained or the list is exhausted.

    A work is skipped (with its reason) when its OpenAlex type is not kept (``type:<t>``), when the
    fetch fails (the fetch reason: not in the PMC OA subset, robots, 403/429, not a PDF, ...) or when
    the C0 screen excludes it (``c0_screen``). ``resolve`` is only called for type-kept works.
    """
    picked: list[str] = []
    skipped: list[tuple[str, str]] = []
    examined = 0
    for w in ranked:
        if len(picked) >= cap:
            break
        examined += 1
        if type_class(w.type) is None:
            skipped.append((w.wid, f"type:{w.type}"))
            continue
        out = resolve(w.wid)
        if not out.ok:
            skipped.append((w.wid, "c0_screen" if out.excluded_c0 else (out.reason or "failed")))
            continue
        picked.append(w.wid)
    return {"picked": picked, "skipped": skipped, "examined": examined, "ranked": len(ranked),
            "exhausted": len(picked) < cap}


def next_to_resolve(ranked: Sequence[Work], cap: int, known: Callable[[str], Outcome | None]) -> list[str]:
    """The works :func:`walk_down` would resolve next given what is already known: walk the list
    with the known outcomes and return the next ``cap - picked`` unknown, type-kept works (resolving
    exactly those cannot over-fetch: each is needed unless an earlier one succeeds)."""
    got = 0
    want: list[str] = []
    for w in ranked:
        if got + len(want) >= cap:
            break
        if type_class(w.type) is None:
            continue
        o = known(w.wid)
        if o is None:
            want.append(w.wid)
        elif o.ok:
            got += 1
    return want


def length_cuts(values: Sequence[int], n_bands: int) -> list[float]:
    """Band boundaries at the ``n_bands`` quantiles of ``values`` (nearest-rank, lower)."""
    xs = sorted(values)
    if not xs:
        raise ValueError("no values")
    return [float(xs[min(len(xs) - 1, (len(xs) * i) // n_bands)]) for i in range(1, n_bands)]


def band_of(n: int, cuts: Sequence[float]) -> int:
    """Band index 0..len(cuts): band i holds cuts[i-1] <= n < cuts[i]."""
    b = 0
    for c in cuts:
        if n >= c:
            b += 1
    return b


Cell = tuple[str, int, str]  # (type class, length band, source)


def _relaxed(cell: Cell, level: str) -> tuple:
    t, b, s = cell
    return {"strict": (t, b, s), "source": (t, b), "source+type": (b,), "any": ()}[level]


def draw_matched(order: Sequence[str], quota: Mapping[Cell, int], taken: set[str],
                 type_of: Callable[[str], str | None], klass: Callable[[str], str | None],
                 resolve: Callable[[str], Outcome], band: Callable[[int], int],
                 is_dup: Callable[[str], bool] | None = None, on_take: Callable[[str], None] | None = None,
                 max_resolves: int | None = None) -> dict[str, Any]:
    """One study paper's share of one v2 random draw: matched on (type, length band, source).

    ``quota``: cell -> count, from the study paper's attributed neighbours. Candidates are walked in
    the draw's seeded order. Before fetching, a candidate is skipped when its type is not kept, and
    not fetched when no open cell has its type and predicted source (``cell_closed``); a fetched,
    accepted (ok) candidate is taken when its (type, band of its chunk count, actual source) cell is
    open, else it is passed over as ``rejected_for_band`` (its type and source had an open cell, its
    band did not) or ``rejected_cell_full``. The walk stops when every cell is full, the candidates
    run out, or ``max_resolves`` fetches were spent. Cells still open are then filled from the
    passed-over candidates, in seeded order, level by level of :data:`RELAX_ORDER` (fetched ones
    first cost nothing; unfetched ones are fetched when the level could use them, within a second
    ``max_resolves``); every take records the level it was matched at.
    """
    open_q: dict[Cell, int] = {c: n for c, n in quota.items() if n > 0}
    need = sum(open_q.values())
    took: list[dict[str, Any]] = []
    later: list[str] = []                 # passed over in the strict pass, in seeded order
    cells: dict[str, Cell | None] = {}    # fetched: wid -> its cell (None: failed / screened out)
    skipped: Counter[str] = Counter()
    resolves = {"strict": 0, "relax": 0}
    budget_hit = False

    def is_open(pred: Callable[[Cell], bool]) -> bool:
        return any(n > 0 and pred(c) for c, n in open_q.items())

    def take(wid: str, cell: Cell, matched: Cell, level: str) -> None:
        open_q[matched] -= 1
        taken.add(wid)
        took.append({"wid": wid, "type": cell[0], "band": cell[1], "source": cell[2], "level": level,
                     "for_cell": list(matched)})
        if on_take is not None:
            on_take(wid)

    def fetch(wid: str, t: str, k: str, phase: str) -> Cell | None:
        resolves[phase] += 1
        out = resolve(wid)
        if not out.ok:
            skipped["c0_screen" if out.excluded_c0 else (out.reason or "failed")] += 1
            cells[wid] = None
            return None
        cells[wid] = (t, band(out.n_chunks), out.source or k)
        return cells[wid]

    for i, wid in enumerate(order):
        if not is_open(lambda c: True):
            break
        if wid in taken:
            continue
        t = type_class(type_of(wid))
        if t is None:
            skipped["type"] += 1
            continue
        if is_dup is not None and is_dup(wid):
            skipped["duplicate"] += 1
            continue
        if not is_open(lambda c, t=t: c[0] == t):
            skipped["type_cells_full"] += 1
            later.append(wid)
            continue
        k = klass(wid)
        if k is None:
            skipped["no_oa_fulltext"] += 1
            continue
        if not is_open(lambda c, t=t, k=k: c[0] == t and c[2] == k):
            skipped["cell_closed"] += 1
            later.append(wid)
            continue
        if max_resolves is not None and resolves["strict"] >= max_resolves:
            budget_hit = True
            later += [w for w in order[i:] if w not in taken]
            break
        cell = fetch(wid, t, k, "strict")
        if cell is None:
            continue
        if open_q.get(cell, 0) > 0:
            take(wid, cell, cell, "strict")
            continue
        src = cell[2]
        skipped["rejected_for_band" if is_open(lambda c, t=t, src=src: c[0] == t and c[2] == src)
                else "rejected_cell_full"] += 1
        later.append(wid)
    # relaxation: the passed-over candidates, in seeded order; fetched ones first cost nothing, the
    # unfetched ones are fetched only when the relaxed level could use them (own budget)
    rank = {w: i for i, w in enumerate(order)}
    later.sort(key=rank.__getitem__)
    relaxed: Counter[str] = Counter()
    for level in RELAX_ORDER[1:]:
        for wid in later:
            if not is_open(lambda c: True):
                break
            if wid in taken or (is_dup is not None and is_dup(wid)):
                continue
            if wid not in cells:
                t = type_class(type_of(wid)) or ""
                if not t:
                    cells[wid] = None
                    continue
                if level == "source" and not is_open(lambda c, t=t: c[0] == t):
                    continue
                if max_resolves is not None and resolves["relax"] >= max_resolves:
                    budget_hit = True
                    continue
                k = klass(wid)
                if k is None:
                    cells[wid] = None
                    continue
                fetch(wid, t, k, "relax")
            cell = cells[wid]
            if cell is None:
                continue
            key = _relaxed(cell, level)
            match = next((c for c, n in sorted(open_q.items()) if n > 0 and _relaxed(c, level) == key), None)
            if match is not None:
                lv = "strict" if match == cell else level   # fetched late, matched exactly
                take(wid, cell, match, lv)
                relaxed[lv] += 1
    got = len(took)
    return {"took": took, "need": need, "got": got, "shortfall": max(0, need - got),
            "quota": {"|".join(map(str, c)): n for c, n in sorted(quota.items())},
            "unfilled": {"|".join(map(str, c)): n for c, n in sorted(open_q.items()) if n > 0},
            "relaxed": dict(relaxed), "skipped": dict(skipped), "resolves": sum(resolves.values()),
            "resolves_by_phase": resolves, "budget_hit": budget_hit, "passed_over": len(later)}


# ------------------------------------------------------------------ v3 (owner, 2026-11-02): the ≥ 80 band

#: v3 splits the open-ended top length band of v2 at 80 chunks (v2 cuts 20/27/34/46 -> 20/27/34/46/80)
V3_SPLIT_AT = 80.0


def v3_cuts(v2_cuts: Sequence[float]) -> list[float]:
    """The v2 cuts plus the split of the open top band at :data:`V3_SPLIT_AT`."""
    cuts = [float(c) for c in v2_cuts]
    if cuts and V3_SPLIT_AT <= cuts[-1]:
        raise ValueError(f"split {V3_SPLIT_AT} is not above the top v2 cut {cuts[-1]}")
    return cuts + [V3_SPLIT_AT]


def carry_over(took: Sequence[Mapping[str, Any]], quota: Mapping[Cell, int], band: Callable[[int], int],
               n_chunks: Callable[[str], int]) -> dict[str, Any]:
    """Which of one study paper's v2 takes stay in v3, under the v3 ``quota`` and ``band``.

    A take is **re-drawn** (released) when it was matched at a band-relaxed level (``any``:
    ``relaxed_on_band``), or when the v3 slot its own band now points to has no room
    (``band_assignment_changed``: the v2 top band split, so its slot moved to the other side of
    the split). Every other take is kept with its v2 level: a take below ``any`` matched its slot's
    band, so its v3 slot is (slot type, its own v3 band, slot source). Takes are considered in v2
    take order; the first ones fill a v3 cell. Returns kept takes (``band``/``for_cell`` rewritten in
    v3 terms), released takes (with ``why``) and the quota left open for the re-draw.
    """
    open_q: Counter[Cell] = Counter({c: n for c, n in quota.items() if n > 0})
    kept: list[dict[str, Any]] = []
    released: list[dict[str, Any]] = []
    for t in took:
        if t["level"] in BAND_RELAXED:
            released.append({**t, "why": "relaxed_on_band"})
            continue
        ft, fb, fs = t["for_cell"]
        if int(t["band"]) != int(fb):
            raise ValueError(f"take {t['wid']} at level {t['level']} has band {t['band']} != slot band {fb}")
        b3 = band(n_chunks(t["wid"]))
        slot: Cell = (ft, b3, fs)
        if open_q[slot] > 0:
            open_q[slot] -= 1
            kept.append({**t, "band": b3, "for_cell": list(slot), "v2_band": t["band"]})
        else:
            released.append({**t, "why": "band_assignment_changed", "v3_band": b3})
    return {"kept": kept, "released": released, "open": {c: n for c, n in sorted(open_q.items()) if n > 0}}


def strict_shortfall(quota: Mapping[Cell, int], took: Iterable[Mapping[str, Any]]) -> dict[Cell, int]:
    """Per cell: slots not filled at level ``strict`` (quota minus strict takes for that cell)."""
    got: Counter[Cell] = Counter(tuple(t["for_cell"]) for t in took if t["level"] == "strict")  # type: ignore[misc]
    return {c: n - got[c] for c, n in sorted(quota.items()) if n - got[c] > 0}


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
