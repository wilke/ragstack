"""The dedicated reranker test: first-stage pools, offline reranking, fusion,
an input-length census and the pre-registered statistics.

The design is the reranker-test pre-registration (kept with the outputs, outside
the repo); this module is its harness (its section 9). Nothing private is in
this file: the question/gold file, the chunk dump, the API base URL, the sidecar
URL and every path come in as arguments (or through the part-4
``--private-config``, for the docs-A in-process route).

Subcommands (each writes one JSON file; every file embeds
``ragstack.provenance.experiment_provenance()``):

``pools``
    Capture first-stage candidate lists per leg (``bm25`` / ``vector`` /
    ``hybrid``) and depth D. Two backends:

    * ``--backend api``: ``POST /v1/retrieve`` with ``rerank=false`` and
      ``top_k=D`` (``MAX_TOP_K`` caps D at 100, so the API route refuses deeper
      pools). ``--live hybrid@50,...`` also captures the API's own reranked top
      10 (``rerank=true, rerank_candidates=D``): the A4/A5/A6 API arms and the
      identity-gate reference.
    * ``--backend inprocess``: the section-aware-trial BM25 / dense / RRF code,
      as ``deployed_docs_part4.production_env`` uses it, to any depth (200 for
      family A). Per-leg depth is ``multiplier x D`` before RRF, as deployed.

    **Depth read-back.** Every request's served depth (the number of hits, and
    the response's ``top_k`` when it echoes one) is recorded against the
    requested depth. A list shorter than requested is ``under-served``; the run
    fails (exit 1) unless ``--allow-short``, in which case that list is marked
    ``GATE-NOT-EVALUABLE`` and every arm built on it inherits the mark. (A
    gateway once clamped ``top_k`` 100 to 10 silently; this is the guard.) The
    in-process route can tell a corpus that simply has fewer matching chunks
    (``exhausted``, allowed) from a clamp; the API route cannot, so any short
    API list counts as under-served. A response carrying ``degraded`` entries
    (e.g. a reranker fallback) is under-served too.

    The section-3 manipulation check (RRF k, multiplier, rerank candidates,
    per-doc cap, boilerplate demotion, glossary boost, glossary-in-reranker) is
    logged before anything is retrieved; a mismatch stops the run (exit 1).

    **Glossary boost read-back** (API route; :mod:`glossary_boost_probe`).
    ``/v1/config`` does not expose ``TEXT_SYMBOL_GLOSSARY_BOOST`` (default 0:
    the field is then never consulted), so after the manipulation check the
    harness probes it: up to 5 dump chunks whose stored ``symbol_glossary``
    holds a token found in no chunk's content are queried ``bm25`` at
    ``top_k`` 10 with that token alone; each must come back. None does
    (``inactive``) or some do (``mixed``) or a probe fails its depth read-back
    (``error``): exit 1 before any pool is captured. Fewer than 3 qualifying
    chunks: ``not_testable``, and every bm25/hybrid-derived list is
    GATE-NOT-EVALUABLE in ``stats`` unless ``--boost-declared X`` (then
    ``declared``). Recorded under ``manipulation.boost_check`` (token hashes,
    never text). The in-process route uses ``--glossary-boost`` directly.
    The gold (chunk ids per quote, by the parts 2-4 normalisation) is computed
    here against the full chunk dump and written beside the lists.

``rerank``
    Rerank captured pools through the cross-encoder sidecar with the five input
    renderings (``content``, ``content+gloss`` = the live
    ``rerank_passage``, ``gloss+content``, ``content+shuffled``,
    ``gloss-only``). The shuffled rendering is a seeded permutation (seed
    261029) of glossary strings (``--shuffle-unit string``) or glossary lines
    (``entry``) across the glossary-bearing chunks of the whole dump. **Identity
    gate first:** on 3 probe records the offline ``content+gloss`` top 10 of
    ``hybrid@50`` must equal the live A4-50 list and order, or nothing else is
    sent (exit 1). One sidecar request per (query, pool) with the whole pool, as
    the API sends it (same batch composition); at most 4 in flight; an
    append-only score cache keyed by hashes makes reruns free.

``fuse``
    A9: RRF (k=60) of the reranker's order with a first-stage order.

``census``
    Reranker input lengths in XLM-R tokens (the bge tokenizer), passage-only over
    the dump and (query, passage) pairs over pooled candidates, per rendering,
    with the count above the sidecar's ``MAX_LENGTH``; neighbour hits (a top-10
    chunk that is the ``prev``/``next`` chunk of a gold chunk).

``stats``
    found@k, ceiling@D, the ``n = (n - ceiling) + ordering loss + found@10``
    decomposition, the inactive-reranker gate (a reranked list -- API A4/A5/A6
    or offline A7 -- whose top 10 equals its un-reranked pool's top 10 on every
    record is GATE-NOT-EVALUABLE, and so is every hypothesis and fuse built on
    it: a wired-but-inactive reranker answers ``rerank=true`` with the fused
    order), rank-of-gold summaries, H1-H3 (Holm over 3) with the part-4
    sign-flip / bootstrap / McNemar machinery imported from
    :mod:`deployed_docs_part4`, exploratory contrasts (uncorrected, Bonferroni
    count beside), per-record gold ranks by id, and every GATE-NOT-EVALUABLE
    reason.

``part4-rescore``
    D3's pre-committed check (prereg section 12): reads a ``part4-glossary/1``
    result (the step-7 docs-A file; writer :mod:`part4_glossary`) and reports
    found@10 for the D3 arms A0, A2, A4-50 and, with ``--questions`` for the
    clusters, H1 by the ``stats`` test. Which per-item cell is which arm is
    stated with ``--map ARM=CELL`` (never guessed); a cell the file does not
    hold exits naming the cells it does. **The writer stores, per record, only
    ``found`` for its budget conditions (e.g. ``k10``, ``B4096``) of the
    reranked list** -- no ranks and no bm25 / hybrid-shallow cell -- so on such
    a file only A4-50 (``--map A4-50=k10``) is readable; an unmapped D3 arm is
    recorded ``absent`` and the run exits 1 unless ``--allow-partial``. The
    found rule is part 4's (every gold quote in the top-10 text), not the gold
    chunk-id rule of ``stats``; the output says so. Post-freeze tooling
    addition; no analysis, threshold or arm changed.

``power-table``
    The power floor (208 pages x 735 questions by default, Holm worst case
    alpha/3), saved as ``power-table-reranker.json``.

**Text in outputs.** Outputs carry ids, ranks and counts. ``--omit-text`` (the
default) keeps query and quote text out of every file and checks the written
JSON for any of it before finishing; ``--no-omit-text`` adds the query text to
the pools file for private debugging only.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import deployed_docs_part4 as dp4  # noqa: E402
import glossary_boost_probe as gbp  # noqa: E402
import section_aware_trial as sat  # noqa: E402
from section_aware_trial import C  # noqa: E402

cs = dp4.cs  # chunktrial_stats, as the part-4 module imports it

SEED = 261029
LISTS_SCHEMA = "reranker-lists/1"
GOLD_SCHEMA = "reranker-gold/1"
RESULTS_SCHEMA = "reranker-results/1"
CENSUS_SCHEMA = "reranker-census/1"
POWER_SCHEMA = "reranker-power/1"
EXIT_OK, EXIT_FAIL = 0, 1
LEGS = ("bm25", "vector", "hybrid")
DEPTHS = (10, 20, 50, 100, 200)
API_MAX_TOP_K = 100
TOP = 10
FOUND_K = (5, 10, 20, 50)
#: hard cap on concurrent requests to any endpoint (sidecar or API); a flag may lower it, never raise it
MAX_INFLIGHT = 4
RENDERINGS = ("content", "content+gloss", "gloss+content", "content+shuffled", "gloss-only")
DEFAULT_RENDERING = "content+gloss"
GNE = "GATE-NOT-EVALUABLE"
MIN_CLUSTERS = 8
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
RERANKER_SNAPSHOT = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
#: section 3 of the prereg: logged before any arm; a mismatch stops the run
PREREG_SETTINGS: dict[str, Any] = {
    "rrf_k": 60,
    "retrieval_candidate_multiplier": 2,
    "rerank_candidates": 50,
    "retrieval_max_per_doc": 0,
    "retrieval_demote_boilerplate": False,
    "text_symbol_glossary_boost": 1.0,
    "rerank_include_symbol_glossary": True,
}
_REPO = Path(__file__).resolve().parents[3]
_SIDECAR_REQ = _REPO / "sidecars" / "crossencoder" / "requirements.txt"


def log(msg: str) -> None:
    print(f"[reranker_test] {msg}", file=sys.stderr, flush=True)


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def provenance() -> dict[str, Any]:
    from ragstack.provenance import experiment_provenance

    p = experiment_provenance(repo=str(_REPO))
    p["citable"] = p.get("dirty") is False and p.get("untracked") == 0
    return p


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ------------------------------------------------------------------ chunks

@dataclass
class Corpus:
    chunks: list[C]
    #: chunk id -> ids of its prev/next chunks
    neighbours: dict[str, set[str]]
    meta: dict[str, Any]

    def __post_init__(self) -> None:
        self.by_id = {c.id: c for c in self.chunks}
        if len(self.by_id) != len(self.chunks):
            raise SystemExit("chunk dump: duplicate chunk ids")


def _md(row: dict[str, Any], key: str) -> Any:
    if row.get(key) not in (None, ""):
        return row[key]
    return (row.get("metadata") or {}).get(key)


def corpus_from_rows(rows: list[dict[str, Any]], meta: dict[str, Any]) -> Corpus:
    """Rows in the text index's shape: ``chunk_id`` (or ``id``), ``doc_id``,
    ``content``, ``symbol_glossary`` (or ``glossary``), and
    ``prev_chunk_id``/``next_chunk_id`` at top level or in ``metadata``."""
    chunks, nb = [], defaultdict(set)
    for r in rows:
        cid = str(r.get("chunk_id") or r.get("id"))
        chunks.append(C(cid, str(r.get("doc_id") or ""), str(r.get("content") or ""), "",
                        str(r.get("symbol_glossary") or r.get("glossary") or "")))
        for k in ("prev_chunk_id", "next_chunk_id"):
            v = _md(r, k)
            if v:
                nb[cid].add(str(v))
    meta = {**meta, "chunks": len(chunks), "chunks_with_glossary": sum(1 for c in chunks if c.glossary),
            "chunks_with_neighbour_links": len(nb)}
    return Corpus(chunks, dict(nb), meta)


def corpus_from_part4(private_config: str, arm: str) -> Corpus:
    """The docs-A route: the part-4 arm built in-process from the live index, as
    ``deployed_docs_part4.production_env`` builds it (network read of the index)."""
    import deployed_docs_trial as dt

    dt.load_private_config(private_config)
    pages, imeta = dt.pages_from_index(dt.read_index())
    arms, _ = dt.build_arms(pages)
    if arm not in arms:
        raise SystemExit(f"unknown part-4 arm {arm!r}")
    chunks = arms[arm]
    nb: dict[str, set[str]] = defaultdict(set)
    by_doc: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for c in chunks:  # ids are "<arm>|<doc_id>|<n>": neighbours are n-1 / n+1 of the same page
        by_doc[c.doc_id].append((int(c.id.rsplit("|", 1)[1]), c.id))
    for lst in by_doc.values():
        lst.sort()
        for (_, x), (_, y) in zip(lst, lst[1:], strict=False):
            nb[x].add(y)
            nb[y].add(x)
    meta = {"source": "part4", "arm": arm, "chunks": len(chunks), "docs": imeta.get("docs"),
            "chunks_with_glossary": sum(1 for c in chunks if c.glossary)}
    return Corpus(chunks, dict(nb), meta)


def load_corpus(args: argparse.Namespace) -> Corpus:
    if getattr(args, "chunks", None):
        p = Path(args.chunks)
        return corpus_from_rows(read_jsonl(p), {"source": "dump", "sha256": sha256_file(p)})
    if getattr(args, "part4_arm", None):
        if not args.private_config:
            raise SystemExit("--part4-arm needs --private-config")
        return corpus_from_part4(args.private_config, args.part4_arm)
    raise SystemExit("a chunk source is required: --chunks DUMP.jsonl or --part4-arm ARM --private-config CFG")


# ------------------------------------------------------------------ questions and gold

FIELD_CANDIDATES = {
    "id": ("record_id", "id", "qid"),
    "query": ("query", "question"),
    "quotes": ("quotes", "gold", "quote"),
    "cluster": ("cluster", "paper", "doc_id"),
}


def _pick(row: dict[str, Any], names: Sequence[str]) -> Any:
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def load_questions(path: Path, fields: dict[str, str | None], set_field: str | None = None,
                   set_value: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The question/gold file: JSONL, one record per row. Field names are
    detected (:data:`FIELD_CANDIDATES`) or given (``--id-field`` etc.). Quotes may
    be a string, a list, or ``expected.quote``; a row with no quote may carry
    ``gold_chunk_ids`` instead. ``valid: false`` excludes a row (counted)."""
    rows = read_jsonl(path)
    out: list[dict[str, Any]] = []
    skipped_set = 0
    for r in rows:
        if set_field and set_value is not None and str(r.get(set_field)) != set_value:
            skipped_set += 1
            continue

        def get(k: str, row: dict[str, Any] = r) -> Any:
            f = fields.get(k)
            return row.get(f) if f else _pick(row, FIELD_CANDIDATES[k])

        rid, query, cluster = get("id"), get("query"), get("cluster")
        quotes = get("quotes")
        if quotes is None and isinstance(r.get("expected"), dict):
            quotes = r["expected"].get("quote")
        if isinstance(quotes, str):
            quotes = [quotes]
        if rid is None or query is None or cluster is None:
            raise SystemExit(f"question file: a row lacks id/query/cluster (keys {sorted(r)})")
        out.append({"id": str(rid), "query": str(query), "cluster": str(cluster),
                    "quotes": [str(q) for q in (quotes or [])],
                    "gold_chunk_ids": [str(x) for x in (r.get("gold_chunk_ids") or [])],
                    "valid": r.get("valid", True) is not False})
    ids = [q["id"] for q in out]
    if len(set(ids)) != len(ids):
        raise SystemExit("question file: duplicate record ids")
    meta = {"sha256": sha256_file(path), "rows": len(rows), "records": len(out), "skipped_other_set": skipped_set,
            "set_filter": [set_field, set_value] if set_field else None,
            "distinct_queries": len({q["query"] for q in out}), "clusters": len({q["cluster"] for q in out})}
    return out, meta


