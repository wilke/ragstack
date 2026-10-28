"""Document structure: validation, role classification and the chunk stamp (#828).

The design is ``docs/plans/828-document-structure.md``; section numbers below
refer to it. This module is format-agnostic: it reads a
:class:`~ragstack.models.DocumentStructure` and nothing else about the source
format. Adapters only ever FILL ``Document.structure``; everything here
consumes it. The ingest-time adapters are registered in
:data:`INGEST_ADAPTERS` (Markdown raw mode, ``markdown/1``, lives in
:mod:`ragstack.ingestion.markdown_structure`); JATS, PDF and extracted text
arrive in later PRs.

Four pieces:

* :func:`validate_structure` — the §2 invariants (1–7), checked on every load
  (``JsonlLoader``, ``ingest_jsonl.py``, any adapter's output). A structure that
  fails is DROPPED, never repaired, and the document still ingests as
  unstructured (:func:`structure_from_record` is the one shared reader).
* :func:`classify_roles` — the ``rules/1`` table of §5 over a whole section
  list, filling the OWN ``roles`` of every section an adapter left unclassified.
* :func:`stamp` — the §6 chunk metadata for one ``[start_char, end_char)``: a
  pure, total function of the structure, the offsets and the chunk's own text.
* :func:`stamp_documents` — the one helper every chunk loop calls (the
  pipeline's ``prepare_documents`` and ``iter_embed_source``, and
  ``scripts/ingest_jsonl.py``'s own loop), between chunking and the boilerplate
  filter.

Offsets are Unicode code points into ``Document.content`` — the coordinate
system of ``Chunk.start_char``/``end_char`` (§4.1 item 2). Nothing here ever
searches the text for a title: ownership is offset overlap (§2, §14 "repeated
text aliasing").
"""
from __future__ import annotations

import bisect
import hashlib
import json
import logging
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ragstack.models import (
    BLOCK_KINDS,
    STRUCTURE_SCHEMA_VERSION,
    Block,
    Chunk,
    Document,
    DocumentStructure,
    Section,
)

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Versions and vocabularies
# --------------------------------------------------------------------------- #

#: The role vocabulary (§5). One flat list, no document-class switch (Q4).
ROLES_VERSION = "roles/1"
#: The heading-rule table targeting ``roles/1`` (§5).
RULES_VERSION = "rules/1"
#: The JATS ``sec-type`` map, versioned like the rules (Q5). Written by the JATS
#: adapter (PR 5b); accepted here as a valid producer.
DECLARED_VERSION = "declared/1"
#: The stamp rules (owner selection, label normalisation, inheritance). A change
#: to any of them is a bump, never an in-place edit (§6 identity note 2).
STAMP_VERSION = "stamp/1"

STRUCTURE_SOURCES = frozenset({"parsed", "inferred"})

ROLES_V1 = frozenset({
    # paper
    "abstract", "introduction", "materials", "methods", "results", "discussion",
    "conclusions", "acknowledgements", "references", "supplementary",
    # docs
    "overview", "installation", "usage", "parameters", "examples", "output",
    "troubleshooting", "faq",
    # both
    "other", "unknown",
})
#: Tags that are a section's ONLY tag when they apply (§5, follow-on decision).
_SOLE_TAGS = frozenset({"other", "unknown"})

CAPTION_KINDS = frozenset({"figure_caption", "table_caption"})

#: A serialised structure over this many bytes is dropped, never truncated (§4).
MAX_STRUCTURE_BYTES = 1_000_000

#: Every chunk-metadata key the stamp may write (§6). Declared in
#: ``contracts/schemas/chunk_metadata.json`` and ``metadata_schema``.
STAMP_KEYS = (
    "section_title", "section_path", "section_level", "section_roles",
    "section_roles_version", "section_crossing", "block_types", "caption_labels",
    "refers_to", "structure_source", "structure_version",
)


def _is_role_source(value: object) -> bool:
    if value in (DECLARED_VERSION, RULES_VERSION):
        return True
    return isinstance(value, str) and value.startswith("llm/1:") and len(value) > len("llm/1:")


