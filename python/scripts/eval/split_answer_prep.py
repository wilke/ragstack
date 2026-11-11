#!/usr/bin/env python3
"""Split answers (exploratory): the step-1 tooling of ``docs/plans/results/split-answer-2026-11/PLAN.md``
§3.2. Offline: no store, no retrieval, no LLM, no gateway. Subcommands:

``items`` (§3.2 (a))
    The neighbour items: per confirmatory pair its max-overlap adopted chunk M with its
    neighbours, as production delivers them, written as a private synthetic chunk export
    (``neighbour-export.jsonl``) and ``build-control`` seed rows (``neighbour-seed.jsonl``), plus the
    counts-only ``neighbour-items.json``. The 150 negative-control items come from a
    ``draw-negative`` directory.
``draw-negative`` (§3.2 (b))
    The 150 stage-A *not* pairs of the negative control, drawn after a committed seed file.
``wall-read`` / ``wall-counts`` (§3.2 (c))
    The private reading sheet of the 10 split windows a section edge crosses, and the
    counts-only result of the read.
``report`` (review nit d)
    The counts-only result of the neighbour registry's ``final.jsonl``, per group and context
    window, each positive group with and without its document-edge items (§3.2 (b)9).

Conventions (``rechunk_prep.py``'s): every path comes from the command line; private outputs are
written once (``alias_study_spot.write_private``) and never inside the repository; public outputs are
counts only, checked against the run's ids and texts (``alias_control_chunks.write_summary`` /
``write_public`` and, for a file meant to be committed, no path either) before anything is written.

**The passage is production's.** :func:`neighbour_passage` links the adopted chunks of M's document
with the production linker (``ragstack.ingestion.chunkers.link_neighbors``), walks them with the
production ``ragstack.retrieval.retriever.expand_context`` over a read-only in-memory store of
those chunks, builds the ``Source`` the query router builds (``api/routers/query.py``), and renders
it with ``ragstack.llm.RagGenerator.format_context`` at the production default budget
(``Settings.llm_max_context_chars``, read from the field default: no environment is read). Only
the citation prefix ``[1] `` is stripped (§3.1 rule 2). Nothing here re-implements a marker: the
literal §3.1 concatenation (:func:`literal_passage`) is the *check* of §3.1 rule 3 (no trim), never
the text.

**Item identity** (§3.2 (a)1): ``chunk_id = "nbr-w<w>:" + <M's chunk id>``; the pair id is the judge's
unchanged ``sha256(record|chunk)[:16]``. Two records sharing M share the export row; one record whose
two windows map to one M has two seed rows that ``build-control`` merges into one pair.

**Edge** (§3.2 (a)2): ``before`` when M is its document's first chunk (no context before),
``after`` when it is the last, else ``none``. At ``w = 2`` a side cut short to one neighbour by
the document edge is not an edge (its neighbour counts are in the export row's metadata). A
one-chunk document has no context: the passage is M itself, M's label is reused and no item is
built (counted as skipped).

**The negative draw's prefix** (§3.2 (b)8, review nit c): the prefix counts candidates AFTER the walk's
one-pair-per-(record, arm) and one-pair-per-window filter. The cells are walked in the fixed order
paper (``same``, ``other``) x arm (``base``, ``neighbour``, ``random``); in each, the pool pairs are
taken in seeded order and a pair becomes a candidate only when its (record, arm) and its window are
not already held by a candidate of any cell (a candidate holds them whether or not it is later
drawn), until the cell holds ``3 x quota`` candidates or its pool is exhausted. Only these
candidates are chunked; the first ``quota`` eligible ones in walk order are drawn; a cell with
fewer is refused, never extended.

**Neighbours of the negatives** (review nit a) come from the draw's own chunk run
(``<negative dir>/chunks/chunks-control.jsonl``); the draw's gate makes every document it shares
with the confirmatory run's ``chunks-control.jsonl`` identical there.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench", _HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_control_chunks as acc  # noqa: E402
import alias_study_common as asc  # noqa: E402
import rechunk_prep as rp  # noqa: E402

SCHEMA = {"items": "split-answer-items/1", "negative": "split-answer-negative-draw/1",
          "wall_sheet": "split-answer-wall-sheet/1", "wall_key": "split-answer-wall-key/1",
          "wall_answers": "split-answer-wall-answers/1", "wall_counts": "split-answer-wall-read-counts/1",
          "report": "split-answer-report/1"}
PREREG = "split-answer-2026-11"

#: §3.2 (a)1: a neighbour item's chunk id is this prefix, ``w<w>:`` and M's chunk id
NBR_PREFIX = rp.NEIGHBOUR_ID_PREFIX
WINDOWS = (1, 2)
#: the groups of PLAN §3 (``split_feasibility.group_of``)
GROUPS = ("split", "recovered", "primary_only", "kept_identical", "kept_changed")
LOST_GROUPS = ("split", "recovered", "primary_only")
POSITIVE_GROUPS = (*LOST_GROUPS, "kept_changed")
NEGATIVE_GROUP = "negative"
ITEM_SETS = ("positive", "negative")
#: §3.1 rule 2 and the export row's ``metadata.formatter``
FORMATTER = "RagGenerator.format_context, with '[1] ' stripped"
CITATION_PREFIX = "[1] "
#: §3.1, quoted from ``llm.py:184-187``: the reference of the rule-3 check (never the text)
SPEC_BEFORE, SPEC_PASSAGE, SPEC_AFTER, SPEC_ELLIPSIS = "(context before)\n", "(passage)\n", "(context after)\n", "…"
EDGES = ("none", "before", "after")

#: §5 (b): the items the plan counts (``feasibility.json`` and the OPEN-2 draw); a different count is
#: reported (``plan_counts``), not refused: the count is what the inputs give
PLAN_ITEMS: dict[str, dict[str, int]] = {
    "split": {"context_window_1": 18, "context_window_2": 2}, "recovered": {"context_window_1": 53},
    "primary_only": {"context_window_1": 4}, "kept_changed": {"context_window_1": 219},
    NEGATIVE_GROUP: {"context_window_1": 150}}
PLAN_TOTAL = 446
#: the private inputs the feasibility counts were made on (``artifacts/feasibility.json``
#: ``inputs_sha256``) and the confirmatory draw's sample (``alias-boundary-confirm-2026-11``
#: ``artifacts/draw.json``); another file is refused
PINNED_INPUTS: dict[str, str] = {
    "pairs_seed": "f76f96882009c11242ec79b38d2be621ffa25eab86c521537a566426237407b8",
    "chunks_control": "e9f00602eca4c6676b7e5968f08d83a01fcbdf36ca910d577419f5cca8748035",
    "chunks_retest": "6cb6c3825740db1cbfc10149fd5283e3fcbe7ab3e3e3465e528557d8556ac12f",
    "label_table": "62e63538710a46c84963e6be9826b60f174f4d90b0750c00444b9d444e16158b",
    "confirm_sample": "227aff47835003c083c2bc222b4c049071eb3753f7d7b7cfd86c6b961b8f3061",
    "control_sample": acc.CONFIRM_CONTROL_SAMPLE_SHA256,
}
STAGE_A_COMMIT_SHA256 = acc.CONFIRM_STAGE_A_COMMIT_SHA256

#: §3.2 (b)
NEGATIVE_SEED_PROPOSED = "261111|split|negative|draw"
PAPERS = ("same", "other")
NEGATIVE_QUOTAS = {"same": 100, "other": 50}
PREFIX_FACTOR = 3
NEGATIVE_STRATUM = "not"
NEGATIVE_EXCLUSIONS = ("unresolved", "one_chunk_document", "edge", "trim", "collision_positive")

#: §3.2 (c)
WALL_MARK = "⟦SECTION WALL⟧"
WALL_N = 10
WALL_ORDER_SEED = "261111|split|wall-read|order"
WALL_ANSWERS = ("needs_both", "one_side", "unclear")
WALL_ROLES = ("owner", "reviewer-agent")

PATH_MARKERS = ("/rag/", "/home/", "/tmp/", "/etc/", "/proc/", "file:")
CALLS = {"store": 0, "retrieval": 0, "llm": 0, "gateway": 0}


# ------------------------------------------------------------------ small helpers

def sha(path: Path) -> str:
    return asc.sha256_file(path)


def read(path: Path) -> list[dict[str, Any]]:
    return rp.read_rows(Path(path))


def stats(xs: Sequence[float]) -> dict[str, Any]:
    """``split_feasibility.stats``: n, mean and the nearest-rank quantiles."""
    if not xs:
        return {"n": 0}
    s = sorted(xs)

    def q(f: float) -> float:
        return s[min(len(s) - 1, int(f * len(s)))]
    return {"n": len(s), "mean": round(statistics.fmean(s), 3), "p10": q(0.1), "p50": q(0.5), "p90": q(0.9),
            "max": s[-1], "min": s[0]}


def path_problems(obj: Any) -> list[str]:
    return ["a path" for s in asc.string_values(obj) if s.startswith("/") or any(m in s for m in PATH_MARKERS)]


def publish(obj: Mapping[str, Any], private: Path, public: Path | None, what: str, forbidden: Iterable[str],
            texts: Iterable[str] = ()) -> None:
    """The counts beside the private files (``write_summary``) and, when asked, the file to commit
    (``write_public``, refused also on any path string). Both checks run before either write."""
    forbidden, texts = set(forbidden), list(texts)
    bad = asc.public_problems(obj, forbidden, texts) + (path_problems(obj) if public is not None else [])
    if bad:
        raise SystemExit(f"REFUSED: {what} would carry {len(bad)} private string(s) ({sorted(set(bad))}); "
                         "nothing written")
    acc.write_summary(private, obj, what, forbidden, texts)
    if public is not None:
        acc.write_public(public, obj, forbidden, texts)


def check_pinned(name: str, path: Path) -> str:
    got = sha(path)
    if got != PINNED_INPUTS[name]:
        raise SystemExit(f"REFUSED: {name} ({Path(path).name}) is not the file the plan's counts were made on "
                         "(sha256 differs)")
    return got


def raw_config(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def token_counter() -> Any:
    """The SFR tokenizer (``alias_control_chunks.TOKENIZER``), as the chunker loads it."""
    from ragstack.ingestion.chunker_config import cached_token_counter

    return cached_token_counter("hf", model=acc.TOKENIZER, base_url=None, api_key=None)


def max_context_chars() -> int:
    """Production's default ``llm_max_context_chars`` (``config.py``), from the field default: the
    settings object is never built, so no environment or ``.env`` file is read."""
    from ragstack.config import Settings

    return int(Settings.model_fields["llm_max_context_chars"].default)


# ------------------------------------------------------------------ item identity (§3.2 (a)1)

def nbr_id(m_chunk_id: str, w: int) -> str:
    if w not in WINDOWS:
        raise ValueError(f"context window {w!r} is not one of {WINDOWS}")
    if not m_chunk_id or str(m_chunk_id).startswith(NBR_PREFIX):
        raise ValueError("M must be a chunk id, not a neighbour item id")
    return f"{NBR_PREFIX}w{w}:{m_chunk_id}"


def parse_nbr_id(item_id: str) -> tuple[int, str] | None:
    """``(w, M)`` of a neighbour item id, ``None`` for anything else."""
    m = re.fullmatch(rf"{re.escape(NBR_PREFIX)}w([0-9]+):(.+)", str(item_id))
    if not m or int(m.group(1)) not in WINDOWS or m.group(2).startswith(NBR_PREFIX):
        return None
    return int(m.group(1)), m.group(2)


def refuse_nbr_ids(ids: Iterable[str], what: str) -> int:
    """Review nit e: no chunk of any export the judge reads may already look like a neighbour item."""
    n = 0
    for cid in ids:
        n += 1
        if str(cid).startswith(NBR_PREFIX):
            raise SystemExit(f"REFUSED: {what} holds a chunk id starting {NBR_PREFIX!r}; neighbour item ids would "
                             "collide with it")
    return n


# ------------------------------------------------------------------ the passage (§3.1)

class _RowsStore:
    """A read-only store over one document's chunks: what ``expand_context`` calls
    (``get_chunks``), nothing else. A missing id is simply not returned, as a store does."""

    def __init__(self, chunks: Sequence[Any]) -> None:
        self._by_id = {c.id: c for c in chunks}
        self.calls = 0

    async def get_chunks(self, ids: Sequence[str], filters: Mapping[str, Any] | None = None) -> list[Any]:
        self.calls += 1
        return [self._by_id[i] for i in ids if i in self._by_id]


def linked_chunks(rows: Sequence[Mapping[str, Any]]) -> list[Any]:
    """One document's adopted chunks in document order as ``Chunk``s, linked by the production
    ``link_neighbors`` (the control export keeps no link keys, §3.2 (a)3)."""
    from ragstack.ingestion.chunkers import link_neighbors
    from ragstack.models import Chunk

    rows = sorted(rows, key=lambda r: (r["start_char"], r["end_char"]))
    docs = {r["doc_id"] for r in rows}
    if len(docs) != 1:
        raise ValueError("linked_chunks takes the chunks of one document")
    ids = [r["chunk_id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise SystemExit("REFUSED: a document holds one chunk id twice")
    chunks = [Chunk(id=r["chunk_id"], doc_id=r["doc_id"], content=r["content"], start_char=r["start_char"],
                    end_char=r["end_char"], metadata={}) for r in rows]
    link_neighbors(chunks)
    return chunks


def production_markers_problems() -> list[str]:
    """§3.1 quotes the markers; a production change to them is a protocol change, refused by name
    (not as a trim)."""
    from ragstack.llm import RagGenerator as G

    want = {"_BEFORE": SPEC_BEFORE, "_PASSAGE": SPEC_PASSAGE, "_AFTER": SPEC_AFTER, "_ELLIPSIS": SPEC_ELLIPSIS}
    return [k for k, v in want.items() if getattr(G, k, None) != v]


def literal_passage(m: str, before: Sequence[str], after: Sequence[str]) -> str:
    """The untrimmed §3.1 concatenation, literally: the rule-3 reference only."""
    parts = []
    if before:
        parts.append(SPEC_BEFORE + "\n".join(before))
    parts.append(SPEC_PASSAGE + m if (before or after) else m)
    if after:
        parts.append(SPEC_AFTER + "\n".join(after))
    return "\n".join(parts)


@dataclass
class Passage:
    text: str
    w: int
    m_chunk_id: str
    neighbour_chunk_ids: list[str]
    edge: str
    start_char: int
    end_char: int
    n_before: int
    n_after: int
    trimmed: bool
    store_calls: int = 0
    chunk_ids: list[str] = field(default_factory=list)


def neighbour_passage(rows: Sequence[Mapping[str, Any]], m_chunk_id: str, w: int,
                      budget_chars: int | None = None) -> Passage | None:
    """M ± ``w`` as production delivers it for one source (§3.1 rules 1-4), or ``None`` for a
    one-chunk document (no context: the passage is M). ``trimmed`` is the rule-3 check: the text
    must equal the untrimmed concatenation and hold no ``…`` absent from the chunk texts."""
    from ragstack.llm import RagGenerator
    from ragstack.models import ScoredChunk, Source
    from ragstack.retrieval.retriever import expand_context

    if w not in WINDOWS:
        raise ValueError(f"context window {w!r} is not one of {WINDOWS}")
    bad = production_markers_problems()
    if bad:
        raise SystemExit(f"REFUSED: production's context markers {bad} are not the ones PLAN §3.1 quotes; the "
                         "neighbour format changed (a protocol change, not a trim)")
    chunks = linked_chunks(rows)
    by_id = {c.id: c for c in chunks}
    m = by_id.get(m_chunk_id)
    if m is None:
        raise SystemExit("REFUSED: M is not a chunk of its document's adopted chunks")
    if len(chunks) == 1:
        return None
    store = _RowsStore(chunks)
    found = asyncio.run(expand_context(store, [ScoredChunk(chunk=m, score=1.0)], w))  # type: ignore[arg-type]
    ctx = found.get(m.id)
    source = Source(doc_id=m.doc_id, chunk_id=m.id, content=m.content, score=1.0, metadata={}, context=ctx)
    gen = RagGenerator(None, max_context_chars=max_context_chars() if budget_chars is None else budget_chars)  # type: ignore[arg-type]
    block = gen.format_context([source])
    if not block.startswith(CITATION_PREFIX):
        raise SystemExit("REFUSED: the production formatter's block does not start with the citation prefix '[1] '")
    text = block[len(CITATION_PREFIX):]
    ctx = list(ctx or [])
    before = [c for c in ctx if c.position < 0]
    after = [c for c in ctx if c.position > 0]
    parts = [*(c.content for c in ctx), m.content]
    trimmed = (text != literal_passage(m.content, [c.content for c in before], [c.content for c in after])
               or text.count(SPEC_ELLIPSIS) != sum(p.count(SPEC_ELLIPSIS) for p in parts))
    ids_in_passage = [c.chunk_id for c in before] + [m.id] + [c.chunk_id for c in after]
    spans = [(by_id[i].start_char, by_id[i].end_char) for i in ids_in_passage]
    edge = "before" if not before else "after" if not after else "none"
    return Passage(text=text, w=w, m_chunk_id=m.id, neighbour_chunk_ids=[c.chunk_id for c in ctx], edge=edge,
                   start_char=min(s for s, _ in spans), end_char=max(e for _, e in spans), n_before=len(before),
                   n_after=len(after), trimmed=trimmed, store_calls=store.calls, chunk_ids=ids_in_passage)


def export_row(p: Passage, m_row: Mapping[str, Any], budget_chars: int) -> dict[str, Any]:
    """§3.2 (a)2: the synthetic export row (``_glossbench_r2.parse_chunk`` shape)."""
    md = m_row.get("metadata") or {}
    return {"chunk_id": nbr_id(p.m_chunk_id, p.w), "doc_id": m_row["doc_id"], "content": p.text,
            "start_char": p.start_char, "end_char": p.end_char,
            "metadata": {"source_path": md.get("source_path"), "title": md.get("title"), "item_type": "neighbour",
                         "context_window": p.w, "max_overlap_chunk_id": p.m_chunk_id,
                         "neighbour_chunk_ids": list(p.neighbour_chunk_ids), "edge": p.edge,
                         "neighbours_before": p.n_before, "neighbours_after": p.n_after,
                         "content_sha256": asc.sha_text(p.text), "formatter": FORMATTER,
                         "max_context_chars": budget_chars,
                         "span_note": "start_char/end_char: the span from the first to the last chunk in the "
                                      "passage, for reference only; content is not that span's text"}}


def doc_texts(cdocs: Mapping[str, Sequence[Mapping[str, Any]]], what: str) -> dict[str, str]:
    out = {}
    for d, rows in cdocs.items():
        t = acc.doc_text(rows)
        if t is None:
            raise SystemExit(f"REFUSED: a document's chunks in {what} do not tile its text")
        out[d] = t
    return out


def group_of(p: Mapping[str, Any], identical: bool) -> str:
    """``split_feasibility.group_of``."""
    if p["lost"]:
        return {"none": "split", "other_only": "recovered", "primary_only": "primary_only"}[p["cover_class"]]
    return "kept_identical" if identical else "kept_changed"


def contains(span: tuple[int, int], ws: int, we: int) -> bool:
    return span[0] <= ws and span[1] >= we


# ------------------------------------------------------------------ the confirmatory pairs

@dataclass
class ConfirmPair:
    control_of: str
    record_id: str
    window_id: str
    doc_id: str
    ws: int
    we: int
    m_chunk_id: str
    group: str


def read_label_table_pairs(path: Path) -> dict[str, dict[str, Any]]:
    rows = read(path)
    meta = [r for r in rows if r.get("kind") == "meta"]
    if len(meta) != 1 or meta[0].get("schema") != rp.SCHEMA["table"]:
        raise SystemExit(f"REFUSED: the label table has no {rp.SCHEMA['table']} meta row")
    return {r["pair_id"]: r for r in rows if r.get("kind") == "pair"}


def confirm_pairs(confirm_dir: Path, label_table: Path, *, pinned: bool = True
                  ) -> tuple[list[ConfirmPair], dict[str, list[dict[str, Any]]], dict[str, str], dict[str, Any]]:
    """The 351 confirmatory pairs with their window, M and group, the adopted chunks by document,
    the document texts and the input sha256s. Every cross-check of ``split_feasibility.py`` that
    bears on an item is repeated (window reproduction, M = the label table's max-overlap chunk)."""
    paths = {"pairs_seed": confirm_dir / "run" / "pairs-seed.jsonl",
             "chunks_control": confirm_dir / "run" / "chunks-control.jsonl",
             "chunks_retest": confirm_dir / "retest" / "chunks-retest.jsonl", "label_table": Path(label_table)}
    shas = {k: (check_pinned(k, p) if pinned else sha(p)) for k, p in paths.items()}
    seeds = read(paths["pairs_seed"])
    cdocs = acc.control_docs(paths["chunks_control"])
    windows = {r["chunk_id"]: r for r in read(paths["chunks_retest"])}
    tpairs = read_label_table_pairs(paths["label_table"])
    if set(tpairs) != {s["control_of"] for s in seeds} or len(seeds) != len(tpairs):
        raise SystemExit("REFUSED: pairs-seed and the label table do not hold the same pairs")
    texts = doc_texts(cdocs, "the confirmatory chunks-control.jsonl")
    ctl_text = {r["chunk_id"]: r["content"] for rows in cdocs.values() for r in rows}
    out = []
    for s in seeds:
        w = windows.get(s["stage_a_chunk_id"])
        if w is None or w["doc_id"] != s["doc_id"] or s["doc_id"] not in texts:
            raise SystemExit("REFUSED: a pair's stage-A window or document is missing")
        ws, we = w["start_char"], w["end_char"]
        if texts[s["doc_id"]][ws:we] != w["content"]:
            raise SystemExit("REFUSED: a window does not reproduce from the document text")
        tp = tpairs[s["control_of"]]
        if tp["max_overlap_chunk_id"] != s["chunk_id"] or tp["record_id"] != s["record_id"]:
            raise SystemExit("REFUSED: the label table's max-overlap chunk is not pairs-seed's")
        out.append(ConfirmPair(s["control_of"], s["record_id"], s["stage_a_chunk_id"], s["doc_id"], ws, we,
                               s["chunk_id"], group_of(tp, ctl_text[s["chunk_id"]] == w["content"])))
    return out, cdocs, texts, shas


# ------------------------------------------------------------------ items (§3.2 (a))

@dataclass
class Items:
    export: dict[str, dict[str, Any]] = field(default_factory=dict)
    seeds: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    tokens: dict[str, dict[str, list[int]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(list)))
    trims: int = 0

    def add(self, p: Passage, m_row: Mapping[str, Any], *, control_of: str, record_id: str, group: str,
            item_set: str, budget: int, counter: Any) -> None:
        row = export_row(p, m_row, budget)
        cid = row["chunk_id"]
        old = self.export.get(cid)
        if old is not None and old != row:
            raise SystemExit("REFUSED: one neighbour item id would carry two different export rows")
        if old is None:
            self.export[cid] = row
        new_item = all((s["record_id"], s["chunk_id"], s["group"]) != (record_id, cid, group) for s in self.seeds)
        self.seeds.append({"control_of": control_of, "record_id": record_id, "chunk_id": cid, "group": group,
                           "context_window": p.w, "item_set": item_set})
        if new_item:
            self.tokens[group][f"context_window_{p.w}"].append(counter.count(p.text))
        self.trims += p.trimmed


def build_items(pairs: Sequence[ConfirmPair], cdocs: Mapping[str, Sequence[Mapping[str, Any]]],
                negatives: Sequence[Mapping[str, Any]], ndocs: Mapping[str, Sequence[Mapping[str, Any]]], *,
                budget: int, counter: Any) -> Items:
    """§5 (b)'s item sets: M ± 1 for every lost and kept_changed pair, M ± 2 for the split windows
    M ± 1 does not contain, M ± 1 for every drawn negative. kept_identical pairs get no item (their
    passage is M; the plan's premise is that they are one-chunk documents, refused otherwise)."""
    it = Items()
    c = it.counts
    for p in pairs:
        rows = cdocs[p.doc_id]
        m_row = next((r for r in rows if r["chunk_id"] == p.m_chunk_id), None)
        if m_row is None:
            raise SystemExit("REFUSED: a pair's M is not among its document's adopted chunks")
        c["pairs"][p.group] += 1
        if p.group == "kept_identical":
            if len(rows) != 1:
                raise SystemExit("REFUSED: a kept_identical pair sits in a document of several chunks; the plan's "
                                 "premise (its passage is M, no item) does not hold")
            c["one_chunk_documents_skipped"][p.group] += 1
            continue
        p1 = neighbour_passage(rows, p.m_chunk_id, 1, budget)
        if p1 is None:
            c["one_chunk_documents_skipped"][p.group] += 1
            continue
        todo = [p1]
        if p.group == "split":
            c["split_windows_contained"]["context_window_1"] += contains((p1.start_char, p1.end_char), p.ws, p.we)
            if not contains((p1.start_char, p1.end_char), p.ws, p.we):
                p2 = neighbour_passage(rows, p.m_chunk_id, 2, budget)
                assert p2 is not None
                c["split_windows_contained"]["context_window_2"] += contains((p2.start_char, p2.end_char), p.ws, p.we)
                todo.append(p2)
        for q in todo:
            it.add(q, m_row, control_of=p.control_of, record_id=p.record_id, group=p.group, item_set="positive",
                   budget=budget, counter=counter)
    positive_rm = {(p.record_id, p.m_chunk_id) for p in pairs}
    for n in negatives:
        if (n["record_id"], n["max_overlap_chunk_id"]) in positive_rm:
            raise SystemExit("REFUSED: a negative item's (record, M) is a positive item's; the sets must be disjoint")
        rows = ndocs.get(n["doc_id"])
        m_row = next((r for r in rows or () if r["chunk_id"] == n["max_overlap_chunk_id"]), None)
        if m_row is None:
            raise SystemExit("REFUSED: a negative's M is not in the draw's chunk run")
        q = neighbour_passage(rows or [], n["max_overlap_chunk_id"], 1, budget)
        if q is None or q.edge != "none":
            raise SystemExit("REFUSED: a drawn negative's M has no neighbour on both sides (the draw's rule)")
        c["pairs"][NEGATIVE_GROUP] += 1
        it.add(q, m_row, control_of=n["pair_id"], record_id=n["record_id"], group=NEGATIVE_GROUP,
               item_set="negative", budget=budget, counter=counter)
    return it


def item_counts(it: Items) -> dict[str, Any]:
    """Counts only: items = distinct (record, item id) per group and window, as the registry keys
    them; seed rows that repeat a (record, item id) merge into one pair in ``build-control``."""
    units: dict[str, dict[str, set[tuple[str, str]]]] = defaultdict(lambda: defaultdict(set))
    edges: dict[str, Counter[str]] = defaultdict(Counter)
    for s in it.seeds:
        key = (s["record_id"], s["chunk_id"])
        w = f"context_window_{s['context_window']}"
        if key not in units[s["group"]][w]:
            units[s["group"]][w].add(key)
            e = it.export[s["chunk_id"]]["metadata"]["edge"]
            edges[s["group"]][f"{w}:{e}"] += 1
    items = {g: {w: len(v) for w, v in sorted(units[g].items())} for g in (*POSITIVE_GROUPS, NEGATIVE_GROUP)}
    all_keys = {(s["record_id"], s["chunk_id"]) for s in it.seeds}
    groups_of: dict[tuple[str, str], set[str]] = defaultdict(set)
    sets_of: dict[tuple[str, str], set[str]] = defaultdict(set)
    for s in it.seeds:
        groups_of[(s["record_id"], s["chunk_id"])].add(s["group"])
        sets_of[(s["record_id"], s["chunk_id"])].add(s["item_set"])
    if any(len(v) > 1 for v in sets_of.values()):
        raise SystemExit("REFUSED: one item is both a positive and a negative item")
    rec_per_row = Counter(r for r, _ in all_keys)
    rows_per_export = Counter(cid for _r, cid in all_keys)
    plan_diff = []
    for g, ws in PLAN_ITEMS.items():
        for w, n in ws.items():
            if items.get(g, {}).get(w, 0) != n:
                plan_diff.append(f"{g}.{w}: {items.get(g, {}).get(w, 0)} != {n}")
    if len(all_keys) != PLAN_TOTAL:
        plan_diff.append(f"total: {len(all_keys)} != {PLAN_TOTAL}")
    return {
        "items": items, "items_total": len(all_keys), "seed_rows": len(it.seeds), "export_rows": len(it.export),
        "items_per_set": dict(sorted(Counter(next(iter(v)) for v in sets_of.values()).items())),
        "seed_rows_merged_on_a_shared_item": len(it.seeds) - len(all_keys),
        "items_in_several_groups": sum(1 for v in groups_of.values() if len(v) > 1),
        "export_rows_shared_by_several_records": sum(1 for v in rows_per_export.values() if v > 1),
        "records_with_several_items": sum(1 for v in rec_per_row.values() if v > 1),
        "edge_items": {g: dict(sorted(edges[g].items())) for g in (*POSITIVE_GROUPS, NEGATIVE_GROUP)},
        "pairs": {g: it.counts["pairs"][g] for g in (*GROUPS, NEGATIVE_GROUP)},
        "one_chunk_documents_skipped": {g: it.counts["one_chunk_documents_skipped"][g] for g in GROUPS},
        "split_windows_contained": dict(it.counts["split_windows_contained"]),
        "passage_tokens": {g: {w: stats(v) for w, v in sorted(it.tokens[g].items())}
                           for g in (*POSITIVE_GROUPS, NEGATIVE_GROUP)},
        "trims_found": it.trims,
        "plan_counts": {"expected": PLAN_ITEMS, "expected_total": PLAN_TOTAL, "matches": not plan_diff,
                        "differences": plan_diff},
    }


def read_negative_dir(negative_dir: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, str]]:
    """The draw's ``sample-negative.jsonl`` and its own chunk run, checked against the draw's summary."""
    summ_p = negative_dir / "negative-draw.json"
    samp_p = negative_dir / "sample-negative.jsonl"
    chunks_p = negative_dir / "chunks" / "chunks-control.jsonl"
    summ = json.loads(summ_p.read_text(encoding="utf-8"))
    if summ.get("schema") != SCHEMA["negative"]:
        raise SystemExit(f"REFUSED: {summ_p.name} is not a {SCHEMA['negative']} summary")
    shas = {"negative_sample": sha(samp_p), "negative_chunks": sha(chunks_p), "negative_draw": sha(summ_p)}
    outs = summ.get("outputs_sha256") or {}
    if outs.get("sample_negative") != shas["negative_sample"] or outs.get("chunks_control") != shas["negative_chunks"]:
        raise SystemExit("REFUSED: the negative directory's files are not the ones its negative-draw.json names")
    return read(samp_p), acc.control_docs(chunks_p), shas


