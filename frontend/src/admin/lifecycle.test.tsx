import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { CtlError } from "./api/http";
import { ctlKeys } from "./api/queries";
import type { CtlFleet, CtlRole, CtlTenant } from "./api/types";
import { FleetView } from "./components/FleetView";
import {
  EMPTY_LIFECYCLE_FORMS,
  LIFECYCLE_DESTRUCTIVE,
  LifecycleResult,
  LifecycleView,
  backupQualifies,
  classifyRefusal,
  decommissionBlocker,
  lifecycleOpSpec,
  lifecycleRun,
  purgeBlocker,
  purgedTenant,
  type LifecycleViewProps,
} from "./components/LifecycleSection";
import { OpFlowView } from "./components/OpFlow";
import { PlanView } from "./components/PlanView";
import { sectionsFor, TenantSection, TenantView } from "./components/TenantView";
import {
  activeManagedTenantFixture,
  decommissionPlanFixture,
  decommissionResultJobFixture,
  doctorFixture,
  hostilePurgeResultJobFixture,
  hostileQuarantinedTenantFixture,
  jobFixture,
  manualTenantFixture,
  purgePlanFixture,
  purgeResultJobFixture,
  QUARANTINE_JOB_ID,
  quarantinedFleetFixture,
  quarantinedTenantFixture,
  stoppedTenantFixture,
  versionFixture,
} from "./fixtures";

// PR-G3.2: the Lifecycle section — decommission (archive + quarantine) and
// purge. Same device as credentials.test.tsx: string renders through the
// props-only seam (`LifecycleView`), the form and the open flow passed in as
// props, data seeded under the query keys for the stateful wrapper.

function render(node: ReactElement, seed?: (qc: QueryClient) => void): string {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, retryOnMount: false, staleTime: Infinity } },
  });
  seed?.(qc);
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

const noop = () => {};

function view(extra: Partial<LifecycleViewProps> = {}): string {
  return render(
    createElement(LifecycleView, {
      name: "hackathon",
      role: "operator",
      tenant: activeManagedTenantFixture,
      forms: EMPTY_LIFECYCLE_FORMS,
      refusal: null,
      settled: null,
      round: 0,
      onForms: noop,
      onRunError: noop,
      onDone: noop,
      onClose: noop,
      onOpenJob: noop,
      ...extra,
    }),
  );
}

const seed = (tenant: CtlTenant) => (qc: QueryClient) => {
  qc.setQueryData(ctlKeys.tenant("hackathon"), tenant);
};

const PREVIEW_ENABLED = /<button type="button"(?![^>]*disabled="")[^>]*>Preview</;
const PREVIEW_DISABLED = /disabled=""[^>]*>Preview</;

// ---------------------------------------------------------------------------
// Absent for a viewer
// ---------------------------------------------------------------------------

