"""#828 PR 2: the Markdown structure adapter, raw mode (``markdown/1``, §9.1).

The adversarial fixture is the #830 parity fixture (BOM, front matter, CRLF,
fences containing ``#``, setext headings, tables, images, ``$$`` maths), read
through ``TextFileLoader`` exactly as both ingest paths read it. Everything is
synthetic. Each expectation here is written out by hand, never recomputed by
the code under test.

Needs the ``[markdown]`` extra; without it the module is skipped (the refusal
when it is missing is tested in ``test_markdown_structure_refusal.py``, which
needs no parser).
"""
from __future__ import annotations

import random
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("mdit_py_plugins", reason="the [markdown] extra is not installed")

from ragstack.ingestion import markdown_structure as md  # noqa: E402
from ragstack.ingestion.loaders import TextFileLoader  # noqa: E402
from ragstack.ingestion.structure import (  # noqa: E402
    attach_structure,
    classify_roles,
    content_sha256,
    stamp,
    structure_provenance,
    validate_structure,
)
from ragstack.models import Document, DocumentStructure  # noqa: E402
from tests.ingestion.test_extract_text_parity import _MARKDOWN  # noqa: E402

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _doc(text: str, source: str = "/docs/page.md") -> Document:
    return Document(id="d", content=text, source=source)


def _build(text: str) -> DocumentStructure:
    built = md.markdown_structure(_doc(text))
    assert built is not None
    validate_structure(built, text)
    return built


def _load(tmp_path: Path, text: str, name: str = "guide.md") -> Document:
    path = tmp_path / name
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    [doc] = TextFileLoader().load(str(path))
    return doc


def _headings(doc_text: str, s: DocumentStructure) -> list[str]:
    return [doc_text[b.start:b.end] for b in s.blocks if b.kind == "heading"]


def _outline(s: DocumentStructure) -> list[tuple]:
    return [(x.title, x.level, x.rank) for x in s.sections]


# --------------------------------------------------------------------------- #
# The adversarial fixture
# --------------------------------------------------------------------------- #

FIXTURE_OUTLINE = [
    (None, 1, None),                        # BOM + front matter preamble
    ("Installing   the tool", 1, 1),        # ATX, emphasis removed, spacing verbatim
    ("Usage", 2, 2),
    ("Setext title", 1, 1),                 # setext =
    ("Second setext", 2, 2),                # setext -, emphasis removed
    ("Results and discussion", 3, 3),       # ATX with a closing ### sequence
]
#: The raw source each heading block covers: the title text only — no `#`
#: run, no closing sequence, no underline — with its inline markup intact.
FIXTURE_HEADING_TEXT = [
    "Installing   the *tool*",
    "Usage",
    "Setext title",
    "Second *setext*",
    "Results and discussion",
]
FIXTURE_BLOCK_KINDS = [
    "front_matter", "heading", "prose", "heading", "prose", "code", "code", "table",
    "quote", "list", "prose", "prose", "prose", "heading", "prose", "code", "heading",
    "figure_caption", "figure", "figure", "figure_caption", "math", "prose", "heading",
    "prose",
]


@pytest.fixture
def fixture_doc(tmp_path) -> Document:
    doc = _load(tmp_path, _MARKDOWN)
    # The loader keeps the BOM and normalises newlines; both are the premise.
    assert doc.content.startswith("﻿---\n") and "\r" not in doc.content
    return doc


def test_fixture_outline_ranks_levels_and_paths(fixture_doc) -> None:
    s = _build(fixture_doc.content)
    assert _outline(s) == FIXTURE_OUTLINE
    assert [x.path for x in s.sections] == [
        [], ["Installing   the tool"], ["Installing   the tool", "Usage"],
        ["Setext title"], ["Setext title", "Second setext"],
        ["Setext title", "Second setext", "Results and discussion"],
    ]
    assert s.adapter == "markdown/1" and s.structure_source == "parsed"
    assert s.content_sha256 == content_sha256(fixture_doc.content)
    # Roles are the classifier's, at stamp time: the adapter leaves them None.
    assert all(x.roles is None and x.role_source is None for x in s.sections)


