"""#828 PR 4: the stripped Markdown render, ``text_render="markdown-plain/1"``.

One parse, two outputs (§9.1): the plain text that REPLACES ``content`` and its
``markdown/1`` structure, with every offset born at an emitter position. The
adversarial fixture is the #830 parity fixture read through ``TextFileLoader``
exactly as both ingest paths read it. Every expectation is written out by
hand, never recomputed by the code under test. Synthetic text only.

Needs the ``[markdown]`` extra (skipped without it; the refusal is tested in
``test_structure_markers.py``, which hides the extra).
"""
from __future__ import annotations

import random
import re
from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("mdit_py_plugins", reason="the [markdown] extra is not installed")

from ragstack.ingestion import markdown_structure as md  # noqa: E402
from ragstack.ingestion.loaders import TextFileLoader  # noqa: E402
from ragstack.ingestion.structure import (  # noqa: E402
    apply_text_render,
    classify_roles,
    content_sha256,
    validate_structure,
)
from ragstack.models import Document  # noqa: E402
from tests.ingestion.test_extract_text_parity import _MARKDOWN  # noqa: E402
from tests.structure_support import paper  # noqa: E402

RENDER = "markdown-plain/1"


def _doc(text: str, source: str = "/docs/page.md") -> Document:
    return Document(id="d", content=text, source=source)


def _render(text: str) -> tuple[str, list]:
    out, s = md.markdown_plain(_doc(text))
    assert s is not None
    validate_structure(s, out)
    return out, s


@pytest.fixture
def fixture_doc(tmp_path) -> Document:
    path = tmp_path / "guide.md"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(_MARKDOWN)
    [doc] = TextFileLoader().load(str(path))
    assert doc.content.startswith("﻿---\n")
    return doc


@pytest.fixture
def rendered(fixture_doc):
    text, s = md.markdown_plain(fixture_doc)
    assert s is not None
    return text, s


# --------------------------------------------------------------------------- #
# Every content word survives (#828 §2)
# --------------------------------------------------------------------------- #

#: What the render must keep: link and image targets, code (inline, fenced,
#: indented, with shell variables), table cells (an escaped pipe included),
#: maths with its delimiters, front matter, inline-HTML text and href, the
#: text inside <details>, the Unicode line.
FIXTURE_NEEDLES = (
    "https://example.org/docs/guide.html#install",   # link target
    "user guide (https://example.org/docs/guide.html#install)",
    "https://example.org/raw",                       # autolink, once
    "diagram (images/arch.png)",                     # image: alt (src)
    "here (https://example.org/ref)",                # reference link resolved
    "anchor (https://example.org/a?b=1&c=2)",        # inline HTML: text + href
    "inline HTML",
    "ragstack-ctl --tenant $TENANT",                 # inline code verbatim
    "snake_case_identifier",                         # intraword _ stays
    "${HOME}/bin",
    "*escaped*",                                     # escapes resolved
    "export RAG_DATA=$HOME/rag   # comment",         # fenced code, verbatim
    'for f in *.md; do echo "$f"; done',
    "indented code block",
    "flag | default | meaning",                      # table: one row per line
    "--size | 512 | tokens per chunk",
    "--url | http://127.0.0.1:1 | a | pipe",         # escaped pipe kept as text
    "quoted text",
    "item one",
    "nested Ctrl",
    "$$E = mc^2$$ and $x_i$",                        # maths verbatim
    "More hidden body",                              # <details> text
    "$$\na^2 + b^2 = c^2\n$$",
    'title: "Install guide"',                        # front matter body
    "tags: [cli, setup]",
    "→ ≤ µg ✓ α-helix \U0001f9ec",
    "# not a heading: a comment inside a fence",      # a fence keeps its '#'
    "Figure 1. A synthetic pipeline diagram (images/fig1.png)",
    "plain alt (images/fig2.png)",
    "Closing text.",
)

#: Markup the render drops.
FIXTURE_GONE = (
    "# Installing", "## Usage", "**bold**", "_emph_", "](", "<a ", "</a>", "<span",
    "<br", "<details>", "<!--", "```", "|---", "============", "---------------",
    "### Results", "> quoted", "- item one", "1. nested", "\r", "﻿",
)


def test_every_content_word_survives(rendered) -> None:
    text, _ = rendered
    for needle in FIXTURE_NEEDLES:
        assert needle in text, needle


def test_markup_is_dropped(rendered) -> None:
    text, _ = rendered
    for gone in FIXTURE_GONE:
        assert gone not in text, gone
    assert "bold" in text and "emph" in text