describe("Lifecycle — absent for a viewer", () => {
  it("the seam renders nothing for a viewer, in every state", () => {
    for (const tenant of [activeManagedTenantFixture, stoppedTenantFixture, quarantinedTenantFixture, manualTenantFixture]) {
      expect(view({ role: "viewer", tenant })).toBe("");
    }
  });

  it("the rail lists Lifecycle for an operator only, after Credentials", () => {
    const op = sectionsFor("operator").map((s) => s.id);
    expect(op).toContain("lifecycle");
    expect(op.indexOf("lifecycle")).toBe(op.indexOf("credentials") + 1);
    expect(sectionsFor("viewer").map((s) => s.id)).not.toContain("lifecycle");
    const viewerRail = render(createElement(TenantView, { name: "hackathon", role: "viewer", onBack: noop }), seed(activeManagedTenantFixture));
    expect(viewerRail).not.toContain("Lifecycle");
    const operatorRail = render(createElement(TenantView, { name: "hackathon", role: "operator", onBack: noop }), seed(activeManagedTenantFixture));
    expect(operatorRail).toContain("Lifecycle");
  });

  it("reaching the section anyway answers the operator panel, with no control", () => {
    const html = render(
      createElement(TenantSection, { section: "lifecycle", name: "hackathon", role: "viewer" as CtlRole }),
      seed(quarantinedTenantFixture),
    );
    expect(html).toContain("Operator credential required.");
    expect(html).not.toContain("Preview");
    expect(html).not.toContain("keep_archive");
  });

  it("the operator's section reads the tenant", () => {
    const html = render(
      createElement(TenantSection, { section: "lifecycle", name: "hackathon", role: "operator" as CtlRole }),
      seed(quarantinedTenantFixture),
    );
    expect(html).toContain('aria-label="purge"');
    expect(html).toContain(QUARANTINE_JOB_ID);
  });

  it("the deep-linkable initial section opens Lifecycle for an operator, never for a viewer", () => {
    const op = render(
      createElement(TenantView, { name: "hackathon", role: "operator", onBack: noop, initialSection: "lifecycle" }),
      seed(quarantinedTenantFixture),
    );
    expect(op).toContain('aria-label="purge"');
    const viewer = render(
      createElement(TenantView, { name: "hackathon", role: "viewer", onBack: noop, initialSection: "lifecycle" }),
      seed(quarantinedTenantFixture),
    );
    expect(viewer).not.toContain('aria-label="purge"');
    expect(viewer).not.toContain("keep_archive");
  });
});

// ---------------------------------------------------------------------------
// Decommission, by state and supervisor
// ---------------------------------------------------------------------------

