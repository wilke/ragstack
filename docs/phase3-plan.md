# Phase 3 — Ingest model selection (`POST /v1/collections`)

**Status:** in progress (branch `feat/model-registry-p3`). Companion to
[model-registry.md](model-registry.md) §5 (roadmap item 3). Scope locked 2026-07-08.
**Progress:** Steps 1–3 done (contracts; real chunk config on `CollectionSpec`;
`POST`/`DELETE /v1/collections` handler with content-addressing + write-through
persistence). Remaining: Step 4 (collection-aware ingest), Step 5 (Go stub), Step 6
(conformance).

Goal: an HTTP front door to the **build-time** path — create a content-addressed
collection that binds a *registered* embedding model + chunk config, then populate it —
so the supply side of the Compare `collection` axis becomes an API call, not a CLI-only
operation.

---

## 1. Why a new endpoint (rationale)

The pivotal design fact ([model-registry.md](model-registry.md) §1): **build-time config
*is* collection identity.** Embedding model + chunker are baked into a collection at
ingest, content-addressed with a provenance manifest. You *cannot* re-point an existing
index at a new embedder — the vectors are model-specific. So "change the embedding model"
does not mean "edit"; it means "**create a new collection**."

That drives the choice of `POST /v1/collections` over extending `POST /v1/ingest`:

1. **The resource created is a *collection*, not a document event.** `/v1/ingest` means
   "append documents to the pipeline the server is already configured with." Selecting a
   model/chunk creates a new corpus *identity* — name the endpoint after the resource it
   mints.
2. **It protects the content-addressing invariant.** If `/v1/ingest` took an `embedding`
   param, a caller could push documents into an existing collection under a *different*
   embedder → mixed/incompatible vectors or a dim error. Binding the embedder once, at
   creation, makes that footgun structurally impossible.
3. **Clean CRUD symmetry.** `GET /v1/collections` (list) already exists;
   `POST /v1/collections` (create) + `DELETE /v1/collections/{id}` completes the resource.
   The model **registry** is the catalog of *available models*; **collections** is the
   catalog of *built corpora* — two clean nouns.
4. **Different lifecycle / auth / scale.** Creating a collection is a heavy, one-time,
   privileged, likely-async build; document ingest is an ongoing append. Separate
   endpoints let them evolve and be permissioned independently.
5. **Supply side of the Compare axis.** Compare A/Bs *collections*; `POST /v1/collections`
   is "mint a new point on that axis" — the endpoint name matches the mental model.

The rejected alternative (extend `/v1/ingest`) wins only on "one fewer endpoint," at the
cost of overloading an append verb with corpus-definition semantics and exposing the
mismatched-embedder footgun.

---

## 2. Load-bearing finding that shapes scope

`POST /v1/ingest` today writes into the server's **single, statically-configured**
pipeline — it takes only `{source, metadata}` and has **no `collection` targeting**
([model-registry.md](model-registry.md) §2). The store layer *can* write to any named
collection (the CLI `ingest_jsonl.py --collection` proves it), but the HTTP ingest
endpoint can't. So Phase 3 is really **two** capabilities, split into sub-phases:

| Sub-phase | Endpoint | What it does | Sync? |
|---|---|---|---|
| **3a — create** | `POST /v1/collections` | Resolve the `embedding` model-ref against the Phase-1 registry + validate chunk config → derive the content-addressed name → register a `CollectionSpec` + write a provenance manifest via `make_ingest_manifest`. Creates an **empty** collection. | **Sync** (no embedding) |
| **3b — populate** | `POST /v1/ingest` + a new `collection` field | Route documents into a *named* collection using **that collection's** bound embedder/chunker (not global settings). Reuses Path B (`ShardedIngestor`) + the GoWe bulk path. | **Async** (job_id) |

Creation defines identity (sync, cheap, privileged); population appends data (async,
heavy). That distinction is the whole reason the endpoints are separate.

---

## 3. How PDFs fit (reminder + boundary)

