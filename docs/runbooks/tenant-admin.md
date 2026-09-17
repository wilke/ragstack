# Administering a running tenant

For the operator on shift during an event: someone cannot see their collection,
someone needs to be made an admin, someone wants their data shared with a
colleague, someone hit a limit and got a number they cannot explain. Everything
here is done through the tenant's own API. For moving a tenant to a new release
see [`tenant-upgrade.md`](tenant-upgrade.md); for a 503 with a `Reference:` id
see [`tracing-a-503.md`](tracing-a-503.md); to duplicate a collection onto
another tenant see [`copy-collection.md`](copy-collection.md).

Every endpoint, field name, status code and default below was read out of the
code at `v1.6.2` and is cited `file:line`. Where a value is a **product default**
rather than a particular tenant's setting, it says so — a tenant's live values
are in its own `tenant.env`, never in this file.

### How to read a citation

Code references are `file:line` against the tree at the tag named above.

- A path starting `python/`, `go/`, `ops/coconut/`, `frontend/` or `contracts/`
  is relative to the **repository root** — `python/ragstack/tenancy.py:36`. This
  form is always unambiguous and is the one to prefer.
- Any other path is relative to **`python/ragstack/`** — `api/security.py:990`,
  `ops/evict.py:158` (which is `python/ragstack/ops/evict.py`, not the repo's
  `ops/`).
- A **bare filename** — `collections.py:1569` — means the file the surrounding
  section is about.
- **Documentation is cited too**, and differently: a bare `ctl-quickstart.md:143`
  is a sibling in `docs/runbooks/`, while anything else is repo-root
  (`CLAUDE.md:102`, `docs/runbooks/ctl-deploy.md:1135`). Prose renumbers far
  faster than code, so read a doc line number as a hint and the surrounding
  heading as the real anchor.

That last form is the one to watch. Several of these basenames exist more than
once in the tree (`collections.py` and `documents.py` both do), so a bare
citation resolves from its section, not from a search. **Anything checking these
mechanically needs that rule, or it will resolve to the wrong file and report
correct citations as broken** — which is worse than not checking, because the
next person edits good citations to satisfy a bad resolver. If such a check is
ever written, qualify the citations first or give the checker the convention.

---

## 0. Setting up: base URL and credentials

```bash
T=hackathon
BASE=http://127.0.0.1:24080                     # direct; the port is registry `ports.api`
# BASE=http://127.0.0.1:9000/ragstack/$T/api    # through the gateway
```

Two credential types, and **exactly one per request**:

| header | what it is |
|---|---|
| `X-API-Key: <key>` | a minted API key; its role comes from `API_KEY_ROLES`, falling back to `DEFAULT_ROLE` (`api/security.py:165-192`) |
| `Authorization: <token>` | a user's bearer identity (BV-BRC p3-token or a JWT) |

`api/security.py:81-92` declares both. **The `Bearer ` prefix is optional** —
`_bearer_credential()` (`api/security.py:657-670`) strips it case-insensitively
when present, because the BV-BRC wire format does not use it. Both
`Authorization: Bearer <jwt>` and a raw `Authorization: <p3-token>` work.

**Sending both headers is a 400, not a precedence rule**
(`api/security.py:858-865`): `"present exactly one credential: X-API-Key or
Authorization, not both"`. If you are debugging with a shell that exports an
`X-API-Key` by habit, that 400 is what you will get the moment you paste a
user's token in.

The `Authorization` header is only an auth *input* when `IDENTITY_PROVIDER` is
not `none` (`config.py:673-679`, product default `"none"`); otherwise
`_authenticate` goes straight to the API-key path (`api/security.py:850-856`).
The BV-BRC provider's issuer literal is `bvbrc` (`identity/bvbrc.py:62`).

`GET /health` is **unauthenticated and has no `/v1`** — `api/routers/health.py:12`
mounted with no prefix at `api/main.py:201`, and it is the one router included
without an auth dependency ("Health stays open for liveness probes",
`api/main.py:197-199`). It returns `{"status":"ok"}` and nothing else.
`GET /v1/health` does not exist and will 404.

---

## 1. Roles and identity

### There are exactly two roles

`api/security.py:95-101`:

```python
ROLE_ADMIN = "admin"
ROLE_USER  = "user"
VALID_ROLES = frozenset({ROLE_ADMIN, ROLE_USER})
```

`researcher` is accepted as a **deprecated alias** and normalized to `user` with
a one-time warning (`normalize_role`, `api/security.py:109-124`). `engineer` and
`manager` were removed outright and are **rejected at startup**
(`api/security.py:104`, `:941-950`) — so a tenant whose `tenant.env` still names
one will not boot. `admin` short-circuits every role gate
(`require_role`, `api/security.py:1226-1259`, the bypass at `:1252`).

### A bearer identity never inherits `DEFAULT_ROLE`

This is deliberate and load-bearing. `api/security.py:638-654`, verbatim from
the docstring and body:

```
literal. ``settings.default_role`` is not consulted and must never appear in
this function or its callers: it is ``admin`` in production, and inheriting
it would make every authenticated end user a superuser.
"""
if subject in admin_subject_allowlist():
    return ROLE_ADMIN
if await _stored_role_is_admin(subject):
    return ROLE_ADMIN
return ROLE_USER
```

So a signed-in user gets the literal `user` unless one of the two admin sources
names them. The same intent is restated at the call site
(`api/security.py:832-836`), in the module docstring (`:22-26`), in the router
(`api/routers/admin_users.py:3-8`) and in `config.py:676-678`.

**Correction worth knowing:** the *code* default of `DEFAULT_ROLE` is `"user"`,
not `admin` — `config.py:624`, `default_role: str = "user"`, commented "Least
privilege by default". The "`admin` in production" the comments refer to is a
**deployment override in a `tenant.env`**, not the product default. Check the
tenant you are on:

```bash
grep -E '^DEFAULT_ROLE=' /rag/data/tenants/$T/config/tenant.env
```

