import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ctlKeys } from "./api/queries";
import type { CtlFleet, CtlTenant } from "./api/types";
import { FleetView, imageBuild } from "./components/FleetView";
import { TenantActions } from "./components/TenantActions";
import { sectionsFor, TenantSection, TenantView } from "./components/TenantView";
import {
  EMPTY_UPGRADE,
  updateCodeArgs,
  upgradeProblem,
  UpgradeFields,
  UpgradeResult,
  upgradeVersionOf,
  upgradeTenant,
  type UpgradeFieldsProps,
  type UpgradeForm,
} from "./components/UpgradeAction";
import {
  activeManagedTenantFixture,
  doctorFixture,
  fleetFixture,
  hostileImageArtifactsFixture,
  IMAGE_COMMIT_166,
  IMAGE_COMMIT_167,
  imageArtifactsFixture,
  imageExternalUiTenantFixture,
  imageTenantFixture,
  imageTenantViewerFixture,
  jobFixture,
  stoppedTenantFixture,
  upgradeJobFixture,
  upgradeJobLegacyFixture,
  versionFixture,
  worktreeManagedTenantFixture,
} from "./fixtures";

// PR-F F6: the Upgrade action (`update-code`), image mode in TenantView's
// Overview and the fleet table. String renders through the props-only seams
// (`UpgradeFields`, `UpgradeResult`); the request asserted through the real
// ops.ts with only `fetch` faked.

function render(node: ReactElement, seed?: (qc: QueryClient) => void): string {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, retryOnMount: false, staleTime: Infinity } },
  });
  seed?.(qc);
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

const noop = () => {};
const B1 = "ragstack-server-v1.6.6-b1.sif";
const B2 = "ragstack-server-v1.6.6-b2.sif";
const N1 = "ragstack-server-v1.6.7-b1.sif";
const IMAGES = imageArtifactsFixture.server_images;
const ARTS = imageArtifactsFixture.artifacts;

function fields(tenant: CtlTenant, form: UpgradeForm, extra: Partial<UpgradeFieldsProps> = {}): string {
  return render(
    createElement(UpgradeFields, {
      form,
      tenant: upgradeTenant(tenant),
      images: IMAGES,
      artifacts: ARTS,
      onChange: noop,
      ...extra,
    }),
  );
}

/** The `<input>` carrying `value="<v>"` in a radio group. */
function radio(html: string, name: string, value: string): string {
  const m = new RegExp(`<input[^>]*name="${name}"[^>]*value="${value.replace(/[.+]/g, "\\$&")}"[^>]*>`).exec(html);
  if (!m) throw new Error(`no ${name}=${value} radio`);
  return m[0];
}

const seed = (tenant: CtlTenant) => (qc: QueryClient) => {
  qc.setQueryData(ctlKeys.tenant("hackathon"), tenant);
  qc.setQueryData(ctlKeys.artifacts(), imageArtifactsFixture);
};

// ---------------------------------------------------------------------------
// The seams, per state
// ---------------------------------------------------------------------------

