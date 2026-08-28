# Model registry, task assignment & dynamic config

**Status:** Phases 1–3 shipped; Phase 4 (the config page) planned — see [Roadmap](#5-roadmap). Phase 1 was [PR #166](https://github.com/wilke/ragstack/pull/166), branch `feat/model-registry`.
**Audience:** anyone reviewing or extending the dynamic-config work.

Goal: **change model configuration on the fly** — register models and model URLs, assign them to tasks (embedding, chunking, LLM, reranker), and reconfigure a running server without a restart, surfaced through a config page for the API and workflows.

---

## 1. The core principle: two classes of config

The single most important design line, derived from how the server wires models today (`python/ragstack/api/deps.py`):

| Class | Tasks | Mechanism today | "Change it" means |
|---|---|---|---|
| **Build-time** | embedding, chunking / tokenizer | Baked into a **collection** at ingest; content-addressed + provenance manifest | **Create a new collection** (re-ingest). You *cannot* re-point an existing index at a new embedder — the vectors are model-specific. |
| **Hot-swappable** | LLM, reranker, rewriter | `app.state` singletons read per-request via `Depends` | **Reassign live** — rebuild the client and swap the attribute atomically. No restart. |

A subtlety: some *scalars* (`rrf_k`, `retrieval_candidate_multiplier`, graph params) are **baked into the `HybridRetriever` instances** at construction, so changing them is not a trivial attribute swap — it needs a retriever rebuild. Treat those as "apply = rebuild retrievers" (still no restart), separate from the trivial generator/reranker swap. Deferred past Phase 1.

**Why this matters for the UI:** the config page must frame embedding/chunker changes as "register a model → **build a collection**", never as an in-place edit — otherwise users expect the impossible.

---

## 2. How model selection works today (baseline)

- **Query / retrieve** pick a model *indirectly* via the **`collection`** parameter. A collection *is* a bound `(embedding model + chunker + stores)` tuple, content-addressed with a provenance manifest. Retrieval must use the exact embedder the corpus was indexed with — so there is no direct "embedding model" query param, by design.
- **Ingest over HTTP** (`POST /v1/ingest`) took only `{source, metadata}` when this was written, and used the server's statically-configured pipeline. *Since Phase 3 it also takes `collection`* — which is how a per-request model/chunker is chosen: by naming a collection already built that way, never by naming a model on the ingest call. The build spec itself is fixed at `POST /v1/collections` (below) and is immutable thereafter (ADR-0002). There is still no standalone chunking endpoint.
- **The CLI** (`scripts/ingest_jsonl.py`) *does* accept `--embedding-model / --embedding-api / --chunk-method / …` — that is how the demo's collections were built.
- **LLM / reranker / rewriter** are built once at startup from `settings` into `app.state` (`_build_llm`, `_build_reranker`, `_build_rewriters` in `deps.py`) and read per-request via `get_generator` / `get_reranker` / `get_rewriters`.
- **Config is read-only**: `GET /v1/config` (admin) reports effective config; there is no write path. Changing anything means editing env / `collections.json` and restarting.

The model registry generalizes the static `collections.json` + env wiring into a **runtime, CRUD-able registry with live assignment** for the hot-swappable tasks.

---

## 3. How it fits the comparison matrix

The Compare tab already A/Bs collections and per-lane levers (`retrieval_mode`, `rewrite`, `rerank`, `top_k`, `use_graph`). The registry plugs in on two sides:

- **Supply side of the `collection` axis** — register embedding/chunk models → ingest → the collections Compare already compares. (Phase 3 exposes the ingest side over HTTP.)
- **New per-lane lever columns** — per-request `llm` / `reranker` refs let you hold the corpus fixed and compare *answers across LLMs* or *rankings across rerankers*. These drop into the existing per-lane Compare controls. (Phase 2.)

So: embedding + chunking are compared **as collections**; LLM + reranker are compared **as per-lane levers**.

---

## 4. Phase 1 — as built

### 4.1 Data model

**`ModelEntry`** (`python/ragstack/api/model_registry.py`):

```jsonc
{
  "id": "mango-scout",          // stable ref (immutable; the URL path)
  "task": "llm",                // embedding | tokenizer | llm | reranker
  "provider": "vllm",           // sidecar | openai | vllm
  "base_urls": ["http://localhost:9005"],  // fleet endpoints; SSRF-checked
  "model": "RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic",
  "dim": null,                  // required (>0) for embedding models
  "params": {}                  // free-form (e.g. max_tokens)
}
```

**Assignments**: a `task -> model_id` map for the **hot-swappable** tasks only (`llm`, `reranker`). `HOT_SWAPPABLE = {"llm", "reranker"}`.

> **Note on `rewriter`:** the LLM-backed rewriters (`multiquery`, `hyde`) are derived from the LLM client, so they are rebuilt whenever the `llm` assignment changes — there is no separate `rewriter` assignment in Phase 1.

### 4.2 API (admin-gated, mounted at `/v1/admin`)

| Method & path | Purpose | Notable responses |
|---|---|---|
| `GET /v1/admin/models/registry` | list models + current assignments | `200 ModelsRegistryResponse` |
| `POST /v1/admin/models/registry` | register a model | `201`; `400` bad task/provider/SSRF/duplicate |
| `PUT /v1/admin/models/registry/{id}` | replace a model | `200`; `404` unknown; `400` invalid |
| `DELETE /v1/admin/models/registry/{id}` | remove a model | `204`; `404` unknown; `409` if assigned |
| `PATCH /v1/admin/config/assignments` | bind hot-swappable task → model, **apply live** | `200 ModelsRegistryResponse`; `404` unknown model; `400` wrong task; `422` non-hot-swappable task |

Every route requires the **admin** role (`require_role(ROLE_ADMIN)`), like `GET /v1/config`.

**Apply semantics** (`deps.apply_assignment`): for `llm`, rebuild `OpenAILLM(base_url, model, http, api_key)` → set `app.state.generator = RagGenerator(llm)` and `app.state.rewriters = _build_rewriters(llm)`. For `reranker`, set `app.state.reranker = SidecarReranker(base_url, http)`. A `null` value reverts the task to its settings-configured default (which may itself be `None` → task disabled). Attribute assignment is atomic in CPython and in-flight requests already captured the prior object via `Depends`, so no lock is needed.

> ⚠️ **Single-endpoint in Phase 1.** `ModelEntry.base_urls` is a list, but the hot-swap clients (`OpenAILLM`, `SidecarReranker`) are single-endpoint, so `apply_assignment` uses **`base_urls[0]` only** — extra endpoints are ignored (and logged as a warning). Multi-endpoint fan-out / failover for a task is **not** a hand-rolled pool here; it belongs in the **Go embedding-router sidecar** ([ADR-0001](adr/0001-execution-topology.md)) — the registry stays the control plane (it holds `base_urls`), the router becomes the data plane that consumes them. Registering extra URLs today is forward-compatible config, not live fan-out. (The existing `PooledEmbedder` fans out only for the build-time **embedding** task, which is not registry-driven.)

### 4.3 Example flow

`$API` is **your own** API's base URL. Do not copy a bare `localhost:8000` from
this page: on the deployment host that port is a production API, and a
production-resolving default in a doc is the defect class of #363/#369/#392.

```bash
API="http://127.0.0.1:${MY_SCRATCH_PORT}"   # or your tenant's URL

# register the LLM
curl -X POST "$API/v1/admin/models/registry" -H 'content-type: application/json' -d '{
  "id":"mango-scout","task":"llm","provider":"vllm",
  "base_urls":["http://localhost:9005"],
  "model":"RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic"}'

# assign it live (no restart) — rebuilds generator + rewriters
curl -X PATCH "$API/v1/admin/config/assignments" -H 'content-type: application/json' -d '{"llm":"mango-scout"}'

# subsequent /v1/query calls now generate with mango; revert with:
curl -X PATCH "$API/v1/admin/config/assignments" -H 'content-type: application/json' -d '{"llm":null}'
```

### 4.4 Persistence & security

- **Persistence:** `settings.models_registry_file` (`MODELS_REGISTRY_FILE`) — a `models.json` write-through file (`{"models": [...], "assignments": {...}}`). Loaded in the lifespan; persisted assignments are **re-applied** over the settings defaults on startup, so a change survives restart. Empty path → in-memory only.
  - ⚠️ **Single-worker assumption.** A file does not share live `app.state` swaps across processes. With multiple uvicorn workers this must move to a shared store (Postgres is already in the stack). Start single-worker; migrate if/when multi-worker is real.
- **SSRF gate:** `settings.model_url_allowlist` (`MODEL_URL_ALLOWLIST`, comma or JSON) — every `base_url` is matched on its parsed **(scheme, host[, port])** against an allowlist entry; **fails closed** (nothing allowed if unset). An entry with no explicit port allows any port on that host (`http://localhost` → `http://localhost:9005`); an entry that pins a port requires it. Matching the parsed authority (not a raw string prefix) is deliberate — a prefix check would let `http://localhost.evil.com` past an allowed `http://localhost`. The server *calls* these URLs, so this is a required control. Default: `http://localhost`, `http://127.0.0.1`.
- **Admin only.** All routes gated by the admin role.
- **No secrets in payloads.** API keys are never accepted in a `ModelEntry`; the LLM key comes from `settings.openai_api_key` (referenced, not stored in the registry). A future enhancement can reference a named secret per entry.

### 4.5 Contracts and tests

- **Contracts:** `contracts/openapi.yaml` (5 paths) + `ModelEntry` / `ModelsRegistryResponse` / `AssignmentsPatch` schemas + `contracts/schemas/models_registry_response.json`.
- **Tests:** 16 python API tests (`tests/api/test_model_registry.py`: CRUD, SSRF reject, live llm/reranker swap, unassign-reverts, delete-while-assigned `409`, wrong-task `400`, build-time `422`, admin-required `403`) + conformance on both impls (`conformance/test_model_registry.py`, GET schema; skips on non-admin like the other admin conformance).

### 4.6 Live verification (2026-08, on the then-`:8000` demo — that port now serves production; re-verify against a scratch server)

| Check | Result |
|---|---|
| `GET` empty → register → assign → `GET` reflects | ✅ `assignments: {"llm":"mango-scout"}` |
| SSRF (`http://evil.example.com`) | ✅ `400` |
| Persisted to `models.json` | ✅ file written |
| Survives restart (reloaded + re-applied) | ✅ registry present after restart |
| Keyless-non-admin | ✅ `403` |
| **Real generation via the hot-swapped LLM** | ✅ `/v1/query` → grounded 703-char answer citing `[1]`, ~1.6 s (mango up) |
| `app.state.generator` None→RagGenerator on assign | ✅ asserted by `test_assign_llm_hot_swaps` |

---

## 5. Roadmap

Phase 1 is standalone and independently mergeable. Later phases each need an explicit go-ahead.

1. **Registry + hot-swap** — ✅ **done** (this doc, PR #166). No re-ingest; live LLM/reranker swap.
2. **Query-time levers** — ✅ **done** (PR #167). Per-request `llm` / `reranker` refs on `/v1/query` (+ reranker on `/v1/retrieve`); `GET /v1/models/available` picker; Compare per-lane `llm` / `rr·model` selects. Overrides build an ephemeral client (no `app.state` mutation); unknown → 404, wrong-task → 400.
3. **Ingest model selection** — ✅ **done**. `POST /v1/collections` accepts `{embedding: <model-ref>, chunk: {...}}` and builds a content-addressed collection; `embedding` resolves against this registry. Both fields are **optional and admin-only**: omitted → the server's default build spec is resolved into concrete values at create time; supplied by a non-admin → **403** (they decide what every future ingest into that collection produces, and `embedding` names admin-registered infra). The open decision below resolved to the dedicated endpoint: `/v1/ingest` gained only a `collection` field, so ingest *selects* a build spec, it never sets one.
4. **Config page** — frontend tabs: **Models** (registry CRUD), **Assignments** (live-editable for llm/reranker; build-time shown as "create a collection"), **Workflows** (parameterize the offline CWL embed/load runs by model ref). Built on `GET /v1/config` + the registry endpoints.

### Open decisions (carried forward)
- **Persistence:** file (`models.json`, single-worker) — current default. Move to Postgres before multi-worker.
- ~~**Ingest surface (Phase 3):** extend `POST /v1/ingest` vs. a dedicated `POST /v1/collections`.~~ **Settled:** the dedicated endpoint. `/v1/ingest` takes `collection` only.
- **Scalar hot-reload:** whether to add the "rebuild retrievers to change `rrf_k`/`top_k`" path (deferred from Phase 1).

---

## 6. Code map (for reviewers)

| Area | File |
|---|---|
| Registry state + validation + persistence | `python/ragstack/api/model_registry.py` |
| Hot-swap applier + lifespan load + `get_model_registry` dep | `python/ragstack/api/deps.py` (`apply_assignment`) |
| Endpoints | `python/ragstack/api/routers/models_registry.py` |
| Router mount | `python/ragstack/api/main.py` |
| Settings | `python/ragstack/config.py` (`models_registry_file`, `model_url_allowlist`) |
| Contracts | `contracts/openapi.yaml`, `contracts/schemas/models_registry_response.json` |
| Tests | `python/tests/api/test_model_registry.py`, `conformance/test_model_registry.py` |

### Operating the demo
Admin endpoints require the admin role. For local dev the demo runs with `DEFAULT_ROLE=admin` (keyless callers become admin) and `MODELS_REGISTRY_FILE` set. A real deployment should instead issue an **admin API key** (`api_key_roles`) rather than making keyless callers admin.
