import { describe, expect, it } from "vitest";
import { GLOSSARY, TERMS, lookupTerm } from "./glossary";

describe("lookupTerm", () => {
  it("resolves a seeded term", () => {
    expect(lookupTerm("hybrid")).toContain("dense vectors + BM25");
  });

  it("is case- and space-insensitive", () => {
    const canonical = lookupTerm("top_k");
    expect(canonical).toBeDefined();
    expect(lookupTerm("  TOP_K ")).toBe(canonical);
    expect(lookupTerm("Cross-Encoder")).toBe(lookupTerm("cross-encoder"));
    expect(lookupTerm("Kendall  τ (order)")).toBe(lookupTerm("Kendall τ (order)"));
  });

  it("resolves an alias to its canonical definition", () => {
    expect(lookupTerm("dims")).toBe(lookupTerm("dim"));
    expect(lookupTerm("passage")).toBe(lookupTerm("source"));
  });

  it("returns undefined for a term that isn't defined", () => {
    expect(lookupTerm("perplexity")).toBeUndefined();
    expect(lookupTerm("")).toBeUndefined();
  });
});

describe("GLOSSARY", () => {
  // Both HelpTip and GlossaryPanel key off `term`, so a duplicate would render
  // twice in the panel and shadow itself in TERMS.
  it("defines each term once", () => {
    const seen = new Set<string>();
    for (const g of GLOSSARY) {
      for (const i of g.items) {
        const k = i.term.toLowerCase();
        expect(seen.has(k)).toBe(false);
        seen.add(k);
      }
    }
  });

  it("exposes every group item through the flat TERMS map", () => {
    for (const g of GLOSSARY) {
      for (const i of g.items) expect(TERMS[i.term.toLowerCase()]).toBe(i.def);
    }
  });

  it("keeps the terms the newer screens rely on", () => {
    for (const t of [
      "chunk",
      "embedding model",
      "provenance",
      "ingest job",
      "run",
      "saved run",
      "claim",
      "citation",
      "retrieval score",
      "vector store",
      "text index (BM25)",
      "graph store",
      "drift",
      "API key",
      "bearer token",
      "admin role",
      "share",
      "public",
      "group",
      "deep health",
      "MAX_COLLECTIONS",
      // Added when the four screens' help passes were merged: each one is a
      // <HelpTip term="…"/> somewhere with no children, so losing the entry
      // silently removes the affordance (an icon tip with no body renders null).
      "retrieval mode",
      "query rewriting",
      "rerank",
      "lane",
      "lane result",
      "chunker",
      "chunk size",
      "overlap",
      "semantic tunables",
      "collection",
      "collection name",
      "run selector",
      "pipeline strip",
      "chunk walking",
      "feedback",
      "credential type",
      "token binding",
      "identity provider",
      "grantee",
      "revoke",
      "access",
      "re-check",
      "deployment",
      "accessible vision mode",
    ]) {
      expect(lookupTerm(t), t).toBeDefined();
    }
  });
});
