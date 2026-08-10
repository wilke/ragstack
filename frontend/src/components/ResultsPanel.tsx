// Orchestrates the results area. Answer-first: the synthesized answer is laid
// out ABOVE the ranked source list — read the conclusion, then verify it
// against the sources below. It's a single /v1/query call (answer + sources
// return atomically), so the order is layout + reading order only.

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
      <AnswerCard
        query={query}
        answer={data?.answer}
        rewrittenQueries={data?.rewritten_queries}
        pending={status === "pending"}
      />
      {status === "pending" ? (
        <SourceSkeleton />
      ) : (
        data && <SourceList sources={data.sources} />
      )}
    </div>
  );
}
