// Presentational only: renders a `ParsedTable` (see ./extraction.ts) — the
// model's extracted-data answer, tolerant of the model having dropped a
// column on some rows. No fetch, no app state; `onDownload` is a plain
// callback the caller wires to whatever writes the TSV file.

import type { ParsedTable } from "./extraction";

export function ResultsTable({
  table,
  onDownload,
}: {
  table: ParsedTable;
  onDownload: () => void;
}) {
  return (
    <div className="rounded-panel border border-line">
      <div className="flex items-center justify-between border-b border-line bg-paper px-4 py-2.5">
        <span className="font-mono text-[11px] font-medium text-dim">
          {table.rows.length} row{table.rows.length === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          onClick={onDownload}
          className="rounded-chip border border-line px-2.5 py-1.5 text-[11px] font-medium text-link hover:bg-paper"
        >
          Download TSV
        </button>
      </div>

      {/* This is the only element allowed to scroll horizontally — the page
          body must not. */}
      <div className="max-h-[480px] overflow-auto">
        <table className="w-full border-collapse text-left text-[13px]">
          <thead>
            <tr>
              {table.headers.map((header, i) => (
                <th
                  key={i}
                  className="sticky top-0 z-10 whitespace-nowrap border-b border-line bg-paper px-3 py-2 font-display text-[11px] font-semibold uppercase tracking-wide text-strong"
                >
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, ri) => (
              <tr key={ri} className={ri % 2 === 1 ? "bg-lineSoft/50" : undefined}>
                {table.headers.map((_, ci) => (
                  // A model can drop a trailing column; render an empty cell
                  // rather than reading past the row's length.
                  <td
                    key={ci}
                    className="border-b border-lineSoft px-3 py-2 align-top text-body"
                  >
                    {row[ci] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
