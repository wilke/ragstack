#!/usr/bin/env python
"""G1 — retrieval-parameter sweep over small, user-owned libraries (#200).

Gate ``docs/libraries-spec.md`` §-1 G1. Protocol:
``reports/g1-library-retrieval/PROTOCOL.md`` (its SHA-256 is stamped into every
manifest as ``protocol_version``).

Why this exists
---------------
RAGStack's shipping retrieval defaults — ``rrf_k=60``,
``retrieval_candidate_multiplier=2``, ``top_k=5``, ``rerank_enabled=False``
(``ragstack/config.py:304-325``) — have **never been measured**. Every prior
retrieval-quality run in this repository (``chunking_compare*``,
``scifact_chunk_eval``) constructed ``HybridRetriever(vstore, tindex, embedder)``
with no ``rrf_scorer`` and no ``candidate_multiplier`` and drove it with
``--retrieve-pool 300 --rerank-pool 100``, i.e. a configuration nothing ships.
This harness is the seam that varies them.

This pilot: the **document-count sweep**
----------------------------------------
The protocol's full design embeds a fixed judged core in a *distractor ladder* that
grows the index to 36k/200k/1M chunks. This module implements the narrower,
prior question first: build a real small library at **50 / 100 / 200 documents**
out of SciFact and sweep the retrieval parameters at each size. The ladder is a
follow-up and the code is shaped for it rather than against it:

* ``build_library_index(..., extra_chunks=...)`` takes pre-embedded distractor
  chunks (the future prod-scroll output) and writes them to **both** Qdrant and
  ES, so adding a rung is a new *source* function, not a rewrite of the builder.
* Every manifest already carries a ``distractors`` block
  (``source_collection`` / ``spec_hash_match`` / ``n_chunks``), zero-filled today.
* The cell grid is keyed on ``rung`` (currently ``"n50" | "n100" | "n200"``), so
  ladder rungs slot in as additional rung labels with no schema change.

**Know what a document-count sweep can and cannot tell you.** SciFact abstracts
chunk at ~1.2 chunks/doc, so 50/100/200 documents are ~61/122/244 *chunks* —
while a real ``fixed_tok512`` PDF library is ~36 chunks/doc, i.e. a 200-document
library is ~7,000 chunks (protocol §1.3b; the spec's "~4k chunks" at
``libraries-spec.md:11`` is itself understated). Two consequences, both stamped
into every report by ``_scale_banner``: the retrieval task at these sizes is near
saturated, and Qdrant builds no HNSW graph below its ``indexing_threshold``, so
the dense leg runs as an *exact brute-force scan* and cannot exhibit the
approximate-search truncation this harness instruments for. The document-count
sweep is a prior on the parameters; the distractor ladder is what turns it into
a library-scale measurement.

Corpus construction, and what happens to unsatisfiable queries
--------------------------------------------------------------
:func:`sample_library` is deterministic given ``(corpus, qrels, n_docs, seed)``:

1. Shuffle the test query ids once with ``random.Random(seed)`` → a fixed order.
2. Walk that order accumulating each query's judged doc ids, and **stop at the
   first query that would push the judged-doc count over
   ``floor(judged_fraction * n_docs)``** (default ``judged_fraction=0.5``).
   Because we stop rather than skip, the retained query set is a *prefix* of the
   shuffled order — so the 50-doc query set is a subset of the 100-doc set is a
   subset of the 200-doc set, and per-query scores are paired across library
   sizes as well as across configurations.
3. Fill the remaining ``n_docs - |judged|`` slots from a single seeded shuffle of
   the non-judged corpus, taking a prefix — so the distractor sets nest too.

**Queries whose relevant documents are not all in the library are DROPPED, not
kept as guaranteed misses.** Justification: such a query scores a deterministic
0.0 under *every* configuration in the grid, so it carries no information about
the parameters under test; keeping it would multiply every per-query metric array
by the same constant < 1, attenuating |Δ| uniformly toward zero while inflating
n, i.e. it would make the sweep *look* better powered while making every effect
smaller. The paired tests are unaffected in sign but the pre-registered
δ = 0.02 threshold is an absolute nDCG difference, so attenuation is not benign.
Every retained query has its full judged set present, which is also what makes
recall@k well defined at library scale. The retained/dropped counts and the
sampling digest are recorded in the manifest.

The pre-registered primary comparison (exactly one)
---------------------------------------------------
    At the largest library size in the run, **shipping defaults**
    (``mode=hybrid, rrf_k=60, top_k=5, candidate_multiplier=2, rerank_enabled=False``
    → per-leg depth 10) **versus the same cell with
    ``candidate_multiplier=10``** (per-leg depth 100), on document-level
    **nDCG@10**, paired over the retained SciFact test queries,
    minimum effect δ = 0.02.

Chosen before any measurement because §1.2 of the protocol identifies retrieval
breadth as the only knob ever observed to move this metric (ΔnDCG@10 ≈ +0.046
between R5 and R5b, twice the largest chunking effect ever measured here), and
because it is the one comparison whose outcome changes a shipping default on its
own. Both cells are force-added to every grid so the comparison always exists.
**Everything else in the grid is an exploratory screen** and is reported as such:
Holm–Bonferroni is applied across the whole grid so no screening cell can be
mistaken for a confirmed result.

Per-leg instrumentation (protocol §5.4 / H1b)
---------------------------------------------
``RRFScorer.fuse`` relabels every chunk ``retrieval_method="hybrid"``
(``scoring/scorers.py:43``), erasing leg provenance, and ``retrieve()`` returns
only the fused list — so per-leg accounting needs new instrumentation.
:class:`InstrumentedHybridRetriever` is an **eval-only subclass** (the production
class is untouched) recording per query: ``dense_hits``, ``bm25_hits``,
``dense_deficit`` / ``bm25_deficit`` vs the requested per-leg depth,
``union_depth``, ``overlap``, ``fused_depth`` and ``rerank_pool_occupancy``.
``test_g1_library_sweep.py`` pins it to produce byte-identical output to
``HybridRetriever.retrieve`` at the same depth, so the instrumentation cannot
silently fork retrieval behaviour.

The registered hypothesis this tests: the spec's premise that "BM25 may return
3-4 hits against dense's 20" (``libraries-spec.md:11``) is **backwards**.
``ElasticsearchTextIndex.search`` issues an exact ``size=D`` match query and
returns ``min(D, |matching docs|)``; ``QdrantVectorStore.search`` passes ``limit``
with **no ``search_params``** — no ``hnsw_ef``, no ``exact`` — so the *dense* leg
is the one that can silently under-return on approximate HNSW. A cell whose
deficit rate exceeds 1% is marked ``INVALID (hit deficit)`` and excluded from the
statistics, mirroring the G2 pass criterion in
``scripts/bench_filter_truncation.py``.

Safety
------
Every mutating call goes through :func:`guard_scratch`, which refuses any name
not starting with ``g1_`` — production Qdrant (``ragstack_sfr_*``) and ES live on
the same host and are only ever *read*. Collections are dropped in a ``finally:``
unless ``--keep``.

Operational note
----------------
The embedding fleet on ``:9001-9008`` is **shared with production**. A sweep
embeds the judged core once per library size (a few thousand chunks) and then
embeds each query exactly once into an on-disk cache, so its fleet footprint is
small — but do **not** run it during a bulk ingest: contention perturbs latency
metrics, and ``detect_live_endpoints`` narrowing mid-run changes which endpoints
served which cell. Qdrant HNSW is also nondeterministic under concurrent
optimizer activity, which is exactly what a concurrent ingest causes.

Usage::

    cd python
    # smoke (~2 minutes): one size, the two pre-registered cells, few queries
    /rag/envs/ragstack/bin/python scripts/eval/g1_library_sweep.py \\
        --doc-counts 50 --query-limit 8 --smoke

    # the full pilot sweep
    /rag/envs/ragstack/bin/python scripts/eval/g1_library_sweep.py \\
        --doc-counts 50,100,200 \\
        --modes hybrid,vector,bm25 \\
        --rrf-k 1,10,20,60,120,240 \\
        --top-k 5,10 \\
        --multipliers 1,2,5,10,20 \\
        --rerank off,on --rerank-candidates 50
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import Any

import httpx

from ragstack import provenance
from ragstack.models import Chunk, ScoredChunk
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.scoring.scorers import RRFScorer, SidecarReranker
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.qdrant import QdrantVectorStore
from ragstack.tenancy import scope_filters

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import _stats  # noqa: E402
import chunking_compare_7way as c7  # noqa: E402
import scifact_chunk_eval as sfe  # noqa: E402

# --------------------------------------------------------------------------- #
# Safety rails — mirrors scripts/bench_filter_truncation.py::guard_scratch
# --------------------------------------------------------------------------- #
SCRATCH_PREFIX = "g1_"


def guard_scratch(name: str) -> str:
    """Refuse to touch anything that is not unmistakably ours.

    Production Qdrant/ES are on the same host; every create / upsert / index /
    delete in this module funnels through here first."""
    if not name.startswith(SCRATCH_PREFIX):
        raise SystemExit(
            f"REFUSING to mutate store {name!r}: G1 scratch stores MUST be named "
            f"{SCRATCH_PREFIX}*"
        )
    return name


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
REPO_ROOT = _HERE.parents[2]
REPORT_ROOT = REPO_ROOT / "reports" / "g1-library-retrieval"
PROTOCOL_PATH = REPORT_ROOT / "PROTOCOL.md"

DATASET_NAME = "scifact"
TENANT = "public"

# The ragstack_lib_v1 build spec (libraries-spec.md:213): fixed token window
# 512/64 over SFR-Embedding-Mistral @ 4096-d.
CHUNK_CONFIG_KEY = "fixed_tok512"

# Shipping defaults under test (ragstack/config.py:304-325).
DEFAULT_RRF_K = 60
DEFAULT_TOP_K = 5
DEFAULT_MULTIPLIER = 2
DEFAULT_RERANK_ENABLED = False
DEFAULT_RERANK_CANDIDATES = 50

# The single pre-registered primary comparison (see module docstring): shipping
# defaults vs. the identical cell at candidate_multiplier=10 (per-leg depth 100).
PRIMARY_MULTIPLIER = 10

# Minimum shippable effect, protocol §7.4.
DELTA = 0.02

# How deep a per-query ranking is stored. The report cutoff k is then free
# (protocol §6.3a): every metric at every k is recomputed from this list.
RANKING_DEPTH = 200

# Report cutoffs.
NDCG_DOC_K = 10          # primary
NDCG_CHUNK_K = 5         # co-primary
RECALL_KS = (10, 20, 100)
CURVE_KS = (1, 3, 5, 10, 20)

# A cell is void if more than this fraction of queries show a per-leg hit
# deficit (protocol §5.4, mirroring the G2 pass criterion).
MAX_DEFICIT_RATE = 0.01


# --------------------------------------------------------------------------- #
# Corpus sampling — pure, deterministic, unit-tested
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LibrarySample:
    """A deterministic N-document library carved out of a labelled corpus."""

    n_docs: int
    seed: int
    judged_fraction: float
    doc_ids: tuple[str, ...]
    judged_doc_ids: tuple[str, ...]
    distractor_doc_ids: tuple[str, ...]
    query_ids: tuple[str, ...]
    n_queries_available: int
    n_queries_dropped: int
    digest: str

    @property
    def rung(self) -> str:
        return f"n{self.n_docs}"


def _digest(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def sample_library(
    corpus_doc_ids: list[str],
    qrels: dict[str, dict[str, int]],
    n_docs: int,
    *,
    seed: int = 0,
    judged_fraction: float = 0.5,
) -> LibrarySample:
    """Carve a deterministic ``n_docs``-document library out of a labelled corpus.

    See the module docstring for the algorithm and for why unsatisfiable queries
    are dropped rather than retained as guaranteed misses. Returns a
    :class:`LibrarySample`; the query sets and distractor sets **nest** across
    increasing ``n_docs`` for a fixed seed.
    """
    if n_docs <= 0:
        raise ValueError(f"n_docs must be positive, got {n_docs}")
    corpus = sorted(set(corpus_doc_ids))
    corpus_set = set(corpus)
    all_qids = sorted(qrels, key=lambda q: (len(q), q))

    rng = random.Random(seed)
    shuffled_qids = list(all_qids)
    rng.shuffle(shuffled_qids)

    judged_budget = max(1, int(judged_fraction * n_docs))
    judged: list[str] = []
    judged_set: set[str] = set()
    kept_qids: list[str] = []
    for qid in shuffled_qids:
        rel = sorted(d for d, g in qrels[qid].items() if g > 0 and d in corpus_set)
        if not rel:
            # Its judged docs are not in this corpus at all — unsatisfiable at any
            # library size, so it never enters the prefix. Stopping here would make
            # the retained set depend on a corpus quirk, so skip and continue.
            continue
        new = [d for d in rel if d not in judged_set]
        if judged and len(judged) + len(new) > judged_budget:
            break  # stop, don't skip — keeps the retained query set a prefix
        if len(judged) + len(new) > n_docs:
            break
        judged.extend(new)
        judged_set.update(new)
        kept_qids.append(qid)

    if not kept_qids:
        raise SystemExit(
            f"no query fits a {n_docs}-doc library at judged_fraction="
            f"{judged_fraction} (budget {judged_budget} judged docs); raise "
            f"--doc-counts or --judged-fraction"
        )

    others = [d for d in corpus if d not in judged_set]
    rng_fill = random.Random(seed + 1)
    rng_fill.shuffle(others)
    distractors = others[: max(0, n_docs - len(judged))]

    doc_ids = tuple(sorted(judged) + sorted(distractors))
    return LibrarySample(
        n_docs=len(doc_ids),
        seed=seed,
        judged_fraction=judged_fraction,
        doc_ids=doc_ids,
        judged_doc_ids=tuple(sorted(judged)),
        distractor_doc_ids=tuple(sorted(distractors)),
        query_ids=tuple(kept_qids),
        n_queries_available=len(all_qids),
        n_queries_dropped=len(all_qids) - len(kept_qids),
        digest=_digest("|".join(doc_ids), "|".join(kept_qids), str(seed)),
    )


# --------------------------------------------------------------------------- #
# Per-leg instrumentation (protocol §5.4) — eval-only subclass
# --------------------------------------------------------------------------- #
@dataclass
class LegStats:
    """Per-query, per-leg accounting for one retrieval."""

    requested_depth: int
    dense_hits: int
    bm25_hits: int
    dense_deficit: int
    bm25_deficit: int
    union_depth: int
    overlap: int
    fused_depth: int
    dense_ms: float
    bm25_ms: float

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


class InstrumentedHybridRetriever(HybridRetriever):
    """``HybridRetriever`` that also reports what each leg actually returned.

    Eval-only: the production class is not modified. ``retrieve_instrumented``
    reproduces ``HybridRetriever.retrieve``'s leg selection, depth arithmetic,
    fusion and truncation exactly (pinned by a unit test) and additionally
    returns the **untruncated** fused union plus a :class:`LegStats`.

    Returning the untruncated union is what makes the report cutoff free
    (protocol §6.3a): ``top_k`` still governs the per-leg depth that was actually
    requested — the parameter under test — while every reported k is read back
    from one stored ranking.

    ``query_vector`` short-circuits the embedder so a sweep embeds each query
    exactly once (protocol §6.6) instead of once per cell.
    """

    async def retrieve_instrumented(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        mode: str = "hybrid",
        query_vector: list[float] | None = None,
    ) -> tuple[list[ScoredChunk], LegStats]:
        depth = top_k * self.candidate_multiplier
        ranked_lists: list[list[ScoredChunk]] = []
        dense: list[ScoredChunk] = []
        sparse: list[ScoredChunk] = []
        dense_ms = bm25_ms = 0.0

        if mode != "bm25":
            vec = query_vector
            if vec is None:
                vec = (await self.embedder.embed([query]))[0]  # type: ignore[attr-defined]
            t0 = time.perf_counter()
            dense = await self.vector_store.search(vec, top_k=depth, filters=filters)
            dense_ms = (time.perf_counter() - t0) * 1000.0
            ranked_lists.append(dense)

        if mode != "vector":
            t0 = time.perf_counter()
            sparse = await self.text_index.search(query, top_k=depth, filters=filters)
            bm25_ms = (time.perf_counter() - t0) * 1000.0
            ranked_lists.append(sparse)

        fused = self.rrf.fuse(ranked_lists)
        dense_ids = {s.chunk.id for s in dense}
        bm25_ids = {s.chunk.id for s in sparse}
        stats = LegStats(
            requested_depth=depth,
            dense_hits=len(dense),
            bm25_hits=len(sparse),
            dense_deficit=(depth - len(dense)) if mode != "bm25" else 0,
            bm25_deficit=(depth - len(sparse)) if mode != "vector" else 0,
            union_depth=len(dense_ids | bm25_ids),
            overlap=len(dense_ids & bm25_ids),
            fused_depth=len(fused),
            dense_ms=dense_ms,
            bm25_ms=bm25_ms,
        )
        return fused, stats


class CachedQueryEmbedder:
    """An ``Embedder`` serving pre-computed query vectors from a dict.

    Removes the shared embedding fleet from the variance budget (protocol §6.6):
    endpoint availability and batch composition can otherwise perturb results
    between cells. A miss is a hard error — silently re-embedding would defeat
    the point."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            if t not in self._vectors:
                raise KeyError(f"no cached query vector for {t[:60]!r}")
            out.append(self._vectors[t])
        return out


