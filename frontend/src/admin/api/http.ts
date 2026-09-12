// The admin bundle's whole HTTP layer: one base, one credential header, one
// error type.
//
// It is deliberately NOT `src/api/client.ts`. That client resolves its target
// and its credential from localStorage at call time (a backend switcher, a
// stored API key, a stored bearer token) — exactly the three things the control
// plane must not have. This surface holds every tenant's admin key; a page that
// can be pointed at another host, or that keeps a key on disk, is a different
// threat model. So:
//
//   * the base is FIXED, derived from the build's own BASE_URL;
//   * the only credential is the read-only session id, and it lives in
//     sessionStorage (src/admin/auth/session.ts), never localStorage;
//   * `credentials: "omit"` — no cookie rides along in either direction;
//   * a 401 on any read drops the session and returns the app to the login
//     screen, because the session is the only thing that could have expired.
//
// MUTATIONS ARE NOT HERE. Every mutating call re-presents a ctl API key in its
// body (`ctl_api_key`) and the daemon answers 409 until PR-C lands the job
// engine; `post()` exists for the two viewer-allowed POSTs — creating a session
// and rendering a gateway diff.

// `api/base.ts`, never `api/config.ts`: base.ts is the BASE_URL derivations and
// nothing else, while config.ts is the tenant UI's localStorage credential
// store. Importing from config would put that storage in this bundle's graph.
import { gatewayApiBase } from "../../api/base";
import type { CtlErrorBody, CtlErrorCode } from "./types";

/**
 * Where the control-plane API is, for this build.
 *
 * Served under `/ragstack/admin/ui/` the sibling API is `/ragstack/admin/api`
 * — the same derivation the tenant UI makes for its own tenant, so the mount
 * point is a deployment decision and not a constant in the source. Served at
 * "/" (`npm run dev:admin`) it returns "", and `/v1/...` goes to the Vite proxy
 * (vite.admin.config.ts → VITE_CTL_TARGET). There is no third option and no
 * switcher: an admin page that could be aimed at an arbitrary host would hand
 * that host a control-plane session.
 */
export function ctlApiBase(): string {
  return gatewayApiBase() ?? "";
}

export function ctlUrl(path: string): string {
  return ctlApiBase() + path;
}

/**
 * A non-2xx answer from the control plane, in the contract's own shape
 * (`contracts/ctl/schemas/error.json`).
 *
 * `detail` is server PROSE and may name paths, units and hosts — the UI
 * renders `code` and `requestId`, never `detail` verbatim (ErrorBanner). It is
 * carried because an operator reading the browser console is a legitimate
 * audience; the screen is not.
 */
export class CtlError extends Error {
  readonly status: number;
  readonly code: CtlErrorCode | null;
  readonly detail: string;
  readonly requestId: string | null;
  readonly extra: Record<string, unknown> | null;

  constructor(init: {
    status: number;
    code: CtlErrorCode | null;
    detail: string;
    requestId: string | null;
    extra?: Record<string, unknown> | null;
  }) {
    super(`ctl ${init.status}${init.code ? ` ${init.code}` : ""}`);
    this.name = "CtlError";
    this.status = init.status;
    this.code = init.code;
    this.detail = init.detail;
    this.requestId = init.requestId;
    this.extra = init.extra ?? null;
  }
}

const ERROR_CODES: readonly string[] = [
  "auth_required",
  "forbidden",
  "both_credentials",
  "not_found",
  "validation",
  "locked",
  "plan_stale",
  "duplicate",
  "doctor_red",
  "confirm_required",
  "refused",
  "rate_limited",
  "internal",
];

function asCode(v: unknown): CtlErrorCode | null {
  return typeof v === "string" && ERROR_CODES.includes(v) ? (v as CtlErrorCode) : null;
}

// ---------------------------------------------------------------------------
// The credential seam.
//
// http.ts must not import session.ts (session.ts posts through http.ts to
// create and revoke a session — that is a cycle). Instead the session module
// installs itself here at import time. The default is "no credential", which is
// also the correct behaviour for a unit test that imports only this file.
// ---------------------------------------------------------------------------

/** What was attached to one request, captured AT REQUEST TIME. */
export interface CtlCredential {
  /** `Authorization: Session <id>`, or none when signed out. */
  headers: Record<string, string>;
  /** The session those headers came from — the identity a 401 is about. */
  sessionId: string | null;
  /** Aborts when that session ends (sign-out, expiry, clear). */
  signal?: AbortSignal;
}

export interface CtlAuth {
  /** The credential to attach right now, bound to the session it came from. */
  credential(): CtlCredential;
  /**
   * The server rejected the session `sessionId`.
   *
   * The id is not decoration: a read that went out under session A can land
   * AFTER the user signed out and back in as B (a slow /v1/logs, a background
   * poll a hidden tab finally flushes). Clearing unconditionally signed B out
   * on A's 401 — the login screen appearing seconds after a successful
   * sign-in, with no failed request of its own to explain it. The installed
   * handler no-ops unless the id still names the current session.
   */
  onUnauthorized(sessionId: string | null): void;
}

const NO_AUTH: CtlAuth = {
  credential: () => ({ headers: {}, sessionId: null }),
  onUnauthorized: () => {},
};

