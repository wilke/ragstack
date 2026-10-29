"""The offline, document-level LLM symbol glossary (#754 fix A, owner decision
2026-10-21: rules AND an LLM pass).

Two callers. ``scripts/symbol_glossary_llm.py`` runs :func:`extract_with_llm`
over documents ahead of time and writes each result to a
:class:`~ragstack.ingestion.symbol_glossary.SymbolGlossaryCache` keyed by
``sha256(document text)`` and :func:`llm_version`; ingest reads that cache,
prefers a hit, and otherwise falls back to the rules. The workflow step
(#847 G2, ``scripts/glossary_step.py`` via :mod:`ragstack.ingestion.glossary_step`)
runs :func:`extract_document` — bounded calls, the cut-off split, the repair
round, a record of every call — and writes a recorded ``glossary.jsonl``
instead: the model's output is an artifact later steps consume, never a value
recomputed on a rerun.

A model *proposes*; the document *disposes*. Every proposed entry passes
:func:`~ragstack.ingestion.symbol_glossary.validate_entries`: the symbol must
occur in the document and every word of the definition must too. The prompt asks
for the same thing, but the gate is what enforces it — the λv trap (a model that
"knows" λv is a ventilation rate, in a paper where it is the viral deactivation
rate) is exactly a model being confidently wrong from prior knowledge.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from ragstack.ingestion.symbol_glossary import (
    ANOTHER_SYMBOL_BETWEEN,
    DEFAULT_CONFIG,
    GATE_REASONS,
    IMPLAUSIBLE,
    NOT_LOCAL,
    SYMBOL_ABSENT,
    WORD_NOT_IN_DOCUMENT,
    Definition,
    Glossary,
    GlossaryConfig,
    GlossaryEntry,
    is_single_token_symbol,
    validate_entries,
    validate_entries_detailed,
)

log = logging.getLogger(__name__)

#: The LLM step's own version, the first component of :func:`llm_version`. Bump
#: on a change to parsing, windowing or validation; a prompt change is caught by
#: the prompt hash on its own. ``llm/2`` (#847 G2): the cut-off split retry, the
#: repair round, and the version string's ``:k<thinking>:r<repair>`` fields.
LLM_EXTRACTOR = "llm/2"

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


#: The repair round (#847 G2): entries the gate rejected go back to the model
#: ONCE per round, each with the gate's reason, together with the window they
#: were proposed from. The answer passes the same gate. Only REPAIRABLE
#: rejections are sent (:func:`repairable`): never ``not_specific`` (a unit, a
#: plain word, a single letter — no rewording makes it a symbol) or
#: ``not_a_symbol`` (a term of the document, not a symbol). The #850 review
#: dropped ``not_specific`` from the template's reason list, which changed the
#: prompt hash and so every ``llm/2`` version string.
REPAIR_PROMPT = """A checker compared your symbol list for this document with the document text and rejected the entries below. Each line gives the entry and the reason:
- symbol_absent: the symbol does not occur in the document exactly as written;
- implausible: the definition is not a short noun phrase (a value, a formula, a sentence);
- word_not_in_document: a word of the definition does not occur in the document;
- not_local: the definition's words do not occur near the symbol;
- another_symbol_between: the document's phrase belongs to a different symbol.

REJECTED:
{rejected}

Correct each entry the document supports: copy the symbol exactly as its characters appear in the document, and the definition from the document's own words near that symbol. Leave out every entry the document does not support. Never use your own knowledge.

Answer with a JSON array and nothing else:
[{{"symbol": "...", "definition": "..."}}]

