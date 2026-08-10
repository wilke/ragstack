import { describe, expect, it } from "vitest";
import { matchSpans, splitSentences } from "./answerMatch";

describe("splitSentences", () => {
  it("splits on terminal punctuation", () => {
    expect(splitSentences("One two. Three four! Five six?")).toEqual([
      "One two.",
      "Three four!",
      "Five six?",
    ]);
  });

  it("keeps closing quotes with their sentence", () => {
    expect(splitSentences('He said "stop here." Then he left.')).toEqual([
      'He said "stop here."',
      "Then he left.",
    ]);
  });

  it("keeps an unterminated tail as a sentence", () => {
    expect(splitSentences("First one. no terminal punctuation here")).toEqual([
      "First one.",
      "no terminal punctuation here",
    ]);
  });

  it("treats newlines as sentence boundaries", () => {
    expect(splitSentences("line one\nline two")).toEqual(["line one", "line two"]);
  });

  it("returns [] for empty and whitespace-only input", () => {
    expect(splitSentences("")).toEqual([]);
    expect(splitSentences("  \n\n  ")).toEqual([]);
  });
});

describe("matchSpans", () => {
  const QUOTE = "Coral reefs are declining because ocean temperatures keep rising.";

  it("scores an exact-quote chunk sentence 1 and offsets slice to that sentence", () => {
    const chunk = `Unrelated intro text about something else entirely. ${QUOTE} Another closing remark about fisheries policy.`;
    const spans = matchSpans(`${QUOTE} [1]`, chunk);
    expect(spans).toHaveLength(1);
    expect(spans[0].score).toBe(1);
    expect(chunk.slice(spans[0].start, spans[0].end)).toBe(QUOTE);
  });

  it("matches a paraphrase whose content-word overlap clears the threshold", () => {
    const answer = "Rising ocean temperatures have caused a decline in coral reefs worldwide.";
    const spans = matchSpans(answer, QUOTE);
    expect(spans).toHaveLength(1);
    // shared {rising, ocean, temperatures, coral, reefs} = 5 of min(7, 8) tokens
    expect(spans[0].score).toBeCloseTo(5 / 7);
    expect(QUOTE.slice(spans[0].start, spans[0].end)).toBe(QUOTE);
  });

  it("rejects weak topical overlap below the threshold", () => {
    const answer = "Rising ocean temperatures have caused a decline in coral reefs worldwide.";
    const chunk = "The weather report mentioned ocean temperatures briefly.";
    // shared {ocean, temperatures} = 2 of min(6, 8) → 0.33 < 0.5
    expect(matchSpans(answer, chunk)).toEqual([]);
  });

  it("lets the caller lower the threshold", () => {
    const answer = "Rising ocean temperatures have caused a decline in coral reefs worldwide.";
    const chunk = "The weather report mentioned ocean temperatures briefly.";
    expect(matchSpans(answer, chunk, { threshold: 0.3 })).toHaveLength(1);
  });

  it("applies the shared-token floor: one common token never matches alone", () => {
    // "Ocean life." shares only {ocean} — coefficient 1/2 would clear 0.5, the
    // floor of 2 shared tokens is what rejects it.
    const answer = "The ocean is warming rapidly this century.";
    expect(matchSpans(answer, "Ocean life.")).toEqual([]);
  });

  it("returns [] when nothing in the chunk relates to the answer", () => {
    const answer = "Photosynthesis converts sunlight into chemical energy.";
    const chunk = "The committee approved next year's municipal parking budget. Voting closed at noon.";
    expect(matchSpans(answer, chunk)).toEqual([]);
  });

  it("returns [] for empty answer, empty chunk, and marker-only answers", () => {
    expect(matchSpans("", QUOTE)).toEqual([]);
    expect(matchSpans(QUOTE, "")).toEqual([]);
    expect(matchSpans("[1] [2, 3]", QUOTE)).toEqual([]);
  });

  it("merges adjacent matching sentences into one span keeping the max score", () => {
    const chunk =
      "Alpha beta gamma delta. Coral reefs are declining because ocean temperatures keep rising. Totally different closing words here.";
    const answer =
      "Alpha beta gamma delta. Rising ocean temperatures have caused a decline in coral reefs worldwide.";
    const spans = matchSpans(answer, chunk);
    expect(spans).toHaveLength(1);
    expect(chunk.slice(spans[0].start, spans[0].end)).toBe(
      "Alpha beta gamma delta. Coral reefs are declining because ocean temperatures keep rising.",
    );
    expect(spans[0].score).toBe(1); // max of the merged pair, not a sum
  });

  it("keeps non-adjacent matches as separate ordered spans", () => {
    const chunk =
      "Alpha beta gamma delta. Unrelated middle filler sentence entirely. Epsilon zeta eta theta.";
    const answer = "Alpha beta gamma delta. Epsilon zeta eta theta.";
    const spans = matchSpans(answer, chunk);
    expect(spans).toHaveLength(2);
    expect(spans[0].start).toBeLessThan(spans[1].start);
    expect(spans[0].end).toBeLessThanOrEqual(spans[1].start);
    expect(chunk.slice(spans[0].start, spans[0].end)).toBe("Alpha beta gamma delta.");
    expect(chunk.slice(spans[1].start, spans[1].end)).toBe("Epsilon zeta eta theta.");
  });

  it("is case-insensitive and ignores punctuation differences", () => {
    const spans = matchSpans(
      "coral reefs are declining because ocean temperatures keep rising",
      "CORAL REEFS — ARE DECLINING, BECAUSE OCEAN TEMPERATURES KEEP RISING!",
    );
    expect(spans).toHaveLength(1);
    expect(spans[0].score).toBe(1);
  });

  it("handles accented unicode tokens", () => {
    const chunk = "Le café sert des croissants chauds tous les matins.";
    const spans = matchSpans("Le café sert des croissants chauds.", chunk);
    expect(spans).toHaveLength(1);
    expect(spans[0].score).toBe(1);
    expect(chunk.slice(spans[0].start, spans[0].end)).toBe(chunk);
  });

  it("trims spans to visible text amid irregular whitespace", () => {
    const chunk = `   ${QUOTE}   \n\n  Extra unrelated line about municipal budgets here.`;
    const spans = matchSpans(QUOTE, chunk);
    expect(spans).toHaveLength(1);
    expect(chunk.slice(spans[0].start, spans[0].end)).toBe(QUOTE);
  });

  it("returns in-bounds, ordered, non-overlapping spans with scores in (0, 1]", () => {
    const chunk =
      "Coral reefs are declining because ocean temperatures keep rising. Reef decline accelerates coastal erosion. Rising temperatures also bleach coral reefs across the tropics.";
    const answer =
      "Rising ocean temperatures have caused a decline in coral reefs worldwide. Coral bleaching across the tropics is accelerating.";
    const spans = matchSpans(answer, chunk);
    expect(spans.length).toBeGreaterThan(0);
    let prevEnd = -1;
    for (const s of spans) {
      expect(s.start).toBeGreaterThan(prevEnd);
      expect(s.end).toBeGreaterThan(s.start);
      expect(s.end).toBeLessThanOrEqual(chunk.length);
      expect(s.score).toBeGreaterThan(0);
      expect(s.score).toBeLessThanOrEqual(1);
      prevEnd = s.end;
    }
  });

  it("is deterministic across calls", () => {
    const answer = "Rising ocean temperatures have caused a decline in coral reefs worldwide.";
    expect(matchSpans(answer, QUOTE)).toEqual(matchSpans(answer, QUOTE));
  });
});
