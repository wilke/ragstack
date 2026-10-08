import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { hashFor, parseHash, type View } from "./AdminApp";
import { ctlKeys } from "./api/queries";
import type { CtlRole, SettingsResponse } from "./api/types";
import {
  draftFrom,
  SettingsEditForm,
  SettingsPanel,
  settingsPatch,
  settingsProblems,
  SettingsView,
  type SettingsDraft,
} from "./components/SettingsView";
import { hostileSettingsFixture, settingsFixture } from "./fixtures";

// PR-G3.4: the settings page. String renders through the props-only seams
// (`SettingsPanel`, `SettingsEditForm`); the stateful `SettingsView` with a
// seeded cache for each role; the request through the real ops.ts with only
// `fetch` faked.

function render(node: ReactElement, seed?: (qc: QueryClient) => void): string {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, retryOnMount: false, staleTime: Infinity } },
  });
  seed?.(qc);
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

const noop = () => {};
const panel = (role: CtlRole, settings: SettingsResponse = settingsFixture, onEdit?: () => void) =>
  render(createElement(SettingsPanel, { settings, role, onEdit }));

/** A deep copy of the fixture's draft, for editing in a test. */
const draft = (s: SettingsResponse = settingsFixture): SettingsDraft => structuredClone(draftFrom(s));

describe("SettingsPanel renders every group", () => {
  const html = panel("viewer");

  it("has the five groups", () => {
    for (const g of ["Retention", "Images", "Python env", "Control plane", "Backup recipients"]) {
      expect(html).toContain(`aria-label="settings: ${g}"`);
    }
  });

  it("shows every value of the fixture", () => {
    const s = settingsFixture;
    for (const v of [
      s.retention.keep_last.backup,
      s.retention.keep_last.pre_update,
      s.retention.keep_partial_hours,
      s.images.qdrant.sif,
      s.images.qdrant.version,
      s.images.qdrant.digest,
      s.images.elasticsearch.sif,
      s.images.elasticsearch.version,
      s.images.elasticsearch.digest,
      s.python_env_default,
      s.ctl.port,
      s.ctl.ui_dist,
      s.recipients.file,
      s.recipients.fingerprints[0],
    ]) {
      expect(html).toContain(String(v));
    }
    expect(html).toContain(`registry generation <span class="tabular-nums text-strong">3</span>`);
  });

  it("auto_delete is the constant false, with no control", () => {
    expect(html).toMatch(/auto_delete<\/div>.*?false/);
    expect(html).not.toContain('name="retention');
  });

  it("recipients are read-only and a viewer's empty fingerprints are explained", () => {
    const viewer = panel("viewer", {
      ...settingsFixture,
      recipients: { ...settingsFixture.recipients, fingerprints: [] },
    });
    expect(viewer).toContain("not shown to a viewer");
    expect(html).not.toContain('name="recipients');
  });

  it("zero recipients warns that secret backups fail closed", () => {
    const none = panel("operator", {
      ...settingsFixture,
      recipients: { ...settingsFixture.recipients, count: 0, fingerprints: [] },
    });
    expect(none).toContain("fails closed");
    expect(html).not.toContain("fails closed");
  });
});

describe("roles", () => {
  it("a viewer has no Edit, even when handed an onEdit", () => {
    expect(panel("viewer", settingsFixture, noop)).not.toContain(">Edit<");
  });

  it("an operator has Edit", () => {
    expect(panel("operator", settingsFixture, noop)).toContain(">Edit<");
    // No handler, no button.
    expect(panel("operator")).not.toContain(">Edit<");
  });

  const seed = (s: SettingsResponse) => (qc: QueryClient) => qc.setQueryData(ctlKeys.settings(), s);

  it("SettingsView: a viewer reads, no Edit, no form, no key prompt", () => {
    const html = render(createElement(SettingsView, { role: "viewer" }), seed(settingsFixture));
    expect(html).toContain("read-only for a viewer");
    expect(html).toContain(settingsFixture.images.qdrant.sif);
    expect(html).not.toContain(">Edit<");
    expect(html).not.toContain("<input");
    expect(html).not.toContain('type="password"');
  });

  it("SettingsView: an operator reads and is offered Edit (the editor opens on click only)", () => {
    const html = render(createElement(SettingsView, { role: "operator" }), seed(settingsFixture));
    expect(html).toContain(">Edit<");
    expect(html).not.toContain("<input");
  });

  it("SettingsView: no role given is the viewer side", () => {
    const html = render(createElement(SettingsView, {}), seed(settingsFixture));
    expect(html).not.toContain(">Edit<");
  });
});

