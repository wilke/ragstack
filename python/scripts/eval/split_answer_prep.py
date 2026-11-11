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

**Edge** (§3.2 (a)2, "the side a document edge removed"): read as a whole side removed. ``before``
when M is its document's first chunk (index 0: no context before), ``after`` when it is the last,
else ``none``; derived from M's index and checked against what production's walk returned
(:func:`neighbour_passage` refuses a walk that does not reach every neighbour within w). At ``w = 2``
a side that holds one neighbour instead of two, because M is its document's second or second-to-last
chunk, is NOT an edge: such an item counts as ``none`` in ``edge_items`` and in the report's
``without_edge`` group although its passage is one chunk shorter (review A7 / Fable F6; at most the 2
w = 2 split items). Its real counts are in the export row's ``neighbours_before`` /
``neighbours_after``. A one-chunk document has no context: the passage is M itself, M's label is
reused and no item is built (counted as skipped).

**Budget**: production scales ``max_context_chars`` by ``2 x window + 1``, the window being the
farthest context position the source actually carries (``RagGenerator._format_context``). Each
export row records that budget (``budget_chars``); ``neighbour-items.json`` gives the nominal budget
per context window (``nominal_budget_chars``) and the per-item budgets counted.

**The negative draw's prefix** (§3.2 (b)8, review nit c): the prefix counts candidates AFTER the walk's
one-pair-per-(record, arm) and one-pair-per-window filter. The cells are walked in the fixed order
paper (``same``, ``other``) x arm (``base``, ``neighbour``, ``random``); in each, the pool pairs are
taken in seeded order and a pair becomes a candidate only when its (record, arm) and its window are
not already held by a candidate of any cell (a candidate holds them whether or not it is later
drawn), until the cell holds ``3 x quota`` candidates or its pool is exhausted. Only these
candidates are chunked; the first ``quota`` eligible ones in walk order are drawn; a cell with
fewer is refused, never extended.

**Neighbours of the negatives** (review nit a) come from the draw's own chunk run
(``<negative dir>/chunks/chunks-control.jsonl``); the draw's gate (checked again by ``items``) makes
every document it shares with the confirmatory run's ``chunks-control.jsonl`` identical there: the
same chunk ids, spans and texts.

**Clarifications of §3.2** (the #999 review; each tightens the plan's literal wording, none loosens it):

- (b)8 ``collision_negative``: a candidate whose (record, M) is an earlier eligible candidate's (walk
  order across all cells) is excluded, by the logic of the positive-collision rule: it would get the
  same neighbour item id and ``build-control`` would merge the two into one pair (149 judged, not
  150). ``items`` refuses two negatives on one (record, item id).
- (b)6 the seed file: the plan says "a file tracked at HEAD has exactly that file's bytes"; that
  would pass a copy anywhere whose bytes equal any committed file. ``draw-negative`` requires the
  ``--seed-file`` to be the repository's :data:`SEED_RELPATH` itself, tracked
  (``git ls-files --error-unmatch``) and identical to HEAD with staged changes included (``git diff
  --quiet HEAD``), on a clean tree; ``negative-draw.json`` records the HEAD commit (``seed_commit``).
- §5 (b) / (a)7 the plan's counts: the plan lists the item counts as the public output; ``items``
  also refuses when they differ from :data:`PLAN_ITEMS` / :data:`PLAN_TOTAL` (446), before anything is
  written. ``--allow-plan-mismatch`` proceeds and writes the differences into ``neighbour-items.json``
  as ``deviation`` (a recorded deviation, not a silent count).
