#!/usr/bin/env python
"""Alias study (OA-1000): what the runner, the judge pipeline and the statistics share.

The DRAFT prereg (``docs/plans/results/alias-study-2026-11/prereg/PREREG-alias-study.md``)
fixes the design these constants encode: the four corpus arms, the two glossary arms, the
three retrievers (§5), the seed strings (§5.5), the record-set identity (§4.1) and the
balanced-set rule (§4.1, *How the balanced set and its sha were computed*). Nothing private
is in this file: record ids, paper ids and paths come from private configs.

**Seeds.** The prereg fixes seed *strings* (``261101|alias|perm`` …). Orderings use them as
written: ``sha256(<seed>|<part>|…)`` ascending (:func:`seeded_key`). A numeric RNG (the
permutation test, the bootstrap, the power simulation) is seeded with the first 8 bytes of
``sha256(<seed>)`` read big-endian (:func:`seed_int`); the prereg does not say how a string
becomes a numpy seed, so this rule is recorded in every output beside the string.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

SEED_BASE = "261101|alias"
SEEDS: dict[str, str] = {
    "neighbours": f"{SEED_BASE}|neighbours",
    "random1": f"{SEED_BASE}|random|1",
    "random2": f"{SEED_BASE}|random|2",
    "noise": f"{SEED_BASE}|noise",
    "boot": f"{SEED_BASE}|boot",
    "perm": f"{SEED_BASE}|perm",
    "judge_order": f"{SEED_BASE}|judge|order",
    "drift": f"{SEED_BASE}|drift",
    "spot_records": f"{SEED_BASE}|spot|records",
    "spot_judge": f"{SEED_BASE}|spot|judge",
}
SEED_RULE = "numpy seed = int.from_bytes(sha256(seed string)[:8], 'big'); orderings sha256('<seed>|<parts>') ascending"

#: §4.1: the accepted-record file's sha256 and the balanced set's identity sha
ACCEPTED_SHA256 = "09fc5ca434f50c5d0e527f2424dd31a2d59e019f59762c7f961dd7d7804ad24f"
BALANCED_SHA256 = "2be86f8c5fd7dafaeb5a183c4dd55cc3e7b31366a48267424916dcbcf2557a0f"
BALANCED_GOAL = {"G": 2, "C": 1}

CORPUS_ARMS = ("base", "neighbour", "random1", "random2")
GLOSSARY_ARMS = ("none", "merged")
#: prereg §5.2 retriever id -> the round-3 runner's condition name (found10.DEFAULT_CONDITIONS)
RETRIEVERS: dict[str, str] = {"default": "base", "bm25": "bm25", "norerank": "norerank"}
CONDITION_TO_RETRIEVER = {v: k for k, v in RETRIEVERS.items()}
#: §5.4: the noise repeat of each corpus arm is default x none, run a second time
REPEAT_GLOSSARY, REPEAT_RETRIEVER = "none", "default"
#: §2 / §6.2: the arm a pair's passage paper belongs to (drift and spot-check strata)
ARM_GROUPS = ("base", "neighbour", "random")
FOUND_K = 10
GATE = "GATE-NOT-EVALUABLE"


def seed_int(seed: str) -> int:
    """The numpy seed for a prereg seed string (module docstring)."""
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")


def seeded_key(seed: str, *parts: str) -> str:
    """``sha256('<seed>|<part>|…')`` hex: the sort key of a seeded content-blind order."""
    return hashlib.sha256("|".join((seed, *parts)).encode("utf-8")).hexdigest()


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(p: str | Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def arm_group(corpus_arm: str) -> str:
    """``random1`` / ``random2`` -> ``random``; ``base`` and ``neighbour`` as they are."""
    if corpus_arm not in CORPUS_ARMS:
        raise ValueError(f"unknown corpus arm {corpus_arm!r}")
    return "random" if corpus_arm.startswith("random") else corpus_arm


def leg_id(corpus: str, glossary: str, retriever: str, repeat: int = 1) -> str:
    """``<corpus>|<glossary>|<retriever>`` (``#2`` appended for the noise repeat)."""
    base = f"{corpus}|{glossary}|{retriever}"
    return base if repeat == 1 else f"{base}#{repeat}"


def all_legs() -> list[tuple[str, str, str, int]]:
    """The 28 legs of §5.4: 24 cells and one noise repeat (default x none) per corpus arm."""
    legs = [(c, g, r, 1) for c in CORPUS_ARMS for g in GLOSSARY_ARMS for r in RETRIEVERS]
    legs += [(c, REPEAT_GLOSSARY, REPEAT_RETRIEVER, 2) for c in CORPUS_ARMS]
    return legs


# ------------------------------------------------------------------ records

def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """The accepted-record file (``glossbench_alias_waves`` summary output): every record
    must carry ``record_id``, ``paper``, ``kind`` (G / C), ``question``, ``primary``,
    ``alias``, ``alias_kind`` and exactly one gold chunk (§4.1 *gold*)."""
    rows = read_jsonl(path)
    seen: set[str] = set()
    for i, r in enumerate(rows, 1):
        for k in ("record_id", "paper", "kind", "question", "primary", "alias", "alias_kind", "gold_chunks"):
            if k not in r:
                raise SystemExit(f"{Path(path).name}:{i}: record lacks {k!r}")
        if r["kind"] not in ("G", "C"):
            raise SystemExit(f"{Path(path).name}:{i}: kind {r['kind']!r} is not G or C")
        if not isinstance(r["gold_chunks"], list) or len(r["gold_chunks"]) != 1:
            raise SystemExit(f"{Path(path).name}:{i}: exactly one gold chunk per record (prereg §4.1)")
        if r["record_id"] in seen:
            raise SystemExit(f"{Path(path).name}:{i}: duplicate record_id")
        seen.add(r["record_id"])
    return rows


def balanced_papers(records: Iterable[Mapping[str, Any]], goal: Mapping[str, int] | None = None) -> set[str]:
    """The satisfied papers (§4.1): the committed ``glossbench_alias_waves.satisfied`` over
    each paper's accepted G alias keys (``glossbench_alias_checks.alias_key``) and C count."""
    import glossbench_alias_checks as ac
    import glossbench_alias_waves as aw

    goal = dict(goal or BALANCED_GOAL)
    by: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in records:
        by[str(r["paper"])].append(r)
    out = set()
    for paper, rs in by.items():
        ps = aw.PaperState(paper, acc_keys={ac.alias_key(r["alias"], r["alias_kind"]) for r in rs if r["kind"] == "G"},
                           acc_c=sum(r["kind"] == "C" for r in rs))
        if aw.satisfied(ps, goal):
            out.add(paper)
    return out


def balanced_ids_sha(ids: Iterable[str]) -> str:
    """§4.1: sha256 of the record ids, sorted, each followed by ``\\n``."""
    return sha_text("".join(f"{i}\n" for i in sorted(ids)))


def split_records(records: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(balanced, descriptive)``: records on satisfied papers carry Layer 1 (§4.2), the rest
    are the descriptive leg. Each record gets ``balanced: bool``."""
    papers = balanced_papers(records)
    bal: list[dict[str, Any]] = []
    desc: list[dict[str, Any]] = []
    for r in records:
        rr = {**r, "balanced": str(r["paper"]) in papers}
        (bal if rr["balanced"] else desc).append(rr)
    return bal, desc


def verify_record_set(path: str | Path, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The study's record-set identity (§4.1): the file's sha256 and the balanced set's id
    sha must be the prereg's. Raises SystemExit on any mismatch."""
    fsha = sha256_file(path)
    bal, desc = split_records(records)
    bsha = balanced_ids_sha(r["record_id"] for r in bal)
    problems = []
    if fsha != ACCEPTED_SHA256:
        problems.append(f"accepted-record file sha256 {fsha[:12]}… != the prereg's {ACCEPTED_SHA256[:12]}…")
    if bsha != BALANCED_SHA256:
        problems.append(f"balanced-set id sha {bsha[:12]}… != the prereg's {BALANCED_SHA256[:12]}…")
    if problems:
        raise SystemExit("record set is not the frozen one: " + "; ".join(problems))
    return {"accepted_sha256": fsha, "balanced_sha256": bsha, "records": len(records), "balanced": len(bal),
            "descriptive": len(desc)}


# ------------------------------------------------------------------ chunks

def read_chunk_exports(paths: Iterable[str | Path]) -> dict[str, dict[str, Any]]:
    """``chunk_id -> {content, doc_id, paper, title}`` over several exports. A chunk id seen
    twice must carry the same content (an expanded arm holds the base chunks with identical
    ids and content, prereg §5.1); a conflict is refused."""
    import _glossbench_r2 as g

    out: dict[str, dict[str, Any]] = {}
    for path in paths:
        with g._open_text(path) as fh:
            for i, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                row = g.parse_chunk(json.loads(line), f"{Path(path).name}:{i}")
                rec = {"content": row.content, "doc_id": row.doc_id, "paper": g.paper_of(row, "path_root"),
                       "title": row.title}
                prev = out.get(row.chunk_id)
                if prev is not None and prev["content"] != rec["content"]:
                    raise SystemExit(f"{Path(path).name}:{i}: chunk id {row.chunk_id[:8]}… seen with other content")
                out.setdefault(row.chunk_id, rec)
    return out


# ------------------------------------------------------------------ public artifacts

def string_values(obj: Any) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            yield str(k)
            yield from string_values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from string_values(v)


def public_problems(obj: Any, forbidden: Iterable[str], texts: Iterable[str] = ()) -> list[str]:
    """§11: a public artifact holds counts, rates, model ids, versions, shas only. Every string
    in ``obj`` (keys included) is checked: equal to (or containing) a forbidden id (record,
    paper, chunk ids), or containing a private text (a question, a passage: any 40-character
    window of it), is a problem. Returns the problems (empty = publishable)."""
    ids = {x for x in forbidden if x}
    windows: set[str] = set()
    for t in texts:
        t = " ".join(str(t).split())
        if len(t) >= 40:
            windows.update(t[i:i + 40] for i in range(0, len(t) - 39, 20))
    bad = []
    for s in string_values(obj):
        flat = " ".join(s.split())
        if s in ids or any(i in s for i in ids if len(i) >= 8):
            bad.append("an id")
        elif any(w in flat for w in windows):
            bad.append("private text")
    return bad
