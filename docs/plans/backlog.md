# Backlog

25 open issues, grouped by what they cost. Status as of 2026-08-26.

---

## Bites hardest — a default that resolves to production

**Eight instances** of one defect class, counted by the rule in
[README.md](README.md#recurring-defect-class-a-default-that-resolves-to-production): one entry per
distinct defect, counted once however often it is rediscovered. The harness ones are the worst,
because a suite that may import production code or write to a production cluster cannot be the
evidence base for anything else.

| # | What | Status |
|---|---|---|
| **#454** | Seven `python/scripts/` CLIs default `--qdrant-url`/`--es-url` to `:6333`/`:9200` — production on this host. **Five are write paths.** #407 set the no-default-on-a-write-target principle for the CWL half; the CLI half was never swept | `OPEN` — the live one |
| #392 | `build_collection_entry` constructs a real Qdrant store for every configured spec, with no `vector_backend` branch. Rediscovered as #451 (closed as duplicate); #444's dead-port pinning hides the symptom without fixing it | `OPEN` |
| #369 | The Postgres test DSN defaults to the shared instance backing a production job store | `OPEN` |
| #432 | `pytest` imported **production code** unless `PYTHONPATH` was pinned, and the ES integration test defaulted to the **production cluster** | `CLOSED` by #444 (guard + opt-in `RAGSTACK_TEST_ES_URL`) and #452 |
| #405 | `conformance/conftest.py` defaulted `RAGSTACK_BASE_URL` to `http://localhost:8000` — a suite that creates and deletes collections, pointed at production's address | `CLOSED` by #452 — now required, with an error naming the hazard |
| #407 | A tenant's GoWe ingest silently targeted production stores | `CLOSED` by #441 — the API seeds the URLs per run, the CWL defaults are gone, and a stale config workaround now refuses the boot |

`docs/testing/use-case-matrix.md` row **H3** ("a tenant's config never resolves to another
tenant's stores") is ❌ and is the #1 item in that document's own build-first list.

---

## Blocks the personal-collections story from being finished

| # | What | Status |
|---|---|---|
| **#422** | `/v1/documents` (#447) and the **ingest** picker (#453) are fixed and merged: a caller who cannot read the tenant default can now list documents and upload. The writable-set picker is in, so an omitted `collection` targets what the caller can *write*. **PR-4 (W12–W14) is not started**, and none of it is deployed | `PARTIAL` |
| **#415** | A failed upload strands its job row. #418 (W1–W3) shrank the class; W4–W10 remain — a cancel endpoint, a reaper, sticky terminal states | `PARTIAL` |
| #86 | Catalog view — phase 4 remainder | `OPEN` |
| #281 | Rename / transfer / merge gaps | `OPEN` |
| #289 | Personal collections at 1,000+ users exceeds ADR-0003's collection budget | `OPEN` by design |

---

## Test infrastructure that cannot express what we need

| # | What | Status |
|---|---|---|
| **#405** | The conformance client fixture sent **no auth header**, and `run_authz_keyed.sh` wired the admin and non-admin keys to the **same value** — the suite's two-principal axis was fictional, which is why #419 shipped | `DONE` — `client` sends the key (`anon_client` for the 401s), the runner provisions four distinct principals and makes the pointer's target private, and `test_persona_p2.py` asserts the P2 rows. `make test-conformance-keyed` |
| #366 | `GET /v1/config` is never schema-validated | `OPEN` |
| #394 | Graph neighbour items are never validated against the schema | `OPEN` |
| #364 | The g1 sweep's timing cells share one rerank cache and exclude query embedding | `OPEN` |

The persona axis (`caller_without_default_access` — P2 in the use-case matrix) exists in Python
fixtures **and, since #405, in conformance**: `make test-conformance-keyed` boots a keyed
in-memory API with four distinct principals and runs the whole suite against it, failing rather
than skipping when a principal it provisioned is missing.

What that run still cannot express, stated so it is not rediscovered:

* a second **tenant** (A4 / #100) — P2 is a second principal inside one tenant;
* an **upload** (C1/C7) — conformance never sends a file, so nothing downstream of one is proven;
* a real **eviction** (E1) — the only eviction call is `dry_run`;
* **retrieval quality** — the keyed run embeds through a hash stub over empty collections, which
  is a plumbing claim and never an L-layer one.

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
