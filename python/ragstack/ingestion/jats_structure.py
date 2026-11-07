"""The JATS structure adapter: ``jats/1`` (#828 §9.2).

Builds a :class:`~ragstack.models.DocumentStructure` for a JATS article
record from the :class:`~ragstack.ingestion.jats.Span` list the same walk that
built the record's text returned (``jats.article_spans`` /
``article_records(with_spans=True)``). Nothing here reads the XML or searches
the text: every offset is a span offset, or a span offset plus the length of
markup :mod:`ragstack.ingestion.jats` itself emitted (§2, "offsets are
accumulated while the text is built, never recovered afterwards").

The mapping (§9.2):

* the abstract span → ONE untitled level-1 section, ``roles=["abstract"]``,
  ``role_source="declared/1"``, ``locator="abstract"``, plus one ``prose``
  block over it. §9.2 calls it "titled ``Abstract``"; it is untitled here
  because a titled section must own a ``heading`` block (§2 invariant 5) and
  the record text has no abstract heading line to put one on.
* each maximal run of depth-0 leaf spans in ``<body>`` (body-lead paragraphs,
  paragraphs between or after top-level ``<sec>``) → an untitled level-1
  section, ``roles=None`` (the rules classify it later).
* ``kind="sec"`` → ``Section(level=depth+1, title=span.title, locator=span.path,
  rank=None)``, with ``path`` the titles of its titled ancestors and itself and
  ``roles`` from :func:`declared_roles` of its ``sec-type``. An untitled
  ``<sec>`` is its own section, never merged into a neighbour.
* section ends are EXTENDED (§2 invariant 3): to the next sibling's start, else
  the parent's (already extended) end, else ``len(text)``, so the ``"\\n\\n"``
  joins that belong to no span — and a parent's text after its last child
  section — fall inside a section. A ``<p>`` after a parent's last child
  ``<sec>`` therefore lies inside that child; the stamp's
  deepest-overlapping-section rule then owns it there.
* ``kind="title"`` → a ``heading`` block over the title text only: the span
  covers the rendered ``"#" * min(depth + 1, 6) + " " + title`` line (the title
  span's depth is its ``<sec>``'s depth + 1; ``_section_walk`` writes
  ``min(sec_depth + 2, 6)`` hashes), so the block starts that many characters
  plus one after ``span.start``.
* leaf spans → blocks by tag (:data:`LEAF_BLOCK_KINDS`, ``prose`` for any other
  tag). Floats (``<table-wrap>``/``<fig>``, in ``<floats-group>`` or nested in a
  ``<p>``) have no span and so no block: they are separate unit records.

Unit records (``content_type="table"|"figure"``) get :func:`unit_structure`
from the :class:`~ragstack.ingestion.jats.UnitLayout` the unit builder
recorded: ONE level-1 section titled with the float's ``<label>`` (so the
stamped ``section_title`` is ``Table 2``, Q13), a ``heading`` block over that
leading label, a caption block carrying the normalised label, and a
``table``/``figure`` block over the grid.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from ragstack.ingestion.jats import Span, UnitLayout
from ragstack.ingestion.structure import (
    DECLARED_VERSION,
    StructureInvalid,
    content_sha256,
    normalise_label,
    validate_structure,
)
from ragstack.models import Block, DocumentStructure, Section

#: The adapter's identity, in ``DocumentStructure.adapter`` and in
#: ``structure_version``. Any change to what this module emits for the same
#: spans is a bump.
ADAPTER = "jats/1"

#: JATS ``sec-type`` → ``roles/1`` tags: the ``declared/1`` map of §5, verbatim.
#: A part not in the map is ``other`` (a declaration that is not in the map is
#: still a declaration). Any change here bumps ``declared/N``.
DECLARED_SEC_TYPES: dict[str, tuple[str, ...]] = {
    "intro": ("introduction",),
    "materials": ("materials",),
    "methods": ("methods",),
    "materials-and-methods": ("materials", "methods"),
    "results": ("results",),
    "discussion": ("discussion",),
    "conclusions": ("conclusions",),
    "supplementary-material": ("supplementary",),
    "cases": ("other",),
    "subjects": ("other",),
}

#: Leaf span kind (the element tag) → block kind. Any tag not listed is
#: ``prose``, as is the abstract.
LEAF_BLOCK_KINDS: dict[str, str] = {
    "p": "prose",
    "label": "prose",
    "list": "list",
    "disp-quote": "quote",
    "disp-formula": "math",
    "preformat": "code",
    "code": "code",
}

_OTHER = "other"


def declared_roles(sec_type: str | None) -> tuple[list[str] | None, str | None]:
    """``(roles, role_source)`` for one ``sec-type`` value under ``declared/1``.

    The value is split on ``|``; each part is stripped, lower-cased and mapped
    on its own (an unmapped part → ``other``); ``other`` is dropped when any
    mapped tag is present; the result is sorted and distinct, never empty.
    ``None``, a blank value or one with no non-blank part → ``(None, None)``:
    no declaration, the section goes to the rules."""
    if sec_type is None:
        return None, None
    parts = [p.strip().lower() for p in sec_type.split("|")]
    parts = [p for p in parts if p]
    if not parts:
        return None, None
    tags: set[str] = set()
    for p in parts:
        tags.update(DECLARED_SEC_TYPES.get(p, (_OTHER,)))
    if tags - {_OTHER}:
        tags.discard(_OTHER)
    return sorted(tags), DECLARED_VERSION


def _heading_markup(title_span: Span) -> int:
    """Characters of ``#`` run plus the one space before the title text."""
    return min(title_span.depth + 1, 6) + 1


