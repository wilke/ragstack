# RAGStack — Status

Persistent status across sessions and machines. Read this first to pick up where the project left off.

**Last updated:** 2026-08-05
**Current tag:** [`v0.15.0`](https://github.com/wilke/ragstack/releases/tag/v0.15.0) at `28dbb8f` · **`main @ bdeecf4`** (post-v0.15.0 merges below; the **access-control MVP** — users/ownership/sharing/groups, ADR-0003/0004/0005 — is the newest block; **read [ROADMAP.md](ROADMAP.md) for what's next + what blocks what**)
**Branch:** `main` (synced with `origin`). M1 ingest hardening (PR #4), M2 scalable ingestion (PR #5), the multi-endpoint embedder pool (PR #8), tenant isolation (PR #10), LLM answer generation (PR #12), the per-tenant concurrency quota (PR #13), the Elasticsearch BM25 text index + hybrid retrieval (PRs #14/#15/#16), and the **M5 intelligence layer + scholarly ingestion** — query rewriting (PR #17), cross-encoder reranking (PRs #20/#23), JSONL corpus ingestion with metadata enrichment (PRs #19/#22), and the parallel bulk ingester (PR #21) — merged. **v0.11.0** adds M4 Phase 1 (Neo4j graph store + tenant-scoped graph endpoints, PR #35), pluggable chunkers (PR #36), per-request rerank control (PR #33), injectable publisher profiles (PR #34), a shared sidecar HTTP client (PR #32), and upsert-then-prune ingest safety (PR #37). See the sections below.
**v0.12.0** adds M4 Phase 2 — LLM KG extractor + tenant-scoped graph-retrieval fusion (PR #40, folding in the [#38](https://github.com/wilke/ragstack/issues/38) graph tenant fix); `--chunk-method {fixed,sentence,words,semantic}` in the bulk JSONL ingester, with a semantic embed-bridge and `--embedding-api-key` for token-authed embedding endpoints (PR #42); and a standalone chunking-mode comparison eval harness (`python/scripts/eval/chunking_compare.py`) benchmarking fixed/sentence/semantic on retrieval quality + cost (PR #44, recommendation: the uniformly-sized modes — semantic costs most with no quality upside). Open follow-up: Go parity for KG extraction ([#41](https://github.com/wilke/ragstack/issues/41)).
**v0.13.0** adds token-based chunk sizing (PR #45, closes [#43](https://github.com/wilke/ragstack/issues/43)): a `TokenCounter` abstraction (`hf`/`endpoint`/`estimate` backends + fallback chain) and token-budget packing/splitting so no chunk exceeds the embedder's context window. Opt-in `chunk_max_tokens` is wired into the API ingest path (default OFF — no tokenizer load when unset); the bulk ingester gains an `embed_isolated` 400-backstop so one over-window chunk can't abort a run; the estimator defaults conservative. Includes a `/simplify` cleanup pass.
**v0.14.0** adds a 7-way char/token chunking-method comparison eval harness (`python/scripts/eval/chunking_compare_7way.py`, PR #49): it ingests one deterministic 1500-doc subset **seven** ways (char 512/2048, token-window 256/512, sentence/words packed ≤512 tok, semantic token-capped) into isolated `chunkcmp_m7_*` Qdrant/ES stores, then reports chunk structure, token-overflow, ingest cost, and known-item retrieval quality (recall/MRR/nDCG, hybrid + reranked) — committed report + CSV, recommendation `fixed_tok512` (deterministic, fully token-safe 512-tok window, zero embedder overflow). Includes three review fixes (eval-query API-key auth, `cap_oversized` source-offset/id anchoring, overflow-column rewording). The chunking-comparison decks were refreshed with the 7-way results (PR #50).
**v0.15.0** makes the chunking eval statistically rigorous (PR #51): a shared stats layer (`python/scripts/eval/_stats.py`) with paired-bootstrap 95% CIs, pairwise difference CIs, and a hand-rolled Wilcoxon signed-rank + Holm–Bonferroni pass (no scipy), wired into both harnesses; and a **SciFact (BEIR) passage-level benchmark** (`scifact_chunk_eval.py`) with real graded qrels — nDCG@10/recall@{10,20,100}/MAP over 300 claim queries. Result: **no config is distinguishable from `fixed_tok512`** (all Holm p=1.000, all diff-CIs span 0), upgrading the invariance claim from an underpowered known-item null to a CI-backed no-difference on a real IR task. Folds in `cap_oversized`/token-window bug fixes, a recall@100 pool-size fix, and a `/simplify` dedup pass.

**Unreleased (on `main`, post-v0.15.0)** — semantic-ingest throughput + resume correctness in `scripts/ingest_jsonl.py`: (1) **PR #67** fans the semantic breakpoint-embedding out across the pool (2.06×, all 8 GPUs instead of 1-pinned); (2) **PR #68** ([#66](https://github.com/wilke/ragstack/issues/66)) runs `chunker.chunk()` off the event loop via `asyncio.to_thread` so embed+upsert workers overlap CPU sentence-splitting (single in-flight, no bridge hardening); (3) **PR #70** ([#65](https://github.com/wilke/ragstack/issues/65)) adds a `done_ranges` checkpoint frontier — out-of-order batch completions above a stalled gap are durably recorded so a resume skips them instead of re-embedding the whole head region (fixes the "checkpoint sticks at the head line" churn; catalog/doc-metrics stay complete; disabled under `--replace`; a failed seq is never recorded, so no data loss). Follow-ups tracked in [#71](https://github.com/wilke/ragstack/issues/71): `--batch-retries` (the lever for flapping endpoints) **landed** in `ce7cd31`; #66 phase-2 `--chunk-concurrency` **landed** in `578e070` (keeps strict-file-order `seq` assignment so `done_ranges` holds; owner-deprioritized as a latency-only nicety). Still open: bounded producer look-ahead.

**Unreleased (on `main`, post-v0.15.0) — M6 dashboard + auth spine** (merged from parallel sessions; `main @ 3a95050`): the M6 groundwork landed ahead of the SPEC's long-term ordering. **RBAC spine** ([#84](https://github.com/wilke/ragstack/issues/84), PR #81) — `Principal` + roles + `require_role`, admin `GET /v1/config` (URL creds redacted, roles fail-fast). **Tenant-scoped read endpoints** ([#85](https://github.com/wilke/ragstack/issues/85), PR #98) — read-only `GET /v1/stats/stores`, `/v1/graph/stats`, `/v1/health/deep`. **Dashboard SPA scaffold + Explore query console** ([#92](https://github.com/wilke/ragstack/issues/92)/[#93](https://github.com/wilke/ragstack/issues/93), PRs #83/#99; React+Vite+TS). **Config quality-knobs wired into `Settings`** (PR #120 — **six** retrieval knobs now declared *and read* at construction: `rrf_k`, `retrieval_candidate_multiplier`, `multiquery_n`, `graph_context_score`, `graph_context_depth`, `llm_max_context_chars` (config.py:203-214, consumed at deps.py/query.py); defaults equal the prior hardcoded constants, and the phantom `.env` no-ops are removed. **Not** weighted-RRF or a score-threshold — those remain reserved under #123) → unblocks the ablation-harness ([#122](https://github.com/wilke/ragstack/issues/122)) and config-hardening ([#123](https://github.com/wilke/ragstack/issues/123)). `sentence-transformers` pinned `<4` (PR #119). New **[ROADMAP.md](ROADMAP.md)** (PRs #120/#121). **M6 is now in progress**, not "not started" — see the milestone TODOs below.

**Unreleased (on `main`, post-v0.15.0) — chunking size-independence** (this session, PRs **#78/#79/#80**): ingest cost is now independent of document size, fixing a fleet-wide stall on giant no-punctuation data-table docs. **#79** rewrites `split_text_to_token_budget` O(n²)→O(n) (tokenize once + offset-slice) and makes `sentence_spans` **separator-aware** (newline/tab/`;`/whitespace) so a punctuation-free table isn't one 400k-token span; **#80** falls the semantic chunkers back to `fixed_token` above `--semantic-max-sentences` (default 3000 spans) so per-span segmentation-embedding can't flood the pool; **#78** quarantines a single 4xx-bad chunk on the pooled embedder (`embed_isolated`) instead of failing the whole fan-out batch (5xx/network still propagate — no data loss). **Root cause corrected:** the "lambda endpoint flakiness" was *our chunking*, not the endpoints — lambda13 is stable with the fix (validated 12-GPU run, 0 flaps). Branches `fix/chunker-size-independence`, `fix/semantic-oversize-fallback` — delete after confirming merged.

**Unreleased (on `main`, post-v0.15.0) — access-control MVP: users, ownership, sharing, groups** (this session, PRs **#248/#249/#250/#251**, `main @ bdeecf4`). Five sequential units built the auth/sharing spine on three new accepted ADRs — **[ADR-0003](docs/adr/0003-access-control.md)** (a tenant is a Qdrant *instance*; access asserted at the collection, not the chunk; two roles `admin`/`user`; admin bypasses ownership as a logged branch), **[ADR-0004](docs/adr/0004-users-groups-shares.md)** (per-tenant Postgres ACLs; `public` is a built-in group; `read<write<owner` + grant-option; soft revocation), **[ADR-0005](docs/adr/0005-tenant-anatomy.md)** (a tenant = one API endpoint + dedicated stateful stores incl. its own ES; scripted provisioning, [#247](https://github.com/wilke/ragstack/issues/247)):
- **#241/#242 (PR #248)** — `user_store.py` (profile store, first-auth upsert, provisional rows) + open collection creation from a server-default build spec (`MAX_COLLECTIONS` cap, `DEFAULT_ROLE=user`, `researcher` alias; `engineer`/`manager` removed).
- **#243 (PR #249)** — `acl_store.py` + `authz.py`: ownership + shares enforced through **one `resolve_access` seam**; startup backfill makes pre-existing collections `owner=legacy:admin` + public-read (behavior-preserving); read-deny is 404 (leak-safe), write-deny 403-when-readable, store-down 503 (fail-closed).
- **#244 (PR #250)** — grant/revoke share API + ShareDialog UI; a grantee is a subject, a bare BV-BRC username, or `@public`. `_shared_scope` widens a grantee's retrieval to the owner's tenant **only** on a non-default, store-exclusive collection (the review-caught co-resident leak).
- **#245 (PR #251)** — `group_store.py`: native per-tenant groups + members (flat, no nesting); group membership unions into `grants_for_subject`, so `authz.py` needed zero change; `/v1/groups` CRUD + `@group:<id>` share grantee; instant revocation.

Each unit: multi-agent build (map→implement→adversarial verify→fix) + an independent security review before merge — which caught real defects each time (id-reuse ownership hijack #243; the co-resident data leak #244). **Deferred, recorded:** `grant_option`/`write`/delegated shares (a later unit); a group owner is not implicitly a member; the graph leg doesn't receive `_shared_scope` widening (under-exposure); the `new-tenant` provisioning script + the shared→per-tenant ES migration for the two prod tenants ([#247](https://github.com/wilke/ragstack/issues/247)/[#246](https://github.com/wilke/ragstack/issues/246)); federated/global-tenant view (its own future ADR). Build order + migration checklist: [#246](https://github.com/wilke/ragstack/issues/246).

**Unreleased (on `main`, post-v0.15.0) — collection lifecycle + restore ([#358](https://github.com/wilke/ragstack/issues/358), phase 2 of [#353](https://github.com/wilke/ragstack/issues/353))**. Every registry row (all four `collection_store` backends; SQL columns are additive, the JSON backend keeps a `{collections_file}.lifecycle.json` sidecar) carries `state ∈ {active, archiving, dormant, restoring, lost}`, `versions` (the ordered archive versions), `archive_pending` and `last_accessed_at`; transitions are compare-and-swap (`set_state(expect=…)`), `last_accessed_at` is batched (`AccessTracker`, flushed every `collection_access_flush_seconds`, never per request), and `evictable()` is the predicate #359 consumes. On the resolution seam (`enforce_access`, read/write after authz): `dormant` → one restore submitted **as the caller** + **503 with `Retry-After`** (`collection_restore_retry_after`); `restoring` → 503; `lost` → 409 with the reason; keyless/API-key callers get 503 saying a user token is required. `POST /v1/collections/{id}/restore` is the explicit owner-or-admin form (202, idempotent, may retry from `lost`). Restore admission at the active bound ([#381](https://github.com/wilke/ragstack/issues/381)): both paths swap `dormant → restoring` through `CollectionStore.begin_restore` (count + CAS in one atomic section, the create path's `create(limit=…)` mirrored), evicting one LRU archived collection first when the bound is met and answering 503 + `Retry-After` ("tenant at capacity", row left `dormant`) when nothing is evictable — so the physically-present count never exceeds `max_collections` across creates and restores. `cwl/restore-collection.cwl` runs `load_embeddings.py --replay` over the pre-staged `ws://` version dirs: everything verified (sha256s, geometry, `spec_hash` == registry row) before any store is even created — exit 3 (`permanentFailCodes`) marks the collection `lost`, exit 2/1 leave it `dormant` for a retry. Settings (end of `config.py`): `collection_access_flush_seconds`, `collection_state_cache_seconds`, `collection_restore_retry_after`, `collection_restore_timeout`, `collection_restore_poll_interval`, `collection_restore_cwl`, `collection_restore_workflow_name`, `collection_restore_inputs_json`. Not yet: a timed live restore.

**Eviction ([#359](https://github.com/wilke/ragstack/issues/359), phase 4 of #353)**. `max_collections` bounds *physically present* collections (`collection_store.PHYSICAL`: active/archiving/restoring — counted inside each backend's atomic create section); at the bound `POST /v1/collections` evicts exactly one least-recently-accessed `active` collection whose archive is current (`ops/evict.py`: CAS `active → dormant` first, then drop Qdrant + ES; never one with an in-flight job, never the legacy shared surface's or a shared store) and answers **507** with per-reason counts when nothing is evictable; `POST /v1/admin/collections/evict?need=k[&dry_run=true]` is the operator's handle. Jobs carry `collection_id`. Restore admission at the bound landed in #381 (previous paragraph). The graph leg: purge and tombstone replay drop the collection's triples via `GraphStore.delete_collection` (#380, closes #295); eviction deliberately does NOT until the archive has a triples leg (nothing could rebuild it). Not yet: the triples archive leg.

**Multi-collection retrieval ([#253](https://github.com/wilke/ragstack/issues/253), phase 4 of #201)**. `collections: [id, …]` (1–5 unique ids, mutually exclusive with `collection`) on `/v1/query` and `/v1/retrieve`: every id is resolved and read-authorized before any leg runs (one unknown/unreadable → 404, one dormant → 503 + `Retry-After`, for the whole request), then one already-collection-scoped `HybridRetriever` leg per member runs concurrently, the legs are RRF-fused on `(collection, chunk id)`, the union is cut to `rerank_candidates` and reranked **once**, and every `Source` carries its `collection`. Never a many-valued store filter (#199/#354); the graph leg is one neighbourhood query with `collection IN […]` (exact on Neo4j). `collections: [x]` is byte-for-byte `collection: x` plus the stamp. Not yet: same-spec shared embedding call, DOI-level fuse-time dedup.

**Operational — live 3-corpus A/B rebuild** (test/prod on `coconut`; an experiment, not a repo deliverable): the SFR-Embedding-Mistral (4096-d) scientific corpus (~448k docs) is being rebuilt side-by-side in **three chunking variants** — `ragstack_sfr_tok256`, `ragstack_sfr_tok512`, `ragstack_sfr_semantic` (`semantic_pooled`) — via a 12-GPU sharded loader (coconut `:9001–9008` + lambda13; 24 shards pinned `k%N`; upsert-only + deterministic uuid5 ids). Tracked in the `prod-rebuild-dual-corpus-plan` memory + [`/rag/documents/HANDOFF-2026-07-02.md`](../../documents/HANDOFF-2026-07-02.md). **This A/B currently has no committed eval home** — see the "3-corpus A/B eval gap" TODO below.

**Deployed location (test+prod):** `/rag/` on host `coconut`. See [Production layout](#production-layout-rag) below.

## Where this fits

| Doc | Role |
|---|---|
| [SPEC.md](SPEC.md) | Architectural north star — data models, milestones (M1–M8), planned endpoints. Authoritative for *intent*. |
| [ROADMAP.md](ROADMAP.md) | **Unified plan** — milestones → workstreams → issues → **dependency graph** + recommended next sequence. Read when deciding *what to build next*. |
| [docs/API.md](docs/API.md) | HTTP API reference — endpoints, auth/tenancy, the retrieval pipeline, metadata filtering, config. Grounded in `contracts/openapi.yaml`. |
| [STATUS.md](STATUS.md) | **This file.** Current state, open TODOs, checkpoints, how to pick up. |
| [MEMORY.md](MEMORY.md) | Project rules, conventions, failures-and-fixes. Read before coding. |
| [CLAUDE.md](CLAUDE.md) | Operating instructions for Claude Code in this repo (commands, layout, working notes). |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Current single-node deployment (what is actually provisioned today). |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [ARCHITECTURE-DEEP-DIVE.md](docs/ARCHITECTURE-DEEP-DIVE.md) | System architecture overview + algorithm/duplication deep-dive. |
| [docs/design/ha-rag-reference-design.md](docs/design/ha-rag-reference-design.md) | Aspirational HA reference design + tri-perspective review (NOT provisioned — see §M8). |
| [docs/model-registry.md](docs/model-registry.md) | Dynamic model registry + task assignment + config-page plan. **Phase 1 shipped** (runtime registry + hot-swap, PR #166); Phases 2–4 planned. |
| [docs/adr/0001-execution-topology.md](docs/adr/0001-execution-topology.md) | ADR-0001 execution topology (GoWe/CWL, Go/Python ownership) — Status: Proposed. |
| [scratchpad.md](scratchpad.md) | Append-only per-session notes — what changed, decisions, rationale. |

## What works today (end-to-end)

- **Monorepo scaffold**: Python (FastAPI, port `8000`) + Go (Chi, port `8080`) implementations of a shared OpenAPI 3.1 contract under `contracts/`.
- **Conformance suite**: HTTP black-box tests in `conformance/` runnable against either implementation via `RAGSTACK_BASE_URL`/`RAGSTACK_IMPL`.
- **Two deployment paths**:
  - Docker Compose (`make up-python` / `make up-go` / `make infra-up`).
  - **Apptainer rootless** (`make infra-up-apptainer`, `make sidecars-up-apptainer`) — preferred on hosts without Docker. Every writable container path is bind-mounted to a persistent host dir under `apptainer/data/`.
- **Infra stack**: Qdrant, Elasticsearch, Neo4j, Postgres, Redis — verified persistence across down/up cycles.
- **Embedding sidecar**: BAAI/bge-base-en-v1.5 (768-d, CPU) on `:50053`.
- **Qdrant integration**: `python/ragstack/stores/qdrant.py` implements the `VectorStore` protocol; CLI tools `python/scripts/{ingest_chunks,search}.py` provide round-trip ingest + semantic search with payload filtering.
- **Embedder abstraction**: `python/ragstack/embedders.py` supports both the local sidecar and any OpenAI-compatible endpoint (e.g. vLLM `--runner pooling`), selectable via `--embedding-api {sidecar,openai}`.
- **Multi-endpoint fan-out** (PR #8): `python/ragstack/embed_pool.py` load-balances embedding across several endpoints (e.g. vLLM replicas on the H200s) with least-loaded routing, a global concurrency cap, failover, and lazy health re-probing. Enabled via `EMBEDDING_ENDPOINTS`; both CLIs accept multiple `--embedding-url`. See the pool section below.
- **Functional REST API** (post-`9114fd1`): `/v1/ingest` runs the real load→chunk→embed→upsert pipeline against Qdrant; `/v1/retrieve` and `/v1/query` run the full M5 pipeline — **query rewrite** (`passthrough`/`multiquery`/`hyde`, PR #17) → `HybridRetriever` dense (Qdrant) + BM25 (Elasticsearch) per variant, tenant-scoped on both legs (PR #15) → **RRF fuse** → optional **cross-encoder rerank** (crossencoder sidecar, PR #20) → answer generation (PR #12). Every stage degrades gracefully (failures return 200 + sources). `DELETE /v1/documents/{id}` removes a doc from both legs.

## M1 ingest hardening (merged in `v0.4.0`, PR #4)

Shortest-path hardening of the PDF→chunk→embed→store→retrieve loop, from the multi-team plan in [`docs/m1-scalable-pdf-ingest-plan.md`](docs/m1-scalable-pdf-ingest-plan.md). 7 feature commits + 5 post-review fixes, 76 tests (73 pass, 3 skip — the skips are PDF tests needing the `pdf` extra) + a live integration pass against real Qdrant + the BGE sidecar. Merged to `main` via merge commit `03d549e`. What landed:

1. **Deterministic IDs** (`9bd3376`) — `loaders.py` doc IDs and `chunkers.py` chunk IDs are now `uuid5`-derived (was random `uuid4` at *both* layers), so re-ingesting a document overwrites in place instead of **silently duplicating the corpus** in Qdrant.
2. **PDF + loader registry + LFI confinement** (`16de832`) — `PdfLoader` (PyMuPDF, lazy `pdf` extra), `LoaderRegistry` dispatch by extension, and `INGEST_ROOT` confinement closing the arbitrary-file-read where `request.source` flowed into `open()`.
3. **Async ingest + JobStore** (`c73da0c`) — `/v1/ingest` returns `accepted` + a real `job_id` and runs in the background; `GET /v1/ingest/{job_id}` reports real status (accepted→running→completed/failed) from `jobstore.py` (in-memory or durable stdlib-sqlite).
4. **Bounded + poison-isolated embed** (`e14fe3b`) — `BatchingEmbedder` bounds request size by item/token budget and bisects a failing batch to quarantine a poison input (re-raising infra errors); `OpenAIEmbedder` now sorts response data by returned index.
5. **Dim reconciliation + (model,dim) collection scoping** (`5f95898`) — collections are auto-named `f(model,dim)`; `ensure_collection` hard-fails on a vector-size mismatch instead of writing mixed vectors. **The core protection for the "test different embedding models" workflow.**
6. **Loud non-durable fallback** (`9a78a5a`) — `require_durable_backends` makes a missing/unreachable Qdrant a fatal startup error instead of a silent degrade to in-memory.
7. **Security gate** (`27b810b`) — API-key auth on `/v1` (constant-time, `/health` open), CORS credentials no longer combined with the wildcard origin, and a production startup gate requiring `api_keys` + `ingest_root`.

**Live smoke test (coconut, prod-like config):** auth 401/200, async ingest→poll→completed, real BGE upsert (0→1 points), retrieval score 0.728, **re-ingest kept point count at 1 (idempotency proven)**, and `/etc/passwd` ingest rejected by `INGEST_ROOT`.

**Post-review hardening** (after the PR review + Copilot pass):

8. **SqliteJobStore connection leak** (`aa862c8`) — every op used `with conn:` (a transaction manager that commits but never closes), leaking a connection/fd per call on the durable path; now wrapped in `closing(...)`.
9. **Replace-on-reingest** (`3dbf9af`) — deterministic IDs only made a *byte-identical* re-ingest idempotent; an *edited* document chunks at shifted offsets (new chunk IDs) and the old chunks lingered as orphans. `pipeline.ingest` now deletes each doc's prior chunks (vector + text + graph) before upserting, after a successful embed so a transient failure can't destroy good data first.
10. **Reap interrupted jobs** (`3c0a96e`) — ingest runs as in-process background tasks, so a restart left durable jobs stuck `running` forever. `JobStore.fail_interrupted()` runs at startup and marks every non-terminal job `failed`/`interrupted`.
11. **Defensive dim check** (`d2ed334`, Copilot note) — `_existing_vector_size` walks the Qdrant config via `getattr` so an unexpected shape skips the optional check instead of raising `AttributeError` and hard-failing startup.
12. **Empty re-ingest no longer wipes data** (`b3b614e`, Copilot note) — the replace step in #9 deleted a document's prior chunks unconditionally; a re-ingest yielding no embeddable chunks (empty doc or all-quarantined) destroyed the prior version and upserted nothing. `pipeline.ingest` now raises `EmptyIngestError` before the delete phase, so the prior corpus survives and the job records `failed`.

Still open in M1: tenant isolation (server-side `tenant_id`). Conformance HTTP tests against the live flow remain a near-term TODO. **Residual on #9:** a crash *between* the deletes and the upsert leaves that one document empty until the next re-ingest — atomic replace needs Qdrant delete+upsert in one batch or the M2 job-resumability work; tracked for M2. **Caveat on #10:** `fail_interrupted()` reaps *all* non-terminal jobs at startup; under the durable sqlite store with **multiple uvicorn workers** a (re)starting worker would mark another worker's legitimately-running jobs failed. Fine for the current single-process model; needs a worker/lease guard before multi-worker.

## M2 scalable ingestion (merged in `v0.5.0`, PR #5)

Resumable 1→500k ingestion on a durable checkpoint, per the plan in [`docs/m1-scalable-pdf-ingest-plan.md`](docs/m1-scalable-pdf-ingest-plan.md) (M2 section). Merged to `main` via merge commit `284a344`. What landed:

1. **Sharded-ingestion seam** (`458a428`) — `manifest.py` (`build_manifest` expands a file or directory into `WorkItem`s whose `item_id` == the loader's document id), `backends.py` (`IngestBackend` protocol + `LocalAsyncIORunner`: bounded asyncio concurrency, no broker; Parsl/GoWe/k8s slot in later), `sharded.py` (`ShardedIngestor` runs a manifest through the pipeline with per-item failure isolation).
2. **Per-item state + resumability** (`25c8cee`) — `JobStore` gains `add_items`/`mark_item`/`completed_item_ids`/`item_counts` (InMemory + Sqlite). `ShardedIngestor` with a job_store skips already-completed items and checkpoints each as it lands. **The resume mechanism works at the ingestor level but is not yet reachable through the API** (every `POST` mints a new job_id; no resume trigger) — see [#6](https://github.com/wilke/ragstack/issues/6).
3. **PostgresJobStore** (`9d001ea`) — multi-process checkpoint of record via asyncpg (lazy pool/schema); new `postgres` extra; selected by `JOB_STORE_BACKEND=postgres` + `postgres_dsn`. Verified live against Postgres 16.
4. **Batch/directory endpoint** (`075a8fb`) — `/v1/ingest` accepts a directory (recursive, `.pdf/.txt/.md`); `GET` reports per-document `items` counts (contract: optional `items` added to `IngestResponse`). A single file is a 1-item manifest, so one path serves both scales.

**Live smoke (coconut, Postgres job store):** directory of 3 docs → job `completed`, `items={total:3,completed:3}`, Qdrant 0→3 points, 3 `job_items` rows in Postgres, re-ingest kept 3 points (idempotent).

**Post-review fixes** (PR review + Copilot pass):

5. **Manifest root re-confinement** (`1d2d338`) — `build_manifest` confined only the top-level source; `rglob` follows symlinks, so a link inside the root escaping it got enumerated. Each file is now re-confined; escaping symlinks are skipped.
6. **Postgres-safe startup + clean shutdown** (`16f1067`) — `fail_interrupted()` is unscoped (marks *all* non-terminal jobs failed), so it's skipped for the multi-process `postgres` backend (memory/sqlite still reap); job store is closed on shutdown so the asyncpg pool doesn't leak.
7. **Status/back-compat correctness** (`c775e96`) — don't overwrite `chunk_ids` on a resume-skip; `_final_status()` treats leftover `pending` (with nothing completed) as `failed` so a wholesale-failed shard isn't reported `completed`; corrected the endpoint's over-claimed resume docstring.
8. **Quality cleanup** (`f0e0694`, `/simplify`) — deduped the sqlite/postgres stores' shared logic (`_prepare_job_update`, `_fold_status_counts`, `_JOB_UPDATE_COLUMNS`); moved `close()` and the Postgres `fail_interrupted` no-op onto the `JobStore` protocol/store (lifespan no longer branches on backend name); collapsed `_final_status` and `build_manifest`. No behavior change.

Still open in M2:
- **API-level resume wiring** — [#6](https://github.com/wilke/ragstack/issues/6): the resume mechanism exists but no endpoint/startup path triggers it, so a crashed batch re-embeds everything on re-submit.
- **Per-owner lease for `fail_interrupted` under Postgres** — [#7](https://github.com/wilke/ragstack/issues/7): the startup sweep is unsafe across workers and is currently disabled for Postgres, so crashed Postgres jobs aren't reaped until a lease/heartbeat scopes ownership.
- Off-request *resumable* manifest build for very large submits (today the build is off-request but in-memory).
- Multi-endpoint embedder pool — **landed in PR #8** (see the section below). Per-tenant concurrency quota **landed in PR #13** (v0.8.0).

## Multi-endpoint embedder pool (merged, PR #8)

The last item on the M2 work-list. `python/ragstack/embed_pool.py` — `PooledEmbedder` satisfies the `Embedder` protocol and drops in behind `BatchingEmbedder` exactly like a single embedder. Merged to `main` via merge commit `a4432ac`. What landed:

1. **Routing + backpressure + failover + health** — least-loaded selection across endpoints; a global semaphore caps total in-flight requests; a 5xx / network / **retriable-4xx (429·408·425)** failure fails over to another endpoint (5xx/network demote the endpoint, a busy 429 does not), while every other 4xx propagates unchanged so `BatchingEmbedder` still quarantines genuine bad input; endpoints are re-probed lazily every `health_interval` so a recovered one rejoins the rotation.
2. **Wiring + config** — `deps._build_embedder` picks the pool when `embedding_endpoints` has >1 URL, else the single `embedding_sidecar_url`; both wrapped in `BatchingEmbedder`. New config: `embedding_endpoints` (accepts comma-separated **or** JSON-array env input via `Annotated[..., NoDecode]`), `embedding_max_concurrency`, `embedding_health_path`. CLIs `ingest_chunks.py`/`search.py` take multiple `--embedding-url` (`nargs="+"`) so bulk ingestion fans out.

12 pool unit tests (routing, failover/demotion, all-fail→RuntimeError, 4xx-propagates, retriable-4xx-fails-over, 5xx-fails-over, backpressure cap, least-loaded distribution, end-to-end health recovery, interval gating, health probing); full suite **109 pass / 4 skip**; repo `ruff check .` clean.

**Post-review fixes** (`/review` + Copilot): retriable-4xx now fails over instead of being mis-quarantined; the health refresh moved *outside* the backpressure semaphore so a slow probe can't hold a permit; `e.response is not None` guard before reading `status_code`; configurable `embedding_health_path` for OpenAI/vLLM backends without `/health` under the embeddings base; test `AsyncClient`-leak fixture; also cleared 5 pre-existing repo-wide ruff errors.

**Per-tenant concurrency quota:** **landed in PR #13** (v0.8.0) — once tenant identity flowed end-to-end (tenant isolation, PR #10), `TenantQuota` caps in-flight ingest items + queries per tenant via a `tenant_slot` dependency. Set `tenant_max_concurrency` below `embedding_max_concurrency` for real isolation.

## Hybrid retrieval — Elasticsearch BM25 text index (merged in `v0.9.0`, PRs #14/#15/#16)

Makes the text-index leg real and routes retrieval through the already-coded `HybridRetriever` (dense vector + BM25, fused via Reciprocal Rank Fusion), replacing vector-only retrieval. Closes the "text index written but never read" gap and the Medium-term "Elasticsearch `TextIndex` adapter" TODO. Tagged at `2947414`. What landed:

1. **`ElasticsearchTextIndex` (BM25), tenant-scoped** (PR #15) — `python/ragstack/stores/elasticsearch.py` over ES BM25, scoped exactly like the Qdrant store: the ES document id is `tenant:chunk_id` (same source under two tenants → distinct docs), searches filter to the caller's readable tenants (`terms`, mirroring Qdrant `MatchAny`), delete is tenant-scoped. Full chunk metadata is persisted and rehydrated so RRF fusion doesn't clobber metadata-rich vector hits. Lazy client import (optional `text` extra; `[async]` pulls `aiohttp` for `AsyncElasticsearch`).
2. **Config + durable gate** (PR #15) — `text_backend` (`memory` | `elasticsearch`) + `elasticsearch_api_key`; `_build_text_index` builds ES when configured and **hard-fails under `require_durable_backends`** (mirroring Qdrant — closes the old "text index is in-memory" warning gap); `ensure_index` readiness gate at startup; ES client closed on shutdown.
3. **Hybrid wiring** (PR #15) — `/v1/retrieve` and `/v1/query` go through `HybridRetriever` via `get_retriever`; the tenant scope reaches **both** legs, so isolation holds in hybrid retrieval. `DELETE /v1/documents/{id}` now purges **both** legs (vector + text) so a deleted doc can't resurface via BM25.
4. **jsonschema dev dep** (PR #14) — `jsonschema>=4.22` (matched to conformance's floor) so the 4 conformance schema-validation tests collect and run (was importing 13, silently skipping 4).

**Live hybrid smoke** (coconut: real Qdrant + BGE sidecar + Elasticsearch 8.13 + Postgres jobs, dedicated test index): ingest writes to both Qdrant and ES (ES index = 3 docs); a lexical query (`"reciprocal rank fusion"`) surfaces the exact-term doc top via the BM25 leg; tenant isolation holds across both legs (alice sees own+public, not bob).

**Post-review hardening** (PRs #15/#16, `/review` + Copilot pass):
- ES `bulk()` now inspects the response and raises on partial failure (was silently dropping docs that never got indexed → a later BM25 search would miss them while ingest reported success).
- Full metadata round-trips through ES (was `tenant_id` only), and filters target `metadata.<key>` (keyword via a dynamic template) for parity with the vector store's metadata-based filtering.
- `ensure_index` creates idempotently and swallows `resource_already_exists_exception` (was a check-then-create TOCTOU race that could crash concurrent / `require_durable_backends` startup).
- `_build_query` fails closed on a missing/empty `tenant_id` filter (an unscoped search would otherwise return chunks across all tenants).

Still off: the graph retrieval leg (`use_graph` flows through but no graph store is wired) until M4/M5. Enable ES with `TEXT_BACKEND=elasticsearch` (+ existing `ELASTICSEARCH_URL`/`_INDEX`). 147 unit/api tests pass; live ES integration test exercises tenant-scoped BM25 search/delete + metadata round-trip (skips if ES absent).

## M5 intelligence + scholarly ingestion (merged in `v0.10.0`, PRs #17/#19/#20/#21/#22/#23)

The M5 retrieval-intelligence layer plus a scholarly-corpus ingestion path. Tagged at `3679436`. What landed:

1. **Query rewriting** (PR #17) — `/v1/query` and `/v1/retrieve` expand the query into retrieval variants per `rewrite_strategies` (`passthrough` default; `multiquery` + `hyde` when an LLM is configured), retrieve each variant concurrently (`asyncio.gather`), and RRF-fuse. Unknown/failing strategies degrade to the plain query. `OpenAILLM.complete_text` backs the rewriters.
2. **Cross-encoder reranking** (PRs #20/#23) — `SidecarReranker` posts the fused pool to the crossencoder sidecar (`:50052`), maps `(scores, indices)` back onto chunks, and cuts to `top_k`. Opt-in (`RERANK_ENABLED`); a deeper `rerank_candidates` pool is fetched when on. Hardened post-review: aligned model defaults across launch paths + startup `/health` model check; sidecar-index validation (range/uniqueness → degrade, not corrupt); `top_k` forwarded to shrink the response; `top_k` on the `Scorer` protocol so implementers are interchangeable; `top_k>=1` request validation. A rerank failure degrades to the fused order (never a 500).
3. **JSONL corpus ingestion + scholarly enrichment** (PRs #19/#22) — `ingestion/enrich.py` recovers DOI / title / authors / citations / year / doc_type from the sparse extraction dumps; `JsonlLoader` registered for `.jsonl`; `scripts/ingest_jsonl.py` streams a multi-hundred-MB dump → chunk → embed → upsert (+ optional ES), resumable via an atomic line checkpoint that persists the active `--doc-types` filter (fail-closed on a mismatched resume) and replaces a doc's prior chunks before upsert (no orphans). `--catalog-out` writes the full per-doc metadata catalog (lockstep with the checkpoint so it can't outrun a resume). **Neighbor-context metadata** (`link_neighbors`/`link_neighbors_by_document`, chunkers.py) stamps `chunk_index`/`prev_chunk_id`/`next_chunk_id` on each doc's ordered chunks (applied in both the API pipeline and bulk-ingest paths; test `test_link_neighbors.py`) — the retrieval-time window/neighbor-expansion signal the dashboard's deferred neighbor-context view relies on.
4. **Parallel bulk ingester** (PR #21) — `ingest_jsonl.py`'s index path is a bounded producer→worker pipeline: `--concurrency N` workers embed+upsert in parallel (fan-out across the embedder pool), with ordered crash-safe checkpointing (each batch carries a seq + last line; the checkpoint advances only over the contiguous completed prefix). A failed batch stalls the checkpoint at the gap and exits non-zero; `--resume` reprocesses from there. **Post-v0.15.0 (PR #70, [#65](https://github.com/wilke/ragstack/issues/65)):** the checkpoint also persists `done_ranges` — batches that completed *out of order above* the stalled prefix — so a resume skips them instead of re-embedding the whole head region every restart. The failed seq is still never recorded (no data loss); the skip is disabled under `--replace`.
5. **mypy baseline cleanup** (PR #18 + follow-ups) — the 11 pre-existing mypy errors are fixed; `python/` type-checks clean.

**Validation:** live corpus (11,573 records): DOI recovered for ~88%, reference lists for ~94% of articles, PubMed cross-check 12/12 DOIs / 6/6 titles. Live rerank smoke promoted an on-topic AAC chunk to top-1 over an off-topic one. Parallel ingest verified idempotent under `--concurrency 4`. 211 unit/api tests pass; ruff + mypy clean.

**This completes M5's core** (query rewriters + cross-encoder reranking + hybrid RRF). Still off: graph-augmented retrieval (`use_graph` flows through but no graph store is wired) until M4. Follow-ups tracked as issues [#25](https://github.com/wilke/ragstack/issues/25)–[#28](https://github.com/wilke/ragstack/issues/28) (consolidate the script onto `IngestionPipeline`; make enrichment publisher-config-driven; per-request rerank opt-out; shared sidecar-client base).

## M4 Phase 1 + extensibility (merged in `v0.11.0`, PRs #32–#37)

A batch of feature + hardening work, each reviewed (multi-agent) and fixed before merge. Tagged at `e89d3f6`.

1. **M4 Phase 1 — knowledge graph** (PR #35): `Neo4jGraphStore` (`graph` extra) behind the `GraphStore` protocol, wired into `deps.py` (lazy import, durable-gate, closed on shutdown) and the `/v1/graph/{entities,neighbors}` endpoints, which are now **tenant-scoped** (own + public). Review fixes: depth capped (`Query(ge=1, le=5)` + store clamp) against a Cypher variable-length DoS; the traversal itself is tenant-scoped (`all(rel … IN $tenants)`) so a multi-hop query can't tunnel through another tenant's edge; `delete_by_doc`'s orphan sweep is endpoint-scoped (no global scan / cross-tenant node delete); in-memory dedup keyed by tenant. **Not yet wired into retrieval** — `use_graph` is still a no-op in `/query` (tracked: [#38](https://github.com/wilke/ragstack/issues/38), which must be fixed first to avoid a cross-tenant leak in `_graph_context`).
2. **Pluggable chunkers** (PR #36): `SentenceChunker`, `WordChunker`, `SemanticChunker` (selectable via `chunk_method`; default `fixed` unchanged), with deterministic ids/offsets — **later extended** with `fixed_token` (fixed-size token-window; the SciFact-recommended `fixed_tok512` production default, PR #42) and `semantic_pooled` (mean-pool variant, PRs #75/#76), both shipping in `CHUNK_METHODS` with CLI handling + unit tests. The semantic chunker embeds via a `SyncEmbedBridge` that owns its embedder+httpx client on a dedicated background loop (review fix: was using the app's main-loop client across loops); chunking runs via `asyncio.to_thread` so it never blocks the event loop. `[chunking]` is an optional extra. **`SegmentationCache`** (`ingestion/segmentation_cache.py`, content-addressed by `config_fingerprint`; wired into the semantic bulk-ingest path, test `test_segmentation_cache.py`) memoizes semantic chunk spans so a re-ingest at the same config skips re-segmentation — materially cutting semantic re-ingest cost (also covered in `docs/ARCHITECTURE-DEEP-DIVE.md`).
3. **Per-request rerank control** (PR #33): `rerank` / `rerank_candidates` on the query/retrieve requests (null = server default), across openapi + JSON schemas + models. Closes [#27](https://github.com/wilke/ragstack/issues/27).
4. **Injectable publisher profiles** (PR #34): enrichment's DOI prefix / filename rule / front-matter set are now a config-selected `PublisherProfile` threaded through the API and bulk-ingest paths (`--publisher-profile`); ASM default unchanged. Closes [#26](https://github.com/wilke/ragstack/issues/26).
5. **Shared sidecar HTTP client** (PR #32): `sidecar_http.SidecarClient` dedups the embedder/reranker HTTP boilerplate. Closes [#28](https://github.com/wilke/ragstack/issues/28).
6. **Ingest upsert-then-prune** (PR #37): the bulk ingester upserts before pruning orphan points by id (`delete_except`, now on the store protocols), so a prune/delete timeout leaves harmless duplicates instead of losing data (closes [#31](https://github.com/wilke/ragstack/issues/31)).

**300 unit/api tests pass, 1 skipped; ruff + mypy clean.** Every PR was rebased onto main and reconciled (the shared `deps.py`/`config.py` made each later merge a small rebase).

## Durable collection registry + build-spec guard (PR #235)

A collection's identity **is** its build spec (embedding model + dim + chunk
method/size/overlap/params). Before this, the mapping `id → {index, model, dim,
chunker}` lived in an unlocked read-modify-write of a JSON file that was *opt-in*
— unset `COLLECTIONS_FILE` meant created libraries vanished on restart, and two
instances sharing one file silently lost entries (measured: **14 of 48 survive**
with 6 concurrent writers).

- **`python/ragstack/collection_store.py`** — `CollectionSpec` moved here (re-exported
  from `api/collections.py`) plus four backends behind one protocol, selected by
  `COLLECTION_STORE_BACKEND`: `json` (default), `memory`, `sqlite`, `postgres`.
  The SQL pair share one DDL string and `ensure_columns` additive migration, per
  [`docs/libraries-spec.md` §8.1](docs/libraries-spec.md) and the `jobstore.py`
  precedent (TEXT/INTEGER only, `json.dumps` for structured fields).
- **The JSON path is unchanged on disk and now concurrency-safe**: read-modify-write
  under an `flock` on `{collections_file}.lock` (a sidecar file, because the write
  ends in `os.replace` and a lock on the old inode says nothing about the new one),
  plus a per-writer unique temp path. Appends are upserts, and unknown keys in
  hand-authored entries (prod's `_alias_note`) survive a rewrite.
- **Migration**: set `COLLECTION_STORE_BACKEND=sqlite|postgres`, leave
  `COLLECTIONS_FILE` in place, restart — the empty table is seeded once from the
  file and never again (a later delete is not resurrected). The file is not
  modified, so rolling back is flipping the setting back.
- **Build-spec guard** (`COLLECTION_SPEC_GUARD`, default on): `/v1/ingest` and
  `/v1/ingest/upload` refuse with **409** when the target collection's recorded
  provenance concretely disagrees with what the ingest would build — naming the
  field (`chunk_size=512 but this ingest would use chunk_size=200`), not just two
  hashes. Applies to the default collection too, which is where a pinned
  `QDRANT_COLLECTION_EXPLICIT` would otherwise let a settings change append
  incoherent data to a 25M-point index. Fails **open** where it cannot know:
  no manifest dir, no manifest, or a field either side leaves unstated.
- **Real bug found**: `write_ingest_manifest_for` dropped `chunk_params` and stamped
  the *server's* `embedding_api`, so the first real ingest into a semantic library
  rewrote its manifest under a **different `spec_hash` for the identical build** —
  fake drift, in exactly the field the guard reads.

## Active TODOs

### Near-term — pick up here in the next session

- [x] ~~Wire `QdrantVectorStore` into `IngestionPipeline` + `api/main.py`~~ — done in `9114fd1`. `python/ragstack/api/deps.py` provides the lifespan + factory; routers depend on `get_pipeline`/`get_vector_store`/`get_embedder`. Qdrant is the default backend.
- [ ] Add conformance tests that exercise the live Qdrant-backed flow against the JSON schemas (`/v1/ingest`, `/v1/retrieve`, `/v1/query`, `DELETE /v1/documents/{id}`). The schemas pass for our shapes (manually verified), but no automated coverage yet.
- [x] ~~Wire an LLM into `/v1/query` so `answer` stops being a placeholder~~ — done in PR #12 (v0.8.0): `OpenAILLM` + `RagGenerator`, opt-in via `llm_endpoint`, degrades to sources-with-a-note on LLM failure. Wiring a real model = `vllm serve <model>` + `LLM_ENDPOINT`.
- [x] ~~Implement `GET /v1/documents`~~ — **done ([#86](https://github.com/wilke/ragstack/issues/86), PR #129).** The list is derived from the **served text index** (ES composite terms-aggregation on the `doc_id` keyword, O(#docs) with a `top_hits` exemplar for doc-level metadata), **not** the job registry — so **CLI-built corpora are visible** (the live build writes chunks to ES). Tenant-scoped (own + `public`), opaque-cursor pagination via `?limit=`/`?cursor=` + an `X-Next-Cursor` response header; `metadata` carries the doc-level fields + `chunk_count`. New `list_documents` on the `TextIndex` protocol (ES + in-memory impls); response stays a bare `DocumentInfo[]` (back-compat). **Still open: `/v1/catalog`** (aggregate/facet view) and a Postgres doc registry if listing must survive an ES-less deployment — folded under #86 follow-ups / #94 catalog browser.
- [ ] **3-corpus A/B eval gap** — the live `tok256`/`tok512`/`semantic` rebuild has **no committed way to be scored** once built. The SciFact harness (`scifact_chunk_eval.py`) and 7-way harness ingest their *own* isolated `chunkcmp_*` stores; they can't currently point at an externally-built collection. Needs: (a) a `--collection`/external-store mode on the eval harnesses so a pre-built corpus is scorable, and (b) fold into the ablation-harness ([#122](https://github.com/wilke/ragstack/issues/122)). File as an issue and link under M7/eval in ROADMAP. See "Benchmarks as eval + regression" note in `scratchpad.md`.
- [~] **Ingest resume hardening** — [#71](https://github.com/wilke/ragstack/issues/71): **2 of 3 parts landed post-v0.15.0.** ✅ `--batch-retries` in-process transient retry (PR/commit `ce7cd31` — the real lever for a **flapping endpoint** to converge; default OFF, idempotent `_store_batch` so retry is safe); ✅ #66 phase-2 `--chunk-concurrency` (`578e070` — file-ordered fold preserves `seq`/`done_ranges` monotonicity, but the owner's profiling deprioritized it as a *latency* nicety, not a throughput lever). ❌ **Still open — the only remaining piece:** bounded producer look-ahead (gate the producer on `seq - next_seq < lookahead` so a stalled early batch can't grow `completed` to the whole file tail — no `asyncio.Condition` exists yet; the bounded queue + chunk-window don't cover this). **Prod action (updated 2026-07-03 — supersedes the earlier lambda-restart note):** the "flapping lambda" churn was root-caused to *our chunking* (O(n²) split + giant-doc segmentation-embed floods), **not** the endpoints — fixed by PRs **#78/#79/#80** (see the size-independence note near the top). The `/rag` checkout is on `main @ 3a95050` (has #68/#70/#78/#79/#80 + `--batch-retries`). The production rebuild now runs via the **12-GPU sharded loader** (`/rag/cache/load3corpus/build_sharded_12gpu.sh`; upsert-only, `MAX_ATTEMPTS=2`, `--batch-retries 5`) — **not** a single lambda job; lambda13 is stable with the fix. The one remaining #71 piece (bounded producer look-ahead) is now **low-urgency**: size-independence removed the giant-doc stall that made a stalled early batch grow `completed` to the file tail.
- [ ] **Go parity for KG extraction (M4 Phase 2)** — [#41](https://github.com/wilke/ragstack/issues/41). Python landed the LLM triple extractor + tenant-scoped graph retrieval fusion in PR #40; `go/` has no equivalent. Bring over the extractor, default-OFF config keys, ingestion wiring (server-side `tenant_id` stamping), and a tenant-scoped graph leg (own + `public`, must not reintroduce the [#38](https://github.com/wilke/ragstack/issues/38) cross-tenant leak).

### Medium-term

- [ ] Cross-encoder reranker apptainer sidecar — mirror the embedding sidecar pattern in `apptainer/sidecars-up.sh`
- [x] ~~Elasticsearch `TextIndex` adapter (`python/ragstack/stores/elasticsearch.py`) — paralleling the Qdrant one~~ — landed in PR #15 (v0.9.0), wired into hybrid retrieval; see the hybrid section above
- [ ] Apptainer wrapper for the Python API itself (currently only available via `deploy/docker-compose.python.yml`)
- [ ] Bring vLLM serving SFR-Embedding-Mistral up on a GPU and run the embed-vs-BGE benchmark against representative chunks

### Long-term (per [SPEC.md](SPEC.md) milestones)

- [~] **M4 — Graph**: ~~Neo4j adapter (`GraphStore` protocol) + tenant-scoped graph endpoints~~ (v0.11.0, PR #35), and ~~Phase 2 — LLM KG extractor + graph-augmented retrieval wired into `HybridRetriever`, tenant-scoped~~ (PR #40, with the [#38](https://github.com/wilke/ragstack/issues/38) tenant fix folded in). Python is feature-complete (default-OFF via `kg_extraction_enabled`). Still open: **Go parity** — [#41](https://github.com/wilke/ragstack/issues/41).
- [x] **M5 — Intelligence**: ~~Query rewriters (HyDE, multi-query), cross-encoder reranking in the pipeline, hybrid retrieval with RRF~~ — **core landed in v0.10.0** (rewriting PR #17, reranking PRs #20/#23) on top of hybrid RRF (v0.9.0). Still open within M5: step-back / entity-expansion rewriters, and graph-augmented retrieval (needs M4).
- [~] **M6 — API & Auth**: API-key auth (v0.4.0) + **RBAC spine + `/v1/config`** ([#84](https://github.com/wilke/ragstack/issues/84), PR #81) + **tenant-scoped read endpoints** ([#85](https://github.com/wilke/ragstack/issues/85), PR #98) **landed**. Still open: doc registry / `GET /v1/documents` ([#86](https://github.com/wilke/ragstack/issues/86) — highest-leverage), `GET /v1/jobs` ([#100](https://github.com/wilke/ragstack/issues/100)), rate limiting + request bounds ([#87](https://github.com/wilke/ragstack/issues/87)), authz conformance ([#88](https://github.com/wilke/ragstack/issues/88)), streaming/citations ([#111](https://github.com/wilke/ragstack/issues/111)/[#29](https://github.com/wilke/ragstack/issues/29)). See [ROADMAP.md](ROADMAP.md) §M6.
- [ ] **M7 — Observability**: Prometheus `/metrics` + OTEL ([#89](https://github.com/wilke/ragstack/issues/89)/[#114](https://github.com/wilke/ragstack/issues/114)), usage log + `/v1/stats/usage` ([#90](https://github.com/wilke/ragstack/issues/90)), per-component ablation/eval harness ([#122](https://github.com/wilke/ragstack/issues/122)). Not started. See [ROADMAP.md](ROADMAP.md) §M7.
- [ ] **M8 — Production**: Helm chart, horizontal scaling, load testing, runbook

## Checkpoints (tagged)

| Tag | Commit | Date | What landed |
|---|---|---|---|
| [`v0.1.0`](https://github.com/wilke/ragstack/releases/tag/v0.1.0) | `71ac896` | 2026-05-11 | CLAUDE.md + Apptainer Docker-free infra stack with persistent host binds |
| [`v0.2.0`](https://github.com/wilke/ragstack/releases/tag/v0.2.0) | `4d28ac5` | 2026-06-03 | Qdrant adapter + ingest/search CLIs + embedder abstraction (sidecar/openai) + embedding sidecar wrapper |
| [`v0.3.0`](https://github.com/wilke/ragstack/releases/tag/v0.3.0) | `435b81c` | 2026-06-24 | Functional REST API — Qdrant wired into the FastAPI app (`/v1/ingest`, `/v1/retrieve`, `/v1/query`, `DELETE`) |
| [`v0.4.0`](https://github.com/wilke/ragstack/releases/tag/v0.4.0) | `03d549e` | 2026-06-24 | M1 ingest hardening (PR #4) — deterministic IDs, PDF + LFI confinement, async ingest/JobStore, bounded+poison-isolated embed, (model,dim) collection scoping, durable-backend gate, API-key auth |
| [`v0.5.0`](https://github.com/wilke/ragstack/releases/tag/v0.5.0) | `284a344` | 2026-06-24 | M2 scalable ingestion (PR #5) — sharded-ingestion seam (manifest + IngestBackend + runner), per-item resumable checkpoint, PostgresJobStore, batch/directory ingest with per-item counts |
| [`v0.6.0`](https://github.com/wilke/ragstack/releases/tag/v0.6.0) | `a4432ac` | 2026-06-25 | Multi-endpoint embedder pool (PR #8) — `PooledEmbedder` fan-out across backends with least-loaded routing, global concurrency cap, 5xx/retriable-4xx failover, lazy health re-probe |
| [`v0.7.0`](https://github.com/wilke/ragstack/releases/tag/v0.7.0) | `bad0ef3` | 2026-06-25 | Tenant isolation (PR #10) — server-derived `tenant_id` per API key, tenant-scoped vector/text/graph stores, shared `public` corpus, read-scope set server-side; fail-closed on partial tenant maps |
| [`v0.8.0`](https://github.com/wilke/ragstack/releases/tag/v0.8.0) | `c9c1944` | 2026-06-26 | RAG answer generation (PR #12) — `/v1/query` returns a grounded LLM answer (`OpenAILLM` + `RagGenerator`, opt-in via `llm_endpoint`, degrades on LLM failure) — plus the per-tenant concurrency quota (PR #13) gating ingest + queries via a `tenant_slot` dependency |
| [`v0.9.0`](https://github.com/wilke/ragstack/releases/tag/v0.9.0) | `2947414` | 2026-06-26 | Elasticsearch BM25 text index + hybrid retrieval (PRs #14/#15/#16) — `ElasticsearchTextIndex` (tenant-scoped, durable BM25), `/v1/retrieve` + `/v1/query` fused vector+BM25 via RRF, delete purges both legs; post-review hardening: bulk-error surfacing, metadata round-trip + filter parity, race-safe `ensure_index`, fail-closed tenant scoping |
| [`v0.10.0`](https://github.com/wilke/ragstack/releases/tag/v0.10.0) | `3679436` | 2026-06-29 | M5 intelligence + scholarly ingestion — query rewriting (PR #17: multiquery/hyde → concurrent retrieve → RRF), cross-encoder reranking via sidecar (PRs #20/#23: index-validation, model-default alignment, `top_k` on the `Scorer` protocol), JSONL corpus ingestion with DOI/title/author/citation enrichment (PRs #19/#22: resumable, filter-aware checkpoint, replace-on-reingest, catalog lockstep), parallel bulk ingester (PR #21: producer→worker, ordered crash-safe checkpoint), mypy baseline clean (PR #18) |
| [`v0.11.0`](https://github.com/wilke/ragstack/releases/tag/v0.11.0) | `e89d3f6` | 2026-06-30 | M4 Phase 1 + extensibility — Neo4j graph store + tenant-scoped graph endpoints (PR #35: depth-cap DoS, traversal-scope, endpoint-scoped orphan sweep), pluggable Sentence/Word/Semantic chunkers (PR #36: dedicated-loop embed bridge, non-blocking chunking), per-request rerank control (PR #33), injectable publisher profiles (PR #34), shared `SidecarClient` (PR #32), upsert-then-prune ingest safety (PR #37: no data loss on prune timeout) |
| [`v0.12.0`](https://github.com/wilke/ragstack/releases/tag/v0.12.0) | `1dbdf3e` | 2026-06-30 | M4 Phase 2 + chunking eval — LLM KG extractor + tenant-scoped graph-retrieval fusion wired into `HybridRetriever` (PR #40, folding in the #38 graph tenant fix; default-OFF via `kg_extraction_enabled`), `--chunk-method {fixed,sentence,words,semantic}` in the bulk JSONL ingester with a semantic embed-bridge + `--embedding-api-key` for token-authed endpoints (PR #42), and a standalone chunking-mode comparison eval harness (PR #44: fixed/sentence/semantic on retrieval quality + cost) |
| [`v0.13.0`](https://github.com/wilke/ragstack/releases/tag/v0.13.0) | `7037c82` | 2026-06-30 | Token-based chunk sizing (PR #45, closes #43) — `TokenCounter` abstraction (`hf`/`endpoint`/`estimate` + fallback chain), token-budget packing/splitting so no chunk exceeds the embedder window; opt-in `chunk_max_tokens` wired into the API ingest path (default OFF, no tokenizer load when unset); `embed_isolated` 400-backstop in the bulk ingester (one over-window chunk can't abort a run); conservative estimator default; reserve headroom on explicit + auto-detected budgets. Includes a `/simplify` dedup/logging cleanup pass |
| [`v0.14.0`](https://github.com/wilke/ragstack/releases/tag/v0.14.0) | `971470c` | 2026-07-01 | 7-way chunking-method comparison eval (PR #49) — `chunking_compare_7way.py` ingests one deterministic 1500-doc subset seven ways (char 512/2048, token-window 256/512, sentence/words ≤512-tok, semantic token-capped) into isolated `chunkcmp_m7_*` stores, reporting structure/overflow/cost + known-item retrieval quality (recall/MRR/nDCG, hybrid + reranked); committed report + CSV; recommendation `fixed_tok512` (deterministic, token-safe, zero embedder overflow). Includes three review fixes (eval-query API-key auth, `cap_oversized` source-offset anchoring, overflow-column rewording). Chunking-comparison decks refreshed with the 7-way results (PR #50) |
| [`v0.15.0`](https://github.com/wilke/ragstack/releases/tag/v0.15.0) | `28dbb8f` | 2026-07-01 | Chunking-eval statistical rigor + SciFact benchmark (PR #51) — shared stats layer (`_stats.py`: paired-bootstrap CIs, pairwise diff CIs, hand-rolled Wilcoxon + Holm–Bonferroni, no scipy) wired into both harnesses; SciFact (BEIR) passage-level eval (`scifact_chunk_eval.py`) with real graded qrels — nDCG@10/recall@{10,20,100}/MAP over 300 claim queries → no config distinguishable from `fixed_tok512` (all Holm p=1.000, diff-CIs span 0). Folds in `cap_oversized`/token-window fixes, a recall@100 pool-size fix, and a `/simplify` dedup pass |

## Production layout (`/rag/`)

This host (`coconut`) runs the canonical deployed stack out of `/rag/`. Dev work still happens in a regular checkout (e.g. `~/Development/ragstack`); `/rag/` is the operating environment.

```
/rag/
├── repos/ragstack/      # git checkout — code is single-source-of-truth here
├── apptainer/images/    # SIFs (qdrant.sif, elasticsearch.sif, neo4j.sif, postgres.sif, redis.sif, python.sif)
├── data/                # all service persistence (qdrant/, elasticsearch/, neo4j/, postgres/, redis/, embedding/)
│   └── tenants/         # per-tenant dedicated stores + manifest.tsv (ADR-0005; provisioned by apptainer/new-tenant.sh)
├── documents/           # input corpus (PDFs, derived chunks JSON)
├── config/rag.env       # env file: RAG_DATA, RAG_IMAGES, RAG_REPO, RAG_ENV, NEO4J_PASSWORD
├── envs/ragstack/       # shared conda env (path-based, multi-user) — Python 3.12 + ragstack[vector]
├── backups/             # DB snapshots (manual today; cron-driven later)
└── bin/
    ├── rag              # operator wrapper — sources rag.env, forwards to make
    └── activate         # sourceable — sets env vars + activates conda env
```

**The apptainer scripts in `repos/ragstack/apptainer/` honour `RAG_DATA` and `RAG_IMAGES` from the environment**, defaulting to in-repo paths when unset. The wrapper at `/rag/bin/rag` exports them by sourcing `config/rag.env`, so `apptainer instance` paths land under `/rag/`.

### Daily use

```bash
# admin/maintainer shell: activate everything in one shot
. /rag/bin/activate
# now: python, pip, ragstack package, env vars, conda env all set
cd $RAG_REPO/python
python scripts/ingest_chunks.py /rag/documents/chunks.json --collection my_corpus

# operator: start/stop services from any cwd
/rag/bin/rag infra-up-apptainer
/rag/bin/rag sidecars-up-apptainer
/rag/bin/rag infra-down-apptainer && /rag/bin/rag sidecars-down-apptainer
```

### Source-of-truth rules

- **Code**: `/rag/repos/ragstack/` is a normal git checkout; pull from `origin` to update. Hot-fix locally + push if needed.
- **Service data**: `/rag/data/<service>/` — owned by apptainer instances, do not edit while services are running.
- **Documents to ingest**: drop them in `/rag/documents/` so they aren't tied to a user's `$HOME`.
- **Secrets / config overrides**: `/rag/config/` — never commit anything from here into the repo.

## How to pick up (new session, possibly new machine)

1. **Clone and inspect**
   ```bash
   git clone https://github.com/wilke/ragstack.git
   cd ragstack
   git checkout v0.2.0           # or main for tip
   ```
2. **Read in this order**: `CLAUDE.md` → `MEMORY.md` → this file → recent `scratchpad.md` entries.
3. **Set up the Python env**
   ```bash
   conda create -n ragstack python=3.12 -y
   conda activate ragstack
   cd python && pip install -e ".[vector]"
   ```
4. **Bring infra + embedding sidecar up** (Apptainer path; no Docker required)
   ```bash
   sudo sysctl -w vm.max_map_count=262144     # one-time, for Elasticsearch
   make infra-pull-apptainer                  # ~1 GB images, one-time
   make sidecars-pull-apptainer               # python base SIF, one-time
   make infra-up-apptainer
   make sidecars-up-apptainer                 # ~5 GB deps install on first run
   ```
5. **Smoke-test the Qdrant pipeline**
   ```bash
   cd python
   python scripts/ingest_chunks.py scripts/example_chunks.json --collection demo
   python scripts/search.py "what is HNSW" --collection demo
   ```
6. **For the next chunk of work**, see "Near-term TODOs" above and the most recent `scratchpad.md` session entry.

## Known issues / friction

- **`default` is a pointer, not a collection (#276).** `GET /v1/collections` on an
  upgraded deployment lists the settings-derived corpus under its content-addressed
  name (or `QDRANT_COLLECTION_EXPLICIT`), not as `default`; `is_default` says which
  entry the pointer names, and `collection=default` in a request means "omitted".
  Consequence to know before changing `EMBEDDING_MODEL` / chunker settings on a
  deployment without an explicit collection name: the derived corpus's **id**
  re-keys with its store — and so do its ACL rows (owner + public grant are keyed by
  id; the backfill re-creates them for the new id, honouring an un-publish recorded
  under the old `default` id). That is correct under "a new store is a new
  collection", but it is visible. A legacy `default` row in a durable registry is
  ignored on read and removed on the next write; ACL rows under `default` stay until
  the merge script (rest of #276).

- **Collection naming changed** (`v0.4.0`): the API now scopes Qdrant collections to `(model, dim)` (e.g. `ragstack_baai_bge_base_en_v1_5_768_<hash>`), so data in the old literal `ragstack` collection is invisible to the API. Re-ingest, or pin `QDRANT_COLLECTION`. The CLI tools (`scripts/`) still use the literal `--collection` name.
- **Tenant isolation migration** (PR #10): point IDs are now derived as `uuid5("{tenant_id}:{chunk_id}")` (was `uuid5("{chunk_id}")`) and every read adds a mandatory `tenant_id ∈ [own, "public"]` filter. Pre-PR points lack a `tenant_id` payload, so a Qdrant MatchAny never matches them and they go silently invisible to `/v1/retrieve` and `/v1/query` after upgrade; re-ingesting orphans rather than replaces them (the new tenant-scoped IDs don't collide with the old un-tenanted ones, and the tenant-scoped delete can't match them either, so storage grows). Remedy: for an existing collection, re-ingest the corpus under the intended tenant (or drop/recreate the collection); a fresh deployment is unaffected. The CLI now takes a `--tenant` flag for stamping (see finding #4).
- **Shared conda env (`ragstack`) — runtime extras present, lint tooling not**: `pytest`/`pytest-asyncio`/`pytest-cov` are installed so `make test-python` runs, and the `pdf` (PyMuPDF 1.27) + `postgres` (asyncpg 0.31) runtime extras are present — so PDF ingest and the Postgres job store both work, and the PDF loader tests pass (only the live Postgres integration test still skips, needing a reachable `TEST_PG_DSN`). Deliberately *not* run as `pip install -e ".[all,dev]"` — that would re-resolve pinned runtime deps (qdrant-client/fastapi) in an env that also backs the deployed stack. **`ruff` is now installed in this env** (PR #8 session), so `ruff check .` runs and is clean repo-wide; **`mypy` is still missing**, so the full `make lint-python` (which chains `ruff && mypy`) can't complete. A dedicated dev venv is the clean long-term home for the type-check tooling.
- `.env.example` still has `NEO4J_PASSWORD=neo4j` — invalid for Neo4j 5. The apptainer `up.sh` defaults to `ragstack` instead. Docker-compose users will hit this until `.env.example` is fixed.
- `vm.max_map_count` requires sudo on each new host. Not automatable in user-space.
- Embedding sidecar deps include CUDA libraries even on CPU-only hosts (sentence-transformers pulls torch + cuda). ~5 GB on disk; first-run install is slow.
- SSH push to GitHub requires `ssh-add` after agent restarts. HTTPS push via `gh auth setup-git` is the workaround used here.
