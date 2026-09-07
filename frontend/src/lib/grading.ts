// Pure logic for the Grading view — the reading guide's vocabulary, the
// verdict/span-judgement bookkeeping, and the export's file list. Everything
// here is a function of its arguments so it can be tested without a DOM (the
// screens themselves are covered by the renderToStaticMarkup smoke tests).
//
// The wording of the six verdicts, the "counts as" column and the need-type
// gloss are transcribed from the pilot sheet
// (docs/plans/results/stage0/artifacts/rdev-pilot-read/template.html), which is
// the design reference the readers were trained on. They must keep saying the
// same thing: a read begun on those sheets continues here, and a verdict means
// what the rubric says it means, not what a paraphrase says.

import type {
  GradingBatch,
  GradingEvidenceSet,
  GradingExportResponse,
  GradingExtraAnswer,
  GradingJudgement,
  GradingSpanJudgement,
  GradingTask,
  GradingVerdict,
  GradingVerdictPutRequest,
  GradingVerdictValue,
} from "../api/client";

/** How a verdict scores in SPEC §6.6.2's tally — the pilot sheet's fourth column. */
export type CountsAs = "label OK" | "label error" | "omission" | "neutral";

export interface VerdictEntry {
  value: GradingVerdictValue;
  meaning: string;
  example: string;
  countsAs: CountsAs;
}

/** The six verdicts, in the pilot sheet's order. */
export const VERDICTS: VerdictEntry[] = [
  {
    value: "correct",
    meaning:
      "every supplied span is correctly located and minimal, and nothing material was missed",
    example:
      "diagnosis case with calf tenderness and raised D-dimer; the span is the sentence giving ultrasonography's 97% sensitivity for DVT; nothing else in the paper adds to it",
    countsAs: "label OK",
  },
  {
    value: "wrong-location",
    meaning: "a supplied span is real text, but that place does not justify relevance to the need",
    example:
      "the span is an abstract sentence that restates the topic; the actual finding is in the results, and the span does not carry it",
    countsAs: "label error",
  },
  {
    value: "non-minimal",
    meaning: "a supplied set includes a sentence that could be removed without losing sufficiency",
    example:
      "the span is three sentences, but the first two are background and the third alone supports the decision",
    countsAs: "label error",
  },
  {
    value: "missed-evidence",
    meaning:
      "there is supporting evidence in the document that no labeler supplied — whether or not other spans were supplied",
    example:
      "the labelers marked the abstract, but the discussion reports the specific outcome the treatment need turns on",
    countsAs: "omission",
  },
  {
    value: "correctly-none",
    meaning:
      "the labelers supplied nothing, and you agree: the document is about the topic but no passage supports a decision for this need",
    example:
      "a review that discusses the condition in general and never states a finding a clinician could act on. This is a correct outcome, not an error.",
    countsAs: "label OK",
  },
  {
    value: "ambiguous",
    meaning:
      "you cannot decide whether two spans are one unit or two, or whether a span is sufficient on its own",
    example:
      "two adjacent sentences each half-support the decision; neither alone suffices and you cannot tell if they should be one set",
    countsAs: "neutral",
  },
];

/** The three per-span judgements, question (a), with the pilot sheet's labels. */
export const SPAN_JUDGEMENTS: { value: GradingJudgement; label: string }[] = [
  { value: "located", label: "located correctly" },
  { value: "wrong", label: "wrong location" },
  { value: "non-minimal", label: "not minimal" },
];

/**
 * The need type explained as WHAT THE CLINICIAN HAS TO DECIDE — never as a
 * synthesis of the document (grading-ui.md §3.4). Unknown types get no gloss
 * rather than a guessed one: the UI explains the need type, it never branches
 * on it, and inventing a sentence for a type the study added later would put
 * words in the protocol's mouth.
 */
const NEED_GLOSS: Record<string, string> = {
  diagnosis: "what is the most likely diagnosis for this patient?",
  test: "what test or investigation should be ordered next?",
  treatment: "what treatment should this patient receive?",
  pointed: "the pointed question below, asked of this document.",
};

export function needGloss(type: string): string {
  return NEED_GLOSS[type] ?? "";
}

/** The pilot sheet's `<set>.<span>` key, kept only as a local map key. */
export function spanKey(set: number, span: number): string {
  return `${set}.${span}`;
}

/** A saved row's per-span judgements as the map the toggles are driven from. */
export function judgementMap(verdict: GradingVerdict | null | undefined): Record<
  string,
  GradingJudgement
> {
  const map: Record<string, GradingJudgement> = {};
  for (const j of verdict?.span_judgements ?? []) map[spanKey(j.set, j.span)] = j.judgement;
  return map;
}

/**
 * The toggle map back into the wire's array form, ordered by (set, span) so a
 * re-save of an unchanged read produces an identical body. Keys that do not
 * parse as `<set>.<span>` are dropped rather than sent — the server 422s on a
 * span the task does not have, and a malformed key is a UI bug, not a verdict.
 */
export function judgementList(map: Record<string, GradingJudgement>): GradingSpanJudgement[] {
  const out: GradingSpanJudgement[] = [];
  for (const [key, judgement] of Object.entries(map)) {
    const [rawSet, rawSpan] = key.split(".");
    const set = Number(rawSet);
    const span = Number(rawSpan);
    if (!Number.isInteger(set) || !Number.isInteger(span) || set < 1 || span < 1) continue;
    out.push({ set, span, judgement });
  }
  out.sort((a, b) => a.set - b.set || a.span - b.span);
  return out;
}

/** The extra-question answers as the wire's array, dropping the unanswered. */
export function extraAnswerList(map: Record<string, string>): GradingExtraAnswer[] {
  return Object.entries(map)
    .filter(([, answer]) => answer !== "")
    .map(([id, answer]) => ({ id, answer }));
}

