import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { hashFor, parseHash, type View } from "./AdminApp";
import { CtlError } from "./api/http";
import { ctlKeys } from "./api/queries";
import type { Job, Plan, StepLogResponse } from "./api/types";
import { JobDetail, JobPage, JobsListView, JobsTable, JobsView, TenantJobs } from "./components/JobsView";
import { OpFlow, OpFlowView, type OpFlowViewProps } from "./components/OpFlow";
import { actionArgs, actionProblem, EMPTY_FORM, TenantActions } from "./components/TenantActions";
import { sectionsFor, TenantSection, TenantView } from "./components/TenantView";
import {
  doctorFixture,
  envFixture,
  fleetFixture,
  hostileJobFixture,
  hostilePlanFixture,
  hostileStepLogsFixture,
  jobFixture,
  jobsFixture,
  planFixture,
  secretsFixture,
  stepLogsFixture,
  tenantFixture,
  versionFixture,
} from "./fixtures";
import type { OpFlowState } from "./lib/opflow";

// PR-G2.3: the views that ACT. Same device as render.test.tsx — string
// renders, no DOM, no fetch; data seeded under the exported query keys. A
// string render cannot click, so every screen of a flow is reached through the
// props-only seam (`OpFlowView`, `JobsListView`, `JobsTable`) with the state
// it would be in, and the stateful wrappers are rendered at their entry point.

function render(node: ReactElement, seed?: (qc: QueryClient) => void): string {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, retryOnMount: false, staleTime: Infinity } },
  });
  seed?.(qc);
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

const KEY = "ui-stop-00000000-0000-4000-8000-000000000000";
const noop = () => {};

function flowView(state: OpFlowState, extra: Partial<OpFlowViewProps> = {}): string {
  return render(
    createElement(OpFlowView, {
      state,
      title: "stop dev",
      onPreview: noop,
      onSpend: noop,
      onAccept: noop,
      onTyped: noop,
      onForceYellow: noop,
      onRetry: noop,
      onReset: noop,
      ...extra,
    }),
  );
}

const err = (code: CtlError["code"], status = 409, extra: Record<string, unknown> | null = null) =>
  new CtlError({ status, code, detail: "server prose that must not render", requestId: "feedfacefeedface", extra });

const greenPlan: Plan = {
  ...planFixture,
  requires_confirm: false,
  confirm_value: null,
  doctor: { ...planFixture.doctor, status: "green", findings: [] },
  steps: planFixture.steps.map((s) => ({ ...s, destructive: false })),
};
const redPlan: Plan = { ...planFixture, doctor: { ...hostilePlanFixture.doctor, status: "red" } };

const PASSWORD_INPUT = 'type="password"';

// ---------------------------------------------------------------------------
// OpFlowView, every state
// ---------------------------------------------------------------------------

