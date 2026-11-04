"""The Markdown structure adapter: ``markdown/1`` (#828 §9.1).

Raw mode (``text_render`` absent, :func:`markdown_structure`) builds a
:class:`~ragstack.models.DocumentStructure` over ``Document.content`` exactly
as :class:`~ragstack.ingestion.loaders.TextFileLoader` stored it — the text is
never changed. Stripped mode (``text_render="markdown-plain/1"``,
:func:`markdown_plain`) renders the same parse to plain text through an
emitter and records the sections and blocks at emitter positions; it is
described at :func:`markdown_plain`, and everything below up to it is raw mode.

Parser: ``markdown-it-py`` on the ``commonmark`` preset, plus ``table`` (not in
the preset), ``mdit_py_plugins.front_matter`` and ``mdit_py_plugins.dollarmath``
— the ``[markdown]`` extra. There is no regex fallback: a ``#`` inside a fenced
block is code, and only a parser knows that (§9.1, §14). A missing extra is a
refusal (:class:`~ragstack.ingestion.structure.StructureBackendUnavailable`),
never "no structure".

How offsets are born (§2: accumulated, never searched for):

* **The BOM.** ``TextFileLoader`` keeps a byte-order mark as ``content[0]``;
  fed to the parser it moves the front matter's opening ``---`` off column 0,
  and the YAML then parses as a setext heading. So the parser reads
  ``content[1:]`` and every offset is shifted back by one. ``content`` itself is
  untouched.
* **Lines.** Block tokens carry line ranges (``token.map``, end-exclusive). The
  parser normalises line terminators (``\\r\\n`` and ``\\r`` → ``\\n``) and
  ``NUL`` → ``U+FFFD``, both of which keep every character's column, so a line
  table over ``content`` split on the same terminators maps (line, column) in
  the parser's text to a code-point offset in ``content``.
* **Headings** — top-level ATX and setext only (a heading inside a blockquote
  or list item is part of that block) — open sections, each recording its raw
  rank in ``Section.rank``. A section opens at the start of its heading's line
  and closes at the start of the next heading of equal or higher rank, or at
  the end of the text: that is already the *extended* end of §2 invariant 3
  (separators belong to the preceding section). ``level`` is tree depth (Q11).
* **The heading block is the title text only** (§2 invariant 5): after the
  indentation, the ``#`` run and its spaces, before any closing ``#`` sequence
  and trailing spaces — exactly the span the parser's inline token holds, which
  is checked character for character. A setext heading's title is its FIRST
  line (a decision, §9.1), and its block covers that line's text only.
  ``Section.title`` is the inline text with markup removed (``*tool*`` →
  ``tool``); the block holds the raw source of the same line.
* **The preamble.** Text before the first heading is an untitled level-1
  section starting at 0, when it holds anything but whitespace and the BOM
  (front matter, a lead paragraph, a link reference definition). When it holds
  nothing, the first heading's section is extended back to 0 instead, so no
  section is only whitespace. A document with no heading is one untitled
  section.
"""
from __future__ import annotations

import bisect
import functools
import re
import threading
from collections.abc import Callable, Sequence
from html.parser import HTMLParser
from importlib import metadata as importlib_metadata
from typing import TYPE_CHECKING, Any

from ragstack.models import Block, Document, DocumentStructure, Section

if TYPE_CHECKING:  # pragma: no cover - typing only
    from markdown_it import MarkdownIt
    from markdown_it.token import Token

#: The adapter's identity, in ``DocumentStructure.adapter`` and in
#: ``structure_version`` (``markdown/1;stamp/1``). Any change to what this
#: module emits for the same text is a bump.
ADAPTER = "markdown/1"
#: The extra that carries the parser (``pip install 'ragstack[markdown]'``).
EXTRA = "markdown"
_BOM = "﻿"
_TERMINATOR = re.compile(r"\r\n|\r|\n")
# What the parser does to its input before tokenising (markdown-it's
# ``normalize`` core rule). Both keep every column.
_PARSER_NEWLINES = re.compile(r"\r\n?")

