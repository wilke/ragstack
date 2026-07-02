// One retrieved source. Sources are the trust centrepiece, so this leads with
// scholarly identity (title + byline), then the matched passage, then citation
// actions. Raw score / retrieval_method are intentionally NOT shown — they move
// behind a future AI-eng debug toggle. All fields are untrusted → React text.

import type { Source } from "../api/client";
import { CitationActions } from "./CitationActions";
import { HighlightedContent } from "./HighlightedContent";

function byline(m: Source["metadata"]): string {
  const authors = Array.isArray(m.authors) ? m.authors.filter(Boolean).join(", ") : m.authors;
  return [m.doc_type, m.year, authors].filter(Boolean).map(String).join(" · ");
}

export function SourceCard({ rank, source }: { rank: number; source: Source }) {
  const { metadata } = source;
  const title = (metadata.title && String(metadata.title)) || source.doc_id;
  const line = byline(metadata);

  return (
    <li className="rounded border border-gray-200 p-3">
      <div className="flex items-baseline gap-2">
        <span className="shrink-0 text-sm text-gray-400">{rank}.</span>
        <span className="font-medium">{title}</span>
      </div>
      {line && <div className="mt-0.5 pl-5 text-xs text-gray-500">{line}</div>}
      <div className="mt-2">
        <HighlightedContent content={source.content} metadata={metadata} />
      </div>
      <CitationActions metadata={metadata} fallbackTitle={title} />
    </li>
  );
}
