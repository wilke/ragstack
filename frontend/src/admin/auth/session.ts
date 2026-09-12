// The admin bundle's ONE credential: a read-only control-plane session.
//
// The rules come from the contract (contracts/ctl/openapi.yaml, info.description)
// and they are the reason this file exists instead of reusing src/api/config.ts:
//
//   * Sign-in presents a ctl API key (`X-API-Key`) OR a BV-BRC bearer token,
//     never both — a request carrying both is 400 `both_credentials`.
//   * That credential is used EXACTLY ONCE, to `POST /v1/session`, and is then
//     dropped. It is never written to any storage, never kept in a React state
//     that outlives the submit, never put in a URL.
//   * What IS kept is the opaque session id the daemon returns, in
//     `sessionStorage` — which dies with the tab. NEVER localStorage: this
//     origin is shared with every tenant UI by decision (the admin mount is
//     internet-reachable for test/dev), and localStorage survives the tab, the
//     reboot and the user walking away.
//
//     That rule is about CREDENTIALS, and only credentials. A viewer
//     PREFERENCE may use localStorage: the accessible-vision mode
//     (src/lib/vision.ts, toggled from the admin header) is one, and surviving
//     the tab is the point — someone who needs the higher-contrast palette
//     needs it on every visit, and the stored value ("accessible") names
//     nobody and authenticates nothing. Preference reads/writes are permitted;
//     credentials never. vision.ts is the ONLY localStorage in this bundle and
//     src/admin/bundle.test.ts fails on a second one.
//   * A session authenticates READS ONLY. Every mutation must re-present a ctl
//     key in its body; PR-B ships no mutation, so nothing here can produce one.
//   * Signing out revokes server-side (`DELETE /v1/session`) and then clears
//     locally, so a copied session id is dead and not merely forgotten.
//
// The BV-BRC password path is the tenant UI's, unchanged: the browser posts the
// password STRAIGHT TO THE PROVIDER (src/api/identity.ts) and RAGStack never
// sees it. What is different here is what happens to the token afterwards — the
// tenant UI stores it, this one spends it.

// `api/base.ts`, not `api/config.ts`: KEY_SCOPE is a BASE_URL derivation, and
// config.ts is the tenant UI's localStorage credential store. Importing it from
// there put that storage in this bundle's graph — the one thing the paragraph
// above says this bundle does not have.
import { KEY_SCOPE } from "../../api/base";
import { del, installCtlAuth, post } from "../api/http";
import type { CtlCredential } from "../api/http";
import type { CtlRole, CtlSessionResponse } from "../api/types";

/**
 * Scoped by the served path exactly as the tenant UI scopes its own keys: the
 * gateway serves every tenant AND this admin bundle from one origin, so an
 * unprefixed key would put the control-plane session in the same slot a
 * tenant's UI reads. sessionStorage is per-tab, not per-path.
 */
const STORAGE_KEY = `ragstack-ctl.${KEY_SCOPE}session`;

/** Exported for the test that asserts which storage this lands in. */
export const SESSION_STORAGE_KEY = STORAGE_KEY;

export interface CtlSession {
  sessionId: string;
  principal: string;
  role: CtlRole;
  expiresAt: string;
}

// ---------------------------------------------------------------------------
// Storage. Every access is wrapped: a browser with site data blocked throws on
// the accessor itself, and the right answer there is "not signed in", not a
// blank page.
// ---------------------------------------------------------------------------

function readStored(): CtlSession | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<CtlSession>;
    if (typeof parsed.sessionId !== "string" || !parsed.sessionId) return null;
    if (parsed.role !== "viewer" && parsed.role !== "operator") return null;
    return {
      sessionId: parsed.sessionId,
      principal: typeof parsed.principal === "string" ? parsed.principal : "",
      role: parsed.role,
      expiresAt: typeof parsed.expiresAt === "string" ? parsed.expiresAt : "",
    };
  } catch {
    return null;
  }
}

function writeStored(session: CtlSession | null): void {
  try {
    if (session) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage disabled — the session then lives only in the module cache */
  }
}

// A module-level cache, so `useSyncExternalStore` gets a STABLE snapshot: a
// fresh object parsed out of storage on every render would loop forever.
let current: CtlSession | null | undefined;

function snapshot(): CtlSession | null {
  if (current === undefined) current = readStored();
  return current;
}

const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

/** Subscribe to sign-in / sign-out / session-expired. */
export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The current session, or null. Stable by reference between changes. */
export function getSession(): CtlSession | null {
  return snapshot();
}

/** Server-side rendering (the string-render tests) has no storage: signed out. */
export function getServerSession(): CtlSession | null {
  return null;
}

