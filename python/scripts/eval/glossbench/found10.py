#!/usr/bin/env python
"""Glossary benchmark round 2: the found@10 runner (prereg §5 M1, §8; plan step 12).

Runs one **arm** on one **corpus** through the served retrieval API
(``POST /v1/retrieve``) under the conditions ``base`` (hybrid + rerank),
``bm25`` and ``norerank``, and writes, per record and condition, the rank of
every gold chunk to the served depth and ``found@10``. The arm is attached to
the index **outside** this runner (the backfill, driven by ``swap.sh``); the
runner only proves which arm is attached and refuses to measure otherwise.

    found10.py run     --private-config CFG --questions Q.jsonl --corpus O --arm qwen36p \\
                       --swap-record SWAP.json --out DIR [--repeat 2]
    found10.py analyse --private-config CFG --questions Q.jsonl [Q2.jsonl ...] --out DIR \\
                       [--proxy PROXY.jsonl] [--results results.json]

Nothing private is in this file: base URL, collection ids, index names, expected
chunk counts, stamps and glossary-file hashes come from ``--private-config``
(see ``examples/found10-config.example.json``); the API key, if any, from the
environment variable the config names.

**Inputs from the sibling tools (one record schema, no adapters).** Questions
are the drafter's accepted records (``glossbench_r2_draft.py summary`` →
``accepted-<set>.jsonl``), which are also what ``wording_proxy.py --records``
reads: ``record_id``, ``set`` (the corpus alias; ``corpus`` is accepted as a
synonym), ``paper``, ``stratum``, ``question``, ``gold_chunks`` (the
quote-bearing chunk ids) — plus the proxy's ``binding_terms`` / ``quantity``
and the drafter's ``quote`` / ``value`` / ``symbol`` / ``doc_id`` /
``source_chunk_id`` / ``flags``, which this runner carries but does not read.
The proxy outcome (``analyse --proxy``) is the proxy tool's own results JSON
(``schema wording-proxy/1``, ``per_record[record_id][arm]["pass"]``).

**Stamps** are the backfill's, which the glossary tools make distinct per arm:
``<version>@file/1`` for a file loaded as is (``rules/3@file/1``); a re-derived
file (``glossary_file.py rederive --merge <mode>``) marks the version
``<base>+merge=<mode>`` BEFORE ``@file/1`` (the pure arm:
``llm/2:<model>:…+merge=llm-only@file/1``; a document with no LLM version at
all: ``none+merge=llm-only@file/1``); the shuffled control
(``backfill … --shuffle-rendered SEED``) appends ``+shuf/1:<seed>:<perm12>``
AFTER ``@file/1``. An arm's config says what its stamps must look like —
``stamps`` / ``stamp_prefixes`` for the base version, ``merge`` (a mode or
``null`` = no marker) and ``shuffle_seed`` (a seed or ``null`` = no suffix) —
and :func:`stamp_allowed` checks all three parts, so a pure arm's file loaded
unmarked, a merged file passed off as pure, or a true index passed off as the
shuffled one is refused on the stamp itself, not only on the swap record.

**Which arm is attached (refuse otherwise).** Before the first call:

1. the swap record (written by ``swap.sh`` after the backfill's apply and
   census) must name this arm and this corpus's index, show a post-apply census
   whose total equals the corpus's expected chunk count and whose every
   ``metadata.symbol_glossary_version`` stamp is allowed as above (an unstamped
   chunk never is), a glossary-file sha256 equal to the arm's for this corpus,
   the arm's shuffle seed (``null`` for a true arm) — which must also be the seed
   the backfill's own ``shuffle`` record names, with every census stamp ending
   in that record's ``stamp_suffix`` — and an apply (``dry_run false``) whose
   ``chunks_written`` equals its ``chunks_to_update``;
2. optionally (``census.es_url`` in the config) the runner reads the same
   census from the index itself, read-only (one ``terms`` aggregation), before
   the first call and after the last: both must equal the swap record's.
   Without it the summary says ``census_source: swap_record``;
3. during the run, every hit of the conditions in ``hit_stamp_conditions``
   (default ``bm25``: those hits come from the text index, which the backfill
   writes) must carry an allowed stamp; one that does not stops the run.
   Hybrid hits may carry the vector store's payload stamp, which the backfill
   does not touch, so they are counted, not enforced.

**Depth read-back.** Each condition requests ``top_k`` (default 50, with
``rerank_candidates`` 50 for ``base``: the server's pool depth is
``max(top_k, rerank_candidates)`` = 50, each leg fetches ``top_k ×
retrieval_candidate_multiplier`` = 2 × 50, RRF (k 60) fuses, the pool is cut to
50 and reranked, then cut to ``top_k``: prereg §5's pipeline) and the runner
records the number of sources actually served. A served depth below the
requested one marks that arm/condition **GATE-NOT-EVALUABLE** (its records
stay in the file; the analysis does not test it). A response with a
``degraded`` entry (e.g. the reranker fell back to RRF order) is not the
condition: it is retried and, if still degraded, recorded as a failed call.
Two things the API does not expose are **assumed and recorded, not read
back**: the per-leg multiplier (2) and the RRF constant (60) are server
settings absent from ``/v1/config``; the operator pins them from the tenant
env in the private config (``server_assumed``) and the summary repeats them.
A reranker that is wired but inactive answers ``rerank: true`` silently with
the RRF order, so after the run ``base`` is gated when its ranked list equals
``norerank``'s on every record.

**Glossary boost read-back** (:mod:`glossary_boost_probe`). The BM25 glossary
field boost (``TEXT_SYMBOL_GLOSSARY_BOOST``, default 0: the field is then never
consulted and every arm collapses) is not in ``/v1/config`` either, so before
the first measurement call the runner probes it behaviourally: up to 5 chunks
whose rendered field holds a token found in no chunk's content are queried in
``bm25`` mode with that token alone (``top_k`` 10); the boost is active iff
each chunk comes back. Candidates need ``--chunk-export`` plus the arm's
``--glossary-file`` (rendered offline as the backfill renders; a shuffled arm's
permutation is reproduced and hash-checked against the swap record) or
``--rendered-fields``. None returned → exit 1 (``inactive``); some → exit 1
(``mixed``); either way ``<label>.boost-check.json`` records the probes (token
hashes, never text). Fewer than 3 qualifying chunks → ``not_testable``: every
condition is GATE-NOT-EVALUABLE unless ``--boost-declared X``, then recorded
as ``declared``. The summary carries it under ``manipulation_check``.

**Failures, pause, resume.** A transport error, 5xx or degraded response is
retried (``api.retries``, backoff); a 4xx is a configuration error and stops
the run (exit 1). A record whose call still fails is ``failed`` (excluded
from its contrasts and counted). More than 2% failed calls is a hard stop
(prereg §8). A ``PAUSE`` file in the output directory or SIGTERM stops issuing
calls, lets in-flight calls finish and exits 75; re-running the same command
resumes (rows are append-only, one fsync per row, keyed
``record|condition``). At most 4 requests in flight (hard cap).

Exit codes: 0 complete, 1 refused / hard stop, 75 paused.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import signal
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

_HERE = Path(__file__).resolve().parent
_EVAL = _HERE.parent
for _p in (_HERE, _EVAL):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import found10_analysis as fa  # noqa: E402
import glossary_boost_probe as gbp  # noqa: E402
from deployed_docs_part4 import (  # noqa: E402
    append_fsync,
    read_jsonl_repair_tail,
    write_json_atomic,
)

EXIT_OK, EXIT_REFUSED, EXIT_PAUSED = 0, 1, 75
MAX_INFLIGHT = 4
FOUND_K = 10
SWAP_SCHEMA = "glossbench-swap/1"
ROW_SCHEMA = "glossbench-r2-found10-row/1"
SUMMARY_SCHEMA = "glossbench-r2-found10-run/1"
PROXY_SCHEMA = "wording-proxy/1"
VERSION_KEY = "symbol_glossary_version"
#: the glossary tools' stamp grammar (ragstack.ingestion.glossary_file / glossary_shuffle):
#: ``<base>[+merge=<mode>]@file/1[+shuf/1:<seed>:<12 hex>]``
FILE_STAMP = "@file/1"
MERGE_MARK = "+merge="
SHUF_MARK = "+shuf/1:"
MERGE_MODES = ("llm-only", "rules-only", "merged", "none")
_SHUF_TAIL = re.compile(r"\+shuf/1:(?P<seed>[A-Za-z0-9|._+-]{1,64}):(?P<perm>[0-9a-f]{12})")
#: the record fields this runner reads (the drafter writes them; the proxy reads the same file)
RECORD_FIELDS = ("record_id", "paper", "stratum", "question", "gold_chunks")
#: server settings prereg §5 relies on that /v1/config does not expose: pinned by the operator
DEFAULT_SERVER_ASSUMED = {"retrieval_candidate_multiplier": 2, "rrf_k": 60}

DEFAULT_CONDITIONS: dict[str, dict[str, Any]] = {
    "base": {"retrieval_mode": "hybrid", "rerank": True, "rerank_candidates": 50, "top_k": 50},
    "bm25": {"retrieval_mode": "bm25", "rerank": False, "top_k": 50},
    "norerank": {"retrieval_mode": "hybrid", "rerank": False, "top_k": 50},
}
#: fixed on every request so a server default cannot change the condition
FIXED_REQUEST = {"use_graph": False, "rewrite_strategies": ["passthrough"], "context_window": 0,
                 "exclude_boilerplate": False}
DEFAULT_API = {"timeout_s": 120.0, "inflight": 4, "retries": 3, "retry_backoff_s": 2.0, "api_key_env": None}
MAX_FAILED_SHARE = 0.02


class Refused(Exception):  # noqa: N818 - a control-flow signal
    """The runner will not measure: the attached arm is not the declared one, or the config is wrong."""


# --------------------------------------------------------------------------- config / inputs
def load_config(path: Path) -> dict[str, Any]:
    return normalise_config(json.loads(path.read_text(encoding="utf-8")))


def normalise_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Defaults merged in, required keys checked. Raises :class:`Refused`."""
    cfg = copy.deepcopy(cfg)
    for key in ("api", "corpora", "arms"):
        if key not in cfg:
            raise Refused(f"private config has no {key!r} block")
    if not cfg["api"].get("base_url"):
        raise Refused("api.base_url is required (no default: defaults on this kind of host resolve to production)")
    cfg["api"] = {**DEFAULT_API, **cfg["api"]}
    conds = cfg.get("conditions") or {}
    cfg["conditions"] = {c: {**DEFAULT_CONDITIONS[c], **conds.get(c, {})} for c in DEFAULT_CONDITIONS}
    for c, spec in cfg["conditions"].items():
        if int(spec["top_k"]) < FOUND_K:
            raise Refused(f"condition {c}: top_k {spec['top_k']} < {FOUND_K}")
    cfg.setdefault("hit_stamp_conditions", ["bm25"])
    cfg["server_assumed"] = {**DEFAULT_SERVER_ASSUMED, **(cfg.get("server_assumed") or {})}
    for arm, acfg in cfg["arms"].items():
        mode = acfg.get("merge")
        if mode is not None and mode not in MERGE_MODES:
            raise Refused(f"arms.{arm}.merge {mode!r} is not one of {MERGE_MODES} or null")
    return cfg


