// OpFlow — one mutation, end to end, the same way for every verb.
//
//   [form] → Preview → KEY (dry run) → PlanView → Run → [TypedConfirm] → KEY
//   (execute, same idempotency key) → JobView polled every 2 s → RevealOnce
//   when the job minted credentials.
//
// The transitions are `lib/opflow.ts`'s pure reducer; this file adds the I/O
// and the screens. Three rules shape it:
//
//   * The contract requires `ctl_api_key` on EVERY mutating POST, dry runs
//     included (op_request.json; a session authenticates reads only). So the
//     key is asked for twice — once to plan, once to run — and NOT kept in
//     between: holding it across the plan review would put a live credential
//     in component state for as long as the operator reads.
//   * The key's only path is `KeyPrompt` → `spendKey` (input wiped before the
//     callback) → `driveWithKey` → the caller's `run` → ops.ts's request body.
//     It is a function argument at every hop: no state, ref, event, context or
//     storage holds it, which `OpFlow.test.ts` asserts over every state and
//     event of a flow.
//   * Server prose is never rendered: refusals go through `ErrorBanner`
//     (code + request id), the plan and job through their redacting views.
//
// Planless flows (`planless`): the job continuations have no dry run — the
// daemon refuses `dry_run: true` there (the plan is the job's own) — so they
// start at the key prompt, and a cancel that would roll back gets its typed
// confirm from the 428 `confirm_required` the daemon answers.
//
// `OpFlowView` is the props-only seam the string-render tests mount in every
// state; `OpFlow` is the stateful wrapper.

import { useEffect, useReducer, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CtlError } from "../api/http";
import { fetchSecrets, newIdempotencyKey, type MutationOutcome } from "../api/ops";
import { ctlKeys, useJob, useStepLogs } from "../api/queries";
import type { Job, Plan, SecretsResponse } from "../api/types";
import {
  OPFLOW_IDLE,
  opFlowReducer,
  type OpFlowEvent,
  type OpFlowState,
} from "../lib/opflow";
import { ErrorBanner } from "./ErrorBanner";
import { DoctorFindings } from "./FleetView";
import { JobView } from "./JobView";
import { KeyPrompt } from "./KeyPrompt";
import { PlanView } from "./PlanView";
import { RevealOnceCard } from "./RevealOnce";
import { TypedConfirm } from "./TypedConfirm";

const EYEBROW = "font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const PRIMARY =
  "rounded-panel bg-ink-900 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40";
const SECONDARY =
  "rounded-panel border border-line bg-white px-3 py-1.5 text-[12.5px] font-medium text-strong hover:bg-paper";

/** What `run` receives: one call's worth of authorization, never kept. */
export interface OpRunRequest {
  ctlKey: string;
  dryRun: boolean;
  confirm?: string;
  forceWithDoctorDiff?: string;
  idempotencyKey: string;
}

export type OpRun = (req: OpRunRequest) => Promise<MutationOutcome>;

/** The ops whose success leaves minted values in the one-shot envelope. */
export const SECRET_BEARING_OPS: readonly string[] = ["create", "key-mint", "restore"];

/**
 * Whether a settled job has secrets to collect: a succeeded secret-bearing op,
 * unless its `result` says outright that none were minted.
 */
export function jobDeliversSecrets(job: Job): boolean {
  if (job.state !== "succeeded" || !SECRET_BEARING_OPS.includes(job.op)) return false;
  return job.result?.secrets_available !== false;
}

function unexpected(detail: string): CtlError {
  return new CtlError({ status: 0, code: null, detail, requestId: null });
}

function asCtlError(err: unknown): CtlError {
  return err instanceof CtlError ? err : unexpected("the request did not complete");
}

/**
 * The request a key is about to authorize, for the state the flow is in —
 * WITHOUT the key, which the caller adds as the call goes out. `null` when the
 * state takes no key (nothing to send).
 */