def article_structure(text: str, spans: Sequence[Span]) -> DocumentStructure:
    """The ``jats/1`` structure of one article record.

    ``text`` is the record's ``text`` and ``spans`` the span list the same walk
    returned, in document pre-order. Pure and deterministic. Raises
    :class:`ValueError` when a span lies outside ``text`` or a title span does
    not start with the heading markup the walk writes — the signature of spans
    paired with the wrong text; the caller validates the result with
    :func:`~ragstack.ingestion.structure.validate_structure` as for any
    adapter."""
    n = len(text)
    sections: list[Section] = []
    parent_of: list[int | None] = []
    blocks: list[Block] = []
    # Open <sec> sections, outermost first: index i holds the section of the
    # enclosing <sec> at depth i, so a span of depth d has the first d entries
    # as its ancestors (pre-order makes this exact).
    stack: list[int] = []
    run: int | None = None  # the open depth-0 leaf-run section, if any

    def add(section: Section, parent: int | None) -> int:
        sections.append(section)
        parent_of.append(parent)
        return len(sections) - 1

    for span in spans:
        if not (0 <= span.start < span.end <= n):
            raise ValueError(f"span {span.path} [{span.start}, {span.end}) outside "
                             f"the text [0, {n})")
        if span.kind == "abstract":
            run = None
            add(Section(start=span.start, end=span.end, level=1, title=None, path=[],
                        roles=["abstract"], role_source=DECLARED_VERSION,
                        locator="abstract"), None)
            blocks.append(Block(start=span.start, end=span.end, kind="prose"))
            continue
        # A sec of depth d sits under the d enclosing secs; a title or leaf of
        # depth d sits under d secs too (its own <sec> is at depth d - 1).
        del stack[span.depth:]
        if span.kind == "sec":
            run = None
            parent = stack[-1] if stack else None
            parent_path = sections[parent].path if parent is not None else []
            roles, source = declared_roles(span.sec_type)
            i = add(Section(
                start=span.start, end=span.end, level=span.depth + 1, title=span.title,
                path=[*parent_path, *([span.title] if span.title is not None else [])],
                roles=roles, role_source=source, rank=None, locator=span.path), parent)
            stack.append(i)
            continue
        if span.kind == "title":
            skip = _heading_markup(span)
            markup = "#" * (skip - 1) + " "
            if text[span.start:span.start + skip] != markup:
                raise ValueError(f"title span {span.path} does not start with {markup!r}")
            blocks.append(Block(start=span.start + skip, end=span.end, kind="heading"))
            continue
        if span.depth == 0:
            # A body leaf outside every <sec>: it joins (or opens) a run.
            if run is None:
                run = add(Section(start=span.start, end=span.end, level=1, title=None,
                                  path=[], roles=None, role_source=None), None)
            else:
                sections[run].end = span.end
        blocks.append(Block(start=span.start, end=span.end,
                            kind=LEAF_BLOCK_KINDS.get(span.kind, "prose")))

    _extend_ends(sections, parent_of, n)
    return DocumentStructure(
        content_sha256=content_sha256(text),
        sections=sections,
        blocks=blocks,
        structure_source="parsed",
        adapter=ADAPTER,
    )