def build_gold(questions: list[dict[str, Any]], corpus: Corpus) -> dict[str, Any]:
    """Gold chunk ids per quote under the parts 2-4 rule (NFKC, casefold, all
    whitespace removed; a quote cut by a chunk boundary counts when its first or
    last 60% is in the chunk: :func:`section_aware_trial.hit`). A record whose
    quotes match no chunk is excluded (``no_gold_chunk_in_index``)."""
    ntext = {c.id: sat.norm(c.content) for c in corpus.chunks}
    recs: dict[str, Any] = {}
    excl: Counter[str] = Counter()
    for q in questions:
        quotes: list[list[str]] = []
        source = "quote"
        if q["quotes"]:
            for qt in q["quotes"]:
                nq = sat.norm(qt)
                quotes.append(sorted(cid for cid, t in ntext.items() if sat.hit(nq, t)))
        elif q["gold_chunk_ids"]:
            source = "ids"
            quotes.append(sorted(x for x in q["gold_chunk_ids"] if x in corpus.by_id))
        reason = None
        if not q["valid"]:
            reason = "invalid_at_build"
        elif not quotes:
            reason = "no_quote"
        elif not any(quotes):
            reason = "no_gold_chunk_in_index"
        if reason:
            excl[reason] += 1
        recs[q["id"]] = {"cluster": q["cluster"], "query_sha": sha(q["query"])[:16], "gold_source": source,
                         "quotes": quotes, "excluded": reason}
    return {"records": recs, "excluded": dict(excl), "n": len(recs),
            "n_included": sum(1 for r in recs.values() if not r["excluded"])}


def rank_of_gold(lst: Sequence[str], rec: dict[str, Any], rule: str = "any") -> int | None:
    """1-based rank of the first gold chunk (``any``), or of the last quote to be
    covered (``all``: every quote needs one of its chunks); None if not in ``lst``."""
    pos: dict[str, int] = {}
    for i, cid in enumerate(lst, 1):
        pos.setdefault(cid, i)
    per = []
    for ids in rec["quotes"]:
        rs = [pos[c] for c in ids if c in pos]
        per.append(min(rs) if rs else None)
    if rule == "any":
        got = [r for r in per if r is not None]
        return min(got) if got else None
    if rule == "all":
        return None if (not per or any(r is None for r in per)) else max(per)  # type: ignore[type-var]
    raise ValueError(f"unknown gold rule {rule!r}")


# ------------------------------------------------------------------ lists files

def parse_key(key: str) -> tuple[str, str, int, str | None]:
    """``bm25@200`` -> (pool, bm25, 200, None); ``live:hybrid@50`` -> (live, hybrid, 50, None);
    ``rr:hybrid@50:content+gloss`` -> (rr, hybrid, 50, content+gloss); ``fuse:name`` -> (fuse, name, 0, None)."""
    if key.startswith("fuse:"):
        return "fuse", key[5:], 0, None
    kind = "pool"
    rest = key
    if key.startswith(("live:", "rr:")):
        kind, rest = key.split(":", 1)
    rendering = None
    if kind == "rr":
        rest, rendering = rest.split(":", 1)
    leg, d = rest.split("@")
    return kind, leg, int(d), rendering


def new_lists(kind: str) -> dict[str, Any]:
    return {"schema": LISTS_SCHEMA, "kind": kind, "lists": {}, "depth": {}, "notes": []}


def load_lists(paths: Iterable[str]) -> dict[str, Any]:
    """Merge lists files; a key in two files is an error."""
    merged: dict[str, Any] = {"lists": {}, "depth": {}, "headers": [], "depths": set(), "identity_gate": None,
                              "manipulation": None, "sidecar": None, "shuffle": None}
    for p in paths:
        d = json.loads(Path(p).read_text())
        if d.get("schema") != LISTS_SCHEMA:
            raise SystemExit(f"{p}: not a {LISTS_SCHEMA} file")
        for k, v in d["lists"].items():
            if k in merged["lists"]:
                raise SystemExit(f"list {k} appears in two inputs")
            merged["lists"][k] = v
        merged["depth"].update(d.get("depth", {}))
        merged["depths"].update(d.get("depths") or [])
        for k in ("identity_gate", "manipulation", "sidecar", "shuffle"):
            if d.get(k) is not None:
                merged[k] = d[k]
        merged["headers"].append({"file": Path(p).name, "sha256": sha256_file(Path(p)), "kind": d.get("kind"),
                                  "backend": d.get("backend"), "commit": (d.get("provenance") or {}).get("commit")})
    return merged


def pool_list(lists: dict[str, Any], leg: str, d: int) -> dict[str, list[str]] | None:
    """The pool at depth ``d``: the exact capture ``leg@d``; for a single leg
    (bm25 / vector), the ``d``-prefix of a deeper capture is the same pool."""
    exact = lists.get(f"{leg}@{d}")
    if exact is not None:
        return exact
    if leg == "hybrid":
        return None
    deeper = sorted((parse_key(k)[2], k) for k in lists
                    if parse_key(k)[0] == "pool" and parse_key(k)[1] == leg and parse_key(k)[2] >= d)
    if not deeper:
        return None
    src = lists[deeper[0][1]]
    return {rid: lst[:d] for rid, lst in src.items()}


def pool_source_key(lists: dict[str, Any], leg: str, d: int) -> str | None:
    if f"{leg}@{d}" in lists:
        return f"{leg}@{d}"
    if leg == "hybrid":
        return None
    deeper = sorted((parse_key(k)[2], k) for k in lists
                    if parse_key(k)[0] == "pool" and parse_key(k)[1] == leg and parse_key(k)[2] >= d)
    return deeper[0][1] if deeper else None


def depth_status(requested: int, served: dict[str, int], exhausted: set[str], short_bad: set[str]) -> dict[str, Any]:
    vals = list(served.values())
    st = "ok"
    if short_bad:
        st = "under-served"
    elif exhausted:
        st = "exhausted"
    return {"requested": requested, "served_min": min(vals) if vals else None, "served_max": max(vals) if vals else None,
            "n_records": len(vals), "n_under_served": len(short_bad), "n_exhausted": len(exhausted),
            "under_served_ids": sorted(short_bad)[:50], "status": st}


def _serialised_forms(text: str) -> set[str]:
    """The ways ``text`` can appear inside a JSON document: verbatim, and
    JSON-escaped with and without ``ensure_ascii`` (quotes, backslashes and
    newlines become ``\\"``, ``\\\\``, ``\\n``; non-ASCII may become ``\\uXXXX``)."""
    return {text, json.dumps(text, ensure_ascii=False)[1:-1], json.dumps(text)[1:-1]}


def check_no_text(obj: Any, texts: Iterable[str], min_len: int = 12) -> list[str]:
    """Texts (queries, quotes) of at least ``min_len`` chars that occur in the JSON
    of ``obj`` as :func:`deployed_docs_part4.write_json_atomic` serialises it, in
    any of their serialised forms (a plain substring scan would miss a quote that
    contains ``"``, ``\\`` or a newline, because JSON escapes them)."""
    blob = json.dumps(obj, indent=1, sort_keys=True, default=str)
    return sorted({t[:40] for t in texts
                   if len(t) >= min_len and any(f in blob for f in _serialised_forms(t))})


def write_out(path: Path, obj: dict[str, Any], questions: list[dict[str, Any]] | None, omit_text: bool) -> None:
    if omit_text and questions:
        texts = [q["query"] for q in questions] + [x for q in questions for x in q["quotes"]]
        leaks = check_no_text(obj, texts)
        if leaks:
            raise SystemExit(f"refusing to write {path.name}: {len(leaks)} query/quote texts would be written "
                             "(--omit-text is on)")
    path.parent.mkdir(parents=True, exist_ok=True)
    dp4.write_json_atomic(path, obj)
    log(f"wrote {path}")


# ------------------------------------------------------------------ manipulation check

def manipulation_check(observed: dict[str, Any], expected: dict[str, Any],
                       sources: dict[str, str]) -> dict[str, Any]:
    rows = {}
    for k, exp in expected.items():
        obs = observed.get(k)
        ok = obs is not None and (math.isclose(float(obs), float(exp)) if isinstance(exp, float)
                                  and not isinstance(exp, bool) else obs == exp)
        rows[k] = {"expected": exp, "observed": obs, "source": sources.get(k, "missing"), "ok": bool(ok)}
    return {"ok": all(r["ok"] for r in rows.values()), "values": rows}


def parse_expect(items: list[str] | None) -> dict[str, Any]:
    exp = dict(PREREG_SETTINGS)
    for it in items or []:
        k, v = it.split("=", 1)
        if k not in exp:
            raise SystemExit(f"--expect: unknown setting {k!r}")
        cur = exp[k]
        exp[k] = (v.lower() in ("1", "true", "yes")) if isinstance(cur, bool) else type(cur)(v)
    return exp


# ------------------------------------------------------------------ in-process first stage

class InProcess:
    """BM25 (content + symbol_glossary at ``boost``, as the store scores it) and
    exact dense over the dump, with the deployed RRF."""

    def __init__(self, corpus: Corpus, boost: float, qmat: np.ndarray, cmat: np.ndarray) -> None:
        self.corpus = corpus
        self.boost = boost
        self.index = sat.BM25(corpus.chunks, list(range(len(corpus.chunks))))
        self.qmat, self.cmat = qmat, cmat
        self.allowed = np.ones(len(corpus.chunks), dtype=bool)

    def bm25(self, query: str, depth: int) -> list[int]:
        terms = sat.analyze(query)
        s = np.zeros(len(self.corpus.chunks))
        self.index.field("content", False).score(terms, s, 1.0)
        if self.boost:
            self.index.field("gloss", "rules").score(terms, s, self.boost)
        nz = np.flatnonzero(s > 0)
        order = nz[np.lexsort((nz, -s[nz]))]
        return order[:depth].tolist()

    def dense(self, qi: int, depth: int) -> list[int]:
        return sat.dense_top(self.cmat, self.qmat[qi], self.allowed, depth)