_lock = threading.Lock()
_parser_cache: MarkdownIt | None = None
#: The stripped render's parser (``text_render="markdown-plain/1"``): the same
#: configuration with markdown-it's ``text_join`` core rule disabled, so a
#: backslash escape stays its own ``text_special`` token and ``\\(…\\)`` maths
#: can be kept verbatim. A separate instance, so raw mode (``markdown/1``) is
#: byte-for-byte what it was.
_plain_parser_cache: MarkdownIt | None = None


class MarkdownBackendMissing(ImportError):
    """The ``[markdown]`` extra is not installed."""


#: Parsed once, inside the lock, before the parser is published: it touches
#: every rule chain the configuration enables (core; block incl. front matter,
#: heading, setext, list, quote, fence, indented code, table, math block, html;
#: inline incl. emphasis, code, link, image, autolink, html, math inline; the
#: inline post-processors), so each ``Ruler`` has compiled its cache.
_WARM_UP = (
    "---\nk: v\n---\n"
    "# a *b* `c` [l](u) ![i](p) <http://x> <kbd>k</kbd> $m$ &amp; \\*\n\n"
    "Setext\n===\n\n"
    "- d\n- e\n\n1. f\n\n> g\n\n```\nh\n```\n\n    i\n\n"
    "| f | g |\n|---|---|\n| h | i |\n\n"
    "$$\nh\n$$\n\n<div>j</div>\n\n---\n\n[r]: http://y\n"
)
_WARM_UP_INLINE = "a *b* `c` [l](u) ![i](p) <http://x> <kbd>k</kbd> $m$ &amp; \\*"


def _parser() -> MarkdownIt:
    """The one configured parser, built on first use. Raises
    :class:`MarkdownBackendMissing` when the extra is absent.

    Thread safety: ``MarkdownIt.parse`` builds a fresh state per call, but each
    ``Ruler`` compiles its rule chains LAZILY on first use, publishing an empty
    cache dict before filling it — a concurrent first parse can see an empty or
    partial chain and return a silently wrong token stream (which the validator
    would accept). So the parser is built into a local, warmed with a parse that
    compiles every ruler, and only then published, all under the lock. After
    that nothing mutates it (``enable``/``disable``/``use`` are never called
    again), so one instance serves concurrent worker threads."""
    global _parser_cache
    with _lock:
        if _parser_cache is None:
            _parser_cache = _build_parser(plain=False)
        return _parser_cache


def _plain_parser() -> MarkdownIt:
    """The stripped render's parser: :func:`_parser`'s configuration with
    ``text_join`` disabled, built, warmed and published under the same lock
    for the same reason."""
    global _plain_parser_cache
    with _lock:
        if _plain_parser_cache is None:
            _plain_parser_cache = _build_parser(plain=True)
        return _plain_parser_cache


def _build_parser(*, plain: bool) -> MarkdownIt:
    """Build and warm one configured parser (call under :data:`_lock`)."""
    try:
        from markdown_it import MarkdownIt
        from mdit_py_plugins.dollarmath import dollarmath_plugin
        from mdit_py_plugins.front_matter import front_matter_plugin
    except ImportError as e:
        raise MarkdownBackendMissing(str(e)) from e
    parser = (
        MarkdownIt("commonmark")
        .enable("table")
        .use(front_matter_plugin)
        # allow_blank_lines=False: with the plugin's default (True), a
        # line opening with `$$` that does not close on that line runs
        # across blank lines to the next line ending in `$$`, turning
        # every heading in between into "math" (review of #843).
        .use(dollarmath_plugin, allow_blank_lines=False)
    )
    if plain:
        parser.disable("text_join")
    parser.parse(_WARM_UP)
    parser.parseInline(_WARM_UP_INLINE)  # the setext first-line title path
    return parser


def reset_parser() -> None:
    """Forget the cached parsers (tests that simulate a missing extra)."""
    global _parser_cache, _plain_parser_cache
    with _lock:
        _parser_cache = None
        _plain_parser_cache = None


def require() -> None:
    """Raise :class:`MarkdownBackendMissing` unless the parser can be built."""
    _parser()


@functools.cache
def _versions() -> str:
    parts = []
    for dist in ("markdown-it-py", "mdit-py-plugins"):
        try:
            parts.append(f"{dist}/{importlib_metadata.version(dist)}")
        except importlib_metadata.PackageNotFoundError:  # pragma: no cover - vendored
            parts.append(f"{dist}/unknown")
    return ";".join(parts)