def content_sha256(content: str) -> str:
    """The binding of a structure to one text (§2 invariant 7)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Validation (§2 invariants)
# --------------------------------------------------------------------------- #

class StructureInvalid(ValueError):
    """A structure broke an invariant. ``reason`` is a short constant label —
    the key a drop is counted under (``DropReport.structure_invalid``,
    ``ingest_jsonl``'s ``structure_dropped``) — and the message says where."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}")


def _tree(sections: list[Section]) -> list[int | None]:
    """Parent index of each section, by the pre-order containment walk. Assumes
    a VALID list (the validator builds the same thing while checking it)."""
    parents: list[int | None] = []
    stack: list[int] = []
    for i, s in enumerate(sections):
        while stack and not (sections[stack[-1]].start <= s.start
                             and s.end <= sections[stack[-1]].end):
            stack.pop()
        parents.append(stack[-1] if stack else None)
        stack.append(i)
    return parents


def validate_structure(
    structure: DocumentStructure, content: str, *, require_roles: bool = False
) -> None:
    """Raise :class:`StructureInvalid` unless ``structure`` satisfies §2's
    invariants against ``content``; otherwise return ``None``.

    1. ``0 <= start < end <= len(content)`` for every section and block.
    2. Sections nest (contain or are disjoint), are in document pre-order, and a
       child's ``level`` is its parent's ``level + 1``; ``path`` is the titles of
       the titled ancestors and the section itself.
    3. Level-1 sections partition ``[0, len(content))`` in order with no gaps;
       the children of a section are ordered, contiguous and end at their
       parent's end (separators belong to the preceding section) — they do not
       cover the parent's own lead text.
    4. Blocks do not overlap and are sorted by ``start``; ``kind`` is one of
       :data:`~ragstack.models.BLOCK_KINDS`; ``label`` only on captions.
    5. A titled section owns exactly one ``heading`` block (owned = the deepest
       section containing it), and it is the first block that starts inside the
       section; an untitled section owns none. Whether the heading block's TEXT
       equals the title is each adapter's own test, not this one (§2 note 5).
    6. (purity of the stamp — a property of :func:`stamp`, tested there).
    7. ``content_sha256`` is the sha256 of ``content``'s UTF-8 encoding.

    Plus the model's own value rules: ``schema_version``, ``structure_source``,
    and ``roles`` (sorted, distinct, non-empty, in ``roles/1``, ``other`` and
    ``unknown`` only alone, ``role_source`` set exactly when ``roles`` is).
    ``require_roles=True`` additionally refuses an unclassified section — the
    check :func:`stamp_documents` makes after :func:`classify_roles`, so an
    unclassified structure can never be stamped as "nothing could be said".
    """
    if structure.schema_version != STRUCTURE_SCHEMA_VERSION:
        raise StructureInvalid(
            "schema_version", f"{structure.schema_version!r} is not {STRUCTURE_SCHEMA_VERSION!r}")
    if structure.structure_source not in STRUCTURE_SOURCES:
        raise StructureInvalid(
            "structure_source", f"{structure.structure_source!r} is not parsed|inferred")
    if not structure.adapter:
        raise StructureInvalid("adapter", "empty adapter name")
    # 7 first: offsets into the wrong text are meaningless, whatever they say.
    if structure.content_sha256 != content_sha256(content):
        raise StructureInvalid("content_sha256", "structure is bound to a different text")

    n = len(content)
    sections, blocks = structure.sections, structure.blocks
    # 1
    for i, s in enumerate(sections):
        if not (0 <= s.start < s.end <= n):
            raise StructureInvalid("offsets", f"section {i} [{s.start}, {s.end}) outside [0, {n})")
    for i, b in enumerate(blocks):
        if not (0 <= b.start < b.end <= n):
            raise StructureInvalid("offsets", f"block {i} [{b.start}, {b.end}) outside [0, {n})")

    # 2 + 3: one pre-order walk with a containment stack.
    children: dict[int | None, list[int]] = {None: []}
    stack: list[int] = []
    prev_start = -1
    for i, s in enumerate(sections):
        if s.start < prev_start:
            raise StructureInvalid("nesting", f"section {i} is out of document order")
        prev_start = s.start
        while stack:
            top = sections[stack[-1]]
            if top.start <= s.start and s.end <= top.end:
                break  # contained: a child of top
            if s.start >= top.end:
                stack.pop()  # disjoint: top is closed
                continue
            raise StructureInvalid(
                "nesting", f"section {i} [{s.start}, {s.end}) partially overlaps "
                f"section {stack[-1]} [{top.start}, {top.end})")
        parent = stack[-1] if stack else None
        expected_level = len(stack) + 1
        if s.level != expected_level:
            raise StructureInvalid(
                "level", f"section {i} has level {s.level}, its depth is {expected_level}")
        if s.title is not None and not s.title.strip():
            raise StructureInvalid("title", f"section {i} has a blank title (use null)")
        parent_path = sections[parent].path if parent is not None else []
        expected_path = [*parent_path, *([s.title] if s.title is not None else [])]
        if s.path != expected_path:
            raise StructureInvalid(
                "path", f"section {i} path {s.path!r} is not its titled ancestry {expected_path!r}")
        if s.rank is not None and not 1 <= s.rank <= 6:
            raise StructureInvalid("rank", f"section {i} rank {s.rank} outside 1..6")
        _check_roles(i, s, require_roles)
        children.setdefault(parent, []).append(i)
        children.setdefault(i, [])
        stack.append(i)

    top_level = children[None]
    if n and not top_level:
        raise StructureInvalid("partition", "no level-1 section covers the text")
    for parent, kids in children.items():
        if not kids:
            continue
        lo, hi = (0, n) if parent is None else (sections[parent].start, sections[parent].end)
        if parent is None and sections[kids[0]].start != lo:
            raise StructureInvalid("partition", f"the first level-1 section starts at "
                                   f"{sections[kids[0]].start}, not 0")
        for left, right in zip(kids, kids[1:], strict=False):
            if sections[left].end != sections[right].start:
                raise StructureInvalid(
                    "partition", f"section {left} ends at {sections[left].end} but its next "
                    f"sibling {right} starts at {sections[right].start} (ends extend to the "
                    f"next start)")
        if sections[kids[-1]].end != hi:
            raise StructureInvalid(
                "partition", f"section {kids[-1]} ends at {sections[kids[-1]].end}, not at "
                f"its parent's end {hi}")

    # 4
    for i, b in enumerate(blocks):
        if b.kind not in BLOCK_KINDS:
            raise StructureInvalid("block_kind", f"block {i} kind {b.kind!r} is not a block kind")
        if b.label is not None and (b.kind not in CAPTION_KINDS or not b.label.strip()):
            raise StructureInvalid("label", f"block {i}: a label belongs on a caption only")
        if i and blocks[i - 1].end > b.start:
            raise StructureInvalid("blocks", f"block {i} overlaps or precedes block {i - 1}")

    # 5
    if sections:
        index = _OwnerIndex(sections, children)
        owned: Counter[int] = Counter()
        for i, b in enumerate(blocks):
            if b.kind != "heading":
                continue
            owner = index.deepest_containing(b.start, b.end)
            if owner is None:
                raise StructureInvalid("heading", f"heading block {i} lies in no single section")
            owned[owner] += 1
        starts = [b.start for b in blocks]
        for i, s in enumerate(sections):
            if s.title is None:
                if owned[i]:
                    raise StructureInvalid("heading", f"untitled section {i} owns a heading block")
                continue
            if owned[i] != 1:
                raise StructureInvalid(
                    "heading", f"titled section {i} owns {owned[i]} heading blocks, not 1")
            first = bisect.bisect_left(starts, s.start)
            if first >= len(blocks) or blocks[first].kind != "heading" \
                    or index.deepest_containing(blocks[first].start, blocks[first].end) != i:
                raise StructureInvalid(
                    "heading", f"titled section {i}'s first block is not its heading")


def _check_roles(i: int, s: Section, require_roles: bool) -> None:
    if s.roles is None:
        if s.role_source is not None:
            raise StructureInvalid("roles", f"section {i} has a role_source but no roles")
        if require_roles:
            raise StructureInvalid("roles", f"section {i} is unclassified")
        return
    if not s.roles:
        raise StructureInvalid("roles", f"section {i} has an empty role list (use null)")
    if s.roles != sorted(set(s.roles)):
        raise StructureInvalid("roles", f"section {i} roles {s.roles!r} are not sorted and distinct")
    unknown = [r for r in s.roles if r not in ROLES_V1]
    if unknown:
        raise StructureInvalid("roles", f"section {i} roles {unknown!r} are not in {ROLES_VERSION}")
    if len(s.roles) > 1 and _SOLE_TAGS.intersection(s.roles):
        raise StructureInvalid("roles", f"section {i}: other/unknown never sit beside a tag")
    if not _is_role_source(s.role_source):
        raise StructureInvalid("role_source", f"section {i} role_source {s.role_source!r}")


class _OwnerIndex:
    """Walk-down lookup of the deepest section containing a range."""

    def __init__(self, sections: list[Section], children: Mapping[int | None, list[int]]):
        self._sections = sections
        self._children = children
        self._starts = {p: [sections[k].start for k in kids] for p, kids in children.items()}

    def deepest_containing(self, start: int, end: int) -> int | None:
        node: int | None = None
        while True:
            kids = self._children.get(node) or []
            j = bisect.bisect_right(self._starts.get(node, []), start) - 1
            if j < 0:
                return node
            k = kids[j]
            s = self._sections[k]
            if not (s.start <= start and end <= s.end):
                return node
            node = k


# --------------------------------------------------------------------------- #
# The shared JSONL reader (§4, §8 path D)
# --------------------------------------------------------------------------- #

def structure_from_record(
    record: Mapping[str, Any], text: str
) -> tuple[DocumentStructure | None, str | None]:
    """Read a JSONL record's top-level ``structure`` key against its ``text``.

    Returns ``(structure, None)`` for a valid structure, ``(None, None)`` when
    the key is absent (or null) — *unknown*, not invalid — and
    ``(None, reason)`` when it is present but unusable: not an object
    (``malformed``), larger than :data:`MAX_STRUCTURE_BYTES` serialised
    (``too_large``), not a valid model (``malformed``) or breaking an invariant
    (the :class:`StructureInvalid` reason). The caller drops the STRUCTURE, keeps
    the document, and counts ``reason``. One reader for ``JsonlLoader`` and for
    ``ingest_jsonl.py``'s own ``Document(...)`` construction, so the two cannot
    disagree about what a valid structure is."""
    raw = record.get("structure")
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, "malformed"
    size = len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size > MAX_STRUCTURE_BYTES:
        return None, "too_large"
    try:
        # strict: a JSON "3" is not an offset, and a bool is not a level.
        structure = DocumentStructure.model_validate(raw, strict=True)
    except ValidationError:
        return None, "malformed"
    try:
        validate_structure(structure, text)
    except StructureInvalid as e:
        return None, e.reason
    return structure, None


# --------------------------------------------------------------------------- #
# Roles: the rules/1 table (§5)
# --------------------------------------------------------------------------- #

#: Exact cues, matched against the normalised title (lower case, numbering and
#: punctuation stripped, whitespace collapsed). ``summary`` and the
#: ``introduction`` sibling rule are context rules in :func:`_match`.
_CUES: dict[str, tuple[str, ...]] = {}


def _cue(tags: tuple[str, ...], *phrases: str) -> None:
    for p in phrases:
        _CUES[p] = tags


_cue(("abstract",), "abstract")
_cue(("introduction",), "introduction", "background")
_cue(("materials",), "materials")
_cue(("materials", "methods"), "materials and methods")
_cue(("methods",), "methods", "experimental procedures", "study design", "patients and methods")
_cue(("results",), "results", "findings")
_cue(("discussion", "results"), "results and discussion")
_cue(("discussion",), "discussion")
_cue(("conclusions",), "conclusion", "conclusions", "concluding remarks")
_cue(("acknowledgements",), "acknowledgements", "acknowledgments", "acknowledgement",
     "acknowledgment", "funding", "author contributions", "conflicts of interest",
     "conflict of interest", "competing interests")
# The one collision (§5): the plural is the bibliography, the singular the API.
_cue(("references",), "references", "bibliography", "literature cited", "works cited")
_cue(("supplementary",), "supplementary material", "supplementary materials",
     "supplementary information", "supplementary data", "supporting information",
     "appendix", "appendices")
_cue(("overview",), "overview", "about")
_cue(("installation",), "install", "installation", "setup", "getting started",
     "requirements", "prerequisites")
_cue(("usage",), "usage", "running", "tutorial", "quick start", "workflow")
_cue(("parameters",), "parameters", "options", "arguments", "configuration", "reference",
     "api reference", "inputs")
_cue(("examples",), "example", "examples", "use cases", "recipes")
_cue(("output",), "output", "outputs", "results format", "output files")
_cue(("troubleshooting",), "troubleshooting", "known issues", "errors", "debugging")
_cue(("faq",), "faq", "frequently asked questions", "questions")

_PREFIX_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("what is ", ("overview",)),
    ("how to ", ("usage",)),
)