DOCUMENT:
<<<
{document}
>>>"""


def prompt_hash() -> str:
    """12 hex chars identifying the prompts (system + user + repair template)."""
    h = hashlib.sha256()
    h.update(SYSTEM_PROMPT.encode("utf-8"))
    h.update(b"\x00")
    h.update(USER_PROMPT.encode("utf-8"))
    h.update(b"\x00")
    h.update(REPAIR_PROMPT.encode("utf-8"))
    return h.hexdigest()[:12]


DEFAULT_WINDOW_CHARS = 80_000
DEFAULT_MAX_WINDOWS = 4
DEFAULT_MAX_TOKENS = 2_048


#: Characters shared by consecutive windows. Part of the ``llm/2`` extractor
#: (not a knob): changing it is an extractor change.
WINDOW_OVERLAP = 2_000


def thinking_code(thinking: bool | None) -> str:
    """The version string's ``k`` field: ``0`` (thinking off was sent), ``1``
    (on was sent), ``u`` (nothing sent: the server's own default)."""
    return "u" if thinking is None else str(int(bool(thinking)))


#: Request fields the step itself sets; an extra body may not override them.
CORE_REQUEST_FIELDS = frozenset({"model", "messages", "max_tokens", "temperature", "seed"})
#: Key names an extra body is refused for, at any depth (camelCase split into
#: words, case-insensitive, ``-`` read as ``_``): the body is recorded verbatim
#: in every row and in the summary, so it must never carry a credential. The
#: names directly under a JSON-schema ``properties`` object are schema field
#: names, not credentials, and are not matched (their values still are).
_SECRET_KEYS = frozenset({"api_key", "apikey", "authorization", "auth", "token", "secret",
                          "password", "bearer", "credentials", "x_api_key", "key"})
_SECRET_SUFFIXES = ("_token", "_secret", "_password", "_api_key", "_apikey")


class ExtraBodyError(ValueError):
    """An ``--llm-extra-body`` the step refuses (exit 2, before any call)."""


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _secret_like(key: str) -> bool:
    """``accessToken`` / ``api-secret`` / ``Authorization`` / ``key`` → True."""
    k = _CAMEL.sub("_", key.strip()).lower().replace("-", "_")
    return k in _SECRET_KEYS or k.endswith(_SECRET_SUFFIXES)


def _secret_paths(obj: Any, path: str = "", *, schema_names: bool = False) -> list[str]:
    """Paths of credential-like keys in ``obj``. ``schema_names``: ``obj`` is
    a JSON-schema ``properties`` object, whose keys are field names (a
    ``response_format`` schema may well name a field ``token``) — skipped, but
    each value is still scanned."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else str(k)
            if not schema_names and _secret_like(str(k)):
                out.append(here)
            out += _secret_paths(v, here,
                                 schema_names=(not schema_names and k == "properties"
                                               and isinstance(v, dict)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _secret_paths(v, f"{path}[{i}]")
    return out


def parse_extra_body(raw: str) -> dict[str, Any] | None:
    """``--llm-extra-body``: a JSON object of extra top-level request fields
    for the chat-completions call (e.g. ``{"reasoning_effort": "low"}`` or a
    ``response_format``), or ``None`` for an empty flag. Refused: invalid
    JSON, anything but an object, a key the step sets itself
    (:data:`CORE_REQUEST_FIELDS`), and any key, at any depth, that looks like
    a credential — the body is recorded verbatim, so it must never carry one.
    An empty object is ``None`` (nothing is sent, nothing is recorded)."""
    if not raw.strip():
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ExtraBodyError(f"--llm-extra-body: bad JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ExtraBodyError(
            f"--llm-extra-body must be a JSON object, not {type(obj).__name__}")
    core = sorted(CORE_REQUEST_FIELDS & set(obj))
    if core:
        raise ExtraBodyError(
            f"--llm-extra-body may not set {core}: the step sets those itself "
            "(use --llm-model / --llm-max-tokens / --llm-seed)")
    secrets = _secret_paths(obj)
    if secrets:
        raise ExtraBodyError(
            f"--llm-extra-body may not carry credential-like keys {secrets}: it is recorded "
            "verbatim in every row and the summary. Pass the key via --llm-api-key-env")
    return obj or None


def merge_extra_body(extra: dict[str, Any] | None,
                     thinking: bool | None) -> dict[str, Any] | None:
    """The extra top-level fields every call sends: ``extra`` plus
    ``--llm-thinking``'s ``chat_template_kwargs.enable_thinking`` (when not
    ``unset``). Precedence: the core fields (model, messages, max_tokens,
    temperature, and a per-request seed) are the step's and an extra body may
    not name them (:func:`parse_extra_body`); ``chat_template_kwargs`` is
    merged key by key, and ``enable_thinking`` belongs to ``--llm-thinking``:
    an extra body that also sets it is refused unless the flag is ``unset``.
    ``None`` when nothing is to be sent."""
    out: dict[str, Any] = json.loads(json.dumps(extra)) if extra else {}
    ctk = out.get("chat_template_kwargs")
    if ctk is not None and not isinstance(ctk, dict):
        # Whatever --llm-thinking says: a server reads it as an object.
        raise ExtraBodyError("--llm-extra-body: chat_template_kwargs must be an object")
    if thinking is not None:
        if isinstance(ctk, dict) and "enable_thinking" in ctk:
            raise ExtraBodyError(
                "--llm-extra-body sets chat_template_kwargs.enable_thinking, which "
                "--llm-thinking owns: pass --llm-thinking unset to set it in the body")
        out["chat_template_kwargs"] = {**(ctk or {}), "enable_thinking": thinking}
    return out or None


def extra_body_hash(extra: dict[str, Any] | None) -> str | None:
    """8 hex chars of the canonical JSON of an extra body (``None`` for none):
    the ``+e`` suffix of the version string's ``k`` field."""
    if not extra:
        return None
    canon = json.dumps(extra, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:8]


