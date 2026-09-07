// The pilot sheet's collapsible reading guide: how to read a pair, and what the
// six verdicts mean — meaning, example and "counts as", one row each.
//
// It is the rubric as the readers were trained on it, so the wording lives in
// lib/grading.ts (VERDICTS) beside the vocabulary itself and is rendered, never
// re-phrased here. Open by default on the first pair a reader sees is
// deliberately NOT the behaviour: the sheet ships it collapsed, and a reader
// scrolling past a wall of rubric on every pair stops reading it.

import { VERDICTS, type CountsAs } from "../../lib/grading";

const COUNTS_TONE: Record<CountsAs, string> = {
  "label OK": "bg-mossSoft text-moss",
  "label error": "bg-rustSoft text-rust",
  omission: "bg-accent-soft text-accent-text",
  neutral: "bg-paper text-muted",
};

export function ReadingGuide() {
  return (
    <details className="mb-[18px] rounded-card border border-line border-l-[3px] border-l-link bg-white px-[18px] py-3">
      <summary className="cursor-pointer py-[2px] text-sm font-semibold text-link focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link">
        How to read a pair, and what the six verdicts mean
      </summary>

      <p className="my-2 max-w-[76ch] text-sm text-body">
        <b className="text-strong">The question</b> is a real clinical case with a{" "}
        <em>need type</em>: diagnosis, test, or treatment. The need type is part of the question,
        not a synthesis from the document — it says what kind of decision the clinician has to
        make. Every case comes in two forms: a <b className="text-strong">summary</b> (short; the
        query the system retrieves with) and a <b className="text-strong">description</b> (the same
        patient in full; a secondary query variant). They are the same question, not a question and
        a reason.
      </p>
      <p className="my-2 max-w-[76ch] text-sm text-body">
        <b className="text-strong">The claimed answer</b> is not a generated answer. The system
        under test is retrieval: can it bring back the passage that supports a decision for this
        case? Each labeler marked the passages it believes make this document relevant to the need.
        You see the union, each span tagged with who found it. A span is a run of whole sentences
        inside one section.
      </p>
      <p className="my-2 max-w-[76ch] text-sm text-body">
        <b className="text-strong">Your job</b> is two questions.{" "}
        <b className="text-strong">(a)</b> For each supplied span: is it correctly located (that
        text really supports the decision) and minimal (no sentence could be dropped)?{" "}
        <b className="text-strong">(b)</b> Reading the whole document: is there supporting evidence
        the labelers did <em>not</em> supply? Then record one verdict for the pair.
      </p>

      <div className="overflow-x-auto">
        <table className="my-2 w-full border-collapse text-[13px]">
          <thead>
            <tr>
              {["verdict", "meaning", "example", "counts as"].map((h) => (
                <th
                  key={h}
                  scope="col"
                  className="border-b border-line px-2 py-[6px] text-left text-[11.5px] font-medium tracking-[0.03em] text-muted"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {VERDICTS.map((v) => (
              <tr key={v.value}>
                <td className="whitespace-nowrap border-b border-line px-2 py-[6px] align-top font-mono text-xs text-strong">
                  {v.value}
                </td>
                <td className="border-b border-line px-2 py-[6px] align-top text-body">
                  {v.meaning}
                </td>
                <td className="border-b border-line px-2 py-[6px] align-top text-dim">
                  {v.example}
                </td>
                <td className="border-b border-line px-2 py-[6px] align-top">
                  <span
                    className={`inline-block whitespace-nowrap rounded-pill px-[7px] py-px text-[11px] font-medium ${COUNTS_TONE[v.countsAs]}`}
                  >
                    {v.countsAs}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="mt-2 text-xs text-muted">
        If a pair has both a wrong span and a missed one, record the verdict for the more serious
        problem and describe the other in the notes. The per-span toggles above each span capture
        (a) separately, so nothing is lost.
      </p>
    </details>
  );
}