def backend() -> str:
    """The parser that builds the structure, for ``enrichment.structure.backend``
    — the audit that would have caught #815's silent fallback. Both packages,
    because the plugins decide what front matter and ``$$`` maths become."""
    require()
    return _versions()


# --------------------------------------------------------------------------- #
# Coordinates
# --------------------------------------------------------------------------- #

class _Lines:
    """Line starts of ``content`` (from ``shift`` on), split on the terminators
    the parser normalises, so parser line ``k`` column ``c`` is
    ``starts[k] + c`` in ``content``."""

    def __init__(self, content: str, shift: int) -> None:
        self.n = len(content)
        starts = [shift]
        for m in _TERMINATOR.finditer(content, shift):
            starts.append(m.end())
        # A text ending in a terminator has no line after it; the parser's maps
        # can still name that line index as an exclusive end.
        self.starts = starts
        self.content = content

    def start(self, line: int) -> int:
        """Offset of the start of ``line``; ``len(content)`` past the end."""
        return self.starts[line] if line < len(self.starts) else self.n

    def text(self, line: int) -> tuple[int, str]:
        """``(offset, text)`` of ``line`` without its terminator."""
        s = self.start(line)
        m = _TERMINATOR.search(self.content, s)
        return s, self.content[s:m.start() if m else self.n]


# --------------------------------------------------------------------------- #
# Inline text
# --------------------------------------------------------------------------- #

_BREAKS = frozenset({"softbreak", "hardbreak"})
#: Inline tokens whose ``content`` is visible text.
_TEXTUAL = frozenset({"text", "code_inline", "math_inline", "math_inline_double",
                      "text_special"})


def _plain(children: Sequence[Token] | None) -> str:
    """The visible text of inline ``children`` with the markup removed —
    emphasis, links, inline HTML tags dropped; code and maths verbatim; an
    image contributes its alt text."""
    out: list[str] = []
    for t in children or ():
        if t.type in _BREAKS:
            out.append("\n" if t.type == "hardbreak" else " ")
        elif t.type in _TEXTUAL:
            out.append(t.content)
        elif t.type == "image":
            out.append(_plain(t.children))
    return "".join(out)


def _title(children: Sequence[Token] | None) -> str | None:
    return _plain(children).strip() or None


def _first_line_title(body: str, env: dict[str, Any]) -> str | None:
    """A setext heading's title: the plain text of its FIRST SOURCE LINE only
    (§9.1's first-line rule), whatever the inline spans of the whole heading
    do. A code span, a maths span or a raw HTML tag may cross the line break
    with no break token between, so cutting the whole heading's inline
    stream cannot find the line end; parsing the first line on its own can.
    ``env`` is the document parse's environment (its link reference
    definitions), so ``[text][ref]`` reads exactly as it does in a one-line
    ATX heading with the same text."""
    [inline] = _parser().parseInline(body, env)
    return _title(inline.children)


# --------------------------------------------------------------------------- #
# Headings
# --------------------------------------------------------------------------- #

def _atx_block(lines: _Lines, line: int, raw: str) -> tuple[int, int] | None:
    """The title-only span of an ATX heading: ``raw`` (the parser's inline
    content, already without markup around it) located after the ``#`` run."""
    off, text = lines.text(line)
    text = text.replace("\x00", "\ufffd")  # the parser's NUL rule; 1:1, columns kept
    i = len(text) - len(text.lstrip(" \t"))
    while i < len(text) and text[i] == "#":
        i += 1
    rest = text[i:]
    start = i + (len(rest) - len(rest.lstrip()))
    if not raw:
        return None
    if text[start:start + len(raw)] != raw:
        raise ValueError(f"heading on line {line + 1}: title span does not match the parse")
    return off + start, off + start + len(raw)


def _setext_block(lines: _Lines, line: int, inline: Token) -> tuple[int, int, str] | None:
    """The span of a setext heading's FIRST line's text (the title rule), and
    that text as the parser saw it (NUL as U+FFFD)."""
    off, text = lines.text(line)
    text = text.replace("\x00", "\ufffd")  # the parser's NUL rule; 1:1, columns kept
    start = len(text) - len(text.lstrip())
    body = text.strip()
    kids = inline.children or []
    first_break = next((k for k in kids if k.type in _BREAKS), None)
    if first_break is not None and first_break.type == "hardbreak" and body.endswith("\\"):
        body = body[:-1].rstrip()  # a backslash hard break is markup, not title
    if not body:
        return None
    if not inline.content.startswith(body):
        raise ValueError(f"heading on line {line + 1}: title span does not match the parse")
    return off + start, off + start + len(body), body


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #

