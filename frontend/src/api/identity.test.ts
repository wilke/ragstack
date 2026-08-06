import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BVBRC_EXCHANGE, exchangePassword, SignInError } from "./identity";

// The password exchange. The properties asserted here are the ones that would
// be silently violated by an innocent-looking refactor: WHERE the password goes,
// that it never lands in a URL, and that a failure says something actionable
// without echoing a response body into the UI.

interface Sent {
  url: string;
  method?: string;
  headers: Record<string, string>;
  body: string;
  credentials?: string;
}

let sent: Sent[] = [];

function stubFetch(response: { status: number; body: string }) {
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    sent.push({
      url,
      method: init?.method,
      headers: { ...((init?.headers ?? {}) as Record<string, string>) },
      body: String(init?.body ?? ""),
      credentials: init?.credentials,
    });
    return {
      ok: response.status >= 200 && response.status < 300,
      status: response.status,
      text: async () => response.body,
    } as Response;
  });
}

beforeEach(() => {
  sent = [];
});
afterEach(() => vi.unstubAllGlobals());

const TOKEN = "un=alice@patricbrc.org|tokenid=t|sig=deadbeef";

describe("password exchange", () => {
  it("posts to the PROVIDER, never to the RAGStack API", async () => {
    // The whole point of the design: RAGStack has no endpoint that accepts a
    // password and must never gain one. A refactor that "simplifies" this into
    // an app request would break that silently, so pin the destination.
    stubFetch({ status: 200, body: TOKEN });
    await exchangePassword(BVBRC_EXCHANGE, "alice", "hunter2");
    expect(sent[0].url).toBe(BVBRC_EXCHANGE.url);
    expect(sent[0].url).toMatch(/^https:\/\//);
    expect(sent[0].url).toContain("patricbrc.org");
  });

  it("never puts the password in the URL", async () => {
    stubFetch({ status: 200, body: TOKEN });
    await exchangePassword(BVBRC_EXCHANGE, "alice", "hunter2");
    // A URL is logged by proxies, kept in history, and sent as a Referer.
    expect(sent[0].url).not.toContain("hunter2");
    expect(sent[0].url).not.toContain("alice");
    expect(sent[0].method).toBe("POST");
  });

  it("sends no cookies in either direction", async () => {
    // A provider session cookie riding along would be ambient authority this
    // exchange neither needs nor wants.
    stubFetch({ status: 200, body: TOKEN });
    await exchangePassword(BVBRC_EXCHANGE, "alice", "hunter2");
    expect(sent[0].credentials).toBe("omit");
  });

  it("form-encodes the credentials, escaping specials", async () => {
    stubFetch({ status: 200, body: TOKEN });
    await exchangePassword(BVBRC_EXCHANGE, "a b&c", "p=q&r");
    const parsed = new URLSearchParams(sent[0].body);
    expect(parsed.get("username")).toBe("a b&c");
    expect(parsed.get("password")).toBe("p=q&r");
    // Simple content type -> no preflight round trip.
    expect(sent[0].headers["Content-Type"]).toBe("application/x-www-form-urlencoded");
  });

  it("returns the raw token body", async () => {
    stubFetch({ status: 200, body: `  ${TOKEN}\n` });
    await expect(exchangePassword(BVBRC_EXCHANGE, "a", "b")).resolves.toBe(TOKEN);
  });

  it("unwraps a JSON envelope if a provider uses one", async () => {
    stubFetch({ status: 200, body: JSON.stringify({ token: TOKEN }) });
    await expect(exchangePassword(BVBRC_EXCHANGE, "a", "b")).resolves.toBe(TOKEN);
  });

  it("reports bad credentials plainly", async () => {
    stubFetch({
      status: 401,
      body: JSON.stringify({ message: "Invalid username, email, or password" }),
    });
    await expect(exchangePassword(BVBRC_EXCHANGE, "a", "b")).rejects.toThrow(
      /Invalid username/,
    );
  });

  it("does not echo a non-JSON error body into the message", async () => {
    // A misrouted request can return HTML — or the request itself. Neither
    // belongs on screen.
    stubFetch({ status: 500, body: "<html><body>Internal Error hunter2</body></html>" });
    const err = await exchangePassword(BVBRC_EXCHANGE, "a", "hunter2").catch((e) => e);
    expect(err).toBeInstanceOf(SignInError);
    expect(err.message).not.toContain("<html>");
    expect(err.message).not.toContain("hunter2");
    expect(err.message).toMatch(/HTTP 500/);
  });

  it("turns a network/CORS rejection into advice, not a stack trace", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("Failed to fetch");
    });
    const err = await exchangePassword(BVBRC_EXCHANGE, "a", "b").catch((e) => e);
    expect(err).toBeInstanceOf(SignInError);
    expect(err.status).toBeNull();
    expect(err.message).toMatch(/p3-login/); // the fallback that always works
  });

  it("treats an empty success body as a failure, not a sign-in", async () => {
    stubFetch({ status: 200, body: "   " });
    await expect(exchangePassword(BVBRC_EXCHANGE, "a", "b")).rejects.toThrow(
      /no token/i,
    );
  });
});