describe("settingsPatch", () => {
  it("is empty when nothing changed", () => {
    expect(settingsPatch(settingsFixture, draft())).toEqual({});
  });

  it("carries only the changed members, nested as the document nests them", () => {
    const d = draft();
    d.images.elasticsearch.version = "8.15.3";
    d.ctl.gateway_enabled = false;
    expect(settingsPatch(settingsFixture, d)).toEqual({
      images: { elasticsearch: { version: "8.15.3" } },
      ctl: { gateway_enabled: false },
    });
  });

  it("sends the port as a number and trims text", () => {
    const d = draft();
    d.ctl.port = " 24101 ";
    d.images.qdrant.sif = " /rag/apptainer/images/qdrant-1.13.sif ";
    expect(settingsPatch(settingsFixture, d)).toEqual({
      images: { qdrant: { sif: "/rag/apptainer/images/qdrant-1.13.sif" } },
      ctl: { port: 24101 },
    });
  });

  it("a value edited back to the original is not a change", () => {
    const d = draft();
    d.ctl.ui_dist = "/elsewhere";
    d.ctl.ui_dist = settingsFixture.ctl.ui_dist;
    expect(settingsPatch(settingsFixture, d)).toEqual({});
  });

  it("never carries a CLI-only or unpersisted member", () => {
    const d = draft();
    d.images.qdrant.version = "x";
    d.ctl.port = "30000";
    const keys = Object.keys(settingsPatch(settingsFixture, d));
    for (const k of ["retention", "recipients", "python_env_default", "registry_generation"]) {
      expect(keys).not.toContain(k);
    }
  });

  it("an untouched field the client redacted is not re-sent as <redacted>", () => {
    const p = settingsPatch(hostileSettingsFixture, draft(hostileSettingsFixture));
    expect(p).toEqual({});
  });
});

describe("settingsProblems", () => {
  it("the fixture is clean", () => {
    expect(settingsProblems(draft())).toEqual({ errors: {}, warnings: {} });
  });

  it("port: an integer from 1024 to 65535", () => {
    for (const bad of ["", "80", "65536", "24100.5", "-1", "abc"]) {
      const d = draft();
      d.ctl.port = bad;
      expect(settingsProblems(d).errors["ctl.port"], bad).toBeDefined();
    }
    for (const ok of ["1024", "65535", "24100"]) {
      const d = draft();
      d.ctl.port = ok;
      expect(settingsProblems(d).errors["ctl.port"], ok).toBeUndefined();
    }
  });

  it("version required, digest pattern enforced", () => {
    const d = draft();
    d.images.qdrant.version = " ";
    d.images.elasticsearch.digest = "sha256:abc";
    const p = settingsProblems(d);
    expect(p.errors["images.qdrant.version"]).toBeDefined();
    expect(p.errors["images.elasticsearch.digest"]).toBeDefined();
  });

  it("a relative path is a warning, not an error", () => {
    const d = draft();
    d.images.qdrant.sif = "images/qdrant.sif";
    d.ctl.ui_dist = "dist";
    const p = settingsProblems(d);
    expect(p.errors).toEqual({});
    expect(p.warnings["images.qdrant.sif"]).toBe("not an absolute path");
    expect(p.warnings["ctl.ui_dist"]).toBe("not an absolute path");
  });
});

