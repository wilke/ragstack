import { describe, expect, it } from "vitest";
import { agreementLabel } from "./AgreementBadge";
import {
  DEFAULT_LEVERS,
  defaultsChips,
  effectiveLevers,
  normalizeOverrides,
  overrideChips,
  type Levers,
} from "./levers";

const glob: Levers = { ...DEFAULT_LEVERS, topK: 5 };

describe("lever overrides", () => {
  it("effective config is defaults overlaid with the sparse overrides", () => {
    const eff = effectiveLevers(glob, { mode: "bm25", topK: 8 });
    expect(eff.mode).toBe("bm25");
    expect(eff.topK).toBe(8);
    expect(eff.rerank).toBe("default"); // untouched levers inherit
  });

  it("normalizeOverrides drops a lever set back to the shared default", () => {
    expect(normalizeOverrides(glob, { mode: "hybrid", rerank: "on" })).toEqual({ rerank: "on" });
    expect(normalizeOverrides(glob, { topK: 5 })).toEqual({});
  });

  it("override chips name ONLY the overridden levers, defaults chips name all", () => {
    expect(overrideChips(glob, {})).toEqual([]);
    expect(overrideChips(glob, { mode: "bm25", useGraph: false })).toEqual([
      "mode bm25",
      "KG off",
    ]);
    expect(defaultsChips(glob)).toEqual([
      "mode hybrid",
      "rewrite none",
      "rerank default",
      "k 5",
      "KG on",
    ]);
  });

  it("model chips use the id basename and appear only when set", () => {
    expect(overrideChips(glob, { llm: "org/some-llm" })).toEqual(["llm some-llm"]);
    expect(defaultsChips({ ...glob, reranker: "org/ce" })).toContain("rr ce");
  });
});

describe("agreement badge label", () => {
  it("collapses full membership, keeps subsets, marks uniques", () => {
    expect(agreementLabel(["A", "B", "C"], 3)).toBe("all lanes");
    expect(agreementLabel(["A", "B"], 3)).toBe("A · B");
    expect(agreementLabel(["C"], 3)).toBe("only C");
  });
});