#: Leading section numbering: ``2.``, ``2.1``, ``2.1.3.``, ``III.``, ``A.``.
_NUMBERING = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[ivxlcdm]+\.|[a-z]\.)\s+", re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s&/]+", re.UNICODE)
_SPACE = re.compile(r"\s+")
#: Combined headings: ``A and B``, ``A & B``, ``A/B``.
_COMBINER = re.compile(r"\s+and\s+|\s*&\s*|\s*/\s*")


def normalise_title(title: str) -> str:
    """The form the ``rules/1`` cues match: case-folded, leading numbering
    removed, punctuation (except the ``&`` and ``/`` combiners) turned into
    spaces, whitespace collapsed."""
    t = _NUMBERING.sub("", title.strip())
    t = _PUNCT.sub(" ", t.replace("_", " ")).casefold()
    return _SPACE.sub(" ", t).strip()


def _lookup(norm: str) -> tuple[str, ...] | None:
    """Context-free cue lookup of one normalised phrase."""
    if norm in _CUES:
        return _CUES[norm]
    if norm.endswith(" reference"):  # "API reference", "Command reference"
        return ("parameters",)
    for prefix, tags in _PREFIX_CUES:
        if norm.startswith(prefix):
            return tags
    return None


def _match(norm: str, *, first: bool, last: bool) -> tuple[str, ...] | None:
    """Cue lookup of one phrase with the positional ``summary`` rule."""
    if norm == "summary":
        if first:
            return ("abstract",)
        if last:
            return ("conclusions",)
        return None
    return _lookup(norm)


