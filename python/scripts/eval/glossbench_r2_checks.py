"""Glossary benchmark round 2, item 2d (pure half): the drafter and verifier
prompts, their parsers, and the layer-1 mechanical checks (prereg §3.2–3.3).

No I/O, no network, no model. The runner is :mod:`glossbench_r2_draft`.

**Blindness (prereg §3.2).** :func:`draft_messages` is built from the chunk's
content, its stratum's instruction, the value-stating tokens found in the
chunk's own text (identical under every arm) and, on a second attempt, the
first attempt's reject codes. It never receives a glossary, an arm name or the
word "glossary"; ``test_glossbench_r2_draft.py`` pins that.

**Layer-1 reason codes** (a record is rejected when any applies; every code
that applies is recorded):

==========================  =====================================================
``unparseable``             the answer is not one JSON object with the fields
``no_quantity``             the drafter said the passage states no such quantity
``question_missing``        empty question
``quote_too_long``          quote over ``quote_max_chars`` (300)
``quote_not_in_paper``      the quote (gold normalisation) is not in the paper
``quote_not_unique``        it occurs more than once in the paper
``cut_by_boundary``         it occurs once in the paper but in no single chunk
``quote_not_in_source``     it is in the paper, not in the chunk it was drafted from
``quantity_not_in_quote``   the value is empty, has no number, or is not in the quote
``number_in_question``      the question prints the value's number
``symbol_missing``          S1/S2: no symbol given
``symbol_not_in_chunk``     S1/S2: the symbol does not occur in the source chunk
``symbol_in_question``      the question contains the symbol (canonical comparison)
``s3_has_symbol``           S3: a symbol was given
``s3_value_statement``      S3: the quote holds a symbol value statement
``symbol_collision``        the paper's own definition sentences give the symbol two
                            unrelated definitions (where detectable); a reject only
                            under ``collision: reject`` — by default a flag (below)
``binding_terms_invalid``   not 1–3 terms of 1–4 words
``binding_word_not_in_paper``  a binding word the paper does not contain
``lexical_leak``            question shares > ``leak_threshold`` of the quote's
                            distinctive tokens
``duplicate_question``      (cross-record) same question, same paper, earlier record kept
``unit_incompatible``       fields mode: the value prints no number with a unit of the
                            field's dimension (:func:`glossbench_r2_fields.unit_check`)
``term_leak``               fields mode: more than ``term_leak_threshold`` of the
                            question's own content words come from the field's term list
                            (:func:`glossbench_r2_fields.term_leak`)
==========================  =====================================================

**Fields mode** (set P, :mod:`glossbench_r2_fields`): the drafter is asked for a
value of a NAMED field, given its name, definition and unit — no glossary, no
arm, no symbol hint, and not the term list (the terms are retrieval terms, not
the question's wording). ``binding_terms`` are the field's terms, set by the
runner, so the two binding-term checks do not apply. The symbol is optional on
an S1/S2 chunk; a record with no symbol is words-only: its ``record_stratum``
is ``S3`` and its quote may not hold a symbol value statement
(``s3_value_statement``), so S3 stays identifiable per record. Flags added:
``unit_dimension_differs`` (e.g. air changes per hour asked as a volume flow),
``unit_unknown``, ``field_unit_unknown``.

Flags (counted, never a reject): ``symbol_collision`` (default mode ``flag``: the
verifier is asked the ``symbol_unique`` question with a note), ``multi_value``
(the symbol states two or more different values in the paper: case/scenario
ambiguity for layer 2 to judge, also noted in its prompt), ``scenario_terms``
(the quote names a case/scenario/condition word the question does not),
``shared_chunk`` (cross-record: two accepted records share a gold chunk).
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _glossbench_r2 as g  # noqa: E402
import glossbench_r2_fields as fl  # noqa: E402

from ragstack.ingestion.symbol_glossary import (  # noqa: E402
    _NOT_TOKEN_AFTER,
    _NOT_TOKEN_BEFORE,
    canonical_symbol,
    canonical_text,
    extract_glossary,
    symbol_occurs,
)

CHECK_DEFAULTS: dict[str, Any] = {
    "quote_max_chars": 300,
    "leak_threshold": 0.5,
    "top_frequent": 200,
    "max_binding_terms": 3,
    "max_binding_words": 4,
    #: "flag" | "reject" | "off": the symbol-collision check (see :func:`symbol_collision`).
    #: Default ``flag``: the check reads the rules extractor's definition sentences, so a
    #: reject would make the record population depend on the ``rules`` arm; a flagged
    #: collision goes to the verifier prompt as a question instead (``symbol_unique``).
    "collision": "flag",
    #: definitions of one symbol whose content-word Jaccard is below this are "unrelated"
    "collision_jaccard": 0.5,
    #: fields mode: the term-leak guard's threshold (a strict "greater than", like leak_threshold)
    "term_leak_threshold": 0.5,
}
#: ``layer1`` also reads ``vs_stoplist`` (the pool header's statistical stoplist) from
#: its cfg; absent, :data:`_glossbench_r2.STAT_TOKENS` applies.

REDRAW_EXEMPT = frozenset({"no_quantity"})
FIELDS = ("question", "quote", "quantity", "value", "binding_terms", "symbol")
#: fields mode: no model-invented binding terms (the field's terms are used); a case statement
FIELD_MODE_FIELDS = ("question", "quote", "quantity", "value", "symbol", "case")

# ------------------------------------------------------------------ prompts

DRAFT_SYSTEM = """You write one question for a benchmark from ONE passage of a scientific paper. Work only from the passage you are given.

