"""JATS XML -> ingest-ready records (the parsing core behind ``scripts/jats_extract.py``).

This is the ported, repo-resident version of the validated out-of-tree converter
``/rag/oa/scripts/jats_to_jsonl.py`` (#301). It turns one PubMed-Central JATS
article into the ``{"text", "path", "metadata"}`` records that
:class:`ragstack.ingestion.loaders.JsonlLoader` consumes, so the OA corpus can
flow through the existing embed/load plane.

It lives in the package (rather than inside the script) for the same reason
``PdfLoader`` does: the CLI (``scripts/jats_extract.py``) is I/O and argument
plumbing, while the parsing rules are pure, importable and unit-tested offline —
and :class:`ragstack.ingestion.loaders.JatsLoader`, the ``.xml`` entry of the
loader registry (#828 PR 5a), reuses them unchanged.

TWO RECORD KINDS, because tables must not be chunked as if they were prose:

  ``content_type="article"``
      One record per article: section-aware body text (abstract + ``<body>``,
      section titles kept as markdown headings) with every ``<table-wrap>`` and
      ``<fig>`` **lifted out at any depth**, so a fixed-token window can never
      splice half a table grid into the middle of a sentence. Measured on the
      validation run: inline-table contamination of prose chunks 17.78% -> 0.27%.

  ``content_type="table"`` / ``content_type="figure"``
      One self-contained record per unit: label + caption (+ ``<table-wrap-foot>``
      legend) travel with the grid, and an oversized table is pre-split BY ROW
      with the caption and header row repeated on every piece, since the stock
      chunker preserves no header context. Units land at <= ``max_chars`` + 2
      characters (the caption/grid separator), i.e. 1,802 at the 1,800 default
      for tables and 1,800 for figures — one 512-token chunk each, so a single
      ``fixed_token 512/64`` pass covers both record kinds.

Every record's ``path`` is unique (``PMC123``, ``PMC123#table-2``,
``PMC123#table-2-part-3``) because ``JsonlLoader`` derives the document id from
``path``; colliding paths overwrite each other.

Floats are collected from the WHOLE tree, not just ``<body>``: MDPI parks them in
``<floats-group>`` outside the body, and keeping front+abstract+body only loses
18-27% of those articles' text.

Metadata emitted per record (see :data:`JATS_METADATA_KEYS`): ``doi``, ``pmid``,
``pmcid``, ``title``, ``authors``, ``keywords``, ``journal``, ``publisher``,
``year``, ``licence``, ``sha256``, ``source_url``, ``content_type``, plus
``section_title`` (the unit suffix) and ``graphic`` (the ``<graphic>`` xlink href)
on unit records, and ``abstract``/``n_tables``/``n_figures`` on article records.
``authors``/``keywords`` are ``"; "``-joined STRINGS: that is the contract
``ingestion.enrich``'s ``parse_authors``/``split_keywords`` expect, and enrich
turns them into ``list[str]``. A consumer that bypasses enrich must convert them
itself or the same key ends up ``str`` here and ``list[str]`` elsewhere, and
cross-collection filters silently match only one side.

TYPES ARE PART OF THAT CONTRACT. ``year`` is an ``int`` (or absent), ``n_tables``
and ``n_figures`` are ``int``, everything else is a ``str``. ``year`` used to be
the raw ``<year>`` text, which is unreachable by a correct filter: the API
declares ``year`` an integer field and matches values BY TYPE, never coercing
(``stores/filters.py``, #471). The live proof is the ``lucid`` tenant's
collection, whose string years make ``{"year": 2021}`` match 129,248 chunks on
the Elasticsearch leg and **0** on the Qdrant leg — the same request, two
answers, no error.

ELEMENT SPANS (#318 P3). :func:`article_spans` returns the article record's
``text`` together with a :class:`Span` per abstract / ``<sec>`` / heading / block
element, offsets into that text accumulated during the very walk that builds it
(``article_prose`` and ``section_text`` are thin wrappers over the same walk, so
the text cannot drift from the spans). Untitled sections and body-lead paragraphs
are first-class spans — the section boundaries the flat text loses. Spans are
opt-in (``article_records(..., with_spans=True)``) and never enter a record's
``metadata``. Since #828 (docs/plans/828-document-structure.md §4) the JATS
adapter's document structure, built from these spans, travels in the JSONL
shard as the record's top-level ``structure`` key; the record dicts this module
returns are unchanged by that (the key is added by the extract tool, from
these spans for the article record and from each unit's :class:`UnitLayout`
for table/figure records).

Stdlib ``xml.etree.ElementTree`` only — the CWL worker image is CPU-only and
carries no lxml guarantee. The one non-stdlib import is
``ragstack.metadata_schema``, which is a dependency-free leaf ON PURPOSE:
this module is imported once per extract task across a 1.44M-document corpus,
so reaching the type table through ``ingestion.enrich`` (pydantic) or
``stores.filters`` (which executes ``ragstack.stores.__init__``: neo4j,
qdrant-client) would put a 16x import-time and 3x RSS floor on every task.
Since #828 PR 5b ``scripts/jats_extract.py`` also imports
:mod:`ragstack.ingestion.jats_structure` to write the ``structure`` key (it
already imported pydantic before that, through ``ragstack.provenance``; measured
+8 modules, ~13 ms, no RSS change, still no store client); this module itself
stays stdlib + ``metadata_schema``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal, NamedTuple, overload
from xml.etree import ElementTree as ET

from ragstack.metadata_schema import coerce_year

XLINK = "{http://www.w3.org/1999/xlink}href"

#: Floats that are lifted out of the prose flow (and emitted as their own units).
LIFT = ("table-wrap", "fig")

#: Default cap for one table/figure unit, in characters (~450 tokens IF the text
#: were prose). Tables are not prose: measured on 1,727 real units, their token
#: density spans 1.61–4.30 chars/token (p50 2.84), so a char cap alone left 32.2%
#: of units over one 512-token window — and the stock chunker then split them
#: WITHOUT caption/header context, the exact contamination the lift-out exists to
#: prevent. Hence ``count_tokens``/``max_tokens`` below: when a token counter is
#: supplied, this char value is only the ceiling and each unit's real budget is
#: derived from its own measured density.
DEFAULT_MAX_CHARS = 1800

#: Default per-unit token budget. 480, not 512: the loader prepends nothing, but
#: headroom absorbs tokenizer drift (added special tokens, version changes)
#: without a re-ingest.
DEFAULT_MAX_TOKENS = 480

#: Counts tokens in a string. None -> char budgets only (the pre-token behaviour).
TokenCounter = Callable[[str], int]


def _fit_pieces(
    build: Callable[[int], list],
    text_of: Callable,
    whole: str,
    max_chars: int,
    count_tokens: TokenCounter | None,
    max_tokens: int,
) -> list:
    """Run ``build(char_cap)`` with a cap that actually bounds TOKENS.

    The splitting mechanics everywhere in this module slice by character index,
    which is the right mechanism (token-aligned slicing would need offset
    mapping); what was wrong was the fixed cap. So: derive this unit's char cap
    from its OWN density (``len/tokens``), build, verify every piece, and shrink
    the cap toward the worst offender until all pieces fit or the floor is hit.
    Converges in ≤3 rounds in practice because density within one unit is stable.

    With no counter this is exactly the old behaviour: one build at ``max_chars``.
    """
    if count_tokens is None:
        return build(max_chars)
    total = count_tokens(whole)
    if total <= max_tokens:
        # The whole unit fits one window — never split it, whatever its chars.
        return build(max(len(whole) + 1, 1))
    density = len(whole) / max(total, 1)
    cap = max(300, min(max_chars, int(max_tokens * density * 0.85)))
    pieces = build(cap)
    for _ in range(3):
        worst = max((count_tokens(text_of(p)) for p in pieces), default=0)
        if worst <= max_tokens or cap <= 300:
            break
        cap = max(300, int(cap * max_tokens / worst * 0.9))
        pieces = build(cap)
    return pieces

#: Default floor for a unit; shorter units are reported as skipped, never
#: silently dropped. Captions like "Figure 1 Flow chart." carry no retrievable
#: information and embed as near-noise (0.5% of units at this default).
DEFAULT_MIN_UNIT_CHARS = 40

#: Every metadata key this module can emit. The downstream ``JsonlLoader``
#: pass-through allow-list is expected to cover these.
JATS_METADATA_KEYS = (
    "doi", "pmid", "pmcid", "title", "authors", "keywords", "journal",
    "publisher", "year", "licence", "sha256", "source_url", "content_type",
    "section_title", "graphic", "abstract", "n_tables", "n_figures",
)

#: The raw metadata keys a JATS corpus opts back in on top of the enriched
#: index subset (``JsonlLoader(passthrough_keys=...)``): the keys
#: ``enrich.EnrichedDoc`` has no slot for. Exactly the ``metadata_passthrough``
#: default of ``cwl/jats-ingest.cwl`` (path C), so the in-process
#: ``JatsLoader`` (#828 PR 5a) stamps the keys path C stamps.
#: ``content_type`` is load-bearing: without it "filter to tables" is
#: unanswerable at query time.
JATS_PASSTHROUGH_KEYS = (
    "content_type", "pmcid", "pmid", "journal", "publisher", "licence",
    "section_title", "sha256", "source_url", "graphic",
)

#: Record kinds, i.e. the values ``content_type`` can take.
CONTENT_TYPES = ("article", "table", "figure")

# Unicode lookalikes that break cross-format matching and defeat substring
# matchers. The zero-width joiners are not cosmetic: a licence fragment used them
# to evade the boilerplate matcher.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_LOOKALIKE = {
    "µ": "μ",  # MICRO SIGN -> GREEK SMALL LETTER MU
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ",
}
_TRANS = str.maketrans(_LOOKALIKE)
_WS = re.compile(r"[ \t]+")
_BLANK = re.compile(r"\n{3,}")
_SENT = re.compile(r"(?<=[.!?])\s+")


def norm(s: str) -> str:
    """Normalise text once, at ingest, so the same quantity matches itself."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.translate(_ZERO_WIDTH).translate(_TRANS)
    s = _WS.sub(" ", s)
    return _BLANK.sub("\n\n", s).strip()