_CONTAINER_KINDS = {
    "table_open": "table",
    "bullet_list_open": "list",
    "ordered_list_open": "list",
    "blockquote_open": "quote",
}
_LEAF_KINDS = {
    "front_matter": "front_matter",
    "fence": "code",
    "code_block": "code",
    "math_block": "math",
    "math_block_label": "math",
    "html_block": "prose",
}
_WRAPPERS = frozenset({"link_open", "link_close"})


def _sole_image(inline: Token) -> Token | None:
    """The image of a paragraph that consists only of one image (optionally
    wrapped in a link), else ``None``."""
    images = []
    for t in inline.children or ():
        if t.type == "image":
            images.append(t)
        elif t.type in _WRAPPERS or t.type in _BREAKS or (t.type == "text" and not t.content.strip()):
            continue
        else:
            return None
    return images[0] if len(images) == 1 else None


def _figure_label(text: str) -> str | None:
    from ragstack.ingestion.structure import normalise_label

    label = normalise_label(text)
    return label if label is not None and label.startswith("Figure ") else None


def _top_level(tokens: list[Token]) -> list[tuple[int, Token, Token | None]]:
    """``(index, token, inline)`` for every nesting-level-0 block token that
    opens or is a block, with the inline child token where there is one."""
    out = []
    for i, t in enumerate(tokens):
        if t.level != 0 or t.map is None or t.nesting == -1:
            continue
        inline = tokens[i + 1] if i + 1 < len(tokens) and tokens[i + 1].type == "inline" else None
        out.append((i, t, inline))
    return out


# --------------------------------------------------------------------------- #
# The adapter
# --------------------------------------------------------------------------- #

def markdown_structure(doc: Document) -> DocumentStructure | None:
    """``markdown/1`` raw mode over ``doc.content`` (§9.1). ``None`` for an
    empty text (no valid section can cover nothing). Raises
    :class:`MarkdownBackendMissing` when the extra is absent, and
    ``ValueError`` if the parse and the text ever disagree about a heading's
    position — never a guessed offset."""
    from ragstack.ingestion.structure import content_sha256

    content = doc.content
    n = len(content)
    if not n:
        return None
    shift = 1 if content.startswith(_BOM) else 0
    src = _PARSER_NEWLINES.sub("\n", content[shift:])
    env: dict[str, Any] = {}
    tokens = _parser().parse(src, env)
    lines = _Lines(content, shift)

    blocks: list[Block] = []
    # (section start, rank, title) per heading, in document order.
    heads: list[tuple[int, int, str | None, int]] = []
    top = _top_level(tokens)
    skip_caption_for: int | None = None  # index into ``top`` already consumed as a caption
    for j, (_i, t, inline) in enumerate(top):
        assert t.map is not None
        lo, hi = lines.start(t.map[0]), lines.start(t.map[1])
        if t.type == "heading_open":
            assert inline is not None
            rank = int(t.tag[1:])
            span: tuple[int, int] | None
            if t.markup in ("=", "-"):  # setext
                sx = _setext_block(lines, t.map[0], inline)
                span = (sx[0], sx[1]) if sx is not None else None
                title = _first_line_title(sx[2], env) if sx is not None else None
            else:
                span = _atx_block(lines, t.map[0], inline.content)
                title = _title(inline.children)
            if title is None or span is None:
                title = None  # an empty heading opens an untitled section
            else:
                blocks.append(Block(start=span[0], end=span[1], kind="heading"))
            heads.append((lo, rank, title, t.map[0]))
            continue
        if skip_caption_for == j:
            continue
        if t.type == "paragraph_open":
            assert inline is not None
            image = _sole_image(inline)
            if image is None:
                blocks.append(Block(start=lo, end=hi, kind="prose"))
                continue
            alt_label = _figure_label(_plain(image.children))
            prefix = next((p for p in ("![", "[![")
                           if inline.content.startswith(p + image.content + "]")), None)
            if alt_label is not None and prefix is not None:
                # The alt text is the caption: one span cannot be two kinds
                # (blocks do not overlap), so the alt is the figure_caption and
                # the rest of the paragraph AFTER it (`](target)`, or
                # `](target)](link)` for a linked image) the figure. The
                # opening `![` / `[![` before the alt is in no block (blocks
                # need not cover the text). An alt caption wins over a label
                # in the following paragraph, which stays prose.
                off, text = lines.text(t.map[0])
                a = off + (len(text) - len(text.lstrip())) + len(prefix)
                a_end = _content_offset(lines, a, len(image.content))
                blocks.append(Block(start=a, end=a_end, kind="figure_caption", label=alt_label))
                blocks.append(Block(start=a_end, end=hi, kind="figure"))
                continue
            blocks.append(Block(start=lo, end=hi, kind="figure"))
            if j + 1 < len(top):
                _ni, nt, ninline = top[j + 1]
                if nt.type == "paragraph_open" and ninline is not None and nt.map is not None:
                    label = _figure_label(_plain(ninline.children))
                    if label is not None:
                        blocks.append(Block(start=lines.start(nt.map[0]),
                                            end=lines.start(nt.map[1]),
                                            kind="figure_caption", label=label))
                        skip_caption_for = j + 1
            continue
        kind = _CONTAINER_KINDS.get(t.type) or _LEAF_KINDS.get(t.type) or "prose"
        blocks.append(Block(start=lo, end=hi, kind=kind))

    sections = _sections(content, heads)
    return DocumentStructure(
        content_sha256=content_sha256(content),
        sections=sections,
        blocks=blocks,
        structure_source="parsed",
        adapter=ADAPTER,
    )


