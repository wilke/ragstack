import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { ApiError, queryRag, type QueryResponse, type Source } from "./api/client";

// Phase-1a scaffold: a minimal query console against the existing /v1/query.
// The full Explore module (sources-first rendering, matched-span highlighting via
// char-offsets, citation actions, thumbs feedback, AI-eng debug mode) builds on
// this — see the tracking issue.

export function App() {
  const [apiKey, setApiKey] = useState("");
  const [query, setQuery] = useState("");

  const run = useMutation<QueryResponse, Error, string>({
    mutationFn: (q) => queryRag({ query: q, top_k: 5 }, apiKey || undefined),
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (query.trim()) run.mutate(query.trim());
  };

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold">RAGStack Explorer</h1>
        <p className="text-sm text-gray-500">Query console (scaffold) — /v1/query</p>
      </header>

      <form onSubmit={onSubmit} className="space-y-3">
        <input
          type="password"
          placeholder="X-API-Key (leave blank if the API is keyless in dev)"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          className="w-full rounded border border-gray-300 px-3 py-2 text-sm"
          aria-label="API key"
        />
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Ask the corpus…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="flex-1 rounded border border-gray-300 px-3 py-2"
            aria-label="Query"
          />
          <button
            type="submit"
            disabled={run.isPending || !query.trim()}
            className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
          >
            {run.isPending ? "Searching…" : "Search"}
          </button>
        </div>
      </form>

      {run.isError && (
        <p className="mt-4 rounded bg-red-50 p-3 text-sm text-red-700">
          {run.error instanceof ApiError
            ? `Error ${run.error.status}: ${run.error.message}`
            : run.error.message}
        </p>
      )}

      {run.data && <Results data={run.data} />}
    </div>
  );
}

function Results({ data }: { data: QueryResponse }) {
  return (
    <section className="mt-6 space-y-6">
      <div>
        <h2 className="mb-1 text-sm font-medium uppercase tracking-wide text-gray-500">
          Answer
        </h2>
        <p className="whitespace-pre-wrap rounded bg-gray-50 p-3">{data.answer}</p>
      </div>

      {data.rewritten_queries.length > 1 && (
        <p className="text-xs text-gray-500">
          Searched: {data.rewritten_queries.join(" · ")}
        </p>
      )}

      <div>
        <h2 className="mb-2 text-sm font-medium uppercase tracking-wide text-gray-500">
          Sources ({data.sources.length})
        </h2>
        <ol className="space-y-3">
          {data.sources.map((s, i) => (
            <SourceCard key={s.chunk_id} rank={i + 1} source={s} />
          ))}
        </ol>
      </div>
    </section>
  );
}

function SourceCard({ rank, source }: { rank: number; source: Source }) {
  const title = String(source.metadata.title ?? source.doc_id);
  const year = source.metadata.year;
  const docType = source.metadata.doc_type;
  return (
    <li className="rounded border border-gray-200 p-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium">
          {rank}. {title}
        </span>
        <span className="shrink-0 text-xs text-gray-400">
          score {source.score.toFixed(3)}
        </span>
      </div>
      <div className="mt-0.5 text-xs text-gray-500">
        {[docType, year].filter(Boolean).join(" · ")}
      </div>
      <p className="mt-2 line-clamp-4 text-sm text-gray-700">{source.content}</p>
    </li>
  );
}