def itext(el) -> str:
    """All descendant text of ``el``, normalised. ``""`` for ``None``."""
    return norm("".join(el.itertext())) if el is not None else ""


def clean_itext(el) -> str:
    """``itertext()`` with every ``<table-wrap>``/``<fig>`` pruned at ANY depth.

    JATS legally nests floats inside ``<p>``. A direct-children-only check leaves
    the entire grid concatenated into the sentence stream -- and, because
    :func:`collect_floats` walks the whole tree, ALSO emits it as a unit record,
    so the table lands in the corpus twice: once as cell soup wrecking a prose
    chunk, once clean. Measured on 3,000 harvested articles: 3,302 floats sit
    below a non-``<sec>`` parent, 3,289 of them inside ``<p>``, affecting 544
    documents (~18%).
    """
    if el is None:
        return ""
    parts: list[str] = []

    def walk(e) -> None:
        if e.text:
            parts.append(e.text)
        for c in e:
            if c.tag not in LIFT:
                walk(c)
            if c.tail:
                parts.append(c.tail)

    walk(el)
    return norm("".join(parts))


# --------------------------------------------------------------------- tables

def _row_cells(tr) -> list[str]:
    return [norm("".join(c.itertext())) for c in tr if c.tag in ("td", "th")]


def table_rows(table) -> tuple[list[str], list[list[str]]]:
    """Return ``(header_cells, body_rows)``. Header falls back to the first row."""
    if table is None:
        return [], []
    head: list[str] = []
    thead = table.find("thead")
    if thead is not None:
        hrows = [_row_cells(tr) for tr in thead.findall(".//tr")]
        hrows = [r for r in hrows if any(r)]
        if hrows:
            # Merge ALL header rows column-wise. Taking only hrows[0] dropped
            # every additional header row (body rows come from <tbody>, which
            # does not contain them): 300 of 1,077 sampled tables (28%) have a
            # multi-row <thead>, so the real column names vanished and the
            # rendered header/body arity did not even match.
            width = max(len(r) for r in hrows)
            head = [" ".join(p for r in hrows
                             if len(r) > i and (p := r[i].strip()))
                    for i in range(width)]
    body_src = table.find("tbody")
    trs = (body_src if body_src is not None else table).findall(".//tr")
    rows = [c for c in (_row_cells(tr) for tr in trs) if any(c)]
    if not head and rows:
        head, rows = rows[0], rows[1:]
    return head, rows


def render_rows(head: list[str], rows: list[list[str]]) -> str:
    """Render a header + rows as a pipe-delimited grid (markdown-ish)."""
    out = []
    if head:
        out.append(" | ".join(head))
        out.append(" | ".join("---" for _ in head))
    out.extend(" | ".join(r) for r in rows)
    return "\n".join(out)


def _split_long_pieces(label: str, body: str, max_chars: int) -> list[tuple[str, int]]:
    """:func:`split_long` plus, per piece, the offset where its ``body`` part
    starts (the length of the repeated ``label`` prefix; the piece's length
    when it is the label alone). Offsets are lengths of what was appended."""
    prefix = f"{label} " if label else ""
    budget = max(200, max_chars - len(prefix))
    out: list[tuple[str, int]] = []
    cur = ""
    for s in _SENT.split(body):
        if cur and len(cur) + len(s) + 1 > budget:
            out.append((prefix + cur.strip(), len(prefix)))
            cur = ""
        cur = f"{cur} {s}".strip() if cur else s
        while len(cur) > budget:            # a single sentence over budget
            out.append((prefix + cur[:budget].strip(), len(prefix)))
            cur = cur[budget:].strip()
    if cur:
        out.append((prefix + cur.strip(), len(prefix)))
    if not out:
        only = prefix.strip()
        return [(only, len(only))]
    return out


