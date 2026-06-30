# RAGStack — Session Handoff

**Updated:** 2026-06-29 (late) · **Repo:** `/rag/repos/ragstack` (deployment checkout on host `coconut`)
**main @ `v0.11.0` (+4 commits, `9aaecbc`).** No open PRs.

> Cold-start read order: this file → repo `STATUS.md` → `MEMORY.md` → recent `scratchpad.md`.
> The repo's STATUS/MEMORY/scratchpad are authoritative + version-controlled; this file is the
> cross-session operating brief + todo (not version-controlled — edit freely).

---

## 1. Where things stand

Built through **v0.11.0**. Since the last handoff (v0.9.0) these merged to `main`:

| Capability | PR | Notes |
|---|---|---|
| Query rewriting (HyDE / multi-query) | #17 | v0.10.0 |
| **Cross-encoder reranker** (sidecar `/rerank`, final stage in `/v1/query`+`/v1/retrieve`) | #20 | opt-in `RERANK_ENABLED`; live-validated |
| **JSONL scholarly ingestion + enrichment** (DOI/title/authors/citations/doc_type) | #19 | `ingestion/enrich.py`, `JsonlLoader`, `scripts/ingest_jsonl.py`, `scripts/verify_doi_pubmed.py` |
| **Parallelized bulk ingester** (fan-out across embed endpoints, ordered checkpoint) | #21 | `--concurrency`, `--embedding-url ...` |
| Per-request rerank control (`rerank`, `rerank_candidates`) | #27/#33 | additive contract change |
| Injectable `PublisherProfile` (enrichment no longer ASM-hardcoded) | #26/#34 | ASM is the default profile |
| Shared `SidecarClient` HTTP helper | #28/#32 | embedder + reranker |
| Sentence/Word/**Semantic** chunkers | #30/#36 | `chunk_method` config (default `fixed`); NLTK `chunking` extra |
| **Ingest data-loss fix** (upsert-then-prune; `delete_except` by-id; `--qdrant-timeout`) | #31/#37 | see gotcha #1 below |
| **M4 Knowledge Graph Phase 1** (`Neo4jGraphStore`, tenant-scoped, graph endpoints live) | #35 | v0.11.0 |
| **M4 Knowledge Graph Phase 2** (`LLMKGExtractor` + pipeline/retriever wiring + tenant-scoped graph leg) | #40 | KG extraction **off by default** |
| API reference doc | #24 | `docs/API.md` |

**Tests:** 318 pass / 1 skip on `main`. ruff clean. mypy clean (0 errors — baseline was cleaned up).

---

## 2. Live infrastructure RIGHT NOW (important — non-obvious, not in git)

A real corpus and a local GPU embedding fleet are **up and in use**:

- **SFR corpus ingested & verified:** **877,343 chunks**, 11,343 docs (9,746 article / 1,349 supplement / 133 front-matter / 115 short), tenant `public`.
  - Qdrant collection: `ragstack_salesforce_sfr_embedding_mistral_4096_928f8ebe` (4096-dim).
  - Elasticsearch index: `ragstack_sfr`.
  - Source: `/rag/inputs/09320c55-a8a7-4f4d-81b3-ae55b7a329fa.jsonl` (11,573 records; `/rag/inputs` is **read-only** to us — see gotcha #4).
  - Full per-doc metadata **catalog** (incl. citations): `/rag/cache/sfr.catalog.jsonl` (11,343 rows).
- **4× vLLM serving `Salesforce/SFR-Embedding-Mistral` (4096-dim)** on H200 GPUs 0–3, ports **9001–9004** (OpenAI-compatible `/v1/embeddings`). Launched from a dedicated env `/rag/envs/vllm` (vLLM 0.23.0). Started with `--runner pooling` (see gotcha #2). GPUs 4–7 left free.
- **Crossencoder reranker sidecar** on **:50052** (`cross-encoder/ms-marco-MiniLM-L-6-v2`, Apptainer).
- **Embedding sidecar** on **:50053** (BGE-base, 768-dim) — the *other* embedding space; unrelated to the SFR corpus.
- Standard infra up: Qdrant :6333, Elasticsearch :9200, Neo4j :7474/:7687 (pwd `ragstack`), Postgres :5432, Redis :6379.
- **`/rag/env.sh`** (mode 0600, holds the HF token) redirects HF/vLLM caches to `/rag/cache`; it is sourced by `/rag/config/rag.env` (so `. /rag/bin/activate` and `rag` pick it up). `/rag/envs/vllm/bin/vllm` is the vLLM CLI.
- **mango.cels.anl.gov:9998** also serves SFR (GPU vLLM) and is reachable; **maple…:9998 is NOT reachable** from coconut.

**Decision pending:** the 4 vLLM endpoints hold GPUs 0–3. Keep them up (needed to query/re-ingest the SFR corpus, and to enable KG extraction), or tear down if idle. To stop: `apptainer`/process kill of the `vllm serve` instances (they were launched as background processes, not Apptainer instances).

### Query the SFR corpus via the API
The query embedding model MUST match the corpus (SFR/4096), else vector search dim-mismatches. Run the Python API with:
```
EMBEDDING_API=openai  EMBEDDING_SIDECAR_URL=http://localhost:9001
EMBEDDING_MODEL=Salesforce/SFR-Embedding-Mistral  EMBEDDING_MODEL_DIM=4096
TEXT_BACKEND=elasticsearch  ELASTICSEARCH_INDEX=ragstack_sfr
RERANK_ENABLED=true  CROSSENCODER_SIDECAR_URL=http://localhost:50052
API_KEYS='["kp"]'  API_KEY_TENANTS='{"kp":"public"}'   # then X-API-Key: kp
```
Bulk re-ingest recipe (fan-out, the measured ~33 min / full corpus, ~443 chunks/s e2e):
```
python scripts/ingest_jsonl.py /rag/inputs/<file>.jsonl --tenant public \
  --embedding-api openai \
  --embedding-url http://localhost:9001 http://localhost:9002 http://localhost:9003 http://localhost:9004 \
  --embedding-model Salesforce/SFR-Embedding-Mistral \
  --text-backend elasticsearch --es-index ragstack_sfr \
  --concurrency 16 --batch-size 64 \
  --catalog-out /rag/cache/sfr.catalog.jsonl --checkpoint /rag/cache/sfr.ckpt
```

---

## 3. Next steps (todo, priority order)

1. **Reconcile / verify nothing regressed** — `main` is green (318 pass). The #38/#39 graph-tenant fix and #40 Phase 2 both touched the retriever; they merged cleanly, but a quick read of `retrieval/retriever.py` `_graph_context` is worth it before building on the graph leg.
2. **Tune + live-enable KG extraction (M4)** — `LLMKGExtractor` is merged but **off** (`KG_EXTRACTION_ENABLED=false`) and the prompt is **untuned**. To exercise it: configure an LLM endpoint (`LLM_ENDPOINT`/`LLM_MODEL` — e.g. a vLLM chat model; the SFR endpoints are *embedding* only, so a separate chat model is needed), set `KG_EXTRACTION_ENABLED=true` + bounds (`KG_EXTRACTION_MAX_CHUNKS`), re-ingest a small slice, and inspect triples via `/v1/graph/entities` + `/v1/graph/neighbors/{entity}`. Tune the extraction prompt against the real model. Neo4j is up (set `GRAPH_BACKEND=neo4j`, pwd `ragstack`).
3. **#41 Go parity for KG** — the Go impl has no KG extractor/wiring (noted TODO from #40). Also Go parity TODOs exist for per-request rerank (#27) and chunkers (#30).
4. **#25 — fold `ingest_jsonl.py` onto the shared `IngestionPipeline`/`ShardedIngestor`** (larger refactor). The script still hand-rolls embed/replace/checkpoint; the #31 fix had to be hand-ported. Detailed plan exists (see the planning workflow output / the issue). The same delete-before-upsert pattern exists latently in `pipeline.py` — fix once on the shared path here.
5. **#29 — return document-level citations on query** (API-side doc-metadata join, keyed by `source_path`/`doc_id`). Today queries return `n_citations` (count) only; the full list lives in the catalog. Issue has the design + the no-code app-side join workaround.
6. **M2 resume (#6/#7)** — per-owner lease for `fail_interrupted` under Postgres; API-level resume for interrupted ingest jobs. Pre-existing, needed before multi-worker ingest.
7. **Observability (M7)** — `otel_exporter_otlp_endpoint` config exists, unused. Lower urgency.

---

## 4. Gotchas learned this session (beyond repo MEMORY.md)

1. **Re-ingest data-loss (fixed in #37).** The bulk ingester did `delete → upsert` per doc; on a large collection the *filtered* delete-by-doc_id times out under concurrency (qdrant `ResponseHandlingException`), the delete commits, the upsert doesn't → points lost. Now **upsert-only by default**; orphan pruning is opt-in `--replace` (upsert-then-prune, by-id via `delete_except`). **Do not** reintroduce delete-before-upsert on large collections. (The 877k corpus lost ~50 docs' chunks during a verify; repaired with an upsert-only pass.)
2. **vLLM embedding model needs `--runner pooling`** (vLLM 0.23). `--task embed` is NOT a valid flag in this version. `vllm serve <model> --runner pooling --port <p> --gpu-memory-utilization 0.4`.
3. **Qdrant `points_count` is approximate while a collection is `yellow`/optimizing** — it drifts and settles. Use it as a trend; for exact counts wait for `green` or cross-check ES `_count`.
4. **`/rag/inputs` is owned by another user and is read-only to us** — write catalogs/checkpoints to `/rag/cache` (writable, 3.7T free), not next to the source JSONL.
5. **Two embedding spaces coexist:** BGE-base 768-d (sidecar :50053) and SFR-Mistral 4096-d (vLLM :9001-4 / mango). A corpus must be queried with the SAME model it was ingested with; Qdrant collections are auto-named `f(model,dim)` so they don't collide, but you must set `EMBEDDING_MODEL`/`EMBEDDING_MODEL_DIM` to match.
6. **Embedding throughput:** single CPU BGE sidecar ~40 chunks/s e2e; single SFR endpoint ~83/s serial; **4 SFR endpoints + parallel ingester ~443 chunks/s e2e** (bottleneck shifts to Qdrant upsert / ES, not embedding). Fan-out needs the *client* to issue concurrent requests (the parallelized ingester does).

---

## 5. Process rules (unchanged — follow for continuity)

- One **feature branch per increment** off `main`; small commits. Commit footer required:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` + the `Claude-Session:` line.
  PR body footer: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **The user merges PRs + cuts tags + bumps STATUS.** After a merge: `git checkout main && git pull`, prune merged branches. Reviewers (maintainer + Copilot) often harden merged code and file follow-up issues — re-read merged files before building on them.
- **Contract-first:** change `contracts/openapi.yaml` + `contracts/schemas/*.json` (`additionalProperties:false`) → both `python/` and `go/` → conformance. Most features need no contract change.
- **Invariants:** tenant_id is server-side (own + `public` on reads, own on writes), threaded through every store/retrieval path; graceful degradation (no 500s); deterministic chunk ids for idempotent re-ingest; optional deps lazy-imported behind `pyproject` extras.
- **Subagents/workflows:** read-only fan-out and worktree-isolated implementation worked well this session (the "ultracode" run shipped #26/#27/#28/#30 + M4-P1 as parallel PRs). Do **not** fan out parallel writers at the live stateful stack; drive live smokes sequentially; never run a delete-before-upsert re-ingest against the live collection.

---

## 6. Operating the stack (coconut)
```bash
. /rag/bin/activate            # RAG_* + HF/vLLM env (via /rag/env.sh) + conda env /rag/envs/ragstack
cd $RAG_REPO/python
python -m pytest tests/ -q     # 318 pass / 1 skip
rag <make-target>              # operator wrapper (infra-up-apptainer, sidecars-up-apptainer, ...)
/rag/envs/vllm/bin/vllm serve Salesforce/SFR-Embedding-Mistral --runner pooling --port 9001 --gpu-memory-utilization 0.4   # an SFR endpoint
```
Open issues backlog: #25, #29, #41, #6, #7 (+ M7). Scratch artifacts from this session live under `/rag/cache/` (catalog, ckpts, logs) and the session scratchpad.