describe("UpgradeFields", () => {
  it("worktree tenant: says it runs from the worktree and will be migrated; no image is current", () => {
    const html = fields(worktreeManagedTenantFixture, EMPTY_UPGRADE);
    expect(html).toContain("worktree v1.6.6");
    expect(html).toContain("migrates it to image mode");
    for (const n of [B1, B2, N1]) expect(radio(html, "upgrade-image", n)).not.toContain("disabled");
    expect(html).not.toContain(">current<");
    // Static UI: Rebuild UI defaults on, and the artifact picker asks for an image first.
    expect(html).toMatch(/<input type="checkbox" name="rebuild_ui" checked=""/);
    expect(html).toContain("Choose an image first");
  });

  it("image tenant, rebuild on (the static default): the current image is marked and selectable", () => {
    const html = fields(imageTenantFixture, EMPTY_UPGRADE);
    expect(html).toContain(`image ${B1}`);
    expect(html).toContain(">current<");
    expect(radio(html, "upgrade-image", B1)).not.toContain("disabled");
    expect(html).not.toContain("migrates it");
  });

  it("image tenant, rebuild off: the current image is disabled (the server refuses a no-op)", () => {
    const html = fields(imageTenantFixture, { ...EMPTY_UPGRADE, rebuildUi: false });
    expect(radio(html, "upgrade-image", B1)).toContain('disabled=""');
    expect(radio(html, "upgrade-image", B2)).not.toContain("disabled");
    expect(html).not.toContain('name="upgrade-artifact"');
  });

  it("rebuild on: the artifact picker lists only artifacts at the chosen image's commit", () => {
    const html = fields(imageTenantFixture, { ...EMPTY_UPGRADE, image: B2 });
    expect(radio(html, "upgrade-artifact", "v1.6.6")).toBeTruthy();
    expect(html).not.toContain('value="v1.6.2"');
    expect(html).not.toContain('value="v1.6.4-12-gabc1234"');
  });

  it("rebuild on, no artifact at the image's commit: the empty state names the commit", () => {
    const html = fields(imageTenantFixture, { ...EMPTY_UPGRADE, image: N1 });
    expect(html).toContain("No prepared artifact is at commit");
    expect(html).toContain(IMAGE_COMMIT_167.slice(0, 12));
    expect(html).toContain("fleet artifact prepare --tag v1.6.7");
    expect(html).not.toContain('name="upgrade-artifact"');
  });

  it("a non-static UI cannot be rebuilt: the checkbox is off and disabled", () => {
    const html = fields(imageExternalUiTenantFixture, EMPTY_UPGRADE);
    expect(html).toMatch(/<input type="checkbox" name="rebuild_ui" disabled=""/);
    expect(html).not.toMatch(/name="rebuild_ui" checked=""/);
    expect(html).toContain("this tenant&#x27;s UI is external");
  });

  it("loading: no registry row yet, no images yet", () => {
    expect(fields(imageTenantViewerFixture, EMPTY_UPGRADE)).toContain("Loading the tenant");
    expect(fields(imageTenantFixture, EMPTY_UPGRADE, { images: null, artifacts: null })).toContain(
      "Loading the prepared server images",
    );
  });
});

describe("TenantActions: the upgrade tab", () => {
  it("an operator sees the upgrade tab; a viewer never sees the Actions section at all", () => {
    const op = render(createElement(TenantSection, { section: "actions", name: "hackathon", role: "operator" }), seed(imageTenantFixture));
    expect(op).toContain(">upgrade</button>");
    expect(sectionsFor("viewer").map((s) => s.id)).not.toContain("actions");
    const viewer = render(createElement(TenantView, { name: "hackathon", role: "viewer", onBack: noop }), seed(imageTenantViewerFixture));
    expect(viewer).not.toContain(">upgrade<");
    expect(viewer).not.toContain("Preview");
    const reached = render(createElement(TenantSection, { section: "actions", name: "hackathon", role: "viewer" }), seed(imageTenantViewerFixture));
    expect(reached).toContain("Operator credential required.");
    expect(reached).not.toContain("upgrade");
  });

  it("the upgrade form, destructive, with Preview held back until an image is chosen", () => {
    const html = render(createElement(TenantActions, { name: "hackathon", initialVerb: "upgrade" }), seed(imageTenantFixture));
    expect(html).toContain("upgrade hackathon");
    expect(html).toContain("text-rust"); // the title is the destructive one
    expect(html).toMatch(/<button type="button" disabled=""[^>]*>Preview<\/button>/);
    expect(html).toContain("Choose a prepared server image.");
    for (const n of [B1, B2, N1]) expect(html).toContain(n);
  });
});

// ---------------------------------------------------------------------------
// Args and the client-side rules
// ---------------------------------------------------------------------------

