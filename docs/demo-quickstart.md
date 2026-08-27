# RAGStack demo quickstart

Stand up a RAGStack demo from scratch on a fresh host: create a collection,
upload some PDFs, ask questions over the API, and point Claude (Claude Desktop
or Claude Code) at it over MCP.

This is deliberately generic — replace the placeholder endpoints, model ids, and
collection names with your own. Everything here uses the API's HTTP surface, so
it works the same whether you run the server locally (`make run-python`) or
against a deployed instance.

For the exact API contract see [`contracts/openapi.yaml`](../contracts/openapi.yaml);
for MCP wiring detail see [`python/ragstack/mcp/README.md`](../python/ragstack/mcp/README.md).
For an existing deployment rather than one you stand up, start at the
[user guide](USER-GUIDE.md) and [COOKBOOK.md](COOKBOOK.md).

---

> ### Set `BASE` and `QDRANT_URL` yourself. There are no defaults here, on purpose.
>
> Every command below uses `"$BASE"`, and step 1 requires `QDRANT_URL`. Neither
> has a default, because the obvious defaults are dangerous: on the RAGStack
> deployment host `http://localhost:6333` and `http://localhost:9200` are the
> **production Qdrant and Elasticsearch**, and `http://localhost:8000` is the
> **legacy production API**. This guide creates collections and ingests
> documents — a copy-pasted command with those defaults is a production write.
>
> ```bash
> export PORT=8030                           # a port you own — check `ss -ltn` first
> export BASE=http://127.0.0.1:$PORT         # the API *you* start in step 1
> export QDRANT_URL=http://127.0.0.1:6399    # a Qdrant *you* started
> ```
>
> On a laptop with nothing else running, the standard ports are fine. On a
> shared host, they are not — pick ports you own, and check with
> `ss -ltn` before you bind. The same applies to any `python/scripts/` CLI:
> pass `--qdrant-url` / `--es-url` explicitly, never rely on their defaults
> (#454).

## 0. Prerequisites

You need three model backends reachable from the API:

- **A Qdrant instance** (vector store) at the `QDRANT_URL` you just set — one
  you started, not one you found listening.
- **An embeddings endpoint** that speaks the OpenAI `/v1/embeddings` API — a
  vLLM server, or the bundled embedding sidecar. Note its model name and vector
  dimension.
- **An LLM endpoint** that speaks the OpenAI `/v1/chat/completions` API, for
  answer generation. (Optional — without it, `/v1/query` returns retrieved
  chunks and a note instead of a generated answer.)

Elasticsearch is optional; the demo below runs vector-only retrieval.

## 1. Start the API

Run the FastAPI app with `INGEST_ROOT` set (uploads have nowhere confined to
land without it — the upload endpoint returns 503 when it is unset) and pointed
at your backends. Adjust the values to your environment:

```bash
export PYTHONPATH="$PWD/python"
export INGEST_ROOT=/var/lib/ragstack/ingest      # a writable dir for staged uploads
# QDRANT_URL is already exported — see the box above. Do NOT default it.
export EMBEDDING_API=openai
export EMBEDDING_MODEL=your-embedding-model       # e.g. Salesforce/SFR-Embedding-Mistral
export EMBEDDING_MODEL_DIM=4096                   # must match the model's real dim
export EMBEDDING_ENDPOINTS=http://localhost:9001,http://localhost:9002  # comma list, NOT JSON
export LLM_ENDPOINT=http://localhost:8003         # optional; enables generated answers
export LLM_MODEL=your-chat-model
export CHUNK_METHOD=fixed_token
export CHUNK_SIZE=512
export CHUNK_OVERLAP=64
export RERANK_ENABLED=false
export DEFAULT_ROLE=admin                         # demo convenience; needed for the
                                                  # embedding/chunk overrides + model admin.
                                                  # Plain collection creation (server-default
                                                  # build spec) works for any principal.

uvicorn ragstack.api.main:app --host 127.0.0.1 --port "$PORT"
```

> Bound to `127.0.0.1`, not `0.0.0.0`: with no credential configured this API is
> open to anyone who can reach the socket, and `DEFAULT_ROLE=admin` above makes
> every such caller an admin. Widen the bind only once you have set `API_KEYS`.

> `EMBEDDING_ENDPOINTS` must be a **comma-separated list**, not a JSON array —
> if you source these vars from a shell file the quotes get stripped and a JSON
> array will not parse.

Confirm it is up:

```bash
curl -s "$BASE"/health          # -> {"status":"ok"}
```

## 2. Register an embedding model (admin)

A collection binds to a *registered* embedding model. Register one first
(admin role required):

```bash
curl -s -X POST "$BASE"/v1/admin/models/registry \
  -H 'Content-Type: application/json' \
  -d '{
        "id": "my-embedder",
        "task": "embedding",
        "provider": "openai",
        "model": "your-embedding-model",
        "dim": 4096,
        "base_urls": ["http://localhost:9001", "http://localhost:9002"]
      }'
```

List what is registered with `GET /v1/admin/models/registry`.

## 3. Create a collection

Create an empty collection bound to that model and a chunk strategy. Build-time
config (model, dim, chunk method) *is* the collection's identity — you populate
it via ingest, you do not re-point it at a different embedder later.

```bash
curl -s -X POST "$BASE"/v1/collections \
  -H 'Content-Type: application/json' \
  -d '{
        "embedding": "my-embedder",
        "chunk": { "method": "fixed_token", "size": 512, "overlap": 64 },
        "id": "my_papers",
        "label": "My demo library"
      }'
```

Passing `id` names a **library**: the id is folded into the physical Qdrant
collection / ES index name, so two libraries built with the same embedding model
and chunker (which is what the UI's "＋ New library" does) each get their own
store instead of aliasing one.

Omit `id` for a **corpus**: both the id and the physical name are then
content-addressed over (model, dim, chunk), so re-creating the same build spec
maps back to the same store (idempotent re-ingest) and 409s at the registry.

Verify with `GET /v1/collections`.

## 4. Upload PDFs

`POST /v1/ingest/upload` is the multipart counterpart to `POST /v1/ingest`
(which takes a server-side path). Each file is staged under
`{INGEST_ROOT}/uploads/{tenant}/{job_id}/` and ingested in the background.
It returns **202** with a `job_id`. The content-type allowlist
(`UPLOAD_CONTENT_TYPES`) accepts **PDF, plain text, Markdown and XML** by
default — anything outside it, or a "PDF" that does not start with `%PDF`, is a
`415`; oversize files are a `413`. (An `.xml` file is accepted at the door but
has no loader yet, so its *item* fails inside the job.)

```bash
curl -s -X POST "$BASE"/v1/ingest/upload \
  -F 'collection=my_papers' \
  -F 'files=@paper1.pdf' \
  -F 'files=@paper2.pdf'
# -> {"job_id":"...","status":"accepted"}
```

The lifecycle is `accepted` → `running` → `completed` | `failed`. There is no
`pending` state — polling for one never terminates.

Poll the job until it finishes:

```bash
curl -s "$BASE"/v1/ingest/<job_id>
```

Re-check `GET /v1/collections` — the chunk count for `my_papers` should climb as
ingest completes.

## 5. Query

Retrieval only (ranked chunks, no LLM):

```bash
curl -s -X POST "$BASE"/v1/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is the main finding?","collection":"my_papers","top_k":5}'
```

Full RAG (retrieval + generated answer with citations):

```bash
curl -s -X POST "$BASE"/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is the main finding?","collection":"my_papers"}'
```

The response has `answer`, `sources` (each with `doc_id`, `chunk_id`, `score`,
`content`, and metadata), and `rewritten_queries`.

## 6. Point Claude at it (MCP)

RAGStack ships an MCP server exposing three tools — `search` (retrieval),
`answer` (full RAG), and `list_collections`. There are two ways to run it.

### Option A — Python MCP server (in this repo)

Needs a Python env with RAGStack + the `mcp` SDK (`pip install -e ".[mcp]"`).
Configure Claude Code:

```bash
claude mcp add ragstack \
  -e RAGSTACK_BASE_URL="$BASE" \
  -e RAGSTACK_COLLECTION=my_papers \
  -- /path/to/python -m ragstack.mcp
```

Or add the same block to Claude Desktop's `claude_desktop_config.json` /
a project `.mcp.json`:

```json
{
  "mcpServers": {
    "ragstack": {
      "command": "/path/to/python",
      "args": ["-m", "ragstack.mcp"],
      "env": {
        "RAGSTACK_BASE_URL": "http://127.0.0.1:8030",
        "RAGSTACK_COLLECTION": "my_papers"
      }
    }
  }
}
```

Set `RAGSTACK_API_KEY` too if the server enforces one. See
[`python/ragstack/mcp/README.md`](../python/ragstack/mcp/README.md) for the full
reference.

### Option B — Go single-binary MCP server (no clone)

A standalone Go binary (`go/cmd/mcp`, PR
[#220](https://github.com/wilke/ragstack/pull/220)) is the no-clone alternative:
build it once, ship the binary, and no Python env or repo checkout is needed on
the client host. It reads the same `RAGSTACK_BASE_URL` / `RAGSTACK_COLLECTION` /
`RAGSTACK_API_KEY` env vars and exposes the same three tools:

```json
{
  "mcpServers": {
    "ragstack": {
      "command": "/path/to/ragstack-mcp",
      "env": {
        "RAGSTACK_BASE_URL": "http://127.0.0.1:8030",
        "RAGSTACK_COLLECTION": "my_papers"
      }
    }
  }
}
```

Once wired up, ask Claude *"What collections are available?"* to confirm, then
ask real questions — Claude will call `answer`/`search` and cite the sources.

## Example questions

Point these at your own corpus — they assume a biomedical / AMR library:

- "What mechanisms do bacteria use to develop resistance to antibiotics?"
- "Summarize the mechanisms of carbapenem resistance in Klebsiella."
- "What role do efflux pumps play in antibiotic resistance?"

---

## Want a populated corpus instead of building one?

This guide stands up your own stack. If you just want to *ask questions* of an
existing one, do not run any of the above: the deployments on `coconut` are
already populated, and the [user guide](USER-GUIDE.md#1-pick-a-deployment-what-the-api-calls-a-tenant)
has the table of base URLs, what each one holds, and how to authorize. The
`demo` deployment serves the `open-access` corpus through the gateway at
`/ragstack/demo/api`; [cookbook-users.md](cookbook-users.md) is the copy-paste
version and [COOKBOOK.md](COOKBOOK.md) answers the questions that come after.
