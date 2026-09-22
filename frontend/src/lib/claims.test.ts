import { describe, expect, it } from "vitest";
import { splitClaims, answerParagraphs } from "./claims";

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

// #622 review: the paragraph splitter's choices were unpinned — reverting the
// blank-line regex to every-newline, or deleting the trim/filter, failed no
// test. Each case here fails exactly one of those mutations.
describe("answerParagraphs", () => {
  it("keeps a bullet list on single newlines as ONE paragraph, breaks intact", () => {
    // The generator emits this shape; the old splitter made four paragraphs,
    // the first draft of the new one collapsed it to a run-on line.
    const out = answerParagraphs("Summary:\n- gene A [1]\n- gene B [2]\n- gene C");
    expect(out).toEqual(["Summary:\n- gene A [1]\n- gene B [2]\n- gene C"]);
  });
  it("splits on a blank line", () => {
    expect(answerParagraphs("A.\n\nB.")).toEqual(["A.", "B."]);
  });
  it("treats a whitespace-only line as blank", () => {
    expect(answerParagraphs("A.\n  \nB.")).toEqual(["A.", "B."]);
    expect(answerParagraphs("A.\n\t\nB.")).toEqual(["A.", "B."]);
  });
  it("normalises CRLF before splitting", () => {
    expect(answerParagraphs("A.\r\n\r\nB.")).toEqual(["A.", "B."]);
    expect(answerParagraphs("x\r\ny")).toEqual(["x\ny"]);
  });
  it("collapses runs of blank lines and trims the edges", () => {
    expect(answerParagraphs("\n\nA.\n\n\n\nB.\n\n")).toEqual(["A.", "B."]);
  });
  it("never yields an empty or whitespace-only paragraph", () => {
    expect(answerParagraphs("")).toEqual([]);
    expect(answerParagraphs("   \n\n  \t \n")).toEqual([]);
  });
});
