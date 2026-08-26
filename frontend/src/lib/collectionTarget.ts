// WHICH COLLECTION THE NEXT REQUEST WILL ACTUALLY HIT — one function, so the
// label and the request body cannot disagree.
//
// #420: Explore showed one collection and queried another. The label came from
// `ConfigChips.tsx` (`opts.find(...) ?? opts[0]`) and the request body from
// `ExploreView.tsx` (`collection || undefined`) — two expressions, in two files,
// that nothing tied together. For a caller who cannot read the tenant's registry
// default they resolved to different collections, and the chip named a corpus
// the request never touched.
//
// The structural fix is that there is now exactly ONE computation. A view calls
// `collectionTarget()` once and hands the SAME value to the request and to the
// chip. A component that renders a label no longer computes anything, so it can
// no longer be wrong about the target.
//
// THE NAMING TRAP THAT CAUSED THE BUG. The listing carries two things called
// "default" and they answer different questions:
//
//   CollectionsResponse.default   — the id THIS CALLER's request targets when it
//                                   omits `collection`. Caller-aware; already
//                                   computed correctly server-side. This module
//                                   is its first consumer in the frontend.
//   CollectionInfo.default /      — which listed entry the GLOBAL registry
//   CollectionInfo.is_default       pointer names. Not caller-aware. Correct for
//                                   "this one can't be deleted" (OpsDashboard);
//                                   wrong for "this is what I'm querying".
//
// Read the response-level one here. Never the per-item flag.

import type { CollectionInfo, CollectionsResponse } from "../api/client";

export interface CollectionTarget {
  /** The id to put in the request body. `null` ⇒ omit the field entirely. */
  id: string | null;
  /** Exactly what the chip must display. Never a guess, never `opts[0]`. */
  label: string;
  /** False when the listing has not answered — the label is a placeholder. */
  known: boolean;
}

/**
 * Resolve the collection a request will target, and the label that names it.
 *
 * `selected` is the user's explicit pick, or `null` for "I did not choose; use
 * whatever the listing says my default is". There is deliberately no `""`
 * sentinel: the empty string used to mean "let the server pick", and because no
 * option ever mapped back to it for a caller who could not read the registry
 * default, the state was pinned there permanently with no UI escape (#420).
 *
 * Rules:
 *  1. No listing yet (loading, or a 401 before a key is set) → omit `collection`
 *     and label the chip "default". This is the graceful degradation the issue
 *     asks to preserve: the chip still renders and the request still goes out,
 *     exactly as before. It is only fully honest once #419 lands — until then
 *     the server resolves its GLOBAL default for an omitted field rather than
 *     this caller's. That is today's behaviour, and it only happens before a
 *     credential is set, when the request would 401 anyway.
 *  2. `selected` names a collection that is actually in the listing → that one.
 *     The membership check is what makes a stale selection unreachable: it can
 *     never survive into a request, because rule 3 catches it. The views' reset
 *     effects are therefore UI hygiene (clearing a dead `<select>` value), not
 *     the thing standing between the user and a phantom id.
 *  3. Otherwise → the listing's caller-aware `default`, labelled with the
 *     matching entry's label (or the id itself if, defensively, nothing
 *     matches). Never `opts[0]`.
 *  4. `default === ""` → the caller can read no collection at all. Omit the
 *     field and say "none" rather than naming a collection that does not exist
 *     for them. (`collections` is empty in that case too.)
 */
export function collectionTarget(
  listing: CollectionsResponse | undefined,
  selected: string | null,
): CollectionTarget {
  if (!listing) return { id: null, label: "default", known: false };

  const items: CollectionInfo[] = listing.collections ?? [];

  if (selected !== null) {
    const picked = items.find((c) => c.id === selected);
    if (picked) return { id: picked.id, label: picked.label, known: true };
  }

  const fallback = listing.default;
  if (!fallback) return { id: null, label: "none", known: true };

  const match = items.find((c) => c.id === fallback);
  return { id: fallback, label: match?.label ?? fallback, known: true };
}

/**
 * The value for a request body's `collection` field: the resolved id, or
 * `undefined` to omit the field when there is nothing honest to send.
 *
 * `undefined` (not `""`) — the field is omitted from the JSON, which is what the
 * API contract means by "let the server decide". An empty string is a real
 * value and would 404.
 */
export function requestCollection(t: CollectionTarget): string | undefined {
  return t.id ?? undefined;
}

/**
 * The entry whose build config (model · dims · chunker) describes the target, or
 * `null` when the target is unknown or not in the listing. Views show this next
 * to the picker; keeping it here means the facts shown and the id sent come from
 * the same lookup.
 */
export function targetInfo(
  listing: CollectionsResponse | undefined,
  t: CollectionTarget,
): CollectionInfo | null {
  if (!listing || t.id === null) return null;
  return listing.collections.find((c) => c.id === t.id) ?? null;
}