/**
 * The PUT body. A whole-row replace: everything the reader has on screen goes
 * in every time, because an omitted list CLEARS the stored one rather than
 * keeping it (GradingVerdictPutRequest).
 */
export function buildVerdictBody(input: {
  verdict: GradingVerdictValue;
  judgements: Record<string, GradingJudgement>;
  extraAnswers: Record<string, string>;
  notes: string;
}): GradingVerdictPutRequest {
  return {
    verdict: input.verdict,
    span_judgements: judgementList(input.judgements),
    extra_answers: extraAnswerList(input.extraAnswers),
    notes: input.notes.trim(),
  };
}

/** `unit:sentence` keys for every sentence any claimed span covers. */
export function highlightedSentences(claims: GradingEvidenceSet[]): Set<string> {
  const keys = new Set<string>();
  for (const set of claims) {
    for (const span of set.spans) {
      for (let i = span.first_sentence; i <= span.last_sentence; i++) {
        keys.add(`${span.unit}:${i}`);
      }
    }
  }
  return keys;
}

/** The anchor id a span's jump link points at. */
export function sentenceAnchor(unit: number, sentence: number): string {
  return `u${unit}s${sentence}`;
}

/**
 * Where to resume: the first task with no verdict of the caller's own, in the
 * SERVER'S order — index 0 when everything is read, so a finished reader lands
 * on the read's first pair rather than nowhere.
 */
export function firstUnreadIndex(tasks: GradingTask[]): number {
  const i = tasks.findIndex((t) => t.verdict === null);
  return i === -1 ? 0 : i;
}

/**
 * Save-and-advance: the next unread task AFTER `from`, wrapping once to catch
 * the ones skipped earlier, or null when the read is complete. Never re-orders
 * — it only scans the order the server gave.
 */
export function nextUnreadIndex(tasks: GradingTask[], from: number): number | null {
  for (let i = from + 1; i < tasks.length; i++) if (tasks[i].verdict === null) return i;
  for (let i = 0; i <= from && i < tasks.length; i++) if (tasks[i].verdict === null) return i;
  return null;
}

/**
 * What a failed save says. The 409 is the one that needs its own sentence: it
 * is not a permission problem and not a bad body — the batch left `open` while
 * the reader had the pair on screen, and their row is now frozen.
 */
export function saveErrorMessage(status: number | null): string {
  if (status === 409) return "This read is closed for adjudication — verdicts can no longer change.";
  if (status === 403) return "You are not one of this read's readers, so you have no row to save.";
  if (status === 404) return "This task is no longer available on this server.";
  if (status === 422) return "The server rejected the verdict as invalid. Reload the pair and retry.";
  if (status === 401) return "Check your API key — the server did not accept the credential.";
  if (status === 503) return "The grading store is unavailable; nothing was saved.";
  if (status === null) return "Could not reach the API — nothing was saved.";
  return `Could not save the verdict (error ${status}).`;
}

/** The same, for the adjudicator's write. */
export function adjudicationErrorMessage(status: number | null): string {
  if (status === 409) return "This batch is still open — freeze the readers' rows first.";
  if (status === 403) return "Only an admin can record the joint-read verdict.";
  return saveErrorMessage(status);
}

export interface ExportFile {
  filename: string;
  content: string;
  mime: string;
}

/**
 * The files an Export downloads: every CSV the envelope carries, in the order
 * it carries them (readers in label order, then `rdev_verdicts_ADJ.csv`), plus
 * one JSON with the per-span judgements and extra answers the CSV columns
 * cannot hold. The CSV names come from the SERVER — `s0_rdev_score.py --a/--b`
 * reads them by name, so the client must not invent or normalise them.
 */
export function exportFiles(envelope: GradingExportResponse): ExportFile[] {
  const files: ExportFile[] = envelope.csv.map((c) => ({
    filename: c.filename,
    content: c.content,
    mime: "text/csv;charset=utf-8",
  }));
  files.push({
    filename: `grading_${envelope.batch_id}_verdicts.json`,
    content: JSON.stringify(
      {
        batch_id: envelope.batch_id,
        name: envelope.name,
        kind: envelope.kind,
        status: envelope.status,
        rubric_sha256: envelope.rubric_sha256,
        order_seed: envelope.order_seed,
        exported_at: envelope.exported_at,
        readers: envelope.readers,
        verdicts: envelope.verdicts,
        adjudications: envelope.adjudications,
      },
      null,
      2,
    ),
    mime: "application/json;charset=utf-8",
  });
  return files;
}

/**
 * Tasks on which the readers who HAVE saved a row do not all agree. Counted
 * only over rows the adjudicator can actually see (`reader_verdicts`, admin +
 * not-`open`); a task where only one reader saved is not a disagreement, it is
 * an incomplete read.
 */
export function disagreementCount(tasks: GradingTask[]): number {
  return tasks.filter((t) => {
    const rows = t.reader_verdicts ?? [];
    return rows.length > 1 && new Set(rows.map((r) => r.verdict)).size > 1;
  }).length;
}

/** The caller's letter in this batch (`A`, `B`, …), or null when not a reader. */
export function readerLabel(batch: GradingBatch, subject: string | null): string | null {
  if (!subject) return null;
  const entry = batch.progress.find((p) => p.reader === subject);
  if (entry) return entry.label;
  const i = batch.readers.indexOf(subject);
  return i === -1 ? null : String.fromCharCode(65 + i);
}

/** `a1b2c3d4…` — enough of the rubric hash to compare by eye, per §5. */
export function rubricShort(sha256: string): string {
  return sha256.slice(0, 12);
}