Real PDF parsing lives in exactly one place — **`PdfLoader`** (PyMuPDF,
`ingestion/loaders.py:80`): plain per-page text, **no OCR** (scanned PDFs hard-fail with
`LoaderError`), **no tables**, minimal metadata. It is reachable **only** via
`POST /v1/ingest` with a server-local path, and only when `ingest_backend=local`. The
**bulk/GoWe/CLI plane never opens PDFs** — `ingest_shard.py` hardcodes `JsonlLoader`; the
CWL scatters over JSONL shards. At scale we ingest *pre-extracted* PDF text (an upstream,
out-of-repo step produces the JSONL).

**Boundary:** Phase 3 does **not** change PDF handling. Collections are populated from
JSONL (bulk/GoWe) or one-off local PDFs via `/v1/ingest` (PyMuPDF, as today). See §5.

---

## 4. Work breakdown (contract-first, per CLAUDE.md)

**Step 1 — Contracts (source of truth, first)**
- `contracts/openapi.yaml`: `POST /v1/collections` (+ `DELETE /v1/collections/{id}`).
- New `contracts/schemas/collection_create_request.json` —
  `{embedding: model-ref, chunk:{method,size,overlap,params?}, label?, id?}`,
  `additionalProperties:false`.
- Extend `IngestRequest` with optional `collection` (omitted ⇒ server default ⇒
  backward-compatible).
- Reuse existing `CollectionInfo` for the response.

**Step 2 — `CollectionSpec` gains real chunk config**
- Add `chunk_overlap` (+ `chunk_params`) to `CollectionSpec` (`api/collections.py`) and to
  the built `CollectionEntry`, so a created collection carries the *actual* chunking, not
  just display labels. (Closes the gap the cookbook review flagged.) Backfill defaults so
  existing `collections.json` still loads.

**Step 3 — `POST /v1/collections` handler** (`api/routers/collections.py`, admin-gated)
- Resolve `embedding` via `get_model_registry` → assert `task=="embedding"` and `dim>0`;
  unknown → 404, wrong-task → 400 (reuse Phase-1 `RegistryError` taxonomy + SSRF
  allowlist). Validate `chunk` against `make_chunker`'s accepted methods.
- Derive the content-addressed name from `(model, dim, chunk)`; persist a `CollectionSpec`
  into the `CollectionRegistry` (JSON write-through — single-worker caveat, Postgres-later,
  same as the model registry). Write the provenance manifest.

**Step 4 — collection-aware ingest** (`api/routers/documents.py` + `deps`)
- Honor `IngestRequest.collection`: build the ingest pipeline's embedder/chunker from
  *that* collection's spec (a per-collection builder in `deps`, mirroring how query
  resolves `collection`) instead of the static singletons.

**Step 5 — Go parity stub** — `POST /v1/collections` returns a schema-valid response
(Python authoritative); conformance GET-schema stays green.

**Step 6 — Tests** — Python API (create → GET reflects; unknown-ref 404; llm-as-embedding
400; SSRF reject; content-address determinism; ingest-with-`collection` writes to the
right store) + conformance schema on both impls.

---

## 5. Explicitly deferred (named, not silently dropped)

- **Phase 3.5 — bulk PDF extraction:** teach the GoWe plane / `ingest_shard.py` to run
  `PdfLoader`, + address the **OCR gap** (scanned PDFs hard-fail today) and table/metadata
  extraction.
- **Create+ingest convenience wrapper** (one call) — can wrap 3a+3b later.
- **Postgres-backed registry** — before multi-worker.
- **Scalar hot-reload** (`rrf_k`/`top_k` retriever rebuild) — carried from Phase 1.

---

## 6. Risks / watch-items

- Step 4 touches the ingest wiring that PDFs and Path B share — regression-test a plain
  `/v1/ingest` (no `collection`) to prove backward compatibility.
- Content-addressed name collisions across tenants: the name is `(model,dim,chunk)`-derived
  and tenant isolation is a *filter*, not a separate collection — confirm a second tenant
  creating the "same" spec reuses the collection rather than erroring.
