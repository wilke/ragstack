# RAGStack user guide

*For people who want to ask questions of a corpus, build their own library of
papers, and understand what they are looking at. Operators provisioning a new
deployment want [Deployment](DEPLOYMENT.md) and the
[new-org cookbook](cookbook-new-org-ingest.md) instead; the copy-paste companion
to this guide is [cookbook-users.md](cookbook-users.md).*

Everything below is true of `main` as of 2026-08-24 and was checked against the
live deployments on `coconut`. Where the UI and the API answer the same question
differently, both answers are given.

---

## 1. Pick a deployment (what the API calls a "tenant")

RAGStack is not one service. Each **deployment** is a complete stack — its own
API process, its own registry of collections, its own credentials, and (for the
isolated ones) its own Qdrant and Elasticsearch. The front proxy on `coconut`
mounts every deployment under one hostname:

| Deployment | UI | API base | Who it is for |
|---|---|---|---|
| `dev` | `http://coconut.cels.anl.gov:9000/ragstack/dev/ui/` | `…/ragstack/dev/api` | Sandbox with its own stores (`oa-dev`, ~24k chunks). Break things here. |
| `demo` | `…/ragstack/demo/ui/` | `…/ragstack/demo/api` | Demonstrations over the `open-access` corpus (47.6M chunks). |
| `asm-next` | `…/ragstack/asm-next/ui/` | `…/ragstack/asm-next/api` | The ASM corpora (`ragstack_sfr_tok256` etc.) behind the current access-control code. |
| `lucid-next` | `…/ragstack/lucid-next/ui/` | `…/ragstack/lucid-next/api` | The Lucid corpus, likewise. |
| `asm`, `lucid` | `…/ragstack/asm/…`, `…/ragstack/lucid/…` | | The two **production doors**: older code, no collection ownership, reads allowed without a credential. Read-only at the gateway. |

`https://coconut.cels.anl.gov:9443/…` serves the same routes over TLS with a
self-signed certificate.

**The word "tenant".** On the wire, `tenant` is the data-isolation scope a
credential carries — the value you see in `GET /v1/stats/tenants` and stamped
on every chunk. In this operation a tenant is a whole deployment: `asm-next`
and `lucid-next` are different tenants, not different accounts on one server.
Reads always return the caller's own tenant **plus** the shared, world-readable
`public` tenant; writes touch only the caller's own.

**In the UI** the deployment is the "API backend" shown in the header and on
the *Account* page. Served through the gateway, the UI derives the right
preset (`Gateway (/ragstack/<name>/api)`) from its own URL, so you normally
never change it. Switching backends re-points every request and refetches what
is on screen; your stored credential is bound to the backend it was confirmed
for, so a token is never silently sent to a different deployment.

**With the API** the deployment is simply the base URL. Everything in this
guide uses `$BASE`:

```bash
export BASE=http://coconut.cels.anl.gov:9000/ragstack/dev/api
curl -s $BASE/health      # {"status":"ok"} — the only route that needs no credential
```

---

## 2. Authorize

Every `/v1/*` request carries **exactly one** credential. Two headers are
accepted; sending both is a `400`.

| Header | What it is | Tenant you become | Role |
|---|---|---|---|
| `X-API-Key: <key>` | A per-deployment key handed out by the operator ("for operators and scripts: a configured key, not a person") | the tenant that key is mapped to (e.g. `asm-ops`) | the role the key is mapped to (`user` or `admin`) |
| `Authorization: <BV-BRC token>` | Your own BV-BRC identity. The `Bearer ` prefix is optional — the raw token works | `bvbrc:<your login>` — a personal scope | `user`, unless an operator listed you in `ADMIN_SUBJECTS` or granted `admin` |

Which one you can use depends on the deployment: `dev` and `lucid-next` are
**bearer-only** (no API keys configured); `demo` and `asm-next` accept both.
All four verify BV-BRC tokens (`IDENTITY_PROVIDER=bvbrc`). The two production
doors (`asm`, `lucid`) predate identity support.

### Getting a BV-BRC token

```bash
p3-login <your BV-BRC username>       # writes ~/.patric_token
export TOKEN=$(cat ~/.patric_token)
curl -s $BASE/v1/stats/tenants -H "Authorization: $TOKEN"
```

