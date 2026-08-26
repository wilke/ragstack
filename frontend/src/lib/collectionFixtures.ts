// THE PERSONA, AS DATA. Frozen `CollectionsResponse` literals shared by every
// Explore / Compare / Collection test, so all of them agree on what the affected
// caller's listing actually looks like.
//
// #420 shipped for one reason: every test caller could read the tenant's
// registry default, so the branch where the visible set EXCLUDES it was never
// exercised. `LISTING_WITHOUT_DEFAULT` is that caller — authenticated, not an
// admin, exactly one readable collection which is not the registry pointer's
// target. Note what that makes true, and what the contract used to deny:
//
//     ZERO entries carry `is_default: true`.
//
// The pointer names a collection this caller cannot see, so it is not in their
// listing at all — while the response-level `default` correctly names the one
// collection they CAN read. That divergence is the whole bug: the UI read the
// per-item flag, found no match, and guessed.
//
// Pure data — no server, no DOM, no fetch.

import type { CollectionsResponse } from "../api/client";

const entry = (id: string, label: string, isDefault: boolean) => ({
  id,
  label,
  model: "BAAI/bge-large-en-v1.5",
  dim: 1024,
  chunk_method: "fixed_token",
  chunk_size: 512,
  default: isDefault,
  is_default: isDefault,
  count: 1234,
});

/**
 * The affected user (#420 / #419). One readable collection, which is NOT the
 * registry default; the pointer's target is invisible to them, so no listed
 * entry is flagged. `default` names the collection they can actually read.
 */
export const LISTING_WITHOUT_DEFAULT: CollectionsResponse = Object.freeze({
  collections: [entry("C_readable", "My papers", false)],
  default: "C_readable",
}) as CollectionsResponse;

/**
 * The caller every test was written for: can read the registry default, which
 * is flagged in their listing and is also their effective target.
 */
export const LISTING_WITH_DEFAULT: CollectionsResponse = Object.freeze({
  collections: [
    entry("C_default", "Curated corpus", true),
    entry("C_other", "Preprints", false),
  ],
  default: "C_default",
}) as CollectionsResponse;

/**
 * Exactly one collection, and it IS the registry default. The case the old
 * `opts.length > 1` gate hid the picker for.
 */
export const LISTING_ONE_COLLECTION: CollectionsResponse = Object.freeze({
  collections: [entry("C_only", "The only corpus", true)],
  default: "C_only",
}) as CollectionsResponse;

/**
 * A caller who can read nothing at all — the #201 new user in the seconds
 * before provisioning. `default: ""` per the contract's empty-list rule.
 */
export const LISTING_EMPTY: CollectionsResponse = Object.freeze({
  collections: [],
  default: "",
}) as CollectionsResponse;
