# RAGStack API Reference

HTTP API for the RAGStack Retrieval-Augmented Generation platform. The surface is
defined contract-first in [`contracts/openapi.yaml`](../contracts/openapi.yaml)
(OpenAPI 3.1) with JSON Schemas under `contracts/schemas/`; that contract is
authoritative and both implementations conform to it.

- **Python** (FastAPI) — default port **8000**
- **Go** (Chi) — default port **8080**

Interactive docs are served by the Python app at `/docs` (Swagger UI) and `/redoc`.

> **Set `BASE` before running anything below.** The examples use `"$BASE"`, deliberately
> without a default, because the obvious default is dangerous: on the deployment host
> `http://localhost:8000` is the **legacy production API**, and the examples in this file
> create collections, grant shares, transfer ownership and ingest documents. A copy-pasted
> command with no `BASE` set is a production write.
>
> ```bash
> export BASE=http://localhost:8000                      # a local dev server you started
> export BASE=https://<host>:9000/ragstack/<tenant>/api   # through the gateway
> ```
>
> Behind the gateway the prefix (`/ragstack/<tenant>/api`) is stripped before the app sees
> the request; the app emits correct absolute URLs for `/docs` and `/openapi.json` from
> `X-Forwarded-Prefix`, or from `ROOT_PATH` when that is pinned (#332).

---

## Authentication & tenancy

Auth is an API key passed in the **`X-API-Key`** header. The key maps to a
**tenant** server-side (via `API_KEY_TENANTS`); the tenant is **never** taken from
the request body, so a client cannot widen its own scope.

With an identity provider enabled (`IDENTITY_PROVIDER=bvbrc|oidc`) an end user
may instead present an **`Authorization`** credential; the tenant is then
`{issuer}:{subject}`. Presenting both in one request is a **400**. The two are
interchangeable everywhere, **including `/v1/admin/*`**: the role gate tests the
authenticated principal's role, not which header produced it, so a bearer
identity that an admin source names reaches every admin route (see
[PATCH /v1/admin/users/{subject}/role](#patch-v1adminuserssubjectrole--bearer-admins)).

- **Reads** (`/v1/query`, `/v1/retrieve`, `/v1/documents`, graph) return the
  caller's own tenant **plus** the shared world-readable **`public`** tenant.
- **Writes/deletes** (`/v1/ingest`, `DELETE /v1/documents/...`) affect only the
  caller's own tenant.

**Collection ownership** (ADR-0003 §2, ADR-0004). On top of the per-chunk tenant
filter above, every collection carries an owner and is gated at resolution by the
one authorization seam (`resolve_access`):

- A **new** collection (`POST /v1/collections`) is **private to its creator** —
  the creator is its owner; nobody else can read it until it is shared. Sharing a
  `read` grant (or a `public` grant) re-opens it.
- **Pre-existing (legacy)** collections — those whose durable spec records **no
  creator** — are **backfilled** at startup as owned by `ACL_BACKFILL_OWNER`
  (default `legacy:admin`) **plus `read` to `public`**, so they stay
  world-readable exactly as before ownership existed. A collection whose spec
  *does* record a creator is never published: if its owner row is missing (a
  crash, or a restart of a non-durable ACL backend) the backfill **repairs** it
  to the recorded creator and it stays private. The backfill is idempotent (runs
  every boot, each ACL row keyed on its own history — a revoked row is never
  resurrected, so un-publishing sticks) and ownership is reassignable.
- **Reads** need owner, an active read grant, or the `public` grant; a denied read
  is a **404** (indistinguishable from an unknown id, so a private collection's
  existence isn't leaked).
- **Ingest into a named (non-default) collection** is **owner-or-admin** (write
  shares are deferred): a caller who can *read* the collection but not write it
  gets a **403**; one who cannot read it gets the same **404** as an unknown id —
  a 403 there would make the write endpoints an existence oracle for private
  collections.
- **An omitted `collection` is resolved per caller, not per registry** (#422,
  #453). On ingest (`POST /v1/ingest`, `POST /v1/ingest/upload`) the target is
  the caller's **writable** default: their [visible listing](#get-v1collections)
  narrowed to what they may write, then the same pick — the registry pointer
  when it survives, else the first entry in listing order. Two refusals, and
  **neither names a collection id** (naming one would make the refusal an
  existence oracle for a collection the caller was never shown):
  - **403** `no collection accepts your uploads: name a collection you own
    explicitly in 'collection', or create your own (POST /v1/collections)` —
    the caller can read something but write nothing.
  - **404** `no collection is accessible to this caller` — the caller can read
    nothing at all. Byte-identical to the read paths' refusal, and the same
    state in which `GET /v1/collections` reports `default: ""` with an empty
    `collections`.

  An **explicitly named** id never enters that picker: it stays
  403-if-readable / 404-if-not, so no request is ever silently rerouted from the
  collection the caller chose to one the server did.
- **The read exemption keys on the shared-surface flag, never on the pointer.**
  Both ingest branches and `DELETE /v1/documents/{doc_id}` authorize with
  `read` when the resolved entry **is the legacy shared surface**
  (`is_shared_surface`) and `write` otherwise — because there the per-chunk
  tenant stamp, not collection ownership, isolates writers (each write/delete
  only ever touches the caller's own tenant's chunks). It is deliberately *not*
  keyed on "is this what `default` points at": aim the pointer at a genuinely
  owned collection (#276) and a pointer-keyed exemption would let any reader of
  it ingest into somebody else's corpus just by omitting `collection`.
- **`DELETE /v1/collections/{id}`** also **revokes every ACL row** of the
  collection (softly — audit history survives), so a later collection reusing
  the same id never inherits the deleted one's owner row or `public` grant.
- **`DELETE /v1/collections/{id}`** is **owner-or-admin** — no longer admin-only:
  a user manages its own private collections. `admin` bypasses every check (a
  named, logged branch), for purge/migration/support.
- **Ownership is transferable, not grantable**: `POST /v1/collections/{id}/owner`
  atomically revokes the current owner row and grants ownership to another user
  (exactly one active owner row per collection). The outgoing owner **loses
  access** — their row is soft-revoked and no replacement `read` grant is minted;
  the response says so explicitly. See
  [POST /v1/collections/{id}/owner](#post-v1collectionsidowner).
- When authentication is unconfigured (keyless dev), collection-ownership
  enforcement is a no-op — the open dev path, exactly as tenant auth is; production
  (`REQUIRE_DURABLE_BACKENDS`) forbids keyless and requires a durable ACL store
  (`USER_STORE_BACKEND` ≠ `memory`).
- An authorization-store outage is a **503** (fail closed) — never a silent allow.
- A key absent from the map resolves to the `default` tenant. If no API keys are
  configured at all (dev mode), requests are unauthenticated and use `default`.
- A request with an unknown key returns **401**.

```bash
curl -s "$BASE"/v1/query \
  -H 'X-API-Key: <your-key>' -H 'Content-Type: application/json' \
  -d '{"query": "..."}'
```

`/health` is open (no key required). All `/v1/*` routes require the header when
keys are configured.

---

## Endpoints

All **49** operations in the contract. "Gate" is the authorization the route
applies on top of authentication; *authenticated* means any valid credential of
either kind. Rows without a link are covered in [Operations &
admin](#operations--admin).

| Method | Path | Gate | Summary |
|---|---|---|---|
| GET | [`/health`](#get-health) | **open** — no credential | Liveness check |
| POST | [`/v1/query`](#post-v1query) | authenticated | Full RAG: rewrite → retrieve → rerank → generate |
| POST | [`/v1/retrieve`](#post-v1retrieve) | authenticated | Retrieve chunks only (no answer) |
| GET | [`/v1/chunks`](#get-v1chunks) | authenticated | Fetch chunks by id (client-side context expansion) |
| GET | [`/v1/collections`](#get-v1collections) | authenticated (**owner-filtered**) | List readable collections + the caller's `default` |
| POST | [`/v1/collections`](#post-v1collections) | authenticated; **admin** for `embedding`/`chunk`, or if `ALLOW_USER_COLLECTION_CREATE=false` | Create a collection |
| DELETE | [`/v1/collections/{id}`](#delete-v1collectionsid) | owner-or-admin | Unregister a collection, optionally purging its data |
| POST | [`/v1/collections/{id}/restore`](#post-v1collectionsidrestore) | owner-or-admin, **bearer only** | Restore a dormant collection from its Workspace archive |
| POST | [`/v1/collections/{id}/graph`](#post-v1collectionsidgraph) | owner-or-admin, **bearer only** | Extract the knowledge graph of one archived version |
| GET | [`/v1/collections/{id}/shares`](#collection-shares) | owner-or-admin | List a collection's shares + owner |
| POST | [`/v1/collections/{id}/shares`](#collection-shares) | owner-or-admin | Grant a read share (or publish via `@public`) |
| DELETE | [`/v1/collections/{id}/shares/{share_id}`](#collection-shares) | owner-or-admin | Revoke a share (un-publish) |
| POST | [`/v1/collections/{id}/owner`](#post-v1collectionsidowner) | current-owner-or-admin | Transfer ownership to another user |
| POST | [`/v1/groups`](#groups) | authenticated (owns what it creates) | Create a group |
| GET | [`/v1/groups`](#groups) | authenticated | List the groups the caller owns or belongs to |
| GET | [`/v1/groups/{id}`](#groups) | owner-or-member (non-member 404) | Group details + members |
| DELETE | [`/v1/groups/{id}`](#groups) | owner-or-admin (`public` not deletable) | Delete a group |
| POST | [`/v1/groups/{id}/members`](#groups) | owner-or-admin | Add a member |
| DELETE | [`/v1/groups/{id}/members/{subject}`](#groups) | owner-or-admin | Remove a member |
| POST | [`/v1/ingest`](#post-v1ingest) | authenticated; owner-or-admin on a named collection; **bearer** under `INGEST_BACKEND=gowe` | Ingest a path or Workspace reference (async job) |
| POST | [`/v1/ingest/upload`](#post-v1ingestupload) | same as `/v1/ingest` | Upload files for ingestion (multipart, async job) |
| GET | [`/v1/ingest/{job_id}`](#get-v1ingestjob_id) | authenticated | Poll ingest job status |
| GET | [`/v1/documents`](#get-v1documents--delete-v1documentsdoc_id) | authenticated | List indexed documents (paginated) |
| DELETE | [`/v1/documents/{doc_id}`](#get-v1documents--delete-v1documentsdoc_id) | `write` on the resolved collection — `read` on the legacy shared surface | Delete a document + its chunks |
| GET | [`/v1/graph/entities`](#get-v1graphentities--get-v1graphneighborsentity--get-v1graphstats) | authenticated | List knowledge-graph entities (`?limit=`) |
| GET | [`/v1/graph/neighbors/{entity}`](#get-v1graphentities--get-v1graphneighborsentity--get-v1graphstats) | authenticated | Entity neighborhood triples (`?depth=`, ≤ 5) |
| GET | [`/v1/graph/stats`](#get-v1graphentities--get-v1graphneighborsentity--get-v1graphstats) | authenticated | KG entity/relationship counts (tenant-scoped) |
| GET | [`/v1/models/available`](#get-v1modelsavailable) | authenticated | Models assignable per-request as `llm` / `reranker` |
| GET | [`/v1/stats/stores`](#operations--admin) | authenticated (scoped to readable tenants) | Per-store chunk/document counts |
| GET | [`/v1/stats/tenants`](#get-v1statstenants) | authenticated (`policy` admin-only) | Who am I: identity, reach, tenant × collection counts |
| GET | [`/v1/stats/models`](#operations--admin) | **admin** | Model endpoint reachability, latency, pool health |
| POST | [`/v1/stats/models/benchmark`](#operations--admin) | **admin** | Short, bounded embedding/LLM throughput probe |
| GET | [`/v1/config`](#operations--admin) | **admin** | Allowlisted effective configuration (no secrets) |
| GET | [`/v1/health/deep`](#get-v1healthdeep) | **admin** | Per-dependency health probe with latencies |
| GET | [`/v1/jobs`](#get-v1jobs) | **admin** | Recent ingest jobs (`source` can carry a path) |
| GET | [`/v1/admin/log-level`](#get--put--delete-v1adminlog-level) | **admin** | The log level in effect in this process |
| PUT | [`/v1/admin/log-level`](#get--put--delete-v1adminlog-level) | **admin** | Change it live, optionally with a TTL auto-revert |
| DELETE | [`/v1/admin/log-level`](#get--put--delete-v1adminlog-level) | **admin** | Drop the override, return to the configured level |
| GET | [`/v1/admin/models/registry`](#operations--admin) | **admin** | Registered models + hot-swappable assignments |
| POST | [`/v1/admin/models/registry`](#operations--admin) | **admin** | Register a model (SSRF-checked `base_urls`) |
| PUT | [`/v1/admin/models/registry/{model_id}`](#operations--admin) | **admin** | Replace a registered model |
| DELETE | [`/v1/admin/models/registry/{model_id}`](#operations--admin) | **admin** | Remove a registered model |
| PATCH | [`/v1/admin/config/assignments`](#operations--admin) | **admin** | Assign models to hot-swappable tasks, applied live |
| POST | [`/v1/admin/collections/evict`](#operations--admin) | **admin** | Evict k LRU archived collections (`?dry_run=`) |
| POST | [`/v1/admin/service-accounts`](#service-accounts) | **admin** | Register a machine identity |
| GET | [`/v1/admin/service-accounts`](#service-accounts) | **admin** | List registered service accounts |
| POST | [`/v1/admin/service-accounts/{subject}/disable`](#service-accounts) | **admin** | Soft-revoke an account's key |
| POST | [`/v1/admin/service-accounts/{subject}/enable`](#service-accounts) | **admin** | Re-enable a disabled account |
| PATCH | [`/v1/admin/users/{subject}/role`](#patch-v1adminuserssubjectrole--bearer-admins) | **admin** | Grant/revoke the admin role for a federated user |

Everything under `/v1/admin/*`, plus `/v1/config`, `/v1/health/deep`,
`/v1/jobs`, `/v1/stats/models` and the benchmark, tests the **authenticated
principal's role** — not which header carried it, so a bearer identity an admin
source names reaches every one of them. The Go scaffold implements only
`/health`, query/retrieve, ingest (+upload, +status), documents (list/delete),
collections (list/create/delete), chunks, `models/available`, the model-registry
listing and the two graph reads; everything else is **Python only**, and the Go
side has no `X-API-Key` or bearer identity path at all.

### GET /health

```bash
curl -s "$BASE"/health
# {"status": "ok"}
```

### POST /v1/query

Full pipeline: optionally expand the query (rewrite strategies), hybrid-retrieve
per variant, RRF-fuse, optionally cross-encoder rerank, then generate a grounded
answer. When no LLM is configured the `answer` is a retrieval-only placeholder
(sources are still returned). Generation/rewrite/rerank failures **degrade
gracefully** (HTTP 200 with sources) rather than erroring.

**Request** (`QueryRequest`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | **required** |
| `top_k` | int | 5 | results to return |
| `rewrite_strategies` | string[] | `["passthrough"]` | also `multiquery`, `hyde` (LLM-backed; ignored if no LLM) |
| `filters` | object | `{}` | metadata equality filters (ANDed); see [Metadata & filtering](#metadata--filtering) |
| `use_graph` | bool | true | include the knowledge-graph retrieval leg |
| `stream` | bool | false | **accepted and ignored** — no streaming is implemented and no response differs (#458). Do not build on it |
| `collection` | string \| null | null | registry collection id to query; null (or the reserved pointer name `"default"`, which #276 makes equivalent to omitting) = **[the caller's default collection](#get-v1collections)** — the id `GET /v1/collections` advertises as `default`, not the global registry pointer. Unknown **or unreadable** → `404`; no readable collection at all → `404` naming no id |
| `collections` | string[] \| null | null | [multi-collection fused retrieval](#multi-collection-retrieval-collections) (issue #253): 1–5 unique registry ids to query together; mutually exclusive with `collection` (both, more than 5, duplicates or `[]` → `422`). Every id is resolved and read-authorized before any retrieval runs: one unknown/unreadable → `404`, one dormant → `503` + `Retry-After`, for the whole request |
| `retrieval_mode` | `hybrid` \| `vector` \| `bm25` | `hybrid` | which retrieval legs run: dense + BM25 fused, dense only, or keyword only. Graph leg is orthogonal (`use_graph`) |
| `rerank` | bool \| null | null | force the cross-encoder on/off for this request; null keeps the server setting (rerank iff a reranker is configured) |
| `rerank_candidates` | int \| null | null | candidate-pool depth fed to the reranker; null = `max(top_k, RERANK_CANDIDATES)`. With `collections` each leg fetches this many and the fused union is cut to it before the single rerank — per-collection recall into the pool is ~`rerank_candidates / N` under RRF interleaving; raise it for more |
| `context_window` | int (0–3) | 0 | server-side [context expansion](#context-expansion-context_window): walk each returned source's `prev_chunk_id` / `next_chunk_id` this many hops each way and attach the neighbours as the source's `context`. `0` = off (response unchanged); above `3` → `422` |
| `llm` | string \| null | null | registered model id to generate with, this request only (`GET /v1/models/available`); unknown → 404, wrong task → 400 |
| `reranker` | string \| null | null | registered model id to rerank with, this request only |

**Response** (`QueryResponse`): `{ answer, sources[], rewritten_queries[] }`. Each
source is `{ doc_id, chunk_id, content, score, metadata, context?, collection? }`; on API-ingested and
current bulk-loaded corpora `metadata` carries `chunk_index`, `prev_chunk_id` and
`next_chunk_id` for client-side [context expansion](#get-v1chunks). `context` is
present only when the request set `context_window > 0` and at least one neighbour
is visible — see [Context expansion](#context-expansion-context_window).
`collection` is present only on a `collections` request — the registry id the
source came from.

```bash
curl -s "$BASE"/v1/query \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"query": "how do viruses evade innate immunity?", "top_k": 5}'
```

### POST /v1/retrieve

Same retrieval (hybrid + optional rerank) but no answer generation.

**Request** (`RetrieveRequest`): `query` (required), `top_k` (5), `filters` (`{}`),
`use_graph` (true), plus the same `collection`, `collections`, `retrieval_mode`, `rerank`,
`rerank_candidates`, `context_window` and `reranker` fields as `/v1/query`. **Response**
(`RetrieveResponse`): `{ sources[] }`.

```bash
curl -s "$BASE"/v1/retrieve \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"query": "mechanisms of antibiotic resistance", "top_k": 10,
       "filters": {"doc_type": "article"}}'
```

### Multi-collection retrieval (`collections`)

`collections: [id, …]` on `/v1/query` and `/v1/retrieve` (issue #253) searches
several registry collections in one request — the open-access corpus next to
the main one, or "everything I can see". Semantics:

- **Resolution first.** Every id goes through the registry, the tenant
  allowlist and the read seam (`enforce_access(read)`) in request order
  *before any retrieval runs*. The first refusal answers the whole request —
  no partial answers: unknown or unreadable → `404` (leak-safe, exactly as the
  singular form); a `dormant`/`restoring` member → `503` + `Retry-After` (the
  dormant member's restore is submitted as the caller, as a single-collection
  read would; nothing else runs — a request with two dormant members restores
  them one retry at a time); a `lost` member → `409`. Two ids resolving to the
  same collection → `422`.
- **One leg per collection, never one many-valued filter.** Each member is
  retrieved by its own already-collection-scoped retriever, at the same
  per-leg candidate depth the singular path uses, all legs concurrently. The
  legs are fused with RRF (ties resolve in request order), the union is cut
  to `rerank_candidates` and **reranked once**, then cut to `top_k`. A many-valued store filter (`collection IN […]`) is never used on
  the vector/BM25 stores (#199, #354); the knowledge-graph leg, where one is
  wired, is one entity match across the members (exact on Neo4j) followed by
  one neighbourhood query per matched entity (at most `graph_query_entity_max`,
  #349), with one pseudo-chunk budget shared across them, not one per member.
- **Provenance.** Every source carries `collection` — the registry id it came
  from. A document present in two collections appears once per collection,
  each copy stamped with its own id (they share a `chunk_id`, not a
  collection). `context_window` neighbours are fetched per source from *its*
  collection with that collection's scope.
- **Cap.** 1–5 unique ids (`maxItems: 5`, the per-owner collection quota);
  mutually exclusive with `collection`. `collections: ["x"]` is byte-for-byte
  `collection: "x"` plus the stamp.

```bash
curl -s "$BASE"/v1/retrieve \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"query": "efflux pumps and multidrug resistance", "top_k": 5,
       "collections": ["open-access", "my-notes"]}'
# {"sources":[{"doc_id":"…","chunk_id":"…","content":"…","score":0.0328,
#              "metadata":{…},"collection":"open-access"}, …]}
```

### Context expansion (`context_window`)

Server-side neighbour expansion on `/v1/query` and `/v1/retrieve` (issue #322).
With `context_window: n` (`1`–`3`; `0` = off, the default; above `3` → `422`),
each returned source is decorated with the chunks up to `n` hops before and
after it in its document, following the `prev_chunk_id` / `next_chunk_id`
links the ingester stamps:

```json
{"doc_id": "…", "chunk_id": "…", "content": "…", "score": 0.0164, "metadata": {…},
 "context": [{"chunk_id": "…", "position": -1, "content": "…"},
             {"chunk_id": "…", "position":  1, "content": "…"}]}
```

- `context` is ordered by `position` (negative = before, positive = after);
  neighbours carry no score.
- **Ranking unchanged.** Expansion runs after fusion, rerank and the `top_k`
  cut and only decorates the sources; neighbours are never merged into the
  scored list. `sources` with `context_window: 0` and with `3` differ only by
  the `context` keys.
- **Scoped like the hit.** Neighbours are fetched with the request's `filters`
  plus the caller's tenant scope (the same predicate retrieval used), so an
  unreadable or filtered-out neighbour is omitted and the walk stops there.
  Document edges end the walk the same way; a source with no visible
  neighbour has no `context` key (never an empty list).
- A neighbour that is itself a returned source is not repeated in `context`;
  the walk continues through it.
- On `/v1/query`, generation sees each source's text with its context
  concatenated in document order under `(context before)` / `(passage)` /
  `(context after)` delimiters; citations still number the sources. The
  prompt's character budget scales with the window, so context never reduces
  how many sources reach the model; when a block still has to shrink, the
  passage is kept whole and its context is trimmed (before-context from the
  left, after-context from the right, marked with `…`).
- One batched store lookup per hop for the whole request (one for
  `context_window: 1`, at most three), independent of `top_k`.
- A `filters` key `GET /v1/chunks` refuses (`doc_id`, `chunk_id`, `content`,
  `start_char`, `end_char`, `library_id`) is a `400` here too when
  `context_window > 0`.

### GET /v1/chunks

Fetch chunks **by id** from a collection — client-side context expansion around
a hit (the server-side alternative is [`context_window`](#context-expansion-context_window)).
`ids` is a comma-separated list, **up to 200 ids; 422 above** (issue #87 —
`max_chunk_ids`); typically the
`prev_chunk_id` / `next_chunk_id` a source's metadata carries, so a client can
page through the document one chunk at a time (each returned chunk carries its
own neighbour ids — the ids are the cursor). An omitted `collection` resolves to
**[the caller's default collection](#get-v1collections)** — the same rule
`/v1/query` and `/v1/retrieve` use, evaluated through the same function — and a
caller who can read none gets `404` naming no id. Tenant-scoped like every read (own + `public`): ids that do
not exist or that the caller may not read are **silently omitted**; order
follows the request. At a document's first/last chunk the neighbour id is
absent (older bulk loads stamped the literal string `"None"`).

```bash
curl -s "$BASE/v1/chunks?collection=open-access&ids=<prev_id>,<next_id>" \
  -H 'X-API-Key: kp'
# {"chunks":[{"doc_id":"…","chunk_id":"…","content":"…","metadata":{…}}, …]}
```

`404` — unknown or unreadable collection. `422` — more than `max_chunk_ids`
ids. `503` — authorization store unavailable.

### GET /v1/collections

The collections this caller may serve `collection` / `collections` from, and the
id an omitted `collection` resolves to **for them**. Any authenticated caller;
there is no role gate.

**Owner-filtered.** The listing is the per-tenant allowlist (`TENANT_COLLECTIONS`)
**intersected with what the caller may actually read** — owned, granted, group-
or `public`-shared; admins see everything. Order is registry **insertion** order,
deliberately not sorted, because the tie-break below depends on it. An
authorization-store outage is **503**, not a short list: the endpoint refuses
rather than risk hiding a readable collection (fail closed).

**Response** (`CollectionsResponse`): `{ collections: CollectionInfo[], default }`.

> **`default` and `is_default` are two different things, and conflating them is
> [#419](../CHANGELOG.md).** Read the right one:
>
> | | `CollectionsResponse.default` | `CollectionInfo.is_default` |
> |---|---|---|
> | Type | **string** (an id) | **boolean** |
> | Means | the id **this caller's** omitted `collection` targets | the **global registry pointer** (`DEFAULT_COLLECTION_ID`) names this entry |
> | Scope | per caller — two callers get different values | per deployment — the same for everyone who can see it |
> | How many | exactly one value, always | true on **at most one** listed entry, and on **zero** when the caller cannot read the one the pointer names |
> | When the caller can read nothing | `""` (empty string) | no entries at all to carry it |
>
> `CollectionInfo.default` is a **deprecated alias of `is_default`** — same
> boolean, confusable name. New clients read `is_default`.
>
> A client that wants a deterministic target should send
> `CollectionsResponse.default` **explicitly** on `/v1/query`, `/v1/retrieve` and
> `/v1/chunks` rather than omitting the field.

**How `default` is computed** — one function, called by the listing, by
`/v1/query`, `/v1/retrieve`, `/v1/chunks` and by both ingest paths, so the
advertised id and the resolved id cannot drift (they used to be separate
expressions, which is #419):

1. the per-tenant allowlist ∩ the caller's readable set = `collections`;
2. the **registry pointer** if it is among them, else the **first entry in
   insertion order**;
3. nothing readable → `default: ""`, `collections: []`, and every implicit-target
   route answers **404** `no collection is accessible to this caller`.

Ingest adds one narrowing step between (1) and (2) — the writable subset — and
its own 403 when that subset is empty; see [Collection
ownership](#authentication--tenancy) and [POST /v1/ingest](#post-v1ingest).

**`CollectionInfo`** — `id`, `label`, `model`, `dim` and `default` are required;
the rest are optional:

| Field | Type | Notes |
|---|---|---|
| `id`, `label` | string | registry id and display label |
| `model`, `dim` | string, int | the bound embedding model and its dimension |
| `chunk_method`, `chunk_size` | string \| null, int \| null | the bound chunk strategy |
| `is_default` | bool | the **global registry pointer** flag (see above) |
| `default` | bool | deprecated alias of `is_default` |
| `state` | string \| null | `active` \| `archiving` \| `dormant` \| `restoring` \| `lost`; null for a collection the registry does not track (the settings-derived default). See [POST /v1/collections/{id}/restore](#post-v1collectionsidrestore) |
| `archive_pending` | bool \| null | the last load succeeded but its archive step failed — the collection stays active and cannot be evicted |
| `versions` | int[] \| null | ordered archive version numbers on the registry row; a restore replays them in this order |
| `count`, `text_count` | int \| null | tenant-scoped vector and BM25 chunk counts (own + `public`, widened by a read share); null when unavailable — compare the two for a vector↔text parity check |
| `provenance` | object \| null | build lineage from the collection's manifest; `source` is `ingest` (verified) or `config` (declared). Deliberately omits `embedding_endpoints` — internal infra URLs are not exposed here |

```bash
curl -s "$BASE"/v1/collections -H 'X-API-Key: kp'
# {"collections":[{"id":"open-access","label":"Open access","model":"BAAI/bge-m3",
#                  "dim":1024,"default":true,"is_default":true,"state":"active",
#                  "count":25143002,"text_count":25143002},
#                 {"id":"my-papers","label":"My papers","model":"BAAI/bge-m3",
#                  "dim":1024,"default":false,"is_default":false,"state":"active"}],
#  "default":"open-access"}
```

`401` — missing or invalid credential. `503` — authorization store unavailable.
Schemas: `contracts/schemas/collections_response.json`, `collection_info.json`.

### POST /v1/collections

Create a collection. `id` and `label` are optional; omitting `embedding` and `chunk`
builds from the **server-default build spec** (resolved to concrete values at create
time, so later default changes never re-identify an existing collection). Supplying
`embedding` or `chunk` is an **admin-only** override → `403` otherwise.
Full schema: `contracts/schemas/collection_create_request.json`.

**Success is `201`** with the new `CollectionInfo`. Refusals:

| Status | When |
|---|---|
| `400` | the referenced model is not an **embedding** model; or the chunk config cannot chunk — an unknown `chunk.method` (the message lists the valid ones), a negative overlap, or an overlap ≥ the effective chunk size (both would loop forever) |
| `403` | the admin-only `embedding`/`chunk` override without the admin role; or `ALLOW_USER_COLLECTION_CREATE=false` and the caller is not an admin; or the deployment's effective cap is zero |
| `404` | the referenced embedding model is not registered (`GET /v1/admin/models/registry`) |
| `409` | the resolved spec collides with an existing collection; or the id is the reserved pointer name `default`; or the id carries residual ACL state owned by another subject (the create is rolled back rather than inheriting it); or the caller already owns `MAX_COLLECTIONS_PER_OWNER` collections — a structured `{owned, limit}` `detail` |
| `413` | the JSON body exceeds `max_json_body_bytes` (default 1 MB) |
| `429` | `rate_limit_collections_create_per_hour` exceeded (default **5**/h; `Retry-After` in seconds; admins exempt) — see [Rate limits](#rate-limits-issue-87) |
| `503` | the authorization store could not record ownership — the create is rolled back (fail closed) |
| `507` | the `max_collections` bound on **active** collections is met and nothing can be evicted to make room (see below) |

The bound counts **active** rows (`state == active`) of the **durable registry**
(`collections_file` / the `collections` table), not the serving process's in-memory
registry, and the count is taken *atomically with the id reservation* — so it sees
collections registered by a sibling API process, by the bulk CLI, or by a hand edit,
and concurrent creates cannot overshoot it. At the bound the create first **evicts
exactly one** least-recently-accessed active collection whose Workspace archive is
current (#359): its row is swapped `active → dormant` *before* its Qdrant collection
and ES index are dropped, so readers get `503 + Retry-After` from that instant and
the first access restores it. Never evicted: a collection with an `accepted`/`running`
ingest job, one whose last archive step failed (`archive_pending`), one never archived,
one whose stores are the legacy shared surface's (the settings-derived default, or a
spec that claims its stores) or are shared with another registry id. When no candidate
exists the create is `507` and the detail counts the ineligible collections per reason
(`not_active`, `archive_pending`, `no_archive`, `in_flight`, `protected`,
`unregistered`); ten concurrent creates with one slot left and nothing evictable yield
one `201` and nine `507`. `POST /v1/admin/collections/evict?need=k[&dry_run=true]`
(admin) runs the same policy by hand and returns the plan / outcome
(`contracts/schemas/eviction_response.json`). A shared-surface `default` pointer is charged a slot, since
it is a pointer rather than a durable row. The reservation happens **before** any
physical Qdrant collection or ES index is created, so a crash mid-create leaves a spec
with no store (which the next startup simply builds) rather than a store with no spec.
Where no durable store is configured (inline `collections_json`, or neither set) the
cap degrades to the in-process count and the create is logged as in-memory only.

The new collection is **owned by its creator and private by default** — no other
caller can read it until it is shared (see *Collection ownership* above). The
creator is recorded on the durable spec itself and the owner row is written right
after the registry write; if the owner row cannot be recorded the create is
**rolled back** (409 for residual ACL state under the id, 503 for a store outage)
rather than returning a 201 whose ownership silently never landed. A crash inside
that window self-heals: the startup backfill repairs the owner row from the
spec-recorded creator (privately — it never publishes a spec-owned collection).

```bash
curl -s "$BASE"/v1/collections \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"id": "my-papers", "label": "My papers"}'
```

### DELETE /v1/collections/{id}

Unregister a collection, optionally purging its data. **Owner-or-admin** (no
longer admin-only — a user manages its own private collections, ADR-0003 §2),
gated through the same seam and with the same leak-safe statuses as the share
routes: **403** readable-but-not-owned, **404** unknown *or* unreadable, **503**
store outage.

**The two forms are mutually exclusive by design, and exactly one is always legal**,
so nothing is undeletable:

| `?purge=` | What happens | Refused (**409**) when |
|---|---|---|
| `false` (default) | drops the **binding** only; the Qdrant collection and the ES index stay | **no other registry entry claims that store** — unregistering would strand data no entry claims and no ACL governs (ADR-0002 decision 5 toward zero), and repeated create/delete cycles would accumulate orphaned stores the collection cap never sees. → **204** |
| `true` | also deletes the Qdrant collection, the ES index, the collection's knowledge-graph triples (**every tenant's**, when a graph backend is configured) and the provenance manifest. **Irreversible** — recoverable only by re-ingesting | the store is **shared with another registry entry** (content-addressed collections built from an identical spec share one store; the message names the others). → **200** `CollectionPurgeReport` |

Both forms are also **409** for the legacy shared collection and for the current
default pointer's target. Purging is **idempotent**: a target already gone is
listed under the report's `absent`, not an error, and a partial failure (Qdrant
dropped, ES errored) is *reported* — never rolled back, never hidden behind a 500.

Deleting **revokes every ACL row** of the collection — the owner row and all
shares, softly, so audit history survives — which is what stops a later
collection reusing the same id from inheriting the deleted one's owner row or
`public` grant.

```bash
curl -s -X DELETE "$BASE"/v1/collections/my-papers          -H 'X-API-Key: kp'  # 204 unregister
curl -s -X DELETE "$BASE"/v1/collections/my-papers?purge=true -H 'X-API-Key: kp'  # 200 + report
```

Schema: `contracts/schemas/collection_purge_report.json`.

### POST /v1/collections/{id}/restore

Explicit, **owner-or-admin** counterpart of the on-access restore (#358). A
collection whose physical stores were evicted is `dormant`: only its archive —
`versions/<n>/` under the **owner's** Workspace, one directory per completed
ingest or delete — still exists. This lists those versions and submits the
`restore-collection` workflow **as the caller** (their bearer token authenticates
the submission and pre-stages every `ws://` version directory), replaying them in
order: every file's sha256 is verified and the manifest's `spec_hash` must equal
the registry row's **before anything is written**; chunk versions upsert both legs
with deterministic ids, tombstone versions delete by doc id.

**Bearer only.** An API-key principal carries no Workspace token and is **400** —
not 401, because the credential is valid, it just cannot be used to submit.

**Idempotent**, and the state is a compare-and-swap so concurrent callers cannot
double-submit:

| Row state on arrival | Result |
|---|---|
| `dormant` | submits, flips the row to `restoring`, returns `submission_id` |
| `restoring` | **202**, no second submission, `submission_id: null` |
| `active` / `archiving` | **202**, nothing to do, `submission_id: null` |
| `lost` | **may retry** — unlike the on-access path, which answers 409 here, since the owner may have repaired the archive |

On completion the row flips `restoring → active`; an engine failure returns it to
`dormant` with the error recorded; a checksum or spec-hash failure marks it `lost`
with the reason.

```bash
curl -s -X POST "$BASE"/v1/collections/my-papers/restore \
  -H "Authorization: Bearer $BVBRC_TOKEN"
# {"collection_id": "my-papers", "state": "restoring", "submission_id": "sub_…",
#  "message": "restore of 3 version(s) submitted"}
```

**202** `CollectionRestoreResponse` `{ collection_id, state, submission_id?,
message }`. Refusals: **400** no bearer credential, or the registry row records no
owner subject so its Workspace folder cannot be located; **403** readable but not
owned; **404** unknown/unreadable; **502** the Workspace listing or the engine
submission failed (the row is left as it was — retry is safe); **503** the
authorization store is unavailable, *or* no workflow engine is configured, *or*
the tenant is [at capacity](#post-v1collections) (`Retry-After` set; nothing is
submitted). Schema: `contracts/schemas/collection_restore_response.json`.

### Collection shares

`GET`/`POST`/`DELETE /v1/collections/{id}/shares` manage who may **read** a
collection (see *Collection ownership* above). All three are **owner-or-admin**,
gated through the same authorization seam as `DELETE /v1/collections`: a non-owner
who can read the collection gets **403**, one who cannot gets **404** (unreadable
== unknown, so a private collection's existence and grantee list never leak), and
an authorization-store outage is **503** (fail closed).

**Grantee resolution** (`POST` body `grantee`, resolved server-side and echoed
back in the response so a typo — an unclaimable grant — is visible):

| Input | Resolves to |
|---|---|
| `@public` or `public` | the built-in world-readable **public group** (read-only) |
| `@group:<id>` or `group:<id>` | a named **group** by id (read-only; the group must exist — else **422** echoing the id) |
| `@service:<subject>` | a **service account** (#258) — the subject is kept **colon-free**, i.e. exactly the string its API key authenticates as. An empty subject, a subject containing `:`, or one of the reserved fallback tenants `default` / `public` is **422**: those two are the tenants every *unmapped* API key resolves to, so `@service:default` would be an unrestricted `@public` wearing a single-account name (use `@public` if that is the intent) |
| a value containing `:` | a full `issuer:subject` string, kept **verbatim** (issuer/subject halves must both be non-empty) |
| a bare username | prefixed with `issuer` (default `bvbrc`) → `bvbrc:<username>` |

`@service:` is **required** for a machine identity, not a convenience: a bare
subject falls into the last row and is qualified to `bvbrc:<subject>` — a
*federated* identity the service account can never authenticate as, so the grant
is created, echoed, and silently never applies. The echoed `grantee_id` is how
you check which namespace it landed in. `@service:` with a colon in the subject
is **422** (it would forge a federated grantee through the machine-namespace
door); the account need not be registered, since registration is opt-in.

v1 is **read-only**: `permission` accepts `read` only (`owner` → **400**, it is
transferred not granted; `write`/anything else → **422**). `grant_option` is not
exposed (always false, delegation deferred). Sharing with a user who has never
logged in pre-provisions their row so the grant is claimed on first login (there
is no BV-BRC username-existence check). **Making a collection public** is
`POST {grantee: "@public"}`; **un-publishing** is `DELETE` of that `public` share.

Once granted, a read share also **widens the grantee's retrieval scope** for that
collection: its chunks are stamped with the owner's per-writer `tenant_id` at
ingest, so a query/retrieve against the shared collection includes the owner's
tenant alongside the caller's own + `public` — the grant makes the data readable,
not just the ACL.

```bash
# Grant read to a BV-BRC user, then publish, then list, then un-publish
curl -s -X POST "$BASE"/v1/collections/my-papers/shares \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"grantee": "alice"}'                 # -> 201, grantee_id "bvbrc:alice"
curl -s -X POST "$BASE"/v1/collections/my-papers/shares \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"grantee": "@service:svc-askclark"}' # -> 201, grantee_id "svc-askclark"
curl -s -X POST "$BASE"/v1/collections/my-papers/shares \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"grantee": "@public"}'                      # publish (read to everyone)
curl -s "$BASE"/v1/collections/my-papers/shares -H 'X-API-Key: kp'
curl -s -X DELETE "$BASE"/v1/collections/my-papers/shares/<share_id> \
  -H 'X-API-Key: kp'                        # -> 204 (un-publish / revoke)
```

**`POST`** (`ShareGrantRequest`): `{ grantee (required), permission?="read",
issuer?="bvbrc" }` → **201** `ShareRecord`. Also: **409** on a duplicate active
grant, a no-op grant to the current owner, or a public write.

**`GET`** → `SharesResponse`: `{ shares: ShareRecord[], owner }`.
`?include_revoked=true` adds soft-revoked rows (audit history: `active:false`,
`revoked_by`/`revoked_at` set).

**`DELETE`** → **204**. The `share_id` must belong to `{id}` (a foreign or
unknown id is **404**, never a cross-collection revoke). Revocation is soft
(audit history survives) and cascades along the `granted_by` chain (ADR-0004).
The active **owner** row is not revocable here (**409** — transfer or delete the
collection instead). Schemas: `contracts/schemas/share_grant_request.json`,
`share_record.json`, `shares_response.json`.

### POST /v1/collections/{id}/owner

Transfer a collection to another user — the flow the shares endpoint refuses
`permission: owner` in favour of. There is exactly **one active owner row per
collection** (a partial unique index enforces it), so a handover is an *atomic
revoke+grant pair*, not an extra share. **Current-owner-or-admin**, gated through
the same seam and with the same leak-safe statuses as the share endpoints (403
readable-but-not-owned, 404 unknown *or* unreadable, 503 store outage).

`POST`, not `PATCH`: there is no owner *document* to merge a partial
representation into. This is a state transition with audit side effects — one row
soft-revoked, one row appended — and it is not idempotent (replaying it is a
**409**, not a no-op).

**The outgoing owner loses access** unless something else grants it back. Their
owner row is **soft-revoked, never deleted** (ADR-0004 decision 6 — the handover
stays in the audit trail), and **no consolation `read` grant is minted** for them:
that would be a second write outside the transfer's transaction, with no rollback
for an already-committed handover, and it would leave an active grant nobody asked
for that the new owner has to discover and revoke — the wrong default for the
common case (offboarding, a mis-assigned collection). Re-granting is one explicit,
audited call: `POST /v1/collections/{id}/shares {"grantee": "<previous owner>"}`.

So that neither choice is *silent*, the response says what happened:
`previous_owner`, the `revoked_share_id` of their revoked row (still visible via
`?include_revoked=true`), and `previous_owner_retains_read` — re-evaluated through
the authorization seam, so it accounts for an independent share, group membership
or a `public` grant (`null` if the store could not answer; the transfer had
already committed). The transfer is deliberately **non-cascading**: every other
share on the collection survives untouched.

`subject` resolves with the same rules as a share `grantee` (see the table above)
**except** that the group forms (`@public`, `@group:<id>`) are **400** — ownership
is grantable to users only, never to a group. A subject that has never
authenticated is pre-provisioned, exactly as a share grantee is.

```bash
curl -s -X POST "$BASE"/v1/collections/my-papers/owner \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"subject": "bob"}'   # -> 200, owner "bvbrc:bob"
# {"collection_id": "my-papers", "owner": "bvbrc:bob",
#  "previous_owner": "bvbrc:alice", "revoked_share_id": "…",
#  "previous_owner_retains_read": false, "share": { … }}
```

**`POST`** (`OwnerTransferRequest`): `{ subject (required), issuer?="bvbrc" }` →
**200** `OwnerTransferResponse`. Also: **409** when `subject` already owns the
collection, or when it has no active owner row to transfer from (only reachable
by an admin — a lost owner row is repaired from the recorded creator by the
startup backfill); **422** for a malformed subject. Schemas:
`contracts/schemas/owner_transfer_request.json`,
`owner_transfer_response.json`.

### POST /v1/collections/{id}/graph

Extract the **knowledge graph** of one archived version of the collection (#350,
phase 6 of #201) — the graph leg of the lifecycle, **off by default and never
part of an ingest** (one LLM call per chunk is ~10× the embed cost). Owner or
admin. Submits the `graph-extract` workflow **as the caller** over the latest
chunk version's `ws://` `versions/<n>/` directory (or `?version=n`; tombstones are
skipped): the LLM extractor runs over that version's chunks, the triples (with
the #347 evidence fields) are archived beside the version as its `triples` leg —
the version's `manifest.json` is rewritten with `graph: true`, the one intended
overwrite of an archived file — and loaded into the graph store scoped by
`(tenant, collection)`. Details: [`docs/ingest-paths.md` § Graph
extraction](ingest-paths.md#graph-extraction-the-triples-leg-350).

**202** `GraphExtractResponse` `{collection_id, version, job_id, submission_id,
message}` — the job (kind `graph`) is polled at `GET /v1/ingest/{job_id}`
(unchanged shape) and completes only once the engine reports the leg
**delivered**; only then does the registry record the version in
`graph_archived_versions` (what eviction's graph drop will be gated on, #380).
**Idempotent per version**: a version whose leg already exists (the row, or the
archived manifest with the triples file `stat`ed present at its recorded size — a
manifest alone is a half-applied delivery and is resubmitted) answers 202 with
`job_id: null` and submits nothing. Refusals: **401** no BV-BRC user (bearer)
token — the submission is made as the user, so API-key/keyless callers cannot
(checked before the id is looked up, so it leaks nothing); **403** readable but
not owned, or the caller's token cannot read the owner's archive; **404**
unknown/unreadable; **409** no registry row (the settings-derived default) or not
`active`/`archiving` (restore it first); **400** no owner subject on the row, no
archive / no chunk version, `version` absent or a tombstone; **429 +
`Retry-After`** an extraction of this *collection* is already in flight (whoever
started it, admins included), or the caller has
`graph_extraction_jobs_per_owner` (default 1; admins exempt) in flight; **502**
the engine/Workspace refused (the job is `failed` with the class label); **503**
no workflow engine configured. An LLM outage never becomes an empty leg: the
extract step exits 1 (retryable) when every attempted chunk — or more than
`graph_extraction_max_failed_fraction` of them — failed its call. A load refused at the triple budget fails the job
`graph_cap_exceeded` with nothing loaded and nothing archived; `upload_failed`
fails it `OUTPUT_STAGING_FAILED` with nothing recorded.

```bash
curl -s -X POST "$BASE"/v1/collections/my-papers/graph \
  -H "Authorization: Bearer $BVBRC_TOKEN"        # -> 202
# {"collection_id": "my-papers", "version": 2, "job_id": "…",
#  "submission_id": "sub_…", "message": "graph extraction of version 2 submitted; …"}
```

Schema: `contracts/schemas/graph_extract_response.json`.

### Groups

`/v1/groups` manages **RAGStack-native named groups of users** — a group is a
share target, so `POST /v1/collections/{id}/shares {grantee: "@group:<id>"}`
grants **read** to every active member at once. Group membership is unioned into
read authorization through the same seam as direct and `public` shares
(`grants_for_subject`), so a membership change is an **instant access change**:
adding a member immediately opens every collection shared to the group, removing
one immediately closes them — evaluated at request time, no caching.

- **`POST /v1/groups`** (`GroupCreateRequest` `{ name }`) → **201** `GroupRecord`.
  Any authenticated caller creates a group they own. Empty/whitespace name →
  **422**; the reserved `public` name or an active name collision for this owner
  → **409**.
- **`GET /v1/groups`** → `GroupsResponse` `{ groups: GroupRecord[] }` — the
  groups the caller owns or is an active member of (the implicit `public` group
  is not listed).
- **`GET /v1/groups/{id}`** → `GroupDetailResponse` `{ group, members:
  GroupMemberRecord[] }`. **Owner-or-member** may view; anyone else gets a
  leak-safe **404** (a private group's existence and membership never leak).
  The built-in `public` group is viewable and returns an empty member list.
- **`DELETE /v1/groups/{id}`** → **204**. **Owner-or-admin** (a non-owner member
  → **403**, a non-member → **404**). Soft delete (audit survives); shares to the
  group become inert immediately. The built-in `public` group is **not deletable**
  (**409**).
- **`POST /v1/groups/{id}/members`** (`GroupMemberAddRequest` `{ subject,
  issuer?="bvbrc" }`) → **201** `GroupMemberRecord`. **Owner-or-admin**. The
  `subject` resolves exactly like a share grantee (full `issuer:subject`
  verbatim, `@service:<subject>` for a service account → kept colon-free, or a
  bare BV-BRC username → `bvbrc:<username>`) and is echoed back; a
  never-logged-in user is pre-provisioned. Membership is a **flat list of users**
  — a group id (any `@group:`/`@public` form) is rejected (**422**, no nesting); a
  duplicate active membership or the built-in `public` group → **409**.
- **`DELETE /v1/groups/{id}/members/{subject}`** (optional `?issuer=` query,
  default `bvbrc`) → **204**. **Owner-or-admin**. `subject` resolves the **same
  way as on add** — a full `issuer:sub` string (and an `@service:` form)
  verbatim, a bare BV-BRC username →
  `bvbrc:<username>` (or the given `issuer`) — so removing with the identifier
  used to add reliably matches. A group-target form (`@group:`/`@public`) is
  rejected (**422**, no nesting). Removing a non-member is a no-op (**204**). Soft
  removal (audit survives).

Group grants stay **read-only** in v1 (the share API rejects `write`/`owner`
grantees for a group, and `write`/`owner` resolution is owner-only regardless).
An outage of the authorization store is **503** (fail closed) on every route.
Schemas: `contracts/schemas/group_record.json`, `group_member_record.json`,
`group_create_request.json`, `group_member_add_request.json`,
`groups_response.json`, `group_detail_response.json`.

### Service accounts

`/v1/admin/service-accounts` registers **machine identities** — the callers that
authenticate with an `X-API-Key` secret rather than a token an external issuer
signed. A service account's **`subject` is its API-key tenant string and its
authorization subject**, so a registered account can own collections, receive
shares and join groups under that one identifier. Subjects are **colon-free**
(`:` is reserved for federated `issuer:sub` identities, keeping the two namespaces
disjoint), and the reserved `default` / `public` tenants are refused (**400**) —
they are shared fallback tenants, not one caller's identity. Admin only, on every
route.

> **Naming a service account as a grantee or member takes the explicit
> `@service:<subject>` form.** Those surfaces qualify a bare, colon-free value
> with the default issuer, so `{"grantee": "svc-askclark"}` stores
> `bvbrc:svc-askclark` — a federated subject the machine account never
> authenticates as, i.e. a grant that silently never applies. Check the echoed
> `grantee_id` / `subject`: it must come back **colon-free**.

**Two subject namespaces, one authorization seam.** Every subject the
authorization code sees (`resolve_access`, share grantees, group members,
collection owners) comes from one of exactly two namespaces, and they differ in
*who vouches for the identity*:

| | Service account (API key) | Federated user (bearer) |
|---|---|---|
| Subject shape | **colon-free** (`svc-something`) | **`issuer:sub`** (`bvbrc:alice`) |
| Who issues it | **us** — the operator mints the key locally | an **external** identity provider |
| The credential | **the key itself is the secret**; authentication is a `secrets.compare_digest` constant-time compare against `API_KEYS`, nothing else | a signed token, **verified against the provider** (signature/introspection) |
| Where it is configured | `API_KEYS` + `API_KEY_TENANTS` + `API_KEY_ROLES` in the tenant env | nothing per-user; the row is created on first successful auth |
| Where its **role** comes from | `API_KEY_ROLES` (falling back to `DEFAULT_ROLE`) | `ADMIN_SUBJECTS` **or** a `users.role` of `admin`, else `user`. **Never `DEFAULT_ROLE`** — it is `admin` in production, and inheriting it would make every authenticated end user a superuser |
| Revocation | disable (soft, within cache TTL) or remove from env + restart (authoritative) | at the provider, plus `IDENTITY_CACHE_TTL_SECONDS`; the admin role separately via `PATCH /v1/admin/users/{subject}/role` (within `ADMIN_ROLE_CACHE_TTL_SECONDS`) or by editing `ADMIN_SUBJECTS` + restart |

Because an API-key tenant string *is* the authz subject, a key mapped to a tenant
literally spelled `bvbrc:alice` would hand its holder Alice's collections. The
**#243 startup guard keeps the namespaces disjoint**: whenever an identity
provider is enabled, `validate_role_settings()` refuses to boot if any
`API_KEY_TENANTS` value contains a `:`. The service-account API enforces the same
rule from the other side (a colon-bearing `subject` is **400**), so a service
account can never collide with — or be impersonated by — a federated identity.

- **`POST /v1/admin/service-accounts`** (`ServiceAccountCreateRequest`
  `{ subject, purpose? }`) → **201** `ServiceAccountRecord`. A colon-bearing,
  blank, over-long (>128 chars) or reserved (`default`/`public`) subject is
  **400**; a subject that already exists as a **human** account is **409**
  (converting a person's row into a machine credential is a privilege event and is
  refused). Re-registering an existing service account returns the stored row
  **unchanged** — a provisioning script is re-runnable, and a re-create is not a
  re-enable.
- **`GET /v1/admin/service-accounts`** (optional `?created_by=`, `?limit=`) →
  `ServiceAccountsResponse`. Oldest first, **disabled accounts included** (soft
  state, never a deletion).
- **`POST /v1/admin/service-accounts/{subject}/disable`** / **`/enable`** → **204**,
  idempotent. **404** for an unknown subject, **409** for a human one — or, on
  `disable`, for **the account the caller is authenticating as**: the disabled
  check runs on that same API-key path, so it would 401 the caller out of the
  `/enable` that undoes it, with no way back through the API. Use another admin
  credential.
- **Enabling never erases the disable.** `disabled_by`/`disabled_at` record the
  last revocation and survive a re-enable; `enabled_by`/`enabled_at` record who
  reversed it (ADR-0004 decision 6). `active` is the state — do not infer it from
  `disabled_at`.

> **This surface manages the account record, never the credential.** `API_KEYS`,
> `API_KEY_TENANTS` and `API_KEY_ROLES` are environment settings with no writer in
> the server. **Provisioning a key is an operator env edit plus a restart, and the
> key AND its tenant mapping must go in the same edit** — startup fails in
> production if `API_KEY_TENANTS` is set and any configured key is unmapped. No
> response here ever carries key material, a key prefix, or a key count.

**Disabling is what makes a leaked key stoppable without that restart.** Once an
account is disabled, its key is rejected with **401** on the API-key path. Two
properties of that check are deliberate and are contract, not implementation
detail (ADR-0004 decision 7):

- It **fails open**. If the user store cannot answer, the request proceeds — the key
  was already verified by a constant-time compare, and locking out every API-key
  caller (the ingest path, and the whole production surface) to enforce a
  revocation convenience is a worse outcome than the revocation lapsing. So
  **disabling is a soft, best-effort revoke; the authoritative revoke is removing
  the key from `API_KEYS` and restarting.**
- It is **cached per subject** for `SERVICE_ACCOUNT_DISABLED_CACHE_TTL_SECONDS`
  (default `30`; `0` disables the cache at the cost of a store read on every
  API-key request), so **the TTL is the revocation lag**, per worker process — the
  same trade `IDENTITY_CACHE_TTL_SECONDS` makes, and it carries the **same hard
  cap: > 300 fails startup**. Raising it to cut ACL-database load would otherwise
  silently buy a revocation window of hours, on the only revoke that works without
  a restart. The worker that serves the disable flushes its own cached answer
  immediately; its siblings wait out the TTL.

**Rotation vs. disable** — the two operations are not the same lever, and the
difference is what an operator has to plan for:

| | Mechanism | Takes effect | Needs a restart? |
|---|---|---|---|
| **Rotate a credential** (new key, retire old) | edit `API_KEYS` / `API_KEY_TENANTS` / `API_KEY_ROLES` in the tenant env | **at restart** — the settings are read once at import | **yes** |
| **Disable an account** (stop a leaked key now) | `POST /v1/admin/service-accounts/{subject}/disable` | **within `SERVICE_ACCOUNT_DISABLED_CACHE_TTL_SECONDS`**, per worker process | no |

So a planned rotation is a scheduled env edit + restart (overlap both keys so the
consumer swaps without downtime), while an emergency is *disable first* — it
lands within the TTL and needs no maintenance window — *then* rotate to make it
authoritative. Step-by-step: **[Provisioning a service account
(`svc-askclark`)](DEPLOYMENT.md#provisioning-a-service-account-svc-askclark-on-the-asm-tenant)**
in the deployment runbook.

Registration is **opt-in**: an API key whose tenant has no record keeps working
exactly as before, and nothing on the key path ever writes a user row. Bearer
authentication is untouched. Schemas:
`contracts/schemas/service_account_record.json`,
`service_accounts_response.json`, `service_account_create_request.json`.
Python only — the Go implementation has no `X-API-Key` authentication path.

### PATCH /v1/admin/users/{subject}/role — bearer admins

A bearer identity **can** be an admin, but only by deliberate assignment. There
are exactly two admin sources, both server-side, so **a token can never elevate
the caller presenting it** — no claim, no header, and nothing in `DEFAULT_ROLE`
is an input:

1. **`ADMIN_SUBJECTS`** — an env allowlist of `issuer:subject` strings.
   Evaluated **first**, as a pure set membership test with **no store read**.
   That is what makes it usable to *bootstrap* (this route is itself
   admin-gated, so a store-only design would 403 the very operator creating the
   first admin), what keeps admin working through a user-store outage, and what
   makes it **break-glass that no database write can revoke**. Entries must be
   colon-**bearing** — a colon-free entry names an API-key tenant and is refused
   at startup, pointing at `API_KEY_ROLES` instead.
2. **`users.role = 'admin'`** — written only here, by an existing admin.

Precedence is **one-directional**: a stored `user` role does not demote an
allowlisted subject. The response's `env_admin` says whether that applies, so a
revoke that changes nothing visible is not mistaken for one that worked.

**The last-admin refusal is scoped to a real lockout, and is decided inside the
write.** It fires only when the users table is the deployment's *last* admin
source: a usable `ADMIN_SUBJECTS` entry stands it down, and so does an API key
mapped to `admin` (`API_KEY_ROLES`/`DEFAULT_ROLE`) — which is very often the
credential making the call, so counting stored admins alone would refuse a
revoke to somebody who can grant it straight back. An `ADMIN_SUBJECTS` entry
whose issuer half no configured provider can produce is **not** a way back (it
can never match; startup warns about it) and does not relax the guard. The check
runs in the same transaction as the write, because "is this the last admin" is a
claim about the whole table: two concurrent revokes evaluated outside it would
each see the other's admin, both pass, and land on zero.

- **`PATCH /v1/admin/users/{subject}/role`** (`UserRoleRequest` `{ role }`) →
  **200** `UserRoleRecord`. **400** for an unknown role or a subject that is not
  a federated `issuer:sub` string (a colon-free subject is an API-key tenant —
  its role lives in `API_KEY_ROLES`); **404** when the subject has no users row
  (the route never mints one: a typo'd issuer prefix would otherwise create a
  permanent admin nobody can authenticate as); **409** for **revoking the last
  stored admin of a deployment that has no other admin source** (an
  unrecoverable lockout — promote somebody else first, map an API key to
  `admin`, or set the allowlist); **503** on a store outage. An unknown role
  stays a **400** even when it targets the last admin: the vocabulary is checked
  before the lockout guard. A service account is a **400**, not a 409 — its
  subject is colon-free by invariant, so it fails the federated-subject rule
  above before any store call.
- **What counts as "another admin source"** is a liveness question, not just a
  configuration one: an `ADMIN_SUBJECTS` entry whose issuer half the *configured*
  provider cannot produce does not count (and startup warns about it), and
  neither does an admin API key whose tenant is a **disabled** service account,
  because that key now 401s. If the store cannot answer whether it is disabled,
  the revoke is refused rather than allowed — a retryable 409 beats an
  irreversible lockout.
- `role_set_by`/`role_set_at` are the audit trail and are never blanked; an
  idempotent re-grant does not re-stamp them (ADR-0004 decision 6).

Two properties of the auth-time read are contract, not implementation detail:

- It **fails closed**. If the user store cannot answer, the caller resolves to
  `user` — the request still authenticates on its verified token, only the
  elevation is withheld. This is the deliberate **mirror image** of the
  service-account disabled check above, which fails *open*: that one can only
  refuse an already-verified credential, while this one can only *grant*
  privilege. `ADMIN_SUBJECTS` is unaffected by an outage.
- It is **cached per subject** for `ADMIN_ROLE_CACHE_TTL_SECONDS` (default `30`,
  capped at `300`, `0` disables), so **the TTL is the demotion lag** — a revoked
  admin keeps admin for that long per worker. The worker serving the PATCH
  flushes its own cache immediately; its siblings wait out the TTL.

**Admin is a hard superuser, not a UI tier**: read/write on every collection in
the deployment (ownership is bypassed with a logged `admin-bypass` decision),
plus all of `/v1/admin/*`, `/v1/config`, `/v1/health/deep`, the model registry
and `stats.policy`. Schemas: `contracts/schemas/user_role_request.json`,
`user_role_record.json`. Python only — Go serves no bearer identity surface.

### POST /v1/ingest

Accepts a `source` and processes it in the **background**, returning immediately
with a `job_id`. What a `source` *is*, and which gates run, depend on
`INGEST_BACKEND`:

| | `INGEST_BACKEND=local` (default) | `INGEST_BACKEND=gowe` (#203/#353) |
|---|---|---|
| `source` | a file or directory path, resolved within `INGEST_ROOT` | a **Workspace reference** — `ws:///<user>/home/…` or `/<user>/home/…`; anything else is **400** |
| Credential | API key or bearer | **bearer BV-BRC identity required** — the job is submitted to the engine *as the caller*; an API-key or keyless principal is **401** |
| `INGEST_ROOT` | **must be configured** — unset, every request is `503` | **never consulted**; nothing is read on the API host, the engine pre-stages the `ws://` source with the caller's token |
| Where the work runs | in-process background task | the GoWe workflow engine |

The `INGEST_ROOT` gate on the local backend is a confinement guard, not a
configuration nag: an unconfined `source` is an arbitrary server-side file read
whose text is retrievable back through `/v1/retrieve`. It is checked at request
time (so it closes keyless deployments too, and cannot brick a running
deployment that never ingests) and **only on the local path** — under `gowe`
control never reaches it.

On the local backend a directory is ingested recursively
(`.pdf`/`.txt`/`.md`/`.jsonl`), one document per item. Re-ingesting the same
source **replaces** that document's chunks (deterministic document id) rather
than duplicating; a re-ingest that yields no embeddable chunks fails the job and
leaves the prior version intact.

> For multi-hundred-MB JSONL corpus dumps, use the operator tool
> `python/scripts/ingest_jsonl.py` instead — it streams, fans out across embedding
> endpoints, and bypasses the per-file size guard. See [Bulk ingestion](#bulk-ingestion).

**Request** (`IngestRequest`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `source` | string | — | **required**; a path under `INGEST_ROOT` (local) or a Workspace reference (gowe) |
| `metadata` | object | `{}` | stamped onto every chunk of the document |
| `collection` | string \| null | null | target registry collection. Omitted (or the reserved name `"default"`) targets the caller's **writable** default — see [Collection ownership](#authentication--tenancy). An explicitly named id is never rerouted: unknown/unreadable → `404`, readable-but-not-writable → `403` |

**Response** (`IngestResponse`): `{ job_id, status, chunk_ids[], items?, collection? }`.
`collection` names the collection the job **actually** targets — the point of it
is the case where the *server* chose, so a caller who omitted the field learns
where the write landed without guessing. The id is always one from the caller's
own `GET /v1/collections`, so echoing it discloses nothing.

```bash
curl -s "$BASE"/v1/ingest \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"source": "papers/2024_review.pdf", "collection": "my-papers"}'
# {"job_id": "...", "status": "accepted", "collection": "my-papers"}
```

### GET /v1/ingest/{job_id}

Polls status: `accepted` → `running` → `completed` | `failed` (unknown id →
`unknown`, HTTP 200). Batch/directory jobs include `items`:
`{ total, completed, failed, pending }`. The response also carries `collection` —
the row's **own** stamp, so a poll answers "where did this land?" with the same
id the accept did; `null` on legacy rows written before the stamp existed.

```bash
curl -s "$BASE"/v1/ingest/<job_id> -H 'X-API-Key: kp'
# {"job_id":"…","status":"completed","chunk_ids":[…],"collection":"my-papers"}
```

### POST /v1/ingest/upload

Multipart counterpart to `POST /v1/ingest`: the caller uploads the bytes instead
of naming a server-side path. Each file is staged under a per-tenant, per-job
directory below `INGEST_ROOT` with a sanitized, traversal-confined filename, then
the same background ingest path runs over it — **202** with a `job_id`, polled at
[`GET /v1/ingest/{job_id}`](#get-v1ingestjob_id) exactly as a path ingest is.
Under `INGEST_BACKEND=gowe` the files go to the caller's Workspace and
`INGEST_ROOT` is not used.

**Request**: `multipart/form-data` with one or more `files` parts (required) and
an optional `collection` field carrying the same semantics as
`IngestRequest.collection` — omitted targets the caller's **writable** default.
The tenant is derived server-side from the credential, never from the body.

```bash
# curl sets multipart/form-data itself — do NOT add -H 'Content-Type: …' here.
curl -s -X POST "$BASE"/v1/ingest/upload \
  -H 'X-API-Key: kp' \
  -F 'files=@2024_review.pdf' \
  -F 'files=@notes.md' \
  -F 'collection=my-papers'
# {"job_id": "...", "status": "accepted", "collection": "my-papers"}
```

Bounds are all checked **before anything is staged or written**, and re-checked
while the files stream out of the server's spool, so nothing oversized reaches
the Workspace or `INGEST_ROOT`: content type in `UPLOAD_CONTENT_TYPES` and a
declared PDF starting with `%PDF` (**415**); per-file `MAX_DOCUMENT_BYTES`, at
most `MAX_UPLOAD_FILES` files, at most `MAX_UPLOAD_BYTES_PER_REQUEST` in total
(**413**). Before the body is read at all, a `Content-Length` over that
per-request cap (plus multipart framing) is **413** and a request without one
(chunked) is **411** — a client that *lies* about `Content-Length` is only
stopped at the gateway, which is why the deployment must enforce a matching body
cap (see DEPLOYMENT.md). **One ingest job per principal at a time**: while a job
of the caller's is still `accepted`/`running`, a new upload is **429** +
`Retry-After` (admins exempt); the same 429 covers the ingest rate-limit bucket
this route **shares** with `POST /v1/ingest`. Also **403**/**404** for the target
collection exactly as on `POST /v1/ingest`, **409** for a `lost` collection,
and **503** when `INGEST_ROOT` is unconfigured, the authorization store is down,
the target is `dormant`/`restoring`, or the tenant is at capacity. Bound values:
[Configuration](#configuration-server); status meanings: [Errors](#errors).

### GET /v1/documents · DELETE /v1/documents/{doc_id}

List the documents the caller can see, aggregated by `doc_id` from the served text
index. Paginated: `?limit=` (default 100, max `MAX_LIST_LIMIT` = 500) and an opaque
`?cursor=` taken from the previous response's **`X-Next-Cursor`** header — the header is
absent on the last page. A malformed cursor is **400** (deliberately generic; the supplied
value is never reflected back). `metadata` carries `chunk_count`. **There is no ordering
guarantee** — treat the sequence as unordered.

Both operations resolve the **caller's** default collection when `collection` is omitted
(#447), not the global registry pointer. A backend fault degrades to `[]` at 200, so an
empty list does not prove an empty corpus.

`DELETE /v1/documents/{doc_id}` removes one document and all its chunks (**204**). It
requires `write` on the resolved collection — except on the legacy shared surface, where
`read` suffices because per-chunk `tenant_id` stamping is the write isolation.

> The Go scaffold still returns `[]` here. This section describes the Python
> implementation.

### GET /v1/graph/entities · GET /v1/graph/neighbors/{entity} · GET /v1/graph/stats

All three are scoped to the caller's readable tenants (own + `public`) and, for a
confined tenant, to its collection. All three **degrade rather than error** when
no graph backend (Neo4j) is configured.

- **`GET /v1/graph/entities`** → `EntityInfo[]` `{ name, triple_count }`,
  most-connected first. `?limit=` (default **100**, `1`–`MAX_LIST_LIMIT` = 500;
  outside the range → **422**). No graph store → `[]`.
- **`GET /v1/graph/neighbors/{entity}`** → `TripleResponse[]`. `?depth=`
  (default **1**, `1`–`MAX_GRAPH_DEPTH` = **5**; above → **422**). The cap is a
  DoS guard, not a preference: `depth` feeds a Cypher variable-length traversal.
  No graph store → `[]`.
- **`GET /v1/graph/stats`** → `GraphStatsResponse`
  `{ backend, available, entities, relationships }`. With no graph store
  configured — or when the probe itself fails — it answers `available: false`
  with null counts rather than a 500 (the failing probe is logged, so an
  operator can tell "not configured" from "down").

**`TripleResponse`** carries the `subject` / `predicate` / `object` triple plus
the **#347 provenance fields** — optional in the contract, always emitted by the
Python implementation, and empty/zero when unknown. These are what the
[graph-extraction](#post-v1collectionsidgraph) leg archives beside a version:

| Field | Type | Notes |
|---|---|---|
| `evidence` | string | the verbatim span the triple was read from |
| `chunk_id` | string | the chunk that produced it |
| `derived_by` | string | `llm`, `tool:<source>`, or `""` (unknown) |
| `confidence` | int (0–3) | `0` unknown, `1` LLM-plausible, `2` corroborated by a tool, `3` verified against a structured source. **An LLM-derived triple is never above 1** |
| `subject_id`, `object_id` | string | typed ids where known, e.g. `bvbrc:genome:<id>` |

Schema: `contracts/schemas/triple_response.json`.

### GET /v1/models/available

The models registered for a **hot-swappable** task, i.e. the ids that are legal
in `/v1/query`'s `llm` and `reranker` fields. **Any authenticated caller** — this
is the picker's data source, so it is not admin-gated; `base_urls` are
deliberately **not** exposed here (registration is admin-only and SSRF-checked,
and the endpoint URLs are infrastructure).

```bash
curl -s "$BASE"/v1/models/available -H 'X-API-Key: kp'
# {"models":[{"id":"llama-70b","task":"llm","label":"Llama 3.3 70B",
#             "model":"meta-llama/Llama-3.3-70B-Instruct","provider":"vllm"}, …]}
```

**200** `AvailableModelsResponse` `{ models: [{ id, task, label, model,
provider }] }`. `401` only. An unknown id on a request is **404**; a registered
id used for the wrong task is **400**. Schema:
`contracts/schemas/available_models_response.json`.

### GET /v1/stats/tenants

The de-facto **"who am I"** call — there is no `/v1/me` — plus a tenant ×
collection count grid. Any authenticated caller.

Where [`GET /v1/stats/stores`](#operations--admin) collapses the caller's readable
tenants into one number per store, this **splits** that union, so an operator can
see which tenant actually owns a corpus (data sitting in `public` rather than the
org's own tenant) and per collection rather than in aggregate.

**The grid is what makes it expensive** — one count per tenant × collection ×
store, seconds on a large corpus. **`?counts=false` answers the identity half
alone**: the same response shape, every `vector_count`/`text_count` null, and
**no store probed**. Use that for a whoami.

```bash
curl -s "$BASE/v1/stats/tenants?counts=false" -H 'X-API-Key: kp'
# {"tenant":"asm","role":"user","readable":["asm","public"],
#  "restricted_to":null,"auth_enabled":true,"policy":null,
#  "tenants":[{"tenant":"asm","own":true,"collections":[…]}, …]}
```

**200** `TenantsResponse`:

| Field | Notes |
|---|---|
| `tenant`, `role` | the authenticated principal and its resolved role |
| `readable` | own + `public` — the tenants every read is filtered to |
| `restricted_to` | this tenant's collection allowlist; `null` = unrestricted |
| `auth_enabled` | false on the keyless dev path (production startup forbids it) |
| `policy` | **admin only** — the full tenant → collections map. `null` for everyone else, since it names other tenants |
| `tenants[]` | one row per readable tenant: `{ tenant, own, collections[] }` |

Rows cover the tenants this caller may read, **including a writer-tenant whose
data a share makes readable** — such a row is scoped to the shared collections
(other cells null) and is **omitted entirely when it carries no chunks**, so it
never discloses an owner the caller could not already see. Columns are only
collections the allowlist permits. Each count degrades to `null` independently.
`503` when the authorization store is unavailable — the breakdown is
owner-filtered, so it refuses rather than risk hiding a readable collection.

### GET /v1/jobs

Recent ingest jobs, most-recent-activity first, for the Ops dashboard.
**Admin only** — a job's `source` can carry a filesystem path, so a non-admin
gets **403**. `?limit=` (default **25**, 1–100).

```bash
curl -s "$BASE/v1/jobs?limit=10" -H 'X-API-Key: kadmin'
# {"jobs":[{"job_id":"…","status":"completed","source":"papers/","error":"",
#           "chunks":412,"items":{"total":17,"completed":17,"failed":0,"pending":0}}]}
```

**200** `JobsResponse` `{ jobs: JobSummary[] }`; each `{ job_id, status, source,
error, chunks, items }`. `error` is a **caller-safe label — the exception class
only, never a raw message**. `chunks` counts the `chunk_ids` stamped on the
record (single-document runs). This is a listing, not a poll: for one job's live
state use [`GET /v1/ingest/{job_id}`](#get-v1ingestjob_id), which needs no role.

### GET /v1/health/deep

Liveness of each backing store — vector, text, graph, job store — with
per-check latency and backend detail. **Admin only**: the `detail` strings can
carry backend hostnames and versions, so a non-admin gets **403**. The open,
keyless liveness check is [`GET /health`](#get-health); this one is neither.

```bash
curl -s "$BASE"/v1/health/deep -H 'X-API-Key: kadmin'
# {"status":"ok","checks":[{"name":"vector","ok":true,"detail":"qdrant 1.12.1",
#                           "latency_ms":3.4}, …]}
```

**200** `DeepHealthResponse` `{ status, checks: [{ name, ok, detail,
latency_ms }] }` — a failing dependency is reported **inside a 200**, in its own
check, rather than turning the probe itself into a 5xx. Schema:
`contracts/schemas/deep_health_response.json`.

### GET · PUT · DELETE /v1/admin/log-level

Change the running process's log level **without a restart**, and see what is in
force. Admin only on all three.

**Process-local, and it resets on restart** — a feature, not an oversight: a
debugging session left at DEBUG cannot silently become a tenant's permanent
configuration. To make a level stick, change `LOG_LEVEL` and restart. `pid` in
the response says which process answered; every production launch today is a
single uvicorn process with no `--workers`, so one call reaches the one process
that serves every request.

- **`GET`** → the live state: `configured_level` (the **raw** `LOG_LEVEL` string,
  exactly as `GET /v1/config` echoes it — including a value the server rejected),
  `configured_level_resolved` (what it resolves to; `INFO` when unrecognised),
  `effective_level` (the root logger **right now**), `runtime_override`,
  `changed_at`/`changed_by`, the dampen set, the per-logger `loggers` list with
  `logger_override_count`/`max_logger_overrides`, and the pending-expiry fields
  below.
- **`PUT`** (`LogLevelRequest`) → **200** with the new state. Applied live, on the
  very next log call. **Validation is atomic**: the whole body is checked before
  anything is applied, so a `422` leaves the effective level exactly as it was.
  - `level` — the new **root** level. Case-insensitive; `warn` is accepted;
    `NOTSET` is rejected.
  - `loggers` — a name → level map with **replace semantics**: what you send
    becomes the complete set of runtime overrides, `{}` clears them all, and
    omitting the field leaves them untouched. Names are charset/length checked
    and **must already exist in the process** — `logging.getLogger(name)` creates
    a logger permanently, so accepting arbitrary names would be an
    unbounded-growth path even behind an admin gate. `ragstack.audit` cannot be
    overridden.
  - `ttl_seconds` — auto-revert after N seconds (1–`max_ttl_seconds`, default no
    expiry). At the deadline the process reverts to the **configured defaults** —
    the same end state `DELETE` produces, *not* whatever was in force before the
    PUT — audited as `audit=expired` and carrying the principal that armed it.
  - **Both fields are optional but a body needs one of them**, and a body
    carrying **only** `ttl_seconds` is refused: a TTL modifies a change, it is
    not one.
  - **A later PUT supersedes an earlier one, expiry included** — every PUT cancels
    any pending revert *before* applying, so two TTLs can never fight. The
    corollary is worth reading twice: **a follow-up PUT that omits `ttl_seconds`
    disarms the expiry the earlier one armed.** `auto_revert_pending`,
    `expires_at` and `expires_in_seconds` in the response say so immediately.
- **`DELETE`** → **200**, idempotent (there is nothing to 404 on: "no override"
  is a valid state to reset from). Clears the root and per-logger overrides,
  re-applies `LOG_LEVEL` + `LOG_DAMPEN_LOGGERS`, and **cancels any pending
  auto-revert**. Audited as `audit=reset`, distinct from `audit=expired`.

**`level` does not govern `uvicorn.*`.** Uvicorn's loggers set
`propagate=False` and carry their own handlers, so `{"level":"CRITICAL"}` leaves
the access line printing and `{"level":"DEBUG"}` does not turn uvicorn debug on.
Name `uvicorn`, `uvicorn.error` or `uvicorn.access` in `loggers` to reach them —
that works, and it is the only way to. The side benefit is that a full "denial of
observability" is not reachable through this endpoint at all.

```bash
# Debug for ten minutes, then revert itself.
curl -s -X PUT "$BASE"/v1/admin/log-level \
  -H 'X-API-Key: kadmin' -H 'Content-Type: application/json' \
  -d '{"level": "DEBUG", "ttl_seconds": 600}'
curl -s "$BASE"/v1/admin/log-level -H 'X-API-Key: kadmin'
curl -s -X DELETE "$BASE"/v1/admin/log-level -H 'X-API-Key: kadmin'   # back to configured
```

Why the TTL exists: httpcore emits roughly **15 log lines per outbound HTTP
call**, and DEBUG deliberately *releases* the dampen set, so one `/v1/query` goes
from about 3 lines to **75–210**. The failure mode is not an attacker; it is an
admin who turned DEBUG on to investigate something and never came back. Every
accepted change is logged at **WARNING**, with the calling principal and the
before/after, on a logger pinned to WARNING so the record survives the very
change that raises the threshold. `422` — an unknown or `NOTSET` level, a body
with neither `level` nor `loggers`, a `ttl_seconds` out of range or alone, an
unknown/invalid logger name, or more overrides than `max_logger_overrides`;
nothing is applied. Schemas: `contracts/schemas/log_level_request.json`,
`log_level_response.json`.

### Operations & admin

The remaining operator surface. All of these are **admin only** except
`GET /v1/stats/stores`, which is scoped to the caller instead.

- **`GET /v1/config`** → `ConfigResponse`. The allowlisted, non-sensitive
  effective runtime configuration for the sysadmin dashboard. **Secrets are never
  returned** — API keys, passwords, DSNs and the `api_key_tenants` /
  `api_key_roles` maps are all excluded. It echoes the **raw** `LOG_LEVEL`
  string, which is why `GET /v1/admin/log-level` exists to report the
  *effective* one.
- **`GET /v1/admin/models/registry`** → the registered models for the pipeline's
  tasks (embedding, tokenizer, llm, reranker) plus the current hot-swappable
  assignments. **`POST`** registers one (**201**; `base_urls` must pass the
  server's SSRF allowlist; a **duplicate id is 400** — use `PUT` to update).
  **`PUT /v1/admin/models/registry/{model_id}`** replaces one (**200**; unknown
  id **404**). **`DELETE …/{model_id}`** removes one (**204**; **409** when a
  task is still assigned to it).
- **`PATCH /v1/admin/config/assignments`** → binds registered models to the
  **hot-swappable** tasks (`llm`, `reranker`) and applies **live**: the affected
  client is rebuilt and atomically swapped, no restart. Only the fields present
  change; a field set to `null` reverts that task to its settings default.
  Build-time tasks (embedding, chunking) are **422** — changing those builds a
  new collection, it does not reconfigure this one.
- **`POST /v1/admin/collections/evict?need=k[&dry_run=true]`** → the operator's
  handle on the active-collection bound (#359), running by hand exactly the
  policy [`POST /v1/collections`](#post-v1collections) runs automatically. Picks
  up to `need` victims (1–1000, default 1), least-recently-accessed first among
  **active** collections whose Workspace archive is current, and makes each
  `dormant`: the registry row is compare-and-swapped `active → dormant`
  **first** — readers get `503` + `Retry-After` from that instant — and only then
  are the Qdrant collection and ES index dropped, best-effort per target. Never
  evicted: one with an `accepted`/`running` ingest job, one whose stores are the
  legacy shared surface's, one whose stores are shared with another registry id.
  `dry_run=true` returns the plan and changes nothing. Schema:
  `contracts/schemas/eviction_response.json`.
- **`GET /v1/stats/stores`** → chunk/document counts for the vector, text and
  graph stores, each **filtered to the caller's readable tenants** (own +
  `public`, widened per collection by a read share) — **never a global store
  total**, so a caller can never learn another tenant's corpus size. *Which*
  collections is the caller's allowlist ∩ ownership, the same filter
  `GET /v1/collections` applies, so a count reports what a query over that
  collection would return and never more. Cost scales with the number of
  readable collections (one probe per physical store per leg) — **poll slowly**;
  the Ops dashboard uses 15 s. An unavailable store degrades to `null`.
- **`GET /v1/stats/models`** → per-role (embedding / llm / reranker) model status
  with per-endpoint reachability, latency and pool health. Admin only: `url` and
  `detail` can carry backend hostnames.
- **`POST /v1/stats/models/benchmark`** → a small, **bounded** embedding + LLM
  throughput probe with per-model timings. Admin only, and the request bounds are
  what keep it from being coaxed into a load test.

---

## Data models

**Source** (returned by query/retrieve):

| Field | Type | Notes |
|---|---|---|
| `doc_id` | string | parent document id |
| `chunk_id` | string | chunk id |
| `content` | string | chunk text |
| `score` | number | RRF fused score, or the cross-encoder score when reranking is on |
| `metadata` | object | arbitrary per-chunk metadata (see below) |
| `context` | object[] | **optional.** Neighbouring chunks of the same document, each `{ chunk_id, position, content }` and ordered by `position` (negative = before, positive = after). Present only when the request set `context_window > 0` **and** at least one neighbour is visible — never an empty list. See [Context expansion](#context-expansion-context_window) |
| `collection` | string | **optional.** The registry id this source came from. Present only on a [multi-collection](#multi-collection-retrieval-collections) request (`collections`, #253); a single-collection response is unchanged |

`doc_id`, `chunk_id`, `content` and `score` are required; the rest are optional.
Schema: `contracts/schemas/source.json`.

---

## Retrieval pipeline

A `/v1/query` (or `/v1/retrieve`) request flows through:

1. **Query expansion** — for each requested rewrite strategy (`passthrough`,
   `multiquery`, `hyde`); LLM-backed strategies are skipped if no LLM is wired.
2. **Hybrid retrieval per variant** — dense vector search (Qdrant) **+** BM25
   (Elasticsearch) **+** optional knowledge-graph leg, each tenant-scoped.
3. **RRF fusion** — Reciprocal Rank Fusion combines the ranked lists.
4. **Cross-encoder rerank** *(optional, server-config)* — when enabled, the fused
   top-`rerank_candidates` pool is rescored by the crossencoder sidecar and cut to
   `top_k`. A rerank failure falls back to the fused order. With rerank off,
   ordering and depth are unchanged.
5. **Answer generation** *(`/v1/query` only)* — an LLM grounds an answer on the
   sources; absent/failed LLM yields a retrieval-only placeholder.

The embedding model used at **query** time must match the model the corpus was
**ingested** with (vectors are model-specific); the Qdrant collection is named
`f(model, dim)` to keep models physically isolated.

---

## Metadata & filtering

`filters` is an object of equality constraints ANDed together and applied to chunk
metadata (Qdrant payload / ES keyword fields), on top of the automatic tenant
scoping. Example: `{"doc_type": "article", "year": 2021}`.

Metadata carried on each chunk depends on the loader. The bulk scholarly-corpus
loader (`ingest_jsonl.py`) stamps:

| Key | Example | Notes |
|---|---|---|
| `doc_type` | `article` | `article` / `supplement` / `front-matter` / `short` |
| `doi` | `10.1128/jvi.02415-06` | recovered from filename/text when absent |
| `doi_source` | `filename` | `metadata` / `filename` / `text` |
| `title` | `...` | when present in source metadata |
| `authors` | `["A. Smith", ...]` | list |
| `year` | `2021` | best-effort |
| `n_citations` | `42` | count (full citation list is kept in the doc-level catalog, not per chunk) |
| `tenant_id` | `public` | set server-side |

---

## Configuration (server)

Key environment variables (see `python/ragstack/config.py` for the full set):

| Var | Purpose |
|---|---|
| `API_KEYS`, `API_KEY_TENANTS` | auth keys and key→tenant map (JSON) |
| `EMBEDDING_API` | `sidecar` \| `openai` |
| `EMBEDDING_SIDECAR_URL` / `EMBEDDING_ENDPOINTS` | embedding endpoint(s); multiple → load-balanced pool |
| `EMBEDDING_MODEL`, `EMBEDDING_MODEL_DIM` | must match the ingested corpus |
| `VECTOR_BACKEND` | `qdrant` \| `memory` |
| `QDRANT_URL`, `QDRANT_TIMEOUT` | Qdrant instance; per-request bound in seconds (default **30**). Unset before #346 the client fell back to httpx's 5 s and a slow-but-healthy search surfaced as a bare 500; now a search that exceeds it is a **503** `qdrant unavailable: …` naming the collection, instance, cause and this knob |
| `TEXT_BACKEND`, `ELASTICSEARCH_INDEX` | `elasticsearch` \| `memory` for BM25 |
| `GRAPH_BACKEND`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` | `memory` (default, in-process, lost on restart) \| `neo4j` (durable property graph). `neo4j` requires the `neo4j` driver — the `graph` extra (`pip install ragstack[graph]`) — installed in **both** the API's environment **and** the worker image (`load_graph.py`, and `load_embeddings.py`'s graph replay, both construct `Neo4jGraphStore`; `extract_graph.py` does not — it only writes the extraction delta); it is not bundled by default. Constructing the store happens at API startup (`lifespan`), so a selected-but-missing driver is a **fatal boot-time error**, not a lazy failure on the first graph call (#404) |
| `RERANK_ENABLED`, `RERANK_CANDIDATES`, `CROSSENCODER_SIDECAR_URL` | cross-encoder rerank stage |
| `LLM_ENDPOINT`, `LLM_MODEL` | OpenAI-compatible chat endpoint for generation (empty → retrieval-only) |
| `INGEST_BACKEND` | `local` (default) \| `gowe`. Selects what a `POST /v1/ingest` `source` means and which gates run — see [POST /v1/ingest](#post-v1ingest). Under `gowe` the job is submitted to the workflow engine as the caller, so a **bearer BV-BRC identity is required** and `INGEST_ROOT` is never consulted |
| `INGEST_ROOT`, `MAX_DOCUMENT_BYTES` | ingest path confinement + size guard. **On `INGEST_BACKEND=local` only**, `INGEST_ROOT` unset → `POST /v1/ingest` and `POST /v1/ingest/upload` return **503** (an unset root would make ingest an arbitrary server-side file read); logged as a warning at startup. Under `gowe` nothing is read on this host and the gate is never reached. `INGEST_ROOT=/`, or a path that is not an existing directory, is **refused at startup**. Additionally required non-empty when `REQUIRE_DURABLE_BACKENDS=true` |
| `MAX_UPLOAD_FILES`, `MAX_UPLOAD_BYTES_PER_REQUEST`, `UPLOAD_CONTENT_TYPES` | `POST /v1/ingest/upload` bounds (#202): at most **50** files per request, at most **500 MB** across them, and the content-type allowlist (default `application/pdf,text/plain,text/markdown,application/xml,text/xml`; a PDF must also start with `%PDF`) → `413` / `415`. Each file is still capped by `MAX_DOCUMENT_BYTES`. Checked against the declared sizes before anything is staged or written (the multipart body has by then been received and spooled by the server); a request whose `Content-Length` exceeds the per-request cap (plus multipart framing) is refused with `413` before the body is read, and one without a `Content-Length` with `411`. **The deployment gateway must enforce a body cap ≈ `MAX_UPLOAD_BYTES_PER_REQUEST`** — a client that lies about `Content-Length` is only stopped there (see DEPLOYMENT.md). One ingest job per principal at a time (`429` + `Retry-After` while one is accepted/running and written to within the last 6 h; admins exempt) is not a setting. All three reported in `GET /v1/config` |
| `REQUIRE_DURABLE_BACKENDS` | production marker — fail fast on missing/unreachable durable backend instead of degrading to in-memory |
| `TENANT_MAX_CONCURRENCY` | per-tenant admission cap on the shared embedding fleet |
| `MAX_COLLECTIONS` | bound on **active** collections in this tenant's stores (default **100**, per ADR-0003's budget; `0` disables) — `state == active` rows of the durable registry; a `dormant` (evicted, archived) collection holds no slot (#359). Physical protection, not an authorization tier — **applies to admins too**. At the bound `POST /v1/collections` evicts one LRU archived collection and proceeds; `507` when nothing is evictable. Set it from the tenant's measured ceilings: `docs/runbooks/active-collection-bound.md` |
| `ALLOW_USER_COLLECTION_CREATE` | capability gate (default **true**, ADR-0003 behaviour) on whether a non-admin may call `POST /v1/collections` at all (#287). `false` makes creation admin-only — for a deployment that must close that plane entirely, e.g. a read-only service account where every other write already 403s a non-owner. Admins are never subject to it; reported in `GET /v1/config` |
| `MAX_COLLECTIONS_PER_OWNER` | per-**owner** collection quota (default **5**, issue #290) — counts active `owner` rows for the principal (`AclStore.count_owned`), distinct from `MAX_COLLECTIONS` above (per-tenant, physical). Enforced on **acquisition**: both `POST /v1/collections` (create) and `POST /v1/collections/{id}/owner` (transfer) 409 with a structured `{owned, limit}` detail when the acquiring subject is already at the limit, checked atomically with the owner-row write so a race yields exactly one winner. Checking transfer too is what closes the create-at-limit/transfer-away/create-again evasion and the quota-poisoning attack (transferring junk onto a colleague to fill their quota). Admins are **exempt** from this quota (logged) — unlike `MAX_COLLECTIONS`, which stays admin-inclusive (ADR-0005 decision 5). `backfill_collection_owners` never refuses over this quota; it only logs a WARNING when repairing/assigning ownership at boot pushes an owner over it. `0` disables the cap; reported in `GET /v1/config` |
| `DEFAULT_ROLE` | role for keyless/unmapped callers (default **`user`**). `researcher` is a deprecated alias for `user`; `engineer`/`manager` are rejected at startup (ADR-0003). **Never applies to a bearer identity** — see `ADMIN_SUBJECTS` |
| `ADMIN_SUBJECTS` | **bearer** subjects that are admin by operator fiat, as `issuer:subject` strings (a colon-free entry is refused at startup: it would name an API-key tenant — use `API_KEY_ROLES`). The break-glass admin source: checked first, no store read, works on an empty users table, survives a store outage, and no database write can revoke it. Only the count is logged |
| `ADMIN_ROLE_CACHE_TTL_SECONDS` | how long the bearer path memoizes the stored `users.role` (default **30**, capped at **300**, `0` disables). **This TTL is the demotion lag** — a revoked admin keeps admin for that long per worker; the granting process flushes its own cache. The lookup **fails closed** (a store outage withholds elevation) |
| `COLLECTION_STORE_BACKEND`, `COLLECTION_STORE_PATH` | collection registry (`memory` \| `json` \| `sqlite` \| `postgres`). **There is no `*_SQLITE_PATH` variant** — a wrong name silently falls back to a *relative* default that resolves against the working directory, so two servers in one checkout share one registry. `REQUIRE_DURABLE_BACKENDS=true` refuses a relative path |
| `JOB_STORE_BACKEND`, `JOB_STORE_PATH` | ingest job store; same relative-path rule |
| `USER_STORE_BACKEND`, `USER_STORE_PATH`/`USER_STORE_DSN` | the tenant's **ACL database** — user profiles *and* collection ownership/shares (`memory` \| `sqlite` \| `postgres`), per tenant like every stateful store (ADR-0005). `REQUIRE_DURABLE_BACKENDS=true` forbids `memory` here |
| `ACL_BACKFILL_OWNER` | subject that inherits ownership of pre-existing (creator-less) collections at first startup after the ACL rollout (default `legacy:admin`); those collections also get a `public` read grant so they stay world-readable exactly as before |
| `WORKSPACE_URL`, `WORKSPACE_TIMEOUT` | the BV-BRC Workspace JSON-RPC endpoint the API writes a user's collection folder (`/<user>/home/.ragstack/collections/<id>/`) and uploaded sources to, and the per-request bound in seconds (default **60**). Every call carries the **caller's** token — there is no service identity — and bytes go to Shock at the upload-node URL the Workspace returns, so no Shock endpoint is configured (#356) |
| `GRAPH_MAX_TRIPLES_PER_COLLECTION`, `GRAPH_EXTRACTION_JOBS_PER_OWNER`, `GRAPH_EXTRACTION_MAX_FAILED_FRACTION` | the graph leg's budgets (#350, #291's siblings): one collection's graph may hold at most **200,000** triples (checked once per extraction by the load tool with one live count; a load that would cross it is refused whole — job error `graph_cap_exceeded`, nothing loaded, nothing archived; `0` disables); one owner may have **1** extraction in flight (a second `POST /v1/collections/{id}/graph` is `429` + `Retry-After`; admins exempt — but a *collection* never has more than one in flight, whoever calls); and the share of a version's attempted chunks whose LLM call may fail (default **0.5**) before the extract step refuses the run as an outage — exit 1, retryable, nothing archived — so an outage never becomes a delivered empty leg |
| `GRAPH_EXTRACT_CONCURRENCY`, `GRAPH_EXTRACT_CWL`, `GRAPH_EXTRACT_WORKFLOW_NAME`, `GRAPH_EXTRACT_INPUTS_JSON` | how the extraction runs on the engine: LLM calls in flight per job (default **8**), the absolute path of `graph-extract.cwl` (empty = the repo copy), the name it registers under, and a JSON object merged over the static workflow inputs — the LLM endpoint/model and the graph store URI default to `LLM_ENDPOINT` / `LLM_MODEL` / `NEO4J_URI` *as the worker sees them*, so override them here when the worker's view differs. Neo4j credentials are never inputs: the worker reads `NEO4J_USER` / `NEO4J_PASSWORD` from its own environment; the LLM API key, if any, from its `OPENAI_API_KEY` |

---

## Bulk ingestion

For large pre-extracted JSONL corpora (`{text, path, metadata}` per line), the
operator tool streams, enriches scholarly metadata, fans out embedding across
endpoints, and is resumable:

```bash
# Run from python/. --qdrant-url and --es-url are NOT optional in practice: they
# default to :6333 and :9200, which on the deployment host are the PRODUCTION
# stores, and this is a write path that creates an index and upserts points (#454).
cd python && python scripts/ingest_jsonl.py corpus.jsonl --tenant public \
  --qdrant-url "$QDRANT_URL" --es-url "$ES_URL" \
  --embedding-api openai \
  --embedding-url http://gpu0:9001 http://gpu1:9002 \
  --embedding-model <model> \
  --text-backend elasticsearch --es-index <idx> \
  --concurrency 16 --catalog-out corpus.catalog.jsonl
```

`--catalog-out` writes the full per-document metadata catalog (including the
extracted citation list); `--no-index` produces the catalog without embedding.

---

## Errors

**Every response carries `X-Request-Id`** — a 16-hex correlation id, stamped by
middleware and **exposed to cross-origin browser clients** (along with
`Retry-After`) so a UI can show it. It is the `rid=` on every log line the
request produced, and the `Reference:` a user reads off an error screen; see
[docs/runbooks/tracing-a-503.md](runbooks/tracing-a-503.md).

**Error body** (`contracts/schemas/error.json`):

| Field | Notes |
|---|---|
| `detail` | **required and deliberately untyped.** A **string** almost everywhere; an **array** of per-field errors for FastAPI's own `422`; an **object** `{error, owned, limit, message}` for the `owner_quota_exceeded` `409`. This is server prose and may name internal hosts, collections and settings — it is for operators and for callers that log it, not for rendering to an end user |
| `request_id` | 16-hex, the same value as the `X-Request-Id` header. Redundant on purpose: a header does not survive a user pasting the JSON into a ticket. Present on the **store-unavailable 503** of `/v1/query` and `/v1/retrieve`; absent from bodies FastAPI's own handler produces |
| `reason` | `timeout` \| `unreachable` \| `error` — the failure class of that same 503. `timeout` means we connected and the search was too slow (a retry often succeeds); `unreachable` means we never reached the store (a retry probably will not help — a connect timeout is classified here, *not* as `timeout`); `error` means the store answered unhappily. **Absent on the other 503 causes**, so treat an absent or unrecognised value as the conservative case |

| Status | When |
|---|---|
| `200` | success — **including** graceful degradation (LLM/rewrite/rerank failure returns sources with a note) |
| `202` | accepted for background work — ingest upload, collection restore, graph extraction |
| `204` | deleted: a document, a share, a group, a group member, a service-account state change, an unregistered collection |
| `400` | a malformed `?cursor=` on `GET /v1/documents` (generic on purpose — the supplied value is never reflected back); a `filters` key `GET /v1/chunks` refuses while `context_window > 0`; an invalid chunk config or a non-embedding model on `POST /v1/collections`; a `permission: owner` share; a group form (`@public`, `@group:`) as an ownership-transfer `subject`; a colon-free subject on `PATCH …/role`; both an API key and an `Authorization` credential in one request; a non-Workspace `source` under `INGEST_BACKEND=gowe`; a restore without a bearer credential |
| `401` | missing, unknown or invalid credential; a disabled service account's key; or `POST /v1/collections/{id}/graph` reached with an API-key or keyless principal — the submission is made *as the user*, so there is no identity to submit with. Note the deliberate asymmetry with `POST /v1/collections/{id}/restore`, which answers **400** in that same situation: the credential there is valid and authenticated, it merely carries no Workspace token |
| `403` | authenticated but not permitted — an admin-only route or build-spec override without the role; writing or deleting a collection you don't own (only when you *can* read it; otherwise `404`); or, on an ingest that **omitted `collection`**, `no collection accepts your uploads: name a collection you own explicitly in 'collection', or create your own (POST /v1/collections)` — which names no id |
| `404` | collection not found **or** not readable by the caller (the two are deliberately indistinguishable, so access can't be probed); an unknown share/group/job/model id; or, on any implicit-target route, `no collection is accessible to this caller` — which also names no id, and is the same state as `default: ""` from `GET /v1/collections` |
| `409` | one of the busiest codes here, and always a **state** conflict, never a permission one: a duplicate active share grant, or one to the current owner; an ownership transfer replayed (it is not idempotent) or one with no active owner row; a `lost` collection; a colliding collection spec, the reserved id `default`, or residual ACL state under the id; the owner quota (`MAX_COLLECTIONS_PER_OWNER`) — the one **object** `detail`; a service-account subject that already exists as a **human** account; revoking the last stored admin of a deployment with no other admin source; deleting the built-in `public` group or revoking an active owner row; the graph/purge/unregister lifecycle guards |
| `413` | the JSON body exceeds `max_json_body_bytes` (default 1 MB) on `POST /v1/ingest`, `POST /v1/collections` or `POST /v1/collections/{id}/shares`; or `POST /v1/ingest/upload` breaks a bound — a file over `max_document_bytes`, more than `max_upload_files` files, or files totalling more than `max_upload_bytes_per_request` (#202) |
| `411` | `POST /v1/ingest/upload` without a `Content-Length` (chunked transfer) — an upload must declare its length so it can be refused before the body is read (#202) |
| `415` | an uploaded file's content type is not in `upload_content_types`, or a declared PDF has no `%PDF` header (#202) |
| `422` | request body fails validation, or a request-shape bound is exceeded (`top_k`, `GET /v1/chunks` `ids`, a list `limit`) |
| `429` | rate limit exceeded (issue #87) — see below; or, on `POST /v1/ingest/upload`, an ingest job of yours is still in flight (#202: one accepted/running job per principal; `Retry-After` set, admins exempt) |
| `502` | an upstream the request is *proxying to* refused: the Workspace listing or the workflow-engine submission on a restore or a graph extraction. The registry row is left as it was, so a retry is safe |
| `503` | a durable backend is unavailable — the request fails closed rather than degrading to open. **Four distinct causes** on `/v1/query` and `/v1/retrieve`, and only the first carries `reason`/`request_id` and a fixed 5 s `Retry-After`: (1) **the store did not answer** — including a Qdrant search that exceeds `QDRANT_TIMEOUT` (#346; `detail` starts `qdrant unavailable:`); (2) the **authorization store** could not answer (fail closed — never a silent allow); (3) the collection is **`dormant`/`restoring`** (#358 — the restore is submitted as the caller, `Retry-After` set); (4) the **tenant is at capacity** (#381). Elsewhere: ingest with no `INGEST_ROOT`, and a restore on a server with no workflow engine |
| `507` | `POST /v1/collections` only: the active-collection bound (`MAX_COLLECTIONS`) is met and **nothing can be evicted** to make room. The `detail` counts the ineligible collections per reason (`not_active`, `archive_pending`, `no_archive`, `in_flight`, `protected`, `unregistered`) |

Error responses never leak filesystem paths or upstream exception text.

> **Unknown request fields are silently ignored (#457).** Every request schema in
> `contracts/schemas/` sets `additionalProperties: false`, but the Python request
> models for `/v1/query`, `/v1/retrieve` and `/v1/ingest` do not set Pydantic's
> `extra="forbid"`, so a typo'd or invented field is dropped instead of being a
> `422`. The contract is authoritative and the implementation is the bug — do not
> rely on an unknown field being either rejected *or* honoured. The admin, group,
> share and collection bodies **do** forbid extras.

### Rate limits (issue #87)

`POST /v1/ingest`, `POST /v1/ingest/upload` (one shared bucket), `POST /v1/collections`
and `POST /v1/collections/{id}/shares` each enforce a per-principal, per-hour
budget (defaults: 10, 5, and 60 respectively) — a token bucket keyed on the
caller's tenant. Exceeding it returns `429` with a `Retry-After` header (seconds
to wait). An `admin` principal is exempt from the bucket (but not from the
request-shape bounds above); if a deployment runs keyless with `DEFAULT_ROLE=admin`,
every caller inherits that exemption and the limiter becomes a no-op — the
server logs a warning at startup when that combination is configured.

**The limiter is per API process**, with no cross-process or cross-replica
coordination: N replicas behind a load balancer give an effective ceiling of N
times the configured rate, not the configured rate itself — the same caveat
`TENANT_MAX_CONCURRENCY` documents.

A request that spends a token but is then rejected for an ordinary reason
(`422` validation, `403`/`404` authorization, `415` a bad upload) still counts
against the hour — only `401` (never reaches the limiter) and `413` (checked
before the bucket) do not. This is intentional, not a bug: a client retrying a
broken payload in a loop burns its own hour rather than getting free retries.
