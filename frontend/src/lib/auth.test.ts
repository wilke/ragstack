import { describe, expect, it } from "vitest";
import {
  accountIssuer,
  accountName,
  AUTH_PROVIDERS,
  bearerAppliesToBase,
  bearerBaseWarning,
  credentialHeaders,
  identityView,
  insecureContextWarning,
  isTokenExpired,
  laneCredential,
  OIDC_SEAM_NOTE,
  parseBvbrcToken,
  resolveCredential,
  sendableCredential,
  signInMessage,
  stripBearerPrefix,
  TOKEN_STORAGE_HINT,
  tokenExpiryNote,
  type Credential,
} from "./auth";

// A shape-accurate BV-BRC token: pipe-delimited fields, `sig=` last. The
// signature is nonsense (only the server verifies it) but the LAYOUT is what
// the parser reads.
function token(fields: Record<string, string>): string {
  const body = Object.entries(fields)
    .map(([k, v]) => `${k}=${v}`)
    .join("|");
  return `${body}|sig=deadbeef`;
}

// --- header selection -------------------------------------------------------
// Mirrors python/ragstack/api/security.py `_authenticate`: with an identity
// provider on, presenting both credentials is a 400 — and the key half of that
// check is `api_key is not None`, so an EMPTY X-API-Key counts as present and
// would 400 an otherwise-good bearer request.
describe("credentialHeaders", () => {
  it("sends an API key as X-API-Key and nothing else", () => {
    const h = credentialHeaders({ mode: "apikey", value: "k1" });
    expect(h["X-API-Key"]).toBe("k1");
    expect(h.Authorization).toBeUndefined();
  });

  it("sends a bearer credential as Authorization and nothing else", () => {
    const h = credentialHeaders({ mode: "bearer", value: "un=alice|sig=ab" });
    expect(h.Authorization).toBe("un=alice|sig=ab");
    expect(h["X-API-Key"]).toBeUndefined();
  });

  it("never emits both headers, whichever mode is active", () => {
    for (const mode of ["apikey", "bearer"] as const) {
      const keys = Object.keys(credentialHeaders({ mode, value: "v" }));
      expect(keys).toHaveLength(1);
    }
  });

  it("omits the header entirely for an empty credential (an empty X-API-Key 400s a bearer request)", () => {
    expect(credentialHeaders({ mode: "apikey", value: "" })).toEqual({});
    expect(credentialHeaders({ mode: "bearer", value: "" })).toEqual({});
    expect(credentialHeaders({ mode: "apikey", value: "   " })).toEqual({});
    expect(credentialHeaders(undefined)).toEqual({});
    expect(credentialHeaders(null)).toEqual({});
  });

  it("trims a pasted token so a trailing newline can't break the header", () => {
    expect(credentialHeaders({ mode: "bearer", value: " tok\n" }).Authorization).toBe("tok");
  });

  it("passes a raw BV-BRC token through without inventing a Bearer prefix", () => {
    // The server strips an optional prefix; the wire format carries none.
    const raw = token({ un: "alice@patricbrc.org", expiry: "1", tokenid: "t" });
    expect(credentialHeaders({ mode: "bearer", value: raw }).Authorization).toBe(raw);
  });
});

describe("resolveCredential", () => {
  it("gives a bare string the app's active mode", () => {
    expect(resolveCredential("v", "bearer")).toEqual({ mode: "bearer", value: "v" });
    expect(resolveCredential("v", "apikey")).toEqual({ mode: "apikey", value: "v" });
  });

  it("keeps an explicit credential's own mode", () => {
    expect(resolveCredential({ mode: "apikey", value: "v" }, "bearer")).toEqual({
      mode: "apikey",
      value: "v",
    });
  });

  it("treats a missing credential as empty, not as an empty header", () => {
    expect(credentialHeaders(resolveCredential(undefined, "apikey"))).toEqual({});
  });
});

describe("laneCredential", () => {
  it("keeps a Compare lane key an API key while the app is on a bearer token", () => {
    const cred = laneCredential("lane-key", "tok");
    expect(cred).toEqual({ mode: "apikey", value: "lane-key" });
    expect(credentialHeaders(cred as Credential)["X-API-Key"]).toBe("lane-key");
    expect(credentialHeaders(cred as Credential).Authorization).toBeUndefined();
  });

  it("passes the app credential through UNPINNED when the lane has none", () => {
    // A pinned {mode, value} would skip sendableCredential's storage check, so
    // the app's own credential has to stay the opaque string.
    expect(laneCredential("", "tok")).toBe("tok");
    expect(laneCredential("   ", "k")).toBe("k");
  });
});

