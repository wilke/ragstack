# Cookbook — using a RAGStack deployment

*Copy-paste recipes for the questions people actually ask: pick a deployment,
sign in, make a collection, put papers in it, ask it things, read around a hit,
see how the server is configured. The concepts behind each step are in the
[user guide](USER-GUIDE.md); the reference is [API.md](API.md). For the same
ground as questions — including the error contract, the `Reference:` id, which
503s are worth retrying, and what an omitted `collection` targets — see
[COOKBOOK.md](COOKBOOK.md). Provisioning a whole new deployment is a different
cookbook: [cookbook-new-org-ingest.md](cookbook-new-org-ingest.md).*

Every recipe assumes two shell variables. Set them once:

```bash
# 0. Pick a deployment (see the table in the user guide) and a credential.
export BASE=http://coconut.cels.anl.gov:9000/ragstack/demo/api

# EITHER a key the operator gave you …
export AUTH="X-API-Key: rk-your-key-here"
# … OR your own BV-BRC identity. All four deployments accept both —
# pick one, never send both:
p3-login <your-bvbrc-username>            # writes ~/.patric_token
export AUTH="Authorization: $(cat ~/.patric_token)"

curl -s $BASE/health                       # {"status":"ok"} — no credential needed
```

Never send both headers in one request (`400`). `jq` is optional but every
recipe is easier to read with it.

---

## 1. Who am I, and what can I read?

```bash
curl -s "$BASE/v1/stats/tenants?counts=false" -H "$AUTH" \
  | jq '{tenant, role, readable, auth_enabled}'
```
```json
{"tenant": "demo-ops", "role": "admin", "readable": ["demo-ops", "public"], "auth_enabled": true}
```

`tenant` is the scope your writes land in; `readable` is what your reads cover
(always your own scope plus the shared `public` one).

**`counts=false` is not optional politeness.** The default probes one count per
tenant × collection × store; on the demo corpus that is seconds of waiting —
and the four fields above need no store at all. Drop it only when you actually
want the numbers:

```bash
curl -s $BASE/v1/stats/tenants -H "$AUTH" | jq '.tenants'   # slow: counts every cell
```

## 2. List the collections

```bash
curl -s $BASE/v1/collections -H "$AUTH" \
  | jq '.default, (.collections[] | {id, label, count, text_count, chunk_method, dim})'
```
```json
"default"
{"id":"open-access","label":"open-access","count":47625155,"text_count":47625155,"chunk_method":"fixed_token","dim":4096}
```

`.default` is the id **your** requests hit when they omit `collection` — it is
computed per caller, so read it here rather than assuming the deployment's
pointer. (The per-entry `is_default` flag is a *different* thing: the global
registry pointer. When you cannot read the collection that pointer names, no
entry carries the flag and `.default` still names a readable collection of
yours.) `count` is the vector-store count *as visible to you*; `text_count` the
BM25 side — they should match.

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
  deployment sets `ALLOW_USER_COLLECTION_CREATE=false`.
- `507` → the deployment is at its active-collection bound and nothing could be
  evicted. Normally invisible: at the bound the server evicts one
  least-recently-used archived collection and retries. `detail` names the
  per-reason counts, so you can tell "wait for an in-flight ingest" from "ask
  the operator to raise `MAX_COLLECTIONS`".

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

PDF, plain text, Markdown and XML (JATS) are accepted — the deployment's
`UPLOAD_CONTENT_TYPES`; anything else, or a "PDF" that does not start with
`%PDF`, is `415`. ≤ 50 MB each (`413`), ≤ 50 files and ≤ 500 MB per request.
An `.xml` file is accepted but has no loader yet: its *item* fails with
`no loader for .xml`.

Always pass `-F collection=…` as above. **If you omit it**, the upload goes to
the first collection *you can write* — and if you own none, that is a `403`
`no collection accepts your uploads`, not a `404` (#453). The `404`
`no collection is accessible to this caller` is the different case: you can read
nothing at all. See
[COOKBOOK.md → Why was my upload refused when I can read the collection
fine?](COOKBOOK.md).

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
by id (up to **200** per call, `422` above that; order preserved, anything you
may not read silently omitted):

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
 "max_document_bytes": 50000000, "ingest_concurrency": 4, "log_level": "INFO", "...": "38 keys, never a secret"}