def _content_offset(lines: _Lines, start: int, length: int) -> int:
    """``start + length`` in ``content`` for a span that starts at content
    offset ``start`` and is ``length`` characters of PARSER text long — the
    two differ only by line terminators the span crosses (``\\r\\n`` is one
    character to the parser)."""
    k = bisect.bisect_right(lines.starts, start) - 1
    col = start - lines.starts[k]
    remaining = length
    while True:
        _off, text = lines.text(k)
        avail = len(text) - col
        if remaining <= avail:
            return lines.starts[k] + col + remaining
        remaining -= avail + 1  # the parser's "\n"
        k += 1
        col = 0
        if k >= len(lines.starts):  # pragma: no cover - the parse said it is there
            raise ValueError("span runs past the end of the text")


def _sections(content: str, heads: list[tuple[int, int, str | None, int]]) -> list[Section]:
    n = len(content)
    raw: list[dict[str, Any]] = []
    first = heads[0][0] if heads else n
    # CommonMark blank = spaces and tabs (plus terminators): a form feed or a
    # Unicode space before the first heading is TEXT the parser puts in a
    # paragraph, so it needs its untitled section (str.strip() would drop it).
    if not heads or content[:first].lstrip(_BOM).strip(" \t\r\n"):
        raw.append({"start": 0, "end": first, "rank": None, "title": None,
                    "depth": 1, "path": [], "line": 0})
    stack: list[dict[str, Any]] = []
    for k, (start, rank, title, line) in enumerate(heads):
        if k == 0 and not raw:
            start = 0  # nothing but whitespace before it: no whitespace-only section
        while stack and stack[-1]["rank"] >= rank:
            stack.pop()["end"] = start
        parent_path = stack[-1]["path"] if stack else []
        node = {"start": start, "end": n, "rank": rank, "title": title,
                "depth": len(stack) + 1,
                "path": [*parent_path, *([title] if title is not None else [])],
                "line": line}
        raw.append(node)
        stack.append(node)
    for node in stack:
        node["end"] = n
    return [
        Section(start=s["start"], end=s["end"], level=s["depth"], title=s["title"],
                path=s["path"], roles=None, role_source=None, rank=s["rank"],
                locator=f"L{s['line'] + 1}")
        for s in raw
    ]


# --------------------------------------------------------------------------- #
# Stripped mode: text_render="markdown-plain/1"
# --------------------------------------------------------------------------- #

