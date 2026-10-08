import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ctlKeys } from "./api/queries";
import type { CtlEnv, CtlRole, CtlTenant, Job } from "./api/types";
import {
  CredentialResult,
  CredentialsView,
  DESTRUCTIVE,
  EMPTY_CREDENTIAL_FORMS,
  credentialOpSpec,
  keyState,
  mintArgs,
  parseAdminSubjects,
  restartRefusal,
  revokeArgs,
  type CredOp,
  type CredentialForms,
  type CredentialsViewProps,
} from "./components/CredentialsSection";
import { jobDeliversSecrets, SECRET_BEARING_OPS } from "./components/OpFlow";
import { sectionsFor, TenantSection, TenantView } from "./components/TenantView";
import {
  adminSubjectsEnvFixture,
  credentialsTenantFixture,
  demoTenantFixture,
  envFixture,
  hostileCredentialsTenantFixture,
  jobFixture,
  supervisedCredentialsTenantFixture,
  tenantFixture,
} from "./fixtures";

// PR-G3.1: the Credentials section. Same device as views.test.tsx — string
// renders through the props-only seam (`CredentialsView`), the open operation
// and the form state passed in as props, data seeded under the query keys for
// the stateful wrapper.

function render(node: ReactElement, seed?: (qc: QueryClient) => void): string {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, retryOnMount: false, staleTime: Infinity } },
  });
  seed?.(qc);
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

const noop = () => {};

function view(extra: Partial<CredentialsViewProps> = {}): string {
  return render(
    createElement(CredentialsView, {
      name: "demo",
      role: "operator",
      tenant: credentialsTenantFixture,
      adminSubjects: parseAdminSubjects(adminSubjectsEnvFixture),
      active: null,
      forms: EMPTY_CREDENTIAL_FORMS,
      settled: null,
      round: 0,
      onOpen: noop,
      onClose: noop,
      onForms: noop,
      onDone: noop,
      ...extra,
    }),
  );
}

function count(haystack: string, needle: string): number {
  return haystack.split(needle).length - 1;
}

const seedDemo = (tenant: CtlTenant, env: CtlEnv | null = adminSubjectsEnvFixture) => (qc: QueryClient) => {
  qc.setQueryData(ctlKeys.tenant("demo"), tenant);
  if (env) qc.setQueryData(ctlKeys.tenantEnv("demo"), env);
};

// ---------------------------------------------------------------------------
// The view, by role
// ---------------------------------------------------------------------------

describe("CredentialsView — operator", () => {
  it("lists keys by fingerprint with their state, admins from ADMIN_SUBJECTS, and service accounts", () => {
    const html = view();
    // keys
    for (const label of ["asm-ops", "old-worker", "leaving-key", "ingest-worker"]) expect(html).toContain(label);
    for (const fp of ["sha256:0a1b2c3d4e5f6071", "sha256:1122334455667788", "sha256:99aabbccddeeff00", "sha256:abcdef0123456789"]) {
      expect(html).toContain(fp);
    }
    expect(html).toContain("svc-asm-web"); // tenant string
    expect(html).toContain("svcbvbrc"); // created_by
    expect(html).toContain(">effective<");
    expect(html).toContain(">revoked<");
    expect(html).toContain(">revoke pending<");
    expect(html).toContain(">pending restart<");
    // Revoke is offered for the two un-revoked keys only.
    expect(count(html, "Revoke…")).toBe(2);
    expect(html).toContain("Mint key…");
    // admins
    expect(html).toContain("bvbrc:wilke");
    expect(html).toContain("bvbrc:isingh");
    expect(count(html, "Remove…")).toBe(2);
    expect(html).toContain("pending until the");
    expect(html).toContain("Restart API…");
    expect(html).toContain("Add admin…");
    // service accounts
    expect(html).toContain("the ASM web front end");
    expect(html).toContain("svc-old-batch");
    expect(html).toContain(">active<");
    expect(html).toContain(">disabled<");
    expect(count(html, "Disable…")).toBe(1);
    expect(count(html, "Enable…")).toBe(1);
    expect(html).toContain("Create service account…");
  });

  it("renders the empty ledger and falls back to the registry's admin count when the env is withheld", () => {
    const html = view({ tenant: demoTenantFixture, adminSubjects: null });
    expect(html).toContain("No keys in this tenant");
    expect(html).toContain("No service accounts.");
    expect(html).toContain("1 admin subject");
    expect(html).toContain("ADMIN_SUBJECTS");
    expect(html).not.toContain("Revoke…");
    expect(html).not.toContain("Remove…");
    // Still able to act.
    expect(html).toContain("Mint key…");
    expect(html).toContain("Add admin…");
    expect(html).toContain("Create service account…");
  });

  it("does not offer removing the last admin subject", () => {
    const html = view({ adminSubjects: ["bvbrc:wilke"] });
    expect(html).toMatch(/disabled=""[^>]*title="The last admin subject cannot be removed\."/);
  });

  it("says the ledger is withheld when the registry row is null", () => {
    const html = view({ tenant: tenantFixture });
    expect(html).toContain("Withheld");
    expect(html).not.toContain("Mint key…");
  });
});

