"""Behavioural read-back of the BM25 glossary field boost (glossary benchmark
round 2 and the reranker test: the manipulation check ``/v1/config`` cannot do).

``TEXT_SYMBOL_GLOSSARY_BOOST`` (default **0.0**) is the weight of the
``symbol_glossary`` field on the BM25 leg. At 0 the field is not consulted at
all, so every glossary arm collapses to the same BM25 ranking and a glossary
experiment is void. ``/v1/config`` does not expose the setting, and this
check does not change the API: it observes the setting's effect instead.

**The probe.** Pick chunks whose rendered glossary (what the index holds for
the arm now attached) carries a *distinctive token*: ASCII alphanumeric with at
least one letter, length >= 5, a whole whitespace-delimited word (surrounding
punctuation stripped, so the standard analyzer cannot split or join it
differently), that occurs in **no** chunk's content (so content BM25 cannot
match it) and in at most :data:`PROBE_TOP_K` chunks' glossaries (so every
glossary match fits in the served list). Query ``retrieval_mode bm25``,
``rerank false``, ``top_k`` :data:`PROBE_TOP_K` with that token alone. With the
boost active the chunk is returned (the token matches its glossary field and
nothing's content); with the boost at 0 the query matches no content, so the
chunk cannot be returned.

**Verdict.** Every probe returns its chunk -> ``active``. None does ->
``inactive`` (the harness stops, exit 1). Some do -> ``mixed`` (stop and
report). A probe whose call failed, came back ``degraded``, served more than
the requested depth, or echoed a ``top_k`` below the requested one ->
``error`` (stop). (``/v1/retrieve`` echoes no ``top_k`` today -- the response
schema is closed -- so the echo branch is defensive; the served count is the
read-back. A clamp below the requested depth makes the probe FAIL, never pass:
the chunk is missing, which is the conservative direction.) Fewer than
:data:`MIN_PROBES` qualifying chunks -> ``not_testable``: the boost-dependent
arms are GATE-NOT-EVALUABLE unless the operator passes ``--boost-declared X``
(then ``declared``, recorded as such, not read back).

**Mention attachment (round 3).** Under ``attach_only_with_value: false`` an
entry renders on every chunk that mentions its symbol, so a token's glossary
document frequency rises and fewer tokens stay within :data:`PROBE_TOP_K`
glossaries. ``max_glossaries`` (``plan(..., max_glossaries=N)``; found10's
``--probe-max-glossaries``) widens the rule to N glossaries AND the probe's
``top_k`` to N with it, so every glossary match still fits in the served list;
the default (:data:`PROBE_TOP_K`) is round 2's probe, unchanged. When even that
leaves fewer than :data:`MIN_PROBES` candidates the reading is
``not_testable`` and ``--boost-declared`` records the configured value as
``declared``.

Outputs carry each probe's token as a sha256 (never the text), its chunk id,
its glossary document frequency, the rank the chunk came back at, requested vs
served depth, and the verdict. The probe proves the boost is **> 0**; its
magnitude is still the declared value.
"""
from __future__ import annotations

import hashlib
import re
import string
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

PROBE_TOP_K = 10
MIN_PROBES = 3
MAX_PROBES = 5
MIN_TOKEN_LEN = 5
CHECK = "glossary_boost_behavioural_probe"
#: verdicts that stop the run before anything is measured
STOP = ("inactive", "mixed", "error")
PROBE_REQUEST = {"retrieval_mode": "bm25", "rerank": False, "top_k": PROBE_TOP_K}

_WORD = re.compile(r"\w+")
_ALNUM = re.compile(r"[0-9A-Za-z]+")
# Outer punctuation the standard analyzer (UAX#29) treats as a word boundary.
# ``_`` is NOT stripped: it is ExtendNumLet, which joins one-sidedly (``_abc``
# and ``abc_`` are indexed whole), so a piece carrying one is never a probe.
_STRIP = string.punctuation.replace("_", "") + "—–‘’“”"


#: the widest ``max_glossaries`` accepted: the probe's ``top_k`` follows it, and a served
#: list of hundreds is no longer a cheap read-only probe
MAX_GLOSSARIES_CAP = 100


def probe_request(max_glossaries: int = PROBE_TOP_K) -> dict[str, Any]:
    """The probe's request: :data:`PROBE_REQUEST` with ``top_k`` = ``max_glossaries``
    (never below :data:`PROBE_TOP_K`), so every glossary that holds the token fits."""
    n = int(max_glossaries)
    if not PROBE_TOP_K <= n <= MAX_GLOSSARIES_CAP:
        raise ProbeUnavailable(f"max_glossaries {n} outside [{PROBE_TOP_K}, {MAX_GLOSSARIES_CAP}]")
    return {**PROBE_REQUEST, "top_k": n}


