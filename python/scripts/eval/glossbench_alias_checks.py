"""Alias benchmark (pure half): the matchers, the drafter and verifier prompts, their
parsers and the layer-1 mechanical checks for the two job kinds.

No I/O, no network, no model. The runner is :mod:`glossbench_alias_draft`, the pool
builder :mod:`glossbench_alias_pool`.

**Study terms.** A glossary entry (the sibling glossary builder's row
``{doc_id, alias, primary, kind: symbol|abbreviation, def_chunk_id, use_chunk_ids}``)
maps an ALIAS (a symbol or an abbreviation) to its PRIMARY term. Two job kinds:

* **G** (glossary question): the chunk contains the alias and NOT the primary term; the
  drafter writes a question that asks WITH THE PRIMARY TERM and is answered by the chunk;
  ``{question, answer, quote}``, the quote verbatim from the chunk and containing the alias.
* **C** (control): the chunk contains the primary term literally; the question uses the
  term; the quote is verbatim and contains the term.

Answers need not be numeric.

**Matchers** (one place each, so a change is one line):

* :func:`alias_in_text` — the alias as a whole token, case-sensitive, after the
  library's canonicalisation (:func:`ragstack.ingestion.symbol_glossary.symbol_occurs`:
  NFKC, sub/superscripts, ``Q_b``/``Q b``/``Qb`` one key). Used for "the chunk / quote
  contains the alias". Meant to be the sibling glossary builder's matcher; if the
  sibling's differs, replace this one function.
* :func:`alias_variant_in` — the stricter question guard: the alias's canonical key with
  any run of spaces, ``_ - . ^ { }`` allowed between its characters, a plural ``s``/``es``
  allowed for an abbreviation, case-INsensitive when the key has two or more characters
  and its casefold is not an English stop word (``AS`` vs ``as`` stays case-sensitive, as
  does a one-letter symbol: ``a`` vs the article).
* :func:`primary_content_words` — the primary term's words of at least 5 characters that
  are not stop words; :func:`chunk_has_primary` (the STRICT rule) is true when any of
  them occurs as a word of the text (casefold, exact token) or the primary's word
  sequence occurs literally.
* :func:`primary_literal_in` — the primary's word sequence occurs contiguously (casefold,
  word tokens of the canonical text; "air-change rate" = "air change rate").
* :func:`primary_in_question` — "the question contains the primary term or a close
  variant": the literal word sequence, or every content word of the primary (>= 3 chars,
  not a stop word) present after a light suffix stem (``-ies -es -s -ing -ed -al``) on
  both sides. A primary with no such word needs the literal sequence.

**Layer-1 reason codes** (a draft is rejected when any applies; every code is recorded):

==========================  =====================================================
``unparseable``             not one JSON object with string question/answer/quote
``no_answer``               the drafter said the passage supports no such question
                            (exempt from the redraw)
``question_missing``        empty question
``answer_missing``          empty answer
``multiple_questions``      the question holds more than one ``?`` (one per job)
``question_too_short``      fewer than ``question_min_words`` words (5)
``question_too_long``       more than ``question_max_words`` words (40) or
                            ``question_max_chars`` characters (300)
``not_standalone``          the question refers to its source document: "this study /
                            paper / work", "the present study", "the study" (not "the
                            study of"), "the authors", "the proposed", "we", "our",
                            lowercase "us", "in this work", "described above", a
                            figure / table / section / equation / appendix /
                            supplementary reference (:func:`self_references`) — a
                            correct answer from ANOTHER paper counts as correct, so a
                            question must be answerable without knowing the paper
``quote_too_long``          over ``quote_max_chars`` (300)
``quote_not_in_paper``      the quote (gold normalisation) is not in the paper
``quote_not_unique``        it occurs more than once in the paper
``cut_by_boundary``         once in the paper, in no single chunk
``quote_not_in_source``     in the paper, not in the chunk it was drafted from
``answer_not_in_quote``     less than ``answer_support_min`` (0.5) of the answer's
                            tokens (stop words removed) occur in the quote; layer 2's
                            ``supports`` judges the meaning
``lexical_leak``            the question shares more than ``leak_threshold`` (0.5) of
                            the quote's distinctive tokens, the primary term's and the
                            alias's tokens excluded (the question must carry the term)
G: ``quote_lacks_alias``    the quote does not contain the alias (:func:`alias_in_text`)
G: ``alias_in_question``    the question contains the alias or a trivial variant
                            (:func:`alias_variant_in`)
G: ``primary_not_in_question``  the question lacks the primary term or a close variant
G: ``gold_has_primary``     the source chunk, or any quote-bearing chunk, contains the
                            primary term under the strict rule (re-checked; the pool
                            built from the sibling's glossary is not trusted)
C: ``term_not_in_question`` the question lacks the term (close-variant rule)
C: ``term_not_in_quote``    the quote does not contain the term literally
C: ``term_not_in_chunk``    the source chunk does not contain the term literally
``duplicate_question``      (cross-record, :func:`glossbench_r2_checks.cross_record`)
==========================  =====================================================

Flags (never a reject): ``shared_chunk`` (cross-record).
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _glossbench_r2 as g  # noqa: E402
import glossbench_r2_checks as ck  # noqa: E402

from ragstack.ingestion.symbol_glossary import (  # noqa: E402
    _NOT_TOKEN_AFTER,
    _NOT_TOKEN_BEFORE,
    canonical_symbol,
    canonical_text,
    symbol_occurs,
)

KINDS = ("G", "C")
ALIAS_KINDS = ("symbol", "abbreviation")
CHECK_DEFAULTS: dict[str, Any] = {
    "quote_max_chars": 300,
    "leak_threshold": 0.5,
    "top_frequent": 200,
    "question_min_words": 5,
    "question_max_words": 40,
    "question_max_chars": 300,
    "answer_support_min": 0.5,
    #: the strict "contains the primary" rule: content words of at least this many characters
    "primary_min_chars": 5,
}
REDRAW_EXEMPT = frozenset({"no_answer"})
DRAFT_KEYS = ("question", "answer", "quote")

# ------------------------------------------------------------------ matchers

_WORD = re.compile(r"[^\W_]+")
_SEP = r"[\s_\-.^{}]*"


def words(text: str) -> list[str]:
    """Casefolded word tokens of the canonical text."""
    return [w.casefold() for w in _WORD.findall(canonical_text(text))]


def alias_in_text(alias: str, text: str) -> bool:
    """The alias as a whole token (case-sensitive, canonical forms): the chunk/quote matcher."""
    return symbol_occurs(alias, text)


@lru_cache(maxsize=4096)
def _variant_pattern(key: str, plural: bool) -> re.Pattern[str]:
    body = _SEP.join(re.escape(c) for c in key)
    tail = r"(?:'?e?s)?" if plural else ""
    insensitive = len(key) >= 2 and key.casefold() not in g.STOP_WORDS
    return re.compile(_NOT_TOKEN_BEFORE + body + tail + _NOT_TOKEN_AFTER, re.I if insensitive else 0)


def alias_variant_in(alias: str, text: str, alias_kind: str = "symbol") -> bool:
    """The alias or a trivial variant (case, spacing, separators, sub/superscript
    normalisation, an abbreviation's plural) as a whole token of ``text``."""
    key = "".join(c for c in canonical_symbol(alias) if c not in "_-.^{}")
    if not key:
        return False
    return _variant_pattern(key, alias_kind == "abbreviation" and len(key) >= 2).search(
        canonical_text(text)) is not None


def primary_content_words(primary: str, min_chars: int = 5) -> list[str]:
    return [w for w in dict.fromkeys(words(primary)) if len(w) >= min_chars and w not in g.STOP_WORDS]


def _contains_seq(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def primary_literal_in(primary: str, text: str) -> bool:
    """The primary's word sequence occurs contiguously in ``text`` (casefold, canonical words)."""
    return _contains_seq(words(text), words(primary))


def chunk_has_primary(primary: str, text: str, min_chars: int = 5) -> bool:
    """The STRICT rule: any content word (>= ``min_chars``) of the primary occurs as a word
    of ``text``, or the primary occurs literally."""
    tw = set(words(text))
    return bool(set(primary_content_words(primary, min_chars)) & tw) or primary_literal_in(primary, text)


_SUFFIXES = ("ies", "es", "s", "ing", "ed", "al")


def stem(w: str) -> str:
    for suf in _SUFFIXES:
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def primary_in_question(primary: str, question: str) -> bool:
    """The question carries the primary term or a close variant (module docstring)."""
    if primary_literal_in(primary, question):
        return True
    content = [w for w in words(primary) if len(w) >= 3 and w not in g.STOP_WORDS]
    if not content:
        return False
    qs = {stem(w) for w in words(question)}
    return all(stem(w) in qs for w in content)


# ------------------------------------------------------------------ standalone

_DOC_NOUNS = (r"study|studies|paper|work|article|manuscript|research|report|analysis|investigation|"
              r"experiments?|review|survey|thesis|chapter|dataset|data set|model|approach|method|framework|"
              r"trial|cohort|text|publication|letter|simulation|simulations")
_SELF_REF = re.compile(
    rf"\b(?:this|the present|the current|these|our)\s+(?:{_DOC_NOUNS})\b"
    r"|\bthe (?:study|paper|article|manuscript|publication)\b(?!\s+(?:of|on|by)\b)"
    r"|\b(?:the|these|its) (?:authors?|researchers|investigators)\b"
    r"|\bthe proposed\b|\b(?:we|our|ours|ourselves)\b|\bin this (?:work|context|setting)\b"
    r"|\b(?:described|mentioned|reported|presented|shown|given|defined|discussed) (?:above|below|here|earlier)\b"
    r"|\b(?:fig(?:ure)?s?|tables?|sections?|eqs?|equations?|appendix|appendices|panels?|schemes?)\.?\s*"
    r"(?:\(\s*)?(?:[a-z]?\d+|[ivx]+\b|s\d+)"
    r"|\b(?:the|this|that|above|following|preceding) (?:fig(?:ure)?|table|section|equation|appendix|panel)s?\b"
    r"|\bsupplementary\b|\bsupporting information\b")
_US = re.compile(r"\bus\b")                       # lowercase "us" only: "the US" is a country


def self_references(question: str) -> list[str]:
    """The phrases by which ``question`` refers to its source document (empty = standalone)."""
    t = unicodedata.normalize("NFKC", question)
    hits = [m.group(0).strip() for m in _SELF_REF.finditer(t.casefold())]
    hits += [m.group(0) for m in _US.finditer(t)]
    return list(dict.fromkeys(hits))


# ------------------------------------------------------------------ prompts

_ALIAS_WORD = {"symbol": "the symbol", "abbreviation": "the abbreviation"}

DRAFT_SYSTEM_G = """You write one question for a retrieval benchmark from ONE passage of a scientific paper. Work only from the passage you are given.

You are told a TERM and a SHORT FORM (a symbol or an abbreviation) that the paper uses for it. The passage uses the short form and does not spell out the term. Write a question that asks about something the passage states about the TERM, then answer with a JSON object only, exactly these keys:
{"question": "...", "answer": "...", "quote": "..."}

- "question": ONE question. Name the TERM in words. Never write the SHORT FORM or any spelling of it. The question must stand alone: a reader who has never seen this paper understands it, and a correct answer from another paper would also be correct. Never refer to the paper, its authors or its parts: no "this study", "this paper", "the authors", "the proposed", "we", "our", "in this work", and no figure, table, section or equation. Between 6 and 35 words.
- "answer": short, in the passage's own words.
- "quote": copied exactly, character for character, from the passage; at most 300 characters; it must contain the SHORT FORM and state the answer.

If the passage states nothing about the TERM that such a question could ask, answer {"none": true}."""

DRAFT_SYSTEM_C = """You write one question for a retrieval benchmark from ONE passage of a scientific paper. Work only from the passage you are given.

You are told a TERM that the passage spells out. Write a question that asks about something the passage states about the TERM, then answer with a JSON object only, exactly these keys:
{"question": "...", "answer": "...", "quote": "..."}

- "question": ONE question. Name the TERM in words. The question must stand alone: a reader who has never seen this paper understands it, and a correct answer from another paper would also be correct. Never refer to the paper, its authors or its parts: no "this study", "this paper", "the authors", "the proposed", "we", "our", "in this work", and no figure, table, section or equation. Between 6 and 35 words.
- "answer": short, in the passage's own words.
- "quote": copied exactly, character for character, from the passage; at most 300 characters; it must contain the TERM and state the answer.

If the passage states nothing about the TERM that such a question could ask, answer {"none": true}."""

REASON_WORDS = {
    "unparseable": "the answer was not the JSON object asked for",
    "question_missing": "the question was empty",
    "answer_missing": "the answer was empty",
    "multiple_questions": "more than one question was asked",
    "question_too_short": "the question was too short",
    "question_too_long": "the question was too long",
    "not_standalone": "the question referred to the paper, its authors or its parts",
    "quote_too_long": "the quote was longer than 300 characters",
    "quote_not_in_paper": "the quote was not copied exactly from the passage",
    "quote_not_in_source": "the quote was not copied exactly from the passage",
    "quote_not_unique": "the quote also occurs elsewhere in the paper",
    "cut_by_boundary": "the quote was not copied exactly from the passage",
    "answer_not_in_quote": "the answer was not stated in the quote",
    "lexical_leak": "the question copied too many of the quote's words",
    "quote_lacks_alias": "the quote did not contain the short form",
    "alias_in_question": "the question contained the short form",
    "primary_not_in_question": "the question did not name the term",
    "term_not_in_question": "the question did not name the term",
    "term_not_in_quote": "the quote did not contain the term",
}


def draft_messages(kind: str, content: str, entry: Mapping[str, Any],
                   previous: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    """The drafter's messages: the chunk, the entry (G: term and short form; C: the term)."""
    if kind not in KINDS:
        raise ValueError(f"job kind {kind!r}")
    user = f"PASSAGE:\n<<<\n{content}\n>>>\n\nTERM: {entry['primary']}\n"
    if kind == "G":
        user += f"SHORT FORM ({_ALIAS_WORD.get(entry.get('alias_kind') or '', 'the short form')}): {entry['alias']}\n"
    if previous is not None:
        why = sorted({REASON_WORDS.get(r, r) for r in previous.get("reasons") or []})
        user += ("\nAn earlier answer for this passage was rejected"
                 + (f" ({'; '.join(why)})" if why else "") + ". Answer again with the JSON object only.")
    return [{"role": "system", "content": DRAFT_SYSTEM_G if kind == "G" else DRAFT_SYSTEM_C},
            {"role": "user", "content": user}]


def parse_draft(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """(draft, error); ``{"none": true}`` -> ``{"none": True}``."""
    try:
        obj = json.loads(ck._strip_fence(text))
    except ValueError as e:
        return None, f"json: {str(e)[:120]}"
    if not isinstance(obj, dict):
        return None, "not an object"
    if obj.get("none") is True:
        return {"none": True}, None
    missing = [k for k in DRAFT_KEYS if k not in obj]
    if missing:
        return None, f"missing {missing}"
    bad = [k for k in DRAFT_KEYS if not isinstance(obj[k], str)]
    if bad:
        return None, f"not a string: {bad}"
    return {k: obj[k] for k in DRAFT_KEYS}, None


VERIFY_SYSTEM = """You check ONE benchmark record drafted from a scientific paper. Judge only from the text you are given; never from outside knowledge.

Answer with a JSON object only, exactly these keys:
{"supports": true|false, "standalone": true|false, "term_denoted": true|false, "single_reading": true|false, "reason": "..."}

- "supports": the QUOTE alone states the ANSWER to the QUESTION (reading the SHORT FORM in the quote as the TERM, when a short form is given).
- "standalone": a reader who has never seen this paper understands the question and would accept a correct answer from any source; it does not refer to the paper, its authors, or a figure, table, section or equation.
- "term_denoted": when a SHORT FORM is given, in this paper the short form in the quote stands for the TERM the question asks with (use the definition passage). When none is given, the quote's term is the TERM the question asks with.
- "single_reading": the question can be read in exactly one way.
- "reason": at most 25 words, pointing to the paper's own words."""

VERIFY_KEYS = ("supports", "standalone", "term_denoted", "single_reading")


def verify_prompt(kind: str, rec: Mapping[str, Any], entry: Mapping[str, Any], passage: str,
                  definition: str, title: str) -> str:
    short = entry["alias"] if kind == "G" else None
    return (f"PAPER TITLE: {title or '(unknown)'}\n\n"
            + (f"DEFINITION PASSAGE:\n<<<\n{definition}\n>>>\n\n" if kind == "G" and definition else "")
            + f"PASSAGE:\n<<<\n{passage}\n>>>\n\n"
            f"TERM: {entry['primary']}\n"
            f"SHORT FORM: {short if short else '(none: a control record)'}\n"
            f"QUESTION: {rec['question']}\n"
            f"ANSWER: {rec['answer']}\n"
            f"QUOTE: {rec['quote']}\n\n"
            "Check the record. Answer with the JSON object only.")


def parse_verdict(text: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        obj = json.loads(ck._strip_fence(text))
    except ValueError as e:
        return None, f"json: {str(e)[:120]}"
    if not isinstance(obj, dict):
        return None, "not an object"
    bad = [k for k in VERIFY_KEYS if not isinstance(obj.get(k), bool)]
    if bad:
        return None, f"not boolean: {bad}"
    return {**{k: obj[k] for k in VERIFY_KEYS}, "reason": str(obj.get("reason", ""))[:400]}, None


# ------------------------------------------------------------------ layer 1

def layer1(draft: Mapping[str, Any] | None, parse_error: str | None, *, kind: str, entry: Mapping[str, Any],
           paper: str, source_chunk: g.ChunkRow, idx: ck.PaperIndex, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """The per-record mechanical checks (module docstring). Same output shape as
    :func:`glossbench_r2_checks.layer1`; ``record_stratum`` is the job kind."""
    c = {**CHECK_DEFAULTS, **cfg}
    out: dict[str, Any] = {"reasons": [], "flags": [], "gold_chunk_ids": [], "leak_overlap": None,
                           "leak_distinctive": None, "leak_ok": None, "record_stratum": kind}
    reasons: list[str] = out["reasons"]
    if draft is None:
        reasons.append("unparseable")
        out["parse_error"] = parse_error
        return {**out, "verdict": "reject"}
    if draft.get("none"):
        reasons.append("no_answer")
        return {**out, "verdict": "reject"}
    q, answer, quote = draft["question"].strip(), draft["answer"].strip(), draft["quote"]
    alias, primary = str(entry["alias"]), str(entry["primary"])
    # the question: present, one, bounded, standalone
    if not q:
        reasons.append("question_missing")
    if not answer:
        reasons.append("answer_missing")
    if q.count("?") > 1:
        reasons.append("multiple_questions")
    nw = len(q.split())
    if q and nw < int(c["question_min_words"]):
        reasons.append("question_too_short")
    if nw > int(c["question_max_words"]) or len(q) > int(c["question_max_chars"]):
        reasons.append("question_too_long")
    refs = self_references(q)
    if refs:
        out["self_references"] = refs
        reasons.append("not_standalone")
    # the quote: length, exactly once in the paper, inside one chunk, in the source chunk
    nq = g.gold_norm(quote)
    if len(quote) > int(c["quote_max_chars"]):
        reasons.append("quote_too_long")
    occurrences = sum(d.count(nq) for d in idx.c.norm_docs(paper)) if nq else 0
    if occurrences == 0:
        reasons.append("quote_not_in_paper")
    elif occurrences > 1:
        reasons.append("quote_not_unique")
    else:
        gold = sorted(cid for cid, text in idx.c.norm_chunks(paper).items() if nq in text)
        out["gold_chunk_ids"] = gold
        if not gold:
            reasons.append("cut_by_boundary")
        elif source_chunk.chunk_id not in gold:
            reasons.append("quote_not_in_source")
    # the answer is stated in the quote (lexically; layer 2 judges the meaning)
    at = set(g.leak_tokens(answer))
    if answer and at:
        share = len(at & set(g.leak_tokens(quote))) / len(at)
        out["answer_support"] = round(share, 4)
        if share < float(c["answer_support_min"]):
            reasons.append("answer_not_in_quote")
    # the kind's own rules
    min_chars = int(c["primary_min_chars"])
    if kind == "G":
        if not alias_in_text(alias, quote):
            reasons.append("quote_lacks_alias")
        if q and alias_variant_in(alias, q, str(entry.get("alias_kind") or "symbol")):
            reasons.append("alias_in_question")
        if q and not primary_in_question(primary, q):
            reasons.append("primary_not_in_question")
        texts = [source_chunk.content] + [idx.c.chunks[cid].content for cid in out["gold_chunk_ids"]
                                          if cid != source_chunk.chunk_id]
        if any(chunk_has_primary(primary, t, min_chars) for t in texts):
            reasons.append("gold_has_primary")
    elif kind == "C":
        if q and not primary_in_question(primary, q):
            reasons.append("term_not_in_question")
        if not primary_literal_in(primary, quote):
            reasons.append("term_not_in_quote")
        if not primary_literal_in(primary, source_chunk.content):
            reasons.append("term_not_in_chunk")
    else:
        raise ValueError(f"job kind {kind!r}")
    # lexical leak, the term's and the alias's own tokens excluded
    if q and quote:
        excused = idx.frequent(paper) | set(g.leak_tokens(primary)) | set(g.leak_tokens(alias))
        ov, n = ck.leak_overlap(q, quote, excused)
        out["leak_overlap"], out["leak_distinctive"] = round(ov, 4), n
        out["leak_ok"] = ov <= float(c["leak_threshold"])
        if not out["leak_ok"]:
            reasons.append("lexical_leak")
    out["reasons"] = sorted(dict.fromkeys(reasons))
    out["flags"] = sorted(dict.fromkeys(out["flags"]))
    return {**out, "verdict": "reject" if out["reasons"] else "pass"}
