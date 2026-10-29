#!/usr/bin/env python
"""The symbol glossary as a recorded workflow step (#847 G2): one shard of
pre-extracted documents in, one ``glossary.jsonl`` out
(:mod:`ragstack.ingestion.glossary_file`), plus a summary.

Reads the shard with the same ``JsonlLoader`` the embed and ingest steps
read, so each row's ``text_sha256`` is the key of the very text the chunks are
cut from. Every row has the rules glossary. With ``--llm on`` it also has the
LLM glossary from an OpenAI-compatible endpoint — bounded (a fixed number of
calls per document, a per-call timeout, transport retries, an optional step
deadline) and degrading per document: a document whose LLM half fails gets
the rules, ``llm: null`` and ``degraded: <reason>``, and the step still exits
0 (the counts and a WARNING report it). See
:mod:`ragstack.ingestion.glossary_step`.

**Every knob is a flag; nothing has a hidden default.** With ``--llm on``,
``--llm-endpoint``, ``--llm-model``, ``--llm-timeout-s``, ``--llm-max-tokens``,
``--llm-window-chars`` and ``--llm-max-windows`` must be given (non-zero) or
the step refuses with exit 2 before any call. With ``--llm off`` (the
default) none is needed and nothing touches the network.

**The environment is read for two things only**: the API key, from the ONE
variable ``--llm-api-key-env`` names (never a flag value: argv is recorded),
and the tools image's ``/opt/ragstack/RELEASE``. The HTTP client ignores proxy
and certificate environment variables (``trust_env=False``). No key, userinfo
or query string reaches a log, the summary or the file; the endpoint is
recorded as ``host:port``.

Exit codes: 0 = the file was written (some documents may be degraded; a
shard with no parseable document — blank, or malformed throughout — is
ZERO rows and exit 0: the ingest step decides that batch's fate);
2 = a configuration refusal, before any call; 3 = the shard is missing or
not UTF-8, or has a duplicate document id.

``--llm-repair-rounds`` > 0 has no measured gain yet: on the G2a live smoke
one round recovered nothing. It sends back only rejections a rewording can
fix (never ``not_specific`` or ``not_a_symbol``).

Usage::

    python scripts/glossary_step.py shard.jsonl --out glossary.jsonl \\
        --summary glossary-summary.json                      # rules only
    python scripts/glossary_step.py shard.jsonl --out glossary.jsonl --llm on \\
        --llm-endpoint http://MODEL-HOST:8000 --llm-model ORG/MODEL \\
        --llm-timeout-s 300 --llm-max-tokens 4096 --llm-window-chars 80000 \\
        --llm-max-windows 4 --llm-concurrency 16 --llm-thinking off \\
        --llm-repair-rounds 1 --llm-cutoff-retry split

``--llm-extra-body JSON`` (default none) sends extra top-level request fields
— another model's reasoning switch, a ``response_format`` — with every call.
It is recorded verbatim (row ``provenance.llm.extra_body``, summary
``settings.extra_body``) and its hash is in the version (``k0+e<8 hex>``), so
**it must never carry credentials**; credential-like keys are refused (exit 2).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

from ragstack.ingestion.glossary_file import (
    GlossaryFileError,
    config_from_header,
    file_sha256,
    read_glossary_file,
    write_glossary_file,
)
from ragstack.ingestion.glossary_step import StepSettings, run_step, settings_problems
from ragstack.ingestion.loaders import JsonlLoader, LoaderError
from ragstack.ingestion.symbol_glossary import DEFAULT_CONFIG, EXTRACTOR_VERSION, GlossaryConfig
from ragstack.ingestion.symbol_glossary_llm import (
    CUTOFF_OFF,
    CUTOFF_POLICIES,
    DEFAULT_BACKOFF,
    LLM_EXTRACTOR,
    ExtraBodyError,
    StepLimits,
    merge_extra_body,
    parse_extra_body,
)
from ragstack.llm import OpenAILLM
from ragstack.provenance import (
    add_provenance_arguments,
    provenance_from_args,
    ragstack_version,
)

log = logging.getLogger("glossary_step")

CONFIG_REFUSED_EXIT_CODE = 2
SHARD_UNREADABLE_EXIT_CODE = 3
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: Sleeps between a call's attempts (429, 5xx, connect): part of the step's
#: bound, not a knob. A module constant so the tests run without sleeping.
BACKOFF: tuple[float, ...] = DEFAULT_BACKOFF
#: ``--llm-thinking`` → ``StepLimits.thinking`` (``None``: send nothing).
_THINKING: dict[str, bool | None] = {"off": False, "on": True, "unset": None}


def _http_client(timeout_s: float, concurrency: int) -> httpx.AsyncClient:
    """The step's one HTTP client. ``trust_env=False``: no proxy or CA
    variable from the worker's environment reaches it (the #849 tool rule)."""
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout_s), trust_env=False,
                             limits=httpx.Limits(max_connections=max(4, concurrency * 2)))