describe("OpFlowView in every state", () => {
  it("idle: the form, a Preview that says it is a dry run, and no key prompt", () => {
    const html = flowView({ kind: "idle" }, { children: createElement("p", null, "THE FORM") });
    expect(html).toContain("THE FORM");
    expect(html).toContain("Preview");
    expect(html).toContain("A dry run");
    expect(html).not.toContain(PASSWORD_INPUT);
    // The form is editable only before the flow starts.
    expect(html).not.toMatch(/<fieldset disabled/);
  });

  it("idle but invalid: Preview disabled, with the reason", () => {
    const html = flowView({ kind: "idle" }, { disabled: true, disabledReason: "from: a bundle id" });
    expect(html).toMatch(/<button type="button" disabled=""[^>]*>Preview<\/button>/);
    expect(html).toContain("from: a bundle id");
  });

  it("planning: the dry run asks for the key itself — the contract requires it on every POST", () => {
    const html = flowView(
      { kind: "planning", idempotencyKey: KEY, stale: false },
      { children: createElement("p", null, "THE FORM") },
    );
    expect(html).toContain(PASSWORD_INPUT);
    expect(html).toContain("ctl API key for the dry run");
    expect(html).toContain('autoComplete="off"');
    expect(html).not.toContain("The plan changed");
    // The form is frozen while a plan of it is in flight or on screen.
    expect(html).toMatch(/<fieldset disabled=""/);
  });

  it("planning after plan_stale: says why it is asking again", () => {
    const html = flowView({ kind: "planning", idempotencyKey: KEY, stale: true });
    expect(html).toContain("The plan changed after you reviewed it");
    expect(html).toContain(PASSWORD_INPUT);
  });

  it("planned (green): the plan and Run, and no key prompt yet", () => {
    const html = flowView({ kind: "planned", idempotencyKey: KEY, plan: greenPlan });
    expect(html).toContain("plan · dry run");
    expect(html).toContain("Run this plan…");
    expect(html).toContain("Discard");
    expect(html).not.toContain(PASSWORD_INPUT);
    expect(html).not.toContain("force_with_doctor_diff");
  });

  it("planned (yellow, destructive steps): the red Run and the doctor-warning acknowledgement", () => {
    const html = flowView({ kind: "planned", idempotencyKey: KEY, plan: planFixture });
    expect(html).toContain("Run this destructive plan…");
    expect(html).toContain("force_with_doctor_diff");
    expect(html).toContain("Never bypasses a red finding");
  });

  it("planned (red): Run is disabled — the execute would be refused", () => {
    const html = flowView({ kind: "planned", idempotencyKey: KEY, plan: redPlan });
    expect(html).toMatch(/<button type="button" disabled=""[^>]*>Run this destructive plan…<\/button>/);
    expect(html).toContain("Doctor is red for this scope");
  });

  it("confirming a requires_confirm plan: the typed confirm comes BEFORE the key", () => {
    const state: OpFlowState = { kind: "confirming", idempotencyKey: KEY, plan: planFixture, confirmValue: "dev" };
    const before = flowView(state);
    expect(before).toContain("Type <code");
    expect(before).toContain("This plan is destructive.");
    expect(before).not.toContain(PASSWORD_INPUT);

    const after = flowView(state, { typedOk: true });
    expect(after).not.toContain("Type <code");
    expect(after).toContain(PASSWORD_INPUT);
    expect(after).toContain("ctl API key to run this");
  });

  it("confirming with nothing to type: straight to the key", () => {
    const html = flowView({ kind: "confirming", idempotencyKey: KEY, plan: greenPlan, confirmValue: null });
    expect(html).toContain(PASSWORD_INPUT);
    expect(html).not.toContain("Type <code");
  });

  it("confirming a planless continuation: no plan, the key prompt", () => {
    const html = flowView(
      { kind: "confirming", idempotencyKey: KEY, plan: null, confirmValue: null },
      { planless: true, title: "Cancel job" },
    );
    expect(html).not.toContain("plan · dry run");
    expect(html).toContain(PASSWORD_INPUT);
    expect(html).toContain("This changes a job");
  });

  it("submitting: no prompt, a status line", () => {
    const html = flowView({ kind: "submitting", idempotencyKey: KEY, plan: greenPlan });
    expect(html).toContain("Submitting…");
    expect(html).not.toContain(PASSWORD_INPUT);
  });

  it("running: the job, without nested continuation buttons, and a link to its page", () => {
    const job = jobFixture("running");
    const html = flowView(
      { kind: "running", idempotencyKey: KEY, jobId: job.id, job, location: null },
      { onOpenJob: noop, logs: stepLogsFixture },
    );
    expect(html).toContain(job.id);
    expect(html).toContain("Open job page");
    expect(html).not.toContain("Cancel job");
    // An operator flow: step logs render.
    expect(html).toContain("tar: 1.2 GiB, sha256 ok");
    expect(html).not.toContain(PASSWORD_INPUT);
  });

  it("running with follow=false: one line, the job id", () => {
    const job = jobFixture("running");
    const html = flowView(
      { kind: "running", idempotencyKey: KEY, jobId: job.id, job, location: null },
      { follow: false },
    );
    expect(html).toContain(`Accepted as job <code class="font-mono">${job.id}</code>`);
    expect(html).not.toContain("steps");
  });

  it("done (failed job): the job's error and rollback, Done, no reveal", () => {
    const job = jobFixture("failed");
    const html = flowView({ kind: "done", idempotencyKey: KEY, job }, { revealLoad: async () => secretsFixture });
    expect(html).toContain("driver_failed");
    expect(html).toContain("rollback");
    expect(html).toContain("Done");
    expect(html).not.toContain("Reveal once");
  });

  it("done (a secret-bearing success): the reveal button, and no value", () => {
    const job: Job = { ...jobFixture("succeeded"), op: "restore" };
    const html = flowView({ kind: "done", idempotencyKey: KEY, job }, { revealLoad: async () => secretsFixture });
    expect(html).toContain("Reveal once");
    for (const s of secretsFixture.secrets) expect(html).not.toContain(s.value);
  });

  it("failed (validation): the code's copy and the reference, never the detail", () => {
    const html = flowView({
      kind: "failed",
      idempotencyKey: KEY,
      error: err("validation", 422, { fields: ["args.from"] }),
      plan: null,
      jobId: null,
    });
    expect(html).toContain("args.from");
    expect(html).toContain("Reference: feedfacefeedface");
    expect(html).not.toContain("server prose");
    expect(html).toContain("Plan again");
    expect(html).toContain("Start over");
  });

  it("failed (locked / duplicate): an Open job link to the named job", () => {
    const jobId = "01J9Z3K7Q8M4N5P6R7S8T9V0W1";
    for (const code of ["locked", "duplicate"] as const) {
      const html = flowView(
        { kind: "failed", idempotencyKey: KEY, error: err(code, 409, { job_id: jobId }), plan: null, jobId },
        { onOpenJob: noop },
      );
      expect(html).toContain(jobId);
      expect(html).toContain("Open job");
    }
  });

  it("failed (doctor_red): the plan's doctor findings", () => {
    const html = flowView({
      kind: "failed",
      idempotencyKey: KEY,
      error: err("doctor_red"),
      plan: planFixture,
      jobId: null,
    });
    expect(html).toContain("doctor findings for this plan");
    expect(html).toContain("supervisor_manual");
  });

  it("failed (403): the operator-credential panel, about the key", () => {
    const html = flowView({
      kind: "failed",
      idempotencyKey: KEY,
      error: err("forbidden", 403),
      plan: null,
      jobId: null,
    });
    expect(html).toContain("Operator credential required.");
    expect(html).toContain("belongs to the subject you are signed in as");
    expect(html).not.toContain("server prose");
  });

  it("the stateful wrapper starts idle; a planless autoStart opens at the key prompt", () => {
    const run = async () => {
      throw new Error("never called by a render");
    };
    const idle = render(createElement(OpFlow, { run, title: "restart dev" }));
    expect(idle).toContain("Preview");
    expect(idle).not.toContain(PASSWORD_INPUT);
    const cont = render(createElement(OpFlow, { run, title: "Resume job", planless: true, autoStart: true }));
    expect(cont).toContain(PASSWORD_INPUT);
    expect(cont).not.toContain("Preview");
  });
});