def split_long(label: str, body: str, max_chars: int) -> list[str]:
    """Split an over-long unit on sentence boundaries, repeating ``label``.

    A unit is stored as ONE chunk, so an unsplit 14k-char caption would embed as
    a ~3,600-token blob -- inside SFR's 4080 window, so it would not error, but
    wildly out of family with a 512-token collection and a poor retrieval unit.
    """
    return [t for t, _ in _split_long_pieces(label, body, max_chars)]


class UnitLayout(NamedTuple):
    """Where the parts of ONE table/figure unit record's ``text`` lie (#828 §9.2).

    ``label`` is the raw ``<label>`` text (``""`` when the float has none); the
    unit text starts with it by construction. ``[caption_start, caption_end)``
    is the caption (plus, for a bitmap-only table, its ``<table-wrap-foot>``),
    empty (``caption_start == caption_end``) when there is none.
    ``[body_start, body_end)`` is the rendered grid (with the foot after it on a
    table's last piece), ``None`` for a figure and for a piece with no grid.
    Offsets come from the lengths of the strings the builders join, never from
    a search — the same rule as :class:`Span`. Consumed by
    :func:`ragstack.ingestion.jats_structure.unit_structure`."""

    label: str
    caption_start: int
    caption_end: int
    body_start: int | None
    body_end: int | None


def _head_caption(label: str, caption: str) -> tuple[int, int]:
    """The caption's range inside ``" ".join(x for x in (label, caption) if x)``."""
    if label and caption:
        return len(label) + 1, len(label) + 1 + len(caption)
    if label:
        return len(label), len(label)
    return 0, len(caption)


def _after_label(label: str, text: str) -> int:
    """Where the text after ``label`` and its one-space separator starts."""
    if not label:
        return 0
    return len(label) + 1 if len(text) > len(label) else len(label)


def _reset_layouts(layouts: dict[str, UnitLayout] | None, base: str) -> None:
    """Drop this unit's entries before a build attempt: :func:`_fit_pieces`
    rebuilds, and a first attempt's ``-part-3`` must not outlive a final build
    with two parts. Other units' entries in a shared dict are left alone."""
    if layouts is None:
        return
    for key in [k for k in layouts if k == base or k.startswith(f"{base}-part-")]:
        del layouts[key]


def _record_layouts(layouts: dict[str, UnitLayout] | None, suffixes: Iterable[str],
                    lays: Iterable[UnitLayout]) -> None:
    if layouts is not None:
        for suffix, lay in zip(suffixes, lays, strict=True):
            layouts[suffix] = lay


def table_units(
    tw,
    idx: int,
    max_chars: int = DEFAULT_MAX_CHARS,
    count_tokens: TokenCounter | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    layouts: dict[str, UnitLayout] | None = None,
) -> list[tuple[str, str]]:
    """Serialise one ``<table-wrap>`` into ``(suffix, text)`` pieces.

    Oversized tables are split BY ROW with the caption and header row repeated
    on every piece -- the stock chunker preserves no header context, so rows
    would otherwise reach the embedder with no column names. "Oversized" is
    measured in TOKENS when ``count_tokens`` is given (see ``DEFAULT_MAX_CHARS``
    for why a char cap alone under-split 32% of real tables), else in chars.

    ``layouts``, when a dict, receives a :class:`UnitLayout` per returned piece,
    keyed by its suffix (the pieces themselves are unchanged).
    """
    base = f"table-{idx}"
    label = itext(tw.find("label"))
    caption = itext(tw.find("caption"))
    # <table-wrap-foot> holds the abbreviation legends and statistical notes that
    # make a table interpretable. 61% of table-wraps have one; because the whole
    # table-wrap is lifted out of the prose, omitting it here deleted that text
    # from the corpus entirely.
    foot = " ".join(x for x in (itext(f) for f in tw.findall("table-wrap-foot")) if x)
    head_txt = " ".join(x for x in (label, caption) if x).strip()
    table = tw.find("table")
    if table is None:  # bitmap-only table: caption is all there is, honestly
        only = " ".join(x for x in (head_txt, foot) if x).strip()
        if not only:
            _reset_layouts(layouts, base)
            return []
        # Oversized caption-only wraps split like prose, with the short <label>
        # ("Table 3") as the repeated prefix and caption+foot as the body — NOT
        # head_txt as both, which would duplicate the caption into every piece.
        body = " ".join(x for x in (caption, foot) if x).strip() or only

        def build_caption_only(cap: int) -> list[tuple[str, str]]:
            _reset_layouts(layouts, base)
            if len(only) <= cap:
                pieces = [(base, only)]
                lays = [UnitLayout(label, _after_label(label, only), len(only), None, None)]
            else:
                split = _split_long_pieces(label, body, cap)
                pieces = [(f"{base}-part-{i}", t) for i, (t, _) in enumerate(split, 1)]
                lays = [UnitLayout(label, cs, len(t), None, None) for t, cs in split]
            _record_layouts(layouts, (s for s, _ in pieces), lays)
            return pieces

        return _fit_pieces(build_caption_only, lambda p: p[1], only, max_chars,
                           count_tokens, max_tokens)

    head, rows = table_rows(table)
    whole = render_rows(head, rows)
    tail = f"\n\n{foot}" if foot else ""
    raw = f"{head_txt}\n\n{whole}{tail}"
    full = raw.strip()
    cap_start, cap_end = _head_caption(label, caption)

    def single() -> UnitLayout:
        # ``full`` is ``raw`` stripped: shift by what the strip removed in front
        # (only when head_txt is empty) and clamp to what it left at the end.
        lead = len(raw) - len(raw.lstrip())
        body_start = min(max(0, len(head_txt) + len(_SEP) - lead), len(full))
        if body_start >= len(full):
            return UnitLayout(label, cap_start, cap_end, None, None)
        return UnitLayout(label, cap_start, cap_end, body_start, len(full))

    def build(cap: int) -> list[tuple[str, str]]:
        _reset_layouts(layouts, base)
        if len(head_txt) + len(whole) + len(tail) <= cap:
            _record_layouts(layouts, [base], [single()])
            return [(base, full)]
        if not rows:  # no parseable rows but over budget -- treat the blob as prose
            split = _split_long_pieces(head_txt, whole + tail, cap)
            pieces = [(f"{base}-part-{i}", t) for i, (t, _) in enumerate(split, 1)]
            _record_layouts(layouts, (s for s, _ in pieces), [
                UnitLayout(label, cap_start, cap_end, *((bs, len(t)) if bs < len(t)
                                                        else (None, None)))
                for t, bs in split])
            return pieces
        prefix = f"{head_txt}\n\n" if head_txt else ""
        hdr = render_rows(head, []) + "\n" if head else ""
        budget = max(200, cap - len(prefix) - len(hdr))
        pieces = []
        cur: list[str] = []
        cur_len, part = 0, 1
        for r in rows:
            line = " | ".join(r)
            if cur and cur_len + len(line) > budget:
                pieces.append((f"{base}-part-{part}",
                               f"{prefix}{hdr}" + "\n".join(cur)))
                part, cur, cur_len = part + 1, [], 0
            while len(line) > budget:  # a single row wider than the whole budget
                pieces.append((f"{base}-part-{part}",
                               f"{prefix}{hdr}" + line[:budget]))
                part, line = part + 1, line[budget:]
            if not line:
                continue
            cur.append(line)
            cur_len += len(line) + 1
        if cur:
            pieces.append((f"{base}-part-{part}", f"{prefix}{hdr}" + "\n".join(cur)))
        if foot and pieces:  # keep the legend with the last piece, not lost
            s, t = pieces[-1]
            pieces[-1] = (s, f"{t}\n\n{foot}")
        # Every piece is prefix + (header + rows [+ foot]): the body starts
        # right after the repeated caption block.
        _record_layouts(layouts, (s for s, _ in pieces), [
            UnitLayout(label, cap_start, cap_end, len(prefix), len(t)) for _, t in pieces])
        return pieces

    return _fit_pieces(build, lambda p: p[1], full, max_chars, count_tokens, max_tokens)


