// One pair, laid out as the pilot sheet lays it out
// (docs/plans/results/stage0/artifacts/rdev-pilot-read/template.html): the
// reading guide, then three LABELED sections —
//
//   The question       the case + the need type, explained as what the clinician
//                      has to DECIDE; summary and description named as two forms
//                      of one question, not a question and a reason.
//   The claimed answer per-span source tags, per-span judgement toggles for
//                      question (a), and a jump link into the document.
//   The document       numbered sentences with the claimed spans highlighted
//                      (DocumentPane).
//
// The verdict bar is NOT here: it is fixed to the viewport in GradingView, so
// the six chips stay reachable however far the reader has scrolled.

import { Eyebrow } from "../explore/Eyebrow";
import { DocumentPane } from "./DocumentPane";
import { ReadingGuide } from "./ReadingGuide";
import { needGloss, sentenceAnchor, spanKey, SPAN_JUDGEMENTS } from "../../lib/grading";
import type { GradingJudgement, GradingTask } from "../../api/client";

function SetTag({ sources }: { sources: string[] }) {
  return (
    <span className="rounded-pill bg-linkSoft px-2 py-px font-sans text-[11px] font-medium text-link">
      {sources.join(" + ")}
    </span>
  );
}

export function PairView({
  task,
  index,
  total,
  judgements,
  onJudgement,
  extraAnswers,
  onExtraAnswer,
  disabled = false,
}: {
  task: GradingTask;
  index: number; // 0-based position in the caller's own order
  total: number;
  judgements: Record<string, GradingJudgement>;
  onJudgement: (key: string, value: GradingJudgement) => void;
  extraAnswers: Record<string, string>;
  onExtraAnswer: (id: string, value: string) => void;
  // `adjudicating`/`closed`: the row is frozen server-side, so the inputs are
  // read-only rather than lying about what a click would do.
  disabled?: boolean;
}) {
  const gloss = needGloss(task.question.type);
  const sentences = task.document.units.reduce((a, u) => a + u.sentences.length, 0);

  return (
    <div>
      <div className="mb-[14px] flex flex-wrap items-baseline justify-between gap-4">
        <h2 className="font-display text-[22px] font-semibold tracking-[-.01em] text-ink-900">
          Pair {index + 1} of {total}
        </h2>
        <div className="flex flex-wrap gap-[14px] text-[13px] text-dim">
          <span className="font-mono">{task.pair_id}</span>
          {task.stratum ? (
            <span className="rounded-pill bg-linkSoft px-2 py-px text-[11.5px] font-medium text-link">
              {task.stratum.replace(/_/g, " ")}
            </span>
          ) : null}
          <span>
            {task.document.units.length} units · {sentences} sentences
          </span>
        </div>
      </div>

      <ReadingGuide />

      {/* ---- The question -------------------------------------------------- */}
      <section className="mb-[18px] rounded-card border border-line border-l-[3px] border-l-link bg-white px-[18px] py-[14px]">
        <Eyebrow>The question — a patient case, and what the clinician needs</Eyebrow>
        <p className="mt-[6px] max-w-[72ch] text-sm text-body">
          <span className="font-semibold text-link">Need: {task.question.type}</span>
          {gloss ? ` — ${gloss}` : null}
        </p>
        <p className="mt-3 max-w-[72ch] text-sm text-body">
          <span className="mb-[2px] block font-mono text-[10.5px] font-medium uppercase tracking-[.11em] text-muted">
            Summary — the case in short; the query the system retrieves with
          </span>
          {task.question.summary}
        </p>
        <p className="mt-3 max-w-[72ch] text-sm text-dim">
          <span className="mb-[2px] block font-mono text-[10.5px] font-medium uppercase tracking-[.11em] text-muted">
            Description — the same case in full; a secondary query variant
          </span>
          {task.question.description}
        </p>
      </section>

      {/* ---- The claimed answer -------------------------------------------- */}
      {task.claims.length > 0 ? (
        <section className="mb-[18px]">
          <Eyebrow>
            The claimed answer — passages the labelers say support this document&apos;s relevance
            to the need
          </Eyebrow>
          <h3 className="mb-2 mt-[6px] text-[13px] font-semibold text-muted">
            {task.claims.length} evidence set{task.claims.length > 1 ? "s" : ""}. For each span,
            mark (a): is it correctly located and minimal?
          </h3>

          {task.claims.map((set) =>
            set.spans.map((span, spanIdx) => {
              const key = spanKey(set.set_index, spanIdx + 1);
              const anchor = sentenceAnchor(span.unit, span.first_sentence);
              return (
                <div
                  key={key}
                  className="mb-2 rounded-r-card border-l-[3px] border-l-accent bg-accent-soft px-[14px] py-[10px]"
                >
                  <div className="mb-1 flex flex-wrap items-center justify-between gap-[10px] font-mono text-[11px] text-accent-text">
                    <span>
                      set {set.set_index}
                      {set.spans.length > 1 ? ` · span ${spanIdx + 1} of ${set.spans.length}` : ""}{" "}
                      —{" "}
                      <a
                        href={`#${anchor}`}
                        className="underline underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link"
                      >
                        unit {span.unit}, sentences {span.first_sentence}–{span.last_sentence}
                      </a>
                    </span>
                    <SetTag sources={set.sources} />
                  </div>

                  <p className="text-[15.5px] leading-[1.55] text-strong">{span.text}</p>

                  <fieldset className="mt-2 flex flex-wrap items-center gap-[6px] border-0 p-0">
                    <legend className="sr-only">
                      Span {key}: is it correctly located and minimal?
                    </legend>
                    {SPAN_JUDGEMENTS.map((j) => (
                      <label
                        key={j.value}
                        className={`inline-flex cursor-pointer items-center gap-[5px] rounded-pill border px-[9px] py-[3px] text-[12.5px] focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-link ${
                          judgements[key] === j.value
                            ? "border-accent bg-accent text-ink-900"
                            : "border-[#d8c98a] text-accent-text"
                        }`}
                      >
                        <input
                          type="radio"
                          name={`span-${task.id}-${key}`}
                          value={j.value}
                          disabled={disabled}
                          checked={judgements[key] === j.value}
                          onChange={() => onJudgement(key, j.value)}
                          className="m-0"
                        />
                        {j.label}
                      </label>
                    ))}
                  </fieldset>
                </div>
              );
            }),
          )}
        </section>
      ) : (
        <section className="mb-[18px] rounded-card border border-dashed border-[#c9c8c3] bg-white px-4 py-3 text-sm text-dim">
          <Eyebrow>The claimed answer</Eyebrow>
          <p className="mt-[6px]">
            <b className="text-strong">No labeler supplied evidence for this pair.</b> Read the
            document and answer question (b): is there evidence here the labelers did not supply?
            If not, the verdict is <span className="font-mono">correctly-none</span>.
          </p>
        </section>
      )}

      {/* ---- Extra questions (a pointed read's guard 2, when authored) ------ */}
      {task.extra_questions.length > 0 ? (
        <section className="mb-[18px] rounded-card border border-line bg-white px-[18px] py-[14px]">
          <Eyebrow>One more question about this pair</Eyebrow>
          {task.extra_questions.map((q) => (
            <div key={q.id} className="mt-3">
              <p className="max-w-[72ch] text-sm text-body">{q.text}</p>
              {q.answer_type === "yes-no" ? (
                <fieldset className="mt-2 flex flex-wrap gap-[6px] border-0 p-0">
                  <legend className="sr-only">{q.text}</legend>
                  {["yes", "no"].map((a) => (
                    <label
                      key={a}
                      className={`inline-flex cursor-pointer items-center gap-[5px] rounded-pill border px-[11px] py-[4px] text-[13px] focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-link ${
                        extraAnswers[q.id] === a
                          ? "border-ink-900 bg-ink-900 text-white"
                          : "border-line text-body"
                      }`}
                    >
                      <input
                        type="radio"
                        name={`extra-${task.id}-${q.id}`}
                        value={a}
                        disabled={disabled}
                        checked={extraAnswers[q.id] === a}
                        onChange={() => onExtraAnswer(q.id, a)}
                        className="m-0"
                      />
                      {a}
                    </label>
                  ))}
                </fieldset>
              ) : (
                <label className="mt-2 block">
                  <span className="sr-only">{q.text}</span>
                  <input
                    type="text"
                    value={extraAnswers[q.id] ?? ""}
                    disabled={disabled}
                    onChange={(e) => onExtraAnswer(q.id, e.target.value)}
                    className="w-full rounded-panel border border-line px-2 py-[6px] text-sm text-body focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link"
                  />
                </label>
              )}
            </div>
          ))}
        </section>
      ) : null}

      {/* ---- The document -------------------------------------------------- */}
      <DocumentPane doc={task.document} claims={task.claims} />
    </div>
  );
}
