import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { hashFor, parseHash, type View } from "./AdminApp";
import { ctlKeys } from "./api/queries";
import type { CreateArgsInput } from "./api/types";
import {
  createArgs,
  CreateArgsSummary,
  CreateTenantWizard,
  CreateTenantWizardView,
  EMPTY_WIZARD,
  firstInvalidStep,
  sortArtifacts,
  stepProblem,
  WIZARD_STEPS,
  type CreateTenantWizardViewProps,
  type WizardForm,
  type WizardStep,
} from "./components/CreateTenantWizard";
import { FleetView } from "./components/FleetView";
import {
  artifactsFixture,
  createArgsFixture,
  doctorFixture,
  fleetFixture,
  hostileArtifactsFixture,
  versionFixture,
} from "./fixtures";
import { RESERVED_TENANT_NAMES } from "./lib/validate";

// PR-G3.3: the create wizard. String renders through the props-only seam
// (`CreateTenantWizardView`) at every step; the stateful wrapper is rendered
// at its entry point; the request is asserted through the real ops.ts with
// only `fetch` faked.

function render(node: ReactElement, seed?: (qc: QueryClient) => void): string {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, retryOnMount: false, staleTime: Infinity } },
  });
  seed?.(qc);
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

const noop = () => {};
const FLEET_NAMES = fleetFixture.tenants.map((t) => t.name);
const CTX = { fleetNames: FLEET_NAMES, artifacts: artifactsFixture.artifacts };

/** The wizard form that produces `createArgsFixture`. */
const FULL_FORM: WizardForm = {
  name: "lab-west",
  artifactId: "v1.6.4-12-gabc1234",
  identity: "bvbrc",
  adminSubjects: ["bvbrc:alice@patricbrc.org", "bvbrc:bob@patricbrc.org"],
  postgres: "local",
  esHeap: "2g",
  templateFrom: "dev",
  settings: [
    { key: "CHUNK_MAX_TOKENS", value: "512" },
    { key: "LOG_LEVEL", value: "debug" },
  ],
  keys: [
    { label: "ops", role: "admin" },
    { label: "reader", role: "user" },
  ],
  serviceAccounts: [{ subject: "gowe", role: "user", purpose: "workflows" }],
  uiMode: "external",
  start: false,
  gateway: false,
  supervisor: "instance",
};

const MINIMAL_FORM: WizardForm = { ...EMPTY_WIZARD, name: "lab-west", artifactId: "v1.6.2" };

function view(step: WizardStep, form: WizardForm, extra: Partial<CreateTenantWizardViewProps> = {}): string {
  return render(
    createElement(CreateTenantWizardView, {
      step,
      form,
      artifacts: artifactsFixture.artifacts,
      fleetNames: FLEET_NAMES,
      onChange: noop,
      onNext: noop,
      onBack: noop,
      onCancel: noop,
      ...extra,
    }),
  );
}

// ---------------------------------------------------------------------------
// The args a create sends
// ---------------------------------------------------------------------------

describe("createArgs", () => {
  it("maps every step onto CreateArgs", () => {
    expect(createArgs(FULL_FORM)).toEqual(createArgsFixture);
  });

  it("omits every default, and the supervisor unless chosen", () => {
    expect(createArgs(MINIMAL_FORM)).toEqual({
      name: "lab-west",
      artifact_id: "v1.6.2",
      identity_provider: "bvbrc",
    });
    expect(createArgs({ ...MINIMAL_FORM, identity: "none" })).toEqual({ name: "lab-west", artifact_id: "v1.6.2" });
    expect(createArgs(MINIMAL_FORM)).not.toHaveProperty("supervisor");
  });

  it("drops admin subjects typed under bvbrc once the provider is none", () => {
    const a = createArgs({ ...FULL_FORM, identity: "none" });
    expect(a).not.toHaveProperty("admin_subjects");
    expect(a).not.toHaveProperty("identity_provider");
  });
});

/**
 * A hand check of `args` against create_request.json's CreateArgs: no member
 * the schema does not define (`additionalProperties: false`), every required
 * member, every pattern / enum / type / bound. No validator dependency — the
 * schema uses a small subset of JSON Schema and this covers it.
 */
