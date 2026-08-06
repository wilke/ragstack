// The credential vocabulary: what kinds of credential exist, which HTTP header
// each becomes, how to read a pasted BV-BRC token, and what to say when the
// server refuses one. Pure functions only — no React, no fetch, no storage — so
// api/client.ts, api/config.ts and the login UI all agree on one set of rules
// and every rule is unit-testable (see auth.test.ts).
//
// MIRRORS THE SERVER. python/ragstack/api/security.py `_authenticate`:
//   * with an identity provider enabled, presenting BOTH an X-API-Key and an
//     Authorization credential is a 400 — so the header builder here is
//     exclusive by construction (it returns one header or none, never two);
//   * the key side of that check is `api_key is not None`, so an X-API-Key sent
//     with an EMPTY value still counts as present and would 400 an otherwise
//     good bearer request. `credentialHeaders` therefore emits NOTHING for an
//     empty credential rather than an empty header;
//   * `_bearer_credential` strips an optional "Bearer " prefix, so a raw
//     BV-BRC token (whose wire format has no scheme) is sent as-is.
//
// OIDC SEAM (deliberately not implemented — issue follow-up). Everything below
// treats a bearer credential as an OPAQUE string plus a mode, so adding Google
// /OIDC later only adds a new way to ACQUIRE that string (authorization code +
// PKCE redirect, a per-deployment client id, silent renew before the ~1h ID
// token expires) — the header plumbing, storage and identity display it would
// reuse are already here. Nothing in the API mints, exchanges or refreshes
// tokens, so an OIDC flow is a browser-side project of its own, not a flag.
// BV-BRC is the flow that needs no backend work: the user pastes the token
// `p3-login` wrote to ~/.patric_token.

import { apiDetail } from "./chunkers";

/** Which header a credential travels in. */
export type AuthMode = "apikey" | "bearer";

/** A credential plus the kind of header it must be sent as. */
export interface Credential {
  mode: AuthMode;
  value: string;
}

/**
 * What every client function accepts as its credential argument.
 *
 * A bare string is the historical shape (the whole app threads one opaque
 * credential string) and takes the app's active mode; an explicit
 * {@link Credential} pins the mode for that one call — which is what lets a
 * Compare lane carry an API key while the app itself is signed in with a token.
 */
export type CredentialInput = string | Credential | undefined;

/** Normalize a credential argument against the app's currently active mode. */
export function resolveCredential(
  input: CredentialInput,
  fallbackMode: AuthMode,
): Credential {
  if (input == null) return { mode: fallbackMode, value: "" };
  if (typeof input === "string") return { mode: fallbackMode, value: input };
  return input;
}

/**
 * What a request may ACTUALLY carry — the last check before a credential
 * becomes a header, and the only one that sees storage and the caller's value
 * at the same instant.
 *
 * `resolveCredential` alone is not enough, because the two halves of the app
 * credential come from different places and can drift apart:
 *   * the VALUE is React state, captured when a component (or a react-query
 *     `queryFn` closure) was created;
 *   * the MODE and the token→base binding live in localStorage, read per
 *     request, and are rewritten by the backend switcher, by the login panel,
 *     and by ANOTHER TAB.
 *
 * Two concrete leaks that gap produced, both fixed by requiring the value to
 * still agree with storage:
 *   * switching backends calls `queryClient.invalidateQueries()`, which refires
 *     every registered observer with its OLD closed-over token before React
 *     re-renders with the unbound one — sending an audience-less BV-BRC token
 *     to a host the user never confirmed, which is exactly what
 *     `bearerAppliesToBase` exists to prevent. Same shape in a second tab,
 *     whose polling dashboards fire on a timer against the base tab B chose.
 *   * a mode flip in one tab relabels the other tab's credential: an API key
 *     sent as `Authorization`, or — the dangerous direction — a bearer token
 *     sent as `X-API-Key`, which against a keyless backend authenticates as the
 *     default tenant (production: `DEFAULT_ROLE=admin`) instead of 401ing.
 *
 * `stored` is the credential storage says is sendable to the CURRENTLY selected
 * base (config.getStoredCredential — empty for a token bound elsewhere), and
 * `savedToken` is the token on disk whatever it is bound to. An explicit
 * {@link Credential} from the caller is a deliberate per-request pin (a Compare
 * lane's own API key) and passes through untouched.
 */