def test_front_matter_is_a_block_and_not_a_heading(fixture_doc) -> None:
    """MUTATION: drop the front_matter plugin, or parse ``content`` with its
    BOM → the YAML parses as a setext ``h2`` titled with the YAML → fails."""
    text = fixture_doc.content
    s = _build(text)
    fm = s.blocks[0]
    assert fm.kind == "front_matter"
    # BOM-shifted: the block starts after the BOM, at the opening fence.
    assert (fm.start, text[fm.start:fm.end]) == (
        1, '---\ntitle: "Install guide"\ntags: [cli, setup]\n---\n')
    assert not any(x.title and ("title:" in x.title or "tags:" in x.title)
                   for x in s.sections)
    # The preamble is untitled, starts at 0 (the BOM), and ends where the
    # first heading's line starts.
    pre = s.sections[0]
    assert (pre.start, pre.title, pre.rank, pre.locator) == (0, None, None, "L1")
    assert text[pre.end:].startswith("# Installing")


def test_heading_blocks_are_the_title_text_only(fixture_doc) -> None:
    """MUTATION: the heading block covers the whole heading line (``# …\\n``,
    or the setext underline) → fails."""
    s = _build(fixture_doc.content)
    assert _headings(fixture_doc.content, s) == FIXTURE_HEADING_TEXT


def test_heading_blocks_equal_titles_after_inline_markup_removal(fixture_doc) -> None:
    """§2 invariant 5's stronger property, for raw Markdown: removing the
    inline markup from the heading block's text gives the title. The removal
    here is a hand-written table, not the adapter's own renderer."""
    unmarked = {"Installing   the *tool*": "Installing   the tool",
                "Second *setext*": "Second setext"}
    s = _build(fixture_doc.content)
    titled = [x for x in s.sections if x.title is not None]
    raw = _headings(fixture_doc.content, s)
    assert [unmarked.get(r, r) for r in raw] == [x.title for x in titled]


def test_block_kinds_of_the_fixture(fixture_doc) -> None:
    text = fixture_doc.content
    s = _build(text)
    assert [b.kind for b in s.blocks] == FIXTURE_BLOCK_KINDS
    by_kind = {}
    for b in s.blocks:
        by_kind.setdefault(b.kind, []).append(text[b.start:b.end])
    # The `#` comment inside the fence is code, never a heading.
    assert "```python\n# not a heading: a comment inside a fence\n```\n" in by_kind["code"]
    assert by_kind["math"] == ["$$\na^2 + b^2 = c^2\n$$\n"]
    assert by_kind["table"][0].startswith("| flag | default | meaning |\n")
    # The thematic break after a blank line is prose, not a setext underline.
    assert "---\n" in by_kind["prose"]


def test_image_captions_by_label(fixture_doc) -> None:
    text = fixture_doc.content
    s = _build(text)
    caps = [(b.label, text[b.start:b.end]) for b in s.blocks if b.kind == "figure_caption"]
    assert caps == [
        # the alt text of an image-only paragraph that starts with a label
        ("Figure 1", "Figure 1. A synthetic pipeline diagram"),
        # the paragraph after an image-only paragraph whose alt has no label
        ("Figure 2", "Figure 2: the second synthetic figure.\n"),
    ]
    figures = [text[b.start:b.end] for b in s.blocks if b.kind == "figure"]
    assert figures == ["](images/fig1.png)\n", "![plain alt](images/fig2.png)\n"]


