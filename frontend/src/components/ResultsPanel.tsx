// Orchestrates the results area. Sources-first: the ranked source list is laid
// out ABOVE the answer, which settles in below — the researcher's eye lands on
// the trustworthy artifact (sources) while the answer text arrives. It's a single
// /v1/query call (answer + sources return atomically), so "sources-first" is
// layout + reading order, not an earlier network call; when streaming lands the
// answer fills in place while the sources stay put.

import type { QueryResponse } from "../api/client";
import { AnswerCard } from "./AnswerCard";
import { SourceSkeleton } from "./AnswerSkeleton";
import { SourceList } from "./SourceList";
import { ErrorBanner } from "./states/ErrorBanner";

interface Props {
  status: "pending" | "error" | "success";
  query: string;
  data?: QueryResponse;
  error?: Error | null;
  onRetry: () => void;
}

export function ResultsPanel({ status, query, data, error, onRetry }: Props) {
  if (status === "error" && error) {
    return <ErrorBanner error={error} onRetry={onRetry} />;
  }

  return (
    <div className="mt-6 space-y-6">
      {status === "pending" ? (
        <SourceSkeleton />
      ) : (
        data && <SourceList sources={data.sources} />
      )}
      <AnswerCard
        query={query}
        answer={data?.answer}
        rewrittenQueries={data?.rewritten_queries}
        pending={status === "pending"}
      />
    </div>
  );
}
