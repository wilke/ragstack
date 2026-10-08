// Recorded control-plane bodies, for the string-render tests.
//
// Shapes are taken from a `ragstack-ctl serve --fake-drivers` daemon, which
// serves `go/internal/ctl/testdata/live-2026-09-10` — the four live tenants as
// they were on the reboot day: dev (dedicated stores, ES heap drift),
// demo and asm-next (shared stores, so their store health is `n/a`),
// lucid-next (mixed: shared qdrant on :6343, exclusive ES on :24003).
//
// Two deliberate departures from what the daemon actually answers, both so a
// test can assert something the fixture would otherwise never exercise:
//
//  * asm-next carries an `error` finding, making the fleet doctor RED. The
//    recorded fleet is yellow, and a chip that has only ever been rendered
//    yellow is a chip whose red path is untested.
//  * `hostileEnv` contains values the daemon would never emit — a real-looking
//    key, a password, a token, a DSN. The client's redaction must not depend on
//    the server's, so the test feeds it a server that leaked.
//
// This module is imported by tests only; nothing in `main.tsx` reaches it, so
// it is not in the bundle.

import type {
  CtlDoctor,
  CtlEnv,
  CtlFleet,
  CtlGateway,
  CtlGatewayRender,
  CtlLogs,
  CtlTenant,
  CtlVersion,
  FleetRow,
} from "./api/types";
import type {
  ArtifactsResponse,
  JobState,
  JobsResponse,
  Job,
  Plan,
  SecretsResponse,
  SettingsResponse,
  Step,
} from "./api/types";
import { JOB_STATES } from "./api/types";

const AT = "2026-09-10T12:00:00Z";

export const fleetFixture: CtlFleet = {
  generated_at: AT,
  registry_generation: 3,
  host: {
    disk_free_bytes: 1_800_000_000_000,
    linger: false,
    ctl_unit_active: false,
    vm_max_map_count: 262144,
    sudoers_group: ["wilke", "svcbvbrc"],
  },
  tenants: [
    {
      name: "dev",
      manifest_name: "dev",
      state: "active",
      owner: "wilke",
      supervisor: "manual",
      stores_mode: "dedicated",
      code_tag: "v1.5.3-44-geb41858",
      drift_count: 1,
      ports: { api: 24040, qdrant_http: 24041, es_http: 24043 },
      health: { api: "ok", qdrant: "ok", es: "ok", deep: "n/a" },
      units: { target: "n/a", api: "n/a", ui: "n/a", qdrant: "n/a", es: "n/a" },
      disk_bytes: 335_529_312_256,
      last_backup: null,
    },
    {
      name: "demo",
      manifest_name: "demo",
      state: "active",
      owner: "wilke",
      supervisor: "manual",
      stores_mode: "shared",
      code_tag: "v1.5.3",
      drift_count: 0,
      ports: { api: 24060, qdrant_http: 24061, es_http: 24063 },
      health: { api: "ok", qdrant: "n/a", es: "n/a", deep: "n/a" },
      units: { target: "n/a", api: "n/a", ui: "n/a", qdrant: "n/a", es: "n/a" },
      disk_bytes: 572_685_746_176,
      last_backup: { at: "2026-09-09T03:00:00Z", fenced: true, verified: true, checked: true },
    },
    {
      name: "lucid-next",
      manifest_name: "lucid",
      state: "active",
      owner: "wilke",
      supervisor: "manual",
      stores_mode: "mixed",
      code_tag: "v1.5.2",
      drift_count: 0,
      ports: { api: 24000, qdrant_http: 24001, es_http: 24003 },
      health: { api: "ok", qdrant: "n/a", es: "ok", deep: "n/a" },
      units: { target: "n/a", api: "n/a", ui: "n/a", qdrant: "n/a", es: "n/a" },
      disk_bytes: 1_090_111_143_936,
      last_backup: null,
    },
    {
      name: "asm-next",
      manifest_name: "asm",
      state: "active",
      owner: "wilke",
      supervisor: "manual",
      stores_mode: "shared",
      code_tag: "v1.5.2",
      drift_count: 2,
      ports: { api: 24020, qdrant_http: 24021, es_http: 24023 },
      health: { api: "degraded", qdrant: "n/a", es: "n/a", deep: "unknown" },
      units: { target: "n/a", api: "n/a", ui: "n/a", qdrant: "n/a", es: "n/a" },
      disk_bytes: 4_100_111_143_936,
      last_backup: { at: "2026-08-20T03:00:00Z", fenced: false, verified: false, checked: false },
    },
  ],
};

