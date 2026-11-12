#!/usr/bin/env python
"""Alias study, judge validation 2: the MODEL-GATEWAY client (JUDGE-VALIDATION-2-PLAN.md §3, §4,
§10, §11 step 2; Amendments 4-6). The one path for judges C, E, KX1 and the hosted candidates.

    model_gateway_judge.py --config STUDY dry-run (--items F | --pairs N) [--h-items N] [--cases F]
                                                 [--study-gateway-calls N]
    model_gateway_judge.py --config STUDY --private-config PRIV read-config
    model_gateway_judge.py --config STUDY --private-config PRIV self-check --cases F [--runs 5]
    model_gateway_judge.py --config STUDY --private-config PRIV label --judge J --batch B
                                                 --version V --items F --cases F

**Reach by model id only.** Every request names a model id; the gateway decides where it is served.
No address, port, header name or key path is in this file, in the tests or in the study config: the
**private config** (``--private-config``, JSON, :data:`PRIVATE_KEYS`) holds them. Keys:

* ``base_url`` -- the model gateway's base URL; ``chat_path`` and ``config_path`` -- the chat
  completions path and the configuration-endpoint path under it;
* ``key_file`` -- the study's service key file (plain text, or a JSON object read at ``key_field``);
  ``auth_header`` and ``auth_template`` (``{key}`` is replaced by the key; default ``"{key}"``);
* ``fallback_header`` and ``fallback_off_value`` -- the request option that switches fallback off,
  sent on every request;
* ``fingerprint_header`` -- the per-response configuration-fingerprint value;
* optional: ``backend_header`` (the serving instance, recorded only in a row's ``private`` field),
  ``outage_statuses`` (gateway-written errors counted as an outage, default ``[502, 503, 504]``),
  ``timeout_s`` (600), and ``config_fields`` naming the configuration endpoint's JSON fields (defaults:
  ``models`` holding a mapping from model id to an entry, or a list of entries with ``id``; per entry
  ``declared``, ``observed``, ``mismatch`` (the contradiction list) and ``fingerprint``).

**Sampling is pinned and sent on every request** (plan §3): for a local judge every one of thinking,
``max_tokens``, temperature, ``top_p``, ``top_k`` and ``n`` must be set in the study config (a config
that leaves one to the engine's defaults is refused); a hosted judge sends ``max_tokens`` and ``n`` = 1,
and temperature only when set. Each stored row carries the settings as sent.

**The identity gate, one rule per class** (plan §4, :func:`classify_reply`, :func:`check_config_read`).
A local judge's reply must carry exactly its pinned full fingerprint: a *partial* value, or a
gateway-written error (an outage status, a connection error; no fingerprint) is an **outage** -- the
reply is not stored, the batch pauses and the configuration endpoint is re-read up to
:data:`REREAD_TRIES` times :data:`REREAD_INTERVAL_S` apart (``sleep`` injected); the pinned full value
resumes the batch and re-issues the paused item; all reads partial or unreadable, any different full
value, or a non-empty contradiction list is a **mismatch**: the batch stops and is void, and the judge
runs no later batch (``STOPPED-<judge>``). A different full value or a served ``model`` outside the
accepted set on a reply is a mismatch at once; a forwarded reply WITHOUT a fingerprint is a mismatch (a
missing value on a stored reply). A hosted judge's value must be in its freeze-commit set; otherwise
that candidate stops, its rows up to the stop kept. The configuration endpoint is read at the start and
end of every batch (the same transient rule); a failed end read voids the rows since the last passing
check.

**Canaries** (plan §4): every batch starts and ends with the 10 fixed canary cases, always on
``alias-judge/3``, under the judge's one canary setting (``deterministic``: temperature 0, the fixed
seed, n = 1; ``batch``: the batch settings at n = 1). End vs start differing on 3 or more of 10 cases,
or agreement with the owner's labels 3 or more lower, voids the batch (:func:`compare_canaries`); under a
deterministic setting 1-2 differing cases are reported, not voided. A local void with a matching
fingerprint is reported as template, parser or default drift. A batch's start canary differing from
the previous batch's end canary on 3 or more cases opens a new canary epoch (no void; later rows carry
the epoch). A voided batch is never re-issued: a batch id runs once.

**Self-check and canary selection** (:func:`run_selfcheck`, :func:`select_canaries`): every case 5 times
per model-gateway judge under its canary setting, refused while any owner label is empty; the 10
canaries are the cases with an identical valid label in all runs on every judge, in committed order,
topped up by the most consistent; each case's flip rate (the share of run pairs that differ) is recorded,
and each hosted judge's observed partial fingerprints become its recorded set.

**Dry run** (:func:`project`): builds every request without sending and prints the call counts; a capped
projection past the warning limit (20,000, Amendment 6: a warning, not a stop) prints a WARNING and the
§10 drop order's proposed cuts. Nothing is ever cut automatically.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _glossbench_r2 as g  # noqa: E402
import alias_judge3_selfcheck as sc  # noqa: E402
import glossbench_alias_judge as jd  # noqa: E402

PRIVATE_REQUIRED = ("base_url", "chat_path", "config_path", "key_file", "auth_header", "fallback_header",
                    "fallback_off_value", "fingerprint_header")
PRIVATE_OPTIONAL: dict[str, Any] = {"auth_template": "{key}", "key_field": None, "backend_header": None,
                                    "outage_statuses": [502, 503, 504], "timeout_s": 600.0, "config_fields": {}}
PRIVATE_KEYS = (*PRIVATE_REQUIRED, *PRIVATE_OPTIONAL)
CONFIG_FIELDS = {"models": "models", "id": "id", "declared": "declared", "observed": "observed",
                 "mismatch": "mismatch", "fingerprint": "fingerprint"}
#: a partial fingerprint is told from a full one by its prefix (plan §4: ``v1:...`` vs ``v1-partial:...``)
PARTIAL_MARKER = "-partial:"
REREAD_TRIES = 5
REREAD_INTERVAL_S = 60.0
N_CANARIES = 10
CANARY_VOID_AT = 3
SELFCHECK_RUNS = 5
CANARY_VERSION = jd.JUDGE_VERSION_V3
CAP_FACTOR = 1.1
WARNING_LIMIT = 20_000
MAX_OUTAGES_PER_ITEM = 3
CLASSES = ("local", "hosted")
LOCAL_SAMPLING = ("thinking", "max_tokens", "temperature", "top_p", "top_k", "n")
SAMPLING_KEYS = LOCAL_SAMPLING
JUDGE_KEYS = ("model", "class", "accepted_models", "pinned_fingerprint", "freeze_set", "sampling", "canary",
              "prompts", "h_reps", "optional")
STUDY_KEYS = ("out_dir", "judges", "canary_cases", "warning_limit", "cap_factor", "excluded_from_warning",
              "partial_marker")


class Stop(Exception):  # noqa: N818
    """A gate decision that ends the batch (``kind``: mismatch / outage / error / cap)."""

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(f"{kind}:{reason}")
        self.kind, self.reason = kind, reason


# ------------------------------------------------------------------ config

def load_private_config(path: Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_private(raw)


def validate_private(raw: Mapping[str, Any]) -> dict[str, Any]:
    missing = [k for k in PRIVATE_REQUIRED if not raw.get(k)]
    if missing:
        raise SystemExit(f"private config: missing {missing}")
    unknown = sorted(set(raw) - set(PRIVATE_KEYS))
    if unknown:
        raise SystemExit(f"private config: unknown keys {unknown}")
    out = {**PRIVATE_OPTIONAL, **raw}
    bad = sorted(set(out["config_fields"]) - set(CONFIG_FIELDS))
    if bad:
        raise SystemExit(f"private config: config_fields unknown keys {bad}")
    out["config_fields"] = {**CONFIG_FIELDS, **out["config_fields"]}
    out["outage_statuses"] = [int(s) for s in out["outage_statuses"]]
    return out


def load_key(priv: Mapping[str, Any]) -> str:
    text = Path(priv["key_file"]).read_text(encoding="utf-8")
    if priv.get("key_field"):
        key = json.loads(text)[priv["key_field"]]
    else:
        key = text.strip()
    if not key:
        raise SystemExit("private config: the key file holds no key")
    return str(key)


def load_study_config(path: Path) -> dict[str, Any]:
    return validate_study(json.loads(Path(path).read_text(encoding="utf-8")))


def validate_study(raw: Mapping[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(raw) - set(STUDY_KEYS))
    if unknown:
        raise SystemExit(f"study config: unknown keys {unknown}")
    cfg = {"canary_cases": [], "warning_limit": WARNING_LIMIT, "cap_factor": CAP_FACTOR,
           "excluded_from_warning": ["C", "E"], "partial_marker": PARTIAL_MARKER, **raw}
    if not cfg.get("out_dir") or not cfg.get("judges"):
        raise SystemExit("study config: out_dir and judges are required")
    judges: dict[str, dict[str, Any]] = {}
    for jid, j in cfg["judges"].items():
        judges[jid] = validate_judge(jid, j, cfg["partial_marker"])
    cfg["judges"] = judges
    cc = list(cfg["canary_cases"])
    if cc and (len(cc) != N_CANARIES or len(set(cc)) != N_CANARIES):
        raise SystemExit(f"study config: canary_cases must name {N_CANARIES} distinct cases")
    return cfg


def validate_judge(jid: str, j: Mapping[str, Any], marker: str = PARTIAL_MARKER) -> dict[str, Any]:
    unknown = sorted(set(j) - set(JUDGE_KEYS))
    if unknown:
        raise SystemExit(f"judge {jid}: unknown keys {unknown}")
    if j.get("class") not in CLASSES or not j.get("model"):
        raise SystemExit(f"judge {jid}: model and class ({'/'.join(CLASSES)}) are required")
    s = dict(j.get("sampling") or {})
    if sorted(set(s) - set(SAMPLING_KEYS)):
        raise SystemExit(f"judge {jid}: sampling keys are {SAMPLING_KEYS}")
    can = dict(j.get("canary") or {})
    out = {"accepted_models": [j["model"]], "pinned_fingerprint": None, "freeze_set": [],
           "prompts": ["alias-judge/2", "alias-judge/3"], "h_reps": 0, "optional": False, **j,
           "sampling": s, "canary": can}
    for v in out["prompts"]:
        if v not in jd.JUDGE_SYSTEMS:
            raise SystemExit(f"judge {jid}: prompt {v!r} not in the registry")
    if j["class"] == "local":
        # plan §3: every sampling setting pinned and sent, never the engine's default
        unset = [k for k in LOCAL_SAMPLING if s.get(k) is None]
        if unset:
            raise SystemExit(f"judge {jid} (local): sampling must pin {list(LOCAL_SAMPLING)}; unset {unset}")
        pin = str(j.get("pinned_fingerprint") or "")
        if not pin or marker in pin:
            raise SystemExit(f"judge {jid} (local): pinned_fingerprint must be a full fingerprint")
        if can.get("mode") != "deterministic":
            raise SystemExit(f"judge {jid} (local): the canary setting is deterministic (plan §4)")
    else:
        if s.get("max_tokens") is None or int(s.get("n") or 1) != 1:
            raise SystemExit(f"judge {jid} (hosted): sampling needs max_tokens and n = 1")
        s["n"] = 1
        if can.get("mode") not in ("deterministic", "batch"):
            raise SystemExit(f"judge {jid} (hosted): canary.mode is deterministic or batch")
    if can.get("mode") == "deterministic" and (can.get("temperature") != 0 or not isinstance(can.get("seed"), int)):
        raise SystemExit(f"judge {jid}: a deterministic canary setting is temperature 0 and an integer seed")
    return out


def sampling_sent(judge: Mapping[str, Any], canary: bool) -> dict[str, Any]:
    """The sampling keys a request carries (exactly what is stored as sent). The canary setting is
    the judge's batch pin with n = 1, and temperature 0 plus the fixed seed when deterministic."""
    s = {k: v for k, v in judge["sampling"].items() if v is not None}
    if canary:
        s["n"] = 1
        if judge["canary"]["mode"] == "deterministic":
            s["temperature"] = 0
            s["seed"] = int(judge["canary"]["seed"])
    return s