# --------------------------------------------------------------------------- #
# The grid
# --------------------------------------------------------------------------- #
def leg_depth_for(
    top_k: int, multiplier: int, rerank_enabled: bool, rerank_candidates: int
) -> int:
    """Per-leg candidate depth, exactly as production composes it.

    ``api/routers/query.py:199-204`` picks ``max(top_k, rerank_candidates)`` when
    reranking and ``top_k`` otherwise; ``retrieval/retriever.py:53`` then
    multiplies by ``candidate_multiplier``. Turning the reranker on therefore
    changes first-stage breadth by 10x as a *side effect* at the shipping
    defaults (5 -> 50) — the confound this function makes explicit and every
    manifest records as ``leg_depth``."""
    base = max(top_k, rerank_candidates) if rerank_enabled else top_k
    return base * multiplier


@dataclass(frozen=True)
class Cell:
    """One point of the sweep grid."""

    rung: str
    n_docs: int
    mode: str
    rrf_k: int | None
    top_k: int
    multiplier: int
    rerank_enabled: bool
    rerank_candidates: int | None

    @property
    def leg_depth(self) -> int:
        return leg_depth_for(
            self.top_k,
            self.multiplier,
            self.rerank_enabled,
            self.rerank_candidates or 0,
        )

    @property
    def cell_id(self) -> str:
        rrf = f"rrf{self.rrf_k}" if self.rrf_k is not None else "rrfna"
        rr = f"rr{self.rerank_candidates}" if self.rerank_enabled else "rr0"
        return (
            f"{self.rung}_{self.mode}_{rrf}_tk{self.top_k}"
            f"_m{self.multiplier}_{rr}"
        )

    @property
    def is_default(self) -> bool:
        return (
            self.mode == "hybrid"
            and self.rrf_k == DEFAULT_RRF_K
            and self.top_k == DEFAULT_TOP_K
            and self.multiplier == DEFAULT_MULTIPLIER
            and self.rerank_enabled is DEFAULT_RERANK_ENABLED
        )

    @property
    def is_primary_alt(self) -> bool:
        return (
            self.mode == "hybrid"
            and self.rrf_k == DEFAULT_RRF_K
            and self.top_k == DEFAULT_TOP_K
            and self.multiplier == PRIMARY_MULTIPLIER
            and self.rerank_enabled is DEFAULT_RERANK_ENABLED
        )

    def as_params(self) -> dict[str, Any]:
        return {
            "rung": self.rung,
            "n_docs": self.n_docs,
            "mode": self.mode,
            "rrf_k": self.rrf_k,
            "top_k": self.top_k,
            "candidate_multiplier": self.multiplier,
            "rerank_enabled": self.rerank_enabled,
            "rerank_candidates": self.rerank_candidates,
            "leg_depth": self.leg_depth,
            "use_graph": False,
            "rewrite_strategies": ["passthrough"],
        }


