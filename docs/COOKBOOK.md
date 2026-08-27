# RAGStack cookbook

Task-indexed. Each recipe is a question someone actually asks, with the answer in the UI
and over the API where both exist, and what goes wrong.

**Reference material lives elsewhere and is not repeated here:** [API.md](API.md) for the
endpoint reference, [USER-GUIDE.md](USER-GUIDE.md) for the guided walkthrough,
[GLOSSARY.md](GLOSSARY.md) for tenant/collection/store, [DEPLOYMENT.md](DEPLOYMENT.md) and
[runbooks/](runbooks/) for operations.

## Before anything: set `BASE`

```bash
export BASE=https://<host>:9000/ragstack/<tenant>/api   # through the gateway
export BASE=http://localhost:8000                        # a dev server you started yourself
```

There is deliberately **no default**. On the deployment host `http://localhost:8000` is the
legacy **production** API, and half the recipes below write. The gateway strips the
`/ragstack/<tenant>/api` prefix before the app sees the request.

---

# Part 1 — For people using a deployment

## Which credential does this deployment want?

Two exist. Which one works is a property of the deployment, not of you.

| Header | What it is |
|---|---|
| `X-API-Key: <key>` | A key an operator issued. Always available. |
| `Authorization: <token>` | Your own identity, verified by the configured provider. Only when `IDENTITY_PROVIDER` is `bvbrc` or `oidc` — while it is `none` (the default) this header **is not an authentication input at all**, and is silently ignored. |

The `Bearer ` prefix is optional; BV-BRC's token format carries no scheme.

**Never send both.** With a provider configured that is a 400:

> `present exactly one credential: X-API-Key or Authorization, not both`

An **empty** `X-API-Key:` header still counts as present, so it trips the same 400.

**UI** — the header chip → `Sign in`. The provider dropdown offers BV-BRC, Google (listed
but not available, with the reason shown) and API key. For BV-BRC your password goes
**straight from your browser to `user.patricbrc.org`** — RAGStack never sees it and has no
endpoint that would accept it. You can also paste a token you already have.

## It says "Not signed in" / "That credential was rejected"

These are different states and the difference is deliberate.

- **"Not signed in"** — the check came back 401 and **no credential was attached**. That is
  a definitive answer about nobody, so it is safe to say you are signed out.
- **"That credential was rejected"** — 401 *with* a credential attached. The token may be
  expired, or this backend may not accept it.
- Anything that is **not** a 401 — a 503, or no response at all (offline, CORS, DNS) — is
  never reported as signed out. "The backend is down" is not "you are logged out".

There is a third case worth knowing: a **200 proves nothing about a token**. If the
deployment has no identity provider, your token is ignored and the request succeeds as the
`default` tenant. The UI detects this and says so rather than showing you as signed in.

The check itself is `GET /v1/stats/tenants?counts=false` — there is no `/v1/whoami`.

```bash
curl -s "$BASE"/v1/stats/tenants?counts=false -H "X-API-Key: $KEY"
# {"tenant":"…","role":"user","readable":["…","public"],"auth_enabled":true,"tenants":[…]}
```

`auth_enabled` reports whether **API keys** are configured. It says nothing about the
identity provider.

## Can I query without signing in?

Only when the deployment has no API keys configured **and** no identity provider. Then
every caller is the `default` tenant and ACL enforcement is a no-op.

If keys are configured, an anonymous request is `401 missing or invalid API key`.

`GET /health` never needs a credential.

## Which collection am I searching?

```bash
curl -s "$BASE"/v1/collections -H "X-API-Key: $KEY"
```

The response has two fields that both look like "the default", and they are **not the same
thing**:

| Field | What it means |
|---|---|
| `default` (top level, a string) | **Your** default — the collection your request targets when you omit `collection`. `""` means you can read none. |
| `is_default` (per collection, a boolean) | The **global** registry pointer. True on at most one entry — and on **zero** entries when you cannot read the one it points at. |

Reading `is_default` to decide where your query goes is exactly bug #419. Use the top-level
`default`.

Resolution order: the registry pointer **when you can see it**, otherwise the **first entry
in your list** — in listing order, which is why "the first one in your list" is literally
true on screen.

**UI** — on Explore the collection chip *is* the picker; the same control appears on
Collections, and Compare gives each lane its own.

## How do I ask a question and read the sources?

```bash
curl -s -X POST "$BASE"/v1/query \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"query":"how do efflux pumps confer multidrug resistance?","top_k":5}'
```

Response: `answer`, `sources`, `rewritten_queries`. Each source carries `doc_id`,
`chunk_id`, `content`, `score`, and optionally `metadata`, `collection` (multi-collection
requests only) and `context`.

Useful fields: `collection` to target one explicitly; `collections` for 1–5 at once
(mutually exclusive with `collection`); `retrieval_mode` (`hybrid` | `vector` | `bm25`);
`rerank`; `filters`; `context_window`.

