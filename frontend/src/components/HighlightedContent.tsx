// Renders passage text with the matched span marked — as separate React
// text/<mark> nodes (each auto-escaped), NEVER dangerouslySetInnerHTML. The
// <mark> carries a non-color cue (underline) so highlight isn't conveyed by
// colour alone.
//
// MVP: the API doesn't emit chunk-relative match offsets yet, so
// segmentContent returns a single unmarked segment and the whole passage is
// framed (by the caller's border) as "the matched passage". When the backend
// adds match_start/match_end this lights up with zero further changes.

import { segmentContent } from "../lib/highlight";
import type { SourceMetadata } from "../api/client";

export function HighlightedContent({
  content,
  metadata,
}: {
  content: string;
  metadata: SourceMetadata;
}) {
  const segments = segmentContent(content, metadata.match_start, metadata.match_end);
  return (
    <p className="border-l-2 border-gray-200 pl-3 text-sm text-gray-700">
      {segments.map((seg, i) =>
        seg.marked ? (
          <mark key={i} className="bg-yellow-200 underline decoration-yellow-500">
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </p>
  );
}
