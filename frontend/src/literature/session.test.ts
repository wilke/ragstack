import { afterEach, describe, expect, it, vi } from "vitest";
import { subjectOf } from "./session";
import { ragstackBase, tenant } from "./api";

describe("subjectOf", () => {
  it("reads un= from the SIGNED region only — a field appended after |sig= does not change the identity", () => {
    // The critical case: appending "|un=eve" after the signature must not let an
    // attacker who can tack text onto a valid token impersonate a different
    // subject. subjectOf must stop at the first "|sig=".
    expect(subjectOf("un=alice@patricbrc.org|tokenid=x|sig=deadbeef|un=eve")).toBe("alice@patricbrc.org");
  });

  it("returns '' when the token has no un= field", () => {
    expect(subjectOf("tokenid=x|sig=deadbeef")).toBe("");
  });

  it("returns '' for an empty string", () => {
    expect(subjectOf("")).toBe("");
  });

  it("still parses un= from a token with no |sig= at all", () => {
    expect(subjectOf("un=bob@patricbrc.org|tokenid=x")).toBe("bob@patricbrc.org");
  });
});

describe("tenant", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const withSearch = (search: string) => {
    vi.stubGlobal("window", { location: { search } });
  };

  it("falls back to the build default when there is no ?tenant= override", () => {
    withSearch("");
    expect(tenant()).toBe("dev");
  });

  it("accepts a plain single-segment override", () => {
    withSearch("?tenant=stage2");
    expect(tenant()).toBe("stage2");
  });

  it.each([
    ["//evil.com", "a scheme-relative host"],
    ["../../x", "a path traversal"],
    ["a/b", "an embedded path segment"],
    ["a.b", "a dot"],
    ["a b", "a space"],
    ["", "empty"],
    ["évil", "a non-ASCII letter"],
  ])("falls back to the build default for %j (%s)", (bad) => {
    withSearch(`?tenant=${encodeURIComponent(bad)}`);
    expect(tenant()).toBe("dev");
  });
});

describe("ragstackBase", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const withSearch = (search: string) => {
    vi.stubGlobal("window", { location: { search } });
  };

  it("is a single-segment relative path built from the tenant, never an absolute URL", () => {
    withSearch("?tenant=stage2");
    const base = ragstackBase();
    expect(base).toBe("/ragstack/stage2/api");
    expect(base.startsWith("/")).toBe(true);
    expect(base).toMatch(/^\/ragstack\/[A-Za-z0-9_-]+\/api$/);
  });

  it("cannot be steered off-origin by a hostile ?tenant= value — falls back to the build default's base", () => {
    for (const bad of ["//evil.com", "../../x", "a/b", "a.b", "a b", "", "évil"]) {
      withSearch(`?tenant=${encodeURIComponent(bad)}`);
      const base = ragstackBase();
      expect(base).toBe("/ragstack/dev/api");
      expect(base).toMatch(/^\/ragstack\/[A-Za-z0-9_-]+\/api$/);
      expect(base).not.toMatch(/^[a-z]+:\/\//i);
    }
  });
});
