// Exchanging a username + password for a provider token.
//
// THE ONE ARCHITECTURAL POINT: this call goes from the BROWSER STRAIGHT TO THE
// IDENTITY PROVIDER. It does not touch the RAGStack API, and RAGStack must never
// grow an endpoint that accepts a password — not even a pass-through one.
//
// That is a deliberate choice, and it is available because BV-BRC's endpoint
// permits it: it echoes the caller's Origin in `access-control-allow-origin` and
// its preflight allows POST with `content-type` (verified against
// user.patricbrc.org). So the password goes to the only party that already knows
// it, over TLS, and every other system in the chain — our API, our logs, our
// database, our proxy — is structurally incapable of seeing it. A server-side
// proxy would work too, and would be strictly worse: it would put user passwords
// through a service that has no business holding them and would make our request
// logs a credential-bearing artifact.
//
// What comes back is the same signed token `p3-login` writes to ~/.patric_token.
// The API verifies its signature offline against a pinned key allowlist (see
// python/ragstack/identity/bvbrc.py), so obtaining the token this way and pasting
// it by hand are indistinguishable to the server. Nothing here is trusted: the
// token is a claim the server checks, not a session we grant.
//
// The password itself is never stored, never logged, never put in a URL, and is
// dropped from component state as soon as the exchange returns.

/** Where a provider's password exchange lives. */
export interface PasswordExchange {
  url: string;
  /** Field names the provider expects in the form body. */
  userField: string;
  passField: string;
}

/**
 * BV-BRC's authenticate endpoint — the same one `p3-login` uses.
 *
 * Pinned in config, never derived from anything a caller supplies: this URL
 * receives a password, so it is exactly the value an attacker would want to
 * influence. Overridable at BUILD time for alpha/beta deployments.
 */
export const BVBRC_EXCHANGE: PasswordExchange = {
  url: import.meta.env.VITE_BVBRC_AUTH_URL || "https://user.patricbrc.org/authenticate",
  userField: "username",
  passField: "password",
};

export class SignInError extends Error {
  readonly status: number | null;
  constructor(message: string, status: number | null) {
    super(message);
    this.name = "SignInError";
    this.status = status;
  }
}

/**
 * Exchange credentials for a provider token. Resolves to the raw token string.
 *
 * Form-encoded on purpose: `application/x-www-form-urlencoded` is a CORS-simple
 * content type, so the common case needs no preflight round-trip.
 */
export async function exchangePassword(
  exchange: PasswordExchange,
  username: string,
  password: string,
  signal?: AbortSignal,
): Promise<string> {
  const body = new URLSearchParams();
  body.set(exchange.userField, username);
  body.set(exchange.passField, password);

  let res: Response;
  try {
    res = await fetch(exchange.url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
      // No cookies either direction: this is a bare credential exchange, and a
      // provider session cookie riding along would be an ambient authority we
      // neither need nor want.
      credentials: "omit",
      signal,
    });
  } catch {
    // A network-layer failure here is indistinguishable from a CORS rejection by
    // design — the browser withholds the detail. Say what the user can act on.
    throw new SignInError(
      "Could not reach the identity provider. Check your connection; if this " +
        "persists, sign in with a token from p3-login instead.",
      null,
    );
  }

  const text = (await res.text()).trim();

  if (!res.ok) {
    // Providers vary: BV-BRC answers 401 with {"message": "..."} on bad
    // credentials. Prefer its wording, but never echo a whole response body
    // into the UI — it can carry markup or, on a misrouted request, the request
    // itself.
    let detail = "";
    try {
      const parsed = JSON.parse(text) as { message?: unknown };
      if (typeof parsed.message === "string") detail = parsed.message;
    } catch {
      /* not JSON — fall through to the generic message */
    }
    if (res.status === 401 || res.status === 403) {
      throw new SignInError(detail || "Incorrect username or password.", res.status);
    }
    throw new SignInError(
      detail || `The identity provider refused the sign-in (HTTP ${res.status}).`,
      res.status,
    );
  }

  // Success shape is the raw token; tolerate a JSON envelope in case a provider
  // wraps it.
  let token = text;
  try {
    const parsed = JSON.parse(text) as Record<string, unknown>;
    for (const key of ["token", "access_token", "auth_token"]) {
      if (typeof parsed[key] === "string") {
        token = parsed[key] as string;
        break;
      }
    }
  } catch {
    /* plain string body — the normal case */
  }

  token = token.trim();
  if (!token) {
    throw new SignInError(
      "The identity provider accepted the credentials but returned no token.",
      res.status,
    );
  }
  return token;
}
