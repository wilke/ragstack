# RAGStack API Reference

HTTP API for the RAGStack Retrieval-Augmented Generation platform. The surface is
defined contract-first in [`contracts/openapi.yaml`](../contracts/openapi.yaml)
(OpenAPI 3.1) with JSON Schemas under `contracts/schemas/`; that contract is
authoritative and both implementations conform to it.

- **Python** (FastAPI) — default port **8000**
- **Go** (Chi) — default port **8080**

All examples below use `http://localhost:8000`. Interactive docs are served by the
Python app at `/docs` (Swagger UI) and `/redoc`.

---

## Authentication & tenancy

Auth is an API key passed in the **`X-API-Key`** header. The key maps to a
**tenant** server-side (via `API_KEY_TENANTS`); the tenant is **never** taken from
the request body, so a client cannot widen its own scope.

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
- **Ingest into the default collection and `DELETE /v1/documents/{id}`** need
  **read** access to the (shared, backfilled-public) default collection: it is
  the pre-ownership multi-tenant surface, where the per-chunk tenant stamp — not
  collection ownership — isolates writers (each write/delete only ever touches
  the caller's own tenant's chunks).
- **`DELETE /v1/collections/{id}`** also **revokes every ACL row** of the
  collection (softly — audit history survives), so a later collection reusing
  the same id never inherits the deleted one's owner row or `public` grant.
- **`DELETE /v1/collections/{id}`** is **owner-or-admin** — no longer admin-only:
  a user manages its own private collections. `admin` bypasses every check (a
  named, logged branch), for purge/migration/support.
- When authentication is unconfigured (keyless dev), collection-ownership
  enforcement is a no-op — the open dev path, exactly as tenant auth is; production
  (`REQUIRE_DURABLE_BACKENDS`) forbids keyless and requires a durable ACL store
  (`USER_STORE_BACKEND` ≠ `memory`).
- An authorization-store outage is a **503** (fail closed) — never a silent allow.
- A key absent from the map resolves to the `default` tenant. If no API keys are
  configured at all (dev mode), requests are unauthenticated and use `default`.
- A request with an unknown key returns **401**.

```bash
curl -s http://localhost:8000/v1/query \
  -H 'X-API-Key: <your-key>' -H 'Content-Type: application/json' \
  -d '{"query": "..."}'
```

`/health` is open (no key required). All `/v1/*` routes require the header when
keys are configured.

---

## Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/v1/query` | Full RAG: rewrite → retrieve → rerank → generate |
| POST | `/v1/retrieve` | Retrieve chunks only (no answer) |
| POST | `/v1/collections` | Create a collection (any principal; `embedding`/`chunk` overrides admin-only) |
| GET | `/v1/collections/{id}/shares` | List a collection's shares + owner (owner-or-admin) |
| POST | `/v1/collections/{id}/shares` | Grant a read share (or publish via `@public`) — owner-or-admin |
| DELETE | `/v1/collections/{id}/shares/{share_id}` | Revoke a share (un-publish) — owner-or-admin |
| POST | `/v1/groups` | Create a group (any authenticated caller owns what they create) |
| GET | `/v1/groups` | List the groups the caller owns or belongs to |
| GET | `/v1/groups/{id}` | Group details + members (owner-or-member; non-member 404) |
| DELETE | `/v1/groups/{id}` | Delete a group (owner-or-admin; `public` not deletable) |
| POST | `/v1/groups/{id}/members` | Add a member (owner-or-admin) |
| DELETE | `/v1/groups/{id}/members/{subject}` | Remove a member (owner-or-admin) |
| POST | `/v1/ingest` | Ingest a file/directory (async job) |
| GET | `/v1/ingest/{job_id}` | Poll ingest job status |
| GET | `/v1/documents` | List indexed documents |
| DELETE | `/v1/documents/{doc_id}` | Delete a document + its chunks |
| GET | `/v1/graph/entities` | List knowledge-graph entities |
| GET | `/v1/graph/neighbors/{entity}` | Entity neighborhood triples |

### GET /health

