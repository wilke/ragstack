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
  ``outage_statuses`` (gateway-written errors counted as an outage, default ``[502, 503, 504]``) and
  ``timeout_s`` (600). The configuration endpoint's JSON shape is fixed in code (:func:`parse_config`).

**Study config, per judge**: ``model``, ``class`` (local / hosted), ``pinned_fingerprint`` (local) or
``freeze_set`` (hosted), ``sampling``, ``canary`` (``mode`` deterministic + ``temperature`` 0 + ``seed``,
or batch), ``prompts``, ``h_reps``, ``optional``, ``max_in_flight`` (1 by default, at most 4); at the
top level ``out_dir``, ``judges``, ``canary_cases`` (the 10 frozen names). The model gateway needs no
size notice (no token cost to the study budget); the study-gateway judges keep theirs.

**Sampling is pinned and sent on every request** (plan §3): for a local judge every one of thinking,
``max_tokens``, temperature, ``top_p``, ``top_k`` and ``n`` must be set in the study config (a config
that leaves one to the engine's defaults is refused); a hosted judge sends ``max_tokens`` and ``n`` = 1,
and temperature only when set. Each stored row carries the settings as sent.

**The identity gate, one rule per class** (plan §4, :func:`classify_reply`, :func:`check_config_read`).
A local judge's reply must carry exactly its pinned full fingerprint: a *partial* value, or a
gateway-written error (an outage status, a connection error; no fingerprint) is an **outage** -- the
reply is not stored, the batch pauses and the configuration endpoint is re-read up to
:data:`REREAD_TRIES` times :data:`REREAD_INTERVAL_S` apart (``sleep`` injected); the pinned full value
resumes the batch and re-issues the paused item; ALL reads partial, any different full value, or a
non-empty contradiction list (on any backend) is a **mismatch**: the batch stops and is void, and the
judge runs no later batch (``STOPPED-<judge>``); a try that was unreadable makes it an outage that
outlasted the re-reads, which PAUSES the batch (resumable). A different full value or a served ``model`` outside the
accepted set on a reply is a mismatch at once; a forwarded reply WITHOUT a fingerprint is a mismatch (a
missing value on a stored reply). A hosted judge's value must be in its freeze-commit set; otherwise
that candidate stops, its rows up to the stop kept. The configuration endpoint is read at the start and
end of every batch (the same transient rule), and its observed block is recorded (a same-fingerprint
restart is reported, not voided); an end read that finds a mismatch voids the rows since the last
passing check (deliberately narrower than a per-call local mismatch, which voids from the batch's first
row), and an end read in an outage pauses the batch. **A pause is not a void**: a paused batch keeps its
rows and checkpoint and resumes under the same batch id (``label --resume``), also after a restart,
with the judge's configuration and the canary cases pinned by sha256; only a closed outcome (done,
``ended:cap``, a void, a mismatch stop) spends the id. **The 110 % cap ends the labels phase**: the end
canary and end read still run and the batch closes as ``ended:cap`` (not resumable). One process per
judge (``LOCK-<judge>``, ``O_EXCL``).

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
import os
import re
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
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
                                    "outage_statuses": [502, 503, 504], "timeout_s": 600.0}