// --- what may actually be sent ---------------------------------------------
// The value comes from React state, the mode and the token→base binding from
// localStorage at request time. These are the states where they disagree.
describe("sendableCredential", () => {
  const TOKEN = token({ un: "alice@patricbrc.org", tokenid: "t" });

  it("sends the credential when state and storage agree", () => {
    expect(sendableCredential("k", { mode: "apikey", value: "k" }, "")).toEqual({
      mode: "apikey",
      value: "k",
    });
    expect(
      sendableCredential(TOKEN, { mode: "bearer", value: TOKEN }, TOKEN),
    ).toEqual({ mode: "bearer", value: TOKEN });
  });

  it("drops a token storage no longer considers bound to this backend", () => {
    // config.getStoredCredential resolves to an empty value after a base
    // switch; the stale closure still holds the token. THIS is the leak.
    const cred = sendableCredential(TOKEN, { mode: "bearer", value: "" }, TOKEN);
    expect(cred.value).toBe("");
    expect(credentialHeaders(cred)).toEqual({});
  });

  it("never labels an API key as Authorization after another tab flips the mode", () => {
    const cred = sendableCredential(
      "SECRET-API-KEY",
      { mode: "bearer", value: TOKEN },
      TOKEN,
    );
    expect(credentialHeaders(cred)).toEqual({});
  });

  it("never labels a bearer token as X-API-Key after the reverse flip", () => {
    // The dangerous direction: a keyless backend treats any X-API-Key as
    // present and resolves the caller to the default tenant (admin in prod).
    const cred = sendableCredential(TOKEN, { mode: "apikey", value: "k" }, TOKEN);
    expect(credentialHeaders(cred)).toEqual({});
  });

  it("recognizes a token structurally, so a sign-out elsewhere can't relabel it", () => {
    // clearStoredToken() wipes the saved value, so equality has nothing to
    // compare against — the BV-BRC shape (`un=` before the `|sig=`) is what is
    // left to recognize, and it is the same rule parseBvbrcToken applies.
    const cred = sendableCredential(TOKEN, { mode: "apikey", value: "k" }, "");
    expect(credentialHeaders(cred)).toEqual({});
    // ...while an ordinary key is unaffected.
    expect(
      credentialHeaders(sendableCredential("plain-key", { mode: "apikey", value: "k" }, "")),
    ).toEqual({ "X-API-Key": "plain-key" });
  });

  it("keeps an explicitly pinned key on its own header while a token is active", () => {
    const pinned = { mode: "apikey", value: "lane-key" } as const;
    expect(
      sendableCredential(pinned, { mode: "bearer", value: TOKEN }, TOKEN),
    ).toStrictEqual({ mode: "apikey", value: "lane-key" });
  });

  it("drops a bearer token pasted into a pinned key box", () => {
    // A pin says which HEADER the value becomes; it is not an exemption from
    // the token check. A Compare lane's key box is a plain text input, so a
    // token pasted there would otherwise go out as X-API-Key — and against a
    // keyless backend that authenticates as the default tenant (production:
    // DEFAULT_ROLE=admin) instead of 401ing.
    expect(
      sendableCredential({ mode: "apikey", value: TOKEN }, { mode: "apikey", value: "" }, ""),
    ).toStrictEqual({ mode: "apikey", value: "" });
    // ...including when it is the saved token rather than a structurally
    // recognisable one.
    expect(
      sendableCredential(
        { mode: "apikey", value: "opaque-token" },
        { mode: "apikey", value: "" },
        "opaque-token",
      ),
    ).toStrictEqual({ mode: "apikey", value: "" });
    // A real lane key is untouched.
    expect(
      sendableCredential({ mode: "apikey", value: "lane-key" }, { mode: "apikey", value: "" }, TOKEN),
    ).toStrictEqual({ mode: "apikey", value: "lane-key" });
  });

  it("treats a missing or empty credential as no header", () => {
    expect(
      credentialHeaders(sendableCredential(undefined, { mode: "apikey", value: "" }, "")),
    ).toEqual({});
    expect(
      credentialHeaders(sendableCredential("  ", { mode: "apikey", value: "" }, "")),
    ).toEqual({});
  });
});

