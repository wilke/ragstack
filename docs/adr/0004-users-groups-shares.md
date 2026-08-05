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

### Schema sketch (normative shape, not final DDL)

```sql
users          (subject PK, issuer, email, display_name, first_seen_at, last_seen_at)
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
