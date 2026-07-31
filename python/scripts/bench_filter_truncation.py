#!/usr/bin/env python3
"""G2 (docs/libraries-spec.md §-1) — does a Qdrant filtered vector search return
the number of results it was asked for, in the shape §4 will actually issue?

The v1 scoped-library filter is a CONJUNCTION::

    library_id == L  AND  tenant_id ANY [owner_of(L), "public"]

Issue #199 measured something else: a single key / single value at ~1%
selectivity over synthetic 128-d vectors, and saw HNSW return 1-5 hits out of 20
while ``exact:true`` found all 20. Wrong filter shape, wrong dimensionality,
wrong vector distribution — it does not transfer. This script re-measures with:

* **real 4096-d SFR vectors**, scrolled read-only out of a production
  collection (default ``ragstack_sfr_tok256``) and re-used as the corpus, so the
  embedding distribution is the true one;
* **synthetic payloads** (``library_id`` + ``tenant_id``) with keyword indexes on
  both, mirroring ``ragstack/stores/qdrant.py::_ensure_tenant_index``;
* a **selectivity sweep** from ~10^-2 down to ~10^-5 of the collection, crossed
  with several ``k``;
* **three filter shapes** (conjunction / library-only / tenant-only) so a failure
  can be attributed to the conjunction rather than to filtering per se;
* **ground truth** from the identical query with ``search_params.exact = True``,
  which bypasses HNSW.

Pass criterion, per cell: ``returned_hits == min(k, |match set|)``.

SAFETY
------
This script reads from production collections and writes ONLY to a scratch
collection whose name is forced to start with ``g2bench_``. It never writes to,
deletes from, reconfigures or optimizes any pre-existing collection. The scratch
collection is dropped in a ``finally:`` block unless ``--keep`` is passed;
``--cleanup-only`` drops every ``g2bench_*`` collection and exits.

Examples
--------
  # tiny smoke run
  python scripts/bench_filter_truncation.py --points 2000 --queries 2

  # the real sweep
  python scripts/bench_filter_truncation.py --points 200000 \
      --json-out /tmp/g2.json

  # emergency broom
  python scripts/bench_filter_truncation.py --cleanup-only
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    HnswConfigDiff,
    MatchAny,
    MatchValue,
    OptimizersConfigDiff,
    PayloadSchemaType,
    PointStruct,
    SearchParams,
    VectorParams,
)

# --------------------------------------------------------------------------- #
# Safety rails
# --------------------------------------------------------------------------- #

SCRATCH_PREFIX = "g2bench_"


def guard_scratch(name: str) -> str:
    """Refuse to touch anything that is not unmistakably ours."""
    if not name.startswith(SCRATCH_PREFIX):
        raise SystemExit(
            f"REFUSING to mutate collection {name!r}: scratch collections MUST be "
            f"named {SCRATCH_PREFIX}*"
        )
    return name


# --------------------------------------------------------------------------- #
# Filter construction — must mirror stores/qdrant.py::_build_filter
# --------------------------------------------------------------------------- #

LIBRARY_FIELD = "library_id"
TENANT_FIELD = "tenant_id"
PUBLIC_TENANT = "public"


def build_filter(filters: dict[str, Any]) -> Filter | None:
    """Byte-for-byte the semantics of ``ragstack.stores.qdrant._build_filter``:
    a list value is ``MatchAny`` (empty list included — it matches nothing), a
    scalar is ``MatchValue``, all conditions go into ``must`` in dict-insertion
    order (scope keys pinned last)."""
    conditions: list[Any] = []
    for key, value in filters.items():
        if isinstance(value, (list, tuple, set)):
            conditions.append(FieldCondition(key=key, match=MatchAny(any=list(value))))
        else:
            conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
    return Filter(must=conditions) if conditions else None


# shape name -> (description, filters-dict builder)
SHAPES: dict[str, str] = {
    "conjunction": "library_id == L AND tenant_id ANY [owner, public]   (the v1 shape)",
    "library_only": "library_id == L                                     (#199's shape)",
    "tenant_only": "tenant_id ANY [owner, public]                       (today's shape)",
}


def shape_filters(shape: str, library_id: str, owner: str) -> dict[str, Any]:
    if shape == "conjunction":
        # §4: scope keys pinned LAST, exactly as tenancy.scope_filters does.
        return {LIBRARY_FIELD: library_id, TENANT_FIELD: [owner, PUBLIC_TENANT]}
    if shape == "library_only":
        return {LIBRARY_FIELD: library_id}
    if shape == "tenant_only":
        return {TENANT_FIELD: [owner, PUBLIC_TENANT]}
    raise ValueError(f"unknown filter shape {shape!r}")


# --------------------------------------------------------------------------- #
# Result records
# --------------------------------------------------------------------------- #


@dataclass
class Trial:
    library_id: str
    query_idx: int
    match_size: int  # exact count(filter) for THIS library
    expected: int  # min(k, match_size) — the pass criterion
    hits: int
    exact_hits: int
    overlap: int
    recall: float
    ms: float
    exact_ms: float

    @property
    def ok(self) -> bool:
        return self.hits == self.expected


@dataclass
class Cell:
    shape: str
    level: str  # e.g. "1e-04"
    library_size: int  # |library| as built
    match_size: int  # exact count of the filter's match set (mean over libs)
    k: int
    trials: list[Trial] = field(default_factory=list)

    # -- aggregates ------------------------------------------------------- #
    @property
    def expected(self) -> int:
        return min(self.k, self.match_size)

    @property
    def hits_mean(self) -> float:
        return _mean([t.hits for t in self.trials])

    @property
    def hits_min(self) -> int:
        return min((t.hits for t in self.trials), default=0)

    @property
    def exact_mean(self) -> float:
        return _mean([t.exact_hits for t in self.trials])

    @property
    def exact_min(self) -> int:
        return min((t.exact_hits for t in self.trials), default=0)

    @property
    def recall_mean(self) -> float:
        return _mean([t.recall for t in self.trials])

    @property
    def recall_min(self) -> float:
        return min((t.recall for t in self.trials), default=0.0)

    @property
    def n_fail(self) -> int:
        """Trials whose HNSW hit count missed min(k, |its own match set|)."""
        return sum(1 for t in self.trials if not t.ok)

    @property
    def n_exact_fail(self) -> int:
        """Trials where even ``exact:true`` missed min(k, |match|). If this is
        non-zero the payload count and the search disagree — read the raw JSON
        before believing anything else in the row."""
        return sum(1 for t in self.trials if t.exact_hits != t.expected)

    @property
    def passed(self) -> bool:
        return self.n_fail == 0

    def to_json(self) -> dict[str, Any]:
        return {
            "shape": self.shape,
            "level": self.level,
            "library_size": self.library_size,
            "match_size": self.match_size,
            "k": self.k,
            "expected": self.expected,
            "hits_mean": self.hits_mean,
            "hits_min": self.hits_min,
            "exact_hits_mean": self.exact_mean,
            "exact_hits_min": self.exact_min,
            "recall_mean": self.recall_mean,
            "recall_min": self.recall_min,
            "n_trials": len(self.trials),
            "n_fail": self.n_fail,
            "n_exact_fail": self.n_exact_fail,
            "pass": self.passed,
            "trials": [asdict(t) for t in self.trials],
        }


def _mean(xs: Sequence[float]) -> float:
    return float(sum(xs)) / len(xs) if xs else 0.0


# --------------------------------------------------------------------------- #
# Corpus construction
# --------------------------------------------------------------------------- #


@dataclass
class Library:
    library_id: str
    owner: str
    level: str
    size: int


def scroll_vectors(
    client: QdrantClient, collection: str, n: int, batch: int
) -> np.ndarray:
    """READ-ONLY scroll of ``n`` real vectors out of ``collection``.

    Payloads are not fetched (we synthesise our own). Returns float32
    ``(n, dim)``. 4096-d float32 is 16 KiB/point, so n=100k is ~1.6 GiB RSS.
    """
    out: list[np.ndarray] = []
    got = 0
    offset = None
    t0 = time.time()
    while got < n:
        want = min(batch, n - got)
        points, offset = client.scroll(
            collection_name=collection,
            limit=want,
            offset=offset,
            with_payload=False,
            with_vectors=True,
        )
        if not points:
            break
        for p in points:
            vec = p.vector
            if isinstance(vec, dict):  # named vectors — take the sole/default one
                vec = next(iter(vec.values()))
            if vec is None:
                continue
            out.append(np.asarray(vec, dtype=np.float32))
        got = len(out)
        print(
            f"    scrolled {got}/{n} vectors ({time.time() - t0:.1f}s)",
            end="\r",
            flush=True,
        )
        if offset is None:
            break
    print()
    if not out:
        raise SystemExit(f"no vectors scrolled from {collection!r}")
    arr = np.vstack(out[:n])
    print(f"    got {arr.shape[0]} vectors of dim {arr.shape[1]} in {time.time() - t0:.1f}s")
    return arr


def plan_libraries(
    n_points: int, fractions: Sequence[float], libs_per_level: int
) -> list[Library]:
    """One set of target libraries per selectivity level. Sizes are deduped so a
    tiny ``--points`` does not produce four identical 1-point levels."""
    libs: list[Library] = []
    seen_sizes: set[int] = set()
    for frac in sorted(fractions, reverse=True):
        size = max(1, int(round(frac * n_points)))
        if size in seen_sizes:
            continue
        seen_sizes.add(size)
        level = f"{frac:.0e}"
        for i in range(libs_per_level):
            libs.append(
                Library(
                    library_id=f"lib_{level}_{i}",
                    owner=f"user_{level}_{i}",
                    level=level,
                    size=size,
                )
            )
    return libs


def assign_payloads(
    n_points: int,
    libs: Sequence[Library],
    rng: random.Random,
    public_frac: float,
    filler_libs: int,
    filler_tenants: int,
) -> list[dict[str, str]]:
    """Scatter the target libraries randomly across the whole id space (the #199
    failure mode is "many small buckets scattered over many segments", so we do
    NOT lay them out contiguously), then fill the remainder with decoy libraries
    and decoy tenants so ``library_id`` has realistic cardinality."""
    total_target = sum(lib.size for lib in libs)
    if total_target > n_points:
        raise SystemExit(
            f"target libraries need {total_target} points but only {n_points} "
            f"available; lower --libs-per-level or raise --points"
        )

    ids = list(range(n_points))
    rng.shuffle(ids)
    payloads: list[dict[str, str]] = [{} for _ in range(n_points)]

    cursor = 0
    for lib in libs:
        chunk = ids[cursor : cursor + lib.size]
        cursor += lib.size
        for pid in chunk:
            tenant = PUBLIC_TENANT if rng.random() < public_frac else lib.owner
            payloads[pid] = {LIBRARY_FIELD: lib.library_id, TENANT_FIELD: tenant}

    for pid in ids[cursor:]:
        tenant = (
            PUBLIC_TENANT
            if rng.random() < public_frac
            else f"filler_u{rng.randrange(filler_tenants)}"
        )
        payloads[pid] = {
            LIBRARY_FIELD: f"filler_lib_{rng.randrange(filler_libs)}",
            TENANT_FIELD: tenant,
        }
    return payloads


# --------------------------------------------------------------------------- #
# Collection lifecycle
# --------------------------------------------------------------------------- #


def create_scratch(
    client: QdrantClient,
    name: str,
    dim: int,
    args: argparse.Namespace,
) -> None:
    guard_scratch(name)
    if client.collection_exists(name):
        print(f"[!] scratch collection {name!r} already exists — dropping it (re-run)")
        client.delete_collection(name)

    hnsw = HnswConfigDiff(
        m=args.hnsw_m,
        ef_construct=args.ef_construct,
        full_scan_threshold=args.full_scan_threshold,
    )
    opt_kwargs: dict[str, Any] = {"indexing_threshold": args.indexing_threshold}
    if args.max_segment_size is not None:
        opt_kwargs["max_segment_size"] = args.max_segment_size
    if args.segment_number is not None:
        opt_kwargs["default_segment_number"] = args.segment_number
    opt = OptimizersConfigDiff(**opt_kwargs)

    print(f"[+] creating {name!r}: dim={dim} distance={args.distance} "
          f"hnsw(m={args.hnsw_m}, ef_construct={args.ef_construct}, "
          f"full_scan_threshold={args.full_scan_threshold}KB) "
          f"optimizers(indexing_threshold={args.indexing_threshold}KB, "
          f"max_segment_size={args.max_segment_size}, "
          f"default_segment_number={args.segment_number})")
    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(
            size=dim, distance=Distance[args.distance.upper()]
        ),
        hnsw_config=hnsw,
        optimizers_config=opt,
    )
    # Mirrors qdrant.py::_ensure_tenant_index — plus the library_id index that
    # §4 says ONLY ragstack_lib_v1 gets.
    for fname in (TENANT_FIELD, LIBRARY_FIELD):
        client.create_payload_index(
            collection_name=name,
            field_name=fname,
            field_schema=PayloadSchemaType.KEYWORD,
        )
    print(f"[+] keyword payload indexes requested on {TENANT_FIELD!r}, {LIBRARY_FIELD!r}")


def upsert_corpus(
    client: QdrantClient,
    name: str,
    vectors: np.ndarray,
    payloads: Sequence[dict[str, str]],
    batch: int,
) -> None:
    guard_scratch(name)
    n = vectors.shape[0]
    t0 = time.time()
    for start in range(0, n, batch):
        stop = min(start + batch, n)
        points = [
            PointStruct(
                id=i, vector=vectors[i].tolist(), payload=dict(payloads[i])
            )
            for i in range(start, stop)
        ]
        client.upsert(collection_name=name, points=points, wait=False)
        print(f"    upserted {stop}/{n} ({time.time() - t0:.1f}s)", end="\r", flush=True)
    print()
    # One final waited write so the WAL is flushed before we start polling.
    client.upsert(
        collection_name=name,
        points=[
            PointStruct(
                id=n - 1, vector=vectors[n - 1].tolist(), payload=dict(payloads[n - 1])
            )
        ],
        wait=True,
    )
    print(f"    upsert complete in {time.time() - t0:.1f}s")


def wait_ready(client: QdrantClient, name: str, n: int, timeout: float) -> dict[str, Any]:
    """Poll until the collection is green, both payload indexes cover every
    point, and the HNSW build has either finished or demonstrably settled.

    The settle check matters: ``indexed_vectors_count`` stays 0 forever when
    every segment is under ``optimizers.indexing_threshold``, and we must not
    confuse "no HNSW to truncate" with "HNSW did not truncate".
    """
    t0 = time.time()
    last: dict[str, Any] = {}
    stable = 0
    prev_indexed = -1
    while time.time() - t0 < timeout:
        info = client.get_collection(name)
        schema = info.payload_schema or {}
        indexed_payload = {
            f: int(getattr(schema.get(f), "points", 0) or 0)
            for f in (TENANT_FIELD, LIBRARY_FIELD)
        }
        last = {
            "status": str(getattr(info.status, "value", info.status)),
            "optimizer_status": str(info.optimizer_status),
            "segments_count": info.segments_count,
            "points_count": info.points_count,
            "indexed_vectors_count": info.indexed_vectors_count,
            "payload_index_points": indexed_payload,
        }
        indexed = int(last["indexed_vectors_count"] or 0)
        stable = stable + 1 if indexed == prev_indexed else 0
        prev_indexed = indexed
        base_ready = (
            last["status"] == "green"
            and (last["points_count"] or 0) >= n
            and all(v >= n for v in indexed_payload.values())
        )
        # Either HNSW covers the corpus, or it has not moved for 3 polls (~6s)
        # and never will — both are legitimate stopping points, but only the
        # first one makes the truncation question meaningful.
        ready = base_ready and (indexed >= n or stable >= 3)
        print(
            f"    status={last['status']} points={last['points_count']} "
            f"indexed_vectors={last['indexed_vectors_count']} "
            f"segments={last['segments_count']} "
            f"payload_idx={indexed_payload} ({time.time() - t0:.0f}s)",
            end="\r",
            flush=True,
        )
        if ready:
            print()
            last["hnsw_coverage"] = (indexed / n) if n else 0.0
            return last
        time.sleep(2.0)
    print()
    print(f"[!] wait_ready timed out after {timeout}s — measuring anyway: {last}")
    last["hnsw_coverage"] = (int(last.get("indexed_vectors_count") or 0) / n) if n else 0.0
    return last


def drop_scratch(client: QdrantClient, name: str) -> bool:
    guard_scratch(name)
    if not client.collection_exists(name):
        print(f"[=] {name!r} already absent")
        return True
    client.delete_collection(name)
    gone = not client.collection_exists(name)
    print(f"[{'+' if gone else '!'}] delete {name!r}: {'CONFIRMED GONE' if gone else 'STILL PRESENT'}")
    return gone


def cleanup_all(client: QdrantClient) -> int:
    names = [c.name for c in client.get_collections().collections]
    targets = [n for n in names if n.startswith(SCRATCH_PREFIX)]
    print(f"[=] collections: {names}")
    if not targets:
        print(f"[=] no {SCRATCH_PREFIX}* collections to remove")
        return 0
    for n in targets:
        drop_scratch(client, n)
    return len(targets)


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #


def run_one(
    client: QdrantClient,
    name: str,
    query: Sequence[float],
    qfilter: Filter | None,
    k: int,
    hnsw_ef: int | None,
) -> tuple[list[Any], float]:
    params = SearchParams(hnsw_ef=hnsw_ef) if hnsw_ef is not None else None
    t0 = time.perf_counter()
    resp = client.query_points(
        collection_name=name,
        query=list(query),
        limit=k,
        query_filter=qfilter,
        with_payload=False,
        search_params=params,
    )
    return [p.id for p in resp.points], (time.perf_counter() - t0) * 1000.0


def run_exact(
    client: QdrantClient,
    name: str,
    query: Sequence[float],
    qfilter: Filter | None,
    k: int,
) -> tuple[list[Any], float]:
    t0 = time.perf_counter()
    resp = client.query_points(
        collection_name=name,
        query=list(query),
        limit=k,
        query_filter=qfilter,
        with_payload=False,
        search_params=SearchParams(exact=True),
    )
    return [p.id for p in resp.points], (time.perf_counter() - t0) * 1000.0


def measure(
    client: QdrantClient,
    name: str,
    libs: Sequence[Library],
    queries: np.ndarray,
    ks: Sequence[int],
    shapes: Sequence[str],
) -> tuple[list[Cell], dict[str, int]]:
    """Cross (shape x level x k), with one trial per (library at that level,
    query vector). Reports mean/min over those trials, never a single sample."""
    # exact match-set size per (shape, library) — this is what min(k, ·) uses.
    match_counts: dict[tuple[str, str], int] = {}
    for shape in shapes:
        for lib in libs:
            f = build_filter(shape_filters(shape, lib.library_id, lib.owner))
            match_counts[(shape, lib.library_id)] = int(
                client.count(collection_name=name, count_filter=f, exact=True).count
            )

    by_level: dict[str, list[Library]] = {}
    for lib in libs:
        by_level.setdefault(lib.level, []).append(lib)

    cells: list[Cell] = []
    total = len(shapes) * len(by_level) * len(ks)
    done = 0
    for shape in shapes:
        for level, level_libs in by_level.items():
            for k in ks:
                sizes = [match_counts[(shape, lb.library_id)] for lb in level_libs]
                cell = Cell(
                    shape=shape,
                    level=level,
                    library_size=level_libs[0].size,
                    match_size=int(round(_mean(sizes))),
                    k=k,
                )
                for lib in level_libs:
                    f = build_filter(shape_filters(shape, lib.library_id, lib.owner))
                    msize = match_counts[(shape, lib.library_id)]
                    for qi in range(queries.shape[0]):
                        q = queries[qi].tolist()
                        hits, ms = run_one(client, name, q, f, k, None)
                        ex, ex_ms = run_exact(client, name, q, f, k)
                        overlap = len(set(hits) & set(ex))
                        cell.trials.append(
                            Trial(
                                library_id=lib.library_id,
                                query_idx=qi,
                                match_size=msize,
                                expected=min(k, msize),
                                hits=len(hits),
                                exact_hits=len(ex),
                                overlap=overlap,
                                recall=(overlap / len(ex)) if ex else 1.0,
                                ms=ms,
                                exact_ms=ex_ms,
                            )
                        )
                cells.append(cell)
                done += 1
                print(
                    f"    cell {done}/{total}: {shape} level={level} k={k} "
                    f"|match|={cell.match_size} hits_min={cell.hits_min} "
                    f"{'PASS' if cell.passed else 'FAIL'}",
                    flush=True,
                )
    counts = {f"{s}:{lid}": c for (s, lid), c in match_counts.items()}
    return cells, counts


def ef_sensitivity(
    client: QdrantClient,
    name: str,
    cell: Cell,
    libs: Sequence[Library],
    queries: np.ndarray,
    efs: Sequence[int],
) -> list[dict[str, Any]]:
    """Re-run one representative cell across hnsw_ef, to see whether the shortfall
    is a search-breadth artefact or a hard per-segment truncation."""
    level_libs = [lb for lb in libs if lb.level == cell.level]
    out: list[dict[str, Any]] = []
    for ef in efs:
        hits: list[int] = []
        recalls: list[float] = []
        for lib in level_libs:
            f = build_filter(shape_filters(cell.shape, lib.library_id, lib.owner))
            for qi in range(queries.shape[0]):
                q = queries[qi].tolist()
                got, _ = run_one(client, name, q, f, cell.k, ef)
                ex, _ = run_exact(client, name, q, f, cell.k)
                hits.append(len(got))
                recalls.append((len(set(got) & set(ex)) / len(ex)) if ex else 1.0)
        out.append(
            {
                "hnsw_ef": ef,
                "hits_mean": _mean(hits),
                "hits_min": min(hits, default=0),
                "recall_mean": _mean(recalls),
                "recall_min": min(recalls, default=0.0),
            }
        )
        print(
            f"    hnsw_ef={ef:<6} hits_mean={_mean(hits):.2f} hits_min={min(hits, default=0)} "
            f"recall_mean={_mean(recalls):.3f}"
        )
    return out


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

_COLS = (
    ("shape", 13),
    ("level", 7),
    ("|lib|", 7),
    ("|match|", 8),
    ("k", 4),
    ("exp", 5),
    ("hits~", 7),
    ("hits.min", 9),
    ("exact~", 7),
    ("exact.min", 10),
    ("recall~", 8),
    ("recall.min", 11),
    ("n", 4),
    ("xfail", 6),
    ("verdict", 7),
)


def print_table(cells: Sequence[Cell]) -> None:
    header = "".join(h.ljust(w) for h, w in _COLS)
    print(header)
    print("-" * len(header))
    for c in cells:
        row = [
            c.shape,
            c.level,
            str(c.library_size),
            str(c.match_size),
            str(c.k),
            str(c.expected),
            f"{c.hits_mean:.2f}",
            str(c.hits_min),
            f"{c.exact_mean:.2f}",
            str(c.exact_min),
            f"{c.recall_mean:.3f}",
            f"{c.recall_min:.3f}",
            str(len(c.trials)),
            str(c.n_exact_fail),
            "PASS" if c.passed else "FAIL",
        ]
        print("".join(v.ljust(w) for v, (_, w) in zip(row, _COLS, strict=True)))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--source-url", default="http://localhost:6333",
                   help="Qdrant to READ real vectors from (never written to).")
    p.add_argument("--target-url", default=None,
                   help="Qdrant to create the g2bench_* scratch collection on "
                        "(default: --source-url).")
    p.add_argument("--api-key", default=None)
    p.add_argument("--prefer-grpc", action="store_true", default=True,
                   help="Use gRPC (default on; 4096-d over REST JSON is slow).")
    p.add_argument("--no-prefer-grpc", dest="prefer_grpc", action="store_false")
    p.add_argument("--grpc-port", type=int, default=6334)
    p.add_argument("--timeout", type=float, default=300.0)

    p.add_argument("--source-collection", default="ragstack_sfr_tok256",
                   help="READ-ONLY source of real SFR vectors.")
    p.add_argument("--collection-suffix", default="sfr",
                   help=f"Scratch collection is {SCRATCH_PREFIX}<suffix>.")

    p.add_argument("--points", "-n", type=int, default=100_000,
                   help="Corpus size. 4096-d float32 = 16 KiB/point "
                        "(100k ~ 1.6 GiB client-side and again server-side).")
    p.add_argument("--queries", type=int, default=5,
                   help="Held-out query vectors; every cell is run against all "
                        "of them (mean/min reported, never one sample).")
    p.add_argument("--fractions", default="1e-2,1e-3,1e-4,1e-5",
                   help="Library selectivity levels as a fraction of --points.")
    p.add_argument("--libs-per-level", type=int, default=3,
                   help="Distinct libraries built at each selectivity level.")
    p.add_argument("--k", default="5,20,50", help="Comma-separated top_k values.")
    p.add_argument("--shapes", default=",".join(SHAPES),
                   help=f"Comma-separated subset of: {', '.join(SHAPES)}")
    p.add_argument("--public-frac", type=float, default=0.5,
                   help="Fraction of points tenanted 'public' (rest get the "
                        "owning/filler user tenant).")
    p.add_argument("--filler-libs", type=int, default=100)
    p.add_argument("--filler-tenants", type=int, default=50)
    p.add_argument("--seed", type=int, default=1337)

    p.add_argument("--distance", default="Cosine", choices=["Cosine", "Dot", "Euclid"])
    p.add_argument("--hnsw-m", type=int, default=16)
    p.add_argument("--ef-construct", type=int, default=100)
    p.add_argument("--full-scan-threshold", type=int, default=10_000,
                   help="hnsw_config.full_scan_threshold in KB (prod: 10000).")
    p.add_argument("--indexing-threshold", type=int, default=10_000,
                   help="optimizers.indexing_threshold in KB (prod: 10000). "
                        "At 16 KiB/vector this is ~625 vectors per segment.")
    p.add_argument("--max-segment-size", type=int, default=None,
                   help="optimizers.max_segment_size in KB. §4 pins this on "
                        "ragstack_lib_v1 because the cliff is per-segment.")
    p.add_argument("--segment-number", type=int, default=None,
                   help="optimizers.default_segment_number (0/None = auto).")
    p.add_argument("--index-timeout", type=float, default=900.0)
    p.add_argument("--require-hnsw", action="store_true",
                   help="Abort instead of measuring if no segment ever crossed "
                        "indexing_threshold — a plain-index PASS is meaningless "
                        "for this question.")

    p.add_argument("--ef-sweep", default="16,32,64,128,256,512,1024",
                   help="hnsw_ef values probed at one representative failing cell.")
    p.add_argument("--force-ef-sweep", action="store_true",
                   help="Run the hnsw_ef sweep even when no cell failed (probes "
                        "the hardest passing cell instead).")
    p.add_argument("--scroll-batch", type=int, default=256)
    p.add_argument("--upsert-batch", type=int, default=128)

    p.add_argument("--json-out", default=None, help="Write machine-readable results here.")
    p.add_argument("--keep", action="store_true",
                   help="DEBUG ONLY: do not drop the scratch collection at the end.")
    p.add_argument("--cleanup-only", action="store_true",
                   help=f"Drop every {SCRATCH_PREFIX}* collection and exit.")
    return p.parse_args(argv)


def make_client(url: str, args: argparse.Namespace) -> QdrantClient:
    return QdrantClient(
        url=url,
        api_key=args.api_key,
        prefer_grpc=args.prefer_grpc,
        grpc_port=args.grpc_port,
        timeout=int(args.timeout),
    )


def _csv_ints(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


def _csv_floats(s: str) -> list[float]:
    return [float(x) for x in s.split(",") if x.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    target_url = args.target_url or args.source_url
    scratch = guard_scratch(f"{SCRATCH_PREFIX}{args.collection_suffix}")

    target = make_client(target_url, args)

    if args.cleanup_only:
        print(f"=== CLEANUP-ONLY on {target_url} ===")
        removed = cleanup_all(target)
        print(f"[=] removed {removed} collection(s)")
        return 0

    source = make_client(args.source_url, args) if args.source_url != target_url else target

    print("=" * 78)
    print("G2 — Qdrant filtered-search truncation, in the v1 library filter shape")
    print("=" * 78)
    print(f"  source (READ-ONLY) : {args.source_url} / {args.source_collection}")
    print(f"  target (SCRATCH)   : {target_url}")
    print(f"  SCRATCH COLLECTION : {scratch}   <-- created now, dropped in finally:")
    print(f"  keep-on-exit       : {args.keep}")
    existing = [c.name for c in target.get_collections().collections]
    print(f"  existing collections on target: {existing}")
    if scratch in existing:
        print(f"  [!] {scratch!r} exists from a previous run and will be replaced")
    print("=" * 78)

    src_info = source.get_collection(args.source_collection)
    vparams = src_info.config.params.vectors
    if isinstance(vparams, dict):
        vparams = next(iter(vparams.values()))
    dim = int(vparams.size)
    print(f"[=] source: {src_info.points_count} points, dim={dim}, "
          f"segments={src_info.segments_count}")

    rng = random.Random(args.seed)
    ks = _csv_ints(args.k)
    shapes = [s.strip() for s in args.shapes.split(",") if s.strip()]
    for s in shapes:
        if s not in SHAPES:
            raise SystemExit(f"unknown shape {s!r}; pick from {list(SHAPES)}")

    print(f"[=] scrolling {args.points + args.queries} real vectors (read-only)")
    allvecs = scroll_vectors(
        source, args.source_collection, args.points + args.queries, args.scroll_batch
    )
    if allvecs.shape[0] < args.points + args.queries:
        raise SystemExit(
            f"only {allvecs.shape[0]} vectors available, need "
            f"{args.points + args.queries}; lower --points"
        )
    corpus = allvecs[: args.points]
    queries = allvecs[args.points : args.points + args.queries]

    libs = plan_libraries(args.points, _csv_floats(args.fractions), args.libs_per_level)
    print(f"[=] libraries planned ({len(libs)}):")
    for level in dict.fromkeys(lb.level for lb in libs):
        sz = next(lb.size for lb in libs if lb.level == level)
        print(f"      level {level}: {args.libs_per_level} libraries x {sz} points")
    payloads = assign_payloads(
        args.points, libs, rng, args.public_frac, args.filler_libs, args.filler_tenants
    )

    results: dict[str, Any] = {
        "config": vars(args) | {"scratch_collection": scratch, "target_url": target_url,
                                "dim": dim},
        "libraries": [asdict(lb) for lb in libs],
    }
    verdict_ok = True
    try:
        create_scratch(target, scratch, dim, args)
        print(f"[=] upserting {args.points} points")
        upsert_corpus(target, scratch, corpus, payloads, args.upsert_batch)
        print("[=] waiting for green + payload indexes")
        telemetry = wait_ready(target, scratch, args.points, args.index_timeout)
        results["collection_state"] = telemetry
        segs = telemetry.get("segments_count") or 0
        indexed = int(telemetry.get("indexed_vectors_count") or 0)
        hnsw_built = indexed >= args.points
        telemetry["hnsw_built"] = hnsw_built
        print(f"[=] segments at measurement time: {segs}   "
              f"(the truncation cliff is evaluated PER SEGMENT)")
        print("=" * 78)
        if hnsw_built:
            print(f"HNSW IS BUILT: indexed_vectors={indexed}/{args.points} "
                  f"across {segs} segments — the truncation question is live.")
        else:
            kb_per_seg = (args.points * dim * 4 / 1024 / max(segs, 1))
            print(
                f"*** HNSW IS *NOT* BUILT: indexed_vectors={indexed}/{args.points}. ***\n"
                f"    ~{kb_per_seg:,.0f} KB/segment vs indexing_threshold="
                f"{args.indexing_threshold} KB, so every segment stayed on the PLAIN\n"
                f"    index and every search below is a brute-force scan. A PASS here\n"
                f"    says NOTHING about HNSW truncation.\n"
                f"    Fix: raise --points, or lower --indexing-threshold "
                f"(e.g. --indexing-threshold 1000)."
            )
            if args.require_hnsw:
                raise SystemExit(
                    "--require-hnsw set and HNSW was not built; aborting before "
                    "producing a meaningless PASS"
                )
        print("=" * 78)

        print("[=] measuring")
        cells, match_counts = measure(target, scratch, libs, queries, ks, shapes)
        results["match_counts"] = match_counts
        results["cells"] = [c.to_json() for c in cells]

        print()
        print("=" * 78)
        print("RESULTS   (exp = min(k, |match|); verdict PASS iff every trial "
              "returned exp hits)")
        print("=" * 78)
        for shape in shapes:
            print(f"\n# {shape}: {SHAPES[shape]}")
            print_table([c for c in cells if c.shape == shape])

        # hnsw_ef sensitivity at one representative failing cell (conjunction first).
        failing = [c for c in cells if not c.passed]
        failing.sort(key=lambda c: (c.shape != "conjunction", c.level, -c.k))
        rep = failing[0] if failing else None
        why = "FAILING"
        if rep is None and args.force_ef_sweep:
            # No failure to probe — sweep the hardest passing cell anyway, so the
            # run still produces an ef curve (and exercises this path).
            candidates = sorted(
                [c for c in cells if c.shape == "conjunction"] or list(cells),
                key=lambda c: (c.match_size, -c.k),
            )
            rep, why = candidates[0], "hardest PASSING (--force-ef-sweep)"
        if rep is not None:
            print(f"\n[=] hnsw_ef sensitivity at representative {why} cell: "
                  f"shape={rep.shape} level={rep.level} k={rep.k} "
                  f"|match|={rep.match_size} (expected {rep.expected})")
            results["ef_sensitivity"] = {
                "cell": {"shape": rep.shape, "level": rep.level, "k": rep.k,
                         "match_size": rep.match_size, "expected": rep.expected,
                         "basis": why},
                "sweep": ef_sensitivity(
                    target, scratch, rep, libs, queries, _csv_ints(args.ef_sweep)
                ),
            }
        else:
            print("\n[=] no failing cell — hnsw_ef sensitivity sweep skipped "
                  "(pass --force-ef-sweep to run it anyway)")
            results["ef_sensitivity"] = None

        conj = [c for c in cells if c.shape == "conjunction"]
        gate = conj if conj else cells
        verdict_ok = all(c.passed for c in gate)
        n_fail_cells = sum(1 for c in gate if not c.passed)
        results["verdict"] = {
            "gate_shape": "conjunction" if conj else "all",
            "cells": len(gate),
            "failing_cells": n_fail_cells,
            "pass": verdict_ok,
            "hnsw_built": hnsw_built,
            "segments_count": segs,
        }
        print()
        print("=" * 78)
        print(
            f"G2 VERDICT: {'PASS' if verdict_ok else 'FAIL'} "
            f"({len(gate) - n_fail_cells}/{len(gate)} cells returned min(k, |match|) "
            f"on every trial)"
        )
        if not hnsw_built:
            print("  ^^ NOT A VALID G2 RESULT: HNSW was never built (see banner above).")
        print("=" * 78)
        stale = [c for c in cells if c.n_exact_fail]
        if stale:
            print(
                f"[!] {len(stale)} cell(s) had exact:true disagree with count(exact) — "
                f"treat those rows as suspect, not as evidence."
            )
    finally:
        print()
        if args.keep:
            print(f"[!] --keep set: leaving {scratch!r} IN PLACE. Remove it with:")
            print(f"    python {sys.argv[0]} --cleanup-only")
        else:
            print(f"[=] cleanup: dropping {scratch!r}")
            try:
                drop_scratch(target, scratch)
            except Exception as e:  # noqa: BLE001 — must not mask the real error
                print(f"[!!] FAILED to drop {scratch!r}: {e}")
                print(f"[!!] remove it manually: python {sys.argv[0]} --cleanup-only")
        remaining = [
            c.name
            for c in target.get_collections().collections
            if c.name.startswith(SCRATCH_PREFIX)
        ]
        print(f"[=] remaining {SCRATCH_PREFIX}* collections: {remaining or 'none'}")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(results, fh, indent=2, default=str)
        print(f"[=] wrote {args.json_out}")

    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(main())