PRIVATE_KEYS = (*PRIVATE_REQUIRED, *PRIVATE_OPTIONAL)
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
MAX_IN_FLIGHT = jd.MAX_INFLIGHT  # 4 (plan §4, §10)
CLASSES = ("local", "hosted")
LOCAL_SAMPLING = ("thinking", "max_tokens", "temperature", "top_p", "top_k", "n")
SAMPLING_KEYS = LOCAL_SAMPLING
JUDGE_KEYS = ("model", "class", "accepted_models", "pinned_fingerprint", "freeze_set", "sampling", "canary",
              "prompts", "h_reps", "optional", "max_in_flight")
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
           "prompts": ["alias-judge/2", "alias-judge/3"], "h_reps": 0, "optional": False, "max_in_flight": 1,
           **j, "sampling": s, "canary": can}
    if not 1 <= int(out["max_in_flight"]) <= MAX_IN_FLIGHT:
        raise SystemExit(f"judge {jid}: max_in_flight is 1..{MAX_IN_FLIGHT}")
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
        self.lock = threading.Lock()

    def headers(self) -> dict[str, str]:
        return {self.priv["auth_header"]: str(self.priv["auth_template"]).replace("{key}", self.key),
                self.priv["fallback_header"]: str(self.priv["fallback_off_value"])}

    def _url(self, path: str) -> str:
        return str(self.priv["base_url"]).rstrip("/") + "/" + str(path).lstrip("/")

    def chat(self, body: Mapping[str, Any]) -> Raw:
        with self.lock:
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
        """The configuration endpoint per model id (:func:`parse_config`); ``None`` when the endpoint
        could not be read (an outage)."""
        try:
            r = self.t.get(self._url(self.priv["config_path"]), headers=self.headers())
            if int(r.status_code) != 200:
                return None
            raw = r.json()
        except Exception:  # noqa: BLE001
            return None
        return parse_config(raw, marker)


def fingerprint_kind(fp: str | None, marker: str = PARTIAL_MARKER, partial: bool | None = None) -> str | None:
    """``full`` / ``partial`` / None. The configuration endpoint's ``partial`` flag decides when given;
    otherwise the value's prefix (``v1:`` full, ``v1-partial:`` partial)."""
    if not fp:
        return None
    if partial is not None:
        return "partial" if partial else "full"
    return "partial" if marker in fp else "full"


def parse_config(raw: Any, marker: str = PARTIAL_MARKER) -> dict[str, dict[str, Any]]:
    """The configuration endpoint (live since 2026-11-12): ``{"time", "models": [{"id", "declared",
    "backends": [{"name", "route", "state", "config", "partial", "observed", "mismatch"}]}]}``. The
    fingerprint (``config``) is per BACKEND and a model may have several (a hosted id on two clusters);
    every backend is returned. Per model id: ``declared``, ``time`` and ``backends`` (each with
    ``fingerprint``, ``fingerprint_kind``, ``observed`` (local backends only), ``mismatch`` (the
    contradiction list, empty when absent), ``name``, ``route``, ``state``). The backend name is private
    (tracing only); it plays no part in identity."""
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
        raise ValueError("configuration endpoint: no models list")
    out: dict[str, dict[str, Any]] = {}
    for m in raw["models"]:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        bs = []
        for b in m.get("backends") or []:
            if not isinstance(b, dict):
                continue
            fp = b.get("config") or None
            flag = b.get("partial")
            bs.append({"name": b.get("name"), "route": b.get("route"), "state": b.get("state"),
                       "fingerprint": fp,
                       "fingerprint_kind": fingerprint_kind(fp, marker, flag if isinstance(flag, bool) else None),
                       "observed": b.get("observed"), "mismatch": list(b.get("mismatch") or [])})
        out[str(m["id"])] = {"declared": m.get("declared"), "time": raw.get("time"), "backends": bs}
    return out


def entry_fingerprints(entry: Mapping[str, Any] | None) -> list[str]:
    return [b["fingerprint"] for b in (entry or {}).get("backends") or [] if b.get("fingerprint")]


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
    """One configuration-endpoint read against a judge, over every backend of its model: ``pass``,
    ``transient`` (unreadable, or only partial values on a local judge) or ``mismatch`` (a non-empty
    contradiction list on any backend of a local judge, or any full value other than the pin). A hosted
    judge's read is recorded, not gated (``pass`` when it holds a value; its per-call gate compares the
    per-response value with the freeze-commit set)."""
    e = (entries or {}).get(judge["model"]) if entries is not None else None
    every = list((e or {}).get("backends") or [])
    if judge["class"] == "local":
        # a contradiction list counts on ANY backend, with or without a fingerprint
        for b in every:
            if b.get("mismatch"):
                return "mismatch", f"contradictions:{','.join(map(str, b['mismatch']))}"
    bs = [b for b in every if b.get("fingerprint")]
    if not bs:
        return "transient", "unread"
    if judge["class"] == "hosted":
        return "pass", ""
    fulls = [b["fingerprint"] for b in bs if b["fingerprint_kind"] == "full"]
    other = [f for f in fulls if f != judge["pinned_fingerprint"]]
    if other:
        return "mismatch", f"fingerprint_different:{other[0]}"
    if fulls:
        return "pass", ""
    return "transient", "fingerprint_partial"