// ---------------------------------------------------------------------------
// TenantView: Actions (operator only) and Jobs (both roles)
// ---------------------------------------------------------------------------

const seedTenant = (qc: QueryClient) => {
  qc.setQueryData(ctlKeys.fleet(), fleetFixture);
  qc.setQueryData(ctlKeys.doctor(), doctorFixture);
  qc.setQueryData(ctlKeys.version(), versionFixture);
  qc.setQueryData(ctlKeys.tenant("dev"), tenantFixture);
  qc.setQueryData(ctlKeys.tenantEnv("dev"), envFixture);
  qc.setQueryData(ctlKeys.jobs({ tenant: "dev" }), jobsFixture);
};

describe("TenantView Actions", () => {
  it("is in an operator's rail and ABSENT from a viewer's", () => {
    expect(sectionsFor("operator").map((s) => s.id)).toContain("actions");
    expect(sectionsFor("viewer").map((s) => s.id)).not.toContain("actions");
    expect(sectionsFor("viewer").map((s) => s.id)).toContain("jobs");

    const operator = render(createElement(TenantView, { name: "dev", role: "operator", onBack: noop }), seedTenant);
    const viewer = render(createElement(TenantView, { name: "dev", role: "viewer", onBack: noop }), seedTenant);
    expect(operator).toContain(">Actions<");
    expect(viewer).not.toContain("Actions");
    expect(operator).toContain(">Jobs<");
    expect(viewer).toContain(">Jobs<");
    // No mutation control of any kind on a viewer's page.
    expect(viewer).not.toContain("Preview");
    expect(viewer).not.toContain(PASSWORD_INPUT);
  });

  it("renders the verbs, the restart form and a Preview for an operator", () => {
    const html = render(createElement(TenantSection, { section: "actions", name: "dev", role: "operator" }), seedTenant);
    for (const v of ["start", "stop", "restart", "backup", "restore"]) expect(html).toContain(`>${v}</button>`);
    expect(html).toContain("restart dev");
    for (const s of ["api", "ui", "qdrant", "es", "postgres"]) expect(html).toContain(`name="only-${s}"`);
    expect(html).toContain('name="force"');
    expect(html).toContain("Preview");
    expect(html).not.toContain(PASSWORD_INPUT);
  });

  it("renders the stop, backup and restore forms", () => {
    const form = (initialVerb: "stop" | "backup" | "restore") =>
      render(createElement(TenantActions, { name: "dev", initialVerb }));
    expect(form("stop")).toContain('name="keep_enabled"');
    const backup = form("backup");
    expect(backup).toContain('name="fence"');
    for (const s of ["config", "state", "stores"]) expect(backup).toContain(`name="scope-${s}"`);
    expect(backup).toContain('name="secrets"');
    const restore = form("restore");
    expect(restore).toContain('name="from"');
    expect(restore).toContain('name="as"');
    // Empty from/as: Preview is held back with the reason.
    expect(restore).toMatch(/<button type="button" disabled=""[^>]*>Preview<\/button>/);
    expect(restore).toContain("a bundle id such as");
  });

  it("a viewer who reaches the section anyway gets the 403 panel, not a form", () => {
    const html = render(createElement(TenantSection, { section: "actions", name: "dev", role: "viewer" }), seedTenant);
    expect(html).toContain("Operator credential required.");
    expect(html).not.toContain("Preview");
  });

  it("builds args the contract defines, defaults omitted", () => {
    expect(actionArgs("restart", EMPTY_FORM)).toEqual({});
    expect(actionArgs("stop", { ...EMPTY_FORM, only: ["es", "api"], force: true, keepEnabled: true })).toEqual({
      only: ["api", "es"],
      force: true,
      keep_enabled: true,
    });
    // keep_enabled is stop's alone.
    expect(actionArgs("start", { ...EMPTY_FORM, keepEnabled: true })).toEqual({});
    expect(actionArgs("backup", { ...EMPTY_FORM, fence: true, scope: ["stores", "config"], secrets: "require" })).toEqual({
      fence: true,
      scope: ["config", "stores"],
      secrets: "require",
    });
    expect(actionArgs("restore", { ...EMPTY_FORM, from: " 20261008T120000Z-backup ", as: "dev-r" })).toEqual({
      from: "20261008T120000Z-backup",
      as: "dev-r",
    });
  });

  it("refuses what the planner would: a scope without config, a fence on a light bundle, a bad restore", () => {
    expect(actionProblem("backup", EMPTY_FORM)).toBeNull();
    expect(actionProblem("backup", { ...EMPTY_FORM, scope: ["stores"] })).toMatch(/config/);
    expect(actionProblem("backup", { ...EMPTY_FORM, scope: ["config", "state"], fence: true })).toMatch(/fence/);
    expect(actionProblem("backup", { ...EMPTY_FORM, scope: ["config", "stores"], fence: true })).toBeNull();
    expect(actionProblem("restore", { ...EMPTY_FORM, from: "../../etc/passwd", as: "x" })).toMatch(/from/);
    expect(actionProblem("restore", { ...EMPTY_FORM, from: "20261008T120000Z-recovery", as: "Bad_Name" })).toMatch(/as:/);
    expect(actionProblem("restore", { ...EMPTY_FORM, from: "20261008T120000Z-pre-update", as: "dev-r" })).toBeNull();
    expect(actionProblem("restart", EMPTY_FORM)).toBeNull();
  });
});

