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
| POST | `/v1/collections/{id}/owner` | Transfer ownership to another user — current-owner-or-admin |
| POST | `/v1/groups` | Create a group (any authenticated caller owns what they create) |
| GET | `/v1/groups` | List the groups the caller owns or belongs to |
| GET | `/v1/groups/{id}` | Group details + members (owner-or-member; non-member 404) |
| DELETE | `/v1/groups/{id}` | Delete a group (owner-or-admin; `public` not deletable) |
| POST | `/v1/groups/{id}/members` | Add a member (owner-or-admin) |
| DELETE | `/v1/groups/{id}/members/{subject}` | Remove a member (owner-or-admin) |
| POST | `/v1/admin/service-accounts` | Register a machine identity (admin only) |
| GET | `/v1/admin/service-accounts` | List registered service accounts (admin only) |
| POST | `/v1/admin/service-accounts/{subject}/disable` | Soft-revoke an account's key (admin only) |
| POST | `/v1/admin/service-accounts/{subject}/enable` | Re-enable a disabled account (admin only) |
| PATCH | `/v1/admin/users/{subject}/role` | Grant/revoke the admin role for a federated user (admin only) |
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
| `collection` | string \| null | null | registry collection id to query; null = the default collection. Unknown **or unreadable** → `404` |
| `retrieval_mode` | `hybrid` \| `vector` \| `bm25` | `hybrid` | which retrieval legs run: dense + BM25 fused, dense only, or keyword only. Graph leg is orthogonal (`use_graph`) |
| `rerank` | bool \| null | null | force the cross-encoder on/off for this request; null keeps the server setting (rerank iff a reranker is configured) |
| `rerank_candidates` | int \| null | null | candidate-pool depth fed to the reranker; null = `max(top_k, RERANK_CANDIDATES)` |
| `context_window` | int (0–3) | 0 | server-side [context expansion](#context-expansion-context_window): walk each returned source's `prev_chunk_id` / `next_chunk_id` this many hops each way and attach the neighbours as the source's `context`. `0` = off (response unchanged); above `3` → `422` |
| `llm` | string \| null | null | registered model id to generate with, this request only (`GET /v1/models/available`); unknown → 404, wrong task → 400 |
| `reranker` | string \| null | null | registered model id to rerank with, this request only |

**Response** (`QueryResponse`): `{ answer, sources[], rewritten_queries[] }`. Each
source is `{ doc_id, chunk_id, content, score, metadata, context? }`; on API-ingested and
current bulk-loaded corpora `metadata` carries `chunk_index`, `prev_chunk_id` and
`next_chunk_id` for client-side [context expansion](#get-v1chunks). `context` is
present only when the request set `context_window > 0` and at least one neighbour
is visible — see [Context expansion](#context-expansion-context_window).

```bash
curl -s http://localhost:8000/v1/query \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"query": "how do viruses evade innate immunity?", "top_k": 5}'
```

### POST /v1/retrieve

Same retrieval (hybrid + optional rerank) but no answer generation.

**Request** (`RetrieveRequest`): `query` (required), `top_k` (5), `filters` (`{}`),
`use_graph` (true), plus the same `collection`, `retrieval_mode`, `rerank`,
`rerank_candidates`, `context_window` and `reranker` fields as `/v1/query`. **Response**
(`RetrieveResponse`): `{ sources[] }`.

```bash
curl -s http://localhost:8000/v1/retrieve \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"query": "mechanisms of antibiotic resistance", "top_k": 10,
       "filters": {"doc_type": "article"}}'
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
  `(context after)` delimiters; citations still number the sources.
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
own neighbour ids — the ids are the cursor). `collection` defaults to the
default collection. Tenant-scoped like every read (own + `public`): ids that do
not exist or that the caller may not read are **silently omitted**; order
follows the request. At a document's first/last chunk the neighbour id is
absent (older bulk loads stamped the literal string `"None"`).

```bash
curl -s "http://localhost:8000/v1/chunks?collection=open-access&ids=<prev_id>,<next_id>" \
  -H 'X-API-Key: kp'
# {"chunks":[{"doc_id":"…","chunk_id":"…","content":"…","metadata":{…}}, …]}
```

`404` — unknown or unreadable collection. `422` — more than `max_chunk_ids`
ids. `503` — authorization store unavailable.

### POST /v1/collections

Create a collection. `id` and `label` are optional; omitting `embedding` and `chunk`
builds from the **server-default build spec** (resolved to concrete values at create
time, so later default changes never re-identify an existing collection). Supplying
`embedding` or `chunk` is an **admin-only** override → `403` otherwise. `409` when the
spec collides with an existing collection; `507` when the `max_collections` bound on
**active** collections is met and nothing can be evicted to make room (see below).
Full schema: `contracts/schemas/collection_create_request.json`.

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
| `@service:<subject>` | a **service account** (#258) — the subject is kept **colon-free**, i.e. exactly the string its API key authenticates as |
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
curl -s -X POST http://localhost:8000/v1/collections/my-papers/shares \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"grantee": "alice"}'                 # -> 201, grantee_id "bvbrc:alice"
curl -s -X POST http://localhost:8000/v1/collections/my-papers/shares \
  -H 'X-API-Key: kp' -H 'Content-Type: application/json' \
  -d '{"grantee": "@service:svc-askclark"}' # -> 201, grantee_id "svc-askclark"
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
curl -s -X POST http://localhost:8000/v1/collections/my-papers/owner \
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
| `QDRANT_URL`, `QDRANT_TIMEOUT` | Qdrant instance; per-request bound in seconds (default **30**). Unset before #346 the client fell back to httpx's 5 s and a slow-but-healthy search surfaced as a bare 500; now a search that exceeds it is a **503** `qdrant unavailable: …` naming the collection, instance, cause and this knob |
| `TEXT_BACKEND`, `ELASTICSEARCH_INDEX` | `elasticsearch` \| `memory` for BM25 |
| `RERANK_ENABLED`, `RERANK_CANDIDATES`, `CROSSENCODER_SIDECAR_URL` | cross-encoder rerank stage |
| `LLM_ENDPOINT`, `LLM_MODEL` | OpenAI-compatible chat endpoint for generation (empty → retrieval-only) |
| `INGEST_ROOT`, `MAX_DOCUMENT_BYTES` | ingest path confinement + size guard. `INGEST_ROOT` unset → `POST /v1/ingest` returns **503** (an unset root would make it an arbitrary server-side file read); logged as a warning at startup. `INGEST_ROOT=/`, or a path that is not an existing directory, is **refused at startup**. Additionally required non-empty when `REQUIRE_DURABLE_BACKENDS=true` |
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
| `413` | the JSON body exceeds `max_json_body_bytes` (default 1 MB) on `POST /v1/ingest`, `POST /v1/collections` or `POST /v1/collections/{id}/shares`; or `POST /v1/ingest/upload` breaks a bound — a file over `max_document_bytes`, more than `max_upload_files` files, or files totalling more than `max_upload_bytes_per_request` (#202) |
| `411` | `POST /v1/ingest/upload` without a `Content-Length` (chunked transfer) — an upload must declare its length so it can be refused before the body is read (#202) |
| `415` | an uploaded file's content type is not in `upload_content_types`, or a declared PDF has no `%PDF` header (#202) |
| `422` | request body fails validation, or a request-shape bound is exceeded (`top_k`, `GET /v1/chunks` `ids`, a list `limit`) |
| `429` | rate limit exceeded (issue #87) — see below; or, on `POST /v1/ingest/upload`, an ingest job of yours is still in flight (#202: one accepted/running job per principal; `Retry-After` set, admins exempt) |
| `503` | a durable backend (including the authorization store) is unavailable — the request fails closed rather than degrading to open. Since #346 this includes a Qdrant search that exceeds `QDRANT_TIMEOUT` (`detail` starts `qdrant unavailable:`; a `Retry-After` header is set) |

Error responses never leak filesystem paths or upstream exception text.

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