def test_section_ends_are_extended_to_the_next_start(fixture_doc) -> None:
    """MUTATION: close a section at its last block's end instead of at the
    next heading's line start → the blank lines between are in no section →
    the validator refuses (partition) → fails."""
    text = fixture_doc.content
    s = _build(text)
    secs = s.sections
    # Level-1 sections tile the text; each child ends at its next sibling's
    # start or at its parent's end.
    tops = [x for x in secs if x.level == 1]
    assert tops[0].start == 0 and tops[-1].end == len(text)
    assert all(a.end == b.start for a, b in zip(tops, tops[1:], strict=False))
    installing, usage = secs[1], secs[2]
    assert usage.end == installing.end == secs[3].start
    # The separator before the next heading belongs to the preceding section.
    assert text[secs[3].start - 2:secs[3].start] == "\n\n"
    assert text[secs[3].start:].startswith("Setext title\n====")


def test_rank_is_recorded_and_never_stamped(fixture_doc) -> None:
    text = fixture_doc.content
    s = _build(text)
    classify_roles(s.sections)
    validate_structure(s, text, require_roles=True)
    deep = s.sections[-1]
    out = stamp(s, deep.start, deep.end, text[deep.start:deep.end])
    assert out["section_level"] == 3 and out["section_title"] == "Results and discussion"
    # Own tags [discussion, results] plus `other` from the two titled,
    # unmatched ancestors (`other` is kept beside inherited tags, §6).
    assert out["section_roles"] == ["discussion", "other", "results"]
    assert deep.rank == 3
    assert out["structure_version"] == "markdown/1;stamp/1"
    assert not any("rank" in k for k in out)


# --------------------------------------------------------------------------- #
# The rules in isolation
# --------------------------------------------------------------------------- #

def test_bom_shift_keeps_every_offset_in_content_coordinates() -> None:
    """MUTATION: parse ``content`` unshifted (BOM included) → front matter
    becomes a heading and the block offsets are off by one → fails."""
    text = "﻿---\nk: v\n---\n# Title\n\nBody.\n"
    s = _build(text)
    assert [(b.kind, text[b.start:b.end]) for b in s.blocks] == [
        ("front_matter", "---\nk: v\n---\n"), ("heading", "Title"), ("prose", "Body.\n")]
    assert _outline(s) == [(None, 1, None), ("Title", 1, 1)]


def test_a_bom_alone_before_the_first_heading_makes_no_preamble() -> None:
    text = "﻿# Title\n\nBody.\n"
    s = _build(text)
    assert _outline(s) == [("Title", 1, 1)]
    assert s.sections[0].start == 0 and text[s.blocks[0].start:s.blocks[0].end] == "Title"


def test_whitespace_before_the_first_heading_makes_no_preamble() -> None:
    text = "\n\n  \n# Title\n\nBody.\n"
    s = _build(text)
    assert _outline(s) == [("Title", 1, 1)] and s.sections[0].start == 0


def test_a_link_reference_definition_is_preamble_text() -> None:
    """It produces no block token, but it is text: an untitled section holds it."""
    text = "[ref]: https://example.org/x\n\n# Title\n"
    s = _build(text)
    assert _outline(s) == [(None, 1, None), ("Title", 1, 1)]


def test_no_heading_is_one_untitled_section_and_empty_is_none() -> None:
    s = _build("Just a paragraph.\n\n- a list\n")
    assert _outline(s) == [(None, 1, None)] and (s.sections[0].start, s.sections[0].end) == (0, 28)
    assert md.markdown_structure(_doc("")) is None


def test_level_is_tree_depth_not_rank() -> None:
    """Q11: ``#`` then ``###`` is depth 2 rank 3; a document starting at
    ``##`` is depth 1 rank 2; a ``##`` after a ``###`` is the ``###``'s sibling."""
    s = _build("## Start\n\ntext\n\n# Top\n\n### Skip\n\nx\n\n## Mid\n\ny\n")
    assert _outline(s) == [("Start", 1, 2), ("Top", 1, 1), ("Skip", 2, 3), ("Mid", 2, 2)]
    assert s.sections[3].path == ["Top", "Mid"]


def test_closing_sequence_and_indentation_are_markup() -> None:
    text = "   ##   Spaced title   ##  \n\nx\n\n# Hash#tag # \n"
    s = _build(text)
    assert _headings(text, s) == ["Spaced title", "Hash#tag"]
    assert [x.title for x in s.sections] == ["Spaced title", "Hash#tag"]


