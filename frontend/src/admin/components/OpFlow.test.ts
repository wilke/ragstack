import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OpFlowEvent, OpFlowState } from "../lib/opflow";

// OpFlow's I/O, end to end through the real modules — session.ts installs the
// credential into http.ts, ops.ts builds the envelope, KeyPrompt's `spendKey`
// wipes the field, OpFlow's `driveWithKey` spends the key, lib/opflow.ts
// decides the next screen — with only `fetch` and the two storages faked.
//
// What is asserted is the per-request key rule as the operator meets it:
//
//   * the dry run AND the execute each carry `ctl_api_key` in the BODY, and
//     each carries the key typed for IT (two prompts, two keys, nothing kept);
//   * the execute carries the typed `confirm` and the same idempotency key;
//   * the key is in no header, no storage, and in NO state the flow passed
//     through and no event it dispatched — `spendKey` → `driveWithKey` → `run`
//     is the only path that ever sees it;
//   * the refusal paths re-ask: `plan_stale` re-runs the dry run on a fresh
//     key, a continuation's 428 opens the typed confirm and re-asks the key.

const SESSION_ID = "c".repeat(64);
const PLAN_KEY = "ctl-key-for-the-dry-run-0001-SECRET";
const RUN_KEY = "ctl-key-for-the-execute-0002-SECRET";
const KEYS = [PLAN_KEY, RUN_KEY];

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

const H = (c: string) => "sha256:" + c.repeat(64);

const PLAN = {
  plan_hash: H("1"),
  op: "stop",
  tenant: "dev",
  registry_generation: 7,
  schema_version: 1,
  doctor: {
    status: "green",
    hash: H("2"),
    generated_at: "2026-10-08T12:00:00Z",
    scope: { tenant: "dev", op: "stop" },
    findings: [],
  },
  requires_confirm: true,
  confirm_value: "dev",
  steps: [],
  warnings: [],
};

