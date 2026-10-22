"""Per-document symbol glossary (#754 fix A).

The failure this module exists to fix is one of **locality**. A scholarly paper
defines a symbol once ("where Qb is the breathing flow rate") and then uses it
for pages — and the passage that holds a *value* ("Qb = 0.5 m3/h", in a table
caption forty thousand characters later) never repeats the words. A chunk-level
BM25 index then cannot match "breathing rate" against the caption that holds the
number, however the query is phrased.

The fix is to carry the definition to the chunk: build one ``symbol ->
definition`` table per document, and give every chunk that *contains* a symbol
the symbol's definition as extra searchable text in its own field
(``Chunk.symbol_glossary`` → the Elasticsearch ``symbol_glossary`` text field).
Nothing about ``content`` changes, so what a query displays, what the reranker
scores, and what is embedded are all byte-identical.

**The one rule, stated once: definitions are copied from the document being
read, never from a lexicon.** In Bazant & Bush (PNAS 2021) ``λv`` is the *viral
deactivation rate*, not the ventilation rate (that is ``λa``) — an extractor that
resolved symbols from prior knowledge would inject a confidently wrong word into
a searchable field. So:

* the rules below only ever copy a span of the document's own text;
* every distinct definition of a symbol is kept **with its position**, and a
  chunk gets the definition nearest before it (symbols are reused within a
  paper; position is the strongest disambiguation cue — ScholarPhi; Asakura &
  Miyao 2024). A symbol defined more than once is flagged
  (:attr:`GlossaryEntry.ambiguous`);
* the optional LLM glossary (:mod:`ragstack.ingestion.symbol_glossary_llm`,
  produced OFFLINE by ``scripts/symbol_glossary_llm.py`` and read here from a
  content-addressed cache) passes through :func:`validate_entries`: a symbol is
  kept only if it occurs in the document, a definition only if every one of its
  words occurs in the document.

**Symbols are compared in canonical form** (:func:`canonical_text`), at
extraction AND at chunk-matching time, through the one function. PDF text spells
one symbol several ways — ``r¯`` (spacing macron) vs ``r̄`` (combining), ``µ``
(micro sign) vs ``μ``, ``λ_v`` / ``λ_{v}`` / ``\\lambda_v`` vs ``λv`` — and a
raw-string match would silently fail to attach, or (after an analyzer strips
the bar) attach ``r¯``'s definition to every ``r``.

Pure and deterministic — no I/O except the cache file, no network, no model — so
it is cheap to unit-test, safe in the ingest worker, and version-stampable
(``EXTRACTOR_VERSION``; ADR-0011 wants a step's version in its provenance).
"""
from __future__ import annotations

import bisect
import hashlib
import json
import logging
import re
import threading
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ragstack.models import Chunk, Document

log = logging.getLogger(__name__)

#: The rules extractor's version. Bump on ANY change to what the rules extract,
#: to canonicalisation, or to how a chunk's field is rendered: it is stamped on
#: every chunk (``metadata.symbol_glossary_version``) and in the collection
#: manifest, and the backfill uses it to decide which chunks are already current.
EXTRACTOR_VERSION = "rules/3"

#: The per-chunk metadata key carrying the version of the glossary that produced
#: the chunk's ``symbol_glossary`` field (``rules/1``, ``llm/1:<model>:<hash>``).
#: Declared in ``metadata_schema`` (keyword) for row-level audit and backfill
#: resumability.
VERSION_KEY = "symbol_glossary_version"

# --- canonical form -----------------------------------------------------------
_COMBINING_MACRON = "̄"
# Bar spellings PDF/JATS extraction produces: the spacing macron (U+00AF, what
# openchia's PDF copies carry: "r¯"), the modifier letter macron (U+02C9) and the
# combining overline (U+0305). All become the combining macron (U+0304).
_BAR_VARIANTS = "¯ˉ̅"
_BARS = _BAR_VARIANTS + _COMBINING_MACRON
_GREEK_NAMES = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "θ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ",
    "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
_LATEX_GREEK = re.compile(r"\\(" + "|".join(sorted(_GREEK_NAMES, key=len, reverse=True)) + r")(?![A-Za-z])")
# A subscript spelled with an underscore: "λ_v", "Q_{b}", "v̄_s" → "λv", "Qb", "v̄s".
_UNDERSCORE_SUB = re.compile(r"(?<=[^\W\d_]|[" + _BARS + r"])_\{?([^\W_]{1,4})\}?")
# Greek variant letters NFKD keeps distinct but authors use interchangeably.
_GREEK_VARIANTS = str.maketrans({"ϕ": "φ", "ϵ": "ε", "ϑ": "θ", "ϱ": "ρ", "ς": "σ", "ϰ": "κ"})


def _sub_with_map(
    pattern: re.Pattern[str], repl: Callable[[re.Match[str]], str], text: str, pos: list[int]
) -> tuple[str, list[int]]:
    """``pattern.sub`` that also carries the raw-offset map ``pos`` along: every
    replacement character maps to the raw offset of the match's start."""
    out: list[str] = []
    out_pos: list[int] = []
    last = 0
    for m in pattern.finditer(text):
        out.append(text[last : m.start()])
        out_pos.extend(pos[last : m.start()])
        rep = repl(m)
        out.append(rep)
        out_pos.extend([pos[m.start()]] * len(rep))
        last = m.end()
    if not out:
        return text, pos
    out.append(text[last:])
    out_pos.extend(pos[last:])  # includes the trailing end-offset sentinel
    return "".join(out), out_pos


def canonical_map(text: str) -> tuple[str, list[int]]:
    """``(canonical, pos)``: the canonical form of ``text`` and, for each of its
    characters, the offset in ``text`` it came from (``pos`` has one extra
    trailing entry, ``len(text)``, so an end offset maps too).

    Canonical = every bar spelling → the combining macron; NFKD (compatibility
    decomposition: ``µ``→``μ``, math-italic ``𝐷``→``D``, ``ﬁ``→``fi``,
    subscript ``ᵥ``→``v``, and precomposed ``ā``→``a``+macron so a bar is always
    a separate mark); Greek variant letters unified (``ϕ``→``φ``); LaTeX Greek
    names (``\\lambda``→``λ``) and underscore subscripts (``λ_v``,
    ``Q_{b}``→``λv``, ``Qb``) folded. NFKD rather than NFKC so that a barred
    letter has ONE spelling whether or not a precomposed form exists.

    Whitespace is preserved: ``λ v`` in running text stays two tokens (joining
    it would glue every "a b" in prose). :func:`canonical_symbol` folds the
    whitespace of a symbol KEY, e.g. an LLM's ``"λ v"``."""
    if text.isascii():
        canon, pos = text, list(range(len(text) + 1))
    else:
        parts: list[str] = []
        pos = []
        for i, ch in enumerate(text):
            if ch.isascii():
                parts.append(ch)
                pos.append(i)
                continue
            if ch in _BAR_VARIANTS:
                ch = _COMBINING_MACRON
            dec = unicodedata.normalize("NFKD", ch).translate(_GREEK_VARIANTS)
            parts.append(dec)
            pos.extend([i] * len(dec))
        canon = "".join(parts)
        pos.append(len(text))
    if "\\" in canon:
        canon, pos = _sub_with_map(_LATEX_GREEK, lambda m: _GREEK_NAMES[m.group(1)], canon, pos)
    if "_" in canon:
        canon, pos = _sub_with_map(_UNDERSCORE_SUB, lambda m: m.group(1), canon, pos)
    return canon, pos


