// GRADING — the study's two-independent-reader evidence read, run through
// RAGStack instead of through a claude.ai artifact where independence was
// honour-based (docs/plans/grading-ui.md, phase 3).
//
// Three screens in one view:
//
//   Batch list    the reads the caller may see, with progress. `GET
//                 /v1/grading/batches` is also the "does this server implement
//                 grading" probe: an older server answers 404, which App reads
//                 as "no Grading tab", never as an error.
//   Pair view     the pilot sheet's layout (PairView) plus the fixed verdict
//                 bar. One PUT, the caller's own row, save-and-advance.
//   Adjudication  admin, once the batch has left `open`: both readers' frozen
//                 rows side by side and the joint verdict, plus Export.
//
// TWO RULES THIS FILE KEEPS.
//
// 1. THE ORDER IS THE SERVER'S. `GET …/batches/{id}/tasks` returns the caller's
//    own seeded permutation — the same one `s0_rdev.py` built the paper
//    readsheets with — so `tasks` is rendered, indexed and advanced through
//    exactly as it arrived. Nothing here sorts, filters or reverses it.
// 2. ONLY THE CALLER'S OWN VERDICT IS EVER DISPLAYED to a reader. The reader
//    path renders `task.verdict` and nothing else; `GET …/verdicts/{reader}` is
//    never called for anybody (there is no client helper for it). The
//    adjudication path renders `reader_verdicts` only, which the server sends
//    solely to an admin and solely once the read is frozen.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  ApiError,
  adjudicateGradingBatch,
  exportGradingBatch,
  listGradingBatches,
  listGradingTasks,
  putGradingVerdict,
  type GradingBatch,
  type GradingJudgement,
  type GradingTask,
  type GradingTasksResponse,
  type GradingVerdict,
  type GradingVerdictPutRequest,
  type GradingVerdictValue,
} from "../api/client";
import {
  buildVerdictBody,
  exportFiles,
  firstUnreadIndex,
  judgementMap,
  nextUnreadIndex,
  readerLabel,
  rubricShort,
  saveErrorMessage,
  type ExportFile,
} from "../lib/grading";
import { AdjudicationView } from "./grading/AdjudicationView";
import { PairView } from "./grading/PairView";
import { TaskRail } from "./grading/TaskRail";
import { VerdictBar } from "./grading/VerdictBar";
import { ErrorBanner } from "./states/ErrorBanner";

/**
 * Hand each export file to the browser as a download. A Blob + a synthetic
 * anchor is the whole mechanism — the export endpoint returns CSV *text* in a
 * JSON envelope (the contract has no download convention), so the file is made
 * here rather than fetched as one.
 */
function downloadFiles(files: ExportFile[]) {
  for (const f of files) {
    const url = URL.createObjectURL(new Blob([f.content], { type: f.mime }));
    const a = document.createElement("a");
    a.href = url;
    a.download = f.filename;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }
}

function statusPill(status: GradingBatch["status"]) {
  const tone =
    status === "open"
      ? "bg-mossSoft text-moss"
      : status === "adjudicating"
        ? "bg-accent-soft text-accent-text"
        : "bg-paper text-muted";
  return (
    <span className={`rounded-pill px-2 py-px font-mono text-[11px] font-medium ${tone}`}>
      {status}
    </span>
  );
}