- (b)6 / (a): ``items`` takes only a negative directory ``draw-negative`` wrote after the committed
  seed: its ``negative-draw.json`` must name the sha256 of the seed file committed at HEAD, the pinned
  stage-A commit and the pinned draw inputs, per-paper quotas of :data:`NEGATIVE_QUOTAS` and a draw that
  filled them (150 pairs). A directory that only names its own files' sha256s is refused.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
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
#: refused unless ``--allow-plan-mismatch`` records it as a deviation (module docstring, clarifications)
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
#: §3.2 (b)6: the seed file, relative to the repository root (module docstring, clarifications)
SEED_RELPATH = "docs/plans/results/split-answer-2026-11/artifacts/negative-draw-seed.json"
#: the draw's pinned inputs ``items`` checks again in ``negative-draw.json``
DRAW_PINNED = ("confirm_sample", "control_sample", "pairs_seed", "chunks_control")
PAPERS = ("same", "other")
NEGATIVE_QUOTAS = {"same": 100, "other": 50}
PREFIX_FACTOR = 3
NEGATIVE_STRATUM = "not"
NEGATIVE_EXCLUSIONS = ("unresolved", "one_chunk_document", "edge", "trim", "collision_positive", "collision_negative")

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
    #: production's budget for this source: max_context_chars x (2 x farthest position + 1)
    budget_chars: int = 0


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
    base = max_context_chars() if budget_chars is None else budget_chars
    gen = RagGenerator(None, max_context_chars=base)  # type: ignore[arg-type]
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
    # Fable F1: the walk must have reached every neighbour the document holds within w. A store that
    # misses a neighbour or a change to production's link keys shortens the context silently
    # (expand_context omits what it cannot fetch); that is refused, never read as a document edge.
    i = chunks.index(m)
    want_before = [c.id for c in chunks[max(0, i - w):i]]
    want_after = [c.id for c in chunks[i + 1:i + 1 + w]]
    if not ctx or len(before) != min(i, w) or len(after) != min(len(chunks) - 1 - i, w) \
            or [c.chunk_id for c in before] != want_before or [c.chunk_id for c in after] != want_after:
        raise SystemExit(f"REFUSED: production's context walk returned {len(before)} before / {len(after)} after for "
                         f"an M with {min(i, w)} / {min(len(chunks) - 1 - i, w)} neighbours within w={w}; the "
                         "neighbours are not the document's (a link key or store mismatch, not a document edge)")
    edge = "before" if i == 0 else "after" if i == len(chunks) - 1 else "none"
    walk_edge = "before" if not before else "after" if not after else "none"
    if edge != walk_edge:  # implied by the counts above; kept as the stated invariant
        raise SystemExit("REFUSED: the edge from M's position is not the edge the walk shows")
    ids_in_passage = [c.chunk_id for c in before] + [m.id] + [c.chunk_id for c in after]
    spans = [(by_id[i].start_char, by_id[i].end_char) for i in ids_in_passage]
    return Passage(text=text, w=w, m_chunk_id=m.id, neighbour_chunk_ids=[c.chunk_id for c in ctx], edge=edge,
                   start_char=min(s for s, _ in spans), end_char=max(e for _, e in spans), n_before=len(before),
                   n_after=len(after), trimmed=trimmed, store_calls=store.calls, chunk_ids=ids_in_passage,
                   budget_chars=base * (2 * max(abs(c.position) for c in ctx) + 1))


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
                         "max_context_chars": budget_chars, "budget_chars": p.budget_chars,
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
    # review A1: two negatives on one (record, M) would be one neighbour item, one registry pair
    if len({(n["record_id"], nbr_id(n["max_overlap_chunk_id"], 1)) for n in negatives}) != len(negatives):
        raise SystemExit("REFUSED: two drawn negatives share one (record, neighbour item id); build-control would merge "
                         "them into one pair (the draw's collision_negative rule)")
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


def negative_summary_problems(summ: Mapping[str, Any], n_sample: int, repo: Path | None = None) -> list[str]:
    """Review A2: ``items`` takes only a directory ``draw-negative`` wrote after the committed seed:
    the summary names the seed file committed at HEAD (by sha256), the pinned stage-A commit, the
    pinned inputs, quotas that are :data:`NEGATIVE_QUOTAS`' and a draw that filled them."""
    bad = []
    head = head_seed_sha256(repo)
    if head is None or summ.get("seed_file_sha256") != head:
        bad.append(f"its seed_file_sha256 is not the sha256 of {SEED_RELPATH} at HEAD")
    if summ.get("stage_a_commit_sha256") != STAGE_A_COMMIT_SHA256:
        bad.append("its stage_a_commit_sha256 is not CONFIRM_STAGE_A_COMMIT_SHA256")
    ins = summ.get("inputs_sha256") or {}
    bad += [f"its inputs_sha256.{k} is not the pinned file's" for k in DRAW_PINNED if ins.get(k) != PINNED_INPUTS[k]]
    quotas, drawn = summ.get("quotas"), summ.get("drawn")
    keys = {cell_key(p, a) for p, a in cells()}
    if not isinstance(quotas, dict) or set(quotas) != keys or any(
            sum(int(quotas[cell_key(p, a)]) for a in asc.ARM_GROUPS) != int(NEGATIVE_QUOTAS[p]) for p in PAPERS):
        bad.append(f"its quotas are not {dict(NEGATIVE_QUOTAS)} per paper")
    if drawn != quotas:
        bad.append("its drawn counts are not its quotas")
    want = sum(int(v) for v in NEGATIVE_QUOTAS.values())
    if summ.get("n_drawn") != want or n_sample != want:
        bad.append(f"it did not draw {want} pairs (n_drawn {summ.get('n_drawn')!r}, sample rows {n_sample})")
    return bad


def read_negative_dir(negative_dir: Path, repo: Path | None = None
                      ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, str]]:
    """The draw's ``sample-negative.jsonl`` and its own chunk run, checked against the draw's summary,
    and the summary against the committed seed and the plan (:func:`negative_summary_problems`)."""
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
    sample = read(samp_p)
    bad = negative_summary_problems(summ, len(sample), repo)
    if bad:
        raise SystemExit(f"REFUSED: the negative directory is not a draw made after the committed seed "
                         f"({'; '.join(bad)}); nothing written")
    return sample, acc.control_docs(chunks_p), shas


