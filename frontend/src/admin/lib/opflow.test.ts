import { describe, expect, it } from "vitest";
import { CtlError } from "../api/http";
import type { CtlErrorCode, Job, Plan } from "../api/types";
import { typedConfirmed } from "./confirm";
import { OPFLOW_IDLE, jobIsLive, opFlowReducer, type OpFlowEvent, type OpFlowState } from "./opflow";

// The brief's `plan.test.ts`: OpFlow's state machine, every transition and
// every refusal code, as a pure function — no DOM, no network.

const KEY = "ui-restart-00000000-0000-4000-8000-000000000000";
const KEY2 = "ui-restart-11111111-1111-4111-8111-111111111111";

const PLAN = {
  plan_hash: "sha256:" + "0".repeat(64),
  op: "stop",
  tenant: "dev",
  registry_generation: 7,
  schema_version: 1,
  doctor: { status: "green", findings: [] },
  requires_confirm: false,
  confirm_value: null,
  steps: [],
  warnings: [],
} as unknown as Plan;

const DESTRUCTIVE = { ...PLAN, requires_confirm: true, confirm_value: "dev" } as Plan;

function job(state: Job["state"], id = "01JAAAAAAAAAAAAAAAAAAAAAAA"): Job {
  return { id, state, op: "stop", tenant: "dev", steps: [] } as unknown as Job;
}

function err(code: CtlErrorCode | null, status = 409, extra: Record<string, unknown> | null = null): CtlError {
  return new CtlError({ status, code, detail: "server prose", requestId: "abcdabcdabcdabcd", extra });
}

function run(events: OpFlowEvent[], from: OpFlowState = OPFLOW_IDLE): OpFlowState {
  return events.reduce(opFlowReducer, from);
}

const toPlanned: OpFlowEvent[] = [
  { type: "plan", idempotencyKey: KEY },
  { type: "planned", plan: PLAN },
];
const toSubmitting: OpFlowEvent[] = [...toPlanned, { type: "confirm" }, { type: "submit" }];

describe("OpFlow — the happy path", () => {
  it("idle → planning → planned → confirming → submitting → running → done", () => {
    let s = opFlowReducer(OPFLOW_IDLE, { type: "plan", idempotencyKey: KEY });
    expect(s).toEqual({ kind: "planning", idempotencyKey: KEY, stale: false });

    s = opFlowReducer(s, { type: "planned", plan: PLAN });
    expect(s).toEqual({ kind: "planned", idempotencyKey: KEY, plan: PLAN });

    s = opFlowReducer(s, { type: "confirm" });
    expect(s).toEqual({ kind: "confirming", idempotencyKey: KEY, plan: PLAN, confirmValue: null });

    s = opFlowReducer(s, { type: "submit" });
    expect(s).toEqual({ kind: "submitting", idempotencyKey: KEY, plan: PLAN });

    const queued = job("queued");
    s = opFlowReducer(s, { type: "accepted", job: queued, location: `/v1/jobs/${queued.id}` });
    expect(s).toEqual({
      kind: "running",
      idempotencyKey: KEY,
      jobId: queued.id,
      job: queued,
      location: `/v1/jobs/${queued.id}`,
    });

    const running = job("running");
    s = opFlowReducer(s, { type: "jobUpdate", job: running });
    expect(s).toMatchObject({ kind: "running", job: running });

    const done = job("succeeded");
    s = opFlowReducer(s, { type: "jobUpdate", job: done });
    expect(s).toEqual({ kind: "done", idempotencyKey: KEY, job: done });
  });

  it("a destructive plan carries its confirm_value into confirming", () => {
    const s = run([{ type: "plan", idempotencyKey: KEY }, { type: "planned", plan: DESTRUCTIVE }, { type: "confirm" }]);
    expect(s).toMatchObject({ kind: "confirming", confirmValue: "dev" });
  });

  it("submit straight from planned is allowed (no typed confirm needed)", () => {
    expect(run([...toPlanned, { type: "submit" }])).toMatchObject({ kind: "submitting", idempotencyKey: KEY });
  });

  it("keeps ONE idempotency key from the dry run to the execute", () => {
    const s = run([...toSubmitting, { type: "accepted", job: job("queued"), location: null }]);
    expect(s).toMatchObject({ idempotencyKey: KEY });
  });

  it("awaiting_cutover keeps running; every settled state is done", () => {
    const running = run([...toSubmitting, { type: "accepted", job: job("running"), location: null }]);
    expect(opFlowReducer(running, { type: "jobUpdate", job: job("awaiting_cutover") }).kind).toBe("running");
    for (const st of ["succeeded", "failed", "rolled_back", "interrupted", "cancelled"] as const) {
      expect(opFlowReducer(running, { type: "jobUpdate", job: job(st) })).toMatchObject({ kind: "done", job: { state: st } });
    }
  });

  it("an accepted job that has already settled (an idempotent replay) goes straight to done", () => {
    const s = run([...toSubmitting, { type: "accepted", job: job("succeeded"), location: null }]);
    expect(s.kind).toBe("done");
  });

  it("ignores a jobUpdate for a different job", () => {
    const running = run([...toSubmitting, { type: "accepted", job: job("running"), location: null }]);
    expect(opFlowReducer(running, { type: "jobUpdate", job: job("succeeded", "01JBBBBBBBBBBBBBBBBBBBBBBB") })).toBe(running);
  });

  it("jobIsLive is exactly queued/running/awaiting_cutover", () => {
    expect(["queued", "running", "awaiting_cutover"].every((s) => jobIsLive(s as Job["state"]))).toBe(true);
    expect(jobIsLive("succeeded")).toBe(false);
    expect(jobIsLive(undefined)).toBe(false);
  });
});