let auth: CtlAuth = NO_AUTH;

export function installCtlAuth(next: CtlAuth): void {
  auth = next;
}

/**
 * One signal that aborts when EITHER input does.
 *
 * `AbortSignal.any` would do this, but it is newer than the browsers this
 * gateway is reached from and newer than the Node the tests run on; the hand
 * version is six lines and has no floor.
 */
function anySignal(a?: AbortSignal, b?: AbortSignal): AbortSignal | undefined {
  if (!a) return b;
  if (!b) return a;
  if (a.aborted) return a;
  if (b.aborted) return b;
  const c = new AbortController();
  const stop = (e: Event) => c.abort((e.target as AbortSignal).reason);
  a.addEventListener("abort", stop, { once: true });
  b.addEventListener("abort", stop, { once: true });
  return c.signal;
}

/** The last `X-Request-Id` a successful read carried, for the footer. */
let lastRequestId: string | null = null;

export function lastCtlRequestId(): string | null {
  return lastRequestId;
}

interface RequestOptions {
  method?: "GET" | "POST" | "DELETE";
  /** Headers that replace the session credential (sign-in presents a key/token). */
  authHeaders?: Record<string, string>;
  body?: unknown;
  signal?: AbortSignal;
  /** Sign-in and sign-out are the session's own calls; a 401 there is not a stale session. */
  skipUnauthorizedHandler?: boolean;
}

async function request(path: string, opts: RequestOptions = {}): Promise<Response> {
  // Captured HERE, before the await: which session this request speaks for is
  // decided at send time, not at response time. `authHeaders` replaces the
  // session credential entirely (sign-in/sign-out present their own), so those
  // calls carry no session identity and no session-scoped abort.
  const attached: CtlCredential | null = opts.authHeaders ? null : auth.credential();

  const headers: Record<string, string> = {
    Accept: "application/json",
    // Nothing the control plane answers may sit in a cache: the fleet view is a
    // liveness claim and a stale one is a lie. `cache: "no-store"` covers the
    // browser's own store; the request header covers an intermediary that would
    // otherwise honour a `max-age` the daemon never set.
    "Cache-Control": "no-store",
    ...(opts.authHeaders ?? attached?.headers ?? {}),
  };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(ctlUrl(path), {
    method: opts.method ?? "GET",
    headers,
    // No ambient authority. The admin mount shares an origin with www.bv-brc.org
    // by decision; a cookie sent automatically would be a credential nobody in
    // this app chose to present.
    credentials: "omit",
    cache: "no-store",
    // A redirect from a control-plane endpoint is never a success path, and
    // following one would replay the session header at wherever it points.
    redirect: "error",
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    // The caller's signal (React Query's, or a view's) AND the session's: a
    // sign-out must not leave the previous operator's reads in flight against
    // the daemon, landing into a cache the next sign-in inherits.
    signal: anySignal(opts.signal, attached?.signal),
  });

  const requestId = res.headers.get("X-Request-Id");
  if (res.ok) {
    if (requestId) lastRequestId = requestId;
    return res;
  }

  let body: Partial<CtlErrorBody> = {};
  try {
    body = (await res.json()) as Partial<CtlErrorBody>;
  } catch {
    /* a proxy 502 is not JSON; the status alone is the message */
  }

  // 401 means the session is gone (expired, revoked, or the daemon restarted).
  // Clearing it here — in the one place every read passes through — is what
  // makes "any read 401s → back to the login screen" true of the whole app
  // instead of of whichever view remembered to check. It clears the session
  // THIS request presented, which may no longer be the current one.
  if (res.status === 401 && !opts.skipUnauthorizedHandler) {
    auth.onUnauthorized(attached?.sessionId ?? null);
  }

  throw new CtlError({
    status: res.status,
    code: asCode(body.code),
    detail: typeof body.detail === "string" ? body.detail : res.statusText,
    requestId: (typeof body.request_id === "string" ? body.request_id : null) ?? requestId,
    extra: (body.extra as Record<string, unknown> | undefined) ?? null,
  });
}

/** Read one endpoint. The session credential is attached automatically. */
export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await request(path, { signal });
  return (await res.json()) as T;
}

/**
 * POST a viewer-allowed endpoint (`/v1/session`, `/v1/gateway/render`).
 *
 * `authHeaders` overrides the session credential — sign-in presents the ctl key
 * or the BV-BRC token exactly once, and the response is the only thing kept.
 */
export async function post<T>(
  path: string,
  opts: {
    body?: unknown;
    authHeaders?: Record<string, string>;
    /**
     * The caller's own cancellation. A POST is combined with the session's
     * signal exactly as a GET is, so `/v1/gateway/render` — which can take
     * seconds — does not outlive the session that asked for it.
     */
    signal?: AbortSignal;
    skipUnauthorizedHandler?: boolean;
  } = {},
): Promise<T> {
  const res = await request(path, { method: "POST", ...opts });
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** DELETE `/v1/session` — the only DELETE a read-only bundle makes. */
export async function del(
  path: string,
  opts: {
    authHeaders?: Record<string, string>;
    signal?: AbortSignal;
    skipUnauthorizedHandler?: boolean;
  } = {},
): Promise<void> {
  await request(path, { method: "DELETE", ...opts });
}