export function requestFor(
  state: OpFlowState,
  opts: { forceYellow?: boolean } = {},
): Omit<OpRunRequest, "ctlKey"> | null {
  if (state.kind === "planning") {
    return { dryRun: true, idempotencyKey: state.idempotencyKey };
  }
  if (state.kind === "confirming") {
    const req: Omit<OpRunRequest, "ctlKey"> = { dryRun: false, idempotencyKey: state.idempotencyKey };
    // Only a value the operator has just TYPED reaches here (the view shows
    // the key prompt after TypedConfirm unlocks); it is the plan's — or the
    // 428's — `confirm_value`, which the daemon compares exactly.
    if (state.confirmValue !== null) req.confirm = state.confirmValue;
    const doctor = state.plan?.doctor;
    if (opts.forceYellow && doctor && doctor.status === "yellow") req.forceWithDoctorDiff = doctor.hash;
    return req;
  }
  return null;
}

/**
 * Spend one key on the call the current state is waiting for, and report what
 * happened as reducer events. The key is an argument here and in `run`, and is
 * dropped when this returns; no event carries it.
 */
export async function driveWithKey(
  state: OpFlowState,
  key: string,
  run: OpRun,
  dispatch: (e: OpFlowEvent) => void,
  opts: { forceYellow?: boolean } = {},
): Promise<void> {
  const base = requestFor(state, opts);
  if (!base) return;
  if (!base.dryRun) dispatch({ type: "submit" });
  try {
    const out = await run({ ...base, ctlKey: key });
    if (base.dryRun) {
      if (out.kind === "plan") dispatch({ type: "planned", plan: out.plan });
      else dispatch({ type: "error", error: unexpected("a dry run answered with a job") });
    } else if (out.kind === "job") {
      dispatch({ type: "accepted", job: out.job, location: out.location });
    } else {
      dispatch({ type: "error", error: unexpected("an execute answered with a plan") });
    }
  } catch (err) {
    dispatch({ type: "error", error: asCtlError(err) });
  }
}

function Refusal({
  error,
  plan,
  onOpenJob,
}: {
  error: CtlError;
  plan: Plan | null;
  onOpenJob?: (id: string) => void;
}) {
  if (error.status === 403) {
    // A 403 on a mutation is about the KEY, not the session (ops.ts): it must
    // be an operator key, and it must belong to the signed-in subject.
    return (
      <div role="alert" className="mt-3 rounded-card bg-accent-soft p-3 text-[12.5px] text-accent-text">
        <div className="font-medium">Operator credential required.</div>
        <div className="mt-1">
          The control plane refused this key for this change: it must be an operator ctl key that
          belongs to the subject you are signed in as. Nothing was changed.
        </div>
        {error.requestId && (
          <div className="mt-1 font-mono text-[11px] opacity-80">Reference: {error.requestId}</div>
        )}
      </div>
    );
  }
  return (
    <>
      <ErrorBanner error={error} onOpenJob={onOpenJob} />
      {error.code === "doctor_red" && plan && (
        <div className="mt-3">
          <div className={EYEBROW}>doctor findings for this plan</div>
          <DoctorFindings doctor={plan.doctor} />
        </div>
      )}
    </>
  );
}

export interface OpFlowViewProps {
  state: OpFlowState;
  title: string;
  /** The verb's own danger, on top of the plan's destructive steps. */
  destructive?: boolean;
  planless?: boolean;
  /** A call is in flight (the key has been spent). */
  busy?: boolean;
  /** The typed confirm for the current `confirming` state has been passed. */
  typedOk?: boolean;
  /** Proceed past a YELLOW plan's warnings (`force_with_doctor_diff`). */
  forceYellow?: boolean;
  /** The form is invalid (or not yet ready): Preview stays disabled. */
  disabled?: boolean;
  /** Why Preview is disabled, when it is. */
  disabledReason?: string;
  previewLabel?: string;
  /** Follow the accepted job here (default) or hand it back via `onDone`. */
  follow?: boolean;
  logs?: Readonly<Record<number, readonly string[]>>;
  onPreview: () => void;
  onSpend: (key: string) => void | Promise<void>;
  onAccept: () => void;
  onTyped: () => void;
  onForceYellow: (on: boolean) => void;
  onRetry: () => void;
  onReset: () => void;
  onOpenJob?: (id: string) => void;
  /** The one secrets read, for a secret-bearing success. */
  revealLoad?: (key: string) => Promise<SecretsResponse>;
  /** The form: shown (and editable) only before a flow starts. */
  children?: ReactNode;
}