def fig_units(
    fig,
    idx: int,
    max_chars: int = DEFAULT_MAX_CHARS,
    count_tokens: TokenCounter | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    layouts: dict[str, UnitLayout] | None = None,
) -> list[tuple[str, str, str]]:
    """``[(suffix, text, graphic_href)]`` for one ``<fig>``.

    Captions are 8.5% of body text and are real prose; the image bytes are
    separate S3 objects and are not needed to retrieve. ``layouts`` is as in
    :func:`table_units` (a figure has no body range).
    """
    base = f"figure-{idx}"
    label = itext(fig.find("label"))
    caption = itext(fig.find("caption"))
    g = fig.find(".//graphic")
    href = (g.get(XLINK) or "") if g is not None else ""
    whole = " ".join(x for x in (label, caption) if x).strip()

    def build(cap: int) -> list[tuple[str, str, str]]:
        _reset_layouts(layouts, base)
        if len(whole) <= cap:
            _record_layouts(layouts, [base],
                            [UnitLayout(label, *_head_caption(label, caption), None, None)])
            return [(base, whole, href)]
        split = _split_long_pieces(label, caption, cap)
        pieces = [(f"{base}-part-{i}", t, href) for i, (t, _) in enumerate(split, 1)]
        _record_layouts(layouts, (p[0] for p in pieces),
                        [UnitLayout(label, cs, len(t), None, None) for t, cs in split])
        return pieces

    return _fit_pieces(build, lambda p: p[1], whole, max_chars, count_tokens, max_tokens)


# ----------------------------------------------------------------------- body

#: The separator every prose level joins its non-empty parts with. Span offsets
#: are accumulated from ``len(part)`` and ``len(_SEP)``, never searched for.
_SEP = "\n\n"


class Span(NamedTuple):
    """One XML element's place in the article record text (#318 P3, #688 §5).

    ``start``/``end`` are offsets into the article record's ``text`` — the
    ``"\\n\\n".join(abstract, body)`` string :func:`article_records` emits — so
    ``text[start:end]`` is exactly the element's normalised contribution:

    * ``kind="abstract"`` — ``clean_itext(abstract)``; one leaf span, depth 0.
    * ``kind="sec"`` — ``section_text(sec, depth)``: the markdown heading (if the
      ``<sec>`` has a non-empty title) plus every child. A container: it covers
      its children's spans.
    * ``kind="title"`` — the ``"## Title"`` heading line of its ``<sec>``.
    * any other ``kind`` — the element tag of a block child of ``<body>``/``<sec>``
      (``"p"``, ``"list"``, ``"disp-quote"``, ``"label"``…): ``clean_itext(el)``.

    ``path`` is a structural path, 1-based among same-tag siblings and counting
    EVERY sibling of that tag (lifted floats and empty elements included), so it
    is XPath-shaped and stable without XML ids: ``body/sec[2]/sec[1]/p[3]``
    resolves with ``root.find(".//body").find("sec[2]/sec[1]/p[3]")``. Its first
    step names the element :func:`article_prose` selected: ``abstract`` is
    ``root.find(".//article-meta//abstract")``, ``body`` is ``root.find(".//body")``.

    ``depth`` is the number of enclosing ``<sec>`` elements: the abstract,
    body-lead paragraphs and top-level ``<sec>`` are 0 (so a ``sec`` span's
    depth is the ``depth`` argument :func:`section_text` was called with, and its
    heading level is ``min(depth + 2, 6)``); a ``<p>`` or ``<title>`` inside a
    top-level ``<sec>`` is 1. The depth-0 spans therefore partition the record.

    ``title`` is the section title text (no ``#``) on ``sec`` and ``title``
    spans, ``None`` when the ``<sec>`` has no title or it normalises to empty —
    an untitled section is a first-class span, not a gap. ``None`` on leaves.

    ``sec_type`` is the ``<sec>``'s ``sec-type`` attribute, stripped, on ``sec``
    spans (``None`` when absent or blank, and on every other kind). It is read
    verbatim — the ``declared/1`` role map that interprets it lives in
    :mod:`ragstack.ingestion.jats_structure` (#828 §5).

    EMPTY ELEMENTS GET NO SPAN. An element whose normalised text is ``""`` is
    dropped by the ``if p`` join filter and contributes nothing to the text, so
    there is no offset it could honestly own; every emitted span has
    ``start < end``. Lifted floats (``<table-wrap>``/``<fig>``) get no span
    either — they are separate unit records.

    ``._asdict()`` gives the JSON row shape ``scripts/jats_extract.py
    --spans-out`` writes.
    """

    kind: str
    path: str
    xml_id: str | None
    start: int
    end: int
    title: str | None
    depth: int
    sec_type: str | None = None


