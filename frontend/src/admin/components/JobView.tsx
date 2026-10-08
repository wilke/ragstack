// One job: its state, its steps, and — for an operator — the way forward.
//
// Pure and props-only: `useJob` (PR-G2.1) owns the poll, and the parent owns
// the continuation calls. The three buttons only CALL BACK; each continuation is
// a mutation, so the parent routes the callback through OpFlow (a KeyPrompt,
// and for cancel the TypedConfirm the daemon asks for) before anything is sent.
//
// Which button appears is decided by the job's state, from the contract:
//   resume   — `interrupted` (reconcile-on-restart parked it; resources held)
//   continue — `awaiting_cutover` (handover / migrate-local waiting on purpose)
//   cancel   — `queued`, `running`, `awaiting_cutover`
// and only for an operator: a viewer sees the job and no controls at all.
//
// A step's `log` is a PATH (read through `GET /v1/jobs/{id}/steps/{n}/log`); the
// lines are passed in `logs`, keyed by step number, and shown to operators
// only. Every server string goes through `redact.ts` and is printed as text.

import { redactMaybe, redactText } from "./redact";
import type { JobState, Job, Step } from "../api/types";
import { StateChip } from "./StateChip";

const EYEBROW = "font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const BUTTON =
  "rounded-panel border border-line bg-white px-3 py-1.5 text-[12px] font-medium text-strong hover:bg-paper";

export type JobRole = "viewer" | "operator";
export type JobAction = "resume" | "continue" | "cancel";

const ACTIONS: Record<JobAction, readonly JobState[]> = {
  resume: ["interrupted"],
  continue: ["awaiting_cutover"],
  cancel: ["queued", "running", "awaiting_cutover"],
};

/** The continuations a role may take on a job in `state`, in display order. */
export function jobActions(state: JobState, role: JobRole): JobAction[] {
  if (role !== "operator") return [];
  return (Object.keys(ACTIONS) as JobAction[]).filter((a) => ACTIONS[a].includes(state));
}

/** Whether a job in `state` is still moving (the poll's stop condition). */
export function jobInFlight(state: JobState): boolean {
  return state === "queued" || state === "running";
}

/** "1 min 05 s" between two RFC 3339 stamps; "—" when either is missing. */
export function duration(from: string | null, to: string | null): string {
  if (!from || !to) return "—";
  const a = Date.parse(from);
  const b = Date.parse(to);
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return "—";
  const s = Math.round((b - a) / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className={`mb-1 ${EYEBROW}`}>{label}</div>
      <div className="font-mono text-[12px] text-strong">{children}</div>
    </div>
  );
}

function Stamp({ at }: { at: string | null }) {
  if (!at) return <span className="text-faint">—</span>;
  return <span title={at}>{at.replace("T", " ").replace("Z", " UTC")}</span>;
}

function StepItem({
  s,
  current,
  role,
  log,
}: {
  s: Step;
  current: boolean;
  role: JobRole;
  log: readonly string[] | undefined;
}) {
  const operator = role === "operator";
  const error = redactMaybe(s.error);
  return (
    <li className="relative border-l-2 border-line pb-4 pl-5 last:pb-0">
      <span
        aria-hidden="true"
        className={`absolute -left-[6px] top-1 h-[10px] w-[10px] rounded-full border-2 border-white ${
          current
            ? "bg-link"
            : s.state === "failed"
              ? "bg-rust"
              : s.state === "succeeded"
                ? "bg-moss"
                : "bg-faint"
        }`}
      />
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] tabular-nums text-dim">{s.n}</span>
        <span className="text-[12.5px] font-medium text-strong">{redactText(s.title)}</span>
        <span className="font-mono text-[11px] text-dim">{s.kind}</span>
        <StateChip kind="step" value={s.state} />
        {current && <span className="font-mono text-[10.5px] text-link">current</span>}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-[11px] text-dim">
        <span>
          attempts <span className="tabular-nums text-strong">{s.attempts}</span>
        </span>
        <span>
          started <Stamp at={s.started_at} />
        </span>
        <span>
          finished <Stamp at={s.finished_at} />
        </span>
        <span>{duration(s.started_at, s.finished_at)}</span>
        <span>checkpoint {s.checkpoint ? "written" : "none"}</span>
      </div>
      {error && (
        <pre className="mt-1 whitespace-pre-wrap rounded-row bg-rustSoft p-2 font-mono text-[11px] text-rust">
          {error}
        </pre>
      )}
      {operator && s.external_ids.length > 0 && (
        <div className="mt-1 font-mono text-[11px] text-dim">
          external ids:{" "}
          {s.external_ids.map((id, i) => (
            <code key={i} className="mr-2 text-strong">
              {redactText(id)}
            </code>
          ))}
        </div>
      )}
      {operator && log && log.length > 0 && (
        <details className="mt-1">
          <summary className="cursor-pointer font-mono text-[11px] text-link">
            log · {log.length} line{log.length === 1 ? "" : "s"}
          </summary>
          <pre className="mt-1 max-h-[320px] overflow-auto rounded-card bg-ink-700 p-3 font-mono text-[11px] leading-[1.5] text-ink-body">
            {log.map(redactText).join("\n")}
          </pre>
        </details>
      )}
      {operator && (!log || log.length === 0) && s.log && (
        <div className="mt-1 font-mono text-[11px] text-faint">log: {redactText(s.log)}</div>
      )}
    </li>
  );
}