`DEFAULT_ROLE` only ever applies to the API-key and keyless paths. The one
startup warning about it is narrower than it sounds: `validate_rate_limit_settings()`
(`api/security.py:990-1023`) warns only when `DEFAULT_ROLE=admin` **and at least
one `RATE_LIMIT_*` setting is greater than 0** — because an `admin` principal is
exempt from the rate-limit bucket, so the limiter would be a silent no-op for
every keyless or unmapped-key caller. With no rate limit configured there is no
warning at all, and configured API keys are not what triggers it. It only warns;
it never refuses to start.

### The two admin sources

| source | where | notes |
|---|---|---|
| `ADMIN_SUBJECTS` env allowlist | `config.py:656-661` (comma-split); read at call time by `admin_subject_allowlist()`, `api/security.py:391-400` | Checked **first**, precisely because it needs no store — it is the recovery path when the user store is down |
| `users.role == 'admin'` | column at `user_store.py:198`; `UserRecord.is_admin` at `user_store.py:203-210`; auth-path read `_stored_role_is_admin`, `api/security.py:568-596` | "only the literal `'admin'` elevates"; `''` reads as user. **Fails closed to `user`** on any store error |

Both are named as "exactly two admin sources" at `api/security.py:28-39`.
Validated at startup by `validate_admin_subjects_settings()`
(`api/security.py:1032`, invoked `api/deps.py:1551`).

Read the tenant's allowlist:

```bash
grep -E '^ADMIN_SUBJECTS=' /rag/data/tenants/$T/config/tenant.env
```

### How a subject is spelled

`<issuer>:<subject>` — e.g. `bvbrc:alice@patricbrc.org`. It is composed in
exactly one place, `api/security.py:825`:

```python
subject = f"{identity.issuer}:{identity.subject}"
```

with the comment above it: *"The one place the tenant/authorization subject is
spelled: built once here and reused for the profile upsert, the role lookup and
the Principal."* There is **no** named helper that composes it — do not go
looking for a `qualify_subject()`. The validator on the admin route is
`_clean_subject()` (`api/routers/admin_users.py:150-181`), which checks that both
halves are non-empty; it does not compose.

An unqualified name (no colon) is **not** a valid subject for the role route —
it is a 400.

### Promoting someone to admin

`PATCH /v1/admin/users/{subject}/role` — `api/routers/admin_users.py:184`, under
prefix `/v1/admin` with `Depends(require_role(ROLE_ADMIN))` at include time
(`api/main.py:237`, `:257-259`).

```bash
curl -s -X PATCH "$BASE/v1/admin/users/bvbrc:alice@patricbrc.org/role" \
  -H "X-API-Key: $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"role":"admin"}'
```

The colon in the path segment is literal and unencoded — the route template is
`/users/{subject}/role` and FastAPI hands it over intact.

- **Body** `UserRoleRequest` (`admin_users.py:102-114`): one field, `role`,
  `"admin"` or `"user"`. `extra="forbid"` — a typo'd field name is a 422, not a
  silent no-op.
- **Response** `UserRoleRecord` (`admin_users.py:117-130`): `subject`, `role`,
  `role_set_by`, `role_set_at`, `env_admin`. **`env_admin` is the field to
  read**: it says whether the subject is *also* in `ADMIN_SUBJECTS`, in which
  case demoting them in the store changes nothing.
- **Statuses**: 400 unknown role or colon-free subject; **404 when there is no
  `users` row** — a person who has never authenticated cannot be promoted in
  advance, they must sign in once first; 409 when this would remove the last
  admin with no other admin source; 503 store outage. Idempotent.
- **The change is not instant fleet-wide.** It flushes *this process's* role
  cache (`reset_role_cache()`, `admin_users.py:274`); other workers lag by
  `ADMIN_ROLE_CACHE_TTL_SECONDS` (`config.py:670`, default 30 s). Tell the person
  to wait half a minute before concluding it did not work.

**There is no `GET /v1/admin/users`.** The only route under `/users` is the
PATCH above — there is no list-users or get-user endpoint in v1. To see whether
someone exists you either promote them (and read the 404) or look at the
tenant's user store directly.

---

## 2. Adding and checking test users

### A human test identity

Humans are **not provisioned** — a `users` row is upserted on first
authentication (`api/security.py:825-836` composes the subject and drives the
profile upsert). So the flow is:

1. The person signs in to the tenant UI once, or calls any authenticated
   endpoint with their token.
2. *Then* you can promote them (§1) or name them as a share grantee.

To verify an identity works before an event, have them call a cheap
authenticated route and read the role back:

```bash
curl -s -H "Authorization: $USER_TOKEN" "$BASE/v1/stats/tenants?counts=false"
```

`GET /v1/stats/tenants` is available to **any authenticated caller**
(`api/routers/stats.py:211`, router gated only by `Depends(resolve_principal)` at
`api/main.py:213-215`), and `?counts=false` (`stats.py:214-220`) returns the
identity and reach fields with every count `null` and probes no store — the
cheapest possible "who am I and what can I see" call. The response
(`TenantsResponse`, `stats.py:199-208`) carries `tenant`, `role`, `readable`,
`restricted_to`, `auth_enabled`, `policy` and `tenants`. **`role` in that
response is the answer to "did my promotion take effect".** `policy` is
populated for admins only.

### Service accounts

For a bot, a loader, or a shared kiosk. The four routes are in
`api/routers/service_accounts.py`, mounted at `/v1/admin` and admin-gated
(`api/main.py:249-251`):

```bash
# create — 201
curl -s -X POST "$BASE/v1/admin/service-accounts" \
  -H "X-API-Key: $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"subject":"ingest-bot","purpose":"hackathon bulk loader"}'

# list — 200
curl -s -H "X-API-Key: $ADMIN_KEY" "$BASE/v1/admin/service-accounts?limit=100"

# disable / enable — 204, idempotent
curl -s -X POST "$BASE/v1/admin/service-accounts/ingest-bot/disable" -H "X-API-Key: $ADMIN_KEY"
curl -s -X POST "$BASE/v1/admin/service-accounts/ingest-bot/enable"  -H "X-API-Key: $ADMIN_KEY"
```