def build_body(judge: Mapping[str, Any], version: str, item: Mapping[str, Any], canary: bool = False
               ) -> tuple[dict[str, Any], dict[str, Any], str]:
    """``(body, sampling as sent, user message)`` for one item; reached by model id only."""
    user = jd.judge_user_message(version, item["question"], item["term"], item["passage"], item.get("title") or "",
                                 [tuple(d) for d in item.get("defs") or []])
    s = sampling_sent(judge, canary)
    body: dict[str, Any] = {"model": judge["model"],
                            "messages": [{"role": "system", "content": jd.JUDGE_SYSTEMS[version]},
                                         {"role": "user", "content": user}]}
    for k in ("max_tokens", "n", "temperature", "top_p", "top_k", "seed"):
        if k in s:
            body[k] = s[k]
    if "thinking" in s:
        body["chat_template_kwargs"] = {"enable_thinking": bool(s["thinking"])}
    return body, s, user


# ------------------------------------------------------------------ transport and gateway

def _header(headers: Any, name: str | None) -> str | None:
    if not name or headers is None:
        return None
    low = name.lower()
    for k, v in dict(headers).items():
        if str(k).lower() == low:
            return str(v) if v not in (None, "") else None
    return None


@dataclass
class Raw:
    status: int
    data: Any = None
    fingerprint: str | None = None
    backend: str | None = None