describe("OpFlow — refusals by code", () => {
  it("plan_stale → back to planning (re-run the dry run) under the same key, plan dropped", () => {
    const s = run([...toSubmitting, { type: "error", error: err("plan_stale") }]);
    expect(s).toEqual({ kind: "planning", idempotencyKey: KEY, stale: true });
    // and the re-run lands a fresh plan
    const fresh = { ...PLAN, registry_generation: 8 } as Plan;
    expect(opFlowReducer(s, { type: "planned", plan: fresh })).toEqual({ kind: "planned", idempotencyKey: KEY, plan: fresh });
  });

  it("confirm_required → confirming with extra.confirm_value", () => {
    const s = run([...toSubmitting, { type: "error", error: err("confirm_required", 428, { confirm_value: "dev" }) }]);
    expect(s).toEqual({ kind: "confirming", idempotencyKey: KEY, plan: PLAN, confirmValue: "dev" });
  });

  it("confirm_required without extra falls back to the plan's confirm_value", () => {
    const s = run([
      { type: "plan", idempotencyKey: KEY },
      { type: "planned", plan: DESTRUCTIVE },
      { type: "submit" },
      { type: "error", error: err("confirm_required", 428) },
    ]);
    expect(s).toMatchObject({ kind: "confirming", confirmValue: "dev" });
  });

  it.each(["locked", "doctor_red", "refused"] as const)("%s → failed with the error retained", (code) => {
    const e = err(code, 409, code === "locked" ? { job_id: "01JHOLDERHOLDERHOLDERHOLDE", since: "t" } : null);
    const s = run([...toSubmitting, { type: "error", error: e }]);
    expect(s).toMatchObject({ kind: "failed", idempotencyKey: KEY, plan: PLAN });
    expect(s.kind === "failed" && s.error).toBe(e);
    if (code === "locked") expect(s).toMatchObject({ jobId: "01JHOLDERHOLDERHOLDERHOLDE" });
    else expect(s).toMatchObject({ jobId: null });
  });

  it("validation (422) → failed with the error retained", () => {
    const e = err("validation", 422, { fields: ["args.only"] });
    const s = run([...toSubmitting, { type: "error", error: e }]);
    expect(s).toMatchObject({ kind: "failed", jobId: null });
    expect(s.kind === "failed" && s.error.extra).toEqual({ fields: ["args.only"] });
  });

  it("duplicate → failed, keeping the earlier job id from extra", () => {
    const s = run([...toSubmitting, { type: "error", error: err("duplicate", 409, { job_id: "01JEARLIEREARLIEREARLIEREA" }) }]);
    expect(s).toMatchObject({ kind: "failed", jobId: "01JEARLIEREARLIEREARLIEREA" });
  });

  it("duplicate without extra.job_id → failed with no job id", () => {
    const s = run([...toSubmitting, { type: "error", error: err("duplicate") }]);
    expect(s).toMatchObject({ kind: "failed", jobId: null });
  });

  it.each([
    ["forbidden", 403],
    ["auth_required", 401],
    ["rate_limited", 429],
    ["internal", 500],
    [null, 502],
  ] as const)("any other failure (%s) → failed", (code, status) => {
    expect(run([...toSubmitting, { type: "error", error: err(code, status) }]).kind).toBe("failed");
  });

  it("a dry run that errors fails (plan_stale/confirm_required only mean something on execute)", () => {
    for (const code of ["plan_stale", "confirm_required", "validation", "forbidden"] as const) {
      const s = run([{ type: "plan", idempotencyKey: KEY }, { type: "error", error: err(code) }]);
      expect(s).toMatchObject({ kind: "failed", plan: null, idempotencyKey: KEY });
    }
  });

  it("an error while following a job fails but keeps the job id", () => {
    const running = run([...toSubmitting, { type: "accepted", job: job("running"), location: null }]);
    expect(opFlowReducer(running, { type: "error", error: err("internal", 500) })).toMatchObject({
      kind: "failed",
      jobId: job("running").id,
    });
  });

  it("errors are ignored in idle, done and failed", () => {
    const done = run([...toSubmitting, { type: "accepted", job: job("succeeded"), location: null }]);
    const failed = run([...toSubmitting, { type: "error", error: err("refused") }]);
    for (const s of [OPFLOW_IDLE, done, failed]) {
      expect(opFlowReducer(s, { type: "error", error: err("internal", 500) })).toBe(s);
    }
  });
});