def run_items(confirm_dir: Path, label_table: Path, main_config: Path, negative_dir: Path, out_dir: Path,
              public_out: Path | None = None, repo: Path | None = None, *,
              allow_plan_mismatch: bool = False) -> dict[str, Any]:
    env = acc.require_env()
    rp.refuse_in_repo(out_dir, "--out-dir")
    pairs, cdocs, texts, shas = confirm_pairs(confirm_dir, label_table)
    negatives, ndocs, nshas = read_negative_dir(negative_dir, repo)
    ntexts = doc_texts(ndocs, "the negative draw's chunks-control.jsonl")
    if shared_doc_problems(ndocs, cdocs):  # review A4: the draw's gate, checked again on what items reads
        raise SystemExit("REFUSED: a document both chunk runs hold has other chunk ids, spans or texts in the "
                         "negative draw's chunks-control.jsonl (§3.2 (b)7 gate); nothing written")
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
    counts = item_counts(it)
    # Fable F5: the plan's counts (§5 (b)) are the run's premise; another count is refused unless the
    # operator allows it, and then it is written into the summary as a deviation
    diff = counts["plan_counts"]["differences"]
    if diff and not allow_plan_mismatch:
        raise SystemExit(f"REFUSED: the items are not the plan's counts ({'; '.join(diff)}); nothing written. "
                         "--allow-plan-mismatch proceeds and records the mismatch as a deviation")
    deviation = ({"plan_count_mismatch": list(diff), "allowed_by": "--allow-plan-mismatch",
                  "note": "the items differ from PLAN §5 (b)'s counts; a recorded deviation, to be stated in the "
                          "write-up"} if diff else None)
    esha = acc.write_private(out_dir / "neighbour-export.jsonl", acc.jsonl_bytes(export_rows), "the neighbour export")
    ssha = acc.write_private(out_dir / "neighbour-seed.jsonl", acc.jsonl_bytes(it.seeds), "the neighbour seed")
    all_texts = {**texts, **ntexts}
    docs = sorted(all_texts)
    rec: dict[str, Any] = {
        "schema": SCHEMA["items"], "exploratory": True, "offline": True, "calls": CALLS, "plan": PREREG,
        "item_type": "neighbour", "formatter": FORMATTER, "max_context_chars": budget,
        "nominal_budget_chars": {f"context_window_{w}": budget * (2 * w + 1) for w in WINDOWS},
        "budget_chars_per_item": {str(k): v for k, v in sorted(Counter(
            r["metadata"]["budget_chars"] for r in export_rows).items())},
        "id_scheme": f"{NBR_PREFIX}w<w>:<max-overlap chunk id>", "tokenizer": acc.TOKENIZER,
        "tokens_counted_on": "the passage as judged (markers included)",
        "edge_rule": "before: M is its document's first chunk; after: the last; none: neighbours on both sides "
                     "(at w=2 a side holding one neighbour, M being the second or second-to-last chunk, is none)",
        **counts,
        "deviation": deviation,
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

def _repo(repo: Path | None) -> Path:
    import alias_study_judge as aj

    return Path(repo or aj.REPO)


def _git(repo: Path | None, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(_repo(repo)), *args], capture_output=True, check=False, timeout=60)


def head_seed_sha256(repo: Path | None = None) -> str | None:
    """The sha256 of :data:`SEED_RELPATH` as committed at HEAD, ``None`` when HEAD has no such file."""
    import hashlib

    try:
        r = _git(repo, "show", f"HEAD:{SEED_RELPATH}")
    except (OSError, subprocess.SubprocessError):
        return None
    return hashlib.sha256(r.stdout).hexdigest() if r.returncode == 0 else None


def head_commit(repo: Path | None = None) -> str | None:
    try:
        r = _git(repo, "rev-parse", "HEAD")
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode().strip() if r.returncode == 0 else None