A BV-BRC token has no audience and cannot be revoked before it expires, so it
is your whole BV-BRC session: paste it only into the deployment you mean, and
sign out of shared machines.

### In the UI

*Sign in* (header) offers three ways in, matching the table above:

- **BV-BRC username + password.** The browser posts the password straight to
  `user.patricbrc.org/authenticate` and keeps only the resulting token; the
  password never reaches RAGStack. This form is normally hidden on a plain
  `http://` page (an on-path attacker could rewrite it). The four demo
  deployments are built with `VITE_ALLOW_PASSWORD_OVER_HTTP=true`, so the form
  *is* shown there — under a red warning that says exactly why. Prefer the
  token paste on anything you don't control.
- **Paste a token** — the same `~/.patric_token` that `p3-login` writes.
- **API key** — for a configured key.

The credential lives in the browser's `localStorage`, as XSS-exposed as any
other; *Sign out* clears it.

### Who am I?

There is no `/v1/me`. The de-facto identity call is

```bash
curl -s $BASE/v1/stats/tenants -H "X-API-Key: $KEY"
# {"tenant":"demo-ops","role":"admin","readable":["demo-ops","public"],
#  "auth_enabled":true,"tenants":[{"tenant":"demo-ops","own":true,
#  "collections":[{"collection":"open-access","vector_count":47625155,...}]}]}
```

`tenant` is the scope you write into, `readable` is what you read from, `role`
decides the admin routes. The UI shows the same on the *Account* page and in
the header's user menu.

### What each status code means here