describe("updateCodeArgs", () => {
  it("non-static UI: image only (rebuild_ui's default is false there)", () => {
    expect(updateCodeArgs({ ...EMPTY_UPGRADE, image: B2 }, "external")).toEqual({ image: B2 });
  });
  it("static UI, rebuild (the default): image + artifact, rebuild_ui omitted", () => {
    expect(updateCodeArgs({ ...EMPTY_UPGRADE, image: B2, artifactId: "v1.6.6" }, "static")).toEqual({
      image: B2,
      artifact_id: "v1.6.6",
    });
    expect(updateCodeArgs({ image: B2, rebuildUi: true, artifactId: "v1.6.6" }, "static")).toEqual({
      image: B2,
      artifact_id: "v1.6.6",
    });
  });
  it("static UI, rebuild off: rebuild_ui false, never an artifact", () => {
    expect(updateCodeArgs({ image: B2, rebuildUi: false, artifactId: "v1.6.6" }, "static")).toEqual({
      image: B2,
      rebuild_ui: false,
    });
  });
});

describe("upgradeProblem: the planner's tenant preconditions, then the args", () => {
  const t = (x: CtlTenant) => upgradeTenant(x);
  it("refuses a stopped tenant, a non-instance one and a withheld row", () => {
    expect(upgradeProblem({ ...EMPTY_UPGRADE, image: B2, rebuildUi: false }, t(stoppedTenantFixture), IMAGES, ARTS)).toMatch(/not active/);
    const systemd: CtlTenant = { ...activeManagedTenantFixture, summary: { ...activeManagedTenantFixture.summary, supervisor: "systemd" } };
    expect(upgradeProblem({ ...EMPTY_UPGRADE, image: B2, rebuildUi: false }, t(systemd), IMAGES, ARTS)).toMatch(/instance supervisor/);
    expect(upgradeProblem(EMPTY_UPGRADE, null, IMAGES, ARTS)).toMatch(/registry row/);
  });
  it("passes a valid upgrade and a valid migration", () => {
    expect(upgradeProblem({ ...EMPTY_UPGRADE, image: B2, artifactId: "v1.6.6" }, t(imageTenantFixture), IMAGES, ARTS)).toBeNull();
    expect(upgradeProblem({ ...EMPTY_UPGRADE, image: B1, rebuildUi: false }, t(worktreeManagedTenantFixture), IMAGES, ARTS)).toBeNull();
  });
  it("no artifact at the commit ⇒ rebuild must be off", () => {
    expect(upgradeProblem({ ...EMPTY_UPGRADE, image: N1 }, t(imageTenantFixture), IMAGES, ARTS)).toMatch(/turn Rebuild UI off/);
    expect(upgradeProblem({ ...EMPTY_UPGRADE, image: N1, rebuildUi: false }, t(imageTenantFixture), IMAGES, ARTS)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The request on the wire
// ---------------------------------------------------------------------------

describe("update-code sends exactly the contract's args", () => {
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

  async function send(args: ReturnType<typeof updateCodeArgs>, dryRun: boolean, confirm?: string) {
    const plan = {
      plan_hash: "sha256:" + "0".repeat(64),
      op: "update-code",
      tenant: "hackathon",
      registry_generation: 7,
      schema_version: 1,
      doctor: { status: "green", findings: [] },
      requires_confirm: true,
      confirm_value: "hackathon",
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
          expires_at: "2026-10-09T23:00:00Z",
          reads_only: true,
        }),
      )
      .mockResolvedValueOnce(dryRun ? json(200, plan) : json(202, { ...upgradeJobFixture, state: "queued" }));
    vi.stubGlobal("fetch", fetchMock);
    const session = await import("./auth/session");
    await session.signInWithApiKey("sign-in-key-0000");
    const { upgradeRun } = await import("./components/UpgradeAction");
    const out = await upgradeRun("hackathon", args)({
      ctlKey: CTL_KEY,
      dryRun,
      confirm,
      idempotencyKey: "ui-update-code-00000000-0000-4000-8000-000000000000",
    });
    const [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/v1\/tenants\/hackathon\/ops\/update-code$/);
    expect(init.method).toBe("POST");
    expect(JSON.stringify(init.headers)).not.toContain(CTL_KEY);
    expect([...local.values()].join()).not.toContain(CTL_KEY);
    return { out, body: JSON.parse(init.body as string) };
  }

  it("image only (an API-only patch on a non-static UI)", async () => {
    const { out, body } = await send(updateCodeArgs({ ...EMPTY_UPGRADE, image: B2 }, "external"), true);
    expect(out.kind).toBe("plan");
    expect(body.args).toEqual({ image: B2 });
    expect(body.dry_run).toBe(true);
    expect(body.ctl_api_key).toBe(CTL_KEY);
  });

  it("image + rebuild + artifact, executed with the typed confirm", async () => {
    const { out, body } = await send(
      updateCodeArgs({ image: B2, rebuildUi: true, artifactId: "v1.6.6" }, "static"),
      false,
      "hackathon",
    );
    expect(out.kind).toBe("job");
    expect(body.args).toEqual({ image: B2, artifact_id: "v1.6.6" });
    expect(body.confirm).toBe("hackathon");
    expect(body.dry_run).toBe(false);
  });

  it("an explicit rebuild_ui false on a static UI", async () => {
    const { body } = await send(updateCodeArgs({ image: B2, rebuildUi: false, artifactId: "" }, "static"), true);
    expect(body.args).toEqual({ image: B2, rebuild_ui: false });
  });
});

// ---------------------------------------------------------------------------
// The success panel
// ---------------------------------------------------------------------------

describe("UpgradeResult", () => {
  it("a migration: version, commit, no previous image, 'migrated to image mode'", () => {
    const html = render(createElement(UpgradeResult, { job: upgradeJobFixture }));
    expect(html).toContain("Migrated to image mode");
    expect(html).toContain(B1);
    expect(html).toContain(">1.6.6<"); // from observed_version, the /v1/version body the post-check read
    expect(html).toContain(IMAGE_COMMIT_166.slice(0, 12));
    expect(html).toContain("none (was worktree mode)");
    expect(html).toContain("rebuilt from v1.6.6");
    expect(html).toContain("20261009T120000Z-pre-update");
  });

  it("image → image: previous image shown, no migration line; plain version string accepted", () => {
    const job = {
      ...upgradeJobFixture,
      result: { ...(upgradeJobFixture.result as object), previous_image: B1, image: B2, migration: false, version: "v1.6.6", observed_version: undefined, rebuild_ui: false, artifact_id: null },
    } as unknown as typeof upgradeJobFixture;
    const html = render(createElement(UpgradeResult, { job }));
    expect(html).not.toContain("Migrated");
    expect(html).toContain(B1);
    expect(html).toContain(">v1.6.6<");
    expect(html).toContain(">kept<");
  });

  it("a job recorded before observed_version: the object in `version` is still read", () => {
    const html = render(createElement(UpgradeResult, { job: upgradeJobLegacyFixture }));
    expect(html).toContain(">1.6.6<");
    expect(html).toContain(IMAGE_COMMIT_166.slice(0, 12));
    expect(html).not.toContain("[object Object]");
  });

  it("observed_version wins over the receipt string, its git_sha over the plan's commit", () => {
    expect(
      upgradeVersionOf({ version: "v1.6.6", commit: "a".repeat(40), observed_version: { version: "1.6.6", git_sha: "b".repeat(40) } }),
    ).toEqual({ version: "1.6.6", commit: "b".repeat(40) });
    expect(upgradeVersionOf({ version: { version: "1.6.5", git_sha: "c".repeat(40) }, commit: "a".repeat(40) })).toEqual({
      version: "1.6.5",
      commit: "c".repeat(40),
    });
    expect(upgradeVersionOf({ version: "v1.6.6", commit: "a".repeat(40) })).toEqual({ version: "v1.6.6", commit: "a".repeat(40) });
    expect(upgradeVersionOf({})).toEqual({ version: null, commit: null });
  });

  it("renders nothing for another op or an unsettled job", () => {
    expect(render(createElement(UpgradeResult, { job: jobFixture("succeeded") }))).toBe("");
    expect(render(createElement(UpgradeResult, { job: { ...upgradeJobFixture, state: "rolled_back" } }))).toBe("");
  });
});

// ---------------------------------------------------------------------------
// Overview and the fleet table
// ---------------------------------------------------------------------------

describe("image mode in the views", () => {
  const overview = (tenant: CtlTenant, role: "operator" | "viewer" = "operator") =>
    render(createElement(TenantSection, { section: "overview", name: "hackathon", role }), (qc) =>
      qc.setQueryData(ctlKeys.tenant("hackathon"), tenant),
    );

  it("operator, image row: image name, version, commit and the previous image", () => {
    const html = overview(imageTenantFixture);
    expect(html).toContain(`image ${B1}`);
    expect(html).toContain("v1.6.6 · ");
    expect(html).toContain(IMAGE_COMMIT_166.slice(0, 12));
    expect(html).toContain("previous image");
    expect(html).toContain("ragstack-server-v1.6.6-b0.sif");
    expect(html).not.toContain("/rag/data/ctl/images"); // the path stays off the page
  });

  it("viewer, image row: the summary's image name", () => {
    const html = overview(imageTenantViewerFixture, "viewer");
    expect(html).toContain(`image ${B1}`);
    expect(html).not.toContain("previous image");
  });

  it("worktree row: worktree <tag>", () => {
    expect(overview(worktreeManagedTenantFixture)).toContain("worktree v1.6.6");
  });

  it("the fleet table badges the mode under the code tag, no extra column", () => {
    const fleet: CtlFleet = {
      ...fleetFixture,
      tenants: [
        { ...fleetFixture.tenants[0], api_mode: "worktree" },
        { ...fleetFixture.tenants[1], api_mode: "image", server_image: "ragstack-server-v1.6.6-b2.sif" },
      ],
    };
    const html = render(createElement(FleetView, { role: "operator", onSelectTenant: noop }), (qc) => {
      qc.setQueryData(ctlKeys.fleet(), fleet);
      qc.setQueryData(ctlKeys.doctor(), doctorFixture);
      qc.setQueryData(ctlKeys.version(), versionFixture);
    });
    expect(html).toContain(">image v1.6.6-b2<");
    expect(html).toContain(">worktree<");
    expect(html).toContain('title="API from server image ragstack-server-v1.6.6-b2.sif"');
    expect((html.match(/<th /g) ?? []).length).toBe(
      (render(createElement(FleetView, { role: "operator", onSelectTenant: noop }), (qc) => {
        qc.setQueryData(ctlKeys.fleet(), fleetFixture);
        qc.setQueryData(ctlKeys.doctor(), doctorFixture);
        qc.setQueryData(ctlKeys.version(), versionFixture);
      }).match(/<th /g) ?? []).length,
    );
    expect(imageBuild("ragstack-server-v1.6.6+4c1322e-b3.sif")).toBe("v1.6.6+4c1322e-b3");
  });
});

// ---------------------------------------------------------------------------
// The canaries
// ---------------------------------------------------------------------------

describe("no secret-shaped field name, no leaked value", () => {
  const LEAKED = /leaked-[a-z-]+-value-\d{4}/;
  it("no rendered field name matches *_key or *password*", () => {
    const markup = [
      fields(worktreeManagedTenantFixture, EMPTY_UPGRADE),
      fields(imageTenantFixture, { ...EMPTY_UPGRADE, image: B2, artifactId: "v1.6.6" }),
      render(createElement(TenantActions, { name: "hackathon", initialVerb: "upgrade" }), seed(imageTenantFixture)),
    ].join("\n");
    const names = [...markup.matchAll(/name="([^"]+)"/g)].map((m) => m[1]);
    expect(names.length).toBeGreaterThan(3);
    for (const n of names) expect(n).not.toMatch(/_key$|password/i);
  });

  it("hostile server-image strings are redacted", () => {
    const html = fields(imageTenantFixture, EMPTY_UPGRADE, {
      images: hostileImageArtifactsFixture.server_images,
      artifacts: hostileImageArtifactsFixture.artifacts,
    });
    expect(html).not.toMatch(LEAKED);
    expect(html).toContain("&lt;redacted&gt;");
  });
});
