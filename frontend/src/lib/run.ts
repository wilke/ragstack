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
  // field (the listing hadn't answered). It stores `target.id` verbatim, which
  // is why it is `string | null`: mapping it back to "" here would put the #420
  // sentinel into the one record whose whole job is to describe the request as
  // sent. It is not decoration — EvidenceView passes it to SourceViewer, which
  // scopes its chunk fetch and its cache keys to it (chunk ids are only unique
  // within a collection), so a wrong value here fetches from the wrong corpus.
  //
  // (Its three readers are all in EvidenceView: the Markdown export, the saved-
  // run row, and that SourceViewer prop. `onSendToCompare` carries only the
  // query — App.tsx:102-108 — so the collection does NOT travel to a Compare
  // lane. Lane.collection is `string | null` on its own account.)
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