function BatchList({
  batches,
  onOpen,
}: {
  batches: GradingBatch[];
  onOpen: (id: string) => void;
}) {
  return (
    <section>
      <h1 className="font-display text-[22px] font-semibold tracking-[-.01em] text-ink-900">
        Reads
      </h1>
      <p className="mb-4 mt-1 max-w-[72ch] text-sm text-dim">
        Each read is a fixed rubric, a fixed reader list and a fixed per-reader order. You see the
        reads you are a reader on (an admin sees every read).
      </p>
      <ul className="flex list-none flex-col gap-2 p-0">
        {batches.map((b) => (
          <li key={b.id}>
            <button
              type="button"
              onClick={() => onOpen(b.id)}
              className="w-full rounded-card border border-line bg-white px-4 py-3 text-left hover:bg-paper focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-[15px] font-semibold text-ink-900">{b.name}</span>
                <span className="flex items-center gap-2 text-xs text-dim">
                  <span className="font-mono">{b.kind}</span>
                  {statusPill(b.status)}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap gap-4 text-xs text-dim">
                <span>{b.task_count} pairs</span>
                {b.progress.map((p) => (
                  <span key={p.reader}>
                    reader {p.label}: {p.saved} / {b.task_count}
                  </span>
                ))}
                <span className="font-mono" title={`rubric sha256 ${b.rubric_sha256}`}>
                  rubric {rubricShort(b.rubric_sha256)}…
                </span>
                <span className="font-mono">seed {b.order_seed}</span>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The draft for ONE pair. Keyed by task id at the call site, so moving to the
 * next pair remounts it and the draft state is initialised from that task's own
 * saved row rather than carried over — the bug where a reader's notes follow
 * them onto the next pair cannot happen by construction.
 */
function PairEditor({
  task,
  index,
  total,
  frozen,
  apiKey,
  batchId,
  status,
  onStatus,
  onSaved,
}: {
  task: GradingTask;
  index: number;
  total: number;
  frozen: boolean;
  apiKey: string;
  batchId: string;
  // The save confirmation lives in the PARENT. Saving advances to the next
  // pair, which remounts this component — so a status held here would be
  // cleared by the very action it is announcing, and the aria-live region
  // would stay silent for exactly the event it exists to speak.
  status: string;
  onStatus: (s: string) => void;
  onSaved: (savedIndex: number) => void;
}) {
  const queryClient = useQueryClient();
  const [verdict, setVerdict] = useState<GradingVerdictValue | null>(task.verdict?.verdict ?? null);
  const [notes, setNotes] = useState(task.verdict?.notes ?? "");
  const [judgements, setJudgements] = useState<Record<string, GradingJudgement>>(
    judgementMap(task.verdict),
  );
  const [extraAnswers, setExtraAnswers] = useState<Record<string, string>>(() => {
    const map: Record<string, string> = {};
    for (const a of task.verdict?.extra_answers ?? []) map[a.id] = a.answer;
    return map;
  });
  const [localError, setLocalError] = useState<string | null>(null);

  const key = ["grading-tasks", batchId, apiKey];

  const save = useMutation<
    GradingVerdict,
    Error,
    GradingVerdictPutRequest,
    { previous: GradingTasksResponse | undefined }
  >({
    mutationFn: (body) => putGradingVerdict(task.id, body, apiKey || undefined),
    // Optimistic: the rail's dot and the pair's "recorded" line flip before the
    // round trip, and roll back if the server refuses. The refetch in onSettled
    // is what the screen ends up trusting — the server assigns `version` and
    // `saved_at`, and this row is not the place to guess them.
    onMutate: async (body) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<GradingTasksResponse>(key);
      if (previous) {
        queryClient.setQueryData<GradingTasksResponse>(key, {
          ...previous,
          tasks: previous.tasks.map((t) =>
            t.id === task.id
              ? {
                  ...t,
                  verdict: {
                    task_id: t.id,
                    reader: previous.reader ?? "",
                    verdict: body.verdict,
                    span_judgements: body.span_judgements ?? [],
                    extra_answers: body.extra_answers ?? [],
                    notes: body.notes ?? "",
                    version: (t.verdict?.version ?? 0) + 1,
                    saved_at: t.verdict?.saved_at ?? "",
                  },
                }
              : t,
          ),
        });
      }
      return { previous };
    },
    onError: (_e, _body, ctx) => {
      if (ctx?.previous) queryClient.setQueryData(key, ctx.previous);
    },
    onSuccess: (row) => {
      onStatus(`Verdict "${row.verdict}" recorded for ${task.pair_id} (version ${row.version}).`);
      onSaved(index);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: key });
      void queryClient.invalidateQueries({ queryKey: ["grading-batches", apiKey] });
    },
  });

  const failure = save.error
    ? saveErrorMessage(save.error instanceof ApiError ? save.error.status : null)
    : null;

  return (
    <>
      <PairView
        task={task}
        index={index}
        total={total}
        judgements={judgements}
        onJudgement={(k, v) => setJudgements((prev) => ({ ...prev, [k]: v }))}
        extraAnswers={extraAnswers}
        onExtraAnswer={(id, v) => setExtraAnswers((prev) => ({ ...prev, [id]: v }))}
        disabled={frozen}
      />
      <VerdictBar
        taskId={task.id}
        value={verdict}
        onChange={(v) => {
          setVerdict(v);
          setLocalError(null);
        }}
        notes={notes}
        onNotes={setNotes}
        saving={save.isPending}
        status={status}
        error={localError ?? failure}
        disabled={frozen}
        savedAt={task.verdict?.saved_at ?? null}
        version={task.verdict?.version ?? null}
        isLast={index === total - 1}
        onSave={() => {
          if (!verdict) {
            setLocalError("Pick a verdict first — one of the six chips.");
            return;
          }
          onStatus("");
          setLocalError(null);
          save.mutate(buildVerdictBody({ verdict, judgements, extraAnswers, notes }));
        }}
      />
    </>
  );
}

function BatchWorkspace({
  batch,
  apiKey,
  isAdmin,
  onBack,
}: {
  batch: GradingBatch;
  apiKey: string;
  isAdmin: boolean;
  onBack: () => void;
}) {
  const queryClient = useQueryClient();
  const tasks = useQuery({
    queryKey: ["grading-tasks", batch.id, apiKey],
    queryFn: () => listGradingTasks(batch.id, apiKey || undefined),
    retry: false,
  });

  const [current, setCurrent] = useState<number | null>(null);
  const [mode, setMode] = useState<"read" | "adjudicate" | null>(null);
  const [exportStatus, setExportStatus] = useState("");
  // Survives the save-and-advance remount (see PairEditor).
  const [saveStatus, setSaveStatus] = useState("");
  const [freezeAsked, setFreezeAsked] = useState(false);

  const list = tasks.data?.tasks ?? [];
  const reader = tasks.data?.reader ?? null;
  const label = readerLabel(batch, reader);
  const frozen = batch.status !== "open";
  const canAdjudicate = isAdmin && frozen;
  // Resume where the reader left off — the first pair with no row of their own,
  // in the server's order. Resolved once the tasks arrive, then owned by the
  // rail; `current` stays null until then so the resume point is not computed
  // from an empty list.
  const index = current ?? firstUnreadIndex(list);
  // An admin who is not a reader has nothing to read: no row to write, and the
  // tasks arrive with `verdict: null` on every one of them.
  const view = mode ?? (reader === null && canAdjudicate ? "adjudicate" : "read");

  const exporting = useMutation({
    mutationFn: () => exportGradingBatch(batch.id, apiKey || undefined),
    onSuccess: (envelope) => {
      const files = exportFiles(envelope);
      downloadFiles(files);
      setExportStatus(`Downloaded ${files.map((f) => f.filename).join(", ")}.`);
    },
    onError: (e) => {
      const status = e instanceof ApiError ? e.status : null;
      setExportStatus(
        status === 409
          ? "The read is still open — freeze it for adjudication before exporting."
          : status === 403
            ? "Only an admin can export a read."
            : "The export failed.",
      );
    },
  });

  const freeze = useMutation({
    mutationFn: () => adjudicateGradingBatch(batch.id, apiKey || undefined),
    onSettled: () => {
      setFreezeAsked(false);
      void queryClient.invalidateQueries({ queryKey: ["grading-batches", apiKey] });
      void queryClient.invalidateQueries({ queryKey: ["grading-tasks", batch.id, apiKey] });
    },
  });

  return (
    <section>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <button
            type="button"
            onClick={onBack}
            className="text-xs text-link underline underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link"
          >
            ← All reads
          </button>
          <h1 className="font-display text-[22px] font-semibold tracking-[-.01em] text-ink-900">
            {batch.name}
          </h1>
          <p className="flex flex-wrap items-center gap-3 text-xs text-dim">
            {statusPill(batch.status)}
            <span className="font-mono">{batch.kind}</span>
            <span className="font-mono" title={`rubric sha256 ${batch.rubric_sha256}`}>
              rubric {rubricShort(batch.rubric_sha256)}…
            </span>
            <span className="font-mono">seed {batch.order_seed}</span>
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {canAdjudicate && reader !== null ? (
            <div className="flex gap-1 rounded-panel border border-line p-[2px]">
              {(["read", "adjudicate"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  aria-pressed={view === m}
                  className={`rounded-[3px] px-3 py-1 text-xs font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link ${
                    view === m ? "bg-ink-900 text-white" : "text-body hover:bg-paper"
                  }`}
                >
                  {m === "read" ? "My read" : "Adjudication"}
                </button>
              ))}
            </div>
          ) : null}

          {isAdmin && batch.status === "open" ? (
            freezeAsked ? (
              <span className="flex items-center gap-2 text-xs text-body">
                Freeze both readers&apos; rows? This ends independence and cannot be undone.
                <button
                  type="button"
                  onClick={() => freeze.mutate()}
                  disabled={freeze.isPending}
                  className="rounded-panel border border-rust bg-white px-2 py-1 font-medium text-rust disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link"
                >
                  {freeze.isPending ? "Freezing…" : "Freeze"}
                </button>
                <button
                  type="button"
                  onClick={() => setFreezeAsked(false)}
                  className="underline underline-offset-2"
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setFreezeAsked(true)}
                className="rounded-panel border border-line bg-white px-3 py-2 text-sm font-medium text-body hover:bg-paper focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link"
              >
                Freeze for adjudication
              </button>
            )
          ) : null}
        </div>
      </div>

      {freeze.error ? (
        <p role="alert" className="mb-3 rounded-card bg-rustSoft px-4 py-2 text-sm text-rust">
          {freeze.error instanceof ApiError && freeze.error.status === 409
            ? "This read has already left `open` — reload to see its current status."
            : freeze.error instanceof ApiError && freeze.error.status === 403
              ? "Only an admin can freeze a read for adjudication."
              : "The server refused to freeze this read."}
        </p>
      ) : null}

      {tasks.isError ? (
        tasks.error instanceof ApiError && tasks.error.status === 404 ? (
          <p role="alert" className="rounded-card bg-rustSoft px-4 py-2 text-sm text-rust">
            This read is not available to you on this server.
          </p>
        ) : (
          <ErrorBanner error={tasks.error} onRetry={() => void tasks.refetch()} />
        )
      ) : tasks.isLoading ? (
        <p className="text-sm text-dim">Loading the read…</p>
      ) : list.length === 0 ? (
        <p className="text-sm text-dim">This read has no pairs.</p>
      ) : view === "adjudicate" ? (
        <AdjudicationView
          batch={batch}
          tasks={list}
          apiKey={apiKey}
          onExport={() => exporting.mutate()}
          exporting={exporting.isPending}
          exportStatus={exportStatus}
        />
      ) : (
        <div className="flex gap-8 pb-[190px]">
          <TaskRail
            batch={batch}
            tasks={list}
            current={index}
            onSelect={(i) => {
              // A deliberate jump is a new context: the previous pair's
              // confirmation must not read as this one's.
              setSaveStatus("");
              setCurrent(i);
            }}
            label={label}
          />
          <div className="min-w-0 flex-1">
            {frozen ? (
              <p className="mb-3 rounded-card border border-line bg-accent-soft px-4 py-2 text-sm text-accent-text">
                This read is closed for adjudication — verdicts can no longer change.
              </p>
            ) : null}
            {reader === null ? (
              <p className="mb-3 rounded-card border border-line bg-paper px-4 py-2 text-sm text-dim">
                You are not a reader on this read, so there is no verdict of yours to record.
              </p>
            ) : null}
            <PairEditor
              key={list[index].id}
              task={list[index]}
              index={index}
              total={list.length}
              frozen={frozen || reader === null}
              apiKey={apiKey}
              batchId={batch.id}
              status={saveStatus}
              onStatus={setSaveStatus}
              onSaved={(savedIndex) => {
                const next = nextUnreadIndex(list, savedIndex);
                if (next !== null) setCurrent(next);
              }}
            />
          </div>
        </div>
      )}
    </section>
  );
}

export function GradingView({ apiKey, isAdmin }: { apiKey: string; isAdmin: boolean }) {
  const [batchId, setBatchId] = useState<string | null>(null);
  const batches = useQuery({
    queryKey: ["grading-batches", apiKey],
    queryFn: () => listGradingBatches(apiKey || undefined),
    retry: false,
  });

  const list = batches.data?.batches ?? [];
  const batch = list.find((b) => b.id === batchId) ?? null;

  if (batches.isLoading) return <p className="text-sm text-dim">Loading reads…</p>;
  // A 404 here is an older server with no grading surface at all — the same
  // probe App uses to decide whether the tab exists. Anything else is a real
  // failure and gets the shared banner (status-mapped copy, `Reference:` id).
  if (batches.isError)
    return batches.error instanceof ApiError && batches.error.status === 404 ? (
      <p className="text-sm text-dim">This server does not implement grading.</p>
    ) : (
      <ErrorBanner error={batches.error} onRetry={() => void batches.refetch()} />
    );
  if (list.length === 0)
    return <p className="text-sm text-dim">You are not a reader on any read.</p>;

  return batch ? (
    <BatchWorkspace
      batch={batch}
      apiKey={apiKey}
      isAdmin={isAdmin}
      onBack={() => setBatchId(null)}
    />
  ) : (
    <BatchList batches={list} onOpen={setBatchId} />
  );
}