describe("TenantView Jobs", () => {
  it("lists this tenant's jobs with state chips, for both roles", () => {
    for (const role of ["viewer", "operator"] as const) {
      const html = render(createElement(TenantSection, { section: "jobs", name: "dev", role }), seedTenant);
      for (const j of jobsFixture.jobs) expect(html).toContain(j.id);
      expect(html).toContain("awaiting_cutover");
      expect(html).toContain("rolled_back");
    }
  });

  it("says so when the job list is refused", () => {
    const html = render(createElement(TenantJobs, { name: "dev", role: "viewer" }), (qc) => {
      const q = qc.getQueryCache().build(qc, { queryKey: [...ctlKeys.jobs({ tenant: "dev" })] });
      q.setState({ ...q.state, status: "error", fetchStatus: "idle", error: err("forbidden", 403) });
    });
    expect(html).toContain("Operator credential required.");
  });
});

// ---------------------------------------------------------------------------
// JobsView (#/jobs) and the job page (#/job/<id>)
// ---------------------------------------------------------------------------

const seedJob = (job: Job, logs?: Record<number, string[]>) => (qc: QueryClient) => {
  qc.setQueryData(ctlKeys.job(job.id), job);
  for (const [n, lines] of Object.entries(logs ?? {})) {
    const body: StepLogResponse = {
      job_id: job.id,
      step: Number(n),
      lines,
      requested: 200,
      returned: lines.length,
      truncated: false,
      redacted: true,
    };
    qc.setQueryData(ctlKeys.jobStepLog(job.id, Number(n)), body);
  }
};