/**
 * Aborts every in-flight request that presented the CURRENT session.
 *
 * Signing out must not leave the previous operator's reads running against the
 * daemon: they would land after the switch, into a React Query cache the next
 * sign-in inherits, and their 401s would arrive while someone else is signed
 * in. http.ts hands this signal to every credentialed fetch; a request that
 * presents its own headers (sign-in, sign-out) is deliberately not bound to it.
 */
let inflight: AbortController | null = null;

/**
 * The current session's abort signal, created on demand.
 *
 * On demand because a session RESTORED from sessionStorage (a reload) never
 * passes through `setSession`, and a restored session's reads need the same
 * leash as a freshly signed-in one's.
 */
function sessionSignal(): AbortSignal | undefined {
  if (!snapshot()) return undefined;
  if (!inflight) inflight = new AbortController();
  return inflight.signal;
}

function setSession(session: CtlSession | null): void {
  // Every session change ends the previous session's requests — including a
  // REPLACEMENT (sign out, sign in as someone else), not just a clear.
  if (inflight) inflight.abort(new DOMException("session ended", "AbortError"));
  inflight = session ? new AbortController() : null;
  current = session;
  writeStored(session);
  emit();
}

/** Forget the session locally. Does NOT revoke — see `signOut`. */
export function clearSession(): void {
  if (snapshot() === null) return;
  setSession(null);
}

// ---------------------------------------------------------------------------
// The seam into http.ts. Installed at import time so any module that reaches
// the network has the credential attached without asking for it.
// ---------------------------------------------------------------------------

installCtlAuth({
  credential(): CtlCredential {
    const s = snapshot();
    if (!s) return { headers: {}, sessionId: null };
    return {
      headers: { Authorization: `Session ${s.sessionId}` },
      // The id travels WITH the request so its 401 can be matched back to it.
      sessionId: s.sessionId,
      signal: sessionSignal(),
    };
  },
  /**
   * Drop the session the server rejected — and only that one.
   *
   * A read started under session A can resolve after the user has signed out
   * and signed back in as B: a hidden tab's queued poll, a slow
   * `/v1/tenants/*\/logs`, a request the network held. Clearing
   * unconditionally made A's 401 sign B out — the login screen appearing a
   * moment after a successful sign-in, with no failed request of B's own to
   * explain it, and B's fresh cache thrown away with it. The abort signal
   * above usually kills those requests first; this comparison is what holds
   * when it does not (a fetch already past the point of cancellation, a
   * caller that supplied no signal).
   */
  onUnauthorized(sessionId: string | null) {
    const s = snapshot();
    if (!s || s.sessionId !== sessionId) return;
    clearSession();
  },
});

// ---------------------------------------------------------------------------
// Sign-in
// ---------------------------------------------------------------------------

function adopt(res: CtlSessionResponse): CtlSession {
  const session: CtlSession = {
    sessionId: res.session_id,
    principal: res.principal,
    role: res.role,
    expiresAt: res.expires_at,
  };
  setSession(session);
  return session;
}

/**
 * Exchange a ctl API key for a session.
 *
 * The key is a parameter and nothing else: it is not returned, not stored, and
 * the caller's form state is cleared on success. `skipUnauthorizedHandler`
 * because a 401 here means "that key is wrong", not "the session expired" —
 * there is no session yet to clear.
 */
export async function signInWithApiKey(apiKey: string): Promise<CtlSession> {
  const res = await post<CtlSessionResponse>("/v1/session", {
    authHeaders: { "X-API-Key": apiKey },
    skipUnauthorizedHandler: true,
  });
  return adopt(res);
}

/**
 * Exchange a BV-BRC token for a session. Same one-shot discipline: the token
 * reaches the daemon once and is not kept by the browser. (The tenant UI DOES
 * keep its token — different audience, different origin policy; do not copy
 * that habit here.)
 */
export async function signInWithBearer(token: string): Promise<CtlSession> {
  const res = await post<CtlSessionResponse>("/v1/session", {
    authHeaders: { Authorization: `Bearer ${token}` },
    skipUnauthorizedHandler: true,
  });
  return adopt(res);
}

/**
 * Revoke server-side, then forget. The local clear happens whatever the network
 * did: a sign-out that leaves the id in storage because the daemon was
 * unreachable is the worst of both outcomes.
 */
export async function signOut(): Promise<void> {
  const s = snapshot();
  clearSession();
  if (!s) return;
  try {
    await del("/v1/session", {
      authHeaders: { Authorization: `Session ${s.sessionId}` },
      skipUnauthorizedHandler: true,
    });
  } catch {
    /* already gone, or unreachable — the id is out of this browser either way */
  }
}
