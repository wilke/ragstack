# Scratchpad — keen-newton worktree

## Session 2026-08-07 (evening) — JATS/OA ingest plane prototype (#301)

Built and proven end-to-end on the DEV tenant against the live `/rag/oa` harvest
(downloading throughout — the growth-stability property was exercised for real).

Pieces (three subagents on disjoint file scopes + integration):
- `JsonlLoader(passthrough_keys=...)` — opt-in metadata pass-through; enriched wins;
  dicts refused (ES dynamic template matches ONE path level); default byte-identical.
- `scripts/plan_shards.py` — shard = sha1(pmcid) % N with N an operator constant, so
  re-planning a grown corpus moves nothing; balance by body_chars (CV 5.3% on 951k);
  excludes only `shard-*.jsonl` so repaired downloads get re-planned.
- `ragstack/ingestion/jats.py` + `scripts/jats_extract.py` — the validated converter as
  a repo tool; byte-identical to `/rag/oa/scripts/jats_to_jsonl.py` on 400 articles.
- `cwl/jats-ingest.cwl` — scatter(extract→embed) chained per shard, single load; the
  load step takes a REGISTRY id + the sqlite registry file, so #263 is in the DAG.
- `embed_shard.py --metadata-passthrough` — the activation point (its loader is real).

**Finding that changed the design: char caps cannot bound table tokens.** Dev run 1
(800 articles, 23,382 chunks): 32.2% of table units exceeded one 512-token window —
token density on real tables spans 1.61–4.30 chars/token (p50 2.84), and the worst unit
was 1,655 tokens at 1,784 chars, UNDER the 1,802-char cap. `fixed_token` then split
them without caption/header — the exact contamination the lift-out exists to prevent.
Fix: `_fit_pieces()` in jats.py derives each unit's char budget from its OWN measured
density, verifies pieces, shrinks toward the worst offender (≤3 rounds). Dev run 2:
**tables 99.6% single-chunk (was 67.8%), figures 100%**; 24,263 chunks, both legs equal;
manifest spec_hash 528f8f36 == registry entry → ADR-0002 guard armed.

**GoWe validation (same evening).** `jats-ingest.cwl` submitted to the live engine
(`--group ragstack --no-upload`, 4 apptainer workers with `--extra-bind /rag`, worker
image rebuilt from today's merge and deployed to /scout/containers). GoWe drives the
nested subworkflow via child submissions — no flattening needed. **The first run caught
a second real bug**: doc ids keyed on `Path(rec_path).resolve()`, and resolve() prepends
the process CWD to a relative identifier like `PMC123#table-2` — so cwltool-less runs
(cwd=checkout) and GoWe workers (cwd=task workdir) minted two id families and re-loading
DUPLICATED the corpus (24,263 → 36,496) instead of upserting. Fixed in #303: absolute
paths keep resolve() (ASM ids preserved), relative paths key on the literal string.
Proven after the fix: run 1 → exactly 12,233 both legs; identical run 2 → still 12,233,
one id family per source_path. Idempotency now holds ACROSS execution environments.

**Batch driver (same night).** `scripts/gowe_batch_ingest.py` + 12 offline tests
(stub gowe CLI, stub store HTTP). Live 2-batch validation on dev: batch 1 (already-
loaded shards) delta 0 at 12,233 both legs — idempotency verified BY the driver;
batch 2 (fresh shards) delta 12,030 → 24,263 both legs, equal to the local cwltool-less
total exactly (cross-environment determinism at full-pipeline level). ~650 MB of staged
.emb.jsonl reclaimed per batch; resume run reports 2 done / 0 to run. Full-batch plan:
batch-size 64, six embedding endpoints (9001-9006 are up; dev inputs list only 2),
current harvest ≈ 2-2.5 days, gated by load throughput if it stays at the measured
~89 chunks/s — re-measure load off NFS before the big run.

**Notice scanner (post-harvest).** `scripts/scan_notices.py`: retraction is NOT a flag
— it is a separate notice article whose `related-article[@related-article-type=
'retracted-article']/@xlink:href` names the retracted ORIGINAL, whose own XML is
untouched. So exclusion is two-hop: drop the notice AND its target. Corrections are the
opposite (original valid + links forward to its fix): both halves kept. Full-harvest
scan: 1,439,753 roots in 34s, 0 unreadable → 3,867 exclusions (0.27%): 106 retraction
notices + 77 retracted originals + 24 EoC notices (26 originals kept+listed) + 3,660
editorial/news/book-review. Root-tag-only matching matters: `article-type="correction"`
greps also hit `related-article-type="correction-forward"` on corrected ORIGINALS
(regression test pins it). exclusions.jsonl feeds plan_shards --exclude directly.

**OA pilot (batch 0 of 32) — DONE and verified, three production bugs found.**
`open-access` on the asm tenant (adopted stores :6333/:9200, ADR-0005 decision 7):
**1,500,340 chunks, legs equal**, facet 992k article / 257k table / 251k figure,
public read verified with a plain user key via the group:public grant. Pilot findings,
all merged: (1) #306 embed concurrency was 8 TOTAL across endpoints; (2) #306 driver
timeout now re-attaches instead of resubmitting; (3) #307 **doc_id had no payload
index** — delete-prior full-scanned per document; the container load's 7h "hang" was
~1 delete/s; creating the index live took it to ~125/s, host-side load then ran 358
chunks/s. Also: one staged .emb.jsonl had a torn line (load failed the file loudly;
re-embedded host-side). Demo's toy open-access purged via its own API — its
content-addressed store name COLLIDED with asm's new collection (same id+spec ⇒ same
name across tenants; the inventory had been reporting it).