def llm_version(
    model: str,
    window_chars: int = DEFAULT_WINDOW_CHARS,
    max_windows: int = DEFAULT_MAX_WINDOWS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    *,
    thinking: bool | None = False,
    repair_rounds: int = 0,
    extra_body: dict[str, Any] | None = None,
) -> str:
    """The cache/provenance version of a glossary from ``model`` under this
    prompt AND these generation limits:
    ``llm/2:<model>:<prompt hash>:w<window chars>x<max windows>:t<max tokens>:k<thinking 0|1|u>[+e<extra hash>]:r<repair rounds>``
    (``ku``: no thinking switch was sent; ``+e<8 hex>``: an ``--llm-extra-body``
    was sent, :func:`extra_body_hash` of it — absent without one, so every
    version written before the flag existed is unchanged).
    The limits change what the model sees and how much it may answer, thinking,
    the extra body and the repair round change what it answers, so a result
    produced under different values is a different result. (The cut-off retry
    policy is not in the string; the step records it per row.)"""
    eh = extra_body_hash(extra_body)
    return (f"{LLM_EXTRACTOR}:{model}:{prompt_hash()}"
            f":w{window_chars}x{max_windows}:t{max_tokens}"
            f":k{thinking_code(thinking)}{f'+e{eh}' if eh else ''}:r{int(repair_rounds)}")


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
    if start >= 0 and end <= start:
        # An array that never closes: an answer cut off at max_tokens. Its
        # complete objects are still proposals (llm/2); the gate decides.
        data: Any = [d for d in (_loads_lenient(m.group(0))
                                 for m in _OBJECT.finditer(body[start:])) if d]
        if not data:
            log.warning("LLM glossary answer had no complete JSON object")
            return []
        return _pairs(data)
    if start < 0:
        log.warning("LLM glossary answer had no JSON array")
        return []
    data = _loads_lenient(body[start : end + 1])
    if data is None:
        # Last resort: salvage the well-formed objects one by one.
        data = [d for d in (_loads_lenient(m.group(0)) for m in _OBJECT.finditer(body)) if d]
        if not data:
            log.warning("LLM glossary answer was not valid JSON")
            return []
    return _pairs(data)


def _pairs(data: Any) -> list[tuple[str, str]]:
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
    window_chars: int = DEFAULT_WINDOW_CHARS,
    window_overlap: int = WINDOW_OVERLAP,
    max_windows: int = DEFAULT_MAX_WINDOWS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    thinking: bool = False,
) -> tuple[Glossary, LLMGlossaryStats]:
    """One document → a validated :class:`Glossary` stamped
    :func:`llm_version`. Temperature 0. A long document is read in windows and
    the proposals unioned before validation. Raises only on transport errors
    (the caller decides whether to retry or skip the document).

    The offline cache path (``scripts/symbol_glossary_llm.py``): no cut-off
    retry, no repair round (``:r0``). ``thinking`` only names what the
    caller's client sends (``extra_body``) in the version; it sends nothing.
    The workflow step uses :func:`extract_document`."""
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
    glossary, stats.rejected = validate_entries(
        proposed, text,
        llm_version(llm.model, window_chars, max_windows, max_tokens, thinking=thinking), cfg
    )
    return glossary, stats


