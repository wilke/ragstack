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
  if (status === 401) return "Deleting a collection needs a valid API key or login.";
  if (status === 403)
    return "Only the collection's owner (or an admin) can delete it.";
  return `Could not delete the collection (error ${status}).`;
}

/**
 * What went wrong GRANTING a share (`POST /v1/collections/{id}/shares`).
 *
 * Owner-or-admin, matching #243: a caller who can read a collection but does not
 * own it gets 403; one who cannot read it gets 404 (existence not leaked); a
 * duplicate/no-op grant is 409; a bad grantee/permission is 422; a store outage
 * is 503 (fail closed). `apiDetail` unwraps the server's own sentence for the
 * cases where it carries actionable text (409/422/400); the raw body is never
 * returned.
 */
export function collectionShareMessage(status: number | null, body: string): string {
  if (status == null) return "Could not share the collection — could not reach the API.";
  if (status === 409) {
    const detail = apiDetail(body);
    return detail || "That grant already exists.";
  }
  if (status === 422) {
    const detail = apiDetail(body);
    return detail
      ? `The server rejected that grant: ${detail}`
      : "That grantee or permission didn't validate — v1 shares are read-only, and the grantee can't be blank.";
  }
  if (status === 400) {
    const detail = apiDetail(body);
    return detail || "Ownership is transferred, not granted — that permission can't be shared.";
  }
  if (status === 404)
    return "That collection was not found (or you can't see it), so it can't be shared.";
  if (status === 401) return "Sharing a collection needs a valid API key or login.";
  if (status === 403)
    return "Only the owner (or an admin) can share this collection.";
  if (status === 503)
    return "The authorization store is unavailable, so sharing is refused right now — try again shortly.";
  return `Could not share the collection (error ${status}).`;
}

/** Same, for revoking a share (`DELETE /v1/collections/{id}/shares/{share_id}`). */
export function collectionShareRevokeMessage(status: number | null, body: string): string {
  if (status == null) return "Could not revoke the share — could not reach the API.";
  if (status === 409) {
    const detail = apiDetail(body);
    return detail || "That grant can't be revoked here.";
  }
  if (status === 404) return "That share is already gone (or was never on this collection).";
  if (status === 401) return "Revoking a share needs a valid API key or login.";
  if (status === 403)
    return "Only the owner (or an admin) can change who this collection is shared with.";
  if (status === 503)
    return "The authorization store is unavailable, so revoking is refused right now — try again shortly.";
  return `Could not revoke the share (error ${status}).`;
}

/**
 * The grantee string that shares a collection with a RAGStack group (issue #245):
 * `@group:<id>`, mirroring the server's `_resolve_grantee`. Kept here (with the
 * `@public` literal) so the share dialog's group picker and the API agree on the
 * one spelling the server parses as a group target.
 */
export function groupGrantee(groupId: string): string {
  return `@group:${groupId.trim()}`;
}

/**
 * What went wrong CREATING a group (`POST /v1/groups`).
 *
 * Group create is open to any authenticated caller — the only failures are a
 * name collision / the reserved `public` name (409, the server names which), an
 * empty name (422), a missing key (401), or the authorization store being down
 * (503, fail closed). `apiDetail` unwraps the server's own sentence for 409/422;
 * the raw body is never returned.
 */
export function groupCreateMessage(status: number | null, body: string): string {
  if (status == null) return "Could not create the group — could not reach the API.";
  if (status === 409) {
    const detail = apiDetail(body);
    return detail || "You already have a group with that name (and “public” is reserved).";
  }
  if (status === 422) {
    const detail = apiDetail(body);
    return detail
      ? `The server rejected that group: ${detail}`
      : "A group needs a non-empty name.";
  }
  if (status === 401) return "Creating a group needs a valid API key or login.";
  if (status === 503)
    return "The authorization store is unavailable, so groups can't be created right now — try again shortly.";
  return `Could not create the group (error ${status}).`;
}