def build_grid(
    n_docs_list: list[int],
    modes: list[str],
    rrf_ks: list[int],
    top_ks: list[int],
    multipliers: list[int],
    rerank_flags: list[bool],
    rerank_candidates: list[int],
) -> list[Cell]:
    """Expand the factor lists into a de-duplicated cell list.

    Two collapses keep the grid honest rather than merely large: ``rrf_k`` is a
    no-op outside ``hybrid`` mode (one leg, nothing to fuse) so non-hybrid cells
    are emitted once with ``rrf_k=None``; and ``rerank_candidates`` is
    meaningless with the reranker off. The two pre-registered primary cells are
    force-added at every rung so the primary comparison always exists regardless
    of what the CLI asked for.
    """
    seen: dict[str, Cell] = {}

    def _add(cell: Cell) -> None:
        seen.setdefault(cell.cell_id, cell)

    for n in n_docs_list:
        rung = f"n{n}"
        for mode, top_k, mult, rr in product(modes, top_ks, multipliers, rerank_flags):
            ks = rrf_ks if mode == "hybrid" else [None]
            cs = rerank_candidates if rr else [None]
            for rrf_k, cand in product(ks, cs):
                _add(Cell(rung, n, mode, rrf_k, top_k, mult, rr, cand))
        # Pre-registered primary pair — always present.
        for mult in (DEFAULT_MULTIPLIER, PRIMARY_MULTIPLIER):
            _add(
                Cell(rung, n, "hybrid", DEFAULT_RRF_K, DEFAULT_TOP_K, mult,
                     DEFAULT_RERANK_ENABLED, None)
            )
    return sorted(seen.values(), key=lambda c: (c.n_docs, c.cell_id))


# --------------------------------------------------------------------------- #
# Metrics for one query (all read from a single stored ranking)
# --------------------------------------------------------------------------- #
def _ranked_docs(chunk_doc_ids: list[str]) -> list[str]:
    """Collapse a ranked chunk list to unique doc_ids, best-rank-first."""
    return list(dict.fromkeys(chunk_doc_ids))


def query_metrics(
    ranked_chunk_ids: list[str],
    ranked_chunk_doc_ids: list[str],
    doc_rels: dict[str, int],
    chunk_rels: dict[str, int],
) -> dict[str, float]:
    """Every reported metric for one query, from one stored ranking.

    ``doc_rels`` is the BEIR doc-level qrel row; ``chunk_rels`` is its chunk-level
    expansion (every chunk of a relevant doc inherits the doc's grade), which is
    what makes the chunk-level ideal DCG correct — a relevant document with 6
    chunks in the index can legitimately fill 6 of the top ranks, and an IDCG
    built from the doc-level row alone would cap nDCG below 1.0 for a perfect
    ranking. Both go through ``_stats.ndcg_at_k`` unchanged.
    """
    docs = _ranked_docs(ranked_chunk_doc_ids)
    relevant_docs = {d for d, g in doc_rels.items() if g > 0}
    out: dict[str, float] = {
        f"ndcg@{NDCG_DOC_K}": _stats.ndcg_at_k(docs, doc_rels, NDCG_DOC_K),
        f"ndcg@{NDCG_CHUNK_K}_chunk": _stats.ndcg_at_k(
            ranked_chunk_ids, chunk_rels, NDCG_CHUNK_K
        ),
        "map": _stats.average_precision(docs, doc_rels),
    }
    for k in RECALL_KS:
        out[f"recall@{k}"] = _stats.recall_at_k(docs, doc_rels, k)
    first = next((i + 1 for i, d in enumerate(docs) if d in relevant_docs), None)
    out["mrr@10"] = _stats.reciprocal_rank(first, cap=10)
    for k in CURVE_KS:
        out[f"ndcg_doc@{k}"] = _stats.ndcg_at_k(docs, doc_rels, k)
        head = ranked_chunk_doc_ids[:k]
        out[f"ctxprec@{k}"] = (
            sum(1 for d in head if d in relevant_docs) / k if k else 0.0
        )
        out[f"unique_docs@{k}"] = float(len(set(head)))
    return out


METRIC_NAMES: tuple[str, ...] = (
    (f"ndcg@{NDCG_DOC_K}", f"ndcg@{NDCG_CHUNK_K}_chunk", "map", "mrr@10")
    + tuple(f"recall@{k}" for k in RECALL_KS)
    + tuple(f"ndcg_doc@{k}" for k in CURVE_KS)
    + tuple(f"ctxprec@{k}" for k in CURVE_KS)
    + tuple(f"unique_docs@{k}" for k in CURVE_KS)
)

PRIMARY_METRIC = f"ndcg@{NDCG_DOC_K}"
CO_PRIMARY_METRIC = f"ndcg@{NDCG_CHUNK_K}_chunk"


def sanity_verdict(
    counters: list[dict[str, Any]], leg_depth: int, dense_matchable: int
) -> dict[str, Any]:
    """The protocol §5.4 pass/void assertion for one cell, from its counters.

    Two rates that must never be conflated:

    * ``*_starved_rate`` — the leg returned fewer than ``D`` for **any** reason,
      including "the index has no more matching chunks". This is the H1b
      measurement (is BM25 really depth-starved at library scale?) and it is
      **not** a failure.
    * ``*_deficit_rate`` — the leg returned fewer than ``min(D, matchable)``,
      i.e. fewer than it could have. That is a truncation bug; above
      ``MAX_DEFICIT_RATE`` the cell is **void**, because every quality number in
      it then describes a degraded pipeline rather than the parameters under
      test. Mirrors the G2 criterion in ``scripts/bench_filter_truncation.py``.
    """
    n = max(1, len(counters))
    dense_rate = sum(1 for c in counters if c["dense_unreturned"] > 0) / n
    bm25_rate = sum(1 for c in counters if c["bm25_unreturned"] > 0) / n
    return {
        "leg_depth": leg_depth,
        "dense_matchable": dense_matchable,
        "dense_deficit_rate": dense_rate,
        "bm25_deficit_rate": bm25_rate,
        "dense_starved_rate": sum(1 for c in counters if c["dense_deficit"] > 0) / n,
        "bm25_starved_rate": sum(1 for c in counters if c["bm25_deficit"] > 0) / n,
        "assertion": "hits == min(D, matchable) per leg",
        "verdict": (
            "PASS"
            if dense_rate <= MAX_DEFICIT_RATE and bm25_rate <= MAX_DEFICIT_RATE
            else "INVALID (hit deficit)"
        ),
    }


# --------------------------------------------------------------------------- #
# Index construction
# --------------------------------------------------------------------------- #
@dataclass
class LibraryIndex:
    """A built ``g1_lib_<n>docs_<spechash>`` Qdrant collection + ES index."""

    sample: LibrarySample
    collection: str
    es_index: str
    spec_hash: str
    n_chunks: int
    chunks_per_doc: float
    doc_to_chunk_ids: dict[str, list[str]]
    manifest: provenance.CollectionManifest
    build_s: float
    distractors: dict[str, Any] = field(default_factory=dict)
    # Ground truth for the §5.4 hit assertion: how many chunks each leg *could*
    # return. Dense is filter-scoped only (HNSW ranks the whole visible set), so
    # it is one number; BM25 is additionally term-scoped, so it is per query.
    dense_matchable: int = 0
    bm25_matchable: dict[str, int] = field(default_factory=dict)


def library_collection_name(n_docs: int, spec_hash: str) -> str:
    return guard_scratch(f"g1_lib_{n_docs}docs_{spec_hash}")


def build_spec() -> tuple[str, str, str]:
    """``(chunk_descriptor, spec_hash, chunk_method)`` for the ragstack_lib_v1 spec."""
    cfg = c7.CONFIG_BY_KEY[CHUNK_CONFIG_KEY]
    desc = provenance.chunk_descriptor(CHUNK_CONFIG_KEY, cfg.size, cfg.overlap, None)
    return desc, provenance.spec_hash(c7.SFR_MODEL, c7.VECTOR_SIZE, desc), cfg.kind