function schemaProblems(args: Record<string, unknown>): string[] {
  const schema = JSON.parse(
    readFileSync(resolve(__dirname, "../../../contracts/ctl/schemas/create_request.json"), "utf8"),
  );
  const def = schema.$defs.CreateArgs;
  const out: string[] = [];
  for (const r of def.required as string[]) if (!(r in args)) out.push(`missing ${r}`);
  // CreateArgs' `$comment` (F4 #713): artifact_id OR image — the op enforces
  // it with a 422 rather than an anyOf the type generator would flatten.
  if (!("artifact_id" in args) && !("image" in args)) out.push("missing artifact_id or image");
  const checkScalar = (path: string, v: unknown, s: Record<string, unknown>) => {
    if (s.type === "string") {
      if (typeof v !== "string") return out.push(`${path} not a string`);
      if (s.pattern && !new RegExp(s.pattern as string).test(v)) out.push(`${path} !~ ${s.pattern}`);
      if (s.enum && !(s.enum as string[]).includes(v)) out.push(`${path} not in enum`);
      if (s.maxLength !== undefined && v.length > (s.maxLength as number)) out.push(`${path} too long`);
    } else if (s.type === "boolean" && typeof v !== "boolean") out.push(`${path} not a boolean`);
  };
  for (const [k, v] of Object.entries(args)) {
    const s = def.properties[k];
    if (!s) {
      out.push(`unknown member ${k}`);
      continue;
    }
    if (s.type === "array") {
      if (!Array.isArray(v)) {
        out.push(`${k} not an array`);
        continue;
      }
      if (s.uniqueItems && new Set(v.map((x) => JSON.stringify(x))).size !== v.length) out.push(`${k} not unique`);
      v.forEach((item, i) => {
        if (s.items.type === "object") {
          for (const r of s.items.required) if (!(r in item)) out.push(`${k}[${i}] missing ${r}`);
          for (const [ik, iv] of Object.entries(item as Record<string, unknown>)) {
            if (!s.items.properties[ik]) out.push(`${k}[${i}] unknown ${ik}`);
            else checkScalar(`${k}[${i}].${ik}`, iv, s.items.properties[ik]);
          }
        } else checkScalar(`${k}[${i}]`, item, s.items);
      });
    } else if (s.type === "object") {
      for (const [sk, sv] of Object.entries(v as Record<string, unknown>)) {
        if (!new RegExp(schema.$defs.PublicSettingKey.pattern).test(sk)) out.push(`settings key ${sk}`);
        if (typeof sv !== "string") out.push(`settings.${sk} not a string`);
      }
    } else checkScalar(k, v, s);
  }
  return out;
}

describe("the assembled args match create_request.json", () => {
  it("the full fixture and the minimal create are schema-valid", () => {
    expect(schemaProblems(createArgsFixture as Record<string, unknown>)).toEqual([]);
    expect(schemaProblems(createArgs(FULL_FORM) as Record<string, unknown>)).toEqual([]);
    expect(schemaProblems(createArgs(MINIMAL_FORM) as Record<string, unknown>)).toEqual([]);
  });

  it("the checker itself catches a wrong member name (the brief's `artifact`, `identity`, `set`, `no_start`)", () => {
    const wrong = { name: "x", artifact: "v1", identity: "bvbrc", set: {}, no_start: true };
    const p = schemaProblems(wrong);
    expect(p).toContain("missing artifact_id or image");
    expect(p).toContain("unknown member artifact");
    expect(p).toContain("unknown member identity");
    expect(p).toContain("unknown member set");
    expect(p).toContain("unknown member no_start");
  });
});

// ---------------------------------------------------------------------------
// The request on the wire: through the real ops.ts, `fetch` faked
// ---------------------------------------------------------------------------

