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
3. Fill the remaining ``n_docs - |judged|`` slots from a **single seeded shuffle
   of the whole corpus**, walking it in order and skipping this rung's judged
   docs. Because the permutation does not depend on ``n_docs``, each rung's
   distractors are a prefix of one shared order and the *libraries* nest:
   ``docs(n50) ⊆ docs(n100) ⊆ docs(n200)``. Shuffling ``corpus - judged_set``
   instead — which an earlier version did — produces a *different* permutation
   per rung and destroys the pairing entirely (measured overlap 1/25 between
   adjacent rungs). :meth:`LibrarySample.nests_within` checks it at runtime and
   the result is recorded in the manifest as ``sample_nesting``.

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

What is swept: absolute per-leg depth D, not (top_k × multiplier)
-----------------------------------------------------------------
Protocol §6.2. ``top_k`` and ``retrieval_candidate_multiplier`` reach
``HybridRetriever`` **only through their product** (``retriever.py:53``), and
:meth:`InstrumentedHybridRetriever.retrieve_instrumented` deliberately does not
truncate — so ``top_k=10, mult=1`` and ``top_k=5, mult=2`` are the *same*
retrieval and would emit byte-identical duplicate cells. Sweeping both would
confound the report cutoff with the retrieval breadth, and every duplicate pair
would enter the multiplicity family as a guaranteed null, deflating every other
adjusted p-value.

So the grid axis is **D**, and :func:`shippable_triples` maps each D back to the
``(top_k, candidate_multiplier, rerank_candidates)`` triples an operator could
actually write — reported per cell, in ``results.csv`` and in the manifest,
because the deliverable (§11) states the triple, not D. The report cutoff k stays
free: every metric at every k is recomputed from one stored top-200 ranking
(§6.3a).

The designated primary comparison (exactly one) — NOT pre-registered
---------------------------------------------------------------------
    At the largest library size in the run, **shipping defaults**
    (``mode=hybrid, rrf_k=60, rerank_enabled=False``, per-leg depth **D = 10**,
    realizable as ``top_k=5 × multiplier=2``) **versus the same cell at
    D = 100** (``top_k=5 × multiplier=20``), on document-level **nDCG@10**,
    paired over the held-out **confirm** split, minimum effect δ = 0.02.

**This pair is not in the pre-registration.** PROTOCOL.md §6.2 registers the
*factor* (absolute depth D over {10, 20, 50, 100, 200}) but never names this pair
as the primary; the pair appears only here and in :data:`PRIMARY_DEPTH`,
committed *with* the harness and after the first smoke runs. Calling it
"pre-registered" would be a false provenance claim in a published artefact, so it
is labelled a **designated** primary everywhere it appears — in the manifest
(``preregistered: false``), the report, and the console — and recorded as
PROTOCOL.md amendment **A4**. It carries the weight of a single pre-specified
comparison (one test, one δ, no selection over the grid); it does not carry the
weight of pre-registration.

It is worth designating because §1.2 identifies retrieval breadth as the only
knob ever observed to move this metric (ΔnDCG@10 ≈ +0.046 between R5 and R5b,
twice the largest chunking effect ever measured here), and because it is the one
comparison whose outcome changes a shipping default on its own. Both cells are
force-added to every grid so the comparison always exists.

Statistics: the two-stage protocol, as pre-registered
------------------------------------------------------
Protocol §6.4 and §7.2, implemented rather than approximated:

* **Split.** 40% tune / 60% confirm, stratified by per-query nDCG@10 difficulty
  quintiles under the shipping default, seed 0, pinned to a fixture keyed on the
  query-set digest (:func:`stratified_split`, :func:`load_or_write_split`).
* **Stage 1 — screen.** The whole grid on the *tune* split, Benjamini–Hochberg
  FDR at q = 0.10, explicitly labelled *not a result* (:func:`screen_rung`).
* **Nomination.** §7.2's rule applied mechanically to stage-1 output: ≤ 5 cells,
  latency-constrained to 2× the default's p95 (§5.5), ties by p95 then by D
  (:func:`nominate`).
* **Stage 2 — confirm.** Holm–Bonferroni at α = 0.05 over exactly the nominated
  cells, **one family across the grid**, on the held-out split
  (:func:`confirm_stage2`).
* **The co-primary votes.** §5.1: a recommendation "must not be worse on either
  primary", so a candidate is recommended only if the 90% CI of
  Δ(nDCG@5-chunk) stays above −δ. Failures are reported as §5.1 split decisions.
* **A/A resolution gate.** §6.4: three replicates of one reference cell per run;
  δ must exceed 3× the A/A SD, with RBO@20 reported alongside
  (:func:`aa_gate`). If it did not run or did not pass, no recommendation.
* **The recommendation gate.** :func:`recommendation_gate` refuses to emit a
  ``LibraryRetrievalDefaults`` block at all when the regime cannot support one —
  HNSW unbuilt or unknown at the decision rung, a void primary cell, a
  substituted reference, an nDCG ceiling that leaves no room for δ, a failed A/A
  gate, or a dirty working tree.

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
    # /rag/envs/ragstack has `ragstack` installed from /rag/repos/ragstack, which
    # predates ragstack/provenance.py — PYTHONPATH must point at THIS checkout.
    export PYTHONPATH="$PWD"

    # smoke (~1 minute): one size, the two pre-registered cells, few queries
    /rag/envs/ragstack/bin/python scripts/eval/g1_library_sweep.py \\
        --doc-counts 50 --query-limit 8 --smoke

    # the full pilot sweep
    /rag/envs/ragstack/bin/python scripts/eval/g1_library_sweep.py \\
        --doc-counts 50,100,200 \\
        --modes hybrid,vector,bm25 \\
        --rrf-k 1,10,20,60,120,240 \\
        --depths 10,20,50,100,200 \\
        --rerank off,on --rerank-candidates 10,25,50,100,200 \\
        --require-clean
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

# The swept factor is **absolute per-leg depth D**, never (top_k x multiplier)
# separately — protocol §6.2. `top_k` and `candidate_multiplier` reach the
# retriever only through their product, so sweeping both would emit byte-identical
# duplicate cells (tk10_m1 == tk5_m2) that enter the multiplicity family as
# guaranteed nulls and deflate every other adjusted p-value.
DEFAULT_DEPTH = DEFAULT_TOP_K * DEFAULT_MULTIPLIER          # 10, rerank off
DEPTH_LEVELS = (10, 20, 50, 100, 200)                        # protocol §6.1
# The designated (NOT pre-registered — see the module docstring) primary
# comparison: D=10 vs D=100 at the largest rung. D=100 is protocol §6.2's worked
# example and is what the shipping defaults compose to with rerank ON.
PRIMARY_DEPTH = 100

# Minimum shippable effect, protocol §7.4.
DELTA = 0.02

# Protocol §6.4 / §7.2 two-stage design.
TUNE_FRACTION = 0.40         # 40% tune / 60% confirm, stratified by difficulty
SPLIT_SEED = 0
SCREEN_Q = 0.10              # stage-1 Benjamini-Hochberg FDR level
CONFIRM_ALPHA = 0.05         # stage-2 Holm-Bonferroni FWER level
MAX_NOMINATIONS = 5          # protocol §7.2: <= 5 candidates reach stage 2
LATENCY_BUDGET_FACTOR = 2.0  # protocol §5.5: p95 <= 2x the default's p95

# Protocol §6.4: 3 replicates of one designated reference cell per rung (an A/A
# null). delta must exceed 3x the A/A SD or the experiment is under-resolved.
AA_REPLICATES = 3
AA_SD_FACTOR = 3.0
AA_RBO_P = 0.9
AA_RBO_DEPTH = 20

# A paired bootstrap over per-query differences that are *identically zero* has
# zero variance, so its CI collapses to exactly [0, 0] and TOST fires at any n.
# That is degeneracy, not equivalence: it means no query in the set discriminates
# the two configurations. Below this many non-zero paired differences the verdict
# is INCONCLUSIVE regardless of the interval.
MIN_DISCRIMINATING_QUERIES = 5

