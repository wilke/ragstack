import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, getTenants } from "../api/client";
import {
  bindTokenToBase,
  clearStoredToken,
  getApiBase,
  getStoredApiKey,
  getStoredCredential,
  getStoredToken,
  getStoredTokenBase,
} from "../api/config";
import {
  bearerAppliesToBase,
  bearerBaseWarning,
  identityView,
  OIDC_SEAM_NOTE,
  parseBvbrcToken,
  signInMessage,
  TOKEN_STORAGE_HINT,
  tokenExpiryNote,
  type Credential,
} from "../lib/auth";

// The login control. Inline panel, not a modal — the UI has no modals (see the
// header of ShareDialog.tsx).
//
// WHAT IT DOES: lets you paste a BV-BRC token or an API key, choose which one is
// active, and then shows WHO THE SERVER SAYS YOU ARE — because a 200 alone proves
// nothing. With `IDENTITY_PROVIDER=none` (the default) the Authorization header
// is not an authentication input at all: a pasted token is ignored, the request
// succeeds as the anonymous default tenant, and in production that tenant is an
// admin. `identityView` therefore only calls it a sign-in when GET
// /v1/stats/tenants comes back with a federated `issuer:subject` tenant.
//
// WHY /v1/stats/tenants: it is the de-facto whoami (tenant + role + auth_enabled)
// and it already exists. There is no /v1/me, and inventing one would mean a
// contract change plus both implementations.
//
// OIDC SEAM — NOT IMPLEMENTED, DELIBERATELY. Google sign-in is a different
// project, not a flag on this one: the server verifies a Google ID token, which a
// browser can only obtain via authorization-code + PKCE with a per-deployment
// client id and registered redirect URI, plus silent renew (~1h expiry). Nothing
// in the API mints, exchanges or refreshes tokens. When it is built, it plugs in
// HERE — as a second way to ACQUIRE the opaque string this panel already stores,
// sends and displays: add an "acquire" branch beside `saveToken` below, keep
// `{mode: "bearer", value}` as the credential shape, and nothing downstream
// changes. See lib/auth.ts.

const CRED_INPUT = "w-full rounded border border-gray-300 bg-white px-2 py-1 text-xs";
const BTN = "rounded border border-gray-300 bg-white px-2 py-1 text-xs hover:bg-gray-100";

function baseLabel(base: string): string {
  return base || "the default proxy";
}