def reread(gw: Gateway, judge: Mapping[str, Any], sleep: Callable[[float], None], log: Callable[[dict], None],
           marker: str = PARTIAL_MARKER, why: str = "") -> tuple[str, str]:
    """The pause rule (plan §4): up to :data:`REREAD_TRIES` reads :data:`REREAD_INTERVAL_S` apart.
    ``pass`` on the pinned value; ``mismatch`` at once on a different full value or a contradiction, or
    when EVERY try returned a partial fingerprint on a local judge (the engine stayed unreadable while
    the gateway answered: plan §4's "all 5 reads return a partial value"); otherwise -- any try
    unreadable (no value at all: the serving outage the plan says is not a mismatch) -- ``outage``,
    which pauses the batch (resumable)."""
    reasons = []
    for i in range(1, REREAD_TRIES + 1):
        sleep(REREAD_INTERVAL_S)
        res, reason = check_config_read(judge, gw.read_config(marker))
        log({"event": "reread", "try": i, "result": res, "reason": reason, "why": why})
        if res in ("pass", "mismatch"):
            return res, reason
        reasons.append(reason)
    if judge["class"] == "local" and all(r == "fingerprint_partial" for r in reasons):
        return "mismatch", "transient_sustained:fingerprint_partial"
    return "outage", f"outage_sustained:{','.join(sorted(set(reasons)))}"


def config_check(gw: Gateway, judge: Mapping[str, Any], sleep: Callable[[float], None],
                 log: Callable[[dict], None], marker: str, at: str) -> dict[str, Any]:
    """A batch start or end read, with the transient rule (a pinned configuration). A hosted judge's
    read is recorded only (``result`` ``recorded``): the plan gates the pinned configurations here and
    a hosted judge per call against its freeze-commit set."""
    entries = gw.read_config(marker)
    res, reason = check_config_read(judge, entries)
    e = (entries or {}).get(judge["model"])
    fps = entry_fingerprints(e)
    observed = [b.get("observed") for b in (e or {}).get("backends") or []]
    log({"event": f"config_{at}", "result": res, "reason": reason, "fingerprints": fps, "observed": observed})
    if judge["class"] == "hosted":
        return {"result": "recorded", "reason": reason, "fingerprints": fps, "observed": observed}
    if res == "transient":
        res, reason = reread(gw, judge, sleep, log, marker, why=f"config_{at}")
    return {"result": res, "reason": reason, "fingerprints": fps, "observed": observed}


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
    cap: int | None = None          # answered calls (HTTP 200) allowed in this batch, re-issues included
    answered: int = 0
    counts: Counter = field(default_factory=Counter)
    observed: set = field(default_factory=set)
    last_check_rows: int = 0
    lock: Any = field(default_factory=threading.Lock)        # counters and the cap reservation
    pause_lock: Any = field(default_factory=threading.Lock)  # one pause (re-read sequence) at a time
    halt: Any = field(default_factory=threading.Event)       # set on a mismatch: no new sends

    def count(self, key: str, n: int = 1) -> None:
        with self.lock:
            self.counts[key] += n