describe("stripBearerPrefix", () => {
  // Mirrors security.py `_bearer_credential`.
  it("strips the scheme case-insensitively and trims", () => {
    expect(stripBearerPrefix("Bearer abc")).toBe("abc");
    expect(stripBearerPrefix("  bearer   abc  ")).toBe("abc");
    expect(stripBearerPrefix("BEARER abc")).toBe("abc");
  });

  it("leaves a raw token (which has no scheme) alone", () => {
    expect(stripBearerPrefix("un=alice|sig=ab")).toBe("un=alice|sig=ab");
    expect(stripBearerPrefix("bearerish")).toBe("bearerish");
  });
});

// --- BV-BRC token reading (display only) ------------------------------------
// Mirrors python/ragstack/identity/bvbrc.py `_split` + `_parse_fields`.
describe("parseBvbrcToken", () => {
  it("reads the display fields out of a token", () => {
    const raw = token({
      un: "alice@patricbrc.org",
      tokenid: "abc-123",
      expiry: "1800000000",
      SigningSubject: "https://user.patricbrc.org/public_key",
    });
    const info = parseBvbrcToken(raw);
    expect(info?.username).toBe("alice@patricbrc.org");
    expect(info?.tokenId).toBe("abc-123");
    expect(info?.expiry).toBe(1800000000);
    expect(info?.signingSubject).toBe("https://user.patricbrc.org/public_key");
  });

  it("accepts a token pasted with a Bearer prefix", () => {
    const raw = `Bearer ${token({ un: "bob", expiry: "1" })}`;
    expect(parseBvbrcToken(raw)?.username).toBe("bob");
  });

  it("ignores fields appended AFTER the signature — they are outside the signed region", () => {
    const raw = `${token({ un: "alice", expiry: "1" })}|un=eve`;
    expect(parseBvbrcToken(raw)?.username).toBe("alice");
  });

  it("lets the FIRST occurrence of a key win, so a duplicate can't shadow it", () => {
    const raw = "un=alice|un=eve|expiry=1|sig=ab";
    expect(parseBvbrcToken(raw)?.username).toBe("alice");
  });

  it("returns null for anything that isn't a BV-BRC token", () => {
    expect(parseBvbrcToken("")).toBeNull();
    expect(parseBvbrcToken("plain-api-key")).toBeNull();
    expect(parseBvbrcToken("eyJhbGciOi.J.W.T")).toBeNull(); // a JWT is not this format
    expect(parseBvbrcToken("|sig=ab")).toBeNull(); // nothing before the signature
    expect(parseBvbrcToken("tokenid=x|sig=ab")).toBeNull(); // no un= subject
  });

  it("reports a non-numeric expiry as unknown rather than guessing", () => {
    expect(parseBvbrcToken("un=alice|expiry=soon|sig=ab")?.expiry).toBeNull();
    expect(parseBvbrcToken("un=alice|sig=ab")?.expiry).toBeNull();
  });
});

describe("token expiry", () => {
  const now = 1_800_000_000_000; // ms

  it("names an expired token and says how to get a new one", () => {
    const raw = token({ un: "alice", expiry: String(now / 1000 - 7200) });
    expect(isTokenExpired(raw, now)).toBe(true);
    const note = tokenExpiryNote(raw, now) ?? "";
    expect(note).toMatch(/expired/i);
    expect(note).toMatch(/p3-login/);
  });

  it("counts down a live token", () => {
    const raw = token({ un: "alice", expiry: String(now / 1000 + 4 * 3600) });
    expect(isTokenExpired(raw, now)).toBe(false);
    expect(tokenExpiryNote(raw, now)).toMatch(/expires in .*4 h/);
  });

  it("says nothing when there is no expiry to read, and never calls it expired", () => {
    expect(tokenExpiryNote("plain-api-key", now)).toBeNull();
    expect(tokenExpiryNote("un=alice|sig=ab", now)).toBeNull();
    expect(isTokenExpired("plain-api-key", now)).toBe(false); // unknown ≠ expired
  });
});

