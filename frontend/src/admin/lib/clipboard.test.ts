import { afterEach, describe, expect, it, vi } from "vitest";
import { clipboardAvailable, copyToClipboard } from "./clipboard";

// Node has a global `navigator` (without `clipboard`) and no `window`; each
// case stubs exactly the globals it is about and restores them afterwards.

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubClipboard(writeText: (t: string) => Promise<void>, secure = true) {
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  vi.stubGlobal("window", { isSecureContext: secure });
}

describe("clipboardAvailable", () => {
  it("is false without navigator.clipboard", () => {
    vi.stubGlobal("navigator", {});
    expect(clipboardAvailable()).toBe(false);
  });

  it("is false in an insecure context", () => {
    stubClipboard(async () => {}, false);
    expect(clipboardAvailable()).toBe(false);
  });

  it("is true with a clipboard in a secure context", () => {
    stubClipboard(async () => {});
    expect(clipboardAvailable()).toBe(true);
  });
});

describe("copyToClipboard", () => {
  it("writes the text and resolves true", async () => {
    const writeText = vi.fn(async () => {});
    stubClipboard(writeText);
    await expect(copyToClipboard("abc")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("abc");
  });

  it("resolves false instead of throwing when the write is denied", async () => {
    stubClipboard(async () => {
      throw new Error("NotAllowedError");
    });
    await expect(copyToClipboard("abc")).resolves.toBe(false);
  });

  it("resolves false and writes nothing when unavailable", async () => {
    const writeText = vi.fn(async () => {});
    stubClipboard(writeText, false);
    await expect(copyToClipboard("abc")).resolves.toBe(false);
    expect(writeText).not.toHaveBeenCalled();
  });
});
