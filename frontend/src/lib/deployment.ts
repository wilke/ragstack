// Which deployment this UI is talking to, as displayable facts: its name, the
// UI's own URL, the API's absolute URL, and both versions.
//
// NAMING: "tenant" here means a whole DEPLOYMENT — the dev / lucid / asm stacks,
// each with its own ports, stores and registry. (The API's `tenant` field is a
// data-ownership scope inside one deployment; Ops calls that "Data ownership".)
//
// The gateway serves each deployment at /ragstack/<name>/{ui,api}, so the name
// is derivable from the path the UI itself was served under — no endpoint
// reports it. Served at "/" (plain dev) there is no name to show.

import { getApiBase } from "../api/config";

/** The deployment name from the served base path, or null when served at "/". */
export function deploymentName(base: string = import.meta.env.BASE_URL || "/"): string | null {
  // "/ragstack/<name>/ui/" → "<name>". Anything else (including a bare "/") has
  // no name we can honestly claim.
  const m = base.match(/^\/[^/]+\/([^/]+)\/ui\/?$/);
  return m ? m[1] : null;
}

/** Absolute URL of this UI (what you would paste to a colleague). */
export function uiUrl(): string {
  if (typeof window === "undefined") return "";
  return new URL(import.meta.env.BASE_URL || "/", window.location.origin).href;
}

/**
 * Absolute URL of the API this UI addresses. `getApiBase()` is a path prefix
 * ("/ragstack/dev/api", "/be/lucid") or "" for same-origin, so it is resolved
 * against the page origin; an absolute custom base is returned unchanged.
 */
export function apiUrlAbsolute(): string {
  if (typeof window === "undefined") return "";
  const base = getApiBase();
  if (/^https?:\/\//i.test(base)) return base;
  return new URL(base || "/", window.location.origin).href.replace(/\/$/, "");
}

/** FastAPI's interactive docs for that API — the useful thing to link to. */
export function apiDocsUrl(): string {
  const base = apiUrlAbsolute();
  return base ? `${base}/docs` : "";
}

/**
 * The UI build's version, injected from package.json at build time
 * (vite.config.ts `define`). Not a server fact — it describes this bundle.
 */
export const uiVersion: string = __APP_VERSION__;
