// The synthesized answer as an editorial block: the WHOLE answer behind the
// yellow rule with a Verify-in-Evidence link, `[n]` markers as citation chips,
// then the
// rewritten-query chips and the feedback control. Shows the skeleton while the
// request is in flight. All content is untrusted → rendered as React text.

import { AnswerSkeleton } from "./AnswerSkeleton";
import { answerParagraphs, firstCited, segmentCitations } from "../lib/claims";
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
// yellow chip is the answer's first-cited source (per-claim grounding is a
// backend gap, so "cited by this claim" reduces to "cited first"); every other
// marker is blue.
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
  const paragraphs = answer ? answerParagraphs(answer) : [];
  // Over the whole answer now that it is one block — there is no "lead" to scope to.
  const first = answer ? firstCited(answer, sourceCount) : null;

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
            card. The yellow chip is the first source the answer cites.
          </span>
        </HelpTip>
      </div>

      {pending ? (
        <AnswerSkeleton />
      ) : (
        <>
          {/* ONE block: the yellow rule spans the entire answer. It used to bound
              only a "lead sentence", which a regex cut at the first period —
              splitting "E. coli" and spilling the remainder outside the rule. */}
          <div className="mb-[18px] border-l-[3px] border-accent pl-4">
            {paragraphs.map((para, i) => (
              <p
                key={i}
                className={
                  i === 0
                    ? "mb-2.5 text-[17px] leading-[1.7] text-strong [text-wrap:pretty]"
                    : "mb-2.5 text-[15px] leading-[1.75] text-body [text-wrap:pretty]"
                }
              >
                <CitedText text={para} sourceCount={sourceCount} first={first} />
              </p>
            ))}
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
