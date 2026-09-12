import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AdminApp, effectiveRole, hashFor, parseHash, VisionToggle } from "./AdminApp";
import { CtlError } from "./api/http";
import { ctlKeys } from "./api/queries";
import { FleetView } from "./components/FleetView";
import { GatewayDiff, GatewayView } from "./components/GatewayView";
import { LoginView } from "./components/LoginView";
import { TenantSection, TenantView } from "./components/TenantView";
import { isSecretName } from "./components/SecretSafeValue";
import {
  doctorFixture,
  envFixture,
  fleetFixture,
  gatewayFixture,
  gatewayRenderFixture,
  hostileEnvFixture,
  hostileTenantFixture,
  logsFixture,
  managedUnitsFixture,
  tenantFixture,
  versionFixture,
} from "./fixtures";

// String-render smoke tests, the same device as src/components/render.test.tsx:
// no DOM, no fetch. Data is SEEDED into the QueryClient under the exported
// query keys, so each view renders a recorded control-plane body and the
// assertions are about what an operator sees, not about what a mock was called
// with.

function render(node: ReactElement, seed?: (qc: QueryClient) => void): string {
  const qc = new QueryClient({
    // `retryOnMount` must join `retry: false` here: with no listeners yet (an
    // SSR string render never mounts), React Query treats every "error, no
    // data" query as due for a mount-time refetch regardless of `retry`, and
    // its OPTIMISTIC result for that pending fetch reports `status: "pending"`
    // — silently reverting exactly the seeded error state seedQueryError below
    // exists to test. Real views never hit this: they mount for real and the
    // seam is React Query's own refetch machinery, not app code.
    defaultOptions: { queries: { retry: false, retryOnMount: false, staleTime: Infinity } },
  });
  seed?.(qc);
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

const seedFleet = (qc: QueryClient) => {
  qc.setQueryData(ctlKeys.fleet(), fleetFixture);
  qc.setQueryData(ctlKeys.doctor(), doctorFixture);
  qc.setQueryData(ctlKeys.version(), versionFixture);
};

const seedTenant = (qc: QueryClient) => {
  seedFleet(qc);
  qc.setQueryData(ctlKeys.tenant("dev"), tenantFixture);
  qc.setQueryData(ctlKeys.tenantEnv("dev"), envFixture);
};

/**
 * Put a query into React Query's real "failed" shape — `setQueryData` alone
 * cannot express this, since a fetch failure sets `error` and `status:
 * "error"` while LEAVING any previously-cached `data` in place (query-core's
 * own reducer never clears `data` on an error action; see
 * `Query#dispatch`/case "error" in `@tanstack/query-core`). That retained-data
 * shape is exactly the bug this file guards: a stale success sitting next to
 * a live error, which a view must not present as a current answer.
 */
function seedQueryError(
  qc: QueryClient,
  key: readonly unknown[],
  opts: { data?: unknown; dataUpdatedAt?: number } = {},
): CtlError {
  const error = new CtlError({
    status: 500,
    code: "internal",
    detail: "the daemon had a problem",
    requestId: "dddddddd11112222",
  });
  const query = qc.getQueryCache().build(qc, { queryKey: key as unknown[] });
  query.setState({
    status: "error",
    fetchStatus: "idle",
    error,
    errorUpdateCount: 1,
    errorUpdatedAt: Date.now(),
    fetchFailureCount: 1,
    fetchFailureReason: error,
    isInvalidated: true,
    data: opts.data,
    dataUpdatedAt: opts.dataUpdatedAt ?? 0,
    dataUpdateCount: opts.data === undefined ? 0 : 1,
    fetchMeta: null,
  });
  return error;
}

const section = (id: "config" | "drift" | "logs" | "doctor", role: "viewer" | "operator") =>
  createElement(TenantSection, { section: id, name: "dev", role });

describe("admin login", () => {
  it("renders the sign-in screen with both credential paths", () => {
    const html = render(createElement(LoginView));
    expect(html).toContain("Control plane");
    expect(html).toContain("Control-plane key");
    expect(html).toContain("BV-BRC account");
    // The promise this bundle is built around, stated on the screen that makes it.
    expect(html).toContain("read-only session");
  });

  it("mounts AdminApp on the login screen when there is no session", () => {
    // `getServerSession()` is null during a string render — which IS the
    // signed-out state: no session, no fleet.
    const html = render(createElement(AdminApp));
    expect(html).toContain("Control plane");
    expect(html).not.toContain("host disk free");
  });
});

describe("hash deep links", () => {
  it("round-trips the two deep links and falls back to the fleet", () => {
    expect(parseHash("#/gateway")).toEqual({ kind: "gateway" });
    expect(parseHash("#/tenant/lucid-next")).toEqual({ kind: "tenant", name: "lucid-next" });
    expect(parseHash("#/nonsense")).toEqual({ kind: "fleet" });
    // A name the registry could never hold is not a deep link.
    expect(parseHash("#/tenant/../etc")).toEqual({ kind: "fleet" });
    expect(hashFor({ kind: "tenant", name: "dev" })).toBe("#/tenant/dev");
  });
});

describe("FleetView", () => {
  const html = render(createElement(FleetView, { onSelectTenant: () => {} }), seedFleet);

  it("lists all four tenants", () => {
    for (const name of ["dev", "demo", "lucid-next", "asm-next"]) {
      expect(html).toContain(name);
    }
  });

  it("shows the doctor verdict as a chip with a finding count", () => {
    expect(html).toContain("red");
    expect(html).toContain("4 findings");
  });

  it("shows the host band facts", () => {
    expect(html).toContain("host disk free");
    expect(html).toContain("vm.max_map_count");
    expect(html).toContain("262,144");
    expect(html).toContain("ctl version");
    expect(html).toContain(versionFixture.version);
    expect(html).toContain("linger");
    expect(html).toContain("tenants up");
  });

  it("renders ports tabular and 'never' for a tenant with no backup", () => {
    expect(html).toContain("tabular-nums");
    expect(html).toContain("24040");
    expect(html).toContain("never");
  });

  it("labels each health dot, so the three dots are not colour-only", () => {
    expect(html).toContain("api: ok");
    expect(html).toContain("qdrant: n/a");
    expect(html).toContain("api: degraded");
  });
});

// The doctor poll rides alongside the fleet poll but is read through its own
// query, so it fails and recovers independently — these are the two failure
// modes the reviewer reproduced: a first fetch that never had data, and a
// background refetch that clobbers nothing but must not pass its stale
// success off as current.
describe("FleetView doctor errors", () => {
  it("shows an error banner (with retry) instead of the loading chip when the first doctor fetch fails", () => {
    const html = render(createElement(FleetView, { onSelectTenant: () => {} }), (qc) => {
      qc.setQueryData(ctlKeys.fleet(), fleetFixture);
      qc.setQueryData(ctlKeys.version(), versionFixture);
      seedQueryError(qc, ctlKeys.doctor());
    });
    // The fleet table still rendered fine off its own successful query.
    expect(html).toContain("dev");
    // No indefinite "doctor …" placeholder, and no verdict chip to click into.
    expect(html).not.toContain("doctor …");
    expect(html).not.toContain("finding");
    // The failure itself is on screen, with a way to retry it.
    expect(html).toContain('role="alert"');
    expect(html).toContain("Reference: dddddddd11112222");
    expect(html).toContain("Retry");
  });

  it("marks a retained green doctor verdict as stale on a failed refresh, rather than showing it as current", () => {
    const greenDoctor = { ...doctorFixture, status: "green" as const };
    const html = render(createElement(FleetView, { onSelectTenant: () => {} }), (qc) => {
      qc.setQueryData(ctlKeys.fleet(), fleetFixture);
      qc.setQueryData(ctlKeys.version(), versionFixture);
      // A poll that had succeeded before (dataUpdatedAt in the past) and then
      // failed on its next tick — react-query's real shape for that keeps
      // `data` and flips `error`/`status`, which is what seedQueryError models.
      seedQueryError(qc, ctlKeys.doctor(), {
        data: greenDoctor,
        dataUpdatedAt: Date.parse("2026-09-10T11:00:00Z"),
      });
    });
    // The retained verdict is still on screen …
    expect(html).toContain("green");
    // … but qualified, not presented as the current read.
    expect(html).toContain("stale");
    // … and the failure has a retry.
    expect(html).toContain('role="alert"');
    expect(html).toContain("Retry");
  });

  it("qualifies the tenant table itself as stale when the fleet poll fails but rows are retained", () => {
    const html = render(createElement(FleetView, { onSelectTenant: () => {} }), (qc) => {
      qc.setQueryData(ctlKeys.doctor(), doctorFixture);
      qc.setQueryData(ctlKeys.version(), versionFixture);
      seedQueryError(qc, ctlKeys.fleet(), {
        data: fleetFixture,
        dataUpdatedAt: Date.parse("2026-09-10T11:55:00Z"),
      });
    });
    expect(html).toContain("dev"); // the retained rows are still shown …
    expect(html).toContain("stale"); // … qualified …
    expect(html).toContain('role="alert"'); // … with the failure surfaced.
  });
});

describe("TenantView", () => {
  it("renders the section rail and the overview", () => {
    const html = render(
      createElement(TenantView, { name: "dev", role: "viewer", onBack: () => {} }),
      seedTenant,
    );
    for (const label of ["Overview", "Config", "Drift", "Doctor", "Logs"]) {
      expect(html).toContain(label);
    }
    expect(html).toContain("manifest name");
    expect(html).toContain("Units");
  });

  it("says the registry row is withheld rather than showing an empty block", () => {
    const html = render(
      createElement(TenantView, { name: "dev", role: "viewer", onBack: () => {} }),
      seedTenant,
    );
    expect(html).toContain("Registry row");
    expect(html).toContain("Withheld");
  });

  it("renders ActiveState and SubState for a systemd-supervised tenant", () => {
    const html = render(
      createElement(TenantView, { name: "dev", role: "operator", onBack: () => {} }),
      (qc) => {
        seedFleet(qc);
        qc.setQueryData(ctlKeys.tenant("dev"), managedUnitsFixture);
      },
    );
    expect(html).toContain("ragstack-dev.target");
    expect(html).toContain("ragstack-dev-api.service");
    expect(html).toContain("ActiveState");
    expect(html).toContain("SubState");
    expect(html).toContain("failed");
  });

  it("renders the Config table: key, class chip, value, source, drift", () => {
    const html = render(section("config", "operator"), seedTenant);
    expect(html).toContain("IDENTITY_PROVIDER");
    expect(html).toContain("ES_JAVA_OPTS");
    expect(html).toContain("public");
    expect(html).toContain("tenant.env");
    expect(html).toContain("file_vs_live");
  });

  it("renders the drift rows", () => {
    const html = render(section("drift", "operator"), seedTenant);
    expect(html).toContain("es_heap_drift");
    expect(html).toContain("512m");
    expect(html).toContain("1g");
  });

  it("filters the doctor findings to this tenant", () => {
    const html = render(section("doctor", "viewer"), seedTenant);
    expect(html).toContain("es_heap_drift");
    // A finding about another tenant must not appear on this page.
    expect(html).not.toContain("pid_owner_mismatch");
  });

  it("tells a viewer that Logs needs an operator credential", () => {
    const html = render(section("logs", "viewer"), seedTenant);
    expect(html).toContain("Operator credential required");
  });

  // The `<pre>` an operator actually reads. It was untested, and it is the one
  // place this bundle renders whole lines of a file the daemon read off disk.
  it("renders the log lines an operator asked for, redaction marker included", () => {
    const html = render(section("logs", "operator"), (qc) => {
      seedTenant(qc);
      qc.setQueryData(ctlKeys.tenantLogs("dev", "api", 200), logsFixture);
    });
    expect(html).toContain("ragstack v1.5.3 starting (tenant=dev)");
    // The daemon redacts before the lines leave it; the screen shows the marker
    // rather than hiding that the key was there.
    expect(html).toContain("API_KEYS=&lt;redacted&gt;");
    expect(html).toContain("redacted by the daemon");
  });

  it("renders a hostile log line as text, not as markup", () => {
    const html = render(section("logs", "operator"), (qc) => {
      seedTenant(qc);
      qc.setQueryData(ctlKeys.tenantLogs("dev", "api", 200), {
        ...logsFixture,
        // A log line is attacker-influenced: anything that reaches a tenant's
        // request path can end up in its API log.
        lines: [...logsFixture.lines, "<script>alert(1)</script>"],
      });
    });
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).not.toContain("<script>");
  });

  it("marks the header's tenant summary as stale when /v1/tenants/{name} fails after a prior success", () => {
    const html = render(
      createElement(TenantView, { name: "dev", role: "operator", onBack: () => {} }),
      (qc) => {
        seedFleet(qc);
        qc.setQueryData(ctlKeys.tenantEnv("dev"), envFixture);
        seedQueryError(qc, ctlKeys.tenant("dev"), {
          data: tenantFixture,
          dataUpdatedAt: Date.parse("2026-09-10T11:00:00Z"),
        });
      },
    );
    // The retained summary is still readable in the header …
    expect(html).toContain(tenantFixture.summary.manifest_name);
    // … but qualified as stale, and the failure is surfaced with a retry.
    expect(html).toContain("stale");
    expect(html).toContain('role="alert"');
    expect(html).toContain("Retry");
  });
});

