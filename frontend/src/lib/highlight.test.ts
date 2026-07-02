import { describe, expect, it } from "vitest";
import { segmentContent } from "./highlight";

describe("segmentContent", () => {
  it("returns one unmarked segment when no offsets are given", () => {
    expect(segmentContent("hello world")).toEqual([{ text: "hello world", marked: false }]);
  });

  it("marks a valid in-range span and reassembles to the original text", () => {
    const segs = segmentContent("hello world", 6, 11);
    expect(segs).toEqual([
      { text: "hello ", marked: false },
      { text: "world", marked: true },
    ]);
    expect(segs.map((s) => s.text).join("")).toBe("hello world");
  });

  it("marks a span in the middle with before and after", () => {
    const segs = segmentContent("abcdef", 2, 4);
    expect(segs).toEqual([
      { text: "ab", marked: false },
      { text: "cd", marked: true },
      { text: "ef", marked: false },
    ]);
  });

  it.each([
    ["out of range end", 0, 999],
    ["negative start", -1, 3],
    ["start >= end", 4, 2],
    ["non-integer", 1.5, 3],
  ])("degrades to a single unmarked segment for %s", (_label, start, end) => {
    expect(segmentContent("abcdef", start, end)).toEqual([{ text: "abcdef", marked: false }]);
  });

  it("never throws and preserves the full string across arbitrary offsets", () => {
    const content = "the quick brown fox";
    for (let s = -2; s <= content.length + 2; s++) {
      for (let e = -2; e <= content.length + 2; e++) {
        const joined = segmentContent(content, s, e).map((x) => x.text).join("");
        expect(joined).toBe(content);
      }
    }
  });
});
