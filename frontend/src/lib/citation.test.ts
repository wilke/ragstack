import { describe, expect, it } from "vitest";
import { doiUrl, formatCitation, isValidDoi } from "./citation";

describe("isValidDoi", () => {
  it("accepts well-formed DOIs", () => {
    expect(isValidDoi("10.1234/abc.def")).toBe(true);
    expect(isValidDoi("10.1000/xyz123")).toBe(true);
  });

  it.each([
    "javascript:alert(1)",
    "not-a-doi",
    "10./missing",
    "http://evil.com/10.1/x",
    "",
    42,
    null,
    undefined,
  ])("rejects %s", (bad) => {
    expect(isValidDoi(bad as unknown)).toBe(false);
  });
});

describe("doiUrl", () => {
  it("builds an encoded resolver URL under the fixed origin", () => {
    expect(doiUrl("10.1234/abc")).toBe("https://doi.org/10.1234%2Fabc");
  });

  it("returns null for an invalid/hostile DOI (button stays disabled)", () => {
    expect(doiUrl("javascript:alert(1)")).toBeNull();
    expect(doiUrl(undefined)).toBeNull();
  });

  it("neutralizes a hostile suffix by encoding it after the fixed prefix", () => {
    const url = doiUrl("10.1/a\"><script>");
    // Still rooted at https://doi.org/ and the payload is percent-encoded.
    expect(url?.startsWith("https://doi.org/")).toBe(true);
    expect(url).not.toContain("<script>");
  });
});

describe("formatCitation", () => {
  it("joins whatever fields exist", () => {
    const s = formatCitation(
      { authors: ["Doe, J.", "Roe, R."], year: 2021, title: "On RAG", doi: "10.1/x" },
      "fallback",
    );
    expect(s).toBe("Doe, J., Roe, R.. (2021). On RAG. https://doi.org/10.1/x");
  });

  it("falls back to the title placeholder and omits missing fields", () => {
    expect(formatCitation({}, "doc-42")).toBe("doc-42");
  });

  it("does not emit a DOI URL for an invalid doi", () => {
    expect(formatCitation({ title: "T", doi: "bad" }, "f")).toBe("T");
  });
});