# The second, subtler degeneracy route: a *constant* paired offset also has zero
# variance, so every resample returns the same mean and the interval collapses —
# without any zero difference for the count above to catch. An interval narrower
# than a millionth of the effect size we care about is not an interval; real
# per-query dispersion produces widths around 1e-3.
MIN_CI_WIDTH = DELTA * 1e-6

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

    def nests_within(self, bigger: LibrarySample) -> bool:
        """Is this library a subset of ``bigger``, queries and documents both?

        Cross-rung pairing is the justification for the whole ladder design, so
        it is checked at runtime and recorded in the manifest rather than
        asserted in a docstring. A distractor promoted to *judged* at the larger
        rung still counts as nesting — the document is present either way."""
        return (
            set(self.doc_ids) <= set(bigger.doc_ids)
            and self.query_ids == bigger.query_ids[: len(self.query_ids)]
        )


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
    are dropped rather than retained as guaranteed misses.

    **Nesting.** For a fixed seed the query sets and the judged-doc sets nest by
    construction (both are prefixes of one shuffled order). The *library* nests
    too — ``set(doc_ids(n_small)) <= set(doc_ids(n_big))`` — because the
    distractor pool is one shuffled permutation of the whole corpus and each rung
    takes a **prefix of that single permutation** minus its own judged set. The
    prefix property is what makes the pairing valid across rungs; an earlier
    version re-shuffled ``corpus - judged_set`` per rung, which produced two
    *unrelated* permutations and 1/25 measured overlap between adjacent rungs
    despite the docstring claiming otherwise.

    The library-nesting guarantee is exact whenever
    ``n_big * (1 - judged_fraction) >= n_small``, which the shipped 50/100/200
    ladder at ``judged_fraction=0.5`` satisfies; ``nests_within`` on the returned
    sample lets a caller check rather than assume.
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

    # Shuffle the WHOLE corpus once — the permutation must not depend on n_docs or
    # on this rung's judged set, or successive rungs get unrelated distractor sets
    # and nothing is paired across rungs.
    pool = list(corpus)
    random.Random(seed + 1).shuffle(pool)
    need = max(0, n_docs - len(judged))
    distractors = []
    for d in pool:
        if len(distractors) >= need:
            break
        if d not in judged_set:
            distractors.append(d)

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
    defaults (5 -> 50) — the confound this function makes explicit.

    This function is **not** a sweep axis. It is the forward map that
    :func:`shippable_triples` inverts at reporting time (protocol §6.2)."""
    base = max(top_k, rerank_candidates) if rerank_enabled else top_k
    return base * multiplier


def shippable_triples(
    depth: int,
    rerank_enabled: bool,
    rerank_candidates: int | None,
    top_ks: tuple[int, ...] = CURVE_KS,
) -> list[dict[str, Any]]:
    """Every production ``(top_k, candidate_multiplier, rerank_candidates)`` triple
    that realizes this absolute per-leg depth — the inverse of
    :func:`leg_depth_for`, and the protocol §6.2 reporting-time mapping.

    The sweep varies D because only D reaches the retriever; but the deliverable
    is config, and config has no ``D`` field. So every cell carries the list of
    settings an operator could actually write to realize it. ``top_k`` ranges over
    the report cutoffs (§6.1) because ``top_k`` *is* the report cutoff in
    production; a triple exists only when ``base`` divides ``D`` exactly, since
    ``candidate_multiplier`` is an integer."""
    out: list[dict[str, Any]] = []
    for tk in top_ks:
        base = max(tk, rerank_candidates or 0) if rerank_enabled else tk
        if base <= 0 or depth % base != 0:
            continue
        mult = depth // base
        if mult < 1:
            continue
        out.append(
            {
                "top_k": tk,
                "candidate_multiplier": mult,
                "rerank_candidates": rerank_candidates if rerank_enabled else None,
            }
        )
    return out


def _fmt_triples(triples: list[dict[str, Any]]) -> str:
    """Compact one-cell rendering of :func:`shippable_triples` for CSV/markdown."""
    return " | ".join(
        f"tk{t['top_k']}xm{t['candidate_multiplier']}" for t in triples
    ) or "none"


@dataclass(frozen=True)
class Cell:
    """One point of the sweep grid.

    The depth factor is **absolute per-leg depth D**, not ``(top_k, multiplier)``.
    Protocol §6.2: only their product reaches ``HybridRetriever``, and
    ``retrieve_instrumented`` deliberately does not truncate, so ``tk10_m1`` and
    ``tk5_m2`` would be byte-identical cells. The shippable triples are recovered
    at reporting time by :func:`shippable_triples`."""

    rung: str
    n_docs: int
    mode: str
    rrf_k: int | None
    depth: int
    rerank_enabled: bool
    rerank_candidates: int | None

    @property
    def leg_depth(self) -> int:
        return self.depth

    @property
    def cell_id(self) -> str:
        rrf = f"rrf{self.rrf_k}" if self.rrf_k is not None else "rrfna"
        rr = f"rr{self.rerank_candidates}" if self.rerank_enabled else "rr0"
        return f"{self.rung}_{self.mode}_{rrf}_d{self.depth}_{rr}"

    @property
    def triples(self) -> list[dict[str, Any]]:
        return shippable_triples(
            self.depth, self.rerank_enabled, self.rerank_candidates
        )

    @property
    def is_default(self) -> bool:
        """The shipping-default configuration: hybrid, rrf_k=60, rerank off, and
        the depth that ``top_k=5 x candidate_multiplier=2`` composes to."""
        return (
            self.mode == "hybrid"
            and self.rrf_k == DEFAULT_RRF_K
            and self.depth == DEFAULT_DEPTH
            and self.rerank_enabled is DEFAULT_RERANK_ENABLED
        )

    @property
    def is_primary_alt(self) -> bool:
        return (
            self.mode == "hybrid"
            and self.rrf_k == DEFAULT_RRF_K
            and self.depth == PRIMARY_DEPTH
            and self.rerank_enabled is DEFAULT_RERANK_ENABLED
        )

    def as_params(self) -> dict[str, Any]:
        return {
            "rung": self.rung,
            "n_docs": self.n_docs,
            "mode": self.mode,
            "rrf_k": self.rrf_k,
            "leg_depth": self.depth,
            "rerank_enabled": self.rerank_enabled,
            "rerank_candidates": self.rerank_candidates,
            # Protocol §6.2: the deliverable states the triple, not D.
            "shippable_triples": self.triples,
            "is_shipping_default": self.is_default,
            "use_graph": False,
            "rewrite_strategies": ["passthrough"],
        }


def build_grid(
    n_docs_list: list[int],
    modes: list[str],
    rrf_ks: list[int],
    depths: list[int],
    rerank_flags: list[bool],
    rerank_candidates: list[int],
) -> list[Cell]:
    """Expand the factor lists into a de-duplicated cell list.

    Three collapses keep the grid honest rather than merely large: ``rrf_k`` is a
    no-op outside ``hybrid`` mode (one leg, nothing to fuse) so non-hybrid cells
    are emitted once with ``rrf_k=None``; ``rerank_candidates`` is meaningless
    with the reranker off; and ``C > D`` is dropped, because protocol §6.3b's
    offline derivation of smaller ``C`` is faithful only while ``C <= D``.

    The two designated primary cells (D=10 and D=100, hybrid, rrf_k=60, rerank
    off) are force-added at every rung so the primary comparison always exists
    regardless of what the CLI asked for.
    """
    seen: dict[str, Cell] = {}

    def _add(cell: Cell) -> None:
        seen.setdefault(cell.cell_id, cell)

    for n in n_docs_list:
        rung = f"n{n}"
        for mode, depth, rr in product(modes, depths, rerank_flags):
            ks = rrf_ks if mode == "hybrid" else [None]
            cs = [c for c in rerank_candidates if c <= depth] if rr else [None]
            for rrf_k, cand in product(ks, cs):
                _add(Cell(rung, n, mode, rrf_k, depth, rr, cand))
        for depth in (DEFAULT_DEPTH, PRIMARY_DEPTH):
            _add(
                Cell(rung, n, "hybrid", DEFAULT_RRF_K, depth,
                     DEFAULT_RERANK_ENABLED, None)
            )
    return sorted(seen.values(), key=lambda c: (c.n_docs, c.depth, c.cell_id))


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
    # Filled by measure_index_telemetry() before any cell runs, so every cell
    # record can carry the regime it was measured in (protocol §6.6).
    hnsw: dict[str, Any] = field(default_factory=dict)
    es: dict[str, Any] = field(default_factory=dict)

    @property
    def hnsw_built(self) -> bool | None:
        return hnsw_state(self.hnsw)

    @property
    def scale_regime(self) -> str:
        return scale_regime_for(self.hnsw)


# Three states, never two. `hnsw_built is False` and "we could not find out" are
# different facts and the second one must never be reported as the first — see
# _scale_banner and recommendation_gate.
REGIME_HNSW = "hnsw"                # graph built; approximate search is live
REGIME_BRUTE_FORCE = "brute_force"  # below indexing_threshold; exact scan
REGIME_UNKNOWN = "unknown"          # telemetry missing or errored


def hnsw_state(hnsw: dict[str, Any] | None) -> bool | None:
    """``True`` / ``False`` / ``None`` for "we do not know".

    ``qdrant_index_info`` returns ``{"error": ...}`` on any failure, with no
    ``hnsw_built`` key at all. Treating a missing key as "built" (or as "not
    built") turns a telemetry outage into an affirmative claim about the index."""
    if not isinstance(hnsw, dict) or "error" in hnsw:
        return None
    v = hnsw.get("hnsw_built")
    return v if isinstance(v, bool) else None


def scale_regime_for(hnsw: dict[str, Any] | None) -> str:
    state = hnsw_state(hnsw)
    if state is None:
        return REGIME_UNKNOWN
    return REGIME_HNSW if state else REGIME_BRUTE_FORCE


def library_collection_name(n_docs: int, spec_hash: str, sample_digest: str) -> str:
    """Name a scratch store so two runs can never merge their corpora.

    ``spec_hash`` covers only model/dim/chunker, so ``g1_lib_50docs_<spec>`` is
    identical for every seed and every ``judged_fraction`` — and both
    ``ensure_collection`` and ``ensure_index`` happily reuse an existing store.
    Two runs at different seeds would therefore silently upsert two different
    50-document corpora into the same collection and score the union. Folding in
    the sample digest (which covers doc ids, query ids and the seed) makes the
    name identify the *corpus*, not just the build spec."""
    return guard_scratch(f"g1_lib_{n_docs}docs_{spec_hash}_{sample_digest[:12]}")


async def assert_store_absent_or_empty(collection: str, es_index: str) -> None:
    """Refuse to upsert into a store that already holds points.

    The name now carries the sample digest, so a collision means either a
    concurrent run or a leaked store from a crashed one. Either way, appending to
    it produces a corpus that matches no manifest."""
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{c7.QDRANT_URL}/collections/{collection}")
        if r.status_code == 200:
            points = (r.json().get("result") or {}).get("points_count") or 0
            if points:
                raise SystemExit(
                    f"REFUSING to build: Qdrant collection {collection!r} already "
                    f"holds {points} points. A leaked store from a crashed run, or "
                    f"a concurrent run at the same seed. Drop it or pass --keep to "
                    f"the run that owns it."
                )
        r = await c.get(f"{c7.ES_URL}/{es_index}/_count")
        if r.status_code == 200:
            n = int(r.json().get("count", 0))
            if n:
                raise SystemExit(
                    f"REFUSING to build: ES index {es_index!r} already holds {n} "
                    f"documents."
                )


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
    created: list[str] | None = None,
    extra_chunks: list[Chunk] | None = None,
    distractor_meta: dict[str, Any] | None = None,
) -> LibraryIndex:
    """Chunk + embed + ingest one library into guarded ``g1_*`` stores.

    ``created`` is the caller's teardown ledger and is appended to **the moment
    the name is known**, before any store is created. Registering it on return
    instead would leak the Qdrant collection and the ES index whenever embedding
    or the upsert loop raises — which is exactly when a leak is most likely.

    ``extra_chunks`` is the **distractor-ladder seam**: pre-embedded chunks (the
    future read-only scroll of production ``ragstack_sfr_tok512``) are written to
    Qdrant *and* ES alongside the judged core. Writing both is not optional —
    BM25 document-frequency and ``avgdl`` statistics are the whole reason the
    ladder exists for the sparse leg, and a Qdrant-only ladder would hold the
    BM25 index at rung 0 while the dense index grew, manufacturing a spurious
    hybrid-vs-dense interaction.
    """
    t0 = time.perf_counter()
    cfg = c7.CONFIG_BY_KEY[CHUNK_CONFIG_KEY]
    desc, spec_hash, _ = build_spec()
    collection = library_collection_name(sample.n_docs, spec_hash, sample.digest)
    es_index = guard_scratch(collection)
    # Register for teardown BEFORE anything else can raise (see the docstring).
    # Nothing between here and the return may fail without the name being on the
    # ledger, so the `finally:` in amain can always clean up.
    if created is not None and collection not in created:
        created.append(collection)
    await assert_store_absent_or_empty(collection, es_index)

    docs = [docs_by_id[d] for d in sample.doc_ids]
    chunks = c7.chunk_docs_for_config(cfg, docs)
    chunks, n_capped = c7.cap_oversized(chunks)

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
) -> tuple[dict[str, list[float]], dict[str, Any]]:
    """Embed every query text once and memoize on disk, keyed by (spec, query set).

    Returns ``(vectors, cache_provenance)``. The provenance block is not
    decoration: the cache key is ``(spec_hash, query texts)``, which says nothing
    about *which model behind the endpoint* produced the vectors. A model swap
    behind ``:9001-9008`` at a fixed ``spec_hash`` would silently reuse stale
    vectors and every dense number in the run would describe an embedder that is
    no longer deployed. The file's own SHA-256 plus a hit/miss flag is what makes
    that detectable after the fact — two runs claiming the same ``spec_hash`` but
    carrying different ``sha256`` values did not use the same query vectors.

    Validation is on the key set and the dimensionality, not just the count: a
    cache with the right number of entries for the wrong queries is worse than a
    miss."""
    texts = [queries[q] for q in sorted(queries)]
    key = _digest(spec_hash, *texts)[:16]
    path = cache_dir / f"{DATASET_NAME}.{spec_hash}.{key}.json"
    out: dict[str, list[float]] | None = None
    hit = False
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        dims = {len(v) for v in cached.values()}
        if set(cached) == set(texts) and dims == {c7.VECTOR_SIZE}:
            print(f"[qvec] {len(cached)} query vectors from cache {path.name}",
                  flush=True)
            out, hit = cached, True
        else:
            print(
                f"[qvec] cache {path.name} rejected "
                f"(keys match={set(cached) == set(texts)}, dims={sorted(dims)}) "
                f"— re-embedding",
                flush=True,
            )
    if out is None:
        print(f"[qvec] embedding {len(texts)} queries once ...", flush=True)
        vecs = await c7.embed_texts_async(client, texts)
        out = dict(zip(texts, vecs, strict=True))
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out), encoding="utf-8")
    meta = {
        "path": str(path),
        "sha256": _sha256_file(path),
        "hit": hit,
        "n_vectors": len(out),
        "dim": c7.VECTOR_SIZE,
        "key": key,
        "spec_hash": spec_hash,
    }
    return out, meta


