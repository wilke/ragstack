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

function messageFor(error: unknown): string {
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
        return "A job holds this tenant's lock. Try again when it finishes.";
      case "doctor_red":
        return "Doctor is red for this scope; the control plane refused.";
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

export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const reference = error instanceof CtlError ? error.requestId : null;
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
  );
}
