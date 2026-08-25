# RAGStack ingest paths

There is **one** chunk→embed→upsert pipeline (`IngestionPipeline.ingest`, reused
everywhere) and **three** ways to feed it. Which one runs is selected by
`INGEST_BACKEND` (`local` | `gowe`, `python/ragstack/config.py:219`) plus which
entrypoint you call. This page maps the three so you pick the right one; it links
to the deeper docs rather than repeating them.

---

## Comparison

| | 1. Local API ingest | 2. GoWe backend | 3. Bulk CLI |
|---|---|---|---|
| **Entrypoint** | `POST /v1/ingest` (server path) · `POST /v1/ingest/upload` (multipart PDF) | Same API endpoints, but `INGEST_BACKEND=gowe` | `python/scripts/ingest_jsonl.py`; CWL `cwl/{ingest-bulk,embed-bulk,load-embeddings}.cwl` |
| **Input** | A server-side path/dir under `INGEST_ROOT`, **or** uploaded PDF bytes | Each manifest item's `source` is a **pre-extracted JSONL shard** fed to `ingest_shard.py` — **NOT a PDF** (`config.py:214-218`) | A pre-extracted JSONL corpus (`{text, path, metadata}` per line) |
| **Execution** | In-process `LocalAsyncIORunner` (`ingestion/backends.py:43`), bounded asyncio, no broker | Shards submitted to the GoWe CWL engine (`GoWeBackend`, `ingestion/gowe_backend.py:34`; built by `make_ingest_backend`, `backends.py:84`) | CLI process (`ingest_jsonl.py`) streaming to Qdrant/ES; or `cwltool`/GoWe running the CWL tools |
| **Identity / tenant** | From request auth (`resolve_tenant`); uploads staged at `{INGEST_ROOT}/uploads/{tenant}/{job_id}/` (`api/routers/documents.py:344`) | Verified/stamped by RAGStack at load; token merely carried to workers — **unresolved** how it reaches a CWL worker (`libraries-spec.md §6`) | `--tenant` flag, **defaults to `public` = world-readable** (`ingest_jsonl.py:1122`) |
| **Job tracking** | `job_id` in RAGStack's `JobStore` (in-memory / SQLite / Postgres, `jobstore.py`) | Same RAGStack `job_id` — **not** a GoWe id (see note below) | None — CLI checkpoints to `<input>.ckpt`; CWL receipts merged by `merge_receipts.py` |
| **When to use** | Demos, a handful of PDFs, self-service upload | (Intended) scaling pre-sharded batches over a worker fleet | Operator corpus builds: large extraction dumps too big for the API size guard |
| **Status** | **Works** | **Works for PDFs from the caller's Workspace** (#203: batch per task, per-document receipts); the JSONL bulk plane needs `GOWE_SHARDS_INPUT_KEY=shards` | **Production** — the operator path for big corpora |

---

## Which do I use?

- **A few PDFs, or an interactive demo** → Local API ingest (`/v1/ingest/upload`).
  See [`docs/demo-quickstart.md`](demo-quickstart.md) and
  [`contracts/openapi.yaml`](../contracts/openapi.yaml).
- **A big pre-extracted JSONL dump for an operator/org corpus** → Bulk CLI
  (`ingest_jsonl.py`). See
  [`docs/cookbook-new-org-ingest.md`](cookbook-new-org-ingest.md).
- **You want the offline plane to scatter pre-sharded JSONL over GoWe workers**
  → GoWe backend (`INGEST_BACKEND=gowe`, needs `gowe_workflow_cwl` + `gowe_url`).
  See [`docs/gowe-integration.md`](gowe-integration.md) and
  [`docs/m1-scalable-pdf-ingest-plan.md`](m1-scalable-pdf-ingest-plan.md).