**A missing LLM is not an error.** With none configured, or when generation fails, you get
**200** with a synthesized answer naming the chunk count and top score, and the sources
intact. Check the answer text before concluding the model is broken.

Use `POST /v1/retrieve` when you want the passages and no generated answer — same fields
minus `llm`, `rewrite_strategies` and `stream`, and the response is just `sources`.

## How do I see the text around a hit?

Two ways.

**Server-side, in the same request** — `context_window` (0–3, default 0; above 3 is a 422)
attaches neighbouring chunks to each source as `context`, with `position` `-1` for the
preceding chunk and `1` for the following. Applied *after* ranking, so it never changes
what came back or in what order.

**Separately** — `GET /v1/chunks?ids=a,b,c`. Up to **200** ids (`max_chunk_ids`); more is a
422, never a silent truncation. Unknown ids are silently omitted, and order follows your
request.

**UI** — the Evidence tab's source viewer walks the document with **‹ prev / next ›** and
*back to match*. Walked neighbours render without a score, because they were never ranked.

## How do I make my own collection?

```bash
curl -s -X POST "$BASE"/v1/collections \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"id":"my-papers","label":"My papers"}'      # 201
```

Every field is optional. `id` names a *library* and must be unique deployment-wide; omit it
and you get a content-addressed corpus over the server's default build spec. The
`embedding` and `chunk` overrides are **admin-only** (403 otherwise).

| You get | It means |
|---|---|
| **403** | Creation is disabled for non-admins (`ALLOW_USER_COLLECTION_CREATE=false`) — ask an operator. |
| **409** with an object `detail` | You are at your personal quota (`MAX_COLLECTIONS_PER_OWNER`, default **5**). The body carries `owned` and `limit`. Free one up by deleting or transferring it. |
| **409** otherwise | That id is taken, or an identical spec already exists. |
| **507** `insufficient_storage` | The deployment is at its collection cap and nothing could be evicted. |

**UI** — Collections → `＋ New collection`. Note the typed name becomes the collection
**id**, not just its label.

## How do I upload files, and know when they are searchable?

```bash
curl -s -X POST "$BASE"/v1/ingest/upload \
  -H "X-API-Key: $KEY" \
  -F 'files=@paper1.pdf' -F 'files=@paper2.pdf' \
  -F 'collection=my-papers'                        # 202
# {"job_id":"…","status":"accepted","collection":"my-papers"}

curl -s "$BASE"/v1/ingest/<job_id> -H "X-API-Key: $KEY"
```

The form field is **`files`**, repeated. `collection` is optional — see the next recipe for
what omitting it targets. The 202 echoes `collection`, which is how you learn where an
implicit upload actually landed.

Polling is **always 200**. Statuses: `accepted` → `running` → `completed` | `failed`, plus
`unknown`. An unrecognised job id **and someone else's job id** both return `unknown`, so
the endpoint never confirms a foreign job exists.

| Refusal | Cause |
|---|---|
| **415** | Not an accepted type. Defaults: PDF, plain text, Markdown, XML. A file claiming to be a PDF must actually start with `%PDF`. |
| **413** | Too big: 50 files, 500 MB per request, 50 MB per document — or the whole body exceeded the cap before a byte was read. |
| **411** | No `Content-Length`. Chunked uploads are not accepted. |
| **429** | You already have an ingest job in flight. Poll it; `Retry-After` is a hint, not a promise. |

**UI** — Collections, step 2. The picker says "Upload PDFs" and only offers PDFs, which is
stricter than the API.

## Why was my upload refused when I can read the collection fine?

Because reading and writing resolve **differently**. An omitted `collection` on ingest
targets what you can *write*, not what you can *read*.

| | Status | What it means |
|---|---|---|
| `no collection accepts your uploads: name a collection you own explicitly in 'collection', or create your own (POST /v1/collections)` | **403** | You can read something, but nothing accepts your writes. |
| `no collection is accessible to this caller` | **404** | You can read nothing at all. Same state as `default: ""` and an empty list. |

Neither names a collection id — the server chose it, so naming it would disclose something
you were never shown.

**A collection you name explicitly is never rerouted.** It is used or refused, never
silently swapped for one the server picked.

## How do I share a collection?

```bash
# one person (a bare name is qualified to bvbrc:<name>)
curl -s -X POST "$BASE"/v1/collections/my-papers/shares \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"grantee":"alice"}'

# everyone
  -d '{"grantee":"@public"}'
# a group that already exists
  -d '{"grantee":"@group:lab-team"}'
# a machine account — the @service: form matters, see below
  -d '{"grantee":"@service:svc-web"}'

curl -s "$BASE"/v1/collections/my-papers/shares -H "X-API-Key: $KEY"
curl -s -X DELETE "$BASE"/v1/collections/my-papers/shares/<share_id> -H "X-API-Key: $KEY"
```

The 201 echoes the **resolved** subject, so a typo is visible immediately.

