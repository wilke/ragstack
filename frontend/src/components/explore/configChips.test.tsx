import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  LISTING_EMPTY,
  LISTING_GHOST_DEFAULT,
  LISTING_ONE_COLLECTION,
  LISTING_WITH_DEFAULT,
  LISTING_WITHOUT_DEFAULT,
} from "../../lib/collectionFixtures";
import { collectionTarget } from "../../lib/collectionTarget";
import { DEFAULT_QUERY_OPTIONS } from "../QueryOptionsMenu";
import { ConfigChips } from "./ConfigChips";

// T14 — what the SHIPPED chip row actually renders, as static markup. No jsdom,
// no testing-library: `renderToStaticMarkup` is the whole harness (App.test.tsx
// explains why, and its own comment describes this class of bug — component
// tests staying green while the shipped UI told the user something false).

const chips = (listing: Parameters<typeof collectionTarget>[0]): ReactElement =>
  createElement(ConfigChips, {
    opts: listing?.collections ?? [],
    target: collectionTarget(listing, null),
    setCollection: () => {},
    options: DEFAULT_QUERY_OPTIONS,
    onOptionsChange: () => {},
    serverRerank: null,
  });

const render = (listing: Parameters<typeof collectionTarget>[0]): string =>
  renderToStaticMarkup(chips(listing));

describe("the collection chip", () => {
  // The dead end. On main the picker rendered only at `opts.length > 1`, so the
  // affected caller — exactly one readable collection — got no control at all,
  // and no way to correct a target the chip was naming wrongly. It now renders
  // whenever there is anything to pick.
  it("gives a caller with exactly one collection a real picker", () => {
    const html = render(LISTING_WITHOUT_DEFAULT);
    expect(html).toContain('aria-label="Collection"');
    expect(html).toContain("<select");
    expect(html).toContain('value="C_readable"');
    expect(html).toContain("My papers");
  });

  it("does the same when that one collection IS the registry default", () => {
    const html = render(LISTING_ONE_COLLECTION);
    expect(html).toContain('aria-label="Collection"');
    // Real id in the option — never the "" sentinel that meant "let the server
    // pick" and that nothing could ever map back to.
    expect(html).toContain('value="C_only"');
    expect(html).not.toContain('value=""');
  });

  // The chip is handed the target; it computes nothing. So the name on screen
  // is the name in the request body, by construction.
  it("names the collection the request will target, not the first option", () => {
    const html = render(LISTING_WITH_DEFAULT);
    const target = collectionTarget(LISTING_WITH_DEFAULT, null);
    expect(html).toContain(`${target.label} ▾`);
    expect(html).toContain(`value="${target.id}"`);
  });

  it("selects the caller's own collection even when it is not flagged default", () => {
    const html = render(LISTING_WITHOUT_DEFAULT);
    const target = collectionTarget(LISTING_WITHOUT_DEFAULT, null);
    expect(target.id).toBe("C_readable");
    // Zero entries carry is_default for this caller — the label must still be
    // right, which is exactly what the old `?? opts[0]` fallback could not do.
    expect(LISTING_WITHOUT_DEFAULT.collections.every((c) => !c.is_default)).toBe(true);
    expect(html).toContain(`${target.label} ▾`);
  });

  // THE INVARIANT, in the one case where it can actually break. Everywhere else
  // the target IS one of the options, so "render the target's label" and
  // "re-derive it from opts" agree and no test can tell them apart — which is
  // precisely how #420 survived review. Here they disagree, so this is the test
  // that fails if the chip ever goes back to computing its own label.
  it("renders the TARGET's label even when the target is not among the options", () => {
    const html = render(LISTING_GHOST_DEFAULT);
    expect(html).toContain("C_ghost ▾");
    expect(html).not.toContain("My papers ▾");
  });

  // With no listing at all (401 before a key is set) the row still renders: a
  // plain chip reading "default", no picker, and the levers still visible.
  it("still renders a plain chip when the listing has not answered", () => {
    const html = render(undefined);
    expect(html).toContain("default");
    expect(html).not.toContain("<select");
    expect(html).toContain("rerank");
  });

  // `known: false` is the difference between "this IS the target" and "this is
  // a placeholder until the listing answers". Saying both with equal confidence
  // is the habit that produced #420, so the placeholder is visibly hedged.
  it("hedges the placeholder chip while the listing is unknown", () => {
    const unknown = render(undefined);
    expect(unknown).toContain("opacity-60");
    expect(unknown).toContain("whatever the server picks");

    // A real, known answer is stated plainly — no hedge.
    const known = render(LISTING_EMPTY);
    expect(known).toContain("none");
    expect(known).not.toContain("opacity-60");
    expect(known).not.toContain("whatever the server picks");
  });
});