export const doctorFixture: CtlDoctor = {
  status: "red",
  hash: `sha256:${"b7f69dd6f922b9d958eb1170ee73b6fb817f0860bc60fd7c9a8ff96597ca7db".padEnd(64, "c")}`,
  generated_at: AT,
  scope: { tenant: null, op: null },
  findings: [
    {
      level: "info",
      code: "gateway_not_managed",
      tenant: null,
      detail: "the ctl does not own a gateway generation yet; routing is still hand-edited",
      repair: "gateway-apply",
    },
    {
      level: "warn",
      code: "es_heap_drift",
      tenant: "dev",
      detail: 'dev: ES_JAVA_OPTS is "1g" live but "512m" in provision.env',
    },
    {
      level: "warn",
      code: "supervisor_manual",
      tenant: "demo",
      detail: "tenant demo is hand-started (owner wilke); nothing restarts it at boot",
      repair: "render-units",
    },
    {
      level: "error",
      code: "pid_owner_mismatch",
      tenant: "asm-next",
      detail: "the process on the api port is owned by a uid the registry does not expect",
    },
  ],
};

export const versionFixture: CtlVersion = {
  version: "v1.5.3-44-geb41858",
  commit: "eb41858ff7a6f723d19d01cb6c89a7d554540386",
  built_at: "2026-09-12T07:43:14Z",
  go: "go1.23.12",
  schema_version: 1,
};

const devRow: FleetRow = fleetFixture.tenants[0];

export const tenantFixture: CtlTenant = {
  summary: devRow,
  // A viewer's body: the registry row is withheld, not empty.
  registry: null,
  status: {
    observed_at: AT,
    api_pid: 1234567,
    api_pid_owner: "wilke",
    listening: { api: true, qdrant_http: true, es_http: true, pg: false, ui: false },
    restart_pending: false,
    running_jobs: [],
  },
  units: {
    supervisor: "manual",
    target: null,
    services: [
      { kind: "api", name: "manual:api", active_state: "active", sub_state: "running", main_pid: 1234567, since: "2026-09-09T22:10:00Z" },
      { kind: "ui", name: "manual:ui", active_state: "inactive", sub_state: null, main_pid: null, since: null },
      { kind: "qdrant", name: "manual:qdrant", active_state: "active", sub_state: "running", main_pid: 1234000, since: "2026-09-09T22:09:00Z" },
      { kind: "es", name: "manual:es", active_state: "active", sub_state: "running", main_pid: 1233000, since: "2026-09-09T22:08:00Z" },
    ],
  },
  drift: [
    {
      code: "es_heap_drift",
      level: "warn",
      field: "ES_JAVA_OPTS",
      expected: "512m",
      actual: "1g",
      observed_at: "2026-09-10T00:00:00Z",
      note: "live heap differs from provision.env",
    },
  ],
};

/** A systemd-supervised tenant, so the units table renders ActiveState/SubState/NRestarts. */
export const managedUnitsFixture: CtlTenant = {
  ...tenantFixture,
  units: {
    supervisor: "systemd",
    target: { name: "ragstack-dev.target", active_state: "active", enabled: true },
    services: [
      { kind: "api", name: "ragstack-dev-api.service", active_state: "active", sub_state: "running", main_pid: 4242, since: "2026-09-10T09:00:00Z" },
      { kind: "es", name: "ragstack-dev-es.service", active_state: "failed", sub_state: "failed", main_pid: null, since: null },
    ],
  },
};

export const envFixture: CtlEnv = {
  tenant: "dev",
  env_layout: "legacy",
  source: "live",
  note: null,
  keys: [
    { key: "IDENTITY_PROVIDER", value_redacted: "bvbrc", source: "tenant.env", class: "public", drift: null },
    { key: "DEFAULT_ROLE", value_redacted: "user", source: "tenant.env", class: "public", drift: null },
    { key: "VECTOR_BACKEND", value_redacted: "qdrant", source: "tenant.env", class: "public", drift: null },
    { key: "ES_JAVA_OPTS", value_redacted: "1g", source: "tenant.env", class: "public", drift: "file_vs_live" },
  ],
};