```bash
curl -s http://localhost:8000/health
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
| `stream` | bool | false | reserved |

**Response** (`QueryResponse`): `{ answer, sources[], rewritten_queries[] }`

```bash
curl -s http://localhost:8000/v1/query \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"query": "how do viruses evade innate immunity?", "top_k": 5}'
```

### POST /v1/retrieve

Same retrieval (hybrid + optional rerank) but no answer generation.

**Request** (`RetrieveRequest`): `query` (required), `top_k` (5), `filters` (`{}`),
`use_graph` (true). **Response** (`RetrieveResponse`): `{ sources[] }`.

```bash
curl -s http://localhost:8000/v1/retrieve \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"query": "mechanisms of antibiotic resistance", "top_k": 10,
       "filters": {"doc_type": "article"}}'
```

### POST /v1/collections

Create a collection. `id` and `label` are optional; omitting `embedding` and `chunk`
builds from the **server-default build spec** (resolved to concrete values at create
time, so later default changes never re-identify an existing collection). Supplying
`embedding` or `chunk` is an **admin-only** override → `403` otherwise. `409` when the
spec collides with an existing collection; `403` when the `max_collections` cap is
reached. Full schema: `contracts/schemas/collection_create_request.json`.

The new collection is **owned by its creator and private by default** — no other
caller can read it until it is shared (see *Collection ownership* above). The
creator is recorded on the durable spec itself and the owner row is written right
after the registry write; if the owner row cannot be recorded the create is
**rolled back** (409 for residual ACL state under the id, 503 for a store outage)
rather than returning a 201 whose ownership silently never landed. A crash inside
that window self-heals: the startup backfill repairs the owner row from the
spec-recorded creator (privately — it never publishes a spec-owned collection).

```bash
curl -s http://localhost:8000/v1/collections \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"id": "my-papers", "label": "My papers"}'
```

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
| a value containing `:` | a full `issuer:subject` string, kept **verbatim** (issuer/subject halves must both be non-empty) |
| a bare username | prefixed with `issuer` (default `bvbrc`) → `bvbrc:<username>` |

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
curl -s -X POST http://localhost:8000/v1/collections/my-papers/shares \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"grantee": "alice"}'                 # -> 201, grantee_id "bvbrc:alice"
curl -s -X POST http://localhost:8000/v1/collections/my-papers/shares \
  -H 'X-API-Key: kp' -d '{"grantee": "@public"}'   # publish (read to everyone)
curl -s http://localhost:8000/v1/collections/my-papers/shares -H 'X-API-Key: kp'
curl -s -X DELETE http://localhost:8000/v1/collections/my-papers/shares/<share_id> \
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
  `subject` resolves exactly like a share grantee (full `issuer:subject` verbatim,
  or a bare BV-BRC username → `bvbrc:<username>`) and is echoed back; a
  never-logged-in user is pre-provisioned. Membership is a **flat list of users**
  — a group id (any `@group:`/`@public` form) is rejected (**422**, no nesting); a
  duplicate active membership or the built-in `public` group → **409**.
- **`DELETE /v1/groups/{id}/members/{subject}`** (optional `?issuer=` query,
  default `bvbrc`) → **204**. **Owner-or-admin**. `subject` resolves the **same
  way as on add** — a full `issuer:sub` string verbatim, a bare BV-BRC username →
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

### POST /v1/ingest

Accepts a file or directory `source` (resolved within `INGEST_ROOT`) and processes
it in the **background**, returning immediately with a `job_id`. **`INGEST_ROOT`
must be configured**: with it unset the endpoint returns `503` on every request,
because an unconfined `source` is an arbitrary server-side file read whose text is
retrievable back through `/v1/retrieve`. A directory is
ingested recursively (`.pdf`/`.txt`/`.md`/`.jsonl`), one document per item.
Re-ingesting the same source **replaces** that document's chunks (deterministic
document id) rather than duplicating; a re-ingest that yields no embeddable chunks
fails the job and leaves the prior version intact.

> For multi-hundred-MB JSONL corpus dumps, use the operator tool
> `python/scripts/ingest_jsonl.py` instead — it streams, fans out across embedding
> endpoints, and bypasses the per-file size guard. See [Bulk ingestion](#bulk-ingestion).

**Request** (`IngestRequest`): `source` (required), `metadata` (`{}`).

**Response** (`IngestResponse`): `{ job_id, status, chunk_ids[], items? }`.

```bash
curl -s http://localhost:8000/v1/ingest \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"source": "papers/2024_review.pdf"}'
# {"job_id": "...", "status": "accepted"}
```

### GET /v1/ingest/{job_id}

Polls status: `accepted` → `running` → `completed` | `failed` (unknown id →
`unknown`, HTTP 200). Batch/directory jobs include `items`:
`{ total, completed, failed, pending }`.

```bash
curl -s http://localhost:8000/v1/ingest/<job_id> -H 'X-API-Key: kp'
```

### GET /v1/documents · DELETE /v1/documents/{doc_id}

List documents (`DocumentInfo[]`: `{ doc_id, source, metadata }`) — **not yet
implemented: currently returns `[]`** (needs a document-registry metadata store;
the vector store holds chunks, not documents). Or delete one document and all
its chunks (scoped to the caller's tenant; **204** on success).

### GET /v1/graph/entities · GET /v1/graph/neighbors/{entity}

List KG entities (`EntityInfo[]`: `{ name, triple_count }`) or fetch an entity's
neighborhood triples (`TripleResponse[]`: `{ subject, predicate, object }`,
optional `?depth=` query param, default 1). Requires the graph backend (Neo4j) to
be configured; otherwise these return empty.

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
| `TEXT_BACKEND`, `ELASTICSEARCH_INDEX` | `elasticsearch` \| `memory` for BM25 |
| `RERANK_ENABLED`, `RERANK_CANDIDATES`, `CROSSENCODER_SIDECAR_URL` | cross-encoder rerank stage |
| `LLM_ENDPOINT`, `LLM_MODEL` | OpenAI-compatible chat endpoint for generation (empty → retrieval-only) |
| `INGEST_ROOT`, `MAX_DOCUMENT_BYTES` | ingest path confinement + size guard. `INGEST_ROOT` unset → `POST /v1/ingest` returns **503** (an unset root would make it an arbitrary server-side file read); logged as a warning at startup. `INGEST_ROOT=/`, or a path that is not an existing directory, is **refused at startup**. Additionally required non-empty when `REQUIRE_DURABLE_BACKENDS=true` |
| `REQUIRE_DURABLE_BACKENDS` | production marker — fail fast on missing/unreachable durable backend instead of degrading to in-memory |
| `TENANT_MAX_CONCURRENCY` | per-tenant admission cap on the shared embedding fleet |
| `MAX_COLLECTIONS` | cap on collections in this tenant's stores (default **100**, per ADR-0003's budget; `0` disables). Physical protection, not an authorization tier — **applies to admins too**; `POST /v1/collections` returns 403 at the cap |
| `DEFAULT_ROLE` | role for keyless/unmapped callers (default **`user`**). `researcher` is a deprecated alias for `user`; `engineer`/`manager` are rejected at startup (ADR-0003) |
| `USER_STORE_BACKEND`, `USER_STORE_PATH`/`USER_STORE_DSN` | the tenant's **ACL database** — user profiles *and* collection ownership/shares (`memory` \| `sqlite` \| `postgres`), per tenant like every stateful store (ADR-0005). `REQUIRE_DURABLE_BACKENDS=true` forbids `memory` here |
| `ACL_BACKFILL_OWNER` | subject that inherits ownership of pre-existing (creator-less) collections at first startup after the ACL rollout (default `legacy:admin`); those collections also get a `public` read grant so they stay world-readable exactly as before |

---

## Bulk ingestion

For large pre-extracted JSONL corpora (`{text, path, metadata}` per line), the
operator tool streams, enriches scholarly metadata, fans out embedding across
endpoints, and is resumable:

```bash
python scripts/ingest_jsonl.py corpus.jsonl --tenant public \
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

| Status | When |
|---|---|
| `200` | success — **including** graceful degradation (LLM/rewrite/rerank failure returns sources with a note) |
| `204` | document deleted |
| `401` | unknown/invalid API key |
| `403` | authenticated but not permitted — supplying an admin-only build-spec override, or writing/deleting a collection you don't own (only when you *can* read it; otherwise `404`) |
| `404` | collection not found **or** not readable by the caller (the two are deliberately indistinguishable, so access can't be probed) |
| `422` | request body fails validation |
| `503` | a durable backend (including the authorization store) is unavailable — the request fails closed rather than degrading to open |

Error responses never leak filesystem paths or upstream exception text.