_VOCAB = ["alpha", "beta", "gamma", "delta", "omega", "kappa", "sigma", "theta", "zeta"]


def _random_page(seed: int) -> tuple[str, list[str]]:
    """A random page and every content word placed in it: in prose, emphasis,
    link text AND target, image alt AND target, inline code, fenced code,
    table cells, inline and block maths, inline HTML text and href."""
    rng = random.Random(seed)
    words: list[str] = []

    def w() -> str:
        x = f"{rng.choice(_VOCAB)}{rng.randint(0, 999)}"
        words.append(x)
        return x

    parts = []
    for _ in range(rng.randint(1, 12)):
        kind = rng.choice(["h", "para", "link", "image", "code", "fence", "table",
                           "math", "html", "list", "quote"])
        if kind == "h":
            parts.append(f"{'#' * rng.randint(1, 4)} {w()} *{w()}*")
        elif kind == "para":
            parts.append(f"{w()} **{w()}** _{w()}_ {w()}")
        elif kind == "link":
            parts.append(f"see [{w()}](https://example.org/{w()}) now")
        elif kind == "image":
            parts.append(f"text ![{w()}](img/{w()}.png) after")
        elif kind == "code":
            parts.append(f"run `{w()} --{w()}` here")
        elif kind == "fence":
            parts.append(f"```\n{w()} = {w()}\n```")
        elif kind == "table":
            parts.append(f"| {w()} | {w()} |\n|---|---|\n| `{w()}` | {w()} |")
        elif kind == "math":
            parts.append(f"inline ${w()}$ and\n\n$$\n{w()}^2\n$$")
        elif kind == "html":
            parts.append(f'an <a href="https://h.example/{w()}">{w()}</a> link')
        elif kind == "list":
            parts.append(f"- {w()}\n- {w()}")
        else:
            parts.append(f"> {w()} {w()}")
    return "\n\n".join(parts) + "\n", words


@pytest.mark.parametrize("seed", range(60))
def test_random_pages_keep_every_word_and_a_valid_structure(seed) -> None:
    src, words = _random_page(seed)
    text, s = _render(src)
    for word in words:
        assert word in text, (word, src)
    classify_roles(s.sections)
    validate_structure(s, text, require_roles=True)
    for b in s.blocks:
        if b.kind == "heading":
            assert not text[b.start:b.end].startswith("#")


# --------------------------------------------------------------------------- #
# Sections and headings at emitter positions
# --------------------------------------------------------------------------- #

FIXTURE_OUTLINE = [
    (None, 1, None),                       # front matter preamble
    ("Installing   the tool", 1, 1),       # emphasis removed, spacing verbatim
    ("Usage", 2, 2),
    ("Setext title", 1, 1),
    ("Second setext", 2, 2),
    ("Results and discussion", 3, 3),      # the closing ### is markup
]


def test_the_outline_ranks_levels_and_paths(rendered) -> None:
    text, s = rendered
    validate_structure(s, text)
    assert [(x.title, x.level, x.rank) for x in s.sections] == FIXTURE_OUTLINE
    assert [x.path for x in s.sections] == [
        [], ["Installing   the tool"], ["Installing   the tool", "Usage"],
        ["Setext title"], ["Setext title", "Second setext"],
        ["Setext title", "Second setext", "Results and discussion"],
    ]
    assert s.adapter == "markdown/1" and s.structure_source == "parsed"
    assert s.content_sha256 == content_sha256(text)


def test_heading_block_text_equals_the_title_exactly(rendered) -> None:
    """MUTATION: emit the heading with its `#` run (or record the block over
    the separator) → the block text is not the title → fails."""
    text, s = rendered
    titled = [x for x in s.sections if x.title is not None]
    heads = [b for b in s.blocks if b.kind == "heading"]
    assert [text[b.start:b.end] for b in heads] == [x.title for x in titled]
    for x in titled:
        # the heading is on its own line, the section starts at its title
        assert text[x.start:].startswith(x.title + "\n") or text[x.start:] == x.title
        assert x.start == 0 or text[x.start - 1] == "\n"


def test_a_link_in_a_heading_renders_as_its_text_only() -> None:
    """Headings take raw mode's title rule (link text, no target); body links
    keep theirs. MUTATION: render heading links as ``text (url)`` → fails."""
    text, s = _render("# Install [the tool](https://t.example/x)\n\n"
                      "Body [link](https://b.example/y).\n")
    assert text == "Install the tool\n\nBody link (https://b.example/y)."
    [sec] = s.sections
    assert sec.title == "Install the tool"
    [head, body] = s.blocks
    assert text[head.start:head.end] == sec.title and body.kind == "prose"