async def build_library_index(
    sample: LibrarySample,
    docs_by_id: dict[str, Any],
    client: httpx.AsyncClient,
    *,
    extra_chunks: list[Chunk] | None = None,
    distractor_meta: dict[str, Any] | None = None,
) -> LibraryIndex:
    """Chunk + embed + ingest one library into guarded ``g1_*`` stores.

    ``extra_chunks`` is the **distractor-ladder seam**: pre-embedded chunks (the
    future read-only scroll of production ``ragstack_sfr_tok512``) are written to
    Qdrant *and* ES alongside the judged core. Writing both is not optional —
    BM25 document-frequency and ``avgdl`` statistics are the whole reason the
    ladder exists for the sparse leg, and a Qdrant-only ladder would hold the
    BM25 index at rung 0 while the dense index grew, manufacturing a spurious
    hybrid-vs-dense interaction.
    """
    cfg = c7.CONFIG_BY_KEY[CHUNK_CONFIG_KEY]
    docs = [docs_by_id[d] for d in sample.doc_ids]
    t0 = time.perf_counter()
    chunks = c7.chunk_docs_for_config(cfg, docs)
    chunks, n_capped = c7.cap_oversized(chunks)
    desc, spec_hash, _ = build_spec()
    collection = library_collection_name(sample.n_docs, spec_hash)
    es_index = guard_scratch(collection)

    print(
        f"[{sample.rung}] {len(docs)} docs -> {len(chunks)} chunks "
        f"({len(chunks)/max(1,len(docs)):.2f}/doc, {n_capped} over cap) "
        f"-> {collection}",
        flush=True,
    )

    vectors = await c7.embed_texts_async(client, [c.content for c in chunks])
    if len(vectors) != len(chunks):
        raise RuntimeError(f"embed count {len(vectors)} != chunk count {len(chunks)}")
    for c, v in zip(chunks, vectors, strict=True):
        c.embedding = v

    all_chunks = list(chunks) + list(extra_chunks or [])
    vstore = QdrantVectorStore(
        url=c7.QDRANT_URL, collection=collection,
        vector_size=c7.VECTOR_SIZE, timeout=120,
    )
    tindex = ElasticsearchTextIndex(url=c7.ES_URL, index=es_index)
    await vstore.ensure_collection()
    await tindex.ensure_index()
    for start in range(0, len(all_chunks), 256):
        batch = all_chunks[start : start + 256]
        await vstore.upsert(batch)
        await tindex.index(batch)
    await tindex.close()

    doc_to_chunk_ids: dict[str, list[str]] = {}
    for c in all_chunks:
        doc_to_chunk_ids.setdefault(c.doc_id, []).append(c.id)

    manifest = provenance.make_ingest_manifest(
        collection=collection,
        model=c7.SFR_MODEL,
        dim=c7.VECTOR_SIZE,
        embedding_api="openai",
        embedding_endpoints=list(c7.SFR_ENDPOINTS),
        chunk_method=CHUNK_CONFIG_KEY,
        chunk_size=cfg.size,
        chunk_overlap=cfg.overlap,
        chunk_params=None,
        corpus=f"{DATASET_NAME}:{sample.n_docs}docs:seed{sample.seed}",
        chunk_count=len(all_chunks),
        ragstack_version=provenance.ragstack_version(),
        source="ingest",
    )
    if manifest.spec_hash != spec_hash:  # pragma: no cover - invariant
        raise RuntimeError("spec_hash drift between name and manifest")

    return LibraryIndex(
        sample=sample,
        collection=collection,
        es_index=es_index,
        spec_hash=spec_hash,
        n_chunks=len(all_chunks),
        chunks_per_doc=len(all_chunks) / max(1, len(docs)),
        doc_to_chunk_ids=doc_to_chunk_ids,
        manifest=manifest,
        build_s=time.perf_counter() - t0,
        distractors=distractor_meta
        or {
            "source_collection": None,
            "source_spec_hash": None,
            "spec_hash_match": None,
            "n_chunks": 0,
            "sample_seed": sample.seed,
            "point_id_digest": None,
        },
    )


async def measure_matchable(
    index: LibraryIndex, queries: dict[str, str], client: httpx.AsyncClient
) -> None:
    """Fill in each leg's *matchable* set size — the denominator of the §5.4
    assertion — with one probe per index, reused by every cell.

    Getting this right is the whole point of the assertion. The two legs have
    genuinely different ceilings and conflating them manufactures false
    failures:

    * **Dense** — HNSW ranks every point the filter admits, so the ceiling is the
      filtered point count. ``QdrantVectorStore.search`` passes ``limit`` with no
      ``search_params`` (no ``hnsw_ef``, no ``exact``), so any shortfall here is
      an approximate-search artefact and **voids the cell** — the G2 failure mode.
    * **BM25** — ``ElasticsearchTextIndex.search`` is an exact ``size=D`` query
      over ``{"match": {"content": query}}``, i.e. chunks sharing at least one
      analyzed term. Its ceiling is per query and is *not* the index size. Asking
      ES for the same query's ``_count`` turns "BM25 returned 4 hits" from an
      alarming truncation into the measurable fact H1b is about: whether only 4
      chunks in the library contain any query term at all.
    """
    from ragstack.stores.elasticsearch import _build_query
    from ragstack.tenancy import readable_tenants

    filters = scope_filters({}, TENANT)
    vstore = QdrantVectorStore(
        url=c7.QDRANT_URL, collection=index.collection,
        vector_size=c7.VECTOR_SIZE, timeout=120,
    )
    try:
        index.dense_matchable = await vstore.count_tenants(readable_tenants(TENANT))
    except Exception as exc:  # noqa: BLE001 - fall back to the built chunk count
        print(f"[sanity] dense count fell back to n_chunks: {exc}", flush=True)
        index.dense_matchable = index.n_chunks

    async def _count(qid: str) -> tuple[str, int]:
        r = await client.post(
            f"{c7.ES_URL}/{index.es_index}/_count",
            json={"query": _build_query(queries[qid], filters)},
            timeout=60.0,
        )
        return qid, int(r.json().get("count", 0))

    pairs = await asyncio.gather(*[_count(q) for q in index.sample.query_ids])
    index.bm25_matchable = dict(pairs)
    lo = min(index.bm25_matchable.values(), default=0)
    hi = max(index.bm25_matchable.values(), default=0)
    print(
        f"[sanity] {index.sample.rung}: dense_matchable={index.dense_matchable} "
        f"bm25_matchable min={lo} max={hi} "
        f"median={statistics.median(index.bm25_matchable.values() or [0]):.0f}",
        flush=True,
    )


