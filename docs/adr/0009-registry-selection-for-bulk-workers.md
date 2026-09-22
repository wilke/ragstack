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
  narrows which worker holds which secret at rest and nothing more.
* **And nothing else contains it either.** An earlier draft of this ADR said the
  containment was that GoWe sits behind the tenant APIs. That is the same mistake
  as the one this ADR corrects — naming a control as a boundary when it is not
  one — so it is recorded rather than quietly replaced. The tenant APIs submit a
  fixed, server-registered workflow with a server-configured group, and no tenant
  input reaches the submission's labels, image or tool; all of that is true and
  none of it is containment, because **the API submits *as the caller***
  (`python/ragstack/api/security.py` `gowe_caller` returns the principal's own
  token, and `documents.py` 401s a principal without one). Everyone who can use
  the ingest path therefore already holds a credential GoWe accepts directly, and
  GoWe's submission API is publicly proxied — only `/api/v1/workers` is guarded
  at the gateway. Group names are enumerable via `GET /api/v1/fleet` — which does
  require a credential (it answers `UNAUTHORIZED` unauthenticated, measured
  2026-09-16; the "only `/api/v1/workers` is guarded" clause above is about the
  nginx gateway, not GoWe's own auth). That is not a mitigation here, because the
  sentence above establishes that everyone who can reach the ingest path already
  holds a credential GoWe accepts. And GoWe
  auto-provisions a user on first contact, so there is no membership list to be
  outside of. Treat the group as **placement and convenience, not security**.
  GoWe#261 (submitter-side group ACL) and GoWe#262 (per-group image and
  admin-registered-workflow restriction) are the controls; until they land there
  is no boundary, only the absence of an attempt. **Confirmed against the GoWe
  tracker on 2026-09-16: #261 and #262 are OPEN, not implemented and not
  scheduled**, and are *not* in v0.20.0.
* **Consequently, credentials that have sat in a worker env file should be
  rotated as part of that hardening** — `NEO4J_PASSWORD` on the `ragstack` group,
  the hackathon `COLLECTION_STORE_DSN`, and, by exactly the same argument,
  `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` on the `default` group, which is where an
  unlabelled submission lands. This deployment is a development and
  demonstration environment, so the rotation was deferred rather than treated as
  an incident; the principle is that the assumption a group contained them was
  never true for the period they were there.

  **Corrected 2026-09-16.** An earlier revision said the rotation was "scheduled
  with #261/#262". It is not scheduled, because *they* are not: both are open and
  unimplemented. The two are also independent, and conflating them deferred the
  rotation behind work that may never be prioritised —

  > rotation does not need the ACL; the ACL is what makes the post-rotation state
  > *stay* clean.

  So these three credentials can and should be rotated on our own schedule. What
  #261/#262 would add is that a rotated secret does not simply re-accumulate the
  same exposure, because the group would finally be an authorization boundary
  rather than a placement hint.
* Defence in depth, not a substitute for care: the worker redacts secret values
  from captured task output, and `ragstack.ops.ingest_target` scrubs DSNs out of
  third-party error text before printing a refusal. Neither licenses putting a
  credential anywhere it could be read.
* The registry secret lives in the worker's secret file, **outside every tenant
  tree**. A `ragstack-ctl tenant backup --fence` bundle and `restore --as <new>`
  therefore carry no DSN for the new name: a restored tenant needs a manual
  worker-secret-file entry before it can ingest. Known follow-up.
