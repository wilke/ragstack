"""``retrieve``: every question x arm against ``POST {api.base_url}{api.endpoint}``.

Request (contracts/openapi.yaml ``RetrieveRequest``)::

    {"query": <question>, "top_k": <api.top_k>, "collection": <arm.collection_id>, **api.request_extra}

``request_extra`` passes any other documented RetrieveRequest field through; it cannot
override query/top_k/collection. It MUST name ``rerank``, ``use_graph`` and
``retrieval_mode`` explicitly (RetrieveRequest field names in contracts/openapi.yaml), so a
server default change, or graph pseudo-chunks, cannot slip in between the noise run and the
main run; ``retrieve`` refuses to start without them. The key is read from
``api.api_key_file`` at call time and sent only as ``X-API-Key``.

Server identity: ``GET {api.base_url}/v1/version`` once per run (``VersionResponse``:
version, git_tag, git_sha, started_at, impl). The result and the request settings (every
body field except the query, :func:`request_settings`) are stored in every row. An ``ok``
row counts (as done on resume, and as usable by ``score``/``noise``) only when its
``request`` equals :func:`request_settings` for the current config and the arm's
collection; ok rows fetched with other settings are ignored and counted. A row without a
``request`` (written before the field existed) cannot be checked: it is accepted and
counted as unverified. The server version is NOT a criterion here: rows from different
server versions are accepted; ``score`` records the versions it used per arm and warns
when they are mixed.

Dormant collections: a 503 that carries ``Retry-After`` (seconds) is not a failure: the
call waits that long (capped at ``api.max_retry_after_s``, 120) and retries, up to
``api.max_503_waits`` (5) times; only then is it recorded as ``http_503``.

Response (``RetrieveResponse``): ``sources[]`` of ``Source`` = ``doc_id``, ``chunk_id``,
``content``, ``score``, ``metadata``. ``source_path`` is not a Source field: it is read from
``metadata.source_path`` (the JSONL record ``path``, set by enrich), falling back to
``metadata.filename``. ``start_char``/``end_char`` are read from ``metadata`` too (the API
attaches them there only when ``end_char > start_char``). ``degraded`` (present only when
a stage fell back) makes the call count as a failure unless ``api.accept_degraded`` is
true: the contract says a degraded response must not be compared with undegraded ones.

``retrieval/<arm>.jsonl`` is the append-only ledger of that arm: one row per call, ok or
not. A rerun skips (question, arm) pairs that already have a usable ``ok`` row (see above). Pause file,
failure stops and max 4 in flight as in :mod:`runner`; one Control spans all arms. Calls
are interleaved: for each question every arm is called before the next question, so slow
drift on the server (load, reranker, cache) does not line up with arm.

Questions: ``questions.jsonl`` and, when present, the check-writer set
``questions.check.jsonl`` (its question ids end in ``:check``; scored as a separate
secondary report). ``--subset noise`` restricts the run to the noise questions and the two
noise arms.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .common import iter_jsonl, log, read_jsonl
from .config import ConfigError, in_flight, out_path, pause_path, read_key, require
from .config import arms as cfg_arms
from .runner import Control, prior_failures, run_pool

_RESERVED = ("query", "top_k", "collection", "collections")
#: RetrieveRequest fields that must be pinned in api.request_extra (contracts/openapi.yaml)
PINNED = ("rerank", "use_graph", "retrieval_mode")
VERSION_ENDPOINT = "/v1/version"
_VERSION_FIELDS = ("version", "git_tag", "git_sha", "started_at", "impl")

#: indirection so tests can skip the Retry-After wait
_sleep = asyncio.sleep


def check_pinned(cfg: dict[str, Any]) -> None:
    """Refuse unless api.request_extra names rerank, use_graph and retrieval_mode."""
    extra = cfg["api"].get("request_extra") or {}
    missing = [k for k in PINNED if k not in extra]
    if missing:
        raise ConfigError("api.request_extra must set " + ", ".join(missing)
                          + " explicitly (RetrieveRequest fields; pins the server behaviour across runs)")


def request_settings(cfg: dict[str, Any], collection_id: str) -> dict[str, Any]:
    """The request body without the query: what every row records."""
    body = request_body(cfg, "", collection_id)
    body.pop("query", None)
    return body


def ledger_path(cfg: dict[str, Any], arm: str) -> Path:
    return out_path(cfg, "retrieval", f"{arm}.jsonl")


def request_body(cfg: dict[str, Any], question: str, collection_id: str) -> dict[str, Any]:
    body = {k: v for k, v in (cfg["api"].get("request_extra") or {}).items() if k not in _RESERVED}
    body.update({"query": question, "top_k": int(cfg["api"]["top_k"]), "collection": collection_id})
    return body


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse_sources(data: Any) -> list[dict[str, Any]]:
    """RetrieveResponse -> ranked hits in the harness shape."""
    out = []
    for rank, s in enumerate((data or {}).get("sources") or [], start=1):
        md = s.get("metadata") or {}
        out.append({"rank": rank, "doc_id": s.get("doc_id"), "chunk_id": s.get("chunk_id"),
                    "source_path": md.get("source_path") or md.get("filename") or "",
                    "content": s.get("content") or "", "score": s.get("score"),
                    "start_char": _int_or_none(md.get("start_char")),
                    "end_char": _int_or_none(md.get("end_char"))})
    return out


def endpoint_url(cfg: dict[str, Any]) -> str:
    return cfg["api"]["base_url"].rstrip("/") + "/" + cfg["api"]["endpoint"].lstrip("/")


def _headers(cfg: dict[str, Any]) -> dict[str, str]:
    headers = {}
    key = read_key(cfg["api"].get("api_key_file"))
    if key:
        headers["X-API-Key"] = key
    return headers


async def server_version(http: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    """``GET /v1/version`` once; the VersionResponse identity fields, or an error marker."""
    url = cfg["api"]["base_url"].rstrip("/") + VERSION_ENDPOINT
    try:
        r = await http.get(url, headers=_headers(cfg), timeout=float(cfg["api"].get("timeout_s") or 120))
        if r.status_code != 200:
            return {"error": f"http_{r.status_code}"}
        data = r.json()
        return {k: data.get(k) for k in _VERSION_FIELDS} if isinstance(data, dict) else {"error": "bad_json"}
    except Exception as e:  # noqa: BLE001 - recorded, not fatal
        return {"error": type(e).__name__}


def _retry_after(r: Any) -> float | None:
    try:
        v = (r.headers or {}).get("Retry-After")
        return float(v) if v is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


async def call_retrieve(http: Any, cfg: dict[str, Any], question: dict[str, Any], arm: dict[str, Any],
                        version: dict[str, Any] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"question_id": question["question_id"], "arm": arm["name"],
                           "collection_id": arm["collection_id"], "top_k": int(cfg["api"]["top_k"]),
                           "server_version": version, "request": request_settings(cfg, arm["collection_id"])}
    max_waits = int(cfg["api"].get("max_503_waits", 5))
    cap = float(cfg["api"].get("max_retry_after_s", 120))
    waits = 0
    while True:
        try:
            r = await http.post(endpoint_url(cfg), json=request_body(cfg, question["question"], arm["collection_id"]),
                                headers=_headers(cfg), timeout=float(cfg["api"].get("timeout_s") or 120))
        except Exception as e:  # noqa: BLE001
            return {**row, "status": f"error:{type(e).__name__}", "waits_503": waits}
        ra = _retry_after(r) if r.status_code == 503 else None
        if ra is not None and waits < max_waits:
            waits += 1
            await _sleep(max(0.0, min(ra, cap)))
            continue
        break
    if waits:
        row["waits_503"] = waits
    if r.status_code != 200:
        return {**row, "status": f"http_{r.status_code}"}
    try:
        data = r.json()
    except ValueError:
        return {**row, "status": "bad_json"}
    degraded = data.get("degraded") if isinstance(data, dict) else None
    hits = parse_sources(data)
    row.update({"hits": hits, "request_id": r.headers.get("X-Request-Id")})
    if degraded:
        row["degraded"] = [d.get("component") for d in degraded if isinstance(d, dict)]
        if not cfg["api"].get("accept_degraded"):
            return {**row, "status": "degraded"}
    return {**row, "status": "ok"}


def request_check(row: dict[str, Any], want: dict[str, Any]) -> str:
    """``match``, ``mismatch``, or ``unverified`` (the row predates the ``request`` field)."""
    got = row.get("request")
    if got is None:
        return "unverified"
    return "match" if got == want else "mismatch"


def _arm_dict(cfg: dict[str, Any], arm: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(arm, dict):
        return arm
    for a in cfg_arms(cfg):
        if a["name"] == arm:
            return a
    raise ConfigError(f"unknown arm {arm!r}")


def usable_ok(cfg: dict[str, Any], arm: dict[str, Any] | str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """``(question_id -> first usable ok row, stats)`` for one arm's ledger.

    Usable: ``status == "ok"`` and ``request`` equal to :func:`request_settings` for the
    current config and the arm's collection, or no ``request`` at all (unverifiable, kept
    so old ledgers still score). Later duplicates are ignored. ``stats``: ``mismatch_rows``
    (ok rows ignored for a different request), ``mismatch_only`` (question ids whose ok
    rows ALL mismatch) and ``unverified`` (question ids whose chosen row has no request)."""
    a = _arm_dict(cfg, arm)
    want = request_settings(cfg, a["collection_id"])
    out: dict[str, dict[str, Any]] = {}
    mismatched: set[str] = set()
    mismatch_rows = 0
    for r in iter_jsonl(ledger_path(cfg, a["name"])):
        if r.get("status") != "ok" or "question_id" not in r:
            continue
        if request_check(r, want) == "mismatch":
            mismatch_rows += 1
            mismatched.add(r["question_id"])
            continue
        out.setdefault(r["question_id"], r)
    unverified = {q for q, r in out.items() if r.get("request") is None}
    return out, {"mismatch_rows": mismatch_rows, "mismatch_only": mismatched - set(out), "unverified": unverified}


def done_pairs(cfg: dict[str, Any], arm: dict[str, Any] | str) -> set[str]:
    """Question ids of the arm that have a usable ok row (:func:`usable_ok`)."""
    return set(usable_ok(cfg, arm)[0])


def latest_ok(cfg: dict[str, Any], arm: dict[str, Any] | str) -> dict[str, dict[str, Any]]:
    """question_id -> the first usable ok row of that arm (:func:`usable_ok`)."""
    return usable_ok(cfg, arm)[0]


def run_questions(cfg: dict[str, Any], subset: str | None) -> list[dict[str, Any]]:
    """Main questions, plus the check-writer set unless the run is the noise subset."""
    questions = read_jsonl(out_path(cfg, "questions.jsonl"))
    if subset == "noise":
        from .noise import noise_questions
        return noise_questions(cfg, questions)
    return questions + read_jsonl(out_path(cfg, "questions.check.jsonl"))


async def run_async(cfg: dict[str, Any], subset: str | None = None, only_arms: list[str] | None = None,
                    http: Any = None) -> tuple[int, Control]:
    require(cfg, "out_dir", "api.base_url", "api.endpoint", "api.top_k")
    check_pinned(cfg)
    all_arms = cfg_arms(cfg)
    questions = run_questions(cfg, subset)
    if subset == "noise":
        from .noise import noise_arms
        names = noise_arms(cfg)
        all_arms = [a for a in all_arms if a["name"] in names]
    if only_arms:
        all_arms = [a for a in all_arms if a["name"] in set(only_arms)]
    stop = cfg["stop"]
    prior = sum(prior_failures(ledger_path(cfg, a["name"])) for a in all_arms)
    control = Control(stop["max_failures"], stop["max_consecutive"], pause_path(cfg), prior)
    own = http is None
    if own:
        import httpx
        http = httpx.AsyncClient()
    try:
        version = await server_version(http, cfg)
        if "error" in version:
            log(f"retrieve: GET {VERSION_ENDPOINT} failed ({version['error']}); recorded in every row")
        else:
            log(f"retrieve: server version {version.get('version')} ({version.get('git_sha')})")
        done: dict[str, set[str]] = {}
        ignored: dict[str, int] = {}
        unverified: dict[str, int] = {}
        for a in all_arms:
            ok, st = usable_ok(cfg, a)
            done[a["name"]] = set(ok)
            ignored[a["name"]] = st["mismatch_rows"]
            unverified[a["name"]] = len(st["unverified"])
        if any(ignored.values()):
            log("retrieve: ignored ok rows fetched with a different request than the current config: "
                + ", ".join(f"{a} {n}" for a, n in ignored.items() if n) + " (refetched)")
        if any(unverified.values()):
            log("retrieve: WARNING: ok rows without a recorded request (unverifiable, counted as done): "
                + ", ".join(f"{a} {n}" for a, n in unverified.items() if n))

        def job_for(q: dict[str, Any], arm: dict[str, Any]):
            async def job() -> dict[str, Any]:
                try:
                    return await call_retrieve(http, cfg, q, arm, version)
                except Exception as e:  # noqa: BLE001 - keep the arm so the row lands in its ledger
                    return {"question_id": q["question_id"], "arm": arm["name"], "status": f"error:{type(e).__name__}"}
            return job

        def jobs():
            for q in questions:  # interleaved: every arm for this question, then the next question
                for arm in all_arms:
                    if q["question_id"] not in done[arm["name"]]:
                        yield job_for(q, arm)

        started = await run_pool(jobs(), control, in_flight(cfg["api"]), lambda row: ledger_path(cfg, row["arm"]))
        log(f"retrieve: {started} calls this run, "
            + ", ".join(f"{a} {len(d)} already done ({ignored[a]} ignored for request mismatch)"
                        for a, d in done.items()))
    finally:
        if own:
            await http.aclose()
    return (75 if control.reason else 0), control


def run(cfg: dict[str, Any], subset: str | None = None, only_arms: list[str] | None = None) -> int:
    code, control = asyncio.run(run_async(cfg, subset, only_arms))
    if control.reason:
        log(f"retrieve: stopped ({control.reason}); rerun to resume")
    return code