describe("JobsView", () => {
  it("lists every job fleet-wide with tenant and state filters", () => {
    const html = render(createElement(JobsView, { role: "viewer", onOpenJob: noop }), (qc) => {
      qc.setQueryData(ctlKeys.jobs(), jobsFixture);
      qc.setQueryData(ctlKeys.fleet(), fleetFixture);
    });
    expect(html).toContain("Jobs");
    for (const j of jobsFixture.jobs) expect(html).toContain(j.id);
    expect(html).toContain('id="jobs-tenant"');
    expect(html).toContain('id="jobs-state"');
    for (const t of fleetFixture.tenants) expect(html).toContain(`<option value="${t.name}">`);
    expect(html).toContain('<option value="interrupted">');
    expect(html).toContain(`${jobsFixture.jobs.length} jobs`);
  });

  it("the list seam: truncation, the loading line, the empty table, and a 403", () => {
    const base = { filter: { tenant: "", state: "" as const }, tenants: [], onFilter: noop, onSelect: noop };
    expect(render(createElement(JobsListView, { ...base, data: { ...jobsFixture, truncated: true }, error: null }))).toContain(
      "more match",
    );
    expect(render(createElement(JobsListView, { ...base, data: undefined, error: null }))).toContain("Loading jobs…");
    expect(render(createElement(JobsTable, { jobs: [], onSelect: noop }))).toContain("No jobs match.");
    expect(render(createElement(JobsListView, { ...base, data: undefined, error: err("forbidden", 403) }))).toContain(
      "Operator credential required.",
    );
  });

  it("job page, operator: step logs, and the continuation the state allows", () => {
    const job = jobFixture("interrupted");
    const html = render(
      createElement(JobPage, { id: job.id, role: "operator", onBack: noop, onOpenJob: noop }),
      seedJob(job, { 3: ["apptainer: stopping es-dev", "FATAL: exit status 255"] }),
    );
    expect(html).toContain(job.id);
    expect(html).toContain("Resume");
    expect(html).not.toContain("Cancel job");
    expect(html).toContain("FATAL: exit status 255");
    // Nothing is asked for until the operator clicks Resume.
    expect(html).not.toContain(PASSWORD_INPUT);

    const parked = jobFixture("awaiting_cutover");
    const p = render(createElement(JobDetail, { id: parked.id, role: "operator" }), seedJob(parked));
    expect(p).toContain("Continue to cutover");
    expect(p).toContain("Cancel job");
  });

  it("job page, viewer: the job, no controls, no step logs", () => {
    const job = jobFixture("interrupted", { viewer: true });
    const html = render(
      createElement(JobPage, { id: job.id, role: "viewer", onBack: noop, onOpenJob: noop }),
      seedJob(job, { 3: ["SHOULD NOT BE FETCHED OR SHOWN"] }),
    );
    expect(html).toContain(job.id);
    expect(html).not.toContain("Resume");
    expect(html).not.toContain("Cancel job");
    expect(html).not.toContain("SHOULD NOT BE FETCHED OR SHOWN");
  });

  it("job page: a refused read is the 403 panel", () => {
    const id = "01J9Z3K7Q8M4N5P6R7S8T9V0W6";
    const html = render(createElement(JobDetail, { id, role: "viewer" }), (qc) => {
      const q = qc.getQueryCache().build(qc, { queryKey: [...ctlKeys.job(id)] });
      q.setState({ ...q.state, status: "error", fetchStatus: "idle", error: err("forbidden", 403) });
    });
    expect(html).toContain("Operator credential required.");
  });
});