async def reranker_provenance(base_url: str) -> dict[str, Any]:
    """Model **and revision** of the cross-encoder actually serving this run.

    Protocol §8 lists "reranker model + revision" as required provenance and
    §6.3b keys the rerank score cache on them — recording only the sidecar URL
    makes both unenforceable, since the sidecar reads ``MODEL_NAME`` from its own
    environment and can be restarted onto a different checkpoint at the same
    port. ``/health`` reports the model id; the revision is resolved from the
    local HF snapshot directory, which is the commit sha the weights were
    materialized from."""
    out: dict[str, Any] = {
        "url": base_url, "model": None, "revision": None, "revision_source": None,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            out["model"] = (await c.get(f"{base_url}/health")).json().get("model")
    except Exception as exc:  # noqa: BLE001 - provenance must never fail a run
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    model = out["model"]
    if not model:
        return out
    hub = Path(
        os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")
    ) / "hub"
    snaps = hub / f"models--{model.replace('/', '--')}" / "snapshots"
    try:
        revs = sorted(p.name for p in snaps.iterdir() if p.is_dir())
    except OSError:
        revs = []
    if len(revs) == 1:
        out["revision"], out["revision_source"] = revs[0], str(snaps)
    elif revs:
        # More than one materialized snapshot: we cannot tell which one the
        # sidecar loaded, so record the ambiguity rather than guessing.
        out["revision_candidates"], out["revision_source"] = revs, str(snaps)
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
    store_factory: Any | None = None,
) -> dict[str, Any]:
    """Run one grid cell over every retained query. Returns per-query arrays,
    Track-C counters and the §5.4 sanity verdict.

    This is the one place every swept parameter is threaded into the retriever,
    so it is also the one place a parameter can silently stop being applied.
    ``store_factory`` exists to make that testable offline: it is called as
    ``store_factory(index)`` and must return ``(vector_store, text_index)``;
    ``None`` builds the real Qdrant/ES clients."""
    qids = list(index.sample.query_ids)
    if store_factory is None:
        vstore: Any = QdrantVectorStore(
            url=c7.QDRANT_URL, collection=index.collection,
            vector_size=c7.VECTOR_SIZE, timeout=120,
        )
        tindex: Any = ElasticsearchTextIndex(url=c7.ES_URL, index=index.es_index)
    else:
        vstore, tindex = store_factory(index)
    retriever = InstrumentedHybridRetriever(
        vstore,
        tindex,
        CachedQueryEmbedder(qvectors),
        rrf_scorer=RRFScorer(k=cell.rrf_k if cell.rrf_k is not None else DEFAULT_RRF_K),
        # The swept factor is absolute per-leg depth D (protocol §6.2). Feeding it
        # as `top_k=D` with `candidate_multiplier=1` makes D reach both legs
        # exactly, with no aliasing between a report cutoff and a breadth knob.
        candidate_multiplier=1,
    )
    filters = scope_filters({}, TENANT)
    sem = asyncio.Semaphore(concurrency)
    depth_driver = cell.depth

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
    close = getattr(tindex, "close", None)
    if close is not None:
        await close()

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
        "rankings": {r["qid"]: r["ranking"] for r in results},
        "sanity": sanity,
        # Machine-readable measurability regime, on EVERY cell record — not only
        # in the markdown banner. A consumer of results.csv or manifest.json must
        # be able to tell that a cell was measured on an unbuilt HNSW graph
        # without reading prose (protocol §6.6; review finding 4).
        "regime": {
            "scale_regime": index.scale_regime,
            "hnsw_built": index.hnsw_built,
            "chunks_per_doc": index.chunks_per_doc,
            "n_chunks": index.n_chunks,
            "indexed_vectors": index.hnsw.get("indexed_vectors"),
            "indexing_threshold": index.hnsw.get("indexing_threshold"),
        },
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
    """Code identity, with the uncommitted delta *identified* rather than flagged.

    A bare ``dirty: true`` is not reproducible provenance — it says a third party
    cannot rebuild this code but not what it was. ``dirty_digest`` is the SHA-256
    of the porcelain status plus the full working-tree diff (tracked files,
    staged and unstacked), so two runs can at least be shown to have run the same
    uncommitted code, and a published number can be traced to a specific delta.
    The recommendation gate additionally refuses to recommend from a dirty tree
    (see :func:`recommendation_gate`)."""
    def _run(*args: str) -> str:
        try:
            return subprocess.run(
                args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
            ).stdout
        except Exception:  # noqa: BLE001
            return ""

    status = _run("git", "status", "--porcelain")
    diff = _run("git", "diff", "HEAD")
    dirty = bool(status.strip())
    return {
        "commit": _run("git", "rev-parse", "HEAD").strip(),
        "branch": _run("git", "rev-parse", "--abbrev-ref", "HEAD").strip(),
        "dirty": dirty,
        "dirty_digest": ("sha256:" + _digest(status, diff)) if dirty else None,
        "dirty_files": sorted(
            ln[3:].strip() for ln in status.splitlines() if ln.strip()
        ),
        "diff_bytes": len(diff.encode("utf-8")) if dirty else 0,
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
    qvec_cache: dict[str, Any],
    reranker: dict[str, Any],
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
            "swept_factor": "absolute per-leg depth D (protocol §6.2)",
            "depths": sorted({c.depth for c in grid}),
            "shippable_triples_by_depth": {
                str(d): shippable_triples(d, False, None)
                for d in sorted({c.depth for c in grid})
            },
            "primary_comparison": {
                "metric": PRIMARY_METRIC,
                "co_primary_metric": CO_PRIMARY_METRIC,
                "reference": "shipping defaults "
                f"(hybrid, rrf_k={DEFAULT_RRF_K}, rerank=off, per-leg depth "
                f"D={DEFAULT_DEPTH})",
                "candidate": f"the same cell at per-leg depth D={PRIMARY_DEPTH}",
                "delta": DELTA,
                # NOT pre-registered: the pair was chosen with the harness, after
                # PROTOCOL.md was hashed. PROTOCOL.md amendment A4 records it as a
                # designated comparison. Claiming pre-registration here would be
                # the one provenance field a reader most relies on being true.
                "preregistered": False,
                "designation": "designated primary (PROTOCOL.md amendment A4)",
            },
            "procedure": {
                "stage1": f"Benjamini-Hochberg FDR q={SCREEN_Q}, tune split",
                "stage2": f"Holm-Bonferroni alpha={CONFIRM_ALPHA} over <= "
                          f"{MAX_NOMINATIONS} nominated cells, confirm split, "
                          f"one family across the grid",
                "co_primary_rule": "protocol §5.1 — not worse on either primary",
            },
        },
        "reranker": reranker,
        "query_vector_cache": qvec_cache,
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
            "query_split": SPLIT_SEED,
            "bootstrap": _stats.SEED,
            "bootstrap_iters": args.bootstrap_iters,
        },
        "argv": list(argv),
        "cwd": os.getcwd(),
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _cell_kwargs(cell_result: dict[str, Any]) -> dict[str, Any]:
    p = cell_result["params"]
    return {
        "rung": p["rung"], "n_docs": p["n_docs"], "mode": p["mode"],
        "rrf_k": p["rrf_k"], "depth": p["leg_depth"],
        "rerank_enabled": p["rerank_enabled"],
        "rerank_candidates": p["rerank_candidates"],
    }