def gated_call(ctx: Ctx, judge: Mapping[str, Any], version: str, item: Mapping[str, Any], *, canary: bool,
               observe_hosted: bool = False, n_rows: Callable[[], int] = lambda: 0) -> dict[str, Any]:
    """Send one item through the gate; return the row to store. Outages pause and re-issue (the
    paused item only); a mismatch, a sustained outage, another HTTP error or the call cap raise
    :class:`Stop`. With n = 1 an invalid reply is re-issued once (prereg R4); with n > 1 the invalid
    samples are kept as ``None`` and counted."""
    body, sent, user = build_body(judge, version, item, canary)
    outages = invalid_reissues = 0
    while True:
        if ctx.halt.is_set():
            raise Stop("halted", "mismatch_in_wave")
        with ctx.lock:  # check and reserve one answered-call slot: no overshoot with several in flight
            if ctx.cap is not None and ctx.answered >= ctx.cap:
                raise Stop("cap", f"call_cap:{ctx.cap}")
            ctx.answered += 1
        raw = ctx.gw.chat(body)
        ctx.count("calls")
        if raw.status != 200:  # an outage or an error returns no answer: the slot is released
            with ctx.lock:
                ctx.answered -= 1
        kind, reason = classify_reply(judge, raw, ctx.priv, observe_hosted=observe_hosted, marker=ctx.marker)
        if kind == "mismatch":
            ctx.halt.set()
        if kind == "outage":
            outages += 1
            ctx.count("pauses")
            ctx.log({"event": "pause", "item": item.get("item_id") or item.get("name"), "reason": reason})
            if outages > MAX_OUTAGES_PER_ITEM:  # a pause of the batch (resumable), never a void
                raise Stop("outage", f"outages_per_item:{reason}")
            with ctx.pause_lock:
                res, why = reread(ctx.gw, judge, ctx.sleep, ctx.log, ctx.marker, why=reason)
            ctx.log({"event": "pause_outcome", "result": res, "reason": why})
            if res == "pass":
                ctx.last_check_rows = n_rows()
                continue
            if res == "mismatch":
                ctx.halt.set()
            raise Stop("mismatch" if res == "mismatch" else "outage", why)
        if kind != "ok":
            raise Stop(kind, reason)
        if observe_hosted and judge["class"] == "hosted":
            ctx.observed.add(raw.fingerprint)
        ch = _choices(raw.data)
        if len(ch) != int(sent.get("n", 1)):  # fewer (or more) samples than sent: the vote would change
            raise Stop("error", f"n_served:{len(ch)}/{sent.get('n', 1)}")
        parsed = [_parse(version, (x.get("message") or {}).get("content") or "") for x in ch]
        labels = [p[0] for p in parsed]
        if len(ch) == 1 and labels[0] is None and invalid_reissues == 0:
            invalid_reissues += 1
            ctx.count("invalid_reissues")
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
        "attempts": 1 + invalid_reissues + outages, "n_served": len(ch),
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
            "state": out / f"state-mg-{jid}.json", "stopped": out / f"STOPPED-{jid}", "lock": out / f"LOCK-{jid}"}


#: closed batches whose rows count: done, ended at the cap, and a hosted candidate's mismatch stop
#: (plan §4: "its rows up to the last passing check are kept and dated"; not a void)
KEPT_STATUSES = ("done", "ended:cap")


def _kept(b: Mapping[str, Any]) -> bool:
    return not b.get("void") and (b["status"] in KEPT_STATUSES or str(b["status"]).startswith("stopped:mismatch"))


def valid_rows_report(out: Path, jid: str) -> dict[str, Any]:
    """A judge's usable label rows: a row counts only when its batch has a CLOSED ledger row that keeps
    rows (:data:`KEPT_STATUSES`, or a hosted mismatch stop, which is not void) and it lies outside the
    batch's void range. Rows of open or paused batches (no ledger row yet) are excluded and listed;
    rows of voided ranges are counted, never used."""
    p = paths(out, jid)
    ledger = {b["batch"]: b for b in _rows(p["batches"])}
    keep: list[dict[str, Any]] = []
    open_b: Counter[str] = Counter()
    voided: Counter[str] = Counter()
    for r in _rows(p["labels"]):
        b = ledger.get(r["batch"])
        if b is None:
            open_b[r["batch"]] += 1
        elif b.get("void") and r["seq"] >= int(b.get("void_from_seq") or 0):
            voided[r["batch"]] += 1
        elif _kept(b) or b.get("void"):
            keep.append(r)
    return {"rows": keep, "excluded_open": dict(open_b), "excluded_void": dict(voided)}