def unit_structure(text: str, layout: UnitLayout, *,
                   kind: Literal["table", "figure"], supplementary: bool) -> DocumentStructure:
    """The ``jats/1`` structure of one table/figure unit record (§9.2).

    One level-1 section over the whole text, titled with ``layout.label``
    (untitled when the float has none), ``roles=["supplementary"]`` declared
    only when the float sat inside ``<supplementary-material>`` (else
    unclassified: the rules decide). Blocks, in order and never overlapping: a
    ``heading`` over the leading label; the ``table_caption`` /
    ``figure_caption`` over the caption range, ``label`` = the normalised
    label (``Fig. 2A`` → ``Figure 2``; ``None`` when it does not parse); the
    ``table`` / ``figure`` block over the body range when there is one.
    Raises :class:`ValueError` when the text does not start with the label or
    a range lies outside the text — a layout paired with the wrong text."""
    n = len(text)
    label = layout.label
    if not n:
        raise ValueError("a unit record has text")
    if text[:len(label)] != label:
        raise ValueError(f"unit text does not start with its label {label!r}")
    title = label or None
    blocks: list[Block] = []
    if label:
        blocks.append(Block(start=0, end=len(label), kind="heading"))
    cs, ce = layout.caption_start, layout.caption_end
    if not (len(label) <= cs <= ce <= n):
        raise ValueError(f"caption range [{cs}, {ce}) outside [{len(label)}, {n}]")
    if ce > cs:
        blocks.append(Block(start=cs, end=ce, kind=f"{kind}_caption",
                            label=normalise_label(label) if label else None))
    if layout.body_start is not None and layout.body_end is not None:
        bs, be = layout.body_start, layout.body_end
        if not (ce <= bs < be <= n):
            raise ValueError(f"body range [{bs}, {be}) outside [{ce}, {n}]")
        blocks.append(Block(start=bs, end=be, kind=kind))
    section = Section(
        start=0, end=n, level=1, title=title, path=[title] if title is not None else [],
        roles=["supplementary"] if supplementary else None,
        role_source=DECLARED_VERSION if supplementary else None,
        rank=None, locator=None)
    return DocumentStructure(
        content_sha256=content_sha256(text),
        sections=[section],
        blocks=blocks,
        structure_source="parsed",
        adapter=ADAPTER,
    )


def record_structure(
    record: Mapping[str, Any],
    spans: Sequence[Span],
    units: Mapping[str, tuple[UnitLayout, bool]],
) -> tuple[dict[str, Any] | None, str | None]:
    """Build, validate and serialise one JATS record's ``jats/1`` structure.

    The ONE place a JATS record gets its structure, for both carriers of it:
    ``scripts/jats_extract.py`` writes the result as the shard record's
    top-level ``structure`` key (#828 §4), and the in-process
    :class:`~ragstack.ingestion.loaders.JatsLoader` (#828 PR 5a) puts it on the
    record before building the :class:`~ragstack.models.Document` from it the
    way :class:`~ragstack.ingestion.loaders.JsonlLoader` does.

    ``record`` is one record of ``jats.convert_file(..., with_spans=True)``;
    ``spans`` and ``units`` are the span list and unit map the same call
    returned. Article records get :func:`article_structure` from the article
    spans, unit records :func:`unit_structure` from their layout.

    Returns ``(structure.model_dump(), None)`` on success, and
    ``(None, reason)`` on any failure — an adapter ``ValueError``, any other
    exception, a :class:`~ragstack.ingestion.structure.StructureInvalid` — with
    ``reason`` the validator's reason, or the exception class name. Never
    raises: a structure that cannot be built is dropped and counted by the
    caller, never a crashed file."""
    kind = record["metadata"].get("content_type", "article")
    text = record["text"]
    try:
        if kind == "article":
            structure = article_structure(text, spans)
        else:
            layout, supplementary = units[record["path"]]
            structure = unit_structure(text, layout, kind=kind, supplementary=supplementary)
        validate_structure(structure, text)
        return structure.model_dump(), None
    except StructureInvalid as e:
        return None, e.reason
    except Exception as e:  # noqa: BLE001 - a dropped structure, never a crashed file
        return None, type(e).__name__


def _extend_ends(sections: list[Section], parent_of: list[int | None], n: int) -> None:
    """§2 invariant 3, top-down: each section ends at its next sibling's start,
    else at its parent's already-extended end, else (level 1) at ``n``.
    Pre-order guarantees a parent is extended before its children read it."""
    # Assigning Section.end in place is intended: the models are not frozen.
    children: dict[int | None, list[int]] = {None: []}
    for i, p in enumerate(parent_of):
        children.setdefault(p, []).append(i)
    order: list[int | None] = [None, *range(len(sections))]
    for p in order:
        kids = children.get(p) or []
        for k, nxt in zip(kids, kids[1:], strict=False):
            sections[k].end = sections[nxt].start
        if kids:
            sections[kids[-1]].end = n if p is None else sections[p].end


__all__ = ["ADAPTER", "DECLARED_SEC_TYPES", "LEAF_BLOCK_KINDS", "article_structure",
           "declared_roles", "record_structure", "unit_structure"]
