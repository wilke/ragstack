"""The glossary workflow step's driver (#847 G2): documents in, glossary-file
rows out — the rules glossary always, the LLM glossary when the step is asked
for it, bounded and degrading per document.

Pure of argv and the environment: ``scripts/glossary_step.py`` parses the
flags, reads the key from the ONE environment variable it is told to, builds
the HTTP client and hands everything here. Tests drive this module with an
``httpx.MockTransport`` that speaks the OpenAI-compatible contract.

**Bounded.** One ``GET /v1/models`` probe first: an endpoint that cannot be
reached (connect error, timeout, 5xx after the retries) degrades EVERY
document at once rather than waiting out every call's timeout. Then per
document at most :meth:`StepLimits.max_calls` calls, each at most
``1 + len(backoff)`` attempts of at most ``timeout_s``; ``concurrency``
documents in flight; and ``deadline_s`` (0 = none) bounds the whole step —
a document not finished by then is degraded ``deadline``.

**Degrade, never fail.** A document whose LLM half cannot be completed gets
``llm: null``, ``merged = rules`` and ``degraded: <reason>``
(``endpoint_unavailable``, ``timeout``, ``http_<code>``, ``bad_response``,
``deadline``, ``model_not_served``, ``exception:<Class>``). The step still
returns every row; the caller exits 0 and reports the counts. A model answer
the gate keeps nothing of is NOT degraded: ``llm`` is then an empty glossary.

**Reuse.** ``reuse`` (a prior file of this step) supplies the LLM half of a
document whose ``text_sha256`` and LLM version match and which was not
degraded; the rules half is always recomputed. Only the other documents call
the model — "a later run fills the LLM half" is one rerun with ``reuse``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from ragstack.ingestion.glossary_file import (
    GlossaryFile,
    GlossaryRow,
    choose_merged,
    masked_key,
)
from ragstack.ingestion.symbol_glossary import (
    EXTRACTOR_VERSION,
    GlossaryConfig,
    content_key,
    extract_glossary,
)
from ragstack.ingestion.symbol_glossary_llm import (
    CUTOFF_POLICIES,
    ENDPOINT_UNAVAILABLE,
    LLM_EXTRACTOR,
    TIMEOUT,
    CallFailed,
    ChatClient,
    StepLimits,
    extract_document,
)

log = logging.getLogger(__name__)

DEADLINE = "deadline"
MODEL_NOT_SERVED = "model_not_served"
SKIPPED_SHORT = "short"


def endpoint_name(url: str) -> str:
    """``host:port`` of ``url`` — what provenance records. Never the userinfo,
    the path or the query (a key in a URL must not reach a file)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return f"{host}:{port}"


@dataclass(frozen=True)
class StepSettings:
    """What the step was asked to do. ``limits`` is ``None`` with the LLM off."""

    llm: bool
    endpoint: str = ""
    model: str = ""
    api_key_env: str = ""
    concurrency: int = 1
    deadline_s: float = 0.0
    min_chars: int = 0
    limits: StepLimits | None = None

    def record(self) -> dict[str, Any]:
        """The settings as provenance records them: no key, no URL beyond host:port."""
        rec: dict[str, Any] = {"llm": self.llm}
        if self.llm and self.limits is not None:
            lim = self.limits
            rec.update(
                model=self.model, endpoint_name=endpoint_name(self.endpoint),
                api_key_env=self.api_key_env, concurrency=self.concurrency,
                deadline_s=self.deadline_s, min_chars=self.min_chars,
                timeout_s=lim.timeout_s, max_tokens=lim.max_tokens,
                window_chars=lim.window_chars, max_windows=lim.max_windows,
                thinking=lim.thinking, repair_rounds=lim.repair_rounds,
                cutoff_retry=lim.cutoff_retry, seed=lim.seed,
                version=lim.version(self.model),
            )
        return rec


def settings_problems(s: StepSettings) -> list[str]:
    """Why the settings cannot run — the tool's exit-2 refusal, before any
    call. With the LLM on, every generation limit must be given (no hidden
    defaults: #847 owner decision)."""
    if not s.llm:
        return []
    out: list[str] = []
    if not s.endpoint:
        out.append("--llm-endpoint is required with --llm on")
    elif urlsplit(s.endpoint).scheme not in ("http", "https") or not urlsplit(s.endpoint).hostname:
        out.append("--llm-endpoint must be an http(s) URL")
    elif urlsplit(s.endpoint).username or urlsplit(s.endpoint).password or urlsplit(s.endpoint).query:
        out.append("--llm-endpoint must not carry credentials or a query string "
                   "(pass the key through --llm-api-key-env)")
    if not s.model:
        out.append("--llm-model is required with --llm on")
    if s.concurrency < 0:
        out.append("--llm-concurrency must be >= 0")
    if s.deadline_s < 0:
        out.append("--llm-deadline-s must be >= 0")
    if s.min_chars < 0:
        out.append("--llm-min-chars must be >= 0")
    lim = s.limits
    if lim is None:
        out.append("internal: no limits with --llm on")
        return out
    for flag, value in (("--llm-timeout-s", lim.timeout_s), ("--llm-max-tokens", lim.max_tokens),
                        ("--llm-window-chars", lim.window_chars),
                        ("--llm-max-windows", lim.max_windows)):
        if not value or value <= 0:
            out.append(f"{flag} must be > 0 with --llm on (there is no default)")
    if lim.repair_rounds < 0:
        out.append("--llm-repair-rounds must be >= 0")
    if lim.cutoff_retry not in CUTOFF_POLICIES:
        out.append(f"--llm-cutoff-retry must be one of {list(CUTOFF_POLICIES)}")
    return out