def seed_file_problems(seed_file: Path, repo: Path | None = None) -> list[str]:
    """§3.2 (b)6, as clarified (module docstring): the seed file is the repository's
    :data:`SEED_RELPATH` itself (not a copy with equal bytes elsewhere), it is tracked
    (``git ls-files --error-unmatch``) and identical to HEAD, staged changes included (``git diff
    --quiet HEAD``), and the tree is clean (untracked files included). Checked before anything else
    is read."""
    repo = _repo(repo)
    if not Path(seed_file).is_file():
        return ["the seed file does not exist"]
    bad = []
    if Path(seed_file).resolve() != (repo / SEED_RELPATH).resolve():
        bad.append(f"the seed file is not the repository's {SEED_RELPATH}")
    try:
        st = _git(repo, "status", "--porcelain")
        tracked = _git(repo, "ls-files", "--error-unmatch", "--", SEED_RELPATH)
        same = _git(repo, "diff", "--quiet", "HEAD", "--", SEED_RELPATH)
    except (OSError, subprocess.SubprocessError):
        return [*bad, "git failed"]
    if st.returncode != 0:
        return [*bad, "git status failed"]
    if st.stdout.strip():
        bad.append("the working tree is not clean")
    if tracked.returncode != 0:
        bad.append(f"{SEED_RELPATH} is not tracked (commit it first)")
    elif same.returncode != 0:
        bad.append(f"{SEED_RELPATH} differs from HEAD (staged or unstaged; commit it first)")
    else:
        # the bytes read are HEAD's bytes (#999 review: a symlinked seed, or one marked
        # assume-unchanged / skip-worktree, passes ls-files and diff while its content changes)
        import hashlib

        if Path(seed_file).is_symlink() or (repo / SEED_RELPATH).is_symlink():
            bad.append(f"{SEED_RELPATH} is a symlink (commit the file itself)")
        elif hashlib.sha256(Path(seed_file).read_bytes()).hexdigest() != head_seed_sha256(repo):
            bad.append(f"{SEED_RELPATH} bytes differ from HEAD's (assume-unchanged / skip-worktree?)")
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
    applies, or ``None`` with its M ± 1 passage. Geometric only: no label is read.

    ``collision_negative`` (review A1, a clarification of §3.2 (b)8): a candidate whose (record, M)
    is an earlier eligible candidate's (walk order across all cells, whether or not that one is
    later drawn) would get that one's neighbour item id and merge with it into one registry pair;
    it is excluded, as ``collision_positive`` keeps the negatives apart from the positives."""
    out: list[tuple[str | None, Passage | None]] = []
    claimed: set[tuple[str, str]] = set()
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
        elif (f["record_id"], s["chunk_id"]) in claimed:
            out.append(("collision_negative", None))
        else:
            claimed.add((f["record_id"], s["chunk_id"]))
            out.append((None, q))
    return out


def shared_doc_problems(ndocs: Mapping[str, Sequence[Mapping[str, Any]]],
                        cdocs: Mapping[str, Sequence[Mapping[str, Any]]]) -> int:
    """§3.2 (b)7's gate: the documents both chunk runs hold must have identical chunks: ids, spans
    and texts (review A4: a chunk id is ``uuid5(doc:start:end)``, so a text of equal length changed
    under ``--texts`` keeps every id and span; the content is compared too)."""
    def chunks(rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, int, int, str]]:
        return [(r["chunk_id"], r["start_char"], r["end_char"], r["content"]) for r in rows]
    return sum(chunks(ndocs[d]) != chunks(cdocs[d]) for d in set(ndocs) & set(cdocs))


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
        raise SystemExit("REFUSED: a document the confirmatory run also chunked got other chunk ids, spans or "
                         "texts (§3.2 (b)7 gate); sample-negative.jsonl not written")
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
            len({r["stage_a_chunk_id"] for r in out_rows}) != len(out_rows) or \
            len({(r["record_id"], r["max_overlap_chunk_id"]) for r in out_rows}) != len(out_rows):
        raise SystemExit("REFUSED: the draw broke its own rule (two pairs of one (record, arm), one window or one "
                         "(record, M))")
    nsha = acc.write_private(out_dir / "sample-negative.jsonl", acc.jsonl_bytes(out_rows), "the negative sample")
    texts_by_doc = doc_texts({d: ndocs[d] for d in sorted({r["doc_id"] for r in out_rows})}, "the draw's chunks")
    docs = sorted(texts_by_doc)
    rec: dict[str, Any] = {
        "schema": SCHEMA["negative"], "exploratory": True, "offline": True, "calls": CALLS, "plan": PREREG,
        "stratum": NEGATIVE_STRATUM, "seed_file": SEED_RELPATH, "seed_file_sha256": sha(seed_file),
        "seed_commit": head_commit(repo),
        "seed_is_the_proposed_one": seed == NEGATIVE_SEED_PROPOSED,
        "draw_rule": "§3.2 (b): stage-A agreed not pairs minus the confirmatory and control samples (pair id and "
                     "window); quotas same 100 / other 50, arms by drift_allocation over the cell records; cells "
                     "walked paper x arm in the seeded order, one pair per (record, arm) and per window across all "
                     "cells; the prefix counts 3 x quota candidates after that filter; the first quota eligible "
                     "candidates per cell are drawn; a short cell is refused",
        "eligibility": "M resolves; M has a neighbour on both sides; the M ± 1 passage is untrimmed; (record, M) is "
                       "no positive item's and no earlier eligible candidate's (walk order, all cells)",
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


# ------------------------------------------------------------------ wall-read / wall-counts (§3.2 (c))

def segment_edges(doc_id: str, text: str) -> set[int]:
    """The adopted chunking's segment edges (``split_feasibility.py``: ``{g.start for g in
    doc_segments(cd.doc)} - {0}`` on the control chunker's document)."""
    cd = acc.chunk_document(doc_id, text, acc.control_chunker())
    return {g.start for g in acc.doc_segments(cd.doc)} - {0}


