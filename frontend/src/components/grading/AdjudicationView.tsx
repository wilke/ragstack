// The adjudicator's screen (admin, batch not `open`): both readers' frozen rows
// side by side per task, and the joint-read verdict — the verdict the study
// actually uses (SPEC §6.6.3) — recorded against each.
//
// This is the ONLY screen in the app that shows more than one reader's row, and
// it can only ever render what the server chose to send: `reader_verdicts`
// appears on a task solely for an admin, solely once POST …/adjudicate has
// frozen the read. The component does not fetch anyone's row; it renders the
// field or, when absent, says nothing is visible yet.
//
// The readers' own rows are never modified here. The pre-adjudication κ is the
// statistic the study reports and it needs the originals.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, putGradingAdjudication } from "../../api/client";
import type {
  GradingAdjudication,
  GradingAdjudicationPutRequest,
  GradingBatch,
  GradingTask,
  GradingVerdict,
  GradingVerdictValue,
} from "../../api/client";
import {
  adjudicationErrorMessage,
  disagreementCount,
  judgementList,
  judgementMap,
  VERDICTS,
} from "../../lib/grading";

function ReaderColumn({
  label,
  subject,
  verdict,
}: {
  label: string;
  subject: string;
  verdict: GradingVerdict | undefined;
}) {
  return (
    <div className="min-w-0 flex-1 rounded-panel border border-line bg-paper px-3 py-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-[11px] font-semibold text-link">Reader {label}</span>
        <span className="truncate font-mono text-[10.5px] text-faint">{subject}</span>
      </div>
      {verdict ? (
        <>
          <p className="mt-1 font-mono text-[13px] text-strong">{verdict.verdict}</p>
          {verdict.span_judgements.length > 0 ? (
            <p className="mt-1 font-mono text-[11px] text-dim">
              {verdict.span_judgements.map((j) => `${j.set}.${j.span}:${j.judgement}`).join("  ")}
            </p>
          ) : null}
          {verdict.notes ? <p className="mt-1 text-xs text-body">{verdict.notes}</p> : null}
          {verdict.extra_answers.length > 0 ? (
            <p className="mt-1 text-[11px] text-dim">
              {verdict.extra_answers.map((a) => `${a.id}: ${a.answer}`).join("; ")}
            </p>
          ) : null}
        </>
      ) : (
        <p className="mt-1 text-xs text-faint">No row saved.</p>
      )}
    </div>
  );
}