Pick ONE quantity whose value the passage states, then answer with a JSON object only, exactly these keys:
{"question": "...", "quote": "...", "quantity": "...", "value": "...", "binding_terms": ["..."], "symbol": "..."}

- "quantity": the quantity, named in words as the paper words it.
- "question": asks for the value of that quantity in this paper's setting. Name the quantity in words. Never write its symbol, and never write the value or any number from the quote. If the value belongs to one case, scenario, condition or group of the study, name it in words in the question.
- "quote": copied exactly, character for character, from the passage; at most 300 characters; it must contain the value.
- "value": the value as printed in the quote (with its unit if one is printed), copied exactly.
- "binding_terms": 1 to 3 short noun phrases, at most 4 words each, that name the quantity the way the paper words it.
- "symbol": the symbol the passage uses for the quantity, exactly as printed, or "" if it uses none.

Prefer a measured or model quantity over a test statistic such as a p-value. If the passage states no suitable quantity, answer {"none": true}."""

_SYMBOL_TASK = ("Pick a quantity that the passage gives a value for through a symbol (for example "
                "\"X = 3.2\"). Tokens that state a value in this passage: {tokens}.")
_WORDS_TASK = ("Pick a quantity that the passage names in words and gives a value for WITHOUT a symbol "
               "(no \"X = 3.2\"). Set \"symbol\" to \"\".")
_REASON_WORDS = {
    "unparseable": "the answer was not the JSON object asked for",
    "question_missing": "the question was empty",
    "quote_too_long": "the quote was longer than 300 characters",
    "quote_not_in_paper": "the quote was not copied exactly from the passage",
    "quote_not_in_source": "the quote was not copied exactly from the passage",
    "quote_not_unique": "the quote also occurs elsewhere in the paper",
    "cut_by_boundary": "the quote was not copied exactly from the passage",
    "quantity_not_in_quote": "the value was not printed in the quote",
    "number_in_question": "the question contained the value",
    "symbol_missing": "no symbol was given",
    "symbol_not_in_chunk": "the symbol does not occur in the passage",
    "symbol_in_question": "the question contained the symbol",
    "s3_has_symbol": "a symbol was given, but this passage needs a quantity without one",
    "s3_value_statement": "the quote states a value through a symbol",
    "symbol_collision": "the symbol names more than one quantity in the paper",
    "binding_terms_invalid": "the binding terms were not 1 to 3 phrases of at most 4 words",
    "binding_word_not_in_paper": "a binding term used a word the paper does not use",
    "lexical_leak": "the question copied too many of the quote's words",
    "unit_incompatible": "the value was not copied with a printed unit of the quantity's kind",
    "term_leak": "the question used other names for the quantity instead of the definition's words",
}

DRAFT_SYSTEM_FIELDS = """You write one question for a benchmark from ONE passage of a scientific paper. Work only from the passage you are given.