async def qdrant_index_info(collection: str) -> dict[str, Any]:
    """HNSW / segment telemetry for the manifest (read-only)."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{c7.QDRANT_URL}/collections/{collection}")
            res = r.json().get("result", {})
        hnsw = (res.get("config", {}).get("hnsw_config") or {})
        optim = (res.get("config", {}).get("optimizer_config") or {})
        points = res.get("points_count") or 0
        indexed = res.get("indexed_vectors_count") or 0
        return {
            "m": hnsw.get("m"),
            "ef_construct": hnsw.get("ef_construct"),
            "search_ef": None,  # QdrantVectorStore.search passes no search_params
            "full_scan_threshold": hnsw.get("full_scan_threshold"),
            "max_segment_size": optim.get("max_segment_size"),
            "indexing_threshold": optim.get("indexing_threshold"),
            "on_disk_payload": res.get("config", {}).get("params", {}).get(
                "on_disk_payload"
            ),
            "points": points,
            "indexed_vectors": indexed,
            "segments_count": res.get("segments_count"),
            "hnsw_coverage": (indexed / points) if points else None,
            # Qdrant does not build an HNSW graph below `indexing_threshold`
            # vectors — a small collection is searched by exact brute force. That
            # is fine for correctness but it means the approximate-search
            # under-return hypothesis is untestable at that scale, and a result
            # from an unbuilt index must not be read as an HNSW result. Same
            # concern as the `hnsw_built` coverage banner in
            # scripts/bench_filter_truncation.py.
            "hnsw_built": bool(points) and indexed > 0,
            "status": res.get("status"),
        }
    except Exception as exc:  # noqa: BLE001 - telemetry must never fail a run
        return {"error": f"{type(exc).__name__}: {exc}"}


async def es_index_info(index: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            root = (await c.get(c7.ES_URL)).json()
            st = (await c.get(f"{c7.ES_URL}/{index}/_stats")).json()
            settings = (await c.get(f"{c7.ES_URL}/{index}/_settings")).json()
        total = st.get("_all", {}).get("primaries", {})
        idx_settings = next(iter(settings.values()), {}).get("settings", {})
        return {
            "version": root.get("version", {}).get("number"),
            "index": index,
            "similarity": "BM25",
            "analyzer": "standard",
            "n_docs": total.get("docs", {}).get("count"),
            "store_bytes": total.get("store", {}).get("size_in_bytes"),
            "number_of_shards": idx_settings.get("index", {}).get("number_of_shards"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
# Teardown — guarded
# --------------------------------------------------------------------------- #
async def teardown(client: httpx.AsyncClient, collections: list[str]) -> bool:
    """Drop the g1_* stores this run created; verify none remain."""
    if not collections:
        return True
    print(f"\n[teardown] dropping {len(collections)} g1_* store(s) ...", flush=True)
    from qdrant_client import AsyncQdrantClient

    qc = AsyncQdrantClient(url=c7.QDRANT_URL, timeout=120)
    for name in collections:
        guard_scratch(name)
        try:
            await qc.delete_collection(collection_name=name)
            print(f"[teardown] dropped Qdrant collection {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[teardown] Qdrant {name}: {exc}")
    remaining_q: list[str] = []
    try:
        cols = await qc.get_collections()
        remaining_q = [c.name for c in cols.collections if c.name in collections]
    except Exception:  # noqa: BLE001
        pass
    await qc.close()
    for name in collections:
        guard_scratch(name)
        try:
            r = await client.delete(f"{c7.ES_URL}/{name}", timeout=60.0)
            print(f"[teardown] dropped ES index {name} (HTTP {r.status_code})")
        except Exception as exc:  # noqa: BLE001
            print(f"[teardown] ES {name}: {exc}")
    remaining_es: list[str] = []
    try:
        r = await client.get(f"{c7.ES_URL}/_cat/indices/g1_*?h=index", timeout=30.0)
        remaining_es = [ln for ln in r.text.split() if ln.strip() in collections]
    except Exception:  # noqa: BLE001
        pass
    gone = not remaining_q and not remaining_es
    print(
        "[teardown] verified: no g1_* stores from this run remain."
        if gone
        else f"[teardown] WARNING leftover Qdrant={remaining_q} ES={remaining_es}",
        flush=True,
    )
    return gone


# --------------------------------------------------------------------------- #
# Query-vector cache (protocol §6.6)
# --------------------------------------------------------------------------- #
async def load_query_vectors(
    queries: dict[str, str], client: httpx.AsyncClient, cache_dir: Path, spec_hash: str
) -> dict[str, list[float]]:
    """Embed every query text once and memoize on disk, keyed by (spec, query set)."""
    texts = [queries[q] for q in sorted(queries)]
    key = _digest(spec_hash, *texts)[:16]
    path = cache_dir / f"{DATASET_NAME}.{spec_hash}.{key}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if len(cached) == len(texts):
            print(f"[qvec] {len(cached)} query vectors from cache {path.name}",
                  flush=True)
            return cached
    print(f"[qvec] embedding {len(texts)} queries once ...", flush=True)
    vecs = await c7.embed_texts_async(client, texts)
    out = dict(zip(texts, vecs, strict=True))
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# Cell evaluation
# --------------------------------------------------------------------------- #
async def evaluate_cell(
    cell: Cell,
    index: LibraryIndex,
    queries: dict[str, str],
    qrels: dict[str, dict[str, int]],
    qvectors: dict[str, list[float]],
    reranker: SidecarReranker,
    rerank_cache: dict[tuple[str, str], float],
    *,
    concurrency: int = 8,
) -> dict[str, Any]:
    """Run one grid cell over every retained query. Returns per-query arrays,
    Track-C counters and the §5.4 sanity verdict."""
    qids = list(index.sample.query_ids)
    vstore = QdrantVectorStore(
        url=c7.QDRANT_URL, collection=index.collection,
        vector_size=c7.VECTOR_SIZE, timeout=120,
    )
    tindex = ElasticsearchTextIndex(url=c7.ES_URL, index=index.es_index)
    retriever = InstrumentedHybridRetriever(
        vstore,
        tindex,
        CachedQueryEmbedder(qvectors),
        rrf_scorer=RRFScorer(k=cell.rrf_k if cell.rrf_k is not None else DEFAULT_RRF_K),
        candidate_multiplier=cell.multiplier,
    )
    filters = scope_filters({}, TENANT)
    sem = asyncio.Semaphore(concurrency)

    # top_k passed to the retriever is the *depth driver*, exactly as production
    # composes it (query.py:199-204 + retriever.py:53). The reported cutoffs are
    # read back from the stored ranking (§6.3a).
    depth_driver = (
        max(cell.top_k, cell.rerank_candidates or 0)
        if cell.rerank_enabled
        else cell.top_k
    )

    async def _one(qid: str) -> dict[str, Any]:
        text = queries[qid]
        async with sem:
            t0 = time.perf_counter()
            fused, legs = await retriever.retrieve_instrumented(
                text, top_k=depth_driver, filters=filters, mode=cell.mode,
                query_vector=qvectors.get(text),
            )
            occupancy = None
            if cell.rerank_enabled and fused:
                cand = cell.rerank_candidates or len(fused)
                head, tail = fused[:cand], fused[cand:]
                occupancy = min(cand, len(fused)) / cand if cand else None
                missing = [
                    h.chunk for h in head if (qid, h.chunk.id) not in rerank_cache
                ]
                if missing:
                    scored = await reranker.score(text, missing)
                    for sc in scored:
                        rerank_cache[(qid, sc.chunk.id)] = sc.score
                head = sorted(
                    head,
                    key=lambda h: rerank_cache.get((qid, h.chunk.id), float("-inf")),
                    reverse=True,
                )
                ordered = head + tail
            else:
                ordered = fused
            latency_ms = (time.perf_counter() - t0) * 1000.0

        ranking = ordered[:RANKING_DEPTH]
        chunk_ids = [s.chunk.id for s in ranking]
        chunk_docs = [s.chunk.doc_id for s in ranking]
        doc_rels = qrels[qid]
        chunk_rels = {
            cid: g
            for d, g in doc_rels.items()
            if g > 0
            for cid in index.doc_to_chunk_ids.get(d, [])
        }
        row = legs.as_row()
        # LegStats carries the deficit vs the *requested* depth. The §5.4 verdict
        # needs the deficit vs what the leg could actually have returned — its
        # matchable ceiling (see measure_matchable) — so both are recorded and
        # never conflated: `*_deficit` is the H1b measurement, `*_unreturned` is
        # the bug.
        dense_expect = min(legs.requested_depth, index.dense_matchable)
        bm25_expect = min(
            legs.requested_depth, index.bm25_matchable.get(qid, index.n_chunks)
        )
        row["dense_matchable"] = index.dense_matchable
        row["bm25_matchable"] = index.bm25_matchable.get(qid)
        row["dense_expected"] = dense_expect if cell.mode != "bm25" else 0
        row["bm25_expected"] = bm25_expect if cell.mode != "vector" else 0
        row["dense_unreturned"] = (
            max(0, dense_expect - legs.dense_hits) if cell.mode != "bm25" else 0
        )
        row["bm25_unreturned"] = (
            max(0, bm25_expect - legs.bm25_hits) if cell.mode != "vector" else 0
        )
        row["rerank_pool_occupancy"] = occupancy
        row["latency_ms"] = latency_ms
        row["query_id"] = qid
        return {
            "qid": qid,
            "metrics": query_metrics(chunk_ids, chunk_docs, doc_rels, chunk_rels),
            "counters": row,
            "ranking": chunk_ids,
        }

    results = await asyncio.gather(*[_one(q) for q in qids])
    await tindex.close()

    per_query = {m: [r["metrics"][m] for r in results] for m in METRIC_NAMES}
    means = {m: (sum(v) / len(v) if v else 0.0) for m, v in per_query.items()}
    counters = [r["counters"] for r in results]

    sanity = sanity_verdict(counters, cell.leg_depth, index.dense_matchable)
    lat = sorted(c["latency_ms"] for c in counters) or [0.0]
    return {
        "cell_id": cell.cell_id,
        "params": cell.as_params(),
        "n_queries": len(qids),
        "query_ids": qids,
        "per_query": per_query,
        "means": means,
        "counters": counters,
        "sanity": sanity,
        "cost": {
            "p50_query_ms": lat[len(lat) // 2],
            "p95_query_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))],
            "mean_dense_ms": statistics.fmean(c["dense_ms"] for c in counters)
            if counters else 0.0,
            "mean_bm25_ms": statistics.fmean(c["bm25_ms"] for c in counters)
            if counters else 0.0,
            "mean_union_depth": statistics.fmean(c["union_depth"] for c in counters)
            if counters else 0.0,
            "mean_overlap": statistics.fmean(c["overlap"] for c in counters)
            if counters else 0.0,
        },
    }


# --------------------------------------------------------------------------- #
# Provenance — the run manifest
# --------------------------------------------------------------------------- #
def _git_info() -> dict[str, Any]:
    def _run(*args: str) -> str:
        try:
            return subprocess.run(
                args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=10
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            return ""

    return {
        "commit": _run("git", "rev-parse", "HEAD"),
        "branch": _run("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(_run("git", "status", "--porcelain")),
    }


def _package_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    out: dict[str, str] = {}
    for pkg in (
        "qdrant-client", "elasticsearch", "httpx", "numpy", "pydantic",
        "transformers", "tokenizers", "datasets", "ragstack",
    ):
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = ""
    return out


def _sha256_file(path: Path) -> str | None:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def dataset_provenance(
    corpus_docs: list[Any],
    queries: dict[str, str],
    qrels: dict[str, dict[str, int]],
    source: str,
) -> dict[str, Any]:
    """Content digests of the corpus / queries / qrels actually loaded — not of a
    download URL, so a silently different cache is detectable."""
    corpus_h = _digest(*(f"{d.id}\x1f{d.content}" for d in sorted(corpus_docs,
                                                                 key=lambda d: d.id)))
    q_h = _digest(*(f"{q}\x1f{queries[q]}" for q in sorted(queries)))
    r_h = _digest(
        *(
            f"{q}\x1f" + ",".join(f"{d}={g}" for d, g in sorted(qrels[q].items()))
            for q in sorted(qrels)
        )
    )
    return {
        "name": DATASET_NAME,
        "source": source,
        "cache_dir": str(sfe.CACHE_DIR),
        "corpus_sha256": corpus_h,
        "queries_sha256": q_h,
        "qrels_sha256": r_h,
        "n_docs": len(corpus_docs),
        "n_queries": len(queries),
        "n_judgments": sum(len(v) for v in qrels.values()),
    }


def build_run_manifest(
    *,
    run_id: str,
    argv: list[str],
    args: argparse.Namespace,
    dataset: dict[str, Any],
    indexes: dict[str, dict[str, Any]],
    grid: list[Cell],
    started_at: str,
) -> dict[str, Any]:
    """The ``ragstack.eval_run/v1`` manifest (protocol §8.2).

    Embeds a ``CollectionManifest`` verbatim per library size and reuses
    ``ragstack.provenance``'s vocabulary (``chunk_descriptor`` / ``spec_hash`` /
    ``ragstack_version``) rather than inventing a parallel one — no eval path in
    this repository recorded provenance before this harness."""
    desc, spec_hash, _ = build_spec()
    return {
        "schema_version": "ragstack.eval_run/v1",
        "run_id": run_id,
        "protocol_version": _sha256_file(PROTOCOL_PATH),
        "protocol_path": str(PROTOCOL_PATH.relative_to(REPO_ROOT))
        if PROTOCOL_PATH.exists() else None,
        "started_at": started_at,
        "finished_at": None,
        "git": _git_info(),
        "ragstack_version": provenance.ragstack_version(),
        "dataset": dataset,
        "build_spec": {
            "chunk_descriptor": desc,
            "spec_hash": spec_hash,
            "chunk_config": CHUNK_CONFIG_KEY,
            "model": c7.SFR_MODEL,
            "dim": c7.VECTOR_SIZE,
            "hard_cap_tokens": c7.HARD_CAP_TOKENS,
        },
        "libraries": indexes,
        "grid": {
            "n_cells": len(grid),
            "cells": [c.cell_id for c in grid],
            "primary_comparison": {
                "metric": PRIMARY_METRIC,
                "reference": "shipping defaults "
                f"(hybrid, rrf_k={DEFAULT_RRF_K}, top_k={DEFAULT_TOP_K}, "
                f"multiplier={DEFAULT_MULTIPLIER}, rerank=off)",
                "candidate": f"same cell with candidate_multiplier={PRIMARY_MULTIPLIER}",
                "delta": DELTA,
                "preregistered": True,
            },
        },
        "runtime": {
            "host": platform.node(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "qdrant_url": c7.QDRANT_URL,
            "es_url": c7.ES_URL,
            "crossencoder_url": c7.RERANKER_URL,
            "embedding_endpoints_live": list(c7.SFR_ENDPOINTS),
            "embed_concurrency": c7.EMBED_CONCURRENCY,
            "eval_concurrency": args.concurrency,
            "packages": _package_versions(),
        },
        "seeds": {
            "sample": args.seed,
            "bootstrap": _stats.SEED,
            "bootstrap_iters": args.bootstrap_iters,
        },
        "argv": list(argv),
        "cwd": os.getcwd(),
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def stats_for_rung(
    cells: list[dict[str, Any]], iters: int
) -> tuple[str, str, dict[str, Any]]:
    """Paired bootstrap CIs + Holm-corrected Wilcoxon vs the shipping default.

    Cells voided by the §5.4 assertion are excluded — their quality numbers
    describe a degraded pipeline, so including them would launder a bug into a
    parameter effect."""
    valid = [c for c in cells if c["sanity"]["verdict"] == "PASS"]
    if not valid:
        return "", "**No valid cell at this rung** (all failed the §5.4 assertion).", {}
    ref = next(
        (c["cell_id"] for c in valid if Cell(**_cell_kwargs(c)).is_default),
        valid[0]["cell_id"],
    )
    by_id = {c["cell_id"]: c for c in valid}
    keys = sorted(by_id)
    metrics = {
        m: {k: by_id[k]["per_query"][m] for k in keys}
        for m in (PRIMARY_METRIC, CO_PRIMARY_METRIC, "recall@10", "map")
    }
    table, interp = _stats.build_stats_table(
        keys, ref, metrics, PRIMARY_METRIC, metrics[PRIMARY_METRIC], iters=iters
    )
    diffs = _stats.bootstrap_diff_ci(metrics[PRIMARY_METRIC], ref, iters=iters)
    raw_p = {
        k: _stats.wilcoxon_signed_rank(
            metrics[PRIMARY_METRIC][k], metrics[PRIMARY_METRIC][ref]
        )[1]
        for k in keys
        if k != ref
    }
    holm = _stats.holm_bonferroni(raw_p) if raw_p else {}
    summary = {
        "reference": ref,
        "n_valid_cells": len(valid),
        "n_void_cells": len(cells) - len(valid),
        "diff_ci": {k: asdict(v) for k, v in diffs.items()},
        "holm": {k: {"adj_p": v[0], "rejected": v[1]} for k, v in holm.items()},
    }
    return table, interp, summary


def _cell_kwargs(cell_result: dict[str, Any]) -> dict[str, Any]:
    p = cell_result["params"]
    return {
        "rung": p["rung"], "n_docs": p["n_docs"], "mode": p["mode"],
        "rrf_k": p["rrf_k"], "top_k": p["top_k"], "multiplier": p["candidate_multiplier"],
        "rerank_enabled": p["rerank_enabled"],
        "rerank_candidates": p["rerank_candidates"],
    }


def primary_comparison(
    cells: list[dict[str, Any]], iters: int
) -> dict[str, Any] | None:
    """The one pre-registered comparison, at the largest rung present."""
    largest = max((c["params"]["n_docs"] for c in cells), default=None)
    if largest is None:
        return None
    ref = alt = None
    for c in cells:
        if c["params"]["n_docs"] != largest:
            continue
        cell = Cell(**_cell_kwargs(c))
        if cell.is_default:
            ref = c
        elif cell.is_primary_alt:
            alt = c
    if ref is None or alt is None:
        return None
    pq = {
        "default": ref["per_query"][PRIMARY_METRIC],
        "depth100": alt["per_query"][PRIMARY_METRIC],
    }
    diff = _stats.bootstrap_diff_ci(pq, "default", iters=iters)["depth100"]
    _, p = _stats.wilcoxon_signed_rank(pq["depth100"], pq["default"])
    tost = _stats.bootstrap_diff_ci(pq, "default", iters=iters, alpha=0.10)["depth100"]
    if abs(diff.point) >= DELTA and p < 0.05:
        verdict = "DIFFERENT"
    elif tost.lo > -DELTA and tost.hi < DELTA:
        verdict = "EQUIVALENT"
    else:
        verdict = "INCONCLUSIVE"
    return {
        "n_docs": largest,
        "reference_cell": ref["cell_id"],
        "candidate_cell": alt["cell_id"],
        "metric": PRIMARY_METRIC,
        "n_queries": ref["n_queries"],
        "reference_mean": ref["means"][PRIMARY_METRIC],
        "candidate_mean": alt["means"][PRIMARY_METRIC],
        "diff_ci95": asdict(diff),
        "diff_ci90": asdict(tost),
        "wilcoxon_p": p,
        "delta": DELTA,
        "verdict": verdict,
        "valid": ref["sanity"]["verdict"] == "PASS"
        and alt["sanity"]["verdict"] == "PASS",
    }


def write_outputs(
    out_dir: Path,
    manifest: dict[str, Any],
    cell_results: list[dict[str, Any]],
    iters: int,
) -> dict[str, Any]:
    """Write manifest + per-cell artefacts + CSV + report into the run directory."""
    (out_dir / "cells").mkdir(parents=True, exist_ok=True)
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)
    for c in cell_results:
        # chunk_one-compatible payload so scripts/eval/aggregate_stats.py works.
        (out_dir / "cells" / f"{c['cell_id']}.json").write_text(
            json.dumps(
                {
                    "config": c["cell_id"],
                    "source": f"{DATASET_NAME}:{c['params']['rung']}",
                    "n_queries": c["n_queries"],
                    "query_ids": c["query_ids"],
                    "means": c["means"],
                    "per_query": c["per_query"],
                    "params": c["params"],
                    "sanity": c["sanity"],
                    "cost": c["cost"],
                },
                indent=2, sort_keys=True,
            ),
            encoding="utf-8",
        )
        with (out_dir / "raw" / f"{c['cell_id']}.counters.jsonl").open(
            "w", encoding="utf-8"
        ) as fh:
            for row in c["counters"]:
                fh.write(json.dumps(row, sort_keys=True) + "\n")

    primary = primary_comparison(cell_results, iters)
    rungs = sorted({c["params"]["rung"] for c in cell_results},
                   key=lambda r: int(r[1:]))
    sections, summaries = [], {}
    for rung in rungs:
        subset = [c for c in cell_results if c["params"]["rung"] == rung]
        table, interp, summary = stats_for_rung(subset, iters)
        summaries[rung] = summary
        sections.append(f"### Rung `{rung}`\n\n{table}\n{interp}\n")

    manifest["finished_at"] = datetime.now(UTC).isoformat()
    manifest["primary_comparison_result"] = primary
    manifest["stats"] = summaries
    manifest["sanity_by_cell"] = {c["cell_id"]: c["sanity"] for c in cell_results}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    with (out_dir / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["cell_id", "n_docs", "mode", "rrf_k", "top_k", "multiplier",
             "rerank_enabled", "rerank_candidates", "leg_depth", "verdict",
             "n_queries", PRIMARY_METRIC, CO_PRIMARY_METRIC, "recall@10",
             "recall@100", "map", "mrr@10", "mean_union_depth", "mean_overlap",
             "dense_starved_rate", "bm25_starved_rate",
             "dense_deficit_rate", "bm25_deficit_rate", "p50_ms", "p95_ms"]
        )
        for c in cell_results:
            p, m, s, cost = c["params"], c["means"], c["sanity"], c["cost"]
            w.writerow([
                c["cell_id"], p["n_docs"], p["mode"], p["rrf_k"], p["top_k"],
                p["candidate_multiplier"], p["rerank_enabled"],
                p["rerank_candidates"], p["leg_depth"], s["verdict"], c["n_queries"],
                round(m[PRIMARY_METRIC], 4), round(m[CO_PRIMARY_METRIC], 4),
                round(m["recall@10"], 4), round(m["recall@100"], 4),
                round(m["map"], 4), round(m["mrr@10"], 4),
                round(cost["mean_union_depth"], 2), round(cost["mean_overlap"], 2),
                round(s["dense_starved_rate"], 4), round(s["bm25_starved_rate"], 4),
                round(s["dense_deficit_rate"], 4), round(s["bm25_deficit_rate"], 4),
                round(cost["p50_query_ms"], 1), round(cost["p95_query_ms"], 1),
            ])

    (out_dir / "report.md").write_text(
        _report_body(manifest, cell_results, primary, sections), encoding="utf-8"
    )
    return primary or {}


def _scale_banner(libs: dict[str, Any]) -> str:
    """Two scale caveats that would otherwise silently misread the whole run.

    (1) SciFact abstracts chunk ~1.2:1, while a real ``fixed_tok512`` PDF library
    chunks ~36:1 (``reports/chunking-comparison-overview.md`` §6.1). A 200-*document*
    SciFact library is therefore a few hundred chunks, not the ~7k chunks a
    200-document real library is — protocol §1.3b, and the reason the distractor
    ladder exists. (2) Qdrant builds no HNSW graph below ``indexing_threshold``
    vectors, so a small library is searched by exact brute force and cannot
    exhibit the approximate-search under-return this harness instruments for.
    """
    lines = ["", "> **Scale caveats — read before quoting any number above.**", ">"]
    worst = min((v["chunks_per_doc"] for v in libs.values()), default=0.0)
    biggest = max((v["n_chunks"] for v in libs.values()), default=0)
    lines.append(
        f"> - Measured **{worst:.2f} chunks/doc** on this corpus. A real "
        f"`fixed_tok512` PDF library measures ~36 chunks/doc, so the largest "
        f"index here ({biggest} chunks) corresponds to roughly "
        f"**{biggest/36:.0f} real documents**, not {max((v['n_docs'] for v in libs.values()), default=0)}. "
        f"The document-count sweep is a *prior* on the parameters, not a "
        f"library-scale measurement; the distractor ladder (protocol §4.3) is "
        f"what makes the chunk count realistic."
    )
    unbuilt = [
        r for r, v in libs.items()
        if isinstance(v.get("hnsw"), dict) and v["hnsw"].get("hnsw_built") is False
    ]
    if unbuilt:
        lines.append(
            f"> - **HNSW was never built** at rung(s) {', '.join(sorted(unbuilt))} "
            f"(points below Qdrant's `indexing_threshold`), so the dense leg ran "
            f"as an exact brute-force scan. Every §5.4 dense verdict at those "
            f"rungs is therefore vacuous with respect to approximate-search "
            f"truncation — it confirms only that exact search is exact."
        )
    else:
        lines.append("> - HNSW was built at every rung; dense results are approximate.")
    return "\n".join(lines) + "\n"


def _report_body(manifest, cell_results, primary, sections) -> str:
    libs = manifest["libraries"]
    lib_rows = "".join(
        f"| `{r}` | {v['n_docs']} | {v['n_chunks']} | {v['chunks_per_doc']:.2f} | "
        f"{v['n_queries']} | {v['n_judged_docs']} | `{v['collection']}` |\n"
        for r, v in sorted(libs.items(), key=lambda kv: kv[1]["n_docs"])
    )
    mech_rows = ""
    for c in sorted(cell_results, key=lambda c: (c["params"]["n_docs"], c["cell_id"])):
        cnt = c["counters"]
        if not cnt:
            continue
        d = statistics.fmean(x["dense_hits"] for x in cnt)
        b = statistics.fmean(x["bm25_hits"] for x in cnt)
        bm = [x["bm25_matchable"] for x in cnt if x["bm25_matchable"] is not None]
        mech_rows += (
            f"| `{c['cell_id']}` | {c['params']['leg_depth']} | {d:.1f} | {b:.1f} | "
            f"{statistics.fmean(bm):.1f} | " if bm else
            f"| `{c['cell_id']}` | {c['params']['leg_depth']} | {d:.1f} | {b:.1f} | – | "
        )
        mech_rows += (
            f"{c['cost']['mean_union_depth']:.1f} | {c['cost']['mean_overlap']:.1f} | "
            f"{c['sanity']['dense_starved_rate']:.3f} | "
            f"{c['sanity']['bm25_starved_rate']:.3f} | "
            f"{c['sanity']['dense_deficit_rate']:.3f} | "
            f"{c['sanity']['bm25_deficit_rate']:.3f} | {c['sanity']['verdict']} |\n"
        )
    if primary:
        pc = (
            f"- reference (`{primary['reference_cell']}`): "
            f"{primary['reference_mean']:.4f}\n"
            f"- candidate (`{primary['candidate_cell']}`): "
            f"{primary['candidate_mean']:.4f}\n"
            f"- Δ{PRIMARY_METRIC} = {primary['diff_ci95']['point']:+.4f} "
            f"[{primary['diff_ci95']['lo']:+.4f}, {primary['diff_ci95']['hi']:+.4f}] "
            f"(95% paired bootstrap, n={primary['n_queries']})\n"
            f"- Wilcoxon p = {primary['wilcoxon_p']:.4f}, δ = {DELTA}\n"
            f"- **verdict: {primary['verdict']}**"
            f"{'' if primary['valid'] else '  — VOID, a cell failed the §5.4 assertion'}\n"
        )
    else:
        pc = "- not computable in this run (a primary cell is missing or void).\n"
    return f"""# G1 — library retrieval parameter sweep