/**
 * The pre-handover shape: tenant.env is 0600 and owned by the operator who
 * provisioned it, so the daemon cannot read it and answers from the
 * registry's own settings/secret_refs instead — `source: "registry"` and a
 * `note` explaining why, which the Config tab must show.
 */
export const registrySourcedEnvFixture: CtlEnv = {
  tenant: "dev",
  env_layout: "legacy",
  source: "registry",
  note: "tenant.env is not readable by svcbvbrc (uid 1002); showing the public settings recorded in the registry at adoption (2026-09-01T00:00:00Z). The tenant is owned by wilke until its handover (PR-E); drift against the live file cannot be checked until then.",
  keys: [
    { key: "LOG_LEVEL", value_redacted: "info", source: "registry", class: "public", drift: null },
    { key: "API_KEYS", value_redacted: "<redacted>", source: "registry", class: "secret", drift: null },
  ],
};

/**
 * What the client must survive if the DAEMON's redaction ever fails.
 *
 * `API_KEY_FINGERPRINT` is the allowed case — a derived value that exists so
 * the real key never has to be shown, and blanking it would remove the only
 * handle an operator has on a key. Everything else here is a bare credential
 * name and its value must not reach the markup, whatever the server said.
 */
export const hostileEnvFixture: CtlEnv = {
  tenant: "dev",
  env_layout: "legacy",
  source: "live",
  note: null,
  keys: [
    { key: "API_KEY_FINGERPRINT", value_redacted: "sha256:1a2b3c4d5e6f7a8b", source: "registry", class: "public", drift: null },
    { key: "API_KEYS", value_redacted: "leaked-api-key-value-0001", source: "secrets.env", class: "secret", drift: null },
    { key: "NEO4J_PASSWORD", value_redacted: "leaked-password-value-0002", source: "secrets.env", class: "secret", drift: null },
    { key: "GOWE_TOKEN", value_redacted: "leaked-token-value-0003", source: "tenant.env", class: "secret", drift: null },
    { key: "USER_STORE_DSN", value_redacted: "postgresql://u:leaked-dsn-value-0004@h/db", source: "tenant.env", class: "secret", drift: null },
  ],
};

/** The same hostile values arriving as a DRIFT row, which prints two values per key. */
const demoRow: FleetRow = fleetFixture.tenants[1];

/**
 * An OPERATOR body for `demo`: shared stores (qdrant/es `ownership: shared`,
 * so `status.listening.qdrant_http/es_http` — which is this tenant's OWN
 * port block — is correctly false; the registry row is what tells the
 * dashboard to render "shared" instead of a down chip) and a `static` UI
 * (built once, served by nginx — never a port of its own, so
 * `status.listening.ui` is not a real fact about it either).
 */