def classify_title(title: str | None, *, first: bool = False, last: bool = False,
                   inferred: bool = False) -> list[str]:
    """The ``rules/1`` own tags of one title, without sibling context (the
    ``introduction`` + ``overview`` rule needs siblings; :func:`classify_roles`
    applies it). ``None`` → ``["unknown"]``; a titled section matching no rule
    → ``["other"]``, or ``["unknown"]`` when the structure is ``inferred``
    ("never a guess", §5)."""
    if title is None:
        return ["unknown"]
    norm = normalise_title(title)
    parts = [p for p in _COMBINER.split(norm) if p]
    tags: set[str] = set()
    if len(parts) > 1:
        matched = [_match(p, first=first, last=last) for p in parts]
        if all(m is not None for m in matched):
            for m in matched:
                tags.update(m or ())
    if not tags:
        whole = _match(norm, first=first, last=last)
        if whole is not None:
            tags.update(whole)
    if not tags:
        return ["unknown"] if inferred else ["other"]
    return sorted(tags)


def classify_roles(sections: list[Section], *, inferred: bool = False) -> None:
    """Fill the OWN ``roles`` of every section whose ``roles`` is ``None``, with
    ``role_source="rules/1"``; never touch a section that already has roles
    (declared roles are never overwritten or added to, §5). One deterministic
    pass over the whole list, because two rules need context: ``summary`` is
    ``abstract`` when it is the first titled level-1 section and
    ``conclusions`` when it is the last; ``introduction`` gains ``overview``
    when a sibling (same parent) is an ``installation`` or ``usage`` section.
    ``inferred`` is the structure's ``structure_source == "inferred"``: a
    titled-but-unmatched inferred section is ``unknown``, not ``other``.
    Mutates ``sections`` in place; assumes a valid tree (pre-order nesting)."""
    if not sections:
        return
    parents = _tree(sections)
    titled_top = [i for i, s in enumerate(sections) if s.level == 1 and s.title is not None]
    first_top = titled_top[0] if titled_top else None
    last_top = titled_top[-1] if titled_top else None
    # One pass, O(n): for each parent, how many of its children classify as an
    # installation/usage section. A per-section rescan of the siblings was
    # quadratic in sibling count (thousands of "Introduction" siblings under the
    # 1 MB ceiling took ~90 s per document, on the event loop).
    is_docs = [s.title is not None and bool({"installation", "usage"} & set(classify_title(s.title)))
               for s in sections]
    docs_children: Counter[int | None] = Counter()
    for i, p in enumerate(parents):
        if is_docs[i]:
            docs_children[p] += 1

    def _docs_sibling(i: int) -> bool:
        return docs_children[parents[i]] - (1 if is_docs[i] else 0) > 0

    for i, s in enumerate(sections):
        if s.roles is not None:
            continue
        tags = classify_title(s.title, first=i == first_top, last=i == last_top,
                              inferred=inferred)
        if "introduction" in tags and s.title is not None \
                and normalise_title(s.title) == "introduction" and _docs_sibling(i):
            tags = sorted({*tags, "overview"})
        s.roles = tags
        s.role_source = RULES_VERSION