| route | line | notes |
|---|---|---|
| `POST /v1/admin/service-accounts` | `service_accounts.py:236` | 201. Body `{subject, purpose}`, `extra="forbid"`. `subject` is 1–128 chars, **must be colon-free**, and may not be `default` or `public`. `purpose` is free text, sanitized and truncated to 256 chars |
| `GET /v1/admin/service-accounts` | `service_accounts.py:273` | query `created_by`, `limit` (default 100, capped at `max_list_limit`). Returns `{"service_accounts":[…]}` |
| `POST …/{subject}/disable` | `service_accounts.py:353` | 204. 409 if you disable your own subject; 409 on a `human` row; 404 unknown |
| `POST …/{subject}/enable` | `service_accounts.py:375` | 204 |

`ServiceAccountInfo` (`service_accounts.py:130-138`): `subject`, `purpose`,
`created_by`, `created_at`, `disabled_by`, `disabled_at`, `enabled_by`,
`enabled_at`, `active`.

> **This surface never mints a credential** and never returns key material. It
> records that a subject is a service account and whether it is active. The
> credential itself comes from the control plane:
> `ragstack-ctl key mint <tenant> <label> --role admin|user [--restart]`, with
> `key revoke <tenant> <id>` to withdraw it
> (`go/cmd/ragstack-ctl/main.go:136-140`). The ctl also has
> `sa create|disable|enable <tenant> <subject>` and
> `admin add|remove <tenant> <subject>` as env-level equivalents of the API
> routes above.

**A service account is spelled `@service:<subject>` when you grant it access** —
e.g. `@service:ingest-bot`. That prefix is `_SERVICE_PREFIX`
(`api/routers/collections.py:1408`) and is parsed at
`api/routers/collections.py:1517-1548`. Only the `@`-sigil form is accepted; a
bare `service:x` is instead read as the federated subject with issuer `service`.

---

## 2a. Many keys, one subject — giving each key holder their own identity

**Symptom.** A batch of API keys was handed out, and `API_KEY_TENANTS` maps them
all onto one subject. Everyone who holds a key is then the same principal:

- **The per-owner quota is shared.** `MAX_COLLECTIONS_PER_OWNER` is per subject,
  so thirty people share ten collections. The refusal tells someone who owns
  nothing that they already own ten.
- **They co-own each other's work**, including `DELETE /v1/collections/{id}`.
- **Nothing is attributable.**

**The fix does not reissue anything.** The mapping is key → subject, so
re-pointing the keys at distinct subjects changes no key value and nobody needs
a new credential. One edit, one restart.

```bash
# 1. look at the distribution FIRST — it picks the largest role-matching group
python3 python/scripts/split_key_subjects.py <tenant>/config/secrets.env \
    --tenant-env <tenant>/config/tenant.env
```

Measure which subject the keys are actually on; do not assume. "Which subjects
exist" and "how the keys divide between them" are different questions, and only
the second decides what to split. A tenant can easily have two subjects where
one carries a single operator key and the other carries all thirty.

```bash
# 2. register each new subject as a service account, while the OLD mapping is
#    still live, so the records exist before anyone authenticates as them
curl -s -X POST "$BASE/v1/admin/service-accounts" -H "$ADMIN" \
     -H 'Content-Type: application/json' \
     -d '{"subject":"attendee-01","label":"attendee 01"}'

# 3. apply (takes a timestamped backup), then restart by the RECORDED PID
python3 python/scripts/split_key_subjects.py <tenant>/config/secrets.env --apply
```

### ⚠️ Confinement makes a created collection vanish

If the old subject appears in `TENANT_COLLECTIONS`, it is **confined** to those
collections — and confinement applies to **reads**. A collection created under a
confined subject is invisible *even to its creator*: the create succeeds, then
the collection is absent from `GET /v1/collections`.

Drop the stale entry in the same edit as the split. An unlisted subject is
unrestricted, which is what an attendee wants — their own collections plus
everything public. The script warns when it sees this if you pass `--tenant-env`.

### What does not move