def test_an_empty_heading_opens_an_untitled_section_without_a_heading_block() -> None:
    text = "# One\n\na\n\n#\n\nb\n\n## Two\n"
    s = _build(text)
    assert _outline(s) == [("One", 1, 1), (None, 1, 1), ("Two", 2, 2)]
    assert s.sections[2].path == ["Two"]  # an untitled ancestor adds nothing
    assert _headings(text, s) == ["One", "Two"]


def test_headings_inside_quotes_and_lists_open_no_section() -> None:
    text = "# Real\n\n> # quoted heading\n\n    # indented code\n\n- # listed heading\n"
    s = _build(text)
    assert _outline(s) == [("Real", 1, 1)]
    assert [b.kind for b in s.blocks] == ["heading", "quote", "code", "list"]


def test_a_multi_line_setext_heading_is_titled_by_its_first_line() -> None:
    """The first-line rule (§9.1, a decision): the title and the heading block
    are the first line only — not the whole heading content."""
    text = "First *line*\nsecond line\n===\n\nBody.\n"
    s = _build(text)
    assert [x.title for x in s.sections] == ["First line"]
    assert _headings(text, s) == ["First *line*"]


def test_html_tags_and_code_in_a_title() -> None:
    text = "# Run `tool --x` <kbd>now</kbd>\n"
    s = _build(text)
    assert s.sections[0].title == "Run tool --x now"
    assert _headings(text, s) == ["Run `tool --x` <kbd>now</kbd>"]


def test_crlf_and_lone_cr_content_keeps_code_point_offsets() -> None:
    """A record from another producer may carry ``\\r\\n`` or a lone ``\\r``
    (``TextFileLoader`` never does). The parser sees one ``\\n`` for each; the
    line table maps back, so offsets stay ``content``'s."""
    text = ("intro\r\n\r\n# One\r\n\r\npara\r\n\r\n"
            "![Figure 3. split\r\nover lines](f.png)\r\n\r\n"
            "Two\r-----\r\rend\r")
    s = _build(text)
    assert _outline(s) == [(None, 1, None), ("One", 1, 1), ("Two", 2, 2)]
    assert _headings(text, s) == ["One", "Two"]
    cap = next(b for b in s.blocks if b.kind == "figure_caption")
    assert (cap.label, text[cap.start:cap.end]) == ("Figure 3", "Figure 3. split\r\nover lines")
    assert text[s.sections[2].start:].startswith("Two\r---")


def test_a_label_paragraph_not_after_an_image_is_prose() -> None:
    text = "Figure 4. shows nothing in particular.\n"
    assert [b.kind for b in _build(text).blocks] == ["prose"]


def test_a_table_label_after_an_image_is_not_a_figure_caption() -> None:
    text = "![x](a.png)\n\nTable 2. not a figure caption\n"
    assert [b.kind for b in _build(text).blocks] == ["figure", "prose"]


# --------------------------------------------------------------------------- #
# attach_structure with the registered adapter
# --------------------------------------------------------------------------- #

def test_attach_dispatches_on_the_md_suffix_only_and_counts_the_backend() -> None:
    text = "# Title\n\nBody.\n"
    docs = [_doc(text, "/d/a.md"), _doc(text, "/d/B.MD"), _doc(text, "/d/c.txt"),
            _doc(text, "/d/d.pdf")]
    counts: Counter[str] = Counter()
    attach_structure(docs, counts=counts)
    assert [d.structure is not None for d in docs] == [True, True, False, False]
    assert all(d.content == text for d in docs)
    backend = md.backend()
    assert backend.startswith("markdown-it-py/") and ";mdit-py-plugins/" in backend
    assert counts == Counter({f"backend:{backend}": 2})


def test_attach_never_replaces_a_structure_that_arrived_with_the_document() -> None:
    from tests.structure_support import paper

    content, structure = paper()
    doc = Document(id="d", content=content, source="/d/x.md", structure=structure)
    attach_structure([doc])
    assert doc.structure is structure