# --------------------------------------------------------------------------- #
# Caption labels and citations (§6 caption_labels / refers_to)
# --------------------------------------------------------------------------- #

_LABEL_HEAD = (
    r"\b(?P<supp>supplement(?:ary|al)\s+)?"
    r"(?P<kind>fig(?:ure)?s?\.?|tab\.|tabs\.|tables?\.?)"
)
#: One label number: an optional ``S`` (supplementary), digits, an optional
#: panel letter (``3A``, ``3(a)``) that is dropped.
_TOKEN = r"S?\d+(?:[A-Za-z](?![A-Za-z])|\([A-Za-z]\))?"
_SEP = r"\s*(?:,\s*(?:and\s+)?|and\s+|&\s*|–|—|-|to\s+)\s*"
_CITATION = re.compile(
    _LABEL_HEAD + r"\s*(?P<list>" + _TOKEN + r"(?:" + _SEP + _TOKEN + r")*)",
    re.IGNORECASE,
)
_LIST_PART = re.compile(
    r"(" + _SEP + r")?(S?\d+)(?:[A-Za-z](?![A-Za-z])|\([A-Za-z]\))?", re.IGNORECASE
)
#: A range wider than this is not expanded (it is more likely a typo or a
#: non-citation than "Figures 1–90").
_MAX_RANGE = 50


def _labels_of(match: re.Match[str]) -> list[str]:
    kind = "Table" if match.group("kind").lower().startswith("tab") else "Figure"
    supp = bool(match.group("supp"))
    out: list[str] = []
    prev: tuple[bool, int] | None = None
    for sep_m in _LIST_PART.finditer(match.group("list")):
        sep, token = sep_m.group(1) or "", sep_m.group(2)
        is_s = token[:1].upper() == "S"
        num = int(token[1:] if is_s else token)
        is_s = is_s or supp
        is_range = bool(re.search(r"–|—|-|\bto\b", sep, re.IGNORECASE))
        if is_range and prev is not None and prev[0] == is_s and 0 < num - prev[1] <= _MAX_RANGE:
            for k in range(prev[1] + 1, num + 1):
                out.append(f"{kind} {'S' if is_s else ''}{k}")
        else:
            out.append(f"{kind} {'S' if is_s else ''}{num}")
        prev = (is_s, num)
    return out


def citations(text: str) -> list[str]:
    """Every figure/table label cited in ``text``, normalised (``Fig. 3``,
    ``FIG 3``, ``Figure 3A``, ``Figs. 2 and 3``, ``Tables 1–3``,
    ``Supplementary Figure 2`` → ``Figure 3``, ``Figure 2``, ``Table 1`` …,
    ``Figure S2``), sorted and distinct. Panel letters are dropped; an ``S``
    prefix is kept (``Figure S1`` ≠ ``Figure 1``)."""
    labels: set[str] = set()
    for m in _CITATION.finditer(text):
        labels.update(_labels_of(m))
    return sorted(labels)


def normalise_label(text: str) -> str | None:
    """The normalised label a caption starts with (``Fig. 3.`` → ``Figure 3``),
    the form ``Block.label`` and ``refers_to`` share; ``None`` when ``text``
    does not start with one."""
    m = _CITATION.match(text.lstrip())
    if m is None:
        return None
    labels = _labels_of(m)
    return labels[0] if labels else None


# --------------------------------------------------------------------------- #
# The stamp (§6)
# --------------------------------------------------------------------------- #

