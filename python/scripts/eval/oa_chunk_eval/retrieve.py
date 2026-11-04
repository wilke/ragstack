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
body field except the query) are stored in every row, so rows fetched at different times
(e.g. the noise subset reused by the main run) can be checked for a server change.

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
not. A rerun skips (question, arm) pairs that already have an ``ok`` row. Pause file,
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


def done_pairs(cfg: dict[str, Any], arm: str) -> set[str]:
    return {r["question_id"] for r in iter_jsonl(ledger_path(cfg, arm)) if r.get("status") == "ok"}


def latest_ok(cfg: dict[str, Any], arm: str) -> dict[str, dict[str, Any]]:
    """question_id -> the first ok row of that arm (later duplicates are ignored)."""
    out: dict[str, dict[str, Any]] = {}
    for r in iter_jsonl(ledger_path(cfg, arm)):
        if r.get("status") == "ok" and "question_id" in r:
            out.setdefault(r["question_id"], r)
    return out


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
        done = {a["name"]: done_pairs(cfg, a["name"]) for a in all_arms}

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
            + ", ".join(f"{a} {len(d)} already done" for a, d in done.items()))
    finally:
        if own:
            await http.aclose()
    return (75 if control.reason else 0), control


def run(cfg: dict[str, Any], subset: str | None = None, only_arms: list[str] | None = None) -> int:
    code, control = asyncio.run(run_async(cfg, subset, only_arms))
    if control.reason:
        log(f"retrieve: stopped ({control.reason}); rerun to resume")
    return code