export const demoTenantFixture: CtlTenant = {
  summary: demoRow,
  registry: {
    name: "demo",
    manifest_name: "demo",
    data_dir: "/rag/data/tenants/demo",
    worktree: "/rag/repos/ragstack",
    artifact_id: null,
    code: { tag: "v1.5.3", sha: null, previous_artifact_id: null },
    python_env: "/rag/envs/ragstack",
    ports: { index: 3, base: 24060, api: 24060, qdrant_http: 24061, qdrant_grpc: 24062, es_http: 24063, es_transport: 24064, pg: 24065 },
    api: { bind: "0.0.0.0", pidfile: "/rag/data/tenants/demo/api.pid", log: "/rag/data/tenants/demo/logs/api.log" },
    stores: {
      qdrant: { ownership: "shared", capabilities: { stop: false, purge: false, restore: false, snapshot: false }, url: "http://localhost:6333", instance: null, sif: null, extra_env: {} },
      elasticsearch: { ownership: "shared", capabilities: { stop: false, purge: false, restore: false, snapshot: false }, url: "http://localhost:9200", instance: null, sif: null, heap: null, provision_heap: null, path_repo: null, extra_env: {} },
      neo4j: { ownership: "external", url: null },
      // demo predates `--postgres`: its ACL/job/collection state is SQLite
      // under state/, so there is no server and the block's +5 port is unused.
      postgres: { kind: "sqlite", ownership: "exclusive", capabilities: { stop: false, purge: false, restore: false, snapshot: false }, url: null, port: null, instance: null, sif: null, data_dir: null },
      dormant_provisioned_dirs: true,
    },
    ui: { mode: "static", port: null, base: "/ragstack/demo/ui/" },
    supervisor: "manual",
    owner: "wilke",
    state: "active",
    desired_boot: "disabled",
    env_layout: "legacy",
    settings: {},
    secret_refs: [],
    env_file_sha256: `sha256:${"a".repeat(64)}`,
    secrets_file_sha256: null,
    identity: { provider: "bvbrc", admin_subjects_count: 1 },
    keys: [],
    service_accounts: [],
    external_refs: [],
    unmanaged_files: [],
    drift: [],
    restart_pending: false,
    release_generation: null,
    rollback_descriptor: null,
    last_ops: {},
    last_backup: { bundle: "/rag/backups/demo/2026-09-09.tar.zst", at: "2026-09-09T03:00:00Z", kind: "backup", fenced: true, verified: true },
    adopted_at: "2026-08-01T00:00:00Z",
  },
  status: {
    observed_at: AT,
    api_pid: 7654321,
    api_pid_owner: "wilke",
    listening: { api: true, qdrant_http: false, es_http: false, pg: false, ui: false },
    restart_pending: false,
    running_jobs: [],
  },
  units: {
    supervisor: "manual",
    target: null,
    services: [
      { kind: "api", name: "manual:api", active_state: "active", sub_state: "running", main_pid: 7654321, since: "2026-09-09T22:10:00Z" },
    ],
  },
  drift: [],
};

/**
 * An OPERATOR body for a `--postgres local` tenant (hackathon on coconut): a
 * DEDICATED postgres instance on the block's +5 port. It is the only shape in
 * which `status.listening.pg` is a real fact about the tenant, and the only
 * one whose Listening table shows a postgres port rather than a kind label.
 */
export const postgresTenantFixture: CtlTenant = {
  ...demoTenantFixture,
  summary: { ...demoTenantFixture.summary, name: "hackathon", manifest_name: "hackathon" },
  registry: {
    ...demoTenantFixture.registry!,
    name: "hackathon",
    manifest_name: "hackathon",
    ports: { index: 4, base: 24080, api: 24080, qdrant_http: 24081, qdrant_grpc: 24082, es_http: 24083, es_transport: 24084, pg: 24085 },
    stores: {
      ...demoTenantFixture.registry!.stores,
      postgres: {
        kind: "local",
        ownership: "exclusive",
        capabilities: { stop: false, purge: false, restore: false, snapshot: false },
        url: "postgresql://localhost:24085",
        port: 24085,
        instance: "postgres-hackathon",
        sif: "/rag/apptainer/images/postgres.sif",
        data_dir: "/rag/data/tenants/hackathon/postgres",
      },
    },
  },
  status: { ...demoTenantFixture.status, listening: { api: true, qdrant_http: true, es_http: true, pg: true, ui: false } },
};

export const hostileTenantFixture: CtlTenant = {
  ...tenantFixture,
  drift: [
    ...tenantFixture.drift,
    {
      code: "secret_value_drift",
      level: "error",
      field: "API_KEYS",
      expected: "leaked-api-key-value-0005",
      actual: "leaked-api-key-value-0006",
      observed_at: AT,
    },
  ],
};

