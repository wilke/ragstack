// One retrieved source. Sources are the trust centrepiece, so this leads with
// scholarly identity (rank + title + byline), then the matched passage behind
// its rule, then citation actions with a per-card Evidence jump. The 3px left
// rule encodes rank (1 navy, 2–3 sky, rest neutral); raw score /
// retrieval_method are intentionally NOT shown — they belong to Evidence.
// All fields are untrusted → React text. The off-topic chip renders ONLY from
// an explicit backend flag (`metadata.off_topic`) — the API does not emit one
// yet (handoff README backend gap #4), and the client must never infer
// off-topicness from scores, so most deployments will never show it.

import type { Source } from "../api/client";
import { CitationActions } from "./CitationActions";
import { HighlightedContent } from "./HighlightedContent";

function byline(m: Source["metadata"]): string {
  const authors = Array.isArray(m.authors) ? m.authors.filter(Boolean).join(", ") : m.authors;
  return [m.doc_type, m.year, authors].filter(Boolean).map(String).join(" · ");
}

// Rank → left-rule color; rank-1's numeral goes amber like the mockup.
function rule(rank: number): string {
  if (rank === 1) return "border-l-ink-900";
  if (rank <= 3) return "border-l-sky";
  return "border-l-[#d8d7d2]";
}

export function SourceCard({
  rank,
  source,
  onOpenEvidence,
}: {
  rank: number;
  source: Source;
  // Receives this card's 0-based index so Evidence opens on this source.
  onOpenEvidence?: (sourceIndex?: number) => void;
}) {
  const { metadata } = source;
  const title = (metadata.title && String(metadata.title)) || source.doc_id;
  const line = byline(metadata);

  return (
    <li className={`rounded-row border border-line border-l-[3px] px-[17px] py-[15px] ${rule(rank)}`}>
      <div className="mb-1 flex items-baseline gap-[9px]">
        <span
          className={`shrink-0 font-mono text-xs font-medium ${
            rank === 1 ? "text-[#ffb800]" : "text-faint"
          }`}
        >
          {rank}.
        </span>
        <span className="flex-1 font-display text-[15px] font-semibold leading-[1.35] text-ink-900">
          {title}
        </span>
        {metadata.off_topic === true ? (
          <span className="shrink-0 rounded-[3px] bg-rustSoft px-1.5 py-[3px] font-mono text-[10px] font-medium text-rust">
            off-topic
          </span>
        ) : null}
      </div>
      {line && <div className="mb-[9px] pl-6 font-mono text-[11px] leading-normal text-dim">{line}</div>}
      {/* HighlightedContent owns the match markup; the child overrides restyle
          its <p> (indent behind a 2px rule, 13.5px body) without forking it. */}
      <div className="ml-6 [&>p]:border-l-2 [&>p]:border-line [&>p]:pl-6 [&>p]:text-[13.5px] [&>p]:leading-[1.7] [&>p]:text-body">
        <HighlightedContent content={source.content} metadata={metadata} />
      </div>
      <div className="pl-6">
        <CitationActions
          metadata={metadata}
          fallbackTitle={title}
          trailing={
            onOpenEvidence ? (
              <button
                type="button"
                onClick={() => onOpenEvidence(rank - 1)}
                className="ml-auto text-[11px] font-medium text-link hover:underline"
              >
                Evidence →
              </button>
            ) : undefined
          }
        />
      </div>
    </li>
  );
}
