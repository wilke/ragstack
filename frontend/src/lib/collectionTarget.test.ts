import { describe, expect, it } from "vitest";
import { newLane, seedLaneCollections } from "../components/CompareView";
import {
  LISTING_EMPTY,
  LISTING_ONE_COLLECTION,
  LISTING_WITH_DEFAULT,
  LISTING_WITHOUT_DEFAULT,
} from "./collectionFixtures";
import { collectionTarget, requestCollection, targetInfo } from "./collectionTarget";

// #420 — the UI showed one collection and queried another. These are pure
// functions over plain listings: no DOM, no fetch, no jsdom (see App.test.tsx
// for why this suite renders to static markup and nothing more).

describe("collectionTarget", () => {
  // T11. The regression, at its narrowest: the affected user opens Explore,
  // touches nothing, and submits. On main the request omitted `collection`
  // entirely (`collection || undefined` over a "" state that nothing could
  // clear), so the server resolved its GLOBAL default and 404'd.
  it("targets the caller's own readable collection when nothing is picked", () => {
    const t = collectionTarget(LISTING_WITHOUT_DEFAULT, null);
    expect(t.id).toBe("C_readable");
    expect(requestCollection(t)).toBe("C_readable");
  });

  // T12 — THE assertion. The label and the submitted collection must name the
  // same thing. It is not asserted by inspecting two expressions and hoping;
  // there is only one computation, and this pins its two readings together for
  // all three callers.
  describe("the label and the request name the same collection", () => {
    const cases = [
      ["a caller who can read the registry pointer", LISTING_WITH_DEFAULT, "C_default", "Curated corpus"],
      ["a caller who cannot", LISTING_WITHOUT_DEFAULT, "C_readable", "My papers"],
      ["a caller with exactly one collection", LISTING_ONE_COLLECTION, "C_only", "The only corpus"],
    ] as const;

    for (const [who, listing, id, label] of cases) {
      it(who, () => {
        const t = collectionTarget(listing, null);
        expect(requestCollection(t)).toBe(id);
        expect(t.label).toBe(label);
        // And the label really is that id's label — not a coincidence between
        // two hardcoded strings.
        const entry = listing.collections.find((c) => c.id === requestCollection(t));
        expect(t.label).toBe(entry?.label);
        expect(t.known).toBe(true);
      });
    }
  });

  // T13. The graceful degradation the issue asks to KEEP: before a credential
  // is set, GET /v1/collections 401s and there is no listing. The chip must
  // still render and the request must still omit the field. (Rule 1 is only
  // fully honest once #419 lands — until then the server resolves its global
  // default for an omitted field. That is today's behaviour, and such a request
  // would 401 anyway.)
  it("degrades to an omitted field and a placeholder label with no listing", () => {
    const t = collectionTarget(undefined, null);
    expect(t.id).toBeNull();
    expect(requestCollection(t)).toBeUndefined();
    expect(t.label).toBe("default");
    expect(t.known).toBe(false);
  });

  it("keeps degrading gracefully even if a selection was somehow made", () => {
    const t = collectionTarget(undefined, "C_readable");
    expect(t.id).toBeNull();
    expect(t.known).toBe(false);
  });

  it("honours an explicit pick, and labels it with that entry", () => {
    const t = collectionTarget(LISTING_WITH_DEFAULT, "C_other");
    expect(requestCollection(t)).toBe("C_other");
    expect(t.label).toBe("Preprints");
  });

  // A selection left over from another tenant/key can never reach a request:
  // it isn't in the listing, so the caller-aware default catches it. The views'
  // reset effects are display hygiene, not the guard.
  it("ignores a stale selection rather than sending a phantom id", () => {
    const t = collectionTarget(LISTING_WITHOUT_DEFAULT, "C_from_another_tenant");
    expect(requestCollection(t)).toBe("C_readable");
    expect(t.label).toBe("My papers");
  });

  // A caller who can read nothing gets an honest "none", not the name of a
  // collection that does not exist for them.
  it("says none, and sends nothing, when the caller can read no collection", () => {
    const t = collectionTarget(LISTING_EMPTY, null);
    expect(t.id).toBeNull();
    expect(requestCollection(t)).toBeUndefined();
    expect(t.label).toBe("none");
    expect(t.known).toBe(true);
  });

  // Defensive: a listing whose `default` names something not in `collections`
  // is a server bug, but the label must still be the id we will actually send —
  // never a guess at some other entry.
  it("labels an unmatched default with the id itself, never another entry", () => {
    const t = collectionTarget(
      { collections: LISTING_WITHOUT_DEFAULT.collections, default: "C_ghost" },
      null,
    );
    expect(requestCollection(t)).toBe("C_ghost");
    expect(t.label).toBe("C_ghost");
    expect(t.label).not.toBe("My papers");
  });
});