@dataclass
class Probe:
    """``GET /v1/models``: whether the step should call the model at all."""

    reason: str | None = None          # a degrade reason for every document, or None
    status: str = "ok"                 # ok | http_<code> | <exception class>
    served: dict[str, Any] | None = None

    def record(self) -> dict[str, Any]:
        return {"status": self.status, "degrades_all": self.reason, "served": self.served}


async def probe(http: httpx.AsyncClient, s: StepSettings, api_key: str | None) -> Probe:
    """One models probe with the same transport retries as a call. Unreachable
    (connect error, timeout, 5xx after the retries) → every document
    ``endpoint_unavailable``; 401/403 → ``http_<code>``; a listing that lacks
    the model → ``model_not_served``. A server without the route (404) is no
    reason to stop: the calls decide."""
    assert s.limits is not None
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{s.endpoint.rstrip('/')}/v1/models"
    status = ENDPOINT_UNAVAILABLE
    backoff = s.limits.backoff
    for attempt in range(1 + len(backoff)):
        try:
            r = await http.get(url, headers=headers, timeout=s.limits.timeout_s)
        except httpx.TimeoutException:
            status = TIMEOUT
        except httpx.TransportError as e:
            status = type(e).__name__
        else:
            if r.status_code < 400:
                return _served(r, s.model)
            status = f"http_{r.status_code}"
            if r.status_code in (401, 403):
                return Probe(status, status)
            if r.status_code < 500 and r.status_code != 429:
                return Probe(None, status)
        if attempt < len(backoff):
            await asyncio.sleep(backoff[attempt])
    return Probe(ENDPOINT_UNAVAILABLE, status)


def _served(r: httpx.Response, model: str) -> Probe:
    try:
        data = r.json()
        listed = [m for m in (data.get("data") or []) if isinstance(m, dict)]
    except (ValueError, AttributeError):
        return Probe(None, "unparseable")
    match = next((m for m in listed if m.get("id") == model), None)
    if match is None:
        return Probe(MODEL_NOT_SERVED, "ok",
                     {"listed": sorted(str(m.get("id")) for m in listed)[:20]})
    keep = ("id", "root", "owned_by", "max_model_len", "revision", "created")
    return Probe(None, "ok", {k: match[k] for k in keep if k in match})


@dataclass
class _Doc:
    doc_id: str
    text: str