# --------------------------------------------------------------------------- #
# Query splits (protocol §6.4)
# --------------------------------------------------------------------------- #
def stratified_split(
    query_ids: list[str],
    difficulty: dict[str, float],
    *,
    tune_fraction: float = TUNE_FRACTION,
    seed: int = SPLIT_SEED,
    n_strata: int = 5,
) -> tuple[list[str], list[str]]:
    """Protocol §6.4's 40/60 tune/confirm split, stratified by difficulty quintiles.

    ``difficulty`` is per-query nDCG@10 under the **shipping-default**
    configuration — the reference in both stages, so stratifying on it cannot
    advantage any candidate. Queries are ordered by difficulty, cut into
    ``n_strata`` contiguous strata, and each stratum is split independently, so
    the two halves cannot differ in baseline difficulty; an unstratified split
    would let a hard-query surplus in one half inflate or deflate every stage-2
    effect.

    Deterministic given ``(query_ids, difficulty, seed)``. Ties in difficulty are
    broken by query id so the ordering does not depend on dict iteration order.
    """
    if not query_ids:
        return [], []
    ordered = sorted(query_ids, key=lambda q: (difficulty.get(q, 0.0), q))
    rng = random.Random(seed)
    tune: list[str] = []
    confirm: list[str] = []
    n = len(ordered)
    bounds = [round(i * n / n_strata) for i in range(n_strata + 1)]
    for lo, hi in zip(bounds[:-1], bounds[1:], strict=True):
        stratum = ordered[lo:hi]
        if not stratum:
            continue
        shuffled = list(stratum)
        rng.shuffle(shuffled)
        n_tune = round(len(shuffled) * tune_fraction)
        tune.extend(shuffled[:n_tune])
        confirm.extend(shuffled[n_tune:])
    return sorted(tune), sorted(confirm)


def load_or_write_split(
    fixture_path: Path,
    query_ids: list[str],
    difficulty: dict[str, float],
    *,
    queries_sha256: str,
    difficulty_cell: str,
) -> dict[str, Any]:
    """Pin the split to a fixture, and reuse it whenever the query set is unchanged.

    Protocol §6.4 wants the split written *before* any sweep run. The difficulty
    it stratifies on is itself a measurement, so the first run necessarily
    derives it — mechanically, from the shipping-default cell alone, at a fixed
    seed. From then on the fixture is authoritative and the split is genuinely
    pre-run.

    Reuse is keyed on ``split_query_ids_sha256`` — a digest of the query ids
    **actually split** — not on the dataset-wide ``queries_sha256``. The two
    differ whenever ``--query-limit`` truncates or a rung drops unsatisfiable
    queries, and keying on the dataset digest would let a 12-query smoke run's
    fixture become authoritative for a 300-query publishable run. The dataset
    digest is still recorded, as provenance.
    """
    ids_sha = "sha256:" + _digest("|".join(sorted(query_ids)))
    if fixture_path.exists():
        try:
            fx = json.loads(fixture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fx = None
        if fx and fx.get("split_query_ids_sha256") == ids_sha:
            fx["source"] = "fixture"
            fx["path"] = str(fixture_path)
            fx["sha256"] = _sha256_file(fixture_path)
            return fx
    tune, confirm = stratified_split(query_ids, difficulty)
    fx = {
        "schema": "ragstack.g1_query_split/v1",
        "dataset": DATASET_NAME,
        "queries_sha256": queries_sha256,
        "split_query_ids_sha256": ids_sha,
        "n_queries": len(query_ids),
        "split_seed": SPLIT_SEED,
        "tune_fraction": TUNE_FRACTION,
        "difficulty_metric": PRIMARY_METRIC,
        "difficulty_cell": difficulty_cell,
        "tune": tune,
        "confirm": confirm,
    }
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(fx, indent=2, sort_keys=True), encoding="utf-8")
    fx["source"] = "derived"
    fx["path"] = str(fixture_path)
    fx["sha256"] = _sha256_file(fixture_path)
    return fx


def _slice(cell: dict[str, Any], metric: str, keep: set[str]) -> list[float]:
    """This cell's per-query array for ``metric``, restricted to ``keep``."""
    return [
        v
        for q, v in zip(cell["query_ids"], cell["per_query"][metric], strict=True)
        if q in keep
    ]


# --------------------------------------------------------------------------- #
# Verdicts (protocol §7.5) — with the degeneracy guard
# --------------------------------------------------------------------------- #
def n_discriminating(a: list[float], b: list[float]) -> int:
    """How many queries actually separate the two configurations."""
    return sum(1 for x, y in zip(a, b, strict=True) if x != y)


def three_way_verdict(
    diff95: _stats.CI, diff90: _stats.CI, p: float, n_disc: int, *, delta: float = DELTA
) -> tuple[str, str | None]:
    """DIFFERENT / EQUIVALENT / INCONCLUSIVE, with a floor on the evidence.

    **Why the floor.** The paired bootstrap resamples *queries*. If every query's
    paired difference is zero — which happens whenever two "different" cells are
    in fact the same retrieval, or when the task saturates — then every resample
    has mean difference exactly 0, the 90% CI is exactly ``[0, 0]``, and TOST
    declares EQUIVALENT at **any** n, including n=1. That is not a measurement of
    sameness; it is the absence of a measurement, and reporting it as
    "genuinely no practical difference" (protocol §7.5) would be a false claim
    in a published artefact. Below :data:`MIN_DISCRIMINATING_QUERIES` non-zero
    paired differences the verdict is INCONCLUSIVE and the reason is recorded.
    """
    if n_disc < MIN_DISCRIMINATING_QUERIES:
        return "INCONCLUSIVE", (
            f"only {n_disc} of the paired per-query differences are non-zero "
            f"(floor {MIN_DISCRIMINATING_QUERIES}); the difference distribution is "
            f"degenerate, so the equivalence interval carries no information"
        )
    if abs(diff95.point) >= delta and p < 0.05:
        return "DIFFERENT", None
    if diff90.lo > -delta and diff90.hi < delta:
        # Second degeneracy route, and the subtler one: a *constant* offset also
        # has zero variance in the paired difference, so every resample returns
        # the same mean and the interval is again a point. The query count is
        # healthy and the floor above does not fire, but the interval still
        # measures nothing. Equivalence must be earned against real dispersion.
        if (diff90.hi - diff90.lo) <= MIN_CI_WIDTH:
            return "INCONCLUSIVE", (
                "the paired difference distribution has zero variance, so the "
                f"bootstrap interval collapsed to a point ({diff90.lo:+.6f}, "
                f"width <= {MIN_CI_WIDTH:g}); TOST would fire at any n. This is "
                "the absence of a measurement, not equivalence"
            )
        return "EQUIVALENT", None
    return "INCONCLUSIVE", None


def compare_cells(
    ref: dict[str, Any],
    alt: dict[str, Any],
    keep: set[str],
    iters: int,
    *,
    metric: str = PRIMARY_METRIC,
) -> dict[str, Any]:
    """One paired comparison of ``alt`` against ``ref`` on ``keep``'s queries."""
    a = _slice(ref, metric, keep)
    b = _slice(alt, metric, keep)
    pq = {"ref": a, "alt": b}
    diff = _stats.bootstrap_diff_ci(pq, "ref", iters=iters)["alt"]
    tost = _stats.bootstrap_diff_ci(pq, "ref", iters=iters, alpha=0.10)["alt"]
    _, p = _stats.wilcoxon_signed_rank(b, a)
    n_disc = n_discriminating(b, a)
    verdict, reason = three_way_verdict(diff, tost, p, n_disc)
    return {
        "metric": metric,
        "reference_cell": ref["cell_id"],
        "candidate_cell": alt["cell_id"],
        "n_queries": len(a),
        "n_discriminating": n_disc,
        "reference_mean": (sum(a) / len(a)) if a else 0.0,
        "candidate_mean": (sum(b) / len(b)) if b else 0.0,
        "diff_ci95": asdict(diff),
        "diff_ci90": asdict(tost),
        "wilcoxon_p": p,
        "delta": DELTA,
        "verdict": verdict,
        "verdict_reason": reason,
        "valid": ref["sanity"]["verdict"] == "PASS"
        and alt["sanity"]["verdict"] == "PASS",
    }