export function sendableCredential(
  input: CredentialInput,
  stored: Credential,
  savedToken: string,
): Credential {
  if (input == null) return { mode: stored.mode, value: "" };
  if (typeof input !== "string") {
    // A pin says which HEADER this value becomes, not that the value is exempt
    // from the token check. A Compare lane's key box is a plain text input, so a
    // user can paste a BV-BRC token into it — and an apikey pin would then ship
    // that token as `X-API-Key` to whichever base is selected, bound or not.
    if (input.mode === "bearer") {
      // A bearer pin is subject to the SAME binding check as a bare string —
      // otherwise the one function documented as the last check before a header
      // has an unguarded hole in it. Nothing constructs a bearer pin today;
      // this is here so that when something does, it cannot skip the check.
      const pinned = (input.value ?? "").trim();
      const sendable = stored.mode === "bearer" && pinned === stored.value.trim();
      return { mode: "bearer", value: sendable ? pinned : "" };
    }
    const pinned = (input.value ?? "").trim();
    const pinnedIsToken =
      (savedToken.trim() !== "" && pinned === savedToken.trim()) ||
      parseBvbrcToken(pinned) !== null;
    return { mode: "apikey", value: pinnedIsToken ? "" : pinned };
  }
  const value = input.trim();
  if (!value) return { mode: stored.mode, value: "" };
  if (stored.mode === "bearer") {
    // Only the exact token storage still considers sendable HERE goes out. Any
    // other string — a stale token, an API key from a tab that flipped mode —
    // resolves to no credential, so the request 401s instead of leaking.
    return { mode: "bearer", value: value === stored.value.trim() ? value : "" };
  }
  // API-key mode. A bearer token must never be relabelled as a key, so a value
  // that IS the saved token — or that is structurally a BV-BRC token, which
  // covers the tab whose token was signed out from under it — is dropped rather
  // than sent under the wrong header. (An opaque OIDC token after a sign-out is
  // the residual gap: only the identity check can catch that one, and the
  // login panel's `identityView` does.)
  const isToken = value === savedToken.trim() || parseBvbrcToken(value) !== null;
  return { mode: "apikey", value: isToken ? "" : value };
}

/**
 * The auth header(s) for a credential: exactly one, or none.
 *
 * Never returns both keys — see the `_authenticate` note at the top of this
 * file — and never returns an empty-valued header. The value is trimmed because
 * a pasted token routinely carries a trailing newline, which is not a legal
 * header value (fetch would throw before the request left the browser).
 */
export function credentialHeaders(cred: Credential | null | undefined): Record<string, string> {
  const value = (cred?.value ?? "").trim();
  if (!value) return {};
  if (cred?.mode === "bearer") return { Authorization: value };
  return { "X-API-Key": value };
}

/**
 * The credential ONE Compare lane must send: its own key if it has one,
 * otherwise the app's.
 *
 * Compare is the only screen with two credential channels. The old expression
 * (`lane.apiKey || apiKey`) picked a VALUE without a kind, so once the app is
 * signed in with a token, a lane's API key would have been labelled
 * `Authorization` — an unauthenticated lane at best, and two credentials never.
 * Pinning {mode, value} keeps a lane an API-key comparison (which is what it is
 * for: tenant is derived from the key) while the app itself is on a bearer
 * token.
 *
 * A lane WITHOUT its own key falls through as the app's opaque credential
 * string, deliberately un-pinned: pinning it would declare a mode from whatever
 * storage said when the lane rendered, and skip the storage agreement check
 * {@link sendableCredential} runs at request time — the app credential must
 * stay subject to that.
 */
