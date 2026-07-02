// Ranked sources as an <ol> so rank order is conveyed to assistive tech.

import type { Source } from "../api/client";
import { SourceCard } from "./SourceCard";

export function SourceList({ sources }: { sources: Source[] }) {
  return (
    <section aria-labelledby="sources-heading">
      <h2
        id="sources-heading"
        className="mb-2 text-sm font-medium uppercase tracking-wide text-gray-500"
      >
        Sources ({sources.length})
      </h2>
      {sources.length === 0 ? (
        <p className="rounded bg-amber-50 p-3 text-sm text-amber-800">
          No sources matched — the answer may be low-confidence.
        </p>
      ) : (
        <ol className="space-y-3">
          {sources.map((s, i) => (
            <SourceCard key={s.chunk_id} rank={i + 1} source={s} />
          ))}
        </ol>
      )}
    </section>
  );
}