function AdjudicationRow({
  batch,
  task,
  apiKey,
  onSaved,
}: {
  batch: GradingBatch;
  task: GradingTask;
  apiKey: string;
  onSaved: () => void;
}) {
  const existing = task.adjudication ?? null;
  const [verdict, setVerdict] = useState<GradingVerdictValue | null>(existing?.verdict ?? null);
  const [notes, setNotes] = useState(existing?.notes ?? "");
  const [status, setStatus] = useState("");

  const rows = task.reader_verdicts ?? [];
  const disagrees = rows.length > 1 && new Set(rows.map((r) => r.verdict)).size > 1;

  const save = useMutation<GradingAdjudication, Error, GradingAdjudicationPutRequest>({
    mutationFn: (body) => putGradingAdjudication(task.id, body, apiKey || undefined),
    onSuccess: (row) => {
      setStatus(`Joint verdict recorded for ${task.pair_id} (version ${row.version}).`);
      onSaved();
    },
  });

  const failure = save.error
    ? adjudicationErrorMessage(save.error instanceof ApiError ? save.error.status : null)
    : null;

  return (
    <li className="rounded-card border border-line bg-white px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-mono text-[13px] text-strong">{task.pair_id}</span>
        {disagrees ? (
          <span className="rounded-pill bg-rustSoft px-2 py-px text-[11px] font-medium text-rust">
            readers disagree
          </span>
        ) : null}
      </div>

      <div className="mt-2 flex flex-wrap gap-2">
        {batch.readers.map((subject, i) => (
          <ReaderColumn
            key={subject}
            label={String.fromCharCode(65 + i)}
            subject={subject}
            verdict={rows.find((r) => r.reader === subject)}
          />
        ))}
      </div>

      <div
        role="radiogroup"
        aria-label={`Joint verdict for ${task.pair_id}`}
        className="mt-3 flex flex-wrap gap-[6px]"
      >
        {VERDICTS.map((v) => (
          <label
            key={v.value}
            title={`${v.meaning} — counts as ${v.countsAs}`}
            className={`inline-flex cursor-pointer items-center gap-[6px] rounded-pill border px-[10px] py-[5px] text-[12.5px] focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-link ${
              verdict === v.value
                ? "border-ink-900 bg-ink-900 text-white"
                : "border-line text-body hover:bg-paper"
            }`}
          >
            <input
              type="radio"
              name={`adj-${task.id}`}
              value={v.value}
              checked={verdict === v.value}
              onChange={() => setVerdict(v.value)}
              className="m-0"
            />
            <span className="font-mono">{v.value}</span>
          </label>
        ))}
      </div>

      <div className="mt-2 flex flex-wrap items-start gap-2">
        <label className="min-w-[220px] flex-1">
          <span className="sr-only">How the disagreement was resolved, for {task.pair_id}</span>
          <input
            type="text"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="How the disagreement was resolved (exported as the ADJ notes column)"
            className="w-full rounded-panel border border-line px-2 py-[6px] text-[13px] text-body focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link"
          />
        </label>
        <button
          type="button"
          disabled={!verdict || save.isPending}
          onClick={() => {
            if (!verdict) return;
            setStatus("");
            // The adjudicated per-span judgements default to the readers'
            // agreement where they HAVE one: identical maps collapse to
            // themselves, so an adjudicator who only changes the pair-level
            // verdict does not silently blank question (a).
            const agreed =
              rows.length > 0 &&
              rows.every(
                (r) =>
                  JSON.stringify(judgementList(judgementMap(r))) ===
                  JSON.stringify(judgementList(judgementMap(rows[0]))),
              )
                ? judgementList(judgementMap(rows[0]))
                : (existing?.span_judgements ?? []);
            save.mutate({ verdict, span_judgements: agreed, notes: notes.trim() });
          }}
          className="rounded-panel border border-ink-900 bg-ink-900 px-3 py-[6px] text-[13px] font-medium text-white disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link"
        >
          {save.isPending ? "Saving…" : "Save joint verdict"}
        </button>
      </div>

      {failure ? <p className="mt-1 text-xs text-rust">{failure}</p> : null}
      <p aria-live="polite" className="min-h-[1.1em] text-xs text-dim">
        {status ||
          (existing
            ? `Joint verdict on record: ${existing.verdict} (version ${existing.version}).`
            : "")}
      </p>
    </li>
  );
}

export function AdjudicationView({
  batch,
  tasks,
  apiKey,
  onExport,
  exporting,
  exportStatus,
}: {
  batch: GradingBatch;
  // In the SERVER'S order, exactly as `GET …/tasks` returned it.
  tasks: GradingTask[];
  apiKey: string;
  onExport: () => void;
  exporting: boolean;
  exportStatus: string;
}) {
  const queryClient = useQueryClient();
  const visible = tasks.some((t) => (t.reader_verdicts?.length ?? 0) > 0);

  return (
    <section>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-semibold text-ink-900">Adjudication</h2>
          <p className="text-[13px] text-dim">
            {disagreementCount(tasks)} of {tasks.length} pairs where the readers disagree. The
            readers&apos; rows are frozen; the joint verdict is what the study uses.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onExport}
            disabled={exporting}
            className="rounded-panel border border-line bg-white px-3 py-2 text-sm font-medium text-body hover:bg-paper disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link"
          >
            {exporting ? "Exporting…" : "Export CSVs"}
          </button>
          <span aria-live="polite" className="text-xs text-dim">
            {exportStatus}
          </span>
        </div>
      </div>

      {!visible ? (
        <p className="rounded-card border border-line bg-paper px-4 py-3 text-sm text-dim">
          No reader rows are visible on these tasks. Either no reader saved one, or this server
          did not send them — they travel only to an admin, and only once the batch has left{" "}
          <span className="font-mono">open</span>.
        </p>
      ) : null}

      <ul className="flex list-none flex-col gap-3 p-0">
        {tasks.map((t) => (
          <AdjudicationRow
            key={t.id}
            batch={batch}
            task={t}
            apiKey={apiKey}
            onSaved={() => {
              void queryClient.invalidateQueries({ queryKey: ["grading-tasks", batch.id] });
            }}
          />
        ))}
      </ul>
    </section>
  );
}
