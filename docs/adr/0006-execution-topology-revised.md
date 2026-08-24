# ADR 0006 — Execution topology, revised from the production build: one ingest plane, a Python-owned online plane, Go by measured trigger

- **Status:** Proposed
- **Date:** 2026-08-24
- **Deciders:** @wilke
- **Supersedes:** [ADR-0001](0001-execution-topology.md) (Proposed 2026-07-02, never accepted)
- **Related:** [#203](https://github.com/wilke/ragstack/issues/203) (route user ingest to GoWe),
  [#25](https://github.com/wilke/ragstack/issues/25) / [#71](https://github.com/wilke/ragstack/issues/71) /
  [#63](https://github.com/wilke/ragstack/issues/63) (ingest-script debt),
  [#41](https://github.com/wilke/ragstack/issues/41) (Go parity), [#116](https://github.com/wilke/ragstack/issues/116)
  (embedding bulkhead), [#343](https://github.com/wilke/ragstack/issues/343) (fleet floor alarm),
  [#89](https://github.com/wilke/ragstack/issues/89) (per-stage timings — the Go trigger's instrument),
  [reports/oa-ingest-run.md](../../reports/oa-ingest-run.md) (the evidence)

## Context

ADR-0001 proposed two planes with three owners: an **offline plane** on GoWe/CWL, an
**online plane** in Go (API gateway, retrieval fan-in, and — "highest value" — a Go
embedding-router sidecar in front of the vLLM fleet), and an **ML core** left in Python
behind HTTP. It was written before any of it existed. Seven weeks later, half of it has
been run in production and the other half has been measured against, and the two halves
came out differently.

**The offline plane was built and worked.** The open-access build ran as CWL workflows
(`cwl/jats-ingest.cwl` and siblings) executed by GoWe, with per-stage Python CLIs
(`jats_extract` → `embed_shard` → `merge_receipts` → `load_embeddings`) and a batch
driver (`scripts/gowe_batch_ingest.py`) that keeps a ledger and verifies both store legs
after every batch. It completed **32 of 32 batches, 47,625,155 chunks, 1,408,194
articles**, with the vector and text legs exactly equal at rest, reading the collection
registry through the SQLite backend the whole way. The engine-owns-scatter/retry model
held; the correctness core that made it safe to retry anything was deterministic ids
(`uuid5`) and idempotent upserts, not orchestration cleverness.

**The Go embedding-router's premise was refuted by measurement.** ADR-0001's rationale was
that goroutines beat asyncio for fan-out across endpoints. During the build the embed
fleet ran at **1.30 of 6 GPUs for ten batches** and nothing noticed. The cause was not the
runtime: the embed source was handed to the pool in fixed 64-document groups, which is
~1.5 sub-requests, and a pool can only spread across as many endpoints as it has
sub-requests. Deriving the group size from the fleet took the same Python pool to **4.32
of 6 GPUs** and the embed phase from 4.13 h to 1.14 h (#334/#335). A shared pool with a
per-batch utilisation floor alarm was then chosen over pinned worker-per-GPU (#336 →
#343). Language was never the lever.

**The Go gateway has no measured trigger.** The two latency fixes of the same month were
both logic: an N+1 owner lookup on `whoami` (5 s → 1.5 ms, #332) and a missing Qdrant
timeout that surfaced as a bare 500 (#346). Query wall time is vLLM + Qdrant +
Elasticsearch + the reranker + the LLM; no measurement attributes a latency or concurrency
failure to the Python layer between them. Meanwhile the Go API tree is ~1,300 lines of
stubs (`HandleQuery` returns `"[pipeline not yet wired]"`), has three tests against 133
Python test files, twelve commits since June against 256, and is not deployed anywhere.
The one Go component in real use is `cmd/mcp`: a static binary a user drops on a laptop
with no Python, no checkout, no environment.

**What actually costs maintenance** is not the runtime but the forks. Ingest has **three
entry points**: the API's in-process `BackgroundTasks` path (which #203 shows cannot take
self-service load), `scripts/ingest_jsonl.py` (1,350 lines re-implementing the pipeline
and its own checkpoint/resume — the #25 fork), and the GoWe plane. A `GoWeBackend`
implementing the `IngestBackend` seam (ADR-0001 Appendix A) also exists in the tree, but
the API's ingest and upload endpoints refuse any non-local backend, so it has no caller.
Two GoWe doors, neither declared canonical; and an unmaintained second API implementation
that the README still advertises as a peer.

## Decision

**1. The offline plane is accepted as built.** CWL workflows + per-stage Python CLIs + the
batch driver are the **operator plane** and the reference shape for every future bulk job
(re-embeds, migrations, evals). Its required parts are the ledger, the per-batch two-leg
verification with settle detection, and the **fleet-utilisation floor alarm (#343)** — the
last is not optional instrumentation but the condition under which the shared-pool
decision (#336) stands. `GoWeBackend` is kept as the **API's submission bridge** (#203):
the same engine and the same CWL tools reached from `POST /v1/ingest`, never a parallel
workflow. One engine, two doors, each with a named owner: the driver for operators, the
backend for users.

**2. One ingest implementation.** The API's in-process ingest is **dev/test only**,
selected by `ingest_backend=local` and never exposed to self-service users; user-triggered
ingest routes to GoWe through the bridge above. `scripts/ingest_jsonl.py` is **retired**:
deprecated now with a pointer to the CWL path, deleted after the next tagged release once
the cookbook and `docs/ingest-paths.md` no longer reference it. This closes #25 and #63 by
decision and the remaining piece of #71 (bounded look-ahead) as moot — the engine bounds
the scatter.

**3. The online plane stays Python.** Gateway, tenancy, ACLs, retrieval fan-in, RRF, and
the embedder pool remain in `python/`. The Go embedding-router of ADR-0001 is
**withdrawn**. The ingest/query bulkhead that #116 asks for is real but is an isolation
property, not a language property: two pool configurations in the existing
`PooledEmbedder` — an ingest pool and a query pool over the fleet that ADR-0005 keeps as
shared plumbing — not a new deployable.

**4. Go by measured trigger — the path is kept open, the scaffold is frozen.** No parity
work on the Go API (#41, the `TODO(parity)` items) until one of these fires:

| Trigger | Instrument | What it would justify |
|---|---|---|
| p95 query latency attributable to the Python layer itself — after subtracting vLLM, Qdrant, ES, reranker and LLM time — exceeds the budget a consumer states | per-stage timings from #89/#90 | a Go gateway + retrieval fan-in, conformance-gated |
| a tenant needs more concurrent queries than horizontally scaled uvicorn workers can hold | the same instrument, plus a load test (#118) | the same |
| a component must run where the conda environment cannot be installed | operational need, not a metric | a static-binary tool, the `cmd/mcp` pattern — first candidate: a loader/verifier CLI for GoWe workers |

Until then the Go API scaffold is neither extended nor deleted, and the README/STATUS
must stop describing it as a peer implementation. What keeps the port possible at any
time is already in place and stays **mandatory for every API change**: `contracts/`
(OpenAPI 3.1 + JSON schemas, `additionalProperties: false`) is the source of truth, and
`conformance/` runs black-box against any implementation selected by `RAGSTACK_IMPL`. Go
remains a first-class module for `cmd/mcp` and any future static-binary tool; those are
built, tested and released, not frozen.

**5. The ML core stays Python behind HTTP** — unchanged from ADR-0001 and not restated.

## Consequences

**Accepted:**

- **There is one API implementation for the foreseeable future**, and the documentation
  has to say so. "Two parallel implementations of the same RAG API" was true of the
  scaffold's intent, not of the system; carrying the claim costs credibility with every
  reader who opens `go/`.
- **`ingest_jsonl.py` users must move** to the CWL path. The cookbook and
  `docs/ingest-paths.md` (already flagged stale, #265) are the migration cost.
- **The Go gate depends on an instrument that does not exist yet.** Per-stage query
  timings (#89/#90) are now on the critical path for *two* reasons — the dashboard and
  this decision — which is an argument for building them early, not for waiving the gate.
- **`GoWeBackend` becomes production code the moment #203 lands** and needs the same
  verification discipline the driver has (receipts, ledger, floor alarm), which it does not
  have today.

**Gained:** one owner for ingest orchestration and one for the online plane; no runtime
maintained on a hypothesis; the 47.6M-chunk build's machinery is the documented reference
rather than a one-off; and the Go path costs nothing until the day it pays.

## Alternatives considered

- **Port the gateway to Go now** (ADR-0001 step 4). Rejected: the gateway is no longer
  "pure logic in `security.py`" — ADR-0003/0004/0005 added ~5,000 lines of ownership,
  shares, groups, service accounts and tenant resolution in the last month, all of which
  would have to be re-implemented and kept in lockstep, and no measurement asks for it.
  This is the #25 fork at the API layer.
- **Delete `go/` entirely.** Rejected: `cmd/mcp` is shipped and used, and static-binary
  distribution is the one Go pattern that has proven itself here. Freezing the API
  scaffold costs nothing; deleting it closes the door this record is meant to keep open.
- **Keep all three ingest entry points.** Rejected; that is the status quo #25 describes,
  and the production build already showed which one is real.
- **Revise ADR-0001 in place** (it was never accepted, so the convention permits it).
  Rejected in favour of superseding: the evidence trail — what was proposed, what
  production showed, what changed — is the point of keeping records.

## Not decided here

Three decisions surfaced by the same build are deliberately left to their own records:

- **Store-backend consolidation.** Five store protocols × three-to-four backends
  (`memory` / `json` / `sqlite` / `postgres`) is ~5,000 lines of parallel code; collapsing
  to `memory` + `sqlite` + `postgres` amends ADR-0004's "JSON stays for single-user dev"
  consequence and is tracked as [#351](https://github.com/wilke/ragstack/issues/351).
- **Scale to 1,000+ users** (#289 vs ADR-0003's collection budget; #230 payload
  partitioning; Qdrant tiered multitenancy) — its own ADR when decided.
- **Vector-store capacity** (#333: quantization, sharding, replicas at 47M × 4096-d;
  research in [reports/quantization-research.md](../../reports/quantization-research.md))
  — its own ADR once the measurement in that report's protocol has been run.