def test_provenance_names_the_adapter_and_the_backend() -> None:
    text = "# Title\n\nBody.\n"
    doc = _doc(text)
    counts: Counter[str] = Counter()
    attach_structure([doc], counts=counts)
    counts["adapter:markdown/1"] += 1  # what stamp_documents counts
    rec = structure_provenance(counts)
    assert rec is not None
    assert rec["structure"]["adapters"] == {"markdown/1": 1}
    assert rec["structure"]["backend"] == md.backend()


def test_an_adapter_error_on_one_document_drops_its_structure_only(monkeypatch) -> None:
    def boom(doc):  # noqa: ANN001
        raise ValueError("parse and text disagree")

    monkeypatch.setattr(md, "markdown_structure", boom)
    doc = _doc("# T\n", "/d/a.md")
    counts: Counter[str] = Counter()
    attach_structure([doc], counts=counts)
    assert doc.structure is None and doc.content == "# T\n"
    assert counts == Counter({"structure_invalid:ValueError": 1})


# --------------------------------------------------------------------------- #
# Property: random Markdown always gives a valid structure with exact titles
# --------------------------------------------------------------------------- #

_WORDS = ["alpha", "beta", "gamma", "delta", "omega", "kappa", "sigma"]


def _random_markdown(seed: int) -> tuple[str, list[tuple[str, str, int]]]:
    """A random page and the (title, raw heading text, rank) of each top-level
    heading it contains, in order. Pieces are separated by blank lines so no
    piece can change how its neighbour parses."""
    rng = random.Random(seed)
    nl = rng.choice(["\n", "\r\n", "\r"])
    parts: list[str] = []
    heads: list[tuple[str, str, int]] = []
    if rng.random() < 0.3:
        parts.append("﻿" if rng.random() < 0.5 else "")
        parts.append(f"---{nl}key: value{nl}---{nl}{nl}")
    for _ in range(rng.randint(0, 14)):
        kind = rng.choice(["atx", "atx", "setext", "para", "fence", "quote", "list", "math"])
        words = rng.sample(_WORDS, rng.randint(1, 3))
        if rng.random() < 0.3:
            words[0] = f"*{words[0]}*"
        raw = " ".join(words)
        title = raw.replace("*", "")
        if kind == "atx":
            rank = rng.randint(1, 6)
            close = " " + "#" * rng.randint(1, 3) if rng.random() < 0.3 else ""
            # No indentation here: after a list, an indented line continues
            # the list item (indentation has its own test).
            parts.append(f"{'#' * rank} {raw}{close}{nl}{nl}")
            heads.append((title, raw, rank))
        elif kind == "setext":
            rank = rng.choice([1, 2])
            parts.append(f"{raw}{nl}{('=' if rank == 1 else '-') * rng.randint(1, 8)}{nl}{nl}")
            heads.append((title, raw, rank))
        elif kind == "para":
            parts.append(f"{raw} text{nl}more {raw}{nl}{nl}")
        elif kind == "fence":
            parts.append(f"```{nl}# {raw}{nl}{raw}{nl}===={nl}```{nl}{nl}")
        elif kind == "quote":
            parts.append(f"> # {raw}{nl}{nl}")
        elif kind == "list":
            parts.append(f"- ## {raw}{nl}- item{nl}{nl}")
        else:
            parts.append(f"$${nl}x = {len(raw)}{nl}$${nl}{nl}")
    return "".join(parts), heads


@pytest.mark.parametrize("seed", range(150))
def test_random_markdown_gives_a_valid_structure_with_exact_titles(seed) -> None:
    text, heads = _random_markdown(seed)
    if not text:
        assert md.markdown_structure(_doc(text)) is None
        return
    s = _build(text)  # validates every §2 invariant
    titled = [x for x in s.sections if x.title is not None]
    assert [(x.title, x.rank) for x in titled] == [(t, r) for t, _raw, r in heads]
    assert _headings(text, s) == [raw for _t, raw, _r in heads]
    classify_roles(s.sections)
    validate_structure(s, text, require_roles=True)


