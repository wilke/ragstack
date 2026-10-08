// OpFlow — the one state machine every admin mutation walks.
//
//   form → dry run → Plan → key prompt (+ typed confirm) → execute with the SAME
//   idempotency key → Job, polled until it settles.
//
// A PURE reducer: no React, no fetch, no clock, no randomness. The caller does
// the I/O (src/admin/api/ops.ts) and dispatches what happened; the reducer
// decides what the screen is. That is what makes every refusal path testable
// without a DOM and keeps the transitions in one place instead of in each view.
//
// What is deliberately NOT in this state: the ctl API key. It is typed into the
// key prompt, handed to `submitOp` as an argument for one call, and dropped. No
// event carries it and no state holds it, so neither a React devtools snapshot
// nor an error report built from this state can contain it. The typed confirm
// text is not here either — only the EXPECTED value (`confirmValue`), which the
// server already published in the plan.
//
// The idempotency key IS here: it is chosen once, when the flow starts planning,
// and reused for the execute (and for any re-plan after `plan_stale` or a
// `confirm_required`), because the daemon persists it only when a job is
// actually created — a refusal leaves it unspent — and a retry under the same
// key after an accepted job returns that job rather than a second one.

import type { CtlError } from "../api/http";
import type { Job, JobState, Plan } from "../api/types";

export type OpFlowState =
  | { kind: "idle" }
  /** The dry run is in flight. `stale` = re-planning after a 409 `plan_stale`. */
  | { kind: "planning"; idempotencyKey: string; stale: boolean }
  /** The plan is on screen; nothing has been authorized. */
  | { kind: "planned"; idempotencyKey: string; plan: Plan }
  /**
   * Key prompt (and, when `confirmValue` is non-null, the typed confirm). A
   * 428 `confirm_required` lands here with the server's `confirm_value`.
   */
  | { kind: "confirming"; idempotencyKey: string; plan: Plan; confirmValue: string | null }
  /** The execute call (`dry_run: false`) is in flight. */
  | { kind: "submitting"; idempotencyKey: string; plan: Plan }
  /** Accepted (202); `job` is the latest body seen. */
  | { kind: "running"; idempotencyKey: string; jobId: string; job: Job; location: string | null }
  /**
   * The job settled — `succeeded`, `failed`, `rolled_back`, `cancelled`, or
   * parked as `interrupted` (resumable). The job's own `error`/`rollback`
   * blocks say how; this is not a transport failure.
   */
  | { kind: "done"; idempotencyKey: string; job: Job }
  /**
   * The CALL was refused or failed (a CtlError). `jobId` is set when the
   * refusal names a job — the earlier job of a `duplicate`, the holder of a
   * `locked` — or when the failure happened while following one.
   */
  | { kind: "failed"; idempotencyKey: string | null; error: CtlError; plan: Plan | null; jobId: string | null };

export type OpFlowEvent =
  /** Start (or restart) the dry run. Pass a fresh key to begin a new mutation. */
  | { type: "plan"; idempotencyKey?: string }
  | { type: "planned"; plan: Plan }
  /** The operator accepted the plan: show the key prompt (+ typed confirm). */
  | { type: "confirm" }
  /** The operator submitted the key: the execute call is going out. */
  | { type: "submit" }
  | { type: "accepted"; job: Job; location: string | null }
  | { type: "jobUpdate"; job: Job }
  | { type: "error"; error: CtlError }
  | { type: "reset" };

export const OPFLOW_IDLE: OpFlowState = { kind: "idle" };

/** Job states that are still moving (or parked awaiting `continue`) — the ones worth polling. */
export const LIVE_JOB_STATES: readonly JobState[] = ["queued", "running", "awaiting_cutover"];

export function jobIsLive(state: JobState | null | undefined): boolean {
  return state != null && LIVE_JOB_STATES.includes(state);
}

function keyOf(s: OpFlowState): string | null {
  return s.kind === "idle" ? null : s.idempotencyKey;
}

function planOf(s: OpFlowState): Plan | null {
  return s.kind === "planned" || s.kind === "confirming" || s.kind === "submitting" ? s.plan : null;
}