def canonical_text(text: str) -> str:
    """The canonical form of ``text`` (see :func:`canonical_map`)."""
    return canonical_map(text)[0]


# A word broken at a line end with a hyphen ("breath-\ning"), or any whitespace
# run that contains a newline. Anchored at a word start so a long letter run is
# tried once, not from every character (quadratic otherwise).
_LINE_BREAK = re.compile(
    r"(?<![^\W\d_])(?P<left>[^\W\d_]+)-(?P<hws>[^\S\n]*\n[^\S\n]*)(?P<right>[^\W\d_]+)"
    # A hyphen between letters/digits where alternative 1 does not apply
    # (e.g. a digit on either side: "COVID-\n19", "SARS-CoV-\n2",
    # "2-\ndimensional"): never joined, the break is dropped and the hyphen kept.
    r"|(?<=[^\W_])-(?P<dws>[^\S\n]*\n[^\S\n]*)(?=[^\W_])"
    r"|(?P<ws>\s*\n\s*)"
)


def _fold_line_breaks(text: str, pos: list[int]) -> tuple[str, list[int]]:
    """Undo the line wrapping of PDF text, carrying the raw-offset map ``pos``
    along EXACTLY (every kept character keeps its own raw offset; a folded
    whitespace run becomes one space at the run's first offset).

    * A whitespace run with ONE newline (a wrapped line) becomes a single space.
      A run with two or more (``\\n\\s*\\n``, a paragraph break) is kept as is,
      so it still ends a definition (``_DEF_END``, ``_DEF_SPAN``).
    * A word hyphenated across a single line break, ``left-\\nright``:

      - joined (``breath-\\ning`` → ``breathing``) only when the hyphen sits
        between two LOWERCASE letters, each side is at least two letters (no
        typesetter breaks off one letter; ``a-\\nb`` is more likely algebra),
        AND the joined word occurs elsewhere in
        the same text unbroken. The document is the dictionary: the rules copy
        the document's words, so a de-hyphenation must produce a word the
        document itself spells;
      - otherwise the hyphen is KEPT and only the break is dropped
        (``well-\\nknown`` → ``well-known``, ``SARS-\\nCoV`` → ``SARS-CoV``,
        ``air-\\nborne`` → ``air-borne`` unless the text also says
        ``airborne``; between letters or digits where the letter rule does
        not apply, e.g. a digit on either side, ``COVID-\\n19`` →
        ``COVID-19``). A hyphen after a space (``rate -\\nflow``) is a dash,
        and folds like any break (``rate - flow``). A real compound split at
        its hyphen cannot be told from a soft hyphen without a dictionary;
        keeping the hyphen never invents a word, it only leaves a soft hyphen
        visible.
    """
    if "\n" not in text:
        return text, pos
    words: set[str] | None = None
    out: list[str] = []
    out_pos: list[int] = []
    last = 0
    for m in _LINE_BREAK.finditer(text):
        if m.group("ws") is not None:
            ws = m.group("ws")
            if ws.count("\n") != 1:
                continue  # a paragraph break: kept verbatim
            out.append(text[last : m.start()])
            out_pos.extend(pos[last : m.start()])
            out.append(" ")
            out_pos.append(pos[m.start()])
            last = m.end()
            continue
        if m.group("dws") is not None:
            out.append(text[last : m.start("dws")])  # up to and including the hyphen
            out_pos.extend(pos[last : m.start("dws")])
            last = m.end()
            continue
        left, right = m.group("left"), m.group("right")
        join = False
        if len(left) >= 2 and len(right) >= 2 and left[-1].islower() and right[0].islower():
            if words is None:
                words = {w.casefold() for w in _WORDS.findall(text)}
            join = (left + right).casefold() in words
        out.append(text[last : m.end("left")])
        out_pos.extend(pos[last : m.end("left")])
        if not join:
            out.append("-")
            out_pos.append(pos[m.start("hws") - 1])
        out.append(right)
        out_pos.extend(pos[m.start("right") : m.end("right")])
        last = m.end()
    if not out:
        return text, pos
    out.append(text[last:])
    out_pos.extend(pos[last:])  # includes the trailing end-offset sentinel
    return "".join(out), out_pos


def definition_map(text: str) -> tuple[str, list[int]]:
    """``(text, pos)`` the rules match DEFINITIONS on: :func:`canonical_map`,
    then :func:`_fold_line_breaks` (#790). PDF text keeps the PDF's line breaks,
    and a definition wrapped across one ("where Qb is the breathing\\nflow
    rate") was cut at the newline. ``pos`` stays exact, so a definition's raw
    offset — what positional attachment compares with ``Chunk.start_char`` — is
    unchanged by the folding.

    Only extraction uses this. Symbol and value matching on chunks
    (:meth:`Glossary.for_chunk`) needs no folding (their patterns allow any
    whitespace, newlines included, between symbol, ``=`` and number), and the
    LLM validation gates (:func:`validate_entries`) keep :func:`canonical_map`
    unchanged."""
    canon, pos = canonical_map(text)
    return _fold_line_breaks(canon, pos)


@lru_cache(maxsize=65536)
def canonical_symbol(symbol: str) -> str:
    """The canonical KEY of a symbol: :func:`canonical_text` with whitespace
    removed (``"λ v"``, ``"λ_v"``, ``"\\lambda_v"`` and ``"λv"`` are one key)."""
    return "".join(canonical_text(symbol.strip()).split())


def display_form(text: str) -> str:
    """NFC for rendering: the indexed field must spell words the way ``content``
    does (composed ``é``), or the analyzer sees different terms."""
    return unicodedata.normalize("NFC", text)


# --- what a "symbol" is ------------------------------------------------------
_BAR_CLASS = f"[{_BARS}]"
# A symbol token: a letter, then up to five letters/digits, any of which may be
# followed by a bar. Deliberately short — a symbol is "Qb", "λv", "r̄", "v̄s",
# "Nmax"; a six-plus letter run is a word. Applied to CANONICAL text.
_SYMBOL_BODY = rf"[^\W\d_]{_BAR_CLASS}?(?:[^\W_]{_BAR_CLASS}?){{0,5}}"
_NOT_TOKEN_BEFORE = rf"(?<![\w{_BARS}])"
_NOT_TOKEN_AFTER = rf"(?![\w{_BARS}])"
_SYMBOL_TOKEN = re.compile(_SYMBOL_BODY)
_SYMBOL = _NOT_TOKEN_BEFORE + rf"(?P<sym>{_SYMBOL_BODY})" + _NOT_TOKEN_AFTER