/** Same, for deleting a group (`DELETE /v1/groups/{id}`). Owner-or-admin. */
export function groupDeleteMessage(status: number | null, body: string): string {
  if (status == null) return "Could not delete the group — could not reach the API.";
  if (status === 409) {
    const detail = apiDetail(body);
    return detail || "The built-in “public” group can't be deleted.";
  }
  if (status === 404) return "That group is already gone (or you can't see it).";
  if (status === 401) return "Deleting a group needs a valid API key or login.";
  if (status === 403)
    return "Only the group's owner (or an admin) can delete it.";
  if (status === 503)
    return "The authorization store is unavailable, so deleting is refused right now — try again shortly.";
  return `Could not delete the group (error ${status}).`;
}

/** What went wrong ADDING a member (`POST /v1/groups/{id}/members`). Owner-or-admin. */
export function groupMemberAddMessage(status: number | null, body: string): string {
  if (status == null) return "Could not add the member — could not reach the API.";
  if (status === 409) {
    const detail = apiDetail(body);
    return detail || "That user is already an active member of this group.";
  }
  if (status === 422) {
    const detail = apiDetail(body);
    return detail
      ? `The server rejected that member: ${detail}`
      : "A group member must be a user, not a group (no nesting), and can't be blank.";
  }
  if (status === 404) return "That group was not found (or you can't see it).";
  if (status === 401) return "Adding a member needs a valid API key or login.";
  if (status === 403)
    return "Only the group's owner (or an admin) can change its membership.";
  if (status === 503)
    return "The authorization store is unavailable, so membership can't change right now — try again shortly.";
  return `Could not add the member (error ${status}).`;
}

/** Same, for removing a member (`DELETE /v1/groups/{id}/members/{subject}`). */
export function groupMemberRemoveMessage(status: number | null, _body: string): string {
  if (status == null) return "Could not remove the member — could not reach the API.";
  if (status === 404) return "That group was not found (or you can't see it).";
  if (status === 401) return "Removing a member needs a valid API key or login.";
  if (status === 403)
    return "Only the group's owner (or an admin) can change its membership.";
  if (status === 503)
    return "The authorization store is unavailable, so membership can't change right now — try again shortly.";
  return `Could not remove the member (error ${status}).`;
}

//: The literals that mean "share with everyone" — both are accepted by the API;
//: the UI sends the canonical `@public`. Kept here so the toggle and the grant
//: form agree on one spelling.
export const PUBLIC_GRANTEE = "@public";
const PUBLIC_LITERALS = new Set(["@public", "public"]);

//: The stored grantee_id of the built-in public group (never issuer-prefixed).
const PUBLIC_GROUP_ID = "public";

/**
 * Resolve what the operator typed into the subject the API will store, mirroring
 * the server's `_resolve_grantee`: `@public`/`public` → `@public` (the public
 * literal, never issuer-prefixed); a value already containing `:` is a full
 * `issuer:subject` string kept verbatim; a bare username is prefixed to
 * `<issuer>:<username>` (issuer defaults to `bvbrc`). Surrounding whitespace is
 * forgiven (people paste); an empty/whitespace input resolves to `""`, the
 * signal to block the Grant button before a request is even sent.
 *
 * This is advisory (a preview) — the server is authoritative and re-resolves the
 * raw grantee — but keeping the rule in one tested place lets the dialog show
 * "grants read to bvbrc:alice" before the round-trip, so a typo is visible early.
 */
export function normalizeGranteeSubject(typed: string, issuer = "bvbrc"): string {
  const g = typed.trim();
  if (g === "") return "";
  if (PUBLIC_LITERALS.has(g)) return PUBLIC_GRANTEE;
  if (g.includes(":")) return g;
  const iss = issuer.trim() || "bvbrc";
  return `${iss}:${g}`;
}

/** Whether a share row is the grant-to-everyone (public group) row. */
export function isPublicShare(rec: { grantee_type: string; grantee_id: string }): boolean {
  return rec.grantee_type === "group" && rec.grantee_id === PUBLIC_GROUP_ID;
}

/**
 * A human label for one share row: "Everyone (public)" for the public group, the
 * resolved subject for a user. Rendered as text, never markup.
 */
export function shareGranteeLabel(rec: { grantee_type: string; grantee_id: string }): string {
  if (isPublicShare(rec)) return "Everyone (public)";
  return rec.grantee_id;
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
  if (status === 401)
    return "Permanently deleting a collection needs a valid API key or login.";
  if (status === 403)
    return "Only the collection's owner (or an admin) can permanently delete it.";
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
