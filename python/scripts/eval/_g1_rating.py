#!/usr/bin/env python
"""Shared vocabulary for the G1 human relevance-rating apparatus (pure stdlib).

The pilot's quality track (``docs/g1-retrieval-protocol.md`` §4.3, §4.4) rests on
graded ``(query, chunk)`` judgments, and on a **measured** agreement between the
LLM judge and human raters — κ(judge–human) < 0.4 demotes the whole quality track
to descriptive-only. That makes the human labels a load-bearing artefact rather
than a side quest, so the three scripts that produce them
(:mod:`g1_make_queries`, :mod:`g1_make_pool`, :mod:`g1_agreement`) share one
vocabulary, defined here:

* **The unit of judgment** is a ``(query_id, chunk_id)`` pair with a *content
  addressed* :func:`pair_id`. Deterministic ids are what let a judgment produced
  by a browser two weeks ago join against a pool regenerated today; a positional
  or random id would not survive a re-pool.
* **The grade scale** is 0/1/2 (:data:`GRADE_LABELS`), the protocol's graded
  scale, with ``_stats.ndcg_at_k``'s ``2**grade - 1`` gain in mind.
* **Blinding** is enforced mechanically, not by convention
  (:func:`blinding_violations`). §4.4's "config leakage" row requires the judge —
  human or model — to see only ``(query, chunk text)``. A human rater who can see
  ``cell_id`` or the LLM's label is not an independent label source, and
  κ(judge–human) computed over such labels is meaningless in the direction that
  matters (it is inflated). The denylist is applied at *both* ends: the generator
  refuses to write an offending assignment and the browser tool refuses to load
  one.
* **The IDF-weighted query↔source-chunk overlap** (:func:`idf_overlap`), which
  threat **T1b** (§9) requires to be recorded as a per-query covariate so the
  lexical bias that LLM query generation imports is *measurable* rather than
  hidden.

Nothing here imports ``ragstack`` or touches a store, so every unit test of the
rating apparatus runs offline with no Qdrant, no ES and no embedding fleet.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# The grade scale
# --------------------------------------------------------------------------- #
GRADES: tuple[int, ...] = (0, 1, 2)

GRADE_LABELS: dict[int, str] = {
    0: "irrelevant",
    1: "partially answers",
    2: "fully answers",
}

#: A 0-vs-2 split is a *qualitative* disagreement (one rater saw an answer where
#: the other saw noise) and is routed to adjudication; a 1-vs-2 or 0-vs-1 split
#: is a boundary call, which the consensus rule resolves without a third rater.
ADJUDICATION_SPREAD = 2

TOOL_VERSION = "g1-rating/1"

PROTOCOL_PATH = "docs/g1-retrieval-protocol.md"


# --------------------------------------------------------------------------- #
# Blinding (protocol §4.4, "config leakage" row)
# --------------------------------------------------------------------------- #
#: Exact field names that must never reach a rater's screen. ``grade``/``label``
#: are here because an assignment carrying a *pre-filled* grade is an anchoring
#: attack on the rater; the rater's own grade is written by the tool on export,
#: into a different file.
BLINDING_DENY_KEYS: frozenset[str] = frozenset(
    {
        "cell",
        "cell_id",
        "config",
        "config_id",
        "grade",
        "grades",
        "judge",
        "judge_grade",
        "judge_label",
        "label",
        "llm_grade",
        "llm_label",
        "mode",
        "params",
        "rank",
        "ranks",
        "rerank_candidates",
        "rerank_enabled",
        "retrieval_method",
        "rrf_k",
        "run_id",
        "score",
        "scores",
        "leg_depth",
        "depth",
        "system",
        "run",
        "best_rank",
        "n_cells",
        "cells",
        "stratum",
        "llm_stratum",
    }
)

#: Substring probes, applied to every key, that catch fields the exact list did
#: not anticipate (``hybrid_rank``, ``bm25_score``, ``judge_reason``, …). A new
#: pooling column should fail closed rather than silently reach a rater.
BLINDING_DENY_SUBSTRINGS: tuple[str, ...] = (
    "bm25",
    "cell",
    "config",
    "dense",
    "hybrid",
    "judge",
    "ndcg",
    "rerank",
    "retriev",
    "rrf",
    "vector",
)

#: The complete set of keys an assignment record may carry. Anything else is a
#: violation even if it dodges the denylist — an allowlist is the only form of
#: this check that stays correct as the pooling script grows columns.
ASSIGNMENT_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "pair_id",
        "assignment_id",
        "rater_id",
        "set",
        "query_id",
        "query",
        "chunk_text",
        "doc_title",
    }
)

ASSIGNMENT_REQUIRED_KEYS: tuple[str, ...] = (
    "pair_id",
    "assignment_id",
    "query_id",
    "query",
    "chunk_text",
)


def blinding_violations(record: dict[str, Any]) -> list[str]:
    """Field names in ``record`` that would break blinding or the allowlist.

    Returns a sorted list of offending keys — empty means the record is safe to
    show a rater. Both the writer (:mod:`g1_make_pool`) and the reader (the
    browser tool) run this, deliberately: a hand-edited or hand-assembled
    assignment file is exactly the case where the writer-side check is absent.
    """
    bad: set[str] = set()
    for key in record:
        low = key.lower()
        if low in BLINDING_DENY_KEYS:
            bad.add(key)
        elif any(probe in low for probe in BLINDING_DENY_SUBSTRINGS):
            bad.add(key)
        elif key not in ASSIGNMENT_ALLOWED_KEYS:
            bad.add(key)
    return sorted(bad)


def assert_blind(records: Iterable[dict[str, Any]]) -> None:
    """Raise :class:`ValueError` on the first record that would leak."""
    for i, rec in enumerate(records):
        bad = blinding_violations(rec)
        if bad:
            raise ValueError(
                f"assignment record {i} ({rec.get('pair_id', '?')}) carries "
                f"blinding-violating or unknown field(s): {', '.join(bad)}"
            )
        missing = [k for k in ASSIGNMENT_REQUIRED_KEYS if not rec.get(k)]
        if missing:
            raise ValueError(
                f"assignment record {i} is missing required field(s): {', '.join(missing)}"
            )


# --------------------------------------------------------------------------- #
# Ids and digests
# --------------------------------------------------------------------------- #
def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def digest(*parts: str) -> str:
    """Short content address over an ordered list of strings."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()[:16]


