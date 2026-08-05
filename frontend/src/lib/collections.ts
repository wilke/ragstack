// Shared vocabulary for the collection-creation flows (the demo Collection view
// and the Ops admin panel), kept out of the components so both say the same
// thing and so the wording can be unit-tested.
//
// TERMINOLOGY — see docs/ARCHITECTURE.md §3 and docs/adr/0003-access-control.md:
//   index      = one physical Qdrant collection + matching ES index
//   collection = registry entry binding (model + dim + chunker) → index  [SHIPPED]
//   library    = not a separate concept — one-to-one with a collection
//   tenant     = a Qdrant instance (hard isolation for orgs, not for users)
// Everything in this module and in the UI that calls POST /v1/collections is a
// *collection*. The demo UI used to call it a "library"; there is no separate
// concept for that name to mean, so do not reintroduce it here.

import { apiDetail } from "./chunkers";

/**
 * What went wrong creating a collection, in a sentence a user can act on.
 *
 * `status` is the HTTP status (null when the request never reached the server)
 * and `body` the raw response text — `apiDetail` unwraps FastAPI's `detail` so
 * the server's own explanation ("chunk overlap (64) must be smaller than…")
 * reaches the screen instead of a bare status code. Never returns the raw body.
 */
export function collectionCreateMessage(status: number | null, body: string): string {
  if (status == null) return "Could not create the collection — could not reach the API.";
  if (status === 409)
    return "A collection with that id already exists — pick another id, or leave the id blank only if you meant to reuse an existing build spec.";
  if (status === 404)
    return "That embedding model isn't in the registry on this server, so a collection can't be bound to it.";
  if (status === 400) {
    const detail = apiDetail(body);
    return detail
      ? `The server rejected the collection config: ${detail}`
      : "The server rejected the collection config (bad model or chunk strategy).";
  }
  if (status === 422) {
    const detail = apiDetail(body);
    return detail
      ? `The collection config didn't validate: ${detail}`
      : "The collection config didn't validate (422).";
  }
  if (status === 401) return "Creating a collection needs a valid API key or login.";
  if (status === 403)
    return "Choosing a chunk strategy or embedding model is admin-only — pick “Server default” (or enter an admin key) and try again.";
  return `Could not create the collection (error ${status}).`;
}

/** Same, for DELETE /v1/collections/{id}. */
export function collectionDeleteMessage(status: number | null, body: string): string {
  if (status == null) return "Could not delete the collection — could not reach the API.";
  if (status === 409) {
    const detail = apiDetail(body);
    return detail || "The default collection can't be deleted.";
  }
  if (status === 404) return "That collection is already gone from the registry.";
  if (status === 401 || status === 403)
    return "Deleting a collection needs an admin API key.";
  return `Could not delete the collection (error ${status}).`;
}

/**
 * Same, for the DESTRUCTIVE `DELETE /v1/collections/{id}?purge=true`.
 *
 * Split from `collectionDeleteMessage` because the 409s mean different things: an
 * unregister can only ever collide with the default collection, while a purge is
 * also refused when the physical store is shared with other registry entries —
 * and that message names them, so it must reach the screen verbatim rather than
 * being flattened to "the default collection can't be deleted".
 */
export function collectionPurgeMessage(status: number | null, body: string): string {
  if (status == null) return "Could not delete the collection — could not reach the API.";
  if (status === 409) {
    const detail = apiDetail(body);
    return detail || "That collection can't be permanently deleted.";
  }
  if (status === 404)
    return "That collection is already gone from the registry. Its physical store, if any, was not touched.";
  if (status === 401 || status === 403)
    return "Permanently deleting a collection needs an admin API key.";
  return `Could not permanently delete the collection (error ${status}).`;
}

/**
 * Whether what the operator typed unlocks the permanent delete for `id`.
 *
 * Typing the id — not clicking OK — is the gate, because this destroys
 * embeddings that cost GPU hours. Surrounding whitespace is forgiven (people
 * paste); case and content are not.
 */
export function purgeConfirmed(typed: string, id: string): boolean {
  return typed.trim() === id && id.length > 0;
}

/** The purge report's four possible targets, in human words. */
const PURGE_TARGETS: Record<string, string> = {
  registry: "the registry binding",
  vectors: "the Qdrant collection",
  text_index: "the Elasticsearch index",
  manifest: "the provenance manifest",
};

export function purgeTargetLabel(target: string): string {
  return PURGE_TARGETS[target] ?? target;
}

/**
 * One honest sentence about a finished purge.
 *
 * Deliberately reports the "already gone" targets too: a purge that found no
 * Qdrant collection did not delete one, and saying "deleted everything" there
 * would be the same dishonesty the server's three-list report exists to avoid.
 * When something FAILED, the sentence leads with that — the physical resource
 * may still exist and is now an orphan nobody is tracking.
 */
export function purgeReportSummary(report: {
  collection_id: string;
  store: string;
  deleted: string[];
  absent: string[];
  failed: { target: string; error: string }[];
  ok: boolean;
}): string {
  const names = (ts: string[]) => ts.map(purgeTargetLabel).join(", ");
  const removed = report.deleted.length ? `Deleted ${names(report.deleted)}.` : "";
  const gone = report.absent.length ? ` Already absent: ${names(report.absent)}.` : "";
  if (report.ok) {
    return `Permanently deleted “${report.collection_id}” (store ${report.store}). ${removed}${gone}`.trim();
  }
  const failures = report.failed
    .map((f) => `${purgeTargetLabel(f.target)} (${f.error})`)
    .join("; ");
  return (
    `Partly deleted “${report.collection_id}” (store ${report.store}). ` +
    `${removed}${gone} COULD NOT delete ${failures} — nothing was rolled back, so that ` +
    `resource may still exist and needs removing by hand.`
  );
}

// The one subtlety that has actually bitten: whether the physical store is
// private to this collection or shared with every collection built the same way.
// Both flows print these verbatim, one line each.
export const ID_EXPLICIT_HINT =
  "Explicit id: this collection gets its own physical Qdrant collection and ES index — nothing else can write into it (#228).";
export const ID_BLANK_HINT =
  "Blank id: the store name is content-addressed from (model, dim, chunker), so any other collection built with the identical spec shares the same physical store and sees the same documents.";