export function laneCredential(
  laneKey: string,
  appCredential: CredentialInput,
): CredentialInput {
  const lane = (laneKey ?? "").trim();
  return lane ? { mode: "apikey", value: lane } : appCredential;
}

/**
 * Drop an optional `Bearer ` scheme, mirroring security.py `_bearer_credential`
 * so the UI reads a pasted credential exactly the way the server will.
 */
export function stripBearerPrefix(raw: string): string {
  const value = (raw ?? "").trim();
  return value.slice(0, 7).toLowerCase() === "bearer " ? value.slice(7).trim() : value;
}

// --- BV-BRC token: what we may show the user -------------------------------

/** The signature-protected fields of a BV-BRC token, for DISPLAY only. */
export interface BvbrcTokenInfo {
  username: string;
  tokenId: string;
  signingSubject: string;
  expiry: number | null; // epoch seconds
}

const SIG_SEPARATOR = "|sig=";

/**
 * Read the display fields out of a pasted BV-BRC token, or null if it isn't one.
 *
 * PARSING IS FOR DISPLAY ONLY — the server is the verifier. Two rules are
 * copied from python/ragstack/identity/bvbrc.py `_split`/`_parse_fields` so the
 * UI can never show a username the server would not honour:
 *   * only the region BEFORE the first `|sig=` is read, because that is the
 *     only part the signature covers (appending `|un=eve` to a valid token must
 *     not change who the UI says you are);
 *   * the FIRST occurrence of a key wins, so a duplicated field cannot shadow
 *     the one the verifier read.
 */
export function parseBvbrcToken(raw: string): BvbrcTokenInfo | null {
  const value = stripBearerPrefix(raw ?? "");
  const sigAt = value.indexOf(SIG_SEPARATOR);
  if (sigAt <= 0) return null; // no signature (or nothing before it) → not a BV-BRC token
  const payload = value.slice(0, sigAt);
  const fields: Record<string, string> = {};
  for (const part of payload.split("|")) {
    const eq = part.indexOf("=");
    if (eq <= 0) continue;
    const key = part.slice(0, eq);
    if (!(key in fields)) fields[key] = part.slice(eq + 1);
  }
  const username = fields.un ?? "";
  if (!username) return null; // `un=` is the only handle the server derives a subject from
  const rawExpiry = fields.expiry;
  const expiry = rawExpiry != null && rawExpiry.trim() !== "" && Number.isFinite(Number(rawExpiry))
    ? Math.trunc(Number(rawExpiry))
    : null;
  return {
    username,
    tokenId: fields.tokenid ?? "",
    signingSubject: fields.SigningSubject ?? "",
    expiry,
  };
}

/** True when a pasted BV-BRC token is already past its `expiry=` field. */
export function isTokenExpired(raw: string, nowMs: number): boolean {
  const info = parseBvbrcToken(raw);
  if (!info || info.expiry == null) return false; // unknown ≠ expired; the server decides
  return info.expiry * 1000 <= nowMs;
}

