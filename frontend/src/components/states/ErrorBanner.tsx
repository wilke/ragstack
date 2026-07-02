// Error branch: role=alert (assertive) + a Retry that re-fires the last query.
// Maps status codes to actionable, non-leaky messages.

import { ApiError } from "../../api/client";

function messageFor(error: Error): string {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) return "Check your API key.";
    if (error.status === 422) return "That query was rejected (validation).";
    return `Error ${error.status}: ${error.message}`;
  }
  return "Something went wrong reaching the API. Please retry.";
}

export function ErrorBanner({ error, onRetry }: { error: Error; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="mt-6 flex items-center justify-between gap-3 rounded bg-red-50 p-3 text-sm text-red-700"
    >
      <span>{messageFor(error)}</span>
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