- **You have raw PDFs and want them ingested through GoWe** → `INGEST_BACKEND=gowe`
  with `GOWE_WORKFLOW_CWL=cwl/pdf-ingest-scatter.cwl` (#203 2b, Option B — a
  batch of PDFs per task, `batch_size` default 20): the API submits **as the
  caller** from the caller's Workspace (browser upload →
  `.ragstack/collections/<id>/sources/` → `ws://` inputs), the engine stages
  in/out with the caller's token, and the run's archive lands at `versions/<n>/`
  (recorded as the job's `archive_ref`). Needs a bearer BV-BRC identity and a
  registered collection. See `cwl/README.md` § PDF scatter-per-batch and
  [Batch semantics](#batch-semantics-on-the-gowe-path-203-2b) below.

---

> **Ran a large ingest?** The production open-access build is written up in
> [`reports/oa-ingest-run.md`](../reports/oa-ingest-run.md) — reproduce commands,
> measured rates, and the incidents worth knowing before the next one.

## Every path targets a registry entry (#263)

The bulk CLIs write straight to Qdrant/ES — that is why they exist, and it does
not change. What changed is **how they learn where to write**:

```bash
# the store name comes from the registry entry, not from you
python scripts/ingest_jsonl.py corpus.jsonl --collection-id asm-tok256

# create it through the API first, so the cap, the owner row and the build
# spec all come from the normal path
python scripts/ingest_shard.py shard.jsonl \
    --collection-id new-corpus --create-via-api http://localhost:8000
```

`--collection-id` resolves through the configured collection store
(`COLLECTION_STORE_BACKEND`) and supplies **every** physical name: the Qdrant
collection, its instance (a routed collection lives on its own), and the ES
index. An id that is not in the registry is refused.

The deprecated `--collection` still takes a *physical* store name and still
works — but only when a registry entry already claims it. An invocation that
would have minted an unclaimed store now exits 2 with the two ways to fix it.

Why the strictness. A store created outside the registry is invisible to
`GET /v1/collections` and to the collection cap, governed by no owner row
(ADR-0004) — and, because it has no provenance manifest, it **permanently
disarms ADR-0002's 409 build-spec guard** for every later API ingest into it
(`check_ingest_build_spec` early-returns when there is no manifest). So each
bulk writer now also writes the manifest, from the registry entry, and checks its
own build parameters against that entry before writing anything.

Wired: `ingest_jsonl.py`, `ingest_shard.py`, `ingest_chunks.py`,
`load_embeddings.py`. The eval harnesses under `scripts/eval/` still name their
own throwaway stores — see the gap below.

---

## The `job_id` distinction (common confusion)

A local ingest `job_id` lives in **RAGStack's own `JobStore`**
(`python/ragstack/jobstore.py` — `InMemoryJobStore` for dev, `SqliteJobStore` or
`PostgresJobStore` for durable/multi-worker), polled at `GET /v1/ingest/{job_id}`.
It is **not** a GoWe submission id, even when `INGEST_BACKEND=gowe`. GoWe has its
own submission ids internally; you do not poll GoWe with a RAGStack `job_id`.

---

## Archive format (`ragstack-archive/1`)

> Phase 2 of #353 (issue #357). The archive is the **last step of the ingest
> workflows** (`cwl/pdf-ingest.cwl`, `cwl/pdf-ingest-scatter.cwl`; standalone
> tool `cwl/archive-collection.cwl`, delete form `cwl/archive-tombstone.cwl`).
> Writer/reader: `python/ragstack/ingestion/archive.py`; CLI
> `python/scripts/archive_version.py`.

A collection's archive is an ordered sequence of **versions** — one per
completed ingest job (a *batch*: that job's chunks + vectors, not the whole
collection) or per delete (a *tombstone*). The registry row orders them;
restore replays them in order. Each version is one directory whose **basename
is the version number**; the workflow emits it as a CWL `Directory` output and
GoWe uploads it under that basename, so it lands at
`…/collections/<id>/versions/<N>/` with no Workspace call and no token inside
any task.

```
<N>/
  manifest.json      identity + counts + sha256/bytes per file (below)
  chunks.jsonl.gz    one record per chunk = the ragstack.embedding_file/v1
                     record minus `embedding` (Chunk.model_dump(), UTF-8, sorted
                     keys). gzip, mtime=0, no filename
  vectors.f32        64-byte header + float32 rows, little-endian, row i is
                     chunk line i of chunks.jsonl.gz
  receipt.json       the load stage's receipt, copied verbatim (pdf-ingest: the
                     load summary) — or a JSON ARRAY of the per-BATCH receipts in
                     batch order (pdf-ingest-scatter: one ShardReceipt per task,
                     each with a `docs` row per document: its `error` and its
                     `chunk_ids`)
  tombstone.json     DELETE versions only: {"format", "count", "doc_ids": [...]}
  triples.jsonl.gz   the GRAPH leg (#350), present only after the extract-graph
                     workflow ran over this version: one Triple record per line
                     (Triple.model_dump(): subject/predicate/object, doc_id,
                     tenant_id, and the #347 evidence fields — evidence,
                     chunk_id, derived_by, confidence, subject_id, object_id;
                     `collection` is empty, the loader stamps it). gzip, mtime=0
```

A tombstone version holds **only** `manifest.json` + `tombstone.json`. The
filenames above are what the writer emits today; a reader never assumes them —
it follows the manifest's **`files` role map** (below). The `triples` role is
**not** written by the ingest workflows: it is added to an existing chunk version
later, by the extract-graph step ([below](#graph-extraction-the-triples-leg-350)),
together with a rewritten `manifest.json` (`graph: true`).

**`manifest.json`** (keys sorted, no timestamps — a re-run of the step is
byte-identical, like the receipts):

| key | value |
|---|---|
| `format` | `"ragstack-archive/1"` |
| `collection_id`, `tenant`, `spec_hash`, `version`, `job_id` | identity: the registry id (not the store name), the tenant, the ADR-0002 build-spec hash, the version number (int), the RAGStack job id. Restore refuses a `spec_hash` that differs from the registry row. |
| `counts` | `{"chunks": rows, "docs": distinct doc_ids}` (`chunks: 0` for a tombstone); plus `"triples": n` once the graph leg exists |
| `files` | **role → filename map** the reader follows: `{"manifest": "manifest.json", "chunks": "chunks.jsonl.gz", "vectors": "vectors.f32", "receipt": "receipt.json"}`, or `manifest` + `tombstone` for a delete, plus `"triples": "triples.jsonl.gz"` once the graph leg exists. Every non-manifest value must have a `sha256` entry and every `sha256` key must be a value of the map (nothing unlisted is trusted). |
| `sha256`, `bytes` | per file, over the bytes **as stored** (i.e. the gzip stream for chunks) — verification needs no decompression; every file is verified before a reader yields anything |
| `vectors` | `{"dim", "rows", "dtype": "float32", "byte_order": "little", "header_bytes": 64}` — must agree with the file's own header (chunk versions only) |
| `chunks_compression` | `"gzip"` — the reader dispatches on this; any other value is refused (`ArchiveCorrupt: unsupported chunks_compression`) |
| `receipts` | how many receipt files went into `receipt.json` (1 = verbatim object, >1 = array) |
| `graph` | `false` as written by the ingest workflows; `true` once the extract-graph step added the `triples` leg. The reader requires the two to agree: `graph: true` without a `triples` role (or the reverse) is `ArchiveCorrupt` — a half-applied extraction, refused rather than guessed at |
| `graph_extraction` | only with `graph: true`: `{"derived_by": "llm", "extractor": <model>, "n_chunks", "n_chunks_empty", "n_chunks_without_triples", "concurrency"}` — the leg's provenance |
| `has_tombstone` | `true` for a delete version |

**`vectors.f32` header** (64 bytes, integers little-endian): `RSF32VEC` magic
(8) · header version `1` (u32) · header length `64` (u32) · `dim` (u32) ·
`rows` (u64) · dtype code `1` = float32 (u32) · byte order `<` (1) · 31
reserved bytes (readers must ignore). Hence `len(file) == 64 + rows × dim × 4`, and
`numpy.memmap(path, dtype="<f4", offset=64, shape=(rows, dim))` reads it
directly. The reader (`read_version`) verifies every sha256, that the header
and manifest geometry agree, and that the file size matches, **before** the
first row — then streams `(chunk_dict, array('f'))` pairs one at a time; any
mismatch is `ArchiveCorrupt`. The writer streams too (bounded blocks of input
lines through a small process pool): 35k × 4096-d packs in seconds with a flat
RSS — the JSONL embed file is never materialised as Python float lists.

**Why gzip, not zstd.** The design names `chunks.jsonl.zst`; `zstandard` is
not a project dependency and shared environments do not get new packages for
one file, so the chunks file is gzip. Readers find the chunks file through the
`files.chunks` role and dispatch on `chunks_compression` (only `gzip` today —
anything else fails loudly rather than mis-reading), so a zstd writer later is
a manifest change plus a reader branch, not a format break. (Vectors are
incompressible float32 either way.)

**Producing it.** `archive_version.py --version N --collection-id <id>
--chunks <emb.jsonl…> --receipt <receipt.json…> --out <dir>` writes
`<dir>/N/`; `--tombstone doc_ids.json` writes the delete form. Both workflows
take `version` and `collection_id` as **required** inputs (the API assigns the
version from the registry), plus optional `spec_hash` / `job_id`. In the
scatter workflow, `ingest_shard.py --embedding-file` writes each PDF's embedded
chunks on their way to the stores, which is what the archive step packs.

## Graph extraction: the `triples` leg (#350)

> Phase 6 of #201. `cwl/extract-graph.cwl` (the tool), `cwl/graph-extract.cwl`
> (the workflow: extract → load), `python/scripts/extract_graph.py` /
> `load_graph.py`, `python/ragstack/graph/{extract_version,archive_load,budget}.py`;
> API side `python/ragstack/graph_extract.py` + `POST /v1/collections/{id}/graph`.

The knowledge graph is a **leg of the collection lifecycle**, archived, restored
and (once #380's eviction half lands) evicted with the collection — but it is
**never part of an ingest**: one LLM call per chunk is roughly an order of
magnitude more than embedding it, so extraction is an explicit, opt-in, budgeted
step over an *already archived* version. Nothing runs until the owner (or an
admin) calls `POST /v1/collections/{id}/graph[?version=n]`, which submits
`graph-extract.cwl` **as the user** over one `versions/<n>/` directory (the
latest chunk version by default; tombstones are skipped):

1. **`extract`** (`extract_graph.py`) reads that version's `chunks.jsonl.gz` —
   text only, the vectors are never touched — and runs `LLMKGExtractor` over
   every chunk with `concurrency` calls in flight (`graph_extract_concurrency`,
   default 8). Every triple carries the #347 stamps: `chunk_id`, `evidence` (the
   verbatim span the model quoted, kept only if it occurs in the chunk),
   `derived_by: "llm"`, `confidence: 1` (the no-launder cap); `tenant_id` comes
   from the chunk's own metadata; `collection` is left **empty** — the physical
   store name is registry knowledge the loader stamps, so the archive stays
   portable. Results are written in chunk order, deduplicated on
   `(subject, predicate, object, doc_id)`, so the same model output gives a
   byte-identical leg. The step emits a **delta directory named by the version**
   holding exactly `manifest.json` (the complete manifest, rewritten:
   `files.triples`, its sha256/bytes, `counts.triples`, `graph: true`,
   `graph_extraction`) and `triples.jsonl.gz`.
2. **`load`** (`load_graph.py`) verifies the leg (manifest + the triples file's
   sha256 — a delta has no chunks to verify), resolves the **physical** collection
   name from the registry entry named by `collection_id` (never the command line;
   the worker sees the same registry the API does, as for restore), takes **one**
   live count of that collection's triples, and refuses the whole load — exit 4,
   nothing written — when `live + incoming` would exceed the budget; otherwise it
   upserts in batches, every triple stamped `collection = <physical name>`
   (idempotent: both graph stores MERGE on the triple's key). Neo4j credentials
   come from the worker's environment, never from a workflow input.
3. The delta Directory is the workflow's **only** output. GoWe post-stages a
   Directory output's listing under `<output_destination>/<basename>/` by
   basename **with overwrite** (`pkg/bvbrc/workspace.go` `WorkspaceUpload`,
   `Overwrite: true`; `scheduler/workspace.go` `stageFileInTree`), so it lands
   *on* `versions/<n>/`: `manifest.json` is overwritten — **the one intended
   overwrite of an archived file** — `triples.jsonl.gz` is added, and the
   chunk/vector/receipt files already there are untouched because they are not
   in the output. Post-staging happens only for COMPLETED submissions, so a
   `load` refusal (or any step failure) delivers nothing: the archive is never
   half-updated.

**Budgets** (`config.py`, #291's siblings): `graph_max_triples_per_collection`
(default **200,000** — the 50k chunk cap × ~4 triples per prose chunk, with
headroom; `0` disables) is checked once per job by `load` with one live
`GraphStore.stats(collection=…)` count (collection-wide, every tenant — the
collection is the unit), and by `extract` against its own output alone (a
version that could never be loaded is refused before its leg is written); the
refusal line is `graph_cap_exceeded: live=L incoming=I cap=C would_fit=W` on
stderr (`live=?` from `extract`) and the API classifies the FAILED submission
by `error.context.exit_code == 4` into the job error `graph_cap_exceeded`, as
the chunk cap does. `graph_extraction_jobs_per_owner` (default **1**) bounds
in-flight extractions per owner — a second is **429 + `Retry-After`**, like the
upload guard; admins are exempt. Extractions are jobs of kind `graph`
(`IngestJob.kind`, an additive column; `""` is an ingest — every legacy row) and
count **separately** from the one-in-flight ingest rule: a multi-hour extraction
does not freeze the owner's uploads.

**Completion is two-phase**, exactly as for the ingest archive: the job completes
only when the engine reports the delta **delivered** (`output_state`), and only
then does the registry row record the version in `graph_archived_versions`
(additive lifecycle column, all four backends) — the flag a follow-up gates
eviction's graph drop on (#380: eviction may only destroy what exists somewhere
else). `upload_failed` fails the job `OUTPUT_STAGING_FAILED` with nothing
recorded (the triples were loaded; the leg is not archived). **Idempotent per
version**: a version whose leg exists answers 202 with `job_id: null` — "exists"
meaning the row says so, or the archived manifest says `graph: true` **and** the
triples file is `stat`ed present at the manifest's recorded size (then the row is
repaired too). The manifest alone is never trusted: the engine uploads a
Directory's listing in filename order, `manifest.json` before `triples.jsonl.gz`,
so an upload failing between the two — or an engine crash mid-upload, which
leaves `output_state: uploading` forever and the job failed by the delivery
timeout — leaves a manifest claiming a leg that was never delivered; that state
reads as *not extracted*, the next `POST` resubmits, and the extract tool
overwrites the stale entries. **One extraction per collection in flight**,
whoever the caller (admins included): two deltas post-staged onto one
`versions/<n>/` would interleave manifest and triples into an `ArchiveCorrupt`
archive, so a second `POST` for a collection with a `graph` job running is 429.

**An outage is not an empty graph.** `LLMKGExtractor.extract_chunk` raises
`ExtractionFailed` when the LLM *call* failed (a reply the model did give but that
parses to nothing is "no facts", as on the ingest path); the driver counts those
as `n_chunks_failed` (in `graph_extraction`) and refuses the run — exit **1**,
retryable, nothing written, no delta — when every attempted chunk failed or the
failed share exceeds `graph_extraction_max_failed_fraction` (default 0.5, the
workflow input `max_failed_fraction`). Delivering `graph: true` with zero triples
after an outage would be permanent under idempotency-per-version.

**Restore** replays the leg: `load_embeddings.py --replay` loads a version's
triples (scoped to the collection, never capped — it re-admits what was archived)
right after that version's chunks, when the worker's pipeline has a graph store
(#399 gives it one under `GRAPH_BACKEND=neo4j`). A version without a leg replays
exactly as before.

**Throughput is measured, not budgeted** (#355): the number that sizes
`graph_extraction_jobs_per_owner` is chunks/s against the real LLM endpoint over
a ~35k-chunk collection, recorded on #350 when the container run happens. The
hermetic perf test (`tests/perf/test_extract_graph_perf.py`) prints the driver's
rate over 100 chunks at concurrency 8 against a 10 ms fake LLM.

**Running it by hand** (cwltool, no engine): `RAGSTACK_FAKE_LLM=1` swaps a
deterministic fake for the endpoint; `graph_backend: memory` + an inline
`COLLECTIONS_JSON` registry make the load step hermetic —
`tests/integration/test_graph_extract_cwl.py` is the worked example.

## Batch semantics on the GoWe path (#203 2b)

`cwl/pdf-ingest-scatter.cwl` ingests a **batch** of PDFs per task: a `batch`
ExpressionTool groups the submitted `pdfs: File[]` into `File[][]` by
`batch_size` (default 20; `1` = one task per PDF, the Option-A shape for a small
upload), and every batch runs extract → `ingest_shard` → one receipt. Per-task
fixed overhead (dispatch, container start, interpreter, tokenizer load: ~2–4 s)
is thereby paid once per 20 PDFs instead of once per PDF.

**Per-document status.** A batch's `ShardReceipt` carries a `docs` row per
document — `error: ""` means its chunks were upserted, otherwise the row names
why not; `chunk_ids` are that document's. `GoWeBackend` maps each work item to
its row by **source basename** (the engine pre-stages a `ws://` input under its
basename; the extract tool records that path), so the job's per-item status,
chunk ids and error are exact per document regardless of how many receipts the
archive holds. An Option-A archive (one receipt per item, no rows to match)
still maps positionally.

**Failure rules.**

| what happened | where it is recorded | task exit |
|---|---|---|
| a scanned / image-only PDF (no text) | the extract report skips it; `ingest_shard --extract-report` writes its row with the constant `NO_TEXT_ERROR` (`ragstack.ingestion.loaders`) — the same string the local path records, so `GROUP BY error` counts it on both paths | 0 (batch continues) |
| a loaded document with no embeddable chunk (empty, or every chunk quarantined) | its row: `NO_CHUNKS_ERROR` (`ragstack.ingestion.receipts`) | 0 |
| **every** document of the batch failed | every row with its own error; the receipt is still `completed` (`n_docs_failed == n_docs`), the embedding file header-only | **0** — a processed batch, not a failed task |
| the batch itself failed (shard unreadable, embedder/store down) | the receipt is `failed`; every row without a more specific error carries the batch error | non-zero (the engine retries the task) |

Why an all-failed batch exits 0: GoWe treats any non-zero exit as a task
failure (it honours no `successCodes`), retries it, then fails the step, its
dependants and the submission — but the sibling batches have already upserted
(ingest is coupled embed+load), so `pack` would never run, no `versions/<n>/`
would exist, the stores and the archive would diverge, and a later restore
would silently omit those documents. Per-document failure is therefore data
in the receipt, never a task failure. Known residual (a #357 format decision):
if **every** batch of a run is all-failed there are zero rows to pack, the
archive tool refuses a zero-row version and the run fails with the per-item
detail lost.

The embedding file — hence the archive version — holds only the successful
documents' chunks. A non-zero task fails the submission before any archive
exists; the API then reports the submission state on every item (no receipts to
read). Only receipts that name **none** of the documents are a
`GoWeContractError` (a workflow that cannot report), never "every document
failed". Two work items sharing a source basename are refused at submission
(`GoWeContractError`): rows are matched by basename, and the engine would stage
them onto one file anyway.

**Poll interval** is per submission: ≤ 50 items poll every 0.5 s, larger runs at
`GOWE_POLL_INTERVAL` (never slower than the setting). **Tokenizer cache:** the
worker image reads the HF tokenizer from `HF_HOME` (`/rag/cache`), which the
GoWe worker must bind into the container (`--extra-bind`); see `cwl/README.md`.

## Restore: replaying an archive (#358)

A collection whose stores were evicted is `dormant` on the registry; the first
authenticated read or ingest (or `POST /v1/collections/{id}/restore`) submits
[`cwl/restore-collection.cwl`](../cwl/restore-collection.cwl) **as the user**
over the `ws://` `versions/<n>/` directories and answers **503 + `Retry-After`**
until it completes (`restoring`); a refused archive (sha256 / geometry /
`spec_hash` mismatch — loader exit 3, before any store is created) makes it
`lost` → 409 until the owner repairs the archive and restores explicitly.
A restore takes a slot against `max_collections` exactly as a create does (#381): the
`dormant → restoring` swap is `begin_restore` — count and swap in one atomic store
section — and at the bound the gate first evicts one least-recently-accessed active
collection whose archive is current, or answers 503 + `Retry-After` ("tenant at
capacity") with the row left `dormant`, so the physically-present count never exceeds
the bound across creates and restores.
`load_embeddings.py --replay DIR…` is the tool: verify every version, then per
version delete each document's prior chunks and stream the upserts (tombstones
delete by doc id), and load a version's graph leg after its chunks when one exists
and the worker has a graph store (#350). The API-side settings, all at the end of `config.py`:

| Setting | Default | Meaning |
|---|---|---|
| `collection_access_flush_seconds` | `60` | `last_accessed_at` is batched in-process and flushed in one registry write this often (and at shutdown) — never per request. |
| `collection_state_cache_seconds` | `5` | How long the resolution path memoizes a row's state; also the cross-process lag for a state change made elsewhere. |
| `collection_restore_retry_after` | `30` | `Retry-After` (seconds) on the 503 while `dormant`/`restoring`. |
| `collection_restore_timeout` | `3600` | A `restoring` row older than this with no live watcher in this process is presumed orphaned and reset to `dormant`; also the watcher's poll timeout. |
| `collection_restore_poll_interval` | `5` | Seconds between submission polls. |
| `collection_restore_cwl` | `""` | Absolute path to `restore-collection.cwl`; empty = the repo copy next to the package. |
| `collection_restore_workflow_name` | `ragstack-restore-collection` | Name the workflow is registered under. |
| `collection_restore_inputs_json` | `{}` | Extra/overriding static inputs — typically `qdrant_url` / `es_url` as seen from the worker. Worker group comes from `gowe_worker_group`. |

## Eviction: the active bound (#359)

`max_collections` bounds **active** collections (`state == active` — the ones whose
Qdrant collection and ES index exist), not registered ones; a `dormant` collection
costs nothing physical. When a create meets the bound, `ops/evict.py` chooses the
least-recently-accessed active collection whose archive is current
(`archive_pending=false`, `versions` non-empty) and makes it dormant: the registry row
is compare-and-swapped `active → dormant` **first** (readers get 503 + `Retry-After`
from that instant), then the two stores are dropped, best-effort per target — a
failed drop keeps the row `dormant` with the leftover named in its `state_reason`
for the store inventory (#299) to find, and nothing is dropped when the swap lost.
Never a victim: a collection with an in-flight ingest job (the job store stamps
`collection_id` on every job for this), one whose stores are the legacy shared
surface's (the settings-derived default, or a spec that **claims** its stores — evicting
it would destroy every tenant's legacy data), or one whose store another registry id
also serves. With no candidate the create is **507**, naming the per-reason counts.
`POST /v1/admin/collections/evict?need=k[&dry_run=true]` runs the same policy by hand.
`last_accessed_at` is the LRU key (batched writes — the tracker is flushed before
selection); a never-accessed collection falls back to its creation time. The graph
leg is not touched yet: `GraphStore` has no per-collection delete (phase 6 of #353).

`MAX_COLLECTIONS` is set per tenant from the tightest of three measured ceilings at
60 % (memory mappings vs `vm.max_map_count`, threads vs the process limit, resident
RAM) over ten loaded collections — measured value: see
`docs/runbooks/active-collection-bound.md`.

## Chunk cap: the per-collection bound (#291)

`max_chunks_per_collection` (default **50,000**: 1,000 documents × the measured ~34 chunks
per article, plus headroom; per user 5 × 50k = 250k chunks ≈ 4 GB of 4096-d vectors, so a
tenant is bounded by `max_collections` long before bytes) bounds the chunks ONE
**user-created** collection may hold. User-created is derived, not stored: an active owner
row whose owner is not an admin (`api/access.py::is_user_created` — not the backfill owner,
not in `ADMIN_SUBJECTS`, not an admin API key's tenant, no stored admin role). Curated
corpora — the legacy shared surface, backfilled or admin-created collections — are exempt
unless the registry entry sets an explicit override: `CollectionSpec.max_chunks` (`null` =
derive, `0` = exempt, `N` = cap at N), settable on the registry only (a SQL `UPDATE
collections SET max_chunks = …` or the key on the JSON entry; there is no PATCH route). The
JSON registry file is unchanged until an override is actually set.

Enforced **once per ingest job, before the first write**: one live `VectorStore.count()`
(unfiltered — the collection is the unit), never a per-chunk store call, never a counter (a
`DELETE /v1/documents` frees budget by construction). `live + incoming > cap` refuses the
**whole job** — nothing is written, not the part that would have fit — with the job error
label `chunk_cap_exceeded` and, on every item / receipt, the formatted refusal
`chunk_cap_exceeded: live=L incoming=I cap=C would_fit=W`. The poll response
(`GET /v1/ingest/{job_id}`) keeps its shape: `failed`, all items `failed`; the label is on
the job row (`GET /v1/jobs`). Per path:

| Path | Where | `incoming` |
|---|---|---|
| API, local (`POST /v1/ingest`, `/v1/ingest/upload`) | `ShardedIngestor.ingest_manifest` — the manifest is the job: every remaining item is loaded + chunked (text only, no GPU, no store), one count, then the admitted job embeds and indexes the very chunks it was sized from (`IngestionPipeline.ingest_prepared`) — nothing is loaded twice | post-chunk, pre-quarantine (a conservative overcount) |
| API, gowe | the API derives the cap per job and passes it as the workflow input `max_chunks` → `ingest_shard --max-chunks`; each scattered task counts once and refuses its own shard (`run_shard`); the API lifts the receipt's label onto the job | post-embed, exact — per task, so concurrent tasks may collectively overshoot by the other tasks' shards |
| bulk (`scripts/load_embeddings.py`) | the invocation is the job: the files' header counts are summed, one count before the first file is read; refused = exit 1 + `chunk_cap` in the summary. User-created here = `spec.owner` set and neither the backfill owner nor an `ADMIN_SUBJECTS` entry (no ACL/user store in a CLI) | the files' header `count`s |
| replay (`--replay`, restore) | **never capped** — it restores what was already admitted | — |

A byte-identical re-ingest at the cap is refused too (delete-prior would net to zero, but
`incoming` is what the job would write): the conservative reading of "refuse the whole
batch". `max_chunks_per_collection=0` disables the default deployment-wide (overrides still
apply). The value is exposed by `GET /v1/config`.

## Known gaps

Be clear-eyed about what does **not** work today:

- **PDF → GoWe is built for Workspace sources only.** Under `INGEST_BACKEND=gowe`
  the two ingest endpoints submit `ws://` inputs as the caller (#202/#203, above);
  a server-side path is refused there. OCR for scanned PDFs does not exist —
  they are counted per job under the constant `NO_TEXT_ERROR` on both paths, the
  data the OCR decision (#202) needs.
- **No ragstack workflow registered on the engine.** `GoWeBackend` requires an
  absolute `gowe_workflow_cwl`; there is no ragstack bulk-ingest workflow
  registered on a running GoWe engine out of the box, and nothing in this repo
  reads CWL step outputs back into a store (`libraries-spec.md §6`).
- **No GoWe worker image.** Running `ingest_shard.py` on real workers needs a
  ragstack + deps worker image — **#135**.
- **Eval harnesses still mint unclaimed stores.** `scripts/eval/*` call
  `ensure_collection()` with a name of their own choosing (`chunkcmp_*`,
  `oa_smoke_*`). They are deliberately throwaway, so forcing each comparison arm
  through a registry entry is the wrong shape — what they need is an explicit
  *ephemeral* convention that the store inventory can recognise and reclaim.
  Until that exists they remain the last source of stores no registry claims,
  and they are why **#293**'s auto-reclaim half is still blocked.

The bulk CLI and local API paths are the ones that run today; the GoWe path is
scaffolded and validated for pre-sharded JSONL but is not a PDF-in ingest route.
