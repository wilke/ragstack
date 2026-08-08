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
| **Status** | **Works** | **Partial** — shards only; PDF→GoWe is an unbuilt gap | **Production** — the operator path for big corpora |

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
- **You have raw PDFs and want them ingested through GoWe** → not available yet;
  see Known gaps.

---

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

## Known gaps

Be clear-eyed about what does **not** work today:

- **PDF → GoWe.** In `gowe` mode each item's `source` must already be a JSONL
  shard, so the browser PDF-upload path does **not** flow through GoWe. Doing so
  needs CWL-plane PDF extraction (a `discover`/`triage`/`extract` stage that
  does not exist — [`libraries-spec.md §6`](libraries-spec.md), stage 2 is
  marked new work). Tracked by **#202** (no file-upload / Workspace-reference
  ingest path) and **#203** (route user-triggered ingest to GoWe).
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