Collections created before the split stay owned by the **old** subject, which
nobody authenticates as afterwards. Transferring them out is
[#558](https://github.com/wilke/ragstack/issues/558) and hands over a collection
the new owner cannot read, so in practice they become admin-only or are
re-created. **Split early**: one orphan is cheap, twenty is not.

Check the arithmetic before choosing a per-owner cap — *n* subjects at the cap
must stay under `MAX_COLLECTIONS`, or the tenant bound evicts somebody's work.

### Later, to give people their real identity: share, do not transfer

Grant the person's `bvbrc:` subject **read** on their key-owned collection.
`shared_scope` widens a grantee to the owner's chunks, so they can search their
own library signed in as themselves, with no CLI. Writes stay with the key. That
is attribution plus real-identity access without waiting on #558.

## 3. Sharing a collection on a user's behalf

All four routes are on the collections router under `/v1`, open to any
authenticated caller at include time (`api/main.py:216-218`) and then gated
per-route. **The path parameter is `{collection_id}`.**

| verb + path | line | status |
|---|---|---|
| `GET /v1/collections/{collection_id}/shares` | `collections.py:1569` | 200 |
| `POST /v1/collections/{collection_id}/shares` | `collections.py:1609` | **201** |
| `DELETE /v1/collections/{collection_id}/shares/{share_id}` | `collections.py:1742` | **204**, empty body |
| `POST /v1/collections/{collection_id}/owner` | `collections.py:1865` | 200 |

Listing and granting are **owner-or-admin** — `enforce_access(principal,
entry.id, "owner")` at `collections.py:1594` (list) and `:1650` (grant). As an
operator with an admin credential you can therefore act on a user's behalf
without their token. Denials
are leak-safe: 404 for a collection you cannot read, 403 for readable-but-not-
owned, 503 when the authorization store is down ("refusing to serve (fail
closed)").

### List

```bash
curl -s -H "X-API-Key: $ADMIN_KEY" \
  "$BASE/v1/collections/$C/shares?include_revoked=true"
```

`include_revoked` (`collections.py:1576-1579`, default `false`) brings back the
soft-revoked rows — that is your audit history. Response `SharesResponse`
(`collections.py:1457-1459`): `{"shares":[…], "owner": <subject|null>}`;
each `ShareInfo` (`collections.py:1441-1455`) is `id`, `collection_id`,
`grantee_type` (`"user"|"group"`), `grantee_id`, `permission`, `granted_by`,
`granted_at`, `revoked_by`, `revoked_at`, `active`.

### Grant

```bash
curl -s -X POST "$BASE/v1/collections/$C/shares" \
  -H "X-API-Key: $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"grantee":"bob@patricbrc.org","permission":"read","issuer":"bvbrc"}'
```

Body `ShareGrantRequest` (`collections.py:1411-1439`), `extra="forbid"`:
`grantee` (required), `permission` (default `"read"`), `issuer` (default
`"bvbrc"`).

**Grantee grammar** — `_resolve_grantee()`, `api/routers/collections.py:1477-1566`.
First match wins, after `strip()`:

| form | resolves to | line |
|---|---|---|
| empty / whitespace | 422 `"grantee must not be empty or whitespace"` | `:1501-1502` |
| `@public` or bare `public` | the built-in group `public` | `:1503-1504` |
| `@group:<id>` or `group:<id>` | that group. Empty id → 422 | `:1507-1514` |
| `@service:<subject>` | that service account, kept verbatim and colon-free | `:1517-1548` |
| anything containing `:` | a verbatim federated `issuer:subject` | `:1549-1562` |
| a bare username | qualified to `<issuer>:<name>`, i.e. `bvbrc:<name>` by default | `:1563-1566` |

Group forms are matched **before** the colon rule, so `group:eng` is never
mis-read as issuer `group`. `@service:` rejects a subject containing `:`
(422 — it would forge a federated identity) and rejects the reserved subjects
`default` and `public` (422 — those are the shared fallback tenants unmapped
keys resolve to, so granting to one would share with every such caller;
`user_store.py:99`).

> **`bvbrc` is hard-coded, not derived from `IDENTITY_PROVIDER`.**
> `_DEFAULT_ISSUER = "bvbrc"` (`collections.py:1387`) is used only as the
> Pydantic default of the request's `issuer` field, and `_resolve_grantee` reads
> *only* its `issuer` argument — it never consults `settings.identity_provider`.
> On a tenant with a different provider you must pass `"issuer": "<label>"`
> explicitly, or give the full `issuer:subject` string. On this fleet every
> tenant sets `IDENTITY_PROVIDER=bvbrc`, so the default happens to be right —
> that is a coincidence of configuration, not a derivation.

**Only `read` is grantable** (`collections.py:1653-1662`):

```python
if perm == PERM_OWNER:
    raise HTTPException(400, "ownership is transferred, not granted; use "
                             f"POST /v1/collections/{entry.id}/owner")
if perm != PERM_READ:
    raise HTTPException(422, f"v1 shares are read-only; permission {perm!r} is not allowed")
```

So `"permission":"owner"` → **400** pointing at the transfer route, and
`"permission":"write"` → **422**. Write and delegated (grant-option) shares are a
deferred MVP cut (`authz.py:24`). Other statuses: 409 when the grantee already
owns the collection (`collections.py:1698-1701`) or already holds an active grant
(`collections.py:1729-1732`, where the store's `ShareInvariantError` becomes the
409); 422 `"unknown group {id!r}; create it via POST /v1/groups first"`
(`collections.py:1682-1685`) — groups must exist before they can be granted to.

### Revoke — soft, and it cascades

```bash
curl -s -X DELETE "$BASE/v1/collections/$C/shares/$SHARE_ID" -H "X-API-Key: $ADMIN_KEY"
```

204, no body. **Soft**: the row is kept with `revoked_at`/`revoked_by` set and is
never deleted (`acl_store.py:18-20`; `revoked_at == ""` is what "active" means,
`acl_store.py:108`). That is why `?include_revoked=true` can answer "who had
access last Tuesday".

**Cascading**: `_revocation_plan()` (`acl_store.py:308-375`) revokes the named
share *and* every active share whose `granted_by` chain leads back to a grantee
that just lost all access. It is a least-fixpoint grounding computation, not a
one-level sweep: a share survives only if its grantor is grounded independently
of the revoked set — the active **owner row is never collateral damage**
(`:343-354`), nor is a share from an external/system grantor such as
`system:backfill`, nor a self-grant. Mutual-grant cycles with no external root
*are* revoked, deliberately. The router logs the cascade count
(`collections.py:1804-1807`), so `grep 'cascade revoked'` in the tenant log tells
you how much a revoke actually took down.

Two refusals to expect: 404 for an unknown share **or one belonging to a
different collection** (`:1786-1802` — the store looks up by id alone, so the
router pre-checks membership), and 409 on the owner row:
`"the owner row is not revocable via the share API; delete the collection or
transfer it"` (`:1789-1793`).

### Transfer ownership

```bash
curl -s -X POST "$BASE/v1/collections/$C/owner" \
  -H "X-API-Key: $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"subject":"carol@patricbrc.org","issuer":"bvbrc"}'
```

Body `OwnerTransferRequest` (`collections.py:1814-1840`): `subject`, `issuer`,
`extra="forbid"`; the subject uses the same grantee grammar. Response
`OwnerTransferResponse` (`collections.py:1842-1852`): `collection_id`, `owner`,
`previous_owner`, `revoked_share_id`, `previous_owner_retains_read`, `share`.

- **The outgoing owner keeps nothing.** Their row is soft-revoked and they get no
  consolation read grant — the handler's docstring spells out why
  (`collections.py:1891-1903`). If they still need access, grant them `read`
  afterwards as a separate call.
- 400 on a group subject (`:1947-1956`) — ownership is grantable to users only.
- 409 when the recipient already owns it, or there is no active owner row, or
  the recipient is at their owner quota (§5).
- 422 for a malformed subject, or a recipient who has never been seen (when the
  actor is not an admin).
- **Read §4 before transferring anything.** Transfer moves the ACL row and
  nothing else.

### Groups

Any authenticated caller can create and list (`api/main.py:219-224` gates the
router with `resolve_principal` only; `create_group` at `groups.py:203-207`);
get/delete/member-management are owner-or-admin through `_authorize_group`
(`groups.py:156`), called at `groups.py:254`, `:272`, `:309` and `:352`. A
non-member gets a leak-safe 404.

| verb + path | line | status | body / params |
|---|---|---|---|
| `POST /v1/groups` | `groups.py:203` | 201 | `{"name":"lab-a"}` |
| `GET /v1/groups` | `groups.py:227` | 200 | — |
| `GET /v1/groups/{group_id}` | `groups.py:246` | 200 | → `{"group":…, "members":[…]}` |
| `DELETE /v1/groups/{group_id}` | `groups.py:264` | 204 | soft-delete |
| `POST /v1/groups/{group_id}/members` | `groups.py:291` | 201 | `{"subject":"bob@patricbrc.org","issuer":"bvbrc"}` |
| `DELETE /v1/groups/{group_id}/members/{subject}` | `groups.py:328` | 204 | `?issuer=bvbrc` |

Members resolve through the same `_resolve_grantee`, and **groups do not nest**:
a `@public`/`@group:` member is 422 `"a group member must be a user, not a
group (no nesting)"` (`groups.py:311-314` on add, `:354-357` on remove).
Removing a non-member is
a 204 no-op. An empty name is 422; a reserved name (`public`) or a name collision
for the same owner is 409. Then share to the group with
`{"grantee":"@group:<group_id>"}`.

For an event, the shape that works is: one group per team, `read` share to the
group, and membership managed in the group rather than by re-granting the
collection.

---

## 4. ⚠️ The trap: chunks are stamped with the **ingesting** principal

**This is the failure that will eat your afternoon**, because it produces a
`200 OK` with an empty result set rather than an error. Read this section before
you bulk-load anything into somebody else's collection.

### What the code does

At ingest, every chunk is stamped with the ingesting principal's subject:

```
python/ragstack/ingestion/pipeline.py:340
    chunk.metadata["tenant_id"] = tenant_id
```

The field is `tenant_id`, aliased `OWNER_FIELD` at `python/ragstack/tenancy.py:36`.
The value is `principal.tenant`, which for a bearer identity *is* the subject
`issuer:subject` (`api/security.py:1217-1218`, `authz.py:9-12`). Graph triples
get the same stamp (`pipeline.py:478`).

> **Correction to a widely-circulated citation.** This is **not** at
> `python/ragstack/api/routers/documents.py:925`. That line stamps the **job
> row** (`tenant_id=principal.tenant` passed to `job_lifecycle`, the GoWe branch
> of `POST /v1/ingest`; same pattern at `:974`, `:1281`, `:1328`). The chunk
> stamp is `ingestion/pipeline.py:340`. If you are going to check one line,
> check that one.

`tenancy.py:9-13` describes the field as "provenance plus defence in depth —
since #243 access is asserted at the *collection*, not by this filter". **In
practice it is load-bearing at read time**, because there are two independent
gates and a query must pass both:

- **Gate A — authorization.** `enforce_access` → `resolve_access`
  (`authz.py:63-131`): owner, grant, public, or admin bypass.
- **Gate B — data visibility.** A `tenant_id` filter merged into the store query
  **last**, so a client-supplied `filters` payload cannot widen it
  (`tenancy.py:78-84`; `readable_tenants()` at `tenancy.py:39-50` yields
  `[tenant, "public"] + extra`). Applied at `api/routers/query.py:673` (single
  collection), `:689`/`:699` (multi-collection), `:834` (`/v1/chunks`), and —
  as a bare `readable_tenants()` call, without a filter dict to merge into —
  `documents.py:1474` for the document listing. Enforced in the store against the
  chunk payload (`stores/qdrant.py:56`, indexed at `:304`; the ES
  `metadata.tenant_id` mapping).

The gap between the gates is closed **only for grantees, and only via the
current owner's subject** — `shared_scope()`, `api/scope.py:39-77`, whose own
docstring (`:45-49`) names the hazard:

> "Read authorization (the ACL share) and data visibility (the per-chunk
> `tenant_id` vector scope) are two independent gates. A private collection's
> chunks are stamped with the OWNER's tenant at ingest, so a grantee whose scope
> is only `{own, public}` passes the read gate but sees zero of the shared
> chunks. This closes that gap…"