def mark_walls(text: str, ws: int, we: int, edges: Iterable[int]) -> str:
    """The window ``text[ws:we]`` with :data:`WALL_MARK` inserted at every edge strictly inside it."""
    inside = sorted(x for x in edges if ws < x < we)
    parts, pos = [], ws
    for x in inside:
        parts += [text[pos:x], WALL_MARK]
        pos = x
    parts.append(text[pos:we])
    return "".join(parts)


def wall_windows(pairs: Sequence[ConfirmPair], texts: Mapping[str, str]) -> list[tuple[ConfirmPair, list[int]]]:
    """§3.2 (c)1: the split windows a segment edge crosses (``window_crosses_segment_edge``)."""
    edges: dict[str, set[int]] = {}
    out = []
    for p in pairs:
        if p.group != "split":
            continue
        if p.doc_id not in edges:
            edges[p.doc_id] = segment_edges(p.doc_id, texts[p.doc_id])
        inside = sorted(x for x in edges[p.doc_id] if p.ws < x < p.we)
        if inside:
            out.append((p, inside))
    return out


def neighbour_registry_problems(neighbour_config: Path, main_raw: Mapping[str, Any]) -> list[str]:
    """§3.2 (c)2: the read happens before any neighbour label exists. The config must be the
    neighbour control config (``control_config_problems``) and its ``out_dir`` must hold no
    ``pairs.jsonl`` and no ``final.jsonl``."""
    import alias_study_judge as aj

    raw = raw_config(neighbour_config)
    bad = aj.control_config_problems(raw, main_raw)
    out = Path(str(raw.get("out_dir")))
    bad += [f"the neighbour registry's out_dir already holds {n}" for n in ("pairs.jsonl", "final.jsonl")
            if (out / n).exists()]
    return bad


def run_wall_read(confirm_dir: Path, label_table: Path, main_config: Path, neighbour_config: Path,
                  out_dir: Path) -> dict[str, Any]:
    import alias_study_spot as asp

    acc.require_env()
    rp.refuse_in_repo(out_dir, "--out-dir")
    bad = neighbour_registry_problems(neighbour_config, raw_config(main_config))
    if bad:
        raise SystemExit(f"REFUSED: {'; '.join(bad)}; the wall read must precede any neighbour label")
    pairs, _cdocs, texts, shas = confirm_pairs(confirm_dir, label_table)
    found = wall_windows(pairs, texts)
    if len(found) != WALL_N:
        raise SystemExit(f"REFUSED: {len(found)} split windows cross a segment edge; PLAN §3.2 (c)1 expects exactly "
                         f"{WALL_N}; nothing written")
    cfg = load_judge_cfg(main_config)
    sa = asp.committed_stage_a(cfg)
    if sa["commit_sha256"] != STAGE_A_COMMIT_SHA256:
        raise SystemExit("REFUSED: the committed stage-A commit is not the one the confirmatory test pinned")
    reg = {p["pair_id"]: p for p in sa["pairs"]}
    order = sorted(found, key=lambda x: (asc.seeded_key(WALL_ORDER_SEED, x[0].control_of), x[0].control_of))
    items, key_items = [], []
    for i, (p, inside) in enumerate(order, 1):
        r = reg.get(p.control_of)
        if r is None or r.get("record_id") != p.record_id or r.get("chunk_id") != p.window_id:
            raise SystemExit("REFUSED: a wall window's pair is not in the committed stage-A registry")
        items.append({"item": i, "question": r["question"], "term": r["term"],
                      "window": mark_walls(texts[p.doc_id], p.ws, p.we, inside)})
        key_items.append({"item": i, "control_of": p.control_of, "record_id": p.record_id,
                          "window_id": p.window_id, "doc_id": p.doc_id, "start_char": p.ws, "end_char": p.we,
                          "edges_inside": len(inside)})
    sheet = {"schema": SCHEMA["wall_sheet"], "plan": PREREG, "wall_mark": WALL_MARK,
             "instructions": "For each item: does the answer to the question need text on BOTH sides of a "
                             f"{WALL_MARK} (needs_both), is it on one side only (one_side), or can you not tell "
                             "(unclear)? Write one answer per item in the answer file; this sets no label.",
             "answers_allowed": list(WALL_ANSWERS), "items": items}
    sheet_sha = acc.write_private(out_dir / "wall-sheet.json", (json.dumps(sheet, indent=1, ensure_ascii=False)
                                                                + "\n").encode("utf-8"), "the wall-read sheet")
    key = {"schema": SCHEMA["wall_key"], "sheet_sha256": sheet_sha, "items": key_items}
    key_sha = acc.write_private(out_dir / "wall-key.json", (json.dumps(key, indent=1) + "\n").encode("utf-8"),
                                "the wall-read key")
    answers = {"schema": SCHEMA["wall_answers"], "sheet_sha256": sheet_sha, "key_sha256": key_sha,
               "reader_role": None, "date": None, "roles_allowed": list(WALL_ROLES),
               "answers_allowed": list(WALL_ANSWERS), "answers": {str(i): None for i in range(1, WALL_N + 1)}}
    ans_sha = acc.write_private(out_dir / "wall-answers.json", (json.dumps(answers, indent=1) + "\n").encode("utf-8"),
                                "the empty wall-read answer file")
    return {"items": len(items), "edges_inside": sum(k["edges_inside"] for k in key_items),
            "sheet_sha256": sheet_sha, "key_sha256": key_sha, "answers_sha256": ans_sha,
            "inputs_sha256": shas}


