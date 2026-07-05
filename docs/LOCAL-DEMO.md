# Local demo — run the UI + API on your machine with SciFact data

A tested recipe to bring up the **full stack locally** (Explore + Ops UI, hybrid
retrieval, catalog) seeded with the **SciFact** benchmark corpus. Two embedding
options: a fully self-contained local model, or fast remote GPUs over an SSH tunnel.

> Companion to [DEPLOYMENT.md](DEPLOYMENT.md) (which covers the general deploy paths).
> This file is the *demo* recipe. Verified on macOS/Apple-Silicon.

---

## Topology

Qdrant (vectors) + Elasticsearch (BM25) run in Docker; the **API runs on the host**
(a `python/.venv`) so it reads `localhost` URLs directly; the **frontend** is the Vite
dev server. Embeddings come from *either* a local BGE sidecar *or* remote SFR GPUs.

```
frontend :5173  →(proxy /v1)→  API :8000 (host venv)
                                 ├─ Qdrant :6333  (docker)
                                 ├─ Elasticsearch :9200  (docker)
                                 └─ embeddings:  BGE sidecar :50053 (docker)  OR  SFR via SSH tunnel :9001-9004
```

---

## 0. Prerequisites

```bash
# Docker running; Node 18+; a Python venv for the API
cd python && python3 -m venv .venv && .venv/bin/pip install -e ".[vector,text]" "uvicorn[standard]" && cd ..
make frontend-install          # npm install in frontend/
```

## 1. Infra (Qdrant + Elasticsearch)

```bash
docker compose -f deploy/docker-compose.local.yml up -d qdrant   # vectors
docker compose -f deploy/docker-compose.infra.yml up -d elasticsearch
```
(`docker-compose.local.yml` also defines a BGE sidecar + a containerized API — see step 3A.)

## 2. Get SciFact (~5,183 abstracts)

```bash
mkdir -p .localdata && cd .localdata
curl -sSL -o scifact.zip https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip
unzip -oq scifact.zip
# corpus.jsonl {_id,title,text} → ingester JSONL {text,path,metadata}
python3 - <<'PY'
import json
with open('scifact/corpus.jsonl') as f, open('scifact_ingest.jsonl','w') as out:
    for line in f:
        r = json.loads(line); t = (r.get('title') or '').strip(); x = (r.get('text') or '').strip()
        if not x: continue
        out.write(json.dumps({'text': (t+'\n\n'+x) if t else x, 'path': 'scifact/'+str(r['_id']),
                              'metadata': {'doc_id': str(r['_id']), 'title': t, 'source': 'scifact'}})+'\n')
PY
cd ..
```

## 3. Embeddings — pick one

### 3A. Self-contained (local BGE, 768-d) — no network, slower on CPU

```bash
docker compose -f deploy/docker-compose.local.yml up -d --build embedding-sidecar
cd python && .venv/bin/python scripts/ingest_jsonl.py ../.localdata/scifact_ingest.jsonl \
  --tenant public --embedding-api sidecar --embedding-url http://localhost:50053 \
  --embedding-model BAAI/bge-base-en-v1.5 --chunk-method fixed --chunk-size 2000 \
  --chunk-token-counter estimate --concurrency 2 && cd ..
export MODEL=BAAI/bge-base-en-v1.5 DIM=768 EMBAPI=sidecar EMBURL=http://localhost:50053
```
> CPU embedding is slow and can time out under load; use `--concurrency 1 --batch-retries 3` if batches fail, or use 3B.

### 3B. Fast remote GPUs (SFR-Mistral, 4096-d) — via SSH tunnel