export const gatewayFixture: CtlGateway = {
  generation: 7,
  published_at: "2026-09-10T11:30:00Z",
  published_by: "svcbvbrc",
  txn_state: "complete",
  pending_diff: true,
  registry_generation: 3,
  nginx: {
    master_pid: 88123,
    master_uid: 1002,
    config_ok: true,
    last_reload_at: "2026-09-10T11:30:02Z",
  },
  routes: [
    { name: "dev", api: 24040, ui: 8090, ui_mode: "dev", readonly: false, status: "active" },
    { name: "demo", api: 24060, ui: 5210, ui_mode: "static", readonly: false, status: "active" },
    { name: "lucid-next", api: 24000, ui: 5211, ui_mode: "static", readonly: false, status: "active" },
    { name: "asm-next", api: 24020, ui: 5212, ui_mode: "static", readonly: false, status: "maintenance" },
  ],
  legacy_routes: [
    { name: "lucid", api: 8010, ui: 5175, ui_mode: "external", readonly: true, status: "active" },
    { name: "asm", api: 8000, ui: 5173, ui_mode: "external", readonly: true, status: "active" },
  ],
};

export const gatewayRenderFixture: CtlGatewayRender = {
  generation: 8,
  registry_generation: 3,
  changed: true,
  files: [
    {
      path: "conf.d/05-tenants.generated.conf",
      diff: "+map $tenant $tenant_api {\n+    dev  \"127.0.0.1:24040\";\n+}\n",
    },
    { path: "snippets/tenants-ui-static.generated.conf", diff: "" },
  ],
  sha256: `sha256:${"c4d89ba78ea0f4db258182e25ce33d0dffa110fe2cdd28ec494162a711b8f2".padEnd(64, "a")}`,
};

export const logsFixture: CtlLogs = {
  tenant: "dev",
  file: "api",
  lines: [
    "INFO api: ragstack v1.5.3 starting (tenant=dev)",
    "API_KEYS=<redacted>",
    "INFO api: ready",
  ],
  requested: 200,
  returned: 3,
  truncated: false,
  redacted: true,
};

// ---------------------------------------------------------------------------
// PR-G2.2: the mutation views — a plan, a job in every state, a jobs list, a
// secrets envelope, settings and artifacts, and their hostile variants.
//
// Shapes follow `contracts/ctl/schemas/*` (plan, job, secrets_response,
// settings_response, artifacts_response). Like `hostileEnvFixture` above, the
// hostile variants are bodies the daemon would never send: every free-text
// field carries a `leaked-*-value-000N` behind a secret NAME (an env
// assignment, a `--token` flag, a URL password, a `secrets.env` preview), so a
// test can assert that the client's own redaction holds whatever the server
// sent.
//
// `secretsFixture` is the exception that proves the rule: its two values are
// what `RevealOnce` MUST show — but only after the explicit reveal.
// ---------------------------------------------------------------------------

const H = (c: string) => `sha256:${c.repeat(64).slice(0, 64)}`;

export const planFixture: Plan = {
  plan_hash: H("3f9a"),
  op: "decommission",
  tenant: "dev",
  registry_generation: 3,
  schema_version: 1,
  doctor: {
    status: "yellow",
    hash: H("71c0"),
    generated_at: AT,
    scope: { tenant: "dev", op: "decommission" },
    findings: [
      {
        level: "warn",
        code: "supervisor_manual",
        tenant: "dev",
        detail: "tenant dev is hand-started (owner wilke); nothing restarts it at boot",
        repair: "render-units",
      },
    ],
  },
  requires_confirm: true,
  confirm_value: "dev",
  steps: [
    {
      n: 1,
      kind: "probe",
      title: "Check the decommission preconditions",
      destructive: false,
      targets: ["dev"],
      would_write: [],
      would_run: [],
      warnings: [],
    },
    {
      n: 2,
      kind: "tar",
      title: "Write a fenced archive bundle with secrets",
      destructive: false,
      targets: ["/rag/backups/dev/2026-10-08T120000Z.tar.zst"],
      would_write: [
        {
          path: "/rag/backups/dev/2026-10-08T120000Z.manifest.json",
          mode: "0640",
          preview: '{\n  "tenant": "dev",\n  "fenced": true,\n  "secrets": "sealed"\n}',
        },
        // The daemon sends null for a secret file's content; the path still shows.
        { path: "/rag/backups/dev/2026-10-08T120000Z/secrets.env.age", mode: "0600", preview: null },
      ],
      would_run: [
        {
          argv: [
            "/usr/bin/tar",
            "--zstd",
            "-cf",
            "/rag/backups/dev/2026-10-08T120000Z.tar.zst",
            "-C",
            "/rag/data/tenants/dev",
            ".",
          ],
        },
      ],
      warnings: [],
    },
    {
      n: 3,
      kind: "apptainer",
      title: "Stop the tenant's API and store instances",
      destructive: true,
      targets: ["manual:api", "qdrant-dev", "es-dev"],
      would_write: [],
      would_run: [
        { argv: ["/usr/bin/apptainer", "instance", "stop", "qdrant-dev"] },
        { argv: ["/usr/bin/apptainer", "instance", "stop", "es-dev"] },
      ],
      warnings: ["open sessions on dev end when the API stops"],
    },
    {
      n: 4,
      kind: "fs",
      title: "Move the data directory into quarantine",
      destructive: true,
      targets: ["/rag/data/tenants/dev"],
      would_write: [
        {
          path: "/rag/data/tenants/.quarantined-dev-20261008/RECOVERY.json",
          mode: "0640",
          preview: '{\n  "tenant": "dev",\n  "bundle": "/rag/backups/dev/2026-10-08T120000Z.tar.zst"\n}',
        },
      ],
      would_run: [],
      warnings: ["nothing is deleted; purge is a separate, later operation"],
    },
    {
      n: 5,
      kind: "registry",
      title: "Mark dev quarantined",
      destructive: false,
      targets: ["registry.json"],
      would_write: [],
      would_run: [],
      warnings: [],
    },
  ],
  warnings: ["dev has 1 drift row; the archive records it as found"],
};