# --- the bounded workflow step's extractor (#847 G2) --------------------------

#: Call kinds in a call record.
FIRST, SPLIT, REPAIR = "first", "split", "repair"
#: Cut-off retry policies.
CUTOFF_OFF, CUTOFF_SPLIT = "off", "split"
CUTOFF_POLICIES = (CUTOFF_OFF, CUTOFF_SPLIT)
#: Degrade reasons a call can end a document's LLM half with. ``http_<code>``
#: (a 4xx other than 429, or 429 after the retries) and ``exception:<Class>``
#: are formed at run time.
ENDPOINT_UNAVAILABLE = "endpoint_unavailable"
TIMEOUT = "timeout"
BAD_RESPONSE = "bad_response"
#: Transport retries per call (429, 5xx, connect): the sleeps between attempts.
DEFAULT_BACKOFF = (2.0, 4.0)
#: Rejected entries sent back in one repair call, at most (bounds the prompt).
MAX_REPAIR_ENTRIES = 100


class ChatClient(Protocol):
    """What the step needs from a client: :meth:`ragstack.llm.OpenAILLM.complete_result`."""

    model: str

    async def complete_result(
        self, messages: list[dict[str, str]], max_tokens: int = ..., temperature: float = ...,
        *, timeout: float | None = ..., extra: dict[str, Any] | None = ...,
    ) -> Any: ...


@dataclass(frozen=True)
class StepLimits:
    """The step's generation limits, every one explicit (no defaults: #847
    owner decision — the tool refuses a zero before any call)."""

    window_chars: int
    max_windows: int
    max_tokens: int
    timeout_s: float
    #: ``True``/``False``: ``chat_template_kwargs.enable_thinking`` is sent so;
    #: ``None``: nothing is sent (a strict OpenAI-compatible server).
    thinking: bool | None
    #: Repair rounds. No measured gain yet: on the G2a live smoke one round
    #: recovered nothing (#850). Keep 0 unless a run shows otherwise.
    repair_rounds: int
    cutoff_retry: str
    seed: int | None = None
    backoff: tuple[float, ...] = DEFAULT_BACKOFF
    #: ``--llm-extra-body`` as given (validated by :func:`parse_extra_body`),
    #: WITHOUT the thinking key; ``None``: none. In the version (``+e``).
    extra_body: dict[str, Any] | None = field(default=None, hash=False)

    def version(self, model: str) -> str:
        return llm_version(model, self.window_chars, self.max_windows, self.max_tokens,
                           thinking=self.thinking, repair_rounds=self.repair_rounds,
                           extra_body=self.extra_body)

    def max_calls(self) -> int:
        """The most model calls one document can make: per window the first
        call, two split halves, and one repair call per round. Each call is
        attempted at most ``1 + len(backoff)`` times."""
        per_window = 1 + (2 if self.cutoff_retry == CUTOFF_SPLIT else 0) + self.repair_rounds
        return self.max_windows * per_window

    def max_attempts(self) -> int:
        return self.max_calls() * (1 + len(self.backoff))