export function LoginPanel({
  credential,
  setCredential,
  base,
}: {
  credential: Credential;
  setCredential: (c: Credential) => void;
  base: string;
}) {
  const [draft, setDraft] = useState("");
  const [keyDraft, setKeyDraft] = useState(getStoredApiKey);

  const savedToken = getStoredToken();
  const savedBase = getStoredTokenBase();
  const bearer = credential.mode === "bearer";
  // Saved, but bound to a different backend: config.getStoredCredential resolves
  // it to an empty value so it is NOT sent until the user re-confirms it here.
  const boundHere = !!savedToken && bearerAppliesToBase(savedBase, base);
  const unbound = bearer && !!savedToken && !boundHere;

  // Whoami. The key carries the full identity (mode + value) AND the base, so a
  // credential or backend change refetches instead of showing a stale identity.
  const whoami = useQuery({
    queryKey: ["whoami", credential.mode, credential.value, base],
    queryFn: () => getTenants(credential.value || undefined),
    retry: false,
  });

  const view = identityView(
    credential.mode,
    whoami.data
      ? {
          tenant: whoami.data.tenant,
          role: whoami.data.role,
          auth_enabled: whoami.data.auth_enabled,
        }
      : null,
  );
  const error = whoami.isError
    ? signInMessage(
        whoami.error instanceof ApiError ? whoami.error.status : null,
        whoami.error instanceof ApiError ? whoami.error.message : "",
      )
    : null;

  const parsed = parseBvbrcToken(draft || savedToken);
  const expiry = tokenExpiryNote(draft || savedToken, Date.now());
  const baseWarning = bearerBaseWarning(base);

  function saveToken(value: string) {
    const token = value.trim();
    if (!token) return;
    // Confirm against the LIVE base, not the `base` prop: another tab may have
    // switched backends since this panel rendered, and this component's prop
    // chain (BackendSwitcher's useState) is frozen at mount. Binding to the live
    // base while the label above still names the old one would send the token
    // to a backend the user was never shown — the exact leak the binding exists
    // to prevent. When they disagree, re-sync and make them confirm again.
    const live = getApiBase();
    if (live !== base) {
      setCredential(getStoredCredential());
      setDraft("");
      return;
    }
    setCredential({ mode: "bearer", value: token });
    // Explicit: setStoredCredential deliberately will not move an existing
    // token's binding, and re-pasting the same token to confirm it for this
    // backend must still bind.
    bindTokenToBase(live);
    setDraft("");
  }

  function signOut() {
    clearStoredToken();
    setCredential({ mode: "apikey", value: getStoredApiKey() });
  }

  return (
    <div className="mt-2 w-full rounded-lg border border-gray-200 bg-gray-50 p-4 text-xs">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="font-medium text-gray-500">Credential</span>
        <button
          type="button"
          onClick={() => setCredential({ mode: "apikey", value: getStoredApiKey() })}
          aria-pressed={!bearer}
          className={`rounded border px-2 py-1 ${
            !bearer ? "border-gray-900 bg-white font-medium text-gray-900" : "border-gray-300 text-gray-500"
          }`}
        >
          API key
        </button>
        <button
          type="button"
          // Only carry a token that is already bound to THIS backend: switching
          // mode must not silently rebind (and re-send) a token saved elsewhere.
          // Recomputed against the LIVE base rather than `boundHere`, which is
          // derived from a prop that another tab's base change leaves stale.
          onClick={() => {
            const bound = !!savedToken && bearerAppliesToBase(savedBase, getApiBase());
            setCredential({ mode: "bearer", value: bound ? savedToken : "" });
          }}
          aria-pressed={bearer}
          className={`rounded border px-2 py-1 ${
            bearer ? "border-gray-900 bg-white font-medium text-gray-900" : "border-gray-300 text-gray-500"
          }`}
        >
          BV-BRC token
        </button>
        <span className="ml-auto text-gray-400">→ {baseLabel(base)}</span>
      </div>

      {bearer ? (
        <div className="space-y-2">
          <label htmlFor="bvbrc-token" className="block font-medium text-gray-600">
            Paste a BV-BRC token
          </label>
          <input
            id="bvbrc-token"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="un=you@patricbrc.org|tokenid=…|sig=…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") saveToken((e.target as HTMLInputElement).value);
            }}
            className={CRED_INPUT}
          />
          <div className="flex flex-wrap gap-2">
            <button type="button" className={BTN} onClick={() => saveToken(draft)} disabled={!draft.trim()}>
              Use this token
            </button>
            {savedToken ? (
              <button type="button" className={BTN} onClick={signOut}>
                Sign out (forget token)
              </button>
            ) : null}
          </div>
          <p className="text-gray-500">
            Run <code>p3-login</code> and copy <code>~/.patric_token</code>. The whole
            pipe-delimited string is the credential; a leading <code>Bearer </code> is
            optional.
          </p>
          {parsed ? (
            <p className="text-gray-600">
              Token says: <span className="font-medium">{parsed.username}</span>
              {parsed.signingSubject ? ` · signed by ${parsed.signingSubject}` : ""} — read
              from the token for display only; the server is the verifier.
            </p>
          ) : null}
          {expiry ? <p className="text-amber-700">{expiry}</p> : null}
          {unbound ? (
            <p className="rounded border border-amber-300 bg-amber-50 p-2 text-amber-800">
              The saved token was bound to {baseLabel(savedBase)} and is NOT being sent to{" "}
              {baseLabel(base)}. A BV-BRC token has no audience, so it is only sent to a
              backend you confirmed.{" "}
              <button type="button" className={BTN} onClick={() => saveToken(savedToken)}>
                Send it to {baseLabel(base)}
              </button>
            </p>
          ) : null}
          {baseWarning ? <p className="text-amber-700">{baseWarning}</p> : null}
          <p className="text-gray-500">{TOKEN_STORAGE_HINT}</p>
          <p className="text-gray-400">{OIDC_SEAM_NOTE}</p>
        </div>
      ) : (
        <div className="space-y-2">
          <label htmlFor="admin-api-key" className="block font-medium text-gray-600">
            API key
          </label>
          <input
            id="admin-api-key"
            type="password"
            autoComplete="off"
            placeholder="X-API-Key (leave blank if the API is keyless in dev)"
            value={keyDraft}
            onChange={(e) => {
              setKeyDraft(e.target.value);
              setCredential({ mode: "apikey", value: e.target.value });
            }}
            className={CRED_INPUT}
          />
          <p className="text-gray-500">
            Sent as <code>X-API-Key</code>, and stored in this browser's localStorage —
            any script on this page can read it.
          </p>
        </div>
      )}

      <div className="mt-3 border-t border-gray-200 pt-2">
        <p className={view.signedIn ? "text-green-700" : "text-gray-600"}>
          {whoami.isPending ? "Checking with the server…" : view.label}
        </p>
        {view.warning ? <p className="text-amber-700">{view.warning}</p> : null}
        {error ? <p className="text-red-700">{error}</p> : null}
      </div>
    </div>
  );
}