class StructureIndex:
    """Precomputed lookups over ONE valid structure, so stamping a document's
    chunks is not quadratic. :func:`stamp` is the pure entry point; this class
    is its implementation, built once per document by :func:`stamp_documents`."""

    def __init__(self, structure: DocumentStructure) -> None:
        self.structure = structure
        sections = structure.sections
        self._sections = sections
        self._parents = _tree(sections)
        kids: dict[int, list[int]] = {}
        for i, p in enumerate(self._parents):
            if p is not None:
                kids.setdefault(p, []).append(i)
        # Segments (§7.1): each leaf is one; a parent's own text before its
        # first child (and after its last child, empty under invariant 3) is
        # one owned by the parent. They partition [0, len(content)).
        segs: list[tuple[int, int, int]] = []
        for i, s in enumerate(sections):
            ch = kids.get(i)
            if not ch:
                segs.append((s.start, s.end, i))
                continue
            if sections[ch[0]].start > s.start:
                segs.append((s.start, sections[ch[0]].start, i))
            if sections[ch[-1]].end < s.end:
                segs.append((sections[ch[-1]].end, s.end, i))
        segs.sort()
        self._segs = segs
        self._seg_starts = [g[0] for g in segs]
        self._blocks = structure.blocks
        self._block_ends = [b.end for b in structure.blocks]
        self._version = f"{structure.adapter};{STAMP_VERSION}"

    def _ancestry(self, i: int) -> list[int]:
        """``i`` and its ancestors, innermost first."""
        out: list[int] = []
        node: int | None = i
        while node is not None:
            out.append(node)
            node = self._parents[node]
        return out

    def _overlapping_segments(self, start: int, end: int) -> list[tuple[int, int, int]]:
        if end <= start:  # a zero-length range sits in the segment holding it
            j = bisect.bisect_right(self._seg_starts, start) - 1
            return [self._segs[j]] if 0 <= j < len(self._segs) else []
        j = max(0, bisect.bisect_right(self._seg_starts, start) - 1)
        out = []
        while j < len(self._segs) and self._segs[j][0] < end:
            if self._segs[j][1] > start:
                out.append(self._segs[j])
            j += 1
        return out

    def owner(self, start: int, end: int) -> int | None:
        """The owning section (§6): among the DEEPEST sections overlapping the
        range, the one with the largest overlap in characters; ties → the
        earliest."""
        segs = self._overlapping_segments(start, end)
        if not segs:
            return None
        candidates: set[int] = set()
        for _, _, i in segs:
            candidates.update(self._ancestry(i))
        depth = max(self._sections[i].level for i in candidates)
        deepest = sorted(i for i in candidates if self._sections[i].level == depth)

        def overlap(i: int) -> int:
            s = self._sections[i]
            return min(end, s.end) - max(start, s.start)

        # max() keeps the FIRST of equal keys; ``deepest`` is in pre-order.
        return max(deepest, key=overlap)

    def stamp(self, start: int, end: int, text: str) -> dict[str, Any]:
        owner = self.owner(start, end)
        if owner is None:
            raise StructureInvalid("offsets", f"range [{start}, {end}) is in no section")
        sec = self._sections[owner]
        out: dict[str, Any] = {}
        if sec.title is not None:
            out["section_title"] = sec.title
        if sec.path:
            out["section_path"] = list(sec.path)
        out["section_level"] = sec.level

        contributions: list[tuple[str, list[str]]] = []
        for i in self._ancestry(owner):
            s = self._sections[i]
            if s.roles is None or s.role_source is None:
                raise StructureInvalid("roles", f"section {i} is unclassified at stamp time")
            contributions.append((s.role_source, s.roles))
        union = {r for _, roles in contributions for r in roles}
        if len(union) > 1:
            union.discard("unknown")
        out["section_roles"] = sorted(union)
        out["section_roles_version"] = sorted(
            {src for src, roles in contributions if union.intersection(roles)})

        if len(self._overlapping_segments(start, end)) > 1:
            out["section_crossing"] = True  # stamped only when true (Q10)

        kinds: set[str] = set()
        captions: set[str] = set()
        j = bisect.bisect_right(self._block_ends, start)
        while j < len(self._blocks) and self._blocks[j].start < end:
            b = self._blocks[j]
            if b.end > start and end > start:
                kinds.add(b.kind)
            if b.kind in CAPTION_KINDS and b.label and start <= b.start < end:
                captions.add(b.label)
            j += 1
        if kinds:
            out["block_types"] = sorted(kinds)
        if captions:
            out["caption_labels"] = sorted(captions)
        cited = citations(text)
        if cited:
            out["refers_to"] = cited
        out["structure_source"] = self.structure.structure_source
        out["structure_version"] = self._version
        return out


def stamp(structure: DocumentStructure, start_char: int, end_char: int,
          content: str) -> dict[str, Any]:
    """The §6 chunk metadata for the range ``[start_char, end_char)`` of a
    document with ``structure``; ``content`` is the CHUNK's own text (what
    ``refers_to`` is read from). Pure and total over a valid, classified
    structure: the same inputs give the same dict on every path, and every
    chunk gets ``section_level``, ``section_roles``, ``section_roles_version``,
    ``structure_source`` and ``structure_version``. Keys with nothing to say
    (an untitled owner's ``section_title``, an empty list) are omitted, never
    written empty; ``section_crossing`` is written only when true."""
    return StructureIndex(structure).stamp(start_char, end_char, content)


# --------------------------------------------------------------------------- #
# The pipeline helpers (§6 "one helper, three call sites"; §2 attach_structure)
# --------------------------------------------------------------------------- #

class StructureBackendUnavailable(RuntimeError):
    """An ingest adapter was asked to run (``STRUCTURE_METADATA=on``, later a
    ``chunk_params`` marker) and its parser is not installed. A REFUSAL, never a
    fallback to "no structure" or a regex (§6, #815): the API answers 503 with
    :attr:`client_detail`, the bulk tools exit 2 at configuration. ``extra``
    names the optional-dependency extra that carries the parser."""

    def __init__(self, adapter: str, extra: str, detail: str) -> None:
        self.adapter = adapter
        self.extra = extra
        self.client_detail = (
            f"document-structure metadata is on (STRUCTURE_METADATA / "
            f"--structure-metadata) but the {adapter} adapter's parser is not "
            f"installed here: install the [{extra}] extra (pip install "
            f"'ragstack[{extra}]'), or turn structure metadata off"
        )
        super().__init__(f"{self.client_detail} ({detail})")