#: The render's identity — the ``text_render`` marker value a collection
#: records (``chunker_config.TEXT_RENDER_MARKDOWN_PLAIN``). Any change to the
#: text this render produces for the same source is a bump: it is collection
#: identity (content, offsets and chunk ids all follow from it).
TEXT_RENDER = "markdown-plain/1"

#: Escapes kept WITH their backslash: ``\\(…\\)`` and ``\\[…\\]`` are maths
#: delimiters, and maths is verbatim, never rendered (#828 §2). Every other
#: escape is markup (``\\*`` → ``*``).
_KEPT_ESCAPES = frozenset("()[]")

#: HTML elements that sit inside a line of text: no gap is opened at their
#: tags, so ``<b>x</b>y`` stays ``xy`` as a browser shows it. Any other tag
#: opens a gap (one space), so the words of adjacent blocks never run together.
_INLINE_HTML = frozenset({
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "del", "dfn", "em", "i",
    "ins", "kbd", "mark", "q", "s", "samp", "small", "span", "strong", "sub", "sup",
    "time", "u", "var",
})


class _VisibleHTML(HTMLParser):
    """The visible text of HTML (#828 §2: "inline HTML: visible text and
    ``href`` targets kept, markup dropped"): character data kept; tags,
    comments and declarations dropped; an anchor's ``href`` appended after its
    text as `` (href)``; ``<img>`` as ``alt (src)``; ``<br>`` as a line break.
    ``tail`` returns the last character emitted before this parser's output,
    for the gap rule across separately fed inline tags."""

    def __init__(self, tail: Callable[[], str] = lambda: "") -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._hrefs: list[str | None] = []
        self._tail = tail

    def _gap(self) -> None:
        last = self._out[-1][-1:] if self._out and self._out[-1] else self._tail()
        if last and not last.isspace():
            self._out.append(" ")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "img":
            alt, src = (a.get("alt") or "").strip(), (a.get("src") or "").strip()
            self._out.append(f"{alt} ({src})" if alt and src else alt or src)
        elif tag == "a":
            self._hrefs.append(a.get("href"))
        elif tag == "br":
            self._out.append("\n")
        elif tag not in _INLINE_HTML:
            self._gap()

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            href = self._hrefs.pop() if self._hrefs else None
            if href:
                self._out.append(f" ({href})")
        elif tag not in _INLINE_HTML and tag not in ("img", "br"):
            self._gap()

    def handle_data(self, data: str) -> None:
        self._out.append(data)

    def take(self) -> str:
        """What was emitted since the last call."""
        out, self._out = "".join(self._out), []
        return out

    def finish(self) -> str:
        """Close the parser; anchors never closed still keep their ``href``."""
        self.close()
        while self._hrefs:
            href = self._hrefs.pop()
            if href:
                self._out.append(f" ({href})")
        return self.take()


def _html_visible(fragment: str) -> str:
    p = _VisibleHTML()
    p.feed(fragment)
    return p.finish().strip()


def _render_inline(children: Sequence[Token] | None, *, one_line: bool) -> str:
    """The stripped text of inline ``children`` (#828 §2): emphasis markers
    dropped; code verbatim (its content, without the backticks); maths
    verbatim WITH its delimiters; a link as ``text (url)`` (once, when the
    text is the url — an autolink); an image as ``alt (src)``; inline HTML as
    its visible text plus ``href`` targets; escapes resolved except the maths
    delimiters (:data:`_KEPT_ESCAPES`). ``one_line`` turns line breaks into
    spaces (a heading, a table cell)."""
    out: list[str] = []
    links: list[tuple[str, int]] = []
    html = _VisibleHTML(lambda: out[-1][-1:] if out and out[-1] else "")
    brk = " " if one_line else "\n"
    for t in children or ():
        kind = t.type
        if kind == "text":
            out.append(t.content)
        elif kind == "text_special":
            kept = t.info == "escape" and t.content in _KEPT_ESCAPES
            out.append(t.markup if kept else t.content)
        elif kind in _BREAKS:
            out.append(brk)
        elif kind == "code_inline":
            out.append(t.content)
        elif kind in ("math_inline", "math_inline_double"):
            out.append(t.markup + t.content + t.markup)
        elif kind == "image":
            alt = _render_inline(t.children, one_line=True).strip()
            src = str(t.attrs.get("src") or "")
            out.append(f"{alt} ({src})" if alt and src else alt or src)
        elif kind == "link_open":
            links.append((str(t.attrs.get("href") or ""), len(out)))
        elif kind == "link_close" and links:
            href, at = links.pop()
            text = "".join(out[at:]).strip()
            if href and href not in (text, f"mailto:{text}"):
                out.append(f" ({href})")
        elif kind == "html_inline":
            html.feed(t.content)
            out.append(html.take())
        # emphasis / strikethrough open and close, and any other token: markup
    out.append(html.finish())
    text = "".join(out)
    return text.replace("\n", " ") if one_line else text