def run_items(confirm_dir: Path, label_table: Path, main_config: Path, negative_dir: Path, out_dir: Path,
              public_out: Path | None = None) -> dict[str, Any]:
    env = acc.require_env()
    rp.refuse_in_repo(out_dir, "--out-dir")
    pairs, cdocs, texts, shas = confirm_pairs(confirm_dir, label_table)
    negatives, ndocs, nshas = read_negative_dir(negative_dir)
    ntexts = doc_texts(ndocs, "the negative draw's chunks-control.jsonl")
    # review nit e: no chunk the judge can look up may already carry a neighbour id
    main_raw = raw_config(main_config)
    exports = asc.read_chunk_exports([str(p) for p in (main_raw.get("chunk_exports") or {}).values()])
    nbr_checked = {"main_config_exports": refuse_nbr_ids(exports, "the main config's chunk exports"),
                   "confirm_chunks_control": refuse_nbr_ids((r["chunk_id"] for rs in cdocs.values() for r in rs),
                                                            "the confirmatory chunks-control.jsonl"),
                   "negative_chunks_control": refuse_nbr_ids((r["chunk_id"] for rs in ndocs.values() for r in rs),
                                                             "the negative draw's chunks-control.jsonl")}
    budget = max_context_chars()
    it = build_items(pairs, cdocs, negatives, ndocs, budget=budget, counter=token_counter())
    if it.trims:
        raise SystemExit(f"REFUSED: {it.trims} passage(s) were trimmed by the production budget (§3.1 rule 3); "
                         "nothing written")
    export_rows = sorted(it.export.values(), key=lambda r: r["chunk_id"])
    if any(not parse_nbr_id(r["chunk_id"]) for r in export_rows):
        raise SystemExit("REFUSED: an export row's id is not a neighbour item id")
    esha = acc.write_private(out_dir / "neighbour-export.jsonl", acc.jsonl_bytes(export_rows), "the neighbour export")
    ssha = acc.write_private(out_dir / "neighbour-seed.jsonl", acc.jsonl_bytes(it.seeds), "the neighbour seed")
    counts = item_counts(it)
    all_texts = {**texts, **ntexts}
    docs = sorted(all_texts)
    rec: dict[str, Any] = {
        "schema": SCHEMA["items"], "exploratory": True, "offline": True, "calls": CALLS, "plan": PREREG,
        "item_type": "neighbour", "formatter": FORMATTER, "max_context_chars": budget,
        "budget_chars": {f"context_window_{w}": budget * (2 * w + 1) for w in WINDOWS},
        "id_scheme": f"{NBR_PREFIX}w<w>:<max-overlap chunk id>", "tokenizer": acc.TOKENIZER,
        "tokens_counted_on": "the passage as judged (markers included)",
        "edge_rule": "before: M is its document's first chunk; after: the last; none: neighbours on both sides",
        **counts,
        "nbr_prefix_checked": nbr_checked,
        "registry": {"drift_size": _drift_size(counts["items_total"]),
                     "opus_cap": math.floor(0.07 * counts["items_total"])},
        "inputs_sha256": {**shas, **nshas, "main_config": sha(main_config)},
        "outputs_sha256": {"neighbour_export": esha, "neighbour_seed": ssha},
        "environment": env,
        "provenance": acc.public_provenance(acc.provenance(
            [all_texts[d] for d in docs], None,
            [[(r["start_char"], r["end_char"]) for r in (cdocs.get(d) or ndocs[d])] for d in docs])),
    }
    forbidden = (set(docs) | {p.control_of for p in pairs} | {p.record_id for p in pairs} | {p.window_id for p in pairs}
                 | {r["chunk_id"] for rs in (*cdocs.values(), *ndocs.values()) for r in rs} | set(it.export)
                 | {n["pair_id"] for n in negatives} | {n["record_id"] for n in negatives}
                 | {n.get("stage_a_chunk_id", "") for n in negatives}
                 | {str((r.get("metadata") or {}).get("source_path") or "") for r in export_rows})
    publish(rec, out_dir / "neighbour-items.json", public_out, "neighbour-items.json", forbidden,
            [*all_texts.values()])
    return rec