describe("createTenant sends the fixture as args", () => {
  const CTL_KEY = "ctl-key-0123456789abcdef-SECRET";
  let local: Map<string, string>;

  beforeEach(() => {
    vi.resetModules();
    local = new Map();
    const store = (m: Map<string, string>) =>
      ({
        getItem: (k: string) => m.get(k) ?? null,
        setItem: (k: string, v: string) => void m.set(k, v),
        removeItem: (k: string) => void m.delete(k),
        clear: () => m.clear(),
        key: () => null,
        length: 0,
      }) as unknown as Storage;
    vi.stubGlobal("sessionStorage", store(new Map()));
    vi.stubGlobal("localStorage", store(local));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("POSTs /v1/tenants with args equal to the fixture and the key in the body only", async () => {
    const plan = {
      plan_hash: "sha256:" + "0".repeat(64),
      op: "create",
      tenant: "lab-west",
      registry_generation: 7,
      schema_version: 1,
      doctor: { status: "green", findings: [] },
      requires_confirm: false,
      confirm_value: null,
      steps: [],
      warnings: [],
    };
    const json = (status: number, body: unknown) =>
      new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        json(201, {
          session_id: "b".repeat(64),
          principal: "key:operator",
          role: "operator",
          expires_at: "2026-10-08T23:00:00Z",
          reads_only: true,
        }),
      )
      .mockResolvedValueOnce(json(200, plan));
    vi.stubGlobal("fetch", fetchMock);
    const session = await import("./auth/session");
    await session.signInWithApiKey("sign-in-key-0000");
    const { createRun } = await import("./components/CreateTenantWizard");

    const out = await createRun(createArgs(FULL_FORM))({
      ctlKey: CTL_KEY,
      dryRun: true,
      idempotencyKey: "ui-create-00000000-0000-4000-8000-000000000000",
    });
    expect(out.kind).toBe("plan");

    const [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/v1\/tenants$/);
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.args).toEqual(createArgsFixture);
    expect(body.ctl_api_key).toBe(CTL_KEY);
    expect(body.dry_run).toBe(true);
    expect(JSON.stringify(init.headers)).not.toContain(CTL_KEY);
    expect([...local.values()].join()).not.toContain(CTL_KEY);
  });
});

// ---------------------------------------------------------------------------
// Each step validates before Next
// ---------------------------------------------------------------------------

