// The api-key + query inputs and submit. Labeled, disabled-while-pending,
// Enter-submits (native <form>).
//
// The key box hides itself while a bearer token is active: it is bound to the
// app's single credential slot, so typing in it would switch the app back to
// key auth and truncate the token. The header owns the login.

import { getStoredAuthMode } from "../api/config";
import { SIGNED_IN_HINT } from "../lib/auth";

interface Props {
  apiKey: string;
  setApiKey: (v: string) => void;
  query: string;
  setQuery: (v: string) => void;
  onSubmit: () => void;
  pending: boolean;
}

export function SearchForm({ apiKey, setApiKey, query, setQuery, onSubmit, pending }: Props) {
  const bearer = getStoredAuthMode() === "bearer";
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (query.trim()) onSubmit();
      }}
      className="space-y-3"
    >
      {bearer ? (
        <p className="text-xs text-gray-500">{SIGNED_IN_HINT}</p>
      ) : (
        <input
          type="password"
          placeholder="X-API-Key (leave blank if the API is keyless in dev)"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          aria-label="API key"
          autoComplete="off"
        />
      )}
      <div className="flex gap-2">
        <input
          type="text"
          placeholder="Ask the corpus…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="flex-1 rounded border border-gray-300 px-3 py-2 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          aria-label="Query"
        />
        <button
          type="submit"
          disabled={pending || !query.trim()}
          className="rounded bg-blue-600 px-4 py-2 text-white transition-opacity hover:bg-blue-700 disabled:opacity-50"
        >
          {pending ? "Searching…" : "Search"}
        </button>
      </div>
    </form>
  );
}