def _section_walk(sec, depth: int, path: str, base: int,
                  spans: list[Span] | None) -> str:
    """:func:`section_text`'s single implementation, optionally recording spans.

    ``base`` is the offset of this section's first character in the final text;
    child offsets are accumulated from the same ``len``s the ``_SEP`` join
    produces, so they are exact by construction. ``spans is None`` builds text
    only (the default ingest path pays no span bookkeeping).
    """
    parts: list[str] = []
    pos = base  # where the next non-empty part would start, before its separator

    def take(text: str) -> int:
        """Append a non-empty part; return its start offset."""
        nonlocal pos
        start = pos + (len(_SEP) if parts else 0)
        parts.append(text)
        pos = start + len(text)
        return start

    def next_start() -> int:
        return pos + (len(_SEP) if parts else 0)

    first = len(spans) if spans is not None else 0
    head_title: str | None = None
    title = sec.find("title")
    if title is not None:
        t = itext(title)
        if t:
            head_title = t
            heading = ("#" * min(depth + 2, 6)) + " " + t
            s = take(heading)
            if spans is not None:
                spans.append(Span("title", f"{path}/title[1]", title.get("id"),
                                  s, s + len(heading), t, depth + 1))
    seen: dict[str, int] = {}
    for child in sec:
        n = seen[child.tag] = seen.get(child.tag, 0) + 1
        if child.tag == "title":
            continue
        if child.tag in LIFT:
            continue
        cpath = f"{path}/{child.tag}[{n}]"
        if child.tag == "sec":
            text = _section_walk(child, depth + 1, cpath, next_start(), spans)
        else:
            text = clean_itext(child)   # prunes floats nested inside <p>
            if text and spans is not None:
                s = next_start()
                spans.append(Span(child.tag, cpath, child.get("id"),
                                  s, s + len(text), None, depth + 1))
        if text:          # the ``if p`` filter: an empty part takes no separator
            take(text)
    out = _SEP.join(parts)
    if out and spans is not None:
        # Pre-order: the container precedes its children (same start, larger end).
        spans.insert(first, Span("sec", path, sec.get("id"), base, base + len(out),
                                 head_title, depth,
                                 (sec.get("sec-type") or "").strip() or None))
    return out


def section_text(sec, depth: int = 0) -> str:
    """Body text for one ``<sec>``, with tables/figures removed and titles kept.

    Section titles are retained inline: the XML carries 952 labelled sections
    across 50 articles (median 20/article), which is chunk-boundary information
    that does not survive PDF extraction at all. (An UNTITLED ``<sec>`` leaves
    no heading — its boundary survives only in :func:`article_spans`.)
    """
    return _section_walk(sec, depth, "sec", 0, None)


def _prose_walk(root, spans: list[Span] | None) -> tuple[str, str]:
    """:func:`article_prose`'s single implementation, optionally recording spans
    with offsets into the RECORD text ``"\\n\\n".join(x for x in (abstract, body) if x)``."""
    abst = root.find(".//article-meta//abstract")
    abstract = clean_itext(abst)
    if abstract and spans is not None:
        spans.append(Span("abstract", "abstract", abst.get("id"),
                          0, len(abstract), None, 0))
    body = root.find(".//body")
    if body is None:
        return abstract, ""
    # The body starts after the abstract and its separator — or at 0 when the
    # abstract is empty and the record join's ``if x`` filter drops it.
    pos = len(abstract) + len(_SEP) if abstract else 0
    chunks: list[str] = []
    seen: dict[str, int] = {}
    for child in body:
        n = seen[child.tag] = seen.get(child.tag, 0) + 1
        if child.tag in LIFT:
            continue
        start = pos + (len(_SEP) if chunks else 0)
        cpath = f"body/{child.tag}[{n}]"
        if child.tag == "sec":
            text = _section_walk(child, 0, cpath, start, spans)
        else:
            text = clean_itext(child)
            if text and spans is not None:
                spans.append(Span(child.tag, cpath, child.get("id"),
                                  start, start + len(text), None, 0))
        if text:
            chunks.append(text)
            pos = start + len(text)
    return abstract, _SEP.join(chunks)


def article_prose(root) -> tuple[str, str]:
    """``(abstract, body_text)`` with floats lifted out."""
    return _prose_walk(root, None)


def article_spans(root) -> tuple[str, list[Span]]:
    """``(prose_text, spans)``: the article record's text and every element's span.

    ``prose_text`` is byte-identical to the ``text`` of the ``content_type=
    "article"`` record :func:`article_records` builds (``""`` when the article
    has no prose and no record is emitted). ``spans`` is in document pre-order
    (sorted by ``start``; a container before its first child) — see
    :class:`Span` for the fields and the empty-element policy. Offsets are
    accumulated during the same walk that builds the text, never recovered by
    string search, so repeated text cannot alias.
    """
    spans: list[Span] = []
    abstract, body = _prose_walk(root, spans)
    return _SEP.join(x for x in (abstract, body) if x), spans


def collect_floats(root) -> tuple[list, list]:
    """Every ``<table-wrap>`` / ``<fig>`` in the document, wherever it lives.

    MDPI parks them in ``<floats-group>``, OUTSIDE ``<body>``. Keeping only
    front+abstract+body loses 18-27% of those articles' text, so this walks the
    whole tree rather than just the body.
    """
    return root.findall(".//table-wrap"), root.findall(".//fig")


def front_meta(root) -> dict:
    """Bibliographic metadata from ``<front>`` (ids, authors, journal, licence).

    ``year`` is an ``int`` (or ``None`` when the article declares no parseable,
    plausible one) — see :data:`JATS_METADATA_KEYS`. Every other value here is a
    string on purpose; ``pmid``/``pmcid`` in particular stay strings, because
    they are identifiers and nothing range-filters them.
    """
    ids = {e.get("pub-id-type"): itext(e)
           for e in root.findall(".//article-meta//article-id")}
    # The PMC id is the FIRST ``pmc``/``pmcid`` article-id (document order),
    # the one :func:`article_pmcid` keys the records on (record_key step 2),
    # so ``metadata.pmcid`` and the record key never name different ids.
    # ``doi``/``pmid`` keep the dict's last-wins choice (unchanged output).
    pmcid = _first_pmc_article_id(root)
    authors = []
    for c in root.findall(".//article-meta//contrib"):
        if (c.get("contrib-type") or "author") != "author":
            continue
        sn, gn = c.find(".//surname"), c.find(".//given-names")
        nm = " ".join(x for x in (itext(gn), itext(sn)) if x)
        if nm:
            authors.append(nm)
    # INT, not the raw string. ``<pub-date>/<year>`` is text in the XML, and
    # emitting it verbatim produced documents that no correct filter can reach:
    # ``year`` is an integer field in the filter grammar (stores/filters.py,
    # #471), matched by type and never coerced. The first pub-date that yields a
    # PLAUSIBLE year wins — an unparseable or out-of-range one is skipped rather
    # than taken, so a malformed <year>2o19</year> falls through to the next
    # pub-date instead of poisoning the record.
    year = None
    for d in root.findall(".//article-meta//pub-date"):
        y = coerce_year(itext(d.find("year")))
        if y is not None:
            year = y
            break
    lic = root.find(".//permissions/license")
    licence = ""
    if lic is not None:
        licence = (lic.get(XLINK) or itext(lic.find("license-p")) or "")[:400]
    kws = [itext(k) for k in root.findall(".//kwd")]
    return {
        "doi": ids.get("doi", ""),
        "pmid": ids.get("pmid", ""),
        "pmcid": pmcid,
        "title": itext(root.find(".//article-meta//article-title")),
        "authors": "; ".join(authors),
        "keywords": "; ".join(k for k in kws if k),
        "journal": itext(root.find(".//journal-title")),
        "publisher": itext(root.find(".//publisher-name")),
        "year": year,
        "licence": licence,
    }


