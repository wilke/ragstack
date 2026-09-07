// "The document" — the segmented text the reader answers question (b) against:
// numbered sentences, unit headings, and the claimed spans highlighted in place.
//
// XSS: every sentence is a React child (auto-escaped text node), never
// dangerouslySetInnerHTML. The corpus text is untrusted ingested content and
// `npm run guard:xss` fails the build if that ever changes.
//
// The highlight carries a non-colour cue (a left rule + the sentence number
// inheriting the highlight ink), matching HighlightedContent's rule that a
// mark is never colour alone.

import { Eyebrow } from "../explore/Eyebrow";
import { highlightedSentences, sentenceAnchor } from "../../lib/grading";
import type { GradingEvidenceSet, GradingDocument } from "../../api/client";

// `doc`, not `document`: the parameter would otherwise shadow the global of
// that name inside this component, which is the sort of thing that reads fine
// until someone adds a `document.getElementById` here.
export function DocumentPane({
  doc,
  claims,
}: {
  doc: GradingDocument;
  claims: GradingEvidenceSet[];
}) {
  const marked = highlightedSentences(claims);
  const sentenceCount = doc.units.reduce((a, u) => a + u.sentences.length, 0);

  return (
    <section className="rounded-card border border-line bg-white px-[22px] pb-[18px] pt-2">
      <Eyebrow className="mt-2">
        The document — read it to answer (b): is there evidence the labelers did not supply?
      </Eyebrow>
      <details open>
        <summary className="cursor-pointer py-[10px] text-sm font-medium text-link focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link">
          {doc.title || doc.doc_id} — {doc.units.length} units,{" "}
          {sentenceCount} sentences
        </summary>

        {doc.units.map((unit) => (
          <div key={unit.index}>
            <div className="mb-[6px] mt-[18px] flex items-baseline gap-2 text-[13px] font-semibold tracking-[.02em] text-muted">
              <span className="font-mono text-[11px] text-link">UNIT {unit.index}</span>
              {unit.title}
            </div>
            {unit.sentences.map((s) => {
              const hl = marked.has(`${unit.index}:${s.i}`);
              return (
                <div
                  key={s.i}
                  id={sentenceAnchor(unit.index, s.i)}
                  className={`grid max-w-[78ch] grid-cols-[44px_minmax(0,1fr)] gap-2 py-px text-[15.5px] leading-[1.6] target:outline target:outline-2 target:outline-offset-2 target:outline-link ${
                    hl
                      ? "rounded-[4px] border-l-2 border-l-accent bg-accent-line text-accent-text"
                      : "text-body"
                  }`}
                >
                  <span
                    className={`pt-[5px] text-right font-mono text-[11px] ${
                      hl ? "text-accent-text" : "text-faint"
                    }`}
                  >
                    {s.i}
                  </span>
                  <span>{s.text}</span>
                </div>
              );
            })}
          </div>
        ))}
      </details>
    </section>
  );
}
