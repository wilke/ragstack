import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, getTenants, queryRag } from "./client";

// WHAT A FAILED RESPONSE CARRIES (#427 W6).
//
// Five request helpers each had their own `if (!res.ok)` block and all five
// discarded the `Response` — so the `X-Request-Id` header the server has been
// sending since W1 reached nothing, and the `reason` in the 503 body was parsed
// by nobody. `throwForResponse` is the single replacement; these pin what it
// must extract and, just as importantly, what it must NOT change.

// Minimal localStorage: vitest runs in the node environment and api/config.ts
// silently degrades to "" when storage throws, which would make these pass for
// the wrong reason.
function installStorage(): void {
  const map = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    value: {
      getItem: (k: string) => map.get(k) ?? null,
      setItem: (k: string, v: string) => void map.set(k, v),
      removeItem: (k: string) => void map.delete(k),
      clear: () => map.clear(),
    },
    configurable: true,
    writable: true,
  });
}

/** A failed `Response`, only as much of one as the client actually touches. */
function failure(
  status: number,
  body: string,
  headers: Record<string, string> = {},
): Response {
  const lower = Object.fromEntries(
    Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]),
  );
  return {
    ok: false,
    status,
    statusText: "Service Unavailable",
    headers: { get: (name: string) => lower[name.toLowerCase()] ?? null },
    text: async () => body,
  } as unknown as Response;
}

function stubFetch(res: Response): void {
  vi.stubGlobal("fetch", async () => res);
}

/** Run `fn`, expecting it to reject with an ApiError, and hand that error back. */
async function caught(fn: () => Promise<unknown>): Promise<ApiError> {
  try {
    await fn();
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError);
    return e as ApiError;
  }
  throw new Error("expected the request to reject, but it resolved");
}

beforeEach(installStorage);
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("throwForResponse", () => {
  it("reads the request id from the X-Request-Id header", async () => {
    stubFetch(
      failure(503, '{"detail":"qdrant unavailable: …","reason":"timeout"}', {
        "X-Request-Id": "8f3a2b1c9d4e5f60",
      }),
    );

    const error = await caught(() => queryRag({ query: "what is RAG?" }));

    expect(error.status).toBe(503);
    expect(error.requestId).toBe("8f3a2b1c9d4e5f60");
    expect(error.reason).toBe("timeout");
  });

  it("falls back to the body's request_id when the header is unreadable", async () => {
    // Cross-origin without `Access-Control-Expose-Headers` the header reads as
    // null even though the server sent it. W1 added the expose list, but the
    // body carries the same id redundantly and a copy-pasted body is the other
    // way a user hands an operator the id.
    stubFetch(failure(503, '{"detail":"…","reason":"unreachable","request_id":"aabbccdd00112233"}'));

    const error = await caught(() => queryRag({ query: "x" }));

    expect(error.requestId).toBe("aabbccdd00112233");
    expect(error.reason).toBe("unreachable");
  });

  it("prefers the header over the body when both are present", async () => {
    stubFetch(
      failure(503, '{"detail":"…","request_id":"from_the_body00"}', {
        "x-request-id": "8f3a2b1c9d4e5f60",
      }),
    );

    expect((await caught(() => queryRag({ query: "x" }))).requestId).toBe("8f3a2b1c9d4e5f60");
  });

  it("survives a body that is not JSON at all", async () => {
    // An nginx 502 page, a proxy timeout, a truncated response. A SyntaxError
    // escaping here would replace a useful ApiError with a useless one and lose
    // the status — so the parse is guarded and the raw text is still the message.
    stubFetch(failure(502, "<html><body>502 Bad Gateway</body></html>"));

    const error = await caught(() => queryRag({ query: "x" }));

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(502);
    expect(error.message).toContain("502 Bad Gateway");
    expect(error.requestId).toBeUndefined();
    expect(error.reason).toBeUndefined();
  });

  it("survives a JSON body that is not an object", async () => {
    stubFetch(failure(500, '"just a string"'));
    const error = await caught(() => queryRag({ query: "x" }));
    expect(error.reason).toBeUndefined();
    expect(error.requestId).toBeUndefined();
  });

  it("ignores non-string request_id and reason values", async () => {
    stubFetch(failure(503, '{"detail":"…","request_id":42,"reason":["timeout"]}'));
    const error = await caught(() => queryRag({ query: "x" }));
    expect(error.requestId).toBeUndefined();
    expect(error.reason).toBeUndefined();
  });

  it("leaves message as the raw body, which lib/auth.ts still words from", async () => {
    // `apiFailure` hands `message` to signInMessage as the response body. If
    // this became the parsed `detail`, every sign-in sentence would change.
    stubFetch(failure(401, '{"detail":"invalid or expired bearer credential"}'));

    const error = await caught(() => getTenants("some-key"));

    expect(error.message).toBe('{"detail":"invalid or expired bearer credential"}');
  });

  it("falls back to statusText for an empty body, as it always did", async () => {
    stubFetch(failure(503, ""));
    expect((await caught(() => queryRag({ query: "x" }))).message).toBe("Service Unavailable");
  });

  it("applies to the GET helpers too, not just POST", async () => {
    // The five blocks it replaced were spread across post/get/del/delJson and
    // the multipart upload; a refactor that only covered `post` would leave the
    // ops dashboard's errors id-less.
    stubFetch(failure(503, '{"detail":"…","reason":"error"}', { "x-request-id": "0011223344556677" }));

    const error = await caught(() => getTenants("some-key"));

    expect(error.requestId).toBe("0011223344556677");
    expect(error.reason).toBe("error");
  });
});