You are told ONE quantity: its name, its definition and its usual unit. Find where the passage states a value for that quantity, then answer with a JSON object only, exactly these keys:
{"question": "...", "quote": "...", "quantity": "...", "value": "...", "symbol": "...", "case": "..."}

- "question": asks for the value of that quantity in this paper's setting. Word it in the definition's own words; do not list other names for the quantity. Never write a symbol, and never write the value or any number from the quote. If the value belongs to one case, scenario, room, condition or group of the study, name it in words in the question.
- "quote": copied exactly, character for character, from the passage; at most 300 characters; it must contain the value.
- "quantity": the quantity, named in words as the paper words it.
- "value": the value exactly as printed in the quote, with the unit printed next to it. Never convert a unit or rewrite a number: if the passage prints "about 2 cm2", write "about 2 cm2".
- "symbol": the symbol the passage uses for the quantity, exactly as printed, or "" if it uses none.
- "case": the case, scenario, room, condition or group of the study the value belongs to, in the paper's words, or "" if the paper gives only one value for it.

The value may be printed in words (for example "two infected people") rather than in digits. If the passage states no value for this quantity, answer {"none": true}."""


def field_draft_messages(content: str, spec: fl.FieldSpec,
                         previous: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    """Fields mode: the chunk text and the named field (name, definition, unit) —
    never a glossary, an arm, the chunk's value-stating tokens or the term list."""
    user = (f"PASSAGE:\n<<<\n{content}\n>>>\n\n"
            f"QUANTITY: {spec.field}\nDEFINITION: {spec.definition}\n"
            f"UNIT: {spec.unit if spec.unit else '(none: a count or a pure number)'}")
    if previous is not None:
        why = sorted({_REASON_WORDS.get(r, r) for r in previous.get("reasons") or []})
        user += ("\n\nAn earlier answer for this passage was rejected"
                 + (f" ({'; '.join(why)})" if why else "") + ". Answer again with the JSON object only.")
    return [{"role": "system", "content": DRAFT_SYSTEM_FIELDS}, {"role": "user", "content": user}]


