# ADR 0005 — Anatomy of a tenant: dedicated stateful stores, scripted provisioning

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** @wilke
- **Amends:** [ADR-0004](0004-users-groups-shares.md) — the users/groups/shares store is
  **per tenant**, not a shared instance; "the Postgres instance already deployed" becomes
  "each tenant's own database".
- **Related:** [ADR-0003](0003-access-control.md) (tenant = instance),
  [#246](https://github.com/wilke/ragstack/issues/246) (release migration checklist),
  [cookbook-new-org-ingest.md](../cookbook-new-org-ingest.md)

## Context

ADR-0003 declared *where* the tenant boundary is — the instance — but never defined what
a tenant *is*. The deployment shows the cost of leaving that implicit: the one org
provisioned for hard isolation has a dedicated Qdrant but its **text index sits in the
shared Elasticsearch**, separated from every other tenant's data by nothing but an index
name — no ES security layer in between. Half of every hybrid query for the "isolated"
tenant runs through shared infrastructure. Nothing in code, config schema, or registry
knows the word tenant; a tenant exists because an operator wrote an env file.

ADR-0004 forced the question: it creates new stateful ACL data (users, groups, shares)
and did not say which side of the tenant boundary that data lives on.

## Decision

**1. Definition.** A tenant is **one API endpoint bound to a dedicated set of stateful
stores, sharing only stateless compute and the host**. Data at rest defines tenancy;
compute passing through does not.

| Inside the tenant (dedicated) | Shared plumbing |
|---|---|
| API server process + its env (identity config, role maps) | embedding fleet |
| Qdrant instance | reranker sidecar |
| Elasticsearch instance | LLM endpoint |
| collection registry + job store + ACL database | frontend (backend switcher) |
| ingest staging directories | the host, GPUs |

**2. Every tenant gets a dedicated Elasticsearch**, exactly as it already gets a
dedicated Qdrant. Index-name separation inside a shared ES is not isolation — it is a
naming convention enforced by nothing.

**3. The ACL/user store is per tenant** (this amends ADR-0004). The **schema and service
code are shared; the database instance is not** — the same rule as reusing the group
service elsewhere: another system runs its *own* instance against its *own* tables. The
`issuer:sub` subject string is globally stable by construction, so one person appearing
in several tenants is rows in several databases describing the same human, with nothing
to sync. Consequences stated plainly: there is **no global user directory**, **no
cross-tenant sharing**, and **`public` means public within this tenant**.

**4. Provisioning is a script, not an API.** Creating a tenant creates infrastructure —
store instances, data directories, an env file, service entries, port assignments — an
operator act with resource consequences, parallel to build-spec overrides being
admin-only. A `new-tenant` script stamps all of it out following the persistence
conventions (every writable path enumerated and bind-mounted; no opaque overlays). A
runtime tenant-management API is deferred until tenant creation is frequent enough that
scripted ops is the bottleneck; at a handful of orgs it never is. The new-org onboarding
cookbook and the ops architecture reference must be updated to match the script.

**5. `max_collections` (default 100) applies within each tenant, to every role including
admin.** The cap is physical protection for the store instances (ADR-0003's budget), not
an authorization tier, so there is no bypass role. `0` disables it. Because the budget is
per store instance and stores are per tenant, the cap multiplies with tenants exactly as
ADR-0003 intended.

## Migration

Only two real tenants exist; everything else in the shared stores is test/demo. Both run
the pre-ADR API and stay **untouched now** — they migrate together with their API upgrade.
When that happens, **invert the obvious move**: after the smaller tenant's text index
(~1.5M docs; snapshot/restore, or re-ingest from Qdrant payloads) and the test/demo
indices leave the current shared ES, what remains *is* the large tenant's data — the
existing instance is **relabeled as that tenant's dedicated ES** and its tens of millions
of documents never move. Tracked in #246.

**6. The tenant model is the design target; the shared-store topology is a dated
migration state, not a supported configuration.** *(Amendment, 2026-08-07.)*

Design and plan against a tenant that **exclusively owns** its Qdrant, its Elasticsearch
and its ACL/registry database. Where the current fleet contradicts that, the fleet is the
thing that is wrong. The `dev` tenant (provisioned by `apptainer/new-tenant.sh`) is the
first deployment that meets decision 1 and is the reference shape.

This is not cosmetic — several designs were being drawn *smaller* than they should be
because a shared instance made the honest version unsafe:

- **Unclaimed stores could not be reclaimed.** On a shared Qdrant, "absent from this
  registry" is indistinguishable from "owned by a tenant that is not currently running" —
  268 GB of production corpora are in exactly that state today, so an automated sweep would
  destroy them. Under exclusive ownership, unclaimed *means* orphan and reclaim becomes
  constructible (#293).
- **The collection cap could not count the thing it protects.** `max_collections` claims to
  bound physical stores but counts registry rows, because a physical count on a shared
  instance would let one tenant's usage refuse another tenant's users — contradicting this
  ADR's own Consequences. Under exclusive ownership a physical count *is* a per-tenant
  count, and the cap can finally count the noun it names (#286, #290).
- **Two APIs over one store double the effective cap.** Each enforces its own limit against
  its own registry. That is a property of the topology, not of the code.

**Corollary — every physical store must be claimed by exactly one registry entry of its
owning tenant.** ADR-0002 decision 5 states this for stores the API creates. The remaining
hole is the bulk path: `scripts/ingest_jsonl.py` and `load_embeddings.py` call
`ensure_collection()` directly, so they create stores the registry never sees — which is
how 24 unclaimed stores accumulated, and why the ADR-0002 build-spec guard is disarmed for
them (#263). The bulk CLI must therefore **take a `--collection-id` that already exists in
the registry, and refuse an unregistered one** — optionally creating it through the API
first, so the spec, the cap and the owner row all come from the normal path. The bulk
*data* path stays direct to Qdrant; only the *registration* moves. Without this, "unclaimed
means orphan" is nearly-true rather than true, and nearly-true is not a property a delete
can be built on.

**7. A tenant may reach exclusive ownership by ADOPTION as well as by migration.**
*(Amendment, 2026-08-08.)* The asm tenant's stores are declared to be the existing
Qdrant `:6333` and Elasticsearch `:9200` — not the empty per-tenant instances the
provisioning script stamped out. The boundary test is decision 1's own: **data at
rest**. Those instances hold asm's three corpora (40.4M points, 58 GB of text
index); standing up fresh stores would have meant moving all of it to satisfy a
naming convention. Adoption inverts the cost: the data stays, and what remains is
a **decommission list** of co-tenants, each with a defined exit:

| co-tenant on the adopted stores | exits when |
|---|---|
| old lucid text leg (`lucid_sfr_tok256` on `:9200`) | the legacy `:8010` API retires (pending lucid web-team sign-off) |
| demo's `open-access` library store | demo repoints to its own stores or retires |
| eval residue (`chunkcmp_*`, `oa_smoke_*`) | deleted per the store inventory (#299) |
| KEEP rollback pair (`ragstack_sfr`, `…928f8ebe`) | operator decision after the old `:8000` door closes |

The store inventory is the progress meter: adoption is COMPLETE when every entry
on these instances shows exactly one owner, asm. Until then the old `:8000` API
and the tenant API are two front doors on one tenant's data — the same-org
transition state the glossary permits. The tenant's empty provisioned store
dirs stay dormant; re-point or delete them when the manifest row is next touched.

## Deferred: federated tenancy

At some point a global view may be wanted — one query across every tenant a person
belongs to. The shape is known and recorded so it isn't re-derived: a **tenant registry**,
a **cross-tenant scope carried on the identity**, and a **gateway API** that routes
per-tenant requests and fuses results (the same N-leg RRF machinery as multi-collection
search). It is deferred deliberately, not just for effort: the gateway **re-centralizes
exactly what physical tenancy decentralizes** — it must hold trust for every tenant, so
its compromise crosses all boundaries at once. If and when it is built, it gets its own
ADR and its own threat model; nothing in this ADR blocks it, because subjects are already
globally stable and every tenant speaks the same API.

## Consequences

**Accepted:**

- **A tenant costs a process set** — Qdrant, an ES JVM (heap is the dominant line item),
  a Postgres database, an API process, plus a backup target and a port block. This is why
  tenants are for *organizations needing hard isolation*, a handful, never per-user.
- **More instances to operate** — upgrades, snapshots, and monitoring multiply by tenant
  count. The provisioning script is what keeps this tractable; hand-built tenants are how
  the current ES gap happened.
- **No cross-tenant anything** by construction. A person with roles in two tenants logs
  into each; their collections cannot be fused until the federation layer exists.
- **The collection budget is genuinely per tenant** — one org's growth cannot exhaust
  another's headroom, completing the ADR-0003 argument.

**Gained:** the word tenant now means one thing; the isolation actually matches what
ADR-0003 claimed; ADR-0004's store placement is decided instead of accidental; and
provisioning is reproducible instead of archaeological.

## Alternatives considered

- **Shared ES with index-name separation** (the status quo). Rejected: no security
  boundary between indices, and it silently made the flagship isolation guarantee false
  for half of every hybrid query.
- **A shared ACL/user store.** One user list, simpler ops. Rejected: it drives a hole
  through the instance boundary — every tenant's membership and sharing graph in one
  database reachable from every tenant's API process.
- **A tenant-management API now.** Rejected as premature: creation is an infrastructure
  act at a frequency of a few per year; a script is auditable, versioned, and cannot be
  invoked by a compromised API process.