// ---------------------------------------------------------------------------
// AdminApp hash routes
// ---------------------------------------------------------------------------

describe("hash deep links for jobs", () => {
  it("parses and prints #/jobs and #/job/<ulid>", () => {
    const id = "01J9Z3K7Q8M4N5P6R7S8T9V0W3";
    expect(parseHash("#/jobs")).toEqual({ kind: "jobs" });
    expect(parseHash(`#/job/${id}`)).toEqual({ kind: "job", id });
    const views: View[] = [
      { kind: "fleet" },
      { kind: "gateway" },
      { kind: "jobs" },
      { kind: "job", id },
      { kind: "tenant", name: "lucid-next" },
    ];
    for (const v of views) expect(parseHash(hashFor(v))).toEqual(v);
  });

  it("refuses anything that is not a job id", () => {
    for (const h of [
      "#/job/",
      "#/job/01j9z3k7q8m4n5p6r7s8t9v0w3", // lower case
      "#/job/01J9Z3K7Q8M4N5P6R7S8T9V0WI", // I is not Crockford
      "#/job/../../v1/jobs",
      "#/jobs/extra",
      "#/tenant/Dev",
    ]) {
      expect(parseHash(h)).toEqual({ kind: "fleet" });
    }
  });
});

// ---------------------------------------------------------------------------
// The secrets canary, on the new seams
// ---------------------------------------------------------------------------

describe("no rendered value comes from a secret-named field (PR-G2.3 views)", () => {
  const LEAKED = /leaked-[a-z-]+-value-\d{4}/;

  it("OpFlow screens: plan, confirm, refusal with doctor findings, followed job", () => {
    const job = hostileJobFixture;
    const markup = [
      flowView({ kind: "planned", idempotencyKey: KEY, plan: hostilePlanFixture }),
      flowView({ kind: "confirming", idempotencyKey: KEY, plan: hostilePlanFixture, confirmValue: "dev" }),
      flowView({ kind: "confirming", idempotencyKey: KEY, plan: hostilePlanFixture, confirmValue: "dev" }, { typedOk: true }),
      flowView({ kind: "submitting", idempotencyKey: KEY, plan: hostilePlanFixture }),
      flowView({
        kind: "failed",
        idempotencyKey: KEY,
        error: new CtlError({
          status: 409,
          code: "doctor_red",
          detail: "API_KEYS=leaked-api-key-value-0301",
          requestId: "0123456789abcdef",
          extra: { job_id: "leaked-token-value-0302" },
        }),
        plan: hostilePlanFixture,
        jobId: null,
      }),
      flowView(
        { kind: "running", idempotencyKey: KEY, jobId: job.id, job: { ...job, state: "running" }, location: null },
        { logs: hostileStepLogsFixture },
      ),
      flowView({ kind: "done", idempotencyKey: KEY, job }, { logs: hostileStepLogsFixture }),
      flowView(
        { kind: "done", idempotencyKey: KEY, job: { ...jobFixture("succeeded"), op: "create" } },
        { revealLoad: async () => secretsFixture },
      ),
    ].join("\n");
    expect(markup).not.toMatch(LEAKED);
    for (const s of secretsFixture.secrets) expect(markup).not.toContain(s.value);
    // Not vacuous: the redacted rows and the hostile doctor finding are on screen.
    expect(markup).toContain("&lt;redacted&gt;");
    expect(markup).toContain("env_grammar");
    expect(markup).toContain("Reveal once");
  });

  it("jobs list and job page", () => {
    const markup = [
      render(createElement(JobsTable, { jobs: [hostileJobFixture], onSelect: noop })),
      render(
        createElement(JobPage, { id: hostileJobFixture.id, role: "operator", onBack: noop, onOpenJob: noop }),
        seedJob(hostileJobFixture, hostileStepLogsFixture),
      ),
      render(
        createElement(JobPage, { id: hostileJobFixture.id, role: "viewer", onBack: noop, onOpenJob: noop }),
        seedJob(hostileJobFixture),
      ),
    ].join("\n");
    expect(markup).not.toMatch(LEAKED);
    expect(markup).toContain("&lt;redacted&gt;");
    expect(markup).toContain("driver_failed");
  });
});