export interface JobViewProps {
  job: Job;
  role: JobRole;
  onResume?: () => void;
  onContinue?: () => void;
  onCancel?: () => void;
  /** Step log lines (`StepLogResponse.lines`) by step number — operator only. */
  logs?: Readonly<Record<number, readonly string[]>>;
  /**
   * `false` hides the continuation buttons even for an operator — for a job
   * shown INSIDE an OpFlow, whose continuations live on the job's own page
   * (`#/job/<id>`) rather than as a second flow nested in the first.
   */
  controls?: boolean;
}

export function JobView({
  job,
  role,
  onResume,
  onContinue,
  onCancel,
  logs,
  controls = true,
}: JobViewProps) {
  const actions = controls ? jobActions(job.state, role) : [];
  const handlers: Record<JobAction, (() => void) | undefined> = {
    resume: onResume,
    continue: onContinue,
    cancel: onCancel,
  };
  const labels: Record<JobAction, string> = {
    resume: "Resume",
    continue: "Continue to cutover",
    cancel: "Cancel job",
  };
  return (
    <section aria-label="job" className="rounded-card border border-line bg-white p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className={EYEBROW}>job</span>
        <code className="font-mono text-[12px] text-strong">{job.id}</code>
        <StateChip kind="job" value={job.state} />
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        <Field label="op">{job.op}</Field>
        <Field label="tenant">{job.tenant ?? "—"}</Field>
        <Field label="principal">{job.principal}</Field>
        <Field label="created">
          <Stamp at={job.created_at} />
        </Field>
        <Field label="started">
          <Stamp at={job.started_at} />
        </Field>
        <Field label="finished">
          <Stamp at={job.finished_at} />
        </Field>
        <Field label="duration">{duration(job.started_at, job.finished_at)}</Field>
        <Field label="step">
          {job.current_step ?? "—"} / {job.steps.length}
        </Field>
        <Field label="request">{job.request_id}</Field>
      </div>

      {role === "operator" && job.lock && (
        <p className="mt-3 font-mono text-[11px] text-dim">
          holds {job.lock.order.join(" → ")} since <Stamp at={job.lock.since} />
        </p>
      )}

      {job.error && (
        <div role="status" className="mt-4 rounded-card bg-rustSoft p-3 text-[12.5px] text-rust">
          <div className={`${EYEBROW} !text-rust`}>error</div>
          <div className="mt-1 font-mono">
            {job.error.code}
            {job.error.step !== null && ` · step ${job.error.step}`}
          </div>
          <pre className="mt-1 whitespace-pre-wrap font-mono text-[11.5px]">{redactText(job.error.detail)}</pre>
        </div>
      )}

      {job.rollback && (
        <div className="mt-3 rounded-card border border-line bg-paper p-3 text-[12.5px]">
          <div className="flex items-center gap-2">
            <span className={EYEBROW}>rollback</span>
            <span className="font-mono text-[11.5px] text-dim">
              {job.rollback.attempted ? "attempted" : "not attempted"}
            </span>
            <StateChip kind="rollback" value={job.rollback.state} title="rollback state" />
          </div>
          {job.rollback.detail && (
            <pre className="mt-1 whitespace-pre-wrap font-mono text-[11.5px] text-body">
              {redactText(job.rollback.detail)}
            </pre>
          )}
        </div>
      )}

      <h3 className="mb-3 mt-5 font-mono text-[11px] font-medium uppercase tracking-[.14em] text-strong">
        steps
      </h3>
      {job.steps.length === 0 ? (
        <p className="text-[12.5px] text-dim">No steps recorded yet.</p>
      ) : (
        <ol className="ml-1">
          {job.steps.map((s) => (
            <StepItem
              key={s.n}
              s={s}
              current={job.current_step === s.n && jobInFlight(job.state)}
              role={role}
              log={logs?.[s.n]}
            />
          ))}
        </ol>
      )}

      {actions.length > 0 && (
        <div className="mt-5 flex flex-wrap gap-2 border-t border-lineSoft pt-4">
          {actions.map((a) => (
            <button
              key={a}
              type="button"
              onClick={handlers[a]}
              disabled={!handlers[a]}
              className={
                a === "cancel"
                  ? `${BUTTON} border-rust/40 text-rust hover:bg-rustSoft`
                  : `${BUTTON} disabled:opacity-40`
              }
            >
              {labels[a]}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
