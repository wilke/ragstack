// Presentational only: the three states of a generated answer (pending /
// error / text). No fetch, no app state. Answer text is untrusted model
// output, so the light markdown below is parsed into React elements —
// `dangerouslySetInnerHTML` never enters the picture.

import type { ReactNode } from "react";

// Splits a line on **bold** markers and renders the captured groups as
// <strong>. `text.split(/\*\*(.+?)\*\*/g)` yields
// [plain, bold, plain, bold, ...] — odd indices are the captured (bold) text.
function renderInline(line: string): ReactNode[] {
  return line.split(/\*\*(.+?)\*\*/g).map((part, i) =>
    i % 2 === 1 ? <strong key={i}>{part}</strong> : <span key={i}>{part}</span>,
  );
}

// Blank lines separate paragraphs; a single newline inside a paragraph is a
// line break.
function renderMarkdownLite(text: string): ReactNode {
  const paragraphs = text.split(/\n{2,}/);
  return paragraphs.map((paragraph, pi) => (
    <p key={pi} className="text-[15px] leading-[1.75] text-body">
      {paragraph.split("\n").map((line, li) => (
        <span key={li}>
          {li > 0 && <br />}
          {renderInline(line)}
        </span>
      ))}
    </p>
  ));
}

function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent"
    />
  );
}

export function AnswerPanel({
  text,
  modelLabel,
  pending,
  error,
}: {
  text?: string;
  modelLabel?: string;
  pending?: boolean;
  error?: string;
}) {
  return (
    <section className="rounded-panel border border-line px-4 py-3.5">
      <div className="mb-2.5 flex items-center justify-between">
        <span className="font-display text-[11px] font-semibold uppercase tracking-wide text-muted">
          Answer
        </span>
        {modelLabel && (
          <span className="font-mono text-[10.5px] text-faint">{modelLabel}</span>
        )}
      </div>

      {pending ? (
        <div className="flex items-center gap-2.5 py-2 text-[13.5px] text-dim">
          <Spinner />
          Generating answer from literature…
        </div>
      ) : error ? (
        <div className="rounded-chip bg-rustSoft px-3 py-2.5 text-[13px] text-rust">{error}</div>
      ) : text ? (
        <div className="space-y-3">{renderMarkdownLite(text)}</div>
      ) : (
        <p className="text-[13px] text-faint">No answer yet.</p>
      )}
    </section>
  );
}