function humanDuration(ms: number): string {
  const mins = Math.round(ms / 60000);
  if (mins < 60) return `${Math.max(mins, 1)} min`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} days`;
}

/**
 * A one-line expiry note for a pasted token, or null when there is nothing to
 * say (not a BV-BRC token, or no `expiry=` field).
 *
 * Worth showing because an expired token is indistinguishable from a broken
 * backend otherwise: every request just starts 401ing.
 */
export function tokenExpiryNote(raw: string, nowMs: number): string | null {
  const info = parseBvbrcToken(raw);
  if (!info || info.expiry == null) return null;
  const deltaMs = info.expiry * 1000 - nowMs;
  if (deltaMs <= 0)
    return `This token expired ${humanDuration(-deltaMs)} ago — run p3-login and paste a fresh one.`;
  return `This token expires in ${humanDuration(deltaMs)}.`;
}

// --- Who the server thinks we are ------------------------------------------

/** The slice of GET /v1/stats/tenants that answers "who am I". */
export interface IdentitySummary {
  tenant: string;
  role: string;
  auth_enabled: boolean;
}

export interface IdentityView {
  /** True only when the server confirmed a *verified federated identity*. */
  signedIn: boolean;
  label: string;
  /** Set when the 200 does not mean what it looks like. */
  warning: string | null;
}

/**
 * A federated (bearer-authenticated) tenant is spelled `issuer:subject` —
 * "bvbrc:alice@patricbrc.org". An API-key tenant is colon-FREE: security.py
 * rejects a colon-bearing `api_key_tenants` value whenever an identity provider
 * is enabled, precisely so the two namespaces can't collide.
 */
export function isFederatedTenant(tenant: string): boolean {
  return (tenant ?? "").includes(":");
}

/**
 * The part of a federated tenant a person recognizes as their name.
 *
 * A tenant is `issuer:subject`; the issuer is deployment plumbing, so the header
 * shows the subject alone ("awilke@bvbrc") and the account page shows both. Only
 * the FIRST colon splits — a subject may legitimately contain more.
 */
export function accountName(tenant: string): string {
  const t = (tenant ?? "").trim();
  if (!isFederatedTenant(t)) return t;
  return t.slice(t.indexOf(":") + 1);
}

/** The issuer half of a federated tenant, or null for an API-key tenant. */
export function accountIssuer(tenant: string): string | null {
  const t = (tenant ?? "").trim();
  if (!isFederatedTenant(t)) return null;
  return t.slice(0, t.indexOf(":"));
}

/** One way in, as offered on the login page. */
export interface AuthProviderOption {
  id: AuthMode | "google";
  label: string;
  blurb: string;
  available: boolean;
  /** Why it cannot be chosen — shown in place of the control. */
  unavailable?: string;
}

/**
 * The providers the login page offers.
 *
 * Google/OIDC is listed but NOT selectable, deliberately and visibly: the server
 * can verify an OIDC ID token, but a browser can only obtain one through an
 * authorization-code + PKCE redirect with a per-deployment client id and a
 * registered redirect URI, plus silent renew. None of that exists yet, and
 * nothing in the API mints or exchanges tokens. Listing it greyed-out with the
 * reason is honest; hiding it would make the seam invisible, and enabling it
 * would be a button that cannot work.
 */
export const AUTH_PROVIDERS: AuthProviderOption[] = [
  {
    id: "bearer",
    label: "BV-BRC",
    blurb: "Sign in with your BV-BRC account token.",
    available: true,
  },
  {
    id: "google",
    label: "Google",
    blurb: "Sign in with a Google account.",
    available: false,
    unavailable:
      "Not available yet. The server can verify a Google ID token, but the browser sign-in flow (authorization code + PKCE, a per-deployment client id, silent renew) is not built — so there is nothing to click.",
  },
  {
    id: "apikey",
    label: "API key",
    blurb: "For operators and scripts: paste a configured API key.",
    available: true,
  },
];

/**
 * Turn a /v1/stats/tenants answer into what the header should say.
 *
 * The trap this exists for: `IDENTITY_PROVIDER` defaults to `none`, and with it
 * off the Authorization header is not an authentication input at all — the
 * request succeeds as the anonymous default tenant, which in production carries
 * `DEFAULT_ROLE=admin`. A 200 therefore proves nothing about the token; only a
 * federated tenant string does. Note also that `auth_enabled` reports
 * `bool(settings.api_keys)` — it is about API KEYS, not about the identity
 * provider — so it is used only to explain a keyless backend, never to decide
 * whether a bearer login worked.
 */
export function identityView(mode: AuthMode, info: IdentitySummary | null): IdentityView {
  if (!info) return { signedIn: false, label: "Not signed in", warning: null };
  const federated = isFederatedTenant(info.tenant);
  if (mode === "bearer") {
    if (federated)
      return {
        signedIn: true,
        label: `Signed in as ${info.tenant} · role ${info.role}`,
        warning: null,
      };
    return {
      signedIn: false,
      label: `The server sees you as “${info.tenant}” · role ${info.role}`,
      warning:
        "This backend ignored the token — it has no identity provider enabled, so every caller is the default tenant. You are not signed in.",
    };
  }
  return {
    signedIn: federated || info.auth_enabled,
    label: federated
      ? `Signed in as ${info.tenant} · role ${info.role}`
      : `API key → tenant “${info.tenant}” · role ${info.role}`,
    warning: info.auth_enabled
      ? null
      : "This backend has no API keys configured, so the key is ignored and every caller is the default tenant.",
  };
}

// --- Messages ---------------------------------------------------------------

/**
 * What went wrong confirming a credential against GET /v1/stats/tenants, in a
 * sentence a user can act on. `apiDetail` unwraps FastAPI's `detail`; the raw
 * body is never returned.
 */
export function signInMessage(status: number | null, body: string): string {
  if (status == null) return "Could not check the credential — could not reach the API.";
  if (status === 400) {
    const detail = apiDetail(body);
    return detail
      ? `The API refused the request: ${detail}`
      : "The API refused a request carrying two credentials — send an API key or a token, not both.";
  }
  if (status === 401)
    return "That credential was rejected — the token may be expired, or this backend may not accept it. Run p3-login and paste a fresh token.";
  if (status === 403)
    return "That identity authenticated, but isn't allowed to read this backend's tenancy.";
  if (status === 404)
    return "This backend has no /v1/stats/tenants endpoint, so the sign-in can't be confirmed here.";
  if (status === 503)
    return "The identity provider is unreachable, so the server couldn't verify the token — try again shortly.";
  return `Could not confirm the identity (error ${status}).`;
}

// --- Where a token may be sent ---------------------------------------------

/**
 * Whether a token saved while `savedBase` was selected may be sent to
 * `currentBase`.
 *
 * The backend switcher changes the target independently of the credential, so
 * without this the same token follows you from asm to lucid to an arbitrary
 * `Custom…` URL. For an API key that is a harmless 401; a BV-BRC token carries
 * NO audience claim (it is equally valid at GoWe and the Workspace) and cannot
 * be revoked before it expires, so sending it to the wrong host is handing over
 * the user's whole BV-BRC session. A saved token is therefore bound to the base
 * it was saved for and must be re-confirmed after a switch.
 */
export function bearerAppliesToBase(savedBase: string, currentBase: string): boolean {
  return (savedBase ?? "") === (currentBase ?? "");
}

/** True for a `Custom…` absolute URL, which leaves the same-origin dev proxy. */
export function isAbsoluteBase(base: string): boolean {
  // The scheme is OPTIONAL: "//evil.example" is protocol-relative, and
  // `new URL("//evil.example/v1/x", "https://good.example/ui/")` resolves to
  // https://evil.example/v1/x — cross-origin, but a `^https?://` test calls it
  // relative and suppresses the warning shown before a token is bound.
  return /^(https?:)?\/\//i.test((base ?? "").trim());
}

/** Extra warning shown before binding a token to a base, or null. */
export function bearerBaseWarning(base: string): string | null {
  if (!isAbsoluteBase(base)) return null;
  return "This backend is an absolute URL called cross-origin. Your token will be sent to that host verbatim — only continue if you trust it.";
}

/** Shown where a transient API-key box used to be, while a token is active. */
export const SIGNED_IN_HINT = "Signed in with a bearer token — manage credentials in the header.";

/** Storage honesty, shown next to the token box. Do not soften this. */
export const TOKEN_STORAGE_HINT =
  "Stored in this browser's localStorage — exactly as XSS-exposed as the API key: any script on this page can read it. A BV-BRC token has no audience and can't be revoked before it expires, so it is your whole BV-BRC session. Sign out when you're done on a shared machine.";

/** The OIDC seam, in the UI: honest about what is not built. */
export const OIDC_SEAM_NOTE =
  "Google (OIDC) sign-in isn't wired up yet — it needs a redirect + PKCE flow and a per-deployment client id. Paste a BV-BRC token for now (p3-login writes one to ~/.patric_token).";
