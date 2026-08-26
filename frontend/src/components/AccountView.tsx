import {
  clearStoredToken,
  getApiBase,
  getStoredApiKey,
  getStoredToken,
  getStoredTokenBase,
} from "../api/config";
import { useState } from "react";
import { getAccessibleVision, setAccessibleVision } from "../lib/vision";
import { BackendSwitcher } from "./BackendSwitcher";
import { HelpTip } from "./HelpTip";
import {
  accountIssuer,
  accountName,
  identityView,
  tokenExpiryNote,
  type Credential,
  type IdentityFailure,
  type IdentitySummary,
} from "../lib/auth";

// Account & preferences.
//
// HONESTY NOTE, because it shapes the whole page: there is no server-side user
// preference surface yet. The API has no /v1/users/me and no preferences
// resource — the only whoami is GET /v1/stats/tenants. So everything below is
// either identity the SERVER reported, or a client-side setting that genuinely
// lives in this browser. Nothing here pretends to persist to the account.
//
// The first real server-side preference will be the default collection (#276),
// which is deliberately scheduled after this work; the placeholder at the bottom
// names it rather than rendering a control that writes nowhere.

function Row({ label, value }: { label: React.ReactNode; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-gray-100 py-2 last:border-0">
      <dt className="text-xs font-medium text-gray-500">{label}</dt>
      <dd className="min-w-0 truncate text-sm text-gray-900">{value}</dd>
    </div>
  );
}