class IngestAdapter:
    """One ingest-time adapter (§2's placement table): ``build`` returns a
    structure for a document or ``None``; ``require`` raises
    :class:`StructureBackendUnavailable` when its parser is missing; ``backend``
    names the parser for ``enrichment.structure.backend``. A plain callable is
    accepted wherever an adapter is (a test's fake): it needs no parser."""

    def __init__(self, name: str, build: Callable[[Document], DocumentStructure | None], *,
                 require: Callable[[], None] | None = None,
                 backend: Callable[[], str] | None = None) -> None:
        self.name = name
        self._build = build
        self._require = require
        self._backend = backend

    def __call__(self, doc: Document) -> DocumentStructure | None:
        return self._build(doc)

    def require(self) -> None:
        if self._require is not None:
            self._require()

    def backend(self) -> str | None:
        return self._backend() if self._backend is not None else None


def _markdown_adapter() -> IngestAdapter:
    """``markdown/1`` (§9.1), imported lazily so this module never needs the
    ``[markdown]`` extra to be imported, only to run the adapter."""
    from ragstack.ingestion import markdown_structure as md

    def _require() -> None:
        try:
            md.require()
        except md.MarkdownBackendMissing as e:
            raise StructureBackendUnavailable(md.ADAPTER, md.EXTRA, str(e)) from None

    def _build(doc: Document) -> DocumentStructure | None:
        _require()
        return md.markdown_structure(doc)

    return IngestAdapter(md.ADAPTER, _build, require=_require, backend=md.backend)


AdapterLike = IngestAdapter | Callable[[Document], DocumentStructure | None]

#: Adapters that build a structure at INGEST from the text itself, keyed by the
#: lower-cased suffix of ``Document.source`` (§2's placement table). PR 2:
#: Markdown raw mode (``.md``, the suffix ``default_loader_registry`` sends to
#: ``TextFileLoader``). Extracted text (§9.5) joins under its own marker. An
#: adapter returns a structure or ``None``.
INGEST_ADAPTERS: dict[str, AdapterLike] = {".md": _markdown_adapter()}


def require_structure_backends(adapters: Mapping[str, AdapterLike] | None = None) -> None:
    """Raise :class:`StructureBackendUnavailable` unless every registered ingest
    adapter's parser is installed. Called at CONFIGURATION, wherever the
    structure step is turned on — the API before it accepts a local ingest, the
    bulk tools before they read a record — so a missing extra refuses the run
    whatever the batch holds, instead of silently ingesting its Markdown
    unstructured (§6: "a refusal, never a fallback")."""
    table = INGEST_ADAPTERS if adapters is None else adapters
    for adapter in table.values():
        require = getattr(adapter, "require", None)
        if require is not None:
            require()


def require_structure_backends_or_exit(mode: str | None) -> None:
    """The bulk tools' configuration check (``ingest_shard.py``,
    ``embed_shard.py``, ``ingest_jsonl.py``): with ``--structure-metadata on``
    and a parser missing, print the reason and exit 2 before any record is
    read, any store is touched or any vector is computed. Off: nothing."""
    if not structure_metadata_enabled(mode):
        return
    try:
        require_structure_backends()
    except StructureBackendUnavailable as e:
        print(f"error: {e.client_detail}", file=sys.stderr)
        raise SystemExit(2) from None


def attach_structure(
    documents: Iterable[Document],
    adapters: Mapping[str, AdapterLike] | None = None,
    counts: Counter[str] | None = None,
) -> None:
    """Give every document that has no structure yet the one its suffix's
    ingest adapter builds (§2), validated before it is accepted; a structure
    that arrived with the document (the JSONL key, a PDF/JATS loader) is never
    replaced. Runs after load and DOI enrichment, before chunking, only when
    the structure step is on. ``documents`` and their ``content`` are never
    changed except for ``structure``.

    An adapter's invalid output — or an adapter that raises on one document —
    is dropped and counted under ``structure_invalid:<reason>`` in ``counts``
    (the invariant label, else the exception class name): the document stays
    unstructured, never half-structured, and the ingest goes on. A MISSING
    PARSER is not such a failure: :class:`StructureBackendUnavailable`
    propagates (the refusal). Each accepted structure counts
    ``backend:<parser>`` when the adapter names one."""
    table = INGEST_ADAPTERS if adapters is None else adapters
    if not table:
        return
    for doc in documents:
        if doc.structure is not None:
            continue
        adapter = table.get(Path(doc.source).suffix.lower())
        if adapter is None:
            continue
        try:
            built = adapter(doc)
            if built is None:
                continue
            validate_structure(built, doc.content)
        except StructureBackendUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 - one document degrades, never the ingest
            reason = e.reason if isinstance(e, StructureInvalid) else type(e).__name__
            if counts is not None:
                counts[f"structure_invalid:{reason}"] += 1
            log.warning("structure from the %s adapter dropped for %r: %s",
                        getattr(adapter, "name", "ingest"), doc.source, e)
            continue
        doc.structure = built
        name = adapter.backend() if isinstance(adapter, IngestAdapter) else None
        if counts is not None and name:
            counts[f"backend:{name}"] += 1