class ProbeUnavailable(Exception):  # noqa: N818 - a reason, not a failure
    """The probe cannot be built for this input (the reason says why)."""


def token_sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def glossary_tokens(text: str) -> set[str]:
    """Distinctive-token candidates of one rendered field, lowercased."""
    out = set()
    for piece in (text or "").split():
        w = piece.strip(_STRIP)
        if (len(w) >= MIN_TOKEN_LEN and w.isascii() and w.isalnum()
                and any(ch.isalpha() for ch in w)):
            out.add(w.lower())
    return out


def content_terms(text: str) -> set[str]:
    """Every term content BM25 could match, generously (both a Unicode ``\\w+``
    split and an ASCII alphanumeric split, lowercased): a token in here is
    never a probe."""
    t = (text or "").lower()
    return set(_WORD.findall(t)) | set(_ALNUM.findall(t))


def pick_probes(chunks: Iterable[tuple[str, str, str]], n: int = MAX_PROBES,
                max_df: int = PROBE_TOP_K) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """``chunks`` = ``(chunk_id, content, rendered glossary)`` for EVERY chunk of
    the index. Returns up to ``n`` probes on distinct chunks and distinct
    tokens (``{"chunk_id", "token", "token_sha256", "glossary_df"}``; the token
    text stays in memory, never in an output) and selection counts.
    Deterministic: lowest glossary document frequency first, ties by hash."""
    rows = [(str(c), str(t or ""), str(g or "")) for c, t, g in chunks]
    in_content: set[str] = set()
    for _, content, _ in rows:
        in_content |= content_terms(content)
    per_chunk = {cid: glossary_tokens(g) - in_content for cid, _, g in rows if g}
    df: Counter[str] = Counter()
    for _, _, g in rows:
        if g:
            df.update(glossary_tokens(g))
    best: list[tuple[int, str, str, str]] = []
    for cid, toks in per_chunk.items():
        ok = [t for t in toks if df[t] <= max_df]
        if ok:
            t = min(ok, key=lambda x: (df[x], token_sha(x)))
            best.append((df[t], token_sha(cid + "|" + t), cid, t))
    best.sort()
    picked: list[dict[str, Any]] = []
    used: set[str] = set()
    for d, _, cid, t in best:
        if t in used:
            continue
        used.add(t)
        picked.append({"chunk_id": cid, "token": t, "token_sha256": token_sha(t), "glossary_df": d})
        if len(picked) >= n:
            break
    stats = {"chunks": len(rows), "chunks_with_glossary": sum(1 for _, _, g in rows if g),
             "chunks_with_a_qualifying_token": len(best), "max_glossary_df": max_df,
             "min_token_len": MIN_TOKEN_LEN}
    return picked, stats


def judge(probe: Mapping[str, Any], resp: Mapping[str, Any] | None, error: str | None = None,
          requested: int = PROBE_TOP_K) -> dict[str, Any]:
    """One probe's result from the API response (``None`` + ``error`` when the
    call failed). Never carries the token text."""
    out: dict[str, Any] = {"chunk_id": probe["chunk_id"], "token_sha256": probe["token_sha256"],
                           "glossary_df": probe["glossary_df"], "requested": requested}
    if resp is None:
        return {**out, "result": "error", "error": error or "no response", "served": None, "rank": None}
    ids = [str(s.get("chunk_id")) for s in resp.get("sources") or []]
    echo = resp.get("top_k")
    rank = next((i for i, c in enumerate(ids, 1) if c == probe["chunk_id"]), None)
    out.update(served=len(ids), response_top_k=echo, rank=rank)
    if resp.get("degraded"):
        return {**out, "result": "error", "error": "degraded response"}
    if echo is not None and int(echo) < requested:
        return {**out, "result": "error", "error": f"depth read-back: response top_k {echo} < {requested}"}
    if len(ids) > requested:
        return {**out, "result": "error", "error": f"depth read-back: served {len(ids)} > {requested}"}
    return {**out, "result": "pass" if rank is not None else "fail"}


def verdict(results: list[dict[str, Any]]) -> str:
    if any(r["result"] == "error" for r in results):
        return "error"
    passed = sum(r["result"] == "pass" for r in results)
    if passed == len(results):
        return "active"
    return "inactive" if passed == 0 else "mixed"


