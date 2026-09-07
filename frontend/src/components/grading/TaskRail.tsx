// The reader's rail: which pair they are on, how far they have got, and every
// pair of the read as a jump target.
//
// THE ORDER IS THE SERVER'S. `GET /v1/grading/batches/{id}/tasks` returns the
// tasks in the CALLER'S OWN seeded permutation — the same permutation
// `s0_rdev.py` used to build the RDEV-readsheet-A/B.html sheets — so a read
// begun on those sheets continues here at the same position. This component
// therefore renders `tasks` as it arrives: no sort, no filter, no reverse, not
// even "unread first". A client-side re-order would silently break the study's
// resumability and could not be detected from the screen.

import type { GradingBatch, GradingTask } from "../../api/client";

export function TaskRail({
  batch,
  tasks,
  current,
  onSelect,
  label,
}: {
  batch: GradingBatch;
  tasks: GradingTask[];
  current: number;
  onSelect: (index: number) => void;
  // The caller's reader letter, or null for an admin who is not a reader.
  label: string | null;
}) {
  const done = tasks.filter((t) => t.verdict !== null).length;
  const pct = tasks.length ? Math.round((100 * done) / tasks.length) : 0;

  return (
    <aside className="flex w-[260px] shrink-0 flex-col gap-4 self-start">
      {label ? (
        <div className="rounded-card border border-line bg-linkSoft px-3 py-[10px]">
          <span className="font-mono text-[10.5px] font-medium uppercase tracking-[.11em] text-muted">
            You are reader
          </span>
          <div className="font-mono text-xl font-semibold text-link">{label}</div>
        </div>
      ) : (
        <div className="rounded-card border border-line bg-paper px-3 py-[10px] text-xs text-dim">
          You are not a reader on this read — you are seeing it as an admin, and no verdict of
          yours is recorded.
        </div>
      )}

      <div className="text-[12.5px] text-dim">
        {done} of {tasks.length} verdicts recorded
        <div className="mt-[6px] h-[6px] overflow-hidden rounded-[3px] bg-lineSoft">
          <div className="h-full bg-accent" style={{ width: `${pct}%` }} />
        </div>
      </div>

      {batch.progress.length > 1 ? (
        <div className="text-xs text-dim">
          <span className="font-mono text-[10.5px] font-medium uppercase tracking-[.11em] text-muted">
            Both readers
          </span>
          <ul className="mt-1">
            {batch.progress.map((p) => (
              <li key={p.reader} className="flex justify-between gap-2">
                <span className="font-mono">{p.label}</span>
                <span>
                  {p.saved} / {batch.task_count}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <ol className="flex list-none flex-col gap-1 p-0">
        {tasks.map((t, i) => {
          const graded = t.verdict !== null;
          return (
            <li key={t.id}>
              <button
                type="button"
                aria-current={i === current ? "true" : undefined}
                onClick={() => onSelect(i)}
                className={`grid w-full grid-cols-[22px_1fr_auto] items-center gap-2 rounded-panel border px-2 py-[7px] text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-link ${
                  i === current
                    ? "border-line bg-linkSoft"
                    : "border-transparent hover:bg-paper"
                }`}
              >
                <span className="font-mono text-xs text-dim">{i + 1}</span>
                <span className="truncate font-mono text-[11.5px] text-body">{t.pair_id}</span>
                <span
                  title={graded ? "verdict recorded" : "not yet read"}
                  className={`h-[9px] w-[9px] rounded-full ${graded ? "bg-moss" : "bg-[#cfcec9]"}`}
                />
                <span className="sr-only">{graded ? "verdict recorded" : "not yet read"}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </aside>
  );
}
