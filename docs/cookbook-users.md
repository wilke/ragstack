# Cookbook — using a RAGStack deployment

*Copy-paste recipes for the questions people actually ask: pick a deployment,
sign in, make a collection, put papers in it, ask it things, read around a hit,
see how the server is configured. The concepts behind each step are in the
[user guide](USER-GUIDE.md); the reference is [API.md](API.md). Provisioning a
whole new deployment is a different cookbook:
[cookbook-new-org-ingest.md](cookbook-new-org-ingest.md).*

Every recipe assumes two shell variables. Set them once:

```bash
# 0. Pick a deployment (see the table in the user guide) and a credential.
export BASE=http://coconut.cels.anl.gov:9000/ragstack/demo/api

# EITHER a key the operator gave you …
export AUTH="X-API-Key: rk-your-key-here"
# … OR your own BV-BRC identity (dev and lucid-next accept ONLY this):
p3-login <your-bvbrc-username>            # writes ~/.patric_token
export AUTH="Authorization: $(cat ~/.patric_token)"

curl -s $BASE/health                       # {"status":"ok"} — no credential needed
```

Never send both headers in one request (`400`). `jq` is optional but every
recipe is easier to read with it.

---

## 1. Who am I, and what can I read?

```bash
curl -s $BASE/v1/stats/tenants -H "$AUTH" | jq '{tenant, role, readable, auth_enabled}'
```
```json
{"tenant": "demo-ops", "role": "admin", "readable": ["demo-ops", "public"], "auth_enabled": true}
```

`tenant` is the scope your writes land in; `readable` is what your reads cover
(always your own scope plus the shared `public` one). The same response lists
every collection in each readable scope with its counts — the full picture of
what this credential can see.

## 2. List the collections

```bash
curl -s $BASE/v1/collections -H "$AUTH" \
  | jq '.default, (.collections[] | {id, label, count, text_count, chunk_method, dim})'
```
```json
"default"
{"id":"open-access","label":"open-access","count":47625155,"text_count":47625155,"chunk_method":"fixed_token","dim":4096}
```

`.default` is the id used when a request omits `collection`. `count` is the
vector-store count *as visible to you*; `text_count` the BM25 side — they
should match.

## 3. Create a collection

```bash
curl -s -X POST $BASE/v1/collections -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"id": "my-papers", "label": "My papers"}' | jq
```

- `201` → it exists, built with the server-default embedding model + chunker,
  **private to you**.
- `409` → that id is taken (or, with `id` omitted, a corpus with this exact
  build spec already exists — that is the same store, use it).
- `403` → you passed `embedding`/`chunk` without the admin role, or the
  deployment is at its collection cap.