def _drift_size(n: int) -> int:
    import alias_study_judge as aj

    return aj.drift_size(n, 0.05)


# ------------------------------------------------------------------ draw-negative (§3.2 (b))

def seed_file_problems(seed_file: Path, repo: Path | None = None) -> list[str]:
    """§3.2 (b)6: the tree is clean (untracked files included) and a file tracked at HEAD has
    exactly the seed file's bytes. Checked before anything else is read."""
    import alias_study_judge as aj

    repo = Path(repo or aj.REPO)
    bad = []
    if not Path(seed_file).is_file():
        return ["the seed file does not exist"]
    try:
        st = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True,
                            check=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return ["git status failed"]
    if st.strip():
        bad.append("the working tree is not clean")
    if not aj.public_tracked_at_head(Path(seed_file), repo):
        bad.append("no file tracked at HEAD has the seed file's bytes (commit it first)")
    return bad


def read_seed(seed_file: Path, repo: Path | None = None) -> str:
    bad = seed_file_problems(seed_file, repo)
    if bad:
        raise SystemExit(f"REFUSED: the negative draw's seed is not recorded before the draw ({'; '.join(bad)}); "
                         "nothing read, nothing written")
    try:
        seed = json.loads(Path(seed_file).read_text(encoding="utf-8")).get("seed")
    except (ValueError, AttributeError) as e:
        raise SystemExit("REFUSED: the seed file is not a JSON object with a 'seed' string") from e
    if not isinstance(seed, str) or not seed.strip():
        raise SystemExit("REFUSED: the seed file holds no non-empty 'seed' string")
    return seed