- Shares are **read-only** in v1: asking for `write` is a 422.
- A bare name is qualified to `bvbrc:<name>`. A service account must therefore be granted
  as `@service:<subject>` — a bare subject becomes a federated identity the machine can
  never authenticate as, which is an inert grant that looks like it worked.
- Granting to a group that does not exist is a **422** telling you to create it first.
- The **owner row is not revocable** through the share API (409). Delete or transfer the
  collection instead.

**UI** — Collections → `Share`. `Make public` toggles the one `@public` row. The button
shows for any collection, because the listing exposes no ownership; a non-owner discovers
the refusal as a 403 the dialog explains.

## How do I hand a collection to someone else?

**API only — there is no UI for this.**

```bash
curl -s -X POST "$BASE"/v1/collections/my-papers/owner \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"subject":"bob"}'
```

Users only — a group is a 400. The transfer is atomic and **non-cascading**: every other
share survives.

**The outgoing owner loses access.** No consolation read share is minted. If you want to
keep reading it, grant yourself one afterwards — that is a second, explicit call. The
response tells you which way it went in `previous_owner_retains_read`.

A recipient at their own quota is a 409.

## How do I use groups?

```bash
curl -s -X POST "$BASE"/v1/groups -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' -d '{"name":"lab-team"}'

curl -s -X POST "$BASE"/v1/groups/<id>/members -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' -d '{"subject":"alice"}'
```

Any authenticated caller may create a group and becomes its owner; only the owner or an
admin may add or remove members. `GET /v1/groups` lists what you own or belong to. Members
may be users only — no nesting.

A member added before they have ever signed in is pre-provisioned, and the membership is
claimed on first login. Removing someone who is not a member is a 204 no-op.

**UI** — Ops tab → Groups. Not admin-gated.

## The search returned 503. Should I retry?

Read `reason` in the body.

| `reason` | Meaning | Retry? |
|---|---|---|
| `timeout` | We reached the store; the search took longer than allowed. | **Yes** — usually a large collection warming up; the second read is often fast. |
| `unreachable` | We never reached the store at all. | Probably not. A connect timeout lands here deliberately, so nothing promises you a warm second read. |
| `error` | The store answered unhappily. | No promise. |
| **absent** | Not a store failure. Authorization store down, the collection is dormant or restoring, or the tenant is at capacity. | Usually yes, shortly. |

Note the body's `request_id`, or the `Reference:` line the UI shows. That is the id an
operator greps for. Give it to them; it is worth more than a description of what you did.

## Why do I see fewer collections than my colleague?

Visibility is **the deployment's per-tenant allowlist, intersected with what you may read**.
You may read a collection when you are an admin, you own it, someone granted it to you
directly, it is shared with `@public`, or it is shared with a group you belong to.

Anything else is simply absent from your listing — and naming it explicitly returns **404**,
identical to an id that does not exist. That is deliberate: a private collection cannot be
probed for existence.

Counts differ too. They are scoped per caller, so a collection reached through a share
reports the owner's chunk count.

## What are the Compare and Evidence tabs for?

**Compare** runs one query across several lanes side by side — each lane its own collection
and its own retrieval settings — and reports how much the lanes agree: shared and unique
documents, overlap, the fastest lane. It compares by document and rank, never by comparing
scores across lanes, because scores from different configurations are not comparable.

**Evidence** takes one answer apart: the pipeline that produced it, the answer split into
claims, the graph entities the answer mentions with their neighbours, and the selected
source in place. Sentence highlighting is a client-side approximation and is labelled as
one — the API returns no chunk-relative match offsets — and claims render ungraded. Nothing
in that view invents a number the API did not return.

---

# Part 2 — For developers integrating against the API

## What base URL do I use, and does `/docs` work behind the gateway?

Direct: `http://<host>:8000` (Python) or `:8080` (Go). Through the gateway:
`https://<host>:9000/ragstack/<tenant>/api`.

