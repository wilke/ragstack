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
Qdrant/ES slot; `dormant`/`lost` rows hold no slot and don't count). With the
runbook's recommended `MAX_COLLECTIONS=150` (see [active-collection-bound.md](active-collection-bound.md)),
that's 120 physically-present collections.

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
default collection — #379's eviction accounting treats an entry with no
registry row as physically present too, since it holds a store slot the same
as any registered one).

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
./new-tenant.sh <new-tenant> --postgres postgresql://ragstack:<pw>@<pg-host>:<pg-port>/postgres

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
| `ALLOW_USER_COLLECTION_CREATE` | Whether a non-admin can create their own collection at all (default `true` in the product, but the tenant's env should state its intent explicitly rather than inherit a default silently). |
| `MAX_COLLECTIONS_PER_OWNER` | Product default 5 — confirm it matches policy for the new tenant rather than assuming the default is still right at this tenant's expected user count. |
| `MAX_CHUNKS_PER_COLLECTION` | Product default 50,000 — the per-collection size cap ADR-0005/#289's interim policy relies on (bounded by size, not by refusing creation). |
| `INGEST_BACKEND=gowe` | The script's template omits an `INGEST_BACKEND` line (defaults to `local`). The personal-collections workflow routes ingest through GoWe (interim-policy comment on #289); set it explicitly. |
| `WORKSPACE_URL` | Required once `INGEST_BACKEND=gowe` and the archive/restore path (#353/#358) is in use — the user's BV-BRC Workspace is where the archive lives. |
| GoWe engine URL (`GOWE_URL`) | Required alongside `INGEST_BACKEND=gowe` — the tenant's ingest and restore submissions need a reachable GoWe engine. |

Edit `tenant.env` directly (it's the operator-editable file; re-running the
script without `--force` keeps your edits) or export overrides at API-start
time. Whichever you do, capture the diff somewhere durable — the next re-run of
`new-tenant.sh` will keep an edited `tenant.env`, but only if it's the same
file the operator edited.

### Mandatory post-provision checks

```bash
set -a; . <RAG_DATA>/tenants/<new-tenant>/config/tenant.env; set +a
cd python && uvicorn ragstack.api.main:app --host 0.0.0.0 --port $PORT &

# 1. liveness (no key)
curl -s "http://localhost:$PORT/health"                                     # {"status":"ok"}

# 2. admin config — confirm the settings above actually took (spot-check key count,
#    not values, so nothing secret-shaped ends up in a paste)
curl -s -H "X-API-Key: <NEW_TENANT_ADMIN_KEY>" "http://localhost:$PORT/v1/config" \
  | jq 'keys | length'

# 3. keyed conformance run against the new tenant
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
  frontend's preset list but on the chatbot side.
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
Qdrant process runs on.** Moving a collection's physical data from one tenant's
store instance to another's does not change any chunk id and does not
re-embed anything, **provided** the destination tenant's key/identity mapping
for the moved user resolves to the *same* tenant string the data was
originally ingested under. If the destination tenant's `API_KEY_TENANTS` (or
identity→tenant mapping) assigns that user a different tenant string, their
own points stop matching the server-side tenant filter applied on every
read/write. Verify this explicitly after the move — it is the one thing a
naive "just copy the collection" move can get silently wrong.

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
   touching anything:
   ```bash
   curl -s -H "X-API-Key: <OLD_TENANT_ADMIN_KEY>" \
     "<old-tenant-api>/v1/collections/<collection-id>" \
     | jq '{state, archive_pending, versions}'
   ```
   `archive_pending` must be `false` and `versions` non-empty. If not, the
   collection has no restorable archive yet — a delta may still be in flight;
   don't proceed until this settles or fall back to snapshot/restore (below).

2. **Copy the registry row and its ACL rows into the new tenant's registry.**
   These live in the tenant's ACL/registry database (sqlite or Postgres,
   per `tenant.env`) and must be moved by hand — table by table:

   | Table | What to copy |
   |---|---|
   | `collections` | The row for `<collection-id>` — carries `owner`, `spec_hash`, `versions`, and the rest of the `CollectionRecord`. Insert with `state='dormant'` on the new tenant (its physical stores don't exist there yet — the first read or an explicit restore call creates them from the archive). |
   | `shares` | Every row with `collection_id = <collection-id>` — grantee (`user`/`group`), permission (`read`/`write`/`owner`), grant option. |
   | `users` | The moved user's row, if it doesn't already exist on the new tenant (subjects are globally stable, but the row itself is per-tenant per ADR-0005 decision 3 — there is no global user directory). |
   | `groups` / `group_members` | Any group the user's shares reference that doesn't already exist on the new tenant, and the user's membership rows in it. |

   Copy `collections` and `shares` together, in the same maintenance window —
   a collection row with no matching share leaves the owner locked out; a
   share with no collection row is dead weight.

3. **Restore on the new tenant.** Either wait for the owner's first
   authenticated read (which triggers a restore submission automatically per
   #358 and returns 503 + `Retry-After` until it completes), or trigger it
   explicitly:
   ```bash
   curl -s -X POST -H "Authorization: Bearer <owner-token>" \
     "<new-tenant-api>/v1/collections/<collection-id>/restore"
   ```
   The restore runs as **the user** (GoWe submission carrying their token, per
   #353) — this is exactly why §2's must-set list includes `INGEST_BACKEND=gowe`,
   `GOWE_URL`, and `WORKSPACE_URL` on the new tenant: without them, the restore
   has nowhere to submit to and nothing to read the archive from. Expect
   roughly the cold-build order of magnitude measured in
   [active-collection-bound.md](active-collection-bound.md) (~100 s store-side
   for a 35k-chunk collection) as a floor — a real restore also has to read the
   archive and replay it, so budget more.

4. **Verify, then retire the old tenant's copy.** Confirm the collection is
   `active` on the new tenant and the owner can query it before deleting
   anything on the old tenant:
   ```bash
   curl -s -H "X-API-Key: <NEW_TENANT_ADMIN_KEY>" \
     "<new-tenant-api>/v1/collections/<collection-id>" | jq '.state'
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
snapshot/restore:

```bash
# old tenant: snapshot the collection
curl -s -X POST "<old-tenant-qdrant>/collections/<collection-id>/snapshots"

# copy the resulting snapshot file into the new tenant's own Qdrant snapshots
# dir (<RAG_DATA>/tenants/<new-tenant>/qdrant/snapshots/), then recover from
# that local path on the new tenant:
curl -s -X PUT "<new-tenant-qdrant>/collections/<collection-id>/snapshots/recover" \
  -H 'Content-Type: application/json' \
  -d '{"location": "file:///<path-to-copied-snapshot>"}'

# Elasticsearch: requires a shared snapshot repository reachable from both
# tenants' ES instances (filesystem or object-store repo registered on each)
curl -s -X PUT "<old-tenant-es>/_snapshot/<repo>/<snapshot-name>?wait_for_completion=true" \
  -H 'Content-Type: application/json' -d "{\"indices\": \"<collection-id>\"}"
curl -s -X POST "<new-tenant-es>/_snapshot/<repo>/<snapshot-name>/_restore" \
  -H 'Content-Type: application/json' -d "{\"indices\": \"<collection-id>\"}"
```

Then copy the registry row + ACL rows exactly as in step 2 of the primary
path (with `state='active'` this time — the physical stores already exist on
the new tenant once the snapshot restore completes).

This path needs a snapshot repository provisioned and reachable from **both**
tenants' Elasticsearch instances ahead of time — the reason to prefer the
archive path whenever it's available.

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