describe("OpFlow — guards and restarts", () => {
  it("plan needs a key to start from idle", () => {
    expect(opFlowReducer(OPFLOW_IDLE, { type: "plan" })).toBe(OPFLOW_IDLE);
  });

  it("re-planning from planned/confirming/failed keeps the key unless a new one is given", () => {
    const planned = run(toPlanned);
    expect(opFlowReducer(planned, { type: "plan" })).toMatchObject({ kind: "planning", idempotencyKey: KEY });
    expect(opFlowReducer(planned, { type: "plan", idempotencyKey: KEY2 })).toMatchObject({ idempotencyKey: KEY2 });
    const failed = run([...toSubmitting, { type: "error", error: err("locked") }]);
    expect(opFlowReducer(failed, { type: "plan" })).toMatchObject({ kind: "planning", idempotencyKey: KEY });
  });

  it("a finished job's key is spent: a new plan from done needs a new key", () => {
    const done = run([...toSubmitting, { type: "accepted", job: job("succeeded"), location: null }]);
    expect(opFlowReducer(done, { type: "plan" })).toBe(done);
    expect(opFlowReducer(done, { type: "plan", idempotencyKey: KEY2 })).toEqual({
      kind: "planning",
      idempotencyKey: KEY2,
      stale: false,
    });
  });

  it("no plan while an execute is in flight or a job is followed", () => {
    const submitting = run(toSubmitting);
    expect(opFlowReducer(submitting, { type: "plan", idempotencyKey: KEY2 })).toBe(submitting);
    const running = run([...toSubmitting, { type: "accepted", job: job("running"), location: null }]);
    expect(opFlowReducer(running, { type: "plan", idempotencyKey: KEY2 })).toBe(running);
  });

  it("out-of-order events leave the state unchanged", () => {
    const planning = run([{ type: "plan", idempotencyKey: KEY }]);
    const cases: [OpFlowState, OpFlowEvent][] = [
      [OPFLOW_IDLE, { type: "planned", plan: PLAN }],
      [OPFLOW_IDLE, { type: "confirm" }],
      [OPFLOW_IDLE, { type: "submit" }],
      [OPFLOW_IDLE, { type: "accepted", job: job("queued"), location: null }],
      [OPFLOW_IDLE, { type: "jobUpdate", job: job("queued") }],
      [planning, { type: "confirm" }],
      [planning, { type: "submit" }],
      [planning, { type: "accepted", job: job("queued"), location: null }],
      [run(toPlanned), { type: "accepted", job: job("queued"), location: null }],
      [run(toPlanned), { type: "planned", plan: PLAN }],
    ];
    for (const [s, e] of cases) expect(opFlowReducer(s, e)).toBe(s);
  });

  it("reset returns to idle from anywhere", () => {
    for (const s of [run(toPlanned), run(toSubmitting), run([...toSubmitting, { type: "error", error: err("refused") }])]) {
      expect(opFlowReducer(s, { type: "reset" })).toBe(OPFLOW_IDLE);
    }
  });

  it("no state ever holds a ctl key — events carry none", () => {
    // A structural check: the states the happy path walks contain only the
    // fields the reducer defines.
    const s = run([...toSubmitting, { type: "accepted", job: job("queued"), location: null }]);
    expect(Object.keys(s).sort()).toEqual(["idempotencyKey", "job", "jobId", "kind", "location"]);
  });
});

describe("typedConfirmed", () => {
  it("is exact apart from surrounding whitespace", () => {
    expect(typedConfirmed("dev", "dev")).toBe(true);
    expect(typedConfirmed("  dev \n", "dev")).toBe(true);
    expect(typedConfirmed("Dev", "dev")).toBe(false);
    expect(typedConfirmed("de v", "dev")).toBe(false);
    expect(typedConfirmed("dev2", "dev")).toBe(false);
  });

  it("an empty expected value never confirms", () => {
    expect(typedConfirmed("", "")).toBe(false);
    expect(typedConfirmed("  ", "")).toBe(false);
  });
});
