# A/B benchmark — embed-to-file offline plane: `main` vs PR #144

**Status:** protocol (pre-registered). Results appended after the run.
**Host:** coconut. **Date:** 2026-07-04.

## Objective

Two independent implementations of the #141 goal (decouple embedding from Qdrant
upsert, with backpressure) exist:

- **A — PR #144** (`feat/141-embed-to-file-ingest`), *currently running the live
  semantic corpus build*: `ingest_jsonl.py --embed-out DIR` writes sharded,
  gzip-compressed JSONL via a `FileSink`; a standalone `qdrant_ingest_agent.py`
  drains it with a `HealthGate` (status + optimizer + **unindexed/segment
  backlog ceilings** + an **in-flight window**).
- **B — `main`** (merged #142/#143/#145/#146): `embed_shard.py` writes a single
  versioned JSONL (`embedding_file.py`); `load_embeddings.py --backpressure`
  loads it through a `BackpressuredVectorStore` decorator (status + optimizer_ok
  + `max_wait`; optional `max_in_flight` semaphore, default off → serial).

This benchmark answers two questions:

1. **Runtime performance** — which methods are faster, per stage, and *why*.
2. **Backpressure fidelity** — under a capped Qdrant (the #141 failure regime),
   which holds the DB more conservatively (fewer drops, bounded VMA/segments).

It deliberately does **not** measure embedding quality/recall: both sides use the
**same embedder and the same inputs**, so the vectors are a fixed variable.

## Why lambda13 SFR (not the coconut fleet, not BGE)

- The live build occupies **all 8 coconut GPUs** (`vllm … :9001–9008`, gpu-mem
  0.4 each) — there is no spare GPU on coconut, and touching that fleet would
  slow the live embed.
- **lambda13** runs a separate SFR fleet (`lambda13.cels.anl.gov:9990–9997`),
  reachable in ~5 ms and **not used by the live build**. Using it gives real,
  production-representative **4096-d SFR** vectors off the live path.
- Vector *dimension* is the key variable: Qdrant's VMA/segment pressure (what
  #141 controls) scales with it. BGE's 768-d is 5.3× smaller and may be **too
  gentle to differentiate the two backpressure implementations** under a cap.
  4096-d SFR reproduces the real pressure, and makes the absolute runtime numbers
  transfer to the production decision instead of only the A-vs-B delta.

## Isolation & safety (hard constraints)

1. **Throwaway Qdrant = a separate process** (own port `:6353`, data under
   `/rag/cache/bench/qdrant`), never the live qdrant/qdrant2. Its optimizer is
   capped for the backpressure test; its VMA budget is independent (per-process).
2. **Embedder = lambda13 SFR** (`:9990–9997`); the coconut `:9001–9008` fleet is
   never contacted.
3. **Two git worktrees** under `~/Development/worktrees/` — one on
   `feat/141-embed-to-file-ingest` (A), one on `main` (B) — so neither run
   touches `/rag/repos/ragstack` (the live checkout).
4. Outputs + collections are throwaway (`/rag/cache/bench/`, `bench_a*`/`bench_b*`),
   torn down after. `/home` is full → all artifacts live on `/rag` (2.9 TB free).

## Inputs

One fixed synthetic corpus (deterministic, reproducible, no production data):
`N` documents of pseudo-text, chunked `fixed_token 256/32` (fast + deterministic;
the plumbing is under test, not chunking). Same input file drives A and B.

## Metrics (captured per run, 3 repetitions, medians reported)

- **Per-stage wall-clock**: embed→file, and drain/load, measured separately.
- **Throughput**: chunks/s (embed), upserts/s (load).
- **Correctness parity**: final `points_count`, distinct `doc_id`s; within one
  embedder A and B must converge to the **same stored point set** (deterministic
  uuid5 ids).
- **Backpressure fidelity** (capped run): Qdrant status timeline (2 s samples),
  peak segment count, dropped-upsert / `ResponseHandlingException` count, and the
  discriminator — **peak process VMA** (`grep -c . /proc/$PID/maps`).
- **Resumability**: kill drain/load at ~50 %, resume, verify idempotent
  completion (no loss/dup).

## Pre-registered hypotheses ("which is faster and why")

- **Embed stage → roughly equal (embedder-bound).** Both call the same pooled SFR
  embedder; the GPU dominates. Second-order: A pays extra CPU to **gzip** each
  shard and runs the heavier `ingest_jsonl` producer (segmentation cache, catalog
  hooks); B is leaner but writes **larger uncompressed** files (more write I/O).
  Net expected a wash; whichever loses does so on gzip-CPU (A) vs write-I/O (B).
- **Load stage on a *healthy* Qdrant → A faster.** A's drain keeps an **in-flight
  window** (`--max-inflight`) so multiple upserts pipeline concurrently; B's
  `load_embeddings` is **serial by default** (`max_in_flight=None` → one upsert at
  a time, admission-gated). So when Qdrant is green, A's concurrency should win;
  B trades that for a simpler, safer default. A pays a decompress-CPU cost reading
  gzip shards, expected small next to network+index time.
- **Load stage on a *capped* Qdrant → throughput converges** (both bounded by
  Qdrant's index rate), but A's **backlog ceilings** (unindexed/segments) should
  hold sooner and keep segments/VMA lower, at the cost of a lower steady rate; B's
  status+optimizer gate may let more accumulate before holding. This is the
  speed-vs-safety crux and the concrete case for folding A's ceilings into
  `BackpressuredVectorStore`.

## Test matrix

1. **Correctness parity** (healthy Qdrant): A and B on the same input → same final
   point set + doc coverage.
2. **Throughput baseline** (healthy Qdrant): 3× each; median per-stage throughput.
3. **Backpressure under cap** (decisive): throwaway Qdrant with capped optimizer +
   low `indexing_threshold`; replay the same embed files through A and B; measure
   hold-vs-hammer, drops, peak VMA/segments.
4. **Resumability**: kill at ~50 %, resume, verify idempotent completion.

## Procedure (repro)

```bash
# 0. Throwaway Qdrant on :6353 (separate process; capped for test 3)
#    data: /rag/cache/bench/qdrant ; NEVER the live instance.
# 1. Worktrees
git worktree add ~/Development/worktrees/bench-144 feat/141-embed-to-file-ingest
git worktree add ~/Development/worktrees/bench-main main
# 2. Fixed input corpus (deterministic synthetic JSONL)
# 3a. A: ingest_jsonl.py --embed-out … (FileSink) ; qdrant_ingest_agent.py drain
# 3b. B: embed_shard.py … ; load_embeddings.py --backpressure …
#     both --embedding-api openai --embedding-url http://lambda13.cels.anl.gov:9990 … :9997
# 4. Capped-Qdrant replay of the embed files (test 3)
# 5. Kill/resume (test 4)
```

## Threats to validity

- lambda13 SFR (4096-d) vs coconut's fleet: same model/dim, but a different host
  — absolute latency includes lambda13's network hop (coconut→lambda13). Noted;
  the A-vs-B delta is unaffected (both use the same endpoints).
- Throwaway Qdrant's cap approximates but isn't identical to the production capped
  config; the *direction* of the result transfers, exact rates don't.
- Single-host CPU contention with the live build adds noise → 3× runs, medians,
  and the live build's state is recorded per run.
- Synthetic text embeds to real 4096-d vectors; fine for plumbing/VMA, irrelevant
  to recall (not measured).

## Results

_To be appended after the run: per-stage timing table (A vs B, medians),
throughput, parity check, the capped-Qdrant backpressure table (drops, peak
VMA/segments), resumability outcome, and a written verdict on which method is
faster and why, plus whether A's backlog ceilings are worth porting into
`BackpressuredVectorStore`._
