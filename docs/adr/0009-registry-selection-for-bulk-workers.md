# 0009. Which collection registry a bulk worker resolves against

Status: Proposed (2026-09-15, issue #563; GoWe#260)

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
   uppercased, with every character outside `[A-Z0-9_]` replaced by `_`. The
   name is validated against a strict pattern before use — it arrives on a
   submission and builds an environment variable name.
3. A `registry` that names an **unconfigured** registry is fatal. It does **not**
   fall back to the unsuffixed `COLLECTION_STORE_*` variables: a silent fallback
   to another tenant's registry is precisely the defect above. The refusal names
   the registry and the variables it looked for, and never a DSN.
4. `registry` absent or empty means exactly the previous behaviour — the
   unsuffixed variables — so a deployment that has not adopted the convention is
   byte-for-byte unchanged.
5. The DSN reaches the container **only** through the worker's `--secret-file`.

## Consequences

* One worker group can serve every tenant. The per-tenant group remains
  available and is still the isolation boundary where it is needed.
* **The trade-off, stated plainly:** with one shared group, every container that
  group runs carries **every** tenant's registry DSN in its environment. That is
  safe only where the tenants are same-org and the tool image is trusted. If
  either ceases to hold — a tenant outside the trust boundary, or a workflow
  that can run arbitrary user code in that image — per-tenant worker groups are
  the answer again, and this ADR does not remove them.
* Defence in depth, not a substitute for care: the worker redacts secret values
  from captured task output, and `ragstack.ops.ingest_target` scrubs DSNs out of
  third-party error text before printing a refusal. Neither licenses putting a
  credential anywhere it could be read.
* The registry secret lives in the worker's secret file, **outside every tenant
  tree**. A `ragstack-ctl tenant backup --fence` bundle and `restore --as <new>`
  therefore carry no DSN for the new name: a restored tenant needs a manual
  worker-secret-file entry before it can ingest. Known follow-up.
* Forward-compatible without being forward-dependent. `COLLECTION_STORE_DSN_<NAME>`
  is already a valid GoWe#260 reference name (their names allow `[A-Za-z0-9_.-]+`),
  so nothing is renamed later. And because `registry` is a bare name rather than
  a secret reference, the env-suffix resolution keeps working *alongside*
  GoWe#260 — adopting `secret://` becomes optional, taken only if we want their
  per-tool allowlist enforcement (`gowe:Execution.secrets: ["registry-*-dsn"]`,
  where an unknown or non-allowlisted reference fails the task before execution).

## Related

* ADR-0002 — collection identity; the registry this selects between.
* ADR-0005 — tenant anatomy; a tenant's registry is one of its stateful stores.
* ADR-0006 — execution topology; the ingest plane this runs on.
* #407 — the same shape one setting earlier: store URLs seeded per job.