describe("step validation", () => {
  it("a complete form passes every step", () => {
    for (const s of WIZARD_STEPS) expect(stepProblem(s, FULL_FORM, CTX), s).toBeNull();
    expect(firstInvalidStep(FULL_FORM, CTX)).toBeNull();
  });

  it("name: grammar, reserved, sandbox, existing", () => {
    expect(stepProblem("name", EMPTY_WIZARD, CTX)).toMatch(/empty/);
    expect(stepProblem("name", { ...FULL_FORM, name: "Lab" }, CTX)).toMatch(/Lowercase/);
    for (const r of RESERVED_TENANT_NAMES) {
      expect(stepProblem("name", { ...FULL_FORM, name: r }, CTX), r).toMatch(/reserved/);
    }
    expect(stepProblem("name", { ...FULL_FORM, name: "ctltest-a" }, CTX)).toMatch(/sandbox/);
    expect(stepProblem("name", { ...FULL_FORM, name: "dev" }, CTX)).toMatch(/already exists/);
  });

  it("code: an artifact must be chosen and prepared", () => {
    expect(stepProblem("code", { ...FULL_FORM, artifactId: "" }, CTX)).toMatch(/Choose/);
    expect(stepProblem("code", { ...FULL_FORM, artifactId: "v9.9.9" }, CTX)).toMatch(/not a prepared/);
  });

  it("identity: subjects validated and refused with none", () => {
    expect(stepProblem("identity", { ...FULL_FORM, adminSubjects: ["alice"] }, CTX)).toMatch(/issuer:subject/);
    expect(stepProblem("identity", { ...FULL_FORM, adminSubjects: ["globus:alice"] }, CTX)).toMatch(/issued by/);
    expect(stepProblem("identity", { ...FULL_FORM, adminSubjects: [""] }, CTX)).toMatch(/issuer:subject/);
    // With none, the typed subjects are kept in the form but not sent.
    expect(stepProblem("identity", { ...FULL_FORM, identity: "none" }, CTX)).toMatch(/identity provider/);
    expect(stepProblem("identity", { ...FULL_FORM, identity: "none", adminSubjects: [] }, CTX)).toBeNull();
  });

  it("stores: the heap", () => {
    expect(stepProblem("stores", { ...FULL_FORM, esHeap: "1.5g" }, CTX)).toMatch(/size in m or g/);
  });

  it("settings: template must be a tenant, rows validated, no duplicates; secret names only warn", () => {
    expect(stepProblem("settings", { ...FULL_FORM, templateFrom: "nope" }, CTX)).toMatch(/not a tenant/);
    expect(stepProblem("settings", { ...FULL_FORM, settings: [{ key: "bad", value: "" }] }, CTX)).toMatch(
      /not a setting name/,
    );
    expect(
      stepProblem(
        "settings",
        {
          ...FULL_FORM,
          settings: [
            { key: "A", value: "1" },
            { key: "A", value: "2" },
          ],
        },
        CTX,
      ),
    ).toMatch(/twice/);
    expect(stepProblem("settings", { ...FULL_FORM, settings: [{ key: "GOWE_TOKEN", value: "x" }] }, CTX)).toBeNull();
  });

  it("credentials: labels, subjects, duplicates, the bootstrap admin", () => {
    expect(stepProblem("credentials", { ...FULL_FORM, keys: [{ label: "Ops", role: "admin" }] }, CTX)).toMatch(
      /Key label/,
    );
    expect(
      stepProblem(
        "credentials",
        {
          ...FULL_FORM,
          keys: [
            { label: "ops", role: "admin" },
            { label: "ops", role: "user" },
          ],
        },
        CTX,
      ),
    ).toMatch(/twice/);
    expect(
      stepProblem("credentials", { ...FULL_FORM, keys: [{ label: "bootstrap-admin", role: "user" }] }, CTX),
    ).toMatch(/must be admin/);
    expect(
      stepProblem(
        "credentials",
        { ...FULL_FORM, serviceAccounts: [{ subject: "-gowe", role: "user", purpose: "" }] },
        CTX,
      ),
    ).toMatch(/Service account/);
    expect(
      stepProblem(
        "credentials",
        { ...FULL_FORM, serviceAccounts: [{ subject: "gowe", role: "user", purpose: "x".repeat(257) }] },
        CTX,
      ),
    ).toMatch(/256/);
  });

  it("options: dev UI cannot run under the instance supervisor", () => {
    expect(stepProblem("options", { ...FULL_FORM, uiMode: "dev", supervisor: "instance" }, CTX)).toMatch(/dev/);
    expect(stepProblem("options", { ...FULL_FORM, uiMode: "dev", supervisor: "systemd" }, CTX)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The view at each step
// ---------------------------------------------------------------------------

describe("CreateTenantWizardView", () => {
  it("renders every step with the stepper and the step's fields", () => {
    const expects: Record<WizardStep, string[]> = {
      name: ['name="name"', 'value="lab-west"'],
      code: ['name="artifact"', "v1.6.4-12-gabc1234", "abc1234def56", "hackathon", "schema compatible"],
      identity: ['name="identity"', "bvbrc:alice@patricbrc.org", "Add subject"],
      stores: ['name="postgres"', 'name="es_heap"', 'value="2g"'],
      settings: ['name="template_from"', "CHUNK_MAX_TOKENS", "Add setting"],
      credentials: ["bootstrap-admin", 'value="ops"', 'value="gowe"', "Add service account"],
      options: ['name="ui_mode"', 'name="supervisor"', 'name="start"', 'name="gateway"'],
      review: ['aria-label="create arguments"', "lab-west", "v1.6.4-12-gabc1234", "workflows"],
    };
    WIZARD_STEPS.forEach((s, i) => {
      const html = view(s, FULL_FORM);
      expect(html).toContain(`step ${i + 1} of 8`);
      expect(html).toContain('aria-current="step"');
      for (const e of expects[s]) expect(html, `${s}: ${e}`).toContain(e);
      // Back on every step but the first; Next on every step but Review.
      expect(html.includes(">Back<"), s).toBe(i > 0);
      expect(html.includes(">Next<"), s).toBe(s !== "review");
      expect(html).not.toContain('role="alert"');
    });
  });

  it("shows a typed name's problem live, and refuses a reserved name with the reason", () => {
    const html = view("name", { ...EMPTY_WIZARD, name: "gowe" });
    expect(html).toContain('role="alert"');
    expect(html).toContain("is reserved");
    expect(html).toContain('aria-invalid="true"');
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Next</);
    // An untouched first field does not open on a red line.
    expect(view("name", EMPTY_WIZARD)).not.toContain('role="alert"');
  });

  it("shows other steps' problems once Next was tried", () => {
    const form = { ...FULL_FORM, esHeap: "lots" };
    expect(view("stores", form)).not.toContain('role="alert"');
    const tried = view("stores", form, { showProblem: true });
    expect(tried).toContain('role="alert"');
    expect(tried).toContain("size in m or g");
  });

  it("lists artifacts newest first, with the compatibility chip", () => {
    const sorted = sortArtifacts([...artifactsFixture.artifacts].reverse());
    expect(sorted.map((a) => a.id)).toEqual(["v1.6.4-12-gabc1234", "v1.6.2"]);
    const html = view("code", MINIMAL_FORM);
    expect(html.indexOf("v1.6.4-12-gabc1234")).toBeLessThan(html.indexOf("v1.6.2"));
    expect(html).toMatch(/value="v1.6.2"[^>]*checked=""|checked=""[^>]*value="v1.6.2"/);
  });

  it("says when nothing is prepared and while loading", () => {
    expect(view("code", MINIMAL_FORM, { artifacts: [] })).toContain("No artifact is prepared");
    expect(view("code", MINIMAL_FORM, { artifacts: null })).toContain("Loading the prepared artifacts");
  });

  it("renders a hostile artifact list escaped and redacted", () => {
    const html = view("code", MINIMAL_FORM, { artifacts: hostileArtifactsFixture.artifacts });
    expect(html).toContain("leaked-tag-&lt;img src=x onerror=alert(1)&gt;");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("leaked-token-value-0301");
    expect(html).toContain("GOWE_TOKEN=");
    expect(html).toContain("schema unchecked");
  });

  it("warns on a secret-shaped setting without blocking", () => {
    const form = { ...FULL_FORM, settings: [{ key: "GOWE_TOKEN", value: "x" }] };
    const html = view("settings", form, { showProblem: true });
    expect(html).toContain("looks like a secret");
    expect(html).not.toContain('role="alert"');
  });

  it("hides the admin subject editor for none and offers to clear what was typed", () => {
    const html = view("identity", { ...FULL_FORM, identity: "none" });
    expect(html).not.toContain("Add subject");
    expect(html).toContain("will not be sent with none");
  });

  it("Review: the summary and the flow slot; an invalid earlier step blocks the flow", () => {
    const flow = createElement("div", { "data-testid": "flow" }, "FLOW");
    const ok = view("review", FULL_FORM, { flow });
    expect(ok).toContain("FLOW");
    const bad = view("review", { ...FULL_FORM, name: "admin" }, { flow });
    expect(bad).not.toContain("FLOW");
    expect(bad).toContain("Name:");
    expect(bad).toContain("reserved");
  });

  it("locked (a job was accepted): no Back, no Cancel, the form frozen", () => {
    const html = view("review", FULL_FORM, { locked: true });
    expect(html).not.toContain(">Back<");
    expect(html).not.toContain(">Cancel<");
    expect(html).toContain("<fieldset disabled");
  });

  it("offers to open the tenant only after the job succeeded", () => {
    const job = { id: "01JAAAAAAAAAAAAAAAAAAAAAAA", tenant: "lab-west", state: "succeeded" } as never;
    const html = view("review", FULL_FORM, { locked: true, settled: job, onOpenTenant: noop });
    expect(html).toContain("Open lab-west");
    expect(html).toContain("shown once");
    const failed = { ...(job as object), state: "failed" } as never;
    expect(view("review", FULL_FORM, { settled: failed, onOpenTenant: noop })).not.toContain("Open lab-west");
  });
});

describe("CreateArgsSummary", () => {
  it("labels defaults and the deployment's supervisor", () => {
    const html = render(createElement(CreateArgsSummary, { args: createArgs(MINIMAL_FORM) as CreateArgsInput }));
    expect(html).toContain("sqlite");
    expect(html).toContain("(default)");
    expect(html).toContain("not sent");
    expect(html).toContain("bootstrap-admin:admin");
  });
});

// ---------------------------------------------------------------------------
// Roles and routing
// ---------------------------------------------------------------------------

describe("operator only", () => {
  const seed = (qc: QueryClient) => {
    qc.setQueryData(ctlKeys.fleet(), fleetFixture);
    qc.setQueryData(ctlKeys.doctor(), doctorFixture);
    qc.setQueryData(ctlKeys.version(), versionFixture);
    qc.setQueryData(ctlKeys.artifacts(), artifactsFixture);
  };

  it("a viewer at #/create gets the 403 panel, no form", () => {
    const html = render(
      createElement(CreateTenantWizard, { role: "viewer", onDone: noop, onCancel: noop }),
      seed,
    );
    expect(html).toContain("Operator credential required");
    expect(html).not.toContain('name="name"');
  });

  it("an operator gets the wizard at step 1", () => {
    const html = render(
      createElement(CreateTenantWizard, { role: "operator", onDone: noop, onCancel: noop }),
      seed,
    );
    expect(html).toContain("Create tenant");
    expect(html).toContain('name="name"');
    expect(html).toContain("step 1 of 8");
  });

  it("the operator wizard at Review mounts the OpFlow for create", () => {
    const html = render(
      createElement(CreateTenantWizard, {
        role: "operator",
        onDone: noop,
        onCancel: noop,
        initialForm: FULL_FORM,
        initialStep: "review",
      }),
      seed,
    );
    expect(html).toContain('aria-label="operation: create lab-west"');
    expect(html).toContain("Plan the create");
    expect(html).not.toContain('type="password"');
  });

  it("FleetView: Create tenant for an operator, absent for a viewer", () => {
    const op = render(createElement(FleetView, { onSelectTenant: noop, role: "operator", onCreate: noop }), seed);
    expect(op).toContain('href="#/create"');
    expect(op).toContain("Create tenant");
    const viewer = render(createElement(FleetView, { onSelectTenant: noop, role: "viewer", onCreate: noop }), seed);
    expect(viewer).not.toContain("Create tenant");
    // No role given = the safe side.
    expect(render(createElement(FleetView, { onSelectTenant: noop }), seed)).not.toContain("Create tenant");
  });

  it("#/create round-trips through the hash", () => {
    expect(parseHash("#/create")).toEqual({ kind: "create" });
    const v: View = { kind: "create" };
    expect(hashFor(v)).toBe("#/create");
    expect(parseHash(hashFor(v))).toEqual(v);
    expect(parseHash("#/create/x")).toEqual({ kind: "fleet" });
  });
});

// ---------------------------------------------------------------------------
// PR-F F6: the Code step's server-image option (`CreateArgs.image`)
// ---------------------------------------------------------------------------

import { createImageProblem } from "./lib/validate";
import { IMAGE_COMMIT_167, imageArtifactsFixture } from "./fixtures";

describe("the Code step's server image option", () => {
  const B1 = "ragstack-server-v1.6.6-b1.sif";
  const N1 = "ragstack-server-v1.6.7-b1.sif";
  const ICTX = {
    fleetNames: FLEET_NAMES,
    artifacts: imageArtifactsFixture.artifacts,
    serverImages: imageArtifactsFixture.server_images,
  };
  const IMAGE_FORM: WizardForm = { ...MINIMAL_FORM, codeSource: "image", image: B1, artifactId: "v1.6.6" };
  const imageView = (form: WizardForm) =>
    view("code", form, {
      artifacts: imageArtifactsFixture.artifacts,
      serverImages: imageArtifactsFixture.server_images,
    });

  it("offers both sources; the artifact picker stays the default", () => {
    const html = view("code", MINIMAL_FORM);
    expect(html).toMatch(/<input[^>]*name="code_source"[^>]*checked=""[^>]*value="artifact"/);
    expect(html).toContain('name="code_source" value="image"');
    expect(html).toContain('name="artifact"');
    expect(html).not.toContain('name="create-image"');
  });

  it("image source: the images, and the artifacts at the chosen image's commit only", () => {
    const html = imageView(IMAGE_FORM);
    expect(html).toMatch(/<input[^>]*name="code_source"[^>]*checked=""[^>]*value="image"/);
    expect(html).toContain('name="create-image"');
    expect(html).toContain("required: the UI mode is static");
    expect(html).toMatch(/<input[^>]*name="create-image-artifact"[^>]*checked=""[^>]*value="v1\.6\.6"/);
    expect(html).not.toContain('value="v1.6.2"');
    expect(html).not.toContain('name="artifact"');
  });

  it("image source with no artifact at its commit: the empty state names the commit", () => {
    const html = imageView({ ...IMAGE_FORM, image: N1, artifactId: "" });
    expect(html).toContain("No prepared artifact is at commit");
    expect(html).toContain(IMAGE_COMMIT_167.slice(0, 12));
  });

  it("createArgs sends image (+ the artifact when chosen) and no stray artifact_id", () => {
    expect(createArgs(IMAGE_FORM)).toEqual({
      name: "lab-west",
      image: B1,
      artifact_id: "v1.6.6",
      identity_provider: "bvbrc",
    });
    const external = createArgs({ ...IMAGE_FORM, artifactId: "", uiMode: "external" });
    expect(external).toEqual({ name: "lab-west", image: B1, identity_provider: "bvbrc", ui_mode: "external" });
    expect(external).not.toHaveProperty("artifact_id");
    expect(schemaProblems(createArgs(IMAGE_FORM) as Record<string, unknown>)).toEqual([]);
    expect(schemaProblems(external as Record<string, unknown>)).toEqual([]);
    // The artifact source never sends `image`.
    expect(createArgs({ ...MINIMAL_FORM, image: B1 })).not.toHaveProperty("image");
  });

  it("validates the Code step and the image-only Options rules", () => {
    expect(stepProblem("code", IMAGE_FORM, ICTX)).toBeNull();
    expect(stepProblem("code", { ...IMAGE_FORM, image: "" }, ICTX)).toMatch(/Choose a prepared server image/);
    expect(stepProblem("code", { ...IMAGE_FORM, artifactId: "" }, ICTX)).toMatch(/static UI/);
    expect(stepProblem("code", { ...IMAGE_FORM, artifactId: "", uiMode: "external" }, ICTX)).toBeNull();
    expect(stepProblem("code", { ...IMAGE_FORM, artifactId: "v1.6.2" }, ICTX)).toMatch(/different code/);
    expect(stepProblem("options", { ...IMAGE_FORM, supervisor: "systemd" }, ICTX)).toMatch(/instance/);
    expect(stepProblem("options", { ...IMAGE_FORM, uiMode: "dev" }, ICTX)).toMatch(/dev/);
    expect(stepProblem("options", { ...IMAGE_FORM, supervisor: "instance" }, ICTX)).toBeNull();
    // The same rule the Code step applies.
    expect(createImageProblem({ image: B1, artifactId: "v1.6.6", uiMode: "static" }, ICTX.serverImages, ICTX.artifacts)).toBeNull();
    // Review re-checks: a static UI chosen after an artifact-less image create is caught.
    expect(firstInvalidStep({ ...IMAGE_FORM, artifactId: "" }, ICTX)?.step).toBe("code");
  });

  it("the review summary shows the image and an absent artifact as —", () => {
    const html = render(
      createElement(CreateArgsSummary, { args: createArgs({ ...IMAGE_FORM, artifactId: "", uiMode: "external" }) }),
    );
    expect(html).toContain(B1);
    expect(html).toMatch(/artifact_id<\/th><td[^>]*><span class="text-dim">—<\/span>/);
  });

  it("no rendered field name matches *_key or *password*", () => {
    const names = [...imageView(IMAGE_FORM).matchAll(/name="([^"]+)"/g)].map((m) => m[1]);
    expect(names.length).toBeGreaterThan(3);
    for (const n of names) expect(n).not.toMatch(/_key$|password/i);
  });
});

describe("createTenant sends the image args", () => {
  beforeEach(() => {
    vi.resetModules();
    const store = () =>
      ({ getItem: () => null, setItem: () => {}, removeItem: () => {}, clear: () => {}, key: () => null, length: 0 }) as unknown as Storage;
    vi.stubGlobal("sessionStorage", store());
    vi.stubGlobal("localStorage", store());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("POSTs /v1/tenants with image + artifact_id", async () => {
    const json = (status: number, body: unknown) =>
      new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        json(201, { session_id: "b".repeat(64), principal: "key:operator", role: "operator", expires_at: "2026-10-09T23:00:00Z", reads_only: true }),
      )
      .mockResolvedValueOnce(
        json(200, {
          plan_hash: "sha256:" + "0".repeat(64),
          op: "create",
          tenant: "lab-west",
          registry_generation: 7,
          schema_version: 1,
          doctor: { status: "green", findings: [] },
          requires_confirm: false,
          confirm_value: null,
          steps: [],
          warnings: [],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const session = await import("./auth/session");
    await session.signInWithApiKey("sign-in-key-0000");
    const { createRun } = await import("./components/CreateTenantWizard");
    const form: WizardForm = {
      ...MINIMAL_FORM,
      codeSource: "image",
      image: "ragstack-server-v1.6.6-b1.sif",
      artifactId: "v1.6.6",
      supervisor: "instance",
    };
    await createRun(createArgs(form))({
      ctlKey: "ctl-key-0123456789abcdef-SECRET",
      dryRun: true,
      idempotencyKey: "ui-create-00000000-0000-4000-8000-000000000000",
    });
    const [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/v1\/tenants$/);
    expect(JSON.parse(init.body as string).args).toEqual({
      name: "lab-west",
      image: "ragstack-server-v1.6.6-b1.sif",
      artifact_id: "v1.6.6",
      identity_provider: "bvbrc",
      supervisor: "instance",
    });
  });
});