* Forward-compatible without being forward-dependent, and this survived GoWe#260
  being re-scoped. **GoWe#260 is implemented by PR #265 and released in
  v0.20.0 — deployed to `:8091` on 2026-09-16 as `0.20.0+06b6696`, verified from
  `GET /api/v1/health` rather than taken from the release note; **now
  `0.20.1+f757592`** (2026-09-17, same verification), a hotfix that does not touch
  the secrets surface — see the note below**
  (confirmed against the tracker 2026-09-16: #265 carries `Closes #260`, and #260
  closed on 2026-09-15 by that merge). GoWe#263 holds the limitations deferred out
  of #260 — `cwltool:Secrets` in sub-workflows, output redaction, IWDR file modes
  — and is open; none of them touch the `secret_env` path this ADR relies on.
  #260 was originally worker-side `secret://<name>` references
  resolved against the worker's own secret file; it is now **submission-time
  secrets** — `POST /submissions` takes an optional `secrets: {NAME: value}` map,
  encrypted at rest the way the BV-BRC token already is, never echoed into
  `inputs`, `submitted_inputs`, the task job, the UI or the logs, delivered only
  to tasks whose tool opts in — either naming what it needs
  (`gowe:Execution.secret_env: [COLLECTION_STORE_DSN]`) or taking all of the
  submission's secrets with `inject_secrets: true` —
  and scrubbed from the task row at terminal state.

  That fits here better, because the tenant API already holds the credential at
  submit time. **The clause below fires when v0.20.0 is live on :8091, not when
  the PR merged** — it is deployed, not merged, that changes what a worker holds.
  **That happened on 2026-09-16 (`0.20.0+06b6696`), so this is now WORK TO DO,
  not a forward-looking note.** Live on the server as of that build: `secrets` and
  `secrets_retention` on `POST /submissions` (server default `ttl:720h`, per-submission
  `keep`), `secret_env` opt-in delivery, `secret_names`/`secrets_state` metadata,
  `DELETE …/secrets` purge, a 409 on retry-after-purge, and 403 for
  anonymous-with-secrets.
  **Cutover precedence, confirmed against the shipped v0.20.0 code (2026-09-16):**
  if a worker's `--secret-file` also defines a name the submission supplies, the
  **submission's value wins** for that task and the worker logs a WARN naming the
  variable. That is the safe direction: during the transition a tenant-supplied
  DSN overrides a stale group-file entry rather than the other way round, so the
  two can coexist and the group file can be emptied *after* the tenant API starts
  sending secrets, not before. Delivery is by exact name on all three runtimes —
  `cmd.Env` locally, `-e NAME` on Docker, `APPTAINERENV_NAME` under Apptainer,
  which Apptainer injects as plain `NAME` inside the container — so a tool reading
  `COLLECTION_STORE_DSN` needs no code change; only the suffix convention goes.

  **Retention does not threaten a long ingest.** The sweep considers only
  TERMINAL submissions and `DELETE …/secrets` refuses a non-terminal one with 409,
  so a running multi-hour ingest cannot lose its secrets under any policy.
  Retention governs post-completion *retries* only: the server default `ttl:720h`
  covers a retry within 30 days of terminal state, and `keep` matters only beyond
  that. The default is therefore sufficient here; `keep` is a dev/demo
  convenience, not a correctness requirement.

  **v0.20.1 (2026-09-17) — a hotfix, and the reason it is recorded here is that it
  came out of #267 rather than the secrets work.** #267 made a failed pre-stage fail
  fast, and the new dispatch gate made an unstaged READY step wait; individually
  correct, together they deadlocked the exact case #267 targeted — a submission was
  activated to RUNNING on the same tick its pre-stage failed, the pre-stage loop only
  revisited PENDING rows, so it never retried, never hit the fail threshold, and sat
  RUNNING with zero tasks for 3.8 hours. v0.20.1 continues pre-staging on
  RUNNING-but-unstaged submissions and recovers already-stuck rows. Nothing about
  `secrets`, `secret_env` or retention changed, so everything above still holds.

  Worth keeping in mind when reading this ADR's confidence about #260: that surface
  was verified on 0.20.0 and has not been re-exercised on 0.20.1. The hotfix's
  regression test (`TestPrestageDeadlock_NeverStrandedRunningWithoutPrestage`) pins
  the combination rather than either half, which is the right shape — a test for
  each mechanism alone would have passed throughout.

  On #260 landing: the tenant API adds
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