@dataclass
class CallRecord:
    window: int
    kind: str
    status: str = "ok"
    finish: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    ms: int = 0
    retries: int = 0
    reasoning: bool = False

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class CallFailed(Exception):
    """A call ended the document's LLM half: ``reason`` is the degrade reason;
    ``records`` every call the document made, the failed one last."""

    def __init__(self, reason: str, records: list[CallRecord]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.records = records


@dataclass
class DocumentOutcome:
    """One document's LLM half."""

    glossary: Glossary
    windows: int
    truncated: bool
    proposed: int
    kept: int
    drops: dict[str, int]
    cut_off: int
    cut_off_unresolved: int
    repair_proposed: int
    records: list[CallRecord] = field(default_factory=list)
    #: Windows that ended with an unresolved cut-off (``off``: the first call
    #: was cut off; ``split``: a half was cut off again).
    windows_cut_off_unresolved: int = 0

    @property
    def retries(self) -> int:
        return sum(r.retries for r in self.records)


def _int_or_none(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


async def _call(
    llm: ChatClient, messages: list[dict[str, str]], limits: StepLimits,
    record: CallRecord, records: list[CallRecord],
) -> Any:
    """One call with the transport retries; appends ``record``. Raises
    :class:`CallFailed` with the degrade reason when the call cannot be
    completed. A cut-off (``finish_reason == "length"``) is NOT a failure,
    even with no content; an empty answer that was not cut off is."""
    records.append(record)
    extra = {"seed": limits.seed} if limits.seed is not None else None
    attempts = 1 + len(limits.backoff)
    reason = ENDPOINT_UNAVAILABLE
    for attempt in range(attempts):
        t0 = time.monotonic()
        try:
            res = await llm.complete_result(messages, limits.max_tokens, 0.0,
                                            timeout=limits.timeout_s, extra=extra)
        except httpx.ConnectTimeout:
            reason = ENDPOINT_UNAVAILABLE           # unreachable: retried
        except httpx.TimeoutException:
            record.ms += int((time.monotonic() - t0) * 1000)
            record.status = TIMEOUT                 # already cost timeout_s: not retried
            raise CallFailed(TIMEOUT, records) from None
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code == 429:
                reason = "http_429"
            elif code >= 500:
                reason = ENDPOINT_UNAVAILABLE
            else:
                record.ms += int((time.monotonic() - t0) * 1000)
                record.status = f"http_{code}"
                raise CallFailed(record.status, records) from None
        except httpx.TransportError:
            reason = ENDPOINT_UNAVAILABLE
        except ValueError:
            record.ms += int((time.monotonic() - t0) * 1000)
            record.status = BAD_RESPONSE
            raise CallFailed(BAD_RESPONSE, records) from None
        else:
            record.ms += int((time.monotonic() - t0) * 1000)
            record.finish = str(res.finish_reason or "")
            usage = res.usage or {}
            record.prompt_tokens = _int_or_none(usage.get("prompt_tokens"))
            record.completion_tokens = _int_or_none(usage.get("completion_tokens"))
            record.reasoning = bool(res.reasoning)
            if not res.content and record.finish != "length":
                # e.g. thinking left on: the answer went to reasoning_content
                # and content is null. Nothing to parse, and not a cut-off.
                record.status = BAD_RESPONSE
                raise CallFailed(BAD_RESPONSE, records)
            return res
        record.ms += int((time.monotonic() - t0) * 1000)
        if attempt < attempts - 1:
            record.retries += 1
            await asyncio.sleep(limits.backoff[attempt])
    record.status = reason
    raise CallFailed(reason, records)


def halves(part: str) -> tuple[str, str]:
    """``part`` split near its middle, at the nearest whitespace when there is
    one within a tenth of the length (so a word is not cut), else exactly."""
    mid = len(part) // 2
    reach = max(1, len(part) // 10)
    for d in range(reach + 1):
        for i in (mid - d, mid + d):
            if 0 < i < len(part) and part[i].isspace():
                return part[:i], part[i:]
    return part[:mid], part[mid:]


def _messages(prompt: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]


#: Gate reasons a repair round may fix (with :func:`repairable`'s proviso for
#: ``symbol_absent``).
REPAIRABLE_REASONS = frozenset({WORD_NOT_IN_DOCUMENT, NOT_LOCAL, ANOTHER_SYMBOL_BETWEEN,
                                IMPLAUSIBLE, SYMBOL_ABSENT})


def repairable(symbol: str, reason: str) -> bool:
    """Is a rejection worth sending back? ``word_not_in_document``,
    ``not_local``, ``another_symbol_between``, ``implausible``, and
    ``symbol_absent`` only for a single-token symbol (a model may have
    mis-copied ``Qb``; a phrase that is not in the document is no symbol to
    copy). Never ``not_specific`` or ``not_a_symbol``."""
    if reason not in REPAIRABLE_REASONS:
        return False
    return reason != SYMBOL_ABSENT or is_single_token_symbol(symbol)


def _repair_prompt(rejected: list[tuple[str, str, str]], part: str) -> str:
    lines = [f"- {json.dumps({'symbol': s, 'definition': d}, ensure_ascii=False)} — {why}"
             for s, d, why in rejected[:MAX_REPAIR_ENTRIES]]
    return REPAIR_PROMPT.format(rejected="\n".join(lines), document=part)


async def extract_document(
    llm: ChatClient, text: str, *, config: GlossaryConfig, limits: StepLimits,
) -> DocumentOutcome:
    """One document's LLM glossary under the step's bounds (#847 G2).

    Per window (at most ``max_windows``): the first call; when it is cut off
    (``finish_reason == "length"``) and the policy is ``split``, the window
    is asked again as two halves at the same ``max_tokens`` — a half cut off
    again is counted ``cut_off_unresolved``, never split further. Proposals
    of every answer (a cut-off answer's salvaged objects included) are
    unioned. Then up to ``repair_rounds`` rounds: per window, the entries of
    that window the gate rejected for a :func:`repairable` reason go back
    once with the reasons (a window with none makes no repair call); the
    answers join the proposals. The glossary is the gate over ALL proposals, so its
    ``drops`` count every rejected proposal by the gate's reason.

    Raises :class:`CallFailed` (carrying every call record) when a call
    cannot be completed; the caller degrades the document."""
    parts, truncated = windows(text, limits.window_chars, WINDOW_OVERLAP, limits.max_windows)
    version = limits.version(llm.model)
    records: list[CallRecord] = []
    by_window: list[list[tuple[str, str]]] = []
    cut_off = unresolved = windows_unresolved = 0
    for i, part in enumerate(parts):
        props: list[tuple[str, str]] = []
        res = await _call(llm, _messages(USER_PROMPT.format(document=part)), limits,
                          CallRecord(i, FIRST), records)
        props.extend(parse_response(res.content) if res.content else [])
        if res.finish_reason == "length":
            cut_off += 1
            before = unresolved
            if limits.cutoff_retry == CUTOFF_SPLIT:
                for half in halves(part):
                    r2 = await _call(llm, _messages(USER_PROMPT.format(document=half)), limits,
                                     CallRecord(i, SPLIT), records)
                    props.extend(parse_response(r2.content) if r2.content else [])
                    if r2.finish_reason == "length":
                        unresolved += 1
            else:
                unresolved += 1
            windows_unresolved += int(unresolved > before)
        by_window.append(props)
    repair_proposed = 0
    pending = [list(p) for p in by_window]  # what the next round may send back
    for _round in range(limits.repair_rounds):
        nxt: list[list[tuple[str, str]]] = [[] for _ in parts]
        for i, part in enumerate(parts):
            if not pending[i]:
                continue
            _, rejected = validate_entries_detailed(pending[i], text, version, config)
            uniq: list[tuple[str, str, str]] = []
            seen: set[tuple[str, str]] = set()
            for sym, defn, why in rejected:
                if not repairable(sym, why):
                    continue                    # no rewording fixes it: never sent
                if (sym, defn) not in seen:
                    seen.add((sym, defn))
                    uniq.append((sym, defn, why))
            if not uniq:
                continue
            res = await _call(llm, _messages(_repair_prompt(uniq, part)), limits,
                              CallRecord(i, REPAIR), records)
            answers = parse_response(res.content) if res.content else []
            repair_proposed += len(answers)
            by_window[i].extend(answers)
            nxt[i] = answers
        pending = nxt
        if not any(pending):
            break
    proposed = [p for props in by_window for p in props]
    glossary, rejected = validate_entries_detailed(proposed, text, version, config)
    drops = Counter(why for _, _, why in rejected)
    return DocumentOutcome(
        glossary=glossary, windows=len(parts), truncated=truncated,
        proposed=len(proposed), kept=len(proposed) - len(rejected),
        drops={k: drops[k] for k in GATE_REASONS if drops[k]},
        cut_off=cut_off, cut_off_unresolved=unresolved, repair_proposed=repair_proposed,
        records=records, windows_cut_off_unresolved=windows_unresolved,
    )


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
