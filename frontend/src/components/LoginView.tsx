import { useState } from "react";
import { BVBRC_EXCHANGE, exchangePassword, SignInError } from "../api/identity";
import {
  bindTokenToBase,
  getApiBase,
  getStoredApiKey,
  getStoredCredential,
  getStoredToken,
  getStoredTokenBase,
} from "../api/config";
import {
  AUTH_PROVIDERS,
  bearerAppliesToBase,
  bearerBaseWarning,
  insecureContextWarning,
  parseBvbrcToken,
  TOKEN_STORAGE_HINT,
  tokenExpiryNote,
  type AuthProviderOption,
  type Credential,
} from "../lib/auth";

// The sign-in PAGE. Pick a provider, give it what it asks for.
//
// WHERE THE PASSWORD GOES — the thing to know before changing anything here:
// the browser posts it STRAIGHT TO THE PROVIDER (api/identity.ts) and gets back
// the same signed token `p3-login` writes to ~/.patric_token. RAGStack never
// sees it. Do not add a "just proxy it through the API" convenience: that would
// put user passwords through a service that has no business holding them, and
// make our request logs credential-bearing. The API deliberately has no
// endpoint that accepts a password, and should never gain one.
//
// The token that comes back is stored and bound exactly like a pasted one, and
// is equally untrusted — the server verifies its signature offline either way.
// This page never claims a sign-in; the header reports what the server says.

const INPUT =
  "w-full rounded border border-gray-300 bg-white px-3 py-2 text-sm focus:border-gray-900 focus:outline-none";

function baseLabel(base: string): string {
  return base || "the default backend";
}

