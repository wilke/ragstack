// The synthesized answer (text only — whitespace preserved), the rewritten-query
// chips, and the feedback control. Shows the skeleton while the request is in
// flight so the answer "settles in" below the already-laid-out sources.

import { AnswerSkeleton } from "./AnswerSkeleton";
import { FeedbackControl } from "./FeedbackControl";

interface Props {
  query: string;
  answer?: string;
  rewrittenQueries?: string[];
  pending: boolean;
}

export function AnswerCard({ query, answer, rewrittenQueries, pending }: Props) {
  return (
    <section aria-labelledby="answer-heading">
      <h2
        id="answer-heading"
        className="mb-1 text-sm font-medium uppercase tracking-wide text-gray-500"
      >
        Answer
      </h2>

      {pending ? (
        <AnswerSkeleton />
      ) : (
        <>
          {/* content is untrusted → rendered as React text (auto-escaped). */}
          <p className="whitespace-pre-wrap rounded bg-gray-50 p-3">{answer}</p>

          {rewrittenQueries && rewrittenQueries.length > 1 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {rewrittenQueries.map((q, i) => (
                <span
                  key={`${i}-${q}`}
                  className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500"
                >
                  {q}
                </span>
              ))}
            </div>
          )}

          {answer && (
            <div className="mt-3">
              <FeedbackControl query={query} answer={answer} />
            </div>
          )}
        </>
      )}
    </section>
  );
}
