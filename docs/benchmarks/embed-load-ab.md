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

**Run 1 (2026-07-04).** Corpus: 3000 synthetic docs → **6000 chunks**, `fixed_token
256/32`, **SFR 4096-d via lambda13** (`:9990–9997`, key `BRCMistral`; ~330
texts/s/endpoint). Throwaway Qdrant `:6353` (healthy, uncapped). Single run
(medians pending). Tests 1–2 completed; tests 3–4 (capped backpressure,
resumability) deferred — see below.

### Per-stage timing (same corpus, embedder, Qdrant)

| Stage | A — #144 (`ingest_jsonl --embed-out` + `qdrant_ingest_agent`) | B — main (`embed_shard` + load) |
|---|---|---|
| **Embed → file** | **46.5 s**, peak RSS **213 MB**, output **76 MB** (gzip shard) | **74.2 s**, peak RSS **2.35 GB**, output **98 MB** (plain JSONL) |
| **Load** (batch 64, healthy) | **17.4 s → 345 upserts/s** (`--max-inflight 4`) | **22.0 s → 272 upserts/s** (serial `BackpressuredVectorStore`) |
| **Load, as-shipped** | works (batched) | **FAILS** — one unbatched `upsert(6000×4096-d ≈ 98 MB)` → `ResponseHandlingException` |
| **Parity** | 6000 points, green | 6000 points, green — **identical set** (deterministic uuid5 ids) |

### Which is faster, and why

- **Embed — A is ~1.6× faster and ~11× leaner in RAM.** A (`ingest_jsonl`) *streams*
  document-by-document and *pipelines* chunk+embed with doc-level concurrency, so
  it overlaps CPU chunking with the lambda13 round-trips and never holds the whole
  corpus in memory (213 MB). B (`embed_shard`→`embed_source`) is **chunk-all-then-
  embed-all**: it materializes every `Chunk` (+ its 4096-d vector) in memory before
  writing (2.35 GB peak) and embeds in one late phase, so it overlaps less and pays
  a large memory peak. A also gzip-compresses (76 vs 98 MB) at a small CPU cost.
- **Load — A is ~27 % faster** on a healthy Qdrant because A keeps **4 upserts
  in-flight** while B's `BackpressuredVectorStore` is **serial by default**
  (`max_in_flight=None` → one upsert at a time, each awaiting a health poll). This
  is exactly the speed-vs-safety trade the decorator was designed around — and B's
  own `max_in_flight` semaphore (added in #145) closes the gap when set > 1.
- **Robustness — A degrades gracefully to large shards, B does not.** main's load
  path (`index_chunks` → a single `vector_store.upsert(all_chunks)`) has **no
  internal batching**, so a large shard exceeds what the Qdrant client will accept
  in one request. A's drain batches (default 64) and survives. main is only safe
  when shards are pre-sized small (as the CWL scatter does).

### Actionable improvements to `main` (evidence-backed)

1. **Batch the upsert in the load path** (`index_chunks` or `load_embeddings`) —
   the highest-value fix; removes the large-shard failure and matches A's
   robustness. Directly related to #77.
2. **Default `max_in_flight > 1`** in the load stage (the #145 semaphore) to
   recover A's pipelining throughput.
3. **Stream in `embed_source`** rather than materialize the full chunk list —
   bounds memory on big shards (lower priority; the CWL model keeps shards small).

### Caveats

- B is *designed* for small pre-sharded inputs (CWL scatter); the single-file
  failure reflects a large shard, not its intended per-shard usage — but the
  graceful-degradation difference is real and operationally relevant.
- Single run, one scale (6000 chunks). Absolute rates include the coconut→lambda13
  network hop and run alongside the live build; the **A-vs-B direction** is the
  robust signal, not the exact seconds.

### Deferred (follow-up run)

- **Test 3 — backpressure under a *capped* Qdrant** (the decisive VMA test) and
  **Test 4 — resumability** were not run in this pass (the runtime + robustness
  findings above were the priority, per the runtime-performance question). They
  need a larger corpus to build real VMA/segment pressure and a capped instance;
  tracked as the next run.
