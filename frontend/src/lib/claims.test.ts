import { describe, expect, it } from "vitest";
import { splitClaims } from "./claims";

describe("splitClaims", () => {
  it("splits sentences and resolves [n] markers to 0-based source indices", () => {
    const claims = splitClaims(
      "Bees are used as a model organism [1]. Ants defend cooperatively [2, 3].",
      3,
    );
    expect(claims).toHaveLength(2);
    expect(claims[0]).toEqual({ text: "Bees are used as a model organism.", cited: [0] });
    expect(claims[1]).toEqual({ text: "Ants defend cooperatively.", cited: [1, 2] });
  });

  it("drops markers that point outside the retrieved set — never a phantom chip", () => {
    const claims = splitClaims("Supported [1]. Unsupported [7].", 2);
    expect(claims[0].cited).toEqual([0]);
    expect(claims[1].cited).toEqual([]);
  });

  it("folds a marker-only trailing fragment into the previous sentence", () => {
    // "…. [1]" splits as sentence + marker-only fragment; the citation must not
    // become a text-less claim block.
    const claims = splitClaims("Bees pollinate crops. [1]", 1);
    expect(claims).toHaveLength(1);
    expect(claims[0].cited).toEqual([0]);
  });

  it("treats paragraphs independently and survives an unterminated tail", () => {
    const claims = splitClaims("First paragraph [1].\n\nSecond without a period [2]", 2);
    expect(claims.map((c) => c.cited)).toEqual([[0], [1]]);
  });

  it("returns [] for an empty answer", () => {
    expect(splitClaims("", 5)).toEqual([]);
    expect(splitClaims("   \n ", 5)).toEqual([]);
  });
});