Run `{manifest['run_id']}` · git `{manifest['git']['commit'][:12]}`
(`{manifest['git']['branch']}`{', dirty' if manifest['git']['dirty'] else ''}) ·
started {manifest['started_at']} · finished {manifest['finished_at']}

Protocol `{manifest.get('protocol_path')}` @ `{manifest.get('protocol_version')}`.
Dataset **{manifest['dataset']['source']}**, corpus
`{manifest['dataset']['corpus_sha256'][:16]}…`, qrels
`{manifest['dataset']['qrels_sha256'][:16]}…`.
Build spec `{manifest['build_spec']['chunk_config']}` /
`{manifest['build_spec']['model']}` @ {manifest['build_spec']['dim']}-d,
spec_hash `{manifest['build_spec']['spec_hash']}`.

## Pre-registered primary comparison

Shipping defaults vs. the identical cell at
`candidate_multiplier={PRIMARY_MULTIPLIER}` (per-leg depth 100), document-level
{PRIMARY_METRIC}, largest library size in the run.

{pc}
Every other cell below is an **exploratory screen**, Holm-corrected across the
rung's grid. No cell other than the one above may be used to change a shipping
default without a confirmatory run on a held-out query split.

## Libraries built

| rung | docs | chunks | chunks/doc | queries | judged docs | collection |
|---|---|---|---|---|---|---|
{lib_rows}
{_scale_banner(libs)}
## Mechanism (Track C) — per-leg accounting