class Gateway:
    """The model gateway: ``transport`` has ``post(url, headers=, json=)`` and ``get(url, headers=)``
    returning objects with ``status_code``, ``headers`` and ``json()`` (an ``httpx.Client`` fits)."""

    def __init__(self, priv: Mapping[str, Any], transport: Any, key: str | None = None) -> None:
        self.priv = priv
        self.t = transport
        self.key = key if key is not None else load_key(priv)
        self.calls = 0

    def headers(self) -> dict[str, str]:
        return {self.priv["auth_header"]: str(self.priv["auth_template"]).replace("{key}", self.key),
                self.priv["fallback_header"]: str(self.priv["fallback_off_value"])}

    def _url(self, path: str) -> str:
        return str(self.priv["base_url"]).rstrip("/") + "/" + str(path).lstrip("/")

    def chat(self, body: Mapping[str, Any]) -> Raw:
        self.calls += 1
        try:
            r = self.t.post(self._url(self.priv["chat_path"]), headers=self.headers(), json=dict(body))
        except Exception as e:  # noqa: BLE001 - a connection error is an outage, never a reply
            return Raw(status=-1, data={"error": type(e).__name__})
        status = int(r.status_code)
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            data = None
        return Raw(status=status, data=data, fingerprint=_header(r.headers, self.priv["fingerprint_header"]),
                   backend=_header(r.headers, self.priv.get("backend_header")))

    def read_config(self, marker: str = PARTIAL_MARKER) -> dict[str, dict[str, Any]] | None:
        """The configuration endpoint per model id: ``declared``, ``observed``, ``mismatch`` (the
        contradiction list), ``fingerprint`` and ``fingerprint_kind`` (full / partial / None).
        ``None`` when the endpoint could not be read (an outage)."""
        try:
            r = self.t.get(self._url(self.priv["config_path"]), headers=self.headers())
            if int(r.status_code) != 200:
                return None
            raw = r.json()
        except Exception:  # noqa: BLE001
            return None
        return parse_config(raw, self.priv["config_fields"], marker)


def fingerprint_kind(fp: str | None, marker: str = PARTIAL_MARKER) -> str | None:
    if not fp:
        return None
    return "partial" if marker in fp else "full"


def parse_config(raw: Any, fields: Mapping[str, str], marker: str = PARTIAL_MARKER) -> dict[str, dict[str, Any]]:
    f = {**CONFIG_FIELDS, **fields}
    models = raw.get(f["models"]) if isinstance(raw, dict) and f["models"] else raw
    if isinstance(models, list):
        models = {str(m.get(f["id"])): m for m in models if isinstance(m, dict)}
    if not isinstance(models, dict):
        raise ValueError("configuration endpoint: no model mapping")
    out = {}
    for mid, e in models.items():
        e = e if isinstance(e, dict) else {}
        fp = e.get(f["fingerprint"]) or None
        out[str(mid)] = {"declared": e.get(f["declared"]), "observed": e.get(f["observed"]),
                         "mismatch": list(e.get(f["mismatch"]) or []), "fingerprint": fp,
                         "fingerprint_kind": fingerprint_kind(fp, marker)}
    return out


# ------------------------------------------------------------------ the identity gate

def classify_reply(judge: Mapping[str, Any], raw: Raw, priv: Mapping[str, Any], *, observe_hosted: bool = False,
                   marker: str = PARTIAL_MARKER) -> tuple[str, str]:
    """``(kind, reason)`` for one reply: ``ok``, ``outage`` (store nothing; pause and re-read),
    ``mismatch`` (store nothing; the class rule stops) or ``error`` (another HTTP error: stop)."""
    if raw.status == -1 or raw.status in priv["outage_statuses"]:
        return "outage", f"gateway_error:{raw.status}"
    if raw.status != 200 or not isinstance(raw.data, dict) or not raw.data.get("choices"):
        return "error", f"http_{raw.status}"
    served = raw.data.get("model")
    if served not in judge["accepted_models"]:
        return "mismatch", f"served_model:{served}"
    fp = raw.fingerprint
    if not fp:  # a forwarded reply without its value: a missing value on a stored reply
        return "mismatch", "fingerprint_missing"
    kind = fingerprint_kind(fp, marker)
    if judge["class"] == "local":
        if kind == "partial":
            return "outage", "fingerprint_partial"
        if fp != judge["pinned_fingerprint"]:
            return "mismatch", f"fingerprint_different:{fp}"
        return "ok", ""
    if observe_hosted:
        return "ok", ""
    if fp not in judge["freeze_set"]:
        return "mismatch", f"fingerprint_not_in_freeze_set:{fp}"
    return "ok", ""