def cells() -> list[tuple[str, str]]:
    return [(p, a) for p in PAPERS for a in asc.ARM_GROUPS]


def cell_key(paper: str, arm: str) -> str:
    return f"{paper}:{arm}"


def negative_walk(final: Sequence[Mapping[str, Any]], registry: Mapping[str, Mapping[str, Any]], *,
                  exclude_pairs: Iterable[str], exclude_windows: Iterable[str], seed: str,
                  quotas_by_paper: Mapping[str, int] | None = None, factor: int = PREFIX_FACTOR
                  ) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any]]:
    """§3.2 (b)2-5 and the prefix of (b)8 (module docstring, review nit c). Returns the candidates
    (final row fields + ``paper``, in cell order then walk order), the quotas per cell and the
    counts (no id).

    Pool: stage-A final rows, ``source == "agree"``, label *not* (``label_of == 0``, not unjudged),
    minus ``exclude_pairs`` (by pair id) and minus the pairs whose window is in ``exclude_windows``.
    Quotas: per paper ``drift_allocation(quota, distinct records per arm cell)``. Walk: the cells
    in the order paper x arm; per cell the pool in the order ``sha256(<seed>|<paper>|<arm>|<pair
    id>)``, ties by pair id; a pair becomes a candidate when its (record, arm) and its window are
    held by no earlier candidate (of any cell), until ``factor x quota`` candidates."""
    import alias_study_judge as aj

    quotas_by_paper = dict(NEGATIVE_QUOTAS if quotas_by_paper is None else quotas_by_paper)
    exclude_pairs, exclude_windows = set(exclude_pairs), set(exclude_windows)
    stage_a = [f for f in final if f.get("stage") == "A"]
    for f in stage_a:
        if f.get("arm") not in asc.ARM_GROUPS:
            raise SystemExit(f"a stage-A final row carries arm {f.get('arm')!r}")
    agreed = [f for f in stage_a if f.get("source") == "agree"]
    nots = [f for f in agreed if acc.label_of(f) == 0 and not acc.is_unjudged(f)]
    ids = [f["pair_id"] for f in nots]
    if len(set(ids)) != len(ids):
        raise SystemExit("REFUSED: a pair id appears twice among stage A's final rows")
    papered = []
    for f in nots:
        r = registry.get(f["pair_id"])
        if r is None or r.get("record_id") != f["record_id"] or r.get("chunk_id") != f["chunk_id"]:
            raise SystemExit("REFUSED: a stage-A final row has no matching stage-A registry row")
        if r.get("arm") != f["arm"]:
            raise SystemExit("REFUSED: a stage-A final row's arm is not its registry row's")
        papered.append({**f, "paper": "same" if r["c_paper"] == r["q_paper"] else "other"})
    in_sample = [f for f in papered if f["pair_id"] in exclude_pairs]
    eligible = [f for f in papered if f["pair_id"] not in exclude_pairs]
    shared = [f for f in eligible if f["chunk_id"] in exclude_windows]
    pool = [f for f in eligible if f["chunk_id"] not in exclude_windows]

    def by_cell(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
        c = Counter(cell_key(f["paper"], f["arm"]) for f in rows)
        return {cell_key(p, a): c[cell_key(p, a)] for p, a in cells()}

    order = {(p, a): sorted((f for f in pool if (f["paper"], f["arm"]) == (p, a)),
                            key=lambda f, p=p, a=a: (asc.seeded_key(seed, p, a, f["pair_id"]), f["pair_id"]))
             for p, a in cells()}
    records = {(p, a): len({f["record_id"] for f in order[(p, a)]}) for p, a in cells()}
    quotas: dict[str, int] = {}
    for p in PAPERS:
        alloc = aj.drift_allocation(int(quotas_by_paper.get(p, 0)), {a: records[(p, a)] for a in asc.ARM_GROUPS})
        quotas.update({cell_key(p, a): alloc[a] for a in asc.ARM_GROUPS})
    rec_arm: set[tuple[str, str]] = set()
    wins: set[str] = set()
    cands: list[dict[str, Any]] = []
    skips: dict[str, Counter[str]] = {cell_key(p, a): Counter() for p, a in cells()}
    walked: dict[str, int] = {}
    n_cand: dict[str, int] = {}
    for p, a in cells():
        k = cell_key(p, a)
        target, got, pos = factor * quotas[k], 0, 0
        lst = order[(p, a)]
        while got < target and pos < len(lst):
            f = lst[pos]
            pos += 1
            if (f["record_id"], f["arm"]) in rec_arm:
                skips[k]["record_arm_taken"] += 1
                continue
            if f["chunk_id"] in wins:
                skips[k]["window_taken"] += 1
                continue
            rec_arm.add((f["record_id"], f["arm"]))
            wins.add(f["chunk_id"])
            cands.append({**f, "cell": k})
            got += 1
        walked[k], n_cand[k] = pos, got
    counts = {"stage_a_pairs": len(stage_a), "agreed": len(agreed), "agreed_not": by_cell(papered),
              "in_sample_excluded": by_cell(in_sample), "shared_window_excluded": by_cell(shared),
              "pool": by_cell(pool), "pool_records": {cell_key(p, a): records[(p, a)] for p, a in cells()},
              "quotas": quotas, "prefix_factor": factor,
              "prefix_targets": {k: factor * v for k, v in quotas.items()}, "candidates": n_cand,
              "walked": walked, "walk_skips": {k: dict(v) for k, v in skips.items()},
              "prefix_reached": {k: n_cand[k] == factor * quotas[k] for k in quotas}}
    return cands, quotas, counts


def negative_eligibility(cands: Sequence[Mapping[str, Any]], seeds: Mapping[str, Mapping[str, Any]],
                         ndocs: Mapping[str, Sequence[Mapping[str, Any]]], positive_rm: set[tuple[str, str]], *,
                         budget: int) -> list[tuple[str | None, Passage | None]]:
    """§3.2 (b)8, per candidate in walk order: the first reason of :data:`NEGATIVE_EXCLUSIONS` that
    applies, or ``None`` with its M ± 1 passage. Geometric only: no label is read."""
    out: list[tuple[str | None, Passage | None]] = []
    for f in cands:
        s = seeds.get(f["pair_id"])
        rows = ndocs.get(s["doc_id"]) if s else None
        if s is None or not rows or all(r["chunk_id"] != s["chunk_id"] for r in rows):
            out.append(("unresolved", None))
            continue
        q = neighbour_passage(rows, s["chunk_id"], 1, budget)
        if q is None:
            out.append(("one_chunk_document", None))
        elif q.edge != "none":
            out.append(("edge", None))
        elif q.trimmed:
            out.append(("trim", None))
        elif (f["record_id"], s["chunk_id"]) in positive_rm:
            out.append(("collision_positive", None))
        else:
            out.append((None, q))
    return out


def shared_doc_problems(ndocs: Mapping[str, Sequence[Mapping[str, Any]]],
                        cdocs: Mapping[str, Sequence[Mapping[str, Any]]]) -> int:
    """§3.2 (b)7's gate: the documents both chunk runs hold must have identical chunk ids and spans."""
    def geo(rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, int, int]]:
        return [(r["chunk_id"], r["start_char"], r["end_char"]) for r in rows]
    return sum(geo(ndocs[d]) != geo(cdocs[d]) for d in set(ndocs) & set(cdocs))


