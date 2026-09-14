// Sign-in for the demo.
//
// Reuses `api/identity.ts` rather than reimplementing it, and that module's
// design note is worth not undoing: the password goes from the BROWSER STRAIGHT
// TO BV-BRC's provider (`user.patricbrc.org/authenticate`, the same endpoint
// `p3-login` uses). It never touches ragstack, this app's origin, the gateway,
// or any log we keep. The exchange is `credentials: "omit"` and
// `redirect: "error"` — do not "simplify" either away.
//
// WHY THERE IS NO COOKIE. The API has no cookie path at all: it authenticates
// from the `Authorization` (or `X-API-Key`) header only — python/ragstack/api/
// security.py uses APIKeyHeader, and there is no set_cookie anywhere in the
// service. So there is no session to establish and nothing to persist server
// side; what we hold is a signed BV-BRC token, which the API verifies offline
// against a pinned key on every request (identity/bvbrc.py). It is a claim we
// present, not a session we were granted — which is also why signing out is
// purely local: there is no server state to clear.
//
// The token lives in sessionStorage (see session.ts), so it is gone when the tab
// closes.

import { useState } from "react";
import { BVBRC_EXCHANGE, SignInError, exchangePassword } from "../api/identity";
import { tenant } from "./api";

const INPUT =
  "w-full rounded-panel border border-line px-3 py-2 text-sm text-strong placeholder:text-faint focus:border-ink-900 focus:outline-none";

export function LoginGate({ onToken }: { onToken: (token: string) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  // The escape hatch for anyone who already has a token from `p3-login`
  // (~/.patric_token) and would rather not retype a password.
  const [paste, setPaste] = useState(false);
  const [pasted, setPasted] = useState("");

  async function signIn(e: React.FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setPending(true);
    setError("");
    try {
      const token = await exchangePassword(BVBRC_EXCHANGE, username.trim(), password);
      // Drop the password from state the moment the exchange returns.
      setPassword("");
      onToken(token.trim());
    } catch (err) {
      setError(
        err instanceof SignInError
          ? err.message
          : `Sign-in failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="mx-auto max-w-md px-4 py-16">
      <h1 className="text-xl font-semibold text-strong">BV-BRC Literature Search</h1>
      <p className="mt-2 text-sm text-dim">
        Sign in with your BV-BRC account. It opens the <code>{tenant()}</code> corpus and the
        Copilot model.
      </p>

      {!paste ? (
        <form className="mt-6 space-y-3" onSubmit={signIn}>
          <div>
            <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-dim" htmlFor="lit-user">
              Username
            </label>
            <input
              id="lit-user"
              className={INPUT}
              value={username}
              autoComplete="username"
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-dim" htmlFor="lit-pass">
              Password
            </label>
            <input
              id="lit-pass"
              type="password"
              className={INPUT}
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error && <p className="text-sm text-strong">{error}</p>}
          <button
            type="submit"
            disabled={pending || !username.trim() || !password}
            className="rounded-pill bg-accent px-5 py-2 text-sm font-semibold text-ink-900 disabled:opacity-50"
          >
            {pending ? "Signing in…" : "Sign in"}
          </button>
          <p className="pt-2 text-xs text-faint">
            Sent directly to BV-BRC, never to this application.{" "}
            <button type="button" onClick={() => setPaste(true)} className="underline hover:text-dim">
              Paste a token instead
            </button>
          </p>
        </form>
      ) : (
        <form
          className="mt-6 space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (pasted.trim()) onToken(pasted.trim());
          }}
        >
          <textarea
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
            placeholder="un=…|tokenid=…|sig=…"
            rows={4}
            className={`${INPUT} font-mono text-xs`}
            aria-label="BV-BRC token"
          />
          <button
            type="submit"
            disabled={!pasted.trim()}
            className="rounded-pill bg-accent px-5 py-2 text-sm font-semibold text-ink-900 disabled:opacity-50"
          >
            Continue
          </button>
          <p className="pt-2 text-xs text-faint">
            The contents of <code>~/.patric_token</code>, or the output of{" "}
            <code>p3-login</code>.{" "}
            <button type="button" onClick={() => setPaste(false)} className="underline hover:text-dim">
              Use username and password
            </button>
          </p>
        </form>
      )}
    </div>
  );
}