const JOB_ID = "01JBBBBBBBBBBBBBBBBBBBBBBB";
function jobBody(state: string, op = "stop") {
  return {
    id: JOB_ID,
    op,
    tenant: "dev",
    principal: "key:operator",
    auth_method: "session",
    sudo_user: null,
    state,
    plan_hash: PLAN.plan_hash,
    request_id: "0123456789abcdef",
    idempotency_key: "ui-stop-x0000000",
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
}

type Call = [string, RequestInit];

async function harness(...answers: Response[]) {
  const fetchMock = vi.fn().mockResolvedValueOnce(
    jsonResponse(201, {
      session_id: SESSION_ID,
      principal: "key:operator",
      role: "operator",
      expires_at: "2026-10-08T23:00:00Z",
      reads_only: true,
    }),
  );
  for (const a of answers) fetchMock.mockResolvedValueOnce(a);
  vi.stubGlobal("fetch", fetchMock);
  const session = await import("../auth/session");
  await session.signInWithApiKey("sign-in-key-00000000");
  vi.mocked(sessionStore.setItem).mockClear();
  vi.mocked(localStore.getItem).mockClear();

  const ops = await import("../api/ops");
  const flow = await import("./OpFlow");
  const { opFlowReducer, OPFLOW_IDLE } = await import("../lib/opflow");
  const { spendKey } = await import("./KeyPrompt");

  // The flow's whole observable life: every state it was in, every event.
  let state: OpFlowState = OPFLOW_IDLE;
  const states: OpFlowState[] = [state];
  const events: OpFlowEvent[] = [];
  const dispatch = (e: OpFlowEvent) => {
    events.push(e);
    state = opFlowReducer(state, e);
    states.push(state);
  };

  /**
   * Type `typed` into a key prompt and submit it, as KeyPrompt does: the field
   * is wiped first, then the key goes to OpFlow's spend.
   */
  async function typeKeyAndSubmit(
    typed: string,
    run: import("./OpFlow").OpRun,
    opts: { forceYellow?: boolean } = {},
  ) {
    let field = typed;
    let wipedBeforeSpend = false;
    await spendKey(
      field,
      () => {
        field = "";
      },
      (key) => {
        wipedBeforeSpend = field === "";
        return flow.driveWithKey(state, key, run, dispatch, opts);
      },
    );
    expect(field).toBe("");
    // When a key was spent at all, the field was already empty by then.
    expect(wipedBeforeSpend).toBe(typed.trim() !== "");
  }

  function body(i: number) {
    const [url, init] = fetchMock.mock.calls[i] as unknown as Call;
    return {
      url,
      headers: init.headers as Record<string, string>,
      body: JSON.parse(init.body as string) as Record<string, unknown>,
    };
  }

  function assertKeyNowhereButBodies() {
    const snapshot = JSON.stringify({ states, events });
    for (const k of KEYS) expect(snapshot).not.toContain(k);
    for (let i = 1; i < fetchMock.mock.calls.length; i++) {
      const c = body(i);
      for (const k of KEYS) {
        expect(JSON.stringify(c.headers)).not.toContain(k);
        expect(c.url).not.toContain(k);
      }
    }
    expect(sessionStore.setItem).not.toHaveBeenCalled();
    for (const m of ["getItem", "setItem", "removeItem", "key", "clear"] as const) {
      expect(localStore[m]).not.toHaveBeenCalled();
    }
    const stored = JSON.stringify([...sessionStore.map.values(), ...localStore.map.values()]);
    for (const k of KEYS) expect(stored).not.toContain(k);
  }

  return {
    fetchMock,
    ops,
    flow,
    dispatch,
    typeKeyAndSubmit,
    body,
    assertKeyNowhereButBodies,
    get state() {
      return state;
    },
    states,
  };
}

describe("OpFlow execute — the key and the confirm", () => {
  it("dry run and execute each send THEIR key in the body; the execute sends confirm; nothing keeps either key", async () => {
    const h = await harness(
      jsonResponse(200, PLAN),
      jsonResponse(202, jobBody("queued"), { Location: `/v1/jobs/${JOB_ID}` }),
    );
    const run = (req: import("./OpFlow").OpRunRequest) => h.ops.submitOp("dev", "stop", { ...req, args: {} });

    h.dispatch({ type: "plan", idempotencyKey: h.ops.newIdempotencyKey("stop") });
    expect(h.state.kind).toBe("planning");
    await h.typeKeyAndSubmit(PLAN_KEY, run);
    expect(h.state.kind).toBe("planned");

    h.dispatch({ type: "confirm" });
    expect(h.state).toMatchObject({ kind: "confirming", confirmValue: "dev" });
    await h.typeKeyAndSubmit(RUN_KEY, run);
    expect(h.state).toMatchObject({ kind: "running", jobId: JOB_ID });

    const dry = h.body(1);
    const exec = h.body(2);
    expect(dry.url).toMatch(/\/v1\/tenants\/dev\/ops\/stop$/);
    expect(dry.body).toMatchObject({ dry_run: true, ctl_api_key: PLAN_KEY });
    expect(dry.body.confirm).toBeUndefined();
    expect(exec.body).toMatchObject({ dry_run: false, ctl_api_key: RUN_KEY, confirm: "dev" });
    expect(exec.body.idempotency_key).toBe(dry.body.idempotency_key);
    // Each prompt's key went to its own request and to no other.
    expect(JSON.stringify(dry.body)).not.toContain(RUN_KEY);
    expect(JSON.stringify(exec.body)).not.toContain(PLAN_KEY);
    expect(dry.headers.Authorization).toBe(`Session ${SESSION_ID}`);
    expect(dry.headers["X-API-Key"]).toBeUndefined();
    expect(exec.headers["X-API-Key"]).toBeUndefined();

    h.assertKeyNowhereButBodies();
  });

  it("an empty key sends nothing and leaves the flow where it was", async () => {
    const h = await harness();
    const run = (req: import("./OpFlow").OpRunRequest) => h.ops.submitOp("dev", "stop", { ...req, args: {} });
    h.dispatch({ type: "plan", idempotencyKey: h.ops.newIdempotencyKey("stop") });
    await h.typeKeyAndSubmit("   ", run);
    expect(h.state.kind).toBe("planning");
    expect(h.fetchMock).toHaveBeenCalledTimes(1); // the sign-in only
  });

  it("a yellow plan proceeds only when the operator ticks it: force_with_doctor_diff is the plan's doctor hash", async () => {
    const yellow = { ...PLAN, requires_confirm: false, confirm_value: null, doctor: { ...PLAN.doctor, status: "yellow" } };
    const h = await harness(jsonResponse(200, yellow), jsonResponse(202, jobBody("running")));
    const run = (req: import("./OpFlow").OpRunRequest) => h.ops.submitOp("dev", "stop", { ...req, args: {} });
    h.dispatch({ type: "plan", idempotencyKey: h.ops.newIdempotencyKey("stop") });
    await h.typeKeyAndSubmit(PLAN_KEY, run);
    h.dispatch({ type: "confirm" });
    expect(h.state).toMatchObject({ kind: "confirming", confirmValue: null });
    await h.typeKeyAndSubmit(RUN_KEY, run, { forceYellow: true });
    const exec = h.body(2);
    expect(exec.body.force_with_doctor_diff).toBe(H("2"));
    expect(exec.body.confirm).toBeUndefined();
    h.assertKeyNowhereButBodies();
  });

  it("plan_stale on the execute re-runs the dry run, on a key typed again", async () => {
    const h = await harness(
      jsonResponse(200, PLAN),
      jsonResponse(409, { code: "plan_stale", detail: "moved", request_id: "1111111111111111" }),
      jsonResponse(200, { ...PLAN, plan_hash: H("3") }),
    );
    const run = (req: import("./OpFlow").OpRunRequest) => h.ops.submitOp("dev", "stop", { ...req, args: {} });
    h.dispatch({ type: "plan", idempotencyKey: h.ops.newIdempotencyKey("stop") });
    await h.typeKeyAndSubmit(PLAN_KEY, run);
    h.dispatch({ type: "confirm" });
    await h.typeKeyAndSubmit(RUN_KEY, run);
    expect(h.state).toMatchObject({ kind: "planning", stale: true });
    await h.typeKeyAndSubmit(PLAN_KEY, run);
    expect(h.state).toMatchObject({ kind: "planned" });
    expect(h.body(3).body).toMatchObject({ dry_run: true, ctl_api_key: PLAN_KEY });
    // One mutation, one idempotency key, across the re-plan.
    expect(h.body(3).body.idempotency_key).toBe(h.body(1).body.idempotency_key);
    h.assertKeyNowhereButBodies();
  });

  it("a cancel the daemon wants confirmed: 428 → typed confirm → the key again → confirm sent", async () => {
    const h = await harness(
      jsonResponse(428, {
        code: "confirm_required",
        detail: "cancelling rolls back",
        request_id: "2222222222222222",
        extra: { confirm_value: "dev" },
      }),
      jsonResponse(202, jobBody("running", "decommission")),
    );
    const run = (req: import("./OpFlow").OpRunRequest) => h.ops.jobContinuation(JOB_ID, "cancel", req);

    // Planless: a continuation has no dry run.
    h.dispatch({ type: "direct", idempotencyKey: h.ops.newIdempotencyKey("job-cancel") });
    expect(h.state).toMatchObject({ kind: "confirming", plan: null, confirmValue: null });
    await h.typeKeyAndSubmit(RUN_KEY, run);
    expect(h.state).toMatchObject({ kind: "confirming", confirmValue: "dev" });
    await h.typeKeyAndSubmit(PLAN_KEY, run);
    expect(h.state.kind).toBe("running");

    const first = h.body(1);
    const second = h.body(2);
    expect(first.url).toMatch(new RegExp(`/v1/jobs/${JOB_ID}/cancel$`));
    expect(first.body).toMatchObject({ dry_run: false, ctl_api_key: RUN_KEY });
    expect(first.body.confirm).toBeUndefined();
    expect(second.body).toMatchObject({ dry_run: false, ctl_api_key: PLAN_KEY, confirm: "dev" });
    expect(second.body.idempotency_key).toBe(first.body.idempotency_key);
    h.assertKeyNowhereButBodies();
  });

  it("requestFor never includes a key, for any state", async () => {
    const { flow } = await harness();
    const { opFlowReducer, OPFLOW_IDLE } = await import("../lib/opflow");
    const planning = opFlowReducer(OPFLOW_IDLE, { type: "plan", idempotencyKey: "ui-x-000000000" });
    expect(flow.requestFor(planning)).toEqual({ dryRun: true, idempotencyKey: "ui-x-000000000" });
    expect(flow.requestFor(OPFLOW_IDLE)).toBeNull();
    for (const s of [planning, OPFLOW_IDLE]) {
      const r = flow.requestFor(s);
      expect(r && "ctlKey" in r).toBeFalsy();
    }
  });
});

describe("jobDeliversSecrets", () => {
  it("is a succeeded create / key-mint / restore, unless result says none were minted", async () => {
    const { jobDeliversSecrets } = await import("./OpFlow");
    const j = (op: string, state: string, result: unknown = null) =>
      ({ ...jobBody(state, op), result }) as unknown as import("../api/types").Job;
    expect(jobDeliversSecrets(j("create", "succeeded"))).toBe(true);
    expect(jobDeliversSecrets(j("key-mint", "succeeded"))).toBe(true);
    expect(jobDeliversSecrets(j("restore", "succeeded"))).toBe(true);
    expect(jobDeliversSecrets(j("create", "succeeded", { secrets_available: false }))).toBe(false);
    expect(jobDeliversSecrets(j("create", "failed"))).toBe(false);
    expect(jobDeliversSecrets(j("restart", "succeeded"))).toBe(false);
  });
});