def pick_reference(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the rung's reference cell, and say so loudly if it is not the default.

    Every comparison at a rung is worded "vs the shipping default". If the
    shipping-default cell is itself void, falling back to the alphabetically
    first valid cell keeps the prose while changing its meaning — so the
    substitution is reported as a field, the rung's directional claims are
    voided, and the rung is excluded from nomination."""
    valid = [c for c in cells if c["sanity"]["verdict"] == "PASS"]
    default = next(
        (c for c in valid if Cell(**_cell_kwargs(c)).is_default), None
    )
    if default is not None:
        return {
            "valid": valid, "reference": default["cell_id"],
            "reference_is_default": True, "substituted": False,
            "n_void_cells": len(cells) - len(valid),
        }
    return {
        "valid": valid,
        "reference": valid[0]["cell_id"] if valid else None,
        "reference_is_default": False,
        "substituted": bool(valid),
        "n_void_cells": len(cells) - len(valid),
    }


# --------------------------------------------------------------------------- #
# Stage 1 — the screen (protocol §7.2: BH FDR q = 0.10, tune split)
# --------------------------------------------------------------------------- #
def screen_rung(
    cells: list[dict[str, Any]], tune: set[str], iters: int
) -> tuple[str, str, dict[str, Any]]:
    """Exploratory screen for one rung, on the **tune** split only.

    Benjamini–Hochberg at q = 0.10, not Holm: this stage makes no ship/no-ship
    claim, and FWER over a whole rung's grid would nominate nothing. Cells voided
    by the §5.4 assertion are excluded — their quality numbers describe a
    degraded pipeline, so including them would launder a bug into a parameter
    effect."""
    picked = pick_reference(cells)
    valid, ref = picked["valid"], picked["reference"]
    if not valid or ref is None:
        return "", "**No valid cell at this rung** (all failed the §5.4 assertion).", {
            "reference": None, "n_valid_cells": 0,
            "n_void_cells": picked["n_void_cells"], "screen": {},
        }
    by_id = {c["cell_id"]: c for c in valid}
    keys = sorted(by_id)
    metrics = {
        m: {k: _slice(by_id[k], m, tune) for k in keys}
        for m in (PRIMARY_METRIC, CO_PRIMARY_METRIC, "recall@10", "map")
    }
    table, _ = _stats.build_stats_table(
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
    bh = _stats.benjamini_hochberg(raw_p, q=SCREEN_Q) if raw_p else {}
    n_flagged = sum(1 for v in bh.values() if v[1])
    interp = (
        f"**Stage-1 screen (exploratory, NOT a result).** Tune split, n="
        f"{len(metrics[PRIMARY_METRIC][ref])}. Benjamini–Hochberg FDR at "
        f"q={SCREEN_Q}: {n_flagged} of {len(bh)} cell(s) flagged for stage 2. "
        f"No cell here may change a shipping default; only the stage-2 "
        f"confirm-split table below can."
    )
    if picked["substituted"]:
        interp = (
            f"> ⚠️ **REFERENCE SUBSTITUTED — directional claims at this rung are "
            f"VOID.** The shipping-default cell failed the §5.4 assertion, so "
            f"`{ref}` is standing in for it. Every 'vs the shipping default' "
            f"reading below is wrong: this rung compares cells to another "
            f"candidate. The rung is excluded from stage-2 nomination.\n\n"
        ) + interp
    summary = {
        "reference": ref,
        "reference_is_default": picked["reference_is_default"],
        "reference_substituted": picked["substituted"],
        "directional_claims": "VOID" if picked["substituted"] else "ok",
        "n_valid_cells": len(valid),
        "n_void_cells": picked["n_void_cells"],
        "split": "tune",
        "n_queries": len(metrics[PRIMARY_METRIC][ref]),
        "diff_ci": {k: asdict(v) for k, v in diffs.items()},
        "bh_fdr_q": SCREEN_Q,
        "screen": {k: {"adj_p": v[0], "flagged": v[1]} for k, v in bh.items()},
    }
    return table, interp, summary


def nominate(
    cell_results: list[dict[str, Any]],
    rung_summaries: dict[str, dict[str, Any]],
    tune: set[str],
) -> dict[str, Any]:
    """Protocol §7.2's nomination rule, applied mechanically to stage-1 output.

    The shortlist is (1) the shipping default, (2) the highest-mean-nDCG@10 cell
    at the smallest rung meeting the §5.5 latency constraint, (3) the same at the
    largest rung, (4) the best dense-only cell, (5) the best rerank-on cell.
    Ties break by lower p95 latency, then by lower D. Rungs whose reference was
    substituted are excluded — their "vs default" ordering is not about the
    default. If (2) and (3) coincide the shortlist is shorter, which §7.2 notes
    is itself weak evidence against H3."""
    usable_rungs = {
        r for r, s in rung_summaries.items()
        if s.get("reference_is_default") and s.get("n_valid_cells")
    }
    pool = [
        c for c in cell_results
        if c["sanity"]["verdict"] == "PASS" and c["params"]["rung"] in usable_rungs
    ]
    if not pool:
        return {
            "shortlist": [], "rationale": {},
            "excluded_rungs": sorted(set(rung_summaries) - usable_rungs),
            "note": "no rung has a valid shipping-default reference",
        }
    defaults = {
        c["params"]["rung"]: c for c in pool if Cell(**_cell_kwargs(c)).is_default
    }

    def _mean(c: dict[str, Any]) -> float:
        vals = _slice(c, PRIMARY_METRIC, tune)
        return sum(vals) / len(vals) if vals else 0.0

    def _within_budget(c: dict[str, Any]) -> bool:
        ref = defaults.get(c["params"]["rung"])
        if ref is None:
            return False
        budget = LATENCY_BUDGET_FACTOR * ref["cost"]["p95_query_ms"]
        return budget <= 0 or c["cost"]["p95_query_ms"] <= budget

    def _best(cands: list[dict[str, Any]]) -> dict[str, Any] | None:
        ok = [c for c in cands if _within_budget(c)]
        if not ok:
            return None
        return max(
            ok,
            key=lambda c: (
                _mean(c), -c["cost"]["p95_query_ms"], -c["params"]["leg_depth"]
            ),
        )

    rungs = sorted(usable_rungs, key=lambda r: int(r[1:]))
    smallest, largest = rungs[0], rungs[-1]
    shortlist: list[str] = []
    rationale: dict[str, str] = {}

    def _take(c: dict[str, Any] | None, why: str) -> None:
        if c is None or len(shortlist) >= MAX_NOMINATIONS:
            return
        if c["cell_id"] in shortlist:
            rationale[c["cell_id"]] += f"; {why}"
            return
        shortlist.append(c["cell_id"])
        rationale[c["cell_id"]] = why

    _take(defaults.get(largest) or next(iter(defaults.values()), None),
          "(1) the shipping default — the reference")
    _take(_best([c for c in pool if c["params"]["rung"] == smallest]),
          f"(2) highest mean {PRIMARY_METRIC} at the smallest rung `{smallest}`")
    _take(_best([c for c in pool if c["params"]["rung"] == largest]),
          f"(3) highest mean {PRIMARY_METRIC} at the largest rung `{largest}`")
    _take(_best([c for c in pool if c["params"]["mode"] == "vector"]),
          "(4) best dense-only cell")
    _take(_best([c for c in pool if c["params"]["rerank_enabled"]]),
          "(5) best rerank-on cell at matched depth")
    return {
        "shortlist": shortlist,
        "rationale": rationale,
        "excluded_rungs": sorted(set(rung_summaries) - usable_rungs),
        "max_nominations": MAX_NOMINATIONS,
        "latency_budget_factor": LATENCY_BUDGET_FACTOR,
    }


# --------------------------------------------------------------------------- #
# Stage 2 — confirm (protocol §7.2: Holm alpha = 0.05, held-out split)
# --------------------------------------------------------------------------- #
def confirm_stage2(
    cell_results: list[dict[str, Any]],
    shortlist: list[str],
    confirm: set[str],
    iters: int,
) -> dict[str, Any]:
    """Holm–Bonferroni over exactly the nominated candidates, on the held-out split.

    One family **across the grid**, not per rung: the nomination rule is itself
    across the grid (items 2 and 3 name different rungs), so the family of
    ship/no-ship tests is the shortlist, and Holm's m is its size.

    The co-primary votes here, as protocol §5.1 requires: "any configuration
    recommended must not be worse than the default on either primary". Worse is
    operationalized as the 90% CI of Δ(nDCG@5-chunk) reaching below −δ — a
    one-sided non-inferiority read of the same interval TOST uses. A candidate
    that clears nDCG@10 but fails this is reported as a **split decision**, and
    §5.1 resolves splits in favour of nDCG@5, i.e. it is not recommended.
    """
    by_id = {c["cell_id"]: c for c in cell_results}
    defaults = {
        c["params"]["rung"]: c
        for c in cell_results
        if Cell(**_cell_kwargs(c)).is_default and c["sanity"]["verdict"] == "PASS"
    }
    comparisons: dict[str, dict[str, Any]] = {}
    for cid in shortlist:
        cand = by_id.get(cid)
        if cand is None:
            continue
        ref = defaults.get(cand["params"]["rung"])
        if ref is None or ref["cell_id"] == cid:
            continue
        primary = compare_cells(ref, cand, confirm, iters, metric=PRIMARY_METRIC)
        co = compare_cells(ref, cand, confirm, iters, metric=CO_PRIMARY_METRIC)
        comparisons[cid] = {"primary": primary, "co_primary": co}
    holm = (
        _stats.holm_bonferroni(
            {k: v["primary"]["wilcoxon_p"] for k, v in comparisons.items()},
            alpha=CONFIRM_ALPHA,
        )
        if comparisons
        else {}
    )
    for cid, v in comparisons.items():
        adj_p, rejected = holm.get(cid, (float("nan"), False))
        d = v["primary"]["diff_ci95"]["point"]
        co_lo = v["co_primary"]["diff_ci90"]["lo"]
        co_ok = co_lo > -DELTA
        v["holm_adj_p"] = adj_p
        v["holm_rejected"] = rejected
        v["co_primary_non_inferior"] = co_ok
        v["split_decision"] = bool(rejected and d >= DELTA and not co_ok)
        # DIFFERENT on the primary requires |D| >= delta AND Holm p < alpha
        # (protocol §7.4.3: magnitude alone is not enough, and neither is
        # significance alone). Recommending additionally requires the direction
        # to be an improvement and the co-primary not to be worse.
        v["confirmed_different"] = bool(rejected and abs(d) >= DELTA)
        v["recommended"] = bool(rejected and d >= DELTA and co_ok)
    return {
        "stage": "confirm",
        "split": "confirm",
        "alpha": CONFIRM_ALPHA,
        "family": "holm over the nominated shortlist, across the grid",
        "family_size": len(comparisons),
        "comparisons": comparisons,
        "recommended": sorted(
            k for k, v in comparisons.items() if v["recommended"]
        ),
        "split_decisions": sorted(
            k for k, v in comparisons.items() if v["split_decision"]
        ),
    }


# --------------------------------------------------------------------------- #
# The A/A replicate gate (protocol §6.4 / §7.4.4)
# --------------------------------------------------------------------------- #
def aa_gate(
    replicates: list[dict[str, Any]], *, delta: float = DELTA
) -> dict[str, Any]:
    """Is δ above the noise floor? Three replicates of one cell, per protocol §6.4.

    "δ must exceed 3× the A/A SD; if it does not, the experiment is
    under-resolved and the query set must be enlarged before any claim is made."
    Two runs of the *same* configuration against the same frozen index should
    agree exactly; where they do not, the residual is HNSW nondeterminism under
    concurrent optimizer activity (threat T4), and it bounds what any Δ can
    mean. RBO@20 is reported alongside the SD because a rank-order wobble deep in
    the list can move a metric without meaning the retrieval changed.
    """
    means = [r["means"][PRIMARY_METRIC] for r in replicates]
    if len(means) < 2:
        return {
            "ran": False, "n_replicates": len(means),
            "reason": "fewer than 2 replicates; protocol §6.4 requires 3",
            "passed": None, "sd": None, "threshold": None,
            "rbo_mean": None, "cell_id": replicates[0]["cell_id"] if replicates else None,
        }
    sd = statistics.stdev(means)
    rbos: list[float] = []
    for i in range(len(replicates) - 1):
        a, b = replicates[i].get("rankings", {}), replicates[i + 1].get("rankings", {})
        for qid in sorted(set(a) & set(b)):
            rbos.append(_stats.rbo(a[qid][:AA_RBO_DEPTH], b[qid][:AA_RBO_DEPTH],
                                   p=AA_RBO_P))
    return {
        "ran": True,
        "cell_id": replicates[0]["cell_id"],
        "n_replicates": len(means),
        "metric": PRIMARY_METRIC,
        "means": means,
        "sd": sd,
        "threshold": delta / AA_SD_FACTOR,
        "sd_factor": AA_SD_FACTOR,
        "rbo_mean": (statistics.fmean(rbos) if rbos else None),
        "rbo_depth": AA_RBO_DEPTH,
        "passed": delta > AA_SD_FACTOR * sd,
    }


# --------------------------------------------------------------------------- #
# The recommendation gate
# --------------------------------------------------------------------------- #
def recommendation_gate(
    primary: dict[str, Any] | None,
    libs: dict[str, Any],
    aa: dict[str, Any],
    git: dict[str, Any],
    rung_summaries: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """May this run emit a ``LibraryRetrievalDefaults`` recommendation at all?

    A recommendation is a normative claim about production configuration
    (protocol §11). It must not be emitted from a regime in which the thing being
    claimed is not measurable, and the harness — not the reader of a markdown
    banner — is where that has to be enforced.

    The blocking conditions, each of which independently makes the deliverable
    unsupportable:

    * **HNSW was never built at the decision rung.** Then H1b is vacuous (an
      exact brute-force scan cannot exhibit approximate-search truncation, so a
      dense §5.4 PASS confirms only that exact search is exact); the retrieval
      task is so far from saturation-free that nDCG sits at 0.94–0.98, which puts
      δ = 0.02 *inside* the ceiling where a real improvement has no room to
      appear; and the depth parameters this experiment is about only bind on an
      approximate index.
    * **HNSW status unknown.** Same treatment: a missing measurement is not a
      passing one.
    * **The A/A resolution gate did not run or did not pass** (protocol §6.4:
      δ must exceed 3× the A/A SD).
    * **A primary cell is void** under §5.4, or the rung's reference was
      substituted for a non-default cell.
    * **The working tree is dirty** — a published number must be traceable to
      committed code (§8.1).
    """
    reasons: list[str] = []
    rung = f"n{primary['n_docs']}" if primary else None
    lib = libs.get(rung or "", {}) if isinstance(libs, dict) else {}
    regime = scale_regime_for(lib.get("hnsw"))
    if regime == REGIME_BRUTE_FORCE:
        reasons.append(
            f"HNSW was never built at the decision rung `{rung}` "
            f"(indexed_vectors=0 below Qdrant's indexing_threshold): the dense leg "
            f"ran as an exact brute-force scan, so H1b is vacuous, the depth "
            f"parameters under test do not bind, and nDCG saturates where "
            f"δ={DELTA} sits inside the ceiling"
        )
    elif regime == REGIME_UNKNOWN:
        reasons.append(
            f"HNSW status at the decision rung `{rung}` is UNKNOWN (Qdrant "
            f"telemetry missing or errored) — a missing measurement is not a "
            f"passing one"
        )
    if primary is None:
        reasons.append("the primary comparison is not computable in this run")
    elif not primary.get("valid"):
        reasons.append("a primary cell failed the §5.4 hit-deficit assertion")
    if primary is not None:
        ceiling = 1.0 - DELTA
        if primary.get("reference_mean", 0.0) > ceiling:
            reasons.append(
                f"the reference cell scores {primary['reference_mean']:.3f} "
                f"{PRIMARY_METRIC}, above the 1−δ={ceiling:.2f} ceiling: an "
                f"improvement of δ={DELTA} does not fit in the remaining headroom"
            )
    if not aa.get("ran"):
        reasons.append(
            f"the A/A resolution gate did not run ({aa.get('reason', 'no replicates')}); "
            f"protocol §6.4 requires δ > {AA_SD_FACTOR}× the A/A SD before any claim"
        )
    elif not aa.get("passed"):
        reasons.append(
            f"the A/A resolution gate FAILED: SD={aa['sd']:.4f}, "
            f"δ={DELTA} is not above {AA_SD_FACTOR}×SD={AA_SD_FACTOR * aa['sd']:.4f} "
            f"— the experiment is under-resolved, enlarge the query set"
        )
    substituted = sorted(
        r for r, s in rung_summaries.items() if s.get("reference_substituted")
    )
    if substituted:
        reasons.append(
            f"the shipping-default reference was substituted at rung(s) "
            f"{', '.join(substituted)} — those rungs' directional claims are void"
        )
    if git.get("dirty"):
        reasons.append(
            f"the working tree is dirty ({len(git.get('dirty_files') or [])} file(s), "
            f"digest {git.get('dirty_digest')}): a published number must be "
            f"reproducible from a commit"
        )
    return {
        "permitted": not reasons,
        "blocked_reasons": reasons,
        "decision_rung": rung,
        "decision_rung_regime": regime,
        "decision_rung_hnsw_built": hnsw_state(lib.get("hnsw")),
    }


def primary_comparison(
    cells: list[dict[str, Any]], iters: int, keep: set[str] | None = None
) -> dict[str, Any] | None:
    """The one **designated** primary comparison, at the largest rung present.

    Not pre-registered — see the module docstring and PROTOCOL.md amendment A4.
    Shipping defaults (D=10) vs the same cell at D=100, on ``PRIMARY_METRIC``.
    ``keep`` restricts to a query split; ``None`` uses every retained query and
    the result is labelled as pooled rather than confirmatory."""
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
    keep_set = keep if keep is not None else set(ref["query_ids"])
    out = compare_cells(ref, alt, keep_set, iters, metric=PRIMARY_METRIC)
    co = compare_cells(ref, alt, keep_set, iters, metric=CO_PRIMARY_METRIC)
    out["n_docs"] = largest
    out["rung"] = f"n{largest}"
    out["split"] = "confirm" if keep is not None else "pooled"
    out["preregistered"] = False
    out["designation"] = "designated primary (see PROTOCOL.md amendment A4)"
    out["co_primary"] = co
    # Protocol §5.1: a recommendation must not be worse on EITHER primary.
    out["co_primary_non_inferior"] = co["diff_ci90"]["lo"] > -DELTA
    return out


def write_outputs(
    out_dir: Path,
    manifest: dict[str, Any],
    cell_results: list[dict[str, Any]],
    iters: int,
    *,
    split: dict[str, Any],
    aa: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write manifest + per-cell artefacts + CSV + report into the run directory.

    Runs the full protocol §6.4/§7.2 pipeline: stage-1 BH screen on the tune
    split, mechanical nomination, stage-2 Holm confirm on the held-out split with
    the co-primary voting, the A/A resolution gate, and the recommendation gate."""
    (out_dir / "cells").mkdir(parents=True, exist_ok=True)
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)
    tune, confirm = set(split["tune"]), set(split["confirm"])
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
                    "regime": c["regime"],
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
        # Protocol §8.3: the top-200 ranking per query is the highest-value
        # artefact — every metric at every k is recomputable from it offline.
        with (out_dir / "raw" / f"{c['cell_id']}.rankings.jsonl").open(
            "w", encoding="utf-8"
        ) as fh:
            for qid in c["query_ids"]:
                fh.write(
                    json.dumps(
                        {"query_id": qid, "chunk_ids": c["rankings"].get(qid, [])},
                        sort_keys=True,
                    )
                    + "\n"
                )

    rungs = sorted({c["params"]["rung"] for c in cell_results},
                   key=lambda r: int(r[1:]))
    sections, summaries = [], {}
    for rung in rungs:
        subset = [c for c in cell_results if c["params"]["rung"] == rung]
        table, interp, summary = screen_rung(subset, tune, iters)
        summaries[rung] = summary
        sections.append(f"### Rung `{rung}`\n\n{table}\n{interp}\n")

    nominations = nominate(cell_results, summaries, tune)
    stage2 = confirm_stage2(cell_results, nominations["shortlist"], confirm, iters)
    primary = primary_comparison(cell_results, iters, keep=confirm)
    aa = aa or {"ran": False, "reason": "no A/A replicates were requested",
                "passed": None}
    gate = recommendation_gate(
        primary, manifest.get("libraries", {}), aa, manifest.get("git", {}), summaries
    )

    manifest["finished_at"] = datetime.now(UTC).isoformat()
    manifest["query_split"] = {
        k: v for k, v in split.items() if k not in ("tune", "confirm")
    } | {"n_tune": len(tune), "n_confirm": len(confirm)}
    manifest["primary_comparison_result"] = primary
    manifest["stage1_screen"] = summaries
    manifest["nomination"] = nominations
    manifest["stage2_confirm"] = stage2
    manifest["aa_gate"] = aa
    manifest["recommendation_gate"] = gate
    manifest["regime_by_cell"] = {c["cell_id"]: c["regime"] for c in cell_results}
    manifest["sanity_by_cell"] = {c["cell_id"]: c["sanity"] for c in cell_results}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    with (out_dir / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["cell_id", "n_docs", "rung", "mode", "rrf_k", "leg_depth",
             "shippable_triples", "rerank_enabled", "rerank_candidates",
             # Machine-readable measurability regime — review finding 4.
             "scale_regime", "hnsw_built", "chunks_per_doc", "n_chunks",
             "verdict", "n_queries", PRIMARY_METRIC, CO_PRIMARY_METRIC, "recall@10",
             "recall@100", "map", "mrr@10", "mean_union_depth", "mean_overlap",
             "dense_starved_rate", "bm25_starved_rate",
             "dense_deficit_rate", "bm25_deficit_rate", "p50_ms", "p95_ms"]
        )
        for c in cell_results:
            p, m, s, cost, g = (
                c["params"], c["means"], c["sanity"], c["cost"], c["regime"]
            )
            w.writerow([
                c["cell_id"], p["n_docs"], p["rung"], p["mode"], p["rrf_k"],
                p["leg_depth"], _fmt_triples(p["shippable_triples"]),
                p["rerank_enabled"], p["rerank_candidates"],
                g["scale_regime"], g["hnsw_built"],
                round(g["chunks_per_doc"], 3), g["n_chunks"],
                s["verdict"], c["n_queries"],
                round(m[PRIMARY_METRIC], 4), round(m[CO_PRIMARY_METRIC], 4),
                round(m["recall@10"], 4), round(m["recall@100"], 4),
                round(m["map"], 4), round(m["mrr@10"], 4),
                round(cost["mean_union_depth"], 2), round(cost["mean_overlap"], 2),
                round(s["dense_starved_rate"], 4), round(s["bm25_starved_rate"], 4),
                round(s["dense_deficit_rate"], 4), round(s["bm25_deficit_rate"], 4),
                round(cost["p50_query_ms"], 1), round(cost["p95_query_ms"], 1),
            ])

    (out_dir / "report.md").write_text(
        _report_body(manifest, cell_results, primary, sections, nominations,
                     stage2, aa, gate),
        encoding="utf-8",
    )
    return {"primary": primary or {}, "gate": gate, "stage2": stage2, "aa": aa}


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
    # THREE states, never two. A rung whose telemetry errored has no `hnsw_built`
    # key at all; bucketing it with "built" would turn a telemetry outage into an
    # affirmative claim that the dense leg ran on a real HNSW graph.
    by_regime: dict[str, list[str]] = {}
    for r, v in libs.items():
        by_regime.setdefault(scale_regime_for(v.get("hnsw")), []).append(r)
    unbuilt = sorted(by_regime.get(REGIME_BRUTE_FORCE, []))
    unknown = sorted(by_regime.get(REGIME_UNKNOWN, []))
    built = sorted(by_regime.get(REGIME_HNSW, []))
    if unbuilt:
        lines.append(
            f"> - **HNSW was never built** at rung(s) {', '.join(unbuilt)} "
            f"(points below Qdrant's `indexing_threshold`), so the dense leg ran "
            f"as an exact brute-force scan. Every §5.4 dense verdict at those "
            f"rungs is therefore vacuous with respect to approximate-search "
            f"truncation — it confirms only that exact search is exact."
        )
    if unknown:
        lines.append(
            f"> - **HNSW status UNKNOWN — do not quote** at rung(s) "
            f"{', '.join(unknown)}: the Qdrant collection telemetry was missing or "
            f"errored, so this run cannot say whether the dense leg ran on an "
            f"approximate graph or an exact scan. This is *not* evidence that the "
            f"graph was built; it is the absence of the measurement."
        )
    if built and not unbuilt and not unknown:
        lines.append(
            "> - HNSW was built at every rung; dense results are approximate."
        )
    elif built:
        lines.append(
            f"> - HNSW was built at rung(s) {', '.join(built)}; dense results are "
            f"approximate there and only there."
        )
    if not libs:
        lines.append(
            "> - No library telemetry recorded — **HNSW status unknown, do not "
            "quote** any dense result from this run."
        )
    return "\n".join(lines) + "\n"


