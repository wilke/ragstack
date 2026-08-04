import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { getCollections, queryRag, type QueryResponse } from "../api/client";
import { describeChunking } from "../lib/chunkers";
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

  // Populate the collection picker. Any authenticated caller can read this;
  // failure (e.g. 401 before a key is set) just hides the picker.
  const collections = useQuery({
    queryKey: ["collections", apiKey],
    queryFn: () => getCollections(apiKey || undefined),
    retry: false,
  });
  const opts = collections.data?.collections ?? [];

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
      queryRag({ query: q, top_k: 5, collection: collection || undefined }, apiKey || undefined),
  });

  const submit = () => {
    const q = query.trim();
    if (q) run.mutate(q);
  };

  const status = run.isPending ? "pending" : run.isError ? "error" : "success";

  return (
    <>
      {opts.length > 1 ? (
        <div className="mb-3 flex items-center gap-2">
          <label htmlFor="collection" className="text-xs font-medium text-gray-500">
            Collection
          </label>
          <select
            id="collection"
            value={collection}
            onChange={(e) => setCollection(e.target.value)}
            className="rounded-md border border-gray-300 px-2 py-1 text-sm"
          >
            {opts.map((c) => {
              // Shared with the Library picker (lib/chunkers.ts) so both name a
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
          <span className="text-xs text-gray-400">
            {(opts.find((c) => (c.default ? "" : c.id) === collection) ?? opts[0])?.model}
          </span>
        </div>
      ) : null}

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