def _json_file(path: Path, schema: str, what: str) -> dict[str, Any]:
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"REFUSED: {what} is not readable JSON") from e
    if not isinstance(obj, dict) or obj.get("schema") != schema:
        raise SystemExit(f"REFUSED: {what} is not a {schema} file")
    return obj


def wall_answer_problems(sheet: Mapping[str, Any], key: Mapping[str, Any], answers: Mapping[str, Any],
                         shas: Mapping[str, str]) -> list[str]:
    bad = []
    if key.get("sheet_sha256") != shas["sheet"]:
        bad.append("the key is not the sheet's")
    if answers.get("sheet_sha256") != shas["sheet"] or answers.get("key_sha256") != shas["key"]:
        bad.append("the answer file is not the sheet's and the key's")
    n = len(sheet.get("items") or [])
    if n != WALL_N or len(key.get("items") or []) != n:
        bad.append(f"the sheet and the key must hold {WALL_N} items")
    a = answers.get("answers")
    want = {str(i) for i in range(1, WALL_N + 1)}
    if not isinstance(a, dict) or set(a) != want:
        bad.append(f"the answers must be keyed 1..{WALL_N}")
    elif any(v not in WALL_ANSWERS for v in a.values()):
        bad.append(f"every answer must be one of {list(WALL_ANSWERS)} (unanswered items remain)")
    if answers.get("reader_role") not in WALL_ROLES:
        bad.append(f"reader_role must be one of {list(WALL_ROLES)}")
    try:
        dt.date.fromisoformat(str(answers.get("date")))
    except ValueError:
        bad.append("date must be an ISO date (YYYY-MM-DD)")
    return bad


def run_wall_counts(sheet_p: Path, key_p: Path, answers_p: Path, out_dir: Path,
                    public_out: Path | None = None) -> dict[str, Any]:
    for p, what in ((sheet_p, "--sheet"), (key_p, "--key"), (answers_p, "--answers"), (out_dir, "--out-dir")):
        rp.refuse_in_repo(Path(p), what)
    sheet = _json_file(sheet_p, SCHEMA["wall_sheet"], "--sheet")
    key = _json_file(key_p, SCHEMA["wall_key"], "--key")
    answers = _json_file(answers_p, SCHEMA["wall_answers"], "--answers")
    shas = {"sheet": sha(sheet_p), "key": sha(key_p), "answers": sha(answers_p)}
    bad = wall_answer_problems(sheet, key, answers, shas)
    if bad:
        raise SystemExit(f"REFUSED: {'; '.join(bad)}; nothing written")
    c = Counter(answers["answers"].values())
    rec = {"schema": SCHEMA["wall_counts"], "exploratory": True, "offline": True, "calls": CALLS, "plan": PREREG,
           "operator_read": "a private read of the split windows a section edge crosses; no judge or gateway call; "
                            "sets no label",
           "n": WALL_N, "counts": {k: c[k] for k in WALL_ANSWERS}, "reader_role": answers["reader_role"],
           "date": str(answers["date"]), "sha256": {"sheet": shas["sheet"], "key": shas["key"],
                                                    "answers": shas["answers"]},
           "provenance": acc.public_provenance(acc.provenance())}
    forbidden = {str(k.get(f)) for k in key["items"] for f in ("control_of", "record_id", "window_id", "doc_id")}
    texts = [str(it.get(f)) for it in sheet["items"] for f in ("question", "term", "window")]
    publish(rec, out_dir / "wall-read-counts.json", public_out, "wall-read-counts.json", forbidden, texts)
    return rec


# ------------------------------------------------------------------ report (review nit d)

LABEL_WORDS = ("answers", "not", "unjudged", "no_final_row")
EDGE_SPLITS = ("all", "without_edge", "edge_only")