_LINKED_HEADINGS = (
    "# [Introduction](#intro)\n\nLead.\n\n"
    "## [Methods](#methods)\n\nHow.\n\n"
    "### ![Results](img/r.png)\n\nWhat.\n\n"
    "## Results and [Discussion](https://d.example/x)\n\nWhy.\n\n"
    "## `run` the <kbd>tool</kbd> $x$\n\nCode.\n\n"
    "Usage [guide](#usage)\n-----\n\nSteps.\n\n"
    "## [Installation](https://i.example/)\n\nInstall.\n"
)


def test_stripped_titles_and_roles_equal_raw_mode_for_headings_with_links() -> None:
    """Roles parity: a linked heading must classify the same in both modes
    (the review found ``Methods (#methods)`` → ``other`` in stripped mode)."""
    raw = md.markdown_structure(_doc(_LINKED_HEADINGS))
    text, plain = _render(_LINKED_HEADINGS)
    assert raw is not None
    classify_roles(raw.sections)
    classify_roles(plain.sections)
    as_pairs = [(x.title, x.roles) for x in raw.sections]
    assert [(x.title, x.roles) for x in plain.sections] == as_pairs
    assert as_pairs[1] == ("Methods", ["methods"])
    assert ("Results and Discussion", ["discussion", "results"]) in as_pairs
    assert [text[b.start:b.end] for b in plain.blocks if b.kind == "heading"] == [
        x.title for x in plain.sections if x.title is not None]
    assert "img/r.png" not in text.split("\n\n")[4]  # the heading line carries no target


def test_block_kinds_and_captions_of_the_fixture(rendered) -> None:
    text, s = rendered
    kinds = [b.kind for b in s.blocks]
    assert kinds == [
        "front_matter", "heading", "prose", "heading", "prose", "code", "code", "table",
        "quote", "list", "prose", "prose", "heading", "prose", "code", "heading",
        "figure_caption", "figure", "figure", "figure_caption", "math", "heading", "prose",
    ]
    caps = [(b.label, text[b.start:b.end]) for b in s.blocks if b.kind == "figure_caption"]
    assert caps == [("Figure 1", "Figure 1. A synthetic pipeline diagram"),
                    ("Figure 2", "Figure 2: the second synthetic figure.")]


def test_top_level_blocks_are_separated_by_one_blank_line(rendered) -> None:
    text, s = rendered
    assert "\n\n\n" not in text and not text.startswith("\n") and not text.endswith("\n")
    for a, b in zip(s.blocks, s.blocks[1:], strict=False):
        between = text[a.end:b.start]
        assert between in ("", "\n\n"), (text[a.start:a.end], between)


def test_maths_delimiters_and_escapes() -> None:
    text, _ = _render("Euler \\(e^{i\\pi}\\) and \\[x\\] but \\*star\\* and &amp; done.\n")
    assert text == "Euler \\(e^{i\\pi}\\) and \\[x\\] but *star* and & done."


def test_an_empty_heading_opens_an_untitled_section_and_an_empty_one_is_left_out() -> None:
    text, s = _render("#\n\nLead under an empty heading.\n\n## Child\n\nx\n\n#\n\n# Next\n")
    assert text == "Lead under an empty heading.\n\nChild\n\nx\n\nNext"
    assert [(x.title, x.level, x.rank) for x in s.sections] == [
        (None, 1, 1), ("Child", 2, 2), ("Next", 1, 1)]
    assert not any(b.kind == "heading" and text[b.start:b.end] == "" for b in s.blocks)


def test_an_empty_heading_at_the_end_is_left_out() -> None:
    text, s = _render("# A\n\ntext\n\n#\n")
    assert text == "A\n\ntext"
    assert [(x.title, x.start, x.end) for x in s.sections] == [("A", 0, len(text))]


def test_nothing_to_render_is_empty_content_and_no_structure() -> None:
    for src in ("", "---\n", "<!-- only a comment -->\n", "#\n", "\n\n"):
        assert md.markdown_plain(_doc(src)) == ("", None), repr(src)


def test_the_render_is_deterministic_and_line_ending_blind() -> None:
    lf = "# T\n\nline one\nline two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    crlf = lf.replace("\n", "\r\n")
    a, sa = _render(lf)
    b, sb = _render("﻿" + crlf)
    assert a == b and sa == sb
    assert _render(lf) == (a, sa)