def check_config_read(judge: Mapping[str, Any], entries: Mapping[str, Any] | None) -> tuple[str, str]:
    """One configuration-endpoint read against a judge: ``pass``, ``transient`` (unreadable, or a
    partial value on a local judge) or ``mismatch`` (a different full value or a contradiction).
    A hosted judge's read is recorded, not gated (``pass`` when it holds a value; its per-call gate
    compares the value with the freeze-commit set)."""
    e = (entries or {}).get(judge["model"]) if entries is not None else None
    if not e or not e.get("fingerprint"):
        return "transient", "unread"
    if judge["class"] == "hosted":
        return "pass", ""
    if e["fingerprint_kind"] == "partial":
        return "transient", "fingerprint_partial"
    if e["fingerprint"] != judge["pinned_fingerprint"]:
        return "mismatch", f"fingerprint_different:{e['fingerprint']}"
    if e.get("mismatch"):
        return "mismatch", f"contradictions:{','.join(map(str, e['mismatch']))}"
    return "pass", ""


def reread(gw: Gateway, judge: Mapping[str, Any], sleep: Callable[[float], None], log: Callable[[dict], None],
           marker: str = PARTIAL_MARKER, why: str = "") -> tuple[str, str]:
    """The pause rule: up to :data:`REREAD_TRIES` reads :data:`REREAD_INTERVAL_S` apart. ``pass`` on the
    pinned value; ``mismatch`` on a different full value or contradiction (at once) or when every read
    stayed transient (a local judge); ``outage`` when every read stayed unreadable on a hosted judge."""
    last = ""
    for i in range(1, REREAD_TRIES + 1):
        sleep(REREAD_INTERVAL_S)
        res, reason = check_config_read(judge, gw.read_config(marker))
        log({"event": "reread", "try": i, "result": res, "reason": reason, "why": why})
        if res in ("pass", "mismatch"):
            return res, reason
        last = reason
    if judge["class"] == "local":
        return "mismatch", f"transient_sustained:{last}"
    return "outage", f"outage_sustained:{last}"


def config_check(gw: Gateway, judge: Mapping[str, Any], sleep: Callable[[float], None],
                 log: Callable[[dict], None], marker: str, at: str) -> dict[str, Any]:
    """A batch start or end read, with the transient rule (a pinned configuration). A hosted judge's
    read is recorded only (``result`` ``recorded``): the plan gates the pinned configurations here and
    a hosted judge per call against its freeze-commit set."""
    entries = gw.read_config(marker)
    res, reason = check_config_read(judge, entries)
    e = (entries or {}).get(judge["model"]) or {}
    log({"event": f"config_{at}", "result": res, "reason": reason, "fingerprint": e.get("fingerprint")})
    if judge["class"] == "hosted":
        return {"result": "recorded", "reason": reason, "fingerprint": e.get("fingerprint")}
    if res == "transient":
        res, reason = reread(gw, judge, sleep, log, marker, why=f"config_{at}")
    return {"result": res, "reason": reason, "fingerprint": e.get("fingerprint")}


# ------------------------------------------------------------------ one gated call

def _parse(version: str, text: str) -> tuple[Any, str]:
    if version in jd.GRADED_VERSIONS:
        return jd.parse_judgement_v3(text)
    v, r = jd.parse_judgement(text)
    return v, r


