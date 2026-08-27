# Backlog

25 open issues, grouped by what they cost. Status as of 2026-08-26.

---

## Bites hardest — a default that resolves to production

Six instances of one defect class. The newest two are the worst because they are in the test
harness, and a suite that may import production code or write to a production cluster cannot be
the evidence base for anything else.

| # | What | Status |
|---|---|---|
| **#432** | `pytest` imports **production code** unless `PYTHONPATH` is pinned (the conda env's editable install resolves `ragstack` to `/rag/repos/ragstack/python`); and `tests/integration/test_elasticsearch.py` defaults to the **production ES cluster** | `OPEN` — file first |
| **#407** | A tenant's GoWe ingest silently targets production stores. The API never seeds store URLs; the CWL defaults are production. **Only a per-tenant config workaround is deployed** — the code fix is unmerged | `OPEN` |
| #392 | `build_collection_entry` constructs a real Qdrant store for every configured spec | `OPEN` |
| #369 | The Postgres test DSN defaults to the shared instance backing a production job store | `OPEN` |

`docs/testing/use-case-matrix.md` row **H3** ("a tenant's config never resolves to another
tenant's stores") is ❌ and is the #1 item in that document's own build-first list.

---

## Blocks the personal-collections story from being finished

| # | What | Status |
|---|---|---|
| **#422** | `/v1/documents` and the **ingest** path still resolve the global default. So a caller who cannot read the tenant default can now ask questions but **cannot list documents or upload**. The ingest half needs a **writable**-set picker — reusing the read-based one would route an upload into a collection the caller can read but does not own | `OPEN` |
| **#415** | A failed upload strands its job row. #418 (W1–W3) shrank the class; W4–W10 remain — a cancel endpoint, a reaper, sticky terminal states | `PARTIAL` |
| #86 | Catalog view — phase 4 remainder | `OPEN` |
| #281 | Rename / transfer / merge gaps | `OPEN` |
| #289 | Personal collections at 1,000+ users exceeds ADR-0003's collection budget | `OPEN` by design |

---

## Test infrastructure that cannot express what we need

| # | What | Status |
|---|---|---|
| **#405** | The conformance client fixture sends **no auth header**, and `run_authz_keyed.sh` wires the admin and non-admin keys to the **same value**. The suite's two-principal axis is fictional — which is why #419 shipped | `OPEN` |
| #366 | `GET /v1/config` is never schema-validated | `OPEN` |
| #394 | Graph neighbour items are never validated against the schema | `OPEN` |
| #364 | The g1 sweep's timing cells share one rerank cache and exclude query embedding | `OPEN` |

The persona axis (`caller_without_default_access` — P2 in the use-case matrix) exists in Python
fixtures but **cannot be expressed in conformance today**. That is #405.

---

## Operational

| # | What | Status |
|---|---|---|
| #402 | Never stop services by process-name pattern — an agent cleanup killed the entire API fleet for ~17 h | `OPEN` (rule is in CLAUDE.md and memory) |
| #404 | The neo4j driver is installed nowhere. Fix merged; **the worker image rebuild is not done** | `PARTIAL` |
| #374 | Rebuild `ragstack-worker.sif` carrying the graph extra | `OPEN` — blocks the graph leg running live |
| #387 | `new-tenant.sh` stamps `MAX_COLLECTIONS=100` and none of the settings that matter | `OPEN` |
| #413 | `start-ragstack-workers.sh` should require an explicit worker count | `OPEN` |
| #406 | `GET /v1/config` does not surface the settings operators are told to set | `OPEN` |

**CI is off.** `.github/workflows/ci.yml` triggers are `workflow_dispatch:` only and the last run
was 2026-08-06; recent green checks are the Pages workflow. `make lint-go` has never run in
automation. W7 added the first Go job, under the same dispatch-only trigger — restoring the
triggers is a spend decision that has not been taken.

---

## Feature work, not blocking

| # | What |
|---|---|
| #411 | A BV-BRC app that runs the ingest CWL in place via `cwl-runner` — the second entry point from ADR-0006 decision 6 |
| #426 | A stored token with no Sign out: the keyless-backend case is live today and predates the fix that surfaced it |
| #388 | Targeted eviction |
| #389 | Honour a `doc_id` filter with `context_window` instead of 400 |
| #400 | Entity-name normalisation at write and match |
| #393 | Monotonic confidence on re-ingest |
| #380 | Per-collection graph delete — eviction deliberately does **not** drop triples, because the archive has no triples leg for an ordinary ingest, so a dropped graph could not be rebuilt |
| #371 | Rate-limit hardening follow-ups |