def _gate_banner(gate: dict[str, Any]) -> str:
    """The recommendation gate, rendered where a reader cannot miss it."""
    if gate.get("permitted"):
        return (
            "> ✅ **Recommendation gate: OPEN.** Every precondition in protocol "
            "§6.4/§11 is satisfied, so a `LibraryRetrievalDefaults` block may be "
            "derived from the stage-2 table below.\n"
        )
    reasons = "\n".join(f"> {i}. {r}" for i, r in enumerate(gate["blocked_reasons"], 1))
    return (
        "> ⛔ **NO RECOMMENDATION MAY BE EMITTED FROM THIS RUN.**\n>\n"
        "> The harness refuses to derive a `LibraryRetrievalDefaults` block "
        "(protocol §11) because this run is in a regime where the claim is not "
        f"measurable. Decision rung `{gate.get('decision_rung')}`, scale regime "
        f"`{gate.get('decision_rung_regime')}`. Blocking conditions:\n>\n"
        f"{reasons}\n>\n"
        "> Numbers below remain valid as *descriptions of this run*. None of them "
        "may be used to change a shipping default.\n"
    )


def _stage2_section(nominations, stage2) -> str:
    if not nominations.get("shortlist"):
        return (
            "No candidate was nominated: "
            f"{nominations.get('note', 'the stage-1 screen flagged nothing usable')}.\n"
        )
    lines = [
        "**Shortlist** (protocol §7.2, applied mechanically to the stage-1 output):",
        "",
    ]
    lines += [
        f"- `{cid}` — {nominations['rationale'].get(cid, '')}"
        for cid in nominations["shortlist"]
    ]
    comps = stage2.get("comparisons", {})
    if not comps:
        lines.append(
            "\nNo confirmatory comparison ran (the shortlist holds only the "
            "shipping default).\n"
        )
        return "\n".join(lines) + "\n"
    lines += [
        "",
        f"Holm–Bonferroni at α={CONFIRM_ALPHA} over exactly these "
        f"{stage2['family_size']} comparison(s), **one family across the grid**, "
        f"on the held-out confirm split.",
        "",
        f"| candidate | Δ{PRIMARY_METRIC} [95% CI] | Holm adj p | "
        f"Δ{CO_PRIMARY_METRIC} 90% lo | co-primary not worse | verdict | recommend |",
        "|---|---|---|---|---|---|---|",
    ]
    for cid, v in sorted(comps.items()):
        d = v["primary"]["diff_ci95"]
        lines.append(
            f"| `{cid}` | {d['point']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}] | "
            f"{v['holm_adj_p']:.4f} | "
            f"{v['co_primary']['diff_ci90']['lo']:+.4f} | "
            f"{'yes' if v['co_primary_non_inferior'] else '**NO**'} | "
            f"{v['primary']['verdict']} | "
            f"{'yes' if v['recommended'] else 'no'} |"
        )
    if stage2.get("split_decisions"):
        lines.append(
            f"\n**Split decision** on {', '.join('`' + k + '`' for k in stage2['split_decisions'])}: "
            f"the candidate clears {PRIMARY_METRIC} but is worse on the co-primary "
            f"{CO_PRIMARY_METRIC}. Protocol §5.1 resolves splits in favour of "
            f"nDCG@5 — these are **not** recommended, and the split is reported "
            f"explicitly as §5.1 requires."
        )
    return "\n".join(lines) + "\n"