# Short English words that a capitalised/lowercase "symbol" test would otherwise
# admit ("In", "we", "The"). A symbol is only ever considered at a definition
# pattern's symbol slot, so this guards the slot, not running text.
_SHORT_WORDS = frozenset(
    """am an as at be by do go he if in is it me my no of oh on or ok so to up us
    we ie eg al id mr ms dr st nd rd th ad pp
    the and for its our has was not but can may all any are how who why one two
    per via see use new low few own off out set yet nor end had did let lot
    fig eq eqs ref""".split()
)
# Units are not symbols: "where Vb≈500 mL is …" must not define "mL", and a
# "m3" glossary entry would attach to every chunk that states a volume. Compared
# in canonical form (so "µm" — micro sign — is caught as "μm").
_UNITS = frozenset(
    canonical_text(u)
    for u in """m cm mm um µm μm nm km m2 m3 m² m³ cm2 cm3 cm² cm³ mm2 mm3 ml mL L dL
    s ms min h hr hrs d yr g kg mg µg μg ng pg Pa kPa MPa hPa Hz kHz J kJ W kW
    K mol mmol M mM nM ppm ppb lpm cfm""".split()
)


def _is_greek(ch: str) -> bool:
    return "Ͱ" <= ch <= "Ͽ" or "ἀ" <= ch <= "῿"


def is_specific_symbol(token: str, *, allow_single_letter: bool = False) -> bool:
    """Is ``token`` (any spelling; canonicalised here) specific enough to be a
    symbol worth a glossary entry?

    The specificity guard the plan calls for: in a symbol-dense paper a glossary
    keyed on ``V`` / ``A`` / ``r`` attaches to every chunk and becomes noise.
    Accepted:

    * anything with a Greek letter (``λv``, ``φs``) or a bar (``r̄``, ``v̄s``);
    * a letter followed by digits or a subscript-like tail, two to five
      characters: ``Qb``, ``Cq``, ``r0``, ``fd`` — but not a capitalised word
      (``The``), an acronym (``RH``, ``CET`` — those are abbreviations, not
      symbols, and BM25 already matches them as words), a short English word
      (``In``, ``is``) or a unit (``mL``, ``m3``, ``μm``);
    * a single Latin letter only when ``allow_single_letter``.
    """
    token = canonical_symbol(token) if token else ""
    if not token or not _SYMBOL_TOKEN.fullmatch(token):
        return False
    if token in _UNITS:
        return False
    if any(_is_greek(c) for c in token) or any(c in _BARS for c in token):
        return True
    if len(token) == 1:
        return allow_single_letter
    if len(token) > 5:
        return False
    if token.isupper():
        return False  # an acronym
    if token.lower() in _SHORT_WORDS:
        return False
    head, tail = token[0], token[1:]
    if any(c.isdigit() for c in tail):
        return True  # r0, V1, N95
    if head.isupper() and tail.islower():
        # "Qb", "Cq" yes; "The", "Rate" no — a capitalised run of three or more
        # lowercase letters is a word at the start of a sentence.
        return len(tail) <= 2 and not _is_vowel_word(token)
    if head.islower() and tail.islower():
        return len(token) == 2  # "fd", "pm", "cv"; "rate" is a word
    # Mixed internal case: "Nmax" no (capital + word), "pH" yes.
    return len(token) <= 3


def _is_vowel_word(token: str) -> bool:
    """A three-letter capitalised token with a vowel in the middle reads as a
    word ("Has", "Was", "Not"), one without ("Qbn") as a symbol."""
    return len(token) == 3 and token[1].lower() in "aeiouy"


# --- definition patterns (all applied to canonical text) ----------------------
# Head nouns that end a definitional noun phrase ("the effective infectious drop
# radius r̄"). A closed list on purpose: the appositive rule is the least certain
# and the head noun is what makes "the X r̄" a definition rather than any noun
# phrase that happens to precede a letter.
_HEAD_NOUNS = (
    "rate|rates|radius|radii|diameter|coefficient|coefficients|factor|factors|"
    "concentration|concentrations|volume|area|height|length|width|depth|distance|"
    "time|times|velocity|speed|density|fraction|number|constant|flux|flow|"
    "temperature|pressure|probability|parameter|ratio|efficiency|size|mass|"
    "frequency|exponent|scale|count|amplitude|viscosity|diffusivity|dose|"
    "exposure|humidity|penetration|transmissibility|occupancy|emission|"
    "threshold|period|duration|capacity|fluence|intensity|power|energy|"
    "potential|charge|weight|yield|level|index|variance|mean|average"
)
_NP_WORD = r"[^\W\d_][\w\-]*"
# Optional function arguments after a symbol: "λv(r)", "Cj(x)".
_ARGS = r"(?:\s*\([^()\n]{1,20}\))?"
# Optional "= value [unit]" after a symbol: "where Vb≈500 mL is the volume per
# breath". No parentheses inside, so "(Cq=30) is the norm" cannot match.
_VALUE = r"(?:\s*[=≈~]\s*[^\s.;()]{1,40}(?:\s+[^\W\d]{1,6}\d?)?)?"
# The start of the NEXT definition clause, which ends the current one: "Cp(t)
# is", "λ (h−1) is", "ρ=ρw/ρa is", "η represents". rules/3 (#790) admits the
# arguments, value and "represents" that the clause-opening rules admit; before,
# "…, and Cp(t) is the …" ran on into the next clause, and in PDF text only the
# line break that happened to fall there had stopped it. The second ``_ARGS``
# takes "μ=μ(T) is".
_NEXT_CLAUSE = _SYMBOL_BODY + _ARGS + _VALUE + _ARGS + r"\s+(?:is|are|denotes|represents)\b"
# What ends a "where X is DEF" definition: sentence end, a semicolon, a comma
# that starts the next "and Y is" clause, "where/while Y is", or a blank line.
# A SINGLE line break no longer ends it: extraction runs on line-folded text
# (:func:`definition_map`), and a paragraph break survives the folding.
# {1,…}, not {2,…}: with _DEF_END's (?<=\S) a span cannot end on a space, so
# "where Qb is the x , Cq is …" needs a one-character span to stop at "x"
# (rules/2 took "x " and then dropped it as implausible; so does this).
_DEF_SPAN = r"(?P<def>[^.;:\n]{1,200}?)"
# Linear on long whitespace runs: the leading whitespace is taken ATOMICALLY
# (``(?>\s*)``), "\s+and" becomes a look-behind for the space, and the
# paragraph break is ``[^\S\n]*\n[^\S\n]*\n`` (the same runs as
# ``\s*\n\s*\n``). The old ``\s*`` followed by ``\s+…`` / ``\n\s*\n`` split a
# space run every possible way: seconds at a few thousand spaces.
_DEF_END = (
    # (?<=\S): a lazy definition ends where this first holds, and whenever it
    # holds inside a whitespace run it also holds at the run's start, so a
    # definition never ends on a space. Checking that first makes each step
    # inside a long space run O(1) instead of a rescan of the run.
    r"(?<=\S)(?=[^\S\n]*\n[^\S\n]*\n|(?>\s*)(?:[.;:](?:\s|$)|,\s*(?:and\s+)?" + _NEXT_CLAUSE
    + r"|,\s*and\b|(?<=\s)(?:and|where|while)\s+" + _NEXT_CLAUSE + r"|$))"
)
_COPULA = (
    r"(?:is|are|denotes|denote|represents|represent|stands\s+for|is\s+defined\s+as"
    r"|is\s+given\s+by)"
)