def _producer(tool: dict[str, Any]) -> str:
    """The header's ``producer``: this script and its version — the tools
    image's (``RELEASE``, via ``tool``), else the installed distribution's.
    Not ``ragstack.__version__``: resolving that consults git and the
    environment, and this tool reads no environment (#849)."""
    version = tool.get("image_version") or ragstack_version() or "unknown"
    return f"scripts/glossary_step.py {version} (extractor {EXTRACTOR_VERSION}, {LLM_EXTRACTOR})"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("shard", help="one JSONL shard of pre-extracted documents (JsonlLoader)")
    p.add_argument("--out", required=True, help="the glossary.jsonl to write (atomically)")
    p.add_argument("--summary", default="", help="also write the step summary (JSON) here")
    p.add_argument("--config", default="",
                   help="GlossaryConfig overrides as a JSON object (strict: an unknown key "
                        "or a wrongly typed value refuses); the file header records it")
    p.add_argument("--llm", choices=["off", "on"], default="off",
                   help="off (default): rules only, no network. on: also the LLM glossary")
    p.add_argument("--llm-endpoint", default="", help="OpenAI-compatible base URL (with --llm on)")
    p.add_argument("--llm-model", default="", help="the served model id (with --llm on)")
    p.add_argument("--llm-api-key-env", default="OPENAI_API_KEY",
                   help="the NAME of the environment variable holding the key (never the "
                        "key). Unset or empty = no Authorization header")
    p.add_argument("--llm-concurrency", type=int, default=0,
                   help="documents in flight (0 = 1)")
    p.add_argument("--llm-timeout-s", type=float, default=0.0,
                   help="per-call timeout in seconds; required (> 0) with --llm on")
    p.add_argument("--llm-max-tokens", type=int, default=0,
                   help="max_tokens per call; required (> 0) with --llm on")
    p.add_argument("--llm-window-chars", type=int, default=0,
                   help="characters per window; required (> 0) with --llm on")
    p.add_argument("--llm-max-windows", type=int, default=0,
                   help="windows per document (the tail past them is not read and is "
                        "recorded as truncated); required (> 0) with --llm on")
    p.add_argument("--llm-thinking", choices=["off", "on", "unset"], default="off",
                   help="off/on send chat_template_kwargs.enable_thinking=false/true (vLLM); "
                        "unset sends no chat_template_kwargs (a strict OpenAI-compatible "
                        "server). Recorded as k0/k1/ku in the version")
    p.add_argument("--llm-repair-rounds", type=int, default=0,
                   help="rounds sending the gate's repairable rejections back with the "
                        "reasons (0 = none). No measured gain yet")
    p.add_argument("--llm-cutoff-retry", choices=list(CUTOFF_POLICIES), default=CUTOFF_OFF,
                   help="split: re-ask a window cut off at max_tokens as two halves, once")
    p.add_argument("--llm-deadline-s", type=float, default=0.0,
                   help="wall-clock bound on the whole step's model calls (0 = none beyond "
                        "the per-call bounds); documents not done by then degrade 'deadline'")
    p.add_argument("--llm-min-chars", type=int, default=0,
                   help="documents shorter than this skip the model (rules only, recorded "
                        "as skipped); 0 = every document")
    p.add_argument("--llm-seed", type=int, default=None,
                   help="send this seed with every call (omitted = no seed sent)")
    p.add_argument("--llm-extra-body", default="", metavar="JSON",
                   help="a JSON object of extra top-level fields sent with every call "
                        "(e.g. {\"reasoning_effort\": \"low\"}); default none. Recorded "
                        "verbatim in every row and the summary and hashed into the version "
                        "(k..+e<8 hex>), so it MUST NOT carry credentials: credential-like "
                        "keys (api_key, authorization, token, ...) are refused, as are the "
                        "fields the step sets (model, messages, max_tokens, temperature, "
                        "seed). chat_template_kwargs merges with --llm-thinking, which owns "
                        "enable_thinking (set it here only with --llm-thinking unset). "
                        "Needs --llm on")
    p.add_argument("--reuse", default="",
                   help="a prior glossary.jsonl of this step: rows whose text_sha256 and LLM "
                        "version match, that were not degraded, and whose recorded cut-off "
                        "policy and seed equal this run's keep their LLM half; only the "
                        "other documents call the model. Needs --llm on")
    add_provenance_arguments(p)
    return p.parse_args(argv)


def _refuse(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr, flush=True)
    return CONFIG_REFUSED_EXIT_CODE


def _config(raw: str) -> GlossaryConfig:
    if not raw.strip():
        return DEFAULT_CONFIG
    try:
        return config_from_header(json.loads(raw), "--config")
    except json.JSONDecodeError as e:
        raise GlossaryFileError(f"--config: bad JSON: {e}") from e