export const hostilePlanFixture: Plan = {
  ...planFixture,
  doctor: {
    ...planFixture.doctor,
    findings: [
      {
        level: "error",
        code: "env_grammar",
        tenant: "dev",
        detail: "line 4: ADMIN_PASSWORD=leaked-password-value-0001 is not KEY=value",
      },
    ],
  },
  warnings: ["GOWE_TOKEN=leaked-token-value-0002 will be rotated"],
  steps: [
    {
      n: 1,
      kind: "envfile",
      title: "Write API_KEYS=leaked-api-key-value-0003",
      destructive: true,
      targets: ["USER_STORE_DSN=postgresql://u:leaked-dsn-value-0004@localhost/db"],
      would_write: [
        // A secret file whose preview the daemon failed to null.
        { path: "/rag/data/tenants/dev/secrets.env", mode: "0600", preview: "API_KEYS=leaked-api-key-value-0005" },
        {
          path: "/rag/data/tenants/dev/tenant.env",
          mode: "0640",
          preview:
            'LOG_LEVEL=info\nNEO4J_PASSWORD=leaked-password-value-0006\n{"client_secret": "leaked-secret-value-0007"}\nDSN=postgresql://ragstack:leaked-dsn-value-0008@127.0.0.1:24085/ragstack',
        },
        {
          path: "/etc/systemd/user/ragstack-dev-api.service",
          mode: "0644",
          preview: "Environment=GOWE_TOKEN=leaked-token-value-0009",
        },
      ],
      would_run: [
        { argv: ["/usr/bin/curl", "--api-key", "leaked-api-key-value-0010", "http://127.0.0.1:24040/v1/health"] },
        { argv: ["/usr/bin/ragstack-admin", "--token=leaked-token-value-0011"] },
      ],
      warnings: ["password: leaked-password-value-0012"],
    },
  ],
};

const JOB_IDS: Record<JobState, string> = {
  queued: "01J9Z3K7Q8M4N5P6R7S8T9V0W0",
  running: "01J9Z3K7Q8M4N5P6R7S8T9V0W1",
  awaiting_cutover: "01J9Z3K7Q8M4N5P6R7S8T9V0W2",
  succeeded: "01J9Z3K7Q8M4N5P6R7S8T9V0W3",
  failed: "01J9Z3K7Q8M4N5P6R7S8T9V0W4",
  rolled_back: "01J9Z3K7Q8M4N5P6R7S8T9V0W5",
  interrupted: "01J9Z3K7Q8M4N5P6R7S8T9V0W6",
  cancelled: "01J9Z3K7Q8M4N5P6R7S8T9V0W7",
};

const T0 = "2026-10-08T12:00:00Z";
const at = (s: number) => new Date(Date.parse(T0) + s * 1000).toISOString().replace(".000Z", "Z");

