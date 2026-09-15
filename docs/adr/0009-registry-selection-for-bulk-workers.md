# 0009. Which collection registry a bulk worker resolves against

Status: Proposed (2026-09-15, issue #563; GoWe#260, #261, #262)

## Context

A bulk ingest resolves its target through the **collection registry** (ADR-0002,
#263): the physical Qdrant collection, the ES index and the build spec all come
from the registry entry, never from the command line. On the GoWe execution
plane (ADR-0006) that resolution happens inside a worker container.

Every other piece of a tenant's physical state already travels on the submission
as a visible workflow input, seeded per job by that tenant's API from its own
settings: `qdrant_url`, `es_url`, `es_index`, `collection`, `embedding_url`,
`tenant` (#407). The registry did not. The worker read it from its own process
environment — `COLLECTION_STORE_BACKEND` / `_PATH` / `_DSN` — which
`gowe-worker` is given once per **worker group**.

So one worker group could serve exactly one tenant's registry. On 2026-09-15 the
shared `ragstack` group was pinned to the **dev** tenant's sqlite registry, and
every ingest on the `hackathon` tenant failed with

```
physical store 'ragstack_lib_scratch_uiguide_…' is claimed by no registry entry
(sqlite:/rag/data/tenants/dev/state/ragstack_collections.db)
```

— after the extract step had already succeeded, so each job died half-done. The
workaround was a per-tenant worker group, which needs a new group, env file,
secret file and image directory per ingesting tenant (ADR-0005's tenant
inventory grows an execution-plane column). That does not scale.

The obvious fix — put the registry's coordinates on the submission next to
`qdrant_url` — is **not available**, and not for a stylistic reason:

* `GET /api/v1/submissions/{id}` returns both `inputs` and `submitted_inputs`.
  `submitted_inputs` is an **immutable snapshot**: a DSN placed there can never
  be withdrawn. Task records return `job`/`inputs` too, the UI renders them, and
  the engine's SQLite stores them in plaintext (only provider tokens are
  encrypted).
* GoWe has no named-secret-reference mechanism today. That is GoWe#260, with no
  timeline.
* What GoWe does have: worker-level `--secret-file` entries, injected into every
  container the worker runs as `apptainer --env NAME=value`, with the secret
  **values** redacted from captured task stdout/stderr. By contrast
  `--env-file` values are logged in clear at INFO — so the non-secret half may
  live there; the credential may only ever come from `--secret-file`.

## Decision

1. A bulk submission carries **`registry`: a NAME** (`hackathon`, `dev`) as an
   optional, visible workflow input, seeded per job by the tenant API from
   `COLLECTION_REGISTRY_NAME` — exactly as `qdrant_url` and `es_url` are seeded.
   A name, never coordinates and never a credential.
2. The tool resolves that name against **its own environment**, using a
   per-registry suffix: `COLLECTION_STORE_BACKEND_<NAME>` plus
   `COLLECTION_STORE_DSN_<NAME>` or `COLLECTION_STORE_PATH_<NAME>` (and
   `COLLECTIONS_FILE_<NAME>` for the json backend). Suffix = the name
   uppercased. The name is validated against `^[a-z0-9][a-z0-9_]{0,63}$` before
   use — it arrives on a submission and builds an environment variable name.

   **Lowercase and underscore only, and that restriction is the point.** Because
   the suffix is the name uppercased, an alphabet that also admitted uppercase,
   `-` or `.` would map `dev`/`Dev`/`DEV` and `a-b`/`a.b`/`a_b` onto the same
   variables — two tenants whose names differed only by case or punctuation
   would silently share one registry, which is this decision's own failure mode
   reintroduced one level up, and invisible: unlike a missing variable, a
   collision resolves *successfully* against the wrong database. Restricting the
   input makes the map injective at no cost: every registry has exactly one
   spelling, and a second spelling is refused at validation rather than folded
   at lookup.
3. A `registry` that names an **unconfigured** registry is fatal. It does **not**
   fall back to the unsuffixed `COLLECTION_STORE_*` variables: a silent fallback
   to another tenant's registry is precisely the defect above. The refusal names
   the registry and the variables it looked for, and never a DSN.
4. `registry` absent or empty means exactly the previous behaviour — the
   unsuffixed variables — so a deployment that has not adopted the convention is
   byte-for-byte unchanged.
5. The DSN reaches the container **only** through the worker's `--secret-file`.
6. `--registry` takes **no ambient environment default**, unlike every other
   flag on the bulk writers. An ambient default would live in a worker group's
   env-file, and this decision's whole direction is one shared group serving
   many tenants — so "absent on the submission" would stop meaning the previous
   behaviour and start silently meaning one particular tenant's registry. It
   would also override `jats-ingest.cwl`'s exemption, whose tool binds no
   `--registry` precisely because it pins `COLLECTION_STORE_*` itself. WHICH
   registry is a per-**job** fact and arrives on the command line or not at all.

## Consequences

* One worker group can serve every tenant.
* **A worker group is NOT a confidentiality boundary.** This ADR originally said
  the per-tenant group "is still the isolation boundary where it is needed."
  That was wrong, and the correction matters more than the original claim did.
  In GoWe as deployed, `CanJoinGroup` is enforced at exactly one call site —
  `internal/server/handler_workers.go:64`, the **worker registration** path. It
  guards the worker→group edge. Nothing authorizes the **submitter**→group edge:
  the scheduler reads `sub.Labels["worker_group"]` and dispatches, so any
  authenticated GoWe user can target any group, with any image, and every
  container on that worker inherits the worker's full `--secret-file` /
  `--env-file` environment. A per-tenant group defends against a rogue *worker*,
  not against another *user*.
* **The trade-off, stated plainly:** with one shared group, every container that
  group runs carries **every** tenant's registry DSN in its environment. Given
  the point above, splitting into per-tenant groups does not fix that — it
  narrows which worker holds which secret at rest and nothing more. The actual
  containment today is that GoWe is fronted by the tenant APIs, which submit a
  fixed registered workflow: a tenant user never chooses a group, an image, or a
  tool. Treat the group as **placement and convenience, not security**, until
  GoWe#261 (submitter-side group ACL) and GoWe#262 (per-group image and
  admin-registered-workflow restriction) land.
* **Because a group was never the boundary, any credential that has sat in a
  worker env file should be rotated once it no longer needs to be there** — at
  the time of writing, `NEO4J_PASSWORD` on the `ragstack` group and the
  hackathon `COLLECTION_STORE_DSN`. Rotation is cheap; the assumption that a
  group contained them was not true for the period they were there.
* Defence in depth, not a substitute for care: the worker redacts secret values
  from captured task output, and `ragstack.ops.ingest_target` scrubs DSNs out of
  third-party error text before printing a refusal. Neither licenses putting a
  credential anywhere it could be read.
* The registry secret lives in the worker's secret file, **outside every tenant
  tree**. A `ragstack-ctl tenant backup --fence` bundle and `restore --as <new>`
  therefore carry no DSN for the new name: a restored tenant needs a manual
  worker-secret-file entry before it can ingest. Known follow-up.
* Forward-compatible without being forward-dependent, and this survived GoWe#260
  being re-scoped. #260 was originally worker-side `secret://<name>` references
  resolved against the worker's own secret file; it is now **submission-time
  secrets** — `POST /submissions` takes an optional `secrets: {NAME: value}` map,
  encrypted at rest the way the BV-BRC token already is, never echoed into
  `inputs`, `submitted_inputs`, the task job, the UI or the logs, delivered only
  to tasks whose tool opts in (`gowe:Execution.secret_env: [COLLECTION_STORE_DSN]`),
  and scrubbed from the task row at terminal state.

  That fits here better, because the tenant API already holds the credential at
  submit time. On #260 landing: the tenant API adds
  `secrets: {COLLECTION_STORE_DSN: <that tenant's dsn>}` to the submission and the
  per-group secret files go away; the tool reads `COLLECTION_STORE_DSN` from its
  environment exactly as it does now, with no suffix. **Nothing in this ADR
  changes** — the visible `registry` name, the CWL binding and the loud failure
  all stay, and `--registry` keeps earning its place as the audit trail of which
  registry a job actually used. Confidentiality then binds to the *submission*
  rather than to the worker: no worker holds a tenant credential at rest, and the
  shared group needs no trust condition at all — the same-org question reduces to
  compute placement, which is GoWe#261/#262.

## Related

* ADR-0002 — collection identity; the registry this selects between.
* ADR-0005 — tenant anatomy; a tenant's registry is one of its stateful stores.
* ADR-0006 — execution topology; the ingest plane this runs on.
* #407 — the same shape one setting earlier: store URLs seeded per job.