and whose implementation (`scope.py:66-77`) widens to `[owner_of(collection)]`
**only when the caller is not the owner**.

### The three ways it bites

1. **Chunks ingested by an admin into a user's collection.** Write access is
   owner-or-admin only (`authz.py:24`; the ingest gate is
   `_authorize_ingest_target`, `documents.py:267-304`, whose `enforce_access`
   call is at `:356`), so an organiser
   can *only* do this with an admin credential — but with one, it works, and the
   chunks are stamped with the **organiser's** subject. The owner then queries:
   `shared_scope` sees that they *are* the owner and returns `[]`
   (`scope.py:75-77`), their scope is `{own_subject, "public"}`, and every chunk
   carries the organiser's subject. **Zero matches. HTTP 200. An empty
   collection, to the person who owns it.**
2. **After an ownership transfer.** `transfer_owner` touches ACL rows only
   (`acl_store.py:455`, `:592`, `:880`); nothing re-stamps chunks anywhere. The
   new owner is in exactly case 1 — the chunks still carry the old owner's
   subject, and being the owner suppresses the widening that would have saved
   them. There is no repair path in the code.
3. **Co-resident or shared-surface collections.** `_widening_eligible`
   (`scope.py:21-36`) suppresses widening entirely for a shared-surface
   collection, or one sharing a physical Qdrant collection / ES index with
   another registry entry. There, even a legitimate **grantee** sees nothing.

A fourth, quieter one: `shared_scope` **fails soft** — an ACL-store hiccup logs
and returns `[]` (`scope.py:70-74`), so grantees silently see zero results
instead of a 503.

### The safe patterns

- **Have the user ingest into their own collection.** This is the default and it
  always works: owner ingests, owner queries, stamp matches.
- **If you must bulk-load for someone**, load as the identity that will own the
  collection — i.e. create the collection under a service account, load as that
  service account, and then **share** it (`read`) to the attendee rather than
  transferring it. A grantee gets the owner-widening; a new owner does not.
