import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The two rules that make this bundle safe to serve from an internet-reachable
// mount, asserted rather than commented:
//
//   1. the session id goes to sessionStorage and NOWHERE else — in particular
//      never localStorage, which survives the tab, the reboot and the user
//      walking away, on an origin shared with every tenant UI;
//   2. a 401 on any read clears it, so the app cannot sit on a dead session
//      showing a stale fleet.
//
// Both are enforced through the real modules: a fake `fetch` and fake storages,
// no mocked internals. `localStorage` here is a SPY that fails the test on any
// write — an assertion about the whole module graph, not about one call site.

interface FakeStorage extends Storage {
  readonly map: Map<string, string>;
}

function makeStorage(onWrite?: (key: string, value: string) => void): FakeStorage {
  const map = new Map<string, string>();
  return {
    map,
    get length() {
      return map.size;
    },
    key: (i: number) => [...map.keys()][i] ?? null,
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => {
      onWrite?.(k, v);
      map.set(k, v);
    },
    removeItem: (k: string) => {
      map.delete(k);
    },
    clear: () => map.clear(),
  } as FakeStorage;
}

const SESSION_ID = "a".repeat(64);

let sessionStore: FakeStorage;
let localWrites: string[];

beforeEach(() => {
  vi.resetModules();
  localWrites = [];
  sessionStore = makeStorage();
  vi.stubGlobal("sessionStorage", sessionStore);
  vi.stubGlobal(
    "localStorage",
    makeStorage((k) => {
      localWrites.push(k);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("ctl session", () => {
  it("keeps the session id in sessionStorage only, never localStorage", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(201, {
        session_id: SESSION_ID,
        principal: "key:pr-b-operator",
        role: "operator",
        expires_at: "2026-09-12T15:43:45Z",
        reads_only: true,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { getSession, signInWithApiKey, SESSION_STORAGE_KEY } = await import("./session");

    const session = await signInWithApiKey("0123456789abcdef");
    expect(session.sessionId).toBe(SESSION_ID);
    expect(session.role).toBe("operator");
    expect(getSession()?.sessionId).toBe(SESSION_ID);

    // Stored exactly once, under the scoped key, in sessionStorage.
    expect(sessionStore.getItem(SESSION_STORAGE_KEY)).toContain(SESSION_ID);
    expect(localWrites).toEqual([]);

    // And the KEY was spent, not kept: it appears in the sign-in request and in
    // no storage at all.
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("0123456789abcdef");
    expect(JSON.stringify([...sessionStore.map.values()])).not.toContain("0123456789abcdef");
  });

  it("presents the session as `Authorization: Session <id>` on a read", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(201, {
          session_id: SESSION_ID,
          principal: "key:viewer",
          role: "viewer",
          expires_at: "2026-09-12T15:43:45Z",
          reads_only: true,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { tenants: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const { signInWithApiKey } = await import("./session");
    const { get } = await import("../api/http");

    await signInWithApiKey("k");
    await get("/v1/fleet");

    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe(`Session ${SESSION_ID}`);
    // No cookie rides along, and nothing may be served from a cache.
    expect(init.credentials).toBe("omit");
    expect(init.cache).toBe("no-store");
    expect(headers["Cache-Control"]).toBe("no-store");
  });

  it("clears the session when any read answers 401", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(201, {
          session_id: SESSION_ID,
          principal: "key:viewer",
          role: "viewer",
          expires_at: "2026-09-12T15:43:45Z",
          reads_only: true,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          401,
          { detail: "session expired", code: "auth_required", request_id: "0123456789abcdef" },
          { "X-Request-Id": "0123456789abcdef" },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const { getSession, signInWithApiKey, SESSION_STORAGE_KEY } = await import("./session");
    const { get, CtlError } = await import("../api/http");

    await signInWithApiKey("k");
    expect(getSession()).not.toBeNull();

    await expect(get("/v1/fleet")).rejects.toBeInstanceOf(CtlError);

    expect(getSession()).toBeNull();
    expect(sessionStore.getItem(SESSION_STORAGE_KEY)).toBeNull();
    expect(localWrites).toEqual([]);
  });

  it("carries the contract's error shape on a 403 and KEEPS the session", async () => {
    // Signed in FIRST. Asserting `getSession() === null` after a 403 while
    // never having signed in proved nothing at all — it would have passed just
    // as well if 403 cleared the session, which is exactly the bug the
    // assertion was there to catch. A viewer opening the Logs tab gets a 403 on
    // every visit; if that signed them out, the dashboard would be unusable.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(201, {
          session_id: SESSION_ID,
          principal: "key:viewer",
          role: "viewer",
          expires_at: "2026-09-12T15:43:45Z",
          reads_only: true,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          403,
          { detail: "operator only", code: "forbidden", request_id: "fedcba9876543210" },
          { "X-Request-Id": "fedcba9876543210" },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const { getSession, signInWithApiKey, SESSION_STORAGE_KEY } = await import("./session");
    const { get } = await import("../api/http");

    await signInWithApiKey("k");
    expect(getSession()?.sessionId).toBe(SESSION_ID);

    await expect(get("/v1/tenants/dev/logs?file=api&lines=200")).rejects.toMatchObject({
      status: 403,
      code: "forbidden",
      requestId: "fedcba9876543210",
    });

    // Still signed in, and still stored: a 403 is "your role is too low", not
    // "your session is gone".
    expect(getSession()?.sessionId).toBe(SESSION_ID);
    expect(sessionStore.getItem(SESSION_STORAGE_KEY)).toContain(SESSION_ID);
  });

  it("does not let a stale session's 401 sign out the session that replaced it", async () => {
    // The race: a read goes out under session A (a background poll, a slow
    // /logs, a hidden tab's queued refetch). Before it lands the user signs out
    // and signs back in as B. A's 401 then arrived at a handler that cleared
    // "the session" unconditionally — throwing B out, seconds after a
    // successful sign-in, with no failed request of B's own to explain it.
    const A = "a".repeat(64);
    const B = "b".repeat(64);
    const session = (id: string, principal: string) =>
      jsonResponse(201, {
        session_id: id,
        principal,
        role: "operator",
        expires_at: "2026-09-12T15:43:45Z",
        reads_only: true,
      });

    let resolveStale: (r: Response) => void = () => {};
    const stalePending = new Promise<Response>((r) => {
      resolveStale = r;
    });

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(session(A, "key:a")) // sign in as A
      .mockReturnValueOnce(stalePending) // the read A started — still in flight
      .mockResolvedValueOnce(new Response(null, { status: 204 })) // A's DELETE /v1/session
      .mockResolvedValueOnce(session(B, "key:b")); // sign in as B
    vi.stubGlobal("fetch", fetchMock);

    const { getSession, signInWithApiKey, signOut } = await import("./session");
    const { get, CtlError } = await import("../api/http");

    await signInWithApiKey("key-a");
    const stale = get("/v1/fleet"); // presents A
    stale.catch(() => {}); // the rejection is asserted below; don't trip unhandled

    // The request it carried was bound to A's signal, so signing out cancels it
    // — this test then resolves it ANYWAY, which is the case the id comparison
    // exists for: a fetch already past the point of cancellation.
    const [, staleInit] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(staleInit.signal?.aborted).toBe(false);

    await signOut();
    await signInWithApiKey("key-b");
    expect(getSession()?.sessionId).toBe(B);
    expect(staleInit.signal?.aborted).toBe(true);

    resolveStale(
      jsonResponse(401, { detail: "session expired", code: "auth_required" }),
    );
    await expect(stale).rejects.toBeInstanceOf(CtlError);

    // B is untouched: A's 401 named A.
    expect(getSession()?.sessionId).toBe(B);
    expect(getSession()?.principal).toBe("key:b");
  });

  it("still clears the session when the CURRENT session's read 401s", async () => {
    // The other half of the same rule — the id match must not turn the handler
    // off. (`clears the session when any read answers 401` above covers the
    // happy path; this one asserts it after a re-sign-in, where the module has
    // seen more than one id.)
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(201, {
          session_id: SESSION_ID,
          principal: "key:viewer",
          role: "viewer",
          expires_at: "2026-09-12T15:43:45Z",
          reads_only: true,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(401, { detail: "gone", code: "auth_required" }));
    vi.stubGlobal("fetch", fetchMock);

    const { getSession, signInWithApiKey } = await import("./session");
    const { get } = await import("../api/http");
    await signInWithApiKey("k");
    await expect(get("/v1/fleet")).rejects.toBeTruthy();
    expect(getSession()).toBeNull();
  });

  it("revokes server-side on sign-out and forgets locally even if that call fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(201, {
          session_id: SESSION_ID,
          principal: "key:viewer",
          role: "viewer",
          expires_at: "2026-09-12T15:43:45Z",
          reads_only: true,
        }),
      )
      .mockRejectedValueOnce(new TypeError("network down"));
    vi.stubGlobal("fetch", fetchMock);

    const { getSession, signInWithApiKey, signOut } = await import("./session");
    await signInWithApiKey("k");
    await signOut();

    expect(getSession()).toBeNull();
    const [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(url).toContain("/v1/session");
    expect(init.method).toBe("DELETE");
  });

  it("survives a browser that throws on the storage accessor", async () => {
    vi.stubGlobal("sessionStorage", {
      getItem() {
        throw new Error("site data blocked");
      },
      setItem() {
        throw new Error("site data blocked");
      },
      removeItem() {
        throw new Error("site data blocked");
      },
    });
    const { getSession } = await import("./session");
    expect(getSession()).toBeNull();
  });
});