| Status | Meaning |
|---|---|
| `401` | Unknown key, or an invalid/expired token. Also what you get when a deployment has no identity provider and you sent a token. |
| `403` | Authenticated, but not allowed: an admin-only field, or writing a collection you can read but do not own. |
| `404` | Unknown collection **or one you may not read** — deliberately indistinguishable, so private collections cannot be probed. |
| `413` | Your JSON body is over the deployment's size cap (creating/ingesting/sharing), or an upload breaks a bound: a file over `max_document_bytes`, more than `max_upload_files` files, or files totalling more than `max_upload_bytes_per_request`. |
| `411` | Your upload has no `Content-Length` (chunked transfer). Send it with a known length. |
| `415` | An uploaded file's content type is not accepted (PDF, plain text, Markdown, JATS XML by default), or a file sent as a PDF does not start with `%PDF`. |
| `422` | Request validation failed, or you're over a bound like `top_k`, `GET /v1/chunks` `ids`, or a list `limit`. |
| `429` | You've hit the per-hour rate limit on this write endpoint (issue #87) — see below — or, on upload, an ingest job of yours is still running: poll it and retry after `Retry-After`. |
| `503` | A backing store did not answer (authorization store, or — since #346 — a Qdrant search that exceeded `QDRANT_TIMEOUT`). Fail-closed, never a silent allow. Retry after the `Retry-After` header. |

**Rate limits**: creating collections, ingesting documents (path or upload,
shared budget) and granting shares are each capped per hour, per caller — a
`429` carries a `Retry-After` header telling you how long to wait. The limit is
enforced **per API process**, so a multi-replica deployment's effective ceiling
is higher than the configured number, not exactly it. A rejected request still
usually spends a token toward that hour (a validation error, an authorization
denial, a bad upload) — only an unauthenticated call and an oversized body
don't — so retrying a broken request in a loop burns your own budget rather
than getting free attempts.

---

## 3. Create a collection

A **collection** is one indexed corpus: a registry entry that binds an embedding
model (and its dimension) to a chunking strategy and one physical store — a
Qdrant collection plus its Elasticsearch index. That binding is fixed when it
is built; a different model or chunker is a *different* collection.

### With the API

```bash
curl -s -X POST $BASE/v1/collections \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"id": "my-papers", "label": "My papers"}'
# 201 {"id":"my-papers","label":"My papers","model":"…","dim":4096,"chunk_method":"fixed_token",...}
```

- **Any principal** can create with the server-default build spec (model +
  chunker are resolved to concrete values at create time, so a later change of
  the defaults never re-identifies your collection). Supplying `embedding` or
  `chunk` is an **admin-only** override (`403` otherwise).
- **Pass an `id` to name a library.** The id is folded into the physical store
  name, so two libraries with the same build spec each get their own store.
  **Omit `id` for a corpus:** id and store name are then content-addressed over
  (model, dim, chunk), so re-creating the same spec maps back to the same store
  (idempotent) and the registry answers `409`.
- The new collection is **private to you** — owned by its creator, unreadable
  by anyone else until shared. `409` on an id collision; `403` when the
  deployment's `MAX_COLLECTIONS` cap (default 100) is reached — that cap applies
  to admins too — or when the deployment has set
  `ALLOW_USER_COLLECTION_CREATE=false` (creation is then admin-only). You are
  also capped at `MAX_COLLECTIONS_PER_OWNER` collections owned at once (default
  **5**) — `409` with the count and the limit if you're already there; admins
  are exempt from this one. Transferring ownership (`POST
  /v1/collections/{id}/owner`) is checked the same way on the *recipient's*
  side, so you can't dodge your own quota by handing a collection off and
  creating another.

Then put documents in it. PDFs go through the multipart upload; it answers
`202` with a job you poll:

```bash
curl -s -X POST $BASE/v1/ingest/upload -H "X-API-Key: $KEY" \
  -F collection=my-papers -F files=@paper1.pdf -F files=@paper2.pdf
# {"job_id":"…","status":"accepted"}
curl -s $BASE/v1/ingest/<job_id> -H "X-API-Key: $KEY"       # accepted → running → completed | failed
curl -s $BASE/v1/collections -H "X-API-Key: $KEY"           # count climbs as chunks land
```

Ingest into a named collection is owner-or-admin. The upload accepts PDF,
plain text, Markdown and JATS XML (the deployment's `UPLOAD_CONTENT_TYPES`);
anything else — or a "PDF" without a `%PDF` header — is `415`. Per request:
at most `MAX_UPLOAD_FILES` (50) files and `MAX_UPLOAD_BYTES_PER_REQUEST`
(500 MB) across them, each file at most `MAX_DOCUMENT_BYTES` (50 MB) — over any
of these is `413`, decided from the declared sizes before anything is stored
(a request whose `Content-Length` is over the per-request cap is refused before
its body is read; a request with no `Content-Length` is `411`, so send uploads
with a known length, not chunked). The deployment's gateway enforces a body cap
of about the same size — that, not the API, is what stops a client that lies
about its length. You get **one ingest job at a time**: while a job of yours
is still `accepted`/`running`, a new upload is `429` with a `Retry-After` —
poll the job and resubmit once it is `completed` or `failed` (a job that has
not moved for 6 hours stops counting). A scanned (image-only) PDF is accepted
but its item fails with `no extractable text (scanned PDF?)`; there is no OCR
yet. A JATS XML file is accepted but there is no loader for it yet — its item
fails with `no loader for .xml` rather than vanishing. Large pre-extracted
corpora do not go through the API at all — see the
[new-org cookbook](cookbook-new-org-ingest.md).

**Share it.** `POST /v1/collections/{id}/shares` grants `read` to a BV-BRC user,
a group, or `public`; `DELETE …/shares/{share_id}` revokes;
`POST …/owner` hands the collection to someone else (you lose access — the
response says so). `DELETE /v1/collections/{id}` is owner-or-admin and also
revokes every share. Full semantics: [API.md → Collection shares](API.md#collection-shares).

### In the UI

*Collections* tab → **New collection**. You give it a name (that is both its id
and its label, so it must be unique on that deployment) and leave the strategy
at *Server default*; *Choose a strategy* is the admin-only override. The same
tab uploads PDFs into the selected collection and shows *Ingest progress*
(total / completed / failed / pending) while the job runs.

---

## 4. Query a collection

Two endpoints, same retrieval:

- **`POST /v1/query`** — the full pipeline: optional query rewriting, hybrid
  retrieval, RRF fusion, optional cross-encoder rerank, then a grounded answer.
  Returns `{answer, sources[], rewritten_queries[]}`. With no LLM configured
  the answer is a placeholder (`[LLM not configured]`) and the sources are still
  real; an LLM outage degrades the same way rather than failing.
- **`POST /v1/retrieve`** — the same retrieval, no generation. Returns
  `{sources[]}`. Use this for anything programmatic.

```bash
curl -s -X POST $BASE/v1/query -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"query": "how do efflux pumps confer multidrug resistance?",
       "collection": "open-access", "top_k": 5}'
```

The request fields that matter to a user:

| Field | Default | What it does |
|---|---|---|
| `collection` | the deployment's default | Which registry collection to search. Unknown *or unreadable* → `404`. `GET /v1/collections` lists what you can see and which one is `default`. |
| `top_k` | 5 | How many sources to return. |
| `retrieval_mode` | `hybrid` | `hybrid` (dense + BM25, fused), `vector` (dense only — meaning-similar text without shared words), `bm25` (keyword only — fast, rewards exact terms). |
| `rerank` | `null` | `true`/`false` force the cross-encoder on/off for this request; `null` keeps the deployment's setting. `rerank_candidates` sets the pool it re-scores. |
| `rewrite_strategies` | `["passthrough"]` | Add `multiquery` or `hyde` to let the LLM expand the question first (ignored without an LLM). |
| `filters` | `{}` | Metadata equality filters, ANDed — e.g. `{"journal": "mBio"}`. Fields are whatever the ingester stamped; see the metadata on any source. |
| `use_graph` | `true` | Include the knowledge-graph leg where a deployment has one (most don't — `graph_backend` is `disabled`). |
| `llm`, `reranker` | `null` | A registered model id from `GET /v1/models/available`, for this request only. |

Every source is `{doc_id, chunk_id, content, score, metadata}`. On the
scholarly corpora the metadata carries `title`, `authors`, `journal`, `doi`,
`pmcid`, `section_title`, `chunk_index`, `source_url`, and the neighbour ids
described next.

**In the UI**, *Explore* is `/v1/query` with the answer's `[n]` citations turned
into chips; *Compare* runs the same question through several lanes (each a
collection with its own levers — mode, rewrite, rerank, `top_k`, model
overrides, even its own key) side by side; *Evidence* is the claim-by-claim view
of one run.

---

## 5. Get the next (and previous) chunks

Retrieval returns one chunk per hit. The chunk before and after it in the same
document are one call away, because the ingester stamps every chunk with its
neighbours:

```json
"metadata": {"chunk_index": 3,
             "prev_chunk_id": "c6304b79-…", "next_chunk_id": "1656f49a-…", ...}
```

### With the API

```bash
curl -s "$BASE/v1/chunks?collection=open-access&ids=c6304b79-…,1656f49a-…" -H "X-API-Key: $KEY"
# {"chunks":[{"doc_id":"…","chunk_id":"c6304b79-…","content":"…","metadata":{…}},
#            {"doc_id":"…","chunk_id":"1656f49a-…","content":"…","metadata":{…}}]}
```

- `ids` is comma-separated, **up to 200 ids; 422 above** per call (issue #87);
  order follows the request.
- Ids you may not read, or that do not exist, are silently omitted — the
  response is only ever what is visible to you.
- At a document's edges the neighbour id is absent: the first chunk has no
  `prev_chunk_id`, the last no `next_chunk_id`. (On corpora bulk-loaded before
  this was tightened the field may carry the literal string `"None"`; treat it
  the same way.)
- Keep walking: each returned chunk carries its own `prev_chunk_id` /
  `next_chunk_id`, so a loop of one call per step pages through the whole
  document. There is no "give me chunks 3–7" call; the ids are the cursor.

### Server-side: `context_window`

If you want the neighbours *with* the hits instead of a second call, ask
`/v1/query` or `/v1/retrieve` for them:

```bash
curl -s "$BASE/v1/retrieve" -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"query": "how does BM25 work?", "top_k": 5, "context_window": 1}'
# {"sources":[{"doc_id":"…","chunk_id":"1656f49a-…","content":"…","score":0.0164,
#              "metadata":{…},
#              "context":[{"chunk_id":"c6304b79-…","position":-1,"content":"…"},
#                         {"chunk_id":"9b2d01e7-…","position":1,"content":"…"}]}, …]}
```

- `context_window` is how many chunks to walk **each way** from every hit:
  `1` = the previous and the next chunk, up to `3`; above that is a `422`.
  The default `0` changes nothing — no `context` key, byte-identical to before.
- `context` is ordered by `position` (negative = before the hit, positive =
  after) and carries **no score**: the score belongs to the matched chunk.
- Ranking is untouched. Neighbours are looked up *after* fusion, rerank and
  the `top_k` cut, and are attached to the hit — never merged into the
  ranked list. What you would get with `context_window: 0` is exactly the
  `sources` you get with `3`, minus the `context` keys.
- Same visibility as the hit: neighbours are fetched with the request's
  `filters` and your tenant scope, so a chunk you could not read (or one your
  filter excludes) is simply not there — and the walk stops at it.
- Document edges behave as above: a first chunk has nothing before it; a hit
  with no visible neighbour at all has no `context` key.
- A neighbour that is itself one of the hits is not repeated in `context`
  (its content is already in `sources`), but the walk continues through it.
- On `/v1/query` the answer is generated from each hit *plus* its context
  (concatenated in document order, delimited as `(context before)` /
  `(passage)` / `(context after)`), so the model sees the surrounding text —
  the citations `[n]` still refer to the hit. If the prompt budget is tight,
  the hit itself is always kept whole; only its context is trimmed.
- Cost: one batched store lookup per hop for the whole request (so one for
  `context_window: 1`, at most three), whatever `top_k` is.

### In the UI

Open a source in *Evidence* (or click a citation chip). The source card shows
the matched passage highlighted and, in its header, **‹ prev / next ›** which
walk the document one chunk at a time using exactly this endpoint; *back to
match* returns to the retrieved chunk. Walked neighbours show no score — the
score belongs to the matched chunk only.

---

## 6. See the configuration

Three layers, from the one you can read as a user to the one that is the truth:

1. **`GET /v1/config`** — the effective, *allowlisted* runtime config of the
   deployment you are talking to: backends, store URLs and index names,
   embedding model and dimension, chunking, `top_k`, rerank settings, ingest
   limits (including the per-collection chunk cap), the
   `ALLOW_USER_COLLECTION_CREATE` capability switch,
   `MAX_COLLECTIONS_PER_OWNER`, log level — 38 keys, **no secrets** (keys,
   passwords, DSNs and the key→tenant maps are never returned). Requires the
   `admin` role; the UI's *Ops* page renders it when it is readable.

   ```bash
   curl -s $BASE/v1/config -H "X-API-Key: $ADMIN_KEY" | jq
   ```

2. **The environment-variable table in [API.md → Configuration](API.md#configuration-server)**
   — the variables an operator is expected to set, with the rules that bite
   (`INGEST_ROOT`, `MAX_COLLECTIONS`, `ADMIN_SUBJECTS`, the `*_STORE_*` trio,
   `QDRANT_TIMEOUT`, …).

3. **`python/ragstack/config.py`** — every option there is. `Settings` is a
   pydantic model with ~120 fields; **each field is an environment variable of
   the same name in upper case** (`chunk_size` → `CHUNK_SIZE`), the default is
   the value in the file, and the comment above each field says what it does.
   That file is the reference; the table above is the curated subset.

Per-request overrides (`top_k`, `retrieval_mode`, `rerank`, `llm`, `reranker`,
`rewrite_strategies`) are not configuration — they are request fields, listed
in §4, and never change the deployment.

**Operators:** a deployment's actual values live in
`/rag/data/tenants/<name>/config/tenant.env` (source with `set -a`); the
[ops page](https://claude.ai/code/artifact/d4e3f303-62db-4f6d-9d1d-c7d7e57061a4)
maps every running service.

---

## Where to go next

- [cookbook-users.md](cookbook-users.md) — every step above as copy-paste
  recipes, plus troubleshooting.
- [API.md](API.md) — the reference: every endpoint, the ownership and sharing
  model, error semantics.
- [GLOSSARY.md](GLOSSARY.md) and the UI's own glossary (the ⓘ tips) — the words:
  tenant, collection, library, share, lane, RRF, rerank.
- [demo-quickstart.md](demo-quickstart.md) — stand up your *own* API on a
  laptop and point Claude at it over MCP.
