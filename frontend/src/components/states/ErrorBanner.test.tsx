import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ErrorBanner } from "./ErrorBanner";
import { ApiError } from "../../api/client";

// WHAT A 503 TELLS THE USER (#427 item D).
//
// The incident: a query 503'd because a Qdrant search exceeded its 30s bound.
// The banner said "The server had a problem (error 503). Please retry." — the
// same sentence a dead backend produces. Retry was in fact the right advice
// (the second read is warm) and the user was not told so; an operator reading a
// screenshot could not tell the two apart either.
//
// These render the real component with `renderToStaticMarkup` — no jsdom, no
// testing-library, per src/App.test.tsx's note. `messageFor` is intentionally
// not exported: asserting on the MARKUP is what proves a user would see the
// copy, and it covers the Reference line, which is JSX rather than a message.

const render = (error: Error) =>
  renderToStaticMarkup(createElement(ErrorBanner, { error, onRetry: () => {} }));

describe("ErrorBanner 503 copy", () => {
  it("tells a timeout user that a retry usually works", () => {
    const markup = render(new ApiError(503, "qdrant unavailable", "a1b2c3d4e5f60789", "timeout"));

    expect(markup).toContain("took longer than the server allows");
    expect(markup).toContain("retrying often succeeds within seconds");
    // ...and does NOT tell them the opposite at the same time.
    expect(markup).not.toContain("may not help");
  });

  it("does not promise a retry when the store was never reached", () => {
    const markup = render(
      new ApiError(503, "qdrant unavailable", "a1b2c3d4e5f60789", "unreachable"),
    );

    expect(markup).toContain("not responding");
    expect(markup).toContain("Retrying may not help right now");
    expect(markup).not.toContain("often succeeds within seconds");
  });

  it("names no cause at all when reason is absent", () => {
    // THE ABSENT CASE IS NOT AN UNKNOWN. The API emits `reason` from exactly
    // one place — the store-unavailable handler — so a 503 without it is one of
    // this endpoint's three OTHER 503 causes: the authorization store
    // fail-closed, a dormant/restoring collection (#358), or the tenant at
    // capacity. The contract this PR writes says the last two are retryable
    // after the delay in `Retry-After`, so "a search backend is not responding,
    // retrying may not help" would be a false cause AND wrong advice — a
    // regression in truthfulness dressed as better copy.
    const markup = render(new ApiError(503, "collection 'x' is restoring: …"));

    expect(markup).toContain("temporarily unavailable");
    expect(markup).toContain("try again shortly");
    expect(markup).not.toContain("not responding");
    expect(markup).not.toContain("may not help");
    expect(markup).not.toContain("often succeeds within seconds");
  });

  it("degrades an unrecognised reason to the no-promise store copy", () => {
    // `reason` is a server enum that may grow, and EVERY value of it comes from
    // the store-unavailable path — so "a backend is not responding" stays true
    // for a value this build has never heard of, while the optimistic warm-read
    // promise is withheld. Unrecognised belongs here, not in the absent branch.
    const markup = render(new ApiError(503, "…", "a1b2c3d4e5f60789", "quantum_flux"));

    expect(markup).toContain("not responding");
    expect(markup).toContain("Retrying may not help right now");
    expect(markup).not.toContain("often succeeds within seconds");
  });

  it("leaves the other 5xx statuses on the generic message", () => {
    const markup = render(new ApiError(502, "bad gateway"));
    expect(markup).toContain("The server had a problem (error 502)");
  });

  it("still maps the non-5xx statuses it always did", () => {
    expect(render(new ApiError(401, ""))).toContain("Check your API key.");
    expect(render(new ApiError(422, ""))).toContain("rejected (validation)");
    expect(render(new ApiError(429, ""))).toContain("Too many requests");
    expect(render(new Error("boom"))).toContain("Something went wrong reaching the API");
  });
});

describe("the reference id", () => {
  it("renders the request id so a screenshot is greppable", () => {
    const markup = render(new ApiError(503, "…", "8f3a2b1c9d4e5f60", "timeout"));
    expect(markup).toContain("Reference: 8f3a2b1c9d4e5f60");
    expect(markup).toContain("font-mono");
  });

  it("renders no reference line when the response carried no id", () => {
    const markup = render(new ApiError(503, "…"));
    expect(markup).not.toContain("Reference:");
  });

  it("renders one for a plain Error too — by rendering nothing", () => {
    // `referenceFor` must not assume ApiError; a thrown TypeError from a
    // rejected fetch reaches this component as well.
    expect(render(new Error("Failed to fetch"))).not.toContain("Reference:");
  });
});

describe("the no-echo rule", () => {
  // THE SAFETY PROPERTY, which was documented in a comment and untested.
  // `error.message` is the RAW RESPONSE BODY (api/client.ts). For a store
  // failure that body names the physical collection, the store's URL, the
  // exception type and the timeout setting; for an unhandled 500 it can carry
  // more. None of it is for a user, and none of it may reach the DOM.
  //
  // NOTE ON REVERT: this test passes against the pre-#427-W6 component too — it
  // pins an existing property rather than new behaviour. That is the point of
  // adding it: the rule now has a guard, so a later edit that "helpfully" shows
  // the server's sentence fails here instead of shipping.
  const MARKER = "MARKER_internal_host_9f2b";

  // Each case names the copy that MUST be there in the marker's place. A bare
  // `toMatch(/Retry|retry/)` would not do it: the Retry BUTTON always renders,
  // so that assertion is satisfied by a banner whose message is empty. Pairing
  // the absent marker with the specific sentence for that status is what makes
  // "it rendered our copy instead" a real claim.
  it.each([
    [
      "503 with a reason",
      new ApiError(503, MARKER, "8f3a2b1c9d4e5f60", "timeout"),
      "took longer than the server allows",
    ],
    ["503 without one", new ApiError(503, MARKER), "temporarily unavailable"],
    ["500", new ApiError(500, MARKER), "The server had a problem (error 500)"],
    ["401", new ApiError(401, MARKER), "Check your API key."],
    ["a plain Error", new Error(MARKER), "Something went wrong reaching the API"],
  ])("never renders error.message — %s", (_label, error, expected) => {
    const markup = render(error);

    expect(markup).not.toContain(MARKER);
    expect(markup).toContain('role="alert"');
    expect(markup).toContain(expected);
  });
});
