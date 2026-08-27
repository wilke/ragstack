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

  it("uses the conservative copy when reason is absent", () => {
    // The 503's other causes — authorization store down, a collection
    // restoring, the tenant at capacity — carry no `reason` at all, and so does
    // any server older than #427 W2a.
    const markup = render(new ApiError(503, "collection is restoring"));

    expect(markup).toContain("Retrying may not help right now");
    expect(markup).not.toContain("often succeeds within seconds");
  });

  it("degrades an unrecognised reason to the conservative copy", () => {
    // `reason` is a server enum that may grow. A value this build has never
    // heard of must NOT fall through to the optimistic branch.
    const markup = render(new ApiError(503, "…", "a1b2c3d4e5f60789", "quantum_flux"));

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

  it.each([
    ["503 with a reason", new ApiError(503, MARKER, "8f3a2b1c9d4e5f60", "timeout")],
    ["503 without one", new ApiError(503, MARKER)],
    ["500", new ApiError(500, MARKER)],
    ["401", new ApiError(401, MARKER)],
    ["a plain Error", new Error(MARKER)],
  ])("never renders error.message — %s", (_label, error) => {
    const markup = render(error);

    expect(markup).not.toContain(MARKER);
    // Non-vacuous: the banner did render, and it rendered OUR copy. Without
    // this an empty string would satisfy the assertion above.
    expect(markup).toContain('role="alert"');
    expect(markup).toMatch(/Retry|retry/);
  });
});