def merge_manifest(meta: dict, pmcid: str, manifest: dict | None) -> dict:
    """Fold the harvest manifest row into the XML-derived metadata.

    The manifest is authoritative for provenance the XML does not carry
    (``sha256``, ``source_url``) and a fallback for what it may be missing
    (``doi_xml`` -> ``doi``, ``licence``). ``sha256``/``source_url`` are always
    present as keys (possibly empty) so the record shape is stable.
    """
    from ragstack.ingestion.work_identity import normalize_pmcid  # no import cycle

    manifest = manifest or {}
    # Only a key that IS a PMC id stands in for a missing pmcid — written as
    # given (``PMC123``, or a shard's explicit bare ``8092463``, as before #828
    # PR 5a). A path key (``/corpus/paper.xml``, record_key step 3) is a record
    # id, not a PMCID, and ``normalize_pmcid`` rejects it.
    meta["pmcid"] = meta.get("pmcid") or (pmcid if normalize_pmcid(pmcid) else "")
    for k_src, k_dst in (("sha256", "sha256"), ("source_url", "source_url"),
                         ("licence", "licence"), ("doi_xml", "doi")):
        v = manifest.get(k_src)
        if v and not meta.get(k_dst):
            meta[k_dst] = v
    meta["source_url"] = manifest.get("source_url", "")
    meta["sha256"] = manifest.get("sha256", "")
    return meta


# ----------------------------------------------------------------- record key

#: A filename stem that IS a PMC id (``PMC8092463``), case-insensitive.
_PMC_STEM = re.compile(r"^PMC\d+$", re.IGNORECASE)
#: An ``<article-id pub-id-type="pmc">`` value: ``PMC123`` or a bare ``123``.
_PMC_ARTICLE_ID = re.compile(r"^\s*(?:PMC)?(\d+)\s*$", re.IGNORECASE)
#: The suffixes a JATS file carries (``.nxml`` is the PMC OA bulk package's).
JATS_SUFFIXES = (".xml", ".nxml")


#: The unit-record ids :func:`article_records` emits: ``<key>#table-N``,
#: ``<key>#figure-N``, each optionally ``-part-M`` (an oversized unit split).
#: ``<key>`` is a PMC id or, for a file with none (:func:`record_key` step
#: 3), the file's resolved path — so it may hold ``/``
#: (``/corpus/paper.xml#table-1``). Nothing else is meant to match — not a
#: JSONL record ``corpus#0001``, not an uploaded file named ``PMC1#supp.pdf``.
#: A loaded file's own path normally cannot: it ends in a registered suffix.
#: The exception is a suffix-less TARGET reached through a link, which
#: dispatches on the link's suffix (``LoaderRegistry._dispatch``):
#: ``paper.xml → blobs/abc#table-1`` keys its article on
#: ``/…/blobs/abc#table-1``, which this grammar reads as a unit of
#: ``/…/blobs/abc`` (contrived; that record is then treated as a unit whose
#: article is absent — it keeps its own record's ids).
UNIT_ID = re.compile(r"^(.+)#(?:table|figure)-\d+(?:-part-\d+)?$")

#: The work ids a unit record takes from its article (:func:`inherit_article_ids`).
WORK_ID_KINDS = ("doi", "pmcid", "pmid")


def unit_root(source: str) -> str:
    """The record a JATS unit record was split from (``"PMC1#table-1"`` →
    ``"PMC1"``), or ``""`` when ``source`` is not a unit id of that grammar
    (:data:`UNIT_ID`)."""
    m = UNIT_ID.match(source or "")
    return m.group(1) if m else ""


def inherit_article_ids(documents: Iterable) -> None:
    """Give each JATS unit record its article's work ids, in place (#975
    round 3, argo S1a). A unit is not an independent identity source: its
    text is a table or caption that may cite other works, so the guard's
    header scan and the enricher's text leg never read it; instead, after the
    ARTICLE has been filled, its present ``doi``/``pmcid``/``pmid`` (and the
    ``doi_source`` that came with the DOI) are copied onto every unit
    (:func:`unit_root`) whose article is among ``documents`` — within one
    load, one source. A unit whose article is not present keeps only what
    its record carried. Duck-typed (``.source``, ``.metadata``) so this
    module stays free of the model layer."""
    docs = list(documents)
    articles = {d.source: d for d in docs if d.source and not unit_root(d.source)}
    for d in docs:
        art = articles.get(unit_root(d.source))
        if art is None:
            continue
        for kind in WORK_ID_KINDS:
            value = art.metadata.get(kind)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if d.metadata.get(kind) != value:
                d.metadata[kind] = value
                if kind == "doi" and art.metadata.get("doi_source"):
                    d.metadata["doi_source"] = art.metadata["doi_source"]


def pmc_stem(path: str | Path) -> str:
    """The filename stem up to the first ``.`` when it is a PMC id
    (``PMC8092463.v2.xml`` → ``PMC8092463``), else ``""``. Step 1 of
    :func:`record_key`, the one step that needs no parse."""
    stem = Path(path).name.split(".")[0]
    return stem if _PMC_STEM.match(stem) else ""


_PMC_ID_TYPES = ("pmc", "pmcid")


def _first_pmc_article_id(root) -> str:
    """The raw text of the FIRST ``<article-id>`` in an ``<article-meta>``
    whose ``pub-id-type`` is ``pmc``/``pmcid`` (document order), or ``""``."""
    for e in root.findall(".//article-meta//article-id"):
        if e.get("pub-id-type") in _PMC_ID_TYPES:
            return itext(e)
    return ""


def _pmc_of(raw: str) -> str:
    m = _PMC_ARTICLE_ID.match(raw or "")
    return f"PMC{int(m.group(1))}" if m and int(m.group(1)) > 0 else ""


def article_pmcid(root) -> str:
    """The article's own PMC id as ``PMC<digits>``: the FIRST
    ``<article-id pub-id-type="pmc">`` (or ``"pmcid"``) in ``<article-meta>``
    — the one :func:`front_meta` writes to ``metadata.pmcid`` — normalised, or
    ``""`` (none, or the first is not a PMC id)."""
    return _pmc_of(_first_pmc_article_id(root))