describe("Credentials — absent for a viewer", () => {
  it("the seam renders nothing", () => {
    expect(view({ role: "viewer" })).toBe("");
  });

  it("the rail lists Credentials for an operator only", () => {
    expect(sectionsFor("operator").map((s) => s.id)).toContain("credentials");
    expect(sectionsFor("viewer").map((s) => s.id)).not.toContain("credentials");
    const viewerRail = render(
      createElement(TenantView, { name: "demo", role: "viewer", onBack: noop }),
      seedDemo(demoTenantFixture),
    );
    expect(viewerRail).not.toContain("Credentials");
    const operatorRail = render(
      createElement(TenantView, { name: "demo", role: "operator", onBack: noop }),
      seedDemo(credentialsTenantFixture),
    );
    expect(operatorRail).toContain("Credentials");
  });

  it("reaching the section anyway answers the operator panel, not the ledger", () => {
    const html = render(
      createElement(TenantSection, { section: "credentials", name: "demo", role: "viewer" as CtlRole }),
      seedDemo(credentialsTenantFixture),
    );
    expect(html).toContain("Operator credential required.");
    expect(html).not.toContain("sha256:0a1b2c3d4e5f6071");
    expect(html).not.toContain("Mint key…");
  });

  it("the operator's section reads the tenant and its env", () => {
    const html = render(
      createElement(TenantSection, { section: "credentials", name: "demo", role: "operator" as CtlRole }),
      seedDemo(credentialsTenantFixture),
    );
    expect(html).toContain("sha256:0a1b2c3d4e5f6071");
    expect(html).toContain("bvbrc:isingh");
    expect(html).toContain("svc-asm-web");
  });

  it("an env without ADMIN_SUBJECTS falls back to the count", () => {
    const html = render(
      createElement(TenantSection, { section: "credentials", name: "demo", role: "operator" as CtlRole }),
      seedDemo(credentialsTenantFixture, envFixture),
    );
    expect(html).toContain("2 admin subjects");
    expect(html).not.toContain("bvbrc:isingh");
  });
});

// ---------------------------------------------------------------------------
// ADMIN_SUBJECTS parsing
// ---------------------------------------------------------------------------

