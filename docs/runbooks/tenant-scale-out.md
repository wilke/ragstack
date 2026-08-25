# Tenant scale-out — provisioning a new tenant and moving a user

Phase 5 of #201 (task spec on the issue). Codifies the interim policy recorded on
#289: at this scale (personal collections, dev tenant, `MAX_COLLECTIONS=100`),
scaling out is an **ops procedure**, not a design change. It stops being
sufficient once #354 (shared-collection partitioning) or #353 (archive +
dormancy, decoupling total users from simultaneously active ones) lands — until
then, this is the playbook.

Every host-specific value below is a placeholder: `<tenant>`, `<base_port>`,
`<user-id>`, `<old-tenant>`, `<new-tenant>`. Never substitute a real tenant name,
port, or hostname into a command you paste into an incident channel or a PR —
read it from the manifest/`tenant.env` on the host instead.

---

## 1. Trigger

Provision tenant N+1 when the **current** tenant's physically-present collection
count reaches **80 % of `MAX_COLLECTIONS`** (per #379, `MAX_COLLECTIONS` bounds
`{active, archiving, restoring}` — the states that hold or are rebuilding a
Qdrant/ES slot; `dormant`/`lost` rows hold no slot and don't count).
[active-collection-bound.md](active-collection-bound.md):22 recommends "100
now; ~150 is defensible without further measurement" — at 100 that's 80
physically-present collections, at 150 it's 120.

Two ways to read the count, and a RAM check alongside them:

**a. Admin `GET /v1/collections`.** Admin sees every registered collection
(non-admin listings are owner-filtered). Each entry carries `state`
(`active | archiving | dormant | restoring | lost`, or null for the
settings-derived default). Count entries whose `state` is `active`, `archiving`,
or `restoring`:

```bash
curl -s -H "X-API-Key: <TENANT_ADMIN_KEY>" "<tenant-api>/v1/collections" \
  | jq '[.collections[] | select(.state == "active" or .state == "archiving" or .state == "restoring")] | length'
```

Add 1 to that number if a `state: null` entry is present (the settings-derived
default collection) — the create path's physically-present count, which
`MAX_COLLECTIONS` is checked against per #379, counts it as holding a store
slot the same as any registered row.

**b. Eviction dry run.** `POST /v1/admin/collections/evict` with `dry_run=true`
reports the same picture from the other side — how much headroom eviction
could free right now, broken down by why a collection is or isn't a candidate
(`not_active`, `archive_pending`, `no_archive`, `in_flight`, `protected`,
`unregistered`):

```bash
curl -s -X POST -H "X-API-Key: <TENANT_ADMIN_KEY>" \
  "<tenant-api>/v1/admin/collections/evict?need=1&dry_run=true" | jq .
```

If eviction can free enough slots (candidates with a current archive and no
in-flight job), scaling out is not yet required — evict first, then re-check
the 80 % line. If the shortfall persists after eviction headroom is used up,
provision N+1.

**c. `store_inventory.py`** — the store-level cross-check, independent of the
registry (physical Qdrant/ES state vs. what any known registry claims). Useful
when the registry and the physical stores might have drifted (a failed drop
left a `dormant` row's stores behind, an unclaimed store from a bulk-CLI path):

```bash
cd python
python scripts/store_inventory.py --tenants-dir <RAG_DATA>/tenants --env <tenant>=<RAG_DATA>/tenants/<tenant>/config/tenant.env
```

**RAM.** Physically-present count is necessary but not sufficient — per
[active-collection-bound.md](active-collection-bound.md), the binding ceiling on
the measured dev tenant was RAM, not the collection count itself (n = 254 at the
full assumed 200 GB share, 152 at a uniform 60 % budget; nothing in
`tenant.env`/`provision.env` states the actual share — "whoever states the
share sets `n`"). Before raising `MAX_COLLECTIONS` past the ~150 default, or
before treating a tenant that is well under 80 % of `MAX_COLLECTIONS` as safe,
sample the host:

```bash
free -g
grep -E 'RssAnon|RssFile|RssShmem|VmHWM' /proc/<qdrant-pid>/status
```

A tenant whose collections run materially larger than the 35k-chunk measurement
basis can hit the RAM ceiling well before the collection-count trigger fires.

---

## 2. Provision tenant N+1

Per [ADR-0005](../adr/0005-tenant-anatomy.md) decision 4, provisioning is a script,
not an API. Always preview first — `--dry-run` prints the complete plan (dirs,
ports, files, commands) and touches nothing:

```bash
cd apptainer
./new-tenant.sh <new-tenant> --dry-run
```

Then provision for real (default: sqlite ACL/registry/job store under the
tenant dir; add `--postgres <admin-dsn>` for a per-tenant database on an
existing Postgres server, the ADR-0004 amendment):

```bash
./new-tenant.sh <new-tenant>
# or:
./new-tenant.sh <new-tenant> --postgres postgresql://<pg-admin-role>:<pw>@<pg-host>:<pg-port>/postgres

# start the tenant's dedicated Qdrant + Elasticsearch
<RAG_DATA>/tenants/<new-tenant>/bin/up.sh
```

### What the script writes

- **A manifest row** — `<new-tenant>\t<index>\t<base_port>`, appended to
  `<RAG_DATA>/tenants/manifest.tsv` (index = max existing + 1; the read-modify-
  write is `flock`-serialized). The row is allocated once and reused verbatim
  on every re-run — never hand-edit it.
- **A port block** — `<base_port>` plus fixed offsets: `+0` API, `+1` Qdrant
  HTTP, `+2` Qdrant gRPC, `+3` ES HTTP, `+4` ES transport, `+5` Postgres
  (reserved, unused unless `--postgres` names an external server). The script
  probes every port for real before writing anything, so a manifest that
  doesn't know about a live deployment's ports can't silently hand out a
  block that's actually occupied.
- **Data directories**, every writable path enumerated under
  `<RAG_DATA>/tenants/<new-tenant>/` (no `--writable-tmpfs`, per house
  convention): `qdrant/storage`, `qdrant/snapshots`, `elasticsearch/{data,logs,config}`,
  `state/` (sqlite DBs when not using `--postgres`), `manifests/`, `ingest/`,
  `config/`, `bin/`. The tenant directory is `chmod 700`.
- **Three env/config files under `config/`:**
  - `tenant.env` — the operator-editable file. Generated API keys
    (`API_KEYS`, `API_KEY_TENANTS`, `API_KEY_ROLES`), `MAX_COLLECTIONS`
    (script default **100**), the dedicated store URLs (`QDRANT_URL`,
    `ELASTICSEARCH_URL`), the shared embedding/reranker endpoints,
    `REQUIRE_DURABLE_BACKENDS=true`, `INGEST_ROOT`. Kept across re-runs unless
    `--force` is passed.
  - `secrets.env` — API keys and (with `--postgres`) the tenant's DB password,
    generated once. Deleting it rotates every secret on the next run. **Never
    read or print this file's contents** — it holds live credentials.
  - `provision.env` — persists the provisioning choices (`--es-heap`, sqlite
    vs. postgres) so a flagless re-run doesn't silently revert them.
- **`bin/up.sh` / `bin/down.sh`** — derived, regenerated deterministically on
  every run. Do not hand-edit; re-run the script to regenerate them.

### Settings that MUST be set explicitly on the new tenant

The script's `tenant.env` template does **not** set these — or stamps a
default that is wrong for this workload — so provisioning a tenant for the
personal-collections workload means editing `tenant.env` (or exporting
overrides) before the API starts:

| Setting | Why it must be explicit |
|---|---|
| `MAX_COLLECTIONS=150` | Script default is **100**. Per [active-collection-bound.md](active-collection-bound.md), ~150 is the defensible number without further per-tenant RAM measurement; leaving the default under-provisions relative to the recommendation this runbook exists to apply. |
| `IDENTITY_PROVIDER=bvbrc` | **Load-bearing for §4.** The script stamps `IDENTITY_PROVIDER=none`. The archive/restore path (#358) submits to GoWe **as the user**, which needs a BV-BRC bearer identity: `gowe_caller()` returns `None` unless `principal.issuer == "bvbrc"` (`ragstack/api/security.py`), and there is no fallback identity. With the script's default, `POST /v1/collections/{id}/restore` and the on-access restore trigger are both dead on this tenant — §4's primary path cannot work until this is set. |
| `ALLOW_USER_COLLECTION_CREATE` | Whether a non-admin can create their own collection at all (default `true` in the product, but the tenant's env should state its intent explicitly rather than inherit a default silently). |
| `MAX_COLLECTIONS_PER_OWNER` | Product default 5 — confirm it matches policy for the new tenant rather than assuming the default is still right at this tenant's expected user count. |
| `MAX_CHUNKS_PER_COLLECTION` | Product default 50,000 — the per-collection size cap ADR-0005/#289's interim policy relies on (bounded by size, not by refusing creation). |
| `GOWE_URL` | The lifecycle gate that wires the restorer (`_build_lifecycle_gate` in `ragstack/api/deps.py`) reads `GOWE_URL`/`WORKSPACE_URL` directly — it is **not gated on `INGEST_BACKEND`**. Required for §4's restore step regardless of how ingest itself is configured. |
| `WORKSPACE_URL` | Same as `GOWE_URL` above — required for the restore path, not conditional on `INGEST_BACKEND`. The user's BV-BRC Workspace is where the archive lives. |
| `INGEST_BACKEND=gowe` | The script's template omits this line (defaults to `local`). Needed so *new* ingest on this tenant also routes through GoWe (interim-policy comment on #289) — set it for that reason, not because the restorer depends on it (it doesn't; see the two rows above). |

Edit `tenant.env` directly (it's the operator-editable file; re-running the
script without `--force` keeps your edits) or export overrides at API-start
time. Whichever you do, capture the diff somewhere durable — the next re-run of
`new-tenant.sh` will keep an edited `tenant.env`, but only if it's the same
file the operator edited.

### Mandatory post-provision checks

Run these from the **repo root** — §2's provisioning commands were run from
`apptainer/`, and starting the API in a subshell keeps the working directory
from drifting so `pytest conformance/` still resolves:

```bash
set -a; . <RAG_DATA>/tenants/<new-tenant>/config/tenant.env; set +a
(cd python && uvicorn ragstack.api.main:app --host 0.0.0.0 --port "$PORT" &)

# 1. liveness (no key)
curl -s "http://localhost:$PORT/health"                                     # {"status":"ok"}

# 2. admin config — confirm the settings above actually took (spot-check key count,
#    not values, so nothing secret-shaped ends up in a paste)
curl -s -H "X-API-Key: <NEW_TENANT_ADMIN_KEY>" "http://localhost:$PORT/v1/config" \
  | jq 'keys | length'

# 3. keyed conformance run against the new tenant (from repo root)
RAGSTACK_BASE_URL="http://localhost:$PORT" RAGSTACK_IMPL=python \
  RAGSTACK_API_KEY=<NEW_TENANT_API_KEY> \
  pytest conformance/
```

A tenant that passes all three is ready to receive new users. One that fails
`/v1/config` or conformance should not be added to the routing map (§3) yet.

---

## 3. Routing map

There is **no dynamic tenant discovery** — routing is a static map, updated by
hand at each of the two places a caller picks a tenant:

- **The frontend backend switcher** (`frontend/src/api/config.ts`,
  `BACKEND_PRESETS`) — a list of `{id, label, url}` entries, where `url` is a
  Vite-proxy path prefix (`/be/<tenant>`) resolved same-origin through the dev
  proxy or the front gateway, not an absolute host:port a browser would have to
  reach directly. Adding a tenant here means adding one entry and wiring the
  matching proxy target.
- **The BV-BRC chatbot configuration** — a static user→tenant map (which
  tenant's API a given user's queries are routed to), analogous to the
  frontend's preset list but on the chatbot side. This repo does not define
  or control that config — treat this bullet as an assertion about an
  external system to confirm with whoever operates the chatbot, not as
  something this runbook can verify or edit.
- **The MCP client** — `go/cmd/mcp`, pointed at exactly one tenant via the
  `RAGSTACK_BASE_URL` environment variable (defaults to `http://localhost:8000`
  if unset). There is no multi-tenant mode in the MCP server; a user who needs
  a different tenant gets a different MCP server instance/config pointed at
  it.

**Rule: new users go to the newest provisioned tenant** (the one just added to
the map in §2), until it in turn approaches the §1 trigger.

**Revisit at three tenants.** A per-user static map does not scale past a
handful of entries by hand, and cross-tenant fusion is explicitly out of reach
under ADR-0005 (see §5) — federated tenancy (a tenant registry, a
cross-tenant scope on the identity, a gateway API doing the same N-leg RRF
fusion as multi-collection search) is the documented answer, and it is
**deliberately deferred** in [ADR-0005 § Deferred: federated tenancy](../adr/0005-tenant-anatomy.md#deferred-federated-tenancy)
pending its own ADR and threat model (a gateway re-centralizes exactly what
per-tenant instances decentralize). Three tenants is the point to stop
hand-editing two static maps and revisit that deferral, not a hard technical
ceiling.

---

## 4. Moving an existing user to a new tenant

### The point-id invariant

Qdrant point ids are `uuid5(NAMESPACE_URL, f"{tenant}:{chunk_id}")`
(`python/ragstack/stores/qdrant.py:_point_id`) — **`tenant` is the payload
string carried in the request's tenant filter, not the physical instance the
Qdrant process runs on.** Moving a collection's physical data from one
tenant's store instance to another's does not change any chunk id and does
not re-embed anything, **provided** the destination tenant resolves the
moved user to the *same* tenant string the data was originally ingested
under. For a bearer-identity principal — the normal case for personal
collections — that string **is** the subject, `f"{issuer}:{sub}"`
(`ragstack/api/security.py:838`), which is globally stable by construction
(ADR-0005 decision 3): the invariant holds automatically across the move,
with no per-tenant mapping to keep in sync. The concrete check is that the
archived `manifest.json`'s `tenant` field equals the moved user's
`issuer:sub`. (An API-key principal is the exception: its tenant string
comes from that tenant's own `API_KEY_TENANTS` mapping, which must be set to
match by hand — that's the one case a naive "just copy the collection" move
can get silently wrong.)

### Primary path: archive-based move (via #358)

Recommended over Qdrant/ES snapshot-restore whenever the collection has a
current archive (`archive_pending=false`, `versions` non-empty) — no snapshot
repository configuration needed, and the Workspace archive is already the
canonical, portable form (per #353: "the portable form is canonical," a Qdrant
snapshot is bound to one dedicated collection's Qdrant instance and can't
restore into a different topology).

There is **no targeted eviction** — the admin evict endpoint is LRU-driven
(`need=k`, whichever collections are oldest by `last_accessed_at`), not "evict
this specific collection." So the move is registry-first, not eviction-first:

1. **On the old tenant**, confirm the collection's archive is current before
   touching anything. There is no single-item `GET` on `/v1/collections/{id}`
   (only `DELETE` and `POST …/restore` take the id as a path param) — filter
   the listing instead:
   ```bash
   curl -s -H "X-API-Key: <OLD_TENANT_ADMIN_KEY>" \
     "<old-tenant-api>/v1/collections" \
     | jq '.collections[] | select(.id=="<collection-id>") | {state, archive_pending, versions}'
   ```
   `archive_pending` must be `false` and `versions` non-empty. If not, the
   collection has no restorable archive yet — a delta may still be in flight;
   don't proceed until this settles or fall back to snapshot/restore (below).

2. **Copy the registry row and its ACL rows into the new tenant's registry.**
   These live in the tenant's ACL/registry database (sqlite or Postgres,
   per `tenant.env`) and must be moved by hand — table by table:

   | Table | What to copy |
   |---|---|
   | `collections` | Copy the row for `<collection-id>` **verbatim, every column** — `collection`, `text_index`, `embedding_*`, `chunk_*`, `spec_hash`, `owner`, `max_chunks`, `versions`, `created_at`/`updated_at`, `last_accessed_at`, and critically **`archive_version`**: this is the `next_version()` counter, kept out of `CollectionRecord`/`put()` deliberately, and a row copied without it re-mints `versions/1/` into a Workspace folder that already has one — silently corrupting the version sequence. Change only `state='dormant'`, `state_reason`, `state_changed_at` on the copy. **`id`, `owner`, and `spec_hash` must be preserved exactly**: the archive lookup is keyed by `/<subject>/home/.ragstack/collections/<id>/versions`, and the loader refuses on a `spec_hash` mismatch. `POST /v1/collections` with the same id is **not** a substitute for this copy — it mints fresh, empty, active physical stores with a newly-derived `spec_hash`, and a subsequent restore call reports "nothing to do" against that empty collection instead of pulling the archive. |
   | `shares` | Every row with `collection_id = <collection-id>` **and `revoked_at = ''`** (no FKs enforce referential integrity between these tables, so don't assume order). The `permission='owner'` row is **mandatory** — restore calls `enforce_access(owner)`, and without that row the owner cannot even trigger their own restore. |
   | `users` | The moved user's row only matters here for `role='admin'` or `kind='service'` — an ordinary human self-provisions on first login (the bearer-identity upsert), so a plain user row is not required for the move to work, though copying it avoids a throwaway provisional row being created first. |
   | `groups` / `group_members` | `groups` carries `owner_subject`/`built_in` — copy any group referenced by a moved `shares` row that doesn't already exist on the new tenant. `group_members` by `subject` — the moved user's membership rows in that group. |

   No FKs exist between these tables, so a `shares` row may legally be
   inserted before the grantor's `users` row — order within the copy doesn't
   matter, but the **set** does: a collection row with no owner share leaves
   it inaccessible; a share with no collection row is dead weight.

   **The new tenant's collection registry is built once at API startup**
   (`app.state.collections`, `_build_collection_registry` in
   `ragstack/api/deps.py`) — a row inserted into the database after the API
   is already running is invisible to it (a restore call 404s) until the
   process restarts. Either insert the copied rows **before** the new
   tenant's API is first started, or restart it immediately after inserting.

3. **Restore on the new tenant.** Either wait for the owner's first
   authenticated read (which triggers a restore submission automatically per
   #358 and returns 503 + `Retry-After` until it completes), or trigger it
   explicitly:
   ```bash
   curl -s -X POST -H "Authorization: Bearer <owner-token>" \
     "<new-tenant-api>/v1/collections/<collection-id>/restore"
   ```
   The restore runs as **the user** (GoWe submission carrying their token, per
   #353) and needs a BV-BRC bearer credential to do it — this is exactly why
   §2's must-set list includes `IDENTITY_PROVIDER=bvbrc`, `GOWE_URL`, and
   `WORKSPACE_URL` on the new tenant: without a `bvbrc` identity `gowe_caller()`
   refuses the submission outright, and without `GOWE_URL`/`WORKSPACE_URL` the
   restorer has nowhere to submit to and nothing to read the archive from
   (`INGEST_BACKEND` is not part of this — the restorer is wired independently
   of it). Expect roughly the cold-build order of magnitude measured in
   [active-collection-bound.md](active-collection-bound.md) (~100 s store-side
   for a 35k-chunk collection) as a floor — a real restore also has to read the
   archive and replay it, so budget more.

4. **Verify, then retire the old tenant's copy.** Confirm the collection is
   `active` on the new tenant and the owner can query it before deleting
   anything on the old tenant:
   ```bash
   curl -s -H "X-API-Key: <NEW_TENANT_ADMIN_KEY>" \
     "<new-tenant-api>/v1/collections" \
     | jq '.collections[] | select(.id=="<collection-id>") | .state'
   # only after this reads "active" and a query round-trips:
   curl -s -X DELETE -H "X-API-Key: <OLD_TENANT_ADMIN_KEY>" \
     "<old-tenant-api>/v1/collections/<collection-id>?purge=true"
   ```
   `purge=true` deletes exactly four things: the registry binding, the Qdrant
   collection, the Elasticsearch index, and the provenance manifest — it does
   **not** touch the Workspace archive (that's not one of its targets). The
   archive is the source of truth throughout this sequence — nothing
   irreplaceable exists only on the old tenant once step 1 confirmed a current
   archive, so deleting the old tenant's physical copy in step 4 is safe.

### Fallback path: Qdrant + Elasticsearch snapshot/restore

For a collection with **no current archive** (pre-#358 data, or a collection
whose last archive step failed and hasn't retried), fall back to physical
snapshot/restore.

**The registry id is not the physical store name.** Physical Qdrant
collection / Elasticsearch index names are a content-addressed slug+hash
(`collection` / `text_index` columns on the `collections` row), not the
registry `id`. Look them up first:

```bash
sqlite3 <old-tenant-registry.db> \
  "SELECT collection, text_index FROM collections WHERE id='<collection-id>'"
# -> use these as <physical-store> below
```

```bash
# old tenant: snapshot the collection (Qdrant native API, not the RAGStack API)
curl -s -X POST "<old-tenant-qdrant>/collections/<physical-store>/snapshots"

# copy the resulting snapshot file into the new tenant's own Qdrant snapshots
# dir (<RAG_DATA>/tenants/<new-tenant>/qdrant/snapshots/); the recover call's
# "location" is a CONTAINER-side path — up.sh binds that host dir to
# /qdrant/snapshots inside the instance, so the file:// URI must use the
# container path, not the host path you just copied it to:
curl -s -X PUT "<new-tenant-qdrant>/collections/<physical-store>/snapshots/recover" \
  -H 'Content-Type: application/json' \
  -d '{"location": "file:///qdrant/snapshots/<snapshot-file>.snapshot"}'
```

**Elasticsearch has no working fallback on a script-provisioned tenant
today.** A filesystem snapshot repository needs `path.repo` set and a bind
mount for the repo directory; the generated `bin/up.sh` passes neither
(no `-Epath.repo`, no repo-dir bind), and hand-editing a generated file
violates this runbook's own "don't hand-edit derived artifacts" rule. So
there is currently **no ES leg of this fallback** on a tenant provisioned by
`new-tenant.sh` as-is — it requires a script change (an `--es-repo` flag,
tracked on #387) before the commands below are usable:

```bash
# NOT USABLE until #387 lands — requires a registered repo on both ES instances
curl -s -X PUT "<old-tenant-es>/_snapshot/<repo>/<snapshot-name>?wait_for_completion=true" \
  -H 'Content-Type: application/json' -d "{\"indices\": \"<physical-store>\"}"
curl -s -X POST "<new-tenant-es>/_snapshot/<repo>/<snapshot-name>/_restore" \
  -H 'Content-Type: application/json' -d "{\"indices\": \"<physical-store>\"}"
```

Until #387 lands, a collection with no current archive and a need to move
has no complete automated path for its text leg — rebuilding the ES index
from the moved Qdrant payloads (or re-ingesting) is the only option.

Then copy the registry row + ACL rows exactly as in step 2 of the primary
path above (with `state='active'` this time — the physical stores already
exist on the new tenant once the snapshot restore completes), following the
same verbatim-copy rules (every column including `archive_version`, the
`owner` share row, the pre-start-or-restart caveat).

---

## 5. What does NOT carry across a tenant move

- **Shares to users who don't exist on the destination tenant.** A `shares`
  row naming a grantee that has no `users` row on the new tenant is dead until
  that user is also provisioned there (per ADR-0005 decision 3, there is no
  global user directory — a person appearing in two tenants is two independent
  rows describing the same human, with nothing to sync).
- **`public`.** `public` means public *within a tenant* (ADR-0005 decision 3).
  A collection marked `public` on the old tenant is not public on the new one
  just because the row moved — the grant is tenant-scoped and needs its own
  `shares` row (grantee `public`) recreated on the new tenant if that's still
  the intent.
- **Cross-tenant fusion.** There is no query that spans two tenants' stores.
  A user with collections split across two tenants (mid-move, or permanently,
  e.g. a personal collection on the new tenant plus a shared corpus that stays
  on the old one) gets two independent result sets, never one fused ranking —
  federation is deferred (§3, ADR-0005 § Deferred: federated tenancy).
- **The chatbot's workaround for split tenancy:** per
  [libraries-spec.md](../libraries-spec.md), **the chatbot MUST NOT implement
  RRF** — rank fusion is a retrieval concern, not a chatbot concern. So the
  workaround for a user whose data spans two tenants is **one call per
  tenant, results presented separately** (e.g. as separate source groups in
  the response), never a client-side merge that fakes a fused ranking the
  product doesn't produce server-side.

---

## 6. Dry run

Before relying on this runbook for a real user move, execute it once against a
throwaway tenant end to end, timed, and record the exact commands run (with
placeholders resolved to the throwaway tenant's real values — that pasted
transcript is allowed to name the throwaway tenant, since it's disposable) in
the appendix below.

Checklist:

- [ ] Provision a throwaway tenant (`./new-tenant.sh <throwaway> --dry-run`,
      then for real). Time it.
- [ ] Set the §2 must-set settings explicitly; run all three post-provision
      checks; confirm all three pass.
- [ ] Create a small test collection on a **source** tenant (or reuse an
      existing disposable one), ingest a handful of documents through GoWe so
      it has a real archive.
- [ ] Confirm `archive_pending=false` and `versions` non-empty on the source.
- [ ] Copy the `collections` row + any `shares`/`users`/`group_members` rows
      to the throwaway tenant by hand; time it.
- [ ] Trigger restore on the throwaway tenant; time it against the
      cold-build figures in [active-collection-bound.md](active-collection-bound.md).
- [ ] Query the collection on the throwaway tenant as the owner; confirm
      results match what the source tenant returned before the move.
- [ ] Confirm the moved user's requests resolve to the same payload tenant
      string post-move (§4's point-id-invariant verification step).
- [ ] Delete the source tenant's copy; confirm the throwaway tenant is now
      the sole physical owner (`store_inventory.py` should show no
      unclaimed/duplicate-claimed store for this collection).
- [ ] Tear down the throwaway tenant (`bin/down.sh`, then remove its manifest
      row and data directory by hand — the script has no `remove-tenant`
      counterpart).

### Appendix: dry-run transcript

> **TODO — not yet executed.** This appendix is a placeholder. Paste the
> verbatim commands and their real timings here once the checklist above has
> actually been run against a throwaway tenant. Do not fill this in from
> memory or by inference from the sections above — it must be a transcript of
> a real run, per #201 phase 5's acceptance criterion ("a dry run against a
> throwaway tenant, timed, with the commands pasted verbatim into the
> runbook"). This dry run is tracked as a follow-up to this PR, not part of
> it.
