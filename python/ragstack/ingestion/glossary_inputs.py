"""The glossary step's workflow inputs, from this API's settings (#847 G2b).

With ``SYMBOL_GLOSSARY_EXTRACTION=llm`` on ``INGEST_BACKEND=gowe`` the API
sends ``glossary_llm: "on"`` and the step's endpoint, model, key NAME and
limits on every submission (``documents.py::_gowe_inputs``). The workflow
carries no policy (#849): every limit is a workflow input defaulting to 0,
which the tool refuses with ``--llm on`` — so the API owns the defaults
below, and ``SYMBOL_GLOSSARY_LLM_INPUTS_JSON`` overrides them per tenant.

The JSON's keys ARE the CWL input names, so the boot check is set
membership against what the registered text declares. Pure: no I/O, no
settings import — the caller passes the settings object.

The key never travels: ``glossary_llm_api_key_env`` names the environment
variable the worker injects from its ``--secret-file``. Workflow inputs are
an immutable, UI-rendered snapshot (``submitted_inputs``).
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from ragstack.ingestion.symbol_glossary_llm import (
    WINDOW_OVERLAP,
    ExtraBodyError,
    parse_extra_body,
)

#: The step's limits when SYMBOL_GLOSSARY_LLM_INPUTS_JSON says nothing. Measured
#: on a shared vLLM server (#847 comment, 2026-10-28): 16 in flight is the
#: shared-server default, 32 the ceiling; ~3-4% of calls hit 4096 tokens, so
#: cut-offs are split once. Repair rounds 0: no measured gain yet.
GLOSSARY_LLM_DEFAULTS: dict[str, Any] = {
    "glossary_llm_concurrency": 16,
    "glossary_llm_timeout_s": 300.0,
    "glossary_llm_max_tokens": 4096,
    "glossary_llm_window_chars": 80000,
    "glossary_llm_max_windows": 4,
    "glossary_llm_thinking": "off",
    "glossary_llm_repair_rounds": 0,
    "glossary_llm_cutoff_retry": "split",
}

#: Every knob SYMBOL_GLOSSARY_LLM_INPUTS_JSON may set, and its type.
KNOB_TYPES: dict[str, type] = {
    "glossary_llm_concurrency": int,
    "glossary_llm_timeout_s": float,
    "glossary_llm_max_tokens": int,
    "glossary_llm_window_chars": int,
    "glossary_llm_max_windows": int,
    "glossary_llm_thinking": str,
    "glossary_llm_repair_rounds": int,
    "glossary_llm_cutoff_retry": str,
    "glossary_llm_deadline_s": float,
    "glossary_llm_min_chars": int,
    "glossary_llm_seed": int,
    "glossary_llm_extra_body": str,
}

#: String knobs with a closed set of values (the tool's argparse choices).
KNOB_CHOICES: dict[str, tuple[str, ...]] = {
    "glossary_llm_thinking": ("off", "on", "unset"),
    "glossary_llm_cutoff_retry": ("off", "split"),
}

#: Inputs the API derives from dedicated settings: refused in the knob JSON
#: (two sources for one value is config believed live while inert).
OWNED_KEYS = frozenset({
    "glossary_llm", "glossary_llm_endpoint", "glossary_llm_model", "glossary_llm_api_key_env",
})

#: Every ``glossary_llm*`` input: none may be set in GOWE_WORKFLOW_INPUTS_JSON
#: (the per-run inputs win the merge, so it would be inert).
ALL_GLOSSARY_INPUTS = OWNED_KEYS | frozenset(KNOB_TYPES)

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: Knobs that must be > 0 for the tool to run with ``--llm on``.
_REQUIRED_POSITIVE = ("glossary_llm_timeout_s", "glossary_llm_max_tokens",
                      "glossary_llm_window_chars", "glossary_llm_max_windows")


def in_process_glossary_mode(mode: str) -> str:
    """The mode the IN-PROCESS attacher runs for ``SYMBOL_GLOSSARY_EXTRACTION``:
    ``llm`` is ``rules`` (the model step is a workflow step only), anything
    else unchanged (``attacher_from_mode`` then decides off/rules/unknown).
    ``documents.gowe_symbol_glossary`` reads the same way, so both ingest
    paths agree."""
    return "rules" if mode == "llm" else mode


class GlossaryInputsError(ValueError):
    """SYMBOL_GLOSSARY_LLM_* cannot drive the registered workflow."""


def _knob_json(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as e:
        raise GlossaryInputsError(f"SYMBOL_GLOSSARY_LLM_INPUTS_JSON is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise GlossaryInputsError("SYMBOL_GLOSSARY_LLM_INPUTS_JSON must be a JSON object")
    return data


def _typed(key: str, value: Any) -> str | None:
    """Why ``value`` is not a valid ``key``, or None."""
    want = KNOB_TYPES[key]
    if want is float:
        ok = isinstance(value, int | float) and not isinstance(value, bool)
    else:
        ok = isinstance(value, want) and not isinstance(value, bool)
    if not ok:
        return f"{key} must be {want.__name__}, got {type(value).__name__}"
    if want in (int, float) and value < 0:
        return f"{key} must be >= 0"
    if key in KNOB_CHOICES and value not in KNOB_CHOICES[key]:
        return f"{key} must be one of {list(KNOB_CHOICES[key])}"
    if key == "glossary_llm_extra_body":
        try:
            parse_extra_body(value)
        except ExtraBodyError as e:
            return f"{key}: {e}"
    return None


def glossary_settings_problems(settings: Any, declared: set[str]) -> list[str]:
    """Why ``SYMBOL_GLOSSARY_EXTRACTION=llm`` cannot drive a workflow that
    declares ``declared`` — the boot refusal (``backends._make_gowe_backend``).
    Empty when the mode is not ``llm``."""
    if getattr(settings, "symbol_glossary_extraction", "") != "llm":
        return []
    out: list[str] = []
    if "glossary_llm" not in declared:
        out.append("the registered workflow declares no `glossary_llm` input, so the model "
                   "step cannot be switched on (llm would be inert)")
    endpoint = str(getattr(settings, "symbol_glossary_llm_endpoint", "") or "")
    parts = urlsplit(endpoint)
    if not endpoint:
        out.append("SYMBOL_GLOSSARY_LLM_ENDPOINT is required (there is no fallback to LLM_ENDPOINT)")
    elif parts.scheme not in ("http", "https") or not parts.hostname:
        out.append("SYMBOL_GLOSSARY_LLM_ENDPOINT must be an http(s) URL")
    elif parts.username or parts.password or parts.query:
        out.append("SYMBOL_GLOSSARY_LLM_ENDPOINT must not carry credentials or a query string "
                   "(the key travels by NAME: SYMBOL_GLOSSARY_LLM_API_KEY_ENV)")
    if not str(getattr(settings, "symbol_glossary_llm_model", "") or ""):
        out.append("SYMBOL_GLOSSARY_LLM_MODEL is required (there is no fallback to LLM_MODEL)")
    if not _ENV_NAME.fullmatch(str(getattr(settings, "symbol_glossary_llm_api_key_env", "") or "")):
        out.append("SYMBOL_GLOSSARY_LLM_API_KEY_ENV must be an environment variable NAME")
    try:
        knobs = _knob_json(str(getattr(settings, "symbol_glossary_llm_inputs_json", "") or "{}"))
    except GlossaryInputsError as e:
        return [*out, str(e)]
    owned = sorted(k for k in knobs if k in OWNED_KEYS)
    if owned:
        out.append(f"SYMBOL_GLOSSARY_LLM_INPUTS_JSON may not set {', '.join(owned)}: they come "
                   "from SYMBOL_GLOSSARY_EXTRACTION / SYMBOL_GLOSSARY_LLM_ENDPOINT / _MODEL / "
                   "_API_KEY_ENV")
    unknown = sorted(k for k in knobs if k not in KNOB_TYPES and k not in OWNED_KEYS)
    if unknown:
        out.append(f"SYMBOL_GLOSSARY_LLM_INPUTS_JSON: unknown key(s) {unknown} "
                   f"(allowed: {sorted(KNOB_TYPES)})")
    undeclared = sorted(k for k in knobs if k in KNOB_TYPES and k not in declared)
    if undeclared and "glossary_llm" in declared:
        out.append(f"SYMBOL_GLOSSARY_LLM_INPUTS_JSON sets {undeclared}, which the registered "
                   "workflow does not declare")
    for key in sorted(k for k in knobs if k in KNOB_TYPES):
        why = _typed(key, knobs[key])
        if why:
            out.append(f"SYMBOL_GLOSSARY_LLM_INPUTS_JSON: {why}")
    merged = {**GLOSSARY_LLM_DEFAULTS, **{k: v for k, v in knobs.items() if k in KNOB_TYPES}}
    for key in _REQUIRED_POSITIVE:
        if isinstance(merged.get(key), int | float) and merged[key] <= 0:
            out.append(f"SYMBOL_GLOSSARY_LLM_INPUTS_JSON: {key} must be > 0 with llm on")
    wc = merged.get("glossary_llm_window_chars")
    if isinstance(wc, int) and 0 < wc <= WINDOW_OVERLAP:
        out.append(f"SYMBOL_GLOSSARY_LLM_INPUTS_JSON: glossary_llm_window_chars must be > "
                   f"{WINDOW_OVERLAP} (the window overlap)")
    return out


def glossary_inputs(settings: Any, declared: set[str], *, n_batches: int) -> dict[str, Any]:
    """The submission's ``glossary_llm_*`` inputs for ``llm`` mode, restricted
    to ``declared``: the endpoint, the model, the key's NAME and the limits
    (defaults, then the knob JSON). ``glossary_llm_concurrency`` is the
    TOTAL documents in flight the tenant allows against the model, split over
    the run's ``n_batches`` tasks (``max(1, total // n_batches)``), so a big
    upload does not multiply its load on a shared server. Call only after
    :func:`glossary_settings_problems` passed at boot."""
    knobs = _knob_json(str(getattr(settings, "symbol_glossary_llm_inputs_json", "") or "{}"))
    values: dict[str, Any] = {
        "glossary_llm_endpoint": settings.symbol_glossary_llm_endpoint,
        "glossary_llm_model": settings.symbol_glossary_llm_model,
        "glossary_llm_api_key_env": settings.symbol_glossary_llm_api_key_env,
        **GLOSSARY_LLM_DEFAULTS,
        **{k: v for k, v in knobs.items() if k in KNOB_TYPES},
    }
    total = int(values.get("glossary_llm_concurrency") or 0)
    if total > 0:
        values["glossary_llm_concurrency"] = max(1, total // max(1, n_batches))
    for key in ("glossary_llm_timeout_s", "glossary_llm_deadline_s"):
        if key in values:
            values[key] = float(values[key])
    return {k: v for k, v in values.items() if k in declared}