**Full-run blocker: #308.** Embed saturates ~58 chunks/s regardless of endpoints (2 vs
6) or concurrency (8 vs 48) — suspect sequential batch embedding in _embed_and_link, or
the endpoints themselves are the ceiling; benchmark single-endpoint first. Real corpus
math: 23.4k chunks/shard × 2,048 = ~48M chunks → 9.6 days at 58 c/s. Load is fine:
358 c/s host-side with the doc_id index. Ledger has batch 00000-00063 done; batches
1–31 wait on #308. asm-next :24020 restarted on current main (tok256 pin intact).

**FULL OA RUN LAUNCHED (2026-08-09 ~15:00).** Driver pid on coconut, log
/rag/ingest/oa/asm-run/driver.log, ledger same dir. 31 batches × 64 shards remaining
(batch 0 = pilot, done). #308 fixed first (#309): PooledEmbedder fans oversized embed()
calls across the fleet (request_batch=128, gather under the pool semaphore; auto factory
always pools) — measured 58 → 560 chunks/s through the real pipeline; fleet benched at
2,606 texts/s so the GPUs were never the ceiling; residual per-shard cost is the 1.2 GB
embedding-file JSON write (#308 stays open, low priority). Worker image rebuilt+verified
with the fix. Projection: ~2.5 h/batch (embed ~74 min at 4 workers + load ~70 min at
358 c/s) ≈ 3.2 days. Expected final: ~48M chunks in open-access on the adopted asm
stores. If the driver dies: rerun the same command — ledger resume + timeout re-attach
are tested. Verify per batch: legs equal in ledger rows; prod ES guard alerts >3s.

Gotchas for the next session:
- conda `ragstack` env lacks `transformers`; run bulk tools with
  `/rag/envs/ragstack/bin/python3.12` + `PYTHONPATH=<dev checkout>/python` + `HF_HOME=/rag/cache`.
  (`make_token_counter` silently falls back hf→endpoint, which fixed_token then rejects —
  the help text 'fixed_token forces hf' overpromises.)
- Qdrant payloads are FLAT (`content_type`), ES nests (`metadata.content_type`) — filters
  differ per leg.
- `ingest_jsonl.py` does NOT use JsonlLoader (calls enrich directly) — still drops
  content_type; irrelevant to the OA plane, noted on #301.
- oa-dev physical name is server-minted; the plan's `ragstack_oa_tok512` mapping is dead.


## Session 2026-08-07 — store inventory: measuring the registry↔store gap (#293)

ADR-0005 decision 6 made exclusive tenant ownership the design target, which re-scoped
three issues. The first buildable piece is the **report-only** half of #293:
`ragstack/ops/store_inventory.py` + `scripts/store_inventory.py`.

Design decisions worth keeping:

- **Claims come from two places, and missing either produces a false `unclaimed`.**
  Registry specs, *and* the deployment's settings-derived (or pinned) default store,
  which has no registry row at all. The largest production corpora on this host are
  claimed only the second way — a tool that read only `*.collections.json` would report
  them as unclaimed, which is the report that gets production deleted.
- **Don't re-derive the default name — call the API's own helpers.** The module builds a
  real `Settings` from each config file and calls `deps._derived_collection_name()` /
  `_es_index_name()` / `_qdrant_url_for()` under a patched module global. A second copy
  of that logic would drift, and drift here has a delete on the end of it.
- **Build `Settings` by installing the parsed values as the environment**, not as init
  kwargs: pydantic-settings JSON-decodes complex fields (`qdrant_collection_routes` is a
  `dict`) in the env source only, so the kwargs route *rejects a config the API accepts*.
  Caught by a test, not by review.
- **`canonical_url` folds loopback spellings and makes the port explicit.** One instance
  written two ways reads as two instances, and then every store on it is unclaimed.
- **Absence is only asserted for a probed backend**, and a configured-but-missing
  registry file raises instead of reading as empty — same reason each time: an empty
  registry turns every store on the instance into a delete candidate.
- Statuses are `claimed` / `claimed-name-only` / `unclaimed-by-known-registries` /
  `claimed-but-absent`. The word *orphan* appears nowhere, and a test enforces that.

**First run found a live defect**: `/rag/data/tenants/asm/config/tenant.env` carries
unquoted JSON in `API_KEYS`/`API_KEY_ROLES`/`API_KEY_TENANTS` — the exact shape that took
lucid-next down (bash strips the inner double quotes, pydantic then fails to parse). The
running `:24020` holds valid JSON in its environ, so the file drifted after start and a
restart would fail. Fix is to single-quote the three values; not applied here (the file
holds live keys).

Because that config is unreadable, asm's stores currently report unclaimed — including
the 877k-point `…928f8ebe` that the switch notes label KEEP. That is the report working:
it says *unclaimed*, and the header says loudly that a registry could not be read.

Deferred deliberately: auto-reclaim (blocked on #263 — the bulk CLI still mints stores the
registry never sees) and Qdrant on-disk sizes unless `--qdrant-storage` is supplied
(Qdrant's API exposes no size; ES's `_cat/indices` does).

### Then #263 — the bulk CLI resolves through the registry

`ragstack/ops/ingest_target.py`, wired into `ingest_jsonl.py`, `ingest_shard.py`,
`ingest_chunks.py`, `load_embeddings.py`. The bulk **data** path is untouched; only the
**registration** moved.

- `--collection-id` resolves through the configured collection store and supplies *every*
  physical name — vector collection, its Qdrant instance, ES index. The line that used to
  read `args.collection or collection_name("ragstack", model, dim)` is exactly how stores
  got minted that no registry ever saw; the CLI can no longer reach it.
- **Routed collections beat the command line**, unrouted ones don't. Writing a routed
  collection to the default instance builds a second invisible copy of a store that
  already exists elsewhere; for everything else the operator naming an instance is the
  more specific statement.
- `--collection` (physical name) still works **when a registry entry claims it**. A flag
  day would strand running pipelines; a warning would be ignored by exactly the callers
  that matter. This splits on the property that matters and nothing else.
- Each writer now writes the provenance manifest **from the registry entry**, which is
  the actual fix: `check_ingest_build_spec` early-returns when `read_manifest()` is None,
  so a store with no manifest disarms ADR-0002's 409 guard forever. A test drives the
  real `deps.check_ingest_build_spec` to prove it is armed after a bulk write.
- `check_build()` treats `None` as "the caller did not say" — a script ingesting
  pre-chunked JSON never learns the chunker and must be prevented from contradicting the
  entry, not blocked by silence.
- `--create-via-api` re-resolves from the durable registry rather than trusting the 201:
  if the API wrote to a different registry than the CLI reads, failing there beats
  failing after a 500k-row load.

**Still open**: `scripts/eval/*` name their own throwaway stores (`chunkcmp_*`,
`oa_smoke_*`). Forcing each comparison arm through a registry entry is the wrong shape;
they need an explicit *ephemeral* convention the inventory can recognise. They are the
last source of unclaimed stores, so #293's reclaim half stays blocked on that, not on this.

---

## Session 2026-08-05 — access-control MVP: users → ownership → sharing → groups (ADR-0003/0004/0005)

Started from a design conversation that turned three loose concepts (tenant, collection,
library) into a decided model, then built it in five sequential units. The design work first:

- **libraries** collapse into **collections** one-to-one — `library_id` is a security label,
  not metadata; it only earns its keep once the ~100–150 collections-per-instance Qdrant
  budget binds, and Qdrant 1.16 removed payload filters from its own RBAC ("prefer
  collection-based access control"). `libraries-spec.md` deprecated in place (shipped code
  still cites §1/§5.0/§8.1/§-1/§16); #230 closed as superseded. → **ADR-0003**.
- **tenant = a Qdrant instance**, not a payload value; the per-chunk `tenant_id` becomes
  provenance. A tenant is one API endpoint + dedicated stateful stores (Qdrant, **its own
  ES**, its own ACL DB); provisioning is a script, not an API. → **ADR-0005** (+ #247).
- **users/groups/shares**: per-tenant Postgres ACLs, `public` a built-in group, `read<write<
  owner` + grant-option, soft revocation via a partial index. → **ADR-0004**.

Then implementation, each unit as a multi-agent workflow (map → implement → 3-lens
adversarial verify → fix) followed by an **independent** security review before merge:

- **#241/#242 → PR #248**: `user_store.py` + first-auth upsert; open collection creation
  from a server default spec; roles → `admin`/`user` (+ `researcher` alias). 7 pre-merge
  review fixes incl. an unbounded-creation DoS → `MAX_COLLECTIONS`.
- **#243 → PR #249**: `acl_store.py` + `authz.py` — the single `resolve_access` seam;
  ownership at collection resolution; behavior-preserving `legacy:admin`+public backfill.
  Independent review caught an **id-reuse ownership hijack** (delete left ACL rows; a reused
  id inherited a stranger's owner row → could purge their data) — fixed + regression-tested.
- **#244 → PR #250**: share API + ShareDialog. Independent review caught a **data leak** the
  build + a first re-verify both missed: `_shared_scope` widened a grantee to the owner's
  `tenant_id`, but the store filter has no `collection_id` predicate, so two collections
  sharing one physical store leaked across the boundary (and the default collection amplified
  it universally). Fixed: widen only on a non-default, store-exclusive collection.
- **#245 → PR #251**: `group_store.py` — native groups, membership unioned into
  `grants_for_subject` so `authz.py` needed no change. Independent review verified the #244
  leak does **not** reappear through the group path (`_shared_scope` is grant-agnostic).

Recurring lesson, three units running: the multi-agent *build* is fast and mostly right; the
independent *adversarial verify* is where correctness actually gets enforced — every review
found something real. Workflows twice finished their fix round without recording the result
(looked unfinished; wasn't) — verified the tree directly rather than resuming blind.

Deferred + recorded (see STATUS + #246): grant_option/write/delegated shares; group owner ≠
member; graph leg lacks `_shared_scope` widening; the `new-tenant` script + shared→per-tenant
ES migration for the two prod tenants (untouched, old API); federated global-tenant view.


## Session 2026-07-01 — semantic ingest: fan-out review + #66/#65 producer & checkpoint fixes

Reviewed and merged **PR #67** (semantic breakpoint-embed fan-out across the pool: 2.06×, all 8 GPUs
vs 1-pinned), then planned+implemented its two filed follow-ups. Planning ran a multi-agent workflow
(understand → design panel per issue → adversarial critic); the critic caught real defects that shaped
the final designs. Both shipped as stacked PRs off `main`.

- **#66 → PR #68** (`perf/ingest-pipeline-producer`): one line — `chunks = await asyncio.to_thread(chunker.chunk, doc)`
  in `ingest_jsonl.py`'s producer. `chunker.chunk()` ran synchronously on the main event loop and the
  semantic path further blocked on the embed bridge's `fut.result()`, starving the embed+upsert workers
  (bursty single-GPU). Awaiting off-thread lets workers drain during the split. **Single in-flight by
  construction** (producer awaits each before the next line) → exactly one caller in `SyncEmbedBridge`,
  so no bridge hardening needed and determinism/#65-frontier untouched. Phase 2 (`--chunk-concurrency`)
  deferred → [#71](https://github.com/wilke/ragstack/issues/71).
- **#65 → PR #70** (`fix/ingest-checkpoint-interval-set`, merged via #70 after the stacked #69 was
  auto-closed when its base #68 merged): the checkpoint advanced only over the contiguous completed-seq
  prefix, so a slow/failed early batch pinned the frontier at the head while later batches upserted out
  of order — every restart re-embedded the lot. Fix: persist **`done_ranges`** (coalesced `[lo,hi]` line
  intervals of above-gap completions); resume skips a line if `<= frontier OR in done_ranges`.

**Non-obvious decisions / critic catches:**
- **The naive skip predicate loses catalog + doc-metrics.** Catalog rows for above-gap batches are
  buffered in `completed[seq]` and only flushed when the frontier folds; a full skip on resume drops
  them permanently. Fix: scope the `done_ranges` skip to **chunk+embed+upsert only** — a resume-skipped
  doc still buffers its catalog row (folds in lockstep) and emits a `"resumed (already indexed)"`
  doc-metrics row. Catalog-only batches (buf empty, buf_catalog non-empty) required a flush-on-catalog-size
  guard + an `if buf or buf_catalog:` EOF flush.
- **Edited-input / `--replace` regression:** `done_ranges` assumes the input is byte-stable across
  restarts (same as the line frontier already does). Gated the skip OFF under `--replace` (which must
  reprocess to prune orphans) and documented the immutability assumption in the module header.
- **No-data-loss invariant preserved:** a *failed* seq is never unioned into `done_ranges`, so its lines
  are in neither set and always re-fed. `done_ranges` is pure optimization metadata — sanitizes to `[]`
  on corruption, degrading to redundant work, never loss. Legacy bare-int + `{line,doc_types}` load with
  `done_ranges=[]`. Also moved `failed.append(seq)` under the lock.
- **Prod tie-in:** the lambda `next-batch` (tok256/tok512) build was hitting exactly this #65 churn under
  lambda-endpoint flapping (checkpoint pinned at line 51, doc-metrics ≈ 2× the file). #70 stops the
  whole-file re-churn, but a *flapping* endpoint still exits non-zero — the convergence lever is the
  deferred `--batch-retries` in [#71](https://github.com/wilke/ragstack/issues/71). Prod must redeploy
  on `main` and restart lambda **without `--replace`**.
- **Env gotcha (again):** tests need the `ragstack` conda env (Python 3.12); the bare `pytest`/`make
  test-python` on PATH is miniconda 3.8 and fails at collection on `dict[str, Any]` annotations.

Closed **#65** and **#66** (linking #70/#68); filed **#71** for the deferred hardening. #67 merged earlier.

## Session 2026-06-29 — review→fix→merge cycle: hybrid (v0.9.0) + M5 intelligence (v0.10.0)

A long review-driven session: opened at `v0.8.0` with PRs #14/#15 pending; closed at `v0.10.0` with
M5's core complete. Pattern throughout: `/review` (multi-agent finders + adversarial verify) → fix
blockers on the branch → check Copilot's comments → merge → reconcile downstream. Every merge was
preceded by a conflict probe (detached test-merge); `/rag/envs/ragstack` is the env that has the deps
(the base `python` env does not).

**Shipped two releases.**
- **v0.9.0** (`2947414`) — Elasticsearch BM25 + hybrid retrieval. #14 jsonschema floor (`>=4.22` to match
  conformance), #15 `ElasticsearchTextIndex` + hybrid wiring, #16 hardening. Review/Copilot fixes:
  `bulk()` surfaces partial-failure errors; full metadata round-trips through ES (filters target
  `metadata.<key>` for parity with the vector store); `ensure_index` is idempotent (create-and-catch,
  not check-then-create — closed a TOCTOU); `_build_query` fails closed without a `tenant_id` filter.
- **v0.10.0** (`3679436`) — M5 intelligence + scholarly ingestion. #17 query rewriting (multiquery/hyde
  → concurrent retrieve via `asyncio.gather` → RRF), #18 mypy baseline clean, #19/#22 JSONL ingestion +
  enrichment, #20/#23 cross-encoder reranking, #21 parallel bulk ingester.

**Design decisions / non-obvious fixes:**
- **mypy baseline (11 errors) cleared via 4 parallel subagents** (one per file group). Split into a portable
  PR #18 (qdrant/loaders/backends — no in-flight work) merged to `main`, while deps.py (TypedDict for the
  embedder kwargs) + scorers.py (`CrossEncoder` type under `TYPE_CHECKING`) rode with the reranker branch.
  `backends.py`: widened `isinstance(res, Exception)` → `BaseException` (gather can return a non-Exception
  BaseException that would crash `.extend`).
- **ingest_jsonl orphan-delete under concurrency** (#21): the producer only flushes a batch on a *document
  boundary*, so a doc's chunks live entirely in one batch/one worker → deleting the batch's distinct doc
  ids before upsert is race-free without a lock, and preserves "delete after a successful embed".
- **Resume-filter footgun** (#19): the checkpoint persists the active `--doc-types`; a resume under a
  different filter fails closed (was silently skipping lines the looser filter would keep).
- **Catalog lockstep** (#22, reconciled onto #21): catalog rows are buffered per batch and written by the
  worker in seq order in lockstep with the checkpoint, so the catalog never outruns the resume point and
  nothing past a failed-batch gap is written.
- **Reranker model mismatch** (#20): the sidecar picks its own model from `MODEL_NAME`; apptainer and
  config defaulted to MiniLM while docker-compose/sidecar defaulted to bge-reranker-v2-m3. Aligned all
  paths + added a startup `/health` model check. Also: validate sidecar `indices` (range/uniqueness →
  degrade, not silently dup/drop); `top_k` added to the `Scorer` protocol so implementers are
  interchangeable; `top_k>=1` request validation.
- **PR #21 was cut from the pre-fix commit** and reverted #19's orphan/resume fixes — rebased onto `main`
  and re-applied them inside the new worker/checkpoint structure (this is why conflict-probing every merge
  mattered).

**Follow-ups deferred → tracked as issues #25–#28:** consolidate `ingest_jsonl.py` onto `IngestionPipeline`
(three hand-rolled copies of the loop now); make enrichment publisher-config-driven (ASM-specific constants);
per-request rerank opt-out; shared sidecar-client base (SidecarReranker/SidecarEmbedder dup HTTP boilerplate).

**Also:** #24 API reference (`docs/API.md`) reviewed + merged (flagged `GET /v1/documents` as a stub
returning `[]`); STATUS.md + README cross-link it. STATUS bumped with an M5 section + checkpoint rows.

---

## Session 2026-06-24 — M2 scalable ingestion (branch `feat/m2-shard-manifest`)

Built the resumable 1→500k ingestion backbone on top of v0.4.0. 4 commits, 94 unit/API tests +
a live batch smoke (real Qdrant + BGE sidecar + Postgres job store).

**Commits:** `458a428` sharding seam (manifest + IngestBackend + LocalAsyncIORunner + ShardedIngestor) ·
`25c8cee` per-item job state + resumable run (skip completed, checkpoint each) · `9d001ea` PostgresJobStore
(asyncpg, lazy pool) · `075a8fb` batch/directory `/v1/ingest` + `items` counts (contract change).

**Design decisions:**
- One code path for 1 doc and 500k: a single file is a 1-item manifest. The `IngestBackend` seam is
  where "single host now, cluster later" lives — `LocalAsyncIORunner` now, Parsl/GoWe/k8s later, same protocol.
- `WorkItem.item_id == loader's deterministic doc id`, so manifest ids, checkpoint ids, and stored
  document ids all coincide (resume + future KG re-run address the same id).
- Resumability is per job_id: `add_items` is idempotent (preserves prior progress), completed items are
  skipped, each outcome checkpointed as it lands. Postgres is the multi-process checkpoint of record
  (sqlite's single writer is the reason to move off it for 500k).
- Job status: `failed` only if the run errors or *every* item fails; partial failures stay `completed`
  with `items.failed > 0`. Single-doc `chunk_ids` kept for back-compat.
- Postgres integration test + the live smoke never call `fail_interrupted()` against the shared DB
  (it reaps ALL non-terminal jobs) and clean up their own rows.

**Live smoke:** dir of 3 docs → completed, items total/completed=3, Qdrant 0→3, 3 Postgres job_items,
re-ingest stayed at 3 (idempotent). Teardown clean.

**Still open in M2:** multi-endpoint EndpointPool (per-tenant quota); resumable (not just off-request)
manifest build for huge submits.

## Session 2026-06-24 — M1 ingest hardening (branch `feat/m1-deterministic-ids`)

Implemented the shortest-path M1 from the multi-team plan (`docs/m1-scalable-pdf-ingest-plan.md`):
robust, scalable PDF→chunk→embed→store→retrieve. 7 commits, 32→69 unit tests, plus a live
integration pass on coconut (real Qdrant + BGE sidecar).

**Commits (oldest→newest):** `9bd3376` deterministic IDs · `16de832` PdfLoader+LoaderRegistry+INGEST_ROOT ·
`c73da0c` async ingest + JobStore · `e14fe3b` bounded/poison-isolated embed · `5f95898` dim
reconciliation + (model,dim) collection scoping · `9a78a5a` loud non-durable fallback ·
`27b810b` API-key auth + CORS fix + prod gate.

**Key decisions / rationale:**
- The duplicate-corpus bug was **two layers**: random `uuid4` in both `loaders.py` (doc id) and
  `chunkers.py` (chunk id). Fixing only the chunker is insufficient — doc id must be deterministic too.
  Chosen keys: resolved path (TextFileLoader), content (StringLoader), `f"{doc.id}:{start}:{end}"` (chunk).
- Async ingest reconciled the *stale* `status=="accepted"` tests by making the behavior real (background
  task) rather than editing assertions. Fixed 3 pre-existing red API tests by adding `tests/api/conftest.py`
  that wires `app.state` with in-memory doubles (lifespan doesn't run under httpx ASGITransport).
- Poison isolation distinguishes 4xx (bad input → bisect & quarantine) from 5xx/network (infra → re-raise),
  so a backend outage never silently drops a corpus.
- Collections scoped to `(model,dim)` for A/B model isolation; `ensure_collection` hard-fails on size
  mismatch. `require_durable_backends` gates the vector store strictly; text index only warns (no ES yet).
- `B008` added to ruff ignore (FastAPI `Depends()`-in-default idiom, pre-existing across routers).

**Live smoke (prod-like config):** auth 401/200 · ingest→poll→completed · 0→1 Qdrant points · retrieve
score 0.728 · **re-ingest kept count at 1 (idempotency proven)** · `/etc/passwd` rejected by INGEST_ROOT.

**Still open in M1:** tenant isolation (`tenant_id`); conformance HTTP tests for the live flow. Next milestone
is M2 (shard/manifest + resumable 1→500k) per the plan.

## Current Session (2026-03-01)

### Completed Work

#### 1-5. Prior Work (plan-c5.md edits)
- Gap analysis, self-hosted model migration, Python/Go duality docs, Elasticsearch BM25, Apptainer deployment

#### 6. Monorepo Refactoring (this session)
Restructured repo for Go + Python parallel development:

**Files moved:**
- `ragstack/` → `python/ragstack/`, `tests/` → `python/tests/`, `pyproject.toml` → `python/pyproject.toml`, `docker/` → `python/docker/`
- Deleted root `docker-compose.yml` (replaced by `deploy/`)

**New directories created:**
- `contracts/` — OpenAPI 3.1 spec, 11 JSON schemas, test fixtures
- `conformance/` — 12 files: HTTP black-box tests (pytest+httpx), schema validation, helpers
- `sidecars/` — 3 Python microservices (crossencoder, embedding, faiss)
- `go/` — Phase 1 scaffold: Chi router, 5 handler files, config, 8 Go tests passing
- `deploy/` — Split Docker Compose: infra, go, python, sidecars
- Root `Makefile`, `.env.example`, updated `.gitignore`

**Verification:**
- Go: `go build` succeeds, 8/8 tests pass
- Python: files moved correctly (git tracks as renames)
- Conformance tests designed to run via RAGSTACK_BASE_URL

### Key Decisions
- Monorepo: python/, go/, sidecars/, contracts/, conformance/, deploy/ as peers
- Go: chi/v5 router, slog logging, google/uuid
- Conformance: HTTP-only black-box, no code imports
- JSON schemas: additionalProperties: false

### Files Modified
- `docs/plan-c5.md` — extensive edits (prior sessions)
- All files in monorepo restructuring (see above)

### Potential Next Steps
- Run conformance tests against both implementations
- Update plan-c5.md project structure to reflect monorepo
- Begin Phase 2 (Qdrant + embedding integration)

---

## Session 2026-06-03 — CLAUDE.md, Apptainer infra, Qdrant adapter

### Completed Work

#### 1. CLAUDE.md + Apptainer infra stack (tag `v0.1.0`, commit `71ac896`)
- `CLAUDE.md` — polyglot monorepo overview, contracts/conformance flow, port split, worktree convention
- `apptainer/{pull,up,down}.sh` — Docker-free infra stack mirroring `deploy/docker-compose.infra.yml`
- All five services bind explicit host dirs under `apptainer/data/<svc>/` for **every** writable path
  (data, logs, configs, sockets, snapshots) — no `--writable-tmpfs`
- ES + Neo4j config dirs seeded from image on first run (they write `elasticsearch.yml` /
  `neo4j.conf` at startup)
- Verified persistence: wrote markers in each service, down→up cycle, read them back identically
- `make infra-{pull,up,down}-apptainer` targets

#### 2. Qdrant adapter + ingest/search CLI (uncommitted on `main`, planned `v0.2.0`)
- `python/ragstack/stores/qdrant.py` — `QdrantVectorStore` implements VectorStore protocol;
  UUID5-hashed point IDs so re-ingest overwrites; original chunk ID preserved in payload
- `python/scripts/ingest_chunks.py` — flatten doc-level metadata onto every chunk, batch
  embed → upsert. Auto-detects vector dim from first embed result, sizes collection
- `python/scripts/search.py` — embed query → `query_points` → text or `--json` output, `--filter k=v` repeatable
- `python/scripts/example_chunks.json` — 2 docs, 5 chunks, demonstrates doc-level metadata pattern

#### 3. Embedder abstraction with vLLM support
- `python/ragstack/embedders.py` — `SidecarEmbedder` (`POST /embed`) + `OpenAIEmbedder`
  (`POST /v1/embeddings`) + `make_embedder()` factory
- Scripts gain `--embedding-api {sidecar,openai}`, `--embedding-model`, pick up `OPENAI_API_KEY` env
- vLLM (`vllm serve <model> --runner pooling`) is just another `--embedding-api openai` endpoint

#### 4. Apptainer embedding sidecar wrapper
- `apptainer/sidecars-{pull,up,down}.sh` — one shared `python.sif` + per-sidecar host-bound `deps/` and `cache/`
- `pip install --target` into `apptainer/data/embedding/deps/` on first `up.sh` (5.1 GB: torch + cuda libs + sentence-transformers)
- HF cache bound at `apptainer/data/embedding/cache/` so BGE download persists
- Run via `python -m uvicorn` (PYTHONPATH=/deps), not the console script (relocated-install shebang issues)
- `make sidecars-{pull,up,down}-apptainer` targets

#### 5. End-to-end validation
- Conda env `ragstack` (Python 3.12.13), `pip install -e ".[vector]"`
- Ingested example_chunks.json: 5 chunks, BGE 768-d, collection `ragstack_demo`
- Search "what is HNSW" → top hit is the Qdrant chunk (score 0.53); filter `tags=deployment` works; "reranking pipeline" → the RRF chunk (0.63)

### Key Decisions
- **Persistence model**: explicit per-path host bind mounts > `--writable-tmpfs` overlay.
  More verbose but state is observable on host and easy to back up
- **Vector ID strategy**: UUID5(chunk_id) — Qdrant requires UUID/int, this is deterministic
- **Embedder protocol**: same async signature for sidecar and OpenAI flavors, switched by CLI flag.
  Default stays `sidecar` so existing callers don't break
- **Apptainer rootless tradeoffs**: no `--cwd` flag (wrapped qdrant CMD in `sh -c 'cd /qdrant && exec'`),
  `--env` shell-sources (ES dotted keys go via `-E key=value` CLI args instead), no PID-1 tini
  (skip it, accept the warning)
- **NEO4J_PASSWORD**: default changed to `ragstack` in apptainer `up.sh`. Neo4j 5 rejects literal `neo4j`.
  `.env.example` still says `neo4j` — also broken for the docker-compose path but out of scope

### Files Modified
- New: `CLAUDE.md`, `apptainer/{pull,up,down,sidecars-pull,sidecars-up,sidecars-down}.sh`,
  `python/ragstack/embedders.py`, `python/ragstack/stores/qdrant.py`,
  `python/scripts/{ingest_chunks.py,search.py,example_chunks.json}`
- Modified: `Makefile` (6 new apptainer targets), `python/ragstack/stores/__init__.py`
  (export QdrantVectorStore behind try/import), `.gitignore` (apptainer artifacts, `*.rdb`)

### Potential Next Steps
- Wire `QdrantVectorStore` into `IngestionPipeline` and `api/main.py` factory
- Cross-encoder reranker apptainer sidecar (parallel to embedding)
- Elasticsearch BM25 store adapter + `TextIndex` impl
- Add a `--embedding-api` autodetect (HEAD probe?) so users don't have to remember the flag
- Bring vLLM up properly (SFR-Embedding-Mistral on H200) and benchmark vs BGE for the workload

---

## 2026-07-03 — STATUS refresh + benchmarks as eval/regression

Refreshed STATUS.md to `main @ 3a95050` (was stale at v0.15.0 / 2026-07-01):
- Recorded the post-v0.15.0 M6 merges from parallel sessions — RBAC spine + `/v1/config` (#84/PR #81), tenant-scoped read endpoints (#85/PR #98), dashboard SPA scaffold + Explore console (#92/#93, PRs #83/#99), config quality-knobs wired (PR #120), `sentence-transformers<4` (PR #119), new ROADMAP.md. M6 flipped "not started" → in progress.
- Recorded this session's chunking size-independence PRs #78/#79/#80 and corrected the root cause (the "lambda flakiness" was our O(n²) split + giant-doc segmentation-embed flood, not the endpoints; lambda13 stable with the fix).
- Rewrote the stale #71 "prod action" note (it told the operator to restart a single lambda job on pre-#70 code) → now points at the 12-GPU sharded loader; marked bounded look-ahead low-urgency.
- Logged the operational 3-corpus A/B (`ragstack_sfr_{tok256,tok512,semantic}`) and the eval-home gap.

### Benchmarks as eval + regression (design note)
The 3-corpus A/B (`tok256` vs `tok512` vs `semantic_pooled`, all SFR/4096, same query space) has **no committed way to be scored** once built: `scifact_chunk_eval.py` and `chunking_compare_7way.py` ingest their *own* isolated `chunkcmp_*` stores; they can't point at an externally-built collection.

Plan (fits the roadmap, doesn't invent a parallel track):
1. **Add an external-store mode** to the eval harnesses: `--collection`/`--es-index`/`--tenant` to score a *pre-built* corpus instead of ingesting a throwaway subset. This is the missing seam that lets an operational corpus be evaluated at all.
2. **Fold into the ablation-harness (#122).** #122 already exists to "isolate embedding/BM25/RRF-k/rewriting/graph/answer-quality — today only chunking is isolated," and PR #120 made the quality knobs config-driven (its precondition). The 3-corpus A/B is the *chunking axis* of #122 run against real built corpora rather than a 1500-doc subset. So: chunking-strategy becomes one configured axis of #122, scored by SciFact (graded qrels) + the known-item harness.
3. **SciFact + more than one benchmark.** SciFact (300 claim queries, graded qrels, bootstrap CIs + Holm–Wilcoxon) is the primary hypothesis test — it already showed chunking is retrieval-invariant (no config distinguishable from `fixed_tok512`). To *show improvement* (not just non-inferiority) we need a benchmark where chunking/retrieval choices actually move the needle: add **BioASQ** (#56, domain-matched to the scientific corpus) and a second BEIR task (e.g. NFCorpus — biomedical) so a win isn't SciFact-specific. Report per-benchmark nDCG@10 / recall@{10,20,100} / MAP with CIs; a real improvement should hold on ≥2.
4. **Regression tests — yes.** Once a corpus + benchmark pair has a recorded baseline score, the harness doubles as a **retrieval-quality regression gate**: pin the baseline (JSON of metric + CI), re-run on PRs that touch chunking/retrieval/RRF/rerank, and **fail if nDCG@10 drops below `baseline_lower_CI − ε`**. Distinguish two tiers: (a) fast **deterministic** gates (chunk counts, token-overflow==0, id determinism, index-parity Qdrant⇄ES) run every CI; (b) slower **quality** gates (SciFact/BioASQ nDCG) run nightly or on retrieval-path labels, since they need the GPU embed fleet. This is the natural home for the ablation-harness output: baseline once, then guard against silent regressions.

Action: file one issue "external-store eval mode + wire 3-corpus A/B into #122; add BioASQ/NFCorpus; promote to nightly regression gate" and link it under M7/eval in ROADMAP.md.