describe("targetInfo", () => {
  it("returns the entry whose build config describes the target", () => {
    const t = collectionTarget(LISTING_WITHOUT_DEFAULT, null);
    expect(targetInfo(LISTING_WITHOUT_DEFAULT, t)?.id).toBe("C_readable");
  });

  it("returns null when there is no target to describe", () => {
    expect(targetInfo(undefined, collectionTarget(undefined, null))).toBeNull();
    expect(targetInfo(LISTING_EMPTY, collectionTarget(LISTING_EMPTY, null))).toBeNull();
  });
});

// T15. Compare, on the lane axis. `renderToStaticMarkup` runs no effects, so a
// seeded lane cannot be observed from a static render — the seeding is a pure
// exported helper instead, and this asserts the whole composition: seed → lane
// collection → target → what the lane REQUESTS and what its header SAYS.
describe("Compare lanes seeded from a listing", () => {
  it("seeds a lane on the caller's readable collection, and labels it the same", () => {
    const seeded = seedLaneCollections(LISTING_WITHOUT_DEFAULT.collections);
    expect(seeded).toEqual(["C_readable"]);

    const t = collectionTarget(LISTING_WITHOUT_DEFAULT, seeded[0]);
    expect(requestCollection(t)).toBe("C_readable");
    expect(t.label).toBe("My papers");
    expect(t.id).toBe("C_readable"); // the lane's id readout — not "default"
  });

  it("seeds one lane per collection, by real id, for a caller who reads the pointer", () => {
    const seeded = seedLaneCollections(LISTING_WITH_DEFAULT.collections);
    expect(seeded).toEqual(["C_default", "C_other"]);
    // The registry-default lane carries its real id, not the "" sentinel.
    expect(seeded[0]).not.toBe("");
    for (const [i, id] of seeded.entries()) {
      const t = collectionTarget(LISTING_WITH_DEFAULT, id);
      expect(requestCollection(t)).toBe(id);
      expect(t.label).toBe(LISTING_WITH_DEFAULT.collections[i].label);
    }
  });

  // The lane that is NOT seeded — "+ Lane", or one the stale-reset parked. On
  // main it was born with `collection: ""`, and for this caller nothing mapped
  // back to "": the <select> displayed the first option ("My papers"), the id
  // readout below it said "default", and the request omitted the field. Three
  // answers for one lane. All four readings must now agree.
  it("gives an ADDED lane the same answer in its request, label and readout", () => {
    const lane = newLane();
    expect(lane.collection).toBeNull(); // never ""

    const t = collectionTarget(LISTING_WITHOUT_DEFAULT, lane.collection);
    expect(requestCollection(t)).toBe("C_readable"); // what it queries
    expect(t.label).toBe("My papers"); // the header select
    expect(t.id).toBe("C_readable"); // the id readout under it
    expect(targetInfo(LISTING_WITHOUT_DEFAULT, t)?.id).toBe("C_readable"); // its build facts
  });

  it("caps the seed at the lane maximum", () => {
    const many = Array.from({ length: 9 }, (_, i) => ({
      ...LISTING_WITH_DEFAULT.collections[0],
      id: `c${i}`,
    }));
    expect(seedLaneCollections(many)).toHaveLength(6);
  });
});
