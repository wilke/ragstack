import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The per-request key rule, asserted through the real modules (session.ts
// installs the credential into http.ts; ops.ts builds the envelope) with only
// `fetch` and the two storages faked:
//
//   * a mutation carries the ctl key in its BODY as `ctl_api_key`, rides on
//     the session header, and never puts the key in a header;
//   * `fetchSecrets` is the one call that sends the key as `X-API-Key`, and it
//     sends NO session header (the endpoint refuses sessions);
//   * the key is never written to either storage;
//   * the status decides the answer: 200 → Plan, 202 → Job + Location; the
//     refusals surface as CtlError with their `extra`.

const SESSION_ID = "b".repeat(64);
const CTL_KEY = "ctl-key-0123456789abcdef-SECRET";

interface SpyStorage extends Storage {
  readonly map: Map<string, string>;
}

function spyStorage(): SpyStorage {
  const map = new Map<string, string>();
  return {
    map,
    get length() {
      return map.size;
    },
    key: vi.fn((i: number) => [...map.keys()][i] ?? null),
    getItem: vi.fn((k: string) => map.get(k) ?? null),
    setItem: vi.fn((k: string, v: string) => {
      map.set(k, v);
    }),
    removeItem: vi.fn((k: string) => {
      map.delete(k);
    }),
    clear: vi.fn(() => map.clear()),
  } as unknown as SpyStorage;
}

let sessionStore: SpyStorage;
let localStore: SpyStorage;

beforeEach(() => {
  vi.resetModules();
  sessionStore = spyStorage();
  localStore = spyStorage();
  vi.stubGlobal("sessionStorage", sessionStore);
  vi.stubGlobal("localStorage", localStore);
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

const SESSION_BODY = {
  session_id: SESSION_ID,
  principal: "key:operator",
  role: "operator",
  expires_at: "2026-10-08T23:00:00Z",
  reads_only: true,
};

const PLAN = {
  plan_hash: "sha256:" + "0".repeat(64),
  op: "restart",
  tenant: "dev",
  registry_generation: 7,
  schema_version: 1,
  doctor: { status: "green", findings: [] },
  requires_confirm: false,
  confirm_value: null,
  steps: [],
  warnings: [],
};

const JOB_ID = "01JAAAAAAAAAAAAAAAAAAAAAAA";
const JOB = {
  id: JOB_ID,
  op: "restart",
  tenant: "dev",
  principal: "key:operator",
  auth_method: "session",
  sudo_user: null,
  state: "queued",
  plan_hash: PLAN.plan_hash,
  request_id: "0123456789abcdef",
  idempotency_key: "ui-restart-x0000000",
  created_at: "2026-10-08T12:00:00Z",
  started_at: null,
  finished_at: null,
  worker: null,
  lock: null,
  reservations: [],
  current_step: null,
  steps: [],
  result: null,
  error: null,
  rollback: null,
};

type Call = [string, RequestInit];

/** Sign in (call 0), then answer the mutation with `answers` in order. */
async function signedIn(...answers: Response[]) {
  const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(201, SESSION_BODY));
  for (const a of answers) fetchMock.mockResolvedValueOnce(a);
  vi.stubGlobal("fetch", fetchMock);
  const session = await import("../auth/session");
  await session.signInWithApiKey("sign-in-key-0000");
  const ops = await import("./ops");
  const http = await import("./http");
  // From here on the storages must see no write at all.
  vi.mocked(sessionStore.setItem).mockClear();
  vi.mocked(localStore.getItem).mockClear();
  vi.mocked(localStore.setItem).mockClear();
  return { fetchMock, ops, http, session };
}

function call(fetchMock: ReturnType<typeof vi.fn>, i: number) {
  const [url, init] = fetchMock.mock.calls[i] as unknown as Call;
  const headers = init.headers as Record<string, string>;
  const body = init.body === undefined ? undefined : JSON.parse(init.body as string);
  return { url, init, headers, body };
}

function assertNoStorage() {
  expect(sessionStore.setItem).not.toHaveBeenCalled();
  for (const m of ["getItem", "setItem", "removeItem", "key", "clear"] as const) {
    expect(localStore[m]).not.toHaveBeenCalled();
  }
  expect(JSON.stringify([...sessionStore.map.values(), ...localStore.map.values()])).not.toContain(CTL_KEY);
}

describe("submitOp", () => {
  it("puts ctl_api_key in the body, rides on the session, sends no X-API-Key", async () => {
    const { fetchMock, ops } = await signedIn(jsonResponse(200, PLAN));
    const key = ops.newIdempotencyKey("restart");

    const out = await ops.submitOp("dev", "restart", {
      args: { only: ["api"] },
      ctlKey: CTL_KEY,
      dryRun: true,
      idempotencyKey: key,
    });

    expect(out).toEqual({ kind: "plan", plan: PLAN });
    const c = call(fetchMock, 1);
    expect(c.url).toMatch(/\/v1\/tenants\/dev\/ops\/restart$/);
    expect(c.init.method).toBe("POST");
    expect(c.body).toEqual({
      dry_run: true,
      idempotency_key: key,
      ctl_api_key: CTL_KEY,
      args: { only: ["api"] },
    });
    expect(c.headers.Authorization).toBe(`Session ${SESSION_ID}`);
    expect(c.headers["X-API-Key"]).toBeUndefined();
    expect(JSON.stringify(c.headers)).not.toContain(CTL_KEY);
    expect(c.url).not.toContain(CTL_KEY);
    assertNoStorage();
  });

  it("returns the Job and its Location on 202, and passes confirm/force through", async () => {
    const { fetchMock, ops } = await signedIn(
      jsonResponse(202, JOB, { Location: `/v1/jobs/${JOB_ID}` }),
    );
    const out = await ops.submitOp("dev", "stop", {
      args: {},
      ctlKey: CTL_KEY,
      dryRun: false,
      confirm: "dev",
      forceWithDoctorDiff: "sha256:" + "a".repeat(64),
      idempotencyKey: "ui-stop-0000000000",
    });

    expect(out).toEqual({ kind: "job", job: JOB, location: `/v1/jobs/${JOB_ID}` });
    const c = call(fetchMock, 1);
    expect(c.body.dry_run).toBe(false);
    expect(c.body.confirm).toBe("dev");
    expect(c.body.force_with_doctor_diff).toBe("sha256:" + "a".repeat(64));
    assertNoStorage();
  });

  it("surfaces 428 confirm_required with extra.confirm_value, keeping the session", async () => {
    const { ops, http, session } = await signedIn(
      jsonResponse(428, {
        code: "confirm_required",
        detail: "needs confirm",
        request_id: "1111111111111111",
        extra: { confirm_value: "dev" },
      }),
    );
    const err = await ops
      .submitOp("dev", "decommission", {
        args: {},
        ctlKey: CTL_KEY,
        dryRun: false,
        idempotencyKey: "ui-decommission-00000",
      })
      .catch((e: unknown) => e);

    expect(err).toBeInstanceOf(http.CtlError);
    expect(err).toMatchObject({ status: 428, code: "confirm_required", extra: { confirm_value: "dev" } });
    // The thrown error carries the server's answer, never the request.
    expect(JSON.stringify(err)).not.toContain(CTL_KEY);
    expect(String((err as Error).message)).not.toContain(CTL_KEY);
    expect(session.getSession()?.sessionId).toBe(SESSION_ID);
    assertNoStorage();
  });

  it.each([
    [409, "plan_stale"],
    [409, "locked"],
    [409, "doctor_red"],
    [409, "duplicate"],
    [409, "refused"],
    [422, "validation"],
    [403, "forbidden"],
  ])("surfaces %i %s as a CtlError", async (status, code) => {
    const { ops } = await signedIn(
      jsonResponse(status, { code, detail: "x", request_id: "2222222222222222", extra: { job_id: JOB_ID } }),
    );
    await expect(
      ops.submitOp("dev", "start", {
        args: {},
        ctlKey: CTL_KEY,
        dryRun: false,
        idempotencyKey: "ui-start-0000000000",
      }),
    ).rejects.toMatchObject({ status, code, extra: { job_id: JOB_ID } });
  });

  it("refuses in the browser, sending nothing, without a key or with a bad idempotency key", async () => {
    const { fetchMock, ops } = await signedIn();
    await expect(
      ops.submitOp("dev", "start", { args: {}, ctlKey: "", dryRun: true, idempotencyKey: "ui-start-00000000" }),
    ).rejects.toMatchObject({ status: 0, code: "validation" });
    await expect(
      ops.submitOp("dev", "start", { args: {}, ctlKey: CTL_KEY, dryRun: true, idempotencyKey: "short" }),
    ).rejects.toMatchObject({ status: 0, code: "validation" });
    expect(fetchMock).toHaveBeenCalledTimes(1); // the sign-in only
  });

  it("encodes the tenant name into the path", async () => {
    const { fetchMock, ops } = await signedIn(jsonResponse(200, PLAN));
    await ops.submitOp("a/b", "start", { args: {}, ctlKey: CTL_KEY, dryRun: true, idempotencyKey: "ui-start-00000000" });
    expect(call(fetchMock, 1).url).toMatch(/\/v1\/tenants\/a%2Fb\/ops\/start$/);
  });
});

describe("the other mutations", () => {
  it("createTenant posts the create_request envelope to /v1/tenants", async () => {
    const { fetchMock, ops } = await signedIn(jsonResponse(200, { ...PLAN, op: "create" }));
    const args = { name: "newt", artifact_id: "art-1", es_heap: "1g", postgres: "sqlite" as const };
    await ops.createTenant({
      args: args as Parameters<typeof ops.createTenant>[0]["args"],
      ctlKey: CTL_KEY,
      dryRun: true,
      idempotencyKey: "ui-create-00000000",
    });
    const c = call(fetchMock, 1);
    expect(c.url).toMatch(/\/v1\/tenants$/);
    expect(c.body).toMatchObject({ ctl_api_key: CTL_KEY, dry_run: true, args });
    expect(c.headers["X-API-Key"]).toBeUndefined();
  });

  it.each(["resume", "continue", "cancel"] as const)("jobContinuation %s posts to /v1/jobs/{id}/%s", async (action) => {
    const { fetchMock, ops } = await signedIn(jsonResponse(202, JOB, { Location: `/v1/jobs/${JOB_ID}` }));
    const out = await ops.jobContinuation(JOB_ID, action, {
      ctlKey: CTL_KEY,
      dryRun: false,
      idempotencyKey: `ui-${action}-00000000`,
    });
    expect(out.kind).toBe("job");
    const c = call(fetchMock, 1);
    expect(c.url).toMatch(new RegExp(`/v1/jobs/${JOB_ID}/${action}$`));
    expect(c.body).toMatchObject({ ctl_api_key: CTL_KEY, args: {} });
    expect(c.headers.Authorization).toBe(`Session ${SESSION_ID}`);
  });

  it("applyGateway posts to /v1/gateway/apply", async () => {
    const { fetchMock, ops } = await signedIn(jsonResponse(200, { ...PLAN, op: "gateway-apply", tenant: null }));
    const out = await ops.applyGateway({ ctlKey: CTL_KEY, dryRun: true, idempotencyKey: "ui-gateway-0000000" });
    expect(out.kind).toBe("plan");
    const c = call(fetchMock, 1);
    expect(c.url).toMatch(/\/v1\/gateway\/apply$/);
    expect(c.body).toMatchObject({ ctl_api_key: CTL_KEY, args: {} });
  });

  it("putSettings PUTs the partial settings as args", async () => {
    const { fetchMock, ops } = await signedIn(jsonResponse(202, JOB, { Location: `/v1/jobs/${JOB_ID}` }));
    await ops.putSettings({
      args: { retention: { keep_last: { backup: 5 } } },
      ctlKey: CTL_KEY,
      dryRun: false,
      confirm: "settings",
      idempotencyKey: "ui-settings-000000",
    });
    const c = call(fetchMock, 1);
    expect(c.init.method).toBe("PUT");
    expect(c.url).toMatch(/\/v1\/settings$/);
    expect(c.body).toMatchObject({
      ctl_api_key: CTL_KEY,
      confirm: "settings",
      args: { retention: { keep_last: { backup: 5 } } },
    });
    assertNoStorage();
  });
});

describe("fetchSecrets", () => {
  const SECRETS = {
    job_id: JOB_ID,
    delivered_at: "2026-10-08T12:01:00Z",
    expires_at: "2026-10-08T12:15:00Z",
    secrets: [{ id: "k1", label: "bootstrap", role: "admin", value: "tenant-secret-value" }],
  };

  it("sends the key as X-API-Key and NO session header", async () => {
    const { fetchMock, ops } = await signedIn(jsonResponse(200, SECRETS));
    const out = await ops.fetchSecrets(JOB_ID, CTL_KEY);
    expect(out).toEqual(SECRETS);
    const c = call(fetchMock, 1);
    expect(c.url).toMatch(new RegExp(`/v1/jobs/${JOB_ID}/secrets$`));
    expect(c.init.method).toBe("GET");
    expect(c.headers["X-API-Key"]).toBe(CTL_KEY);
    expect(c.headers.Authorization).toBeUndefined();
    expect(c.init.body).toBeUndefined();
    expect(c.init.cache).toBe("no-store");
    expect(c.init.credentials).toBe("omit");
    assertNoStorage();
    expect(JSON.stringify([...sessionStore.map.values()])).not.toContain("tenant-secret-value");
  });

  it("a 401 (wrong key) does not sign the session out; a 410 surfaces", async () => {
    const { ops, session } = await signedIn(
      jsonResponse(401, { code: "auth_required", detail: "x", request_id: "3333333333333333" }),
      jsonResponse(410, { code: "not_found", detail: "already delivered", request_id: "4444444444444444" }),
    );
    await expect(ops.fetchSecrets(JOB_ID, CTL_KEY)).rejects.toMatchObject({ status: 401 });
    expect(session.getSession()?.sessionId).toBe(SESSION_ID);
    await expect(ops.fetchSecrets(JOB_ID, CTL_KEY)).rejects.toMatchObject({ status: 410, code: "not_found" });
    expect(session.getSession()?.sessionId).toBe(SESSION_ID);
  });

  it("refuses without a key, sending nothing", async () => {
    const { fetchMock, ops } = await signedIn();
    await expect(ops.fetchSecrets(JOB_ID, "")).rejects.toMatchObject({ status: 0 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("newIdempotencyKey", () => {
  it("is ui-<prefix>-<uuid>, matches the contract pattern, and is fresh each time", async () => {
    const { newIdempotencyKey, IDEMPOTENCY_KEY_PATTERN } = await import("./ops");
    const a = newIdempotencyKey("key-mint");
    const b = newIdempotencyKey("key-mint");
    expect(a).toMatch(/^ui-key-mint-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(a).toMatch(IDEMPOTENCY_KEY_PATTERN);
    expect(a).toMatch(/^[A-Za-z0-9._:-]{8,128}$/);
    expect(a).not.toBe(b);
  });

  it("sanitizes the prefix into the contract alphabet", async () => {
    const { newIdempotencyKey } = await import("./ops");
    expect(newIdempotencyKey("a b/c")).toMatch(/^ui-a_b_c-/);
    expect(newIdempotencyKey("x".repeat(500)).length).toBeLessThanOrEqual(128);
  });

  it("falls back to getRandomValues without randomUUID", async () => {
    const real = globalThis.crypto;
    vi.stubGlobal("crypto", { getRandomValues: (b: Uint8Array) => real.getRandomValues(b) });
    const { newIdempotencyKey } = await import("./ops");
    expect(newIdempotencyKey("start")).toMatch(
      /^ui-start-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });
});

describe("job polling", () => {
  it("polls every 2 s only while queued, running or awaiting_cutover", async () => {
    vi.stubGlobal("document", { hidden: false });
    const { jobRefetchInterval, JOB_POLL_MS } = await import("./queries");
    expect(JOB_POLL_MS).toBe(2000);
    for (const s of ["queued", "running", "awaiting_cutover"] as const) {
      expect(jobRefetchInterval(s)).toBe(2000);
    }
    for (const s of ["succeeded", "failed", "rolled_back", "interrupted", "cancelled"] as const) {
      expect(jobRefetchInterval(s)).toBe(false);
    }
    expect(jobRefetchInterval(undefined)).toBe(false);
  });

  it("stops while the tab is hidden", async () => {
    vi.stubGlobal("document", { hidden: true });
    const { jobRefetchInterval } = await import("./queries");
    expect(jobRefetchInterval("running")).toBe(false);
  });

  it("keys spell every filter member so {} and {tenant: undefined} are one entry", async () => {
    const { ctlKeys, ctlPaths } = await import("./queries");
    expect(ctlKeys.jobs({})).toEqual(ctlKeys.jobs({ tenant: undefined }));
    expect(ctlKeys.jobs({ tenant: "dev" })).not.toEqual(ctlKeys.jobs({}));
    expect(ctlPaths.jobs({ tenant: "dev", state: "running" })).toBe("/v1/jobs?tenant=dev&state=running");
    expect(ctlPaths.jobs()).toBe("/v1/jobs");
    expect(ctlPaths.audit({ limit: 50 })).toBe("/v1/audit?limit=50");
  });
});