export function AccountView({
  credential,
  identity,
  checking,
  failure,
  onSignIn,
  onSignedOut,
  onCredentialChange,
  onBaseChange,
}: {
  credential: Credential;
  identity: IdentitySummary | null;
  /** The whoami answer has not arrived yet — no verdict to render. */
  checking: boolean;
  /** The whoami request failed, or null when it did not. */
  failure: IdentityFailure | null;
  onSignIn: () => void;
  onSignedOut: (c: Credential) => void;
  /** Re-resolve the app credential after a backend change. */
  onCredentialChange: (c: Credential) => void;
  /** Report a same-tab base change to App (no storage event fires locally). */
  onBaseChange: (base: string) => void;
}) {
  const view = identityView(credential, identity, checking, failure);
  // Mirrors the persisted vision mode so the checkbox re-renders on toggle;
  // lib/vision.ts owns storage + the <html> attribute.
  const [accessibleVision, setAccessibleVisionState] = useState(getAccessibleVision);
  const base = getApiBase();
  const savedToken = getStoredToken();
  const tokenBase = getStoredTokenBase();
  const expiry = tokenExpiryNote(savedToken, Date.now());

  // The wrong-backend escape hatch, mounted on EVERY state of this page.
  // Picking the wrong backend is a common reason a sign-in appears not to work,
  // and the states where that is most likely — the check hanging, the check
  // failing — are exactly the ones an early return used to drop it from.
  const backendCard = (
    <div className="mt-6 rounded-xl border border-gray-200 bg-white p-5 text-left">
      {/* No paragraph here: BackendSwitcher's own "deployment" tip already
          defines a deployment, re-pointing and the per-browser persistence. */}
      <h3 className="text-sm font-medium text-gray-700">API backend</h3>
      <BackendSwitcher setCredential={onCredentialChange} onBaseChange={onBaseChange} />
    </div>
  );

  // Clearing the token is the one action that must work in every state,
  // including the ones with no confirmed identity: the credential is in
  // localStorage and is still being sent on every request, so "the check keeps
  // failing" must be escapable from the UI.
  const signOut = (
    <button
      type="button"
      onClick={() => {
        clearStoredToken();
        onSignedOut({ mode: "apikey", value: getStoredApiKey() });
      }}
      className="mt-3 rounded border border-gray-300 bg-white px-3 py-2 text-sm text-gray-800 hover:bg-gray-100"
    >
      Sign out
    </button>
  );

  // Nothing definite, so no VERDICT and nothing to act on: no "not signed in"
  // and no Sign in button. Whoami is a network call — seconds on a deployment
  // whose store counts are slow — and rendering the signed-out screen in that
  // window is what made a completed sign-in look like a failed one and sent
  // people round the login loop again. Only the verdict block waits; the page
  // itself (and its backend picker) stays.
  if (view.state === "checking") {
    return (
      <div className="mx-auto max-w-md py-10 text-center">
        <h2 className="text-base font-semibold text-gray-900">Account &amp; preferences</h2>
        <p className="mt-2 text-sm text-gray-500" role="status">
          {view.label}
        </p>
        {backendCard}
      </div>
    );
  }

  // The check FAILED. Not a verdict either way, so this must not say "you are
  // not signed in" — "the backend is down" reported as "you are signed out"
  // sends the user to paste a token that will fail identically.
  if (view.state === "unconfirmed") {
    return (
      <div className="mx-auto max-w-md py-10 text-center">
        <h2 className="text-base font-semibold text-gray-900">Account &amp; preferences</h2>
        <p className="mt-2 text-sm text-gray-600">{view.label}</p>
        <p className="mx-auto mt-3 max-w-sm rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800">
          {view.warning}
        </p>
        {view.identity ? (
          <p className="mt-3 text-xs text-gray-500">
            The name above is what the last successful check reported. It is not
            being asserted now — the credential in this browser is still being sent
            on every request, and the server has stopped confirming it.
          </p>
        ) : null}
        <div className="mt-4 flex justify-center gap-2">
          <button
            type="button"
            onClick={onSignIn}
            className="rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
          >
            Sign in again
          </button>
        </div>
        {signOut}
        {backendCard}
      </div>
    );
  }

  if (view.state === "signed-out") {
    return (
      <div className="mx-auto max-w-md py-10 text-center">
        <h2 className="text-base font-semibold text-gray-900">Account &amp; preferences</h2>
        <p className="mt-2 text-sm text-gray-600">You are not signed in.</p>
        {view.warning ? (
          <p className="mx-auto mt-3 max-w-sm rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800">
            {view.warning}
          </p>
        ) : null}
        <button
          type="button"
          onClick={onSignIn}
          className="mt-4 rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
        >
          Sign in
        </button>
        {backendCard}
      </div>
    );
  }

  const tenant = identity?.tenant ?? "";
  const issuer = accountIssuer(tenant);

  return (
    <div className="mx-auto max-w-2xl space-y-6 py-6">
      <section className="rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-base font-semibold text-gray-900">Account</h2>
        <p className="mt-1 text-xs text-gray-500">
          As reported by the server for the credential this browser is sending — not
          read from the token.
        </p>
        {/* Signed in WITH a caveat is a real state (see identityView): a key the
            backend accepts because it configures no keys at all. The union used
            to spell signed-in as `warning: null`, which deleted this sentence. */}
        {view.warning ? (
          <p className="mt-3 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800">
            {view.warning}
          </p>
        ) : null}
        <dl className="mt-4">
          <Row label="Name" value={accountName(tenant)} />
          <Row label="Identity provider" value={issuer ?? "API key (not a person)"} />
          <Row label="Owner scope" value={tenant} />
          <Row
            label="Role"
            value={
              <span
                className={
                  identity?.role === "admin"
                    ? "rounded bg-gray-900 px-1.5 py-0.5 text-xs font-medium text-white"
                    : ""
                }
              >
                {identity?.role}
              </span>
            }
          />
          <Row
            label={
              <>
                Credential type{" "}
                <HelpTip icon side="right" term="credential type" />
              </>
            }
            value={credential.mode === "bearer" ? "Bearer token" : "API key"}
          />
          <Row label="Backend" value={base || "default (same origin)"} />
        </dl>
        {identity?.role === "admin" ? (
          <p className="mt-3 rounded border border-gray-300 bg-gray-50 p-2 text-xs text-gray-700">
            Admin is a deployment-wide superuser: read and write on every collection,
            all of <code>/v1/admin/*</code>, and the model registry.
          </p>
        ) : null}
      </section>

      {credential.mode === "bearer" ? (
        <section className="rounded-xl border border-gray-200 bg-white p-5">
          <h2 className="text-base font-semibold text-gray-900">Session</h2>
          <dl className="mt-4">
            <Row
              label={
                <>
                  Token bound to{" "}
                  <HelpTip icon side="right" term="token binding" />
                </>
              }
              value={tokenBase || "the default backend"}
            />
            <Row label="Expiry" value={expiry ?? "no expiry field in the token"} />
          </dl>
          <p className="mt-3 text-xs text-gray-500">
            A BV-BRC token has no audience claim and cannot be revoked before it
            expires, so it is only ever sent to the backend you confirmed it for.
            Signing out deletes it from this browser; it stays valid elsewhere until
            expiry.
          </p>
          {signOut}
        </section>
      ) : null}

      <section className="rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-base font-semibold text-gray-900">Preferences</h2>

        <div className="mt-4">
          <h3 className="mb-2 text-sm font-medium text-gray-700">API backend</h3>
          <BackendSwitcher setCredential={onCredentialChange} onBaseChange={onBaseChange} />
        </div>

        <div className="mt-5 border-t border-gray-100 pt-4">
          <h3 className="text-sm font-medium text-gray-700">
            Accessibility{" "}
            <HelpTip icon side="right" term="accessible vision mode" />
          </h3>
          <label className="mt-2 flex items-start gap-2.5">
            <input
              type="checkbox"
              checked={accessibleVision}
              onChange={(e) => {
                setAccessibleVisionState(e.target.checked);
                setAccessibleVision(e.target.checked);
              }}
              className="mt-0.5"
            />
            {/* No sub-label: the tip on the heading above is the one definition
                of what this mode swaps and where it is kept. */}
            <span className="text-sm text-gray-700">
              Color-vision friendly colors &amp; higher contrast
            </span>
          </label>
        </div>

        <div className="mt-5 border-t border-gray-100 pt-4">
          <p className="text-sm text-gray-600">
            There are no <em>account</em> preferences yet. The backend selection and your
            credential live in this browser only — nothing on this page is stored against
            your account, because the API has no user-preference resource.
          </p>
          <p className="mt-2 text-xs text-gray-500">
            The first one will be a{" "}
            <span className="font-medium">default collection</span> — the collection a
            query uses when you name none. It is deliberately not built yet.
          </p>
        </div>
      </section>
    </div>
  );
}
