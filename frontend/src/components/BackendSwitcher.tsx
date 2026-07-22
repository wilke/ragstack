import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  BACKEND_PRESETS,
  getApiBase,
  setApiBase,
  setStoredApiKey,
} from "../api/config";

// Header control: pick which API the whole UI talks to (asm / lucid / demo /
// custom) and carry an admin key — both persisted to localStorage. Changing
// either invalidates every react-query cache so all panels refetch against the
// new target. This makes the "admin-only" / "Not Found" states self-serviceable
// from the browser instead of needing a separate Vite instance or env var.

const CUSTOM = "__custom__";

function presetIdForUrl(url: string): string {
  const hit = BACKEND_PRESETS.find((p) => p.url === url);
  return hit ? hit.id : CUSTOM;
}

export function BackendSwitcher({
  apiKey,
  setApiKey,
}: {
  apiKey: string;
  setApiKey: (v: string) => void;
}) {
  const queryClient = useQueryClient();
  const [base, setBase] = useState(getApiBase());
  const [selectId, setSelectId] = useState(presetIdForUrl(getApiBase()));

  function applyBase(url: string) {
    const clean = url.replace(/\/$/, "");
    setApiBase(clean);
    setBase(clean);
    setSelectId(presetIdForUrl(clean));
    // Same-key queries won't re-run on a base-only change, so force a refetch.
    void queryClient.invalidateQueries();
  }

  function onSelect(id: string) {
    setSelectId(id);
    if (id === CUSTOM) return; // wait for the URL field
    const preset = BACKEND_PRESETS.find((p) => p.id === id);
    if (preset) applyBase(preset.url);
  }

  function onKey(v: string) {
    setApiKey(v);
    setStoredApiKey(v);
    // apiKey is part of every query key → changing it refetches on its own.
  }

  const effective = base || "same-origin (proxy)";

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
          placeholder="http://host:port"
          defaultValue={base}
          aria-label="Custom API URL"
          onBlur={(e) => applyBase(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") applyBase((e.target as HTMLInputElement).value);
          }}
          className="w-56 rounded border border-gray-300 bg-white px-2 py-1 text-xs"
        />
      ) : null}

      <input
        type="password"
        placeholder="admin key (optional)"
        value={apiKey}
        aria-label="Admin API key"
        onChange={(e) => onKey(e.target.value)}
        className="w-44 rounded border border-gray-300 bg-white px-2 py-1 text-xs"
      />

      <span className="ml-auto truncate text-gray-400" title={effective}>
        → {effective}
      </span>
    </div>
  );
}