def draft_messages(content: str, stratum: str, vs_tokens: Sequence[str],
                   previous: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    """The drafter's messages: the chunk text and the task, nothing else."""
    task = _WORDS_TASK if stratum == "S3" else _SYMBOL_TASK.format(
        tokens=", ".join(vs_tokens) if vs_tokens else "(none found)")
    user = f"PASSAGE:\n<<<\n{content}\n>>>\n\n{task}"
    if previous is not None:
        why = sorted({_REASON_WORDS.get(r, r) for r in previous.get("reasons") or []})
        q = (previous.get("draft") or {}).get("quantity")
        user += ("\n\nAn earlier answer for this passage was rejected"
                 + (f" ({'; '.join(why)})" if why else "") + "."
                 + (f" Choose a different quantity than \"{q}\" if the passage has one." if q else "")
                 + " Answer again with the JSON object only.")
    return [{"role": "system", "content": DRAFT_SYSTEM}, {"role": "user", "content": user}]


def _strip_fence(text: str) -> str:
    t = text.strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", t, re.S)
    return m.group(1) if m else t


def parse_draft(text: str, mode: str = "open") -> tuple[dict[str, Any] | None, str | None]:
    """(draft, error). ``{"none": true}`` parses to ``{"none": True}``. ``mode``
    ``fields`` expects :data:`FIELD_MODE_FIELDS` (no binding terms)."""
    try:
        obj = json.loads(_strip_fence(text))
    except ValueError as e:
        return None, f"json: {str(e)[:120]}"
    if not isinstance(obj, dict):
        return None, "not an object"
    if obj.get("none") is True:
        return {"none": True}, None
    if mode == "fields":
        missing = [f for f in FIELD_MODE_FIELDS if f not in obj]
        if missing:
            return None, f"missing {missing}"
        return {f: ("" if obj[f] is None else str(obj[f])) for f in FIELD_MODE_FIELDS}, None
    missing = [f for f in FIELDS if f not in obj]
    if missing:
        return None, f"missing {missing}"
    terms = obj["binding_terms"]
    if isinstance(terms, str):
        terms = [terms]
    if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
        return None, "binding_terms is not a list of strings"
    out = {f: ("" if obj[f] is None else str(obj[f])) for f in FIELDS if f != "binding_terms"}
    out["binding_terms"] = [t.strip() for t in terms]
    return out, None


VERIFY_SYSTEM = """You check ONE benchmark record drafted from a scientific paper. Judge only from the text you are given (the beginning of the paper, the passage, the quote); never from outside knowledge.

Answer with a JSON object only, exactly these keys:
{"supports": true|false, "single_reading": true|false, "same_case": true|false, "symbol_unique": true|false, "reason": "..."}

- "supports": the quote, read in the passage, states that the answer is the value of the quantity the question asks for.
- "single_reading": the question can be read in exactly one way in this paper; a reader of the paper would know which quantity and which setting it means.
- "same_case": the answer is not taken from a different case, scenario, condition, group or arm of the study than the one the question names (or, if the question names none, the paper reports only one value for it).
- "symbol_unique": the symbol is not also used for a different quantity in the text you see. If no symbol is given, answer true.
- "reason": at most 25 words, pointing to the paper's own words."""

VERIFY_KEYS = ("supports", "single_reading", "same_case", "symbol_unique")
#: Layer-1 flags that become a question to the verifier (never a glossary, never an arm).
FLAG_NOTES = {
    "symbol_collision": ("A mechanical check found the paper's own text defining this symbol in more "
                         "than one way. Decide \"symbol_unique\" from the text with particular care."),
    "multi_value": ("A mechanical check found the paper stating more than one value for this symbol. "
                    "Decide \"same_case\" with particular care."),
}


def verify_prompt(rec: Mapping[str, Any], passage: str, head: str, title: str,
                  flags: Sequence[str] = ()) -> str:
    sym = rec.get("symbol") or ""
    notes = "".join(f"NOTE: {FLAG_NOTES[f]}\n" for f in FLAG_NOTES if f in flags)
    return (f"BEGINNING OF THE PAPER{' (title: ' + title + ')' if title else ''}:\n<<<\n{head}\n>>>\n\n"
            f"PASSAGE:\n<<<\n{passage}\n>>>\n\n"
            f"QUESTION: {rec['question']}\n"
            f"QUANTITY ASKED: {rec['quantity']}\n"
            f"ANSWER: {rec['value']}\n"
            f"SYMBOL: {sym if sym else '(none)'}\n"
            f"QUOTE: {rec['quote']}\n"
            f"{notes}\n"
            "Check the record. Answer with the JSON object only.")


def parse_verdict(text: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        obj = json.loads(_strip_fence(text))
    except ValueError as e:
        return None, f"json: {str(e)[:120]}"
    if not isinstance(obj, dict):
        return None, "not an object"
    bad = [k for k in VERIFY_KEYS if not isinstance(obj.get(k), bool)]
    if bad:
        return None, f"not boolean: {bad}"
    return {**{k: obj[k] for k in VERIFY_KEYS}, "reason": str(obj.get("reason", ""))[:400]}, None


# ------------------------------------------------------------------ layer 1

_DIGITS = re.compile(r"\d+(?:[.,]\d+)*")
_SCENARIO = re.compile(r"\b(case|scenario|condition|group|arm|cohort|setting|simulation|experiment|"
                       r"treatment|baseline|control)s?\b")
_WORD = re.compile(r"[^\W_]+")


def _words(text: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(canonical_text(text))]


def numbers_in(value: str) -> list[str]:
    return _DIGITS.findall(unicodedata.normalize("NFKC", value))


def question_prints_number(question: str, value: str) -> bool:
    q = unicodedata.normalize("NFKC", question)
    for num in numbers_in(value):
        if re.search(r"(?<![\d.,])" + re.escape(num) + r"(?![\d])", q):
            return True
    vw = {w for w in re.findall(r"[a-z]+", value.casefold()) if w in g.NUMBER_WORDS}
    qw = set(re.findall(r"[a-z]+", question.casefold()))
    return bool(vw & qw)


def leak_overlap(question: str, quote: str, frequent: set[str]) -> tuple[float, int]:
    """(share of the quote's distinctive tokens that the question contains, |distinctive|).
    Distinctive = quote tokens not among the paper's most frequent; 0.0 when none."""
    distinct = set(g.leak_tokens(quote)) - frequent
    if not distinct:
        return 0.0, 0
    return len(distinct & set(g.leak_tokens(question))) / len(distinct), len(distinct)


def symbol_values(symbol: str, texts: Sequence[str]) -> set[str]:
    """The distinct numbers the paper states for ``symbol`` (``sym = number``)."""
    key = canonical_symbol(symbol)
    if not key:
        return set()
    pat = re.compile(_NOT_TOKEN_BEFORE + re.escape(key) + _NOT_TOKEN_AFTER
                     + r"\s*(?:\([^()\n]{0,20}\))?\s*[=≈≃~∼]\s*([-−+]?\s*\.?\d[\d.,]*)")
    out: set[str] = set()
    for t in texts:
        for m in pat.finditer(canonical_text(t)):
            out.add(re.sub(r"\s+", "", m.group(1)).rstrip(".,").replace("−", "-"))
    return out


_DEF_STOP = frozenset("a an the of in on at to for with by from and or per as is are be its their".split())


def _jaccard(a: str, b: str) -> float:
    x = {w for w in _words(a) if w not in _DEF_STOP}
    y = {w for w in _words(b) if w not in _DEF_STOP}
    return len(x & y) / len(x | y) if x | y else 1.0


def symbol_collision(symbol: str, texts: Sequence[str], jaccard: float) -> bool:
    """Where detectable: the paper's own definition sentences (the library's
    deterministic patterns, applied to each document of the paper) give the
    symbol two definitions that share less than ``jaccard`` of their content
    words. A symbol the paper never defines in a recognisable sentence is not
    detectable and passes (layer 2's ``symbol_unique`` still judges it)."""
    key = canonical_symbol(symbol)
    defs: list[str] = []
    for t in texts:
        for e in extract_glossary(t).entries:
            if e.symbol == key:
                defs.extend(d.text for d in e.definitions)
    return any(_jaccard(a, b) < jaccard for i, a in enumerate(defs) for b in defs[i + 1:])


class PaperIndex:
    """Per-paper caches the checks need: normalised docs and chunks, word set,
    frequent tokens, and the collision verdict per symbol."""

    def __init__(self, corpus: g.Corpus, top_frequent: int) -> None:
        self.c = corpus
        self.top = top_frequent
        self._words: dict[str, set[str]] = {}
        self._freq: dict[str, set[str]] = {}
        self._coll: dict[tuple[str, str, float], bool] = {}

    def words(self, paper: str) -> set[str]:
        if paper not in self._words:
            self._words[paper] = {w for t in self.c.paper_texts(paper) for w in _words(t)}
        return self._words[paper]

    def frequent(self, paper: str) -> set[str]:
        if paper not in self._freq:
            cnt = self.c.token_counts(paper)
            self._freq[paper] = {t for t, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[: self.top]}
        return self._freq[paper]

    def collision(self, paper: str, symbol: str, jaccard: float) -> bool:
        k = (paper, canonical_symbol(symbol), jaccard)
        if k not in self._coll:
            self._coll[k] = symbol_collision(symbol, self.c.paper_texts(paper), jaccard)
        return self._coll[k]


def layer1(draft: Mapping[str, Any] | None, parse_error: str | None, *, stratum: str, paper: str,
           source_chunk: g.ChunkRow, idx: PaperIndex, cfg: Mapping[str, Any],
           field: fl.FieldSpec | None = None) -> dict[str, Any]:
    """The per-record mechanical checks. Returns ``{"verdict": "pass"|"reject",
    "reasons": [...], "flags": [...], "gold_chunk_ids": [...], "leak_overlap": x,
    "leak_distinctive": n, "leak_ok": bool, "record_stratum": s}``. ``field`` set =
    fields mode (see the module docstring)."""
    c = {**CHECK_DEFAULTS, **cfg}
    out: dict[str, Any] = {"reasons": [], "flags": [], "gold_chunk_ids": [], "leak_overlap": None,
                           "leak_distinctive": None, "leak_ok": None, "record_stratum": stratum}
    reasons: list[str] = out["reasons"]
    if draft is None:
        reasons.append("unparseable")
        out["parse_error"] = parse_error
        return {**out, "verdict": "reject"}
    if draft.get("none"):
        reasons.append("no_quantity")
        return {**out, "verdict": "reject"}
    q, quote, value = draft["question"].strip(), draft["quote"], draft["value"].strip()
    sym = draft["symbol"].strip()
    if not q:
        reasons.append("question_missing")
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
    # the value: printed in the quote, with a number; not in the question
    if not value or not g.has_number(value) or g.gold_norm(value) not in nq:
        reasons.append("quantity_not_in_quote")
    if not g.has_number(quote):
        reasons.append("quantity_not_in_quote")
    if q and value and question_prints_number(q, value):
        reasons.append("number_in_question")
    # the symbol
    texts = idx.c.paper_texts(paper)
    words_only = stratum == "S3" or (field is not None and not sym)
    if words_only:
        out["record_stratum"] = "S3"
    if words_only:
        if sym:
            reasons.append("s3_has_symbol")
        if g.value_statements(quote, c.get("vs_stoplist")):
            reasons.append("s3_value_statement")
    else:
        if not sym:
            reasons.append("symbol_missing")
        elif not symbol_occurs(sym, source_chunk.content):
            reasons.append("symbol_not_in_chunk")
    if sym and q and symbol_occurs(sym, q):
        reasons.append("symbol_in_question")
    if sym and stratum != "S3" and c["collision"] != "off" and idx.collision(paper, sym, float(c["collision_jaccard"])):
        (reasons if c["collision"] == "reject" else out["flags"]).append("symbol_collision")
    if sym and len(symbol_values(sym, texts)) > 1:
        out["flags"].append("multi_value")
    named = {m.group(1) for m in _SCENARIO.finditer(quote.casefold())}
    if named and not named & {m.group(1) for m in _SCENARIO.finditer(q.casefold())}:
        out["flags"].append("scenario_terms")
    if field is not None:
        # fields mode: the printed unit, the term-leak guard; binding terms are the field's
        u = fl.unit_check(value, field.unit)
        out["unit_check"] = u
        if u == "incompatible":
            reasons.append("unit_incompatible")
        elif u != "ok":
            out["flags"].append({"related": "unit_dimension_differs", "unknown": "unit_unknown",
                                 "field_unknown": "field_unit_unknown"}[u])
        if q:
            share, n = fl.term_leak(q, field.terms, field.definition, draft.get("case") or "")
            out["term_leak"], out["term_leak_words"] = round(share, 4), n
            if share > float(c["term_leak_threshold"]):
                reasons.append("term_leak")
    else:
        # binding terms
        terms = [t for t in draft["binding_terms"] if t]
        if (not terms or len(terms) > int(c["max_binding_terms"]) or len(terms) != len(draft["binding_terms"])
                or any(len(t.split()) > int(c["max_binding_words"]) for t in terms)):
            reasons.append("binding_terms_invalid")
        paper_words = idx.words(paper)
        if any(w not in paper_words for t in terms for w in _words(t)):
            reasons.append("binding_word_not_in_paper")
    # lexical leak
    if q and quote:
        ov, n = leak_overlap(q, quote, idx.frequent(paper))
        out["leak_overlap"], out["leak_distinctive"] = round(ov, 4), n
        out["leak_ok"] = ov <= float(c["leak_threshold"])
        if not out["leak_ok"]:
            reasons.append("lexical_leak")
    out["reasons"] = sorted(dict.fromkeys(reasons))
    out["flags"] = sorted(dict.fromkeys(out["flags"]))
    return {**out, "verdict": "reject" if out["reasons"] else "pass"}


def cross_record(records: Sequence[dict[str, Any]]) -> None:
    """In place, over the layer-1 passes in record order: ``duplicate_question``
    (same paper, same normalised question; the first is kept) and ``shared_chunk``
    (a flag on every kept record whose gold chunk another kept record shares)."""
    seen: set[tuple[str, str]] = set()
    kept: list[dict[str, Any]] = []
    for r in records:
        if r["l1"]["verdict"] != "pass":
            continue
        key = (r["paper"], g.gold_norm(r["draft"]["question"]))
        if key in seen:
            r["l1"]["reasons"] = sorted({*r["l1"]["reasons"], "duplicate_question"})
            r["l1"]["verdict"] = "reject"
            continue
        seen.add(key)
        kept.append(r)
    use = Counter(cid for r in kept for cid in set(r["l1"]["gold_chunk_ids"]))
    for r in kept:
        if any(use[cid] > 1 for cid in r["l1"]["gold_chunk_ids"]):
            r["l1"]["flags"] = sorted({*r["l1"]["flags"], "shared_chunk"})
