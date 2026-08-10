import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { getCollections, getConfig, queryRag, type QueryResponse } from "../api/client";
import { describeChunking } from "../lib/chunkers";
import {
  DEFAULT_QUERY_OPTIONS,
  QueryOptionsMenu,
  queryOptionsRequest,
  type QueryOptions,
} from "./QueryOptionsMenu";
import { ResultsPanel } from "./ResultsPanel";
import { SearchForm } from "./SearchForm";
import { EmptyState } from "./states/EmptyState";

// Phase-1a Explore MVP: a sources-first query console over /v1/query. Single
// request → answer + sources return atomically; the source list is the trust
// centrepiece (rendered first), the answer settles in below.

export function ExploreView({
  apiKey,
  setApiKey,
}: {
  apiKey: string;
  setApiKey: (v: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [collection, setCollection] = useState(""); // "" → default collection
  // Pipeline levers from the Options menu. Applied on the NEXT search — an
  // in-flight request keeps the options it was sent with.
  const [options, setOptions] = useState<QueryOptions>(DEFAULT_QUERY_OPTIONS);

  // Populate the collection picker. Any authenticated caller can read this;
  // failure (e.g. 401 before a key is set) just hides the picker.
  const collections = useQuery({
    queryKey: ["collections", apiKey],
    queryFn: () => getCollections(apiKey || undefined),
    retry: false,
  });
  const opts = collections.data?.collections ?? [];

  // The server's effective config, for presenting its rerank default in the
  // Options menu. /v1/config is admin-only — a 403 just leaves the menu on the
  // code default, it is not an error worth surfacing here.
  const config = useQuery({
    queryKey: ["config", apiKey],
    queryFn: () => getConfig(apiKey || undefined),
    retry: false,
  });
  const serverRerank = config.data ? config.data.rerank_enabled === true : null;

  // Reset a stale selection when the registry changes (apiKey/tenant switch): a
  // collection no longer offered would be submitted as a phantom id (backend 404),
  // and the picker hides once only the default remains — leaving it un-clearable.
  useEffect(() => {
    if (opts.length === 0) return;
    const valid = new Set(opts.map((c) => (c.default ? "" : c.id)));
    if (!valid.has(collection)) setCollection("");
  }, [opts, collection]);

  const run = useMutation<QueryResponse, Error, string>({
    mutationFn: (q) =>
      queryRag(
        { query: q, collection: collection || undefined, ...queryOptionsRequest(options) },
        apiKey || undefined,
      ),
  });

  const submit = () => {
    const q = query.trim();
    if (q) run.mutate(q);
  };

  const status = run.isPending ? "pending" : run.isError ? "error" : "success";

  return (
    <>
      {/* Collection picker (when there is a choice) + the Options popover. The
          menu renders unconditionally — the pipeline levers apply to the default
          collection too. */}
      <div className="mb-3 flex items-center gap-2">
        {opts.length > 1 ? (
          <>
            <label htmlFor="collection" className="text-xs font-medium text-gray-500">
              Collection
            </label>
            <select
              id="collection"
              value={collection}
              onChange={(e) => setCollection(e.target.value)}
              className="min-w-0 rounded-md border border-gray-300 px-2 py-1 text-sm"
            >
              {opts.map((c) => {
                // Shared with the Collection picker (lib/chunkers.ts) so both name a
                // collection's build config the same way, and semantic collections
                // don't get an invented size appended.
                const built = describeChunking(c);
                return (
                  <option key={c.id} value={c.default ? "" : c.id}>
                    {c.label}
                    {c.count != null ? ` (${c.count.toLocaleString()})` : ""}
                    {built ? ` · ${built}` : ""}
                  </option>
                );
              })}
            </select>
            <span className="truncate text-xs text-gray-400">
              {(opts.find((c) => (c.default ? "" : c.id) === collection) ?? opts[0])?.model}
            </span>
          </>
        ) : null}
        <QueryOptionsMenu
          value={options}
          onChange={(patch) => setOptions((o) => ({ ...o, ...patch }))}
          serverRerank={serverRerank}
        />
      </div>

      <SearchForm
        apiKey={apiKey}
        setApiKey={setApiKey}
        query={query}
        setQuery={setQuery}
        onSubmit={submit}
        pending={run.isPending}
      />

      {run.isIdle ? (
        <EmptyState />
      ) : (
        <ResultsPanel
          status={status}
          query={run.variables ?? query}
          data={run.data}
          error={run.error}
          onRetry={() => run.variables && run.mutate(run.variables)}
        />
      )}
    </>
  );
}