def scan_article_pmcid(path: str | Path) -> str:
    """:func:`article_pmcid` of a file, STREAMED: ``iterparse`` stops at the
    first ``pmc``/``pmcid`` article-id inside an ``<article-meta>``, so the
    usual front-matter id costs a few KB. Same element, same normalisation
    as :func:`article_pmcid` of the parsed file. A file with no such id is
    read to the end (an ``<article-meta>`` may sit in a later
    ``<sub-article>``, and :func:`article_pmcid` would find it there), but
    never as a tree: every finished element is cleared and detached from its
    parent (#975 round 3 nit 2), except inside an ``<article-id>``, whose
    text is read whole at its end. Raises what ``iterparse`` raises
    (``ET.ParseError``, ``OSError``); the caller bounds the file's size."""
    meta = 0  # how many <article-meta> elements are open
    in_id = 0  # how many <article-id> elements are open
    stack: list = []  # the open elements, root first
    for event, el in ET.iterparse(str(path), events=("start", "end")):
        if event == "start":
            stack.append(el)
            if el.tag == "article-meta":
                meta += 1
            elif el.tag == "article-id":
                in_id += 1
            continue
        stack.pop()
        if el.tag == "article-meta":
            meta -= 1
        elif el.tag == "article-id":
            in_id -= 1
            if meta and el.get("pub-id-type") in _PMC_ID_TYPES:
                return _pmc_of(itext(el))
        if in_id:
            continue  # part of an article-id's text: kept until it is read
        el.clear()
        # Its earlier siblings were detached at their own ends, so it is its
        # parent's FIRST child. (Not the last: the parser runs ahead of the
        # events by a read buffer, so later siblings are already attached.)
        if stack and len(stack[-1]) and stack[-1][0] is el:
            del stack[-1][0]
    return ""


def record_key(path: str | Path, xml_pmcid: str | None = None) -> str:
    """The record key of one JATS file: the article record's ``path`` and the
    prefix of its unit records (``<key>#table-1``), from which every document
    id derives (#828 PR 5a). ONE rule for every route — ``JatsLoader``
    (in process and in the GoWe extract step), ``jats_extract`` and the API's
    same-document identity:

    1. the filename stem up to the first ``.`` when it is a PMC id
       (``PMC8092463.xml``, ``PMC8092463.nxml``, ``PMC8092463.v2.xml`` →
       ``PMC8092463``), taken LITERALLY — ``pmc4242.xml`` → ``pmc4242``, case
       kept, leading zeros kept (step 2 normalises; step 1 never did) — so it
       is byte-identical to the key every PMC-named file has always had and
       existing collections keep their ids;
    2. else the article's ``<article-id pub-id-type="pmc">`` (``xml_pmcid``,
       normalised to ``PMC<digits>`` without leading zeros, as
       ``work_identity.normalize_pmcid`` does);
    3. else the file's RESOLVED PATH (``/corpus/plos/journal.pone.0012345.xml``)
       — exactly the key ``PdfLoader``/``TextFileLoader`` give a file at that
       path, so ``deterministic_doc_id`` of it is the id a PDF there would get.

    Steps 1–2 give two files one key only when they name the same PMC id —
    the same work, so a replace is right. Step 3 gives two files one key only
    when they are one file. No key derived from a bare name can make two
    different works collide (``*/main.xml``, two uploads ``paper.xml``). The
    price, as for PDFs: a step-3 file's ids follow its path, so a moved file,
    or a GoWe run that stages the input under a fresh path, gets new ids."""
    stem = pmc_stem(path)
    if stem:
        return stem
    pmc = _pmc_of(xml_pmcid or "")
    if pmc:
        return pmc
    return str(Path(path).resolve())


def file_record_key(path: str | Path) -> str:
    """:func:`record_key` of a file on disk: reads it for step 2 only when
    the name is not a PMC id (:func:`scan_article_pmcid`, streamed). An
    unreadable/unparseable file falls through to step 3 (its ingest fails on
    the parse anyway)."""
    if pmc_stem(path):
        return pmc_stem(path)
    try:
        xml_pmcid = scan_article_pmcid(path)
    except Exception:  # noqa: BLE001 - unreadable/unparseable: the path
        xml_pmcid = ""
    return record_key(path, xml_pmcid)


def _local(tag) -> str:
    return str(tag).rsplit("}", 1)[-1]


def is_article_root(root) -> bool:
    """Whether the document element is ``<article>`` (namespace-tolerant)."""
    return _local(root.tag) == "article"


def unwrap_articleset(root):
    """``(article, None)`` for the element to convert, or ``(None, reason)``.

    NCBI ``efetch db=pmc`` returns ``<pmc-articleset><article>…</article>
    </pmc-articleset>``, commonly saved as ``PMC123.xml``. A set holding
    exactly ONE ``<article>`` is that article (unwrapped, on every route); a
    set holding none or several is refused (``not a JATS article (…)``) —
    one file, one article, one record key. Any other root is returned as is."""
    if _local(root.tag) != "pmc-articleset":
        return root, None
    arts = [c for c in root if _local(c.tag) == "article"]
    if len(arts) == 1:
        return arts[0], None
    return None, (f"not a JATS article (<pmc-articleset> holding {len(arts)} "
                  f"articles; one article per file)")


# -------------------------------------------------------------------- records

_Records = tuple[list[dict], list[dict]]
_RecordsWithSpans = tuple[list[dict], list[dict], list[Span],
                          dict[str, tuple[UnitLayout, bool]]]