def load_questions(paths: Sequence[Path]) -> list[dict[str, Any]]:
    """The drafter's accepted records (one JSONL line each): ``record_id``, ``set``
    (the corpus alias — ``corpus`` is accepted as a synonym and ``corpus`` is what
    the record carries from here on), ``paper``, ``stratum``, ``question`` and
    ``gold_chunks``, the quote-bearing chunk id(s) of the 512 index (prereg §3.3
    gold width; never empty). Other fields (``binding_terms``, ``quantity``,
    ``quote``, …) are kept untouched. Record ids are unique across all files."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in paths:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            r = json.loads(line)
            if "gold_chunk_ids" in r and "gold_chunks" not in r:
                raise Refused(f"{p.name}:{i}: record names 'gold_chunk_ids'; the field is 'gold_chunks' "
                              "(the drafter's and the wording proxy's name)")
            for k in RECORD_FIELDS:
                if k not in r:
                    raise Refused(f"{p.name}:{i}: record lacks {k!r}")
            corpus = r.get("corpus", r.get("set"))
            if not isinstance(corpus, str) or not corpus:
                raise Refused(f"{p.name}:{i}: record lacks 'set' (or 'corpus')")
            if not isinstance(r["gold_chunks"], list) or not r["gold_chunks"] \
                    or not all(isinstance(x, str) and x for x in r["gold_chunks"]):
                raise Refused(f"{p.name}:{i}: gold_chunks must be a non-empty list of ids")
            if r["record_id"] in seen:
                raise Refused(f"{p.name}:{i}: duplicate record_id {r['record_id']!r}")
            seen.add(r["record_id"])
            out.append({**r, "corpus": corpus})
    return out


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def arm_label(arm: str, repeat: int | None) -> str:
    return arm if not repeat or repeat == 1 else f"{arm}#{repeat}"


# --------------------------------------------------------------------------- census
def split_stamp(stamp: str) -> tuple[str, str | None, tuple[str, str] | None] | None:
    """``<base>[+merge=<mode>]@file/1[+shuf/1:<seed>:<perm12>]`` → ``(base, mode,
    (seed, perm))``; ``None`` when the stamp is not a backfill file stamp."""
    base, sep, tail = stamp.partition(FILE_STAMP)
    if not sep or not base:
        return None
    mode = None
    if MERGE_MARK in base:
        base, _, mode = base.rpartition(MERGE_MARK)
        if mode not in MERGE_MODES or not base:
            return None
    shuf = None
    if tail:
        m = _SHUF_TAIL.fullmatch(tail)
        if m is None:
            return None
        shuf = (m.group("seed"), m.group("perm"))
    return base, mode, shuf


def stamp_allowed(stamp: str | None, arm_cfg: Mapping[str, Any], shuffle_suffix: str | None = None) -> bool:
    """Every part of the stamp is the arm's: the base version is an exact ``stamps``
    entry or starts with a ``stamp_prefixes`` entry; the merge marker is the arm's
    ``merge`` (a mode, or ``null`` = no marker; an arm config without the key
    constrains nothing); the shuffle suffix is present with the arm's
    ``shuffle_seed`` (and equals ``shuffle_suffix``, the backfill's own
    ``stamp_suffix``, when given) or absent for a true arm. ``None`` /
    ``"<none>"`` (an unstamped chunk) and anything not in the stamp grammar is
    never allowed."""
    if not stamp or stamp == "<none>":
        return False
    parts = split_stamp(stamp)
    if parts is None:
        return False
    base, mode, shuf = parts
    exact = arm_cfg.get("stamps") or []
    prefixes = [p for p in (arm_cfg.get("stamp_prefixes") or []) if p]
    if not (stamp in exact or base in exact or any(base.startswith(p) for p in prefixes)):
        return False
    if "merge" in arm_cfg and mode != arm_cfg["merge"]:
        return False
    seed = arm_cfg.get("shuffle_seed")
    if seed is None:
        return shuf is None
    if shuf is None or shuf[0] != seed:
        return False
    return shuffle_suffix is None or stamp.endswith(shuffle_suffix)


def verify_swap_record(record: Mapping[str, Any], cfg: Mapping[str, Any], corpus: str, arm: str) -> dict[str, Any]:
    """Every check of item 1 in the module docstring. Returns the verified census
    ``{total, stamps}``; raises :class:`Refused` listing every failed check."""
    problems: list[str] = []
    ccfg = cfg["corpora"].get(corpus)
    acfg = cfg["arms"].get(arm)
    if ccfg is None:
        raise Refused(f"corpus {corpus!r} is not in the private config")
    if acfg is None:
        raise Refused(f"arm {arm!r} is not in the private config")
    if not (acfg.get("stamps") or acfg.get("stamp_prefixes")):
        raise Refused(f"arm {arm!r} declares no stamps / stamp_prefixes")
    if record.get("schema") != SWAP_SCHEMA:
        problems.append(f"swap record schema {record.get('schema')!r} != {SWAP_SCHEMA!r}")
    if record.get("arm") != arm:
        problems.append(f"swap record is for arm {record.get('arm')!r}, not {arm!r}")
    if record.get("index") != ccfg.get("index"):
        problems.append("swap record index != the corpus's configured index")
    after = record.get("census_after") or {}
    stamps = after.get("stamps")
    total = after.get("total")
    if not isinstance(stamps, dict) or not isinstance(total, int):
        problems.append("swap record has no census_after {total, stamps}")
        stamps, total = {}, None
    exp = ccfg.get("expected_chunks")
    if exp is None:
        problems.append(f"corpora.{corpus}.expected_chunks is not set")
    elif total != exp:
        problems.append(f"census total {total} != expected chunks {exp}")
    if stamps and sum(stamps.values()) != total:
        problems.append(f"census stamp counts sum {sum(stamps.values())} != total {total}")
    apply = record.get("apply") or {}
    if apply.get("dry_run") is not False:
        problems.append("swap record has no applied backfill (apply.dry_run is not false)")
    if apply.get("errors") or apply.get("failed"):
        problems.append(f"backfill apply reported errors={apply.get('errors')} failed={apply.get('failed')}")
    if "chunks_written" in apply and apply.get("chunks_written") != apply.get("chunks_to_update"):
        problems.append(f"backfill apply wrote {apply.get('chunks_written')} of "
                        f"{apply.get('chunks_to_update')} planned chunks")
    gf_apply = (apply.get("glossary_file") or {}).get("sha256")
    gf_rec = (record.get("glossary_file") or {}).get("sha256")
    if gf_apply and gf_rec and gf_apply != gf_rec:
        problems.append("the backfill rendered a file other than the one swap.sh hashed")
    gf_sha = gf_apply or gf_rec
    want_sha = (acfg.get("glossary_file_sha256") or {}).get(corpus)
    if not want_sha:
        problems.append(f"arms.{arm}.glossary_file_sha256.{corpus} is not set")
    elif gf_sha != want_sha:
        problems.append("glossary file sha256 of the swap != the arm's declared file")
    seed = acfg.get("shuffle_seed")
    if record.get("shuffle_seed") != seed:
        problems.append(f"shuffle seed {record.get('shuffle_seed')!r} != the arm's {seed!r}")
    shuffle = apply.get("shuffle")
    suffix: str | None = None
    if seed is not None:
        if not isinstance(shuffle, dict) or shuffle.get("seed") != seed:
            problems.append(f"the backfill's own shuffle record names seed {None if not shuffle else shuffle.get('seed')!r}, "
                            f"not {seed!r}")
        else:
            suffix = str(shuffle.get("stamp_suffix") or f"{SHUF_MARK}{seed}:{str(shuffle.get('permutation_sha256'))[:12]}")
    elif shuffle:
        problems.append("the backfill applied a shuffle but the arm is a true (unshuffled) arm")
    bad = sorted(s for s in stamps if not stamp_allowed(s, acfg, suffix))
    if bad:
        problems.append(f"census carries stamps the arm does not allow: {bad}")
    if problems:
        raise Refused("census check failed: " + "; ".join(problems))
    return {"total": total, "stamps": dict(stamps), "shuffle_suffix": suffix}


async def live_census(client: httpx.AsyncClient, es_url: str, index: str) -> dict[str, Any]:
    """Read-only: one ``terms`` aggregation on the stamp, ``size 0``."""
    body = {"size": 0, "track_total_hits": True,
            "aggs": {"v": {"terms": {"field": f"metadata.{VERSION_KEY}", "size": 100, "missing": "<none>"}}}}
    r = await client.post(f"{es_url.rstrip('/')}/{index}/_search", json=body)
    r.raise_for_status()
    d = r.json()
    return {"total": int(d["hits"]["total"]["value"]),
            "stamps": {b["key"]: int(b["doc_count"]) for b in d["aggregations"]["v"]["buckets"]}}


# --------------------------------------------------------------------------- one call
def request_body(cond: Mapping[str, Any], question: str, collection: str) -> dict[str, Any]:
    return {"query": question, "collection": collection, **FIXED_REQUEST, **dict(cond)}


def score_response(sources: Sequence[Mapping[str, Any]], gold: Sequence[str], requested: int) -> dict[str, Any]:
    """Ranks (1-based, first occurrence) of each gold chunk within the served list,
    ``None`` beyond it; ``found10`` = any gold chunk at rank ≤ 10; the served depth
    and whether it is short of the requested depth."""
    ids = [str(s.get("chunk_id")) for s in sources]
    first: dict[str, int] = {}
    for i, c in enumerate(ids, 1):
        first.setdefault(c, i)
    ranks = {g: first.get(g) for g in gold}
    best = min((r for r in ranks.values() if r is not None), default=None)
    stamps = Counter(str((s.get("metadata") or {}).get(VERSION_KEY, "<none>")) for s in sources)
    return {"served": len(ids), "requested": int(requested), "depth_short": len(ids) < int(requested),
            "depth_over": len(ids) > int(requested), "gold_ranks": ranks, "best_rank": best,
            "found10": int(best is not None and best <= FOUND_K), "ranked_chunk_ids": ids,
            "hit_stamps": dict(stamps)}


class Runner:
    """One (corpus, arm[, repeat]) run. ``client`` is injectable (tests use
    ``httpx.MockTransport``)."""

    def __init__(self, cfg: dict[str, Any], records: list[dict[str, Any]], corpus: str, arm: str,
                 out: Path, swap_record_path: Path, repeat: int | None = None,
                 client_factory: Callable[[], httpx.AsyncClient] | None = None,
                 sleep: Callable[[float], Any] = asyncio.sleep,
                 probe_chunks: Sequence[tuple[str, str, str]] | None = None,
                 probe_unavailable: str | None = None, probe_source: str | None = None,
                 boost_declared: float | None = None) -> None:
        self.cfg, self.corpus, self.arm = cfg, corpus, arm
        self.label = arm_label(arm, repeat)
        self.records = [r for r in records if r["corpus"] == corpus]
        if not self.records:
            raise Refused(f"no records for corpus {corpus!r}")
        ccfg = cfg["corpora"].get(corpus)
        if not ccfg or not ccfg.get("collection"):
            raise Refused(f"corpora.{corpus}.collection is not set")
        self.ccfg: dict[str, Any] = ccfg
        self.acfg = cfg["arms"].get(arm) or {}
        self.out_dir = out / corpus
        self.rows_path = self.out_dir / f"{self.label}.jsonl"
        self.summary_path = self.out_dir / f"{self.label}.summary.json"
        self.pause_path = out / "PAUSE"
        self.swap_record_path = swap_record_path
        api = cfg["api"]
        self.inflight = max(1, min(MAX_INFLIGHT, int(api["inflight"])))
        self.retries = int(api["retries"])
        self.backoff = float(api["retry_backoff_s"])
        self._client_factory = client_factory or self._default_client
        self._sleep = sleep
        self.stop_reason: str | None = None
        self.hard_stop: str | None = None
        self.config_error: str | None = None
        self.shuffle_suffix: str | None = None
        # the glossary-boost read-back (glossary_boost_probe): candidates are
        # (chunk_id, content, the rendered field the index holds for this arm)
        self.probe_chunks = probe_chunks
        self.probe_unavailable = probe_unavailable
        self.probe_source = probe_source
        self.boost_declared = boost_declared
        if boost_declared is not None and boost_declared <= 0:
            raise Refused(f"--boost-declared {boost_declared:g}: a boost <= 0 never consults the glossary field")
        self.boost_check: dict[str, Any] | None = None

    def _default_client(self) -> httpx.AsyncClient:
        api = self.cfg["api"]
        headers = {"Content-Type": "application/json"}
        env = api.get("api_key_env")
        if env:
            key = os.environ.get(env)
            if not key:
                raise Refused(f"api.api_key_env names {env!r}, which is not set")
            headers["X-API-Key"] = key
        return httpx.AsyncClient(timeout=float(api["timeout_s"]), headers=headers)

    # ---- one retrieval with retries
    async def _call(self, client: httpx.AsyncClient, rec: Mapping[str, Any], cond: str) -> dict[str, Any]:
        spec = self.cfg["conditions"][cond]
        body = request_body(spec, rec["question"], self.ccfg["collection"])
        url = f"{self.cfg['api']['base_url'].rstrip('/')}/v1/retrieve"
        last_err = None
        degraded = None
        for attempt in range(1, self.retries + 2):
            t0 = time.monotonic()
            try:
                r = await client.post(url, json=body)
            except httpx.HTTPError as e:
                last_err = f"{type(e).__name__}: {str(e)[:200]}"
            else:
                rid = r.headers.get("X-Request-Id")
                if 400 <= r.status_code < 500:
                    raise Refused(f"HTTP {r.status_code} on /v1/retrieve (request id {rid}): "
                                  f"{r.text[:300]} - a configuration error, not a retrieval failure")
                if r.status_code >= 500:
                    last_err = f"HTTP {r.status_code} (request id {rid})"
                else:
                    d = r.json()
                    degraded = d.get("degraded")
                    if degraded:
                        last_err = f"degraded: {degraded}"
                    else:
                        sc = score_response(d.get("sources") or [], rec["gold_chunks"], int(spec["top_k"]))
                        return {"failed": False, "attempts": attempt, "request_id": rid,
                                "latency_ms": round(1000 * (time.monotonic() - t0), 1), **sc}
            if attempt <= self.retries:
                await self._sleep(self.backoff * attempt)
        return {"failed": True, "attempts": self.retries + 1, "error": last_err, "degraded": degraded,
                "found10": None, "gold_ranks": None, "best_rank": None, "served": None,
                "requested": int(spec["top_k"]), "depth_short": False, "depth_over": False,
                "hit_stamps": {}}

    def _row(self, rec: Mapping[str, Any], cond: str, res: dict[str, Any]) -> dict[str, Any]:
        return {"schema": ROW_SCHEMA, "key": f"{rec['record_id']}|{cond}", "record_id": rec["record_id"],
                "corpus": rec["corpus"], "paper": rec["paper"], "stratum": rec["stratum"], "arm": self.arm,
                "run_label": self.label, "condition": cond, "utc": now_iso(), **res}

    def _check_hit_stamps(self, cond: str, res: Mapping[str, Any]) -> None:
        if cond not in self.cfg["hit_stamp_conditions"] or res.get("failed"):
            return
        bad = sorted(s for s in res.get("hit_stamps", {}) if not stamp_allowed(s, self.acfg, self.shuffle_suffix))
        if bad:
            self.hard_stop = (f"a served {cond} hit carries stamp(s) {bad} that arm {self.arm!r} does not allow: "
                              "the index does not hold the declared arm")

    async def run(self) -> int:
        swap = json.loads(self.swap_record_path.read_text(encoding="utf-8"))
        verified = verify_swap_record(swap, self.cfg, self.corpus, self.arm)
        self.shuffle_suffix = verified["shuffle_suffix"]
        census = {"total": verified["total"], "stamps": verified["stamps"]}
        self.out_dir.mkdir(parents=True, exist_ok=True)
        done = {r["key"]: r for r in read_jsonl_repair_tail(self.rows_path)} if self.rows_path.exists() else {}
        census_cfg = self.cfg.get("census") or {}
        live_before = live_after = None
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGTERM, self._sigterm)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - non-main thread / platform
            pass
        async with self._client_factory() as client:
            if census_cfg.get("es_url"):
                live_before = await live_census(client, census_cfg["es_url"], self.ccfg["index"])
                if live_before != census:
                    raise Refused(f"live census {live_before} != the swap record's {census}")
            server = await self._server_info(client)
            self.boost_check = await self._boost_probe(client)
            if self.boost_check["status"] in gbp.STOP:
                write_json_atomic(self.out_dir / f"{self.label}.boost-check.json",
                                  {"schema": "glossbench-r2-boost-check/1", "corpus": self.corpus,
                                   "arm": self.arm, "run_label": self.label, "utc": now_iso(),
                                   "manipulation_check": {"glossary_boost": self.boost_check}})
                raise Refused(f"glossary boost check: {self.boost_check['status']} - "
                              f"{self.boost_check['reason']} (nothing measured; "
                              f"{self.label}.boost-check.json has the probes)")
            jobs = [(rec, cond) for rec in self.records for cond in self.cfg["conditions"]
                    if f"{rec['record_id']}|{cond}" not in done]
            sem = asyncio.Semaphore(self.inflight)

            async def one(rec: Mapping[str, Any], cond: str) -> None:
                if self.stop_reason or self.hard_stop or self.config_error:
                    return
                async with sem:
                    if self.pause_path.exists():
                        self.stop_reason = self.stop_reason or "PAUSE file present"
                    if self.stop_reason or self.hard_stop or self.config_error:
                        return
                    try:
                        res = await self._call(client, rec, cond)
                    except Refused as e:
                        self.config_error = self.config_error or str(e)
                        return
                    self._check_hit_stamps(cond, res)
                    row = self._row(rec, cond, res)
                    append_fsync(self.rows_path, row)
                    done[row["key"]] = row

            await asyncio.gather(*(one(r, c) for r, c in jobs))
            if self.config_error:
                raise Refused(self.config_error)
            if census_cfg.get("es_url") and not self.stop_reason:
                live_after = await live_census(client, census_cfg["es_url"], self.ccfg["index"])
                if live_after != census:
                    self.hard_stop = (self.hard_stop or
                                      f"the index changed during the run: census {live_after} != {census}")
        return self._finish(done, swap, census, live_before, live_after, server)

    async def _probe_call(self, client: httpx.AsyncClient, token: str
                          ) -> tuple[dict[str, Any] | None, str | None]:
        """One read-only bm25 probe query, retried like a measurement call; a 4xx is
        a configuration error."""
        body = request_body(gbp.PROBE_REQUEST, token, self.ccfg["collection"])
        url = f"{self.cfg['api']['base_url'].rstrip('/')}/v1/retrieve"
        last = None
        for attempt in range(1, self.retries + 2):
            try:
                r = await client.post(url, json=body)
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}"
            else:
                if 400 <= r.status_code < 500:
                    raise Refused(f"HTTP {r.status_code} on the boost probe (request id "
                                  f"{r.headers.get('X-Request-Id')}) - a configuration error")
                if r.status_code < 500:
                    return r.json(), None
                last = f"HTTP {r.status_code}"
            if attempt <= self.retries:
                await self._sleep(self.backoff * attempt)
        return None, last

    async def _boost_probe(self, client: httpx.AsyncClient) -> dict[str, Any]:
        """The behavioural read-back of TEXT_SYMBOL_GLOSSARY_BOOST, before the first
        measurement call (at most ``inflight`` probes in flight)."""
        probes, ready = gbp.plan(self.probe_chunks, declared=self.boost_declared,
                                 unavailable=self.probe_unavailable, source=self.probe_source)
        if not probes:
            assert ready is not None
            return ready
        sem = asyncio.Semaphore(self.inflight)

        async def one(p: Mapping[str, Any]) -> dict[str, Any]:
            async with sem:
                resp, err = await self._probe_call(client, p["token"])
            return gbp.judge(p, resp, err)

        results = list(await asyncio.gather(*(one(p) for p in probes)))
        return gbp.finish(probes, results, ready or {})

    def _sigterm(self) -> None:
        self.stop_reason = self.stop_reason or "SIGTERM"

    async def _server_info(self, client: httpx.AsyncClient) -> dict[str, Any]:
        """Read-only GETs recorded for provenance: ``/v1/version`` and (admin only, best
        effort) ``/v1/config``."""
        info: dict[str, Any] = {}
        base = self.cfg["api"]["base_url"].rstrip("/")
        for path in ("/v1/version", "/v1/config"):
            try:
                r = await client.get(base + path)
                info[path] = r.json() if r.status_code == 200 else {"status": r.status_code}
            except (httpx.HTTPError, ValueError) as e:
                info[path] = {"error": type(e).__name__}
        return info

    def _finish(self, done: dict[str, dict[str, Any]], swap: Mapping[str, Any], census: Mapping[str, Any],
                live_before: Any, live_after: Any, server: Mapping[str, Any]) -> int:
        from ragstack.provenance import experiment_provenance

        prov = experiment_provenance()
        per_cond: dict[str, Any] = {}
        n_calls = n_failed = 0
        expected = len(self.records)
        for cond in self.cfg["conditions"]:
            rows = [r for r in done.values() if r["condition"] == cond]
            ok = [r for r in rows if not r.get("failed")]
            short = sum(bool(r.get("depth_short")) for r in ok)
            over = sum(bool(r.get("depth_over")) for r in ok)
            stamps: Counter[str] = Counter()
            for r in ok:
                stamps.update(r.get("hit_stamps") or {})
            n_calls += len(rows)
            n_failed += len(rows) - len(ok)
            gate = None
            if short or over:
                gate = (f"{fa.GATE}: served depth differs from the requested {self.cfg['conditions'][cond]['top_k']} "
                        f"on {short + over} call(s) (short {short}, over {over})")
            per_cond[cond] = {
                "request": request_body(self.cfg["conditions"][cond], "<question>", "<collection>"),
                "records": expected, "rows": len(rows), "ok": len(ok), "failed": len(rows) - len(ok),
                "found10": sum(int(r["found10"]) for r in ok),
                "served_min": min((r["served"] for r in ok), default=None),
                "served_max": max((r["served"] for r in ok), default=None),
                "requested": int(self.cfg["conditions"][cond]["top_k"]),
                "depth_short": short, "depth_over": over, "hit_stamps": dict(stamps),
                "hit_stamps_enforced": cond in self.cfg["hit_stamp_conditions"], "gate": gate}
        complete = all(per_cond[c]["rows"] == expected for c in per_cond)
        # a reranker that is wired but inactive answers `rerank: true` with the RRF
        # order: `base` identical to `norerank` on every record is not the condition
        if complete and {"base", "norerank"} <= set(per_cond):
            by_key = {(r["record_id"], r["condition"]): r for r in done.values() if not r.get("failed")}
            pairs = [(by_key.get((rid, "base")), by_key.get((rid, "norerank"))) for rid in {k[0] for k in by_key}]
            pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
            same = sum(x.get("ranked_chunk_ids") == y.get("ranked_chunk_ids") for x, y in pairs)
            per_cond["base"]["identical_to_norerank"] = {"records": len(pairs), "identical": same}
            if pairs and same == len(pairs) and not per_cond["base"]["gate"]:
                per_cond["base"]["gate"] = (f"{fa.GATE}: the base ranking equals the norerank ranking on every "
                                            f"record ({same}/{len(pairs)}): the reranker was not active")
        # the glossary boost could not be read back and was not declared: every
        # condition has a BM25 leg, so none is the pre-registered condition
        bgate = (self.boost_check or {}).get("gate")
        if bgate:
            for c in per_cond.values():
                c["gate"] = c["gate"] or f"{fa.GATE}: {bgate}"
        failed_share = n_failed / n_calls if n_calls else 0.0
        if complete and failed_share > MAX_FAILED_SHARE and not self.hard_stop:
            self.hard_stop = f"{n_failed}/{n_calls} retrieval calls failed (> {MAX_FAILED_SHARE:.0%})"
        status = ("hard_stop" if self.hard_stop else "paused" if self.stop_reason and not complete
                  else "complete" if complete else "incomplete")
        summary = {
            "schema": SUMMARY_SCHEMA, "status": status, "reason": self.hard_stop or self.stop_reason,
            "corpus": self.corpus, "arm": self.arm, "run_label": self.label,
            "collection": self.ccfg["collection"], "index": self.ccfg.get("index"),
            "found_k": FOUND_K, "conditions": per_cond, "calls": n_calls, "failed_calls": n_failed,
            "failed_share": round(failed_share, 5),
            "server_assumed": {**self.cfg["server_assumed"],
                               "note": "not exposed by /v1/config; pinned by the operator from the server's env"},
            "census": {"verified": census, "source": "swap_record+live" if live_before is not None else "swap_record",
                       "live_before": live_before, "live_after": live_after,
                       "swap_record_sha256": sha256_file(self.swap_record_path),
                       "swap_utc": {k: swap.get(k) for k in ("utc_start", "utc_end")},
                       "glossary_file_sha256": (self.acfg.get("glossary_file_sha256") or {}).get(self.corpus),
                       "shuffle_seed": self.acfg.get("shuffle_seed"), "shuffle_stamp_suffix": self.shuffle_suffix,
                       "merge": self.acfg.get("merge"), "allowed_stamps": self.acfg.get("stamps"),
                       "allowed_stamp_prefixes": self.acfg.get("stamp_prefixes")},
            "manipulation_check": {"glossary_boost": self.boost_check},
            "server": server, "utc": now_iso(), "provenance": prov,
            "citable": bool(prov.get("dirty") is False and prov.get("untracked") == 0),
        }
        write_json_atomic(self.summary_path, summary)
        print(json.dumps({k: summary[k] for k in ("status", "reason", "corpus", "run_label", "calls",
                                                  "failed_calls")}), flush=True)
        if status == "hard_stop":
            return EXIT_REFUSED
        if status in ("paused", "incomplete"):
            return EXIT_PAUSED
        return EXIT_OK


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- analysis I/O
def collect(out: Path, records: Sequence[Mapping[str, Any]], proxy: Path | None = None
            ) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, str], dict[str, Any]]:
    """Read every ``<out>/<corpus>/<label>.summary.json`` and its rows into
    ``per_record[rid][label] = {"found": {cond: 0/1/None}, "proxy": 0/1/None}``.

    Only ``complete`` runs are read; any other status is listed and the label's
    arm/condition is gated for that corpus. A condition the run marked gated
    (depth read-back) is gated. ``proxy`` is the wording proxy's results JSON
    (``schema wording-proxy/1``): ``per_record[record_id][arm]["pass"]``."""
    by_id = {r["record_id"]: r for r in records}
    per_record: dict[str, dict[str, dict[str, Any]]] = {rid: {} for rid in by_id}
    gated: dict[str, str] = {}
    runs: dict[str, Any] = {}
    for sp in sorted(out.glob("*/*.summary.json")):
        s = json.loads(sp.read_text(encoding="utf-8"))
        key = f"{s['corpus']}/{s['run_label']}"
        runs[key] = {"status": s["status"], "reason": s.get("reason"), "citable": s.get("citable"),
                     "summary_sha256": sha256_file(sp),
                     "commit": (s.get("provenance") or {}).get("commit"),
                     "census": s.get("census", {}).get("verified")}
        if s["status"] != "complete":
            for cond in fa.CONDITIONS:
                gated[f"{s['run_label']}|{cond}"] = f"{fa.GATE}: run {key} is {s['status']}"
            continue
        for cond, c in s["conditions"].items():
            if c.get("gate"):
                gated[f"{s['run_label']}|{cond}"] = f"{key}: {c['gate']}"
        rows_path = sp.with_name(sp.name.replace(".summary.json", ".jsonl"))
        runs[key]["rows_sha256"] = sha256_file(rows_path)
        for r in read_jsonl_repair_tail(rows_path):
            if r["record_id"] not in by_id:
                continue
            slot = per_record[r["record_id"]].setdefault(s["run_label"], {"found": {}, "proxy": None})
            slot["found"][r["condition"]] = None if r.get("failed") else int(r["found10"])
    if proxy is not None:
        px = json.loads(proxy.read_text(encoding="utf-8"))
        if not isinstance(px, dict) or px.get("schema") != PROXY_SCHEMA or not isinstance(px.get("per_record"), dict):
            raise Refused(f"{proxy.name}: not a wording-proxy results file (schema {PROXY_SCHEMA!r} with per_record)")
        for rid, arms in px["per_record"].items():
            if rid not in per_record:
                continue
            for arm, j in arms.items():
                v = j.get("pass") if isinstance(j, dict) else None
                per_record[rid].setdefault(arm, {"found": {}, "proxy": None})["proxy"] = (
                    None if v is None else int(bool(v)))
    return per_record, gated, runs


def probe_candidates(cfg: Mapping[str, Any], corpus: str, arm: str, swap: Mapping[str, Any],
                     chunk_export: str | None, glossary_file: str | None, rendered_fields: str | None
                     ) -> tuple[list[tuple[str, str, str]] | None, str | None, str | None]:
    """``(chunk_id, content, rendered field)`` for every chunk of the index, for the
    boost probe: content from the chunk export (``export_chunks.py``, which never
    exports the glossary), the field either rendered offline from the arm's
    glossary file exactly as the backfill renders it (the file's sha256 must be
    the arm's; a shuffled arm's permutation is reproduced over the backfill's
    keys, ``corpora.<c>.es_id_prefix`` + chunk id, and must hash to the swap
    record's) or read from an export of rendered fields ``{chunk_id,
    symbol_glossary}``. Returns ``(None, reason, None)`` when not available. The
    swap record itself carries counts only, no rendered field."""
    import wording_proxy as wp

    if not chunk_export:
        return None, "no --chunk-export given", None
    if not (glossary_file or rendered_fields):
        return None, "--chunk-export without --glossary-file or --rendered-fields", None
    rows = read_jsonl_repair_tail(Path(chunk_export))
    try:
        if rendered_fields:
            fields = wp.fields_from_export(Path(rendered_fields))
            source = "chunk export + rendered-fields export"
        else:
            assert glossary_file is not None
            want = ((cfg["arms"].get(arm) or {}).get("glossary_file_sha256") or {}).get(corpus)
            if sha256_file(Path(glossary_file)) != want:
                raise Refused("--glossary-file is not the arm's declared glossary file (sha256 differs)")
            fields, _ = wp.fields_from_glossary_file(Path(glossary_file), rows, allow_unverifiable=bool(
                (cfg.get("probe") or {}).get("glossary_allow_unverifiable")))
            source = "chunk export + glossary file rendered offline (the backfill's rendering)"
            seed = (cfg["arms"].get(arm) or {}).get("shuffle_seed")
            if seed is not None:
                perm = ((swap.get("apply") or {}).get("shuffle") or {}).get("permutation_sha256")
                # the backfill keys its shuffle by Elasticsearch ``_id`` = ``<tenant>:<chunk_id>``
                # (stores/elasticsearch.py ``_es_id``), so the prefix is the index's tenant + ``:``
                prefix = (cfg["corpora"].get(corpus) or {}).get("es_id_prefix")
                if not prefix:
                    raise gbp.ProbeUnavailable(f"shuffled arm: corpora.{corpus}.es_id_prefix is not set (the "
                                               "backfill keys the permutation by ES _id, '<tenant>:' + chunk id)")
                fields = gbp.fields_with_shuffle(fields, str(seed), prefix, perm)
                source += " + the backfill's shuffle, reproduced and hash-verified"
    except wp.ProxyInputError as e:
        raise Refused(f"boost probe candidates: {e}") from e
    except gbp.ProbeUnavailable as e:
        return None, str(e), None
    return [(str(r["chunk_id"]), str(r.get("content") or ""), fields.get(str(r["chunk_id"]), ""))
            for r in rows], None, source


def cmd_run(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(Path(args.private_config))
        records = load_questions([Path(args.questions)])
        swap = json.loads(Path(args.swap_record).read_text(encoding="utf-8"))
        chunks, unavailable, source = probe_candidates(cfg, args.corpus, args.arm, swap, args.chunk_export,
                                                       args.glossary_file, args.rendered_fields)
        runner = Runner(cfg, records, args.corpus, args.arm, Path(args.out), Path(args.swap_record),
                        repeat=args.repeat, probe_chunks=chunks, probe_unavailable=unavailable,
                        probe_source=source, boost_declared=args.boost_declared)
        return asyncio.run(runner.run())
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr, flush=True)
        return EXIT_REFUSED


def cmd_analyse(args: argparse.Namespace) -> int:
    from ragstack.provenance import experiment_provenance

    cfg = json.loads(Path(args.private_config).read_text(encoding="utf-8"))
    records = load_questions([Path(q) for q in args.questions])
    out = Path(args.out)
    per_record, gated, runs = collect(out, records, Path(args.proxy) if args.proxy else None)
    result = fa.analyse(records, per_record, cfg.get("analysis"), gated, power=not args.no_power,
                        power_sims=args.power_sims)
    prov = experiment_provenance()
    result["runs"] = runs
    result["inputs_sha256"] = {Path(q).name: sha256_file(Path(q)) for q in args.questions}
    if args.proxy:
        result["inputs_sha256"][Path(args.proxy).name] = sha256_file(Path(args.proxy))
    result["provenance"] = prov
    result["citable"] = bool(prov.get("dirty") is False and prov.get("untracked") == 0
                             and all(r.get("citable") for r in runs.values()))
    dst = Path(args.results) if args.results else out / "results-found10.json"
    write_json_atomic(dst, result)
    fam = result["family"]["hypotheses"]
    print(json.dumps({h: {"status": t.get("status"), "p": t.get("p"), "p_holm": t.get("p_holm"),
                          "net_share": t.get("net_share"), "n": t.get("n")} for h, t in fam.items()}, indent=1))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="measure one arm on one corpus through the served API")
    r.add_argument("--private-config", required=True)
    r.add_argument("--questions", required=True, help="the drafter's accepted-<set>.jsonl")
    r.add_argument("--corpus", required=True, help="corpus alias (the records' `set`), e.g. O or A")
    r.add_argument("--arm", required=True, help="arm name as in the private config")
    r.add_argument("--swap-record", required=True, help="the swap.sh record of the arm now attached")
    r.add_argument("--out", required=True)
    r.add_argument("--repeat", type=int, default=None, help="repeat number (2 = the noise-floor repeat)")
    r.add_argument("--chunk-export", default=None,
                   help="export_chunks.py output (every chunk of the index): the boost probe's content side")
    r.add_argument("--glossary-file", default=None,
                   help="the arm's glossary file (sha256 must be the arm's): fields rendered offline for the probe")
    r.add_argument("--rendered-fields", default=None,
                   help="instead of --glossary-file: an export of rendered fields {chunk_id, symbol_glossary}")
    r.add_argument("--boost-declared", type=float, default=None,
                   help="the server's TEXT_SYMBOL_GLOSSARY_BOOST, used only when the probe is not testable "
                        "(recorded as declared, not read back)")
    r.set_defaults(func=cmd_run)
    a = sub.add_parser("analyse", help="paired contrasts, Holm, NI, power from the run files")
    a.add_argument("--private-config", required=True)
    a.add_argument("--questions", required=True, nargs="+")
    a.add_argument("--out", required=True, help="the run directory")
    a.add_argument("--proxy", default=None, help="the wording proxy's results JSON (wording_proxy.py --out)")
    a.add_argument("--results", default=None)
    a.add_argument("--no-power", action="store_true")
    a.add_argument("--power-sims", type=int, default=2000)
    a.set_defaults(func=cmd_analyse)
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