- **Ingest first, transfer never** is the shorter rule. If a transfer has already
  happened, the chunks must be re-ingested by the new owner; there is no
  re-stamp.

### How to recognise it in under a minute

An empty answer with a healthy store is the signature. Counts agree with queries
(they use the same widening — `count_scope`/`count_scope_many`, `scope.py:114`,
`:134`), so a collection that reports chunks but answers nothing is a different
bug; a collection that reports **0 chunks to its owner** while the ingest job
says `completed` with a positive `chunks` count (§6) is this one.

```bash
# what the job thought it wrote (admin):
curl -s -H "X-API-Key: $ADMIN_KEY" "$BASE/v1/jobs?limit=25" | python3 -m json.tool
# what the owner can see:
curl -s -H "Authorization: $USER_TOKEN" "$BASE/v1/stats/tenants" | python3 -m json.tool
```

`chunks: 812, status: "completed"` on one side and `vector_count: 0` for that
collection on the other is the trap, not a store outage.

---

## 5. Quotas and limits

### The product defaults

These are the values in `python/ragstack/config.py` — **the code defaults, which
a tenant may override.**

| setting | default | line |
|---|---|---|
| `MAX_COLLECTIONS` | `100` | `config.py:158` |
| `MAX_COLLECTIONS_PER_OWNER` | `5` | `config.py:210` |
| `ALLOW_USER_COLLECTION_CREATE` | `true` | `config.py:192` |
| `MAX_CHUNKS_PER_COLLECTION` | `50_000` | `config.py:178` |

There are **no env-var aliases**: `Settings` (`config.py:25`) declares no
`env_prefix` and no `validation_alias`, so each field maps to its own name,
case-insensitively. The env var is spelled exactly as above.

Semantics that surprise people:

- **`0` means the cap is disabled**, not "refuse everything" — `MAX_COLLECTIONS=0`
  (`config.py:144`, `api/eviction.py:255-264`) and
  `MAX_COLLECTIONS_PER_OWNER=0` (`config.py:209`).
- **Admins are exempt from `MAX_COLLECTIONS_PER_OWNER` but not from
  `MAX_COLLECTIONS`** (`config.py:203-207`, `api/access.py:256-261`).

### Read the tenant's live values, not this table

Every tenant overrides some of these, and a general runbook must not carry one
tenant's numbers. Read them:

```bash
grep -E '^(MAX_COLLECTIONS|MAX_COLLECTIONS_PER_OWNER|ALLOW_USER_COLLECTION_CREATE|MAX_CHUNKS_PER_COLLECTION|RATE_LIMIT_)' \
  /rag/data/tenants/$T/config/tenant.env

python3 -c 'import json,sys
t=json.load(open("/rag/data/tenants/registry.json"))["tenants"][sys.argv[1]]
print(json.dumps(t["settings"], indent=1))' "$T"
```

The registry's `settings` block is a projection taken at adopt time; **the
`tenant.env` is the live truth**, and a divergence between the two means the env
was edited without a re-adopt.

### What the user sees when they hit a limit

**Per-owner quota → 409.** `api/access.py:82-100`, raised on both acquisition
paths — create (`access.py:287`) and ownership transfer
(`collections.py:2086`, which is why a transfer can fail on the *recipient's*
quota). The `detail` is a **JSON object, not a string**:

```json
{"error": "owner_quota_exceeded", "owned": 5, "limit": 5,
 "message": "already owns 5 collection(s), at the quota of 5 (MAX_COLLECTIONS_PER_OWNER); free one up (delete or transfer it away) before acquiring another"}
```

**The user cannot look their quota up.** `GET /v1/config` is admin-only
(`api/routers/admin.py:102`, gated at `api/main.py:237-238`) and it is the only
place `max_collections_per_owner` and `allow_user_collection_create` are exposed
(`admin.py:96-97`). So a user learns their limit **from the 409 and nowhere
else** — which is exactly why the `message` above spells the env var out. When
someone asks "how many am I allowed", you run:

```bash
curl -s -H "X-API-Key: $ADMIN_KEY" "$BASE/v1/config" | python3 -m json.tool
```

Note that `GET /v1/config` does **not** expose `max_collections` at all — it is
absent from the response allowlist (`admin.py:90` has
`max_chunks_per_collection` but no `max_collections`). For the tenant-wide bound
you must read `tenant.env`. Even an admin cannot get it from the API.

---

## 6. Collection capacity and eviction

`MAX_COLLECTIONS` bounds the **physically present** collections — each one holds
real Qdrant and Elasticsearch resources (ADR-0003).

### Automatic LRU eviction at the bound

`POST /v1/collections` evicts on demand: at the cap it calls
`make_room_for_create()` (`api/eviction.py:236-253`) from
`api/routers/collections.py:662-678`, which evicts **exactly one** victim and
retries **once**. A second `AT_CAP` is a 507 — *"a concurrent create took the
slot the eviction freed; retry"* (`collections.py:674-676`).

The LRU key is `last_accessed_at`, falling back to `created_at`, then `-inf`
(`lru_stamp`, `ops/evict.py:158-166`, sorted at `:219` by `(lru_stamp(r), r.spec.id)`).
Batched access touches are flushed before the sort so the key is not stale
(`api/eviction.py:10-12`). A collection is eligible only if it is `active`, not
`archive_pending`, has archive versions, has no in-flight job, and is not
`protected` (`ops/evict.py:16-21`). Restore admission drives the same evict-one
machinery (`RestoreCapacity.make_room`, `api/eviction.py:298-313`, called from
`api/lifecycle.py:289`) but answers **503 + `Retry-After`** rather than 507: when
`make_room()` returns a reason instead of `None`, the gate raises it at
`api/lifecycle.py:251` through `_retry` (`api/lifecycle.py:178-183`, which is
where the `Retry-After` header is attached).

### The operator's handle

```bash
curl -s -X POST -H "X-API-Key: $ADMIN_KEY" \
  "$BASE/v1/admin/collections/evict?need=3&dry_run=true" | python3 -m json.tool
```