async def run_step(
    docs: Sequence[Any],
    settings: StepSettings,
    config: GlossaryConfig,
    *,
    llm: ChatClient | None = None,
    http: httpx.AsyncClient | None = None,
    api_key: str | None = None,
    reuse: GlossaryFile | None = None,
    reuse_sha256: str | None = None,
    tool: dict[str, Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[GlossaryRow], dict[str, Any]]:
    """Rows for ``docs`` (``Document``-like: ``id`` + ``content``), in input
    order, and the step summary. ``llm`` (an ``OpenAILLM``) and ``http`` (the
    client it posts with, used for the probe) are required with the LLM on."""
    t_start = clock()
    deadline = t_start + settings.deadline_s if settings.deadline_s > 0 else None
    tool = dict(tool or {})
    items = [_Doc(d.id, d.content) for d in docs]
    lim = settings.limits
    version = lim.version(settings.model) if settings.llm and lim is not None else None
    probe_rec: Probe | None = None
    if settings.llm:
        if llm is None or http is None or lim is None:
            raise ValueError("run_step: the LLM is on but no client was given")
        probe_rec = await probe(http, settings, api_key)
        if probe_rec.reason:
            log.warning("glossary step: the model endpoint %s is not usable (%s); every "
                        "document gets the rules glossary, degraded %s",
                        endpoint_name(settings.endpoint), probe_rec.status, probe_rec.reason)
    sem = asyncio.Semaphore(max(1, settings.concurrency))
    reused_ids: set[str] = set()

    def base_prov() -> dict[str, Any]:
        return {"rules": {"version": EXTRACTOR_VERSION}, "llm": None, "reused_from": None,
                "tool": tool}

    def llm_prov() -> dict[str, Any]:
        assert lim is not None
        return {"version": version, "model": settings.model,
                "endpoint_name": endpoint_name(settings.endpoint),
                "served": probe_rec.served if probe_rec else None,
                "thinking": lim.thinking, "repair_rounds": lim.repair_rounds,
                "cutoff_retry": lim.cutoff_retry, "seed": lim.seed,
                "windows": 0, "truncated": False, "calls": [], "proposed": 0, "kept": 0,
                "repair_proposed": 0, "cut_off": 0, "cut_off_unresolved": 0, "retries": 0,
                "reasoning_seen": 0, "ms": 0, "skipped": None, "degraded_reason": None}

    async def one(doc: _Doc) -> GlossaryRow:
        rules = extract_glossary(doc.text, config)
        key = content_key(doc.text)
        prov = base_prov()

        def row(llm_g: Any, drops: dict[str, int], degraded: str | None) -> GlossaryRow:
            return GlossaryRow(doc.doc_id, key, len(doc.text), masked_key(doc.text), rules,
                               llm_g, choose_merged(rules, llm_g), drops, prov, degraded)

        if not settings.llm:
            return row(None, {}, None)
        assert lim is not None and llm is not None
        prior = reuse.rows.get(doc.doc_id) if reuse is not None else None
        if (prior is not None and prior.text_sha256 == key and prior.degraded is None
                and prior.llm is not None and prior.llm.version == version):
            reused_ids.add(doc.doc_id)
            prov = {**dict(prior.provenance), "reused_from": reuse_sha256, "tool": tool,
                    "rules": {"version": EXTRACTOR_VERSION}}
            return row(prior.llm, dict(prior.drops), None)
        lp = llm_prov()
        prov["llm"] = lp
        if len(doc.text) < settings.min_chars:
            lp["skipped"] = SKIPPED_SHORT
            return row(None, {}, None)
        if probe_rec is not None and probe_rec.reason:
            lp["degraded_reason"] = probe_rec.reason
            return row(None, {}, probe_rec.reason)
        t0 = clock()
        try:
            async with sem:
                if deadline is not None and clock() >= deadline:
                    raise TimeoutError
                remaining = None if deadline is None else max(0.0, deadline - clock())
                async with asyncio.timeout(remaining):
                    out = await extract_document(llm, doc.text, config=config, limits=lim)
        except CallFailed as e:
            lp.update(calls=[r.as_dict() for r in e.records], degraded_reason=e.reason,
                      retries=sum(r.retries for r in e.records),
                      reasoning_seen=sum(int(r.reasoning) for r in e.records),
                      ms=int((clock() - t0) * 1000))
            return row(None, {}, e.reason)
        except TimeoutError:
            lp.update(degraded_reason=DEADLINE, ms=int((clock() - t0) * 1000))
            return row(None, {}, DEADLINE)
        except Exception as e:  # noqa: BLE001 — one document never sinks the batch
            reason = f"exception:{type(e).__name__}"
            log.warning("glossary step: %s degraded (%s)", doc.doc_id, reason)
            lp.update(degraded_reason=reason, ms=int((clock() - t0) * 1000))
            return row(None, {}, reason)
        lp.update(windows=out.windows, truncated=out.truncated,
                  calls=[r.as_dict() for r in out.records], proposed=out.proposed,
                  kept=out.kept, repair_proposed=out.repair_proposed, cut_off=out.cut_off,
                  cut_off_unresolved=out.cut_off_unresolved, retries=out.retries,
                  reasoning_seen=sum(int(r.reasoning) for r in out.records),
                  ms=int((clock() - t0) * 1000))
        return row(out.glossary, dict(out.drops), None)

    rows = list(await asyncio.gather(*(one(d) for d in items)))
    return rows, _summary(rows, settings, probe_rec, reused_ids, clock() - t_start)


def _summary(rows: list[GlossaryRow], s: StepSettings, probe_rec: Probe | None,
             reused: set[str], seconds: float) -> dict[str, Any]:
    reasons: Counter[str] = Counter(r.degraded for r in rows if r.degraded)
    drops: Counter[str] = Counter()
    tallies: Counter[str] = Counter()
    for r in rows:
        drops.update(r.drops)
        lp = r.provenance.get("llm") if r.doc_id not in reused else None
        if isinstance(lp, dict):
            tallies["llm_calls"] += len(lp.get("calls") or [])
            for k in ("proposed", "kept", "repair_proposed", "cut_off",
                      "cut_off_unresolved", "retries", "reasoning_seen"):
                tallies[k] += int(lp.get(k) or 0)
            tallies["skipped_short"] += int(lp.get("skipped") == SKIPPED_SHORT)
            tallies["truncated"] += int(bool(lp.get("truncated")))
    return {
        "documents": len(rows),
        "with_llm": sum(1 for r in rows if r.llm is not None),
        "degraded": sum(reasons.values()),
        "degraded_reasons": dict(sorted(reasons.items())),
        "reused": len(reused),
        "merged_entries": sum(len(r.merged.entries) for r in rows),
        "drops": dict(sorted(drops.items())),
        **{k: tallies[k] for k in ("llm_calls", "proposed", "kept", "repair_proposed",
                                   "cut_off", "cut_off_unresolved", "retries",
                                   "reasoning_seen", "skipped_short", "truncated")},
        "settings": s.record(),
        "probe": probe_rec.record() if probe_rec is not None else None,
        "extractors": {"rules": EXTRACTOR_VERSION, "llm": LLM_EXTRACTOR},
        "seconds": round(seconds, 3),
    }
