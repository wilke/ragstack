// The two things BOTH bundles derive from the build's own `BASE_URL`, and
// nothing else.
//
// This file exists so the admin bundle (src/admin/**) never has to import
// api/config.ts. config.ts is the tenant UI's localStorage-backed credential
// store — a backend switcher, a stored API key, a stored bearer token. The
// admin bundle deliberately has none of those (its session lives in
// sessionStorage; see src/admin/auth/session.ts), and an import that pulls
// config.ts into the admin module graph would ship that storage code to a page
// whose whole security story is "no credential on disk".
//
// Nothing here touches storage. Keep it that way: a test asserts the admin
// bundle's ONLY localStorage access is the vision-mode preference
// (src/admin/bundle.test.ts), and it will fail if this module grows one.

/**
 * When a UI is served under a path prefix by the front proxy
 * (`/ragstack/<tenant>/ui/`), the sibling API is `/ragstack/<tenant>/api`.
 * Derive it from Vite's own base rather than hardcoding a tenant, so every
 * base-aware instance gets a correct target for free.
 *
 * The tenant UI needs it because the app calls `/v1/...` absolute — behind the
 * gateway that resolves to the gateway ROOT, which is a 404, not to the
 * tenant's API. The admin bundle needs exactly the same derivation: served at
 * `/ragstack/admin/ui/`, the control plane's sibling API is
 * `/ragstack/admin/api`.
 *
 * Returns null when served at "/" (plain dev), where the Vite proxy already
 * handles `/v1`.
 */
export function gatewayApiBase(): string | null {
  const base = import.meta.env.BASE_URL || "/";
  const m = base.match(/^(.*)\/ui\/?$/);
  // m[1] is legitimately EMPTY for a gateway that mounts a tenant at '/ui/'
  // (-> '/api'); the regex already excludes a bare '/', so test m, not m[1].
  return m ? `${m[1]}/api` : null;
}

/**
 * The per-deployment prefix every persisted key carries.
 *
 * Browser storage is scoped to the ORIGIN, but the front proxy serves every
 * tenant AND the admin mount from one origin (`/ragstack/<name>/ui/`).
 * Unprefixed keys therefore made all tenants share one stored base, one API key
 * and one bearer token: opening tenant B's UI would read tenant A's stored base
 * and silently address A's API — and hand A's backend the token confirmed for
 * it while the user believed they were on B. Scope the keys to the served path
 * so each UI has its own.
 *
 * Plain dev (BASE_URL="/") keeps the original key names, so nobody's local
 * settings are disturbed.
 *
 * It lives here rather than in config.ts because the admin session key is
 * scoped with it too (src/admin/auth/session.ts) and must not drag config.ts's
 * localStorage into the admin bundle to get it.
 */
export const KEY_SCOPE = (() => {
  const base = (import.meta.env.BASE_URL || "/").replace(/\/+$/, "");
  return base ? `${base}.` : "";
})();
