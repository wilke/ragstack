// Sign-in for the control plane.
//
// Not src/components/LoginView.tsx. That screen's whole job is to PERSIST a
// credential: it reads and writes localStorage through api/config.ts, binds a
// bearer token to a backend base, and offers a paste-a-token affordance so the
// token survives reloads. Every one of those is the opposite of what the
// control plane requires — the credential here is spent once on
// `POST /v1/session` and dropped, and nothing but the returned session id is
// kept (sessionStorage, per tab).
//
// What IS reused is the part that matters: `exchangePassword` against
// `BVBRC_EXCHANGE` (src/api/identity.ts), so the password still goes from the
// browser STRAIGHT TO BV-BRC and never touches a RAGStack service — ours or the
// control plane's. The token that comes back is posted to `/v1/session` and
// forgotten.

import { useState } from "react";
import { BVBRC_EXCHANGE, exchangePassword, SignInError } from "../../api/identity";
import { insecureContextWarning, passwordOverHttpAllowed } from "../../lib/auth";
import { CtlError } from "../api/http";
import { signInWithApiKey, signInWithBearer, type CtlSession } from "../auth/session";

const INPUT =
  "w-full rounded-panel border border-line bg-white px-3 py-2 text-sm focus:border-ink-900 focus:outline-none";

type Method = "key" | "bvbrc";

function failureMessage(err: unknown): string {
  if (err instanceof CtlError) {
    if (err.code === "forbidden") {
      return "That credential is valid but its subject is not on the control plane's principal list.";
    }
    if (err.code === "auth_required") return "That credential was not accepted.";
    if (err.code === "both_credentials") return "Only one credential may be presented.";
    if (err.code === "rate_limited") return "Too many attempts — wait a moment.";
    return `Sign-in failed (${err.status}).`;
  }
  if (err instanceof SignInError) return err.message;
  return "Could not reach the control plane.";
}

export function LoginView({ onSignedIn }: { onSignedIn?: (s: CtlSession) => void }) {
  const [method, setMethod] = useState<Method>("key");
  const [apiKey, setApiKey] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const insecure = insecureContextWarning(
    typeof window !== "undefined" ? window.isSecureContext : true,
  );
  // Same deployment override the tenant login honours: the form still renders,
  // and the warning above it is NOT suppressed.
  const passwordBlocked =
    insecure !== null && !passwordOverHttpAllowed(import.meta.env.VITE_ALLOW_PASSWORD_OVER_HTTP);

  async function run(fn: () => Promise<CtlSession>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const session = await fn();
      // Drop every credential from component state the moment it has been
      // spent. Nothing below this line may be able to replay a sign-in.
      setApiKey("");
      setPassword("");
      onSignedIn?.(session);
    } catch (err) {
      setError(failureMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function submitKey(e: React.FormEvent) {
    e.preventDefault();
    const key = apiKey.trim();
    if (!key) return;
    void run(() => signInWithApiKey(key));
  }

  function submitPassword(e: React.FormEvent) {
    e.preventDefault();
    if (passwordBlocked) return;
    if (!username.trim() || !password) return;
    void run(async () => {
      const token = await exchangePassword(BVBRC_EXCHANGE, username.trim(), password);
      return signInWithBearer(token);
    });
  }

  return (
    <div className="mx-auto max-w-[420px] px-5 py-14">
      <div className="mb-1 font-mono text-[10px] font-medium uppercase tracking-[.16em] text-muted">
        ragstack
      </div>
      <h1 className="mb-1 font-display text-[26px] font-extrabold leading-tight text-ink-900">
        Control plane
      </h1>
      <p className="mb-6 text-[13px] leading-relaxed text-body">
        Sign in with a control-plane API key, or with your BV-BRC account. Either
        one is exchanged for a read-only session that lives in this tab only —
        nothing is written to this browser's persistent storage.
      </p>

      <div role="tablist" aria-label="Sign-in method" className="mb-5 flex gap-1">
        {(
          [
            ["key", "Control-plane key"],
            ["bvbrc", "BV-BRC account"],
          ] as [Method, string][]
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={method === id}
            onClick={() => {
              setMethod(id);
              setError(null);
            }}
            className={`rounded-panel px-3 py-1.5 text-[12.5px] font-medium ${
              method === id
                ? "border-b-2 border-accent bg-accent-soft text-ink-900"
                : "text-dim hover:text-ink-900"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {insecure && (
        <div role="status" className="mb-4 rounded-card bg-accent-soft p-3 text-[12.5px] text-accent-text">
          {insecure}
        </div>
      )}

      {method === "key" ? (
        <form onSubmit={submitKey}>
          <label className="mb-1.5 block text-[12px] font-medium text-strong" htmlFor="ctl-api-key">
            Control-plane API key
          </label>
          <input
            id="ctl-api-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            className={INPUT}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="X-API-Key"
          />
          <p className="mt-2 text-[11.5px] leading-relaxed text-dim">
            Presented once to <code className="font-mono">POST /v1/session</code>. The key is not
            stored; a mutation (from PR-C onwards) will ask for it again.
          </p>
          <button
            type="submit"
            disabled={busy || !apiKey.trim()}
            className="mt-4 w-full rounded-panel bg-ink-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      ) : (
        <form onSubmit={submitPassword}>
          <label className="mb-1.5 block text-[12px] font-medium text-strong" htmlFor="ctl-username">
            BV-BRC username
          </label>
          <input
            id="ctl-username"
            className={INPUT}
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <label
            className="mb-1.5 mt-3 block text-[12px] font-medium text-strong"
            htmlFor="ctl-password"
          >
            Password
          </label>
          <input
            id="ctl-password"
            type="password"
            className={INPUT}
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <p className="mt-2 text-[11.5px] leading-relaxed text-dim">
            The password goes straight to BV-BRC from this browser; RAGStack and the
            control plane never see it. The token it returns is posted once to{" "}
            <code className="font-mono">/v1/session</code> and discarded.
          </p>
          <button
            type="submit"
            disabled={busy || passwordBlocked || !username.trim() || !password}
            className="mt-4 w-full rounded-panel bg-ink-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      )}

      {error && (
        <div role="alert" className="mt-4 rounded-card bg-rustSoft p-3 text-[12.5px] text-rust">
          {error}
        </div>
      )}
    </div>
  );
}