def valid_rows(out: Path, jid: str) -> list[dict[str, Any]]:
    return valid_rows_report(out, jid)["rows"]


def _sha(obj: Any) -> str:
    return g.sha_text(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=list))


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


def _canary_identity(canaries: Sequence[Mapping[str, Any]]) -> str:
    keys = ("name", "title", "term", "question", "defs", "passage", "expected_label")
    return _sha([{k: c.get(k) for k in keys} for c in canaries])


class JudgeLock:
    """One process per judge: an ``O_EXCL`` lock file (``LOCK-<judge>``) held while a batch runs. A
    stale lock (a killed process) is removed by the operator after checking the pid it records."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> JudgeLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise SystemExit(f"{self.path.name} exists: another process runs this judge (or a stale lock: "
                             "check the pid inside, then remove it)") from None
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{os.getpid()} {g.now_iso()}\n")
        return self

    def __exit__(self, *exc: Any) -> None:
        self.path.unlink(missing_ok=True)


def run_batch(cfg: Mapping[str, Any], gw: Gateway, jid: str, batch: str, version: str,
              items: Sequence[Mapping[str, Any]], cases: Sequence[Mapping[str, Any]], *,
              sleep: Callable[[float], None] = time.sleep, resume: bool = False) -> dict[str, Any]:
    """One batch under the judge's lock file: :func:`_run_batch`."""
    with JudgeLock(paths(Path(cfg["out_dir"]), jid)["lock"]):
        return _run_batch(cfg, gw, jid, batch, version, items, cases, sleep=sleep, resume=resume)