def load_judge_cfg(path: Path) -> dict[str, Any]:
    """The main run's judge config, validated by the judge (``alias_study_judge.load_config``)."""
    return acc.judge_cfg(path)


def run_draw_negative(main_config: Path, seed_file: Path, confirm_dir: Path, confirm_sample: Path,
                      control_sample: Path, texts: Sequence[Path], out_dir: Path, public_out: Path | None = None,
                      repo: Path | None = None) -> dict[str, Any]:
    import alias_study_spot as asp

    seed = read_seed(seed_file, repo)  # §3.2 (b)6: before anything else is read
    env = acc.require_env()
    rp.refuse_in_repo(out_dir, "--out-dir")
    paths = {"confirm_sample": Path(confirm_sample), "control_sample": Path(control_sample),
             "pairs_seed": confirm_dir / "run" / "pairs-seed.jsonl",
             "chunks_control": confirm_dir / "run" / "chunks-control.jsonl"}
    shas = {k: check_pinned(k, p) for k, p in paths.items()}
    cfg = load_judge_cfg(main_config)
    sa = asp.committed_stage_a(cfg)
    if sa["commit_sha256"] != STAGE_A_COMMIT_SHA256:
        raise SystemExit("REFUSED: the committed stage-A commit is not the one the confirmatory test pinned "
                         "(CONFIRM_STAGE_A_COMMIT_SHA256)")
    registry = {p["pair_id"]: p for p in sa["pairs"]}
    excl = [*read(paths["confirm_sample"]), *read(paths["control_sample"])]
    cands, quotas, counts = negative_walk(sa["final"], registry, exclude_pairs={r["pair_id"] for r in excl},
                                          exclude_windows={r["chunk_id"] for r in excl}, seed=seed)
    # §3.2 (b)7: the candidates through chunk and map, unchanged, into a private directory
    cdir = out_dir / "chunks"
    sample_rows = [{"pair_id": f["pair_id"], "record_id": f["record_id"], "chunk_id": f["chunk_id"],
                    "stratum": NEGATIVE_STRATUM, "arm": f["arm"], "in_spot_sheet": False} for f in cands]
    acc.write_private(cdir / "sample.jsonl", acc.jsonl_bytes(sample_rows), "the negative candidate sample")
    acc.run_chunk(cfg["chunk_exports"], texts, cdir / "sample.jsonl", cdir)
    acc.run_map(cfg["chunk_exports"], cdir / "sample.jsonl", cdir)
    ndocs = acc.control_docs(cdir / "chunks-control.jsonl")
    cdocs = acc.control_docs(paths["chunks_control"])
    if shared_doc_problems(ndocs, cdocs):
        raise SystemExit("REFUSED: a document the confirmatory run also chunked got other chunk ids or spans "
                         "(§3.2 (b)7 gate); sample-negative.jsonl not written")
    refuse_nbr_ids((r["chunk_id"] for rs in ndocs.values() for r in rs), "the negative draw's chunks-control.jsonl")
    seeds = {s["control_of"]: s for s in read(cdir / "pairs-seed.jsonl")}
    positive_rm = {(s["record_id"], s["chunk_id"]) for s in read(paths["pairs_seed"])}
    budget = max_context_chars()
    verdicts = negative_eligibility(cands, seeds, ndocs, positive_rm, budget=budget)
    reasons: dict[str, Counter[str]] = {cell_key(p, a): Counter() for p, a in cells()}
    drawn: list[tuple[Mapping[str, Any], Passage]] = []
    per_cell: Counter[str] = Counter()
    for f, (why, q) in zip(cands, verdicts, strict=True):
        if why is not None:
            reasons[f["cell"]][why] += 1
        elif per_cell[f["cell"]] < quotas[f["cell"]]:
            per_cell[f["cell"]] += 1
            assert q is not None
            drawn.append((f, q))
        else:
            reasons[f["cell"]]["eligible_beyond_quota"] += 1
    short = {k: (per_cell[k], q) for k, q in quotas.items() if per_cell[k] < q}
    if short:
        raise SystemExit(f"REFUSED: {len(short)} cell(s) ran short of eligible candidates within the 3 x quota "
                         f"prefix ({', '.join(f'{k} {g}/{q}' for k, (g, q) in sorted(short.items()))}); not extended "
                         "(a larger prefix is a recorded deviation); sample-negative.jsonl not written")
    counter = token_counter()
    out_rows, toks = [], []
    for f, q in drawn:
        s = seeds[f["pair_id"]]
        out_rows.append({"pair_id": f["pair_id"], "record_id": f["record_id"], "stage_a_chunk_id": f["chunk_id"],
                         "max_overlap_chunk_id": s["chunk_id"], "doc_id": s["doc_id"], "paper": f["paper"],
                         "arm": f["arm"]})
        toks.append(counter.count(q.text))
    if len({(r["record_id"], r["arm"]) for r in out_rows}) != len(out_rows) or \
            len({r["stage_a_chunk_id"] for r in out_rows}) != len(out_rows):
        raise SystemExit("REFUSED: the draw broke its own rule (two pairs of one (record, arm) or one window)")
    nsha = acc.write_private(out_dir / "sample-negative.jsonl", acc.jsonl_bytes(out_rows), "the negative sample")
    texts_by_doc = doc_texts({d: ndocs[d] for d in sorted({r["doc_id"] for r in out_rows})}, "the draw's chunks")
    docs = sorted(texts_by_doc)
    rec: dict[str, Any] = {
        "schema": SCHEMA["negative"], "exploratory": True, "offline": True, "calls": CALLS, "plan": PREREG,
        "stratum": NEGATIVE_STRATUM, "seed_file_sha256": sha(seed_file),
        "seed_is_the_proposed_one": seed == NEGATIVE_SEED_PROPOSED,
        "draw_rule": "§3.2 (b): stage-A agreed not pairs minus the confirmatory and control samples (pair id and "
                     "window); quotas same 100 / other 50, arms by drift_allocation over the cell records; cells "
                     "walked paper x arm in the seeded order, one pair per (record, arm) and per window across all "
                     "cells; the prefix counts 3 x quota candidates after that filter; the first quota eligible "
                     "candidates per cell are drawn; a short cell is refused",
        "eligibility": "M resolves; M has a neighbour on both sides; the M ± 1 passage is untrimmed; (record, M) is "
                       "no positive item's",
        **counts,
        "exclusions": {k: {r: v[r] for r in (*NEGATIVE_EXCLUSIONS, "eligible_beyond_quota")}
                       for k, v in reasons.items()},
        "drawn": {k: per_cell[k] for k in quotas}, "n_drawn": len(out_rows),
        "passage_tokens": stats(toks), "tokenizer": acc.TOKENIZER, "max_context_chars": budget,
        "shared_documents_checked": len(set(ndocs) & set(cdocs)),
        "stage_a_commit_sha256": sa["commit_sha256"],
        "inputs_sha256": {**shas, "main_config": sha(main_config)},
        "outputs_sha256": {"sample_negative": nsha, "chunks_control": sha(cdir / "chunks-control.jsonl"),
                           "candidate_sample": sha(cdir / "sample.jsonl"),
                           "pairs_seed": sha(cdir / "pairs-seed.jsonl")},
        "environment": env,
        "provenance": acc.public_provenance(acc.provenance(
            [texts_by_doc[d] for d in docs], None, [[(r["start_char"], r["end_char"]) for r in ndocs[d]]
                                                    for d in docs])),
    }
    forbidden = ({f["pair_id"] for f in sa["final"]} | {f["record_id"] for f in sa["final"]}
                 | {f["chunk_id"] for f in sa["final"]} | {r["pair_id"] for r in excl} | {r["chunk_id"] for r in excl}
                 | {str(p.get("c_paper")) for p in sa["pairs"]} | {str(p.get("q_paper")) for p in sa["pairs"]}
                 | set(ndocs) | {r["chunk_id"] for rs in ndocs.values() for r in rs})
    publish(rec, out_dir / "negative-draw.json", public_out, "negative-draw.json", forbidden, texts_by_doc.values())
    return rec


