# Cookbook — new-org API server + GoWe ingest of 40,000 documents

Scope: stand up an API server for a **new organization** and bulk-ingest ~40k docs
with the GoWe workflow. Grounded in the code on `main` (2026-07-07). Placeholders are
`UPPERCASE`. "Verified" = read from source; "operator-supplied" = you fill in.

---

## 0. First decide: how to isolate the org  (tenant vs. collection)

These are **orthogonal** in RAGStack — use both, for different jobs:

| Mechanism | What it is | Isolation? |
|---|---|---|
| **Tenant** (`tenant_id`, from the API key) | Shared Qdrant collection + ES index, filtered by `tenant_id` on **every** read/write/delete, enforced **server-side** from the key (`security.py` → `scope_filters`). Point ids are `uuid5("{tenant}:{chunk_id}")` so tenants never overwrite each other. | **Yes — the real boundary.** A caller only ever sees its own tenant + `public`. |
| **Collection** (`collection` id) | A **physically separate** Qdrant collection + ES index (own embedding model/dim/chunk). Selected per-request via `collection`. | **No access control.** `_resolve_entry` does *not* check tenant→collection; `GET /v1/collections` lists every id to any authenticated caller. Physical/routing separation only. |

**Recommendation:** give the org **its own tenant** (the enforced boundary) **and** its own
collection (clean physical separation + independent lifecycle: own embedding model, own
re-index/delete, own stats). Do **not** rely on the collection prefix alone as security —
it is not enforced.

Convention used below (pick your own): `ORG_ID=acme` → `TENANT=acme`,
`COLLECTION=acme_sfr_tok256`, one API key mapped to that tenant.

> Per-tenant *collection restriction* is not in the codebase — if you need "tenant X may
> only query collection Y" enforced, that's a small follow-up PR (`api_key_allowed_collections`
> + a check in `_resolve_entry`), not available today.

---

## 1. Prerequisites (host: coconut)

```bash
. /rag/bin/activate      # ragstack conda env (/rag/envs/ragstack) + HF_HOME=/rag/cache + endpoints
```

Bring up infra + an embedding backend (skip whatever is already running):

```bash
cd /rag/repos/ragstack
make infra-up-apptainer          # Qdrant :6333, Elasticsearch :9200, Neo4j :7687, Postgres, Redis
make sidecars-up-apptainer       # BGE cross-encoder/embedding sidecar :50053 (CPU)
```

**Embedding choice for 40k docs:** prefer the **GPU vLLM fleet** (`:9001–:9008`,
`Salesforce/SFR-Embedding-Mistral`, dim 4096) over the CPU BGE sidecar — ingest is
embed-bound. Confirm at least one endpoint answers and serves the expected model:

```bash
curl -s localhost:9001/v1/models | grep -o 'SFR-Embedding-Mistral' && echo "fleet up"
```
If nothing answers, start a pooling vLLM instance per the `--runner pooling` recipe in
MEMORY.md (one per GPU, ports `:9001..:9008`) before continuing — nothing downstream
works without it.

Key facts (verified):
- Python env: `/rag/envs/ragstack` (3.12). Run pytest/uvicorn with this interpreter.
- `HF_HOME=/rag/cache` (shared tokenizer cache — required for `fixed_token` chunking).
- GoWe engine: `http://localhost:8091`, BV-BRC token auth (anonymous disabled).

---

## 2. Start a NEW API server for the org

Create an env file. If another API is already on `:8000`, pick a free port.

> **JSON values must be single-quoted** (`API_KEYS='[...]'`). They are sourced by the
> shell below, and unquoted `[`/`{`/`"` get mangled — invalid JSON fails pydantic at
> startup. Generate real keys, e.g. `openssl rand -hex 32`.

