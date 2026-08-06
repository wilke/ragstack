// Runtime API target + admin key, persisted in localStorage so a dev can point
// the UI at any backend (and carry an admin key) without env vars or restarting
// Vite. The client (client.ts) reads getApiBase() at call time and prefixes
// every request.
//
// The presets are Vite-proxy PATH PREFIXES ("/be/<name>"), not absolute URLs, so
// switching stays same-origin and reaches the backend THROUGH the dev proxy (see
// vite.config.ts). That's what makes it work over an SSH/port forward, where only
// the Vite port is reachable — a bare http://localhost:8020 in the browser would
// hit the viewer's own machine. "" = the default proxy (VITE_API_TARGET).
//
// A Custom entry takes an absolute URL and calls the backend DIRECTLY (relies on
// the API's permissive CORS). Convenient for local dev, but it does NOT traverse
// a port forward — prefer a preset (or point VITE_API_TARGET at your backend).

import { bearerAppliesToBase, type AuthMode, type Credential } from "../lib/auth";

export interface BackendPreset {
  id: string;
  label: string;
  url: string; // "/be/<name>" proxy prefix, or "" for the default proxy
}

/**
 * When this UI is served under a path prefix by the front proxy
 * (`/ragstack/<tenant>/ui/`), the sibling API is `/ragstack/<tenant>/api`.
 * Derive it from Vite's own base rather than hardcoding a tenant, so every
 * base-aware instance gets a correct preset for free.
 *
 * It has to be a preset at all because the app calls `/v1/...` absolute — behind
 * the gateway that resolves to the gateway ROOT, which is a 404, not to the
 * tenant's API. Returns null when served at "/" (plain dev), where the Vite
 * proxy already handles `/v1`.
 */
function gatewayApiBase(): string | null {
  const base = import.meta.env.BASE_URL || "/";
  const m = base.match(/^(.*)\/ui\/?$/);
  // m[1] is legitimately EMPTY for a gateway that mounts a tenant at '/ui/'
  // (-> '/api'); the regex already excludes a bare '/', so test m, not m[1].
  return m ? `${m[1]}/api` : null;
}

const GATEWAY_BASE = gatewayApiBase();

export const BACKEND_PRESETS: BackendPreset[] = [
  ...(GATEWAY_BASE
    ? [{ id: "gateway", label: `Gateway (${GATEWAY_BASE})`, url: GATEWAY_BASE }]
    : []),
  { id: "proxy", label: "Default (Vite proxy)", url: "" },
  { id: "unified", label: "Unified explorer · :8020", url: "/be/unified" },
  { id: "asm", label: "asm (prod) · :8000", url: "/be/asm" },
  { id: "lucid", label: "lucid (prod) · :8010", url: "/be/lucid" },
];

const BASE_STORAGE_KEY = "ragstack.apiBase";
const KEY_STORAGE_KEY = "ragstack.apiKey";
// Login (#…): which credential is active, the bearer token itself, and the
// backend base that token was saved for. Same dotted namespace, same read()/
// write() wrappers — writing "" removes the key.
const MODE_STORAGE_KEY = "ragstack.authMode";
const TOKEN_STORAGE_KEY = "ragstack.bearerToken";
const TOKEN_BASE_STORAGE_KEY = "ragstack.bearerBase";

function read(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return ""; // storage disabled (e.g. private mode) → in-memory defaults
  }
}