def _choices(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    return sorted(data.get("choices") or [], key=lambda x: int(x.get("index") or 0))


@dataclass
class Ctx:
    gw: Gateway
    priv: Mapping[str, Any]
    marker: str
    sleep: Callable[[float], None]
    log: Callable[[dict], None]
    cap: int | None = None
    counts: Counter = field(default_factory=Counter)
    observed: set = field(default_factory=set)
    last_check_rows: int = 0


def gated_call(ctx: Ctx, judge: Mapping[str, Any], version: str, item: Mapping[str, Any], *, canary: bool,
               observe_hosted: bool = False, n_rows: Callable[[], int] = lambda: 0) -> dict[str, Any]:
    """Send one item through the gate; return the row to store. Outages pause and re-issue (the
    paused item only); a mismatch, a sustained outage, another HTTP error or the call cap raise
    :class:`Stop`. With n = 1 an invalid reply is re-issued once (prereg R4); with n > 1 the invalid
    samples are kept as ``None`` and counted."""
    body, sent, user = build_body(judge, version, item, canary)
    outages = invalid_reissues = 0
    while True:
        if ctx.cap is not None and ctx.gw.calls >= ctx.cap:
            raise Stop("cap", f"call_cap:{ctx.cap}")
        raw = ctx.gw.chat(body)
        ctx.counts["calls"] += 1
        kind, reason = classify_reply(judge, raw, ctx.priv, observe_hosted=observe_hosted, marker=ctx.marker)
        if kind == "outage":
            outages += 1
            ctx.counts["pauses"] += 1
            ctx.log({"event": "pause", "item": item.get("item_id") or item.get("name"), "reason": reason})
            if outages > MAX_OUTAGES_PER_ITEM:
                raise Stop("outage", f"outages_per_item:{reason}")
            res, why = reread(ctx.gw, judge, ctx.sleep, ctx.log, ctx.marker, why=reason)
            ctx.log({"event": "pause_outcome", "result": res, "reason": why})
            if res == "pass":
                ctx.last_check_rows = n_rows()
                continue
            raise Stop("mismatch" if res == "mismatch" else "outage", why)
        if kind != "ok":
            raise Stop(kind, reason)
        if observe_hosted and judge["class"] == "hosted":
            ctx.observed.add(raw.fingerprint)
        ch = _choices(raw.data)
        parsed = [_parse(version, (x.get("message") or {}).get("content") or "") for x in ch]
        labels = [p[0] for p in parsed]
        if len(ch) == 1 and labels[0] is None and invalid_reissues == 0:
            invalid_reissues += 1
            ctx.counts["invalid_reissues"] += 1
            continue
        break
    msgs = [x.get("message") or {} for x in ch]
    reported = [("reasoning" in m or "reasoning_content" in m) for m in msgs]
    thinking_served = (any(bool(m.get("reasoning") or m.get("reasoning_content")) for m in msgs)
                       if any(reported) else None)
    u = raw.data.get("usage") or {}
    rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
    graded = version in jd.GRADED_VERSIONS
    row: dict[str, Any] = {
        "item_id": item.get("item_id") or item.get("name"), "judge_version": version,
        "system_sha256": g.sha_text(jd.JUDGE_SYSTEMS[version]), "prompt_sha256": g.sha_text(user),
        ("grades" if graded else "verdicts"): [(lab if graded else (None if lab is None else int(lab)))
                                                for lab in labels],
        "reasons": [p[1] for p in parsed], "invalid": sum(lab is None for lab in labels),
        "attempts": 1 + invalid_reissues + outages,
        "model": raw.data.get("model"), "requested_model": judge["model"], "fingerprint": raw.fingerprint,
        "fingerprint_kind": fingerprint_kind(raw.fingerprint, ctx.marker), "sampling_sent": sent,
        "thinking_served": thinking_served, "reasoning_tokens": None if rt is None else int(rt),
        "pt": u.get("prompt_tokens"), "ct": u.get("completion_tokens"), "date": g.now_iso(),
        "private": {"backend": raw.backend}}
    if len(labels) == 1:
        row["grade" if graded else "verdict"] = row["grades" if graded else "verdicts"][0]
    return row


def public_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """A row without its private field (the serving instance is never in a public file)."""
    return {k: v for k, v in row.items() if k != "private"}


# ------------------------------------------------------------------ canaries

def _label_of(row: Mapping[str, Any]) -> Any:
    return row.get("grade")


def compare_canaries(start: Mapping[str, Any], end: Mapping[str, Any], expected: Mapping[str, str],
                     deterministic: bool) -> dict[str, Any]:
    """End vs start canary (plan §4, the void rule). An invalid label is a value of its own."""
    names = list(expected)
    differ = [n for n in names if start.get(n) != end.get(n)]
    agree_s = sum(start.get(n) == expected[n] for n in names)
    agree_e = sum(end.get(n) == expected[n] for n in names)
    reasons = []
    if len(differ) >= CANARY_VOID_AT:
        reasons.append(f"differ:{len(differ)}")
    if agree_s - agree_e >= CANARY_VOID_AT:
        reasons.append(f"owner_agreement_drop:{agree_s - agree_e}")
    return {"differ": len(differ), "differ_cases": differ, "agree_start": agree_s, "agree_end": agree_e,
            "void": bool(reasons), "reasons": reasons,
            "reported": bool(deterministic and 0 < len(differ) < CANARY_VOID_AT)}


def epoch_boundary(prev_end: Mapping[str, Any] | None, start: Mapping[str, Any]) -> tuple[bool, int]:
    """Chaining: a batch's start canary vs the previous batch's end canary for the same judge."""
    if prev_end is None:
        return False, 0
    d = sum(prev_end.get(n) != start.get(n) for n in start)
    return d >= CANARY_VOID_AT, d


def flip_rate(labels: Sequence[Any]) -> float:
    """The share of run pairs whose labels differ (the plan's q, measured)."""
    pairs = list(combinations(labels, 2))
    return round(sum(a != b for a, b in pairs) / len(pairs), 4) if pairs else 0.0


def select_canaries(runs: Mapping[str, Mapping[str, Sequence[Any]]], order: Sequence[str],
                    k: int = N_CANARIES, n_runs: int = SELFCHECK_RUNS) -> dict[str, Any]:
    """The selection rule (plan §4): the cases whose label was identical (and valid) in all
    ``n_runs`` runs on EVERY model-gateway judge, in the committed order; if fewer than ``k``
    qualify, the remaining places go to the most consistent cases (the summed size of each judge's
    largest group of equal valid labels), ties in committed order."""
    def stable(c: str) -> bool:
        return all(len(r[c]) == n_runs and len(set(r[c])) == 1 and r[c][0] is not None for r in runs.values())

    def consistency(c: str) -> int:
        return sum(max(Counter(x for x in r[c] if x is not None).values(), default=0) for r in runs.values())

    qualifying = [c for c in order if stable(c)]
    chosen = qualifying[:k]
    rest = sorted((c for c in order if c not in chosen), key=lambda c: (-consistency(c), order.index(c)))
    filled = rest[: max(0, k - len(chosen))]
    chosen = chosen + filled
    return {"canaries": chosen, "qualifying": len(qualifying), "filled": filled,
            "flip_rates": {c: {j: flip_rate(r[c]) for j, r in runs.items()} for c in chosen},
            "all_flip_rates": {c: {j: flip_rate(r[c]) for j, r in runs.items()} for c in order}}


# ------------------------------------------------------------------ files

def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def paths(out: Path, jid: str) -> dict[str, Path]:
    return {"labels": out / f"labels-mg-{jid}.jsonl", "canary": out / f"canary-mg-{jid}.jsonl",
            "batches": out / f"batches-mg-{jid}.jsonl", "events": out / f"events-mg-{jid}.jsonl",
            "state": out / f"state-mg-{jid}.json", "stopped": out / f"STOPPED-{jid}"}


def valid_rows(out: Path, jid: str) -> list[dict[str, Any]]:
    """A judge's label rows outside every voided range (the voided rows are counted, never used)."""
    p = paths(out, jid)
    voids = {b["batch"]: b for b in _rows(p["batches"]) if b.get("void")}
    keep = []
    for r in _rows(p["labels"]):
        b = voids.get(r["batch"])
        if b is None or r["seq"] < int(b.get("void_from_seq", 0)):
            keep.append(r)
    return keep


# ------------------------------------------------------------------ one batch

def canary_cases(cfg: Mapping[str, Any], cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sc.require_owner_labels(cases)
    names = list(cfg["canary_cases"])
    if len(names) != N_CANARIES:
        raise SystemExit(f"canary_cases: {N_CANARIES} chosen cases are required (fixed in the freeze commit)")
    by = {c["name"]: c for c in cases}
    missing = [n for n in names if n not in by]
    if missing:
        raise SystemExit(f"canary_cases not among the self-check cases: {missing}")
    return [dict(by[n]) for n in names]


def run_batch(cfg: Mapping[str, Any], gw: Gateway, jid: str, batch: str, version: str,
              items: Sequence[Mapping[str, Any]], cases: Sequence[Mapping[str, Any]], *,
              sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """One batch (one judge x one prompt over the items): start read, start canary, the items,
    end canary, end read; the void and epoch rules; a summary row in ``batches-mg-<judge>.jsonl``."""
    judge = cfg["judges"][jid]
    out = Path(cfg["out_dir"])
    p = paths(out, jid)
    if version not in judge["prompts"]:
        raise SystemExit(f"judge {jid}: prompt {version} is not one of its batches {judge['prompts']}")
    if p["stopped"].exists():
        raise SystemExit(f"judge {jid} is stopped ({p['stopped'].read_text().strip()}): no later batch runs")
    if any(b["batch"] == batch for b in _rows(p["batches"])):
        raise SystemExit(f"judge {jid}: batch {batch!r} already ran (a batch is never re-issued)")
    if judge["class"] == "hosted" and not judge["freeze_set"]:
        raise SystemExit(f"judge {jid} (hosted): no freeze_set (recorded in the freeze commit)")
    canaries = canary_cases(cfg, cases)
    expected = {c["name"]: c["expected_label"] for c in canaries}
    state: dict[str, Any] = (json.loads(p["state"].read_text()) if p["state"].exists()
                             else {"epoch": 0, "last_end_canary": None})
    seq0 = len(_rows(p["labels"]))
    stored: list[dict[str, Any]] = []
    planned = len(items) + 2 * N_CANARIES
    ctx = Ctx(gw=gw, priv=gw.priv, marker=cfg["partial_marker"], sleep=sleep,
              log=lambda e: _append(p["events"], {"judge": jid, "batch": batch, "t": g.now_iso(), **e}),
              cap=gw.calls + math.floor(planned * float(cfg["cap_factor"])))
    ctx.last_check_rows = seq0

    def n_rows() -> int:
        return seq0 + len(stored)

    det = judge["canary"]["mode"] == "deterministic"
    summary: dict[str, Any] = {"batch": batch, "judge": jid, "judge_version": version, "items": len(items),
                               "planned_calls": planned, "void": False, "epoch": state["epoch"],
                               "canary_setting": sampling_sent(judge, canary=True), "started": g.now_iso()}

    def canary(at: str) -> dict[str, Any]:
        labels = {}
        for c in canaries:
            row = gated_call(ctx, judge, CANARY_VERSION, c, canary=True, n_rows=n_rows)
            row.update({"judge": jid, "batch": batch, "kind": f"canary_{at}", "epoch": state["epoch"]})
            _append(p["canary"], row)
            labels[c["name"]] = _label_of(row)
        return labels

    def finish(status: str, **extra: Any) -> dict[str, Any]:
        summary.update({"status": status, "stored": len(stored), "calls": ctx.counts["calls"],
                        "pauses": ctx.counts["pauses"], "invalid_reissues": ctx.counts["invalid_reissues"],
                        "ended": g.now_iso(), **extra})
        _append(p["batches"], summary)
        return summary

    def stop(e: Stop, at: str) -> dict[str, Any]:
        if e.kind == "mismatch":
            # local: the batch stops and is void; hosted: that candidate stops, its rows kept
            local = judge["class"] == "local"
            p["stopped"].write_text(f"{batch}:{at}:{e.reason}\n")
            return finish(f"stopped:mismatch:{e.reason}", void=local,
                          void_from_seq=seq0 if local else None, stopped_at=at)
        return finish(f"paused:{e.kind}:{e.reason}", stopped_at=at)

    start = config_check(gw, judge, sleep, ctx.log, ctx.marker, "start")
    summary["config_start"] = start
    if start["result"] not in ("pass", "recorded"):
        return stop(Stop("mismatch" if start["result"] == "mismatch" else "outage", start["reason"]), "config_start")
    try:
        start_c = canary("start")
    except Stop as e:
        return stop(e, "canary_start")
    boundary, d_prev = epoch_boundary(state["last_end_canary"], start_c)
    if boundary:
        state["epoch"] += 1
    summary.update({"epoch": state["epoch"], "epoch_boundary": boundary, "differ_from_previous_end": d_prev})
    try:
        for it in items:
            row = gated_call(ctx, judge, version, it, canary=False, n_rows=n_rows)
            row.update({"judge": jid, "batch": batch, "kind": "label", "epoch": state["epoch"],
                        "seq": seq0 + len(stored), "k": f"{jid}|{batch}|{row['item_id']}"})
            _append(p["labels"], row)
            stored.append(row)
        end_c = canary("end")
    except Stop as e:
        return stop(e, "labels")
    cmp = compare_canaries(start_c, end_c, expected, det)
    summary["canary"] = cmp
    end = config_check(gw, judge, sleep, ctx.log, ctx.marker, "end")
    summary["config_end"] = end
    state["last_end_canary"] = end_c
    p["state"].write_text(json.dumps(state, sort_keys=True))
    if end["result"] not in ("pass", "recorded"):
        p["stopped"].write_text(f"{batch}:config_end:{end['reason']}\n")
        return finish(f"void:config_end:{end['reason']}", void=True, void_from_seq=ctx.last_check_rows)
    if cmp["void"]:
        drift = judge["class"] == "local"  # the fingerprint matched at the end read
        p["stopped"].write_text(f"{batch}:canary:{','.join(cmp['reasons'])}\n")
        return finish("void:canary", void=True, void_from_seq=seq0,
                      drift_report="template_parser_or_default_drift" if drift else None)
    return finish("done")


# ------------------------------------------------------------------ self-check and selection

def run_selfcheck(cfg: Mapping[str, Any], gw: Gateway, cases: Sequence[Mapping[str, Any]],
                  judge_ids: Sequence[str] | None = None, runs: int = SELFCHECK_RUNS, *,
                  sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """The pre-freeze self-check (JUDGE-3-PROMPT.md §4): every case ``runs`` times per judge under its
    canary setting on ``alias-judge/3``; per-case agreement with the owner's labels; the hosted
    judges' observed partial fingerprints; then the canary selection over every judge that finished.
    Refused while any owner label is empty."""
    sc.require_owner_labels(cases)
    out = Path(cfg["out_dir"])
    res: dict[str, Any] = {"judges": {}, "runs": runs, "cases": [c["name"] for c in cases]}
    labels: dict[str, dict[str, list[Any]]] = {}
    for jid in judge_ids or list(cfg["judges"]):
        judge = cfg["judges"][jid]
        p = paths(out, jid)
        def log(e: dict, jid: str = jid, p: dict[str, Path] = p) -> None:
            _append(p["events"], {"judge": jid, "batch": "selfcheck", "t": g.now_iso(), **e})

        ctx = Ctx(gw=gw, priv=gw.priv, marker=cfg["partial_marker"], sleep=sleep, log=log)
        lab: dict[str, list[Any]] = {c["name"]: [] for c in cases}
        status = "done"
        try:
            for r in range(1, runs + 1):
                for c in cases:
                    row = gated_call(ctx, judge, CANARY_VERSION, c, canary=True, observe_hosted=True)
                    row.update({"judge": jid, "batch": "selfcheck", "kind": "selfcheck", "run": r})
                    _append(out / f"selfcheck-mg-{jid}.jsonl", row)
                    lab[c["name"]].append(_label_of(row))
        except Stop as e:
            status = f"stopped:{e.kind}:{e.reason}"
        res["judges"][jid] = {
            "status": status, "calls": ctx.counts["calls"], "pauses": ctx.counts["pauses"],
            "canary_setting": sampling_sent(judge, canary=True),
            "agree_with_owner": {c["name"]: sum(x == c["expected_label"] for x in lab[c["name"]]) for c in cases},
            "observed_fingerprints": sorted(ctx.observed) if judge["class"] == "hosted" else None}
        if status == "done":
            labels[jid] = lab
    res["selection"] = select_canaries(labels, [c["name"] for c in cases], n_runs=runs) if labels else None
    res["selection_judges"] = sorted(labels)
    return res


# ------------------------------------------------------------------ dry run

def judge_calls(judge: Mapping[str, Any], n_pairs: int, h_items: int, n_cases: int) -> dict[str, Any]:
    """Planned calls of one judge (plan §10): each prompt on all pairs, the repetitions on H as one
    more batch, 10 canaries at the start and end of each batch, the 5-run self-check."""
    batches = len(judge["prompts"]) + (1 if judge["h_reps"] and h_items else 0)
    c: dict[str, Any] = {"labels_by_prompt": dict.fromkeys(judge["prompts"], n_pairs),
                         "h_reps": int(judge["h_reps"]) * h_items, "canary": 2 * N_CANARIES * batches,
                         "selfcheck": n_cases * SELFCHECK_RUNS, "batches": batches}
    c["total"] = sum(c["labels_by_prompt"].values()) + c["h_reps"] + c["canary"] + c["selfcheck"]
    return c


def project(cfg: Mapping[str, Any], n_pairs: int, h_items: int, n_cases: int, study_calls: int) -> dict[str, Any]:
    """The projection against the warning limit; the drop order's proposed cuts when it is passed
    (the operator reports them to the owner; nothing is cut here)."""
    per = {jid: judge_calls(j, n_pairs, h_items, n_cases) for jid, j in cfg["judges"].items()}
    counted = [jid for jid in per if jid not in cfg["excluded_from_warning"]]
    f, lim = float(cfg["cap_factor"]), int(cfg["warning_limit"])

    def capped(base: int) -> int:
        return math.ceil((study_calls + base) * f)

    base = sum(per[j]["total"] for j in counted)
    out: dict[str, Any] = {"per_judge": per, "counted": counted, "study_gateway_calls": study_calls,
                           "total": study_calls + base, "capped_total": capped(base), "warning_limit": lim,
                           "warning": capped(base) > lim, "drop_order": []}
    if out["warning"]:
        opt = [j for j in counted if cfg["judges"][j]["optional"]]
        opt_v2 = sum(per[j]["labels_by_prompt"].get("alias-judge/2", 0) for j in opt)
        opt_all = sum(per[j]["total"] for j in opt)
        reps = sum(per[j]["h_reps"] + 2 * N_CANARIES for j in counted if per[j]["h_reps"] and j not in opt)
        for i, (what, left) in enumerate((("drop the optional candidates' /2 run", base - opt_v2),
                                          ("drop the optional candidates", base - opt_all),
                                          ("drop all candidates' repetitions 2-3 on H", base - opt_all - reps))):
            out["drop_order"].append({"step": i + 1, "action": what, "capped_total_after": capped(left)})
    return out


def dry_run(cfg: Mapping[str, Any], items: Sequence[Mapping[str, Any]] | None, n_pairs: int, h_items: int,
            cases: Sequence[Mapping[str, Any]], study_calls: int, *, n_cases: int | None = None,
            echo: Callable[[str], None] = print) -> dict[str, Any]:
    """Build every request (labels per prompt, canaries, self-check) without sending; print counts.
    ``n_cases`` (default: the cases given) is the self-check size the projection counts."""
    built: Counter[str] = Counter()
    chars: Counter[str] = Counter()
    for jid, j in cfg["judges"].items():
        for v in j["prompts"]:
            for it in items or []:
                body, _s, _u = build_body(j, v, it)
                built[jid] += 1
                chars[jid] += sum(len(m["content"]) for m in body["messages"])
        for c in cases:
            body, _s, _u = build_body(j, CANARY_VERSION, c, canary=True)
            built[jid] += 1
    n = len(items) if items is not None else n_pairs
    pr = project(cfg, n, h_items, len(cases) if n_cases is None else int(n_cases), study_calls)
    pr["requests_built"] = dict(built)
    pr["chars_built"] = dict(chars)
    for jid, c in pr["per_judge"].items():
        tag = "" if jid in pr["counted"] else " (outside the warning figure)"
        echo(f"{jid}{tag}: {c['total']} calls = labels {c['labels_by_prompt']} + H reps {c['h_reps']} + "
             f"canary {c['canary']} ({c['batches']} batches) + self-check {c['selfcheck']}")
    echo(f"study gateway {study_calls} + counted judges = {pr['total']}; with the {cfg['cap_factor']} caps "
         f"{pr['capped_total']} (warning limit {pr['warning_limit']})")
    if pr["warning"]:
        echo(f"WARNING: the capped projection {pr['capped_total']} passes the warning limit "
             f"{pr['warning_limit']}. Stop before the candidate calls and report to the owner; the drop "
             "order proposes (nothing is cut automatically):")
        for s in pr["drop_order"]:
            echo(f"  {s['step']}. {s['action']} -> capped total {s['capped_total_after']}")
    return pr


# ------------------------------------------------------------------ CLI

def _items(path: Path) -> list[dict[str, Any]]:
    rows = _rows(Path(path))
    for r in rows:
        missing = [k for k in ("item_id", "question", "term", "passage") if k not in r]
        if missing:
            raise SystemExit(f"items: row missing {missing}")
    return rows


def main(argv: Sequence[str] | None = None, *, transport: Any = None, sleep: Callable[[float], None] = time.sleep,
         echo: Callable[[str], None] = print) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--private-config", type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dry-run")
    src = d.add_mutually_exclusive_group(required=True)
    src.add_argument("--items", type=Path)
    src.add_argument("--pairs", type=int)
    d.add_argument("--h-items", type=int, default=0)
    d.add_argument("--cases", type=Path)
    d.add_argument("--study-gateway-calls", type=int, default=0)
    d.add_argument("--selfcheck-cases", type=int, help="self-check size to count (default: --cases, else 20)")
    sub.add_parser("read-config")
    s = sub.add_parser("self-check")
    s.add_argument("--cases", type=Path)
    s.add_argument("--runs", type=int, default=SELFCHECK_RUNS)
    s.add_argument("--judge", action="append")
    lb = sub.add_parser("label")
    lb.add_argument("--judge", required=True)
    lb.add_argument("--batch", required=True)
    lb.add_argument("--version", required=True)
    lb.add_argument("--items", type=Path, required=True)
    lb.add_argument("--cases", type=Path)
    a = ap.parse_args(argv)
    cfg = load_study_config(a.config)
    if a.cmd == "dry-run":
        cases = sc.load_cases(a.cases) if a.cases else []
        items = _items(a.items) if a.items else None
        nc = a.selfcheck_cases if a.selfcheck_cases is not None else (len(cases) if a.cases else 20)
        pr = dry_run(cfg, items, a.pairs or 0, a.h_items, cases, a.study_gateway_calls, n_cases=nc, echo=echo)
        echo(json.dumps({k: pr[k] for k in ("total", "capped_total", "warning", "requests_built")}, sort_keys=True))
        return 0
    if a.private_config is None:
        raise SystemExit(f"{a.cmd}: --private-config is required (the gateway's address, key and header names)")
    priv = load_private_config(a.private_config)
    if transport is None:
        import httpx

        transport = httpx.Client(timeout=float(priv["timeout_s"]))
    gw = Gateway(priv, transport)
    out = Path(cfg["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "read-config":
        entries = gw.read_config(cfg["partial_marker"])
        if entries is None:
            echo("configuration endpoint: unreadable")
            return 1
        rep = {jid: {**(entries.get(j["model"]) or {}), "model": j["model"],
                     "check": check_config_read(j, entries)[0]} for jid, j in cfg["judges"].items()}
        _append(out / "config-reads-mg.jsonl", {"t": g.now_iso(), "judges": rep})
        echo(json.dumps(rep, sort_keys=True, indent=1))
        return 0
    if a.cmd == "self-check":
        res = run_selfcheck(cfg, gw, sc.load_cases(a.cases), a.judge, a.runs, sleep=sleep)
        g.write_json_atomic(out / "selfcheck-mg.json", res)
        echo(json.dumps({k: res[k] for k in ("selection", "selection_judges")}, sort_keys=True))
        return 0
    res = run_batch(cfg, gw, a.judge, a.batch, a.version, _items(a.items), sc.load_cases(a.cases), sleep=sleep)
    echo(json.dumps({k: res.get(k) for k in ("status", "stored", "void", "epoch", "calls")}, sort_keys=True))
    return 0 if res["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
