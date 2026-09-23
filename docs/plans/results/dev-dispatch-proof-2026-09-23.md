# Dev dispatch proof — API → GoWe → `ragstack-dev` worker → dev Qdrant (2026-09-23)

**Verdict: proven for the default build spec. Not yet proven for a semantic method, because the API's build-spec override is admin-only and the submitting principal was not admin.**

## What ran

| | |
|---|---|
| tenant / tag | `dev`, **v1.6.4** (`ffd04cb`), `INGEST_BACKEND=gowe`, `GOWE_WORKER_GROUP=ragstack-dev`, `INGEST_WORKER_UNSUPPORTED_METHODS=` (empty: semantic admitted), `ALLOW_USER_COLLECTION_CREATE=true` |
| principal | the owner's BV-BRC token (non-admin on dev), via `http://localhost:9000/ragstack/dev/api` |
| workers | `ragstack-dev-1` / `-2` (pids 552677/552678), `--image-dir /scout/containers/ragstack-dev`, image `ragstack-worker.sif -> ragstack-worker-v1.6.3-1-ga2be96f.sif` (the symlink-to-versioned layout of #614) |
| input | `contracts/fixtures/documents/sample_small.pdf` (908 B) |

```
08:44:13Z  POST /v1/collections {"id":"dispatch-proof"}            -> 201
           physical: ragstack_lib_dispatch_proof_salesforce_sfr_embedding_4096_fixed_512_64_220ff9ba
           chunk_method=fixed size=512 overlap=64  (server default; spec_hash c0ba7587)   points_count 0
08:44:14Z  POST /v1/ingest/upload collection=dispatch-proof         -> 202  job_id 9f9cfc5b-2db5-4627-ad08-eea0405c36ce
08:44:17Z  worker-ragstack-dev-1: task received step=extract   executing in Apptainer image=ragstack-worker.sif
08:44:21Z  worker-ragstack-dev-1: task received step=ingest
08:44:29Z  dev Qdrant :24041  points_count 0 -> 1
08:44:30Z  worker-ragstack-dev-1: task received step=pack
```

**The task is tied to the API's job, not just adjacent in time** — three joins, strongest first:

1. **The dev API log records the hand-off** (`/rag/data/tenants/dev/logs/api-dev.log`, grep by the *submission* id, not the job id):
   `08:44:15.062Z rid=12b812bc13c34aea route="POST /v1/ingest/upload" msg="gowe: submitted sub_c4b49e40-bdfc-4658-ad4f-8e942ec32cd8 (1 item(s)) as workflow wf_518c2e23-d988-48db-ac43-2279869ee1fe"`.
2. **The worker's own command lines** (`worker-ragstack-dev-1.log`, the `executing in Apptainer` lines): ingest carries
   `--collection ragstack_lib_dispatch_proof_… --collection-id dispatch-proof --chunk-method fixed --chunk-size 512 --chunk-overlap 64 --qdrant-url http://localhost:24041 --es-url http://localhost:24043 --embedding-url :9001 :9002`; pack carries `--job-id 9f9cfc5b-… --spec-hash c0ba7587`.
3. **The pack step's outputs** in `/scout/wf/gowe/workdir/ragstack-dev-1/task_cc7850d3-801f-4326-bca7-9d5fe565ae60/1/`: `manifest.json` (collection_id, job_id, spec_hash) and `receipt.json` (the staged source path under `sub_c4b49e40…`).

GoWe's own record of the submission (`GET :8091/api/v1/submissions/sub_c4b49e40…`, 08:53Z, author-observed with a token; the reviewer could not reproduce without one): workflow `ragstack-bulk-ingest`, state **`COMPLETED`**, inputs
`job_id=9f9cfc5b…`, `collection_id=dispatch-proof`, `chunk_method=fixed`, `qdrant_url=http://localhost:24041`,
`es_url=http://localhost:24043`, `embedding_url=[:9001, :9002]`, `spec_hash=c0ba7587` — the dev stores, the dev spec.

Each DAG step is dispatched only after its predecessor succeeds, and the point landed between `ingest` and `pack` — so the chain **API submit → GoWe scheduling by `worker_group` label → `ragstack-dev` worker → tool image → dev Qdrant** is exercised end to end on dev, for the first time through the API (earlier dev runs of the semantic tool went through `ingest_shard.py` directly and never touched the registry — which is why `sem-e2e` was "unknown collection" to the API).

## What is NOT proven, and why

**Semantic through the API.** `POST /v1/collections {"chunk":{"method":"semantic_pooled"}}` → **403**
`build-spec overrides ('embedding', 'chunk') are admin-only; omit both fields to create a collection from the server-default build spec`.
A non-admin can only create default-spec (fixed/512) collections. Proving `semantic_pooled` through the API therefore needs either an admin key on dev for that one `POST`, or a tenant whose *default* spec is semantic. The worker side of semantic is already proven on dev (`docs/plans/results/semantic-vs-pooled-2026-09-18.md`); what remains unexercised is only the registry entry + `_gowe_inputs` carrying the semantic method.

## Observations (not failures)

- **`stage-out failed … workspace stager: no authentication`** (2/2/4 WARN lines for extract/ingest/pack, 8 per submission) — pre-existing on three of the four `ragstack-hackathon` workers (earliest 2026-09-17 08:56 -05:00 on hackathon-2; -3 and -4 later that day; hackathon-1 has run no task since the flag was added), and **by design, not a misconfiguration** (traced by the GoWe session): `--workspace-stager` is required on these groups (GoWe #267/#268); the worker makes an eager per-file `ws://` upload attempt with the step's `OutputDestination`, and plain `worker`-executor steps (extract/ingest/pack) never receive a per-task credential, so that attempt fails every time and falls back to `file://`; the real delivery happens server-side afterwards with the submission's own token. Confirmed on this submission: `output_destination=ws:///awilke@bvbrc/home/.ragstack/collections/dispatch-proof/versions/`, **`output_state=delivered`**. No token to add anywhere. Cosmetic fix tracked as **GoWe #272** (skip or downgrade the attempt when no credential exists).
- **I polled the wrong path for 2 minutes.** The 24 polls went to `GET /v1/ingest/jobs/{id}` (does not exist → 404) and one to `GET /v1/jobs/{id}` (does not exist either; `/v1/jobs` is the admin-only list, #85/#100). The per-id read is **`GET /v1/ingest/{job_id}`** (`contracts/openapi.yaml:550`, `api/routers/documents.py:1555`, tenant-scoped since #130) — and as the submitter it answered `{"status":"completed","items":{"total":1,"completed":1,...},"collection":"dispatch-proof"}`; a fake id answers 200 `unknown` by design (no IDOR). The job row is created for every backend (`job_lifecycle` at `api/routers/documents.py:1017`, before the GoWe dispatch at `:1027`). **#628** was filed on the wrong premise and has been re-scoped to what is real: the Go stub answers `not_found` where the contract says `unknown`; no conformance test covers the cross-tenant job read; the 202 body does not name the poll path (which is how this happened).
- `GET /v1/collections/{id}` → 405 (not in the contract; the listing is the read path). Not a bug, noted so nobody re-discovers it.

## State left behind

Collection `dispatch-proof` on dev (1 point). Delete when no longer useful:
`DELETE /v1/collections/dispatch-proof` as its creator, or leave as the smoke fixture for the next tag bump.