The gateway strips the prefix, so the app only ever sees `/v1/...`. It learns the prefix
back from `X-Forwarded-Prefix` and uses it to emit correct absolute URLs — which is what
makes `/docs` and `/openapi.json` work through the gateway rather than 404 (#332).

The header is validated, not trusted: one leading slash, a restricted charset, no `..`
segments. `ROOT_PATH` **pins** the prefix instead, and an invalid value fails closed rather
than falling back to the header. Setting `ROOT_PATH=/v1` 404s the whole API.

## `/v1/query` or `/v1/retrieve`?

`/v1/retrieve` returns `sources` and stops. `/v1/query` runs rewrite → retrieve → rerank →
generate and returns `answer`, `sources`, `rewritten_queries`.

`/v1/query` **degrades to 200**, never to an error: with no LLM configured, or on a
generation failure, the answer text says so and the sources are intact. Do not treat a 200
as proof that a model ran.

Shared fields and their defaults: `top_k` 5 (max 100), `filters` `{}`, `use_graph` true,
`rerank` null (server default; `false` forces skip), `rerank_candidates` null, `collection`
null, `collections` null (1–5, mutually exclusive with `collection`), `retrieval_mode`
`hybrid`, `context_window` 0, `reranker` null. `/v1/query` adds `rewrite_strategies`
(`["passthrough"]`), `llm`, and `stream`.

**Two things to know before you build against this:**

- **Unknown fields are silently ignored** (#457). Every request schema says
  `additionalProperties: false`, but the models do not enforce it, so `rerank_candidate`
  (singular) is dropped and the request succeeds with defaults. Check your field names
  against the schema; the server will not.
- **`stream` is accepted and does nothing** (#458). It is in the schema and inert.

On a multi-collection request every id is resolved and read-authorized **before** any
retrieval runs: one unreadable member 404s the whole request, one dormant member 503s it.
No partial answers.

## What does an omitted `collection` target?

This is the behaviour most likely to surprise you, because **reads and writes resolve
differently**.

Both start from the same visible set — the per-tenant allowlist intersected with what you
can read — in **insertion order**, and both pick the registry pointer when you can see it,
otherwise the first visible entry.

**Read paths** (`/v1/query`, `/v1/retrieve`, `/v1/chunks`, `GET`/`DELETE /v1/documents`)
stop there.

**Ingest paths** (`POST /v1/ingest`, `/v1/ingest/upload`) apply one more filter: candidates
must be **writable** — you own it, you are an admin, or it is the legacy shared surface
where per-chunk tenant stamping is the write isolation. Writability narrows readability and
never widens it.

Hence two distinct refusals:

| Constant | Status | Body |
|---|---|---|
| `NO_ACCESSIBLE_COLLECTION` | **404** | `no collection is accessible to this caller` |
| `NO_WRITABLE_COLLECTION` | **403** | `no collection accepts your uploads: name a collection you own explicitly in 'collection', or create your own (POST /v1/collections)` |

Neither names an id. The 403 states a *rule* you can act on; it is not claiming something
does not exist.

**A named collection you cannot access returns 404, not 403** — byte-identical to an
unknown id: `unknown collection 'x'; see GET /v1/collections`. Write and owner denials
return 403 *only when you can read the collection anyway*. Without this, write endpoints
would be an existence oracle: probe with a guessed id, 403 means it exists.

The reserved id `"default"` is a pointer, not a collection: naming it is the same as
omitting it.

## How do I expand context around a hit?

`context_window` (0–3) on query/retrieve does it server-side, attaching `context` entries
with a `position` offset — no second round trip, ranking untouched.

`GET /v1/chunks?ids=…&collection=…` fetches by id: up to 200, order preserved, unknown ids
silently omitted, over the limit is a 422.

## How do I filter, and pick a model per request?

`filters` keys are **bare chunk-metadata field names** — `{"journal":"mBio"}`, ANDed.
`metadata.<key>` is *not* an alias. Lists match by membership, and an **empty list matches
nothing** rather than meaning "unconstrained". Structural keys (`chunk_id`, `doc_id`,
`content`, `start_char`, `end_char`, `library_id`) are refused with a **400**, not silently
ignored.

Tenant scoping is applied last and cannot be widened — a `tenant_id` you supply is
overwritten.

For models, read `GET /v1/models/available` (any authenticated caller) and pass an id in
`llm` (query only) or `reranker`. Unknown id → 404 with `; see GET /v1/models/available`
appended; right id but wrong task → 400. Only `llm` and `reranker` are swappable per
request; embedding and chunking are build-time properties of a collection.

## Create → ingest → poll, programmatically

```bash
COLL=$(curl -s -X POST "$BASE"/v1/collections \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"id":"my-papers","label":"My papers"}' | jq -r .id)

JOB=$(curl -s -X POST "$BASE"/v1/ingest/upload -H "X-API-Key: $KEY" \
  -F 'files=@paper.pdf' -F "collection=$COLL" | jq -r .job_id)

until [ "$(curl -s "$BASE"/v1/ingest/$JOB -H "X-API-Key: $KEY" | jq -r .status)" \
        != accepted ]; do sleep 2; done
```

`POST /v1/ingest` (JSON, 200) is a different endpoint: `source` is a **server-side path**
confined to `INGEST_ROOT`, and the endpoint is 503 on every request when that is unset.
Under the GoWe backend `source` is a Workspace reference instead and a bearer identity is
required.

**One ingest job per principal at a time.** A second upload is **429** with `Retry-After:
30` — a *poll hint, not a lease*; the job may take much longer. The slot frees itself when
the job reaches a terminal state, and a row untouched for 6 hours stops counting. Admins
are exempt.

Two accepted asymmetries: the guard is on `/v1/ingest/upload` only, so a running
`POST /v1/ingest` job blocks your uploads but not vice versa; and two simultaneous uploads
can both pass the check.

## What does an error look like, and how do I correlate it?

Only **`detail` is guaranteed**, and it is deliberately untyped, because it ships in three
shapes: a string almost everywhere, an **array** for FastAPI's 422 validation body, and an
**object** for the `owner_quota_exceeded` 409 (`{error, owned, limit, message}`). Parse
accordingly.

Two conditional fields appear on the store-unavailable 503 of query and retrieve:
`request_id` (16 hex) and `reason`.

**Every response carries `X-Request-Id`**, and that same value is `rid=` on every log line
the request produced. It is CORS-exposed alongside `Retry-After`, so a browser client can
read it. The server always generates its own — an inbound `X-Request-Id` is recorded for
gateway correlation but never echoed, so it cannot be forged or repeated.

When you log a failure, log that id. `grep rid=<id>` is the whole first step of an
operator's investigation.

## Which 503s are worth retrying?

`reason=timeout` means connected-but-slow: retry, often warm within seconds.
`reason=unreachable` means never reached: retry probably will not help. `reason=error`
means the store answered unhappily. **Absent or unrecognised must be treated as the
conservative case** — it is not a store failure at all, but one of authorization-store
fail-closed, a dormant or restoring collection, or the tenant at capacity.

A `ConnectTimeout` is classified `unreachable`, not `timeout`, precisely so nothing
promises a warm second read that will not happen.

## What are the limits?

Per-principal hourly buckets, 429 with `Retry-After`: **ingest 10** (shared by
`/v1/ingest` and `/v1/ingest/upload`), **collection create 5**, **share grants 60**. Admins
are exempt from the buckets, never from the bounds. These are **per API process** — N
replicas give N× the rate.

Bounds apply to everyone: JSON body 1 MB (413), `top_k` 100 (422), `/v1/chunks` `ids` 200
(422), list `limit` 500 (422). Uploads: 50 files, 500 MB per request, 50 MB per document
(413), and a content-type allowlist (415).

The upload 411/413 come from a middleware that runs **before the body is read** — the only
check that can, since the framework otherwise spools the whole multipart body to disk
first. A client that lies about `Content-Length` is only stopped by your gateway, so
configure one with a comparable body cap.

## How do I tell which implementation I am talking to?

**Nothing on the wire says.** `/health` is byte-identical and the schema has no room for a
version field. Behavioural tells only: `/docs`, `/redoc` and `/openapi.json` exist on
Python and are absent on Go; Go's `/v1/query` returns a literal `[pipeline not yet wired]`
placeholder; Go's `/v1/ingest/upload` is the surface's only 501.

The Go scaffold implements **no authentication or authorization at all** — no key check, no
bearer, no tenancy, no collection ACL — and most surfaces are stubs. Treat it as a contract
fixture, not a deployment.

## How do I run conformance against my own build?

The suite is black-box over HTTP and **does not start a server** — bring one up first.

```bash
make test-conformance-python      # :8000
make test-conformance-go          # :8080
make test-conformance-keyed       # self-booted, in-memory, four distinct principals
```

**`RAGSTACK_BASE_URL` is required and has no default** (#405). It used to default to
`http://localhost:8000`, which on the deployment host is a live production API — a suite
that creates and deletes collections was pointed at it. The port convention now lives only
in the Make targets, where it cannot fire by accident.

Doctrine worth copying: a missing credential **skips loudly, naming the variable**; a
credential that is present but is not what it claims **fails**. Skips are the silent
failure mode of a conformance suite.

## Pagination and ordering

`GET /v1/documents` is paginated: `limit` (default 100, max 500) and an opaque `cursor`
from the previous response's **`X-Next-Cursor`** header, which is **absent on the last
page**. A malformed cursor is a deliberately generic 400 — your value is never reflected
back. **There is no ordering guarantee.** And it degrades to `[]` at 200 on a backend
fault, so an empty list does not prove an empty corpus.

`GET /v1/collections` is **not** paginated and returns your whole visible set. Its order is
meaningful — registry insertion order — which is what makes "the first entry in your list"
a usable rule.

---

# Part 3 — For operators

## What configuration is actually in effect?

```bash
curl -s "$BASE"/v1/config -H "X-API-Key: $ADMIN_KEY"        # admin only
```

An allowlist of non-sensitive settings — backends, store URLs (credentials stripped),
embedding, chunking, retrieval, bounds. Secrets are never *read*, so a new setting cannot
leak by accident.

**One trap:** it echoes the **raw** `LOG_LEVEL`. A value rejected at startup still shows
here while INFO is actually in force. For the truth, ask the log-level endpoint.

## Are my dependencies healthy?

```bash
curl -s "$BASE"/v1/health/deep -H "X-API-Key: $ADMIN_KEY"   # admin only
```

Four probes — vector, text, graph, jobstore — each with `ok`, `detail` and `latency_ms`;
`status` is `ok` or `degraded`. The probes are **read-only by design**: a health check must
never provision infrastructure, so they call `healthcheck()`, not `ensure_collection()`.

For counts, `GET /v1/stats/stores` and `/v1/stats/tenants` are open to any authenticated
caller and scoped to what that caller can read — they are **not** fleet totals. Use
`?counts=false` unless you want the counts: the counted path probes every readable
collection and takes seconds on a large deployment.

`POST /v1/stats/models/benchmark` runs a real workload against the live embedder and LLM.
It is bounded so it cannot be coaxed into a load test, but it **consumes the serving
fleet** — call it on demand, never on a poll.

## How do I turn up logging without a restart?

```bash
curl -s -X PUT "$BASE"/v1/admin/log-level -H "X-API-Key: $ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"level":"DEBUG","ttl_seconds":900}'

curl -s "$BASE"/v1/admin/log-level -H "X-API-Key: $ADMIN_KEY"    # what is in force
curl -s -X DELETE "$BASE"/v1/admin/log-level -H "X-API-Key: $ADMIN_KEY"   # revert now
```

`ttl_seconds` is 1–86400. When it expires the process reverts to its **configured**
defaults — the same end state `DELETE` produces, audited as `expired` rather than `reset`.
The TTL exists for the admin who turned DEBUG on to investigate something, was interrupted,
and never came back.

Three behaviours worth knowing before you rely on it:

- **`loggers` has replace semantics.** What you send becomes the complete override set;
  `{}` clears them all; omitting the field leaves them untouched.
- **Every PUT cancels a pending expiry before applying.** So a follow-up PUT that omits
  `ttl_seconds` **disarms** the earlier one. Two TTLs can never overlap.
- **It is process-local and resets on restart.** To make a level stick, set `LOG_LEVEL` and
  restart.

A refusal is a 422 that changes nothing — the whole body is validated before any logger is
touched. The audit logger cannot be silenced through this endpoint.

**`LOG_DAMPEN_LOGGERS`** pins the HTTP transports (`httpx`, `httpcore`,
`elastic_transport`, `urllib3`) to WARNING while the root is at INFO, and releases them at
DEBUG. Only the transports: one `/v1/query` makes at least five outbound calls and up to
fourteen on the multi-collection path, so leaving them at INFO buries the one summary line
at a signal-to-noise of 1:5 or worse. `neo4j` and `qdrant_client` are deliberately not
damped — closer to the data path, far less chatty.

## A user sent me a screenshot with a `Reference:` id

That id is `rid`. It is the whole entry point.

```bash
grep rid=396fb7425f8748dc /rag/data/tenants/<tenant>/logs/api-<tenant>.log
```

1. **Get the id.** From the UI it is the `Reference:` line. From a client it is the
   `X-Request-Id` **response header**, present on every response. On the store-unavailable
   503 it is also in the body as `request_id` — a header does not survive a copy-paste into
   a ticket, which is why it is in both.
2. **Expect one summary line, plus whatever failure lines that request wrote.** Every record
   written while the request was in flight carries `rid`, so ad-hoc warnings come along.
3. **Read `wall_ms` first, then find the stage that accounts for it.** `vector_ms` ≈
   `wall_ms` with low `inflight` and a fast retry → a cold read. `vector_ms` dominant with
   high `inflight` → contention, not the store. `generate_ms` → the LLM. `embed_ms` →
   the embedding fleet, and `embed_ep` names which endpoint served it.
4. **Look for the retry** — a different `rid`, the same `qsha` and `coll`, seconds later.
   A 3003.8 ms attempt followed by a 20.3 ms one on the same collection *is* the diagnosis.
5. **A 503 with no `reason` is not a store failure.** It is authorization-store fail-closed,
   a dormant or restoring collection, or the tenant at capacity — none of which log a line
   of their own. The `rid` grep returning **exactly one line** is itself the discriminator,
   because a store failure always writes two.

No `Reference:` line at all means the response carried no id — either the gateway answered,
or the error escaped the framework's own middleware. Check the gateway log first.

Full procedure: [runbooks/tracing-a-503.md](runbooks/tracing-a-503.md).

## What do the timings mean — and what can't they tell me?

Stage timings render as **`sum/count`**, never a bare sum, and `wall_ms` is always printed
beside them. The query path nests concurrent gathers in three places, so stage sums
routinely *exceed* wall time: five legs of nine seconds render as `vector_ms=45000.0/5`,
and a bare `45000` would be read as one 45-second search by the next person to grep the
log. The `/count` is never elided, even at 1.

**`self_ms` is an upper bound, and is labelled as one.** It is `wall − Σ(mean of each
recognised external stage)`, clamped at zero. Two deliberate conservatisms: it subtracts
each stage's *mean* rather than its sum, so where legs were actually sequential it
under-subtracts — the safe direction; and an **unrecognised stage name is not subtracted at
all**, so a newly-added external call inflates `self_ms` rather than silently deflating it.

Two external round trips sit inside `wall_ms` and outside every stage — the identity
provider on the bearer path, and a cached store read on the API-key path. Both land in
`self_ms`. Before quoting it as "Python-layer time", time those first.

The five-minute rollup reports p50/p95 as **bucket upper bounds** — hence `_le` —
never an interpolation. `p95_ms_le=5000.0` means "at most 5000 ms". The bucket tail
straddles both the old 30 s and current 60 s store timeouts on purpose, so mass migrating
out of the 2.5–5 s buckets is a creeping-bound signal several windows before the first user
sees a 503.

**What these numbers cannot tell you:** they do not measure host page-cache state. A cold
cache and an unlucky query vector touching more segments than usual produce an identical
line. Report "cold cache" as the surviving hypothesis and say what would falsify it — the
retry's own line is the evidence that makes it testable.

## An ingest job is stuck and the tenant can't upload

One in-flight ingest job per principal; a second upload is 429 while any `accepted` or
`running` row exists.

```bash
curl -s "$BASE"/v1/jobs?limit=25 -H "X-API-Key: $ADMIN_KEY"     # admin only
```

**There is no cancel endpoint.** Three things actually work:

1. **Wait.** A row untouched for six hours stops counting. No action needed.
2. **Restart the tenant's API.** At startup every non-terminal row is swept to `failed`.
   Works on the sqlite and memory job stores — **but is a deliberate no-op on Postgres**,
   because an unscoped sweep would fail jobs legitimately running in sibling workers. If a
   tenant is ever moved to Postgres, this lever silently stops working.
3. **Use an admin credential.** Admins bypass the guard, and the bypass is logged.

## Register a model and swap it in

Hot-swappable tasks are exactly **`llm` and `reranker`**. Embedding, chunking and tokenizer
are build-time properties of a collection — naming one in an assignment is a 422, not a
silent no-op, because changing them means building a new collection rather than mutating a
running one.

```bash
curl -s -X POST "$BASE"/v1/admin/models/registry -H "X-API-Key: $ADMIN_KEY" \
  -H 'Content-Type: application/json' -d '{...}'                 # 201

curl -s -X PATCH "$BASE"/v1/admin/config/assignments -H "X-API-Key: $ADMIN_KEY" \
  -H 'Content-Type: application/json' -d '{"llm":"my-model"}'
```

Only fields **present** in the patch change; a field set to `null` reverts that task to its
settings default. Every requested task is validated before any live swap, so an invalid task
cannot leave an earlier one half-applied.

Two things take effect without a restart: an assignment change, and **a `PUT` to a model
that is currently assigned** — that rebuilds and atomically swaps the live client. Merely
registering a model changes no live behaviour. All of it persists across restarts.

Deleting a model that is currently assigned is a 409: unassign first.

## Create a service account for a web front-end

```bash
curl -s -X POST "$BASE"/v1/admin/service-accounts -H "X-API-Key: $ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"subject":"svc-web","purpose":"the ASM web UI"}'          # 201
```

**This mints no credential.** It records the account. The subject is both the API-key tenant
string and the authorization subject, so the account can own collections, receive shares and
join groups — but issuing the key itself is an operator edit to `API_KEYS` plus a restart.
Passing a key in this body is a 422 rather than a silent no-op.

`disable` and `enable` are 204 and idempotent. **Disable is a soft, best-effort revoke:** the
check is TTL-cached (30 s by default, hard cap 300), it flushes only *this* process's cache,
and it **fails open** when the user store cannot answer — so an unreachable store means a
disabled account keeps working rather than every caller being locked out. **The authoritative
revoke is removing the key from `API_KEYS` and restarting.**

Self-disable is a 409: the check runs on the path you just authenticated on, so the next
request — including the `/enable` that would undo it — would be a 401 with no way back
through the API.

`default` and `public` are refused as subjects. `default` is what every valid-but-unmapped
key resolves to, so registering and disabling it would 401 every such caller at once,
including the admin key that would have to call `/enable`.

## Grant or revoke admin for a federated user

```bash
curl -s -X PATCH "$BASE"/v1/admin/users/alice/role -H "X-API-Key: $ADMIN_KEY" \
  -H 'Content-Type: application/json' -d '{"role":"admin"}'
```

This is the **only in-API way a bearer identity becomes an admin**. Nothing travelling with
a credential — an OIDC claim, a token field, a header — is an input to the role decision. A
token can never elevate the caller presenting it.

The response distinguishes `role` (what is stored, and what this API can change) from
`env_admin` (whether `ADMIN_SUBJECTS` also names the subject, which it cannot). That env
allowlist is evaluated **first** and short-circuits, which is what makes it break-glass: it
works on an empty users table, survives a user-store outage, and no database write can
revoke it. Removing it is an env edit plus a restart.

**Admin is a hard superuser, not a UI tier** — it bypasses ownership on every collection and
opens every admin surface.

Revoking the last admin is refused when it would be **unrecoverable**, judged against all
three recovery sources on *liveness*, not mere presence: an allowlist entry whose issuer the
active provider never emits matches no token, and an admin key whose tenant is a disabled
service account now 401s.

## Manage capacity

`MAX_COLLECTIONS` (default 100) counts **active** rows only — a dormant collection holds no
store slot and is not counted.

At the bound, `POST /v1/collections` **evicts one least-recently-used archived collection
and proceeds**. It answers **507 `insufficient_storage`** only when nothing is evictable.
That is a different code from the 503s, which mean: a restore at capacity, or a read against
a collection that is dormant or restoring.

```bash
curl -s -X POST "$BASE"/v1/admin/collections/evict?need=3\&dry_run=true \
  -H "X-API-Key: $ADMIN_KEY"
```

`dry_run` returns the same plan without acting, and is **always 200** — even when fewer than
`need` victims exist, with `shortfall` counting per reason why the rest were ineligible.

Eviction never touches a collection with a running ingest job, one whose archive is not
current, or one whose physical stores are shared with another registry id. The registry row
is flipped to `dormant` **first** — so readers immediately get 503 + `Retry-After` — and only
then are the stores dropped.

`POST /v1/collections/{id}/restore` is idempotent and always 202. It verifies every file's
checksum and the manifest's spec hash **before writing anything**, and it needs a bearer
credential, because the archive is read as the caller.

## Deploy a new version, and revert it

```bash
PID=$(cat /rag/data/tenants/<t>/api-<name>.pid)
tr '\0' ' ' < /proc/$PID/cmdline; readlink -f /proc/$PID/cwd   # confirm BOTH first
kill -TERM "$PID"; while [ -d /proc/$PID ]; do sleep 1; done

git -C /rag/repos/tenants/<worktree> checkout --detach <tag>
cd /rag/repos/tenants/<worktree>/python
set -a; . /rag/data/tenants/<t>/config/tenant.env; set +a
[ -f /rag/data/tenants/<t>/config/secrets.env ] && \
  { set -a; . /rag/data/tenants/<t>/config/secrets.env; set +a; }
export HF_HOME=/rag/cache PYTHONPATH=$PWD
nohup /rag/envs/ragstack/bin/python -m uvicorn ragstack.api.main:app \
  --host 0.0.0.0 --port <port> >> /rag/data/tenants/<t>/logs/api-<name>.log 2>&1 &
echo $! > /rag/data/tenants/<t>/api-<name>.pid
```

Three things in that recipe are load-bearing:

- **`PYTHONPATH=$PWD`.** Without it the shared venv's editable install resolves `ragstack`
  to a frozen pre-security checkout, and you restart the tenant onto July's code against
  today's production stores.
- **The `secrets.env` guard.** Only two tenants have one; an unconditional `.` breaks the
  other two.
- **Stop by the recorded pid.** Never by process-name pattern — see the last recipe.

**The path asymmetry that catches everyone:** data dirs drop the `-next` suffix
(`/rag/data/tenants/asm`) while worktrees and pid-file names keep it
(`/rag/repos/tenants/asm-next`, `api-asm-next.pid`).

Verify through the gateway: `GET /ragstack/<tenant>/api/v1/collections?counts=false`.
**401 means alive**; 502 means the API is down.

Reverting is the same recipe with the previous tag — **plus any config the release
required**. A code revert that leaves the config behind can put a tenant back to writing
into the wrong stores.

## Rotate keys

`API_KEYS` lives in `tenant.env` and is a **JSON list**, not comma-separated. Splitting it
on `,` yields a 67-character string that 401s while looking like a valid 64-character key.
Parse it with `json.loads`. `API_KEY_TENANTS` and `API_KEY_ROLES` are JSON objects.

Rotation is an env edit **plus a restart** — nothing writes these at runtime and there is no
reload endpoint. **The key and its tenant mapping must go in the same edit:** startup fails
if `API_KEY_TENANTS` is set and any configured key is unmapped, so a half-done rotation
refuses to boot rather than serving in a broken state. Have a rollback file ready.

To bridge the window without a restart, disable the service account — but read the soft-revoke
caveats above before relying on it.

## The rules that exist because something broke

- **Never stop a service by process-name pattern.** `pkill -f "uvicorn ragstack.api.main:app"`
  in one cleanup took every API on the host down for seventeen hours: production runs the
  same command line as every scratch server. Stop by the pid recorded at launch, or resolve
  by port and verify `/proc/<pid>/cwd` **and** `cmdline` first. **`pgrep` returning nothing is
  a fleet-wide alarm, not proof your cleanup worked.**
- **A default that resolves to production** is this codebase's most persistent defect class —
  eight tracked instances. The shape is always the same: the default *is* the documented
  convention, and the convention is right on a laptop. It is wrong here because the
  deployment host is also the development host, so the convention and production name the
  same address. The fix is never to change the convention; it is to make the value
  **required** and keep the convention in the invocation.
- **Know which stores a tenant writes to before you test against it.** Two of the four
  tenants point at the production Qdrant and Elasticsearch; the other two have their own. A
  write test on the wrong one lands in production.
- **Pin `PYTHONPATH` on every Python invocation.** The editable install in the shared venv
  resolves to a frozen production checkout.
