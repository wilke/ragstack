"""JATS XML -> ingest-ready records (the parsing core behind ``scripts/jats_extract.py``).

This is the ported, repo-resident version of the validated out-of-tree converter
``/rag/oa/scripts/jats_to_jsonl.py`` (#301). It turns one PubMed-Central JATS
article into the ``{"text", "path", "metadata"}`` records that
:class:`ragstack.ingestion.loaders.JsonlLoader` consumes, so the OA corpus can
flow through the existing embed/load plane without an XML loader.

It lives in the package (rather than inside the script) for the same reason
``PdfLoader`` does: the CLI (``scripts/jats_extract.py``) is I/O and argument
plumbing, while the parsing rules are pure, importable and unit-tested offline —
and a future ``JatsLoader``/``LoaderRegistry`` entry can reuse them unchanged.

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

Stdlib ``xml.etree.ElementTree`` only — the CWL worker image is CPU-only and
carries no lxml guarantee.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from xml.etree import ElementTree as ET

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


def split_long(label: str, body: str, max_chars: int) -> list[str]:
    """Split an over-long unit on sentence boundaries, repeating ``label``.

    A unit is stored as ONE chunk, so an unsplit 14k-char caption would embed as
    a ~3,600-token blob -- inside SFR's 4080 window, so it would not error, but
    wildly out of family with a 512-token collection and a poor retrieval unit.
    """
    prefix = f"{label} " if label else ""
    budget = max(200, max_chars - len(prefix))
    out, cur = [], ""
    for s in _SENT.split(body):
        if cur and len(cur) + len(s) + 1 > budget:
            out.append(prefix + cur.strip())
            cur = ""
        cur = f"{cur} {s}".strip() if cur else s
        while len(cur) > budget:            # a single sentence over budget
            out.append(prefix + cur[:budget].strip())
            cur = cur[budget:].strip()
    if cur:
        out.append(prefix + cur.strip())
    return out or [prefix.strip()]


def table_units(
    tw,
    idx: int,
    max_chars: int = DEFAULT_MAX_CHARS,
    count_tokens: TokenCounter | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[tuple[str, str]]:
    """Serialise one ``<table-wrap>`` into ``(suffix, text)`` pieces.

    Oversized tables are split BY ROW with the caption and header row repeated
    on every piece -- the stock chunker preserves no header context, so rows
    would otherwise reach the embedder with no column names. "Oversized" is
    measured in TOKENS when ``count_tokens`` is given (see ``DEFAULT_MAX_CHARS``
    for why a char cap alone under-split 32% of real tables), else in chars.
    """
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
            return []
        return _fit_pieces(
            lambda cap: ([(f"table-{idx}", only)] if len(only) <= cap else
                         [(f"table-{idx}-part-{i}", t) for i, t in
                          enumerate(split_long(head_txt, foot or head_txt, cap), 1)]),
            lambda p: p[1], only, max_chars, count_tokens, max_tokens,
        )

    head, rows = table_rows(table)
    whole = render_rows(head, rows)
    tail = f"\n\n{foot}" if foot else ""
    full = f"{head_txt}\n\n{whole}{tail}".strip()

    def build(cap: int) -> list[tuple[str, str]]:
        if len(head_txt) + len(whole) + len(tail) <= cap:
            return [(f"table-{idx}", full)]
        if not rows:  # no parseable rows but over budget -- treat the blob as prose
            return [(f"table-{idx}-part-{i}", t) for i, t in
                    enumerate(split_long(head_txt, whole + tail, cap), 1)]
        prefix = f"{head_txt}\n\n" if head_txt else ""
        hdr = render_rows(head, []) + "\n" if head else ""
        budget = max(200, cap - len(prefix) - len(hdr))
        pieces: list[tuple[str, str]] = []
        cur: list[str] = []
        cur_len, part = 0, 1
        for r in rows:
            line = " | ".join(r)
            if cur and cur_len + len(line) > budget:
                pieces.append((f"table-{idx}-part-{part}",
                               f"{prefix}{hdr}" + "\n".join(cur)))
                part, cur, cur_len = part + 1, [], 0
            while len(line) > budget:  # a single row wider than the whole budget
                pieces.append((f"table-{idx}-part-{part}",
                               f"{prefix}{hdr}" + line[:budget]))
                part, line = part + 1, line[budget:]
            if not line:
                continue
            cur.append(line)
            cur_len += len(line) + 1
        if cur:
            pieces.append((f"table-{idx}-part-{part}", f"{prefix}{hdr}" + "\n".join(cur)))
        if foot and pieces:  # keep the legend with the last piece, not lost
            s, t = pieces[-1]
            pieces[-1] = (s, f"{t}\n\n{foot}")
        return pieces

    return _fit_pieces(build, lambda p: p[1], full, max_chars, count_tokens, max_tokens)


def fig_units(
    fig,
    idx: int,
    max_chars: int = DEFAULT_MAX_CHARS,
    count_tokens: TokenCounter | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[tuple[str, str, str]]:
    """``[(suffix, text, graphic_href)]`` for one ``<fig>``.

    Captions are 8.5% of body text and are real prose; the image bytes are
    separate S3 objects and are not needed to retrieve.
    """
    label = itext(fig.find("label"))
    caption = itext(fig.find("caption"))
    g = fig.find(".//graphic")
    href = (g.get(XLINK) or "") if g is not None else ""
    whole = " ".join(x for x in (label, caption) if x).strip()

    def build(cap: int) -> list[tuple[str, str, str]]:
        if len(whole) <= cap:
            return [(f"figure-{idx}", whole, href)]
        return [(f"figure-{idx}-part-{i}", t, href)
                for i, t in enumerate(split_long(label, caption, cap), 1)]

    return _fit_pieces(build, lambda p: p[1], whole, max_chars, count_tokens, max_tokens)


# ----------------------------------------------------------------------- body

def section_text(sec, depth: int = 0) -> str:
    """Body text for one ``<sec>``, with tables/figures removed and titles kept.

    Section titles are retained inline: the XML carries 952 labelled sections
    across 50 articles (median 20/article), which is chunk-boundary information
    that does not survive PDF extraction at all.
    """
    parts: list[str] = []
    title = sec.find("title")
    if title is not None:
        t = itext(title)
        if t:
            parts.append(("#" * min(depth + 2, 6)) + " " + t)
    for child in sec:
        if child.tag == "title":
            continue
        if child.tag in LIFT:
            continue
        if child.tag == "sec":
            parts.append(section_text(child, depth + 1))
        else:
            parts.append(clean_itext(child))   # prunes floats nested inside <p>
    return "\n\n".join(p for p in parts if p)


def article_prose(root) -> tuple[str, str]:
    """``(abstract, body_text)`` with floats lifted out."""
    abst = root.find(".//article-meta//abstract")
    abstract = clean_itext(abst)
    body = root.find(".//body")
    if body is None:
        return abstract, ""
    chunks: list[str] = []
    for child in body:
        if child.tag in LIFT:
            continue
        chunks.append(section_text(child) if child.tag == "sec"
                      else clean_itext(child))
    return abstract, "\n\n".join(c for c in chunks if c)


def collect_floats(root) -> tuple[list, list]:
    """Every ``<table-wrap>`` / ``<fig>`` in the document, wherever it lives.

    MDPI parks them in ``<floats-group>``, OUTSIDE ``<body>``. Keeping only
    front+abstract+body loses 18-27% of those articles' text, so this walks the
    whole tree rather than just the body.
    """
    return root.findall(".//table-wrap"), root.findall(".//fig")


def front_meta(root) -> dict:
    """Bibliographic metadata from ``<front>`` (ids, authors, journal, licence)."""
    ids = {e.get("pub-id-type"): itext(e)
           for e in root.findall(".//article-meta//article-id")}
    authors = []
    for c in root.findall(".//article-meta//contrib"):
        if (c.get("contrib-type") or "author") != "author":
            continue
        sn, gn = c.find(".//surname"), c.find(".//given-names")
        nm = " ".join(x for x in (itext(gn), itext(sn)) if x)
        if nm:
            authors.append(nm)
    year = ""
    for d in root.findall(".//article-meta//pub-date"):
        y = itext(d.find("year"))
        if y:
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
        "pmcid": ids.get("pmc", "") or ids.get("pmcid", ""),
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
    manifest = manifest or {}
    meta["pmcid"] = meta.get("pmcid") or pmcid
    for k_src, k_dst in (("sha256", "sha256"), ("source_url", "source_url"),
                         ("licence", "licence"), ("doi_xml", "doi")):
        v = manifest.get(k_src)
        if v and not meta.get(k_dst):
            meta[k_dst] = v
    meta["source_url"] = manifest.get("source_url", "")
    meta["sha256"] = manifest.get("sha256", "")
    return meta


# -------------------------------------------------------------------- records

def article_records(
    root,
    pmcid: str,
    manifest: dict | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_unit_chars: int = DEFAULT_MIN_UNIT_CHARS,
    count_tokens: TokenCounter | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> tuple[list[dict], list[dict]]:
    """One parsed JATS ``root`` -> ``(records, skipped)``.

    ``records`` are JSONL-ready ``{"text", "path", "metadata"}`` dicts; ``skipped``
    are ``{"path", "pmcid", "kind", "reason"}`` dicts for units below
    ``min_unit_chars`` (``kind="unit"``) and for an article with no prose at all
    (``kind="prose"``). Nothing is dropped silently. Pure — no filesystem access.
    """
    meta = merge_manifest(front_meta(root), pmcid, manifest)
    abstract, body = article_prose(root)
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

    def _emit(units: Iterable[tuple[str, str, dict]], content_type: str) -> None:
        for suffix, text, extra in units:
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

    _emit(((s, t, {}) for i, tw in enumerate(tws, 1)
           for s, t in table_units(tw, i, max_chars, count_tokens, max_tokens)),
          "table")
    _emit(((s, t, {"graphic": href}) for i, fig in enumerate(figs, 1)
           for s, t, href in fig_units(fig, i, max_chars, count_tokens, max_tokens)),
          "figure")

    return records, skipped


def convert_file(
    xml_path: str | Path,
    pmcid: str | None = None,
    manifest: dict | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_unit_chars: int = DEFAULT_MIN_UNIT_CHARS,
    count_tokens: TokenCounter | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> tuple[list[dict], list[dict]]:
    """Read + convert one JATS file -> ``(records, skipped)``.

    An unparseable / missing / unreadable file yields ``([], [entry])`` with
    ``kind="article"`` — one bad file is reported, never raised, so it cannot sink
    a shard. ``pmcid`` defaults to the filename stem up to the first ``.``
    (``PMC123.1.xml`` -> ``PMC123``).
    """
    path = Path(xml_path)
    pmcid = pmcid or path.name.split(".")[0]
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        return [], [{"path": str(path), "pmcid": pmcid, "kind": "article",
                     "reason": f"parse error: {e}"}]
    except Exception as e:  # noqa: BLE001 - missing/unreadable file must not raise
        return [], [{"path": str(path), "pmcid": pmcid, "kind": "article",
                     "reason": f"{type(e).__name__}: {e}"}]
    return article_records(root, pmcid, manifest, max_chars, min_unit_chars,
                           count_tokens, max_tokens)
