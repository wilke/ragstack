import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getTenants } from "./client";
import {
  bindTokenToBase,
  clearStoredToken,
  getStoredCredential,
  setApiBase,
  setStoredCredential,
} from "./config";

// Where a credential is actually allowed to go — the seam between api/config.ts
// (storage: which backend is selected, which credential kind is active, which
// backend a token was saved for) and api/client.ts (which header a request
// carries). auth.test.ts covers the rules in isolation; this exercises the real
// modules against real storage, because the bug class here is precisely the two
// disagreeing.
//
// THE SCENARIO: the app threads one opaque credential STRING through React
// state, captured whenever a component or a react-query `queryFn` closure was
// created. The backend selection and the token→base binding live in
// localStorage and are rewritten later — by the backend switcher (which then
// calls `queryClient.invalidateQueries()`, refiring every existing observer
// with its OLD closed-over value) and by other tabs sharing that storage. A
// BV-BRC token has no audience claim and can't be revoked before it expires, so
// one such request hands the user's whole BV-BRC session to a host they never
// confirmed.

// Minimal localStorage; vitest runs in the node environment, and config.ts
// silently degrades to "" if storage throws, which would make every assertion
// below pass for the wrong reason.
function installStorage(): void {
  const map = new Map<string, string>();
  const storage = {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
    key: (i: number) => [...map.keys()][i] ?? null,
    get length() {
      return map.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: storage,
    configurable: true,
    writable: true,
  });
}

const TOKEN = "un=alice@patricbrc.org|tokenid=t|sig=deadbeef";

interface Sent {
  url: string;
  headers: Record<string, string>;
}

let sent: Sent[] = [];

beforeEach(() => {
  installStorage();
  sent = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    sent.push({ url, headers: { ...((init?.headers ?? {}) as Record<string, string>) } });
    return {
      ok: true,
      json: async () => ({ tenant: "default", role: "user", auth_enabled: false }),
    } as Response;
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

/** Sign in with a bearer token at `base`, the way LoginPanel does. */
function signIn(base: string): string {
  setApiBase(base);
  setStoredCredential({ mode: "bearer", value: TOKEN });
  // LoginPanel binds explicitly after persisting: setStoredCredential will not
  // MOVE an existing token's binding (that would let a stale tab re-bind by
  // merely flipping mode), so re-confirming the same token for a new backend
  // has to say so.
  bindTokenToBase(base);
  return getStoredCredential().value; // what React state would be holding
}

describe("credential routing", () => {
  it("sends a bound token to the backend it was confirmed for", async () => {
    const held = signIn("/be/asm");
    await getTenants(held);
    expect(sent[0].url).toBe("/be/asm/v1/stats/tenants");
    expect(sent[0].headers.Authorization).toBe(TOKEN);
  });

  it("does NOT follow the token to a backend chosen after sign-in", async () => {
    // The switcher's exact sequence, and the stale value a refetch still holds.
    const held = signIn("/be/asm");
    setApiBase("https://evil.example");
    await getTenants(held);
    expect(sent[0].url).toBe("https://evil.example/v1/stats/tenants");
    expect(sent[0].headers.Authorization).toBeUndefined();
    expect(sent[0].headers["X-API-Key"]).toBeUndefined();
  });

  it("starts sending it again once the user re-confirms it for the new backend", async () => {
    signIn("/be/asm");
    setApiBase("/be/lucid");
    const reconfirmed = signIn("/be/lucid"); // the panel's "Send it to …" button
    await getTenants(reconfirmed);
    expect(sent[0].headers.Authorization).toBe(TOKEN);
  });

  it("does not let a stale tab re-bind the token by merely persisting it", async () => {
    // Tab B signed in at /be/asm. Tab A then switches to another backend —
    // localStorage is shared, but tab B's React state (and the `base` its login
    // panel renders) is frozen at mount. If persisting the active credential
    // re-bound the token to the LIVE base, tab B flipping to bearer mode — or
    // any re-render that writes the credential back — would silently hand the
    // token to a host tab B never displayed. Binding must be explicit.
    signIn("/be/asm");
    setApiBase("https://evil.example"); // the other tab moved it
    setStoredCredential({ mode: "bearer", value: TOKEN }); // stale tab persists
    await getTenants(getStoredCredential().value);
    expect(sent[0].url).toBe("https://evil.example/v1/stats/tenants");
    expect(sent[0].headers.Authorization).toBeUndefined();
    expect(sent[0].headers["X-API-Key"]).toBeUndefined();
  });

  it("does not relabel an API key as Authorization when another tab signs in", async () => {
    setApiBase("");
    setStoredCredential({ mode: "apikey", value: "SECRET-API-KEY" });
    const held = getStoredCredential().value;
    setStoredCredential({ mode: "bearer", value: TOKEN }); // the other tab
    await getTenants(held);
    expect(sent[0].headers).toEqual({});
  });

  it("does not relabel a token as X-API-Key when another tab signs out", async () => {
    // The dangerous direction: a keyless backend counts any X-API-Key as
    // present and resolves the caller to the default tenant, which production
    // gives DEFAULT_ROLE=admin — a 200 that looks like a successful sign-in.
    const held = signIn("");
    clearStoredToken();
    setStoredCredential({ mode: "apikey", value: "k" });
    await getTenants(held);
    expect(sent[0].headers).toEqual({});
  });

  it("still sends a Compare lane's own key, pinned to X-API-Key", async () => {
    signIn("/be/asm");
    await getTenants({ mode: "apikey", value: "lane-key" });
    expect(sent[0].headers["X-API-Key"]).toBe("lane-key");
    expect(sent[0].headers.Authorization).toBeUndefined();
  });
});