```bash
# On your machine: tunnel 4 endpoints (adjust host/ports to yours)
ssh -N -L 9001:localhost:9001 -L 9002:localhost:9002 \
       -L 9003:localhost:9003 -L 9004:localhost:9004 USER@your-gpu-host
# verify: curl http://localhost:9001/v1/models

cd python && .venv/bin/python scripts/ingest_jsonl.py ../.localdata/scifact_ingest.jsonl \
  --tenant public --embedding-api openai \
  --embedding-url http://localhost:9001 http://localhost:9002 http://localhost:9003 http://localhost:9004 \
  --embedding-model Salesforce/SFR-Embedding-Mistral --chunk-method fixed --chunk-size 2000 \
  --chunk-token-counter estimate --concurrency 4 --batch-retries 3 && cd ..
# note the printed collection name, e.g. ragstack_salesforce_sfr_embedding_mistral_4096_<hash>
export MODEL=Salesforce/SFR-Embedding-Mistral DIM=4096 EMBAPI=openai \
       EMBURL='http://localhost:9001,http://localhost:9002,http://localhost:9003,http://localhost:9004'
```
The 4-way fan-out embeds the whole corpus in well under a minute. Add a Bearer token
with `--embedding-api-key <key>` / `OPENAI_API_KEY=<key>` if your endpoint needs one.

## 4. Populate BM25 without re-embedding

The ingest above wrote vectors to Qdrant. BM25 only needs the text, so backfill ES from
Qdrant (no second embedding pass). Use the collection name the ingester printed:

```bash
cd python && .venv/bin/python scripts/backfill_es_from_qdrant.py \
  --collection ragstack_salesforce_sfr_embedding_mistral_4096_<hash> && cd ..
```

## 5. Run the API (host) + frontend

```bash
# API — inline env so it ignores the root .env (Go/compose keys the Python Settings rejects)
cd python && \
QDRANT_COLLECTION_EXPLICIT=<the collection from step 3> \
VECTOR_BACKEND=qdrant QDRANT_URL=http://localhost:6333 \
TEXT_BACKEND=elasticsearch ELASTICSEARCH_URL=http://localhost:9200 \
GRAPH_BACKEND=memory JOB_STORE_BACKEND=memory \
EMBEDDING_API=$EMBAPI EMBEDDING_ENDPOINTS=$EMBURL \
EMBEDDING_MODEL=$MODEL EMBEDDING_MODEL_DIM=$DIM \
REQUIRE_DURABLE_BACKENDS=false DEFAULT_ROLE=admin \
.venv/bin/uvicorn ragstack.api.main:app --host 0.0.0.0 --port 8000 &
cd ..
make frontend-dev     # Vite on :5173, proxies /v1 + /health → :8000
```

`DEFAULT_ROLE=admin` lets the keyless UI reach the admin-gated `/v1/health/deep`.

## 6. Use it

- **UI:** http://localhost:5173 — **Explore** tab (ask the corpus) and **Ops** tab (store counts + deep health).
- **API docs:** http://localhost:8000/docs
- Try: `folic acid and vitamin B12 in chronic kidney disease`, `antibiotic resistance in bacteria`.

## 7. Stop / clean up

```bash
pkill -f "uvicorn ragstack.api.main"; pkill -f vite
docker compose -f deploy/docker-compose.local.yml down
docker compose -f deploy/docker-compose.infra.yml down elasticsearch
# scratch data lives in .localdata/ (gitignored)
```

---

## Notes & gotchas

- **Collection name = `f(model, dim)`**, so BGE (768) and SFR (4096) are *different* Qdrant collections. Pin the serving API to one via `QDRANT_COLLECTION_EXPLICIT`; the ES index follows it automatically.
- **Run the API from `python/`, not the repo root** — the root `.env` carries Go/sidecar keys the Python `Settings` rejects (`extra_forbidden`). Inline env vars (as above) are CWD-independent.
- **SFR needs the tunnel up for live queries too** — query embedding goes through it, not just ingest.
- **ES version:** pin the client to match the server (`elasticsearch>=8.13,<9` for ES 8.13); a v9 client rejects an 8.x server on the media-type header.
- **No LLM wired** → `/v1/query` returns ranked sources with a "generation unavailable" note (fine for a retrieval demo); set `LLM_ENDPOINT` to add generated answers.