# --------------------------------------------------------------------------- #
# Review of #843
# --------------------------------------------------------------------------- #

def test_an_unclosed_dollar_line_does_not_swallow_headings_across_blank_lines() -> None:
    """MUTATION: dollarmath's default ``allow_blank_lines=True`` → the
    ``$$E…`` line opens a math block running to the closing ``$$`` line, and
    the heading between is gone (one untitled section, one math block)."""
    text = "$$E = mc^2$$ and text\n\n# Heading\n\ntext\n\n$$\na\n$$\n"
    s = _build(text)
    assert _outline(s) == [(None, 1, None), ("Heading", 1, 1)]
    assert [(b.kind, text[b.start:b.end]) for b in s.blocks] == [
        ("prose", "$$E = mc^2$$ and text\n"), ("heading", "Heading"), ("prose", "text\n"),
        ("math", "$$\na\n$$\n")]


@pytest.mark.parametrize("text, outline", [
    # A space-only line is CommonMark-blank: no preamble.
    (" \n# Title\n\nBody\n", [("Title", 1, 1)]),
    # A form feed is not blank to CommonMark: it is a paragraph, so it needs
    # its own untitled section (else the heading is not its section's first
    # block and the whole structure is refused).
    ("\x0c\n# Title\n", [(None, 1, None), ("Title", 1, 1)]),
    ("\u00a0\n# Title\n", [(None, 1, None), ("Title", 1, 1)]),
])
def test_the_preamble_rule_uses_commonmark_blankness(text, outline) -> None:
    """MUTATION: ``str.strip()`` (Unicode whitespace) for the preamble test →
    the form-feed / NBSP cases lose their preamble → invariant 5 → fails."""
    assert _outline(_build(text)) == outline


@pytest.mark.parametrize("text", ["# T\x00x\n\nb\n", "T\x00x\n===\n"])
def test_a_nul_in_a_heading_keeps_the_structure(text) -> None:
    """The parser reads NUL as U+FFFD (1:1, so columns hold); the heading-span
    check compares against the same replacement. The title is the PARSE's text
    (``U+FFFD``); the block covers the raw source. MUTATION: compare against
    the raw line → ValueError → the structure is dropped → fails."""
    s = _build(text)
    assert [x.title for x in s.sections] == ["T\ufffdx"]
    assert _headings(text, s) == ["T\x00x"]


def test_concurrent_first_use_of_a_fresh_parser_gives_identical_structures(
    monkeypatch,
) -> None:
    """``Ruler`` compiles its chains lazily and publishes an empty cache before
    filling it, so a concurrent FIRST parse could see an empty or partial chain
    and return a silently wrong (but valid) structure. The parser is warmed
    inside the lock before it is published. MUTATION: publish before warming
    (or drop the warm-up) → wrong structures at a high rate under this test's
    slowed compile and tiny switch interval."""
    from markdown_it import ruler as ruler_mod

    text = ("# Title\n\nBody *x* `y` [l](u).\n\n## Sub\n\n- a\n- b\n\n```\n# c\n```\n\n"
            "> q\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n$$\nm\n$$\n")
    doc = _doc(text)
    md.reset_parser()
    expected = md.markdown_structure(doc)
    assert expected is not None
    expected_dump = expected.model_dump()

    real_compile = ruler_mod.Ruler.__compile__

    def slow_compile(self):  # noqa: ANN001
        # markdown-it publishes an empty cache dict, then fills it. Hold that
        # window open so a concurrent getRules() would see it.
        self.__cache__ = {}
        time.sleep(0.002)
        real_compile(self)

    monkeypatch.setattr(ruler_mod.Ruler, "__compile__", slow_compile)
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    wrong = 0
    try:
        for _ in range(40):
            md.reset_parser()
            results: list[object] = [None] * 8
            barrier = threading.Barrier(8)

            def run(i: int, barrier=barrier, results=results) -> None:  # noqa: ANN001
                barrier.wait()
                try:
                    r = md.markdown_structure(doc)
                    results[i] = r.model_dump() if r is not None else None
                except Exception as e:  # noqa: BLE001
                    results[i] = f"{type(e).__name__}: {e}"

            threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            wrong += sum(r != expected_dump for r in results)
    finally:
        sys.setswitchinterval(old_interval)
        md.reset_parser()
    assert wrong == 0