# R1: "where Qb is the breathing flow rate (exhaled volume per time)", and its
# continuation clauses "…, and pm is the mask penetration factor".
_WHERE = re.compile(
    r"\bwhere\s+" + _SYMBOL + _ARGS + _VALUE + r"\s+" + _COPULA + r"\s+" + _DEF_SPAN + _DEF_END,
    re.IGNORECASE,
)
_CONTINUATION = re.compile(
    r"(?:,|;)\s*(?:and\s+)?" + _SYMBOL + r"\s+" + _COPULA + r"\s+" + _DEF_SPAN + _DEF_END,
    re.IGNORECASE,
)
# R2: appositive "the effective infectious drop radius r̄" / "the dilution
# factor, fd". Two to six words ending in a head noun, then the symbol.
_APPOSITIVE = re.compile(
    # Possessive whitespace (\s++, \s*+) wherever a letter or "," must follow:
    # the same matches, without backtracking through a long run of spaces.
    r"\bthe\s++(?P<def>(?:" + _NP_WORD + r"\s++){1,5}(?:" + _HEAD_NOUNS + r"))"
    # "(?:\s*,)?\s+", not "\s*,?\s+": the same matches, without the split
    # ambiguity that is quadratic on a long run of spaces.
    r"(?:\s*+,)?\s++" + _SYMBOL + _ARGS
    # Not when the symbol opens its OWN definition clause: in "where pm is the
    # mask penetration factor, pf is the filtration efficiency" the phrase
    # before "pf" belongs to pm (#754 review). With arguments or a unit in
    # parentheses (rules/3), only a DEFINING clause counts: "the initial
    # concentration, Cp(t) is the point concentration" is Cp's own clause (the
    # optional _ARGS used to backtrack past "(t)" and dodge the check), while
    # "the pedestrian density ρ(t) is a major determinant" and "the social
    # distance index Pd (%) is expressed as …" keep the appositive (measured on
    # dev openchia: the plain lookahead lost three correct definitions there).
    + r"(?!\s+(?:is|are|denotes?|represents?)\b"
    r"|\s*\([^()\n]{1,20}\)\s+(?:(?:is|are)\s+the|denotes?|represents?)\b)",
    re.IGNORECASE,
)
# R3: "λ̄c = λa+… is the air purification rate" / "Qb is the …" — a symbol, an
# optional "= expression", then "is the DEF".
_IS_THE = re.compile(
    _SYMBOL + _ARGS + _VALUE + r"\s+(?:is|denotes|represents)\s+the\s+" + _DEF_SPAN + _DEF_END,
)
# R4: parenthetical "breathing rate (Qb)". Anchored at a word start: unanchored,
# the leftmost-match engine retries the phrase from every character of a long
# token — quadratic on a 100k-char run.
_PAREN = re.compile(
    # Possessive whitespace: the same matches, linear on long space runs.
    r"(?<![\w\-])(?P<def>(?:" + _NP_WORD + r"\s++){0,5}(?:" + _HEAD_NOUNS + r"))\s*+\(\s*+"
    + _SYMBOL + r"\s*+\)",
    re.IGNORECASE,
)
# R5: coordination — "Qb, Cq and λv are the breathing rate, the quanta
# concentration and the deactivation rate, respectively". The known failure mode
# of single-symbol rules (SymDef 2023). Pairs only when the counts agree.
_SYM_IN_LIST = _NOT_TOKEN_BEFORE + _SYMBOL_BODY + _NOT_TOKEN_AFTER + _ARGS
_RESPECTIVELY = re.compile(
    # "\s*(?:,\s*)?and", not "\s*,?\s*and": the same matches, without the
    # split ambiguity that is quadratic on a long run of spaces.
    r"(?P<syms>" + _SYM_IN_LIST + r"(?:\s*,\s*" + _SYM_IN_LIST + r"){0,6}\s*(?:,\s*)?and\s+"
    + _SYM_IN_LIST + r")\s+(?:are|denote|represent|are\s+defined\s+as)\s+"
    # {1,…}: see _DEF_SPAN; fewer than five characters never pair two
    # plausible definitions, so the outcome matches rules/2's {5,…}.
    r"(?P<defs>[^.;:\n]{1,400}?)(?<=\S)\s*(?:,\s*)?respectively\b",
)
_LIST_SPLIT = re.compile(r"\s*,\s*(?:and\s+)?|\s+and\s+")

_TRAILING_EQUATION = re.compile(r"(?:,\s*|\s+)(?:and|or|with)\s+\S*[=≈<>≤≥]|\s*\S*[=≈]")
_LEADING_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_WORDS = re.compile(r"[^\W\d_]+")
# Function words that may not appear as appositive/parenthetical modifiers: "the
# same rate λv" is not a definition of λv.
_NP_STOP = frozenset(
    """a an the this that these those its their our his her same such each every
    any all some other another is are was were be been of in on at to for with by
    from and or but if as than then which who whose where when we it they""".split()
)
# A number immediately before a symbol slot: the token is a unit ("500 mL").
_AFTER_NUMBER = re.compile(r"\d\s*$")
# A relation right after an appositive's symbol makes it a comparison, not a
# naming ("the air exchange rate, λs(r)<λa"; "the adjustment time, λc−1").
_RELATION_AFTER = re.compile(r"\s*(?:[<>≤≥≪≫≲≳∝]|[−-]\s*1\b)")
_SENTENCE_END = re.compile(r"[.;](?:\s|$)|\n\s*\n")

def _clean_definition(raw: str, max_words: int) -> str:
    """Collapse whitespace, drop a leading article, cap at ``max_words``.

    Truncation keeps a PREFIX of the document's own words, so even a capped
    definition is still a span of the document."""
    text = " ".join(raw.split())
    # An equation trailing the phrase ("… filtration and λr=Qr/V") is the next
    # clause, not part of the definition: cut before the conjunction that opens it.
    eq = _TRAILING_EQUATION.search(text)
    if eq:
        text = text[: eq.start()]
    text = _LEADING_ARTICLE.sub("", text).strip(" ,")
    words = text.split(" ")
    if len(words) > max_words:
        text = " ".join(words[:max_words])
        # Do not leave an unbalanced "(" dangling off the cut.
        if text.count("(") > text.count(")"):
            text = text[: text.rfind("(")].rstrip()
    return text