describe("Decommission", () => {
  it("active, ctl-run: offered, archive ON by default, Preview enabled, no purge", () => {
    const html = view();
    expect(html).toContain('aria-label="decommission"');
    expect(html).toContain("operation: archive and decommission hackathon");
    expect(html).toMatch(/<input type="checkbox" name="archive" class="mt-0.5" checked=""/);
    expect(html).toMatch(PREVIEW_ENABLED);
    // The archive copy: fenced, checked, stays stopped, and the identity it needs.
    expect(html).toContain("fenced full backup");
    expect(html).toContain("checks");
    expect(html).toContain("stays stopped");
    expect(html).toContain("ragstack-ctl fleet backup-identity init");
    // Purge is absent on a tenant that is not quarantined.
    expect(html).not.toContain('aria-label="purge"');
    expect(html).not.toContain("keep_archive");
    // A destructive op: titled red.
    expect(html).toMatch(/text-rust">archive and decommission hackathon/);
  });

  it("archive OFF: the copy requires a fenced checked-or-verified bundle and says whether the last one qualifies", () => {
    const off = { ...EMPTY_LIFECYCLE_FORMS, archive: false };
    const ok = view({ forms: off });
    expect(ok).toContain("operation: decommission hackathon (no new archive)");
    expect(ok).toContain("existing fenced bundle that is");
    expect(ok).toContain("checked or verified");
    expect(ok).toContain("It qualifies.");
    expect(ok).toContain("qualifies for decommission without archive");
    expect(ok).toMatch(PREVIEW_ENABLED);

    const none = view({ tenant: { ...stoppedTenantFixture }, forms: off });
    expect(none).toContain("It does not qualify");
    expect(none).toContain("does not qualify for decommission without archive");
    // Warned, not gated: a selftest sandbox needs no bundle and the daemon decides.
    expect(none).toMatch(PREVIEW_ENABLED);
  });

  it("stopped + archive: disabled with the reason (start it first); unticking archive enables it", () => {
    const html = view({ tenant: stoppedTenantFixture });
    expect(html).toContain('aria-label="decommission"');
    expect(html).toMatch(PREVIEW_DISABLED);
    expect(html).toContain("hackathon is stopped, and the archive snapshots its stores");
    expect(html).toContain("start it first");
    const off = view({ tenant: stoppedTenantFixture, forms: { ...EMPTY_LIFECYCLE_FORMS, archive: false } });
    expect(off).toMatch(PREVIEW_ENABLED);
  });

  it("manual supervisor: disabled with the reason (handover first), archive or not", () => {
    for (const archive of [true, false]) {
      const html = view({ name: "demo", tenant: manualTenantFixture, forms: { ...EMPTY_LIFECYCLE_FORMS, archive } });
      expect(html).toContain('aria-label="decommission"');
      expect(html).toMatch(PREVIEW_DISABLED);
      expect(html).toContain("supervisor: manual");
      expect(html).toContain("handover");
    }
  });

  it("not offered in other states", () => {
    for (const state of ["provisioned", "migrating", "handover", "decommissioned"] as const) {
      const t: CtlTenant = {
        ...activeManagedTenantFixture,
        summary: { ...activeManagedTenantFixture.summary, state },
        registry: { ...activeManagedTenantFixture.registry!, state },
      };
      const html = view({ tenant: t });
      expect(html).not.toContain('aria-label="decommission"');
      expect(html).not.toContain('aria-label="purge"');
      expect(html).toContain("Decommission is offered for an active or stopped tenant");
    }
  });

  it("the gates, as pure functions", () => {
    expect(decommissionBlocker({ state: "active", supervisor: "instance" }, "x", true)).toBeNull();
    expect(decommissionBlocker({ state: "active", supervisor: "systemd" }, "x", false)).toBeNull();
    expect(decommissionBlocker({ state: "stopped", supervisor: "instance" }, "x", true)).toMatch(/start it first/);
    expect(decommissionBlocker({ state: "stopped", supervisor: "instance" }, "x", false)).toBeNull();
    expect(decommissionBlocker({ state: "active", supervisor: "manual" }, "x", false)).toMatch(/handover/);
    expect(backupQualifies(null)).toBe(false);
    expect(backupQualifies({ fenced: true, verified: false })).toBe(false);
    expect(backupQualifies({ fenced: true, verified: false, checked: true })).toBe(true);
    expect(backupQualifies({ fenced: true, verified: true })).toBe(true);
    expect(backupQualifies({ fenced: false, verified: true, checked: true })).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// The decommission refusals: code + a fixed hint, never the detail
// ---------------------------------------------------------------------------

describe("decommission refusals", () => {
  const refused = (detail: string) =>
    new CtlError({ status: 409, code: "refused", detail, requestId: "0123456789abcdef" });
  const NO_RECIPIENT =
    "decommission archives hackathon first, and the archive must carry the tenant's secrets (backup secrets=require): no age recipient is configured in /rag/config/ctl/backup-recipients.txt. Run `ragstack-ctl fleet backup-identity init` (then restart the daemon), or decommission with --archive=false over an existing fenced bundle that is checked or verified";

  it("classifies the daemon's refusals by reason", () => {
    expect(classifyRefusal(refused(NO_RECIPIENT))?.reason).toBe("no_recipient");
    expect(classifyRefusal(refused("hackathon is stopped, and the archive snapshots its stores through their running APIs: start it first"))?.reason).toBe("stopped");
    expect(classifyRefusal(refused("demo is neither a tenant this ctl runs (supervisor systemd or instance, owner svcbvbrc — it is manual/wilke) nor a selftest sandbox"))?.reason).toBe("unmanaged");
    expect(classifyRefusal(refused("decommission needs a fenced bundle that is checked or verified"))?.reason).toBe("no_backup");
    expect(classifyRefusal(refused("something else"))?.reason).toBe("other");
    expect(classifyRefusal(new CtlError({ status: 409, code: "locked", detail: NO_RECIPIENT, requestId: null }))).toBeNull();
    expect(classifyRefusal(new Error("x"))).toBeNull();
  });

  it("the no-recipient refusal renders its code and the CLI command, never the raw detail", () => {
    const html = view({ refusal: classifyRefusal(refused(`${NO_RECIPIENT} API_KEYS=leaked-api-key-value-0420`)) });
    expect(html).toContain('aria-label="refusal hint"');
    expect(html).toContain(">refused<");
    expect(html).toContain("no_recipient");
    expect(html).toContain("ragstack-ctl fleet backup-identity init");
    expect(html).not.toContain("/rag/config/ctl/backup-recipients.txt");
    expect(html).not.toContain("leaked-api-key-value-0420");
    expect(html).not.toContain("decommission archives hackathon first");
  });

  it("lifecycleRun reports a failed decommission and rethrows; a purge failure is not classified", async () => {
    const err = refused(NO_RECIPIENT);
    const submit = vi.fn().mockRejectedValue(err);
    const seen: unknown[] = [];
    const run = lifecycleRun("hackathon", lifecycleOpSpec("hackathon", "decommission", EMPTY_LIFECYCLE_FORMS), (e) => seen.push(e), submit);
    await expect(run({ ctlKey: "k", dryRun: true, idempotencyKey: "ui-decommission-1" })).rejects.toBe(err);
    expect(seen).toEqual([err]);

    const purgeSeen: unknown[] = [];
    const purgeRun = lifecycleRun("hackathon", lifecycleOpSpec("hackathon", "purge", EMPTY_LIFECYCLE_FORMS), (e) => purgeSeen.push(e), submit);
    await expect(purgeRun({ ctlKey: "k", dryRun: true, idempotencyKey: "ui-purge-1" })).rejects.toBe(err);
    expect(purgeSeen).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Purge: only when quarantined
// ---------------------------------------------------------------------------

describe("Purge", () => {
  it("absent on every state but quarantined", () => {
    for (const tenant of [activeManagedTenantFixture, stoppedTenantFixture, manualTenantFixture]) {
      const html = view({ tenant });
      expect(html).not.toContain('aria-label="purge"');
      expect(html).not.toContain("keep_archive");
      expect(html).not.toMatch(/operation: purge/);
    }
  });

  it("quarantined: the red panel, keep_archive OFF by default, Preview enabled; no decommission", () => {
    const html = view({ tenant: quarantinedTenantFixture });
    expect(html).toContain('aria-label="purge"');
    expect(html).toContain("border-rust");
    expect(html).toContain("Purge — irreversible");
    expect(html).toContain("operation: purge hackathon");
    expect(html).toMatch(/text-rust">purge hackathon/);
    expect(html).toMatch(/<input type="checkbox" name="keep_archive" class="mt-0.5"\/>/);
    expect(html).toMatch(PREVIEW_ENABLED);
    // The copy: what goes, that it is irreversible, what remains.
    for (const s of ["quarantined data", "code checkout", "the units", "the archive", "irreversible", "tombstone", "audit log"]) {
      expect(html).toContain(s);
    }
    expect(html).not.toContain('aria-label="decommission"');
  });

  it("keep_archive ticked: titled so", () => {
    const html = view({ tenant: quarantinedTenantFixture, forms: { ...EMPTY_LIFECYCLE_FORMS, keepArchive: true } });
    expect(html).toContain("operation: purge hackathon (keep the archive)");
    expect(html).toMatch(/name="keep_archive" class="mt-0.5" checked=""/);
  });

  it("disabled with the reason on a manual tenant, or a row without quarantine.dir", () => {
    const manualQ: CtlTenant = {
      ...quarantinedTenantFixture,
      summary: { ...quarantinedTenantFixture.summary, supervisor: "manual" },
      registry: { ...quarantinedTenantFixture.registry!, supervisor: "manual" },
    };
    const m = view({ tenant: manualQ });
    expect(m).toMatch(PREVIEW_DISABLED);
    expect(m).toContain("supervisor: manual");
    const { quarantine: _q, ...rowWithout } = quarantinedTenantFixture.registry!;
    void _q;
    const noDir = view({ tenant: { ...quarantinedTenantFixture, registry: rowWithout } });
    expect(noDir).toMatch(PREVIEW_DISABLED);
    expect(noDir).toContain("records no quarantine.dir");
    expect(noDir).toContain("no quarantine record");
    expect(purgeBlocker({ supervisor: "instance", quarantineDir: "/x" })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Status block
// ---------------------------------------------------------------------------

describe("Status", () => {
  it("quarantined: the quarantine block, the job link, the bundle and the plain sentence", () => {
    const html = view({ tenant: quarantinedTenantFixture });
    expect(html).toContain(">quarantined<");
    expect(html).toContain('aria-label="quarantine"');
    expect(html).toContain("/rag/data/tenants/hackathon.quarantined-20261008T120500Z");
    expect(html).toContain("/rag/backups/tenants/hackathon/20261008T120000Z-backup");
    // A ULID job id is a button that opens the job.
    expect(html).toMatch(new RegExp(`<button type="button"[^>]*>${QUARANTINE_JOB_ID}</button>`));
    expect(html).toContain("stopped and");
    expect(html).toContain("unrouted");
    expect(html).toContain("recoverable until it is");
    expect(html).toContain("purged");
  });

  it("last backup: fenced, checked, verified chips and the bundle", () => {
    const html = view();
    expect(html).toContain(">fenced<");
    expect(html).toContain(">checked<");
    expect(html).toContain(">unverified<");
    expect(html).toContain("/rag/backups/tenants/hackathon/20261001T030000Z-backup");
    const never = view({ tenant: stoppedTenantFixture });
    expect(never).toContain(">never<");
  });

  it("the header of a quarantined tenant says so, and offers Lifecycle to an operator only", () => {
    const op = render(createElement(TenantView, { name: "hackathon", role: "operator", onBack: noop }), seed(quarantinedTenantFixture));
    expect(op).toContain("recoverable until purged");
    expect(op).toContain("Lifecycle →");
    const viewer = render(createElement(TenantView, { name: "hackathon", role: "viewer", onBack: noop }), seed(quarantinedTenantFixture));
    expect(viewer).toContain("recoverable until purged");
    expect(viewer).not.toContain("Lifecycle →");
  });
});

// ---------------------------------------------------------------------------
// Arguments, typed confirm, destructive flags
// ---------------------------------------------------------------------------

describe("lifecycleOpSpec — arguments and confirm", () => {
  it("decommission sends {archive} explicitly, both ways", () => {
    expect(lifecycleOpSpec("hackathon", "decommission", EMPTY_LIFECYCLE_FORMS)).toMatchObject({
      verb: "decommission",
      args: { archive: true },
      destructive: true,
      confirmValue: "hackathon",
    });
    expect(lifecycleOpSpec("hackathon", "decommission", { ...EMPTY_LIFECYCLE_FORMS, archive: false }).args).toEqual({ archive: false });
  });

  it("purge sends {keep_archive} explicitly, both ways", () => {
    expect(lifecycleOpSpec("hackathon", "purge", EMPTY_LIFECYCLE_FORMS)).toMatchObject({
      verb: "purge",
      args: { keep_archive: false },
      destructive: true,
      confirmValue: "hackathon",
    });
    expect(lifecycleOpSpec("hackathon", "purge", { ...EMPTY_LIFECYCLE_FORMS, keepArchive: true }).args).toEqual({ keep_archive: true });
  });

  it("lifecycleRun binds the verb and the args to the tenant's op endpoint", async () => {
    const submit = vi.fn().mockResolvedValue({ kind: "plan", plan: purgePlanFixture });
    const req = { ctlKey: "ctl-key", dryRun: true, idempotencyKey: "ui-purge-abc12345" };
    await lifecycleRun("hackathon", lifecycleOpSpec("hackathon", "purge", { archive: true, keepArchive: true }), noop, submit)(req);
    expect(submit).toHaveBeenCalledWith("hackathon", "purge", { ...req, args: { keep_archive: true } });
    await lifecycleRun("hackathon", lifecycleOpSpec("hackathon", "decommission", { archive: false, keepArchive: false }), noop, submit)(req);
    expect(submit).toHaveBeenLastCalledWith("hackathon", "decommission", { ...req, args: { archive: false } });
  });

  it("mirrors the daemon's destructive flags", () => {
    expect(LIFECYCLE_DESTRUCTIVE).toEqual({ decommission: true, purge: true });
  });

  it("the typed confirm asks for the tenant name, for both verbs", () => {
    for (const [verb, plan] of [
      ["decommission", decommissionPlanFixture],
      ["purge", purgePlanFixture],
    ] as const) {
      const spec = lifecycleOpSpec("hackathon", verb, EMPTY_LIFECYCLE_FORMS);
      expect(plan.confirm_value).toBe(spec.confirmValue);
      const html = renderToStaticMarkup(
        createElement(OpFlowView, {
          state: { kind: "confirming", idempotencyKey: `ui-${verb}-1`, plan, confirmValue: spec.confirmValue },
          title: spec.title,
          destructive: spec.destructive,
          onPreview: noop,
          onSpend: noop,
          onAccept: noop,
          onTyped: noop,
          onForceYellow: noop,
          onRetry: noop,
          onReset: noop,
        }),
      );
      expect(html).toMatch(/Type <code[^>]*>hackathon<\/code> to confirm/);
      expect(html).not.toContain("ctl API key to run this");
    }
  });
});

// ---------------------------------------------------------------------------
// The plans: destructive steps red, warnings in front of the operator
// ---------------------------------------------------------------------------

describe("the plans", () => {
  it("decommission: the fence stop and the rename are destructive and red; the archive warning shows", () => {
    const html = renderToStaticMarkup(createElement(PlanView, { plan: decommissionPlanFixture }));
    expect(html).toContain("15 steps");
    expect(html).toContain("· 6 destructive");
    expect(html).toMatch(/text-rust">stop the API for the fence/);
    expect(html).toMatch(/text-rust">quarantine the data directory/);
    expect(html).toContain("the API is not started again");
    expect(html).toContain("--archive (the default)");
    expect(html).toContain("required: <strong>hackathon</strong>");
  });

  it("purge: every removal IRREVERSIBLE, the row last", () => {
    const html = renderToStaticMarkup(createElement(PlanView, { plan: purgePlanFixture }));
    expect(html).toContain("· 5 destructive");
    expect(html.split("IRREVERSIBLE: there is no rollback").length - 1).toBe(5);
    expect(purgePlanFixture.steps.at(-1)?.kind).toBe("registry");
    for (const s of purgePlanFixture.steps.filter((x) => x.destructive)) {
      expect(s.warnings.some((w) => w.startsWith("IRREVERSIBLE"))).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// Settled jobs: onPurged, and the result notes
// ---------------------------------------------------------------------------

describe("settled jobs", () => {
  it("onPurged fires only for a succeeded purge of this tenant", () => {
    expect(purgedTenant(purgeResultJobFixture, "hackathon")).toBe(true);
    expect(purgedTenant(purgeResultJobFixture, "dev")).toBe(false);
    expect(purgedTenant({ ...purgeResultJobFixture, state: "failed" }, "hackathon")).toBe(false);
    expect(purgedTenant(decommissionResultJobFixture, "hackathon")).toBe(false);
    expect(purgedTenant({ ...jobFixture("succeeded"), tenant: "hackathon" }, "hackathon")).toBe(false);
  });

  it("the purge result: bytes freed, archive, tombstone, removed paths", () => {
    const html = renderToStaticMarkup(createElement(LifecycleResult, { job: purgeResultJobFixture }));
    expect(html).toContain('aria-label="purge result"');
    expect(html).toContain(">deleted<");
    expect(html).toContain("hackathon · block 24080");
    expect(html).toContain("/rag/repos/hackathon");
    expect(html).toContain("/rag/data/tenants/hackathon.quarantined-20261008T120500Z");
  });

  it("the decommission result: the quarantine dir and the archive bundle", () => {
    const html = view({ settled: decommissionResultJobFixture });
    expect(html).toContain('aria-label="decommission result"');
    expect(html).toContain("/rag/backups/tenants/hackathon/20261008T120000Z-backup");
  });

  it("no result note for an unsettled or failed job", () => {
    expect(renderToStaticMarkup(createElement(LifecycleResult, { job: { ...purgeResultJobFixture, state: "failed" } }))).toBe("");
    expect(renderToStaticMarkup(createElement(LifecycleResult, { job: { ...purgeResultJobFixture, result: null } }))).toBe("");
  });
});

// ---------------------------------------------------------------------------
// FleetView: quarantined rows stay, with a Purge link for operators
// ---------------------------------------------------------------------------

describe("FleetView quarantined rows", () => {
  const seedFleet = (fleet: CtlFleet) => (qc: QueryClient) => {
    qc.setQueryData(ctlKeys.fleet(), fleet);
    qc.setQueryData(ctlKeys.doctor(), doctorFixture);
    qc.setQueryData(ctlKeys.version(), versionFixture);
  };

  it("the row stays listed with its chip; the Purge link is the operator's only", () => {
    const op = render(createElement(FleetView, { onSelectTenant: noop, role: "operator" }), seedFleet(quarantinedFleetFixture));
    expect(op).toContain(">hackathon<");
    expect(op).toContain(">quarantined<");
    expect(op).toContain('href="#/tenant/hackathon"');
    expect(op.split(">Purge…<").length - 1).toBe(1);

    const viewer = render(createElement(FleetView, { onSelectTenant: noop, role: "viewer" }), seedFleet(quarantinedFleetFixture));
    expect(viewer).toContain(">quarantined<");
    expect(viewer).not.toContain("Purge…");
    // The default role is viewer.
    const dflt = render(createElement(FleetView, { onSelectTenant: noop }), seedFleet(quarantinedFleetFixture));
    expect(dflt).not.toContain("Purge…");
  });

  it("no Purge link on a fleet with nothing quarantined", () => {
    const html = render(
      createElement(FleetView, { onSelectTenant: noop, role: "operator" }),
      seedFleet({ ...quarantinedFleetFixture, tenants: quarantinedFleetFixture.tenants.slice(0, -1) }),
    );
    expect(html).not.toContain("Purge…");
  });
});

// ---------------------------------------------------------------------------
// The secrets canary, on the Lifecycle section
// ---------------------------------------------------------------------------

describe("no secret-named value renders from the lifecycle views", () => {
  const hostile = () =>
    [
      view({ tenant: hostileQuarantinedTenantFixture }),
      view({ tenant: hostileQuarantinedTenantFixture, settled: hostilePurgeResultJobFixture }),
      render(
        createElement(TenantSection, { section: "lifecycle", name: "hackathon", role: "operator" as CtlRole }),
        seed(hostileQuarantinedTenantFixture),
      ),
      renderToStaticMarkup(createElement(LifecycleResult, { job: hostilePurgeResultJobFixture })),
    ].join("\n");

  it("members no record has, and the result's secret-named members, never render", () => {
    const html = hostile();
    for (const leaked of [
      "leaked-key-value-0404",
      "leaked-password-value-0406",
      "leaked-key-value-0408",
      "leaked-token-value-0409",
      "leaked-secret-value-0410",
      "leaked-key-value-0411",
    ]) {
      expect(html).not.toContain(leaked);
    }
  });

  it("paths are not secret-named: they render, escaped — never as markup", () => {
    const html = hostile();
    expect(html).toContain("leaked-path-value-0401");
    expect(html).toContain("leaked-bundle-value-0402");
    expect(html).toContain("leaked-bundle-value-0403");
    expect(html).toContain("leaked-path-value-0407");
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(html).toContain("&lt;b&gt;bold&lt;/b&gt;");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<b>bold");
    expect(html).not.toContain("<script");
    // A job id that is not a ULID is text, not a button.
    expect(html).toContain("leaked-job-value-0405&lt;script&gt;");
    expect(html).not.toMatch(/<button[^>]*>leaked-job-value-0405/);
  });
});

