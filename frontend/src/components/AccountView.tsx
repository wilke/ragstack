import { clearStoredToken, getApiBase, getStoredToken, getStoredTokenBase } from "../api/config";
import {
  accountIssuer,
  accountName,
  identityView,
  tokenExpiryNote,
  type Credential,
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

function Row({ label, value }: { label: string; value: React.ReactNode }) {
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
  onSignIn,
  onSignedOut,
}: {
  credential: Credential;
  identity: IdentitySummary | null;
  onSignIn: () => void;
  onSignedOut: (c: Credential) => void;
}) {
  const view = identityView(credential.mode, identity);
  const base = getApiBase();
  const savedToken = getStoredToken();
  const tokenBase = getStoredTokenBase();
  const expiry = tokenExpiryNote(savedToken, Date.now());

  if (!view.signedIn) {
    return (
      <div className="mx-auto max-w-md py-10 text-center">
        <p className="text-sm text-gray-600">You are not signed in.</p>
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
        <dl className="mt-4">
          <Row label="Name" value={accountName(tenant)} />
          <Row label="Identity provider" value={issuer ?? "API key (not a person)"} />
          <Row label="Tenant" value={tenant} />
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
          <Row label="Credential type" value={credential.mode === "bearer" ? "Bearer token" : "API key"} />
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
            <Row label="Token bound to" value={tokenBase || "the default backend"} />
            <Row label="Expiry" value={expiry ?? "no expiry field in the token"} />
          </dl>
          <p className="mt-3 text-xs text-gray-500">
            A BV-BRC token has no audience claim and cannot be revoked before it
            expires, so it is only ever sent to the backend you confirmed it for.
            Signing out deletes it from this browser; it stays valid elsewhere until
            expiry.
          </p>
          <button
            type="button"
            onClick={() => {
              clearStoredToken();
              onSignedOut({ mode: "apikey", value: "" });
            }}
            className="mt-3 rounded border border-gray-300 bg-white px-3 py-2 text-sm text-gray-800 hover:bg-gray-100"
          >
            Sign out
          </button>
        </section>
      ) : null}

      <section className="rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-base font-semibold text-gray-900">Preferences</h2>
        <p className="mt-2 text-sm text-gray-600">
          There are no account preferences yet. The backend selection and your
          credential live in this browser only — nothing on this page is stored against
          your account, because the API has no user-preference resource.
        </p>
        <p className="mt-2 text-xs text-gray-500">
          The first one will be a <span className="font-medium">default collection</span>{" "}
          — the collection a query uses when you name none. It is deliberately not built
          yet.
        </p>
      </section>
    </div>
  );
}
