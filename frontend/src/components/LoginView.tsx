import { useState } from "react";
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
  OIDC_SEAM_NOTE,
  parseBvbrcToken,
  TOKEN_STORAGE_HINT,
  tokenExpiryNote,
  type AuthProviderOption,
  type Credential,
} from "../lib/auth";

// The sign-in PAGE (as opposed to LoginPanel, the inline control in the backend
// switcher). A centered card, a provider to choose, then the credential that
// provider needs.
//
// Why a provider CHOICE when only one federated provider works today: the choice
// is the honest shape of the problem — the deployment decides which identity
// provider is enabled, and a user arriving at this page needs to know which one
// applies to them. Google is listed and visibly unavailable rather than hidden,
// so the seam is discoverable instead of a surprise. See AUTH_PROVIDERS.
//
// This page never claims a sign-in. It hands the credential to the app and the
// header's UserMenu reports what the SERVER says, because with
// IDENTITY_PROVIDER=none a pasted token is ignored and every caller is the
// default tenant — which in production carries DEFAULT_ROLE=admin.

const INPUT =
  "w-full rounded border border-gray-300 bg-white px-3 py-2 text-sm focus:border-gray-900 focus:outline-none";

function baseLabel(base: string): string {
  return base || "the default backend";
}

function ProviderCard({
  provider,
  selected,
  onSelect,
}: {
  provider: AuthProviderOption;
  selected: boolean;
  onSelect: () => void;
}) {
  const disabled = !provider.available;
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onSelect}
      disabled={disabled}
      aria-pressed={selected}
      className={`w-full rounded-lg border p-3 text-left transition ${
        disabled
          ? "cursor-not-allowed border-gray-200 bg-gray-50 opacity-70"
          : selected
            ? "border-gray-900 bg-white shadow-sm"
            : "border-gray-300 bg-white hover:border-gray-400"
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-gray-900">{provider.label}</span>
        {disabled ? (
          <span className="rounded bg-gray-200 px-1.5 py-0.5 text-[11px] font-medium text-gray-600">
            not available
          </span>
        ) : selected ? (
          <span aria-hidden="true" className="text-gray-900">
            ✓
          </span>
        ) : null}
      </div>
      <p className="mt-1 text-xs text-gray-500">{provider.blurb}</p>
      {disabled && provider.unavailable ? (
        <p className="mt-2 text-xs text-gray-500">{provider.unavailable}</p>
      ) : null}
    </button>
  );
}

export function LoginView({
  setCredential,
  onDone,
}: {
  setCredential: (c: Credential) => void;
  onDone: () => void;
}) {
  const [choice, setChoice] = useState<AuthProviderOption["id"]>("bearer");
  const [tokenDraft, setTokenDraft] = useState("");
  const [keyDraft, setKeyDraft] = useState(getStoredApiKey);
  const [notice, setNotice] = useState<string | null>(null);

  const base = getApiBase();
  const savedToken = getStoredToken();
  const boundHere = !!savedToken && bearerAppliesToBase(getStoredTokenBase(), base);
  const parsed = parseBvbrcToken(tokenDraft || savedToken);
  const expiry = tokenExpiryNote(tokenDraft || savedToken, Date.now());
  const baseWarning = bearerBaseWarning(base);

  function useToken(raw: string) {
    const token = raw.trim();
    if (!token) return;
    // Confirm against the LIVE base — another tab may have switched backends
    // since this page rendered, and binding to a backend the user was never
    // shown is precisely the leak the binding exists to prevent.
    const live = getApiBase();
    if (live !== base) {
      setCredential(getStoredCredential());
      setNotice(
        `The selected backend changed to ${baseLabel(live)} while this page was open, ` +
          "so nothing was saved. Confirm again to send your token there.",
      );
      return;
    }
    setNotice(null);
    setCredential({ mode: "bearer", value: token });
    bindTokenToBase(live);
    setTokenDraft("");
    onDone();
  }

  return (
    <div className="mx-auto max-w-md py-10">
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-gray-900">Sign in</h2>
        <p className="mt-1 text-sm text-gray-500">
          Choose how you want to authenticate to{" "}
          <span className="font-medium text-gray-700">{baseLabel(base)}</span>.
        </p>

        <div className="mt-5 space-y-2">
          {AUTH_PROVIDERS.map((p) => (
            <ProviderCard
              key={p.id}
              provider={p}
              selected={choice === p.id}
              onSelect={() => setChoice(p.id)}
            />
          ))}
        </div>

        <div className="mt-6 border-t border-gray-100 pt-5">
          {choice === "bearer" ? (
            <div className="space-y-3">
              <label htmlFor="login-token" className="block text-sm font-medium text-gray-700">
                BV-BRC token
              </label>
              <input
                id="login-token"
                type="password"
                autoComplete="off"
                spellCheck={false}
                placeholder="un=you@patricbrc.org|tokenid=…|sig=…"
                value={tokenDraft}
                onChange={(e) => setTokenDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") useToken((e.target as HTMLInputElement).value);
                }}
                className={INPUT}
              />
              <p className="text-xs text-gray-500">
                Run <code>p3-login</code> and copy the contents of{" "}
                <code>~/.patric_token</code>. Paste the whole pipe-delimited string; a
                leading <code>Bearer </code> is optional.
              </p>
              {parsed ? (
                <p className="text-xs text-gray-600">
                  Token says: <span className="font-medium">{parsed.username}</span>
                  {parsed.signingSubject ? ` · signed by ${parsed.signingSubject}` : ""} —
                  read from the token for display only; the server is the verifier.
                </p>
              ) : null}
              {expiry ? <p className="text-xs text-amber-700">{expiry}</p> : null}
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => useToken(tokenDraft)}
                  disabled={!tokenDraft.trim()}
                  className="rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-40"
                >
                  Sign in
                </button>
                {savedToken && !boundHere ? (
                  <button
                    type="button"
                    onClick={() => useToken(savedToken)}
                    className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800"
                  >
                    Send my saved token to {baseLabel(base)}
                  </button>
                ) : null}
              </div>
              {savedToken && !boundHere ? (
                <p className="text-xs text-amber-800">
                  A saved token is bound to a different backend and is not being sent
                  here. A BV-BRC token has no audience, so it only goes to a backend you
                  confirmed.
                </p>
              ) : null}
              {baseWarning ? <p className="text-xs text-amber-700">{baseWarning}</p> : null}
              <p className="text-xs text-gray-500">{TOKEN_STORAGE_HINT}</p>
            </div>
          ) : choice === "apikey" ? (
            <div className="space-y-3">
              <label htmlFor="login-key" className="block text-sm font-medium text-gray-700">
                API key
              </label>
              <input
                id="login-key"
                type="password"
                autoComplete="off"
                spellCheck={false}
                placeholder="configured API key"
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
            <p className="text-sm text-gray-500">{OIDC_SEAM_NOTE}</p>
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
