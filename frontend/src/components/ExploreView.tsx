import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { queryRag, type QueryResponse } from "../api/client";
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

  const run = useMutation<QueryResponse, Error, string>({
    mutationFn: (q) => queryRag({ query: q, top_k: 5 }, apiKey || undefined),
  });

  const submit = () => {
    const q = query.trim();
    if (q) run.mutate(q);
  };

  const status = run.isPending ? "pending" : run.isError ? "error" : "success";

  return (
    <>
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