// --- who the server says we are ---------------------------------------------
// The trap: IDENTITY_PROVIDER defaults to `none`, and with it off the
// Authorization header is not an authentication input — the request still
// succeeds, as the default tenant, which runs as admin in production. Only a
// federated `issuer:subject` tenant proves the bearer path authenticated.
describe("identityView", () => {
  it("confirms a bearer login only on a federated issuer:subject tenant", () => {
    const v = identityView(
      "bearer",
      {
        tenant: "bvbrc:alice@patricbrc.org",
        role: "user",
        auth_enabled: false, // reports bool(api_keys) — irrelevant to a bearer login
      },
      false,
    );
    expect(v.state).toBe("signed-in");
    expect(v.label).toContain("bvbrc:alice@patricbrc.org");
    expect(v.state === "signed-in" && v.warning).toBeNull();
  });

  it("refuses to call a 200 a login when the backend ignored the token", () => {
    const v = identityView(
      "bearer",
      { tenant: "default", role: "admin", auth_enabled: true },
      false,
    );
    expect(v.state).toBe("signed-out");
    const warning = v.state === "signed-out" ? (v.warning ?? "") : "";
    expect(warning).toMatch(/identity provider/i);
    expect(warning).toMatch(/not signed in/i);
  });

  it("names the tenant an API key maps to", () => {
    const v = identityView("apikey", { tenant: "asm", role: "admin", auth_enabled: true }, false);
    expect(v.state).toBe("signed-in");
    expect(v.label).toContain("asm");
    expect(v.state === "signed-in" && v.warning).toBeNull();
  });

  it("warns that a keyless backend ignores the key", () => {
    const v = identityView(
      "apikey",
      { tenant: "default", role: "admin", auth_enabled: false },
      false,
    );
    expect(v.state).toBe("signed-out");
    expect((v.state === "signed-out" ? (v.warning ?? "") : "")).toMatch(/no API keys/i);
  });

  // The three-state contract. A boolean here spelled "still asking" and "the
  // server says nobody" the same way, and whoami takes SECONDS on a deployment
  // with slow store counts — so a freshly signed-in user was shown the
  // signed-out screen and sent back to the login form.
  it("reports an unanswered check as checking, never as signed out", () => {
    const v = identityView("bearer", null, true);
    expect(v.state).toBe("checking");
    expect(v.label).not.toMatch(/not signed in/i);
    // No `warning` to render, by type: the checking variant does not carry one,
    // so a caller cannot treat it as the signed-out case without narrowing.
    expect("warning" in v).toBe(false);
  });

  it("says 'not signed in' only once the check has resolved to nobody", () => {
    const v = identityView("bearer", null, false);
    expect(v.state).toBe("signed-out");
    expect(v.label).toMatch(/not signed in/i);
  });

  // `pending` decides the null case only: a background refetch must not blank
  // an identity the server already confirmed.
  it("keeps a known identity on screen while it is being refreshed", () => {
    const v = identityView(
      "bearer",
      { tenant: "bvbrc:alice@patricbrc.org", role: "user", auth_enabled: false },
      true,
    );
    expect(v.state).toBe("signed-in");
  });
});

