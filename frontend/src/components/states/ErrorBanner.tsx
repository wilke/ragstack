// Error branch: role=alert (assertive) + a Retry that re-fires the last query.
// Maps status codes to actionable, non-leaky messages.

import { ApiError } from "../../api/client";

function messageFor(error: Error): string {
  // Never echo error.message by default — for an ApiError it's the raw response
  // body (api/client.ts), which can carry internal server detail/stack traces.
  // Map to status-specific, user-safe copy instead.
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) return "Check your API key.";
    if (error.status === 422) return "That query was rejected (validation).";
    if (error.status === 429) return "Too many requests — wait a moment and retry.";
    if (error.status >= 500) {
      // A 503 is the one server error where retrying is sometimes genuinely
      // right and sometimes futile, and until #427 the user was told the same
      // thing either way — "The server had a problem (error 503). Please
      // retry." — whether a search had timed out or the backend was dead.
      // `reason` (contracts/schemas/error.json) is the server's own
      // discriminator, and there are only two branches worth drawing:
      //
      //   timeout      -> we CONNECTED and the search exceeded its bound. The
      //                   retry hits a warm read and often succeeds in seconds,
      //                   so say that and put Retry forward.
      //   unreachable  -> we never reached the store, or the 503 came from one
      //   error           of its other causes (authorization store down, a
      //   absent          collection restoring, the tenant at capacity), or the
      //                   server predates `reason`. Offer Retry, promise nothing.
      //
      // An UNRECOGNISED value lands in the second branch on purpose: `reason`
      // is a server-side enum that may grow, and a value this build has never
      // heard of must degrade to the conservative copy, not the optimistic one.
      if (error.status === 503) {
        if (error.reason === "timeout") {
          return (
            "The search took longer than the server allows. This is usually a large " +
            "collection warming up — retrying often succeeds within seconds."
          );
        }
        return "A search backend is not responding. Retrying may not help right now.";
      }
      return `The server had a problem (error ${error.status}). Please retry.`;
    }
    return `Request failed (error ${error.status}). Please retry.`;
  }
  return "Something went wrong reaching the API. Please retry.";
}

// The server's correlation id for this failure, when the response carried one.
// This is rendered where `error.message` is not, and the distinction is the
// point of the rule above: `message` is server PROSE and may name internal
// hosts, collections and settings; this is 16 hex characters the server
// generated for the purpose. It is what turns a user's screenshot into a single
// `grep rid=<id>` (#427).
function referenceFor(error: Error): string | undefined {
  return error instanceof ApiError ? error.requestId : undefined;
}

export function ErrorBanner({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const reference = referenceFor(error);
  return (
    <div
      role="alert"
      className="mt-6 flex items-start justify-between gap-3 rounded bg-red-50 p-3 text-sm text-red-700"
    >
      <div>
        <span>{messageFor(error)}</span>
        {reference && (
          <div className="mt-1 font-mono text-xs text-red-600">
            Reference: {reference}
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="shrink-0 rounded border border-red-300 px-3 py-1 font-medium hover:bg-red-100"
      >
        Retry
      </button>
    </div>
  );
}