def _aa_section(aa: dict[str, Any]) -> str:
    if not aa.get("ran"):
        return (
            f"**Not run** — {aa.get('reason', 'no replicates')}. Protocol §6.4 "
            f"requires 3 replicates of one reference cell per rung and δ > "
            f"{AA_SD_FACTOR}× the A/A SD before any claim; without it the "
            f"resolution of this experiment is unmeasured and the recommendation "
            f"gate stays shut.\n"
        )
    verdict = "PASS" if aa["passed"] else "**FAIL — experiment under-resolved**"
    rbo = f"{aa['rbo_mean']:.4f}" if aa["rbo_mean"] is not None else "n/a"
    return (
        f"{aa['n_replicates']} replicate(s) of `{aa['cell_id']}`, "
        f"{PRIMARY_METRIC} = {', '.join(f'{m:.4f}' for m in aa['means'])}.\n\n"
        f"- A/A SD = **{aa['sd']:.5f}**; δ = {DELTA} must exceed "
        f"{AA_SD_FACTOR}×SD = {AA_SD_FACTOR * aa['sd']:.5f} → {verdict}\n"
        f"- mean RBO@{aa['rbo_depth']} between consecutive replicates = {rbo}\n"
    )


def _report_body(manifest, cell_results, primary, sections, nominations,
                 stage2, aa, gate) -> str:
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
            f"- reference (`{primary['reference_cell']}`, D={DEFAULT_DEPTH}): "
            f"{primary['reference_mean']:.4f}\n"
            f"- candidate (`{primary['candidate_cell']}`, D={PRIMARY_DEPTH}): "
            f"{primary['candidate_mean']:.4f}\n"
            f"- Δ{PRIMARY_METRIC} = {primary['diff_ci95']['point']:+.4f} "
            f"[{primary['diff_ci95']['lo']:+.4f}, {primary['diff_ci95']['hi']:+.4f}] "
            f"(95% paired bootstrap, {primary['split']} split, "
            f"n={primary['n_queries']}, "
            f"{primary['n_discriminating']} discriminating)\n"
            f"- Δ{CO_PRIMARY_METRIC} 90% CI lower bound = "
            f"{primary['co_primary']['diff_ci90']['lo']:+.4f} → co-primary "
            f"{'not worse' if primary['co_primary_non_inferior'] else '**WORSE** (§5.1 split decision)'}\n"
            f"- Wilcoxon p = {primary['wilcoxon_p']:.4f}, δ = {DELTA}\n"
            f"- **verdict: {primary['verdict']}**"
            f"{'' if primary['valid'] else '  — VOID, a cell failed the §5.4 assertion'}\n"
            + (f"- verdict reason: {primary['verdict_reason']}\n"
               if primary.get("verdict_reason") else "")
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

{_gate_banner(gate)}
## Designated primary comparison

**Not pre-registered.** This specific pair was chosen when the harness was
written, after the protocol was hashed; PROTOCOL.md amendment A4 records it as a
*designated* primary. It carries the weight of a single pre-specified
comparison — one test, one δ, no selection over the grid — but it does **not**
carry the weight of pre-registration, and it is labelled that way everywhere it
appears.

Shipping defaults (per-leg depth D={DEFAULT_DEPTH}, realizable as
`{_fmt_triples(shippable_triples(DEFAULT_DEPTH, False, None))}`) vs. the identical
cell at D={PRIMARY_DEPTH} (`{_fmt_triples(shippable_triples(PRIMARY_DEPTH, False, None))}`),
document-level {PRIMARY_METRIC}, largest library size in the run, on the
**held-out confirm split**.

{pc}
## Query split (protocol §6.4)

{TUNE_FRACTION:.0%} tune / {1 - TUNE_FRACTION:.0%} confirm, stratified by
per-query {PRIMARY_METRIC} difficulty quintiles under the shipping default,
seed {SPLIT_SEED}. n_tune={manifest['query_split']['n_tune']},
n_confirm={manifest['query_split']['n_confirm']}, fixture
`{manifest['query_split'].get('path')}` @ `{manifest['query_split'].get('sha256')}`
({manifest['query_split'].get('source')}).

## A/A resolution gate (protocol §6.4)

{_aa_section(aa)}
## Stage 2 — confirmatory (protocol §7.2)

{_stage2_section(nominations, stage2)}
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
## Stage 1 — exploratory screen by rung (NOT a result)

Benjamini–Hochberg FDR at q={SCREEN_Q} on the **tune** split only, per protocol
§7.2. Nothing in this section may change a shipping default; only the stage-2
table above can, and only when the recommendation gate is open.

{''.join(sections)}
## Reproducing

