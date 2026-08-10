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
        <p className="text-xs text-dim">{SIGNED_IN_HINT}</p>
      ) : (
        <input
          type="password"
          placeholder="X-API-Key (leave blank if the API is keyless in dev)"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          className="w-full rounded-pill border border-line px-5 py-2 text-xs text-strong placeholder:text-faint focus:border-ink-900 focus:outline-none"
          aria-label="API key"
          autoComplete="off"
        />
      )}
      <div className="flex gap-2.5">
        <input
          type="text"
          placeholder="Ask the corpus…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="h-12 min-w-0 flex-1 rounded-pill border-[1.5px] border-ink-900 px-5 text-[15.5px] text-strong placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-accent"
          aria-label="Query"
        />
        <button
          type="submit"
          disabled={pending || !query.trim()}
          className="flex h-12 shrink-0 items-center gap-2 rounded-pill bg-accent px-[22px] text-sm font-semibold text-ink-900 transition-opacity hover:brightness-95 disabled:opacity-50"
        >
          {pending ? (
            "Asking…"
          ) : (
            <>
              Ask{" "}
              <span aria-hidden="true" className="text-[15px]">
                →
              </span>
            </>
          )}
        </button>
      </div>
    </form>
  );
}
