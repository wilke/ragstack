import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  BACKEND_PRESETS,
  getApiBase,
  getStoredCredential,
  setApiBase,
} from "../api/config";
import { type Credential } from "../lib/auth";
import { LoginPanel } from "./LoginPanel";

// Header control: pick which API the whole UI talks to (asm / lucid / demo /
// custom) and carry a credential — both persisted to localStorage. Changing
// either invalidates every react-query cache so all panels refetch against the
// new target. This makes the "admin-only" / "Not Found" states self-serviceable
// from the browser instead of needing a separate Vite instance or env var.
//
// It also owns the sign-in: the credential can be an API key (the inline box,
// unchanged) or a pasted bearer token (the LoginPanel below). Both live in the
// app's ONE credential slot, so exactly one is ever sent — presenting both is a
// 400 server-side.

const CUSTOM = "__custom__";

function presetIdForUrl(url: string): string {
  const hit = BACKEND_PRESETS.find((p) => p.url === url);
  return hit ? hit.id : CUSTOM;
}

export function BackendSwitcher({
  credential,
  setCredential,
}: {
  credential: Credential;
  setCredential: (c: Credential) => void;
}) {
  const queryClient = useQueryClient();
  const [base, setBase] = useState(getApiBase());
  const [selectId, setSelectId] = useState(presetIdForUrl(getApiBase()));
  const [loginOpen, setLoginOpen] = useState(false);

  // localStorage is shared across tabs, but this component's `base` is seeded
  // once at mount — so a switch in another tab left this header naming a backend
  // that is no longer selected, and the login panel confirming a token against
  // it. Requests already fail closed (client.ts re-reads storage per request),
  // but the label must not lie about where a token would go.
  useEffect(() => {
    function resync() {
      const live = getApiBase();
      setBase(live);
      setSelectId(presetIdForUrl(live));
      setCredential(getStoredCredential());
    }
    window.addEventListener("storage", resync);
    return () => window.removeEventListener("storage", resync);
  }, [setCredential]);

  function applyBase(url: string) {
    const clean = url.replace(/\/$/, "");
    setApiBase(clean);
    setBase(clean);
    setSelectId(presetIdForUrl(clean));
    // A bearer token is bound to the base it was saved for, so re-resolve the
    // credential: after a switch the token stops being sent until the user
    // confirms it for the new target (see config.getStoredCredential).
    //
    // This is the UI half only. It cannot be the enforcement, because the
    // invalidateQueries() below refires every already-registered observer whose
    // queryFn still closes over the OLD token value — before React re-renders
    // with this one. The binding is enforced per request in client.ts
    // (`sendableCredential`), which re-reads storage as the header is built.
    setCredential(getStoredCredential());
    // Same-key queries won't re-run on a base-only change, so force a refetch.
    void queryClient.invalidateQueries();
  }

  // Identity is not always part of a query key (a token can change while the
  // key string stays ""), so invalidate explicitly on every credential change —
  // otherwise one principal can be shown another's cached collection list.
  function applyCredential(c: Credential) {
    setCredential(c);
    void queryClient.invalidateQueries();
  }

  function onSelect(id: string) {
    setSelectId(id);
    if (id === CUSTOM) return; // wait for the URL field
    const preset = BACKEND_PRESETS.find((p) => p.id === id);
    if (preset) applyBase(preset.url);
  }

  function onKey(v: string) {
    // One writer only: App.tsx persists through config.setStoredCredential.
    // No explicit invalidation here — the key value is part of every query key,
    // so it refetches on its own (and invalidating per keystroke would storm).
    setCredential({ mode: "apikey", value: v });
  }

  const effective = !base
    ? "default proxy"
    : base.startsWith("/be/")
      ? `proxy → ${base.slice(4)}`
      : `direct → ${base}`;

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-xs">
      <span className="font-medium text-gray-500">API</span>

      <select
        aria-label="API backend"
        value={selectId}
        onChange={(e) => onSelect(e.target.value)}
        className="rounded border border-gray-300 bg-white px-2 py-1 text-xs"
      >
        {BACKEND_PRESETS.map((p) => (
          <option key={p.id} value={p.id}>
            {p.label}
          </option>
        ))}
        <option value={CUSTOM}>Custom…</option>
      </select>

      {selectId === CUSTOM ? (
        <input
          type="url"
          placeholder="http://host:port (direct — local only)"
          title="Absolute URL, called directly (relies on CORS). Does not traverse a port forward — use a preset for that."
          defaultValue={base}
          aria-label="Custom API URL"
          onBlur={(e) => applyBase(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") applyBase((e.target as HTMLInputElement).value);
          }}
          className="w-56 rounded border border-gray-300 bg-white px-2 py-1 text-xs"
        />
      ) : null}

      {credential.mode === "bearer" ? (
        credential.value ? (
          <span className="rounded border border-gray-300 bg-white px-2 py-1 text-gray-600">
            bearer token
          </span>
        ) : (
          // Bearer mode with nothing sendable: the saved token is bound to a
          // different backend. Requests are going out anonymous, which reads as
          // a broken backend unless the header says otherwise. Sign in re-binds.
          <span
            className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-amber-800"
            title="The saved token was confirmed for a different backend and is not being sent to this one. Open Sign in to send it here."
          >
            token not sent here
          </span>
        )
      ) : (
        <input
          type="password"
          placeholder="admin key (optional)"
          value={credential.value}
          aria-label="Admin API key"
          onChange={(e) => onKey(e.target.value)}
          className="w-44 rounded border border-gray-300 bg-white px-2 py-1 text-xs"
        />
      )}

      <button
        type="button"
        onClick={() => setLoginOpen((o) => !o)}
        aria-expanded={loginOpen}
        className="rounded border border-gray-300 bg-white px-2 py-1 text-xs hover:bg-gray-100"
      >
        {loginOpen ? "Close sign-in" : "Sign in"}
      </button>

      <span className="ml-auto truncate text-gray-400" title={effective}>
        → {effective}
      </span>

      {loginOpen ? (
        <LoginPanel credential={credential} setCredential={applyCredential} base={base} />
      ) : null}
    </div>
  );
}
