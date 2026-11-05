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
"""
from __future__ import annotations

from collections.abc import Sequence

from ragstack.ingestion.jats import Span
from ragstack.ingestion.structure import DECLARED_VERSION, content_sha256
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
           "declared_roles"]
