# ADR 0003 — Access control: physical tenancy, collection-level ownership, two roles

- **Status:** Accepted
- **Date:** 2026-08-04
- **Deciders:** @wilke
- **Related:** [#230](https://github.com/wilke/ragstack/issues/230) (library_id payload partitioning),
  [#197](https://github.com/wilke/ragstack/issues/197) (`get_chunks` drops filter keys),
  [ADR-0002](0002-collection-identity.md), [libraries-spec.md](../libraries-spec.md)

## Context

Three things in the tree look like access control. Only one works, none expresses
ownership, and the word *tenant* means two incompatible things.

- **Per-chunk `tenant_id`** is genuinely enforced — stamped at ingest, injected last into
  every filter so a caller cannot widen it, fail-closed on an empty list across all three
  store backends. But because access *is* a payload filter, every filter bug is a security
  bug: `get_chunks` silently drops every key except `tenant_id` (#197); `delete_except`
  computes its keep-set under the wrong tenant; `count_tenants` / `list_documents` /
  `delete` accept no filter dict at all.
- **Roles** are four constants of which exactly one is ever gated on — `require_role` is
  called with `ROLE_ADMIN` and nothing else in the entire tree. `engineer` and `manager`
  are validated at startup and assignable, but no route distinguishes them from
  `researcher`. A bearer identity is hard-coded to `ROLE_RESEARCHER`, so **every
  authenticated user carries the identical role** and the axis conveys nothing about real
  users. *(Context = the state that prompted this ADR. The hard-coding is gone: a bearer
  identity can now be an admin by explicit server-side assignment — see the bearer-admins
  amendment under decision 4.)*
- **`TENANT_COLLECTIONS`** confines a tenant to a set of collection ids but defaults to
  `{}` — feature off — and is an operator-authored env map, not a property of the resource.

No resource carries an owner. `CollectionSpec` is thirteen fields of pure build spec; a grep
for `owner|created_by|visibility|acl|shared_with` across `python/ragstack/` returns one
hit, a comment about job leases. So `DELETE /v1/collections/{id}?purge=true` performs no
ownership check at all — its only guards beyond the admin role are integrity ones (the
default collection, a physical store shared by another registry entry). Any admin may
destroy any collection, because who created it was never recorded.

Meanwhile the *deployment* already practises a stronger tenancy than the code knows about:
one Qdrant instance holds the shared corpora, a second serves an isolated org. Two
meanings of "tenant" — a payload value and a process — share one name.

Upstream settled the underlying question while `libraries-spec.md` was being written.
Qdrant **removed payload-based filters from its JWT/RBAC in v1.16** (deprecated 1.15,
PR #7450), stating *"payload based filters are inconsistent for read/write operations"* and
naming the alternative: *"prefer collection-based access control."* Qdrant will not enforce
payload-level tenant isolation; the collection is its only server-enforced boundary below
the instance.

## Decision

**1. A tenant is a Qdrant instance, not a payload value.** Organisations needing hard
isolation get their own instance — the only absolute boundary available, since Qdrant has
no namespace above the collection and no longer enforces anything below it. The per-chunk
`tenant_id` is renamed **`owner_id`** and demoted to provenance: it records who ingested a
chunk and is no longer an authorization mechanism. It stays stamped, and stays enforced in
filters for at least one release as defence in depth.

**2. Access is asserted at the collection.** One check at collection resolution, before any
store call. A collection carries an owner, a visibility (`public` | `private`), and a share
list. This aligns with Qdrant's own RBAC direction and removes security from the filter
path entirely.

**3. A library is a collection.** One-to-one, no separate entity — already true in the code
since #228, where a named collection *is* one Qdrant collection plus one ES index and
"library" survives only as the `lib` marker in the derived store name. Users create
collections directly; `collection_create_request` currently requires `embedding` and
`chunk`, so a server-configured **default build spec** must supply them, with those fields
admin-only. A `user` may create and share private collections and read public ones.

**4. Two roles: `admin` and `user`.** Drop `engineer` and `manager` — both inert. Rename
`researcher` → `user` (coordinated: the API-key fallback in `security.py`
`_principal_from_key`, and present in `api_key_roles` on deployed servers; the bearer
path's role now comes from `_bearer_role` instead of a constant). `maintainer` is deliberately **not** added: the only
endpoint exclusive to admin under that split would be `GET /v1/config`, a read-only
allowlist that already excludes `api_keys`, `*_password` and `postgres_dsn` and redacts URL
userinfo. The real admin/maintainer boundary — editing `rag.env`, restarting units,
managing images — is outside the API and already enforced by shell access. Add the role
when a *write* operation separates it; the likely trigger is delegating corpus curation
without granting model-registry writes, since those change what every future ingest
produces.

*Amended (bearer admins).* A bearer identity resolves to `user` unless an **explicit
server-side source** names it an admin — never `DEFAULT_ROLE`, which is `admin` in
production. The two sources are `ADMIN_SUBJECTS` (an env allowlist of `issuer:subject`
strings, evaluated first with no store read, so it bootstraps on an empty users table
and survives a store outage) and a `users.role` of `admin`, written only through
`PATCH /v1/admin/users/{subject}/role` by an existing admin. Nothing that travels with
the credential is an input, so a token cannot self-elevate; the store read fails
**closed** (an outage withholds elevation, never grants it), which is the deliberate
mirror of the service-account disabled check's fail-open.

A consequence worth stating, because it changes what "admin only" means on the wire:
`require_role` tests the authenticated principal's role and not which credential produced
it, so a bearer admin reaches **every** `/v1/admin/*` route. The contract lists both
security schemes on those operations for that reason. Third source of the same role,
unchanged: an API key mapped to `admin` via `API_KEY_ROLES`/`DEFAULT_ROLE` — which is why
the last-admin revoke refusal counts *all three* sources rather than stored admins alone.

**5. Admin bypasses ownership** — explicitly, as a named branch in the authorization check,
and logged. Required for purge, migration and support. Today the bypass exists only because
there is nothing to bypass; it must become a decision the code states rather than an
absence the code implies.

## Consequences

**Accepted:**

- **The collection count is now a budget, and it is the binding constraint.** Qdrant's own
  docs call a collection-per-user an *"antipattern"* and warn against *"hundreds and
  thousands"*; Cloud enforces a cap stated as 200 by maintainers and 1000 in the docs.
  Measured here: an empty collection already costs 8 segments, ~104 files and ~496 KB, and
  17 collections drive 1,561 threads and 61,219 mmaps. **Budget ~100–150 per instance,
  alert at 100.** Reported failure order as the count grows: RAM → restart time and
  consensus load → thread exhaustion (~1000) → crash on create (~2000). Elasticsearch is
  not the constraint — default `cluster.max_shards_per_node` is 1000 against 11 in use.
- **The budget is per instance**, so decision 1 multiplies it. An org with 30 researchers
  holding 3 collections each fits comfortably on its own Qdrant.
- **Creation has user-visible latency** — two physical stores must be created. Rules out
  ephemeral or per-query sets.
- **Empty collections are not free.** Five created, one filled, still costs five index
  structures.
- **"Search everything I can see" becomes N-way fan-out plus RRF**, capped. Not a
  regression — a payload filter could never span physical collections either — but work
  that must exist before the feature does.
- **Sharing shares the build spec.** Harmless for read; the reason write and delete stay
  owner-only.
- **An instance per org costs a process, RAM, a port and a backup target**, and makes
  cross-org search impossible. For orgs that is the point; it is why this scales to a
  handful, not to per-user.

**Gained:** security stops depending on filter correctness, so #197 and its siblings become
ordinary bugs; revocation is a row update, not a payload rewrite; isolation holds even when
filter code is wrong; three concepts collapse to two; and the enforcement boundary matches
the one Qdrant itself supports.

**The escape hatch stays open.** The physical store name is derived and never exposed — the
API surfaces only `id`. A collection can later be backed by a shared store without an API
change, which is what makes deferring #230 safe rather than merely cheap. *(Clarified with
[#276](https://github.com/wilke/ragstack/issues/276): this guards the id↔store
**decoupling**, not the spelling. A corpus created without an explicit id — and, since
#276, the settings-derived corpus — takes its content-addressed name as its `id`; that id
is a registry key that happens to equal the store's current name, stays stable through
`QDRANT_COLLECTION_EXPLICIT`, and can still be re-backed by another store later.)*

## Alternatives considered

- **A separate `library` entity partitioned by `library_id`** ([libraries-spec.md](../libraries-spec.md), #230).
  Instant creation, thousands of sets, cheap document re-homing. Rejected: `library_id`
  carries no descriptive value — unlike author, year or DOI it is a *security label* the
  server injects and the caller cannot widen — so it earns its keep only once the
  collection budget above is exhausted, and nothing else. It also returns enforcement to
  the payload layer that Qdrant deprecated in 1.15 and removed in 1.16. The spec is
  retained for the day the budget binds, with one correction pending: it predates Qdrant
  1.16 **tiered multitenancy** (custom sharding with a fallback shard, plus tenant
  promotion via `replicate_points`), which is upstream's own supported answer to the same
  problem and should be evaluated before any bespoke `library_id` work.
- **Three roles (`admin` / `maintainer` / `user`).** Matches how the team is organised — an
  admin and a production engineer. Rejected because the API cannot express the distinction
  today: every operation a maintainer would perform is already permitted to admin, and the
  one endpoint left exclusively to admin is read-only and secret-free. Revisit on a
  concrete write operation, not on org structure.
- **Keeping per-chunk `tenant_id` as the authorization mechanism.** Zero migration.
  Rejected: it makes the correctness of `get_chunks`, `delete_except`, `count_tenants` and
  every future store method a security property, and it forces a payload rewrite to publish
  or unpublish a corpus because `tenant_id` conflates "who owns this row" with "who may
  read it".

## Follow-up

`#199` recorded that `is_tenant=true` + `payload_m` made filtered results *worse* (0/20 at
10 values) — and, to its credit, already attributed the mechanism to the companion setting
`m: 0`, which leaves no global HNSW graph, only per-value subgraphs. The caution for the
record is narrower: `is_tenant` itself is a **disk-layout** hint (it co-locates a tenant's
vectors for sequential reads), a Qdrant maintainer responding to an identical report said
he would not expect it to affect recall at all, and toggling it on an existing collection
does not rebuild the graph — so a measurement taken right after enabling it measures the
*old* graph. Do not let "`is_tenant` degrades recall" circulate shorn of the `m: 0`
context. Note also `g1-retrieval-protocol.md` §2.1: #199's truncation did not reproduce in
the v1 conjunction shape, which confines the finding to its original conditions without
refuting it.

The live measurements quoted under Consequences (empty-collection cost, thread and mmap
counts, ES shard usage) were taken 2026-08-04 on the development deployment — Qdrant
1.18.0, single node — and are recorded only here.