def _run_batch(cfg: Mapping[str, Any], gw: Gateway, jid: str, batch: str, version: str,
               items: Sequence[Mapping[str, Any]], cases: Sequence[Mapping[str, Any]], *,
               sleep: Callable[[float], None], resume: bool) -> dict[str, Any]:
    """One batch (one judge x one prompt over the items): a configuration read, the start canary,
    the items, the end canary, the end read; the void, cap and epoch rules.

    **A pause is not a void.** A serving outage (unreadable configuration reads, gateway errors, more
    than :data:`MAX_OUTAGES_PER_ITEM` outages on one item), another HTTP error or a short sample count
    PAUSE the batch: its checkpoint keeps the phase, the epoch, the counters and the configuration
    identity (``judge_sha256``, ``canary_sha256``, ``items_sha256``), the stored rows stay, and
    ``resume=True`` continues it under the same batch id after a configuration re-read, sending only the
    unanswered canary cases (rebuilt from the canary file) and items -- also after a process restart. A
    resume, or a later batch of the judge, is refused when the judge's configuration or the canary cases
    differ. Only a closed outcome (done, ended at the cap, a void, a mismatch stop) is written to the
    ledger and spends the batch id.

    **The cap ends the labels phase** (owner decision): once the 110 % answered-call cap of the labels
    phase is reached, no further item is sent; the end canary and the end read still run (so the stored
    rows are vouched for), the ledger row says ``ended:cap`` with the unlabelled count, no STOPPED file is
    written and nothing is voided. A cap-ended batch cannot be resumed; more calls need a dated amendment.

    **Void ranges, deliberately different:** a per-call mismatch on a local judge voids the whole batch
    (from its first row: the identity failed while the batch was open), while a failed END read voids
    only the rows since the last passing configuration check (the rows before it were vouched for by
    that check)."""
    judge = cfg["judges"][jid]
    out = Path(cfg["out_dir"])
    p = paths(out, jid)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", batch):
        raise SystemExit(f"batch id {batch!r}: letters, digits, _ and - only")
    if version not in judge["prompts"]:
        raise SystemExit(f"judge {jid}: prompt {version} is not one of its batches {judge['prompts']}")
    if p["stopped"].exists():
        raise SystemExit(f"judge {jid} is stopped ({p['stopped'].read_text().strip()}): no later batch runs")
    if any(b["batch"] == batch for b in _rows(p["batches"])):
        raise SystemExit(f"judge {jid}: batch {batch!r} already closed (a batch is never re-issued)")
    if judge["class"] == "hosted" and not judge["freeze_set"]:
        raise SystemExit(f"judge {jid} (hosted): no freeze_set (recorded in the freeze commit)")
    canaries = canary_cases(cfg, cases)
    expected = {c["name"]: c["expected_label"] for c in canaries}
    judge_sha, canary_sha = _sha(judge), _canary_identity(canaries)
    ck_path = out / f"checkpoint-mg-{jid}-{batch}.json"
    others = [x.name for x in out.glob(f"checkpoint-mg-{jid}-*.json") if x != ck_path]
    if others:
        raise SystemExit(f"judge {jid}: another batch is paused ({others}); resume or close it first")
    items_sha = _sha([dict(i) for i in items])
    state: dict[str, Any] = (json.loads(p["state"].read_text()) if p["state"].exists()
                             else {"epoch": 0, "last_end_canary": None})
    for k, v in (("judge_sha256", judge_sha), ("canary_sha256", canary_sha)):
        if state.get(k) not in (None, v):
            raise SystemExit(f"judge {jid}: {k} differs from the previous batch's (the configuration or the "
                             "canary cases changed); a change needs a dated amendment")
    labels_now = len(_rows(p["labels"]))
    if ck_path.exists():
        if not resume:
            raise SystemExit(f"judge {jid}: batch {batch!r} is paused; pass --resume to continue it")
        ck = json.loads(ck_path.read_text())
        for k, v in (("version", version), ("items_sha256", items_sha), ("judge_sha256", judge_sha),
                     ("canary_sha256", canary_sha)):
            if ck[k] != v:
                raise SystemExit(f"judge {jid}: batch {batch!r} resumed with a different {k}")
        ck["resumes"] += 1
    else:
        if resume:
            raise SystemExit(f"judge {jid}: batch {batch!r} has no paused checkpoint to resume")
        ck = {"batch": batch, "judge": jid, "version": version, "items_sha256": items_sha,
              "judge_sha256": judge_sha, "canary_sha256": canary_sha, "canaries": list(expected),
              "phase": "start", "epoch": state["epoch"], "epoch_boundary": None,
              "differ_from_previous_end": None, "seq0": labels_now, "last_check_seq": labels_now, "calls": 0,
              "answered": 0, "pauses": 0, "invalid_reissues": 0, "resumes": 0, "capped": False,
              "started": g.now_iso(), "config_reads": []}
    done_ids = {r["item_id"] for r in _rows(p["labels"]) if r["batch"] == batch}
    session_rows: list[dict[str, Any]] = []
    planned = len(items) + 2 * N_CANARIES
    cap_total = math.floor(planned * float(cfg["cap_factor"]))
    ctx = Ctx(gw=gw, priv=gw.priv, marker=cfg["partial_marker"], sleep=sleep,
              log=lambda e: _append(p["events"], {"judge": jid, "batch": batch, "t": g.now_iso(), **e}),
              answered=int(ck["answered"]))

    def n_rows() -> int:
        return labels_now + len(session_rows)

    def canary_got(at: str) -> dict[str, Any]:
        """The canary labels already stored for this batch and phase, from the canary file itself."""
        return {r["item_id"]: _label_of(r) for r in _rows(p["canary"])
                if r["batch"] == batch and r["kind"] == f"canary_{at}"}

    def save() -> None:
        with ctx.lock:
            ck.update({"calls": ck["calls"] + ctx.counts["calls"], "pauses": ck["pauses"] + ctx.counts["pauses"],
                       "invalid_reissues": ck["invalid_reissues"] + ctx.counts["invalid_reissues"],
                       "last_check_seq": max(int(ck["last_check_seq"]), ctx.last_check_rows),
                       "answered": ctx.answered})
            ctx.counts.clear()
        g.write_json_atomic(ck_path, ck)

    def summary(status: str, **extra: Any) -> dict[str, Any]:
        stored = sum(1 for r in _rows(p["labels"]) if r["batch"] == batch)
        reads = ck["config_reads"]
        obs_start = reads[0].get("observed") if reads else None
        obs_end = reads[-1].get("observed") if reads else None
        return {"batch": batch, "judge": jid, "judge_version": version, "items": len(items),
                "planned_calls": planned, "cap": cap_total, "void": False, "epoch": ck["epoch"],
                "epoch_boundary": ck["epoch_boundary"], "differ_from_previous_end": ck["differ_from_previous_end"],
                "canary_setting": sampling_sent(judge, canary=True), "started": ck["started"], "status": status,
                "stored": stored, "unlabelled": len(items) - stored, "calls": ck["calls"],
                "answered": ck["answered"], "pauses": ck["pauses"], "invalid_reissues": ck["invalid_reissues"],
                "resumes": ck["resumes"], "judge_sha256": judge_sha, "canary_sha256": canary_sha,
                "config_reads": reads, "observed_changed": obs_start != obs_end, "ended": g.now_iso(), **extra}

    def finish(status: str, **extra: Any) -> dict[str, Any]:
        save()
        row = summary(status, **extra)
        _append(p["batches"], row)
        ck_path.unlink(missing_ok=True)
        return row

    def stop(e: Stop, at: str) -> dict[str, Any]:
        if e.kind == "mismatch":
            # local: the batch stops and is void (from its first row); hosted: that candidate stops,
            # its rows kept
            local = judge["class"] == "local"
            p["stopped"].write_text(f"{batch}:{at}:{e.reason}\n")
            return finish(f"stopped:mismatch:{e.reason}", void=local,
                          void_from_seq=ck["seq0"] if local else None, stopped_at=at)
        # a pause: resumable under the same batch id
        ck["paused"] = {"kind": e.kind, "reason": e.reason, "at": at, "t": g.now_iso()}
        save()
        ctx.log({"event": "batch_paused", "kind": e.kind, "reason": e.reason, "at": at})
        return summary(f"paused:{e.kind}:{e.reason}", stopped_at=at)

    def canary(at: str) -> None:
        got = canary_got(at)
        for c in canaries:
            if c["name"] in got:
                continue
            row = gated_call(ctx, judge, CANARY_VERSION, c, canary=True, n_rows=n_rows)
            row.update({"judge": jid, "batch": batch, "kind": f"canary_{at}", "epoch": ck["epoch"]})
            _append(p["canary"], row)
            save()

    def read(at: str) -> dict[str, Any]:
        r = config_check(gw, judge, sleep, ctx.log, ctx.marker, at)
        ck["config_reads"].append({"at": at, **r})
        return r

    def read_failed(r: Mapping[str, Any], at: str) -> Stop | None:
        if r["result"] in ("pass", "recorded"):
            return None
        return Stop("mismatch" if r["result"] == "mismatch" else "outage", r["reason"])

    det = judge["canary"]["mode"] == "deterministic"
    at = "start" if not ck["resumes"] else f"resume{ck['resumes']}"
    ck.pop("paused", None)
    bad = read_failed(read(at), at)
    if bad is not None:
        return stop(bad, f"config_{at}")
    ctx.last_check_rows = n_rows()  # a passing check: the rows before it are vouched for
    try:
        if ck["phase"] == "start":
            canary("start")
            start_c = canary_got("start")
            boundary, d_prev = epoch_boundary(state["last_end_canary"], start_c)
            ck.update({"epoch": state["epoch"] + (1 if boundary else 0), "epoch_boundary": boundary,
                       "differ_from_previous_end": d_prev, "phase": "labels"})
            _retag_epoch(p["canary"], batch, "canary_start", ck["epoch"])
            save()
        if ck["phase"] == "labels":
            ctx.cap = max(0, cap_total - N_CANARIES)  # the labels phase; the end canary always runs
            todo = [it for it in items if it["item_id"] not in done_ids]
            nk = int(judge["max_in_flight"])
            with ThreadPoolExecutor(max_workers=nk) as pool:
                for w in range(0, len(todo), nk):
                    wave = todo[w:w + nk]
                    futs = [pool.submit(gated_call, ctx, judge, version, it, canary=False, n_rows=n_rows)
                            for it in wave]
                    stops: list[Stop] = []
                    for f in futs:
                        try:
                            row = f.result()
                        except Stop as e:
                            stops.append(e)
                            continue
                        row.update({"judge": jid, "batch": batch, "kind": "label", "epoch": ck["epoch"],
                                    "seq": n_rows(), "k": f"{jid}|{batch}|{row['item_id']}"})
                        _append(p["labels"], row)
                        session_rows.append(row)
                    save()
                    real = [e for e in stops if e.kind not in ("cap", "halted")]
                    if real:  # a mismatch outranks a pause
                        raise next((e for e in real if e.kind == "mismatch"), real[0])
                    if any(e.kind == "cap" for e in stops):
                        ck["capped"] = True
                        ctx.log({"event": "cap_reached", "cap": ctx.cap})
                        break
            ctx.cap = None
            ck["phase"] = "end"
            save()
        canary("end")
    except Stop as e:
        return stop(e, ck["phase"])
    end_c = canary_got("end")
    cmp = compare_canaries(canary_got("start"), end_c, expected, det)
    end = read("end")
    bad = read_failed(end, "end")
    if bad is not None and bad.kind == "outage":  # a serving outage at the end read: pause (phase "end")
        return stop(bad, "config_end")
    state.update({"epoch": ck["epoch"], "last_end_canary": end_c, "judge_sha256": judge_sha,
                  "canary_sha256": canary_sha})
    g.write_json_atomic(p["state"], state)
    if bad is not None:
        p["stopped"].write_text(f"{batch}:config_end:{end['reason']}\n")
        return finish(f"void:config_end:{end['reason']}", canary=cmp, void=True,
                      void_from_seq=int(ck["last_check_seq"]))
    if cmp["void"]:
        drift = judge["class"] == "local"  # the fingerprint matched at the end read
        p["stopped"].write_text(f"{batch}:canary:{','.join(cmp['reasons'])}\n")
        return finish("void:canary", canary=cmp, void=True, void_from_seq=ck["seq0"],
                      drift_report="template_parser_or_default_drift" if drift else None)
    return finish("ended:cap" if ck["capped"] else "done", canary=cmp)