function step(n: number, title: string, kind: string, state: Step["state"], extra: Partial<Step> = {}): Step {
  const started = state === "pending" || state === "skipped" ? null : at(n * 10);
  const finished = state === "pending" || state === "running" || state === "skipped" ? null : at(n * 10 + 7);
  return {
    n,
    kind,
    title,
    state,
    attempts: started ? 1 : 0,
    started_at: started,
    finished_at: finished,
    error: null,
    log: started ? `steps/${n}.log` : null,
    checkpoint: state === "succeeded",
    external_ids: [],
    ...extra,
  };
}

const STEP_SPECS: [string, string][] = [
  ["Check the preconditions", "probe"],
  ["Write a fenced archive bundle", "tar"],
  ["Stop the tenant's instances", "apptainer"],
  ["Move the data directory into quarantine", "fs"],
  ["Mark the tenant quarantined", "registry"],
];

/** Step states per job state: mixed on purpose, so every chip renders. */
const STEP_STATES_FOR: Record<JobState, Step["state"][]> = {
  queued: ["pending", "pending", "pending", "pending", "pending"],
  running: ["succeeded", "succeeded", "running", "pending", "pending"],
  awaiting_cutover: ["succeeded", "succeeded", "succeeded", "pending", "pending"],
  succeeded: ["succeeded", "succeeded", "succeeded", "skipped", "succeeded"],
  failed: ["succeeded", "succeeded", "failed", "pending", "pending"],
  rolled_back: ["succeeded", "rolled_back", "failed", "pending", "pending"],
  interrupted: ["succeeded", "succeeded", "interrupted", "pending", "pending"],
  cancelled: ["succeeded", "skipped", "skipped", "skipped", "skipped"],
};

/**
 * An OPERATOR body for a job in `state`. `{ viewer: true }` gives the viewer's
 * shape: same schema, `worker`/`lock`/`reservations`/`steps[].log`/
 * `steps[].external_ids` nulled or emptied, as the daemon serves it.
 */
export function jobFixture(state: JobState, opts: { viewer?: boolean } = {}): Job {
  const states = STEP_STATES_FOR[state];
  const steps = STEP_SPECS.map(([title, kind], i) => {
    const s = step(i + 1, title, kind, states[i]);
    if (s.n === 2 && s.state !== "pending") s.external_ids = ["bundle:2026-10-08T120000Z"];
    if (s.state === "failed") {
      s.error = "apptainer instance stop es-dev: exit status 255";
      s.attempts = 2;
    }
    return s;
  });
  const parked = state === "awaiting_cutover" || state === "interrupted";
  const live = state === "running" || parked;
  const current = steps.find((s) => s.state === "running" || s.state === "failed" || s.state === "interrupted");
  const failedRun = state === "failed" || state === "rolled_back";
  const job: Job = {
    id: JOB_IDS[state],
    op: state === "awaiting_cutover" ? "handover" : "decommission",
    tenant: "dev",
    principal: "wilke@patricbrc.org",
    auth_method: "api_key",
    sudo_user: null,
    state,
    plan_hash: planFixture.plan_hash,
    request_id: "a1b2c3d4e5f60718",
    idempotency_key: `idem-${state}`,
    created_at: T0,
    started_at: state === "queued" ? null : at(5),
    finished_at: live || state === "queued" ? null : at(60),
    worker: live ? { pid: 424242, host: "coconut", mode: "daemon" } : null,
    lock: live ? { order: ["registry", "tenant"], since: at(5) } : null,
    reservations: live ? [{ resource: "dir:/rag/data/tenants/.quarantined-dev-20261008", until: null }] : [],
    current_step: state === "awaiting_cutover" ? 4 : (current?.n ?? null),
    steps,
    result: null,
    error: failedRun
      ? { step: 3, code: "driver_failed", detail: "apptainer instance stop es-dev: exit status 255" }
      : null,
    rollback:
      state === "failed"
        ? { attempted: true, state: "partial", detail: "the archive bundle is kept; es-dev is still running" }
        : state === "rolled_back"
          ? { attempted: true, state: "succeeded", detail: "the bundle step was undone; the tenant is as it was" }
          : null,
  };
  if (!opts.viewer) return job;
  return {
    ...job,
    worker: null,
    lock: null,
    reservations: [],
    steps: job.steps.map((s) => ({ ...s, log: null, external_ids: [] })),
  };
}

