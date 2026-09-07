// The pilot sheet's fixed verdict bar: six chips, notes, save-and-advance.
//
// The chips are a REAL radio group — `role="radiogroup"` over native
// `<input type="radio">`, one name — so arrow keys move between them, only the
// selected one is a tab stop, and the group is announced as one control. Each
// chip's title carries its meaning and "counts as"; the help line under the bar
// repeats the selected chip's meaning in visible text, because a title
// attribute is not reachable by keyboard or touch.
//
// The save confirmation is an `aria-live="polite"` region: saving is the one
// action here with no visual consequence a screen-reader user could otherwise
// notice — the pair advances, but the sentence saying the verdict was RECORDED
// (and at which version) has to be spoken.

import { VERDICTS } from "../../lib/grading";
import type { GradingVerdictValue } from "../../api/client";

/** A stored ISO timestamp as a local one; the raw value if it will not parse. */
function formatSavedAt(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function VerdictBar({
  taskId,
  value,
  onChange,
  notes,
  onNotes,
  onSave,
  saving,
  status,
  error,
  disabled = false,
  savedAt,
  version,
  isLast,
}: {
  taskId: string;
  value: GradingVerdictValue | null;
  onChange: (v: GradingVerdictValue) => void;
  notes: string;
  onNotes: (v: string) => void;
  onSave: () => void;
  saving: boolean;
  // The aria-live sentence: what just happened, or "".
  status: string;
  error: string | null;
  disabled?: boolean;
  savedAt: string | null;
  version: number | null;
  isLast: boolean;
}) {
  const selected = VERDICTS.find((v) => v.value === value) ?? null;

  return (
    <div className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-white px-[34px] pb-[14px] pt-3 shadow-[0_-8px_24px_-16px_rgba(0,20,50,.35)]">
      <div className="flex max-w-[1100px] flex-wrap items-start gap-[10px]">
        <div
          role="radiogroup"
          aria-label="Pair verdict"
          className="flex flex-wrap gap-[6px]"
        >
          {VERDICTS.map((v) => (
            <label
              key={v.value}
              title={`${v.meaning} — counts as ${v.countsAs}`}
              className={`inline-flex cursor-pointer items-center gap-[6px] rounded-pill border px-[11px] py-[6px] text-[13px] focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-link ${
                value === v.value
                  ? "border-ink-900 bg-ink-900 text-white"
                  : "border-line text-body hover:bg-paper"
              }`}
            >
              <input
                type="radio"
                name={`verdict-${taskId}`}
                value={v.value}
                checked={value === v.value}
                disabled={disabled}
                onChange={() => onChange(v.value)}
                className="m-0"
              />
              <span className="font-mono">{v.value}</span>
              <span className={value === v.value ? "text-white/75" : "text-dim"}>
                · {v.countsAs}
              </span>
            </label>
          ))}
        </div>

        <div className="min-w-[220px] flex-1">
          <label className="sr-only" htmlFor={`notes-${taskId}`}>
            Notes for this pair
          </label>
          <textarea
            id={`notes-${taskId}`}
            value={notes}
            disabled={disabled}
            onChange={(e) => onNotes(e.target.value)}
            placeholder="Notes (optional): what you looked at, what was missing, why ambiguous…"
            className="min-h-[38px] w-full resize-y rounded-panel border border-line px-[9px] py-[7px] text-[13px] text-body focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link"
          />
        </div>

        <button
          type="button"
          onClick={onSave}
          disabled={saving || disabled}
          className="rounded-panel border border-ink-900 bg-ink-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link"
        >
          {saving ? "Saving…" : isLast ? "Save verdict" : "Save and next"}
        </button>
      </div>

      <p className="mt-[6px] max-w-[1100px] text-xs text-muted">
        {error ? (
          <span className="text-rust">{error}</span>
        ) : selected ? (
          <>
            <span className="font-mono text-strong">{selected.value}</span> — {selected.meaning}{" "}
            (counts as {selected.countsAs}).
          </>
        ) : (
          <>
            <b className="text-strong">(a)</b> Are the supplied spans correctly located and
            minimal? <b className="text-strong">(b)</b> Is there evidence in this document the
            labeler did not supply? Record one verdict for the pair.
          </>
        )}
      </p>

      {/* The one thing a save changes that is not visible: that it happened. */}
      <p aria-live="polite" className="min-h-[1.2em] text-xs text-dim">
        {status ||
          (savedAt && version !== null
            ? `Recorded ${formatSavedAt(savedAt)} (version ${version}). Change it and save again if you need to.`
            : "")}
      </p>
    </div>
  );
}