def pair_id(query_id: str, chunk_id: str) -> str:
    """The judgment unit's content-addressed id.

    Deterministic in ``(query_id, chunk_id)`` only — *not* in the pool, the run,
    the rater or the ordering — so judgments collected in one round join cleanly
    against a pool rebuilt in another, and a pair that appears in two rounds is
    recognisably the same pair (which is how judge self-consistency and
    test-retest reliability become computable at all).
    """
    return "p-" + digest(query_id, chunk_id)


def query_id_for(text: str, source: str, seq: int) -> str:
    """``g1q-<source-initial><seq>-<digest>`` — readable and content-addressed."""
    return f"g1q-{source[0]}{seq:04d}-{digest(text)[:8]}"


# --------------------------------------------------------------------------- #
# JSONL / CSV IO
# --------------------------------------------------------------------------- #
def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: not valid JSON ({exc})") from exc
            if not isinstance(rec, dict):
                raise ValueError(f"{path}:{lineno}: expected a JSON object")
            out.append(rec)
    return out


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    return p


def write_json(path: str | Path, obj: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def read_table(path: str | Path) -> list[dict[str, Any]]:
    """Read ``.jsonl`` / ``.json`` / ``.csv`` / ``.tsv`` into a list of dicts.

    The human query-submission format (SOP §3) is deliberately whatever a domain
    expert already has open: a spreadsheet export or a JSONL dump. Both land in
    the same normalizer, so the ingestion path — and therefore the provenance —
    is identical.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".jsonl":
        return read_jsonl(p)
    if suffix == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("queries", [])
        if not isinstance(data, list):
            raise ValueError(f"{p}: expected a JSON array (or {{'queries': [...]}})")
        return [dict(r) for r in data]
    if suffix in (".csv", ".tsv"):
        delim = "\t" if suffix == ".tsv" else ","
        with p.open(encoding="utf-8-sig", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh, delimiter=delim)]
    raise ValueError(f"{p}: unsupported table format {suffix!r} (want .jsonl/.json/.csv/.tsv)")


def iter_glob(patterns: Sequence[str]) -> Iterator[Path]:
    """Expand shell-style patterns and bare directories into files, sorted.

    A bare directory expands to its ``*.jsonl`` children, because the sweep
    writes one rankings file per cell into ``raw/`` and pointing at the directory
    is what an operator will type.
    """
    seen: set[Path] = set()
    for pat in patterns:
        p = Path(pat)
        if p.is_dir():
            hits = sorted(p.glob("*.jsonl"))
        elif p.exists():
            hits = [p]
        else:
            root = Path(p.anchor) if p.is_absolute() else Path()
            rel = p.relative_to(p.anchor) if p.is_absolute() else p
            hits = sorted(root.glob(str(rel)))
        for h in hits:
            if h not in seen:
                seen.add(h)
                yield h


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def git_info(repo: Path | None = None) -> dict[str, Any]:
    """``{commit, branch, dirty}`` for the checkout the script runs from."""
    cwd = str(repo or Path(__file__).resolve().parents[3])

    def _run(*args: str) -> str:
        try:
            return subprocess.run(
                args, cwd=cwd, capture_output=True, text=True, timeout=15, check=False
            ).stdout.strip()
        except Exception:  # noqa: BLE001 - provenance must never fail a run
            return ""

    status = _run("git", "status", "--porcelain")
    return {
        "commit": _run("git", "rev-parse", "HEAD"),
        "branch": _run("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status),
    }


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def protocol_version() -> dict[str, Any]:
    """SHA-256 of the pre-registration, as every G1 manifest must record (§8.1)."""
    path = repo_root() / PROTOCOL_PATH
    return {"path": PROTOCOL_PATH, "sha256": sha256_file(path)}


def manifest_header(tool: str, argv: Sequence[str] | None = None) -> dict[str, Any]:
    """The provenance block every artefact of this apparatus carries."""
    return {
        "schema_version": "ragstack.g1_rating/v1",
        "tool": tool,
        "tool_version": TOOL_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(),
        "protocol": protocol_version(),
        "git": git_info(),
        "python": sys.version.split()[0],
        "argv": list(argv if argv is not None else sys.argv),
    }


# --------------------------------------------------------------------------- #
# Lexical overlap — the T1b covariate (§9)
# --------------------------------------------------------------------------- #
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")

#: A small stop list. Kept short on purpose: IDF already suppresses common words,
#: and an aggressive list would silently *lower* measured overlap, which is the
#: wrong direction for a covariate whose job is to expose a bias.
STOPWORDS: frozenset[str] = frozenset(
    """a an and are as at be by for from has have how in is it its of on or that the
    to was were what when where which who why with does do did can could""".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


def idf_table(documents: Iterable[str]) -> dict[str, float]:
    """Smoothed IDF over a chunk corpus: ``ln((N + 1) / (df + 1)) + 1``.

    Protocol §9 T1b(b) requires the IDF to come from the P200 index so the
    covariate is comparable across rungs — i.e. pass the *largest* rung's chunk
    text here, not the query set's source chunks.
    """
    df: dict[str, int] = {}
    n = 0
    for doc in documents:
        n += 1
        for term in set(tokenize(doc)):
            df[term] = df.get(term, 0) + 1
    return {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}


def _default_idf(idf: dict[str, float]) -> float:
    """IDF for a term unseen in the corpus — the maximum observed, since an
    unseen term is maximally rare. Falls back to 1.0 on an empty table."""
    return max(idf.values()) if idf else 1.0


def idf_overlap(query: str, chunk: str, idf: dict[str, float]) -> dict[str, float]:
    """IDF-weighted and unweighted overlap between a query and its source chunk.

    ``idf_overlap`` is the share of the query's *IDF mass* that also occurs in
    the chunk — 1.0 means every content term of the query is verbatim in the text
    it is supposed to retrieve, which is the known-item proxy §5.7 disqualifies.
    ``jaccard`` is the unweighted set measure §9 also asks for.
    """
    q_terms = set(tokenize(query))
    c_terms = set(tokenize(chunk))
    if not q_terms:
        return {"idf_overlap": 0.0, "jaccard": 0.0, "n_query_terms": 0}
    fallback = _default_idf(idf)
    total = sum(idf.get(t, fallback) for t in q_terms)
    shared = sum(idf.get(t, fallback) for t in q_terms & c_terms)
    union = q_terms | c_terms
    return {
        "idf_overlap": round(shared / total, 6) if total else 0.0,
        "jaccard": round(len(q_terms & c_terms) / len(union), 6) if union else 0.0,
        "n_query_terms": len(q_terms),
    }


def tertile_edges(values: Sequence[float]) -> list[float]:
    """The two cut points of the overlap tertiles (§9 T1b(c) sensitivity split)."""
    vals = sorted(values)
    if not vals:
        return []
    return [
        vals[max(0, min(len(vals) - 1, int(round(q * (len(vals) - 1)))))] for q in (1 / 3, 2 / 3)
    ]


def distribution(values: Sequence[float]) -> dict[str, Any]:
    """``{mean, p50, p90, min, max, tertile_edges, n}`` for a manifest block."""
    vals = sorted(values)
    if not vals:
        return {"n": 0, "mean": None, "p50": None, "p90": None, "min": None, "max": None,
                "tertile_edges": []}

    def _q(q: float) -> float:
        return vals[max(0, min(len(vals) - 1, int(round(q * (len(vals) - 1)))))]

    return {
        "n": len(vals),
        "mean": round(sum(vals) / len(vals), 6),
        "p50": round(_q(0.5), 6),
        "p90": round(_q(0.9), 6),
        "min": round(vals[0], 6),
        "max": round(vals[-1], 6),
        "tertile_edges": [round(e, 6) for e in tertile_edges(vals)],
    }
