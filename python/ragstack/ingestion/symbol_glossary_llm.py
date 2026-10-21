"""The offline, document-level LLM symbol glossary (#754 fix A, owner decision
2026-10-21: rules AND an LLM pass).

Not an ingest step. Ingest and the GoWe workers have no LLM endpoint, and an
in-process model call is not reproducible (ADR-0011). Instead
``scripts/symbol_glossary_llm.py`` runs this over documents ahead of time and
writes each result to a :class:`~ragstack.ingestion.symbol_glossary.SymbolGlossaryCache`
keyed by ``sha256(document text)`` and :func:`llm_version` (``llm/1`` + model id
+ prompt hash). Ingest reads that cache, prefers a hit, and otherwise falls back
to the rules — so an ingest never waits on, or varies with, a model.

A model *proposes*; the document *disposes*. Every proposed entry passes
:func:`~ragstack.ingestion.symbol_glossary.validate_entries`: the symbol must
occur in the document and every word of the definition must too. The prompt asks
for the same thing, but the gate is what enforces it — the λv trap (a model that
"knows" λv is a ventilation rate, in a paper where it is the viral deactivation
rate) is exactly a model being confidently wrong from prior knowledge.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from ragstack.ingestion.symbol_glossary import (
    DEFAULT_CONFIG,
    Definition,
    Glossary,
    GlossaryConfig,
    GlossaryEntry,
    validate_entries,
)

log = logging.getLogger(__name__)

#: The LLM step's own version, the first component of :func:`llm_version`. Bump
#: on a change to parsing, windowing or validation; a prompt change is caught by
#: the prompt hash on its own.
LLM_EXTRACTOR = "llm/1"

SYSTEM_PROMPT = (
    "You build the symbol glossary of ONE scientific document. You copy from the "
    "document; you never use outside knowledge. Output JSON only."
)

USER_PROMPT = """List every mathematical symbol or variable that this document defines in its own words.

Rules:
- One JSON object per symbol and meaning. Never put two symbols in one object.
- Copy each symbol EXACTLY as its characters appear in the document text below (for example "Qb", "λv", "r¯", "Cq"), even if the extraction ran a subscript into the letter.
- The definition is a short noun phrase (at most 12 words) made ONLY of words that appear in the document. Prefer copying the document's own phrase verbatim.
- Resolve "respectively" lists: "a, b and c are the x, y and z, respectively" gives three objects, a = x, b = y, c = z.
- Never use your own knowledge of what a symbol usually means. If the document does not say what a symbol is, leave it out.
- If the document gives a symbol two different meanings, output one object per meaning, in the order they appear.
- Leave out units (m, mL, h, μm), plain numbers and abbreviations of words (RH, CET).

Answer with a JSON array and nothing else:
[{{"symbol": "...", "definition": "..."}}]