# --------------------------------------------------------------------------- #
# apply_text_render: the ingest step every path calls
# --------------------------------------------------------------------------- #

def test_apply_replaces_md_content_and_structure_and_leaves_other_suffixes(fixture_doc) -> None:
    raw = fixture_doc.content
    txt = Document(id="t", content="Plain *notes* # kept", source="/d/notes.txt")
    pdf = Document(id="p", content="# pdf text", source="/d/a.pdf")
    counts: Counter[str] = Counter()
    assert apply_text_render([fixture_doc, txt, pdf], RENDER, counts) == 1
    expected, s = md.markdown_plain(Document(id="x", content=raw, source="/x.md"))
    assert fixture_doc.content == expected and fixture_doc.structure == s
    assert txt.content == "Plain *notes* # kept" and txt.structure is None
    assert pdf.content == "# pdf text" and pdf.structure is None
    assert [k for k in counts if k.startswith("backend:markdown-it-py/")]


def test_apply_without_a_render_changes_nothing(fixture_doc) -> None:
    before = fixture_doc.model_dump()
    assert apply_text_render([fixture_doc], None) == 0
    assert fixture_doc.model_dump() == before


def test_apply_drops_a_structure_that_arrived_with_the_raw_text() -> None:
    content, structure = paper()
    doc = Document(id="d", content="# Title\n\nBody.\n", source="/d/p.md")
    doc.structure = structure  # bound to another text; the render replaces it
    apply_text_render([doc], RENDER)
    assert doc.content == "Title\n\nBody." and doc.structure is not None
    assert doc.structure.content_sha256 == content_sha256(doc.content)
    assert content  # (the paper text itself is not involved)


def test_an_invalid_rendered_structure_is_dropped_but_the_content_is_the_render(
    monkeypatch,
) -> None:
    """Metadata may degrade, identity may not: the content stays the render."""
    from ragstack.models import DocumentStructure

    def bad(doc):
        return "rendered", DocumentStructure(content_sha256="0" * 64, sections=[],
                                             blocks=[], structure_source="parsed",
                                             adapter="markdown/1")

    monkeypatch.setattr(md, "markdown_plain", bad)
    doc = Document(id="d", content="# raw", source="/d/p.md")
    counts: Counter[str] = Counter()
    apply_text_render([doc], RENDER, counts)
    assert doc.content == "rendered" and doc.structure is None
    assert counts["structure_invalid:content_sha256"] == 1


def test_the_suffix_decides_case_insensitively(tmp_path: Path) -> None:
    doc = Document(id="d", content="# A\n\nb\n", source=str(tmp_path / "PAGE.MD"))
    assert apply_text_render([doc], RENDER) == 1 and doc.content == "A\n\nb"
    assert re.fullmatch(r"A\n\nb", doc.content)


def test_concurrent_first_use_of_a_fresh_plain_parser_gives_identical_renders(
    monkeypatch,
) -> None:
    """The stripped render has its own parser (``text_join`` disabled), built,
    warmed and published under the same lock as raw mode's, for the same
    reason (§9.1: a concurrent FIRST parse could see a half-compiled rule
    chain and return a silently wrong token stream). MUTATION: publish the
    plain parser before warming it → wrong renders under this test's slowed
    compile and tiny switch interval."""
    import sys
    import threading
    import time

    from markdown_it import ruler as ruler_mod

    text = ("# Title\n\nBody *x* `y` [l](u) \\(m\\).\n\n## Sub\n\n- a\n- b\n\n```\n# c\n```\n\n"
            "> q\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n$$\nm\n$$\n")
    doc = _doc(text)
    md.reset_parser()
    expected = md.markdown_plain(doc)
    real_compile = ruler_mod.Ruler.__compile__

    def slow_compile(self):  # noqa: ANN001
        self.__cache__ = {}
        time.sleep(0.002)
        real_compile(self)

    monkeypatch.setattr(ruler_mod.Ruler, "__compile__", slow_compile)
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    wrong = 0
    try:
        for _ in range(30):
            md.reset_parser()
            results: list[object] = [None] * 8
            barrier = threading.Barrier(8)

            def run(i: int, barrier=barrier, results=results) -> None:  # noqa: ANN001
                barrier.wait()
                try:
                    results[i] = md.markdown_plain(doc)
                except Exception as e:  # noqa: BLE001
                    results[i] = f"{type(e).__name__}: {e}"

            threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            wrong += sum(r != expected for r in results)
    finally:
        sys.setswitchinterval(old_interval)
        md.reset_parser()
    assert wrong == 0
