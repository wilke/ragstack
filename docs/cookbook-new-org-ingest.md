# Cookbook — new-org API server + GoWe ingest of 40,000 documents

Scope: stand up an API server for a **new organization** and bulk-ingest ~40k docs
with the GoWe workflow. Grounded in the code on `main` (2026-07-07). Placeholders are
`UPPERCASE`. "Verified" = read from source; "operator-supplied" = you fill in.

---

## 0. First decide: how to isolate the org  (tenant vs. collection)

These are **orthogonal** in RAGStack — use both, for different jobs:

| Mechanism | What it is | Isolation? |
|---|---|---|
| **Tenant** (`tenant_id`, from the API key) | Shared Qdrant collection + ES index, filtered by `tenant_id` on **every** read/write/delete, enforced **server-side** from the key (`security.py` → `scope_filters`). Point ids are `uuid5("{tenant}:{chunk_id}")` so tenants never overwrite each other. *Amended by [ADR-0005](adr/0005-tenant-anatomy.md): a tenant is now a **dedicated instance set** (own Qdrant, own ES, own ACL/registry state) — the shared-store filtering above still applies within an instance, but org isolation gets its own stores, provisioned by `new-tenant.sh` (§2).* | **Yes — the real boundary.** A caller only ever sees its own tenant + `public`. |
| **Collection** (`collection` id) | A **physically separate** Qdrant collection + ES index (own embedding model/dim/chunk). Selected per-request via `collection`. | **Yes — owner/grant enforced** (ADR-0003, #243): reads pass through `resolve_access` at collection resolution (owner, share, or `public` grant); writes and delete are owner-or-admin; `GET /v1/collections` lists only what the caller can read. Pre-existing collections were backfilled `owner=legacy:admin` + public-read, so they behave as before until un-published. |

**Recommendation:** give the org **its own tenant** (per [ADR-0005](adr/0005-tenant-anatomy.md)
a full instance set for hard isolation) **and** its own collection (clean physical separation +
independent lifecycle: own embedding model, own re-index/delete, own stats). Collection
ownership is now enforced, but the tenant remains the hard boundary.

Convention used below (pick your own): `ORG_ID=acme` → `TENANT=acme`,
`COLLECTION=acme_sfr_tok256`, one API key mapped to that tenant.

> Per-tenant *collection confinement* exists as `TENANT_COLLECTIONS` (#187) — an operator
> env map confining a tenant to a set of collection ids — and since #243 it **intersects**
> with ownership/grants rather than replacing them: a caller needs to survive the allowlist
> *and* hold read access.

---

## 1. Prerequisites (host: coconut)

```bash
. /rag/bin/activate      # ragstack conda env (/rag/envs/ragstack) + HF_HOME=/rag/cache + endpoints
```

Bring up the shared pieces (skip whatever is already running). Per ADR-0005 the org
gets its **own** Qdrant + ES from the provisioning script in §2 — the shared infra
stack is only needed for the sidecars/dev stores, not for the new org's data:

```bash
cd /rag/repos/ragstack
make sidecars-up-apptainer       # BGE cross-encoder/embedding sidecar :50053 (CPU)
./apptainer/pull.sh              # ensure qdrant.sif / elasticsearch.sif exist (shared SIFs, reused per tenant)
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
- GoWe engine: `http://GOWE_HOST`, BV-BRC token auth (anonymous disabled).

---

## 2. Provision the tenant — script, not hand-built steps

Per [ADR-0005](adr/0005-tenant-anatomy.md) decision 4, a tenant is created by
**`apptainer/new-tenant.sh`** — it stamps out everything the hand-rolled version of
this section used to build (env file, dedicated stores, ports), deterministically and
idempotently. Hand-built tenants are how the shared-ES isolation gap happened.

> Provisioning a tenant to take on *new users* because an existing tenant is
> approaching its collection bound — rather than for a new org's own isolated
> deployment — is a distinct procedure with its own trigger, settings and
> routing-map update: see
> [tenant-scale-out.md](runbooks/tenant-scale-out.md).

```bash
cd /rag/repos/ragstack

# preview the complete plan first (dirs, ports, files, commands) — touches nothing
./apptainer/new-tenant.sh acme --dry-run

# provision (default: sqlite ACL/registry/job stores under the tenant dir)
/rag/bin/rag new-tenant-apptainer NAME=acme
# or, for a per-tenant DATABASE in the existing Postgres server (ADR-0004 amendment):
./apptainer/new-tenant.sh acme --postgres postgresql://ragstack:PW@localhost:5432/postgres

# start the org's dedicated Qdrant + Elasticsearch (instances qdrant-acme, elasticsearch-acme)
$RAG_DATA/tenants/acme/bin/up.sh          # stop later with .../bin/down.sh
```

The script allocates a stable port block (recorded in `$RAG_DATA/tenants/manifest.tsv`;
re-runs reuse it verbatim), creates every writable dir under `$RAG_DATA/tenants/acme/`,
and stamps `$RAG_DATA/tenants/acme/config/tenant.env` with generated API keys, the
role maps (`user`/`admin` — `researcher` is a deprecated alias), the per-tenant store
URLs, the shared embedding fleet endpoints, `REQUIRE_DURABLE_BACKENDS=true` and an
`INGEST_ROOT` confined to the tenant dir. The keys are in the env file — hand the
user key to the org, keep the admin key for ops.

Register the org collection so queries can address it (the `collection` field must
match the physical name you ingest into, and the embedding model/dim must match).
Write the registry file into the tenant dir and reference it from `tenant.env`
(uncomment the stamped `# COLLECTIONS_FILE=...` line — operator edits are kept on
re-runs, without `--force`):

```bash
cat > $RAG_DATA/tenants/acme/config/collections.json <<'EOF'
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

Launch the API on the allocated port (`PORT` is already in the env file — the plan and
`tenant.env` show it; first tenant gets `:24000`). **Source** the env file (`set -a`
exports every assignment) — do not pipe it through `xargs`, which strips the JSON quotes:

```bash
cd /rag/repos/ragstack/python
set -a; . $RAG_DATA/tenants/acme/config/tenant.env; set +a
/rag/envs/ragstack/bin/uvicorn ragstack.api.main:app --host 0.0.0.0 --port $PORT
```

Verify (note `/v1/config` and `/v1/health/deep` are **admin-only** — all-or-nothing, so
use the admin key; the user key gets 403 on both):
```bash
curl -s localhost:$PORT/health                                          # {"status":"ok"}  (no key)
curl -s -H "X-API-Key: ACME_ADMIN_KEY" localhost:$PORT/v1/config         # effective config (admin)
curl -s -H "X-API-Key: ACME_ADMIN_KEY" localhost:$PORT/v1/health/deep    # per-store probes (admin)
```

> Qdrant collection + ES index are **auto-created on first write** (dim-validated) in
> the **tenant's own instances** — `QDRANT_URL`/`ELASTICSEARCH_URL` in `tenant.env`
> already point at them. No pre-creation step needed; a dim mismatch is fatal, not silent.

> The ops architecture reference artifact still shows the pre-ADR shared ES; it is
> updated when the existing tenants migrate (#246), not now.

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
  (engine)                                 (--runtime none, in the         :9001..:9008
                                            ragstack env; CPU orchestration) (GPU embedding)
                                                    │
                                                    └── upsert ──▶ the TENANT'S Qdrant/ES
                                                        (ports from the tenant's plan, §2 —
                                                         NOT the shared :6333/:9200; see §4.2)
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
    --server http://GOWE_HOST --runtime none \
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
# Store targets: REQUIRED, no defaults (#407/#454). These are the ORG's own
# instances from its provisioning plan — never :6333/:9200, which are production.
qdrant_url: http://localhost:<acme qdrant port from the plan>
es_url: http://localhost:<acme es port from the plan>
EOF
```

> **Per-tenant stores (ADR-0005):** `ingest-bulk.cwl` takes `qdrant_url` and `es_url`
> as **required** workflow inputs with no defaults (#454) — name the org's own
> instances from its provisioning plan, or the run is refused at submission. It used
> to declare neither, so every scattered worker fell through to `ingest_shard.py`'s
> `:6333`/`:9200` and wrote to the *shared production* stack rather than the tenant's
> own. If you are following an older copy of this page that told you to add them to
> the step's `arguments`, you no longer need to — and if you did, check where that
> run wrote.

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
    c = GoWeClient("http://GOWE_HOST")           # token from $GOWE_TOKEN
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
built-in checkpoint/resume — good for a first load or a rerun. Same tenant/collection
knobs; `--qdrant-url`/`--es-url` must point at the **tenant's own instances** (the ports
below are the first tenant's block from §2 — read yours from the plan / `tenant.env`):

```bash
cd /rag/repos/ragstack/python
/rag/envs/ragstack/bin/python scripts/ingest_jsonl.py /path/to/acme_40k.jsonl \
  --tenant acme \
  --collection acme_sfr_tok256 \
  --embedding-api openai \
  --embedding-url http://localhost:9001 http://localhost:9002 http://localhost:9003 http://localhost:9004 \
  --embedding-model Salesforce/SFR-Embedding-Mistral \
  --chunk-method fixed_token --chunk-size 256 --chunk-overlap 32 \
  --qdrant-url http://localhost:24001 \
  --text-backend elasticsearch --es-url http://localhost:24003 --es-index acme_sfr_tok256 \
  --batch-size 128 --concurrency 4 --embedding-max-concurrency 8 \
  --batch-retries 3 \
  --resume --checkpoint /rag/cache/acme_40k.ckpt
```
Re-run with the same `--checkpoint` to resume after an interruption (out-of-order
`done_ranges` tracked, so completed shards aren't re-embedded).

---

## 6. Verify + query as the org

Use the API port from §2's plan (`$PORT` in `tenant.env`; first tenant gets `:24000`):

```bash
# collection shows up with tenant-scoped counts
curl -s -H "X-API-Key: ACME_API_KEY" localhost:$PORT/v1/collections

# query — automatically scoped to tenant "acme" + public (server-side, from the key)
curl -s -X POST localhost:$PORT/v1/query \
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