Registered prediction (protocol §1.3c / H1b): the spec's "BM25 returns 3-4 hits
against dense's 20" premise is backwards. `ElasticsearchTextIndex.search` is an
exact `size=D` query returning `min(D, |chunks sharing a term|)`;
`QdrantVectorStore.search` passes `limit` with **no `search_params`** (no
`hnsw_ef`, no `exact`), so the *dense* leg is the one that can silently
under-return.

Two different quantities, kept apart on purpose. **starved** = the leg returned
fewer than `D` for any reason, including "the index simply has no more matching
chunks" — that is the H1b measurement and it is not a bug. **deficit** = the leg
returned fewer than `min(D, matchable)`, i.e. fewer than it could have — that
*is* a bug and it voids the cell.

| cell | D | mean dense_hits | mean bm25_hits | mean bm25_matchable | union | overlap | dense starved | bm25 starved | dense deficit | bm25 deficit | §5.4 |
|---|---|---|---|---|---|---|---|---|---|---|---|
{mech_rows}
## Retrieval quality by rung

{''.join(sections)}
## Reproducing

Full `argv`, seeds, package versions, HNSW/ES index telemetry and per-library
`CollectionManifest`s are in `manifest.json`. Per-cell per-query arrays are in
`cells/<cell_id>.json` (`chunk_one`-compatible, so
`scripts/eval/aggregate_stats.py` reads them); per-query Track-C counters are in
`raw/<cell_id>.counters.jsonl`.
"""


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def _run_dir(base: Path, run_id: str) -> Path:
    """A fresh per-run directory. Never overwrites: the existing harnesses clobber
    a fixed REPORT_PATH/CSV_PATH, which is the reproducibility gap this closes."""
    path = base / run_id
    n = 1
    while path.exists():
        path = base / f"{run_id}-{n}"
        n += 1
    path.mkdir(parents=True)
    return path


async def amain(args: argparse.Namespace, argv: list[str]) -> int:
    started_at = datetime.now(UTC).isoformat()
    corpus_docs, queries_raw, qrels, source = sfe.load_scifact()
    queries = {q: v[0] for q, v in queries_raw.items()}
    # A qrel row without query text is unusable; a query without qrels is unscorable.
    qrels = {q: v for q, v in qrels.items() if q in queries and v}
    queries = {q: t for q, t in queries.items() if q in qrels}
    docs_by_id = {d.id: d for d in corpus_docs}
    print(
        f"[data] source={source} corpus={len(corpus_docs)} queries={len(queries)} "
        f"judgments={sum(len(v) for v in qrels.values())}",
        flush=True,
    )

    samples = [
        sample_library(
            list(docs_by_id), qrels, n,
            seed=args.seed, judged_fraction=args.judged_fraction,
        )
        for n in args.doc_counts
    ]
    if args.query_limit:
        samples = [
            LibrarySample(
                **{**asdict(s), "query_ids": s.query_ids[: args.query_limit]}
            )
            for s in samples
        ]
    for s in samples:
        print(
            f"[sample] {s.rung}: {s.n_docs} docs "
            f"({len(s.judged_doc_ids)} judged + {len(s.distractor_doc_ids)} "
            f"distractor), {len(s.query_ids)} queries retained / "
            f"{s.n_queries_available} available, digest {s.digest[:12]}",
            flush=True,
        )

    grid = build_grid(
        args.doc_counts, args.modes, args.rrf_k, args.top_k, args.multipliers,
        args.rerank, args.rerank_candidates,
    )
    print(f"[grid] {len(grid)} cells over {len(samples)} librar(ies)", flush=True)

    run_id = f"g1-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir = _run_dir(args.out_root, run_id)
    print(f"[out] {out_dir}", flush=True)

    created: list[str] = []
    timeout = httpx.Timeout(300.0, connect=30.0)
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    try:
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
            reranker = SidecarReranker(c7.RERANKER_URL, http=client)
            indexes: dict[str, LibraryIndex] = {}
            for s in samples:
                idx = await build_library_index(s, docs_by_id, client)
                created.append(idx.collection)
                indexes[s.rung] = idx

            _, spec_hash, _ = build_spec()
            wanted = {queries[q] for s in samples for q in s.query_ids}
            qvectors = await load_query_vectors(
                {q: queries[q] for s in samples for q in s.query_ids},
                client, args.cache_dir, spec_hash,
            )
            missing = wanted - set(qvectors)
            if missing:
                raise SystemExit(f"query-vector cache missing {len(missing)} entries")

            lib_manifest: dict[str, dict[str, Any]] = {}
            for rung, idx in indexes.items():
                await measure_matchable(idx, queries, client)
                lib_manifest[rung] = {
                    "n_docs": idx.sample.n_docs,
                    "n_chunks": idx.n_chunks,
                    "chunks_per_doc": idx.chunks_per_doc,
                    "n_queries": len(idx.sample.query_ids),
                    "n_judged_docs": len(idx.sample.judged_doc_ids),
                    "n_queries_dropped": idx.sample.n_queries_dropped,
                    "dense_matchable": idx.dense_matchable,
                    "bm25_matchable": idx.bm25_matchable,
                    "collection": idx.collection,
                    "es_index": idx.es_index,
                    "sample": asdict(idx.sample),
                    "manifest": idx.manifest.model_dump(),
                    "hnsw": await qdrant_index_info(idx.collection),
                    "es": await es_index_info(idx.es_index),
                    "distractors": idx.distractors,
                    "build_s": idx.build_s,
                }

            manifest = build_run_manifest(
                run_id=run_id, argv=argv, args=args,
                dataset=dataset_provenance(corpus_docs, queries, qrels, source),
                indexes=lib_manifest, grid=grid, started_at=started_at,
            )

            rerank_cache: dict[tuple[str, str], float] = {}
            cell_results: list[dict[str, Any]] = []
            for i, cell in enumerate(grid, 1):
                idx = indexes[cell.rung]
                t0 = time.perf_counter()
                res = await evaluate_cell(
                    cell, idx, queries, qrels, qvectors, reranker, rerank_cache,
                    concurrency=args.concurrency,
                )
                cell_results.append(res)
                print(
                    f"[cell {i}/{len(grid)}] {cell.cell_id} D={cell.leg_depth} "
                    f"{PRIMARY_METRIC}={res['means'][PRIMARY_METRIC]:.4f} "
                    f"{CO_PRIMARY_METRIC}={res['means'][CO_PRIMARY_METRIC]:.4f} "
                    f"union={res['cost']['mean_union_depth']:.1f} "
                    f"{res['sanity']['verdict']} ({time.perf_counter()-t0:.1f}s)",
                    flush=True,
                )

            primary = write_outputs(
                out_dir, manifest, cell_results, args.bootstrap_iters
            )
            print("\n" + "=" * 78)
            print(f"G1 sweep complete — {len(cell_results)} cells → {out_dir}")
            if primary:
                d = primary["diff_ci95"]
                print(
                    f"PRE-REGISTERED PRIMARY ({primary['n_docs']} docs, "
                    f"{PRIMARY_METRIC}): {d['point']:+.4f} "
                    f"[{d['lo']:+.4f}, {d['hi']:+.4f}] "
                    f"p={primary['wilcoxon_p']:.4f} → {primary['verdict']}"
                )
            print("=" * 78)
    finally:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if args.keep:
                print(f"\n[teardown] skipped (--keep): {created}")
            else:
                await teardown(client, created)
    return 0


def _int_list(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


def _bool_list(s: str) -> list[bool]:
    out = []
    for tok in s.split(","):
        tok = tok.strip().lower()
        if tok in ("off", "false", "0", "no"):
            out.append(False)
        elif tok in ("on", "true", "1", "yes"):
            out.append(True)
        elif tok:
            raise argparse.ArgumentTypeError(f"expected on/off, got {tok!r}")
    return out or [False]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--doc-counts", type=_int_list, default=[50, 100, 200],
                   help="library sizes in DOCUMENTS (default 50,100,200)")
    p.add_argument("--modes", default="hybrid,vector,bm25",
                   help="retrieval modes: hybrid,vector,bm25")
    p.add_argument("--rrf-k", type=_int_list, default=[DEFAULT_RRF_K],
                   help="RRF constants to sweep (hybrid only)")
    p.add_argument("--top-k", type=_int_list, default=[DEFAULT_TOP_K],
                   help="top_k values (drives per-leg depth, see leg_depth_for)")
    p.add_argument("--multipliers", type=_int_list,
                   default=[DEFAULT_MULTIPLIER, PRIMARY_MULTIPLIER],
                   help="retrieval_candidate_multiplier values")
    p.add_argument("--rerank", type=_bool_list, default=[False],
                   help="rerank_enabled values: off,on")
    p.add_argument("--rerank-candidates", type=_int_list,
                   default=[DEFAULT_RERANK_CANDIDATES],
                   help="rerank_candidates values (used only when --rerank includes on)")
    p.add_argument("--seed", type=int, default=0, help="corpus-sampling seed")
    p.add_argument("--judged-fraction", type=float, default=0.5,
                   help="max fraction of a library that may be judged documents")
    p.add_argument("--query-limit", type=int, default=0,
                   help="cap the retained query set (0 = all); smoke runs only")
    p.add_argument("--concurrency", type=int, default=8,
                   help="in-flight queries per cell")
    p.add_argument("--bootstrap-iters", type=int, default=_stats.BOOTSTRAP_ITERS)
    p.add_argument("--out-root", type=Path, default=REPORT_ROOT / "runs",
                   help="per-run output directories are created under here")
    p.add_argument("--cache-dir", type=Path,
                   default=REPORT_ROOT / "cache" / "query_vectors")
    p.add_argument("--keep", action="store_true",
                   help="keep the g1_* stores (default: torn down in a finally:)")
    p.add_argument("--smoke", action="store_true",
                   help="minimal grid: the two pre-registered cells only")
    p.add_argument("--endpoints", default=None,
                   help="comma-separated SFR base URLs (else the built-in 16)")
    p.add_argument("--embedding-api-key", default=None)
    p.add_argument("--hard-cap-tokens", type=int, default=c7.HARD_CAP_TOKENS)
    args = p.parse_args(argv)
    args.modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    bad = set(args.modes) - {"hybrid", "vector", "bm25"}
    if bad:
        p.error(f"unknown --modes {sorted(bad)}")
    if args.smoke:
        args.modes = ["hybrid"]
        args.rrf_k = [DEFAULT_RRF_K]
        args.top_k = [DEFAULT_TOP_K]
        args.multipliers = [DEFAULT_MULTIPLIER, PRIMARY_MULTIPLIER]
        args.rerank = [False]
    return args


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv) if argv is None else ["g1_library_sweep.py", *argv]
    args = parse_args(argv)
    c7.HARD_CAP_TOKENS = args.hard_cap_tokens
    c7.EMBED_API_KEY = args.embedding_api_key or os.environ.get("OPENAI_API_KEY")
    candidates = (
        [u.strip() for u in args.endpoints.split(",") if u.strip()]
        if args.endpoints else list(c7.DEFAULT_ENDPOINTS)
    )
    print(f"Probing {len(candidates)} candidate embedding endpoints ...", flush=True)
    c7.SFR_ENDPOINTS = c7.detect_live_endpoints(candidates, c7.EMBED_API_KEY)
    if not c7.SFR_ENDPOINTS:
        raise SystemExit("No live embedding endpoints; aborting.")
    print(
        f"Using {len(c7.SFR_ENDPOINTS)}/{len(candidates)} live SFR endpoint(s). "
        f"NOTE: this fleet is shared with production — do not sweep during a "
        f"bulk ingest.",
        flush=True,
    )
    from ragstack.ingestion.tokenization import HFTokenCounter

    c7.TOKEN_COUNTER = HFTokenCounter(model=c7.SFR_MODEL)
    c7.TOKEN_COUNTER._tokenizer()
    return asyncio.run(amain(args, raw_argv))


if __name__ == "__main__":
    raise SystemExit(main())