# --------------------------------------------------------------------------- #
# Review of #844 (the docs PR) against #843
# --------------------------------------------------------------------------- #

def _atx_title(first_line: str) -> str | None:
    """What a one-line ATX heading with this text is titled — the reference
    the setext first-line rule must agree with."""
    s = _build(f"# {first_line}\n")
    return s.sections[0].title


@pytest.mark.parametrize("text, first_line, title", [
    # a maths span crossing the line break
    ("a $y\nx$ b\n===\n", "a $y", "a $y"),
    # a code span crossing it: markdown-it turns its newline into a space
    ("Title `a\nb` more\n===\n", "Title `a", "Title `a"),
    # a raw HTML tag crossing it: the tag is dropped, line 2's text is not
    ('Title <span\nclass="x">more</span>\n===\n', "Title <span", "Title <span"),
    # an ordinary two-line setext heading
    ("First *line*\nsecond line\n===\n", "First *line*", "First line"),
])
def test_a_setext_title_is_the_plain_text_of_its_first_source_line_only(
    text, first_line, title
) -> None:
    """MUTATION: take the title from the whole heading's inline stream (cut at
    the first break or at a ``\\n``) → the code-span and HTML cases read
    ``Title a b more`` / ``Title more`` → fails."""
    s = _build(text)
    assert _headings(text, s) == [first_line]
    assert [x.title for x in s.sections] == [title]
    assert s.sections[0].title == _atx_title(first_line)


def test_a_setext_first_line_resolves_reference_links_like_atx() -> None:
    text = "See [the guide][g]\nmore\n===\n\n[g]: https://example.org/g\n"
    s = _build(text)
    assert s.sections[0].title == "See the guide"


def _figure_blocks(text: str) -> list[tuple[str, str | None, str]]:
    s = _build(text)
    return [(b.kind, b.label, text[b.start:b.end]) for b in s.blocks]


def test_a_linked_image_with_a_labelled_alt_is_split_into_caption_and_figure() -> None:
    """MUTATION: gate the alt caption on an unlinked ``![`` only → the linked
    image is one bare figure and its label is lost → fails."""
    text = "[![Figure 1: x](p.png)](https://example.org/u)\n"
    assert _figure_blocks(text) == [
        ("figure_caption", "Figure 1", "Figure 1: x"),
        ("figure", None, "](p.png)](https://example.org/u)\n"),
    ]


def test_an_unlinked_image_with_a_labelled_alt_is_split_into_caption_and_figure() -> None:
    text = "![Figure 2. y](q.png)\n"
    assert _figure_blocks(text) == [
        ("figure_caption", "Figure 2", "Figure 2. y"),
        ("figure", None, "](q.png)\n"),
    ]


def test_a_linked_image_without_a_label_is_one_figure() -> None:
    text = "[![a diagram](p.png)](https://example.org/u)\n\nplain text after\n"
    assert _figure_blocks(text) == [
        ("figure", None, "[![a diagram](p.png)](https://example.org/u)\n"),
        ("prose", None, "plain text after\n"),
    ]


def test_an_alt_caption_wins_over_a_labelled_following_paragraph() -> None:
    """Precedence: when both carry a label, the alt is the caption and the
    following paragraph stays prose (one figure, one caption)."""
    text = "![Figure 3. alt caption](r.png)\n\nFigure 3: also looks like a caption.\n"
    assert _figure_blocks(text) == [
        ("figure_caption", "Figure 3", "Figure 3. alt caption"),
        ("figure", None, "](r.png)\n"),
        ("prose", None, "Figure 3: also looks like a caption.\n"),
    ]
