"""Config loading. Everything site-specific comes from the JSON file passed with
``--config``; this module holds only non-site defaults (sizes, thresholds, seeds
for the bootstrap) and never a hostname, port, key or path.

Relative paths in the config are resolved against the config file's directory.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

#: Hard cap from the operating rules (PLAN-AND-PASS-RULE.md: gateway <= 4 in flight).
MAX_IN_FLIGHT_CAP = 4

DEFAULTS: dict[str, Any] = {
    "sample": {
        "seed": None,
        "n_eval_articles": None,
        "n_background": 0,
        "allocation": "proportional",
        "strata": {
            "journal_family_map": {},
            "default_family": "other",
            "length_bands": [],
            "year_bands": [],
        },
    },
    "passages": {
        "chars": 1200,
        "per_article": 1,
        "section_targets": ["results", "discussion", "methods", "introduction", "abstract"],
        "min_frac": 0.75,
        "max_frac": 1.25,
        "fallback_body": False,
    },
    "writer": {
        "n": 10,
        "temperature": 0.8,
        "top_p": None,
        "max_tokens": 512,
        "extra_body": {},
        "max_in_flight": 4,
        "timeout_s": 300,
        "empty_retry_s": 30,
    },
    "check_writer": None,
    "prompt": {
        "system": (
            "You write one question about a passage from a scientific article. The question "
            "must be answerable from the passage alone, must make sense to a reader who has "
            "not seen the passage (no 'this study', 'the authors', 'the passage', 'according "
            "to'), and must not copy runs of words from the passage. Then copy, character for "
            "character, the shortest span of the passage that answers it."
        ),
        "user": (
            "Passage:\n{passage}\n\nAnswer with JSON only: "
            "{{\"question\": \"...\", \"answer_quote\": \"...\"}}"
        ),
    },
    "checks": {
        "question_min": 25,
        "question_max": 400,
        "quote_min": 10,
        "quote_max": 300,
        "max_copied_run": 3,
    },
    "questions": {
        "max_attempts": 3,
    },
    "api": {
        "top_k": 10,
        "endpoint": "/v1/retrieve",
        "request_extra": {},
        "max_in_flight": 4,
        "timeout_s": 120,
        "accept_degraded": False,
        "max_503_waits": 5,
        "max_retry_after_s": 120,
    },
    "arms": [],
    "stop": {"max_failures": 20, "max_consecutive": 5, "pause_file": "PAUSE"},
    "score": {
        "ks": [1, 5, 10],
        "coverage": 0.90,
        "offsets": "verify",
        "tokenizer": None,
        "tokenizer_label": None,
        "max_unlocated_share": 0.01,
        "bootstrap_iters": 10000,
        "bootstrap_seed": 0,
        "strata_dims": ["journal_family", "length_band"],
        "report_dims": ["journal_family", "length_band", "year_band", "section_type"],
        "skip_loss_threshold": 0.10,
    },
    "decide": {
        "k": 5,
        "alpha_one_sided": 0.025,
        "stratum_point": -2.0,
        "context_ratio": 1.5,
        "min_stratum_n": 30,
    },
    "margins": None,
    "noise": {
        "n_questions": 300,
        "arms": None,
        "planned_n": 2000,
        "target_half_width": None,
        "split_reps": 1000,
        "seed": 0,
        "floor": 2.0,
    },
}

_PATH_KEYS = ("out_dir", "manifest", "include_pmcids", "corpus_root")


class ConfigError(ValueError):
    pass


def _merge(base: Any, over: Any) -> Any:
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def _resolve(base: Path, p: str | None) -> str | None:
    if p is None or p == "":
        return p
    q = Path(p)
    return str(q if q.is_absolute() else (base / q))


def load_config(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"cannot read config {p}: {type(e).__name__}") from e
    if not isinstance(raw, dict):
        raise ConfigError("config must be a JSON object")
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}
    cfg = _merge(DEFAULTS, raw)
    base = p.resolve().parent
    for k in _PATH_KEYS:
        if isinstance(cfg.get(k), str):
            cfg[k] = _resolve(base, cfg[k])
    ext = cfg.get("extracted")
    if isinstance(ext, str):
        ext = [ext]
    if ext is not None:
        cfg["extracted"] = [_resolve(base, e) for e in ext]
    for key in ("writer", "check_writer", "api"):
        blk = cfg.get(key)
        if isinstance(blk, dict) and blk.get("api_key_file"):
            blk["api_key_file"] = _resolve(base, blk["api_key_file"])
    if isinstance(cfg.get("check_writer"), dict):
        cfg["check_writer"] = _merge(dict(DEFAULTS["writer"]), cfg["check_writer"])
    cfg["_config_path"] = str(p.resolve())
    return cfg


def require(cfg: dict[str, Any], *dotted: str) -> None:
    """Raise ConfigError naming every missing (None/empty) dotted key."""
    missing = []
    for d in dotted:
        cur: Any = cfg
        for part in d.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None or cur == "" or cur == []:
            missing.append(d)
    if missing:
        raise ConfigError("config is missing: " + ", ".join(missing))


def out_path(cfg: dict[str, Any], *parts: str) -> Path:
    require(cfg, "out_dir")
    return Path(cfg["out_dir"]).joinpath(*parts)


def pause_path(cfg: dict[str, Any]) -> Path:
    pf = cfg["stop"].get("pause_file") or "PAUSE"
    q = Path(pf)
    return q if q.is_absolute() else out_path(cfg, pf)


def in_flight(block: dict[str, Any]) -> int:
    n = int(block.get("max_in_flight") or 1)
    return max(1, min(n, MAX_IN_FLIGHT_CAP))


def arms(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    out = cfg.get("arms") or []
    if not out:
        raise ConfigError("config has no arms")
    names = [a.get("name") for a in out]
    if len(set(names)) != len(names) or any(not n for n in names):
        raise ConfigError("arm names must be present and unique")
    for a in out:
        if a.get("cost_class") not in ("cheap", "mid", "expensive"):
            raise ConfigError(f"arm {a['name']}: cost_class must be cheap|mid|expensive")
        if not a.get("collection_id"):
            raise ConfigError(f"arm {a['name']}: collection_id missing")
    base = [a for a in out if a.get("baseline")]
    if len(base) != 1:
        raise ConfigError("exactly one arm must have baseline: true")
    return out


def baseline_arm(cfg: dict[str, Any]) -> dict[str, Any]:
    return next(a for a in arms(cfg) if a.get("baseline"))


def read_key(path: str | None) -> str | None:
    """Read an API key from a file at call time. The value is never logged or stored."""
    if not path:
        return None
    key = Path(path).read_text(encoding="utf-8").strip()
    return key or None