def record(status: str, *, probes: list[dict[str, Any]] | None = None, stats: Mapping[str, Any] | None = None,
           declared: float | None = None, reason: str | None = None, source: str | None = None,
           request: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The manipulation-check entry. ``gate`` is set when the boost-dependent
    arms are GATE-NOT-EVALUABLE (``not_testable`` without a declared boost)."""
    probes = probes or []
    gate = None
    if status == "not_testable" and declared is None:
        gate = ("glossary boost not testable (no chunk qualifies for the behavioural probe) and not "
                "declared (--boost-declared): boost-dependent arms not evaluable")
    return {"check": CHECK, "status": status, "boost_check": status,
            "verdict": status if status in ("active", "inactive", "mixed", "error") else None,
            "n_probes": len(probes), "n_passed": sum(p.get("result") == "pass" for p in probes),
            "min_probes": MIN_PROBES, "request": {**(request or PROBE_REQUEST), "query": "<probe token>"},
            "probes": [{k: v for k, v in p.items() if k != "token"} for p in probes],
            "selection": dict(stats or {}), "candidates_source": source, "declared": declared,
            "declared_label": "declared (--boost-declared), not read back" if declared is not None else None,
            "reason": reason, "gate": gate,
            "note": "the probe shows the boost is > 0; its magnitude is the declared value"}


def plan(chunks: Iterable[tuple[str, str, str]] | None, *, declared: float | None,
         unavailable: str | None = None, source: str | None = None,
         max_glossaries: int = PROBE_TOP_K) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Probes to send, or the finished ``not_testable`` / ``declared`` record
    when there are fewer than :data:`MIN_PROBES`. ``declared <= 0`` is refused:
    a declared zero boost voids the glossary arms. ``max_glossaries`` widens
    the glossary-frequency rule and the probe's ``top_k`` together
    (:func:`probe_request`); the request to send is ``ctx["request"]``."""
    if declared is not None and declared <= 0:
        raise ProbeUnavailable(f"--boost-declared {declared:g}: a boost <= 0 never consults the glossary field")
    req = probe_request(max_glossaries)
    if chunks is None:
        st = "declared" if declared is not None else "not_testable"
        return [], record(st, declared=declared, reason=unavailable or "no probe candidates given",
                          source=source, request=req)
    probes, stats = pick_probes(chunks, max_df=req["top_k"])
    if len(probes) < MIN_PROBES:
        st = "declared" if declared is not None else "not_testable"
        return [], record(st, stats=stats, declared=declared, source=source, request=req,
                          reason=f"{len(probes)} chunk(s) qualify, fewer than {MIN_PROBES}")
    return probes, {"stats": stats, "source": source, "declared": declared, "request": req}


def finish(probes: list[dict[str, Any]], results: list[dict[str, Any]], ctx: Mapping[str, Any]) -> dict[str, Any]:
    v = verdict(results)
    reason = {"inactive": "no probe chunk came back for its glossary-only token: the BM25 glossary boost is 0 "
                          "(or the glossary field is not indexed); every glossary arm would collapse",
              "mixed": "some probe chunks came back and some did not: stop and inspect",
              "error": "a probe call failed or failed its depth read-back"}.get(v)
    return record(v, probes=results, stats=ctx.get("stats"), declared=ctx.get("declared"), reason=reason,
                  source=ctx.get("source"), request=ctx.get("request"))


def fields_with_shuffle(fields: Mapping[str, str], seed: str, key_prefix: str,
                        expected_permutation_sha256: str | None) -> dict[str, str]:
    """The fields the index holds under the shuffled control: the backfill keys
    its permutation by Elasticsearch ``_id`` (``key_prefix + chunk_id``), so the
    same keys and seed reproduce it; the permutation hash must equal the one the
    backfill recorded, or the probe is not built on what the index holds."""
    from ragstack.ingestion.glossary_shuffle import shuffle_rendered

    if not expected_permutation_sha256:
        raise ProbeUnavailable("shuffled arm: the swap record names no permutation_sha256 to verify against")
    keyed = {key_prefix + cid: v for cid, v in fields.items()}
    res = shuffle_rendered(keyed, seed)
    if res.permutation_sha256 != expected_permutation_sha256:
        raise ProbeUnavailable("shuffled arm: the reproduced permutation differs from the backfill's "
                               "(check the corpus's es_id_prefix); the rendered fields are unknown")
    return {cid: res.assignment.get(key_prefix + cid, "") for cid in fields}