# ------------------------------------------------------------------ CLI

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("items", help="§3.2 (a): the neighbour export, the seed rows and neighbour-items.json")
    a.add_argument("--confirm-dir", type=Path, required=True,
                   help="the private confirmatory directory (run/pairs-seed.jsonl, run/chunks-control.jsonl, "
                        "retest/chunks-retest.jsonl)")
    a.add_argument("--label-table", type=Path, required=True, help="the rechunk label table (rechunk-label-table/1)")
    a.add_argument("--main-config", type=Path, required=True, help="the alias main run's judge config")
    a.add_argument("--negative-dir", type=Path, required=True, help="the draw-negative --out-dir")
    a.add_argument("--out-dir", type=Path, required=True, help="private, outside the repository, written once")
    a.add_argument("--public-out", type=Path, help="also write the counts here (artifacts/neighbour-items.json)")
    d = sub.add_parser("draw-negative", help="§3.2 (b): the 150 stage-A not pairs of the negative control")
    d.add_argument("--main-config", type=Path, required=True, help="the alias main run's judge config")
    d.add_argument("--seed-file", type=Path, required=True,
                   help="artifacts/negative-draw-seed.json, committed at HEAD on a clean tree before the draw")
    d.add_argument("--confirm-dir", type=Path, required=True, help="the private confirmatory directory")
    d.add_argument("--confirm-sample", type=Path, required=True, help="the confirmatory sample.jsonl (351 pairs)")
    d.add_argument("--control-sample", type=Path, required=True, help="the exploratory control sample.jsonl (150)")
    d.add_argument("--texts", type=Path, required=True, action="append", help="record texts, as for chunk")
    d.add_argument("--out-dir", type=Path, required=True, help="private, outside the repository, written once")
    d.add_argument("--public-out", type=Path, help="also write the counts here (artifacts/negative-draw.json)")
    args = ap.parse_args(argv)
    if args.cmd == "items":
        rec = run_items(args.confirm_dir, args.label_table, args.main_config, args.negative_dir, args.out_dir,
                        args.public_out)
        print(json.dumps({k: rec[k] for k in ("items", "items_total", "trims_found", "plan_counts")}, indent=1))
    elif args.cmd == "draw-negative":
        rec = run_draw_negative(args.main_config, args.seed_file, args.confirm_dir, args.confirm_sample,
                                args.control_sample, args.texts, args.out_dir, args.public_out)
        print(json.dumps({k: rec[k] for k in ("quotas", "drawn", "n_drawn", "exclusions")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
