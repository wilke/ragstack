// Shared vocabulary for the collection-creation flows (the demo Collection view
// and the Ops admin panel), kept out of the components so both say the same
// thing and so the wording can be unit-tested.
//
// TERMINOLOGY — see docs/libraries-spec.md §0:
//   index      = one physical Qdrant collection + matching ES index
//   collection = registry entry binding (model + dim + chunker) → index  [SHIPPED]
//   library    = an access-controlled, user-owned document set inside a
//                collection, isolated by `library_id`                   [NOT BUILT — #230]
// Everything in this module and in the UI that calls POST /v1/collections is a
// *collection*. The demo UI used to call it a "library", which collides head-on
// with the not-yet-implemented concept; do not reintroduce that name here.

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
  if (status === 401 || status === 403)
    return "Creating a collection needs an admin API key.";
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

// The one subtlety that has actually bitten: whether the physical store is
// private to this collection or shared with every collection built the same way.
// Both flows print these verbatim, one line each.
export const ID_EXPLICIT_HINT =
  "Explicit id: this collection gets its own physical Qdrant collection and ES index — nothing else can write into it (#228).";
export const ID_BLANK_HINT =
  "Blank id: the store name is content-addressed from (model, dim, chunker), so any other collection built with the identical spec shares the same physical store and sees the same documents.";