def _settings(args: argparse.Namespace, extra_body: dict[str, Any] | None = None) -> StepSettings:
    llm = args.llm == "on"
    limits = StepLimits(
        window_chars=args.llm_window_chars, max_windows=args.llm_max_windows,
        max_tokens=args.llm_max_tokens, timeout_s=args.llm_timeout_s,
        thinking=_THINKING[args.llm_thinking], repair_rounds=args.llm_repair_rounds,
        cutoff_retry=args.llm_cutoff_retry, seed=args.llm_seed, backoff=BACKOFF,
        extra_body=extra_body,
    ) if llm else None
    return StepSettings(llm=llm, endpoint=args.llm_endpoint, model=args.llm_model,
                        api_key_env=args.llm_api_key_env if llm else "",
                        concurrency=max(1, args.llm_concurrency), deadline_s=args.llm_deadline_s,
                        min_chars=args.llm_min_chars, limits=limits)


def _load(shard: str) -> list[Any]:
    """The shard's documents. A blank shard (the extract stage skipped every
    file of the batch) and one with no usable document are ZERO documents —
    the ingest step decides that batch's fate, not this one."""
    path = Path(shard)
    if not path.is_file():
        raise OSError(f"{shard}: no such file")
    with path.open(encoding="utf-8") as fh:
        if not any(line.strip() for line in fh):
            return []
    try:
        return JsonlLoader().load(shard)
    except LoaderError:
        return []


async def amain(args: argparse.Namespace) -> int:
    problems: list[str] = []
    extra_body: dict[str, Any] | None = None
    request_extra: dict[str, Any] | None = None
    try:
        extra_body = parse_extra_body(args.llm_extra_body)
        request_extra = merge_extra_body(extra_body, _THINKING[args.llm_thinking])
    except ExtraBodyError as e:
        problems.append(str(e))
        extra_body = None
    if args.llm_extra_body.strip() and args.llm != "on":
        problems.append("--llm-extra-body needs --llm on")
    settings = _settings(args, extra_body)
    problems += settings_problems(settings)
    if args.reuse and not settings.llm:
        problems.append("--reuse needs --llm on (it reuses LLM halves)")
    if settings.llm and not _ENV_NAME.fullmatch(args.llm_api_key_env or ""):
        problems.append("--llm-api-key-env must be an environment variable NAME")
    try:
        config = _config(args.config)
    except GlossaryFileError as e:
        problems.append(str(e))
        config = DEFAULT_CONFIG
    reuse = None
    reuse_sha = None
    if args.reuse and not problems:
        try:
            reuse = read_glossary_file(args.reuse)
            reuse_sha = file_sha256(args.reuse)
        except (GlossaryFileError, OSError) as e:
            problems.append(f"--reuse: {e}")
        else:
            if reuse.config != config:
                problems.append("--reuse: the prior file was built under a different config")
    if problems:
        for msg in problems:
            _refuse(msg)
        return CONFIG_REFUSED_EXIT_CODE
    try:
        docs = _load(args.shard)
    except (OSError, UnicodeDecodeError) as e:
        print(f"error: shard unreadable: {e}", file=sys.stderr, flush=True)
        return SHARD_UNREADABLE_EXIT_CODE
    ids = [d.id for d in docs]
    if len(set(ids)) != len(ids):
        print("error: shard unreadable: duplicate document ids", file=sys.stderr, flush=True)
        return SHARD_UNREADABLE_EXIT_CODE
    tool = provenance_from_args(args)
    if settings.llm:
        assert settings.limits is not None
        api_key = os.environ.get(args.llm_api_key_env) or None
        # --llm-extra-body merged with --llm-thinking (merge_extra_body states
        # the precedence); None sends nothing extra.
        extra = request_extra
        async with _http_client(settings.limits.timeout_s, settings.concurrency) as http:
            client = OpenAILLM(settings.endpoint, settings.model, http, api_key=api_key,
                               extra_body=extra)
            rows, summary = await run_step(docs, settings, config, llm=client, http=http,
                                           api_key=api_key, reuse=reuse, reuse_sha256=reuse_sha,
                                           tool=tool)
    else:
        rows, summary = await run_step(docs, settings, config, tool=tool)
    try:
        counts = write_glossary_file(args.out, rows, producer=_producer(tool), config=config)
    except GlossaryFileError as e:
        print(f"error: {e}", file=sys.stderr, flush=True)
        return 1
    summary.update(out=str(args.out), counts=counts, provenance=tool)
    if args.summary:
        Path(args.summary).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                      encoding="utf-8")
    if summary["degraded"]:
        log.warning("glossary step: %d of %d document(s) DEGRADED to the rules glossary "
                    "(%s) — endpoint %s", summary["degraded"], summary["documents"],
                    ", ".join(f"{k}={v}" for k, v in summary["degraded_reasons"].items()),
                    summary["settings"].get("endpoint_name"))
    print(json.dumps({"out": str(args.out), "counts": counts,
                      "degraded_reasons": summary["degraded_reasons"],
                      "llm_calls": summary["llm_calls"]}, sort_keys=True), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(amain(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
