# ADR 0007 — Tenant control plane: `ragstack-ctl` owns tenant lifecycle, credentials, gateway and supervision

- **Status:** Proposed
- **Date:** 2026-09-11
- **Deciders:** @wilke
- **Amends:** [ADR-0005](0005-tenant-anatomy.md) decision 4 — "provisioning is a script, not an
  API; a runtime tenant-management API is deferred until scripted ops is the bottleneck". The
  trigger has fired; the API arrives as a **separate operator service**, not as a route on the
  tenant API.
- **Related:** [ADR-0006](0006-execution-topology-revised.md) decision 4, trigger row 3 ("a
  component must run where the conda environment cannot be installed → a static-binary tool,
  the `cmd/mcp` pattern") — the row this record invokes to build the control plane in Go;
  [ADR-0005](0005-tenant-anatomy.md) § *Deferred: federated tenancy* (the re-centralised-trust
  warning this record's threat model answers); `ops/coconut/restore.sh` (the 2026-09-10 reboot);
  the plan `ragstack-ctl — tenant management control plane` (v3.1, 2026-09-11)

## Context

Four tenants (`dev`, `demo`, `lucid-next`, `asm-next`) run on coconut as hand-started
processes owned by one human account: a uvicorn API from a per-tenant git worktree, a Vite
dev UI, and per-tenant Qdrant/Elasticsearch apptainer instances. Nothing starts at boot.
There is no backup tooling, no decommission, no credential rotation (keys are minted once by
`apptainer/new-tenant.sh` into `tenant.env`), and gateway routing is hand-edited in five
places. The **2026-09-10 reboot** showed the cost: `ops/coconut/restore.sh`, a script with
hard-coded tenant lists, was the only way back, and the host came up with the gateway and
Elasticsearch down until an admin persisted a sysctl and installed a unit by hand.

ADR-0005 decision 4 said a management API would wait until scripted ops became the
bottleneck. It is the bottleneck: the requested operations — create, start, shutdown,
snapshot, backup, restore, update, migrate, decommission, credentials, gateway generation,
boot persistence, a fleet dashboard — are each a multi-file edit today, none of them
recorded, none reversible. Each tenant API is blind to its siblings and has no subprocess
code; the only inventory is `manifest.tsv` plus the nginx map. That rules out putting the
management surface *inside* a tenant API: it would give a compromised tenant process the
means to reach every other tenant's files and processes, exactly the boundary ADR-0005 drew.

Two facts about the host shape the design. The tenants' Python environment is a shared
conda env whose editable finder points at a stale checkout, so a Python tool would carry the
`PYTHONPATH` pinning problem the tenants already have and could not be installed on a
migration target at all — ADR-0006's third trigger, verbatim. And `sudo` on coconut has
`requiretty`, so nothing non-interactive can escalate; whatever owns the tenants has to
*already be* the account that runs them.

Stated plainly for the record: the operator surface will be **reachable from the internet**
at `/ragstack/admin/{api,ui}` through the public gateway. That is a user decision for the
test/dev deployment (production will be internal), and it means the service holding every
tenant's admin credentials sits behind the same door as the tenants' own UIs. The controls in
decision 6 are the only boundary.

## Decision

**1. A separate operator service, `ragstack-ctl`, owns tenant lifecycle.** It is a Go static
binary — one program that is both the daemon (`serve`, loopback HTTP) and the CLI — plus a
TypeScript admin UI built as its own bundle. It owns creation, start/stop, backup, restore,
handover, decommission, credentials (API keys, admin subjects, service accounts), gateway
generation and reload, boot persistence and the fleet view. The tenant API gains only what
the ctl needs to *read*: `GET /v1/version` (any credential; `contracts/openapi.yaml`,
Python-only in v1) and, later, `/v1/me`. Go is licensed for this by ADR-0006 decision 4 row 3
and by nothing else — the API scaffold's freeze is untouched; this is the `cmd/mcp` pattern
applied to an operator tool, with `contracts/ctl/` + `conformance/ctl/` holding it to the
same contract-first discipline as the tenant API.

**2. `registry.json` is the source of truth; everything else is derived.** One file
(`/rag/data/tenants/registry.json`, schema in `contracts/ctl/schemas/registry.json`,
`additionalProperties: false`) records the fleet: tenants, ports, artifacts, images,
legacy routes, permanent tombstones. `manifest.tsv` is a projection of it (same header, rows
by index), the systemd units are rendered from it, the nginx maps and static-UI snippet are
rendered from it, `MIGRATE.md` in a bundle is rendered from it. The existing scripts and
`new-tenant.sh` keep working during migration by reading the projection; `new-tenant.sh` is
the **parity oracle** for the Go renderers (`--dry-run` output compared in tests) until it is
retired. Port allocation is `max(tenants ∪ tombstones) + 1`, so a decommissioned block is
never reissued. Adoption of the four live tenants is registry-only and gated on a preview
that reconciles with the live manifest before anything is committed.

**3. Every mutation is a job: planned, idempotent, locked, audited, resumable.** A mutating
call first produces a **plan** — steps with the files it would write (redacted preview) and
the commands it would run — with a hash over the plan, the registry generation and the
doctor findings. Execution re-validates the plan *after* taking locks (registry → manifest →
tenant → gateway → images, one order everywhere, the CLI's `--direct` mode included) and
refuses `plan_stale` / `doctor_red` / `locked` / `confirm_required` rather than proceeding.
A persisted idempotency key returns the original job for a replay. Steps record durable
external ids (snapshot names, unit names, staging dirs) *before* the external call; on daemon
start, jobs whose worker died are marked `interrupted` and their external outcomes reconciled
before any resume. Every intent and outcome is an audit row with principal, auth method,
`SUDO_USER`, request id, redacted args and plan hash. Minted secrets never appear in a job,
log or audit row: they are delivered once through an encrypted envelope and then gone.

**4. One service account owns managed tenants and the gateway; `systemd --user` is the
supervisor.** `svcbvbrc` — confirmed by the user to be a service account, and already the
owner of the nginx gateway — runs the daemon and, after a rehearsed handover, every managed
tenant. Supervision is `systemd --user` under that account with linger enabled and a
root-installed `user@<uid>` drop-in (`RequiresMountsFor=/rag`, unit path on `/rag`, not in
the NFS home). Stores run as **foreground** `apptainer run` under `Type=simple` with
`KillMode=mixed`, because an Elasticsearch that is flushing must outlive the SIGTERM to its
starter and `squashfuse_ll` must outlive the ES; the selftest asserts a graceful ES stop and no
SIGKILL. The API unit gates on a bounded readiness probe (`ragstack-ctl wait-ready`) and
carries `RAGSTACK_GIT_TAG`/`_SHA` so `/v1/version` reports the launched artifact. Ownership
is expressed through a `ragops` group with a permission table the writers enforce before
`rename` — never `chown`, so rollback needs no root. Consequence: `apptainer instance list`
no longer shows managed stores; ops scripts read the registry.

**5. The ctl manages; it does not query.** Its only calls into a tenant API are an exact
allowlist — `GET /health`, `/v1/version`, `/v1/health/deep`, `/v1/config`,
`/v1/collections?counts=false`, `/v1/jobs`, and the service-account and user-role admin
routes — matched on `(method, path-template, query-set)` after `url.Parse` + `path.Clean`,
credentials sent only to the tenant's *registered* origin, no redirects followed. A control
plane that could search would be a federation gateway with no threat model, which ADR-0005
deliberately deferred; this one cannot.

**6. The control plane is internet-reachable by decision, and these controls are the
boundary.** The listener binds `127.0.0.1:23990` only; the gateway proxies
`/ragstack/admin/api/` to it and overwrites `X-Forwarded-Prefix` and `Host` (forged and
duplicate forwarding headers are tested before exposure). A BV-BRC token or a ctl API key
(bound to principal, role and a revocation id) authenticates; both together is a 400; none
is 401; a **valid credential whose subject is not listed is 403 — there is no default
role**. The browser exchanges a credential for an opaque, server-side, subject-bound session
kept in `sessionStorage`, and that session authorises **reads only**; **every mutation
re-presents a ctl API key in the request body**, which the browser never persists (BV-BRC
tokens carry no audience and are already stored by the tenant UIs on the same origin).
Rate limiting is keyed by `sha256(credential)` with a global tarpit, because all public
traffic arrives from one `$remote_addr`. The admin bundle ships a strict CSP, a fixed API
base and no backend selector, from a reviewed production build.

The **executable surface is split from settings**: the HTTP API mutates only typed,
allowlisted public settings and consumes *prepared artifact ids*; code refs, images,
toolchains, unit templates and backup recipients are CLI-only trusted-operator actions, and
`npm ci`/builds never run from a request. Archive extraction rejects absolute and traversing
entries, symlink/hardlink escapes, special files and overlapping trees, and enforces size
and count limits; destructive operations go through directory handles with the tenant's
identity re-verified immediately before mutation. Backups put every secret-bearing file —
current and historical — inside an `age` payload and **fail closed** when no recipients are
configured; a restore in v1 targets **only a fresh, isolated tenant**, never in place. And the
code contains **no shell, no `sudo`, no `pkill`, no `--fakeroot`** (lint tests): subprocesses
are argv lists of absolute allowlisted programs with a sanitised environment; humans reach
the service account through `ops/coconut/ctl-as-svc.sh` from a tty.

**7. Host bootstrap is Ansible, and only host bootstrap.** Root items (the `ragops` group,
linger, the `user@` drop-in, sysctls, the proxy unit and the crontab line it replaces), the
optional hardening pass and the ctl install live in `ops/ansible/` and double as the
installer for a migration target. The ctl **never invokes Ansible**, and Ansible **never
performs a tenant operation** — tenant lifecycle and gateway generation are the ctl's alone,
so there is exactly one writer for each artifact. `doctor` checks the same facts the
playbooks assert, so playbook/reality drift shows on the dashboard.

**8. Fleet admins are the `seed-admins-svcbvbrc` sudoers group.** Its members are the
people who may become the service account; the CLI records `SUDO_USER` on every audit row
so an action taken *as* `svcbvbrc` is still attributed to the human who took it. No new
sudoers rules are added by this record.

## Threat model

Internet-reachable by decision, re-centralised trust across tenants (ADR-0005's federation
warning), and accepted because the operations it replaces are already single-account,
unrecorded and irreversible — the ctl makes them audited, planned and reversible. What is
defended, and how:

| Threat | Control (decision) |
|---|---|
| Credential theft via the public mount — the surface holds every tenant's admin key | loopback bind behind the gateway; sessions are read-only; mutations re-present a ctl key never persisted by the browser; CSP + reviewed bundle; credential-keyed rate limit + tarpit (6) |
| A valid BV-BRC user who is not an operator | 403-default, no default role; authz matrix is deny-by-default op × role (6) |
| Header forgery through the proxy (`X-Forwarded-Prefix`, `Host`) | gateway overwrites both; authn/authz are independent of source address; forged/duplicate header tests before exposure (6) |
| Code execution through a "setting" (a path, an image, a code ref) | executable-surface split; prepared artifacts pinned to SHA/digest by CLI only; typed public settings only over HTTP (6) |
| Injection via tenant names, paths, env values into units/nginx | single `ValidateName` + reserved list; renderers quote everything and refuse `[;{}$"'\\\n]`; envfile grammar refuses `$`, `export`, continuations (2, 6) |
| SSRF from the ctl into the host | tenant-API exact allowlist; loopback + port allowlist for store probes; registered origins only; no redirects (5) |
| Archive/bundle traversal on restore | safe extraction (no absolute/traversing/symlink-escape/special entries; size + count limits) (6) |
| Secrets leaking into registry, backups, logs, audit | classification table (public/secret/executable/unsupported); registry holds refs, never values; fail-closed `age` encryption; key-shaped and value-seeded redactors with canary tests for current, legacy and revoked values (3, 6) |
| Replay, double-execution, mid-job death | persisted idempotency key; locks in one order; reservations; reconcile-on-restart before resume (3) |
| Manifest / registry split-brain | registry generation first, projection second; permanent tombstones; `new-tenant.sh` dies when a registry exists (2) |
| Inconsistent or unrestorable backups | fenced backups with explicit inventory; isolated restore verification; only verified bundles count toward retention; no automatic deletion in v1 (6) |
| Restore destroying live data | v1 restores only into a fresh tenant; in-place restore is v1.x (6) |
| Privilege escalation through the tool | no sudo/shell/pkill/`--fakeroot` in code; one owner (`svcbvbrc`); no new sudoers rules; `SUDO_USER` in audit (4, 6, 8) |
| Group-writable binaries, units, SIFs | hardening pass (open decision) + `doctor` red on any writable path component (4, 7) |
| Handover losing writes | staged handover, gateway read-only, explicit commit, resync rollback from an immutable `rollback_descriptor` (3) |
| Decommission destroying the wrong thing | v1 is quarantine only (rename + tombstone + recovery bundle outside every deletion root); live purge is a separately named op in v1.x (3) |

Not defended, and said so: a compromise of the `svcbvbrc` account or of the gateway host
crosses every tenant boundary at once. That is the price of one owner, and it is the same
price the current single-human-account operation already pays without the audit trail.

## Consequences

**Accepted:**

- **A new component with its own contract, conformance suite and threat model** — the
  operations budget grows by one daemon, one CLI and one UI bundle, in a second language. It
  is paid for by the operations it replaces being hand-edited across five places today.
- **The public mount is a standing risk for test/dev**, mitigated but not removed by
  decision 6. Production moves to an internal mount (v1.x); until then the surface is
  reviewed as if it were production.
- **One account owns everything managed.** Handover of the existing tenants from the human
  account to `svcbvbrc` is a rehearsed, per-tenant, rollback-able operation, and until each
  is handed over it stays restorable by `restore.sh` — the two supervisors coexist, each
  skipping the other's tenants.
- **Root is needed once** (group, linger, drop-in, sysctls, proxy unit) and never again;
  every change after bootstrap is a user-level unit or a file the service account owns.
- **`apptainer instance list` stops being the inventory** and the ops scripts learn to read
  the registry; `new-tenant.sh` survives only as a parity oracle until retirement.
- **Conservative v1**: no automatic retention deletion, no in-place restore, no live purge,
  no cross-host migration, no key rotation with grace. Each is a named v1.x item with the
  precondition it waits on.

**Gained:** tenants that come back after a reboot without a human; every tenant operation
planned, dry-runnable, idempotent and audited; credentials that can be minted and revoked
with proof; backups that are fenced, verified and encrypted; a gateway generated from one
record instead of edited in five; and a fleet view that shows configured-vs-running for
every tenant.

## Alternatives considered

- **Keep the scripts** (`new-tenant.sh`, `restore.sh`, hand edits). Rejected: ADR-0005's own
  trigger is met — the 2026-09-10 reboot proved the scripts are the only path back and that
  their hard-coded lists are the inventory.
- **A Python ctl in the shared conda env.** Rejected: the env's editable finder points at a
  stale checkout, so the tool would inherit the `PYTHONPATH`-pinning problem it is meant to
  solve; and it could not be installed on a migration target without first reproducing the
  env — ADR-0006 decision 4 row 3, exactly.
- **A per-user ctl under the human account, with `sudo -u svcbvbrc` for the reload.**
  Rejected: `requiretty` makes non-interactive `sudo` impossible, and two owners of one
  gateway is the split-brain this record exists to remove.
- **System-level systemd units.** Rejected: every tenant change would need root; user units
  under the service account need root once, at bootstrap.
- **Campus-only mount for the control plane.** Rejected by the user for test/dev, where the
  admin surface must be reachable from outside; production will be internal, and the
  controls in decision 6 are designed as if it already were.
- **A dedicated new service account.** Not needed: the user confirms `svcbvbrc` *is* a
  service account (it already owns the gateway), and one owner for the gateway and the
  tenants is what makes reload and rollback root-free.
- **Management routes on the tenant API** (the shape ADR-0005 decision 4 deferred).
  Rejected: it would give a compromised tenant process the means to reach every sibling's
  files and processes — the boundary ADR-0005 drew, and the reason the ctl cannot query.

## Migration

Delivered as PRs A–E of the plan, each with a go/no-go: **A** contracts, this record,
`/v1/version`, the BV-BRC verifier fixture vectors (`contracts/fixtures/identity/bvbrc/`,
replayed by both verifiers), the read-only core and adoption of the four live tenants
(registry committed only after a preview reconciles with the live manifest, projection
byte-identical), and the Ansible roles run in check mode; **B** the daemon unit, gateway
generation as a semantic no-op for the four tenants (bodies byte-identical, same master pid),
and the first usable dashboard; **C** the job engine with crash-recovery tests; **D** managed
sandbox tenants end-to-end (create → backup → restore-as → quarantine) plus the no-login boot
rehearsal; **E** legacy-script fencing and the rehearsed `dev` handover (handover → rollback
→ handover → commit, 48 h soak). Remaining handovers, `update code`, the full admin UI and
retirement of the script tenant groups are v1.1; the final proof is a reboot drill with four
`/health` 200s before any human acts. Throughout, a tenant is restorable by exactly one
supervisor: `restore.sh` until its handover commits, `ragstack-ctl start --all` after.