Omit `id` when you want a content-addressed corpus rather than a named library;
see [user guide §3](USER-GUIDE.md#3-create-a-collection).

## 4. Put PDFs in it and watch the job

```bash
JOB=$(curl -s -X POST $BASE/v1/ingest/upload -H "$AUTH" \
        -F collection=my-papers -F files=@paper1.pdf -F files=@paper2.pdf | jq -r .job_id)

# poll until status is completed (or failed)
watch -n 5 "curl -s $BASE/v1/ingest/$JOB -H '$AUTH' | jq '{status, error, chunks, items}'"

# the count climbs as chunks land
curl -s $BASE/v1/collections -H "$AUTH" | jq '.collections[] | select(.id=="my-papers") | .count'
```

PDFs only (`415` otherwise), ≤ 50 MB each (`413`), ≤ 50 files per request.
`GET /v1/documents` lists your documents (paginated: `limit`, `cursor`);
`DELETE /v1/documents/{doc_id}` removes one document's chunks.

## 5. Share it — or make it public

```bash
# read access for one BV-BRC user (a bare username is qualified to bvbrc:<username>)
curl -s -X POST $BASE/v1/collections/my-papers/shares -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"grantee": "colleague@patricbrc.org", "permission": "read"}'

# world-readable (every caller's "public" scope)
curl -s -X POST $BASE/v1/collections/my-papers/shares -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"grantee": "@public", "permission": "read"}'

# a group (@group:<id>, must exist) or a service account (@service:<subject>) work the same way;
# the response echoes the resolved subject so a typo is visible

# list / revoke
curl -s $BASE/v1/collections/my-papers/shares -H "$AUTH" | jq
curl -s -X DELETE $BASE/v1/collections/my-papers/shares/<share_id> -H "$AUTH"
```

Only the owner (or an admin) can share, upload into, or delete the collection;
someone with a `read` share can query it. `POST …/owner` transfers ownership —
and takes your own access with it. Details: [API.md → Collection shares](API.md#collection-shares).

## 6. Ask a question

Full RAG — answer plus the sources it was grounded on:

```bash
curl -s -X POST $BASE/v1/query -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"query": "how do efflux pumps confer multidrug resistance?",
       "collection": "open-access", "top_k": 5}' \
  | jq '{answer, rewritten_queries, sources: [.sources[] | {chunk_id, score, title: .metadata.title, doi: .metadata.doi}]}'
```

Retrieval only — the ranked passages, no LLM:

```bash
curl -s -X POST $BASE/v1/retrieve -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"query": "efflux pumps multidrug resistance", "collection": "open-access", "top_k": 5}' \
  | jq '.sources[] | {chunk_id, score, section: .metadata.section_title, title: .metadata.title}'
```

One source, in full:

```json
{"doc_id": "5f94e8b5-…", "chunk_id": "f3020aa7-…", "score": 0.0164,
 "content": "…the passage…",
 "metadata": {"title": "…", "authors": "…", "journal": "…", "doi": "10.…", "pmcid": "PMC…",
              "section_title": "Results", "chunk_index": 1,
              "prev_chunk_id": "3fa12cef-…", "next_chunk_id": "c6304b79-…", "source_url": "…"}}
```

`404` means the collection is unknown **or not readable by you** — check
recipe 2.

## 7. Tune the retrieval per request

All of these are request fields; none of them change the server.

```bash
# keyword-only (exact terms, no embedding), no rerank, more hits
-d '{"query": "blaKPC-2 Klebsiella", "collection": "open-access",
     "retrieval_mode": "bm25", "rerank": false, "top_k": 20}'

# dense-only, and force the cross-encoder on with a deeper pool
-d '{"query": "…", "retrieval_mode": "vector", "rerank": true, "rerank_candidates": 100}'

# restrict by metadata (equality, ANDed) — any field the ingester stamped
-d '{"query": "…", "filters": {"journal": "mBio"}}'

# let the LLM expand the question first (needs an LLM on the deployment)
-d '{"query": "…", "rewrite_strategies": ["passthrough", "multiquery"]}'

# a different registered model, for this request only
curl -s $BASE/v1/models/available -H "$AUTH"           # {"models":[{"id":…,"task":"llm"|"reranker",…}]}
-d '{"query": "…", "llm": "<model id>"}'
```

## 8. Read around a hit — the previous and next chunks

Every source carries the ids of its neighbours in the same document. Fetch them
by id (up to 20 per call, order preserved, anything you may not read silently
omitted):

```bash
# take the top hit's neighbours…
curl -s -X POST $BASE/v1/retrieve -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"query": "efflux pumps multidrug resistance", "collection": "open-access", "top_k": 1}' \
  > hit.json
IDS=$(jq -r '.sources[0].metadata | [.prev_chunk_id, .next_chunk_id] | map(select(. != null and . != "None")) | join(",")' hit.json)

# …and fetch them
curl -s "$BASE/v1/chunks?collection=open-access&ids=$IDS" -H "$AUTH" \
  | jq '.chunks[] | {chunk_id, idx: .metadata.chunk_index, next: .metadata.next_chunk_id, content: .content[:120]}'
```

Each returned chunk carries its own `prev_chunk_id` / `next_chunk_id`, so
repeating the second call with the new `next_chunk_id` walks forward one chunk
per call — the ids are the cursor. A whole document, forward from a hit:

```bash
NEXT=$(jq -r '.sources[0].metadata.next_chunk_id' hit.json)
while [ -n "$NEXT" ] && [ "$NEXT" != "null" ] && [ "$NEXT" != "None" ]; do
  C=$(curl -s "$BASE/v1/chunks?collection=open-access&ids=$NEXT" -H "$AUTH" | jq '.chunks[0]')
  echo "$C" | jq -r '"[\(.metadata.chunk_index)] \(.content[:100])…"'
  NEXT=$(echo "$C" | jq -r '.metadata.next_chunk_id')
done
```

(At a document's first/last chunk the neighbour is `null` — or the literal
string `"None"` on corpora bulk-loaded before that was tightened; the guard
above handles both.) In the UI this is the **‹ prev / next ›** walker on a
source card in *Evidence*.

## 9. See how the deployment is configured

```bash
curl -s $BASE/v1/config -H "$AUTH" | jq     # admin role required; 403 otherwise
```
```json
{"vector_backend": "qdrant", "text_backend": "elasticsearch", "graph_backend": "disabled",
 "embedding_api": "openai", "embedding_model": "Salesforce/SFR-Embedding-Mistral", "embedding_model_dim": 4096,
 "chunk_method": "fixed_token", "chunk_size": 512, "chunk_overlap": 64,
 "top_k": 5, "rerank_enabled": true, "rerank_candidates": 50, "reranker_model": "BAAI/bge-reranker-v2-m3",
 "qdrant_collection_explicit": "demo_g1_sfr_tok512", "elasticsearch_index": "demo_g1_sfr_tok512",
 "max_document_bytes": 50000000, "ingest_concurrency": 4, "log_level": "INFO", "...": "36 keys, never a secret"}
```

That is the *effective* configuration of the deployment you are talking to.
The complete catalogue of options — every one an environment variable named
after the field in upper case — is `python/ragstack/config.py`; the ones an
operator is expected to set, with their gotchas, are tabulated in
[API.md → Configuration](API.md#configuration-server).

## 10. When it doesn't work

| You see | It means | Do |
|---|---|---|
| `401 missing or invalid API key` / `invalid or expired bearer credential` | wrong key, expired token, or a token sent to a deployment without identity support | recipe 1 against the right `$BASE`; `p3-login` again |
| `400 present exactly one credential` | both headers sent | unset one |
| `403` on `POST /v1/collections` | you sent `embedding`/`chunk`, or the cap is reached | drop the override; ask an admin |
| `404` on a collection you know exists | you cannot read it | ask the owner for a share (recipe 5) |
| `409` on create | id taken / spec exists | pick another id, or use the existing one |
| `422` | body failed validation | read `detail` — usually a wrong type or an unknown `retrieval_mode` |
| `503 qdrant unavailable: … ReadTimeout … QDRANT_TIMEOUT` | the vector store was too slow for this request (host under load) | retry after `Retry-After`; if it persists, tell the operator — it is the deployment, not your request |
| `503` on ingest | `INGEST_ROOT` unset on that deployment | operator setting; uploads are disabled there |
| UI says "not signed in" right after signing in | that backend has no identity provider, so the token is not an authentication input | switch to a deployment that verifies tokens, or use a key |
| The answer is `[LLM not configured]` | no LLM on this deployment | the sources are real; use `/v1/retrieve`, or pass `llm` if a model is registered |
