import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { queryRag, type QueryResponse } from "./api/client";
import { ResultsPanel } from "./components/ResultsPanel";
import { SearchForm } from "./components/SearchForm";
import { EmptyState } from "./components/states/EmptyState";

// Phase-1a Explore MVP: a sources-first query console over the existing
// /v1/query. Single request → answer + sources return atomically; the source
// list is the trust centrepiece (rendered first), the answer settles in below.
// No backend changes. Deferred to follow-ups (see #93): true intra-passage
// highlighting (needs chunk-relative match offsets), neighbor context (needs a
// chunk-by-id endpoint), an AI-eng debug toggle, streaming, and SSO.

export function App() {
  const [apiKey, setApiKey] = useState("");
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
    <div className="mx-auto max-w-3xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold">RAGStack Explorer</h1>
        <p className="text-sm text-gray-500">Explore — ask the corpus, verify the sources</p>
      </header>

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
    </div>
  );
}