describe("effective role", () => {
  // `/v1/me` is the server's answer NOW; the session's copy is its answer at
  // sign-in. They disagree when the key's role changed since — and the Logs
  // gate used to trust the stale one while the header chip showed the fresh
  // one, so the two halves of the same screen could contradict each other.
  it("prefers /v1/me over the session's sign-in copy", () => {
    expect(effectiveRole({ role: "viewer" }, { role: "operator" })).toBe(
      "viewer",
    );
    expect(effectiveRole({ role: "operator" }, { role: "viewer" })).toBe(
      "operator",
    );
  });

  it("falls back to the session copy only until /v1/me resolves", () => {
    expect(effectiveRole(undefined, { role: "operator" })).toBe("operator");
    expect(effectiveRole(undefined, { role: "viewer" })).toBe("viewer");
  });
});

describe("accessible-vision toggle", () => {
  // The mode's only control used to live on the tenant UI's Account screen,
  // which this bundle does not ship — so on the admin pages it was unreachable.
  it("is in the bundle, off by default, and announces its pressed state", () => {
    const html = render(createElement(VisionToggle));
    expect(html).toContain('aria-pressed="false"');
    expect(html).toContain("High contrast");
  });
});

describe("GatewayView", () => {
  const html = render(createElement(GatewayView), (qc) =>
    qc.setQueryData(ctlKeys.gateway(), gatewayFixture),
  );

  it("renders the generation, publication and nginx facts", () => {
    expect(html).toContain("generation");
    expect(html).toContain("txn state");
    expect(html).toContain("complete");
    expect(html).toContain("88123");
    expect(html).toContain("pending diff");
  });

  it("renders both route tables", () => {
    expect(html).toContain("Routes");
    expect(html).toContain("Legacy routes");
    for (const name of ["dev", "demo", "lucid-next", "asm-next", "lucid", "asm"]) {
      expect(html).toContain(name);
    }
    expect(html).toContain("maintenance");
    expect(html).toContain("ui mode");
  });

  it("offers a diff and never an apply", () => {
    expect(html).toContain("Show diff");
    expect(html).not.toContain("Apply");
  });

  // The diff `<pre>` only appears after a mutation succeeds, which a string
  // render cannot reach — hence `GatewayDiff` as its own component.
  it("renders each file's diff, and '(unchanged)' for an empty one", () => {
    const diff = render(createElement(GatewayDiff, { render: gatewayRenderFixture }));
    expect(diff).toContain("conf.d/05-tenants.generated.conf");
    expect(diff).toContain("map $tenant $tenant_api {");
    expect(diff).toContain("127.0.0.1:24040");
    expect(diff).toContain("would publish generation 8");
    expect(diff).toContain("changed");
    // The second file's diff is "" — say so rather than showing a blank box.
    expect(diff).toContain("snippets/tenants-ui-static.generated.conf");
    expect(diff).toContain("(unchanged)");
  });

  it("renders a hostile diff line as text, not as markup", () => {
    const diff = render(
      createElement(GatewayDiff, {
        render: {
          ...gatewayRenderFixture,
          files: [{ path: "conf.d/05-tenants.generated.conf", diff: "+# <script>alert(1)</script>" }],
        },
      }),
    );
    expect(diff).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(diff).not.toContain("<script>");
  });
});