`api/routers/admin_collections.py:22-28`; `need` is `1..1000` (default 1),
`dry_run` defaults false. **It always returns 200** — a shortfall is data, not an
error (`admin_collections.py:6-7`). `EvictionResponse` (`api/eviction.py:89-95`):

```
{need, dry_run, evicted, victims[], shortfall{needed, found, reasons{…}}}
```

`victims[]` (`eviction.py:59-69`): `collection_id`, `last_accessed_at`,
`idle_seconds`, `state`, `reason`, `deleted[]`, `absent[]`, `failed[]`, `ok`.
`shortfall.reasons` (`eviction.py:72-79`) counts `not_active`, `archive_pending`,
`no_archive`, `in_flight`, `protected`, `unregistered` — **that histogram is the
diagnosis**: `no_archive` means nothing can be evicted because nothing has been
archived, which is an operator problem, not a user one.

**Always run `dry_run=true` first.** Eviction drops the collection's physical
Qdrant and ES resources.

### The two refusals

- **507** when nothing is evictable — `api/eviction.py:214-232`. The `detail` is
  a **string**, and it is written to be shown to a user: *"active collection
  bound reached (N): … Delete unused collections, wait for in-flight ingests and
  pending archives, or have the operator raise MAX_COLLECTIONS"*. A
  `Retry-After: 5` header is attached only on the transient variants.
- **403**, not 507, when the *effective* cap is zero —
  `collections.py:424-434`, raised at `:660`. `effective_limit()` is
  `max_collections` minus one when a shared-surface pointer exists
  (`api/eviction.py:255-264`), so a tenant with `MAX_COLLECTIONS=1` and a shared
  surface has an effective cap of 0 and refuses **every** create with a 403. If
  users report "I cannot create anything at all", check for the 403 before
  investigating capacity.

---

## 6a. Which ingest backend a tenant runs, and what it costs you

`INGEST_BACKEND` takes exactly two values, and the router **501s anything else**
(`api/routers/documents.py::_refuse_unknown_backend`). It decides where an upload
is staged, and therefore what credential a caller needs:

| `INGEST_BACKEND` | Uploads staged to | Caller needs |
|---|---|---|
| `local` (the default, when the key is unset) | `INGEST_ROOT` on the API host | any authenticated principal — an API key is fine |
| `gowe` | the **caller's own BV-BRC Workspace**, `ws://…`, and a workflow submitted as them | a **BV-BRC bearer token**; an API key or keyless caller gets 401 |

The `gowe` rule has one implementation, `api/security.py::gowe_caller`, and its
docstring is explicit that there is no fallback identity and none may be added:
writing into somebody's Workspace requires that somebody's credential.

Read the fleet before assuming:

```bash
for t in /rag/data/tenants/*/config/tenant.env; do
  printf '%-14s ' "$(basename "$(dirname "$(dirname "$t")")")"
  grep -hE '^INGEST_BACKEND=' "$t" || echo 'INGEST_BACKEND=local (unset)'
done
```

**Why an operator cares.** A test rig, a scripted bulk load, or any automation
holding an API key can exercise the upload path only on a `local` tenant. On a
`gowe` tenant the same automation needs a real user's token, which is a different
kind of secret with a different lifetime. Put automation that must upload on a
`local`-backend tenant, and keep the token-based path for testing the Workspace
behaviour itself.

**Other object stores are not a configuration away.** There is no Shock or S3
backend: `_refuse_unknown_backend` rejects any value that is not `local` or
`gowe` with a 501, so adding one is an implementation task (a backend in
`ingestion/`, its staging and reference-resolution rules, and the guard widened),
not a `tenant.env` edit.

## 6b. Exposing a shared corpus without copying it

A tenant can serve a collection whose stores live on **another instance**, so a
large corpus is registered once and read by several tenants rather than copied
per tenant. Introduced in `v1.6.2` (`ES_COLLECTION_ROUTES`, the twin of the
existing `QDRANT_COLLECTION_ROUTES`).

Both legs must be routed — the vector leg and the text leg — or retrieval reads
one store and misses the other:

```bash
# in the tenant's tenant.env, keyed by the PHYSICAL store name, not the collection id
QDRANT_COLLECTION_ROUTES='{"ragstack_lib_<store>":"http://localhost:6333"}'
ES_COLLECTION_ROUTES='{"ragstack_lib_<store>":"http://localhost:9200"}'
```

**Order matters: register, then route.** Register the collection on the tenant
with the *same collection id* AND the *same build spec* as the origin — same
model, dimension and chunking. The physical name is hashed from
`name|model|dim|chunk` (`stores/qdrant.py:186`), so the id is part of it:
registering the same corpus under a different id mints a different store name and
the routes silently miss. A different
spec mints a different name and the routes will not match anything.

**Never purge a routed collection.** A purge would destroy stores the tenant does
not own and other tenants are still reading. `v1.6.2` refuses it, but the reason
to know this is that the refusal is the *second* line of defence, not the first.

Verifying a route landed:

```bash
# the routed index must NOT appear in the tenant's own Elasticsearch
curl -s "http://127.0.0.1:<tenant_es_port>/_cat/indices?h=index" | grep <store>
# …and a query against the collection must return hits and scores matching the origin
```

A routed collection still needs its own share and, on a keyed deployment, an
entry in the read-only keys' allowlist — routing makes the data reachable, not
readable.

## 7. Diagnosing a user report

### "My ingest failed and it won't tell me why"