function write(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

export function getApiBase(): string {
  // Fall back to the gateway base when nothing is stored: served under
  // /ragstack/<tenant>/ui/ the app would otherwise call /v1/... absolute, which
  // resolves to the gateway ROOT and 404s until someone opens the switcher.
  return read(BASE_STORAGE_KEY) || GATEWAY_BASE || "";
}

export function setApiBase(url: string): void {
  write(BASE_STORAGE_KEY, url.replace(/\/$/, ""));
}

export function getStoredApiKey(): string {
  return read(KEY_STORAGE_KEY);
}

export function setStoredApiKey(key: string): void {
  write(KEY_STORAGE_KEY, key);
}

// --- Login: the stored credential has a KIND ------------------------------
//
// `getStoredApiKey`/`setStoredApiKey` above are unchanged and still mean "the
// X-API-Key". Everything below adds the second kind (a pasted bearer token) and
// the mode that says which one is active. api/client.ts reads only
// `getStoredAuthMode()` (at call time, like getApiBase) — the credential VALUE
// is still passed explicitly into every request function, never read from here.

/** Which credential the app is currently sending. Anything unrecognized is a key. */
export function getStoredAuthMode(): AuthMode {
  return read(MODE_STORAGE_KEY) === "bearer" ? "bearer" : "apikey";
}

export function setStoredAuthMode(mode: AuthMode): void {
  // "apikey" is the default, so store it as absence rather than a literal.
  write(MODE_STORAGE_KEY, mode === "bearer" ? "bearer" : "");
}

export function getStoredToken(): string {
  return read(TOKEN_STORAGE_KEY);
}

/** The API base this token was saved for; see `bearerAppliesToBase`. */
export function getStoredTokenBase(): string {
  return read(TOKEN_BASE_STORAGE_KEY);
}

/**
 * Persist a bearer token, BOUND to a backend base (defaulting to the selected
 * one). The binding is the mitigation for the switcher cross-sending an
 * audience-less token to another deployment: after a base change the token stays
 * on disk but stops being sent until the user re-confirms it for the new target.
 */
export function setStoredToken(token: string, base: string = getApiBase()): void {
  write(TOKEN_STORAGE_KEY, token);
  write(TOKEN_BASE_STORAGE_KEY, token ? base : "");
}

export function clearStoredToken(): void {
  setStoredToken("");
}

/**
 * The credential the app should actually send right now.
 *
 * In bearer mode a token that is bound to a DIFFERENT base resolves to an empty
 * value — the request goes out anonymous (and 401s) rather than leaking the
 * token to a backend the user never confirmed. The login panel reads
 * `getStoredToken()`/`getStoredTokenBase()` directly to explain that state.
 */
export function getStoredCredential(): Credential {
  if (getStoredAuthMode() === "bearer") {
    const token = getStoredToken();
    const usable = token && bearerAppliesToBase(getStoredTokenBase(), getApiBase());
    return { mode: "bearer", value: usable ? token : "" };
  }
  return { mode: "apikey", value: getStoredApiKey() };
}

/**
 * Write the active credential back. The single writer — see App.tsx.
 *
 * An EMPTY bearer value means "no usable token right now" (e.g. the saved one is
 * bound to another base), NOT "forget the token": it must not wipe storage, or
 * switching backends and back would silently sign the user out. Deleting a token
 * is `clearStoredToken()`, called explicitly by the sign-out control.
 */
export function setStoredCredential(cred: Credential): void {
  setStoredAuthMode(cred.mode);
  if (cred.mode === "bearer") {
    // Persist a NEW token, bound to the base selected right now. Never MOVE an
    // existing token's binding here: setStoredToken defaults its base to the
    // live getApiBase(), so re-persisting the same token would silently re-bind
    // it to whatever base storage currently names. A second tab holds its base
    // in React state from mount, so "switch to bearer mode" there would hand the
    // token to a backend that tab never displayed — defeating the binding that
    // is the whole mitigation. Re-binding is an explicit, user-confirmed act:
    // see bindTokenToBase.
    if (cred.value && cred.value !== getStoredToken()) setStoredToken(cred.value);
  } else setStoredApiKey(cred.value);
}

/**
 * Re-bind the already-saved token to `base` — the user-confirmed "yes, send my
 * token to this other backend too" action. Separate from setStoredCredential so
 * that merely persisting the active credential can never move the binding; the
 * caller must have just shown the user which base they are confirming.
 */
export function bindTokenToBase(base: string): void {
  const token = getStoredToken();
  if (token) setStoredToken(token, base);
}

// Resolve a request path against the selected backend. Relative ("") stays
// same-origin so the Vite proxy / prod host handles it unchanged.
export function apiUrl(path: string): string {
  const base = getApiBase();
  return base ? base + path : path;
}
