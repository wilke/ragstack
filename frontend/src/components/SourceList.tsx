// Ranked sources as an <ol> so rank order is conveyed to assistive tech,
// headed by the SOURCES eyebrow with the whole-run Evidence jump.

import type { Source } from "../api/client";
import { Eyebrow } from "./explore/Eyebrow";
import { HelpTip } from "./HelpTip";
import { SourceCard } from "./SourceCard";

export function SourceList({
  sources,
  onOpenEvidence,
}: {
  sources: Source[];
  // Called bare for "open the whole run"; cards pass their 0-based index.
  onOpenEvidence?: (sourceIndex?: number) => void;
}) {
  return (
    <section aria-labelledby="sources-heading" className="border-t border-line pt-6">
      <div className="mb-3.5 flex items-baseline gap-2.5">
        <Eyebrow id="sources-heading">Sources ({sources.length})</Eyebrow>
        {/* Card anatomy is explained ONCE here, not per card. */}
        <HelpTip icon side="bottom" term="source">
          <span className="mb-1.5 block">
            One card per retrieved chunk, ordered by the pipeline&rsquo;s retrieval score
            — the left rule shades rank 1, then ranks 2–3, then the rest. Rank means
            &ldquo;best match for this query&rdquo;, not &ldquo;true&rdquo;: read the
            passage before you rely on it.
          </span>
          <span className="mb-1.5 block">
            Under the title: doc_type · year · authors, from whatever metadata the
            ingester stamped — any of the three can be missing, and the title falls back
            to the doc_id.
          </span>
          <span className="block">
            The quoted text is the retrieved chunk verbatim, not the whole document. The
            numeric score behind the ranking is shown in Evidence.
          </span>
        </HelpTip>
        {sources.length > 0 && onOpenEvidence ? (
          <button
            type="button"
            onClick={() => onOpenEvidence()}
            className="ml-auto text-[11.5px] font-medium text-link hover:underline"
          >
            Open all in Evidence →
          </button>
        ) : null}
      </div>
      {sources.length === 0 ? (
        <p className="rounded-panel bg-accent-soft p-3 text-sm text-accent-text">
          No sources matched — the answer may be low-confidence.
        </p>
      ) : (
        <ol className="space-y-3">
          {sources.map((s, i) => (
            <SourceCard key={s.chunk_id} rank={i + 1} source={s} onOpenEvidence={onOpenEvidence} />
          ))}
        </ol>
      )}
    </section>
  );
}
