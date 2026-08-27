# ADR 0004 — Users, groups, and shares: Postgres ACLs with grant-option delegation

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** @wilke
- **Amends:** [ADR-0003](0003-access-control.md) decision 2 — the `visibility` field is
  replaced by a share to the built-in `public` group, so one mechanism answers "who may
  read" instead of two.
- **Related:** [#197](https://github.com/wilke/ragstack/issues/197), the GoWe engine
  (intended second consumer of the group service)

> **Implementation notes (added 2026-08-05 as #243 shipped the enforcement layer):**
> Two refinements the accepted text does not state. (1) *Revocation is grounded, not
> blanket.* Decision 5 says "revoking a grantor also revokes everything they granted
> onward"; the implementation computes a grounded least-fixpoint — an onward grant
> survives if its grantee retains access through an independent grounded share, and
> mutual-grant cycles collapse rather than protecting each other. This is strictly safer
> than the literal wording. (2) *Grant-option delegation is not yet exposed.* #244 shipped the
> shares API but deliberately scopes it to `read` grants by an owner-or-admin only —
> `grant_option`, `write`, and non-owner delegation are not exposed, so "a grantor can never
> delegate more than they hold" has nothing to enforce against yet. It MUST be enforced when a
> later unit exposes delegated granting; the DDL-as-contract note in #243 flags this for any
> second consumer coding against the shares table before then.

> **Implementation notes (added 2026-08-05 as #244 shipped the shares API/UI):**
> #244 exposed granting but deliberately kept it **read-only and owner-or-admin**:
> `POST /v1/collections/{id}/shares` accepts `permission: read` only, only the owner
> (or an admin) may grant, and `grant_option` is **not writable** (always false). So no
> caller can yet write a non-owner or delegable grant, and the grant-option enforcement
> refinement note (2) above anticipated at #244 did **not** land — "a grantor can never
> delegate more than they hold" is still unenforced because nothing writes a delegable
> grant. It moves to the later issue that exposes `grant_option`/`write`; until then the
> #243 DDL-as-contract flag stands for any second consumer. Making a collection public is
> `GRANT read TO @public` and un-publishing is `DELETE` of that share, replacing the
> ADR-0003 `visibility` field as decided above.

> **Implementation notes (added 2026-08-05 as #245 shipped groups natively):**
> #245 realizes **decision 3** and completes the `public`-as-built-in-group half of
> **decision 4**. Groups are now first-class RAGStack-native rows (`group_store.py`,
> memory/sqlite/postgres backends): `POST/GET /v1/groups`, `GET/DELETE
> /v1/groups/{id}`, and `POST/DELETE /v1/groups/{id}/members` — create, list, view,
> soft-delete, and flat (no-nesting) membership. The built-in `public` group is a real,
> listable, viewable row that is **never member-editable and never deletable** (the
> store refuses both; the API surfaces the refusal as 409). Group grants are honoured
> through the **same** `grants_for_subject` seam as user grants — the store overrides it
> to union a subject's direct shares with the shares to every group they actively belong
> to (plus `public`) — so a `GRANT read TO @group:<id>` reaches every active member at
> read time with no per-router SQL. **Grant-option / write / owner delegation is still
> deferred:** #245 exposes no delegable grant, so refinement note (2) above — "a grantor
> can never delegate more than they hold … MUST be enforced when a later unit exposes
> delegated granting" — remains correctly unfulfilled and moves past the MVP.

> **Implementation notes (added 2026-08-27 as ownership transfer shipped as its own
> endpoint):**
> Decision 5 says an owner change is *"an ordinary revoke+grant pair in the same audited
> table"*. That stayed true of the **data** and stopped being true of the **API**.
> `POST /v1/collections/{id}/shares` now **rejects `permission: owner` with a 400** naming
> `POST /v1/collections/{id}/owner`, which is the only route by which ownership moves.
> Three reasons, each a consequence of this ADR's own decisions rather than a departure
> from them. (1) *Atomicity.* Decision 5's partial unique index means there is exactly one
> active owner row, so a handover is a revoke **and** a grant that must both land or
> neither; it runs inside one transaction in `AclStore.transfer_owner` on every backend.
> Exposed as a pair of share calls it would be two requests, and a caller who made the
> first and not the second would leave a collection with no owner. (2) *It is not a
> grant-shaped thing.* Granting is additive; a transfer is a state transition with audit
> side effects, and replaying it is a 409, not a no-op — which is also why the endpoint is
> POST rather than PATCH (there is no `GET …/owner` document to merge into). (3) *The
> outgoing owner loses access*, with no consolation `read` grant minted: that would be a
> second write outside the transaction, and every permission here comes from an explicit
> row with a real `granted_by` — a row the system invented on the actor's behalf would be
> a grant nobody asked for, still active, that the new owner must discover and revoke.
> Decision 6's soft revoke keeps the handover in the audit trail either way.
>
> Ownership *acquisition* is also where ADR-0003's per-owner quota is enforced alongside
> creation, so a transfer can neither be used to evade the quota nor to weaponise it by
> filling a colleague's. Nothing about the **schema** changed: `permission = 'owner'`, one
> active row, `shares_active_owner`. What changed is that the shares API is now read-only
> in the strict sense — it grants `read` and nothing else (any other permission is a 422).

## Context

ADR-0003 put access on the collection — an owner, a visibility, a share list — but that
is a shape, not a design. Underneath it, nothing exists:

- **There is no user table.** Identities are minted per-request from a verified token
  (`f"{issuer}:{subject}"`) and never persisted. Users cannot be enumerated, a share
  dialog cannot autocomplete, and nobody can be granted anything before their first login.
- **The grantee cannot be named.** OIDC's `sub` is an opaque provider string no colleague
  can type; only BV-BRC's `un` is human-legible. Sharing is structurally impossible for
  OIDC identities as built.
- **BV-BRC has no group concept**, so groups cannot be delegated to the IdP.
- **Two mechanisms answered one question** in ADR-0003 as written: a `visibility` enum
  *and* a share list both decide who may read. Two mechanisms means two code paths and
  two revocation stories.

A Postgres instance is already deployed in the infra stack, and `CollectionStore` already
has a `postgres` backend, so a relational ACL store adds no new service.

## Decision

**1. A profile row on first authentication.** The first verified token for a subject
upserts `users(subject, issuer, email, display_name, first_seen_at, last_seen_at)`.
`subject` is the tenant string `issuer:sub` — the same key the rest of the system uses.
This makes users enumerable and shares FK-checkable; it grants nothing by itself.

*Amended.* The row additionally carries `role` (+ `role_set_by`/`role_set_at`, the same
append-only audit shape as decision 6), which is the **stored** half of a bearer
identity's RBAC role — the other half being the `ADMIN_SUBJECTS` env allowlist, which is
checked first and cannot be revoked from the API. `role` is an **identity-class** column:
it is excluded from the `upsert_seen` ON CONFLICT assignment list in both SQL dialects, so
the first-auth hook that runs on every login is *structurally* unable to reset an admin's
grant. It is written only by `set_role`, which refuses service accounts (an API-key
principal's role is `API_KEY_ROLES`) and never creates a row. `set_role` also owns the
last-admin refusal, because that one is a claim about the whole table and has to be
evaluated in the same transaction as the write it vetoes — a caller-side count would let
two concurrent revokes each observe the other's admin and both pass. The caller decides
only *whether to ask for* the refusal: it is skipped when an admin source outside this
table (a usable `ADMIN_SUBJECTS` entry, an API key mapped to `admin`) would survive the
write, since the refusal exists to prevent an unrecoverable lockout and nothing else.

**2. Pending shares keyed on a verified email claim.** A share to someone who has never
logged in is stored as `pending_shares(email, collection_id, permission, grant_option,
granted_by, …)` and converted to a real share on the first login whose token carries a
**verified** matching email (`email_verified=true` for OIDC; BV-BRC usernames are shared
directly, no pending row needed). An unverified email claim never claims a pending share —
otherwise registering a colleague's address at any accepted IdP steals their grants.

**3. Groups are RAGStack-native, in Postgres, designed as a reusable service.**
`groups(id, name, owner_subject, built_in)` + `group_members(group_id, subject, added_by,
added_at)`. BV-BRC emits no group claims, so this cannot be delegated. The schema and its
API live behind their own seam so GoWe can consume the same service rather than grow a
second membership table.

**4. `public` is a built-in group, not a user and not a field.** One reserved row,
`built_in=true`, whose membership test short-circuits to *true* for every caller —
the same shape as Postgres's `PUBLIC` pseudo-role and Unix's `other`. A fake user row was
rejected (users authenticate, own things, and have emails; every enumeration would need to
exclude it forever) and a sentinel string was rejected (it forfeits the FK and lets a typo
grant to nobody, silently). Making a collection public *is* `GRANT read TO public`;
un-publishing is revoking that row. **The public group can hold `read` only** — a write
grant to it is rejected at the API, not warned about.

**5. Permissions are `read < write < owner`, with delegation as a grant option — not
`rwx`.** Share-further is orthogonal to read/write (a steward may re-share what they
cannot edit; a collaborator may edit what they must not re-share), so it is a boolean
`grant_option` on the share row — SQL's `WITH GRANT OPTION` — not an `x` level. A grantor
can never delegate more than they hold, and never with `grant_option` unless their own
grant carries it. Every row records `granted_by`; **revocation follows the chain**, so
revoking a grantor also revokes everything they granted onward. `owner` is a permission
level like the others, with two restrictions: it is **grantable to users only, never to a
group** (a group cannot answer for a corpus, and group-membership edits must never move
ownership implicitly), and there is **exactly one active owner row per collection** —
enforced by its own partial unique index on `(collection_id) WHERE permission = 'owner'
AND revoked_at IS NULL`, since the general active-shares index below is per-grantee and
cannot express it. This makes admin reassignment an ordinary revoke+grant pair in the same
audited table, per ADR-0003's admin-bypass decision, rather than a special-cased column.

**6. Revocation is soft.** `revoked_at` + `revoked_by`, never DELETE. The performance
objection is answered by a partial index:

```sql
CREATE UNIQUE INDEX shares_active
  ON shares (collection_id, grantee_type, grantee_id, permission)
  WHERE revoked_at IS NULL;
```

Access checks read only this index, which contains only active rows — lookup cost scales
with *active* shares regardless of history. The partial-unique form also permits re-grant
after revoke, which a plain unique constraint would block. Hard delete was rejected
because it buys nothing: an audit requirement would then recreate the same rows in a
separate log table.

**7. Service accounts are `users` rows with `kind='service'`, and disabling one is a
*soft* revoke that fails open** (issue #258). A machine identity authenticated by an
`X-API-Key` secret we mint is the same kind of thing as a person for every purpose the
rest of this ADR cares about — it owns collections, receives shares, joins groups — so it
is a row in the same table on the same connection, not a fourth store. Two properties
make it safe to share the table:

- **The subject namespaces are disjoint.** A bearer subject is always `f"{issuer}:{sub}"`;
  a service subject is **colon-free**. The data layer enforces the colon rule on create,
  which is the same partition the startup guard already enforces on `api_key_tenants`
  values when an identity provider is enabled. A service account therefore cannot collide
  with, or be impersonated by, a federated identity. The price of that disjointness is
  that a colon-free grantee is *ambiguous at the API surface* — it is also how the share
  dialog spells a bare BV-BRC username, which resolves to `bvbrc:<name>`. So naming a
  service account as a share grantee or a group member takes the explicit
  **`@service:<subject>`** form, alongside `@public` and `@group:<id>`: it is the only
  input that yields a colon-free grantee, and without it every grant to a machine
  identity would be created, echoed, and silently never apply.
- **The reserved tenants are not identities.** `default` (what every valid-but-unmapped
  API key and the whole keyless path resolve to) and `public` (the shared corpus) are
  refused as service subjects. Registering one and disabling it would 401 every such
  caller at once — including the admin key needed to re-enable — turning a per-account
  revoke into an unrecoverable deployment-wide lockout. For the same reason, disabling
  the account *you are authenticating as* is refused.
- **`upsert_seen` can never mint or reclassify one.** The first-auth hook carries the
  identity-class columns through unchanged, and the SQL backends narrow their
  `ON CONFLICT` assignment list so the invariant survives a lost race. Registering an
  account is an explicit, admin-only, audited call (`created_by`/`created_at`), and it
  refuses to convert an existing human row (409) — that is a privilege event.

**The API manages the account record, never the credential.** `API_KEYS` /
`API_KEY_TENANTS` / `API_KEY_ROLES` are environment settings with no writer in the
process; an in-process mutation would not reach a sibling worker and would vanish on
restart. Provisioning a key stays an operator edit plus a restart, and the key *and* its
tenant mapping must land in the same edit or the next boot fails its production settings
check.

**The decision that needed making: the auth-time disabled check FAILS OPEN.** A disabled
account's key is rejected with 401 on the API-key path — that is the point of the record,
and the only way to stop a leaked credential without a restart. But when the user store
cannot answer, the request **proceeds**. This deliberately splits from how *authorization*
behaves here (an unavailable ACL store is a 503, fail closed) and sits with how
*authentication* side effects already behave (the first-auth profile upsert is
fire-and-forget). The reasoning: the key is the primary authentication factor and has
already been verified by a constant-time compare, so the caller is authenticated no matter
what the store says; the disable flag is a revocation convenience layered on top. Failing
closed would promote the ACL database to a hard availability dependency of *every*
API-key request — in this deployment, the ingest path and the entire production surface —
so a partition, a full disk, or a DoS on the database would lock out every machine
account including the ones nobody ever disabled. Trading a working authentication path for
a revocation convenience is a bad bargain.

The honest consequences, which belong in the runbook and not only in a code comment:

- **Disabling is a soft, best-effort revoke. The authoritative revoke is removing the key
  from `API_KEYS` and restarting.**
- The check is memoized per subject for `SERVICE_ACCOUNT_DISABLED_CACHE_TTL_SECONDS`
  (default 30, `0` = no cache), so **the TTL is the revocation lag**, per process — the
  same framing, and the same tradeoff, as `IDENTITY_CACHE_TTL_SECONDS`. The worker that
  serves the disable flushes its own cached answer; its siblings wait out the TTL.
- While the store is unreachable, a disabled account is not revoked at all. The first
  such failure logs at WARNING and the flag re-arms on recovery, so every *outage* is
  visible at the default level rather than only the first one in a process's life.
- **A re-enable never erases the disable.** `disabled_by`/`disabled_at` are the record of
  the last revocation and survive; `enabled_by`/`enabled_at` record who reversed it. State
  therefore lives in its own `disabled` column rather than in "is `disabled_at` empty" —
  the audit trail is the point (decision 6), and a row that was revoked once must never
  read back identical to one that never was. This is the `users`-table analogue of
  revoke-keeps-`revoked_by`/re-grant-inserts-a-new-row in `shares`; the table is one row
  per subject, so it keeps the last event of each kind rather than a full history.
- Registration is **opt-in**: an API key whose tenant has no row keeps authenticating
  exactly as before, and nothing on the key path ever *writes* a row (the `default` tenant
  of the keyless dev path and of an unmapped key would otherwise pollute the table).

Rejected: fail closed (above); and the hybrid "fail closed for subjects this process has
already observed as registered, fail open otherwise" — it is strictly better than fail-open
against a DoS-the-database attack, but it is still not airtight across a cold process start,
and it makes the revocation semantics depend on a process's history, which is a much harder
property to explain to an operator than "the TTL is the lag".

### Schema sketch (normative shape, not final DDL)

```sql
users          (subject PK, issuer, email, display_name, first_seen_at, last_seen_at,
                kind human|service, created_by, purpose,
                disabled, disabled_by, disabled_at, enabled_by, enabled_at)
                -- service: colon-free subject, minted by an admin, never by first-auth
                -- disabled: THE state; soft revoke, the auth-path check is cached
                --   and fails open. The two by/at pairs are append-only audit and
                --   are never cleared — a re-enable must not erase the fact that a
                --   revocation happened (decision 6), so state is its own column
groups         (id PK, name, owner_subject → users, built_in)
group_members  (group_id → groups, subject → users, added_by, added_at)
shares         (id PK, collection_id, grantee_type user|group, grantee_id,
                permission read|write|owner, grant_option,
                granted_by → users, granted_at, revoked_by, revoked_at)
                -- owner: users only, one active row per collection (own partial index)
                -- public group: read only, enforced at the API
pending_shares (email, collection_id, permission, grant_option,
                granted_by, granted_at, claimed_by, claimed_at)
```

Effective permission of caller *C* on collection *K* =
max over active rows granted to *C*, to any group containing *C*, or to `public` —
then ADR-0003's admin bypass applies on top, as its own logged branch.

## Consequences

**Accepted:**

- **Postgres becomes mandatory for any deployment that enables sharing.** The JSON
  `CollectionStore` backend stays for single-user dev, but concurrent grant/revoke under
  `flock` is not a story worth writing. Sharing off ⇒ no new requirement.
- **First-login upsert puts a write on the auth path.** It must be fire-and-forget
  (auth never fails because the profile write did) and idempotent.
- **Pending shares depend on IdP email hygiene.** The `email_verified` gate is
  non-negotiable and must be tested against each accepted issuer, not assumed.
- **Grant chains add a recursive revoke.** Cheap in Postgres (`WITH RECURSIVE` over
  `granted_by`), but `granted_by` must be captured from the first row ever written — it
  cannot be retrofitted.
- **Group membership changes are instant access changes.** Adding someone to a group
  grants them every collection shared to it, with no per-collection event. Group
  membership edits therefore need the same audit columns as shares.
- **The OIDC provider must start extracting `email` / `email_verified` / `name` claims**
  (`oidc.py` currently reads `sub` only) — a small, contained change.

**Gained:** shares can name a person before they exist in the system; one mechanism from
"share with a colleague" through "make it public" to "transfer ownership"; delegation
without an ungoverned re-share free-for-all; and a permanent, queryable answer to *who
could read this corpus in a given month, and who granted it* — provenance that licensed
content may actually demand.

## Alternatives considered

- **Share links / capability tokens.** Far less machinery and handles never-logged-in
  users natively. Rejected: a bearer link is access without identity — no per-user audit,
  and revocation is all-or-nothing per link. Unacceptable for licensed corpora.
- **Delegating groups and ACLs to the BV-BRC Workspace** (the `libraries-spec.md` §2
  model). Rejected on fact: BV-BRC has no group concept, and a per-request remote ACL
  probe puts an external service on the hot path of every query, failing closed on
  someone else's downtime.
- **A `visibility` enum alongside shares** (ADR-0003 as literally written). Rejected as
  two mechanisms for one question; superseded by the `public` group grant.
- **`rwx` with x = re-share.** The semantics survive (as `grant_option`); the encoding
  does not — an ordered ladder cannot express "may re-share but not write" or "may write
  but not re-share", both of which have concrete owners' use cases here.