def embed_matrices(corpus: Corpus, queries: list[str], urls: list[str], model: str,
                   emb_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    store = sat.EmbStore(emb_dir)
    texts = list(queries) + [c.content for c in corpus.chunks]
    asyncio.run(sat.embed_missing(store, texts, urls, model))
    return store.matrix(queries), store.matrix([c.content for c in corpus.chunks])


def capture_inprocess(ip: InProcess, questions: list[dict[str, Any]], depths: list[int], legs: list[str],
                      multiplier: int) -> tuple[dict[str, Any], dict[str, Any]]:
    dmax = max(depths)
    deep = multiplier * dmax
    ids = [c.id for c in ip.corpus.chunks]
    n_chunks = len(ids)
    lists: dict[str, dict[str, list[str]]] = defaultdict(dict)
    served: dict[str, dict[str, int]] = defaultdict(dict)
    exhausted: dict[str, set[str]] = defaultdict(set)
    bad: dict[str, set[str]] = defaultdict(set)
    for qi, q in enumerate(questions):
        rid = q["id"]
        b = ip.bm25(q["query"], deep)
        d = ip.dense(qi, deep)
        if "bm25" in legs:
            k = f"bm25@{dmax}"
            lists[k][rid] = [ids[i] for i in b[:dmax]]
            served[k][rid] = len(lists[k][rid])
            if served[k][rid] < dmax:
                # in-process the scorer can say why: only chunks sharing a query term score
                exhausted[k].add(rid)
        if "vector" in legs:
            k = f"vector@{dmax}"
            lists[k][rid] = [ids[i] for i in d[:dmax]]
            served[k][rid] = len(lists[k][rid])
            if served[k][rid] < dmax:
                (exhausted if served[k][rid] == n_chunks else bad)[k].add(rid)
        if "hybrid" in legs:
            for D in depths:
                k = f"hybrid@{D}"
                fused = sat.rrf(d[:multiplier * D], b[:multiplier * D])[:D]
                lists[k][rid] = [ids[i] for i in fused]
                served[k][rid] = len(fused)
                if len(fused) < D:
                    (exhausted if len(set(d[:multiplier * D]) | set(b[:multiplier * D])) == len(fused)
                     else bad)[k].add(rid)
    depth = {k: depth_status(parse_key(k)[2], served[k], exhausted[k], bad[k]) for k in lists}
    return dict(lists), depth


# ------------------------------------------------------------------ API first stage

#: ``--auth-header``: where the credential from ``--api-key-env`` goes. ``x-api-key``: the
#: ``X-API-Key`` header (a RAGStack API key); ``authorization``: the ``Authorization`` header,
#: verbatim, no ``Bearer `` prefix (a BV-BRC user token; ``ragstack.api.security``).
AUTH_HEADERS = {"x-api-key": "X-API-Key", "authorization": "Authorization"}


def api_credential(env_name: str | None, environ: dict[str, str] | None = None) -> str | None:
    """The credential, read only from the environment variable ``env_name`` (never argv).
    ``None``/empty name: no credential. A named variable that is unset or empty is
    refused -- a run must not fall back to anonymous access silently."""
    if not env_name:
        return None
    key = (os.environ if environ is None else environ).get(env_name)
    if not key:
        raise SystemExit(f"--api-key-env names {env_name!r}, which is not set "
                         "(pass --api-key-env '' for an API without authentication)")
    return key


class Api:
    """``POST /v1/retrieve`` (read-only), at most :data:`MAX_INFLIGHT` in flight."""

    def __init__(self, base_url: str, api_key: str | None, collection: str | None, inflight: int,
                 http: Any = None, retries: int = 3, backoff_s: float = 2.0,
                 auth_header: str = "x-api-key") -> None:
        import httpx

        if auth_header not in AUTH_HEADERS:
            raise SystemExit(f"--auth-header {auth_header!r} is not one of {sorted(AUTH_HEADERS)}")
        self.base = base_url.rstrip("/")
        self.collection = collection
        self.inflight = max(1, min(int(inflight), MAX_INFLIGHT))
        #: the header NAME (recorded); the value is never recorded or logged
        self.auth_header = AUTH_HEADERS[auth_header] if api_key else None
        headers = {AUTH_HEADERS[auth_header]: api_key} if api_key else {}
        self.http = http or httpx.Client(timeout=300, headers=headers)
        self.retries, self.backoff = retries, backoff_s
        self.calls = 0
        self.config_status: int | str | None = None
        self._lock = threading.Lock()

    def config(self) -> dict[str, Any] | None:
        """``GET /v1/config``, best effort: a non-200 (a 403 "insufficient role" for a
        user token) or a failure is recorded in :attr:`config_status`, never fatal; the
        settings then come from ``--server-settings`` alone."""
        try:
            r = self.http.get(f"{self.base}/v1/config")
            self.config_status = r.status_code
            if r.status_code != 200:
                log(f"GET /v1/config -> {r.status_code}; settings come from --server-settings only")
                return None
            return r.json()
        except Exception as e:  # noqa: BLE001
            self.config_status = type(e).__name__
            log(f"GET /v1/config failed ({type(e).__name__}); settings come from --server-settings only")
            return None

    def retrieve(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.collection:
            body = {**body, "collection": self.collection}
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                r = self.http.post(f"{self.base}/v1/retrieve", json=body)
                with self._lock:
                    self.calls += 1
                if r.status_code >= 500:
                    raise RuntimeError(f"HTTP {r.status_code}")
                if r.status_code != 200:
                    raise SystemExit(f"/v1/retrieve -> HTTP {r.status_code}: {r.text[:200]}")
                return r.json()
            except SystemExit:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(self.backoff * (attempt + 1))
        raise RuntimeError(f"/v1/retrieve failed after {self.retries} tries: {last}")


def capture_api(api: Api, questions: list[dict[str, Any]], depths: list[int], legs: list[str],
                live: list[tuple[str, int]]) -> tuple[dict[str, Any], dict[str, Any]]:
    dmax = max(depths)
    if dmax > API_MAX_TOP_K:
        raise SystemExit(f"the API route caps pool depth at MAX_TOP_K={API_MAX_TOP_K} (asked {dmax}); "
                         "depth-200 pools run in-process")
    reqs: list[tuple[str, int, dict[str, Any]]] = []  # (key, requested served depth, body)
    # ``use_graph=false`` pins the two legs the prereg defines (BM25, dense): the
    # server's default is ``true``, and a configured graph store would otherwise
    # add a third leg the in-process route and the offline rerank do not have.
    for leg in legs:
        for D in (depths if leg == "hybrid" else [dmax]):
            reqs.append((f"{leg}@{D}", D, {"top_k": D, "rerank": False, "retrieval_mode": leg,
                                           "use_graph": False}))
    for leg, D in live:
        reqs.append((f"live:{leg}@{D}", TOP, {"top_k": TOP, "rerank": True, "rerank_candidates": D,
                                              "retrieval_mode": leg, "use_graph": False}))
    jobs = [(k, want, body, q) for k, want, body in reqs for q in questions]
    lists: dict[str, dict[str, list[str]]] = defaultdict(dict)
    served: dict[str, dict[str, int]] = defaultdict(dict)
    bad: dict[str, set[str]] = defaultdict(set)
    echoed: dict[str, set[Any]] = defaultdict(set)
    degraded: Counter[str] = Counter()
    lock = threading.Lock()

    def one(job: tuple[str, int, dict[str, Any], dict[str, Any]]) -> None:
        k, want, body, q = job
        resp = api.retrieve({**body, "query": q["query"]})
        ids = [str(s["chunk_id"]) for s in resp.get("sources", [])]
        echo = resp.get("top_k")
        with lock:
            lists[k][q["id"]] = ids
            served[k][q["id"]] = len(ids)
            if echo is not None:
                echoed[k].add(echo)
            short = len(ids) < want or (echo is not None and int(echo) < int(body["top_k"]))
            if resp.get("degraded"):
                degraded[k] += 1
                short = True
            if short:
                bad[k].add(q["id"])

    with ThreadPoolExecutor(max_workers=api.inflight) as ex:
        list(ex.map(one, jobs))
    depth = {}
    for k, want, _ in reqs:
        depth[k] = {**depth_status(want, served[k], set(), bad[k]),
                    "response_top_k": sorted(echoed[k]) if echoed[k] else None,
                    "degraded_responses": degraded[k]}
    return dict(lists), depth


def api_boost_probe(api: Api, corpus: Corpus, declared: float | None) -> dict[str, Any]:
    """The behavioural read-back of the BM25 glossary boost (module docstring,
    ``pools``): candidates from the chunk dump's stored fields, at most
    ``api.inflight`` probes in flight."""
    chunks: list[tuple[str, str, str]] | None = None
    unavailable = None
    if corpus.meta.get("source") == "part4":
        unavailable = "part-4 chunks are built in-process; their ids are not the index's"
    else:
        chunks = [(c.id, c.content, c.glossary) for c in corpus.chunks]
    try:
        probes, ready = gbp.plan(chunks, declared=declared, unavailable=unavailable,
                                 source="chunk dump (--chunks): the stored symbol_glossary")
    except gbp.ProbeUnavailable as e:
        raise SystemExit(str(e)) from e
    if not probes:
        assert ready is not None
        return ready

    def one(p: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = api.retrieve({**gbp.PROBE_REQUEST, "query": p["token"], "use_graph": False})
        except RuntimeError as e:
            return gbp.judge(p, None, str(e)[:200])
        return gbp.judge(p, resp)

    with ThreadPoolExecutor(max_workers=api.inflight) as ex:
        results = list(ex.map(one, probes))
    return gbp.finish(probes, results, ready or {})


def parse_live(spec: str | None) -> list[tuple[str, int]]:
    out = []
    for it in (spec or "").split(","):
        if it.strip():
            leg, d = it.strip().split("@")
            if leg not in LEGS:
                raise SystemExit(f"--live: unknown leg {leg!r}")
            out.append((leg, int(d)))
    return out


def cmd_pools(args: argparse.Namespace) -> int:
    questions, qmeta = load_questions(Path(args.questions), question_fields(args), args.set_field, args.set_value)
    corpus = load_corpus(args)
    depths = sorted({int(x) for x in args.depths.split(",")})
    legs = [x for x in args.legs.split(",") if x]
    for leg in legs:
        if leg not in LEGS:
            raise SystemExit(f"unknown leg {leg!r}")
    expected = parse_expect(args.expect)
    declared = json.loads(Path(args.server_settings).read_text()) if args.server_settings else {}
    out = new_lists("pools")
    out.update(backend=args.backend, depths=depths, legs=legs, questions=qmeta, corpus=corpus.meta)
    if args.backend == "api":
        if not args.base_url:
            raise SystemExit("--base-url is required for the API backend (there is no default)")
        key = api_credential(args.api_key_env)
        api = Api(args.base_url, key, args.collection, args.inflight, auth_header=args.auth_header)
        cfg = api.config() or {}
        out["auth"] = {"header": api.auth_header, "api_key_env": args.api_key_env or None}
        out["config_readable"] = api.config_status == 200
        out["config_status"] = api.config_status
        observed, src = {}, {}
        for k in expected:
            if k in cfg:
                observed[k], src[k] = cfg[k], "GET /v1/config"
            elif k in declared:
                observed[k], src[k] = declared[k], "declared (--server-settings)"
        manip = manipulation_check(observed, expected, src)
        manip["reranker_model"] = cfg.get("reranker_model", declared.get("reranker_model"))
        manip["max_top_k_declared"] = declared.get("max_top_k", API_MAX_TOP_K)
        out["manipulation"] = manip
        log(f"manipulation check: {json.dumps(manip)}")
        if not manip["ok"]:
            write_out(Path(args.out), {**out, "provenance": provenance(), "status": "manipulation_check_mismatch"},
                      questions, args.omit_text)
            log("manipulation check mismatch: stopped before any retrieval")
            return EXIT_FAIL
        bc = api_boost_probe(api, corpus, args.boost_declared)
        manip["boost_check"] = bc
        log(f"glossary boost check: {bc['status']} ({bc['n_passed']}/{bc['n_probes']} probes returned their chunk)")
        if bc["status"] in gbp.STOP:
            manip["ok"] = False
            write_out(Path(args.out), {**out, "provenance": provenance(), "status": "boost_check_failed",
                                       "api_calls": api.calls}, questions, args.omit_text)
            log(f"glossary boost check {bc['status']}: {bc['reason']}; stopped before any pool was captured")
            return EXIT_FAIL
        lists, depth = capture_api(api, questions, depths, legs, parse_live(args.live))
        out["api_calls"] = api.calls
    else:
        if args.live:
            raise SystemExit("--live needs the API backend (the in-process route has no live reranker)")
        observed = {"rrf_k": sat.RRF_K, "retrieval_candidate_multiplier": args.multiplier,
                    "rerank_candidates": PREREG_SETTINGS["rerank_candidates"], "retrieval_max_per_doc": 0,
                    "retrieval_demote_boilerplate": False, "text_symbol_glossary_boost": args.glossary_boost,
                    "rerank_include_symbol_glossary": True}
        manip = manipulation_check(observed, expected, dict.fromkeys(observed, "in-process"))
        manip["boost_check"] = {"check": gbp.CHECK, "status": "in-process", "boost_check": "in-process",
                                "boost": args.glossary_boost, "gate": None,
                                "note": "the in-process BM25 applies --glossary-boost itself"}
        out["manipulation"] = manip
        log(f"manipulation check: {json.dumps(manip)}")
        if not manip["ok"]:
            write_out(Path(args.out), {**out, "provenance": provenance(), "status": "manipulation_check_mismatch"},
                      questions, args.omit_text)
            log("manipulation check mismatch: stopped before any retrieval")
            return EXIT_FAIL
        urls = [u for u in (args.embed_urls or "").split(",") if u]
        if not urls or not args.emb_dir:
            raise SystemExit("the in-process backend needs --embed-urls and --emb-dir (dense leg)")
        qmat, cmat = embed_matrices(corpus, [q["query"] for q in questions], urls, args.embed_model,
                                    Path(args.emb_dir))
        ip = InProcess(corpus, args.glossary_boost, qmat, cmat)
        lists, depth = capture_inprocess(ip, questions, depths, legs, args.multiplier)
        out["embed_model"] = args.embed_model
    out["lists"], out["depth"] = lists, depth
    if not args.omit_text:
        out["queries"] = {q["id"]: q["query"] for q in questions}
    gold = build_gold(questions, corpus)
    gold_out = {"schema": GOLD_SCHEMA, "provenance": provenance(), "questions": qmeta, "corpus": corpus.meta, **gold}
    under = sorted(k for k, v in depth.items() if v["status"] == "under-served")
    out["provenance"] = provenance()
    for k in under:
        depth[k]["status"] = GNE if args.allow_short else "under-served"
    out["status"] = "complete" if not under else ("complete_with_gate" if args.allow_short else "under_served")
    write_out(Path(args.out), out, questions, args.omit_text)
    write_out(Path(args.gold_out), gold_out, questions, args.omit_text)
    for k in under:
        d = depth[k]
        log(f"DEPTH READ-BACK: {k} requested {d['requested']}, served min {d['served_min']} "
            f"({d['n_under_served']} records short)")
    if under and not args.allow_short:
        log("under-served lists: failing the run (use --allow-short to keep them marked GATE-NOT-EVALUABLE)")
        return EXIT_FAIL
    return EXIT_OK


# ------------------------------------------------------------------ renderings and the shuffle

def render(c: C, rendering: str, shuffled: dict[str, str] | None = None) -> str:
    """The passage the cross-encoder scores. ``content+gloss`` is exactly
    ``ragstack.scoring.scorers.rerank_passage`` with the glossary on."""
    g = c.glossary
    if rendering == "content":
        return c.content
    if rendering == "content+gloss":
        return f"{c.content}\n{g}" if g else c.content
    if rendering == "gloss+content":
        return f"{g}\n{c.content}" if g else c.content
    if rendering == "content+shuffled":
        if shuffled is None:
            raise ValueError("content+shuffled needs the shuffle map")
        s = shuffled.get(c.id, "")
        return f"{c.content}\n{s}" if s else c.content
    if rendering == "gloss-only":
        return g
    raise ValueError(f"unknown rendering {rendering!r}")


def shuffle_glossaries(chunks: Sequence[C], seed: int = SEED, unit: str = "string") -> tuple[dict[str, str], dict[str, Any]]:
    """A seeded permutation of glossaries across the glossary-bearing chunks.

    ``string``: whole glossary strings are permuted, so the population (which
    chunks carry one) and the multiset of strings are kept and only the pairing
    breaks. ``entry``: the glossary lines are pooled, permuted and re-dealt with
    each chunk's original line count. Bearers are ordered by chunk id, so the
    result depends only on the dump and the seed."""
    bearers = sorted((c for c in chunks if c.glossary), key=lambda c: c.id)
    rng = random.Random(seed)
    if unit == "string":
        strings = [c.glossary for c in bearers]
        perm = list(strings)
        rng.shuffle(perm)
        out = {c.id: s for c, s in zip(bearers, perm, strict=True)}
    elif unit == "entry":
        lines = [c.glossary.split("\n") for c in bearers]
        pool = [x for ls in lines for x in ls]
        rng.shuffle(pool)
        out, i = {}, 0
        for c, ls in zip(bearers, lines, strict=True):
            out[c.id] = "\n".join(pool[i:i + len(ls)])
            i += len(ls)
    else:
        raise ValueError(f"unknown shuffle unit {unit!r}")
    meta = {"seed": seed, "unit": unit, "bearers": len(bearers),
            "unchanged": sum(1 for c in bearers if out[c.id] == c.glossary),
            "chars_total": sum(len(c.glossary) for c in bearers),
            "sha256": sha(json.dumps(sorted(out.items())))}
    return out, meta


# ------------------------------------------------------------------ the sidecar

class Sidecar:
    """``POST /rerank`` with the whole pool (``top_k`` = pool size), at most
    :data:`MAX_INFLIGHT` in flight; the response is validated."""

    def __init__(self, url: str, inflight: int, http: Any = None, retries: int = 3, backoff_s: float = 2.0) -> None:
        import httpx

        self.url = url.rstrip("/")
        self.inflight = max(1, min(int(inflight), MAX_INFLIGHT))
        self.http = http or httpx.Client(timeout=600)
        self.retries, self.backoff = retries, backoff_s
        self.calls = 0
        self.pairs = 0
        self._lock = threading.Lock()

    def ready(self) -> dict[str, Any]:
        try:
            r = self.http.get(f"{self.url}/ready")
            return {"http": r.status_code, **(r.json() if r.content else {})}
        except Exception as e:  # noqa: BLE001
            return {"http": None, "error": type(e).__name__}

    def rerank(self, query: str, docs: list[str]) -> tuple[list[int], list[float]]:
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                r = self.http.post(f"{self.url}/rerank", json={"query": query, "documents": docs, "top_k": len(docs)})
                r.raise_for_status()
                d = r.json()
                idx, sc = [int(i) for i in d["indices"]], [float(s) for s in d["scores"]]
                if sorted(idx) != list(range(len(docs))) or len(sc) != len(idx):
                    raise ValueError("sidecar response is not a permutation of the pool")
                with self._lock:
                    self.calls += 1
                    self.pairs += len(docs)
                return idx, sc
            except ValueError:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(self.backoff * (attempt + 1))
        raise RuntimeError(f"/rerank failed after {self.retries} tries: {last}")


class RerankCache:
    """Append-only JSONL: sha256(query, passages) -> the sidecar's order and scores (no text)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.d: dict[str, dict[str, Any]] = {}
        if path.exists():
            for x in dp4.read_jsonl_repair_tail(path):
                self.d[x["k"]] = x
        self._lock = threading.Lock()

    @staticmethod
    def key(query: str, docs: list[str]) -> str:
        h = hashlib.sha256(query.encode())
        for p in docs:
            h.update(b"\x1e" + p.encode())
        return h.hexdigest()

    def get(self, k: str) -> dict[str, Any] | None:
        return self.d.get(k)

    def put(self, k: str, idx: list[int], sc: list[float]) -> None:
        row = {"k": k, "i": idx, "s": sc}
        with self._lock:
            self.d[k] = row
            dp4.append_fsync(self.path, row)


def rerank_pools(sc: Sidecar, cache: RerankCache, corpus: Corpus, questions: list[dict[str, Any]],
                 pools: dict[str, list[str]], rendering: str, shuffled: dict[str, str] | None,
                 rids: Sequence[str] | None = None) -> dict[str, list[str]]:
    qmap = {q["id"]: q["query"] for q in questions}
    todo = [r for r in (rids if rids is not None else sorted(pools)) if r in pools and r in qmap]
    out: dict[str, list[str]] = {}
    lock = threading.Lock()

    def one(rid: str) -> None:
        ids = pools[rid]
        missing = [x for x in ids if x not in corpus.by_id]
        if missing:
            raise SystemExit(f"pooled chunk ids missing from the chunk dump ({len(missing)}; e.g. {missing[0]})")
        docs = [render(corpus.by_id[x], rendering, shuffled) for x in ids]
        k = RerankCache.key(qmap[rid], docs)
        hit = cache.get(k)
        if hit is None:
            idx, s = sc.rerank(qmap[rid], docs) if docs else ([], [])
            cache.put(k, idx, s)
        else:
            idx = hit["i"]
        with lock:
            out[rid] = [ids[i] for i in idx]

    with ThreadPoolExecutor(max_workers=sc.inflight) as ex:
        list(ex.map(one, todo))
    return out


def parse_jobs(spec: str, lists: dict[str, Any], depths: Sequence[int]) -> list[tuple[str, int, str]]:
    """``LEG@D:RENDERING`` items, ``*`` allowed for D and RENDERING, comma-separated."""
    jobs: list[tuple[str, int, str]] = []
    for it in spec.split(","):
        it = it.strip()
        if not it:
            continue
        lhs, rend = it.split(":")
        leg, d = lhs.split("@")
        ds = list(depths) if d == "*" else [int(d)]
        rs = list(RENDERINGS) if rend == "*" else [rend]
        for r in rs:
            if r not in RENDERINGS:
                raise SystemExit(f"unknown rendering {r!r}")
        for D in ds:
            if pool_list(lists, leg, D) is None:
                continue
            for r in rs:
                if (leg, D, r) not in jobs:
                    jobs.append((leg, D, r))
    return jobs


def load_identity_reference(path: Path, arm: str | None = None) -> dict[str, list[str]]:
    """The reference reranked lists for the identity gate: ``{rid: [ids]}``, a
    lists file (its ``live:hybrid@50``), or the part-4 retrieval store
    (``part4-retrieval.jsonl``: rows ``{"k": "retrieval|<qid>|<arm>", "rr": [...]}``,
    the reranked list per question; ``arm`` selects the arm, e.g. ``T512o0``)."""
    if path.suffix == ".jsonl":
        if not arm:
            raise SystemExit("--identity-reference is a part-4 retrieval store: --identity-arm ARM is required")
        out: dict[str, list[str]] = {}
        for row in dp4.read_jsonl_repair_tail(path):
            k = str(row.get("k", "")).split("|")
            if len(k) == 3 and k[0] == "retrieval" and k[2] == arm:
                out[k[1]] = [str(x) for x in row["rr"]]
        if not out:
            raise SystemExit(f"{path.name}: no retrieval rows for arm {arm!r}")
        return out
    ref = json.loads(path.read_text())
    if isinstance(ref, dict) and "lists" in ref:
        ref = ref["lists"].get("live:hybrid@50")
        if ref is None:
            raise SystemExit(f"{path.name}: lists file without live:hybrid@50")
    if not isinstance(ref, dict):
        raise SystemExit(f"{path.name}: not a {{rid: [ids]}} map")
    return {str(k): [str(x) for x in v] for k, v in ref.items()}


def identity_gate(live: dict[str, list[str]], offline: dict[str, list[str]], probes: Sequence[str]) -> dict[str, Any]:
    rows = {}
    for rid in probes:
        a, b = offline.get(rid, [])[:TOP], live.get(rid, [])[:TOP]
        rows[rid] = {"equal": a == b, "same_set": set(a) == set(b), "first_diff": next(
            (i + 1 for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), None)}
    ok = bool(probes) and all(r["equal"] for r in rows.values())
    return {"status": "pass" if ok else "fail", "probes": rows, "n_probes": len(probes)}


def dist_versions(deps_dir: Path, names: Sequence[str] = ("sentence_transformers", "transformers", "torch")) -> dict[str, str | None]:
    out: dict[str, str | None] = dict.fromkeys(names)
    if deps_dir.is_dir():
        for p in deps_dir.glob("*.dist-info"):
            m = re.match(r"^(.+?)-([^-]+)\.dist-info$", p.name)
            if m and m.group(1).replace("-", "_").lower() in out:
                out[m.group(1).replace("-", "_").lower()] = m.group(2)
    return out


def requirement_pin(name: str = "sentence-transformers", req: Path = _SIDECAR_REQ) -> str | None:
    if not req.is_file():
        return None
    for line in req.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line.lower().startswith(name):
            return line[len(name):].strip() or None
    return None


def pin_drift(installed: str | None, spec: str | None) -> bool | None:
    if not installed or not spec:
        return None
    try:
        from packaging.specifiers import SpecifierSet

        return installed not in SpecifierSet(spec)
    except Exception:  # noqa: BLE001
        return None


def snapshot_from_cache(cache_dir: Path, model: str = RERANKER_MODEL) -> str | None:
    ref = cache_dir / f"models--{model.replace('/', '--')}" / "refs" / "main"
    if not ref.is_file():
        ref = cache_dir / "hub" / f"models--{model.replace('/', '--')}" / "refs" / "main"
    return ref.read_text().strip() if ref.is_file() else None


def sidecar_record(sc: Sidecar, args: argparse.Namespace) -> dict[str, Any]:
    ready = sc.ready()
    deps = dist_versions(Path(args.sidecar_deps)) if args.sidecar_deps else {}
    pin = requirement_pin()
    snap = snapshot_from_cache(Path(args.reranker_cache)) if args.reranker_cache else args.reranker_snapshot
    device = ready.get("device")
    return {
        "model": ready.get("model"), "expected_model": args.expect_model, "snapshot": snap,
        "expected_snapshot": args.expect_snapshot,
        "ready": {k: ready.get(k) for k in ("http", "status", "device", "loaded", "error")},
        "max_length": args.sidecar_max_length, "batch_size": args.sidecar_batch_size, "device": device,
        "fp16": (str(device).startswith("cuda") if device else None),
        "settings_source": "declared from the sidecar's launch environment (/ready does not expose them)",
        "installed": deps, "requirements_pin_sentence_transformers": pin,
        "sentence_transformers_outside_pin": pin_drift(deps.get("sentence_transformers"), pin),
    }


def cmd_rerank(args: argparse.Namespace) -> int:
    questions, qmeta = load_questions(Path(args.questions), question_fields(args), args.set_field, args.set_value)
    corpus = load_corpus(args)
    src = load_lists([args.pools])
    if src["manipulation"] is not None and not src["manipulation"].get("ok", False):
        raise SystemExit("the pools file failed its manipulation check; refusing to rerank it")
    lists = src["lists"]
    depths = sorted(src["depths"]) or sorted({parse_key(k)[2] for k in lists if parse_key(k)[0] == "pool"})
    sc = Sidecar(args.rerank_url, args.inflight)
    side = sidecar_record(sc, args)
    out = new_lists("rerank")
    out.update(sidecar=side, questions=qmeta, corpus=corpus.meta, pools_input=src["headers"])
    if side["model"] != args.expect_model:
        out.update(provenance=provenance(), status="sidecar_model_mismatch")
        write_out(Path(args.out), out, questions, args.omit_text)
        log(f"sidecar serves {side['model']!r}, expected {args.expect_model!r}: stopped")
        return EXIT_FAIL
    if side["snapshot"] and args.expect_snapshot and side["snapshot"] != args.expect_snapshot:
        out.update(provenance=provenance(), status="sidecar_snapshot_mismatch")
        write_out(Path(args.out), out, questions, args.omit_text)
        log("reranker snapshot differs from the expected one: stopped")
        return EXIT_FAIL
    shuffled, smeta = shuffle_glossaries(corpus.chunks, args.shuffle_seed, args.shuffle_unit)
    out["shuffle"] = smeta
    cache = RerankCache(Path(args.cache))
    # ---- identity gate first
    ref_key, ref = None, None
    if args.identity_reference:
        ref = load_identity_reference(Path(args.identity_reference), args.identity_arm)
        ref_key = f"file:{Path(args.identity_reference).name}" + (f"|{args.identity_arm}" if args.identity_arm else "")
    elif "live:hybrid@50" in lists:
        ref, ref_key = lists["live:hybrid@50"], "live:hybrid@50"
    pool50 = pool_list(lists, "hybrid", 50)
    if ref is not None and pool50 is not None:
        cands = sorted(r for r in ref if r in pool50)
        probes = (args.probe_ids.split(",") if args.probe_ids
                  else sorted(random.Random(args.shuffle_seed).sample(cands, min(3, len(cands)))))
        off = rerank_pools(sc, cache, corpus, questions, pool50, DEFAULT_RENDERING, shuffled, probes)
        gate = {**identity_gate(ref, off, probes), "reference": ref_key}
        out["identity_gate"] = gate
        log(f"identity gate: {gate['status']} on {len(probes)} probes")
        if gate["status"] != "pass":
            out.update(provenance=provenance(), status="identity_gate_failed",
                       sidecar_calls=sc.calls)
            write_out(Path(args.out), out, questions, args.omit_text)
            log("identity gate failed: offline content+gloss does not reproduce the live A4-50 list; stopped")
            return EXIT_FAIL
    else:
        out["identity_gate"] = {"status": "absent", "reference": None,
                                "note": "no live A4-50 list and no --identity-reference"}
        log("identity gate: ABSENT (no reference list); offline arms are recorded as ungated")
    jobs = parse_jobs(args.jobs, lists, depths)
    for leg, D, rend in jobs:
        key = f"rr:{leg}@{D}:{rend}"
        pools = pool_list(lists, leg, D)
        assert pools is not None
        log(f"rerank {key}: {len(pools)} records")
        out["lists"][key] = rerank_pools(sc, cache, corpus, questions, pools, rend, shuffled)
        base = src["depth"].get(pool_source_key(lists, leg, D) or "", {})
        out["depth"][key] = {"pool": pool_source_key(lists, leg, D), "pool_depth": D,
                             "status": base.get("status", "unknown")}
        if rend == "gloss-only":
            # exploratory only: a glossary-less chunk is scored on an EMPTY passage
            out["depth"][key]["role"] = "exploratory"
            out["depth"][key]["empty_passages"] = sum(
                1 for ids in pools.values() for x in ids if not corpus.by_id[x].glossary)
    if "rr:hybrid@50:content+gloss" in out["lists"] and "live:hybrid@50" in lists:
        a, b = out["lists"]["rr:hybrid@50:content+gloss"], lists["live:hybrid@50"]
        common = sorted(set(a) & set(b))
        out["identity_all"] = {"n": len(common), "equal_top10": sum(a[r][:TOP] == b[r][:TOP] for r in common)}
    out.update(provenance=provenance(), status="complete", sidecar_calls=sc.calls, sidecar_pairs=sc.pairs,
               jobs=[f"{a}@{b}:{c}" for a, b, c in jobs])
    write_out(Path(args.out), out, questions, args.omit_text)
    return EXIT_OK


# ------------------------------------------------------------------ fuse (A9)

def rrf_ids(first: Sequence[str], second: Sequence[str], k: int = sat.RRF_K) -> list[str]:
    """RRF over two id lists; ties keep first-seen order (reranked list first)."""
    score: dict[str, float] = {}
    for lst in (first, second):
        for r, i in enumerate(lst):
            score[i] = score.get(i, 0.0) + 1.0 / (k + r + 1)
    return sorted(score, key=lambda i: -score[i])


DEFAULT_FUSE = ("A9-bm25=rr:hybrid@50:content+gloss+bm25@50,"
                "A9-fused=rr:hybrid@50:content+gloss+hybrid@50")


def cmd_fuse(args: argparse.Namespace) -> int:
    src = load_lists(args.lists)
    lists = src["lists"]
    out = new_lists("fuse")
    out["variants"] = {}
    for it in args.variants.split(","):
        if not it.strip():
            continue
        name, rhs = it.strip().split("=", 1)
        a_key, b_key = rhs.rsplit("+", 1)
        a = lists.get(a_key)
        kind, leg, D, _ = parse_key(b_key)
        b = lists.get(b_key) if kind != "pool" else pool_list(lists, leg, D)
        if a is None or b is None:
            log(f"fuse {name}: absent input ({a_key if a is None else b_key}); skipped")
            out["variants"][name] = {"status": "absent", "first": a_key, "second": b_key}
            continue
        out["lists"][f"fuse:{name}"] = {rid: rrf_ids(a[rid], b.get(rid, []), args.rrf_k) for rid in a}
        st = [src["depth"].get(a_key, {}).get("status"),
              src["depth"].get(pool_source_key(lists, leg, D) or b_key, {}).get("status")]
        out["depth"][f"fuse:{name}"] = {"inputs": [a_key, b_key],
                                        "status": GNE if GNE in st or "under-served" in st else "ok"}
        out["variants"][name] = {"status": "ok", "first": a_key, "second": b_key, "rrf_k": args.rrf_k}
    out.update(provenance=provenance(), inputs=src["headers"], identity_gate=src["identity_gate"])
    write_out(Path(args.out), out, None, True)
    return EXIT_OK


# ------------------------------------------------------------------ census

def _dist(xs: list[int], limit: int) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    a = np.asarray(xs)
    return {"n": int(a.size), "median": float(np.median(a)), "p95": float(np.percentile(a, 95)),
            "max": int(a.max()), "over_max_length": int((a > limit).sum())}


def neighbour_hits(lists: dict[str, Any], gold: dict[str, Any], nb: dict[str, set[str]],
                   rule: str = "any") -> dict[str, Any]:
    out = {}
    for key, per in sorted(lists.items()):
        n_hit = n_hit_not_found = 0
        for rid, lst in per.items():
            g = gold["records"].get(rid)
            if not g or g["excluded"]:
                continue
            gids = {x for ids in g["quotes"] for x in ids}
            near = set().union(*(nb.get(x, set()) for x in gids)) - gids if gids else set()
            top = lst[:TOP]
            if any(x in near for x in top):
                n_hit += 1
                r = rank_of_gold(lst, g, rule)
                if r is None or r > TOP:
                    n_hit_not_found += 1
        out[key] = {"records_with_neighbour_in_top10": n_hit, "of_which_not_found10": n_hit_not_found}
    return out


def bge_tokenizer(model: str) -> Callable[[list[str], list[str] | None], list[int]]:
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model, local_files_only=True)

    def count(a: list[str], b: list[str] | None) -> list[int]:
        out: list[int] = []
        for i in range(0, len(a), 256):
            if b is None:
                enc = tok(a[i:i + 256], add_special_tokens=False, truncation=False)
            else:
                enc = tok(a[i:i + 256], b[i:i + 256], truncation=False)
            out += [len(x) for x in enc["input_ids"]]
        return out

    return count


def census(corpus: Corpus, questions: list[dict[str, Any]], lists: dict[str, Any], gold: dict[str, Any],
           count: Callable[[list[str], list[str] | None], list[int]], max_length: int,
           pool_keys: Sequence[str], renderings: Sequence[str], shuffled: dict[str, str]) -> dict[str, Any]:
    passages = {}
    pairs = {}
    qmap = {q["id"]: q["query"] for q in questions}
    for rend in renderings:
        texts = [render(c, rend, shuffled) for c in corpus.chunks]
        passages[rend] = _dist(count(texts, None), max_length)
        for pk in pool_keys:
            kind, leg, D, _ = parse_key(pk)
            pools = pool_list(lists, leg, D)
            if pools is None:
                pairs[f"{pk}|{rend}"] = {"status": "absent"}
                continue
            qs, ps = [], []
            for rid, ids in pools.items():
                if rid in qmap:
                    for x in ids:
                        qs.append(qmap[rid])
                        ps.append(render(corpus.by_id[x], rend, shuffled))
            pairs[f"{pk}|{rend}"] = _dist(count(qs, ps), max_length)
    return {"tokenizer": "bge-reranker-v2-m3 (XLM-R)", "max_length": max_length,
            "passage_tokens": passages, "pair_tokens": pairs,
            "neighbour_hits": neighbour_hits(lists, gold, corpus.neighbours)}


def cmd_census(args: argparse.Namespace) -> int:
    questions, qmeta = load_questions(Path(args.questions), question_fields(args), args.set_field, args.set_value)
    corpus = load_corpus(args)
    src = load_lists(args.lists)
    gold = json.loads(Path(args.gold).read_text())
    shuffled, smeta = shuffle_glossaries(corpus.chunks, args.shuffle_seed, args.shuffle_unit)
    res = census(corpus, questions, src["lists"], gold, bge_tokenizer(args.tokenizer), args.max_length,
                 [x for x in args.pool_keys.split(",") if x], [x for x in args.renderings.split(",") if x], shuffled)
    out = {"schema": CENSUS_SCHEMA, "provenance": provenance(), "questions": qmeta, "corpus": corpus.meta,
           "shuffle": smeta, "inputs": src["headers"], "max_length_source": "declared (--max-length)", **res}
    write_out(Path(args.out), out, questions, args.omit_text)
    return EXIT_OK


# ------------------------------------------------------------------ stats

def arm_map(lists: dict[str, Any], depths: Sequence[int], prefer: str = "live") -> dict[str, dict[str, Any]]:
    """Named arms of the prereg -> the list each is read from (``key``) and the
    pool it reranks (``pool``: leg, D). ``prefer`` picks the API-live or the
    offline list for A4/A5/A6 when both exist."""
    have_pool = lambda leg, d: pool_source_key(lists, leg, d) is not None  # noqa: E731
    out: dict[str, dict[str, Any]] = {}
    bm_key = pool_source_key(lists, "bm25", TOP)
    if bm_key:
        out["A0"] = {"key": bm_key, "route": "pool prefix", "pool": None}
    vk = pool_source_key(lists, "vector", TOP)
    if vk:
        out["A1"] = {"key": vk, "route": "pool prefix", "pool": None}
    if "hybrid@10" in lists:
        out["A2"] = {"key": "hybrid@10", "route": "pool", "pool": None}
    if "hybrid@50" in lists:
        out["A3"] = {"key": "hybrid@50", "route": "pool prefix", "pool": None}

    def reranked(name: str, leg: str, d: int) -> None:
        live, off = f"live:{leg}@{d}", f"rr:{leg}@{d}:{DEFAULT_RENDERING}"
        order = [live, off] if prefer == "live" else [off, live]
        for k in order:
            if k in lists:
                out[name] = {"key": k, "route": "api" if k.startswith("live:") else "offline",
                             "pool": (leg, d) if have_pool(leg, d) else None}
                return

    for d in depths:
        reranked(f"A4-{d}", "hybrid", d)
        reranked(f"A5-{d}", "bm25", d)
    reranked("A6", "vector", 50)
    for rend in RENDERINGS:
        k = f"rr:hybrid@50:{rend}"
        if k in lists:
            out[f"A7-{rend}"] = {"key": k, "route": "offline", "pool": ("hybrid", 50) if have_pool("hybrid", 50) else None,
                                 "role": "exploratory (E2: reranker input)"}
            if rend == "gloss-only":
                out[f"A7-{rend}"]["note"] = ("exploratory only: a glossary-less chunk is scored on an empty passage; "
                                             "not a reranker-input variant of the shipped path")
    for k in lists:
        if k.startswith("fuse:"):
            out[k[5:]] = {"key": k, "route": "offline fuse", "pool": None}
    return out


def ranks_for(lists: dict[str, Any], key: str, gold: dict[str, Any], rids: Sequence[str], rule: str) -> dict[str, int | None]:
    per = lists[key]
    return {r: (rank_of_gold(per[r], gold["records"][r], rule) if r in per else None) for r in rids}


def rank_summary(ranks: dict[str, int | None], list_len: int) -> dict[str, Any]:
    n = len(ranks)
    got = sorted(r for r in ranks.values() if r is not None)
    q = (np.percentile(got, [25, 50, 75]).tolist() if got else [None] * 3)
    return {"n": n, "found_in_list": len(got), "median_rank": q[1], "iqr": [q[0], q[2]],
            "share_rank_11_50": (sum(1 for r in got if 11 <= r <= 50) / n) if n else None,
            "mrr50": (sum(1.0 / r for r in got if r <= 50) / n) if n else None,
            **{f"found@{k}": (sum(1 for r in got if r <= k) if list_len >= k else None) for k in FOUND_K}}


def outcome(ranks: dict[str, int | None], rids: Sequence[str], k: int) -> list[int]:
    return [int(ranks[r] is not None and ranks[r] <= k) for r in rids]  # type: ignore[operator]


def decomposition(arm_ranks: dict[str, int | None], pool_ranks: dict[str, int | None], rids: Sequence[str],
                  d: int) -> dict[str, Any]:
    """``n = (n - ceiling@D) + ordering loss@D + found@10``, record by record. A
    record found in the top 10 whose gold was not in the captured pool (only
    possible when the live arm drew a different pool) is counted apart."""
    in_pool = {r: pool_ranks[r] is not None and pool_ranks[r] <= d for r in rids}  # type: ignore[operator]
    found = {r: arm_ranks[r] is not None and arm_ranks[r] <= TOP for r in rids}  # type: ignore[operator]
    out_pool = sum(1 for r in rids if not in_pool[r] and not found[r])
    loss = sum(1 for r in rids if in_pool[r] and not found[r])
    f = sum(1 for r in rids if found[r])
    outside = sum(1 for r in rids if found[r] and not in_pool[r])
    return {"n": len(rids), "ceiling": sum(in_pool.values()), "not_in_pool": out_pool, "ordering_loss": loss,
            "found@10": f, "found_but_not_in_captured_pool": outside,
            "identity": out_pool + loss + f == len(rids)}


def per_cluster(clusters: Sequence[str], a: Sequence[int], b: Sequence[int]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"gained": 0, "lost": 0, "n": 0})
    for c, x, y in zip(clusters, a, b, strict=True):
        out[c]["n"] += 1
        out[c]["gained"] += int(x > y)
        out[c]["lost"] += int(x < y)
    return {k: dict(v) for k, v in sorted(out.items())}


def attainable(m_nonzero: int, alpha_test: float) -> bool:
    """Can the exact one-sided sign-flip reach ``alpha_test`` with ``m`` non-zero clusters? (min p = 2^-m)"""
    return m_nonzero > 20 or 2.0 ** (-m_nonzero) <= alpha_test


def run_contrast(clusters: list[str], a: list[int], b: list[int], kind: str, an: dict[str, Any], seed: int,
                 alternative: str = "greater") -> dict[str, Any]:
    r = dp4.contrast_test(clusters, a, b, kind, an, seed, alternative)
    r["per_cluster"] = per_cluster(clusters, a, b)
    return r


HYPOTHESES = {
    "H1": {"a": ("A0", "found10"), "b": ("A4-50", "found10"), "type": "superiority",
           "statement": "found@10(A0) - found@10(A4-50) > 0"},
    "H2": {"a": ("bm25", "ceiling50"), "b": ("hybrid", "ceiling50"), "type": "superiority",
           "statement": "ceiling@50(hybrid) - ceiling@50(bm25) < 0, tested as bm25 - hybrid > 0"},
    "H3": {"a": ("A5-50", "found10"), "b": ("A0", "found10"), "type": "noninferiority",
           "statement": "found@10(A5-50) - found@10(A0) > -delta"},
}


def inactive_reranker(lists: dict[str, Any], rids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Per reranked list (``live:`` API A4/A5/A6, ``rr:`` offline A7 and its
    A4/A5 stand-ins): how many records' top 10 equal the un-reranked pool's top
    10. ``gated`` when that is every record: a wired-but-inactive reranker
    answers ``rerank=true`` with the fused order (found10.py gates ``base``
    identical to ``norerank`` the same way; mirrored here, not imported: that
    runner compares whole served lists of one request shape, this harness top
    10s against a separately captured pool)."""
    out: dict[str, dict[str, Any]] = {}
    for key, per in lists.items():
        kind, leg, d, _ = parse_key(key)
        if kind not in ("live", "rr"):
            continue
        pool = pool_list(lists, leg, d)
        if pool is None:
            continue
        common = [r for r in rids if r in per and r in pool]
        same = sum(per[r][:TOP] == pool[r][:TOP] for r in common)
        out[key] = {"pool": pool_source_key(lists, leg, d), "records": len(common), "identical_top10": same,
                    "gated": bool(common) and same == len(common)}
    return out


def holm_family(hyps: dict[str, dict[str, Any]], order: Sequence[str], alpha: float) -> list[str]:
    """Holm over the whole confirmatory family, in place. A gated or absent
    member (or one without a p) enters at p = 1.0, so the family size never
    shrinks (found10's convention; prereg). Sets ``p_holm`` / ``significant``
    and the mid-p ``holm_mid`` / ``significant_mid`` on every member that has
    a p, plus the labelled sensitivity ``holm_drop_gated`` (Holm over the
    evaluable members only, the old behaviour) on the evaluable ones. Returns
    the evaluable members."""
    def p_of(h: str, key: str) -> float:
        r = hyps[h]
        v = r.get(key)
        return 1.0 if (r.get("p") is None or r.get("gate") or r.get("status") == "absent" or v is None) \
            else float(v)

    testable = [h for h in order if hyps[h].get("p") is not None and not hyps[h].get("gate")
                and hyps[h].get("status") != "absent"]
    hm = cs.holm([p_of(h, "p") for h in order], alpha)
    hmid = cs.holm([p_of(h, "p_mid") for h in order], alpha)
    for i, h in enumerate(order):
        if hyps[h].get("p") is None:
            continue
        hyps[h].update(p_holm=hm["p_adj"][i], significant=bool(hm["reject"][i]),
                       holm_mid=hmid["p_adj"][i], significant_mid=bool(hmid["reject"][i]),
                       holm_entered_as=None if h in testable else 1.0)
    if testable:
        drop = cs.holm([float(hyps[h]["p"]) for h in testable], alpha)
        for h, padj, rej in zip(testable, drop["p_adj"], drop["reject"], strict=True):
            hyps[h]["holm_drop_gated"] = {"p_holm": padj, "significant": bool(rej), "m": len(testable),
                                          "label": "sensitivity: gated/absent members dropped from the family"}
    return testable


def analyse(lists: dict[str, Any], depth_info: dict[str, Any], gold: dict[str, Any], *, depths: Sequence[int],
            an: dict[str, Any], seed: int, rule: str = "any", prefer: str = "live",
            identity: dict[str, Any] | None = None, per_record: bool = True,
            boost_check: dict[str, Any] | None = None) -> dict[str, Any]:
    recs = gold["records"]
    rids = sorted(r for r, g in recs.items() if not g["excluded"])
    inactive = inactive_reranker(lists, rids)
    boost_gate = (boost_check or {}).get("gate")
    clusters = [recs[r]["cluster"] for r in rids]
    n_clusters = len(set(clusters))
    arms = arm_map(lists, depths, prefer)
    gate_status = (identity or {}).get("status", "absent")

    def list_status(key: str) -> list[str]:
        """GATE-NOT-EVALUABLE reasons that attach to a list (its own depth, its pool's depth, the identity gate)."""
        reasons = []
        kind, leg, d, _ = parse_key(key)
        for k in {key, pool_source_key(lists, leg, d) if kind in ("pool", "rr", "live") else None} - {None}:
            st = (depth_info.get(k) or {}).get("status")
            if st in (GNE, "under-served"):
                reasons.append(f"depth read-back: {k} under-served")
        if kind == "fuse":
            if (depth_info.get(key) or {}).get("status") == GNE:
                reasons.append(f"depth read-back: an input of {key} under-served")
        if kind in ("rr", "fuse") and gate_status == "fail":
            reasons.append("identity gate failed")
        inputs = [key] + (list((depth_info.get(key) or {}).get("inputs") or []) if kind == "fuse" else [])
        for k in inputs:
            if (inactive.get(k) or {}).get("gated"):
                i = inactive[k]
                reasons.append(f"inactive reranker: {k} top {TOP} equals its un-reranked pool {i['pool']} "
                               f"on every record ({i['identical_top10']}/{i['records']})")
        if boost_gate and (kind == "fuse" or leg in ("bm25", "hybrid")):
            reasons.append(f"glossary boost: {boost_gate}")
        return reasons

    ranks: dict[str, dict[str, int | None]] = {}
    arm_out: dict[str, Any] = {}
    for name, spec in arms.items():
        rk = ranks_for(lists, spec["key"], gold, rids, rule)
        ranks[name] = rk
        llen = max((len(v) for v in lists[spec["key"]].values()), default=0)
        if spec["route"] == "pool prefix":
            llen = min(llen, TOP if name in ("A0", "A1", "A3") else llen)
            rk = {r: (x if x is not None and x <= llen else None) for r, x in rk.items()}
            ranks[name] = rk
        arm_out[name] = {"list": spec["key"], "route": spec["route"], "summary": rank_summary(rk, llen),
                         "gate": list_status(spec["key"]) or None,
                         "identity_gate": gate_status if spec["route"].startswith("offline") else None,
                         "identical_to_pool": inactive.get(spec["key"]),
                         **{k: spec[k] for k in ("role", "note") if k in spec}}
    # The confirmatory family needs a PASSED identity gate when any of its reranked
    # arms (A4-50, A5-50) is the offline rerank rather than the API's own list: an
    # absent or failed gate makes H1-H3 GATE-NOT-EVALUABLE on this set (owner
    # decision; supply --identity-reference to `rerank` to run the gate).
    family_offline = [a for a in ("A4-50", "A5-50") if (arm_out.get(a) or {}).get("route") == "offline"]
    family_gate = ([f"identity gate {gate_status}: {', '.join(family_offline)} reranked offline "
                    "(run the gate with --identity-reference)"]
                   if family_offline and gate_status != "pass" else [])
    # ceilings (A10) and decompositions
    ceilings: dict[str, Any] = {}
    for leg in LEGS:
        for d in depths:
            k = pool_source_key(lists, leg, d)
            if k is None:
                continue
            pr = pool_list(lists, leg, d)
            assert pr is not None
            rk = {r: (rank_of_gold(pr[r], recs[r], rule) if r in pr else None) for r in rids}
            ranks[f"pool:{leg}@{d}"] = rk
            ceilings[f"{leg}@{d}"] = {"ceiling": sum(1 for x in rk.values() if x is not None), "n": len(rids),
                                      "from": k, "gate": list_status(k) or None}
    decomp = {}
    for name, spec in arms.items():
        if spec["pool"] is None:
            continue
        leg, d = spec["pool"]
        decomp[name] = {"pool": f"{leg}@{d}", **decomposition(ranks[name], ranks[f"pool:{leg}@{d}"], rids, d)}

    def vec(arm: str, what: str) -> list[int] | None:
        if what == "found10":
            return outcome(ranks[arm], rids, TOP) if arm in ranks else None
        key = f"pool:{arm}@50"
        return outcome(ranks[key], rids, 50) if key in ranks else None

    def arm_gate(arm: str, what: str) -> list[str]:
        if what == "found10":
            return list((arm_out.get(arm) or {}).get("gate") or [])
        c = ceilings.get(f"{arm}@50")
        return (c or {}).get("gate") or []

    hyps: dict[str, Any] = {}
    alpha = float(an["alpha"])
    for h, spec in HYPOTHESES.items():
        a, b = vec(*spec["a"]), vec(*spec["b"])
        base = {"statement": spec["statement"], "type": spec["type"],
                "contrast": f"{spec['a'][0]}:{spec['a'][1]} - {spec['b'][0]}:{spec['b'][1]}"}
        if a is None or b is None:
            hyps[h] = {**base, "status": "absent", "p": None}
            continue
        reasons = arm_gate(*spec["a"]) + arm_gate(*spec["b"]) + list(family_gate)
        if n_clusters < MIN_CLUSTERS:
            reasons.append(f"fewer than {MIN_CLUSTERS} clusters ({n_clusters})")
        r = run_contrast(clusters, a, b, spec["type"], an, seed)
        m = r.get("n_pages_nonzero")
        if spec["type"] == "superiority" and m is not None and not attainable(int(m), alpha / len(HYPOTHESES)):
            reasons.append(f"{m} non-zero clusters: alpha/{len(HYPOTHESES)} not attainable")
        hyps[h] = {**base, **r, "gate": reasons or None}
    testable = holm_family(hyps, list(HYPOTHESES), alpha)
    for r in hyps.values():
        if r.get("status") == "absent":
            continue
        if r.get("gate"):
            r["status"] = GNE
        elif r["type"] == "superiority":
            r["status"] = "confirmed" if r.get("significant") else "not confirmed"
        else:
            r["status"] = "confirmed" if r.get("significant") else "not shown non-inferior"
    # exploratory contrasts: two-sided, uncorrected, Bonferroni count beside
    expl: dict[str, Any] = {}
    refs = [x for x in ("A0", "A4-50") if x in ranks]
    for ref in refs:
        for name in arms:
            if name == ref:
                continue
            a, b = outcome(ranks[name], rids, TOP), outcome(ranks[ref], rids, TOP)
            r = run_contrast(clusters, a, b, "superiority", an, seed, "two-sided")
            expl[f"{name} - {ref}"] = {**r, "gate": (arm_out[name]["gate"] or []) + (arm_out[ref]["gate"] or []) or None}
    for r in expl.values():
        r["bonferroni_m"] = len(expl)
        r["note"] = "exploratory: uncorrected p; not a finding"
    out = {"n_records": len(recs), "n_included": len(rids), "excluded": gold.get("excluded", {}),
           "n_clusters": n_clusters, "gold_rule": rule, "arms": arm_out, "ceilings": ceilings,
           "decomposition": decomp, "hypotheses": hyps, "exploratory": expl,
           "inactive_reranker": inactive, "boost_check": boost_check,
           "family": {"procedure": "Holm over H1-H3", "alpha": alpha, "delta": float(an["delta"]),
                      "tested": testable, "identity_gate": gate_status, "offline_arms": family_offline,
                      "family_size": len(HYPOTHESES),
                      "note": ("Holm over all of H1-H3: a GATE-NOT-EVALUABLE or absent member enters at p = 1.0 "
                               "(the family never shrinks silently; found10's convention, prereg). "
                               "holm_drop_gated is the sensitivity that drops those members instead")}}
    if per_record:
        out["per_record"] = {r: {"cluster": recs[r]["cluster"], **{a: ranks[a][r] for a in ranks}} for r in rids}
    return out


def repeat_check(main: dict[str, Any], rep: dict[str, Any], gold: dict[str, Any], rule: str) -> dict[str, Any]:
    out = {}
    rids = sorted(r for r, g in gold["records"].items() if not g["excluded"])
    for k in sorted(set(main) & set(rep)):
        a, b = main[k], rep[k]
        common = [r for r in rids if r in a and r in b]
        fa = [rank_of_gold(a[r], gold["records"][r], rule) for r in common]
        fb = [rank_of_gold(b[r], gold["records"][r], rule) for r in common]
        out[k] = {"n": len(common), "found10_differs": sum((x is not None and x <= TOP) != (y is not None and y <= TOP)
                                                           for x, y in zip(fa, fb, strict=True)),
                  "top10_differs": sum(a[r][:TOP] != b[r][:TOP] for r in common)}
    return out


def cmd_stats(args: argparse.Namespace) -> int:
    src = load_lists(args.lists)
    gold = json.loads(Path(args.gold).read_text())
    depths = sorted({int(x) for x in args.depths.split(",")})
    an = {"n_perm": args.n_perm, "n_boot": args.n_boot, "ci_level": args.ci_level, "delta": args.delta,
          "alpha": args.alpha}
    boost_check = (src["manipulation"] or {}).get("boost_check")
    if boost_check is None and any(h.get("backend") == "api" for h in src["headers"]):
        boost_check = {"status": "absent", "boost_check": "absent",
                       "gate": "an API pools file without the glossary boost read-back (it predates the probe)"}
    res = analyse(src["lists"], src["depth"], gold, depths=depths, an=an, seed=args.seed, rule=args.gold_rule,
                  prefer=args.prefer, identity=src["identity_gate"], per_record=args.per_record,
                  boost_check=boost_check)
    if args.repeat:
        rep = load_lists(args.repeat)
        res["repeat"] = repeat_check(src["lists"], rep["lists"], gold, args.gold_rule)
    depth_table = {k: {x: v.get(x) for x in ("requested", "served_min", "served_max", "n_under_served",
                                              "n_exhausted", "status", "response_top_k", "degraded_responses",
                                              "pool", "pool_depth", "inputs") if x in v}
                   for k, v in sorted(src["depth"].items())}
    out = {"schema": RESULTS_SCHEMA, "provenance": provenance(), "set": args.set_label,
           "inputs": src["headers"], "gold_input": {"file": Path(args.gold).name,
                                                    "sha256": sha256_file(Path(args.gold)),
                                                    "questions": gold.get("questions")},
           "reranker": {"model": RERANKER_MODEL, "expected_snapshot": RERANKER_SNAPSHOT,
                        "sidecar": src["sidecar"]},
           "manipulation_check": src["manipulation"], "identity_gate": src["identity_gate"],
           "shuffle": src["shuffle"], "depth_read_back": depth_table,
           "analysis": {"seed": args.seed, "n_perm": args.n_perm, "n_boot": args.n_boot,
                        "ci_level": args.ci_level, "delta": args.delta, "alpha": args.alpha,
                        "test": "cluster sign-flip permutation (standard p; exact when <= 20 non-zero clusters), "
                                "cluster bootstrap percentile CI, McNemar descriptive "
                                "(deployed_docs_part4.contrast_test)"},
           **res}
    write_out(Path(args.out), out, None, True)
    for h, r in res["hypotheses"].items():
        log(f"{h}: {r.get('status')} (p={r.get('p')}, holm={r.get('p_holm')}, gate={r.get('gate')})")
    return EXIT_OK


# ------------------------------------------------------------------ part4-rescore (D3)

#: The writer's schema (``part4_glossary.RESULTS_SCHEMA``): what the step-7 docs-A result is.
PART4_SCHEMA = "part4-glossary/1"
PART4_RESCORE_SCHEMA = "reranker-part4-rescore/1"
#: The arms D3 pre-commits the re-score to (prereg section 12, D3). No other arm is read.
D3_ARMS = ("A0", "A2", "A4-50")
PART4_FOUND_RULE = ("part-4 found: every gold quote of the record occurs (parts 2-4 normalisation, text hit) "
                    "in the chunks of the cell's delivered context; for a top-10 cell that is the top 10 of "
                    "the cell's ranked list. Not the gold-chunk-id rule of `stats` (gold rule 'any'): it is "
                    "an ALL-quotes rule (nearest `stats` analogue: --gold-rule all), and a text hit counts "
                    "any chunk whose text holds the quote, so a quote whose text recurs in another chunk "
                    "(overlap, repeated passages) can be found without the gold chunk id being in the top "
                    "10. The file holds no ranks or chunk ids, so this cannot be re-scored by id here.")


def parse_part4_map(items: list[str] | None) -> dict[str, str]:
    """``ARM=CELL`` per ``--map``: which per-item cell of the part-4 result holds
    the arm's top-10 ``found``. ARM must be one of :data:`D3_ARMS`; nothing is
    mapped by default (the cell names are the writer's budget conditions, e.g.
    ``k10``, and which arm a cell is must be stated, not guessed)."""
    out: dict[str, str] = {}
    for v in items or []:
        arm, sep, cell = v.partition("=")
        if not sep or not cell or arm not in D3_ARMS:
            raise SystemExit(f"--map wants ARM=CELL with ARM one of {list(D3_ARMS)}, got {v!r}")
        if arm in out:
            raise SystemExit(f"--map: arm {arm!r} given twice")
        out[arm] = cell
    if not out:
        raise SystemExit(f"part4-rescore needs at least one --map ARM=CELL (ARM in {list(D3_ARMS)})")
    return out


def read_part4_result(obj: dict[str, Any], condition: str | None, part4_arm: str,
                      mapping: dict[str, str]) -> dict[str, Any]:
    """Per-record 0/1 top-10 outcomes of the mapped arms from a ``part4-glossary/1``
    results object (``per_item[record_id][glossary condition][part-4 arm][cell]
    ["found"]``, as :func:`part4_glossary.rescore` writes it). Strict: every record
    must hold every mapped cell with a boolean ``found``; anything else exits
    naming what the file does hold."""
    if obj.get("schema") != PART4_SCHEMA:
        raise SystemExit(f"part4-rescore: not a {PART4_SCHEMA} result (schema {obj.get('schema')!r})")
    per_item = obj.get("per_item")
    if not isinstance(per_item, dict) or not per_item:
        raise SystemExit("part4-rescore: the result has no per_item records (a counts-only copy?)")
    cond = condition or obj.get("reference_condition")
    names = [c.get("name") for c in obj.get("glossary_conditions") or [] if isinstance(c, dict)]
    if not cond or (names and cond not in names):
        raise SystemExit(f"part4-rescore: glossary condition {cond!r} is not in the result ({names})")
    cells: set[str] = set()
    out: dict[str, dict[str, int]] = {a: {} for a in mapping}
    for rid in sorted(per_item):
        arms = (per_item[rid] or {}).get(cond)
        if not isinstance(arms, dict) or not isinstance(arms.get(part4_arm), dict):
            raise SystemExit(f"part4-rescore: record {rid!r} has no {cond}/{part4_arm} cell "
                             f"(arms: {sorted(arms) if isinstance(arms, dict) else None})")
        row = arms[part4_arm]
        cells.update(row)
        for a, cell in mapping.items():
            found = (row.get(cell) or {}).get("found") if isinstance(row.get(cell), dict) else None
            if not isinstance(found, bool):
                raise SystemExit(f"part4-rescore: --map {a}={cell}: record {rid!r} has no boolean "
                                 f"found in cell {cell!r}; the cells this result holds: {sorted(cells | set(row))}")
            out[a][rid] = int(found)
    return {"condition": cond, "outcomes": out, "cells": sorted(cells), "records": sorted(per_item)}


def part4_rescore(obj: dict[str, Any], *, condition: str | None, part4_arm: str, mapping: dict[str, str],
                  clusters: dict[str, str] | None, an: dict[str, Any], seed: int,
                  per_record: bool = True) -> dict[str, Any]:
    """D3's pre-committed check on the step-7 file: found@10 per mapped arm and,
    when A0 and A4-50 are both mapped and clusters are given, H1 by the same
    test as ``stats`` (:func:`run_contrast`, superiority, one-sided). A2 enters
    as a found@10 count; no other contrast is computed here. The coverage of
    :data:`D3_ARMS` is reported per arm (``absent``: no ``--map`` for it)."""
    got = read_part4_result(obj, condition, part4_arm, mapping)
    rids = got["records"]
    oc = got["outcomes"]
    arms = {a: {"cell": mapping[a], "n": len(rids), "found@10": sum(oc[a].values())} for a in mapping}
    coverage = {a: (f"mapped: cell {mapping[a]!r}" if a in mapping else "absent") for a in D3_ARMS}
    h1: dict[str, Any]
    spec = HYPOTHESES["H1"]
    if not {"A0", "A4-50"} <= set(mapping):
        h1 = {"status": "not computed", "reason": "A0 and A4-50 must both be mapped"}
    elif clusters is None:
        h1 = {"status": "not computed", "reason": "no --questions (clusters) given"}
    else:
        missing = [r for r in rids if r not in clusters]
        if missing:
            raise SystemExit(f"part4-rescore: {len(missing)} result records are not in the questions file "
                             f"(e.g. {missing[:3]})")
        a, b = [oc["A0"][r] for r in rids], [oc["A4-50"][r] for r in rids]
        h1 = {"statement": spec["statement"], "type": spec["type"],
              **run_contrast([clusters[r] for r in rids], a, b, spec["type"], an, seed)}
    prov = obj.get("provenance") or {}
    out: dict[str, Any] = {
        "schema": PART4_RESCORE_SCHEMA,
        "role": ("D3 pre-committed check on the step-7 result; the citable family A is the fresh run "
                 "under this harness (prereg section 12, D3)"),
        "source": {"schema": obj.get("schema"), "citable": obj.get("citable"),
                   "commit": prov.get("commit"), "dirty": prov.get("dirty"), "untracked": prov.get("untracked"),
                   "glossary_condition": got["condition"], "part4_arm": part4_arm,
                   "cells_available": got["cells"]},
        "found_rule": PART4_FOUND_RULE,
        "d3_coverage": coverage, "d3_complete": all(a in mapping for a in D3_ARMS),
        "arms": arms, "hypotheses": {"H1": h1},
    }
    if per_record:
        out["per_record"] = {r: {a: oc[a][r] for a in mapping} for r in rids}
    return out


def cmd_part4_rescore(args: argparse.Namespace) -> int:
    mapping = parse_part4_map(args.map)
    path = Path(args.part4_result)
    obj = json.loads(path.read_text(encoding="utf-8"))
    questions, qmeta = (load_questions(Path(args.questions), question_fields(args), args.set_field, args.set_value)
                        if args.questions else (None, None))
    clusters = {q["id"]: q["cluster"] for q in questions} if questions else None
    an = {"n_perm": args.n_perm, "n_boot": args.n_boot, "ci_level": args.ci_level, "delta": args.delta,
          "alpha": args.alpha}
    res = part4_rescore(obj, condition=args.condition, part4_arm=args.part4_arm, mapping=mapping,
                        clusters=clusters, an=an, seed=args.seed, per_record=args.per_record)
    out = {"provenance": provenance(), "set": args.set_label,
           "input": {"file": path.name, "sha256": sha256_file(path)}, "questions": qmeta,
           "analysis": {"seed": args.seed, "n_perm": args.n_perm, "n_boot": args.n_boot,
                        "ci_level": args.ci_level, "alpha": args.alpha}, **res}
    write_out(Path(args.out), out, questions, args.omit_text)
    for a, r in res["arms"].items():
        log(f"{a}: found@10 {r['found@10']}/{r['n']} (cell {r['cell']})")
    if not res["d3_complete"]:
        absent = [a for a, v in res["d3_coverage"].items() if v == "absent"]
        log(f"part4-rescore: D3 arms absent: {absent}")
        if not args.allow_partial:
            return EXIT_FAIL
    return EXIT_OK


# ------------------------------------------------------------------ power table

def power_table(seed: int = SEED, n_pages: int = 208, n_questions: int = 735, delta: float = 0.05,
                alpha: float = 0.05, n_family: int = 3, n_sims: int = 2000, n_flips: int = 2000,
                layout: list[int] | None = None, discordances: Sequence[float] = (0.05, 0.10, 0.15, 0.20, 0.25),
                ) -> dict[str, Any]:
    """Power of one hypothesis at the Holm worst case ``alpha / n_family``
    (:func:`chunktrial_stats.simulate_power`, via the part-4 module's import, one
    seed for every cell). H1/H2 superiority at a discordant share of .3/.4/.5;
    H3 non-inferiority at ``delta`` with a true net share of 0 / -.01 / -.02;
    direction page-ICC 0 and 0.1. One reader (retrieval outcomes need no grader).

    Not :func:`deployed_docs_part4.power_table` itself: that one hard-codes the
    part-4 family of four and its H2-H4 labels."""
    lay = layout or cs.even_split(n_questions, n_pages)
    rows = []
    for share_icc in (0.0, 0.1):
        for disc in discordances:
            cells = ([("H1/H2", "superiority", s, None, 0.0) for s in (0.3, 0.4, 0.5)]
                     + [("H3", "noninferiority", net / disc, net, delta) for net in (0.0, -0.01, -0.02)])
            for hyp, kind, share, net, dlt in cells:
                r = cs.simulate_power(n_pages, lay, disc, share, share_icc=share_icc, alpha=alpha, n_family=n_family,
                                      delta=dlt, n_sims=n_sims, n_flips=n_flips, seed=seed)
                rows.append({"hypothesis": hyp, "type": kind, "discordance": disc,
                             "discordant_share": round(share, 6), "true_net_share": round(disc * share, 6),
                             "share_icc": share_icc, "alpha_test": r["alpha_test"], "power": r["power"],
                             "mc_se": round(r["mc_se"], 4), **({"ni_true_net": net} if net is not None else {})})
    return {"schema": POWER_SCHEMA, "n_pages": n_pages, "n_questions": int(sum(lay)), "layout": lay,
            "delta": delta, "alpha": alpha, "n_family": n_family, "n_sims": n_sims, "n_flips": n_flips,
            "seed": seed, "discordance_icc": 0.0, "rows": rows}


def cmd_power(args: argparse.Namespace) -> int:
    layout = None
    n_pages, n_q = args.pages, args.questions_n
    if args.gold:
        g = json.loads(Path(args.gold).read_text())
        cnt = Counter(r["cluster"] for r in g["records"].values() if not r["excluded"])
        layout = sorted(cnt.values(), reverse=True)
        n_pages, n_q = len(layout), sum(layout)
    t = power_table(args.seed, n_pages, n_q, args.delta, args.alpha, 3, args.n_sims, args.n_flips, layout)
    t["provenance"] = provenance()
    write_out(Path(args.out), t, None, True)
    return EXIT_OK


# ------------------------------------------------------------------ CLI

def question_fields(args: argparse.Namespace) -> dict[str, str | None]:
    return {"id": args.id_field, "query": args.query_field, "quotes": args.quote_field, "cluster": args.cluster_field}


def _q_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--questions", required=True, help="question/gold JSONL (private; never committed)")
    p.add_argument("--id-field")
    p.add_argument("--query-field")
    p.add_argument("--quote-field")
    p.add_argument("--cluster-field", help="cluster (page / paper); default: cluster|paper|doc_id")
    p.add_argument("--set-field", help="keep only rows whose SET_FIELD equals --set-value")
    p.add_argument("--set-value")
    p.add_argument("--omit-text", action=argparse.BooleanOptionalAction, default=True,
                   help="keep query and quote text out of every output (default on; checked before writing)")


def _corpus_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--chunks", help="chunk dump JSONL: chunk_id, doc_id, content, symbol_glossary, prev/next ids")
    p.add_argument("--part4-arm", help="docs-A route: build this part-4 arm in-process (needs --private-config)")
    p.add_argument("--private-config", default=os.environ.get("CHUNKTRIAL_PRIVATE_CONFIG"))


def _shuffle_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--shuffle-seed", type=int, default=SEED)
    p.add_argument("--shuffle-unit", choices=("string", "entry"), default="string")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pools", help="capture first-stage lists per leg and depth (+ the gold file)")
    _q_args(p)
    _corpus_args(p)
    p.add_argument("--backend", choices=("api", "inprocess"), required=True)
    p.add_argument("--depths", default=",".join(map(str, DEPTHS)))
    p.add_argument("--legs", default=",".join(LEGS))
    p.add_argument("--live", help="API only: reranked arms to capture, e.g. hybrid@50,bm25@50,vector@50")
    p.add_argument("--base-url", help="API base URL (required for --backend api; no default)")
    p.add_argument("--api-key-env", default="RAGSTACK_API_KEY",
                   help="env var holding the credential (refused when unset; '' = no authentication)")
    p.add_argument("--auth-header", choices=sorted(AUTH_HEADERS), default="x-api-key",
                   help="x-api-key: X-API-Key (a RAGStack key); authorization: Authorization, no Bearer "
                        "prefix (a BV-BRC user token)")
    p.add_argument("--collection")
    p.add_argument("--server-settings", help="JSON of the server's launch settings /v1/config does not expose")
    p.add_argument("--expect", action="append", help="override an expected setting, KEY=VALUE")
    p.add_argument("--inflight", type=int, default=MAX_INFLIGHT)
    p.add_argument("--embed-urls")
    p.add_argument("--embed-model", default=sat.SFR_MODEL)
    p.add_argument("--emb-dir", help="embedding cache directory (hash-keyed, no text)")
    p.add_argument("--multiplier", type=int, default=PREREG_SETTINGS["retrieval_candidate_multiplier"])
    p.add_argument("--glossary-boost", type=float, default=PREREG_SETTINGS["text_symbol_glossary_boost"])
    p.add_argument("--boost-declared", type=float, default=None,
                   help="API route: the server's TEXT_SYMBOL_GLOSSARY_BOOST, used only when the behavioural probe "
                        "is not testable (recorded as declared, not read back)")
    p.add_argument("--allow-short", action="store_true",
                   help="keep under-served lists, marked GATE-NOT-EVALUABLE, instead of failing")
    p.add_argument("--out", required=True)
    p.add_argument("--gold-out", required=True)
    p.set_defaults(fn=cmd_pools)

    p = sub.add_parser("rerank", help="rerank captured pools through the sidecar (identity gate first)")
    _q_args(p)
    _corpus_args(p)
    _shuffle_args(p)
    p.add_argument("--pools", required=True)
    p.add_argument("--rerank-url", required=True)
    p.add_argument("--jobs", default="hybrid@*:content+gloss,bm25@*:content+gloss,vector@50:content+gloss,hybrid@50:*",
                   help="LEG@D:RENDERING items; * for every depth / rendering")
    p.add_argument("--cache", required=True, help="score cache JSONL (hash-keyed)")
    p.add_argument("--identity-reference", help="reference reranked lists when live A4-50 was not captured: "
                   "{rid: [ids]}, a lists file, or a part-4 retrieval store (.jsonl, with --identity-arm)")
    p.add_argument("--identity-arm", help="the part-4 arm to read from a .jsonl --identity-reference (e.g. T512o0)")
    p.add_argument("--probe-ids", help="comma-separated probe record ids (default: 3 seeded)")
    p.add_argument("--inflight", type=int, default=MAX_INFLIGHT)
    p.add_argument("--expect-model", default=RERANKER_MODEL)
    p.add_argument("--expect-snapshot", default=RERANKER_SNAPSHOT)
    p.add_argument("--reranker-cache", help="the sidecar's HF cache dir (snapshot read from refs/main)")
    p.add_argument("--reranker-snapshot", help="declared snapshot hash when the cache is not readable")
    p.add_argument("--sidecar-deps", help="the sidecar's deps dir (installed versions read from *.dist-info)")
    p.add_argument("--sidecar-max-length", type=int, help="the sidecar's MAX_LENGTH from its launch environment")
    p.add_argument("--sidecar-batch-size", type=int, help="the sidecar's RERANK_BATCH_SIZE")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_rerank)

    p = sub.add_parser("fuse", help="A9: RRF of the reranker order with a first-stage order")
    p.add_argument("--lists", nargs="+", required=True)
    p.add_argument("--variants", default=DEFAULT_FUSE, help="NAME=RERANKED_KEY+FIRST_STAGE_KEY,...")
    p.add_argument("--rrf-k", type=int, default=sat.RRF_K)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_fuse)

    p = sub.add_parser("census", help="reranker input lengths (XLM-R tokens) and neighbour hits")
    _q_args(p)
    _corpus_args(p)
    _shuffle_args(p)
    p.add_argument("--lists", nargs="+", required=True)
    p.add_argument("--gold", required=True)
    p.add_argument("--tokenizer", default=RERANKER_MODEL)
    p.add_argument("--max-length", type=int, default=4096, help="the sidecar's MAX_LENGTH (declared)")
    p.add_argument("--pool-keys", default="hybrid@50,bm25@50")
    p.add_argument("--renderings", default=",".join(RENDERINGS))
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_census)

    p = sub.add_parser("stats", help="decomposition, rank-of-gold, H1-H3 (Holm), exploratory contrasts")
    p.add_argument("--lists", nargs="+", required=True)
    p.add_argument("--gold", required=True)
    p.add_argument("--set-label", required=True, help="alias of the set (e.g. docs-A/T512o0, papers-OC)")
    p.add_argument("--depths", default=",".join(map(str, DEPTHS)))
    p.add_argument("--prefer", choices=("live", "offline"), default="live",
                   help="A4/A5/A6 from the API-live list or the offline rerank when both exist")
    p.add_argument("--gold-rule", choices=("any", "all"), default="any")
    p.add_argument("--delta", type=float, default=0.05)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--n-perm", type=int, default=10_000)
    p.add_argument("--n-boot", type=int, default=10_000)
    p.add_argument("--ci-level", type=float, default=0.90)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--repeat", nargs="+", help="lists of the noise-floor repeat (A0, A4-50)")
    p.add_argument("--per-record", action=argparse.BooleanOptionalAction, default=True,
                   help="per-record gold ranks by id (default on)")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("part4-rescore",
                       help="D3: the pre-committed re-score of a part4-glossary/1 result (A0, A2, A4-50)")
    p.add_argument("--part4-result", required=True, help="a part4-glossary/1 results JSON (private)")
    p.add_argument("--part4-arm", required=True, help="the part-4 chunker arm (e.g. T512o0)")
    p.add_argument("--condition", help="glossary condition (default: the result's reference_condition)")
    p.add_argument("--map", action="append", metavar="ARM=CELL", required=True,
                   help=f"which per-item cell holds ARM's top-10 found; ARM in {list(D3_ARMS)} (repeatable)")
    p.add_argument("--questions", help="question JSONL for the clusters (H1); optional")
    p.add_argument("--id-field")
    p.add_argument("--query-field")
    p.add_argument("--quote-field")
    p.add_argument("--cluster-field", help="cluster (page / paper); default: cluster|paper|doc_id")
    p.add_argument("--set-field")
    p.add_argument("--set-value")
    p.add_argument("--omit-text", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--set-label", required=True, help="alias of the set (e.g. docs-A/T512o0)")
    p.add_argument("--allow-partial", action="store_true",
                   help="exit 0 although a D3 arm is unmapped (it is recorded as absent either way)")
    p.add_argument("--delta", type=float, default=0.05)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--n-perm", type=int, default=10_000)
    p.add_argument("--n-boot", type=int, default=10_000)
    p.add_argument("--ci-level", type=float, default=0.90)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--per-record", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_part4_rescore)

    p = sub.add_parser("power-table", help="the power floor (Holm worst case alpha/3)")
    p.add_argument("--pages", type=int, default=208)
    p.add_argument("--questions-n", type=int, default=735)
    p.add_argument("--gold", help="take the cluster layout from a gold file instead")
    p.add_argument("--delta", type=float, default=0.05)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--n-sims", type=int, default=2000)
    p.add_argument("--n-flips", type=int, default=2000)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_power)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