Full `argv`, seeds, package versions, HNSW/ES index telemetry, reranker model +
revision, query-vector cache digest and per-library `CollectionManifest`s are in
`manifest.json`. Per-cell per-query arrays are in `cells/<cell_id>.json`
(`chunk_one`-compatible, so `scripts/eval/aggregate_stats.py` reads them);
per-query Track-C counters are in `raw/<cell_id>.counters.jsonl`; the top-{RANKING_DEPTH}
ranking per query is in `raw/<cell_id>.rankings.jsonl`, from which every metric at
every k is recomputable offline.
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
    # Cross-rung pairing is the design's justification (protocol §4.3i), so it is
    # verified rather than asserted in prose.
    ordered = sorted(samples, key=lambda s: s.n_docs)
    nesting = [
        {"smaller": a.rung, "bigger": b.rung, "nests": a.nests_within(b)}
        for a, b in zip(ordered[:-1], ordered[1:], strict=True)
    ]
    for n in nesting:
        if not n["nests"]:
            print(
                f"[sample] WARNING {n['smaller']} does NOT nest within "
                f"{n['bigger']}: cross-rung differences are not paired and the "
                f"H3 difference-in-differences is invalid at this ladder.",
                flush=True,
            )

    grid = build_grid(
        args.doc_counts, args.modes, args.rrf_k, args.depths,
        args.rerank, args.rerank_candidates,
    )
    print(
        f"[grid] {len(grid)} cells over {len(samples)} librar(ies) — swept factor "
        f"is absolute per-leg depth D {sorted({c.depth for c in grid})}",
        flush=True,
    )

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
                idx = await build_library_index(
                    s, docs_by_id, client, created=created
                )
                indexes[s.rung] = idx

            _, spec_hash, _ = build_spec()
            wanted = {queries[q] for s in samples for q in s.query_ids}
            qvectors, qvec_cache = await load_query_vectors(
                {q: queries[q] for s in samples for q in s.query_ids},
                client, args.cache_dir, spec_hash,
            )
            missing = wanted - set(qvectors)
            if missing:
                raise SystemExit(f"query-vector cache missing {len(missing)} entries")

            lib_manifest: dict[str, dict[str, Any]] = {}
            for rung, idx in indexes.items():
                await measure_matchable(idx, queries, client)
                # Telemetry lands on the LibraryIndex first so every cell record
                # can carry its measurement regime (review finding 4).
                idx.hnsw = await qdrant_index_info(idx.collection)
                idx.es = await es_index_info(idx.es_index)
                print(
                    f"[regime] {rung}: scale_regime={idx.scale_regime} "
                    f"hnsw_built={idx.hnsw_built} "
                    f"chunks_per_doc={idx.chunks_per_doc:.2f}",
                    flush=True,
                )
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
                    "hnsw": idx.hnsw,
                    "es": idx.es,
                    "scale_regime": idx.scale_regime,
                    "hnsw_built": idx.hnsw_built,
                    "distractors": idx.distractors,
                    "build_s": idx.build_s,
                }

            manifest = build_run_manifest(
                run_id=run_id, argv=argv, args=args,
                dataset=dataset_provenance(corpus_docs, queries, qrels, source),
                indexes=lib_manifest, grid=grid, started_at=started_at,
                qvec_cache=qvec_cache,
                reranker=await reranker_provenance(c7.RERANKER_URL),
            )
            manifest["sample_nesting"] = nesting
            if manifest["git"]["dirty"] and args.require_clean:
                raise SystemExit(
                    "REFUSING to run: --require-clean was passed and the working "
                    f"tree is dirty ({len(manifest['git']['dirty_files'])} file(s), "
                    f"digest {manifest['git']['dirty_digest']}). A publishable "
                    "artefact must be reproducible from a commit."
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

            # --- A/A null (protocol §6.4): re-run ONE reference cell -------- #
            largest = max(indexes.values(), key=lambda i: i.sample.n_docs)
            ref_cell = next(
                (c for c in grid if c.rung == largest.sample.rung and c.is_default),
                None,
            )
            aa: dict[str, Any] = {
                "ran": False, "passed": None,
                "reason": "--aa-replicates < 2" if args.aa_replicates < 2
                          else "no shipping-default cell at the largest rung",
            }
            if ref_cell is not None and args.aa_replicates >= 2:
                reps = [
                    r for r in cell_results if r["cell_id"] == ref_cell.cell_id
                ]
                for r in range(len(reps), args.aa_replicates):
                    print(
                        f"[a/a] replicate {r + 1}/{args.aa_replicates} of "
                        f"{ref_cell.cell_id}", flush=True,
                    )
                    reps.append(
                        await evaluate_cell(
                            ref_cell, largest, queries, qrels, qvectors, reranker,
                            rerank_cache, concurrency=args.concurrency,
                        )
                    )
                aa = aa_gate(reps)
                print(
                    f"[a/a] SD={aa['sd']:.5f} threshold={aa['threshold']:.5f} "
                    f"rbo={aa['rbo_mean']} → "
                    f"{'PASS' if aa['passed'] else 'FAIL (under-resolved)'}",
                    flush=True,
                )

            # --- Query split (protocol §6.4) -------------------------------- #
            smallest = min(indexes.values(), key=lambda i: i.sample.n_docs)
            probe = next(
                (
                    c for c in cell_results
                    if c["params"]["rung"] == smallest.sample.rung
                    and Cell(**_cell_kwargs(c)).is_default
                ),
                None,
            ) or cell_results[0]
            difficulty = dict(
                zip(probe["query_ids"], probe["per_query"][PRIMARY_METRIC],
                    strict=True)
            )
            split = load_or_write_split(
                args.split_fixture,
                list(probe["query_ids"]),
                difficulty,
                queries_sha256=manifest["dataset"]["queries_sha256"],
                difficulty_cell=probe["cell_id"],
            )
            print(
                f"[split] {len(split['tune'])} tune / {len(split['confirm'])} "
                f"confirm ({split['source']}, stratified on {probe['cell_id']})",
                flush=True,
            )

            out = write_outputs(
                out_dir, manifest, cell_results, args.bootstrap_iters,
                split=split, aa=aa,
            )
            primary, gate = out["primary"], out["gate"]
            print("\n" + "=" * 78)
            print(f"G1 sweep complete — {len(cell_results)} cells → {out_dir}")
            if primary:
                d = primary["diff_ci95"]
                void = "" if primary["valid"] else "  [VOID — §5.4 hit deficit]"
                print(
                    f"DESIGNATED PRIMARY (not pre-registered; {primary['n_docs']} "
                    f"docs, {PRIMARY_METRIC}, {primary['split']} split): "
                    f"{d['point']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}] "
                    f"p={primary['wilcoxon_p']:.4f} → {primary['verdict']}{void}"
                )
                if primary.get("verdict_reason"):
                    print(f"  reason: {primary['verdict_reason']}")
                if not primary["co_primary_non_inferior"]:
                    print(
                        f"  §5.1 SPLIT DECISION: worse on the co-primary "
                        f"{CO_PRIMARY_METRIC}"
                    )
            if gate["permitted"]:
                print("RECOMMENDATION GATE: OPEN")
            else:
                print("RECOMMENDATION GATE: **SHUT** — no LibraryRetrievalDefaults "
                      "may be derived from this run:")
                for i, r in enumerate(gate["blocked_reasons"], 1):
                    print(f"  {i}. {r}")
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
    p.add_argument("--depths", type=_int_list,
                   default=[DEFAULT_DEPTH, PRIMARY_DEPTH],
                   help="ABSOLUTE per-leg depths D to sweep (protocol §6.2). "
                        "top_k and candidate_multiplier are NOT swept "
                        "independently — only their product reaches the "
                        "retriever, so sweeping both emits duplicate cells. The "
                        "shippable (top_k, multiplier) triples for each D are "
                        "reported per cell.")
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
    p.add_argument("--split-fixture", type=Path,
                   default=REPORT_ROOT / "fixtures" / f"g1_{DATASET_NAME}_split.json",
                   help="pinned tune/confirm split (protocol §6.4); derived and "
                        "written on first use, authoritative thereafter")
    p.add_argument("--aa-replicates", type=int, default=AA_REPLICATES,
                   help="A/A null replicates of the reference cell at the largest "
                        "rung (protocol §6.4). <2 disables the gate, which in turn "
                        "blocks any recommendation.")
    p.add_argument("--require-clean", action="store_true",
                   help="refuse to run from a dirty working tree (use when the "
                        "output is intended for publication)")
    p.add_argument("--keep", action="store_true",
                   help="keep the g1_* stores (default: torn down in a finally:)")
    p.add_argument("--smoke", action="store_true",
                   help="minimal grid: the two designated primary cells only")
    p.add_argument("--endpoints", default=None,
                   help="comma-separated SFR base URLs (else the built-in 16)")
    p.add_argument("--embedding-api-key", default=None)
    p.add_argument("--qdrant-url", default=c7.QDRANT_URL,
                   help="Qdrant base URL the g1_* scratch collections are built in "
                        "(default: the chunking_compare_7way constant). guard_scratch "
                        "guards NAMES, not hosts — point this at a non-production "
                        "instance.")
    p.add_argument("--es-url", default=c7.ES_URL,
                   help="Elasticsearch base URL for the g1_* scratch indices "
                        "(same caveat as --qdrant-url)")
    p.add_argument("--hard-cap-tokens", type=int, default=c7.HARD_CAP_TOKENS)
    args = p.parse_args(argv)
    args.modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    bad = set(args.modes) - {"hybrid", "vector", "bm25"}
    if bad:
        p.error(f"unknown --modes {sorted(bad)}")
    if args.smoke:
        args.modes = ["hybrid"]
        args.rrf_k = [DEFAULT_RRF_K]
        args.depths = [DEFAULT_DEPTH, PRIMARY_DEPTH]
        args.rerank = [False]
    return args


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv) if argv is None else ["g1_library_sweep.py", *argv]
    args = parse_args(argv)
    c7.HARD_CAP_TOKENS = args.hard_cap_tokens
    c7.EMBED_API_KEY = args.embedding_api_key or os.environ.get("OPENAI_API_KEY")
    # Stores are set here, before any index is built, so the manifest
    # (build_run_manifest reads c7.QDRANT_URL / c7.ES_URL) records the override.
    c7.QDRANT_URL = args.qdrant_url.rstrip("/")
    c7.ES_URL = args.es_url.rstrip("/")
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