DOCUMENT:
<<<
{document}
>>>"""


def prompt_hash() -> str:
    """12 hex chars identifying the prompt (system + user template)."""
    h = hashlib.sha256()
    h.update(SYSTEM_PROMPT.encode("utf-8"))
    h.update(b"\x00")
    h.update(USER_PROMPT.encode("utf-8"))
    return h.hexdigest()[:12]


def llm_version(model: str) -> str:
    """The cache/provenance version of a glossary from ``model`` under this
    prompt: ``llm/1:<model>:<prompt hash>``."""
    return f"{LLM_EXTRACTOR}:{model}:{prompt_hash()}"


def windows(text: str, size: int, overlap: int, max_windows: int) -> tuple[list[str], bool]:
    """Split ``text`` into at most ``max_windows`` windows of ``size`` chars
    (``overlap`` shared). Returns ``(windows, truncated)``; ``truncated`` means
    the tail past the last window was not read."""
    if size <= 0 or len(text) <= size:
        return [text], False
    step = max(1, size - max(0, overlap))
    out: list[str] = []
    start = 0
    while len(out) < max(1, max_windows):
        out.append(text[start : start + size])
        if start + size >= len(text):
            return out, False
        start += step
    return out, True


# A backslash JSON does not allow ("\\lambda", "\\(") — a model copying LaTeX.
_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')
_OBJECT = re.compile(r"\{[^{}]*\}")


def _loads_lenient(raw: str) -> Any:
    """``json.loads``, then the same with stray backslashes escaped, then the
    first complete value only (a model that appends prose). ``None`` if all fail."""
    for candidate in (raw, _BAD_ESCAPE.sub(r"\\\\", raw)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        try:
            return json.JSONDecoder().raw_decode(candidate)[0]
        except json.JSONDecodeError:
            pass
    return None


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_response(text: str) -> list[tuple[str, str]]:
    """``[(symbol, definition)]`` from a model answer. Tolerates a code fence and
    prose around the array; anything unparseable yields ``[]`` (logged), never
    an exception — one bad answer must not fail a run over hundreds of
    documents."""
    body = _FENCE.sub("", text.strip())
    start, end = body.find("["), body.rfind("]")
    if start < 0 or end <= start:
        log.warning("LLM glossary answer had no JSON array")
        return []
    data = _loads_lenient(body[start : end + 1])
    if data is None:
        # Last resort: salvage the well-formed objects one by one.
        data = [d for d in (_loads_lenient(m.group(0)) for m in _OBJECT.finditer(body)) if d]
        if not data:
            log.warning("LLM glossary answer was not valid JSON")
            return []
    out: list[tuple[str, str]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        sym, defn = item.get("symbol"), item.get("definition")
        if isinstance(sym, str) and isinstance(defn, str):
            out.append((sym, defn))
    return out


class ChatLLM(Protocol):
    model: str

    async def complete_detailed(
        self, messages: list[dict[str, str]], max_tokens: int = ..., temperature: float = ...
    ) -> tuple[str, str]: ...


@dataclass
class LLMGlossaryStats:
    windows: int = 0
    truncated: bool = False
    proposed: int = 0
    rejected: int = 0
    cut_off: int = 0


async def extract_with_llm(
    llm: ChatLLM,
    text: str,
    *,
    config: GlossaryConfig | None = None,
    window_chars: int = 80_000,
    window_overlap: int = 2_000,
    max_windows: int = 4,
    max_tokens: int = 2_048,
) -> tuple[Glossary, LLMGlossaryStats]:
    """One document → a validated :class:`Glossary` stamped
    :func:`llm_version`. Temperature 0. A long document is read in windows and
    the proposals unioned before validation. Raises only on transport errors
    (the caller decides whether to retry or skip the document)."""
    cfg = config or DEFAULT_CONFIG
    parts, truncated = windows(text, window_chars, window_overlap, max_windows)
    stats = LLMGlossaryStats(windows=len(parts), truncated=truncated)
    proposed: list[tuple[str, str]] = []
    for part in parts:
        answer, finish = await llm.complete_detailed(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_PROMPT.format(document=part)},
            ],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        if finish == "length":
            stats.cut_off += 1
        proposed.extend(parse_response(answer))
    stats.proposed = len(proposed)
    glossary, stats.rejected = validate_entries(proposed, text, llm_version(llm.model), cfg)
    return glossary, stats


def merged(rules: Glossary, llm: Glossary, config: GlossaryConfig | None = None) -> Glossary:
    """Union of two glossaries of ONE document, every definition at its own
    offset (duplicates by text keep the earliest). For experiments; ingest does
    not merge — it prefers the cached LLM glossary."""
    cfg = config or DEFAULT_CONFIG
    per: dict[str, dict[str, Definition]] = {}
    for g in (llm, rules):
        for e in g.entries:
            bucket = per.setdefault(e.symbol, {})
            for d in e.definitions:
                k = d.text.casefold()
                if k not in bucket or d.offset < bucket[k].offset:
                    bucket[k] = d
    entries = tuple(
        GlossaryEntry(s, tuple(sorted(b.values(), key=lambda d: (d.offset, d.text)))[: cfg.max_definitions_per_symbol])
        for s, b in sorted(per.items())
    )
    return Glossary(entries, f"{llm.version}+{rules.version}")


def stats_dict(stats: LLMGlossaryStats) -> dict[str, Any]:
    return dict(stats.__dict__)
