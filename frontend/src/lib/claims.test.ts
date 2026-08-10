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

// Citation attribution when the marker follows terminal punctuation. The
// generator emits both forms; before the trailing-marker clause in splitClaims
// every citation shifted one claim down and claim 1 rendered uncited.
describe("citations after the period", () => {
  it("attaches a trailing marker to the sentence it follows", () => {
    const c = splitClaims("Bees pollinate flowers. [1] Flowers produce nectar. [2]", 2);
    expect(c.map((x) => x.text)).toEqual(["Bees pollinate flowers.", "Flowers produce nectar."]);
    expect(c[0].cited).toEqual([0]);
    expect(c[1].cited).toEqual([1]);
  });

  it("agrees with the marker-before-the-period form", () => {
    const after = splitClaims("A is true. [1] B is false. [2] C is unknown. [3]", 3);
    const before = splitClaims("A is true [1]. B is false [2]. C is unknown [3].", 3);
    expect(after.map((x) => x.cited)).toEqual([[0], [1], [2]]);
    expect(after.map((x) => x.cited)).toEqual(before.map((x) => x.cited));
  });

  it("keeps multi-source and out-of-range markers correct", () => {
    const c = splitClaims("Two sources agree. [1, 2] One is missing. [9]", 2);
    expect(c[0].cited).toEqual([0, 1]);
    expect(c[1].cited).toEqual([]); // [9] points outside the retrieved set
  });
});