def read_items_dir(items_dir: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """``items``' private export and seed rows, checked against its ``neighbour-items.json``."""
    summ_p = items_dir / "neighbour-items.json"
    summ = _json_file(summ_p, SCHEMA["items"], "neighbour-items.json")
    shas = {"neighbour_export": sha(items_dir / "neighbour-export.jsonl"),
            "neighbour_seed": sha(items_dir / "neighbour-seed.jsonl"), "neighbour_items": sha(summ_p)}
    outs = summ.get("outputs_sha256") or {}
    if outs.get("neighbour_export") != shas["neighbour_export"] or outs.get("neighbour_seed") != shas["neighbour_seed"]:
        raise SystemExit("REFUSED: the items directory's files are not the ones its neighbour-items.json names")
    export = {r["chunk_id"]: r for r in read(items_dir / "neighbour-export.jsonl")}
    return export, read(items_dir / "neighbour-seed.jsonl"), shas


def report_counts(export: Mapping[str, Mapping[str, Any]], seeds: Sequence[Mapping[str, Any]],
                  finals: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Per group and context window: the items' final labels, each group ``all`` / ``without_edge``
    / ``edge_only`` (§3.2 (b)9), with the Wilson interval of the *answers* rate under both policies
    for unjudged items (review A8 / Fable F5): ``answers_rate_unjudged_as_not`` (over answers + not +
    unjudged, as ``alias_control_chunks.label_of`` counts) and ``answers_rate_unjudged_dropped`` (over
    answers + not, the confirmatory analysis's primary policy). For the negative control the first
    lowers the false-answers rate, so neither is reported alone; the unjudged count is kept. An item
    is one (record, neighbour item id), the registry's pair; one in several groups counts in each."""
    import alias_study_judge as aj

    units: dict[tuple[str, str], dict[str, Any]] = {}
    for s in seeds:
        if not parse_nbr_id(s["chunk_id"]) or s["chunk_id"] not in export:
            raise SystemExit("REFUSED: a seed row names no neighbour item of the export")
        pid = aj.pair_id(s["record_id"], s["chunk_id"])
        units[(s["group"], pid)] = {"w": int(s["context_window"]), "edge": export[s["chunk_id"]]["metadata"]["edge"]}
    foreign = set(finals) - {pid for _g, pid in units}
    if foreign:
        raise SystemExit(f"REFUSED: the final file holds {len(foreign)} pair(s) that are no neighbour item; it is not "
                         "the neighbour registry's final.jsonl")
    tab: dict[str, dict[str, dict[str, Counter[str]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(Counter)))
    for (g, pid), u in units.items():
        word = rp.label_word(finals.get(pid)) or "no_final_row"
        w = f"context_window_{u['w']}"
        for split in ("all", "edge_only" if u["edge"] != "none" else "without_edge"):
            tab[g][w][split][word] += 1
    out: dict[str, Any] = {}
    for g in (*POSITIVE_GROUPS, NEGATIVE_GROUP):
        out[g] = {}
        for w in sorted(tab[g]):
            out[g][w] = {}
            for split in (EDGE_SPLITS if g != NEGATIVE_GROUP else ("all",)):
                c = tab[g][w][split]
                out[g][w][split] = {
                    **{k: c[k] for k in LABEL_WORDS},
                    "answers_rate_unjudged_as_not": acc.wilson(c["answers"], c["answers"] + c["not"] + c["unjudged"]),
                    "answers_rate_unjudged_dropped": acc.wilson(c["answers"], c["answers"] + c["not"])}
    return {"by_group": out, "items": len(units), "final_rows": len(finals),
            "items_without_final_row": sum(1 for _g, pid in units if pid not in finals)}


def run_report(items_dir: Path, neighbour_final: Path, out_dir: Path, public_out: Path | None = None) -> dict[str, Any]:
    rp.refuse_in_repo(out_dir, "--out-dir")
    rp.refuse_in_repo(Path(neighbour_final), "--neighbour-final")
    export, seeds, shas = read_items_dir(items_dir)
    finals = rp.final_rows(neighbour_final)
    counts = report_counts(export, seeds, finals)
    rec = {"schema": SCHEMA["report"], "exploratory": True, "offline": True, "calls": CALLS, "plan": PREREG,
           "item_type": "neighbour",
           "rate_rule": {"answers_rate_unjudged_as_not": "answers / (answers + not + unjudged): unjudged counted as "
                                                         "not (alias_control_chunks.label_of); lowers the negative "
                                                         "control's false-answers rate",
                         "answers_rate_unjudged_dropped": "answers / (answers + not): unjudged dropped (the "
                                                          "confirmatory analysis's primary policy)",
                         "no_final_row": "items without a final row are in neither denominator"},
           "edge_rule": "without_edge: M has neighbours on both sides; edge_only: a document edge removed one side "
                        "(§3.2 (b)9); negatives carry no edge item by the draw's rule",
           "negative_control": "the negative group's answers rates are the false-answers rate, under both "
                               "unjudged policies",
           **counts,
           "inputs_sha256": {**shas, "neighbour_final": sha(neighbour_final)},
           "provenance": acc.public_provenance(acc.provenance())}
    forbidden = (set(export) | set(finals) | {s["record_id"] for s in seeds} | {s["control_of"] for s in seeds}
                 | {str(r["metadata"].get("max_overlap_chunk_id")) for r in export.values()}
                 | {c for r in export.values() for c in r["metadata"].get("neighbour_chunk_ids") or ()}
                 | {str(r["doc_id"]) for r in export.values()}
                 | {str(r["metadata"].get("source_path") or "") for r in export.values()})
    publish(rec, out_dir / "neighbour-report.json", public_out, "neighbour-report.json", forbidden,
            [r["content"] for r in export.values()])
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
    a.add_argument("--allow-plan-mismatch", action="store_true",
                   help="proceed when the items differ from PLAN §5 (b)'s counts, recording the mismatch as a "
                        "deviation in neighbour-items.json (refused by default)")
    d = sub.add_parser("draw-negative", help="§3.2 (b): the 150 stage-A not pairs of the negative control")
    d.add_argument("--main-config", type=Path, required=True, help="the alias main run's judge config")
    d.add_argument("--seed-file", type=Path, required=True,
                   help=f"the repository's {SEED_RELPATH}, committed at HEAD on a clean tree before the draw")
    d.add_argument("--confirm-dir", type=Path, required=True, help="the private confirmatory directory")
    d.add_argument("--confirm-sample", type=Path, required=True, help="the confirmatory sample.jsonl (351 pairs)")
    d.add_argument("--control-sample", type=Path, required=True, help="the exploratory control sample.jsonl (150)")
    d.add_argument("--texts", type=Path, required=True, action="append", help="record texts, as for chunk")
    d.add_argument("--out-dir", type=Path, required=True, help="private, outside the repository, written once")
    d.add_argument("--public-out", type=Path, help="also write the counts here (artifacts/negative-draw.json)")
    w = sub.add_parser("wall-read", help="§3.2 (c): the private reading sheet of the split windows a wall crosses")
    w.add_argument("--confirm-dir", type=Path, required=True, help="the private confirmatory directory")
    w.add_argument("--label-table", type=Path, required=True, help="the rechunk label table")
    w.add_argument("--main-config", type=Path, required=True, help="the alias main run's judge config")
    w.add_argument("--neighbour-config", type=Path, required=True,
                   help="the neighbour control config; its out_dir must hold no pairs.jsonl / final.jsonl yet")
    w.add_argument("--out-dir", type=Path, required=True, help="private, outside the repository, written once")
    c = sub.add_parser("wall-counts", help="§3.2 (c)4: the counts-only result of the wall read")
    c.add_argument("--sheet", type=Path, required=True, help="wall-sheet.json from wall-read")
    c.add_argument("--key", type=Path, required=True, help="wall-key.json from wall-read")
    c.add_argument("--answers", type=Path, required=True, help="wall-answers.json, filled in by the reader")
    c.add_argument("--out-dir", type=Path, required=True, help="private, outside the repository, written once")
    c.add_argument("--public-out", type=Path, help="also write the counts here (artifacts/wall-read-counts.json)")
    r = sub.add_parser("report", help="review nit d: the counts-only result of the neighbour registry's final.jsonl")
    r.add_argument("--items-dir", type=Path, required=True, help="the items --out-dir")
    r.add_argument("--neighbour-final", type=Path, required=True,
                   help="the neighbour registry's final.jsonl, after finalize")
    r.add_argument("--out-dir", type=Path, required=True, help="private, outside the repository, written once")
    r.add_argument("--public-out", type=Path, help="also write the counts here (artifacts/neighbour-report.json)")
    args = ap.parse_args(argv)
    if args.cmd == "items":
        rec = run_items(args.confirm_dir, args.label_table, args.main_config, args.negative_dir, args.out_dir,
                        args.public_out, allow_plan_mismatch=args.allow_plan_mismatch)
        print(json.dumps({k: rec[k] for k in ("items", "items_total", "trims_found", "plan_counts", "deviation")},
                         indent=1))
    elif args.cmd == "draw-negative":
        rec = run_draw_negative(args.main_config, args.seed_file, args.confirm_dir, args.confirm_sample,
                                args.control_sample, args.texts, args.out_dir, args.public_out)
        print(json.dumps({k: rec[k] for k in ("quotas", "drawn", "n_drawn", "exclusions")}, indent=1))
    elif args.cmd == "wall-read":
        res = run_wall_read(args.confirm_dir, args.label_table, args.main_config, args.neighbour_config, args.out_dir)
        print(json.dumps({k: res[k] for k in ("items", "edges_inside")}, indent=1))
    elif args.cmd == "wall-counts":
        rec = run_wall_counts(args.sheet, args.key, args.answers, args.out_dir, args.public_out)
        print(json.dumps({k: rec[k] for k in ("n", "counts", "reader_role", "date")}, indent=1))
    elif args.cmd == "report":
        rec = run_report(args.items_dir, args.neighbour_final, args.out_dir, args.public_out)
        print(json.dumps(rec["by_group"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