/** Step log lines by step number, as `GET /v1/jobs/{id}/steps/{n}/log` returns them. */
export const stepLogsFixture: Record<number, string[]> = {
  2: ["tar: writing /rag/backups/dev/2026-10-08T120000Z.tar.zst", "tar: 1.2 GiB, sha256 ok"],
  3: ["apptainer: stopping qdrant-dev", "apptainer: stopping es-dev", "FATAL: exit status 255"],
};

export const jobsFixture: JobsResponse = {
  jobs: JOB_STATES.map((s) => jobFixture(s)),
  limit: 50,
  truncated: false,
};

/** A failed job whose every server string carries a leaked value behind a secret name. */
export const hostileJobFixture: Job = (() => {
  const base = jobFixture("failed");
  return {
    ...base,
    steps: base.steps.map((s) =>
      s.n === 3
        ? {
            ...s,
            title: "Rotate GOWE_TOKEN=leaked-token-value-0101",
            error: "curl --api-key leaked-api-key-value-0102 failed; API_KEYS=leaked-api-key-value-0103",
            external_ids: ["dsn=postgresql://u:leaked-dsn-value-0104@h/db"],
            log: "steps/3.log",
          }
        : s,
    ),
    error: { step: 3, code: "driver_failed", detail: "NEO4J_PASSWORD=leaked-password-value-0105 rejected" },
    rollback: { attempted: true, state: "failed", detail: "client_secret: leaked-secret-value-0106 left on disk" },
  };
})();

export const hostileStepLogsFixture: Record<number, string[]> = {
  3: [
    "env: API_KEYS=leaked-api-key-value-0107",
    "exec /usr/bin/curl --token=leaked-token-value-0108",
    '{"password": "leaked-password-value-0109"}',
  ],
};

/**
 * The one body whose values MUST render — after the explicit reveal only. The
 * canary asserts they are absent from the pre-click render and present after.
 */
export const secretsFixture: SecretsResponse = {
  job_id: JOB_IDS.succeeded,
  delivered_at: "2026-10-08T12:01:00Z",
  expires_at: "2026-10-08T12:16:00Z",
  secrets: [
    { id: "k-01", label: "bootstrap admin", role: "admin", value: "leaked-secret-value-0001" },
    { id: "k-02", label: "ingest worker", role: "user", value: "leaked-secret-value-0002" },
  ],
};

export const settingsFixture: SettingsResponse = {
  registry_generation: 3,
  retention: { keep_last: { backup: 7, pre_update: 3 }, keep_partial_hours: 24, auto_delete: false },
  images: {
    qdrant: { sif: "/rag/apptainer/images/qdrant.sif", version: "1.12.4", digest: H("9d") },
    elasticsearch: { sif: "/rag/apptainer/images/elasticsearch.sif", version: "8.15.2", digest: H("e4") },
  },
  python_env_default: "/rag/envs/ragstack",
  ctl: { port: 24100, ui_dist: "/rag/data/ctl/ui/dist", gateway_enabled: true },
  recipients: {
    file: "/rag/config/ctl/backup-recipients.txt",
    count: 1,
    fingerprints: ["sha256:0a1b2c3d4e5f6071"],
    read_only: true,
  },
};

export const artifactsFixture: ArtifactsResponse = {
  artifacts: [
    {
      id: "v1.6.4-12-gabc1234",
      sha: "abc1234def5678901234567890abcdef12345678",
      tag: "v1.6.4-12-gabc1234",
      prepared_at: "2026-10-07T09:00:00Z",
      prepared_by: "svcbvbrc",
      schema_compatible: true,
      tenants: [],
    },
    {
      id: "v1.6.2",
      sha: "0123456789abcdef0123456789abcdef01234567",
      tag: "v1.6.2",
      prepared_at: "2026-09-17T09:00:00Z",
      prepared_by: "wilke",
      schema_compatible: true,
      tenants: ["hackathon"],
    },
  ],
};