def _render_blocks(tokens: Sequence[Token]) -> list[str]:
    """The stripped units of a run of block tokens, in order: one per
    paragraph, nested heading, code block, maths block, HTML block, front
    matter and TABLE ROW (cells joined by `` | ``). List and quote markers are
    dropped; their contents are units like any other. Blank units are left
    out."""
    units: list[str] = []
    row: list[str] | None = None
    i, n = 0, len(tokens)
    while i < n:
        t = tokens[i]
        kind = t.type
        has_inline = i + 1 < n and tokens[i + 1].type == "inline"
        if kind in ("paragraph_open", "heading_open") and has_inline:
            units.append(_render_inline(tokens[i + 1].children,
                                        one_line=kind == "heading_open"))
            i += 2
            continue
        if kind in ("th_open", "td_open") and has_inline:
            if row is not None:
                row.append(_render_inline(tokens[i + 1].children, one_line=True).strip())
            i += 2
            continue
        if kind == "tr_open":
            row = []
        elif kind == "tr_close" and row is not None:
            units.append(" | ".join(row))
            row = None
        elif kind in ("fence", "code_block"):
            units.append(t.content.rstrip("\n"))
        elif kind == "math_block":
            units.append(t.markup + t.content + t.markup)
        elif kind == "math_block_label":
            units.append(f"{t.markup}{t.content}{t.markup} ({t.info})")
        elif kind == "html_block":
            units.append(_html_visible(t.content))
        elif kind == "front_matter":
            units.append(t.content.rstrip("\n"))
        i += 1
    return [u for u in units if u.strip()]


def _top_level_items(tokens: list[Token]) -> list[tuple[int, int]]:
    """``(first, end)`` token-index ranges of the level-0 blocks: an opening
    token through its matching close, or a single self-contained token."""
    items: list[tuple[int, int]] = []
    i, n = 0, len(tokens)
    while i < n:
        t = tokens[i]
        if t.level != 0 or t.nesting == -1:
            i += 1
            continue
        if t.nesting == 0:
            items.append((i, i + 1))
            i += 1
            continue
        j = i + 1
        while j < n and not (tokens[j].level == 0 and tokens[j].nesting == -1):
            j += 1
        items.append((i, j + 1))
        i = j + 1
    return items