export function OpFlowView(p: OpFlowViewProps) {
  const s = p.state;
  const plan = s.kind === "planned" || s.kind === "confirming" || s.kind === "submitting" ? s.plan : null;
  const destructive = Boolean(p.destructive) || Boolean(plan?.steps.some((st) => st.destructive));
  const startLabel = p.previewLabel ?? (p.planless ? "Authorize…" : "Preview");

  return (
    <section aria-label={`operation: ${p.title}`} className="rounded-card border border-line bg-white p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className={EYEBROW}>operation</span>
        <span className={`text-[13px] font-semibold ${p.destructive ? "text-rust" : "text-strong"}`}>
          {p.title}
        </span>
        {s.kind !== "idle" && (
          <span className="font-mono text-[10.5px] text-dim">{s.kind}</span>
        )}
      </div>

      {p.children && (
        <fieldset disabled={s.kind !== "idle"} className="mb-3 disabled:opacity-60">
          {p.children}
        </fieldset>
      )}

      {s.kind === "idle" && (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={p.onPreview} disabled={p.disabled} className={PRIMARY}>
            {startLabel}
          </button>
          <span className="text-[11.5px] text-dim">
            {p.disabled && p.disabledReason
              ? p.disabledReason
              : p.planless
                ? "Asks for your ctl API key; nothing is sent before you submit it."
                : "A dry run: shows the plan and changes nothing. It still asks for your ctl API key."}
          </span>
        </div>
      )}

      {s.kind === "planning" && (
        <div className="space-y-3">
          {s.stale && (
            <p role="status" className="rounded-card bg-accent-soft p-3 text-[12.5px] text-accent-text">
              The plan changed after you reviewed it — the registry moved or a doctor finding
              changed. Plan again and review the new one before running it.
            </p>
          )}
          <KeyPrompt onSubmit={p.onSpend} busy={p.busy} title="ctl API key for the dry run">
            The dry run changes nothing, but the control plane re-authorizes every request that
            could: present your key for this one. It is not kept; running the plan asks again.
          </KeyPrompt>
          <button type="button" onClick={p.onReset} className={SECONDARY}>
            Cancel
          </button>
        </div>
      )}

      {plan && <PlanView plan={plan} />}

      {s.kind === "planned" && (
        <div className="mt-3 space-y-3">
          {s.plan.doctor.status === "yellow" && (
            <label className="flex items-start gap-2 text-[12.5px] text-body">
              <input
                type="checkbox"
                checked={Boolean(p.forceYellow)}
                onChange={(e) => p.onForceYellow(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                Proceed past the doctor warnings above (sends this plan's doctor hash as{" "}
                <code className="font-mono">force_with_doctor_diff</code>; a warning this op
                depends on refuses the run otherwise). Never bypasses a red finding.
              </span>
            </label>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={p.onAccept}
              disabled={s.plan.doctor.status === "red"}
              title={s.plan.doctor.status === "red" ? "Doctor is red: the run would be refused." : undefined}
              className={destructive ? `${PRIMARY} !bg-rust` : PRIMARY}
            >
              {destructive ? "Run this destructive plan…" : "Run this plan…"}
            </button>
            <button type="button" onClick={p.onReset} className={SECONDARY}>
              Discard
            </button>
          </div>
        </div>
      )}

      {s.kind === "confirming" && (
        <div className="mt-3 space-y-3">
          {s.confirmValue !== null && !p.typedOk ? (
            <TypedConfirm
              expected={s.confirmValue}
              destructive={destructive}
              warnings={s.plan?.warnings ?? []}
              onConfirm={p.onTyped}
              onCancel={p.onReset}
            />
          ) : (
            <>
              <KeyPrompt onSubmit={p.onSpend} busy={p.busy} title="ctl API key to run this">
                {p.planless
                  ? "This changes a job: present your ctl API key for this one request."
                  : "Running the plan above: present your ctl API key for this one request. The execute pins the plan hash you reviewed."}
              </KeyPrompt>
              <button type="button" onClick={p.onReset} className={SECONDARY}>
                Cancel
              </button>
            </>
          )}
        </div>
      )}

      {s.kind === "submitting" && (
        <p role="status" className="mt-3 text-[12.5px] text-dim">
          Submitting…
        </p>
      )}

      {(s.kind === "running" || s.kind === "done") && p.follow === false && (
        <div role="status" className="mt-1 flex flex-wrap items-center gap-3 text-[12.5px] text-body">
          <span>
            Accepted as job <code className="font-mono">{s.kind === "running" ? s.jobId : s.job.id}</code>.
          </span>
          <button type="button" onClick={p.onReset} className={SECONDARY}>
            Close
          </button>
        </div>
      )}

      {(s.kind === "running" || s.kind === "done") && p.follow !== false && (
        <div className="mt-3 space-y-3">
          <JobView job={s.job} role="operator" logs={p.logs} controls={false} />
          {s.kind === "done" && jobDeliversSecrets(s.job) && p.revealLoad && (
            <RevealOnceCard load={p.revealLoad} />
          )}
          <div className="flex flex-wrap items-center gap-2">
            {p.onOpenJob && (
              <button
                type="button"
                onClick={() => p.onOpenJob?.(s.kind === "running" ? s.jobId : s.job.id)}
                className={SECONDARY}
              >
                Open job page
              </button>
            )}
            {s.kind === "done" && (
              <button type="button" onClick={p.onReset} className={SECONDARY}>
                Done
              </button>
            )}
          </div>
        </div>
      )}

      {s.kind === "failed" && (
        <div className="mt-1 space-y-3">
          <Refusal error={s.error} plan={s.plan} onOpenJob={p.onOpenJob} />
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={p.onRetry} className={SECONDARY}>
              {p.planless ? "Try again" : "Plan again"}
            </button>
            <button type="button" onClick={p.onReset} className={SECONDARY}>
              Start over
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

export interface OpFlowProps {
  /** The mutation: ops.ts's `submitOp`/`applyGateway`/`jobContinuation` with the verb's args bound. */
  run: OpRun;
  title: string;
  /** Names the op in the idempotency key (`ui-<op>-<uuid>`), for the audit reader. */
  op?: string;
  destructive?: boolean;
  /**
   * The expected typed confirmation when the daemon will not name one: for a
   * planned flow, the fallback when a `requires_confirm` plan carries no
   * `confirm_value`; for a planless flow, a typed confirm asked BEFORE the key.
   */
  confirmValue?: string;
  /** A job continuation: no dry run, straight to the key prompt. */
  planless?: boolean;
  /** Start at the key prompt on mount (planless only) — the click that opened the flow was the intent. */
  autoStart?: boolean;
  /** `false`: do not follow the job here; `onDone` fires on acceptance. */
  follow?: boolean;
  disabled?: boolean;
  disabledReason?: string;
  previewLabel?: string;
  /** The job settled (or, with `follow: false`, was accepted). */
  onDone?: (job: Job) => void;
  onOpenJob?: (id: string) => void;
  /** Called by Start over / Close / Cancel, after the flow is back to idle. */
  onClose?: () => void;
  children?: ReactNode;
}

function initial(props: OpFlowProps): OpFlowState {
  if (props.planless && props.autoStart) {
    return opFlowReducer(OPFLOW_IDLE, {
      type: "direct",
      idempotencyKey: newIdempotencyKey(props.op ?? "op"),
      confirmValue: props.confirmValue ?? null,
    });
  }
  return OPFLOW_IDLE;
}

export function OpFlow(props: OpFlowProps) {
  const qc = useQueryClient();
  const [state, rawDispatch] = useReducer(opFlowReducer, props, initial);
  const [busy, setBusy] = useState(false);
  const [typedOk, setTypedOk] = useState(false);
  const [forceYellow, setForceYellow] = useState(false);
  const op = props.op ?? "op";

  // Every event goes through here: a refusal resets the typed confirm (a 428
  // asks for a value afresh), and an accepted job seeds its own poll.
  function dispatch(e: OpFlowEvent) {
    if (e.type === "error" || e.type === "reset" || e.type === "plan" || e.type === "direct") {
      setTypedOk(false);
    }
    if (e.type === "plan" || e.type === "reset" || e.type === "direct") setForceYellow(false);
    if (e.type === "accepted") {
      qc.setQueryData(ctlKeys.job(e.job.id), e.job);
      void qc.invalidateQueries({ queryKey: ["ctl", "jobs"] });
    }
    rawDispatch(e);
  }

  const jobId = state.kind === "running" ? state.jobId : null;
  const followed = useJob(jobId, { enabled: state.kind === "running" && props.follow !== false });
  useEffect(() => {
    if (state.kind === "running" && followed.data && followed.data.id === state.jobId) {
      rawDispatch({ type: "jobUpdate", job: followed.data });
    }
    // `state` is read for its kind/id only; the poll's data is the trigger.
  }, [followed.data]);

  const visibleJob = state.kind === "running" || state.kind === "done" ? state.job : undefined;
  const logs = useStepLogs(visibleJob, props.follow !== false);

  // onDone once per job: on settling (following) or on acceptance (not).
  const reported = useRef<string | null>(null);
  useEffect(() => {
    const settled =
      state.kind === "done" ? state.job : state.kind === "running" && props.follow === false ? state.job : null;
    if (settled && reported.current !== settled.id) {
      reported.current = settled.id;
      // What a finished job changed is now stale everywhere it is shown.
      void qc.invalidateQueries({ queryKey: ["ctl", "fleet"] });
      void qc.invalidateQueries({ queryKey: ["ctl", "jobs"] });
      void qc.invalidateQueries({ queryKey: ["ctl", "gateway"] });
      if (settled.tenant) void qc.invalidateQueries({ queryKey: ctlKeys.tenant(settled.tenant) });
      props.onDone?.(settled);
    }
  }, [state, props, qc]);

  // A requires_confirm plan whose confirm_value is missing would otherwise be
  // unconfirmable; the caller's `confirmValue` fills that one gap.
  const shown: OpFlowState =
    state.kind === "confirming" && state.confirmValue === null && state.plan?.requires_confirm && props.confirmValue
      ? { ...state, confirmValue: props.confirmValue }
      : state;

  async function spend(key: string) {
    setBusy(true);
    try {
      await driveWithKey(shown, key, props.run, dispatch, { forceYellow });
    } finally {
      setBusy(false);
    }
  }

  function start() {
    reported.current = null;
    if (props.planless) {
      dispatch({ type: "direct", idempotencyKey: newIdempotencyKey(op), confirmValue: props.confirmValue ?? null });
    } else {
      dispatch({ type: "plan", idempotencyKey: newIdempotencyKey(op) });
    }
  }

  function retry() {
    if (state.kind !== "failed") return;
    // A refusal leaves the idempotency key unspent, so a retry reuses it —
    // except after `duplicate`, which says the key already names another
    // request: that one needs a fresh key.
    const key =
      state.error.code === "duplicate" || !state.idempotencyKey ? newIdempotencyKey(op) : state.idempotencyKey;
    if (props.planless) {
      dispatch({ type: "direct", idempotencyKey: key, confirmValue: props.confirmValue ?? null });
    } else {
      dispatch({ type: "plan", idempotencyKey: key });
    }
  }

  function reset() {
    dispatch({ type: "reset" });
    props.onClose?.();
  }

  return (
    <OpFlowView
      state={shown}
      title={props.title}
      destructive={props.destructive}
      planless={props.planless}
      busy={busy}
      typedOk={typedOk}
      forceYellow={forceYellow}
      disabled={props.disabled}
      disabledReason={props.disabledReason}
      previewLabel={props.previewLabel}
      follow={props.follow}
      logs={logs}
      onPreview={start}
      onSpend={(key) => spend(key)}
      onAccept={() => dispatch({ type: "confirm" })}
      onTyped={() => setTypedOk(true)}
      onForceYellow={setForceYellow}
      onRetry={retry}
      onReset={reset}
      onOpenJob={props.onOpenJob}
      revealLoad={
        state.kind === "done" ? (key: string) => fetchSecrets(state.job.id, key) : undefined
      }
    >
      {props.children}
    </OpFlowView>
  );
}
