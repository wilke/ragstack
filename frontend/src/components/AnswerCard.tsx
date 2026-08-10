// The synthesized answer as an editorial block: lead claim (first sentence)
// behind the yellow rule with a Verify-in-Evidence link, remaining sentences
// as secondary paragraphs, `[n]` markers as citation chips, then the
// rewritten-query chips and the feedback control. Shows the skeleton while the
// request is in flight. All content is untrusted → rendered as React text.

import { AnswerSkeleton } from "./AnswerSkeleton";
import { firstCited, segmentCitations, splitAnswer } from "../lib/claims";
import { lookupTerm } from "../lib/glossary";
import { Eyebrow } from "./explore/Eyebrow";
import { FeedbackControl } from "./FeedbackControl";
import { HelpTip } from "./HelpTip";

interface Props {
  query: string;
  answer?: string;
  rewrittenQueries?: string[];
  pending: boolean;
  // How many sources the response returned — markers outside 1..n stay text.
  sourceCount: number;
  onOpenEvidence: () => void;
}

// Inline text with `[n]` markers rendered as superscript citation chips. The
// yellow chip is scoped to the LEAD claim's first-cited source (per-claim
// grounding is a backend gap; "cited by this claim" reduces to "cited first in
// the lead" until it exists) — secondary-paragraph chips are always blue.
function CitedText({
  text,
  sourceCount,
  first,
}: {
  text: string;
  sourceCount: number;
  first: number | null;
}) {
  return (
    <>
      {segmentCitations(text, sourceCount).map((seg, i) =>
        "cite" in seg ? (
          <sup
            key={i}
            className={`ml-[3px] rounded-[4px] px-[5px] py-[2px] font-mono text-[11px] font-medium ${
              seg.cite === first ? "bg-accent text-ink-600" : "bg-linkSoft text-link"
            }`}
          >
            {seg.cite}
          </sup>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </>
  );
}

export function AnswerCard({
  query,
  answer,
  rewrittenQueries,
  pending,
  sourceCount,
  onOpenEvidence,
}: Props) {
  const parts = answer ? splitAnswer(answer) : { lead: "", rest: [] };
  // Computed from the lead only: the yellow treatment belongs to the lead claim.
  const first = answer ? firstCited(parts.lead, sourceCount) : null;

  return (
    <section aria-labelledby="answer-heading">
      <div className="mb-4 flex items-center gap-2">
        <Eyebrow id="answer-heading">Answer</Eyebrow>
        {/* One tip for the citation grammar, on the section heading rather than
            on every chip. */}
        <HelpTip icon side="bottom" term="citation">
          <span className="mb-1.5 block">{lookupTerm("citation")}</span>
          <span className="block">
            n is the source&rsquo;s rank in the Sources list below, so [2] is the second
            card. The yellow chip is the first source the lead claim cites — the sentence
            behind the yellow rule.
          </span>
        </HelpTip>
      </div>

      {pending ? (
        <AnswerSkeleton />
      ) : (
        <>
          <div className="mb-[18px] border-l-[3px] border-accent pl-4">
            <p className="mb-2.5 text-[20px] leading-[1.7] text-strong [text-wrap:pretty]">
              <CitedText text={parts.lead} sourceCount={sourceCount} first={first} />
            </p>
            <span className="flex items-center gap-2">
              <button
                type="button"
                onClick={onOpenEvidence}
                className="inline-flex items-center gap-[7px] text-[11.5px] font-medium text-link hover:underline"
              >
                Verify in Evidence{" "}
                <span aria-hidden="true" className="text-xs">
                  →
                </span>
              </button>
              {/* The one "where does Evidence take me" tip on this screen — the
                  per-source "Evidence →" links land in the same view. */}
              <HelpTip icon side="bottom" term="evidence">
                <span className="mb-1.5 block">{lookupTerm("evidence")}</span>
                <span className="block">
                  It also shows the retrieval legs this run was sent with. Claims are
                  ungraded there — the API returns no per-claim grounding — and each
                  source card&rsquo;s &ldquo;Evidence →&rdquo; opens the same view with
                  that source already selected.
                </span>
              </HelpTip>
            </span>
          </div>

          {parts.rest.map((p, i) => (
            <p key={i} className="mb-[22px] text-base leading-[1.75] text-body">
              <CitedText text={p} sourceCount={sourceCount} first={null} />
            </p>
          ))}

          {rewrittenQueries && rewrittenQueries.length > 1 && (
            <div className="mb-4 flex flex-wrap gap-1.5">
              {rewrittenQueries.map((q, i) => (
                <span
                  key={`${i}-${q}`}
                  className="rounded-chip bg-[#f2f1ed] px-2.5 py-1 font-mono text-[10.5px] text-[#6a6a64]"
                >
                  {q}
                </span>
              ))}
            </div>
          )}

          {answer && <FeedbackControl query={query} answer={answer} />}
        </>
      )}
    </section>
  );
}