```bash
cat > /rag/config/acme.env <<'EOF'
# --- identity / isolation ---
# Two keys: a researcher key for the org, an admin key for ops (deep health, config).
API_KEYS='["ACME_API_KEY","ACME_ADMIN_KEY"]'
API_KEY_TENANTS='{"ACME_API_KEY":"acme","ACME_ADMIN_KEY":"acme"}'   # key -> tenant (the enforced boundary)
API_KEY_ROLES='{"ACME_API_KEY":"researcher","ACME_ADMIN_KEY":"admin"}'  # researcher = read/query; admin = ops
DEFAULT_ROLE=researcher

# --- stores ---
VECTOR_BACKEND=qdrant
QDRANT_URL=http://localhost:6333
TEXT_BACKEND=elasticsearch
ELASTICSEARCH_URL=http://localhost:9200
GRAPH_BACKEND=disabled                           # enable neo4j only if you want the KG leg
JOB_STORE_BACKEND=postgres
POSTGRES_DSN=postgresql+asyncpg://ragstack:ragstack@localhost/ragstack
REQUIRE_DURABLE_BACKENDS=true                    # fail fast if a store is down (prod)

# --- embedding (must MATCH what you ingest with; content-addressed) ---
EMBEDDING_API=openai                             # vLLM speaks the OpenAI embeddings API
EMBEDDING_ENDPOINTS='["http://localhost:9001","http://localhost:9002","http://localhost:9003","http://localhost:9004"]'
EMBEDDING_MODEL=Salesforce/SFR-Embedding-Mistral
EMBEDDING_MODEL_DIM=4096

# --- the org collection registry (so /v1/query can select it) ---
COLLECTIONS_FILE=/rag/config/acme.collections.json

# --- Phase-1/2 model registry (optional; enables hot-swap + per-request llm/reranker) ---
MODELS_REGISTRY_FILE=/rag/config/acme.models.json
MODEL_URL_ALLOWLIST=http://localhost                # widen to real backends explicitly
EOF
```

Register the org collection so queries can address it (the `collection` field must
match the physical name you ingest into, and the embedding model/dim must match):

```bash
cat > /rag/config/acme.collections.json <<'EOF'
[
  {
    "id": "acme",
    "label": "ACME · SFR / fixed_token 256",
    "collection": "acme_sfr_tok256",
    "text_index": "acme_sfr_tok256",
    "embedding_api": "openai",
    "embedding_model": "Salesforce/SFR-Embedding-Mistral",
    "embedding_model_dim": 4096,
    "embedding_endpoints": ["http://localhost:9001","http://localhost:9002"],
    "chunk_method": "fixed_token",
    "chunk_size": 256
  }
]
EOF
```

> `chunk_method`/`chunk_size` here are **display labels only** (`CollectionSpec` has no
> `chunk_overlap` field — it would be silently dropped). The *actual* chunking is set by
> the ingest job (§4.2 / §5), which is what must match; keep the two in sync by hand.

Launch (own port so it can coexist with an existing server). **Source** the env file
(`set -a` exports every assignment) — do not pipe it through `xargs`, which strips the
JSON quotes:

```bash
cd /rag/repos/ragstack/python
set -a; . /rag/config/acme.env; set +a
/rag/envs/ragstack/bin/uvicorn ragstack.api.main:app --host 0.0.0.0 --port 8010
```

Verify (note `/v1/config` and `/v1/health/deep` are **admin-only** — all-or-nothing, so
use the admin key; the researcher key gets 403 on both):
```bash
curl -s localhost:8010/health                                          # {"status":"ok"}  (no key)
curl -s -H "X-API-Key: ACME_ADMIN_KEY" localhost:8010/v1/config         # effective config (admin)
curl -s -H "X-API-Key: ACME_ADMIN_KEY" localhost:8010/v1/health/deep    # per-store probes (admin)
```

> Qdrant collection + ES index are **auto-created on first write** (dim-validated). No
> pre-creation step needed; a dim mismatch is fatal, not silent.

---

## 3. Prepare the corpus (JSONL, one doc per line)

Required shape (verified — `ingest_jsonl.py` / `ingest_shard.py`):
```jsonl
{"text": "full document text …", "path": "source/acme/doc001.pdf", "metadata": {"title": "…", "year": 2025}}
```

Shard for GoWe. Files must live under the engine's upload/download dir (`/scout/wf/data`).
Don't over-shard — GoWe costs ~1–3 s/shard fixed; ~1k docs/shard (≈40 shards for 40k) is a
good balance:

```bash
mkdir -p /scout/wf/data/acme/shards
split -l 1000 -d --additional-suffix=.jsonl \
      /path/to/acme_40k.jsonl /scout/wf/data/acme/shards/acme.s
# → acme.s00.jsonl … acme.s39.jsonl
```

---

## 4. Ingest via the GoWe workflow  ← the requested path

### 4.0 Execution topology & the two scaling knobs

The CWL workflow (`ingest-bulk.cwl`) is pure orchestration; the actual embedding is a
remote HTTP call. So the worker doing `ingest_shard` does **not** need a GPU — it
chunks, then POSTs batches to a vLLM embedding instance and upserts to Qdrant/ES. Two
independent ways to scale 40k docs (both supported today, pick either/both):

```
                 scatter (N shards)
  GoWe engine ─────────────────────────▶  ragstack-cpu workers  ──HTTP──▶  vLLM fleet
  :8091                                    (--runtime none, in the         :9001..:9008
                                            ragstack env; CPU orchestration) (GPU embedding)
                                                    │
                                                    └── upsert ──▶ Qdrant :6333 / ES :9200
```