Correct — it will not. `IngestResponse` (`api/routers/documents.py:847-859`) has
exactly five fields: `job_id`, `status`, `chunk_ids`, `items`, `collection`.
**There is no error field**, and the contract forbids one
(`jobstore.py:46-48`: *"Never exposed on IngestResponse —
`contracts/schemas/ingest_response.json` forbids it via
`additionalProperties: false`"*).

The reason lives on the job row, and `GET /v1/jobs` is **admin-only**
(`api/routers/jobs.py:40`, gated at `api/main.py:241`) because a job's `source`
is a raw path and would leak across tenants (`jobs.py:1-8`). So the user
genuinely cannot self-serve this; you have to look:

```bash
curl -s -H "X-API-Key: $ADMIN_KEY" "$BASE/v1/jobs?limit=25" | python3 -m json.tool
```

`limit` is 1–100, default 25 (`jobs.py:42`). `JobSummary` (`jobs.py:27-33`):
`job_id`, `status`, `source`, **`error`**, `chunks`, `items{pending, completed,
failed}`. Status vocabulary: `accepted`, `running`, `completed`, `failed`,
`unknown` (`jobstore.py:26-31`).

**`error` is a caller-safe label only** — an exception class name, never a raw
path or an upstream message (`jobstore.py:43-45`). So it tells you the *class* of
failure; the detail is in the tenant log, which is where
[`tracing-a-503.md`](tracing-a-503.md) picks up.

**There is no `GET /v1/jobs/{id}`.** The single-job poll is
`GET /v1/ingest/{job_id}` (`documents.py:1384`), which is non-admin and
tenant-scoped — but it returns `IngestResponse`, so it gives `status: "failed"`
and no reason. An unknown id there is `status: "unknown"` with **200**, not a
404 (`documents.py:1393-1394`), so "unknown" does not distinguish a typo'd id
from an expired one.

### "Everything is slow / something is broken"

```bash
curl -s "$BASE/health"                                                    # unauth, {"status":"ok"}
curl -s -H "X-API-Key: $ADMIN_KEY" "$BASE/v1/health/deep" | python3 -m json.tool
curl -s -H "X-API-Key: $ADMIN_KEY" "$BASE/v1/stats/stores" | python3 -m json.tool
curl -s -H "X-API-Key: $ADMIN_KEY" "$BASE/v1/stats/tenants" | python3 -m json.tool
```

- **`GET /v1/health/deep`** — `api/routers/health_deep.py:58`, **admin-only**
  (`api/main.py:239`). Returns `{status: "ok"|"degraded", checks: [...]}`; each
  check is `{name, ok, detail, latency_ms}` and the four names, in order, are
  `vector`, `text`, `graph`, `jobstore` (`health_deep.py:90-95`). `detail`
  carries the backend name on success and `"{backend}: {ExcType}: {msg}"` on
  failure (`:51-53`). Any failing check makes `status` `degraded`.
  **`latency_ms` is the number to read** when the complaint is "slow" rather
  than "broken".
- **`GET /v1/stats/stores`** — `api/routers/stats.py:111`. **Not admin-only**:
  any authenticated caller, tenant-scoped in the handler
  (`api/main.py:213-215`). `{tenants, vector, text, graph}` where each store is
  `{backend, available, count}`.
- **`GET /v1/stats/tenants`** — `api/routers/stats.py:211`, also not admin-only.
  `?counts=false` skips every store probe. Per-collection `vector_count` /
  `text_count` are here, which is what you compare against a job's `chunks`
  when chasing §4. `policy` is populated for admins only.

Note the asymmetry: `/v1/health/deep` is admin-gated but the two `/v1/stats/*`
routes are not. A user *can* be asked to run the stats calls themselves.

### A 503 with a `Reference:` id

Do not improvise. [`tracing-a-503.md`](tracing-a-503.md) is the decision
procedure from a user's screenshot to the leg that was slow — the `Reference:`
line is the `rid` grep key, `X-Request-Id` is the header form, and the runbook
covers the cases where the body carries no id at all. Follow it rather than
restating it here.

---

## 8. Turning the log level up (and back down)

Three admin routes, `api/routers/admin_log_level.py`, mounted at `/v1/admin`
(`api/main.py:271-272`):

| verb + path | line |
|---|---|
| `GET /v1/admin/log-level` | `:148` |
| `PUT /v1/admin/log-level` | `:155` |
| `DELETE /v1/admin/log-level` | `:186` (reset, idempotent, always 200) |

```bash
# raise everything to debug for 10 minutes, then auto-revert
curl -s -X PUT "$BASE/v1/admin/log-level" \
  -H "X-API-Key: $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"level":"debug","ttl_seconds":600}'

# or just one logger, leaving the root level alone
curl -s -X PUT "$BASE/v1/admin/log-level" \
  -H "X-API-Key: $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"loggers":{"ragstack.stores.qdrant":"debug"},"ttl_seconds":600}'

curl -s -X DELETE "$BASE/v1/admin/log-level" -H "X-API-Key: $ADMIN_KEY"
```

Body `LogLevelRequest` (`admin_log_level.py:80-145`), `extra="forbid"`. All three
fields are optional but **at least one is required** — an empty body is a 422 and
changes nothing.

- `level` — case-insensitive; `warn` is accepted; `NOTSET` is rejected.
- `loggers` — a `{name: level}` map with **REPLACE semantics**: `{}` clears every
  per-logger override, and omitting the key leaves existing ones untouched. Do
  not send a one-logger map expecting it to be additive.
- `ttl_seconds` — 1..86400, auto-reverts. **Always set it.** An event is exactly
  the situation where a debug level set "just for a minute" is still on at
  midnight.

The response (`LogLevelResponse`, `admin_log_level.py:47-77`) carries `pid`,
`configured_level`, `configured_level_resolved`, `effective_level`,
`runtime_override`, `changed_at`, `changed_by`, `dampening_active`,
`dampen_loggers[]`, `loggers[]`, `logger_override_count`,
`max_logger_overrides`, `auto_revert_pending`, `ttl_seconds`, `expires_at`,
`expires_in_seconds`, `max_ttl_seconds`.

**The override is process-local and resets on restart**
(`admin_log_level.py:18-20`) — so the `pid` in the response matters, and a
tenant restarted mid-event silently drops back to its configured level. Every
change is audited at WARNING with the principal's tenant.

---

## Related

- [`tenant-upgrade.md`](tenant-upgrade.md) — moving the tenant to a new tag,
  and the current fleet state.
- [`tracing-a-503.md`](tracing-a-503.md) — the `Reference:` id procedure.
- [`ctl-quickstart.md`](ctl-quickstart.md) / [`ctl-deploy.md`](ctl-deploy.md) —
  the control plane, including `key mint`, `sa create` and `admin add`.
- `docs/adr/0003-access-control.md`, `0004-users-groups-shares.md` — why there
  are two roles, why access is asserted at the collection, and why revocation is
  soft.
