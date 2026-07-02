import { afterEach, describe, expect, it, vi } from "vitest";
import { type FeedbackEvent, hashAnswer, recordFeedback } from "./feedback";

const KEY = "ragstack.feedback";

function mockSessionStorage() {
  const store = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  });
  return store;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("hashAnswer", () => {
  it("is deterministic and distinguishes different answers", () => {
    expect(hashAnswer("hello")).toBe(hashAnswer("hello"));
    expect(hashAnswer("a")).not.toBe(hashAnswer("b"));
  });
});

describe("recordFeedback", () => {
  it("appends events to sessionStorage with a timestamp", () => {
    const store = mockSessionStorage();
    recordFeedback({ query: "q1", answerHash: "h1", verdict: "up" });
    recordFeedback({ query: "q2", answerHash: "h2", verdict: "down" });

    const saved = JSON.parse(store.get(KEY)!) as FeedbackEvent[];
    expect(saved).toHaveLength(2);
    expect(saved[0]).toMatchObject({ query: "q1", answerHash: "h1", verdict: "up" });
    expect(saved[1]).toMatchObject({ query: "q2", verdict: "down" });
    expect(typeof saved[0].ts).toBe("number");
  });

  it("never throws when sessionStorage is unavailable", () => {
    vi.stubGlobal("sessionStorage", undefined);
    expect(() =>
      recordFeedback({ query: "q", answerHash: "h", verdict: "up" }),
    ).not.toThrow();
  });

  it("never throws when a write fails (e.g. quota exceeded)", () => {
    vi.stubGlobal("sessionStorage", {
      getItem: () => null,
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    });
    expect(() =>
      recordFeedback({ query: "q", answerHash: "h", verdict: "down" }),
    ).not.toThrow();
  });
});