// --------------------------------------------------------------------------
// The secret rule: no field whose NAME is a bare credential may have its value
// rendered — whatever the server sent. The daemon redacts; this asserts the
// client does not depend on that.
// --------------------------------------------------------------------------

describe("no rendered value comes from a secret-named field", () => {
  it("classifies names the way the contract's allowlist does", () => {
    for (const n of [
      "API_KEYS",
      "api_key",
      "NEO4J_PASSWORD",
      "GOWE_TOKEN",
      "USER_STORE_DSN",
      "CLIENT_SECRET",
    ]) {
      expect(isSecretName(n)).toBe(true);
    }
    // The documented exception: a derived value exists so the real one never
    // has to be shown, and blanking it would remove the operator's only handle.
    for (const n of [
      "api_key_fingerprint",
      "secret_refs",
      "secrets_file_sha256",
      "key_fingerprints",
      "admin_subjects_count",
    ]) {
      expect(isSecretName(n)).toBe(false);
    }
  });

  it("withholds leaked values across every view that can render one", () => {
    const seedHostile = (qc: QueryClient) => {
      seedFleet(qc);
      qc.setQueryData(ctlKeys.tenant("dev"), hostileTenantFixture);
      qc.setQueryData(ctlKeys.tenantEnv("dev"), hostileEnvFixture);
      qc.setQueryData(ctlKeys.gateway(), gatewayFixture);
    };

    const markup = [
      render(createElement(FleetView, { onSelectTenant: () => {} }), seedHostile),
      render(
        createElement(TenantView, { name: "dev", role: "operator", onBack: () => {} }),
        seedHostile,
      ),
      render(section("config", "operator"), seedHostile),
      render(section("drift", "operator"), seedHostile),
      render(createElement(GatewayView), seedHostile),
    ].join("\n");

    for (const leaked of [
      "leaked-api-key-value-0001",
      "leaked-password-value-0002",
      "leaked-token-value-0003",
      "leaked-dsn-value-0004",
      "leaked-api-key-value-0005",
      "leaked-api-key-value-0006",
    ]) {
      expect(markup).not.toContain(leaked);
    }

    // The allowed derived value still renders: a test that only asserts absence
    // cannot tell "withheld correctly" from "the whole table is empty".
    const config = render(section("config", "operator"), seedHostile);
    expect(config).toContain("sha256:1a2b3c4d5e6f7a8b");
    expect(config).toContain("&lt;redacted&gt;");
  });
});
