import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  BACKEND_PRESETS,
  getApiBase,
  getStoredCredential,
  setApiBase,
} from "../api/config";
import { type Credential } from "../lib/auth";

// Which API the whole UI talks to. Persisted to localStorage; changing it
// invalidates every react-query cache so no panel keeps rendering the previous
// deployment's data.
//
// CREDENTIALS ARE NOT HERE. This used to carry an API-key box, a token badge and
// a Sign in button, which meant two places to sign in and two places to get it
// wrong. Signing in belongs to the login page (reachable from the account menu);
// this control answers only "which backend". The one credential concern that
// remains is re-resolving the stored credential after a base change, because a
// bearer token is bound to the backend it was confirmed for and must stop being
// sent when that changes.
//
// It lives in Account & preferences rather than the app header: picking a
// backend is a setting, not something to carry on every screen.

const CUSTOM = "__custom__";

function presetIdForUrl(url: string): string {
  const hit = BACKEND_PRESETS.find((p) => p.url === url);
  return hit ? hit.id : CUSTOM;
}

/** How the selected base is actually reached, in words. */
function effectiveLabel(base: string): string {
  if (!base) return "same origin (default proxy)";
  if (base.startsWith("/be/")) return `dev proxy → ${base.slice(4)}`;
  // Any other relative base is a path on this origin — typically the gateway
  // mount (/ragstack/<tenant>/api). Calling that "direct" would be wrong: it is
  // same-origin and goes through the front proxy.
  if (base.startsWith("/")) return `same origin → ${base}`;
  return `cross-origin → ${base}`;
}

export function BackendSwitcher({
  setCredential,
  onBaseChange,
}: {
  setCredential: (c: Credential) => void;
  /** Tell App the base moved. A `storage` event does NOT fire in the tab that
   *  wrote it, so the same-tab case has to be reported explicitly. */
  onBaseChange: (base: string) => void;
}) {
  const queryClient = useQueryClient();
  const [base, setBase] = useState(getApiBase());
  const [selectId, setSelectId] = useState(presetIdForUrl(getApiBase()));

  // localStorage is shared across tabs, but this component's `base` is seeded
  // once at mount — so a switch in another tab left this control naming a
  // backend that is no longer selected. Requests already fail closed (client.ts
  // re-reads storage per request); the label must not lie either.
  useEffect(() => {
    // Display state only. The credential resync and cache invalidation live in
    // App, which is always mounted — this component is not (it is on the
    // preferences screen), so it must not be the only thing listening.
    function resync() {
      const live = getApiBase();
      setBase(live);
      setSelectId(presetIdForUrl(live));
    }
    window.addEventListener("storage", resync);
    return () => window.removeEventListener("storage", resync);
  }, []);

  function applyBase(url: string) {
    setApiBase(url);
    // Read BACK rather than reusing what we wrote: storage normalizes (trailing
    // slashes, the same-origin marker, the gateway fallback), and state that
    // disagrees with getApiBase() disables the login page's binding check.
    const clean = getApiBase();
    setBase(clean);
    setSelectId(presetIdForUrl(clean));
    onBaseChange(clean);
    // A bearer token is bound to the base it was saved for: after a switch it
    // stops being sent until the user re-confirms it on the login page.
    setCredential(getStoredCredential());
    // Same-key queries won't re-run on a base-only change, so force a refetch.
    void queryClient.invalidateQueries();
  }

  function onSelect(id: string) {
    setSelectId(id);
    if (id === CUSTOM) return; // wait for the URL field
    const preset = BACKEND_PRESETS.find((p) => p.id === id);
    if (preset) applyBase(preset.url);
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="API backend"
          value={selectId}
          onChange={(e) => onSelect(e.target.value)}
          className="rounded border border-gray-300 bg-white px-2 py-1.5 text-sm"
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
            placeholder="http://host:port (called directly — local only)"
            title="Absolute URL, called directly (relies on CORS). Does not traverse a port forward — use a preset for that."
            defaultValue={base}
            aria-label="Custom API URL"
            onBlur={(e) => applyBase(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") applyBase((e.target as HTMLInputElement).value);
            }}
            className="w-64 rounded border border-gray-300 bg-white px-2 py-1.5 text-sm"
          />
        ) : null}
      </div>

      <p className="text-xs text-gray-500">{effectiveLabel(base)}</p>
    </div>
  );
}