def _retag_epoch(path: Path, batch: str, kind: str, epoch: int) -> None:
    """Set the epoch on a batch's rows of one kind (the start canary is written before the epoch
    boundary is known); an atomic rewrite."""
    rows = _rows(path)
    for r in rows:
        if r.get("batch") == batch and r.get("kind") == kind:
            r["epoch"] = epoch
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, path)


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
    requested = list(judge_ids or cfg["judges"])
    unfinished = [j for j in requested if j not in labels]
    res["selection_judges"] = sorted(labels)
    if unfinished:  # plan §4: the canaries are stable on EVERY model-gateway judge
        res["selection"] = None
        res["selection_reason"] = f"no selection: self-check not finished on {unfinished}"
    else:
        res["selection"] = select_canaries(labels, [c["name"] for c in cases], n_runs=runs)
        res["selection_reason"] = None
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
    lb.add_argument("--resume", action="store_true", help="continue a paused batch under the same id")
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
        if res["selection_reason"]:
            echo(res["selection_reason"])
        echo(json.dumps({k: res[k] for k in ("selection", "selection_judges")}, sort_keys=True))
        return 0
    res = run_batch(cfg, gw, a.judge, a.batch, a.version, _items(a.items), sc.load_cases(a.cases), sleep=sleep,
                    resume=a.resume)
    echo(json.dumps({k: res.get(k) for k in ("status", "stored", "void", "epoch", "calls")}, sort_keys=True))
    return 0 if res["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