def _plausible_definition(defn: str, symbol: str) -> bool:
    """A definition must say something in words: at least one alphabetic word of
    three or more letters, mostly words rather than numbers/operators, and not
    simply restate the symbol."""
    if not defn or defn == symbol:
        return False
    words = _WORDS.findall(defn)
    if not any(len(w) >= 3 for w in words):
        return False
    tokens = defn.split()
    alpha = sum(1 for t in tokens if _WORDS.search(t))
    if alpha < max(1, (2 * len(tokens)) // 3):
        return False
    if re.match(r"^[=<>≈~±+\-*/\d]", defn):
        return False  # "is 0.5 m3/h": a value, not a definition
    return True


@dataclass(frozen=True)
class GlossaryConfig:
    """Tunables for extraction and rendering. Every default is stated here so an
    operator tunes from a JSON string (:func:`config_from_json`), not from code."""

    #: Distinct definitions kept per symbol, in document order. More than one is
    #: the point (the λv trap; symbols reused within a paper); a cap keeps a
    #: symbol re-defined in every section from growing without bound.
    max_definitions_per_symbol: int = 5
    #: Words per definition. A definition is a noun phrase, not a paragraph.
    max_definition_words: int = 12
    #: Entries rendered into one chunk's field (sorted, so the cap is stable).
    max_entries_per_chunk: int = 12
    #: Characters in one chunk's rendered field.
    max_chars_per_chunk: int = 1200
    #: Admit single Latin letters (``V``, ``A``, ``r``) as symbols. Off: in a
    #: symbol-dense paper they attach to nearly every chunk.
    allow_single_letter: bool = False
    #: Render, for a symbol with several definitions, only the one nearest at or
    #: before the chunk's start (falling back to the first one after it). Off:
    #: render every definition.
    positional: bool = True
    #: Attach a symbol's definition to a chunk only where the chunk STATES A
    #: VALUE for it — the symbol followed by ``=``/``≈``/``~`` and a number
    #: (``Qb = 0.5 m3/h``, ``r̄ ≈ 1 μm``, ``λv(r) = 0.3/h``). The glossary exists
    #: to carry a definition to the chunk that holds the number; attaching it to
    #: every mention lifted whole symbol-dense papers into the top 10 (#754
    #: experiment: ~9.7 of 10 BM25 slots at boost 2). On by default.
    attach_only_with_value: bool = True
    #: LLM/cached definitions only: every content word of a definition must
    #: occur within this many characters of an occurrence of the symbol in the
    #: document (0 = off: words anywhere in the document suffice). The
    #: document-wide words-occur gate is not enough: on one openchia copy of
    #: Bazant & Bush the model proposed "λv — rate of ventilation", and all three
    #: words occur somewhere in that paper. They do not occur NEXT TO λv, where
    #: the paper says it is the deactivation rate. 200 rejects that and keeps
    #: every correct λv definition on the other copies (#754 experiment).
    definition_window: int = 200
    #: Rule toggles.
    rule_where: bool = True
    rule_appositive: bool = True
    rule_is_the: bool = True
    rule_parenthetical: bool = True
    rule_respectively: bool = True


DEFAULT_CONFIG = GlossaryConfig()


def config_from_json(raw: str) -> GlossaryConfig:
    """Build a :class:`GlossaryConfig` from a JSON object of overrides.

    Mirrors ``boilerplate.config_from_json``: unknown keys and malformed JSON
    degrade to the defaults with a warning, and every value is coerced to its
    declared type here rather than failing once per chunk deep in an ingest."""
    if not raw or not raw.strip():
        return DEFAULT_CONFIG
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("expected a JSON object")
    except Exception as e:
        log.warning("ignoring malformed symbol glossary config (%s); using defaults", e)
        return DEFAULT_CONFIG
    types = {f.name: f.type for f in fields(GlossaryConfig)}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in types:
            log.warning("ignoring unknown symbol glossary config key %r", key)
            continue
        try:
            if types[key] == "bool":
                if not isinstance(value, bool):
                    raise TypeError(f"expected a boolean, got {value!r}")
                kwargs[key] = value
            elif types[key] == "int":
                kwargs[key] = int(value)
            else:  # pragma: no cover - no other field types exist today
                kwargs[key] = value
        except (TypeError, ValueError) as e:
            log.warning("ignoring symbol glossary config key %r (%s)", key, e)
    return GlossaryConfig(**kwargs)


def config_dict(config: GlossaryConfig) -> dict[str, Any]:
    """The config as a plain dict — what provenance records."""
    return {f.name: getattr(config, f.name) for f in fields(GlossaryConfig)}


# --- the glossary -------------------------------------------------------------


@lru_cache(maxsize=4096)
def symbol_pattern(symbol: str) -> re.Pattern[str]:
    """A regex matching canonical ``symbol`` as a whole token of CANONICAL text
    (not inside a longer token; a bar is part of the token, so ``r`` does not
    match inside ``r̄``). Case-sensitive: ``Qb`` and ``qb`` differ."""
    return re.compile(_NOT_TOKEN_BEFORE + re.escape(symbol) + _NOT_TOKEN_AFTER)


@lru_cache(maxsize=4096)
def value_pattern(symbol: str) -> re.Pattern[str]:
    """Canonical ``symbol`` stating a value: the whole token, optional function
    arguments (``λv(r)``), then ``=``/``≈``/``≃``/``~``/``∼`` and a number
    (optionally signed; a decimal may start with ``.``). A bare equation
    (``λ̄c = λa + λf``) is NOT a value statement, and neither is a comparison
    (``λs(r) < λa``)."""
    return re.compile(
        _NOT_TOKEN_BEFORE + re.escape(symbol) + _NOT_TOKEN_AFTER
        + r"\s*(?:\([^()\n]{0,20}\))?\s*[=≈≃~∼]\s*[-−+]?\s*\.?\d"
    )


def states_value(symbol: str, text: str) -> bool:
    """Does ``text`` state a value for ``symbol`` (both canonicalised)?"""
    key = canonical_symbol(symbol) if symbol else ""
    return bool(key) and value_pattern(key).search(canonical_text(text)) is not None


def symbol_occurs(symbol: str, text: str) -> bool:
    """Does ``symbol`` (any spelling) occur in ``text`` (any spelling)? Both
    sides go through the same canonicalisation."""
    key = canonical_symbol(symbol) if symbol else ""
    return bool(key) and symbol_pattern(key).search(canonical_text(text)) is not None


@dataclass(frozen=True)
class Definition:
    #: The definition text, as the document spells it (whitespace collapsed).
    text: str
    #: Character offset in the document's RAW text where this definition was
    #: found — the same coordinates as ``Chunk.start_char``.
    offset: int


@dataclass(frozen=True)
class GlossaryEntry:
    #: The canonical symbol key (:func:`canonical_symbol`).
    symbol: str
    #: Distinct definitions in document order.
    definitions: tuple[Definition, ...]

    @property
    def ambiguous(self) -> bool:
        """Defined more than once, differently — the case where picking one
        global "best" definition is how the wrong one gets picked."""
        return len(self.definitions) > 1

    def definition_for(self, start_char: int | None) -> tuple[Definition, ...]:
        """The definition(s) to render for a chunk starting at ``start_char``:
        the nearest at or before it, else the first after it. ``None`` = all."""
        if start_char is None or len(self.definitions) <= 1:
            return self.definitions
        offsets = [d.offset for d in self.definitions]
        i = bisect.bisect_right(offsets, start_char)
        return (self.definitions[i - 1] if i else self.definitions[0],)


@dataclass(frozen=True)
class Glossary:
    """One document's symbol table. ``entries`` is sorted by symbol, and each
    entry's definitions are in document order — so the same document always
    yields byte-identical output."""

    entries: tuple[GlossaryEntry, ...] = ()
    version: str = EXTRACTOR_VERSION

    def __bool__(self) -> bool:
        return bool(self.entries)

    def as_dict(self) -> dict[str, list[str]]:
        return {e.symbol: [d.text for d in e.definitions] for e in self.entries}

    @property
    def ambiguous_symbols(self) -> list[str]:
        return [e.symbol for e in self.entries if e.ambiguous]

    def for_chunk(
        self,
        chunk_text: str,
        config: GlossaryConfig | None = None,
        start_char: int | None = None,
    ) -> str:
        """The rendered ``symbol_glossary`` field for one chunk: one
        ``"<symbol> — <definition>"`` line per symbol that occurs in
        ``chunk_text`` (canonical comparison), sorted, capped. With
        ``start_char`` and ``config.positional``, a symbol defined several times
        renders only its nearest definition (:meth:`GlossaryEntry.definition_for`).
        ``""`` when no symbol occurs."""
        cfg = config or DEFAULT_CONFIG
        if not self.entries or not chunk_text:
            return ""
        canon = canonical_text(chunk_text)
        where = start_char if cfg.positional else None
        lines: list[str] = []
        size = 0
        for entry in self.entries:
            if len(lines) >= cfg.max_entries_per_chunk:
                break
            pattern = value_pattern(entry.symbol) if cfg.attach_only_with_value else symbol_pattern(entry.symbol)
            if pattern.search(canon) is None:
                continue
            defs = "; ".join(d.text for d in entry.definition_for(where))
            line = display_form(f"{entry.symbol} — {defs}")
            extra = len(line) + (1 if lines else 0)
            if size + extra > cfg.max_chars_per_chunk:
                break
            lines.append(line)
            size += extra
        return "\n".join(lines)


def _build(
    found: Iterable[tuple[int, str, str]], config: GlossaryConfig, version: str
) -> Glossary:
    """``found`` = (raw document offset, symbol, definition). Canonicalise the
    symbol, dedupe definitions per symbol (case-insensitively; the first offset
    wins), keep document order, cap, sort symbols."""
    per: dict[str, list[Definition]] = {}
    seen: dict[str, set[str]] = {}
    for offset, sym, defn in sorted(found, key=lambda t: (t[0], t[1], t[2])):
        key = canonical_symbol(sym)
        norm = display_form(defn).casefold()
        bucket = per.setdefault(key, [])
        keys = seen.setdefault(key, set())
        if norm in keys or len(bucket) >= config.max_definitions_per_symbol:
            continue
        keys.add(norm)
        bucket.append(Definition(display_form(defn), offset))
    entries = tuple(GlossaryEntry(s, tuple(d)) for s, d in sorted(per.items()) if d)
    return Glossary(entries, version)


def extract_glossary(text: str, config: GlossaryConfig | None = None) -> Glossary:
    """Extract a document's symbol table with the deterministic rules.

    Runs on the canonical form of ``text`` (so every spelling of a symbol lands
    on one key) and records each definition's offset in the RAW ``text``. Pure
    and total: never raises on empty, symbol-free or pathological input; same
    input → byte-identical output. Every definition is a span of the document
    (canonicalised, whitespace collapsed, leading article dropped, possibly
    truncated)."""
    cfg = config or DEFAULT_CONFIG
    if not text or not text.strip():
        return Glossary((), EXTRACTOR_VERSION)
    canon, pos = definition_map(text)
    found: list[tuple[int, str, str]] = []

    def add(offset: int, sym: str, raw: str) -> None:
        if not is_specific_symbol(sym, allow_single_letter=cfg.allow_single_letter):
            return
        defn = _clean_definition(raw, cfg.max_definition_words)
        if _plausible_definition(defn, sym):
            found.append((pos[min(offset, len(pos) - 1)], sym, defn))

    if cfg.rule_where:
        # Sentence ends, found once: searching for the next one per match is
        # quadratic on a long run without one (rules/3 folds single line
        # breaks, so such runs are longer than they were).
        ends = [e.start() for e in _SENTENCE_END.finditer(canon)]
        # A watermark: the continuation search is rules/2's, unbounded to the
        # sentence end, but each stretch of text is scanned once. A second
        # "where" in a sentence already scanned adds nothing (its clauses were
        # found by the first scan), and rescanning it per match was quadratic
        # on a long run without a sentence end. No window cut, so no clause is
        # ever truncated by one.
        covered = 0
        for m in _WHERE.finditer(canon):
            add(m.start("sym"), m.group("sym"), m.group("def"))
            # Continuation clauses of the same sentence: "…, and pm is the …".
            i = bisect.bisect_left(ends, m.end())
            sentence_end = ends[i] if i < len(ends) else len(canon)
            start = max(m.end("def"), covered)
            if start < sentence_end:
                for c in _CONTINUATION.finditer(canon, start, sentence_end):
                    add(c.start("sym"), c.group("sym"), c.group("def"))
                covered = sentence_end
    if cfg.rule_is_the:
        for m in _IS_THE.finditer(canon):
            if _AFTER_NUMBER.search(canon, max(0, m.start("sym") - 12), m.start("sym")):
                continue  # "500 mL is the volume per breath": a unit, not a symbol
            add(m.start("sym"), m.group("sym"), m.group("def"))
    for rule_on, pattern in (
        (cfg.rule_appositive, _APPOSITIVE),
        (cfg.rule_parenthetical, _PAREN),
    ):
        if not rule_on:
            continue
        for m in pattern.finditer(canon):
            # The regex is leftmost, so the phrase may have started too early
            # ("We estimate the settling speed (vs)"): keep only the words after
            # the last function word.
            words = m.group("def").split()
            cut = max((i for i, w in enumerate(words[:-1]) if w.lower() in _NP_STOP), default=-1)
            words = words[cut + 1 :]
            if len(words) < 2:
                continue  # "the rate λv" says nothing
            if _RELATION_AFTER.match(canon, m.end()):
                continue  # "smaller than the air exchange rate, λs(r)<λa"
            add(m.start("sym"), m.group("sym"), " ".join(words))
    if cfg.rule_respectively:
        for m in _RESPECTIVELY.finditer(canon):
            syms: list[tuple[int, str]] = []
            at = m.start("syms")
            for part in _LIST_SPLIT.split(m.group("syms")):
                at = canon.index(part, at)
                syms.append((at, _TRAILING_ARGS.sub("", part)))
                at += len(part)
            defs = [d for d in _LIST_SPLIT.split(m.group("defs")) if d.strip()]
            if len(syms) < 2 or len(syms) != len(defs):
                continue
            for (offset, sym), defn in zip(syms, defs, strict=True):
                add(offset, sym, defn)
    return _build(found, cfg, EXTRACTOR_VERSION)


# --- validation (the LLM glossary's gate) -------------------------------------

_TRAILING_ARGS = re.compile(r"\s*\([^()]*\)$")


def _norm_words(text: str) -> set[str]:
    return {w.casefold() for w in _WORDS.findall(canonical_text(text))}


# Words that carry no meaning of their own in a definition; the locality gate
# checks only the others.
_DEF_STOP = frozenset(
    "a an the of in on at to for with by from and or per as is are be its their this that".split()
)


def _near_symbol(defn: str, canon: str, occurrences: list[int], window: int) -> bool:
    """Does every content word of ``defn`` occur within ``window`` characters
    of SOME occurrence of the symbol? ``occurrences`` are offsets into
    ``canon``, and the spans are cut from ``canon`` itself (then casefolded),
    so offsets and spans always refer to the same string. Words are matched by
    a stem prefix (the word minus up to three trailing letters, at least five),
    so "deactivation" is found in "deactivated"."""
    words = [w.casefold() for w in _WORDS.findall(canonical_text(defn))]
    words = [w for w in words if len(w) > 2 and w not in _DEF_STOP]
    if not words:
        return False
    spans = [canon[max(0, o - window) : o + window].casefold() for o in occurrences]
    for w in words:
        stem = re.compile(r"\b" + re.escape(w[: max(5, len(w) - 3)]))
        if not any(stem.search(span) for span in spans):
            return False
    return True


def _verbatim_pairs(
    defn: str, canon: str, occurrences: list[int], window: int
) -> list[tuple[int, int, int, int]]:
    """``(distance, def_start, def_end, symbol_offset)`` for every verbatim occurrence
    of ``defn`` within ``window`` of a symbol occurrence, nearest first."""
    needle = " ".join(canonical_text(defn).split()).casefold()
    if not needle:
        return []
    hay = canon.casefold()
    if len(hay) != len(canon):  # casefold changed lengths (ß→ss): offsets unusable
        return []
    out = []
    for m in re.finditer(re.escape(needle).replace(r"\ ", r"\s+"), hay):
        for o in occurrences:
            dist = o - m.end() if o >= m.end() else m.start() - o
            if 0 <= dist <= window:
                out.append((dist, m.start(), m.end(), o))
    return sorted(out)


def _another_symbol_between(canon: str, a: int, b: int, symbol: str) -> bool:
    """Is a DIFFERENT specific symbol token between offsets ``a`` and ``b``?
    "the air change rate λa and the viral deactivation rate λv": the phrase
    "air change rate" is next to λa, and λa stands between it and λv."""
    lo, hi = min(a, b), max(a, b)
    for m in re.finditer(_SYMBOL, canon[lo:hi]):
        tok = m.group("sym")
        if tok != symbol and is_specific_symbol(tok):
            return True
    return False


def validate_entries(
    raw: Iterable[tuple[str, str] | tuple[str, str, int | None]],
    document_text: str,
    version: str,
    config: GlossaryConfig | None = None,
) -> tuple[Glossary, int]:
    """Keep only what the document itself supports. ``raw`` items are
    ``(symbol, definition)`` or ``(symbol, definition, offset)``. Returns
    ``(glossary, rejected_count)``.

    * the symbol (canonicalised; trailing arguments ``λv(r)`` → ``λv``) must
      occur in the document as a whole token and pass the rules' specificity
      guard;
    * locality (``config.definition_window``): every content word of the
      definition must occur near an occurrence of the symbol — a definition
      assembled from words scattered across the paper is not the paper's
      definition of THIS symbol;
    * every word of the definition must occur in the document (canonical,
      case-insensitive) — the "copied from the document, never from a lexicon"
      rule, checked rather than trusted. A model that "knows" λv is a
      ventilation rate cannot get that word past this gate unless the paper
      says it;
    * offset: the given one, else where the definition occurs verbatim in the
      document, else the symbol's first occurrence.
    """
    cfg = config or DEFAULT_CONFIG
    canon, pos = canonical_map(document_text)
    canon_fold = canon.casefold()
    doc_words = {w.casefold() for w in _WORDS.findall(canon)}
    found: list[tuple[int, str, str]] = []
    rejected = 0
    for item in raw:
        sym, raw_def = item[0], item[1]
        given = item[2] if len(item) > 2 else None  # type: ignore[misc]
        sym = canonical_symbol(_TRAILING_ARGS.sub("", (sym or "").strip()))
        defn = _clean_definition(raw_def or "", cfg.max_definition_words)
        occ = symbol_pattern(sym).search(canon) if sym else None
        ok = (
            occ is not None
            and is_specific_symbol(sym, allow_single_letter=cfg.allow_single_letter)
            and _plausible_definition(defn, sym)
            and _norm_words(defn) <= doc_words
        )
        occurrences = [m.start() for m in symbol_pattern(sym).finditer(canon)] if ok else []
        pairs = _verbatim_pairs(defn, canon, occurrences, cfg.definition_window or len(canon)) if ok else []
        if ok and cfg.definition_window > 0:
            ok = _near_symbol(defn, canon, occurrences, cfg.definition_window)
            # A verbatim copy of the phrase sits next to the symbol only through
            # ANOTHER symbol (it names that one): reject. Paraphrases cannot be
            # checked this way — a known limitation, see the tests.
            if ok and pairs:
                # The region between the definition copy and the symbol: from
                # the copy's END to the symbol, or from the symbol's end to
                # the copy's start.
                ok = any(
                    not _another_symbol_between(
                        canon, d1 if d1 <= o else o + len(sym), o if d1 <= o else d0, sym
                    )
                    for _, d0, d1, o in pairs
                )
        if not ok or occ is None:
            rejected += 1
            continue
        if isinstance(given, int):
            offset = given
        elif pairs:
            offset = pos[pairs[0][1]]  # the verbatim copy nearest a use of the symbol
        else:
            at = canon_fold.find(" ".join(canonical_text(defn).split()).casefold())
            offset = pos[at] if at >= 0 else pos[occ.start()]
        found.append((offset, sym, defn))
    return _build(found, cfg, version), rejected


# --- the content-addressed cache ---------------------------------------------


def content_key(text: str) -> str:
    """The cache key for a document: sha256 of its text, outer whitespace
    stripped (so the text reassembled from chunks by the backfill and the
    loader's ``Document.content`` agree when they differ only at the edges)."""
    return hashlib.sha256(text.strip().encode("utf-8", "replace")).hexdigest()


_CacheEntries = list[tuple[str, tuple[tuple[str, int], ...]]]


class SymbolGlossaryCache:
    """Per-document glossaries keyed by ``content_key`` + extractor version.

    The segmentation-cache pattern (``ingestion/segmentation_cache.py``): an
    append-only JSONL file, loaded fully at construction, a corrupt line skipped.
    One record per (key, version): ``{"k": sha256, "v": "llm/1:<model>:<hash>",
    "e": [[symbol, [[definition, offset], ...]], ...]}``. Written only by the
    offline LLM step; ingest opens it read-only and never calls a model.

    A lookup returns the LAST record written for the key (a re-run with a new
    prompt or model supersedes the old one), unless ``version`` pins one.
    """

    def __init__(self, path: Path | str, *, writable: bool = False) -> None:
        self._path = Path(path)
        self._records: dict[str, dict[str, _CacheEntries]] = {}
        #: Per-(key, version) generation facts, e.g. ``{"cut_off": 1}`` when the
        #: model hit ``max_tokens`` (its answer may be missing rows).
        self._meta: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._load()
        self._fh = open(self._path, "a", encoding="utf-8") if writable else None

    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        entries: _CacheEntries = [
                            (str(s), tuple((str(d), int(o)) for d, o in ds)) for s, ds in rec["e"]
                        ]
                        by_version = self._records.setdefault(str(rec["k"]), {})
                        by_version.pop(str(rec["v"]), None)  # re-insert = last wins
                        by_version[str(rec["v"])] = entries
                        self._meta[(str(rec["k"]), str(rec["v"]))] = dict(rec.get("m") or {})
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        continue
        except FileNotFoundError:
            pass

    def __len__(self) -> int:
        return len(self._records)

    def get(self, key: str, version: str | None = None) -> tuple[str, _CacheEntries] | None:
        with self._lock:
            by_version = self._records.get(key)
            if not by_version:
                return None
            if version is not None:
                entries = by_version.get(version)
                return (version, entries) if entries is not None else None
            last = next(reversed(by_version))
            return last, by_version[last]

    def meta(self, key: str, version: str) -> dict[str, Any]:
        """Generation facts recorded with a (key, version) record; ``{}`` if none."""
        with self._lock:
            return dict(self._meta.get((key, version), {}))

    def put(self, key: str, glossary: Glossary, meta: dict[str, Any] | None = None) -> None:
        if self._fh is None:
            raise RuntimeError("SymbolGlossaryCache opened read-only")
        entries: _CacheEntries = [
            (e.symbol, tuple((d.text, d.offset) for d in e.definitions)) for e in glossary.entries
        ]
        rec: dict[str, Any] = {"k": key, "v": glossary.version,
                               "e": [[s, [[d, o] for d, o in ds]] for s, ds in entries]}
        if meta:
            rec["m"] = dict(meta)
        with self._lock:
            by_version = self._records.setdefault(key, {})
            by_version.pop(glossary.version, None)
            by_version[glossary.version] = entries
            self._meta[(key, glossary.version)] = dict(meta or {})
            self._fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:  # noqa: BLE001
                pass


# --- the pipeline step --------------------------------------------------------


@dataclass
class AttachResult:
    """What :meth:`SymbolGlossaryAttacher.apply` did — counts for the INFO log."""

    chunks_with_glossary: int = 0
    docs_rules: int = 0
    docs_cached: int = 0
    #: Symbols defined more than once, differently, summed over documents.
    ambiguous_symbols: int = 0
    versions: set[str] = field(default_factory=set)

    def summary(self) -> str:
        return (
            f"{self.chunks_with_glossary} chunk(s) carry a glossary; "
            f"documents: {self.docs_cached} from the LLM cache, {self.docs_rules} by rules; "
            f"{self.ambiguous_symbols} symbol(s) defined more than once"
        )


class SymbolGlossaryAttacher:
    """Build each document's glossary and render it onto that document's chunks.

    Prefers a cached (offline, LLM-produced) glossary for the document's exact
    text when a cache is configured and holds one — re-validated against the
    document here, so a cache written by an older, laxer validator still cannot
    inject a word the document does not contain — and otherwise falls back to
    the rules. Every chunk of a processed document is stamped with
    ``metadata.symbol_glossary_version`` (also the empty ones: "processed, no
    symbol here" is a fact the backfill needs to skip it)."""

    def __init__(
        self,
        config: GlossaryConfig | None = None,
        cache: SymbolGlossaryCache | None = None,
        cache_version: str | None = None,
    ) -> None:
        self.config = config or DEFAULT_CONFIG
        self.cache = cache
        self.cache_version = cache_version

    def glossary_for(self, text: str) -> Glossary:
        if self.cache is not None:
            hit = self.cache.get(content_key(text), self.cache_version)
            if hit is not None:
                version, entries = hit
                raw = [(s, d, o) for s, defs in entries for d, o in defs]
                glossary, _ = validate_entries(raw, text, version, self.config)
                return glossary
        return extract_glossary(text, self.config)

    def apply(self, documents: Sequence[Document], chunks: Sequence[Chunk]) -> AttachResult:
        result = AttachResult()
        by_doc: dict[str, list[Chunk]] = {}
        for chunk in chunks:
            by_doc.setdefault(chunk.doc_id, []).append(chunk)
        for doc in documents:
            doc_chunks = by_doc.get(doc.id)
            if not doc_chunks:
                continue
            glossary = self.glossary_for(doc.content)
            if glossary.version == EXTRACTOR_VERSION:
                result.docs_rules += 1
            else:
                result.docs_cached += 1
            result.versions.add(glossary.version)
            result.ambiguous_symbols += len(glossary.ambiguous_symbols)
            for chunk in doc_chunks:
                chunk.symbol_glossary = glossary.for_chunk(
                    chunk.content, self.config, start_char=chunk.start_char
                )
                chunk.metadata[VERSION_KEY] = glossary.version
                if chunk.symbol_glossary:
                    result.chunks_with_glossary += 1
        return result

    def provenance(self) -> dict[str, Any]:
        """The step record for the collection manifest (hash-excluded
        ``enrichment``; ADR-0011)."""
        rec: dict[str, Any] = {
            "extractor": EXTRACTOR_VERSION,
            "config": config_dict(self.config),
        }
        if self.cache is not None:
            rec["llm_cache"] = True
            if self.cache_version:
                rec["llm_cache_version"] = self.cache_version
        return {"symbol_glossary": rec}


def attacher_from_mode(
    mode: str, config_json: str = "", cache_path: str = ""
) -> SymbolGlossaryAttacher | None:
    """Build an attacher from the operator-facing ``off`` / ``rules`` mode.

    The one place the API and the CLI ingest paths turn their setting into a
    step, so they cannot drift on what ``rules`` means. ``cache_path`` names an
    LLM glossary cache written by ``scripts/symbol_glossary_llm.py``; it is
    opened read-only and a missing file is an empty cache."""
    if mode == "off":
        return None
    if mode != "rules":
        raise ValueError(f"unknown symbol glossary mode {mode!r} (expected off|rules)")
    cache = SymbolGlossaryCache(cache_path) if cache_path else None
    return SymbolGlossaryAttacher(config_from_json(config_json), cache)