function jobIdOf(s: OpFlowState): string | null {
  if (s.kind === "running") return s.jobId;
  if (s.kind === "done") return s.job.id;
  if (s.kind === "failed") return s.jobId;
  return null;
}

function extraString(error: CtlError, field: string): string | null {
  const v = error.extra?.[field];
  return typeof v === "string" && v.length > 0 ? v : null;
}

function settle(idempotencyKey: string, jobId: string, job: Job, location: string | null): OpFlowState {
  return jobIsLive(job.state)
    ? { kind: "running", idempotencyKey, jobId, job, location }
    : { kind: "done", idempotencyKey, job };
}

function onError(s: OpFlowState, error: CtlError): OpFlowState {
  const key = keyOf(s);
  const plan = planOf(s);
  const fail = (jobId: string | null = jobIdOf(s)): OpFlowState => ({
    kind: "failed",
    idempotencyKey: key,
    error,
    plan,
    jobId,
  });

  // Only the execute call can be stale or unconfirmed; the same codes from a
  // dry run (they do not happen) would have no plan to go back to.
  if (s.kind === "submitting" || s.kind === "confirming") {
    if (error.code === "plan_stale") {
      // The registry (or a finding) moved between the plan and the execute.
      // The old plan is no longer what would run, so it is dropped and the
      // dry run is re-run under the same key — the refusal did not spend it.
      return { kind: "planning", idempotencyKey: s.idempotencyKey, stale: true };
    }
    if (error.code === "confirm_required") {
      return {
        kind: "confirming",
        idempotencyKey: s.idempotencyKey,
        plan: s.plan,
        confirmValue: extraString(error, "confirm_value") ?? s.plan.confirm_value,
      };
    }
  }
  if (error.code === "duplicate" || error.code === "locked") {
    // `duplicate`: the key names an earlier, different request — link to it.
    // `locked`: another job holds the lock — show it.
    return fail(extraString(error, "job_id") ?? jobIdOf(s));
  }
  // locked/doctor_red/refused/validation and every other code (forbidden,
  // rate_limited, internal, a proxy error with no code): the error is kept so
  // ErrorBanner can render its code and request id.
  return fail();
}

/** The OpFlow transition function. Invalid events leave the state unchanged. */
export function opFlowReducer(s: OpFlowState, e: OpFlowEvent): OpFlowState {
  switch (e.type) {
    case "reset":
      return OPFLOW_IDLE;

    case "plan": {
      // Not while an execute is in flight or a job is being followed: those
      // end in `done`/`failed` (or `reset`) before a new dry run can start.
      if (s.kind === "submitting" || s.kind === "running") return s;
      const key = e.idempotencyKey ?? keyOf(s);
      if (!key) return s; // a flow cannot start without a key
      // A finished job's key is spent: re-using it would return that job.
      if (s.kind === "done" && !e.idempotencyKey) return s;
      return { kind: "planning", idempotencyKey: key, stale: false };
    }

    case "planned":
      if (s.kind !== "planning") return s;
      return { kind: "planned", idempotencyKey: s.idempotencyKey, plan: e.plan };

    case "confirm":
      if (s.kind !== "planned") return s;
      return {
        kind: "confirming",
        idempotencyKey: s.idempotencyKey,
        plan: s.plan,
        confirmValue: s.plan.requires_confirm ? s.plan.confirm_value : null,
      };

    case "submit":
      if (s.kind !== "confirming" && s.kind !== "planned") return s;
      return { kind: "submitting", idempotencyKey: s.idempotencyKey, plan: s.plan };

    case "accepted":
      if (s.kind !== "submitting") return s;
      return settle(s.idempotencyKey, e.job.id, e.job, e.location);

    case "jobUpdate":
      if (s.kind !== "running" || e.job.id !== s.jobId) return s;
      return settle(s.idempotencyKey, s.jobId, e.job, s.location);

    case "error":
      if (s.kind === "idle" || s.kind === "done" || s.kind === "failed") return s;
      return onError(s, e.error);
  }
}