def markdown_plain(doc: Document) -> tuple[str, DocumentStructure | None]:
    """The stripped render, ``text_render="markdown-plain/1"`` (#828 §2,
    §9.1): ``(content, structure)`` — the plain text that REPLACES the
    document's content, and its ``markdown/1`` structure, both from ONE parse.

    The text is built by an emitter that appends strings; every section and
    block offset is the emitter position its text landed at (§2: offsets are
    born in the stripped text, never searched for). Rules:

    * **headings** — the title on its own line, ``#`` and underline dropped;
      the title is the heading's whole rendered text on one line (links as
      ``text (url)``), and its ``heading`` block covers exactly that text, so
      ``content[block] == title``. Top-level headings open sections (tree
      depth = ``level``, the raw rank in ``Section.rank``), as in raw mode; a
      heading inside a quote or list is a line of that block. An empty heading
      opens an untitled section with no heading block and emits nothing; such
      a section that ends up holding no text is left out (no empty section).
    * **links** ``text (url)``; **images** ``alt (src)``; **inline HTML** its
      visible text plus ``href`` targets; **code** and **maths** verbatim
      (maths keeps ``$``/``$$``/``\\(…\\)``); **tables** one row per line,
      cells joined by `` | ``; **emphasis** markers dropped; list and quote
      markers dropped; thematic breaks emit nothing; front matter is its YAML
      body (a ``front_matter`` block).
    * Top-level blocks are separated by a blank line; lines inside a block by
      one newline. The BOM, line endings and trailing whitespace of the source
      are not content; the parser's NUL replacement (U+FFFD) is.
    * Block kinds and the image-caption rule are raw mode's, at emitter
      positions: an image-only paragraph is a ``figure``; a Figure label at the
      start of its alt text makes the alt text the ``figure_caption`` and the
      rest the ``figure``, else a following paragraph that starts with one is
      the caption.

    ``("", None)`` when nothing renders. Raises :class:`MarkdownBackendMissing`
    when the extra is absent."""
    from ragstack.ingestion.structure import content_sha256

    raw = doc.content
    shift = 1 if raw.startswith(_BOM) else 0
    src = _PARSER_NEWLINES.sub("\n", raw[shift:])
    tokens = _plain_parser().parse(src, {})

    parts: list[str] = []
    pos = 0

    def emit(text: str) -> tuple[int, int]:
        nonlocal pos
        start = pos
        parts.append(text)
        pos += len(text)
        return start, pos

    def separate() -> None:
        if pos:
            emit("\n\n")

    blocks: list[Block] = []
    heads: list[tuple[int, int, str | None, int]] = []
    items = _top_level_items(tokens)
    skip: int | None = None  # an item already consumed as a figure caption
    for j, (lo, hi) in enumerate(items):
        if j == skip:
            continue
        t = tokens[lo]
        assert t.map is not None
        if t.type == "heading_open":
            title = _render_inline(tokens[lo + 1].children, one_line=True).strip() or None
            if title is None:
                # Emits nothing; its section starts where the next text will
                # (after the separator the next block emits), so it is never
                # only a separator. Clamped to the end below.
                heads.append((pos + 2 if pos else 0, int(t.tag[1:]), None, t.map[0]))
                continue
            separate()
            s, e = emit(title)
            blocks.append(Block(start=s, end=e, kind="heading"))
            heads.append((s, int(t.tag[1:]), title, t.map[0]))
            continue
        units = _render_blocks(tokens[lo:hi])
        if not units:
            continue
        text = "\n".join(units)
        separate()
        s, e = emit(text)
        if t.type == "paragraph_open":
            image = _sole_image(tokens[lo + 1])
            if image is None:
                blocks.append(Block(start=s, end=e, kind="prose"))
                continue
            alt = _render_inline(image.children, one_line=True).strip()
            alt_label = _figure_label(alt) if alt else None
            if alt_label is not None and text.startswith(alt):
                blocks.append(Block(start=s, end=s + len(alt), kind="figure_caption",
                                    label=alt_label))
                if s + len(alt) < e:
                    blocks.append(Block(start=s + len(alt), end=e, kind="figure"))
                continue
            blocks.append(Block(start=s, end=e, kind="figure"))
            if j + 1 < len(items):
                nlo, nhi = items[j + 1]
                if tokens[nlo].type == "paragraph_open":
                    nunits = _render_blocks(tokens[nlo:nhi])
                    ntext = "\n".join(nunits)
                    label = _figure_label(ntext) if ntext else None
                    if label is not None:
                        separate()
                        ns, ne = emit(ntext)
                        blocks.append(Block(start=ns, end=ne, kind="figure_caption",
                                            label=label))
                        skip = j + 1
            continue
        kind = _CONTAINER_KINDS.get(t.type) or _LEAF_KINDS.get(t.type) or "prose"
        blocks.append(Block(start=s, end=e, kind=kind))

    content = "".join(parts)
    if not content:
        return "", None
    n = len(content)
    heads = [(min(start, n), rank, title, line) for start, rank, title, line in heads]
    # An empty heading that ended up holding no text has a zero-length
    # section (it can have no children: they would be inside it); leave it out.
    sections = [s for s in _sections(content, heads) if s.start < s.end]
    return content, DocumentStructure(
        content_sha256=content_sha256(content),
        sections=sections,
        blocks=blocks,
        structure_source="parsed",
        adapter=ADAPTER,
    )


__all__ = ["ADAPTER", "EXTRA", "TEXT_RENDER", "MarkdownBackendMissing", "backend",
           "markdown_plain", "markdown_structure", "require", "reset_parser"]