describe("SettingsEditForm", () => {
  it("edits images and ctl only; recipients, python env and retention have no input", () => {
    const html = render(
      createElement(SettingsEditForm, { draft: draft(), problems: settingsProblems(draft()), onChange: noop }),
    );
    for (const n of [
      "images.qdrant.sif",
      "images.qdrant.version",
      "images.qdrant.digest",
      "images.elasticsearch.sif",
      "images.elasticsearch.version",
      "images.elasticsearch.digest",
      "ctl.port",
      "ctl.ui_dist",
      "ctl.gateway_enabled",
    ]) {
      expect(html).toContain(`name="${n}"`);
    }
    expect(html).not.toMatch(/name="(retention|recipients|python_env_default|registry_generation)/);
    expect(html).not.toContain('type="password"');
  });

  it("marks errors and warnings next to their field", () => {
    const d = draft();
    d.ctl.port = "80";
    d.ctl.ui_dist = "dist";
    const html = render(createElement(SettingsEditForm, { draft: d, problems: settingsProblems(d), onChange: noop }));
    expect(html).toContain("an integer from 1024 to 65535");
    expect(html).toContain("warning: not an absolute path");
    expect(html).toContain('aria-invalid="true"');
  });
});

// ---------------------------------------------------------------------------
// The request on the wire: through the real ops.ts, `fetch` faked
// ---------------------------------------------------------------------------

describe("putSettings sends the patch as args", () => {
  const CTL_KEY = "ctl-key-0123456789abcdef-SECRET";
  let local: Map<string, string>;
  let session: Map<string, string>;

  beforeEach(() => {
    vi.resetModules();
    local = new Map();
    session = new Map();
    const store = (m: Map<string, string>) =>
      ({
        getItem: (k: string) => m.get(k) ?? null,
        setItem: (k: string, v: string) => void m.set(k, v),
        removeItem: (k: string) => void m.delete(k),
        clear: () => m.clear(),
        key: () => null,
        length: 0,
      }) as unknown as Storage;
    vi.stubGlobal("sessionStorage", store(session));
    vi.stubGlobal("localStorage", store(local));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("PUTs /v1/settings with the key and the patch in the body only", async () => {
    const plan = {
      plan_hash: "sha256:" + "0".repeat(64),
      op: "settings-put",
      tenant: null,
      registry_generation: 3,
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
    const auth = await import("./auth/session");
    await auth.signInWithApiKey("sign-in-key-0000");
    const { putSettings } = await import("./api/ops");

    const d = draft();
    d.ctl.gateway_enabled = false;
    d.images.qdrant.version = "1.13.0";
    const patch = settingsPatch(settingsFixture, d);

    const out = await putSettings({
      args: patch,
      ctlKey: CTL_KEY,
      dryRun: true,
      idempotencyKey: "ui-settings-put-00000000-0000-4000-8000-000000000000",
    });
    expect(out.kind).toBe("plan");

    const [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/v1\/settings$/);
    expect(init.method).toBe("PUT");
    const body = JSON.parse(init.body as string);
    expect(body.args).toEqual({ images: { qdrant: { version: "1.13.0" } }, ctl: { gateway_enabled: false } });
    expect(body.ctl_api_key).toBe(CTL_KEY);
    expect(body.dry_run).toBe(true);
    expect(JSON.stringify(init.headers)).not.toContain(CTL_KEY);
    expect([...local.values(), ...session.values()].join()).not.toContain(CTL_KEY);
  });
});

describe("routing", () => {
  it("#/settings round-trips through the hash", () => {
    expect(parseHash("#/settings")).toEqual({ kind: "settings" });
    const v: View = { kind: "settings" };
    expect(hashFor(v)).toBe("#/settings");
    expect(parseHash(hashFor(v))).toEqual(v);
    expect(parseHash("#/settings/x")).toEqual({ kind: "fleet" });
  });
});

// ---------------------------------------------------------------------------
// Canary: a server that leaked into every free-text member
// ---------------------------------------------------------------------------

describe("hostile settings", () => {
  const LEAKED = /leaked-[a-z-]+-value-\d{4}/;
  const seed = (qc: QueryClient) => qc.setQueryData(ctlKeys.settings(), hostileSettingsFixture);

  it("no leaked value in the panel for either role, and markup stays text", () => {
    for (const role of ["viewer", "operator"] as const) {
      const html = panel(role, hostileSettingsFixture, noop);
      expect(html, role).not.toMatch(LEAKED);
      expect(html, role).not.toContain("<img");
      expect(html, role).toContain("&lt;img src=x onerror=alert(1)&gt;");
    }
  });

  it("no leaked value in the stateful view for either role", () => {
    for (const role of ["viewer", "operator"] as const) {
      expect(render(createElement(SettingsView, { role }), seed), role).not.toMatch(LEAKED);
    }
  });

  it("no leaked value in the editor's prefilled inputs", () => {
    const d = draft(hostileSettingsFixture);
    const html = render(createElement(SettingsEditForm, { draft: d, problems: settingsProblems(d), onChange: noop }));
    expect(html).not.toMatch(LEAKED);
    expect(html).not.toContain("<img");
  });
});
