// The api-key + query inputs and submit. Labeled, disabled-while-pending,
// Enter-submits (native <form>).

interface Props {
  apiKey: string;
  setApiKey: (v: string) => void;
  query: string;
  setQuery: (v: string) => void;
  onSubmit: () => void;
  pending: boolean;
}

export function SearchForm({ apiKey, setApiKey, query, setQuery, onSubmit, pending }: Props) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (query.trim()) onSubmit();
      }}
      className="space-y-3"
    >
      <input
        type="password"
        placeholder="X-API-Key (leave blank if the API is keyless in dev)"
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
        className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        aria-label="API key"
        autoComplete="off"
      />
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
