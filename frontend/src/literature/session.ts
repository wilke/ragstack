// The demo's credential slot.
//
// sessionStorage, NOT localStorage — deliberately, and for the same reason the
// control-plane bundle keeps its session there (src/admin/auth/session.ts): this
// is a live BV-BRC bearer token that opens both a ragstack tenant and the
// Copilot API, and a demo laptop on a conference stage should not leave one on
// disk after the tab closes.
//
// The key is scoped to the served path, like KEY_SCOPE in src/api/base.ts: the
// gateway serves every app on this host from ONE origin, so an unscoped key
// would be shared with the tenant explorer and the admin dashboard.

const KEY = `${(import.meta.env.BASE_URL || "/").replace(/\/+$/, "")}.bvbrcToken`;

export function getToken(): string {
  try {
    return sessionStorage.getItem(KEY) ?? "";
  } catch {
    return ""; // storage disabled (private mode) → in-memory only
  }
}

export function setToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(KEY, token);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

/**
 * The BV-BRC subject encoded in the token, for the `user_id` the Copilot API
 * wants alongside the credential.
 *
 * A BV-BRC token is a signed key=value string, not a JWT:
 *   un=alice@patricbrc.org|tokenid=<uuid>|expiry=<epoch>|…|sig=<hex>
 *
 * Read only from the SIGNED region — everything before the first `|sig=`.
 * Appending `|un=eve` to a valid token leaves the signature intact but adds a
 * field outside the signed bytes, and the server discards it
 * (python/ragstack/identity/bvbrc.py `_split`). Scanning the whole string here
 * would make this display disagree with the identity the API actually resolves.
 *
 * Parsed for DISPLAY and for the Copilot API's `user_id` only — never for an
 * access decision. Nothing on this side is trusted.
 */
export function subjectOf(token: string): string {
  const signed = token.split("|sig=", 1)[0];
  const m = /(?:^|\|)un=([^|]+)/.exec(signed);
  return m ? m[1] : "";
}
