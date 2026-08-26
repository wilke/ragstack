import type { QueryResponse } from "../api/client";
import type { QueryOptions } from "../components/QueryOptionsMenu";

// One completed Explore query run — the unit Evidence takes apart and Compare
// re-runs. App owns a single `run: RunRecord | null` (most recent only, no
// history yet): Explore writes it via onRun on every successful /v1/query,
// Evidence reads it. The record snapshots collection + options as SENT, so a
// lever changed after submit can't be misattributed to this run's results.
export interface RunRecord {
  id: string; // short client-side id (e.g. "20f31a") — display + deep-link handle, never sent to the API
  query: string;
  // The registry id the request ACTUALLY carried, or null when it omitted the
  // field (the listing hadn't answered). Deliberately NOT "" — that sentinel is
  // what let the UI show one collection and query another (#420), and this is
  // the seam where it would grow back: onSendToCompare feeds this value straight
  // into a Compare lane, whose `collection` is `string | null` for the same
  // reason.
  collection: string | null;
  options: QueryOptions; // pipeline levers the request was sent with
  response: QueryResponse;
  startedAt?: number; // Date.now() at submit
  ms?: number; // wall-clock elapsed for the /v1/query round trip
}

// Session counter + random tail: unique even across rapid re-submits, and
// short enough to read like the mockup's run ids.
let seq = 0;
export function newRunId(): string {
  return `${(seq++).toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}
