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
      last_backup: { at: "2026-09-09T03:00:00Z", fenced: true, verified: true },
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
      last_backup: { at: "2026-08-20T03:00:00Z", fenced: false, verified: false },
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
    listening: { api: true, qdrant_http: true, es_http: true, ui: false },
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
  keys: [
    { key: "IDENTITY_PROVIDER", value_redacted: "bvbrc", source: "tenant.env", class: "public", drift: null },
    { key: "DEFAULT_ROLE", value_redacted: "user", source: "tenant.env", class: "public", drift: null },
    { key: "VECTOR_BACKEND", value_redacted: "qdrant", source: "tenant.env", class: "public", drift: null },
    { key: "ES_JAVA_OPTS", value_redacted: "1g", source: "tenant.env", class: "public", drift: "file_vs_live" },
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
  keys: [
    { key: "API_KEY_FINGERPRINT", value_redacted: "sha256:1a2b3c4d5e6f7a8b", source: "registry", class: "public", drift: null },
    { key: "API_KEYS", value_redacted: "leaked-api-key-value-0001", source: "secrets.env", class: "secret", drift: null },
    { key: "NEO4J_PASSWORD", value_redacted: "leaked-password-value-0002", source: "secrets.env", class: "secret", drift: null },
    { key: "GOWE_TOKEN", value_redacted: "leaked-token-value-0003", source: "tenant.env", class: "secret", drift: null },
    { key: "USER_STORE_DSN", value_redacted: "postgresql://u:leaked-dsn-value-0004@h/db", source: "tenant.env", class: "secret", drift: null },
  ],
};

/** The same hostile values arriving as a DRIFT row, which prints two values per key. */
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