// --- messages ----------------------------------------------------------------
describe("signInMessage", () => {
  it("explains the both-credentials 400 the server enforces", () => {
    expect(signInMessage(400, "")).toMatch(/not both|two credentials/i);
  });

  it("prefers the server's own sentence on a 400", () => {
    const body = JSON.stringify({
      detail: "present exactly one credential: X-API-Key or Authorization, not both",
    });
    expect(signInMessage(400, body)).toContain("present exactly one credential");
  });

  it("points an expired token at p3-login on a 401", () => {
    expect(signInMessage(401, "")).toMatch(/expired/i);
    expect(signInMessage(401, "")).toMatch(/p3-login/);
  });

  it("distinguishes 'could not verify' (503) from 'rejected' (401)", () => {
    expect(signInMessage(503, "")).toMatch(/unreachable|couldn't verify/i);
    expect(signInMessage(503, "")).not.toMatch(/rejected/i);
  });

  it("distinguishes an unreachable API from an HTTP status", () => {
    expect(signInMessage(null, "")).toContain("could not reach the API");
    expect(signInMessage(500, "")).toContain("error 500");
  });

  it("never leaks the raw response body", () => {
    const body = JSON.stringify({ detail: [{ msg: "bad" }], trace: "secrets" });
    for (const status of [400, 401, 403, 404, 503, 500, null]) {
      expect(signInMessage(status, body)).not.toContain("{");
      expect(signInMessage(status, body)).not.toContain("secrets");
    }
  });
});

// --- where a token may be sent ----------------------------------------------
// A BV-BRC token carries no audience claim and cannot be revoked before it
// expires, so the backend switcher must not carry it to another deployment.
describe("bearer/base binding", () => {
  it("sends a token only to the base it was saved for", () => {
    expect(bearerAppliesToBase("/be/asm", "/be/asm")).toBe(true);
    expect(bearerAppliesToBase("/be/asm", "/be/lucid")).toBe(false);
    expect(bearerAppliesToBase("/be/asm", "https://elsewhere.example")).toBe(false);
  });

  it("treats the default proxy ('') as a base like any other", () => {
    expect(bearerAppliesToBase("", "")).toBe(true);
    expect(bearerAppliesToBase("", "/be/asm")).toBe(false);
  });

  it("warns before a token leaves the same-origin proxy for a typed URL", () => {
    expect(bearerBaseWarning("https://someone.example")).toMatch(/cross-origin/i);
    expect(bearerBaseWarning("/be/asm")).toBeNull();
    expect(bearerBaseWarning("")).toBeNull();
  });
});

describe("UI copy", () => {
  it("is honest that the token is XSS-exposed in localStorage", () => {
    expect(TOKEN_STORAGE_HINT).toMatch(/localStorage/);
    expect(TOKEN_STORAGE_HINT).toMatch(/any script/i);
    expect(TOKEN_STORAGE_HINT).toMatch(/revoke/i);
  });

  it("does not promise an OIDC flow that isn't built", () => {
    expect(OIDC_SEAM_NOTE).toMatch(/isn't wired up|not available/i);
    expect(OIDC_SEAM_NOTE).toMatch(/BV-BRC/);
  });
});

describe("account identity display", () => {
  it("shows the subject, not the issuer plumbing", () => {
    expect(accountName("bvbrc:awilke@bvbrc")).toBe("awilke@bvbrc");
    expect(accountIssuer("bvbrc:awilke@bvbrc")).toBe("bvbrc");
  });

  it("splits on the FIRST colon only, so a subject may contain one", () => {
    // An OIDC `sub` is opaque and may legitimately contain a colon; splitting
    // on the last (or on every) colon would display a truncated name.
    expect(accountName("oidc:https://accounts.google.com/123")).toBe(
      "https://accounts.google.com/123",
    );
    expect(accountIssuer("oidc:https://accounts.google.com/123")).toBe("oidc");
  });

  it("treats an API-key tenant as having no issuer", () => {
    expect(accountName("demo-ops")).toBe("demo-ops");
    expect(accountIssuer("demo-ops")).toBeNull();
  });
});

describe("login page providers", () => {
  it("offers BV-BRC and an API key, and lists Google as unavailable", () => {
    const byId = Object.fromEntries(AUTH_PROVIDERS.map((p) => [p.id, p]));
    expect(byId.bvbrc.available).toBe(true);
    expect(byId.apikey.available).toBe(true);
    expect(byId.google.available).toBe(false);
  });

  it("offers BV-BRC a real username/password sign-in, not just a paste", () => {
    const bvbrc = AUTH_PROVIDERS.find((p) => p.id === "bvbrc");
    expect(bvbrc?.methods).toContain("password");
    // Paste stays as the fallback for anyone who already ran p3-login.
    expect(bvbrc?.methods).toContain("paste");
  });

  it("never claims a password sign-in for a provider that has none", () => {
    // Google does not accept passwords from third-party sites, by design; a
    // password form there would be a phishing pattern, not a feature.
    const google = AUTH_PROVIDERS.find((p) => p.id === "google");
    expect(google?.methods).not.toContain("password");
    for (const p of AUTH_PROVIDERS) {
      if (!p.available) expect(p.methods).toHaveLength(0);
    }
  });

  it("says WHY Google cannot be chosen rather than hiding it", () => {
    // A greyed-out provider with no reason reads as a bug; the seam is real and
    // the page should name it.
    const google = AUTH_PROVIDERS.find((p) => p.id === "google");
    expect(google?.unavailable).toBeTruthy();
    expect(google?.unavailable).toMatch(/PKCE|authorization code|not built/i);
  });

  it("never marks an unavailable provider without an explanation", () => {
    for (const p of AUTH_PROVIDERS) {
      if (!p.available) expect(p.unavailable, `${p.id} needs a reason`).toBeTruthy();
    }
  });
});

describe("password entry requires a secure page", () => {
  it("refuses to reassure on a plaintext page", () => {
    const warn = insecureContextWarning(false);
    expect(warn).toBeTruthy();
    // The reason must name the real risk: the form itself can be rewritten, so
    // "we post it over TLS" is not a defence.
    expect(warn).toMatch(/rewritten|captured/i);
    expect(warn).toMatch(/HTTPS/);
    // ...and it must offer the way that still works.
    expect(warn).toMatch(/p3-login|token/i);
  });

  it("says nothing on HTTPS or localhost", () => {
    expect(insecureContextWarning(true)).toBeNull();
  });
});