- **Knob 1 — more CPU workers** in the `ragstack-cpu` group → more shards embed
  concurrently (the engine scatters across them). Each worker is cheap (no GPU).
- **Knob 2 — more vLLM endpoints** listed in `embedding_url` → each worker fans out its
  batches across the fleet (`embedding-max-concurrency`). Add SFR instances on
  `:9001..:9008` and list them all.

You could alternatively run **GPU workers** that embed locally, but with the vLLM-fleet
topology that's unnecessary — CPU workers + a reachable fleet is the validated path
(ADR-0001: ML stays behind HTTP). Rule of thumb: size **worker count × endpoint count**
to your GPU budget; don't over-shard (GoWe's ~1–3 s/shard fixed cost favours ~1k-doc shards).

### 4.1 Make sure a ragstack worker is online

Ingest needs ragstack's deps, so it runs on a dedicated **`--runtime none` worker in the
ragstack env**, group `ragstack-cpu` (one, `ragstack-cpu-1`, is already deployed). To scale
40k-doc throughput, start several — scatter runs in parallel across workers in the group:

The `gowe-worker` binary is **not** in the ragstack env — it lives in the GoWe install
(`/scout/Experiments/GoWe/bin/gowe-worker`). Put the ragstack env **first** on PATH so the
`--runtime none` worker executes `ingest_shard` with ragstack's deps:

```bash
# repeat with --name ragstack-cpu-2..N to parallelize the scatter
PATH="/rag/envs/ragstack/bin:$PATH" HF_HOME=/rag/cache \
  /scout/Experiments/GoWe/bin/gowe-worker \
    --server http://localhost:8091 --runtime none \
    --name ragstack-cpu-2 --group ragstack-cpu \
    --workdir /scout/wf/data/ragstack-workdir --stage-out file:///scout/wf/data
```

> `ragstack-cpu-1` is already deployed, so a baseline submit lands on it without starting
> anything. But one worker = **serial** scatter — for 40k docs start several (`-2..N`) so
> the engine fans shards across them.

### 4.2 Job inputs (tenant + collection are inputs to the CWL)

```bash
cat > /scout/wf/data/acme/ingest.inputs.yml <<EOF
shards:
$(for f in /scout/wf/data/acme/shards/acme.s*.jsonl; do echo "  - {class: File, path: $f}"; done)
collection: acme_sfr_tok256          # → org collection (physical separation)
tenant: acme                         # → the enforced isolation boundary
chunk_method: fixed_token
chunk_size: 256
chunk_overlap: 32
embedding_model: Salesforce/SFR-Embedding-Mistral
embedding_url:
  - http://localhost:9001
  - http://localhost:9002
  - http://localhost:9003
  - http://localhost:9004
# embedding_api_key: TOKEN          # only for keyed endpoints; drop for keyless
EOF
```

### 4.3 Submit to the live GoWe engine

GoWe auth is a **BV-BRC token** (anonymous is disabled). Mint one with the BV-BRC CLI —
`p3-login <bvbrc-username>` — which writes `~/.patric_token`; `GoWeClient` auto-loads that
(or `~/.gowe/credentials.json`, or `$GOWE_TOKEN`). You need a BV-BRC account; if you don't
have one, use the direct-CLI path in §5, which needs no GoWe token. To set it explicitly:
```bash
export GOWE_TOKEN='<BV-BRC token>'      # or rely on ~/.patric_token from p3-login
```

Driver (verified against `ragstack.ingestion.gowe_client.GoWeClient`: register → submit →
wait → download). The `worker_group` label pins the run to `ragstack-cpu`:

```bash
/rag/envs/ragstack/bin/python - <<'PY'
import asyncio, yaml
from pathlib import Path
from ragstack.ingestion.gowe_client import GoWeClient

CWL   = Path("/rag/repos/ragstack/cwl/ingest-bulk.cwl").read_text()
INPUTS= yaml.safe_load(Path("/scout/wf/data/acme/ingest.inputs.yml").read_text())

async def main():
    c = GoWeClient("http://localhost:8091")           # token from $GOWE_TOKEN
    try:
        wf  = await c.register_workflow("acme-bulk-ingest", CWL)
        sub = await c.submit(wf, INPUTS, labels={"worker_group": "ragstack-cpu"})
        print("submitted", sub["id"])
        done = await c.wait(sub["id"], poll_interval=5.0, timeout=14400)  # up to 4h
        print("state:", done["state"])
        # Pull the merge_receipts summary to see per-shard outcomes / failed ids.
        summary = done.get("outputs", {}).get("summary")
        if summary:
            print(await c.download(summary["location"]))   # {"ingested": …, "failed_shards": [...]}
    finally:
        await c.close()

asyncio.run(main())
PY
```