@overload
def article_records(
    root, pmcid: str, manifest: dict | None = ..., max_chars: int = ...,
    min_unit_chars: int = ..., count_tokens: TokenCounter | None = ...,
    max_tokens: int = ..., *, with_spans: Literal[False] = ...,
) -> _Records: ...
@overload
def article_records(
    root, pmcid: str, manifest: dict | None = ..., max_chars: int = ...,
    min_unit_chars: int = ..., count_tokens: TokenCounter | None = ...,
    max_tokens: int = ..., *, with_spans: Literal[True],
) -> _RecordsWithSpans: ...
def article_records(
    root,
    pmcid: str,
    manifest: dict | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_unit_chars: int = DEFAULT_MIN_UNIT_CHARS,
    count_tokens: TokenCounter | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    *,
    with_spans: bool = False,
) -> _Records | _RecordsWithSpans:
    """One parsed JATS ``root`` -> ``(records, skipped)``.

    ``records`` are JSONL-ready ``{"text", "path", "metadata"}`` dicts; ``skipped``
    are ``{"path", "pmcid", "kind", "reason"}`` dicts for units below
    ``min_unit_chars`` (``kind="unit"``) and for an article with no prose at all
    (``kind="prose"``). Nothing is dropped silently. Pure — no filesystem access.

    ``with_spans=True`` returns two more elements: the article record's
    :class:`Span` list (offsets into that record's ``text``; ``[]`` when no
    article record is emitted) and ``units``, ``{record path: (UnitLayout,
    supplementary)}`` for every emitted table/figure record — its
    :class:`UnitLayout` and whether the float sits inside a
    ``<supplementary-material>``. Both are parallel returns, not record keys, on
    purpose: spans are large and are not a filterable field, so they must never
    reach ``metadata`` (what the passthrough allow-list, ``index_metadata`` and
    the Elasticsearch mapping read) — the record dicts are identical either way.
    They MAY reach the JSONL shard, but only as the document structure the JATS
    adapter builds from them, in the record's top-level ``structure`` key
    (#828 §4, which reverses the earlier "never the shard" half of this rule:
    the shard is the only carrier from the extract step to the ingest step).
    """
    meta = merge_manifest(front_meta(root), pmcid, manifest)
    spans: list[Span] | None = [] if with_spans else None
    abstract, body = _prose_walk(root, spans)
    tws, figs = collect_floats(root)
    records: list[dict] = []
    skipped: list[dict] = []

    prose = "\n\n".join(x for x in (abstract, body) if x)
    if prose.strip():
        m = dict(meta, content_type="article", abstract=abstract,
                 n_tables=len(tws), n_figures=len(figs))
        records.append({"text": prose, "path": pmcid, "metadata": m})
    else:
        skipped.append({"path": pmcid, "pmcid": pmcid, "kind": "prose",
                        "reason": "no abstract or body text"})

    units: dict[str, tuple[UnitLayout, bool]] | None = {} if with_spans else None
    # ElementTree has no parent pointers: one {child: parent} map, built only
    # when unit layouts are wanted, answers "inside <supplementary-material>?".
    parent_of = ({c: p for p in root.iter() for c in p} if units is not None else {})

    def _supplementary(el) -> bool:
        node = parent_of.get(el)
        while node is not None:
            if node.tag == "supplementary-material":
                return True
            node = parent_of.get(node)
        return False

    def _emit(units_in: Iterable[tuple[str, str, dict, UnitLayout | None, bool]],
              content_type: str) -> None:
        for suffix, text, extra, layout, supplementary in units_in:
            path = f"{pmcid}#{suffix}"
            if len(text.strip()) < min_unit_chars:
                skipped.append({
                    "path": path, "pmcid": pmcid, "kind": "unit",
                    "reason": f"{content_type} unit shorter than {min_unit_chars} chars "
                              f"({len(text.strip())})",
                })
                continue
            m = dict(meta, content_type=content_type, section_title=suffix, **extra)
            m.pop("abstract", None)
            records.append({"text": text, "path": path, "metadata": m})
            if units is not None and layout is not None:
                units[path] = (layout, supplementary)

    def _tables() -> Iterable[tuple[str, str, dict, UnitLayout | None, bool]]:
        for i, tw in enumerate(tws, 1):
            lays: dict[str, UnitLayout] | None = {} if units is not None else None
            pieces = table_units(tw, i, max_chars, count_tokens, max_tokens, lays)
            sup = units is not None and _supplementary(tw)
            for s, t in pieces:
                yield s, t, {}, (lays or {}).get(s), sup

    def _figures() -> Iterable[tuple[str, str, dict, UnitLayout | None, bool]]:
        for i, fig in enumerate(figs, 1):
            lays: dict[str, UnitLayout] | None = {} if units is not None else None
            pieces = fig_units(fig, i, max_chars, count_tokens, max_tokens, lays)
            sup = units is not None and _supplementary(fig)
            for s, t, href in pieces:
                yield s, t, {"graphic": href}, (lays or {}).get(s), sup

    _emit(_tables(), "table")
    _emit(_figures(), "figure")

    if spans is not None and units is not None:
        return records, skipped, (spans if prose.strip() else []), units
    return records, skipped


@overload
def convert_file(
    xml_path: str | Path, pmcid: str | None = ..., manifest: dict | None = ...,
    max_chars: int = ..., min_unit_chars: int = ...,
    count_tokens: TokenCounter | None = ..., max_tokens: int = ...,
    *, with_spans: Literal[False] = ..., require_article: bool = ...,
) -> _Records: ...
@overload
def convert_file(
    xml_path: str | Path, pmcid: str | None = ..., manifest: dict | None = ...,
    max_chars: int = ..., min_unit_chars: int = ...,
    count_tokens: TokenCounter | None = ..., max_tokens: int = ...,
    *, with_spans: Literal[True], require_article: bool = ...,
) -> _RecordsWithSpans: ...
def convert_file(
    xml_path: str | Path,
    pmcid: str | None = None,
    manifest: dict | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_unit_chars: int = DEFAULT_MIN_UNIT_CHARS,
    count_tokens: TokenCounter | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    *,
    with_spans: bool = False,
    require_article: bool = False,
) -> _Records | _RecordsWithSpans:
    """Read + convert one JATS file -> ``(records, skipped)``.

    An unparseable / missing / unreadable file yields ``([], [entry])`` with
    ``kind="article"`` — one bad file is reported, never raised, so it cannot sink
    a shard. A ``<pmc-articleset>`` holding one ``<article>`` is converted as
    that article, on every route; one holding none or several fails
    (:func:`unwrap_articleset`), and — with ``require_article`` — so does any
    other well-formed file whose document element is not ``<article>``
    (``reason`` starts ``not a JATS article``). ``pmcid`` defaults to
    :func:`record_key` of the file (``PMC123.1.xml`` -> ``PMC123``; else the
    article's own PMC id; else the file's resolved path). ``with_spans`` is as
    in :func:`article_records` (a failed file's spans are ``[]``, its units
    ``{}``).
    """
    path = Path(xml_path)
    failed: list[dict] | None = None
    label = pmcid or pmc_stem(path) or path.name
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        failed = [{"path": str(path), "pmcid": label, "kind": "article",
                   "reason": f"parse error: {e}"}]
    except Exception as e:  # noqa: BLE001 - missing/unreadable file must not raise
        failed = [{"path": str(path), "pmcid": label, "kind": "article",
                   "reason": f"{type(e).__name__}: {e}"}]
    else:
        article, why = unwrap_articleset(root)
        if article is None:
            failed = [{"path": str(path), "pmcid": label, "kind": "article",
                       "reason": str(why)}]
        elif require_article and not is_article_root(article):
            failed = [{"path": str(path), "pmcid": label, "kind": "article",
                       "reason": f"not a JATS article (document element "
                                 f"<{_local(article.tag)}>)"}]
        else:
            root = article
            if not pmcid:
                pmcid = record_key(path, article_pmcid(root))
    if failed is not None:
        return ([], failed, [], {}) if with_spans else ([], failed)
    assert pmcid
    if with_spans:
        return article_records(root, pmcid, manifest, max_chars, min_unit_chars,
                               count_tokens, max_tokens, with_spans=True)
    return article_records(root, pmcid, manifest, max_chars, min_unit_chars,
                           count_tokens, max_tokens)