```

That is the *effective* configuration of the deployment you are talking to.
The complete catalogue of options — every one an environment variable named
after the field in upper case — is `python/ragstack/config.py`; the ones an
operator is expected to set, with their gotchas, are tabulated in
[API.md → Configuration](API.md#configuration-server).

## 10. When it doesn't work

| You see | It means | Do |
|---|---|---|
| `401` **with** a credential sent (`missing or invalid API key` / `invalid or expired bearer credential`) | that credential was refused — wrong key, expired token | recipe 1 against the right `$BASE`; `p3-login` again |
| `401` with **no** credential sent | not a refusal: you are simply signed out. A keyed backend 401s an anonymous caller | send `$AUTH`. Re-running `p3-login` changes nothing |
| `400 present exactly one credential` | both headers sent | unset one |
| `403` on `POST /v1/collections` | you sent `embedding`/`chunk` without admin, or the deployment sets `ALLOW_USER_COLLECTION_CREATE=false` | drop the override; ask an admin |
| `507` on `POST /v1/collections` | at the active-collection bound and nothing was evictable | read `detail`'s per-reason counts; wait, delete one of yours, or ask the operator to raise `MAX_COLLECTIONS` |
| `403 no collection accepts your uploads` on ingest | you omitted `collection` and own none | name one you own, or create one (recipe 3) |
| `404 no collection is accessible to this caller` | your readable set is empty | ask for a share (recipe 5) |
| `404` on a collection you know exists | you cannot read it | ask the owner for a share (recipe 5) |
| `409` on create | id taken / spec exists | pick another id, or use the existing one |
| `422` | body failed validation | read `detail` — usually a wrong type or an unknown `retrieval_mode` |
| `503` with `"reason": "timeout"` | we reached the store and the search was too slow | **retry** — the second read is often warm |
| `503` with `"reason": "unreachable"` or `"error"` | we never reached the store, or it answered unhappily | a retry probably will not help; send the operator the `request_id` |
| `503` with **no** `reason` | a different cause: authorization store failing closed, a dormant/restoring collection (its restore was kicked off), or the tenant at capacity | honour `Retry-After`; treat as "do not assume a retry helps" |
| `503` on ingest | `INGEST_ROOT` unset on that deployment | operator setting; uploads are disabled there |
| UI says **"Checking sign-in…"** | the identity check has not answered yet | wait — this is not a verdict, and there is nothing to act on |
| UI says **"Not confirmed"** / `unconfirmed` | the check *failed*, so there is no verdict either way; your credential is still stored and still being sent | read the amber sentence; it is usually the API being unreachable, not your credential |
| UI says **"not signed in"** with an amber sentence about the token being ignored | that backend has no identity provider, so every caller is the default tenant | none of the four `coconut` deployments is in this state — check you are on the backend you think you are |
| The answer is `[LLM not configured]` | no LLM on this deployment | the sources are real; use `/v1/retrieve`, or pass `llm` if a model is registered |

**Every response carries `X-Request-Id`**, and the UI prints it under an error as
`Reference: <id>`. Quote it verbatim when you report a problem — it is the grep
key for [the operator's runbook](runbooks/tracing-a-503.md). To capture it
yourself:

```bash
curl -sS -D- -o /dev/null -X POST $BASE/v1/retrieve -H "$AUTH" \
  -H 'Content-Type: application/json' -d '{"query":"x"}' | grep -i x-request-id
```

A store-unavailable `503` also puts it in the body as `request_id`, next to
`reason` — a header does not survive a copy-paste into a ticket, so the body
carries it too. Depth on all of this:
[COOKBOOK.md → What does an error look like, and how do I correlate it?](COOKBOOK.md)
and *Which 503s are worth retrying?*.