def stamp_documents(
    documents: Iterable[Document], chunks: list[Chunk], counts: Counter[str] | None = None
) -> None:
    """Stamp every chunk of a structured document with :func:`stamp` (§6).

    Called by every chunk loop between chunking and the boilerplate filter. For
    each structured document: :func:`classify_roles` fills the sections the
    adapter left unclassified, the structure is re-validated with
    ``require_roles=True``, and each of its chunks gets the stamp. A chunk of an
    unstructured document gets NONE of the keys (absent = not processed).

    All-or-nothing per document: every stamp is computed before any is
    applied, so a structure that fails at stamp time (or a chunk whose offsets
    do not reproduce its text) leaves that document's chunks exactly as they
    were — counted under ``degraded:<reason>`` and logged, never fatal, the
    ``_attach_glossary`` contract.

    ``counts`` accumulates the receipt's ``enrichment.structure`` numbers:
    ``adapter:<name>`` per structured document, ``structure_missing`` per
    document without one, ``chunks_stamped``, ``degraded:<reason>``."""
    docs = list(documents)
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.doc_id, []).append(c)
    seen: set[str] = set()
    for doc in docs:
        if doc.id in seen:
            continue
        seen.add(doc.id)
        if doc.structure is None:
            if counts is not None:
                counts["structure_missing"] += 1
            continue
        if counts is not None:
            counts[f"adapter:{doc.structure.adapter}"] += 1
        mine = by_doc.get(doc.id, [])
        try:
            structure = doc.structure
            classify_roles(structure.sections,
                           inferred=structure.structure_source == "inferred")
            validate_structure(structure, doc.content, require_roles=True)
            index = StructureIndex(structure)
            stamps = []
            for c in mine:
                if doc.content[c.start_char:c.end_char] != c.content:
                    raise StructureInvalid(
                        "offsets", f"chunk {c.id} does not reproduce its document text")
                stamps.append(index.stamp(c.start_char, c.end_char, c.content))
        except Exception as e:  # noqa: BLE001 - degrade the document, never fail the ingest
            # Counted by reason, like ``structure_invalid``: the invariant
            # label for a StructureInvalid, else the exception class name.
            reason = e.reason if isinstance(e, StructureInvalid) else type(e).__name__
            if counts is not None:
                counts[f"degraded:{reason}"] += 1
            log.warning("structure stamp skipped for %r: %s", doc.source or doc.id, e)
            continue
        for c, s in zip(mine, stamps, strict=True):
            c.metadata.update(s)
        if counts is not None:
            counts["chunks_stamped"] += len(mine)


def structure_provenance(
    counts: Counter[str], since: Counter[str] | None = None
) -> dict[str, Any] | None:
    """The ``enrichment.structure`` record (§6) for what the step did since
    ``since``: ``{"structure": {"stamp": "stamp/1", "adapters": {name: n},
    "structure_missing": n, "chunks_stamped": n}}`` (plus ``degraded`` and
    ``structure_invalid``, each a ``{reason: n}`` map, only when non-zero). ``None`` when the step saw no
    document. ``backend`` names the parser(s) that built a structure (the
    ``backend:<parser>`` counts :func:`attach_structure` keeps), present only
    when an ingest adapter actually built one."""
    done = counts - (since or Counter())
    adapters = {k.split(":", 1)[1]: n for k, n in sorted(done.items()) if k.startswith("adapter:")}
    missing = done["structure_missing"]
    if not adapters and not missing:
        return None
    rec: dict[str, Any] = {
        "stamp": STAMP_VERSION,
        "adapters": adapters,
        "structure_missing": missing,
        "chunks_stamped": done["chunks_stamped"],
    }
    degraded = {k.split(":", 1)[1]: n for k, n in sorted(done.items())
                if k.startswith("degraded:")}
    if degraded:
        rec["degraded"] = degraded
    invalid = {k.split(":", 1)[1]: n for k, n in sorted(done.items())
               if k.startswith("structure_invalid:")}
    if invalid:
        rec["structure_invalid"] = invalid
    backends = sorted(k.split(":", 1)[1] for k, n in done.items()
                      if k.startswith("backend:") and n)
    if backends:
        # The parser that built the structures (#815's audit): one string, the
        # §6 shape; two only if two parser builds ran in one process.
        rec["backend"] = ", ".join(backends)
    return {"structure": rec}


def merge_enrichment(*records: dict[str, Any] | None) -> dict[str, Any] | None:
    """Combine step records (``{"symbol_glossary": …}``, ``{"structure": …}``)
    into one ``enrichment`` object, ``None`` when every one is ``None`` — so a
    receipt or manifest from a run with no step stays byte-identical."""
    out: dict[str, Any] = {}
    for rec in records:
        if rec:
            out.update(rec)
    return out or None


def structure_metadata_enabled(mode: str | None) -> bool:
    """``STRUCTURE_METADATA`` / ``--structure-metadata``: on exactly for
    ``on``; anything else (including a typo) is off, the reading the API's
    GoWe submission also uses."""
    return mode == "on"


__all__ = [
    "Block", "DocumentStructure", "Section",
    "STAMP_KEYS", "STAMP_VERSION", "ROLES_V1", "RULES_VERSION", "DECLARED_VERSION",
    "IngestAdapter", "StructureBackendUnavailable", "StructureIndex", "StructureInvalid",
    "attach_structure", "citations", "require_structure_backends",
    "require_structure_backends_or_exit",
    "classify_roles", "classify_title", "content_sha256", "merge_enrichment",
    "normalise_label", "normalise_title", "stamp", "stamp_documents",
    "structure_from_record", "structure_metadata_enabled", "structure_provenance",
    "validate_structure",
]