describe("parseAdminSubjects", () => {
  const env = (value: string, cls: "public" | "secret" = "public"): CtlEnv => ({
    ...adminSubjectsEnvFixture,
    keys: [{ key: "ADMIN_SUBJECTS", value_redacted: value, source: "tenant.env", class: cls, drift: null }],
  });
  it("reads a JSON list and the comma form, like the daemon", () => {
    expect(parseAdminSubjects(env('["bvbrc:a","bvbrc:b"]'))).toEqual(["bvbrc:a", "bvbrc:b"]);
    expect(parseAdminSubjects(env(" bvbrc:a , bvbrc:b ,"))).toEqual(["bvbrc:a", "bvbrc:b"]);
    expect(parseAdminSubjects(env(""))).toEqual([]);
    expect(parseAdminSubjects(env("[]"))).toEqual([]);
  });
  it("is null when withheld: absent, not public, or no env", () => {
    expect(parseAdminSubjects(envFixture)).toBeNull();
    expect(parseAdminSubjects(env('["bvbrc:a"]', "secret"))).toBeNull();
    expect(parseAdminSubjects(undefined)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Argument building: what each form submits
// ---------------------------------------------------------------------------

describe("credentialOpSpec — each form's arguments", () => {
  const forms = (patch: Partial<CredentialForms>): CredentialForms => ({ ...EMPTY_CREDENTIAL_FORMS, ...patch });
  const spec = (op: CredOp, f: CredentialForms = EMPTY_CREDENTIAL_FORMS, sup: "instance" | "manual" = "instance") =>
    credentialOpSpec("demo", op, f, sup);

  it("key-mint: label + role, tenant_string when given, restart/prove only when chosen", () => {
    const base = forms({ mint: { label: " ingest-worker ", role: "admin", tenantString: "", restart: false, prove: false } });
    expect(spec({ kind: "key-mint" }, base)).toMatchObject({
      verb: "key-mint",
      args: { label: "ingest-worker", role: "admin" },
      destructive: false,
      problem: null,
    });
    expect(spec({ kind: "key-mint" }, base).args).not.toHaveProperty("restart");
    const full = forms({ mint: { label: "k1", role: "user", tenantString: "asm-ops", restart: true, prove: true } });
    expect(spec({ kind: "key-mint" }, full).args).toEqual({
      label: "k1",
      role: "user",
      tenant_string: "asm-ops",
      restart: true,
      prove: true,
    });
  });

  it("key-mint: refuses an invalid label, tenant string, or prove without restart", () => {
    const bad = (m: Partial<CredentialForms["mint"]>) =>
      spec({ kind: "key-mint" }, forms({ mint: { ...EMPTY_CREDENTIAL_FORMS.mint, label: "ok", ...m } })).problem;
    expect(bad({ label: "" })).toMatch(/label/);
    expect(bad({ label: "Bad_Label" })).toMatch(/label/);
    expect(bad({ tenantString: "-x" })).toMatch(/tenant string/);
    expect(bad({ prove: true, restart: false })).toMatch(/prove needs restart/);
    expect(bad({})).toBeNull();
  });

  it("key-revoke: the row's id, destructive, restart/prove as chosen", () => {
    const s = spec({ kind: "key-revoke", id: "old-worker" }, forms({ revoke: { restart: true, prove: true } }));
    expect(s).toMatchObject({ verb: "key-revoke", args: { id: "old-worker", restart: true, prove: true }, destructive: true, problem: null });
    expect(spec({ kind: "key-revoke", id: "old-worker" }).args).toEqual({ id: "old-worker" });
    // prove without restart is dropped, never sent
    expect(revokeArgs("k", { restart: false, prove: true }, "instance")).toEqual({ id: "k" });
  });

  it("admin-add / admin-remove: the subject, validated by the issuer:sub pattern", () => {
    expect(spec({ kind: "admin-add" }, forms({ adminSubject: " bvbrc:alice " }))).toMatchObject({
      verb: "admin-add",
      args: { subject: "bvbrc:alice" },
      destructive: false,
      problem: null,
    });
    expect(spec({ kind: "admin-add" }, forms({ adminSubject: "alice" })).problem).toMatch(/issuer:sub/);
    expect(spec({ kind: "admin-remove", subject: "bvbrc:alice" })).toMatchObject({
      verb: "admin-remove",
      args: { subject: "bvbrc:alice" },
      destructive: true,
      problem: null,
    });
  });

  it("sa-create / sa-disable / sa-enable", () => {
    const f = forms({ sa: { subject: "svc-ingest", role: "user", purpose: "  nightly ingest " } });
    expect(spec({ kind: "sa-create" }, f)).toMatchObject({
      verb: "sa-create",
      args: { subject: "svc-ingest", role: "user", purpose: "nightly ingest" },
      destructive: false,
      problem: null,
    });
    expect(spec({ kind: "sa-create" }, forms({ sa: { subject: "svc", role: "admin", purpose: "" } })).args).toEqual({
      subject: "svc",
      role: "admin",
    });
    expect(spec({ kind: "sa-create" }, forms({ sa: { subject: "bvbrc:x", role: "user", purpose: "" } })).problem).toMatch(/no colon/);
    expect(spec({ kind: "sa-create" }, forms({ sa: { subject: "svc", role: "user", purpose: "x".repeat(257) } })).problem).toMatch(/purpose/);
    expect(spec({ kind: "sa-disable", subject: "svc-asm-web" })).toMatchObject({ verb: "sa-disable", args: { subject: "svc-asm-web" }, destructive: true });
    expect(spec({ kind: "sa-enable", subject: "svc-old-batch" })).toMatchObject({ verb: "sa-enable", args: { subject: "svc-old-batch" }, destructive: false });
  });

  it("restart-api: restart with only [api], destructive", () => {
    expect(spec({ kind: "restart-api" })).toMatchObject({ verb: "restart", args: { only: ["api"] }, destructive: true, problem: null });
  });

  it("mirrors the daemon's destructive flags (go/internal/ctl/ops/ops.go)", () => {
    expect(DESTRUCTIVE).toEqual({
      "key-mint": false,
      "key-revoke": true,
      "admin-add": false,
      "admin-remove": true,
      "sa-create": false,
      "sa-disable": true,
      "sa-enable": false,
      "restart-api": true,
    });
  });

  it("key-mint is a secret-bearing op: its value arrives through RevealOnce only", () => {
    expect(SECRET_BEARING_OPS).toContain("key-mint");
    const minted: Job = { ...jobFixture("succeeded"), op: "key-mint" };
    expect(jobDeliversSecrets(minted)).toBe(true);
    for (const op of ["key-revoke", "admin-add", "admin-remove", "sa-create", "sa-disable", "sa-enable"] as const) {
      expect(jobDeliversSecrets({ ...minted, op })).toBe(false);
    }
  });
});

// ---------------------------------------------------------------------------
// The open flows, and the manual-tenant restart gating
// ---------------------------------------------------------------------------

describe("open operations", () => {
  it("mint on a supervised tenant: restart offered, prove only with restart", () => {
    const off = view({ tenant: supervisedCredentialsTenantFixture, active: { kind: "key-mint" } });
    expect(off).toContain("operation: mint a key on demo");
    expect(off).toMatch(/<input type="checkbox" name="restart"(?![^>]*disabled="")/);
    expect(off).toMatch(/<input type="checkbox" name="prove"[^>]*disabled=""/);
    expect(off).not.toContain("supervisor: manual");
    // Preview waits for a valid label.
    expect(off).toMatch(/disabled=""[^>]*>Preview</);

    const on = view({
      tenant: supervisedCredentialsTenantFixture,
      active: { kind: "key-mint" },
      forms: { ...EMPTY_CREDENTIAL_FORMS, mint: { label: "k1", role: "user", tenantString: "", restart: true, prove: false } },
    });
    expect(on).toMatch(/<input type="checkbox" name="prove"(?![^>]*disabled="")/);
    expect(on).toMatch(/<button type="button"(?![^>]*disabled="")[^>]*>Preview</);
  });

  it("on a manual tenant restart is disabled with the reason, and never sent", () => {
    const html = view({ active: { kind: "key-mint" } });
    expect(html).toMatch(/<input type="checkbox" name="restart"[^>]*disabled=""/);
    expect(html).toMatch(/<input type="checkbox" name="prove"[^>]*disabled=""/);
    expect(html).toContain("supervisor: manual");
    // The Restart API button is disabled with the same reason.
    expect(html).toMatch(/disabled=""[^>]*title="This tenant is supervisor: manual[^"]*"[^>]*>Restart API…/);
    expect(restartRefusal("manual")).not.toBeNull();
    expect(restartRefusal("instance")).toBeNull();
    expect(restartRefusal("systemd")).toBeNull();
    // A restart left ticked from a supervised tenant is dropped, not sent.
    expect(mintArgs({ label: "k", role: "user", tenantString: "", restart: true, prove: true }, "manual")).toEqual({ label: "k", role: "user" });
    expect(revokeArgs("k", { restart: true, prove: true }, "manual")).toEqual({ id: "k" });
    expect(credentialOpSpec("demo", { kind: "restart-api" }, EMPTY_CREDENTIAL_FORMS, "manual").problem).toMatch(/manual/);
  });

  it("a destructive flow is titled in red; a revoke names its key", () => {
    const html = view({ tenant: supervisedCredentialsTenantFixture, active: { kind: "key-revoke", id: "asm-ops" } });
    expect(html).toContain("operation: revoke key asm-ops on demo");
    expect(html).toMatch(/text-rust">revoke key asm-ops on demo/);
    const add = view({ active: { kind: "admin-add" } });
    expect(add).not.toMatch(/text-rust">add an admin subject/);
  });

  it("each flow is drawn under its own subsection", () => {
    for (const op of [
      { kind: "admin-add" },
      { kind: "admin-remove", subject: "bvbrc:isingh" },
      { kind: "sa-create" },
      { kind: "sa-disable", subject: "svc-asm-web" },
      { kind: "sa-enable", subject: "svc-old-batch" },
      { kind: "restart-api" },
    ] as CredOp[]) {
      const html = view({ tenant: supervisedCredentialsTenantFixture, active: op });
      expect(count(html, "operation: ")).toBe(1);
    }
  });
});

// ---------------------------------------------------------------------------
// Job result notes
// ---------------------------------------------------------------------------

describe("CredentialResult", () => {
  const withResult = (result: Record<string, unknown> | null): Job =>
    ({ ...jobFixture("succeeded"), op: "key-mint", result }) as unknown as Job;

  it("renders pending_until_restart and effective", () => {
    const pending = renderToStaticMarkup(createElement(CredentialResult, { job: withResult({ effective: false, pending_until_restart: true }) }));
    expect(pending).toContain("pending until restart");
    expect(pending).toContain("not yet effective");
    const live = renderToStaticMarkup(createElement(CredentialResult, { job: withResult({ effective: true }) }));
    expect(live).toContain(">effective<");
    expect(live).not.toContain("pending until restart");
  });

  it("renders a proof: status against expected, fingerprints only", () => {
    const html = renderToStaticMarkup(
      createElement(CredentialResult, {
        job: withResult({
          effective: true,
          proof: {
            minted: { fingerprint: "sha256:abcdef0123456789", status: 200, expected: 200 },
            revoked: { fingerprint: "leaked-key-value-0010", status: 200, expected: 401 },
            "<b>x</b>": { status: 401, expected: 401 },
            broken: "leaked-key-value-0011",
          },
        }),
      }),
    );
    expect(html).toContain("minted");
    expect(html).toContain("200 (expected 200)");
    expect(html).toContain("sha256:abcdef0123456789");
    expect(html).toContain("200 (expected 401)");
    expect(html).toContain("check 3");
    expect(html).toContain("unreadable");
    expect(html).not.toContain("leaked-key-value-0010");
    expect(html).not.toContain("leaked-key-value-0011");
    expect(html).not.toContain("<b>");
  });

  it("renders nothing for a result without credential facts", () => {
    expect(renderToStaticMarkup(createElement(CredentialResult, { job: withResult(null) }))).toBe("");
    expect(renderToStaticMarkup(createElement(CredentialResult, { job: withResult({ key_id: "k" }) }))).toBe("");
  });

  it("shows under the open flow once the job settled", () => {
    const html = view({
      active: { kind: "admin-add" },
      settled: { ...jobFixture("succeeded"), op: "admin-add", result: { subject: "bvbrc:alice", pending_until_restart: true } } as unknown as Job,
    });
    expect(html).toContain('aria-label="credential result"');
    expect(html).toContain("pending until restart");
  });
});

// ---------------------------------------------------------------------------
// The secrets canary, on the Credentials section
// ---------------------------------------------------------------------------

describe("no key value renders from the credential ledger", () => {
  const hostile = () =>
    [
      view({ tenant: hostileCredentialsTenantFixture }),
      view({ tenant: hostileCredentialsTenantFixture, active: { kind: "key-revoke", id: "hostile-key" } }),
      render(
        createElement(TenantSection, { section: "credentials", name: "demo", role: "operator" as CtlRole }),
        seedDemo(hostileCredentialsTenantFixture),
      ),
    ].join("\n");

  it("a member no KeyRecord has (api_key) and a value posing as a fingerprint never render", () => {
    const html = hostile();
    expect(html).not.toContain("leaked-key-value-0009");
    expect(html).not.toContain("leaked-key-value-0008");
    expect(html).not.toMatch(/leaked-key-value-\d{4}/);
  });

  it("free text renders, escaped — never as markup", () => {
    const html = hostile();
    // Not vacuous: the descriptive fields are on screen.
    expect(html).toContain("leaked-label-value-0001");
    expect(html).toContain("leaked-tenant-value-0002");
    expect(html).toContain("leaked-creator-value-0003");
    expect(html).toContain("leaked-purpose-value-0004");
    expect(html).toContain("sha256:feedfacecafebeef");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<b>bold");
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(html).toContain("&lt;script&gt;");
  });

  it("the key state of the hostile rows still reads from the two facts", () => {
    expect(keyState({ effective: true, revoked_at: null }).label).toBe("effective");
    expect(keyState({ effective: false, revoked_at: null }).label).toBe("pending restart");
    expect(keyState({ effective: true, revoked_at: "2026-10-08T00:00:00Z" }).label).toBe("revoke pending");
    expect(keyState({ effective: false, revoked_at: "2026-10-08T00:00:00Z" }).label).toBe("revoked");
  });
});