> The output key name (`summary`) matches `ingest-bulk.cwl`'s `outputs:`; if your engine
> nests outputs differently, print `done` once to see the shape. A non-empty
> `failed_shards` means re-submit those shards — idempotent upsert makes that safe.

Notes (verified):
- Idempotent: deterministic `uuid5` ids + upsert-only → a retried/re-run shard overwrites in
  place, no duplication. GoWe owns scatter/retry/resume.
- The gather step (`merge_receipts`) surfaces **failed shard ids** — check the summary; a
  partial failure is reported, not silently under-ingested.
- `/v1/ingest` does **not** drive GoWe (returns 501 for `ingest_backend!=local`) — it expects
  pre-sharded files; that transparent-sharding bridge is a known follow-up. Submit the
  workflow directly, as above.
- Throughput is embed-bound (~5–8 docs/s on one worker+endpoint; scales with workers ×
  fleet endpoints). 40k docs → plan for tens of minutes to a few hours depending on fleet
  size and worker count.

---

## 5. (Alternative) direct CLI ingest — simplest single-node baseline

If you don't need cluster scatter, `ingest_jsonl.py` does the whole run in-process with
built-in checkpoint/resume — good for a first load or a rerun. Same tenant/collection knobs:

```bash
cd /rag/repos/ragstack/python
/rag/envs/ragstack/bin/python scripts/ingest_jsonl.py /path/to/acme_40k.jsonl \
  --tenant acme \
  --collection acme_sfr_tok256 \
  --embedding-api openai \
  --embedding-url http://localhost:9001 http://localhost:9002 http://localhost:9003 http://localhost:9004 \
  --embedding-model Salesforce/SFR-Embedding-Mistral \
  --chunk-method fixed_token --chunk-size 256 --chunk-overlap 32 \
  --text-backend elasticsearch --es-url http://localhost:9200 --es-index acme_sfr_tok256 \
  --batch-size 128 --concurrency 4 --embedding-max-concurrency 8 \
  --batch-retries 3 \
  --resume --checkpoint /rag/cache/acme_40k.ckpt
```
Re-run with the same `--checkpoint` to resume after an interruption (out-of-order
`done_ranges` tracked, so completed shards aren't re-embedded).

---

## 6. Verify + query as the org

```bash
# collection shows up with tenant-scoped counts
curl -s -H "X-API-Key: ACME_API_KEY" localhost:8010/v1/collections

# query — automatically scoped to tenant "acme" + public (server-side, from the key)
curl -s -X POST localhost:8010/v1/query \
  -H "X-API-Key: ACME_API_KEY" -H "Content-Type: application/json" \
  -d '{"query":"…","collection":"acme","top_k":5}'
```
A different org's key can never read `acme` data (tenant filter enforced at Qdrant + ES).

---

## 7. Gotchas (verified)

- **Embedding model/dim must match** between ingest and the registered collection —
  collections are content-addressed on (model, dim, chunk); a mismatch either builds a
  different collection or fails the dim check.
- **`fixed_token` needs the real HF tokenizer** — keep `HF_HOME=/rag/cache` shared, or every
  GoWe task re-downloads it (`--chunk-token-counter estimate` is force-reverted to `hf`).
- **GoWe worker routing:** the `ragstack-cpu` label can never be claimed by a `default`
  worker, so ingest won't silently land on a stock container and `ModuleNotFound`.
- **Neo4j** (only if you enable the graph leg): password must not be the default `neo4j`
  (Neo4j 5 rejects it) — set `NEO4J_PASSWORD=ragstack`.
- **Graph triples are stamped with their collection** (#209). Unlike Qdrant/ES, one Neo4j
  holds every collection's triples, so the boundary lives in the data: a query on `acme`
  never fuses graph context derived from another collection, and a re-ingest into `acme`
  never deletes another collection's triples for the same `doc_id`. Triples written
  *before* that change carry no stamp and are invisible to any collection-scoped read —
  re-ingest to re-derive them (the KG is small and derived; nothing else is affected).
  `ensure_schema` at startup drops the old `entity_name_tenant` constraint and creates
  `entity_name_tenant_collection`; both statements are idempotent.
- **Single ragstack worker = serial scatter.** Start more `ragstack-cpu-N` workers to
  actually parallelize 40k docs across the fleet.
