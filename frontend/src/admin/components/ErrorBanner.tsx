// The admin bundle's error branch — src/components/states/ErrorBanner.tsx for
// the control plane.
//
// It is a sibling rather than the same component because the two APIs have
// different error shapes and different rules. The tenant banner branches on an
// `ApiError` status; the control plane's contract makes `code` the field a
// client branches on and `request_id` a REQUIRED member of every non-2xx body,
// and it says in as many words that the admin UI never renders `detail`
// verbatim (it may name paths, units and hosts). Both halves of that are
// enforced here: the copy comes from `code`, and the only server string on
// screen is the 16-hex correlation id.

import { CtlError } from "../api/http";

// What may be read out of `extra`. It is the error's only untyped member, so
// every value is checked against the SHAPE the contract documents for it before
// it is printed, and anything else is dropped: a job id is a ULID, a principal
// is a subject string with no spaces, a lock name is one of five words, a field
// name is an identifier path. Never free text.
const ULID = /^[0-9A-HJKMNP-TV-Z]{26}$/;
const PRINCIPAL = /^[A-Za-z0-9@._:+-]{1,128}$/;
const LOCKS = ["registry", "manifest", "tenant", "gateway", "images"];
const FIELD = /^[A-Za-z_][A-Za-z0-9_.[\]-]{0,63}$/;

function extraString(error: CtlError, key: string, shape: RegExp): string | null {
  const v = error.extra?.[key];
  return typeof v === "string" && shape.test(v) ? v : null;
}

/** The job that holds the lock (`locked`) or already ran the key (`duplicate`). */
export function extraJobId(error: unknown): string | null {
  return error instanceof CtlError ? extraString(error, "job_id", ULID) : null;
}

/** Who holds a lock, as far as `extra` names it in a checkable shape. */
function lockHolder(error: CtlError): string {
  const job = extraString(error, "job_id", ULID);
  const principal = extraString(error, "principal", PRINCIPAL);
  const lockV = error.extra?.lock;
  const lock = typeof lockV === "string" && LOCKS.includes(lockV) ? lockV : null;
  const what = lock ? `the ${lock} lock` : "a lock this operation needs";
  if (job && principal) return `Job ${job} (${principal}) holds ${what}.`;
  if (job) return `Job ${job} holds ${what}.`;
  if (principal) return `${principal} holds ${what}.`;
  return `Another job holds ${what}.`;
}

/** `extra.fields` — the offending request members, names only. */
function fieldNames(error: CtlError): string[] {
  const v = error.extra?.fields;
  if (!Array.isArray(v)) return [];
  return v.filter((f): f is string => typeof f === "string" && FIELD.test(f)).slice(0, 12);
}

/** The banner copy for an error — from `code` (and shape-checked `extra`) only. */
export function messageFor(error: unknown): string {
  if (error instanceof CtlError) {
    switch (error.code) {
      case "auth_required":
        return "Your session has expired. Sign in again.";
      case "forbidden":
        return "This view needs an operator credential.";
      case "both_credentials":
        return "The request carried two credentials; only one may be presented.";
      case "not_found":
        return "The control plane does not know that name.";
      case "rate_limited":
        return "Too many requests — wait a moment and retry.";
      case "locked":
        return `${lockHolder(error)} Try again when it finishes.`;
      case "doctor_red":
        return "Doctor is red for this scope; the control plane refused. Clear the error findings on the plan, then plan again.";
      case "plan_stale":
        return "The plan changed after you reviewed it — the registry moved or a doctor finding changed. Review the new plan before running it.";
      case "duplicate": {
        const job = extraString(error, "job_id", ULID);
        return job
          ? `This request was already submitted as job ${job}; nothing new was started.`
          : "This request was already submitted; nothing new was started.";
      }
      case "confirm_required":
        return "This operation needs a typed confirmation before it runs.";
      case "validation": {
        const fields = fieldNames(error);
        return fields.length
          ? `The control plane rejected these arguments: ${fields.join(", ")}.`
          : "The control plane rejected the request's arguments.";
      }
      case "refused":
        return "The control plane refused this request as a matter of policy.";
      case "internal":
        return "The control plane had a problem. Please retry.";
      default:
        return `Request failed (${error.status}). Please retry.`;
    }
  }
  return "Something went wrong reaching the control plane. Please retry.";
}

export function ErrorBanner({
  error,
  onRetry,
  onOpenJob,
}: {
  error: unknown;
  onRetry?: () => void;
  /** Offered for `locked` and `duplicate` when `extra.job_id` is a job id. */
  onOpenJob?: (jobId: string) => void;
}) {
  const reference = error instanceof CtlError ? error.requestId : null;
  const job =
    error instanceof CtlError && (error.code === "locked" || error.code === "duplicate")
      ? extraJobId(error)
      : null;
  return (
    <div
      role="alert"
      className="mt-4 flex items-start justify-between gap-3 rounded-card bg-rustSoft p-3 text-sm text-rust"
    >
      <div>
        <span>{messageFor(error)}</span>
        {reference && (
          <div className="mt-1 font-mono text-[11px] opacity-80">Reference: {reference}</div>
        )}
      </div>
      <div className="flex shrink-0 gap-2">
        {job && onOpenJob && (
          <button
            type="button"
            onClick={() => onOpenJob(job)}
            className="shrink-0 rounded-panel border border-rust/40 px-3 py-1 text-[12px] font-medium hover:bg-rust/10"
          >
            Open job
          </button>
        )}
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="shrink-0 rounded-panel border border-rust/40 px-3 py-1 text-[12px] font-medium hover:bg-rust/10"
          >
            Retry
          </button>
        )}
      </div>
    </div>
  );
}