export function LoginView({
  setCredential,
  onDone,
}: {
  setCredential: (c: Credential) => void;
  onDone: () => void;
}) {
  const [providerId, setProviderId] = useState<AuthProviderOption["id"]>("bvbrc");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [tokenDraft, setTokenDraft] = useState("");
  const [keyDraft, setKeyDraft] = useState(getStoredApiKey);
  const [showPaste, setShowPaste] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const provider =
    AUTH_PROVIDERS.find((p) => p.id === providerId) ?? AUTH_PROVIDERS[0];
  const base = getApiBase();
  const savedToken = getStoredToken();
  const boundHere = !!savedToken && bearerAppliesToBase(getStoredTokenBase(), base);
  const parsed = parseBvbrcToken(tokenDraft);
  const expiry = tokenExpiryNote(tokenDraft || savedToken, Date.now());
  const baseWarning = bearerBaseWarning(base);
  // The browser's own verdict: HTTPS or localhost is secure, anything else is
  // not. A password form on a plaintext page can be rewritten in transit.
  const insecure = insecureContextWarning(
    typeof window !== "undefined" ? window.isSecureContext : true,
  );

  /** Store a token we just obtained (or the user pasted) and leave. */
  function acceptToken(raw: string): boolean {
    const token = raw.trim();
    if (!token) return false;
    // Confirm against the LIVE base: another tab may have switched backends
    // since this page rendered, and binding to a backend the user was never
    // shown is the leak the binding exists to prevent.
    const live = getApiBase();
    if (live !== base) {
      setCredential(getStoredCredential());
      setNotice(
        `The selected backend changed to ${baseLabel(live)} while this page was open, ` +
          "so nothing was saved. Confirm again to send your token there.",
      );
      return false;
    }
    setNotice(null);
    setCredential({ mode: "bearer", value: token });
    bindTokenToBase(live);
    return true;
  }

  async function signInWithPassword(e: React.FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setBusy(true);
    setError(null);
    try {
      const token = await exchangePassword(BVBRC_EXCHANGE, username.trim(), password);
      // Drop the password the instant it has been exchanged — it must not sit
      // in component state waiting for a re-render or a crash dump.
      setPassword("");
      if (acceptToken(token)) {
        setTokenDraft("");
        onDone();
      }
    } catch (err) {
      setError(
        err instanceof SignInError
          ? err.message
          : "Sign-in failed for an unexpected reason.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-md py-10">
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-gray-900">Sign in</h2>
        <p className="mt-1 text-sm text-gray-500">
          to <span className="font-medium text-gray-700">{baseLabel(base)}</span>
        </p>

        <div className="mt-5">
          <label htmlFor="login-provider" className="block text-sm font-medium text-gray-700">
            Identity provider
          </label>
          <select
            id="login-provider"
            value={providerId}
            onChange={(e) => {
              setProviderId(e.target.value as AuthProviderOption["id"]);
              setError(null);
              setPassword("");
            }}
            className="mt-1 w-full rounded border border-gray-300 bg-white px-3 py-2 text-sm"
          >
            {AUTH_PROVIDERS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
                {p.available ? "" : " — not available"}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-gray-500">{provider.blurb}</p>
        </div>

        <div className="mt-5 border-t border-gray-100 pt-5">
          {!provider.available ? (
            <p className="rounded border border-gray-200 bg-gray-50 p-3 text-xs text-gray-600">
              {provider.unavailable}
            </p>
          ) : provider.id === "apikey" ? (
            <div className="space-y-3">
              <label htmlFor="login-key" className="block text-sm font-medium text-gray-700">
                API key
              </label>
              <input
                id="login-key"
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={keyDraft}
                onChange={(e) => setKeyDraft(e.target.value)}
                className={INPUT}
              />
              <p className="text-xs text-gray-500">
                An API key identifies a configured tenant, not a person. Its role comes
                from the server's <code>API_KEY_ROLES</code>.
              </p>
              <button
                type="button"
                onClick={() => {
                  setCredential({ mode: "apikey", value: keyDraft.trim() });
                  onDone();
                }}
                className="rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
              >
                Use this key
              </button>
            </div>
          ) : (
            <>
              {insecure ? (
                <div className="mb-4 rounded border border-red-300 bg-red-50 p-3 text-xs text-red-800">
                  <p className="font-medium">Not a secure connection</p>
                  <p className="mt-1">{insecure}</p>
                </div>
              ) : null}
              <form onSubmit={signInWithPassword} className="space-y-3">
                <div>
                  <label
                    htmlFor="login-username"
                    className="block text-sm font-medium text-gray-700"
                  >
                    Username
                  </label>
                  <input
                    id="login-username"
                    type="text"
                    autoComplete="username"
                    spellCheck={false}
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    className={`${INPUT} mt-1`}
                  />
                </div>
                <div>
                  <label
                    htmlFor="login-password"
                    className="block text-sm font-medium text-gray-700"
                  >
                    Password
                  </label>
                  <input
                    id="login-password"
                    type="password"
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className={`${INPUT} mt-1`}
                  />
                </div>
                <button
                  type="submit"
                  disabled={busy || !username.trim() || !password}
                  className="w-full rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-40"
                >
                  {busy ? "Signing in…" : "Sign in"}
                </button>
              </form>

              {error ? (
                <p
                  role="alert"
                  className="mt-3 rounded border border-red-300 bg-red-50 p-2 text-xs text-red-800"
                >
                  {error}
                </p>
              ) : null}

              <p className="mt-3 text-xs text-gray-500">
                Your password goes directly from this browser to {provider.label} over
                HTTPS and is exchanged for a token. It is never sent to RAGStack, and
                RAGStack stores nothing but the token — the same one{" "}
                <code>p3-login</code> writes.
              </p>

              <div className="mt-4 border-t border-gray-100 pt-3">
                <button
                  type="button"
                  onClick={() => setShowPaste((s) => !s)}
                  aria-expanded={showPaste}
                  className="text-xs text-gray-600 underline hover:text-gray-900"
                >
                  {showPaste ? "Hide" : "Already have a token? Paste it instead"}
                </button>
                {showPaste ? (
                  <div className="mt-3 space-y-2">
                    <input
                      id="login-token"
                      type="password"
                      autoComplete="off"
                      spellCheck={false}
                      placeholder="un=you@patricbrc.org|tokenid=…|sig=…"
                      value={tokenDraft}
                      onChange={(e) => setTokenDraft(e.target.value)}
                      className={INPUT}
                    />
                    {parsed ? (
                      <p className="text-xs text-gray-600">
                        Token says: <span className="font-medium">{parsed.username}</span>{" "}
                        — for display only; the server is the verifier.
                      </p>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => {
                        if (acceptToken(tokenDraft)) {
                          setTokenDraft("");
                          onDone();
                        }
                      }}
                      disabled={!tokenDraft.trim()}
                      className="rounded border border-gray-300 bg-white px-3 py-2 text-sm text-gray-800 hover:bg-gray-100 disabled:opacity-40"
                    >
                      Use this token
                    </button>
                  </div>
                ) : null}
              </div>

              {savedToken && !boundHere ? (
                <div className="mt-4 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800">
                  A saved token is bound to a different backend and is not being sent
                  here. A BV-BRC token has no audience, so it only goes to a backend you
                  confirmed.{" "}
                  <button
                    type="button"
                    onClick={() => {
                      if (acceptToken(savedToken)) onDone();
                    }}
                    className="underline"
                  >
                    Send it to {baseLabel(base)}
                  </button>
                </div>
              ) : null}
              {expiry ? <p className="mt-2 text-xs text-amber-700">{expiry}</p> : null}
              {baseWarning ? (
                <p className="mt-2 text-xs text-amber-700">{baseWarning}</p>
              ) : null}
              <p className="mt-2 text-xs text-gray-500">{TOKEN_STORAGE_HINT}</p>
            </>
          )}

          {notice ? (
            <p className="mt-3 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800">
              {notice}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
